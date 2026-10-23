# DX-SMART Beacon for Home Assistant

Home Assistant custom integration for **DX-SMART BLE beacons**, including **DX-CP27** and **DX-CP35**. It reads advertisements through Home Assistant's shared Bluetooth stack (local adapters or ESPHome Bluetooth proxies).

| Entity | Source | Notes |
|---|---|---|
| Presence tracker | Any advertisement | Optional home/away tracking with configurable departure timeout |
| Battery (%) | DX device-info frame, 0xFEAB | Retains the last-known value with its observation time and stale flag |
| Low battery | Battery percentage | Configurable threshold; unavailable when the battery reading is stale |
| Receiving advertisements | Bluetooth reception | Reception status, independent of room inference |
| Last seen | Advertisement timestamp | Packet observation time; reading cached data does not advance it |
| Chip temperature (°C) | Eddystone TLM | Diagnostic; chip temperature, not ambient |
| Battery voltage (mV) | Eddystone TLM | Optional diagnostic |
| Advertising count, uptime | Eddystone TLM | Optional diagnostics |
| Last reboot | Decreasing TLM counters | Optional diagnostic; detected after both uptime and advertising count decrease |
| Estimated distance, signal strength | iBeacon / Bluetooth | Optional diagnostics |
| Nearest receiver | Home Assistant Bluetooth | Optional diagnostic showing Home Assistant's selected adapter/proxy |
| TX power at 1 m | iBeacon | Optional diagnostic |

Distance, signal strength, nearest receiver, TX power, voltage, advertising count, uptime, and last reboot are disabled by default for newly registered entities. Existing enable/disable choices are preserved. Enable optional entities under Settings → Devices & Services → Entities.

## Installation

### HACS

1. HACS → Integrations → ⋮ → Custom repositories → add this repository URL as an Integration.
2. Install **DX-SMART Beacon** and restart Home Assistant.

### Manual

Copy `custom_components/dx_beacon` into your `config/custom_components/` folder and restart.

## Beacon setup in the DX-SMART app

Before adding a beacon to Home Assistant, configure the beacon itself using the manufacturer's **DX-SMART** mobile app.

- Enable the iBeacon, TLM and DX device-info frames in **DX-SMART** for identity, telemetry and battery percentage.
- If using the optional distance diagnostic, calibrate RSSI at 1 m in **DX-SMART**.
- The proprietary ACC frame is not transmitted in legacy BLE advertising on the tested firmware and is not supported.

## Home Assistant configuration

Automatic discovery requires the DX address-bearing device-info frame or DX manufacturer data. Generic Eddystone devices are not automatically identified as DX beacons.

Use Settings → Devices & Services → Add integration → **DX-SMART Beacon** to choose an observed beacon or enter its MAC address manually. Manual entry is available even when other beacons have been discovered, and can add iBeacon-only devices.

The advertised name identifies the model: for example, CP27 names become **DX-CP27**, while CP35 names become **DX-CP35**. Existing entries without a saved model are identified when a recognizable advertisement becomes available, including after startup. An explicit model selection is preserved. Devices with an unrecognizable or customized name use the generic **DX-SMART Beacon** label; choose the actual model during setup or through **Reconfigure**. The shared DX/iBeacon frames do not establish the hardware model, and the old hardcoded CP27 registry label is not used as evidence.

The integration's settings provide:

| Setting | Default | Purpose |
|---|---|---|
| Departure timeout | 180 seconds | Time without a new observation before marking the beacon away |
| Telemetry freshness timeout | 3,600 seconds | Maximum age of battery/TLM observations before they become stale |
| Low battery threshold | 20% | Battery percentage at or below which the low-battery indicator turns on |
| Telemetry format | DX-CP35 for that model | Explicit firmware layout; standard Eddystone is available for other firmware |

Presence initially stays unknown until a recent observation arrives or the startup departure timeout passes. If no Bluetooth scanner is actively scanning, presence is unknown. Timeouts and unchanged-advertisement reception are checked every ten seconds. Missing reception cannot by itself identify departure, an exhausted battery, or lost receiver coverage.

Battery percentage remains visible after expiry with `is_stale: true` and its `last_updated` observation time. Temperature, voltage, advertising count, and uptime become unavailable after their own TLM observation expires. A new iBeacon packet does not refresh battery or TLM timestamps.

Where the scanner supplies raw advertisements, timestamps follow the individual frames actually received. On backends without raw packets, telemetry timestamps conservatively follow observed payload changes. Unchanged merged fields do not prove fresh telemetry. Values found only in the merged cache are retained without inventing an individual frame timestamp.

Use **Reconfigure** on an existing entry to update the model or replace the beacon's MAC address. Entity IDs, history, and the logical device identity are retained. Original identity addresses remain reserved to prevent duplicate entities.

## Using Bermuda

Use Bermuda for room inference and its location calibration. This integration supplies DX telemetry and beacon health independently, without a Bermuda dependency or data bridge.

The DX presence tracker can be used alongside Bermuda's room sensors. If using Bermuda's home/away tracker too, disable the unwanted tracker through Home Assistant's entity controls and choose the intended tracker under Settings → People.

The beacon MAC and iBeacon UUID/major/minor attributes help match the same beacon between integrations. The nearest-receiver diagnostic follows Home Assistant's selection and is not a room estimate. Its display name prefers the proxy's user-assigned name, then its detected device name or adapter title. `source` retains the receiver identifier and `source_name` supplies the display name.

## Firmware and diagnostics

The supplied DX-CP35 capture uses little-endian voltage, advertising count, and uptime, with standard signed big-endian temperature. Its TLM frame `2000930c17000412000018120000` decodes to 3219 mV, 23 °C, 4,612 advertisements and 463.2 seconds of uptime. The integration uses this explicit format for DX-CP35; it never guesses byte order based on whether a number looks plausible. Select **Standard Eddystone** if your firmware follows the standard layout.

Download diagnostics from the integration entry's menu in Settings → Devices & Services. The JSON report includes settings, telemetry, frame observation times, stale fields, scanner monitoring status, parsing format and errors. MAC addresses, receiver names and beacon UUIDs are redacted; raw advertisements are omitted.

## Requirements

- Home Assistant 2026.3 or newer.
- A working Bluetooth adapter or ESPHome Bluetooth proxy.
