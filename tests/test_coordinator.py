"""Tests for coordinator.py – BleakClient is fully mocked."""
from __future__ import annotations

from datetime import time as dtime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# conftest.py stubs HA + bleak before these imports
from custom_components.oclean_ble.coordinator import (
    OcleanCoordinator,
    _in_window,
    _parse_poll_windows,
)
from custom_components.oclean_ble.const import (
    DATA_BATTERY,
    DATA_LAST_BRUSH_SCORE,
)


def _make_service_info(mac="AA:BB:CC:DD:EE:FF"):
    """Return a mock BluetoothServiceInfoBleak with a .device attribute."""
    device = MagicMock()
    device.address = mac
    service_info = MagicMock()
    service_info.device = device
    return service_info


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_hass():
    hass = MagicMock()
    hass.data = {}
    return hass


def _make_coordinator(hass=None, mac="AA:BB:CC:DD:EE:FF", poll_interval=300):
    hass = hass or _make_hass()
    return OcleanCoordinator(hass, mac, "Oclean", poll_interval)


def _make_bleak_client(battery_value=75):
    """Return a fully mocked BleakClient."""
    client = AsyncMock()
    client.is_connected = True
    client.write_gatt_char = AsyncMock()
    client.start_notify = AsyncMock()
    client.stop_notify = AsyncMock()
    client.disconnect = AsyncMock()
    client.read_gatt_char = AsyncMock(return_value=bytearray([battery_value]))
    return client


# ---------------------------------------------------------------------------
# _poll_device – success path
# ---------------------------------------------------------------------------

def _bt_no_device(bt_mock):
    """Both HA bluetooth lookups return None → BLEDevice stub path."""
    bt_mock.async_last_service_info.return_value = None
    bt_mock.async_ble_device_from_address.return_value = None


class TestPollDeviceSuccess:
    def _patch_bt_with_device(self, bt_mock):
        """Return a service_info mock and configure bt_mock to return it."""
        si = _make_service_info()
        bt_mock.async_last_service_info.return_value = si
        return si

    @pytest.mark.asyncio
    async def test_battery_read_on_success(self):
        coordinator = _make_coordinator()
        client = _make_bleak_client(battery_value=82)

        with patch("custom_components.oclean_ble.coordinator.bluetooth") as bt_mock, \
             patch("custom_components.oclean_ble.coordinator.establish_connection",
                   new_callable=AsyncMock, return_value=client):
            self._patch_bt_with_device(bt_mock)
            result = await coordinator._poll_device()

        assert result[DATA_BATTERY] == 82
        assert coordinator.last_poll_successful is True

    @pytest.mark.asyncio
    async def test_time_calibration_sent(self):
        coordinator = _make_coordinator()
        client = _make_bleak_client()

        with patch("custom_components.oclean_ble.coordinator.bluetooth") as bt_mock, \
             patch("custom_components.oclean_ble.coordinator.establish_connection",
                   new_callable=AsyncMock, return_value=client), \
             patch("custom_components.oclean_ble.coordinator.asyncio.sleep", new_callable=AsyncMock):
            self._patch_bt_with_device(bt_mock)
            await coordinator._poll_device()

        # First write_gatt_char call should be the time calibration (6 bytes: 020E + 4 timestamp)
        first_call_args = client.write_gatt_char.call_args_list[0]
        cmd_bytes = first_call_args[0][1]  # positional arg index 1
        assert cmd_bytes[:2] == bytes.fromhex("020E"), "First write must be time calibration"
        assert len(cmd_bytes) == 6, "Time calibration must be 6 bytes"

    @pytest.mark.asyncio
    async def test_status_query_sent(self):
        coordinator = _make_coordinator()
        client = _make_bleak_client()

        with patch("custom_components.oclean_ble.coordinator.bluetooth") as bt_mock, \
             patch("custom_components.oclean_ble.coordinator.establish_connection",
                   new_callable=AsyncMock, return_value=client), \
             patch("custom_components.oclean_ble.coordinator.asyncio.sleep", new_callable=AsyncMock):
            self._patch_bt_with_device(bt_mock)
            await coordinator._poll_device()

        # Second write_gatt_char call should be CMD_QUERY_STATUS (0303)
        second_call_args = client.write_gatt_char.call_args_list[1]
        cmd_bytes = second_call_args[0][1]
        assert cmd_bytes == bytes.fromhex("0303"), "Second write must be status query"

    @pytest.mark.asyncio
    async def test_disconnect_called_on_success(self):
        coordinator = _make_coordinator()
        client = _make_bleak_client()

        with patch("custom_components.oclean_ble.coordinator.bluetooth") as bt_mock, \
             patch("custom_components.oclean_ble.coordinator.establish_connection",
                   new_callable=AsyncMock, return_value=client), \
             patch("custom_components.oclean_ble.coordinator.asyncio.sleep", new_callable=AsyncMock):
            self._patch_bt_with_device(bt_mock)
            await coordinator._poll_device()

        client.disconnect.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_establish_connection_used_when_service_info_available(self):
        """When service_info is cached, establish_connection is called with service_info.device."""
        coordinator = _make_coordinator()
        client = _make_bleak_client(battery_value=70)
        service_info = _make_service_info()

        with patch("custom_components.oclean_ble.coordinator.bluetooth") as bt_mock, \
             patch("custom_components.oclean_ble.coordinator.establish_connection",
                   new_callable=AsyncMock, return_value=client) as ec_mock, \
             patch("custom_components.oclean_ble.coordinator.asyncio.sleep", new_callable=AsyncMock):
            bt_mock.async_last_service_info.return_value = service_info

            result = await coordinator._poll_device()

        ec_mock.assert_awaited_once()
        assert ec_mock.call_args[0][1] is service_info.device, \
            "establish_connection must receive service_info.device (BLEDevice), not service_info"
        assert result[DATA_BATTERY] == 70

    @pytest.mark.asyncio
    async def test_bleak_error_raised_when_device_not_in_ha_registry(self):
        """When device is completely unknown to HA (no scanner has seen it),
        a clear BleakError is raised instead of crashing with IndexError."""
        from bleak import BleakError

        coordinator = _make_coordinator(mac="AA:BB:CC:DD:EE:FF")

        with patch("custom_components.oclean_ble.coordinator.bluetooth") as bt_mock:
            _bt_no_device(bt_mock)
            with pytest.raises(BleakError, match="not found in HA bluetooth registry"):
                await coordinator._poll_device()

    @pytest.mark.asyncio
    async def test_async_last_service_info_called_with_connectable(self):
        """async_last_service_info must be called with connectable=True first."""
        coordinator = _make_coordinator()
        client = _make_bleak_client()

        with patch("custom_components.oclean_ble.coordinator.bluetooth") as bt_mock, \
             patch("custom_components.oclean_ble.coordinator.establish_connection",
                   new_callable=AsyncMock, return_value=client), \
             patch("custom_components.oclean_ble.coordinator.asyncio.sleep", new_callable=AsyncMock):
            self._patch_bt_with_device(bt_mock)
            await coordinator._poll_device()

        calls = bt_mock.async_last_service_info.call_args_list
        assert any(c == ((coordinator.hass, coordinator._mac), {"connectable": True})
                   for c in calls), "connectable=True call must be present"

    @pytest.mark.asyncio
    async def test_notification_data_merged_into_result(self):
        coordinator = _make_coordinator()
        client = _make_bleak_client(battery_value=50)

        async def fake_sleep(_):
            pass

        with patch("custom_components.oclean_ble.coordinator.bluetooth") as bt_mock, \
             patch("custom_components.oclean_ble.coordinator.establish_connection",
                   new_callable=AsyncMock, return_value=client), \
             patch("custom_components.oclean_ble.coordinator.asyncio.sleep", side_effect=fake_sleep):
            self._patch_bt_with_device(bt_mock)
            result = await coordinator._poll_device()

        assert DATA_BATTERY in result


# ---------------------------------------------------------------------------
# _poll_device – failure path
# ---------------------------------------------------------------------------

class TestPollDeviceFailure:
    @pytest.mark.asyncio
    async def test_bleak_error_raises_update_failed_on_first_poll(self):
        from bleak import BleakError
        from homeassistant.helpers.update_coordinator import UpdateFailed

        coordinator = _make_coordinator()

        with patch("custom_components.oclean_ble.coordinator.bluetooth") as bt_mock, \
             patch("custom_components.oclean_ble.coordinator.establish_connection",
                   new_callable=AsyncMock, side_effect=BleakError("timeout")):
            _bt_no_device(bt_mock)
            with pytest.raises(UpdateFailed):
                await coordinator._async_update_data()

    @pytest.mark.asyncio
    async def test_bleak_error_returns_stale_data_after_first_success(self):
        from bleak import BleakError

        coordinator = _make_coordinator()
        coordinator._last_raw = {DATA_BATTERY: 55}

        with patch("custom_components.oclean_ble.coordinator.bluetooth") as bt_mock, \
             patch("custom_components.oclean_ble.coordinator.establish_connection",
                   new_callable=AsyncMock, side_effect=BleakError("device gone")):
            _bt_no_device(bt_mock)
            result = await coordinator._async_update_data()

        assert result.battery == 55
        assert result.is_brushing is False
        assert coordinator.last_poll_successful is False

    @pytest.mark.asyncio
    async def test_disconnect_called_even_on_unexpected_error(self):
        """disconnect runs in finally even when asyncio.sleep raises unexpectedly."""
        coordinator = _make_coordinator()
        client = _make_bleak_client()

        with patch("custom_components.oclean_ble.coordinator.bluetooth") as bt_mock, \
             patch("custom_components.oclean_ble.coordinator.establish_connection",
                   new_callable=AsyncMock, return_value=client), \
             patch(
                 "custom_components.oclean_ble.coordinator.asyncio.sleep",
                 side_effect=RuntimeError("unexpected"),
             ):
            bt_mock.async_last_service_info.return_value = _make_service_info()
            client.connect = AsyncMock()

            with pytest.raises(RuntimeError):
                await coordinator._poll_device()

        client.disconnect.assert_awaited_once()


# ---------------------------------------------------------------------------
# Data persistence across polls
# ---------------------------------------------------------------------------

class TestDataPersistence:
    @pytest.mark.asyncio
    async def test_persistent_keys_kept_across_polls(self):
        coordinator = _make_coordinator()
        coordinator._last_raw = {
            DATA_BATTERY: 60,
            DATA_LAST_BRUSH_SCORE: 88,
        }
        client = _make_bleak_client(battery_value=61)

        with patch("custom_components.oclean_ble.coordinator.bluetooth") as bt_mock, \
             patch("custom_components.oclean_ble.coordinator.establish_connection",
                   new_callable=AsyncMock, return_value=client), \
             patch("custom_components.oclean_ble.coordinator.asyncio.sleep", new_callable=AsyncMock):
            bt_mock.async_last_service_info.return_value = _make_service_info()
            result = await coordinator._poll_device()

        # Fresh battery overrides old; old score is carried forward
        assert result[DATA_BATTERY] == 61
        assert result[DATA_LAST_BRUSH_SCORE] == 88


# ---------------------------------------------------------------------------
# _parse_poll_windows
# ---------------------------------------------------------------------------

class TestParsePollWindows:
    def test_empty_string_returns_empty_list(self):
        assert _parse_poll_windows("") == []

    def test_single_valid_window(self):
        result = _parse_poll_windows("07:00-07:45")
        assert result == [(dtime(7, 0), dtime(7, 45))]

    def test_two_valid_windows(self):
        result = _parse_poll_windows("07:00-07:45, 21:30-22:00")
        assert result == [
            (dtime(7, 0), dtime(7, 45)),
            (dtime(21, 30), dtime(22, 0)),
        ]

    def test_three_valid_windows(self):
        result = _parse_poll_windows("06:00-06:30, 12:00-12:15, 22:00-22:30")
        assert len(result) == 3

    def test_max_three_windows_enforced(self):
        result = _parse_poll_windows("06:00-06:30, 12:00-12:15, 22:00-22:30, 23:00-23:30")
        assert len(result) == 3

    def test_invalid_entry_skipped(self):
        result = _parse_poll_windows("badentry, 07:00-07:45")
        assert result == [(dtime(7, 0), dtime(7, 45))]

    def test_same_start_end_skipped(self):
        result = _parse_poll_windows("07:00-07:00")
        assert result == []

    def test_overnight_window_preserved(self):
        result = _parse_poll_windows("23:00-01:00")
        assert result == [(dtime(23, 0), dtime(1, 0))]

    def test_whitespace_tolerance(self):
        result = _parse_poll_windows("  07:00-07:45  ,  21:30-22:00  ")
        assert result == [
            (dtime(7, 0), dtime(7, 45)),
            (dtime(21, 30), dtime(22, 0)),
        ]


# ---------------------------------------------------------------------------
# _in_window
# ---------------------------------------------------------------------------

class TestInWindow:
    def test_inside_normal_window(self):
        assert _in_window(dtime(7, 0), dtime(8, 0), dtime(7, 30)) is True

    def test_at_start_of_window(self):
        assert _in_window(dtime(7, 0), dtime(8, 0), dtime(7, 0)) is True

    def test_at_end_of_window(self):
        assert _in_window(dtime(7, 0), dtime(8, 0), dtime(8, 0)) is True

    def test_before_window(self):
        assert _in_window(dtime(7, 0), dtime(8, 0), dtime(6, 59)) is False

    def test_after_window(self):
        assert _in_window(dtime(7, 0), dtime(8, 0), dtime(8, 1)) is False

    def test_overnight_inside_after_midnight(self):
        assert _in_window(dtime(23, 0), dtime(1, 0), dtime(0, 30)) is True

    def test_overnight_inside_before_midnight(self):
        assert _in_window(dtime(23, 0), dtime(1, 0), dtime(23, 30)) is True

    def test_overnight_outside(self):
        assert _in_window(dtime(23, 0), dtime(1, 0), dtime(12, 0)) is False
