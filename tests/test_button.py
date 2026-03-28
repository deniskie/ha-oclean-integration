"""Tests for button.py – all three Oclean button entities."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.components.button import ButtonEntityDescription

from custom_components.oclean_ble.button import BUTTON_DESCRIPTIONS, OcleanButton

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_coordinator():
    """Create a mock coordinator with button-relevant async methods."""
    coord = MagicMock()
    coord.data = MagicMock()
    coord.last_update_success = True
    coord.async_reset_brush_head = AsyncMock()
    coord.async_sync_time = AsyncMock()
    coord.async_request_refresh = AsyncMock()
    return coord


def _make_button(key):
    coord = _make_coordinator()
    desc = next(d for d in BUTTON_DESCRIPTIONS if d.key == key)
    return OcleanButton(coord, desc, "AA:BB:CC:DD:EE:FF", "Oclean"), coord


# ---------------------------------------------------------------------------
# BUTTON_DESCRIPTIONS
# ---------------------------------------------------------------------------


class TestButtonDescriptions:
    def test_three_descriptions_defined(self):
        assert len(BUTTON_DESCRIPTIONS) == 3

    def test_all_keys(self):
        keys = {d.key for d in BUTTON_DESCRIPTIONS}
        assert keys == {"reset_brush_head", "sync_time", "poll_now"}


# ---------------------------------------------------------------------------
# OcleanButton.__init__
# ---------------------------------------------------------------------------


class TestOcleanButtonInit:
    @pytest.mark.parametrize("key", ["reset_brush_head", "sync_time", "poll_now"])
    def test_entity_description_set(self, key):
        button, _ = _make_button(key)
        assert button.entity_description.key == key


# ---------------------------------------------------------------------------
# OcleanButton.async_press
# ---------------------------------------------------------------------------


class TestOcleanButtonAsyncPress:
    @pytest.mark.asyncio
    async def test_reset_brush_head(self):
        button, coord = _make_button("reset_brush_head")
        await button.async_press()
        coord.async_reset_brush_head.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_sync_time(self):
        button, coord = _make_button("sync_time")
        await button.async_press()
        coord.async_sync_time.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_poll_now(self):
        button, coord = _make_button("poll_now")
        await button.async_press()
        coord.async_request_refresh.assert_awaited_once()


# ---------------------------------------------------------------------------
# OcleanButton – unknown key (defensive)
# ---------------------------------------------------------------------------


class TestOcleanButtonUnknownKey:
    @pytest.mark.asyncio
    async def test_async_press_noop_for_unknown_key(self):
        coord = _make_coordinator()
        desc = ButtonEntityDescription(key="nonexistent")
        button = OcleanButton(coord, desc, "AA:BB:CC:DD:EE:FF", "Oclean")
        await button.async_press()
        coord.async_reset_brush_head.assert_not_awaited()
        coord.async_sync_time.assert_not_awaited()
        coord.async_request_refresh.assert_not_awaited()
