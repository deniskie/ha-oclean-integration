"""
Scans all BLE devices with advertisement data and highlights the Oclean service UUID.
Falls back to connecting unknown devices to read their GATT services.

Usage:  python3 oclean_find.py
"""
import asyncio
from bleak import BleakScanner, BleakClient

OCLEAN_SERVICE_UUID = "8082caa8-41a6-4021-91c6-56f9b954cc18"


async def main():
    print("Scanning for 10 s ...\n")
    devices_adv = await BleakScanner.discover(timeout=10, return_adv=True)

    candidates = []   # devices without a name – possibly Oclean
    found = None

    for addr, (dev, adv) in devices_adv.items():
        svc_uuids = [s.lower() for s in adv.service_uuids]
        is_oclean = OCLEAN_SERVICE_UUID.lower() in svc_uuids
        marker = "  <- OCLEAN ✓" if is_oclean else ""
        print(f"  {dev.name or '(no name)'!r:25s}  {addr}  rssi={adv.rssi:4d}  services={svc_uuids}{marker}")
        if is_oclean:
            found = dev
        elif dev.name is None:
            candidates.append((dev, adv.rssi))

    if found:
        print(f"\n✓ Oclean found: {found.address}")
        return

    # No service UUID match – connect to all unknown devices sorted by RSSI
    candidates.sort(key=lambda x: -x[1])
    print(f"\nNo direct UUID match. Connecting to {len(candidates)} unknown device(s) ...")

    for dev, rssi in candidates:
        print(f"\n  Trying {dev.address}  (rssi={rssi}) ...", end=" ", flush=True)
        try:
            async with BleakClient(dev, timeout=5) as client:
                svcs = [str(s.uuid).lower() for s in client.services]
                if OCLEAN_SERVICE_UUID.lower() in svcs:
                    print(f"\n\n  ✓ OCLEAN FOUND!  Address: {dev.address}")
                    print(f"  All services: {svcs}")
                    return
                print(f"not Oclean (services: {len(svcs)})")
        except Exception as e:
            print(f"connection failed: {e}")

    print("\nOclean not identified – is the brush turned on and in range?")


asyncio.run(main())
