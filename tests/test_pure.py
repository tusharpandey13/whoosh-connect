import pytest

from whoosh_connect import obc
from whoosh_connect.mapping import DEFAULT_MAP, ButtonTracker, ConfigError, load_map
from whoosh_connect.zwift import (BatteryFrame, Button, ButtonsFrame, ChallengeFrame, LogFrame,
                                  RideOnFrame, UnknownFrame, VendorFrame, decode_varint,
                                  parse_adv, parse_frame)

B = Button


def test_z1_varint():
    assert decode_varint(bytes.fromhex("ffffffff0f"))[0] == 0xFFFFFFFF
    assert decode_varint(bytes.fromhex("fbffffff0f"))[0] == 0xFFFFFFFB
    with pytest.raises(ValueError):
        decode_varint(bytes.fromhex("ffff"))


def test_z2_all_released():
    assert parse_frame(bytes.fromhex("2308ffffffff0f")) == ButtonsFrame(frozenset())


@pytest.mark.parametrize("hexs,btns", [
    ("2308fbffffff0f", {B.RIGHT}), ("2308feffffff0f", {B.LEFT}), ("2308bfffffff0f", {B.Y}),
    ("2308fffeffff0f", {B.Z}), ("2308fffdffff0f", {B.SHIFT_L}), ("2308ffdfffff0f", {B.SHIFT_R}),
    ("2308fefeffff0f", {B.LEFT, B.Z})])
def test_z3_buttons(hexs, btns):
    assert parse_frame(bytes.fromhex(hexs)) == ButtonsFrame(frozenset(btns))


def test_z4_z5_other_frames():
    assert parse_frame(bytes.fromhex("191050")) == BatteryFrame(80)
    assert isinstance(parse_frame(bytes.fromhex("ff03000a21aa")), ChallengeFrame)
    assert isinstance(parse_frame(bytes.fromhex("ff0500fa05aa")), VendorFrame)
    assert isinstance(parse_frame(bytes.fromhex("2a0801")), LogFrame)
    assert isinstance(parse_frame(b"RideOn"), RideOnFrame)


@pytest.mark.parametrize("raw", [b"", b"\x23", b"\x23\x08", b"\x23\x08\xff", b"\x99\x01\x02", b"\xff"])
def test_z6_malformed(raw):
    assert isinstance(parse_frame(raw), UnknownFrame)


def test_z7_adv():
    assert parse_adv({0x094A: bytes.fromhex("0b2d7e")}).side == "left"
    assert parse_adv({0x094A: bytes.fromhex("0b2d7e")}).id == "2d7e"
    assert parse_adv({0x094A: bytes.fromhex("0ae89b")}).side == "right"
    assert parse_adv({0x004C: b"\x0b\x2d\x7e"}) is None


def test_o1_o2():
    assert obc.encode_button_state([(1, True)]) == bytes.fromhex("010101")
    assert obc.encode_button_state([(1, True), (2, False)]) == bytes.fromhex("0101010200")
    assert obc.encode_device_status(80, True) == bytes.fromhex("025001")
    assert obc.encode_device_status(None, True) == bytes.fromhex("02ff01")
    assert obc.encode_device_status(80, False)[-1] == 0


EXAMPLE = bytes([4, 1, 5]) + b"zwift" + bytes([6]) + b"1.52.0" + bytes([4, 1, 2, 0x10, 0x14])


def test_o3_app_info():
    i = obc.parse_app_info(EXAMPLE)
    assert (i.app_id, i.version, i.buttons) == ("zwift", "1.52.0", frozenset({1, 2, 0x10, 0x14}))
    zero = bytes([4, 1, 1]) + b"x" + bytes([1]) + b"1" + bytes([0])
    assert obc.parse_app_info(zero).buttons == frozenset()
    with pytest.raises(ValueError):
        obc.parse_app_info(EXAMPLE[:-2])


def test_o4_split():
    msgs, rest = obc.split_messages(EXAMPLE + EXAMPLE)
    assert msgs == [EXAMPLE, EXAMPLE] and rest == b""
    msgs, rest = obc.split_messages(EXAMPLE[:7])
    assert msgs == [] and rest == EXAMPLE[:7]
    msgs, rest = obc.split_messages(rest + EXAMPLE[7:])
    assert msgs == [EXAMPLE] and rest == b""
    assert obc.split_messages(bytes([3, 0, 0, 0]) + EXAMPLE)[0] == [bytes([3, 0, 0, 0]), EXAMPLE]
    assert obc.split_messages(b"\x99\x01\x02") == ([], b"")


def test_o5_txt():
    t = obc.txt_record("whoosh-connect", "abc")
    assert set(t) == {"version", "id", "name", "service-uuids", "manufacturer", "model"}
    assert t["version"] == "1"


def test_m1_default_map():
    exp = {B.SHIFT_R: 1, B.SHIFT_L: 2, B.LEFT: 0x18, B.RIGHT: 0x19, B.UP: 0x10, B.DOWN: 0x11,
           B.A: 0x14, B.B: 0x15, B.Y: 0x40, B.Z: 0x16}
    assert {b: a.obc for b, a in DEFAULT_MAP.items()} == exp
    supported = {1, 2, 3, *range(0x10, 0x1A), 0x40}
    assert all(a.obc in supported for a in DEFAULT_MAP.values())


def test_m2_config(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text('[map]\nA = "HOME"\nZ = "NONE"\n')
    m = load_map(p)
    assert m[B.A].obc == 0x17 and B.Z not in m and m[B.SHIFT_R].obc == 1
    for bad in ('[map]\nQ = "HOME"\n', '[map]\nA = "WAT"\n', "[map\n"):
        p.write_text(bad)
        with pytest.raises(ConfigError):
            load_map(p)
    with pytest.raises(ConfigError):
        load_map(tmp_path / "missing.toml")


def test_m3_tracker():
    t = ButtonTracker()
    assert t.update({B.A}) == [(B.A, True)]
    assert t.update({B.A}) == []
    assert t.update({B.A, B.SHIFT_R}) == [(B.SHIFT_R, True)]
    assert t.update(set()) == [(B.A, False), (B.SHIFT_R, False)]
    t.update({B.UP, B.Y})
    assert t.release_all() == [(B.UP, False), (B.Y, False)]


def test_ping_is_a_message_not_garbage():
    """MyWhoosh pings with a bare 0x00 every ~4 s; it must survive splitting."""
    from whoosh_connect.obc import split_messages
    msgs, rest = split_messages(bytes.fromhex("00"))
    assert msgs == [b"\x00"] and rest == b""
    msgs, rest = split_messages(bytes.fromhex("00") + bytes.fromhex("03000000"))
    assert msgs == [b"\x00", bytes.fromhex("03000000")] and rest == b""
