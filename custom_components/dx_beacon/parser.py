"""Decode beacon frames without guessing byte order from their values."""

from __future__ import annotations

from .const import TLM_DX_CP35, TLM_STANDARD


def parse_battery(payload: bytes, address: str) -> dict | None:
    """Decode legacy battery-only and CP35 address-bearing device-info frames."""
    if len(payload) not in (1, 7) or payload[0] > 100:
        return None
    if (
        len(payload) == 7
        and payload[1:].hex().upper() != address.replace(":", "").upper()
    ):
        return None
    return {"battery_pct": payload[0]}


def parse_tlm(payload: bytes, tlm_format: str = TLM_STANDARD) -> dict | None:
    """Decode standard TLM or the explicitly selected CP35 firmware layout."""
    if len(payload) < 14 or payload[:2] != b"\x20\x00":
        return None
    if tlm_format not in (TLM_STANDARD, TLM_DX_CP35):
        return None
    byteorder = "little" if tlm_format == TLM_DX_CP35 else "big"
    return {
        "battery_mv": int.from_bytes(payload[2:4], byteorder) or None,
        "temperature": (
            None
            if payload[4:6] == b"\x80\x00"
            else int.from_bytes(payload[4:6], "big", signed=True) / 256
        ),
        "adv_count": int.from_bytes(payload[6:10], byteorder),
        "uptime_s": int.from_bytes(payload[10:14], byteorder) / 10,
    }


def parse_ibeacon(payload: bytes) -> dict | None:
    """Decode an Apple-format iBeacon manufacturer payload."""
    if len(payload) < 23 or payload[:2] != b"\x02\x15":
        return None
    u = payload[2:18].hex()
    return {
        "uuid": f"{u[:8]}-{u[8:12]}-{u[12:16]}-{u[16:20]}-{u[20:]}",
        "major": int.from_bytes(payload[18:20], "big"),
        "minor": int.from_bytes(payload[20:22], "big"),
        "tx_power": int.from_bytes(payload[22:23], "big", signed=True),
    }


def estimate_distance(tx_power: int | None, rssi: int | None) -> float | None:
    """Estimate proximity using the iBeacon Tracker formula."""
    if tx_power is None or rssi is None or tx_power >= 0 or rssi >= 0:
        return None
    ratio = rssi / tx_power
    distance = ratio**10 if ratio < 1 else 0.89976 * ratio**7.7095 + 0.111
    return round(distance, 2)


def packet_payloads(raw: bytes) -> tuple[dict[str, bytes], dict[int, bytes]]:
    """Extract only frames actually present in one raw BLE advertisement.

    Home Assistant's service/manufacturer dictionaries merge older frames.
    Those dictionaries cannot establish individual packet observation times.
    """
    services: dict[str, bytes] = {}
    manufacturers: dict[int, bytes] = {}
    offset = 0
    while offset < len(raw) and (length := raw[offset]):
        end = offset + length + 1
        if end > len(raw):
            break
        ad_type = raw[offset + 1]
        data = raw[offset + 2 : end]
        if ad_type == 0x16 and len(data) >= 2:
            uuid = int.from_bytes(data[:2], "little")
            services[f"0000{uuid:04x}-0000-1000-8000-00805f9b34fb"] = data[2:]
        elif ad_type == 0xFF and len(data) >= 2:
            manufacturers[int.from_bytes(data[:2], "little")] = data[2:]
        offset = end
    return services, manufacturers
