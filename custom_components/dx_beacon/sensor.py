from __future__ import annotations

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    PERCENTAGE,
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    EntityCategory,
    UnitOfElectricPotential,
    UnitOfLength,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import BeaconData
from .const import CONF_ADDRESS
from .entity import DxBeaconEntity

D = SensorEntityDescription
DESCRIPTIONS: tuple[SensorEntityDescription, ...] = (
    D(
        key="source_name",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    D(
        key="distance",
        native_unit_of_measurement=UnitOfLength.METERS,
        device_class=SensorDeviceClass.DISTANCE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    D(
        key="rssi",
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    D(
        key="battery_pct",
        native_unit_of_measurement=PERCENTAGE,
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    D(
        key="temperature",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    D(
        key="tx_power",
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    D(
        key="battery_mv",
        native_unit_of_measurement=UnitOfElectricPotential.MILLIVOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    D(
        key="adv_count",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    D(
        key="uptime_s",
        native_unit_of_measurement=UnitOfTime.SECONDS,
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        suggested_display_precision=0,
    ),
    D(
        key="last_seen",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    D(
        key="last_reboot",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
)

TLM_KEYS = frozenset({"battery_mv", "temperature", "adv_count", "uptime_s"})


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, add: AddEntitiesCallback
) -> None:
    address = entry.data[CONF_ADDRESS]
    add(DxBeaconSensor(entry, address, d) for d in DESCRIPTIONS)


class DxBeaconSensor(DxBeaconEntity, SensorEntity):
    def __init__(self, entry, address, desc):
        super().__init__(entry, address, desc.key)
        self.entity_description = desc
        self._attr_translation_key = desc.key
        self._is_stale = False

    def _apply(self, data: BeaconData) -> None:
        self._attr_native_value = getattr(data, self._key)
        self._is_stale = self._key in data.stale_keys
        if self._key == "battery_pct":
            updated = data.battery_seen
        elif self._key in TLM_KEYS:
            updated = data.tlm_seen
        elif self._key == "tx_power":
            updated = data.ibeacon_seen
        elif self._key == "last_reboot":
            updated = data.last_reboot
        else:
            updated = data.last_seen
        self._attr_extra_state_attributes = {
            **self._common_attributes(data),
            "last_updated": updated.isoformat() if updated else None,
            "is_stale": self._is_stale,
        }

    @property
    def available(self) -> bool:
        return self._attr_native_value is not None and (
            self._key == "battery_pct" or not self._is_stale
        )
