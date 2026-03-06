"""Tests for entity.py – OcleanEntity base class."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

# conftest.py stubs HA + bleak before this import
from custom_components.oclean_ble.entity import OcleanEntity


def _make_coordinator(data=None, last_update_success=True):
    coord = MagicMock()
    coord.data = data
    coord.last_update_success = last_update_success
    return coord


def _make_entity(coordinator=None, *, data=None, last_update_success=True):
    if coordinator is None:
        coordinator = _make_coordinator(data=data, last_update_success=last_update_success)
    return OcleanEntity(coordinator, mac="AA:BB:CC:DD:EE:FF", device_name="Oclean", unique_id_suffix="test")


# ---------------------------------------------------------------------------
# available property (line 45)
# ---------------------------------------------------------------------------


class TestAvailableProperty:
    def test_available_when_data_present(self):
        entity = _make_entity(data={"battery": 80})
        assert entity.available is True

    def test_unavailable_when_data_is_none(self):
        entity = _make_entity(data=None)
        assert entity.available is False


# ---------------------------------------------------------------------------
# _session_field_available – last_update_success=False branch (lines 55-57)
# ---------------------------------------------------------------------------


class TestSessionFieldAvailable:
    def test_last_update_failed_value_present_returns_true(self):
        """When last poll failed but value exists, field is available (stale data shown)."""
        entity = _make_entity(data={"battery": 80}, last_update_success=False)
        assert entity._session_field_available(42) is True

    def test_last_update_failed_value_none_returns_false(self):
        """When last poll failed and value is None, field is unavailable."""
        entity = _make_entity(data={"battery": 80}, last_update_success=False)
        assert entity._session_field_available(None) is False

    def test_last_update_success_value_present_returns_true(self):
        coord = _make_coordinator(data=MagicMock(), last_update_success=True)
        coord.data.get = MagicMock(return_value=None)  # last_brush_time is None
        entity = _make_entity(coordinator=coord)
        assert entity._session_field_available(50) is True

    def test_last_update_success_brush_time_set_value_none_returns_false(self):
        """brush_time set but value=None → structural unavailability → False."""
        from custom_components.oclean_ble.const import DATA_LAST_BRUSH_TIME

        coord = _make_coordinator(last_update_success=True)
        data_mock = MagicMock()
        data_mock.get = lambda k, *_: 1_700_000_000 if k == DATA_LAST_BRUSH_TIME else None
        coord.data = data_mock
        entity = _make_entity(coordinator=coord)
        assert entity._session_field_available(None) is False

    def test_last_update_success_no_brush_time_value_none_returns_true(self):
        """No brush_time yet and value=None → not structurally absent → True."""
        coord = _make_coordinator(last_update_success=True)
        data_mock = MagicMock()
        data_mock.get = lambda *_: None
        coord.data = data_mock
        entity = _make_entity(coordinator=coord)
        assert entity._session_field_available(None) is True
