"""Binary sensor platform for the Oclean Toothbrush integration.

No binary sensors are currently exposed because active-brushing detection
is not reliably determinable from the available BLE data.
"""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Oclean binary sensor entities (none currently)."""
