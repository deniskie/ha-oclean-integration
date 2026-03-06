"""Device protocol profiles for Oclean BLE devices.

Each profile describes which GATT characteristics to subscribe and which
commands to send for one device family.  The coordinator selects the correct
profile after reading the Model Number from the BLE Device Information Service
(DIS) and uses it for every subsequent step of the poll.
"""
from __future__ import annotations

from dataclasses import dataclass

from .const import (
    CHANGE_INFO_UUID,
    CMD_QUERY_EXTENDED_DATA_T1,
    CMD_QUERY_RUNNING_DATA,
    CMD_QUERY_RUNNING_DATA_T1,
    CMD_QUERY_STATUS,
    READ_NOTIFY_CHAR_UUID,
    RECEIVE_BRUSH_UUID,
    SEND_BRUSH_CMD_UUID,
    WRITE_CHAR_UUID,
)


@dataclass(frozen=True)
class DeviceProtocol:
    """Static capability profile of one Oclean device family.

    Attributes:
        name:                Human-readable family name (used in log messages).
        notify_chars:        GATT characteristics to subscribe for notifications.
        query_commands:      Sequence of (characteristic_uuid, command_bytes) pairs
                             to send after subscribing.  Sent in order; errors are
                             logged at DEBUG level and do not abort the poll.
        supports_pagination: True if the device supports 0309 session pagination
                             (Type-0 / Oclean X Pro family only).
    """

    name: str
    notify_chars: tuple[str, ...]
    query_commands: tuple[tuple[str, bytes], ...]
    supports_pagination: bool


# ---------------------------------------------------------------------------
# Concrete profiles
# ---------------------------------------------------------------------------

#: Type-0 – Oclean X Pro / OCLEANY3 / OCLEANY3P  (extended 0308 records)
TYPE0 = DeviceProtocol(
    name="Type-0",
    notify_chars=(READ_NOTIFY_CHAR_UUID, CHANGE_INFO_UUID),
    query_commands=(
        (WRITE_CHAR_UUID, CMD_QUERY_STATUS),
        (WRITE_CHAR_UUID, CMD_QUERY_RUNNING_DATA),
    ),
    supports_pagination=True,
)

#: Type-1 – Oclean X / OCLEANY3M  (0307 push records)
TYPE1 = DeviceProtocol(
    name="Type-1",
    notify_chars=(READ_NOTIFY_CHAR_UUID, RECEIVE_BRUSH_UUID, SEND_BRUSH_CMD_UUID),
    query_commands=(
        (WRITE_CHAR_UUID, CMD_QUERY_STATUS),
        (SEND_BRUSH_CMD_UUID, CMD_QUERY_RUNNING_DATA_T1),
        (SEND_BRUSH_CMD_UUID, CMD_QUERY_EXTENDED_DATA_T1),
    ),
    supports_pagination=False,
)

#: Legacy – Oclean Air 1 / OCLEANA1  (no working notify chars; battery-only)
LEGACY = DeviceProtocol(
    name="Legacy",
    notify_chars=(),
    query_commands=(
        (WRITE_CHAR_UUID, CMD_QUERY_STATUS),
    ),
    supports_pagination=False,
)

#: Unknown – fallback for unrecognised or absent model IDs.
#: Subscribes to all known characteristics and sends all known commands so
#: that new devices produce useful debug logs and degrade gracefully.
UNKNOWN = DeviceProtocol(
    name="Unknown",
    notify_chars=(
        READ_NOTIFY_CHAR_UUID,
        RECEIVE_BRUSH_UUID,
        CHANGE_INFO_UUID,
        SEND_BRUSH_CMD_UUID,
    ),
    query_commands=(
        (WRITE_CHAR_UUID, CMD_QUERY_STATUS),
        (WRITE_CHAR_UUID, CMD_QUERY_RUNNING_DATA),
        (SEND_BRUSH_CMD_UUID, CMD_QUERY_RUNNING_DATA_T1),
        (SEND_BRUSH_CMD_UUID, CMD_QUERY_EXTENDED_DATA_T1),
    ),
    supports_pagination=True,  # safe: pagination stops when no new sessions arrive
)


# ---------------------------------------------------------------------------
# Model-ID → protocol lookup
# ---------------------------------------------------------------------------

_MODEL_MAP: dict[str, DeviceProtocol] = {
    # Type-1 devices (Oclean X family – 0307 push)
    "OCLEANY3M": TYPE1,
    # Type-0 devices (Oclean X Pro family – extended 0308)
    "OCLEANY3":  TYPE0,
    "OCLEANY3P": TYPE0,
    # Legacy devices (battery-only; no functional notify characteristics)
    "OCLEANA1":  LEGACY,
}


def protocol_for_model(model_id: str | None) -> DeviceProtocol:
    """Return the DeviceProtocol for a BLE DIS model-ID string.

    Falls back to UNKNOWN for unrecognised or absent model IDs so that
    new devices are handled gracefully and produce useful debug logs.
    """
    if not model_id:
        return UNKNOWN
    return _MODEL_MAP.get(model_id, UNKNOWN)
