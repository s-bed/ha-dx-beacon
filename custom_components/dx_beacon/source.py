"""Resolve Bluetooth receivers to their Home Assistant names."""

from __future__ import annotations

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr


@callback
def async_resolve_source_name(hass: HomeAssistant, source: str) -> str:
    """Prefer the receiver's user-assigned device name over its adapter title."""
    entry = next(
        (
            entry
            for entry in hass.config_entries.async_entries("bluetooth")
            if entry.unique_id and entry.unique_id.casefold() == source.casefold()
        ),
        None,
    )
    registry = dr.async_get(hass)
    device = None
    # Remote scanners can have a different Bluetooth and network MAC. Home
    # Assistant links their Bluetooth entry to the actual proxy device.
    if entry and (device_id := entry.data.get("source_device_id")):
        device = registry.async_get(device_id)
    if device is None:
        device = registry.async_get_device(
            connections={(dr.CONNECTION_BLUETOOTH, dr.format_mac(source))}
        )
    if device and (name := device.name_by_user or device.name):
        return name
    return (entry.title if entry else None) or source
