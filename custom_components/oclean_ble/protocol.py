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
    CMD_DEVICE_INFO,
    CMD_QUERY_DEVICE_SETTINGS,
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
        write_char:          GATT characteristic UUID used for one-off write commands
                             (area_remind, brush_head_max_days, reset_brush_head,
                             time calibration).  Type-0 uses fbb85; Type-1 uses fbb89.
    """

    name: str
    notify_chars: tuple[str, ...]
    query_commands: tuple[tuple[str, bytes], ...]
    supports_pagination: bool
    write_char: str = WRITE_CHAR_UUID
    uses_t1_calibration: bool = False


# ---------------------------------------------------------------------------
# Concrete profiles
# ---------------------------------------------------------------------------

#: Type-0 – reserved; currently no confirmed production devices use this profile.
#: OCLEANY3 was previously here but was reclassified to TYPE1 (issue #49).
TYPE0 = DeviceProtocol(
    name="Type-0",
    notify_chars=(READ_NOTIFY_CHAR_UUID, CHANGE_INFO_UUID),
    query_commands=(
        (WRITE_CHAR_UUID, CMD_QUERY_STATUS),
        (WRITE_CHAR_UUID, CMD_DEVICE_INFO),
        (WRITE_CHAR_UUID, CMD_QUERY_DEVICE_SETTINGS),
        (WRITE_CHAR_UUID, CMD_QUERY_RUNNING_DATA),
    ),
    supports_pagination=True,
)

#: Type-1 – Oclean X / OCLEANY3M  (0307 push records)
TYPE1 = DeviceProtocol(
    name="Type-1",
    notify_chars=(READ_NOTIFY_CHAR_UUID, RECEIVE_BRUSH_UUID, SEND_BRUSH_CMD_UUID),
    query_commands=(
        (SEND_BRUSH_CMD_UUID, CMD_QUERY_STATUS),
        (SEND_BRUSH_CMD_UUID, CMD_DEVICE_INFO),
        (SEND_BRUSH_CMD_UUID, CMD_QUERY_DEVICE_SETTINGS),
        (SEND_BRUSH_CMD_UUID, CMD_QUERY_RUNNING_DATA_T1),
    ),
    supports_pagination=False,
    write_char=SEND_BRUSH_CMD_UUID,
    uses_t1_calibration=True,
)

#: Type-Z1 – Oclean Z1 / OCLEANY5
#: APK handler: C3350f mode=1 (p105g/C3350f.java)
#: Query commands routed to fbb85 except 0307 which goes to fbb89.
#: Notify characteristics: fbb86 + fbb90 (fbb89 is write-only, subscribe fails).
#: Time calibration uses the TYPE1 format (0201 + 8-byte datetime, mo5292L).
#: Area-remind command: 0209 + byte (mo5305l0, same for all modes in C3350f).
#: Brush-head-max-days: 0217 + 2-byte LE short (mo5345x mode=1).
TYPE_Z1 = DeviceProtocol(
    name="Type-Z1",
    notify_chars=(READ_NOTIFY_CHAR_UUID, RECEIVE_BRUSH_UUID),
    query_commands=(
        (WRITE_CHAR_UUID, CMD_QUERY_STATUS),
        (WRITE_CHAR_UUID, CMD_DEVICE_INFO),
        (WRITE_CHAR_UUID, CMD_QUERY_DEVICE_SETTINGS),
        (SEND_BRUSH_CMD_UUID, CMD_QUERY_RUNNING_DATA_T1),
    ),
    supports_pagination=False,
    write_char=WRITE_CHAR_UUID,
    uses_t1_calibration=True,
)

#: Legacy – Oclean Air 1 / OCLEANA1  (no working notify chars; battery-only)
LEGACY = DeviceProtocol(
    name="Legacy",
    notify_chars=(),
    query_commands=((WRITE_CHAR_UUID, CMD_QUERY_STATUS),),
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
        (WRITE_CHAR_UUID, CMD_DEVICE_INFO),
        (WRITE_CHAR_UUID, CMD_QUERY_DEVICE_SETTINGS),
        (WRITE_CHAR_UUID, CMD_QUERY_RUNNING_DATA),
        (SEND_BRUSH_CMD_UUID, CMD_QUERY_RUNNING_DATA_T1),
    ),
    supports_pagination=True,  # safe: pagination stops when no new sessions arrive
)


# ---------------------------------------------------------------------------
# Model-ID → protocol lookup
# ---------------------------------------------------------------------------

_MODEL_MAP: dict[str, DeviceProtocol] = {
    # ------------------------------------------------------------------
    # Type-1 – 0307 push via RECEIVE_BRUSH_UUID / SEND_BRUSH_CMD_UUID
    # APK handler: C3385w0 mode=1
    # ------------------------------------------------------------------
    "OCLEANY3M": TYPE1,  # Oclean X              – confirmed (logs 2026-02-21)
    "OCLEANY3MH": TYPE1,  # Oclean X (HW variant) – confirmed (logs 2026-03-09, issue #19)
    "OCLEANY3MT": TYPE1,  # Oclean X (T)          – APK DeviceType 25
    "OCLEANY3MTN": TYPE1,  # Oclean X (TN)         – APK DeviceType 26
    "OCLEANY3MN": TYPE1,  # Oclean X (N)          – APK DeviceType 27
    "OCLEANY3N": TYPE1,  # Oclean X (N model)    – APK DeviceType 28
    "OCLEANY3MD": TYPE1,  # Oclean X (MD)         – APK DeviceType 30
    "OCLEANY3D": TYPE1,  # Oclean X (D)          – APK DeviceType 32
    "OCLEANY3D1": TYPE1,  # Oclean X (D1)         – APK DeviceType 33
    "OCLEANY3D2": TYPE1,  # Oclean X (D2)         – APK DeviceType 43
    "OCLEANR3L": TYPE1,  # Oclean R3L            – APK DeviceType 38
    # APK handler: C3352g mode=0  (same BLE structure as OCLEANY3P)
    "OCLEANY3P": TYPE1,  # Oclean X Pro Elite    – confirmed (logs 2026-02-25, issue #3)
    "OCLEANY3PD": TYPE1,  # Oclean X Pro Elite D  – APK DeviceType 29
    # OCLEANX20 – Oclean X Pro 20: confirmed TYPE1 via debug logs (issue #37, 2026-03-09)
    # No CHANGE_INFO_UUID, fbb89 write-only (subscribe fails), 0307 push via fbb90.
    # Same year_byte=0/021f/5100 pattern as OCLEANY3P.
    "OCLEANX20": TYPE1,  # Oclean X Pro 20       – confirmed (logs 2026-03-09, issue #37)
    # ------------------------------------------------------------------
    # Type-1 – Oclean X Pro / OCLEANY3 family
    # Previously mapped to Type-0 (0308/fbb86) based on APK analysis, but
    # empirical BLE logs (issue #49, 2026-03-10) show the device only pushes
    # session data when CMD 0307 is sent to fbb89 (SEND_BRUSH_CMD_UUID).
    # With Type-0 the device receives 0308 on fbb85 and returns nothing.
    # The response uses the same *B# multi-packet format as OCLEANY3P.
    # ------------------------------------------------------------------
    "OCLEANY3": TYPE1,  # Oclean X Pro          – corrected (logs 2026-03-10, issue #49)
    "OCLEANY3S": TYPE1,  # Oclean X Pro (S)      – APK DeviceType 9
    "OCLEANY3T": TYPE1,  # Oclean X Pro (T)      – APK DeviceType 10
    # ------------------------------------------------------------------
    # Type-Z1 – Oclean Z1 / OCLEANY5
    # APK handler: C3350f mode=1
    # 0303/0202/0302 via fbb85; 0307 via fbb89; notify on fbb86+fbb90.
    # ------------------------------------------------------------------
    "OCLEANY5": TYPE_Z1,  # Oclean Z1 – confirmed (issue #69, 2026-03-23)
    # ------------------------------------------------------------------
    # Legacy – fbb86 has no CCCD; battery read via direct characteristic read
    # APK handler: C3385w0 mode=0 (Air 1 family)
    # ------------------------------------------------------------------
    "OCLEANA1": LEGACY,  # Oclean Air 1          – confirmed (logs 2026-02-27, issue #7)
    "OCLEANA1a": LEGACY,  # Oclean Air 1a         – APK DeviceType 7
    "OCLEANA1b": LEGACY,  # Oclean Air 1b         – APK DeviceType 15
    "OCLEANA1c": LEGACY,  # Oclean Air 1c         – APK DeviceType 17
    "OCLEANA1d": LEGACY,  # Oclean Air 1d         – APK DeviceType 18
    # ------------------------------------------------------------------
    # Not mapped → UNKNOWN fallback (tries all chars/commands, logs everything):
    #   OCLEANX1/OCLEANY2/OCLEANX1+/OCLEANY2+/OCLEANK1 – older Dialog handler classes
    #   OCLEANW1/W1a/W1b/W1d – Wone serial protocol, unrelated BLE stack
    #   OCLEANC1 – WiFi only, no BLE session data
    #   OCLEANA1e/A1f – C3352g mode=3, protocol not yet confirmed
    #   0001..000F generic model IDs – handler confirmed but protocol untested via BLE
    # ------------------------------------------------------------------
}


def protocol_for_model(model_id: str | None) -> DeviceProtocol:
    """Return the DeviceProtocol for a BLE DIS model-ID string.

    Falls back to UNKNOWN for unrecognised or absent model IDs so that
    new devices are handled gracefully and produce useful debug logs.
    """
    if not model_id:
        return UNKNOWN
    return _MODEL_MAP.get(model_id, UNKNOWN)
