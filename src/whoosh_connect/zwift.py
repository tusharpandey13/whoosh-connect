"""Zwift Click v2 protocol: constants, frame parsing, button bits (pure)."""
from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum

MANUFACTURER_ID = 0x094A
TYPE_CLICK_V2_LEFT = 0x0B
TYPE_CLICK_V2_RIGHT = 0x0A
SERVICE = "0000fc82-0000-1000-8000-00805f9b34fb"
_SUFFIX = "-19ca-4651-86e5-fa29dcdd09d1"
ASYNC = "00000002" + _SUFFIX  # notify: device -> host
SYNC_RX = "00000003" + _SUFFIX  # host writes here
SYNC_TX = "00000004" + _SUFFIX  # indications
BATTERY_CHAR = "00002a19-0000-1000-8000-00805f9b34fb"
RIDE_ON = b"RideOn"
POST_HANDSHAKE = bytes([0xFF, 0x04, 0x00])


class Button(IntEnum):
    """Bit positions in the active-low button mask."""

    LEFT = 0
    UP = 1
    RIGHT = 2
    DOWN = 3
    A = 4
    B = 5
    Y = 6
    Z = 7
    SHIFT_L = 8
    SHIFT_DN_L = 9
    POWERUP_L = 10
    ONOFF_L = 11
    SHIFT_R = 12
    SHIFT_DN_R = 13
    POWERUP_R = 14
    ONOFF_R = 15


@dataclass(frozen=True)
class ButtonsFrame:
    pressed: frozenset[Button]


@dataclass(frozen=True)
class BatteryFrame:
    percent: int


@dataclass(frozen=True)
class ChallengeFrame:
    raw: bytes


@dataclass(frozen=True)
class VendorFrame:
    raw: bytes


@dataclass(frozen=True)
class LogFrame:
    raw: bytes


@dataclass(frozen=True)
class RideOnFrame:
    pass


@dataclass(frozen=True)
class UnknownFrame:
    raw: bytes


Frame = (ButtonsFrame | BatteryFrame | ChallengeFrame | VendorFrame
         | LogFrame | RideOnFrame | UnknownFrame)


def decode_varint(buf: bytes, i: int = 0) -> tuple[int, int]:
    """LEB128 decode starting at buf[i]; returns (value, next_index)."""
    value = shift = 0
    while True:
        if i >= len(buf):
            raise ValueError("truncated varint")
        b = buf[i]
        value |= (b & 0x7F) << shift
        i += 1
        shift += 7
        if not b & 0x80:
            return value, i
        if shift > 70:
            raise ValueError("varint too long")


def parse_frame(data: bytes) -> Frame:
    """Classify one notification. Never raises (F8)."""
    data = bytes(data)
    try:
        if data.startswith(RIDE_ON):
            return RideOnFrame()
        if len(data) >= 3 and data[0] == 0x23 and data[1] == 0x08:
            mask, _ = decode_varint(data, 2)
            return ButtonsFrame(frozenset(b for b in Button if not (mask >> b) & 1))
        if len(data) >= 3 and data[0] == 0x19 and data[1] == 0x10:
            return BatteryFrame(decode_varint(data, 2)[0])
        if len(data) >= 2 and data[0] == 0xFF:
            return ChallengeFrame(data) if data[1] == 0x03 else VendorFrame(data)
        if data[:1] == b"\x2a":
            return LogFrame(data)
    except ValueError:
        pass
    return UnknownFrame(data)


@dataclass(frozen=True)
class PuckInfo:
    side: str  # "left" | "right" | "unknown"
    id: str  # 4 hex chars, e.g. "2d7e"


def parse_adv(manufacturer_data: dict[int, bytes]) -> PuckInfo | None:
    d = manufacturer_data.get(MANUFACTURER_ID)
    if not d or len(d) < 3:
        return None
    side = {TYPE_CLICK_V2_LEFT: "left", TYPE_CLICK_V2_RIGHT: "right"}.get(d[0], "unknown")
    return PuckInfo(side, bytes(d[1:3]).hex())
