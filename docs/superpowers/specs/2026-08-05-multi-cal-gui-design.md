# Multi-Device Calibration in GUI — Design Spec

**Date:** 2026-08-05

## 1. Overview

Replace the single device ID entry in the calibrate panel with multi-device cards, so 2-4 old-ADC devices can be calibrated simultaneously in one temperature sweep.

## 2. Changes

### 2.1 Calibrate Panel UI

Replace the single device ID entry:
```
设备ID: [195082]
```
With device cards:
```
设备列表:                            [+ 添加设备]
┌─────────────────────────────────────────────┐
│ #1  [195082]  (旧ADC)  [✕]                  │
│ #2  [192084]  (旧ADC)  [✕]                  │
└─────────────────────────────────────────────┘
```

- Protocol fixed as "old_adc" (no dropdown needed)
- No step interval dropdown (always all points for calibration)
- Minimum 1 device, maximum 8
- Preload from DEVICE_TABLE (old_adc entries only)

### 2.2 CalibrationThread Changes

Current: single `TtagReceiver(device_id, ...)`
New: `MultiReceiver(device_ids, ...)` from ttag_dual_verify

Data collection loop:
```
for each target temperature:
    1. Set water bath, wait for stability
    2. Wait for ADC stability (ALL devices)
    3. Record ADC for each device: (target, pv, adc_mean, adc_range, n)
    4. Write to Excel (Sheet per device)
```

### 2.3 Excel Output

```
桌面\cal_multi_{devices}_{timestamp}.xlsx
├── Sheet "195082 标定" — target, pv, adc_mean, adc_range, n, time
├── Sheet "192084 标定" — ...
└── Sheet "192080 标定" — ...
```

## 3. Files Modified

- `ttag_cal_app.py` — calibrate panel + CalibrationThread
- No changes to ttag_dual_verify.py, ttag_monitor.py, water_bath_control.py

## 4. Out of Scope

- Verifying accuracy during calibration
- New protocol devices in calibration mode
- Fitting during multi-device calibration (fit after, separately)
