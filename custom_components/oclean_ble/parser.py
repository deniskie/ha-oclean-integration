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
    COVERAGE_PRESSURE_THRESHOLD,
    DATA_BATTERY,
    DATA_BRUSH_HEAD_DAYS,
    DATA_BRUSH_HEAD_USAGE,
    DATA_BRUSH_MODE,
    DATA_IS_BRUSHING,
    DATA_LAST_BRUSH_AREAS,
    DATA_LAST_BRUSH_COVERAGE,
    DATA_LAST_BRUSH_DURATION,
    DATA_LAST_BRUSH_GESTURE_ARRAY,
    DATA_LAST_BRUSH_GESTURE_CODE,
    DATA_LAST_BRUSH_PNUM,
    DATA_LAST_BRUSH_POWER_ARRAY,
    DATA_LAST_BRUSH_PRESSURE,
    DATA_LAST_BRUSH_PRESSURE_RATIO,
    DATA_LAST_BRUSH_SCORE,
    DATA_LAST_BRUSH_TIME,
    RESP_BRUSH_AREAS_T1,
    RESP_BRUSH_AREAS_Y3P,
    RESP_DEVICE_INFO,
    RESP_DEVICE_SETTINGS,
    RESP_EXTENDED_T1,
    RESP_INFO,
    RESP_INFO_T1,
    RESP_K3GUIDE,
    RESP_SCORE_T1,
    RESP_SESSION_META_T1,
    RESP_SESSION_META_Y3P,
    RESP_STATE,
    RESP_UNKNOWN_4B00,
    RESP_UNKNOWN_5400,
    TOOTH_AREA_NAMES,
    area_names_for_count,
)

_LOGGER = logging.getLogger(__name__)

# Earliest plausible session year for any Oclean device.
_MIN_YEAR = 2015
# Base year used in Oclean's year encoding (device stores year - 2000).
_YEAR_2000 = 2000
# Minimum payload sizes for each binary record format.
_RUNNING_DATA_MIN_RECORD_SIZE = 18  # 0308 simple format (m5348m1)
_T1_MIN_SIZE = 12  # 0307 Type-1 push (need through byte 11 for pNum)
_T1_FULL_RECORD_SIZE = 42  # 0307 paginated m18f record (C3385w0_fallback.java)

# Public alias used by the coordinator for *B# multi-packet reassembly.
# Both C3385w0 (OCLEANY3M) and C3352g (OCLEANY3/OCLEANY3P) use 42-byte records.
T1_C3352G_RECORD_SIZE: int = _T1_FULL_RECORD_SIZE
_EXT_MIN_SIZE = 32  # 0308 extended format (AbstractC0002b.m37y)


def _parse_signed_byte(value: int) -> int:
    """Interpret a single byte as a signed int8 (-128..127)."""
    return value if value < 128 else value - 256


def _extract_nibbles(byte_val: int) -> list[int]:
    """Extract 4 × 2-bit values from one byte (APK: a.b.a / m13a).

    Index 0 = MSBits 7-6, index 3 = LSBits 1-0.  Each value is 0-3.
    Used to decode powerArray from bytes 30-32 of 42-byte 0307 records.
    """
    return [(byte_val >> (6 - 2 * i)) & 0x3 for i in range(4)]


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
    zone_names: tuple[str, ...] | None = None,
) -> tuple[dict[str, int], int, int, int]:
    """Build area-pressure dict, cleaned-zone count, average pressure, and coverage %.

    Coverage follows the official Oclean app logic (APK: C2928q.java):
    a zone counts as adequately cleaned when raw_pressure > COVERAGE_PRESSURE_THRESHOLD.

    *zone_names* defaults to auto-detection based on ``len(area_pressures)``
    (8 → standard 8-zone, 12 → 12-zone YD-series).

    Returns:
        (area_dict, zones_cleaned, avg_pressure, coverage_pct)
    """
    if zone_names is None:
        zone_names = area_names_for_count(len(area_pressures))
    count = min(len(area_pressures), len(zone_names))
    area_dict: dict[str, int] = {zone_names[i]: int(area_pressures[i]) for i in range(count)}
    zones_cleaned = sum(1 for v in area_pressures[:count] if v > 0)
    zones_covered = sum(1 for v in area_pressures[:count] if v > COVERAGE_PRESSURE_THRESHOLD)
    avg_pressure = round(sum(area_pressures[:count]) / count) if count else 0
    coverage_pct = round(zones_covered / count * 100) if count else 0
    return area_dict, zones_cleaned, avg_pressure, coverage_pct


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
    year = year_byte + _YEAR_2000
    if year < _MIN_YEAR:
        raise ValueError(f"implausible year {year} (byte={year_byte:#04x})")
    return datetime.datetime(year, month, day, hour, minute, second)


def parse_notification(data: bytes, *, dental_cast: int = 8) -> dict[str, Any]:
    """Parse a BLE notification from the Oclean device.

    Dispatches to the appropriate handler via the ``_PARSERS`` registry
    (Strategy pattern). Unknown data is logged as hex for empirical
    analysis during testing.

    *dental_cast* (8 or 12) is forwarded to area-pressure parsers so they
    extract the correct number of zone bytes.
    """
    if len(data) < 2:
        _LOGGER.debug("Oclean notification too short: %s", data.hex())
        return {}

    handler = _PARSERS.get(data[:2])
    if handler is not None:
        return handler(data[2:], dental_cast=dental_cast)

    # Try JSON fallback (brush session data may arrive as JSON string)
    try:
        text = data.decode("utf-8").strip()
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            _LOGGER.debug("Oclean JSON notification: %s", parsed)
            return _map_json_brush_data(parsed)
    except (UnicodeDecodeError, json.JSONDecodeError):
        pass

    # OCLEANY3MH score+session record: dynamic prefix where byte 0 = score.
    # Structural fingerprint: data[1:3] == 0x03 0x03, data[4:9] == ff×5.
    # data[3] varies (observed: 0x03 for older sessions, 0x00 for recent ones).
    # Confirmed format (empirical logs + APK C3385w0_fallback.java):
    #   byte  0:   score (0-100)
    #   bytes 1-3: 03 03 XX  (XX = session-age indicator, varies)
    #   bytes 4-8: ff×5  (separator)
    #   byte  9:   year_base (+2000)
    #   byte 10:   month
    #   byte 11:   day
    #   byte 12:   hour
    #   byte 13:   minute
    #   byte 14:   second
    #   byte 15:   pNum
    #   bytes 16-17: duration BE (seconds)
    #   bytes 18-19: validDuration BE (seconds)
    if len(data) >= 20 and data[1] == 0x03 and data[2] == 0x03 and data[4:9] == b"\xff\xff\xff\xff\xff":
        return _parse_xx03_session_record(data)

    # Unknown format – log raw hex for debugging/empirical analysis
    _LOGGER.debug(
        "Oclean unknown notification type 0x%s, raw: %s",
        data[:2].hex().upper(),
        data.hex(),
    )
    return {}


def _parse_xx03_session_record(data: bytes) -> dict[str, Any]:
    """Parse an OCLEANY3MH score+session record with a dynamic 2-byte prefix.

    Called from parse_notification() when the structural fingerprint matches
    (data[1:3] == 0x03 0x03, data[4:9] == ff×5).  The full raw notification
    is passed (not stripped), so byte indices here are absolute.

    Confirmed via empirical logs (issue #19):
      - 0x3A at byte 0 = score 58  (user-confirmed against Oclean app)
      - year_base 0x1A at byte 9 = 2026, M/D/H/Min/S at bytes 10-14

    Score of 0x00 or > 100 is omitted (not yet available or device placeholder).
    """
    try:
        year_base = data[9]
        year = year_base + _YEAR_2000
        if year < _MIN_YEAR:
            _LOGGER.debug("Oclean XX03 record: implausible year_base 0x%02x, raw: %s", year_base, data.hex())
            return {}

        month, day, hour, minute, second = data[10], data[11], data[12], data[13], data[14]
        device_dt = datetime.datetime(year, month, day, hour, minute, second)
        timestamp_s = int(time.mktime(device_dt.timetuple()))

        result: dict[str, Any] = {
            DATA_LAST_BRUSH_TIME: timestamp_s,
            DATA_LAST_BRUSH_PNUM: int(data[15]),
        }

        score = data[0]
        if 0 < score <= 100:
            result[DATA_LAST_BRUSH_SCORE] = score

        duration = (data[16] << 8) | data[17]
        if duration > 0:
            result[DATA_LAST_BRUSH_DURATION] = duration

        _LOGGER.debug(
            "Oclean XX03 session record parsed: ts=%d score=%s pNum=%d duration=%s (raw: %s)",
            timestamp_s,
            result.get(DATA_LAST_BRUSH_SCORE, "n/a"),
            result[DATA_LAST_BRUSH_PNUM],
            result.get(DATA_LAST_BRUSH_DURATION, "n/a"),
            data.hex(),
        )
        return result

    except (IndexError, ValueError, OverflowError) as err:
        _LOGGER.debug("Oclean XX03 record parse error: %s (raw: %s)", err, data.hex())
        return {}


def _parse_state_response(payload: bytes, **kwargs: Any) -> dict[str, Any]:
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

    # byte 0 bit 0: is_brushing (confirmed via APK C3385w0 analysis)
    result[DATA_IS_BRUSHING] = bool(payload[0] & 0x01)

    # byte 3 = battery level (confirmed: matches GATT Battery Characteristic read).
    if len(payload) >= 4:
        batt = int(payload[3])
        if 0 <= batt <= 100:
            result[DATA_BATTERY] = batt

    _LOGGER.debug("Oclean STATE parsed: %s (raw: %s)", result, payload.hex())

    # Log unknown bytes to help identify their purpose over time.
    # Enable via:  logger: logs: custom_components.oclean_ble: debug
    _LOGGER.debug(
        "Oclean STATE unknown bytes –"
        " b0=0x%02x (bit0=is_brushing)"
        " b1=0x%02x (unknown, varies)"
        " b2=0x%02x (unknown, varies)"
        " b4-5=%s (unknown, always 0x0000 so far)",
        payload[0],
        payload[1] if len(payload) > 1 else -1,
        payload[2] if len(payload) > 2 else -1,
        payload[4:6].hex() if len(payload) >= 6 else "n/a",
    )

    return result


def _parse_info_response(payload: bytes, **kwargs: Any) -> dict[str, Any]:
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
    if len(payload) >= 2 and payload[0] == 0 and payload[1] >= 32 and len(payload) >= payload[1]:
        # Extended format confirmed – do NOT fall back to simple parser on failure.
        # Simple parser would misread the length header as year=2000, producing a
        # plausible-but-wrong timestamp while all richer fields remain empty.
        record = _parse_extended_running_data_record(payload)
        if record:
            return record
        _LOGGER.debug("Oclean INFO: extended format detected but parsing failed, raw: %s", payload.hex())
        return {}

    if len(payload) >= 2 and payload[0] == 0:
        # Byte 0 == 0 marks the extended-format header but payload[1] < 32 means
        # the device sent a short status/ack packet (no session record embedded).
        # Cannot be interpreted as simple format (simple format has year ≥ 24 at
        # byte 0).  Return silently – not an error.
        _LOGGER.debug("Oclean INFO: short status packet (%d bytes), no session data", len(payload))
        return {}

    # Simple 18-byte format
    record = _parse_running_data_record(payload)
    if record:
        return record

    _LOGGER.debug("Oclean INFO: could not parse payload, raw: %s", payload.hex())
    return {}


def _parse_m18f_record(record: bytes) -> dict[str, Any]:
    """Parse one full 42-byte m18f session record (paginated 0307 response).

    Byte layout confirmed from C3385w0_fallback.java (APK, DeviceType OCLEANY3M,
    C3385w0 mode=1, lines 1558-1705).

      byte  0:  year - 2000
      byte  1:  month
      byte  2:  day
      byte  3:  hour
      byte  4:  minute
      byte  5:  second
      byte  6:  pNum              (brush-scheme ID)
      bytes 7-8:  duration        (2-byte BE, total session seconds)
      bytes 9-10: validDuration   (2-byte BE, not stored as sensor)
      bytes 11-16: area1..area6   (tooth-area pressure bytes 1-6)
      byte 17:  reserved
      bytes 18-19: area7..area8   (tooth-area pressure bytes 7-8)
      bytes 20-32: gesture/power/pressure bitfield data
      byte 33:  score             (brush coverage score 0-100)
      byte 34:  point             (score rating, not used)
      bytes 35-41: reserved
    """
    if len(record) < _T1_FULL_RECORD_SIZE:
        return {}

    try:
        device_dt = _device_datetime(record[0], record[1], record[2], record[3], record[4], record[5])
        timestamp_s = int(time.mktime(device_dt.timetuple()))

        result: dict[str, Any] = {
            DATA_LAST_BRUSH_TIME: timestamp_s,
            DATA_LAST_BRUSH_PNUM: int(record[6]),
        }

        duration_s = (record[7] << 8) | record[8]
        if duration_s > 0:
            result[DATA_LAST_BRUSH_DURATION] = duration_s

        # Score at byte 33 (0xFF = no data)
        score = record[33]
        if 0 < score <= 100:
            result[DATA_LAST_BRUSH_SCORE] = score

        # pressureRatio: bytes 11-15 are time-distribution percentages (sum ≈ 100)
        # across 5 zone groups – NOT per-tooth area pressures.
        # Confirmed via Oclean Cloud API: pressureDistribution is always empty for
        # OCLEANY3M; the app uploads these as pressureRatio "#"-delimited strings.
        # Coverage = zone groups with ratio > 0 / 5 total groups × 100%.
        pressure_ratio = list(record[11:16])
        zones_active = sum(1 for v in pressure_ratio if v > 0)
        coverage_pct = round(zones_active / len(pressure_ratio) * 100)
        result[DATA_LAST_BRUSH_COVERAGE] = coverage_pct

        # gestureCode (APK: byte 14, overlaps pressureRatio[3])
        # pressureCode = m14b(bytes 11-15): maps dominant zone to a 50-90 score.
        result[DATA_LAST_BRUSH_GESTURE_CODE] = int(record[14])
        result[DATA_LAST_BRUSH_PRESSURE_RATIO] = pressure_ratio
        result[DATA_LAST_BRUSH_GESTURE_ARRAY] = list(record[18:31])  # bytes 18-30 inclusive
        result[DATA_LAST_BRUSH_POWER_ARRAY] = (
            _extract_nibbles(record[30]) + _extract_nibbles(record[31]) + _extract_nibbles(record[32])
        )
        _LOGGER.debug("Oclean 0307 m18f record point=%d (raw byte 34, APK: not used)", record[34])
        _LOGGER.debug(
            "Oclean 0307 m18f research: gestureCode=%d gestureArray=%s powerArray=%s",
            result[DATA_LAST_BRUSH_GESTURE_CODE],
            result[DATA_LAST_BRUSH_GESTURE_ARRAY],
            result[DATA_LAST_BRUSH_POWER_ARRAY],
        )

        _LOGGER.debug(
            "Oclean 0307 m18f parsed: ts=%d pNum=%d duration=%s score=%s gestureCode=%d (raw: %s)",
            timestamp_s,
            result[DATA_LAST_BRUSH_PNUM],
            result.get(DATA_LAST_BRUSH_DURATION, "n/a"),
            result.get(DATA_LAST_BRUSH_SCORE, "n/a"),
            result[DATA_LAST_BRUSH_GESTURE_CODE],
            record[:_T1_FULL_RECORD_SIZE].hex(),
        )
        return result

    except (IndexError, ValueError, OverflowError) as err:
        _LOGGER.debug(
            "Oclean m18f record parse error: %s (raw: %s)",
            err,
            record[:_T1_FULL_RECORD_SIZE].hex() if len(record) >= _T1_FULL_RECORD_SIZE else record.hex(),
        )
        return {}


def parse_t1_c3385w0_record(record: bytes) -> dict[str, Any]:
    """Parse one 42-byte session record for OCLEANY3M / OCLEANY3 (C3385w0 class).

    Used by OCLEANY3M (Oclean X) and OCLEANY3 (Oclean X Pro) when the coordinator
    reassembles the continuation packets from a ``0307 *B# count`` notification
    sequence with a non-zero year_base byte (byte 0 != 0x00).

    Byte layout (confirmed from real OCLEANY3MH device log + APK C3385w0_fallback.java):
      byte  0:   year_base (full_year − 2000)              ✓
      byte  1:   month (1-12)                              ✓
      byte  2:   day   (1-31)                              ✓
      byte  3:   hour  (0-23)                              ✓
      byte  4:   minute (0-59)                             ✓
      byte  5:   second (0-59)                             ✓
      byte  6:   pNum (brush-scheme ID)                    ✓
      bytes 7-8: duration BE uint16 (seconds)              ✓
      bytes 9-10: validDuration BE (not stored as sensor)  ✓
      bytes 11-15: area1..area5 (tooth zones)              ✓ APK + real device confirmed
      byte 16:   discarded by APK (not an area byte)       ✓ APK L1620 result not assigned
      byte 17:   timezone index → getTimeZoneString()      ✓ APK L1638+L1812 (not stored)
      bytes 18-29: gestureArray[0..11] (12 elements)       ✓ APK-confirmed; NOT area7-8
                   (areas 6-8 absent from *B# record; arrive via 2604 enrichment push)
      byte 30:   gestureCode (2-bit value at a.b.a(·, 2))  ✓ APK-confirmed; not yet extracted
                 + powerArray nibbles a.b.a(·, 0/1/3)
      bytes 31-32: powerArray nibble source                ✓ APK-confirmed; not yet extracted
      byte 33:   score (0-100, 0xFF = absent)              ✓
      byte 34:   point (not used as sensor)                ✓ APK-confirmed
      bytes 35-41: reserved                                ?

    Note: pressureRatio in the APK C3385w0 class maps to bytes 11-15, which are
    the same bytes as area1-5. It is therefore not a distinct sensor field.

    gestureCode, gestureArray and powerArray are not currently extracted (byte
    offsets are APK-confirmed but no real-device correlation available yet).

    All out-of-range values are silently omitted from the result.
    """
    if len(record) < T1_C3352G_RECORD_SIZE:
        _LOGGER.debug("Oclean C3385w0 record too short (%d bytes): %s", len(record), record.hex())
        return {}

    year_base = record[0]
    year = year_base + _YEAR_2000
    if year < _MIN_YEAR:
        _LOGGER.debug(
            "Oclean C3385w0 record: implausible year_base 0x%02x (year %d < %d), raw: %s",
            year_base,
            year,
            _MIN_YEAR,
            record[:T1_C3352G_RECORD_SIZE].hex(),
        )
        return {}

    try:
        month = record[1]
        day = record[2]
        hour = record[3]
        minute = record[4]
        second = record[5]

        device_dt = datetime.datetime(year, month, day, hour, minute, second)

        timestamp_s = int(time.mktime(device_dt.timetuple()))
        result: dict[str, Any] = {DATA_LAST_BRUSH_TIME: timestamp_s}

        result[DATA_LAST_BRUSH_PNUM] = int(record[6])

        duration_s = (record[7] << 8) | record[8]
        if duration_s > 0:
            result[DATA_LAST_BRUSH_DURATION] = duration_s

        score = record[33]
        if 0 < score <= 100:
            result[DATA_LAST_BRUSH_SCORE] = score

        # Areas are NOT extracted from the *B# record: the format only contains 5 of 8
        # tooth-zone bytes (bytes 11-15), and bytes 18-19 are gestureArray[0-1], not areas.
        # All 8 areas arrive via the 2604 enrichment push (_parse_brush_areas_t1_response).

        _LOGGER.debug(
            "Oclean C3385w0 record parsed: ts=%d pNum=%d duration=%s score=%s (raw: %s)",
            timestamp_s,
            result[DATA_LAST_BRUSH_PNUM],
            result.get(DATA_LAST_BRUSH_DURATION, "n/a"),
            result.get(DATA_LAST_BRUSH_SCORE, "n/a"),
            record[:T1_C3352G_RECORD_SIZE].hex(),
        )
        return result

    except (IndexError, ValueError, OverflowError) as err:
        _LOGGER.debug(
            "Oclean C3385w0 record parse error: %s (raw: %s)",
            err,
            record[:T1_C3352G_RECORD_SIZE].hex() if len(record) >= T1_C3352G_RECORD_SIZE else record.hex(),
        )
        return {}


def parse_t1_c3352g_record(record: bytes) -> dict[str, Any]:
    """Parse one 42-byte session record from the C3352g *B# multi-packet stream.

    Used by OCLEANY3P (Oclean X Pro Elite) when the coordinator reassembles the
    continuation packets from a ``0307 *B# count`` notification sequence with a
    non-zero year_base byte.  OCLEANY3M / OCLEANY3 use ``parse_t1_c3385w0_record``
    instead.

    Byte layout confirmed via APK source analysis of C3352g_fallback.java
    (second code path, bytesToIntBe trace):

      byte  0:   year_base (full_year − 2000)   ✓
      byte  1:   month (1-12)                   ✓
      byte  2:   day   (1-31)                   ✓
      byte  3:   hour  (0-23)                   ✓
      byte  4:   minute (0-59)                  ✓
      byte  5:   second (0-59)                  ✓
      byte  6:   pNum (brush-scheme ID)         ✓
      bytes 7-8: duration BE uint16 (seconds)   ✓
      bytes 9-10: validDuration BE              ✓
      bytes 11-15: pressureRatio[0..4]          ✓ (5 pressure-bucket counts)
      byte 16:   unused                         ✓
      byte 17:   (unknown)
      byte 18:   unused                         ✓
      byte 19:   gestureCode                    ✓ (= gestureArray[0])
      bytes 19-30: gestureArray[0..11]          ✓ (12 gesture values)
      bytes 30-32: powerArray nibble source     ?
      byte 33:   score (0-100, 0xFF = absent)   ✓
      bytes 34-41: reserved                     ?

    NOTE: bytes 11-19 are **pressureRatio / gestureCode** data, NOT tooth-zone
    area coverage.  Per-zone area data arrives via separate 021f push
    notifications and is handled by _parse_brush_areas_y3p_response().

    All out-of-range values are silently omitted from the result.
    """
    if len(record) < T1_C3352G_RECORD_SIZE:
        _LOGGER.debug("Oclean C3352g record too short (%d bytes): %s", len(record), record.hex())
        return {}

    year_base = record[0]
    year = year_base + _YEAR_2000
    if year < _MIN_YEAR:
        _LOGGER.debug(
            "Oclean C3352g record: implausible year_base 0x%02x (year %d < %d), raw: %s",
            year_base,
            year,
            _MIN_YEAR,
            record[:T1_C3352G_RECORD_SIZE].hex(),
        )
        return {}

    try:
        month = record[1]
        day = record[2]
        hour = record[3]
        minute = record[4]
        second = record[5]

        device_dt = datetime.datetime(year, month, day, hour, minute, second)

        timestamp_s = int(time.mktime(device_dt.timetuple()))
        result: dict[str, Any] = {DATA_LAST_BRUSH_TIME: timestamp_s}

        # pNum at byte 6 (uncertain offset – positional from C3385w0)
        result[DATA_LAST_BRUSH_PNUM] = int(record[6])

        # Duration at bytes 7-8 BE (uncertain offset)
        duration_s = (record[7] << 8) | record[8]
        if duration_s > 0:
            result[DATA_LAST_BRUSH_DURATION] = duration_s

        # Score at byte 33 (confirmed APK: C3352g_fallback.java r57=byte[33])
        score = record[33]
        if 0 < score <= 100:
            result[DATA_LAST_BRUSH_SCORE] = score

        # pressureRatio: bytes 11-15 (5 pressure-bucket counts, confirmed APK)
        # gestureCode:   byte 19 (confirmed APK: r15=byte[19], JSON key "gestureCode")
        # gestureArray:  bytes 19-30 (12 values, gestureCode is element 0)
        # powerArray:    nibbles from bytes 30-32
        # NOTE: bytes 11-19 are pressure/gesture data, NOT tooth-zone areas.
        #       Area data comes from 021f push notifications only.
        result[DATA_LAST_BRUSH_GESTURE_CODE] = int(record[19])
        result[DATA_LAST_BRUSH_PRESSURE_RATIO] = list(record[11:16])
        result[DATA_LAST_BRUSH_GESTURE_ARRAY] = list(record[19:31])
        result[DATA_LAST_BRUSH_POWER_ARRAY] = (
            _extract_nibbles(record[30]) + _extract_nibbles(record[31]) + _extract_nibbles(record[32])
        )
        _LOGGER.debug("Oclean C3352g record point=%d (raw byte 34, APK: not used)", record[34])
        _LOGGER.debug(
            "Oclean C3352g record research: gestureCode=%d gestureArray=%s powerArray=%s",
            result[DATA_LAST_BRUSH_GESTURE_CODE],
            result[DATA_LAST_BRUSH_GESTURE_ARRAY],
            result[DATA_LAST_BRUSH_POWER_ARRAY],
        )

        _LOGGER.debug(
            "Oclean C3352g record parsed: ts=%d pNum=%d duration=%s score=%s gestureCode=%d (raw: %s)",
            timestamp_s,
            result[DATA_LAST_BRUSH_PNUM],
            result.get(DATA_LAST_BRUSH_DURATION, "n/a"),
            result.get(DATA_LAST_BRUSH_SCORE, "n/a"),
            result[DATA_LAST_BRUSH_GESTURE_CODE],
            record[:T1_C3352G_RECORD_SIZE].hex(),
        )
        return result

    except (IndexError, ValueError, OverflowError) as err:
        _LOGGER.debug(
            "Oclean C3352g record parse error: %s (raw: %s)",
            err,
            record[:T1_C3352G_RECORD_SIZE].hex() if len(record) >= T1_C3352G_RECORD_SIZE else record.hex(),
        )
        return {}


def parse_y3p_stream_record(record: bytes) -> dict[str, Any]:
    """Parse one 42-byte session record from the OCLEANY3P *B# stream.

    OCLEANY3P uses the same *B# multi-packet reassembly as OCLEANY3, but encodes
    byte 0 as 0x00 (no year stored on device).  The year is inferred from the
    current wall clock, stepping back by one year if the resulting datetime lies
    in the future.

    Byte layout (confirmed 2026-03-15 from log analysis of issue #49, sw=1.0.0.41):
      byte  0:   0x00 – no year encoded
      byte  1:   month (1-12)
      byte  2:   day   (1-31)
      byte  3:   hour  (0-23)
      byte  4:   minute (0-59)
      byte  5:   second (0-59)
      bytes 7-8:  session duration, big-endian uint16 (seconds)
      bytes 21-28: 8 tooth-area pressure values (BrushAreaType order)
      byte  33:  brushing score (0-100; 0xFF = absent)
    """
    if len(record) < T1_C3352G_RECORD_SIZE:
        _LOGGER.debug("Oclean Y3P stream record too short (%d bytes): %s", len(record), record.hex())
        return {}

    try:
        month = record[1]
        day = record[2]
        hour = record[3]
        minute = record[4]
        second = record[5]

        if month == 0:
            _LOGGER.debug(
                "Oclean Y3P stream record: device clock not synced (month=0), "
                "sync via the Oclean app to fix timestamps (raw: %s)",
                record[:T1_C3352G_RECORD_SIZE].hex(),
            )
            return {}

        now = datetime.datetime.now()
        year = now.year
        device_dt = datetime.datetime(year, month, day, hour, minute, second)
        if device_dt > now:
            year -= 1
            device_dt = datetime.datetime(year, month, day, hour, minute, second)

        timestamp_s = int(time.mktime(device_dt.timetuple()))
        result: dict[str, Any] = {DATA_LAST_BRUSH_TIME: timestamp_s}

        duration_s = (record[7] << 8) | record[8]
        if duration_s > 0:
            result[DATA_LAST_BRUSH_DURATION] = duration_s

        area_bytes = bytes(record[21:29])
        area_dict, _zones_cleaned, avg_pressure, coverage_pct = _build_area_stats(area_bytes)
        if any(v > 0 for v in area_bytes):
            result[DATA_LAST_BRUSH_AREAS] = area_dict
            result[DATA_LAST_BRUSH_PRESSURE] = avg_pressure
            result[DATA_LAST_BRUSH_COVERAGE] = coverage_pct

        score = record[33]
        if 0 < score <= 100:
            result[DATA_LAST_BRUSH_SCORE] = score

        # gestureCode / pressureRatio / gestureArray / powerArray (APK: C3385w0_fallback)
        result[DATA_LAST_BRUSH_GESTURE_CODE] = int(record[14])
        result[DATA_LAST_BRUSH_PRESSURE_RATIO] = list(record[11:16])
        result[DATA_LAST_BRUSH_GESTURE_ARRAY] = list(record[18:31])
        result[DATA_LAST_BRUSH_POWER_ARRAY] = (
            _extract_nibbles(record[30]) + _extract_nibbles(record[31]) + _extract_nibbles(record[32])
        )
        _LOGGER.debug("Oclean Y3P stream record point=%d (raw byte 34, APK: not used)", record[34])
        _LOGGER.debug(
            "Oclean Y3P stream record research: gestureCode=%d gestureArray=%s powerArray=%s",
            result[DATA_LAST_BRUSH_GESTURE_CODE],
            result[DATA_LAST_BRUSH_GESTURE_ARRAY],
            result[DATA_LAST_BRUSH_POWER_ARRAY],
        )

        _LOGGER.debug(
            "Oclean Y3P stream record: ts=%d duration=%s score=%s gestureCode=%d (raw: %s)",
            timestamp_s,
            result.get(DATA_LAST_BRUSH_DURATION, "n/a"),
            result.get(DATA_LAST_BRUSH_SCORE, "n/a"),
            result[DATA_LAST_BRUSH_GESTURE_CODE],
            record[:T1_C3352G_RECORD_SIZE].hex(),
        )
        return result

    except (IndexError, ValueError, OverflowError) as err:
        _LOGGER.debug("Oclean Y3P stream record parse error: %s (raw: %s)", err, record.hex())
        return {}


def _parse_t1_ocleanx20_inline(payload: bytes) -> dict[str, Any]:
    """Parse OCLEANX20 extended-offset inline 0307 payload.

    session_count==0 AND year_byte==0: 4-byte extended header at bytes 5–8 shifts
    the session record to offset 9. Byte 8 is unconfirmed (observed 0x8d).
    Confirmed via debug logs 2026-03-09 (issue #37).
    """
    if len(payload) < 18:
        return {}
    try:
        device_dt = _device_datetime(payload[9], payload[10], payload[11], payload[12], payload[13], payload[14])
        timestamp_s = int(time.mktime(device_dt.timetuple()))
        result: dict[str, Any] = {
            DATA_LAST_BRUSH_TIME: timestamp_s,
            DATA_LAST_BRUSH_PNUM: int(payload[15]),
        }
        duration = (payload[16] << 8) | payload[17]
        if duration > 0:
            result[DATA_LAST_BRUSH_DURATION] = duration
        _LOGGER.debug(
            "Oclean 0307 extended-offset inline: ts=%d pNum=%d duration=%s s byte8=0x%02x (raw: %s)",
            timestamp_s,
            result[DATA_LAST_BRUSH_PNUM],
            result.get(DATA_LAST_BRUSH_DURATION, "n/a"),
            payload[8],
            payload.hex(),
        )
        return result
    except (IndexError, ValueError, OverflowError) as err:
        _LOGGER.debug(
            "Oclean 0307 extended-offset inline parse failed: %s (raw: %s)",
            err,
            payload.hex(),
        )
        return {}


def _parse_info_t1_response(payload: bytes, **kwargs: Any) -> dict[str, Any]:
    """Parse a 0307 Type-1 running-data push payload (Oclean X / OCLEANY3M).

    The device responds in several modes depending on model and session state:

    **Inline mode** (session_count == 0, year_byte != 0, payload = 18 bytes):
      Header: "*B#" + 0x0000 + first 13 bytes of the most-recent m18f record.
      Score is NOT included (record truncated at byte 12).

    **Paginated mode** (session_count > 0, year_byte != 0, payload = 5 + N×42 bytes):
      Header: "*B#" + RecordCount[2B BE] + N × 42-byte m18f records.
      Full m18f records include score (byte 33), area pressures (bytes 11-16, 18-19).
      Multi-packet reassembly (C5733b.m8524e) handled by the BLE layer; the HA
      integration receives the already-reassembled payload.

    **OCLEANY3P deferred push** (session_count > 0, year_byte == 0):
      Device defers data; will push via 021f / 5100 notifications instead.

    **OCLEANX20 extended-offset inline** (session_count == 0, year_byte == 0,
      payload = 18 bytes):
      Header: "*B#" + 0x0000 + 4-byte extended header (bytes 5–8) + session record.
      Session record starts at byte 9: year/month/day/hour/min/sec/pNum + duration[2B].
      Byte 8 is an unconfirmed device-specific byte (observed: 0x8d on OCLEANX20).
      Discriminated from OCLEANY3P by session_count == 0.

    APK source: AbstractC0002b.m18f / C3385w0_fallback.java / C5733b.m8524e
    (DeviceType OCLEANY3M, protocol 14, i12=1).
    """
    _LOGGER.debug("Oclean Type-1 INFO response raw: %s", payload.hex())

    if len(payload) < _T1_MIN_SIZE:
        _LOGGER.debug("Oclean Type-1 INFO: payload too short (%d bytes)", len(payload))
        return {}

    session_count = (payload[3] << 8) | payload[4]

    # Paginated mode: session_count > 0, year_byte != 0, AND full 42-byte record fits.
    # When payload is short (< 47 bytes), the device used inline format with count > 0
    # (observed: session_count=1 with 18-byte payload).  Fall through to inline parse.
    if session_count > 0 and payload[5] != 0 and len(payload) >= 5 + _T1_FULL_RECORD_SIZE:
        result = _parse_m18f_record(payload[5 : 5 + _T1_FULL_RECORD_SIZE])
        if result:
            _LOGGER.debug(
                "Oclean 0307 paginated: %d session(s), newest ts=%d score=%s",
                session_count,
                result.get(DATA_LAST_BRUSH_TIME, 0),
                result.get(DATA_LAST_BRUSH_SCORE, "n/a"),
            )
        return result

    if payload[5] == 0:
        if session_count > 0:
            # OCLEANY3P: device has sessions but defers data via 021f/5100 notifications.
            _LOGGER.debug(
                "Oclean 0307: year_byte=0x00, session_count=%d – "
                "device will push session data via 021f/5100 notifications (raw: %s)",
                session_count,
                payload.hex(),
            )
            return {}

        # OCLEANX20 extended-offset inline: session_count == 0, year_byte == 0.
        # The 4-byte extended header (bytes 5–8) shifts the session record to offset 9.
        return _parse_t1_ocleanx20_inline(payload)

    # Inline mode (session_count == 0): truncated 13-byte record, no score
    try:
        device_dt = _device_datetime(payload[5], payload[6], payload[7], payload[8], payload[9], payload[10])
        timestamp_s = int(time.mktime(device_dt.timetuple()))

        result = {
            DATA_LAST_BRUSH_TIME: timestamp_s,
            DATA_LAST_BRUSH_PNUM: int(payload[11]),
        }

        if len(payload) >= 14:
            result[DATA_LAST_BRUSH_DURATION] = (payload[12] << 8) | payload[13]

        _LOGGER.debug(
            "Oclean 0307 inline: ts=%d pNum=%d duration=%s s (raw: %s)",
            timestamp_s,
            result[DATA_LAST_BRUSH_PNUM],
            result.get(DATA_LAST_BRUSH_DURATION, "n/a"),
            payload.hex(),
        )
        return result

    except (IndexError, ValueError, OverflowError) as err:
        _LOGGER.debug("Oclean Type-1 record parse error: %s (raw: %s)", err, payload.hex())
        return {}


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
            DATA_LAST_BRUSH_TIME: timestamp_s,
            DATA_LAST_BRUSH_PRESSURE: pressure,
            DATA_BRUSH_HEAD_USAGE: blunt_teeth,
        }
        _LOGGER.debug(
            "Oclean 0308-simple parsed: %s (blunt_teeth=%d, pNum=%d, week=%d)",
            result,
            blunt_teeth,
            p_num,
            week,
        )

        # Log unknown bytes to help identify their purpose over time.
        _LOGGER.debug(
            "Oclean 0308-simple unknown bytes –"
            " b7=0x%02x week=%d (weekday? 0-indexed?)"
            " b8=0x%02x pNum=%d (brush-scheme ID, not mapped to name here)"
            " b9-13=%s (unknown – 5 bytes)",
            data[7],
            week,
            data[8],
            p_num,
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
    if len(data) < _EXT_MIN_SIZE:
        return {}

    try:
        device_dt = _device_datetime(data[2], data[3], data[4], data[5], data[6], data[7])
        p_num = int(data[8])
        duration = int.from_bytes(data[9:11], byteorder="big")
        # data[13:18]: 5 intermediate pressure zone values (not mapped to sensors)
        tz_offset_quarters = _parse_signed_byte(data[19])
        area_pressures = data[20:28]  # 8 tooth area pressure bytes
        score = int(data[28])
        timestamp_s = _build_utc_timestamp(device_dt, tz_offset_quarters)
        area_dict, zones_cleaned, avg_pressure, coverage_pct = _build_area_stats(area_pressures)

        result: dict[str, Any] = {
            DATA_LAST_BRUSH_TIME: timestamp_s,
            DATA_LAST_BRUSH_DURATION: duration,
            DATA_LAST_BRUSH_SCORE: max(0, min(100, score)),
            DATA_LAST_BRUSH_PRESSURE: avg_pressure,
            DATA_LAST_BRUSH_AREAS: area_dict,
            DATA_LAST_BRUSH_COVERAGE: coverage_pct,
            DATA_LAST_BRUSH_PNUM: p_num,
        }

        _LOGGER.debug(
            "Oclean extended running-data: ts=%d score=%d duration=%ds pNum=%d zones_cleaned=%d/8 coverage=%d%% avg_pressure=%d",
            timestamp_s,
            score,
            duration,
            p_num,
            zones_cleaned,
            coverage_pct,
            avg_pressure,
        )
        return result

    except (IndexError, ValueError, OverflowError) as err:
        _LOGGER.debug("Oclean extended record parse error: %s (raw: %s)", err, data.hex())
        return {}


def _parse_k3guide_response(payload: bytes, **kwargs: Any) -> dict[str, Any]:
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
    zone_name = TOOTH_AREA_NAMES[zone_id - 1] if 1 <= zone_id <= 8 else "stop"
    _LOGGER.debug(
        "Oclean K3GUIDE: liftUp=%d liftDown=%d rightUp=%d rightDown=%d zone=%d(%s) workingState=%d",
        payload[0],
        payload[1],
        payload[2],
        payload[3],
        zone_id,
        zone_name,
        payload[5],
    )
    # Real-time data only – not stored as persistent sensor state
    return {}


def _handle_device_info_ack(payload: bytes, **kwargs: Any) -> dict[str, Any]:
    """Handle 0202 device-info ACK (no sensor data payload)."""
    _LOGGER.debug("Oclean device-info ACK: %s", payload.hex())
    return {}


def _parse_device_settings_response(payload: bytes, **kwargs: Any) -> dict[str, Any]:
    """Parse 0302 device-settings response payload (bytes after the 2-byte type marker).

    Sent by device in response to CMD_QUERY_DEVICE_SETTINGS (030201).
    Byte layout (source: APK C3367n0.java / C3385w0_fallback.java):
      byte 0: batteryLevel (also available from 0303; redundant)
      byte 1: networkStatus (bool)
      byte 2: raiseWake (bool)
      byte 3: voiceMainSwitch (bool)
      byte 4: bindState (bool)
      byte 5: modeNum – active brushing mode number (device-family-specific)
      byte 6: brushSongSwitch (bool)
      byte 7: unknown
      byte 8: overCross (bool)
      bytes 9-10: unknown (2-byte value)
      byte 11: deviceTheme
      bytes 12-15: unknown
      byte 16: year (+ 2000) – device clock
      bytes 17-21: month/day/hour/minute/second
      byte 22: unknown
      byte 23: areaRemind (bool)
      byte 24: timezone offset
      bytes 25-27: headMaxTimeLong (2-byte BE; unit TBD)
      bytes 27-29: headUsedTimeLong (2-byte BE; unit TBD)
      bytes 29-31: headUsedDays (2-byte BE; calendar days since brush-head reset)
      byte 31: headUsedTimes (session count since brush-head reset) / deviceLanguage
    """
    _LOGGER.debug("Oclean 0302 device-settings raw: %s  len=%d", payload.hex(), len(payload))
    for i, b in enumerate(payload):
        _LOGGER.debug("  0302[%02d] = 0x%02X  (%3d)", i, b, b)

    result: dict[str, Any] = {}
    if len(payload) < 6:
        _LOGGER.debug("Oclean device-settings response too short (%d < 6)", len(payload))
        return result

    result[DATA_BRUSH_MODE] = int(payload[5])

    if len(payload) >= 32:
        head_max = int.from_bytes(payload[25:27], "big")
        head_used = int.from_bytes(payload[27:29], "big")
        head_days = int.from_bytes(payload[29:31], "big")
        head_times = payload[31]
        _LOGGER.debug(
            "Oclean 0302 brush-head counters –"
            " headMaxTimeLong=%d (0x%04x, unit TBD)"
            " headUsedTimeLong=%d (0x%04x, unit TBD)"
            " headUsedDays=%d"
            " headUsedTimes=%d",
            head_max,
            head_max,
            head_used,
            head_used,
            head_days,
            head_times,
        )
        result[DATA_BRUSH_HEAD_USAGE] = head_times
        result[DATA_BRUSH_HEAD_DAYS] = head_days

    _LOGGER.debug("Oclean device-settings parsed: %s", result)
    return result


def _parse_score_t1_response(payload: bytes, **kwargs: Any) -> dict[str, Any]:
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
    return {DATA_LAST_BRUSH_SCORE: score_clamped}


def _parse_session_meta_t1_response(payload: bytes, **kwargs: Any) -> dict[str, Any]:
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
            payload[7],
            payload[8],
            payload[9],
            payload[10],
            payload[11],
            payload[12],
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
        result: dict[str, Any] = {DATA_LAST_BRUSH_TIME: timestamp_s}
        if duration > 0 and duration != 0xFF:
            result[DATA_LAST_BRUSH_DURATION] = duration
        return result
    except (IndexError, ValueError, OverflowError) as err:
        _LOGGER.debug("Oclean 5a00 parse error: %s (raw: %s)", err, payload.hex())
        return {}


def _parse_brush_areas_t1_response(payload: bytes, *, dental_cast: int = 8, **kwargs: Any) -> dict[str, Any]:
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
    area_end = 6 + dental_cast  # 14 for 8-zone, 18 for 12-zone
    if len(payload) < area_end:
        _LOGGER.debug("Oclean 2604 too short (%d bytes, need %d): %s", len(payload), area_end, payload.hex())
        return {}

    area_pressures = payload[6:area_end]
    area_dict, zones_cleaned, avg_pressure, coverage_pct = _build_area_stats(area_pressures)

    result: dict[str, Any] = {
        DATA_LAST_BRUSH_AREAS: area_dict,
        DATA_LAST_BRUSH_PRESSURE: avg_pressure,
        DATA_LAST_BRUSH_COVERAGE: coverage_pct,
    }

    _LOGGER.debug(
        "Oclean 2604 areas: %s zones_cleaned=%d/%d coverage=%d%% avg_pressure=%d (raw: %s)",
        area_dict,
        zones_cleaned,
        dental_cast,
        coverage_pct,
        avg_pressure,
        payload.hex(),
    )
    return result


def _parse_0314_response(payload: bytes, **kwargs: Any) -> dict[str, Any]:
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


def _log_5400_response(payload: bytes, **kwargs: Any) -> dict[str, Any]:
    """Log all bytes of an 0x5400 push notification for empirical protocol analysis.

    This notification type is not present in the Oclean APK dispatch table –
    it appears to be emitted by newer Oclean X firmware after a brushing session.
    The byte layout is unknown; this handler captures the full payload so that
    field positions can be confirmed by comparing with known session values
    (score, area pressures) visible in the Oclean app.

    Once the layout is confirmed, replace this handler with a real parser.

    Observed raw (2026-02-24, Oclean X / OCLEANY3M, single data point):
      54 00  00 00 0f 00 08 23 17 22 08 00 11 11 07 0f 0f 11 00 00
             ^--- payload starts here (18 bytes)

    Candidate hypotheses (to be verified against app data):
      - bytes  0- 1: header / flags
      - byte   2:    unknown (0x0f = 15; count? duration LSB?)
      - byte   3:    unknown (0x00)
      - bytes  4-11: 8 tooth-area pressure values (8, 35, 23, 34, 8, 0, 17, 17)?
      - bytes 12-17: additional data (7, 15, 15, 17, 0, 0)

    Alternative: same offset as 2604 (bytes 6-13 for areas):
      - bytes  6-13: 23, 34, 8, 0, 17, 17, 7, 15
    """
    _LOGGER.debug("Oclean 5400 raw: %s  len=%d", payload.hex(), len(payload))
    for i, b in enumerate(payload):
        _LOGGER.debug("  5400[%02d] = 0x%02X  (%3d)", i, b, b)

    # Log the two most likely area-byte windows so they appear side-by-side in
    # the log and can be matched against the Oclean app's per-area values.
    if len(payload) >= 12:
        window_a = payload[4:12]  # hypothesis A: bytes 4-11
        _LOGGER.debug(
            "  5400 area-candidate A (bytes 4-11): %s → %s",
            window_a.hex(),
            list(window_a),
        )
    if len(payload) >= 14:
        window_b = payload[6:14]  # hypothesis B: bytes 6-13 (same offset as 2604)
        _LOGGER.debug(
            "  5400 area-candidate B (bytes 6-13): %s → %s",
            window_b.hex(),
            list(window_b),
        )

    return {}


def _log_4b00_response(payload: bytes, **kwargs: Any) -> dict[str, Any]:
    """Log all bytes of a 0x4B00 push notification for empirical protocol analysis.

    Observed on OCLEANY3MH (issue #19, 2026-03-10, sw=1.0.0.3).
    Appears after the 0307 session response; may be a session index or list header.

    Observed raw (single data point):
      4b 00  00 00 1f 00 00 00 00 00 28 03 42 33 07 00 03 01 00 00
             ^--- payload starts here (18 bytes)

    Candidate hypotheses (unconfirmed):
      - byte   2: unknown (0x1f = 31; record count? buffer size?)
      - bytes  8- 9: unknown (0x28 0x03 = 40, 3)
      - bytes 10-12: unknown (0x42 0x33 0x07; possible internal address or CRC)
      - byte  14: unknown (0x03)
      - byte  15: unknown (0x01; could be page / record index)
    """
    _LOGGER.debug("Oclean 4b00 raw: %s  len=%d", payload.hex(), len(payload))
    for i, b in enumerate(payload):
        _LOGGER.debug("  4b00[%02d] = 0x%02X  (%3d)", i, b, b)
    return {}


def _parse_brush_areas_y3p_response(payload: bytes, *, dental_cast: int = 8, **kwargs: Any) -> dict[str, Any]:
    """Parse 021f per-tooth-area pressure data (OCLEANY3P / Oclean X Pro Elite).

    Analogous to 2604 on OCLEANY3M.  Byte layout confirmed 2026-03-07 from
    APK C3352g fallback analysis and cross-referenced against log data.

    Observed raw (2026-02-24, OCLEANY3P sw=1.0.0.41):
      021f  00 00 0f 00 0f 21 11 23 01 0d 12 0f 01 0f 0f 12 00 00
            ^--- payload (18 bytes)

    Byte layout (same offsets as 2604):
      bytes 0-1: unknown header (0x00 0x00)
      byte  2:   unknown (0x0f = 15; possibly session counter)
      byte  3:   unknown (0x00)
      byte  4:   unknown (0x0f = 15)
      byte  5:   unknown (0x21 = 33)
      bytes 6-13: 8 tooth-area pressure values, BrushAreaType order
                  (AREA_LIFT_UP_OUT … AREA_RIGHT_DOWN_IN)
      bytes 14+:  additional zone data
    """
    area_end = 6 + dental_cast
    if len(payload) < area_end:
        _LOGGER.debug("Oclean 021f too short (%d bytes, need %d): %s", len(payload), area_end, payload.hex())
        return {}

    area_pressures = payload[6:area_end]
    area_dict, zones_cleaned, avg_pressure, coverage_pct = _build_area_stats(area_pressures)

    result: dict[str, Any] = {
        DATA_LAST_BRUSH_AREAS: area_dict,
        DATA_LAST_BRUSH_PRESSURE: avg_pressure,
        DATA_LAST_BRUSH_COVERAGE: coverage_pct,
    }

    _LOGGER.debug(
        "Oclean 021f areas: %s zones_cleaned=%d/%d coverage=%d%% avg_pressure=%d (raw: %s)",
        area_dict,
        zones_cleaned,
        dental_cast,
        coverage_pct,
        avg_pressure,
        payload.hex(),
    )
    return result


def _parse_session_meta_y3p_response(payload: bytes, **kwargs: Any) -> dict[str, Any]:
    """Parse 5100 session-metadata push (OCLEANY3P / Oclean X Pro Elite).

    Analogous to 5a00 on OCLEANY3M but without an explicit year byte.
    The year is inferred from the current year, walking back by one year if
    the reconstructed datetime would lie in the future.

    Observed raw (2026-02-24, OCLEANY3P sw=1.0.0.41):
      5100  ff ff ff ff ff ff ff 00 08 0d 00 38 32 00 00 78 00 78
            ^--- payload (18 bytes)

    Byte layout (confirmed 2026-03-07):
      bytes 0-6:  7 × 0xFF = empty slots (no previous sessions stored)
      byte  7:    0x00 – type/version indicator (not year)
      byte  8:    month (1-12)
      byte  9:    day   (1-31)
      byte  10:   hour  (0-23)
      byte  11:   minute (0-59)
      byte  12:   second (0-59)
      bytes 13-14: unknown (observed: 0x00 0x00)
      byte  15:   session duration in seconds (observed: 0x78 = 120 s)
      byte  16:   unknown
      byte  17:   session duration duplicate
    """
    if len(payload) < 16:
        _LOGGER.debug("Oclean 5100 too short (%d bytes): %s", len(payload), payload.hex())
        return {}

    try:
        month = payload[8]
        day = payload[9]
        hour = payload[10]
        minute = payload[11]
        second = payload[12]

        # Year is not encoded; infer from current year, stepping back if the
        # result would be in the future (e.g., session in August, poll in February).
        now = datetime.datetime.now()
        year = now.year
        device_dt = datetime.datetime(year, month, day, hour, minute, second)
        if device_dt > now:
            year -= 1
            device_dt = datetime.datetime(year, month, day, hour, minute, second)

        timestamp_s = int(time.mktime(device_dt.timetuple()))
        duration = payload[15]

        _LOGGER.debug(
            "Oclean 5100 session: date=%s ts=%d duration=%ds b7=0x%02x (raw: %s)",
            device_dt.strftime("%Y-%m-%d %H:%M:%S"),
            timestamp_s,
            duration,
            payload[7],
            payload.hex(),
        )
        result: dict[str, Any] = {DATA_LAST_BRUSH_TIME: timestamp_s}
        if duration > 0 and duration != 0xFF:
            result[DATA_LAST_BRUSH_DURATION] = duration
        return result
    except (IndexError, ValueError, OverflowError) as err:
        _LOGGER.debug("Oclean 5100 parse error: %s (raw: %s)", err, payload.hex())
        return {}


# Strategy registry: 2-byte response-type prefix → handler function.
# To add support for a new notification type, add one entry here.
_PARSERS: dict[bytes, Callable[..., dict[str, Any]]] = {
    RESP_STATE: _parse_state_response,
    RESP_DEVICE_SETTINGS: _parse_device_settings_response,
    RESP_INFO: _parse_info_response,
    RESP_INFO_T1: _parse_info_t1_response,
    RESP_DEVICE_INFO: _handle_device_info_ack,
    RESP_K3GUIDE: _parse_k3guide_response,
    RESP_EXTENDED_T1: _parse_0314_response,
    RESP_SCORE_T1: _parse_score_t1_response,
    RESP_SESSION_META_T1: _parse_session_meta_t1_response,
    RESP_BRUSH_AREAS_T1: _parse_brush_areas_t1_response,
    RESP_UNKNOWN_5400: _log_5400_response,
    RESP_UNKNOWN_4B00: _log_4b00_response,
    RESP_BRUSH_AREAS_Y3P: _parse_brush_areas_y3p_response,
    RESP_SESSION_META_Y3P: _parse_session_meta_y3p_response,
}


# Public set of all 2-byte prefixes recognised by parse_notification().
# Available for external callers (e.g. tests, diagnostic tools) that need to
# inspect which notification types this parser handles.
KNOWN_NOTIFICATION_PREFIXES: frozenset[bytes] = frozenset(_PARSERS.keys())


_JSON_KEY_MAP: tuple[tuple[str, tuple[str, ...], bool], ...] = (
    # (result_key, candidate_keys, cast_to_int)
    (DATA_LAST_BRUSH_SCORE, ("score", "brushScore", "brush_score", "totalScore"), True),
    (DATA_LAST_BRUSH_DURATION, ("duration", "brushDuration", "brush_duration", "time"), True),
    (DATA_LAST_BRUSH_PRESSURE, ("pressure", "avgPressure", "avg_pressure"), True),
    (DATA_LAST_BRUSH_TIME, ("timestamp", "endTime", "end_time", "brushTime"), False),
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
