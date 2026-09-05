from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import callback
from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity

from . import BeaconData, signal
from .const import DOMAIN


class DxBeaconEntity(Entity):
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, entry: ConfigEntry, address: str, key: str) -> None:
        self._entry = entry
        self._key = key
        self._attr_unique_id = f"{address}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, address)},
            connections={(CONNECTION_BLUETOOTH, address)},
            name=f"DX-CP27 {address[-5:]}",
            manufacturer="SHEN ZHEN DX-SMART TECHNOLOGY CO.,LTD",
            model="DX-CP27",
        )

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(self.hass, signal(self._entry.entry_id), self._handle)
        )
        data: BeaconData | None = self.hass.data[DOMAIN].get(self._entry.entry_id)
        if data:
            self._apply(data)

    @callback
    def _handle(self, data: BeaconData) -> None:
        self._apply(data)
        self.async_write_ha_state()

    def _apply(self, data: BeaconData) -> None:  # override
        raise NotImplementedError
