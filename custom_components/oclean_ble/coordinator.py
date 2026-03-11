"""DataUpdateCoordinator for the Oclean integration."""

from __future__ import annotations

import asyncio
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
    BLE_PAGINATION_TIMEOUT,
    BLE_POST_CONNECT_DELAY,
    BLE_READ_FALLBACK_DELAY,
    BLE_SUBSCRIBE_TIMEOUT,
    CMD_CALIBRATE_TIME_PREFIX,
    CMD_CLEAR_BRUSH_HEAD,
    CMD_QUERY_RUNNING_DATA_NEXT,
    DATA_BATTERY,
    DATA_BRUSH_HEAD_USAGE,
    DATA_HW_REVISION,
    DATA_LAST_BRUSH_AREAS,
    DATA_LAST_BRUSH_DURATION,
    DATA_LAST_BRUSH_PNUM,
    DATA_LAST_BRUSH_PRESSURE,
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
    STORAGE_VERSION,
    WRITE_CHAR_UUID,
)
from .models import OcleanDeviceData
from .parser import (
    T1_C3352G_RECORD_SIZE,
    parse_battery,
    parse_notification,
    parse_t1_c3352g_record,
)
from .protocol import UNKNOWN, DeviceProtocol, protocol_for_model
from .statistics import import_new_sessions

_LOGGER = logging.getLogger(__name__)


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

        # Software brush-head usage counter.
        # Used when the device does not expose a hardware counter via BLE (0308 bytes 14-15).
        # Once a hardware value is ever received, _brush_head_hw_supported is set to True
        # and the software counter is no longer written to the sensor.
        self._brush_head_sw_count: int = 0
        self._brush_head_hw_supported: bool = False

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
            await client.write_gatt_char(WRITE_CHAR_UUID, CMD_CLEAR_BRUSH_HEAD, response=True)
            self._log.info("brush head counter reset sent")
        finally:
            if client.is_connected:
                await client.disconnect()

        # Reset software counter regardless of hw support (covers both cases)
        self._brush_head_sw_count = 0
        await self._save_store()
        self._log.debug("brush head sw counter reset to 0")
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
            await self._calibrate_time(client)
        finally:
            if client.is_connected:
                await client.disconnect()

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

    def _resolve_ble_device(self):
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
            self._brush_head_sw_count = stored.get("brush_head_count", 0)
            self._brush_head_hw_supported = stored.get("brush_head_hw", False)
            last_session = stored.get("last_session", {})
            if last_session:
                self._last_raw.update(last_session)
            self._log.debug(
                "loaded store: last_session_ts=%d, brush_head_count=%d, hw=%s",
                self._last_session_ts,
                self._brush_head_sw_count,
                self._brush_head_hw_supported,
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
                "brush_head_count": self._brush_head_sw_count,
                "brush_head_hw": self._brush_head_hw_supported,
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
            all_sessions = await self._setup_and_read(client, collected)
        finally:
            if client.is_connected:
                await client.disconnect()

        self.last_poll_successful = True

        # Detect hardware brush-head counter support
        if collected.get(DATA_BRUSH_HEAD_USAGE) is not None:
            self._brush_head_hw_supported = True

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

        # Software brush-head counter: increment per new session when hw not supported
        if not self._brush_head_hw_supported:
            if new_session_count > 0:
                self._brush_head_sw_count += new_session_count
                self._log.debug(
                    "brush head sw counter: +%d → %d",
                    new_session_count,
                    self._brush_head_sw_count,
                )
            collected[DATA_BRUSH_HEAD_USAGE] = self._brush_head_sw_count

        # Merge with last known persistent data, then overwrite with fresh values
        merged = {**{k: self._last_raw.get(k) for k in _PERSISTENT_KEYS}, **collected}
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
        handler = self._make_notification_handler(collected, all_sessions, seen_ts, session_received)

        await self._run_ble_queries(client, collected, all_sessions, session_received, handler)
        self._finalize_sessions(collected, all_sessions)
        return all_sessions

    def _make_notification_handler(
        self,
        collected: dict[str, Any],
        all_sessions: list[dict[str, Any]],
        seen_ts: set[int],
        session_received: asyncio.Event,
    ) -> Callable[[Any, bytearray], None]:
        """Return a BLE notification callback that accumulates session data.

        The returned handler parses each notification, applies the "newer
        timestamp wins" policy to prevent a stale 5a00 push from overwriting
        a more-recent 0307 timestamp, merges fields into *collected*, and
        appends new sessions to *all_sessions*.

        Multi-packet reassembly (*B# format):
        OCLEANY3 sends session records split across multiple BLE packets: a
        0307 *B# header packet with record_count + inline data, followed by
        raw continuation packets until all record_count × 42 bytes are received.
        The handler detects the header (payload[5] = year_base != 0 means real
        data), accumulates continuation chunks, and flushes complete records
        through ``parse_t1_c3352g_record`` when the buffer is full.
        OCLEANY3P sends the same *B# header but with year_base=0 (placeholder);
        its actual session data arrives separately via 021f/5100 notifications
        and is handled by the normal dispatch path.
        """
        _log = self._log  # capture for use in the closure

        # Mutable reassembly state – use a dict to avoid 'nonlocal' for primitives.
        _t1: dict[str, Any] = {"in_progress": False, "buf": bytearray(), "expected": 0}

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
            _t1["in_progress"] = False
            _t1["buf"] = bytearray()
            _t1["expected"] = 0
            num_records = len(buf) // T1_C3352G_RECORD_SIZE
            _log.debug("*B# reassembly complete: parsing %d record(s)", num_records)
            for i in range(num_records):
                chunk = buf[i * T1_C3352G_RECORD_SIZE : (i + 1) * T1_C3352G_RECORD_SIZE]
                _accept(parse_t1_c3352g_record(chunk))

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
            # payload[5] = year_base of the first record (year − 2000).
            # year_base != 0 → real session records from OCLEANY3 → start reassembly.
            # year_base == 0 → OCLEANY3P placeholder (no inline data; sessions arrive
            #   via 021f/5100); let _accept() handle the already-parsed result.
            if len(data) >= 8 and data[2:5] == _T1_MAGIC:
                payload = data[2:]  # strip 0307 prefix
                record_count = (payload[3] << 8) | payload[4]
                if record_count > 0 and payload[5] != 0x00:
                    total_expected = record_count * T1_C3352G_RECORD_SIZE
                    inline = bytearray(payload[5:])  # bytes already in this packet (from record byte 0)
                    _t1["buf"] = inline
                    _t1["expected"] = total_expected
                    _t1["in_progress"] = True
                    _log.debug(
                        "*B# header: count=%d, expected=%d bytes, inline=%d bytes",
                        record_count,
                        total_expected,
                        len(inline),
                    )
                    if len(inline) >= total_expected:
                        _flush_t1_buffer()
                    # parse_notification returned {} for this case (deferred branch);
                    # do not call _accept – reassembly handles the data.
                    return

            _accept(parsed)

        return handler

    async def _run_ble_queries(
        self,
        client: BleakClient,
        collected: dict[str, Any],
        all_sessions: list[dict[str, Any]],
        session_received: asyncio.Event,
        handler: Callable[[Any, bytearray], None],
    ) -> None:
        """Execute the full GATT operation sequence for one poll.

        Mirrors the Java SDK order (C3335a / C3340b1):
          1. Time calibration  (020E + BE timestamp)   – mo5289B
          2. DIS read / cache
          3. Subscribe to notification characteristics
          4. Status + running-data query commands
          5. READ fallback for devices without CCCD (e.g. OCLEANA1)
          6. Session pagination (0309)
          7. Enrichment wait if sessions were received
          8. Battery read
        """
        await self._calibrate_time(client)
        await self._read_device_info_service(client, collected)
        subscribed = await self._subscribe_notifications(client, handler)
        await self._send_query_commands(client, session_received)
        # Fallback for devices (e.g. OCLEANA1) where READ_NOTIFY_CHAR_UUID has no
        # CCCD and cannot be subscribed; poll the characteristic directly instead.
        if READ_NOTIFY_CHAR_UUID not in subscribed:
            await self._read_response_char_fallback(client, handler)
        await self._paginate_sessions(client, all_sessions, session_received)
        if all_sessions:
            # Allow the device extra time to push enrichment notifications
            # (0000 score, 2604 zone pressures) that arrive unsolicited after
            # the session response.
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
        """Send time-calibration command (020E + BE timestamp)."""
        timestamp = int(time.time())
        time_bytes = struct.pack(">I", timestamp)
        cal_cmd = CMD_CALIBRATE_TIME_PREFIX + time_bytes
        try:
            await client.write_gatt_char(WRITE_CHAR_UUID, cal_cmd, response=True)
            self._log.debug("time calibration sent (ts=%d)", timestamp)
        except Exception as err:  # noqa: BLE001
            self._log.warning("time calibration failed: %s (%s)", err, type(err).__name__)

    async def _subscribe_notifications(
        self, client: BleakClient, handler: Callable[[Any, bytearray], None]
    ) -> frozenset[str]:
        """Subscribe to notification characteristics for the active device protocol.

        Returns the set of UUIDs successfully subscribed.  Callers use this to
        detect devices (e.g. OCLEANA1) that don't support CCCD-based notifications
        on READ_NOTIFY_CHAR_UUID and must be polled via direct READ instead.
        """
        subscribed: set[str] = set()
        for char_uuid in self._protocol.notify_chars:
            try:
                await asyncio.wait_for(
                    client.start_notify(char_uuid, handler),
                    timeout=BLE_SUBSCRIBE_TIMEOUT,
                )
                subscribed.add(char_uuid)
                self._log.debug("subscribed to %s", char_uuid)
            except Exception as err:  # noqa: BLE001
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
        poll the characteristic after a short delay and feed the result through
        the same notification_handler as if it had arrived as a notify event.
        """
        await asyncio.sleep(BLE_READ_FALLBACK_DELAY)
        try:
            raw = await client.read_gatt_char(READ_NOTIFY_CHAR_UUID)
            data = bytes(raw)
            self._log.debug("READ fallback on READ_NOTIFY_CHAR: %s", data.hex())
            if len(data) > 2:
                handler(None, bytearray(data))
        except Exception as err:  # noqa: BLE001
            self._log.debug("READ fallback failed: %s (%s)", err, type(err).__name__)

    async def _send_query_commands(self, client: BleakClient, session_received: asyncio.Event) -> None:
        """Send query commands for the active device protocol; wait for first session.

        Commands are defined by the protocol profile so only appropriate commands
        are sent for the connected device.  Failures are logged at DEBUG level
        since unexpected commands are simply ignored by the firmware.
        """
        for char_uuid, cmd in self._protocol.query_commands:
            try:
                await client.write_gatt_char(char_uuid, cmd, response=True)
                self._log.debug("command 0x%s sent to ...%s", cmd.hex(), char_uuid[-8:])
            except Exception as err:  # noqa: BLE001
                self._log.debug(
                    "command 0x%s skipped: %s (%s)",
                    cmd.hex(),
                    err,
                    type(err).__name__,
                )

        # Wait for first session notification (or timeout if device has no records)
        try:
            await asyncio.wait_for(session_received.wait(), timeout=float(BLE_NOTIFICATION_WAIT))
        except asyncio.TimeoutError:
            self._log.debug(
                "no session notification within %.1f s (device may have no records)",
                float(BLE_NOTIFICATION_WAIT),
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
                await client.write_gatt_char(WRITE_CHAR_UUID, CMD_QUERY_RUNNING_DATA_NEXT, response=True)
            except BaseException as err:  # noqa: BLE001  # must catch CancelledError (issue #9)
                self._log.debug("0309 write failed at page %d: %s", page, err)
                break

            try:
                await asyncio.wait_for(session_received.wait(), timeout=BLE_PAGINATION_TIMEOUT)
            except asyncio.TimeoutError:
                self._log.debug("no more sessions after page %d", page)
                break

    async def _read_battery_and_unsubscribe(self, client: BleakClient, collected: dict[str, Any]) -> None:
        """Read battery level via GATT.

        stop_notify is intentionally omitted: the BLE disconnect in _poll_device's
        finally block tears down all subscriptions automatically, saving 4 extra
        GATT round-trips (~0.4 s) per poll.
        """
        self._log.debug("poll collected so far: %s", collected)
        try:
            batt_raw = await client.read_gatt_char(BATTERY_CHAR_UUID)
            self._log.debug("battery raw: %s", bytes(batt_raw).hex())
            batt = parse_battery(bytes(batt_raw))
            if batt is not None:
                collected[DATA_BATTERY] = batt
        except Exception as err:  # noqa: BLE001
            self._log.warning("battery read failed: %s (%s)", err, type(err).__name__)
