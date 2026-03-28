# Changelog

## [v1.2.1] – 2026-03-28

### Fixes

- **BLE subscription hardened** – Proactively clear all CCCD descriptors before subscribing; disconnect+reconnect retry when all subscriptions fail persistently ("Notify acquired"); improved warning message with actionable user steps (#78).
- **`is_brushing` binary sensor removed** – The sensor parsed 0303 byte 0 bit 0 correctly but was unreliable in practice: the toothbrush does not push BLE data during brushing and the 300 s polling interval cannot catch the ~2–3 min window (#6).
- **gestureCode/gestureArray byte offsets corrected** – All three 42-byte record parsers (m18f format) now extract gestureCode as a 2-bit value from byte 30 (was byte 14/19) and gestureArray from bytes 23–30 (was 18–30), matching APK `AbstractC0002b` analysis (#72).
- **OCLEANA1 LEGACY protocol expanded** – Added `0202` (device info), `0302` (brush-head counter), and `0307 via fbb89` (session data) to the LEGACY query commands. APK confirms OCLEANA1 uses the same session path as TYPE1 (#7).

### Docs

- **README** – Removed Binary Sensors section, added Oclean X Ultra (OCLEANV1a) to compatibility table, changed "Not Yet Implemented" to "Not Planned" with explanation for active-brushing detection.

---

## [v1.2.0] – 2026-03-28

### New Features

- **Coverage sensor** (0–100 %) — percentage of zones adequately cleaned per session. Calculation is device-type-aware:
  - **TYPE1** (Oclean X family): calculated from `pressureRatio` — 5 zone groups with time share > 0 ÷ 5 × 100 %. Verified via Oclean Cloud API: `pressureDistribution` is always empty for OCLEANY3M.
  - **TYPE0 / enrichment** (2604/021f): zones with raw pressure > 100 ÷ 8 × 100 % (matches official Oclean app threshold from `C2928q.java`).
- **Duration Rating sensor** (0–100 %) — how well the recommended 240 s brushing duration was met. Formula from APK: `min(100, duration / 240 × 100)`.
- **Pressure Detail sensor** — average of the 5-segment pressure ratio from 42-byte session records. Per-segment values available as entity attributes (`segment_1` … `segment_5`).
- **Power Distribution sensor** — number of zones with power > 0 from per-zone power levels (0–3 scale). Per-zone values and `gesture_array` available as attributes.
- **Gesture sensor** — raw brushing technique indicator (byte 14 of 42-byte session records).
- **Remind Switch** (CMD 0239) — toggle the brushing reminder on/off.
- **Running Switch** (CMD 0240) — toggle the auto power-off timer on/off.
- **Cloud API session viewer** (`tools/oclean_api_test.py`) — fetch session data from the Oclean Cloud API to compare with BLE raw values. Helps reverse-engineer coverage and pressure calculations across device models.

### Fixes

- **TYPE1 coverage from pressureRatio** — bytes 11-15 of the 0307 m18f record are time-distribution percentages across 5 zone groups (sum ≈ 100), NOT per-tooth area pressures. The previous mapping to 8 tooth areas was incorrect for OCLEANY3M.
- **Polling fallback** — added fallback when BLE notification subscriptions fail (#78).
- **Enrichment wait** — fixed enrichment wait for inline sessions and OCLEANA1 battery read.

---

## [v1.1.3] – 2026-03-26

### Bug Fixes

- **OCLEANA1 live battery** – Enabled live battery readings via no-CCCD subscribe fallback for fbb86 on Oclean Air 1 devices (#85).

---

## [v1.1.2] – 2026-03-25

### New Features

- **Oclean X Ultra support** – Added `OCLEANV1a` (Oclean X Ultra) to the TYPE1 protocol map.

### Bug Fixes

- **BLE connection reliability** – Hardened connection handling to reduce disconnects and retry failures (#83).
- **READ fallback for RECEIVE_BRUSH_UUID** – Fixed fallback when BlueZ "Notify acquired" persists on fbb90, preventing session data from being read.
- **Firmware checker tool** – Corrected Oclean API field names and success code in `oclean_firmware_check.py`.

---

## [v1.1.1] – 2026-03-23

### New Features

- **Oclean Z1 support** – Added support for the Oclean Z1 (`OCLEANY5`). Uses a new `TYPE_Z1` protocol profile with hybrid command routing: 0303 / 0202 / 0302 via `fbb85`, 0307 via `fbb89`. Notify on `fbb86` + `fbb90`. Time calibration uses the TYPE1 format (closes #69).
- **Brush Scheme select** – New select entity to change the active brushing programme directly from HA. Supported on all TYPE1 devices (Oclean X family) and TYPE_Z1 (Oclean Z1). Model-specific scheme lists are used automatically.
- **Last Poll sensor** – New diagnostic timestamp sensor showing when the device was last successfully polled over BLE.

### Improvements

- **Sensor renames** – All sensor names have been shortened for a cleaner dashboard display:

  | Before | After |
  |---|---|
  | Last Brush Score | Score |
  | Last Brush Duration | Duration |
  | Last Brush Pressure | Pressure |
  | Last Brush Time | Last Session |
  | Brush Head Usage | Head Sessions |
  | Brush Head Days | Head Age |
  | Brush Mode | Mode |
  | Hardware Revision | HW Revision |
  | Firmware Version | Firmware |
  | Last Brush Areas | Cleaned Zones |
  | Last Brush Scheme Type | Last Scheme |
  | Tooth Area X | Zone X |

- **Diagnostic block** – The following entities were moved to the diagnostic section to keep the main sensor block focused on brushing data: Pressure, Head Age, Mode, all Zone sensors (8×), Brushing binary sensor, Area Reminder switch, Brush Head Lifetime number.
- **Research sensors removed** – Gesture Code, Gesture Array, and Power Array are no longer exposed as HA entities. Their raw values are still logged at DEBUG level (prefix `research:`) for protocol analysis.
- **"Notify acquired" retry** – When BlueZ reports a stale CCCD subscription from a previous dropped connection, the integration now releases it via `stop_notify` and retries automatically instead of logging a warning (closes #49).
- **Button renamed** – "Poll Now" renamed to "Update Now" for clarity.

### Bug Fixes

- **Wrong area bytes on OCLEANY3P** – The C3352g and C3385w0 record parsers were incorrectly sharing byte-layout logic. Split into separate parsers; area pressure bytes are now read from the correct offsets for each device family.

---

## [v1.1.0] – 2026-03-21

### New Features

- **Poll Now button** – New button entity (`button.oclean_poll_now`) triggers an immediate BLE poll directly from the HA dashboard, without needing to use the `oclean_ble.poll` service action.
- **Software brush-head counter** – When the device does not expose a hardware session counter via the `0302` response (e.g. OCLEANY3M), the integration now maintains a software counter that increments by the number of new sessions on each poll. The hardware value always takes priority when available; both counters stay in sync. The counter is reset to 0 when "Reset Brush Head" is pressed.

### Improvements

- **Last Brush Duration** – Default display unit changed from seconds to minutes (e.g. `2.5 min` instead of `150 s`). The unit can still be overridden per entity in HA settings.
- **TYPE1 command routing** – All four query commands (0303 / 0202 / 0302 / 0307) for TYPE1 devices (Oclean X family) are now sent via `fbb89` (`SEND_BRUSH_CMD_UUID`) as required by the firmware. Previously 0303 / 0202 / 0302 were incorrectly sent to `fbb85`.
- **`write_char` field on DeviceProtocol** – Each protocol profile now declares the correct characteristic for one-off write commands (area reminder, brush-head lifetime, time calibration, reset). TYPE1 uses `fbb89`; all other profiles use `fbb85`.
- **Standalone writes on TYPE1** – Area Reminder, Brush Head Lifetime, Sync Time, and Reset Brush Head now subscribe to the device's notify characteristics before writing. This is required on TYPE1 devices where `fbb89` is only exposed after at least one notify subscription (e.g. `fbb90`) is active.
- **020F ACK logging** – "Reset Brush Head" subscribes to `fbb86` and `fbb90` before sending the reset command and logs any notification received within 2 seconds. This aids protocol research into whether the device returns an updated counter value.

### Bug Fixes

- **Area Reminder switch "Characteristic not found"** – Toggling the switch on TYPE1 devices (OCLEANY3M etc.) failed with `Characteristic fbb89 was not found`. Fixed by subscribing to notify chars before the write (see improvement above).
- **Sync Time button on TYPE1** – Same root cause as Area Reminder; the `020E`/`0201` calibration write now also uses the subscribe-first pattern.
- **ESPHome proxy – battery stuck** (closes #7): When the ESPHome BLE proxy has a stale GATT cache and `0x2A19` (Battery Service) is not found, the integration now immediately invalidates the DIS cache and triggers a full GATT re-discovery, then retries the battery notification subscription within the same poll. Previously the proxy would not rediscover `0x2A19` for up to 24 hours.

---

## [v1.0.10] – 2026-03-20

### Bug Fixes

- **Config flow 500 error on open** (closes #55): The setup wizard crashed with a `500 Internal Server Error` when opened via Settings → Integrations. Root cause: `voluptuous_serialize` cannot serialize custom validator functions. Replaced `_validate_poll_interval` with `selector.NumberSelector` (HA-idiomatic, serializable). The poll interval gap constraint (must be 0 or ≥ 60 s) is now validated inline, consistent with the options flow.

---

## [v1.0.9] – 2026-03-15

### Bug Fixes

- **OCLEANY3P – all sessions now imported** (closes #49): The `*B#` multi-packet reassembly was skipped for Oclean X Pro Elite devices because they encode `0x00` at record byte 0 (no year stored on device). Only the first session was imported; the remaining sessions were silently discarded. The coordinator now correctly enters reassembly for all devices and selects the right parser based on the year byte: `0x00` → year inferred from wall clock (`parse_y3p_stream_record`), any other value → year read from the record (`parse_t1_c3352g_record`).
- **Non-blocking startup**: The integration no longer raises `ConfigEntryNotReady` when the device is unreachable at HA startup. Coordinator and entities are registered immediately so the poll service and all entity entries always exist. Entities show as unavailable until the first successful poll.
- **Battery notifications** (closes #7): Subscribe to characteristic `0x2A19` before reading to ensure notifications are received.
- **Options flow**: Reject poll interval values between 1 and 59 seconds (must be 0 for manual or ≥ 60 s).
- **Setup wizard**: Allow poll interval 0 (manual / no auto-polling) in the config flow (closes #51).
- **Stability**: Added timeout to `start_notify()` to prevent a BlueZ hang on stale subscriptions.

### Internal

- Log active config on entry load; clarify `poll_interval=0` wording in UI.

---

## [v1.0.8] – 2026-03-11

### New Features

- **Oclean X Pro full session data** – Reclassified OCLEANY3 from Type-0 to Type-1 protocol; sessions are now fetched via `0307` and multi-packet `*B#` reassembly instead of the non-working `0308` path. Score, duration, and tooth area pressures are now available.
- **`*B#` multi-packet BLE reassembly** – The coordinator now correctly reassembles multi-packet session streams (used by OCLEANY3 / OCLEANY3P) into complete 42-byte records before parsing.
- **OCLEANY3MH score parsing** – The dynamic-prefix `XX03` notification format (score at byte 0, confirmed from empirical logs) is now parsed. Score, duration, pNum and timestamp are extracted and stored.
- **Per-coordinator log prefix** – Every log line is now prefixed with `[MODEL/XX]` (e.g. `[OCLEANY3M/A4]`) to make multi-device log files easier to filter.

### Bug Fixes

- Fixed sessions sensor showing score/areas of the *oldest* retrieved session instead of the newest when multiple sessions were returned in one poll (e.g. paginated `*B#` records).
- Fixed DIS (Device Information Service) cached values being lost when a BLE read fails mid-session; the coordinator now falls back to the previously cached model/firmware values instead of resetting to `UNKNOWN`.

## [v1.0.7] – 2026-03-09

### New Features

- **Oclean X Pro 20 (OCLEANX20) support** – Added protocol mapping and parser for the extended-offset inline `0307` format used by OCLEANX20 devices (issue #37).
- **Manual poll action** – Added `oclean_ble.poll` service action to trigger an immediate BLE poll without waiting for the next scheduled interval (issue #39).
- **Sync Time button** – New button entity to manually calibrate the device clock (020E command) on demand (issue #43).
- **Spanish translation** – Added `es.json` locale.

### Bug Fixes

- Fixed initial poll being skipped when poll windows were configured but no cached data existed yet (issue #34).
- Fixed brush-head counter sensor not updating immediately after pressing "Reset Brush Head" (issues #40/#41).

### Internal

- Named BLE timing constants replace magic numbers throughout the coordinator.
- Coordinator `_setup_and_read` split into focused helper methods for readability.
- `DATA_*` and `Callable` constants used consistently; string literals removed.
- Dependabot added for pip and GitHub Actions dependency updates.

---

## [v1.0.6] – 2026-03-09

### Bug Fixes

- Fixed initial poll being skipped when poll windows were configured and no cached data existed (issue #36).
- State is now persisted after every successful poll (not only when new sessions are found).

---

## [v1.0.5] – 2026-03-09

### New Features

- **Oclean X Pro Elite (OCLEANY3P) session data** – `021f` (tooth areas) and `5100` (session metadata) notifications are now parsed and stored (issue #3).
- **Paginated 0307 score + areas** – Full 42-byte `m18f` records from paginated `0307` responses now yield score and tooth area pressures (issue #29).
- **Enrichment wait** – A short wait after session notifications allows `0000`/`2604` score/area pushes to arrive before the poll completes (issue #31).
- **Extended protocol map** – All known Oclean model IDs from the APK are now mapped to the correct protocol (issue #25).

### Bug Fixes

- Fixed spurious "could not parse" log entries for short `0308` status ACK packets (issue #26).
- Fixed a race condition in the file log handler attachment (issue #24).

---

## [v1.0.4] – 2026-03-07

### New Features

- **DeviceProtocol profiles** – Introduced per-device-family protocol objects (`TYPE0`, `TYPE1`, `LEGACY`, `UNKNOWN`) replacing hard-coded characteristic lists (issue #15).
- **Oclean Air 1 (OCLEANA1) support** – Added READ fallback for devices that lack a CCCD on the notify characteristic and therefore cannot subscribe to BLE notifications (issue #7/#16).
- **021f/5100 research logging** – Verbose per-byte logging for OCLEANY3P push notifications to enable format analysis (issue #3/#17).

### Bug Fixes

- Fixed Last Brush Score sensor remaining `unavailable` after an HA restart even when a valid score had been seen before (issue #19).
- Statistics module extracted to `statistics.py` to avoid circular imports.

---

## [v1.0.3] – 2026-03-06

### New Features

- **Brand images** – Integration logo and icon are now served via the HA 2026.3 brands proxy API (issue #14).

### Bug Fixes

- Fixed `datetime` unbound error when session timestamp parsing failed during pagination (issue #8).
- Fixed `CancelledError` propagating out of the pagination loop and aborting the entire poll (issue #9).
- Fixed silent `return` missing for `0307` year_byte=0 case on OCLEANY3P (issue #3).

---

## [v1.0.2] – 2026-02-24

### New Features

- **Tooth area sensors** – Each of the 8 tooth zones (e.g. *Left Upper Outside*, *Right Lower Inside*) now has its own sensor entity, populated from `2604` area notifications on Type-1 devices (Oclean X series).
- **Per-zone long-term statistics** – Area pressure values are imported as individual HA long-term statistics so zone history appears correctly in the Energy/Statistics dashboard.
- **Last Brush Scheme sensor** – The active brushing scheme (pNum) is now extracted from `0307` responses and mapped to a human-readable scheme name.
- **Duration from `0307`** – Session duration is now parsed directly from bytes 12–13 of the `0307` payload, replacing the previous formula estimate.
- **Type-1 enrichment notifications** – Score (`0000`), per-area data (`2604`), and session metadata (`5a00`) are now parsed and merged into the session snapshot for Oclean X devices.
- **Unavailable sensors** – Sensors that are structurally unavailable for a given device (e.g. area sensors on Oclean X Pro vs. Oclean X) are now explicitly marked as `unavailable` rather than showing stale data.
- **`0x5400` notification capture** – A newly observed firmware push (`0x5400`, absent from the APK) is now logged byte-by-byte to support ongoing protocol analysis.

### Bug Fixes

- Fixed score reporting on Type-1 devices: the device-computed score from `0000` notifications now correctly overwrites the previous formula-based estimate.
- Fixed enrichment notifications (`0000` / `2604`) not being merged into the session snapshot when they arrived after the `0307` response in the same poll cycle.
- Removed incorrect claim that `0307` byte 13 encodes session duration (it does not).

### Removed

- `last_brush_clean` sensor removed – it was redundant with the individual zone pressure sensors.

### Internal

- Shared parser helpers (`_device_datetime`, `_build_area_stats`, `_build_utc_timestamp`) extracted to reduce duplication.
- GitHub Actions workflows added: Hassfest, HACS validation, and pytest CI.
