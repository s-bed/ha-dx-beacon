# DX-SMART Beacon for Home Assistant

Home Assistant custom integration for **DX-SMART BLE beacons** (DX-CP27 and other models with the DX-SMART standard firmware).

It reads the beacon advertisements through Home Assistant's Bluetooth stack (local adapter or ESPHome Bluetooth proxy) and exposes:

| Entity | Source | Notes |
|---|---|---|
| `device_tracker` | iBeacon / any frame | `home` / `not_home` (unavailable after ~3 min without packets) |
| Estimated distance (m) | iBeacon | Same formula as the core iBeacon Tracker, uses the beacon's RSSI@1m |
| Signal strength (dBm) | any frame | RSSI as seen by the receiving adapter/proxy |
| Battery (%) | DX-SMART device-info frame (`0xFEAB`) | |
| Battery voltage (mV) | Eddystone TLM | diagnostic |
| Chip temperature (°C) | Eddystone TLM | diagnostic – chip temperature, not ambient |
| Advertising count, uptime | Eddystone TLM | diagnostic |
| TX power @1m (dBm) | iBeacon | diagnostic |

The `source` attribute on every entity tells which adapter/proxy received the last packet – useful for room-level presence with several ESPHome proxies.

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
