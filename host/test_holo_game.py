# -*- coding: utf-8 -*-
"""Host side of the tilt gestures. Run with the player's environment (it imports player.py):
  uv run --python 3.11 --with pyserial --with pillow --with pystray --with zeroconf --with winrt-runtime
    --with winrt-Windows.Media.Control --with winrt-Windows.Foundation
    --with winrt-Windows.Foundation.Collections --with winrt-Windows.Storage.Streams python test_holo_game.py
"""
import io
import logging
import time

from PIL import Image

import buddy
import clawd
import holo
import holo_game
import player

log = logging.getLogger("test")


def feed(lines):
    dev = player.Device()
    dev._feed("".join(line + "\n" for line in lines).encode())
    return dev


def test_parsing():
    st = ("HOLO st up=99s wifi=3 rssi=-60 frames=5 age=2 drawMax=99 heap=167008 board=75.5C chip=89.4C mhz=240 "
          "imu=0.01,-0.02,0.99")
    assert holo.parse_temps(st) == (75.5, 89.4)
    dev = feed(["T S", "HOLO rock fired swing=40 y=0.02", "T L", "T R", "T ?", "garbage", st, "PEER xiao -46 ch=7 n=12 age=240"])
    assert dev.peer[:2] == (-46, 240)
    assert feed(["PEER xiao none ch=7 n=0 age=0"]).peer[:2] == (None, 0)
    assert feed(["PEER xiao x"]).peer is None
    assert [t[:2] for t in dev.pop_touches()] == [("T", "S"), ("T", "L"), ("T", "R"), ("T", "?")]
    assert dev.temps[0] == (75.5, 89.4)


def test_old_host_ignored_new_lines():
    """What the previous holo.py did with a touch: only gestures in TILT_STEP act."""
    assert "S" not in holo.TILT_STEP


class FakeLink:
    def __init__(self, touches):
        self.touches, self.sock, self.temps, self.sent = touches, None, ((None, None), None), []
        self.hello = type("E", (), {"is_set": lambda s: False, "clear": lambda s: None})()

    def pop_touches(self):
        out, self.touches = self.touches, []
        return out

    def send_jpeg(self, data, tag):
        self.sent.append(tag)


class FakeFeed:
    online = True

    def __init__(self, state="idle", state_b="idle"):
        self.state, self.state_b = state, state_b

    def snapshot(self):
        now_ms = time.time() * 1000
        return [{"id": "a", "project": "D:/a", "state": self.state, "lastEventAt": now_ms},
                {"id": "b", "project": "D:/b", "state": self.state_b, "lastEventAt": now_ms}]


def make_display(touches, state="idle", dark=False):
    """HoloDisplay without its thread, so ticks run here."""
    disp = holo.HoloDisplay.__new__(holo.HoloDisplay)
    disp.link, disp.feed, disp.is_dark, disp.log = FakeLink(touches), FakeFeed(state), (lambda: dark), log
    disp.pose_since, disp.sent_level = [None, 0.0], None
    disp.pin_id, disp.pin_until, disp.shown_id, disp.name_until = None, 0.0, None, 0.0
    disp.buddy = buddy.Buddy(log)
    disp.arcade = holo_game.make_arcade(log)
    return disp


def dizzy_of(disp):
    return next(g for g in disp.arcade.games if isinstance(g, holo_game.Dizzy))


def test_tilts_still_step_and_rock_does_not():
    now = time.monotonic()
    disp = make_display([("T", "R", now)])
    disp._tick(now)
    assert disp.pin_id == "b" and dizzy_of(disp).until == 0
    disp = make_display([("T", "S", now)])
    disp._tick(now)
    assert disp.pin_id is None and dizzy_of(disp).until > now
    assert disp.link.sent == [b"C"]
    disp = make_display([("T", "?", now)])
    disp._tick(now)
    assert disp.pin_id is None and dizzy_of(disp).until == 0


def test_tilt_while_dizzy_switches_session_and_keeps_dizzy():
    now = time.monotonic()
    disp = make_display([("T", "S", now)])
    disp._tick(now)
    until = dizzy_of(disp).until
    disp.link.touches = [("T", "R", now + 1)]
    disp._tick(now + 1)
    assert disp.pin_id == "b" and disp.shown_id == "b"  # switched, name shown by the normal path
    assert dizzy_of(disp).until == until and dizzy_of(disp).scene(now + 1) is not None  # still dizzy
    disp.link.touches = [("T", "L", now + 2)]
    disp._tick(now + 2)
    assert disp.pin_id == "a" and dizzy_of(disp).until == until


def test_tilt_to_a_waiting_session_ends_dizzy():
    now = time.monotonic()
    disp = make_display([("T", "S", now)], "running")
    disp.feed.state_b = "idle"
    disp._tick(now)
    assert dizzy_of(disp).until > now
    disp.feed.state_b = "waiting"  # became urgent: ordered first, so it is on screen and real status wins
    disp._tick(now + 1)
    assert dizzy_of(disp).until == 0


def test_stale_rock_ignored():
    now = time.monotonic()
    disp = make_display([("T", "S", now - 5)])
    disp._tick(now)
    assert dizzy_of(disp).until == 0
    # a late left/right tilt keeps working as before
    disp = make_display([("T", "L", now - 5)])
    disp._tick(now)
    assert disp.pin_id is not None


def test_real_status_wins_and_dark_stops():
    now = time.monotonic()
    for state in ("waiting", "failed"):
        disp = make_display([("T", "S", now)], state)
        disp._tick(now)
        assert dizzy_of(disp).until == 0, state  # ended, not merely hidden: nothing resumes afterwards
    disp = make_display([("T", "S", now)], "running")
    disp._tick(now)
    assert dizzy_of(disp).until > now
    disp = make_display([("T", "S", now)], dark=True)
    disp._tick(now)
    assert dizzy_of(disp).until == 0


def test_night_is_dark_and_keeps_the_board_fed_with_black():
    """Without a frame for 5 s the firmware lights up and shows Waiting PC."""
    now = time.monotonic()
    disp = make_display([], dark=True)
    disp.link.sock = object()
    sent = []
    disp.link.send_jpeg = lambda data, tag: sent.append((now_tick, tag, data))
    real = time.monotonic
    try:
        for step in range(25):  # 5 s at the display's 4 fps
            now_tick = now + step * 0.25
            time.monotonic = lambda: now_tick
            disp._tick(now)
    finally:
        time.monotonic = real
    assert sent[0][1:] == (b"B", bytes([0]))
    frames = sent[1:]
    assert len(frames) >= 4 and all(tag == b"C" for _, tag, _ in frames)
    assert max(b[0] - a[0] for a, b in zip(frames, frames[1:])) < 5
    assert all(data == holo.BLACK_FRAME for _, _, data in frames)
    assert max(Image.open(io.BytesIO(holo.BLACK_FRAME)).convert("L").getdata()) < 10


def test_timing_levels_and_cap():
    game = holo_game.Dizzy()
    assert game.scene(0.0) is None
    game.on_event("S", 0.0)
    assert (game.level, game.until) == (1, 5.0)
    assert game.scene(4.9) is not None and game.scene(5.0) is None
    now = 0.0
    for _ in range(30):  # 30 s of rocking, a rock event each second
        now += 1.0
        game.on_event("S", now)
        assert game.until - now <= holo_game.Dizzy.MAX_SECS
    assert game.level == holo_game.Dizzy.MAX_LEVEL
    assert game.scene(now + game.MAX_SECS) is None  # recovers on its own after the rocking stops
    game.on_event("S", now + game.MAX_SECS)
    assert game.level == 1  # a new round starts small again


def test_render():
    agent = {"id": "a", "project": "D:/a", "state": "idle", "lastEventAt": time.time() * 1000}
    game = holo_game.Dizzy()
    game.on_event("S", 0.0)
    for t in (0.0, 0.25, 0.5, 4.2, 4.7, 4.99):
        draw = game.scene(t)
        jpg = clawd.render_holo(agent, True, t, [None, 0.0], False, (75.5, 90.0), draw)
        img = Image.open(io.BytesIO(jpg))
        assert img.size == (240, 240) and len(jpg) < 40000
        assert img.getpixel((2, 2))[0] < 10  # black stays black: the prism shows it as clear glass


def test_count_label():
    from PIL import ImageDraw, ImageFont
    d = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    font = ImageFont.truetype(clawd.FONT, 15)
    a = {"project": "D:/git/ccfbfans", "sessionCount": 3}
    assert clawd.project_label(d, a, font, 210) == "ccfbfans ×3"
    assert clawd.project_label(d, {"project": "D:/git/ccfbfans", "sessionCount": 1}, font, 210) == "ccfbfans"
    assert clawd.project_label(d, {"project": "D:/git/ccfbfans"}, font, 210) == "ccfbfans"
    assert clawd.project_label(d, {"project": "D:/git/ccfbfans", "sessionCount": "x"}, font, 210) == "ccfbfans"
    long = {"project": "D:/git/" + "a-very-long-project-name" * 4, "sessionCount": 12}
    text = clawd.project_label(d, long, font, 210)
    assert text.endswith("… ×12") and d.textlength(text, font=font) <= 210
    nested = {"project": "D:/git/xiao_esp32/evolve/proposals", "sessionCount": 2}
    assert clawd.project_label(d, nested, font, 120) == "…/proposals ×2"
    assert clawd.project_label(d, nested, font, 400) == "xiao_esp32/evolve/proposals ×2"


def test_forward_shows_chat_back_jumps_centre_does_nothing():
    now = time.monotonic()
    disp = make_display([("T", "C", now)])
    disp._tick(now)
    assert not disp.buddy.holo_shows_chat(now) and disp.pin_id is None and dizzy_of(disp).until == 0
    disp = make_display([("T", "F", now)])
    disp._tick(now)
    assert disp.buddy.holo_shows_chat(now) and not disp.buddy.active(now)
    disp = make_display([("T", "B", now)])
    disp.buddy.rssi = -37
    disp._tick(now)
    assert disp.buddy.active(now) and disp.buddy.current.script.kind == "fight" and disp.buddy.current.first == buddy.HOLO


def test_real_status_cancels_interaction():
    now = time.monotonic()
    disp = make_display([("T", "B", now)], "waiting")
    disp.buddy.rssi = -37
    disp._tick(now)
    assert not disp.buddy.active(now)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
    print("PASSED")
