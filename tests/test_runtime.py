"""Runtime lifecycle, registry metadata and publication behavior."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, create_autospec, patch

import pytest
from homeassistant.components.bluetooth import BluetoothChange
from homeassistant.helpers import device_registry as dr

from custom_components.dx_beacon import (
    BeaconData,
    BeaconProcessor,
    _update_device,
    async_setup_entry,
    async_unload_entry,
)
from custom_components.dx_beacon.const import (
    APPLE_ID,
    CONF_ADDRESS,
    CONF_IDENTITY_ADDRESS,
    CONF_MODEL,
    DOMAIN,
    DX_BATT_UUID,
    EDDYSTONE_UUID,
    TLM_DX_CP35,
)

ORIGINAL = "AA:BB:CC:DD:EE:FF"
CURRENT = "AA:BB:CC:DD:EE:00"


@pytest.mark.parametrize("success", [False, True])
@pytest.mark.asyncio
async def test_failed_unload_retains_live_runtime(success):
    state = BeaconData(battery_pct=50)
    hass = SimpleNamespace(
        data={DOMAIN: {"beacon": state}},
        config_entries=SimpleNamespace(
            async_unload_platforms=AsyncMock(return_value=success)
        ),
    )
    assert await async_unload_entry(hass, SimpleNamespace(entry_id="beacon")) is success
    assert ("beacon" in hass.data[DOMAIN]) is not success
    if not success:
        assert hass.data[DOMAIN]["beacon"] is state


@pytest.mark.parametrize("current_api", [False, True])
@pytest.mark.parametrize("owned_by_entry", [False, True])
def test_replacement_corrects_model_without_removing_other_integrations_connections(
    current_api,
    owned_by_entry,
):
    registry = create_autospec(dr.DeviceRegistry, instance=True)
    device = SimpleNamespace(
        id="device-id",
        connections={(dr.CONNECTION_BLUETOOTH, ORIGINAL), ("other", "preserved")},
    )
    if current_api:
        device.config_entry_id = "beacon" if owned_by_entry else "bermuda"
        # Supply the new API even when running against Home Assistant 2026.3.
        registry.async_get_device_by_identifier = Mock(return_value=device)
    else:
        device.config_entries = {"beacon"} if owned_by_entry else {"beacon", "bermuda"}
        if hasattr(registry, "async_get_device_by_identifier"):
            del registry.async_get_device_by_identifier
        registry.async_get_device.return_value = device
    entry = SimpleNamespace(
        entry_id="beacon",
        data={
            CONF_ADDRESS: CURRENT,
            CONF_IDENTITY_ADDRESS: ORIGINAL,
            CONF_MODEL: "DX-CP35",
        },
    )
    with patch("custom_components.dx_beacon.dr.async_get", return_value=registry):
        _update_device(SimpleNamespace(), entry, CURRENT)
    if current_api:
        registry.async_get_device_by_identifier.assert_called_once_with(
            (DOMAIN, ORIGINAL), "beacon"
        )
        registry.async_get_device.assert_not_called()
    else:
        registry.async_get_device.assert_called_once_with(
            identifiers={(DOMAIN, ORIGINAL)}
        )
    updates = registry.async_update_device.call_args.kwargs
    assert updates["model"] == "DX-CP35"
    if not owned_by_entry:
        assert "new_connections" not in updates
    else:
        assert updates["new_connections"] == {
            (dr.CONNECTION_BLUETOOTH, CURRENT),
            ("other", "preserved"),
        }


def test_known_model_format_is_case_insensitive():
    assert BeaconProcessor(CURRENT, {}, model="dx-cp35").data.tlm_format == TLM_DX_CP35


def test_stale_startup_cache_does_not_bypass_presence_grace():
    now = datetime(2026, 10, 23, tzinfo=UTC)
    with patch("custom_components.dx_beacon.monotonic_time_coarse", return_value=1000):
        processor = BeaconProcessor(CURRENT, {})
    processor.observe(
        SimpleNamespace(
            time=100.0,
            source="receiver",
            raw=None,
            service_data={},
            manufacturer_data={},
        ),
        now,
        1000,
    )
    processor.expire(now, 1000, monitoring=True)
    assert processor.data.present is None
    processor.expire(now + timedelta(seconds=180), 1180, monitoring=True)
    assert processor.data.present is False


def test_telemetry_expiry_uses_monotonic_time_despite_wall_clock_change():
    now = datetime(2026, 10, 23, tzinfo=UTC)
    with patch("custom_components.dx_beacon.monotonic_time_coarse", return_value=100):
        processor = BeaconProcessor(CURRENT, {})
    processor.observe(
        SimpleNamespace(
            time=100.0,
            source="receiver",
            raw=bytes.fromhex("0416abfe32"),
            service_data={"0000feab-0000-1000-8000-00805f9b34fb": b"\x32"},
            manufacturer_data={},
        ),
        now,
        100,
    )
    processor.expire(now, 100, monitoring=True)
    assert "battery_pct" not in processor.data.stale_keys
    processor.expire(now - timedelta(hours=1), 3700, monitoring=True)
    assert "battery_pct" in processor.data.stale_keys


def test_supplied_merged_capture_retains_values_without_inventing_frame_times():
    now = datetime(2026, 10, 23, tzinfo=UTC)
    address = "48:87:2D:9E:5C:3D"
    with patch("custom_components.dx_beacon.monotonic_time_coarse", return_value=100):
        processor = BeaconProcessor(address, {}, model="DX-CP35")
    processor.observe(
        SimpleNamespace(
            time=100,
            source="receiver",
            service_data={
                DX_BATT_UUID: bytes.fromhex("6448872d9e5c3d"),
                EDDYSTONE_UUID: bytes.fromhex("2000930c17000412000018120000"),
            },
            manufacturer_data={
                APPLE_ID: bytes.fromhex(
                    "0215e2c56db5dffb48d2b060d0f5a71096e000050006c7"
                )
            },
            raw=bytes.fromhex(
                "0201060303aafe1116aafe2000930c17000412000018120000000000000000"
            ),
        ),
        now,
        100,
    )
    processor.expire(now, 100, monitoring=True)
    assert processor.data.battery_pct == 100
    assert processor.data.battery_mv == 3219
    assert processor.data.major == 5
    assert processor.data.minor == 6
    assert processor.data.tlm_seen == now
    assert processor.data.battery_seen is None
    assert processor.data.ibeacon_seen is None
    assert processor.data.stale_keys == {"battery_pct"}
    assert processor.data.frames_seen == {"eddystone_tlm"}


def test_other_eddystone_frames_do_not_count_as_malformed_telemetry():
    with patch("custom_components.dx_beacon.monotonic_time_coarse", return_value=100):
        processor = BeaconProcessor(CURRENT, {})
    processor.observe(
        SimpleNamespace(
            time=100,
            source="receiver",
            raw=None,
            service_data={EDDYSTONE_UUID: b"\x00" + bytes(19)},
            manufacturer_data={},
        ),
        datetime(2026, 10, 23, tzinfo=UTC),
        100,
    )
    assert processor.data.parser_errors == {}
    assert processor.data.tlm_seen is None


@pytest.mark.asyncio
async def test_packet_subscription_is_optional_and_publications_are_throttled():
    clock = SimpleNamespace(monotonic=100.0, now=datetime(2026, 10, 23, tzinfo=UTC))
    hass = SimpleNamespace(
        data={},
        config_entries=SimpleNamespace(async_forward_entry_setups=AsyncMock()),
    )
    entry = SimpleNamespace(
        entry_id="beacon",
        data={CONF_ADDRESS: CURRENT},
        options={},
        async_on_unload=Mock(),
    )
    info = SimpleNamespace(
        time=100.0,
        source="receiver",
        raw=None,
        service_data={},
        manufacturer_data={},
        rssi=-60,
    )
    with (
        patch("custom_components.dx_beacon._update_device"),
        patch(
            "custom_components.dx_beacon.monotonic_time_coarse",
            side_effect=lambda: clock.monotonic,
        ),
        patch(
            "custom_components.dx_beacon.dt_util.utcnow", side_effect=lambda: clock.now
        ),
        patch(
            "custom_components.dx_beacon.async_resolve_source_name",
            return_value="Receiver",
        ),
        patch(
            "custom_components.dx_beacon.bluetooth.async_register_callback"
        ) as subscribe,
        patch("custom_components.dx_beacon.bluetooth.async_track_unavailable"),
        patch(
            "custom_components.dx_beacon.bluetooth.async_register_advertisement_callback",
            create=True,
        ) as packets,
        patch(
            "custom_components.dx_beacon.bluetooth.async_current_scanners",
            return_value=[SimpleNamespace(scanning=True)],
        ) as scanners,
        patch(
            "custom_components.dx_beacon.bluetooth.async_last_service_info",
            return_value=None,
        ) as latest,
        patch("custom_components.dx_beacon.async_track_time_interval") as interval,
        patch("custom_components.dx_beacon.async_dispatcher_send") as dispatch,
    ):
        await async_setup_entry(hass, entry)
        assert entry.async_on_unload.call_count == 4
        subscribe.call_args.args[1](info, BluetoothChange.ADVERTISEMENT)
        assert hass.data[DOMAIN][entry.entry_id].present
        dispatch.reset_mock()
        packet_callback = packets.call_args.args[1]
        for elapsed in range(1, 10):
            clock.monotonic = info.time = 100 + elapsed
            clock.now += timedelta(seconds=1)
            packet_callback(info)
        dispatch.assert_not_called()
        clock.monotonic = info.time = 110
        clock.now += timedelta(seconds=1)
        latest.return_value = info
        interval.call_args.args[1](clock.now)
        assert dispatch.call_count == 1
        assert hass.data[DOMAIN][entry.entry_id].last_seen == clock.now

        # Registered scanners that stopped scanning do not prove the beacon left.
        scanners.return_value = [SimpleNamespace(scanning=False)]
        clock.monotonic += 181
        clock.now += timedelta(seconds=181)
        interval.call_args.args[1](clock.now)
        assert hass.data[DOMAIN][entry.entry_id].present is None
        assert not hass.data[DOMAIN][entry.entry_id].receiving
