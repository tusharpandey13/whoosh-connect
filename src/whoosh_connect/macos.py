"""macOS-only helpers for the app. Every OS call goes through an injectable seam."""
from __future__ import annotations

import logging
import subprocess

log = logging.getLogger(__name__)

# CBManagerAuthorization values (CoreBluetooth, macOS 10.15+)
_AUTH = {0: "not_determined", 1: "restricted", 2: "denied", 3: "allowed"}


def bt_authorization(cb=None) -> str:
    """This process's Bluetooth permission, read without triggering the system prompt.

    Returns allowed | denied | restricted | not_determined | unknown.
    """
    try:
        if cb is None:
            import CoreBluetooth as cb  # pyobjc, pulled in by bleak on macOS
        return _AUTH.get(int(cb.CBManager.authorization()), "unknown")
    except Exception as e:  # noqa: BLE001 - diagnostics must never crash the app
        log.debug("bluetooth authorization unreadable: %r", e)
        return "unknown"


def bt_denied(cb=None) -> bool:
    return bt_authorization(cb) in ("denied", "restricted")


_NOTIFY = ["-e", "on run argv", "-e",
           "display notification (item 1 of argv) with title (item 2 of argv)", "-e", "end run"]


def notify(text: str, title: str = "whoosh-connect", runner=subprocess.Popen) -> None:
    """A system notification. Text goes in as an argument, never spliced into the script."""
    try:
        runner(["osascript", *_NOTIFY, text, title],
               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:  # noqa: BLE001 - a missed notification must not stop the bridge
        log.debug("notification failed: %r", e)
