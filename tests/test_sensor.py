"""Tests for sensor.py – all four sensor entity classes."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

# conftest.py stubs HA + bleak before these imports
from homeassistant.components.sensor import SensorDeviceClass, SensorEntityDescription

from custom_components.oclean_ble.const import (
    DATA_LAST_BRUSH_AREAS,
    DATA_LAST_BRUSH_PNUM,
    DATA_LAST_BRUSH_SCORE,
    DATA_LAST_BRUSH_TIME,
    SCHEME_NAMES,
    TOOTH_AREA_NAMES,
)
from custom_components.oclean_ble.sensor import (
    OcleanBrushAreasSensor,
    OcleanSchemeSensor,
    OcleanSensor,
    OcleanToothAreaSensor,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_coordinator(data=None, last_update_success=True):
    coord = MagicMock()
    coord.data = data
    coord.last_update_success = last_update_success
    return coord


def _make_sensor(key, device_class=None, data=None, last_update_success=True):
    coord = _make_coordinator(data=data, last_update_success=last_update_success)
    desc = SensorEntityDescription(key=key, device_class=device_class)
    return OcleanSensor(coord, desc, "AA:BB:CC:DD:EE:FF", "Oclean")


def _make_areas_sensor(data=None, last_update_success=True):
    coord = _make_coordinator(data=data, last_update_success=last_update_success)
    return OcleanBrushAreasSensor(coord, "AA:BB:CC:DD:EE:FF", "Oclean")


def _make_scheme_sensor(data=None, last_update_success=True):
    coord = _make_coordinator(data=data, last_update_success=last_update_success)
    return OcleanSchemeSensor(coord, "AA:BB:CC:DD:EE:FF", "Oclean")


def _make_tooth_sensor(zone_name, data=None, last_update_success=True):
    coord = _make_coordinator(data=data, last_update_success=last_update_success)
    return OcleanToothAreaSensor(coord, "AA:BB:CC:DD:EE:FF", "Oclean", zone_name)


# ---------------------------------------------------------------------------
# OcleanSensor.native_value
# ---------------------------------------------------------------------------


class TestOcleanSensorNativeValue:
    def test_returns_none_when_data_is_none(self):
        sensor = _make_sensor("battery", data=None)
        assert sensor.native_value is None

    def test_returns_none_when_value_is_none(self):
        sensor = _make_sensor("battery", data={"battery": None})
        assert sensor.native_value is None

    def test_returns_raw_value_for_non_timestamp(self):
        sensor = _make_sensor("battery", data={"battery": 80})
        assert sensor.native_value == 80

    def test_timestamp_class_converts_to_datetime(self):
        from datetime import datetime

        sensor = _make_sensor(
            DATA_LAST_BRUSH_TIME,
            device_class=SensorDeviceClass.TIMESTAMP,
            data={DATA_LAST_BRUSH_TIME: 1_700_000_000},
        )
        result = sensor.native_value
        assert isinstance(result, datetime)

    def test_timestamp_invalid_value_returns_none(self):
        sensor = _make_sensor(
            DATA_LAST_BRUSH_TIME,
            device_class=SensorDeviceClass.TIMESTAMP,
            data={DATA_LAST_BRUSH_TIME: "not-a-timestamp"},
        )
        assert sensor.native_value is None

    def test_timestamp_out_of_range_returns_none(self):
        """OSError on platforms with limited timestamp range."""
        sensor = _make_sensor(
            DATA_LAST_BRUSH_TIME,
            device_class=SensorDeviceClass.TIMESTAMP,
            data={DATA_LAST_BRUSH_TIME: 99_999_999_999_999},
        )
        # May return None (OSError) or a datetime depending on the platform
        result = sensor.native_value
        assert result is None or hasattr(result, "year")


# ---------------------------------------------------------------------------
# OcleanSensor.available
# ---------------------------------------------------------------------------


class TestOcleanSensorAvailable:
    def test_stale_data_with_value_is_available(self):
        sensor = _make_sensor("battery", data={"battery": 50}, last_update_success=False)
        assert sensor.available is True

    def test_stale_data_without_value_is_unavailable(self):
        sensor = _make_sensor("battery", data={"battery": None}, last_update_success=False)
        assert sensor.available is False

    def test_stale_data_no_data_dict_is_unavailable(self):
        sensor = _make_sensor("battery", data=None, last_update_success=False)
        assert sensor.available is False

    def test_session_key_after_first_session_value_none_is_unavailable(self):
        """Device had a session but score field is still None → not supported."""
        sensor = _make_sensor(
            DATA_LAST_BRUSH_SCORE,
            data={DATA_LAST_BRUSH_TIME: 1_700_000_000, DATA_LAST_BRUSH_SCORE: None},
        )
        assert sensor.available is False

    def test_session_key_before_first_session_is_available(self):
        """No session yet → can't declare field unsupported."""
        sensor = _make_sensor(
            DATA_LAST_BRUSH_SCORE,
            data={DATA_LAST_BRUSH_TIME: None, DATA_LAST_BRUSH_SCORE: None},
        )
        assert sensor.available is True

    def test_session_key_with_value_is_available(self):
        sensor = _make_sensor(
            DATA_LAST_BRUSH_SCORE,
            data={DATA_LAST_BRUSH_TIME: 1_700_000_000, DATA_LAST_BRUSH_SCORE: 85},
        )
        assert sensor.available is True

    def test_non_session_key_always_available(self):
        sensor = _make_sensor("battery", data={"battery": None, DATA_LAST_BRUSH_TIME: 123})
        assert sensor.available is True


# ---------------------------------------------------------------------------
# OcleanBrushAreasSensor
# ---------------------------------------------------------------------------


class TestOcleanBrushAreasSensor:
    def test_native_value_none_when_no_data(self):
        sensor = _make_areas_sensor(data=None)
        assert sensor.native_value is None

    def test_native_value_none_when_areas_missing(self):
        sensor = _make_areas_sensor(data={DATA_LAST_BRUSH_AREAS: None})
        assert sensor.native_value is None

    def test_native_value_counts_nonzero_zones(self):
        areas = {
            "upper_left_out": 100,
            "upper_left_in": 0,
            "lower_left_out": 80,
            "lower_left_in": 0,
        }
        sensor = _make_areas_sensor(data={DATA_LAST_BRUSH_AREAS: areas})
        assert sensor.native_value == 2

    def test_native_value_all_zeros_returns_zero(self):
        areas = dict.fromkeys(TOOTH_AREA_NAMES, 0)
        sensor = _make_areas_sensor(data={DATA_LAST_BRUSH_AREAS: areas})
        assert sensor.native_value == 0

    def test_extra_state_attributes_returns_areas_dict(self):
        areas = {"upper_left_out": 120, "lower_right_in": 50}
        sensor = _make_areas_sensor(data={DATA_LAST_BRUSH_AREAS: areas})
        assert sensor.extra_state_attributes == areas

    def test_extra_state_attributes_none_when_no_data(self):
        sensor = _make_areas_sensor(data=None)
        assert sensor.extra_state_attributes is None

    def test_available_returns_false_when_session_exists_but_areas_none(self):
        sensor = _make_areas_sensor(data={DATA_LAST_BRUSH_TIME: 1_700_000_000, DATA_LAST_BRUSH_AREAS: None})
        assert sensor.available is False

    def test_available_true_when_areas_present(self):
        areas = {"zone": 100}
        sensor = _make_areas_sensor(data={DATA_LAST_BRUSH_TIME: 1_700_000_000, DATA_LAST_BRUSH_AREAS: areas})
        assert sensor.available is True

    def test_available_stale_data_with_areas_returns_true(self):
        areas = {"zone": 50}
        sensor = _make_areas_sensor(data={DATA_LAST_BRUSH_AREAS: areas}, last_update_success=False)
        assert sensor.available is True

    def test_available_stale_data_no_areas_returns_false(self):
        sensor = _make_areas_sensor(data={DATA_LAST_BRUSH_AREAS: None}, last_update_success=False)
        assert sensor.available is False


# ---------------------------------------------------------------------------
# OcleanSchemeSensor
# ---------------------------------------------------------------------------


class TestOcleanSchemeSensor:
    def test_native_value_none_when_no_data(self):
        sensor = _make_scheme_sensor(data=None)
        assert sensor.native_value is None

    def test_native_value_none_when_pnum_missing(self):
        sensor = _make_scheme_sensor(data={DATA_LAST_BRUSH_PNUM: None})
        assert sensor.native_value is None

    def test_native_value_known_pnum_returns_name(self):
        pnum, name = next(iter(SCHEME_NAMES.items()))
        sensor = _make_scheme_sensor(data={DATA_LAST_BRUSH_PNUM: pnum})
        assert sensor.native_value == name

    def test_native_value_unknown_pnum_returns_string(self):
        unknown_pnum = 9999
        assert unknown_pnum not in SCHEME_NAMES
        sensor = _make_scheme_sensor(data={DATA_LAST_BRUSH_PNUM: unknown_pnum})
        assert sensor.native_value == str(unknown_pnum)

    def test_available_false_when_session_exists_pnum_none(self):
        sensor = _make_scheme_sensor(data={DATA_LAST_BRUSH_TIME: 1_700_000_000, DATA_LAST_BRUSH_PNUM: None})
        assert sensor.available is False

    def test_available_true_when_pnum_present(self):
        sensor = _make_scheme_sensor(data={DATA_LAST_BRUSH_TIME: 1_700_000_000, DATA_LAST_BRUSH_PNUM: 21})
        assert sensor.available is True

    def test_available_stale_with_value_returns_true(self):
        sensor = _make_scheme_sensor(data={DATA_LAST_BRUSH_PNUM: 21}, last_update_success=False)
        assert sensor.available is True

    def test_available_stale_no_value_returns_false(self):
        sensor = _make_scheme_sensor(data={DATA_LAST_BRUSH_PNUM: None}, last_update_success=False)
        assert sensor.available is False


# ---------------------------------------------------------------------------
# OcleanToothAreaSensor
# ---------------------------------------------------------------------------


class TestOcleanToothAreaSensor:
    _zone = TOOTH_AREA_NAMES[0]  # "upper_left_out"

    def test_native_value_none_when_no_data(self):
        sensor = _make_tooth_sensor(self._zone, data=None)
        assert sensor.native_value is None

    def test_native_value_none_when_areas_missing(self):
        sensor = _make_tooth_sensor(self._zone, data={DATA_LAST_BRUSH_AREAS: None})
        assert sensor.native_value is None

    def test_native_value_returns_zone_pressure(self):
        areas = {self._zone: 120}
        sensor = _make_tooth_sensor(self._zone, data={DATA_LAST_BRUSH_AREAS: areas})
        assert sensor.native_value == 120

    def test_native_value_none_when_zone_absent(self):
        # Areas dict present but this zone key missing
        sensor = _make_tooth_sensor(self._zone, data={DATA_LAST_BRUSH_AREAS: {}})
        assert sensor.native_value is None

    def test_attr_name_derived_from_zone(self):
        sensor = _make_tooth_sensor("upper_left_out", data=None)
        assert "Upper Left Out" in sensor._attr_name

    def test_available_false_when_session_exists_no_areas(self):
        sensor = _make_tooth_sensor(
            self._zone,
            data={DATA_LAST_BRUSH_TIME: 1_700_000_000, DATA_LAST_BRUSH_AREAS: None},
        )
        assert sensor.available is False

    def test_available_true_when_areas_present(self):
        areas = {self._zone: 50}
        sensor = _make_tooth_sensor(
            self._zone,
            data={DATA_LAST_BRUSH_TIME: 1_700_000_000, DATA_LAST_BRUSH_AREAS: areas},
        )
        assert sensor.available is True

    def test_all_tooth_area_zones_instantiate(self):
        for zone in TOOTH_AREA_NAMES:
            sensor = _make_tooth_sensor(zone, data=None)
            assert sensor._zone_name == zone
