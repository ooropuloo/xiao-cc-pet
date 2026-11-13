# -*- coding: utf-8 -*-
"""Tiny voxel renderer for the 3D 小橘人 (painter's algorithm, flat-shaded faces).

The model is the 2D sprite extruded: body 4 voxels deep, arms 2, legs 2,
eyes are dark voxels on the front layer. Pure PIL, ~300 faces per frame.
"""
import math

ORANGE = (217, 119, 87)
EYE = (40, 26, 22)
LIGHT = (-0.45, -0.75, -0.5)  # from upper-left-front
_ln = math.sqrt(sum(c * c for c in LIGHT))
LIGHT = tuple(c / _ln for c in LIGHT)

# 11 wide x 6 tall body rows (top to bottom); legs added below
SPRITE = [
    "..BBBBBBB..",
    "..BEBBBEB..",
    "..BEBBBEB..",
    "AABBBBBBBAA",
    "AABBBBBBBAA",
    "..BBBBBBB..",
]
LEG_COLS = (2, 4, 6, 8)
DEPTH = 4

FACES = [  # (normal, 4 corner offsets) for a unit cube at origin
    ((0, 0, -1), [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)]),  # front (toward camera)
    ((0, 0, 1), [(1, 0, 1), (0, 0, 1), (0, 1, 1), (1, 1, 1)]),
    ((-1, 0, 0), [(0, 0, 1), (0, 0, 0), (0, 1, 0), (0, 1, 1)]),
    ((1, 0, 0), [(1, 0, 0), (1, 0, 1), (1, 1, 1), (1, 1, 0)]),
    ((0, -1, 0), [(0, 0, 1), (1, 0, 1), (1, 0, 0), (0, 0, 0)]),  # top (y grows downward)
    ((0, 1, 0), [(0, 1, 0), (1, 1, 0), (1, 1, 1), (0, 1, 1)]),
]


KEYBOARD = (46, 50, 58)
KEY = (196, 200, 210)
KEY_DARK = (128, 134, 148)
KEY_LIT = (255, 214, 110)
KEY_TOP = 5.0            # y of the keycaps' top face = bottom of the arms, so the hands rest on it
KB_COLS, KB_PITCH, KB_X0 = 12, 0.94, 0.0


def build(eye_mode="open", legs=(0, 0, 0, 0), arms=(0, 0), keyboard=None):
    """Return {(x, y, z): color}. eye_mode: open | blink | x | look_l | look_r | look_up.

    arms: (left, right) drop in voxels (negative lifts). keyboard: when not None the
    arms reach forward onto the keyboard from typing_rig().
    """
    vox = {}
    for y, row in enumerate(SPRITE):
        for x, ch in enumerate(row):
            if ch == ".":
                continue
            if ch == "A":
                drop = arms[0] if x < 5 else arms[1]
                for z in range(-2, 2) if keyboard is not None else range(1, 3):
                    vox[(x, y + drop, z)] = ORANGE
                continue
            for z in range(DEPTH):
                vox[(x, y, z)] = ORANGE
    # eyes on the front layer
    eye_cols = {"look_l": (2.6, 6.6), "look_r": (3.4, 7.4)}.get(eye_mode, (3, 7))
    for ec in eye_cols:
        c = int(round(ec))
        if eye_mode == "blink":
            vox[(c, 2, 0)] = EYE
        elif eye_mode in ("x", "closed"):
            pass  # drawn as lines by the caller
        elif eye_mode == "look_up":
            vox[(c, 1, 0)] = EYE
        else:
            vox[(c, 1, 0)] = EYE
            vox[(c, 2, 0)] = EYE
    for i, c in enumerate(LEG_COLS):
        lift = legs[i]
        for y in range(6, 8 - lift):
            for z in (1, 2):
                vox[(c, y, z)] = ORANGE
    return vox


def typing_rig(lit):
    """Boxes (x0, y0, z0, x1, y1, z1, colour) for a keyboard at hand height in front
    of the body (it hides the legs). lit: (row, col) key glowing, or None.
    """
    boxes = [(-0.4, KEY_TOP + 0.2, -3.9, 11.4, KEY_TOP + 0.9, 0.3, KEYBOARD)]  # case
    for r in range(3):
        z0 = -3.55 + r * 0.95
        off = (0.0, 0.25, 0.5)[r]
        for c in range(KB_COLS - (1 if r else 0)):
            x0 = KB_X0 + off + c * KB_PITCH
            col = KEY_LIT if lit == (r, c) else (KEY_DARK if c in (0, KB_COLS - 1) else KEY)
            boxes.append((x0, KEY_TOP, z0, x0 + 0.76, KEY_TOP + 0.2, z0 + 0.74, col))
    # bottom row: two modifiers each side of a long space bar
    for x0, x1 in ((0.0, 1.6), (1.84, 3.1), (3.34, 7.66), (7.9, 9.16), (9.4, 11.0)):
        col = KEY_LIT if lit == (3, 0) and x0 == 3.34 else (KEY if x0 == 3.34 else KEY_DARK)
        boxes.append((x0, KEY_TOP, -0.7, x1, KEY_TOP + 0.2, 0.04, col))
    return boxes


def render(draw, vox, cx, cy, size, yaw, pitch=0.32, bob=0.0, S=2, dist=34.0, tilt=0.0, boxes=(), roll=0.0):
    """Draw voxels centred at (cx, cy) in 240-space; size = px per voxel.

    tilt rotates the model about its feet (x axis); negative falls backwards. roll leans it sideways.
    boxes: extra (x0, y0, z0, x1, y1, z1, colour) solids of any size, depth-sorted with the voxels.
    """
    cyaw, syaw = math.cos(yaw), math.sin(yaw)
    cp, sp = math.cos(pitch), math.sin(pitch)
    ct, st = math.cos(tilt), math.sin(tilt)
    cr, sr = math.cos(roll), math.sin(roll)
    ox, oy, oz = 5.5, 4.0 + bob, DEPTH / 2  # positive bob lifts the model
    FEET = 8.0

    def tilt_p(y, z):
        y, z = y - FEET, z - oz
        return y * ct - z * st + FEET, y * st + z * ct + oz

    def xf(x, y, z):
        y, z = tilt_p(y, z)
        x, y = (x - ox) * cr - (y - FEET) * sr + ox, (x - ox) * sr + (y - FEET) * cr + FEET
        x, y, z = x - ox, y - oy, z - oz
        x, z = x * cyaw - z * syaw, x * syaw + z * cyaw
        y, z = y * cp - z * sp, y * sp + z * cp
        return x, y, z

    def proj(p):
        k = dist / (p[2] + dist)
        return (cx + p[0] * size * k) * S, (cy + p[1] * size * k) * S

    def rot_n(n):
        x, y, z = n
        y, z = y * ct - z * st, y * st + z * ct
        x, y = x * cr - y * sr, x * sr + y * cr
        x, z = x * cyaw - z * syaw, x * syaw + z * cyaw
        y, z = y * cp - z * sp, y * sp + z * cp
        return x, y, z

    solids = [((x, y, z), (1, 1, 1), col, True) for (x, y, z), col in vox.items()]
    solids += [((b[0], b[1], b[2]), (b[3] - b[0], b[4] - b[1], b[5] - b[2]), b[6], False) for b in boxes]
    polys = []
    for (x, y, z), (wx, wy, wz), col, is_vox in solids:
        for n, corners in FACES:
            if is_vox and (x + n[0], y + n[1], z + n[2]) in vox:
                continue  # hidden by a neighbour
            rn = rot_n(n)
            pts3 = [xf(x + a * wx, y + b * wy, z + c * wz) for a, b, c in corners]
            ctr = [sum(p[i] for p in pts3) / 4 for i in range(3)]
            # camera sits at z = -dist looking +z
            view = (ctr[0], ctr[1], ctr[2] + dist)
            if rn[0] * view[0] + rn[1] * view[1] + rn[2] * view[2] >= 0:
                continue
            # LIGHT points toward the light, so a face is lit when its normal agrees with it
            lam = max(0.0, rn[0] * LIGHT[0] + rn[1] * LIGHT[1] + rn[2] * LIGHT[2])
            k = 0.55 + 0.6 * lam
            fill = tuple(min(255, int(c * k)) for c in col)
            edge = tuple(int(c * 0.93) for c in fill)
            polys.append((ctr[2], [proj(p) for p in pts3], fill, edge))
    polys.sort(key=lambda t: -t[0])
    for _, pts, fill, edge in polys:
        draw.polygon(pts, fill=fill, outline=edge)
    return proj, xf
