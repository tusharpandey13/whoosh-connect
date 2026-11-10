"""Unlock timestamp store, Zwift process detection, unlock flow."""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import time
from datetime import datetime
from pathlib import Path

ZWIFT_NAMES = {"ZwiftApp", "Zwift", "ZwiftLauncher"}
UNLOCK_VALID_S = 24 * 3600
MIN_RUN_S = 20


def default_state_path() -> Path:
    return Path.home() / ".config" / "whoosh-connect" / "state.json"


class StateStore:
    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else default_state_path()

    def record_unlock(self, ts: float) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"unlock": ts}))

    def last_unlock(self) -> float | None:
        try:
            return float(json.loads(self.path.read_text())["unlock"])
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def unlock_age(self, now: float) -> float | None:
        t = self.last_unlock()
        return None if t is None else now - t


def _ps_names() -> set[str]:
    out = subprocess.run(["ps", "-axo", "comm="], capture_output=True, text=True, timeout=5).stdout
    return {os.path.basename(line.strip()) for line in out.splitlines()}


def zwift_running(ps=_ps_names) -> bool:
    """ps: callable returning the set of running process names (injectable)."""
    return bool(ZWIFT_NAMES & set(ps()))


def _hhmm(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%H:%M")


CHECKLIST = """Unlock the Clicks via Zwift:
  1. Wake the trainer and open Zwift.
  2. Pair the trainer first, then the Clicks; keep them connected ~30 s and press a few buttons.
  3. Quit Zwift with Cmd-Q.
Waiting for Zwift to start (Ctrl-C to abort)..."""


async def unlock_flow(store: StateStore, ps=zwift_running, clock=time.time,
                      sleep=asyncio.sleep, say=print) -> bool:
    say(CHECKLIST)
    while not ps():
        await sleep(2)
    start = clock()
    say("Zwift detected; waiting for it to exit...")
    while ps():
        await sleep(2)
    now = clock()
    if now - start < MIN_RUN_S:
        say(f"Warning: Zwift ran only {now - start:.0f} s (< {MIN_RUN_S} s); unlock may not have happened, not recorded.")
        return False
    store.record_unlock(now)
    say(f"Unlocked at {_hhmm(now)}; valid until about {_hhmm(now + UNLOCK_VALID_S)} tomorrow")
    return True
