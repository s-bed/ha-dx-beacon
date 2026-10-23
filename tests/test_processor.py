"""Packet parsing, frame freshness and presence deadlines use real observations."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from custom_components.dx_beacon import TLM_KEYS, BeaconProcessor
from custom_components.dx_beacon.const import (
    APPLE_ID,
    CONF_DEPARTURE_TIMEOUT,
    CONF_TELEMETRY_TIMEOUT,
    CONF_TLM_FORMAT,
    DX_BATT_UUID,
    EDDYSTONE_UUID,
    TLM_DX_CP35,
    TLM_STANDARD,
)
from custom_components.dx_beacon.parser import packet_payloads, parse_ibeacon, parse_tlm

ADDRESS = "48:87:2D:9E:5C:3D"
SOURCE = "A8:BD:27:C5:31:30"
NOW = datetime(2026, 10, 23, 12, 0, 0, tzinfo=UTC)
CAPTURE_TLM = bytes.fromhex("2000930c17000412000018120000")
CAPTURE_IBEACON = bytes.fromhex("0215e2c56db5dffb48d2b060d0f5a71096e000050006c7")
CAPTURE_BATTERY = bytes.fromhex("6448872d9e5c3d")


def tlm_payload(
    *,
    battery_mv=3219,
    temperature=23,
    adv_count=4612,
    uptime_ticks=4632,
    byteorder="little",
):
    return (
        b"\x20\x00"
        + battery_mv.to_bytes(2, byteorder)
        + int(temperature * 256).to_bytes(2, "big", signed=True)
        + adv_count.to_bytes(4, byteorder)
        + uptime_ticks.to_bytes(4, byteorder)
    )


def raw_service(uuid, payload):
    """One actual service-data AD structure, followed by padding."""
    uuid_bytes = int(uuid[4:8], 16).to_bytes(2, "little")
    return (
        b"\x02\x01\x06"
        + bytes([len(payload) + 3, 0x16])
        + uuid_bytes
        + payload
        + b"\x00"
    )


def observation(
    *, time=100, services=None, manufacturers=None, raw=None, source=SOURCE
):
    return SimpleNamespace(
        time=float(time),
        source=source,
        service_data=services or {},
        manufacturer_data=manufacturers or {},
        raw=raw,
    )


@pytest.fixture
def processor():
    with patch("custom_components.dx_beacon.monotonic_time_coarse", return_value=100):
        return BeaconProcessor(
            ADDRESS,
            {
                CONF_TLM_FORMAT: TLM_DX_CP35,
                CONF_DEPARTURE_TIMEOUT: 30,
                CONF_TELEMETRY_TIMEOUT: 10,
            },
        )


def test_cp35_capture_uses_explicit_firmware_layout():
    assert parse_tlm(CAPTURE_TLM, TLM_DX_CP35) == {
        "battery_mv": 3219,
        "temperature": 23,
        "adv_count": 4612,
        "uptime_s": 463.2,
    }


def test_standard_tlm_does_not_guess_plausible_little_endian_values():
    """Both voltage orders can look plausible; 19-day counters must not reset."""
    payload = tlm_payload(
        battery_mv=3078,
        temperature=-3.5,
        adv_count=0x01000000,
        uptime_ticks=0x01000000,
        byteorder="big",
    )
    assert parse_tlm(payload) == {
        "battery_mv": 3078,
        "temperature": -3.5,
        "adv_count": 0x01000000,
        "uptime_s": 0x01000000 / 10,
    }


def test_tlm_unsupported_temperature_and_battery_sentinels():
    payload = b"\x20\x00\x00\x00\x80\x00" + bytes(8)
    parsed = parse_tlm(payload)
    assert parsed["battery_mv"] is None
    assert parsed["temperature"] is None
    assert parsed["uptime_s"] == 0


@pytest.mark.parametrize(
    "payload,tlm_format",
    [
        (b"", TLM_STANDARD),
        (CAPTURE_TLM[:-1], TLM_DX_CP35),
        (b"\x10\x00" + CAPTURE_TLM[2:], TLM_DX_CP35),
        (b"\x20\x01" + CAPTURE_TLM[2:], TLM_DX_CP35),
        (CAPTURE_TLM, "unsupported"),
    ],
)
def test_malformed_or_unsupported_tlm_is_rejected(payload, tlm_format):
    assert parse_tlm(payload, tlm_format) is None


def test_ibeacon_capture_and_malformed_packets():
    assert parse_ibeacon(CAPTURE_IBEACON) == {
        "uuid": "e2c56db5-dffb-48d2-b060-d0f5a71096e0",
        "major": 5,
        "minor": 6,
        "tx_power": -57,
    }
    assert parse_ibeacon(CAPTURE_IBEACON[:-1]) is None
    assert parse_ibeacon(b"\x02\x14" + CAPTURE_IBEACON[2:]) is None


def test_raw_packet_parsing_uses_actual_ad_structures():
    """Ignore truncated tails while preserving complete earlier structures."""
    raw = bytes.fromhex(
        "0201060303aafe1116aafe2000930c17000412000018120000000000000000"
    )
    assert packet_payloads(raw) == ({EDDYSTONE_UUID: CAPTURE_TLM}, {})
    manufacturer = bytes([len(CAPTURE_IBEACON) + 3, 0xFF, 0x4C, 0]) + CAPTURE_IBEACON
    assert packet_payloads(manufacturer + b"\x10\x16") == (
        {},
        {APPLE_ID: CAPTURE_IBEACON},
    )
    assert packet_payloads(b"\xff\x16") == ({}, {})


def test_duplicate_and_out_of_order_observations_do_not_refresh_frames(processor):
    processor.observe(
        observation(services={DX_BATT_UUID: CAPTURE_BATTERY}),
        NOW,
        100,
    )
    first = processor.data
    # A changed merged payload at the exact same source timestamp is still cached.
    processor.observe(
        observation(services={DX_BATT_UUID: b"\x01"}),
        NOW + timedelta(seconds=5),
        105,
    )
    assert processor.data == first
    processor.observe(
        observation(time=99, source="other", services={DX_BATT_UUID: b"\x02"}),
        NOW + timedelta(seconds=5),
        105,
    )
    assert processor.data == first
    # Future observation timestamps cannot create persistent false presence.
    processor.observe(
        observation(time=200, services={DX_BATT_UUID: b"\x03"}),
        NOW,
        100,
    )
    assert processor.data == first


def test_raw_tlm_does_not_refresh_merged_cached_battery(processor):
    processor.observe(
        observation(
            services={DX_BATT_UUID: CAPTURE_BATTERY},
            raw=raw_service(DX_BATT_UUID, CAPTURE_BATTERY),
        ),
        NOW,
        100,
    )
    processor.observe(
        observation(
            time=108,
            services={DX_BATT_UUID: CAPTURE_BATTERY, EDDYSTONE_UUID: CAPTURE_TLM},
            raw=raw_service(EDDYSTONE_UUID, CAPTURE_TLM),
        ),
        NOW + timedelta(seconds=8),
        108,
    )
    assert processor.data.battery_seen == NOW
    assert processor.data.tlm_seen == NOW + timedelta(seconds=8)
    assert processor.data.last_seen == NOW + timedelta(seconds=8)
    assert processor.data.freshness_method == "raw_packet"
    processor.expire(NOW + timedelta(seconds=11), 111, monitoring=True)
    assert processor.data.stale_keys == frozenset({"battery_pct"})
    assert processor.data.battery_pct == 100
    assert processor.data.present


def test_repeated_raw_frames_refresh_their_own_observation_time(processor):
    raw = raw_service(EDDYSTONE_UUID, CAPTURE_TLM)
    processor.observe(observation(raw=raw), NOW, 100)
    processor.observe(observation(time=105, raw=raw), NOW + timedelta(seconds=5), 105)
    assert processor.data.tlm_seen == NOW + timedelta(seconds=5)
    assert processor.data.battery_seen is None


def test_merged_unchanged_payloads_expire_conservatively(processor):
    services = {DX_BATT_UUID: CAPTURE_BATTERY, EDDYSTONE_UUID: CAPTURE_TLM}
    processor.observe(observation(services=services), NOW, 100)
    processor.observe(
        observation(time=115, services=services),
        NOW + timedelta(seconds=15),
        115,
    )
    processor.expire(NOW + timedelta(seconds=15), 115, monitoring=True)
    assert processor.data.present
    assert processor.data.receiving
    assert processor.data.last_seen == NOW + timedelta(seconds=15)
    assert processor.data.battery_seen is None
    assert processor.data.tlm_seen is None
    assert processor.data.stale_keys == TLM_KEYS | {"battery_pct"}
    assert processor.data.freshness_method == "payload_change"
    assert processor.data.battery_pct == 100  # Retain last-known measurements.


def test_initial_merged_values_seed_history_with_unknown_frame_freshness(processor):
    processor.observe(
        observation(
            services={DX_BATT_UUID: CAPTURE_BATTERY, EDDYSTONE_UUID: CAPTURE_TLM},
            manufacturers={APPLE_ID: CAPTURE_IBEACON},
        ),
        NOW,
        100,
    )
    processor.expire(NOW, 100, monitoring=True)
    assert processor.data.battery_pct == 100
    assert processor.data.battery_mv == 3219
    assert processor.data.uuid == "e2c56db5-dffb-48d2-b060-d0f5a71096e0"
    assert processor.data.battery_seen is None
    assert processor.data.tlm_seen is None
    assert processor.data.ibeacon_seen is None
    assert processor.data.stale_keys == TLM_KEYS | {"battery_pct"}
    assert processor.data.last_seen == NOW


def test_merged_payload_changes_confirm_only_the_changed_frame(processor):
    processor.observe(
        observation(
            services={DX_BATT_UUID: CAPTURE_BATTERY, EDDYSTONE_UUID: CAPTURE_TLM},
            manufacturers={APPLE_ID: CAPTURE_IBEACON},
        ),
        NOW,
        100,
    )
    changed_tlm = tlm_payload(adv_count=4613, uptime_ticks=4682)
    processor.observe(
        observation(
            time=105,
            services={DX_BATT_UUID: CAPTURE_BATTERY, EDDYSTONE_UUID: changed_tlm},
            manufacturers={APPLE_ID: CAPTURE_IBEACON},
        ),
        NOW + timedelta(seconds=5),
        105,
    )
    processor.expire(NOW + timedelta(seconds=5), 105, monitoring=True)
    assert processor.data.battery_seen is None
    assert processor.data.ibeacon_seen is None
    assert processor.data.tlm_seen == NOW + timedelta(seconds=5)
    assert processor.data.stale_keys == {"battery_pct"}
    assert processor.data.adv_count == 4613
    assert processor.data.freshness_method == "payload_change"


def test_frame_timestamps_account_for_cached_observation_age(processor):
    processor.observe(
        observation(raw=raw_service(EDDYSTONE_UUID, CAPTURE_TLM)),
        NOW + timedelta(seconds=5),
        105,
    )
    assert processor.data.last_seen == NOW
    assert processor.data.tlm_seen == NOW
    processor.expire(NOW + timedelta(seconds=31), 131, monitoring=True)
    assert processor.data.present is False


def test_startup_presence_waits_for_the_configured_deadline(processor):
    assert processor.data.present is None
    processor.expire(NOW + timedelta(seconds=29), 129, monitoring=True)
    assert processor.data.present is None
    processor.expire(NOW + timedelta(seconds=30), 130, monitoring=True)
    assert processor.data.present is False
    processor.observe(observation(time=131), NOW + timedelta(seconds=31), 131)
    processor.expire(NOW + timedelta(seconds=31), 131, monitoring=True)
    assert processor.data.present is True
    processor.expire(NOW + timedelta(seconds=60), 160, monitoring=True)
    assert processor.data.present is True
    processor.expire(NOW + timedelta(seconds=61), 161, monitoring=True)
    assert processor.data.present is False


def test_no_scanners_means_unknown_presence_and_reception(processor):
    processor.observe(observation(), NOW, 100)
    processor.expire(NOW, 100, monitoring=False)
    assert processor.data.present is None
    assert not processor.data.receiving
    assert processor.data.monitoring is False
    processor.expire(NOW + timedelta(seconds=2), 102, monitoring=True)
    assert processor.data.present is True
    processor.expire(NOW + timedelta(seconds=60), 160, monitoring=False)
    assert processor.data.present is None


def test_parser_errors_do_not_refresh_valid_telemetry(processor):
    processor.observe(
        observation(raw=raw_service(EDDYSTONE_UUID, CAPTURE_TLM)),
        NOW,
        100,
    )
    processor.observe(
        observation(time=105, services={EDDYSTONE_UUID: CAPTURE_TLM[:-1]}),
        NOW + timedelta(seconds=5),
        105,
    )
    assert processor.data.parser_errors == {"eddystone_tlm": 1}
    assert processor.data.tlm_seen == NOW
    assert processor.data.battery_mv == 3219


@pytest.mark.parametrize(
    "payload",
    [b"\x65", b"\x64\x00", bytes.fromhex("6448872d9e0000")],
)
def test_invalid_battery_percentage_or_embedded_address_is_rejected(processor, payload):
    processor.observe(observation(services={DX_BATT_UUID: payload}), NOW, 100)
    assert processor.data.battery_pct is None
    assert processor.data.battery_seen is None
    assert processor.data.parser_errors == {"dx_battery": 1}


def test_simultaneous_counter_decrease_detects_reboot(processor):
    processor.observe(
        observation(
            raw=raw_service(
                EDDYSTONE_UUID, tlm_payload(adv_count=1000, uptime_ticks=1000)
            )
        ),
        NOW,
        100,
    )
    processor.observe(
        observation(
            time=120,
            raw=raw_service(
                EDDYSTONE_UUID, tlm_payload(adv_count=10, uptime_ticks=100)
            ),
        ),
        NOW + timedelta(seconds=20),
        120,
    )
    assert processor.data.last_reboot == NOW + timedelta(seconds=10)


@pytest.mark.parametrize(
    "old_count,old_ticks,new_count,new_ticks",
    [
        (0xFFFFFFF0, 0xFFFFFFF0, 10, 100),  # Uptime and packet counters wrapped.
        (0xFFFFFFF0, 1000, 10, 1100),  # Packet counter alone wrapped.
    ],
)
def test_counter_wrap_does_not_report_a_reboot(
    processor,
    old_count,
    old_ticks,
    new_count,
    new_ticks,
):
    processor.observe(
        observation(
            raw=raw_service(
                EDDYSTONE_UUID, tlm_payload(adv_count=old_count, uptime_ticks=old_ticks)
            )
        ),
        NOW,
        100,
    )
    processor.observe(
        observation(
            time=120,
            raw=raw_service(
                EDDYSTONE_UUID, tlm_payload(adv_count=new_count, uptime_ticks=new_ticks)
            ),
        ),
        NOW + timedelta(seconds=20),
        120,
    )
    assert processor.data.last_reboot is None
