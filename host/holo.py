"""HoloCubic: the Claude character alone on black, so it floats inside the prism.

Runs on its own thread with its own socket: a dead HoloCubic blocks sendall() for many
seconds and must not freeze the round display or the question bridge in player.py.
"""
import io
import re
import threading
import time

from PIL import Image

import buddy
import clawd
import holo_game

HOST = "holocubic.local"
FRAME_SECS = 0.25
PIN_SECS = 60        # a project picked by tilting stays this long, then the most urgent one is back
NAME_SECS = 2.5      # the project name shows this long after the shown project changes
TEMP_STALE_SECS = 20 # the board reports every 5 s; older than this and the screen shows `--`
TILT_STEP = {"R": 1, "L": -1}  # tilt right = next session, left = previous
# Night: backlight off and black, the board runs hot enough already. Frames must keep coming anyway:
# the firmware takes 5 s without one as "the PC is gone" and lights up with Waiting PC.
NIGHT_FRAME_SECS = 2


def black_frame():
    out = io.BytesIO()
    Image.new("RGB", (240, 240), (0, 0, 0)).save(out, "JPEG", quality=50)
    return out.getvalue()


BLACK_FRAME = black_frame()


def parse_temps(line):
    """(board, chip) in °C from a `HOLO st` line; None for a value that is missing or `nan`."""
    found = (re.search(rf"\b{key}=(-?\d+(?:\.\d+)?)C\b", line) for key in ("board", "chip"))
    return tuple(float(m.group(1)) if m else None for m in found)


def fresh_temps(link, now):
    """The link's latest (board, chip), or (None, None) if nothing recent came in."""
    temps, temps_at = link.temps
    return (None, None) if temps_at is None or now - temps_at > TEMP_STALE_SECS else temps


class HoloDisplay:
    night_frame_at = 0.0

    def __init__(self, link, feed, is_dark, log, buddy):
        self.link, self.feed, self.is_dark, self.log, self.buddy = link, feed, is_dark, log, buddy
        self.pose_since = [None, 0.0]
        self.pin_id, self.pin_until = None, 0.0
        self.shown_id, self.name_until = None, 0.0
        self.sent_level = None
        self.arcade = holo_game.make_arcade(log)
        threading.Thread(target=self._run, daemon=True).start()

    def _pick(self, ordered, now):
        if now >= self.pin_until:
            self.pin_id = None
        for a in ordered:
            if a.get("id") == self.pin_id:
                return a
        return ordered[0] if ordered else None

    def _step_project(self, ordered, now, step):
        if not ordered:
            return
        ids = [a.get("id") for a in ordered]
        i = ids.index(self._pick(ordered, now).get("id"))
        self.pin_id, self.pin_until = ids[(i + step) % len(ids)], now + PIN_SECS
        self.log.info(f"holo tilt {step:+d} -> {self.pin_id}")

    def _tick(self, t0):
        now = time.monotonic()
        ordered = clawd.order_agents(self.feed.snapshot())
        for kind, gesture, at in self.link.pop_touches():
            if kind != "T" or self.arcade.on_event(gesture, at, now):
                continue
            if gesture in TILT_STEP:
                self._step_project(ordered, now, TILT_STEP[gesture])
            elif gesture == "F":  # tilt forward: look at the chat with the round display
                self.buddy.peek(now)
            elif gesture == "B":  # tilt back: jump over there
                self.buddy.jump(buddy.HOLO, now)
        agent = self._pick(ordered, now)
        agent_id = agent.get("id") if agent else None
        if agent_id != self.shown_id:
            self.shown_id, self.name_until = agent_id, now + NAME_SECS

        if self.link.hello.is_set():
            self.link.hello.clear()
            self.sent_level = None
        dark = self.is_dark()
        level = 0 if dark else 255
        if level != self.sent_level and self.link.sock is not None:
            self.link.send_jpeg(bytes([level]), b"B")
            self.sent_level = level
        if dark:
            self.arcade.stop()  # nobody can see it
            if now - self.night_frame_at >= NIGHT_FRAME_SECS:
                self.night_frame_at = now
                self.link.send_jpeg(BLACK_FRAME, b"C")
        else:
            state = clawd.agent_view(agent, self.feed.online)[0] if self.feed.online else "idle"
            scene = self.arcade.scene(state, now)
            if state in holo_game.REAL_STATUS:
                self.buddy.cancel()  # the real status wins, like over the games
            if self.buddy.holo_shows_chat(now) and state not in holo_game.REAL_STATUS:
                jpg = self.buddy.render(buddy.HOLO, now, now - t0, fresh_temps(self.link, now))
            else:
                jpg = clawd.render_holo(agent, self.feed.online, now - t0, self.pose_since,
                                        now < self.name_until, fresh_temps(self.link, now), scene)
            self.link.send_jpeg(jpg, b"C")

    def _run(self):
        t0 = time.monotonic()
        while True:
            start = time.monotonic()
            try:
                self._tick(t0)
            except Exception as e:  # a bad ccpet field must not end the thread
                self.log.info(f"holo render error: {e!r}")
            time.sleep(max(0.01, FRAME_SECS - (time.monotonic() - start)))
