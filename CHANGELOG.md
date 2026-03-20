# Changelog

## [v1.0.10] – 2026-03-20

### Bug Fixes

- **Config flow 500 error on open** (closes #55): The setup wizard crashed with a `500 Internal Server Error` when opened via Settings → Integrations. Root cause: `voluptuous_serialize` cannot serialize custom validator functions. Replaced `_validate_poll_interval` with `selector.NumberSelector` (HA-idiomatic, serializable). The poll interval gap constraint (must be 0 or ≥ 60 s) is now validated inline, consistent with the options flow.

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
