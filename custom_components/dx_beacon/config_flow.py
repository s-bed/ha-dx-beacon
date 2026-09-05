from __future__ import annotations

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.components.bluetooth import (
    BluetoothServiceInfoBleak,
    async_discovered_service_info,
)
from homeassistant.helpers.device_registry import format_mac

from .const import CONF_ADDRESS, DOMAIN, EDDYSTONE_UUID


class DxBeaconConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._discovered: dict[str, str] = {}
        self._address: str | None = None

    async def async_step_bluetooth(self, info: BluetoothServiceInfoBleak):
        address = format_mac(info.address).upper()
        await self.async_set_unique_id(address)
        self._abort_if_unique_id_configured()
        self._address = address
        self.context["title_placeholders"] = {"name": address}
        return await self.async_step_confirm()

    async def async_step_confirm(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(
                title=f"DX beacon {self._address}", data={CONF_ADDRESS: self._address}
            )
        return self.async_show_form(
            step_id="confirm", description_placeholders={"name": self._address}
        )

    async def async_step_user(self, user_input=None):
        if user_input is not None:
            address = format_mac(user_input[CONF_ADDRESS]).upper()
            await self.async_set_unique_id(address)
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title=f"DX beacon {address}", data={CONF_ADDRESS: address}
            )
        for info in async_discovered_service_info(self.hass, connectable=False):
            if EDDYSTONE_UUID in info.service_data:
                self._discovered[format_mac(info.address).upper()] = (
                    f"{info.name or 'Eddystone'} ({info.address})"
                )
        schema = vol.Schema(
            {
                vol.Required(CONF_ADDRESS): vol.In(self._discovered)
                if self._discovered
                else str
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema)
