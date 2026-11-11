"""Windows key injection via SendInput (fallback sink, #13). No new dependency: ctypes.

Same decisions as the macOS ``KeyboardSink``: only [a-z0-9] keys, taps fire on press,
a key-up is never skipped, and nothing is typed unless MyWhoosh is the foreground app.
The FFI is behind ``send_input=`` / ``foreground=`` seams; only the decisions and the
struct layout are tested off Windows.
"""
from __future__ import annotations

import ctypes
import logging
import re

log = logging.getLogger(__name__)

INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
MAPVK_VK_TO_VSC = 0

# Fixed-width types so the layout is Win64's on any host (ctypes.wintypes' DWORD is 8 bytes on macOS).
WORD, DWORD, LONG, ULONG_PTR = ctypes.c_uint16, ctypes.c_uint32, ctypes.c_int32, ctypes.c_size_t


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", WORD), ("wScan", WORD), ("dwFlags", DWORD), ("time", DWORD),
                ("dwExtraInfo", ULONG_PTR)]


class MOUSEINPUT(ctypes.Structure):  # only here so the union has Windows' size
    _fields_ = [("dx", LONG), ("dy", LONG), ("mouseData", DWORD), ("dwFlags", DWORD),
                ("time", DWORD), ("dwExtraInfo", ULONG_PTR)]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", DWORD), ("wParamL", WORD), ("wParamH", WORD)]


class _U(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", DWORD), ("u", _U)]


def vk(key: str) -> int:
    """Virtual-key code for one [a-z0-9] character (VK_A..VK_Z = 'A'..'Z', VK_0..VK_9 = '0'..'9')."""
    return ord(key.upper())


def key_input(code: int, up: bool, scan: int = 0) -> INPUT:
    return INPUT(type=INPUT_KEYBOARD,
                 ki=KEYBDINPUT(wVk=code, wScan=scan, dwFlags=KEYEVENTF_KEYUP if up else 0))


def _user32():
    return ctypes.WinDLL("user32", use_last_error=True)  # type: ignore[attr-defined]


def inputs(events: list[tuple[int, bool]], scan=lambda code: 0):
    """One INPUT array for ``[(vk, up), ...]``: a tap goes to Windows as a single call."""
    return (INPUT * len(events))(*(key_input(code, up, scan(code)) for code, up in events))


def _send_input(events: list[tuple[int, bool]]) -> bool:
    u = _user32()
    arr = inputs(events, scan=lambda code: u.MapVirtualKeyW(code, MAPVK_VK_TO_VSC))
    sent = u.SendInput(len(events), arr, ctypes.sizeof(INPUT))
    if sent != len(events):  # e.g. UIPI: MyWhoosh running elevated
        log.warning("SendInput refused (error %d)", ctypes.get_last_error())
    return sent == len(events)


def _foreground() -> str:
    import psutil

    u = _user32()
    pid = DWORD()
    u.GetWindowThreadProcessId(u.GetForegroundWindow(), ctypes.byref(pid))
    try:
        return psutil.Process(pid.value).name()
    except psutil.Error:
        return ""


class WindowsKeyboardSink:
    def __init__(self, send_input=_send_input, foreground=_foreground):
        self._send, self._foreground = send_input, foreground

    async def send(self, changes):
        for a, down in changes:
            if not a.key or not re.fullmatch(r"[a-z0-9]", a.key):
                log.debug("no keyboard key for %s", a.name)
                continue
            if not a.hold and not down:
                continue  # taps fire on press only
            # never skip a key-up: that would leave the key stuck down
            if down and "mywhoosh" not in self._foreground().lower():
                log.warning("MyWhoosh not frontmost; dropped %s", a.name)
                continue
            code = vk(a.key)
            if a.hold:
                self._send([(code, not down)])
            else:  # a tap is a full press, down+up in one SendInput so it can't be split
                self._send([(code, False), (code, True)])

    async def status(self, battery, connected):
        pass

    async def close(self):
        pass
