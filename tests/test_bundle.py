"""#1: what the bundled .app does on launch (no PyInstaller, no GUI needed)."""
import io
import sys

from whoosh_connect import bundle, cli


class Tty(io.StringIO):
    def isatty(self):
        return True


def test_app_args_default_to_run():
    assert bundle.app_args([]) == ["run"]
    assert bundle.app_args(["-psn_0_12345"]) == ["run"]  # Finder's process serial number
    assert bundle.app_args(["replay", "x.jsonl"]) == ["replay", "x.jsonl"]


def test_finder_launch_logs_to_file(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "stderr", io.StringIO())  # no terminal; restored after the test
    seen = []

    def fake_main(argv):
        seen.append(argv)
        print("hello log", file=sys.stderr)
        return 0

    log = tmp_path / "Logs" / "ride.log"
    assert bundle.launch([], main=fake_main, log_path=log) == 0
    sys.stderr.close()
    assert seen == [["run"]]
    assert log.read_text() == "hello log\n"


def test_terminal_launch_keeps_stderr(tmp_path, monkeypatch):
    tty = Tty()
    monkeypatch.setattr(sys, "stderr", tty)
    log = tmp_path / "ride.log"
    assert bundle.launch(["check"], main=lambda argv: 7, log_path=log) == 7
    assert sys.stderr is tty and not log.exists()


def test_check_reports_every_module():
    out = []
    assert bundle.check(say=out.append, importer=lambda m: None) == 0
    assert out[0].startswith("whoosh-connect ") and "source" in out[0]
    if sys.platform == "darwin":
        assert any("bleak.backends.corebluetooth.scanner" in line for line in out)
    assert out[-1].strip().startswith("log ")


def test_check_fails_on_missing_backend():
    def importer(m):
        if "corebluetooth" in m or m == "psutil":
            raise ImportError(f"No module named {m!r}")

    out = []
    assert bundle.check(say=out.append, importer=importer) == 1
    assert any(line.startswith("  ERR psutil") for line in out)


def test_check_subcommand(monkeypatch):
    monkeypatch.setattr(bundle, "check", lambda: 0)
    assert cli.main(["check"]) == 0


def test_version_is_known():
    assert bundle.version() != "unknown"  # editable install carries metadata; the spec copies it
