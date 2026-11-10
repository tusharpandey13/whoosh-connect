"""Zwift D-Lock: the cloud half of the Click v2 unlock.

The puck sends `ff 03 00` + an opaque challenge and only streams button frames
once the answer is written back to char 0003. That answer is minted by Zwift's
D-Lock service for the rider's account:

    POST /api/d-lock-service/device/authenticate   Content-Type: application/protobuf
    body = the challenge payload (header stripped)
    -> opaque blob (Zwift logs it as 23 bytes for a Click v2)

Evidence: notes/unlock-analysis.md. See `challenges()` for reading a capture and
`framing_candidates()` for the host->puck framings to try.
"""
from __future__ import annotations

import asyncio
import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass

from .capture import read_capture
from .zwift import ASYNC, SYNC_TX

GAME_API = "https://us-or-rly101.zwift.com"
ENDPOINT = GAME_API + "/api/d-lock-service/device/authenticate"
HEADER = bytes([0xFF, 0x03, 0x00])
_RX = {ASYNC[:8], SYNC_TX[:8]}
_TOKEN_RE = re.compile(r"--token=(\{.*\})")


@dataclass(frozen=True)
class Challenge:
    raw: bytes  # whole notification, header included
    t: float | None = None

    @property
    def payload(self) -> bytes:
        return self.raw[len(HEADER):] if self.raw.startswith(HEADER) else self.raw

    def __str__(self) -> str:
        return f"{len(self.raw)} B: {self.raw.hex()}"


def challenges(rows) -> list[Challenge]:
    """Every `ff 03` challenge in a JSONL capture, oldest first."""
    out = []
    for r in rows:
        if r.get("ev") != "rx" or r.get("ch") not in _RX:
            continue
        raw = bytes.fromhex(r.get("hex", ""))
        if raw.startswith(HEADER):
            out.append(Challenge(raw, r.get("t")))
    return out


def framing_candidates(answer: bytes) -> dict[str, bytes]:
    """Host->puck writes to try, most likely first (the framing is not yet known)."""
    return {
        "ff0400+answer": bytes([0xFF, 0x04, 0x00]) + answer,
        "ff04+answer": bytes([0xFF, 0x04]) + answer,
        "answer": answer,
    }


@dataclass(frozen=True)
class SignResult:
    status: int
    body: bytes

    @property
    def signed(self) -> bool:
        return self.status in (200, 201) and bool(self.body)

    def describe(self) -> str:
        if self.signed:
            return f"HTTP {self.status}, {len(self.body)} B signed: {self.body.hex()}"
        if self.status in (200, 204):
            return (f"HTTP {self.status}, 0 B: service parsed the challenge but minted nothing "
                    f"(stale challenge, or device not registered to this account)")
        text = self.body[:120].decode("utf-8", "replace")
        return f"HTTP {self.status}: {text}"


def _post(url: str, headers: dict[str, str], body: bytes, timeout: float) -> tuple[int, bytes]:
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:  # 4xx/5xx carry a diagnostic body
        return e.code, e.read()


async def sign(challenge: Challenge, token: str, post=_post, endpoint: str = ENDPOINT,
               timeout: float = 20.0) -> SignResult:
    """Ask the D-Lock service for the answer to `challenge`. Never logs the token."""
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/protobuf",
        "Accept": "application/protobuf",
    }
    status, body = await asyncio.to_thread(post, endpoint, headers, challenge.payload, timeout)
    return SignResult(status, body)


def token_from_ps(text: str) -> str | None:
    """Pull the game's `access_token` out of a `ps -axo args=` dump (never print it)."""
    for line in text.splitlines():
        m = _TOKEN_RE.search(line)
        if not m:
            continue
        try:
            tok = json.loads(m.group(1)).get("access_token")
        except ValueError:
            continue
        if tok:
            return tok
    return None


def acquire_token(ps_output=None) -> str | None:
    """Token of the running Zwift game, or from a file when `ps` is not readable."""
    if ps_output is None:
        import subprocess
        try:
            ps_output = subprocess.run(["ps", "-axo", "args="], capture_output=True,
                                       text=True, timeout=5).stdout
        except (OSError, subprocess.SubprocessError):
            return None
    return token_from_ps(ps_output)


@dataclass(frozen=True)
class PushResult:
    framing: str
    wrote: bool
    buttons: int


async def push(client, answer: bytes, sleep=asyncio.sleep, listen=8.0) -> list[PushResult]:
    """Write each candidate framing to 0003 and watch for button frames.

    `client` is anything bleak-shaped: it is already connected; we subscribe to
    0002 and count `23 08` notifications after each write. The puck streams
    buttons only when it accepted the answer, so a non-zero count is proof.
    """
    from .zwift import SYNC_RX
    seen = {"n": 0, "stop": False}

    def on_frame(_c, data):
        if not seen["stop"] and bytes(data)[:2] == bytes([0x23, 0x08]):
            seen["n"] += 1

    await client.start_notify(ASYNC, on_frame)
    results = []
    for name, frame in framing_candidates(answer).items():
        seen["n"] = 0
        await client.write_gatt_char(SYNC_RX, frame, response=False)
        await sleep(listen)
        results.append(PushResult(name, True, seen["n"]))
        if seen["n"]:
            break
    seen["stop"] = True
    return results


def challenges_in_file(path) -> list[Challenge]:
    return challenges(read_capture(path))
