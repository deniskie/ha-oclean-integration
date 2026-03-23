"""Tests for switch.py – OcleanSwitch entity."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

# conftest.py stubs HA + bleak before these imports
from custom_components.oclean_ble.switch import SWITCH_DESCRIPTIONS, OcleanSwitch, async_setup_entry

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_coordinator(area_remind=None, over_pressure=None):
    coord = MagicMock()
    coord.area_remind = area_remind
    coord.over_pressure = over_pressure
    coord.async_set_area_remind = AsyncMock()
    coord.async_set_over_pressure = AsyncMock()
    return coord


def _make_switch(key: str, area_remind=None, over_pressure=None):
    coord = _make_coordinator(area_remind=area_remind, over_pressure=over_pressure)
    desc = next(d for d in SWITCH_DESCRIPTIONS if d.key == key)
    return OcleanSwitch(coord, desc, "AA:BB:CC:DD:EE:FF", "Oclean X")


# ---------------------------------------------------------------------------
# SWITCH_DESCRIPTIONS
# ---------------------------------------------------------------------------


class TestSwitchDescriptions:
    def test_area_remind_present(self):
        keys = [d.key for d in SWITCH_DESCRIPTIONS]
        assert "area_remind" in keys

    def test_over_pressure_present(self):
        keys = [d.key for d in SWITCH_DESCRIPTIONS]
        assert "over_pressure" in keys


# ---------------------------------------------------------------------------
# is_on – area_remind
# ---------------------------------------------------------------------------


class TestIsOnAreaRemind:
    def test_returns_none_when_not_set(self):
        sw = _make_switch("area_remind", area_remind=None)
        assert sw.is_on is None

    def test_returns_true(self):
        sw = _make_switch("area_remind", area_remind=True)
        assert sw.is_on is True

    def test_returns_false(self):
        sw = _make_switch("area_remind", area_remind=False)
        assert sw.is_on is False


# ---------------------------------------------------------------------------
# is_on – over_pressure
# ---------------------------------------------------------------------------


class TestIsOnOverPressure:
    def test_returns_none_when_not_set(self):
        sw = _make_switch("over_pressure", over_pressure=None)
        assert sw.is_on is None

    def test_returns_true(self):
        sw = _make_switch("over_pressure", over_pressure=True)
        assert sw.is_on is True

    def test_returns_false(self):
        sw = _make_switch("over_pressure", over_pressure=False)
        assert sw.is_on is False


# ---------------------------------------------------------------------------
# async_turn_on / async_turn_off – area_remind
# ---------------------------------------------------------------------------


class TestTurnAreaRemind:
    @pytest.mark.asyncio
    async def test_turn_on_calls_set_area_remind_true(self):
        sw = _make_switch("area_remind")
        await sw.async_turn_on()
        sw.coordinator.async_set_area_remind.assert_awaited_once_with(True)

    @pytest.mark.asyncio
    async def test_turn_off_calls_set_area_remind_false(self):
        sw = _make_switch("area_remind")
        await sw.async_turn_off()
        sw.coordinator.async_set_area_remind.assert_awaited_once_with(False)


# ---------------------------------------------------------------------------
# async_turn_on / async_turn_off – over_pressure
# ---------------------------------------------------------------------------


class TestTurnOverPressure:
    @pytest.mark.asyncio
    async def test_turn_on_calls_set_over_pressure_true(self):
        sw = _make_switch("over_pressure")
        await sw.async_turn_on()
        sw.coordinator.async_set_over_pressure.assert_awaited_once_with(True)

    @pytest.mark.asyncio
    async def test_turn_off_calls_set_over_pressure_false(self):
        sw = _make_switch("over_pressure")
        await sw.async_turn_off()
        sw.coordinator.async_set_over_pressure.assert_awaited_once_with(False)


# ---------------------------------------------------------------------------
# async_setup_entry
# ---------------------------------------------------------------------------


class TestAsyncSetupEntry:
    @pytest.mark.asyncio
    async def test_registers_both_switches(self):
        """async_setup_entry must create one entity per SWITCH_DESCRIPTIONS entry."""
        from custom_components.oclean_ble.const import CONF_DEVICE_NAME, CONF_MAC_ADDRESS, DOMAIN

        hass = MagicMock()
        hass.data = {}
        coord = _make_coordinator()
        hass.data[DOMAIN] = {"entry_id_123": coord}

        entry = MagicMock()
        entry.entry_id = "entry_id_123"
        entry.data = {CONF_MAC_ADDRESS: "AA:BB:CC:DD:EE:FF", CONF_DEVICE_NAME: "Oclean"}

        added = []
        async_add = MagicMock(side_effect=added.extend)

        await async_setup_entry(hass, entry, async_add)

        assert len(added) == len(SWITCH_DESCRIPTIONS)
        keys = {e.entity_description.key for e in added}
        assert "area_remind" in keys
        assert "over_pressure" in keys
