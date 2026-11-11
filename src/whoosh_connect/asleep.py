"""Pure: notice when the Click's press window closes ("gears asleep", #9).

Roughly 50 s after the press that woke it, a locked Click v2 sends ``ff 05 00 ea/fa 05``
and stops reporting until the next press (docs/hardware.md). This only *watches*
frames that ``ble.py`` already delivers; it sends nothing to the unit.
"""
from __future__ import annotations

import enum
import time

from .zwift import STOPPED_PREFIXES, ButtonsFrame, VendorFrame

# A stopped frame this soon after (re)connecting is the previous boot's leftovers;
# ble.py ignores it for its reset path for the same reason (ClickLink.reset_settle).
SETTLE_S = 3.0
# A press this recent means the rider is mid-window and will press again anyway.
ACTIVE_S = 10.0


class Event(enum.Enum):
    ASLEEP = "asleep"
    AWAKE = "awake"


class SleepWatch:
    def __init__(self, clock=time.monotonic, settle_s=SETTLE_S, active_s=ACTIVE_S):
        self.clock, self.settle_s, self.active_s = clock, settle_s, active_s
        self.asleep = False
        self._armed = False           # one ASLEEP per window
        self._link_at = -1e9
        self._last_press = -1e9

    def on_link(self, up: bool) -> Event | None:
        was = self.asleep
        self.asleep, self._armed = False, up
        self._link_at = self.clock()
        return Event.AWAKE if was else None

    def on_frame(self, frame) -> Event | None:
        now = self.clock()
        if isinstance(frame, ButtonsFrame):
            self._last_press, self._armed = now, True
            if self.asleep:
                self.asleep = False
                return Event.AWAKE
            return None
        if not (isinstance(frame, VendorFrame) and frame.raw.startswith(STOPPED_PREFIXES)):
            return None
        if not self._armed or now - self._link_at < self.settle_s:
            return None
        if now - self._last_press < self.active_s:  # mid-window: stay quiet
            return None
        self._armed, self.asleep = False, True
        return Event.ASLEEP
