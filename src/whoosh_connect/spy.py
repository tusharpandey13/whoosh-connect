"""Passive Click listener: connect, subscribe, never write.

Used by `whoosh-connect unlock --capture` to record what the *real* Zwift app
puts on the air, including the `ff 03` unlock challenge. It deliberately sends
nothing, so Zwift stays the one that does the handshake (and the answering).
"""
from __future__ import annotations

import asyncio
import logging

from .zwift import ASYNC, MANUFACTURER_ID, SYNC_TX

log = logging.getLogger(__name__)
WAKE_HINT = "Press a Click button to wake it (the spy needs the puck to advertise)"


class Spy:
    def __init__(self, on_frame=None, capture=None, scanner_factory=None, client_factory=None,
                 sleep=asyncio.sleep, scan_s=20.0, reconnect_s=5.0):
        if scanner_factory is None or client_factory is None:
            from bleak import BleakClient, BleakScanner
            scanner_factory = scanner_factory or BleakScanner
            client_factory = client_factory or BleakClient
        self.on_frame = on_frame
        self.capture = capture
        self.scanner_factory, self.client_factory = scanner_factory, client_factory
        self.sleep, self.scan_s, self.reconnect_s = sleep, scan_s, reconnect_s
        self._closed = False
        self._clients: list = []

    def _cap(self, ev, dev, **kw):
        if self.capture:
            self.capture.write(ev, dev=dev[:4], **kw)

    async def _scan(self) -> dict:
        found: dict[str, tuple] = {}

        def cb(device, adv):
            mfr = adv.manufacturer_data.get(MANUFACTURER_ID)
            if mfr and device.address not in found:
                found[device.address] = (device, mfr.hex())
                self._cap("adv", device.address, addr=device.address, rssi=adv.rssi, mfr=mfr.hex())

        async with self.scanner_factory(cb):
            await self.sleep(self.scan_s)
        return found

    async def _session(self, device, mfr: str) -> None:
        tag = device.address
        client = self.client_factory(device)
        self._clients.append(client)

        def on_notify(ch):
            def cb(_c, data):
                data = bytes(data)
                self._cap("rx", tag, ch=ch[:8], hex=data.hex())
                if self.on_frame:
                    self.on_frame(tag, data)
            return cb

        try:
            await client.connect()
            for ch in (ASYNC, SYNC_TX):
                await client.start_notify(ch, on_notify(ch))
                self._cap("sub", tag, ch=ch[:8])
            try:
                self._cap("read", tag, ch="00000004",
                          hex=bytes(await client.read_gatt_char(SYNC_TX)).hex())
            except Exception as e:
                self._cap("readerr", tag, err=str(e))
            self._cap("link", tag, up=True)
            log.info("spy attached to %s (%s); it stays silent unless Zwift talks first",
                     tag[:4].upper(), mfr)
            while not self._closed:
                await self.sleep(0.5)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.debug("spy session %s ended: %s", tag[:4], e)
        finally:
            self._cap("link", tag, up=False)
            try:
                await client.disconnect()
            except Exception:
                pass
            if client in self._clients:
                self._clients.remove(client)

    async def run(self) -> None:
        while not self._closed:
            try:
                found = await self._scan()
            except Exception as e:
                log.warning("spy scan failed: %s", e)
                found = {}
            if not found:
                log.info(WAKE_HINT)
                await self.sleep(1.0)
                continue
            await asyncio.gather(*(self._session(d, m) for d, m in found.values()),
                                 return_exceptions=True)
            if not self._closed:
                await self.sleep(self.reconnect_s)

    async def close(self) -> None:
        self._closed = True
        for c in list(self._clients):
            try:
                await c.disconnect()
            except Exception:
                pass
