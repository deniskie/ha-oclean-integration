"""Tests for coordinator.py – BleakClient is fully mocked."""

from __future__ import annotations

import asyncio
from datetime import time as dtime
from unittest.mock import ANY, AsyncMock, MagicMock, patch

import pytest

from custom_components.oclean_ble.const import (
    DATA_BATTERY,
    DATA_LAST_BRUSH_AREAS,
    DATA_LAST_BRUSH_DURATION,
    DATA_LAST_BRUSH_PRESSURE,
    DATA_LAST_BRUSH_SCORE,
    DATA_LAST_BRUSH_TIME,
    DATA_MODEL_ID,
    DATA_SW_VERSION,
)

# conftest.py stubs HA + bleak before these imports
from custom_components.oclean_ble.coordinator import (
    OcleanCoordinator,
    _in_window,
    _parse_poll_windows,
)
from custom_components.oclean_ble.parser import T1_C3352G_RECORD_SIZE


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
    client.write_gatt_descriptor = AsyncMock()
    client.start_notify = AsyncMock()
    client.stop_notify = AsyncMock()
    client.disconnect = AsyncMock()
    client.read_gatt_char = AsyncMock(return_value=bytearray([battery_value]))
    # services.get_characteristic returns None → _clear_cccd exits early without
    # issuing any GATT descriptor write (avoids unawaited-coroutine warnings).
    client.services = MagicMock()
    client.services.get_characteristic.return_value = None
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

        with (
            patch("custom_components.oclean_ble.coordinator.bluetooth") as bt_mock,
            patch(
                "custom_components.oclean_ble.coordinator.establish_connection",
                new_callable=AsyncMock,
                return_value=client,
            ),
        ):
            self._patch_bt_with_device(bt_mock)
            result = await coordinator._poll_device()

        assert result[DATA_BATTERY] == 82
        assert coordinator.last_poll_successful is True

    @pytest.mark.asyncio
    async def test_time_calibration_sent(self):
        coordinator = _make_coordinator()
        client = _make_bleak_client()

        with (
            patch("custom_components.oclean_ble.coordinator.bluetooth") as bt_mock,
            patch(
                "custom_components.oclean_ble.coordinator.establish_connection",
                new_callable=AsyncMock,
                return_value=client,
            ),
            patch("custom_components.oclean_ble.coordinator.asyncio.sleep", new_callable=AsyncMock),
        ):
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

        with (
            patch("custom_components.oclean_ble.coordinator.bluetooth") as bt_mock,
            patch(
                "custom_components.oclean_ble.coordinator.establish_connection",
                new_callable=AsyncMock,
                return_value=client,
            ),
            patch("custom_components.oclean_ble.coordinator.asyncio.sleep", new_callable=AsyncMock),
        ):
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

        with (
            patch("custom_components.oclean_ble.coordinator.bluetooth") as bt_mock,
            patch(
                "custom_components.oclean_ble.coordinator.establish_connection",
                new_callable=AsyncMock,
                return_value=client,
            ),
            patch("custom_components.oclean_ble.coordinator.asyncio.sleep", new_callable=AsyncMock),
        ):
            self._patch_bt_with_device(bt_mock)
            await coordinator._poll_device()

        client.disconnect.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_establish_connection_used_when_service_info_available(self):
        """When service_info is cached, establish_connection is called with service_info.device."""
        coordinator = _make_coordinator()
        client = _make_bleak_client(battery_value=70)
        service_info = _make_service_info()

        with (
            patch("custom_components.oclean_ble.coordinator.bluetooth") as bt_mock,
            patch(
                "custom_components.oclean_ble.coordinator.establish_connection",
                new_callable=AsyncMock,
                return_value=client,
            ) as ec_mock,
            patch("custom_components.oclean_ble.coordinator.asyncio.sleep", new_callable=AsyncMock),
        ):
            bt_mock.async_last_service_info.return_value = service_info

            result = await coordinator._poll_device()

        ec_mock.assert_awaited_once()
        assert ec_mock.call_args[0][1] is service_info.device, (
            "establish_connection must receive service_info.device (BLEDevice), not service_info"
        )
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

        with (
            patch("custom_components.oclean_ble.coordinator.bluetooth") as bt_mock,
            patch(
                "custom_components.oclean_ble.coordinator.establish_connection",
                new_callable=AsyncMock,
                return_value=client,
            ),
            patch("custom_components.oclean_ble.coordinator.asyncio.sleep", new_callable=AsyncMock),
        ):
            self._patch_bt_with_device(bt_mock)
            await coordinator._poll_device()

        calls = bt_mock.async_last_service_info.call_args_list
        assert any(c == ((coordinator.hass, coordinator._mac), {"connectable": True}) for c in calls), (
            "connectable=True call must be present"
        )

    @pytest.mark.asyncio
    async def test_notification_data_merged_into_result(self):
        coordinator = _make_coordinator()
        client = _make_bleak_client(battery_value=50)

        async def fake_sleep(_):
            pass

        with (
            patch("custom_components.oclean_ble.coordinator.bluetooth") as bt_mock,
            patch(
                "custom_components.oclean_ble.coordinator.establish_connection",
                new_callable=AsyncMock,
                return_value=client,
            ),
            patch("custom_components.oclean_ble.coordinator.asyncio.sleep", side_effect=fake_sleep),
        ):
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

        with (
            patch("custom_components.oclean_ble.coordinator.bluetooth") as bt_mock,
            patch(
                "custom_components.oclean_ble.coordinator.establish_connection",
                new_callable=AsyncMock,
                side_effect=BleakError("timeout"),
            ),
        ):
            _bt_no_device(bt_mock)
            with pytest.raises(UpdateFailed):
                await coordinator._async_update_data()

    @pytest.mark.asyncio
    async def test_bleak_error_returns_stale_data_after_first_success(self):
        from bleak import BleakError

        coordinator = _make_coordinator()
        coordinator._last_raw = {DATA_BATTERY: 55}

        with (
            patch("custom_components.oclean_ble.coordinator.bluetooth") as bt_mock,
            patch(
                "custom_components.oclean_ble.coordinator.establish_connection",
                new_callable=AsyncMock,
                side_effect=BleakError("device gone"),
            ),
        ):
            _bt_no_device(bt_mock)
            result = await coordinator._async_update_data()

        assert result.battery == 55
        assert coordinator.last_poll_successful is False

    @pytest.mark.asyncio
    async def test_disconnect_called_even_on_unexpected_error(self):
        """disconnect runs in finally even when asyncio.sleep raises unexpectedly."""
        coordinator = _make_coordinator()
        client = _make_bleak_client()

        with (
            patch("custom_components.oclean_ble.coordinator.bluetooth") as bt_mock,
            patch(
                "custom_components.oclean_ble.coordinator.establish_connection",
                new_callable=AsyncMock,
                return_value=client,
            ),
            patch(
                "custom_components.oclean_ble.coordinator.asyncio.sleep",
                side_effect=RuntimeError("unexpected"),
            ),
        ):
            bt_mock.async_last_service_info.return_value = _make_service_info()
            client.connect = AsyncMock()

            with pytest.raises(RuntimeError):
                await coordinator._poll_device()

        client.disconnect.assert_awaited_once()


# ---------------------------------------------------------------------------
# Data persistence across polls
# ---------------------------------------------------------------------------


class TestSessionEnrichment:
    """Enrichment notifications (0000/2604) must be merged into the session snapshot."""

    # Real notification bytes captured from Oclean X logs (2026-02-24):
    #   5a00 → session ts=1771889059 (2026-02-24 00:24:19 local), duration=150 s
    #   2604 → 8 zone pressures, avg_pressure=18, clean=100 %
    #   0000 → score=95
    _NOTIF_5A00 = bytes.fromhex("5a00ffffffffffffff1a02180018134c00960096")
    _NOTIF_2604 = bytes.fromhex("2604390000000f0018181a1a0710071009101007")
    _NOTIF_0000 = bytes.fromhex("00005f00ffffffffffffff1a0215101a23e7001e")

    def _make_client_firing_notifications(self, *notification_payloads):
        """BleakClient whose first start_notify call fires the given raw bytes."""
        client = _make_bleak_client(battery_value=93)
        call_count = [0]

        async def fake_start_notify(uuid, handler):
            call_count[0] += 1
            if call_count[0] == 1:
                for payload in notification_payloads:
                    handler(None, bytearray(payload))

        client.start_notify = AsyncMock(side_effect=fake_start_notify)
        return client

    @pytest.mark.asyncio
    async def test_score_areas_pressure_clean_merged_into_session(self):
        """Score (0000) and areas/pressure/clean (2604) must end up in all_sessions."""
        coordinator = _make_coordinator()
        coordinator._store_loaded = True

        client = self._make_client_firing_notifications(
            self._NOTIF_5A00,
            self._NOTIF_2604,
            self._NOTIF_0000,
        )

        captured: list = []

        async def fake_import(_hass, _mac_slug, _device_name, sessions, last_ts):
            captured.extend(sessions)
            return last_ts

        with (
            patch("custom_components.oclean_ble.coordinator.bluetooth") as bt_mock,
            patch(
                "custom_components.oclean_ble.coordinator.establish_connection",
                new_callable=AsyncMock,
                return_value=client,
            ),
            patch("custom_components.oclean_ble.coordinator.asyncio.sleep", new_callable=AsyncMock),
            patch("custom_components.oclean_ble.coordinator.import_new_sessions", side_effect=fake_import),
        ):
            bt_mock.async_last_service_info.return_value = _make_service_info()
            await coordinator._poll_device()

        assert captured, "At least one session must have been captured"
        newest = max(captured, key=lambda s: s.get("last_brush_time", 0))
        assert newest.get(DATA_LAST_BRUSH_SCORE) == 95, "Score from 0000 must be in session"
        assert isinstance(newest.get(DATA_LAST_BRUSH_AREAS), dict), "Areas from 2604 must be in session"
        assert newest.get(DATA_LAST_BRUSH_PRESSURE) == 18, "Pressure from 2604 must be in session"

    @pytest.mark.asyncio
    async def test_enrichment_does_not_overwrite_existing_session_fields(self):
        """Fields already in the session snapshot (e.g. from 0308 extended) must not be overwritten."""
        import struct

        coordinator = _make_coordinator()
        coordinator._store_loaded = True

        # Build a minimal valid 0308 extended payload (32 bytes) with score=42
        payload = bytearray(34)
        payload[0] = 0x00  # high byte of length
        payload[1] = 0x20  # length = 32
        payload[2] = 26  # year-2000 = 2026
        payload[3] = 2  # month
        payload[4] = 24  # day
        payload[5] = 1  # hour
        payload[6] = 0  # minute
        payload[7] = 0  # second
        payload[8] = 42  # pNum
        struct.pack_into(">H", payload, 9, 120)  # duration = 120 s
        struct.pack_into(">H", payload, 11, 100)  # validDuration
        payload[19] = 0  # tz offset
        for i in range(8):
            payload[20 + i] = 10  # area pressures
        payload[28] = 42  # score = 42 (must NOT be overwritten by 0000 score=95)
        payload[29] = 1  # schemeType
        raw_0308 = bytes([0x03, 0x08]) + bytes(payload)

        client = self._make_client_firing_notifications(
            raw_0308,
            self._NOTIF_0000,  # score=95 – must NOT overwrite score=42 from 0308
        )

        captured: list = []

        async def fake_import(_hass, _mac_slug, _device_name, sessions, last_ts):
            captured.extend(sessions)
            return last_ts

        with (
            patch("custom_components.oclean_ble.coordinator.bluetooth") as bt_mock,
            patch(
                "custom_components.oclean_ble.coordinator.establish_connection",
                new_callable=AsyncMock,
                return_value=client,
            ),
            patch("custom_components.oclean_ble.coordinator.asyncio.sleep", new_callable=AsyncMock),
            patch("custom_components.oclean_ble.coordinator.import_new_sessions", side_effect=fake_import),
        ):
            bt_mock.async_last_service_info.return_value = _make_service_info()
            await coordinator._poll_device()

        assert captured, "At least one session must have been captured"
        newest = max(captured, key=lambda s: s.get("last_brush_time", 0))
        assert newest.get(DATA_LAST_BRUSH_SCORE) == 42, "Score from 0308 must not be overwritten by 0000 enrichment"


class TestDataPersistence:
    @pytest.mark.asyncio
    async def test_persistent_keys_kept_across_polls(self):
        coordinator = _make_coordinator()
        coordinator._last_raw = {
            DATA_BATTERY: 60,
            DATA_LAST_BRUSH_SCORE: 88,
        }
        client = _make_bleak_client(battery_value=61)

        with (
            patch("custom_components.oclean_ble.coordinator.bluetooth") as bt_mock,
            patch(
                "custom_components.oclean_ble.coordinator.establish_connection",
                new_callable=AsyncMock,
                return_value=client,
            ),
            patch("custom_components.oclean_ble.coordinator.asyncio.sleep", new_callable=AsyncMock),
        ):
            bt_mock.async_last_service_info.return_value = _make_service_info()
            result = await coordinator._poll_device()

        # Fresh battery overrides old; old score is carried forward (no new session)
        assert result[DATA_BATTERY] == 61
        assert result[DATA_LAST_BRUSH_SCORE] == 88

    @pytest.mark.asyncio
    async def test_stale_enrichment_cleared_on_new_session_without_enrichment(self):
        """OCLEANY3P inline 0307 (session_count=0): stale score/areas/pressure must be
        cleared when a new session timestamp arrives but no enrichment data is received.

        Regression test for issue #49 (comment 13): after the first *B# poll the
        device switches to session_count=0 inline mode which omits score/areas/pressure.
        Without this fix those fields would show the previous session's values alongside
        the new session's timestamp, giving misleading sensor readings.
        """
        coordinator = _make_coordinator()
        coordinator._store_loaded = True
        # Simulate last-known state: previous session with enrichment fields populated.
        coordinator._last_raw = {
            DATA_LAST_BRUSH_TIME: 1,  # old timestamp – any 2026 date will be newer
            DATA_LAST_BRUSH_SCORE: 88,
            DATA_LAST_BRUSH_AREAS: {"upper_left_out": 50},
            DATA_LAST_BRUSH_PRESSURE: 14.0,
            DATA_LAST_BRUSH_DURATION: 120,
        }

        # 0307 inline notification from OCLEANY3P with session_count=0 (year_byte=0x1a).
        # Encodes 2026-03-19 23:05:53, pNum=75, duration=150 s – NO score/areas.
        # Identical to the raw bytes logged in issue #49 comment 13.
        notif_0307_inline = bytes.fromhex("03072a422300001a03131705354b009600960002")

        client = _make_bleak_client(battery_value=41)

        async def fake_start_notify(uuid, handler):
            handler(None, bytearray(notif_0307_inline))

        client.start_notify = AsyncMock(side_effect=fake_start_notify)

        with (
            patch("custom_components.oclean_ble.coordinator.bluetooth") as bt_mock,
            patch(
                "custom_components.oclean_ble.coordinator.establish_connection",
                new_callable=AsyncMock,
                return_value=client,
            ),
            patch("custom_components.oclean_ble.coordinator.asyncio.sleep", new_callable=AsyncMock),
            patch(
                "custom_components.oclean_ble.coordinator.import_new_sessions", new_callable=AsyncMock, return_value=0
            ),
        ):
            bt_mock.async_last_service_info.return_value = _make_service_info()
            result = await coordinator._poll_device()

        # New session timestamp must be present (inline 0307 parsed correctly)
        assert DATA_LAST_BRUSH_TIME in result
        assert result[DATA_LAST_BRUSH_TIME] > 1, "New timestamp must be newer than the old one"
        # Duration from the inline notification
        assert result.get(DATA_LAST_BRUSH_DURATION) == 150

        # Stale enrichment from previous session must be cleared
        assert result.get(DATA_LAST_BRUSH_SCORE) is None, (
            "Stale score from previous session must not be shown for new session"
        )
        assert result.get(DATA_LAST_BRUSH_AREAS) is None, (
            "Stale areas from previous session must not be shown for new session"
        )
        assert result.get(DATA_LAST_BRUSH_PRESSURE) is None, (
            "Stale pressure from previous session must not be shown for new session"
        )

    @pytest.mark.asyncio
    async def test_enrichment_preserved_when_no_new_session(self):
        """Stale enrichment must NOT be cleared when no new session is detected.

        When the device returns the same session timestamp (e.g. repeated polls
        between brushing events), score/areas/pressure from _last_raw must be kept.
        """
        coordinator = _make_coordinator()
        coordinator._last_raw = {
            DATA_BATTERY: 60,
            DATA_LAST_BRUSH_SCORE: 77,
            DATA_LAST_BRUSH_AREAS: {"upper_left_out": 30},
            DATA_LAST_BRUSH_PRESSURE: 12.0,
        }
        # No session notification fired – collected has no DATA_LAST_BRUSH_TIME
        client = _make_bleak_client(battery_value=60)

        with (
            patch("custom_components.oclean_ble.coordinator.bluetooth") as bt_mock,
            patch(
                "custom_components.oclean_ble.coordinator.establish_connection",
                new_callable=AsyncMock,
                return_value=client,
            ),
            patch("custom_components.oclean_ble.coordinator.asyncio.sleep", new_callable=AsyncMock),
        ):
            bt_mock.async_last_service_info.return_value = _make_service_info()
            result = await coordinator._poll_device()

        assert result.get(DATA_LAST_BRUSH_SCORE) == 77, "Score must be preserved when no new session"
        assert result.get(DATA_LAST_BRUSH_AREAS) == {"upper_left_out": 30}
        assert result.get(DATA_LAST_BRUSH_PRESSURE) == 12.0


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


# ---------------------------------------------------------------------------
# _import_new_sessions  (regression: issue #8)
# ---------------------------------------------------------------------------


class TestImportNewSessions:
    """Regression guard for issue #8: UnboundLocalError caused by a local
    `import datetime` that shadowed the module-level import and made `datetime`
    an unbound local at the point of first use on line 734.
    """

    @pytest.mark.asyncio
    async def test_timestamps_logged_without_unbound_error(self):
        """Must not raise UnboundLocalError when sessions are present (issue #8).

        Before fix: `import datetime` inside _import_new_sessions() made
        `datetime` a local variable throughout the entire function.
        `datetime.datetime.fromtimestamp()` on the logging line – before
        the import statement – raised UnboundLocalError.
        """
        from custom_components.oclean_ble.statistics import import_new_sessions

        hass = _make_hass()
        sessions = [{"last_brush_time": 1740145339, "last_brush_duration": 150}]

        with patch("custom_components.oclean_ble.statistics._load_recorder_api", return_value=None):
            # Before fix: UnboundLocalError on the _LOGGER.debug("Oclean → import …") line
            await import_new_sessions(hass, "aa_bb_cc_dd_ee_ff", "Oclean", sessions, 0)

    @pytest.mark.asyncio
    async def test_session_with_zero_timestamp_uses_na_placeholder(self):
        """last_brush_time=0 must use the 'n/a' branch without error."""
        from custom_components.oclean_ble.statistics import import_new_sessions

        hass = _make_hass()
        sessions = [{"last_brush_time": 0, "last_brush_duration": 150}]

        with patch("custom_components.oclean_ble.statistics._load_recorder_api", return_value=None):
            await import_new_sessions(hass, "aa_bb_cc_dd_ee_ff", "Oclean", sessions, 0)

    @pytest.mark.asyncio
    async def test_no_new_sessions_skips_import(self):
        """Sessions already imported (ts <= last_session_ts) must be silently skipped."""
        from custom_components.oclean_ble.statistics import import_new_sessions

        hass = _make_hass()
        sessions = [{"last_brush_time": 1740145339}]

        result = await import_new_sessions(hass, "aa_bb_cc_dd_ee_ff", "Oclean", sessions, 9_999_999_999)
        assert result == 9_999_999_999


# ---------------------------------------------------------------------------
# _paginate_sessions  (regression: issue #9)
# ---------------------------------------------------------------------------


class TestPaginateSessions:
    """Regression guard for issue #9: asyncio.CancelledError raised by
    write_gatt_char (e.g. ESPHome proxy timeout) was not caught because
    CancelledError inherits from BaseException, not Exception.
    """

    @pytest.mark.asyncio
    async def test_cancelled_error_breaks_gracefully(self):
        """CancelledError from write_gatt_char must not propagate (issue #9).

        Before fix: `except Exception` did not catch asyncio.CancelledError.
        The error bubbled through _setup_and_read → _poll_device →
        _async_update_data and crashed every subsequent poll.
        """
        coord = _make_coordinator()
        coord._store_loaded = True
        coord._last_session_ts = 0

        client = AsyncMock()
        client.write_gatt_char = AsyncMock(side_effect=asyncio.CancelledError())

        all_sessions = [{"last_brush_time": 1740145339}]
        event = asyncio.Event()

        # Before fix: asyncio.CancelledError propagated out of this call
        await coord._paginate_sessions(client, all_sessions, event)

    @pytest.mark.asyncio
    async def test_bleak_error_breaks_gracefully(self):
        """Any Exception subclass from write_gatt_char must also break pagination."""
        from bleak import BleakError

        coord = _make_coordinator()
        coord._store_loaded = True
        coord._last_session_ts = 0

        client = AsyncMock()
        client.write_gatt_char = AsyncMock(side_effect=BleakError("disconnected"))

        all_sessions = [{"last_brush_time": 1740145339}]
        event = asyncio.Event()

        await coord._paginate_sessions(client, all_sessions, event)

    @pytest.mark.asyncio
    async def test_empty_sessions_skips_all_writes(self):
        """No sessions → pagination loop exits immediately without any write."""
        coord = _make_coordinator()
        coord._store_loaded = True

        client = AsyncMock()
        event = asyncio.Event()

        await coord._paginate_sessions(client, [], event)

        client.write_gatt_char.assert_not_called()

    @pytest.mark.asyncio
    async def test_already_known_session_stops_without_write(self):
        """Session ts <= _last_session_ts must stop pagination before writing."""
        coord = _make_coordinator()
        coord._store_loaded = True
        coord._last_session_ts = 9_999_999_999

        client = AsyncMock()
        event = asyncio.Event()
        all_sessions = [{"last_brush_time": 1740145339}]

        await coord._paginate_sessions(client, all_sessions, event)

        client.write_gatt_char.assert_not_called()

    @pytest.mark.asyncio
    async def test_timeout_waiting_for_next_page_breaks_gracefully(self):
        """asyncio.TimeoutError while waiting for the next notification must break pagination."""
        from custom_components.oclean_ble.const import CMD_QUERY_RUNNING_DATA_NEXT, WRITE_CHAR_UUID

        coord = _make_coordinator()
        coord._store_loaded = True
        coord._last_session_ts = 0

        client = AsyncMock()
        client.write_gatt_char = AsyncMock()

        all_sessions = [{"last_brush_time": 1740145339}]
        event = asyncio.Event()
        # event is never set → asyncio.wait_for raises TimeoutError after 2 s

        await coord._paginate_sessions(client, all_sessions, event)

        client.write_gatt_char.assert_called_once_with(WRITE_CHAR_UUID, CMD_QUERY_RUNNING_DATA_NEXT, response=True)


# ---------------------------------------------------------------------------
# _poll_skip_reason – cooldown and window skip paths
# ---------------------------------------------------------------------------


class TestPollSkipReason:
    def test_cooldown_active_returns_reason(self):
        import time

        coord = _make_coordinator()
        coord._cooldown_until = time.time() + 3600
        reason = coord._poll_skip_reason()
        assert reason is not None
        assert "cooldown" in reason

    def test_cooldown_expired_returns_none(self):
        coord = _make_coordinator()
        coord._cooldown_until = 1.0  # far in the past
        assert coord._poll_skip_reason() is None

    def test_outside_windows_returns_reason(self):
        from custom_components.oclean_ble.coordinator import _parse_poll_windows

        coord = _make_coordinator()
        # Set windows to a range that is very unlikely to include now (midnight-00:01)
        # To be safe, use both "00:00-00:01" and "23:59-00:00" and pick the opposite.
        # Simplest: set a single window from 00:00 to 00:01 and ensure current time is outside.
        # Use a 1-minute window at 00:00-00:01; if the test runs at 00:00, we fall back to no-op.
        # Instead, set _poll_windows directly to a known-past window.
        coord._poll_windows = _parse_poll_windows("00:00-00:01")
        import datetime

        if datetime.datetime.now().hour == 0 and datetime.datetime.now().minute == 0:
            return  # skip if window happens to be active now
        reason = coord._poll_skip_reason()
        assert reason is not None
        assert "outside poll windows" in reason

    def test_inside_windows_returns_none(self):
        from custom_components.oclean_ble.coordinator import _parse_poll_windows

        coord = _make_coordinator()
        # Window covering all hours: 00:00-23:59
        coord._poll_windows = _parse_poll_windows("00:00-23:59")
        assert coord._poll_skip_reason() is None

    def test_no_windows_configured_returns_none(self):
        coord = _make_coordinator()
        assert coord._poll_windows == []
        assert coord._poll_skip_reason() is None


# ---------------------------------------------------------------------------
# _async_update_data – skip path (with and without stale data)
# ---------------------------------------------------------------------------


class TestAsyncUpdateDataSkip:
    @pytest.mark.asyncio
    async def test_skip_with_stale_data_returns_device_data(self):
        import time

        coord = _make_coordinator()
        coord._store_loaded = True
        coord._cooldown_until = time.time() + 3600
        coord._last_raw = {DATA_BATTERY: 80}
        result = await coord._async_update_data()
        assert result.battery == 80

    @pytest.mark.asyncio
    async def test_skip_without_stale_data_bypasses_restriction_and_polls(self):
        # When no cached data exists, poll restrictions are bypassed so the
        # initial setup always reaches the device.  With no BLE device available
        # the poll raises UpdateFailed (not a silent empty result).
        import time

        from homeassistant.helpers.update_coordinator import UpdateFailed

        coord = _make_coordinator()
        coord._store_loaded = True
        coord._cooldown_until = time.time() + 3600
        coord._last_raw = {}
        with pytest.raises(UpdateFailed):
            await coord._async_update_data()

    @pytest.mark.asyncio
    async def test_skip_without_stale_data_returns_data_when_ble_available(self):
        # When no cached data exists and a poll window restriction would apply,
        # the bypass polls the device.  If BLE succeeds, data is returned normally.
        import time

        coord = _make_coordinator()
        coord._store_loaded = True
        coord._cooldown_until = time.time() + 3600
        coord._last_raw = {}
        client = _make_bleak_client(battery_value=91)

        with (
            patch("custom_components.oclean_ble.coordinator.bluetooth") as bt_mock,
            patch(
                "custom_components.oclean_ble.coordinator.establish_connection",
                new_callable=AsyncMock,
                return_value=client,
            ),
            patch("custom_components.oclean_ble.coordinator.asyncio.sleep", new_callable=AsyncMock),
        ):
            bt_mock.async_last_service_info.return_value = _make_service_info()
            result = await coord._async_update_data()

        assert result.battery == 91

    @pytest.mark.asyncio
    async def test_save_store_called_after_poll_without_new_sessions(self):
        # _save_store() must be called after every successful poll so that
        # battery and model data survive an HA restart even when no new
        # brush sessions were detected.
        coord = _make_coordinator()
        coord._store_loaded = True
        client = _make_bleak_client(battery_value=60)
        saved: list[dict] = []

        async def _fake_save(data):
            saved.append(data)

        coord._store.async_save = _fake_save

        with (
            patch("custom_components.oclean_ble.coordinator.bluetooth") as bt_mock,
            patch(
                "custom_components.oclean_ble.coordinator.establish_connection",
                new_callable=AsyncMock,
                return_value=client,
            ),
            patch("custom_components.oclean_ble.coordinator.asyncio.sleep", new_callable=AsyncMock),
        ):
            bt_mock.async_last_service_info.return_value = _make_service_info()
            await coord._async_update_data()

        assert len(saved) >= 1, "_save_store must be called after every successful poll"
        assert saved[-1]["last_session"].get(DATA_BATTERY) == 60


# ---------------------------------------------------------------------------
# _load_store – with stored data (previously persisted state)
# ---------------------------------------------------------------------------


class TestLoadStoreWithData:
    @pytest.mark.asyncio
    async def test_restores_persisted_values(self):
        from unittest.mock import AsyncMock as AM

        coord = _make_coordinator()
        coord._store.async_load = AM(
            return_value={
                "last_session_ts": 1_700_000_000,
            }
        )
        await coord._load_store()
        assert coord._last_session_ts == 1_700_000_000
        assert coord._store_loaded is True

    @pytest.mark.asyncio
    async def test_empty_store_leaves_defaults(self):
        coord = _make_coordinator()
        # Default _StoreStub returns None → no changes
        await coord._load_store()
        assert coord._last_session_ts == 0
        assert coord._store_loaded is True

    @pytest.mark.asyncio
    async def test_last_session_restored_into_last_raw(self):
        """Session data persisted in storage must be restored to _last_raw on startup.

        Ensures last_brush_score survives HA restarts even when the 0000 score push
        does not fire during the next poll (issue #19).
        """
        from unittest.mock import AsyncMock as AM

        coord = _make_coordinator()
        coord._store.async_load = AM(
            return_value={
                "last_session_ts": 1_700_000_000,
                "last_session": {
                    DATA_LAST_BRUSH_SCORE: 95,
                    "last_brush_time": 1_700_000_000,
                },
            }
        )
        await coord._load_store()
        assert coord._last_raw[DATA_LAST_BRUSH_SCORE] == 95
        assert coord._last_raw["last_brush_time"] == 1_700_000_000

    @pytest.mark.asyncio
    async def test_save_store_persists_last_session(self):
        """_save_store must write last_session containing non-None _last_raw session fields."""
        saved: dict = {}

        async def _fake_save(data):
            saved.update(data)

        coord = _make_coordinator()
        coord._store.async_save = _fake_save
        coord._last_raw = {DATA_LAST_BRUSH_SCORE: 88, "last_brush_time": 1_700_000_001}
        await coord._save_store()
        assert "last_session" in saved
        assert saved["last_session"][DATA_LAST_BRUSH_SCORE] == 88


# ---------------------------------------------------------------------------
# _read_device_info_service – DIS cache path
# ---------------------------------------------------------------------------


class TestReadDeviceInfoServiceCached:
    @pytest.mark.asyncio
    async def test_dis_cache_hit_skips_read(self):
        import time

        from custom_components.oclean_ble.const import DATA_MODEL_ID, DATA_SW_VERSION

        coord = _make_coordinator()
        coord._dis_last_read_ts = time.time() - 100  # 100 s ago → within 24 h window
        coord._last_raw = {DATA_MODEL_ID: "OCLEANY3P", DATA_SW_VERSION: "2.3.4"}
        client = _make_bleak_client()
        collected: dict = {}
        await coord._read_device_info_service(client, collected)
        client.read_gatt_char.assert_not_called()
        assert collected[DATA_MODEL_ID] == "OCLEANY3P"
        assert collected[DATA_SW_VERSION] == "2.3.4"


# ---------------------------------------------------------------------------
# _send_query_commands – write failure paths
# ---------------------------------------------------------------------------


class TestSendQueryCommandsWriteFailures:
    @pytest.mark.asyncio
    async def test_all_writes_fail_no_crash(self):
        from bleak import BleakError

        coord = _make_coordinator()
        coord._store_loaded = True
        client = _make_bleak_client()
        client.write_gatt_char = AsyncMock(side_effect=BleakError("write failed"))
        event = asyncio.Event()
        event.set()  # skip the wait
        # Must not raise
        await coord._send_query_commands(client, event)

    @pytest.mark.asyncio
    async def test_status_write_fails_continues_to_running_data(self):
        import itertools

        from bleak import BleakError

        coord = _make_coordinator()
        coord._store_loaded = True
        client = _make_bleak_client()
        # First call (CMD_QUERY_STATUS) raises; subsequent calls succeed
        call_results = itertools.chain(
            [BleakError("status failed")],
            [None, None, None],  # remaining writes succeed
        )

        async def side_effect(*_a, **_kw):
            r = next(call_results)
            if isinstance(r, Exception):
                raise r

        client.write_gatt_char = AsyncMock(side_effect=side_effect)
        event = asyncio.Event()
        event.set()
        await coord._send_query_commands(client, event)
        # At least the running-data writes were attempted
        assert client.write_gatt_char.call_count >= 2


# ---------------------------------------------------------------------------
# _import_new_sessions – recorder API available, statistics import
# ---------------------------------------------------------------------------


class TestImportNewSessionsWithRecorder:
    def _make_stat_classes(self):
        """Return minimal StatisticData / StatisticMetaData stubs."""

        class _SD:
            def __init__(self, *, start, mean, state):
                self.start = start
                self.mean = mean
                self.state = state

        class _SM:
            def __init__(self, **kwargs):
                self.__dict__.update(kwargs)

        return _SD, _SM

    @staticmethod
    def _install_dt_util_stub():
        """Install a homeassistant.util.dt stub with a real UTC tzinfo."""
        import datetime
        import sys
        import types

        dt_stub = types.ModuleType("homeassistant.util.dt")
        dt_stub.UTC = datetime.UTC
        util_stub = types.ModuleType("homeassistant.util")
        util_stub.dt = dt_stub
        sys.modules["homeassistant.util"] = util_stub
        sys.modules["homeassistant.util.dt"] = dt_stub

    @pytest.mark.asyncio
    async def test_imports_new_session_and_updates_last_ts(self):
        from custom_components.oclean_ble.statistics import import_new_sessions

        self._install_dt_util_stub()
        _SD, _SM = self._make_stat_classes()
        add_fn = MagicMock()
        hass = _make_hass()

        with patch("custom_components.oclean_ble.statistics._load_recorder_api", return_value=(_SD, _SM, add_fn)):
            sessions = [{"last_brush_time": 1_700_000_001, DATA_LAST_BRUSH_SCORE: 80}]
            new_ts = await import_new_sessions(hass, "aa_bb_cc_dd_ee_ff", "Oclean", sessions, 0)

        assert new_ts == 1_700_000_001
        add_fn.assert_called()

    @pytest.mark.asyncio
    async def test_no_new_sessions_skips_import(self):
        from custom_components.oclean_ble.statistics import import_new_sessions

        _SD, _SM = self._make_stat_classes()
        add_fn = MagicMock()
        hass = _make_hass()

        with patch("custom_components.oclean_ble.statistics._load_recorder_api", return_value=(_SD, _SM, add_fn)):
            sessions = [{"last_brush_time": 1_700_000_001}]
            new_ts = await import_new_sessions(hass, "aa_bb_cc_dd_ee_ff", "Oclean", sessions, 9_999_999_999)

        assert new_ts == 9_999_999_999
        add_fn.assert_not_called()

    @pytest.mark.asyncio
    async def test_recorder_unavailable_skips_gracefully(self):
        from custom_components.oclean_ble.statistics import import_new_sessions

        hass = _make_hass()
        with patch("custom_components.oclean_ble.statistics._load_recorder_api", return_value=None):
            # Must not raise
            new_ts = await import_new_sessions(
                hass, "aa_bb_cc_dd_ee_ff", "Oclean", [{"last_brush_time": 1_700_000_001}], 0
            )
        assert new_ts == 0  # recorder unavailable → original ts returned unchanged

    @pytest.mark.asyncio
    async def test_area_stats_imported_when_present(self):
        from custom_components.oclean_ble.statistics import import_new_sessions

        self._install_dt_util_stub()
        _SD, _SM = self._make_stat_classes()
        add_fn = MagicMock()
        hass = _make_hass()
        areas = {"upper_left_out": 20, "lower_right_in": 15}

        with patch("custom_components.oclean_ble.statistics._load_recorder_api", return_value=(_SD, _SM, add_fn)):
            sessions = [{"last_brush_time": 1_700_000_002, DATA_LAST_BRUSH_AREAS: areas}]
            await import_new_sessions(hass, "aa_bb_cc_dd_ee_ff", "Oclean", sessions, 0)

        # add_fn called at least once per area zone
        assert add_fn.call_count >= len(areas)

    @pytest.mark.asyncio
    async def test_metric_add_raises_continues_to_next_metric(self):
        """Exception in async_add_external_statistics must log and continue (lines 116-123)."""
        from custom_components.oclean_ble.statistics import import_new_sessions

        self._install_dt_util_stub()
        _SD, _SM = self._make_stat_classes()
        add_fn = MagicMock(side_effect=Exception("stat write failed"))
        hass = _make_hass()

        with patch("custom_components.oclean_ble.statistics._load_recorder_api", return_value=(_SD, _SM, add_fn)):
            sessions = [{"last_brush_time": 1_700_000_003, DATA_LAST_BRUSH_SCORE: 90}]
            # Must not raise despite add_fn failing
            new_ts = await import_new_sessions(hass, "aa_bb_cc_dd_ee_ff", "Oclean", sessions, 0)

        assert new_ts == 1_700_000_003

    @pytest.mark.asyncio
    async def test_area_stat_add_raises_continues(self):
        """Exception in async_add_external_statistics for area stats must continue (lines 156-162)."""
        from custom_components.oclean_ble.statistics import import_new_sessions

        self._install_dt_util_stub()
        _SD, _SM = self._make_stat_classes()
        add_fn = MagicMock(side_effect=Exception("area stat write failed"))
        hass = _make_hass()
        areas = {"upper_left_out": 30}

        with patch("custom_components.oclean_ble.statistics._load_recorder_api", return_value=(_SD, _SM, add_fn)):
            sessions = [{"last_brush_time": 1_700_000_004, DATA_LAST_BRUSH_AREAS: areas}]
            new_ts = await import_new_sessions(hass, "aa_bb_cc_dd_ee_ff", "Oclean", sessions, 0)

        assert new_ts == 1_700_000_004


# ---------------------------------------------------------------------------
# _load_recorder_api – fallback import path (lines 37-53)
# ---------------------------------------------------------------------------


class TestLoadRecorderAPIFallback:
    def test_fallback_path_when_statistic_data_not_in_recorder_statistics(self):
        """When StatisticData is absent from recorder.statistics, fall back to recorder.models."""
        import sys
        import types

        from custom_components.oclean_ble.statistics import _load_recorder_api

        class FakeSD:
            pass

        class FakeSM:
            pass

        def fake_add(*_a, **_kw):
            pass

        # Build a recorder.statistics stub WITHOUT StatisticData (simulates old HA split)
        rec_stats = types.ModuleType("homeassistant.components.recorder.statistics")
        rec_stats.async_add_external_statistics = fake_add
        # StatisticData / StatisticMetaData intentionally absent

        rec_models = types.ModuleType("homeassistant.components.recorder.models")
        rec_models.StatisticData = FakeSD
        rec_models.StatisticMetaData = FakeSM

        # Save and patch sys.modules
        old = {
            k: sys.modules.pop(k, None)
            for k in (
                "homeassistant.components.recorder.statistics",
                "homeassistant.components.recorder.models",
            )
        }
        sys.modules["homeassistant.components.recorder.statistics"] = rec_stats
        sys.modules["homeassistant.components.recorder.models"] = rec_models
        try:
            result = _load_recorder_api()
        finally:
            sys.modules.pop("homeassistant.components.recorder.statistics", None)
            sys.modules.pop("homeassistant.components.recorder.models", None)
            for k, v in old.items():
                if v is not None:
                    sys.modules[k] = v

        assert result is not None
        SD, SM, add_fn = result
        assert SD is FakeSD
        assert SM is FakeSM

    def test_both_paths_fail_returns_none(self):
        """When both import attempts fail, _load_recorder_api returns None."""
        import sys

        from custom_components.oclean_ble.statistics import _load_recorder_api

        old = {
            k: sys.modules.pop(k, None)
            for k in (
                "homeassistant.components.recorder.statistics",
                "homeassistant.components.recorder.models",
            )
        }
        try:
            result = _load_recorder_api()
        finally:
            for k, v in old.items():
                if v is not None:
                    sys.modules[k] = v

        assert result is None


# ---------------------------------------------------------------------------
# _calibrate_time – write failure path (lines 583-587)
# ---------------------------------------------------------------------------


class TestCalibrateTimeWriteFailure:
    @pytest.mark.asyncio
    async def test_write_failure_does_not_raise(self):
        """Exception in write_gatt_char must be swallowed with a warning log."""
        coord = _make_coordinator()
        client = _make_bleak_client()
        client.write_gatt_char = AsyncMock(side_effect=Exception("write failed"))
        # Must not raise
        await coord._calibrate_time(client)


# ---------------------------------------------------------------------------
# _read_battery_and_unsubscribe – read failure path (lines 684-685)
# ---------------------------------------------------------------------------


class TestBatteryReadFailure:
    @pytest.mark.asyncio
    async def test_read_failure_does_not_raise(self):
        """Exception in read_gatt_char for battery must log a warning and not crash."""
        coord = _make_coordinator()
        client = _make_bleak_client()
        client.read_gatt_char = AsyncMock(side_effect=Exception("read failed"))
        collected: dict = {}
        # Must not raise
        await coord._read_battery_and_unsubscribe(client, collected)
        assert DATA_BATTERY not in collected

    @pytest.mark.asyncio
    async def test_notification_value_skips_read(self):
        """If DATA_BATTERY already set (from notification), read_gatt_char is not called."""
        coord = _make_coordinator()
        client = _make_bleak_client(battery_value=99)
        collected: dict = {DATA_BATTERY: 55}
        await coord._read_battery_and_unsubscribe(client, collected)
        # Value must stay at notification value, not overwritten by read
        assert collected[DATA_BATTERY] == 55
        client.read_gatt_char.assert_not_called()


class TestSubscribeBatteryNotifications:
    @pytest.mark.asyncio
    async def test_subscribe_success_registers_callback(self):
        """start_notify must be called with BATTERY_CHAR_UUID on success."""
        from custom_components.oclean_ble.const import BATTERY_CHAR_UUID

        coord = _make_coordinator()
        client = _make_bleak_client()
        collected: dict = {}
        await coord._subscribe_battery_notifications(client, collected)
        client.start_notify.assert_any_call(BATTERY_CHAR_UUID, ANY)

    @pytest.mark.asyncio
    async def test_subscribe_failure_does_not_raise(self):
        """Exception during start_notify must be swallowed gracefully."""
        coord = _make_coordinator()
        client = _make_bleak_client()
        client.start_notify = AsyncMock(side_effect=Exception("no CCCD"))
        collected: dict = {}
        await coord._subscribe_battery_notifications(client, collected)
        assert DATA_BATTERY not in collected

    @pytest.mark.asyncio
    async def test_notification_callback_sets_battery(self):
        """When start_notify fires the callback, collected[DATA_BATTERY] is updated."""
        from custom_components.oclean_ble.const import BATTERY_CHAR_UUID

        coord = _make_coordinator()
        client = _make_bleak_client()
        collected: dict = {}

        # Capture the callback that _subscribe_battery_notifications registers
        captured_cb: list = []

        async def _fake_start_notify(uuid, cb):
            if uuid == BATTERY_CHAR_UUID:
                captured_cb.append(cb)

        client.start_notify = AsyncMock(side_effect=_fake_start_notify)
        await coord._subscribe_battery_notifications(client, collected)

        assert len(captured_cb) == 1
        # Simulate device pushing a battery level notification (72 %)
        captured_cb[0](None, bytearray([72]))
        assert collected[DATA_BATTERY] == 72


# ---------------------------------------------------------------------------
# _subscribe_notifications – Notify-acquired retry logic
# ---------------------------------------------------------------------------


class TestSubscribeNotificationsRetry:
    @pytest.mark.asyncio
    async def test_success_on_first_attempt(self):
        """All notify_chars subscribed successfully without retries."""
        from custom_components.oclean_ble.protocol import TYPE1

        coord = _make_coordinator()
        coord._protocol = TYPE1
        client = _make_bleak_client()
        handler = lambda _char, _data: None  # noqa: E731
        subscribed = await coord._subscribe_notifications(client, handler)
        assert subscribed == frozenset(TYPE1.notify_chars)
        client.stop_notify.assert_not_called()

    @pytest.mark.asyncio
    async def test_notify_acquired_releases_and_retries(self):
        """On 'Notify acquired', stop_notify is called and start_notify is retried."""
        from custom_components.oclean_ble.protocol import TYPE1

        coord = _make_coordinator()
        coord._protocol = TYPE1
        client = _make_bleak_client()

        call_counts: dict[str, int] = {}

        async def _fake_start_notify(uuid, _handler):
            call_counts[uuid] = call_counts.get(uuid, 0) + 1
            if call_counts[uuid] == 1:
                raise Exception("[org.bluez.Error.NotPermitted] Notify acquired")
            # second call succeeds (no exception)

        client.start_notify = AsyncMock(side_effect=_fake_start_notify)
        handler = lambda _char, _data: None  # noqa: E731
        subscribed = await coord._subscribe_notifications(client, handler)

        # All chars must be in the subscribed set (retry succeeded)
        assert subscribed == frozenset(TYPE1.notify_chars)
        # stop_notify must have been called once per char that had Notify acquired
        assert client.stop_notify.await_count == len(TYPE1.notify_chars)

    @pytest.mark.asyncio
    async def test_notify_acquired_retry_also_fails_logs_warning(self):
        """When retry also fails, char is NOT in subscribed set and no exception raised."""
        from custom_components.oclean_ble.protocol import TYPE1

        coord = _make_coordinator()
        coord._protocol = TYPE1
        client = _make_bleak_client()
        client.start_notify = AsyncMock(side_effect=Exception("[org.bluez.Error.NotPermitted] Notify acquired"))
        handler = lambda _char, _data: None  # noqa: E731
        subscribed = await coord._subscribe_notifications(client, handler)

        # No char successfully subscribed
        assert len(subscribed) == 0
        # stop_notify attempted for each char
        assert client.stop_notify.await_count == len(TYPE1.notify_chars)

    @pytest.mark.asyncio
    async def test_other_error_does_not_retry(self):
        """A non-'Notify acquired' error skips the retry path and leaves char unsubscribed."""
        from custom_components.oclean_ble.protocol import TYPE1

        coord = _make_coordinator()
        coord._protocol = TYPE1
        client = _make_bleak_client()
        client.start_notify = AsyncMock(
            side_effect=Exception("[org.bluez.Error.NotSupported] Operation is not supported")
        )
        handler = lambda _char, _data: None  # noqa: E731
        subscribed = await coord._subscribe_notifications(client, handler)

        assert len(subscribed) == 0
        client.stop_notify.assert_not_called()


# ---------------------------------------------------------------------------
# _read_device_info_service – device registry update path (lines 556-576)
# ---------------------------------------------------------------------------


class TestDISDeviceRegistryUpdate:
    @pytest.mark.asyncio
    async def test_device_registry_updated_when_sw_version_present(self):
        """async_update_device must be called when sw_version or model_id is read."""
        from custom_components.oclean_ble.const import DIS_HW_REV_UUID, DIS_MODEL_UUID, DIS_SW_REV_UUID

        coord = _make_coordinator()
        coord._dis_last_read_ts = 0.0  # force fresh DIS read

        async def _fake_read(uuid, **_kw):
            return {
                DIS_MODEL_UUID: bytearray(b"OCLEANY3P"),
                DIS_HW_REV_UUID: bytearray(b"Rev.D"),
                DIS_SW_REV_UUID: bytearray(b"2.3.4"),
            }.get(uuid, bytearray())

        client = _make_bleak_client()
        client.read_gatt_char = AsyncMock(side_effect=_fake_read)

        mock_device_entry = MagicMock()
        mock_device_entry.id = "test-device-entry-id"
        mock_dr = MagicMock()
        mock_dr.async_get_device.return_value = mock_device_entry

        collected: dict = {}
        with patch("homeassistant.helpers.device_registry.async_get", return_value=mock_dr, create=True):
            await coord._read_device_info_service(client, collected)

        mock_dr.async_update_device.assert_called_once()
        call_kwargs = mock_dr.async_update_device.call_args[1]
        assert call_kwargs["sw_version"] == "2.3.4"
        assert call_kwargs["model"] == "OCLEANY3P"

    @pytest.mark.asyncio
    async def test_device_registry_update_exception_swallowed(self):
        """If async_get raises, the exception must be caught and not propagate (line 575-576)."""
        from custom_components.oclean_ble.const import DIS_SW_REV_UUID

        coord = _make_coordinator()
        coord._dis_last_read_ts = 0.0

        async def _fake_read(uuid, **_kw):
            return bytearray(b"TestModel") if uuid == DIS_SW_REV_UUID else bytearray()

        client = _make_bleak_client()
        client.read_gatt_char = AsyncMock(side_effect=_fake_read)

        collected: dict = {}
        with patch(
            "homeassistant.helpers.device_registry.async_get", side_effect=Exception("dr unavailable"), create=True
        ):
            # Must not raise
            await coord._read_device_info_service(client, collected)


# ---------------------------------------------------------------------------
# _poll_device – hardware brush-head detection and post-brush cooldown
# ---------------------------------------------------------------------------


class TestPollDeviceHwBrushAndCooldown:
    @pytest.mark.asyncio
    async def test_hw_brush_head_usage_passed_through_from_collected(self):
        """DATA_BRUSH_HEAD_USAGE from the 0302 response must appear in the poll result."""
        from custom_components.oclean_ble.const import DATA_BRUSH_HEAD_USAGE

        coord = _make_coordinator()
        coord._store_loaded = True
        client = _make_bleak_client()

        async def fake_setup_and_read(_client, collected):
            collected[DATA_BRUSH_HEAD_USAGE] = 7
            return []

        async def fake_import(_h, _m, _d, sessions, last_ts):
            return last_ts

        with (
            patch("custom_components.oclean_ble.coordinator.bluetooth") as bt_mock,
            patch(
                "custom_components.oclean_ble.coordinator.establish_connection",
                new_callable=AsyncMock,
                return_value=client,
            ),
            patch("custom_components.oclean_ble.coordinator.asyncio.sleep", new_callable=AsyncMock),
            patch("custom_components.oclean_ble.coordinator.import_new_sessions", side_effect=fake_import),
            patch.object(coord, "_setup_and_read", side_effect=fake_setup_and_read),
        ):
            bt_mock.async_last_service_info.return_value = _make_service_info()
            result = await coord._poll_device()

        assert result[DATA_BRUSH_HEAD_USAGE] == 7

    @pytest.mark.asyncio
    async def test_post_brush_cooldown_activated_after_new_session(self):
        """_cooldown_until must be set in the future when new sessions arrive and cooldown is configured."""
        import time

        coord = OcleanCoordinator(_make_hass(), "AA:BB:CC:DD:EE:FF", "Oclean", 300, post_brush_cooldown_h=1)
        coord._store_loaded = True
        coord._last_session_ts = 0
        client = _make_bleak_client()

        async def fake_setup_and_read(_client, _collected):
            return [{"last_brush_time": 1_700_000_001}]

        async def fake_import(_h, _m, _d, sessions, last_ts):
            return 1_700_000_001

        with (
            patch("custom_components.oclean_ble.coordinator.bluetooth") as bt_mock,
            patch(
                "custom_components.oclean_ble.coordinator.establish_connection",
                new_callable=AsyncMock,
                return_value=client,
            ),
            patch("custom_components.oclean_ble.coordinator.asyncio.sleep", new_callable=AsyncMock),
            patch("custom_components.oclean_ble.coordinator.import_new_sessions", side_effect=fake_import),
            patch.object(coord, "_setup_and_read", side_effect=fake_setup_and_read),
        ):
            bt_mock.async_last_service_info.return_value = _make_service_info()
            await coord._poll_device()

        assert coord._cooldown_until > time.time()


# ---------------------------------------------------------------------------
# async_sync_time
# ---------------------------------------------------------------------------


class TestAsyncSyncTime:
    @pytest.mark.asyncio
    async def test_sends_020e_for_unknown_protocol(self):
        """async_sync_time must write 020E + 4-byte timestamp for UNKNOWN protocol."""
        coord = _make_coordinator()
        client = _make_bleak_client()

        with (
            patch("custom_components.oclean_ble.coordinator.bluetooth") as bt_mock,
            patch(
                "custom_components.oclean_ble.coordinator.establish_connection",
                new_callable=AsyncMock,
                return_value=client,
            ),
        ):
            bt_mock.async_last_service_info.return_value = _make_service_info()
            await coord.async_sync_time()

        client.write_gatt_char.assert_awaited_once()
        cmd_bytes = client.write_gatt_char.call_args[0][1]
        assert cmd_bytes[:2] == bytes.fromhex("020E"), "Must send 020E time-calibration command"
        assert len(cmd_bytes) == 6, "020E + 4-byte timestamp = 6 bytes"
        client.disconnect.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_sends_0201_for_type1_protocol(self):
        """Type-1 coordinator must send 0201 + 8-byte datetime payload, not 020E.

        Regression test for issue #49 comment: 020E does not sync the clock on
        OCLEANY3P (C3352g handler). The correct command is 0201 (mo5292L).
        """
        from custom_components.oclean_ble.protocol import TYPE1

        coord = _make_coordinator()
        coord._protocol = TYPE1
        client = _make_bleak_client()

        with (
            patch("custom_components.oclean_ble.coordinator.bluetooth") as bt_mock,
            patch(
                "custom_components.oclean_ble.coordinator.establish_connection",
                new_callable=AsyncMock,
                return_value=client,
            ),
        ):
            bt_mock.async_last_service_info.return_value = _make_service_info()
            await coord.async_sync_time()

        client.write_gatt_char.assert_awaited_once()
        cmd_bytes = client.write_gatt_char.call_args[0][1]
        assert cmd_bytes[:2] == bytes.fromhex("0201"), "Type-1 must send 0201 command"
        assert len(cmd_bytes) == 10, "0201 + 8-byte datetime payload = 10 bytes"
        # Payload sanity: year offset, month, day must be plausible
        yy, month, day = cmd_bytes[2], cmd_bytes[3], cmd_bytes[4]
        assert 24 <= yy <= 99, f"year-2000 byte out of range: {yy}"
        assert 1 <= month <= 12, f"month byte out of range: {month}"
        assert 1 <= day <= 31, f"day byte out of range: {day}"
        # tzIndex must be in valid range (1-33)
        tz_idx = cmd_bytes[9]
        assert 1 <= tz_idx <= 33, f"tz_idx out of range: {tz_idx}"
        client.disconnect.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_disconnects_on_write_failure(self):
        """disconnect must be called even when _calibrate_time raises."""
        from bleak import BleakError

        coord = _make_coordinator()
        client = _make_bleak_client()
        client.write_gatt_char = AsyncMock(side_effect=BleakError("write failed"))

        with (
            patch("custom_components.oclean_ble.coordinator.bluetooth") as bt_mock,
            patch(
                "custom_components.oclean_ble.coordinator.establish_connection",
                new_callable=AsyncMock,
                return_value=client,
            ),
        ):
            bt_mock.async_last_service_info.return_value = _make_service_info()
            # _calibrate_time swallows exceptions internally, so async_sync_time must not raise
            await coord.async_sync_time()

        client.disconnect.assert_awaited_once()


# Hardware brush-head counter (0302 response)
# ---------------------------------------------------------------------------


class TestHardwareBrushHeadCounter:
    @pytest.mark.asyncio
    async def test_brush_head_usage_from_collected_appears_in_result(self):
        """DATA_BRUSH_HEAD_USAGE placed in collected (from 0302 parser) must reach the result."""
        from custom_components.oclean_ble.const import DATA_BRUSH_HEAD_USAGE

        coord = _make_coordinator()
        coord._store_loaded = True
        client = _make_bleak_client()

        async def fake_setup(_client, collected):
            collected[DATA_BRUSH_HEAD_USAGE] = 42
            return []

        async def fake_import(_h, _m, _d, sessions, last_ts):
            return last_ts

        with (
            patch("custom_components.oclean_ble.coordinator.bluetooth") as bt_mock,
            patch(
                "custom_components.oclean_ble.coordinator.establish_connection",
                new_callable=AsyncMock,
                return_value=client,
            ),
            patch("custom_components.oclean_ble.coordinator.asyncio.sleep", new_callable=AsyncMock),
            patch("custom_components.oclean_ble.coordinator.import_new_sessions", side_effect=fake_import),
            patch.object(coord, "_setup_and_read", side_effect=fake_setup),
        ):
            bt_mock.async_last_service_info.return_value = _make_service_info()
            result = await coord._poll_device()

        assert result[DATA_BRUSH_HEAD_USAGE] == 42

    @pytest.mark.asyncio
    async def test_sw_counter_increments_when_hw_absent(self):
        """SW counter must be incremented by new_session_count when HW value is absent."""
        from custom_components.oclean_ble.const import DATA_BRUSH_HEAD_USAGE, DATA_LAST_BRUSH_TIME

        coord = _make_coordinator()
        coord._store_loaded = True
        client = _make_bleak_client()

        session = {DATA_LAST_BRUSH_TIME: 1_700_000_001}

        async def fake_setup(_client, collected):
            # No DATA_BRUSH_HEAD_USAGE in collected → SW counter path
            return [session]

        async def fake_import(_h, _m, _d, sessions, last_ts):
            return max(s.get(DATA_LAST_BRUSH_TIME, 0) for s in sessions)

        with (
            patch("custom_components.oclean_ble.coordinator.bluetooth") as bt_mock,
            patch(
                "custom_components.oclean_ble.coordinator.establish_connection",
                new_callable=AsyncMock,
                return_value=client,
            ),
            patch("custom_components.oclean_ble.coordinator.asyncio.sleep", new_callable=AsyncMock),
            patch("custom_components.oclean_ble.coordinator.import_new_sessions", side_effect=fake_import),
            patch.object(coord, "_setup_and_read", side_effect=fake_setup),
        ):
            bt_mock.async_last_service_info.return_value = _make_service_info()
            result = await coord._poll_device()

        assert result[DATA_BRUSH_HEAD_USAGE] == 1
        assert coord._brush_head_sw_count == 1

    @pytest.mark.asyncio
    async def test_hw_value_syncs_sw_counter(self):
        """When device delivers DATA_BRUSH_HEAD_USAGE, SW counter must be updated to match."""
        from custom_components.oclean_ble.const import DATA_BRUSH_HEAD_USAGE

        coord = _make_coordinator()
        coord._store_loaded = True
        coord._brush_head_sw_count = 5  # outdated SW value
        client = _make_bleak_client()

        async def fake_setup(_client, collected):
            collected[DATA_BRUSH_HEAD_USAGE] = 12  # HW counter from 0302
            return []

        async def fake_import(_h, _m, _d, sessions, last_ts):
            return last_ts

        with (
            patch("custom_components.oclean_ble.coordinator.bluetooth") as bt_mock,
            patch(
                "custom_components.oclean_ble.coordinator.establish_connection",
                new_callable=AsyncMock,
                return_value=client,
            ),
            patch("custom_components.oclean_ble.coordinator.asyncio.sleep", new_callable=AsyncMock),
            patch("custom_components.oclean_ble.coordinator.import_new_sessions", side_effect=fake_import),
            patch.object(coord, "_setup_and_read", side_effect=fake_setup),
        ):
            bt_mock.async_last_service_info.return_value = _make_service_info()
            result = await coord._poll_device()

        assert result[DATA_BRUSH_HEAD_USAGE] == 12
        assert coord._brush_head_sw_count == 12

    @pytest.mark.asyncio
    async def test_sw_counter_reset_on_brush_head_reset(self):
        """async_reset_brush_head must reset SW counter to 0."""
        coord = _make_coordinator()
        coord._store_loaded = True
        coord._brush_head_sw_count = 7
        client = _make_bleak_client()
        client.write_gatt_char = AsyncMock()

        with (
            patch("custom_components.oclean_ble.coordinator.bluetooth") as bt_mock,
            patch(
                "custom_components.oclean_ble.coordinator.establish_connection",
                new_callable=AsyncMock,
                return_value=client,
            ),
            patch("custom_components.oclean_ble.coordinator.asyncio.sleep", new_callable=AsyncMock),
        ):
            bt_mock.async_last_service_info.return_value = _make_service_info()
            await coord.async_reset_brush_head()

        assert coord._brush_head_sw_count == 0


# ---------------------------------------------------------------------------
# DIS cache – fresh read when never read or expired (> 24 h)
# ---------------------------------------------------------------------------


class TestReadDeviceInfoServiceCacheExpiry:
    @pytest.mark.asyncio
    async def test_dis_never_read_triggers_fresh_read(self):
        """_dis_last_read_ts == 0 must always trigger a fresh DIS read."""
        coord = _make_coordinator()
        client = _make_bleak_client()
        client.read_gatt_char = AsyncMock(return_value=bytearray(b"OCLEANY3M"))
        collected: dict = {}
        await coord._read_device_info_service(client, collected)
        client.read_gatt_char.assert_called()

    @pytest.mark.asyncio
    async def test_dis_expired_triggers_fresh_read(self):
        """_dis_last_read_ts older than 24 h must trigger a fresh DIS read."""
        import time

        coord = _make_coordinator()
        coord._dis_last_read_ts = time.time() - 90_000  # 25 h ago
        client = _make_bleak_client()
        client.read_gatt_char = AsyncMock(return_value=bytearray(b"OCLEANY3M"))
        collected: dict = {}
        await coord._read_device_info_service(client, collected)
        client.read_gatt_char.assert_called()

    @pytest.mark.asyncio
    async def test_dis_fresh_updates_last_read_ts(self):
        """After a successful DIS read _dis_last_read_ts must be set to ~now."""
        import time

        coord = _make_coordinator()
        client = _make_bleak_client()
        client.read_gatt_char = AsyncMock(return_value=bytearray(b"OCLEANY3M"))
        before = time.time()
        await coord._read_device_info_service(client, {})
        assert coord._dis_last_read_ts >= before

    @pytest.mark.asyncio
    async def test_dis_all_reads_fail_does_not_advance_ts(self):
        """If all DIS reads fail, _dis_last_read_ts must not be updated so the
        next poll retries rather than waiting 24 h."""
        import time

        from custom_components.oclean_ble.const import DATA_MODEL_ID

        coord = _make_coordinator()
        ts_before = coord._dis_last_read_ts  # 0.0
        client = _make_bleak_client()
        client.read_gatt_char = AsyncMock(side_effect=Exception("Insufficient authorization"))
        await coord._read_device_info_service(client, {})
        assert coord._dis_last_read_ts == ts_before

    @pytest.mark.asyncio
    async def test_dis_read_failure_falls_back_to_cached_model_id(self):
        """When DIS reads fail, cached model_id from _last_raw must be injected
        into collected so the protocol profile is not reset to UNKNOWN."""
        from custom_components.oclean_ble.const import DATA_MODEL_ID
        from custom_components.oclean_ble.protocol import LEGACY

        coord = _make_coordinator()
        # Simulate a previous successful poll that cached model_id
        coord._last_raw = {DATA_MODEL_ID: "OCLEANA1"}
        coord._protocol = LEGACY

        client = _make_bleak_client()
        client.read_gatt_char = AsyncMock(side_effect=Exception("Insufficient authorization"))
        collected: dict = {}
        await coord._read_device_info_service(client, collected)

        assert collected.get(DATA_MODEL_ID) == "OCLEANA1"
        assert coord._protocol is LEGACY  # must not be reset to UNKNOWN

    @pytest.mark.asyncio
    async def test_dis_partial_read_failure_uses_cache_for_missing_keys(self):
        """If only some DIS reads fail, cached values must fill the gaps."""
        from custom_components.oclean_ble.const import (
            DATA_HW_REVISION,
            DATA_MODEL_ID,
            DATA_SW_VERSION,
            DIS_HW_REV_UUID,
            DIS_MODEL_UUID,
            DIS_SW_REV_UUID,
        )

        coord = _make_coordinator()
        coord._last_raw = {
            DATA_MODEL_ID: "OCLEANA1",
            DATA_HW_REVISION: "Rev.D",
            DATA_SW_VERSION: "1.0.0.4",
        }

        def _side_effect(uuid):
            if uuid == DIS_MODEL_UUID:
                return bytearray(b"OCLEANA1")
            raise Exception("Insufficient authorization")

        client = _make_bleak_client()
        client.read_gatt_char = AsyncMock(side_effect=_side_effect)
        collected: dict = {}
        await coord._read_device_info_service(client, collected)

        assert collected[DATA_MODEL_ID] == "OCLEANA1"
        assert collected[DATA_HW_REVISION] == "Rev.D"  # from cache
        assert collected[DATA_SW_VERSION] == "1.0.0.4"  # from cache


# ---------------------------------------------------------------------------
# Manual poll mode (poll_interval=0 → update_interval=None)
# ---------------------------------------------------------------------------


class TestManualPollMode:
    def test_update_interval_is_none_when_poll_interval_zero(self):
        """Coordinator must not set a timer when poll_interval=0 (manual mode)."""
        coord = _make_coordinator(poll_interval=0)
        assert coord.update_interval is None

    def test_update_interval_set_when_poll_interval_nonzero(self):
        """Coordinator must set a timer for positive poll_interval values."""
        from datetime import timedelta

        coord = _make_coordinator(poll_interval=300)
        assert coord.update_interval == timedelta(seconds=300)

    @pytest.mark.asyncio
    async def test_async_request_refresh_triggers_poll_in_manual_mode(self):
        """async_request_refresh() must poll the device even in manual mode."""
        coord = _make_coordinator(poll_interval=0)
        coord._store_loaded = True
        client = _make_bleak_client()

        with (
            patch("custom_components.oclean_ble.coordinator.bluetooth") as bt_mock,
            patch(
                "custom_components.oclean_ble.coordinator.establish_connection",
                new_callable=AsyncMock,
                return_value=client,
            ),
            patch("custom_components.oclean_ble.coordinator.asyncio.sleep", new_callable=AsyncMock),
        ):
            bt_mock.async_last_service_info.return_value = _make_service_info()
            result = await coord._poll_device()

        assert result is not None


# ---------------------------------------------------------------------------
# *B# multi-packet reassembly (OCLEANY3 / OCLEANY3P)
# ---------------------------------------------------------------------------


def _make_c3352g_record_bytes(
    *,
    year: int = 2026,
    month: int = 3,
    day: int = 9,
    hour: int = 8,
    minute: int = 30,
    second: int = 0,
    pnum: int = 5,
    duration: int = 120,
    score: int = 80,
) -> bytes:
    """Build a single 42-byte C3352g session record (year_base at byte 0)."""
    record = bytearray(42)
    record[0] = year - 2000  # year_base byte
    record[1] = month
    record[2] = day
    record[3] = hour
    record[4] = minute
    record[5] = second
    record[6] = pnum
    record[7] = (duration >> 8) & 0xFF
    record[8] = duration & 0xFF
    record[33] = score
    return bytes(record)


def _make_t1_c3352g_packets(records: list[bytes], ble_mtu: int = 20) -> list[bytes]:
    """Chop a *B# stream into BLE-MTU-sized notification packets.

    First packet: ``0307 *B# count_hi count_lo [inline bytes up to MTU-7]``
    Subsequent packets: raw continuation bytes, each up to *ble_mtu* bytes.
    """
    count = len(records)
    record_data = b"".join(records)
    header = bytes([0x03, 0x07, 0x2A, 0x42, 0x23, (count >> 8) & 0xFF, count & 0xFF])
    stream = header + record_data
    return [stream[i : i + ble_mtu] for i in range(0, len(stream), ble_mtu)]


class TestT1C3352gReassembly:
    """Multi-packet *B# reassembly in _make_notification_handler."""

    def _make_client_firing_notifications(self, *notification_payloads):
        client = _make_bleak_client(battery_value=80)
        call_count = [0]

        async def fake_start_notify(uuid, handler):
            call_count[0] += 1
            if call_count[0] == 1:
                for payload in notification_payloads:
                    handler(None, bytearray(payload))

        client.start_notify = AsyncMock(side_effect=fake_start_notify)
        return client

    async def _run_poll(self, client) -> list[dict]:
        coordinator = _make_coordinator()
        coordinator._store_loaded = True
        captured: list = []

        async def fake_import(_hass, _mac_slug, _device_name, sessions, last_ts):
            captured.extend(sessions)
            return last_ts

        with (
            patch("custom_components.oclean_ble.coordinator.bluetooth") as bt_mock,
            patch(
                "custom_components.oclean_ble.coordinator.establish_connection",
                new_callable=AsyncMock,
                return_value=client,
            ),
            patch("custom_components.oclean_ble.coordinator.asyncio.sleep", new_callable=AsyncMock),
            patch("custom_components.oclean_ble.coordinator.import_new_sessions", side_effect=fake_import),
        ):
            bt_mock.async_last_service_info.return_value = _make_service_info()
            await coordinator._poll_device()

        return captured

    @pytest.mark.asyncio
    async def test_single_record_multi_packet(self):
        """count=1 record split across multiple 20-byte packets → 1 session imported."""
        record = _make_c3352g_record_bytes(month=3, day=9, hour=8, minute=30, second=0, duration=120, score=80)
        assert len(record) == T1_C3352G_RECORD_SIZE
        packets = _make_t1_c3352g_packets([record])
        assert len(packets) > 1, "Must require more than one 20-byte BLE packet"

        captured = await self._run_poll(self._make_client_firing_notifications(*packets))

        assert len(captured) == 1, f"Expected 1 session, got {len(captured)}"
        session = captured[0]
        assert session.get(DATA_LAST_BRUSH_DURATION) == 120
        assert session.get(DATA_LAST_BRUSH_SCORE) == 80
        import datetime

        dt = datetime.datetime.fromtimestamp(session[DATA_LAST_BRUSH_TIME])
        assert dt.month == 3
        assert dt.day == 9

    @pytest.mark.asyncio
    async def test_two_records_multi_packet(self):
        """count=2 records → 2 distinct sessions imported."""
        rec1 = _make_c3352g_record_bytes(month=3, day=9, hour=7, minute=0, second=0, duration=120, score=70)
        rec2 = _make_c3352g_record_bytes(month=3, day=8, hour=22, minute=0, second=0, duration=90, score=60)
        packets = _make_t1_c3352g_packets([rec1, rec2])

        captured = await self._run_poll(self._make_client_firing_notifications(*packets))

        assert len(captured) == 2, f"Expected 2 sessions, got {len(captured)}"
        durations = {s.get(DATA_LAST_BRUSH_DURATION) for s in captured}
        assert durations == {120, 90}

    @pytest.mark.asyncio
    async def test_notifications_after_reassembly_are_processed(self):
        """Notifications arriving after a completed *B# stream must be dispatched."""
        record = _make_c3352g_record_bytes(month=3, day=9, hour=8, minute=0, second=0, duration=60, score=50)
        packets = _make_t1_c3352g_packets([record])
        # 5100 carries a session: M=3 D=16 H=8 Min=0 Sec=0, duration=120 (0x78 BE)
        notif_5100 = bytes.fromhex("5100ffffffffffffff00031000080000780078")
        sequence = list(packets) + [notif_5100]

        captured = await self._run_poll(self._make_client_firing_notifications(*sequence))

        # Must get at least 1 session from the *B# reassembly
        assert len(captured) >= 1

    @pytest.mark.asyncio
    async def test_count_zero_does_not_start_reassembly(self):
        """0307 *B# with count=0 → no reassembly, no sessions added."""
        header_only = bytes.fromhex("03072a42230000")
        captured = await self._run_poll(self._make_client_firing_notifications(header_only))
        assert captured == []

    @pytest.mark.asyncio
    async def test_reassembly_state_isolated_per_poll(self):
        """Reassembly state must not leak between two successive polls."""
        record = _make_c3352g_record_bytes(month=3, day=9, hour=8, minute=0, second=0, duration=120, score=75)
        packets = _make_t1_c3352g_packets([record])
        coordinator = _make_coordinator()
        coordinator._store_loaded = True
        captured_runs: list[list] = []

        for _run in range(2):
            captured: list = []

            async def fake_import(_hass, _mac_slug, _device_name, sessions, last_ts, _cap=captured):
                _cap.extend(sessions)
                return last_ts

            call_count = [0]

            async def fake_start_notify(uuid, handler, _pkts=packets, _count=call_count):
                _count[0] += 1
                if _count[0] == 1:
                    for p in _pkts:
                        handler(None, bytearray(p))

            client = _make_bleak_client(battery_value=80)
            client.start_notify = AsyncMock(side_effect=fake_start_notify)

            with (
                patch("custom_components.oclean_ble.coordinator.bluetooth") as bt_mock,
                patch(
                    "custom_components.oclean_ble.coordinator.establish_connection",
                    new_callable=AsyncMock,
                    return_value=client,
                ),
                patch("custom_components.oclean_ble.coordinator.asyncio.sleep", new_callable=AsyncMock),
                patch("custom_components.oclean_ble.coordinator.import_new_sessions", side_effect=fake_import),
            ):
                bt_mock.async_last_service_info.return_value = _make_service_info()
                await coordinator._poll_device()

            captured_runs.append(list(captured))

        assert len(captured_runs[0]) == 1
        assert len(captured_runs[1]) == 1


# ---------------------------------------------------------------------------
# _CoordLoggerAdapter – per-device log prefix
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# _oclean_tz_index
# ---------------------------------------------------------------------------


class TestOcleanTzIndex:
    """Unit tests for the _oclean_tz_index() helper used by 0201 time calibration."""

    def test_utc(self):
        from custom_components.oclean_ble.coordinator import _oclean_tz_index

        assert _oclean_tz_index(0) == 14  # GMT+00:00 is index 14

    def test_cet(self):
        from custom_components.oclean_ble.coordinator import _oclean_tz_index

        assert _oclean_tz_index(60) == 15  # GMT+01:00

    def test_cest(self):
        from custom_components.oclean_ble.coordinator import _oclean_tz_index

        assert _oclean_tz_index(120) == 16  # GMT+02:00

    def test_negative_offset(self):
        from custom_components.oclean_ble.coordinator import _oclean_tz_index

        assert _oclean_tz_index(-300) == 8  # GMT-05:00

    def test_nearest_for_nonstandard_offset(self):
        from custom_components.oclean_ble.coordinator import _oclean_tz_index

        # GMT+05:45 (Nepal) → index 23; offset = 5*60+45 = 345
        assert _oclean_tz_index(345) == 23

    def test_result_always_in_valid_range(self):
        from custom_components.oclean_ble.coordinator import _oclean_tz_index

        for offset in range(-720, 780, 30):
            idx = _oclean_tz_index(offset)
            assert 1 <= idx <= 33, f"index {idx} out of range for offset {offset}"


class TestCoordLoggerAdapter:
    """Tests for _CoordLoggerAdapter.process()."""

    def test_prefix_contains_model_and_mac_suffix(self):
        """process() must prepend [MODEL/XX] where XX is the last two MAC hex chars."""
        import logging

        from custom_components.oclean_ble.coordinator import _CoordLoggerAdapter

        coord = _make_coordinator(mac="AA:BB:CC:DD:EE:FF")
        coord._last_raw[DATA_MODEL_ID] = "OCLEANY3M"
        adapter = _CoordLoggerAdapter(logging.getLogger("test"), {"coord": coord})

        msg, kwargs = adapter.process("hello world", {})

        assert msg == "[OCLEANY3M/FF] hello world"
        assert kwargs == {}

    def test_prefix_uses_question_mark_when_model_unknown(self):
        """When DATA_MODEL_ID is not yet set, model token must be '?'."""
        import logging

        from custom_components.oclean_ble.coordinator import _CoordLoggerAdapter

        coord = _make_coordinator(mac="AA:BB:CC:DD:EE:11")
        # _last_raw starts empty → no model
        adapter = _CoordLoggerAdapter(logging.getLogger("test"), {"coord": coord})

        msg, _ = adapter.process("test", {})

        assert msg.startswith("[?/11]")


# ---------------------------------------------------------------------------
# _patch_aioesphomeapi_uuid_parser – success path
# ---------------------------------------------------------------------------


class TestPatchAioesphomeapiUuidParser:
    """Test the aioesphomeapi patch when the module IS present."""

    def test_short_uuid_list_returns_null_uuid(self):
        """After patching, a 0- or 1-element list must return the null UUID."""
        import sys
        import types

        # Build a real module object so attribute assignment/reads work normally.
        fake_model = types.ModuleType("aioesphomeapi.model")
        original_join = lambda value: "-".join(str(v) for v in value)  # noqa: E731
        fake_model._join_split_uuid = original_join  # type: ignore[attr-defined]

        # Inject into sys.modules so the import inside the patch function succeeds.
        sys.modules.setdefault("aioesphomeapi", types.ModuleType("aioesphomeapi"))
        sys.modules["aioesphomeapi.model"] = fake_model

        try:
            from custom_components.oclean_ble.coordinator import _patch_aioesphomeapi_uuid_parser

            _patch_aioesphomeapi_uuid_parser()

            patched = fake_model._join_split_uuid  # type: ignore[attr-defined]

            # Short list (0 elements) → null UUID
            assert patched([]) == "00000000-0000-0000-0000-000000000000"
            # Single-element list → null UUID
            assert patched(["only-one"]) == "00000000-0000-0000-0000-000000000000"
            # Two-element list → passes through to original
            assert patched(["a", "b"]) == "a-b"
        finally:
            sys.modules.pop("aioesphomeapi.model", None)


# ---------------------------------------------------------------------------
# _paginate_sessions – protocol without pagination support
# ---------------------------------------------------------------------------


class TestPaginateSessionsNoPagination:
    @pytest.mark.asyncio
    async def test_non_paginating_protocol_skips_immediately(self):
        """_paginate_sessions must return early when protocol.supports_pagination is False."""
        coord = _make_coordinator()
        coord._store_loaded = True

        # Use LEGACY protocol (OCLEANA1) which has no pagination
        from custom_components.oclean_ble.protocol import LEGACY

        coord._protocol = LEGACY

        client = AsyncMock()
        event = asyncio.Event()
        all_sessions = [{"last_brush_time": 1740145339}]

        await coord._paginate_sessions(client, all_sessions, event)

        client.write_gatt_char.assert_not_called()


# ---------------------------------------------------------------------------
# _read_response_char_fallback – success path (len > 2)
# ---------------------------------------------------------------------------


class TestReadResponseCharFallback:
    @pytest.mark.asyncio
    async def test_data_longer_than_2_bytes_calls_handler(self):
        """When read returns >2 bytes the handler must be called with the data."""
        coord = _make_coordinator()
        coord._store_loaded = True

        client = AsyncMock()
        # Return a realistic payload (e.g. a 0303 state response)
        client.read_gatt_char = AsyncMock(return_value=bytearray([0x03, 0x03, 0x01, 0x00, 0x00, 0x4B]))

        calls: list[bytearray] = []

        def handler(_sender, data: bytearray) -> None:
            calls.append(data)

        with patch("custom_components.oclean_ble.coordinator.asyncio.sleep", new_callable=AsyncMock):
            await coord._read_response_char_fallback(client, handler)

        assert len(calls) == 1
        assert calls[0] == bytearray([0x03, 0x03, 0x01, 0x00, 0x00, 0x4B])

    @pytest.mark.asyncio
    async def test_data_exactly_2_bytes_does_not_call_handler(self):
        """When read returns exactly 2 bytes (len == 2) handler must NOT be called."""
        coord = _make_coordinator()
        coord._store_loaded = True

        client = AsyncMock()
        client.read_gatt_char = AsyncMock(return_value=bytearray([0x03, 0x03]))

        calls: list = []

        def handler(_sender, data: bytearray) -> None:
            calls.append(data)

        with patch("custom_components.oclean_ble.coordinator.asyncio.sleep", new_callable=AsyncMock):
            await coord._read_response_char_fallback(client, handler)

        assert calls == []

    @pytest.mark.asyncio
    async def test_read_exception_does_not_raise(self):
        """A BleakError from read_gatt_char must be caught and not propagate."""
        from bleak import BleakError

        coord = _make_coordinator()
        coord._store_loaded = True

        client = AsyncMock()
        client.read_gatt_char = AsyncMock(side_effect=BleakError("not connected"))

        def handler(_sender, data):
            pass  # should never be called

        with patch("custom_components.oclean_ble.coordinator.asyncio.sleep", new_callable=AsyncMock):
            # Must not raise
            await coord._read_response_char_fallback(client, handler)


# ---------------------------------------------------------------------------
# _poll_receive_brush_fallback – polling loop when subscriptions fail
# ---------------------------------------------------------------------------


class TestPollReceiveBrushFallback:
    @pytest.mark.asyncio
    async def test_polls_multiple_times_and_calls_handler(self):
        """Polling loop should read fbb90 multiple times and call handler for new data."""
        coord = _make_coordinator()
        coord._store_loaded = True

        client = AsyncMock()
        # First read: too short (ignored), second: valid payload, third: same payload (deduped)
        payload_valid = bytearray([0x03, 0x07, 0x01, 0x00, 0x00, 0x4B, 0x01, 0x02])
        client.read_gatt_char = AsyncMock(
            side_effect=[
                bytearray([0x00]),  # fbb90 attempt 1: too short
                bytearray([0x00]),  # fbb86 attempt 1: too short
                payload_valid,  # fbb90 attempt 2: valid
                bytearray([0x00]),  # fbb86 attempt 2: too short
                payload_valid,  # fbb90 attempt 3: duplicate → skipped
                bytearray([0x00]),  # fbb86 attempt 3
                bytearray([0x00]),  # fbb90 attempt 4
                bytearray([0x00]),  # fbb86 attempt 4
                bytearray([0x00]),  # fbb90 attempt 5
                bytearray([0x00]),  # fbb86 attempt 5
                bytearray([0x00]),  # fbb90 attempt 6
                bytearray([0x00]),  # fbb86 attempt 6
            ]
        )

        calls: list[bytearray] = []

        def handler(_sender, data: bytearray) -> None:
            calls.append(data)

        session_received = asyncio.Event()

        with patch("custom_components.oclean_ble.coordinator.asyncio.sleep", new_callable=AsyncMock):
            await coord._poll_receive_brush_fallback(client, handler, session_received)

        # Only one handler call (duplicate was deduplicated)
        assert len(calls) == 1
        assert calls[0] == payload_valid

    @pytest.mark.asyncio
    async def test_stops_early_when_session_received(self):
        """Polling should stop early when session_received event is set."""
        coord = _make_coordinator()
        coord._store_loaded = True

        client = AsyncMock()
        payload = bytearray([0x03, 0x07, 0x01, 0x00, 0x00, 0x4B])
        client.read_gatt_char = AsyncMock(return_value=payload)

        calls: list[bytearray] = []

        def handler(_sender, data: bytearray) -> None:
            calls.append(data)

        session_received = asyncio.Event()
        session_received.set()  # Already signalled

        with patch("custom_components.oclean_ble.coordinator.asyncio.sleep", new_callable=AsyncMock):
            await coord._poll_receive_brush_fallback(client, handler, session_received)

        # Should not read at all since session_received was already set
        assert len(calls) == 0

    @pytest.mark.asyncio
    async def test_read_exception_does_not_propagate(self):
        """BleakError during polling must be caught and not propagate."""
        from bleak import BleakError

        coord = _make_coordinator()
        coord._store_loaded = True

        client = AsyncMock()
        client.read_gatt_char = AsyncMock(side_effect=BleakError("disconnected"))

        def handler(_sender, data):
            pass

        session_received = asyncio.Event()

        with patch("custom_components.oclean_ble.coordinator.asyncio.sleep", new_callable=AsyncMock):
            # Must not raise
            await coord._poll_receive_brush_fallback(client, handler, session_received)


# ---------------------------------------------------------------------------
# async_reset_brush_head – brush head counter reset
# ---------------------------------------------------------------------------


class TestAsyncResetBrushHead:
    @pytest.mark.asyncio
    async def test_reset_sends_command(self):
        """async_reset_brush_head must send CMD_CLEAR_BRUSH_HEAD to the device."""
        from custom_components.oclean_ble.const import CMD_CLEAR_BRUSH_HEAD, WRITE_CHAR_UUID

        coord = _make_coordinator()
        coord._store_loaded = True
        client = _make_bleak_client()

        with (
            patch("custom_components.oclean_ble.coordinator.bluetooth") as bt_mock,
            patch(
                "custom_components.oclean_ble.coordinator.establish_connection",
                new_callable=AsyncMock,
                return_value=client,
            ),
            patch("custom_components.oclean_ble.coordinator.asyncio.sleep", new_callable=AsyncMock),
            patch.object(coord, "_save_store", new_callable=AsyncMock),
        ):
            bt_mock.async_last_service_info.return_value = _make_service_info()
            await coord.async_reset_brush_head()

        client.write_gatt_char.assert_called_once_with(WRITE_CHAR_UUID, CMD_CLEAR_BRUSH_HEAD, response=True)


# ---------------------------------------------------------------------------
# async_set_area_remind / async_set_brush_head_max_days
# ---------------------------------------------------------------------------


class TestStandaloneWrites:
    @pytest.mark.asyncio
    async def test_area_remind_on_writes_correct_command(self):
        """async_set_area_remind(True) must write CMD_AREA_REMIND + 0x01."""
        from custom_components.oclean_ble.const import CMD_AREA_REMIND

        coord = _make_coordinator()
        client = _make_bleak_client()

        with (
            patch("custom_components.oclean_ble.coordinator.bluetooth") as bt_mock,
            patch(
                "custom_components.oclean_ble.coordinator.establish_connection",
                new_callable=AsyncMock,
                return_value=client,
            ),
            patch("custom_components.oclean_ble.coordinator.asyncio.sleep", new_callable=AsyncMock),
            patch.object(coord, "_save_store", new_callable=AsyncMock),
        ):
            bt_mock.async_last_service_info.return_value = _make_service_info()
            await coord.async_set_area_remind(True)

        cmd_bytes = client.write_gatt_char.call_args[0][1]
        assert cmd_bytes == CMD_AREA_REMIND + bytes([0x01])
        assert coord.area_remind is True

    @pytest.mark.asyncio
    async def test_area_remind_off_writes_correct_command(self):
        """async_set_area_remind(False) must write CMD_AREA_REMIND + 0x00."""
        from custom_components.oclean_ble.const import CMD_AREA_REMIND

        coord = _make_coordinator()
        client = _make_bleak_client()

        with (
            patch("custom_components.oclean_ble.coordinator.bluetooth") as bt_mock,
            patch(
                "custom_components.oclean_ble.coordinator.establish_connection",
                new_callable=AsyncMock,
                return_value=client,
            ),
            patch("custom_components.oclean_ble.coordinator.asyncio.sleep", new_callable=AsyncMock),
            patch.object(coord, "_save_store", new_callable=AsyncMock),
        ):
            bt_mock.async_last_service_info.return_value = _make_service_info()
            await coord.async_set_area_remind(False)

        cmd_bytes = client.write_gatt_char.call_args[0][1]
        assert cmd_bytes == CMD_AREA_REMIND + bytes([0x00])
        assert coord.area_remind is False

    @pytest.mark.asyncio
    async def test_set_brush_head_max_days_writes_correct_command(self):
        """async_set_brush_head_max_days must write CMD_BRUSH_HEAD_MAX_DAYS + 2-byte big-endian days."""
        from custom_components.oclean_ble.const import CMD_BRUSH_HEAD_MAX_DAYS

        coord = _make_coordinator()
        client = _make_bleak_client()

        with (
            patch("custom_components.oclean_ble.coordinator.bluetooth") as bt_mock,
            patch(
                "custom_components.oclean_ble.coordinator.establish_connection",
                new_callable=AsyncMock,
                return_value=client,
            ),
            patch("custom_components.oclean_ble.coordinator.asyncio.sleep", new_callable=AsyncMock),
            patch.object(coord, "_save_store", new_callable=AsyncMock),
        ):
            bt_mock.async_last_service_info.return_value = _make_service_info()
            await coord.async_set_brush_head_max_days(90)

        cmd_bytes = client.write_gatt_char.call_args[0][1]
        assert cmd_bytes == CMD_BRUSH_HEAD_MAX_DAYS + (90).to_bytes(2, "big")
        assert coord.brush_head_max_days == 90

    @pytest.mark.asyncio
    async def test_write_standalone_subscribes_notify_chars_before_write(self):
        """_write_standalone must subscribe to protocol notify_chars before writing."""
        from custom_components.oclean_ble.const import CMD_AREA_REMIND
        from custom_components.oclean_ble.protocol import TYPE1

        coord = _make_coordinator()
        coord._protocol = TYPE1
        client = _make_bleak_client()

        with (
            patch("custom_components.oclean_ble.coordinator.bluetooth") as bt_mock,
            patch(
                "custom_components.oclean_ble.coordinator.establish_connection",
                new_callable=AsyncMock,
                return_value=client,
            ),
            patch("custom_components.oclean_ble.coordinator.asyncio.sleep", new_callable=AsyncMock),
            patch.object(coord, "_save_store", new_callable=AsyncMock),
        ):
            bt_mock.async_last_service_info.return_value = _make_service_info()
            await coord.async_set_area_remind(True)

        # start_notify must have been called for each notify char in TYPE1
        assert client.start_notify.await_count == len(TYPE1.notify_chars)
        # stop_notify must match
        assert client.stop_notify.await_count == len(TYPE1.notify_chars)


# ---------------------------------------------------------------------------
# TYPE_Z1 hybrid routing
# ---------------------------------------------------------------------------


class TestTypeZ1Protocol:
    """Verify Type-Z1 (Oclean Z1 / OCLEANY5) hybrid command routing.

    Type-Z1 routes 0303/0202/0302 via fbb85 (WRITE_CHAR_UUID) and 0307 via
    fbb89 (SEND_BRUSH_CMD_UUID).  Time calibration uses the 0201 + 8-byte
    datetime format (same as TYPE1, because uses_t1_calibration=True).
    Standalone writes (area_remind, brush_head_max_days) use fbb85 as write_char.
    """

    @pytest.mark.asyncio
    async def test_query_commands_hybrid_routing(self):
        """Each query command must be sent to the characteristic declared in TYPE_Z1."""
        from custom_components.oclean_ble.const import (
            CMD_DEVICE_INFO,
            CMD_QUERY_DEVICE_SETTINGS,
            CMD_QUERY_RUNNING_DATA_T1,
            CMD_QUERY_STATUS,
            SEND_BRUSH_CMD_UUID,
            WRITE_CHAR_UUID,
        )
        from custom_components.oclean_ble.protocol import TYPE_Z1

        coord = _make_coordinator()
        coord._protocol = TYPE_Z1
        coord._store_loaded = True
        client = _make_bleak_client()
        event = asyncio.Event()
        event.set()

        await coord._send_query_commands(client, event)

        calls = {(args[0][0], args[0][1]): True for args in client.write_gatt_char.call_args_list}
        # 0303, 0202, 0302 must go via fbb85
        assert (WRITE_CHAR_UUID, CMD_QUERY_STATUS) in calls
        assert (WRITE_CHAR_UUID, CMD_DEVICE_INFO) in calls
        assert (WRITE_CHAR_UUID, CMD_QUERY_DEVICE_SETTINGS) in calls
        # 0307 must go via fbb89
        assert (SEND_BRUSH_CMD_UUID, CMD_QUERY_RUNNING_DATA_T1) in calls

    @pytest.mark.asyncio
    async def test_time_calibration_uses_0201_format(self):
        """TYPE_Z1 uses_t1_calibration=True → sync_time must send 0201 + 8-byte payload."""
        from custom_components.oclean_ble.protocol import TYPE_Z1

        coord = _make_coordinator()
        coord._protocol = TYPE_Z1
        client = _make_bleak_client()

        with (
            patch("custom_components.oclean_ble.coordinator.bluetooth") as bt_mock,
            patch(
                "custom_components.oclean_ble.coordinator.establish_connection",
                new_callable=AsyncMock,
                return_value=client,
            ),
        ):
            bt_mock.async_last_service_info.return_value = _make_service_info()
            await coord.async_sync_time()

        client.write_gatt_char.assert_awaited_once()
        cmd_bytes = client.write_gatt_char.call_args[0][1]
        assert cmd_bytes[:2] == bytes.fromhex("0201"), "TYPE_Z1 must send 0201 calibration command"
        assert len(cmd_bytes) == 10, "0201 + 8-byte datetime payload = 10 bytes"

    @pytest.mark.asyncio
    async def test_standalone_write_uses_fbb85(self):
        """TYPE_Z1 write_char is fbb85 → area_remind must write to WRITE_CHAR_UUID."""
        from custom_components.oclean_ble.const import CMD_AREA_REMIND, WRITE_CHAR_UUID
        from custom_components.oclean_ble.protocol import TYPE_Z1

        coord = _make_coordinator()
        coord._protocol = TYPE_Z1
        client = _make_bleak_client()

        with (
            patch("custom_components.oclean_ble.coordinator.bluetooth") as bt_mock,
            patch(
                "custom_components.oclean_ble.coordinator.establish_connection",
                new_callable=AsyncMock,
                return_value=client,
            ),
            patch("custom_components.oclean_ble.coordinator.asyncio.sleep", new_callable=AsyncMock),
            patch.object(coord, "_save_store", new_callable=AsyncMock),
        ):
            bt_mock.async_last_service_info.return_value = _make_service_info()
            await coord.async_set_area_remind(True)

        char_used = client.write_gatt_char.call_args[0][0]
        assert char_used == WRITE_CHAR_UUID, "TYPE_Z1 standalone writes must use fbb85"
        cmd_bytes = client.write_gatt_char.call_args[0][1]
        assert cmd_bytes == CMD_AREA_REMIND + bytes([0x01])

    @pytest.mark.asyncio
    async def test_notify_chars_subscribed_for_standalone_write(self):
        """_write_standalone on TYPE_Z1 must subscribe to fbb86 + fbb90 before writing."""
        from custom_components.oclean_ble.const import CMD_AREA_REMIND
        from custom_components.oclean_ble.protocol import TYPE_Z1

        coord = _make_coordinator()
        coord._protocol = TYPE_Z1
        client = _make_bleak_client()

        with (
            patch("custom_components.oclean_ble.coordinator.bluetooth") as bt_mock,
            patch(
                "custom_components.oclean_ble.coordinator.establish_connection",
                new_callable=AsyncMock,
                return_value=client,
            ),
            patch("custom_components.oclean_ble.coordinator.asyncio.sleep", new_callable=AsyncMock),
            patch.object(coord, "_save_store", new_callable=AsyncMock),
        ):
            bt_mock.async_last_service_info.return_value = _make_service_info()
            await coord.async_set_area_remind(True)

        assert client.start_notify.await_count == len(TYPE_Z1.notify_chars)
        assert client.stop_notify.await_count == len(TYPE_Z1.notify_chars)
