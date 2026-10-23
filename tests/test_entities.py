"""Entity identity, freshness, and battery alert behavior."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from homeassistant.components.binary_sensor import BinarySensorDeviceClass
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.const import (
    STATE_HOME,
    STATE_NOT_HOME,
    STATE_UNKNOWN,
    EntityCategory,
)
from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH

from custom_components.dx_beacon import BeaconData
from custom_components.dx_beacon.binary_sensor import (
    DESCRIPTIONS as BINARY_DESCRIPTIONS,
)
from custom_components.dx_beacon.binary_sensor import (
    DxBeaconBinarySensor,
)
from custom_components.dx_beacon.const import (
    CONF_ADDRESS,
    CONF_IDENTITY_ADDRESS,
    CONF_LOW_BATTERY_THRESHOLD,
    CONF_MODEL,
    DEFAULT_MODEL,
    DOMAIN,
)
from custom_components.dx_beacon.device_tracker import DxBeaconTracker
from custom_components.dx_beacon.sensor import DESCRIPTIONS, DxBeaconSensor

BEACON = "AA:BB:CC:DD:EE:FF"
REPLACEMENT = "AA:BB:CC:DD:EE:00"
NOW = datetime(2026, 10, 23, 18, 0, tzinfo=UTC)
SENSOR_DESCRIPTIONS = {desc.key: desc for desc in DESCRIPTIONS}
BINARY_SENSOR_DESCRIPTIONS = {desc.key: desc for desc in BINARY_DESCRIPTIONS}


def entry(data=None, options=None):
    """Build only the config-entry fields used by the entities."""
    return SimpleNamespace(
        entry_id="beacon-1",
        data={CONF_ADDRESS: BEACON, **(data or {})},
        options=options or {},
    )


def sensor(key):
    return DxBeaconSensor(entry(), BEACON, SENSOR_DESCRIPTIONS[key])


def binary_sensor(key, options=None):
    return DxBeaconBinarySensor(
        entry(options=options), BEACON, BINARY_SENSOR_DESCRIPTIONS[key]
    )


def test_replacement_keeps_entity_and_device_identity():
    """A new radio address must not create a second set of entities."""
    original = DxBeaconTracker(entry(), BEACON)
    replacement = DxBeaconTracker(
        entry({CONF_ADDRESS: REPLACEMENT, CONF_IDENTITY_ADDRESS: BEACON}), REPLACEMENT
    )
    assert replacement.unique_id == original.unique_id == f"{BEACON}_tracker"
    assert replacement.device_info["identifiers"] == {(DOMAIN, BEACON)}
    assert replacement.device_info["connections"] == {
        (CONNECTION_BLUETOOTH, REPLACEMENT)
    }


def test_model_defaults_and_override():
    default = DxBeaconTracker(entry(), BEACON)
    assert default.device_info["model"] == DEFAULT_MODEL
    assert default.device_info["name"] == f"{DEFAULT_MODEL} {BEACON[-5:]}"
    overridden = DxBeaconTracker(entry({CONF_MODEL: "DX-CP27"}), BEACON)
    assert overridden.device_info["model"] == "DX-CP27"


def test_tracker_starts_unknown_and_exposes_last_seen():
    tracker = DxBeaconTracker(entry(), BEACON)
    tracker._apply(BeaconData())
    assert tracker.state == STATE_UNKNOWN
    tracker._apply(BeaconData(present=True, last_seen=NOW))
    assert tracker.state == STATE_HOME
    assert tracker.extra_state_attributes["last_seen"] == NOW.isoformat()
    tracker._apply(BeaconData(present=False, last_seen=NOW))
    assert tracker.state == STATE_NOT_HOME


@pytest.mark.parametrize("key", ["rssi", "distance", "source_name", "tx_power"])
def test_optional_reception_entities_are_diagnostics(key):
    entity = sensor(key)
    assert entity.entity_category == EntityCategory.DIAGNOSTIC
    assert not entity.entity_registry_enabled_default


def test_battery_and_last_seen_are_enabled_by_default():
    assert sensor("battery_pct").entity_registry_enabled_default
    assert sensor("last_seen").entity_registry_enabled_default
    assert not sensor("last_reboot").entity_registry_enabled_default


@pytest.mark.parametrize(
    ("key", "timestamp_field"),
    [
        ("battery_pct", "battery_seen"),
        ("battery_mv", "tlm_seen"),
        ("temperature", "tlm_seen"),
        ("adv_count", "tlm_seen"),
        ("uptime_s", "tlm_seen"),
        ("tx_power", "ibeacon_seen"),
        ("rssi", "last_seen"),
        ("distance", "last_seen"),
        ("source_name", "last_seen"),
    ],
)
def test_each_sensor_reports_its_own_frame_timestamp(key, timestamp_field):
    data = BeaconData(
        battery_pct=75,
        battery_mv=3000,
        temperature=21.5,
        adv_count=100,
        uptime_s=60,
        tx_power=-59,
        rssi=-60,
        distance=1.2,
        source_name="Hallway proxy",
        battery_seen=NOW - timedelta(minutes=10),
        tlm_seen=NOW - timedelta(minutes=5),
        ibeacon_seen=NOW - timedelta(minutes=1),
        last_seen=NOW,
    )
    entity = sensor(key)
    entity._apply(data)
    assert entity.available
    assert (
        entity.extra_state_attributes["last_updated"]
        == getattr(data, timestamp_field).isoformat()
    )
    assert not entity.extra_state_attributes["is_stale"]


def test_expired_telemetry_is_unavailable_but_battery_remains_last_known():
    data = BeaconData(
        present=True,
        receiving=True,
        last_seen=NOW,
        battery_pct=12,
        battery_seen=NOW - timedelta(hours=2),
        temperature=20.0,
        tlm_seen=NOW - timedelta(hours=2),
        stale_keys=frozenset({"temperature", "battery_pct"}),
    )
    temperature = sensor("temperature")
    temperature._apply(data)
    assert not temperature.available
    assert temperature.extra_state_attributes["is_stale"]
    battery = sensor("battery_pct")
    battery._apply(data)
    assert battery.available
    assert battery.native_value == 12
    assert battery.extra_state_attributes["is_stale"]
    assert (
        battery.extra_state_attributes["last_updated"] == data.battery_seen.isoformat()
    )


@pytest.mark.parametrize("key", ["last_seen", "last_reboot"])
def test_timestamp_sensors_publish_aware_datetimes(key):
    entity = sensor(key)
    entity._apply(BeaconData(**{key: NOW}))
    assert entity.device_class == SensorDeviceClass.TIMESTAMP
    assert entity.native_value == NOW
    assert entity.available


def test_reception_is_unknown_then_tracks_reception_independently_of_presence():
    entity = binary_sensor("reception")
    entity._apply(BeaconData())
    assert entity.available
    assert entity.is_on is None
    assert entity.device_class == BinarySensorDeviceClass.CONNECTIVITY
    entity._apply(BeaconData(last_seen=NOW, receiving=True, present=True))
    assert entity.is_on is True
    entity._apply(BeaconData(last_seen=NOW, receiving=False, present=True))
    assert entity.is_on is False


@pytest.mark.parametrize(("level", "expected"), [(21, False), (20, True), (0, True)])
def test_low_battery_threshold(level, expected):
    entity = binary_sensor("low_battery")
    entity._apply(BeaconData(battery_pct=level, battery_seen=NOW))
    assert entity.device_class == BinarySensorDeviceClass.BATTERY
    assert entity.available
    assert entity.is_on is expected


def test_custom_low_battery_threshold_and_freshness():
    entity = binary_sensor("low_battery", {CONF_LOW_BATTERY_THRESHOLD: 30})
    entity._apply(BeaconData())
    assert not entity.available
    data = BeaconData(battery_pct=25, battery_seen=NOW)
    entity._apply(data)
    assert entity.available
    assert entity.is_on is True
    assert entity.extra_state_attributes["threshold"] == 30
    entity._apply(replace(data, stale_keys=frozenset({"battery_pct"})))
    assert not entity.available
    assert entity.is_on is None
    entity._apply(
        replace(data, battery_pct=50, battery_seen=NOW + timedelta(minutes=1))
    )
    assert entity.available
    assert entity.is_on is False


def test_battery_value_without_observation_cannot_raise_a_low_battery_alert():
    entity = binary_sensor("low_battery")
    entity._apply(BeaconData(battery_pct=5))
    assert not entity.available
    assert entity.is_on is None
