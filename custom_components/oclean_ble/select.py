"""Select entities for the Oclean Toothbrush integration."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_DEVICE_NAME, CONF_MAC_ADDRESS, DOMAIN, OCLEANY3M_SCHEMES
from .coordinator import OcleanCoordinator
from .entity import OcleanEntity

# Ordered list of (pnum, english_name) for stable UI ordering
_SCHEME_OPTIONS: list[tuple[int, str]] = sorted(
    ((pnum, name) for pnum, (name, _) in OCLEANY3M_SCHEMES.items()),
    key=lambda x: x[0],
)
_NAME_TO_PNUM: dict[str, int] = {name: pnum for pnum, name in _SCHEME_OPTIONS}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Oclean select entities."""
    coordinator: OcleanCoordinator = hass.data[DOMAIN][entry.entry_id]
    mac = entry.data[CONF_MAC_ADDRESS]
    device_name = entry.data.get(CONF_DEVICE_NAME, "Oclean")
    async_add_entities([OcleanSchemeSelect(coordinator, mac, device_name)])


class OcleanSchemeSelect(OcleanEntity, SelectEntity):
    """Select entity for choosing the active brush scheme on OCLEANY3M devices.

    Only OCLEANY3M (Oclean X) is supported; the entity reports as unavailable
    for other models.  State is assumed (write-only BLE command) and persisted
    so the selection survives HA restarts.
    """

    _attr_assumed_state = True
    _attr_icon = "mdi:toothbrush"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_translation_key = "brush_scheme"

    def __init__(
        self,
        coordinator: OcleanCoordinator,
        mac: str,
        device_name: str,
    ) -> None:
        super().__init__(coordinator, mac, device_name)
        self._attr_unique_id = f"{mac}_brush_scheme"
        self._attr_options = [name for _, name in _SCHEME_OPTIONS]

    @property
    def available(self) -> bool:
        """Available only for OCLEANY3M devices."""
        if self.coordinator.data is None:
            return False
        model = self.coordinator.data.model_id or ""
        return model == "OCLEANY3M"

    @property
    def current_option(self) -> str | None:
        """Return the name of the currently-active scheme, or None if unknown."""
        pnum = self.coordinator.active_scheme_pnum
        if pnum is None:
            return None
        entry = OCLEANY3M_SCHEMES.get(pnum)
        return entry[0] if entry else None

    async def async_select_option(self, option: str) -> None:
        """Send the SetBrushScheme command for the selected scheme."""
        pnum = _NAME_TO_PNUM.get(option)
        if pnum is None:
            return
        await self.coordinator.async_set_brush_scheme(pnum)
        self.async_write_ha_state()
