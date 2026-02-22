"""Parser for Oclean BLE notification data."""
from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any

from .const import (
    RESP_DEVICE_INFO,
    RESP_INFO,
    RESP_INFO_T1,
    RESP_K3GUIDE,
    RESP_STATE,
    TOOTH_AREA_NAMES,
)

_LOGGER = logging.getLogger(__name__)


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

    Note: last_brush_score, last_brush_duration, and last_brush_clean are NOT
    available from the STATE (0303) notification. They arrive via the INFO
    response (0308) path when the device has completed brush sessions.
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
        record = _parse_extended_running_data_record(payload)
        if record:
            return record

    # Fall back to simple 18-byte format
    record = _parse_running_data_record(payload)
    if record:
        return record

    _LOGGER.debug("Oclean INFO: could not parse payload, raw: %s", payload.hex())
    return {}


def _parse_info_t1_response(payload: bytes) -> dict[str, Any]:
    """Parse a 0307 Type-1 running-data response payload (Oclean X).

    Byte layout confirmed empirically (Oclean X, 5 sessions, 2026-02-21 to 2026-02-22):

      bytes 0-4: device/model constant (same across all sessions – not session data)
        0x2a 0x42 0x23 0x00 0x00 observed; purpose unknown (model ID? pNum?)

      byte 5:  year - 2000  (confirmed)
      byte 6:  month        (confirmed)
      byte 7:  day          (confirmed)
      byte 8:  hour         (confirmed)
      byte 9:  minute       (confirmed)
      byte 10: second       (confirmed)

      byte 11: unknown (observed values: 0x00, 0x4c, 0xe7, 0x13, 0x1f, 0x1c, 0x4d –
               highly variable; purpose unknown)

      byte 12: 0x00 (consistent – padding)
      byte 13: brushing metric in seconds (CONFIRMED – see score/duration computation below)
      byte 14: 0x00 (consistent – padding)
      byte 15: unknown (observed: 0x96, 0x1e, 0x78, 0x0b – NOT always equal to byte 13;
               initial "redundant copy" hypothesis disproved by session 2026-02-22 where
               byte13=0x96=150 but byte15=0x0b=11; purpose unknown – ignored)
      byte 16: unknown (observed: 0x00, 0x02, 0x07, 0x01, 0x64) – purpose unclear
      byte 17: session counter (empirically observed as monotonically increasing, 0-indexed;
               observed values 0, 1, 4, 5 – some sessions not captured in between)

    Note: the app-displayed score (1–100) is NOT present in the BLE payload;
    it is computed server-side by the Oclean cloud from raw sensor data.
    Timezone is not applied; device local time is used directly.
    """
    _LOGGER.debug("Oclean Type-1 INFO response raw: %s", payload.hex())

    _T1_MIN_SIZE = 14  # need at least through byte 13 (brushing metric)
    if len(payload) < _T1_MIN_SIZE:
        _LOGGER.debug("Oclean Type-1 INFO: payload too short (%d bytes)", len(payload))
        return {}

    try:
        import datetime as _dt
        import time as _time

        # bytes 5-10: device local timestamp (confirmed)
        year = payload[5] + 2000
        month = payload[6]
        day = payload[7]
        hour = payload[8]
        minute = payload[9]
        second = payload[10]

        device_dt = _dt.datetime(year, month, day, hour, minute, second)
        # The 0307 payload carries no timezone offset – the device stores local time.
        # time.mktime() interprets the struct_time as the system local timezone
        # (= HA configured timezone on HA OS) and returns a correct UTC Unix timestamp.
        timestamp_s = int(_time.mktime(device_dt.timetuple()))

        result: dict[str, Any] = {
            "last_brush_time": timestamp_s,
        }

        # byte 13: brushing metric in seconds (Oclean X counts in 30-second zones;
        #   minimum value = 30 even for very short sessions, maximum ~ 120–150).
        #   Confirmed: byte13=30 → score 1 (7 s brush),
        #              byte13=120 → score 90 (2 min brush).
        #
        # Score formula (empirically confirmed):
        #   score = clamp(byte13 - 30, 1, 100)
        #   • byte13=30  → clamp(0,  1, 100) =  1  (minimum for any short session)
        #   • byte13=120 → clamp(90, 1, 100) = 90  (full 2-minute brush)
        #   • byte13≥130 → clamp(≥100, 1, 100) = 100 (perfect)
        #
        # Duration: byte13 is returned as-is in seconds; note that 30 is the
        #   device floor (a 7 s session still reports 30 s).
        #
        brushing_metric = payload[13]
        if brushing_metric > 0:
            result["last_brush_score"] = max(1, min(100, brushing_metric - 30))
            result["last_brush_duration"] = brushing_metric  # seconds (floor: 30)

        _LOGGER.debug(
            "Oclean 0307 parsed: %s (raw: %s, byte13=%d)",
            result, payload.hex(), brushing_metric,
        )

        # Log unknown bytes to help identify their purpose over time.
        # Enable via:  logger: logs: custom_components.oclean_ble: debug
        _LOGGER.debug(
            "Oclean 0307 unknown bytes –"
            " const=%s (device model constant? bytes0-4)"
            " b11=0x%02x (unknown, highly variable)"
            " b15=0x%02x (unknown; NOT always equal to byte13=0x%02x)"
            " b16=0x%02x (unknown)"
            " b17=0x%02x (session counter?)",
            payload[0:5].hex(),
            payload[11],
            payload[15] if len(payload) > 15 else -1,
            brushing_metric,
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
        year = data[0] + 2000
        month = data[1]
        day = data[2]
        hour = data[3]
        minute = data[4]
        second = data[5]
        # byte 6: timezone offset in quarter-hours (signed)
        tz_offset_quarters = data[6] if data[6] < 128 else data[6] - 256
        week = data[7]
        p_num = data[8]
        # bytes 9–13: unknown

        # bytes 14–15: blunt-teeth count (little-endian uint16)
        blunt_teeth = int.from_bytes(data[14:16], byteorder="little")

        # bytes 16–17: pressure raw (little-endian uint16) / 300
        pressure_raw = int.from_bytes(data[16:18], byteorder="little")
        pressure = round(pressure_raw / 300, 2)

        # Build UTC timestamp from device-local time + tz offset
        import calendar as _cal
        import datetime as _dt

        # tz_offset_quarters is in 15-minute steps
        tz_offset_minutes = tz_offset_quarters * 15
        device_dt = _dt.datetime(year, month, day, hour, minute, second)
        utc_dt = device_dt - _dt.timedelta(minutes=tz_offset_minutes)
        timestamp_ms = int(_cal.timegm(utc_dt.timetuple())) * 1000

        result: dict[str, Any] = {
            "last_brush_time": timestamp_ms // 1000,
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
        import calendar as _cal
        import datetime as _dt

        year = data[2] + 2000
        month = data[3]
        day = data[4]
        hour = data[5]
        minute = data[6]
        second = data[7]
        p_num = int(data[8])
        duration = int.from_bytes(data[9:11], byteorder="big")
        # data[13:18]: 5 intermediate pressure zone values (not mapped to sensors)
        tz_offset_quarters = int(data[19]) if data[19] < 128 else int(data[19]) - 256
        area_pressures = data[20:28]   # 8 tooth area pressure bytes
        score = int(data[28])
        scheme_type = int(data[29])

        # Build UTC timestamp from device-local time + timezone offset
        tz_offset_minutes = tz_offset_quarters * 15
        device_dt = _dt.datetime(year, month, day, hour, minute, second)
        utc_dt = device_dt - _dt.timedelta(minutes=tz_offset_minutes)
        timestamp_s = int(_cal.timegm(utc_dt.timetuple()))

        # Map 8 area pressures to named zones (BrushAreaType order: index 0 = value 1)
        area_dict: dict[str, int] = {
            name: int(area_pressures[i]) for i, name in enumerate(TOOTH_AREA_NAMES)
        }
        zones_cleaned = sum(1 for v in area_pressures if v > 0)

        # Average pressure across all 8 tooth zones (raw 0-255)
        avg_pressure = round(sum(area_pressures) / len(area_pressures))

        result: dict[str, Any] = {
            "last_brush_time": timestamp_s,
            "last_brush_duration": duration,
            "last_brush_score": max(0, min(100, score)),
            "last_brush_pressure": avg_pressure,
            "last_brush_areas": area_dict,
            "last_brush_scheme_type": scheme_type,
            "last_brush_pnum": p_num,
        }

        # Coverage proxy for last_brush_clean: percentage of zones with non-zero pressure
        if zones_cleaned > 0:
            result["last_brush_clean"] = round(zones_cleaned / 8 * 100)

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


# Strategy registry: 2-byte response-type prefix → handler function.
# To add support for a new notification type, add one entry here.
_PARSERS: dict[bytes, Callable[[bytes], dict[str, Any]]] = {
    RESP_STATE:       _parse_state_response,
    RESP_INFO:        _parse_info_response,
    RESP_INFO_T1:     _parse_info_t1_response,
    RESP_DEVICE_INFO: _handle_device_info_ack,
    RESP_K3GUIDE:     _parse_k3guide_response,
}


_JSON_KEY_MAP: tuple[tuple[str, tuple[str, ...], bool], ...] = (
    # (result_key, candidate_keys, cast_to_int)
    ("last_brush_score",    ("score", "brushScore", "brush_score", "totalScore"),        True),
    ("last_brush_duration", ("duration", "brushDuration", "brush_duration", "time"),     True),
    ("last_brush_clean",    ("clean", "cleanScore", "clean_score", "cleanPercent"),      True),
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
