"""Clean 02 02 test: RideOn, then 02 02 on char 0100, then a real press window.

No ff 04 00 anywhere (it mutes a locked unit, which confounded every earlier try).

Success = `23 08` button frames still arriving late in the window (>15 s) with no
`ff 03` challenge muting them. A locked unit bursts idle frames, takes the
challenge at ~4-8 s, and is silent ~2.4 s later.
"""
import asyncio, os, subprocess, sys, time
sys.path.insert(0, "src")
from bleak import BleakClient, BleakScanner

ASYNC = "00000002-19ca-4651-86e5-fa29dcdd09d1"
RX = "00000003-19ca-4651-86e5-fa29dcdd09d1"
TX = "00000004-19ca-4651-86e5-fa29dcdd09d1"
CH0100 = "00000100-19ca-4651-86e5-fa29dcdd09d1"
MFR = 0x0B  # left
WINDOW = float(os.environ.get("WINDOW", "30"))
SLEEP = float(os.environ.get("SLEEP", "0"))
NOPAYLOAD = os.environ.get("NOPAYLOAD") == "1"


def beep(n=1):
    subprocess.run(["osascript", "-e", f"beep {n}"], check=False)


async def find(timeout=120):
    found = {}

    def cb(d, a):
        m = a.manufacturer_data.get(0x094A)
        if m and m[0] == MFR:
            found[d.address] = d

    print("scanning for the left Click (it wakes on a press)...", flush=True)
    end = time.time() + timeout
    while time.time() < end and not found:
        async with BleakScanner(cb):
            for _ in range(10):
                await asyncio.sleep(0.5)
                if found:
                    break
    return next(iter(found.values()), None)


async def main():
    if SLEEP:
        print(f"quiet {SLEEP:.0f}s first", flush=True)
        await asyncio.sleep(SLEEP)
    print(">>> 3 BEEPS: PRESS THE LEFT CLICK NOW <<<", flush=True)
    beep(3)
    dev = await find()
    if not dev:
        print("no puck", flush=True)
        return
    print(f"found {dev.address}", flush=True)

    log = []
    t0 = None

    def on(_c, d):
        b = bytes(d)
        rel = time.monotonic() - t0
        log.append((rel, b))
        tag = ("BUTTON" if b[:2] == b"\x23\x08"
               else "CHALLENGE" if b.startswith(b"\xff\x03") else "rx")
        print(f"  {rel:6.2f}s {tag} {b.hex()[:64]}", flush=True)

    async with BleakClient(dev, timeout=20) as c:
        for ch in (ASYNC, TX, CH0100):
            try:
                await c.start_notify(ch, on)
            except Exception:
                pass
        t0 = time.monotonic()
        await c.write_gatt_char(RX, b"RideOn", response=False)
        if NOPAYLOAD:
            print(f"  {time.monotonic()-t0:6.2f}s tx RideOn only (CONTROL)", flush=True)
        else:
            await asyncio.sleep(0.5)
            await c.write_gatt_char(CH0100, bytes.fromhex("0202"), response=False)
            print(f"  {time.monotonic()-t0:6.2f}s tx RideOn + 0202@0100", flush=True)
        end = time.monotonic() + WINDOW
        while time.monotonic() < end:
            await asyncio.sleep(min(6.0, max(0.1, end - time.monotonic())))
            if time.monotonic() < end:
                beep(1)

    buttons = [b for _, b in log if b[:2] == b"\x23\x08"]
    challenge = next((r for r, b in log if b.startswith(b"\xff\x03")), None)
    late = [r for r, b in log if b[:2] == b"\x23\x08" and r > 15]
    print(f"\nRESULT {'CONTROL rideon only' if NOPAYLOAD else '0202@0100 (no ff0400)'}")
    print(f"  button frames: {len(buttons)}  (late, >15 s: {len(late)})")
    print(f"  challenge:     {'none' if challenge is None else str(round(challenge,1)) + ' s'}")
    print(f"  last frame at: {max(r for r, _ in log) if log else 0:.1f} s")
    if len(late) > 20:
        print("  *** buttons flowed late in the window (presses keep streaming) ***")
    elif buttons:
        print("  burst only: stream died during the window")
    else:
        print("  no button frames at all (press must have registered to count as silence)")


asyncio.run(main())
