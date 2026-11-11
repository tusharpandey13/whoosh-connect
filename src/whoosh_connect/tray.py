"""Menu-bar item (pystray). All AppKit work happens on the main thread (#2).

``pystray`` and ``Pillow`` are optional (``pip install 'whoosh-connect[app]'``) and only
imported when a real icon is built, so the logic here is testable without them.
"""
from __future__ import annotations

import logging
import signal
import subprocess
import sys

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


def open_url(url: str, runner=subprocess.Popen) -> None:
    runner(["open", url])


class Tray:
    """Owns the pystray icon. ``update`` may be called from any thread."""

    def __init__(self, quit, pystray=None, on_main=None, image=draw, opener=open_url,
                 signals=None):
        if pystray is None:
            import pystray
        self.pystray, self._quit, self.image, self.opener = pystray, quit, image, opener
        self.on_main = on_main or (_on_main_darwin if sys.platform == "darwin" else (lambda f: f()))
        self.signals = signals
        self.status = Status(Phase.SEARCHING, Phase.SEARCHING.value)
        self.icon = pystray.Icon(TITLE, image(self.status.phase), TITLE, menu=self._menu())
        self._quitting = False

    # -- menu ---------------------------------------------------------------------------
    def _menu(self):
        Item, Menu = self.pystray.MenuItem, self.pystray.Menu
        return Menu(
            Item(lambda _i: self.status.line, None, enabled=False),
            Item("Open Bluetooth Settings", self._open_settings,
                 visible=lambda _i: bool(self.status.error and self.status.error.settings_url)),
            Menu.SEPARATOR,
            Item("Quit whoosh-connect", self.quit),
        )

    def _open_settings(self, *_):
        if self.status.error and self.status.error.settings_url:
            self.opener(self.status.error.settings_url)

    # -- state --------------------------------------------------------------------------
    def update(self, status: Status) -> None:
        self.on_main(lambda: self._apply(status))

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
