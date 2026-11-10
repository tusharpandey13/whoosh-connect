"""JSONL capture writer/reader (scripts/probe2.py format) and replay."""
from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Iterator

from .zwift import ASYNC, SYNC_TX, parse_frame

_RX_CHARS = {ASYNC[:8], SYNC_TX[:8]}


class CaptureWriter:
    def __init__(self, path, clock=time.time):
        self._f = open(path, "w")
        self._clock = clock

    def write(self, ev: str, **kw) -> None:
        kw["ev"] = ev
        kw["t"] = round(self._clock(), 3)
        self._f.write(json.dumps(kw) + "\n")
        self._f.flush()

    def close(self) -> None:
        self._f.close()


def read_capture(path) -> Iterator[dict]:
    with open(path) as f:
        for line in f:
            if line.strip():
                yield json.loads(line)


async def replay(path, pipeline, device: str | None = None, speed: float = 0) -> None:
    rows = list(read_capture(path))
    if device is None:
        advs = [r for r in rows if r["ev"] == "adv"]
        left = [r for r in advs if r.get("mfr", "").startswith("0b")]
        pick = left or advs
        device = pick[0]["dev"] if pick else next((r["dev"] for r in rows if r["ev"] == "rx"), None)
    await pipeline.on_link(True, None)
    prev = None
    for r in rows:
        if r["ev"] != "rx" or r.get("dev") != device or r.get("ch") not in _RX_CHARS:
            continue
        if speed > 0 and prev is not None:
            await asyncio.sleep(max(0.0, (r["t"] - prev) / speed))
        prev = r["t"]
        await pipeline.on_frame(parse_frame(bytes.fromhex(r["hex"])))
