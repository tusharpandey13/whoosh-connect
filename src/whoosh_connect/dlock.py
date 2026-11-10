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
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

from .capture import read_capture
from .zwift import ASYNC, SYNC_RX, SYNC_TX, decode_varint

GAME_API = "https://us-or-rly101.zwift.com"
ENDPOINT = GAME_API + "/api/d-lock-service/device/authenticate"
HEADER = bytes([0xFF, 0x03, 0x00])
GRACE_S = 2.4  # puck stops streaming buttons this long after an unanswered challenge
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


def unwrap_field1(blob: bytes) -> bytes | None:
    """`0a <len> <len bytes>` -> the inner bytes.

    The 23 B cloud answer is `0a 15` + 21 B, i.e. protobuf field 1; Zwift may write
    the inner 21 B to the puck rather than the wrapper, so both are tried.
    """
    if len(blob) < 3 or blob[0] != 0x0A:
        return None
    try:
        n, i = decode_varint(blob, 1)
    except ValueError:
        return None
    return blob[i:] if n and i + n == len(blob) else None


def _shapes(answer: bytes) -> list[tuple[str, bytes]]:
    sig = unwrap_field1(answer)
    shapes = [("ff0400+answer", bytes([0xFF, 0x04, 0x00]) + answer)]
    if sig:
        shapes.append(("ff0400+sig", bytes([0xFF, 0x04, 0x00]) + sig))
    shapes += [("ff04+answer", bytes([0xFF, 0x04]) + answer), ("answer", answer)]
    return shapes


def framing_candidates(answer: bytes) -> dict[str, bytes]:
    """Host->puck data shapes to try, most likely first (the framing is not yet known)."""
    return dict(_shapes(answer))


@dataclass(frozen=True)
class WriteCandidate:
    framing: str
    char: bytes
    data: bytes
    response: bool = False


def write_candidates(answer: bytes) -> list[WriteCandidate]:
    """Every (characteristic, data, write type) worth trying, best guess first."""
    out = [WriteCandidate(n, SYNC_RX, d) for n, d in _shapes(answer)]
    out += [WriteCandidate(n + " resp", SYNC_RX, d, True) for n, d in _shapes(answer)]
    out.append(WriteCandidate("answer->0004", SYNC_TX, answer))
    return out


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
    frames: int = 0  # any notification during the window, not just buttons
    error: str | None = None


async def push(client, answer: bytes, sleep=asyncio.sleep, listen=8.0, seen: dict | None = None,
               subscribe: bool = True, prompt=None) -> list[PushResult]:
    """Write each candidate framing and watch for button frames.

    `client` is anything bleak-shaped: it is already connected; we subscribe to
    0002 and count `23 08` notifications after each write. The puck streams
    buttons only when it accepted the answer *and* a button is pressed, so
    `prompt(name, seconds)` is called before each window: without a press a zero
    count proves nothing. `seen` is shared with the caller (live()) to keep the
    running button total.
    """
    seen = seen if seen is not None else {"n": 0}
    seen.setdefault("any", 0)
    stop = {"v": False}

    def on_frame(_c, data):
        if stop["v"]:
            return
        seen["any"] += 1
        if bytes(data)[:2] == bytes([0x23, 0x08]):
            seen["n"] += 1

    if subscribe:
        await client.start_notify(ASYNC, on_frame)
    results = []
    for cand in write_candidates(answer):
        seen["n"] = seen["any"] = 0
        wrote, error = True, None
        try:
            await client.write_gatt_char(cand.char, cand.data, response=cand.response)
        except Exception as e:  # unsupported char/write type must not end the search
            wrote, error = False, str(e)
        if wrote:
            if prompt:
                prompt(cand.framing, listen)
            await sleep(listen)
        results.append(PushResult(cand.framing, wrote, seen["n"], seen["any"], error))
        if seen["n"]:
            break
    stop["v"] = True
    return results


def challenges_in_file(path) -> list[Challenge]:
    return challenges(read_capture(path))


@dataclass
class LiveResult:
    challenge: Challenge | None = None
    answer: bytes = b""
    status: int = 0
    frames_seen: int = 0
    push: list = None  # list[PushResult] once we tried writing


async def live(client, token, sleep=asyncio.sleep, wait=30.0, listen=8.0, signer=None,
               on_log=print, prelude: bytes | None = None) -> LiveResult:
    """Connect to a puck, catch its own challenge, sign it, write it back.

    This is the full offline unlock: the only thing Zwift is still needed for is
    a bearer token. `client` must be bleak-shaped and already connected.
    `prelude` is written right after RideOn (e.g. `ff 04 00`); the challenge shape
    seems to change with it (82 B after RideOn alone, 100 B after RideOn + ff 04 00).
    """
    from .zwift import RIDE_ON, SYNC_RX

    signer = signer or sign
    res = LiveResult()
    pending: list[Challenge] = []
    buttons = {"n": 0}

    def on_frame(_c, data):
        data = bytes(data)
        if data.startswith(HEADER):
            pending.append(Challenge(data))
        elif data[:2] == bytes([0x23, 0x08]):
            buttons["n"] += 1

    await client.start_notify(ASYNC, on_frame)
    await client.write_gatt_char(SYNC_RX, RIDE_ON, response=False)
    on_log("sent RideOn; waiting for a challenge (the puck only asks when its unlock lapsed)")
    if prelude:
        await sleep(0.3)
        await client.write_gatt_char(SYNC_RX, prelude, response=False)
        on_log(f"sent prelude {prelude.hex()}")

    waited = 0.0
    step = 0.25
    while not pending and waited < wait:
        await sleep(step)
        waited += step
    res.frames_seen = buttons["n"]
    if not pending:
        on_log(f"no challenge in {int(wait)} s: the puck is still unlocked "
               f"({buttons['n']} button frames seen)")
        return res

    res.challenge = pending[-1]
    on_log(f"challenge {len(res.challenge.raw)} B: {res.challenge.raw.hex()}")
    started = time.monotonic()
    result = await signer(res.challenge, token)
    elapsed = time.monotonic() - started
    res.status = result.status
    on_log(f"  {result.describe()} in {elapsed:.2f} s")
    if elapsed > GRACE_S:
        on_log(f"  warning: the puck mutes itself {GRACE_S:.1f} s after the challenge; "
               f"this answer arrived {elapsed - GRACE_S:.1f} s late")
    if not result.signed:
        return res
    res.answer = result.body
    res.push = await push(client, res.answer, sleep=sleep, listen=listen, seen=buttons,
                          subscribe=False,
                          prompt=lambda name, secs: on_log(
                              f"  {name}: press Click buttons now ({secs:.0f} s)"))
    for p in res.push:
        verdict = (f"accepted, {p.buttons} button frames" if p.buttons
                   else ("write failed: " + p.error if p.error else "silent, no button press seen"))
        on_log(f"  {p.framing}: {verdict}")
    return res
