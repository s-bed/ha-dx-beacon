from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import callback
from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity

from . import BeaconData, signal
from .const import CONF_IDENTITY_ADDRESS, CONF_MODEL, DEFAULT_MODEL, DOMAIN


class DxBeaconEntity(Entity):
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, entry: ConfigEntry, address: str, key: str) -> None:
        self._entry = entry
        self._key = key
        identity = entry.data.get(CONF_IDENTITY_ADDRESS, address)
        model = entry.data.get(CONF_MODEL, DEFAULT_MODEL)
        self._attr_unique_id = f"{identity}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, identity)},
            connections={(CONNECTION_BLUETOOTH, address)},
            name=f"{model} {identity[-5:]}",
            manufacturer="SHEN ZHEN DX-SMART TECHNOLOGY CO.,LTD",
            model=model,
        )

    @staticmethod
    def _common_attributes(data: BeaconData) -> dict:
        """Keep reception and beacon identity available for automations."""
        return {
            "source": data.source,
            "source_name": data.source_name,
            "uuid": data.uuid,
            "major": data.major,
            "minor": data.minor,
        }

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, signal(self._entry.entry_id), self._handle
            )
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
