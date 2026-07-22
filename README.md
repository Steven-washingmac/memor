# TTAG Wireless Temperature Tag — Calibration & Monitoring Toolkit

Complete toolchain for **TRG wireless temperature tag (TTAG)** monitoring, automated calibration with a thermostatic water bath, and MATLAB-based ADC-to-temperature polynomial fitting.

## Hardware

| Component | Model / Spec |
|-----------|-------------|
| Temperature Tag | TTAG wireless tag (NCP18XH103D03RB Murata 10KΩ NTC, B=3380K) |
| Base Station | TRG wireless base station (55-AA protocol, TCP) |
| Water Bath | Lichen (力辰) thermostatic water bath (Modbus RTU, 9600-8N1) |
| Divider Resistor | 6.2 KΩ fixed |
| ADC Resolution | 10-bit (0–1023) |

## Project Structure

```
memor/
├── ttag_monitor.py        # Real-time TTAG monitoring (TCP 55-AA protocol)
├── ttag_calibration.py    # Automated calibration pipeline (water bath + TTAG)
├── water_bath_control.py  # Water bath Modbus RTU controller (standalone)
├── ttag_fitting.py        # Python: ADC→Temperature auto-fitting (runs after calibration)
├── ttag_web.py            # Web dashboard for TTAG monitoring
├── ttag_fitting.m         # MATLAB: ADC → Temperature polynomial fitting (alternative)
├── ntc_fitting.m          # MATLAB: NTC thermistor lookup-table fitting
├── ntc_fit_6.m            # MATLAB: Alternative NTC fitting (6th order)
├── ntcb.csv               # NTC resistance-temperature lookup data
├── requirements.txt       # Python dependencies (pip install -r requirements.txt)
└── README.md
```

---

## File Descriptions

### 1. `ttag_monitor.py` — Real-Time TTAG Monitor

**Purpose:** Listens for TRG base station 55-AA protocol frames over TCP, parses wireless tag data (RSSI, type, ID, ADC), and displays real-time info.

**Key Features:**
- TCP Server mode (default): listens on `0.0.0.0:20226`, waits for base station connection
- TCP Client mode (`--connect IP:PORT`): connects to remote base station
- Multi-layer filtering: tag type, ID range, ADC validity
- Real-time terminal display with per-tag status
- CSV data export
- Interactive command shell for runtime rule management
- ADC stability detection (sliding window)

**Usage:**
```bash
python ttag_monitor.py                          # Server mode, type 0x00 only
python ttag_monitor.py --type 0x00,0x01,0x03   # Multiple tag types
python ttag_monitor.py --device 230030           # Single device tracking
python ttag_monitor.py --connect 192.168.3.188:20226  # Client mode
python ttag_monitor.py --export data.csv         # Export to CSV
```

---

### 2. `ttag_calibration.py` — Automated Calibration Pipeline

**Purpose:** Fully automated calibration workflow — controls the water bath through a temperature sweep, captures TTAG ADC readings at each point, validates stability, and outputs calibration data to Excel.

**Workflow:**
1. Sets water bath to target temperature via Modbus
2. Monitors bath PV (actual temperature) until stable within ±tolerance
3. Collects TTAG ADC values from base station
4. Waits for ADC stability (sliding window, peak-to-peak ≤ threshold)
5. Records actual temperature + ADC mean to Excel
6. Repeats for each temperature point

**Key Parameters:**
| Parameter | Default | Description |
|-----------|---------|-------------|
| `--start` / `--end` | 5.0 / 50.0 | Temperature range (°C) |
| `--step` | 0.2 | Temperature step (°C) |
| `--bath-tolerance` | 0.1 | Bath stability tolerance (°C) |
| `--stability-window` | 3.0 | ADC stability window (seconds) |
| `--stability-threshold` | 2 | ADC peak-to-peak threshold |

**Usage:**
```bash
# Small test: 5°C → 8°C
python ttag_calibration.py --device 230030 --start 5 --end 8 --step 0.2

# Full calibration: 5°C → 50°C (226 points)
python ttag_calibration.py --device 230030 --start 5 --end 50 --step 0.2

# Preview without running
python ttag_calibration.py --device 230030 --dry-run

# Skip TTAG (water bath only)
python ttag_calibration.py --device 230030 --no-ttag --start 5 --end 8 --step 0.2
```

**Output:** `calibration_data.xlsx` — contains target temp, actual temp, ADC mean, ADC range, sample count, timestamp per point.

**Dependencies:** `pip install openpyxl pyserial`

---

### 3. `water_bath_control.py` — Water Bath Modbus Controller

**Purpose:** Standalone control and monitoring of the Lichen thermostatic water bath via Modbus RTU.

**Discovered Register Map:**
| Register | Access | Scale | Description |
|----------|--------|-------|-------------|
| `0x0100` | Read | ÷100 | PV — Current actual temperature (e.g. 499 = 4.99°C) |
| `0x010A` | Write | ×10 | SV — Target setpoint temperature (e.g. 50 = 5.0°C) |
| `0x0105` | Read | raw | Heating output percentage |

**Usage:**
```bash
python water_bath_control.py              # Read current status
python water_bath_control.py 25.0         # Set target to 25.0°C
python water_bath_control.py --monitor    # Continuous monitoring
```

**Dependencies:** `pip install pyserial`

---

### 4. `ttag_fitting.py` — Python ADC→Temperature Auto-Fitting

**Purpose:** Automatically runs after calibration completes. Reads the Excel output, performs 6th- and 7th-order polynomial fitting of ADC → Temperature, picks the best fit with max error < 0.1°C, and generates outputs.

**Outputs:**
- `<excel_name>_fit.png` — dual-panel plot (fitted curve + error distribution)
- `<excel_name>_coeffs.txt` — polynomial coefficients for archival
- Console: ready-to-paste `adc_to_temperature()` Python function

**Standalone usage:**
```bash
python ttag_fitting.py calibration_data.xlsx
```

**Dependencies:** `pip install numpy matplotlib openpyxl`

---

### 5. `ttag_fitting.m` — MATLAB ADC→Temperature Fitting (Alternative)

**Purpose:** MATLAB alternative to `ttag_fitting.py`. Reads `calibration_data.xlsx`, performs polynomial fitting, and finds the optimal polynomial order.

> **Note:** You must update the file path inside the script before running on a different computer.

**Usage (MATLAB):**
```matlab
run('ttag_fitting.m')
```

---

### 6. `ntc_fitting.m` / `ntc_fit_6.m` — NTC Lookup-Table Fitting

**Purpose:** Fits the NTC thermistor's resistance-temperature curve using pre-computed lookup data (`ntcb.csv`). Used as a reference / validation against the empirical calibration.

---

### 7. `ttag_web.py` — Web Dashboard

**Purpose:** Browser-based real-time monitoring dashboard for TTAG tag data, providing a graphical alternative to the terminal-based `ttag_monitor.py`.

---

## Protocol Reference

### 55-AA Frame Structure

```
55 AA | Length | StationID | FuncCode | SN | TagCount | TagBlocks × N | Checksum
 2B   | 2B LE  |   2B LE   |    1B    | 1B |    1B    |    9B × N     |    1B
```

### Tag Block (9 bytes per tag)

| Offset | Size | Field |
|--------|------|-------|
| +0 | 1B | RSSI (signal strength) |
| +1 | 1B | Tag type |
| +2 | 3B | Tag ID (little-endian, LSB first) |
| +5 | 2B | ADC value (little-endian) |
| +7 | 2B | Reserved |

- ADC = `0xFFFF` (65535) indicates low battery
- Tag ID example: bytes `8E 82 03` = 0x03828E = 230030

---

## Calibration Pipeline Overview

```
1. [Modbus]    Set water bath target → SV register (0x010A)
2. [Modbus]    Poll PV register (0x0100) until |PV - Target| <= tolerance
3. [TCP 55AA]  Collect ADC samples from base station
4. [Stability] Verify ADC stable (sliding window, pk-pk <= threshold)
5. [Record]    Save (target_temp, actual_temp, adc_mean) to Excel
6. [Repeat]    Move to next temperature step
7. [Auto-Fit]  Run 6th/7th-order polynomial fitting (ADC → Temperature), verify < 0.1°C error
8. [Output]    Save fitting chart (*_fit.png) + coefficients (*_coeffs.txt)
```

## Quick Start

### Prerequisites

```bash
# Install Python dependencies
pip install openpyxl pyserial
```

### Terminal Launch Commands

All commands below are run from the project directory. Copy and paste directly into PowerShell or CMD:

```bash
# Clone and enter project folder
git clone https://github.com/Steven-washingmac/memor.git
cd memor
pip install -r requirements.txt
```

#### 1. Test Water Bath Connection

```bash
# Read current water bath temperature and setpoint
python water_bath_control.py

# Set water bath to 25.0°C
python water_bath_control.py 25.0

# Continuous monitoring mode (Ctrl+C to stop)
python water_bath_control.py --monitor
```

#### 2. Test TTAG Data Reception

```bash
# Server mode - listen for base station connection
python ttag_monitor.py --device 230030

# Show all tag types
python ttag_monitor.py --type 0x00,0x01,0x02,0x03,0x50 --debug

# Client mode - connect to remote base station
python ttag_monitor.py --connect 192.168.3.188:20226 --device 230030

# Export data to CSV
python ttag_monitor.py --device 230030 --export data.csv
```

#### 3. Run Automated Calibration

```bash
# Preview calibration plan without running (dry-run mode)
python ttag_calibration.py --device 230030 --dry-run

# Small test run: 5°C → 8°C (16 points, ~3-5 minutes)
python ttag_calibration.py --device 230030 --start 5 --end 8 --step 0.2

# Full calibration: 5°C → 50°C (226 points)
python ttag_calibration.py --device 230030 --start 5 --end 50 --step 0.2

# Water bath only (skip TTAG - for testing bath control)
python ttag_calibration.py --device 230030 --no-ttag --start 5 --end 8 --step 0.2

# Resume from previous run (auto-detects device, completed temps, step)
python ttag_calibration.py --resume cal_230030_0722_1530.xlsx --end 50
python ttag_calibration.py --resume cal_230030_0722_1530.csv --end 50
```

#### 4. Fitting (runs automatically after calibration)

```bash
# Auto-runs after calibration completes — no manual step needed.
# Or run standalone on any calibration Excel:
python ttag_fitting.py calibration_data.xlsx
```

#### 5. MATLAB Fitting (alternative, optional)

```matlab
% Open MATLAB, update the file path inside the script, then run:
run('ttag_fitting.m')
```

### Output Files

| File | Generated By | Description |
|------|-------------|-------------|
| `cal_*.xlsx` | `ttag_calibration.py` | Raw calibration data (auto-timestamped: `cal_{device}_{MMDD}_{HHMM}.xlsx`) |
| `cal_*.csv` | `ttag_calibration.py` | Per-point CSV backup (written immediately each point) |
| `cal_*_fit.png` | `ttag_fitting.py` (auto) | Comparison plot: fitted curve vs raw data + error distribution |
| `cal_*_coeffs.txt` | `ttag_fitting.py` (auto) | Polynomial coefficients for Python integration |
