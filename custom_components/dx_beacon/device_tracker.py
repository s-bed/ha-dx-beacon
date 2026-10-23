from __future__ import annotations

from homeassistant.components.device_tracker import SourceType
from homeassistant.components.device_tracker.config_entry import BaseTrackerEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_HOME, STATE_NOT_HOME
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import BeaconData
from .const import CONF_ADDRESS
from .entity import DxBeaconEntity


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, add: AddEntitiesCallback) -> None:
    add([DxBeaconTracker(entry, entry.data[CONF_ADDRESS])])


class DxBeaconTracker(DxBeaconEntity, BaseTrackerEntity):
    _attr_entity_registry_enabled_default = True
    _attr_translation_key = "tracker"

    def __init__(self, entry, address):
        super().__init__(entry, address, "tracker")
        self._present = False

    def _apply(self, data: BeaconData) -> None:
        self._present = data.present
        self._attr_extra_state_attributes = {
            "rssi": data.rssi, "distance": data.distance, "source": data.source,
            "source_name": data.source_name,
            "uuid": data.uuid, "major": data.major, "minor": data.minor,
        }

    @property
    def source_type(self) -> SourceType:
        return SourceType.BLUETOOTH_LE

    @property
    def state(self) -> str:
        return STATE_HOME if self._present else STATE_NOT_HOME
