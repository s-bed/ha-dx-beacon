"""Downloadable, privacy-preserving diagnostics for DX beacons."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from typing import Any

from homeassistant.components.diagnostics import REDACTED
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import (
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
    TLM_DX_CP35,
    TLM_STANDARD,
)
from .model import default_tlm_format

_PRIVATE_KEYS = frozenset(
    {CONF_ADDRESS, CONF_IDENTITY_ADDRESS, "source", "source_name", "uuid", "name"}
)
_CONFIG_KEYS = _PRIVATE_KEYS | {
    CONF_MODEL,
    CONF_DEPARTURE_TIMEOUT,
    CONF_TELEMETRY_TIMEOUT,
    CONF_LOW_BATTERY_THRESHOLD,
    CONF_TLM_FORMAT,
}
_RUNTIME_KEYS = _PRIVATE_KEYS | {
    "battery_mv",
    "battery_pct",
    "temperature",
    "adv_count",
    "uptime_s",
    "major",
    "minor",
    "tx_power",
    "distance",
    "rssi",
    "present",
    "receiving",
    "monitoring",
    "last_seen",
    "battery_seen",
    "tlm_seen",
    "ibeacon_seen",
    "last_reboot",
    "stale_keys",
    "frames_seen",
    "parser_errors",
    "tlm_format",
    "freshness_method",
}


def _serialize(value: Any, key: str | None = None) -> Any:
    """Keep reports JSON serializable without exposing device identities."""
    if key in _PRIVATE_KEYS:
        return REDACTED if value is not None else None
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {name: _serialize(item, name) for name, item in value.items()}
    if isinstance(value, (frozenset, set)):
        return sorted(_serialize(item) for item in value)
    if isinstance(value, (tuple, list)):
        return [_serialize(item) for item in value]
    return value


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return safe settings, telemetry, frame freshness and parsing decisions."""
    options = getattr(entry, "options", {})
    model = entry.data.get(CONF_MODEL, DEFAULT_MODEL)
    default_tlm = default_tlm_format(model)
    settings = {
        key: options.get(key, entry.data.get(key, default))
        for key, default in (
            (CONF_DEPARTURE_TIMEOUT, DEFAULT_DEPARTURE_TIMEOUT),
            (CONF_TELEMETRY_TIMEOUT, DEFAULT_TELEMETRY_TIMEOUT),
            (CONF_LOW_BATTERY_THRESHOLD, DEFAULT_LOW_BATTERY_THRESHOLD),
            (CONF_TLM_FORMAT, default_tlm),
        )
    }
    data = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    runtime = (
        _serialize(
            {key: value for key, value in asdict(data).items() if key in _RUNTIME_KEYS}
        )
        if data is not None
        else None
    )
    tlm_format = (
        runtime.get("tlm_format") if runtime is not None else None
    ) or settings[CONF_TLM_FORMAT]
    byte_order = "little" if tlm_format == TLM_DX_CP35 else "big"
    return {
        "device": {
            "manufacturer": "DX-SMART",
            "model": model,
        },
        "configuration": {
            "data": _serialize(
                {key: value for key, value in entry.data.items() if key in _CONFIG_KEYS}
            ),
            "options": _serialize(
                {key: value for key, value in options.items() if key in _CONFIG_KEYS}
            ),
            "effective_settings": settings,
        },
        "loaded": data is not None,
        "runtime": runtime,
        "advertisements": {
            "supported_frames": ["dx_battery", "eddystone_tlm", "ibeacon"],
            "seen_frames": runtime.get("frames_seen", []) if runtime else [],
            "last_observations": {
                key: runtime.get(key) if runtime else None
                for key in ("last_seen", "battery_seen", "tlm_seen", "ibeacon_seen")
            },
            "freshness_method": (runtime.get("freshness_method") if runtime else None),
            "freshness_note": (
                "Frame timestamps track received raw packets where available. "
                "When raw packets are unavailable, timestamps conservatively track "
                "observed payload changes; initial merged values have unknown "
                "freshness and cached frames do not refresh telemetry."
            ),
        },
        "parsing": {
            "tlm_format": tlm_format,
            "known_tlm_formats": [TLM_DX_CP35, TLM_STANDARD],
            "byte_order": {
                "battery_mv": byte_order,
                "temperature": "big",
                "adv_count": byte_order,
                "uptime_s": byte_order,
            },
            "decisions": [
                "TLM integer byte order follows the configured format; values are not guessed.",
                "Temperature uses signed big-endian 8.8 fixed-point encoding.",
                "TLM uptime is decoded in tenths of a second.",
            ],
            "errors": runtime.get("parser_errors", {}) if runtime else {},
        },
    }
