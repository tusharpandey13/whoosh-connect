"""Start at login (#3): a per-user LaunchAgent for the bundled app.

Same job as ``scripts/install_launchd.sh`` (same label, throttle, ``KeepAlive``
``SuccessfulExit=false`` and ``ProcessType=Interactive``), but pointing at the app's own
binary instead of a repo venv, and toggled from the menu instead of a make target.

Turning it on or off only writes or removes the plist; launchd reads it at the next
login. We deliberately do not ``launchctl bootstrap``/``bootout`` from inside the app:
bootstrap would start a second copy next to the one the rider is clicking in, and
bootout would kill that copy when it was itself started by launchd.
"""
from __future__ import annotations

import plistlib
import sys
from pathlib import Path

from . import state

LABEL = "com.tusharpandey13.whoosh-connect"
THROTTLE_S = 10
TRANSLOCATED = "/AppTranslocation/"


class NotMovable(Exception):
    """The app runs from a translocated (quarantined, read-only) copy; its path won't survive."""


def program_args(executable: str | None = None, frozen: bool | None = None) -> list[str]:
    """What launchd should run: the .app's own binary, or ``python -m whoosh_connect`` in dev."""
    exe = executable or sys.executable
    frozen = getattr(sys, "frozen", False) if frozen is None else frozen
    return [exe, "app"] if frozen else [exe, "-m", "whoosh_connect", "app"]


def plist(args: list[str], log: Path) -> dict:
    return {
        "Label": LABEL,
        "ProgramArguments": args,
        "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False},  # Quit (exit 0) stays quit; a crash restarts
        "ThrottleInterval": THROTTLE_S,
        "StandardOutPath": str(log),
        "StandardErrorPath": str(log),  # catches anything before Python opens the log itself
        "ProcessType": "Interactive",
    }


class LoginItem:
    def __init__(self, home=Path.home, args=program_args, log_path=state.default_log_path):
        self.home, self.args, self.log_path = home, args, log_path

    @property
    def path(self) -> Path:
        return self.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"

    def available(self) -> bool:
        return TRANSLOCATED not in self.args()[0]

    def enabled(self) -> bool:
        return self.path.exists()

    def refresh(self) -> bool:
        """If the app moved since Start at login was ticked, point the agent at the new path.

        Otherwise the menu would show it ticked while launchd fails at every login.
        """
        if not self.enabled() or not self.available():
            return False
        try:
            current = plistlib.loads(self.path.read_bytes()).get("ProgramArguments")
        except Exception:  # noqa: BLE001 - unreadable plist: rewrite it
            current = None
        if current == self.args():
            return False
        self.set(True)
        return True

    def render(self) -> bytes:
        return plistlib.dumps(plist(self.args(), self.log_path()))

    def set(self, on: bool) -> None:
        if not on:
            self.path.unlink(missing_ok=True)
            return
        if not self.available():
            raise NotMovable(self.args()[0])
        self.log_path().parent.mkdir(parents=True, exist_ok=True)  # launchd won't create it
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_bytes(self.render())
        tmp.replace(self.path)
