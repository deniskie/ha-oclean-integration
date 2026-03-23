"""Integration tests for Oclean X / Oclean X Pro Elite (OCLEANY3M / OCLEANY3MH).

These tests feed real BLE notification bytes captured from a live OCLEANY3MH device
(log: logs/20260311_#19_NicGray78_v2.log) through the complete coordinator pipeline
and assert on the resulting OcleanDeviceData field values.

Device model:  OCLEANY3MH (Oclean X)
Protocol:      Type-1 – 0307 *B# multi-packet reassembly
Session path:  0307 header + continuation packets → parse_t1_c3385w0_record()
               (not 2604 / 0000 push notifications)

Note: last_brush_areas and last_brush_pressure are NOT extracted from the *B# record.
OCLEANY3M records only contain 5 tooth-zone bytes (11-15); all 8 areas come
exclusively from the 2604 enrichment push (_parse_brush_areas_t1_response).

Real poll sequence captured in log (2026-03-11 20:08:22):
  0303020e461b0100                        → battery=27, is_brushing=False
  03072a422300011a030b14021700007800780514 → *B# header (1 record, inline 13 B)
  4b0000001f00000000001c14190c0a0a0a000000 → continuation (+20 B, 33/42 total)
  5b030301ffffffffff1a0306071e0f0000780078 → continuation (+20 B, 53/42 → flush)

Expected parsed values (confirmed from log line 26):
  last_brush_time     = 1773219743
  last_brush_duration = 120
  last_brush_score    = 91
  last_brush_pnum     = 0
"""

from __future__ import annotations

import pytest

from custom_components.oclean_ble.const import (
    DATA_BATTERY,
    DATA_LAST_BRUSH_AREAS,
    DATA_LAST_BRUSH_DURATION,
    DATA_LAST_BRUSH_PNUM,
    DATA_LAST_BRUSH_PRESSURE,
    DATA_LAST_BRUSH_SCORE,
    DATA_LAST_BRUSH_TIME,
)
from tests.integration_helpers import make_coordinator, run_poll
from tests.simulator import OcleanDeviceSimulator

# ---------------------------------------------------------------------------
# Real bytes captured from OCLEANY3MH log (2026-03-11 20:08:22)
# ---------------------------------------------------------------------------

# STATE: is_brushing=False, battery=27
_BYTES_0303 = bytes.fromhex("0303020e461b0100")

# 0307 *B# header: count=1 record, inline record[0:13]
# record[0]=0x1a (year_base=2026), [1]=03 [2]=0b [3]=14 [4]=02 [5]=17 → 2026-03-11 20:02:23
# pnum=0, duration=120s
_BYTES_0307_HEADER = bytes.fromhex("03072a422300011a030b14021700007800780514")

# continuation +20 B: record[13:33]
_BYTES_CONT1 = bytes.fromhex("4b0000001f00000000001c14190c0a0a0a000000")

# continuation +20 B: record[33:42 + extra] → completes the 42-byte record (flush)
_BYTES_CONT2 = bytes.fromhex("5b030301ffffffffff1a0306071e0f0000780078")

# Expected parsed values (from log "poll collected so far")
_EXPECTED_TS = 1773219743
_EXPECTED_DURATION = 120
_EXPECTED_SCORE = 91
_EXPECTED_PNUM = 0

_MAC = "70:28:45:83:2A:C9"


def _coordinator():
    return make_coordinator(_MAC, "Oclean X")


def _full_session_client():
    """Client that delivers the complete 3-packet *B# session burst."""
    return (
        OcleanDeviceSimulator()
        .with_battery(27)
        .add_notification(_BYTES_0303)
        .add_notification(_BYTES_0307_HEADER)
        .add_notification(_BYTES_CONT1)
        .add_notification(_BYTES_CONT2)
        .build_client()
    )


# ---------------------------------------------------------------------------
# TestOcleanY3MRealData – real bytes from OCLEANY3MH log
# ---------------------------------------------------------------------------


class TestOcleanY3MRealData:
    """End-to-end poll using verbatim notification bytes from OCLEANY3MH device.

    Validates that the *B# multi-packet reassembly in the coordinator correctly
    accumulates the three packets and the C3352g parser extracts all fields.
    """

    @pytest.mark.asyncio
    async def test_battery_from_0303_notification(self):
        """Battery is parsed from the 0303 STATE notification."""
        result = await run_poll(_coordinator(), _full_session_client())
        assert result[DATA_BATTERY] == 27

    @pytest.mark.asyncio
    async def test_session_timestamp_correct(self):
        """Timestamp decoded from *B# record is close to the device log value.

        The parser uses time.mktime() which is timezone-dependent.  We accept
        timestamps within ±14 h of the UTC reference (covers UTC-14 … UTC+14).
        """
        result = await run_poll(_coordinator(), _full_session_client())
        ts = result[DATA_LAST_BRUSH_TIME]
        assert abs(ts - _EXPECTED_TS) <= 14 * 3600, f"Timestamp {ts} too far from expected {_EXPECTED_TS}"

    @pytest.mark.asyncio
    async def test_session_duration_correct(self):
        """Duration (seconds) decoded from *B# record matches the device log."""
        result = await run_poll(_coordinator(), _full_session_client())
        assert result[DATA_LAST_BRUSH_DURATION] == _EXPECTED_DURATION

    @pytest.mark.asyncio
    async def test_session_score_correct(self):
        """Brushing score (0-100) decoded from *B# record matches the device log."""
        result = await run_poll(_coordinator(), _full_session_client())
        assert result[DATA_LAST_BRUSH_SCORE] == _EXPECTED_SCORE

    @pytest.mark.asyncio
    async def test_session_pnum_correct(self):
        """Brush-scheme pNum decoded from *B# record matches the device log."""
        result = await run_poll(_coordinator(), _full_session_client())
        assert result[DATA_LAST_BRUSH_PNUM] == _EXPECTED_PNUM

    @pytest.mark.asyncio
    async def test_areas_not_in_star_b_record(self):
        """Areas are not extracted from the *B# record – they come from the 2604 push.

        OCLEANY3M *B# records only contain 5 tooth-zone bytes (11-15); areas 6-8
        are absent and area data is only authoritative from the 2604 enrichment push
        (_parse_brush_areas_t1_response).  Without a 2604 notification this poll
        must not set last_brush_areas or last_brush_pressure.
        """
        result = await run_poll(_coordinator(), _full_session_client())
        assert DATA_LAST_BRUSH_AREAS not in result
        assert DATA_LAST_BRUSH_PRESSURE not in result

    @pytest.mark.asyncio
    async def test_no_session_when_only_0303_received(self):
        """Poll with only a 0303 STATE notification yields battery but no session."""
        client = OcleanDeviceSimulator().with_battery(27).add_notification(_BYTES_0303).build_client()
        result = await run_poll(_coordinator(), client)
        assert result[DATA_BATTERY] == 27
        assert result.get(DATA_LAST_BRUSH_TIME) is None
        assert result.get(DATA_LAST_BRUSH_SCORE) is None

    @pytest.mark.asyncio
    async def test_incomplete_star_b_hash_no_session(self):
        """If the *B# buffer never fills (only header + 1 continuation), no session is emitted."""
        client = (
            OcleanDeviceSimulator()
            .with_battery(27)
            .add_notification(_BYTES_0307_HEADER)
            .add_notification(_BYTES_CONT1)
            # _BYTES_CONT2 is intentionally omitted → buffer stays at 33/42 bytes
            .build_client()
        )
        result = await run_poll(_coordinator(), client)
        assert result.get(DATA_LAST_BRUSH_TIME) is None
        assert result.get(DATA_LAST_BRUSH_SCORE) is None
