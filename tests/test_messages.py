"""#7: every failure the rider can hit maps to one plain sentence with a next action."""
import re

import pytest

from whoosh_connect import cli, messages
from whoosh_connect.ble import BluetoothUnavailable, _map_error
from whoosh_connect.mapping import ConfigError
from whoosh_connect.obc_server import AlreadyRunning, PortsBusy

RIDER = [m for m in vars(messages).values() if isinstance(m, messages.Message)]
ACTIONS = ("Turn", "Open", "Quit", "look", "Fix", "Use", "Move")


def test_message_table_is_complete():
    assert {messages.BT_OFF, messages.BT_DENIED, messages.PORTS, messages.RUNNING, messages.ZWIFT,
            messages.CONFIG, messages.UNKNOWN} <= set(RIDER)
    assert set(messages.BY_CODE) == {2, 3, 4, 6}  # 5 is the optional dlock: never rider-facing


@pytest.mark.parametrize("m", RIDER, ids=lambda m: m.text[:20])
def test_rider_text_is_plain(m):
    assert not re.search(r"\d{3,}", m.text), "no port numbers"
    assert not re.search(r"(Error|Exception|Traceback|Unavailable|Busy|errno)", m.text)
    assert m.text.count(". ") <= 2 and m.text.endswith(".")
    assert any(a in m.text for a in ACTIONS), "names the next action"


@pytest.mark.parametrize("exc,expected", [
    (BluetoothUnavailable("x", reason="off"), messages.BT_OFF),
    (BluetoothUnavailable("x", reason="denied"), messages.BT_DENIED),
    (BluetoothUnavailable("x"), messages.BT_OFF),
    (PortsBusy("no free TCP port in 36867-36877"), messages.PORTS),
    (AlreadyRunning("whoosh-connect is already advertised"), messages.RUNNING),
    (ConfigError("unknown button NOPE"), messages.CONFIG),
    (RuntimeError("boom"), messages.UNKNOWN),
])
def test_for_error(exc, expected):
    assert messages.for_error(exc) is expected


@pytest.mark.parametrize("bleak_text,reason", [
    ("Bluetooth device is turned off", "off"),           # bleak corebluetooth, adapter off
    ("BLE is not authorized - check macOS privacy settings", "denied"),
    ("Bluetooth adapter is powered off", "off"),
    ("Unauthorized", "denied"),
])
def test_map_error_reason(bleak_text, reason):
    e = _map_error(Exception(bleak_text))
    assert isinstance(e, BluetoothUnavailable) and e.reason == reason
    assert bleak_text in str(e)  # the technical text survives for the log


def test_map_error_passes_other_errors():
    e = ValueError("something else")
    assert _map_error(e) is e


def test_explain_logs_detail_not_ui(caplog):
    caplog.set_level("INFO")
    m = messages.explain(PortsBusy("no free TCP port in 36867-36877"))
    assert m is messages.PORTS
    assert "PortsBusy" in caplog.text and "36867" in caplog.text
    assert "36867" not in m.text


def test_cli_prints_only_the_sentence(monkeypatch, capsys):
    async def boom(args):
        raise AlreadyRunning("whoosh-connect is already advertised on this machine: pkill -f whoosh_connect")

    monkeypatch.setattr(cli, "_run", boom)
    assert cli.main(["run"]) == cli.RUNNING
    err = capsys.readouterr().err.strip().splitlines()
    assert err[-1] == messages.RUNNING.text
    assert "pkill" not in err[-1] and "AlreadyRunning" not in err[-1]


@pytest.mark.parametrize("exc,code", [
    (BluetoothUnavailable("x", reason="denied"), cli.BT),
    (PortsBusy("p"), cli.PORTS),
])
def test_cli_exit_codes_with_sentences(monkeypatch, capsys, exc, code):
    async def boom(args):
        raise exc

    monkeypatch.setattr(cli, "_run", boom)
    assert cli.main(["run"]) == code
    assert capsys.readouterr().err.strip().splitlines()[-1] == messages.for_error(exc).text
