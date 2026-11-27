"""Diagnostics download for the Oclean Toothbrush integration.

Contains the command-probe report and the last raw notification frames, so a
user can attach one file to an issue when their model behaves differently.
The MAC address and the device name are redacted.
"""

from __future__ import annotations

import dataclasses
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_DEVICE_NAME, CONF_MAC_ADDRESS, DOMAIN
from .coordinator import OcleanCoordinator

TO_REDACT = {CONF_MAC_ADDRESS, CONF_DEVICE_NAME}


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: ConfigEntry) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator: OcleanCoordinator = hass.data[DOMAIN][entry.entry_id]
    frames = coordinator.raw_frames
    return {
        "entry": async_redact_data({"data": dict(entry.data), "options": dict(entry.options)}, TO_REDACT),
        "protocol": coordinator.protocol_name,
        "last_poll_successful": coordinator.last_poll_successful,
        "device_data": dataclasses.asdict(coordinator.data) if coordinator.data else None,
        "probe_report": coordinator.probe_report,
        "unknown_frames": sorted({frame["hex"] for frame in frames if not frame["known"]}),
        "raw_frames": frames,
    }
