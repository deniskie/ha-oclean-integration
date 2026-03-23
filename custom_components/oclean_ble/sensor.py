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
    DATA_BRUSH_HEAD_DAYS,
    DATA_BRUSH_HEAD_USAGE,
    DATA_BRUSH_MODE,
    DATA_HW_REVISION,
    DATA_LAST_BRUSH_AREAS,
    DATA_LAST_BRUSH_DURATION,
    DATA_LAST_BRUSH_GESTURE_ARRAY,
    DATA_LAST_BRUSH_GESTURE_CODE,
    DATA_LAST_BRUSH_PNUM,
    DATA_LAST_BRUSH_POWER_ARRAY,
    DATA_LAST_BRUSH_PRESSURE,
    DATA_LAST_BRUSH_SCORE,
    DATA_LAST_BRUSH_TIME,
    DATA_LAST_POLL,
    DATA_MODEL_ID,
    DATA_SW_VERSION,
    DOMAIN,
    SCHEME_NAMES,
    TOOTH_AREA_NAMES,
)
from .coordinator import OcleanCoordinator
from .entity import OcleanEntity


def _get_areas(coordinator_data: dict | None) -> dict[str, int] | None:
    """Return the last_brush_areas dict, or None if unavailable or wrong type."""
    if coordinator_data is None:
        return None
    areas = coordinator_data.get(DATA_LAST_BRUSH_AREAS)
    return areas if isinstance(areas, dict) else None


# Session-derived keys: only populated from parsed brush session notifications.
# After the first session is received (DATA_LAST_BRUSH_TIME is set), any of
# these keys that remain None are structurally unsupported by the device protocol.
_SESSION_DERIVED_KEYS: frozenset[str] = frozenset(
    {
        DATA_LAST_BRUSH_DURATION,
        DATA_LAST_BRUSH_PRESSURE,
        DATA_LAST_BRUSH_AREAS,
        DATA_LAST_BRUSH_PNUM,
    }
)


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
        suggested_unit_of_measurement=UnitOfTime.MINUTES,
        icon="mdi:timer",
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
        # headUsedTimes from 0302 response: number of brushing sessions since last reset.
    ),
    SensorEntityDescription(
        key=DATA_BRUSH_HEAD_DAYS,
        name="Brush Head Days",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement="d",
        icon="mdi:calendar-sync",
        # headUsedDays from 0302 response: calendar days since last brush-head reset.
    ),
    # Device settings – read from 0302 device-settings response
    SensorEntityDescription(
        key=DATA_BRUSH_MODE,
        name="Brush Mode",
        icon="mdi:tune",
        state_class=SensorStateClass.MEASUREMENT,
    ),
    # Device information (diagnostic) – read from BLE Device Information Service (0x180A)
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
    SensorEntityDescription(
        key=DATA_SW_VERSION,
        name="Firmware Version",
        icon="mdi:chip",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    # NOTE: MAC Address is handled by OcleanMacSensor below (needs self._mac).
    SensorEntityDescription(
        key=DATA_LAST_POLL,
        name="Last Poll",
        device_class=SensorDeviceClass.TIMESTAMP,
        icon="mdi:clock-check-outline",
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
        OcleanSensor(coordinator, description, mac, device_name) for description in SENSOR_DESCRIPTIONS
    ]
    entities.append(OcleanBrushAreasSensor(coordinator, mac, device_name))
    entities.append(OcleanSchemeSensor(coordinator, mac, device_name))
    entities.extend(OcleanToothAreaSensor(coordinator, mac, device_name, zone_name) for zone_name in TOOTH_AREA_NAMES)
    entities.append(OcleanGestureCodeSensor(coordinator, mac, device_name))
    entities.append(OcleanGestureArraySensor(coordinator, mac, device_name))
    entities.append(OcleanPowerArraySensor(coordinator, mac, device_name))
    entities.append(OcleanMacSensor(coordinator, mac, device_name))
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
        data = self.coordinator.data
        if not self.coordinator.last_update_success:
            # Stay available if we have any cached value
            return data is not None and data.get(self.entity_description.key) is not None
        # If the device has reported at least one session but this session-derived
        # field is still None, the device protocol does not support it.
        return not (
            self.entity_description.key in _SESSION_DERIVED_KEYS
            and data is not None
            and data.get(DATA_LAST_BRUSH_TIME) is not None
            and data.get(self.entity_description.key) is None
        )


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

    @property
    def native_value(self) -> int | None:
        """Return the number of zones with non-zero pressure."""
        areas = _get_areas(self.coordinator.data)
        if areas is None:
            return None
        return sum(1 for v in areas.values() if v > 0)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return per-zone pressure values as state attributes."""
        return _get_areas(self.coordinator.data)

    @property
    def available(self) -> bool:
        """Return True if coordinator is available or we have stale area data."""
        return self._session_field_available(_get_areas(self.coordinator.data))


class OcleanSchemeSensor(OcleanEntity, SensorEntity):
    """Sensor showing the brush scheme (programme) used in the last session.

    State: human-readable scheme name from SCHEME_NAMES, or the pNum as string
    if the programme ID is not in the lookup table.
    """

    _attr_name = "Last Brush Scheme Type"
    _attr_icon = "mdi:clipboard-list"

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
    def native_value(self) -> str | None:
        """Return scheme name, or pNum as string if not in lookup table."""
        pnum = self._get_pnum()
        if pnum is None:
            return None
        return SCHEME_NAMES.get(pnum, str(pnum))

    @property
    def available(self) -> bool:
        """Return True if coordinator is available or we have stale pNum data."""
        return self._session_field_available(self._get_pnum())


class OcleanToothAreaSensor(OcleanEntity, SensorEntity):
    """Individual sensor for one tooth-zone pressure value from the last session.

    One instance is created per entry in TOOTH_AREA_NAMES (8 total).
    State: raw pressure value 0-255 for that zone (0 = not cleaned / no data).
    Source: last_brush_areas dict populated from 2604 BLE notifications.
    """

    _attr_icon = "mdi:tooth"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self,
        coordinator: OcleanCoordinator,
        mac: str,
        device_name: str,
        zone_name: str,
    ) -> None:
        super().__init__(coordinator, mac, device_name, f"tooth_area_{zone_name}")
        self._zone_name = zone_name
        self._attr_name = "Tooth Area " + zone_name.replace("_", " ").title()

    @property
    def native_value(self) -> int | None:
        """Return the pressure value for this zone, or None if unavailable."""
        areas = _get_areas(self.coordinator.data)
        if areas is None:
            return None
        return areas.get(self._zone_name)

    @property
    def available(self) -> bool:
        """Return True if coordinator is available or we have stale area data."""
        return self._session_field_available(_get_areas(self.coordinator.data))


class OcleanGestureCodeSensor(OcleanEntity, SensorEntity):
    """Sensor for the gestureCode byte from 42-byte 0307 session records.

    gestureCode = byte 14 of the 42-byte record (APK: iBytesToIntBe13 / m14b component 3).
    Raw value 0-255; exact semantics TBD pending empirical analysis.
    """

    _attr_name = "Last Brush Gesture Code"
    _attr_icon = "mdi:gesture"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: OcleanCoordinator, mac: str, device_name: str) -> None:
        super().__init__(coordinator, mac, device_name, DATA_LAST_BRUSH_GESTURE_CODE)

    @property
    def native_value(self) -> int | None:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.get(DATA_LAST_BRUSH_GESTURE_CODE)

    @property
    def available(self) -> bool:
        return self._session_field_available(self.native_value)


class OcleanGestureArraySensor(OcleanEntity, SensorEntity):
    """Sensor exposing the full 13-element gestureArray from 42-byte 0307 records.

    gestureArray = bytes 18-30 of the 42-byte record (APK: C3385w0_fallback).
    State: number of non-zero elements (0-13).
    Attributes: gesture_0 … gesture_12 with the raw per-zone motion values.
    Note: elements [3:11] (bytes 21-28) overlap with the area-pressure sensor.
    """

    _attr_name = "Last Brush Gesture Array"
    _attr_icon = "mdi:chart-bar"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: OcleanCoordinator, mac: str, device_name: str) -> None:
        super().__init__(coordinator, mac, device_name, DATA_LAST_BRUSH_GESTURE_ARRAY)

    def _get_array(self) -> list[int] | None:
        if self.coordinator.data is None:
            return None
        val = self.coordinator.data.get(DATA_LAST_BRUSH_GESTURE_ARRAY)
        return val if isinstance(val, list) else None

    @property
    def native_value(self) -> int | None:
        arr = self._get_array()
        return sum(1 for v in arr if v > 0) if arr is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        arr = self._get_array()
        if arr is None:
            return None
        return {f"gesture_{i}": v for i, v in enumerate(arr)}

    @property
    def available(self) -> bool:
        return self._session_field_available(self._get_array())


class OcleanPowerArraySensor(OcleanEntity, SensorEntity):
    """Sensor exposing the 12-element powerArray from 42-byte 0307 records.

    powerArray = 12 × 2-bit nibbles extracted from bytes 30-32 (APK: a.b.a / m13a).
    Each value is 0-3 (power intensity level for one zone).
    State: average power level (float 0.0-3.0).
    Attributes: power_0 … power_11 with individual nibble values.
    """

    _attr_name = "Last Brush Power Array"
    _attr_icon = "mdi:lightning-bolt"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: OcleanCoordinator, mac: str, device_name: str) -> None:
        super().__init__(coordinator, mac, device_name, DATA_LAST_BRUSH_POWER_ARRAY)

    def _get_array(self) -> list[int] | None:
        if self.coordinator.data is None:
            return None
        val = self.coordinator.data.get(DATA_LAST_BRUSH_POWER_ARRAY)
        return val if isinstance(val, list) else None

    @property
    def native_value(self) -> float | None:
        arr = self._get_array()
        if arr is None or len(arr) == 0:
            return None
        return round(sum(arr) / len(arr), 2)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        arr = self._get_array()
        if arr is None:
            return None
        return {f"power_{i}": v for i, v in enumerate(arr)}

    @property
    def available(self) -> bool:
        return self._session_field_available(self._get_array())


class OcleanMacSensor(OcleanEntity, SensorEntity):
    """Diagnostic sensor exposing the device Bluetooth MAC address."""

    _attr_name = "MAC Address"
    _attr_icon = "mdi:bluetooth"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: OcleanCoordinator, mac: str, device_name: str) -> None:
        super().__init__(coordinator, mac, device_name, "mac_address")

    @property
    def native_value(self) -> str:
        return self._mac

    @property
    def available(self) -> bool:
        return True
