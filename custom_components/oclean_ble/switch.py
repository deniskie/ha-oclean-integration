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
    SwitchEntityDescription(
        key="remind_switch",
        name="Brushing Reminder",
        icon="mdi:bell-ring-outline",
    ),
    SwitchEntityDescription(
        key="running_switch",
        name="Auto Power-Off Timer",
        icon="mdi:timer-off-outline",
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

    # Map switch key → coordinator property / setter method name.
    _KEY_TO_PROP = {
        "area_remind": "area_remind",
        "over_pressure": "over_pressure",
        "remind_switch": "remind_switch",
        "running_switch": "running_switch",
    }
    _KEY_TO_SETTER = {
        "area_remind": "async_set_area_remind",
        "over_pressure": "async_set_over_pressure",
        "remind_switch": "async_set_remind_switch",
        "running_switch": "async_set_running_switch",
    }

    @property
    def is_on(self) -> bool | None:
        prop = self._KEY_TO_PROP.get(self.entity_description.key)
        return getattr(self.coordinator, prop, None) if prop else None

    async def async_turn_on(self, **kwargs) -> None:
        setter = self._KEY_TO_SETTER.get(self.entity_description.key)
        if setter:
            await getattr(self.coordinator, setter)(True)

    async def async_turn_off(self, **kwargs) -> None:
        setter = self._KEY_TO_SETTER.get(self.entity_description.key)
        if setter:
            await getattr(self.coordinator, setter)(False)
