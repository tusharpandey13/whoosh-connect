"""#9: one "gears asleep" notice per closed press window, quiet mid-window."""
import asyncio

from whoosh_connect import app, macos, messages
from whoosh_connect.asleep import ACTIVE_S, SETTLE_S, Event, SleepWatch
from whoosh_connect.status import Phase, StatusModel
from whoosh_connect.zwift import Button, ButtonsFrame, VendorFrame, parse_frame

STOPPED_EA = parse_frame(bytes.fromhex("ff0500ea05aabb"))
STOPPED_FA = parse_frame(bytes.fromhex("ff0500fa05"))
OTHER_VENDOR = VendorFrame(bytes.fromhex("ff0400"))
PRESS = ButtonsFrame(frozenset({Button.SHIFT_R}))


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def watch_after_link():
    c = Clock()
    w = SleepWatch(clock=c)
    w.on_link(True)
    c.t += SETTLE_S + 1
    return w, c


def test_real_stopped_bytes_parse_as_vendor_frames():
    assert isinstance(STOPPED_EA, VendorFrame) and isinstance(STOPPED_FA, VendorFrame)


def test_closed_window_fires_once():
    w, c = watch_after_link()
    c.t += 50
    assert w.on_frame(STOPPED_EA) is Event.ASLEEP and w.asleep
    assert w.on_frame(STOPPED_FA) is None  # same window: no repeat
    c.t += 60
    assert w.on_frame(STOPPED_EA) is None


def test_press_wakes_and_rearms():
    w, c = watch_after_link()
    assert w.on_frame(STOPPED_EA) is Event.ASLEEP
    assert w.on_frame(PRESS) is Event.AWAKE and not w.asleep
    c.t += ACTIVE_S + 40
    assert w.on_frame(STOPPED_EA) is Event.ASLEEP  # the next window may fire again


def test_quiet_mid_window():
    w, c = watch_after_link()
    w.on_frame(PRESS)
    c.t += ACTIVE_S - 1
    assert w.on_frame(STOPPED_EA) is None and not w.asleep


def test_leftover_stopped_frame_after_connect_ignored():
    c = Clock()
    w = SleepWatch(clock=c)
    w.on_link(True)
    c.t += SETTLE_S - 0.5
    assert w.on_frame(STOPPED_EA) is None
    c.t += 1
    assert w.on_frame(STOPPED_EA) is Event.ASLEEP


def test_new_link_rearms_and_clears():
    w, c = watch_after_link()
    w.on_frame(STOPPED_EA)
    assert w.on_link(False) is Event.AWAKE and not w.asleep
    assert w.on_frame(STOPPED_EA) is None  # link down: not armed
    w.on_link(True)
    c.t += SETTLE_S + 1
    assert w.on_frame(STOPPED_EA) is Event.ASLEEP


def test_other_frames_ignored():
    w, _ = watch_after_link()
    assert w.on_frame(OTHER_VENDOR) is None
    assert w.on_frame(parse_frame(bytes.fromhex("191050"))) is None


# -- menu line ---------------------------------------------------------------------------
def test_menu_line_says_asleep_and_clears_on_link_change():
    m = StatusModel()
    m.set_link(True, 80)
    m.set_apps(1)
    m.set_asleep(True)
    s = m.snapshot()
    assert s.phase is Phase.RIDING and "Gears asleep - tap + or - once" in s.line
    m.set_asleep(False)
    assert "asleep" not in m.snapshot().line
    m.set_asleep(True)
    m.set_link(False)
    assert "asleep" not in m.snapshot().line


# -- bridge: notify only while riding, once per window ------------------------------------
class NullPipeline:
    async def on_frame(self, frame):
        pass

    async def on_link(self, up, battery=None):
        pass


def bridge(apps):
    c, sent = Clock(), []
    model = StatusModel()
    b = app.Bridge(model, clock=c, notify=sent.append)
    b.pipeline = NullPipeline()
    model.set_apps(apps)
    return b, model, c, sent


async def test_bridge_notifies_once_while_riding():
    b, model, c, sent = bridge(apps=1)
    await b._on_link(True, 80)
    c.t += SETTLE_S + 1
    await b._on_frame(STOPPED_EA)
    await b._on_frame(STOPPED_FA)
    assert sent == ["Gears asleep - tap + or - once"]
    assert "Gears asleep" in model.snapshot().line
    await b._on_frame(PRESS)
    assert "asleep" not in model.snapshot().line


async def test_bridge_no_notification_when_not_riding():
    b, model, c, sent = bridge(apps=0)
    await b._on_link(True, 80)
    c.t += SETTLE_S + 1
    await b._on_frame(STOPPED_EA)
    assert sent == [] and "Gears asleep" in model.snapshot().line  # menu still says it


async def test_frames_still_reach_the_pipeline():
    b, _, c, _ = bridge(apps=1)
    got = []

    class Rec(NullPipeline):
        async def on_frame(self, frame):
            got.append(frame)

    b.pipeline = Rec()
    await b._on_frame(STOPPED_EA)
    await b._on_frame(PRESS)
    assert got == [STOPPED_EA, PRESS]


# -- notification seam -------------------------------------------------------------------
def test_notify_passes_text_as_argument_not_script():
    calls = []
    macos.notify('say "hi" & rm -rf', runner=lambda argv, **kw: calls.append(argv))
    argv = calls[0]
    assert argv[0] == "osascript" and argv[-2:] == ['say "hi" & rm -rf', "whoosh-connect"]
    assert all('say "hi"' not in a for a in argv[:-2])


def test_notify_never_raises():
    def boom(*a, **kw):
        raise OSError("no osascript")
    macos.notify("x", runner=boom)


def test_asleep_message_matches_issue_text():
    assert messages.ASLEEP.text.rstrip(".") == "Gears asleep - tap + or - once"


def test_watch_sends_nothing_to_the_unit():
    import inspect
    from whoosh_connect import asleep
    src = inspect.getsource(asleep)
    assert "write" not in src and "RESET" not in src
