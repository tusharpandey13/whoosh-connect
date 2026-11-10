# Connect to each Zwift Click, dump GATT, do the RideOn handshake, log button notifications.
import asyncio, sys, time
from bleak import BleakScanner, BleakClient
B = "-19ca-4651-86e5-fa29dcdd09d1"
ASYNC, TX, RX = "00000002"+B, "00000003"+B, "00000004"+B
DUR = int(sys.argv[1]) if len(sys.argv) > 1 else 25
async def probe(d):
    async with BleakClient(d) as c:
        print(f"\n## {d.name} {d.address}")
        for s in c.services:
            print(" svc", s.uuid, s.description)
            for ch in s.characteristics:
                v = ""
                if "read" in ch.properties:
                    try: v = (await c.read_gatt_char(ch)).hex()
                    except Exception as e: v = f"<{e}>"
                print("   ch", ch.uuid, ch.properties, v)
        log = lambda tag: (lambda _, data: print(f"  {time.strftime('%X')} {tag} {data.hex()}"))
        if any(ch.uuid == ASYNC for s in c.services for ch in s.characteristics):
            await c.start_notify(ASYNC, log("ASYNC"))
            await c.start_notify(RX, log("RX"))
            await c.write_gatt_char(TX, b"RideOn", response=False)
            print(f"  wrote RideOn; press buttons for {DUR}s")
            await asyncio.sleep(DUR)
async def main():
    found = {}
    def cb(d, adv):
        if (adv.local_name or "").startswith("Zwift") or 0x094a in adv.manufacturer_data:
            if d.address not in found:
                found[d.address] = d
                print("adv", adv.local_name, d.address, adv.rssi, {hex(k): v.hex() for k, v in adv.manufacturer_data.items()}, adv.service_uuids)
    async with BleakScanner(cb): await asyncio.sleep(15)
    for d in found.values():
        try: await probe(d)
        except Exception as e: print("ERR", d.address, e)
asyncio.run(main())
