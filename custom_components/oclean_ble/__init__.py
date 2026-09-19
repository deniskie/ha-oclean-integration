"""Oclean Smart Toothbrush Home Assistant integration."""

from __future__ import annotations

import json
import logging
import logging.handlers
import pathlib
import queue

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.const import __version__ as HA_VERSION
from homeassistant.core import HomeAssistant, ServiceCall

from .const import (
    CONF_DEVICE_NAME,
    CONF_MAC_ADDRESS,
    CONF_POLL_INTERVAL,
    CONF_POLL_WINDOWS,
    CONF_POST_BRUSH_COOLDOWN,
    DEFAULT_POLL_INTERVAL,
    DEFAULT_POST_BRUSH_COOLDOWN,
    DOMAIN,
    SERVICE_POLL,
)
from .coordinator import OcleanCoordinator

_LOGGER = logging.getLogger(__name__)
_MANIFEST = json.loads((pathlib.Path(__file__).parent / "manifest.json").read_text())
_INTEGRATION_VERSION = _MANIFEST.get("version", "unknown")

PLATFORMS: list[Platform] = [
    Platform.BUTTON,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
]

# Key under hass.data[DOMAIN] where the shared (queue) log handler is stored
_FILE_HANDLER_KEY = "_file_handler"
# Key for the QueueListener thread that owns the RotatingFileHandler
_LOG_LISTENER_KEY = "_log_listener"


def _build_file_handler(log_path: pathlib.Path) -> logging.handlers.RotatingFileHandler:
    """Create the RotatingFileHandler (blocking I/O – must run in executor)."""
    handler = logging.handlers.RotatingFileHandler(
        log_path,
        maxBytes=1 * 1024 * 1024,  # 1 MB per file
        backupCount=2,  # keep oclean_ble.log + .1 + .2
        encoding="utf-8",
    )
    # No handler-level filter: the integration logger's effective level (set
    # via HA's `logger:` config or the UI debug toggle) decides what is
    # written.  The handler is only attached at all when debug logging is
    # enabled for this integration – see _attach_file_handler().
    handler.setLevel(logging.NOTSET)
    handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s  %(levelname)-8s  [%(name)s]  %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    return handler


async def _attach_file_handler(hass: HomeAssistant) -> None:
    """Attach a rotating file handler to the oclean_ble logger (once per HA session).

    Log file: <config_dir>/oclean_ble.log
    Max size:  1 MB, 2 rotated backups (≤ 3 MB total)

    Opt-in: the file is only written while debug logging is enabled for this
    integration (``logger:`` YAML config or the UI "enable debug logging"
    toggle, followed by an integration reload).  Without debug enabled no file
    handler is attached and no log file is created, so raw hex payloads and
    session data never end up on disk (or in backups) by default.

    The handler is shared across multiple config entries (multiple devices).
    It is removed when the last entry is unloaded.
    """
    domain_data = hass.data.setdefault(DOMAIN, {})
    if _FILE_HANDLER_KEY in domain_data:
        return  # already attached (or attachment in progress)

    oclean_logger = logging.getLogger("custom_components.oclean_ble")
    if not oclean_logger.isEnabledFor(logging.DEBUG):
        # Debug logging not enabled for this integration – skip the file log.
        # Deliberately no sentinel here: a later reload with debug enabled
        # must be able to attach the handler.
        return

    # Set sentinel *before* the async gap so that a second config entry being
    # set up concurrently also sees the key and skips duplicate attachment.
    domain_data[_FILE_HANDLER_KEY] = None

    log_path = pathlib.Path(hass.config.config_dir) / "oclean_ble.log"
    # open() is blocking – run in the default executor to avoid loop warnings
    file_handler = await hass.async_add_executor_job(_build_file_handler, log_path)

    # All file I/O happens on the listener thread, never on the event loop.
    # The logger itself only gets a QueueHandler, whose emit() is a queue put.
    # Without this, a log call made from the loop performs the write – and on
    # rollover also a close()/open() pair – inline, which HA reports as a
    # blocking call inside the event loop (issue #124).
    log_queue: queue.SimpleQueue[logging.LogRecord] = queue.SimpleQueue()
    listener = logging.handlers.QueueListener(log_queue, file_handler, respect_handler_level=True)
    listener.start()
    queue_handler = logging.handlers.QueueHandler(log_queue)
    queue_handler.setLevel(logging.NOTSET)

    oclean_logger.addHandler(queue_handler)
    domain_data[_FILE_HANDLER_KEY] = queue_handler
    domain_data[_LOG_LISTENER_KEY] = listener
    _LOGGER.info("Oclean log file: %s", log_path)


async def _detach_file_handler(hass: HomeAssistant) -> None:
    """Remove the log handler when the last entry is unloaded."""
    domain_data = hass.data.get(DOMAIN, {})
    handler = domain_data.pop(_FILE_HANDLER_KEY, None)
    listener = domain_data.pop(_LOG_LISTENER_KEY, None)
    if handler is None:
        return
    oclean_logger = logging.getLogger("custom_components.oclean_ble")
    oclean_logger.removeHandler(handler)
    if listener is not None:
        # stop() drains the queue and closes the file – both blocking.
        await hass.async_add_executor_job(_stop_listener, listener)
    await hass.async_add_executor_job(handler.close)
    _LOGGER.debug("Oclean file log handler detached")


def _stop_listener(listener: logging.handlers.QueueListener) -> None:
    """Drain the queue, then close the file handlers it owns (blocking)."""
    listener.stop()
    for handler in listener.handlers:
        handler.close()


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Oclean from a config entry."""
    await _attach_file_handler(hass)

    mac = entry.data[CONF_MAC_ADDRESS]
    device_name = entry.data.get(CONF_DEVICE_NAME, "Oclean")
    poll_interval = entry.options.get(
        CONF_POLL_INTERVAL,
        entry.data.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL),
    )

    poll_windows = entry.options.get(CONF_POLL_WINDOWS, "")
    post_brush_cooldown_h = int(entry.options.get(CONF_POST_BRUSH_COOLDOWN, DEFAULT_POST_BRUSH_COOLDOWN))

    _LOGGER.info(
        "Oclean integration v%s starting: mac=%s name=%s (HA %s)",
        _INTEGRATION_VERSION,
        mac,
        device_name,
        HA_VERSION,
    )
    _LOGGER.debug(
        "Oclean config: poll_interval=%s poll_windows=%r post_brush_cooldown_h=%d",
        f"{poll_interval}s" if poll_interval > 0 else "manual (disabled)",
        poll_windows or "(none)",
        post_brush_cooldown_h,
    )

    coordinator = OcleanCoordinator(
        hass,
        mac,
        device_name,
        poll_interval,
        poll_windows=poll_windows,
        post_brush_cooldown_h=post_brush_cooldown_h,
    )

    # Register coordinator and set up platforms *before* the first poll so that
    # the poll service and all entities always exist, even when the device is
    # sleeping on HA startup.  Entities will show as unavailable until the first
    # successful poll.
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Listen for option updates (e.g. changed poll interval)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    # Register the poll service once per domain (shared across all config entries)
    if not hass.services.has_service(DOMAIN, SERVICE_POLL):

        async def _handle_poll(call: ServiceCall) -> None:
            """Trigger an immediate BLE poll for one or all Oclean devices."""
            entry_id: str | None = call.data.get("entry_id")
            domain_data = hass.data.get(DOMAIN, {})
            if entry_id:
                coordinator = domain_data.get(entry_id)
                if coordinator and isinstance(coordinator, OcleanCoordinator):
                    await coordinator.async_poll_now()
            else:
                for key, value in domain_data.items():
                    if not key.startswith("_") and isinstance(value, OcleanCoordinator):
                        await value.async_poll_now()

        hass.services.async_register(
            DOMAIN,
            SERVICE_POLL,
            _handle_poll,
            schema=vol.Schema({vol.Optional("entry_id"): str}),
        )

    # Initial poll: best-effort and NON-BLOCKING.  Awaiting async_refresh() here
    # would stall HA startup by up to BLE_POLL_TOTAL_TIMEOUT + several connect
    # attempts while the BLE stack waits for a possibly-sleeping toothbrush,
    # triggering HA's "still starting / not everything available" warning.
    # Run it as a background task tied to the entry lifecycle instead so setup
    # returns immediately; entities stay unavailable until the poll succeeds
    # (on the configured interval or via a manual service call).  The task is
    # cancelled automatically on unload.
    entry.async_create_background_task(
        hass,
        coordinator.async_refresh(),
        name=f"{DOMAIN}_initial_refresh_{entry.entry_id}",
    )

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
        # Remove file handler and poll service only when no more entries remain
        remaining = [k for k in hass.data.get(DOMAIN, {}) if not k.startswith("_")]
        if not remaining:
            await _detach_file_handler(hass)
            hass.services.async_remove(DOMAIN, SERVICE_POLL)
    return unload_ok


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle options update (e.g. poll interval change)."""
    await hass.config_entries.async_reload(entry.entry_id)
