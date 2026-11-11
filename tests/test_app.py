"""#2: menu-bar states, the bridge behind them, and a Quit that closes BLE + mDNS."""
import asyncio
import signal

import pytest

from whoosh_connect import app, messages
from whoosh_connect.ble import BluetoothUnavailable
from whoosh_connect.obc_server import AlreadyRunning, ObcServer, PortsBusy
from whoosh_connect.status import Phase, Status, StatusModel
from whoosh_connect.tray import Tray

APP_INFO = bytes([4, 1, 8]) + b"mywhoosh" + bytes([5]) + b"6.2.3" + bytes([4, 1, 2, 0x18, 0x19])


# -- status model ------------------------------------------------------------------------
def test_phases_and_lines():
    seen = []
    m = StatusModel(seen.append)
    assert m.snapshot().phase is Phase.SEARCHING and "wake" in m.snapshot().line
    m.set_link(True, 80)
    assert seen[-1].phase is Phase.WAITING and "OpenBikeControl" in seen[-1].line
    assert "battery 80 %" in seen[-1].line
    m.set_apps(1)
    assert seen[-1].phase is Phase.RIDING
    m.set_error(messages.BT_DENIED)
    assert seen[-1] == Status(Phase.ERROR, messages.BT_DENIED.text, messages.BT_DENIED, 80)
    m.clear_error()
    assert seen[-1].phase is Phase.RIDING
    m.set_link(False)
    assert seen[-1].phase is Phase.SEARCHING and "battery" not in seen[-1].line


def test_on_change_only_on_change():
    seen = []
    m = StatusModel(seen.append)
    m.set_apps(0)
    m.set_apps(0)
    m.set_battery(None)
    assert seen == [m.snapshot()]  # first call reports, repeats don't


# -- bridge ------------------------------------------------------------------------------
class FakeAdv:
    def __init__(self):
        self.calls = []

    async def register(self, name, port, ip, txt):
        self.calls.append("register")

    async def unregister(self):
        self.calls.append("unregister")


class FakeLink:
    """Stands in for ClickLink: scripted outcomes per run(), records close()."""
    instances = []

    def __init__(self, script):
        self.script = script

    def __call__(self, on_frame, on_link):
        self.on_frame, self.on_link = on_frame, on_link
        self.closed = False
        FakeLink.instances.append(self)
        return self

    async def run(self):
        step = self.script.pop(0) if self.script else "hold"
        if isinstance(step, Exception):
            raise step
        if step == "up":
            await self.on_link(True, 80)
        await asyncio.Event().wait()  # until cancelled / closed

    async def close(self):
        self.closed = True


def make_bridge(model, script=("up",), ps=lambda: False, server_factory=None, **kw):
    adv = FakeAdv()
    sf = server_factory or (lambda port: ObcServer(host="127.0.0.1", port=0, advertiser=adv))
    b = app.Bridge(model, server_factory=sf, link_factory=FakeLink(list(script)), ps=ps,
                   retry_s=0.01, poll_s=0.01, clear_after_s=0.05, config=None, **kw)
    return b, adv


async def until(pred, timeout=2.0):
    async with asyncio.timeout(timeout):
        while not pred():
            await asyncio.sleep(0.005)


async def test_bridge_walks_all_states(tmp_path, monkeypatch, caplog):
    monkeypatch.setenv("HOME", str(tmp_path))
    caplog.set_level("INFO")
    seen = []
    model = StatusModel(lambda s: seen.append(s.phase))
    b, adv = make_bridge(model)
    task = asyncio.create_task(b.run())
    await until(lambda: model.snapshot().phase is Phase.WAITING)
    r, w = await asyncio.open_connection("127.0.0.1", b.server.port)
    w.write(APP_INFO)
    await w.drain()
    await until(lambda: model.snapshot().phase is Phase.RIDING)
    w.close()
    await until(lambda: model.snapshot().phase is Phase.WAITING)
    await b.stop()
    task.cancel()
    assert seen[:4] == [Phase.SEARCHING, Phase.WAITING, Phase.RIDING, Phase.WAITING]
    assert b.link.closed and adv.calls == ["register", "unregister"]
    assert not [r for r in caplog.records if r.levelno >= 30], "normal ride path must not warn"


async def test_denied_then_granted_recovers_without_restart(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    model = StatusModel()
    errors = []
    model.on_change = lambda s: errors.append(s.error)
    b, adv = make_bridge(model, script=[BluetoothUnavailable("x", reason="denied"), "up"])
    task = asyncio.create_task(b.run())
    await until(lambda: messages.BT_DENIED in errors)
    await until(lambda: model.snapshot().phase is Phase.WAITING)  # same process, no restart
    assert model.error is None
    await b.stop()
    task.cancel()


async def test_bluetooth_off_stays_error_while_retrying(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    model = StatusModel()
    flips = []
    model.on_change = lambda s: flips.append(s.phase)
    off = [BluetoothUnavailable("x", reason="off") for _ in range(5)]
    b, _ = make_bridge(model, script=off)
    task = asyncio.create_task(b.run())
    await until(lambda: len(FakeLink.instances) >= 5)
    # clear_after_s outlives each failed attempt, so no Searching<->Error flicker
    assert flips.count(Phase.ERROR) == 1 and model.error is messages.BT_OFF
    await b.stop()
    task.cancel()


async def test_zwift_blocks_then_clears(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    running = iter([True, True, False])
    model = StatusModel()
    errs = []
    model.on_change = lambda s: errs.append(s.error)

    async def nosleep(_):
        await asyncio.sleep(0)

    b, _ = make_bridge(model, ps=lambda: next(running, False), sleep=nosleep)
    task = asyncio.create_task(b.run())
    await until(lambda: model.snapshot().phase is Phase.WAITING)
    assert errs[0] is messages.ZWIFT and model.error is None
    await b.stop()
    task.cancel()


async def test_already_running_is_explained_not_retried(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))

    class Taken:
        app_infos = []

        async def start(self):
            raise AlreadyRunning("whoosh-connect is already advertised")

    model = StatusModel()
    b, _ = make_bridge(model, server_factory=lambda port: Taken())
    await asyncio.wait_for(b.run(), 1)
    assert model.error is messages.RUNNING and b.link is None
    await b.stop()


async def test_ports_busy_retries(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    adv = FakeAdv()
    tries = []

    def sf(port):
        tries.append(port)
        srv = ObcServer(host="127.0.0.1", port=0, advertiser=adv)
        if len(tries) < 3:
            async def busy():
                raise PortsBusy("no free TCP port in 36867-36877")
            srv.start = busy
        return srv

    model = StatusModel()
    errs = []
    model.on_change = lambda s: errs.append(s.error)
    b, _ = make_bridge(model, server_factory=sf)
    task = asyncio.create_task(b.run())
    await until(lambda: model.snapshot().phase is Phase.WAITING)
    assert len(tries) == 3 and messages.PORTS in errs
    await b.stop()
    task.cancel()


async def test_bad_config_is_explained(tmp_path):
    bad = tmp_path / "c.toml"
    bad.write_text('[map]\nNOPE = "HOME"\n')
    model = StatusModel()
    b, _ = make_bridge(model)
    b.config = str(bad)
    await b.run()
    assert model.error is messages.CONFIG


async def test_stop_before_start_is_safe():
    b, _ = make_bridge(StatusModel())
    await b.stop()


# -- tray (fake pystray, no AppKit) ------------------------------------------------------
class FakeIcon:
    def __init__(self, name, icon, title, menu):
        self.name, self.icon, self.title, self.menu = name, icon, title, menu
        self.menu_updates = self.stopped = self.ran = 0

    def update_menu(self):
        self.menu_updates += 1

    def stop(self):
        self.stopped += 1

    def run(self):
        self.ran += 1


class FakeItem:
    def __init__(self, text, action, enabled=True, visible=True):
        self.text, self.action, self.enabled, self.visible = text, action, enabled, visible

    def label(self):
        return self.text(self) if callable(self.text) else self.text

    def shown(self):
        return self.visible(self) if callable(self.visible) else self.visible


class FakeMenu:
    SEPARATOR = object()

    def __init__(self, *items):
        self.items = items


class FakePystray:
    Icon, MenuItem, Menu = FakeIcon, FakeItem, FakeMenu


def tray(**kw):
    quits, opened = [], []
    t = Tray(quit=lambda: quits.append(1), pystray=FakePystray, on_main=lambda f: f(),
             image=lambda phase: phase.name, opener=opened.append, **kw)
    return t, quits, opened


def items(t):
    return [i for i in t.icon.menu.items if i is not FakeMenu.SEPARATOR]


def test_tray_menu_tracks_status():
    t, _, opened = tray()
    line, settings, quit_item = items(t)
    assert line.enabled is False and "Searching" in line.label()
    assert not settings.shown() and quit_item.label() == "Quit whoosh-connect"
    t.update(Status(Phase.ERROR, messages.BT_DENIED.text, messages.BT_DENIED))
    assert t.icon.icon == "ERROR" and line.label() == messages.BT_DENIED.text
    assert settings.shown()
    settings.action()
    assert opened == [messages.BT_SETTINGS_URL]
    assert messages.BT_DENIED.text in t.icon.title


def test_tray_redraws_icon_only_on_phase_change():
    t, _, _ = tray()
    drawn = []
    t.image = lambda p: drawn.append(p) or p.name
    t.update(Status(Phase.WAITING, "Waiting - a"))
    t.update(Status(Phase.WAITING, "Waiting - a (battery 80 %)"))
    t.update(Status(Phase.RIDING, "Riding"))
    assert drawn == [Phase.WAITING, Phase.RIDING] and t.icon.menu_updates == 3


def test_tray_quit_closes_bridge_then_icon_once():
    t, quits, _ = tray()
    items(t)[-1].action()
    items(t)[-1].action()
    assert quits == [1] and t.icon.stopped == 1


def test_tray_quit_stops_icon_even_if_bridge_fails():
    t = Tray(quit=lambda: 1 / 0, pystray=FakePystray, on_main=lambda f: f(), image=str)
    with pytest.raises(ZeroDivisionError):
        t.quit()
    assert t.icon.stopped == 1


def test_sigterm_routes_to_quit():
    handlers = {}

    class Signals:
        @staticmethod
        def signal(sig, fn):
            handlers[sig] = fn

    t, quits, _ = tray(signals=Signals)
    t.run()
    assert t.icon.ran == 1
    handlers[signal.SIGTERM](signal.SIGTERM, None)
    assert quits == [1] and t.icon.stopped == 1


def test_draw_every_phase():
    pytest.importorskip("PIL")
    from whoosh_connect.tray import draw

    imgs = {p: draw(p) for p in Phase}
    assert all(i.size == (44, 44) and i.mode == "RGBA" for i in imgs.values())
    assert len({i.tobytes() for i in imgs.values()}) == 4  # all distinguishable


# -- the whole quit path, threads included ------------------------------------------------
def test_main_quit_closes_ble_and_mdns(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    made = {}

    def bridge_factory(model):
        b, adv = make_bridge(model)
        made.update(bridge=b, adv=adv, model=model)
        return b

    class AutoQuitTray:
        def __init__(self, quit):
            self._quit, self.seen = quit, []

        def update(self, status):
            self.seen.append(status.phase)

        def run(self):
            import time
            deadline = time.monotonic() + 2
            while Phase.WAITING not in self.seen and time.monotonic() < deadline:
                time.sleep(0.01)
            self._quit()  # what the Quit menu item does

    assert app.main([], tray_factory=AutoQuitTray, bridge_factory=bridge_factory) == 0
    assert made["bridge"].link.closed
    assert made["adv"].calls == ["register", "unregister"]


def test_bridge_crash_turns_icon_red():
    model = StatusModel()
    fut = asyncio.Future(loop=asyncio.new_event_loop())
    fut.set_exception(RuntimeError("boom"))
    app._bridge_ended(fut, model)
    assert model.error is messages.UNKNOWN
