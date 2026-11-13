# -*- coding: utf-8 -*-
"""Answer Claude Code's AskUserQuestion from the round display.

A PreToolUse hook (host/device_answer.py) POSTs the questions to 127.0.0.1:5810/ask
and blocks; the player shows them on the Claude page, taps pick options, and the
answers go back as the HTTP response. No answer within `wait` seconds -> 204 and
the hook lets the terminal ask as usual. Localhost only, so no firewall rule needed.
In the board's night window the question also goes to Telegram as a card with one
button per option (NIGHT_WAIT seconds to answer). The ezt_tasks bot owns the bot's
updates, so it forwards a press ("xq:<rid>:<n>") to POST /tg here; this side edits the card.
"""
import json
import os
import queue
import secrets
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = 5810
MAX_HOLD = 70  # touches extend the wait, but never past this (device_answer.py waits 75 s)
NIGHT_WAIT = float(os.environ.get("XIAO_NIGHT_WAIT", "300"))  # device_answer.py / the hook timeout must exceed it
TG_ENV = os.environ.get("XIAO_TG_ENV", "D:/git/ezt_tasks/backend/.env")  # same bot as evolve/tg.py; never logged


def _tg(method, payload):
    env = {}
    for line in open(TG_ENV, encoding="utf-8").read().splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    data = urllib.parse.urlencode({"chat_id": env["TELEGRAM_CHAT_ID"], **payload}).encode()
    url = f"https://api.telegram.org/bot{env['TELEGRAM_BOT_TOKEN']}/{method}"
    with urllib.request.urlopen(url, data=data, timeout=20) as r:
        return json.loads(r.read().decode("utf-8"))


def card(c):
    """Card text + inline keyboard for snapshot `c` (see AskBridge._card)."""
    name = os.path.basename(c["project"].rstrip("/\\")) or "?"
    head = f"\U0001F319 Claude 在等你回答（{name}）"
    if c["final"]:
        lines = [head, ""]
        lines += [f"{q}\n→ {a}" for q, a in c["answers"].items()]
        lines.append({"answered": "✅ 已回答，Claude 繼續執行。", "timeout": "⌛ 逾時沒回答，已交給終端機。",
                      "terminal": "⌨️ 已改用終端機回答。"}[c["final"]])
        return "\n".join(lines), None
    q = c["q"]
    lines = [head + (f"　第 {c['idx'] + 1}/{c['total']} 題" if c["total"] > 1 else ""), "", q.get("question", "")]
    rows = []
    for i, o in enumerate(q.get("options", [])):
        if o.get("description"):
            lines.append(f"・{o.get('label', '')}：{o['description']}")
        mark = ("☑ " if i in c["selected"] else "☐ ") if q.get("multiSelect") else ""
        rows.append([{"text": mark + o.get("label", ""), "callback_data": f"xq:{c['rid']}:{i}"}])
    last = [{"text": "⌨️ 改用終端機", "callback_data": f"xq:{c['rid']}:term"}]
    if q.get("multiSelect"):
        last.insert(0, {"text": "✔ 完成", "callback_data": f"xq:{c['rid']}:done"})
    rows.append(last)
    return "\n".join(lines), {"inline_keyboard": rows}


class AskBridge:
    def __init__(self, log, port=PORT):
        self.log = log
        self.cond = threading.Condition()
        self.req = None
        self.version = 0  # bumps on every change so the player knows to redraw
        self.night = False  # set by the player from the board's night window
        self.cards = queue.Queue()  # card snapshots, sent / edited in order by one worker
        threading.Thread(target=self._card_worker, daemon=True).start()
        bridge = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _json(self, obj, code=200):
                data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
                if self.path == "/tg":  # a card button, forwarded by the ezt_tasks bot
                    self._json({"text": bridge.tg_press(str(body.get("rid", "")), str(body.get("n", "")))})
                    return
                if self.path != "/ask":
                    self.send_error(404)
                    return
                answers = bridge.ask(body.get("questions") or [], float(body.get("wait", 20)), body.get("project", ""))
                if answers is None:
                    self.send_response(204)
                    self.end_headers()
                    return
                self._json({"answers": answers})

        self.server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        log.info(f"ask bridge on 127.0.0.1:{port}")

    def ask(self, questions, wait, project):
        if not questions:
            return None
        with self.cond:
            if self.req is not None:
                return None  # one question set at a time; the other falls back to the terminal
            night = self.night
            if night:
                wait = NIGHT_WAIT
            self.req = {
                "questions": questions, "idx": 0, "answers": {}, "selected": set(),
                "deadline": time.monotonic() + wait, "done": False, "ok": False,
                "limit": time.monotonic() + (NIGHT_WAIT if night else MAX_HOLD),
                "project": project, "new": True,
                "rid": secrets.token_hex(3) if night else None, "final": None,
            }
            self.version += 1
            self.log.info(f"ask: {len(questions)} question(s), waiting {wait:.0f}s" + (" (night: TG card)" if night else ""))
            self._card(self.req)
            # deadline can move (touched()), so re-check it rather than trusting one long timeout
            while not self.req["done"] and time.monotonic() < self.req["deadline"]:
                self.cond.wait(timeout=0.5)
            req, self.req = self.req, None
            self.version += 1
            req["final"] = "answered" if req["ok"] else req["final"] or "timeout"
            self._card(req)
            self.log.info("ask: answered " + json.dumps(req["answers"], ensure_ascii=False) if req["ok"] else "ask: no answer on device, terminal takes over")
            return req["answers"] if req["ok"] else None

    # ---- Telegram card ----
    def _card(self, r):
        """Queue the card for request `r` as it is now (caller holds the lock). No-op by day."""
        if r.get("rid") is None:
            return
        i = min(r["idx"], len(r["questions"]) - 1)
        self.cards.put({"rid": r["rid"], "project": r["project"], "idx": r["idx"], "total": len(r["questions"]),
                        "q": r["questions"][i], "selected": set(r["selected"]), "final": r["final"],
                        "answers": dict(r["answers"])})

    def _card_worker(self):
        msg_ids = {}  # rid -> message_id of its card
        while True:
            c = self.cards.get()
            text, kb = card(c)
            try:
                if c["rid"] not in msg_ids:
                    if c["final"]:
                        continue  # never got to send the card: nothing to close
                    payload = {"text": text[:4000]}
                    if kb:
                        payload["reply_markup"] = json.dumps(kb, ensure_ascii=False)
                    msg_ids[c["rid"]] = _tg("sendMessage", payload)["result"]["message_id"]
                    self.log.info(f"ask: TG card sent ({c['rid']})")
                else:
                    payload = {"message_id": msg_ids[c["rid"]], "text": text[:4000]}
                    if kb:
                        payload["reply_markup"] = json.dumps(kb, ensure_ascii=False)
                    _tg("editMessageText", payload)
                if c["final"]:
                    msg_ids.pop(c["rid"], None)
            except Exception as e:
                # no str(e): the URL holds the token; "message is not modified" also lands here, harmlessly
                self.log.info(f"ask: TG card {c['rid']} failed: {type(e).__name__}")

    def tg_press(self, rid, n):
        """A card button: option index, "done" (multi-select) or "term" (hand it to the terminal)."""
        with self.cond:
            r = self.req
            if r is None or r.get("rid") != rid or r["done"]:
                return "這題已經結束"
            if n == "term":
                r["final"] = "terminal"
                r["done"] = True
                self.cond.notify_all()
                return "已交給終端機"
            q = r["questions"][r["idx"]]
            idx = -1 if n == "done" else int(n) if n.isdigit() else -2
            if idx == -2 or (idx == -1 and not r["selected"]):
                return "請先選一個" if idx == -1 else "?"
            label = "完成" if idx == -1 else q.get("options", [])[idx].get("label", "") if idx < len(q.get("options", [])) else ""
        self.choose(idx)
        return f"已選：{label}" if label else "?"

    def view(self):
        """Snapshot for rendering, or None when nothing is being asked."""
        with self.cond:
            r = self.req
            if r is None or r["done"]:  # answered, ask() just hasn't cleared it yet
                return None
            if time.monotonic() >= r["deadline"]:
                self.cond.notify_all()
                return None
            q = r["questions"][r["idx"]]
            new, r["new"] = r["new"], False
            return {
                "q": q, "idx": r["idx"], "total": len(r["questions"]), "selected": set(r["selected"]),
                "remaining": r["deadline"] - time.monotonic(), "new": new, "project": r["project"],
            }

    def touched(self, extend=10.0):
        """User is interacting: keep at least `extend` seconds on the clock."""
        with self.cond:
            if self.req is not None:
                self.req["deadline"] = min(self.req["limit"], max(self.req["deadline"], time.monotonic() + extend))

    def choose(self, option_index):
        """Tap on option row `option_index`; -1 = the multi-select Done button."""
        with self.cond:
            r = self.req
            if r is None:
                return
            q = r["questions"][r["idx"]]
            opts = q.get("options", [])
            if q.get("multiSelect"):
                if option_index == -1:
                    if not r["selected"]:
                        return
                    r["answers"][q["question"]] = ", ".join(opts[i]["label"] for i in sorted(r["selected"]))
                    self._next(r)
                elif 0 <= option_index < len(opts):
                    r["selected"] ^= {option_index}
            elif 0 <= option_index < len(opts):
                r["answers"][q["question"]] = opts[option_index]["label"]
                self._next(r)
            self.version += 1
            if not r["done"]:
                self._card(r)  # the board or the card moved on: keep the card in step
            self.cond.notify_all()

    def _next(self, r):
        r["idx"] += 1
        r["selected"] = set()
        if r["idx"] >= len(r["questions"]):
            r["done"] = r["ok"] = True
