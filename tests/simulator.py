"""BLE device simulator for Oclean integration tests.

Provides ``OcleanDeviceSimulator``: a helper that builds realistic BleakClient
mocks which fire BLE notification sequences, enabling end-to-end coordinator
tests without physical hardware.

Usage (builder pattern)::

    from tests.simulator import OcleanDeviceSimulator

    client = (
        OcleanDeviceSimulator()
        .with_battery(82)
        .add_0307_session(2026, 2, 21, 15, 42, 19, pnum=77, duration=150)
        .add_brush_areas((24, 24, 26, 26, 7, 16, 7, 16))
        .add_score(95)
        .build_client()
    )
"""
from __future__ import annotations

import struct
from unittest.mock import AsyncMock

# ---------------------------------------------------------------------------
# Payload builder functions
# Each function returns a complete BLE notification (prefix included).
# Byte offsets are verified against real device captures and the APK parser.
# ---------------------------------------------------------------------------


def build_0307_payload(
    year: int,
    month: int,
    day: int,
    hour: int,
    minute: int,
    second: int,
    pnum: int,
    duration: int,
    valid_duration: int = 0,
) -> bytes:
    """Build a full 20-byte 0307 Type-1 session push notification (Oclean X).

    Layout (confirmed against real captures, see test_real_data.py)::

      [0:2]   prefix 03 07
      [2:5]   magic 2a 42 23  ("*B#")
      [5:7]   session count 00 00  (inline-push mode)
      [7]     year - 2000
      [8]     month (1-12)
      [9]     day   (1-31)
      [10]    hour  (0-23)
      [11]    minute (0-59)
      [12]    second (0-59)
      [13]    pNum  (brush-scheme ID)
      [14:16] duration (BE uint16, seconds)
      [16:18] validDuration (BE uint16, seconds)
      [18:20] 00 00  (pressure bytes, not parsed by the parser)
    """
    data = bytearray(20)
    data[0:2] = b"\x03\x07"
    data[2:5] = b"\x2a\x42\x23"
    data[5:7] = b"\x00\x00"
    data[7] = year - 2000
    data[8] = month
    data[9] = day
    data[10] = hour
    data[11] = minute
    data[12] = second
    data[13] = pnum & 0xFF
    struct.pack_into(">H", data, 14, duration)
    struct.pack_into(">H", data, 16, valid_duration)
    return bytes(data)


def build_0308_extended_payload(
    year: int,
    month: int,
    day: int,
    hour: int,
    minute: int,
    second: int,
    pnum: int,
    duration: int,
    score: int,
    area_pressures: tuple = (10, 10, 10, 10, 10, 10, 10, 10),
    tz_offset_quarters: int = 0,
    valid_duration: int | None = None,
) -> bytes:
    """Build a full 36-byte 0308 extended running-data notification (Oclean X Pro).

    Layout (2-byte prefix + 34-byte payload = 36 bytes total)::

      [0:2]   prefix 03 08
      [2]     0x00  (high byte of BE record_length, always 0 for BLE MTU < 256)
      [3]     0x20  (= 32, record_length LSB; activates the extended parser path)
      [4]     year - 2000
      [5]     month
      [6]     day
      [7]     hour
      [8]     minute
      [9]     second
      [10]    pNum
      [11:13] duration (BE uint16, seconds)
      [13:15] validDuration (BE uint16, seconds)
      [15:20] 5 intermediate pressure zones (not sensor-mapped; left at 0)
      [20]    RESERVED
      [21]    tz_offset_quarters (signed int8, quarter-hours from UTC)
      [22:30] 8 tooth-area pressure bytes (BrushAreaType order)
      [30]    score (0-100)
      [31:36] schemeType, busBrushing, crossNumber, extra (unused in tests)
    """
    if valid_duration is None:
        valid_duration = duration
    notif = bytearray(36)
    notif[0:2] = b"\x03\x08"
    notif[2] = 0x00
    notif[3] = 0x20
    notif[4] = year - 2000
    notif[5] = month
    notif[6] = day
    notif[7] = hour
    notif[8] = minute
    notif[9] = second
    notif[10] = pnum & 0xFF
    struct.pack_into(">H", notif, 11, duration)
    struct.pack_into(">H", notif, 13, valid_duration)
    notif[21] = tz_offset_quarters & 0xFF
    for i, p in enumerate(area_pressures[:8]):
        notif[22 + i] = int(p) & 0xFF
    notif[30] = max(0, min(100, score))
    return bytes(notif)


def build_0000_score_payload(score: int) -> bytes:
    """Build a 0000 score-push notification.

    Only ``score`` at payload[0] is read by the parser.  score=0xFF means
    "no data" and the parser returns an empty dict.
    """
    data = bytearray(20)
    data[0:2] = b"\x00\x00"
    data[2] = score & 0xFF
    for i in range(3, 10):
        data[i] = 0xFF
    return bytes(data)


def build_5a00_payload(
    year: int,
    month: int,
    day: int,
    hour: int,
    minute: int,
    second: int,
    duration: int,
) -> bytes:
    """Build a 5a00 session-meta push notification (20 bytes).

    Layout::

      [0:2]   prefix 5a 00
      [2:9]   7 × 0xFF  (empty session slots)
      [9]     year - 2000
      [10]    month
      [11]    day
      [12]    hour
      [13]    minute
      [14]    second
      [15]    unknown (0x00)
      [16]    unknown (0x00)
      [17]    duration in seconds (byte, capped at 0xFF)
      [18]    unknown (0x00)
      [19]    duration duplicate
    """
    data = bytearray(20)
    data[0] = 0x5A
    data[1] = 0x00
    for i in range(2, 9):
        data[i] = 0xFF
    data[9] = year - 2000
    data[10] = month
    data[11] = day
    data[12] = hour
    data[13] = minute
    data[14] = second
    data[17] = duration & 0xFF
    data[19] = duration & 0xFF
    return bytes(data)


def build_2604_payload(
    area_pressures: tuple = (24, 24, 26, 26, 7, 16, 7, 16),
) -> bytes:
    """Build a 2604 brush-areas push notification (20 bytes).

    Layout::

      [0:2]   prefix 26 04
      [2]     unknown (0x39 observed in real captures)
      [3:6]   unknown (0x000000)
      [6]     unknown (0x0F observed)
      [7]     unknown (0x00)
      [8:16]  8 tooth-area pressure bytes (BrushAreaType order)
      [16:20] additional zone data (not parsed)
    """
    data = bytearray(20)
    data[0] = 0x26
    data[1] = 0x04
    data[2] = 0x39
    data[6] = 0x0F
    for i, p in enumerate(area_pressures[:8]):
        data[8 + i] = int(p) & 0xFF
    return bytes(data)


# ---------------------------------------------------------------------------
# OcleanDeviceSimulator
# ---------------------------------------------------------------------------


class OcleanDeviceSimulator:
    """Simulate an Oclean BLE device as a BleakClient mock.

    Build a notification sequence with the builder methods, then call
    ``build_client()`` to get a fully configured mock.  The mock fires all
    accumulated notifications synchronously when the coordinator subscribes
    to the first GATT notify characteristic.

    Example – Oclean X session with score and area data::

        client = (
            OcleanDeviceSimulator()
            .with_battery(82)
            .add_0307_session(2026, 2, 21, 15, 42, 19, pnum=77, duration=150)
            .add_brush_areas((24, 24, 26, 26, 7, 16, 7, 16))
            .add_score(95)
            .build_client()
        )

    Example – Oclean X Pro extended session::

        client = (
            OcleanDeviceSimulator()
            .with_battery(65)
            .add_0308_extended_session(
                2026, 2, 24, 7, 30, 0,
                pnum=42, duration=120, score=88,
                area_pressures=(15, 20, 10, 12, 18, 25, 30, 8),
            )
            .build_client()
        )
    """

    def __init__(self) -> None:
        self._battery: int = 75
        self._notifications: list[bytes] = []
        self._notify_errors: dict[str, type[Exception] | Exception] = {}

    def with_battery(self, battery: int) -> OcleanDeviceSimulator:
        """Set the battery level returned by the GATT Battery Characteristic read."""
        self._battery = battery
        return self

    def with_notify_errors(
        self,
        errors: dict[str, type[Exception] | Exception],
    ) -> OcleanDeviceSimulator:
        """Make start_notify raise for specific characteristic UUIDs (simulates OCLEANA1)."""
        self._notify_errors = errors
        return self

    def add_notification(self, data: bytes) -> OcleanDeviceSimulator:
        """Add a raw notification payload (for custom / experimental types)."""
        self._notifications.append(data)
        return self

    def add_0307_session(
        self,
        year: int,
        month: int,
        day: int,
        hour: int,
        minute: int,
        second: int,
        *,
        pnum: int,
        duration: int,
        valid_duration: int = 0,
    ) -> OcleanDeviceSimulator:
        """Add a Type-1 (0307) session push notification (Oclean X / OCLEANY3M)."""
        self._notifications.append(
            build_0307_payload(
                year, month, day, hour, minute, second, pnum, duration, valid_duration
            )
        )
        return self

    def add_0308_extended_session(
        self,
        year: int,
        month: int,
        day: int,
        hour: int,
        minute: int,
        second: int,
        *,
        pnum: int,
        duration: int,
        score: int,
        area_pressures: tuple = (10, 10, 10, 10, 10, 10, 10, 10),
        tz_offset_quarters: int = 0,
    ) -> OcleanDeviceSimulator:
        """Add an extended (0308) session notification (Oclean X Pro / OCLEANY3)."""
        self._notifications.append(
            build_0308_extended_payload(
                year, month, day, hour, minute, second,
                pnum, duration, score, area_pressures, tz_offset_quarters,
            )
        )
        return self

    def add_score(self, score: int) -> OcleanDeviceSimulator:
        """Add a 0000 score-push notification.  score=0xFF means "no data"."""
        self._notifications.append(build_0000_score_payload(score))
        return self

    def add_brush_areas(
        self,
        area_pressures: tuple = (24, 24, 26, 26, 7, 16, 7, 16),
    ) -> OcleanDeviceSimulator:
        """Add a 2604 brush-areas notification with per-zone pressure values."""
        self._notifications.append(build_2604_payload(area_pressures))
        return self

    def add_session_meta(
        self,
        year: int,
        month: int,
        day: int,
        hour: int,
        minute: int,
        second: int,
        *,
        duration: int,
    ) -> OcleanDeviceSimulator:
        """Add a 5a00 session-meta push notification."""
        self._notifications.append(
            build_5a00_payload(year, month, day, hour, minute, second, duration)
        )
        return self

    def build_client(self) -> AsyncMock:
        """Build a BleakClient mock that fires the accumulated notifications.

        All notifications are delivered synchronously on the **first**
        ``start_notify`` call, which simulates the device's BLE notification
        burst after the coordinator sends its query commands.
        """
        notifications = list(self._notifications)
        battery = self._battery
        notify_errors = dict(self._notify_errors)

        client = AsyncMock()
        client.is_connected = True
        client.write_gatt_char = AsyncMock()
        client.stop_notify = AsyncMock()
        client.disconnect = AsyncMock()
        client.read_gatt_char = AsyncMock(return_value=bytearray([battery]))

        call_count = [0]

        async def _start_notify(uuid: str, handler) -> None:
            if uuid in notify_errors:
                err = notify_errors[uuid]
                raise err() if isinstance(err, type) else err
            call_count[0] += 1
            if call_count[0] == 1:
                for payload in notifications:
                    handler(None, bytearray(payload))

        client.start_notify = AsyncMock(side_effect=_start_notify)
        return client
