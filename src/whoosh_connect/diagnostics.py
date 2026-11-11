"""Copy diagnostics (#10): one redacted text bundle for a support message.

Same discipline as ``dlock``: a bearer token never leaves the process. Puck
addresses are cut to their last four characters, as the logs already show them.
"""
from __future__ import annotations

import collections
import platform
import re
import subprocess
import sys
import time

from . import state

LOG_LINES = 500
CUT = "[redacted]"

_SECRETS = [
    # Authorization headers, --token=..., "access_token": "...", token=..., JWTs
    (re.compile(r"(?i)(bearer\s+)\S+"), r"\1" + CUT),
    (re.compile(r"(?i)(--token[=\s]+)(\{.*?\}|\S+)"), r"\1" + CUT),
    (re.compile(r'(?i)("?(?:access|refresh|id)?_?token"?\s*[:=]\s*)("[^"]*"|\S+)'), r"\1" + CUT),
    (re.compile(r"\beyJ[\w-]+\.[\w-]+\.[\w-]+"), CUT),
]
_UUID = re.compile(r"\b[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{8}([0-9A-Fa-f]{4})\b")
_MAC = re.compile(r"\b(?:[0-9A-Fa-f]{2}[:-]){4}([0-9A-Fa-f]{2})[:-]([0-9A-Fa-f]{2})\b")


def redact(text: str) -> str:
    for rx, repl in _SECRETS:
        text = rx.sub(repl, text)
    text = _UUID.sub(lambda m: f"…{m.group(1).upper()}", text)  # CoreBluetooth device ids
    return _MAC.sub(lambda m: f"…{(m.group(1) + m.group(2)).upper()}", text)


def tail(path, n: int = LOG_LINES) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return "".join(collections.deque(f, n))
    except OSError as e:
        return f"(no log: {e.strerror or e})\n"


def ago(seconds: float | None) -> str:
    if seconds is None:
        return "never"
    m = int(seconds // 60)
    return f"{m // 60} h {m % 60} min ago" if m >= 60 else f"{m} min ago"


def report(*, version: str, os_version: str, python: str, bluetooth: str, status: str,
           unlock_age: float | None, zwift_now: bool, zwift_seen: bool, log: str) -> str:
    """Pure: the whole bundle, redacted as one piece so nothing slips past."""
    head = [
        "whoosh-connect diagnostics",
        f"app version:        {version}",
        f"macOS / OS:         {os_version}",
        f"python:             {python}",
        f"bluetooth access:   {bluetooth}",
        f"status:             {status}",
        f"last unlock:        {ago(unlock_age)}",
        f"zwift running now:  {'yes' if zwift_now else 'no'}",
        f"zwift seen:         {'yes' if zwift_seen else 'no'}",
        f"--- last {LOG_LINES} log lines ---",
    ]
    return redact("\n".join(head) + "\n" + log)


def os_version(mac_ver=platform.mac_ver, plat=platform.platform) -> str:
    v = mac_ver()[0]
    return f"macOS {v} ({platform.machine()})" if v else plat()


def collect(status: str, zwift_seen: bool, *, log_path=None, store=None, bt=None, ps=None,
            clock=time.time) -> str:
    from .bundle import version
    from .macos import bt_authorization

    return report(
        version=version(), os_version=os_version(), python=sys.version.split()[0],
        bluetooth=(bt or bt_authorization)(),
        status=status,
        unlock_age=(store or state.StateStore()).unlock_age(clock()),  # read-only
        zwift_now=(ps or state.zwift_running)(),
        zwift_seen=zwift_seen,
        log=tail(log_path or state.default_log_path()),
    )


def copy(text: str, runner=subprocess.run) -> bool:
    try:
        runner(["pbcopy"], input=text.encode(), check=True, timeout=5)
        return True
    except Exception:  # noqa: BLE001 - a support button must never crash the app
        return False
