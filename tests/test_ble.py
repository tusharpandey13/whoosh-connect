import asyncio
import logging
import types

import pytest

from whoosh_connect.capture import CaptureWriter, read_capture
from whoosh_connect.ble import BluetoothUnavailable, ClickLink
from whoosh_connect.zwift import ASYNC, POST_HANDSHAKE, RESET, RIDE_ON, SYNC_RX, SYNC_TX

LEFT = bytes.fromhex("0b2d7e")
RIGHT = bytes.fromhex("0ae89b")


class Clock:
    def __init__(self):
        self.t = 0.0
        self.delays = []

    def __call__(self):
        return self.t

    async def sleep(self, d):
        self.delays.append(d)
        self.t += d
        await asyncio.sleep(0)


def fake_scanner(adverts, err=None):
    """adverts: list of (address, mfr bytes), delivered on scanner start."""
    class S:
        def __init__(self, cb):
            self.cb = cb

        async def __aenter__(self):
            if err:
                raise err
            for addr, mfr in adverts:
                self.cb(types.SimpleNamespace(address=addr),
                        types.SimpleNamespace(manufacturer_data={0x094A: mfr}, rssi=-50))

        async def __aexit__(self, *a):
            pass
    return S


class FakeClient:
    instances = []

    def __init__(self, device, disconnected_callback=None, reply=True, fail_connect=False):
        self.dc, self.reply, self.fail_connect = disconnected_callback, reply, fail_connect
        self.log, self.cbs, self.disconnected = [], {}, False

    async def connect(self):
        if self.fail_connect:
            raise OSError("connect failed")
        self.log.append(("connect",))

    async def start_notify(self, uuid, cb):
        self.cbs[uuid] = cb
        self.log.append(("notify", uuid[:8]))

    async def write_gatt_char(self, uuid, data, response=True):
        self.log.append(("write", uuid[:8], bytes(data), response))
        if data == RIDE_ON and self.reply:
            self.cbs[SYNC_TX](None, bytearray(b"RideOn"))

    async def read_gatt_char(self, uuid):
        return bytes([80])

    async def disconnect(self):
        self.disconnected = True


def make_link(clock, adverts, clients, **kw):
    frames, links = [], []

    async def on_frame(f):
        frames.append(f)

    async def on_link(up, batt=None):
        links.append((up, batt))

    it = iter(clients)
    link = ClickLink(on_frame, on_link, scanner_factory=fake_scanner(adverts),
                     client_factory=lambda d, disconnected_callback=None: _bind(next(it), disconnected_callback),
                     clock=clock, sleep=clock.sleep, zwift_running=kw.pop("zwift_running", lambda: False),
                     handshake_timeout=kw.pop("handshake_timeout", 0.05), **kw)
    return link, frames, links


def _bind(client, cb):
    client.dc = cb
    return client


async def test_b1_selection():
    clock = Clock()
    link = ClickLink(None, None, scanner_factory=fake_scanner([("R", RIGHT), ("L", LEFT)]),
                     client_factory=object, clock=clock, sleep=clock.sleep)
    dev, info = await link.select()
    assert dev.address == "L" and info.side == "left"


async def test_b1_right_only_after_wait_and_wake_hint(caplog):
    caplog.set_level(logging.INFO)
    clock = Clock()
    link = ClickLink(None, None, scanner_factory=fake_scanner([("R", RIGHT)]),
                     client_factory=object, clock=clock, sleep=clock.sleep)
    dev, info = await link.select()
    assert info.side == "right" and clock.t >= 10
    assert "right puck only" in caplog.text
    # F1: nothing found -> wake hint repeats every 30 s
    caplog.clear()
    clock2 = Clock()
    link = ClickLink(None, None, scanner_factory=fake_scanner([]), client_factory=object,
                     clock=clock2, sleep=clock2.sleep)
    task = asyncio.create_task(link.select())
    while clock2.t < 65:
        await asyncio.sleep(0)
    await link.close()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert caplog.text.count("Press a Click button") == 3  # t=0, 30, 60


async def test_b2_b6_handshake_and_frames(caplog):
    caplog.set_level(logging.INFO)
    clock = Clock()
    c = FakeClient(None)
    link, frames, links = make_link(clock, [("L", LEFT)], [c])
    task = asyncio.create_task(link.run())
    while not links:
        await asyncio.sleep(0.01)
    assert c.log == [("connect",), ("notify", ASYNC[:8]), ("notify", SYNC_TX[:8]),
                     ("write", SYNC_RX[:8], RIDE_ON, False), ("write", SYNC_RX[:8], POST_HANDSHAKE, False)]
    assert links == [(True, 80)]
    c.cbs[ASYNC](None, bytearray.fromhex("191050"))
    await asyncio.sleep(0.05)
    assert [type(f).__name__ for f in frames] == ["BatteryFrame"]
    assert "left puck 2D7E" in caplog.text
    await link.close()
    task.cancel()


async def test_b3_b4_b5_retry_and_reconnect(caplog):
    caplog.set_level(logging.INFO)
    clock = Clock()
    c1 = FakeClient(None, reply=False)  # F7: no RideOn
    c2 = FakeClient(None)  # connects, then drops
    c3 = FakeClient(None, fail_connect=True)
    c4 = FakeClient(None)
    link, frames, links = make_link(clock, [("L", LEFT)], [c1, c2, c3, c4], zwift_running=lambda: True)
    task = asyncio.create_task(link.run())
    while not links:
        await asyncio.sleep(0.01)
    assert c1.disconnected and links == [(True, 80)]
    c2.dc(c2)  # B4: disconnect callback
    while len(links) < 3:
        await asyncio.sleep(0.01)
    assert links[1] == (False, None) and links[2] == (True, 80)
    assert c4.log[-1] == ("write", SYNC_RX[:8], POST_HANDSHAKE, False)
    # attempt delays: after F7 failure 1; after drop 1; after failed connect 2
    assert clock.delays.count(1) >= 2 and 2 in clock.delays
    assert "Zwift is running" in caplog.text  # F4
    await link.close()
    task.cancel()


async def test_b7_bluetooth_unavailable():
    for msg in ("Bluetooth device is turned off", "not authorized to use Bluetooth"):
        clock = Clock()
        link = ClickLink(None, None, scanner_factory=fake_scanner([], err=RuntimeError(msg)),
                         client_factory=object, clock=clock, sleep=clock.sleep)
        with pytest.raises(BluetoothUnavailable):
            await link.run()


async def test_b8_capture_roundtrip(tmp_path):
    clock = Clock()
    cap = CaptureWriter(tmp_path / "c.jsonl")
    c = FakeClient(None)
    link, frames, links = make_link(clock, [("L-ADDR", LEFT)], [c], capture=cap)
    task = asyncio.create_task(link.run())
    while not links:
        await asyncio.sleep(0.01)
    c.cbs[ASYNC](None, bytearray.fromhex("191050"))
    await link.close()
    task.cancel()
    cap.close()
    rows = list(read_capture(tmp_path / "c.jsonl"))
    evs = [(r["ev"], r.get("ch"), r.get("hex")) for r in rows]
    assert ("tx", "00000003", RIDE_ON.hex()) in evs
    assert ("rx", "00000004", RIDE_ON.hex()) in evs
    assert ("rx", "00000002", "191050") in evs
    assert rows[0]["ev"] == "adv" and rows[0]["dev"] == "L-AD"


async def test_b9_reset_on_device_stopped(caplog):
    """A unit that says it stopped talking gets one RESET 0x18 per cooldown."""
    caplog.set_level(logging.INFO)
    clock = Clock()
    c = FakeClient(None)
    link, frames, links = make_link(clock, [("L", LEFT)], [c])
    task = asyncio.create_task(link.run())
    while not links:
        await asyncio.sleep(0.01)
    c.cbs[ASYNC](None, bytearray.fromhex("ff0500ea05190a0c44383532464141"))
    await asyncio.sleep(0.02)
    assert ("write", SYNC_RX[:8], RESET, False) in c.log
    assert "sent RESET" in caplog.text
    # a second stopped frame inside the cooldown must not add a RESET
    c.cbs[ASYNC](None, bytearray.fromhex("ff0500fa05120a0c44383532464141"))
    await asyncio.sleep(0.02)
    assert [e for e in c.log if e[:3] == ("write", SYNC_RX[:8], RESET)] == [
        ("write", SYNC_RX[:8], RESET, False)]
    # and the stopped frame is still delivered as a vendor frame, not swallowed
    assert any(type(f).__name__ == "VendorFrame" for f in frames)
    await link.close()
    task.cancel()
