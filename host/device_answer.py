# -*- coding: utf-8 -*-
"""PreToolUse hook for AskUserQuestion: let the round display answer.

Sends the questions to host/player.py (127.0.0.1:5810) and waits. If the device
answers, returns allow + updatedInput.answers so Claude Code skips the terminal
prompt. Any failure or no answer -> exit 0 with no output, and the terminal asks
as usual. Stdlib only so it runs under any Python.
"""
import json
import os
import sys
import urllib.request

WAIT = float(os.environ.get("XIAO_ASK_WAIT", "30"))
MAX_WAIT = 330  # day: <= 70 s; night: the TG card waits askbridge.NIGHT_WAIT (300). Under the hook timeout (340)
URL = "http://127.0.0.1:5810/ask"


def main():
    try:
        # Claude Code sends UTF-8; sys.stdin would decode it as cp950 on this machine
        payload = json.loads(sys.stdin.buffer.read().decode("utf-8"))
    except Exception:
        return
    tool_input = payload.get("tool_input") or {}
    questions = tool_input.get("questions") or []
    if not questions:
        return
    body = json.dumps({"questions": questions, "wait": WAIT, "project": payload.get("cwd", "")}).encode("utf-8")
    req = urllib.request.Request(URL, data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=MAX_WAIT) as r:
            if r.status != 200:
                return
            answers = json.loads(r.read().decode("utf-8")).get("answers")
    except Exception:
        return  # player not running / timed out: terminal asks instead
    if not answers:
        return
    out = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
            "permissionDecisionReason": "answered on the round display",
            "updatedInput": dict(tool_input, answers=answers),
        }
    }
    sys.stdout.write(json.dumps(out, ensure_ascii=True))


if __name__ == "__main__":
    main()
