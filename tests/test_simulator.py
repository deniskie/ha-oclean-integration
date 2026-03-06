"""Integration tests using OcleanDeviceSimulator.

Tests the complete pipeline from BLE notification bytes through the coordinator
to sensor native_value, without a real device or a full HA instance.

Each test class covers one device scenario or sensor entity type.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# conftest.py stubs HA + bleak before these imports
from custom_components.oclean_ble.coordinator import OcleanCoordinator, _NOTIFY_CHARS
from custom_components.oclean_ble.const import (
    DATA_BATTERY,
    DATA_LAST_BRUSH_AREAS,
    DATA_LAST_BRUSH_DURATION,
    DATA_LAST_BRUSH_PRESSURE,
    DATA_LAST_BRUSH_PNUM,
    DATA_LAST_BRUSH_SCORE,
    DATA_LAST_BRUSH_TIME,
)
from custom_components.oclean_ble.models import OcleanDeviceData

from tests.simulator import OcleanDeviceSimulator


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _make_hass():
    hass = MagicMock()
    hass.data = {}
    return hass


def _make_coordinator(mac: str = "AA:BB:CC:DD:EE:FF") -> OcleanCoordinator:
    coord = OcleanCoordinator(_make_hass(), mac, "Oclean", 300)
    coord._store_loaded = True  # skip async store load on first poll
    return coord


def _make_service_info(mac: str = "AA:BB:CC:DD:EE:FF"):
    device = MagicMock()
    device.address = mac
    si = MagicMock()
    si.device = device
    return si


async def _run_poll(coordinator: OcleanCoordinator, client: AsyncMock) -> dict:
    """Run a single _poll_device() with standard mocks and return the raw result.

    Patches:
    - bluetooth: returns a valid service_info so establish_connection is reached
    - establish_connection: returns the given client mock
    - asyncio.sleep: no-op to avoid real delays
    - _paginate_sessions: no-op to avoid the 2-second asyncio.wait_for timeout
    - _import_new_sessions: no-op (HA recorder not available in unit tests)
    """
    with patch("custom_components.oclean_ble.coordinator.bluetooth") as bt_mock, \
         patch(
             "custom_components.oclean_ble.coordinator.establish_connection",
             new_callable=AsyncMock,
             return_value=client,
         ), \
         patch(
             "custom_components.oclean_ble.coordinator.asyncio.sleep",
             new_callable=AsyncMock,
         ), \
         patch.object(coordinator, "_paginate_sessions", new_callable=AsyncMock), \
         patch.object(coordinator, "_import_new_sessions", new_callable=AsyncMock):
        bt_mock.async_last_service_info.return_value = _make_service_info()
        return await coordinator._poll_device()


# ---------------------------------------------------------------------------
# Oclean X (Type-1, 0307) scenarios
# ---------------------------------------------------------------------------


class TestOcleanXScenarios:
    """End-to-end poll tests for Oclean X (OCLEANY3M, 0307 path).

    The Oclean X sends session data across three separate notifications:
      0307 → timestamp, pNum, duration
      2604 → 8 tooth-area pressures (average = pressure)
      0000 → score
    """

    @pytest.mark.asyncio
    async def test_session_all_fields_present(self):
        """0307 + 2604 + 0000 → all session fields populated."""
        coordinator = _make_coordinator()
        client = (
            OcleanDeviceSimulator()
            .with_battery(82)
            .add_0307_session(2026, 2, 21, 15, 42, 19, pnum=77, duration=150)
            .add_brush_areas((24, 24, 26, 26, 7, 16, 7, 16))
            .add_score(95)
            .build_client()
        )

        result = await _run_poll(coordinator, client)

        assert result[DATA_BATTERY] == 82
        assert result[DATA_LAST_BRUSH_SCORE] == 95
        assert result[DATA_LAST_BRUSH_DURATION] == 150
        assert result[DATA_LAST_BRUSH_PNUM] == 77
        assert isinstance(result[DATA_LAST_BRUSH_AREAS], dict)
        assert len(result[DATA_LAST_BRUSH_AREAS]) == 8
        assert result[DATA_LAST_BRUSH_PRESSURE] > 0
        assert result[DATA_LAST_BRUSH_TIME] > 0

    @pytest.mark.asyncio
    async def test_session_without_areas_no_pressure_or_areas(self):
        """0307 + 0000 only (no 2604) → areas and pressure must be absent."""
        coordinator = _make_coordinator()
        client = (
            OcleanDeviceSimulator()
            .add_0307_session(2026, 2, 21, 15, 42, 19, pnum=77, duration=150)
            .add_score(95)
            .build_client()
        )

        result = await _run_poll(coordinator, client)

        assert result.get(DATA_LAST_BRUSH_AREAS) is None
        assert result.get(DATA_LAST_BRUSH_PRESSURE) is None
        # Score still arrives via 0000
        assert result[DATA_LAST_BRUSH_SCORE] == 95

    @pytest.mark.asyncio
    async def test_score_0xff_not_set(self):
        """0000 with score=0xFF (no data) must leave last_brush_score unset."""
        coordinator = _make_coordinator()
        client = (
            OcleanDeviceSimulator()
            .add_0307_session(2026, 2, 21, 15, 42, 19, pnum=77, duration=150)
            .add_score(0xFF)
            .build_client()
        )

        result = await _run_poll(coordinator, client)

        assert result.get(DATA_LAST_BRUSH_SCORE) is None

    @pytest.mark.asyncio
    async def test_battery_always_present(self):
        """Battery must be populated even when no session notification is received."""
        coordinator = _make_coordinator()
        client = OcleanDeviceSimulator().with_battery(55).build_client()

        result = await _run_poll(coordinator, client)

        assert result[DATA_BATTERY] == 55
        assert result.get(DATA_LAST_BRUSH_TIME) is None

    @pytest.mark.asyncio
    async def test_area_pressure_is_average_of_zone_pressures(self):
        """last_brush_pressure must equal round(sum(areas) / 8)."""
        area_pressures = (10, 20, 30, 40, 50, 60, 70, 80)
        expected_avg = round(sum(area_pressures) / len(area_pressures))

        coordinator = _make_coordinator()
        client = (
            OcleanDeviceSimulator()
            .add_0307_session(2026, 2, 21, 15, 42, 19, pnum=0, duration=120)
            .add_brush_areas(area_pressures)
            .build_client()
        )

        result = await _run_poll(coordinator, client)

        assert result[DATA_LAST_BRUSH_PRESSURE] == expected_avg

    @pytest.mark.asyncio
    async def test_all_8_tooth_areas_in_areas_dict(self):
        """last_brush_areas must contain exactly 8 zone keys."""
        from custom_components.oclean_ble.const import TOOTH_AREA_NAMES

        coordinator = _make_coordinator()
        client = (
            OcleanDeviceSimulator()
            .add_0307_session(2026, 2, 21, 15, 42, 19, pnum=0, duration=120)
            .add_brush_areas((5, 10, 15, 20, 25, 30, 35, 40))
            .build_client()
        )

        result = await _run_poll(coordinator, client)
        areas = result[DATA_LAST_BRUSH_AREAS]

        assert set(areas.keys()) == set(TOOTH_AREA_NAMES)
        # Values must match the order of TOOTH_AREA_NAMES
        for i, name in enumerate(TOOTH_AREA_NAMES):
            assert areas[name] == (i + 1) * 5, f"Wrong pressure for zone {name}"

    @pytest.mark.asyncio
    async def test_score_enriched_into_session_snapshot(self):
        """0000 score must be merged into the all_sessions snapshot for stats import."""
        coordinator = _make_coordinator()
        client = (
            OcleanDeviceSimulator()
            .add_0307_session(2026, 2, 21, 15, 42, 19, pnum=77, duration=150)
            .add_score(88)
            .build_client()
        )

        captured: list = []

        async def fake_import(sessions):
            captured.extend(sessions)

        with patch("custom_components.oclean_ble.coordinator.bluetooth") as bt_mock, \
             patch(
                 "custom_components.oclean_ble.coordinator.establish_connection",
                 new_callable=AsyncMock,
                 return_value=client,
             ), \
             patch(
                 "custom_components.oclean_ble.coordinator.asyncio.sleep",
                 new_callable=AsyncMock,
             ), \
             patch.object(coordinator, "_paginate_sessions", new_callable=AsyncMock), \
             patch.object(coordinator, "_import_new_sessions", side_effect=fake_import):
            bt_mock.async_last_service_info.return_value = _make_service_info()
            await coordinator._poll_device()

        assert captured, "At least one session must have been captured"
        newest = max(captured, key=lambda s: s.get("last_brush_time", 0))
        assert newest.get(DATA_LAST_BRUSH_SCORE) == 88


# ---------------------------------------------------------------------------
# Oclean X Pro (Type-0, 0308 extended) scenarios
# ---------------------------------------------------------------------------


class TestOcleanXProScenarios:
    """End-to-end poll tests for Oclean X Pro (OCLEANY3/Y3P, 0308 extended path).

    The Oclean X Pro sends all session fields in a single extended 0308 record:
    timestamp, pNum, duration, score, 8 tooth-area pressures, tz offset.
    """

    @pytest.mark.asyncio
    async def test_extended_session_all_fields(self):
        """0308 extended → all session fields populated in one notification."""
        areas = (15, 20, 10, 12, 18, 25, 30, 8)
        coordinator = _make_coordinator()
        client = (
            OcleanDeviceSimulator()
            .with_battery(65)
            .add_0308_extended_session(
                2026, 2, 24, 7, 30, 0,
                pnum=42, duration=120, score=88,
                area_pressures=areas,
            )
            .build_client()
        )

        result = await _run_poll(coordinator, client)

        assert result[DATA_BATTERY] == 65
        assert result[DATA_LAST_BRUSH_SCORE] == 88
        assert result[DATA_LAST_BRUSH_DURATION] == 120
        assert result[DATA_LAST_BRUSH_PNUM] == 42
        assert isinstance(result[DATA_LAST_BRUSH_AREAS], dict)
        expected_avg = round(sum(areas) / len(areas))
        assert result[DATA_LAST_BRUSH_PRESSURE] == expected_avg

    @pytest.mark.asyncio
    async def test_0308_score_not_overwritten_by_0000(self):
        """Score embedded in 0308 extended must not be overwritten by a later 0000."""
        coordinator = _make_coordinator()
        client = (
            OcleanDeviceSimulator()
            .add_0308_extended_session(
                2026, 2, 24, 7, 30, 0,
                pnum=42, duration=120, score=42,   # score from 0308 = 42
            )
            .add_score(99)   # 0000 tries to push score=99 – must be ignored
            .build_client()
        )

        captured: list = []

        async def fake_import(sessions):
            captured.extend(sessions)

        with patch("custom_components.oclean_ble.coordinator.bluetooth") as bt_mock, \
             patch(
                 "custom_components.oclean_ble.coordinator.establish_connection",
                 new_callable=AsyncMock,
                 return_value=client,
             ), \
             patch(
                 "custom_components.oclean_ble.coordinator.asyncio.sleep",
                 new_callable=AsyncMock,
             ), \
             patch.object(coordinator, "_paginate_sessions", new_callable=AsyncMock), \
             patch.object(coordinator, "_import_new_sessions", side_effect=fake_import):
            bt_mock.async_last_service_info.return_value = _make_service_info()
            await coordinator._poll_device()

        assert captured, "At least one session must have been captured"
        newest = max(captured, key=lambda s: s.get("last_brush_time", 0))
        assert newest.get(DATA_LAST_BRUSH_SCORE) == 42, (
            "Score from 0308 extended must not be overwritten by 0000 enrichment"
        )

    @pytest.mark.asyncio
    async def test_score_clamped_to_100(self):
        """Score values above 100 in the BLE payload must be clamped to 100."""
        coordinator = _make_coordinator()
        client = (
            OcleanDeviceSimulator()
            .add_0308_extended_session(
                2026, 2, 24, 7, 30, 0,
                pnum=1, duration=90, score=127,  # out-of-range raw value
            )
            .build_client()
        )

        result = await _run_poll(coordinator, client)

        assert result.get(DATA_LAST_BRUSH_SCORE) == 100

    @pytest.mark.asyncio
    async def test_tz_offset_applied_to_timestamp(self):
        """Non-zero tz_offset_quarters must shift the UTC timestamp accordingly."""
        # Device reports 07:30:00 local time, UTC+2 (8 quarter-hours)
        # UTC timestamp should be 05:30:00 UTC
        coordinator_utc = _make_coordinator()
        coordinator_utcplus2 = _make_coordinator(mac="BB:BB:BB:BB:BB:BB")

        def _make_client(tz: int):
            return (
                OcleanDeviceSimulator()
                .add_0308_extended_session(
                    2026, 2, 24, 7, 30, 0,
                    pnum=1, duration=90, score=80,
                    tz_offset_quarters=tz,
                )
                .build_client()
            )

        result_utc = await _run_poll(coordinator_utc, _make_client(0))
        result_utcplus2 = await _run_poll(coordinator_utcplus2, _make_client(8))

        ts_utc = result_utc[DATA_LAST_BRUSH_TIME]
        ts_utcplus2 = result_utcplus2[DATA_LAST_BRUSH_TIME]

        # UTC+2 device: same local time → UTC timestamp 2 hours earlier
        assert ts_utc - ts_utcplus2 == 2 * 3600


# ---------------------------------------------------------------------------
# Sensor state mapping (OcleanDeviceData → sensor entity native_value)
# ---------------------------------------------------------------------------


class TestSensorStateMapping:
    """OcleanDeviceData → sensor entity native_value mapping tests.

    A coordinator mock is given a pre-built OcleanDeviceData and the
    sensor's native_value property is checked directly, without BLE.
    """

    def _make_sensor_coordinator(self, data: OcleanDeviceData):
        coord = MagicMock()
        coord.data = data
        coord.last_update_success = True
        return coord

    def test_score_sensor_native_value(self):
        from custom_components.oclean_ble.sensor import OcleanSensor, SENSOR_DESCRIPTIONS

        data = OcleanDeviceData(last_brush_score=88)
        coord = self._make_sensor_coordinator(data)
        desc = next(d for d in SENSOR_DESCRIPTIONS if d.key == DATA_LAST_BRUSH_SCORE)
        sensor = OcleanSensor(coord, desc, "AA:BB:CC:DD:EE:FF", "Oclean")

        assert sensor.native_value == 88

    def test_duration_sensor_native_value(self):
        from custom_components.oclean_ble.sensor import OcleanSensor, SENSOR_DESCRIPTIONS

        data = OcleanDeviceData(last_brush_duration=150)
        coord = self._make_sensor_coordinator(data)
        desc = next(d for d in SENSOR_DESCRIPTIONS if d.key == DATA_LAST_BRUSH_DURATION)
        sensor = OcleanSensor(coord, desc, "AA:BB:CC:DD:EE:FF", "Oclean")

        assert sensor.native_value == 150

    def test_timestamp_sensor_returns_datetime(self):
        from datetime import datetime
        from custom_components.oclean_ble.sensor import OcleanSensor, SENSOR_DESCRIPTIONS

        ts = 1740145339  # 2026-02-21 ~15:42 UTC
        data = OcleanDeviceData(last_brush_time=ts)
        coord = self._make_sensor_coordinator(data)
        desc = next(d for d in SENSOR_DESCRIPTIONS if d.key == DATA_LAST_BRUSH_TIME)
        sensor = OcleanSensor(coord, desc, "AA:BB:CC:DD:EE:FF", "Oclean")

        result = sensor.native_value
        assert isinstance(result, datetime)
        assert result.timestamp() == pytest.approx(ts, abs=1)

    def test_battery_sensor_native_value(self):
        from custom_components.oclean_ble.sensor import OcleanSensor, SENSOR_DESCRIPTIONS

        data = OcleanDeviceData(battery=77)
        coord = self._make_sensor_coordinator(data)
        desc = next(d for d in SENSOR_DESCRIPTIONS if d.key == DATA_BATTERY)
        sensor = OcleanSensor(coord, desc, "AA:BB:CC:DD:EE:FF", "Oclean")

        assert sensor.native_value == 77

    def test_areas_sensor_counts_nonzero_zones(self):
        from custom_components.oclean_ble.sensor import OcleanBrushAreasSensor
        from custom_components.oclean_ble.const import TOOTH_AREA_NAMES

        areas = {name: (10 if i < 5 else 0) for i, name in enumerate(TOOTH_AREA_NAMES)}
        data = OcleanDeviceData(last_brush_areas=areas)
        coord = self._make_sensor_coordinator(data)
        sensor = OcleanBrushAreasSensor(coord, "AA:BB:CC:DD:EE:FF", "Oclean")

        assert sensor.native_value == 5  # 5 zones with pressure > 0

    def test_areas_sensor_extra_attributes_are_per_zone_dict(self):
        from custom_components.oclean_ble.sensor import OcleanBrushAreasSensor
        from custom_components.oclean_ble.const import TOOTH_AREA_NAMES

        areas = {name: i * 10 for i, name in enumerate(TOOTH_AREA_NAMES)}
        data = OcleanDeviceData(last_brush_areas=areas)
        coord = self._make_sensor_coordinator(data)
        sensor = OcleanBrushAreasSensor(coord, "AA:BB:CC:DD:EE:FF", "Oclean")

        attrs = sensor.extra_state_attributes
        assert isinstance(attrs, dict)
        assert set(attrs.keys()) == set(TOOTH_AREA_NAMES)

    def test_scheme_sensor_returns_known_name(self):
        from custom_components.oclean_ble.sensor import OcleanSchemeSensor
        from custom_components.oclean_ble.const import SCHEME_NAMES

        pnum = next(iter(SCHEME_NAMES))
        expected_name = SCHEME_NAMES[pnum]
        data = OcleanDeviceData(last_brush_pnum=pnum)
        coord = self._make_sensor_coordinator(data)
        sensor = OcleanSchemeSensor(coord, "AA:BB:CC:DD:EE:FF", "Oclean")

        assert sensor.native_value == expected_name

    def test_scheme_sensor_returns_pnum_string_for_unknown(self):
        from custom_components.oclean_ble.sensor import OcleanSchemeSensor
        from custom_components.oclean_ble.const import SCHEME_NAMES

        pnum = 9999
        assert pnum not in SCHEME_NAMES
        data = OcleanDeviceData(last_brush_pnum=pnum)
        coord = self._make_sensor_coordinator(data)
        sensor = OcleanSchemeSensor(coord, "AA:BB:CC:DD:EE:FF", "Oclean")

        assert sensor.native_value == str(pnum)

    def test_score_sensor_unavailable_when_session_received_but_no_score(self):
        """Session-derived field: available=False when time is set but score is None."""
        from custom_components.oclean_ble.sensor import OcleanSensor, SENSOR_DESCRIPTIONS

        data = OcleanDeviceData(last_brush_time=1740145339, last_brush_score=None)
        coord = self._make_sensor_coordinator(data)
        desc = next(d for d in SENSOR_DESCRIPTIONS if d.key == DATA_LAST_BRUSH_SCORE)
        sensor = OcleanSensor(coord, desc, "AA:BB:CC:DD:EE:FF", "Oclean")

        assert sensor.available is False

    def test_battery_sensor_always_available_when_data_present(self):
        from custom_components.oclean_ble.sensor import OcleanSensor, SENSOR_DESCRIPTIONS

        data = OcleanDeviceData(battery=77)
        coord = self._make_sensor_coordinator(data)
        desc = next(d for d in SENSOR_DESCRIPTIONS if d.key == DATA_BATTERY)
        sensor = OcleanSensor(coord, desc, "AA:BB:CC:DD:EE:FF", "Oclean")

        assert sensor.available is True

    def test_score_sensor_none_when_coordinator_data_is_none(self):
        from custom_components.oclean_ble.sensor import OcleanSensor, SENSOR_DESCRIPTIONS

        coord = MagicMock()
        coord.data = None
        coord.last_update_success = True
        desc = next(d for d in SENSOR_DESCRIPTIONS if d.key == DATA_LAST_BRUSH_SCORE)
        sensor = OcleanSensor(coord, desc, "AA:BB:CC:DD:EE:FF", "Oclean")

        assert sensor.native_value is None


# ---------------------------------------------------------------------------
# Oclean Air 1 (OCLEANA1) – all notify characteristics fail  (issue #7)
# ---------------------------------------------------------------------------


class TestOcleanA1AllNotifyFail:
    """Issue #7: Oclean Air 1 (OCLEANA1) – all notify characteristics fail.

    Real device behaviour captured in logs/20260227_#7_bato2000_oclean_ble.log:
      - fbb86, fbb90: no CCCD descriptor → BleakError
      - fbb89: no notify property → BleakError
      - 6c290d2e: not found → BleakError
    The integration must complete the poll gracefully with only battery data.
    """

    @pytest.mark.asyncio
    async def test_poll_completes_without_crash(self):
        from bleak import BleakError

        client = (
            OcleanDeviceSimulator()
            .with_battery(99)
            .with_notify_errors({uuid: BleakError("no notify") for uuid in _NOTIFY_CHARS})
            .build_client()
        )
        result = await _run_poll(_make_coordinator(), client)
        assert result is not None

    @pytest.mark.asyncio
    async def test_battery_still_available(self):
        from bleak import BleakError

        client = (
            OcleanDeviceSimulator()
            .with_battery(99)
            .with_notify_errors({uuid: BleakError("no notify") for uuid in _NOTIFY_CHARS})
            .build_client()
        )
        result = await _run_poll(_make_coordinator(), client)
        assert result[DATA_BATTERY] == 99

    @pytest.mark.asyncio
    async def test_no_session_data_when_all_notify_fail(self):
        from bleak import BleakError

        client = (
            OcleanDeviceSimulator()
            .with_battery(99)
            .with_notify_errors({uuid: BleakError("no notify") for uuid in _NOTIFY_CHARS})
            .build_client()
        )
        result = await _run_poll(_make_coordinator(), client)
        assert result.get(DATA_LAST_BRUSH_TIME) is None
        assert result.get(DATA_LAST_BRUSH_SCORE) is None
        assert result.get(DATA_LAST_BRUSH_DURATION) is None
