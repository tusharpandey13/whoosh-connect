# Connect to all Zwift Clicks concurrently, subscribe to every notify/indicate char,
# do RideOn handshake, log all traffic with timestamps for DUR seconds.
import asyncio, sys, time, json
from bleak import BleakScanner, BleakClient
B = "-19ca-4651-86e5-fa29dcdd09d1"
TX = "00000003" + B
DUR = int(sys.argv[1]) if len(sys.argv) > 1 else 60
OUT = sys.argv[2] if len(sys.argv) > 2 else "probe2.jsonl"
# optional extra hex writes to 0003 after RideOn, comma-separated, e.g. ff0400
EXTRA = [bytes.fromhex(x) for x in sys.argv[3].split(",")] if len(sys.argv) > 3 else []
f = open(OUT, "w")
def rec(**kw):
    kw["t"] = round(time.time(), 3); f.write(json.dumps(kw) + "\n"); f.flush()
    print(kw)
async def run(d):
    async with BleakClient(d) as c:
        tag = d.address[:4]
        for s in c.services:
            for ch in s.characteristics:
                if ch.uuid.endswith(B) and "read" in ch.properties:
                    try: rec(dev=tag, ev="read", ch=ch.uuid[:8], hex=(await c.read_gatt_char(ch)).hex())
                    except Exception as e: rec(dev=tag, ev="readerr", ch=ch.uuid[:8], err=str(e))
                if ch.uuid.endswith(B) and ({"notify", "indicate"} & set(ch.properties)):
                    u = ch.uuid[:8]
                    try: await c.start_notify(ch, lambda _, data, u=u: rec(dev=tag, ev="rx", ch=u, hex=data.hex()))
                    except Exception as e: rec(dev=tag, ev="suberr", ch=u, err=str(e))
        await c.write_gatt_char(TX, b"RideOn", response=False)
        rec(dev=tag, ev="tx", ch="00000003", hex=b"RideOn".hex())
        for x in EXTRA:
            await asyncio.sleep(0.3)
            await c.write_gatt_char(TX, x, response=False)
            rec(dev=tag, ev="tx", ch="00000003", hex=x.hex())
        await asyncio.sleep(DUR)
async def main():
    found = {}
    def cb(d, adv):
        if 0x094a in adv.manufacturer_data and d.address not in found:
            found[d.address] = d
            rec(dev=d.address[:4], ev="adv", addr=d.address, rssi=adv.rssi, mfr=adv.manufacturer_data[0x094a].hex())
    async with BleakScanner(cb):
        for _ in range(180):
            await asyncio.sleep(0.5)
            if len(found) >= 2: break
    rec(ev="start", n=len(found), dur=DUR)
    await asyncio.gather(*(run(d) for d in found.values()), return_exceptions=True)
    rec(ev="end")
asyncio.run(main())
