# -*- coding: utf-8 -*-
"""Temperature page: the board's chip with a 5-minute trend, plus the HoloCubic's readings.

PC-drawn like the Claude page. The chip sensor is uncalibrated, so watch the trend, not the degree.
"""
import io

import clawd

from PIL import Image, ImageDraw, ImageFont

PAGE_INDEX = 4  # same as PAGE_TEMP in the firmware
HISTORY = 60    # samples kept for the trend: one every 5 s from the board = 5 minutes
BG = (11, 12, 17)
TEXT, MUTED = (235, 235, 240), (140, 140, 150)
BAND_NONE, BAND_OK, BAND_HOT = (60, 62, 72), (32, 110, 70), (175, 40, 30)
TREND_BOX = (46, 132, 194, 160)
TREND_OK, TREND_HOT, TREND_GRID = (90, 200, 210), (255, 90, 70), (40, 42, 52)
TREND_MIN_SPAN = 4  # °C the trend's height covers at least: sensor noise shouldn't look like a swing


def trend_points(history, box=TREND_BOX, min_span=TREND_MIN_SPAN):
    """Line points for the readings, newest at the right edge; a short history starts part way along."""
    x0, y0, x1, y1 = box
    low, high = min(history), max(history)
    mid, span = (low + high) / 2, max(high - low, min_span)
    step = (x1 - x0) / (HISTORY - 1)
    first = x1 - (len(history) - 1) * step
    return [(first + i * step, y1 - (v - mid + span / 2) / span * (y1 - y0)) for i, v in enumerate(history)]


def status_of(chip):
    """(band text, band colour, whether it counts as hot) for a chip reading or None."""
    if chip is None:
        return "沒有資料", BAND_NONE, False
    return ("偏熱", BAND_HOT, True) if chip >= clawd.CHIP_WARN else ("正常", BAND_OK, False)


def render(chip, history, holo_temps=None):
    """chip: fresh °C or None; history: recent readings, oldest first;
    holo_temps: (board, chip) or None when the HoloCubic isn't driven by this player."""
    S = 2
    img = Image.new("RGB", (240 * S, 240 * S), BG)
    d = ImageDraw.Draw(img)
    label, band, hot = status_of(chip)
    d.rounded_rectangle(tuple(v * S for v in (84, 104, 156, 126)), radius=11 * S, fill=band)
    x0, y0, x1, y1 = TREND_BOX
    d.line([(x0 * S, y1 * S), (x1 * S, y1 * S)], fill=TREND_GRID, width=S)
    if len(history) >= 2:
        d.line([(x * S, y * S) for x, y in trend_points(history)], fill=TREND_HOT if hot else TREND_OK, width=2 * S, joint="curve")
    clawd.draw_page_dots(d, PAGE_INDEX, S)
    img = img.resize((240, 240), Image.LANCZOS)

    # text on the final frame like render_holo: draw_holo_temps works in 240-space
    d = ImageDraw.Draw(img)
    f = lambda size, face=clawd.FONT: ImageFont.truetype(face, size)
    d.text((120, 36), "XIAO 晶片", font=f(14), fill=MUTED, anchor="mm")
    big = "--" if chip is None else f"{chip:.0f}°"
    d.text((120, 74), big, font=f(52), fill=clawd.HOLO_TEMP_HOT if hot else TEXT, anchor="mm")
    d.text((120, 115), label, font=f(13), fill=(255, 255, 255), anchor="mm")
    if history:
        span = f"近 {max(1, round(len(history) * 5 / 60))} 分鐘  {min(history):.0f}–{max(history):.0f}°"
        d.text((120, 171), span, font=f(10, clawd.FONT_LIGHT), fill=MUTED, anchor="mm")
    if holo_temps is not None:
        d.text((120, 188), "HoloCubic", font=f(10, clawd.FONT_LIGHT), fill=MUTED, anchor="mm")
        clawd.draw_holo_temps(d, holo_temps, y=204, size=14)
    out = io.BytesIO()
    img.save(out, "JPEG", quality=85)
    return out.getvalue()
