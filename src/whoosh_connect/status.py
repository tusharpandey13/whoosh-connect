"""Pure: what the menu bar shows. Four phases, derived from the link, the app count and errors."""
from __future__ import annotations

import enum
from dataclasses import dataclass

from .messages import Message


class Phase(enum.Enum):
    SEARCHING = "Searching"
    WAITING = "Waiting for MyWhoosh"
    RIDING = "Riding"
    ERROR = "Error"


HINTS = {
    Phase.SEARCHING: "press a shifter button to wake it",
    Phase.WAITING: "in MyWhoosh pick OpenBikeControl > whoosh-connect",
}


@dataclass(frozen=True)
class Status:
    phase: Phase
    line: str                      # the one line shown at the top of the menu
    error: Message | None = None
    battery: int | None = None


class StatusModel:
    """Fed from the bridge's asyncio thread; calls ``on_change`` only when the status changes."""

    def __init__(self, on_change=lambda status: None):
        self.on_change = on_change
        self.link_up = False
        self.apps = 0
        self.battery: int | None = None
        self.error: Message | None = None
        self._last: Status | None = None

    def snapshot(self) -> Status:
        if self.error:
            return Status(Phase.ERROR, self.error.text, self.error, self.battery)
        if not self.link_up:
            phase = Phase.SEARCHING
        elif not self.apps:
            phase = Phase.WAITING
        else:
            phase = Phase.RIDING
        line = phase.value
        if phase in HINTS:
            line += f" - {HINTS[phase]}"
        if self.link_up and self.battery is not None:
            line += f" (battery {self.battery} %)"
        return Status(phase, line, None, self.battery)

    def _changed(self) -> None:
        s = self.snapshot()
        if s != self._last:
            self._last = s
            self.on_change(s)

    def set_link(self, up: bool, battery: int | None = None) -> None:
        self.link_up = up
        if battery is not None:
            self.battery = battery
        self._changed()

    def set_apps(self, n: int) -> None:
        self.apps = n
        self._changed()

    def set_battery(self, battery: int | None) -> None:
        if battery is not None:
            self.battery = battery
            self._changed()

    def set_error(self, msg: Message | None) -> None:
        self.error = msg
        self._changed()

    def clear_error(self) -> None:
        self.set_error(None)
