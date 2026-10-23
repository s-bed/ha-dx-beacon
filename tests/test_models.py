"""Hardware identification for new and legacy CP27/CP35 entries."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from homeassistant.components.bluetooth import BluetoothChange

from custom_components.dx_beacon import async_setup_entry
from custom_components.dx_beacon.const import (
    CONF_ADDRESS,
    CONF_MODEL,
    CONF_TLM_FORMAT,
    DEFAULT_MODEL,
    DOMAIN,
    EDDYSTONE_UUID,
    TLM_DX_CP35,
    TLM_STANDARD,
)
from custom_components.dx_beacon.device_tracker import DxBeaconTracker

ADDRESS = "48:87:2D:9E:5C:3D"
NOW = datetime(2026, 10, 23, tzinfo=UTC)


def observation(name, tlm_format=TLM_STANDARD):
    order = "little" if tlm_format == TLM_DX_CP35 else "big"
    payload = (
        b"\x20\x00"
        + (3100).to_bytes(2, order)
        + b"\x17\x00"
        + (100).to_bytes(4, order)
        + (1000).to_bytes(4, order)
    )
    return SimpleNamespace(
        name=name,
        time=100.0,
        source="receiver",
        rssi=-60,
        manufacturer_data={},
        service_data={EDDYSTONE_UUID: payload},
        raw=bytes([len(payload) + 3, 0x16, 0xAA, 0xFE]) + payload,
    )


@pytest.fixture
def runtime():
    entry = SimpleNamespace(
        entry_id="beacon",
        data={CONF_ADDRESS: ADDRESS},
        options={},
        async_on_unload=Mock(),
    )

    def update_entry(updated, **updates):
        updated.data = updates["data"]
        return True

    hass = SimpleNamespace(
        data={},
        config_entries=SimpleNamespace(
            async_update_entry=Mock(side_effect=update_entry),
            async_schedule_reload=Mock(),
            async_forward_entry_setups=AsyncMock(),
        ),
    )
    with (
        patch(
            "custom_components.dx_beacon.bluetooth.async_register_advertisement_callback",
            None,
            create=True,
        ),
        patch("custom_components.dx_beacon._update_device"),
        patch("custom_components.dx_beacon.monotonic_time_coarse", return_value=100),
        patch("custom_components.dx_beacon.dt_util.utcnow", return_value=NOW),
        patch(
            "custom_components.dx_beacon.async_resolve_source_name",
            return_value="Receiver",
        ),
        patch(
            "custom_components.dx_beacon.bluetooth.async_last_service_info",
            return_value=None,
        ) as latest,
        patch(
            "custom_components.dx_beacon.bluetooth.async_current_scanners",
            return_value=[SimpleNamespace(scanning=True)],
        ),
        patch(
            "custom_components.dx_beacon.bluetooth.async_register_callback"
        ) as subscribe,
        patch("custom_components.dx_beacon.bluetooth.async_track_unavailable"),
        patch("custom_components.dx_beacon.async_track_time_interval"),
        patch("custom_components.dx_beacon.async_dispatcher_send"),
    ):
        yield SimpleNamespace(
            hass=hass, entry=entry, latest=latest, subscribe=subscribe
        )


@pytest.mark.parametrize(
    "name,expected_model,expected_format",
    [
        ("CP27-5C3D", "DX-CP27", TLM_STANDARD),
        ("DX-CP27-5C3D", "DX-CP27", TLM_STANDARD),
        ("CP35-5C3D-E", "DX-CP35", TLM_DX_CP35),
        ("cp35_5c3d", "DX-CP35", TLM_DX_CP35),
    ],
)
@pytest.mark.asyncio
async def test_legacy_entry_detects_actual_model_before_creating_entities(
    runtime, name, expected_model, expected_format
):
    runtime.latest.return_value = observation(name, expected_format)
    await async_setup_entry(runtime.hass, runtime.entry)
    assert runtime.entry.data[CONF_MODEL] == expected_model
    entity = DxBeaconTracker(runtime.entry, ADDRESS)
    assert entity.device_info["model"] == expected_model
    assert entity.unique_id == f"{ADDRESS}_tracker"
    data = runtime.hass.data[DOMAIN][runtime.entry.entry_id]
    assert data.tlm_format == expected_format
    assert data.battery_mv == 3100
    runtime.hass.config_entries.async_schedule_reload.assert_not_called()


@pytest.mark.asyncio
async def test_unknown_name_does_not_guess_model_from_shared_dx_frames(runtime):
    runtime.latest.return_value = observation("Renamed keys")
    await async_setup_entry(runtime.hass, runtime.entry)
    entity = DxBeaconTracker(runtime.entry, ADDRESS)
    assert entity.device_info["model"] == DEFAULT_MODEL == "DX-SMART Beacon"
    assert CONF_MODEL not in runtime.entry.data
    assert runtime.hass.data[DOMAIN][runtime.entry.entry_id].tlm_format == TLM_STANDARD
    runtime.hass.config_entries.async_update_entry.assert_not_called()


@pytest.mark.parametrize("saved_model", ["DX-CP27", "DX-CP35", "Custom model"])
@pytest.mark.asyncio
async def test_explicit_model_and_firmware_options_survive_detection(
    runtime, saved_model
):
    runtime.entry.data[CONF_MODEL] = saved_model
    runtime.entry.options[CONF_TLM_FORMAT] = TLM_STANDARD
    runtime.latest.return_value = observation("CP35-5C3D-E")
    await async_setup_entry(runtime.hass, runtime.entry)
    assert DxBeaconTracker(runtime.entry, ADDRESS).device_info["model"] == saved_model
    assert runtime.hass.data[DOMAIN][runtime.entry.entry_id].tlm_format == TLM_STANDARD
    runtime.hass.config_entries.async_update_entry.assert_not_called()
    runtime.hass.config_entries.async_schedule_reload.assert_not_called()


@pytest.mark.parametrize("initial_model", [None, DEFAULT_MODEL])
@pytest.mark.parametrize("advertised_model", ["CP27-5C3D", "CP35-5C3D-E"])
@pytest.mark.asyncio
async def test_first_recognizable_advertisement_after_startup_schedules_one_reload(
    runtime, initial_model, advertised_model
):
    if initial_model is not None:
        runtime.entry.data[CONF_MODEL] = initial_model
    await async_setup_entry(runtime.hass, runtime.entry)
    callback = runtime.subscribe.call_args.args[1]
    info = observation(advertised_model)
    callback(info, BluetoothChange.ADVERTISEMENT)
    callback(info, BluetoothChange.ADVERTISEMENT)
    expected = "DX-CP27" if "CP27" in advertised_model else "DX-CP35"
    assert runtime.entry.data[CONF_MODEL] == expected
    runtime.hass.config_entries.async_update_entry.assert_called_once()
    runtime.hass.config_entries.async_schedule_reload.assert_called_once_with(
        runtime.entry.entry_id
    )
