# -*- coding: utf-8 -*-
"""Signal strength to interaction. Only needs pillow:
  uv run --python 3.11 --with pillow python test_buddy.py
"""
import io
import logging
import random

from PIL import Image

import buddy as b

log = logging.getLogger("test")
TEMPS = {b.XIAO: 50.0, b.HOLO: 88.0, "board": 74.0}


class Run:
    """Drives a Buddy second by second, the way the boards report."""

    def __init__(self, seed=1):
        self.buddy = b.Buddy(log, random.Random(seed))
        self.now = 1000.0
        self.kinds = []

    def go(self, secs, rssi, age=200, holo=True, paused=False, temps=TEMPS):
        for _ in range(int(secs * 2)):
            self.now += 0.5
            report = (rssi, age, self.now)
            before = self.buddy.serial
            self.buddy.update(self.now, {b.XIAO: report, b.HOLO: report if holo else None}, temps, paused)
            if self.buddy.serial != before:
                self.kinds.append(self.buddy.current.script.kind)


def test_measured_distances_fall_in_the_right_zones():
    measured = {"touching": (-28, "near"), "10 cm": (-40, "mid"), "30 cm": (-47, "mid"), "called far by the user": (-52, "far"), "1 m": (-63.5, "far")}
    for name, (rssi, zone) in measured.items():
        assert b.zone_of(rssi, "mid") == zone, name
    assert b.zone_of(-35.5, None) == "mid"  # where the boards first sat on the desk: must not count as too close


def test_hysteresis():
    assert b.zone_of(-36, "near") == "near" and b.zone_of(-36, "mid") == "mid"
    assert b.zone_of(-48, "far") == "far" and b.zone_of(-48, "mid") == "mid"


def test_too_close_starts_a_near_reaction_once_then_later_again():
    run = Run()
    run.go(5, -37)
    assert run.kinds == []
    run.go(1, -28)
    run.go(3, -28)
    assert len(run.kinds) == 1 and run.kinds[0] in b.NEAR_WEIGHTS
    run.go(20, -28)
    assert len(run.kinds) == 1  # no nagging while it stays close
    run.go(200, -28)
    assert len(run.kinds) >= 2


def test_all_near_kinds_come_up():
    seen = set()
    for seed in range(60):
        run = Run(seed)
        run.go(3, -37)
        run.go(4, -28)
        seen.update(run.kinds)
    assert seen == set(b.NEAR_WEIGHTS)


def test_hot_comes_up_more_when_hot():
    def count(temps):
        return sum(Run(seed).buddy._pick_near(temps) == "hot" for seed in range(600))
    assert count({"board": 80.0}) > count({"board": 50.0}) * 1.5


def test_far_needs_to_last():
    run = Run()
    run.go(3, -37)
    run.go(3, -65)
    assert run.kinds == []
    run.go(8, -65)
    assert run.kinds == ["far"]
    run.go(30, -65)
    assert run.kinds == ["far"]  # once per trip away


def test_someone_walking_past_does_not_count_as_far():
    run = Run()
    run.go(3, -37)
    for _ in range(4):
        run.go(1, -75)
        run.go(3, -37)
    assert run.kinds == []


def test_unplugged_board_is_lost_then_back():
    run = Run()
    run.go(3, -37)
    run.go(3, -37, age=9000, holo=False)
    assert run.kinds == ["lost"] and run.buddy.rssi is None
    run.go(10, -37, age=9000, holo=False)
    run.go(130, -37, age=9000, holo=False)
    assert run.kinds == ["lost"]
    run.go(3, -37)
    assert run.kinds == ["lost", "back"]


def test_paused_starts_nothing_and_ends_what_runs():
    run = Run()
    run.go(3, -37)
    run.go(4, -28, paused=True)
    assert run.kinds == []
    run.go(1, -28)
    assert run.buddy.active(run.now)
    run.go(1, -28, paused=True)
    assert not run.buddy.active(run.now)


def test_jump_is_a_fight_from_that_side_and_only_one_at_a_time():
    run = Run()
    run.go(1, -37)
    assert run.buddy.jump(b.XIAO, run.now)
    assert run.buddy.current.first == b.XIAO and run.buddy.current.script.kind == "fight"
    assert not run.buddy.jump(b.HOLO, run.now)


def test_no_jump_when_the_other_board_cannot_be_heard():
    run = Run()
    run.go(3, -37)
    run.go(3, -37, age=9000, holo=False)
    run.buddy.cancel()
    assert not run.buddy.jump(b.XIAO, run.now)
    assert run.buddy.current.script.kind == "lost" and not run.buddy.current.script.jump


def test_close_before_the_unplug_does_not_keep_fighting():
    run = Run()
    run.go(4, -28)
    run.kinds.clear()
    run.buddy.cancel()
    run.go(300, -28, age=9000, holo=False)
    assert set(run.kinds) <= {"lost"} and run.buddy.zone is None


def test_both_sides_share_the_messages_in_order():
    run = Run(3)
    run.buddy._begin("fight", run.now, b.XIAO)
    run.go(12, -37)
    said = list(run.buddy.history)
    assert [t for _, t, _ in said] == ["靠太近了啦！", "來啊！", "碰！", "啪！", "…算你狠"]
    assert [w for w, _, _ in said] == [b.XIAO, b.HOLO, b.XIAO, b.HOLO, b.XIAO]


def test_hot_message_uses_the_real_temperature():
    run = Run()
    script = b.SCRIPTS["hot"][0]
    run.buddy.current = b.Interaction(script, b.HOLO, run.now, 88)
    run.go(1, -37)
    assert run.buddy.history[0][1] == "88° 了，走開啦"


def test_pull_only_for_what_should_pull():
    run = Run()
    run.buddy._begin("far", run.now)
    assert run.buddy.wants_pull(run.now) is None
    run.buddy.cancel()
    run.buddy._begin("fight", run.now)
    assert run.buddy.wants_pull(run.now) == run.buddy.serial


def test_every_script_renders_on_both_boards_through_its_whole_length():
    for kind, scripts in b.SCRIPTS.items():
        for script in scripts:
            run = Run()
            run.buddy.current = b.Interaction(script, b.XIAO, run.now, 88)
            start = run.now
            for i in range(int(script.secs * 2) + 1):
                now = start + i * 0.5
                run.buddy.update(now, {}, TEMPS)
                if run.buddy.current is None:
                    run.buddy.current = b.Interaction(script, b.XIAO, start, 88)
                for side in (b.XIAO, b.HOLO):
                    jpg = run.buddy.render(side, now, now, (74.0, 88.0))
                    assert Image.open(io.BytesIO(jpg)).size == (240, 240) and len(jpg) < 40000, (kind, i, len(jpg))


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
    print("PASSED")
