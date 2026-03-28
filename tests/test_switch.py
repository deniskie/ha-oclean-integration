"""Tests for switch.py – all four Oclean switch entities."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.components.switch import SwitchEntityDescription

from custom_components.oclean_ble.switch import SWITCH_DESCRIPTIONS, OcleanSwitch

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_coordinator(**props):
    """Create a mock coordinator with configurable property values."""
    coord = MagicMock()
    coord.data = MagicMock()
    coord.last_update_success = True
    # Default all properties to None
    coord.area_remind = props.get("area_remind")
    coord.over_pressure = props.get("over_pressure")
    coord.remind_switch = props.get("remind_switch")
    coord.running_switch = props.get("running_switch")
    # Async setters
    coord.async_set_area_remind = AsyncMock()
    coord.async_set_over_pressure = AsyncMock()
    coord.async_set_remind_switch = AsyncMock()
    coord.async_set_running_switch = AsyncMock()
    return coord


def _make_switch(key, **coord_props):
    coord = _make_coordinator(**coord_props)
    desc = next(d for d in SWITCH_DESCRIPTIONS if d.key == key)
    return OcleanSwitch(coord, desc, "AA:BB:CC:DD:EE:FF", "Oclean"), coord


# ---------------------------------------------------------------------------
# SWITCH_DESCRIPTIONS
# ---------------------------------------------------------------------------


class TestSwitchDescriptions:
    def test_four_descriptions_defined(self):
        assert len(SWITCH_DESCRIPTIONS) == 4

    def test_all_keys(self):
        keys = {d.key for d in SWITCH_DESCRIPTIONS}
        assert keys == {"area_remind", "over_pressure", "remind_switch", "running_switch"}

    def test_remind_switch_icon(self):
        desc = next(d for d in SWITCH_DESCRIPTIONS if d.key == "remind_switch")
        assert desc.icon == "mdi:bell-ring-outline"

    def test_running_switch_icon(self):
        desc = next(d for d in SWITCH_DESCRIPTIONS if d.key == "running_switch")
        assert desc.icon == "mdi:timer-off-outline"


# ---------------------------------------------------------------------------
# OcleanSwitch.is_on
# ---------------------------------------------------------------------------


class TestOcleanSwitchIsOn:
    @pytest.mark.parametrize("key", ["area_remind", "over_pressure", "remind_switch", "running_switch"])
    def test_is_on_none_by_default(self, key):
        switch, _ = _make_switch(key)
        assert switch.is_on is None

    @pytest.mark.parametrize("key", ["area_remind", "over_pressure", "remind_switch", "running_switch"])
    def test_is_on_true(self, key):
        switch, _ = _make_switch(key, **{key: True})
        assert switch.is_on is True

    @pytest.mark.parametrize("key", ["area_remind", "over_pressure", "remind_switch", "running_switch"])
    def test_is_on_false(self, key):
        switch, _ = _make_switch(key, **{key: False})
        assert switch.is_on is False


# ---------------------------------------------------------------------------
# OcleanSwitch.async_turn_on / async_turn_off
# ---------------------------------------------------------------------------


class TestOcleanSwitchTurnOnOff:
    @pytest.mark.parametrize(
        ("key", "setter"),
        [
            ("area_remind", "async_set_area_remind"),
            ("over_pressure", "async_set_over_pressure"),
            ("remind_switch", "async_set_remind_switch"),
            ("running_switch", "async_set_running_switch"),
        ],
    )
    @pytest.mark.asyncio
    async def test_turn_on_calls_setter(self, key, setter):
        switch, coord = _make_switch(key)
        await switch.async_turn_on()
        getattr(coord, setter).assert_awaited_once_with(True)

    @pytest.mark.parametrize(
        ("key", "setter"),
        [
            ("area_remind", "async_set_area_remind"),
            ("over_pressure", "async_set_over_pressure"),
            ("remind_switch", "async_set_remind_switch"),
            ("running_switch", "async_set_running_switch"),
        ],
    )
    @pytest.mark.asyncio
    async def test_turn_off_calls_setter(self, key, setter):
        switch, coord = _make_switch(key)
        await switch.async_turn_off()
        getattr(coord, setter).assert_awaited_once_with(False)


# ---------------------------------------------------------------------------
# OcleanSwitch – assumed state
# ---------------------------------------------------------------------------


class TestOcleanSwitchAssumedState:
    def test_assumed_state_is_true(self):
        switch, _ = _make_switch("remind_switch")
        assert switch._attr_assumed_state is True


# ---------------------------------------------------------------------------
# OcleanSwitch – unknown key (defensive)
# ---------------------------------------------------------------------------


class TestOcleanSwitchUnknownKey:
    def test_is_on_returns_none_for_unknown_key(self):
        coord = _make_coordinator()
        desc = SwitchEntityDescription(key="nonexistent")
        switch = OcleanSwitch(coord, desc, "AA:BB:CC:DD:EE:FF", "Oclean")
        assert switch.is_on is None

    @pytest.mark.asyncio
    async def test_turn_on_noop_for_unknown_key(self):
        coord = _make_coordinator()
        desc = SwitchEntityDescription(key="nonexistent")
        switch = OcleanSwitch(coord, desc, "AA:BB:CC:DD:EE:FF", "Oclean")
        # Should not raise
        await switch.async_turn_on()

    @pytest.mark.asyncio
    async def test_turn_off_noop_for_unknown_key(self):
        coord = _make_coordinator()
        desc = SwitchEntityDescription(key="nonexistent")
        switch = OcleanSwitch(coord, desc, "AA:BB:CC:DD:EE:FF", "Oclean")
        await switch.async_turn_off()
