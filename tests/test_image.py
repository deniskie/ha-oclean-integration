"""Tests for the Oclean brush coverage image entity."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from custom_components.oclean_ble.const import (
    DATA_LAST_BRUSH_AREAS,
    DATA_LAST_BRUSH_TIME,
    TOOTH_AREA_NAMES,
)
from custom_components.oclean_ble.image import (
    OcleanBrushCoverageImage,
    _generate_svg,
    _pressure_to_color,
)

# ---------------------------------------------------------------------------
# _pressure_to_color
# ---------------------------------------------------------------------------


class TestPressureToColor:
    """Map raw 0-255 pressure to CSS colour string."""

    def test_zero_returns_none(self) -> None:
        assert _pressure_to_color(0) is None

    def test_negative_returns_none(self) -> None:
        assert _pressure_to_color(-1) is None

    def test_low_pressure_green(self) -> None:
        assert _pressure_to_color(40) == "#4CAF50"

    def test_medium_pressure_orange(self) -> None:
        assert _pressure_to_color(120) == "#FF9800"

    def test_high_pressure_red(self) -> None:
        assert _pressure_to_color(200) == "#F44336"

    def test_max_pressure_red(self) -> None:
        assert _pressure_to_color(255) == "#F44336"

    def test_boundary_80_is_green(self) -> None:
        assert _pressure_to_color(80) == "#4CAF50"

    def test_boundary_81_is_orange(self) -> None:
        assert _pressure_to_color(81) == "#FF9800"

    def test_boundary_160_is_orange(self) -> None:
        assert _pressure_to_color(160) == "#FF9800"

    def test_boundary_161_is_red(self) -> None:
        assert _pressure_to_color(161) == "#F44336"


# ---------------------------------------------------------------------------
# _generate_svg
# ---------------------------------------------------------------------------


class TestGenerateSvg:
    """Test SVG generation."""

    @staticmethod
    def _mixed_areas() -> dict[str, int]:
        return {
            "upper_left_out": 120,
            "upper_left_in": 0,
            "lower_left_out": 30,
            "lower_left_in": 60,
            "upper_right_out": 45,
            "upper_right_in": 200,
            "lower_right_out": 0,
            "lower_right_in": 95,
        }

    def test_returns_valid_svg(self) -> None:
        svg = _generate_svg(dict.fromkeys(TOOTH_AREA_NAMES, 50))
        assert svg.startswith("<svg")
        assert svg.strip().endswith("</svg>")

    def test_contains_namespace(self) -> None:
        svg = _generate_svg(dict.fromkeys(TOOTH_AREA_NAMES, 0))
        assert 'xmlns="http://www.w3.org/2000/svg"' in svg

    def test_zero_pressure_no_colour_overlay(self) -> None:
        svg = _generate_svg(dict.fromkeys(TOOTH_AREA_NAMES, 0))
        assert "#4CAF50" not in svg
        assert "#FF9800" not in svg
        assert "#F44336" not in svg

    def test_mixed_pressures_contain_colours(self) -> None:
        svg = _generate_svg(self._mixed_areas())
        assert "#4CAF50" in svg  # green (low pressure zones)
        assert "#FF9800" in svg  # orange (medium pressure zones)
        assert "#F44336" in svg  # red (high pressure zone)

    def test_all_max_pressure_only_red(self) -> None:
        svg = _generate_svg(dict.fromkeys(TOOTH_AREA_NAMES, 255))
        assert "#F44336" in svg
        assert "#4CAF50" not in svg

    def test_empty_dict(self) -> None:
        svg = _generate_svg({})
        assert svg.startswith("<svg")

    def test_contains_labels(self) -> None:
        svg = _generate_svg(dict.fromkeys(TOOTH_AREA_NAMES, 0))
        assert ">L</text>" in svg
        assert ">R</text>" in svg

    def test_size_is_small(self) -> None:
        svg = _generate_svg(self._mixed_areas())
        assert len(svg) < 15000  # should be well under 15 KB


# ---------------------------------------------------------------------------
# OcleanBrushCoverageImage entity
# ---------------------------------------------------------------------------


class TestOcleanBrushCoverageImage:
    """Test the image entity behaviour."""

    @staticmethod
    def _make_entity(coordinator_data: dict | None = None) -> OcleanBrushCoverageImage:
        coordinator = MagicMock()
        coordinator.data = coordinator_data
        coordinator.last_update_success = True
        coordinator.hass = MagicMock()
        return OcleanBrushCoverageImage(coordinator, "AA:BB:CC:DD:EE:FF", "Test Oclean")

    def test_unique_id(self) -> None:
        entity = self._make_entity()
        assert entity._attr_unique_id == "AA:BB:CC:DD:EE:FF_brush_coverage"

    def test_translation_key(self) -> None:
        entity = self._make_entity()
        assert entity._attr_translation_key == "brush_coverage"

    def test_content_type_is_svg(self) -> None:
        entity = self._make_entity()
        assert entity._attr_content_type == "image/svg+xml"

    def test_available_no_data(self) -> None:
        entity = self._make_entity(coordinator_data=None)
        assert entity.available is True

    def test_available_with_areas(self) -> None:
        areas = dict.fromkeys(TOOTH_AREA_NAMES, 50)
        entity = self._make_entity({DATA_LAST_BRUSH_AREAS: areas, DATA_LAST_BRUSH_TIME: 1234567890})
        assert entity.available is True

    def test_unavailable_session_but_no_areas(self) -> None:
        entity = self._make_entity({DATA_LAST_BRUSH_TIME: 1234567890})
        assert entity.available is False

    @pytest.mark.asyncio
    async def test_async_image_returns_none_without_data(self) -> None:
        entity = self._make_entity(coordinator_data=None)
        result = await entity.async_image()
        assert result is None

    @pytest.mark.asyncio
    async def test_async_image_returns_svg_bytes(self) -> None:
        areas = dict.fromkeys(TOOTH_AREA_NAMES, 100)
        entity = self._make_entity({DATA_LAST_BRUSH_AREAS: areas})
        result = await entity.async_image()
        assert result is not None
        assert result.startswith(b"<svg")

    @pytest.mark.asyncio
    async def test_caching(self) -> None:
        areas = dict.fromkeys(TOOTH_AREA_NAMES, 50)
        entity = self._make_entity({DATA_LAST_BRUSH_AREAS: areas})

        result1 = await entity.async_image()
        result2 = await entity.async_image()
        assert result1 is result2
