# Developer Tools

These scripts allow you to test the BLE communication with an Oclean toothbrush
directly from your computer – **without a running Home Assistant instance**.

They were used during development to reverse-engineer the BLE protocol and
empirically confirm the byte layout of sensor data.

---

## Prerequisites

- **Python 3.11+**
- **bleak** library:
  ```bash
  pip install bleak
  ```
- **macOS or Linux with Bluetooth** (Windows untested)
- Toothbrush must be **switched on** and within BLE range (~5–10 m)

> **macOS note:** macOS does not expose raw MAC addresses via CoreBluetooth.
> Bleak uses a CoreBluetooth UUID instead (e.g. `A1B2C3D4-E5F6-...`).
> This UUID is device-specific and consistent across reboots on the same Mac.
> Use `oclean_find.py` to discover it.

---

## oclean_find.py – Discover Your Device

Scans all nearby BLE devices and identifies the Oclean toothbrush by its service UUID.
Falls back to connecting to unnamed devices if the service UUID is not visible in
advertisements (common on macOS due to CoreBluetooth behavior).

**Usage:**
```bash
python3 tools/oclean_find.py
```

**Example output:**
```
Scanning for 10 s ...

  'Oclean X'               A1B2C3D4-E5F6-...  rssi= -62  services=[...]  <- OCLEAN ✓

✓ Oclean found: A1B2C3D4-E5F6-...
```

Note the address – you will need it for `oclean_local_test.py`.

---

## oclean_local_test.py – Full Poll Simulation

Simulates exactly one coordinator poll cycle as Home Assistant would execute it.
Imports `parser.py` and `const.py` directly from the integration (no HA needed).

**Steps performed (same order as the HA coordinator):**
1. Find device (by address or auto-scan)
2. Connect via BLE
3. Send time calibration command (`020E + BE timestamp`)
4. Subscribe to all notification characteristics
5. Send `CMD_QUERY_STATUS` (`0303`) → battery level
6. Send `CMD_DEVICE_INFO` (`0202`) → ACK only
7. Send `CMD_QUERY_RUNNING_DATA` (`0308`) → brush session (Type 0 / extended format)
8. Send `CMD_QUERY_RUNNING_DATA_T1` (`0307`) → brush session (Oclean X / Type 1)
9. Wait 3 s for notifications (or longer, see flags below)
10. Read battery via GATT Battery Characteristic
11. Print sensor values as they would appear in Home Assistant

**Usage:**
```bash
# Auto-scan (slower, may not find device on macOS):
python3 tools/oclean_local_test.py

# Direct connection via address (recommended):
python3 tools/oclean_local_test.py --address A1B2C3D4-E5F6-...

# Test K3GUIDE (0340) real-time zone guidance + is_brushing:
#   Script waits 30 s – start brushing after it connects!
python3 tools/oclean_local_test.py --address A1B2C3D4-E5F6-... --brushing

# Test session history pagination (fetch up to 5 older sessions via 0309):
python3 tools/oclean_local_test.py --address A1B2C3D4-E5F6-... --pages 5

# Test brush head counter reset (020F):
#   ⚠️  Resets the wear counter on the device – confirmation required!
python3 tools/oclean_local_test.py --address A1B2C3D4-E5F6-... --reset-brush-head
```

**Example output (Oclean X, 0307 Type-1):**
```
22:08:30  INFO     Found: 'Oclean X' @ A1B2C3D4-E5F6-...
22:08:31  INFO     Connected.
22:08:33  INFO     ✓ Time calibration sent (ts=1771708113)
22:08:33  INFO     ✓ Subscribed  5f78df94-798c-46f5-990a-855b673fbb86
22:08:33  INFO     NOTIFY  raw=0303020e1c140100
22:08:33  INFO     NOTIFY  raw=03072a422300001a0215102c1c00007800780201
22:08:36  INFO     ✓ Battery read: 20%

============================================================
  HA sensor values after this poll
============================================================
  battery                           20%
  last_brush_score                  90 / 100
  last_brush_duration               120s
  last_brush_time                   2026-02-21 22:05:34 UTC
  last_brush_areas                  -- (extended 0308 format not received)
============================================================
```

**What each flag tests:**

| Flag | Tests |
|------|-------|
| *(none)* | Battery, score, duration, timestamp, brush_head_usage (basic poll) |
| `--brushing` | K3GUIDE (0340) real-time zone guidance, is_brushing flag (0303 byte 0) |
| `--pages N` | 0309 session history pagination, older session records |
| `--reset-brush-head` | CMD 020F brush head reset, ACK response content |

**Extended 0308 Format (tooth zone pressures, score, pNum):**
The extended 32-byte format (`AbstractC0002b.m37y`) is only sent by **Type-0 devices**
(Oclean X Pro Elite, X Ultra, and other non-X models). The Oclean X uses the 0307
Type-1 protocol instead. If you own a Type-0 device, run the standard poll and look for
`last_brush_areas` in the output to confirm the extended format is working.

---

## How It Connects to the Integration

The script imports `parser.py` and `const.py` from `../custom_components/oclean_ble/`
using `importlib` to bypass `__init__.py` (which has Home Assistant imports).

```
tools/
  oclean_local_test.py        imports ↓
  oclean_find.py

../custom_components/oclean_ble/
  const.py                    BLE UUIDs and command bytes
  parser.py                   notification parsing logic
```

Any changes you make to `parser.py` are immediately reflected in the test script
on the next run – no HA restart needed.

---

## Debugging Unknown Payloads

Unknown notification types are logged at DEBUG level:
```
DEBUG  Oclean unknown notification type 0x0308, raw: 0308...
```

To decode new payload bytes:
1. Run the script before and after a brush session
2. Compare the raw hex between sessions
3. Note which bytes change and correlate with session data (duration, score)
4. Update `parser.py` and add a test in `tests/test_parser.py`
