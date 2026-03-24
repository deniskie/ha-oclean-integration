"""Tests for the birthday / child-mode feature (CMD 0211).

Covers:
- OcleanBirthdayDate entity (date.py)
- OcleanBirthdaySexSelect entity (select.py)
- OcleanCoordinator.async_set_birthday / persistence helpers
"""

from __future__ import annotations

import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.oclean_ble.date import OcleanBirthdayDate
from custom_components.oclean_ble.select import _SEX_BY_INT, _SEX_OPTIONS, OcleanBirthdaySexSelect

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_coordinator(birthday_date=None, birthday_sex=1):
    c = MagicMock()
    c.birthday_date = birthday_date
    c.birthday_sex = birthday_sex
    c.async_set_birthday = AsyncMock()
    return c


def _make_date_entity(birthday_date=None):
    coordinator = _make_coordinator(birthday_date=birthday_date)
    return OcleanBirthdayDate(coordinator, "AA:BB:CC:DD:EE:FF", "TestBrush")


def _make_sex_entity(birthday_sex=1):
    coordinator = _make_coordinator(birthday_sex=birthday_sex)
    return OcleanBirthdaySexSelect(coordinator, "AA:BB:CC:DD:EE:FF", "TestBrush")


# ---------------------------------------------------------------------------
# OcleanBirthdayDate – native_value
# ---------------------------------------------------------------------------


class TestBirthdayDateNativeValue:
    def test_none_when_not_set(self):
        entity = _make_date_entity(birthday_date=None)
        assert entity.native_value is None

    def test_returns_date(self):
        d = datetime.date(2015, 6, 15)
        entity = _make_date_entity(birthday_date=d)
        assert entity.native_value == d

    def test_is_assumed_state(self):
        entity = _make_date_entity()
        assert entity._attr_assumed_state is True


# ---------------------------------------------------------------------------
# OcleanBirthdayDate – async_set_value
# ---------------------------------------------------------------------------


class TestBirthdayDateSetValue:
    @pytest.mark.asyncio
    async def test_calls_coordinator_with_birthday(self):
        entity = _make_date_entity()
        d = datetime.date(2012, 3, 22)
        await entity.async_set_value(d)
        entity.coordinator.async_set_birthday.assert_awaited_once_with(birthday=d)

    @pytest.mark.asyncio
    async def test_calls_coordinator_each_time(self):
        entity = _make_date_entity()
        d1 = datetime.date(2010, 1, 1)
        d2 = datetime.date(2012, 6, 30)
        await entity.async_set_value(d1)
        await entity.async_set_value(d2)
        assert entity.coordinator.async_set_birthday.await_count == 2


# ---------------------------------------------------------------------------
# OcleanBirthdaySexSelect – options
# ---------------------------------------------------------------------------


class TestBirthdaySexOptions:
    def test_options_contain_all_keys(self):
        entity = _make_sex_entity()
        assert set(entity.options) == {"unknown", "male", "female"}

    def test_options_class_attr(self):
        assert set(OcleanBirthdaySexSelect._attr_options) == {"unknown", "male", "female"}


# ---------------------------------------------------------------------------
# OcleanBirthdaySexSelect – current_option
# ---------------------------------------------------------------------------


class TestBirthdaySexCurrentOption:
    def test_default_male(self):
        entity = _make_sex_entity(birthday_sex=1)
        assert entity.current_option == "male"

    def test_female(self):
        entity = _make_sex_entity(birthday_sex=2)
        assert entity.current_option == "female"

    def test_unknown(self):
        entity = _make_sex_entity(birthday_sex=0)
        assert entity.current_option == "unknown"

    def test_invalid_returns_none(self):
        entity = _make_sex_entity(birthday_sex=99)
        assert entity.current_option is None


# ---------------------------------------------------------------------------
# OcleanBirthdaySexSelect – async_select_option
# ---------------------------------------------------------------------------


class TestBirthdaySexSelectOption:
    @pytest.mark.asyncio
    async def test_select_male_sends_sex_1(self):
        entity = _make_sex_entity()
        await entity.async_select_option("male")
        entity.coordinator.async_set_birthday.assert_awaited_once_with(sex=1)

    @pytest.mark.asyncio
    async def test_select_female_sends_sex_2(self):
        entity = _make_sex_entity()
        await entity.async_select_option("female")
        entity.coordinator.async_set_birthday.assert_awaited_once_with(sex=2)

    @pytest.mark.asyncio
    async def test_select_unknown_sends_sex_0(self):
        entity = _make_sex_entity()
        await entity.async_select_option("unknown")
        entity.coordinator.async_set_birthday.assert_awaited_once_with(sex=0)

    @pytest.mark.asyncio
    async def test_unknown_option_does_nothing(self):
        entity = _make_sex_entity()
        await entity.async_select_option("alien")
        entity.coordinator.async_set_birthday.assert_not_awaited()


# ---------------------------------------------------------------------------
# Internal mappings consistency
# ---------------------------------------------------------------------------


class TestSexMappings:
    def test_sex_options_roundtrip(self):
        for name, val in _SEX_OPTIONS.items():
            assert _SEX_BY_INT[val] == name

    def test_sex_by_int_roundtrip(self):
        for val, name in _SEX_BY_INT.items():
            assert _SEX_OPTIONS[name] == val


# ---------------------------------------------------------------------------
# Coordinator async_set_birthday – unit-level tests (direct logic)
# ---------------------------------------------------------------------------


class TestCoordinatorSetBirthday:
    """Test async_set_birthday payload construction.

    We instantiate the real coordinator-level helper by patching out the BLE
    layer so no actual hardware is required.
    """

    def _make_real_coordinator(self):
        """Return a minimal real OcleanCoordinator with all BLE I/O mocked."""
        from custom_components.oclean_ble.coordinator import OcleanCoordinator

        hass = MagicMock()
        hass.data = {}
        coordinator = OcleanCoordinator.__new__(OcleanCoordinator)
        coordinator.hass = hass
        coordinator._device_name = "TestBrush"
        coordinator._mac = "AA:BB:CC:DD:EE:FF"
        coordinator._birthday_date = None
        coordinator._birthday_sex = 1
        coordinator._store = MagicMock()
        coordinator._store.async_save = AsyncMock()
        coordinator._resolve_ble_device = MagicMock(return_value=MagicMock())
        coordinator._write_standalone = AsyncMock()
        coordinator._log = MagicMock()
        coordinator._last_raw = {}
        coordinator._last_session_ts = 0
        coordinator._area_remind = None
        coordinator._over_pressure = None
        coordinator._brush_head_max_days = None
        coordinator._brush_head_sw_count = 0
        coordinator._active_scheme_pnum = None
        return coordinator

    @pytest.mark.asyncio
    async def test_persists_without_sending_when_no_date(self):
        coord = self._make_real_coordinator()
        with patch("custom_components.oclean_ble.coordinator.establish_connection") as mock_conn:
            await coord.async_set_birthday(sex=2)
        coord._store.async_save.assert_awaited_once()
        mock_conn.assert_not_called()
        assert coord._birthday_sex == 2
        assert coord._birthday_date is None

    @pytest.mark.asyncio
    async def test_clamps_sex_to_valid_range(self):
        coord = self._make_real_coordinator()
        with patch("custom_components.oclean_ble.coordinator.establish_connection"):
            await coord.async_set_birthday(sex=99)
        assert coord._birthday_sex == 2

        with patch("custom_components.oclean_ble.coordinator.establish_connection"):
            await coord.async_set_birthday(sex=-5)
        assert coord._birthday_sex == 0

    def _fake_today(self):
        """Return a fixed 'today' date for deterministic age calculations."""
        return datetime.date(2026, 3, 23)

    async def _run_birthday(self, coord, birthday, sex=1):
        mock_client = AsyncMock()
        mock_client.is_connected = True
        mock_client.disconnect = AsyncMock()
        with (
            patch("custom_components.oclean_ble.coordinator.establish_connection", return_value=mock_client),
            patch("custom_components.oclean_ble.coordinator.asyncio.sleep", new_callable=AsyncMock),
            patch("custom_components.oclean_ble.coordinator.datetime") as mock_dt,
        ):
            mock_dt.date.today.return_value = self._fake_today()
            mock_dt.date.fromisoformat = datetime.date.fromisoformat
            await coord.async_set_birthday(birthday=birthday, sex=sex)
        return coord._write_standalone.call_args[0][1]

    @pytest.mark.asyncio
    async def test_sends_correct_payload(self):
        coord = self._make_real_coordinator()
        d = datetime.date(2015, 6, 15)
        cmd = await self._run_birthday(coord, d, sex=1)
        # prefix 0x0211
        assert cmd[:2] == bytes.fromhex("0211")
        # sex=1, age=11 (2026-2015), month=6, day=15
        assert cmd[2] == 1  # sex
        assert cmd[3] == 11  # age 2026-2015
        assert cmd[4] == 6  # month
        assert cmd[5] == 15  # day

    @pytest.mark.asyncio
    async def test_age_clamped_below_min(self):
        """A 2-year-old (born 2024) is clamped to age 3."""
        coord = self._make_real_coordinator()
        cmd = await self._run_birthday(coord, datetime.date(2024, 1, 1))
        assert cmd[3] == 3  # clamped to min age

    @pytest.mark.asyncio
    async def test_age_clamped_above_max(self):
        """A 25-year-old (born 2001) is clamped to age 18."""
        coord = self._make_real_coordinator()
        cmd = await self._run_birthday(coord, datetime.date(2001, 1, 1))
        assert cmd[3] == 18  # clamped to max age
