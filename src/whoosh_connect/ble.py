"""BLE link to a Zwift Click v2: select, connect, handshake, reconnect."""
from __future__ import annotations

import asyncio
import logging
import time

from . import state
from .zwift import (ASYNC, BATTERY_CHAR, POST_HANDSHAKE, RESET, RIDE_ON, STOPPED_PREFIXES,
                    SYNC_RX, SYNC_TX, RideOnFrame, parse_adv, parse_frame)

log = logging.getLogger(__name__)
WAKE_HINT = "Press a Click button to wake it"
_BT_ERRORS = ("turned off", "powered off", "unauthori", "not authori", "permission")


class BluetoothUnavailable(Exception):
    pass


class HandshakeTimeout(Exception):
    pass


def _map_error(e: Exception) -> Exception:
    if any(k in str(e).lower() for k in _BT_ERRORS):
        return BluetoothUnavailable(
            "Bluetooth unavailable or permission denied: turn Bluetooth on and enable your terminal "
            "in System Settings -> Privacy & Security -> Bluetooth")
    return e


class ClickLink:
    def __init__(self, on_frame, on_link, scanner_factory=None, client_factory=None,
                 clock=time.monotonic, sleep=asyncio.sleep, zwift_running=state.zwift_running,
                 capture=None, handshake_timeout=5.0, pair_wait=10.0, reset_cooldown=2.0):
        if scanner_factory is None or client_factory is None:
            from bleak import BleakClient, BleakScanner
            scanner_factory = scanner_factory or BleakScanner
            client_factory = client_factory or BleakClient
        self.on_frame, self.on_link = on_frame, on_link
        self.scanner_factory, self.client_factory = scanner_factory, client_factory
        self.clock, self.sleep, self.zwift_running = clock, sleep, zwift_running
        self.capture, self.handshake_timeout, self.pair_wait = capture, handshake_timeout, pair_wait
        self.reset_cooldown = reset_cooldown
        self._closed = False
        self._client = None

    def _cap(self, ev, dev, **kw):
        if self.capture:
            self.capture.write(ev, dev=dev[:4], **kw)

    async def select(self):
        """Return (device, PuckInfo): the left puck, or the right one after pair_wait (F1)."""
        found: dict[str, tuple] = {}
        first = None

        def cb(device, adv):
            nonlocal first
            info = parse_adv(adv.manufacturer_data)
            if info and device.address not in found:
                found[device.address] = (device, info)
                first = first if first is not None else self.clock()
                self._cap("adv", device.address, addr=device.address, rssi=adv.rssi,
                          mfr=adv.manufacturer_data[0x094A].hex())

        log.info(WAKE_HINT)
        last_hint = self.clock()
        try:
            async with self.scanner_factory(cb):
                while not self._closed:
                    left = [v for v in found.values() if v[1].side == "left"]
                    if left:
                        return left[0]
                    if found and self.clock() - first >= self.pair_wait:
                        log.warning("right puck only: left-side buttons unavailable")
                        return next(iter(found.values()))
                    if not found and self.clock() - last_hint >= 30:
                        last_hint = self.clock()
                        log.info(WAKE_HINT)
                    await self.sleep(0.5)
        except Exception as e:
            raise _map_error(e) from e
        raise asyncio.CancelledError

    async def _reset(self, client, tag) -> None:
        """Restart a unit that announced it stopped talking (clickbridge's 0x18).

        A Click goes quiet roughly a minute after waking, or shortly after it has
        asked for an unlock it never got. `0x18` brings the button engine back for
        another window, which is enough to keep a ride alive without an unlock.
        """
        try:
            await client.write_gatt_char(SYNC_RX, RESET, response=False)
            self._cap("tx", tag, ch=SYNC_RX[:8], hex=RESET.hex())
            log.info("Click stopped talking; sent RESET 0x18 for another window")
        except Exception as e:
            log.debug("RESET failed: %r", e)

    async def _session(self, device, info) -> None:
        tag = device.address
        queue: asyncio.Queue = asyncio.Queue()
        ack, gone = asyncio.Event(), asyncio.Event()
        last_reset = [-1e9]
        resets: set = set()

        def on_notify(char):
            def cb(_, data):
                data = bytes(data)
                self._cap("rx", tag, ch=char[:8], hex=data.hex())
                if data.startswith(STOPPED_PREFIXES) and self._client is client:
                    now = self.clock()
                    if now - last_reset[0] >= self.reset_cooldown:
                        last_reset[0] = now
                        task = asyncio.create_task(self._reset(client, tag))
                        resets.add(task)
                        task.add_done_callback(resets.discard)
                frame = parse_frame(data)
                if isinstance(frame, RideOnFrame):
                    ack.set()
                else:
                    queue.put_nowait(frame)
            return cb

        client = self._client = self.client_factory(device, disconnected_callback=lambda _c: gone.set())
        pump = None
        try:
            try:
                await client.connect()
                await client.start_notify(ASYNC, on_notify(ASYNC))
                await client.start_notify(SYNC_TX, on_notify(SYNC_TX))
                await client.write_gatt_char(SYNC_RX, RIDE_ON, response=False)
                self._cap("tx", tag, ch=SYNC_RX[:8], hex=RIDE_ON.hex())
                try:
                    await asyncio.wait_for(ack.wait(), self.handshake_timeout)
                except asyncio.TimeoutError:
                    raise HandshakeTimeout("no RideOn reply") from None
                await client.write_gatt_char(SYNC_RX, POST_HANDSHAKE, response=False)
                self._cap("tx", tag, ch=SYNC_RX[:8], hex=POST_HANDSHAKE.hex())
            except (HandshakeTimeout, asyncio.CancelledError):
                raise
            except Exception as e:
                raise _map_error(e) from e
            battery = None
            try:
                battery = (await client.read_gatt_char(BATTERY_CHAR))[0]
            except Exception:
                log.debug("battery read failed")

            async def run_pump():
                while True:
                    await self.on_frame(await queue.get())

            pump = asyncio.create_task(run_pump())
            self._cap("link", tag, up=True)
            await self.on_link(True, battery)
            log.info("Clicks connected (%s puck %s, battery %s). Press right shift to test",
                     info.side, info.id.upper(), f"{battery} %" if battery is not None else "unknown")
            self._ok = True
            await gone.wait()
        finally:
            if pump:
                pump.cancel()
            for t in list(resets):
                t.cancel()
            try:
                await client.disconnect()
            except Exception:
                pass

    async def run(self) -> None:
        fails = 0
        first = True
        while not self._closed:
            if not first:
                if self.zwift_running():
                    log.warning("Zwift is running and will grab the Clicks; quit it (Cmd-Q)")
                await self.sleep(min(2 ** fails, 10))
                fails += 1
            first = False
            self._ok = False
            try:
                device, info = await self.select()
                await self._session(device, info)
                log.warning("Clicks disconnected")
            except BluetoothUnavailable:
                raise
            except HandshakeTimeout:
                log.warning("handshake timed out; retrying")
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning("BLE error: %s", e)
            if self._ok:
                fails = 0
                self._cap("link", "link", up=False)
                await self.on_link(False, None)

    async def close(self) -> None:
        self._closed = True
        if self._client:
            try:
                await self._client.disconnect()
            except Exception:
                pass
