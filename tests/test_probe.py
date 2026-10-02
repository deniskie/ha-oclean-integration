"""Tests for the command probe, send_command, diagnostics and entity gating.

The simulated device answers like an Oclean X Ultra (OCLEANV1a) seen in a
real poll: only 0202 and 0307 are answered, and only on fbb89.  The 0307
frame has the layout of a real extended-offset inline answer (no unsynced
sessions); the session time is made up.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.oclean_ble import _coordinator_for, _register_research_services
from custom_components.oclean_ble.commands import (
    COMMANDS_BY_KEY,
    KNOWN_COMMANDS,
    PROBE_COMMANDS,
    char_short,
    known_prefix,
    resolve_char,
)
from custom_components.oclean_ble.const import (
    CONF_DEVICE_NAME,
    CONF_MAC_ADDRESS,
    DATA_BATTERY,
    DATA_BRUSH_HEAD_DAYS,
    DATA_BRUSH_HEAD_USAGE,
    DATA_BRUSH_MODE,
    DATA_LAST_BRUSH_TIME,
    DATA_MODEL_ID,
    DATA_SW_VERSION,
    DOMAIN,
    RECEIVE_BRUSH_UUID,
    SEND_BRUSH_CMD_UUID,
    SERVICE_PROBE_COMMANDS,
    SERVICE_SEND_COMMAND,
    WRITE_CHAR_UUID,
)
from custom_components.oclean_ble.coordinator import OcleanCoordinator
from custom_components.oclean_ble.diagnostics import async_get_config_entry_diagnostics
from custom_components.oclean_ble.parser import is_known_frame
from custom_components.oclean_ble.protocol import protocol_for_model
from custom_components.oclean_ble.sensor import async_setup_entry as sensor_setup_entry
from tests.integration_helpers import make_coordinator, run_poll
from tests.simulator import OcleanDeviceSimulator

_MAC = "AA:BB:CC:DD:EE:01"
# OCLEANV1a answer to 0307 with no unsynced session (extended-offset inline);
# layout and header bytes from a real poll, session time replaced.
_V1A_0307 = bytes.fromhex("03072a42230000000000641a010f0800000200b4")
_ACK_0202 = bytes.fromhex("02024f4b")
_UNKNOWN = bytes.fromhex("11223344")


def _x_ultra_client():
    return (
        OcleanDeviceSimulator()
        .on_command(bytes.fromhex("0307"), _V1A_0307, char=SEND_BRUSH_CMD_UUID)
        .on_command(bytes.fromhex("0202"), _ACK_0202, char=SEND_BRUSH_CMD_UUID)
        .on_command(bytes.fromhex("0314"), _UNKNOWN, char=WRITE_CHAR_UUID)
        .build_client()
    )


def _v1a_coordinator() -> OcleanCoordinator:
    coord = make_coordinator(_MAC, "Oclean")
    coord._last_raw = {DATA_MODEL_ID: "OCLEANV1a", DATA_SW_VERSION: "1.1.3.8"}
    return coord


async def _probe(coord: OcleanCoordinator, client) -> dict:
    with patch("custom_components.oclean_ble.coordinator.asyncio.sleep", new_callable=AsyncMock):
        await coord._listen_all(client)
        return await coord._run_probe(client)


# ---------------------------------------------------------------------------
# Command table
# ---------------------------------------------------------------------------


class TestCommandTable:
    def test_keys_unique(self):
        assert len(COMMANDS_BY_KEY) == len(KNOWN_COMMANDS)

    def test_probe_only_read_only_queries(self):
        keys = {cmd.key for cmd in PROBE_COMMANDS}
        assert "clear_brush_head" not in keys
        assert "calibrate_time" not in keys
        assert all(cmd.response is not None for cmd in PROBE_COMMANDS)

    def test_resolve_char_aliases(self):
        assert resolve_char("brush_cmd", WRITE_CHAR_UUID) == SEND_BRUSH_CMD_UUID
        assert resolve_char("FBB85", SEND_BRUSH_CMD_UUID) == WRITE_CHAR_UUID
        assert resolve_char(None, WRITE_CHAR_UUID) == WRITE_CHAR_UUID
        with pytest.raises(ValueError, match="unknown characteristic"):
            resolve_char("nope", WRITE_CHAR_UUID)

    def test_known_prefix(self):
        assert known_prefix(_V1A_0307) == "running_data_t1"
        assert known_prefix(_UNKNOWN) is None
        assert char_short(SEND_BRUSH_CMD_UUID) == "fbb89"

    def test_is_known_frame(self):
        assert is_known_frame(_V1A_0307)
        assert is_known_frame(bytes.fromhex("3a0303ffffffffffff1a0306071e0f00007800780000"))
        assert not is_known_frame(_UNKNOWN)
        assert not is_known_frame(b"\x03")


# ---------------------------------------------------------------------------
# Probe
# ---------------------------------------------------------------------------


class TestProbe:
    async def test_x_ultra_answers(self):
        report = await _probe(_v1a_coordinator(), _x_ultra_client())
        assert report["supported"] == ["device_info", "running_data_t1"]
        assert report["results"]["running_data_t1"]["answered_on"] == ["fbb89"]
        assert report["results"]["status"]["answered"] is False
        assert report["results"]["device_settings"]["answered"] is False
        assert report["model_id"] == "OCLEANV1a"
        assert report["sw_version"] == "1.1.3.8"

    async def test_unknown_frames_reported(self):
        report = await _probe(_v1a_coordinator(), _x_ultra_client())
        assert report["unknown_frames"] == ["11223344"]
        # 0314 answered with an unknown frame: recorded but not "answered"
        assert report["results"]["extended_data_t1"]["answered"] is False
        frames = report["results"]["extended_data_t1"]["attempts"][1]["frames"]
        assert frames[0]["hex"] == "11223344"

    async def test_every_query_tried_on_both_chars(self):
        client = _x_ultra_client()
        await _probe(_v1a_coordinator(), client)
        written = [(c.args[0], bytes(c.args[1])) for c in client.write_gatt_char.call_args_list]
        for cmd in PROBE_COMMANDS:
            assert (SEND_BRUSH_CMD_UUID, cmd.payload) in written
            assert (WRITE_CHAR_UUID, cmd.payload) in written

    async def test_write_error_recorded(self):
        client = _x_ultra_client()
        client.write_gatt_char.side_effect = OSError("gatt write failed")
        report = await _probe(_v1a_coordinator(), client)
        assert report["supported"] == []
        assert "gatt write failed" in report["results"]["status"]["attempts"][0]["error"]
        # Nothing was actually tested: no verdict, and the report is not trusted
        assert report["tested"] is False
        assert report["results"]["status"]["answered"] is None
        coord = _v1a_coordinator()
        coord._probe_report = report
        assert coord.command_supported("status") is None

    async def test_failed_probe_not_stored(self):
        from bleak import BleakError

        coord = _v1a_coordinator()
        coord._store = MagicMock(async_save=AsyncMock())
        client = _x_ultra_client()
        client.write_gatt_char.side_effect = OSError("dropped")
        bt, conn, sleep = _patched_connection(client)
        with bt, conn, sleep, pytest.raises(BleakError, match="nothing stored"):
            await coord.async_probe_commands()
        assert coord.probe_report is None
        coord._store.async_save.assert_not_called()

    async def test_listens_only_on_model_notify_chars(self):
        coord = _v1a_coordinator()
        coord._protocol = protocol_for_model("OCLEANV1a")
        client = _x_ultra_client()
        await coord._listen_all(client)
        subscribed = {c.args[0] for c in client.start_notify.call_args_list}
        assert subscribed == set(coord._protocol.notify_chars)
        assert SEND_BRUSH_CMD_UUID not in subscribed

    async def test_frames_kept_for_diagnostics(self):
        coord = _v1a_coordinator()
        await _probe(coord, _x_ultra_client())
        hexes = [f["hex"] for f in coord.raw_frames]
        assert _V1A_0307.hex() in hexes
        assert coord.raw_frames[-1]["char"] == "fbb86"

    def test_probe_due(self):
        coord = _v1a_coordinator()
        assert coord._probe_due()
        coord._probe_report = {"model_id": "OCLEANV1a", "sw_version": "1.1.3.8"}
        assert not coord._probe_due()
        assert coord._probe_due("OCLEANV1a", "1.1.4.0")  # firmware update

    def test_command_supported(self):
        coord = _v1a_coordinator()
        assert coord.command_supported("status") is None
        coord._probe_report = {"results": {"status": {"answered": False}, "device_info": {"answered": True}}}
        assert coord.command_supported("status") is False
        assert coord.command_supported("device_info") is True
        assert coord.command_supported("extended_data_t1") is None


class TestProbeInsidePoll:
    async def test_auto_probe_runs_once_and_keeps_session(self):
        coord = _v1a_coordinator()
        coord._auto_probe = True
        client = (
            OcleanDeviceSimulator()
            .add_0307_session(2026, 9, 30, 7, 30, 0, pnum=2, duration=120)
            # A probe answer that would parse as a much newer session: must be ignored.
            .on_command(bytes.fromhex("0308"), bytes.fromhex("0308") + bytes(40), char=WRITE_CHAR_UUID)
            .on_command(bytes.fromhex("0307"), _V1A_0307, char=SEND_BRUSH_CMD_UUID)
            .build_client()
        )
        result = await run_poll(coord, client)
        assert coord.probe_report is not None
        assert "running_data_t1" in coord.probe_report["supported"]
        assert coord._probing is False
        # The session read by the poll is still the one reported
        assert result[DATA_LAST_BRUSH_TIME] is not None

    async def test_no_probe_without_auto_probe(self):
        coord = _v1a_coordinator()
        await run_poll(coord, _x_ultra_client())
        assert coord.probe_report is None

    async def test_probing_flag_blocks_parsing(self):
        coord = _v1a_coordinator()
        collected: dict = {}
        sessions: list = []
        handler, _ = coord._make_notification_handler(collected, sessions, set(), asyncio.Event())
        coord._probing = True
        handler(None, bytearray(_V1A_0307))
        assert collected == {}
        assert coord.raw_frames[-1]["hex"] == _V1A_0307.hex()


# ---------------------------------------------------------------------------
# Standalone actions (probe service, send_command)
# ---------------------------------------------------------------------------


def _patched_connection(client):
    return (
        patch("custom_components.oclean_ble.coordinator.bluetooth"),
        patch(
            "custom_components.oclean_ble.coordinator.establish_connection",
            new_callable=AsyncMock,
            return_value=client,
        ),
        patch("custom_components.oclean_ble.coordinator.asyncio.sleep", new_callable=AsyncMock),
    )


class TestStandaloneActions:
    async def test_async_probe_commands_stores_report(self):
        coord = _v1a_coordinator()
        coord._store = MagicMock(async_save=AsyncMock())
        client = _x_ultra_client()
        bt, conn, sleep = _patched_connection(client)
        with bt, conn, sleep:
            report = await coord.async_probe_commands()
        assert report["supported"] == ["device_info", "running_data_t1"]
        saved = coord._store.async_save.call_args[0][0]
        assert saved["probe_report"] == report
        client.disconnect.assert_awaited()

    async def test_send_command_decodes_frames(self):
        coord = _v1a_coordinator()
        client = _x_ultra_client()
        bt, conn, sleep = _patched_connection(client)
        with bt, conn, sleep:
            result = await coord.async_send_command(bytes.fromhex("0307"), SEND_BRUSH_CMD_UUID, 1.0)
        assert result["sent"] == "0307"
        assert result["char"] == "fbb89"
        assert result["error"] is None
        frame = result["frames"][0]
        assert frame["hex"] == _V1A_0307.hex()
        assert frame["decoded"][DATA_LAST_BRUSH_TIME] > 0
        # The diagnostics buffer is not polluted with decoded data
        assert "decoded" not in coord.raw_frames[-1]

    async def test_send_commands_single_connection(self):
        coord = _v1a_coordinator()
        client = _x_ultra_client()
        bt, conn, sleep = _patched_connection(client)
        with bt, conn as connect, sleep:
            result = await coord.async_send_commands(
                [bytes.fromhex("0202"), bytes.fromhex("0307")], SEND_BRUSH_CMD_UUID, 0.5
            )
        connect.assert_awaited_once()
        assert [r["sent"] for r in result] == ["0202", "0307"]
        assert result[0]["frames"][0]["hex"] == "02024f4b"
        assert result[1]["frames"][0]["decoded"][DATA_LAST_BRUSH_TIME] > 0

    def test_default_command_char(self):
        assert _v1a_coordinator().default_command_char == SEND_BRUSH_CMD_UUID

    async def test_store_round_trip(self):
        coord = _v1a_coordinator()
        coord._store_loaded = False
        report = {"supported": ["device_info"], "results": {}}
        report["tested"] = True
        coord._store = MagicMock(async_load=AsyncMock(return_value={"probe_report": report}))
        await coord.async_load_store()
        assert coord.probe_report == report

    async def test_untested_stored_report_discarded(self):
        coord = _v1a_coordinator()
        coord._store_loaded = False
        coord._store = MagicMock(async_load=AsyncMock(return_value={"probe_report": {"supported": []}}))
        await coord.async_load_store()
        assert coord.probe_report is None


# ---------------------------------------------------------------------------
# Services
# ---------------------------------------------------------------------------


def _hass_with(*coordinators):
    hass = MagicMock()
    hass.data = {DOMAIN: {f"entry{i}": c for i, c in enumerate(coordinators)}}
    handlers = {}
    hass.services.async_register = MagicMock(
        side_effect=lambda domain, name, handler, **kw: handlers.__setitem__(name, (handler, kw))
    )
    _register_research_services(hass)
    return hass, handlers


def _call(data):
    call = MagicMock()
    call.data = data
    return call


class TestServices:
    def _coordinator(self):
        coord = MagicMock(spec=OcleanCoordinator)
        coord.default_command_char = SEND_BRUSH_CMD_UUID
        coord.preferred_char = MagicMock(side_effect=lambda char, _cmd: char)
        coord.async_send_command = AsyncMock(return_value={"frames": []})
        coord.async_probe_commands = AsyncMock(return_value={"supported": []})
        return coord

    def test_registered_with_response(self):
        _, handlers = _hass_with(self._coordinator())
        assert handlers[SERVICE_PROBE_COMMANDS][1]["supports_response"] == "only"
        assert handlers[SERVICE_SEND_COMMAND][1]["supports_response"] == "optional"

    async def test_send_known_command_with_args(self):
        coord = self._coordinator()
        _, handlers = _hass_with(coord)
        await handlers[SERVICE_SEND_COMMAND][0](_call({"command": "area_remind", "payload": "01", "wait": 2.0}))
        coord.async_send_command.assert_awaited_once_with(bytes.fromhex("020d01"), WRITE_CHAR_UUID, 2.0)

    async def test_known_command_follows_probe(self):
        coord = self._coordinator()
        coord.preferred_char = MagicMock(return_value=WRITE_CHAR_UUID)
        _, handlers = _hass_with(coord)
        await handlers[SERVICE_SEND_COMMAND][0](_call({"command": "device_settings", "payload": "", "wait": 1.0}))
        coord.preferred_char.assert_called_once_with(SEND_BRUSH_CMD_UUID, bytes.fromhex("030201"))
        assert coord.async_send_command.call_args[0][1] == WRITE_CHAR_UUID

    async def test_send_raw_payload_default_char(self):
        coord = self._coordinator()
        _, handlers = _hass_with(coord)
        await handlers[SERVICE_SEND_COMMAND][0](_call({"payload": "03 07", "wait": 1.0}))
        coord.async_send_command.assert_awaited_once_with(bytes.fromhex("0307"), SEND_BRUSH_CMD_UUID, 1.0)

    async def test_send_several_payloads_one_connection(self):
        coord = self._coordinator()
        coord.async_send_commands = AsyncMock(return_value=[{"frames": []}, {"frames": []}])
        _, handlers = _hass_with(coord)
        result = await handlers[SERVICE_SEND_COMMAND][0](_call({"payload": "0301, 0304", "wait": 1.0}))
        coord.async_send_commands.assert_awaited_once_with(
            [bytes.fromhex("0301"), bytes.fromhex("0304")], SEND_BRUSH_CMD_UUID, 1.0
        )
        assert len(result["exchanges"]) == 2

    async def test_send_raw_payload_explicit_char(self):
        coord = self._coordinator()
        _, handlers = _hass_with(coord)
        await handlers[SERVICE_SEND_COMMAND][0](_call({"payload": "0303", "characteristic": "write", "wait": 1.0}))
        assert coord.async_send_command.call_args[0][1] == WRITE_CHAR_UUID

    async def test_invalid_hex_and_empty(self):
        from homeassistant.exceptions import HomeAssistantError

        _, handlers = _hass_with(self._coordinator())
        with pytest.raises(HomeAssistantError):
            await handlers[SERVICE_SEND_COMMAND][0](_call({"payload": "zz", "wait": 1.0}))
        with pytest.raises(HomeAssistantError):
            await handlers[SERVICE_SEND_COMMAND][0](_call({"payload": "", "wait": 1.0}))

    async def test_device_error_becomes_service_error(self):
        from homeassistant.exceptions import HomeAssistantError

        coord = self._coordinator()
        coord.async_probe_commands.side_effect = OSError("not reachable")
        _, handlers = _hass_with(coord)
        with pytest.raises(HomeAssistantError):
            await handlers[SERVICE_PROBE_COMMANDS][0](_call({}))

    def test_coordinator_selection(self):
        from homeassistant.exceptions import HomeAssistantError

        a, b = self._coordinator(), self._coordinator()
        hass, _ = _hass_with(a, b)
        assert _coordinator_for(hass, "entry1") is b
        with pytest.raises(HomeAssistantError):
            _coordinator_for(hass, None)
        with pytest.raises(HomeAssistantError):
            _coordinator_for(hass, "missing")


# ---------------------------------------------------------------------------
# Entity gating and diagnostics
# ---------------------------------------------------------------------------


def _entry():
    entry = MagicMock()
    entry.entry_id = "entry0"
    entry.data = {CONF_MAC_ADDRESS: _MAC, CONF_DEVICE_NAME: "Bathroom"}
    entry.options = {}
    return entry


class TestGatingAndDiagnostics:
    async def _sensor_keys(self, coord):
        hass = MagicMock()
        hass.data = {DOMAIN: {"entry0": coord}}
        added = []
        await sensor_setup_entry(hass, _entry(), added.extend)
        return {getattr(e, "entity_description", MagicMock(key=None)).key for e in added}

    async def test_settings_sensors_dropped_when_0302_unanswered(self):
        coord = _v1a_coordinator()
        coord._probe_report = {"results": {"device_settings": {"answered": False}}}
        keys = await self._sensor_keys(coord)
        assert DATA_BRUSH_MODE not in keys
        assert DATA_BRUSH_HEAD_DAYS not in keys

    async def test_all_sensors_without_probe(self):
        keys = await self._sensor_keys(_v1a_coordinator())
        assert DATA_BRUSH_MODE in keys
        assert DATA_BRUSH_HEAD_DAYS in keys

    async def test_diagnostics_redacts_and_reports(self):
        coord = _v1a_coordinator()
        coord._probe_report = {"supported": ["running_data_t1"]}
        coord._record_frame(MagicMock(uuid=RECEIVE_BRUSH_UUID), _V1A_0307)
        coord._record_frame(MagicMock(uuid=RECEIVE_BRUSH_UUID), _UNKNOWN)
        hass = MagicMock()
        hass.data = {DOMAIN: {"entry0": coord}}
        diag = await async_get_config_entry_diagnostics(hass, _entry())
        assert diag["entry"]["data"][CONF_MAC_ADDRESS] == "**REDACTED**"
        assert diag["entry"]["data"][CONF_DEVICE_NAME] == "**REDACTED**"
        assert diag["probe_report"] == {"supported": ["running_data_t1"]}
        assert diag["unknown_frames"] == ["11223344"]
        assert diag["raw_frames"][0]["char"] == "fbb90"
        assert _MAC not in str(diag)


# ---------------------------------------------------------------------------
# Fragmented 0302 device-settings response and probe-driven query routing
# ---------------------------------------------------------------------------

# OCLEANV1a answer to 030201 on fbb85: two notifications, each with a 0302
# header. Layout from a real capture; the device clock bytes are made up.
_V1A_0302_A = bytes.fromhex("0302232424000101000300010101010300020000")
_V1A_0302_B = bytes.fromhex("03021a010f0800000101" + "1000f0005a0000080200")
_V1A_0303 = bytes.fromhex("030302000024")


def _feed(coord, *frames):
    collected: dict = {}
    handler, _ = coord._make_notification_handler(collected, [], set(), asyncio.Event())
    for frame in frames:
        handler(None, bytearray(frame))
    return collected


class TestDeviceSettingsReassembly:
    def test_record_parsed_from_two_fragments(self):
        collected = _feed(_v1a_coordinator(), _V1A_0302_A, _V1A_0302_B)
        assert collected[DATA_BATTERY] == 36
        assert collected[DATA_BRUSH_MODE] == 3
        # sessions on the head = bytes 27-28 (0x005a); byte 31 (8) is not the counter
        assert collected[DATA_BRUSH_HEAD_USAGE] == 90
        assert DATA_BRUSH_HEAD_DAYS not in collected
        assert collected["area_remind"] is True

    def test_first_fragment_alone_sets_nothing(self):
        # The old per-packet parse read the length byte as battery (35) and
        # byte 5 as the mode; the fragment is now held until the record is whole.
        assert _feed(_v1a_coordinator(), _V1A_0302_A) == {}

    def test_invalid_record_falls_back_to_single_packet_parse(self):
        garbage = bytes.fromhex("0302") + bytes([0xFF] * 18)
        collected = _feed(_v1a_coordinator(), _V1A_0302_A, garbage)
        # fallback: the regular parser saw both packets separately
        assert DATA_BRUSH_HEAD_USAGE not in collected
        assert collected[DATA_BRUSH_MODE] == 0xFF  # byte 5 of the last packet, as before

    def test_unprefixed_0302_uses_existing_parser(self):
        collected = _feed(_v1a_coordinator(), bytes.fromhex("0302500000000004"))
        assert collected[DATA_BATTERY] == 80
        assert collected[DATA_BRUSH_MODE] == 4

    def test_record_validation(self):
        from custom_components.oclean_ble.parser import device_settings_record_valid, parse_device_settings_record

        good = (_V1A_0302_A[4:] + _V1A_0302_B[2:])[:34]
        assert device_settings_record_valid(good)
        assert not device_settings_record_valid(good[:20])
        bad_clock = good[:17] + bytes([13]) + good[18:]  # month 13
        assert parse_device_settings_record(bad_clock) == {}


_V1A_PROBE = {
    "tested": True,
    "results": {
        "status": {"payload": "0303", "answered": True, "answered_on": ["fbb85"]},
        "device_info": {"payload": "0202", "answered": True, "answered_on": ["fbb85"]},
        "device_settings": {"payload": "030201", "answered": True, "answered_on": ["fbb85"]},
        "running_data_t1": {"payload": "0307", "answered": True, "answered_on": ["fbb89", "fbb85"]},
    },
}


class TestV1aRealSession:
    """Frames from a real OCLEANV1a poll right after a brushing session (time made up)."""

    def test_inline_session_reports_scheduled_length_only(self):
        from custom_components.oclean_ble.const import DATA_LAST_BRUSH_DURATION_SCHEDULED

        collected = _feed(_v1a_coordinator(), bytes.fromhex("03072a42230000000000521a01100800000300c8"))
        assert collected[DATA_LAST_BRUSH_DURATION_SCHEDULED] == 200
        assert "last_brush_duration" not in collected

    def test_head_counter_increments_per_session(self):
        before = _feed(_v1a_coordinator(), _V1A_0302_A, _V1A_0302_B)
        after_b = _V1A_0302_B[:13] + bytes.fromhex("005b") + _V1A_0302_B[15:]  # bytes 27-28 of the record
        after = _feed(_v1a_coordinator(), _V1A_0302_A, after_b)
        assert after[DATA_BRUSH_HEAD_USAGE] == before[DATA_BRUSH_HEAD_USAGE] + 1

    async def test_new_inline_session_clears_previous_real_duration(self):
        coord = _v1a_coordinator()
        coord._dis_last_read_ts = 9e18
        coord._last_raw.update({DATA_LAST_BRUSH_TIME: 1, "last_brush_duration": 95})
        client = (
            OcleanDeviceSimulator()
            .on_command(bytes.fromhex("0307"), bytes.fromhex("03072a42230000000000521a01100800000300c8"))
            .build_client()
        )
        result = await run_poll(coord, client)
        assert result["last_brush_duration_scheduled"] == 200
        assert result.get("last_brush_duration") is None


class TestProbeDrivenRouting:
    def test_query_char_follows_probe(self):
        coord = _v1a_coordinator()
        assert coord._query_char(SEND_BRUSH_CMD_UUID, bytes.fromhex("0303")) == SEND_BRUSH_CMD_UUID
        coord._probe_report = _V1A_PROBE
        assert coord._query_char(SEND_BRUSH_CMD_UUID, bytes.fromhex("0303")) == WRITE_CHAR_UUID
        assert coord._query_char(SEND_BRUSH_CMD_UUID, bytes.fromhex("030201")) == WRITE_CHAR_UUID
        # answered on the default char too: keep the default
        assert coord._query_char(SEND_BRUSH_CMD_UUID, bytes.fromhex("0307")) == SEND_BRUSH_CMD_UUID
        # never answered: keep the default
        assert coord._query_char(SEND_BRUSH_CMD_UUID, bytes.fromhex("0314")) == SEND_BRUSH_CMD_UUID

    async def test_poll_reads_settings_after_probe(self):
        def device():
            return (
                OcleanDeviceSimulator()
                .on_command(bytes.fromhex("0303"), _V1A_0303, char=WRITE_CHAR_UUID)
                .on_command(bytes.fromhex("030201"), _V1A_0302_A, _V1A_0302_B, char=WRITE_CHAR_UUID)
                .on_command(bytes.fromhex("0307"), _V1A_0307, char=SEND_BRUSH_CMD_UUID)
                .build_client()
            )

        coord = _v1a_coordinator()
        coord._dis_last_read_ts = 9e18  # DIS cached: keep the model from _last_raw
        before = await run_poll(coord, device())
        assert DATA_BRUSH_MODE not in before or before[DATA_BRUSH_MODE] is None

        coord._probe_report = _V1A_PROBE
        after = await run_poll(coord, device())
        assert after[DATA_BRUSH_MODE] == 3
        assert after[DATA_BRUSH_HEAD_USAGE] == 90
        assert coord.area_remind is True
