"""
Tests based on real BLE payloads captured from a physical Oclean X device.

Every hex string in this file is a verbatim BLE notification received from
the device (MAC 70:28:45:69:E4:A4, name "Oclean X") during development.
Capture date: 2026-02-21.

Goal: guard against regressions that break real-device compatibility.
      If one of these tests fails, a real device will no longer show correct
      data in Home Assistant.

Capture procedure: tools/oclean_capture.py
"""
from __future__ import annotations

import datetime
import time

from custom_components.oclean_ble.parser import parse_notification


# ---------------------------------------------------------------------------
# Helper: compute expected Unix timestamp for a device-local datetime.
# Mirrors the logic in _parse_info_t1_response: time.mktime() interprets
# the naive datetime in the system's local timezone (= HA timezone on HA OS).
# ---------------------------------------------------------------------------

def _local_ts(year: int, month: int, day: int, hour: int, minute: int, second: int) -> int:
    return int(time.mktime(datetime.datetime(year, month, day, hour, minute, second).timetuple()))


# ===========================================================================
# Real 0303 STATE responses  (reply to CMD_QUERY_STATUS 0x0303)
# ===========================================================================

class TestReal0303StateResponse:
    """CMD_QUERY_STATUS reply from Oclean X while idle."""

    # Captured 2026-02-21, device idle, battery 29 %
    # Full notification:  03 03 02 0e 4b 1d 00 00
    #   payload[1]:   0x0e (unknown, varies between polls)
    #   payload[2]:   0x4b (unknown, varies between polls)
    #   payload[3]:   0x1d = 29 → battery %
    RAW_BATTERY_29 = bytes.fromhex("0303020e4b1d0000")

    # Captured 2026-02-21, battery 25 %
    # Full notification:  03 03 02 0e 39 19 00 00,  payload[3]=0x19=25
    RAW_BATTERY_25 = bytes.fromhex("030302 0e 39 19 00 00".replace(" ", ""))

    # Captured 2026-02-22, battery 51 %
    # Full notification:  03 03 02 0f 00 33 00 00
    #   payload[1]:   0x0f (byte 1 varies – previously saw 0x0e)
    #   payload[2]:   0x00 (byte 2 varies – "cached score" hypothesis disproved)
    #   payload[3]:   0x33 = 51 → battery %
    RAW_BATTERY_51 = bytes.fromhex("0303020f00330000")

    def test_battery_29_extracted(self):
        result = parse_notification(self.RAW_BATTERY_29)
        assert result["battery"] == 29

    def test_battery_25_extracted(self):
        result = parse_notification(self.RAW_BATTERY_25)
        assert result["battery"] == 25

    def test_battery_51_extracted(self):
        result = parse_notification(self.RAW_BATTERY_51)
        assert result["battery"] == 51

    def test_battery_51_byte2_zero(self):
        """byte2=0x00 (not 0x4b as in earlier captures) must not affect battery extraction."""
        result = parse_notification(self.RAW_BATTERY_51)
        assert result["battery"] == 51

    def test_no_brush_session_fields(self):
        """STATE response must never produce brush-session data."""
        result = parse_notification(self.RAW_BATTERY_29)
        for key in (
            "last_brush_score",
            "last_brush_duration",
            "last_brush_time",
            "last_brush_pressure",
            "last_brush_clean",
            "last_brush_areas",
        ):
            assert key not in result, f"Unexpected key in STATE response: {key}"

    def test_only_battery_key_present(self):
        result = parse_notification(self.RAW_BATTERY_29)
        assert set(result.keys()) == {"battery"}

    def test_battery_is_int(self):
        result = parse_notification(self.RAW_BATTERY_29)
        assert isinstance(result["battery"], int)


# ===========================================================================
# Real 0202 device-info ACK  (reply to CMD_DEVICE_INFO 0x0202)
# ===========================================================================

class TestReal0202DeviceInfoAck:
    """CMD_DEVICE_INFO reply is just an "OK" acknowledge, no sensor data."""

    # Full notification:  02 02 4f 4b   (= "0202 OK")
    RAW_ACK = bytes.fromhex("02024f4b")

    def test_ack_produces_empty_dict(self):
        assert parse_notification(self.RAW_ACK) == {}

    def test_ack_not_mistaken_for_brush_data(self):
        result = parse_notification(self.RAW_ACK)
        assert "battery" not in result
        assert "last_brush_score" not in result


# ===========================================================================
# Real 0307 Type-1 running-data responses  (Oclean X brush sessions)
#
# All three sessions were captured on 2026-02-21 from the Oclean X.
# The device sends 0307 on RECEIVE_BRUSH_UUID after CMD_QUERY_RUNNING_DATA_T1.
#
# Payload layout (bytes after the 0307 prefix):
#   0-4:  device constant  2a 42 23 00 00  (same in every session)
#   5:    year-2000        (confirmed)
#   6:    month            (confirmed)
#   7:    day              (confirmed)
#   8:    hour             (confirmed)
#   9:    minute           (confirmed)
#   10:   second           (confirmed)
#   11:   unknown          (variable: 4d, e7, 00, ...)
#   12:   0x00             (padding)
#   13:   brushing metric  (seconds; score = clamp(byte13-30, 1, 100))
#   14:   0x00             (padding)
#   15:   equals byte 13   (redundant copy)
#   16:   unknown          (observed: 27, 64, 02)
#   17:   session counter  (monotonically increasing; observed 0, 1, 4, 5)
# ===========================================================================

class TestReal0307Session1_Score100:
    """
    Session 1 – long brush, score 100.

    Full notification (20 bytes):
      03 07 2a 42 23 00 00 1a 02 15 0f 2a 13 4d 00 96 00 96 27 04
                                                ^^ byte13 = 0x96 = 150
    Timestamp:  2026-02-21 15:42:19 (device local time)
    Duration:   150 s
    Score:      NOT in 0307 payload – arrives via 0000 notification
    """

    RAW = bytes.fromhex("03072a422300001a02150f2a134d009600962704")

    def test_duration_150s(self):
        assert parse_notification(self.RAW)["last_brush_duration"] == 150

    def test_timestamp(self):
        result = parse_notification(self.RAW)
        expected = _local_ts(2026, 2, 21, 15, 42, 19)
        assert result["last_brush_time"] == expected

    def test_no_pressure_or_areas(self):
        """0307 format does not carry zone pressures."""
        result = parse_notification(self.RAW)
        assert "last_brush_pressure" not in result
        assert "last_brush_areas" not in result
        assert "last_brush_clean" not in result

    def test_no_pnum_or_scheme_type(self):
        """0307 format does not carry pNum or schemeType."""
        result = parse_notification(self.RAW)
        assert "last_brush_pnum" not in result
        assert "last_brush_scheme_type" not in result

    def test_no_score_in_result(self):
        """Score is NOT in 0307; it arrives via the 0000 notification."""
        assert "last_brush_score" not in parse_notification(self.RAW)

    def test_expected_keys_only(self):
        result = parse_notification(self.RAW)
        assert set(result.keys()) == {"last_brush_time", "last_brush_duration"}


class TestReal0307Session2_Score1:
    """
    Session 2 – very short brush (~7 s), score 1.

    Full notification (20 bytes):
      03 07 2a 42 23 00 00 1a 02 15 10 19 1f e7 00 1e 00 1e 64 00
                                                ^^ byte13 = 0x1e = 30 (device floor)
    Timestamp:  2026-02-21 16:25:31 (device local time)
    Duration:   30 s  (device reports minimum floor even for 7 s sessions)
    Score:      NOT in 0307 payload – arrives via 0000 notification
    """

    RAW = bytes.fromhex("03072a422300001a021510191fe7001e001e6400")

    def test_duration_30s_floor(self):
        """Oclean X reports 30 s minimum even for sessions shorter than 30 s."""
        assert parse_notification(self.RAW)["last_brush_duration"] == 30

    def test_timestamp(self):
        result = parse_notification(self.RAW)
        expected = _local_ts(2026, 2, 21, 16, 25, 31)
        assert result["last_brush_time"] == expected

    def test_no_extended_fields(self):
        result = parse_notification(self.RAW)
        assert "last_brush_pressure" not in result
        assert "last_brush_areas" not in result
        assert "last_brush_clean" not in result

    def test_no_score_in_result(self):
        assert "last_brush_score" not in parse_notification(self.RAW)

    def test_expected_keys_only(self):
        result = parse_notification(self.RAW)
        assert set(result.keys()) == {"last_brush_time", "last_brush_duration"}


class TestReal0307Session3_Score90:
    """
    Session 3 – full 2-minute brush, score 90.

    Full notification (20 bytes):
      03 07 2a 42 23 00 00 1a 02 15 10 2c 1c 00 00 78 00 78 02 01
                                                ^^ byte13 = 0x78 = 120 s
    Timestamp:  2026-02-21 16:44:28 (device local time)
    Duration:   120 s (= 2 minutes)
    Score:      NOT in 0307 payload – arrives via 0000 notification
    """

    RAW = bytes.fromhex("03072a422300001a0215102c1c00007800780201")

    def test_duration_120s(self):
        assert parse_notification(self.RAW)["last_brush_duration"] == 120

    def test_timestamp(self):
        result = parse_notification(self.RAW)
        expected = _local_ts(2026, 2, 21, 16, 44, 28)
        assert result["last_brush_time"] == expected

    def test_no_extended_fields(self):
        result = parse_notification(self.RAW)
        assert "last_brush_pressure" not in result
        assert "last_brush_areas" not in result

    def test_no_score_in_result(self):
        assert "last_brush_score" not in parse_notification(self.RAW)

    def test_expected_keys_only(self):
        result = parse_notification(self.RAW)
        assert set(result.keys()) == {"last_brush_time", "last_brush_duration"}


class TestReal0307Session4_Score100_Byte15Disproof:
    """
    Session 4 – captured 2026-02-22 01:45:39 via oclean_capture.py.

    Full notification (20 bytes):
      03 07 2a 42 23 00 00 1a 02 16 01 2d 27 4c 00 96 00 0b 00 00
                                                ^^ byte13 = 0x96 = 150
                                                            ^^ byte15 = 0x0b = 11  ≠ byte13!
    Timestamp:  2026-02-22 01:45:39 (device local time)
    Duration:   150 s
    Score:      NOT in 0307 payload – arrives via 0000 notification

    Key finding: byte 15 is 0x0b = 11, NOT equal to byte 13 = 0x96 = 150.
    This disproves the earlier hypothesis that byte 15 is a redundant copy of byte 13.
    The parser ignores byte 15.
    """

    RAW = bytes.fromhex("03072a422300001a0216012d274c0096000b0000")

    def test_duration_150s(self):
        assert parse_notification(self.RAW)["last_brush_duration"] == 150

    def test_timestamp(self):
        result = parse_notification(self.RAW)
        expected = _local_ts(2026, 2, 22, 1, 45, 39)
        assert result["last_brush_time"] == expected

    def test_byte15_not_equal_byte13_does_not_affect_output(self):
        """byte15=0x0b ≠ byte13=0x96: parser uses only byte13 for duration."""
        raw_bytes = bytearray(self.RAW)
        assert raw_bytes[2 + 13] == 0x96  # byte13 (after 0307 prefix)
        assert raw_bytes[2 + 15] == 0x0b  # byte15 ≠ byte13
        result = parse_notification(self.RAW)
        assert result["last_brush_duration"] == 150

    def test_no_score_in_result(self):
        assert "last_brush_score" not in parse_notification(self.RAW)

    def test_expected_keys_only(self):
        result = parse_notification(self.RAW)
        assert set(result.keys()) == {"last_brush_time", "last_brush_duration"}


# ===========================================================================
# Cross-session consistency  (all four sessions)
# ===========================================================================

class TestReal0307CrossSession:
    """Consistency checks across all four captured sessions."""

    SESSIONS = [
        # (raw_hex, expected_duration, year, month, day, hour, minute, second)
        ("03072a422300001a02150f2a134d009600962704", 150, 2026, 2, 21, 15, 42, 19),
        ("03072a422300001a021510191fe7001e001e6400",  30, 2026, 2, 21, 16, 25, 31),
        ("03072a422300001a0215102c1c00007800780201", 120, 2026, 2, 21, 16, 44, 28),
        # Session 4 captured 2026-02-22: byte15=0x0b ≠ byte13=0x96 (disproves "redundant copy")
        ("03072a422300001a0216012d274c0096000b0000", 150, 2026, 2, 22,  1, 45, 39),
    ]

    def test_all_sessions_produce_no_score(self):
        """Score is NOT in 0307; it arrives via the 0000 notification."""
        for raw_hex, *_ in self.SESSIONS:
            result = parse_notification(bytes.fromhex(raw_hex))
            assert "last_brush_score" not in result, f"Unexpected score in {raw_hex[:20]}…"

    def test_all_sessions_produce_duration(self):
        for raw_hex, duration, *_ in self.SESSIONS:
            result = parse_notification(bytes.fromhex(raw_hex))
            assert result.get("last_brush_duration") == duration, f"Expected duration={duration}"

    def test_all_sessions_produce_timestamp(self):
        for raw_hex, _, year, month, day, hour, minute, second in self.SESSIONS:
            result = parse_notification(bytes.fromhex(raw_hex))
            expected = _local_ts(year, month, day, hour, minute, second)
            assert result.get("last_brush_time") == expected, f"Timestamp mismatch for {raw_hex[:20]}…"

    def test_sessions_ordered_chronologically(self):
        """Later sessions should have a later timestamp than earlier ones."""
        timestamps = []
        for raw_hex, *_ in self.SESSIONS:
            result = parse_notification(bytes.fromhex(raw_hex))
            timestamps.append(result["last_brush_time"])
        assert timestamps == sorted(timestamps), "Sessions not in chronological order"

    def test_device_constant_bytes_do_not_appear_in_output(self):
        """The leading 2a 42 23 00 00 constant must not leak into any field value."""
        for raw_hex, *_ in self.SESSIONS:
            result = parse_notification(bytes.fromhex(raw_hex))
            for key, value in result.items():
                if isinstance(value, int):
                    assert value != 0x2a42230000, f"Device constant leaked into {key}"

    def test_duration_positive(self):
        for raw_hex, *_ in self.SESSIONS:
            result = parse_notification(bytes.fromhex(raw_hex))
            assert result["last_brush_duration"] > 0
