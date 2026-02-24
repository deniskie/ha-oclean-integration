"""Shared entity base class for the Oclean BLE integration."""
from __future__ import annotations

from typing import Any

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DATA_LAST_BRUSH_TIME, DOMAIN, MANUFACTURER
from .coordinator import OcleanCoordinator


class OcleanEntity(CoordinatorEntity[OcleanCoordinator]):
    """Template base class shared by all Oclean entities.

    Consolidates the boilerplate that was duplicated across
    OcleanSensor, OcleanBinarySensor, and OcleanButton:
      - _attr_has_entity_name
      - _attr_unique_id construction
      - _attr_device_info construction
      - a sensible default for ``available``
    """

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: OcleanCoordinator,
        mac: str,
        device_name: str,
        unique_id_suffix: str,
    ) -> None:
        super().__init__(coordinator)
        self._mac = mac
        self._attr_unique_id = f"{mac}_{unique_id_suffix}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, mac)},
            name=device_name,
            manufacturer=MANUFACTURER,
        )

    @property
    def available(self) -> bool:
        """Return True when the coordinator holds any data (including stale)."""
        return self.coordinator.data is not None

    def _session_field_available(self, value: Any) -> bool:
        """Shared availability logic for session-derived sensor fields.

        Returns False when at least one session has been received
        (DATA_LAST_BRUSH_TIME is set) but *value* has never been populated,
        indicating structural unavailability on this device.
        Falls back to stale-data availability when the last poll failed.
        """
        if not self.coordinator.last_update_success:
            return value is not None
        if (
            self.coordinator.data is not None
            and self.coordinator.data.get(DATA_LAST_BRUSH_TIME) is not None
            and value is None
        ):
            return False
        return True
