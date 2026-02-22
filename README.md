# Oclean Toothbrush (inofficial) – Home Assistant Integration

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)
[![HA Version](https://img.shields.io/badge/Home%20Assistant-2023.4%2B-blue)](https://www.home-assistant.io/)

> **Unofficial community integration.** This project is not affiliated with, endorsed by,
> or connected to Oclean / Zhuhai Ice Bear Smart Home Technology Co., Ltd.
> "Oclean" is a registered trademark of its respective owner.

Custom integration for **Oclean Smart Toothbrushes** (Oclean X, X Pro, X Pro Elite, X Ultra).

Connects every 5 minutes via Bluetooth, reads brushing data, then disconnects.
No cloud, no account, fully local.

> **Device tested:** Oclean X
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

### Sensors

| Entity | Description | Unit | Status |
|--------|-------------|------|--------|
| `sensor.oclean_battery` | Battery level | % | ✅ Tested |
| `sensor.oclean_last_brush_score` | Quality score of last session (0–100) | – | ✅ Tested |
| `sensor.oclean_last_brush_duration` | Duration of last session | s | ✅ Tested |
| `sensor.oclean_last_brush_time` | Timestamp of last session | timestamp | ✅ Tested |
| `sensor.oclean_brush_head_usage` | Brush head wear indicator | – | ⚠️ Unconfirmed |
| `sensor.oclean_last_brush_pressure` | Average brushing pressure across all tooth zones | – | ⚠️ Unconfirmed |
| `sensor.oclean_last_brush_clean` | Percentage of tooth zones that were cleaned | % | ⚠️ Unconfirmed |
| `sensor.oclean_last_brush_areas` | Number of cleaned tooth zones (0–8); individual zone values as attributes | – | ⚠️ Unconfirmed |
| `sensor.oclean_last_brush_scheme_id` | Brush programme ID; programme names are managed in the Oclean cloud | – | ⚠️ Unconfirmed |
| `sensor.oclean_last_brush_scheme_type` | Brush programme category | – | ⚠️ Unconfirmed |

### Binary Sensors

| Entity | Description | Status |
|--------|-------------|--------|
| `binary_sensor.oclean_brushing` | `on` while brushing | ⚠️ Unconfirmed |

### Buttons

| Entity | Description | Status |
|--------|-------------|--------|
| `button.oclean_reset_brush_head` | Resets the brush head wear counter | ⚠️ Unconfirmed |

**Legend:**
- ✅ **Tested** – confirmed working on a real Oclean X with multiple sessions
- ⚠️ **Unconfirmed** – implemented based on APK reverse-engineering; needs more device testing
- ❌ **Not implemented** – see [Roadmap](#roadmap)

---

## Feature Status

### ✅ Confirmed Working

| Feature | Details |
|---------|---------|
| Bluetooth connection | Automatic reconnect at each poll interval |
| Battery level | Read directly from device |
| Last brush score | Confirmed with multiple real sessions (Oclean X) |
| Last brush duration | In seconds (minimum reported: 30 s) |
| Last brush timestamp | Device local time |
| Time calibration | Device clock synced on every poll |
| Poll interval | Configurable 60–N seconds (default: 300 s) |
| Stale data persistence | Sensors keep last known value when device is unreachable |
| Config flow | Manual MAC address entry with validation |
| Duplicate prevention | Sessions deduplicated by timestamp; no double-import |

### ⚠️ Implemented – Needs More Testing

| Feature | Details |
|---------|---------|
| Brush head usage counter | Resets when the "Reset Brush Head" button is pressed |
| Last brush pressure | Average brushing pressure across all tooth zones |
| Last brush clean | Percentage of tooth zones that were cleaned (extended format only) |
| Last brush areas | Pressure per tooth zone; individual zones available as entity attributes |
| Last brush scheme ID / type | Programme ID and category (names are cloud-managed) |
| is_brushing detection | Not yet confirmed for Oclean X; always reports `off` currently |
| Brush head reset button | Sends reset command to device; no response verification yet |
| Session history pagination | Fetches multiple pages of session history from device |
| HA long-term statistics | Sessions imported with their actual timestamps (historical data support) |
| Bluetooth auto-discovery | Device is found automatically if visible to HA Bluetooth |
| Older device protocol | Two data formats implemented; not yet tested on real non-X hardware |

### ❌ Not Yet Implemented

| Feature | Details |
|---------|---------|
| Real-time brushing detection | is_brushing flag unreliable on Oclean X (see issue tracker) |
| Device model/firmware sensor | Device info response parsed but no version data extracted |
| German UI translations | Only English strings present |
| Multiple brushes in one household | Supported via multiple config entries; not tested |

---

## Configuration

The poll interval and device name can be changed after setup via **Settings → Integrations → Oclean → Configure**.

### Debug Logging

Add to `configuration.yaml`:

```yaml
logger:
  default: warning
  logs:
    custom_components.oclean_ble: debug
```

After brushing, filter the HA log for `Oclean` to see raw Bluetooth payloads.
Unknown notification types are logged as hex – this helps extend the parser.

---

## Compatibility

| Device | Status | Notes |
|--------|--------|-------|
| Oclean X | ✅ Tested | Full support: battery, score, duration, timestamp |
| Oclean X Pro | ⚠️ Unknown | Should work; same protocol family |
| Oclean X Pro Elite | ⚠️ Unknown | May use a different data format |
| Oclean X Ultra | ⚠️ Unknown | May use extended data format |
| Other Oclean models | ⚠️ Unknown | Open an issue with raw log output |

> If your device is unsupported, enable debug logging, brush your teeth, and open an issue with the raw hex output. This is how the Oclean X support was developed.

---

## Roadmap

- [ ] Confirm is_brushing detection on Oclean X
- [ ] Validate tooth zone pressure data on real hardware
- [ ] Validate real-time zone guidance on K3-series devices
- [ ] Add German translations (`translations/de.json`)
- [ ] Publish to HACS default repository
- [ ] Decode remaining unknown fields in Oclean X session data

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
