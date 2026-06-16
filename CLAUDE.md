# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

### Run all tests
```bash
pip install -r requirements-test.txt
pytest
```

### Lint (always include tests/)
```bash
ruff check custom_components/ tests/
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
            ├─ _calibrate_time()           → write CMD 020E + timestamp via DeviceProtocol.write_char
            ├─ _read_device_info_service() → BLE DIS (model/fw/hw, cached 24 h)
            ├─ _subscribe_notifications()  → up to 4 GATT notify characteristics (per DeviceProtocol.notify_chars)
            ├─ _send_query_commands()      → CMD sequence from DeviceProtocol.query_commands
            │                                TYPE1: 0303/0202/0302/0307 all via fbb89 (SEND_BRUSH_CMD_UUID)
            │                                TYPE0: 0303/0202/0302/0308 all via fbb85 (WRITE_CHAR_UUID)
            ├─ _paginate_sessions()        → CMD 0309 via fbb85 until no new sessions (TYPE0 only)
            └─ _read_battery_and_unsubscribe()
                  → notification_handler() calls parse_notification()
                       └─ _PARSERS registry dispatches by 2-byte prefix
                            ├─ 0303 → _parse_state_response()
                            ├─ 0307 → _parse_info_t1_response()   (Type-1 / Oclean X family)
                            ├─ 0308 → _parse_info_response()      (Type-0; auto-detects extended vs. simple binary format)
                            ├─ 0202 → _handle_device_info_ack()
                            ├─ 0000 → _parse_score_t1_response()  (Type-1 enrichment: score push after session)
                            ├─ 2604 → _parse_brush_areas_t1_response() (Type-1 enrichment: per-tooth areas)
                            └─ 0340 → _parse_k3guide_response()   (K3-series real-time guidance)
  └─ OcleanDeviceData (dataclass snapshot)
       └─ CoordinatorEntity subclasses (OcleanSensor, OcleanBinarySensor, OcleanButton)
```

### Key files

| File | Role |
|------|------|
| `coordinator.py` | `OcleanCoordinator` – BLE polling, session pagination, HA long-term statistics import, stale-data persistence |
| `parser.py` | Pure functions: BLE byte payload → dict. Strategy registry `_PARSERS` keyed on 2-byte response prefix. Two binary record formats (simple 18-byte and extended 32-byte). |
| `protocol.py` | `DeviceProtocol` dataclass – per-family capability profile: `notify_chars`, `query_commands`, `supports_pagination`, `write_char`. `protocol_for_model()` maps DIS model-IDs to profiles. |
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
- `brush_head_sw_count` – software brush-head session counter; incremented per new session when the device does not expose a hardware counter via 0302. When the hardware value IS available, it syncs `brush_head_sw_count` to match, so switching between HW and SW mode is seamless. Reset by `async_reset_brush_head()`.

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

### Device protocol mapping (confirmed via APK + empirical logs)

| Device | Model-ID | Protocol | All commands via | Session response | Extended fields |
|--------|----------|----------|-----------------|-----------------|-----------------|
| Oclean X | OCLEANY3M | TYPE1 | fbb89 (SEND_BRUSH_CMD_UUID) | fbb90 (RECEIVE_BRUSH_UUID) | Score + areas **inline** in the 42-byte `*B#` record (areas = gestureArray bytes 23-30). `0000`/`2604` enrichment pushes may additionally arrive. |
| Oclean X Pro | OCLEANY3 | TYPE1 | fbb89 | fbb90 | Score + areas **inline** in the 42-byte `*B#` record (areas = gestureArray bytes 23-30). Same `parse_t1_c3385w0_record` path as OCLEANY3M. |
| Oclean X Pro Elite | OCLEANY3P | TYPE1 | fbb89 | fbb90 | Score + areas **inline** in the 42-byte `*B#` record (areas = gestureArray bytes 23-30). NOT via `021f`/`5100`/`2604` pushes. |
| Oclean Air 1 | OCLEANA1 | LEGACY | fbb85 (WRITE_CHAR_UUID) | fbb86 READ (no CCCD) | None |

**All TYPE1 devices** send query commands (0303/0202/0302/0307) via `fbb89` (`SEND_BRUSH_CMD_UUID`) during polls. Responses arrive as notifications on `fbb90` (`RECEIVE_BRUSH_UUID`) or `fbb86` (`READ_NOTIFY_CHAR_UUID`). The `write_char` field on `DeviceProtocol` controls which characteristic is used for one-off standalone writes (area_remind, brush_head_max_days, reset_brush_head, time calibration, brush scheme). For TYPE1, standalone writes use `fbb85` (`WRITE_CHAR_UUID`) — confirmed via APK `C3385w0_fallback.java` (the TYPE1 handler); only `0307` poll queries use `fbb89`. Note: `C3376s.java` in the APK sources is the handler for **WiFi-only devices** (model IDs 0005/0006/000D) and must not be used as a reference for TYPE1 BLE behavior.

**OCLEANY3M / OCLEANY3**: Both use `parse_t1_c3385w0_record` for the reassembled `*B#` record. **Score and per-zone tooth areas are inline in that record** (score = byte 33, areas = gestureArray bytes 23-30, time-per-zone). The `0000`/`2604` enrichment pushes on fbb90 are additional/optional — they were never the only source of areas. In inline mode (no new sessions) the 13-byte truncated record omits score and gestureArray; enrichment pushes may or may not follow depending on firmware.

**Issue #109 fix (2026-06-16):** `parse_t1_c3385w0_record` previously read tooth areas from bytes 11-15, which are the **pressureRatio** buckets (values 50–90), not the per-zone gestureArray. A session therefore looked unbalanced (one zone ≈90, the rest 0). Areas now come from the gestureArray (bytes 23-30), identical to `parse_t1_c3352g_record`. `pressure_ratio` remains a separate field from bytes 11-15. This was the same bug as #72/#105 in the other parser path.

**OCLEANY3P** (Oclean X Pro Elite): Uses 0307. When the device has new unsynced sessions, it responds with the `*B#` (hex `2a4223`) multi-packet stream, reassembled by the coordinator into 42-byte records. **All session data — including per-zone tooth areas — is inline in that record**; there is no separate area/score push.

**Empirically disproven (issue #49 logs + APK C3352g analysis, 2026-06):** the old `021f`/`5100`/`2604` "enrichment push" theory for OCLEANY3P is wrong:
- `2604` never appears on OCLEANY3P (it is the OCLEANY3M variant).
- `021f` is a static constant (`021f00000f000f211123010d120f010f0f120000`), not session data.
- `5100` is `0xFF`-filled placeholder.
- `0000` "notifications" are actually `*B#` stream continuation fragments.

Areas are parsed from record bytes 23-30 (the APK `gestureArray` field) by **all three TYPE1 `*B#` parsers** — `parse_t1_c3385w0_record` (OCLEANY3M/OCLEANY3), `parse_t1_c3352g_record` (OCLEANY3P) and `parse_y3p_stream_record`. These 8 values are `gestureArray` = `getTime12()`, a per-zone metric mapped 1:1 onto `TOOTH_AREA_NAMES` (BrushAreaType enum order, value 1..8). pressureRatio is the separate bytes-11-15 field.

**Coverage reproduces the app's on-device diagram logic (revised 2026-06-16, fully verified; supersedes the v1.3.1 ">7 s" note).** From APK `C1793b.m3803y`/`m3804z` (formula) + smali (the `i10` multiplier):
- The app **normalizes** each zone by the sum of all zones, scaled by the session duration: `norm[k] = raw[k] / sum * duration`. The multiplier `i10` was confirmed in smali — the caller in `MineReportActivity` passes `BrushRecordEntity.getTimeLong()` (the session duration in seconds) to `m3803y`/`m3804z`.
- A zone is "covered" (diagram level 3) when `norm[k] >= threshold`. 8-zone path thresholds: `8 / 9 / 10` (YD0003 / default / Y3PD). We use the default **9** for all TYPE1 devices (YD0003/Y3PD variants not distinguished yet). 12-zone path uses `<=2.0 → 1, <5.5 → 2, else 3`. There is **no literal "7 s" threshold anywhere** in the APK — the old v1.3.1 note was wrong.
- Our parsers implement this exactly: `share_threshold = AREA_COVERAGE_NORM_THRESHOLD(=9) / duration`, then a zone counts when `raw/sum >= share_threshold` (see `_build_area_stats` and `const.py`). So coverage scales correctly with session length.
- **Caveat:** the coverage **%** the official app *displays* is a separate `clean` field sourced from the **cloud** (`BrushRecordResult.getClean()`); it is **absent from the BLE record** (no `clean` key in the record JSON) and cannot be reproduced exactly offline. Our value reproduces the on-device *diagram classification*, the closest faithful local equivalent — not the cloud %.

**OCLEANA1** (Oclean Air 1): fbb86 characteristic exists but has no CCCD → cannot subscribe for notifications. Coordinator uses direct `read_gatt_char()` fallback after sending query commands.

### OCLEANY3P APK References
- `apk/oclean-project/sources/p352w/C5733b.java` – multi-packet reassembly for 0307 (header: "0307*B#" + RecordCount[2B] + RecordLen[1-2B] + data)
- `apk/oclean-project/sources/p105g/C3376s.java` – Type-1 protocol handler

### Known recurring BleakErrors (harmless, every poll)

- `5f78df94-...-fbb89`: characteristic does not support notify/indicate – firmware limitation on Oclean X
- `6c290d2e-...`: characteristic not found – likely K3-series only, absent on Oclean X

### What to look for when debugging missing extended fields (OCLEANY3/Y3P)

1. Check if any `0308` prefix responses are received at all (grep `"INFO response raw payload"`)
2. If `0308` arrives: check if `payload[0]==0 and payload[1]>=32` – if not, it falls to simple parser
3. If extended detected: does `"extended running-data"` appear? If not, check `"extended record parse error"`
4. If simple parser runs instead: fields like Areas/Pressure/Scheme are not in that format

### What to look for when debugging missing Score/Areas on TYPE1 (OCLEANY3M/OCLEANY3)

Score and per-zone areas are **inline in the reassembled 42-byte `*B#` record** (score = byte 33, areas = gestureArray bytes 23-30). The `0000`/`2604` pushes are optional enrichment.
1. Check if 0307 session is received at all: `grep "0307 inline\|0307 paginated\|C3385w0 record\|C3352g record\|m18f parsed" oclean_ble.log`
2. If the full `*B#` record was reassembled, check the parsed areas/coverage: `grep "C3385w0 record: areas\|C3385w0 record parsed" oclean_ble.log`
3. If only a 13-byte truncated record arrived (inline mode, no new sessions), score/gestureArray are absent — optional enrichment may follow: `grep "0000 score\|2604 areas" oclean_ble.log`
4. Areas look unbalanced despite a high score? Confirm areas come from bytes 23-30, not the pressureRatio bytes 11-15 (the issue #109 bug).
5. Check 0302 device-settings response: `grep "0302 brush-head counters" oclean_ble.log` – confirms all TYPE1 commands reach the device via fbb89

### `b2` byte in 0303 STATE response

**Resolved via APK analysis.** Bytes 1 and 2 of the 0303 STATE response are **not parsed by the official app** – confirmed in `C3367n0.java`. Only byte 0 (status) and byte 3 (capacity/battery) are extracted. The varying byte 2 (0x0f–0x1d) is likely an internal counter and can be safely ignored.