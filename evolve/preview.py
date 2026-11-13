# -*- coding: utf-8 -*-
"""Render what the board would show, from the live host/ or a proposal's copy of it.

    python preview.py --host <dir with clawd.py> --page claude --date 2026-12-24 --out <dir>
    python preview.py --host <dir with player.py> --page player --out <dir>

Writes <out>/<page>.png (contact sheet, 4 per row) and <out>/<page>.gif (animated).
Run it with the player's uv environment (see run-preview.bat): player.py imports winrt.
"""
import argparse
import io
import os
import sys
import time
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
FONT = "C:/Windows/Fonts/msjhbd.ttc"

POSES = [  # same set as docs/clawd-poses.html
    ("思考中", dict(state="running", activity="thinking", statusText="思考中")),
    ("敲鍵盤", dict(state="running", activity="typing", statusText="編輯 clawd.py")),
    ("倒地冒煙", dict(state="failed", statusText="工具出錯了")),
    ("睡覺", dict(state="idle", statusText="待命中", asleep=True)),
    ("讀檔張望", dict(state="review", activity="reading", statusText="讀取 player.py")),
    ("等你授權", dict(state="waiting", statusText="等你授權：執行: npm test")),
    ("回合完成", dict(state="jumping", statusText="回合完成")),
    ("待命", dict(state="idle", statusText="待命中")),
]


def sheet(frames, names, out_png):
    cols = 4
    rows = (len(frames) + cols - 1) // cols
    img = Image.new("RGB", (cols * 250, rows * 272), (24, 22, 21))
    d = ImageDraw.Draw(img)
    f = ImageFont.truetype(FONT, 15)
    for i, (fr, name) in enumerate(zip(frames, names)):
        x, y = (i % cols) * 250 + 5, (i // cols) * 272 + 5
        img.paste(fr, (x, y))
        d.text((x + 120, y + 252), name, font=f, fill=(230, 220, 210), anchor="mm")
    img.save(out_png)


def claude_page(out):
    import clawd
    now = time.time() * 1000
    stills, names, anim = [], [], []
    for name, fields in POSES:
        f = dict(fields)
        asleep = f.pop("asleep", False)
        agent = dict(id=name, project="D:/git/xiao_esp32", lastEventAt=now - (300000 if asleep else 0), **f)
        clawd._pose_since[:] = [None, 0]
        for i in range(16):  # 1.6 s per pose at 10 fps
            jpg, _ = clawd.render([agent], True, 0, 100 + i * 0.1)
            fr = Image.open(io.BytesIO(jpg)).convert("RGB")
            anim.append(fr)
            if i == 8:
                stills.append(fr)
                names.append(name)
    sheet(stills, names, out / "claude.png")
    anim[0].save(out / "claude.gif", save_all=True, append_images=anim[1:], duration=100, loop=0)


def player_page(out):
    import player
    cover = Image.new("RGB", (480, 480))
    dc = ImageDraw.Draw(cover)
    for y in range(480):  # stand-in album art: a warm gradient with a disc
        dc.line([(0, y), (480, y)], fill=(200 - y // 4, 90 + y // 8, 120 + y // 5))
    dc.ellipse((120, 120, 360, 360), fill=(30, 26, 40))
    dc.ellipse((220, 220, 260, 260), fill=(230, 200, 120))
    states = [
        ("播放中", dict(cover=cover, title="Lo-fi Beats to Code To", artist="Chill Station", position=83, duration=214, playing=True), None),
        ("暫停", dict(cover=cover, title="Lo-fi Beats to Code To", artist="Chill Station", position=83, duration=214, playing=False), None),
        ("按下一首", dict(cover=cover, title="Lo-fi Beats to Code To", artist="Chill Station", position=83, duration=214, playing=True), "next"),
        ("沒有媒體", dict(cover=None, title="", artist="", position=0, duration=0, playing=False), None),
    ]
    stills, names = [], []
    for name, st, pressed in states:
        stills.append(Image.open(io.BytesIO(player.render(st, pressed))).convert("RGB"))
        names.append(name)
    sheet(stills, names, out / "player.png")
    stills[0].save(out / "player.gif", save_all=True, append_images=stills[1:], duration=900, loop=0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default=str(ROOT / "host"))
    ap.add_argument("--page", choices=("claude", "player"), default="claude")
    ap.add_argument("--date", help="pretend today is YYYY-MM-DD (holiday themes)")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    if a.date:
        os.environ["XIAO_THEME_DATE"] = a.date
    sys.path.insert(0, a.host)
    os.chdir(a.host)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (claude_page if a.page == "claude" else player_page)(out)
    print("wrote", out / f"{a.page}.png", out / f"{a.page}.gif")


if __name__ == "__main__":
    main()
