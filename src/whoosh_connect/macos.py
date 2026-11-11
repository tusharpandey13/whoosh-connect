"""macOS-only helpers for the app. Every OS call goes through an injectable seam."""
from __future__ import annotations

import logging

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
