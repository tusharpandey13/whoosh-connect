import collections
import logging

from whoosh_connect import cli
from whoosh_connect.capture import replay
from whoosh_connect.mapping import DEFAULT_MAP
from whoosh_connect.pipeline import Pipeline
from whoosh_connect.zwift import BatteryFrame, Button, ButtonsFrame

EXPECTED = {"SHIFT_UP": 18, "SHIFT_DOWN": 17, "STEER_LEFT": 4, "UP": 5, "STEER_RIGHT": 5,
            "DOWN": 8, "SELECT": 4, "BACK": 6, "CAMERA": 6, "MENU": 4}


class Rec:
    def __init__(self):
        self.sends, self.statuses = [], []

    async def send(self, changes):
        self.sends.append(changes)

    async def status(self, battery, connected):
        self.statuses.append((battery, connected))

    async def close(self):
        pass


async def test_p1_replay_probe5(fix):
    rec = Rec()
    await replay(fix / "probe5_unlocked.jsonl", Pipeline(DEFAULT_MAP, [rec]))
    downs, ups = collections.Counter(), collections.Counter()
    for batch in rec.sends:
        for a, d in batch:
            (downs if d else ups)[a.name] += 1
    assert downs == EXPECTED
    assert ups == downs


async def test_p2_one_batch_per_frame():
    rec = Rec()
    p = Pipeline(DEFAULT_MAP, [rec])
    await p.on_frame(ButtonsFrame(frozenset({Button.SHIFT_R, Button.LEFT})))
    assert len(rec.sends) == 1 and len(rec.sends[0]) == 2
    await p.on_frame(ButtonsFrame(frozenset({Button.SHIFT_R, Button.LEFT})))  # F10
    assert len(rec.sends) == 1


async def test_p3_link_down_releases():
    rec = Rec()
    p = Pipeline(DEFAULT_MAP, [rec])
    await p.on_link(True, 80)
    await p.on_frame(ButtonsFrame(frozenset({Button.A})))
    await p.on_link(False)
    assert [(a.name, d) for a, d in rec.sends[-1]] == [("SELECT", False)]
    assert rec.statuses == [(80, True), (80, False)]
    await p.on_frame(BatteryFrame(79))
    assert rec.statuses[-1] == (79, False)
    await p.on_frame(BatteryFrame(79))
    assert len(rec.statuses) == 3


async def test_p4_unlock_hint_once(caplog):
    t = [0.0]
    p = Pipeline(DEFAULT_MAP, [Rec()], clock=lambda: t[0])
    await p.on_link(True, 80)
    caplog.set_level(logging.INFO)
    t[0] = 59
    p.tick()
    assert "locked" not in caplog.text
    t[0] = 61
    p.tick()
    p.tick()
    assert caplog.text.count("probably locked") == 1
    await p.on_link(True, 80)  # new link: armed again, but a frame silences it
    await p.on_frame(ButtonsFrame(frozenset()))
    t[0] = 200
    p.tick()
    assert caplog.text.count("probably locked") == 1


async def test_p5_locked_fixtures(fix):
    for name in ("probe3_locked.jsonl", "probe2_burst.jsonl"):
        rec = Rec()
        await replay(fix / name, Pipeline(DEFAULT_MAP, [rec]))
        if name == "probe3_locked.jsonl":
            assert rec.sends == []


def test_c5_cli_replay(fix, capsys):
    assert cli.main(["replay", str(fix / "probe5_unlocked.jsonl"), "--sink", "print"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert sum(1 for l in out if "SHIFT_UP" in l and "down" in l) == 18
    assert sum(1 for l in out if "SHIFT_DOWN" in l and "down" in l) == 17
