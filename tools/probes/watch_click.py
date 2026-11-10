"""Reconnecting listener: keeps connecting to one Click and logs every frame.

Survives a battery power-cycle and reconnect. Log: /tmp/watch_<side>.log
"""
import asyncio, sys, time
from bleak import BleakScanner, BleakClient

ASYNC = "00000002-19ca-4651-86e5-fa29dcdd09d1"
RX = "00000003-19ca-4651-86e5-fa29dcdd09d1"
TX = "00000004-19ca-4651-86e5-fa29dcdd09d1"
SIDE = sys.argv[1] if len(sys.argv) > 1 else "right"
DUR = float(sys.argv[2]) if len(sys.argv) > 2 else 600.0
MFR = {"left": 0x0B, "right": 0x0A}[SIDE]
LOG = f"/tmp/watch_{SIDE}.log"


def log(msg):
    line = f"{time.strftime('%H:%M:%S')} {msg}"
    print(line, flush=True)
    with open(LOG, "a", buffering=1) as f:
        f.write(line + "\n")


async def find(timeout=20):
    found = {}

    def cb(d, a):
        m = a.manufacturer_data.get(0x094A)
        if m and m[0] == MFR:
            found[d.address] = d

    end = time.time() + timeout
    while time.time() < end and not found:
        async with BleakScanner(cb):
            for _ in range(10):
                await asyncio.sleep(0.5)
                if found:
                    break
    return next(iter(found.values()), None)


async def main():
    end_all = time.time() + DUR
    log(f"=== watching {SIDE} for {int(DUR)}s (log {LOG})")
    while time.time() < end_all:
        dev = await find()
        if not dev:
            log("not advertising; will retry")
            continue
        log(f"--- found {dev.address}, connecting")
        t0 = time.monotonic()
        try:
            async with BleakClient(dev, timeout=15) as c:
                def on(_c, d):
                    b = bytes(d)
                    tag = ("BUTTON" if b[:2] == b"\x23\x08"
                           else "CHALLENGE" if b.startswith(b"\xff\x03") else "rx")
                    log(f"    {time.monotonic()-t0:7.2f}s {tag} {b.hex()}")

                for ch in (ASYNC, TX):
                    try:
                        await c.start_notify(ch, on)
                    except Exception as e:
                        log(f"    notify {ch[:8]} failed: {e!r}")
                await c.write_gatt_char(RX, b"RideOn", response=False)
                log("    RideOn sent")
                while c.is_connected and time.time() < end_all:
                    await asyncio.sleep(1.0)
                log("    link closed, reconnecting")
        except Exception as e:
            log(f"--- connect failed: {e!r}")
        await asyncio.sleep(2.0)
    log("=== watch over")


asyncio.run(main())
