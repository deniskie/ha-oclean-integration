#!/usr/bin/env python3
"""
Passive BLE advertisement monitor for Oclean devices.

Purpose: Understand when and how the Oclean toothbrush advertises –
  particularly whether advertisement data changes during brushing –
  in order to implement event-driven (push) sync instead of polling.

Usage:
    # Monitor all nearby Oclean devices passively (no connection)
    python3 tools/oclean_adv_monitor.py

    # Monitor a specific device by address
    python3 tools/oclean_adv_monitor.py --address "70:28:45:69:E4:A4"

    # Monitor a specific device by name
    python3 tools/oclean_adv_monitor.py --name "Oclean X"

    # Save all captured advertisements to JSON for analysis
    python3 tools/oclean_adv_monitor.py --output adv_log.json

    # Increase scan sensitivity (default: active scanning every 100ms)
    python3 tools/oclean_adv_monitor.py --interval 0.05

Press Ctrl+C to stop.  The script prints a change-only log by default so the
terminal stays readable.  Use --verbose to see every repeated advertisement.

Requirements:
    pip install bleak
"""
from __future__ import annotations

import argparse
import asyncio
import datetime
import json
import sys
from dataclasses import dataclass, field, asdict
from typing import Any

try:
    from bleak import BleakScanner
    from bleak.backends.device import BLEDevice
    from bleak.backends.scanner import AdvertisementData
except ImportError:
    print("Install bleak first:  pip install bleak")
    sys.exit(1)

OCLEAN_SERVICE_UUID = "8082caa8-41a6-4021-91c6-56f9b954cc18"


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class AdvSnapshot:
    """One advertisement packet captured from the device."""
    ts: str
    address: str
    name: str
    rssi: int
    service_uuids: list[str]
    manufacturer_data: dict[str, str]   # company_id (hex) → data (hex)
    service_data: dict[str, str]        # uuid → data (hex)
    tx_power: int | None
    raw_summary: str                    # human-readable one-liner


def _snapshot(device: BLEDevice, adv: AdvertisementData) -> AdvSnapshot:
    name = device.name or adv.local_name or ""
    mfr = {
        f"0x{k:04X}": v.hex()
        for k, v in (adv.manufacturer_data or {}).items()
    }
    svc_data = {
        uuid: data.hex()
        for uuid, data in (adv.service_data or {}).items()
    }
    parts: list[str] = [f"rssi={adv.rssi}"]
    if mfr:
        parts.append(f"mfr={mfr}")
    if svc_data:
        parts.append(f"svc_data={svc_data}")
    if adv.tx_power is not None:
        parts.append(f"tx_power={adv.tx_power}")
    return AdvSnapshot(
        ts=datetime.datetime.now().isoformat(timespec="milliseconds"),
        address=device.address,
        name=name,
        rssi=adv.rssi,
        service_uuids=[u.lower() for u in (adv.service_uuids or [])],
        manufacturer_data=mfr,
        service_data=svc_data,
        tx_power=adv.tx_power,
        raw_summary="  ".join(parts),
    )


def _adv_fingerprint(snap: AdvSnapshot) -> str:
    """Returns a string that changes only when the advertisement payload changes.

    RSSI fluctuates normally – excluded from the fingerprint so we only log
    real payload changes.
    """
    return "|".join([
        str(sorted(snap.service_uuids)),
        str(sorted(snap.manufacturer_data.items())),
        str(sorted(snap.service_data.items())),
        str(snap.tx_power),
    ])


# ---------------------------------------------------------------------------
# Scanner logic
# ---------------------------------------------------------------------------

class AdvMonitor:
    def __init__(
        self,
        filter_address: str | None,
        filter_name: str | None,
        verbose: bool,
        output: str | None,
    ):
        self._filter_address = filter_address.lower() if filter_address else None
        self._filter_name = filter_name.lower() if filter_name else None
        self._verbose = verbose
        self._output = output

        self._all_logs: list[dict] = []
        self._last_fingerprint: dict[str, str] = {}   # address → fingerprint
        self._last_seen: dict[str, str] = {}           # address → ts
        self._first_seen: dict[str, str] = {}          # address → ts
        self._seen_count: dict[str, int] = {}

    def _matches(self, device: BLEDevice, adv: AdvertisementData) -> bool:
        name = (device.name or adv.local_name or "").lower()
        service_uuids = [u.lower() for u in (adv.service_uuids or [])]
        is_oclean = name.startswith("oclean") or OCLEAN_SERVICE_UUID.lower() in service_uuids
        if not is_oclean:
            return False
        if self._filter_address and device.address.lower() != self._filter_address:
            return False
        if self._filter_name and self._filter_name not in name:
            return False
        return True

    def callback(self, device: BLEDevice, adv: AdvertisementData) -> None:
        if not self._matches(device, adv):
            return

        snap = _snapshot(device, adv)
        addr = snap.address
        fp = _adv_fingerprint(snap)

        # Track first/last seen
        now_ts = snap.ts
        if addr not in self._first_seen:
            self._first_seen[addr] = now_ts
        self._last_seen[addr] = now_ts
        self._seen_count[addr] = self._seen_count.get(addr, 0) + 1

        # Determine if payload changed
        prev_fp = self._last_fingerprint.get(addr)
        changed = prev_fp != fp
        self._last_fingerprint[addr] = fp

        record = asdict(snap)
        record["changed"] = changed
        record["seen_count"] = self._seen_count[addr]
        self._all_logs.append(record)

        # Print
        if changed or self._verbose or addr not in self._last_fingerprint:
            tag = "[CHANGE]" if (changed and prev_fp is not None) else "[FIRST] " if prev_fp is None else "      "
            print(f"  {snap.ts[11:]}  {tag}  {addr}  {snap.name!r:12s}  {snap.raw_summary}")
        else:
            # Always print count so user knows it's alive
            count = self._seen_count[addr]
            if count % 50 == 0:
                print(f"  {snap.ts[11:]}  [seen {count:4d}×]  {addr}  {snap.name!r}  (no change)")

    async def run(self) -> None:
        print("=" * 70)
        print("Oclean BLE Advertisement Monitor")
        print("Logs payload changes. Press Ctrl+C to stop.")
        print("=" * 70)
        if self._filter_address:
            print(f"  Filter: address = {self._filter_address}")
        if self._filter_name:
            print(f"  Filter: name contains '{self._filter_name}'")
        print()

        async with BleakScanner(
            detection_callback=self.callback,
            # scanning_mode="passive",  # uncomment on Linux for truly passive
        ):
            try:
                while True:
                    await asyncio.sleep(1)
            except asyncio.CancelledError:
                pass
            except KeyboardInterrupt:
                pass

        self._print_summary()
        if self._output:
            self._save_json()

    def _print_summary(self) -> None:
        print("\n" + "=" * 70)
        print(f"Stopped. Captured {len(self._all_logs)} advertisement(s).\n")
        for addr in sorted(self._first_seen):
            name = next(
                (r["name"] for r in reversed(self._all_logs) if r["address"] == addr), "?"
            )
            changes = sum(1 for r in self._all_logs if r["address"] == addr and r["changed"])
            count = self._seen_count.get(addr, 0)
            print(f"  {addr}  {name!r}")
            print(f"    first seen : {self._first_seen[addr]}")
            print(f"    last seen  : {self._last_seen[addr]}")
            print(f"    total pkts : {count}")
            print(f"    payload Δ  : {changes}")
            # Print last known payload
            last = next(
                (r for r in reversed(self._all_logs) if r["address"] == addr), None
            )
            if last:
                print(f"    last mfr   : {last['manufacturer_data']}")
                print(f"    last svc   : {last['service_data']}")
                print(f"    svc uuids  : {last['service_uuids']}")
            print()
        print("=" * 70)

    def _save_json(self) -> None:
        with open(self._output, "w", encoding="utf-8") as f:  # type: ignore[arg-type]
            json.dump(self._all_logs, f, indent=2, ensure_ascii=False)
        print(f"Saved {len(self._all_logs)} records to {self._output}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Passively monitor Oclean BLE advertisements",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--address", help="Filter by BLE address / CoreBluetooth UUID")
    parser.add_argument("--name", help="Filter by device name (substring)")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Print every advertisement, not just changes")
    parser.add_argument("--output", metavar="FILE",
                        help="Save all advertisement records to JSON")
    args = parser.parse_args()

    monitor = AdvMonitor(
        filter_address=args.address,
        filter_name=args.name,
        verbose=args.verbose,
        output=args.output,
    )
    await monitor.run()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
