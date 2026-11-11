"""#3: Start at login writes/removes a LaunchAgent for the bundled binary."""
import argparse
import io
import plistlib

import pytest

from whoosh_connect import cli, messages
from whoosh_connect.launchagent import LABEL, LoginItem, NotMovable, program_args
from whoosh_connect.tray import Tray

APP_EXE = "/Applications/whoosh-connect.app/Contents/MacOS/whoosh-connect"
TRANSLOCATED = ("/private/var/folders/xy/T/AppTranslocation/1234/d/whoosh-connect.app"
                "/Contents/MacOS/whoosh-connect")


def item(tmp_path, exe=APP_EXE):
    return LoginItem(home=lambda: tmp_path, args=lambda: [exe, "app"],
                     log_path=lambda: tmp_path / "Library/Logs/whoosh-connect/ride.log")


def test_program_args_bundled_vs_dev():
    assert program_args(APP_EXE, frozen=True) == [APP_EXE, "app"]
    assert program_args("/v/bin/python", frozen=False) == ["/v/bin/python", "-m", "whoosh_connect", "app"]


def test_plist_keeps_the_install_script_contract(tmp_path):
    li = item(tmp_path)
    p = plistlib.loads(li.render())
    assert p["Label"] == LABEL == "com.tusharpandey13.whoosh-connect"
    assert p["ProgramArguments"] == [APP_EXE, "app"]
    assert p["RunAtLoad"] is True
    assert p["KeepAlive"] == {"SuccessfulExit": False}  # crash -> restart, Quit -> stays quit
    assert p["ThrottleInterval"] == 10
    assert p["ProcessType"] == "Interactive"
    log = str(tmp_path / "Library/Logs/whoosh-connect/ride.log")
    assert p["StandardOutPath"] == p["StandardErrorPath"] == log
    assert "WorkingDirectory" not in p  # no repo path any more


def test_same_label_and_throttle_as_install_script():
    from pathlib import Path
    script = (Path(__file__).parents[1] / "scripts/install_launchd.sh").read_text()
    assert f'LABEL="{LABEL}"' in script and "<integer>10</integer>" in script
    assert "<string>Interactive</string>" in script


def test_on_off_round_trip(tmp_path):
    li = item(tmp_path)
    assert not li.enabled()
    li.set(True)
    assert li.enabled() and li.path == tmp_path / f"Library/LaunchAgents/{LABEL}.plist"
    assert (tmp_path / "Library/Logs/whoosh-connect").is_dir()  # launchd won't create it
    assert plistlib.loads(li.path.read_bytes())["ProgramArguments"][0] == APP_EXE
    li.set(True)  # idempotent
    li.set(False)
    assert not li.enabled()
    li.set(False)  # idempotent


def test_translocated_app_refuses(tmp_path):
    li = item(tmp_path, exe=TRANSLOCATED)
    assert not li.available()
    with pytest.raises(NotMovable):
        li.set(True)
    assert not li.enabled()
    li.set(False)  # turning off is always allowed


# -- menu checkbox ------------------------------------------------------------------------
from test_app import FakeMenu, FakePystray  # noqa: E402


def login_menu_item(t):
    return next(i for i in t.icon.menu.items
                if i is not FakeMenu.SEPARATOR and i.label().startswith("Start at login"))


def test_menu_checkbox_toggles(tmp_path):
    li = item(tmp_path)
    t = Tray(quit=lambda: None, pystray=FakePystray, on_main=lambda f: f(), image=str, login=li)
    mi = login_menu_item(t)
    assert mi.checked(mi) is False and mi.enabled(mi) is True
    mi.action()
    assert li.enabled() and mi.checked(mi) is True and t.icon.menu_updates == 1
    mi.action()
    assert not li.enabled()


def test_menu_checkbox_explains_translocation(tmp_path):
    li = item(tmp_path, exe=TRANSLOCATED)
    t = Tray(quit=lambda: None, pystray=FakePystray, on_main=lambda f: f(), image=str, login=li)
    mi = login_menu_item(t)
    assert mi.enabled(mi) is False and messages.MOVE_TO_APPS.text in mi.label()
    mi.action()  # even if clicked, never raises out of the menu
    assert not li.enabled()


def test_menu_has_no_checkbox_without_login_item():
    t = Tray(quit=lambda: None, pystray=FakePystray, on_main=lambda f: f(), image=str)
    assert not any(i is not FakeMenu.SEPARATOR and i.label().startswith("Start at login")
                   for i in t.icon.menu.items)


# -- CLI: login-item [--print|--on|--off] -------------------------------------------------
def args(**kw):
    return argparse.Namespace(**{"print": False, "on": False, "off": False, **kw})


def test_cli_print_changes_nothing(tmp_path):
    li, out = item(tmp_path), io.StringIO()
    assert cli._login_item(args(print=True), item=li, out=out) == cli.OK
    assert plistlib.loads(out.getvalue().encode())["Label"] == LABEL
    assert not li.enabled()


def test_cli_on_off_status(tmp_path):
    li, out = item(tmp_path), io.StringIO()
    assert cli._login_item(args(on=True), item=li, out=out) == cli.OK and li.enabled()
    assert "start at login: on" in out.getvalue()
    assert cli._login_item(args(off=True), item=li, out=out) == cli.OK and not li.enabled()
    assert cli._login_item(args(), item=li, out=out) == cli.OK
    assert out.getvalue().rstrip().splitlines()[-1].startswith("start at login: off")


def test_cli_on_translocated_is_usage_error(tmp_path, capsys):
    li = item(tmp_path, exe=TRANSLOCATED)
    assert cli._login_item(args(on=True), item=li, out=io.StringIO()) == cli.USAGE
    assert messages.MOVE_TO_APPS.text in capsys.readouterr().err


def test_cli_parser_accepts_login_item():
    a = cli._parser().parse_args(["login-item", "--print"])
    assert a.cmd == "login-item" and a.print
    with pytest.raises(SystemExit):
        cli._parser().parse_args(["login-item", "--on", "--off"])


def test_refresh_follows_a_moved_app(tmp_path):
    exe = ["/Users/r/Downloads/whoosh-connect.app/Contents/MacOS/whoosh-connect"]
    li = LoginItem(home=lambda: tmp_path, args=lambda: [exe[0], "app"],
                   log_path=lambda: tmp_path / "ride.log")
    assert li.refresh() is False  # not enabled: nothing to do
    li.set(True)
    assert li.refresh() is False  # same path
    exe[0] = APP_EXE
    assert li.refresh() is True
    assert plistlib.loads(li.path.read_bytes())["ProgramArguments"] == [APP_EXE, "app"]


def test_refresh_never_points_at_a_translocated_copy(tmp_path):
    li = item(tmp_path)
    li.set(True)
    moved = item(tmp_path, exe=TRANSLOCATED)
    assert moved.refresh() is False
    assert plistlib.loads(li.path.read_bytes())["ProgramArguments"][0] == APP_EXE
