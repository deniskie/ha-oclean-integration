"""Tests for __init__.py – integration setup, teardown, and file handler management."""

from __future__ import annotations

import asyncio
import logging
import logging.handlers
import os
import pathlib
from unittest.mock import AsyncMock, MagicMock, patch

from custom_components.oclean_ble import (
    _FILE_HANDLER_KEY,
    PLATFORMS,
    _attach_file_handler,
    _build_file_handler,
    _detach_file_handler,
    async_setup_entry,
    async_unload_entry,
)
from custom_components.oclean_ble.const import (
    CONF_DEVICE_NAME,
    CONF_MAC_ADDRESS,
    DOMAIN,
    SERVICE_POLL,
)

_TMPDIR = os.environ.get("TMPDIR", "/tmp")


def _make_hass(config_dir: str | None = None) -> MagicMock:
    hass = MagicMock()
    hass.data = {}
    hass.config.config_dir = config_dir or _TMPDIR
    hass.async_add_executor_job = AsyncMock(side_effect=lambda fn, *a: fn(*a))
    hass.config_entries.async_forward_entry_setups = AsyncMock()
    hass.config_entries.async_unload_platforms = AsyncMock(return_value=True)
    hass.config_entries.async_reload = AsyncMock()
    hass.services.has_service = MagicMock(return_value=False)
    hass.services.async_register = MagicMock()
    hass.services.async_remove = MagicMock()
    return hass


def _make_entry(entry_id: str = "test_entry") -> MagicMock:
    from homeassistant.config_entries import ConfigEntry

    return ConfigEntry(
        data={CONF_MAC_ADDRESS: "AA:BB:CC:DD:EE:FF", CONF_DEVICE_NAME: "Oclean"},
        options={},
        entry_id=entry_id,
    )


# ---------------------------------------------------------------------------
# _build_file_handler
# ---------------------------------------------------------------------------


class TestBuildFileHandler:
    def test_returns_rotating_file_handler(self):
        log_path = pathlib.Path(_TMPDIR) / "oclean_test_build.log"
        handler = _build_file_handler(log_path)
        try:
            assert isinstance(handler, logging.handlers.RotatingFileHandler)
        finally:
            handler.close()
            log_path.unlink(missing_ok=True)

    def test_max_bytes_is_1mb(self):
        log_path = pathlib.Path(_TMPDIR) / "oclean_test_maxbytes.log"
        handler = _build_file_handler(log_path)
        try:
            assert handler.maxBytes == 1 * 1024 * 1024
        finally:
            handler.close()
            log_path.unlink(missing_ok=True)

    def test_backup_count_is_2(self):
        log_path = pathlib.Path(_TMPDIR) / "oclean_test_backup.log"
        handler = _build_file_handler(log_path)
        try:
            assert handler.backupCount == 2
        finally:
            handler.close()
            log_path.unlink(missing_ok=True)

    def test_level_is_debug(self):
        log_path = pathlib.Path(_TMPDIR) / "oclean_test_level.log"
        handler = _build_file_handler(log_path)
        try:
            assert handler.level == logging.DEBUG
        finally:
            handler.close()
            log_path.unlink(missing_ok=True)

    def test_encoding_is_utf8(self):
        log_path = pathlib.Path(_TMPDIR) / "oclean_test_enc.log"
        handler = _build_file_handler(log_path)
        try:
            assert handler.encoding == "utf-8"
        finally:
            handler.close()
            log_path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# _attach_file_handler / _detach_file_handler
# ---------------------------------------------------------------------------


class TestAttachFileHandler:
    def test_attaches_handler_to_logger(self):
        hass = _make_hass()
        asyncio.run(_attach_file_handler(hass))
        handler = hass.data[DOMAIN][_FILE_HANDLER_KEY]
        assert handler is not None
        assert isinstance(handler, logging.handlers.RotatingFileHandler)
        oclean_logger = logging.getLogger("custom_components.oclean_ble")
        assert handler in oclean_logger.handlers
        oclean_logger.removeHandler(handler)
        handler.close()

    def test_idempotent_second_call_no_op(self):
        hass = _make_hass()
        asyncio.run(_attach_file_handler(hass))
        first_handler = hass.data[DOMAIN][_FILE_HANDLER_KEY]
        asyncio.run(_attach_file_handler(hass))
        assert hass.data[DOMAIN][_FILE_HANDLER_KEY] is first_handler
        oclean_logger = logging.getLogger("custom_components.oclean_ble")
        count = sum(1 for h in oclean_logger.handlers if h is first_handler)
        assert count == 1
        oclean_logger.removeHandler(first_handler)
        first_handler.close()

    def test_sentinel_prevents_concurrent_attach(self):
        hass = _make_hass()
        hass.data.setdefault(DOMAIN, {})[_FILE_HANDLER_KEY] = None
        asyncio.run(_attach_file_handler(hass))
        assert hass.data[DOMAIN][_FILE_HANDLER_KEY] is None


class TestDetachFileHandler:
    def test_removes_handler_and_closes(self):
        hass = _make_hass()
        asyncio.run(_attach_file_handler(hass))
        handler = hass.data[DOMAIN][_FILE_HANDLER_KEY]
        oclean_logger = logging.getLogger("custom_components.oclean_ble")
        assert handler in oclean_logger.handlers
        asyncio.run(_detach_file_handler(hass))
        assert _FILE_HANDLER_KEY not in hass.data.get(DOMAIN, {})
        assert handler not in oclean_logger.handlers

    def test_no_op_when_no_handler(self):
        hass = _make_hass()
        asyncio.run(_detach_file_handler(hass))

    def test_no_op_when_handler_is_none(self):
        hass = _make_hass()
        hass.data[DOMAIN] = {_FILE_HANDLER_KEY: None}
        asyncio.run(_detach_file_handler(hass))
        assert _FILE_HANDLER_KEY not in hass.data[DOMAIN]


# ---------------------------------------------------------------------------
# async_setup_entry
# ---------------------------------------------------------------------------


class TestAsyncSetupEntry:
    @patch("custom_components.oclean_ble._attach_file_handler", new_callable=AsyncMock)
    @patch("custom_components.oclean_ble.OcleanCoordinator")
    def test_creates_coordinator_and_stores_in_hass_data(self, mock_coord_cls, mock_attach):
        hass = _make_hass()
        entry = _make_entry()
        mock_coord = MagicMock()
        mock_coord.async_refresh = AsyncMock()
        mock_coord_cls.return_value = mock_coord

        result = asyncio.run(async_setup_entry(hass, entry))

        assert result is True
        assert hass.data[DOMAIN][entry.entry_id] is mock_coord

    @patch("custom_components.oclean_ble._attach_file_handler", new_callable=AsyncMock)
    @patch("custom_components.oclean_ble.OcleanCoordinator")
    def test_forwards_platforms(self, mock_coord_cls, mock_attach):
        hass = _make_hass()
        entry = _make_entry()
        mock_coord_cls.return_value = MagicMock(async_refresh=AsyncMock())

        asyncio.run(async_setup_entry(hass, entry))

        hass.config_entries.async_forward_entry_setups.assert_awaited_once_with(entry, PLATFORMS)

    @patch("custom_components.oclean_ble._attach_file_handler", new_callable=AsyncMock)
    @patch("custom_components.oclean_ble.OcleanCoordinator")
    def test_calls_async_refresh(self, mock_coord_cls, mock_attach):
        hass = _make_hass()
        entry = _make_entry()
        mock_coord = MagicMock()
        mock_coord.async_refresh = AsyncMock()
        mock_coord_cls.return_value = mock_coord

        asyncio.run(async_setup_entry(hass, entry))

        mock_coord.async_refresh.assert_awaited_once()

    @patch("custom_components.oclean_ble._attach_file_handler", new_callable=AsyncMock)
    @patch("custom_components.oclean_ble.OcleanCoordinator")
    def test_registers_poll_service(self, mock_coord_cls, mock_attach):
        hass = _make_hass()
        entry = _make_entry()
        mock_coord_cls.return_value = MagicMock(async_refresh=AsyncMock())

        asyncio.run(async_setup_entry(hass, entry))

        hass.services.async_register.assert_called_once()
        call_args = hass.services.async_register.call_args
        assert call_args[0][0] == DOMAIN
        assert call_args[0][1] == SERVICE_POLL

    @patch("custom_components.oclean_ble._attach_file_handler", new_callable=AsyncMock)
    @patch("custom_components.oclean_ble.OcleanCoordinator")
    def test_skips_service_registration_when_already_registered(self, mock_coord_cls, mock_attach):
        hass = _make_hass()
        hass.services.has_service = MagicMock(return_value=True)
        entry = _make_entry()
        mock_coord_cls.return_value = MagicMock(async_refresh=AsyncMock())

        asyncio.run(async_setup_entry(hass, entry))

        hass.services.async_register.assert_not_called()

    @patch("custom_components.oclean_ble._attach_file_handler", new_callable=AsyncMock)
    @patch("custom_components.oclean_ble.OcleanCoordinator")
    def test_coordinator_receives_correct_args(self, mock_coord_cls, mock_attach):
        hass = _make_hass()
        entry = _make_entry()
        mock_coord_cls.return_value = MagicMock(async_refresh=AsyncMock())

        asyncio.run(async_setup_entry(hass, entry))

        mock_coord_cls.assert_called_once()
        args, kwargs = mock_coord_cls.call_args
        assert args[0] is hass
        assert args[1] == "AA:BB:CC:DD:EE:FF"
        assert args[2] == "Oclean"
        assert args[3] == 300  # DEFAULT_POLL_INTERVAL


# ---------------------------------------------------------------------------
# async_unload_entry
# ---------------------------------------------------------------------------


class TestAsyncUnloadEntry:
    @patch("custom_components.oclean_ble._detach_file_handler", new_callable=AsyncMock)
    @patch("custom_components.oclean_ble._attach_file_handler", new_callable=AsyncMock)
    @patch("custom_components.oclean_ble.OcleanCoordinator")
    def test_removes_coordinator_from_hass_data(self, mock_coord_cls, mock_attach, mock_detach):
        hass = _make_hass()
        entry = _make_entry()
        mock_coord_cls.return_value = MagicMock(async_refresh=AsyncMock())

        asyncio.run(async_setup_entry(hass, entry))
        assert entry.entry_id in hass.data[DOMAIN]

        result = asyncio.run(async_unload_entry(hass, entry))

        assert result is True
        assert entry.entry_id not in hass.data[DOMAIN]

    @patch("custom_components.oclean_ble._detach_file_handler", new_callable=AsyncMock)
    @patch("custom_components.oclean_ble._attach_file_handler", new_callable=AsyncMock)
    @patch("custom_components.oclean_ble.OcleanCoordinator")
    def test_calls_detach_on_last_entry(self, mock_coord_cls, mock_attach, mock_detach):
        hass = _make_hass()
        entry = _make_entry()
        mock_coord_cls.return_value = MagicMock(async_refresh=AsyncMock())

        asyncio.run(async_setup_entry(hass, entry))
        asyncio.run(async_unload_entry(hass, entry))

        mock_detach.assert_awaited_once_with(hass)

    @patch("custom_components.oclean_ble._detach_file_handler", new_callable=AsyncMock)
    @patch("custom_components.oclean_ble._attach_file_handler", new_callable=AsyncMock)
    @patch("custom_components.oclean_ble.OcleanCoordinator")
    def test_removes_poll_service_on_last_entry(self, mock_coord_cls, mock_attach, mock_detach):
        hass = _make_hass()
        entry = _make_entry()
        mock_coord_cls.return_value = MagicMock(async_refresh=AsyncMock())

        asyncio.run(async_setup_entry(hass, entry))
        asyncio.run(async_unload_entry(hass, entry))

        hass.services.async_remove.assert_called_once_with(DOMAIN, SERVICE_POLL)

    @patch("custom_components.oclean_ble._detach_file_handler", new_callable=AsyncMock)
    @patch("custom_components.oclean_ble._attach_file_handler", new_callable=AsyncMock)
    @patch("custom_components.oclean_ble.OcleanCoordinator")
    def test_keeps_handler_when_other_entries_remain(self, mock_coord_cls, mock_attach, mock_detach):
        hass = _make_hass()
        entry1 = _make_entry("entry1")
        entry2 = _make_entry("entry2")
        mock_coord_cls.return_value = MagicMock(async_refresh=AsyncMock())

        asyncio.run(async_setup_entry(hass, entry1))
        hass.services.has_service = MagicMock(return_value=True)
        asyncio.run(async_setup_entry(hass, entry2))

        asyncio.run(async_unload_entry(hass, entry1))

        assert "entry2" in hass.data[DOMAIN]
        mock_detach.assert_not_awaited()
        hass.services.async_remove.assert_not_called()

    @patch("custom_components.oclean_ble._detach_file_handler", new_callable=AsyncMock)
    @patch("custom_components.oclean_ble._attach_file_handler", new_callable=AsyncMock)
    @patch("custom_components.oclean_ble.OcleanCoordinator")
    def test_unload_returns_false_when_platforms_fail(self, mock_coord_cls, mock_attach, mock_detach):
        hass = _make_hass()
        hass.config_entries.async_unload_platforms = AsyncMock(return_value=False)
        entry = _make_entry()
        mock_coord_cls.return_value = MagicMock(async_refresh=AsyncMock())

        asyncio.run(async_setup_entry(hass, entry))
        result = asyncio.run(async_unload_entry(hass, entry))

        assert result is False
        assert entry.entry_id in hass.data[DOMAIN]
