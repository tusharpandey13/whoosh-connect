"""Glue: frames -> tracker -> mapper -> sinks."""
from __future__ import annotations

import logging
import sys
import time
from typing import Protocol

from .mapping import Action, ButtonTracker
from .zwift import BatteryFrame, ButtonsFrame, Frame

log = logging.getLogger(__name__)
LOCKED_AFTER_S = 60
UNLOCK_HINT = ("No button frames 60 s after connecting: the Clicks are probably locked. "
               "Run `whoosh-connect unlock`.")


class Sink(Protocol):
    async def send(self, changes: list[tuple[Action, bool]]) -> None: ...
    async def status(self, battery: int | None, connected: bool) -> None: ...
    async def close(self) -> None: ...


class PrintSink:
    async def send(self, changes):
        for a, down in changes:
            print(f"{a.name:<12} {'down' if down else 'up'}", file=sys.stdout, flush=True)

    async def status(self, battery, connected):
        log.debug("status battery=%s connected=%s", battery, connected)

    async def close(self):
        pass


class Pipeline:
    def __init__(self, map, sinks, clock=time.monotonic):
        self.map, self.sinks, self.clock = map, list(sinks), clock
        self.tracker = ButtonTracker()
        self.battery: int | None = None
        self.connected = False
        self._link_at: float | None = None
        self._saw_buttons = self._hinted = False

    async def _each(self, method: str, *args) -> None:
        for s in self.sinks:
            try:
                await getattr(s, method)(*args)
            except Exception:  # a failing sink must not kill the ride
                log.exception("sink %s.%s failed", type(s).__name__, method)

    async def _emit(self, transitions) -> None:
        mapped = []
        for btn, down in transitions:
            if btn in self.map:
                mapped.append((self.map[btn], down))
            else:
                log.debug("unmapped button %s", btn.name)
        if not mapped:
            return
        for a, down in mapped:
            if down:
                log.info("%s ▲", a.name)
        await self._each("send", mapped)

    async def on_frame(self, frame: Frame) -> None:
        if isinstance(frame, ButtonsFrame):
            self._saw_buttons = True
            await self._emit(self.tracker.update(frame.pressed))
        elif isinstance(frame, BatteryFrame):
            if frame.percent != self.battery:
                self.battery = frame.percent
                await self._each("status", self.battery, self.connected)
        else:
            log.debug("frame %r", frame)

    async def on_link(self, connected: bool, battery: int | None = None) -> None:
        self.connected = connected
        if connected:
            self.battery = battery if battery is not None else self.battery
            self._link_at, self._saw_buttons, self._hinted = self.clock(), False, False
        else:
            self._link_at = None
            await self._emit(self.tracker.release_all())
        await self._each("status", self.battery, connected)

    def tick(self) -> None:
        """Call periodically; logs the unlock hint once per link (F3)."""
        if (self._link_at is not None and not self._saw_buttons and not self._hinted
                and self.clock() - self._link_at >= LOCKED_AFTER_S):
            self._hinted = True
            log.warning(UNLOCK_HINT)

    async def close(self) -> None:
        await self._each("close")
