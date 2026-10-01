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
    DATA_BRUSH_HEAD_DAYS,
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

    def test_default_command_char(self):
        assert _v1a_coordinator().default_command_char == SEND_BRUSH_CMD_UUID

    async def test_store_round_trip(self):
        coord = _v1a_coordinator()
        coord._store_loaded = False
        report = {"supported": ["device_info"], "results": {}}
        coord._store = MagicMock(async_load=AsyncMock(return_value={"probe_report": report}))
        await coord.async_load_store()
        assert coord.probe_report == report


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

    async def test_send_raw_payload_default_char(self):
        coord = self._coordinator()
        _, handlers = _hass_with(coord)
        await handlers[SERVICE_SEND_COMMAND][0](_call({"payload": "03 07", "wait": 1.0}))
        coord.async_send_command.assert_awaited_once_with(bytes.fromhex("0307"), SEND_BRUSH_CMD_UUID, 1.0)

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
