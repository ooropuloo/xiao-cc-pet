# -*- coding: utf-8 -*-
"""HoloCubic mini games: tilt events turned into short playful scenes on the prism.

The firmware sends "T <gesture>" lines; Arcade offers each one to its games. A game that wants a
gesture returns True from on_event, and while it has something to show scene() returns a
draw(d, t, S) that replaces the normal character. Add a game: a class with gestures/on_event/scene/stop, in Arcade's list; the first game
naming a gesture gets it. The real status always wins: waiting and failed end or suspend any game at once.
"""
import math

import clawd
import themes
import voxel

EVENT_STALE_SECS = 2   # a gesture this old was stuck behind a blocked send: the moment has passed
REAL_STATUS = ("waiting", "failed")


class Arcade:
    def __init__(self, games, log):
        self.games, self.log = games, log

    def on_event(self, gesture, at, now):
        """True if a game took this gesture, or would have but it came in too late."""
        for game in self.games:
            if gesture in game.gestures:
                if now - at > EVENT_STALE_SECS:
                    self.log.info(f"holo game: stale T {gesture} ({now - at:.1f} s old) ignored")
                else:
                    self.log.info(f"holo game: T {gesture} -> {game.on_event(gesture, now)}")
                return True
        return False

    def stop(self):
        for game in self.games:
            game.stop()

    def scene(self, state, now):
        """The draw() of the running game, or None for the normal character."""
        if state in REAL_STATUS:
            self.stop()
            return None
        for game in self.games:
            draw = game.scene(now)
            if draw:
                return draw
        return None


class Dizzy:
    """Rock the cube forward and back: the character gets dizzy, then recovers by itself."""

    gestures = ("S",)
    FIRST_SECS = 5.0
    MORE_SECS = 1.5    # each further rock adds this
    MAX_SECS = 8.0     # never more than this still to go, however long the rocking
    MAX_LEVEL = 3

    def __init__(self):
        self.until, self.level = 0.0, 0

    def on_event(self, gesture, now):
        if now >= self.until:
            self.level, self.until = 1, now + self.FIRST_SECS
        else:
            self.level = min(self.MAX_LEVEL, self.level + 1)
            self.until = min(now + self.MAX_SECS, self.until + self.MORE_SECS)
        return f"dizzy: level={self.level} left={self.until - now:.1f}s"

    def stop(self):
        self.until = 0.0

    def scene(self, now):
        if now >= self.until:
            return None
        level, left = self.level, self.until - now
        return lambda d, t, S: draw_dizzy(d, t, S, level, left)


# At 4 fps a motion needs several frames per cycle to read as motion, and things that repeat must move
# less than half their spacing per frame or they appear to run backwards.
WOBBLE_PERIOD = 1.25   # s: five frames
STAR_TURN = 2.09       # rad/s: 30 degrees per frame, under half of the widest spacing (5 stars: 72 degrees)
SWIRL_TURN = 5.03      # rad/s: 72 degrees per frame, clearly one way round
RECOVER_SECS = 1.0     # the wobble dies away over the last second
EYES_BACK_SECS = 0.4   # normal eyes for the last moment
AMOUNT = {1: 0.65, 2: 0.85, 3: 1.0}
STAR = (255, 220, 120)
EYE_WHITE = (250, 244, 232)
CX, CY, CELL = 120, 94, 9  # same place and size as the normal idle character


def star_points(x, y, k):
    return [(x, y - k), (x + k * 0.3, y - k * 0.3), (x + k, y), (x + k * 0.3, y + k * 0.3),
            (x, y + k), (x - k * 0.3, y + k * 0.3), (x - k, y), (x - k * 0.3, y - k * 0.3)]


def draw_swirl_eye(d, proj, xf, cx, t, S):
    """A white disc with a turning spiral, drawn on the front face so it follows the body."""
    R, CY_EYE, Z = 1.45, 2.0, -0.02
    disc = [proj(xf(cx + R * math.cos(i * math.pi / 12), CY_EYE + R * math.sin(i * math.pi / 12), Z)) for i in range(24)]
    d.polygon(disc, fill=EYE_WHITE)
    turns, steps = 2.25, 44
    line = []
    for i in range(steps + 1):
        f = i / steps
        a = f * turns * 2 * math.pi + t * SWIRL_TURN
        r = R * 0.9 * f
        line.append(proj(xf(cx + r * math.cos(a), CY_EYE + r * math.sin(a), Z)))
    d.line(line, fill=voxel.EYE, width=clawd.s(1.5, S))


def draw_dizzy(d, t, S, level, left):
    fade = min(1.0, left / RECOVER_SECS)
    amount = AMOUNT[level] * fade
    wave = math.sin(2 * math.pi * t / WOBBLE_PERIOD)
    swirl = left > EYES_BACK_SECS

    x = CX + wave * amount * CELL * 1.3
    roll = wave * amount * 0.3
    tilt = math.cos(2 * math.pi * t / WOBBLE_PERIOD) * amount * 0.14
    yaw = wave * amount * 0.3
    leaning = amount > 0.2
    legs = (0, 0, 1, 1) if leaning and wave > 0 else (1, 1, 0, 0) if leaning and wave < 0 else (0, 0, 0, 0)
    arms = (-0.6, 0) if leaning and wave > 0 else (0, -0.6) if leaning and wave < 0 else (0, 0)

    sw, sy = CELL * 5.2, CY + 4.9 * CELL
    d.ellipse((clawd.s(x - sw, S), clawd.s(sy - 4, S), clawd.s(x + sw, S), clawd.s(sy + 4, S)), fill=(8, 7, 7))
    proj, xf = voxel.render(d, voxel.build("closed" if swirl else "open", legs, arms), x, CY, CELL, yaw,
                            S=S, tilt=tilt, roll=roll)
    if swirl:
        for eye_x in (3.5, 7.5):
            draw_swirl_eye(d, proj, xf, eye_x, t, S)

    hx, hy = (v / S for v in proj(xf(5.5, 0, 2)))
    count = 2 + level
    for i in range(count):
        a = t * STAR_TURN + i * 2 * math.pi / count
        depth = (math.sin(a) + 1) / 2  # 0 behind the head, 1 in front
        k = (4.5 + 3.5 * depth) * fade
        if k < 1:
            continue
        sx, sy = hx + math.cos(a) * 46, hy - 14 + math.sin(a) * 13
        shade = 0.6 + 0.4 * depth
        d.polygon([(clawd.s(px, S), clawd.s(py, S)) for px, py in star_points(sx, sy, k)],
                  fill=tuple(int(c * shade) for c in STAR))
    top = min(proj(xf(px, py, pz))[1] for px in (0, 11) for py in (0, 8) for pz in (0, 4)) / S
    themes.call("front", d, "dizzy", t, top, proj(xf(5.5, 0, 2)), S)


def make_arcade(log):
    return Arcade([Dizzy()], log)
