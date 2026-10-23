"""Receiver naming and handoffs against the Home Assistant API."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData
from homeassistant.components.bluetooth import (
    BluetoothChange,
    BluetoothServiceInfoBleak,
)
from homeassistant.helpers import device_registry as dr

from custom_components.dx_beacon import async_setup_entry, estimate_distance
from custom_components.dx_beacon.const import (
    APPLE_ID,
    CONF_ADDRESS,
    DOMAIN,
    DX_BATT_UUID,
)
from custom_components.dx_beacon.device_tracker import DxBeaconTracker
from custom_components.dx_beacon.sensor import DESCRIPTIONS, DxBeaconSensor
from custom_components.dx_beacon.source import async_resolve_source_name

BEACON = "AA:BB:CC:DD:EE:FF"
SOURCE = "11:22:33:44:55:66"
OTHER_SOURCE = "11:22:33:44:55:77"


@pytest.fixture
def hass():
    """Mock only the HA services the integration calls."""
    return SimpleNamespace(
        data={},
        config_entries=SimpleNamespace(
            async_entries=Mock(return_value=[]),
            async_forward_entry_setups=AsyncMock(),
        ),
    )


@pytest.fixture
def registry():
    with patch(
        "custom_components.dx_beacon.source.dr.async_get",
        return_value=Mock(spec=dr.DeviceRegistry),
    ) as get_registry:
        registry = get_registry.return_value
        registry.async_get.return_value = None
        registry.async_get_device.return_value = None
        yield registry


@pytest.mark.parametrize(
    "user_name,expected",
    [("Living room AP", "Living room AP"), (None, "Detected proxy")],
)
def test_linked_proxy_name(hass, registry, user_name, expected):
    """Use HA's proxy device link even when its network MAC differs."""
    hass.config_entries.async_entries.return_value = [
        SimpleNamespace(
            unique_id=SOURCE, data={"source_device_id": "proxy-device"}, title="Adapter"
        )
    ]
    registry.async_get.return_value = SimpleNamespace(
        name_by_user=user_name, name="Detected proxy"
    )
    assert async_resolve_source_name(hass, SOURCE.lower()) == expected
    registry.async_get.assert_called_once_with("proxy-device")
    registry.async_get_device.assert_not_called()


def test_local_adapter_or_deleted_proxy_link(hass, registry):
    hass.config_entries.async_entries.return_value = [
        SimpleNamespace(
            unique_id=SOURCE,
            data={"source_device_id": "deleted-device"},
            title="Adapter",
        )
    ]
    registry.async_get_device.return_value = SimpleNamespace(
        name_by_user="USB Bluetooth", name=None
    )
    assert async_resolve_source_name(hass, SOURCE.lower()) == "USB Bluetooth"
    registry.async_get_device.assert_called_once_with(
        connections={(dr.CONNECTION_BLUETOOTH, SOURCE.lower())}
    )


@pytest.mark.parametrize("title", ["Kitchen proxy", "", None])
def test_title_and_mac_fallback(hass, registry, title):
    hass.config_entries.async_entries.return_value = [
        SimpleNamespace(unique_id=None, data={}, title="Unrelated"),
        SimpleNamespace(unique_id=SOURCE, data={}, title=title),
    ]
    assert async_resolve_source_name(hass, SOURCE) == (title or SOURCE)
    hass.config_entries.async_entries.return_value = []
    assert async_resolve_source_name(hass, SOURCE) == SOURCE


def advertisement(source=SOURCE, rssi=-59):
    """A non-connectable iBeacon with DX battery data."""
    return BluetoothServiceInfoBleak.from_device_and_advertisement_data(
        BLEDevice(BEACON, "Beacon", {}),
        AdvertisementData(
            local_name="Beacon",
            manufacturer_data={
                APPLE_ID: b"\x02\x15" + bytes(range(16)) + b"\x00\x01\x00\x02\xc5"
            },
            service_data={DX_BATT_UUID: b"\x55"},
            service_uuids=[],
            tx_power=None,
            rssi=rssi,
            platform_data=(),
        ),
        source,
        100.0,
        False,
    )


@pytest.fixture
async def receiver(hass, registry):
    registry.async_get_device.return_value = SimpleNamespace(
        name_by_user="Living room AP", name=None
    )
    entry = SimpleNamespace(
        entry_id="beacon-1", data={CONF_ADDRESS: BEACON}, async_on_unload=Mock()
    )
    with (
        patch(
            "custom_components.dx_beacon.bluetooth.async_register_callback"
        ) as register,
        patch(
            "custom_components.dx_beacon.bluetooth.async_track_unavailable"
        ) as unavailable,
        patch("custom_components.dx_beacon.async_track_time_interval") as interval,
        patch(
            "custom_components.dx_beacon.bluetooth.async_last_service_info"
        ) as latest,
        patch("custom_components.dx_beacon.async_dispatcher_send") as dispatch,
    ):
        assert await async_setup_entry(hass, entry)
        on_adv = register.call_args.args[1]
        on_adv(advertisement(), BluetoothChange.ADVERTISEMENT)
        yield SimpleNamespace(
            hass=hass,
            entry=entry,
            on_adv=on_adv,
            on_unavailable=unavailable.call_args.args[1],
            refresh=interval.call_args.args[1],
            latest=latest,
            register=register,
            interval=interval,
            dispatch=dispatch,
            registry=registry,
        )


@pytest.mark.asyncio
async def test_sensor_and_tracker_expose_name_and_mac(receiver):
    data = receiver.hass.data[DOMAIN][receiver.entry.entry_id]
    assert data.source == SOURCE
    assert data.source_name == "Living room AP"
    assert data.battery_pct == 85
    assert data.major == 1
    assert data.minor == 2
    assert data.distance == 1.01
    assert receiver.register.call_args.args[2] == {
        "address": BEACON,
        "connectable": False,
    }
    assert receiver.interval.call_args.args[2] == timedelta(seconds=10)
    # All three registrations are removed when the entry unloads.
    assert receiver.entry.async_on_unload.call_count == 3
    for desc in DESCRIPTIONS:
        sensor = DxBeaconSensor(receiver.entry, BEACON, desc)
        sensor._apply(data)
        assert sensor.extra_state_attributes["source_name"] == "Living room AP"
        assert sensor.extra_state_attributes["source"] == SOURCE
        if desc.key == "source_name":
            assert sensor.native_value == "Living room AP"
            assert sensor.available
            assert sensor.entity_registry_enabled_default
    tracker = DxBeaconTracker(receiver.entry, BEACON)
    tracker._apply(data)
    assert tracker.extra_state_attributes["source_name"] == "Living room AP"
    assert tracker.extra_state_attributes["source"] == SOURCE


@pytest.mark.asyncio
async def test_handoff_with_unchanged_payload(receiver):
    """A cached source change updates reception without an advertisement callback."""
    receiver.latest.return_value = advertisement(OTHER_SOURCE, -45)
    receiver.registry.async_get_device.return_value = SimpleNamespace(
        name_by_user="Kitchen AP", name=None
    )
    receiver.refresh(datetime.now(UTC))
    data = receiver.hass.data[DOMAIN][receiver.entry.entry_id]
    assert data.source == OTHER_SOURCE
    assert data.source_name == "Kitchen AP"
    assert data.rssi == -45
    assert data.distance == estimate_distance(-59, -45)
    assert data.battery_pct == 85
    assert data.present
    receiver.latest.assert_called_once_with(receiver.hass, BEACON, connectable=False)


@pytest.mark.asyncio
async def test_rename_and_unchanged_refresh(receiver):
    receiver.latest.return_value = advertisement()
    receiver.dispatch.reset_mock()
    receiver.refresh(datetime.now(UTC))
    receiver.dispatch.assert_not_called()
    receiver.registry.async_get_device.return_value.name_by_user = "Renamed AP"
    receiver.refresh(datetime.now(UTC))
    assert (
        receiver.hass.data[DOMAIN][receiver.entry.entry_id].source_name == "Renamed AP"
    )
    receiver.dispatch.assert_called_once()


@pytest.mark.asyncio
async def test_unavailable_and_return(receiver):
    receiver.on_unavailable(advertisement())
    data = receiver.hass.data[DOMAIN][receiver.entry.entry_id]
    assert not data.present
    assert data.source_name is None
    assert data.source == SOURCE  # Preserve the historical MAC attribute.
    assert data.rssi is None
    assert data.distance is None
    sensor = DxBeaconSensor(receiver.entry, BEACON, DESCRIPTIONS[0])
    sensor._apply(data)
    assert not sensor.available
    receiver.latest.return_value = advertisement()
    receiver.dispatch.reset_mock()
    receiver.refresh(datetime.now(UTC))
    receiver.dispatch.assert_not_called()  # Cached data must not revive an absent beacon.
    receiver.on_adv(advertisement(), BluetoothChange.ADVERTISEMENT)
    sensor._apply(receiver.hass.data[DOMAIN][receiver.entry.entry_id])
    assert sensor.available
    assert sensor.native_value == "Living room AP"
