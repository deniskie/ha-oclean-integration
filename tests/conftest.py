"""Shared fixtures for Oclean integration tests.

These tests run WITHOUT a full Home Assistant instance.
A minimal stub of every homeassistant.* module is injected before imports.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock

# ---------------------------------------------------------------------------
# Ensure the custom_components package is importable from this repo layout
# ---------------------------------------------------------------------------
_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))


# ---------------------------------------------------------------------------
# Module stub helper – idempotent (returns existing module if already created)
# ---------------------------------------------------------------------------


def _stub(name: str) -> ModuleType:
    """Return the stub module for *name*, creating it if necessary."""
    if name not in sys.modules:
        mod = ModuleType(name)
        sys.modules[name] = mod
    return sys.modules[name]


def _install_ha_stubs() -> None:
    """Inject lightweight stubs for all homeassistant.* modules we import."""

    # ---- homeassistant root ----
    ha_root = _stub("homeassistant")
    ha_root.__version__ = "2025.1.0"

    # ---- homeassistant.core ----
    core = _stub("homeassistant.core")
    core.HomeAssistant = MagicMock
    core.ServiceCall = MagicMock
    core.callback = lambda f: f
    core.ServiceResponse = dict

    from enum import StrEnum

    class SupportsResponse(StrEnum):
        NONE = "none"
        OPTIONAL = "optional"
        ONLY = "only"

    core.SupportsResponse = SupportsResponse

    # ---- homeassistant.const ----
    from enum import Enum, StrEnum

    const = _stub("homeassistant.const")
    const.__version__ = "2025.1.0"
    const.Platform = Enum("Platform", ["SENSOR", "BINARY_SENSOR", "BUTTON", "NUMBER", "SELECT", "SWITCH"])
    const.PERCENTAGE = "%"
    const.SIGNAL_STRENGTH_DECIBELS_MILLIWATT = "dBm"

    class UnitOfTime:
        SECONDS = "s"
        MINUTES = "min"
        DAYS = "d"

    const.UnitOfTime = UnitOfTime

    class EntityCategory(StrEnum):
        DIAGNOSTIC = "diagnostic"
        CONFIG = "config"

    const.EntityCategory = EntityCategory

    # ---- homeassistant.exceptions ----
    exc = _stub("homeassistant.exceptions")

    class ConfigEntryNotReady(Exception):
        pass

    exc.ConfigEntryNotReady = ConfigEntryNotReady

    class HomeAssistantError(Exception):
        pass

    exc.HomeAssistantError = HomeAssistantError

    # ---- homeassistant.data_entry_flow ----
    daf = _stub("homeassistant.data_entry_flow")
    daf.FlowResult = dict

    # ---- homeassistant.config_entries ----
    ce = _stub("homeassistant.config_entries")

    class ConfigEntry:
        def __init__(self, data=None, options=None, entry_id="test"):
            self.data = data or {}
            self.options = options or {}
            self.entry_id = entry_id
            # Tasks created via async_create_background_task, so tests can await
            # them deterministically instead of racing the event loop.
            self.background_tasks = []

        def add_update_listener(self, cb):
            return lambda: None

        def async_on_unload(self, cb):
            pass

        def async_create_background_task(self, hass, target, name=None, eager_start=True):
            """Stub of HA's entry-scoped background task helper.

            Schedules *target* on the running loop and keeps a reference in
            ``background_tasks``; tests await those to observe the result.
            """
            import asyncio

            task = asyncio.ensure_future(target)
            self.background_tasks.append(task)
            return task

    class _ConfigFlow:
        """Base stub – real flow subclasses this."""

        def __init_subclass__(cls, *, domain=None, **kwargs):
            super().__init_subclass__(**kwargs)

    class _OptionsFlow:
        config_entry = None  # injected by HA; accessed via self.config_entry

    ce.ConfigEntry = ConfigEntry
    ce.ConfigFlow = _ConfigFlow
    ce.OptionsFlow = _OptionsFlow

    # ---- homeassistant.helpers (parent) ----
    _stub("homeassistant.helpers")

    # ---- homeassistant.helpers.selector ----
    sel = _stub("homeassistant.helpers.selector")

    class NumberSelectorMode:
        BOX = "box"
        SLIDER = "slider"

    class NumberSelectorConfig:
        def __init__(self, **kwargs):
            pass

    class NumberSelector:
        def __init__(self, config=None):
            pass

        def __call__(self, value):
            return value

    class TextSelectorConfig:
        def __init__(self, **kwargs):
            pass

    class TextSelector:
        def __init__(self, config=None):
            pass

    sel.NumberSelectorMode = NumberSelectorMode
    sel.NumberSelectorConfig = NumberSelectorConfig
    sel.NumberSelector = NumberSelector

    class TimeSelector:
        def __init__(self, config=None):
            pass

        def __call__(self, value):
            return value

    sel.TextSelectorConfig = TextSelectorConfig
    sel.TextSelector = TextSelector
    sel.TimeSelector = TimeSelector

    # ---- homeassistant.helpers.update_coordinator ----
    uc = _stub("homeassistant.helpers.update_coordinator")

    class UpdateFailed(Exception):
        pass

    class DataUpdateCoordinator:
        def __init__(self, hass, logger, *, name, update_interval):
            self.hass = hass
            self.data = None
            self.last_update_success = True
            self.update_interval = update_interval

        async def async_config_entry_first_refresh(self):
            self.data = await self._async_update_data()

        async def async_refresh(self):
            self.data = await self._async_update_data()

        def __class_getitem__(cls, item):
            return cls

    class CoordinatorEntity:
        def __init__(self, coordinator):
            self.coordinator = coordinator

        def __class_getitem__(cls, item):
            return cls

        async def async_added_to_hass(self):
            """No-op stand-in for HA's coordinator subscription hook."""

        def async_on_remove(self, func):
            """Record the unsubscribe callable the way HA does."""
            self._on_remove_callbacks = getattr(self, "_on_remove_callbacks", [])
            self._on_remove_callbacks.append(func)

    uc.UpdateFailed = UpdateFailed
    uc.DataUpdateCoordinator = DataUpdateCoordinator
    uc.CoordinatorEntity = CoordinatorEntity
    exc.UpdateFailed = UpdateFailed

    # ---- homeassistant.helpers.storage ----
    storage = _stub("homeassistant.helpers.storage")

    class _StoreStub:
        """Minimal async-compatible Store stub (avoids Python 3.14 InvalidSpecError)."""

        def __init__(self, *args, **kwargs):
            pass

        async def async_load(self):
            return None

        async def async_save(self, data):
            pass

    storage.Store = _StoreStub

    # ---- homeassistant.helpers.device_registry ----
    dr = _stub("homeassistant.helpers.device_registry")

    class DeviceInfo(dict):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)

    dr.DeviceInfo = DeviceInfo

    # ---- homeassistant.helpers.entity ----
    he = _stub("homeassistant.helpers.entity")
    from enum import StrEnum as _StrEnum

    class _EntityCategory(_StrEnum):
        CONFIG = "config"
        DIAGNOSTIC = "diagnostic"

    he.EntityCategory = _EntityCategory

    # ---- homeassistant.helpers.entity_platform ----
    ep = _stub("homeassistant.helpers.entity_platform")
    ep.AddEntitiesCallback = object

    # ---- homeassistant.components ----
    comp = _stub("homeassistant.components")

    # ---- homeassistant.components.diagnostics ----
    diag = _stub("homeassistant.components.diagnostics")

    def _redact(data, to_redact):
        if isinstance(data, dict):
            return {k: ("**REDACTED**" if k in to_redact else _redact(v, to_redact)) for k, v in data.items()}
        if isinstance(data, list):
            return [_redact(v, to_redact) for v in data]
        return data

    diag.async_redact_data = _redact
    comp.diagnostics = diag

    # ---- homeassistant.components.bluetooth ----
    bt = _stub("homeassistant.components.bluetooth")
    bt.async_last_service_info = MagicMock(return_value=None)
    bt.async_discovered_service_info = MagicMock(return_value=[])
    # Passive advertisement listening (RSSI sensor). Default: registering is a
    # no-op returning an unsubscribe callable, and no scanner has seen the MAC.
    bt.async_register_callback = MagicMock(return_value=lambda: None)
    bt.async_scanner_devices_by_address = MagicMock(return_value=[])

    class BluetoothCallbackMatcher:
        def __init__(self, address=None, connectable=None):
            self.address = address
            self.connectable = connectable

    class BluetoothScanningMode(Enum):
        PASSIVE = "passive"
        ACTIVE = "active"

    bt.BluetoothCallbackMatcher = BluetoothCallbackMatcher
    bt.BluetoothScanningMode = BluetoothScanningMode

    class BluetoothServiceInfoBleak:
        pass

    bt.BluetoothServiceInfoBleak = BluetoothServiceInfoBleak
    comp.bluetooth = bt

    # ---- homeassistant.components.sensor ----
    sensor = _stub("homeassistant.components.sensor")

    class SensorDeviceClass(Enum):
        BATTERY = "battery"
        VOLTAGE = "voltage"
        DURATION = "duration"
        TIMESTAMP = "timestamp"
        SIGNAL_STRENGTH = "signal_strength"

    class SensorStateClass(Enum):
        MEASUREMENT = "measurement"

    class SensorEntityDescription:
        def __init__(
            self,
            *,
            key,
            name="",
            device_class=None,
            state_class=None,
            native_unit_of_measurement=None,
            suggested_unit_of_measurement=None,
            icon=None,
            entity_category=None,
            entity_registry_enabled_default=True,
            **kwargs,
        ):
            self.key = key
            self.name = name
            self.device_class = device_class
            self.state_class = state_class
            self.native_unit_of_measurement = native_unit_of_measurement
            self.icon = icon
            self.entity_category = entity_category
            self.entity_registry_enabled_default = entity_registry_enabled_default

    class SensorEntity:
        pass

    sensor.SensorDeviceClass = SensorDeviceClass
    sensor.SensorStateClass = SensorStateClass
    sensor.SensorEntityDescription = SensorEntityDescription
    sensor.SensorEntity = SensorEntity

    # ---- homeassistant.components.select ----
    sel = _stub("homeassistant.components.select")

    class SelectEntity:
        _attr_assumed_state = False
        _attr_icon = None
        _attr_entity_category = None
        _attr_translation_key = None
        _attr_options: list[str] = []

        @property
        def options(self) -> list[str]:
            return self._attr_options

        def async_write_ha_state(self) -> None:
            pass

    sel.SelectEntity = SelectEntity

    # ---- homeassistant.components.switch ----
    sw = _stub("homeassistant.components.switch")

    class SwitchEntityDescription:
        def __init__(self, *, key, name="", icon=None, **kwargs):
            self.key = key
            self.name = name
            self.icon = icon

    class SwitchEntity:
        pass

    sw.SwitchEntityDescription = SwitchEntityDescription
    sw.SwitchEntity = SwitchEntity

    # ---- homeassistant.components.button ----
    btn = _stub("homeassistant.components.button")

    class ButtonEntityDescription:
        def __init__(self, *, key, name="", icon=None, **kwargs):
            self.key = key
            self.name = name
            self.icon = icon

    class ButtonEntity:
        pass

    btn.ButtonEntityDescription = ButtonEntityDescription
    btn.ButtonEntity = ButtonEntity

    # ---- homeassistant.components.number ----
    num = _stub("homeassistant.components.number")

    class NumberMode(Enum):
        AUTO = "auto"
        BOX = "box"
        SLIDER = "slider"

    class NumberEntityDescription:
        def __init__(
            self,
            *,
            key,
            name="",
            icon=None,
            native_min_value=None,
            native_max_value=None,
            native_step=None,
            native_unit_of_measurement=None,
            mode=None,
            entity_category=None,
            **kwargs,
        ):
            self.key = key
            self.name = name
            self.icon = icon
            self.native_min_value = native_min_value
            self.native_max_value = native_max_value
            self.native_step = native_step
            self.native_unit_of_measurement = native_unit_of_measurement
            self.mode = mode
            self.entity_category = entity_category

    class NumberEntity:
        pass

    num.NumberMode = NumberMode
    num.NumberEntityDescription = NumberEntityDescription
    num.NumberEntity = NumberEntity

    # ---- homeassistant.components.binary_sensor ----
    bs = _stub("homeassistant.components.binary_sensor")

    class BinarySensorDeviceClass(Enum):
        RUNNING = "running"

    class BinarySensorEntityDescription:
        def __init__(self, *, key, name="", device_class=None, icon=None):
            self.key = key
            self.name = name
            self.device_class = device_class
            self.icon = icon

    class BinarySensorEntity:
        pass

    bs.BinarySensorDeviceClass = BinarySensorDeviceClass
    bs.BinarySensorEntityDescription = BinarySensorEntityDescription
    bs.BinarySensorEntity = BinarySensorEntity

    # ---- bleak (stub if not installed) ----
    if "bleak" not in sys.modules or not hasattr(sys.modules["bleak"], "BleakError"):
        bleak = _stub("bleak")

        class BleakError(Exception):
            pass

        class BleakClient:
            pass

        bleak.BleakError = BleakError
        bleak.BleakClient = BleakClient

    # bleak.backends.device – needed for BLEDevice import
    if "bleak.backends" not in sys.modules:
        _stub("bleak.backends")
    if "bleak.backends.device" not in sys.modules:
        bd = _stub("bleak.backends.device")

        class BLEDevice:
            def __init__(self, address, name=None, details=None, rssi=0, **kwargs):
                self.address = address
                self.name = name
                self.details = details or {}
                self.rssi = rssi

        bd.BLEDevice = BLEDevice

    # ---- bleak_retry_connector ----
    if "bleak_retry_connector" not in sys.modules:
        brc = _stub("bleak_retry_connector")
        brc.establish_connection = AsyncMock()


_install_ha_stubs()
