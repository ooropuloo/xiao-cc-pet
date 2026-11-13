# -*- coding: utf-8 -*-
"""The round display and the HoloCubic notice each other: signal strength picks a small scripted
exchange, both boards show it on a chat page with the 小橘人 acting it out.

The boards only report how strongly they hear each other (firmware peer_link.h); everything else is
decided here. Distances measured 2026-11-06, average of both boards in dBm:
touching -28, 10 cm -40, 30 cm -47, 1 m -63, and the user calls about -52 far. Direction can't be told from one antenna, so a jump leaves
through the top of one screen and drops in from the top of the other.
"""
import collections
import io
import math
import random
import statistics
import threading
from dataclasses import dataclass

from PIL import Image, ImageDraw, ImageEnhance, ImageFont

import clawd
import voxel

XIAO, HOLO = "xiao", "holo"
NAMES = {XIAO: "小圓盤", HOLO: "全息"}
FACE_RING = {XIAO: (255, 190, 120), HOLO: (120, 200, 255)}

NEAR_ON, NEAR_OFF = -33, -38  # dBm; two values so a reading on the line doesn't flip back and forth
FAR_ON, FAR_OFF = -50, -46    # the user called about -52 far (2026-11-06); 30 cm at -47 stays in between
SAMPLE_SECS = 4               # the median of this long smooths a hand passing by
REPORT_STALE_SECS = 5         # a board's report older than this says nothing
LOST_AGE_MS = 8000            # a board that hasn't heard the other for this long has lost it
FAR_DWELL_SECS = 5            # really moved away, not somebody walking past
NEAR_AGAIN_SECS = (45, 150)   # staying close: another random reaction after this long
ZONE_COOLDOWN_SECS = 120      # far / lost / back don't repeat sooner than this
CHAT_PEEK_SECS = 12           # tilting the HoloCubic forward shows the chat page this long
HISTORY = 12
HOT_BOARD, HOT_CHIP = 76, 60  # the HoloCubic's board, the round display's chip: 太熱 comes up more often

AWAY_NOTE = "去找{}玩了…"


@dataclass(frozen=True)
class Line:
    at: float
    who: str  # "a" starts it, "b" answers
    text: str
    mood: str


@dataclass(frozen=True)
class Script:
    kind: str
    secs: float
    lines: tuple
    jump: bool = False  # a leaves its screen and visits b's
    pull: bool = True   # the round display switches to the chat page for it


def L(at, who, text, mood):
    return Line(at, who, text, mood)


SCRIPTS = {
    "hello": (Script("hello", 5, (L(.3, "a", "嗨～好近喔", "smile"), L(2, "b", "嗨嗨！", "smile"))),
              Script("hello", 5, (L(.3, "a", "你也在這？", "look"), L(2, "b", "一直都在啊", "smile")))),
    "surprise": (Script("surprise", 6, (L(.3, "a", "哇！好近！", "surprised"), L(2.2, "b", "嚇我一跳！", "surprised"))),
                 Script("surprise", 6, (L(.3, "a", "誰靠過來了！", "surprised"), L(2.2, "b", "是我啦！", "smile")))),
    "hot": (Script("hot", 6, (L(.3, "a", "{temp}° 了，走開啦", "hot"), L(2.4, "b", "你才比較熱", "angry"))),
            Script("hot", 6, (L(.3, "a", "好熱…別貼著", "hot"), L(2.4, "b", "我也快融了", "hot")))),
    "fight": (Script("fight", 11, (L(.2, "a", "靠太近了啦！", "angry"), L(1.9, "b", "來啊！", "angry"),
                                   L(4, "a", "碰！", "angry"), L(5.6, "b", "啪！", "angry"),
                                   L(8, "a", "…算你狠", "dizzy")), jump=True),),
    "far": (Script("far", 7, (L(.3, "a", "你跑去哪了？", "look"), L(2.4, "b", "我在這～", "look"),
                              L(4.4, "a", "訊號好弱喔", "sad")), pull=False),),
    "lost": (Script("lost", 7, (L(.3, "a", "喂？收不到你了", "sad"), L(3.4, "a", "…是不是睡了", "sad")), pull=False),),
    "back": (Script("back", 6, (L(.3, "a", "你回來了！", "surprised"), L(2.2, "b", "想我嗎", "smile"))),),
}
NEAR_WEIGHTS = {"hello": 3, "surprise": 3, "fight": 2, "hot": 1}  # 2026-11-11: fight was 6 of 11, too often
HOT_WEIGHT = 2


def other(side):
    return HOLO if side == XIAO else XIAO


def zone_of(rssi, zone):
    """The zone for a smoothed reading, staying in the current one until clearly past the line."""
    if rssi >= (NEAR_OFF if zone == "near" else NEAR_ON):
        return "near"
    if rssi <= (FAR_OFF if zone == "far" else FAR_ON):
        return "far"
    return "mid"


class Interaction:
    def __init__(self, script, first, start, temp=None):
        self.script, self.first, self.start, self.temp = script, first, start, temp
        self.end = start + script.secs
        self.said = 0

    def side_of(self, who):
        return self.first if who == "a" else other(self.first)


def label_of(rssi):
    """Words for the status line, not a distance: reflections move the reading more than a ruler would."""
    if rssi is None:
        return "聽不到對方"
    return "太靠近" if rssi >= NEAR_ON else "很遠" if rssi <= FAR_ON else "剛好"


class Buddy:
    """update() runs on the player's loop; render() also runs on the HoloCubic's thread, hence the lock."""

    def __init__(self, log, rng=random):
        self.log, self.rng = log, rng
        self.lock = threading.RLock()
        self.samples = {XIAO: collections.deque(), HOLO: collections.deque()}
        self.zone = None
        self.rssi = None
        self.far_since = None
        self.far_seen = False
        self.lost = False
        self.current = None
        self.history = collections.deque(maxlen=HISTORY)
        self.next_near = 0.0
        self.zone_ready = 0.0
        self.chat_until = 0.0
        self.serial = 0  # counts interactions, so the player pulls the page once per interaction

    # ---------- what's happening ----------

    def _read_signal(self, now, reports):
        """(smoothed dBm or None, whether the boards have lost each other). reports: {side: (rssi, age_ms, at)}."""
        heard, silent = False, False
        for side, report in reports.items():
            if report is None or now - report[2] > REPORT_STALE_SECS:
                continue
            rssi, age_ms, at = report
            samples = self.samples[side]
            if rssi is None or age_ms > LOST_AGE_MS:
                silent = True
            else:
                heard = True
                if not samples or samples[-1][0] != at:
                    samples.append((at, rssi))
        medians = []
        for samples in self.samples.values():
            while samples and now - samples[0][0] > SAMPLE_SECS:
                samples.popleft()
            if samples:
                medians.append(statistics.median(v for _, v in samples))
        return (sum(medians) / len(medians) if medians and heard else None), silent and not heard

    def update(self, now, reports, temps, paused=False):
        """reports: {side: (rssi or None, age_ms, monotonic time) or None}; temps: {side: °C or None} for
        the chip, plus "board" for the HoloCubic. paused: nobody should be interrupted (a question, a dark screen)."""
        with self.lock:
            self.rssi, lost = self._read_signal(now, reports)
            if paused:
                self.cancel()
            self._say(now)
            if self.current and now >= self.current.end:
                self.current = None
            self._follow_zone(now, lost, temps, paused)

    def _follow_zone(self, now, lost, temps, paused):
        was_lost, self.lost = self.lost, lost
        if self.rssi is not None:
            zone = zone_of(self.rssi, self.zone)
            if zone != self.zone:
                self.log.info(f"peer zone {self.zone} -> {zone} ({self.rssi:.0f} dBm)")
                if zone == "near":
                    self.next_near = now
                self.zone = zone
                self.far_since = now if zone == "far" else None
                self.far_seen = False
        else:  # nobody heard: how close it used to be no longer means anything
            self.zone, self.far_since, self.far_seen = None, None, False
        if paused or self.current:
            return
        if lost and not was_lost:
            self.log.info("peer lost")
            self._begin_zone("lost", now, first=next(iter(self._alive_sides(temps)), XIAO))
        elif was_lost and not lost and self.rssi is not None:
            self.log.info("peer back")
            self._begin_zone("back", now)
        elif self.zone == "near" and now >= self.next_near:
            self._begin(self._pick_near(temps), now, temps=temps)
            self.next_near = self.current.end + self.rng.uniform(*NEAR_AGAIN_SECS)
        elif self.zone == "far" and not self.far_seen and now - self.far_since >= FAR_DWELL_SECS:
            self.far_seen = True
            self._begin_zone("far", now)

    def _alive_sides(self, temps):
        return [side for side in (XIAO, HOLO) if temps.get(side) is not None]

    def _begin_zone(self, kind, now, first=None):
        if now >= self.zone_ready:
            self.zone_ready = now + ZONE_COOLDOWN_SECS
            self._begin(kind, now, first)

    def _pick_near(self, temps):
        weights = dict(NEAR_WEIGHTS)
        if (temps.get("board") or 0) >= HOT_BOARD or (temps.get(XIAO) or 0) >= HOT_CHIP:
            weights["hot"] = HOT_WEIGHT
        return self.rng.choices(list(weights), list(weights.values()))[0]

    def _begin(self, kind, now, first=None, temps=None):
        temps = temps or {}
        if first is None:
            first = HOLO if kind == "hot" else self.rng.choice((XIAO, HOLO))  # the hot one complains
        script = self.rng.choice(SCRIPTS[kind])
        temp = temps.get(first)
        self.current = Interaction(script, first, now, None if temp is None else round(temp))
        self.serial += 1
        self.log.info(f"buddy {kind} starts from {first} ({self.rssi and round(self.rssi)} dBm)")

    def _say(self, now):
        it = self.current
        if not it:
            return
        for line in it.script.lines[it.said:]:
            if now - it.start < line.at:
                break
            text = line.text.format(temp="好幾十" if it.temp is None else it.temp)
            self.history.append((it.side_of(line.who), text, line.mood))
            it.said += 1

    def jump(self, side, now):
        """Asked for by a person: this board's character visits the other one. False if something else is
        on, or the other board can't be heard: there's nobody to visit, so it says so instead."""
        with self.lock:
            if self.current:
                return False
            if self.rssi is None:
                self._begin("lost", now, side)
                return False
            self._begin("fight", now, side)
            return True

    def cancel(self):
        with self.lock:
            self.current = None

    def peek(self, now):
        with self.lock:
            self.chat_until = now + CHAT_PEEK_SECS

    def active(self, now):
        with self.lock:
            return bool(self.current and now < self.current.end)

    def wants_pull(self, now):
        """The serial of an interaction that should bring the round display to the chat page, else None."""
        with self.lock:
            return self.serial if self.active(now) and self.current.script.pull else None

    def holo_shows_chat(self, now):
        with self.lock:
            return self.active(now) or now < self.chat_until

    # ---------- drawing ----------

    def render(self, side, now, t, temps=None):
        """temps: the HoloCubic's (board, chip) for its bottom line; the round display gets its page dots."""
        with self.lock:
            it = self.current if self.active(now) else None
            history = list(self.history)[-2:]
            rssi = self.rssi
        S = 2
        img = Image.new("RGB", (240 * S, 240 * S), clawd.BG if side == XIAO else (0, 0, 0))
        d = ImageDraw.Draw(img)
        if side == XIAO:
            d.ellipse((6, 6, 240 * S - 6, 240 * S - 6), outline=clawd.RING["idle"], width=clawd.s(4, S))
        draw_header(d, rssi, S)
        draw_stage(d, side, it, now - it.start if it else 0.0, t, S)
        draw_chat(d, history, side, S)
        if side == HOLO:
            img = ImageEnhance.Brightness(ImageEnhance.Color(img).enhance(clawd.HOLO_COLOR)).enhance(clawd.HOLO_BRIGHTNESS)
            d = ImageDraw.Draw(img)
        img = img.resize((240, 240), Image.LANCZOS)
        d = ImageDraw.Draw(img)
        if side == HOLO:
            clawd.draw_holo_temps(d, temps or (None, None))
        else:
            clawd.draw_page_dots(d, PAGE_INDEX, 1)
        out = io.BytesIO()
        img.save(out, "JPEG", quality=85, subsampling=0)
        return out.getvalue()


PAGE_INDEX = 5  # same as PAGE_CHAT in the firmware

STAGE_Y = 78
LEAVE_START, LEAVE_END = 0.5, 1.1      # squat, then up and out of its own screen
ARRIVE_START, ARRIVE_END = 0.9, 1.6    # dropping into the other one
RETURN_LEAD = 1.6                      # seconds before the end the visitor heads home
OFF_TOP = -70


def s(v, S):
    return clawd.s(v, S)


def draw_header(d, rssi, S):
    text = "聽不到對方" if rssi is None else f"{label_of(rssi)} · {rssi:.0f} dBm"
    d.text((s(120, S), s(20, S)), text, font=ImageFont.truetype(clawd.FONT_LIGHT, s(10.5, S)), fill=(165, 160, 155), anchor="mm")


def draw_eyes(d, proj, xf, mood, t, S):
    """Expression on the front face, drawn over the eyeless ("closed") model."""
    def line(pts, fill=voxel.EYE, width=2.2):
        d.line([proj(xf(x, y, -0.02)) for x, y in pts], fill=fill, width=s(width, S), joint="curve")

    for ec in (3.5, 7.5):
        if mood == "smile":
            line([(ec - .9, 2.6), (ec, 1.7), (ec + .9, 2.6)])
        elif mood == "surprised":
            pts = [proj(xf(ec + .85 * math.cos(i * math.pi / 8), 2.1 + 1.15 * math.sin(i * math.pi / 8), -0.02)) for i in range(16)]
            d.polygon(pts, fill=voxel.EYE)
        elif mood == "angry":
            lean = 1 if ec < 5 else -1
            line([(ec - 1, 1.2 - .5 * lean), (ec + 1, 1.2 + .5 * lean)], width=2.6)
            line([(ec, 2.0), (ec, 3.0)], width=3)
        elif mood == "hot":
            line([(ec - .9, 2.4), (ec + .9, 2.4)])
        elif mood == "sad":
            lean = 1 if ec < 5 else -1
            line([(ec - .9, 1.4 + .5 * lean), (ec + .9, 1.4 - .5 * lean)], width=1.8)
            line([(ec, 2.1), (ec, 3.0)], width=3)
        elif mood == "dizzy":
            line([(ec - .9, 1.5), (ec + .9, 3.1)])
            line([(ec - .9, 3.1), (ec + .9, 1.5)])
        else:  # look
            line([(ec + .4, 1.6), (ec + .4, 3.0)], width=3)


def draw_actor(d, x, y, cell, mood, t, S, bob=0.0, yaw=0.0, roll=0.0, arms=(0, 0), legs=(0, 0, 0, 0)):
    """The character, in 240-space. Returns the point above its head."""
    sw, sy = cell * 5.0, y + 4.9 * cell
    if y > -20:
        d.ellipse((s(x - sw, S), s(sy - 3, S), s(x + sw, S), s(sy + 3, S)), fill=(10, 9, 9))
    proj, xf = voxel.render(d, voxel.build("closed", legs, arms), x, y, cell, yaw, bob=bob, S=S, roll=roll)
    draw_eyes(d, proj, xf, mood, t, S)
    hx, hy = proj(xf(5.5, 0, 2))
    return hx / S, hy / S


def draw_bangs(d, t, S):
    """Exclamation marks popping up either side of the head, clear of the face."""
    font = ImageFont.truetype(clawd.FONT, s(24, S))
    for i, x in enumerate((60, 180)):
        ph = (t * .6 + i / 2) % 1.0
        d.text((s(x, S), s(66 - ph * 18, S)), "!", font=font, fill=(255, 215, 90), anchor="mm",
               stroke_width=s(1.5, S), stroke_fill=(60, 40, 10))


def draw_steam(d, hx, hy, t, S):
    for i in range(4):
        ph = (t * .7 + i / 4) % 1.0
        x, y = hx + (i - 1.5) * 18 + 4 * math.sin(ph * 6), hy - 2 - ph * 13  # stays under the signal line
        r, g = 2.5 + ph * 4, int(190 - 120 * ph)
        d.ellipse((s(x - r, S), s(y - r, S), s(x + r, S), s(y + r, S)), fill=(g, g, g))
    for side in (-1, 1):  # sweat
        y = hy + 8 + (t * 40 % 26)
        x = hx + side * 40
        d.polygon([(s(x, S), s(y - 5, S)), (s(x - 3, S), s(y + 2, S)), (s(x + 3, S), s(y + 2, S))], fill=(120, 190, 255))
        d.ellipse((s(x - 3, S), s(y - 1, S), s(x + 3, S), s(y + 4, S)), fill=(120, 190, 255))


def draw_telescope(d, hx, hy, S):
    x0, y0, x1, y1 = hx + 12, hy + 16, hx + 46, hy - 2
    d.line([(s(x0, S), s(y0, S)), (s(x1, S), s(y1, S))], fill=(150, 130, 90), width=s(7, S))
    d.line([(s(x1 - 8, S), s(y1 + 4, S)), (s(x1 + 3, S), s(y1 - 2, S))], fill=(200, 180, 130), width=s(11, S))


def draw_lost_waves(d, hx, hy, t, S):
    for i in range(3):
        r = 7 + i * 6
        col = (70, 70, 80) if i >= int(t * 1.5) % 4 else (150, 190, 230)
        d.arc((s(hx - r, S), s(hy - 3 - r, S), s(hx + r, S), s(hy - 3 + r, S)), 220, 320, fill=col, width=s(2.5, S))


def draw_cloud(d, t, S):
    """A cartoon scuffle: a jittering dust ball with limbs and stars flying out."""
    frame = int(t * 5)
    rng = random.Random(frame)
    for _ in range(9):
        # dark enough to survive the HoloCubic's brightness push; low enough to clear the messages
        x, y = 120 + rng.uniform(-42, 42), 78 + rng.uniform(-16, 16)
        r, g = rng.uniform(16, 26), rng.randint(85, 135)
        d.ellipse((s(x - r, S), s(y - r, S), s(x + r, S), s(y + r, S)), fill=(g, g, g - 8))
    for _ in range(5):
        a, n = rng.uniform(0, 6.28), rng.uniform(40, 62)
        x, y = 120 + math.cos(a) * n, 80 + math.sin(a) * n * .6
        d.line([(s(x, S), s(y, S)), (s(x + math.cos(a) * 13, S), s(y + math.sin(a) * 10, S))], fill=voxel.ORANGE, width=s(6, S))
    for _ in range(4):
        x, y, k = 120 + rng.uniform(-62, 62), 80 + rng.uniform(-40, 30), rng.uniform(5, 9)
        d.polygon([(s(px, S), s(py, S)) for px, py in star_points(x, y, k)], fill=(255, 220, 120))
    word = ("碰！", "啪！", "咚！")[frame // 3 % 3]
    d.text((s(120 + rng.uniform(-8, 8), S), s(78 + rng.uniform(-5, 5), S)), word,
           font=ImageFont.truetype(clawd.FONT, s(24, S)), fill=(255, 240, 200), anchor="mm",
           stroke_width=s(2, S), stroke_fill=(30, 26, 22))


def star_points(x, y, k):
    return [(x, y - k), (x + k * .3, y - k * .3), (x + k, y), (x + k * .3, y + k * .3),
            (x, y + k), (x - k * .3, y + k * .3), (x - k, y), (x - k * .3, y - k * .3)]


def fly(e, start, end, y_from, y_to):
    f = max(0.0, min(1.0, (e - start) / (end - start)))
    return y_from + (y_to - y_from) * f * f


def draw_jump(d, side, it, e, t, S):
    secs, visitor = it.script.secs, it.first
    home = side == visitor
    away_at = secs - RETURN_LEAD
    if home:
        if e < LEAVE_END:  # squat, then gone
            squat = 0.5 if LEAVE_START - .35 < e < LEAVE_START else 0.0
            draw_actor(d, 120, fly(e, LEAVE_START, LEAVE_END, STAGE_Y, OFF_TOP), 8, "smile", t, S, bob=-squat)
        elif e >= away_at + .4:  # dropping back in
            draw_actor(d, 120, fly(e, away_at + .4, away_at + 1.1, OFF_TOP, STAGE_Y) if e < away_at + 1.1 else STAGE_Y,
                       8, "smile", t, S)
        else:
            d.text((s(120, S), s(STAGE_Y, S)), AWAY_NOTE.format(NAMES[other(side)]),
                   font=ImageFont.truetype(clawd.FONT_LIGHT, s(14, S)), fill=(170, 165, 160), anchor="mm")
        return
    arrived = ARRIVE_END <= e < away_at
    leaving = e >= away_at
    if arrived:
        if e < away_at - 1.5:
            draw_cloud(d, t, S)
        else:  # the dust settles on two dizzy heads
            draw_actor(d, 86, STAGE_Y, 6.5, "dizzy", t, S, roll=.14 * math.sin(t * 5))
            draw_actor(d, 154, STAGE_Y, 6.5, "dizzy", t, S, roll=-.14 * math.sin(t * 5))
        return
    if leaving:
        draw_actor(d, 92, fly(e, away_at, away_at + .6, STAGE_Y, OFF_TOP), 6.5, "smile", t, S)
    elif e >= ARRIVE_START:
        draw_actor(d, 92, fly(e, ARRIVE_START, ARRIVE_END, OFF_TOP, STAGE_Y), 6.5, "smile", t, S)
    draw_actor(d, 148, STAGE_Y, 6.5, "look" if e < ARRIVE_END else "smile", t, S)


def draw_stage(d, side, it, e, t, S):
    if not it:
        draw_actor(d, 120, STAGE_Y, 8, "smile", t, S, bob=math.sin(t * 2) * .2, yaw=math.sin(t * .6) * .4)
        return
    if it.script.jump:
        draw_jump(d, side, it, e, t, S)
        return
    kind = it.script.kind
    mood = next((ln.mood for ln in reversed(it.script.lines) if e >= ln.at and it.side_of(ln.who) == side),
                it.script.lines[0].mood)
    if kind == "hello":
        wave = int(t * 4) % 2
        draw_actor(d, 120, STAGE_Y, 8, mood, t, S, arms=(0, -1.6 if wave else -.6), yaw=.15)
    elif kind == "surprise":
        draw_actor(d, 120, STAGE_Y, 8, mood, t, S, bob=abs(math.sin(t * 3)) * .3)
        draw_bangs(d, t, S)
    elif kind == "hot":
        hx, hy = draw_actor(d, 120, STAGE_Y, 8, mood, t, S, roll=.04 * math.sin(t * 9))
        draw_steam(d, hx, hy, t, S)
    elif kind == "far":
        hx, hy = draw_actor(d, 110, STAGE_Y, 8, mood, t, S, yaw=-.45 + .2 * math.sin(t * 1.2))
        draw_telescope(d, hx, hy, S)
    elif kind == "lost":
        hx, hy = draw_actor(d, 120, STAGE_Y + 6, 8, mood, t, S, bob=-.6)
        draw_lost_waves(d, hx, hy, t, S)
    else:  # back
        draw_actor(d, 120, STAGE_Y, 8, mood, t, S, bob=abs(math.sin(t * 5)) * 1.1, yaw=t * 2.5)
        draw_bangs(d, t, S)


def draw_face(d, x, y, who, mood, S):
    """A small head beside a message: the speaker's colour ring tells who it is."""
    r = 11
    d.rounded_rectangle((s(x - r, S), s(y - r, S), s(x + r, S), s(y + r, S)), radius=s(5, S), fill=voxel.ORANGE,
                        outline=FACE_RING[who], width=s(2, S))
    for ex in (x - 5, x + 5):
        if mood == "surprised":
            d.ellipse((s(ex - 2.6, S), s(y - 4, S), s(ex + 2.6, S), s(y + 3, S)), fill=voxel.EYE)
        elif mood == "smile":
            d.arc((s(ex - 3, S), s(y - 3, S), s(ex + 3, S), s(y + 3, S)), 200, 340, fill=voxel.EYE, width=s(1.6, S))
        elif mood == "dizzy":
            d.line([(s(ex - 2.5, S), s(y - 3, S)), (s(ex + 2.5, S), s(y + 2, S))], fill=voxel.EYE, width=s(1.5, S))
            d.line([(s(ex - 2.5, S), s(y + 2, S)), (s(ex + 2.5, S), s(y - 3, S))], fill=voxel.EYE, width=s(1.5, S))
        elif mood == "hot":
            d.line([(s(ex - 3, S), s(y, S)), (s(ex + 3, S), s(y, S))], fill=voxel.EYE, width=s(1.6, S))
        else:
            d.line([(s(ex, S), s(y - 3, S)), (s(ex, S), s(y + 2, S))], fill=voxel.EYE, width=s(2.2, S))
    if mood == "angry":
        d.line([(s(x - 8, S), s(y - 7, S)), (s(x - 2, S), s(y - 4, S))], fill=voxel.EYE, width=s(1.5, S))
        d.line([(s(x + 8, S), s(y - 7, S)), (s(x + 2, S), s(y - 4, S))], fill=voxel.EYE, width=s(1.5, S))
    elif mood == "sad":
        d.line([(s(x - 8, S), s(y - 4, S)), (s(x - 2, S), s(y - 7, S))], fill=voxel.EYE, width=s(1.5, S))
        d.line([(s(x + 8, S), s(y - 4, S)), (s(x + 2, S), s(y - 7, S))], fill=voxel.EYE, width=s(1.5, S))


def draw_chat(d, lines, side, S):
    """The last two messages, your own board's on the right; the round screen narrows towards the bottom."""
    font = ImageFont.truetype(clawd.FONT_LIGHT, s(13, S))
    if not lines:
        d.text((s(120, S), s(160, S)), "靠近就會聊天", font=font, fill=(130, 125, 120), anchor="mm")
        return
    rows = {1: (160,), 2: (145, 180)}[len(lines)]
    for (who, text, mood), y in zip(lines, rows):
        mine = who == side
        width = d.textlength(text, font=font) / S + 16
        limit = 142 if y < 170 else 128
        if width > limit:
            text = clawd.fit(d, text, font, s(limit - 16, S))
            width = limit
        total = width + 28
        left = 120 - total / 2
        if mine:
            face_x, x0 = left + total - 11, left
        else:
            face_x, x0 = left + 11, left + 28
        d.rounded_rectangle((s(x0, S), s(y - 13, S), s(x0 + width, S), s(y + 13, S)), radius=s(10, S),
                            fill=(66, 54, 44) if mine else (42, 46, 58))
        d.text((s(x0 + width / 2, S), s(y, S)), text, font=font, fill=(245, 242, 235), anchor="mm")
        draw_face(d, face_x, y, who, mood, S)
