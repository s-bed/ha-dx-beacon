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
    EntityCategory,
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
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
    D(key="distance", name="Distanza stimata", native_unit_of_measurement=UnitOfLength.METERS,
      device_class=SensorDeviceClass.DISTANCE, state_class=SensorStateClass.MEASUREMENT,
      suggested_display_precision=2),
    D(key="rssi", name="Intensità del segnale", native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
      device_class=SensorDeviceClass.SIGNAL_STRENGTH, state_class=SensorStateClass.MEASUREMENT),
    D(key="battery_pct", name="Batteria", native_unit_of_measurement=PERCENTAGE,
      device_class=SensorDeviceClass.BATTERY, state_class=SensorStateClass.MEASUREMENT),
    D(key="temperature", name="Temperatura chip", native_unit_of_measurement=UnitOfTemperature.CELSIUS,
      device_class=SensorDeviceClass.TEMPERATURE, state_class=SensorStateClass.MEASUREMENT,
      suggested_display_precision=1),
    D(key="tx_power", name="Potenza a 1 m", native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
      device_class=SensorDeviceClass.SIGNAL_STRENGTH, entity_category=EntityCategory.DIAGNOSTIC),
    D(key="battery_mv", name="Tensione batteria", native_unit_of_measurement=UnitOfElectricPotential.MILLIVOLT,
      device_class=SensorDeviceClass.VOLTAGE, state_class=SensorStateClass.MEASUREMENT,
      entity_category=EntityCategory.DIAGNOSTIC),
    D(key="adv_count", name="Pacchetti inviati", state_class=SensorStateClass.TOTAL_INCREASING,
      entity_category=EntityCategory.DIAGNOSTIC),
    D(key="uptime_s", name="Uptime", native_unit_of_measurement=UnitOfTime.SECONDS,
      device_class=SensorDeviceClass.DURATION, state_class=SensorStateClass.TOTAL_INCREASING,
      entity_category=EntityCategory.DIAGNOSTIC, suggested_display_precision=0),
)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, add: AddEntitiesCallback) -> None:
    address = entry.data[CONF_ADDRESS]
    add(DxBeaconSensor(entry, address, d) for d in DESCRIPTIONS)


class DxBeaconSensor(DxBeaconEntity, SensorEntity):
    def __init__(self, entry, address, desc):
        super().__init__(entry, address, desc.key)
        self.entity_description = desc
        self._attr_translation_key = desc.key

    def _apply(self, data: BeaconData) -> None:
        self._attr_native_value = getattr(data, self._key)
        self._attr_extra_state_attributes = {
            "source": data.source, "uuid": data.uuid, "major": data.major, "minor": data.minor,
        }

    @property
    def available(self) -> bool:
        return self._attr_native_value is not None
