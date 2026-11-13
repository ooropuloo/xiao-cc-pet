# -*- coding: utf-8 -*-
"""Telegram helpers. Credentials only come from ezt_tasks' .env and are never printed."""
import json
import os
from pathlib import Path

import requests

# a .env with TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID; override with XIAO_TG_ENV
ENV = Path(os.environ.get("XIAO_TG_ENV", "D:/git/ezt_tasks/backend/.env"))


def _env():
    env = {}
    for line in ENV.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return env["TELEGRAM_BOT_TOKEN"], env["TELEGRAM_CHAT_ID"]


def _post(method, data, files=None):
    token, chat = _env()
    r = requests.post(f"https://api.telegram.org/bot{token}/{method}", data={"chat_id": chat, **data},
                      files=files, timeout=120)
    if not r.ok:
        raise RuntimeError(f"telegram {method} {r.status_code}: {r.text[:200]}")
    return r.json()


def text(msg, buttons=None):
    """buttons: [(label, callback_data), ...] shown as one row under the message."""
    data = {"text": msg[:4000]}
    if buttons:
        data["reply_markup"] = json.dumps({"inline_keyboard": [[{"text": t, "callback_data": c} for t, c in buttons]]})
    return _post("sendMessage", data)


def media(path, caption=""):
    path = Path(path)
    method, field = ("sendAnimation", "animation") if path.suffix.lower() == ".gif" else ("sendPhoto", "photo")
    with open(path, "rb") as f:
        return _post(method, {"caption": caption[:1000]}, files={field: (path.name, f)})
