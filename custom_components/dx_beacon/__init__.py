"""DX-SMART beacon telemetry and optional home presence."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta

from bluetooth_data_tools import monotonic_time_coarse
from homeassistant.components import bluetooth
from homeassistant.components.bluetooth import (
    BluetoothCallbackMatcher,
    BluetoothChange,
    BluetoothScanningMode,
    BluetoothServiceInfoBleak,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util

from .const import (
    APPLE_ID,
    CONF_ADDRESS,
    CONF_DEPARTURE_TIMEOUT,
    CONF_IDENTITY_ADDRESS,
    CONF_MODEL,
    CONF_TELEMETRY_TIMEOUT,
    CONF_TLM_FORMAT,
    DEFAULT_DEPARTURE_TIMEOUT,
    DEFAULT_MODEL,
    DEFAULT_TELEMETRY_TIMEOUT,
    DOMAIN,
    DX_BATT_UUID,
    DX_MANUFACTURER_ID,
    EDDYSTONE_UUID,
    TLM_STANDARD,
)
from .model import default_tlm_format, model_from_name
from .parser import (
    estimate_distance,
    packet_payloads,
    parse_battery,
    parse_ibeacon,
    parse_tlm,
)
from .source import async_resolve_source_name

PLATFORMS = ["sensor", "device_tracker", "binary_sensor"]
RECEPTION_INTERVAL = timedelta(seconds=10)
TLM_KEYS = frozenset({"battery_mv", "temperature", "adv_count", "uptime_s"})


@dataclass
class BeaconData:
    """Values and observation times, independent of entity enablement."""

    battery_mv: int | None = None
    battery_pct: int | None = None
    temperature: float | None = None
    adv_count: int | None = None
    uptime_s: float | None = None
    uuid: str | None = None
    major: int | None = None
    minor: int | None = None
    tx_power: int | None = None
    distance: float | None = None
    rssi: int | None = None
    source: str | None = None
    source_name: str | None = None
    present: bool | None = None
    receiving: bool = False
    monitoring: bool | None = None
    last_seen: datetime | None = None
    battery_seen: datetime | None = None
    tlm_seen: datetime | None = None
    ibeacon_seen: datetime | None = None
    last_reboot: datetime | None = None
    stale_keys: frozenset[str] = frozenset()
    frames_seen: frozenset[str] = frozenset()
    parser_errors: dict[str, int] = field(default_factory=dict)
    tlm_format: str = TLM_STANDARD
    freshness_method: str = "payload_change"


def signal(entry_id: str) -> str:
    """Return the entity update signal for one config entry."""
    return f"{DOMAIN}_{entry_id}_update"


class BeaconProcessor:
    """Process observations and expire presence using actual observation time."""

    def __init__(self, address: str, options: dict, model: str = DEFAULT_MODEL) -> None:
        self.address = address
        self.departure_timeout = options.get(
            CONF_DEPARTURE_TIMEOUT, DEFAULT_DEPARTURE_TIMEOUT
        )
        self.telemetry_timeout = options.get(
            CONF_TELEMETRY_TIMEOUT, DEFAULT_TELEMETRY_TIMEOUT
        )
        self.data = BeaconData(
            tlm_format=options.get(
                CONF_TLM_FORMAT,
                default_tlm_format(model),
            )
        )
        self.started = monotonic_time_coarse()
        self.last_received: float | None = None
        self._source_times: dict[str, float] = {}
        self._frame_times: dict[str, float] = {}
        self._valid_frame_times: dict[str, float] = {}
        self._payloads: dict[str, bytes] = {}
        self._seeded_frames: set[str] = set()

    def observe(
        self,
        info: BluetoothServiceInfoBleak,
        now: datetime,
        monotonic_now: float,
    ) -> None:
        """Accept each observation once; never rejuvenate cached packets."""
        observed = info.time
        if observed > monotonic_now + 5:
            return
        if observed <= self._source_times.get(info.source, float("-inf")):
            return
        self._source_times[info.source] = observed
        seen_at = now - timedelta(seconds=max(0, monotonic_now - observed))
        updates: dict = {}
        if self.last_received is None or observed > self.last_received:
            self.last_received = observed
            updates["last_seen"] = seen_at
        raw = getattr(info, "raw", None)
        if raw is not None:
            services, manufacturers = packet_payloads(bytes(raw))
            method = "raw_packet"
        else:
            services, manufacturers = info.service_data, info.manufacturer_data
            method = "payload_change"

        if raw is not None:
            # A merged cache can contain useful last-known values for other
            # frames. Retain those without claiming they were observed now.
            cached = {
                "dx_battery": info.service_data.get(DX_BATT_UUID),
                "eddystone_tlm": info.service_data.get(EDDYSTONE_UUID),
                "ibeacon": info.manufacturer_data.get(APPLE_ID)
                or info.manufacturer_data.get(DX_MANUFACTURER_ID),
            }
            for frame, payload in cached.items():
                if (
                    payload is None
                    or frame in self._seeded_frames
                    or frame in self._frame_times
                ):
                    continue
                if frame == "dx_battery":
                    parsed = parse_battery(bytes(payload), self.address)
                elif frame == "eddystone_tlm":
                    parsed = parse_tlm(bytes(payload), self.data.tlm_format)
                else:
                    parsed = parse_ibeacon(bytes(payload))
                if parsed is not None:
                    self._seeded_frames.add(frame)
                    updates.update(parsed)

        tlm_payload = services.get(EDDYSTONE_UUID)
        frames = {
            "eddystone_tlm": tlm_payload
            if tlm_payload and tlm_payload[0] == 0x20
            else None,
            "dx_battery": services.get(DX_BATT_UUID),
            "ibeacon": manufacturers.get(APPLE_ID)
            or manufacturers.get(DX_MANUFACTURER_ID),
        }
        for frame, payload in frames.items():
            if payload is None:
                continue
            payload = bytes(payload)
            if observed <= self._frame_times.get(frame, float("-inf")):
                continue
            # Merged fields without raw bytes cannot prove an unchanged frame
            # was transmitted again. Timestamp payload changes conservatively.
            if raw is None and self._payloads.get(frame) == payload:
                continue
            first_merged_value = raw is None and frame not in self._payloads
            self._frame_times[frame] = observed
            self._payloads[frame] = payload
            parsed: dict | None = None
            if frame == "eddystone_tlm":
                parsed = parse_tlm(payload, self.data.tlm_format)
                if parsed is not None:
                    updates["tlm_seen"] = seen_at
                    previous_uptime = self.data.uptime_s
                    previous_count = self.data.adv_count
                    # A simultaneous decrease indicates a reboot. A natural
                    # uptime-counter rollover must not generate a reboot.
                    if (
                        not first_merged_value
                        and previous_uptime is not None
                        and previous_count is not None
                        and parsed["uptime_s"] < previous_uptime
                        and parsed["adv_count"] < previous_count
                        and not (
                            previous_uptime > 0xF0000000 / 10
                            and parsed["uptime_s"] < 0x0FFFFFFF / 10
                        )
                    ):
                        updates["last_reboot"] = seen_at - timedelta(
                            seconds=parsed["uptime_s"]
                        )
            elif frame == "dx_battery":
                parsed = parse_battery(payload, self.address)
                if parsed is not None:
                    updates["battery_seen"] = seen_at
            else:
                parsed = parse_ibeacon(payload)
                if parsed is not None:
                    updates["ibeacon_seen"] = seen_at
            if parsed is None:
                errors = dict(updates.get("parser_errors", self.data.parser_errors))
                errors[frame] = errors.get(frame, 0) + 1
                updates["parser_errors"] = errors
            else:
                updates.update(parsed)
                if first_merged_value:
                    # The first merged snapshot establishes a baseline, not
                    # proof of when any individual frame was transmitted.
                    timestamp_key = {
                        "dx_battery": "battery_seen",
                        "eddystone_tlm": "tlm_seen",
                        "ibeacon": "ibeacon_seen",
                    }[frame]
                    updates.pop(timestamp_key, None)
                    continue
                self._valid_frame_times[frame] = observed
                updates["frames_seen"] = updates.get(
                    "frames_seen", self.data.frames_seen
                ) | {frame}
                updates["freshness_method"] = method
        self.data = replace(self.data, **updates)

    def expire(self, now: datetime, monotonic_now: float, monitoring: bool) -> None:
        """Apply departure and per-frame expiry without losing history."""
        receiving = (
            self.last_received is not None
            and monotonic_now - self.last_received < self.departure_timeout
        )
        present: bool | None = receiving
        if not monitoring or (
            not receiving
            and (self.last_received is None or self.last_received < self.started)
            and monotonic_now - self.started < self.departure_timeout
        ):
            present = None
        stale: set[str] = set()
        if (
            monotonic_now - self._valid_frame_times.get("dx_battery", float("-inf"))
            >= self.telemetry_timeout
        ):
            stale.add("battery_pct")
        if (
            monotonic_now - self._valid_frame_times.get("eddystone_tlm", float("-inf"))
            >= self.telemetry_timeout
        ):
            stale.update(TLM_KEYS)
        self.data = replace(
            self.data,
            receiving=receiving and monitoring,
            present=present,
            monitoring=monitoring,
            stale_keys=frozenset(stale),
            **(
                {"rssi": None, "distance": None, "source_name": None}
                if not receiving or not monitoring
                else {}
            ),
        )


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Use HA's shared Bluetooth stack without opening a separate scanner."""
    address = entry.data[CONF_ADDRESS].upper()
    _save_detected_model(
        hass, entry, bluetooth.async_last_service_info(hass, address, connectable=False)
    )
    processor = BeaconProcessor(
        address, dict(entry.options), entry.data.get(CONF_MODEL, DEFAULT_MODEL)
    )
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = processor.data
    _update_device(hass, entry, address)
    last_published = float("-inf")
    model_reload_requested = False

    @callback
    def _detect_model(info: BluetoothServiceInfoBleak) -> bool:
        nonlocal model_reload_requested
        if model_reload_requested:
            return True
        if _save_detected_model(hass, entry, info):
            # Rebuild entities and their parser once, retaining all identities.
            # This also handles a legacy beacon first appearing after startup.
            model_reload_requested = True
            hass.config_entries.async_schedule_reload(entry.entry_id)
            return True
        return False

    @callback
    def _push(*, force: bool = False) -> None:
        nonlocal last_published
        previous = hass.data[DOMAIN].get(entry.entry_id)
        if previous == processor.data:
            return
        # Counters and packet timestamps can change many times per second.
        # Publish routine telemetry at most every ten seconds, but deliver
        # presence, battery-alert and availability changes immediately.
        now = monotonic_time_coarse()
        significant = previous is None or any(
            getattr(previous, key) != getattr(processor.data, key)
            for key in (
                "present",
                "receiving",
                "battery_pct",
                "last_reboot",
                "stale_keys",
            )
        )
        if (
            force
            or significant
            or now - last_published >= RECEPTION_INTERVAL.total_seconds()
        ):
            last_published = now
            hass.data[DOMAIN][entry.entry_id] = processor.data
            async_dispatcher_send(hass, signal(entry.entry_id), processor.data)

    @callback
    def _with_reception(info: BluetoothServiceInfoBleak | None) -> None:
        if (
            info is not None
            and processor.data.receiving
            and monotonic_time_coarse() - info.time < processor.departure_timeout
        ):
            processor.data = replace(
                processor.data,
                rssi=info.rssi,
                source=info.source,
                source_name=async_resolve_source_name(hass, info.source),
                distance=estimate_distance(processor.data.tx_power, info.rssi),
            )
        else:
            processor.data = replace(
                processor.data, rssi=None, distance=None, source_name=None
            )

    @callback
    def _on_adv(info: BluetoothServiceInfoBleak, change: BluetoothChange) -> None:
        if _detect_model(info):
            return
        now, monotonic_now = dt_util.utcnow(), monotonic_time_coarse()
        processor.observe(info, now, monotonic_now)
        processor.expire(now, monotonic_now, monitoring=True)
        _with_reception(info)
        _push()

    @callback
    def _refresh_reception(now: datetime) -> None:
        monotonic_now = monotonic_time_coarse()
        info = bluetooth.async_last_service_info(hass, address, connectable=False)
        if info is not None:
            if _detect_model(info):
                return
            processor.observe(info, now, monotonic_now)
        processor.expire(
            now,
            monotonic_now,
            monitoring=any(
                scanner.scanning for scanner in bluetooth.async_current_scanners(hass)
            ),
        )
        _with_reception(info)
        _push(force=True)

    @callback
    def _on_unavailable(info: BluetoothServiceInfoBleak) -> None:
        # HA's learned timeout is independent of the user's departure grace.
        _refresh_reception(dt_util.utcnow())

    entry.async_on_unload(
        bluetooth.async_register_callback(
            hass,
            _on_adv,
            BluetoothCallbackMatcher(address=address, connectable=False),
            BluetoothScanningMode.PASSIVE,
        )
    )
    entry.async_on_unload(
        bluetooth.async_track_unavailable(
            hass,
            _on_unavailable,
            address,
            connectable=False,
        )
    )
    # Newer releases deliver identical advertisements directly. On HA 2026.3
    # the timestamp-aware 10-second cache refresh supplies those observations.
    if register_packets := getattr(
        bluetooth, "async_register_advertisement_callback", None
    ):

        @callback
        def _on_packet(info: BluetoothServiceInfoBleak) -> None:
            if _detect_model(info):
                return
            now, monotonic_now = dt_util.utcnow(), monotonic_time_coarse()
            processor.observe(info, now, monotonic_now)
            processor.expire(now, monotonic_now, monitoring=True)
            _push()

        entry.async_on_unload(register_packets(hass, _on_packet, address))
    entry.async_on_unload(
        async_track_time_interval(
            hass,
            _refresh_reception,
            RECEPTION_INTERVAL,
        )
    )
    _refresh_reception(dt_util.utcnow())
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


@callback
def _save_detected_model(
    hass: HomeAssistant,
    entry: ConfigEntry,
    info: BluetoothServiceInfoBleak | None,
) -> bool:
    """Resolve missing legacy models from names; keep explicit user choices.

    Old device-registry metadata was hardcoded to CP27 and cannot identify
    hardware. Shared service UUIDs and manufacturer frames cannot either.
    """
    if entry.data.get(CONF_MODEL) not in (None, DEFAULT_MODEL) or info is None:
        return False
    if (model := model_from_name(getattr(info, "name", None))) is None:
        return False
    hass.config_entries.async_update_entry(
        entry, data={**entry.data, CONF_MODEL: model}
    )
    return True


@callback
def _update_device(hass: HomeAssistant, entry: ConfigEntry, address: str) -> None:
    """Correct existing metadata and replace connections owned only by DX.

    Other integrations may share the Bluetooth device. Preserve their physical
    address associations when replacing just the DX integration's beacon.
    """
    registry = dr.async_get(hass)
    identity = entry.data.get(CONF_IDENTITY_ADDRESS, address)
    if (device := registry.async_get_device(identifiers={(DOMAIN, identity)})) is None:
        return
    updates: dict = {"model": entry.data.get(CONF_MODEL, DEFAULT_MODEL)}
    if device.config_entries <= {entry.entry_id}:
        updates["new_connections"] = {
            connection
            for connection in device.connections
            if connection[0] != dr.CONNECTION_BLUETOOTH
        } | {(dr.CONNECTION_BLUETOOTH, address)}
    registry.async_update_device(device.id, **updates)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Keep state intact if a platform cannot unload."""
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False
    hass.data[DOMAIN].pop(entry.entry_id, None)
    return True
