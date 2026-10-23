"""Configure DX-SMART beacons and their local telemetry settings."""

from __future__ import annotations

import re
from typing import Any

import voluptuous as vol
from homeassistant import config_entries, data_entry_flow
from homeassistant.components.bluetooth import (
    BluetoothServiceInfoBleak,
    async_discovered_service_info,
)
from homeassistant.core import callback
from homeassistant.helpers import selector
from homeassistant.helpers.device_registry import format_mac

from .const import (
    APPLE_ID,
    CONF_ADDRESS,
    CONF_DEPARTURE_TIMEOUT,
    CONF_IDENTITY_ADDRESS,
    CONF_LOW_BATTERY_THRESHOLD,
    CONF_MODEL,
    CONF_TELEMETRY_TIMEOUT,
    CONF_TLM_FORMAT,
    DEFAULT_DEPARTURE_TIMEOUT,
    DEFAULT_LOW_BATTERY_THRESHOLD,
    DEFAULT_MODEL,
    DEFAULT_TELEMETRY_TIMEOUT,
    DOMAIN,
    DX_BATT_UUID,
    DX_MANUFACTURER_ID,
    EDDYSTONE_UUID,
    TLM_DX_CP35,
    TLM_STANDARD,
)
from .model import default_tlm_format, model_from_name, normalize_model


def _normalize_address(address: str) -> str:
    """Accept common MAC spellings, while rejecting incomplete addresses."""
    address = format_mac(address.strip()).upper()
    if not re.fullmatch(r"(?:[0-9A-F]{2}:){5}[0-9A-F]{2}", address):
        raise ValueError("Invalid Bluetooth MAC address")
    return address


def is_dx_beacon(info: BluetoothServiceInfoBleak) -> bool:
    """Require DX manufacturer data or the address-bearing DX info frame."""
    if DX_MANUFACTURER_ID in info.manufacturer_data:
        return True
    payload = info.service_data.get(DX_BATT_UUID)
    if payload is None or len(payload) != 7 or payload[0] > 100:
        return False
    try:
        address = _normalize_address(info.address)
    except ValueError:
        return False
    return bytes(payload[1:7]) == bytes.fromhex(address.replace(":", ""))


def infer_model(info: BluetoothServiceInfoBleak) -> str:
    """Use an advertised model or an honest generic label when unknown."""
    return model_from_name(info.name) or DEFAULT_MODEL


def _model(value: str) -> str:
    """Do not store an empty hardware model."""
    if not (value := value.strip()):
        raise vol.Invalid("Model cannot be empty")
    return normalize_model(value)


class DxBeaconConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Discover, manually add, or replace a beacon."""

    VERSION = 1

    def __init__(self) -> None:
        self._discovered: dict[str, str] = {}
        self._models: dict[str, str] = {}
        self._address: str | None = None
        self._model = DEFAULT_MODEL

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry):
        """Allow settings changes with Home Assistant's automatic reload."""
        return DxBeaconOptionsFlow()

    def _discover(self) -> None:
        """Offer other beacon protocols for deliberate manual selection, too."""
        self._discovered.clear()
        self._models.clear()
        for info in async_discovered_service_info(self.hass, connectable=False):
            apple_payload = info.manufacturer_data.get(APPLE_ID, b"")
            if not (
                EDDYSTONE_UUID in info.service_data
                or DX_BATT_UUID in info.service_data
                or (len(apple_payload) >= 23 and apple_payload[:2] == b"\x02\x15")
                or DX_MANUFACTURER_ID in info.manufacturer_data
            ):
                continue
            try:
                address = _normalize_address(info.address)
            except ValueError:
                continue
            self._discovered[address] = f"{info.name or 'Beacon'} ({address})"
            self._models[address] = infer_model(info)

    def _abort_if_identity_configured(
        self, address: str, *, exclude_entry_id: str | None = None
    ) -> None:
        """Do not reuse an address that still anchors another entry's entities."""
        for entry in self.hass.config_entries.async_entries(DOMAIN):
            if entry.entry_id == exclude_entry_id:
                continue
            identity = entry.data.get(
                CONF_IDENTITY_ADDRESS, entry.data.get(CONF_ADDRESS)
            )
            if identity is None:
                continue
            try:
                identity = _normalize_address(identity)
            except ValueError:
                continue
            if identity == address:
                raise data_entry_flow.AbortFlow("already_configured")

    async def async_step_bluetooth(self, info: BluetoothServiceInfoBleak):
        """Only automatically identify advertisements with DX-specific evidence."""
        if not is_dx_beacon(info):
            return self.async_abort(reason="not_supported")
        try:
            address = _normalize_address(info.address)
        except ValueError:
            return self.async_abort(reason="not_supported")
        await self.async_set_unique_id(address)
        self._abort_if_unique_id_configured()
        self._abort_if_identity_configured(address)
        self._address = address
        self._model = infer_model(info)
        self.context["title_placeholders"] = {"name": address}
        return await self.async_step_confirm()

    async def async_step_confirm(self, user_input=None):
        """Confirm discovery and allow a model correction."""
        errors = {}
        if user_input is not None:
            try:
                model = _model(user_input.get(CONF_MODEL, self._model))
            except vol.Invalid:
                errors[CONF_MODEL] = "invalid_model"
            else:
                return self.async_create_entry(
                    title=f"DX beacon {self._address}",
                    data={CONF_ADDRESS: self._address, CONF_MODEL: model},
                )
        return self.async_show_form(
            step_id="confirm",
            data_schema=vol.Schema(
                {vol.Required(CONF_MODEL, default=self._model): str}
            ),
            description_placeholders={"name": self._address},
            errors=errors,
        )

    async def async_step_user(self, user_input=None):
        """Always leave a path to manual entry, even with discovered beacons."""
        # Preserve direct submissions used by existing callers and tests.
        if user_input is not None:
            return await self.async_step_manual(user_input)
        self._discover()
        if not self._discovered:
            return await self.async_step_manual()
        return self.async_show_menu(
            step_id="user", menu_options=["discovered", "manual"]
        )

    async def async_step_discovered(self, user_input=None):
        """Select an observed beacon without requiring DX auto identification."""
        if user_input is not None:
            self._address = user_input[CONF_ADDRESS]
            self._model = self._models.get(self._address, DEFAULT_MODEL)
            return await self.async_step_manual()
        return self.async_show_form(
            step_id="discovered",
            data_schema=vol.Schema(
                {vol.Required(CONF_ADDRESS): vol.In(self._discovered)}
            ),
        )

    async def async_step_manual(self, user_input=None):
        """Accept a beacon address regardless of the current discovery cache."""
        errors = {}
        if user_input is not None:
            try:
                address = _normalize_address(user_input[CONF_ADDRESS])
            except ValueError:
                errors[CONF_ADDRESS] = "invalid_address"
            try:
                model = _model(user_input.get(CONF_MODEL, self._model))
            except vol.Invalid:
                errors[CONF_MODEL] = "invalid_model"
            if not errors:
                await self.async_set_unique_id(address)
                self._abort_if_unique_id_configured()
                self._abort_if_identity_configured(address)
                return self.async_create_entry(
                    title=f"DX beacon {address}",
                    data={
                        CONF_ADDRESS: address,
                        CONF_MODEL: model,
                    },
                )
        return self.async_show_form(
            step_id="manual",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_ADDRESS, default=self._address or ""): str,
                    vol.Required(CONF_MODEL, default=self._model): str,
                }
            ),
            errors=errors,
        )

    async def async_step_reconfigure(self, user_input=None):
        """Replace hardware while retaining entity and device identities."""
        entry = self._get_reconfigure_entry()
        errors = {}
        if user_input is not None:
            try:
                address = _normalize_address(user_input[CONF_ADDRESS])
            except ValueError:
                errors[CONF_ADDRESS] = "invalid_address"
            try:
                model = _model(
                    user_input.get(
                        CONF_MODEL, entry.data.get(CONF_MODEL, DEFAULT_MODEL)
                    )
                )
            except vol.Invalid:
                errors[CONF_MODEL] = "invalid_model"
            if not errors:
                await self.async_set_unique_id(address)
                if address != entry.unique_id:
                    self._abort_if_unique_id_configured()
                self._abort_if_identity_configured(
                    address, exclude_entry_id=entry.entry_id
                )
                return self.async_update_reload_and_abort(
                    entry,
                    unique_id=address,
                    data_updates={
                        CONF_ADDRESS: address,
                        CONF_MODEL: model,
                        CONF_IDENTITY_ADDRESS: entry.data.get(
                            CONF_IDENTITY_ADDRESS, entry.data[CONF_ADDRESS]
                        ),
                    },
                )
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_ADDRESS, default=entry.data[CONF_ADDRESS]): str,
                    vol.Required(
                        CONF_MODEL, default=entry.data.get(CONF_MODEL, DEFAULT_MODEL)
                    ): str,
                }
            ),
            errors=errors,
        )


class DxBeaconOptionsFlow(config_entries.OptionsFlowWithReload):
    """Adjust presence timing and telemetry interpretation."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        """Validate user settings and preserve any other saved options."""
        options = self.config_entry.options
        model = self.config_entry.data.get(CONF_MODEL, DEFAULT_MODEL)
        default_tlm = default_tlm_format(model)
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_DEPARTURE_TIMEOUT,
                    default=options.get(
                        CONF_DEPARTURE_TIMEOUT, DEFAULT_DEPARTURE_TIMEOUT
                    ),
                ): vol.All(vol.Coerce(int), vol.Range(min=10, max=3600)),
                vol.Required(
                    CONF_TELEMETRY_TIMEOUT,
                    default=options.get(
                        CONF_TELEMETRY_TIMEOUT, DEFAULT_TELEMETRY_TIMEOUT
                    ),
                ): vol.All(vol.Coerce(int), vol.Range(min=60, max=86400)),
                vol.Required(
                    CONF_LOW_BATTERY_THRESHOLD,
                    default=options.get(
                        CONF_LOW_BATTERY_THRESHOLD, DEFAULT_LOW_BATTERY_THRESHOLD
                    ),
                ): vol.All(vol.Coerce(int), vol.Range(min=1, max=100)),
                vol.Required(
                    CONF_TLM_FORMAT, default=options.get(CONF_TLM_FORMAT, default_tlm)
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[TLM_STANDARD, TLM_DX_CP35],
                        translation_key="tlm_format",
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
            }
        )
        errors = {}
        if user_input is not None:
            try:
                validated = schema(user_input)
            except vol.Invalid:
                errors["base"] = "invalid_options"
            else:
                return self.async_create_entry(title="", data={**options, **validated})
        return self.async_show_form(step_id="init", data_schema=schema, errors=errors)
