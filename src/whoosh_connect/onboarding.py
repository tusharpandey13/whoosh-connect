"""First-run walkthrough (#8): four steps that advance on their own as each is detected.

Driven by the same ``Status`` snapshots as the menu, so there is no Next button:
Zwift quit -> shifter found (Waiting) -> MyWhoosh attached (Riding). The fourth step
(the press window) can't be detected in advance; it is taught once, on the first ride.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from . import messages, state
from .status import Phase, Status

log = logging.getLogger(__name__)
TOTAL = 4
STEPS = {
    1: "Quit Zwift if it's open - it holds the shifters.",
    2: "Press any button on a shifter to wake it.",
    3: "In MyWhoosh: connection screen > OpenBikeControl > pick whoosh-connect.",
    4: "If the gears ever stop answering, press a shifter button once.",
}


def step_for(status: Status) -> int | None:
    """Which step this status asks of the rider; None while some other error is showing."""
    if status.error is messages.ZWIFT:
        return 1
    if status.phase is Phase.ERROR:
        return None
    return {Phase.SEARCHING: 2, Phase.WAITING: 3, Phase.RIDING: 4}[status.phase]


class OnboardingStore:
    """``app.json`` next to the config. Not ``state.json``: ``record_unlock`` rewrites that whole."""

    def __init__(self, path: Path | None = None):
        self.path = path or state.config_dir() / "app.json"

    def _read(self) -> dict:
        try:
            data = json.loads(self.path.read_text())
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def done(self) -> bool:
        return self._read().get("onboarded") is True

    def mark_done(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps({**self._read(), "onboarded": True}))
            tmp.replace(self.path)
        except OSError as e:  # worst case the rider sees the steps again next launch
            log.warning("could not save setup progress: %r", e)


class Onboarding:
    """Call ``update`` with every status. Steps only move forward; one notice per step."""

    def __init__(self, notify, show, store: OnboardingStore | None = None):
        self.notify, self.show = notify, show       # notify(text, title); show(line | None)
        self.store = store or OnboardingStore()
        self.active = not self.store.done()
        self.step = 0

    @staticmethod
    def line(step: int) -> str:
        return f"Setup: step {step} of {TOTAL} - {STEPS[step]}"

    def update(self, status: Status) -> None:
        if not self.active:
            return
        step = step_for(status)
        if step is None or step <= self.step:
            return
        self.step = step
        self.show(self.line(step))
        self.notify(STEPS[step], f"whoosh-connect setup ({step} of {TOTAL})")
        if step == TOTAL:
            self.store.mark_done()  # the last line stays up for this session only
            self.active = False
