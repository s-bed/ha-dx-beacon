"""Discovery, settings and replacement flows against Home Assistant's API."""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from homeassistant import config_entries
from homeassistant.data_entry_flow import AbortFlow, FlowResultType
from homeassistant.helpers.data_entry_flow import FlowManagerResourceView

from custom_components.dx_beacon.config_flow import (
    DxBeaconConfigFlow,
    DxBeaconOptionsFlow,
    infer_model,
    is_dx_beacon,
)
from custom_components.dx_beacon.const import (
    APPLE_ID,
    CONF_ADDRESS,
    CONF_DEPARTURE_TIMEOUT,
    CONF_IDENTITY_ADDRESS,
    CONF_LOW_BATTERY_THRESHOLD,
    CONF_MODEL,
    CONF_TELEMETRY_TIMEOUT,
    CONF_TLM_FORMAT,
    DEFAULT_MODEL,
    DOMAIN,
    DX_BATT_UUID,
    DX_MANUFACTURER_ID,
    EDDYSTONE_UUID,
    TLM_DX_CP35,
    TLM_STANDARD,
)

BEACON = "48:87:2D:9E:5C:3D"
REPLACEMENT = "48:87:2D:9E:5C:3E"


def advertisement(*, address=BEACON, name="CP35_5C3D", service=None, manufacturer=None):
    return SimpleNamespace(
        address=address,
        name=name,
        service_data=service or {},
        manufacturer_data=manufacturer or {},
    )


def configure_flow(flow, *, source=config_entries.SOURCE_USER, entry=None):
    """Use real HA flow helpers with the entry services they require."""
    flow.hass = SimpleNamespace(
        config_entries=SimpleNamespace(
            flow=SimpleNamespace(async_progress_by_handler=Mock(return_value=[])),
            async_entry_for_domain_unique_id=Mock(return_value=None),
            async_entries=Mock(return_value=[]),
            async_get_known_entry=Mock(return_value=entry),
            async_update_entry=Mock(return_value=True),
            async_schedule_reload=Mock(),
        )
    )
    flow.handler = DOMAIN
    flow.context = {"source": source}
    if entry is not None:
        flow.context["entry_id"] = entry.entry_id
    return flow


def entry(*, data=None, options=None, unique_id=BEACON):
    return SimpleNamespace(
        entry_id="beacon-1",
        data=data or {CONF_ADDRESS: BEACON},
        options=options or {},
        unique_id=unique_id,
        source=config_entries.SOURCE_USER,
        state=config_entries.ConfigEntryState.LOADED,
        update_listeners=[],
    )


@pytest.mark.parametrize(
    "payload,expected",
    [
        (bytes.fromhex("6448872d9e5c3d"), True),
        (bytes.fromhex("0048872d9e5c3d"), True),
        (bytes.fromhex("6548872d9e5c3d"), False),
        (bytes.fromhex("6448872d9e5c3e"), False),
        (bytes.fromhex("643d5c9e2d8748"), False),
        (b"\x64", False),
        (bytes.fromhex("6448872d9e5c3d00"), False),
    ],
)
def test_dx_identification_validates_address_bearing_frame(payload, expected):
    assert is_dx_beacon(advertisement(service={DX_BATT_UUID: payload})) is expected


def test_manufacturer_identifies_dx_and_generic_frames_do_not():
    assert is_dx_beacon(advertisement(manufacturer={DX_MANUFACTURER_ID: b"\x02\x15"}))
    assert not is_dx_beacon(advertisement(service={EDDYSTONE_UUID: b"\x20\x00"}))
    assert not is_dx_beacon(advertisement(manufacturer={APPLE_ID: b"\x02\x15"}))
    assert not is_dx_beacon(
        advertisement(address="invalid", service={DX_BATT_UUID: b"\x64" * 7})
    )


@pytest.mark.parametrize(
    "name,expected",
    [
        ("CP27_5C3D", "DX-CP27"),
        ("DX-CP27-5C3D", "DX-CP27"),
        ("CP35_5C3D", "DX-CP35"),
        ("CP35-5C3D-E", "DX-CP35"),
        ("DX-CP27", "DX-CP27"),
        ("cp35 tag", "DX-CP35"),
        (None, DEFAULT_MODEL),
        ("Renamed keys", DEFAULT_MODEL),
        ("MyCP35tag", DEFAULT_MODEL),
    ],
)
def test_advertised_model(name, expected):
    assert infer_model(advertisement(name=name)) == expected


@pytest.mark.asyncio
async def test_manual_address_remains_available_with_discovery():
    flow = configure_flow(DxBeaconConfigFlow())
    with patch(
        "custom_components.dx_beacon.config_flow.async_discovered_service_info",
        return_value=[advertisement(service={EDDYSTONE_UUID: b"\x20\x00"})],
    ):
        result = await flow.async_step_user()
    assert result["type"] is FlowResultType.MENU
    assert result["menu_options"] == ["discovered", "manual"]
    form = await flow.async_step_manual()
    assert form["step_id"] == "manual"
    result = await flow.async_step_manual(
        {CONF_ADDRESS: REPLACEMENT, CONF_MODEL: "Other beacon"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_ADDRESS: REPLACEMENT, CONF_MODEL: "Other beacon"}


@pytest.mark.asyncio
async def test_empty_discovery_offers_manual_form():
    flow = configure_flow(DxBeaconConfigFlow())
    with patch(
        "custom_components.dx_beacon.config_flow.async_discovered_service_info",
        return_value=[],
    ):
        result = await flow.async_step_user()
    assert result["step_id"] == "manual"


@pytest.mark.asyncio
async def test_ibeacon_only_can_be_selected_explicitly():
    flow = configure_flow(DxBeaconConfigFlow())
    with patch(
        "custom_components.dx_beacon.config_flow.async_discovered_service_info",
        return_value=[advertisement(manufacturer={APPLE_ID: b"\x02\x15" + bytes(21)})],
    ):
        await flow.async_step_user()
    form = await flow.async_step_discovered()
    assert form["data_schema"]({CONF_ADDRESS: BEACON}) == {CONF_ADDRESS: BEACON}
    form = await flow.async_step_discovered({CONF_ADDRESS: BEACON})
    assert form["step_id"] == "manual"
    assert form["data_schema"]({}) == {CONF_ADDRESS: BEACON, CONF_MODEL: "DX-CP35"}


@pytest.mark.asyncio
async def test_other_apple_traffic_is_not_offered_as_a_beacon():
    flow = configure_flow(DxBeaconConfigFlow())
    with patch(
        "custom_components.dx_beacon.config_flow.async_discovered_service_info",
        return_value=[advertisement(manufacturer={APPLE_ID: b"\x07" + bytes(30)})],
    ):
        result = await flow.async_step_user()
    assert result["step_id"] == "manual"


@pytest.mark.asyncio
async def test_direct_user_submission_compatibility_and_normalization():
    flow = configure_flow(DxBeaconConfigFlow())
    result = await flow.async_step_user({CONF_ADDRESS: "48872d9e5c3d"})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_ADDRESS: BEACON, CONF_MODEL: DEFAULT_MODEL}
    assert flow.unique_id == BEACON


@pytest.mark.asyncio
async def test_invalid_manual_address_and_duplicates():
    flow = configure_flow(DxBeaconConfigFlow())
    result = await flow.async_step_user({CONF_ADDRESS: "not-a-mac"})
    assert result["errors"] == {CONF_ADDRESS: "invalid_address"}
    flow.hass.config_entries.async_entry_for_domain_unique_id.return_value = entry()
    with pytest.raises(AbortFlow, match="already_configured"):
        await flow.async_step_user({CONF_ADDRESS: BEACON})


@pytest.mark.asyncio
async def test_automatic_discovery_is_strict_and_confirmation_allows_model():
    flow = configure_flow(DxBeaconConfigFlow(), source=config_entries.SOURCE_BLUETOOTH)
    result = await flow.async_step_bluetooth(
        advertisement(service={EDDYSTONE_UUID: b"\x20\x00"})
    )
    assert result["reason"] == "not_supported"
    result = await flow.async_step_bluetooth(
        advertisement(service={DX_BATT_UUID: bytes.fromhex("6448872d9e5c3d")})
    )
    assert result["step_id"] == "confirm"
    result = await flow.async_step_confirm({CONF_MODEL: "DX-CP35"})
    assert result["data"] == {CONF_ADDRESS: BEACON, CONF_MODEL: "DX-CP35"}


@pytest.mark.asyncio
async def test_replacement_preserves_original_identity_and_updates_unique_id():
    current = entry()
    flow = configure_flow(
        DxBeaconConfigFlow(), source=config_entries.SOURCE_RECONFIGURE, entry=current
    )
    result = await flow.async_step_reconfigure(
        {CONF_ADDRESS: REPLACEMENT, CONF_MODEL: "DX-CP35"}
    )
    assert result["reason"] == "reconfigure_successful"
    update = flow.hass.config_entries.async_update_entry.call_args.kwargs
    assert update["unique_id"] == REPLACEMENT
    assert update["data"] == {
        CONF_ADDRESS: REPLACEMENT,
        CONF_MODEL: "DX-CP35",
        CONF_IDENTITY_ADDRESS: BEACON,
    }
    flow.hass.config_entries.async_schedule_reload.assert_called_once_with(
        current.entry_id
    )
    current.data = update["data"]
    current.unique_id = REPLACEMENT
    await flow.async_step_reconfigure(
        {CONF_ADDRESS: "48:87:2D:9E:5C:3F", CONF_MODEL: "DX-CP27"}
    )
    assert (
        flow.hass.config_entries.async_update_entry.call_args.kwargs["data"][
            CONF_IDENTITY_ADDRESS
        ]
        == BEACON
    )


@pytest.mark.asyncio
async def test_reconfigure_rejects_duplicate_and_allows_same_address():
    current = entry()
    flow = configure_flow(
        DxBeaconConfigFlow(), source=config_entries.SOURCE_RECONFIGURE, entry=current
    )
    flow.hass.config_entries.async_entry_for_domain_unique_id.return_value = entry(
        unique_id=REPLACEMENT
    )
    with pytest.raises(AbortFlow, match="already_configured"):
        await flow.async_step_reconfigure({CONF_ADDRESS: REPLACEMENT})
    flow.hass.config_entries.async_entry_for_domain_unique_id.return_value = current
    result = await flow.async_step_reconfigure(
        {CONF_ADDRESS: BEACON, CONF_MODEL: "DX-CP27"}
    )
    assert result["reason"] == "reconfigure_successful"
    assert (
        flow.hass.config_entries.async_update_entry.call_args.kwargs["data"][CONF_MODEL]
        == "DX-CP27"
    )


@pytest.mark.parametrize("source", ["manual", "bluetooth"])
@pytest.mark.asyncio
async def test_replaced_hardware_address_cannot_create_colliding_entities(source):
    """A replacement keeps the old address reserved for its entity identities."""
    current = entry()
    replacement_flow = configure_flow(
        DxBeaconConfigFlow(), source=config_entries.SOURCE_RECONFIGURE, entry=current
    )
    await replacement_flow.async_step_reconfigure({CONF_ADDRESS: REPLACEMENT})
    current.data = (
        replacement_flow.hass.config_entries.async_update_entry.call_args.kwargs["data"]
    )
    current.unique_id = REPLACEMENT

    flow = configure_flow(DxBeaconConfigFlow())
    flow.hass.config_entries.async_entries.return_value = [current]
    with pytest.raises(AbortFlow, match="already_configured"):
        if source == "manual":
            await flow.async_step_user({CONF_ADDRESS: BEACON})
        else:
            await flow.async_step_bluetooth(
                advertisement(manufacturer={DX_MANUFACTURER_ID: b"\x02\x15"})
            )


@pytest.mark.asyncio
async def test_reconfigure_respects_other_identity_and_can_restore_own_hardware():
    replaced = entry(
        data={CONF_ADDRESS: REPLACEMENT, CONF_IDENTITY_ADDRESS: BEACON},
        unique_id=REPLACEMENT,
    )
    other = entry(
        data={CONF_ADDRESS: "48:87:2D:9E:5C:3F"}, unique_id="48:87:2D:9E:5C:3F"
    )
    other.entry_id = "beacon-2"
    flow = configure_flow(
        DxBeaconConfigFlow(), source=config_entries.SOURCE_RECONFIGURE, entry=other
    )
    flow.hass.config_entries.async_entries.return_value = [replaced, other]
    with pytest.raises(AbortFlow, match="already_configured"):
        await flow.async_step_reconfigure({CONF_ADDRESS: BEACON})

    flow = configure_flow(
        DxBeaconConfigFlow(), source=config_entries.SOURCE_RECONFIGURE, entry=replaced
    )
    flow.hass.config_entries.async_entries.return_value = [replaced, other]
    result = await flow.async_step_reconfigure({CONF_ADDRESS: BEACON})
    assert result["reason"] == "reconfigure_successful"
    assert (
        flow.hass.config_entries.async_update_entry.call_args.kwargs["data"][
            CONF_IDENTITY_ADDRESS
        ]
        == BEACON
    )


@pytest.mark.parametrize(
    "model,expected_format",
    [(None, TLM_STANDARD), ("DX-CP35", TLM_DX_CP35), ("DX-CP27", TLM_STANDARD)],
)
@pytest.mark.asyncio
async def test_options_model_defaults_and_reload(model, expected_format):
    current = entry(
        data={CONF_ADDRESS: BEACON, **({CONF_MODEL: model} if model else {})}
    )
    flow = configure_flow(DxBeaconOptionsFlow(), entry=current)
    flow.handler = current.entry_id
    assert flow.automatic_reload
    result = await flow.async_step_init()
    defaults = result["data_schema"]({})
    assert defaults == {
        CONF_DEPARTURE_TIMEOUT: 180,
        CONF_TELEMETRY_TIMEOUT: 3600,
        CONF_LOW_BATTERY_THRESHOLD: 20,
        CONF_TLM_FORMAT: expected_format,
    }
    result = await flow.async_step_init(defaults)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == defaults


@pytest.mark.parametrize(
    "key,value",
    [
        (CONF_DEPARTURE_TIMEOUT, 9),
        (CONF_DEPARTURE_TIMEOUT, 3601),
        (CONF_TELEMETRY_TIMEOUT, 59),
        (CONF_TELEMETRY_TIMEOUT, 86401),
        (CONF_LOW_BATTERY_THRESHOLD, 0),
        (CONF_LOW_BATTERY_THRESHOLD, 101),
        (CONF_TLM_FORMAT, "auto"),
    ],
)
@pytest.mark.asyncio
async def test_options_reject_values_outside_supported_ranges(key, value):
    current = entry()
    flow = configure_flow(DxBeaconOptionsFlow(), entry=current)
    flow.handler = current.entry_id
    result = await flow.async_step_init({key: value})
    assert result["errors"] == {"base": "invalid_options"}


@pytest.mark.asyncio
async def test_options_preserve_existing_values():
    current = entry(options={CONF_TLM_FORMAT: TLM_STANDARD, "future_option": True})
    flow = configure_flow(DxBeaconOptionsFlow(), entry=current)
    flow.handler = current.entry_id
    result = await flow.async_step_init()
    assert result["data_schema"]({})[CONF_TLM_FORMAT] == TLM_STANDARD
    result = await flow.async_step_init({CONF_LOW_BATTERY_THRESHOLD: 15})
    assert result["data"]["future_option"] is True
    assert result["data"][CONF_TLM_FORMAT] == TLM_STANDARD
    assert result["data"][CONF_LOW_BATTERY_THRESHOLD] == 15


def test_translation_keys_match():
    root = Path(__file__).parents[1] / "custom_components" / DOMAIN
    source = json.loads((root / "strings.json").read_text())

    def keys(value):
        return {
            key: keys(child) if isinstance(child, dict) else None
            for key, child in value.items()
        }

    for path in (root / "translations").glob("*.json"):
        assert keys(json.loads(path.read_text())) == keys(source)


@pytest.mark.parametrize(
    "step", ["manual", "confirm", "reconfigure", "discovered", "options"]
)
@pytest.mark.asyncio
async def test_forms_serialize_through_home_assistant_http_view(step):
    """A valid Python schema must also render as JSON for the setup dialog."""
    current = entry()
    if step == "options":
        flow = configure_flow(DxBeaconOptionsFlow(), entry=current)
        flow.handler = current.entry_id
        result = await flow.async_step_init()
    else:
        flow = configure_flow(
            DxBeaconConfigFlow(),
            source=config_entries.SOURCE_RECONFIGURE,
            entry=current,
        )
        flow._address = BEACON
        flow._discovered = {BEACON: "CP35 beacon"}
        result = await getattr(flow, f"async_step_{step}")()
    rendered = FlowManagerResourceView(Mock())._prepare_result_json(result)
    json.dumps(rendered)
    assert isinstance(rendered["data_schema"], list)
    assert rendered["data_schema"]


@pytest.mark.parametrize("step", ["manual", "confirm", "reconfigure"])
@pytest.mark.parametrize("model", ["", "   "])
@pytest.mark.asyncio
async def test_empty_model_returns_field_error_instead_of_server_error(step, model):
    current = entry()
    flow = configure_flow(
        DxBeaconConfigFlow(), source=config_entries.SOURCE_RECONFIGURE, entry=current
    )
    flow._address = BEACON
    result = await getattr(flow, f"async_step_{step}")(
        {CONF_ADDRESS: BEACON, CONF_MODEL: model}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_MODEL: "invalid_model"}
    assert flow.unique_id is None
    flow.hass.config_entries.async_update_entry.assert_not_called()
    json.dumps(FlowManagerResourceView(Mock())._prepare_result_json(result))


@pytest.mark.parametrize("step", ["manual", "confirm", "reconfigure"])
@pytest.mark.asyncio
async def test_model_normalization_runs_after_form_submission(step):
    current = entry()
    flow = configure_flow(
        DxBeaconConfigFlow(),
        source=(
            config_entries.SOURCE_RECONFIGURE
            if step == "reconfigure"
            else config_entries.SOURCE_USER
        ),
        entry=current,
    )
    flow._address = BEACON
    result = await getattr(flow, f"async_step_{step}")(
        {CONF_ADDRESS: BEACON, CONF_MODEL: " cp27 "}
    )
    if step == "reconfigure":
        data = flow.hass.config_entries.async_update_entry.call_args.kwargs["data"]
    else:
        data = result["data"]
    assert data[CONF_MODEL] == "DX-CP27"
