# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

### Run all tests
```bash
pip install -r requirements-test.txt
pytest
```

### Run a single test file
```bash
pytest tests/test_parser.py
```

### Run a single test class or function
```bash
pytest tests/test_parser.py::TestParseInfoT1Response
pytest tests/test_parser.py::TestParseBattery::test_valid_value
```

No build step is needed – this is a pure Python Home Assistant custom integration installed by copying `custom_components/oclean_ble/` into an HA config directory.

---

## Architecture

This is a **Home Assistant custom integration** (`domain: oclean_ble`) for Oclean Smart Toothbrushes. It connects via BLE every N seconds (default 300 s), reads brushing data, then disconnects. No cloud, no account.

### Data flow

```
BLE Device
  └─ establish_connection (bleak_retry_connector)
       └─ OcleanCoordinator._poll_device()
            ├─ _calibrate_time()         → write CMD 020E + timestamp
            ├─ _read_device_info_service() → BLE DIS (model/fw/hw, cached 24 h)
            ├─ _subscribe_notifications() → up to 4 GATT notify characteristics
            ├─ _send_query_commands()     → CMD 0303 (status), 0308 / 0307 (sessions)
            ├─ _paginate_sessions()       → CMD 0309 until no new sessions
            └─ _read_battery_and_unsubscribe()
                  → notification_handler() calls parse_notification()
                       └─ _PARSERS registry dispatches by 2-byte prefix
                            ├─ 0303 → _parse_state_response()
                            ├─ 0307 → _parse_info_t1_response()   (Type-1 / Oclean X)
                            ├─ 0308 → _parse_info_response()      (Type-0; auto-detects extended vs. simple binary format)
                            ├─ 0202 → _handle_device_info_ack()
                            └─ 0340 → _parse_k3guide_response()   (K3-series real-time guidance)
  └─ OcleanDeviceData (dataclass snapshot)
       └─ CoordinatorEntity subclasses (OcleanSensor, OcleanBinarySensor, OcleanButton)
```

### Key files

| File | Role |
|------|------|
| `coordinator.py` | `OcleanCoordinator` – BLE polling, session pagination, HA long-term statistics import, stale-data persistence |
| `parser.py` | Pure functions: BLE byte payload → dict. Strategy registry `_PARSERS` keyed on 2-byte response prefix. Two binary record formats (simple 18-byte and extended 32-byte). |
| `models.py` | `OcleanDeviceData` dataclass – typed snapshot returned by the coordinator. Field names match `DATA_*` string constants from `const.py` so sensors look up values via `getattr`. |
| `const.py` | All GATT UUIDs, BLE command bytes, response type markers, `DATA_*` / `SENSOR_*` key constants, `SCHEME_NAMES` lookup, `TOOTH_AREA_NAMES` tuple |
| `entity.py` | `OcleanEntity` base class – shared `unique_id`, `device_info`, and `available` logic |
| `sensor.py` | All sensor entities; `OcleanSensor` (generic), `OcleanBrushAreasSensor` (zone dict as attributes), `OcleanSchemeSensor` (pNum + name attribute) |
| `config_flow.py` | Config + options flow; auto-discovery via Bluetooth, manual MAC entry fallback |
| `__init__.py` | `async_setup_entry` / `async_unload_entry`; attaches/detaches a rotating file log handler (`oclean_ble.log`, 1 MB × 3) shared across all config entries |

### Parser format detection (0308 path)

`_parse_info_response()` auto-detects which binary format to use:
- **Extended (32+ bytes):** `payload[0] == 0` AND `payload[1] >= 32` AND `len(payload) >= payload[1]` → `_parse_extended_running_data_record()` – yields score, duration, 8 tooth area pressures, scheme type, pNum
- **Simple (18 bytes):** fallback → `_parse_running_data_record()` – yields timestamp, pressure, brush-head wear counter

The Type-1 path (0307) is a separate handler `_parse_info_t1_response()` with a different byte layout (device constant in bytes 0-4, timestamp bytes 5-10, brushing metric byte 13).

### Session import and deduplication

`OcleanCoordinator` persists state in HA storage (one store per MAC, keyed `oclean_ble.<mac_slug>`):
- `last_session_ts` – Unix timestamp of the newest imported session; prevents re-importing sessions across restarts
- `brush_head_count` / `brush_head_hw` – software brush-head counter (used when the device doesn't expose a hardware counter)

New sessions are imported via `recorder.statistics.async_add_external_statistics` with their actual timestamps so historical data appears correctly in HA graphs.

### Test setup

Tests run **without a full Home Assistant instance**. `tests/conftest.py` injects lightweight module stubs for all `homeassistant.*` and `bleak*` imports before the integration code is imported. BleakClient is mocked in coordinator tests using `unittest.mock.AsyncMock`. No fixtures are required; test classes directly construct `OcleanCoordinator` with a `MagicMock` hass.

### BLE compatibility note

`coordinator.py` patches `aioesphomeapi._join_split_uuid` at module load time to handle malformed GATT descriptors from Oclean firmware (zero-element UUID lists). This is a one-time no-op on setups without an ESPHome BLE proxy.

---

## Log analysis (`oclean_ble.log`)

The integration writes `oclean_ble.log` to the HA config directory automatically (no `configuration.yaml` change needed). Use a Task/Bash agent to analyze it – the file can exceed 256 KB.

### Key grep patterns

```bash
# Errors and warnings
grep "ERROR\|WARNING" oclean_ble.log

# Parse failures
grep "could not parse\|parse error\|too short\|parsing failed\|implausible" oclean_ble.log

# Format breakdown: how often is each path used?
grep -c "extended running-data" oclean_ble.log     # 0308 extended (OCLEANY3/Y3P)
grep -c "0308-simple parsed" oclean_ble.log        # 0308 simple
grep -c "Type-1 INFO response raw" oclean_ble.log  # 0307 (Oclean X)

# Extended format detected but parsing failed?
grep -c "extended format detected but parsing failed" oclean_ble.log

# Successful session parses with field values
grep "extended running-data" oclean_ble.log | tail -5
grep "Type-1 INFO" oclean_ble.log | grep -v "raw" | tail -5

# Connection problems
grep "BleakError\|not found\|Insufficient" oclean_ble.log | grep -v "DEBUG" | head -20

# DIS cache behaviour
grep -c "skipped, cached" oclean_ble.log
grep -c "DIS read" oclean_ble.log
```

### Device protocol mapping (confirmed empirically)

| Device | Response prefix | Extended fields available |
|--------|----------------|--------------------------|
| Oclean X (OCLEANY3M) | **0307** only | No – timestamp only (byte 13 purpose unconfirmed; not parsed) |
| Oclean X Pro (OCLEANY3) | 0308 expected | Yes – if extended format (payload[0]==0, len>=32) |
| Oclean X Pro Elite (OCLEANY3P) | 0308 expected | Yes – if extended format |

The Oclean X never sends 0308 – it only uses 0307. Extended fields (Areas, Pressure, Scheme, Clean) are therefore **structurally unavailable** on Oclean X and missing sensor values on this device are expected, not a bug.

### Known recurring BleakErrors (harmless, every poll)

- `5f78df94-...-fbb89`: characteristic does not support notify/indicate – firmware limitation on Oclean X
- `6c290d2e-...`: characteristic not found – likely K3-series only, absent on Oclean X

### What to look for when debugging missing extended fields (OCLEANY3/Y3P)

1. Check if any `0308` prefix responses are received at all (grep `"INFO response raw payload"`)
2. If `0308` arrives: check if `payload[0]==0 and payload[1]>=32` – if not, it falls to simple parser
3. If extended detected: does `"extended running-data"` appear? If not, check `"extended record parse error"`
4. If simple parser runs instead: fields like Areas/Pressure/Scheme are not in that format

### `b2` byte in 0303 STATE response

**Resolved via APK analysis.** Bytes 1 and 2 of the 0303 STATE response are **not parsed by the official app** – confirmed in `C3367n0.java`. Only byte 0 (status) and byte 3 (capacity/battery) are extracted. The varying byte 2 (0x0f–0x1d) is likely an internal counter and can be safely ignored.