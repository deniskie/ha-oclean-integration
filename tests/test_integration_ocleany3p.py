"""Integration tests for Oclean X Pro Elite (OCLEANY3P).

OCLEANY3P uses the *B# multi-packet stream protocol where the device signals
session_count > 0 with year_byte = 0x00 in the 0307 response.  The coordinator
then accumulates continuation packets until all session records are complete,
and calls ``parse_y3p_stream_record()`` on each 42-byte chunk.

Y3P stream record layout (42 bytes):
  byte  0:   0x00 – year not stored; inferred from wall clock
  bytes 1-5: month / day / hour / minute / second
  byte  6:   reserved (0x00)
  bytes 7-8: duration, big-endian uint16 (seconds)
  bytes 9-20:  reserved
  bytes 21-28: 8 tooth-area pressure values
  bytes 29-32: reserved
  byte  33:  brushing score (0-100; 0xFF = absent)
  bytes 34-41: padding

These tests use the ``add_y3p_stream_session`` simulator builder which constructs
the three BLE packets that carry one 42-byte record.
"""

from __future__ import annotations

import pytest

from custom_components.oclean_ble.const import (
    DATA_BATTERY,
    DATA_LAST_BRUSH_AREAS,
    DATA_LAST_BRUSH_DURATION,
    DATA_LAST_BRUSH_PRESSURE,
    DATA_LAST_BRUSH_SCORE,
    DATA_LAST_BRUSH_TIME,
    TOOTH_AREA_NAMES,
)
from tests.integration_helpers import make_coordinator, run_poll
from tests.simulator import OcleanDeviceSimulator

_MAC = "AA:BB:CC:DD:EE:F5"


def _coordinator():
    return make_coordinator(_MAC, "Oclean X Pro Elite")


# ---------------------------------------------------------------------------
# TestOcleanY3PStreamSession – Y3P *B# stream protocol
# ---------------------------------------------------------------------------


class TestOcleanY3PStreamSession:
    """OCLEANY3P *B# multi-packet stream: one 42-byte record across 3 BLE packets.

    The OcleanDeviceSimulator.add_y3p_stream_session() method builds the
    correct 0307 header + 2 continuation packets for a single Y3P record.
    """

    @pytest.mark.asyncio
    async def test_session_all_fields_present(self):
        """Y3P stream → duration, score, areas, and pressure all populated."""
        areas = (10, 20, 30, 15, 25, 35, 5, 10)
        client = (
            OcleanDeviceSimulator()
            .with_battery(65)
            .add_y3p_stream_session(
                3,
                11,
                20,
                2,
                23,
                duration=120,
                score=88,
                area_pressures=areas,
            )
            .build_client()
        )
        result = await run_poll(_coordinator(), client)

        assert result[DATA_BATTERY] == 65
        assert result[DATA_LAST_BRUSH_DURATION] == 120
        assert result[DATA_LAST_BRUSH_SCORE] == 88
        assert isinstance(result[DATA_LAST_BRUSH_AREAS], dict)
        assert len(result[DATA_LAST_BRUSH_AREAS]) == 8
        assert result[DATA_LAST_BRUSH_PRESSURE] > 0

    @pytest.mark.asyncio
    async def test_session_timestamp_is_set(self):
        """Y3P stream record produces a non-zero timestamp."""
        client = OcleanDeviceSimulator().add_y3p_stream_session(3, 11, 20, 2, 23, duration=120, score=88).build_client()
        result = await run_poll(_coordinator(), client)
        assert result.get(DATA_LAST_BRUSH_TIME) is not None
        assert result[DATA_LAST_BRUSH_TIME] > 0

    @pytest.mark.asyncio
    async def test_area_values_match_input(self):
        """Area pressures are stored in the canonical zone-name order."""
        areas = (1, 2, 3, 4, 5, 6, 7, 8)
        client = (
            OcleanDeviceSimulator()
            .add_y3p_stream_session(3, 11, 20, 2, 23, duration=90, score=70, area_pressures=areas)
            .build_client()
        )
        result = await run_poll(_coordinator(), client)
        area_dict = result[DATA_LAST_BRUSH_AREAS]
        for i, name in enumerate(TOOTH_AREA_NAMES):
            assert area_dict[name] == areas[i], f"Mismatch for zone {name}"

    @pytest.mark.asyncio
    async def test_pressure_is_rounded_average_of_areas(self):
        """last_brush_pressure is the rounded average of the 8 area values.

        Uses areas where round() and int() differ: sum=7, /8=0.875
          round(0.875) = 1  (correct)
          int(0.875)   = 0  (wrong – would mean floor, not round)
        """
        areas = (7, 0, 0, 0, 0, 0, 0, 0)  # sum=7 / 8 = 0.875 → round=1, int=0
        client = (
            OcleanDeviceSimulator()
            .add_y3p_stream_session(3, 11, 20, 2, 23, duration=90, score=70, area_pressures=areas)
            .build_client()
        )
        result = await run_poll(_coordinator(), client)
        assert result[DATA_LAST_BRUSH_PRESSURE] == 1

    @pytest.mark.asyncio
    async def test_score_0xff_not_set(self):
        """Y3P record with score byte = 0xFF must leave last_brush_score absent."""
        client = (
            OcleanDeviceSimulator().add_y3p_stream_session(3, 11, 20, 2, 23, duration=90, score=0xFF).build_client()
        )
        result = await run_poll(_coordinator(), client)
        assert result.get(DATA_LAST_BRUSH_SCORE) is None

    @pytest.mark.asyncio
    async def test_no_areas_when_all_pressures_zero(self):
        """Y3P record with all area bytes = 0 must leave last_brush_areas absent."""
        client = (
            OcleanDeviceSimulator()
            .add_y3p_stream_session(3, 11, 20, 2, 23, duration=90, score=70, area_pressures=(0,) * 8)
            .build_client()
        )
        result = await run_poll(_coordinator(), client)
        assert result.get(DATA_LAST_BRUSH_AREAS) is None
        assert result.get(DATA_LAST_BRUSH_PRESSURE) is None

    @pytest.mark.asyncio
    async def test_battery_only_when_no_session(self):
        """Poll with no session notifications yields only battery."""
        client = OcleanDeviceSimulator().with_battery(65).build_client()
        result = await run_poll(_coordinator(), client)
        assert result[DATA_BATTERY] == 65
        assert result.get(DATA_LAST_BRUSH_TIME) is None

    @pytest.mark.asyncio
    async def test_incomplete_stream_no_session(self):
        """Only the 0307 header + first continuation (33/42 bytes) → no session emitted.

        The *B# buffer must not flush until the full 42-byte record is received.
        """
        # Build 0307 header for 1 record manually to verify the partial-packet path.
        record = bytearray(42)
        record[0] = 0x00  # year_byte
        record[1] = 3  # month
        record[2] = 11  # day
        record[3] = 20  # hour
        record[7] = 0x00
        record[8] = 0x78  # duration=120

        header = bytearray(20)
        header[0:2] = b"\x03\x07"
        header[2:5] = b"\x2a\x42\x23"
        header[5] = 0x00
        header[6] = 0x01  # count=1
        header[7:20] = record[0:13]

        client = (
            OcleanDeviceSimulator()
            .with_battery(65)
            .add_notification(bytes(header))
            .add_notification(bytes(record[13:33]))  # +20 B → 33/42
            # intentionally omitting the final 9-byte packet
            .build_client()
        )
        result = await run_poll(_coordinator(), client)
        assert result.get(DATA_LAST_BRUSH_TIME) is None
        assert result.get(DATA_LAST_BRUSH_SCORE) is None
