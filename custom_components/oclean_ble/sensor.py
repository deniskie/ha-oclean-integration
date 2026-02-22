"""Sensor entities for the Oclean Toothbrush integration."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    CONF_DEVICE_NAME,
    CONF_MAC_ADDRESS,
    DATA_BATTERY,
    DATA_BRUSH_HEAD_USAGE,
    DATA_HW_REVISION,
    DATA_LAST_BRUSH_AREAS,
    DATA_LAST_BRUSH_CLEAN,
    DATA_LAST_BRUSH_DURATION,
    DATA_LAST_BRUSH_PRESSURE,
    DATA_LAST_BRUSH_PNUM,
    DATA_LAST_BRUSH_SCORE,
    DATA_LAST_BRUSH_SCHEME_TYPE,
    DATA_LAST_BRUSH_TIME,
    DATA_MODEL_ID,
    DATA_SW_VERSION,
    DOMAIN,
    SCHEME_NAMES,
)
from .coordinator import OcleanCoordinator
from .entity import OcleanEntity

SENSOR_DESCRIPTIONS: tuple[SensorEntityDescription, ...] = (
    SensorEntityDescription(
        key=DATA_BATTERY,
        name="Battery",
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
        icon="mdi:battery",
    ),
    SensorEntityDescription(
        key=DATA_LAST_BRUSH_SCORE,
        name="Last Brush Score",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:star",
        # 0–100 dimensionless
    ),
    SensorEntityDescription(
        key=DATA_LAST_BRUSH_DURATION,
        name="Last Brush Duration",
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        icon="mdi:timer",
    ),
    SensorEntityDescription(
        key=DATA_LAST_BRUSH_CLEAN,
        name="Last Brush Clean",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:tooth",
        # 0–100 dimensionless
    ),
    SensorEntityDescription(
        key=DATA_LAST_BRUSH_PRESSURE,
        name="Last Brush Pressure",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:gauge",
        # 0–255 raw ADC value
    ),
    SensorEntityDescription(
        key=DATA_LAST_BRUSH_TIME,
        name="Last Brush Time",
        device_class=SensorDeviceClass.TIMESTAMP,
        icon="mdi:clock-outline",
    ),
    SensorEntityDescription(
        key=DATA_BRUSH_HEAD_USAGE,
        name="Brush Head Usage",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:toothbrush",
        # Raw wear indicator (blunt_teeth); resets to 0 after brush head replacement.
        # Higher values = more wear. Unit unknown (possibly cumulative ADC value).
    ),
    SensorEntityDescription(
        key=DATA_LAST_BRUSH_SCHEME_TYPE,
        name="Last Brush Scheme Type",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:clipboard-list",
        # Integer 0-8: brush-scheme category. Names are cloud-managed; not mapped here.
    ),
    # Device information (diagnostic) – read from BLE Device Information Service (0x180A)
    SensorEntityDescription(
        key=DATA_SW_VERSION,
        name="Firmware Version",
        icon="mdi:chip",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SensorEntityDescription(
        key=DATA_MODEL_ID,
        name="Model",
        icon="mdi:identifier",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SensorEntityDescription(
        key=DATA_HW_REVISION,
        name="Hardware Revision",
        icon="mdi:wrench",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    # NOTE: DATA_LAST_BRUSH_PNUM is handled by OcleanSchemeSensor below (custom class).
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Oclean sensor entities."""
    coordinator: OcleanCoordinator = hass.data[DOMAIN][entry.entry_id]
    mac = entry.data[CONF_MAC_ADDRESS]
    device_name = entry.data.get(CONF_DEVICE_NAME, "Oclean")

    entities: list[Any] = [
        OcleanSensor(coordinator, description, mac, device_name)
        for description in SENSOR_DESCRIPTIONS
    ]
    entities.append(OcleanBrushAreasSensor(coordinator, mac, device_name))
    entities.append(OcleanSchemeSensor(coordinator, mac, device_name))
    async_add_entities(entities)


class OcleanSensor(OcleanEntity, SensorEntity):
    """A single Oclean sensor entity."""

    def __init__(
        self,
        coordinator: OcleanCoordinator,
        description: SensorEntityDescription,
        mac: str,
        device_name: str,
    ) -> None:
        super().__init__(coordinator, mac, device_name, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        """Return the sensor value, or None if not yet available."""
        if self.coordinator.data is None:
            return None
        value = self.coordinator.data.get(self.entity_description.key)
        if value is None:
            return None

        # Convert unix timestamp to datetime for timestamp sensors
        if self.entity_description.device_class == SensorDeviceClass.TIMESTAMP:
            try:
                return datetime.fromtimestamp(int(value)).astimezone()
            except (ValueError, TypeError, OSError):
                return None

        return value

    @property
    def available(self) -> bool:
        """Return True if coordinator is available or we have stale data."""
        if not self.coordinator.last_update_success:
            # Stay available if we have any cached value
            return (
                self.coordinator.data is not None
                and self.coordinator.data.get(self.entity_description.key) is not None
            )
        return True


class OcleanBrushAreasSensor(OcleanEntity, SensorEntity):
    """Sensor showing how many tooth zones were cleaned in the last session.

    State: number of zones (0-8) with non-zero pressure during the last session.
    Attributes: per-zone pressure dict (zone_name → raw pressure 0-255) from the
      extended 0308 BLE record (bytes 20-27, BrushAreaType order).
    Zone names: upper_left_out/in, lower_left_out/in,
                upper_right_out/in, lower_right_out/in.
    """

    _attr_name = "Last Brush Areas"
    _attr_icon = "mdi:tooth-outline"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self,
        coordinator: OcleanCoordinator,
        mac: str,
        device_name: str,
    ) -> None:
        super().__init__(coordinator, mac, device_name, DATA_LAST_BRUSH_AREAS)

    def _get_areas(self) -> dict[str, int] | None:
        if self.coordinator.data is None:
            return None
        areas = self.coordinator.data.get(DATA_LAST_BRUSH_AREAS)
        return areas if isinstance(areas, dict) else None

    @property
    def native_value(self) -> int | None:
        """Return the number of zones with non-zero pressure."""
        areas = self._get_areas()
        if areas is None:
            return None
        return sum(1 for v in areas.values() if v > 0)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return per-zone pressure values as state attributes."""
        return self._get_areas()

    @property
    def available(self) -> bool:
        """Return True if coordinator is available or we have stale area data."""
        if not self.coordinator.last_update_success:
            return self._get_areas() is not None
        return True


class OcleanSchemeSensor(OcleanEntity, SensorEntity):
    """Sensor showing the brush scheme (programme) used in the last session.

    State: pNum integer (for use in automations / templates).
    Attribute: scheme_name → human-readable English name from SCHEME_NAMES lookup,
      or None if the pNum is unknown (device-family-specific; cloud-managed names).
    """

    _attr_name = "Last Brush Scheme"
    _attr_icon = "mdi:clipboard-list"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self,
        coordinator: OcleanCoordinator,
        mac: str,
        device_name: str,
    ) -> None:
        super().__init__(coordinator, mac, device_name, DATA_LAST_BRUSH_PNUM)

    def _get_pnum(self) -> int | None:
        if self.coordinator.data is None:
            return None
        value = self.coordinator.data.get(DATA_LAST_BRUSH_PNUM)
        return int(value) if value is not None else None

    @property
    def native_value(self) -> int | None:
        """Return the pNum as numeric state."""
        return self._get_pnum()

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return the human-readable scheme name as an attribute."""
        pnum = self._get_pnum()
        if pnum is None:
            return None
        return {"scheme_name": SCHEME_NAMES.get(pnum)}

    @property
    def available(self) -> bool:
        """Return True if coordinator is available or we have stale pNum data."""
        if not self.coordinator.last_update_success:
            return self._get_pnum() is not None
        return True
