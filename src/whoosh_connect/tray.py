"""Menu-bar item (pystray). All AppKit work happens on the main thread (#2).

``pystray`` and ``Pillow`` are optional (``pip install 'whoosh-connect[app]'``) and only
imported when a real icon is built, so the logic here is testable without them.
"""
from __future__ import annotations

import logging
import signal
import subprocess
import sys

from . import messages
from .status import Phase, Status

log = logging.getLogger(__name__)
TITLE = "whoosh-connect"
# colour + shape per phase, so it also reads in a monochrome or colour-blind glance
LOOK = {
    Phase.SEARCHING: ((150, 150, 150), "ring"),
    Phase.WAITING: ((235, 160, 0), "half"),
    Phase.RIDING: ((40, 180, 80), "dot"),
    Phase.ERROR: ((220, 50, 40), "bang"),
}


def draw(phase: Phase, size: int = 44):
    """A small status glyph for ``phase`` as a PIL image."""
    from PIL import Image, ImageDraw

    colour, shape = LOOK[phase]
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    pad, w = size // 6, max(2, size // 10)
    box = (pad, pad, size - pad, size - pad)
    if shape == "ring":
        d.ellipse(box, outline=colour, width=w)
    elif shape == "half":
        d.ellipse(box, outline=colour, width=w)
        d.pieslice(box, 90, 270, fill=colour)
    else:
        d.ellipse(box, fill=colour)
    if shape == "bang":
        cx = size // 2
        d.rectangle((cx - w // 2, pad + w, cx + w // 2, size // 2 + w), fill="white")
        d.rectangle((cx - w // 2, size - pad - 2 * w, cx + w // 2, size - pad - w), fill="white")
    return img


def _on_main_darwin(fn) -> None:
    from PyObjCTools import AppHelper

    AppHelper.callAfter(fn)


COPIED = "Diagnostics copied. Paste them into your message."
NOT_COPIED = "Couldn't copy diagnostics. The log is in ~/Library/Logs/whoosh-connect."


def _notify(text: str) -> None:
    from .macos import notify

    notify(text)


def open_url(url: str, runner=subprocess.Popen) -> None:
    runner(["open", url])


class Tray:
    """Owns the pystray icon. ``update`` may be called from any thread."""

    def __init__(self, quit, pystray=None, on_main=None, image=draw, opener=open_url,
                 signals=None, login=None, diagnostics=None, notify=None):
        if pystray is None:
            import pystray
        self.pystray, self._quit, self.image, self.opener = pystray, quit, image, opener
        self.on_main = on_main or (_on_main_darwin if sys.platform == "darwin" else (lambda f: f()))
        self.signals, self.login, self.diagnostics = signals, login, diagnostics
        self.notify = notify or _notify
        self.status = Status(Phase.SEARCHING, Phase.SEARCHING.value)
        self.setup: str | None = None  # first-run step line (#8)
        self.icon = pystray.Icon(TITLE, image(self.status.phase), TITLE, menu=self._menu())
        self._quitting = False

    # -- menu ---------------------------------------------------------------------------
    def _menu(self):
        Item, Menu = self.pystray.MenuItem, self.pystray.Menu
        items = [
            Item(lambda _i: self.status.line, None, enabled=False),
            Item(lambda _i: self.setup or "", None, enabled=False, visible=lambda _i: bool(self.setup)),
            Item("Open Bluetooth Settings", self._open_settings,
                 visible=lambda _i: bool(self.status.error and self.status.error.settings_url)),
            Menu.SEPARATOR,
        ]
        if self.login is not None:
            items.append(Item(self._login_label, self._toggle_login,
                              checked=lambda _i: self.login.enabled(),
                              enabled=lambda _i: self.login.available()))
        if self.diagnostics is not None:
            items.append(Item("Copy diagnostics", self._copy_diagnostics))
        items.append(Item("Quit whoosh-connect", self.quit))
        return Menu(*items)

    def _copy_diagnostics(self, *_) -> None:
        try:
            ok = self.diagnostics()
        except Exception as e:  # noqa: BLE001 - a menu click must never kill the app
            log.warning("copy diagnostics: %r", e)
            ok = False
        self.notify(COPIED if ok else NOT_COPIED)

    def _login_label(self, _item=None) -> str:
        if self.login.available():
            return "Start at login"
        return f"Start at login - {messages.MOVE_TO_APPS.text}"

    def _toggle_login(self, *_) -> None:
        on = not self.login.enabled()
        try:
            self.login.set(on)
            log.info("start at login: %s", "on" if on else "off")
        except Exception as e:  # noqa: BLE001 - a menu click must never kill the app
            log.warning("start at login: %r", e)
        self.icon.update_menu()

    def _open_settings(self, *_):
        if self.status.error and self.status.error.settings_url:
            self.opener(self.status.error.settings_url)

    # -- state --------------------------------------------------------------------------
    def update(self, status: Status) -> None:
        self.on_main(lambda: self._apply(status))

    def set_setup(self, line: str | None) -> None:
        def apply():
            self.setup = line
            self.icon.update_menu()
        self.on_main(apply)

    def _apply(self, status: Status) -> None:
        changed_phase = status.phase != self.status.phase
        self.status = status
        if changed_phase:
            self.icon.icon = self.image(status.phase)
            log.info("status: %s", status.line)
        self.icon.title = f"{TITLE}: {status.line}"
        self.icon.update_menu()

    # -- lifecycle ----------------------------------------------------------------------
    def quit(self, *_) -> None:
        if self._quitting:
            return
        self._quitting = True
        log.info("quitting")
        try:
            self._quit()  # closes BLE + mDNS; returns within ~4 s
        finally:
            self.icon.stop()

    def _install_sigterm(self) -> None:
        """launchd stops agents with SIGTERM; route it through Quit inside the AppKit loop."""
        if self.signals is None and sys.platform == "darwin":
            from PyObjCTools import MachSignals as signals
        else:
            signals = self.signals
        if signals is not None:
            signals.signal(signal.SIGTERM, lambda *_: self.quit())

    def run(self) -> None:
        self._install_sigterm()
        self.icon.run()
