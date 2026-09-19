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
    _LOG_LISTENER_KEY,
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


def _run_setup(hass, entry):
    """Run async_setup_entry and drain the entry's background tasks.

    The initial poll is scheduled via entry.async_create_background_task (it
    must not block HA startup), so tests have to await it explicitly before
    asserting on the coordinator.
    """

    async def _inner():
        result = await async_setup_entry(hass, entry)
        if getattr(entry, "background_tasks", None):
            await asyncio.gather(*entry.background_tasks)
        return result

    return asyncio.run(_inner())


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

    def test_level_is_notset(self):
        # No handler-level filter: the integration logger's effective level
        # decides what is written (the handler is only attached when debug
        # logging is enabled for the integration).
        log_path = pathlib.Path(_TMPDIR) / "oclean_test_level.log"
        handler = _build_file_handler(log_path)
        try:
            assert handler.level == logging.NOTSET
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


class _debug_enabled:
    """Temporarily enable DEBUG on the integration logger (restored on exit)."""

    def __enter__(self):
        self._logger = logging.getLogger("custom_components.oclean_ble")
        self._old_level = self._logger.level
        self._logger.setLevel(logging.DEBUG)
        return self._logger

    def __exit__(self, *exc_info):
        self._logger.setLevel(self._old_level)


class _debug_disabled:
    """Temporarily force the integration logger above DEBUG (restored on exit)."""

    def __enter__(self):
        self._logger = logging.getLogger("custom_components.oclean_ble")
        self._old_level = self._logger.level
        self._logger.setLevel(logging.INFO)
        return self._logger

    def __exit__(self, *exc_info):
        self._logger.setLevel(self._old_level)


class TestAttachFileHandler:
    def test_attaches_queue_handler_to_logger(self):
        # The logger must get a QueueHandler, not the file handler itself:
        # its emit() is a queue put, so a log call from the event loop never
        # performs file I/O (issue #124).
        hass = _make_hass()
        with _debug_enabled() as oclean_logger:
            asyncio.run(_attach_file_handler(hass))
            handler = hass.data[DOMAIN][_FILE_HANDLER_KEY]
            assert isinstance(handler, logging.handlers.QueueHandler)
            assert handler in oclean_logger.handlers
            assert not any(isinstance(h, logging.handlers.RotatingFileHandler) for h in oclean_logger.handlers)
            asyncio.run(_detach_file_handler(hass))

    def test_listener_owns_the_rotating_file_handler(self):
        hass = _make_hass()
        with _debug_enabled():
            asyncio.run(_attach_file_handler(hass))
            listener = hass.data[DOMAIN][_LOG_LISTENER_KEY]
            assert [type(h) for h in listener.handlers] == [logging.handlers.RotatingFileHandler]
            asyncio.run(_detach_file_handler(hass))

    def test_record_reaches_the_file_through_the_queue(self):
        hass = _make_hass()
        with _debug_enabled() as oclean_logger:
            asyncio.run(_attach_file_handler(hass))
            listener = hass.data[DOMAIN][_LOG_LISTENER_KEY]
            log_path = pathlib.Path(listener.handlers[0].baseFilename)
            oclean_logger.debug("queued marker line")
            asyncio.run(_detach_file_handler(hass))  # stop() drains the queue
            assert "queued marker line" in log_path.read_text(encoding="utf-8")

    def test_detach_stops_the_listener_thread(self):
        hass = _make_hass()
        with _debug_enabled():
            asyncio.run(_attach_file_handler(hass))
            listener = hass.data[DOMAIN][_LOG_LISTENER_KEY]
            asyncio.run(_detach_file_handler(hass))
            assert listener._thread is None
            assert _LOG_LISTENER_KEY not in hass.data[DOMAIN]

    def test_idempotent_second_call_no_op(self):
        hass = _make_hass()
        with _debug_enabled() as oclean_logger:
            asyncio.run(_attach_file_handler(hass))
            first_handler = hass.data[DOMAIN][_FILE_HANDLER_KEY]
            asyncio.run(_attach_file_handler(hass))
            assert hass.data[DOMAIN][_FILE_HANDLER_KEY] is first_handler
            count = sum(1 for h in oclean_logger.handlers if h is first_handler)
            assert count == 1
            oclean_logger.removeHandler(first_handler)
            first_handler.close()

    def test_sentinel_prevents_concurrent_attach(self):
        hass = _make_hass()
        hass.data.setdefault(DOMAIN, {})[_FILE_HANDLER_KEY] = None
        with _debug_enabled():
            asyncio.run(_attach_file_handler(hass))
        assert hass.data[DOMAIN][_FILE_HANDLER_KEY] is None

    # --- Opt-in behaviour: no debug → no file log ---

    def test_skips_attach_when_debug_disabled(self):
        hass = _make_hass()
        with _debug_disabled() as oclean_logger:
            handlers_before = list(oclean_logger.handlers)
            asyncio.run(_attach_file_handler(hass))
            assert _FILE_HANDLER_KEY not in hass.data.get(DOMAIN, {})
            assert oclean_logger.handlers == handlers_before

    def test_attach_works_after_enabling_debug(self):
        # A skipped attach must not poison the sentinel: enabling debug and
        # reloading (= calling attach again) must attach the handler.
        hass = _make_hass()
        with _debug_disabled():
            asyncio.run(_attach_file_handler(hass))
        assert _FILE_HANDLER_KEY not in hass.data.get(DOMAIN, {})
        with _debug_enabled() as oclean_logger:
            asyncio.run(_attach_file_handler(hass))
            handler = hass.data[DOMAIN][_FILE_HANDLER_KEY]
            assert handler is not None
            assert handler in oclean_logger.handlers
            oclean_logger.removeHandler(handler)
            handler.close()


class TestDetachFileHandler:
    def test_removes_handler_and_closes(self):
        hass = _make_hass()
        with _debug_enabled() as oclean_logger:
            asyncio.run(_attach_file_handler(hass))
            handler = hass.data[DOMAIN][_FILE_HANDLER_KEY]
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

        result = _run_setup(hass, entry)

        assert result is True
        assert hass.data[DOMAIN][entry.entry_id] is mock_coord

    @patch("custom_components.oclean_ble._attach_file_handler", new_callable=AsyncMock)
    @patch("custom_components.oclean_ble.OcleanCoordinator")
    def test_forwards_platforms(self, mock_coord_cls, mock_attach):
        hass = _make_hass()
        entry = _make_entry()
        mock_coord_cls.return_value = MagicMock(async_refresh=AsyncMock())

        _run_setup(hass, entry)

        hass.config_entries.async_forward_entry_setups.assert_awaited_once_with(entry, PLATFORMS)

    @patch("custom_components.oclean_ble._attach_file_handler", new_callable=AsyncMock)
    @patch("custom_components.oclean_ble.OcleanCoordinator")
    def test_calls_async_refresh(self, mock_coord_cls, mock_attach):
        hass = _make_hass()
        entry = _make_entry()
        mock_coord = MagicMock()
        mock_coord.async_refresh = AsyncMock()
        mock_coord_cls.return_value = mock_coord

        _run_setup(hass, entry)

        mock_coord.async_refresh.assert_awaited_once()

    @patch("custom_components.oclean_ble._attach_file_handler", new_callable=AsyncMock)
    @patch("custom_components.oclean_ble.OcleanCoordinator")
    def test_initial_refresh_does_not_block_setup(self, mock_coord_cls, mock_attach):
        # Regression: awaiting the initial poll stalled HA startup for up to
        # BLE_POLL_TOTAL_TIMEOUT per sleeping brush ("still starting" warning).
        # Setup must return while the poll is still in flight.
        hass = _make_hass()
        entry = _make_entry()
        started = asyncio.Event()
        release = asyncio.Event()

        async def _slow_refresh():
            started.set()
            await release.wait()

        mock_coord = MagicMock()
        mock_coord.async_refresh = AsyncMock(side_effect=_slow_refresh)
        mock_coord_cls.return_value = mock_coord

        async def _inner():
            result = await async_setup_entry(hass, entry)
            # Setup returned even though the poll has not finished.
            await asyncio.wait_for(started.wait(), timeout=1)
            assert not entry.background_tasks[0].done()
            release.set()
            await asyncio.gather(*entry.background_tasks)
            return result

        assert asyncio.run(_inner()) is True
        mock_coord.async_refresh.assert_awaited_once()

    @patch("custom_components.oclean_ble._attach_file_handler", new_callable=AsyncMock)
    @patch("custom_components.oclean_ble.OcleanCoordinator")
    def test_initial_refresh_task_is_entry_scoped(self, mock_coord_cls, mock_attach):
        # The task must be created on the config entry (not a bare
        # asyncio.create_task) so HA cancels it automatically on unload.
        hass = _make_hass()
        entry = _make_entry()
        mock_coord_cls.return_value = MagicMock(async_refresh=AsyncMock())

        _run_setup(hass, entry)

        assert len(entry.background_tasks) == 1

    @patch("custom_components.oclean_ble._attach_file_handler", new_callable=AsyncMock)
    @patch("custom_components.oclean_ble.OcleanCoordinator")
    def test_registers_poll_service(self, mock_coord_cls, mock_attach):
        hass = _make_hass()
        entry = _make_entry()
        mock_coord_cls.return_value = MagicMock(async_refresh=AsyncMock())

        _run_setup(hass, entry)

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

        _run_setup(hass, entry)

        hass.services.async_register.assert_not_called()

    @patch("custom_components.oclean_ble._attach_file_handler", new_callable=AsyncMock)
    @patch("custom_components.oclean_ble.OcleanCoordinator")
    def test_coordinator_receives_correct_args(self, mock_coord_cls, mock_attach):
        hass = _make_hass()
        entry = _make_entry()
        mock_coord_cls.return_value = MagicMock(async_refresh=AsyncMock())

        _run_setup(hass, entry)

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

        _run_setup(hass, entry)
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

        _run_setup(hass, entry)
        asyncio.run(async_unload_entry(hass, entry))

        mock_detach.assert_awaited_once_with(hass)

    @patch("custom_components.oclean_ble._detach_file_handler", new_callable=AsyncMock)
    @patch("custom_components.oclean_ble._attach_file_handler", new_callable=AsyncMock)
    @patch("custom_components.oclean_ble.OcleanCoordinator")
    def test_removes_poll_service_on_last_entry(self, mock_coord_cls, mock_attach, mock_detach):
        hass = _make_hass()
        entry = _make_entry()
        mock_coord_cls.return_value = MagicMock(async_refresh=AsyncMock())

        _run_setup(hass, entry)
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

        _run_setup(hass, entry1)
        hass.services.has_service = MagicMock(return_value=True)
        _run_setup(hass, entry2)

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

        _run_setup(hass, entry)
        result = asyncio.run(async_unload_entry(hass, entry))

        assert result is False
        assert entry.entry_id in hass.data[DOMAIN]
