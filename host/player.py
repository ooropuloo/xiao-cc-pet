# -*- coding: utf-8 -*-
"""PC side of the round-display player.

Reads Windows "now playing" (SMTC), prefers the browser session (YouTube),
renders a 240x240 round UI as JPEG and pushes it over USB serial to the
XIAO ESP32C3. Touch events come back and are turned into play/pause/skip.
"""
import argparse
import asyncio
import colorsys
import io
import logging
import math
import socket
import struct
import unicodedata
import threading
import time
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

import serial

import askbridge
import buddy as buddy_mod
import clawd
import holo
import sfx
import temppage
from PIL import Image, ImageDraw, ImageFilter, ImageFont
from winrt.windows.media.control import (
    GlobalSystemMediaTransportControlsSessionManager as MediaManager,
    GlobalSystemMediaTransportControlsSessionPlaybackStatus as Status,
)
from winrt.windows.storage.streams import Buffer, InputStreamOptions

SIZE = 240
BROWSERS = ("chrome", "msedge", "firefox", "brave", "opera")
# tabs whose video previews also show up as media sessions: only used when nothing else is there
BACKSTAGE = ("meta business suite", "youtube studio", "facebook")
FONT = "C:/Windows/Fonts/msjhbd.ttc"
FONT_LIGHT = "C:/Windows/Fonts/msjh.ttc"
FONT_HANGUL = "C:/Windows/Fonts/malgunbd.ttf"
FONT_HANGUL_LIGHT = "C:/Windows/Fonts/malgun.ttf"
ACCENT = (120, 220, 90)

log = logging.getLogger("player")
peer_log = logging.getLogger("peer")  # every signal report: raw data for the motion test, too much for player.log
peer_log.propagate = False


def setup_log(path, max_bytes, backups):
    h = RotatingFileHandler(path, maxBytes=max_bytes, backupCount=backups, encoding="utf-8")
    h.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
    log.addHandler(h)
    log.setLevel(logging.INFO)
    h = RotatingFileHandler(Path(path).with_name("peer.log"), maxBytes=max_bytes, backupCount=backups, encoding="utf-8")
    h.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
    peer_log.addHandler(h)
    peer_log.setLevel(logging.INFO)

# Button hit areas on the 240x240 screen: name -> (cx, cy, radius)
BUTTONS = {"prev": (70, 186, 22), "play": (120, 186, 24), "next": (170, 186, 22)}


def parse_peer(line, now):
    """`PEER xiao -46 ch=7 n=12 age=240` -> (-46, 240, now); `PEER xiao none ...` -> (None, 0, now); else None."""
    parts = line.split()
    try:
        rssi = None if parts[2] == "none" else int(parts[2])
        return rssi, int(line.rsplit("age=", 1)[1]), now
    except (IndexError, ValueError):
        return None


class Device:
    """Shared line protocol; subclasses move the bytes (USB serial or Wi-Fi TCP)."""

    active = True  # False while nobody can see it (desktop window hidden): skip rendering

    def __init__(self):
        self.touches = []
        self.lock = threading.Lock()
        self.hello = threading.Event()
        self.page = 0  # page the board is showing, from its "P n" lines
        self.night = False  # board's night window, from its "N <in window> <lit>" lines
        self.lit = True     # False while the board has its backlight off: send nothing
        # ((board, chip), monotonic time) from "HOLO st"; replaced as one tuple, read by HoloDisplay's thread
        self.temps = ((None, None), None)
        self.chip_history = ()  # the board's recent chip readings from "TEMP", oldest first; replaced whole
        self.peer = None  # (dBm or None, ms since it last heard the other board, monotonic time) from "PEER"; replaced whole

    def _feed(self, buf):
        while b"\n" in buf:
            line, buf = buf.split(b"\n", 1)
            text = line.decode(errors="replace").strip()
            if text.startswith(("DOOR", "WIFI", "HOLO", "TEMP")):
                log.info(text)
            if text.startswith(("HOLO st", "TEMP ")):  # "TEMP" = the round display, chip only
                self.temps = (holo.parse_temps(text), time.monotonic())
                chip = self.temps[0][1]
                if text.startswith("TEMP ") and chip is not None:
                    self.chip_history = (*self.chip_history, chip)[-temppage.HISTORY:]
            if text.startswith("PEER "):
                peer_log.info(text)
                self.peer = parse_peer(text, time.monotonic())
            if text == "HELLO":
                self.hello.set()
            elif text.startswith("P "):
                self.page = int(text[2:])
                log.info(f"board page {self.page}")
            elif text.startswith("N "):
                night, lit = (p == "1" for p in text.split()[1:3])
                if (night, lit) != (self.night, self.lit):
                    log.info(f"board night={int(night)} lit={int(lit)}")
                if lit and not self.lit:
                    self.hello.set()  # woken up: push a fresh frame instead of the stale one
                self.night, self.lit = night, lit
            elif text.startswith("T "):  # HoloCubic gesture
                with self.lock:
                    self.touches.append(("T", text[2:], time.monotonic()))  # when: holo_game drops stale ones
            elif text == "S":
                with self.lock:
                    self.touches.append(("S", 0, 0))
            elif text[:2] in ("D ", "U ", "M ", "H "):
                kind, x, y = text.split()
                with self.lock:
                    self.touches.append((kind, int(x), int(y)))
        return buf

    def pop_touches(self):
        with self.lock:
            out, self.touches = self.touches, []
        return out

    @staticmethod
    def frame(data, tag=b"G"):
        # "JP" + page tag: G = player page, C = Claude Code page
        return b"JP" + tag + struct.pack("<I", len(data)) + data


class SerialDevice(Device):
    def __init__(self, port):
        super().__init__()
        self.ser = serial.Serial()
        self.ser.port = port
        self.ser.baudrate = 115200
        self.ser.dtr = False
        self.ser.rts = False
        self.ser.timeout = 0.1
        self.ser.open()
        self.name = port
        threading.Thread(target=self._reader, daemon=True).start()

    def _reader(self):
        buf = b""
        while True:
            buf = self._feed(buf + self.ser.read(256))

    def send_jpeg(self, data, tag=b"G"):
        self.ser.write(self.frame(data, tag))


def resolve(host):
    try:
        return socket.gethostbyname(host)
    except OSError:
        if not host.endswith(".local"):
            raise
    from zeroconf import Zeroconf  # Windows can't always resolve mDNS names itself

    zc = Zeroconf()
    try:
        info = zc.get_service_info("_xiaoplayer._tcp.local.", host.split(".")[0] + "._xiaoplayer._tcp.local.", timeout=3000)
        if info and info.parsed_addresses():
            return info.parsed_addresses()[0]
        addrs = zc.cache.entries_with_name(host + ".")
        for rec in addrs:
            if getattr(rec, "address", None):
                return socket.inet_ntoa(rec.address)
    finally:
        zc.close()
    raise OSError(f"cannot resolve {host}")


class TcpDevice(Device):
    """Wi-Fi link to the board's TCP server; reconnects forever."""

    def __init__(self, host, port=5800):
        super().__init__()
        self.host, self.port = host, port
        self.name = f"{host}:{port}"
        self.sock = None
        self.down_logged = False
        threading.Thread(target=self._run, daemon=True).start()
        threading.Thread(target=self._heartbeat, daemon=True).start()

    def _heartbeat(self):
        # A rebooted board never tells us the old connection is gone; writing to it
        # does (RST), which makes recv() fail and _run() reconnect. The firmware's
        # parser skips bytes that aren't part of a "JPG" frame.
        while True:
            time.sleep(2)
            sock = self.sock
            if sock is None:
                continue
            try:
                sock.sendall(b"\n")
            except OSError:
                self._drop(sock)

    def _drop(self, sock):
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            sock.close()
        except OSError:
            pass

    def _run(self):
        while True:
            try:
                addr = resolve(self.host)
                sock = socket.create_connection((addr, self.port), timeout=5)
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                sock.settimeout(None)
                self.sock = sock
                self.down_logged = False
                log.info(f"{self.host} link up {addr}:{self.port}")
                buf = b""
                while True:
                    chunk = sock.recv(1024)
                    if not chunk:
                        raise ConnectionError("closed by device")
                    buf = self._feed(buf + chunk)
            except Exception as e:
                if not self.down_logged:  # an unplugged display retries forever: say so once
                    log.info(f"{self.host} link down: {e}")
                    self.down_logged = True
                self.sock = None
                time.sleep(3)

    def send_jpeg(self, data, tag=b"G"):
        sock = self.sock
        if sock is None:
            return
        try:
            sock.sendall(self.frame(data, tag))
        except OSError:
            self._drop(sock)


async def read_thumbnail(props):
    if props.thumbnail is None:
        return None
    stream = await props.thumbnail.open_read_async()
    buf = Buffer(stream.size)
    await stream.read_async(buf, buf.capacity, InputStreamOptions.READ_AHEAD)
    try:
        return Image.open(io.BytesIO(bytes(buf))).convert("RGB")
    except Exception:
        return None


async def pick_session(mgr, last):
    """Playing music first; when paused stay on the session picked last time (`last` holds its
    app id and title) instead of jumping to whichever tab comes first."""
    sessions = list(mgr.get_sessions())
    browsers = [s for s in sessions if any(b in s.source_app_user_model_id.lower() for b in BROWSERS)]
    rows = []
    for s in browsers or sessions:
        try:
            title = (await s.try_get_media_properties_async()).title or ""
        except Exception:
            continue
        key = (s.source_app_user_model_id, title)
        rows.append((s, key, any(b in title.lower() for b in BACKSTAGE),
                     s.get_playback_info().playback_status == Status.PLAYING))
    for want in (lambda k, back, play: play and not back,
                 lambda k, back, play: k == last.get("key") and not back,
                 lambda k, back, play: not back,
                 lambda k, back, play: play,
                 lambda k, back, play: True):
        for s, k, back, play in rows:
            if want(k, back, play):
                last["key"] = k
                return s
    return None


def text_font(text, font_path, size):
    """Microsoft JhengHei has no Hangul, so a Korean title came out as boxes. Malgun Gothic also covers Latin and common Hanja."""
    if any("\uac00" <= c <= "\ud7a3" or "\u1100" <= c <= "\u11ff" or "\u3130" <= c <= "\u318f" for c in text):
        font_path = FONT_HANGUL if font_path == FONT else FONT_HANGUL_LIGHT
    return ImageFont.truetype(font_path, size)


def fit_text(draw, text, font, width):
    if draw.textlength(text, font=font) <= width:
        return text
    while text and draw.textlength(text + "…", font=font) > width:
        text = text[:-1]
    return text + "…"


def wrap_title(draw, text, font, width):
    """One or two lines. English breaks between words, Chinese anywhere; only the second line gets cut."""
    if draw.textlength(text, font=font) <= width:
        return [text]
    cut = len(text)
    while draw.textlength(text[:cut], font=font) > width:
        cut -= 1
    if text[cut - 1].isascii() and text[cut].isascii() and " " in text[:cut]:
        cut = text.rindex(" ", 0, cut + 1)
    elif text[cut] in "，。、！？：；）】」』》":
        cut -= 1  # closing punctuation stays with the line it closes
    return [text[:cut].rstrip(), fit_text(draw, text[cut:].strip(), font, width)]


def center_crop_square(img):
    w, h = img.size
    s = min(w, h)
    return img.crop(((w - s) // 2, (h - s) // 2, (w - s) // 2 + s, (h - s) // 2 + s))


BG_DIM = 0.45         # blurred cover behind the text, at most this bright
BG_MAX_LUM = 0.06     # brightest part of that backdrop (WCAG luminance): keeps the light grey time readable
MIN_CONTRAST = 5.0    # accent on that backdrop; higher washes warm colours out, lower leaves blue looking dim
PAUSED_ART = 0.42     # paused cover brightness: white pause bars stay above 4.5:1 even on an all-white cover
_look = (None, None)  # (cover, its look); a new track brings a new Image


def rel_lum(rgb):
    def linear(v):
        v /= 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    return 0.2126 * linear(rgb[0]) + 0.7152 * linear(rgb[1]) + 0.0722 * linear(rgb[2])


def cover_look(cover):
    """(accent, brighter accent while scrubbing, backdrop brightness) taken from the cover.
    Accent: its most vivid hue, lifted until it reads on the backdrop. Black-and-white covers keep ACCENT."""
    global _look
    if _look[0] is cover:
        return _look[1]
    pixels = list(center_crop_square(cover).resize((24, 24), Image.BILINEAR).getdata())
    top = sorted(rel_lum(p) for p in pixels)[int(len(pixels) * 0.95)]
    dim = min(BG_DIM, (BG_MAX_LUM / top) ** (1 / 2.2)) if top > 0 else BG_DIM
    backdrop = top * dim ** 2.2

    bins = [[0.0, 0.0, 0.0] for _ in range(12)]  # weight, weighted hue, weighted saturation
    for r, g, b in pixels:
        h, sat, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
        if sat >= 0.25 and v >= 0.2:
            weight = sat * sat * v  # vivid beats merely common
            row = bins[int(h * 12) % 12]
            row[0] += weight
            row[1] += weight * h
            row[2] += weight * sat
    weight, hue, sat = max(bins, key=lambda row: row[0])
    accent, bright = ACCENT, (170, 245, 140)
    if weight / len(pixels) >= 0.015:
        hue, sat = hue / weight, min(0.8, max(0.45, sat / weight))
        tone = lambda l: tuple(int(c * 255) for c in colorsys.hls_to_rgb(hue, l, sat))
        light = 0.62
        while light < 0.9 and (rel_lum(tone(light)) + 0.05) / (backdrop + 0.05) < MIN_CONTRAST:
            light += 0.02
        accent, bright = tone(light), tone(min(0.92, light + 0.12))
    look = accent, bright, dim
    _look = (cover, look)
    return look


CONTROLS_SECS = 5.0  # a tap in the middle shows the buttons this long
JUMP_HOLD_SECS = 0.7 # Claude page: holding this long, then letting go, sends the character over to the HoloCubic
SEEK_R = 96          # touches starting this far from the centre drag the progress ring (same as firmware)
                     # once held still there: the device then sends "H" (a swipe from the rim changes page)
SEEK_SETTLE = 0.5    # seek only once the finger has stayed off this long (the touch chip blinks off mid-drag)
TIME_R = 112         # the time label stays inside this radius (the progress ring starts at 114)


def ring_frac(x, y):
    """0 at 12 o'clock, growing clockwise like the progress ring."""
    return (math.degrees(math.atan2(y - 120, x - 120)) + 90) % 360 / 360


def time_text(draw, position, duration, font, y, size):
    """`pos / total`, or just pos when that would run into the progress ring at this height (hour-long videos)."""
    full = f"{mmss(position)} / {mmss(duration)}"
    half_chord = math.sqrt(max(0, TIME_R ** 2 - (y + size / 2 - SIZE / 2) ** 2))
    return full if draw.textlength(full, font=font) <= 2 * half_chord * 2 else mmss(position)  # font is at S=2


def render(state, pressed=None, controls=True, scrub=None):
    """controls=False: buttons tucked away, bigger cover (a tap in the middle brings them back).
    scrub: 0..1 while a finger drags the ring; draws that position with a knob and its time."""
    S = 2  # supersample for smooth edges
    W = SIZE * S
    cover = state["cover"]
    accent, bright = ACCENT, (170, 245, 140)
    if cover is not None:
        accent, bright, dim = cover_look(cover)
        sq = center_crop_square(cover)
        bg = sq.resize((W, W)).filter(ImageFilter.GaussianBlur(18 * S))
        bg = Image.blend(bg, Image.new("RGB", (W, W), (0, 0, 0)), 1 - dim)
    else:
        bg = Image.new("RGB", (W, W), (18, 18, 22))
    d = ImageDraw.Draw(bg)

    # progress ring around the round edge
    ring = 6 * S
    box = (ring // 2, ring // 2, W - ring // 2, W - ring // 2)
    d.arc(box, 0, 360, fill=(60, 60, 60), width=ring)
    if state["duration"] > 0:
        frac = scrub if scrub is not None else max(0.0, min(1.0, state["position"] / state["duration"]))
        if frac > 0:
            d.arc(box, -90, -90 + 360 * frac, fill=bright if scrub is not None else accent, width=ring)
        if scrub is not None or controls:
            # knob at the head of the arc: shows the ring can be dragged
            a = math.radians(-90 + 360 * frac)
            rr = W / 2 - ring / 2
            kx, ky = W / 2 + rr * math.cos(a), W / 2 + rr * math.sin(a)
            kr = (9 if scrub is not None else 6) * S
            d.ellipse((kx - kr, ky - kr, kx + kr, ky + kr), fill=(255, 255, 255), outline=accent, width=2 * S)

    # cover art
    cs = (96 if controls else 116) * S
    cx0, cy0 = (W - cs) // 2, (26 if controls else 30) * S
    # with the buttons tucked away nothing else tells a paused track from a playing one
    paused = bool(state["title"]) and not state["playing"] and not controls
    if cover is not None:
        art = center_crop_square(cover).resize((cs, cs), Image.LANCZOS)
        if paused:
            art = art.point(lambda v: int(v * PAUSED_ART))
        mask = Image.new("L", (cs, cs), 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, cs, cs), radius=12 * S, fill=255)
        bg.paste(art, (cx0, cy0), mask)
    else:
        d.rounded_rectangle((cx0, cy0, cx0 + cs, cy0 + cs), radius=12 * S, fill=(40, 40, 46))
        if not paused:
            d.text((W // 2, cy0 + cs // 2), "♪", font=ImageFont.truetype("C:/Windows/Fonts/seguisym.ttf", 48 * S), fill=(120, 120, 120), anchor="mm")
    if paused:
        my = cy0 + cs // 2
        for x0 in (W // 2 - 17 * S, W // 2 + 5 * S):
            d.rounded_rectangle((x0, my - 22 * S, x0 + 12 * S, my + 22 * S), radius=3 * S, fill=(255, 255, 255))

    # title / artist
    ft = text_font(state["title"], FONT, 15 * S)
    fa = text_font(state["artist"], FONT_LIGHT, 11 * S)
    title = state["title"] or "沒有播放中的媒體"
    # the resting view has room for a second line, and YouTube titles need it; the buttons only show for a few seconds
    lines = [fit_text(d, title, ft, 180 * S)] if controls else wrap_title(d, title, ft, 180 * S)
    ty = 137 if controls else (166 if len(lines) == 1 else 158)
    for i, line in enumerate(lines):
        d.text((W // 2, (ty + i * 18) * S), line, font=ft, fill=accent, anchor="mm")
    artist = fit_text(d, state["artist"], fa, (160 if len(lines) == 1 else 150) * S)  # lower down the circle is narrower
    d.text((W // 2, (ty + 19 + (len(lines) - 1) * 17) * S), artist, font=fa, fill=(220, 220, 220), anchor="mm")

    # buttons
    for name, (x, y, r) in (BUTTONS.items() if controls else ()):
        x, y, r = x * S, y * S, r * S
        if pressed == name:
            d.ellipse((x - r, y - r, x + r, y + r), fill=(255, 255, 255, 60), outline=None)
        if name == "play":
            d.ellipse((x - r, y - r, x + r, y + r), fill=(255, 255, 255))
            if state["playing"]:
                bw, bh = 5 * S, 16 * S
                d.rectangle((x - 9 * S, y - bh // 2 * 1, x - 9 * S + bw, y + bh // 2), fill=(20, 20, 20))
                d.rectangle((x + 4 * S, y - bh // 2, x + 4 * S + bw, y + bh // 2), fill=(20, 20, 20))
            else:
                t = 10 * S
                d.polygon([(x - t + 3 * S, y - t), (x - t + 3 * S, y + t), (x + t + 2 * S, y)], fill=(20, 20, 20))
        else:
            t = 8 * S
            sgn = -1 if name == "prev" else 1
            tip = x + sgn * t
            d.polygon([(x - sgn * t, y - t), (x - sgn * t, y + t), (tip, y)], fill=(255, 255, 255))
            d.rectangle((tip - (0 if sgn > 0 else 3 * S), y - t, tip + (3 * S if sgn > 0 else 0), y + t), fill=(255, 255, 255))

    # time
    if state["duration"] > 0:
        time_y = 218 if controls else 212  # 218: clear of the play button above and the knob at the bottom of the ring
        if scrub is not None:  # where the finger is, big and bright
            fsk = ImageFont.truetype(FONT, 13 * S)
            txt = time_text(d, scrub * state["duration"], state["duration"], fsk, time_y, 13)
            d.text((W // 2, time_y * S), txt, font=fsk, fill=bright, anchor="mm")
        else:
            fm = ImageFont.truetype(FONT_LIGHT, 10 * S)
            txt = time_text(d, state["position"], state["duration"], fm, time_y, 10)
            d.text((W // 2, time_y * S), txt, font=fm, fill=(235, 235, 235), anchor="mm")

    img = bg.resize((SIZE, SIZE), Image.LANCZOS)
    out = io.BytesIO()
    img.save(out, "JPEG", quality=85)
    return out.getvalue()


def clean_text(s):
    """Titles often use styled letters (𝐏𝐥𝐚𝐲𝐥𝐢𝐬𝐭 = MATHEMATICAL BOLD) and emoji the board's
    font doesn't have: they came out as boxes. NFKC turns the letters back into plain ones,
    symbols (So: ☕ ✨ ...) and emoji joiners / variation selectors are dropped, and so are
    the emoji pieces that are not So: skin tones (👍🏻), the keycap ring (3️⃣ keeps the 3)
    and the tag letters of subdivision flags (England)."""
    s = unicodedata.normalize("NFKC", s or "")
    s = "".join(c for c in s if unicodedata.category(c) not in ("So", "Cs", "Co")
                and c not in "\u200d\ufe0e\ufe0f\u20e3"
                and not 0x1F3FB <= ord(c) <= 0x1F3FF
                and not 0xE0000 <= ord(c) <= 0xE007F)
    return " ".join(s.split())


def mmss(sec):
    """m:ss, or h:mm:ss from an hour up (long videos showed as 83:45)."""
    sec = max(0, int(sec))
    h, m, s = sec // 3600, sec // 60 % 60, sec % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def hit(x, y):
    for name, (cx, cy, r) in BUTTONS.items():
        if (x - cx) ** 2 + (y - cy) ** 2 <= (r + 10) ** 2:
            return name
    if 26 <= y <= 122 and 72 <= x <= 168:
        return "play"  # tapping the cover toggles too
    return None


class Screen:
    """What one display shows and how its touches act. The board and the desktop window
    each get one, so they can sit on different pages; media, ccpet and the question are shared."""

    def __init__(self, dev, local_pointer):
        self.dev = dev
        self.local_pointer = local_pointer  # draws the dial pointer itself, so frames may lag while dragging
        self.seen_page = 0
        self.last_sent_key = None
        self.last_send = 0.0
        self.pressed = None
        self.q_cursor = 0        # dial cursor (index into clawd.dial_items)
        self.q_key = None        # (question text, idx) the cursor belongs to
        self.q_drag = False      # current touch started on the ring
        self.q_center = False    # current touch started on the centre (drawn lit)
        self.q_shown = 0.0       # when the current question appeared; earlier / too-early touches don't count
        self.q_last_view = None  # what the last question frame showed, to skip identical redraws
        self.sent_until, self.sent_label = 0.0, ""
        self.claude_sel = 0
        self.claude_down = None    # when the finger landed on the Claude page
        self.last_claude = 0.0
        self.last_chat = 0.0
        self.controls_until = 0.0  # player page: buttons shown until then
        self.scrub = None          # player page: 0..1 while a finger drags the progress ring
        self.scrub_up = 0.0        # when that finger lifted; the seek waits SEEK_SETTLE for it to come back

    async def _commit_seek(self, ctx):
        state, session = ctx["state"], ctx["session"]
        target = min(self.scrub, 0.999) * state["duration"]
        self.scrub, self.scrub_up = None, 0.0
        if session is not None and state["duration"] > 0:
            log.info(f"-> seek {mmss(target)} ({self.dev.name})")
            await session.try_change_playback_position_async(int(target * 10_000_000))
            state["position"] = target
            state["seek"] = (target, time.monotonic())
        ctx["media_changed"]()

    async def handle_touches(self, ctx):
        dev, page, asking, bridge, session, state = self.dev, self.dev.page, ctx["asking"], ctx["bridge"], ctx["session"], ctx["state"]
        if page != self.seen_page:
            self.seen_page = page
            self.last_sent_key = None
            self.last_claude = 0.0
        if self.scrub is not None and self.scrub_up and time.monotonic() - self.scrub_up >= SEEK_SETTLE:
            await self._commit_seek(ctx)
        for kind, x, y in dev.pop_touches():
            if ctx["debug_touch"]:
                log.info(f"touch {dev.name} {kind} {x} {y} (page {page})")
            if page == clawd.PAGE_INDEX and asking is not None:
                q = asking["q"]
                items = clawd.dial_items(q)
                zone = clawd.touch_zone(x, y)
                bridge.touched()
                if kind == "D":
                    # a finger that lands within 0.6 s of the question (or was already down when it
                    # appeared) must not answer it: the default option would be sent by accident
                    fresh = time.monotonic() - self.q_shown >= 0.6
                    self.q_drag, self.q_center = zone == "ring" and fresh, zone == "center" and fresh
                    if self.q_drag:
                        self.q_cursor = clawd.dial_index(q, x, y)
                elif kind == "M" and self.q_drag:
                    self.q_cursor = clawd.dial_index(q, x, y)
                elif kind == "S":
                    self.q_drag = self.q_center = False
                elif kind == "U":
                    tapped = self.q_center and zone == "center"
                    self.q_drag = self.q_center = False
                    if tapped:
                        item = items[self.q_cursor % len(items)]
                        opts = q.get("options", [])
                        label = ""
                        if item == clawd.DONE:
                            if asking["selected"]:
                                label = ", ".join(opts[i].get("label", "") for i in sorted(asking["selected"]))
                            bridge.choose(-1)
                        elif q.get("multiSelect"):
                            bridge.choose(item)  # toggle
                        else:
                            label = opts[item].get("label", "")
                            bridge.choose(item)
                        if label:
                            ctx["sent"](label)
                self.last_claude = 0.0  # redraw now so the finger sees the result immediately
                continue
            if page == clawd.PAGE_INDEX:
                if kind == "D":
                    self.claude_down = time.monotonic()
                elif kind == "S":
                    self.claude_down = None
                elif kind == "U":
                    if self.claude_down is not None and time.monotonic() - self.claude_down >= JUMP_HOLD_SECS:
                        ctx["buddy"].jump(buddy_mod.XIAO, time.monotonic())
                    else:  # tap: next project
                        self.claude_sel += 1
                        self.last_claude = 0.0
                    self.claude_down = None
                continue
            if page == buddy_mod.PAGE_INDEX:
                if kind == "U":  # tap: jump over there
                    ctx["buddy"].jump(buddy_mod.XIAO, time.monotonic())
                continue
            if page == temppage.PAGE_INDEX:
                continue  # nothing to tap there; must not fall through to the player's buttons
            on_rim = (x - 120) ** 2 + (y - 120) ** 2 > SEEK_R ** 2
            if kind == "H" and state["duration"] > 0:  # held on the ring: the knob shows up, the finger drags it
                if self.scrub is None:
                    self.scrub = ring_frac(x, y)
                self.scrub_up, self.pressed = 0.0, None  # back within SEEK_SETTLE: the same drag goes on
                kind = "M"
            elif kind == "H":
                continue
            elif kind == "D" and self.scrub is not None and not on_rim:
                await self._commit_seek(ctx)  # a new touch elsewhere: the drag is done
            if self.scrub is not None:
                if kind == "D":
                    continue  # back on the ring: an "H" goes on with the drag, else SEEK_SETTLE commits it
                if kind == "M":
                    f = ring_frac(x, y)
                    # no wrapping across 12 o'clock: past the end sticks at the end, before 0 at 0
                    if self.scrub > 0.75 and f < 0.25:
                        f = 1.0
                    elif self.scrub < 0.25 and f > 0.75:
                        f = 0.0
                    self.scrub = f
                elif kind == "U":
                    self.scrub_up = time.monotonic()  # committed at the top once the finger stays off
                else:
                    self.scrub, self.scrub_up = None, 0.0  # swipe: drop it
                continue
            shown = time.monotonic() < self.controls_until
            btn = hit(x, y) if shown else None
            if kind == "S":  # swipe to another page: cancel the press
                self.pressed = None
                self.last_sent_key = None
            elif kind == "D":
                self.pressed = btn
            elif kind == "U":
                self.pressed = None
                if not shown:
                    self.controls_until = time.monotonic() + CONTROLS_SECS  # any tap brings the buttons up
                    continue
                if not btn:
                    self.controls_until = 0.0  # tap beside the buttons tucks them away
                    continue
                self.controls_until = time.monotonic() + CONTROLS_SECS
                if session is not None:
                    log.info(f"-> {btn} ({dev.name})")
                    if btn == "play":
                        await session.try_toggle_play_pause_async()
                        state["playing"] = not state["playing"]
                    elif btn == "next":
                        await session.try_skip_next_async()
                    elif btn == "prev":
                        await session.try_skip_previous_async()
                    ctx["media_changed"]()

    def draw(self, ctx, now):
        dev, page, asking, state = self.dev, self.dev.page, ctx["asking"], ctx["state"]
        if dev.hello.is_set():
            dev.hello.clear()
            self.last_sent_key = None  # device (re)booted or window shown: push a frame now
            self.last_claude = 0.0
            self.q_last_view = None
        if not dev.active or not dev.lit:
            return
        if now < self.sent_until and page == clawd.PAGE_INDEX:
            if now - self.last_claude >= 0.2:
                dev.send_jpeg(clawd.render_sent(self.sent_label), b"C")
                self.last_claude = now
        elif asking is not None:
            key = (asking["q"].get("question"), asking["idx"])
            if key != self.q_key:
                self.q_key, self.q_cursor = key, 0
                self.q_drag = self.q_center = False
                self.q_shown = now
            # Redraw only on change (cursor, press, selection, countdown second); every frame costs the
            # board ~0.1-0.2 s of decoding. Still send one at least every second: the board treats
            # ring touches as dialing only while 'Q' frames keep arriving.
            q_view = (self.q_cursor % len(clawd.dial_items(asking["q"])), self.q_center, tuple(sorted(asking["selected"])),
                      int(asking["remaining"]), asking["idx"])
            if asking["new"]:
                # tell the board the sector count so it can draw the drag pointer itself
                dev.send_jpeg(bytes([len(clawd.dial_items(asking["q"]))]), b"K")
            # while dragging the board shows the pointer, so full frames can lag a bit
            min_gap = 0.35 if self.q_drag and self.local_pointer else 0.0
            changed = q_view != self.q_last_view and now - self.last_claude >= min_gap
            if asking["new"] or changed or now - self.last_claude >= 1.0:
                dev.send_jpeg(clawd.render_question(asking, now - ctx["t0"], self.q_cursor, self.q_center), b"Q")
                self.q_last_view = q_view
                self.last_claude = now
        elif page == clawd.PAGE_INDEX:
            if now - self.last_claude >= 0.2:  # ~5 fps animation while this page is on screen
                try:
                    jpg, _ = clawd.render(ctx["feed"].snapshot(), ctx["feed"].online, self.claude_sel, now - ctx["t0"],
                                          holo.fresh_temps(dev, now)[1])
                    dev.send_jpeg(jpg, b"C")
                except Exception as e:  # a bad ccpet field must not take the player down
                    log.info(f"claude page render error: {e!r}")
                self.last_claude = now
        elif page == buddy_mod.PAGE_INDEX:
            if now - self.last_chat >= 0.2:
                dev.send_jpeg(ctx["buddy"].render(buddy_mod.XIAO, now, now - ctx["t0"]), b"M")
                self.last_chat = now
        elif page == temppage.PAGE_INDEX:
            chip = holo.fresh_temps(dev, now)[1]
            holo_temps = holo.fresh_temps(ctx["holo"], now) if ctx["holo"] else None
            send_key = (None if chip is None else round(chip), dev.chip_history, holo_temps)
            if send_key != self.last_sent_key:  # a sample every 5 s; each frame costs the board a decode
                dev.send_jpeg(temppage.render(chip, dev.chip_history, holo_temps), b"T")
                self.last_sent_key = send_key
        elif page == 0:
            controls = time.monotonic() < self.controls_until
            scrub = None if self.scrub is None else round(self.scrub, 3)
            send_key = (state["title"], state["artist"], state["playing"], int(state["position"]),
                        ctx["cover_key"], ctx["cover_ver"], self.pressed, controls, scrub)
            if send_key != self.last_sent_key and now - self.last_send > 0.15:
                dev.send_jpeg(render(state, self.pressed, controls, scrub))
                self.last_sent_key = send_key
                self.last_send = now

    def sent(self, label):
        self.sent_label = label
        self.sent_until = time.monotonic() + 1.0


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", help="USB serial port (e.g. COM13); omit to use Wi-Fi")
    ap.add_argument("--host", default="xiao-player.local", help="board address for the Wi-Fi link")
    ap.add_argument("--debug-touch", action="store_true")
    ap.add_argument("--no-sfx", action="store_true", help="mute the Claude Code state sounds")
    ap.add_argument("--no-holo", action="store_true", help="don't drive the HoloCubic")
    ap.add_argument("--no-desktop", action="store_true", help="no tray icon / desktop copy of the screen")
    ap.add_argument("--log", default=str(Path(__file__).with_name("player.log")))
    ap.add_argument("--log-max-bytes", type=int, default=1_000_000)
    ap.add_argument("--log-backups", type=int, default=2)
    args = ap.parse_args()
    setup_log(args.log, args.log_max_bytes, args.log_backups)

    dev = SerialDevice(args.port) if args.port else TcpDevice(args.host)
    screens = [Screen(dev, local_pointer=True)]
    if not args.no_desktop:
        try:
            import sys
            sys.modules.setdefault("player", sys.modules[__name__])  # desktop.py reuses Device without re-importing us
            import desktop
            screens.append(Screen(desktop.DesktopDevice(log), local_pointer=False))
        except Exception as e:  # no tray must never cost the board its player
            log.info(f"desktop screen unavailable: {e!r}")
    mgr = await MediaManager.request_async()
    log.info(f"using {dev.name}, waiting for media…")

    state = {"title": "", "artist": "", "cover": None, "playing": False, "position": 0.0, "duration": 0.0}
    cover_key = None
    cover_ver = 0
    picked = {}
    cover_checked = 0.0
    track_since = 0.0
    feed = clawd.CcpetFeed(log)
    sounds = sfx.StateSounds(log, enabled=not args.no_sfx)
    bridge = askbridge.AskBridge(log)
    asking = None  # current AskUserQuestion view while one is on screen
    holo_link = None if args.no_holo else TcpDevice(holo.HOST)
    buddy = buddy_mod.Buddy(log)
    pulled_serial, back_to_page = 0, None  # the interaction that last brought the board to the chat page, and where it was
    if holo_link:
        holo.HoloDisplay(holo_link, feed, lambda: not dev.lit, log, buddy)
    t0 = time.monotonic()

    def media_changed():
        for sc in screens:
            sc.last_sent_key = None

    def sent(label):
        for sc in screens:
            sc.sent(label)

    while True:
        session = await pick_session(mgr, picked)
        if session is not None:
            try:
                props = await session.try_get_media_properties_async()
                info = session.get_playback_info()
                tl = session.get_timeline_properties()
                key = (props.title, props.artist)
                now_t = time.monotonic()
                if key != cover_key:
                    cover_key = key
                    track_since = now_t
                    cover_checked = 0.0
                    state["cover"] = None
                    log.info(f"track {session.source_app_user_model_id}: {clean_text(props.title)[:60]!r} "
                             f"status={info.playback_status} thumb={'yes' if props.thumbnail else 'no'}")
                # browsers publish artwork a moment after the title, so keep retrying
                # while it's missing and re-read once more shortly after a track change
                if now_t - cover_checked > 2.0 and (state["cover"] is None or now_t - track_since < 4.0):
                    cover_checked = now_t
                    img = await read_thumbnail(props)
                    if img is not None:
                        if state["cover"] is None:
                            log.info(f"cover {img.size[0]}x{img.size[1]} after {now_t - track_since:.1f}s")
                        state["cover"] = img
                        cover_ver += 1
                state["title"] = clean_text(props.title)
                state["artist"] = clean_text(props.artist)
                state["playing"] = info.playback_status == Status.PLAYING
                dur = tl.end_time.total_seconds() if tl.end_time else 0.0
                pos = tl.position.total_seconds() if tl.position else 0.0
                if state["playing"] and tl.last_updated_time:
                    pos += (datetime.now(timezone.utc) - tl.last_updated_time).total_seconds()
                state["duration"] = dur
                state["position"] = min(pos, dur) if dur else 0.0
                # Chrome reports the new position a moment after a seek: until then trust the seek
                sk = state.get("seek")
                if sk and now_t - sk[1] < 4.0:
                    expect = sk[0] + (now_t - sk[1] if state["playing"] else 0.0)
                    if abs(state["position"] - expect) > 3:
                        state["position"] = min(expect, dur) if dur else 0.0
            except Exception as e:
                log.info(f"media read error: {e}")
        else:
            state.update(title="", artist="", cover=None, playing=False, position=0.0, duration=0.0)
            cover_key = None

        # touches act on the question as it was last drawn, then the fresh view is drawn
        ctx = {"asking": asking, "bridge": bridge, "session": session, "state": state,
               "debug_touch": args.debug_touch, "media_changed": media_changed, "sent": sent, "buddy": buddy}
        for sc in screens:
            await sc.handle_touches(ctx)

        # the board owns the night schedule: muted cues, questions to TG instead of the screen
        sounds.quiet = bridge.night = dev.night
        if feed.online:
            sounds.update(feed.snapshot())

        now = time.monotonic()
        asking = bridge.view()  # "new" is true for exactly one call
        if asking is not None and asking["new"]:
            sounds.play_now("waiting")
            log.info(f"question frame -> board (link {'up' if getattr(dev, 'sock', True) else 'DOWN'}, board page {dev.page})")
        holo_temps = holo.fresh_temps(holo_link, now) if holo_link else (None, None)
        buddy.update(now, {buddy_mod.XIAO: dev.peer, buddy_mod.HOLO: holo_link.peer if holo_link else None},
                     {buddy_mod.XIAO: holo.fresh_temps(dev, now)[1], buddy_mod.HOLO: holo_temps[1], "board": holo_temps[0]},
                     paused=asking is not None or not dev.lit)
        wanted = buddy.wants_pull(now)
        if wanted and wanted != pulled_serial:
            pulled_serial = wanted
            if dev.page != buddy_mod.PAGE_INDEX:  # the board refuses on its own if a finger is down or it's on a local page
                back_to_page = dev.page
                dev.send_jpeg(bytes([buddy_mod.PAGE_INDEX]), b"Y")
        elif not buddy.active(now) and back_to_page is not None:
            if dev.page == buddy_mod.PAGE_INDEX:  # swiped elsewhere meanwhile: leave them there
                dev.send_jpeg(bytes([back_to_page]), b"Y")
            back_to_page = None
        ctx.update(asking=asking, feed=feed, t0=t0, cover_key=cover_key, cover_ver=cover_ver, holo=holo_link)
        for sc in screens:
            sc.draw(ctx, now)

        await asyncio.sleep(0.05)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
