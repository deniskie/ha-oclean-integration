"""Date entities for the Oclean Toothbrush integration."""

from __future__ import annotations

import datetime

from homeassistant.components.date import DateEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_DEVICE_NAME, CONF_MAC_ADDRESS, DOMAIN
from .coordinator import OcleanCoordinator
from .entity import OcleanEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Oclean date entities."""
    coordinator: OcleanCoordinator = hass.data[DOMAIN][entry.entry_id]
    mac = entry.data[CONF_MAC_ADDRESS]
    device_name = entry.data.get(CONF_DEVICE_NAME, "Oclean")
    async_add_entities([OcleanBirthdayDate(coordinator, mac, device_name)])


class OcleanBirthdayDate(OcleanEntity, DateEntity):
    """Date entity for setting the child's birthday (CMD 0211).

    Part of the child-mode feature: the device adjusts its brushing guidance
    based on the child's age (derived from the birthday) and sex.
    State is assumed (write-only BLE command) and persisted locally so the
    entity shows the correct date after HA restarts.
    The BLE command is sent only when a birthday is available; updating sex
    alone via the companion select entity also triggers a send if a birthday
    is already set.
    """

    _attr_assumed_state = True
    _attr_icon = "mdi:cake-variant-outline"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_translation_key = "birthday"

    def __init__(
        self,
        coordinator: OcleanCoordinator,
        mac: str,
        device_name: str,
    ) -> None:
        super().__init__(coordinator, mac, device_name, "birthday")

    @property
    def native_value(self) -> datetime.date | None:
        return self.coordinator.birthday_date

    async def async_set_value(self, value: datetime.date) -> None:
        await self.coordinator.async_set_birthday(birthday=value)
        self.async_write_ha_state()
