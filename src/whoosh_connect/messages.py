"""One plain sentence, with the next action, for every way the bridge can stop (#7).

Rider-facing text never holds an exception name, a traceback or a port number;
the technical detail goes to the log file instead (see ``explain``).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from .ble import BluetoothUnavailable
from .mapping import ConfigError
from .obc_server import AlreadyRunning, PortsBusy

log = logging.getLogger(__name__)

BT_SETTINGS_URL = "x-apple.systempreferences:com.apple.preference.security?Privacy_Bluetooth"


@dataclass(frozen=True)
class Message:
    text: str
    settings_url: str | None = None  # where the "fix it" button should take the rider


BT_OFF = Message("Bluetooth is off. Turn it on in Control Centre.")
BT_DENIED = Message("Bluetooth permission denied. Open System Settings and allow whoosh-connect "
                    "under Privacy & Security > Bluetooth.", BT_SETTINGS_URL)
PORTS = Message("Another app is using the bridge's port. Quit it and try again.")
RUNNING = Message("whoosh-connect is already running - look in the menu bar.")
ZWIFT = Message("Zwift is holding your shifters. Quit Zwift, then carry on.")
CONFIG = Message("Your whoosh-connect settings file has a mistake. Fix or delete config.toml, "
                 "then start again.")
UNKNOWN = Message("Something went wrong. Use Copy diagnostics in the menu and send it to us.")

BY_CODE = {3: BT_OFF, 4: PORTS, 6: RUNNING, 2: CONFIG}  # 5 (dlock) is not rider-facing


def for_error(e: BaseException) -> Message:
    if isinstance(e, BluetoothUnavailable):
        return BT_DENIED if getattr(e, "reason", None) == "denied" else BT_OFF
    if isinstance(e, PortsBusy):
        return PORTS
    if isinstance(e, AlreadyRunning):
        return RUNNING
    if isinstance(e, ConfigError):
        return CONFIG
    return UNKNOWN


def explain(e: BaseException) -> Message:
    """The rider message for ``e``; its technical text goes to the log only."""
    log.info("details: %s: %s", type(e).__name__, e)
    return for_error(e)
