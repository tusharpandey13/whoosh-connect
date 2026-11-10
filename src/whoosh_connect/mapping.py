"""Button -> Action map, TOML overrides, press/release diffing (pure)."""
from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

from . import obc
from .zwift import Button


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Action:
    name: str
    obc: int
    key: str | None = None  # keyboard key
    hold: bool = False  # keyboard: key down/up instead of a tap


_KEYS = {"SHIFT_UP": ("k", False), "SHIFT_DOWN": ("i", False),
         "STEER_LEFT": ("a", True), "STEER_RIGHT": ("d", True)}


def action(name: str) -> Action:
    key, hold = _KEYS.get(name, (None, False))
    return Action(name, obc.IDS[name], key, hold)


DEFAULT_MAP: dict[Button, Action] = {
    Button.SHIFT_R: action("SHIFT_UP"),
    Button.SHIFT_L: action("SHIFT_DOWN"),
    Button.LEFT: action("STEER_LEFT"),
    Button.RIGHT: action("STEER_RIGHT"),
    Button.UP: action("UP"),
    Button.DOWN: action("DOWN"),
    Button.A: action("SELECT"),
    Button.B: action("BACK"),
    Button.Y: action("CAMERA"),
    Button.Z: action("MENU"),
}


def load_map(path: str | Path | None) -> dict[Button, Action]:
    m = dict(DEFAULT_MAP)
    if path is None:
        return m
    try:
        with open(path, "rb") as f:
            cfg = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise ConfigError(f"cannot read config {path}: {e}") from e
    for bname, aname in cfg.get("map", {}).items():
        if bname not in Button.__members__:
            raise ConfigError(f"unknown button {bname!r}; valid: {', '.join(Button.__members__)}")
        btn = Button[bname]
        if aname == "NONE":
            m.pop(btn, None)
        elif aname in obc.IDS:
            m[btn] = action(aname)
        else:
            raise ConfigError(f"unknown action {aname!r} for {bname}; valid: NONE, {', '.join(obc.IDS)}")
    return m


class ButtonTracker:
    def __init__(self) -> None:
        self._held: frozenset[Button] = frozenset()

    def update(self, pressed) -> list[tuple[Button, bool]]:
        new = frozenset(pressed)
        out = [(b, b in new) for b in sorted(new ^ self._held)]
        self._held = new
        return out

    def release_all(self) -> list[tuple[Button, bool]]:
        return self.update(())
