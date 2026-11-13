# -*- coding: utf-8 -*-
"""09:30 job: send every ready proposal to Telegram with its previews, and ask.

    python ask.py [--dry]

Quiet when there is nothing new. Also reports a failed 04:00 survey, so a broken job isn't silent.
"""
import argparse
import datetime as dt
import json
from pathlib import Path

import tg

HERE = Path(__file__).resolve().parent
PAGE_NAME = {"claude": "Claude 頁（小橘人）", "player": "播放器頁", "door": "開門頁"}
KIND_NAME = {"holiday": "節日主題", "design": "設計改版", "fix": "修正"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()
    send_text = print if a.dry else tg.text
    send_media = (lambda p, c="": print("[media]", p, c)) if a.dry else tg.media

    today = dt.date.today().isoformat()
    st_file = HERE / "logs" / f"{today}.status.json"
    if not st_file.exists():
        send_text(f"⚠️ 小圓盤每日 survey：今天 {today} 04:00 沒有跑（找不到 status）。檢查排程 xiao-evolve-survey。")
    else:
        st = json.loads(st_file.read_text(encoding="utf-8"))
        if not st.get("ok"):
            send_text(f"⚠️ 小圓盤每日 survey 有問題：{st.get('note', '')[:600]}")

    for mf in sorted((HERE / "proposals").glob("P-*/manifest.json")):
        m = json.loads(mf.read_text(encoding="utf-8"))
        if m.get("status") != "ready":
            continue
        w = mf.parent
        head = f"🎨 小圓盤提案 {m['id']}\n{PAGE_NAME.get(m['page'], m['page'])}・{KIND_NAME.get(m.get('kind'), m.get('kind', ''))}"
        body = f"\n\n{m.get('title', '')}\n{m.get('summary', '')}"
        if m.get("why"):
            body += f"\n為什麼：{m['why']}"
        if m.get("needs_ota"):
            body += "\n（這個要更新韌體：套用時會自動 OTA，板子會重開一次）"
        files = [f for f in m.get("changed", [])]
        body += "\n改動：" + "、".join(c["path"] for c in files[:6])
        ask = f"\n\n按下面的按鈕，或回覆「小圓盤 更新 {m['id']}」／「小圓盤 跳過 {m['id']}」。"
        previews = [w / p for p in m.get("previews", []) if (w / p).exists()]
        if previews:
            send_media(previews[0], head)
            for p in previews[1:4]:
                send_media(p)
        buttons = [("✅ 更新", f"xiao:apply:{m['id']}"), ("⏭ 跳過", f"xiao:skip:{m['id']}")]
        if a.dry:
            print(head + body + ask, buttons)
        else:
            tg.text(head + body + ask, buttons)
        if not a.dry:
            m["status"] = "asked"
            m["asked"] = today
            mf.write_text(json.dumps(m, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
