# TTAG GUI Dual-Device Verification Mode — Design Spec

**Date:** 2026-08-03
**Status:** Approved

## 1. Overview

Add a "Verify" mode to the existing `ttag_cal_app.py` tkinter GUI, supporting simultaneous multi-device accuracy verification within the same application. The existing "Calibrate" mode remains unchanged.

## 2. Architecture

```
ttag_cal_app.py (modified, ~2500 lines)
├── Mode toggle: Calibrate | Verify (shared top bar)
├── Verify Panel (new):
│   ├── DeviceManager — dynamic add/remove device cards
│   ├── TempPointEditor — presets + text input + live preview
│   ├── VerifyControls — start/pause/stop buttons
│   └── LiveStatus — progress bar + per-device status line
├── DataTable (new, bottom, resizable):
│   ├── Per-device tabs
│   ├── Real-time row insertion (green=pass, red=fail)
│   └── Load historical Excel data
├── VerifyThread (new, threading.Thread):
│   ├── Reuses water bath stability logic from CalibrationThread
│   ├── MultiReceiver from ttag_dual_verify.py
│   ├── Per-device StabilityDetector with interval filtering
│   └── Pushes status via Queue to UI
└── Calibrate Panel (existing, unchanged) — all current calibration functionality preserved
```

## 3. Reused Modules (no changes)

- `water_bath_control.py` — WaterBath class
- `ttag_monitor.py` — frame parsing, FRAME_HEADER
- `ttag_dual_verify.py` — MultiReceiver, StabilityDetector, DEVICE_TABLE, calc_temperature, adc_to_temperature, COEFFS

## 4. UI Components

### 4.1 Mode Toggle

Two-button segmented control at top: "Calibrate (单设备)" | "Verify (多设备)". Switches which parameter panel is visible. Calibrate panel is the existing one, Verify panel is new.

### 4.2 Verify Panel — Device Cards

Dynamic list of device cards. Each card:
- Device ID entry (6-digit, validated)
- Protocol dropdown: "New (Direct Temp)" | "Old (ADC→Poly)"
- Interval dropdown: "All Points" | "Every 5°C" | "Every 10°C"
- Delete button (✕) — disabled when only 1 device remains
- "+ Add Device" button at bottom — minimum 1, maximum 6 devices

Default: loads from DEVICE_TABLE pre-configured devices.

### 4.3 Verify Panel — Temperature Points

- 5 preset buttons: Low (-20~0) | Mid (0~40) | High (40~90) | 5°C Step | 0.2°C Fine
- Text area: comma/space separated values, editable
- Live preview line: "N points | range start→end °C | ~time estimate"
- Preset buttons auto-fill text area; manual edits reflected in preview

### 4.4 Verify Panel — Parameters

Shared settings row:
- Bath tolerance: ±0.3°C (default)
- Stability samples: 5 (default)
- Stability threshold: 5 (default)
- COM port: dropdown with scan button (reuse existing)

### 4.5 Verify Panel — Controls

- ▶ Start Verify — begins the verification loop
- ⏸ Pause — pauses after current point
- ■ Stop — stops immediately, saves all collected data

### 4.6 Progress Bar

Horizontal progress bar with: percentage, current/total points, current temperature, elapsed time.

### 4.7 Live Status Line

Single-line summary: per-device latest reading + pass/fail icon + bath status.

### 4.8 Data Table (Bottom, Resizable)

- **Layout**: Vertical split with draggable divider. Top = controls (~40%), Bottom = table (~60% by default). Divider position persists.
- **Tabs**: One tab per device. Tab label shows device ID + protocol shorthand + summary stats.
- **Columns** (per protocol):
  - Old ADC: # | Target°C | Bath°C | ADC Mean | ADC Range | n | Calc°C | Error°C | Pass | Time
  - New Direct: # | Target°C | Bath°C | Raw | Raw Range | n | Tag°C | Error°C | Pass | Time
- **Row styling**: Green background = passed (within ±1°C), Red = failed
- **Auto-scroll**: Follows latest row during active verification
- **Data source**: Reads from current session's Excel file. Can also load historical Excel files via File menu.
- **Refresh**: Table updates automatically on each point write (detects Excel file changes)

## 5. VerifyThread

```
VerifyThread(threading.Thread)
├── __init__(params, status_queue)
│   params: devices list, temp points, bath_port, tolerance, samples, threshold
├── run():
│   Loop over temp points:
│   1. Set water bath SV, wait for stability (reuse CalibrationThread logic)
│   2. Determine active devices (filter by interval)
│   3. Feed per-device StabilityDetector from MultiReceiver
│   4. Wait until ALL active devices stable OR 4min timeout
│   5. If timeout with 0 data → ask user: wait or skip?
│   6. Calculate temperature per protocol
│   7. Write to Excel (one sheet per device, point-by-point save)
│   8. Push status update to Queue
│   9. If paused: wait; if stopped: save and exit
├── Pause/Resume/Stop via threading.Event flags
└── Error handling: exceptions pushed to Queue, UI shows dialog
```

## 6. Data Flow

```
WaterBath ──PV/SV──→ VerifyThread ──status──→ Queue ──poll──→ MainWindow UI
MultiReceiver ──tag data──→ VerifyThread ──results──→ Excel (point-by-point)
                                         ──results──→ DataTable (real-time)
```

## 7. Excel Output

Format: `TTAG_双测_{sorted_device_ids}.xlsx` on OneDrive desktop.
One sheet per device: `{device_id} ADC复测` or `{device_id} 温度复测`.
Same file on re-runs appends data (matched by device ID combination).

## 8. Error Handling

| Scenario | Handling |
|----------|----------|
| COM port busy | Show dialog, suggest checking port |
| Excel file locked | Show warning at startup, suggest closing Excel |
| Device no data (4min timeout) | Popup: "Device X has no data. Wait 2min more? [Yes] [Skip point]" |
| Water bath timeout (15min) | Skip point, mark all devices as "bath timeout" |
| VerifyThread exception | Catch, show traceback in dialog, save partial results |
| Window close during run | Confirm dialog, stop thread gracefully, save |

## 9. Scope Boundaries

**In scope:**
- Mode toggle + Verify panel UI
- Dynamic device cards (add/remove)
- Temp point presets + text editor
- Resizable data table with per-device tabs
- VerifyThread with multi-device stability + interval filtering
- Excel output (one file, per-device sheets)
- Pause/Resume/Stop controls
- Load historical Excel into data table

**Out of scope:**
- Modifying existing calibration mode
- Curve fitting or plotting in verify mode
- Network-based multi-PC operation
- Automated report generation (MATLAB/plot handled separately)
