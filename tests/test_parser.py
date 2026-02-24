"""Unit tests for parser.py – no Home Assistant required."""
from __future__ import annotations

import calendar
import datetime
import json

from custom_components.oclean_ble.parser import (
    _MIN_YEAR,
    _device_datetime,
    _map_json_brush_data,
    _parse_brush_areas_t1_response,
    _parse_extended_running_data_record,
    _parse_info_response,
    _parse_info_t1_response,
    _parse_k3guide_response,
    _parse_running_data_record,
    _parse_score_t1_response,
    _parse_session_meta_t1_response,
    _parse_state_response,
    parse_battery,
    parse_notification,
)
from custom_components.oclean_ble.const import (
    RESP_BRUSH_AREAS_T1,
    RESP_INFO,
    RESP_INFO_T1,
    RESP_K3GUIDE,
    RESP_SCORE_T1,
    RESP_SESSION_META_T1,
    TOOTH_AREA_NAMES,
)


# ---------------------------------------------------------------------------
# Helper: build a syntactically valid 18-byte running-data record
# ---------------------------------------------------------------------------

def _make_record(
    *,
    year: int = 2024,
    month: int = 3,
    day: int = 15,
    hour: int = 8,
    minute: int = 30,
    second: int = 0,
    tz_quarters: int = 32,   # +32 × 15 min = UTC+8
    week: int = 11,
    p_num: int = 1,
    blunt_teeth: int = 5,
    pressure_raw: int = 600,  # → 600/300 = 2.0
    padding: int = 0,         # value for unknown bytes 9–13
    extra: bytes = b"",       # appended after byte 17
) -> bytes:
    """Build a minimal 18-byte (+ extra) running-data record."""
    record = bytearray(18)
    record[0] = year - 2000
    record[1] = month
    record[2] = day
    record[3] = hour
    record[4] = minute
    record[5] = second
    record[6] = tz_quarters % 256          # signed → unsigned byte storage
    record[7] = week
    record[8] = p_num
    record[9:14] = bytes([padding] * 5)    # unknown bytes
    record[14:16] = blunt_teeth.to_bytes(2, "little")
    record[16:18] = pressure_raw.to_bytes(2, "little")
    return bytes(record) + extra


def _expected_utc_ts(year, month, day, hour, minute, second, tz_quarters) -> int:
    """Compute the expected UTC unix timestamp using the same logic as the parser."""
    tz_offset_quarters = tz_quarters if tz_quarters < 128 else tz_quarters - 256
    tz_offset_minutes = tz_offset_quarters * 15
    device_dt = datetime.datetime(year, month, day, hour, minute, second)
    utc_dt = device_dt - datetime.timedelta(minutes=tz_offset_minutes)
    return calendar.timegm(utc_dt.timetuple())


# ---------------------------------------------------------------------------
# _device_datetime helper
# ---------------------------------------------------------------------------

class TestDeviceDatetime:
    """Tests for _device_datetime() – the shared year-2000 datetime builder."""

    # --- Happy path ---

    def test_returns_correct_datetime(self):
        dt = _device_datetime(24, 3, 15, 8, 30, 0)
        assert dt == datetime.datetime(2024, 3, 15, 8, 30, 0)

    def test_year_offset_applied(self):
        """year_byte + 2000 must equal the returned year."""
        dt = _device_datetime(26, 1, 1, 0, 0, 0)
        assert dt.year == 2026

    def test_all_fields_preserved(self):
        dt = _device_datetime(23, 11, 7, 22, 59, 45)
        assert (dt.year, dt.month, dt.day, dt.hour, dt.minute, dt.second) == (
            2023, 11, 7, 22, 59, 45
        )

    def test_leap_day_accepted(self):
        """Feb 29 in a leap year must not raise."""
        dt = _device_datetime(24, 2, 29, 12, 0, 0)  # 2024 is a leap year
        assert dt.day == 29

    def test_min_year_boundary_accepted(self):
        """_MIN_YEAR itself must be accepted."""
        dt = _device_datetime(_MIN_YEAR - 2000, 6, 1, 0, 0, 0)
        assert dt.year == _MIN_YEAR

    def test_year_99_accepted(self):
        """year_byte=99 → year 2099 is valid."""
        dt = _device_datetime(99, 1, 1, 0, 0, 0)
        assert dt.year == 2099

    # --- Year-validation boundary ---

    def test_year_below_min_raises(self):
        """Any year below _MIN_YEAR must raise ValueError."""
        import pytest
        with pytest.raises(ValueError, match="implausible year"):
            _device_datetime(_MIN_YEAR - 2000 - 1, 1, 1, 0, 0, 0)

    def test_year_zero_raises(self):
        """year_byte=0 → year 2000 → rejected."""
        import pytest
        with pytest.raises(ValueError):
            _device_datetime(0, 1, 1, 0, 0, 0)

    def test_year_14_raises(self):
        """year_byte=14 → year 2014 → rejected."""
        import pytest
        with pytest.raises(ValueError):
            _device_datetime(14, 6, 15, 12, 0, 0)

    # --- Invalid dates (delegated to datetime constructor) ---

    def test_month_zero_raises(self):
        import pytest
        with pytest.raises(ValueError):
            _device_datetime(24, 0, 1, 0, 0, 0)

    def test_month_13_raises(self):
        import pytest
        with pytest.raises(ValueError):
            _device_datetime(24, 13, 1, 0, 0, 0)

    def test_day_zero_raises(self):
        import pytest
        with pytest.raises(ValueError):
            _device_datetime(24, 1, 0, 0, 0, 0)

    def test_day_32_raises(self):
        import pytest
        with pytest.raises(ValueError):
            _device_datetime(24, 1, 32, 0, 0, 0)

    def test_hour_24_raises(self):
        import pytest
        with pytest.raises(ValueError):
            _device_datetime(24, 1, 1, 24, 0, 0)

    def test_minute_60_raises(self):
        import pytest
        with pytest.raises(ValueError):
            _device_datetime(24, 1, 1, 0, 60, 0)

    def test_second_60_raises(self):
        import pytest
        with pytest.raises(ValueError):
            _device_datetime(24, 1, 1, 0, 0, 60)

    def test_leap_day_in_non_leap_year_raises(self):
        """Feb 29 in a non-leap year must raise."""
        import pytest
        with pytest.raises(ValueError):
            _device_datetime(23, 2, 29, 0, 0, 0)  # 2023 is not a leap year


# ---------------------------------------------------------------------------
# parse_battery
# ---------------------------------------------------------------------------

class TestParseBattery:
    def test_valid_value(self):
        assert parse_battery(bytes([75])) == 75

    def test_zero(self):
        assert parse_battery(bytes([0])) == 0

    def test_full(self):
        assert parse_battery(bytes([100])) == 100

    def test_empty_bytes(self):
        assert parse_battery(b"") is None

    def test_over_100_returns_none(self):
        assert parse_battery(bytes([101])) is None
        assert parse_battery(bytes([255])) is None

    def test_ignores_trailing_bytes(self):
        # Standard BLE battery char is 1 byte; extra bytes should be ignored
        assert parse_battery(bytes([80, 0, 0])) == 80


# ---------------------------------------------------------------------------
# parse_notification – routing
# ---------------------------------------------------------------------------

class TestParseNotificationRouting:
    def test_empty_returns_empty(self):
        assert parse_notification(b"") == {}

    def test_single_byte_returns_empty(self):
        assert parse_notification(bytes([0x09])) == {}

    def test_state_response_routed(self):
        # 03 03 + observed real payload (Oclean X, idle): 02 0e 4b 1d 00 00
        # byte 3 = 0x1d = 29 → battery
        data = bytes([0x03, 0x03, 0x02, 0x0e, 0x4b, 0x1d, 0x00, 0x00])
        result = parse_notification(data)
        assert result["battery"] == 29
        assert "is_brushing" not in result

    def test_state_response_routed_minimal(self):
        # 03 03 + short payload (< 4 bytes) → {} (no battery field available)
        data = bytes([0x03, 0x03, 0x02])
        result = parse_notification(data)
        assert result == {}

    def test_info_response_routed(self):
        # 03 08 → info response with short payload → {}
        data = bytes([0x03, 0x08, 0xAA, 0xBB])
        result = parse_notification(data)
        assert result == {}

    def test_device_info_ack_routed(self):
        # 02 02 4F 4B → device-info ACK ("OK"), no sensor data
        data = bytes([0x02, 0x02, 0x4F, 0x4B])
        result = parse_notification(data)
        assert result == {}

    def test_json_fallback(self):
        payload = json.dumps({"score": 88}).encode()
        result = parse_notification(payload)
        assert result.get("last_brush_score") == 88

    def test_unknown_binary_returns_empty(self):
        data = bytes([0xFF, 0xFF, 0x01, 0x02, 0x03])
        result = parse_notification(data)
        assert result == {}


# ---------------------------------------------------------------------------
# _parse_state_response
# ---------------------------------------------------------------------------

class TestParseStateResponse:
    def test_battery_extracted_from_byte3(self):
        # Observed on Oclean X: byte 3 = battery %.
        # Payload after 0303 prefix: 02 0e XX battery 00 00
        payload = bytes([0x02, 0x0e, 0x4b, 0x1d, 0x00, 0x00])  # battery = 0x1d = 29
        result = _parse_state_response(payload)
        assert result["battery"] == 29

    def test_battery_extracted_various_values(self):
        payload = bytes([0x02, 0x0e, 0x39, 0x19, 0x00, 0x00])  # battery = 0x19 = 25
        assert _parse_state_response(payload)["battery"] == 25

    def test_battery_out_of_range_ignored(self):
        # Values > 100 are not a valid battery level
        payload = bytes([0x02, 0x0e, 0x00, 0x80, 0x00, 0x00])  # 0x80 = 128 → skip
        result = _parse_state_response(payload)
        assert "battery" not in result

    def test_no_is_brushing_in_result(self):
        # byte 0 on Oclean X is always 0x02 regardless of brushing state → not reported
        payload = bytes([0x02, 0x0e, 0x4b, 0x1d, 0x00, 0x00])
        result = _parse_state_response(payload)
        assert "is_brushing" not in result

    def test_short_payload_no_crash(self):
        # Payloads shorter than 4 bytes → no battery (byte 3 absent), no crash
        assert _parse_state_response(bytes([0x02])) == {}
        assert _parse_state_response(bytes([0x02, 0x0e, 0x39])) == {}

    def test_empty_payload_returns_empty(self):
        assert _parse_state_response(b"") == {}

    def test_exact_4_bytes_extracts_battery(self):
        payload = bytes([0x02, 0x0e, 0x38, 0x1b])  # battery = 0x1b = 27
        assert _parse_state_response(payload)["battery"] == 27


# ---------------------------------------------------------------------------
# _map_json_brush_data
# ---------------------------------------------------------------------------

class TestMapJsonBrushData:
    def test_camel_case_keys(self):
        data = {
            "brushScore": 75,
            "brushDuration": 120,
            "cleanScore": 90,
            "avgPressure": 42,
            "endTime": 1700000000,
        }
        result = _map_json_brush_data(data)
        assert result["last_brush_score"] == 75
        assert result["last_brush_duration"] == 120
        assert result["last_brush_pressure"] == 42
        assert result["last_brush_time"] == 1700000000

    def test_snake_case_keys(self):
        data = {
            "score": 60,
            "duration": 90,
            "clean": 80,
            "pressure": 100,
            "timestamp": 1700000001,
        }
        result = _map_json_brush_data(data)
        assert result["last_brush_score"] == 60
        assert result["last_brush_duration"] == 90
        assert result["last_brush_pressure"] == 100
        assert result["last_brush_time"] == 1700000001

    def test_unknown_keys_returns_empty(self):
        result = _map_json_brush_data({"foo": 1, "bar": 2})
        assert result == {}

    def test_partial_keys(self):
        result = _map_json_brush_data({"score": 55})
        assert result == {"last_brush_score": 55}
        assert "last_brush_duration" not in result

    def test_priority_first_match_wins(self):
        # "score" comes before "brushScore" in the search order
        data = {"score": 10, "brushScore": 99}
        result = _map_json_brush_data(data)
        assert result["last_brush_score"] == 10

    def test_string_score_cast_to_int(self):
        result = _map_json_brush_data({"score": "72"})
        assert result["last_brush_score"] == 72
        assert isinstance(result["last_brush_score"], int)


# ---------------------------------------------------------------------------
# _parse_running_data_record  (new binary parser from C3340b1.m5348m1)
# ---------------------------------------------------------------------------

class TestParseRunningDataRecord:

    # --- Happy path ---

    def test_utc_plus8_timestamp(self):
        """Device in UTC+8: local 08:30 → UTC 00:30."""
        record = _make_record(
            year=2024, month=3, day=15,
            hour=8, minute=30, second=0,
            tz_quarters=32,          # +32 × 15 min = +480 min = UTC+8
        )
        result = _parse_running_data_record(record)
        expected_ts = _expected_utc_ts(2024, 3, 15, 8, 30, 0, 32)
        assert result["last_brush_time"] == expected_ts

    def test_utc_minus5_timestamp(self):
        """Device in UTC-5: local 12:00 → UTC 17:00."""
        # -20 quarter-hours stored as 256 - 20 = 236
        record = _make_record(
            year=2024, month=3, day=15,
            hour=12, minute=0, second=0,
            tz_quarters=236,         # 236 - 256 = -20 → -300 min = UTC-5
        )
        result = _parse_running_data_record(record)
        expected_ts = _expected_utc_ts(2024, 3, 15, 12, 0, 0, 236)
        assert result["last_brush_time"] == expected_ts

    def test_utc_zero_timezone(self):
        """Device in UTC: no offset, timestamps are identical."""
        record = _make_record(
            year=2024, month=1, day=1,
            hour=0, minute=0, second=0,
            tz_quarters=0,
        )
        result = _parse_running_data_record(record)
        expected_ts = _expected_utc_ts(2024, 1, 1, 0, 0, 0, 0)
        assert result["last_brush_time"] == expected_ts

    def test_pressure_calculation(self):
        """pressure_raw / 300, rounded to 2 decimal places."""
        record = _make_record(pressure_raw=600)
        result = _parse_running_data_record(record)
        assert result["last_brush_pressure"] == 2.0

    def test_pressure_non_round(self):
        """Non-divisible raw value rounds to 2 decimals."""
        record = _make_record(pressure_raw=100)
        result = _parse_running_data_record(record)
        # 100 / 300 = 0.333...
        assert result["last_brush_pressure"] == round(100 / 300, 2)

    def test_pressure_zero(self):
        record = _make_record(pressure_raw=0)
        result = _parse_running_data_record(record)
        assert result["last_brush_pressure"] == 0.0

    def test_pressure_max_uint16(self):
        """65535 / 300 = 218.45."""
        record = _make_record(pressure_raw=65535)
        result = _parse_running_data_record(record)
        assert result["last_brush_pressure"] == round(65535 / 300, 2)

    def test_exact_18_bytes_accepted(self):
        """Exactly 18 bytes is the minimum valid record."""
        record = _make_record()
        assert len(record) == 18
        result = _parse_running_data_record(record)
        assert "last_brush_time" in result
        assert "last_brush_pressure" in result

    def test_extra_trailing_bytes_ignored(self):
        """Records longer than 18 bytes (unknown fields) are accepted."""
        record = _make_record(extra=bytes([0xDE, 0xAD, 0xBE, 0xEF]))
        assert len(record) == 22
        result = _parse_running_data_record(record)
        assert "last_brush_time" in result

    def test_returns_only_known_keys(self):
        """Only last_brush_time, last_brush_pressure, and brush_head_usage are returned."""
        result = _parse_running_data_record(_make_record())
        assert set(result.keys()) == {"last_brush_time", "last_brush_pressure", "brush_head_usage"}

    # --- Year / month boundary ---

    def test_year_before_2015_rejected(self):
        """Years before 2015 are invalid (no Oclean devices existed then)."""
        record = _make_record(year=2000, month=1, day=1)
        result = _parse_running_data_record(record)
        assert result == {}

    def test_year_boundary_2015_accepted(self):
        """Exactly 2015 is the minimum accepted year."""
        record = _make_record(year=2015, month=1, day=1)
        result = _parse_running_data_record(record)
        assert "last_brush_time" in result

    def test_year_boundary_2099(self):
        """byte 0 = 99 → year 2099."""
        record = _make_record(year=2099, month=12, day=31)
        result = _parse_running_data_record(record)
        assert result["last_brush_time"] is not None

    # --- Edge cases / error paths ---

    def test_too_short_returns_empty(self):
        """Payloads shorter than 18 bytes must return {}."""
        assert _parse_running_data_record(b"") == {}
        assert _parse_running_data_record(bytes(17)) == {}

    def test_17_bytes_returns_empty(self):
        assert _parse_running_data_record(bytes(17)) == {}

    def test_invalid_date_returns_empty(self):
        """An impossible date (month=13) triggers ValueError → returns {}."""
        record = _make_record(month=13)
        assert _parse_running_data_record(record) == {}

    def test_invalid_date_day_zero_returns_empty(self):
        """Day=0 is an impossible date."""
        record = _make_record(day=0)
        assert _parse_running_data_record(record) == {}

    def test_pressure_little_endian_byte_order(self):
        """Verify LE byte order: raw=0x0102 → bytes [0x02, 0x01] at positions 16–17."""
        record = bytearray(_make_record(pressure_raw=0))
        # Manually write 0x0102 in little-endian: low byte first
        record[16] = 0x02
        record[17] = 0x01
        result = _parse_running_data_record(bytes(record))
        # 0x0102 = 258
        assert result["last_brush_pressure"] == round(258 / 300, 2)

    def test_blunt_teeth_little_endian(self):
        """blunt_teeth uses LE: value 0x0200 = 512 → bytes [0x00, 0x02]."""
        record = bytearray(_make_record(blunt_teeth=0))
        record[14] = 0x00
        record[15] = 0x02
        # blunt_teeth = 512; not in output dict but must not cause a crash
        result = _parse_running_data_record(bytes(record))
        assert "last_brush_time" in result


# ---------------------------------------------------------------------------
# _parse_info_response  (wraps _parse_running_data_record)
# ---------------------------------------------------------------------------

class TestParseInfoResponse:

    def test_valid_record_returns_data(self):
        """A full 18-byte payload is parsed into brush data."""
        payload = _make_record(pressure_raw=300)   # → 1.0
        result = _parse_info_response(payload)
        assert result["last_brush_pressure"] == 1.0
        assert "last_brush_time" in result

    def test_too_short_payload_returns_empty(self):
        """Payload shorter than _RUNNING_DATA_MIN_RECORD_SIZE returns {}."""
        assert _parse_info_response(b"") == {}
        assert _parse_info_response(bytes(10)) == {}

    def test_exact_minimum_size_parsed(self):
        payload = _make_record()
        assert len(payload) == 18
        result = _parse_info_response(payload)
        assert result != {}

    def test_invalid_date_falls_through_to_empty(self):
        """Bad date in record → {} (no crash)."""
        result = _parse_info_response(_make_record(month=0))
        assert result == {}


# ---------------------------------------------------------------------------
# parse_notification → INFO routing (end-to-end through the full chain)
# ---------------------------------------------------------------------------

class TestParseNotificationInfoRouting:

    def test_info_notification_with_valid_record(self):
        """03 07 header + valid 18-byte record → brush data extracted."""
        payload = RESP_INFO + _make_record(pressure_raw=450)
        result = parse_notification(payload)
        assert result["last_brush_pressure"] == round(450 / 300, 2)
        assert "last_brush_time" in result

    def test_info_notification_too_short_record(self):
        """03 07 header + only 4 bytes payload → {} (too short for a record)."""
        payload = RESP_INFO + bytes([0xAA, 0xBB, 0xCC, 0xDD])
        result = parse_notification(payload)
        assert result == {}

    def test_info_notification_exact_minimum(self):
        """03 07 header + exactly 18 bytes → parsed."""
        payload = RESP_INFO + _make_record()
        result = parse_notification(payload)
        assert "last_brush_time" in result

    def test_info_notification_utc_timestamp_correctness(self):
        """Full notification round-trip preserves UTC conversion."""
        tz_quarters = 32   # UTC+8
        rec = _make_record(
            year=2025, month=6, day=21,
            hour=9, minute=0, second=0,
            tz_quarters=tz_quarters,
            pressure_raw=300,
        )
        result = parse_notification(RESP_INFO + rec)
        expected = _expected_utc_ts(2025, 6, 21, 9, 0, 0, tz_quarters)
        assert result["last_brush_time"] == expected


# ---------------------------------------------------------------------------
# Helpers for Type-1 (0307) records
# ---------------------------------------------------------------------------

def _make_t1_record(
    *,
    year: int = 2026,
    month: int = 2,
    day: int = 21,
    hour: int = 16,
    minute: int = 25,
    second: int = 31,
    pnum: int = 0,
    duration: int = 0,
    extra: bytes = b"\x02\x01",
) -> bytes:
    """Build a Type-1 (0307) running-data payload (14 bytes header + extra).

    Byte layout (confirmed via APK AbstractC0002b.m18f):
      bytes 0-2 : magic "*B#"  (0x2a 0x42 0x23)
      bytes 3-4 : session count 0x0000
      bytes 5-10: timestamp (year-2000, month, day, hour, minute, second)
      byte  11  : pNum (brush-scheme ID)
      bytes 12-13: duration in seconds (2-byte BE)
      extra     : bytes 14+ (validDuration, pressureArea, etc.)
    """
    header = bytearray(14)
    header[0:5] = b"\x2a\x42\x23\x00\x00"   # magic "*B#" + session count
    header[5] = year - 2000
    header[6] = month
    header[7] = day
    header[8] = hour
    header[9] = minute
    header[10] = second
    header[11] = pnum
    header[12] = (duration >> 8) & 0xFF
    header[13] = duration & 0xFF
    return bytes(header) + extra


def _expected_t1_ts(year, month, day, hour, minute, second) -> int:
    """Expected unix timestamp from Type-1 record (device local time → system local tz)."""
    import time
    return int(time.mktime(datetime.datetime(year, month, day, hour, minute, second).timetuple()))


# ---------------------------------------------------------------------------
# _parse_info_t1_response  (Type-1 / 0307 running-data)
# ---------------------------------------------------------------------------

class TestParseInfoT1Response:

    # --- Real observed payloads (ground truth) ---

    def test_real_session_7s(self):
        """Short brush: timestamp, pNum=231, duration=30 s extracted."""
        # raw: 03072a422300001a021510191fe7001e001e6400
        # payload[11]=0xe7=231 (pNum), payload[12:14]=0x001e=30 s (duration)
        payload = bytes.fromhex("2a422300001a021510191fe7001e001e6400")
        result = _parse_info_t1_response(payload)
        expected_ts = _expected_t1_ts(2026, 2, 21, 16, 25, 31)
        assert result["last_brush_time"] == expected_ts
        assert result["last_brush_pnum"] == 231
        assert result["last_brush_duration"] == 30
        assert "last_brush_score" not in result

    def test_real_session_2min(self):
        """2-min brush: timestamp, pNum=0, duration=120 s extracted."""
        # raw: 03072a422300001a0215102c1c00007800780201
        # payload[11]=0x00=0 (pNum), payload[12:14]=0x0078=120 s (duration)
        payload = bytes.fromhex("2a422300001a0215102c1c00007800780201")
        result = _parse_info_t1_response(payload)
        expected_ts = _expected_t1_ts(2026, 2, 21, 16, 44, 28)
        assert result["last_brush_time"] == expected_ts
        assert result["last_brush_pnum"] == 0
        assert result["last_brush_duration"] == 120
        assert "last_brush_score" not in result

    def test_real_session_150s(self):
        """150 s brush: timestamp, pNum=77, duration=150 s extracted."""
        # raw: 03072a422300001a02150f2a134d009600962704
        # payload[11]=0x4d=77 (pNum), payload[12:14]=0x0096=150 s (duration)
        payload = bytes.fromhex("2a422300001a02150f2a134d009600962704")
        result = _parse_info_t1_response(payload)
        assert result["last_brush_pnum"] == 77
        assert result["last_brush_duration"] == 150
        assert "last_brush_score" not in result

    def test_score_never_in_result(self):
        """Score is NOT returned by 0307; it comes from the 0000 notification."""
        rec = _make_t1_record()
        assert "last_brush_score" not in _parse_info_t1_response(rec)

    # --- Timestamp ---

    def test_timestamp_extracted_from_bytes_5_10(self):
        rec = _make_t1_record(year=2025, month=6, day=15, hour=10, minute=5, second=30)
        result = _parse_info_t1_response(rec)
        expected = _expected_t1_ts(2025, 6, 15, 10, 5, 30)
        assert result["last_brush_time"] == expected

    # --- Edge cases ---

    def test_too_short_returns_empty(self):
        assert _parse_info_t1_response(b"") == {}
        assert _parse_info_t1_response(bytes(10)) == {}  # need 12 bytes (through pNum)
        assert _parse_info_t1_response(bytes(11)) == {}  # 11 bytes still too short

    def test_exactly_12_bytes_accepted(self):
        """Minimum valid payload: 12 bytes (through pNum at byte 11)."""
        payload = bytearray(12)
        payload[0:5] = b"\x2a\x42\x23\x00\x00"
        payload[5] = 26   # year - 2000
        payload[6] = 2    # month
        payload[7] = 21   # day
        payload[8] = 16   # hour
        payload[9] = 25   # minute
        payload[10] = 31  # second
        payload[11] = 42  # pNum
        result = _parse_info_t1_response(bytes(payload))
        assert "last_brush_time" in result
        assert result["last_brush_pnum"] == 42
        assert "last_brush_duration" not in result  # need 14 bytes for duration
        assert "last_brush_score" not in result

    def test_invalid_date_returns_empty(self):
        rec = _make_t1_record(month=13)
        assert _parse_info_t1_response(rec) == {}

    def test_invalid_day_zero_returns_empty(self):
        rec = _make_t1_record(day=0)
        assert _parse_info_t1_response(rec) == {}


# ---------------------------------------------------------------------------
# parse_notification → Type-1 INFO routing (0307)
# ---------------------------------------------------------------------------

class TestParseNotificationInfoT1Routing:

    def test_0307_real_session_2min(self):
        """Real 2-min session: timestamp, pNum=0, duration=120 s extracted."""
        raw = bytes.fromhex("03072a422300001a0215102c1c00007800780201")
        result = parse_notification(raw)
        assert "last_brush_time" in result
        assert result["last_brush_pnum"] == 0
        assert result["last_brush_duration"] == 120
        assert "last_brush_score" not in result

    def test_0307_real_session_7s(self):
        """Real 7s session: timestamp, pNum=231, duration=30 s extracted."""
        raw = bytes.fromhex("03072a422300001a021510191fe7001e001e6400")
        result = parse_notification(raw)
        assert "last_brush_time" in result
        assert result["last_brush_pnum"] == 231
        assert result["last_brush_duration"] == 30
        assert "last_brush_score" not in result

    def test_0307_too_short_returns_empty(self):
        payload = RESP_INFO_T1 + bytes([0xAA, 0xBB, 0xCC])
        assert parse_notification(payload) == {}

    def test_0307_valid_constructed_record(self):
        rec = _make_t1_record(year=2026, month=1, day=10,
                               hour=7, minute=30, second=0, pnum=76, duration=120)
        result = parse_notification(RESP_INFO_T1 + rec)
        assert result["last_brush_pnum"] == 76
        assert result["last_brush_duration"] == 120
        assert "last_brush_score" not in result
        expected = _expected_t1_ts(2026, 1, 10, 7, 30, 0)
        assert result["last_brush_time"] == expected

    def test_0307_and_0308_handled_independently(self):
        """0307 and 0308 route to different parsers and produce independent results."""
        t1_raw = bytes.fromhex("03072a422300001a0215102c1c00007800780201")
        t0_raw = RESP_INFO + _make_record(pressure_raw=300)
        r_t1 = parse_notification(t1_raw)
        r_t0 = parse_notification(t0_raw)
        assert "last_brush_score" not in r_t1
        assert "last_brush_pressure" in r_t0
        assert "last_brush_pressure" not in r_t1
        # 0307 carries pNum and duration; 0308-simple carries pressure
        assert "last_brush_pnum" in r_t1
        assert "last_brush_duration" in r_t1


# ---------------------------------------------------------------------------
# Helper: build an extended 32-byte running-data record (AbstractC0002b.m37y)
# ---------------------------------------------------------------------------

def _make_extended_record(
    *,
    year: int = 2026,
    month: int = 2,
    day: int = 21,
    hour: int = 20,
    minute: int = 0,
    second: int = 0,
    p_num: int = 7,
    duration: int = 120,
    valid_duration: int = 110,
    pressure_zones: tuple = (50, 50, 50, 50, 50),  # 5 intermediate zone values (bytes 13-17)
    tz_quarters: int = 4,                           # +4 × 15 min = UTC+1
    area_pressures: tuple = (100, 80, 90, 70, 110, 85, 95, 60),  # 8 tooth area bytes 20-27
    score: int = 85,
    scheme_type: int = 2,
    bus_brushing: int = 0,
    cross_number: int = 1,
    extra: bytes = b"",
) -> bytes:
    """Build a minimal 32-byte extended running-data record (+ optional extra bytes)."""
    record_length = 32 + len(extra)
    buf = bytearray(32)
    buf[0] = (record_length >> 8) & 0xFF   # high byte of BE uint16 (always 0 for BLE MTU < 256)
    buf[1] = record_length & 0xFF           # low byte = actual length
    buf[2] = year - 2000
    buf[3] = month
    buf[4] = day
    buf[5] = hour
    buf[6] = minute
    buf[7] = second
    buf[8] = p_num
    buf[9]  = (duration >> 8) & 0xFF
    buf[10] = duration & 0xFF
    buf[11] = (valid_duration >> 8) & 0xFF
    buf[12] = valid_duration & 0xFF
    buf[13:18] = bytes(pressure_zones)      # 5 intermediate pressure zone values
    buf[18] = 0                             # RESERVED
    buf[19] = tz_quarters % 256             # signed stored as unsigned byte
    buf[20:28] = bytes(area_pressures)      # 8 tooth area pressure values
    buf[28] = score
    buf[29] = scheme_type
    buf[30] = bus_brushing
    buf[31] = cross_number
    return bytes(buf) + extra


# ---------------------------------------------------------------------------
# _parse_extended_running_data_record  (new – AbstractC0002b.m37y format)
# ---------------------------------------------------------------------------

class TestParseExtendedRunningDataRecord:

    # --- Field extraction ---

    def test_score_extracted(self):
        rec = _make_extended_record(score=85)
        assert _parse_extended_running_data_record(rec)["last_brush_score"] == 85

    def test_score_zero(self):
        rec = _make_extended_record(score=0)
        assert _parse_extended_running_data_record(rec)["last_brush_score"] == 0

    def test_score_clamped_at_100(self):
        """Scores > 100 (firmware bug?) must be clamped."""
        rec = _make_extended_record(score=255)
        assert _parse_extended_running_data_record(rec)["last_brush_score"] == 100

    def test_duration_extracted(self):
        rec = _make_extended_record(duration=180)
        assert _parse_extended_running_data_record(rec)["last_brush_duration"] == 180

    def test_duration_be_uint16(self):
        """Duration is BE uint16: value 300 = 0x012C → bytes [0x01, 0x2C]."""
        rec = _make_extended_record(duration=300)
        result = _parse_extended_running_data_record(rec)
        assert result["last_brush_duration"] == 300

    def test_p_num_extracted(self):
        rec = _make_extended_record(p_num=42)
        assert _parse_extended_running_data_record(rec)["last_brush_pnum"] == 42

    # --- Timestamp & timezone ---

    def test_utc_conversion_utcplus1(self):
        """tz_quarters=4 (UTC+1): device 20:00 → UTC 19:00."""
        rec = _make_extended_record(
            year=2026, month=2, day=21,
            hour=20, minute=0, second=0,
            tz_quarters=4,
        )
        expected = _expected_utc_ts(2026, 2, 21, 20, 0, 0, 4)
        assert _parse_extended_running_data_record(rec)["last_brush_time"] == expected

    def test_utc_conversion_utcplus8(self):
        """tz_quarters=32 (UTC+8): device 08:30 → UTC 00:30."""
        rec = _make_extended_record(
            year=2026, month=1, day=1,
            hour=8, minute=30, second=0,
            tz_quarters=32,
        )
        expected = _expected_utc_ts(2026, 1, 1, 8, 30, 0, 32)
        assert _parse_extended_running_data_record(rec)["last_brush_time"] == expected

    def test_utc_conversion_negative_offset(self):
        """tz_quarters=236 (= -20, UTC-5): device 12:00 → UTC 17:00."""
        rec = _make_extended_record(
            year=2026, month=6, day=15,
            hour=12, minute=0, second=0,
            tz_quarters=236,
        )
        expected = _expected_utc_ts(2026, 6, 15, 12, 0, 0, 236)
        assert _parse_extended_running_data_record(rec)["last_brush_time"] == expected

    def test_utc_zero_offset(self):
        rec = _make_extended_record(tz_quarters=0)
        expected = _expected_utc_ts(2026, 2, 21, 20, 0, 0, 0)
        assert _parse_extended_running_data_record(rec)["last_brush_time"] == expected

    # --- Tooth area pressures ---

    def test_area_dict_has_all_8_zone_names(self):
        rec = _make_extended_record()
        areas = _parse_extended_running_data_record(rec)["last_brush_areas"]
        assert set(areas.keys()) == set(TOOTH_AREA_NAMES)

    def test_area_dict_values_match_input(self):
        pressures = (10, 20, 30, 40, 50, 60, 70, 80)
        rec = _make_extended_record(area_pressures=pressures)
        areas = _parse_extended_running_data_record(rec)["last_brush_areas"]
        for i, name in enumerate(TOOTH_AREA_NAMES):
            assert areas[name] == pressures[i], f"Zone {name}: expected {pressures[i]}, got {areas[name]}"

    def test_area_dict_zone_order_matches_brushareatype(self):
        """Verify BrushAreaType enum order: value 1 = upper_left_out at index 0."""
        pressures = (1, 2, 3, 4, 5, 6, 7, 8)  # each area gets its BrushAreaType value
        rec = _make_extended_record(area_pressures=pressures)
        areas = _parse_extended_running_data_record(rec)["last_brush_areas"]
        assert areas["upper_left_out"] == 1     # AREA_LIFT_UP_OUT   (value 1)
        assert areas["upper_left_in"]  == 2     # AREA_LIFT_UP_IN    (value 2)
        assert areas["lower_left_out"] == 3     # AREA_LIFT_DOWN_OUT (value 3)
        assert areas["lower_left_in"]  == 4     # AREA_LIFT_DOWN_IN  (value 4)
        assert areas["upper_right_out"] == 5    # AREA_RIGHT_UP_OUT  (value 5)
        assert areas["upper_right_in"]  == 6    # AREA_RIGHT_UP_IN   (value 6)
        assert areas["lower_right_out"] == 7    # AREA_RIGHT_DOWN_OUT(value 7)
        assert areas["lower_right_in"]  == 8    # AREA_RIGHT_DOWN_IN (value 8)

    def test_area_all_zones_zero_still_returns_dict(self):
        """All-zero areas → dict present with 8 zero values."""
        rec = _make_extended_record(area_pressures=(0, 0, 0, 0, 0, 0, 0, 0))
        result = _parse_extended_running_data_record(rec)
        assert "last_brush_areas" in result
        assert all(v == 0 for v in result["last_brush_areas"].values())

    # --- Average pressure ---

    def test_avg_pressure_calculation(self):
        """avg_pressure = round(sum(areas) / 8)."""
        pressures = (100, 80, 90, 70, 110, 85, 95, 60)  # sum=690, avg=86.25 → 86
        rec = _make_extended_record(area_pressures=pressures)
        assert _parse_extended_running_data_record(rec)["last_brush_pressure"] == 86

    def test_avg_pressure_all_zero(self):
        rec = _make_extended_record(area_pressures=(0, 0, 0, 0, 0, 0, 0, 0))
        assert _parse_extended_running_data_record(rec)["last_brush_pressure"] == 0

    def test_avg_pressure_all_max(self):
        """255 × 8 / 8 = 255."""
        rec = _make_extended_record(area_pressures=(255, 255, 255, 255, 255, 255, 255, 255))
        assert _parse_extended_running_data_record(rec)["last_brush_pressure"] == 255

    # --- Size and edge cases ---

    def test_exactly_32_bytes_accepted(self):
        rec = _make_extended_record()
        assert len(rec) == 32
        result = _parse_extended_running_data_record(rec)
        assert "last_brush_score" in result

    def test_extra_trailing_bytes_ignored(self):
        """Records > 32 bytes (pressureProfile suffix) must still parse correctly."""
        rec = _make_extended_record(extra=bytes([0xAA, 0xBB, 0xCC, 0xDD]))
        assert len(rec) == 36
        result = _parse_extended_running_data_record(rec)
        assert result["last_brush_score"] == 85

    def test_too_short_31_bytes_returns_empty(self):
        assert _parse_extended_running_data_record(bytes(31)) == {}

    def test_too_short_empty_returns_empty(self):
        assert _parse_extended_running_data_record(b"") == {}

    def test_invalid_date_returns_empty(self):
        """Impossible date (month=13) triggers ValueError → {}."""
        rec = _make_extended_record(month=13)
        assert _parse_extended_running_data_record(rec) == {}

    def test_invalid_day_zero_returns_empty(self):
        rec = _make_extended_record(day=0)
        assert _parse_extended_running_data_record(rec) == {}

    def test_all_expected_keys_present(self):
        """Verify the full set of keys returned by the extended parser."""
        rec = _make_extended_record(area_pressures=(50, 50, 50, 50, 50, 50, 50, 50))
        result = _parse_extended_running_data_record(rec)
        for key in (
            "last_brush_time",
            "last_brush_duration",
            "last_brush_score",
            "last_brush_pressure",
            "last_brush_areas",
            "last_brush_pnum",
        ):
            assert key in result, f"Expected key missing: {key}"


# ---------------------------------------------------------------------------
# _parse_info_response  – format auto-detection (extended vs. simple)
# ---------------------------------------------------------------------------

class TestParseInfoResponseFormatDetection:

    def test_extended_format_detected_and_parsed(self):
        """payload[0]==0, payload[1]>=32 → extended format with score/areas/pNum."""
        payload = _make_extended_record(score=72, p_num=5, duration=90)
        result = _parse_info_response(payload)
        assert result["last_brush_score"] == 72
        assert result["last_brush_pnum"] == 5
        assert result["last_brush_duration"] == 90
        assert "last_brush_areas" in result

    def test_simple_format_used_when_byte0_nonzero(self):
        """year-2000=24 in byte0 → simple format → no score/areas."""
        payload = _make_record(year=2024, pressure_raw=300)
        result = _parse_info_response(payload)
        assert result["last_brush_pressure"] == 1.0
        assert "last_brush_score" not in result
        assert "last_brush_areas" not in result

    def test_year_2000_rejected_by_simple_format(self):
        """byte0==0 (year=2000) fails extended check AND year-validation in simple parser → {}."""
        payload = _make_record(year=2000, month=3, day=15)
        # payload[0]=0 (year-2000=0), payload[1]=3 (month) < 32 → extended check fails
        # Simple parser: year 2000 < 2015 → rejected
        result = _parse_info_response(payload)
        assert result == {}

    def test_extended_requires_sufficient_payload_length(self):
        """payload[1]=32 but payload is only 20 bytes → extended check fails → simple fallback."""
        # Manually craft a payload that claims to be 32 bytes but is only 20
        buf = bytearray(20)
        buf[0] = 0
        buf[1] = 32   # claims 32 bytes but len=20 → 20 >= 32 is False
        result = _parse_info_response(bytes(buf))
        # Falls through to simple format; bytes(20) has year=0→2000, month=0 → invalid → {}
        assert result == {}

    def test_extended_format_all_zones_zero_parses_correctly(self):
        """Extended record with all areas=0 still produces score and areas."""
        payload = _make_extended_record(area_pressures=(0, 0, 0, 0, 0, 0, 0, 0))
        result = _parse_info_response(payload)
        assert "last_brush_score" in result
        assert "last_brush_areas" in result

    def test_end_to_end_via_parse_notification_extended(self):
        """Full round-trip: parse_notification(0308 + extended_record) → extended fields."""
        extended = _make_extended_record(score=60, p_num=3, duration=150)
        result = parse_notification(RESP_INFO + extended)
        assert result["last_brush_score"] == 60
        assert result["last_brush_pnum"] == 3
        assert result["last_brush_duration"] == 150
        assert "last_brush_areas" in result

    def test_end_to_end_via_parse_notification_simple(self):
        """Full round-trip: parse_notification(0308 + simple_record) → simple fields."""
        simple = _make_record(pressure_raw=600)
        result = parse_notification(RESP_INFO + simple)
        assert result["last_brush_pressure"] == 2.0
        assert "last_brush_score" not in result


# ---------------------------------------------------------------------------
# _parse_k3guide_response  (new – 0340 real-time zone guidance)
# ---------------------------------------------------------------------------

class TestParseK3GuideResponse:

    def test_valid_6_bytes_returns_empty_dict(self):
        """K3GUIDE is real-time only – must not persist any sensor state."""
        payload = bytes([100, 80, 90, 70, 3, 1])
        assert _parse_k3guide_response(payload) == {}

    def test_any_valid_zone_ids_return_empty(self):
        """Zone IDs 1-8 are all valid; function must not crash and returns {}."""
        for zone_id in range(1, 9):
            payload = bytes([50, 50, 50, 50, zone_id, 1])
            assert _parse_k3guide_response(payload) == {}

    def test_zone_id_255_stop_returns_empty(self):
        """Zone ID 255 = brushing stopped (AREA_STOP). Must not raise."""
        payload = bytes([0, 0, 0, 0, 255, 0])
        assert _parse_k3guide_response(payload) == {}

    def test_zone_id_out_of_range_returns_empty(self):
        """Zone IDs 0, 9-254 are not defined in BrushAreaType. Must not raise."""
        for zone_id in (0, 9, 127, 254):
            payload = bytes([50, 50, 50, 50, zone_id, 1])
            assert _parse_k3guide_response(payload) == {}

    def test_too_short_5_bytes_returns_empty(self):
        assert _parse_k3guide_response(bytes(5)) == {}

    def test_too_short_empty_returns_empty(self):
        assert _parse_k3guide_response(b"") == {}

    def test_exact_6_bytes_minimum_accepted(self):
        payload = bytes([0, 0, 0, 0, 1, 0])
        assert _parse_k3guide_response(payload) == {}

    def test_extra_bytes_beyond_6_accepted(self):
        """Additional bytes (firmware version differences) must not crash."""
        payload = bytes([100, 80, 90, 70, 3, 1, 0xFF, 0xFF])
        assert _parse_k3guide_response(payload) == {}


# ---------------------------------------------------------------------------
# parse_notification → K3GUIDE routing (0340)
# ---------------------------------------------------------------------------

class TestParseNotificationK3GuideRouting:

    def test_0340_prefix_returns_empty(self):
        """0340 notifications are real-time only and must not return sensor state."""
        data = RESP_K3GUIDE + bytes([100, 80, 90, 70, 3, 1])
        assert parse_notification(data) == {}

    def test_0340_prefix_short_payload_returns_empty(self):
        """0340 with payload < 6 bytes: returns {} without crash."""
        data = RESP_K3GUIDE + bytes([50, 50])
        assert parse_notification(data) == {}

    def test_0340_not_confused_with_0308(self):
        """0340 and 0308 must route to different handlers."""
        k3guide_data = RESP_K3GUIDE + bytes([100, 80, 90, 70, 3, 1])
        info_data = RESP_INFO + _make_record(pressure_raw=300)
        assert parse_notification(k3guide_data) == {}
        assert "last_brush_pressure" in parse_notification(info_data)

    def test_0340_all_zone_ids_handled(self):
        """Routing is stable for all valid zone IDs in the K3GUIDE payload."""
        for zone_id in list(range(1, 9)) + [255]:
            data = RESP_K3GUIDE + bytes([50, 50, 50, 50, zone_id, 0])
            assert parse_notification(data) == {}


# ---------------------------------------------------------------------------
# Type-1 score notification (0000)
# ---------------------------------------------------------------------------

class TestParseScoreT1Response:
    # Observed payload from Oclean X log 2026-02-24: score=95, rest logged only
    _REAL_PAYLOAD = bytes.fromhex("5f00ffffffffffffff1a0215101a23e7001e")

    def test_real_payload_returns_score_95(self):
        result = _parse_score_t1_response(self._REAL_PAYLOAD)
        assert result == {"last_brush_score": 95}

    def test_score_zero_is_valid(self):
        payload = bytes([0]) + b"\x00" * 17
        assert _parse_score_t1_response(payload) == {"last_brush_score": 0}

    def test_score_100_is_valid(self):
        payload = bytes([100]) + b"\x00" * 17
        assert _parse_score_t1_response(payload) == {"last_brush_score": 100}

    def test_score_clamped_above_100(self):
        payload = bytes([200]) + b"\x00" * 17
        assert _parse_score_t1_response(payload)["last_brush_score"] == 100

    def test_score_ff_returns_empty(self):
        """0xFF means no data."""
        payload = bytes([0xFF]) + b"\x00" * 17
        assert _parse_score_t1_response(payload) == {}

    def test_empty_payload_returns_empty(self):
        assert _parse_score_t1_response(b"") == {}

    def test_single_byte_minimum(self):
        assert _parse_score_t1_response(bytes([50])) == {"last_brush_score": 50}

    def test_routed_via_parse_notification(self):
        data = RESP_SCORE_T1 + self._REAL_PAYLOAD
        result = parse_notification(data)
        assert result == {"last_brush_score": 95}

    def test_routing_independent_of_rest_bytes(self):
        """Any 0000-prefixed notification routes to this handler."""
        data = RESP_SCORE_T1 + bytes([70])
        assert parse_notification(data) == {"last_brush_score": 70}


# ---------------------------------------------------------------------------
# Type-1 session-metadata notification (5a00) – logging only
# ---------------------------------------------------------------------------

class TestParseSessionMetaT1Response:
    # Observed payload: Feb 24 2026 00:24:19, duration 150 s
    _REAL_PAYLOAD = bytes.fromhex("ffffffffffffff1a02180018134c00960096")

    def test_real_payload_returns_timestamp_and_duration(self):
        result = _parse_session_meta_t1_response(self._REAL_PAYLOAD)
        assert "last_brush_time" in result
        assert result["last_brush_duration"] == 150
        expected_ts = _expected_t1_ts(2026, 2, 24, 0, 24, 19)
        assert result["last_brush_time"] == expected_ts

    def test_duration_ff_omitted(self):
        """0xFF duration means no data – should not appear in result."""
        payload = bytearray(self._REAL_PAYLOAD)
        payload[15] = 0xFF
        result = _parse_session_meta_t1_response(bytes(payload))
        assert "last_brush_duration" not in result

    def test_duration_zero_omitted(self):
        payload = bytearray(self._REAL_PAYLOAD)
        payload[15] = 0
        result = _parse_session_meta_t1_response(bytes(payload))
        assert "last_brush_duration" not in result

    def test_too_short_returns_empty(self):
        assert _parse_session_meta_t1_response(b"\xff" * 17) == {}

    def test_empty_returns_empty(self):
        assert _parse_session_meta_t1_response(b"") == {}

    def test_routed_via_parse_notification(self):
        data = RESP_SESSION_META_T1 + self._REAL_PAYLOAD
        result = parse_notification(data)
        assert "last_brush_time" in result
        assert result["last_brush_duration"] == 150


# ---------------------------------------------------------------------------
# Type-1 brush-areas notification (2604)
# ---------------------------------------------------------------------------

class TestParseBrushAreasT1Response:
    # Observed payload from Oclean X log 2026-02-24
    _REAL_PAYLOAD = bytes.fromhex("390000000f0018181a1a0710071009101007")

    def test_real_payload_returns_areas(self):
        result = _parse_brush_areas_t1_response(self._REAL_PAYLOAD)
        assert "last_brush_areas" in result
        areas = result["last_brush_areas"]
        assert len(areas) == 8
        assert areas["upper_left_out"] == 0x18   # 24
        assert areas["upper_left_in"] == 0x18    # 24
        assert areas["lower_left_out"] == 0x1a   # 26
        assert areas["lower_left_in"] == 0x1a    # 26
        assert areas["upper_right_out"] == 0x07  # 7
        assert areas["upper_right_in"] == 0x10   # 16
        assert areas["lower_right_out"] == 0x07  # 7
        assert areas["lower_right_in"] == 0x10   # 16

    def test_real_payload_pressure(self):
        result = _parse_brush_areas_t1_response(self._REAL_PAYLOAD)
        assert "last_brush_pressure" in result
        assert "last_brush_clean" not in result

    def test_all_zones_zero_areas_present(self):
        payload = bytes([0x39, 0, 0, 0, 0x0F, 0]) + bytes(8) + bytes(4)
        result = _parse_brush_areas_t1_response(payload)
        assert "last_brush_areas" in result
        assert "last_brush_clean" not in result

    def test_area_names_match_tooth_area_names_order(self):
        areas = bytes([1, 2, 3, 4, 5, 6, 7, 8])
        payload = bytes(6) + areas
        result = _parse_brush_areas_t1_response(payload)
        for i, name in enumerate(TOOTH_AREA_NAMES):
            assert result["last_brush_areas"][name] == i + 1

    def test_too_short_13_bytes_returns_empty(self):
        assert _parse_brush_areas_t1_response(bytes(13)) == {}

    def test_empty_returns_empty(self):
        assert _parse_brush_areas_t1_response(b"") == {}

    def test_exactly_14_bytes_minimum_accepted(self):
        result = _parse_brush_areas_t1_response(bytes(14))
        assert "last_brush_areas" in result

    def test_routed_via_parse_notification(self):
        data = RESP_BRUSH_AREAS_T1 + self._REAL_PAYLOAD
        result = parse_notification(data)
        assert "last_brush_areas" in result
        assert result["last_brush_areas"]["upper_left_out"] == 24
