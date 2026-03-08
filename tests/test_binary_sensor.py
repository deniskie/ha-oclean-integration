"""Tests for binary_sensor.py – OcleanBinarySensor entity."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

# conftest.py stubs HA + bleak before these imports
from homeassistant.components.binary_sensor import BinarySensorDeviceClass

from custom_components.oclean_ble.binary_sensor import OcleanBinarySensor, async_setup_entry
from custom_components.oclean_ble.const import DATA_IS_BRUSHING

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_coordinator(data=None, last_update_success=True):
    coord = MagicMock()
    coord.data = data
    coord.last_update_success = last_update_success
    return coord


def _make_sensor(data=None, last_update_success=True):
    coord = _make_coordinator(data=data, last_update_success=last_update_success)
    return OcleanBinarySensor(coord, "AA:BB:CC:DD:EE:FF", "Oclean X")


# ---------------------------------------------------------------------------
# is_on property
# ---------------------------------------------------------------------------


class TestIsOn:
    def test_returns_none_when_data_is_none(self):
        sensor = _make_sensor(data=None)
        assert sensor.is_on is None

    def test_returns_false_when_not_brushing(self):
        sensor = _make_sensor(data={DATA_IS_BRUSHING: False})
        assert sensor.is_on is False

    def test_returns_true_when_brushing(self):
        sensor = _make_sensor(data={DATA_IS_BRUSHING: True})
        assert sensor.is_on is True

    def test_returns_none_when_key_missing(self):
        """get() returns None (default) when is_brushing is absent."""
        sensor = _make_sensor(data={})
        assert sensor.is_on is None

    def test_device_class_is_running(self):
        sensor = _make_sensor()
        assert sensor._attr_device_class == BinarySensorDeviceClass.RUNNING

    def test_translation_key(self):
        sensor = _make_sensor()
        assert sensor._attr_translation_key == "is_brushing"

    def test_name(self):
        sensor = _make_sensor()
        assert sensor._attr_name == "Brushing"


# ---------------------------------------------------------------------------
# async_setup_entry
# ---------------------------------------------------------------------------


class TestAsyncSetupEntry:
    @pytest.mark.asyncio
    async def test_adds_one_entity(self):
        hass = MagicMock()
        coordinator = _make_coordinator()
        hass.data = {"oclean_ble": {"entry-1": coordinator}}

        entry = MagicMock()
        entry.entry_id = "entry-1"
        entry.data = {"mac_address": "AA:BB:CC:DD:EE:FF", "device_name": "Oclean X"}

        added = []
        async_add_entities = MagicMock(side_effect=added.extend)

        await async_setup_entry(hass, entry, async_add_entities)

        assert len(added) == 1
        assert isinstance(added[0], OcleanBinarySensor)
