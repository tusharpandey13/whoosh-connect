"""#4: a denied Bluetooth permission is detected and explained, never a silent 'Searching'."""
import plistlib
from pathlib import Path

import pytest
from bleak.exc import BleakBluetoothNotAvailableError, BleakBluetoothNotAvailableReason as R

from whoosh_connect import bundle, macos, messages
from whoosh_connect.ble import BluetoothUnavailable, _map_error

SPEC = Path(__file__).parents[1] / "packaging" / "whoosh-connect.spec"


@pytest.mark.parametrize("reason,text,expected", [
    # the exact texts bleak 3.x's corebluetooth backend raises
    (R.DENIED_BY_USER, "Bluetooth access is denied by the user for the current application. "
                       "Check macOS privacy settings.", "denied"),
    (R.DENIED_BY_SYSTEM, "Bluetooth access is restricted for the current application, e.g. by "
                         "parental controls. Ask the admin to remove this restriction.", "denied"),
    (R.DENIED_BY_UNKNOWN, "Bluetooth is not authorized for an unknown reason. Check macOS privacy "
                          "settings.", "denied"),
    (R.POWERED_OFF, "Bluetooth device is turned off", "off"),
    (R.NO_BLUETOOTH, "Bluetooth is unsupported", "off"),
])
def test_bleak_reason_maps(reason, text, expected):
    e = _map_error(BleakBluetoothNotAvailableError(text, reason))
    assert isinstance(e, BluetoothUnavailable) and e.reason == expected
    m = messages.for_error(e)
    assert m is (messages.BT_DENIED if expected == "denied" else messages.BT_OFF)


def test_denied_text_without_reason():
    # older bleak: only the text; "denied by the user" used to slip through as a generic error
    e = _map_error(Exception("Bluetooth access is denied by the user for the current application."))
    assert isinstance(e, BluetoothUnavailable) and e.reason == "denied"


def test_denied_message_opens_settings():
    assert messages.BT_DENIED.settings_url.startswith("x-apple.systempreferences:")
    assert "System Settings" in messages.BT_DENIED.text


class FakeCB:
    def __init__(self, value):
        class CBManager:
            @staticmethod
            def authorization():
                if isinstance(value, Exception):
                    raise value
                return value
        self.CBManager = CBManager


@pytest.mark.parametrize("value,expected", [
    (0, "not_determined"), (1, "restricted"), (2, "denied"), (3, "allowed"), (9, "unknown"),
    (AttributeError("no CBManager.authorization before 10.15"), "unknown"),
])
def test_bt_authorization(value, expected):
    assert macos.bt_authorization(FakeCB(value)) == expected
    assert macos.bt_denied(FakeCB(value)) == (expected in ("denied", "restricted"))


def test_info_plist_has_usage_strings():
    p = bundle.info_plist("1.2.3", "26")
    for k in ("NSBluetoothAlwaysUsageDescription", "NSBluetoothPeripheralUsageDescription"):
        assert "Bluetooth" in p[k] and "Click" in p[k]
    assert p["CFBundleShortVersionString"] == "1.2.3" and p["LSMinimumSystemVersion"] == "26"
    plistlib.dumps(p)  # every value is plist-serialisable


def test_spec_uses_info_plist():
    assert "info_plist=info_plist(VERSION, MIN_MACOS)" in SPEC.read_text()
