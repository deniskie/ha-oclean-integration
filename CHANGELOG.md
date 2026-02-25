# Changelog

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
