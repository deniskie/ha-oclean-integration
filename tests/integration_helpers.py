"""Shared helpers for per-model integration tests.

Provides factory functions and a poll runner used by all three per-model
integration test modules (test_integration_ocleana1, test_integration_ocleany3m,
test_integration_ocleany3p).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from custom_components.oclean_ble.coordinator import OcleanCoordinator


def make_coordinator(mac: str, name: str) -> OcleanCoordinator:
    """Create a minimal OcleanCoordinator with HA storage pre-loaded."""
    hass = MagicMock()
    hass.data = {}
    coord = OcleanCoordinator(hass, mac, name, 300)
    coord._store_loaded = True
    return coord


def make_service_info(mac: str):
    """Create a minimal BluetoothServiceInfo mock for the given MAC."""
    device = MagicMock()
    device.address = mac
    si = MagicMock()
    si.device = device
    return si


async def run_poll(coordinator: OcleanCoordinator, client: AsyncMock) -> dict:
    """Run coordinator._poll_device() with all external dependencies mocked.

    Patches:
      - bluetooth.async_last_service_info → minimal service-info for coordinator MAC
      - establish_connection               → returns the provided client mock
      - asyncio.sleep                      → no-op (speeds up tests)
      - coordinator._paginate_sessions     → no-op (unit-tested separately)
      - import_new_sessions                → returns 0
    """
    with (
        patch("custom_components.oclean_ble.coordinator.bluetooth") as bt_mock,
        patch(
            "custom_components.oclean_ble.coordinator.establish_connection",
            new_callable=AsyncMock,
            return_value=client,
        ),
        patch(
            "custom_components.oclean_ble.coordinator.asyncio.sleep",
            new_callable=AsyncMock,
        ),
        patch.object(coordinator, "_paginate_sessions", new_callable=AsyncMock),
        patch(
            "custom_components.oclean_ble.coordinator.import_new_sessions",
            new_callable=AsyncMock,
            return_value=0,
        ),
    ):
        bt_mock.async_last_service_info.return_value = make_service_info(coordinator._mac)
        return await coordinator._poll_device()
