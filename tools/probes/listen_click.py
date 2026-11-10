"""Long-running listener: connect the right Click, send RideOn, log every frame.

No 0100 writes, no ff 04 00: this only answers "does a press produce any frame
at all right now?" Log: /tmp/listen_right.log
"""
import asyncio, sys, time
from bleak import BleakScanner, BleakClient

ASYNC = "00000002-19ca-4651-86e5-fa29dcdd09d1"
RX = "00000003-19ca-4651-86e5-fa29dcdd09d1"
TX = "00000004-19ca-4651-86e5-fa29dcdd09d1"
SIDE = sys.argv[1] if len(sys.argv) > 1 else "right"
MFR = {"left": 0x0B, "right": 0x0A}[SIDE]
LOG = f"/tmp/listen_{SIDE}.log"


def log(msg):
    print(msg, flush=True)
    with open(LOG, "a", buffering=1) as f:
        f.write(msg + "\n")


async def scan(timeout=600):
    found = {}

    def cb(d, a):
        m = a.manufacturer_data.get(0x094A)
        if m and m[0] == MFR:
            found[d.address] = d

    log(f"{time.strftime('%H:%M:%S')} scanning for {SIDE} Click ...")
    end = time.time() + timeout
    while time.time() < end and not found:
        async with BleakScanner(cb):
            for _ in range(20):
                await asyncio.sleep(0.5)
                if found:
                    break
    return next(iter(found.values()), None)


async def main():
    dev = await scan()
    if not dev:
        log("no Click seen")
        return
    log(f"{time.strftime('%H:%M:%S')} found {dev.address}")
    t0 = time.monotonic()

    def on(_c, d):
        b = bytes(d)
        tag = "BUTTON" if b[:2] == b"\x23\x08" else ("CHALLENGE" if b.startswith(b"\xff\x03") else "rx")
        log(f"  {time.monotonic()-t0:7.2f}s {tag} {b.hex()}")

    async with BleakClient(dev, timeout=15) as c:
        for ch in (ASYNC, TX):
            try:
                await c.start_notify(ch, on)
            except Exception:
                pass
        await c.write_gatt_char(RX, b"RideOn", response=False)
        log(f"{time.strftime('%H:%M:%S')} connected, RideOn sent; PRESS THE {SIDE.upper()} CLICK NOW (listening 240 s)")
        await asyncio.sleep(240)
    log("done")


asyncio.run(main())
