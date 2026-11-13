# -*- coding: utf-8 -*-
"""Soft tick cues for Claude Code state changes, played on the PC (the board has no speaker).

Only states that ask for attention make a sound; "running" flips on every tool call
and would be noise. WAVs are synthesized on first use into host/sounds/.
Set "F" (soft ticks) from the listening page docs/sfx-variants.html, chosen 2026-11-02 and made
louder than on the page; the old 8-bit square cues were too harsh.
"""
import math
import random
import struct
import time
import wave
import winsound
from pathlib import Path

RATE = 22050
SOUND_DIR = Path(__file__).with_name("sounds")
COOLDOWN = 15.0
PEAK = 0.7       # 0..1 of full scale; the listening page had these at 0.35, asked for louder
VERSION = "tick2"  # part of the file name: a new version is synthesized instead of reusing old WAVs

# state -> (start s, tone Hz, length s): a sine "tock" with a short noise click on top
CUES = {
    "waiting": [(0.0, 1200, 0.16), (0.11, 1600, 0.16)],   # two rising ticks: needs you
    "jumping": [(0.0, 1400, 0.20)],                       # one bright tick: turn done
    "failed": [(0.0, 400, 0.24)],                         # one low knock: tool error
}


def _tick(freq, dur, seed):
    rnd = random.Random(seed)
    n = int(RATE * dur)
    out = []
    for i in range(n):
        t = i / RATE
        tone = math.sin(2 * math.pi * freq * t) * min(1.0, t / 0.002) * math.exp(-t / 0.06)
        click = rnd.gauss(0, 1) * math.exp(-t / 0.004) * 0.25
        out.append(tone + click)
    return out


def _render(parts):
    total = max(st + d for st, _, d in parts) + 0.02
    buf = [0.0] * int(RATE * total)
    for st, f, d in parts:
        i0 = int(st * RATE)
        for j, v in enumerate(_tick(f, d, int(f))):
            if i0 + j < len(buf):
                buf[i0 + j] += v
    peak = max(abs(v) for v in buf) or 1.0
    k = PEAK / peak
    return b"".join(struct.pack("<h", int(max(-1.0, min(1.0, v * k)) * 32767)) for v in buf)


def sound_path(state):
    return SOUND_DIR / f"{state}_{VERSION}.wav"


def ensure_sounds():
    SOUND_DIR.mkdir(exist_ok=True)
    for name, parts in CUES.items():
        p = sound_path(name)
        if p.exists():
            continue
        with wave.open(str(p), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(RATE)
            w.writeframes(_render(parts))


class StateSounds:
    def __init__(self, log, enabled=True):
        self.log = log
        self.enabled = enabled
        self.quiet = False  # night mode on the board: cues are skipped, not queued
        self.prev = None  # project -> state; None until the first snapshot
        self.last_played = {}
        if enabled:
            ensure_sounds()

    def update(self, agents):
        cur = {a["project"]: a.get("state") for a in agents}
        if self.prev is None:  # don't replay whatever was already true at startup
            self.prev = cur
            return
        for project, state in cur.items():
            if state != self.prev.get(project) and state in CUES:
                self._play(project, state)
        self.prev = cur

    def play_now(self, state):
        """Play a cue right away (no per-project cooldown), e.g. when a question reaches the device."""
        self.last_played[("*", state)] = time.monotonic()
        if self.enabled and not self.quiet and state in CUES:
            winsound.PlaySound(str(sound_path(state)), winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)

    def _play(self, project, state):
        now = time.monotonic()
        if now - self.last_played.get((project, state), -COOLDOWN) < COOLDOWN:
            return
        self.last_played[(project, state)] = now
        self.log.info(f"sfx {state} ({project})" + (" muted (night)" if self.quiet else ""))
        if self.enabled and not self.quiet:
            winsound.PlaySound(str(sound_path(state)), winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
