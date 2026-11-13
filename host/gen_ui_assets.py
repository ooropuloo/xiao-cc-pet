# -*- coding: utf-8 -*-
"""Render the locally drawn pages as JPEGs and emit firmware/ytplayer/ui_assets.h.

Door page: one image per state; the firmware paints the hold ring and Wi-Fi dot.
System page: static labels and bar tracks; the firmware paints values and bar fills.
Geometry constants must match DOOR_* / RING_* / SYS_* in ytplayer.ino.
"""
import io
import math
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

S = 3
W = 240 * S
CX, CY = 120, 112
BTN_R = 56
RING_IN, RING_OUT = 64, 71
FONT = "C:/Windows/Fonts/msjhbd.ttc"
FONT_LIGHT = "C:/Windows/Fonts/msjh.ttc"

STATES = {
    "idle":      {"c1": (255, 150, 70), "c2": (232, 89, 12), "icon": "door", "text": "長按開門", "tc": (235, 235, 240)},
    "opening":   {"c1": (200, 120, 60), "c2": (150, 60, 10), "icon": "door_open", "text": "開門中…", "tc": (255, 170, 90)},
    "ok":        {"c1": (120, 230, 120), "c2": (40, 160, 70), "icon": "check", "text": "已開門", "tc": (130, 230, 130)},
    "fail_net":  {"c1": (255, 110, 110), "c2": (190, 40, 50), "icon": "wifi_off", "text": "連不上門禁", "tc": (255, 120, 120)},
    "fail_busy": {"c1": (255, 190, 90), "c2": (210, 120, 20), "icon": "wait", "text": "門正在動作，稍後再試", "tc": (255, 200, 110)},
    "fail":      {"c1": (255, 110, 110), "c2": (190, 40, 50), "icon": "cross", "text": "開門失敗", "tc": (255, 120, 120)},
}


def s(v):
    return int(round(v * S))


def background():
    img = Image.new("RGB", (W, W), (8, 9, 13))
    glow = Image.new("L", (W, W), 0)
    gd = ImageDraw.Draw(glow)
    for r in range(150, 0, -2):
        gd.ellipse((s(CX - r), s(CY - r), s(CX + r), s(CY + r)), fill=int(40 * (1 - r / 150) ** 1.5))
    tint = Image.new("RGB", (W, W), (40, 46, 66))
    return Image.composite(tint, img, glow)


def radial_button(c1, c2):
    r = s(BTN_R)
    size = 2 * r
    btn = Image.new("RGB", (size, size))
    px = btn.load()
    hx, hy = size * 0.38, size * 0.30  # highlight toward top-left
    maxd = math.hypot(size, size) * 0.75
    for y in range(size):
        for x in range(size):
            t = min(1.0, math.hypot(x - hx, y - hy) / maxd)
            px[x, y] = tuple(int(c1[i] + (c2[i] - c1[i]) * t) for i in range(3))
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size - 1, size - 1), fill=255)
    return btn, mask


def draw_icon(d, kind, gap=(0, 0, 0)):
    white = (255, 255, 255)
    lw = s(4)
    if kind in ("door", "door_open"):
        # frame
        fx0, fy0, fx1, fy1 = CX - 17, CY - 25, CX + 17, CY + 25
        d.rounded_rectangle((s(fx0), s(fy0), s(fx1), s(fy1)), radius=s(3), outline=white, width=lw)
        if kind == "door":
            d.ellipse((s(CX + 8) - s(3), s(CY + 2) - s(3), s(CX + 8) + s(3), s(CY + 2) + s(3)), fill=white)
        else:
            # leaf swung open toward the viewer (perspective trapezoid)
            leaf = [(s(fx0), s(fy0)), (s(fx0 + 22), s(fy0 - 6)), (s(fx0 + 22), s(fy1 + 6)), (s(fx0), s(fy1))]
            d.polygon(leaf, fill=white)
            d.ellipse((s(fx0 + 15) - s(2.5), s(CY) - s(2.5), s(fx0 + 15) + s(2.5), s(CY) + s(2.5)), fill=(200, 110, 40))
    elif kind == "check":
        d.line([(s(CX - 20), s(CY + 1)), (s(CX - 6), s(CY + 15)), (s(CX + 21), s(CY - 14))], fill=white, width=s(7), joint="curve")
    elif kind == "cross":
        for a, b in (((-15, -15), (15, 15)), ((-15, 15), (15, -15))):
            d.line([(s(CX + a[0]), s(CY + a[1])), (s(CX + b[0]), s(CY + b[1]))], fill=white, width=s(7))
    elif kind == "wifi_off":
        # Wi-Fi fan with a slash: "can't reach the door controller", not "the door refused"
        oy = CY + 17  # fan origin, so the fan sits centred in the button
        for r in (14, 25, 36):
            d.arc((s(CX - r), s(oy - r), s(CX + r), s(oy + r)), 225, 315, fill=white, width=s(5))
        d.ellipse((s(CX - 4.5), s(oy - 4.5), s(CX + 4.5), s(oy + 4.5)), fill=white)
        a, b = (s(CX - 25), s(CY - 23)), (s(CX + 25), s(CY + 25))
        d.line([a, b], fill=gap, width=s(10))  # cut the fan so the slash reads cleanly
        d.line([a, b], fill=white, width=s(5))
    elif kind == "wait":
        d.ellipse((s(CX - 20), s(CY - 20), s(CX + 20), s(CY + 20)), outline=white, width=lw)
        d.line([(s(CX), s(CY - 11)), (s(CX), s(CY)), (s(CX + 9), s(CY + 6))], fill=white, width=lw, joint="curve")


PAGES = 6
SYS_BG = (11, 12, 17)
# rows: (icon, y of the value line); bars sit at y + 10. Uptime row has no bar.
SYS_ROWS = [("wifi", 66), ("ram", 100), ("flash", 134), ("cpu", 168)]
SYS_UP_Y = 196
SYS_ICON_X = 38
SYS_BAR_X0, SYS_BAR_X1, SYS_BAR_H = 54, 204, 4
ICON_COL = (165, 170, 190)


def draw_sys_icon(d, kind, cx, cy):
    c = ICON_COL
    lw = s(1.6)
    if kind == "wifi":
        for r in (3.5, 7, 10.5):
            d.arc((s(cx - r), s(cy + 4 - r), s(cx + r), s(cy + 4 + r)), 225, 315, fill=c, width=lw)
        d.ellipse((s(cx - 1.4), s(cy + 2.6), s(cx + 1.4), s(cy + 5.4)), fill=c)
    elif kind == "ram":
        d.rounded_rectangle((s(cx - 7), s(cy - 4), s(cx + 7), s(cy + 4)), radius=s(1), outline=c, width=lw)
        for i in range(4):
            x = cx - 4.5 + i * 3
            d.line([(s(x), s(cy + 4)), (s(x), s(cy + 7))], fill=c, width=lw)
            d.rectangle((s(x - 0.8), s(cy - 1.5), s(x + 0.8), s(cy + 1.5)), fill=c)
    elif kind == "flash":
        pts = [(cx - 5, cy - 7), (cx + 2, cy - 7), (cx + 5, cy - 4), (cx + 5, cy + 7), (cx - 5, cy + 7)]
        d.polygon([(s(x), s(y)) for x, y in pts], outline=c, width=lw)
        for i in range(3):
            x = cx - 2.5 + i * 2.2
            d.line([(s(x), s(cy - 5)), (s(x), s(cy - 2))], fill=c, width=s(1))
    elif kind == "cpu":
        d.rounded_rectangle((s(cx - 5), s(cy - 5), s(cx + 5), s(cy + 5)), radius=s(1), outline=c, width=lw)
        d.rectangle((s(cx - 2), s(cy - 2), s(cx + 2), s(cy + 2)), fill=c)
        for i in (-2.5, 0, 2.5):
            for a, b in (((cx + i, cy - 5), (cx + i, cy - 8)), ((cx + i, cy + 5), (cx + i, cy + 8)),
                         ((cx - 5, cy + i), (cx - 8, cy + i)), ((cx + 5, cy + i), (cx + 8, cy + i))):
                d.line([(s(a[0]), s(a[1])), (s(b[0]), s(b[1]))], fill=c, width=s(1.2))
    elif kind == "clock":
        d.ellipse((s(cx - 7), s(cy - 7), s(cx + 7), s(cy + 7)), outline=c, width=lw)
        d.line([(s(cx), s(cy - 4.5)), (s(cx), s(cy)), (s(cx + 3.5), s(cy + 2))], fill=c, width=lw, joint="curve")


def page_dots(d, active):
    xs = [120 + (i - (PAGES - 1) / 2) * 12 for i in range(PAGES)]  # same row as clawd.draw_page_dots
    for i, x in enumerate(xs):
        col = (255, 255, 255) if i == active else (70, 70, 80)
        d.ellipse((s(x - 3), s(225 - 3), s(x + 3), s(225 + 3)), fill=col)


def render_sys():
    img = Image.new("RGB", (W, W), SYS_BG)
    d = ImageDraw.Draw(img)
    ft = ImageFont.truetype(FONT, s(14))
    d.text((s(120), s(24)), "系統狀態", font=ft, fill=(235, 235, 240), anchor="mm")
    for icon, y in SYS_ROWS:
        draw_sys_icon(d, icon, SYS_ICON_X, y + 4)
        d.rounded_rectangle((s(SYS_BAR_X0), s(y + 10), s(SYS_BAR_X1), s(y + 10 + SYS_BAR_H)), radius=s(2), fill=(36, 39, 50))
    draw_sys_icon(d, "clock", SYS_ICON_X + 8, SYS_UP_Y)
    page_dots(d, 2)
    return img.resize((240, 240), Image.LANCZOS)


def render(state):
    st = STATES[state]
    img = background()

    # soft glow under the button
    glow = Image.new("L", (W, W), 0)
    ImageDraw.Draw(glow).ellipse((s(CX - BTN_R - 4), s(CY - BTN_R + 2), s(CX + BTN_R + 4), s(CY + BTN_R + 10)), fill=150)
    glow = glow.filter(ImageFilter.GaussianBlur(s(10)))
    img = Image.composite(Image.new("RGB", (W, W), tuple(int(c * 0.55) for c in st["c2"])), img, glow)

    d = ImageDraw.Draw(img)
    # progress track (firmware paints the fill over this)
    d.ellipse((s(CX - RING_OUT), s(CY - RING_OUT), s(CX + RING_OUT), s(CY + RING_OUT)), fill=(34, 37, 48))
    d.ellipse((s(CX - RING_IN), s(CY - RING_IN), s(CX + RING_IN), s(CY + RING_IN)), fill=(10, 11, 16))

    btn, mask = radial_button(st["c1"], st["c2"])
    img.paste(btn, (s(CX - BTN_R), s(CY - BTN_R)), mask)
    d = ImageDraw.Draw(img)
    # thin inner rim highlight
    d.ellipse((s(CX - BTN_R), s(CY - BTN_R), s(CX + BTN_R), s(CY + BTN_R)), outline=tuple(min(255, c + 40) for c in st["c1"]), width=s(1))
    draw_icon(d, st["icon"], gap=btn.getpixel((btn.width // 2, btn.height // 2)))

    ft = ImageFont.truetype(FONT_LIGHT, s(12))
    d.text((s(120), s(30)), "D1 大門", font=ft, fill=(140, 145, 160), anchor="mm")
    size = 17 if len(st["text"]) <= 6 else 13
    fs = ImageFont.truetype(FONT, s(size))
    d.text((s(120), s(202)), st["text"], font=fs, fill=st["tc"], anchor="mm")

    page_dots(d, 1)

    return img.resize((240, 240), Image.LANCZOS)


def main(out_h, preview_dir=None):
    lines = ["// Generated by host/gen_ui_assets.py — do not edit.", "#pragma once", "#include <Arduino.h>", ""]
    images = [(f"IMG_DOOR_{n.upper()}", f"door_{n}", render(n)) for n in STATES]
    images.append(("IMG_SYS_BG", "sys_bg", render_sys()))
    for sym, fname, img in images:
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=92)
        data = buf.getvalue()
        if preview_dir:
            img.save(Path(preview_dir) / f"{fname}.png")
        hexes = ",".join(f"0x{b:02x}" for b in data)
        lines.append(f"const uint8_t {sym}[] PROGMEM = {{{hexes}}};")
        lines.append(f"const uint32_t {sym}_LEN = {len(data)};")
        print(fname, len(data), "bytes")
    Path(out_h).write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)
