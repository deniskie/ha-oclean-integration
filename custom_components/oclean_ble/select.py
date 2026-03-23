"""Select entities for the Oclean Toothbrush integration."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    CONF_DEVICE_NAME,
    CONF_MAC_ADDRESS,
    DOMAIN,
    OCLEANY3M_SCHEMES,
    SCHEMES_BY_MODEL,
)
from .coordinator import OcleanCoordinator
from .entity import OcleanEntity
from .protocol import TYPE1, TYPE_Z1, protocol_for_model


def _schemes_for_model(
    model_id: str | None,
) -> dict[int, tuple[str, list[tuple[int, int]]]] | None:
    """Return the scheme dict for a device model, or None if unsupported."""
    if not model_id:
        return None
    proto = protocol_for_model(model_id)
    if proto is TYPE1:
        return SCHEMES_BY_MODEL.get(model_id, OCLEANY3M_SCHEMES)
    if proto is TYPE_Z1:
        return SCHEMES_BY_MODEL.get(model_id)
    return None


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
    """Select entity for choosing the active brush scheme.

    Supported on all TYPE1 devices (OCLEANY3M / X family, OCLEANY3P / X Pro Elite,
    OCLEANY3 / X Pro, OCLEANX20, …) and TYPE_Z1 (OCLEANY5 / Z1).
    The entity reports as unavailable for Legacy and unsupported models.
    State is assumed (write-only BLE command) and persisted so the selection
    survives HA restarts.
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
        super().__init__(coordinator, mac, device_name, "brush_scheme")

    @property
    def available(self) -> bool:
        """Available only for models that have a scheme dict."""
        if self.coordinator.data is None:
            return False
        return _schemes_for_model(self.coordinator.data.model_id) is not None

    @property
    def options(self) -> list[str]:
        """Return selectable scheme names for the current device model."""
        if self.coordinator.data is None:
            return []
        schemes = _schemes_for_model(self.coordinator.data.model_id)
        if schemes is None:
            return []
        return [name for _, (name, _) in sorted(schemes.items())]

    @property
    def current_option(self) -> str | None:
        """Return the name of the currently-active scheme, or None if unknown."""
        pnum = self.coordinator.active_scheme_pnum
        if pnum is None or self.coordinator.data is None:
            return None
        schemes = _schemes_for_model(self.coordinator.data.model_id)
        if schemes is None:
            return None
        entry = schemes.get(pnum)
        return entry[0] if entry else None

    async def async_select_option(self, option: str) -> None:
        """Send the SetBrushScheme command for the selected scheme."""
        if self.coordinator.data is None:
            return
        schemes = _schemes_for_model(self.coordinator.data.model_id)
        if schemes is None:
            return
        name_to_pnum = {name: pnum for pnum, (name, _) in schemes.items()}
        pnum = name_to_pnum.get(option)
        if pnum is None:
            return
        await self.coordinator.async_set_brush_scheme(pnum)
        self.async_write_ha_state()
