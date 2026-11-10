"""OpenBikeControl message encode/decode (pure). See PROTOCOL.md / MDNS.md."""
from __future__ import annotations

import logging
from dataclasses import dataclass

log = logging.getLogger(__name__)

SHIFT_UP, SHIFT_DOWN, GEAR_SET = 0x01, 0x02, 0x03
UP, DOWN, LEFT, RIGHT, SELECT, BACK, MENU, HOME = 0x10, 0x11, 0x12, 0x13, 0x14, 0x15, 0x16, 0x17
STEER_LEFT, STEER_RIGHT, CAMERA, HUD = 0x18, 0x19, 0x40, 0x44

NAMES = {
    SHIFT_UP: "SHIFT_UP", SHIFT_DOWN: "SHIFT_DOWN", GEAR_SET: "GEAR_SET",
    UP: "UP", DOWN: "DOWN", LEFT: "LEFT", RIGHT: "RIGHT", SELECT: "SELECT",
    BACK: "BACK", MENU: "MENU", HOME: "HOME", STEER_LEFT: "STEER_LEFT",
    STEER_RIGHT: "STEER_RIGHT", CAMERA: "CAMERA", HUD: "HUD",
}
IDS = {v: k for k, v in NAMES.items()}
SERVICE_UUID = "d273f680-d548-419d-b9d1-fa0472345229"
MSG_BUTTONS, MSG_STATUS, MSG_HAPTIC, MSG_APP_INFO = 0x01, 0x02, 0x03, 0x04
MSG_PING = 0x00  # MyWhoosh sends one every ~4 s; answering with status keeps it happy


def encode_button_state(changes: list[tuple[int, bool]]) -> bytes:
    out = bytearray([MSG_BUTTONS])
    for bid, pressed in changes:
        out += bytes([bid, 1 if pressed else 0])
    return bytes(out)


def encode_device_status(battery: int | None, connected: bool) -> bytes:
    b = 0xFF if battery is None else max(0, min(100, battery))
    return bytes([MSG_STATUS, b, 1 if connected else 0])


@dataclass(frozen=True)
class AppInfo:
    app_id: str
    version: str
    buttons: frozenset[int]  # empty = all supported


def _app_info_len(buf: bytes) -> int | None:
    """Total length of the app-info message at buf[0], or None if incomplete."""
    try:
        i = 3 + buf[2]  # type, version, id_len, id
        i += 1 + buf[i]  # ver_len, ver
        return i + 1 + buf[i]  # count, ids
    except IndexError:
        return None


def parse_app_info(data: bytes) -> AppInfo:
    n = _app_info_len(data)
    if len(data) < 3 or data[0] != MSG_APP_INFO or n is None or len(data) < n:
        raise ValueError("malformed app info")
    idl = data[2]
    app_id = data[3:3 + idl].decode("utf-8", "replace")
    j = 3 + idl
    version = data[j + 1:j + 1 + data[j]].decode("utf-8", "replace")
    j += 1 + data[j]
    return AppInfo(app_id, version, frozenset(data[j + 1:j + 1 + data[j]]))


def split_messages(buffer: bytes) -> tuple[list[bytes], bytes]:
    """Split the app->device TCP stream into messages; returns (messages, rest)."""
    msgs: list[bytes] = []
    while buffer:
        t = buffer[0]
        n = (1 if t == MSG_PING
             else 4 if t == MSG_HAPTIC
             else _app_info_len(buffer) if t == MSG_APP_INFO
             else -1)
        if n == -1:
            log.debug("unknown OBC message type 0x%02x, dropping %s", t, buffer.hex())
            return msgs, b""
        if n is None or len(buffer) < n:
            break
        msgs.append(buffer[:n])
        buffer = buffer[n:]
    return msgs, buffer


def txt_record(name: str, device_id: str) -> dict[str, str]:
    return {
        "version": "1", "id": device_id, "name": name,
        "service-uuids": SERVICE_UUID, "manufacturer": "whoosh-connect",
        "model": "Zwift Click v2 bridge",
    }
