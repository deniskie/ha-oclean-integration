# Oclean BLE Protocol – Complete Reference

> **Basis:** Reverse-engineering of the decompiled Oclean Android APK (`com.yunding.noopsychebrushforeign`)
> **Analyzed source files:** `p105g/AbstractC3347e.java`, `p105g/C3335a.java`, `p105g/C3337a1.java`,
> `p105g/C3339b0.java`, `p105g/C3340b1.java`, `p105g/C3346d1.java`, `p105g/C3350f.java`,
> `p105g/C3352g.java`, `p105g/C3354h.java`, `p105g/C3367n0.java`, `p105g/C3376s.java`,
> `p105g/C3381u0.java`, `p105g/C3385w0.java`, `p105g/C3387x0.java`, `p105g/C3389y0.java`,
> `p105g/C3391z0.java`, `com/ocleanble/lib/OcleanBleManager.java`

---

## Table of Contents

1. [GATT Profile: Services & Characteristics](#1-gatt-profile-services--characteristics)
2. [Frame Format & Response Types](#2-frame-format--response-types)
3. [Command Table – Standard Protocol (02xx / 03xx)](#3-command-table--standard-protocol-02xx--03xx)
4. [Command Table – WiFi Protocol (F2xx / F3xx / F7xx)](#4-command-table--wifi-protocol-f2xx--f3xx--f7xx)
5. [Command Table – Special Commands](#5-command-table--special-commands)
6. [Response Formats (complete byte layouts)](#6-response-formats-complete-byte-layouts)
   – 6.2a Simple 0308 (20 bytes), 6.2b Extended 0308 (32+ bytes), 6.2c K3GUIDE 0340
7. [Brush Head Counter & Reset](#7-brush-head-counter--reset)
8. [Device Type Matrix](#8-device-type-matrix)
9. [Connection & Synchronization Flow](#9-connection--synchronization-flow)
10. [Error Codes](#10-error-codes)
11. [Open Questions](#11-open-questions)

---

## 1. GATT Profile: Services & Characteristics

### 1.1 Oclean Standard Service (Type 0 / Type 1)

```
Service UUID: 8082caa8-41a6-4021-91c6-56f9b954cc18
```

| Characteristic | UUID | Properties | Device Types |
|----------------|------|------------|--------------|
| Read / Notify | `5f78df94-798c-46f5-990a-855b673fbb86` | Read, Notify | All |
| Write (Command) | `9d84b9a3-000c-49d8-9183-855b673fbb85` | Write | All |
| Change Info | `6c290d2e-1c03-aca1-ab48-a9b908bae79e` | Notify | Type 0 only |
| Send Brush CMD | `5f78df94-798c-46f5-990a-855b673fbb89` | Write | Type 1 only |
| Receive Brush | `5f78df94-798c-46f5-990a-855b673fbb90` | Notify | Type 1 only |

### 1.2 Oclean WiFi Service (Type F – C3346d1)

```
Service UUID:         0000aaaa-0000-1000-8000-00805f9b34fb
Write Characteristic: 0000bbb2-0000-1000-8000-00805f9b34fb  (Write)
Notify Characteristic: 0000bbb3-0000-1000-8000-00805f9b34fb  (Read, Notify)
```

### 1.3 Standard BLE Battery Service

```
Service UUID:        0000180f-0000-1000-8000-00805f9b34fb
Characteristic UUID: 00002a19-0000-1000-8000-00805f9b34fb  (Read, Notify)
```

Returns the battery level as a single byte value (0–100 %).

---

## 2. Frame Format & Response Types

### 2.1 Request Format (App → Device)

```
┌─────────────────┬────────────────────────────────────┐
│  Command Bytes  │            Payload                  │
│   (2–3 bytes)   │       (variable, may be empty)      │
└─────────────────┴────────────────────────────────────┘
```

- Write type `WRITE_TYPE_WITH_RESPONSE` (ACK expected) for all control commands
- Write type `WRITE_TYPE_NO_RESPONSE` exclusively for OTA firmware chunks
- Byte order: predominantly **Big-Endian**; exceptions (LE) explicitly marked

Internally, the SDK base class `AbstractC3347e` uses two core methods:
```java
m5388r(BluetoothGattCharacteristic, byte[], boolean waitResponse, boolean?, listener)
  └→ m5389s()  // queued write with LinkedBlockingQueue
```

### 2.2 Response Format (Device → App)

Responses arrive as GATT notifications on the **Read/Notify Characteristic**:

```
┌────────────────┬────────────────────────────────────┐
│  Type Marker   │            Payload                  │
│   (2 bytes)    │       (variable)                    │
└────────────────┴────────────────────────────────────┘
```

**Response type markers:**

| Marker (Bytes 0–1) | Constant | Triggered by | Meaning |
|--------------------|----------|--------------|---------|
| `03 03` | `RESP_STATE` | `CMD_QUERY_STATUS` | Device status |
| `03 08` | `RESP_INFO` | `CMD_QUERY_RUNNING_DATA` | Brush session data |
| `02 02` | `RESP_DEVICE_INFO` | `CMD_DEVICE_INFO` | ACK "OK" |
| `02 0F` | – | `CMD_CLEAR_BRUSH_HEAD` | ACK |
| `09 ED` | – | Device push | STATE type (push notifications) |
| `03 07` | – | Device push | INFO type (push notifications) |
| `03 02` | – | `030201` | Detailed device info response (34 bytes) |

### 2.3 ACK Format

For most setting commands (Boolean values, `020F`, etc.) the device responds with:
```
Parsing: bytesToAscii(response, response.length - 2, 2)
Type:    ReceivedType.STATE
Example: [0x02, 0x0F, 0x4F, 0x4B] → "OK"
```

---

## 3. Command Table – Standard Protocol (02xx / 03xx)

### 3.1 Time Calibration & Date

| Hex | Function | Payload | Source |
|-----|----------|---------|--------|
| `020E` | **Time calibration (Calibrate Time)** | 4 bytes, Unix timestamp (seconds), **Big-Endian** | `mo5289B` |
| `0201` | Set date / time | Time string as bytes | `mo5292L` |

**Example `020E`:**
```python
import struct, time
cmd = bytes.fromhex("020E") + struct.pack(">I", int(time.time()))
```

---

### 3.2 Device Queries

| Hex | Function | Payload | Response Marker | Source |
|-----|----------|---------|-----------------|--------|
| `0202` | Device information (simple) | – | `0202 4F 4B` (ACK) | `mo5310r0` |
| `030201` | Device information (detailed, 34 bytes) | – | `0302 ...` | `mo5301W0` |
| `0303` | **Device status** | – | `0303 ...` | `mo5295Q0` |
| `0307` | Firmware version / device data (INFO type) | – | `0307 ...` | `mo5364I0` |
| `0308` | **Brush session data (Running Data)** | – | `0308 ...` | `mo5299S0` |
| `0309` | Brush session data, next page | – | `0308 ...` | `mo5301W0` |
| `0723` | Hardware & serial number (36 bytes ASCII) | – | `0723 ...` | – |
| `020B` | Protocol version | – | STATE type | – |
| `0234` | Info query (Extended) | – | INFO type | – |
| `0235` | Settings query (Extended) | – | STATE type | – |
| `0236` | Info query | – | STATE type | – |
| `0237` | Info query | – | STATE type | – |
| `0239` | Info query | – | STATE type | – |
| `0240` | Info query | – | STATE type | – |
| `0241`–`0245` | Info queries (C3367n0 only) | – | STATE type | – |
| `0313` | 4-byte info | – | STATE type | – |
| `0314` | Info query | – | INFO type | – |
| `03A0` | Info query (Extended) | – | INFO type | – |
| `0341` | Info query (C3367n0, z11=true) | – | STATE type | – |

---

### 3.3 Brush Head & Reset

| Hex | Function | Payload | Response | Source |
|-----|----------|---------|----------|--------|
| `020F` | **Reset brush head data** | – (no payload) | ACK | `mo5322F0` |

Full documentation → [Section 7](#7-brush-head-counter--reset)

---

### 3.4 Brush Scheme Commands

| Hex | Function | Payload |
|-----|----------|---------|
| `0206` | Transfer brush scheme (packet 1) | pnum + step count + step data |
| `020B` | Transfer brush scheme (packet 2) | Marker `0x2A`/`0x2B` + remaining data |
| `0315` | Set brush area type | `BrushAreaType` enum (1 byte) |
| `0316` | Brush area setting | 1 byte |

---

### 3.5 Device Settings (Boolean / Byte Values)

| Hex | Function | Payload | Notes |
|-----|----------|---------|-------|
| `0203` | Pause / Resume | 3 bytes: pause flag + gear mode | – |
| `020C` | Set brightness | 1 byte, brightness value | – |
| `0207` | Motor speed / level | 4 bytes, Big-Endian integer | – |
| `0209` | Enable wake gesture | `0x01` = active, `0xEC` = inactive | `mo5305l0` |
| `020D` | Setting (Boolean) | 1 byte | `mo5298S` |
| `0210` | Setting | 1 byte | C3337a1 only |
| `0211` | Set birthday / user profile | Date string + pNum | `mo5315Z` |
| `0212` | Setting (Boolean) | 1 byte | `mo5290G0` |
| `0213` | Setting (Boolean) | 1 byte | `mo5338h0` |
| `0214` | Setting | 1 byte | C3385w0 only |
| `0215` | Setting / Battery query | 1 byte | C3350f, C3385w0 only |
| `0216` | Setting (Integer, 1 byte) | 1 byte | `mo5335e0` |
| `0217` | Setting (2 bytes, Big-Endian) | 2 bytes | – |
| `0221` | Setting | 1 byte | C3385w0 only |
| `0222` | Setting (Boolean) | 1 byte | C3354h, C3352g |
| `0223` | Setting (Boolean) | 1 byte | – |
| `0224` | Setting (Boolean) | 1 byte | C3354h, C3352g |
| `0225` | Setting (Boolean) | 1 byte | – |
| `0226` | Setting (Boolean) | 1 byte | C3354h only |
| `0227` | Setting (Boolean) | 1 byte | C3354h only |
| `0228` | Setting (Boolean) | 1 byte | – |
| `0230` | Setting (Boolean) | 1 byte | – |
| `0231` | 3 settings | 3 bytes | – |
| `0233` | Set URL / string | URL string (variable) | – |
| `023A` | Setting | – | C3381u0 only |
| `02A0` | Setting (Boolean) | 1 byte | `mo5331X0` |
| `02A1`–`02A5` | Settings (Extended) | variable | C3367n0 / C3387x0 only |

---

## 4. Command Table – WiFi Protocol (F2xx / F3xx / F7xx)

Applies to **WiFi-capable devices** (class `C3346d1`). The F-commands are functionally identical to the standard commands (02xx/03xx) but use different BLE UUIDs (Service `0000aaaa-...`).

**Mapping Standard → WiFi:**

| Standard Hex | WiFi Hex | Function |
|-------------|----------|----------|
| `0201` | `F201` | Set date / time |
| `0202` | `F202` | Device status (Reset) |
| `0203` | `F203` | Pause / Resume |
| `0206` | `F206` | Brush scheme packet 1 |
| `020B` | `F20B` | Brush scheme packet 2 |
| `020C` | `F20C` | Brightness / Boolean |
| `020D` | `F20D` | Boolean setting |
| `0209` | `F209` | Wake gesture |
| `0211` | `F211` | Birthday / profile |
| `0212` | `F212` | Boolean setting |
| `0213` | `F213` | Boolean setting |
| `0216` | `F216` | Integer setting |
| `0223` | `F223` | Set string / name |
| `0226` | `F226` | Boolean setting |
| `0303` | `F303` | Device status query |
| `0307` | `F307` | Firmware / device data query |
| `0308` | `F308` | Brush session data |
| `0309` | `F309` | Brush session data (next page) |
| `030201` | `F30201` | Full device info |
| `09EDEF` | `F9EDEF` | **Factory Reset** |

**WiFi-specific commands (no standard equivalent):**

| WiFi Hex | Function | Source |
|----------|----------|--------|
| `F715` | Set MAC address | `mo5351a0` |
| `F725` | Unknown | C3346d1 |
| `F726` | Download license / URL | `mo5350M` |

---

## 5. Command Table – Special Commands

| Hex | Function | Payload | Notes |
|-----|----------|---------|-------|
| `09EDEF` | **Factory Reset** (standard devices) | – | `waitResponse=false` |
| `F9EDEF` | **Factory Reset** (WiFi devices) | – | `waitResponse=false` |
| `07B3` | Unknown | – | C3387x0 only |
| `030A` | Unknown | – | C3335a only |
| `030B` | Unknown | – | C3335a only |
| `030D` | Unknown | – | C3335a only |
| `0306` | Conditional query (type check) | – | C3389y0 only |

**On `09EDEF` / `F9EDEF` – Factory Reset:**

```java
// AbstractC3347e.java
public void I1111llI1(OnOcleanCommandListener listener) {
    m5388r(this.f12501k,
           ConverterUtils.INSTANCE.hexStringToBytes("09EDEF"),
           false, false, listener);  // waitResponse=false!
}

// C3346d1.java (WiFi override)
public final void I1111llI1(OnOcleanCommandListener listener) {
    m5388r(this.f12501k,
           ConverterUtils.INSTANCE.hexStringToBytes("F9EDEF"),
           false, false, listener);
}
```

- **No payload**, no response expected (`waitResponse=false`)
- Called in the app via the obfuscated method `I1111llI1()`
- The 2-byte prefix `09ED` matches the STATE response marker → the device sends a STATE push after the reset

---

## 6. Response Formats (complete byte layouts)

### 6.1 `0303` STATUS Response – Device Status

Response to `CMD_QUERY_STATUS`. After removing the 2-byte marker:

```
Byte 0  : Status flags (integer value read by app)
           Bit 0 = is_brushing (0 = idle, 1 = brushing)
           Further bits: undocumented
Byte 1  : NOT PARSED by the official app (reserved / device-internal use)
Byte 2  : NOT PARSED by the official app (observed: varies 0x0f–0x1d, likely a counter)
Byte 3  : capacity = battery level (0–100 %)
Bytes 4+: NOT PARSED / device-specific
```

**JSON representation (AbstractC0002b.m15c):**
```json
{"status": <byte0>, "capacity": <byte3>}
```

**APK source (C3367n0.java, lines 749–757):**
```java
} else if (str.equals("0303")) {
    if (bArr2.length >= 4) {
        iBytesToIntLe2 = converterUtils.bytesToIntLe(bArr2, 0, 1);  // byte0: status
        iBytesToIntLe  = converterUtils.bytesToIntLe(bArr2, 3, 4);  // byte3: capacity
    }
    AbstractC3347e.m5356u(this, true, ReceivedType.INFO,
        AbstractC0002b.m15c(iBytesToIntLe2, iBytesToIntLe));
}
```

Bytes 1 and 2 are read from the BLE packet but not extracted into any named field. Their purpose is unknown and they can be safely ignored.

---

### 6.2 `0308` INFO Response – Brush Session Record

Response to `CMD_QUERY_RUNNING_DATA`. Two formats exist in the firmware, distinguished by the first byte.

#### 6.2a Simple Format (20 bytes) – `C3340b1.m5348m1()`

Used by older / simpler Oclean models. Byte 0 = year-2000 (always ≥ 1 for any date after 2001).

```
┌───────┬──────┬────────┬────────────────────┬───────────────────────────────────────────┐
│ Byte  │ Size │ Endian │ Field              │ Description                               │
├───────┼──────┼────────┼────────────────────┼───────────────────────────────────────────┤
│ 0     │ 1    │ BE     │ year               │ +2000 gives full year                     │
│ 1     │ 1    │ BE     │ month              │ 1–12                                      │
│ 2     │ 1    │ BE     │ day                │ 1–31                                      │
│ 3     │ 1    │ BE     │ hour               │ 0–23                                      │
│ 4     │ 1    │ BE     │ minute             │ 0–59                                      │
│ 5     │ 1    │ BE     │ second             │ 0–59                                      │
│ 6     │ 1    │ BE(s)  │ tz_offset          │ Timezone in quarter-hours (signed int8)   │
│       │      │        │                    │ Example: +32 = UTC+8 (+32×15=+480 min)    │
│ 7     │ 1    │ BE     │ week               │ Calendar week                             │
│ 8     │ 1    │ BE     │ pNum               │ Brush scheme ID                           │
│ 9–13  │ 5    │ –      │ RESERVED           │ Not used, always ignored                  │
│ 14–15 │ 2    │ LE     │ blunt_teeth        │ Brush head usage indicator (see §7)       │
│ 16–17 │ 2    │ LE     │ pressure_raw       │ Raw pressure; pressure_raw / 300 = float  │
│ 18–19 │ 2    │ –      │ (unknown)          │ Only when MTU > 20; purpose unclear       │
└───────┴──────┴────────┴────────────────────┴───────────────────────────────────────────┘
```

#### 6.2b Extended Format (32+ bytes) – `AbstractC0002b.m37y()`

Used by K3-series and newer models. Identified by bytes 0–1 being a **Big-Endian uint16 record-length header**
(byte 0 = 0 for all BLE payloads; byte 1 = total record length ≥ 32). Contains full session data including
score, tooth zone pressures, pNum and schemeType directly in the BLE packet.

```
┌────────┬──────┬────────┬──────────────────────┬──────────────────────────────────────────────┐
│ Byte   │ Size │ Endian │ Field                │ Description                                  │
├────────┼──────┼────────┼──────────────────────┼──────────────────────────────────────────────┤
│ 0–1    │ 2    │ BE     │ record_length        │ Total record length in bytes (= 32 + extras) │
│ 2      │ 1    │ BE     │ year                 │ +2000 gives full year                        │
│ 3      │ 1    │ BE     │ month                │ 1–12                                         │
│ 4      │ 1    │ BE     │ day                  │ 1–31                                         │
│ 5      │ 1    │ BE     │ hour                 │ 0–23                                         │
│ 6      │ 1    │ BE     │ minute               │ 0–59                                         │
│ 7      │ 1    │ BE     │ second               │ 0–59                                         │
│ 8      │ 1    │ BE     │ pNum                 │ Brush scheme ID (cloud-managed name)         │
│ 9–10   │ 2    │ BE     │ duration             │ Total session duration (seconds)             │
│ 11–12  │ 2    │ BE     │ validDuration        │ Duration with valid pressure (seconds)       │
│ 13–17  │ 5    │ BE     │ pressureZones[0..4]  │ 5 intermediate zone pressure values          │
│ 18     │ 1    │ –      │ RESERVED             │ Padding / future use                         │
│ 19     │ 1    │ BE(s)  │ tz_offset            │ Timezone in quarter-hours (signed int8)      │
│ 20     │ 1    │ BE     │ area_upper_left_out  │ AREA_LIFT_UP_OUT pressure (BrushAreaType=1)  │
│ 21     │ 1    │ BE     │ area_upper_left_in   │ AREA_LIFT_UP_IN pressure  (BrushAreaType=2)  │
│ 22     │ 1    │ BE     │ area_lower_left_out  │ AREA_LIFT_DOWN_OUT pressure (=3)             │
│ 23     │ 1    │ BE     │ area_lower_left_in   │ AREA_LIFT_DOWN_IN pressure  (=4)             │
│ 24     │ 1    │ BE     │ area_upper_right_out │ AREA_RIGHT_UP_OUT pressure  (=5)             │
│ 25     │ 1    │ BE     │ area_upper_right_in  │ AREA_RIGHT_UP_IN pressure   (=6)             │
│ 26     │ 1    │ BE     │ area_lower_right_out │ AREA_RIGHT_DOWN_OUT pressure (=7)            │
│ 27     │ 1    │ BE     │ area_lower_right_in  │ AREA_RIGHT_DOWN_IN pressure  (=8)            │
│ 28     │ 1    │ BE     │ score                │ Brush quality score 0–100                    │
│ 29     │ 1    │ BE     │ schemeType           │ Scheme category 0–8                          │
│ 30     │ 1    │ BE     │ busBrushing          │ Flag: 1 = session from cloud sync            │
│ 31     │ 1    │ BE     │ crossNumber          │ Overcross count (overPullNum)                │
│ 32+    │ var  │ –      │ pressureProfile      │ Variable-length pressure profile data        │
└────────┴──────┴────────┴──────────────────────┴──────────────────────────────────────────────┘
```

**Format disambiguation** (implemented in `parser.py`):
```python
# payload = data[2:]  (after stripping the 0308 response type prefix)
if payload[0] == 0 and payload[1] >= 32 and len(payload) >= payload[1]:
    # Extended format
else:
    # Simple format (payload[0] = year-2000 ≥ 24 for current dates)
```

**Tooth zone pressure values (bytes 20–27):**
Each byte is a raw pressure value 0–255. Zero means the zone was not brushed.
Zone names are taken from `com/ocleanble/lib/device/BrushAreaType.java`:

| BrushAreaType value | Byte index | Zone name | Anatomy |
|---------------------|-----------|-----------|---------|
| 1 | 20 | `upper_left_out` | Upper left, outer surface |
| 2 | 21 | `upper_left_in` | Upper left, inner surface |
| 3 | 22 | `lower_left_out` | Lower left, outer surface |
| 4 | 23 | `lower_left_in` | Lower left, inner surface |
| 5 | 24 | `upper_right_out` | Upper right, outer surface |
| 6 | 25 | `upper_right_in` | Upper right, inner surface |
| 7 | 26 | `lower_right_out` | Lower right, outer surface |
| 8 | 27 | `lower_right_in` | Lower right, inner surface |
| 255 | – | STOP | Brushing stopped |

**Timestamp calculation (same for both formats):**
```python
import datetime, calendar

tz_minutes = (tz_offset if tz_offset < 128 else tz_offset - 256) * 15
device_dt = datetime.datetime(year + 2000, month, day, hour, minute, second)
utc_dt = device_dt - datetime.timedelta(minutes=tz_minutes)
unix_timestamp = calendar.timegm(utc_dt.timetuple())
```

**Pagination:** If the device has more sessions stored than fit in one notification, `0308` delivers the
newest page. Older pages are fetched with `0309` (same format, same disambiguation logic).

---

### 6.2c `0340` K3GUIDE – Real-Time Zone Guidance

Sent by K3-series devices **during active brushing** to indicate the current active tooth zone and live
pressure per quadrant. Used to drive the guidance display on the device. Not stored as persistent sensor
state in the integration.
Source: `C3367n0.java:737–745`.

```
┌───────┬──────┬────────┬──────────────────────┬──────────────────────────────────────────────┐
│ Byte  │ Size │ Endian │ Field                │ Description                                  │
├───────┼──────┼────────┼──────────────────────┼──────────────────────────────────────────────┤
│ 0     │ 1    │ BE     │ liftUp               │ Left upper zone live pressure (0–255)        │
│ 1     │ 1    │ BE     │ liftDown             │ Left lower zone live pressure (0–255)        │
│ 2     │ 1    │ BE     │ rightUp              │ Right upper zone live pressure (0–255)       │
│ 3     │ 1    │ BE     │ rightDown            │ Right lower zone live pressure (0–255)       │
│ 4     │ 1    │ BE     │ currentPosition      │ Active zone ID: 1–8 (BrushAreaType); 255=stop│
│ 5     │ 1    │ BE     │ workingState         │ Device working state                         │
└───────┴──────┴────────┴──────────────────────┴──────────────────────────────────────────────┘
```

Triggered via `ChangeType.K3GUIDE` (value 8) in the SDK event dispatcher.

---

### 6.3 `0302` Device Info Response (34+ bytes)

Response to `CMD_DEVICE_INFO` (`030201`). Contains full device configuration.
Source: `C3367n0.java:761–824` (fully analyzed).

```
┌────────┬──────┬────────┬──────────────────────┬─────────────────────────────────────────┐
│ Byte   │ Size │ Endian │ Field                │ Description                             │
├────────┼──────┼────────┼──────────────────────┼─────────────────────────────────────────┤
│ 0      │ 1    │ BE     │ batteryLevel         │ Battery level 0–100 %                   │
│ 1      │ 1    │ BE     │ networkStatus        │ 0 = offline, ≠0 = online (WiFi)         │
│ 2      │ 1    │ BE     │ raiseWake            │ 1 = Lift-to-Wake active                 │
│ 3      │ 1    │ BE     │ voiceMainSwitch      │ 1 = Voice output active                 │
│ 4      │ 1    │ BE     │ (reserved)           │ –                                       │
│ 5      │ 1    │ BE     │ voiceMainSwitch (2)  │ 1 = active (duplicate or variant)       │
│ 6      │ 1    │ BE     │ bindState            │ 2 = device is bound                     │
│ 7      │ 1    │ BE     │ (reserved)           │ –                                       │
│ 8      │ 1    │ BE     │ modeNum              │ Current mode (0–N)                      │
│ 9–10   │ 2    │ BE     │ overCross            │ 1 = over-crossing detected (active)     │
│ 11     │ 1    │ BE     │ brushSongSwitch      │ 1 = Brushing music active               │
│ 12     │ 1    │ BE     │ deviceTheme          │ Theme ID                                │
│ 13–15  │ 3    │ BE     │ (reserved)           │ –                                       │
│ 16     │ 1    │ BE     │ year - 2000          │ Device clock: year                      │
│ 17     │ 1    │ BE     │ month                │ Device clock: month                     │
│ 18     │ 1    │ BE     │ day                  │ Device clock: day                       │
│ 19     │ 1    │ BE     │ hour                 │ Device clock: hour                      │
│ 20     │ 1    │ BE     │ minute               │ Device clock: minute                    │
│ 21     │ 1    │ BE     │ second               │ Device clock: second                    │
│ 22     │ 1    │ BE     │ (reserved)           │ –                                       │
│ 23     │ 1    │ BE     │ areaRemind           │ ≠0 = area reminder active               │
│ 24     │ 1    │ BE     │ timezoneOffset       │ Timezone code (minute-offset mapping)   │
│ 25–30  │ 6    │ BE     │ (reserved)           │ –                                       │
│ 31     │ 1    │ BE     │ deviceLanguage       │ Language ID                             │
│ 32–33  │ 2    │ BE     │ (reserved)           │ –                                       │
└────────┴──────┴────────┴──────────────────────┴─────────────────────────────────────────┘
```

**Note:** No brush head counter in this response. The device description JSON contains `batteryLevel`, `networkStatus`, `voiceMainSwitch`, `bindState`, `brushSongSwitch`, `areaRemind`, `deviceLanguage`, `modeNum`, `overCross`, date/time.

---

### 6.4 `0307` INFO Response – Firmware / Session Data

Response to CMD `0307`, arrives as INFO type notification.

**Two observed formats:**

**Format A – ASCII version string** (used by most device types per APK source):
```
Format: Version string as ASCII or JSON
Example: {"firmware": "1.2.3", "hardware": "2.0"}
APK handler: AbstractC3347e.m5374d0() → bytesToAscii()
```

**Format B – 20-byte binary session record** (observed on Oclean X / OCLEANY3M):
```
Byte layout (payload after stripping the 0307 prefix):
  Bytes 0-4 : device/model constant (0x2a 0x42 0x23 0x00 0x00 on Oclean X)
  Byte 5    : year - 2000  (confirmed)
  Byte 6    : month        (confirmed)
  Byte 7    : day          (confirmed)
  Byte 8    : hour         (confirmed)
  Byte 9    : minute       (confirmed)
  Byte 10   : second       (confirmed)
  Byte 11   : unknown (highly variable; 0x00, 0x4c, 0xe7, 0x13, 0x1f, 0x1c, 0x4d)
  Byte 12   : 0x00 (consistent; padding)
  Byte 13   : unknown (NOT parsed by official APK; purpose unconfirmed)
  Byte 14   : 0x00 (consistent; padding)
  Byte 15   : unknown (NOT always equal to byte 13; purpose unknown)
  Byte 16   : unknown (observed: 0x00, 0x02, 0x07, 0x01, 0x64)
  Byte 17   : session counter? (empirically increasing; observed 0, 1, 4, 5)

NOTE: The official APK does NOT parse this binary format – it treats all 0307
responses as ASCII strings. The byte 5-10 timestamp mapping was confirmed
empirically from 5 Oclean X sessions (2026-02-21 to 2026-02-22). All other
byte interpretations are unconfirmed hypotheses.
```

---

### 6.5 `0723` Hardware/Serial Response (36 bytes)

Split into two 18-byte packets (MTU fragmentation):

```
Format: ASCII string  "{hardware},{serial}."
0x2C (,) separates hardware version from serial number
0x2E (.) marks the end
→ Result: {"hardware": "X.Y", "serial": "OCLEAN12345"}
```

---

### 6.6 ACK Response for Setting Commands

For Boolean commands (`0212`, `0213`, `020F`, `0209`, etc.):

```
Response: [cmd_byte0, cmd_byte1, ..., 0x4F, 0x4B]
                                         "OK"
Parsing:  bytesToAscii(data, data.length - 2, 2) → "OK"
ReceivedType.STATE
```

---

## 7. Brush Head Counter & Reset

### 7.1 Data Source: `blunt_teeth` (Bytes 14–15 in the 0308 Record)

The device transmits a 16-bit value (Little-Endian) at position 14–15 in every Running Data record (0308), which serves as the brush head usage indicator:

```python
blunt_teeth = int.from_bytes(data[14:16], byteorder="little")
```

| Behavior | Description |
|----------|-------------|
| After brush head replacement | Starts at 0 |
| After each brush session | Increases |
| After `020F` command | Reset to 0 |

**Note:** The exact counting mechanism (linear +1 per session or ADC-based wear value) cannot be fully determined from the APK source code. The value is named "brush head wear indicator" (`bluntTeethNumber` in the original naming).

### 7.2 Data Sources in the Original App (not BLE)

| Source | Field | Access |
|--------|-------|--------|
| Cloud API | `BrushHeadEntity.count` (alias `timesNum`) | HTTP POST |
| MMKV local | `"BurshHead{MAC}/Count"` (int) | Android-internal |
| MMKV local | `"BurshHead{MAC}/Days"` (int) | Android-internal |
| BLE | `blunt_teeth` (Bytes 14–15 in 0308) | **Directly available** |

### 7.3 Reset Command `020F`

```
BLE Write to: WRITE_CHAR_UUID (9d84b9a3-000c-49d8-9183-855b673fbb85)
Bytes:        [0x02, 0x0F]
Payload:      – (no payload)
Response:     ACK (ReceivedType.STATE, last 2 bytes as ASCII)
```

**Implementation reference (APK):**
```java
// OcleanBleManager.java:667
public final void clearHeadData(String mac, OnOcleanCommandListener listener) {
    abstractC3347e.mo5322F0(listener);
}

// All device type classes (identical):
public final boolean mo5322F0(OnOcleanCommandListener listener) {
    m5388r(WRITE_CHAR_UUID, hexStringToBytes("020F"), true, false, listener);
    return true;
}
```

**Implemented in (all identical):**
`C3335a` · `C3339b0` · `C3350f` · `C3352g` · `C3354h` · `C3376s` · `C3381u0` · `C3385w0`

---

## 8. Device Type Matrix

| Class | Type | Devices (Examples) | Special Features |
|-------|------|-------------------|-----------------|
| `C3335a` | 0 | Standard models (older) | `CHANGE_INFO_UUID` for notifications |
| `C3340b1` | 0/1 | Oclean X, F, Z1 | Base implementation for Type 0 Running Data |
| `C3350f` | 0/1 | Premium with screen | Switch via `f12514u` (0 or 1) |
| `C3352g` | 0/1 | Mixed types | JADX decompilation incomplete |
| `C3354h` | 1 | Premium | Direct implementation without switch |
| `C3367n0` | 1+ | Extended with screen | Many additional commands (0241–0245, 02A1–02A5) |
| `C3376s` | 1 | Oclean X Pro, W10 | Full Type-1 implementation |
| `C3381u0` | 1 | Further premium models | Identical to C3376s |
| `C3385w0` | 0/1 | Switch-based | Similar to C3350f |
| `C3387x0` | Extended | With screen + 0313 | Additional extended commands |
| `C3339b0` | Extended | Another variant | `0231`, `0234`–`0237`, `0239`, `0240` |
| `C3346d1` | F (WiFi) | WiFi models | Own UUIDs + F2xx/F3xx commands |
| `C3389y0` | Special | Conditional | `0306` with type check |
| `C3391z0` | Special | – | `0301`, `0302` for multi-page queries |

**Type-specific command differences:**

| Command | Type 0 Standard | Type 1 Premium | WiFi (Type F) |
|---------|----------------|----------------|---------------|
| Request running data | `0308` → WRITE_CHAR | `0307` → SEND_BRUSH_CMD_UUID | `F308` → WRITE_F |
| Receive running data | READ_NOTIFY_CHAR | RECEIVE_BRUSH_UUID | RECEIVE_F |
| Brush head reset | `020F` → WRITE_CHAR | `020F` → WRITE_CHAR | `F20F` (unclear) |
| Status | `0303` → WRITE_CHAR | `0303` → WRITE_CHAR | `F303` → WRITE_F |
| Factory reset | `09EDEF` | `09EDEF` | `F9EDEF` |

---

## 9. Connection & Synchronization Flow

### 9.1 Standard Flow (Home Assistant BLE Integration)

```
1. BLE Connect (establish_connection, max 3 attempts)
2. await asyncio.sleep(2.0)          ← GATT cache warmup for proxy backends
3. WRITE 020E + 4-byte BE timestamp  ← Time calibration
4. start_notify READ_NOTIFY_CHAR
5. start_notify RECEIVE_BRUSH_UUID   ← Type 1 only (ignored if not present)
6. start_notify CHANGE_INFO_UUID     ← Type 0 only (ignored if not present)
7. start_notify SEND_BRUSH_CMD_UUID  ← Type 1 only (ignored if not present)
8. WRITE 0303                        ← Device status (is_brushing, capacity)
9. WRITE 0202                        ← Device info (ACK)
10. WRITE 0308                       ← Brush sessions (Running Data)
11. await asyncio.sleep(3.0)         ← Wait for notifications
12. READ 00002a19-...                ← Battery characteristic (standard BLE)
13. stop_notify (all)
14. Disconnect
```

### 9.2 Real-Time Brushing Flow (Push from Device)

```
Device → App: 0303 response (is_brushing=1)        ← Brush start
Device → App: 0308 notifications (real-time data)  ← During brushing
Device → App: 0303 response (is_brushing=0)        ← Brush end
App → Device: WRITE 0308                           ← Request full session
Device → App: 0308 response (final session data)   ← Complete record
```

---

## 10. Error Codes

### 10.1 SDK Error Codes (OcleanBleManager)

| Code | Meaning |
|------|---------|
| `69` | Device not connected / not in BLE registry |
| `70` | BLE command could not be sent (characteristic missing) |

### 10.2 Connection Error States

| State | Cause | Resolution |
|-------|-------|------------|
| Device not in HA registry | Device not yet seen by BT scanner | Wait 30 s, turn device on |
| `IndexError` (habluetooth proxy) | Empty `BLEDevice` stub | Caught as `BleakError` |
| `BleakError` on CHANGE_INFO_UUID | Characteristic only on Type-0 devices | Ignorable, caught in `except` |
| Timeout | Device doesn't respond within BLE_CONNECT_TIMEOUT (10 s) | Retry at next poll interval |

---

## 11. Open Questions

### 11.1 Resolved Points (since last version)

| Point | Status | Result |
|-------|--------|--------|
| 0308 Bytes 9–13 | ✅ Resolved | Bytes 9–13 are **reserved and not used**. Byte 9 was incorrectly documented as pNum – corrected: pNum = Byte 8 (simple format). |
| `09EDEF` command | ✅ Resolved | **Factory Reset**. Method `I1111llI1()`. `waitResponse=false`. |
| WiFi device type | ✅ Resolved | Class `C3346d1`, own UUIDs, F2xx/F3xx command mapping. |
| 0308 Record size | ✅ Resolved | **Two formats**: simple = 20 bytes (`C3340b1.m5348m1`); extended = 32+ bytes (`AbstractC0002b.m37y`). Format identified by first byte (0 = extended, year-2000 ≥ 1 = simple). |
| 0308 Bytes 18–19 (simple) | ✅ Resolved | In the **extended** format these positions are RESERVED (byte 18) and tz_offset (byte 19); only present in extended records (not in simple 20-byte format). |
| 0340 K3GUIDE | ✅ Resolved | Real-time zone guidance notification. 6 bytes: liftUp, liftDown, rightUp, rightDown, currentPosition (BrushAreaType 1–8 / 255=stop), workingState. Source: `C3367n0.java:737–745`, `ChangeType.K3GUIDE=8`. |
| Type-1 RECEIVE_BRUSH_UUID | ✅ Partial | Receives session data in the same 20-byte format as 0308. |

### 11.2 Remaining Unknowns

| Field / Command | Status | Description |
|----------------|--------|-------------|
| `0303` Byte 1 | ✅ Resolved | **Not parsed by app.** Present in BLE packet but not extracted. Purpose unknown, can be ignored. |
| `0303` Byte 2 | ✅ Resolved | **Not parsed by app.** Observed to vary continuously (0x0f–0x1d), likely an internal counter. Confirmed unused by APK source. |
| `0307` Bytes 11–17 | ❓ Unknown | Byte 11 highly variable (likely internal counter); byte 13 variable but NOT confirmed as duration (official APK does not parse it); byte 15 NOT always equal to byte 13; byte 17 may be a session counter. |
| `blunt_teeth` unit | ⚠️ Partial | Increases per session, reset via `020F`. Whether linear +1 or ADC wear value: unknown. |
| `020F` ACK content | ❓ Unknown | Response bytes after ACK not analyzed. Does the device include the new (=0) counter value in the ACK? |
| Pagination `0309` | ⚠️ Partial | Format identical to 0308. When does the device send additional pages? No page header in the record. |
| Extended 0308 on Oclean X | ❓ Unconfirmed | Extended 32-byte format implemented based on `AbstractC0002b.m37y`. Not yet observed on Oclean X (which uses 0307 Type-1). Needs hardware verification on Type-0 models. |
| `schemeType` values | ⚠️ Partial | Integer 0–8 in extended 0308 byte 29. Meaning of each value not documented in APK. |
| pNum → plan name | ❓ Cloud-managed | No hardcoded name mapping in APK. Names fetched from cloud API. |
| `0215` | ❓ Unknown | Only in C3350f, C3385w0. Possibly alternative battery read or device status. |
| `023A` | ❓ Unknown | Only in C3381u0. Setting of unknown type. |
| `0241`–`0245` | ❓ Unknown | Only in C3367n0 (display devices). Possibly display or animation settings. |
| `02A1`–`02A5` | ❓ Unknown | Only in C3367n0 / C3387x0. Possibly extended feature flags. |
| `030A`, `030B`, `030D` | ❓ Unknown | Only in C3335a. Possibly older/unused queries. |
| `0306` | ❓ Unknown | In C3389y0 with type check. Sent conditionally. |
| `0341` | ❓ Unknown | In C3367n0 with `z11=true` flag. Possibly animation or display special. |
| `07B3` | ❓ Unknown | Only in C3387x0. No context found. |
| `F725`, `F726` | ⚠️ Partial | WiFi-specific. `F726` = Download license/URL (method `mo5350M`). `F725` unknown. |
| `0303` Capacity vs. Battery | ⚠️ Likely battery | `capacity` in byte 3 of the 0303 response (observed = 29 ≈ battery %). Must be verified against Battery Characteristic – are both values identical? |

### 11.3 Verification Method for Open Questions

To resolve the remaining unknowns:

1. **0303 Bytes 1–3:** Enable `WARNING`-level logging in HA (coordinator already logs raw hex). Read app values simultaneously and compare.
2. **Pagination:** Connect device with many stored sessions, observe whether multiple 0308 notifications arrive.
3. **020F ACK:** After reset, capture the full ACK response in hex and check if payload is present.
4. **blunt_teeth linear:** Log the value before and after multiple sessions.

---

*Created: 2026-02-21 | Updated: 2026-02-21 | Basis: APK reverse-engineering `com.yunding.noopsychebrushforeign`*
*Total documented commands: 78+ (Standard 02xx/03xx: ~45, WiFi F2xx/F3xx/F7xx: ~20, Special: ~10)*
