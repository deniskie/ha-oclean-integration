"""Oclean Smart Toothbrush Home Assistant integration."""
from __future__ import annotations

import logging
import logging.handlers
import pathlib

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .const import (
    CONF_DEVICE_NAME,
    CONF_MAC_ADDRESS,
    CONF_POLL_INTERVAL,
    CONF_POLL_WINDOWS,
    CONF_POST_BRUSH_COOLDOWN,
    DEFAULT_POLL_INTERVAL,
    DEFAULT_POST_BRUSH_COOLDOWN,
    DOMAIN,
)
from .coordinator import OcleanCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.BINARY_SENSOR, Platform.BUTTON]

# Key under hass.data[DOMAIN] where the shared file handler is stored
_FILE_HANDLER_KEY = "_file_handler"


def _build_file_handler(log_path: pathlib.Path) -> logging.handlers.RotatingFileHandler:
    """Create the RotatingFileHandler (blocking I/O – must run in executor)."""
    handler = logging.handlers.RotatingFileHandler(
        log_path,
        maxBytes=1 * 1024 * 1024,  # 1 MB per file
        backupCount=2,              # keep oclean_ble.log + .1 + .2
        encoding="utf-8",
    )
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(logging.Formatter(
        fmt="%(asctime)s  %(levelname)-8s  [%(name)s]  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    return handler


async def _attach_file_handler(hass: HomeAssistant) -> None:
    """Attach a rotating file handler to the oclean_ble logger (once per HA session).

    Log file: <config_dir>/oclean_ble.log
    Max size:  1 MB, 2 rotated backups (≤ 3 MB total)
    Level:     DEBUG – all unknown-byte traces and raw hex dumps included.

    The handler is shared across multiple config entries (multiple devices).
    It is removed when the last entry is unloaded.
    """
    domain_data = hass.data.setdefault(DOMAIN, {})
    if _FILE_HANDLER_KEY in domain_data:
        return  # already attached

    log_path = pathlib.Path(hass.config.config_dir) / "oclean_ble.log"
    # open() is blocking – run in the default executor to avoid loop warnings
    handler = await hass.async_add_executor_job(_build_file_handler, log_path)

    oclean_logger = logging.getLogger("custom_components.oclean_ble")
    oclean_logger.addHandler(handler)
    domain_data[_FILE_HANDLER_KEY] = handler
    _LOGGER.debug("Oclean file log handler attached → %s", log_path)


async def _detach_file_handler(hass: HomeAssistant) -> None:
    """Remove the file handler when the last entry is unloaded."""
    domain_data = hass.data.get(DOMAIN, {})
    handler = domain_data.pop(_FILE_HANDLER_KEY, None)
    if handler is None:
        return
    oclean_logger = logging.getLogger("custom_components.oclean_ble")
    oclean_logger.removeHandler(handler)
    # handler.close() flushes and closes the underlying file – run in executor
    await hass.async_add_executor_job(handler.close)
    _LOGGER.debug("Oclean file log handler detached")


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
    post_brush_cooldown_h = int(
        entry.options.get(CONF_POST_BRUSH_COOLDOWN, DEFAULT_POST_BRUSH_COOLDOWN)
    )

    coordinator = OcleanCoordinator(
        hass, mac, device_name, poll_interval,
        poll_windows=poll_windows,
        post_brush_cooldown_h=post_brush_cooldown_h,
    )

    # Perform the first refresh – raises ConfigEntryNotReady if device unreachable
    # and no cached data exists yet.
    try:
        await coordinator.async_config_entry_first_refresh()
    except Exception as err:
        # Device may be sleeping; don't block setup entirely – HA will retry.
        _LOGGER.warning(
            "Oclean initial poll failed (%s) – integration will retry", err
        )
        raise ConfigEntryNotReady(f"Oclean not reachable on startup: {err}") from err

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Listen for option updates (e.g. changed poll interval)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
        # Remove file handler only when no more entries remain
        remaining = [
            k for k in hass.data.get(DOMAIN, {})
            if not k.startswith("_")
        ]
        if not remaining:
            await _detach_file_handler(hass)
    return unload_ok


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle options update (e.g. poll interval change)."""
    await hass.config_entries.async_reload(entry.entry_id)
