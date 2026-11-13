# -*- coding: utf-8 -*-
"""Holidays the Claude page can dress up for (Taiwan + international), lunar dates computed.

    python holidays.py [YYYY-MM-DD] [days]   -> upcoming holidays as JSON lines

Each entry: name, date (the day itself), show_from / show_to (suggested theme window).
Needs the lunardate package (uv run --with lunardate).
"""
import datetime as dt
import json
import sys

from lunardate import LunarDate

# (key, name, kind, month, day, days shown before, days shown after); kind: solar | lunar | rule
FIXED = [
    ("newyear", "元旦／跨年", "solar", 1, 1, 3, 1),
    ("valentine", "西洋情人節", "solar", 2, 14, 2, 0),
    ("children", "兒童節", "solar", 4, 4, 2, 0),
    ("labor", "勞動節", "solar", 5, 1, 1, 0),
    ("fathers", "父親節（台灣 88）", "solar", 8, 8, 2, 0),
    ("teachers", "教師節", "solar", 9, 28, 1, 0),
    ("national", "國慶日", "solar", 10, 10, 2, 0),
    ("halloween", "萬聖節", "solar", 10, 31, 7, 0),
    ("christmas", "聖誕節", "solar", 12, 25, 7, 1),
    ("lunar_ny", "農曆新年（初一）", "lunar", 1, 1, 3, 5),  # 除夕 is the day before, inside the window
    ("lantern", "元宵節", "lunar", 1, 15, 2, 0),
    ("dragon", "端午節", "lunar", 5, 5, 3, 0),
    ("qixi", "七夕情人節", "lunar", 7, 7, 2, 0),
    ("ghost", "中元節", "lunar", 7, 15, 2, 0),
    ("midautumn", "中秋節", "lunar", 8, 15, 4, 0),
    ("mothers", "母親節", "rule", 5, 0, 2, 0),         # second Sunday of May
    ("winter_solstice", "冬至（吃湯圓）", "rule", 12, 0, 1, 0),
]


def _date(kind, y, m, d):
    if kind == "solar":
        return dt.date(y, m, d)
    if kind == "lunar":
        return LunarDate(y, m, d).to_solar_date()
    if m == 5:  # second Sunday of May
        first = dt.date(y, 5, 1)
        return first + dt.timedelta(days=(6 - first.weekday()) % 7 + 7)
    return dt.date(y, 12, 22 if y % 4 in (2, 3) else 21)  # 冬至 in Taiwan time (UTC+8), 2024-2031


def upcoming(today=None, days=45):
    today = today or dt.date.today()
    out = []
    for y in (today.year, today.year + 1):
        for key, name, kind, m, d, before, after in FIXED:
            day = _date(kind, y, m, d)
            if today <= day + dt.timedelta(days=after) and day - today <= dt.timedelta(days=days):
                out.append(dict(key=f"{key}{y}", name=name, date=day.isoformat(), days_left=(day - today).days,
                                show_from=(day - dt.timedelta(days=before)).isoformat(),
                                show_to=(day + dt.timedelta(days=after)).isoformat()))
    return sorted(out, key=lambda h: h["date"])


if __name__ == "__main__":
    t = dt.date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else None
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 45
    for h in upcoming(t, n):
        print(json.dumps(h, ensure_ascii=False))
