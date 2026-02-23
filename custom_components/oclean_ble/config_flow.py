"""Config flow for the Oclean Toothbrush integration."""
from __future__ import annotations

import logging
import re
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.components import bluetooth
from homeassistant.components.bluetooth import BluetoothServiceInfoBleak
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import selector

from .const import (
    CONF_DEVICE_NAME,
    CONF_MAC_ADDRESS,
    CONF_POLL_INTERVAL,
    CONF_POLL_WINDOWS,
    CONF_POST_BRUSH_COOLDOWN,
    CONF_WINDOW_COUNT,
    CONF_WINDOW_END,
    CONF_WINDOW_START,
    DEFAULT_POLL_INTERVAL,
    DEFAULT_POST_BRUSH_COOLDOWN,
    DOMAIN,
    MIN_POLL_INTERVAL,
    OCLEAN_SERVICE_UUID,
)

_LOGGER = logging.getLogger(__name__)

_MAC_RE = re.compile(r"^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")

def _parse_windows_list(windows_str: str) -> list[tuple[str, str]]:
    """Parse 'HH:MM-HH:MM[, ...]' into a list of ('HH:MM:00', 'HH:MM:00') tuples.

    The ':00' suffix makes the values directly usable as TimeSelector defaults.
    """
    result: list[tuple[str, str]] = []
    for part in (windows_str or "").split(",")[:3]:
        part = part.strip()
        if "-" not in part:
            continue
        try:
            s_raw, e_raw = part.split("-", 1)
            s_raw, e_raw = s_raw.strip(), e_raw.strip()
            sh, sm = s_raw.split(":")
            eh, em = e_raw.split(":")
            int(sh), int(sm), int(eh), int(em)  # validate
        except (ValueError, AttributeError):
            continue
        result.append((f"{s_raw}:00", f"{e_raw}:00"))
    return result


def _windows_list_to_str(windows: list[tuple[str, str]]) -> str:
    """Combine ('HH:MM:SS', 'HH:MM:SS') tuples into 'HH:MM-HH:MM[, ...]' for storage."""
    parts: list[str] = []
    for s, e in windows:
        s5, e5 = s[:5], e[:5]  # "HH:MM:SS" → "HH:MM"
        if s5 and e5 and s5 != e5:
            parts.append(f"{s5}-{e5}")
    return ", ".join(parts)


class OcleanConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Oclean."""

    VERSION = 1

    def __init__(self) -> None:
        self._discovered_devices: dict[str, str] = {}  # mac -> name
        self._mac: str | None = None
        self._name: str | None = None

    # ------------------------------------------------------------------
    # Bluetooth discovery (passive, triggered by HA bluetooth component)
    # ------------------------------------------------------------------

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> FlowResult:
        """Handle device discovered via bluetooth integration."""
        await self.async_set_unique_id(discovery_info.address)
        self._abort_if_unique_id_configured()

        self._mac = discovery_info.address
        self._name = discovery_info.name or "Oclean"

        self.context["title_placeholders"] = {"name": self._name}
        return await self.async_step_confirm()

    async def async_step_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Confirm a bluetooth-discovered device."""
        if user_input is not None:
            poll_interval = user_input.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL)
            return self.async_create_entry(
                title=self._name or "Oclean",
                data={
                    CONF_MAC_ADDRESS: self._mac,
                    CONF_DEVICE_NAME: self._name,
                    CONF_POLL_INTERVAL: poll_interval,
                },
            )

        return self.async_show_form(
            step_id="confirm",
            description_placeholders={"name": self._name, "mac": self._mac},
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_POLL_INTERVAL, default=DEFAULT_POLL_INTERVAL
                    ): vol.All(int, vol.Range(min=MIN_POLL_INTERVAL)),
                }
            ),
        )

    # ------------------------------------------------------------------
    # Manual setup flow (user initiates via "Add Integration")
    # ------------------------------------------------------------------

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step – show discovered devices or manual entry."""
        # Scan for already-discovered Oclean devices
        discovered = bluetooth.async_discovered_service_info(self.hass)
        for info in discovered:
            if OCLEAN_SERVICE_UUID.lower() in [s.lower() for s in info.service_uuids]:
                self._discovered_devices[info.address] = info.name or info.address

        if self._discovered_devices and user_input is None:
            return await self.async_step_pick_device()

        return await self.async_step_manual(user_input)

    async def async_step_pick_device(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Let the user pick from discovered devices."""
        errors: dict[str, str] = {}

        if user_input is not None:
            mac = user_input[CONF_MAC_ADDRESS]
            await self.async_set_unique_id(mac)
            self._abort_if_unique_id_configured()
            self._mac = mac
            self._name = self._discovered_devices.get(mac, "Oclean")
            poll_interval = user_input.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL)
            return self.async_create_entry(
                title=self._name,
                data={
                    CONF_MAC_ADDRESS: mac,
                    CONF_DEVICE_NAME: self._name,
                    CONF_POLL_INTERVAL: poll_interval,
                },
            )

        device_options = {
            mac: f"{name} ({mac})"
            for mac, name in self._discovered_devices.items()
        }

        return self.async_show_form(
            step_id="pick_device",
            errors=errors,
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_MAC_ADDRESS): vol.In(device_options),
                    vol.Optional(
                        CONF_POLL_INTERVAL, default=DEFAULT_POLL_INTERVAL
                    ): vol.All(int, vol.Range(min=MIN_POLL_INTERVAL)),
                }
            ),
        )

    async def async_step_manual(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle manual MAC address entry."""
        errors: dict[str, str] = {}

        if user_input is not None:
            mac = user_input[CONF_MAC_ADDRESS].upper().strip()
            if not _MAC_RE.match(mac):
                errors[CONF_MAC_ADDRESS] = "invalid_mac"
            else:
                await self.async_set_unique_id(mac)
                self._abort_if_unique_id_configured()
                poll_interval = user_input.get(
                    CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL
                )
                return self.async_create_entry(
                    title=user_input.get(CONF_DEVICE_NAME, "Oclean"),
                    data={
                        CONF_MAC_ADDRESS: mac,
                        CONF_DEVICE_NAME: user_input.get(CONF_DEVICE_NAME, "Oclean"),
                        CONF_POLL_INTERVAL: poll_interval,
                    },
                )

        return self.async_show_form(
            step_id="manual",
            errors=errors,
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_MAC_ADDRESS): str,
                    vol.Optional(CONF_DEVICE_NAME, default="Oclean"): str,
                    vol.Optional(
                        CONF_POLL_INTERVAL, default=DEFAULT_POLL_INTERVAL
                    ): vol.All(int, vol.Range(min=MIN_POLL_INTERVAL)),
                }
            ),
        )

    # ------------------------------------------------------------------
    # Options flow (change poll interval after setup)
    # ------------------------------------------------------------------

    @staticmethod
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> OcleanOptionsFlow:
        return OcleanOptionsFlow()


class OcleanOptionsFlow(config_entries.OptionsFlow):
    """Handle Oclean options via a multi-step flow.

    Step 1 (init):     poll interval, post-brush cooldown, number of poll windows (0-3)
    Step 2-4 (window_1/2/3): one TimeSelector pair per requested window

    Note: self.config_entry is injected automatically by HA since 2024.3.
    """

    def __init__(self) -> None:
        self._poll_interval: int = DEFAULT_POLL_INTERVAL
        self._cooldown: int = DEFAULT_POST_BRUSH_COOLDOWN
        self._window_count: int = 0
        # Windows parsed from the current config – used to pre-fill each window step.
        self._existing_windows: list[tuple[str, str]] = []
        # Windows collected step-by-step as the user fills them in.
        self._collected_windows: list[tuple[str, str]] = []

    # ------------------------------------------------------------------
    # Step 1 – global settings + window count
    # ------------------------------------------------------------------

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        if user_input is not None:
            self._poll_interval = user_input[CONF_POLL_INTERVAL]
            self._cooldown = int(
                user_input.get(CONF_POST_BRUSH_COOLDOWN, DEFAULT_POST_BRUSH_COOLDOWN)
            )
            self._window_count = int(user_input.get(CONF_WINDOW_COUNT, 0))
            self._collected_windows = []
            if self._window_count > 0:
                return await self.async_step_window_1()
            return self.async_create_entry(
                title="",
                data={
                    CONF_POLL_INTERVAL: self._poll_interval,
                    CONF_POST_BRUSH_COOLDOWN: self._cooldown,
                    CONF_POLL_WINDOWS: "",
                },
            )

        current_interval = self.config_entry.options.get(
            CONF_POLL_INTERVAL,
            self.config_entry.data.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL),
        )
        current_cooldown = int(
            self.config_entry.options.get(CONF_POST_BRUSH_COOLDOWN, DEFAULT_POST_BRUSH_COOLDOWN)
        )
        self._existing_windows = _parse_windows_list(
            self.config_entry.options.get(CONF_POLL_WINDOWS, "")
        )
        current_count = len(self._existing_windows)

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_POLL_INTERVAL, default=current_interval): vol.All(
                        int, vol.Range(min=MIN_POLL_INTERVAL)
                    ),
                    vol.Optional(
                        CONF_POST_BRUSH_COOLDOWN, default=current_cooldown
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=0,
                            max=23,
                            step=1,
                            unit_of_measurement="h",
                            mode=selector.NumberSelectorMode.BOX,
                        )
                    ),
                    vol.Optional(
                        CONF_WINDOW_COUNT, default=current_count
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=0, max=3, step=1, mode=selector.NumberSelectorMode.BOX
                        )
                    ),
                }
            ),
        )

    # ------------------------------------------------------------------
    # Steps 2-4 – one step per poll window
    # ------------------------------------------------------------------

    async def async_step_window_1(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        return await self._async_step_window(1, user_input)

    async def async_step_window_2(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        return await self._async_step_window(2, user_input)

    async def async_step_window_3(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        return await self._async_step_window(3, user_input)

    async def _async_step_window(
        self, num: int, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            s = (user_input.get(CONF_WINDOW_START) or "").strip()
            e = (user_input.get(CONF_WINDOW_END) or "").strip()
            if not s:
                errors[CONF_WINDOW_START] = "window_incomplete"
            elif not e:
                errors[CONF_WINDOW_END] = "window_incomplete"

            if not errors:
                self._collected_windows.append((s, e))
                if num < self._window_count:
                    return await getattr(self, f"async_step_window_{num + 1}")()
                return self.async_create_entry(
                    title="",
                    data={
                        CONF_POLL_INTERVAL: self._poll_interval,
                        CONF_POST_BRUSH_COOLDOWN: self._cooldown,
                        CONF_POLL_WINDOWS: _windows_list_to_str(self._collected_windows),
                    },
                )

        # Pre-populate from current config if this window already existed.
        existing = (
            self._existing_windows[num - 1]
            if num - 1 < len(self._existing_windows)
            else None
        )
        s_default = existing[0] if existing else None
        e_default = existing[1] if existing else None

        return self.async_show_form(
            step_id=f"window_{num}",
            errors=errors,
            data_schema=vol.Schema(
                {
                    (
                        vol.Required(CONF_WINDOW_START, default=s_default)
                        if s_default
                        else vol.Required(CONF_WINDOW_START)
                    ): selector.TimeSelector(),
                    (
                        vol.Required(CONF_WINDOW_END, default=e_default)
                        if e_default
                        else vol.Required(CONF_WINDOW_END)
                    ): selector.TimeSelector(),
                }
            ),
        )
