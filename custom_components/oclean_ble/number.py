"""Number entities for the Oclean Toothbrush integration."""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberEntityDescription, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_DEVICE_NAME, CONF_MAC_ADDRESS, DOMAIN
from .coordinator import OcleanCoordinator
from .entity import OcleanEntity

NUMBER_DESCRIPTIONS: tuple[NumberEntityDescription, ...] = (
    NumberEntityDescription(
        key="brush_head_max_days",
        icon="mdi:toothbrush",
        native_min_value=30,
        native_max_value=365,
        native_step=1,
        native_unit_of_measurement=UnitOfTime.DAYS,
        mode=NumberMode.BOX,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Oclean number entities."""
    coordinator: OcleanCoordinator = hass.data[DOMAIN][entry.entry_id]
    mac = entry.data[CONF_MAC_ADDRESS]
    device_name = entry.data.get(CONF_DEVICE_NAME, "Oclean")
    async_add_entities(OcleanNumber(coordinator, description, mac, device_name) for description in NUMBER_DESCRIPTIONS)


class OcleanNumber(OcleanEntity, NumberEntity):
    """Oclean number entity for numeric device settings.

    State is stored persistently in the coordinator; _attr_assumed_state
    signals that write-only commands cannot be confirmed by the device.
    """

    _attr_assumed_state = True

    def __init__(
        self,
        coordinator: OcleanCoordinator,
        description: NumberEntityDescription,
        mac: str,
        device_name: str,
    ) -> None:
        super().__init__(coordinator, mac, device_name, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> float | None:
        if self.entity_description.key == "brush_head_max_days":
            v = self.coordinator.brush_head_max_days
            return float(v) if v is not None else None
        return None

    async def async_set_native_value(self, value: float) -> None:
        if self.entity_description.key == "brush_head_max_days":
            await self.coordinator.async_set_brush_head_max_days(int(value))
