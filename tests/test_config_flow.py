"""Tests for config_flow.py – validates schema logic and MAC validation."""
from __future__ import annotations

import pytest

# conftest.py stubs HA before these imports
from custom_components.oclean_ble.config_flow import (
    _MAC_RE,
    _parse_windows_list,
    _windows_list_to_str,
)
from custom_components.oclean_ble.const import (
    DEFAULT_POLL_INTERVAL,
    MIN_POLL_INTERVAL,
)


# ---------------------------------------------------------------------------
# MAC address regex validation
# ---------------------------------------------------------------------------

class TestMacAddressRegex:
    VALID = [
        "AA:BB:CC:DD:EE:FF",
        "00:11:22:33:44:55",
        "a0:b1:c2:d3:e4:f5",   # lowercase
        "A0:B1:C2:D3:E4:F5",   # uppercase
    ]
    INVALID = [
        "AA:BB:CC:DD:EE",          # only 5 octets
        "AA:BB:CC:DD:EE:FF:00",    # 7 octets
        "AABBCCDDEEFF",            # no colons
        "AA-BB-CC-DD-EE-FF",       # hyphens
        "GG:BB:CC:DD:EE:FF",       # invalid hex
        "",
        "AA:BB:CC:DD:EE:GG",
        "AA:BB:CC:DD:EE:F",        # too short last octet
    ]

    @pytest.mark.parametrize("mac", VALID)
    def test_valid_macs(self, mac):
        assert _MAC_RE.match(mac), f"Expected {mac!r} to be valid"

    @pytest.mark.parametrize("mac", INVALID)
    def test_invalid_macs(self, mac):
        assert not _MAC_RE.match(mac), f"Expected {mac!r} to be invalid"


# ---------------------------------------------------------------------------
# Poll interval constants
# ---------------------------------------------------------------------------

class TestPollIntervalConstants:
    def test_default_is_5_minutes(self):
        assert DEFAULT_POLL_INTERVAL == 300

    def test_minimum_is_1_minute(self):
        assert MIN_POLL_INTERVAL == 60

    def test_default_ge_minimum(self):
        assert DEFAULT_POLL_INTERVAL >= MIN_POLL_INTERVAL


# ---------------------------------------------------------------------------
# Config flow input normalization
# ---------------------------------------------------------------------------

class TestMacNormalization:
    """The config flow calls .upper().strip() on the MAC before validation."""

    def test_lowercase_mac_normalizes(self):
        raw = "aa:bb:cc:dd:ee:ff"
        normalized = raw.upper().strip()
        assert _MAC_RE.match(normalized)

    def test_whitespace_stripped(self):
        raw = "  AA:BB:CC:DD:EE:FF  "
        normalized = raw.upper().strip()
        assert _MAC_RE.match(normalized)

    def test_mixed_case_normalizes(self):
        raw = "Aa:Bb:Cc:Dd:Ee:Ff"
        normalized = raw.upper().strip()
        assert _MAC_RE.match(normalized)


# ---------------------------------------------------------------------------
# Poll window helpers
# ---------------------------------------------------------------------------

class TestParseWindowsList:
    def test_empty_string_returns_empty(self):
        assert _parse_windows_list("") == []

    def test_single_window(self):
        assert _parse_windows_list("07:00-09:00") == [("07:00:00", "09:00:00")]

    def test_two_windows(self):
        result = _parse_windows_list("07:00-09:00, 20:00-22:30")
        assert result == [("07:00:00", "09:00:00"), ("20:00:00", "22:30:00")]

    def test_three_windows(self):
        result = _parse_windows_list("06:00-07:00, 12:00-13:00, 21:00-22:00")
        assert len(result) == 3

    def test_honours_max_three(self):
        result = _parse_windows_list("06:00-07:00, 08:00-09:00, 10:00-11:00, 12:00-13:00")
        assert len(result) == 3

    def test_invalid_entry_skipped(self):
        result = _parse_windows_list("notawindow, 07:00-09:00")
        assert result == [("07:00:00", "09:00:00")]

    def test_whitespace_tolerant(self):
        assert _parse_windows_list("  07:00 - 09:00  ") == [("07:00:00", "09:00:00")]


class TestWindowsListToStr:
    def test_empty_list(self):
        assert _windows_list_to_str([]) == ""

    def test_single_window(self):
        assert _windows_list_to_str([("07:00:00", "09:00:00")]) == "07:00-09:00"

    def test_two_windows(self):
        result = _windows_list_to_str([("07:00:00", "09:00:00"), ("20:00:00", "22:30:00")])
        assert result == "07:00-09:00, 20:00-22:30"

    def test_strips_seconds(self):
        assert _windows_list_to_str([("07:30:45", "09:15:00")]) == "07:30-09:15"

    def test_equal_start_end_skipped(self):
        assert _windows_list_to_str([("07:00:00", "07:00:00")]) == ""

    def test_roundtrip(self):
        original = "07:00-09:00, 20:00-22:30"
        assert _windows_list_to_str(_parse_windows_list(original)) == original
