# -*- coding: utf-8 -*-
"""Dated overlays for the Claude page (holidays). One module per theme in this folder.

A theme module defines START and END ("YYYY-MM-DD", inclusive) and any of:
    ring: (r, g, b)                      idle ring colour while active
    back(d, t, S)                        drawn after the ring, before the character
    front(d, pose, t, top, head, S)      drawn after the character and its props
Outside its dates a theme does nothing, so a holiday switches itself off.
"""
import datetime
import importlib
import os
import pkgutil

_cache = {"key": None, "mods": []}


def active(today=None):
    """Themes whose dates include today. XIAO_THEME_DATE=YYYY-MM-DD overrides today (previews)."""
    forced = os.environ.get("XIAO_THEME_DATE")
    today = today or (datetime.date.fromisoformat(forced) if forced else datetime.date.today())
    if _cache["key"] != today:
        mods = []
        for info in pkgutil.iter_modules(__path__):
            try:
                m = importlib.import_module(f"{__name__}.{info.name}")
                if datetime.date.fromisoformat(m.START) <= today <= datetime.date.fromisoformat(m.END):
                    mods.append(m)
            except Exception:
                continue  # a broken theme must never take the page down
        _cache.update(key=today, mods=mods)
    return _cache["mods"]


def call(hook, *args):
    for m in active():
        f = getattr(m, hook, None)
        if f:
            try:
                f(*args)
            except Exception:
                pass
