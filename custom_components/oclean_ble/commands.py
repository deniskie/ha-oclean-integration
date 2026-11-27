"""Table of known Oclean BLE commands, used by the command probe and send_command.

Every entry mirrors a constant in const.py; the table only adds the metadata a
generic tool needs: which characteristic(s) the command is written to, which
response prefix identifies its answer, and whether it is safe to send blindly.

``probe`` marks read-only queries.  Commands that change device state (time
calibration, brush-head reset, settings, brush schemes) are listed so that
send_command can address them by name, but the probe never sends them.
"""

from __future__ import annotations

from dataclasses import dataclass

from .const import (
    CHANGE_INFO_UUID,
    CMD_AREA_REMIND,
    CMD_BRUSH_HEAD_MAX_DAYS,
    CMD_CALIBRATE_TIME_PREFIX,
    CMD_CALIBRATE_TIME_T1_PREFIX,
    CMD_CLEAR_BRUSH_HEAD,
    CMD_DEVICE_INFO,
    CMD_OVER_PRESSURE,
    CMD_QUERY_DEVICE_SETTINGS,
    CMD_QUERY_EXTENDED_DATA_T1,
    CMD_QUERY_RUNNING_DATA,
    CMD_QUERY_RUNNING_DATA_NEXT,
    CMD_QUERY_RUNNING_DATA_T1,
    CMD_QUERY_STATUS,
    CMD_REMIND_SWITCH,
    CMD_RUNNING_SWITCH,
    CMD_SET_BRUSH_SCHEME,
    READ_NOTIFY_CHAR_UUID,
    RECEIVE_BRUSH_UUID,
    RESP_DEVICE_INFO,
    RESP_DEVICE_SETTINGS,
    RESP_EXTENDED_T1,
    RESP_INFO,
    RESP_INFO_T1,
    RESP_STATE,
    SEND_BRUSH_CMD_UUID,
    WRITE_CHAR_UUID,
)

# Short aliases accepted by the send_command service for the two write characteristics.
CHAR_ALIASES: dict[str, str] = {
    "write": WRITE_CHAR_UUID,  # fbb85
    "brush_cmd": SEND_BRUSH_CMD_UUID,  # fbb89
}

# Characteristics a probe or send_command listens on.  Not every model has all
# of them; subscription failures are expected and ignored.
LISTEN_CHARS: tuple[str, ...] = (READ_NOTIFY_CHAR_UUID, RECEIVE_BRUSH_UUID, CHANGE_INFO_UUID)


@dataclass(frozen=True)
class OcleanCommand:
    """One known command.

    Attributes:
        key:          Stable name, used in the probe report and by send_command.
        payload:      Command bytes (prefix only for commands that take arguments).
        chars:        Characteristics to try, in order.  The probe tries all of them.
        response:     2-byte prefix of the expected answer, or None if unknown.
        probe:        True for read-only queries that the probe may send.
        description:  One-line human description (APK method in brackets).
    """

    key: str
    payload: bytes
    chars: tuple[str, ...]
    response: bytes | None
    probe: bool
    description: str


_BOTH = (SEND_BRUSH_CMD_UUID, WRITE_CHAR_UUID)

KNOWN_COMMANDS: tuple[OcleanCommand, ...] = (
    # --- read-only queries -------------------------------------------------
    OcleanCommand("status", CMD_QUERY_STATUS, _BOTH, RESP_STATE, True, "Status + battery (mo5295Q0)"),
    OcleanCommand("device_info", CMD_DEVICE_INFO, _BOTH, RESP_DEVICE_INFO, True, "Device info ACK (mo5310r0)"),
    OcleanCommand(
        "device_settings",
        CMD_QUERY_DEVICE_SETTINGS,
        _BOTH,
        RESP_DEVICE_SETTINGS,
        True,
        "Mode, area remind, brush-head counters (W0)",
    ),
    OcleanCommand(
        "running_data_t1",
        CMD_QUERY_RUNNING_DATA_T1,
        _BOTH,
        RESP_INFO_T1,
        True,
        "Type-1 session records, *B# stream",
    ),
    OcleanCommand("running_data", CMD_QUERY_RUNNING_DATA, _BOTH, RESP_INFO, True, "Type-0 session records (mo5299S0)"),
    OcleanCommand(
        "running_data_next",
        CMD_QUERY_RUNNING_DATA_NEXT,
        _BOTH,
        RESP_INFO,
        True,
        "Type-0 next session page (mo5301W0)",
    ),
    OcleanCommand(
        "extended_data_t1",
        CMD_QUERY_EXTENDED_DATA_T1,
        _BOTH,
        RESP_EXTENDED_T1,
        True,
        "Extended session data (mo5337g1)",
    ),
    # --- state-changing commands (never probed) ----------------------------
    OcleanCommand(
        "calibrate_time", CMD_CALIBRATE_TIME_PREFIX, (WRITE_CHAR_UUID,), None, False, "+4-byte BE unix time (Type 0)"
    ),
    OcleanCommand(
        "calibrate_time_t1",
        CMD_CALIBRATE_TIME_T1_PREFIX,
        (WRITE_CHAR_UUID,),
        None,
        False,
        "+8-byte datetime (Type 1)",
    ),
    OcleanCommand(
        "clear_brush_head", CMD_CLEAR_BRUSH_HEAD, (WRITE_CHAR_UUID,), None, False, "Reset brush-head counter"
    ),
    OcleanCommand("set_brush_scheme", CMD_SET_BRUSH_SCHEME, (WRITE_CHAR_UUID,), None, False, "+scheme packet"),
    OcleanCommand("area_remind", CMD_AREA_REMIND, (WRITE_CHAR_UUID,), None, False, "+01 on / 00 off"),
    OcleanCommand("over_pressure", CMD_OVER_PRESSURE, (WRITE_CHAR_UUID,), None, False, "+01 on / 00 off"),
    OcleanCommand("remind_switch", CMD_REMIND_SWITCH, (WRITE_CHAR_UUID,), None, False, "+01 on / 00 off"),
    OcleanCommand("running_switch", CMD_RUNNING_SWITCH, (WRITE_CHAR_UUID,), None, False, "+01 on / 00 off"),
    OcleanCommand("brush_head_max_days", CMD_BRUSH_HEAD_MAX_DAYS, (WRITE_CHAR_UUID,), None, False, "+2-byte BE days"),
)

COMMANDS_BY_KEY: dict[str, OcleanCommand] = {cmd.key: cmd for cmd in KNOWN_COMMANDS}
PROBE_COMMANDS: tuple[OcleanCommand, ...] = tuple(cmd for cmd in KNOWN_COMMANDS if cmd.probe)

# Entities whose only data source is one specific query.  They are created only
# when the stored probe report shows the device answers that query (or when no
# probe has run yet, so first-time setups keep every entity).
ENTITY_SOURCE_COMMAND: dict[str, str] = {
    "brush_mode": "device_settings",
    "brush_head_days": "device_settings",
}


def char_short(uuid: str) -> str:
    """Return the last five hex digits of a characteristic UUID (e.g. 'fbb89')."""
    return uuid.replace("-", "")[-5:]


def resolve_char(value: str | None, default: str) -> str:
    """Map a send_command characteristic argument (alias, short or full UUID) to a UUID."""
    if not value:
        return default
    value = value.strip().lower()
    if value in CHAR_ALIASES:
        return CHAR_ALIASES[value]
    for uuid in (*CHAR_ALIASES.values(), *LISTEN_CHARS):
        if value in (uuid, char_short(uuid)):
            return uuid
    raise ValueError(f"unknown characteristic: {value}")


def known_prefix(frame: bytes) -> str | None:
    """Return the key of the command whose response prefix matches *frame*, if any."""
    for cmd in KNOWN_COMMANDS:
        if cmd.response is not None and frame[:2] == cmd.response:
            return cmd.key
    return None
