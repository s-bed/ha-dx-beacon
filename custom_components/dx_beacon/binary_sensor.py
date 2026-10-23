"""Battery alerts and reception diagnostics for DX-SMART beacons."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import BeaconData
from .const import (
    CONF_ADDRESS,
    CONF_LOW_BATTERY_THRESHOLD,
    DEFAULT_LOW_BATTERY_THRESHOLD,
)
from .entity import DxBeaconEntity

DESCRIPTIONS = (
    BinarySensorEntityDescription(
        key="low_battery",
        device_class=BinarySensorDeviceClass.BATTERY,
    ),
    BinarySensorEntityDescription(
        key="reception",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, add: AddEntitiesCallback
) -> None:
    """Set up the beacon's binary sensors."""
    address = entry.data[CONF_ADDRESS]
    add(DxBeaconBinarySensor(entry, address, desc) for desc in DESCRIPTIONS)


class DxBeaconBinarySensor(DxBeaconEntity, BinarySensorEntity):
    """Expose fresh battery alerts and Bluetooth reception status."""

    def __init__(self, entry, address, desc):
        super().__init__(entry, address, desc.key)
        self.entity_description = desc
        self._attr_translation_key = desc.key
        self._threshold = entry.options.get(
            CONF_LOW_BATTERY_THRESHOLD, DEFAULT_LOW_BATTERY_THRESHOLD
        )
        self._attr_available = desc.key != "low_battery"

    def _apply(self, data: BeaconData) -> None:
        self._attr_extra_state_attributes = self._common_attributes(data)
        if self._key == "reception":
            self._attr_is_on = data.receiving if data.last_seen is not None else None
            return

        stale = "battery_pct" in data.stale_keys
        self._attr_available = (
            data.battery_pct is not None and data.battery_seen is not None and not stale
        )
        self._attr_is_on = (
            data.battery_pct <= self._threshold if self._attr_available else None
        )
        self._attr_extra_state_attributes.update(
            {
                "threshold": self._threshold,
                "last_updated": (
                    data.battery_seen.isoformat() if data.battery_seen else None
                ),
                "is_stale": stale,
            }
        )
