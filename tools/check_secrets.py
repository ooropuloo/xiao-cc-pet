# -*- coding: utf-8 -*-
"""Refuse to publish secrets. Never prints a secret value.

    python tools/check_secrets.py            check the files staged for commit (git hook)
    python tools/check_secrets.py <dir>      check every file under <dir>

Secret values are read from the local firmware/ytplayer/secrets.h (ignored by git) and the Telegram
.env named by XIAO_TG_ENV (or --secrets-h / --env). Also blocks secrets.h, .env and *.bin by name,
and generic shapes: a Telegram bot token, a long token in a /s/<token> share link.
"""
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GENERIC = {
    "telegram bot token": re.compile(rb"\b\d{8,10}:[A-Za-z0-9_-]{30,}"),
    "share-link token": re.compile(rb"/s/[A-Za-z0-9_-]{20,}"),
}
BLOCKED_NAMES = re.compile(r"(^|/)(secrets\.h|\.env|[^/]*\.env)$|\.bin$")


def arg(name, default):
    return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else default


def secret_values():
    vals = {}
    sh = Path(arg("--secrets-h", ROOT / "firmware" / "ytplayer" / "secrets.h"))
    if sh.exists():
        for k, v in re.findall(r'#define\s+(\w+)\s+"([^"]*)"', sh.read_text(encoding="utf-8")):
            if k == "WIFI_SSID" or len(v) < 6:
                continue
            vals[k] = v
            if k == "DOOR_URL":
                for seg in re.findall(r"[A-Za-z0-9_-]{16,}", v):
                    vals["DOOR_TOKEN"] = seg
    env = arg("--env", os.environ.get("XIAO_TG_ENV", ""))
    if env and Path(env).exists():
        for line in Path(env).read_text(encoding="utf-8").splitlines():
            if line.startswith("TELEGRAM_BOT_TOKEN="):
                vals["TG_TOKEN"] = line.split("=", 1)[1].strip()
    return {k: v.encode() for k, v in vals.items() if v}


def files_to_check():
    dirs = [a for a in sys.argv[1:] if not a.startswith("--") and Path(a).is_dir()]
    if dirs:
        base = Path(dirs[0])
        for p in base.rglob("*"):
            if p.is_file() and ".git" not in p.parts:
                yield p.relative_to(base).as_posix(), p.read_bytes()
        return
    names = subprocess.run(["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z"],
                           capture_output=True, cwd=ROOT).stdout.decode().split("\0")
    for n in filter(None, names):
        data = subprocess.run(["git", "show", f":{n}"], capture_output=True, cwd=ROOT).stdout
        yield n, data


def main():
    vals = secret_values()
    bad = []
    for name, data in files_to_check():
        if BLOCKED_NAMES.search(name):
            bad.append(f"{name}: file type not allowed")
            continue
        for k, v in vals.items():
            if v in data:
                bad.append(f"{name}: contains {k}")
        for k, rx in GENERIC.items():
            if rx.search(data):
                bad.append(f"{name}: looks like a {k}")
    print(f"checked with {len(vals)} local secret values ({', '.join(sorted(vals)) or 'none found'})")
    if bad:
        print("BLOCKED:\n  " + "\n  ".join(bad))
        sys.exit(1)
    print("ok: no secrets found")


if __name__ == "__main__":
    main()
