"""DX-SMART beacon: iBeacon + Eddystone TLM + batteria, con device_tracker."""
from __future__ import annotations

import logging
from dataclasses import dataclass, replace

from homeassistant.components import bluetooth
from homeassistant.components.bluetooth import (
    BluetoothCallbackMatcher,
    BluetoothChange,
    BluetoothScanningMode,
    BluetoothServiceInfoBleak,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .const import CONF_ADDRESS, DOMAIN, EDDYSTONE_UUID, DX_BATT_UUID, APPLE_ID

_LOGGER = logging.getLogger(__name__)
PLATFORMS = ["sensor", "device_tracker"]


@dataclass
class BeaconData:
    # TLM
    battery_mv: int | None = None
    battery_pct: int | None = None
    temperature: float | None = None
    adv_count: int | None = None
    uptime_s: float | None = None
    # iBeacon
    uuid: str | None = None
    major: int | None = None
    minor: int | None = None
    tx_power: int | None = None
    distance: float | None = None
    # comune
    rssi: int | None = None
    source: str | None = None
    present: bool = False


def parse_tlm(payload: bytes) -> dict | None:
    if len(payload) < 14 or payload[0] != 0x20 or payload[1] != 0x00:
        return None
    vbat_le = int.from_bytes(payload[2:4], "little")
    vbat_be = int.from_bytes(payload[2:4], "big")
    vbat = vbat_le if 1500 <= vbat_le <= 4500 else vbat_be
    t_int = int.from_bytes(payload[4:5], "big", signed=True)
    temp = t_int + payload[5] / 256.0 if payload[4:6] != b"\x80\x00" else None
    adv_be, adv_le = int.from_bytes(payload[6:10], "big"), int.from_bytes(payload[6:10], "little")
    sec_be, sec_le = int.from_bytes(payload[10:14], "big"), int.from_bytes(payload[10:14], "little")
    adv = adv_le if adv_be > 0x00FFFFFF and adv_le <= 0x00FFFFFF else adv_be
    sec = sec_le if sec_be > 0x00FFFFFF and sec_le <= 0x00FFFFFF else sec_be
    return {"battery_mv": vbat or None, "temperature": temp, "adv_count": adv, "uptime_s": sec / 10.0}


def parse_ibeacon(payload: bytes) -> dict | None:
    if len(payload) < 23 or payload[0] != 0x02 or payload[1] != 0x15:
        return None
    u = payload[2:18].hex()
    uuid = f"{u[0:8]}-{u[8:12]}-{u[12:16]}-{u[16:20]}-{u[20:32]}"
    return {
        "uuid": uuid,
        "major": int.from_bytes(payload[18:20], "big"),
        "minor": int.from_bytes(payload[20:22], "big"),
        "tx_power": int.from_bytes(payload[22:23], "big", signed=True),
    }


def estimate_distance(tx_power: int | None, rssi: int | None) -> float | None:
    """Stessa formula usata da Home Assistant (iBeacon Tracker / Android Beacon Library)."""
    if tx_power is None or rssi is None or rssi == 0 or tx_power == 0:
        return None
    ratio = rssi / tx_power
    if ratio < 1.0:
        d = ratio**10
    else:
        d = 0.89976 * ratio**7.7095 + 0.111
    return round(d, 2)


def signal(entry_id: str) -> str:
    return f"{DOMAIN}_{entry_id}_update"


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    address = entry.data[CONF_ADDRESS].upper()
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = BeaconData()

    @callback
    def _push(data: BeaconData) -> None:
        hass.data[DOMAIN][entry.entry_id] = data
        async_dispatcher_send(hass, signal(entry.entry_id), data)

    @callback
    def _on_adv(info: BluetoothServiceInfoBleak, change: BluetoothChange) -> None:
        prev: BeaconData = hass.data[DOMAIN][entry.entry_id]
        upd: dict = {"rssi": info.rssi, "source": info.source, "present": True}
        raw = info.service_data.get(EDDYSTONE_UUID)
        if raw and (tlm := parse_tlm(bytes(raw))):
            upd.update(tlm)
        batt = info.service_data.get(DX_BATT_UUID)
        if batt and 0 <= batt[0] <= 100:
            upd["battery_pct"] = batt[0]
        ib = info.manufacturer_data.get(APPLE_ID)
        if ib and (ibd := parse_ibeacon(bytes(ib))):
            upd.update(ibd)
        data = replace(prev, **upd)
        data.distance = estimate_distance(data.tx_power, data.rssi)
        _push(data)

    @callback
    def _on_unavailable(info: BluetoothServiceInfoBleak) -> None:
        prev: BeaconData = hass.data[DOMAIN][entry.entry_id]
        _push(replace(prev, present=False, distance=None, rssi=None))

    entry.async_on_unload(
        bluetooth.async_register_callback(
            hass, _on_adv, BluetoothCallbackMatcher(address=address), BluetoothScanningMode.PASSIVE
        )
    )
    entry.async_on_unload(
        bluetooth.async_track_unavailable(hass, _on_unavailable, address, connectable=False)
    )
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    hass.data[DOMAIN].pop(entry.entry_id, None)
    return ok
