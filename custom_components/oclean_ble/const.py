"""Constants for the Oclean Toothbrush integration."""

DOMAIN = "oclean_ble"
MANUFACTURER = "Oclean"

# Default polling interval in seconds
DEFAULT_POLL_INTERVAL = 300  # 5 minutes
MIN_POLL_INTERVAL = 60       # 1 minute

# BLE UUIDs
OCLEAN_SERVICE_UUID = "8082caa8-41a6-4021-91c6-56f9b954cc18"

# BLE Device Information Service (0x180A) – standard GATT service
DIS_MODEL_UUID   = "00002a24-0000-1000-8000-00805f9b34fb"   # Model Number String
DIS_HW_REV_UUID  = "00002a27-0000-1000-8000-00805f9b34fb"   # Hardware Revision String
DIS_SW_REV_UUID  = "00002a28-0000-1000-8000-00805f9b34fb"   # Software Revision String
BATTERY_SERVICE_UUID = "0000180f-0000-1000-8000-00805f9b34fb"
BATTERY_CHAR_UUID = "00002a19-0000-1000-8000-00805f9b34fb"       # Read, Notify
READ_NOTIFY_CHAR_UUID = "5f78df94-798c-46f5-990a-855b673fbb86"   # Notify (all types)
WRITE_CHAR_UUID = "9d84b9a3-000c-49d8-9183-855b673fbb85"         # Write (all types)
SEND_BRUSH_CMD_UUID = "5f78df94-798c-46f5-990a-855b673fbb89"     # Write (Type 1 running-data cmd)
RECEIVE_BRUSH_UUID = "5f78df94-798c-46f5-990a-855b673fbb90"      # Notify (Type 1 brush records)
CHANGE_INFO_UUID = "6c290d2e-1c03-aca1-ab48-a9b908bae79e"        # Notify (Type 0 only)

# BLE Commands (hex bytes)
# Source: C3335a.java / C3340b1.java
CMD_QUERY_STATUS = bytes.fromhex("0303")       # mo5295Q0 – all types
CMD_DEVICE_INFO = bytes.fromhex("0202")        # mo5310r0 – all types
CMD_CALIBRATE_TIME_PREFIX = bytes.fromhex("020E")  # mo5289B Type 0: + 4-byte BE unix timestamp
CMD_QUERY_RUNNING_DATA = bytes.fromhex("0308")    # mo5299S0 Type 0 / C3340b1 – fetch brush records
CMD_QUERY_RUNNING_DATA_T1 = bytes.fromhex("0307") # Type 1 (Oclean X): send to SEND_BRUSH_CMD_UUID
CMD_QUERY_RUNNING_DATA_NEXT = bytes.fromhex("0309")  # mo5301W0 – follow-up page

# Response type markers (first 2 bytes)
# Observed on Oclean X: the device echoes the command prefix as the response type.
# CMD_QUERY_STATUS (0303) → response starts with 0303
# CMD_QUERY_RUNNING_DATA (0308) → response starts with 0308
# CMD_DEVICE_INFO (0202) → response is "0202 4F 4B" (= "OK", just an ACK)
RESP_STATE = bytes.fromhex("0303")       # Response to CMD_QUERY_STATUS – device status
RESP_INFO = bytes.fromhex("0308")        # Response to CMD_QUERY_RUNNING_DATA – brush records (Type 0)
RESP_INFO_T1 = bytes.fromhex("0307")    # Response to CMD_QUERY_RUNNING_DATA_T1 – brush records (Type 1, Oclean X)
RESP_DEVICE_INFO = bytes.fromhex("0202") # Response to CMD_DEVICE_INFO – "OK" acknowledge
RESP_K3GUIDE = bytes.fromhex("0340")    # Real-time zone guidance during brushing (K3 devices)

# Config entry keys
CONF_MAC_ADDRESS = "mac_address"
CONF_POLL_INTERVAL = "poll_interval"
CONF_DEVICE_NAME = "device_name"
CONF_POLL_WINDOWS = "poll_windows"           # str: "HH:MM-HH:MM[, HH:MM-HH:MM, ...]", "" = disabled
CONF_POST_BRUSH_COOLDOWN = "post_brush_cooldown"  # int hours, 0 = disabled

# Options-flow fields for the multi-step window setup (not persisted; combined into CONF_POLL_WINDOWS).
CONF_WINDOW_COUNT = "window_count"   # int 0-3: how many poll windows the user wants
CONF_WINDOW_START = "window_start"   # str "HH:MM:SS": start time in a per-window step
CONF_WINDOW_END   = "window_end"     # str "HH:MM:SS": end time in a per-window step
DEFAULT_POST_BRUSH_COOLDOWN = 0

# Coordinator data keys
DATA_BATTERY = "battery"
DATA_IS_BRUSHING = "is_brushing"
DATA_LAST_BRUSH_SCORE = "last_brush_score"
DATA_LAST_BRUSH_DURATION = "last_brush_duration"
DATA_LAST_BRUSH_CLEAN = "last_brush_clean"
DATA_LAST_BRUSH_PRESSURE = "last_brush_pressure"
DATA_LAST_BRUSH_TIME = "last_brush_time"

# Sensor names (entity IDs suffix)
SENSOR_BATTERY = "battery"
SENSOR_LAST_BRUSH_SCORE = "last_brush_score"
SENSOR_LAST_BRUSH_DURATION = "last_brush_duration"
SENSOR_LAST_BRUSH_CLEAN = "last_brush_clean"
SENSOR_LAST_BRUSH_PRESSURE = "last_brush_pressure"
SENSOR_LAST_BRUSH_TIME = "last_brush_time"
BINARY_SENSOR_IS_BRUSHING = "is_brushing"

# BLE connection timeout in seconds
BLE_CONNECT_TIMEOUT = 10
# Time to wait for notifications after sending a command
BLE_NOTIFICATION_WAIT = 3

# Brush head reset command
CMD_CLEAR_BRUSH_HEAD = bytes.fromhex("020F")

# Coordinator data keys (additional)
DATA_BRUSH_HEAD_USAGE = "brush_head_usage"
DATA_MODEL_ID    = "model_id"       # Model Number from BLE DIS (e.g. "OCLEANY3M")
DATA_HW_REVISION = "hw_revision"   # Hardware Revision from BLE DIS (e.g. "Rev.D")
DATA_SW_VERSION  = "sw_version"    # Software Revision from BLE DIS (e.g. "1.0.0.20")
DATA_LAST_BRUSH_AREAS = "last_brush_areas"         # dict: zone_name → pressure (0-255)
DATA_LAST_BRUSH_SCHEME_TYPE = "last_brush_scheme_type"  # int 0-8 (scheme category)
DATA_LAST_BRUSH_PNUM = "last_brush_pnum"            # int (brush-scheme ID; see SCHEME_NAMES below)

# Sensor / button entity key suffixes
SENSOR_BRUSH_HEAD_USAGE = "brush_head_usage"
SENSOR_LAST_BRUSH_AREAS = "last_brush_areas"
SENSOR_LAST_BRUSH_SCHEME_TYPE = "last_brush_scheme_type"
SENSOR_LAST_BRUSH_PNUM = "last_brush_pnum"
BUTTON_RESET_BRUSH_HEAD = "reset_brush_head"

# Tooth area zone names in BrushAreaType enum order (value 1 → index 0 … value 8 → index 7)
# Source: com/ocleanble/lib/device/BrushAreaType.java
TOOTH_AREA_NAMES: tuple[str, ...] = (
    "upper_left_out",   # AREA_LIFT_UP_OUT    (value 1)
    "upper_left_in",    # AREA_LIFT_UP_IN     (value 2)
    "lower_left_out",   # AREA_LIFT_DOWN_OUT  (value 3)
    "lower_left_in",    # AREA_LIFT_DOWN_IN   (value 4)
    "upper_right_out",  # AREA_RIGHT_UP_OUT   (value 5)
    "upper_right_in",   # AREA_RIGHT_UP_IN    (value 6)
    "lower_right_out",  # AREA_RIGHT_DOWN_OUT (value 7)
    "lower_right_in",   # AREA_RIGHT_DOWN_IN  (value 8)
)

# Brush scheme name lookup: pNum → English display name.
# Source: GET /Romap/v1/DeviceContoller/GetAllResources (language=en), fetched 2026-02-22.
# Notes:
#   - pNum is device-family-specific; same integer can mean different schemes on different models.
#   - pNums 0-2 are omitted here because they conflict across device families.
#   - Missing translations (K1/OCLEANR3W/V1 series) are also omitted.
#   - pNums 21-50: OCLEANX1, OCLEANA1, OCLEANY2 family
#   - pNums 72-104: OCLEANY3, OCLEANY5, OCLEANR3W, OCLEANV1/V20 family (newer devices)
SCHEME_NAMES: dict[int, str] = {
    # OCLEANX1 / OCLEANA1 / OCLEANY2 family
    21: "Sensitive Cleaning",
    23: "Robust Cleaning",
    24: "Beginner",
    25: "Strong Cleaning",
    26: "Sugary Diet Cleaning",
    27: "Gestation Period Cleaning",
    28: "Strong Whitening",
    30: "Gum Care",
    31: "Standard Quick Cleaning",
    32: "Standard Whitening",
    34: "Deep Cleaning",
    36: "Braces Cleaning",
    37: "Sensitive Cleaning",
    38: "Robust Whitening",
    39: "Gentle Teeth Spa",
    40: "Teeth Spa",
    41: "Strong Teeth Spa",
    42: "Gentle Quick Cleaning",
    43: "Beginner",
    44: "Whitening",
    45: "Gum Massage",
    46: "Travel",
    47: "18 Days Whitening",
    48: "24 Days Whitening",
    49: "Teeth Strengthening",
    50: "Super Cleaning",
    53: "Standard Brushing Regimen",
    # OCLEANY3 / OCLEANY5 / OCLEANR3W / OCLEANV1/V20 family (newer models)
    72: "Strong Cleaning",
    73: "Super Cleaning",
    74: "Post-Wash Sensitivity",
    75: "Standard Whitening",
    76: "Strong Whitening",
    77: "Super Whitening",
    78: "Sensitive Cleaning",
    79: "Gentle Teeth Spa",
    80: "Standard Teeth Spa",
    81: "Deep Cleaning Spa",
    82: "Gum Care Cleaning",
    83: "Clear Your Mouth After Meals",
    84: "Gum Massage",
    85: "Gum Care Cleaning",
    86: "Newbie Whitening",
    87: "Braces Cleaning",
    88: "Quick Cleaning",
    89: "Travel",
    90: "Gestation Care",
    91: "Gentle Teeth Spa",
    92: "Standard Teeth Spa",
    93: "Deep Cleaning Spa",
    94: "Newbie Whitening",
    95: "Strong Whitening",
    96: "Super Whitening",
    97: "Sensitive Cleaning",
    98: "Braces Cleaning",
    99: "Strong Cleaning",
    100: "Super Cleaning",
    101: "Gestation Care",
    102: "Gum Care Cleaning",
    104: "Gum Care Cleaning",
}

# Persistent storage for session history
STORAGE_VERSION = 1

# Max number of 0309 pages to fetch per poll (safety limit)
MAX_SESSION_PAGES = 50
