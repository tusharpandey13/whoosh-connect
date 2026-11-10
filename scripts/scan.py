import asyncio
from bleak import BleakScanner
async def main():
    devs = await BleakScanner.discover(timeout=12, return_adv=True)
    for addr,(d,adv) in devs.items():
        n = adv.local_name or d.name or ""
        md = {hex(k): v.hex() for k,v in adv.manufacturer_data.items()}
        print("ANY", addr, repr(n))
        if "Zwift" in n or "VANRYSEL" in n or 0x094a in adv.manufacturer_data:
            print(addr, repr(n), adv.rssi, "svc=",adv.service_uuids, "mfr=",md, "sd=",{k:v.hex() for k,v in adv.service_data.items()})
asyncio.run(main())
