"""Native diagnostics preserve useful telemetry without leaking identities."""

import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from homeassistant.components.diagnostics import REDACTED

from custom_components.dx_beacon import BeaconData
from custom_components.dx_beacon.const import (
    CONF_ADDRESS,
    CONF_DEPARTURE_TIMEOUT,
    CONF_IDENTITY_ADDRESS,
    CONF_MODEL,
    CONF_TLM_FORMAT,
    DOMAIN,
    TLM_DX_CP35,
    TLM_STANDARD,
)
from custom_components.dx_beacon.diagnostics import async_get_config_entry_diagnostics


def config_entry(**data):
    return SimpleNamespace(
        entry_id="beacon-entry",
        data={
            CONF_ADDRESS: "48:87:2D:9E:5C:3D",
            CONF_IDENTITY_ADDRESS: "48:87:2D:9E:00:00",
            CONF_MODEL: "DX-CP35",
            **data,
        },
        options={CONF_DEPARTURE_TIMEOUT: 240},
        title="CP35 private name",
        unique_id="Private unique ID",
    )


@pytest.mark.asyncio
async def test_loaded_diagnostics_are_serializable_redacted_and_useful():
    now = datetime(2026, 10, 23, 12, 34, 56, tzinfo=UTC)
    entry = config_entry(private_future_setting="secret value")
    entry.options["unrecognized_setting"] = "another secret value"
    data = BeaconData(
        battery_mv=3219,
        battery_pct=100,
        temperature=23.0,
        adv_count=1184256,
        uptime_s=463.2,
        uuid="e2c56db5-dffb-48d2-b060-d0f5a71096e0",
        source="A8:BD:27:C5:31:30",
        source_name="Private bedroom receiver",
        present=True,
        receiving=True,
        last_seen=now,
        battery_seen=now,
        tlm_seen=now,
        ibeacon_seen=now,
        last_reboot=now,
        stale_keys=frozenset({"temperature", "battery_mv"}),
        frames_seen=frozenset({"dx_battery", "eddystone_tlm", "ibeacon"}),
        parser_errors={"eddystone_tlm": 2},
        tlm_format=TLM_DX_CP35,
    )
    hass = SimpleNamespace(data={DOMAIN: {entry.entry_id: data}})
    report = await async_get_config_entry_diagnostics(hass, entry)
    serialized = json.dumps(report)

    for private in (
        entry.data[CONF_ADDRESS],
        entry.data[CONF_IDENTITY_ADDRESS],
        data.uuid,
        data.source,
        data.source_name,
        entry.title,
        entry.unique_id,
        "secret value",
        "another secret value",
    ):
        assert private not in serialized
    assert report["device"]["model"] == "DX-CP35"
    assert report["loaded"]
    assert report["configuration"]["data"][CONF_ADDRESS] == REDACTED
    assert report["configuration"]["effective_settings"][CONF_DEPARTURE_TIMEOUT] == 240
    assert report["runtime"]["source_name"] == REDACTED
    assert report["runtime"]["battery_mv"] == 3219
    assert report["runtime"]["last_reboot"] == now.isoformat()
    assert report["runtime"]["stale_keys"] == ["battery_mv", "temperature"]
    assert report["advertisements"]["last_observations"]["tlm_seen"] == now.isoformat()
    assert report["advertisements"]["seen_frames"] == [
        "dx_battery",
        "eddystone_tlm",
        "ibeacon",
    ]
    assert report["parsing"]["errors"] == {"eddystone_tlm": 2}
    assert report["parsing"]["byte_order"] == {
        "battery_mv": "little",
        "temperature": "big",
        "adv_count": "little",
        "uptime_s": "little",
    }
    # Downloading diagnostics must not modify the live integration state.
    assert data.source_name == "Private bedroom receiver"
    assert data.stale_keys == frozenset({"temperature", "battery_mv"})


@pytest.mark.asyncio
@pytest.mark.parametrize("runtime_data", [{}, {DOMAIN: {}}])
async def test_diagnostics_when_entry_is_not_loaded(runtime_data):
    entry = config_entry()
    report = await async_get_config_entry_diagnostics(
        SimpleNamespace(data=runtime_data), entry
    )
    json.dumps(report)
    assert not report["loaded"]
    assert report["runtime"] is None
    assert report["advertisements"]["seen_frames"] == []
    assert all(
        value is None
        for value in report["advertisements"]["last_observations"].values()
    )
    assert report["parsing"]["errors"] == {}
    assert report["parsing"]["tlm_format"] == TLM_DX_CP35


@pytest.mark.asyncio
async def test_standard_tlm_decisions_follow_selected_format():
    entry = config_entry(**{CONF_TLM_FORMAT: TLM_STANDARD})
    report = await async_get_config_entry_diagnostics(SimpleNamespace(data={}), entry)
    assert report["parsing"]["tlm_format"] == TLM_STANDARD
    assert set(report["parsing"]["byte_order"].values()) == {"big"}


@pytest.mark.asyncio
async def test_unloaded_other_model_defaults_to_standard_tlm():
    entry = config_entry(**{CONF_MODEL: "Other DX beacon"})
    report = await async_get_config_entry_diagnostics(SimpleNamespace(data={}), entry)
    assert (
        report["configuration"]["effective_settings"][CONF_TLM_FORMAT] == TLM_STANDARD
    )
    assert report["parsing"]["tlm_format"] == TLM_STANDARD


@pytest.mark.asyncio
async def test_case_insensitive_cp35_model_defaults_to_cp35_tlm():
    entry = config_entry(**{CONF_MODEL: "dx-cp35"})
    report = await async_get_config_entry_diagnostics(SimpleNamespace(data={}), entry)
    assert report["configuration"]["effective_settings"][CONF_TLM_FORMAT] == TLM_DX_CP35
    assert report["parsing"]["tlm_format"] == TLM_DX_CP35
