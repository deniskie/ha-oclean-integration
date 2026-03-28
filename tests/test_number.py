"""Tests for number.py -- Oclean number entities."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.components.number import NumberEntityDescription

from custom_components.oclean_ble.number import NUMBER_DESCRIPTIONS, OcleanNumber

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_coordinator(**props):
    """Create a mock coordinator with configurable property values."""
    coord = MagicMock()
    coord.data = MagicMock()
    coord.last_update_success = True
    coord.brush_head_max_days = props.get("brush_head_max_days")
    coord.async_set_brush_head_max_days = AsyncMock()
    return coord


def _make_number(key, **coord_props):
    coord = _make_coordinator(**coord_props)
    desc = next(d for d in NUMBER_DESCRIPTIONS if d.key == key)
    return OcleanNumber(coord, desc, "AA:BB:CC:DD:EE:FF", "Oclean"), coord


# ---------------------------------------------------------------------------
# NUMBER_DESCRIPTIONS
# ---------------------------------------------------------------------------


class TestNumberDescriptions:
    def test_one_description_defined(self):
        assert len(NUMBER_DESCRIPTIONS) == 1

    def test_key_is_brush_head_max_days(self):
        assert NUMBER_DESCRIPTIONS[0].key == "brush_head_max_days"

    def test_min_value(self):
        desc = NUMBER_DESCRIPTIONS[0]
        assert desc.native_min_value == 30

    def test_max_value(self):
        desc = NUMBER_DESCRIPTIONS[0]
        assert desc.native_max_value == 365

    def test_step(self):
        desc = NUMBER_DESCRIPTIONS[0]
        assert desc.native_step == 1


# ---------------------------------------------------------------------------
# OcleanNumber -- assumed state
# ---------------------------------------------------------------------------


class TestOcleanNumberAssumedState:
    def test_assumed_state_is_true(self):
        number, _ = _make_number("brush_head_max_days")
        assert number._attr_assumed_state is True


# ---------------------------------------------------------------------------
# OcleanNumber.native_value
# ---------------------------------------------------------------------------


class TestOcleanNumberNativeValue:
    def test_returns_int_when_set(self):
        number, _ = _make_number("brush_head_max_days", brush_head_max_days=90)
        assert number.native_value == 90
        assert isinstance(number.native_value, int)

    def test_returns_none_when_not_set(self):
        number, _ = _make_number("brush_head_max_days")
        assert number.native_value is None


# ---------------------------------------------------------------------------
# OcleanNumber.async_set_native_value
# ---------------------------------------------------------------------------


class TestOcleanNumberSetNativeValue:
    @pytest.mark.asyncio
    async def test_calls_coordinator_setter(self):
        number, coord = _make_number("brush_head_max_days")
        await number.async_set_native_value(120.0)
        coord.async_set_brush_head_max_days.assert_awaited_once_with(120)


# ---------------------------------------------------------------------------
# OcleanNumber -- unknown key (defensive)
# ---------------------------------------------------------------------------


class TestOcleanNumberUnknownKey:
    def test_native_value_returns_none_for_unknown_key(self):
        coord = _make_coordinator()
        desc = NumberEntityDescription(key="nonexistent")
        number = OcleanNumber(coord, desc, "AA:BB:CC:DD:EE:FF", "Oclean")
        assert number.native_value is None
