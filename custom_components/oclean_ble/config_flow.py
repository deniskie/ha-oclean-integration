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

from .const import (
    CONF_DEVICE_NAME,
    CONF_MAC_ADDRESS,
    CONF_POLL_INTERVAL,
    DEFAULT_POLL_INTERVAL,
    DOMAIN,
    MIN_POLL_INTERVAL,
    OCLEAN_SERVICE_UUID,
)

_LOGGER = logging.getLogger(__name__)

_MAC_RE = re.compile(r"^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")


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
    """Handle Oclean options (poll interval).

    Note: self.config_entry is injected automatically by HA since 2024.3.
    """

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        current_interval = self.config_entry.options.get(
            CONF_POLL_INTERVAL,
            self.config_entry.data.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL),
        )
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_POLL_INTERVAL, default=current_interval): vol.All(
                        int, vol.Range(min=MIN_POLL_INTERVAL)
                    ),
                }
            ),
        )
