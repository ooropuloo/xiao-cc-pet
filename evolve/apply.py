# -*- coding: utf-8 -*-
"""Act on the user's Telegram answer to a proposal.

    python apply.py 更新 P-20261102-claude     apply (backup, copy, restart player / OTA, send a fresh screenshot)
    python apply.py 跳過 P-20261102-claude     mark skipped (the survey won't propose the idea again)
    python apply.py 還原 P-20261102-claude     undo an applied proposal from its backup
    python apply.py 清單                        list proposals

Prints one short Chinese result; the Telegram bot replies with it.
"""
import datetime as dt
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
HISTORY = HERE / "history.json"
BASH = os.environ.get("XIAO_BASH", r"C:\Program Files\Git\bin\bash.exe")


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def load(pid):
    mf = HERE / "proposals" / pid / "manifest.json"
    if not mf.exists():
        raise SystemExit(f"找不到提案 {pid}")
    return mf, json.loads(mf.read_text(encoding="utf-8"))


def save(mf, m):
    mf.write_text(json.dumps(m, ensure_ascii=False, indent=2), encoding="utf-8")


def remember(m, status):
    try:
        hist = json.loads(HISTORY.read_text(encoding="utf-8"))
    except Exception:
        hist = []
    hist = [h for h in hist if h["id"] != m["id"]]
    hist.append({"id": m["id"], "page": m["page"], "title": m.get("title", ""), "kind": m.get("kind", ""),
                 "holiday_key": m.get("holiday_key"), "status": status, "date": dt.date.today().isoformat()})
    HISTORY.write_text(json.dumps(hist, ensure_ascii=False, indent=2), encoding="utf-8")


def restart_player():
    """run-player.bat restarts player.py 3 s after it exits."""
    log = ROOT / "host" / "player.log"
    start = log.stat().st_size if log.exists() else 0
    subprocess.run(["powershell", "-NoProfile", "-Command",
                    "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
                    "Where-Object { $_.CommandLine -like '*player.py*' } | "
                    "ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"], capture_output=True)
    for _ in range(40):
        time.sleep(1)
        with open(log, "rb") as f:  # only lines written after the kill
            f.seek(start if log.stat().st_size >= start else 0)
            if b"link up" in f.read():
                return "player.py 已重啟並連上板子"
    return "player.py 已重啟，但 40 秒內沒看到連上板子，請看 host/player.log"


def ota():
    # utf-8: arduino-cli prints Chinese; the cp950 default crashed the reader thread and left stdout None (2026-11-11)
    r = subprocess.run([BASH, str(ROOT / "flash.sh"), "ota"], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=1800)
    out = (r.stdout or "") + (r.stderr or "")
    if r.returncode != 0 or "OTA upload done" not in (r.stdout or ""):
        return False, out[-300:]
    return True, "韌體已 OTA，板子重開中"


def screenshot(m):
    """Render the live page after applying and send it, so the answer comes with proof."""
    out = HERE / "backups" / m["id"] / "after"
    page = "claude" if m["page"] == "claude" else "player" if m["page"] == "player" else None
    try:
        import tg
        if page:
            args = ["cmd", "/c", str(HERE / "run-preview.bat"), "--page", page, "--out", str(out)]
            if m.get("kind") == "holiday" and m.get("holiday_date"):
                args += ["--date", m["holiday_date"]]
            subprocess.run(args, capture_output=True, timeout=300)
            tg.media(out / f"{page}.gif", f"{m['id']} 套用後（正式程式渲染）")
        else:
            out.mkdir(parents=True, exist_ok=True)
            subprocess.run([sys.executable, str(ROOT / "host" / "gen_ui_assets.py"), str(out / "ui_assets.h"), str(out)],
                           cwd=str(ROOT / "host"), capture_output=True, timeout=300)
            tg.media(out / "door_idle.png", f"{m['id']} 套用後的開門頁")
    except Exception as e:
        return f"（截圖沒送出：{e}）"
    return ""


def apply(pid):
    mf, m = load(pid)
    if m.get("status") not in ("ready", "asked"):
        return f"{pid} 目前是 {m.get('status')}，不能套用"
    w = mf.parent
    for c in m["changed"]:
        live = ROOT / c["path"]
        now = sha(live) if live.exists() else None
        if now != c["base"]:
            return f"{pid} 沒套用：{c['path']} 在提案之後被改過，內容對不上。明天的 survey 會用新版重做。"
    bk = HERE / "backups" / pid
    shutil.rmtree(bk, ignore_errors=True)
    for c in m["changed"]:
        live = ROOT / c["path"]
        if live.exists():
            (bk / "files" / c["path"]).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(live, bk / "files" / c["path"])
    for c in m["changed"]:
        live = ROOT / c["path"]
        live.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(w / c["path"], live)
    notes = []
    if m.get("needs_ota"):
        try:
            ok, msg = ota()
        except Exception as e:  # never leave the live files half applied
            ok, msg = False, f"{type(e).__name__}: {e}"
        if not ok:
            restore(m)
            return f"{pid} OTA 失敗，已把檔案還原：{msg}"
        notes.append(msg)
    if any(c["path"].startswith("host/") for c in m["changed"]):
        notes.append(restart_player())
    m["status"], m["applied"] = "applied", dt.date.today().isoformat()
    save(mf, m)
    remember(m, "applied")
    notes.append(screenshot(m))
    return f"✅ 已套用 {pid}：{m.get('title', '')}。" + "；".join(n for n in notes if n) + \
        f"\n不滿意就回「小圓盤 還原 {pid}」。"


def restore(m):
    bk = HERE / "backups" / m["id"] / "files"
    for c in m["changed"]:
        live = ROOT / c["path"]
        if c["base"] is None:
            live.unlink(missing_ok=True)
        elif (bk / c["path"]).exists():
            shutil.copy2(bk / c["path"], live)


def revert(pid):
    mf, m = load(pid)
    if m.get("status") != "applied":
        return f"{pid} 目前是 {m.get('status')}，沒有套用過"
    restore(m)
    notes = []
    if m.get("needs_ota"):
        notes.append(ota()[1])
    if any(c["path"].startswith("host/") for c in m["changed"]):
        notes.append(restart_player())
    m["status"] = "reverted"
    save(mf, m)
    remember(m, "reverted")
    return f"↩️ 已還原 {pid}。" + "；".join(notes)


def skip(pid):
    mf, m = load(pid)
    if m.get("status") not in ("ready", "asked"):
        return f"{pid} 目前是 {m.get('status')}"
    m["status"] = "skipped"
    save(mf, m)
    remember(m, "skipped")
    return f"好，跳過 {pid}（{m.get('title', '')}），之後不會再提這個點子。"


def listing():
    rows = []
    for mf in sorted((HERE / "proposals").glob("P-*/manifest.json"))[-15:]:
        m = json.loads(mf.read_text(encoding="utf-8"))
        rows.append(f"{m['id']}  {m.get('status')}  {m.get('title', '')}")
    return "\n".join(rows) or "還沒有提案"


def main():
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    verb = sys.argv[1]
    pid = sys.argv[2] if len(sys.argv) > 2 else ""
    fn = {"更新": apply, "套用": apply, "跳過": skip, "略過": skip, "還原": revert}.get(verb)
    if verb in ("清單", "list"):
        print(listing())
    elif fn and pid:
        print(fn(pid))
    else:
        print("用法：小圓盤 更新|跳過|還原 P-xxx，或 小圓盤 清單")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
