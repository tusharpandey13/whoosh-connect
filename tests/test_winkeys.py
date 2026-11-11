"""#13: Windows SendInput sink - decisions tested by injection; the FFI itself is unverified."""
import ctypes

from whoosh_connect import keyboard
from whoosh_connect.keyboard import KeyboardSink
from whoosh_connect.mapping import Action
from whoosh_connect.winkeys import (INPUT, INPUT_KEYBOARD, KEYEVENTF_KEYUP, WindowsKeyboardSink,
                                    key_input, vk)

UP = Action("SHIFT_UP", 0, key="k", hold=False)
STEER = Action("STEER_LEFT", 0, key="a", hold=True)
NOKEY = Action("UTURN", 0, key=None)
BADKEY = Action("ODD", 0, key="F1")


def sink(front="MyWhoosh.exe"):
    sent = []
    s = WindowsKeyboardSink(send_input=lambda code, up: sent.append((code, up)) or True,
                            foreground=lambda: front)
    return s, sent


async def test_tap_is_down_then_up_on_press_only():
    s, sent = sink()
    await s.send([(UP, True), (UP, False)])
    assert sent == [(ord("K"), False), (ord("K"), True)]


async def test_hold_maps_down_and_up():
    s, sent = sink()
    await s.send([(STEER, True)])
    await s.send([(STEER, False)])
    assert sent == [(ord("A"), False), (ord("A"), True)]


async def test_refuses_to_type_when_mywhoosh_not_foreground():
    s, sent = sink(front="notepad.exe")
    await s.send([(UP, True), (STEER, True)])
    assert sent == []


async def test_never_skips_a_key_up_even_after_focus_moves():
    front = ["MyWhoosh.exe"]
    sent = []
    s = WindowsKeyboardSink(send_input=lambda c, up: sent.append((c, up)), foreground=lambda: front[0])
    await s.send([(STEER, True)])
    front[0] = "explorer.exe"
    await s.send([(STEER, False)])
    assert sent == [(ord("A"), False), (ord("A"), True)]


async def test_unmapped_keys_are_ignored():
    s, sent = sink()
    await s.send([(NOKEY, True), (BADKEY, True)])
    assert sent == []


def test_vk_codes():
    assert [vk(c) for c in "az09"] == [0x41, 0x5A, 0x30, 0x39]


def test_input_struct_has_win64_layout():
    if ctypes.sizeof(ctypes.c_void_p) != 8:
        return
    assert ctypes.sizeof(INPUT) == 40  # what SendInput's cbSize must be on x64
    e = key_input(0x4B, up=True, scan=0x25)
    assert e.type == INPUT_KEYBOARD and e.ki.wVk == 0x4B and e.ki.wScan == 0x25
    assert e.ki.dwFlags == KEYEVENTF_KEYUP and key_input(0x4B, up=False).ki.dwFlags == 0


def test_platform_pick_keeps_macos_path():
    assert type(keyboard.keyboard_sink("darwin")) is KeyboardSink
    assert type(keyboard.keyboard_sink("win32")) is WindowsKeyboardSink


def test_constructing_the_windows_sink_off_windows_touches_no_ffi():
    WindowsKeyboardSink()  # user32 is only loaded on first send
