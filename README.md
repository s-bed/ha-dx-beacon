# DX-SMART Beacon for Home Assistant

Home Assistant custom integration for **DX-SMART BLE beacons** (DX-CP27 and other models with the DX-SMART standard firmware).

It reads the beacon advertisements through Home Assistant's Bluetooth stack (local adapter or ESPHome Bluetooth proxy) and exposes:

| Entity | Source | Notes |
|---|---|---|
| `device_tracker` | iBeacon / any frame | `home` / `not_home` (unavailable after ~3 min without packets) |
| Estimated distance (m) | iBeacon | Same formula as the core iBeacon Tracker, uses the beacon's RSSI@1m |
| Signal strength (dBm) | any frame | RSSI as seen by the receiving adapter/proxy |
| Nearest receiver | Home Assistant Bluetooth | Name of the adapter/proxy selected by Home Assistant |
| Battery (%) | DX-SMART device-info frame (`0xFEAB`) | |
| Battery voltage (mV) | Eddystone TLM | diagnostic |
| Chip temperature (°C) | Eddystone TLM | diagnostic – chip temperature, not ambient |
| Advertising count, uptime | Eddystone TLM | diagnostic |
| TX power @1m (dBm) | iBeacon | diagnostic |

Each beacon has a **Nearest receiver** sensor showing the Home Assistant name of its receiving Bluetooth adapter/proxy. It prefers the device name you set in Home Assistant, then the detected device name or Bluetooth adapter title, and falls back to the source MAC if no name is available. The same name is available in the `source_name` attribute on every entity; the existing `source` attribute keeps the raw source MAC for automations.

The receiver follows Home Assistant's Bluetooth source selection, which uses signal strength and stale-advertisement handling. This is an estimate of the nearest Bluetooth receiver, not a guaranteed physical location. A Wi-Fi AP must also provide a Bluetooth scanner/proxy to appear. Receiver changes, signal strength, and name changes are refreshed from Home Assistant's cache every 10 seconds, including when the beacon payload stays the same. The Nearest receiver sensor becomes unavailable when Home Assistant marks the beacon unseen; `source` retains the last receiver MAC.

TX power, battery voltage, advertising count, and uptime are disabled by default. The presence tracker and other sensors are enabled by default. These defaults apply to newly registered entities; existing enable/disable choices are preserved. Disabled entities can be enabled in Settings → Devices & Services → Entities.

## Installation

### HACS (recommended)
1. HACS → Integrations → ⋮ → **Custom repositories** → add this repository URL, category *Integration*.
2. Install **DX-SMART Beacon**, restart Home Assistant.

### Manual
Copy `custom_components/dx_beacon` into your `config/custom_components/` folder and restart.

## Configuration
Settings → Devices & Services → the beacon is discovered automatically (Eddystone service UUID `0xFEAA`).
If not, *Add integration* → **DX-SMART Beacon** → pick the beacon's MAC address.

## Beacon setup (DX-SMART app)
- Keep at least the **iBeacon** and **TLM** frames enabled; the device-info frame provides battery %.
- Calibrate `RSSI@1m` in the iBeacon frame with the real RSSI measured at 1 m from your adapter for a meaningful distance estimate.
- The proprietary **ACC** (accelerometer) frame is not transmitted in legacy BLE advertising on the tested firmware and is not supported.

## Requirements
- Home Assistant 2026.3 or newer
- A working Bluetooth adapter or an ESPHome Bluetooth proxy
