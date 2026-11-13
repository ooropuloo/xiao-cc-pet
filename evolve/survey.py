# -*- coding: utf-8 -*-
"""04:00 job: let Claude survey designs / upcoming holidays and build proposals in sandboxes.

    python survey.py [--dry]      (--dry: set up workspaces and print the prompt, don't run Claude)

Each page gets a workspace evolve/proposals/P-<date>-<page>/ holding a copy of the live code.
Claude edits only there; afterwards this script
  * restores any live file Claude changed (and voids that run's proposals),
  * drops workspaces without manifest.json,
  * records which live files each proposal changes (+ their hashes, for conflict checks),
  * compiles door proposals (firmware) to make sure they build.
"""
import argparse
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
PROPOSALS = HERE / "proposals"
LOGS = HERE / "logs"
HISTORY = HERE / "history.json"
CLAUDE = Path(os.environ.get("XIAO_CLAUDE",
                             r"C:\Program Files\nodejs\node_modules\@anthropic-ai\claude-code\bin\claude.exe"))
UV = Path(os.environ["USERPROFILE"]) / ".local" / "bin" / "uv.exe"
FQBN = "esp32:esp32:XIAO_ESP32C3:CDCOnBoot=default,PartitionScheme=min_spiffs"
PAGES = ("claude", "player", "door")
TIMEOUT = 60 * 60

# live files Claude must not touch (relative to ROOT)
GUARD_DIRS = ["host", "firmware/ytplayer", "docs", "evolve", "scripts"]
SKIP_PARTS = {"__pycache__", "build", "proposals", "logs", "backups", ".snap"}
SKIP_SUFFIX = (".log", ".log.1", ".log.2", ".err.log")
SKIP_NAMES = {"desktop.json", "history.json"}


def guarded_files():
    out = []
    for d in GUARD_DIRS:
        for p in (ROOT / d).rglob("*"):
            rel = p.relative_to(ROOT)
            if p.is_file() and not (set(rel.parts) & SKIP_PARTS) and not p.name.endswith(SKIP_SUFFIX) \
                    and p.name not in SKIP_NAMES:
                out.append(rel.as_posix())
    out.append("flash.sh")
    return out


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def copy_tree(src, dst, skip_names=()):
    def ignore(_, names):
        return [n for n in names if n in SKIP_PARTS or n in skip_names or n.endswith(SKIP_SUFFIX)]
    shutil.copytree(src, dst, ignore=ignore)


def upcoming_holidays(today):
    r = subprocess.run([str(UV), "run", "-q", "--with", "lunardate", "python", str(HERE / "holidays.py"),
                        today.isoformat(), "30"], capture_output=True, text=True, encoding="utf-8",
                       env={**os.environ, "PYTHONIOENCODING": "utf-8"}, timeout=300)
    return [json.loads(l) for l in r.stdout.splitlines() if l.strip()]


def load_history():
    try:
        return json.loads(HISTORY.read_text(encoding="utf-8"))
    except Exception:
        return []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()
    today = dt.date.today()
    stamp = today.strftime("%Y%m%d")
    LOGS.mkdir(exist_ok=True)
    PROPOSALS.mkdir(exist_ok=True)
    log_md = LOGS / f"{today.isoformat()}.md"
    status = {"date": today.isoformat(), "started": time.strftime("%H:%M:%S"), "ok": False, "proposals": [], "note": ""}
    status_file = LOGS / f"{today.isoformat()}.status.json"

    # 1. snapshot of the live files
    snap = HERE / ".snap" / stamp
    shutil.rmtree(snap, ignore_errors=True)
    before = {}
    for rel in guarded_files():
        before[rel] = sha(ROOT / rel)
        (snap / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / rel, snap / rel)

    # 2. one workspace per page
    ws = {}
    for page in PAGES:
        w = PROPOSALS / f"P-{stamp}-{page}"
        if (w / "manifest.json").exists():
            continue  # already ran today
        shutil.rmtree(w, ignore_errors=True)
        copy_tree(ROOT / "host", w / "host", skip_names={"desktop.json", "sounds"})
        if page == "door":
            copy_tree(ROOT / "firmware" / "ytplayer", w / "firmware" / "ytplayer", skip_names={"secrets.h"})
        (w / "preview").mkdir()
        ws[page] = w
    if not ws:
        print("already surveyed today")
        return

    hist = load_history()
    themes = sorted(p.stem for p in (ROOT / "host" / "themes").glob("*.py") if p.stem != "__init__")
    hol = upcoming_holidays(today)
    prompt = (HERE / "survey_prompt.md").read_text(encoding="utf-8").format(
        today=today.isoformat(),
        holidays="\n".join(f"  - {h['key']}：{h['name']} {h['date']}（還有 {h['days_left']} 天；顯示 {h['show_from']}～{h['show_to']}）" for h in hol) or "  （無）",
        themes="、".join(themes) or "（無）",
        history="\n".join(f"  - {h['id']} [{h['page']}] {h['title']} → {h['status']}" for h in hist[-40:]) or "  （尚無）",
        ws_claude=ws.get("claude", "(今天已做過)"), ws_player=ws.get("player", "(今天已做過)"),
        ws_door=ws.get("door", "(今天已做過)"), preview_bat=HERE / "run-preview.bat", log_md=log_md)
    (LOGS / f"{today.isoformat()}.prompt.md").write_text(prompt, encoding="utf-8")
    if a.dry:
        print(prompt)
        return

    # 3. run Claude
    allowed = ["Read", "Write", "Edit", "Glob", "Grep", "WebSearch", "WebFetch",
               "Bash(python *)", "Bash(cmd //c *)", "Bash(ls *)", "Bash(cp *)", "Bash(mkdir *)",
               "Bash(cat *)", "Bash(diff *)", "Bash(cd *)", "Bash(PYTHONIOENCODING=utf-8 *)"]
    denied = ["Read(**/secrets.h)", "Bash(*secrets*)"]
    t0 = time.time()
    try:
        r = subprocess.run([str(CLAUDE), "-p", "--output-format", "json", "--permission-mode", "acceptEdits",
                            "--allowedTools", *allowed, "--disallowedTools", *denied],
                           input=prompt, capture_output=True, text=True, encoding="utf-8",
                           cwd=str(PROPOSALS), timeout=TIMEOUT)
        (LOGS / f"{today.isoformat()}.claude.json").write_text(r.stdout or "", encoding="utf-8")
        if r.returncode != 0:
            status["note"] = f"claude exit {r.returncode}: {(r.stderr or r.stdout)[-400:]}"
    except subprocess.TimeoutExpired:
        status["note"] = f"claude timed out after {TIMEOUT // 60} min"
    status["minutes"] = round((time.time() - t0) / 60, 1)

    # 4. guard: restore anything live that changed
    touched = [rel for rel in before if not (ROOT / rel).exists() or sha(ROOT / rel) != before[rel]]
    touched += [rel for rel in guarded_files() if rel not in before and not rel.startswith("evolve/logs")]
    for rel in touched:
        if rel in before:
            shutil.copy2(snap / rel, ROOT / rel)
        else:
            (ROOT / rel).unlink(missing_ok=True)
    if touched:
        status["note"] += f" | 改到正式檔案已還原，本次提案作廢：{', '.join(touched[:8])}"
        for w in ws.values():
            shutil.rmtree(w, ignore_errors=True)
        ws = {}

    # 5. keep workspaces with a manifest, describe what they change
    for page, w in ws.items():
        mf = w / "manifest.json"
        if not mf.exists():
            shutil.rmtree(w, ignore_errors=True)
            continue
        try:
            m = json.loads(mf.read_text(encoding="utf-8"))
        except Exception as e:
            status["note"] += f" | {w.name} manifest 壞了：{e}"
            shutil.rmtree(w, ignore_errors=True)
            continue
        changed = []
        for p in w.rglob("*"):
            rel_ws = p.relative_to(w).as_posix()
            # only code can go live; previews and scratch files Claude leaves around never do
            if not p.is_file() or not rel_ws.startswith(("host/", "firmware/")) or "__pycache__" in rel_ws:
                continue
            live = ROOT / rel_ws
            if not live.exists() or sha(live) != sha(p):
                changed.append({"path": rel_ws, "base": sha(live) if live.exists() else None})
        if not changed:
            shutil.rmtree(w, ignore_errors=True)
            continue
        hd = next((h["date"] for h in hol if h["key"] == m.get("holiday_key")), None)
        if hd:
            m["holiday_date"] = hd
        m.update(id=w.name, page=page, changed=changed, status="ready", created=today.isoformat(),
                 needs_ota=any(c["path"].startswith("firmware/") for c in changed))
        m["previews"] = [p for p in m.get("previews", []) if (w / p).exists()] or \
            [p.relative_to(w).as_posix() for p in sorted((w / "preview").glob("*.*"))][:6]
        if m["needs_ota"]:
            m["compile_ok"] = compile_check(w)
            if not m["compile_ok"]:
                m["status"] = "broken"
        mf.write_text(json.dumps(m, ensure_ascii=False, indent=2), encoding="utf-8")
        status["proposals"].append({"id": m["id"], "title": m.get("title", ""), "status": m["status"]})

    shutil.rmtree(snap, ignore_errors=True)
    status["ok"] = not status["note"]
    status["finished"] = time.strftime("%H:%M:%S")
    status_file.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(status, ensure_ascii=False))


def compile_check(w):
    """Build the proposal's firmware with the real secrets.h copied in for the duration only."""
    fw = w / "firmware" / "ytplayer"
    sec = fw / "secrets.h"
    shutil.copy2(ROOT / "firmware" / "ytplayer" / "secrets.h", sec)
    try:
        r = subprocess.run([str(ROOT / "tools" / "arduino-cli.exe"), "compile", "-b", FQBN,
                            "--output-dir", str(w / "build-check"), str(fw)],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=1800)
        (w / "build-check.log").write_text((r.stdout or "")[-4000:] + (r.stderr or "")[-4000:], encoding="utf-8")
        return r.returncode == 0
    finally:
        sec.unlink(missing_ok=True)
        shutil.rmtree(w / "build-check", ignore_errors=True)


if __name__ == "__main__":
    main()
