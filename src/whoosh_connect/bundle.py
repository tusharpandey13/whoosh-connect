"""What the bundled .app does on launch, kept out of the PyInstaller entry for testing."""
from __future__ import annotations

import sys
from importlib import metadata

from . import cli, state


def version() -> str:
    try:
        return metadata.version("whoosh-connect")
    except metadata.PackageNotFoundError:
        return "unknown"


def app_args(argv: list[str]) -> list[str]:
    """Finder may pass a -psn_… process serial number; no real args means `run`."""
    args = [a for a in argv if not a.startswith("-psn_")]
    return args or ["run"]


def launch(argv: list[str], main=cli.main, stderr=None, log_path=None) -> int:
    """Run the CLI. A Finder launch has no terminal, so stderr goes to the log file."""
    stream = stderr if stderr is not None else sys.stderr
    if stream is None or not stream.isatty():
        path = log_path or state.default_log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        sys.stderr = open(path, "a", buffering=1, encoding="utf-8")
    return main(app_args(argv))


def check(say=print, importer=__import__) -> int:
    """`whoosh-connect check`: prove the build has what a ride needs, without scanning."""
    ok = True
    say(f"whoosh-connect {version()} on Python {sys.version.split()[0]} "
        f"({'bundled' if getattr(sys, 'frozen', False) else 'source'})")
    mods = ["bleak", "zeroconf", "psutil", "platformdirs"]
    if sys.platform == "darwin":
        mods += ["bleak.backends.corebluetooth.scanner", "bleak.backends.corebluetooth.client",
                 "CoreBluetooth"]
    for m in mods:
        try:
            importer(m)
            say(f"  ok  {m}")
        except Exception as e:  # noqa: BLE001 - report every failure, not the first
            ok = False
            say(f"  ERR {m}: {e!r}")
    say(f"  log {state.default_log_path()}")
    return 0 if ok else 1
