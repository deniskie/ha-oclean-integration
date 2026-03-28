"""Tests for config_flow.py – validates schema logic and MAC validation."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

# conftest.py stubs HA before these imports
from custom_components.oclean_ble.config_flow import (
    _MAC_RE,
    OcleanConfigFlow,
    OcleanOptionsFlow,
    _parse_windows_list,
    _windows_list_to_str,
)
from custom_components.oclean_ble.const import (
    CONF_MAC_ADDRESS,
    CONF_POLL_INTERVAL,
    CONF_POLL_WINDOWS,
    CONF_POST_BRUSH_COOLDOWN,
    CONF_WINDOW_COUNT,
    CONF_WINDOW_END,
    CONF_WINDOW_START,
    DEFAULT_POLL_INTERVAL,
    DEFAULT_POST_BRUSH_COOLDOWN,
    MIN_POLL_INTERVAL,
)

# ---------------------------------------------------------------------------
# MAC address regex validation
# ---------------------------------------------------------------------------


class TestMacAddressRegex:
    VALID = [
        "AA:BB:CC:DD:EE:FF",
        "00:11:22:33:44:55",
        "a0:b1:c2:d3:e4:f5",  # lowercase
        "A0:B1:C2:D3:E4:F5",  # uppercase
    ]
    INVALID = [
        "AA:BB:CC:DD:EE",  # only 5 octets
        "AA:BB:CC:DD:EE:FF:00",  # 7 octets
        "AABBCCDDEEFF",  # no colons
        "AA-BB-CC-DD-EE-FF",  # hyphens
        "GG:BB:CC:DD:EE:FF",  # invalid hex
        "",
        "AA:BB:CC:DD:EE:GG",
        "AA:BB:CC:DD:EE:F",  # too short last octet
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


# ---------------------------------------------------------------------------
# Poll interval gap validation in config flow steps
# ---------------------------------------------------------------------------


class TestPollIntervalGapValidationInFlowSteps:
    """Values in (0, MIN_POLL_INTERVAL) must be rejected in config-flow steps,
    and NumberSelector floats must be stored as int."""

    def _make_flow(self) -> OcleanConfigFlow:
        flow = OcleanConfigFlow()
        flow._mac = "AA:BB:CC:DD:EE:FF"
        flow._name = "Oclean"
        flow.async_create_entry = MagicMock(return_value={"type": "create_entry"})
        flow.async_show_form = MagicMock(return_value={"type": "form"})
        flow.async_set_unique_id = AsyncMock()
        flow._abort_if_unique_id_configured = MagicMock()
        return flow

    @pytest.mark.parametrize(
        ("value", "expect_error"),
        [
            (0, False),  # manual mode – allowed
            (1, True),  # 1 s – in gap
            (MIN_POLL_INTERVAL - 1, True),  # 59 s – in gap
            (MIN_POLL_INTERVAL, False),  # 60 s – minimum allowed
            (300, False),  # default – allowed
        ],
    )
    def test_confirm_validates_gap(self, value, expect_error):
        flow = self._make_flow()
        asyncio.run(flow.async_step_confirm({CONF_POLL_INTERVAL: value}))
        if expect_error:
            errors = flow.async_show_form.call_args.kwargs["errors"]
            assert errors.get(CONF_POLL_INTERVAL) == "invalid_poll_interval"
            flow.async_create_entry.assert_not_called()
        else:
            flow.async_create_entry.assert_called_once()

    @pytest.mark.parametrize(
        ("value", "expect_error"),
        [
            (0, False),
            (30, True),
            (MIN_POLL_INTERVAL, False),
        ],
    )
    def test_manual_validates_gap(self, value, expect_error):
        flow = self._make_flow()
        asyncio.run(flow.async_step_manual({CONF_MAC_ADDRESS: "AA:BB:CC:DD:EE:FF", CONF_POLL_INTERVAL: value}))
        if expect_error:
            errors = flow.async_show_form.call_args.kwargs["errors"]
            assert errors.get(CONF_POLL_INTERVAL) == "invalid_poll_interval"
            flow.async_create_entry.assert_not_called()
        else:
            flow.async_create_entry.assert_called_once()

    def test_confirm_stores_integer_not_float(self):
        """NumberSelector returns floats – verify the int() cast stores an int."""
        flow = self._make_flow()
        asyncio.run(flow.async_step_confirm({CONF_POLL_INTERVAL: 300.0}))
        data = flow.async_create_entry.call_args.kwargs["data"]
        assert isinstance(data[CONF_POLL_INTERVAL], int)
        assert data[CONF_POLL_INTERVAL] == 300


# ---------------------------------------------------------------------------
# _parse_windows_list edge cases (ValueError path, lines 61-62)
# ---------------------------------------------------------------------------


class TestParseWindowsEdgeCases:
    """Malformed entries that trigger the ValueError/AttributeError catch."""

    def test_non_numeric_hour(self):
        assert _parse_windows_list("aa:bb-cc:dd") == []

    def test_partial_time_missing_minute(self):
        assert _parse_windows_list("07-09") == []

    def test_empty_parts_around_dash(self):
        assert _parse_windows_list("-") == []

    def test_extra_colons(self):
        assert _parse_windows_list("07:00:00-09:00:00") == []

    def test_mixed_valid_and_malformed(self):
        result = _parse_windows_list("aa:bb-cc:dd, 07:00-09:00, xx:yy-zz:ww")
        assert result == [("07:00:00", "09:00:00")]

    def test_none_input(self):
        assert _parse_windows_list(None) == []


# ---------------------------------------------------------------------------
# OcleanOptionsFlow (lines 251, 264-305, 340-377)
# ---------------------------------------------------------------------------


class TestOcleanOptionsFlow:
    def _make_flow(self) -> OcleanOptionsFlow:
        flow = OcleanOptionsFlow()
        entry = MagicMock()
        entry.options = {
            CONF_POLL_INTERVAL: DEFAULT_POLL_INTERVAL,
            CONF_POST_BRUSH_COOLDOWN: DEFAULT_POST_BRUSH_COOLDOWN,
            CONF_POLL_WINDOWS: "",
        }
        entry.data = {CONF_POLL_INTERVAL: DEFAULT_POLL_INTERVAL}
        flow.config_entry = entry
        flow.async_create_entry = MagicMock(return_value={"type": "create_entry"})
        flow.async_show_form = MagicMock(return_value={"type": "form"})
        return flow

    def test_init_no_input_shows_form(self):
        flow = self._make_flow()
        asyncio.run(flow.async_step_init(None))
        flow.async_show_form.assert_called_once()
        assert flow.async_show_form.call_args.kwargs["step_id"] == "init"

    def test_init_valid_input_zero_windows_creates_entry(self):
        flow = self._make_flow()
        asyncio.run(
            flow.async_step_init(
                {
                    CONF_POLL_INTERVAL: 300,
                    CONF_POST_BRUSH_COOLDOWN: 2,
                    CONF_WINDOW_COUNT: 0,
                }
            )
        )
        flow.async_create_entry.assert_called_once()
        data = flow.async_create_entry.call_args.kwargs["data"]
        assert data[CONF_POLL_INTERVAL] == 300
        assert data[CONF_POST_BRUSH_COOLDOWN] == 2
        assert data[CONF_POLL_WINDOWS] == ""

    def test_init_invalid_poll_interval_shows_error(self):
        flow = self._make_flow()
        asyncio.run(
            flow.async_step_init(
                {
                    CONF_POLL_INTERVAL: 30,
                    CONF_POST_BRUSH_COOLDOWN: 0,
                    CONF_WINDOW_COUNT: 0,
                }
            )
        )
        flow.async_show_form.assert_called_once()
        errors = flow.async_show_form.call_args.kwargs["errors"]
        assert errors[CONF_POLL_INTERVAL] == "invalid_poll_interval"
        flow.async_create_entry.assert_not_called()

    def test_init_with_windows_calls_window_step(self):
        flow = self._make_flow()
        flow.async_step_window_1 = AsyncMock(return_value={"type": "form"})
        asyncio.run(
            flow.async_step_init(
                {
                    CONF_POLL_INTERVAL: 300,
                    CONF_POST_BRUSH_COOLDOWN: 0,
                    CONF_WINDOW_COUNT: 1,
                }
            )
        )
        flow.async_step_window_1.assert_called_once()
        flow.async_create_entry.assert_not_called()

    def test_window_step_valid_input_creates_entry(self):
        flow = self._make_flow()
        flow._poll_interval = 300
        flow._cooldown = 0
        flow._window_count = 1
        flow._collected_windows = []
        asyncio.run(
            flow._async_step_window(
                1,
                {
                    CONF_WINDOW_START: "07:00:00",
                    CONF_WINDOW_END: "09:00:00",
                },
            )
        )
        flow.async_create_entry.assert_called_once()
        data = flow.async_create_entry.call_args.kwargs["data"]
        assert data[CONF_POLL_WINDOWS] == "07:00-09:00"

    def test_window_step_missing_start_shows_error(self):
        flow = self._make_flow()
        flow._poll_interval = 300
        flow._cooldown = 0
        flow._window_count = 1
        flow._collected_windows = []
        flow._existing_windows = []
        asyncio.run(
            flow._async_step_window(
                1,
                {
                    CONF_WINDOW_START: "",
                    CONF_WINDOW_END: "09:00:00",
                },
            )
        )
        flow.async_show_form.assert_called_once()
        errors = flow.async_show_form.call_args.kwargs["errors"]
        assert errors[CONF_WINDOW_START] == "window_incomplete"

    def test_window_step_missing_end_shows_error(self):
        flow = self._make_flow()
        flow._poll_interval = 300
        flow._cooldown = 0
        flow._window_count = 1
        flow._collected_windows = []
        flow._existing_windows = []
        asyncio.run(
            flow._async_step_window(
                1,
                {
                    CONF_WINDOW_START: "07:00:00",
                    CONF_WINDOW_END: "",
                },
            )
        )
        flow.async_show_form.assert_called_once()
        errors = flow.async_show_form.call_args.kwargs["errors"]
        assert errors[CONF_WINDOW_END] == "window_incomplete"

    def test_window_step_no_input_shows_form(self):
        flow = self._make_flow()
        flow._existing_windows = [("07:00:00", "09:00:00")]
        asyncio.run(flow._async_step_window(1, None))
        flow.async_show_form.assert_called_once()
        assert flow.async_show_form.call_args.kwargs["step_id"] == "window_1"

    def test_window_delegates(self):
        """async_step_window_1/2/3 delegate to _async_step_window."""
        flow = self._make_flow()
        flow._existing_windows = []
        asyncio.run(flow.async_step_window_1(None))
        assert flow.async_show_form.call_args.kwargs["step_id"] == "window_1"

        flow2 = self._make_flow()
        flow2._existing_windows = []
        asyncio.run(flow2.async_step_window_2(None))
        assert flow2.async_show_form.call_args.kwargs["step_id"] == "window_2"

        flow3 = self._make_flow()
        flow3._existing_windows = []
        asyncio.run(flow3.async_step_window_3(None))
        assert flow3.async_show_form.call_args.kwargs["step_id"] == "window_3"

    def test_multi_window_chains(self):
        """window_count=2: first window calls window_2, second creates entry."""
        flow = self._make_flow()
        flow._poll_interval = 300
        flow._cooldown = 0
        flow._window_count = 2
        flow._collected_windows = []
        flow._existing_windows = []
        flow.async_step_window_2 = AsyncMock(return_value={"type": "form"})
        asyncio.run(
            flow._async_step_window(
                1,
                {
                    CONF_WINDOW_START: "07:00:00",
                    CONF_WINDOW_END: "09:00:00",
                },
            )
        )
        flow.async_step_window_2.assert_called_once()
        flow.async_create_entry.assert_not_called()


# ---------------------------------------------------------------------------
# Pick device step (lines 157-180)
# ---------------------------------------------------------------------------


class TestPickDeviceStep:
    def _make_flow(self) -> OcleanConfigFlow:
        flow = OcleanConfigFlow()
        flow._discovered_devices = {"AA:BB:CC:DD:EE:FF": "Oclean X"}
        flow.async_create_entry = MagicMock(return_value={"type": "create_entry"})
        flow.async_show_form = MagicMock(return_value={"type": "form"})
        flow.async_set_unique_id = AsyncMock()
        flow._abort_if_unique_id_configured = MagicMock()
        return flow

    def test_pick_device_valid_creates_entry(self):
        flow = self._make_flow()
        asyncio.run(
            flow.async_step_pick_device(
                {
                    CONF_MAC_ADDRESS: "AA:BB:CC:DD:EE:FF",
                    CONF_POLL_INTERVAL: 300,
                }
            )
        )
        flow.async_create_entry.assert_called_once()
        data = flow.async_create_entry.call_args.kwargs["data"]
        assert data[CONF_MAC_ADDRESS] == "AA:BB:CC:DD:EE:FF"
        assert data[CONF_POLL_INTERVAL] == 300

    def test_pick_device_invalid_poll_interval_shows_error(self):
        flow = self._make_flow()
        asyncio.run(
            flow.async_step_pick_device(
                {
                    CONF_MAC_ADDRESS: "AA:BB:CC:DD:EE:FF",
                    CONF_POLL_INTERVAL: 30,
                }
            )
        )
        flow.async_show_form.assert_called_once()
        errors = flow.async_show_form.call_args.kwargs["errors"]
        assert errors[CONF_POLL_INTERVAL] == "invalid_poll_interval"
        flow.async_create_entry.assert_not_called()

    def test_pick_device_no_input_shows_form(self):
        flow = self._make_flow()
        asyncio.run(flow.async_step_pick_device(None))
        flow.async_show_form.assert_called_once()
        assert flow.async_show_form.call_args.kwargs["step_id"] == "pick_device"


# ---------------------------------------------------------------------------
# Bluetooth step (lines 93-100)
# ---------------------------------------------------------------------------


class TestBluetoothStep:
    def _make_flow(self) -> OcleanConfigFlow:
        flow = OcleanConfigFlow()
        flow.async_set_unique_id = AsyncMock()
        flow._abort_if_unique_id_configured = MagicMock()
        flow.async_step_confirm = AsyncMock(return_value={"type": "form"})
        flow.context = {}
        return flow

    def test_bluetooth_step_sets_mac_and_name(self):
        flow = self._make_flow()
        discovery = MagicMock()
        discovery.address = "AA:BB:CC:DD:EE:FF"
        discovery.name = "Oclean X Pro"
        asyncio.run(flow.async_step_bluetooth(discovery))
        assert flow._mac == "AA:BB:CC:DD:EE:FF"
        assert flow._name == "Oclean X Pro"
        flow.async_set_unique_id.assert_called_once_with("AA:BB:CC:DD:EE:FF")
        flow._abort_if_unique_id_configured.assert_called_once()
        flow.async_step_confirm.assert_called_once()

    def test_bluetooth_step_defaults_name(self):
        flow = self._make_flow()
        discovery = MagicMock()
        discovery.address = "11:22:33:44:55:66"
        discovery.name = ""
        asyncio.run(flow.async_step_bluetooth(discovery))
        assert flow._name == "Oclean"

    def test_bluetooth_step_sets_title_placeholders(self):
        flow = self._make_flow()
        discovery = MagicMock()
        discovery.address = "AA:BB:CC:DD:EE:FF"
        discovery.name = "MyBrush"
        asyncio.run(flow.async_step_bluetooth(discovery))
        assert flow.context["title_placeholders"] == {"name": "MyBrush"}


# ---------------------------------------------------------------------------
# async_get_options_flow (line 251)
# ---------------------------------------------------------------------------


class TestAsyncGetOptionsFlow:
    def test_returns_options_flow_instance(self):
        entry = MagicMock()
        result = OcleanConfigFlow.async_get_options_flow(entry)
        assert isinstance(result, OcleanOptionsFlow)


# ---------------------------------------------------------------------------
# async_step_user with discovered devices (lines 145-153)
# ---------------------------------------------------------------------------


class TestUserStepWithDiscovery:
    def test_user_step_with_discovered_devices_goes_to_pick(self):
        flow = OcleanConfigFlow()
        flow.hass = MagicMock()

        from custom_components.oclean_ble import config_flow as cf_module
        from custom_components.oclean_ble.const import OCLEAN_SERVICE_UUID

        fake_info = MagicMock()
        fake_info.service_uuids = [OCLEAN_SERVICE_UUID]
        fake_info.address = "AA:BB:CC:DD:EE:FF"
        fake_info.name = "Oclean X"

        flow.async_step_pick_device = AsyncMock(return_value={"type": "form"})
        flow.async_step_manual = AsyncMock(return_value={"type": "form"})

        original_fn = cf_module.bluetooth.async_discovered_service_info
        cf_module.bluetooth.async_discovered_service_info = MagicMock(return_value=[fake_info])
        try:
            asyncio.run(flow.async_step_user(None))
            flow.async_step_pick_device.assert_called_once()
            flow.async_step_manual.assert_not_called()
        finally:
            cf_module.bluetooth.async_discovered_service_info = original_fn
