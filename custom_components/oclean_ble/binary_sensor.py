"""Binary sensor entities for the Oclean Toothbrush integration."""
from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    CONF_DEVICE_NAME,
    CONF_MAC_ADDRESS,
    DATA_IS_BRUSHING,
    DOMAIN,
)
from .coordinator import OcleanCoordinator
from .entity import OcleanEntity

BINARY_SENSOR_DESCRIPTIONS: tuple[BinarySensorEntityDescription, ...] = (
    BinarySensorEntityDescription(
        key=DATA_IS_BRUSHING,
        name="Brushing",
        device_class=BinarySensorDeviceClass.RUNNING,
        icon="mdi:toothbrush",
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Oclean binary sensor entities."""
    coordinator: OcleanCoordinator = hass.data[DOMAIN][entry.entry_id]
    mac = entry.data[CONF_MAC_ADDRESS]
    device_name = entry.data.get(CONF_DEVICE_NAME, "Oclean")

    async_add_entities(
        OcleanBinarySensor(coordinator, description, mac, device_name)
        for description in BINARY_SENSOR_DESCRIPTIONS
    )


class OcleanBinarySensor(OcleanEntity, BinarySensorEntity):
    """Binary sensor for Oclean brushing state."""

    def __init__(
        self,
        coordinator: OcleanCoordinator,
        description: BinarySensorEntityDescription,
        mac: str,
        device_name: str,
    ) -> None:
        super().__init__(coordinator, mac, device_name, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        """Return True if the device reports active brushing."""
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.is_brushing
