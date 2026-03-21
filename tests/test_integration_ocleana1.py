"""Integration tests for Oclean Air 1 (OCLEANA1).

OCLEANA1 cannot subscribe to BLE notifications on READ_NOTIFY_CHAR_UUID (fbb86)
because that characteristic has no CCCD descriptor.  The coordinator detects this
at runtime: if fbb86 is not in the subscribed set after _subscribe_notifications(),
it falls back to a direct GATT read of that characteristic.

Real device behaviour (logs/20260313_#7_bato2000_oclean_ble.log):
  - start_notify(fbb86, …)  → BleakError (no CCCD)
  - coordinator calls _read_response_char_fallback()
  - read_gatt_char(READ_NOTIFY_CHAR_UUID) → b'' (empty, device cleared the value)
  - battery = 0x35 = 53 from direct GATT read of 0x2A19

Protocol note:  OCLEANA1 is classified as Legacy once the model_id is known;
in tests the coordinator starts as UNKNOWN but the read-fallback is triggered by
the BleakError on fbb86 regardless of protocol, because UNKNOWN still tries to
subscribe to fbb86 and will fail the same way.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from custom_components.oclean_ble.const import (
    DATA_BATTERY,
    DATA_LAST_BRUSH_DURATION,
    DATA_LAST_BRUSH_SCORE,
    DATA_LAST_BRUSH_TIME,
    READ_NOTIFY_CHAR_UUID,
)
from tests.integration_helpers import make_coordinator, run_poll
from tests.simulator import OcleanDeviceSimulator

_MAC = "70:28:45:5F:AC:C1"


def _coordinator():
    return make_coordinator(_MAC, "Oclean Air 1")


# ---------------------------------------------------------------------------
# TestOcleanA1ReadFallback
# ---------------------------------------------------------------------------


class TestOcleanA1ReadFallback:
    """OCLEANA1: fbb86 subscribe fails → direct GATT READ fallback is triggered.

    When READ_NOTIFY_CHAR_UUID (fbb86) cannot be subscribed, the coordinator
    must fall back to reading the characteristic value directly.  This class
    tests the complete poll pipeline for this device type.
    """

    @pytest.mark.asyncio
    async def test_poll_completes_without_crash(self):
        """Poll must complete (return dict) even when fbb86 subscribe fails."""
        from bleak import BleakError

        client = (
            OcleanDeviceSimulator()
            .with_battery(53)
            .with_notify_errors({READ_NOTIFY_CHAR_UUID: BleakError("no CCCD")})
            .with_read_char_responses({READ_NOTIFY_CHAR_UUID: b""})
            .build_client()
        )
        result = await run_poll(_coordinator(), client)
        assert isinstance(result, dict)

    @pytest.mark.asyncio
    async def test_battery_from_gatt_read(self):
        """Battery must be read from 0x2A19 GATT char when 0303 notification is missed.

        Because fbb86 is not subscribed, the 0303 STATE response is never received
        via notification.  Battery comes from the direct read_gatt_char(BATTERY_CHAR_UUID).
        """
        from bleak import BleakError

        client = (
            OcleanDeviceSimulator()
            .with_battery(53)
            .with_notify_errors({READ_NOTIFY_CHAR_UUID: BleakError("no CCCD")})
            .with_read_char_responses({READ_NOTIFY_CHAR_UUID: b""})
            .build_client()
        )
        result = await run_poll(_coordinator(), client)
        assert result[DATA_BATTERY] == 53

    @pytest.mark.asyncio
    async def test_no_session_when_fallback_returns_empty(self):
        """Empty READ fallback response → no session fields in result.

        This mirrors the real OCLEANA1 log where the device returned b'' from fbb86.
        """
        from bleak import BleakError

        client = (
            OcleanDeviceSimulator()
            .with_battery(53)
            .with_notify_errors({READ_NOTIFY_CHAR_UUID: BleakError("no CCCD")})
            .with_read_char_responses({READ_NOTIFY_CHAR_UUID: b""})
            .build_client()
        )
        result = await run_poll(_coordinator(), client)
        assert result.get(DATA_LAST_BRUSH_TIME) is None
        assert result.get(DATA_LAST_BRUSH_SCORE) is None
        assert result.get(DATA_LAST_BRUSH_DURATION) is None

    @pytest.mark.asyncio
    async def test_state_parsed_when_fallback_returns_0303_bytes(self):
        """If fbb86 holds a valid 0303 response, battery is set from that notification.

        Some device revisions may store the last notification as the readable value.
        The coordinator must parse the bytes received via READ exactly like a push
        notification.

        Execution order is deterministic: _read_response_char_fallback() runs before
        _read_battery_and_unsubscribe(), which skips the GATT read once DATA_BATTERY
        is already in collected.  Battery therefore comes from the 0303 parse (40),
        not from the GATT read (0).
        """
        from bleak import BleakError

        # 0303 STATE: is_brushing=False, battery=40
        state_bytes = bytes.fromhex("0303020e46280200")

        client = (
            OcleanDeviceSimulator()
            .with_battery(0)  # GATT read would return 0 – skipped because 0303 sets battery first
            .with_notify_errors({READ_NOTIFY_CHAR_UUID: BleakError("no CCCD")})
            .with_read_char_responses({READ_NOTIFY_CHAR_UUID: state_bytes})
            .build_client()
        )
        result = await run_poll(_coordinator(), client)
        assert result[DATA_BATTERY] == 40

    @pytest.mark.asyncio
    async def test_read_fallback_exception_handled_gracefully(self):
        """If read_gatt_char(fbb86) raises, the poll must still complete.

        Double-failure scenario: fbb86 subscribe fails (no CCCD) AND the
        subsequent direct read also raises.  The coordinator catches the
        exception and the poll finishes normally; battery comes from 0x2A19.
        """
        from bleak import BleakError

        client = (
            OcleanDeviceSimulator()
            .with_battery(53)
            .with_notify_errors({READ_NOTIFY_CHAR_UUID: BleakError("no CCCD")})
            .build_client()
        )

        async def _raising_read(uuid: str) -> bytearray:
            if uuid == READ_NOTIFY_CHAR_UUID:
                raise BleakError("read failed too")
            return bytearray([53])

        client.read_gatt_char = AsyncMock(side_effect=_raising_read)

        result = await run_poll(_coordinator(), client)
        assert isinstance(result, dict)
        assert result[DATA_BATTERY] == 53

    @pytest.mark.asyncio
    async def test_read_fallback_not_triggered_when_fbb86_subscribed(self):
        """When fbb86 subscribes successfully, the READ fallback must NOT be called.

        Validates that read_gatt_char(fbb86) is only called when subscription failed.
        """
        fbb86_reads: list[str] = []

        async def _tracking_read(uuid: str) -> bytearray:
            if uuid == READ_NOTIFY_CHAR_UUID:
                fbb86_reads.append(uuid)
            return bytearray([75])

        client = OcleanDeviceSimulator().with_battery(75).build_client()
        # Patch read_gatt_char on the already-built client to track calls
        client.read_gatt_char = AsyncMock(side_effect=_tracking_read)

        await run_poll(_coordinator(), client)

        assert fbb86_reads == [], "read_gatt_char(fbb86) must NOT be called when subscription succeeded"
