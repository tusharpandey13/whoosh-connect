# Passive Zwift Click spy: connect to the pucks, subscribe to 0002 (notify) and
# 0004 (indicate), NEVER write anything. Whatever the puck sends is driven by the
# real Zwift app's handshake, so this shows the device side of the Zwift unlock.
#
#   .venv/bin/python scripts/spy.py [DUR=600] [OUT=raw/ble/spy.jsonl] [--write]
#
# --write sends RideOn after connect (make the puck talk to us instead of Zwift).
import asyncio, json, sys, time
from bleak import BleakScanner, BleakClient

B = "-19ca-4651-86e5-fa29dcdd09d1"
ASYNC, SYNC_RX, SYNC_TX = "00000002" + B, "00000003" + B, "00000004" + B
MFR = 0x094A

DUR = int(sys.argv[1]) if len(sys.argv) > 1 and not sys.argv[1].startswith("-") else 600
OUT = sys.argv[2] if len(sys.argv) > 2 and not sys.argv[2].startswith("-") else "raw/ble/spy.jsonl"
WRITE_RIDEON = "--write" in sys.argv

f = open(OUT, "w")


def rec(**kw):
    kw["t"] = round(time.time(), 3)
    f.write(json.dumps(kw) + "\n")
    f.flush()
    print(kw, flush=True)


async def run(d):
    tag = d.address[:4]
    try:
        async with BleakClient(d) as c:
            rec(dev=tag, ev="connected", addr=d.address)
            for ch in (ASYNC, SYNC_TX):
                await c.start_notify(ch, lambda _, data, u=ch[:8]: rec(dev=tag, ev="rx", ch=u, hex=data.hex()))
                rec(dev=tag, ev="sub", ch=ch[:8])
            try:
                rec(dev=tag, ev="read", ch="00000004", hex=(await c.read_gatt_char(SYNC_TX)).hex())
            except Exception as e:
                rec(dev=tag, ev="readerr", err=str(e))
            if WRITE_RIDEON:
                await c.write_gatt_char(SYNC_RX, b"RideOn", response=False)
                rec(dev=tag, ev="tx", ch="00000003", hex=b"RideOn".hex())
            await asyncio.sleep(DUR)
    except Exception as e:
        rec(dev=tag, ev="error", err=str(e))


async def main():
    found = {}

    def cb(d, adv):
        if MFR in adv.manufacturer_data and d.address not in found:
            found[d.address] = d
            rec(dev=d.address[:4], ev="adv", addr=d.address, rssi=adv.rssi,
                mfr=adv.manufacturer_data[MFR].hex())

    async with BleakScanner(cb):
        for _ in range(2400):
            await asyncio.sleep(0.5)
            if len(found) >= 2:
                break
    rec(ev="start", n=len(found), dur=DUR, write_rideon=WRITE_RIDEON)
    await asyncio.gather(*(run(d) for d in found.values()), return_exceptions=True)
    rec(ev="end")


asyncio.run(main())
