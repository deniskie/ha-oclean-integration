"""Binary sensor platform for the Oclean Toothbrush integration."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_DEVICE_NAME, CONF_MAC_ADDRESS, DATA_IS_BRUSHING, DOMAIN
from .coordinator import OcleanCoordinator
from .entity import OcleanEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Oclean binary sensor entities."""
    coordinator: OcleanCoordinator = hass.data[DOMAIN][entry.entry_id]
    mac = entry.data[CONF_MAC_ADDRESS]
    device_name = entry.data[CONF_DEVICE_NAME]
    async_add_entities([OcleanBinarySensor(coordinator, mac, device_name)])


class OcleanBinarySensor(OcleanEntity, BinarySensorEntity):
    """Binary sensor for active brushing state (0303 byte 0, bit 0).

    True when the device reports it is currently brushing at poll time.
    Because the integration polls every 5 minutes the sensor typically reads
    False; it will read True only if a poll happens to land during a session.
    """

    _attr_device_class = BinarySensorDeviceClass.RUNNING
    _attr_translation_key = "is_brushing"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: OcleanCoordinator,
        mac: str,
        device_name: str,
    ) -> None:
        super().__init__(coordinator, mac, device_name, "is_brushing")
        self._attr_name = "Brushing"

    @property
    def is_on(self) -> bool | None:
        """Return True when the device is actively brushing."""
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.get(DATA_IS_BRUSHING)
