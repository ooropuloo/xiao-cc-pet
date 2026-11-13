# -*- coding: utf-8 -*-
"""Claude Code page: pixel 小橘人 driven by the ccpet server's state stream.

ccpet (D:/git/ccpet/server.js, port 3378) already turns ~/.claude/projects JSONL
into one state per project; this module only listens to its SSE and draws.
States: running, review, waiting, failed, jumping, idle.
"""
import io
import json
import math
import threading
import time
import urllib.request

import themes
import voxel

from functools import lru_cache

from PIL import Image, ImageDraw, ImageEnhance, ImageFont

CCPET_EVENTS = "http://127.0.0.1:3378/events"
FONT = "C:/Windows/Fonts/msjhbd.ttc"
FONT_LIGHT = "C:/Windows/Fonts/msjh.ttc"
ORANGE = (217, 119, 87)
ORANGE_DARK = (170, 82, 56)
EYE = (34, 22, 18)
BG = (16, 14, 13)
PAGES, PAGE_INDEX = 6, 3
# widest text that stays inside the status ring (inner radius 112) at its height on the circle
TITLE_W = 112   # title, y=30
DETAIL_W = 160  # second detail line, y≈191

STATE_PRIORITY = {"waiting": 5, "failed": 4, "running": 3, "review": 3, "jumping": 2, "idle": 1}
RING = {
    "waiting": (255, 160, 60),
    "failed": (235, 70, 70),
    "running": ORANGE,
    "review": (120, 170, 255),
    "jumping": (120, 220, 120),
    "idle": (60, 56, 54),
}
LABEL = {"waiting": "等你回覆", "failed": "出錯了", "running": "工作中", "review": "檢視中", "jumping": "完成！", "idle": "待命中"}

class CcpetFeed:
    """Background SSE reader; keeps the latest agents list."""

    def __init__(self, log):
        self.log = log
        self.agents = []
        self.online = False
        self.lock = threading.Lock()
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        while True:
            try:
                # ccpet only writes on changes; a long read timeout avoids reconnect churn
                with urllib.request.urlopen(CCPET_EVENTS, timeout=300) as r:
                    self.online = True
                    self.log.info("ccpet feed up")
                    for raw in r:
                        line = raw.decode("utf-8", "replace").strip()
                        if line.startswith("data:"):
                            msg = json.loads(line[5:])
                            if msg.get("type") == "snapshot":
                                with self.lock:
                                    self.agents = msg.get("agents", [])
            except Exception as e:
                if self.online:
                    self.log.info(f"ccpet feed down: {e}")
                self.online = False
                time.sleep(5)

    def snapshot(self):
        with self.lock:
            return list(self.agents)


def short_project(p):
    # ccpet project ids look like "D:/git/xiao/esp32" (older builds: "D--git-xiao-esp32")
    for sep in ("/git/", "--git-", "--"):
        if sep in p:
            p = p.split(sep, 1)[1]
    return p or "?"


def order_agents(agents):
    return sorted(agents, key=lambda a: (-STATE_PRIORITY.get(a.get("state"), 0), -a.get("lastEventAt", 0)))


def fit(d, text, font, width):
    text = " ".join(str(text).split())  # ccpet text can contain newlines
    if d.textlength(text, font=font) <= width:
        return text
    while text and d.textlength(text + "…", font=font) > width:
        text = text[:-1]
    return text + "…"


def project_label(d, agent, font, width):
    """The short project name, with ` ×3` when ccpet groups several sessions; only the name gets cut."""
    count = agent.get("sessionCount", 1)
    suffix = f" ×{count}" if type(count) is int and count > 1 else ""
    width -= d.textlength(suffix, font=font)
    name = short_project(agent["project"])
    if d.textlength(name, font=font) > width and "/" in name:
        name = "…/" + name.rsplit("/", 1)[1]  # folders of one repo share its start: the end tells them apart
    return fit(d, name, font, width) + suffix


def wrap2(d, text, font, width):
    text = " ".join(text.split())
    lines, cur = [], ""
    for ch in text:
        if d.textlength(cur + ch, font=font) > width:
            lines.append(cur)
            cur = ch
            if len(lines) == 2:
                break
        else:
            cur += ch
    if len(lines) < 2 and cur:
        lines.append(cur)
    if len(lines) == 2 and d.textlength(text, font=font) > 2 * width:
        lines[1] = fit(d, lines[1] + "…", font, width)
    return lines


SLEEP_AFTER = 90  # seconds of idle before the character dozes off


def pose_of(state, activity, sleeping):
    """Collapse ccpet state + activity into one animation name."""
    if sleeping:
        return "sleep"
    if state == "running" and activity in ("thinking", "compacting"):
        return "think"
    if state == "running" and activity == "typing":
        return "type"
    if state == "running" and activity == "reading":
        return "review"
    return state


def draw_clawd(d, pose, t, cx, cy, cell, S, since=0.0):
    """3D voxel 小橘人; t is seconds (drives animation), since = seconds in this pose.
    Returns the model's top y."""
    bob, shake, yaw, tilt = 0.0, 0.0, 0.0, 0.0
    legs = (0, 0, 0, 0)
    arms = (0, 0)
    keyboard = None
    eye_mode = "open"
    if pose == "idle":
        bob = math.sin(t * 2.0) * 0.25
        yaw = math.sin(t * 0.6) * 0.55
        if (t % 4.0) < 0.15:
            eye_mode = "blink"
    elif pose == "sleep":
        # legs tucked in, body settled onto the floor, slow breathing
        legs = (2, 2, 2, 2)
        bob = -2.0 + math.sin(t * 1.3) * 0.08
        yaw = 0.35
        eye_mode = "closed"
    elif pose == "running":
        step = int(t * 6) % 2
        legs = (1, 0, 1, 0) if step else (0, 1, 0, 1)
        bob = 0.15 if step else 0
        yaw = t * 0.9
    elif pose == "type":
        tap = int(t * 9)
        keyboard = ((tap * 5) % 3, (tap * 7) % 11)
        arms = (-0.4, 0) if tap % 2 else (0, -0.4)  # one hand lifts while the other presses
        yaw = 0.3 + math.sin(t * 0.8) * 0.06
        bob = 0.06 if tap % 2 else 0.0
    elif pose == "think":
        bob = math.sin(t * 1.6) * 0.18
        yaw = 0.3 + math.sin(t * 0.5) * 0.25
        eye_mode = "look_up"
    elif pose == "review":
        look = int(t * 1.5) % 2
        eye_mode = "look_l" if look else "look_r"
        yaw = -0.45 if look else 0.45
    elif pose == "waiting":
        bob = abs(math.sin(t * 4.0)) * 1.2
    elif pose == "failed":
        # topples backwards in 0.45 s, bounces once, then lies there
        a = min(1.0, since / 0.45)
        tilt = -1.3 * a * a
        if 0.45 < since < 0.75:
            tilt += 0.18 * math.sin((since - 0.45) / 0.3 * math.pi)
        shake = math.sin(t * 40) * 0.12 if since < 0.6 else 0.0
        yaw = 0.25
        eye_mode = "x"
    elif pose == "jumping":
        bob = abs(math.sin(t * 5.0)) * 1.3
        yaw = t * 6.0

    # floor shadow shrinks as the character rises
    sw = cell * 5.2 * (1 - 0.08 * max(bob, 0))
    sy = cy + 4.9 * cell
    d.ellipse((s(cx - sw, S), s(sy - 4, S), s(cx + sw, S), s(sy + 4, S)), fill=(8, 7, 7))

    vox = voxel.build(eye_mode, legs, arms, keyboard)
    boxes = voxel.typing_rig(keyboard) if keyboard is not None else ()
    proj, xf = voxel.render(d, vox, cx + shake * cell, cy, cell, yaw, bob=bob, S=S, tilt=tilt, boxes=boxes)
    if eye_mode == "x":
        for ec in (3, 7):
            p0, p1 = proj(xf(ec - 0.1, 0.9, -0.02)), proj(xf(ec + 1.1, 3.1, -0.02))
            q0, q1 = proj(xf(ec - 0.1, 3.1, -0.02)), proj(xf(ec + 1.1, 0.9, -0.02))
            d.line([p0, p1], fill=EYE, width=s(2.4, S))
            d.line([q0, q1], fill=EYE, width=s(2.4, S))
    elif eye_mode == "closed":
        for ec in (3, 7):
            d.line([proj(xf(ec - 0.2, 2.5, -0.02)), proj(xf(ec + 1.2, 2.5, -0.02))], fill=EYE, width=s(2.2, S))
    xs =[proj(xf(x, y, z)) for x in (0, 11) for y in (0, 8) for z in (0, 4)]
    return min(p[1] for p in xs) / S, proj(xf(5.5, 0, 2))


def draw_props(d, pose, t, top, head, S, since=0.0):
    """2D overlays around the model: Zz, thought bubble, smoke + ERROR."""
    hx, hy = head[0] / S, head[1] / S
    if pose == "sleep":
        fz = ImageFont.truetype(FONT, s(11, S))
        for i in range(3):
            ph = (t * 0.45 + i / 3) % 1.0
            x, y = hx + 34 + ph * 18, hy + 4 - ph * 26
            k = int(90 + 140 * math.sin(ph * math.pi))
            fz = ImageFont.truetype(FONT, s(9 + 7 * ph, S))
            d.text((s(x, S), s(y, S)), "z", font=fz, fill=(k, k, min(255, k + 25)), anchor="mm")
    elif pose == "think":
        bx, by = 180, top - 16
        for r, (x, y) in ((2.2, (bx - 27, by + 19)), (3.5, (bx - 20, by + 12))):
            d.ellipse((s(x - r, S), s(y - r, S), s(x + r, S), s(y + r, S)), fill=(240, 236, 228))
        d.rounded_rectangle((s(bx - 20, S), s(by - 11, S), s(bx + 20, S), s(by + 11, S)), radius=s(10, S), fill=(240, 236, 228))
        n = int(t * 2.5) % 4
        for i in range(3):
            col = (90, 84, 80) if i < n else (205, 200, 192)
            x = bx - 10 + i * 10
            d.ellipse((s(x - 2.6, S), s(by - 2.6, S), s(x + 2.6, S), s(by + 2.6, S)), fill=col)
    elif pose == "type":
        # monitor above the head; code lines type out, then the screen scrolls
        x0, y0, x1, y1 = 88, 38, 152, 70
        d.rounded_rectangle((s(x0, S), s(y0, S), s(x1, S), s(y1, S)), radius=s(4, S), fill=(34, 36, 44), outline=(90, 94, 108), width=s(1.5, S))
        d.rectangle((s(116, S), s(y1, S), s(124, S), s(y1 + 4, S)), fill=(90, 94, 108))
        code = [(0, 0.55, (110, 200, 120)), (6, 0.7, (120, 170, 235)), (6, 0.45, (230, 200, 110)),
                (0, 0.6, (110, 200, 120)), (6, 0.8, (120, 170, 235)), (12, 0.35, (200, 140, 220))]
        chars = int(t * 14)
        shown = chars // 10
        first = max(0, shown - 3)
        for row, i in enumerate(range(first, shown + 1)):
            ind, w, col = code[i % len(code)]
            full = (x1 - x0 - 14 - ind) * w
            ln = full if i < shown else full * (chars % 10) / 10
            y = y0 + 6 + row * 6.5
            if ln > 0:
                d.rounded_rectangle((s(x0 + 7 + ind, S), s(y, S), s(x0 + 7 + ind + ln, S), s(y + 3.5, S)), radius=s(1.5, S), fill=col)
            if i == shown and int(t * 3) % 2 == 0:
                cxr = x0 + 8 + ind + ln
                d.rectangle((s(cxr, S), s(y - 0.5, S), s(cxr + 2.5, S), s(y + 4, S)), fill=(235, 235, 235))
    elif pose == "failed" and since > 0.45:
        for i in range(3):
            ph = ((t - 0.45) * 0.6 + i / 3) % 1.0
            x = hx + 6 * math.sin(ph * 6 + i) + (i - 1) * 6
            y = hy - 8 - ph * 38
            r = 3 + ph * 6
            g = int(150 - 90 * ph)
            d.ellipse((s(x - r, S), s(y - r, S), s(x + r, S), s(y + r, S)), fill=(g, g, g))
        if int(t * 2.5) % 2 == 0:
            fe = ImageFont.truetype(FONT, s(12, S))
            d.text((s(120, S), s(140, S)), "ERROR", font=fe, fill=(235, 70, 70), anchor="mm")


def s(v, S):
    return int(round(v * S))


_pose_since = [None, 0.0]  # [(agent id, pose), t when it started] -> drives one-shot motions


def agent_view(agent, online):
    state = agent.get("state", "idle") if agent else "idle"
    idle_for = time.time() - agent.get("lastEventAt", 0) / 1000 if agent else SLEEP_AFTER
    sleeping = online and state == "idle" and idle_for >= SLEEP_AFTER
    return state, pose_of(state, agent.get("activity", "") if agent else "", sleeping), sleeping


def pose_elapsed(clock, agent, pose, t):
    key = (agent.get("id") if agent else None, pose)
    if clock[0] != key:
        clock[:] = [key, t]
    return t - clock[1]


def draw_scene(d, pose, state, t, since, S):
    """The character with its props, in 240-space; returns the model's top y."""
    # thinking sits lower so the bubble clears the face; typing smaller and lower under its monitor
    top, head = draw_clawd(d, pose, t, 120, {"think": 100, "type": 114}.get(pose, 94), 8 if pose == "type" else 9, S, since)
    draw_props(d, pose, t, top, head, S, since)
    themes.call("front", d, pose, t, top, head, S)
    if state == "waiting":
        bx, by = 166, top - 6
        d.ellipse((s(bx - 11, S), s(by - 11, S), s(bx + 11, S), s(by + 11, S)), fill=(255, 245, 230))
        d.polygon([(s(bx - 8, S), s(by + 6, S)), (s(bx - 14, S), s(by + 16, S)), (s(bx - 2, S), s(by + 9, S))], fill=(255, 245, 230))
        d.text((s(bx, S), s(by, S)), "?", font=ImageFont.truetype(FONT, s(16, S)), fill=ORANGE_DARK, anchor="mm")
    if state == "jumping":
        for i in range(5):
            a = t * 3 + i * 1.256
            r = 52 + 6 * math.sin(t * 6 + i)
            x, y = 120 + math.cos(a) * r, 92 + math.sin(a) * r * 0.6
            k = 4 + 2 * math.sin(t * 8 + i)
            star = [(x, y - k), (x + k * 0.3, y - k * 0.3), (x + k, y), (x + k * 0.3, y + k * 0.3),
                    (x, y + k), (x - k * 0.3, y + k * 0.3), (x - k, y), (x - k * 0.3, y - k * 0.3)]
            d.polygon([(s(px, S), s(py, S)) for px, py in star], fill=(255, 220, 120))
    return top


HOLO_CROP = (30, 10, 210, 190)  # the character's box in 240-space, enlarged to fill the panel
# the prism passes under half the panel's light and the backlight is already full: push the image itself
HOLO_BRIGHTNESS, HOLO_COLOR = 1.5, 1.5
# the scene ends at y=194 (typing pose) on the final frame; both lines live below it
HOLO_NAME_Y, HOLO_TEMP_Y = 204, 225
# board, chip in °C: red from here. Highest of 7178 logged readings: 78.5 / 95.0 (sensors uncalibrated)
HOLO_TEMP_WARN = (85, 100)
HOLO_TEMP_OK, HOLO_TEMP_HOT = (255, 255, 255), (255, 60, 40)
# the XIAO's own chip, °C: same red line as the system page's bar (firmware drawSysValues)
CHIP_WARN = 60
CHIP_OK = (190, 185, 180)
CHIP_LINE_Y = 214


def draw_holo_temps(d, temps, y=HOLO_TEMP_Y, size=20):
    """`板 76°  晶片 90°` centred along the bottom; a None value (no fresh reading) shows `--`."""
    font = ImageFont.truetype(FONT, size)
    parts = []
    for label, value, warn in zip(("板", "晶片"), temps, HOLO_TEMP_WARN):
        text = f"{label} --" if value is None else f"{label} {value:.0f}°"
        parts.append((text, HOLO_TEMP_HOT if value is not None and value >= warn else HOLO_TEMP_OK))
    gap = 14
    x = 120 - (sum(d.textlength(text, font=font) for text, _ in parts) + gap) / 2
    for text, colour in parts:
        d.text((x, y), text, font=font, fill=colour, anchor="lm")
        x += d.textlength(text, font=font) + gap


def render_holo(agent, online, t, clock, show_name, temps=(None, None), scene=None):
    """Pure black background: the prism shows black as clear glass. temps = (board, chip) in °C.
    scene: a mini game's draw(d, t, S), shown instead of the character (holo_game.py).
    Text is drawn on the normal, unmirrored frame: the panel's MADCTL MX mirrors it for the prism."""
    S = 2
    img = Image.new("RGB", (240 * S, 240 * S), (0, 0, 0))
    d = ImageDraw.Draw(img)
    state, pose, _ = agent_view(agent, online)
    since = pose_elapsed(clock, agent, pose, t)
    if scene:
        scene(d, t, S)
    else:
        draw_scene(d, pose if online else "idle", state if online else "idle", t, since, S)
    img = img.crop(tuple(v * S for v in HOLO_CROP)).resize((240, 240), Image.LANCZOS)
    img = ImageEnhance.Brightness(ImageEnhance.Color(img).enhance(HOLO_COLOR)).enhance(HOLO_BRIGHTNESS)
    d = ImageDraw.Draw(img)
    if not online or (show_name and agent):
        font = ImageFont.truetype(FONT, 15)
        name = project_label(d, agent, font, 210) if online and agent else "ccpet 未啟動"
        d.text((120, HOLO_NAME_Y), name, font=font, fill=(255, 255, 255), anchor="mm")
    draw_holo_temps(d, temps)
    out = io.BytesIO()
    # 4:4:4 keeps the orange edges crisp on black; q85 pays for the always-on temperature line (q90 was +28%)
    img.save(out, "JPEG", quality=85, subsampling=0)
    return out.getvalue()


def draw_page_dots(d, active, S):
    """12 px apart: six dots still clear the status ring at this height."""
    for i in range(PAGES):
        x = 120 + (i - (PAGES - 1) / 2) * 12
        col = (255, 255, 255) if i == active else (70, 70, 80)
        d.ellipse((s(x - 3, S), s(222, S), s(x + 3, S), s(228, S)), fill=col)


def draw_chip_line(d, chip, S):
    """`晶片 45°` between the text rows and the page dots; None (no fresh reading) shows `--`."""
    text, colour = "晶片 --", CHIP_OK
    if chip is not None:
        text, colour = f"晶片 {chip:.0f}°", HOLO_TEMP_HOT if chip >= CHIP_WARN else CHIP_OK
    d.text((s(120, S), s(CHIP_LINE_Y, S)), text, font=ImageFont.truetype(FONT, s(10.5, S)), fill=colour, anchor="mm")


def render(agents, online, sel, t, chip=None):
    """chip: the board's own temperature in °C, or None when there's no fresh reading."""
    S = 2
    W = 240 * S
    img = Image.new("RGB", (W, W), BG)
    d = ImageDraw.Draw(img)
    ordered = order_agents(agents)
    agent = ordered[sel % len(ordered)] if ordered else None
    state, pose, sleeping = agent_view(agent, online)
    since = pose_elapsed(_pose_since, agent, pose, t)

    # status ring; waiting pulses
    ring = RING.get(state, RING["idle"])
    if state == "idle":
        for m in themes.active():
            ring = getattr(m, "ring", ring)
    if state == "waiting":
        k = 0.55 + 0.45 * (0.5 + 0.5 * math.sin(t * 5))
        ring = tuple(int(c * k) for c in ring)
    d.ellipse((s(3, S), s(3, S), W - s(3, S), W - s(3, S)), outline=ring, width=s(5, S))
    if online:
        themes.call("back", d, t, S)

    ft = ImageFont.truetype(FONT, s(13, S))
    fs = ImageFont.truetype(FONT, s(16, S))
    fb = ImageFont.truetype(FONT_LIGHT, s(10.5, S))
    fm = ImageFont.truetype(FONT_LIGHT, s(9.5, S))
    fd = ImageFont.truetype(FONT, s(12, S))

    if not online:
        draw_clawd(d, "idle", t, 120, 96, 9, S)
        d.text((s(120, S), s(168, S)), "ccpet 未啟動", font=fs, fill=(200, 200, 200), anchor="mm")
        d.text((s(120, S), s(190, S)), "開 D:\\git\\ccpet\\start.bat", font=fm, fill=(130, 130, 130), anchor="mm")
    else:
        title = project_label(d, agent, ft, s(TITLE_W, S)) if agent else "目前沒有 session"
        d.text((s(120, S), s(30, S)), title, font=ft, fill=(235, 225, 215), anchor="mm")
        if len(ordered) > 1:
            # below the status text: up here the monitor, bubbles and smoke would paint over it
            d.text((s(120, S), s(204, S)), f"{sel % len(ordered) + 1} / {len(ordered)}  點一下換專案", font=fm, fill=(150, 145, 140), anchor="mm")
        draw_scene(d, pose, state, t, since, S)

        label = agent.get("statusText") or LABEL.get(state, state) if agent else ""
        if sleeping:
            label = "睡著了…"
        # ccpet sends "等你授權：<what>"; what is being asked is the whole point of this state, so it gets its own lines
        label, _, asking = label.partition("：") if state == "waiting" else (label, "", "")
        d.text((s(120, S), s(160, S)), fit(d, label, fs, s(180, S)), font=fs, fill=RING.get(state, (220, 220, 220)) if state != "idle" else (200, 195, 190), anchor="mm")
        if asking:
            for i, line in enumerate(wrap2(d, asking, fd, s(DETAIL_W, S))):
                d.text((s(120, S), s(177 + i * 14, S)), line, font=fd, fill=(240, 232, 222), anchor="mm")
        else:
            bubble = (agent.get("bubble") or "") if agent else ""
            for i, line in enumerate(wrap2(d, bubble, fb, s(DETAIL_W, S)) if bubble else []):
                d.text((s(120, S), s(178 + i * 13, S)), line, font=fb, fill=(150, 145, 140), anchor="mm")

    draw_chip_line(d, chip, S)
    draw_page_dots(d, PAGE_INDEX, S)

    out = io.BytesIO()
    img.resize((240, 240), Image.LANCZOS).save(out, "JPEG", quality=85)
    return out.getvalue(), state


# ---------- AskUserQuestion screen: a dial ----------
# Drag along the ring to move the cursor between options, tap the centre to send.
# Multi-select adds a "完成" sector; tapping the centre toggles the option under the cursor.

RING_R0, RING_R1 = 100, 116   # ring band (240-space)
DIAL_MIN_R = 72               # touches farther out than this dial (firmware uses the same number)
CENTER_R = 68                 # taps closer than this confirm (centre disc is 70)
SECTOR_R_OUT, SECTOR_R_IN = 111, 80   # drawn band only; hit areas are DIAL_MIN_R / CENTER_R above
SECTOR_GAP, SECTOR_CORNER = 6, 8      # px between neighbours, corner radius
DONE = "__done__"


def dial_items(q):
    items = list(range(min(6, len(q.get("options", [])))))
    if q.get("multiSelect"):
        items.append(DONE)
    return items


def dial_index(q, x, y):
    """Sector under (x, y); sector 0 is centred on 12 o'clock, then clockwise."""
    k = len(dial_items(q))
    seg = 360 / k
    a = (math.degrees(math.atan2(y - 120, x - 120)) + 90 + seg / 2) % 360
    return int(a // seg) % k


def touch_zone(x, y):
    r = math.hypot(x - 120, y - 120)
    return "ring" if r > DIAL_MIN_R else "center" if r < CENTER_R else "between"


def item_label(q, item):
    return "完成" if item == DONE else q["options"][item].get("label", "")


def sector_outline(phi, half, r_out, r_in, gap, corner, step=2.0):
    """Annular wedge centred on angle phi (degrees) with rounded corners and straight sides that
    stay gap/2 from the sector boundary, so neighbours are the same distance apart at any radius."""
    off = corner + gap / 2  # corner centres are this far from the boundary line
    co, ci = r_out - corner, r_in + corner
    th_o = half - math.degrees(math.asin(off / co))
    th_i = half - math.degrees(math.asin(off / ci))

    def arc(cx, cy, r, a0, a1):
        n = max(2, int(abs(a1 - a0) / step) + 1)
        return [(cx + r * math.cos(math.radians(a0 + (a1 - a0) * j / n)),
                 cy + r * math.sin(math.radians(a0 + (a1 - a0) * j / n))) for j in range(n + 1)]

    # one half (angle 0 .. +half), walking from the outer rim to the inner rim
    side = arc(0, 0, r_out, 0, th_o)
    side += arc(co * math.cos(math.radians(th_o)), co * math.sin(math.radians(th_o)), corner, th_o, half + 90)
    side += arc(ci * math.cos(math.radians(th_i)), ci * math.sin(math.radians(th_i)), corner, half + 90, th_i + 180)
    side += arc(0, 0, r_in, th_i, 0)
    # mirror it for the other half, then rotate onto phi
    pts = side + [(x, -y) for x, y in reversed(side)]
    c, n = math.cos(math.radians(phi)), math.sin(math.radians(phi))
    return [(x * c - y * n, x * n + y * c) for x, y in pts]


@lru_cache(maxsize=8)
def sector_masks(k, S):
    """Antialiased sector masks for a k-sector dial, as (mask, (x, y)) for paste. Static, so cached."""
    AA = 4  # on top of S
    seg = 360 / k
    out = []
    for i in range(k):
        pts = sector_outline(-90 + i * seg, seg / 2, SECTOR_R_OUT, SECTOR_R_IN, SECTOR_GAP, SECTOR_CORNER)
        m = Image.new("L", (240 * S * AA, 240 * S * AA), 0)
        ImageDraw.Draw(m).polygon([((120 + x) * S * AA, (120 + y) * S * AA) for x, y in pts], fill=255)
        m = m.reduce(AA)
        box = m.getbbox()
        out.append((m.crop(box), box[:2]))
    return out


def render_question(v, t, cursor=0, pressed=False):
    """Dial layout (after Codex's mock): fat numbered sectors around, question + big choice inside.
    cursor: index into dial_items(q); pressed: finger is on the centre right now."""
    S = 2
    W = 240 * S
    q = v["q"]
    items = dial_items(q)
    k = len(items)
    cursor %= k
    multi = bool(q.get("multiSelect"))
    img = Image.new("RGB", (W, W), (12, 13, 20))
    d = ImageDraw.Draw(img)
    d.ellipse((s(2, S), s(2, S), W - s(2, S), W - s(2, S)), outline=(44, 47, 58), width=s(4, S))

    seg = 360 / k
    r_out, r_in = SECTOR_R_OUT, SECTOR_R_IN
    masks = sector_masks(k, S)
    fn = ImageFont.truetype(FONT_LIGHT, s(13, S))
    fd = ImageFont.truetype(FONT, s(11, S))
    for i, item in enumerate(items):
        on = item != DONE and item in v["selected"]
        if i == cursor:
            col = (255, 212, 112) if item != DONE else (120, 215, 140)
        elif item == DONE:
            col = (44, 70, 54)
        else:
            col = (70, 60, 40) if on else (40, 43, 62)
        a0 = -90 - seg / 2 + i * seg  # sector 0 centred on 12 o'clock
        img.paste(col, masks[i][1], masks[i][0])
        mid = math.radians(a0 + seg / 2)
        lx, ly = 120 + math.cos(mid) * (r_out + r_in) / 2, 120 + math.sin(mid) * (r_out + r_in) / 2
        txt = "完成" if item == DONE else ("√" if on else "") + str(i + 1)
        tcol = (30, 26, 18) if i == cursor else (200, 205, 220)
        d.text((s(lx, S), s(ly, S)), txt, font=fd if item == DONE else fn, fill=tcol, anchor="mm")

    cr = 70
    d.ellipse((s(120 - cr, S), s(120 - cr, S), s(120 + cr, S), s(120 + cr, S)),
              fill=(52, 55, 78) if pressed else (36, 38, 56), outline=(62, 66, 90), width=s(1.5, S))

    fq = ImageFont.truetype(FONT_LIGHT, s(9.5, S))
    qlines = wrap2(d, q.get("question", ""), fq, s(112, S))
    for i, line in enumerate(qlines):
        d.text((s(120, S), s((80 if len(qlines) > 1 else 86) + i * 12, S)), line, font=fq, fill=(185, 188, 205), anchor="mm")

    cur = items[cursor]
    fb = ImageFont.truetype(FONT_LIGHT, s(17, S))
    big = wrap2(d, item_label(q, cur), fb, s(118, S))
    y0 = 118 if len(big) == 1 else 111
    for i, line in enumerate(big):
        d.text((s(120, S), s(y0 + i * 19, S)), line, font=fb, fill=(245, 246, 252), anchor="mm")

    fs = ImageFont.truetype(FONT_LIGHT, s(9, S))
    n_opts = k - (1 if multi else 0)
    pos = "完成" if cur == DONE else f"{cursor + 1} / {n_opts}"
    d.text((s(120, S), s(141, S)), pos, font=fs, fill=(150, 154, 175), anchor="mm")

    if cur == DONE:
        hint = "點擊完成" if v["selected"] else "先勾選選項"
    elif multi:
        hint = "點擊取消勾選" if cur in v["selected"] else "點擊勾選"
    else:
        hint = "點擊確認"
    fh = ImageFont.truetype(FONT, s(10.5, S))
    hx = 120
    d.line([(s(hx - 30, S), s(160, S)), (s(hx - 26, S), s(164, S)), (s(hx - 19, S), s(156, S))], fill=(255, 212, 112), width=s(1.6, S), joint="curve")
    d.text((s(hx - 14, S), s(160, S)), hint, font=fh, fill=(255, 212, 112), anchor="lm")
    head = f"{v['idx'] + 1}/{v['total']} · " if v["total"] > 1 else ""
    d.text((s(120, S), s(178, S)), f"{head}{int(v['remaining'])}s", font=fs, fill=(110, 114, 135), anchor="mm")

    out = io.BytesIO()
    img.resize((240, 240), Image.LANCZOS).save(out, "JPEG", quality=88)
    return out.getvalue()


def render_sent(label):
    """Short confirmation after an answer is sent."""
    S = 2
    W = 240 * S
    img = Image.new("RGB", (W, W), BG)
    d = ImageDraw.Draw(img)
    d.ellipse((s(3, S), s(3, S), W - s(3, S), W - s(3, S)), outline=RING["jumping"], width=s(5, S))
    cx, cy, r = 120, 92, 30
    d.ellipse((s(cx - r, S), s(cy - r, S), s(cx + r, S), s(cy + r, S)), fill=(90, 190, 110))
    d.line([(s(cx - 14, S), s(cy + 1, S)), (s(cx - 4, S), s(cy + 11, S)), (s(cx + 15, S), s(cy - 10, S))],
           fill=(255, 255, 255), width=s(6, S), joint="curve")
    d.text((s(120, S), s(145, S)), "已送出", font=ImageFont.truetype(FONT, s(16, S)), fill=(150, 230, 160), anchor="mm")
    fo = ImageFont.truetype(FONT_LIGHT, s(11.5, S))
    d.text((s(120, S), s(168, S)), fit(d, label, fo, s(170, S)), font=fo, fill=(225, 220, 212), anchor="mm")
    out = io.BytesIO()
    img.resize((240, 240), Image.LANCZOS).save(out, "JPEG", quality=88)
    return out.getvalue()
