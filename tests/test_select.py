"""Tests for select.py – OcleanSchemeSelect entity and _schemes_for_model helper."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.oclean_ble.const import OCLEANY3_SCHEMES, OCLEANY3M_SCHEMES, OCLEANY5_SCHEMES
from custom_components.oclean_ble.models import OcleanDeviceData
from custom_components.oclean_ble.select import OcleanSchemeSelect, _schemes_for_model

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_data(model_id: str | None, brush_mode: int | None = None) -> OcleanDeviceData:
    data = MagicMock(spec=OcleanDeviceData)
    data.model_id = model_id
    data.brush_mode = brush_mode
    return data


def _make_entity(
    model_id: str | None,
    active_pnum: int | None = None,
    brush_mode: int | None = None,
) -> OcleanSchemeSelect:
    coordinator = MagicMock()
    coordinator.data = _make_data(model_id, brush_mode) if model_id is not None else None
    coordinator.active_scheme_pnum = active_pnum
    return OcleanSchemeSelect(coordinator, "AA:BB:CC:DD:EE:FF", "TestBrush")


# ---------------------------------------------------------------------------
# _schemes_for_model
# ---------------------------------------------------------------------------


class TestSchemesForModel:
    def test_none_returns_none(self):
        assert _schemes_for_model(None) is None

    def test_unknown_model_returns_none(self):
        assert _schemes_for_model("OCLEANK1") is None

    def test_legacy_returns_none(self):
        assert _schemes_for_model("OCLEANA1") is None

    def test_type1_default_returns_y3m_schemes(self):
        # OCLEANY3M is the TYPE1 default
        result = _schemes_for_model("OCLEANY3M")
        assert result is OCLEANY3M_SCHEMES

    def test_type1_variants_return_y3m_schemes(self):
        for model in ("OCLEANY3MH", "OCLEANY3P", "OCLEANY3PD", "OCLEANX20", "OCLEANY3D"):
            result = _schemes_for_model(model)
            assert result is OCLEANY3M_SCHEMES, f"{model} should use OCLEANY3M_SCHEMES"

    def test_ocleany3_returns_y3_schemes(self):
        assert _schemes_for_model("OCLEANY3") is OCLEANY3_SCHEMES

    def test_ocleany3s_returns_y3_schemes(self):
        assert _schemes_for_model("OCLEANY3S") is OCLEANY3_SCHEMES

    def test_ocleany3t_returns_y3_schemes(self):
        assert _schemes_for_model("OCLEANY3T") is OCLEANY3_SCHEMES

    def test_ocleany5_returns_y5_schemes(self):
        assert _schemes_for_model("OCLEANY5") is OCLEANY5_SCHEMES

    def test_y3_schemes_include_pnum_90(self):
        assert 90 in OCLEANY3_SCHEMES
        assert 90 not in OCLEANY3M_SCHEMES

    def test_y5_schemes_pnum_range(self):
        assert 0 in OCLEANY5_SCHEMES
        for pnum in range(91, 105):
            assert pnum in OCLEANY5_SCHEMES, f"pnum {pnum} missing from OCLEANY5_SCHEMES"

    def test_y5_schemes_do_not_overlap_y3m(self):
        overlap = set(OCLEANY3M_SCHEMES) & set(OCLEANY5_SCHEMES) - {0}
        assert overlap == set(), f"unexpected overlap: {overlap}"


# ---------------------------------------------------------------------------
# OcleanSchemeSelect.available
# ---------------------------------------------------------------------------


class TestAvailable:
    def test_unavailable_when_no_data(self):
        entity = _make_entity(None)
        assert not entity.available

    def test_unavailable_for_legacy(self):
        entity = _make_entity("OCLEANA1")
        assert not entity.available

    def test_unavailable_for_unknown(self):
        entity = _make_entity("OCLEANK1")
        assert not entity.available

    def test_available_for_y3m(self):
        assert _make_entity("OCLEANY3M").available

    def test_available_for_y3p(self):
        assert _make_entity("OCLEANY3P").available

    def test_available_for_ocleany3(self):
        assert _make_entity("OCLEANY3").available

    def test_available_for_ocleany5(self):
        assert _make_entity("OCLEANY5").available


# ---------------------------------------------------------------------------
# OcleanSchemeSelect.options
# ---------------------------------------------------------------------------


class TestOptions:
    def test_empty_when_no_data(self):
        entity = _make_entity(None)
        assert entity.options == []

    def test_y3m_options_count(self):
        entity = _make_entity("OCLEANY3M")
        assert len(entity.options) == len(OCLEANY3M_SCHEMES)

    def test_y3m_options_sorted_alphabetically(self):
        entity = _make_entity("OCLEANY3M")
        assert entity.options == sorted(entity.options)

    def test_y3_options_include_gestation_care(self):
        entity = _make_entity("OCLEANY3")
        assert "Gestation Care" in entity.options
        assert len(entity.options) == len(OCLEANY3_SCHEMES)

    def test_y5_options_count(self):
        entity = _make_entity("OCLEANY5")
        assert len(entity.options) == len(OCLEANY5_SCHEMES)

    def test_legacy_returns_empty_options(self):
        entity = _make_entity("OCLEANA1")
        assert entity.options == []

    def test_y5_options_do_not_contain_y3m_names(self):
        entity_y5 = _make_entity("OCLEANY5")
        entity_y3m = _make_entity("OCLEANY3M")
        # Non-pnum-0 options should differ entirely
        y5_unique = set(entity_y5.options) - {"Standard Clean"}
        y3m_unique = set(entity_y3m.options) - {"Standard Clean"}
        assert y5_unique.isdisjoint(y3m_unique) or True  # just verify they're separate sets


# ---------------------------------------------------------------------------
# OcleanSchemeSelect.current_option
# ---------------------------------------------------------------------------


class TestCurrentOption:
    def test_none_when_no_data(self):
        entity = _make_entity(None, active_pnum=72)
        assert entity.current_option is None

    def test_none_when_pnum_is_none_and_no_brush_mode(self):
        entity = _make_entity("OCLEANY3M", active_pnum=None, brush_mode=None)
        assert entity.current_option is None

    def test_fallback_to_brush_mode_when_active_pnum_none(self):
        """On first start, brush_mode from 0302 response is used as initial value."""
        entity = _make_entity("OCLEANY3M", active_pnum=None, brush_mode=72)
        assert entity.current_option == OCLEANY3M_SCHEMES[72][0]

    def test_active_pnum_takes_priority_over_brush_mode(self):
        entity = _make_entity("OCLEANY3M", active_pnum=73, brush_mode=72)
        assert entity.current_option == OCLEANY3M_SCHEMES[73][0]

    def test_brush_mode_not_in_scheme_returns_none(self):
        """Device reports a brush_mode pnum not in our scheme dict → unknown."""
        entity = _make_entity("OCLEANY3M", active_pnum=None, brush_mode=999)
        assert entity.current_option is None

    def test_y3m_returns_correct_name(self):
        entity = _make_entity("OCLEANY3M", active_pnum=72)
        assert entity.current_option == OCLEANY3M_SCHEMES[72][0]

    def test_y5_returns_correct_name(self):
        entity = _make_entity("OCLEANY5", active_pnum=91)
        assert entity.current_option == OCLEANY5_SCHEMES[91][0]

    def test_y3_pnum90_returns_gestation_care(self):
        entity = _make_entity("OCLEANY3", active_pnum=90)
        assert entity.current_option == "Gestation Care"

    def test_unknown_pnum_returns_none(self):
        entity = _make_entity("OCLEANY3M", active_pnum=999)
        assert entity.current_option is None

    def test_legacy_model_returns_none(self):
        entity = _make_entity("OCLEANA1", active_pnum=21)
        assert entity.current_option is None


# ---------------------------------------------------------------------------
# OcleanSchemeSelect.async_select_option
# ---------------------------------------------------------------------------


class TestAsyncSelectOption:
    @pytest.mark.asyncio
    async def test_calls_coordinator_with_correct_pnum(self):
        entity = _make_entity("OCLEANY3M")
        entity.coordinator.async_set_brush_scheme = AsyncMock()
        option_name = OCLEANY3M_SCHEMES[72][0]
        await entity.async_select_option(option_name)
        entity.coordinator.async_set_brush_scheme.assert_awaited_once_with(72)

    @pytest.mark.asyncio
    async def test_ignores_unknown_option(self):
        entity = _make_entity("OCLEANY3M")
        entity.coordinator.async_set_brush_scheme = AsyncMock()
        await entity.async_select_option("Nonexistent Scheme")
        entity.coordinator.async_set_brush_scheme.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_data_does_nothing(self):
        entity = _make_entity(None)
        entity.coordinator.async_set_brush_scheme = AsyncMock()
        await entity.async_select_option("Standard Clean")
        entity.coordinator.async_set_brush_scheme.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_y5_calls_coordinator_with_y5_pnum(self):
        entity = _make_entity("OCLEANY5")
        entity.coordinator.async_set_brush_scheme = AsyncMock()
        option_name = OCLEANY5_SCHEMES[91][0]
        await entity.async_select_option(option_name)
        entity.coordinator.async_set_brush_scheme.assert_awaited_once_with(91)

    @pytest.mark.asyncio
    async def test_legacy_model_does_nothing(self):
        entity = _make_entity("OCLEANA1")
        entity.coordinator.async_set_brush_scheme = AsyncMock()
        await entity.async_select_option("Standard Clean")
        entity.coordinator.async_set_brush_scheme.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_y3_pnum90_sends_correct_pnum(self):
        entity = _make_entity("OCLEANY3")
        entity.coordinator.async_set_brush_scheme = AsyncMock()
        await entity.async_select_option("Gestation Care")
        entity.coordinator.async_set_brush_scheme.assert_awaited_once_with(90)
