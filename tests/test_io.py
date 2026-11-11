import asyncio
import logging
import socket
import sys

import pytest

from whoosh_connect import cli, obc, state
from whoosh_connect.capture import replay
from whoosh_connect.keyboard import KeyboardSink
from whoosh_connect.mapping import DEFAULT_MAP, action
from whoosh_connect.obc_server import ObcServer, ObcSink, PortsBusy
from whoosh_connect.pipeline import Pipeline
from whoosh_connect.state import StateStore, unlock_flow, zwift_running

APP_INFO = bytes([4, 1, 8]) + b"mywhoosh" + bytes([5]) + b"6.2.3" + bytes([4, 1, 2, 0x18, 0x19])


class FakeAdv:
    def __init__(self):
        self.calls = []

    async def register(self, name, port, ip, txt):
        self.calls.append(("register", name, port, ip, txt))

    async def unregister(self):
        self.calls.append(("unregister",))


async def server(**kw):
    adv = FakeAdv()
    s = ObcServer(host="127.0.0.1", port=0, advertiser=adv, **kw)
    await s.start()
    return s, adv


async def connect(s):
    r, w = await asyncio.open_connection("127.0.0.1", s.port)
    return r, w


async def until(cond, n=200):
    for _ in range(n):
        if cond():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition not met")


def test_s1_s2_guard():
    import subprocess
    import zeroconf
    with pytest.raises(AssertionError):
        zeroconf.Zeroconf()
    with pytest.raises(AssertionError):
        subprocess.run(["osascript", "-e", "beep"])
    with pytest.raises(AssertionError):
        subprocess.run(["pgrep", "-x", "ZwiftApp"])


async def test_s3_s4_register_status_appinfo_broadcast():
    s, adv = await server()
    assert adv.calls[0][:3] == ("register", "whoosh-connect", s.port)
    assert adv.calls[0][4]["version"] == "1"
    r, w = await connect(s)
    assert await r.readexactly(3) == obc.encode_device_status(None, False)
    w.write(APP_INFO)
    await until(lambda: s.app_infos)
    assert s.app_infos[0].app_id == "mywhoosh" and s.app_infos[0].buttons == {1, 2, 0x18, 0x19}
    # status is re-sent after the app-info handshake, so the app knows we are ready
    assert await r.readexactly(3) == obc.encode_device_status(None, False)
    w.write(bytes([3, 0, 0, 0]))  # haptic ignored
    s.broadcast(bytes.fromhex("010101"))
    assert await r.readexactly(3) == bytes.fromhex("010101")
    assert not s.supports(0x40) and s.supports(1)
    w.close()
    await s.stop()
    assert adv.calls[-1] == ("unregister",)


async def test_s5_two_clients():
    s, _ = await server()
    a, aw = await connect(s)
    b, bw = await connect(s)
    await a.readexactly(3), await b.readexactly(3)
    await until(lambda: len(s._clients) == 2)
    s.broadcast(b"\x01\x01\x01")
    assert await a.readexactly(3) == await b.readexactly(3) == b"\x01\x01\x01"
    aw.close()
    await until(lambda: len(s._clients) == 1)
    s.broadcast(b"\x01\x02\x01")
    assert await b.readexactly(3) == b"\x01\x02\x01"
    bw.close()
    await s.stop()


async def test_s6_no_clients_rate_limited(caplog):
    t = [0.0]
    s, _ = await server(clock=lambda: t[0])
    caplog.set_level(logging.WARNING)
    s.broadcast(b"\x01\x01\x01")
    s.broadcast(b"\x01\x01\x00")
    assert caplog.text.count("no app connected") == 1
    t[0] = 31
    s.broadcast(b"\x01\x01\x01")
    assert caplog.text.count("no app connected") == 2
    await s.stop()


async def test_s7_port_busy():
    blocker = socket.socket()
    blocker.bind(("127.0.0.1", 0))
    blocker.listen()
    p = blocker.getsockname()[1]
    s = ObcServer(host="127.0.0.1", port=p, advertiser=FakeAdv())
    await s.start()
    assert p < s.port < p + 11
    await s.stop()
    with pytest.raises(PortsBusy):
        await ObcServer(host="127.0.0.1", port=p, advertiser=FakeAdv(), span=1).start()
    blocker.close()


async def test_s8_end_to_end(fix):
    s, _ = await server()
    r, w = await connect(s)
    await r.readexactly(3)
    await until(lambda: s._clients)
    await replay(fix / "probe5_unlocked.jsonl", Pipeline(DEFAULT_MAP, [ObcSink(s)]))
    await asyncio.sleep(0.1)
    data = await asyncio.wait_for(r.read(1 << 16), 1)
    while True:
        try:
            more = await asyncio.wait_for(r.read(1 << 16), 0.2)
        except asyncio.TimeoutError:
            break
        if not more:
            break
        data += more
    assert data.count(b"\x01\x01\x01") >= 18 and data.count(b"\x01\x02\x01") >= 17
    w.close()
    await s.stop()


def kb(front="MyWhoosh"):
    calls = []
    return KeyboardSink(runner=lambda argv, **k: calls.append((argv, k)), frontmost=lambda: front), calls


async def test_k1_k2_tap_and_hold():
    sink, calls = kb()
    await sink.send([(action("SHIFT_UP"), True)])
    assert len(calls) == 1 and calls[0][0][:2] == ["osascript", "-e"]
    assert calls[0][0][2].endswith('keystroke "k"')
    await sink.send([(action("SHIFT_UP"), False)])
    assert len(calls) == 1
    await sink.send([(action("STEER_LEFT"), True), (action("STEER_LEFT"), False)])
    assert calls[1][0][2].endswith('key down "a"') and calls[2][0][2].endswith('key up "a"')
    assert all(isinstance(c[0], list) and not c[1].get("shell") for c in calls)  # K4


async def test_k3_not_frontmost(caplog):
    sink, calls = kb(front="Safari")
    await sink.send([(action("SHIFT_UP"), True)])
    assert calls == [] and "not frontmost" in caplog.text


def test_c1_store(tmp_path):
    st = StateStore(tmp_path / "s" / "state.json")
    assert st.last_unlock() is None and st.unlock_age(100) is None
    st.record_unlock(1000.0)
    assert StateStore(tmp_path / "s" / "state.json").unlock_age(1500.0) == 500.0


async def _flow(polls, tmp_path):
    seq = iter(polls)
    t = [0.0]

    async def sleep(d):
        t[0] += d

    msgs = []
    st = StateStore(tmp_path / "st.json")
    ok = await unlock_flow(st, ps=lambda: next(seq), clock=lambda: t[0], sleep=sleep, say=msgs.append)
    return ok, st, msgs


async def test_c2_unlock_flow(tmp_path):
    ok, st, msgs = await _flow([False] + [True] * 15 + [False], tmp_path)
    assert ok and st.last_unlock() is not None and "Unlocked at" in msgs[-1]
    ok, st, msgs = await _flow([False] + [True] * 5 + [False], tmp_path / "b")
    assert not ok and st.last_unlock() is None and "Warning" in msgs[-1]


def test_zwift_running_injectable():
    assert zwift_running(ps=lambda: {"ZwiftApp", "bash"})
    assert not zwift_running(ps=lambda: {"MyWhoosh"})


def test_zwift_running_windows_names():
    # psutil on Windows reports executable names with .exe, in any case
    assert zwift_running(ps=lambda: {"System Idle Process", "explorer.exe", "ZwiftApp.exe"})
    assert zwift_running(ps=lambda: {"ZWIFTLAUNCHER.EXE"}) is False  # names stay case-sensitive
    assert zwift_running(ps=lambda: {"ZwiftLauncher.EXE"})
    assert not zwift_running(ps=lambda: {"MyWhooshGame.exe", "svchost.exe"})
    assert not zwift_running(ps=lambda: {"ZwiftApp.exe.bak"})


def test_ps_names_uses_psutil_shape():
    class P:
        def __init__(self, name):
            self.info = {"name": name}

    asked = []

    def fake_iter(attrs):
        asked.append(attrs)
        # None = psutil.AccessDenied ad_value; "" = kernel/zombie entries
        return iter([P("ZwiftApp.exe"), P(None), P(""), P("explorer.exe")])

    names = state._ps_names(process_iter=fake_iter)
    assert asked == [["name"]]
    assert names == {"ZwiftApp.exe", "explorer.exe"}
    assert zwift_running(ps=lambda: state._ps_names(process_iter=fake_iter))


def test_ps_names_never_shells_out():
    # the real psutil path must not spawn `ps` (the conftest guard would raise if it did)
    assert isinstance(state._ps_names(), set)


async def test_c3_preflight(tmp_path):
    st = StateStore(tmp_path / "st.json")
    msgs = []
    await cli.preflight(st, ps=lambda: False, clock=lambda: 1e6, say=msgs.append)
    # a missing/lapsed unlock is no longer a blocking message: it is only a debug note
    assert msgs == []
    st.record_unlock(1e6 - 100)
    msgs.clear()
    await cli.preflight(st, ps=lambda: False, clock=lambda: 1e6, say=msgs.append)
    assert msgs == []
    seq = iter([True, True, False])

    async def nosleep(d):
        pass

    await cli.preflight(st, ps=lambda: next(seq), clock=lambda: 1e6, sleep=nosleep, say=msgs.append)
    assert "Zwift is running" in msgs[0]


def test_c4_exit_codes(tmp_path, monkeypatch):
    bad = tmp_path / "bad.toml"
    bad.write_text('[map]\nNOPE = "HOME"\n')
    monkeypatch.setattr(cli, "zwift_running", lambda: False)
    assert cli.main(["run", "--config", str(bad)]) == 2

    async def no_preflight(*a, **k):
        pass

    monkeypatch.setattr(cli, "preflight", no_preflight)

    class BT:
        def __init__(self, *a, **k):
            pass

        async def run(self):
            raise cli.BluetoothUnavailable("off")

        async def close(self):
            pass

    monkeypatch.setattr(cli, "ClickLink", BT)
    monkeypatch.setattr(cli.ObcServer, "start", lambda self: asyncio.sleep(0))
    monkeypatch.setattr(cli.ObcServer, "stop", lambda self: asyncio.sleep(0))
    monkeypatch.setattr("whoosh_connect.obc_server.ZeroconfAdvertiser.unregister", lambda self: asyncio.sleep(0))
    assert cli.main(["run"]) == 3

    async def busy(self):
        raise PortsBusy("busy")

    monkeypatch.setattr(cli.ObcServer, "start", busy)
    assert cli.main(["run"]) == 4
    assert cli.main(["replay", str(tmp_path / "missing.jsonl")]) == 2


async def test_c6_second_instance_says_so(monkeypatch):
    """A second `run` must explain the mDNS collision, not raise NonUniqueNameException."""
    try:
        from zeroconf.exceptions import NonUniqueNameException
    except ModuleNotFoundError:
        from zeroconf._exceptions import NonUniqueNameException

    from whoosh_connect.obc_server import AlreadyRunning, ZeroconfAdvertiser

    class Boom:
        async def async_register_service(self, info):
            raise NonUniqueNameException("whoosh-connect._openbikecontrol._tcp.local.")

        async def async_unregister_service(self, info):
            pass

        async def async_close(self):
            pass

    adv = ZeroconfAdvertiser()
    monkeypatch.setattr("zeroconf.asyncio.AsyncZeroconf", lambda: Boom())
    with pytest.raises(AlreadyRunning) as e:
        await adv.register("whoosh-connect", 36867, "192.168.1.7", {"version": "1"})
    assert "already advertised" in str(e.value)
    assert adv._az is None


def test_paths_prefer_new_dir(tmp_path):
    new, old = tmp_path / "new", tmp_path / "old"
    # fresh install: neither exists -> new location
    assert state.resolve("state.json", new, old) == new / "state.json"
    # existing macOS install: only ~/.config copy -> keep reading it
    old.mkdir()
    (old / "state.json").write_text('{"unlock": 5}')
    assert state.resolve("state.json", new, old) == old / "state.json"
    assert StateStore(state.resolve("state.json", new, old)).last_unlock() == 5.0
    # both exist -> the new one wins
    new.mkdir()
    (new / "state.json").write_text('{"unlock": 9}')
    assert state.resolve("state.json", new, old) == new / "state.json"
    # no home dir at all (packaged app on Windows) -> new location, no crash
    assert state.resolve("config.toml", new, None) == new / "config.toml"


def test_legacy_dir_without_home(tmp_path):
    def no_home():
        raise RuntimeError("Could not determine home directory.")

    assert state.legacy_dir(home=no_home) is None
    assert state.legacy_dir(home=lambda: tmp_path) == tmp_path / ".config" / "whoosh-connect"


def test_default_paths_follow_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert state.config_dir().is_relative_to(tmp_path)
    assert state.default_state_path() == state.config_dir() / "state.json"
    legacy = tmp_path / ".config" / "whoosh-connect"
    legacy.mkdir(parents=True)
    (legacy / "config.toml").write_text('[map]\nZ = "NONE"\n')
    if state.config_dir() != legacy:  # on Linux XDG and legacy are the same dir
        assert state.default_config_path() == legacy / "config.toml"
        assert cli._config_path(None) == legacy / "config.toml"
    assert cli._config_path("x.toml") == "x.toml"


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS log location")
def test_log_path_matches_launchd(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    # the location scripts/install_launchd.sh and `make ride-bg` already write to (#3, #10)
    assert state.default_log_path() == tmp_path / "Library" / "Logs" / "whoosh-connect" / "ride.log"
    assert state.config_dir() == tmp_path / "Library" / "Application Support" / "whoosh-connect"
