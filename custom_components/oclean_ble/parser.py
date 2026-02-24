"""Parser for Oclean BLE notification data."""
from __future__ import annotations

import calendar
import datetime
import json
import logging
import time
from collections.abc import Callable
from typing import Any

from .const import (
    RESP_BRUSH_AREAS_T1,
    RESP_DEVICE_INFO,
    RESP_EXTENDED_T1,
    RESP_INFO,
    RESP_INFO_T1,
    RESP_K3GUIDE,
    RESP_SCORE_T1,
    RESP_SESSION_META_T1,
    RESP_STATE,
    TOOTH_AREA_NAMES,
)

_LOGGER = logging.getLogger(__name__)

# Earliest plausible session year for any Oclean device.
_MIN_YEAR = 2015


def _parse_signed_byte(value: int) -> int:
    """Interpret a single byte as a signed int8 (-128..127)."""
    return value if value < 128 else value - 256


def _build_utc_timestamp(device_dt: datetime.datetime, tz_offset_quarters: int) -> int:
    """Convert a device-local datetime and timezone offset to a UTC Unix timestamp.

    Args:
        device_dt: Device-local datetime (no tzinfo).
        tz_offset_quarters: Signed offset from UTC in 15-minute steps.
    """
    utc_dt = device_dt - datetime.timedelta(minutes=tz_offset_quarters * 15)
    return int(calendar.timegm(utc_dt.timetuple()))


def _build_area_stats(
    area_pressures: bytes,
) -> tuple[dict[str, int], int, int]:
    """Build area-pressure dict, cleaned-zone count, and average pressure.

    Returns:
        (area_dict, zones_cleaned, avg_pressure)
    """
    area_dict: dict[str, int] = {
        name: int(area_pressures[i]) for i, name in enumerate(TOOTH_AREA_NAMES)
    }
    zones_cleaned = sum(1 for v in area_pressures if v > 0)
    avg_pressure = round(sum(area_pressures) / len(area_pressures))
    return area_dict, zones_cleaned, avg_pressure


def _device_datetime(
    year_byte: int,
    month: int,
    day: int,
    hour: int,
    minute: int,
    second: int,
) -> datetime.datetime:
    """Build a device-local datetime from a year-2000-encoded byte.

    Raises ValueError if the resulting year predates the first Oclean devices,
    which is caught by each parser's existing except block.
    """
    year = year_byte + 2000
    if year < _MIN_YEAR:
        raise ValueError(f"implausible year {year} (byte={year_byte:#04x})")
    return datetime.datetime(year, month, day, hour, minute, second)


def parse_notification(data: bytes) -> dict[str, Any]:
    """Parse a BLE notification from the Oclean device.

    Dispatches to the appropriate handler via the ``_PARSERS`` registry
    (Strategy pattern). Unknown data is logged as hex for empirical
    analysis during testing.
    """
    if len(data) < 2:
        _LOGGER.debug("Oclean notification too short: %s", data.hex())
        return {}

    handler = _PARSERS.get(data[:2])
    if handler is not None:
        return handler(data[2:])

    # Try JSON fallback (brush session data may arrive as JSON string)
    try:
        text = data.decode("utf-8").strip()
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            _LOGGER.debug("Oclean JSON notification: %s", parsed)
            return _map_json_brush_data(parsed)
    except (UnicodeDecodeError, json.JSONDecodeError):
        pass

    # Unknown format – log raw hex for debugging/empirical analysis
    _LOGGER.debug(
        "Oclean unknown notification type 0x%s, raw: %s",
        data[:2].hex().upper(),
        data.hex(),
    )
    return {}


def _parse_state_response(payload: bytes) -> dict[str, Any]:
    """Parse a 0303 state response payload (bytes after the 2-byte type marker).

    Observed byte layout on Oclean X (response to CMD_QUERY_STATUS 0303):
      byte 0: status flags (observed: always 0x02 on Oclean X)
      byte 1: unknown (observed: 0x0e, 0x0f – varies between polls)
      byte 2: unknown (observed: 0x4b, 0x00 – varies; earlier "cached score" hypothesis disproved)
      byte 3: battery % (confirmed: matches GATT Battery Characteristic read)
      bytes 4-5: unknown (observed: 0x00 0x00)

    Note: last_brush_score and last_brush_duration are NOT available from the
    STATE (0303) notification. They arrive via the INFO response (0308) path
    when the device has completed brush sessions.
    """
    result: dict[str, Any] = {}

    if len(payload) < 1:
        _LOGGER.debug("Oclean STATE response too short: %s", payload.hex())
        return result

    # byte 3 = battery level (confirmed: matches GATT Battery Characteristic read).
    if len(payload) >= 4:
        batt = int(payload[3])
        if 0 <= batt <= 100:
            result["battery"] = batt

    _LOGGER.debug("Oclean STATE parsed: %s (raw: %s)", result, payload.hex())

    # Log unknown bytes to help identify their purpose over time.
    # Enable via:  logger: logs: custom_components.oclean_ble: debug
    if len(payload) >= 1:
        _LOGGER.debug(
            "Oclean STATE unknown bytes –"
            " b0=0x%02x (status? always 0x02 on OcleanX)"
            " b1=0x%02x (unknown, varies)"
            " b2=0x%02x (unknown, varies)"
            " b4-5=%s (unknown, always 0x0000 so far)",
            payload[0],
            payload[1] if len(payload) > 1 else -1,
            payload[2] if len(payload) > 2 else -1,
            payload[4:6].hex() if len(payload) >= 6 else "n/a",
        )

    return result


def _parse_info_response(payload: bytes) -> dict[str, Any]:
    """Parse a 0308 info response payload (CMD_QUERY_RUNNING_DATA).

    Two distinct record formats exist in the firmware:

    **Extended format** (AbstractC0002b.m37y – 32+ bytes):
      Identified by: payload[0] == 0 (high byte of BE uint16 length header)
      AND payload[1] >= 32 (minimum extended record size).
      Contains: timestamp, pNum, duration, validDuration, 5 pressure zones,
                tz offset, 8 tooth area pressures, score, schemeType, and more.

    **Simple format** (C3340b1.m5348m1 – 18 bytes):
      Identified by: payload[0] >= 1 (year-2000 for any date from 2001 onward).
      Contains: timestamp, tz offset, week, pNum, blunt-teeth count, pressure raw.
    """
    _LOGGER.debug("Oclean INFO response raw payload: %s", payload.hex())

    # Extended format: byte 0 is the high byte of a BE uint16 record-length field.
    # For BLE payloads (MTU < 256 bytes) this is always 0; byte 1 is the actual length.
    # The simple format has byte 0 = year-2000 which is ≥ 24 for any current date.
    if (
        len(payload) >= 2
        and payload[0] == 0
        and payload[1] >= 32
        and len(payload) >= payload[1]
    ):
        # Extended format confirmed – do NOT fall back to simple parser on failure.
        # Simple parser would misread the length header as year=2000, producing a
        # plausible-but-wrong timestamp while all richer fields remain empty.
        record = _parse_extended_running_data_record(payload)
        if record:
            return record
        _LOGGER.debug("Oclean INFO: extended format detected but parsing failed, raw: %s", payload.hex())
        return {}

    # Simple 18-byte format
    record = _parse_running_data_record(payload)
    if record:
        return record

    _LOGGER.debug("Oclean INFO: could not parse payload, raw: %s", payload.hex())
    return {}


def _parse_info_t1_response(payload: bytes) -> dict[str, Any]:
    """Parse a 0307 Type-1 running-data response payload (Oclean X).

    Byte layout confirmed empirically (Oclean X, 5 sessions, 2026-02-21 to 2026-02-22):

      byte  0:   pNum candidate – UNCONFIRMED (observed: 0x2a=42 consistently across
                 polls; could be the brush-scheme ID if only one session is present;
                 needs multi-session data with different scheme selections to confirm)
      bytes 1-4: unknown constant (observed: 0x42 0x23 0x00 XX; byte 4 varies 0/1/2)

      byte 5:  year - 2000  (confirmed)
      byte 6:  month        (confirmed)
      byte 7:  day          (confirmed)
      byte 8:  hour         (confirmed)
      byte 9:  minute       (confirmed)
      byte 10: second       (confirmed)

      byte 11: unknown (observed values: 0x00, 0x4c, 0xe7, 0x13, 0x1f, 0x1c, 0x4d –
               highly variable; purpose unknown)

      byte 12: 0x00 (consistent – padding)
      byte 13: unknown (variable; observed: 0x4d, 0x00, 0xe7, 0x4c, 0x1c, 0x27 – NOT
               parsed by the official APK; purpose unconfirmed)
      byte 14: 0x00 (consistent – padding)
      byte 15: unknown (observed: 0x96, 0x1e, 0x78, 0x0b – NOT always equal to byte 13;
               initial "redundant copy" hypothesis disproved by session 2026-02-22 where
               byte13=0x96=150 but byte15=0x0b=11; purpose unknown – ignored)
      byte 16: unknown (observed: 0x00, 0x02, 0x07, 0x01, 0x64) – purpose unclear
      byte 17: session counter (empirically observed as monotonically increasing, 0-indexed;
               observed values 0, 1, 4, 5 – some sessions not captured in between)

    Note: the device-computed score is NOT present in this payload.  It
    arrives separately in the 0000 (RESP_SCORE_T1) notification which fires
    after the session and overwrites whatever this handler would set.
    Timezone is not applied; device local time is used directly.
    """
    _LOGGER.debug("Oclean Type-1 INFO response raw: %s", payload.hex())

    _T1_MIN_SIZE = 11  # need at least through byte 10 (timestamp second field)
    if len(payload) < _T1_MIN_SIZE:
        _LOGGER.debug("Oclean Type-1 INFO: payload too short (%d bytes)", len(payload))
        return {}

    try:
        # bytes 5-10: device local timestamp (confirmed)
        device_dt = _device_datetime(
            payload[5], payload[6], payload[7], payload[8], payload[9], payload[10]
        )
        # The 0307 payload carries no timezone offset – the device stores local time.
        # time.mktime() interprets the struct_time as the system local timezone
        # (= HA configured timezone on HA OS) and returns a correct UTC Unix timestamp.
        timestamp_s = int(time.mktime(device_dt.timetuple()))

        result: dict[str, Any] = {
            "last_brush_time": timestamp_s,
        }

        # byte 0: pNum candidate (UNCONFIRMED).
        # Observed as 0x2a=42 consistently, but only one session has been captured so far.
        # If this byte changes when the user selects a different brush scheme, it IS the pNum.
        # Store it provisionally so the "Last Brush Scheme" sensor can show a value.
        p_num_candidate = int(payload[0])
        result["last_brush_pnum"] = p_num_candidate

        _LOGGER.debug(
            "Oclean 0307 parsed: ts=%d pNum_candidate=%d (byte0=0x%02x, UNCONFIRMED)"
            " – verify by switching schemes and checking if byte0 changes (raw: %s)",
            timestamp_s, p_num_candidate, p_num_candidate, payload.hex(),
        )

        # Log remaining unknown bytes for ongoing protocol analysis.
        # Enable via:  logger: logs: custom_components.oclean_ble: debug
        _LOGGER.debug(
            "Oclean 0307 unknown bytes –"
            " b1-4=%s (unknown constant)"
            " b11=0x%02x (unknown, highly variable)"
            " b15=0x%02x (NOT a copy of b13; purpose unknown)"
            " b16=0x%02x (unknown)"
            " b17=0x%02x (session counter?)",
            payload[1:5].hex(),
            payload[11] if len(payload) > 11 else -1,
            payload[15] if len(payload) > 15 else -1,
            payload[16] if len(payload) > 16 else -1,
            payload[17] if len(payload) > 17 else -1,
        )

        return result

    except (IndexError, ValueError, OverflowError) as err:
        _LOGGER.debug("Oclean Type-1 record parse error: %s (raw: %s)", err, payload.hex())
        return {}


# Minimum bytes per running-data record (from m5348m1 byte access pattern)
_RUNNING_DATA_MIN_RECORD_SIZE = 18


def _parse_running_data_record(data: bytes) -> dict[str, Any]:
    """Parse one binary running-data record from CMD_QUERY_RUNNING_DATA (0308) response.

    Byte layout reverse-engineered from C3340b1.m5348m1().
    Returns an empty dict if the data is too short or looks invalid.
    """
    if len(data) < _RUNNING_DATA_MIN_RECORD_SIZE:
        return {}

    try:
        device_dt = _device_datetime(data[0], data[1], data[2], data[3], data[4], data[5])
        # byte 6: timezone offset in quarter-hours (signed)
        tz_offset_quarters = _parse_signed_byte(data[6])
        week = data[7]
        p_num = data[8]
        # bytes 9–13: unknown

        # bytes 14–15: blunt-teeth count (little-endian uint16)
        blunt_teeth = int.from_bytes(data[14:16], byteorder="little")

        # bytes 16–17: pressure raw (little-endian uint16) / 300
        pressure_raw = int.from_bytes(data[16:18], byteorder="little")
        pressure = round(pressure_raw / 300, 2)

        timestamp_s = _build_utc_timestamp(device_dt, tz_offset_quarters)

        result: dict[str, Any] = {
            "last_brush_time": timestamp_s,
            "last_brush_pressure": pressure,
            "brush_head_usage": blunt_teeth,
        }
        _LOGGER.debug(
            "Oclean 0308-simple parsed: %s (blunt_teeth=%d, pNum=%d, week=%d)",
            result, blunt_teeth, p_num, week,
        )

        # Log unknown bytes to help identify their purpose over time.
        _LOGGER.debug(
            "Oclean 0308-simple unknown bytes –"
            " b7=0x%02x week=%d (weekday? 0-indexed?)"
            " b8=0x%02x pNum=%d (brush-scheme ID, not mapped to name here)"
            " b9-13=%s (unknown – 5 bytes)",
            data[7], week,
            data[8], p_num,
            data[9:14].hex(),
        )

        return result

    except (IndexError, ValueError, OverflowError) as err:
        _LOGGER.debug("Oclean running-data record parse error: %s (raw: %s)", err, data.hex())
        return {}


def _parse_extended_running_data_record(data: bytes) -> dict[str, Any]:
    """Parse a 32+ byte extended running-data record (AbstractC0002b.m37y format).

    Byte layout reverse-engineered from AbstractC0002b.m37y():
      bytes  0-1:  record_length (BE uint16, total including this header)
      byte   2:    year - 2000
      byte   3:    month (1-12)
      byte   4:    day (1-31)
      byte   5:    hour (0-23)
      byte   6:    minute (0-59)
      byte   7:    second (0-59)
      byte   8:    pNum (brush-scheme ID; cloud-managed name)
      bytes  9-10: duration (BE uint16, total session seconds)
      bytes 11-12: validDuration (BE uint16, seconds with valid pressure)
      bytes 13-17: 5 pressure zone values (byte each)
      byte  18:    RESERVED
      byte  19:    timezone offset (signed int8, quarter-hours from UTC)
      bytes 20-27: 8 tooth area pressure values (BrushAreaType order, 1-8)
      byte  28:    score (0-100)
      byte  29:    schemeType (0-8, scheme category)
      byte  30:    busBrushing flag
      byte  31:    crossNumber (overPullNum)
      bytes 32+:   pressureProfile (variable)
    """
    _EXT_MIN_SIZE = 32
    if len(data) < _EXT_MIN_SIZE:
        return {}

    try:
        device_dt = _device_datetime(data[2], data[3], data[4], data[5], data[6], data[7])
        p_num = int(data[8])
        duration = int.from_bytes(data[9:11], byteorder="big")
        # data[13:18]: 5 intermediate pressure zone values (not mapped to sensors)
        tz_offset_quarters = _parse_signed_byte(data[19])
        area_pressures = data[20:28]   # 8 tooth area pressure bytes
        score = int(data[28])
        scheme_type = int(data[29])

        timestamp_s = _build_utc_timestamp(device_dt, tz_offset_quarters)
        area_dict, zones_cleaned, avg_pressure = _build_area_stats(area_pressures)

        result: dict[str, Any] = {
            "last_brush_time": timestamp_s,
            "last_brush_duration": duration,
            "last_brush_score": max(0, min(100, score)),
            "last_brush_pressure": avg_pressure,
            "last_brush_areas": area_dict,
            "last_brush_scheme_type": scheme_type,
            "last_brush_pnum": p_num,
        }

        _LOGGER.debug(
            "Oclean extended running-data: ts=%d score=%d duration=%ds pNum=%d "
            "schemeType=%d zones_cleaned=%d/8 avg_pressure=%d",
            timestamp_s, score, duration, p_num, scheme_type, zones_cleaned, avg_pressure,
        )
        return result

    except (IndexError, ValueError, OverflowError) as err:
        _LOGGER.debug("Oclean extended record parse error: %s (raw: %s)", err, data.hex())
        return {}


def _parse_k3guide_response(payload: bytes) -> dict[str, Any]:
    """Parse 0340 K3GUIDE real-time zone guidance notification.

    Sent by K3-series devices during active brushing to indicate the current
    active zone and live pressure per quadrant. Used for real-time guidance on
    the device display. Not stored as persistent sensor state.

    Byte layout (C3367n0.java):
      byte 0: liftUp          – left upper zone pressure (0-255)
      byte 1: liftDown        – left lower zone pressure (0-255)
      byte 2: rightUp         – right upper zone pressure (0-255)
      byte 3: rightDown       – right lower zone pressure (0-255)
      byte 4: currentPosition – active zone ID (1-8; 255 = brushing stopped)
      byte 5: workingState    – device working state
    """
    if len(payload) < 6:
        _LOGGER.debug("Oclean K3GUIDE payload too short: %s", payload.hex())
        return {}

    zone_id = payload[4]
    zone_name = (
        TOOTH_AREA_NAMES[zone_id - 1] if 1 <= zone_id <= 8 else "stop"
    )
    _LOGGER.debug(
        "Oclean K3GUIDE: liftUp=%d liftDown=%d rightUp=%d rightDown=%d "
        "zone=%d(%s) workingState=%d",
        payload[0], payload[1], payload[2], payload[3],
        zone_id, zone_name, payload[5],
    )
    # Real-time data only – not stored as persistent sensor state
    return {}


def _handle_device_info_ack(payload: bytes) -> dict[str, Any]:
    """Handle 0202 device-info ACK (no sensor data payload)."""
    _LOGGER.debug("Oclean device-info ACK: %s", payload.hex())
    return {}


def _parse_score_t1_response(payload: bytes) -> dict[str, Any]:
    """Parse 0000 score-push notification (Type-1 devices: Oclean X series).

    Observed byte layout (reverse-engineered from BLE log analysis 2026-02-24):
      byte  0: brushing score (0-100); 0xFF = no data
      byte  1: unknown (observed: 0x00)
      bytes 2-8: 7 × 0xFF = empty previous-session slots
      bytes 9+: older session reference data (timestamp-like)

    This notification is pushed by the device after a brushing session completes
    and carries the device-computed score.  It arrives *after* the 0307 response
    in the same poll cycle, so it correctly overwrites the 0307 formula estimate.
    """
    if len(payload) < 1:
        _LOGGER.debug("Oclean 0000 score: payload empty")
        return {}
    score = payload[0]
    if score == 0xFF:
        _LOGGER.debug("Oclean 0000 score: no data (0xFF)")
        return {}
    score_clamped = max(0, min(100, score))
    _LOGGER.debug("Oclean 0000 score=%d (raw: %s)", score_clamped, payload.hex())
    return {"last_brush_score": score_clamped}


def _parse_session_meta_t1_response(payload: bytes) -> dict[str, Any]:
    """Parse 5a00 session-metadata push (Type-1 devices: Oclean X series).

    Observed byte layout (reverse-engineered from BLE log analysis 2026-02-24):
      bytes 0-6:  7 × 0xFF = empty slots (no previous sessions stored)
      byte  7:    year - 2000  (e.g. 0x1A = 26 → 2026)
      byte  8:    month (1-12)
      byte  9:    day   (1-31)
      byte  10:   hour  (0-23)
      byte  11:   minute (0-59)
      byte  12:   second (0-59)
      byte  13:   unknown
      byte  14:   unknown
      byte  15:   session duration in seconds (same value duplicated at byte 17)
      byte  16:   unknown
      byte  17:   session duration in seconds (duplicate of byte 15)

    NOTE: The coordinator applies "newer timestamp wins" logic before merging
    these values into the shared `collected` dict, so a stale 5a00 (which may
    carry an older session than the concurrent 0307 response) never overwrites
    a more recent timestamp + duration already seen in the same poll cycle.
    """
    if len(payload) < 18:
        _LOGGER.debug("Oclean 5a00 too short (%d bytes): %s", len(payload), payload.hex())
        return {}

    try:
        device_dt = _device_datetime(
            payload[7], payload[8], payload[9],
            payload[10], payload[11], payload[12],
        )
        timestamp_s = int(time.mktime(device_dt.timetuple()))
        duration = payload[15]
        _LOGGER.debug(
            "Oclean 5a00 session: date=%s ts=%d duration=%ds b13=0x%02x b16=0x%02x (raw: %s)",
            device_dt.strftime("%Y-%m-%d %H:%M:%S"),
            timestamp_s,
            duration,
            payload[13],
            payload[16],
            payload.hex(),
        )
        result: dict[str, Any] = {"last_brush_time": timestamp_s}
        if duration > 0 and duration != 0xFF:
            result["last_brush_duration"] = duration
        return result
    except (IndexError, ValueError, OverflowError) as err:
        _LOGGER.debug("Oclean 5a00 parse error: %s (raw: %s)", err, payload.hex())
        return {}


def _parse_brush_areas_t1_response(payload: bytes) -> dict[str, Any]:
    """Parse 2604 per-tooth-area data (Type-1 devices: Oclean X series).

    Observed byte layout (reverse-engineered from BLE log analysis 2026-02-24):
      byte  0:   unknown (observed: 0x39 = 57; possibly secondary score or session counter)
      bytes 1-3: unknown (observed: 0x000000)
      byte  4:   unknown (observed: 0x0F = 15)
      byte  5:   unknown (observed: 0x00)
      bytes 6-13: 8 tooth-area pressure values, BrushAreaType order
                  (AREA_LIFT_UP_OUT … AREA_RIGHT_DOWN_IN; same as extended 0308 format)
      bytes 14+:  additional zone data (purpose unknown)
    """
    if len(payload) < 14:
        _LOGGER.debug("Oclean 2604 too short (%d bytes): %s", len(payload), payload.hex())
        return {}

    area_pressures = payload[6:14]
    area_dict, zones_cleaned, avg_pressure = _build_area_stats(area_pressures)

    result: dict[str, Any] = {
        "last_brush_areas": area_dict,
        "last_brush_pressure": avg_pressure,
    }

    _LOGGER.debug(
        "Oclean 2604 areas: %s zones_cleaned=%d/8 avg_pressure=%d"
        " b0=0x%02x b4=0x%02x (raw: %s)",
        area_dict, zones_cleaned, avg_pressure,
        payload[0], payload[4], payload.hex(),
    )
    return result


def _parse_0314_response(payload: bytes) -> dict[str, Any]:
    """Log a 0314 extended-data response for protocol research.

    The 0314 command (CMD_QUERY_EXTENDED_DATA_T1) is sent to SEND_BRUSH_CMD_UUID
    on Type-1 devices (Oclean X Pro / C3376s).  The device has not been observed
    to respond to this command; this handler logs if a response is ever received.
    """
    _LOGGER.debug(
        "Oclean 0314 response received – raw hex: %s  len=%d",
        payload.hex(),
        len(payload),
    )
    for i, b in enumerate(payload):
        _LOGGER.debug("  0314[%02d] = 0x%02X  (%d)", i, b, b)
    return {}


# Strategy registry: 2-byte response-type prefix → handler function.
# To add support for a new notification type, add one entry here.
_PARSERS: dict[bytes, Callable[[bytes], dict[str, Any]]] = {
    RESP_STATE:           _parse_state_response,
    RESP_INFO:            _parse_info_response,
    RESP_INFO_T1:         _parse_info_t1_response,
    RESP_DEVICE_INFO:     _handle_device_info_ack,
    RESP_K3GUIDE:         _parse_k3guide_response,
    RESP_EXTENDED_T1:     _parse_0314_response,
    RESP_SCORE_T1:        _parse_score_t1_response,
    RESP_SESSION_META_T1: _parse_session_meta_t1_response,
    RESP_BRUSH_AREAS_T1:  _parse_brush_areas_t1_response,
}


_JSON_KEY_MAP: tuple[tuple[str, tuple[str, ...], bool], ...] = (
    # (result_key, candidate_keys, cast_to_int)
    ("last_brush_score",    ("score", "brushScore", "brush_score", "totalScore"),        True),
    ("last_brush_duration", ("duration", "brushDuration", "brush_duration", "time"),     True),
    ("last_brush_pressure", ("pressure", "avgPressure", "avg_pressure"),                 True),
    ("last_brush_time",     ("timestamp", "endTime", "end_time", "brushTime"),           False),
)


def _map_json_brush_data(data: dict[str, Any]) -> dict[str, Any]:
    """Map keys from a JSON brush-session notification to internal data keys.

    Field names are guesses based on common Oclean API patterns.
    Adjust after observing actual notifications.
    """
    result: dict[str, Any] = {}
    for result_key, candidates, cast_int in _JSON_KEY_MAP:
        for key in candidates:
            if key in data:
                result[result_key] = int(data[key]) if cast_int else data[key]
                break
    if result:
        _LOGGER.debug("Oclean brush session data mapped: %s", result)
    else:
        _LOGGER.debug("Oclean JSON brush data – no known keys matched: %s", data)
    return result


def parse_battery(data: bytes) -> int | None:
    """Parse battery level from the standard BLE Battery Characteristic.

    Returns battery percentage (0–100) or None if data is invalid.
    """
    if not data:
        return None
    level = data[0]
    if 0 <= level <= 100:
        return level
    _LOGGER.warning("Oclean unexpected battery value: %d", level)
    return None
