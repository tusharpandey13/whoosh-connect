"""Autouse safety guard: tests must never touch real BLE, mDNS, osascript, pgrep or Zwift/MyWhoosh."""
import subprocess
from pathlib import Path

import pytest

FIX = Path(__file__).parent / "fixtures"
FORBIDDEN = {"osascript", "open", "pgrep", "ps"}


def _boom(*a, **k):
    raise AssertionError("real BLE/mDNS in tests")


@pytest.fixture(autouse=True)
def _guard(monkeypatch):
    import bleak
    import zeroconf
    import zeroconf.asyncio

    for mod, name in [(bleak, "BleakScanner"), (bleak, "BleakClient"), (zeroconf, "Zeroconf"),
                      (zeroconf.asyncio, "AsyncZeroconf")]:
        monkeypatch.setattr(mod, name, _boom)

    def wrap(real):
        def guarded(args, *a, **k):
            argv0 = args if isinstance(args, str) else (args[0] if args else "")
            if str(argv0).split("/")[-1] in FORBIDDEN:
                raise AssertionError(f"forbidden subprocess in tests: {argv0}")
            return real(args, *a, **k)
        return guarded

    monkeypatch.setattr(subprocess, "run", wrap(subprocess.run))
    monkeypatch.setattr(subprocess, "Popen", wrap(subprocess.Popen))
    # never let a test read or write the owner's real state/config
    monkeypatch.setenv("HOME", str(Path("/nonexistent-whoosh-test-home")))


@pytest.fixture
def fix():
    return FIX
