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
    # Authorization: Bearer/Basic ...
    (re.compile(r"(?i)\b(bearer|basic)(\s+)[^\s'\",}]+"), r"\1\2" + CUT),
    # --token=<json or word>, also quoted ('{...}' / "...") as ps shows it
    (re.compile(r"(?i)(--token[=\s]+)('[^']*'|\"[^\"]*\"|\{[^\n]*\}|\S+)"), r"\1" + CUT),
    # key/value: "access_token": "...", 'refresh_token': '...', token=..., id_token:...
    # (an unquoted key must touch its = or :, so prose like "next token = 5" survives)
    (re.compile(r"""(?i)((?:['"](?:\w+_)?token['"]\s*|\b(?:\w+_)?token)[:=]\s*)"""
                r"""('[^']*'|"[^"]*"|[^\s,}]+)"""), r"\1" + CUT),
    # a JWT with no label at all
    (re.compile(r"\beyJ[\w-]+\.[\w-]+\.[\w-]+"), CUT),
]
# CoreBluetooth device ids; standard GATT UUIDs (Bluetooth base UUID) are not identifiers
_UUID = re.compile(r"\b(?![0-9A-Fa-f]{8}-0000-1000-8000-00805[Ff]9[Bb]34[Ff][Bb])[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{8}([0-9A-Fa-f]{4})\b")
_MAC = re.compile(r"\b(?:[0-9A-Fa-f]{2}[:-]){4}([0-9A-Fa-f]{2})[:-]([0-9A-Fa-f]{2})\b")


def redact(text: str) -> str:
    for rx, repl in _SECRETS:
        text = rx.sub(repl, text)
    text = _UUID.sub(lambda m: f"…{m.group(1).upper()}", text)  # CoreBluetooth device ids
    return _MAC.sub(lambda m: f"…{(m.group(1) + m.group(2)).upper()}", text)


TAIL_BYTES = 512 * 1024  # ride.log is never rotated; don't read months of it from a menu click


def tail(path, n: int = LOG_LINES, max_bytes: int = TAIL_BYTES) -> str:
    try:
        with open(path, "rb") as f:
            size = f.seek(0, 2)
            f.seek(max(0, size - max_bytes))
            if size > max_bytes:
                f.readline()  # drop the partial first line
            lines = collections.deque(f, n)
        return b"".join(lines).decode("utf-8", errors="replace")
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
