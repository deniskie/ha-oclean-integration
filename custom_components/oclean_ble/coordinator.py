"""DataUpdateCoordinator for the Oclean integration."""
from __future__ import annotations

import asyncio
import logging
import struct
import time
import traceback
import datetime
from datetime import time as _dtime
from datetime import timedelta
from typing import Any

from bleak import BleakClient, BleakError
from bleak_retry_connector import establish_connection

from homeassistant.components import bluetooth
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    BATTERY_CHAR_UUID,
    BLE_NOTIFICATION_WAIT,
    CHANGE_INFO_UUID,
    CMD_CALIBRATE_TIME_PREFIX,
    CMD_CLEAR_BRUSH_HEAD,
    CMD_QUERY_EXTENDED_DATA_T1,
    CMD_QUERY_RUNNING_DATA,
    CMD_QUERY_RUNNING_DATA_NEXT,
    CMD_QUERY_RUNNING_DATA_T1,
    CMD_QUERY_STATUS,
    DATA_BATTERY,
    DATA_BRUSH_HEAD_USAGE,
    DATA_HW_REVISION,
    DATA_LAST_BRUSH_AREAS,
    DATA_LAST_BRUSH_DURATION,
    DATA_LAST_BRUSH_PRESSURE,
    DATA_LAST_BRUSH_PNUM,
    DATA_LAST_BRUSH_SCORE,
    DATA_LAST_BRUSH_TIME,
    DATA_MODEL_ID,
    DATA_SW_VERSION,
    DIS_HW_REV_UUID,
    DIS_MODEL_UUID,
    DIS_SW_REV_UUID,
    DOMAIN,
    MAX_SESSION_PAGES,
    READ_NOTIFY_CHAR_UUID,
    RECEIVE_BRUSH_UUID,
    SEND_BRUSH_CMD_UUID,
    STORAGE_VERSION,
    WRITE_CHAR_UUID,
)
from .models import OcleanDeviceData
from .parser import parse_battery, parse_notification

_LOGGER = logging.getLogger(__name__)


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

# Metrics exported to HA long-term statistics
# (data_key, statistic_name_suffix, unit_of_measurement)
_STAT_METRICS: tuple[tuple[str, str, str | None], ...] = (
    (DATA_LAST_BRUSH_SCORE,    "brush_score",    "%"),
    (DATA_LAST_BRUSH_DURATION, "brush_duration", "s"),
    (DATA_LAST_BRUSH_PRESSURE, "brush_pressure", None),
)

# Fields delivered by enrichment notifications (0000/2604) that carry no timestamp.
# These arrive AFTER the session-creating notification (5a00/0307) and must be
# merged back into the session snapshot for correct statistics import.
_ENRICHMENT_KEYS: tuple[str, ...] = (
    DATA_LAST_BRUSH_SCORE,
    DATA_LAST_BRUSH_AREAS,
    DATA_LAST_BRUSH_PRESSURE,
)

# BLE notification characteristics to subscribe/unsubscribe in each poll
_NOTIFY_CHARS: tuple[str, ...] = (
    READ_NOTIFY_CHAR_UUID,   # all device types
    RECEIVE_BRUSH_UUID,      # Type 1 – brush session data
    CHANGE_INFO_UUID,        # Type 0 – change-info notifications
    SEND_BRUSH_CMD_UUID,     # Type 1 – running-data result
)


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
            update_interval=timedelta(seconds=update_interval),
        )
        self._mac = mac_address
        self._device_name = device_name
        # Carries raw dict across polls so sensors keep their last value on failure
        self._last_raw: dict[str, Any] = {}
        # Track whether the last poll succeeded
        self.last_poll_successful: bool = False

        # Persistent storage: tracks last imported session timestamp per device.
        # Storage key is unique per MAC so multi-device setups don't conflict.
        _mac_slug = mac_address.replace(":", "_").lower()
        self._store: Store = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{_mac_slug}")
        # Unix timestamp (seconds) of the newest session already imported into HA statistics.
        # Sessions with last_brush_time > this value are considered new.
        self._last_session_ts: int = 0
        self._store_loaded: bool = False

        # Software brush-head usage counter.
        # Used when the device does not expose a hardware counter via BLE (0308 bytes 14-15).
        # Once a hardware value is ever received, _brush_head_hw_supported is set to True
        # and the software counter is no longer written to the sensor.
        self._brush_head_sw_count: int = 0
        self._brush_head_hw_supported: bool = False

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
        skip_reason = self._poll_skip_reason()
        if skip_reason:
            _LOGGER.debug("Oclean poll skipped: %s", skip_reason)
            if self._last_raw:
                return OcleanDeviceData.from_dict(self._last_raw)
            return OcleanDeviceData()

        try:
            raw = await self._poll_device()
            return OcleanDeviceData.from_dict(raw)
        except Exception as err:  # noqa: BLE001
            # Catch all exceptions (BleakError, TimeoutError, IndexError from
            # habluetooth proxy backend, etc.) so HA can keep retrying rather
            # than crashing the integration.
            _LOGGER.debug(
                "Oclean %s poll failed: %s (%s)\n%s",
                self._mac, err, type(err).__name__,
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

        Called by the "Reset Brush Head" button entity.
        Raises BleakError if the device cannot be reached.
        """
        service_info = bluetooth.async_last_service_info(
            self.hass, self._mac, connectable=True
        )
        ble_device = (
            service_info.device
            if service_info is not None
            else bluetooth.async_ble_device_from_address(
                self.hass, self._mac, connectable=True
            )
        )
        if ble_device is None:
            raise BleakError(
                f"Oclean {self._mac} not found in HA bluetooth registry."
            )

        client = await establish_connection(
            BleakClient,
            ble_device,
            self._device_name,
            max_attempts=3,
        )
        try:
            await asyncio.sleep(2.0)
            await client.write_gatt_char(WRITE_CHAR_UUID, CMD_CLEAR_BRUSH_HEAD, response=True)
            _LOGGER.info("Oclean brush head counter reset sent to %s", self._mac)
        finally:
            if client.is_connected:
                await client.disconnect()

        # Reset software counter regardless of hw support (covers both cases)
        self._brush_head_sw_count = 0
        await self._save_store()
        _LOGGER.debug("Oclean brush head sw counter reset to 0")

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
            import datetime as _datetime  # noqa: PLC0415
            now_t = _datetime.datetime.now().time()
            if not any(_in_window(s, e, now_t) for s, e in self._poll_windows):
                windows_str = ", ".join(
                    f"{s.strftime('%H:%M')}-{e.strftime('%H:%M')}"
                    for s, e in self._poll_windows
                )
                return f"outside poll windows ({windows_str})"

        return None

    def _resolve_ble_device(self):
        """BLEDevice from HA Bluetooth registry; raises BleakError if not found."""
        service_info = bluetooth.async_last_service_info(
            self.hass, self._mac, connectable=True
        )
        if service_info is not None:
            return service_info.device
        device = bluetooth.async_ble_device_from_address(
            self.hass, self._mac, connectable=True
        )
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
            self._brush_head_sw_count = stored.get("brush_head_count", 0)
            self._brush_head_hw_supported = stored.get("brush_head_hw", False)
            _LOGGER.debug(
                "Oclean loaded store: last_session_ts=%d, brush_head_count=%d, hw=%s",
                self._last_session_ts, self._brush_head_sw_count, self._brush_head_hw_supported,
            )
        self._store_loaded = True

    async def _save_store(self) -> None:
        """Persist coordinator state to HA storage."""
        await self._store.async_save({
            "last_session_ts": self._last_session_ts,
            "brush_head_count": self._brush_head_sw_count,
            "brush_head_hw": self._brush_head_hw_supported,
        })

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
            all_sessions = await self._setup_and_read(client, collected)
        finally:
            if client.is_connected:
                await client.disconnect()

        self.last_poll_successful = True

        # Detect hardware brush-head counter support
        if collected.get(DATA_BRUSH_HEAD_USAGE) is not None:
            self._brush_head_hw_supported = True

        # Count new sessions before _import_new_sessions updates _last_session_ts
        new_session_count = sum(
            1 for s in all_sessions
            if s.get("last_brush_time", 0) > self._last_session_ts
        )

        # Import new sessions into HA long-term statistics
        if all_sessions:
            await self._import_new_sessions(all_sessions)

        # Post-brush cooldown: pause polling for N hours after a new session
        if new_session_count > 0 and self._post_brush_cooldown_s > 0:
            self._cooldown_until = time.time() + self._post_brush_cooldown_s
            _LOGGER.info(
                "Oclean post-brush cooldown: %d new session(s) detected, "
                "pausing polls for %.1f h",
                new_session_count,
                self._post_brush_cooldown_s / 3600,
            )

        # Software brush-head counter: increment per new session when hw not supported
        if not self._brush_head_hw_supported:
            if new_session_count > 0:
                self._brush_head_sw_count += new_session_count
                await self._save_store()
                _LOGGER.debug(
                    "Oclean brush head sw counter: +%d → %d",
                    new_session_count, self._brush_head_sw_count,
                )
            collected[DATA_BRUSH_HEAD_USAGE] = self._brush_head_sw_count

        # Merge with last known persistent data, then overwrite with fresh values
        merged = {**{k: self._last_raw.get(k) for k in _PERSISTENT_KEYS}, **collected}
        self._last_raw = merged
        return merged

    async def _setup_and_read(
        self,
        client: BleakClient,
        collected: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Perform GATT operations and return all fetched brush session records.

        Operation order mirrors the Java SDK (C3335a / C3340b1):
          1. Time calibration  (020E + BE timestamp)   – mo5289B
          2. Subscribe to all notification characteristics
          3. Status query      (0303)                  – mo5295Q0
          4. Device-info query (0202)                  – mo5310r0
          5. Running-data req  (0308) + pagination (0309) until no new sessions
          6. Battery read      (standard GATT char)
          7. Unsubscribe / disconnect
        """
        # Delay after connect: gives habluetooth's proxy backend time to finish
        # processing the GATT service table before we start issuing commands.
        await asyncio.sleep(2.0)

        all_sessions: list[dict[str, Any]] = []
        _seen_ts: set[int] = set()
        session_received = asyncio.Event()

        def notification_handler(sender: Any, raw: bytearray) -> None:
            data = bytes(raw)
            _LOGGER.debug("Oclean notification raw: %s", data.hex())
            parsed = parse_notification(data)
            _LOGGER.debug("Oclean notification parsed: %s", parsed)
            if parsed:
                # "Newer timestamp wins": if this notification carries a
                # last_brush_time that is older than what we already have,
                # drop the time-dependent fields so a stale 5a00 push never
                # overwrites a more recent timestamp + duration from 0307.
                incoming_ts = parsed.get("last_brush_time")
                if incoming_ts is not None:
                    current_ts = collected.get("last_brush_time", 0)
                    if incoming_ts < current_ts:
                        parsed = {
                            k: v for k, v in parsed.items()
                            if k not in ("last_brush_time", "last_brush_duration")
                        }
                collected.update(parsed)
                # If this notification contains a brush session, accumulate it
                ts = parsed.get("last_brush_time")
                if ts and ts not in _seen_ts:
                    _seen_ts.add(ts)
                    all_sessions.append(dict(parsed))
                    session_received.set()

        await self._calibrate_time(client)
        await self._read_device_info_service(client, collected)
        await self._subscribe_notifications(client, notification_handler)
        await self._send_query_commands(client, session_received)
        await self._paginate_sessions(client, all_sessions, session_received)
        await self._read_battery_and_unsubscribe(client, collected)

        # Enrich the latest session snapshot with fields from enrichment notifications
        # (0000 → score, 2604 → areas/pressure/clean).  These notifications carry no
        # timestamp so they update `collected` but are not captured in all_sessions.
        # We merge them into the snapshot of the newest session so that stats import
        # receives complete session data.
        if all_sessions:
            latest = max(all_sessions, key=lambda s: s.get("last_brush_time", 0))
            enriched = {
                k: collected[k]
                for k in _ENRICHMENT_KEYS
                if k in collected and k not in latest
            }
            if enriched:
                latest.update(enriched)
                _LOGGER.debug("Oclean session snapshot enriched: %s", list(enriched.keys()))

        _LOGGER.debug(
            "Oclean fetched %d session(s) total from device (last_known_ts=%d)",
            len(all_sessions), self._last_session_ts,
        )
        # Log each session timestamp so we can check across polls whether the
        # device deletes sessions after retrieval or keeps them.
        for i, s in enumerate(all_sessions):
            ts = s.get("last_brush_time", 0)
            status = "NEW" if ts > self._last_session_ts else "known"
            _LOGGER.debug(
                "Oclean  session[%d]: ts=%d (%s)  %s",
                i,
                ts,
                datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S") if ts else "n/a",
                status,
            )
        return all_sessions

    async def _read_device_info_service(
        self, client: BleakClient, collected: dict[str, Any]
    ) -> None:
        """Read BLE Device Information Service (0x180A) characteristics.

        Populates model_id, hw_revision, and sw_version in collected.
        Also updates the HA device registry so the info panel shows the
        firmware version without needing a dedicated sensor.
        """
        # Firmware/model info rarely changes (only after a firmware update).
        # Re-read from the device at most once every 24 h; use the cached values
        # from _last_raw for all other polls to keep the BLE session short.
        _DIS_REFRESH_INTERVAL = 86_400  # 24 h in seconds
        dis_keys = (DATA_MODEL_ID, DATA_HW_REVISION, DATA_SW_VERSION)
        age = time.time() - self._dis_last_read_ts
        if self._dis_last_read_ts > 0 and age < _DIS_REFRESH_INTERVAL:
            for key in dis_keys:
                cached = self._last_raw.get(key)
                if cached:
                    collected[key] = cached
            _LOGGER.debug(
                "Oclean DIS skipped (cached, %.0f h until refresh: model=%s sw=%s)",
                (_DIS_REFRESH_INTERVAL - age) / 3600,
                self._last_raw.get(DATA_MODEL_ID),
                self._last_raw.get(DATA_SW_VERSION),
            )
            return

        dis_chars = {
            DATA_MODEL_ID:    DIS_MODEL_UUID,
            DATA_HW_REVISION: DIS_HW_REV_UUID,
            DATA_SW_VERSION:  DIS_SW_REV_UUID,
        }
        for key, uuid in dis_chars.items():
            try:
                raw = await client.read_gatt_char(uuid)
                collected[key] = raw.decode("utf-8").strip("\x00").strip()
                _LOGGER.debug("Oclean DIS %s: %s", key, collected[key])
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug("Oclean DIS read skipped for %s: %s", uuid[-8:], err)

        if any(collected.get(k) for k in dis_keys):
            self._dis_last_read_ts = time.time()

        # Mirror model/firmware into the HA device registry so the device
        # info panel shows the values without requiring a dedicated sensor.
        sw_version = collected.get(DATA_SW_VERSION)
        hw_revision = collected.get(DATA_HW_REVISION)
        model_id = collected.get(DATA_MODEL_ID)
        if sw_version or model_id:
            try:
                from homeassistant.helpers import device_registry as dr  # noqa: PLC0415
                device_registry = dr.async_get(self.hass)
                device_entry = device_registry.async_get_device(
                    identifiers={(DOMAIN, self._mac)}
                )
                if device_entry:
                    device_registry.async_update_device(
                        device_entry.id,
                        sw_version=sw_version,
                        hw_version=hw_revision,
                        model=model_id,
                    )
                    _LOGGER.debug(
                        "Oclean device registry updated: model=%s sw=%s hw=%s",
                        model_id, sw_version, hw_revision,
                    )
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug("Oclean device registry update skipped: %s", err)

    async def _calibrate_time(self, client: BleakClient) -> None:
        """Send time-calibration command (020E + BE timestamp)."""
        timestamp = int(time.time())
        time_bytes = struct.pack(">I", timestamp)
        cal_cmd = CMD_CALIBRATE_TIME_PREFIX + time_bytes
        try:
            await client.write_gatt_char(WRITE_CHAR_UUID, cal_cmd, response=True)
            _LOGGER.debug("Oclean time calibration sent (ts=%d)", timestamp)
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Oclean time calibration failed: %s (%s)", err, type(err).__name__)

    async def _subscribe_notifications(self, client: BleakClient, handler: Any) -> None:
        """Subscribe to all notification characteristics."""
        for char_uuid in _NOTIFY_CHARS:
            try:
                await client.start_notify(char_uuid, handler)
                _LOGGER.debug("Oclean subscribed to %s", char_uuid)
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug(
                    "Oclean could not subscribe to %s: %s (%s)",
                    char_uuid, err, type(err).__name__,
                )

    async def _send_query_commands(
        self, client: BleakClient, session_received: asyncio.Event
    ) -> None:
        """Send status, device-info, and running-data commands; wait for first session."""
        try:
            await client.write_gatt_char(WRITE_CHAR_UUID, CMD_QUERY_STATUS, response=True)
            _LOGGER.debug("Oclean CMD_QUERY_STATUS sent")
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Oclean status query failed: %s (%s)", err, type(err).__name__)

        # CMD_DEVICE_INFO (0202) intentionally omitted: the device responds with a plain
        # "OK" ACK that provides no useful data.  All firmware/model info comes from the
        # BLE Device Information Service (0x180A) read in _read_device_info_service().

        # Running-data first page (0308); also try Type-1 variant (device ignores unknown cmds)
        try:
            await client.write_gatt_char(WRITE_CHAR_UUID, CMD_QUERY_RUNNING_DATA, response=True)
            _LOGGER.debug("Oclean CMD_QUERY_RUNNING_DATA (0308) sent")
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Oclean running-data request failed: %s (%s)", err, type(err).__name__)
        try:
            await client.write_gatt_char(SEND_BRUSH_CMD_UUID, CMD_QUERY_RUNNING_DATA_T1, response=True)
            _LOGGER.debug("Oclean CMD_QUERY_RUNNING_DATA_T1 (0307) sent")
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Oclean Type-1 running-data skipped: %s (%s)", err, type(err).__name__)
        try:
            await client.write_gatt_char(SEND_BRUSH_CMD_UUID, CMD_QUERY_EXTENDED_DATA_T1, response=True)
            _LOGGER.debug("Oclean CMD_QUERY_EXTENDED_DATA_T1 (0314) sent")
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Oclean 0314 extended-data skipped: %s (%s)", err, type(err).__name__)

        # Wait for first session notification (or timeout if device has no records)
        try:
            await asyncio.wait_for(session_received.wait(), timeout=float(BLE_NOTIFICATION_WAIT))
        except asyncio.TimeoutError:
            _LOGGER.debug("Oclean no session notification after first 0308 (device may have no records)")

    async def _paginate_sessions(
        self,
        client: BleakClient,
        all_sessions: list[dict[str, Any]],
        session_received: asyncio.Event,
    ) -> None:
        """Fetch older sessions via 0309 pagination until done or safety limit reached."""
        for page in range(MAX_SESSION_PAGES - 1):
            if not all_sessions:
                break
            last_ts = all_sessions[-1].get("last_brush_time", 0)
            if last_ts and last_ts <= self._last_session_ts:
                _LOGGER.debug(
                    "Oclean pagination stopped: reached already-known session (ts=%d) at page %d",
                    last_ts, page,
                )
                break

            session_received.clear()
            try:
                await client.write_gatt_char(
                    WRITE_CHAR_UUID, CMD_QUERY_RUNNING_DATA_NEXT, response=True
                )
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug("Oclean 0309 write failed at page %d: %s", page, err)
                break

            try:
                await asyncio.wait_for(session_received.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                _LOGGER.debug("Oclean no more sessions after page %d", page)
                break

    async def _read_battery_and_unsubscribe(
        self, client: BleakClient, collected: dict[str, Any]
    ) -> None:
        """Read battery level via GATT.

        stop_notify is intentionally omitted: the BLE disconnect in _poll_device's
        finally block tears down all subscriptions automatically, saving 4 extra
        GATT round-trips (~0.4 s) per poll.
        """
        _LOGGER.debug("Oclean poll collected so far: %s", collected)
        try:
            batt_raw = await client.read_gatt_char(BATTERY_CHAR_UUID)
            _LOGGER.debug("Oclean battery raw: %s", bytes(batt_raw).hex())
            batt = parse_battery(bytes(batt_raw))
            if batt is not None:
                collected[DATA_BATTERY] = batt
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Oclean battery read failed: %s (%s)", err, type(err).__name__)

    async def _import_new_sessions(self, sessions: list[dict[str, Any]]) -> None:
        """Import brush sessions newer than last_session_ts into HA long-term statistics.

        Uses recorder.statistics.async_add_external_statistics so that historical
        sessions (e.g. recorded while HA was offline) appear with their actual
        timestamps in the HA energy/statistics graphs.
        """
        new_sessions = [
            s for s in sessions
            if s.get("last_brush_time", 0) > self._last_session_ts
        ]
        if not new_sessions:
            _LOGGER.debug("Oclean no new sessions to import into statistics")
            return

        _LOGGER.debug(
            "Oclean importing %d new session(s) into HA statistics:", len(new_sessions)
        )
        for s in new_sessions:
            ts = s.get("last_brush_time", 0)
            _LOGGER.debug(
                "Oclean  → import ts=%d (%s)",
                ts,
                datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S") if ts else "n/a",
            )

        # Import recorder API lazily to handle setups where recorder is unavailable
        recorder_api = self._load_recorder_api()
        if recorder_api is None:
            _LOGGER.debug(
                "Oclean recorder statistics API not available; skipping history import"
            )
            return
        StatisticData, StatisticMetaData, async_add_external_statistics = recorder_api

        import datetime  # noqa: PLC0415

        from homeassistant.util import dt as dt_util  # noqa: PLC0415

        mac_slug = self._mac.replace(":", "_").lower()

        for data_key, stat_suffix, unit in _STAT_METRICS:
            stat_rows: list[Any] = []
            for session in new_sessions:
                value = session.get(data_key)
                if value is None:
                    continue
                ts = session["last_brush_time"]
                start_dt = datetime.datetime.fromtimestamp(ts, tz=dt_util.UTC).replace(minute=0, second=0, microsecond=0)
                stat_rows.append(
                    StatisticData(
                        start=start_dt,
                        mean=float(value),
                        state=float(value),
                    )
                )

            if not stat_rows:
                continue

            metadata = StatisticMetaData(
                has_mean=True,
                has_sum=False,
                name=f"Oclean {self._device_name} {stat_suffix.replace('_', ' ').title()}",
                source=DOMAIN,
                statistic_id=f"{DOMAIN}:{mac_slug}_{stat_suffix}",
                unit_of_measurement=unit,
            )
            try:
                async_add_external_statistics(self.hass, metadata, stat_rows)
            except Exception as err:  # noqa: BLE001
                _LOGGER.warning(
                    "Oclean statistics import failed for '%s_%s': %s – skipping",
                    mac_slug, stat_suffix, err,
                )
                continue
            _LOGGER.debug(
                "Oclean imported %d row(s) for statistic '%s:%s_%s'",
                len(stat_rows), DOMAIN, mac_slug, stat_suffix,
            )

        # Import per-zone area pressures as individual statistics
        area_stats_by_zone: dict[str, list[Any]] = {}
        for session in new_sessions:
            areas = session.get(DATA_LAST_BRUSH_AREAS)
            if not isinstance(areas, dict):
                continue
            ts = session["last_brush_time"]
            start_dt = datetime.datetime.fromtimestamp(ts, tz=dt_util.UTC).replace(
                minute=0, second=0, microsecond=0
            )
            for zone_name, pressure in areas.items():
                area_stats_by_zone.setdefault(zone_name, []).append(
                    StatisticData(
                        start=start_dt,
                        mean=float(pressure),
                        state=float(pressure),
                    )
                )

        for zone_name, stat_rows in area_stats_by_zone.items():
            metadata = StatisticMetaData(
                has_mean=True,
                has_sum=False,
                name=(
                    f"Oclean {self._device_name} Area"
                    f" {zone_name.replace('_', ' ').title()}"
                ),
                source=DOMAIN,
                statistic_id=f"{DOMAIN}:{mac_slug}_area_{zone_name}",
                unit_of_measurement=None,
            )
            try:
                async_add_external_statistics(self.hass, metadata, stat_rows)
            except Exception as err:  # noqa: BLE001
                _LOGGER.warning(
                    "Oclean statistics import failed for area '%s': %s – skipping",
                    zone_name, err,
                )
                continue
            _LOGGER.debug(
                "Oclean imported %d row(s) for area statistic '%s:%s_area_%s'",
                len(stat_rows), DOMAIN, mac_slug, zone_name,
            )

        # Persist the newest session timestamp so next poll knows what's already imported
        max_ts = max(s.get("last_brush_time", 0) for s in new_sessions)
        if max_ts > self._last_session_ts:
            self._last_session_ts = max_ts
            await self._save_store()
            _LOGGER.debug("Oclean updated last_session_ts to %d", self._last_session_ts)

    @staticmethod
    def _load_recorder_api():
        """Load recorder statistics API; return (StatisticData, StatisticMetaData, async_add_external_statistics) or None."""
        try:
            from homeassistant.components.recorder.statistics import (  # noqa: PLC0415
                StatisticData,
                StatisticMetaData,
                async_add_external_statistics,
            )
            return StatisticData, StatisticMetaData, async_add_external_statistics
        except ImportError:
            pass
        try:
            from homeassistant.components.recorder.statistics import async_add_external_statistics  # noqa: PLC0415
            from homeassistant.components.recorder.models import StatisticData, StatisticMetaData  # noqa: PLC0415
            return StatisticData, StatisticMetaData, async_add_external_statistics
        except ImportError:
            return None
