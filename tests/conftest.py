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
    _stub("homeassistant")

    # ---- homeassistant.core ----
    core = _stub("homeassistant.core")
    core.HomeAssistant = MagicMock
    core.callback = lambda f: f

    # ---- homeassistant.const ----
    from enum import Enum
    const = _stub("homeassistant.const")
    const.Platform = Enum("Platform", ["SENSOR", "BINARY_SENSOR", "BUTTON"])
    const.PERCENTAGE = "%"

    class UnitOfTime:
        SECONDS = "s"
    const.UnitOfTime = UnitOfTime

    # ---- homeassistant.exceptions ----
    exc = _stub("homeassistant.exceptions")

    class ConfigEntryNotReady(Exception):
        pass

    exc.ConfigEntryNotReady = ConfigEntryNotReady

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

        def add_update_listener(self, cb):
            return lambda: None

        def async_on_unload(self, cb):
            pass

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

    # ---- homeassistant.helpers.update_coordinator ----
    uc = _stub("homeassistant.helpers.update_coordinator")

    class UpdateFailed(Exception):
        pass

    class DataUpdateCoordinator:
        def __init__(self, hass, logger, *, name, update_interval):
            self.hass = hass
            self.data = None
            self.last_update_success = True

        async def async_config_entry_first_refresh(self):
            self.data = await self._async_update_data()

        def __class_getitem__(cls, item):
            return cls

    class CoordinatorEntity:
        def __init__(self, coordinator):
            self.coordinator = coordinator

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

    # ---- homeassistant.helpers.entity_platform ----
    ep = _stub("homeassistant.helpers.entity_platform")
    ep.AddEntitiesCallback = object

    # ---- homeassistant.components ----
    comp = _stub("homeassistant.components")

    # ---- homeassistant.components.bluetooth ----
    bt = _stub("homeassistant.components.bluetooth")
    bt.async_last_service_info = MagicMock(return_value=None)
    bt.async_discovered_service_info = MagicMock(return_value=[])

    class BluetoothServiceInfoBleak:
        pass

    bt.BluetoothServiceInfoBleak = BluetoothServiceInfoBleak
    comp.bluetooth = bt

    # ---- homeassistant.components.sensor ----
    sensor = _stub("homeassistant.components.sensor")

    class SensorDeviceClass(Enum):
        BATTERY = "battery"
        DURATION = "duration"
        TIMESTAMP = "timestamp"

    class SensorStateClass(Enum):
        MEASUREMENT = "measurement"

    class SensorEntityDescription:
        def __init__(self, *, key, name="", device_class=None, state_class=None,
                     native_unit_of_measurement=None, icon=None):
            self.key = key
            self.name = name
            self.device_class = device_class
            self.state_class = state_class
            self.native_unit_of_measurement = native_unit_of_measurement
            self.icon = icon

    class SensorEntity:
        pass

    sensor.SensorDeviceClass = SensorDeviceClass
    sensor.SensorStateClass = SensorStateClass
    sensor.SensorEntityDescription = SensorEntityDescription
    sensor.SensorEntity = SensorEntity

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
