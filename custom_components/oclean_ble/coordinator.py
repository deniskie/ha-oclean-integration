"""DataUpdateCoordinator for the Oclean integration."""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import datetime
import logging
import struct
import time
import traceback
from collections.abc import Callable
from datetime import time as _dtime
from datetime import timedelta
from typing import Any

from bleak import BleakClient, BleakError
from bleak.backends.device import BLEDevice
from bleak_retry_connector import establish_connection
from homeassistant.components import bluetooth
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    BATTERY_CHAR_UUID,
    BLE_ENRICHMENT_WAIT,
    BLE_NOTIFICATION_WAIT,
    BLE_NOTIFICATION_WAIT_NO_SUB,
    BLE_PAGINATION_TIMEOUT,
    BLE_POLL_FALLBACK_ATTEMPTS,
    BLE_POLL_FALLBACK_INTERVAL,
    BLE_POLL_TOTAL_TIMEOUT,
    BLE_POST_CONNECT_DELAY,
    BLE_READ_FALLBACK_DELAY,
    BLE_SUBSCRIBE_FIRST_TIMEOUT,
    BLE_SUBSCRIBE_RETRY_TIMEOUT,
    BLE_WRITE_TIMEOUT,
    CMD_AREA_REMIND,
    CMD_BRUSH_HEAD_MAX_DAYS,
    CMD_CALIBRATE_TIME_PREFIX,
    CMD_CALIBRATE_TIME_T1_PREFIX,
    CMD_CLEAR_BRUSH_HEAD,
    CMD_OVER_PRESSURE,
    CMD_QUERY_RUNNING_DATA_NEXT,
    CMD_REMIND_SWITCH,
    CMD_RUNNING_SWITCH,
    CMD_SET_BIRTHDAY,
    CMD_SET_BRUSH_SCHEME_CONT,
    DATA_BATTERY,
    DATA_BRUSH_HEAD_USAGE,
    DATA_HW_REVISION,
    DATA_LAST_BRUSH_AREAS,
    DATA_LAST_BRUSH_DURATION,
    DATA_LAST_BRUSH_PNUM,
    DATA_LAST_BRUSH_PRESSURE,
    DATA_LAST_BRUSH_SCORE,
    DATA_LAST_BRUSH_TIME,
    DATA_LAST_POLL,
    DATA_MODEL_ID,
    DATA_SW_VERSION,
    DIS_HW_REV_UUID,
    DIS_MODEL_UUID,
    DIS_SW_REV_UUID,
    DOMAIN,
    MAX_SESSION_PAGES,
    OCLEANY3M_SCHEMES,
    READ_NOTIFY_CHAR_UUID,
    RECEIVE_BRUSH_UUID,
    SCHEMES_BY_MODEL,
    STORAGE_VERSION,
    WRITE_CHAR_UUID,
)
from .models import OcleanDeviceData
from .parser import (
    T1_C3352G_RECORD_SIZE,
    parse_battery,
    parse_notification,
    parse_t1_c3352g_record,
    parse_t1_c3385w0_record,
    parse_y3p_stream_record,
)
from .protocol import UNKNOWN, DeviceProtocol, protocol_for_model
from .statistics import import_new_sessions

_LOGGER = logging.getLogger(__name__)

# Standard BLE CCCD descriptor UUID (Client Characteristic Configuration Descriptor).
# Writing 0x0000 disables notifications/indications on the device side, clearing any
# stale subscription state left over from a previous bonded or crashed connection.
_CCCD_UUID = "00002902-0000-1000-8000-00805f9b34fb"


async def _clear_cccd(client: BleakClient, char_uuid: str) -> None:
    """Write 0x0000 to the CCCD descriptor of *char_uuid*, suppressing all errors.

    BlueZ keeps per-bond CCCD state on the device.  After a crashed or proxy-
    disconnected session the device may still have notifications enabled, causing
    the next ``start_notify`` to fail with "Notify acquired" or a TimeoutError.
    Explicitly clearing the CCCD resets that state before retrying.
    """
    try:
        char = client.services.get_characteristic(char_uuid)
        if char is None:
            return
        cccd = char.get_descriptor(_CCCD_UUID)
        if cccd is None:
            return
        await client.write_gatt_descriptor(cccd.handle, b"\x00\x00")
    except Exception:  # noqa: BLE001
        pass


async def _try_subscribe_no_cccd(
    client: BleakClient,
    char_uuid: str,
    handler: Callable[[Any, bytearray], None],
    log: logging.Logger | logging.LoggerAdapter[Any],
) -> bool:
    """Subscribe to a notify-only characteristic that has no CCCD descriptor.

    Some BLE firmware (e.g. OCLEANA1) sends unsolicited notifications on a
    characteristic without advertising a CCCD descriptor.  Android handles
    this by calling setCharacteristicNotification() locally; BlueZ also
    accepts StartNotify() without CCCD.  Bleak's Python layer adds an extra
    check and raises when no CCCD is found.

    Workaround: inject a synthetic CCCD descriptor into the Bleak char object
    so the pre-flight check passes, call start_notify, then remove it.
    """
    injected_handle: int | None = None
    char = None
    try:
        char = client.services.get_characteristic(char_uuid)
        if char is not None and char.get_descriptor(_CCCD_UUID) is None:
            from bleak.backends.descriptor import BleakGATTDescriptor

            fake = BleakGATTDescriptor(obj=None, handle=0xFFFE, uuid=_CCCD_UUID, characteristic=char)
            char.add_descriptor(fake)
            injected_handle = 0xFFFE
            log.debug("injected fake CCCD for %s – retrying start_notify", char_uuid)
    except Exception:  # noqa: BLE001
        pass  # injection failed – try start_notify anyway, it may still succeed

    try:
        await client.start_notify(char_uuid, handler)
        log.debug("subscribed to %s (no-CCCD path)", char_uuid)
        return True
    except Exception as err:  # noqa: BLE001
        log.debug(
            "no-CCCD subscribe also failed for %s: %s (%s)",
            char_uuid,
            err,
            type(err).__name__,
        )
        return False
    finally:
        if char is not None and injected_handle is not None:
            char._descriptors.pop(injected_handle, None)


# Gear-byte encoding table used by m28p() for TYPE1 SetBrushScheme (0206).
# Gears 1-12 map to specific encoded values; all others (13-41) encode as 0x00.
_GEAR_ENCODE: dict[int, int] = {1: 5, 2: 6, 3: 7, 4: 8, 5: 17, 6: 18, 7: 19, 8: 20, 9: 21, 10: 22, 11: 23, 12: 24}


def _build_scheme_packets(pnum: int, steps: list[tuple[int, int]]) -> list[bytes]:
    """Build TYPE1 SetBrushScheme BLE packet(s) following APK AbstractC0002b.m28p() logic.

    Single packet: [0x02, 0x06, pnum, stepCount, (enc_gear, gear, dur)*N, 0x00, 0x05]

    If the payload exceeds 20 bytes (conservative threshold for ATT_MTU=23), the
    APK splits it into two GATT writes:
      Packet 1: payload[0:16] + [0x2A, 0x2B]
      Packet 2: CMD_SET_BRUSH_SCHEME_CONT (0x020B) + payload[16:]
    """
    payload = bytearray([0x02, 0x06, pnum & 0xFF, len(steps) & 0xFF])
    for gear, dur in steps:
        payload.append(_GEAR_ENCODE.get(gear, 0))
        payload.append(gear & 0xFF)
        payload.append(dur & 0xFF)
    payload += bytes([0x00, 0x05])
    if len(payload) > 20:
        pkt1 = bytes(payload[:16]) + bytes([0x2A, 0x2B])
        pkt2 = CMD_SET_BRUSH_SCHEME_CONT + bytes(payload[16:])
        return [pkt1, pkt2]
    return [bytes(payload)]


class _CoordLoggerAdapter(logging.LoggerAdapter):
    """Logger adapter that prepends [MODEL/XX] to every log message.

    MODEL is the device model ID (e.g. OCLEANY3P) or '?' before the first DIS
    read.  XX are the last two hex characters of the MAC address so that log
    entries from multiple devices can be told apart at a glance.
    """

    def process(self, msg: str, kwargs: dict) -> tuple[str, dict]:  # type: ignore[override]
        coord: OcleanCoordinator = self.extra["coord"]  # type: ignore[index, assignment]
        model = coord._last_raw.get(DATA_MODEL_ID) or "?"
        suffix = coord._mac.replace(":", "")[-2:].upper()
        return f"[{model}/{suffix}] {msg}", kwargs


def _patch_aioesphomeapi_uuid_parser() -> None:
    """Patch aioesphomeapi to handle GATT descriptors with empty UUIDs.

    The Oclean toothbrush firmware sends some GATT descriptors with a
    zero-element UUID list. aioesphomeapi._join_split_uuid() assumes at
    least 2 elements and raises IndexError: list index out of range.

    This one-time patch replaces the empty list with a null-UUID string
    so service discovery completes normally.
    """
    try:
        import aioesphomeapi.model as _model  # only present in proxy setups

        _orig_join = _model._join_split_uuid

        def _safe_join_split_uuid(value: list) -> str:
            if len(value) < 2:
                # Oclean sends malformed descriptors with 0 or 1 UUID parts.
                # Return a null UUID so service parsing can continue.
                return "00000000-0000-0000-0000-000000000000"
            return _orig_join(value)

        _model._join_split_uuid = _safe_join_split_uuid
        _LOGGER.debug("aioesphomeapi UUID parser patched for Oclean BLE compatibility")
    except Exception:  # noqa: BLE001
        # aioesphomeapi not installed (no proxy / standalone bleak) – nothing to do
        pass


_patch_aioesphomeapi_uuid_parser()


# ---------------------------------------------------------------------------
# Poll-window helpers
# ---------------------------------------------------------------------------


def _parse_poll_windows(windows_str: str) -> list[tuple[_dtime, _dtime]]:
    """Parse 'HH:MM-HH:MM[, HH:MM-HH:MM, ...]' into (start, end) time pairs.

    Accepts up to 3 windows.  Windows where start == end are skipped.
    Invalid entries are silently ignored.
    """
    result: list[tuple[_dtime, _dtime]] = []
    for part in (windows_str or "").split(","):
        part = part.strip()
        if "-" not in part:
            continue
        try:
            s, e = part.split("-", 1)
            sh, sm = s.strip().split(":")
            eh, em = e.strip().split(":")
            start = _dtime(int(sh), int(sm))
            end = _dtime(int(eh), int(em))
        except (ValueError, AttributeError):
            continue
        if start != end:
            result.append((start, end))
    return result[:3]  # honour the "up to 3 windows" promise


def _in_window(start: _dtime, end: _dtime, now: _dtime) -> bool:
    """Return True if *now* falls within [start, end].

    Supports overnight windows (e.g. 23:00–01:00) where start > end.
    """
    if start < end:
        return start <= now <= end
    # overnight window
    return now >= start or now <= end


# Keys that persist from previous poll when the device is unreachable
_PERSISTENT_KEYS = (
    DATA_BATTERY,
    DATA_BRUSH_HEAD_USAGE,
    DATA_LAST_BRUSH_SCORE,
    DATA_LAST_BRUSH_DURATION,
    DATA_LAST_BRUSH_PRESSURE,
    DATA_LAST_BRUSH_TIME,
    DATA_LAST_BRUSH_AREAS,
    DATA_LAST_BRUSH_PNUM,
    DATA_MODEL_ID,
    DATA_HW_REVISION,
    DATA_SW_VERSION,
)

# Fields delivered by enrichment notifications (0000/2604) that carry no timestamp.
# These arrive AFTER the session-creating notification (5a00/0307) and must be
# merged back into the session snapshot for correct statistics import.
_ENRICHMENT_KEYS: tuple[str, ...] = (
    DATA_LAST_BRUSH_SCORE,
    DATA_LAST_BRUSH_AREAS,
    DATA_LAST_BRUSH_PRESSURE,
)

# All notify characteristics across all device types (used as fallback set for
# the UNKNOWN protocol and referenced in tests via _NOTIFY_CHARS).
_NOTIFY_CHARS: tuple[str, ...] = UNKNOWN.notify_chars

# DIS re-read interval: 24 h in seconds. Info only changes after firmware updates.
_DIS_REFRESH_INTERVAL = 86_400

# Oclean GMT offset table (1-based, 33 entries) – from DateUtils.java / C3352g.java.
# Used to map the local UTC offset to the tzIndex byte in the 0201 calibration command.
_TZ_OFFSETS_MIN: tuple[int, ...] = (
    -720,
    -660,
    -600,
    -540,
    -480,
    -420,
    -360,
    -300,
    -240,
    -210,
    -180,
    -120,
    -60,
    0,
    60,
    120,
    180,
    210,
    240,
    270,
    300,
    330,
    345,
    360,
    390,
    420,
    480,
    540,
    570,
    600,
    660,
    720,
    780,
)


def _oclean_tz_index(offset_minutes: int) -> int:
    """Return the 1-based Oclean timezone index closest to *offset_minutes*."""
    return min(range(len(_TZ_OFFSETS_MIN)), key=lambda i: abs(_TZ_OFFSETS_MIN[i] - offset_minutes)) + 1


class OcleanCoordinator(DataUpdateCoordinator[OcleanDeviceData]):
    """Coordinator that polls the Oclean toothbrush via BLE every N seconds."""

    def __init__(
        self,
        hass: HomeAssistant,
        mac_address: str,
        device_name: str,
        update_interval: int,
        poll_windows: str = "",
        post_brush_cooldown_h: int = 0,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=update_interval) if update_interval > 0 else None,
        )
        self._mac = mac_address
        self._device_name = device_name
        # Carries raw dict across polls so sensors keep their last value on failure
        self._last_raw: dict[str, Any] = {}
        # Track whether the last poll succeeded
        self.last_poll_successful: bool = False

        # Per-instance logger that automatically prepends [MODEL/XX] to every
        # message so multi-device log files can be filtered per entity.
        self._log: logging.LoggerAdapter = _CoordLoggerAdapter(_LOGGER, {"coord": self})

        # Persistent storage: tracks last imported session timestamp per device.
        # Storage key is unique per MAC so multi-device setups don't conflict.
        _mac_slug = mac_address.replace(":", "_").lower()
        self._store: Store = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{_mac_slug}")
        # Unix timestamp (seconds) of the newest session already imported into HA statistics.
        # Sessions with last_brush_time > this value are considered new.
        self._last_session_ts: int = 0
        self._store_loaded: bool = False

        # User-controlled device settings (write-only; state persisted locally).
        self._area_remind: bool | None = None
        self._over_pressure: bool | None = None
        self._remind_switch: bool | None = None
        self._running_switch: bool | None = None
        self._brush_head_max_days: int | None = None
        # Child birthday / user-info (CMD 0211).  sex: 0=unknown 1=male 2=female.
        self._birthday_date: datetime.date | None = None
        self._birthday_sex: int = 1
        # Software brush-head session counter: counts new sessions since the last
        # brush-head reset when the device does not expose a hardware counter via 0302.
        self._brush_head_sw_count: int = 0
        # Last-applied brush scheme pnum (OCLEANY3M only); None = never set.
        self._active_scheme_pnum: int | None = None

        # Active device protocol profile – selected after the first DIS read.
        # UNKNOWN is the safe fallback: subscribes all chars, sends all commands.
        self._protocol: DeviceProtocol = UNKNOWN

        # Unix timestamp of the last successful DIS read.  0.0 = never read.
        # DIS values (model, firmware, hw revision) are stable but could change
        # after a firmware update, so we re-read them every 24 hours.
        self._dis_last_read_ts: float = 0.0

        # Smart polling: optional time windows + post-brush cooldown.
        self._poll_windows: list[tuple[_dtime, _dtime]] = _parse_poll_windows(poll_windows)
        self._post_brush_cooldown_s: int = post_brush_cooldown_h * 3600
        # Unix timestamp until which polls are suppressed after a new session.
        self._cooldown_until: float = 0.0

    # ------------------------------------------------------------------
    # DataUpdateCoordinator interface
    # ------------------------------------------------------------------

    async def _async_update_data(self) -> OcleanDeviceData:
        """Fetch the latest data from the device."""
        # Load persisted session timestamp on first poll
        if not self._store_loaded:
            await self._load_store()

        # Smart polling: skip BLE connection when outside a configured time window
        # or while the post-brush cooldown is active.
        # Exception: always poll when no cached data exists so that the initial
        # setup (or first poll after a restart with no persisted store) completes
        # regardless of configured windows.
        skip_reason = self._poll_skip_reason()
        if skip_reason and self._last_raw:
            self._log.debug("poll skipped: %s", skip_reason)
            return OcleanDeviceData.from_dict(self._last_raw)
        if skip_reason:
            self._log.debug(
                "poll restriction '%s' bypassed – no cached data, polling anyway",
                skip_reason,
            )

        try:
            raw = await self._poll_device()
            return OcleanDeviceData.from_dict(raw)
        except Exception as err:
            # Catch all exceptions (BleakError, TimeoutError, IndexError from
            # habluetooth proxy backend, etc.) so HA can keep retrying rather
            # than crashing the integration.
            self._log.debug(
                "poll failed: %s (%s)\n%s",
                err,
                type(err).__name__,
                traceback.format_exc(),
            )
            self.last_poll_successful = False
            if self._last_raw:
                # Return stale data; sensors will remain available with old values
                return OcleanDeviceData.from_dict(self._last_raw)
            raise UpdateFailed(f"Oclean device not reachable: {err}") from err

    # ------------------------------------------------------------------
    # Public API for button entities
    # ------------------------------------------------------------------

    async def async_reset_brush_head(self) -> None:
        """Connect to the device and send CMD_CLEAR_BRUSH_HEAD (020F).

        Subscribes to response characteristics before sending the command so
        any ACK notification is captured and logged for protocol research.
        Called by the "Reset Brush Head" button entity.
        Raises BleakError if the device cannot be reached.
        """
        ble_device = self._resolve_ble_device()
        client = await establish_connection(
            BleakClient,
            ble_device,
            self._device_name,
            max_attempts=3,
        )
        try:
            await asyncio.sleep(BLE_POST_CONNECT_DELAY)

            def _ack_handler(_sender: Any, raw: bytearray) -> None:
                data = bytes(raw)
                parsed = parse_notification(data)
                self._log.info(
                    "020F ACK notification: raw=%s parsed=%s",
                    data.hex(),
                    parsed,
                )

            subscribed_ack: list[str] = []
            for char_uuid in (READ_NOTIFY_CHAR_UUID, RECEIVE_BRUSH_UUID):
                try:
                    await client.start_notify(char_uuid, _ack_handler)
                    subscribed_ack.append(char_uuid)
                except Exception:  # noqa: BLE001
                    pass

            await client.write_gatt_char(self._protocol.write_char, CMD_CLEAR_BRUSH_HEAD, response=True)
            self._log.info("brush head counter reset sent")
            await asyncio.sleep(2.0)

            for char_uuid in subscribed_ack:
                with contextlib.suppress(Exception):
                    await client.stop_notify(char_uuid)
        finally:
            with contextlib.suppress(Exception):
                await client.disconnect()

        self._brush_head_sw_count = 0
        await self._save_store()
        if self.data is not None:
            self.async_set_updated_data(dataclasses.replace(self.data, brush_head_usage=0))

    async def async_sync_time(self) -> None:
        """Connect to the device and sync the current time (020E + BE timestamp).

        Called by the "Sync Time" button entity.
        Raises BleakError if the device cannot be reached.
        """
        ble_device = self._resolve_ble_device()
        client = await establish_connection(
            BleakClient,
            ble_device,
            self._device_name,
            max_attempts=3,
        )
        try:
            await asyncio.sleep(BLE_POST_CONNECT_DELAY)
            subscribed: list[str] = []
            for char_uuid in self._protocol.notify_chars:
                try:
                    await client.start_notify(char_uuid, lambda _s, _r: None)
                    subscribed.append(char_uuid)
                except Exception:  # noqa: BLE001
                    pass
            await self._calibrate_time(client)
            for char_uuid in subscribed:
                with contextlib.suppress(Exception):
                    await client.stop_notify(char_uuid)
        finally:
            with contextlib.suppress(Exception):
                await client.disconnect()

    @property
    def area_remind(self) -> bool | None:
        """Return the last-written area-reminder state, or None if never set."""
        return self._area_remind

    @property
    def over_pressure(self) -> bool | None:
        """Return the last-written over-pressure alert state, or None if never set."""
        return self._over_pressure

    @property
    def remind_switch(self) -> bool | None:
        """Return the last-written remind-switch state, or None if never set."""
        return self._remind_switch

    @property
    def running_switch(self) -> bool | None:
        """Return the last-written running-switch state, or None if never set."""
        return self._running_switch

    @property
    def brush_head_max_days(self) -> int | None:
        """Return the last-written brush-head max-lifetime in days, or None if never set."""
        return self._brush_head_max_days

    async def async_set_area_remind(self, enabled: bool) -> None:
        """Connect and write CMD_AREA_REMIND (020D) to the device.

        Called by the Area Reminder switch entity.  State is persisted so the
        switch shows the correct value after HA restarts.
        """
        ble_device = self._resolve_ble_device()
        client = await establish_connection(
            BleakClient,
            ble_device,
            self._device_name,
            max_attempts=3,
        )
        cmd = CMD_AREA_REMIND + bytes([0x01 if enabled else 0x00])
        try:
            await asyncio.sleep(BLE_POST_CONNECT_DELAY)
            await self._write_standalone(client, cmd)
            self._log.info("area remind set to %s", enabled)
        finally:
            with contextlib.suppress(Exception):
                await client.disconnect()
        self._area_remind = enabled
        await self._save_store()

    async def async_set_over_pressure(self, enabled: bool) -> None:
        """Connect and write CMD_OVER_PRESSURE (0212) to the device.

        Called by the Over-Pressure Alert switch entity.  State is persisted so
        the switch shows the correct value after HA restarts.
        """
        ble_device = self._resolve_ble_device()
        client = await establish_connection(
            BleakClient,
            ble_device,
            self._device_name,
            max_attempts=3,
        )
        cmd = CMD_OVER_PRESSURE + bytes([0x01 if enabled else 0x00])
        try:
            await asyncio.sleep(BLE_POST_CONNECT_DELAY)
            await self._write_standalone(client, cmd)
            self._log.info("over pressure set to %s", enabled)
        finally:
            with contextlib.suppress(Exception):
                await client.disconnect()
        self._over_pressure = enabled
        await self._save_store()

    async def async_set_remind_switch(self, enabled: bool) -> None:
        """Connect and write CMD_REMIND_SWITCH (0239) to the device.

        Called by the Brushing Reminder switch entity.  State is persisted so
        the switch shows the correct value after HA restarts.
        """
        ble_device = self._resolve_ble_device()
        client = await establish_connection(
            BleakClient,
            ble_device,
            self._device_name,
            max_attempts=3,
        )
        cmd = CMD_REMIND_SWITCH + bytes([0x01 if enabled else 0x00])
        try:
            await asyncio.sleep(BLE_POST_CONNECT_DELAY)
            await self._write_standalone(client, cmd)
            self._log.info("remind switch set to %s", enabled)
        finally:
            with contextlib.suppress(Exception):
                await client.disconnect()
        self._remind_switch = enabled
        await self._save_store()

    async def async_set_running_switch(self, enabled: bool) -> None:
        """Connect and write CMD_RUNNING_SWITCH (0240) to the device.

        Called by the Auto Power-Off Timer switch entity.  State is persisted so
        the switch shows the correct value after HA restarts.
        """
        ble_device = self._resolve_ble_device()
        client = await establish_connection(
            BleakClient,
            ble_device,
            self._device_name,
            max_attempts=3,
        )
        cmd = CMD_RUNNING_SWITCH + bytes([0x01 if enabled else 0x00])
        try:
            await asyncio.sleep(BLE_POST_CONNECT_DELAY)
            await self._write_standalone(client, cmd)
            self._log.info("running switch set to %s", enabled)
        finally:
            with contextlib.suppress(Exception):
                await client.disconnect()
        self._running_switch = enabled
        await self._save_store()

    async def async_set_brush_head_max_days(self, days: int) -> None:
        """Connect and write CMD_BRUSH_HEAD_MAX_DAYS (0217) to the device.

        Called by the Brush Head Max Lifetime number entity.  State is persisted
        so the number shows the correct value after HA restarts.
        """
        ble_device = self._resolve_ble_device()
        client = await establish_connection(
            BleakClient,
            ble_device,
            self._device_name,
            max_attempts=3,
        )
        cmd = CMD_BRUSH_HEAD_MAX_DAYS + days.to_bytes(2, "big")
        try:
            await asyncio.sleep(BLE_POST_CONNECT_DELAY)
            await self._write_standalone(client, cmd)
            self._log.info("brush head max days set to %d", days)
        finally:
            with contextlib.suppress(Exception):
                await client.disconnect()
        self._brush_head_max_days = days
        await self._save_store()

    @property
    def birthday_date(self) -> datetime.date | None:
        """Return the last-written birthday date, or None if never set."""
        return self._birthday_date

    @property
    def birthday_sex(self) -> int:
        """Return the last-written sex value (0=unknown, 1=male, 2=female)."""
        return self._birthday_sex

    @property
    def active_scheme_pnum(self) -> int | None:
        """Return the pnum of the last-applied brush scheme, or None if never set."""
        return self._active_scheme_pnum

    async def async_set_birthday(
        self,
        birthday: datetime.date | None = None,
        sex: int | None = None,
    ) -> None:
        """Persist birthday/sex and write CMD_SET_BIRTHDAY (0211) to the device.

        Byte layout (4 bytes after the 2-byte prefix):
          [0] sex       – 0=unknown, 1=male, 2=female (clamped 0-2)
          [1] age       – years since birth, clamped to [3, 18]
          [2] month     – 1-12
          [3] day       – 1-31

        Either argument may be omitted to update only one of the two values.
        The BLE command is only sent when a birthday date is available.
        Source: C3376s.Z / mo5315Z + DateUtils.getBirthdayInfo (APK).
        """
        if birthday is not None:
            self._birthday_date = birthday
        if sex is not None:
            self._birthday_sex = max(0, min(2, sex))
        await self._save_store()

        if self._birthday_date is None:
            return  # nothing to send without a birthday

        today = datetime.date.today()
        age = max(3, min(18, today.year - self._birthday_date.year))
        payload = bytes([self._birthday_sex, age, self._birthday_date.month, self._birthday_date.day])
        cmd = CMD_SET_BIRTHDAY + payload

        ble_device = self._resolve_ble_device()
        client = await establish_connection(
            BleakClient,
            ble_device,
            self._device_name,
            max_attempts=3,
        )
        try:
            await asyncio.sleep(BLE_POST_CONNECT_DELAY)
            await self._write_standalone(client, cmd)
            self._log.info(
                "birthday set: date=%s sex=%d age=%d",
                self._birthday_date,
                self._birthday_sex,
                age,
            )
        finally:
            if client.is_connected:
                await client.disconnect()

    async def async_set_brush_scheme(self, pnum: int) -> None:
        """Connect and send the SetBrushScheme command (0206) to the device.

        Builds the BLE packet following APK AbstractC0002b.m28p() and sends it
        via the device's write characteristic.  For schemes with more than 4 steps
        the payload exceeds 20 bytes and is split into two GATT writes.
        Called by the Brush Scheme select entity.
        Raises ValueError for unknown pnums, BleakError on connection failure.
        """
        model_id = self.data.model_id if self.data else None
        scheme_dict = SCHEMES_BY_MODEL.get(model_id or "", OCLEANY3M_SCHEMES)
        scheme = scheme_dict.get(pnum)
        if scheme is None:
            raise ValueError(f"Unknown scheme pnum {pnum} for model {model_id}")
        name, steps = scheme
        packets = _build_scheme_packets(pnum, steps)

        ble_device = self._resolve_ble_device()
        client = await establish_connection(
            BleakClient,
            ble_device,
            self._device_name,
            max_attempts=3,
        )
        try:
            await asyncio.sleep(BLE_POST_CONNECT_DELAY)

            def _ack_handler(_sender: Any, raw: bytearray) -> None:
                data = bytes(raw)
                parsed = parse_notification(data)
                self._log.debug(
                    "0206 ACK notification: raw=%s parsed=%s",
                    data.hex(),
                    parsed,
                )

            subscribed: list[str] = []
            for char_uuid in (READ_NOTIFY_CHAR_UUID, RECEIVE_BRUSH_UUID):
                try:
                    await client.start_notify(char_uuid, _ack_handler)
                    subscribed.append(char_uuid)
                except Exception:  # noqa: BLE001
                    pass
            for i, pkt in enumerate(packets):
                await client.write_gatt_char(self._protocol.write_char, pkt, response=True)
                if i < len(packets) - 1:
                    await asyncio.sleep(0.05)
            self._log.info(
                "brush scheme set to pnum=%d (%s), %d packet(s): %s",
                pnum,
                name,
                len(packets),
                " ".join(p.hex() for p in packets),
            )
            # Wait for the device to process the command and send an ACK
            # notification before disconnecting.  The APK (C3344d) blocks until
            # a notification arrives; without this wait the device may discard
            # the in-flight command when the connection drops.
            await asyncio.sleep(2.0)
            for char_uuid in subscribed:
                with contextlib.suppress(Exception):
                    await client.stop_notify(char_uuid)
        finally:
            with contextlib.suppress(Exception):
                await client.disconnect()
        self._active_scheme_pnum = pnum
        await self._save_store()

    async def _write_standalone(self, client: BleakClient, cmd: bytes) -> None:
        """Subscribe to notify chars, write *cmd* to write_char, then unsubscribe.

        On TYPE1 devices the standalone write_char is fbb85 (APK C3376s f12501k).
        Subscribing to fbb90/fbb86 first mirrors the poll setup and ensures the
        device is ready to accept writes on all protocols.
        """
        subscribed: list[str] = []
        for char_uuid in self._protocol.notify_chars:
            try:
                await client.start_notify(char_uuid, lambda _s, _r: None)
                subscribed.append(char_uuid)
            except Exception:  # noqa: BLE001
                pass
        await client.write_gatt_char(self._protocol.write_char, cmd, response=True)
        for char_uuid in subscribed:
            with contextlib.suppress(Exception):
                await client.stop_notify(char_uuid)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _poll_skip_reason(self) -> str | None:
        """Return a human-readable reason to skip this poll, or None to proceed.

        Checks (in order):
          1. Post-brush cooldown: skip for N hours after the last new session.
          2. Time windows: skip if the current time is outside all configured windows.
        """
        now_ts = time.time()
        if self._cooldown_until and now_ts < self._cooldown_until:
            remaining_h = (self._cooldown_until - now_ts) / 3600
            return f"post-brush cooldown active ({remaining_h:.1f} h remaining)"

        if self._poll_windows:
            now_t = datetime.datetime.now().time()
            if not any(_in_window(s, e, now_t) for s, e in self._poll_windows):
                windows_str = ", ".join(f"{s.strftime('%H:%M')}-{e.strftime('%H:%M')}" for s, e in self._poll_windows)
                return f"outside poll windows ({windows_str})"

        return None

    def _resolve_ble_device(self) -> BLEDevice:
        """BLEDevice from HA Bluetooth registry; raises BleakError if not found."""
        service_info = bluetooth.async_last_service_info(self.hass, self._mac, connectable=True)
        if service_info is not None:
            return service_info.device
        device = bluetooth.async_ble_device_from_address(self.hass, self._mac, connectable=True)
        if device is None:
            raise BleakError(
                f"Oclean {self._mac} not found in HA bluetooth registry. "
                "Turn on the toothbrush and wait ~30 s for the proxy to see it, "
                "then the next poll will connect automatically."
            )
        return device

    async def _load_store(self) -> None:
        """Load persisted data from HA storage."""
        stored = await self._store.async_load()
        if stored:
            self._last_session_ts = stored.get("last_session_ts", 0)
            self._area_remind = stored.get("area_remind")
            self._over_pressure = stored.get("over_pressure")
            self._remind_switch = stored.get("remind_switch")
            self._running_switch = stored.get("running_switch")
            self._brush_head_max_days = stored.get("brush_head_max_days")
            self._brush_head_sw_count = stored.get("brush_head_sw_count", 0)
            self._active_scheme_pnum = stored.get("active_scheme_pnum")
            raw_birthday = stored.get("birthday_date")
            if raw_birthday:
                with contextlib.suppress(ValueError):
                    self._birthday_date = datetime.date.fromisoformat(raw_birthday)
            self._birthday_sex = stored.get("birthday_sex", 1)
            last_session = stored.get("last_session", {})
            if last_session:
                self._last_raw.update(last_session)
            self._log.debug(
                "loaded store: last_session_ts=%d",
                self._last_session_ts,
            )
        self._store_loaded = True

    async def _save_store(self) -> None:
        """Persist coordinator state to HA storage."""
        last_session = {
            k: self._last_raw[k] for k in _PERSISTENT_KEYS if k in self._last_raw and self._last_raw[k] is not None
        }
        await self._store.async_save(
            {
                "last_session_ts": self._last_session_ts,
                "area_remind": self._area_remind,
                "over_pressure": self._over_pressure,
                "remind_switch": self._remind_switch,
                "running_switch": self._running_switch,
                "brush_head_max_days": self._brush_head_max_days,
                "brush_head_sw_count": self._brush_head_sw_count,
                "active_scheme_pnum": self._active_scheme_pnum,
                "birthday_date": self._birthday_date.isoformat() if self._birthday_date else None,
                "birthday_sex": self._birthday_sex,
                "last_session": last_session,
            }
        )

    async def _poll_device(self) -> dict[str, Any]:
        """Connect to the device, read data, then disconnect."""
        collected: dict[str, Any] = {}

        # Resolve a BLEDevice through HA's bluetooth layer (covers local adapters
        # AND active ESPHome proxies).
        ble_device = self._resolve_ble_device()

        client = await establish_connection(
            BleakClient,
            ble_device,
            self._device_name,
            max_attempts=3,
        )

        all_sessions: list[dict[str, Any]] = []
        try:
            all_sessions = await asyncio.wait_for(
                self._setup_and_read(client, collected),
                timeout=BLE_POLL_TOTAL_TIMEOUT,
            )
        except asyncio.TimeoutError:
            self._log.warning(
                "poll exceeded total timeout of %ds – aborting",
                BLE_POLL_TOTAL_TIMEOUT,
            )
            raise
        finally:
            with contextlib.suppress(Exception):
                await client.disconnect()

        self.last_poll_successful = True
        collected[DATA_LAST_POLL] = int(time.time())

        # Count new sessions before _import_new_sessions updates _last_session_ts
        new_session_count = sum(1 for s in all_sessions if s.get(DATA_LAST_BRUSH_TIME, 0) > self._last_session_ts)

        # Import new sessions into HA long-term statistics
        if all_sessions:
            mac_slug = self._mac.replace(":", "_").lower()
            new_ts = await import_new_sessions(
                self.hass, mac_slug, self._device_name, all_sessions, self._last_session_ts
            )
            if new_ts > self._last_session_ts:
                self._last_session_ts = new_ts
                self._log.debug("updated last_session_ts to %d", self._last_session_ts)

        # Post-brush cooldown: pause polling for N hours after a new session
        if new_session_count > 0 and self._post_brush_cooldown_s > 0:
            self._cooldown_until = time.time() + self._post_brush_cooldown_s
            self._log.info(
                "post-brush cooldown: %d new session(s) detected, pausing polls for %.1f h",
                new_session_count,
                self._post_brush_cooldown_s / 3600,
            )

        # Software brush-head counter: fallback when device does not report headUsedTimes
        # via 0302. Incremented per new session; hardware value from 0302 takes priority
        # and keeps the SW counter in sync so switching between HW/SW is seamless.
        if DATA_BRUSH_HEAD_USAGE in collected:
            self._brush_head_sw_count = collected[DATA_BRUSH_HEAD_USAGE]
        else:
            if new_session_count > 0:
                self._brush_head_sw_count += new_session_count
                self._log.debug(
                    "sw brush-head count incremented by %d → %d", new_session_count, self._brush_head_sw_count
                )
            collected[DATA_BRUSH_HEAD_USAGE] = self._brush_head_sw_count

        # Merge with last known persistent data, then overwrite with fresh values
        merged = {**{k: self._last_raw.get(k) for k in _PERSISTENT_KEYS}, **collected}

        # Clear stale enrichment fields (score/areas/pressure) when a NEW session is
        # detected but the current poll did not deliver enrichment data.  This prevents
        # showing a previous session's score/areas alongside the current session's
        # timestamp (e.g. OCLEANY3P inline mode where session_count=0 omits enrichment).
        new_ts = collected.get(DATA_LAST_BRUSH_TIME, 0)
        prev_ts = self._last_raw.get(DATA_LAST_BRUSH_TIME) or 0
        if new_ts and new_ts > prev_ts:
            for key in _ENRICHMENT_KEYS:
                if key not in collected:
                    merged.pop(key, None)

        self._last_raw = merged

        # Persist after every successful poll so that battery, model, and session
        # fields survive an HA restart even when no new sessions were imported.
        await self._save_store()

        return merged

    async def _setup_and_read(
        self,
        client: BleakClient,
        collected: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Perform GATT operations and return all fetched brush session records."""
        cached_model = self._last_raw.get(DATA_MODEL_ID)
        self._protocol = protocol_for_model(cached_model)
        self._log.debug(
            "poll start: mac=%s ts=%d protocol=%s (model=%s)",
            self._mac,
            int(time.time()),
            self._protocol.name,
            cached_model or "unknown",
        )
        # Delay after connect: gives habluetooth's proxy backend time to finish
        # processing the GATT service table before we start issuing commands.
        await asyncio.sleep(BLE_POST_CONNECT_DELAY)

        all_sessions: list[dict[str, Any]] = []
        seen_ts: set[int] = set()
        session_received = asyncio.Event()
        handler, flush_pending = self._make_notification_handler(collected, all_sessions, seen_ts, session_received)

        await self._run_ble_queries(client, collected, all_sessions, session_received, handler, flush_pending)
        self._finalize_sessions(collected, all_sessions)
        return all_sessions

    def _make_notification_handler(
        self,
        collected: dict[str, Any],
        all_sessions: list[dict[str, Any]],
        seen_ts: set[int],
        session_received: asyncio.Event,
    ) -> tuple[Callable[[Any, bytearray], None], Callable[[], None]]:
        """Return a (handler, flush_pending) pair for BLE notification processing.

        *handler* is the BLE notification callback that accumulates session data.
        It parses each notification, applies the "newer timestamp wins" policy to
        prevent a stale 5a00 push from overwriting a more-recent 0307 timestamp,
        merges fields into *collected*, and appends new sessions to *all_sessions*.

        *flush_pending* flushes any complete 42-byte records already accumulated
        in the *B# reassembly buffer.  Call it after the session-wait timeout to
        recover records that arrived before the BLE connection is torn down.

        Multi-packet reassembly (*B# format):
        OCLEANY3 sends session records split across multiple BLE packets: a
        0307 *B# header packet with record_count + inline data, followed by
        raw continuation packets until all record_count × 42 bytes are received.
        The handler detects the header, accumulates continuation chunks, and
        flushes complete records through the appropriate parse function when the
        buffer is full.  OCLEANY3M/OCLEANY3 encode year_base (year−2000) at byte 0
        and use ``parse_t1_c3385w0_record`` (tooth-zone areas at bytes 11-19).
        OCLEANY3P with non-zero year_base uses ``parse_t1_c3352g_record`` (bytes 11-19
        are pressureRatio, not areas; area data comes from 021f push notifications).
        OCLEANY3P with year_base=0x00 uses ``parse_y3p_stream_record``.
        """
        _log = self._log  # capture for use in the closure

        # Mutable reassembly state – use a dict to avoid 'nonlocal' for primitives.
        _t1: dict[str, Any] = {
            "in_progress": False,
            "buf": bytearray(),
            "expected": 0,
            "parse_fn": parse_t1_c3385w0_record,
        }

        # Magic header bytes for the *B# multi-packet format (after 0307 prefix).
        _T1_MAGIC = b"\x2a\x42\x23"  # '*B#'

        def _accept(parsed: dict[str, Any]) -> None:
            """Merge one parsed dict into collected/all_sessions (shared logic)."""
            if not parsed:
                return
            incoming_ts = parsed.get(DATA_LAST_BRUSH_TIME)
            # Only update collected when the incoming data is at least as new as
            # what we already have.  Older timestamped sessions (e.g. from *B#
            # pagination) are appended to all_sessions for stats import but must
            # not overwrite score/areas/pressure of the most-recent session in
            # collected.  Enrichment notifications (0000, 2604) never carry a
            # timestamp (incoming_ts = None) so they always update collected.
            if incoming_ts is None or incoming_ts >= collected.get(DATA_LAST_BRUSH_TIME, 0):
                collected.update(parsed)
            if incoming_ts and incoming_ts not in seen_ts:
                seen_ts.add(incoming_ts)
                all_sessions.append(dict(parsed))
                session_received.set()

        def _flush_t1_buffer() -> None:
            """Parse all complete 42-byte records from the reassembly buffer."""
            buf = bytes(_t1["buf"])
            parse_fn = _t1["parse_fn"]
            _t1["in_progress"] = False
            _t1["buf"] = bytearray()
            _t1["expected"] = 0
            _t1["parse_fn"] = parse_t1_c3385w0_record
            num_records = len(buf) // T1_C3352G_RECORD_SIZE
            _log.debug("*B# reassembly complete: parsing %d record(s)", num_records)
            for i in range(num_records):
                chunk = buf[i * T1_C3352G_RECORD_SIZE : (i + 1) * T1_C3352G_RECORD_SIZE]
                _accept(parse_fn(chunk))

        def handler(_sender: Any, raw: bytearray) -> None:
            data = bytes(raw)
            _log.debug("notification raw: %s", data.hex())

            # --- Continuation packet for active *B# reassembly ---
            # While reassembly is active, every incoming packet is treated as
            # continuation data regardless of its first two bytes. Byte count
            # is the only reliable discriminator: continuation chunks contain
            # raw record bytes that can coincidentally match any known prefix.
            if _t1["in_progress"]:
                _t1["buf"].extend(data)
                _log.debug(
                    "*B# continuation: +%d bytes (%d/%d)",
                    len(data),
                    len(_t1["buf"]),
                    _t1["expected"],
                )
                if len(_t1["buf"]) >= _t1["expected"]:
                    _flush_t1_buffer()
                return

            # --- Normal notification dispatch ---
            parsed = parse_notification(data)
            _log.debug("notification parsed: %s", parsed)

            # --- Check for *B# multi-packet header (0307 + *B# magic + count) ---
            # payload[3:5] = record_count (2-byte BE).
            # payload[5] = record byte 0 (year_base for OCLEANY3; 0x00 for OCLEANY3P).
            # Both devices use the same *B# format; year_base=0x00 selects the Y3P parser.
            if len(data) >= 8 and data[2:5] == _T1_MAGIC:
                payload = data[2:]  # strip 0307 prefix
                record_count = (payload[3] << 8) | payload[4]
                if record_count > 0:
                    total_expected = record_count * T1_C3352G_RECORD_SIZE
                    inline = bytearray(payload[5:])  # bytes already in this packet (from record byte 0)
                    _t1["buf"] = inline
                    _t1["expected"] = total_expected
                    _t1["in_progress"] = True
                    if payload[5] == 0x00:
                        _t1["parse_fn"] = parse_y3p_stream_record
                        _parser_name = "Y3P-stream"
                    elif (collected.get(DATA_MODEL_ID) or "").startswith("OCLEANY3P"):
                        _t1["parse_fn"] = parse_t1_c3352g_record
                        _parser_name = "C3352g (Y3P)"
                    else:
                        _t1["parse_fn"] = parse_t1_c3385w0_record
                        _parser_name = "C3385w0 (Y3M/Y3)"
                    _log.debug(
                        "*B# header: count=%d, expected=%d bytes, inline=%d bytes, parser=%s",
                        record_count,
                        total_expected,
                        len(inline),
                        _parser_name,
                    )
                    if len(inline) >= total_expected:
                        _flush_t1_buffer()
                    # parse_notification returned {} for this case (deferred branch);
                    # do not call _accept – reassembly handles the data.
                    return

            _accept(parsed)

        def flush_pending() -> None:
            """Flush any complete 42-byte records buffered so far.

            Called after the BLE session-wait timeout to recover records that
            arrived before the connection is torn down but before the full
            expected byte count was reached (e.g. slow ESPHome BLE proxy).
            """
            if _t1["in_progress"] and _t1["buf"]:
                available = len(_t1["buf"])
                complete = (available // T1_C3352G_RECORD_SIZE) * T1_C3352G_RECORD_SIZE
                _log.debug(
                    "*B# partial flush: %d/%d bytes, flushing %d complete record(s)",
                    available,
                    _t1["expected"],
                    available // T1_C3352G_RECORD_SIZE,
                )
                _t1["expected"] = complete  # limit flush to complete records only
                _flush_t1_buffer()

        return handler, flush_pending

    async def _run_ble_queries(
        self,
        client: BleakClient,
        collected: dict[str, Any],
        all_sessions: list[dict[str, Any]],
        session_received: asyncio.Event,
        handler: Callable[[Any, bytearray], None],
        flush_pending: Callable[[], None],
    ) -> None:
        """Execute the full GATT operation sequence for one poll.

        Mirrors the Java SDK order (C3335a / C3340b1):
          1. Time calibration  (020E + BE timestamp)   – mo5289B
          2. DIS read / cache
          3. Subscribe to notification characteristics
          3b. Subscribe to 0x2A19 battery notifications (captures push before step 8)
          4. Status + running-data query commands
          5. READ fallback for devices without CCCD (e.g. OCLEANA1)
          6. Session pagination (0309)
          7. Enrichment wait if sessions were received
          8. Battery read (skipped if notification already delivered the value)
        """
        await self._calibrate_time(client)
        await self._read_device_info_service(client, collected)
        subscribed = await self._subscribe_notifications(client, handler)
        await self._subscribe_battery_notifications(client, collected)

        # Determine if notification subscriptions for data characteristics failed.
        # When none of the protocol's notify chars could be subscribed (persistent
        # "Notify acquired" on BlueZ), shorten the notification wait and use a
        # polling loop instead — the device still processes commands sent via fbb89
        # and updates the characteristic values even without active subscriptions.
        notify_chars_ok = any(c in subscribed for c in self._protocol.notify_chars)
        if notify_chars_ok:
            await self._send_query_commands(client, session_received)
        else:
            self._log.debug("no data characteristics subscribed – using shortened wait + polling fallback")
            await self._send_query_commands(client, session_received, notify_wait=BLE_NOTIFICATION_WAIT_NO_SUB)

        # If the session-wait timed out while a *B# stream was mid-flight (e.g.
        # slow ESPHome proxy), flush whatever complete records arrived so far.
        flush_pending()
        # Fallback for devices (e.g. OCLEANA1) where READ_NOTIFY_CHAR_UUID has no
        # CCCD and cannot be subscribed; poll the characteristic directly instead.
        if READ_NOTIFY_CHAR_UUID not in subscribed:
            await self._read_response_char_fallback(client, handler)
        # Polling fallback for TYPE1 devices where RECEIVE_BRUSH_UUID subscription
        # failed (e.g. BlueZ "Notify acquired" that persists even after CCCD clear).
        # Poll fbb90 in a loop to catch the response the device writes after
        # processing the 0307 command.
        if RECEIVE_BRUSH_UUID in self._protocol.notify_chars and RECEIVE_BRUSH_UUID not in subscribed:
            await self._poll_receive_brush_fallback(client, handler, session_received)
        await self._paginate_sessions(client, all_sessions, session_received)
        # Allow the device extra time to push enrichment notifications
        # (0000 score, 2604 zone pressures) that arrive unsolicited after
        # the session response.  Wait when ANY session data was received –
        # including inline-mode 0307 responses that update *collected* but
        # may not add new entries to all_sessions (e.g. repeat polls with
        # the same timestamp on OCLEANV1a / OCLEANX20).  (issues #37, #81)
        has_session_data = bool(all_sessions) or DATA_LAST_BRUSH_TIME in collected
        if has_session_data:
            await asyncio.sleep(BLE_ENRICHMENT_WAIT)
        await self._read_battery_and_unsubscribe(client, collected)

    def _finalize_sessions(
        self,
        collected: dict[str, Any],
        all_sessions: list[dict[str, Any]],
    ) -> None:
        """Merge enrichment fields into the newest session and log session summary.

        Enrichment notifications (0000 → score, 2604 → areas/pressure) carry no
        timestamp so they land in *collected* but are not captured in all_sessions.
        We merge them into the newest session so stats import receives complete data.
        """
        if all_sessions:
            latest = max(all_sessions, key=lambda s: s.get(DATA_LAST_BRUSH_TIME, 0))
            enriched = {k: collected[k] for k in _ENRICHMENT_KEYS if k in collected and k not in latest}
            if enriched:
                latest.update(enriched)
                self._log.debug("session snapshot enriched: %s", list(enriched.keys()))

        self._log.debug(
            "fetched %d session(s) total from device (last_known_ts=%d)",
            len(all_sessions),
            self._last_session_ts,
        )
        for i, s in enumerate(all_sessions):
            ts = s.get(DATA_LAST_BRUSH_TIME, 0)
            status = "NEW" if ts > self._last_session_ts else "known"
            self._log.debug(
                " session[%d]: ts=%d (%s)  %s",
                i,
                ts,
                datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S") if ts else "n/a",
                status,
            )

    async def _read_device_info_service(self, client: BleakClient, collected: dict[str, Any]) -> None:
        """Read BLE Device Information Service (0x180A) characteristics.

        Populates model_id, hw_revision, and sw_version in collected.
        Also updates the HA device registry so the info panel shows the
        firmware version without needing a dedicated sensor.
        """
        # Firmware/model info rarely changes (only after a firmware update).
        # Re-read from the device at most once every 24 h; use the cached values
        # from _last_raw for all other polls to keep the BLE session short.
        dis_keys = (DATA_MODEL_ID, DATA_HW_REVISION, DATA_SW_VERSION)
        age = time.time() - self._dis_last_read_ts
        if self._dis_last_read_ts > 0 and age < _DIS_REFRESH_INTERVAL:
            for key in dis_keys:
                cached = self._last_raw.get(key)
                if cached:
                    collected[key] = cached
            self._log.debug(
                "DIS skipped (cached, %.0f h until refresh: model=%s sw=%s)",
                (_DIS_REFRESH_INTERVAL - age) / 3600,
                self._last_raw.get(DATA_MODEL_ID),
                self._last_raw.get(DATA_SW_VERSION),
            )
            return

        dis_chars = {
            DATA_MODEL_ID: DIS_MODEL_UUID,
            DATA_HW_REVISION: DIS_HW_REV_UUID,
            DATA_SW_VERSION: DIS_SW_REV_UUID,
        }
        got_fresh_dis = False
        for key, uuid in dis_chars.items():
            try:
                raw = await client.read_gatt_char(uuid)
                collected[key] = raw.decode("utf-8").strip("\x00").strip()
                self._log.debug("DIS %s: %s", key, collected[key])
                got_fresh_dis = True
            except Exception as err:  # noqa: BLE001
                self._log.debug("DIS read skipped for %s: %s", uuid[-8:], err)
                # Fall back to cached value so a transient BLE error (e.g.
                # "Insufficient authorization") does not reset the protocol
                # profile to UNKNOWN and break the rest of the poll.
                cached = self._last_raw.get(key)
                if cached:
                    collected[key] = cached

        # Only advance the refresh timestamp when we actually read fresh data.
        # If all reads failed we leave _dis_last_read_ts unchanged so the next
        # poll will retry the DIS read rather than waiting another 24 h.
        if got_fresh_dis:
            self._dis_last_read_ts = time.time()

        # Update protocol profile based on the freshly read model ID.
        model_id = collected.get(DATA_MODEL_ID)
        new_protocol = protocol_for_model(model_id)
        if new_protocol is not self._protocol:
            self._log.debug(
                "protocol updated: %s → %s (model=%s)",
                self._protocol.name,
                new_protocol.name,
                model_id,
            )
            self._protocol = new_protocol
        if new_protocol is UNKNOWN and model_id:
            self._log.warning(
                "unrecognised model ID '%s' on %s – using generic fallback protocol. "
                "Basic sensors (battery, last brush time) may work, but session details "
                "(score, duration, areas) may be missing. "
                "Please open an issue at https://github.com/deniskie/ha-oclean-integration "
                "with this model ID so full support can be added.",
                model_id,
                self._mac,
            )

        # Mirror model/firmware into the HA device registry so the device
        # info panel shows the values without requiring a dedicated sensor.
        sw_version = collected.get(DATA_SW_VERSION)
        hw_revision = collected.get(DATA_HW_REVISION)
        model_id = collected.get(DATA_MODEL_ID)
        if sw_version or model_id:
            try:
                device_registry = dr.async_get(self.hass)
                device_entry = device_registry.async_get_device(identifiers={(DOMAIN, self._mac)})
                if device_entry:
                    device_registry.async_update_device(
                        device_entry.id,
                        sw_version=sw_version,
                        hw_version=hw_revision,
                        model=model_id,
                    )
                    self._log.debug(
                        "device registry updated: model=%s sw=%s hw=%s",
                        model_id,
                        sw_version,
                        hw_revision,
                    )
            except Exception as err:  # noqa: BLE001
                self._log.debug("device registry update skipped: %s", err)

    async def _calibrate_time(self, client: BleakClient) -> None:
        """Send time-calibration command appropriate for the active protocol.

        Type-0 / Unknown:  020E + 4-byte big-endian Unix timestamp  (mo5289B, C3335a.java)
        Type-1:            0201 + 8-byte datetime payload            (mo5292L, C3352g.java)

        The Type-1 payload encodes [year-2000, month, day, hour, min, sec, weekday, tzIndex]
        as plain decimal byte values, where weekday is 0=Sunday..6=Saturday and tzIndex is
        a 1-based index into the Oclean GMT offset table (GMT-12 … GMT+13, 33 entries).
        """
        if self._protocol.uses_t1_calibration:
            now = datetime.datetime.now().astimezone()
            utc_offset = now.utcoffset()
            offset_min = int(utc_offset.total_seconds() / 60) if utc_offset is not None else 0
            tz_idx = _oclean_tz_index(offset_min)
            weekday = (now.weekday() + 1) % 7  # Python Mon=0..Sun=6 → Oclean Sun=0..Sat=6
            payload = bytes(
                [
                    now.year - 2000,
                    now.month,
                    now.day,
                    now.hour,
                    now.minute,
                    now.second,
                    weekday,
                    tz_idx,
                ]
            )
            cal_cmd = CMD_CALIBRATE_TIME_T1_PREFIX + payload
            self._log.debug(
                "time calibration sent (Type-1): %04d-%02d-%02d %02d:%02d:%02d wday=%d tz_idx=%d",
                now.year,
                now.month,
                now.day,
                now.hour,
                now.minute,
                now.second,
                weekday,
                tz_idx,
            )
        else:
            timestamp = int(time.time())
            cal_cmd = CMD_CALIBRATE_TIME_PREFIX + struct.pack(">I", timestamp)
            self._log.debug("time calibration sent (ts=%d)", timestamp)
        try:
            await client.write_gatt_char(self._protocol.write_char, cal_cmd, response=True)
        except Exception as err:  # noqa: BLE001
            self._log.warning("time calibration failed: %s (%s)", err, type(err).__name__)

    async def _subscribe_notifications(
        self, client: BleakClient, handler: Callable[[Any, bytearray], None]
    ) -> frozenset[str]:
        """Subscribe to notification characteristics for the active device protocol.

        Returns the set of UUIDs successfully subscribed.  Callers use this to
        detect devices (e.g. OCLEANA1) that don't support CCCD-based notifications
        on READ_NOTIFY_CHAR_UUID and must be polled via direct READ instead.

        When BlueZ reports "Notify acquired" (stale CCCD subscription from a
        previous connection that did not disconnect cleanly), the method calls
        stop_notify to release the existing subscription and retries once.
        This situation is common after a connection drop (proxy timeout, device
        moved out of range) where the normal disconnect path was not taken.
        """
        subscribed: set[str] = set()
        for char_uuid in self._protocol.notify_chars:
            try:
                await asyncio.wait_for(
                    client.start_notify(char_uuid, handler),
                    timeout=BLE_SUBSCRIBE_FIRST_TIMEOUT,
                )
                subscribed.add(char_uuid)
                self._log.debug("subscribed to %s", char_uuid)
            except Exception as err:  # noqa: BLE001
                is_timeout = isinstance(err, asyncio.TimeoutError)
                is_stale = "Notify acquired" in str(err)
                if is_timeout or is_stale:
                    # Both TimeoutError and "Notify acquired" indicate a stale CCCD
                    # state on the device.  Release the local handler, clear the CCCD
                    # descriptor (write 0x0000) to force the device to accept a fresh
                    # subscription, then retry with a longer timeout.
                    if is_timeout:
                        self._log.debug(
                            "subscribe timeout on %s – clearing CCCD and retrying",
                            char_uuid,
                        )
                    else:
                        self._log.debug(
                            "Notify acquired on %s – releasing stale subscription and retrying",
                            char_uuid,
                        )
                    with contextlib.suppress(Exception):
                        await client.stop_notify(char_uuid)
                    await _clear_cccd(client, char_uuid)
                    await asyncio.sleep(0.3)
                    try:
                        await asyncio.wait_for(
                            client.start_notify(char_uuid, handler),
                            timeout=BLE_SUBSCRIBE_RETRY_TIMEOUT,
                        )
                        subscribed.add(char_uuid)
                        self._log.debug("subscribed to %s (after CCCD clear)", char_uuid)
                    except Exception as retry_err:  # noqa: BLE001
                        self._log.warning(
                            "could not subscribe to %s even after CCCD clear: %s"
                            " – if the Oclean app is open, close it and wait for the next poll",
                            char_uuid,
                            retry_err,
                        )
                else:
                    is_no_cccd = "does not have a characteristic client config descriptor" in str(err)
                    if is_no_cccd:
                        self._log.debug("no CCCD on %s – trying no-CCCD subscribe path", char_uuid)
                        if await _try_subscribe_no_cccd(
                            client,
                            char_uuid,
                            handler,
                            self._log,
                        ):
                            subscribed.add(char_uuid)
                    else:
                        self._log.debug(
                            "could not subscribe to %s: %s (%s)",
                            char_uuid,
                            err,
                            type(err).__name__,
                        )
        return frozenset(subscribed)

    async def _read_response_char_fallback(
        self, client: BleakClient, handler: Callable[[Any, bytearray], None]
    ) -> None:
        """Directly READ READ_NOTIFY_CHAR_UUID for devices without CCCD support.

        Devices like OCLEANA1 (Protocol 6) place their response data in
        READ_NOTIFY_CHAR_UUID as a readable value rather than pushing it via
        BLE notifications.  When subscription fails (no CCCD descriptor), we
        poll the characteristic multiple times and feed any non-empty result
        through the same notification_handler as if it had arrived as a notify
        event.  Multiple reads catch responses to different commands (e.g.
        0303 status + battery) that the device may write sequentially.
        """
        last_hex = ""
        for attempt in range(3):
            await asyncio.sleep(BLE_READ_FALLBACK_DELAY)
            try:
                raw = await client.read_gatt_char(READ_NOTIFY_CHAR_UUID)
                data = bytes(raw)
                current_hex = data.hex()
                self._log.debug("READ fallback attempt %d: %s", attempt + 1, current_hex)
                if len(data) > 2 and current_hex != last_hex:
                    handler(None, bytearray(data))
                    last_hex = current_hex
                elif current_hex == last_hex:
                    self._log.debug("READ fallback: same data as previous read, stopping")
                    break
            except Exception as err:  # noqa: BLE001
                self._log.debug("READ fallback attempt %d failed: %s (%s)", attempt + 1, err, type(err).__name__)
                break

    async def _poll_receive_brush_fallback(
        self,
        client: BleakClient,
        handler: Callable[[Any, bytearray], None],
        session_received: asyncio.Event,
    ) -> None:
        """Poll RECEIVE_BRUSH_UUID in a loop when notification subscription failed.

        On TYPE1 devices (Oclean X / X Pro) the device pushes session data as
        BLE notifications on RECEIVE_BRUSH_UUID (fbb90).  When BlueZ reports
        "Notify acquired" persistently (e.g. stale bond state that survives
        reboots), notifications never arrive.

        This fallback reads fbb90 repeatedly after the query commands have been
        sent.  The device firmware updates the characteristic value when it has
        data — polling catches it even without an active notification subscription.
        Reading also covers fbb86 (READ_NOTIFY_CHAR_UUID) on each iteration since
        some devices place status responses there.

        Stops early if the notification handler signals a session was received
        (e.g. via a successful READ that the handler parsed).
        """
        seen_hex: set[str] = set()
        for attempt in range(BLE_POLL_FALLBACK_ATTEMPTS):
            if session_received.is_set():
                self._log.debug("poll fallback: session received, stopping early")
                break
            await asyncio.sleep(BLE_POLL_FALLBACK_INTERVAL)
            # Poll fbb90
            try:
                raw = await client.read_gatt_char(RECEIVE_BRUSH_UUID)
                data = bytes(raw)
                hex_str = data.hex()
                if len(data) > 2 and hex_str not in seen_hex:
                    seen_hex.add(hex_str)
                    self._log.debug(
                        "poll fallback fbb90 [%d/%d]: %s",
                        attempt + 1,
                        BLE_POLL_FALLBACK_ATTEMPTS,
                        hex_str,
                    )
                    handler(None, bytearray(data))
                elif len(data) <= 2:
                    self._log.debug(
                        "poll fallback fbb90 [%d/%d]: too short (%d B)",
                        attempt + 1,
                        BLE_POLL_FALLBACK_ATTEMPTS,
                        len(data),
                    )
            except Exception as err:  # noqa: BLE001
                self._log.debug(
                    "poll fallback fbb90 [%d/%d] failed: %s (%s)",
                    attempt + 1,
                    BLE_POLL_FALLBACK_ATTEMPTS,
                    err,
                    type(err).__name__,
                )
            # Also poll fbb86 — some devices place status/settings responses there
            try:
                raw86 = await client.read_gatt_char(READ_NOTIFY_CHAR_UUID)
                data86 = bytes(raw86)
                hex86 = data86.hex()
                if len(data86) > 2 and hex86 not in seen_hex:
                    seen_hex.add(hex86)
                    self._log.debug(
                        "poll fallback fbb86 [%d/%d]: %s",
                        attempt + 1,
                        BLE_POLL_FALLBACK_ATTEMPTS,
                        hex86,
                    )
                    handler(None, bytearray(data86))
            except Exception:  # noqa: BLE001
                pass  # fbb86 READ failures are expected on some devices

    async def _send_query_commands(
        self,
        client: BleakClient,
        session_received: asyncio.Event,
        notify_wait: float | None = None,
    ) -> None:
        """Send query commands for the active device protocol; wait for first session.

        Commands are defined by the protocol profile so only appropriate commands
        are sent for the connected device.  Failures are logged at DEBUG level
        since unexpected commands are simply ignored by the firmware.

        *notify_wait* overrides the default notification timeout (used when
        subscriptions failed and a polling fallback will follow).
        """
        for char_uuid, cmd in self._protocol.query_commands:
            try:
                await asyncio.wait_for(
                    client.write_gatt_char(char_uuid, cmd, response=True),
                    timeout=BLE_WRITE_TIMEOUT,
                )
                self._log.debug("command 0x%s sent to ...%s", cmd.hex(), char_uuid[-8:])
            except Exception as err:  # noqa: BLE001
                self._log.debug(
                    "command 0x%s skipped: %s (%s)",
                    cmd.hex(),
                    err,
                    type(err).__name__,
                )

        wait = notify_wait if notify_wait is not None else float(BLE_NOTIFICATION_WAIT)
        # Wait for first session notification (or timeout if device has no records)
        try:
            await asyncio.wait_for(session_received.wait(), timeout=wait)
        except asyncio.TimeoutError:
            self._log.debug(
                "no session notification within %.1f s (device may have no records)",
                wait,
            )

    async def _paginate_sessions(
        self,
        client: BleakClient,
        all_sessions: list[dict[str, Any]],
        session_received: asyncio.Event,
    ) -> None:
        """Fetch older sessions via 0309 pagination until done or safety limit reached."""
        if not self._protocol.supports_pagination:
            self._log.debug("pagination skipped (%s protocol)", self._protocol.name)
            return

        for page in range(MAX_SESSION_PAGES - 1):
            if not all_sessions:
                break
            last_ts = all_sessions[-1].get(DATA_LAST_BRUSH_TIME, 0)
            if last_ts and last_ts <= self._last_session_ts:
                self._log.debug(
                    "pagination stopped: reached already-known session (ts=%d) at page %d",
                    last_ts,
                    page,
                )
                break

            session_received.clear()
            try:
                await asyncio.wait_for(
                    client.write_gatt_char(WRITE_CHAR_UUID, CMD_QUERY_RUNNING_DATA_NEXT, response=True),
                    timeout=BLE_WRITE_TIMEOUT,
                )
            except BaseException as err:  # noqa: BLE001  # must catch CancelledError (issue #9)
                self._log.debug("0309 write failed at page %d: %s", page, err)
                break

            try:
                await asyncio.wait_for(session_received.wait(), timeout=BLE_PAGINATION_TIMEOUT)
            except asyncio.TimeoutError:
                self._log.debug("no more sessions after page %d", page)
                break

    async def _subscribe_battery_notifications(self, client: BleakClient, collected: dict[str, Any]) -> None:
        """Subscribe to 0x2A19 battery-level notifications (APK: m5371W on all devices).

        The Oclean firmware pushes an updated battery value on 0x2A19 shortly
        after connect (confirmed via APK: C3385w0 / C3335a both call m5371W on
        BATTERY_SERVICE_UUID + BATTERY_CHARACTER_UUID).  Subscribing here – before
        the query commands – ensures any push that arrives during the poll window
        is captured in *collected*.  _read_battery_and_unsubscribe then skips the
        explicit read_gatt_char if the notification already delivered a value.
        """

        def _batt_notify(_sender: Any, raw: bytearray) -> None:
            val = parse_battery(bytes(raw))
            if val is not None:
                collected[DATA_BATTERY] = val
                self._log.debug("battery notification: %d%%", val)

        try:
            await asyncio.wait_for(
                client.start_notify(BATTERY_CHAR_UUID, _batt_notify),
                timeout=BLE_SUBSCRIBE_FIRST_TIMEOUT,
            )
            self._log.debug("subscribed to battery notifications (0x2A19)")
        except Exception as err:  # noqa: BLE001
            if "not found" in str(err).lower():
                # ESPHome BLE proxies cache the remote GATT table and may not
                # include the Battery Service (0x180F / 0x2A19) when the cache
                # is stale.  A fresh DIS read forces the proxy to redo full GATT
                # discovery, after which 0x2A19 becomes visible.
                self._log.debug("0x2A19 not found – invalidating DIS cache to force GATT re-discovery, retrying")
                self._dis_last_read_ts = 0.0
                await self._read_device_info_service(client, collected)
                try:
                    await asyncio.wait_for(
                        client.start_notify(BATTERY_CHAR_UUID, _batt_notify),
                        timeout=BLE_SUBSCRIBE_RETRY_TIMEOUT,
                    )
                    self._log.debug("subscribed to battery notifications (0x2A19) after GATT re-discovery")
                except Exception as retry_err:  # noqa: BLE001
                    self._log.debug(
                        "battery notify subscribe failed after retry: %s (%s)",
                        retry_err,
                        type(retry_err).__name__,
                    )
            else:
                self._log.debug("battery notify subscribe failed: %s (%s)", err, type(err).__name__)

    async def _read_battery_and_unsubscribe(self, client: BleakClient, collected: dict[str, Any]) -> None:
        """Read battery level via GATT, unless a notification already delivered it.

        If _subscribe_battery_notifications captured a 0x2A19 push during the poll
        window, DATA_BATTERY is already in *collected* and the explicit read is
        skipped.  The read_gatt_char fallback covers devices that support the
        characteristic but do not push notifications proactively.

        stop_notify is intentionally omitted: the BLE disconnect in _poll_device's
        finally block tears down all subscriptions automatically, saving 4 extra
        GATT round-trips (~0.4 s) per poll.
        """
        self._log.debug("poll collected so far: %s", collected)
        if DATA_BATTERY in collected:
            self._log.debug("battery already set from notification: %d%%", collected[DATA_BATTERY])
            return
        try:
            batt_raw = await client.read_gatt_char(BATTERY_CHAR_UUID)
            self._log.debug("battery raw: %s", bytes(batt_raw).hex())
            batt = parse_battery(bytes(batt_raw))
            if batt is not None:
                collected[DATA_BATTERY] = batt
        except Exception as err:  # noqa: BLE001
            self._log.warning("battery read failed: %s (%s)", err, type(err).__name__)
