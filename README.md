# Oclean Toothbrush (unofficial) – Home Assistant Integration

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)
[![GitHub Release](https://img.shields.io/github/v/release/deniskie/ha-oclean-integration)](https://github.com/deniskie/ha-oclean-integration/releases)
[![License](https://img.shields.io/github/license/deniskie/ha-oclean-integration)](LICENSE)
[![Tests](https://img.shields.io/github/actions/workflow/status/deniskie/ha-oclean-integration/tests.yaml?label=tests)](https://github.com/deniskie/ha-oclean-integration/actions)
[![hassfest](https://img.shields.io/github/actions/workflow/status/deniskie/ha-oclean-integration/hassfest.yaml?label=hassfest)](https://github.com/deniskie/ha-oclean-integration/actions/workflows/hassfest.yaml)
[![validate](https://img.shields.io/github/actions/workflow/status/deniskie/ha-oclean-integration/validate.yaml?label=validate)](https://github.com/deniskie/ha-oclean-integration/actions/workflows/validate.yaml)

> **Unofficial community integration.** This project is not affiliated with, endorsed by,
> or connected to Oclean / Zhuhai Ice Bear Smart Home Technology Co., Ltd.
> "Oclean" is a registered trademark of its respective owner.

Custom integration for **Oclean Smart Toothbrushes** (Oclean X, X Pro, X Pro Elite, and compatible models).

Connects every 5 minutes via Bluetooth, reads brushing data, then disconnects.
No cloud, no account, fully local.

> **Devices tested:** Oclean X (OCLEANY3M) · Oclean X HW variant (OCLEANY3MH) · Oclean X Pro (OCLEANY3) · Oclean X Pro Elite (OCLEANY3P) · Oclean X Pro 20 (OCLEANX20) · Oclean X Ultra (OCLEANV1a) · Oclean Z1 (OCLEANY5) · Oclean Air 1 (OCLEANA1)
> **Protocol:** Reverse-engineered from the official Oclean APK

---

## Installation

### Option A – HACS (recommended)

1. Open **HACS** in Home Assistant.
2. Click **⋮ → Custom repositories**.
3. Add `https://github.com/deniskie/ha-oclean-integration` as **Integration**.
4. Search for **Oclean** and install.
5. Restart Home Assistant.

### Option B – Manual

1. Download or clone this repository.
2. Copy the `custom_components/oclean_ble/` folder to your HA config directory:
   ```
   /config/custom_components/oclean_ble/
   ```
3. Restart Home Assistant.

### Setup

After restart, go to **Settings → Integrations → Add Integration** and search for **Oclean**.

- If your brush is turned on and nearby, it may be **auto-discovered** via Bluetooth.
- Otherwise, enter the **MAC address** manually (found in the official Oclean app under device settings).

---

## Entities

Entity IDs follow the pattern `<platform>.<device_name>_<suffix>` where `<device_name>` is the name you gave the device during setup (default: `oclean`).

### Sensors

#### Session data – updated after each brushing session

| Name | Suffix | Description | Unit | Status |
|------|--------|-------------|------|--------|
| Battery | `_battery` | Current battery level | % | ✅ Tested |
| Last Session | `_last_session` | Timestamp of the last brushing session | timestamp | ✅ Tested |
| Score | `_score` | Brushing quality score of the last session (0–100) | – | ✅ Tested |
| Duration | `_duration` | Duration of the last session | s (displayed as min) | ✅ Tested |
| Last Scheme | `_last_scheme` | Brushing programme used in the last session – human-readable name (e.g. "Clean", "Sensitive") from the pNum lookup table; falls back to the raw number if unknown | – | ✅ Tested |
| Pressure | `_pressure` | Average brushing pressure across all tooth zones in the last session (raw ADC value 0–255) | – | ⚠️ Unconfirmed |
| Coverage | `_coverage` | Percentage of tooth zones adequately cleaned in the last session (0–100 %). Uses the official Oclean app threshold: zones with raw pressure > 100 count as covered. | % | ✅ Tested |
| Duration Rating | `_duration_rating` | How well the recommended brushing duration (240 s) was met (0–100 %, capped). Formula from the official Oclean app: `min(100, duration / 240 × 100)` | % | ⚠️ Unconfirmed |
| Gesture | `_gesture` | Brushing technique indicator from the device (raw value 0–255 from byte 14 of 42-byte session records) | – | ⚠️ Unconfirmed |
| Pressure Detail | `_pressure_detail` | Average of the 5-segment pressure ratio from 42-byte session records. Per-segment values available as entity attributes (`segment_1` … `segment_5`) | – | ⚠️ Unconfirmed |
| Power Distribution | `_power_distribution` | Number of zones with power > 0 from per-zone power levels (0–3 scale). Per-zone values available as entity attributes (mapped to tooth area names), plus `gesture_array` | – | ⚠️ Unconfirmed |
| Cleaned Zones | `_cleaned_zones` | Number of tooth zones (0–8) with non-zero pressure in the last session; individual per-zone pressures available as entity attributes | – | ⚠️ Unconfirmed |
| Zone \<name\> | `_zone_<name>` | Pressure for one individual tooth zone in the last session (raw value 0–255; 0 = not brushed / no data). Eight sensors, one per zone: `upper_left_out`, `upper_left_in`, `lower_left_out`, `lower_left_in`, `upper_right_out`, `upper_right_in`, `lower_right_out`, `lower_right_in` | – | ⚠️ Unconfirmed |

#### Device settings – updated every poll from the device

| Name | Suffix | Description | Unit | Status |
|------|--------|-------------|------|--------|
| Mode | `_mode` | Active brushing mode currently set on the device (`modeNum` from 0302 response, byte 5). Device-family-specific integer – e.g. 1 = Standard, 2 = Sensitive; exact mapping varies by model. Unlike "Last Scheme", this reflects the device's current configuration, not the last session. | – | ⚠️ Unconfirmed |
| Head Sessions | `_head_sessions` | Number of brushing sessions since the last brush head reset (`headUsedTimes` from 0302 response) | – | ⚠️ Unconfirmed |
| Head Age | `_head_age` | Calendar days elapsed since the last brush head reset (`headUsedDays` from 0302 response) | d | ⚠️ Unconfirmed |

#### Diagnostic – device information, always shown

| Name | Suffix | Description | Unit | Status |
|------|--------|-------------|------|--------|
| Model | `_model` | Device model ID from BLE Device Information Service (e.g. `OCLEANY3M`) | – | ✅ Tested |
| Firmware | `_firmware` | Firmware version string from BLE DIS (e.g. `1.0.0.20`) | – | ✅ Tested |
| HW Revision | `_hw_revision` | Hardware revision string from BLE DIS (e.g. `Rev.D`) | – | ✅ Tested |
| MAC Address | `_mac_address` | Bluetooth MAC address of the device | – | ✅ Tested |
| Last Poll | `_last_poll` | Timestamp of the last completed BLE poll | timestamp | ✅ Tested |

### Switches

| Name | Suffix | Description | Status |
|------|--------|-------------|--------|
| Area Reminder | `_area_reminder` | Enables/disables the tooth-zone completion reminder on the device (writes CMD 020D; state persisted locally – assumes state, no read-back) | ⚠️ Unconfirmed |
| Over-Pressure Alert | `_over_pressure` | Enables/disables the brushing over-pressure alert on the device (writes CMD 0212; state persisted locally – assumes state, no read-back) | ⚠️ Unconfirmed |
| Brushing Reminder | `_brushing_reminder` | Enables/disables the brushing reminder on the device (writes CMD 0239; state persisted locally – assumes state, no read-back) | ⚠️ Unconfirmed |
| Auto Power-Off Timer | `_auto_power_off_timer` | Enables/disables the automatic power-off timer on the device (writes CMD 0240; state persisted locally – assumes state, no read-back) | ⚠️ Unconfirmed |

### Numbers

| Name | Suffix | Description | Unit | Status |
|------|--------|-------------|------|--------|
| Brush Head Lifetime | `_brush_head_lifetime` | Maximum brush head lifetime in days. When "Head Age" or "Head Sessions" exceeds this value, a replacement reminder is shown in HA (writes CMD 0217; state persisted locally) | d | ⚠️ Unconfirmed |

### Buttons

| Name | Suffix | Description | Status |
|------|--------|-------------|--------|
| Reset Brush Head | `_reset_brush_head` | Resets the brush head wear counter on the device (CMD 020F) and clears the local session counter | ⚠️ Unconfirmed |
| Sync Time | `_sync_time` | Synchronises the current time to the device clock | ✅ Tested |
| Update Now | `_update_now` | Triggers an immediate BLE poll (same as the `oclean_ble.poll` action) | ✅ Tested |

**Legend:**
- ✅ **Tested** – confirmed working on real hardware with multiple sessions
- ⚠️ **Unconfirmed** – implemented based on APK reverse-engineering; needs more device testing
- ❌ **Not implemented** – see [Roadmap](#roadmap)

---

## Feature Status

### ✅ Confirmed Working

| Feature | Details |
|---------|---------|
| Bluetooth connection | Automatic reconnect at each poll interval |
| Battery level | Read directly from device |
| Last brush score | Confirmed with multiple real sessions (Oclean X); delivered via separate BLE notification after session end |
| Last brush timestamp | Device local time, confirmed across multiple sessions |
| Last brush duration | Session length; displayed in minutes by default (e.g. 2.5 min). Confirmed on Oclean X (0307 format, APK-verified via AbstractC0002b.m18f) |
| Last brush scheme type | Brush programme name/ID (pNum); confirmed on Oclean X via 0307 (APK-verified) |
| Time calibration | Device clock synced on every poll and on demand via the *Sync Time* button |
| Poll interval | Configurable 60–86400 seconds (default: 300 s), or set to `0` for manual-only mode |
| Stale data persistence | Sensors keep last known value when device is unreachable |
| Config flow | Manual MAC address entry with validation |
| Duplicate prevention | Sessions deduplicated by timestamp; no double-import |
| Device info | Model, firmware version, and hardware revision read from standard BLE Device Information Service (0x180A); shown in HA device info panel and as diagnostic sensors |

### ⚠️ Implemented – Needs More Testing

| Feature | Details |
|---------|---------|
| Brush head usage counter | Resets when the "Reset Brush Head" button is pressed |
| Last brush pressure | Average brushing pressure across all tooth zones (extended 0308 format only) |
| Last brush areas | Pressure per tooth zone; individual zones as entity attributes and as 8 dedicated sensors. Source: `2604` notifications on Type-1 devices (Oclean X series) or extended 0308 format on Type-0 devices |
| Duration rating | Percentage of recommended 240 s brushing duration achieved (APK formula: `timeLong / 240`); calculated from session duration |
| Brushing quality metrics | Gesture code, 5-segment pressure ratio, and per-zone power distribution (0–3 scale) from 42-byte session records; exposed as sensors with detail attributes |
| Feature toggle switches | Brushing Reminder (CMD 0239) and Auto Power-Off Timer (CMD 0240); state persisted locally |
| Brush head reset button | Sends reset command to device; no response verification yet |
| Session history pagination | Fetches multiple pages of session history from device |
| Offline session import | Sessions recorded while HA was unreachable are imported on the next poll, provided they are still in the device's buffer. Note: the official Oclean app likely clears the buffer on sync – for best results, avoid using the official app in parallel. |
| HA long-term statistics | Sessions imported with their actual timestamps (historical data support) |
| Bluetooth auto-discovery | Device is found automatically if visible to HA Bluetooth |
| Older device protocol | Two data formats implemented; not yet tested on real non-X hardware |
| Multiple brushes in one household | Each brush is a separate config entry; not yet tested with multiple devices |

### ❌ Not Planned

| Feature | Details |
|---------|---------|
| Active-brushing detection | Removed in v1.3.0. The 0303 STATE byte correctly reports brushing status, but the device does not push BLE data during brushing and the polling interval is too long to catch the ~2–3 min brushing window reliably. |

---

## Configuration

The poll interval and other options can be changed after setup via **Settings → Integrations → Oclean → Configure**.

### Manual Polling Mode

Set the poll interval to `0` in **Settings → Integrations → Oclean → Configure** to disable automatic polling entirely. In this mode the device is only contacted when you explicitly trigger a poll.

Use the built-in `oclean_ble.poll` action to trigger a poll on demand – from automations, scripts, or the HA Developer Tools:

```yaml
action: oclean_ble.poll
data: {}             # polls all Oclean devices
```

To target a single device when you have multiple brushes, pass its `entry_id` (found under **Settings → Integrations → Oclean → ⋮ → System Information**):

```yaml
action: oclean_ble.poll
data:
  entry_id: "abc123def456..."
```

This is useful for event-driven automations, for example polling immediately after a bed-occupancy sensor detects you went to sleep:

```yaml
automation:
  trigger:
    - platform: state
      entity_id: binary_sensor.bed_occupancy
      to: "on"
  action:
    - action: oclean_ble.poll
      data: {}
```

The `oclean_ble.poll` action is also available when automatic polling is active – it simply triggers an immediate poll in addition to the regular schedule.

### Post-Brush Cooldown

After a new session is detected, polling can be paused for a configurable number of hours. This avoids unnecessary Bluetooth connections immediately after brushing, when no new data is expected.

Set the cooldown duration (in hours) in **Settings → Integrations → Oclean → Configure**. The default is `0` (disabled).

### Poll Windows

You can restrict polling to specific time windows to reduce Bluetooth traffic outside brushing hours. The options flow guides you step-by-step through up to 3 time windows (e.g. morning and evening) using a native time picker.

When no windows are configured, the device is polled at every interval.

### Debug Logging

The integration writes a dedicated log file `oclean_ble.log` to the same directory as your `configuration.yaml` (e.g. `/config/oclean_ble.log`). The file rotates at 1 MB and keeps up to 3 files.

**Debug entries (raw BLE payloads, parse results, etc.) are only written when the log level is set to `debug`.** Without this, only warnings and errors appear in both `oclean_ble.log` and the main HA log.

Add the following to `configuration.yaml`:

```yaml
logger:
  default: warning
  logs:
    custom_components.oclean_ble: debug
```

This enables debug output in both `oclean_ble.log` and the **main HA log** (Settings → Logs).

After brushing, filter the log for `Oclean` to see raw Bluetooth payloads.
Unknown notification types are logged as hex – this helps extend the parser.

---

## Compatibility

| Device | Model ID | Status | Notes |
|--------|----------|--------|-------|
| Oclean X | OCLEANY3M | ✅ Tested | Battery, score, timestamp, duration, and scheme ID confirmed. Extended fields (areas, pressure) not supported by this device. |
| Oclean X (HW variant) | OCLEANY3MH | ✅ Tested | Score, timestamp, duration, and pNum confirmed via `XX03` notification format (issue #19). |
| Oclean X Pro | OCLEANY3 | ⚠️ Partial | Session data fetched via `0307` + `*B#` multi-packet reassembly; implementation complete but needs real-device confirmation. |
| Oclean X Pro Elite | OCLEANY3P | ⚠️ Partial | Session metadata via `5100`, tooth areas via `021f`; implementation complete, full field confirmation pending. |
| Oclean X Pro 20 | OCLEANX20 | ⚠️ Partial | Extended-offset inline `0307` format; timestamp and duration confirmed, further field testing pending. |
| Oclean Z1 | OCLEANY5 | ⚠️ Partial | Type-Z1 protocol (APK handler C3350f mode=1): 0303/0202/0302 via fbb85, 0307 via fbb89, notify on fbb86+fbb90. Time calibration uses 0201 + 8-byte datetime format. Implementation complete (issue #69), real-device confirmation pending. |
| Oclean Air 1 | OCLEANA1 | ✅ Tested | Battery confirmed. No CCCD on notify characteristic – uses direct READ fallback. Session fields not available on this model. |
| Oclean Air 1 variants | OCLEANA1a–d | ⚠️ Partial | Same protocol as OCLEANA1; untested on real hardware. |
| Oclean X Ultra | OCLEANV1a | ⚠️ Partial | Battery, timestamp, duration, programme confirmed. Score and tooth areas pending (issue #81). |
| Other Oclean models | – | ⚠️ Unknown | Open an issue with raw log output |

> If brush session detail fields (areas, pressure, scheme) are missing or the timestamp looks wrong, enable debug logging, brush your teeth, and open an issue with the raw hex output from the HA log.

> If your device is not listed, enable debug logging and open an issue with the raw hex output. This is how Oclean X support was developed.

---

## Roadmap

- [ ] Confirm brush area / pressure / scheme fields on Oclean X Pro (OCLEANY3) and X Pro Elite (OCLEANY3P) with real-device logs (implementation complete)
- [ ] Validate real-time zone guidance on K3-series devices
- [x] Configurable poll windows with native time picker
- [x] Manual polling mode with `oclean_ble.poll` action
- [ ] Publish to HACS default repository
- [ ] Decode remaining unknown fields in session data

---

## Requirements

- Home Assistant **2023.4** or newer
- HA **Bluetooth** integration enabled (built-in; requires a compatible Bluetooth adapter or ESPHome proxy)
- `bleak` and `bleak-retry-connector` are bundled with HA's bluetooth stack – no separate installation required

---

## For Developers

Full protocol documentation including GATT UUIDs, command tables, byte layouts, and device type matrix:
→ [BLE Protocol Reference](docs/BLE-PROTOCOL-REFERENCE.md)

---

## Contributing & Feedback

Feedback and contributions are welcome.

- **Bug reports / feature requests:** Open an issue on GitHub
- **New device data:** Enable debug logging, brush your teeth, copy the raw hex output from the HA log, and open an issue
- **Pull requests:** PRs are welcome – please include a brief description of what was tested

---

## License

MIT – see [LICENSE](LICENSE)
