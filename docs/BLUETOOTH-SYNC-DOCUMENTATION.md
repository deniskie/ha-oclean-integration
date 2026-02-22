# Oclean Smart Toothbrush – Bluetooth Synchronization: Technical Documentation

> **Basis:** Reverse-engineering of the decompiled Oclean Android APK (`com.yunding.noopsychebrushforeign`)
> **Protocol:** Bluetooth Low Energy (BLE) / GATT

---

## Table of Contents

1. [Architecture Overview](#1-architecture-overview)
2. [BLE GATT Profile: Services & Characteristics](#2-ble-gatt-profile-services--characteristics)
3. [Connection Setup](#3-connection-setup)
4. [Command Protocol](#4-command-protocol)
5. [Synchronization Flows](#5-synchronization-flows)
6. [Brush Data Transfer After Brushing](#6-brush-data-transfer-after-brushing)
7. [Data Models](#7-data-models)
8. [Event and Notification System](#8-event-and-notification-system)
9. [OTA Firmware Update via BLE](#9-ota-firmware-update-via-ble)
10. [WiFi Provisioning (BluFi Protocol)](#10-wifi-provisioning-blufi-protocol)
11. [Security & Authentication](#11-security--authentication)
12. [Key Files](#12-key-files)

---

## 1. Architecture Overview

The Bluetooth communication is structured in two layers:

```
┌─────────────────────────────────────────────────┐
│              Android App (UI Layer)              │
│   com.yunding.noopsychebrushforeign.page.*       │
└─────────────────┬───────────────────────────────┘
                  │ public API
┌─────────────────▼───────────────────────────────┐
│         OcleanBleManager  (Singleton)            │
│         com.ocleanble.lib.OcleanBleManager       │
│  • Scan, Connect, Disconnect                     │
│  • readBatteryLevel(), calibrationTime()         │
│  • synBrushScheme(), syncRunningGears()          │
│  • otaUpgrade()                                  │
└─────────────────┬───────────────────────────────┘
                  │
┌─────────────────▼───────────────────────────────┐
│         Connection Manager  (p154j.C3693g)       │
│  • ConcurrentHashMap: MAC → ConnectedDeviceInfo  │
│  • Listener lists for all event types            │
└─────────────────┬───────────────────────────────┘
                  │
┌─────────────────▼───────────────────────────────┐
│   Device Profiles / GATT Handler  (p105g.*)      │
│  AbstractC3347e (Base)                           │
│  ├── C3335a  (Device type 0: Standard models)   │
│  └── C3376s  (Device type 1: Premium models)    │
│  • Command queues (LinkedBlockingQueue)          │
│  • Byte encoding & MTU fragmentation             │
└─────────────────┬───────────────────────────────┘
                  │ Android BLE API
┌─────────────────▼───────────────────────────────┐
│            Toothbrush (BLE Peripheral)           │
└─────────────────────────────────────────────────┘
```

**Core Singleton:** `OcleanBleManager`
- Initialization with configurable timeouts
- SDK authentication via cloud endpoint

**Configurable Timeouts (default values):**

| Parameter | Default |
|-----------|---------|
| `connectTimeout` | 15 seconds |
| `sendTimeout` | 3 seconds |
| `receiveTimeout` | 5 seconds |
| `scanPeriod` | 20 seconds |

---

## 2. BLE GATT Profile: Services & Characteristics

### 2.1 Oclean Proprietary Service

```
Service UUID: 8082caa8-41a6-4021-91c6-56f9b954cc18
```

#### Device Type 1 – Premium Models (C3376s)

| Characteristic | UUID | Properties |
|----------------|------|------------|
| Read/Info (Notify) | `5f78df94-798c-46f5-990a-855b673fbb86` | Read, Notify |
| Write/Info (Command) | `9d84b9a3-000c-49d8-9183-855b673fbb85` | Write |
| Send Brush Command | `5f78df94-798c-46f5-990a-855b673fbb89` | Write |
| Receive Brush Data | `5f78df94-798c-46f5-990a-855b673fbb90` | Notify |

#### Device Type 0 – Standard Models (C3335a)

| Characteristic | UUID | Properties |
|----------------|------|------------|
| Read/Info (Notify) | `5F78DF94-798C-46F5-990A-855B673FBB86` | Read, Notify |
| Write/Info (Command) | `9D84B9A3-000C-49D8-9183-855B673FBB85` | Write |
| Change Info (Notify) | `6C290D2E-1C03-ACA1-AB48-A9B908BAE79E` | Notify |

### 2.2 Battery Service (BLE Standard)

```
Service UUID:        0000180f-0000-1000-8000-00805f9b34fb
Characteristic UUID: 00002a19-0000-1000-8000-00805f9b34fb
CCCD Descriptor:     00002902-0000-1000-8000-00805f9b34fb
```
- Properties: Read, Notify
- Returns battery level as a single byte value (0–100 %)

### 2.3 GATT Characteristic Configuration

After connection is established, notifications are enabled via the **Client Characteristic Configuration Descriptor (CCCD, 0x2902)**. This allows asynchronous push messages from the brush to the app.

---

## 3. Connection Setup

### 3.1 Scan Phase

```
App                          Toothbrush (BLE Advertising)
 │                                       │
 │◄──── BLE Advertising (Name/MAC) ──────│
 │                                       │
 │  onFoundDevice(OcleanBluetoothDevice) │
 │  ← Name/MAC filtering                │
```

The `OcleanBluetoothDevice` class contains:
- `deviceName`: BLE device name
- `mac`: MAC address
- `rssi`: Signal strength
- `AdvertisingInfo`: Payload of advertising packets

### 3.2 Connection Setup (Sequence Diagram)

```
App (OcleanBleManager)        Android BLE Stack         Toothbrush
         │                          │                       │
         │ connectionDevice(mac)    │                       │
         │─────────────────────────►│                       │
         │                          │ connectGatt()         │
         │                          │──────────────────────►│
         │                          │                       │
         │                          │◄── onConnectionStateChange(CONNECTED)
         │                          │                       │
         │                          │ discoverServices()    │
         │                          │──────────────────────►│
         │                          │◄── onServicesDiscovered()
         │                          │                       │
         │ requestMtu(size)         │                       │
         │─────────────────────────►│──────────────────────►│
         │                          │◄── onMtuChanged(mtu)  │
         │                          │                       │
         │ setCharacteristicNotification(READ_CHAR, true)   │
         │─────────────────────────►│──────────────────────►│
         │                          │                       │
         │ onCompleteConnection()   │                       │
         │◄─────────────────────────│                       │
```

**MTU Negotiation:**
- Default: 20 bytes (BLE default)
- Optimized: Device-specific, larger for faster transfer
- MtuSize enum: `DEFAULT`, further device-specific values

### 3.3 Connection State

Managed by `ConnectedDeviceInfo`:

```java
// Key fields
boolean isConnected
boolean isOTAUpgrade
boolean safeMode
MtuSize mtuSize      // currently negotiated MTU
OtaType otaType      // OTA upgrade type (None / Standard / ...)
```

---

## 4. Command Protocol

### 4.1 Frame Format

Commands are sent as **byte arrays** to the Write Characteristic. The format:

```
┌────────────┬────────────────────────────────────────┐
│  Command   │              Payload                   │
│  (2 bytes) │         (variable length)              │
└────────────┴────────────────────────────────────────┘
```

- Command bytes are defined as hex strings in the code (e.g. `"020E"`)
- Conversion: hex string → byte array via `ConverterUtils`
- Byte order: predominantly **Big-Endian** (exceptions documented)

### 4.2 Command Table

#### Synchronization Commands

| Command Bytes | Function | Payload |
|---------------|----------|---------|
| `020E` | Time calibration (Calibrate Time) | 4 bytes, Unix timestamp (seconds, Big-Endian) |
| `0201` | Set time | Time string |
| `0202` | Query device information | – |
| `020B` | Query protocol version | – |
| `0303` | Query device status | – |

#### Brush Scheme Commands

| Command Bytes | Function | Payload |
|---------------|----------|---------|
| `0206` | Transfer brush scheme (packet 1) | pnum, step count, step data |
| `020B` | Transfer brush scheme (packet 2) | Continuation with marker bytes |

#### Device Settings

| Command Bytes | Function | Payload |
|---------------|----------|---------|
| `0203` | Pause/Resume | 3 bytes: pause flag + gear mode |
| `020C` | Set brightness | Brightness value |
| `0207` | Set motor speed/level | 4 bytes, Big-Endian integer |
| `0209` | Enable/disable wake gesture | `0x01` = active, `0xEC` = inactive |
| `0211` | Set birthday | Date format |
| `020D` | Setting (Boolean) | Boolean byte |
| `020F` | Query unknown setting | – |
| `0212` | Setting (Boolean) | Boolean byte |
| `0213` | Setting (Boolean) | Boolean byte |
| `0216` | Setting (single byte) | 1 byte |
| `0217` | Setting (2 bytes, Big-Endian) | 2 bytes |
| `0223` | Setting (Boolean) | Boolean byte |
| `0225` | Setting (Boolean) | Boolean byte |
| `0228` | Setting (Boolean) | Boolean byte |
| `0230` | Setting (Boolean) | Boolean byte |
| `0231` | Setting (3 Booleans) | 3 bytes |
| `0233` | Set URL | URL string (variable) |

#### Device Information

| Command Bytes | Function | Response Payload |
|---------------|----------|-----------------|
| `0723` | Hardware & serial number | 36 bytes ASCII: `{hw},{serial}.` |
| `0307` | Firmware version (INFO type) | Version string |
| `09ED` | Device state (STATE type) | State bytes |
| `0234` | Query info | – |
| `0235` | Query setting | – |
| `0313` | Setting (4 bytes) | 4 bytes |
| `0314` | Query info | – |
| `0315` | Set brush area type | BrushAreaType enum |
| `03A0` | Query info | – |
| `02A0` | Setting (Boolean) | Boolean byte |

### 4.3 Response Recognition

Responses are classified by the first 2 bytes:

```
09 ED  →  STATE type  (device state response)
03 07  →  INFO type   (device information response)
```

### 4.4 Hardware/Serial Number Parsing

```
Input:  36 bytes (split into two 18-byte packets)
Format: ASCII string  "{hardware},{serial}."
Delimiters:
  0x2C  =  Comma ','   → separates hardware and serial
  0x2E  =  Period '.'  → marks end
Output: JSON  {"hardware": "...", "serial": "..."}
```

### 4.5 MTU Fragmentation

For commands larger than `(MTU - 3)` bytes, automatic packetization occurs:

```
Packet 1: Command header + first (MTU-3) bytes of payload
          ├── pnum (scheme number)
          ├── step count
          └── step data (duration, gears, totalTime)

Packet 2: Continuation marker + remaining payload
          ├── Marker 0x2A (42)  ← continuation identifier
          ├── Marker 0x2B (43)  ← alternative marker byte
          └── remaining data
```

---

## 5. Synchronization Flows

### 5.1 Full Sync Flow (after connection setup)

```
1. CONNECTION PHASE
   ├── BLE scan → Oclean device found
   ├── GATT Connect
   ├── Services discovery
   ├── MTU negotiation
   └── Enable notifications

2. INITIAL SYNC
   ├── Time calibration    →  Command 020E (Unix timestamp, 4 bytes)
   ├── Read battery        →  Battery Characteristic (standard BLE)
   ├── Device status       →  Command 0303
   ├── Device information  →  Command 0202
   ├── Firmware version    →  Command 0307
   └── Hardware/serial     →  Command 0723

3. CONFIGURATION SYNC
   ├── Transfer brush scheme  →  Commands 0206 / 020B
   ├── Motor settings         →  Command 0207
   ├── User settings          →  Commands 020D, 020F, 0212, 0213, ...
   └── Wake gesture           →  Command 0209

4. OPERATING MODE (push notifications)
   ├── Real-time brush data   ←  onRunningData(JSON)
   ├── Battery level change   ←  onBatteryChange(%)
   ├── State change           ←  onStatusChange(status)
   ├── Charging state         ←  onChargingStateChange(state)
   └── Error codes            ←  onErrorInfo(code)

5. DISCONNECTION PHASE
   └── onConnectionStateChange(DISCONNECTED) → Reconnect logic
```

### 5.2 Time Calibration (Calibrate Time)

```java
// Command: 020E
// Payload: 4 bytes, Unix timestamp in seconds, Big-Endian

ByteBuffer buffer = ByteBuffer.allocate(4);
buffer.order(ByteOrder.BIG_ENDIAN);
buffer.putInt((int)(System.currentTimeMillis() / 1000));
byte[] timestampBytes = buffer.array();

// Full frame:
// [0x02, 0x0E, ts_byte3, ts_byte2, ts_byte1, ts_byte0]
```

### 5.3 Brush Scheme Transfer (Brush Scheme Sync)

**BrushScheme data structure:**
```
BrushScheme
├── pnum:          int   (scheme number / identifier)
├── totalTime:     int   (total brushing time in seconds)
├── schemeType:    int   (scheme type)
├── cleanPower:    int   (cleaning power)
├── inverterGear:  int   (gear setting)
├── inverterCycle: int   (cycle configuration)
├── hintType:      int   (hint type / notification)
└── steps: List<BrushSchemeStep>
         └── BrushSchemeStep
             ├── duration:  int    (step duration)
             ├── gears:     int    (toothbrush level)
             └── voiceNum:  String (voice prompt ID, default: "0")
```

**Transfer flow:**
```
App                                    Toothbrush
 │                                         │
 │── Write(0206 + Packet1 header+steps) ──►│
 │                                         │  (if > MTU-3 bytes:)
 │── Write(020B + marker + remaining data)►│
 │                                         │
 │◄── Notify: onSchemeChange(schemeId) ────│
```

### 5.4 Real-Time Brush Data (Running Data)

During brushing, the brush sends push notifications:

```
Callback: onRunningData(String address, String jsonValue)
Format:   JSON string

Data content (from notification routing in AbstractC3347e):
- Real-time brush data
- Oral area guidance (K3 mode: onK3AreasGuide)
- Running state (onRunningState)
- K3 mode change (onK3ModeChange)
```

### 5.5 Notification Routing (AbstractC3347e.m5391t)

```
Incoming Notification
        │
        ▼
  Type detection (first 2 bytes)
        │
   ┌────┴────┐
   │         │
09ED        0307
(STATE)    (INFO)
   │         │
   ▼         ▼
State-      Info-
Routing     Routing
   │
   ├── Battery        → onBatteryChange(address, %)
   ├── Status         → onStatusChange(address, status)
   ├── Scheme         → onSchemeChange(address, schemeId)
   ├── Running Data   → onRunningData(address, json)
   ├── Charging       → onChargingStateChange(address, state)
   ├── Error          → onErrorInfo(address, errorCode)
   ├── K3 Mode        → onK3ModeChange(address, mode)
   └── Area Guide     → onK3AreasGuide(address, json)
```

---

## 6. Brush Data Transfer After Brushing

This section describes the complete path of brush data: from the toothbrush via BLE into the local database through to cloud upload.

### 6.1 Overview

```
Toothbrush           App (OcleanDataService)              Cloud Server
    │                           │                                  │
    │─── BLE Notify ───────────►│                                  │
    │    onRunningData()        │                                  │
    │                           │ readRunningData()                │
    │◄── BLE read request ──────│                                  │
    │                           │                                  │
    │─── BrushRecordResult ────►│                                  │
    │    (session data)         │                                  │
    │                           │ SQLite save                      │
    │                           │ (BrushRecordEntity)              │
    │                           │                                  │
    │                           │ Every 5 s / 300 s:              │
    │                           │ Encode records                   │
    │                           │──── HTTP POST reportList ───────►│
    │                           │                                  │
    │                           │◄─── SubmitBrushResult ──────────│
    │                           │     (successList, abandList)     │
    │                           │                                  │
    │                           │ Update SQLite with               │
    │                           │ server IDs                       │
```

### 6.2 Step 1: BLE Notification from Device

After completing a brush session, the toothbrush triggers a GATT notification:

```
Callback: OnOcleanDataChangeListener.onRunningData(String address, String value)
File:     p154j/C3693g.java
```

The connection manager dispatches the event to all registered listeners:

```java
// C3693g.java – Event dispatch
public final void onRunningData(String address, String value) {
    for (OnOcleanDataChangeListener listener : f13744h) {
        handler.post(new RunnableC0490o1(listener, address, value, 2));
    }
}
```

### 6.3 Step 2: Fetch Full Session Data (`readRunningData`)

After the notification, the app actively requests the full brush data from the device:

```
Method:  OcleanBleManager.readRunningData(String mac, OnOcleanCommandListener listener)
File:    com/ocleanble/lib/OcleanBleManager.java
```

Internally calls `AbstractC3347e.mo5299S0(listener)`, which sends a BLE read/write command to the responsible characteristic.

**Error handling:**

| Error code | Meaning |
|------------|---------|
| `69` | Device not connected / not found |
| `70` | BLE command could not be sent |

### 6.4 Step 3: Brush Session Data Structure (`BrushRecordResult`)

The device delivers a `BrushRecordResult` object with the following fields:

```
BrushRecordResult
├── id                   String   Server ID (empty for new records)
├── appLocalId           String   App-side local ID
├── date                 String   Date (YYYY-MM-DD)
├── amorpm               int      1 = AM, 2 = PM
├── timeLong             int      Brushing duration in seconds
├── schemeId             String   ID of the brush scheme used
├── schemeType           int      Scheme type (0–8)
├── schemeTimeLong       int      Target brushing time per scheme (seconds)
├── score                int      Quality score (0–100)
├── clean                int      Cleaning effectiveness (0–100)
├── pressure             int      Average pressure (0–255)
├── gesture              int      Brushing technique score (0–255)
├── speckle              int      Plaque coverage
├── time12               String   12-segment timeline (time distribution)
├── pressure12           String   12-segment pressure history
├── pressureRatio        String   Pressure distribution ratio
├── pressureDistribution String   Pressure map (full)
├── overPullNum          int      Number of over-pull events
├── nurseSchemeNum       int      Care plan scheme number
├── deviceMac            String   Brush MAC address
├── deviceName           String   Device name
├── deviceKind           int      Device category
├── typeName             String   Type (e.g. "brushing")
├── at                   String   @ field (metadata)
└── ota                  String   OTA version info
```

**Timeline arrays (`time12`, `pressure12`):**
The brushing time is divided into **12 segments**. Each segment represents a time section of the brush session with the respective pressure value. This enables time-resolved analysis of brushing behavior.

### 6.5 Step 4: Local Storage (`BrushRecordEntity`)

The `BrushRecordResult` is written to the SQLite database as a `BrushRecordEntity`.

**Full fields:**

```
BrushRecordEntity (SQLite)
├── _id                  int      Local database ID (auto-increment)
├── localId              String   App-generated unique ID
├── id                   String   Server ID (populated after upload)
├── date                 String   YYYY-MM-DD
├── timer                String   HH:MM:SS
├── amorpm               int      1 = AM, 2 = PM
├── timeLong             int      Brushing duration (seconds)
├── second               int      Additional seconds value
├── schemeId             String   Scheme ID
├── schemeTitle          String   Scheme name (display)
├── schemeType           int      Scheme type
├── score                int      Quality score (0–100)
├── clean                int      Cleaning effectiveness (0–100)
├── pressure             int      Average pressure
├── gesture              int      Technique score
├── speckle              int      Plaque coverage
├── gestureArray         String   Technique data time series (CSV)
├── powerArray           String   Pressure data time series (CSV)
├── pressureRatio        String   Pressure ratio
├── pressureDistribution String   Pressure map
├── overPullNum          int      Over-pull events
├── planType             int      Care plan type
├── planTimeLong         int      Care plan duration
├── point                int      Points (populated after server response)
├── y3_Point             int      Y3-device points
├── isValid              int      Validity flag
├── deviceMac            String   MAC address
├── deviceName           String   Device name
├── deviceKind           int      Device category
├── childId              String   User/child profile ID
├── userTagId            String   User tag
├── type                 String   Type ("brushing", "flossing", …)
├── c_TimeZone           String   Client timezone
├── b_TimeZone           String   Backend timezone
├── b_DateTime           String   Backend timestamp
└── ota                  String   OTA version info
```

### 6.6 Step 5: Upload Timer

`OcleanDataService` runs a timer (`RunnableC2579g`) that loads all untransferred records from the local database and uploads them:

| Phase | Interval |
|-------|---------|
| Immediately after connection setup | 5 seconds |
| Continuous operation | 300 seconds (5 minutes) |

The upload can additionally be triggered manually via the event `LiveEventBus.get("SubmitBrushData")`.

### 6.7 Step 6: Binary Compression Before HTTP Upload

Before uploading records, they are packed into two compressed 64-bit integers (`OcleanDataService.RunnableC2579g.run()`).

#### Pre-processing of Date/Time Fields

```
date  "2024-03-15"  →  remove delimiters  →  "20240315"
timer "14:30:45"    →  remove delimiters  →  "143045"
```

#### `jIntValue` – Date, Time & Speckle (packed)

```
Bit layout (LSB to MSB):

 Bits  0       : amorpm     (1 bit)   – 0 = AM, 1 = PM
 Bits  1 –  8  : minutes    (8 bits)
 Bits  9 – 16  : hours      (8 bits)
 Bits 17 – 21  : day        (5 bits)
 Bits 22 – 25  : month      (4 bits)
 Bits 26 – 31  : timeLong   (6 bits)  – brushing duration
 Bits 32 – 63  : speckle    (32 bits) – plaque coverage
```

Source code (simplified):
```java
long jIntValue =
  ((long) speckle
    | (((((((((((((long) timeLong) << 6)
      | (long)(year % 100)) << 4)
      | (long) month)      << 5)
      | (long) day)        << 8)
      | (long) hour)       << 8)
      | (long) minute)     << 8)) << 1)
  | (long) amorpm;
```

#### `j10` – Rating Metrics (packed)

```
Bit layout (LSB to MSB):

 Bits  0 –  7  : recordOffset  (8 bits)  – index within upload batch
 Bits  8 – 15  : gesture       (8 bits)  – technique score
 Bits 16 – 23  : pressure      (8 bits)  – average pressure
 Bits 24 – 31  : score         (8 bits)  – quality score
 Bits 32 – 39  : clean         (8 bits)  – cleaning effectiveness
 Bits 40 – 47  : schemeType    (8 bits)  – brush scheme type
```

Source code (simplified):
```java
long j10 =
  (((((((((((long) schemeType) << 8)
    | (long) clean)   << 8)
    | (long) score)   << 8)
    | (long) pressure) << 8)
    | (long) gesture)  << 8)
  | (long) recordOffset;
```

### 6.8 Step 7: HTTP POST Format

All records in an upload batch are separated by `|` (pipe) and transmitted as a single string.

#### Structure of a single record (comma-separated)

| Position | Field | Description |
|----------|-------|-------------|
| 1 | `deviceMac` | Toothbrush MAC address |
| 2 | `jIntValue` | Packed date/time/speckle (Long) |
| 3 | `j10` | Packed metrics (Long) |
| 4 | `schemeId` | Brush scheme ID |
| 5 | `type` | Session type (e.g. `"brushing"`) |
| 6 | `gestureArray` | Technique time series (CSV) |
| 7 | `powerArray` | Pressure time series (CSV) |
| 8 | `second` | Additional seconds value |
| 9 | `pressureRatio` | Pressure ratio |
| 10 | `childId` | User/child profile ID |
| 11 | `c_TimeZone` | Client timezone |
| 12 | `b_TimeZone` | Backend timezone |
| 13 | `b_DateTime` | Backend timestamp |
| 14 | `y3_Point` | Y3-device points |
| 15 | `localId` | Local record ID |
| 16 | `isValid` | Validity flag |
| 17 | `planType` | Care plan type |
| 18 | `planTimeLong` | Care plan duration |
| 19 | `overPullNum` | Over-pull events |

#### HTTP POST Parameters

```
Content-Type: application/x-www-form-urlencoded

reportList  =  "<record1>|<record2>|..."
appTime     =  "2024-03-15 14:30:45"            (client timestamp, yyyy-MM-dd HH:mm:ss)
timeZone    =  "GMT+08:00"                       (local timezone)
agent       =  "OcleanApp/3.x.x ..."            (user agent string)
```

#### Example `reportList` (one record, schematic)

```
AA:BB:CC:DD:EE:FF,<jIntValue_long>,<j10_long>,scheme_001,brushing,
0,0,1,2,1,2,...,0,1,0,2,...,45,0.3,user_123,
GMT+08:00,GMT+08:00,2024-03-15 14:30:45,0,local_789,1,0,120,0
```

### 6.9 Step 8: Server Response and Database Update

The server responds with `SubmitBrushResult` (processed in `C2587c0`):

```
SubmitBrushResult
├── successList: List<SuccessInfo>
│   └── SuccessInfo
│       ├── localId    int     Local ID of the record
│       ├── serverId   String  Server-assigned ID
│       ├── point      int     Calculated points
│       └── nowTime    String  Server timestamp (optional, "YYYY-MM-DDTHH:MM:SS")
│
└── abandList: List<AbandInfo>
    └── AbandInfo
        └── localId    int     Local ID of the rejected record
```

**Processing logic:**

```
successList entry
├── nowTime present?
│   ├── YES → mo8297h(localId, serverId, point, date, time)
│   │          full update including server timestamp
│   └── NO  → mo8298i(localId, point, serverId)
│              simple update without time correction
│
abandList entry
└── mo8292c(localId) → mark record as "discarded"
```

After a successful upload, `LiveEventBus.get("refresh_ai_dialog")` is triggered to update the UI.

### 6.10 Error Handling: `ErrorBrushRecordEntity`

If the upload fails, records are stored in a separate table `ErrorBrushRecordEntity` and retried at the next timer cycle:

```
ErrorBrushRecordEntity
├── records    String  Serialized records (raw data)
├── date       String  Date of error
├── timer      String  Time of error
└── deviceMac  String  MAC address
```

---

## 7. Data Models

### 7.1 ConnectedDeviceInfo

Central state class for a connected device:

```java
class ConnectedDeviceInfo {
    BluetoothGatt bluetoothGatt;     // GATT connection object
    int           butteryValue;      // Battery level 0–100 %
    AbstractC3347e command;          // Command handler (device-specific)
    int           deviceId;
    String        deviceIdFirmwareRevision;
    String        deviceIdSoftwareRevision;
    String        deviceIdModelNumber;  // default: ""
    String        deviceBleName;        // default: ""
    String        deviceBleMac;         // default: ""
    String        protocol;             // default: ""
    boolean       isConnected;
    boolean       isOTAUpgrade;
    boolean       safeMode;
    boolean       supportUpgradeUrl;
    OtaType       otaType;         // default: OtaType.None
    MtuSize       mtuSize;         // default: MtuSize.DEFAULT
    AbstractC4580c otaHook;        // OTA upgrade handler
}
```

### 7.2 OcleanBluetoothDevice (Scan Result)

```java
class OcleanBluetoothDevice {
    String        deviceName;      // BLE device name
    String        mac;             // MAC address
    int           rssi;            // Signal strength (dBm)
    AdvertisingInfo advertisingInfo; // Payload of advertising packets
}
```

### 7.3 NotifyResult (Notification Wrapper)

Used in the `LinkedBlockingQueue` for asynchronous processing:
- Wraps incoming GATT notifications
- Contains characteristic UUID + received bytes

---

## 8. Event and Notification System

### 8.1 Callback Interfaces

| Interface | Methods | Purpose |
|-----------|---------|---------|
| `OnOcleanScanListener` | `onFoundDevice()` | Found BLE device |
| `OnOcleanConnectionListener` | `onCompleteConnection()`, `onDisconnected()` | Connection state |
| `OnOcleanDataChangeListener` | 7 methods (see below) | Data synchronization |
| `OnOcleanBatteryChangeListener` | `onBatteryChange()` | Battery level |
| `OnOcleanRunningChangeListener` | `onRunningData()` | Real-time brush data |
| `OnOcleanModeChangeListener` | `onModeChange()` | Mode change |
| `OnOcleanCommandListener` | various | Command acknowledgements |
| `OnOcleanDeviceVersionListener` | `onVersionReceived()` | Firmware version |
| `OnOcleanOtaUpgradeListener` | `onProgress()`, `onComplete()`, `onError()` | OTA progress |

### 8.2 OnOcleanDataChangeListener – Full Methods

```java
interface OnOcleanDataChangeListener {
    void onBatteryChange(String address, int value);         // Battery %
    void onChargingStateChange(String address, int state);   // Charging state
    void onErrorInfo(String address, int error);             // Error code
    void onGestureChanged(String address, String json);      // Gesture detected
    void onRunningData(String address, String value);        // Real-time brush data (JSON)
    void onSchemeChange(String address, int schemeId);       // Scheme changed
    void onStatusChange(String address, int status);         // Device status
}
```

### 8.3 Queue-Based Message System

The GATT handler (`AbstractC3347e`) uses three parallel `LinkedBlockingQueue` instances:

```
┌─────────────────────────────────────────────────┐
│         AbstractC3347e (GATT Handler)            │
│                                                  │
│  f12504n: LinkedBlockingQueue  ← Notify queue   │
│  f12505o: LinkedBlockingQueue  ← Write queue    │
│  f12506p: LinkedBlockingQueue  ← Read queue     │
│                                                  │
│  f12499i: MTU size                              │
│  f12501k: Write characteristic                  │
└─────────────────────────────────────────────────┘
```

- **Notify queue:** received GATT notifications
- **Write queue:** pending write operations
- **Read queue:** pending read requests
- Atomic counters track open operations

---

## 9. OTA Firmware Update via BLE

### 9.1 Flow

```
App                              Toothbrush
 │                                   │
 │ (1) Download firmware binary (via cloud)
 │                                   │
 │── OTA start command ─────────────►│
 │                                   │
 │── Firmware chunk 1 ──────────────►│  (WRITE_TYPE_NO_RESPONSE)
 │── Firmware chunk 2 ──────────────►│
 │── Firmware chunk N ──────────────►│
 │                                   │
 │◄── Notify: Progress (0–100%) ─────│
 │◄── Notify: onUpgradeComplete() ───│
```

### 9.2 AbstractC4580c – Key Fields

```java
abstract class AbstractC4580c {
    Context            f15796a;  // Android Context
    ConnectedDeviceInfo f15797b;  // connected device
    String             f15798c;  // BLE MAC address
    MtuSize            f15799d;  // MTU size
    float              f15800e;  // progress 0.0–100.0
    boolean            f15801f;  // upgrade active?
    OnOcleanOtaUpgradeListener f15802g; // progress listener
    long               f15803h;  // elapsed time (ms)
}
```

### 9.3 Write Modes

| Mode | Constant | Usage |
|------|----------|-------|
| `WRITE_TYPE_WITH_RESPONSE` | Type 2 | Control commands (ACK expected) |
| `WRITE_TYPE_NO_RESPONSE` | Type 1 | Firmware chunks (maximum throughput) |

---

## 10. WiFi Provisioning (BluFi Protocol)

For WiFi-capable models, the **Espressif BluFi Protocol** is used over BLE.

### 10.1 BluFi Service

- Dedicated GATT service for WiFi configuration
- Implemented in `com.ocleanble.lib.blufi`

### 10.2 Flow

```
App                                      Toothbrush
 │                                          │
 │── Scan WiFi command (BLE Write) ────────►│
 │◄── Notify: BlufiScanResult (networks) ───│
 │                                          │
 │── WiFi credentials (SSID+PW) via BLE ──►│
 │   (encrypted via BluFi protocol)        │
 │                                          │
 │◄── Notify: BlufiStatusResponse ──────────│
 │   (connection state: connected/error)   │
```

### 10.3 BluFi Response Classes

| Class | Function |
|-------|---------|
| `BlufiStatusResponse` | Connection state (SSID, IP, RSSI) |
| `BlufiVersionResponse` | BluFi protocol version |
| `BlufiScanResult` | Found WiFi networks |
| `BlufiErrorResult` | Error codes for WiFi connection |

### 10.4 Security Types (Constants from `p051d.C3019b`)

| Constant | Type |
|----------|------|
| `SECURITY_OPEN` | Open network |
| `SECURITY_WEP` | WEP |
| `SECURITY_WPA` | WPA |
| `SECURITY_WPA2` | WPA2 |
| `SECURITY_WPA_WPA2` | WPA/WPA2 |

---

## 11. Security & Authentication

### 11.1 SDK Authentication

```
Endpoint: https://sdkauth.oclean.com/api/auth.ashx
Method:   HTTPS POST
Purpose:  Validation of the SDK client before BLE communication

Control: Metadata flag VALIDATE
- true  → authentication is enforced
- false → authentication is skipped
```

Callback: `AuthenticationCallback` (in `com.ocleanble.lib.callback`)

### 11.2 Transport Security

- REST API communication: HTTPS via OkHttp3
- BLE communication: Proprietary binary protocol (no BLE layer encryption confirmed)
- WiFi credentials: Via BluFi protocol (Espressif-specific encryption)

---

## 12. Key Files

| File | Package | Function |
|------|---------|---------|
| `OcleanBleManager.java` | `com.ocleanble.lib` | Main SDK singleton, public API |
| `AbstractC3347e.java` | `p105g` | Abstract GATT callback base class |
| `C3335a.java` | `p105g` | Device type 0 implementation (Standard) |
| `C3376s.java` | `p105g` | Device type 1 implementation (Premium) |
| `C3693g.java` | `p154j` | Connection manager & event dispatcher |
| `C3687a.java` | `p154j` | Scan listener implementation |
| `AbstractC4580c.java` | `p229o` | OTA upgrade base class |
| `ConnectedDeviceInfo.java` | `com.ocleanble.lib.entity` | Device state |
| `BrushScheme.java` | `com.ocleanble.lib.entity` | Brush scheme data model |
| `BrushSchemeStep.java` | `com.ocleanble.lib.entity` | Individual step in brush scheme |
| `OcleanBluetoothDevice.java` | `com.ocleanble.lib.entity` | Scan result |
| `OnOcleanDataChangeListener.java` | `com.ocleanble.lib.callback` | Data sync callback |
| `C3019b.java` | `p051d` | Protocol constants (WiFi/BluFi) |

---

*Created by analysis of decompiled APK sources. All UUIDs, command bytes, and protocol details were extracted directly from the source code.*
