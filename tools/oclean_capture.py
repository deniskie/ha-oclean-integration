#!/usr/bin/env python3
"""
Capture raw BLE notifications from an Oclean device and print the hex bytes.

Purpose: collect ground-truth data for adding new tests to tests/test_real_data.py.

Usage:
    # Scan for nearby Oclean devices and show them
    python3 tools/oclean_capture.py --scan-only

    # Auto-connect to first Oclean device found
    python3 tools/oclean_capture.py

    # Connect to a specific device by BLE address (CoreBluetooth UUID on macOS,
    # MAC address on Linux/Windows)
    python3 tools/oclean_capture.py --address "B5B3C1B0-8CDC-4F06-9B54-8C23174EE8AA"

    # Connect to a specific device by name
    python3 tools/oclean_capture.py --name "Oclean X"

    # Save captured data to a JSON file  (do NOT use > redirection!)
    python3 tools/oclean_capture.py --output captured.json

    # Listen longer (default: 10 seconds after connection)
    python3 tools/oclean_capture.py --duration 30

Requirements:
    pip install bleak

Output: for each BLE notification received, prints:
  [HH:MM:SS] CHAR <uuid-suffix> raw: <hex>  →  <parsed fields>

At the end, prints a summary of all captures and (if --output) saves a JSON file
with all raw hex strings ready to paste into tests/test_real_data.py.
"""
from __future__ import annotations

import argparse
import asyncio
import datetime
import json
import struct
import sys
from typing import Any
import time

try:
    from bleak import BleakClient, BleakScanner
    from bleak.backends.device import BLEDevice
except ImportError:
    print("Install bleak first:  pip install bleak")
    sys.exit(1)

# ---------------------------------------------------------------------------
# BLE UUIDs and commands (from custom_components/oclean_ble/const.py)
# ---------------------------------------------------------------------------

OCLEAN_SERVICE_UUID   = "8082caa8-41a6-4021-91c6-56f9b954cc18"
BATTERY_CHAR_UUID     = "00002a19-0000-1000-8000-00805f9b34fb"
READ_NOTIFY_CHAR_UUID = "5f78df94-798c-46f5-990a-855b673fbb86"
WRITE_CHAR_UUID       = "9d84b9a3-000c-49d8-9183-855b673fbb85"
SEND_BRUSH_CMD_UUID   = "5f78df94-798c-46f5-990a-855b673fbb89"
RECEIVE_BRUSH_UUID    = "5f78df94-798c-46f5-990a-855b673fbb90"
CHANGE_INFO_UUID      = "6c290d2e-1c03-aca1-ab48-a9b908bae79e"

CMD_QUERY_STATUS          = bytes.fromhex("0303")
CMD_DEVICE_INFO           = bytes.fromhex("0202")
CMD_QUERY_RUNNING_DATA    = bytes.fromhex("0308")    # Type-0 devices
CMD_QUERY_RUNNING_DATA_T1 = bytes.fromhex("0307")    # Type-1 (Oclean X)
CMD_QUERY_RUNNING_DATA_NEXT = bytes.fromhex("0309")  # Follow-up page

# Characteristics to subscribe to (attempt all; silently skip if not found)
NOTIFY_CHARS = [
    READ_NOTIFY_CHAR_UUID,
    RECEIVE_BRUSH_UUID,
    CHANGE_INFO_UUID,
]


# ---------------------------------------------------------------------------
# Minimal parser – mirrors parser.py logic without the HA import chain
# ---------------------------------------------------------------------------

def _describe(data: bytes) -> str:
    """Return a short human-readable description of a known notification."""
    if len(data) < 2:
        return "(too short)"
    t = data[:2]
    payload = data[2:]

    if t == bytes.fromhex("0303"):
        if len(payload) >= 4:
            batt = payload[3]
            if 0 <= batt <= 100:
                return f"STATE  battery={batt}%"
        return "STATE (short payload)"

    if t == bytes.fromhex("0307"):
        if len(payload) >= 14:
            year  = payload[5] + 2000
            month = payload[6]
            day   = payload[7]
            hour  = payload[8]
            minute = payload[9]
            second = payload[10]
            metric = payload[13]
            score  = max(1, min(100, metric - 30)) if metric > 0 else 0
            return (
                f"0307 session  ts={year}-{month:02d}-{day:02d} "
                f"{hour:02d}:{minute:02d}:{second:02d}  "
                f"byte13={metric}  score={score}  (byte13 purpose unconfirmed)"
            )
        return "0307 (short payload)"

    if t == bytes.fromhex("0308"):
        if len(payload) >= 2 and payload[0] == 0 and payload[1] >= 32 and len(payload) >= payload[1]:
            # Extended format
            year  = payload[2] + 2000
            month = payload[3]
            day   = payload[4]
            hour  = payload[5]
            minute = payload[6]
            second = payload[7]
            p_num  = payload[8]
            dur    = int.from_bytes(payload[9:11], "big")
            score  = payload[28]
            scheme = payload[29]
            areas  = list(payload[20:28])
            zones  = sum(1 for v in areas if v > 0)
            return (
                f"0308 extended  ts={year}-{month:02d}-{day:02d} "
                f"{hour:02d}:{minute:02d}:{second:02d}  "
                f"pNum={p_num}  dur={dur}s  score={score}  "
                f"schemeType={scheme}  zones={zones}/8  areas={areas}"
            )
        if len(payload) >= 18:
            # Simple format
            year   = payload[0] + 2000
            month  = payload[1]
            day    = payload[2]
            hour   = payload[3]
            minute = payload[4]
            second = payload[5]
            p_num  = payload[8]
            blunt  = int.from_bytes(payload[14:16], "little")
            praw   = int.from_bytes(payload[16:18], "little")
            return (
                f"0308 simple  ts={year}-{month:02d}-{day:02d} "
                f"{hour:02d}:{minute:02d}:{second:02d}  "
                f"pNum={p_num}  bluntTeeth={blunt}  pressureRaw={praw}"
            )
        return "0308 (short payload)"

    if t == bytes.fromhex("0202"):
        return "0202 device-info ACK"

    if t == bytes.fromhex("0340"):
        return "0340 K3GUIDE real-time zone"

    # JSON fallback
    try:
        text = data.decode("utf-8").strip()
        obj = json.loads(text)
        return f"JSON: {obj}"
    except Exception:
        pass

    return f"UNKNOWN type 0x{t.hex().upper()}"


# ---------------------------------------------------------------------------
# Scanner helpers
# ---------------------------------------------------------------------------

async def scan_for_oclean(duration: float = 5.0) -> list[BLEDevice]:
    """Scan for Oclean devices and return a list of found devices."""
    print(f"Scanning for {duration:.0f} seconds …")
    # return_adv=True gives {address: (BLEDevice, AdvertisementData)} – the only
    # reliable way to access service UUIDs in bleak ≥ 0.20 (metadata was removed).
    devices_and_adv = await BleakScanner.discover(timeout=duration, return_adv=True)
    oclean = []
    for device, adv in devices_and_adv.values():
        name = device.name or adv.local_name or ""
        service_uuids = [u.lower() for u in (adv.service_uuids or [])]
        if (
            name.lower().startswith("oclean")
            or OCLEAN_SERVICE_UUID.lower() in service_uuids
        ):
            oclean.append(device)
    return oclean


# ---------------------------------------------------------------------------
# Main capture loop
# ---------------------------------------------------------------------------

async def capture(
    address: str | None,
    name: str | None,
    duration: float,
    output: str | None,
) -> None:
    # --- Find device (keep scanner active until connection succeeds) ---
    # On macOS/CoreBluetooth, BLEDevice objects from discover() expire quickly.
    # find_device_by_filter() keeps the scanner running and returns a "live"
    # device that CoreBluetooth can connect to immediately.

    if address:
        print(f"Scanning for device {address} …")
        device = await BleakScanner.find_device_by_address(address, timeout=15)
        if device is None:
            print(f"  ✗ Device {address} not found. Make sure it is in range.")
            return
    else:
        filter_name = name.lower() if name else None

        def _is_oclean(d: BLEDevice, adv: Any) -> bool:
            dev_name = (d.name or getattr(adv, "local_name", None) or "").lower()
            service_uuids = [u.lower() for u in getattr(adv, "service_uuids", None) or []]
            name_match = dev_name.startswith("oclean")
            uuid_match = OCLEAN_SERVICE_UUID.lower() in service_uuids
            if filter_name:
                return (name_match or uuid_match) and filter_name in dev_name
            return name_match or uuid_match

        print("Scanning for Oclean device (press power button once if not found) …")
        device = await BleakScanner.find_device_by_filter(_is_oclean, timeout=15)
        if device is None:
            print("  ✗ No Oclean device found nearby.")
            print("    Make sure the toothbrush is awake (press power button once).")
            return

    print(f"  Found: {device.name!r}  address={device.address}")
    print(f"Connecting …")

    # --- Capture storage ---
    captures: list[dict] = []

    def _on_notify(char_uuid: str, data: bytes) -> None:
        ts = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]
        short_uuid = char_uuid[-8:]
        desc = _describe(data)
        print(f"  [{ts}] CHAR …{short_uuid}  raw: {data.hex()}  →  {desc}")
        captures.append({
            "ts": datetime.datetime.now().isoformat(),
            "characteristic": char_uuid,
            "raw_hex": data.hex(),
            "description": desc,
        })

    # --- Connect and subscribe ---
    async with BleakClient(device, timeout=30) as client:
        print(f"  ✓ Connected (address: {client.address})\n")

        # Delay after connect: gives the BLE stack time to finish GATT service
        # discovery before we start issuing commands (matches coordinator behaviour).
        await asyncio.sleep(2.0)

        # --- Send commands and wait for responses ---
        async def send(uuid: str, cmd: bytes, label: str) -> None:
            ts_str = datetime.datetime.now().strftime("%H:%M:%S")
            try:
                # response=True (Write With Response) is required – the device
                # ignores Write Without Response commands and never sends notifications.
                await client.write_gatt_char(uuid, cmd, response=True)
                print(f"  [{ts_str}] SEND  {label}  ({cmd.hex()})  →  …{uuid[-8:]}")
            except Exception as err:
                print(f"  [{ts_str}] SEND  {label}  ✗  {err}")

        # 1. Time calibration (wake up + sync clock) – BEFORE subscribing so the
        #    device is ready to send notifications when we subscribe.
        ts_bytes = struct.pack(">I", int(time.time()))
        try:
            await client.write_gatt_char(
                WRITE_CHAR_UUID,
                bytes.fromhex("020E") + ts_bytes,
                response=True,
            )
            now_str = datetime.datetime.now().strftime("%H:%M:%S")
            print(f"  [{now_str}] SEND  TIME_CALIBRATE  (020e{ts_bytes.hex()})  →  …{WRITE_CHAR_UUID[-8:]}")
        except Exception as err:
            print(f"  (TIME_CALIBRATE failed: {err})")

        # 2. Subscribe to notify characteristics (must happen before query commands)
        subscribed: list[str] = []
        all_notify_chars = NOTIFY_CHARS + [SEND_BRUSH_CMD_UUID]
        for uuid in all_notify_chars:
            try:
                await client.start_notify(uuid, lambda _, d, u=uuid: _on_notify(u, d))
                subscribed.append(uuid[-12:])
            except Exception:
                pass  # characteristic may not exist on this device model
        print(f"  Subscribed to: {', '.join(subscribed) if subscribed else '(none found)'}\n")

        # 3. Query device info (triggers 0202 ACK)
        await send(WRITE_CHAR_UUID, CMD_DEVICE_INFO, "CMD_DEVICE_INFO")
        await asyncio.sleep(1)

        # 4. Query status (triggers 0303 STATE → battery)
        await send(WRITE_CHAR_UUID, CMD_QUERY_STATUS, "CMD_QUERY_STATUS")
        await asyncio.sleep(2)

        # 5. Query running data – Type-0 (0308 extended, most Oclean models)
        await send(WRITE_CHAR_UUID, CMD_QUERY_RUNNING_DATA, "CMD_QUERY_RUNNING_DATA")
        await asyncio.sleep(2)

        # 6. Query running data – Type-1 (0307, Oclean X)
        #    Sent to SEND_BRUSH_CMD_UUID; response arrives on RECEIVE_BRUSH_UUID
        await send(SEND_BRUSH_CMD_UUID, CMD_QUERY_RUNNING_DATA_T1, "CMD_QUERY_RUNNING_DATA_T1")
        await asyncio.sleep(2)

        # 6. Read battery characteristic directly
        try:
            batt_raw = await client.read_gatt_char(BATTERY_CHAR_UUID)
            batt = batt_raw[0] if batt_raw else None
            ts = datetime.datetime.now().strftime("%H:%M:%S")
            print(f"  [{ts}] READ  BATTERY_CHAR  raw: {batt_raw.hex()}  →  battery={batt}%")
            captures.append({
                "ts": datetime.datetime.now().isoformat(),
                "characteristic": BATTERY_CHAR_UUID,
                "raw_hex": batt_raw.hex(),
                "description": f"BATTERY READ battery={batt}%",
            })
        except Exception as err:
            print(f"  (Battery read failed: {err})")

        # 7. Wait for any remaining notifications
        if duration > 0:
            print(f"\n  Waiting {duration:.0f} s for additional notifications …")
            await asyncio.sleep(duration)

    # --- Summary ---
    print(f"\n{'='*60}")
    print(f"Captured {len(captures)} notification(s) total.\n")

    if captures:
        print("Paste into tests/test_real_data.py:\n")
        for i, c in enumerate(captures, 1):
            print(f'  # {i}. {c["description"]}')
            print(f'  RAW = bytes.fromhex("{c["raw_hex"]}")')
            print()

    # --- Save JSON ---
    if output:
        with open(output, "w", encoding="utf-8") as f:
            json.dump(captures, f, indent=2, ensure_ascii=False)
        print(f"Saved to {output}")

    print("=" * 60)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Capture raw BLE data from an Oclean device",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--address", help="BLE address / CoreBluetooth UUID of the device")
    parser.add_argument("--name", help="Device name (substring match, e.g. 'Oclean X')")
    parser.add_argument("--scan-only", action="store_true", help="Only scan and list nearby Oclean devices")
    parser.add_argument("--duration", type=float, default=10.0,
                        help="Seconds to wait for notifications after sending commands (default: 10)")
    parser.add_argument("--output", metavar="FILE",
                        help="Save all captures to a JSON file")
    args = parser.parse_args()

    if args.scan_only:
        devices = await scan_for_oclean(duration=8.0)
        if devices:
            print(f"\nFound {len(devices)} Oclean device(s):")
            for d in devices:
                print(f"  {d.name!r}  address={d.address}")
        else:
            print("No Oclean devices found.")
            print("Make sure the toothbrush is awake (press power button once).")
        return

    await capture(
        address=args.address,
        name=args.name,
        duration=args.duration,
        output=args.output,
    )


if __name__ == "__main__":
    asyncio.run(main())
