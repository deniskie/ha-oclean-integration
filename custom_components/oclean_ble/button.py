"""Button entities for the Oclean Toothbrush integration."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_DEVICE_NAME, CONF_MAC_ADDRESS, DOMAIN
from .coordinator import OcleanCoordinator
from .entity import OcleanEntity

BUTTON_DESCRIPTIONS: tuple[ButtonEntityDescription, ...] = (
    ButtonEntityDescription(
        key="reset_brush_head",
        name="Reset Brush Head",
        icon="mdi:toothbrush-paste",
    ),
    ButtonEntityDescription(
        key="sync_time",
        name="Sync Time",
        icon="mdi:clock-sync",
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Oclean button entities."""
    coordinator: OcleanCoordinator = hass.data[DOMAIN][entry.entry_id]
    mac = entry.data[CONF_MAC_ADDRESS]
    device_name = entry.data.get(CONF_DEVICE_NAME, "Oclean")

    async_add_entities(OcleanButton(coordinator, description, mac, device_name) for description in BUTTON_DESCRIPTIONS)


class OcleanButton(OcleanEntity, ButtonEntity):
    """Oclean button entity (e.g. brush head reset)."""

    def __init__(
        self,
        coordinator: OcleanCoordinator,
        description: ButtonEntityDescription,
        mac: str,
        device_name: str,
    ) -> None:
        super().__init__(coordinator, mac, device_name, description.key)
        self.entity_description = description

    async def async_press(self) -> None:
        """Handle button press."""
        if self.entity_description.key == "reset_brush_head":
            await self.coordinator.async_reset_brush_head()
        elif self.entity_description.key == "sync_time":
            await self.coordinator.async_sync_time()
