"""One unlock shot per wake cycle, with selectable handshake mode.

MODE=passive  connect + subscribe only (no writes at all)
MODE=rideon   RideOn
MODE=ff0400   RideOn, then ff 04 00

The puck issues ~one challenge per wake cycle, so run exactly one shot, then let
it sleep before the next.
"""
import asyncio, os, subprocess, sys, time
sys.path.insert(0, "src")
from bleak import BleakClient, BleakScanner

from whoosh_connect import dlock

ASYNC = "00000002-19ca-4651-86e5-fa29dcdd09d1"
RX = "00000003-19ca-4651-86e5-fa29dcdd09d1"
TX = "00000004-19ca-4651-86e5-fa29dcdd09d1"
MODE = os.environ.get("MODE", "passive")
SIDE = os.environ.get("SIDE", "left")
WAIT = float(os.environ.get("WAIT", "25"))
SLEEP = float(os.environ.get("SLEEP", "0"))
MFR = {"left": 0x0B, "right": 0x0A}[SIDE]
TOKEN = open("/tmp/whoosh-zwift.token").read().strip()


def beep(n=1):
    subprocess.run(["osascript", "-e", f"beep {n}"], check=False)


async def find(timeout=60):
    found = {}

    def cb(d, a):
        m = a.manufacturer_data.get(0x094A)
        if m and m[0] == MFR:
            found[d.address] = d

    print(f"scanning {SIDE} (press it to wake)...", flush=True)
    end = time.time() + timeout
    while time.time() < end and not found:
        async with BleakScanner(cb):
            for _ in range(10):
                await asyncio.sleep(0.5)
                if found:
                    break
    return next(iter(found.values()), None)


def f3_len(raw: bytes):
    """protobuf field 3 length in an ff 03 challenge payload."""
    p = raw[3:] if raw.startswith(dlock.HEADER) else raw
    try:
        i = 0
        fields = {}
        while i < len(p):
            key = p[i]; i += 1
            f, w = key >> 3, key & 7
            if w == 2:
                n = 0; shift = 0
                while True:
                    b = p[i]; i += 1
                    n |= (b & 0x7F) << shift
                    if not b & 0x80:
                        break
                    shift += 7
                fields[f] = n
                i += n
            elif w == 0:
                while p[i] & 0x80:
                    i += 1
                i += 1
            else:
                return None
        return fields.get(3)
    except IndexError:
        return None


async def main():
    if SLEEP:
        print(f"quiet for {SLEEP:.0f}s so the puck re-arms its challenge...", flush=True)
        await asyncio.sleep(SLEEP)
        print(">>> 3 BEEPS: PRESS THE PUCK NOW <<<", flush=True)
        beep(3)
    dev = await find(timeout=120)
    if not dev:
        print("no puck", flush=True)
        return
    print(f"found {dev.address}; mode={MODE}", flush=True)
    pending = []
    buttons = {"n": 0}

    def on(_c, d):
        b = bytes(d)
        if b.startswith(dlock.HEADER):
            pending.append(dlock.Challenge(b))
            print(f"  CHALLENGE {len(b)} B (f3={f3_len(b)} B): {b.hex()}", flush=True)
        elif b[:2] == b"\x23\x08":
            buttons["n"] += 1
        else:
            print(f"  rx {b.hex()[:60]}", flush=True)

    async with BleakClient(dev, timeout=20) as c:
        for ch in (ASYNC, TX):
            try:
                await c.start_notify(ch, on)
            except Exception:
                pass
        if MODE in ("rideon", "ff0400"):
            await c.write_gatt_char(RX, b"RideOn", response=False)
            print("  sent RideOn", flush=True)
        if MODE == "ff0400":
            await asyncio.sleep(0.3)
            await c.write_gatt_char(RX, b"\xff\x04\x00", response=False)
            print("  sent ff0400", flush=True)
        waited = 0.0
        while not pending and waited < WAIT:
            await asyncio.sleep(0.25)
            waited += 0.25
        if not pending:
            print(f"  no challenge in {WAIT:.0f}s; button frames={buttons['n']}", flush=True)
            return
        beep(1)
        ch = pending[-1]
        t = time.monotonic()
        res = await dlock.sign(ch, TOKEN)
        print(f"  sign -> {res.describe()}  [{time.monotonic()-t:.2f}s]", flush=True)
        if not res.signed:
            return
        for _ in range(3):
            beep(2)
        print("  >>> PRESS BUTTONS for each window <<<", flush=True)

        def prompt(name, secs):
            print(f"  window {name}: press now ({secs:.0f}s)", flush=True)
            beep(2)

        results = await dlock.push(c, res.body, listen=9.0, seen=buttons, subscribe=False,
                                   prompt=prompt)
        for p in results:
            print(f"  {p.framing:14s} wrote={p.wrote} buttons={p.buttons} frames={p.frames} err={p.error}",
                  flush=True)
        if any(p.buttons for p in results):
            print("  *** UNLOCK ACCEPTED ***", flush=True)
            for _ in range(4):
                beep(3)


asyncio.run(main())
