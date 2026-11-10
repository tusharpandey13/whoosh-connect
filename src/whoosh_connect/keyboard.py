"""macOS key injection via osascript (fallback sink)."""
from __future__ import annotations

import logging
import re
import subprocess

log = logging.getLogger(__name__)
_FRONT = 'tell application "System Events" to get name of first application process whose frontmost is true'


class KeyboardSink:
    def __init__(self, runner=subprocess.run, frontmost=None):
        self._run = runner
        self._frontmost = frontmost or self._osa_frontmost

    def _osa(self, script: str):
        return self._run(["osascript", "-e", script], capture_output=True, text=True, timeout=5)

    def _osa_frontmost(self) -> str:
        return (self._osa(_FRONT).stdout or "").strip()

    async def send(self, changes):
        for a, down in changes:
            if not a.key or not re.fullmatch(r"[a-z0-9]", a.key):
                log.debug("no keyboard key for %s", a.name)
                continue
            if not a.hold and not down:
                continue  # taps fire on press only
            # never skip a key-up: that would leave the key stuck down
            if down and "mywhoosh" not in self._frontmost().lower():
                log.warning("MyWhoosh not frontmost; dropped %s", a.name)
                continue
            verb = "keystroke" if not a.hold else ("key down" if down else "key up")
            self._osa(f'tell application "System Events" to {verb} "{a.key}"')

    async def status(self, battery, connected):
        pass

    async def close(self):
        pass
