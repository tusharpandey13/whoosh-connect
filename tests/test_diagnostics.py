"""#10: Copy diagnostics - one redacted bundle on the clipboard."""
import asyncio

from whoosh_connect import app, diagnostics as d
from whoosh_connect.status import StatusModel
from whoosh_connect.tray import COPIED, NOT_COPIED, Tray

JWT = "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiIxMjMifQ.c2lnbmF0dXJlLWJ5dGVz"
CB_ID = "3F2504E0-4F89-11D3-9A0C-0305E82C2D7E"


def test_bearer_and_tokens_are_gone_entirely():
    lines = [
        f"Authorization: Bearer {JWT}",
        "ZwiftApp --token={\"access_token\":\"abc.def.ghi\",\"expires_in\":3600}",
        '{"access_token": "s3cr3t-value", "refresh_token": "r3fr3sh"}',
        f"token={JWT}",
        f"stray jwt {JWT} in a line",
    ]
    out = d.redact("\n".join(lines))
    for secret in (JWT, "abc.def.ghi", "s3cr3t-value", "r3fr3sh", "eyJ"):
        assert secret not in out
    assert out.count(d.CUT) >= 5


def test_addresses_cut_to_last_four():
    out = d.redact(f"connect {CB_ID} and AA:BB:CC:DD:2D:7E or aa-bb-cc-dd-ee-ff")
    assert out == "connect …2D7E and …2D7E or …EEFF"


def test_ordinary_log_lines_survive():
    line = "12:01:02 Click right 2D7E connected, battery 80 %; app MyWhoosh 6.2.3"
    assert d.redact(line) == line


def test_report_has_every_field_and_is_redacted():
    text = d.report(version="0.1.0", os_version="macOS 26.1 (arm64)", python="3.15.2",
                    bluetooth="allowed", status="Riding", unlock_age=3 * 3600 + 120,
                    zwift_now=False, zwift_seen=True,
                    log=f"line 1\nBearer {JWT}\ndevice {CB_ID}\n")
    for want in ("0.1.0", "macOS 26.1", "3.15.2", "bluetooth access:   allowed",
                 "status:             Riding", "3 h 2 min ago", "zwift running now:  no",
                 "zwift seen:         yes", "line 1", "…2D7E"):
        assert want in text
    assert JWT not in text and CB_ID not in text


def test_ago():
    assert d.ago(None) == "never" and d.ago(59) == "0 min ago" and d.ago(3600) == "1 h 0 min ago"


def test_tail_last_500_lines(tmp_path):
    p = tmp_path / "ride.log"
    p.write_text("".join(f"{i}\n" for i in range(1200)))
    out = d.tail(p).splitlines()
    assert len(out) == 500 and out[0] == "700" and out[-1] == "1199"
    assert d.tail(tmp_path / "missing.log").startswith("(no log:")


def test_tail_survives_bad_bytes(tmp_path):
    p = tmp_path / "ride.log"
    p.write_bytes(b"ok\n\xff\xfe broken\n")
    assert "ok" in d.tail(p)


def test_collect_uses_seams_and_reads_unlock_only(tmp_path):
    log = tmp_path / "ride.log"
    log.write_text(f"hello\nBearer {JWT}\n")

    class Store:
        def unlock_age(self, now):
            assert now == 5000.0
            return 600.0

        def record_unlock(self, ts):  # must never be called from diagnostics
            raise AssertionError("diagnostics wrote the unlock state")

    text = d.collect("Searching", False, log_path=log, store=Store(), bt=lambda: "denied",
                     ps=lambda: True, clock=lambda: 5000.0)
    assert "10 min ago" in text and "bluetooth access:   denied" in text
    assert "zwift running now:  yes" in text and "zwift seen:         no" in text
    assert "hello" in text and JWT not in text


def test_copy_goes_through_pbcopy():
    calls = []
    assert d.copy("abc", runner=lambda argv, **kw: calls.append((argv, kw["input"])))
    assert calls == [(["pbcopy"], b"abc")]

    def boom(*a, **kw):
        raise FileNotFoundError("pbcopy")
    assert d.copy("abc", runner=boom) is False


# -- menu ---------------------------------------------------------------------------------
from test_app import FakeMenu, FakePystray  # noqa: E402


def diag_item(t):
    return next(i for i in t.icon.menu.items
                if i is not FakeMenu.SEPARATOR and i.label() == "Copy diagnostics")


def test_menu_item_copies_and_says_so():
    said = []
    t = Tray(quit=lambda: None, pystray=FakePystray, on_main=lambda f: f(), image=str,
             diagnostics=lambda: True, notify=said.append)
    diag_item(t).action()
    assert said == [COPIED]


def test_menu_item_failure_is_reported_not_raised():
    said = []

    def broken():
        raise RuntimeError("x")

    t = Tray(quit=lambda: None, pystray=FakePystray, on_main=lambda f: f(), image=str,
             diagnostics=broken, notify=said.append)
    diag_item(t).action()
    assert said == [NOT_COPIED]


async def test_bridge_remembers_zwift_was_seen(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    running = iter([True, False])

    async def nosleep(_):
        await asyncio.sleep(0)

    class Stop(Exception):
        pass

    def no_server(port):
        raise Stop

    b = app.Bridge(StatusModel(), ps=lambda: next(running, False), sleep=nosleep,
                   server_factory=no_server)
    assert b.zwift_seen is False
    try:
        await b.run()
    except Stop:
        pass
    assert b.zwift_seen is True
