"""Switch entities for the Oclean Toothbrush integration."""

from __future__ import annotations

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_DEVICE_NAME, CONF_MAC_ADDRESS, DOMAIN
from .coordinator import OcleanCoordinator
from .entity import OcleanEntity

SWITCH_DESCRIPTIONS: tuple[SwitchEntityDescription, ...] = (
    SwitchEntityDescription(
        key="area_remind",
        name="Area Reminder",
        icon="mdi:tooth-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SwitchEntityDescription(
        key="over_pressure",
        name="Over-Pressure Alert",
        icon="mdi:alert-circle-outline",
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Oclean switch entities."""
    coordinator: OcleanCoordinator = hass.data[DOMAIN][entry.entry_id]
    mac = entry.data[CONF_MAC_ADDRESS]
    device_name = entry.data.get(CONF_DEVICE_NAME, "Oclean")
    async_add_entities(OcleanSwitch(coordinator, description, mac, device_name) for description in SWITCH_DESCRIPTIONS)


class OcleanSwitch(OcleanEntity, SwitchEntity):
    """Oclean switch entity for device settings that can only be written, not read back.

    State is stored persistently in the coordinator so the switch shows the
    correct position after HA restarts.  _attr_assumed_state signals to the
    frontend that the integration cannot confirm the command was applied.
    """

    _attr_assumed_state = True

    def __init__(
        self,
        coordinator: OcleanCoordinator,
        description: SwitchEntityDescription,
        mac: str,
        device_name: str,
    ) -> None:
        super().__init__(coordinator, mac, device_name, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        if self.entity_description.key == "area_remind":
            return self.coordinator.area_remind
        if self.entity_description.key == "over_pressure":
            return self.coordinator.over_pressure
        return None

    async def async_turn_on(self, **kwargs) -> None:
        if self.entity_description.key == "area_remind":
            await self.coordinator.async_set_area_remind(True)
        elif self.entity_description.key == "over_pressure":
            await self.coordinator.async_set_over_pressure(True)

    async def async_turn_off(self, **kwargs) -> None:
        if self.entity_description.key == "area_remind":
            await self.coordinator.async_set_area_remind(False)
        elif self.entity_description.key == "over_pressure":
            await self.coordinator.async_set_over_pressure(False)
