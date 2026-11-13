# -*- coding: utf-8 -*-
"""Candidate sound sets for the Claude-state cues, to pick one by ear.

    python sfx_variants.py <out_dir> <page_template> <page_out>

Writes <out_dir>/<set>/<cue>.wav (22.05 kHz mono 16-bit) and a listening page with every
clip embedded. Cues: waiting (needs you), jumping (turn done), failed (tool error).
"""
import base64
import io
import json
import math
import sys
import wave
from pathlib import Path

import numpy as np

SR = 22050


def t_axis(sec):
    return np.arange(int(SR * sec)) / SR


def env(sec, attack=0.005, decay=0.25):
    t = t_axis(sec)
    a = np.clip(t / attack, 0, 1)
    return a * np.exp(-t / decay)


def place(parts, total):
    """parts: [(start_sec, signal)] mixed into one buffer of `total` seconds"""
    out = np.zeros(int(SR * total))
    for start, sig in parts:
        i = int(start * SR)
        n = min(len(sig), len(out) - i)
        out[i:i + n] += sig[:n]
    return out


# ---------- voices ----------
def square(f, sec, vol=0.02):  # the current sfx.py voice, for comparison
    t = t_axis(sec)
    return np.sign(np.sin(2 * math.pi * f * t)) * vol if f else np.zeros_like(t)


def sine_ding(f, sec=0.55):
    t = t_axis(sec)
    return np.sin(2 * math.pi * f * t) * env(sec, 0.02, 0.16)


def marimba(f, sec=0.35):
    t = t_axis(sec)
    s = np.sin(2 * math.pi * f * t) + 0.35 * np.sin(2 * math.pi * f * 4 * t) * np.exp(-t / 0.03)
    return s * env(sec, 0.002, 0.09)


def bell(f, sec=1.1):
    t = t_axis(sec)
    mod = 2.0 * np.exp(-t / 0.25) * np.sin(2 * math.pi * f * 3.5 * t)  # FM: bright strike, mellow tail
    return np.sin(2 * math.pi * f * t + mod) * env(sec, 0.003, 0.35)


def drop(f0, f1, sec=0.16):
    t = t_axis(sec)
    f = f0 * (f1 / f0) ** (t / sec)  # exponential glide
    ph = 2 * math.pi * np.cumsum(f) / SR
    return np.sin(ph) * env(sec, 0.003, 0.06)


def tick(f, sec=0.12):
    t = t_axis(sec)
    rng = np.random.default_rng(int(f))
    click = rng.standard_normal(len(t)) * np.exp(-t / 0.004) * 0.25
    return (np.sin(2 * math.pi * f * t) * env(sec, 0.002, 0.04) + click)


def square_seq(notes):
    return np.concatenate([square(f, d) for f, d in notes])


# ---------- sets ----------
C5, D5, E5, F5, G5, A5, B5, C6, D6, E6, G6 = 523.25, 587.33, 659.25, 698.46, 783.99, 880.0, 987.77, 1046.5, 1174.66, 1318.5, 1568.0
SETS = {
    "A": ("現在的 8-bit 方波", "目前在用的聲音，當對照組。", {
        "waiting": square_seq([(784, 0.09), (0, 0.03), (1175, 0.16)]),
        "jumping": square_seq([(523, 0.07), (659, 0.07), (784, 0.07), (1047, 0.16)]),
        "failed": square_seq([(220, 0.12), (0, 0.05), (196, 0.18)]),
    }),
    "B": ("柔和叮咚", "正弦波加淡出，跟 ccpet 桌寵同一種聲音，最不刺耳。", {
        "waiting": place([(0, sine_ding(A5)), (0.16, sine_ding(D6))], 0.75),
        "jumping": place([(0, sine_ding(C6, 0.4)), (0.1, sine_ding(E6, 0.4)), (0.2, sine_ding(G6, 0.6))], 0.85),
        "failed": place([(0, sine_ding(E5, 0.5)), (0.18, sine_ding(C5, 0.7))], 0.9),
    }),
    "C": ("木琴", "有敲擊感、收得很快，像辦公室的輕提醒。", {
        "waiting": place([(0, marimba(G5)), (0.12, marimba(C6))], 0.5),
        "jumping": place([(0, marimba(C5)), (0.08, marimba(E5)), (0.16, marimba(G5)), (0.24, marimba(C6))], 0.6),
        "failed": place([(0, marimba(D5)), (0.14, marimba(A5 / 2))], 0.5),
    }),
    "D": ("小鐘", "FM 合成的鐘，尾音比較長、比較有存在感。", {
        "waiting": place([(0, bell(E6) * 0.6)], 1.1),
        "jumping": place([(0, bell(C6) * 0.5), (0.18, bell(G6) * 0.5)], 1.3),
        "failed": place([(0, bell(F5 / 2) * 0.6)], 1.1),
    }),
    "E": ("水滴", "音高會滑動的「啵」，俏皮、很短。", {
        "waiting": place([(0, drop(900, 1500)), (0.14, drop(900, 1500))], 0.35),
        "jumping": place([(0, drop(600, 1200)), (0.09, drop(800, 1600)), (0.18, drop(1000, 2000))], 0.4),
        "failed": place([(0, drop(700, 300, 0.25))], 0.3),
    }),
    "F": ("極簡輕敲", "很短、很低調，只想知道有事就好。", {
        "waiting": place([(0, tick(1200)), (0.1, tick(1600))], 0.25),
        "jumping": place([(0, tick(1400, 0.15))], 0.2),
        "failed": place([(0, tick(400, 0.18))], 0.22),
    }),
}
CUE_NAMES = {"waiting": "等你回答", "jumping": "一輪完成", "failed": "工具出錯"}


def to_wav(sig, peak=0.35):
    if np.max(np.abs(sig)) > 0:
        sig = sig / np.max(np.abs(sig)) * peak  # same peak for every clip so they compare fairly
    if len(sig) > 200:
        fade = np.linspace(1, 0, 200)
        sig[-200:] *= fade
    pcm = (np.clip(sig, -1, 1) * 32767).astype("<i2").tobytes()
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm)
    return buf.getvalue()


def main():
    out, tpl, page = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
    data = []
    for key, (name, note, cues) in SETS.items():
        d = out / key
        d.mkdir(parents=True, exist_ok=True)
        clips = {}
        for cue, sig in cues.items():
            wav = to_wav(sig.copy())  # every set at the same peak, so loudness doesn't decide
            (d / f"{cue}.wav").write_bytes(wav)
            clips[cue] = "data:audio/wav;base64," + base64.b64encode(wav).decode()
        data.append({"key": key, "name": name, "note": note, "clips": clips})
    html = tpl.read_text(encoding="utf-8")
    assert html.count("/*DATA*/") == 1
    page.write_text(html.replace("/*DATA*/", json.dumps({"cues": CUE_NAMES, "sets": data}, ensure_ascii=False)), encoding="utf-8")
    print("wrote", page, page.stat().st_size, "bytes")


if __name__ == "__main__":
    main()
