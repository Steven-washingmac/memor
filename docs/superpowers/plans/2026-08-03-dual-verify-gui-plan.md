# Dual-Device Verify Mode in ttag_cal_app.py — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a multi-device verification mode to the existing tkinter GUI, with dynamic device cards, temp point presets, resizable data table, and multi-device parallel verification thread.

**Architecture:** Mode toggle switches between existing Calibrate panel and new Verify panel within same MainWindow. VerifyThread reuses water bath stability logic from CalibrationThread and MultiReceiver/StabilityDetector from ttag_dual_verify.py. Data flows from MultiReceiver → VerifyThread → Queue → UI poll, with point-by-point Excel output.

**Tech Stack:** Python tkinter (built-in), openpyxl, pyserial, ttag_monitor, ttag_dual_verify

## Global Constraints

- Existing calibration mode must remain fully functional and unchanged
- ttag_dual_verify.py, ttag_monitor.py, water_bath_control.py are NOT modified
- Window: 1100x750 minimum, resizable
- Defaults: bath_tolerance=0.3, samples=5, threshold=5
- Excel: `TTAG_双测_{sorted_ids}.xlsx` on OneDrive desktop, per-device sheets

---

### Task 1: Add the `PanedWindow` split layout and data table frame

**Files:**
- Modify: `ttag_cal_app.py:_build_ui()` (line ~1031)

**Interfaces:**
- Consumes: existing `_build_ui()`, `_build_settings()`, `_build_controls()` methods
- Produces: `self.paned` (tk.PanedWindow, vertical), `self.top_frame`, `self.bottom_frame`, `self.data_table_notebook` (ttk.Notebook)

- [ ] **Step 1: Wrap existing content in PanedWindow**

In `_build_ui()`, replace the flat `main_frame` layout with a vertical `tk.PanedWindow` (sash orientation vertical). The top frame holds existing settings + controls + status. The bottom frame holds the data table.

```python
def _build_ui(self):
    # Vertical split: controls | data table
    self.paned = tk.PanedWindow(self, orient=tk.VERTICAL, sashrelief=tk.RAISED, sashwidth=6)
    self.paned.pack(fill='both', expand=True)

    self.top_frame = ttk.Frame(self.paned, padding=8)
    self.bottom_frame = ttk.Frame(self.paned)

    self.paned.add(self.top_frame, minsize=200, stretch='always')
    self.paned.add(self.bottom_frame, minsize=100, stretch='always')

    main_frame = self.top_frame  # alias for existing code to work

    # Existing build methods operate on main_frame
    self._build_settings(main_frame)
    self._build_controls(main_frame)
    # ... rest of existing code

    # Build data table in bottom frame
    self._build_data_table()
```

- [ ] **Step 2: Create `_build_data_table()` method**

```python
def _build_data_table(self):
    """Bottom panel: tabbed data table for verification results"""
    table_label = ttk.Label(self.bottom_frame, text='📊 Data Table', font=('', 10, 'bold'))
    table_label.pack(anchor='w', padx=4, pady=(4, 0))

    self.data_notebook = ttk.Notebook(self.bottom_frame)
    self.data_notebook.pack(fill='both', expand=True, padx=4, pady=4)

    # Placeholder tab
    placeholder = ttk.Frame(self.data_notebook)
    self.data_notebook.add(placeholder, text='No data')
    ttk.Label(placeholder, text='Start verification to see data here',
              foreground='gray').pack(expand=True)
```

- [ ] **Step 3: Verify existing calibration mode still works**

Run: `python ttag_cal_app.py` — window opens, calibration UI unchanged, paned split visible with empty bottom panel.

- [ ] **Step 4: Commit**

```bash
git add ttag_cal_app.py
git commit -m "feat: add PanedWindow split layout with data table placeholder"
```

---

### Task 2: Add mode toggle between Calibrate and Verify

**Files:**
- Modify: `ttag_cal_app.py:_build_settings()` and `_build_ui()` (lines ~1055, ~1031)

**Interfaces:**
- Consumes: existing `_build_settings()`
- Produces: `self.mode_var` (tk.StringVar, 'calibrate'|'verify'), `_on_mode_change()` callback

- [ ] **Step 1: Add mode toggle variable and frame-switching logic**

In `__init__`, add `self.mode_var = tk.StringVar(value='calibrate')`. Create a mode toggle bar.

```python
# In __init__:
self.mode_var = tk.StringVar(value='calibrate')

# In _build_ui, before _build_settings:
mode_bar = ttk.Frame(main_frame)
mode_bar.pack(fill='x', pady=(0, 8))

ttk.Label(mode_bar, text='Mode:', font=('', 10)).pack(side='left', padx=(0, 10))

cal_rb = ttk.Radiobutton(mode_bar, text='Calibrate (Single)', variable=self.mode_var,
                          value='calibrate', command=self._on_mode_change)
cal_rb.pack(side='left', padx=5)

verify_rb = ttk.Radiobutton(mode_bar, text='Verify (Multi-Device)', variable=self.mode_var,
                             value='verify', command=self._on_mode_change)
verify_rb.pack(side='left', padx=5)
```

- [ ] **Step 2: Create mode-specific frames**

```python
def _build_settings(self, parent):
    # Container for calibrate-specific settings
    self.cal_frame = ttk.LabelFrame(parent, text='Connection & Parameters', padding=8)
    # ... existing settings go into self.cal_frame (rename from 'frame')

    # Container for verify-specific settings (hidden initially)
    self.verify_frame = ttk.LabelFrame(parent, text='Verify — Devices & Points', padding=8)

def _on_mode_change(self):
    mode = self.mode_var.get()
    if mode == 'calibrate':
        self.cal_frame.pack(fill='x', before=self.ctrl_frame)
        self.verify_frame.pack_forget()
        self.data_notebook.tab(0, text='No data')
    else:
        self.cal_frame.pack_forget()
        self.verify_frame.pack(fill='x', before=self.ctrl_frame)
```

- [ ] **Step 3: Verify toggle works**

Run the app, toggle modes — calibrate settings show/hide, verify frame shows/hides.

- [ ] **Step 4: Commit**

```bash
git add ttag_cal_app.py
git commit -m "feat: add mode toggle (Calibrate/Verify) with frame switching"
```

---

### Task 3: Build Verify panel — device cards

**Files:**
- Modify: `ttag_cal_app.py` — add `_build_verify_panel()` method

**Interfaces:**
- Consumes: `self.verify_frame` from Task 2
- Produces: `self.device_rows` (list of dicts with tk vars), `_add_device_row()`, `_remove_device_row()`

- [ ] **Step 1: Import DEVICE_TABLE from ttag_dual_verify**

At top of file:
```python
try:
    from ttag_dual_verify import DEVICE_TABLE, COEFFS
except ImportError:
    DEVICE_TABLE = {}
    COEFFS = []
```

- [ ] **Step 2: Build device list UI**

```python
def _build_verify_panel(self):
    """Verify mode: device cards + temp points"""
    parent = self.verify_frame

    # Device list header
    hdr = ttk.Frame(parent)
    hdr.pack(fill='x')
    ttk.Label(hdr, text='Devices:', font=('', 10, 'bold')).pack(side='left')
    ttk.Button(hdr, text='+ Add Device', command=self._add_device_row).pack(side='right')

    # Scrollable device list
    self.device_canvas = tk.Canvas(parent, height=120, highlightthickness=0)
    scrollbar = ttk.Scrollbar(parent, orient='vertical', command=self.device_canvas.yview)
    self.device_inner = ttk.Frame(self.device_canvas)
    self.device_inner.bind('<Configure>',
        lambda e: self.device_canvas.configure(scrollregion=self.device_canvas.bbox('all')))
    self.device_canvas.create_window((0, 0), window=self.device_inner, anchor='nw')
    self.device_canvas.configure(yscrollcommand=scrollbar.set)

    self.device_canvas.pack(fill='x', side='left', expand=True)
    scrollbar.pack(side='right', fill='y')

    self.device_rows = []
    # Preload DEVICE_TABLE entries
    for did, cfg in sorted(DEVICE_TABLE.items()):
        self._add_device_row(did, cfg['protocol'], cfg.get('step', 0))
```

- [ ] **Step 3: Implement add/remove device row**

```python
def _add_device_row(self, did='', proto='new_direct', step=0):
    row_frame = ttk.Frame(self.device_inner)
    row_frame.pack(fill='x', pady=2)

    id_var = tk.StringVar(value=str(did))
    proto_var = tk.StringVar(value=proto)
    step_var = tk.StringVar(value='0' if step == 0 else str(step))

    ttk.Label(row_frame, text=f'#{len(self.device_rows) + 1}', width=3).pack(side='left')
    ttk.Entry(row_frame, textvariable=id_var, width=8).pack(side='left', padx=3)
    ttk.Combobox(row_frame, textvariable=proto_var, values=['new_direct', 'old_adc'],
                 width=12, state='readonly').pack(side='left', padx=3)
    ttk.Label(row_frame, text='Every:').pack(side='left')
    ttk.Combobox(row_frame, textvariable=step_var, values=['0', '5', '10'],
                 width=6).pack(side='left', padx=3)
    ttk.Label(row_frame, text='°C').pack(side='left')

    def remove():
        row_frame.destroy()
        self.device_rows.remove(row_data)
        self._renumber_devices()

    ttk.Button(row_frame, text='✕', width=2, command=remove).pack(side='right', padx=3)

    row_data = {'frame': row_frame, 'id_var': id_var,
                'proto_var': proto_var, 'step_var': step_var}
    self.device_rows.append(row_data)

def _renumber_devices(self):
    for i, row in enumerate(self.device_rows, 1):
        for child in row['frame'].winfo_children():
            if isinstance(child, ttk.Label) and child.cget('text').startswith('#'):
                child.config(text=f'#{i}')
                break
```

- [ ] **Step 4: Verify cards work**

Run app, switch to verify mode. Device cards from DEVICE_TABLE appear, add/remove works.

- [ ] **Step 5: Commit**

```bash
git add ttag_cal_app.py
git commit -m "feat: add verify panel with dynamic device cards"
```

---

### Task 4: Build Verify panel — temperature points editor

**Files:**
- Modify: `ttag_cal_app.py` — extend `_build_verify_panel()`

**Interfaces:**
- Consumes: `self.verify_frame` from Task 2
- Produces: `self.temp_text` (tk.Text), `self.temp_preview_var` (tk.StringVar), `_preset_temps()`, `_get_temp_points()`

- [ ] **Step 1: Add preset buttons and text editor**

```python
# In _build_verify_panel, after device section:

# Preset buttons
preset_frame = ttk.Frame(parent)
preset_frame.pack(fill='x', pady=(8, 4))
presets = [
    ('Low (-20~0)', [-20, -15, -10, -5, 0]),
    ('Mid (0~40)', [0, 5, 10, 15, 20, 25, 30, 35, 40]),
    ('High (40~90)', [40, 50, 60, 70, 80, 90]),
    ('5°C Step', list(range(5, 91, 5))),
    ('0.2°C Fine', None),  # uses start/end/step fields
]
for label, temps in presets:
    btn = ttk.Button(preset_frame, text=label,
                     command=lambda t=temps, l=label: self._preset_temps(t, l))
    btn.pack(side='left', padx=2)

# Temp text area
ttk.Label(parent, text='Temperature points (comma/space separated):').pack(anchor='w')
self.temp_text = tk.Text(parent, height=3, width=80, font=('Consolas', 10))
self.temp_text.pack(fill='x')
self.temp_text.bind('<KeyRelease>', lambda e: self._update_temp_preview())

# Preview line
self.temp_preview_var = tk.StringVar(value='0 points')
ttk.Label(parent, textvariable=self.temp_preview_var, foreground='#666').pack(anchor='w')
```

- [ ] **Step 2: Implement presets and preview**

```python
def _preset_temps(self, temps, label):
    if temps is None:
        return  # '0.2°C Fine' handled via start/end/step fields in future task
    text = ', '.join(str(t) for t in temps)
    self.temp_text.delete('1.0', 'end')
    self.temp_text.insert('1.0', text)
    self._update_temp_preview()

def _update_temp_preview(self):
    raw = self.temp_text.get('1.0', 'end').strip()
    parts = raw.replace(',', ' ').split()
    try:
        temps = [float(p) for p in parts]
    except ValueError:
        self.temp_preview_var.set('Invalid: non-numeric values')
        return
    if not temps:
        self.temp_preview_var.set('0 points')
        return
    temps.sort()
    self.temp_preview_var.set(
        f'{len(temps)} points | {temps[0]:.1f} → {temps[-1]:.1f} °C'
    )
```

- [ ] **Step 3: Verify presets work**

Run app, click preset buttons — text area fills, preview updates.

- [ ] **Step 4: Commit**

```bash
git add ttag_cal_app.py
git commit -m "feat: add temperature point editor with presets and live preview"
```

---

### Task 5: Build Verify panel — controls and status

**Files:**
- Modify: `ttag_cal_app.py` — extend `_build_verify_panel()`, modify control buttons

**Interfaces:**
- Consumes: existing `_build_controls()` (line ~1128)
- Produces: `_start_verify()`, `_pause_verify()`, `_stop_verify()`, mode-aware button bindings

- [ ] **Step 1: Add verify-specific controls**

```python
# In _build_verify_panel:
param_row = ttk.Frame(parent)
param_row.pack(fill='x', pady=4)

# Bath tolerance
ttk.Label(param_row, text='Tol ±:').pack(side='left')
self.verify_tol_var = tk.StringVar(value='0.3')
ttk.Entry(param_row, textvariable=self.verify_tol_var, width=5).pack(side='left', padx=(2,10))

# Samples
ttk.Label(param_row, text='Samples:').pack(side='left')
self.verify_samples_var = tk.StringVar(value='5')
ttk.Entry(param_row, textvariable=self.verify_samples_var, width=4).pack(side='left', padx=(2,10))

# Threshold
ttk.Label(param_row, text='Threshold:').pack(side='left')
self.verify_thresh_var = tk.StringVar(value='5')
ttk.Entry(param_row, textvariable=self.verify_thresh_var, width=4).pack(side='left', padx=2)
```

- [ ] **Step 2: Update start button to be mode-aware**

Replace existing `_start_cal` binding with mode dispatch:

```python
def _start(self):
    if self.mode_var.get() == 'verify':
        self._start_verify()
    else:
        self._start_cal()

def _start_verify(self):
    devices = []
    for row in self.device_rows:
        try:
            did = int(row['id_var'].get())
        except ValueError:
            continue
        proto = row['proto_var'].get()
        step = int(row['step_var'].get())
        devices.append((did, proto, step))
    if not devices:
        messagebox.showwarning('No Devices', 'Add at least one device.')
        return
    temps_raw = self.temp_text.get('1.0', 'end').strip()
    parts = temps_raw.replace(',', ' ').split()
    try:
        temps = sorted([float(p) for p in parts])
    except ValueError:
        messagebox.showwarning('Invalid Input', 'Temperature points contain non-numeric values.')
        return
    if not temps:
        messagebox.showwarning('No Points', 'Enter temperature points.')
        return

    params = {
        'devices': devices,
        'temps': temps,
        'bath_port': self.com_port_var.get(),
        'bath_tolerance': float(self.verify_tol_var.get()),
        'stability_samples': int(self.verify_samples_var.get()),
        'stability_threshold': int(self.verify_thresh_var.get()),
        'port': int(self.ttag_port_var.get()),
        'connect_to': self.client_host_var.get() if self.conn_mode_var.get() == 'client' else None,
    }
    self.verify_thread = VerifyThread(params, self.status_queue)
    self.verify_thread.start()
    self.start_btn.config(state='disabled')
    self.pause_btn.config(state='normal')
    self.stop_btn.config(state='normal')
```

- [ ] **Step 3: Wire pause/stop for verify mode**

```python
def _pause(self):
    if self.mode_var.get() == 'verify' and self.verify_thread:
        if self.verify_thread.paused.is_set():
            self.verify_thread.paused.clear()
            self.pause_btn.config(text='⏸ Pause')
        else:
            self.verify_thread.paused.set()
            self.pause_btn.config(text='▶ Resume')
    else:
        self._pause_cal()

def _stop(self):
    if self.mode_var.get() == 'verify' and self.verify_thread:
        self.verify_thread.stopped.set()
        self.verify_thread.paused.clear()
    else:
        self._stop_cal()
```

- [ ] **Step 4: Commit**

```bash
git add ttag_cal_app.py
git commit -m "feat: add verify controls and mode-aware start/pause/stop"
```

---

### Task 6: Create VerifyThread class

**Files:**
- Modify: `ttag_cal_app.py` — add VerifyThread class (before MainWindow)

**Interfaces:**
- Consumes: MultiReceiver, StabilityDetector from `ttag_dual_verify`
- Produces: `VerifyThread(threading.Thread)` with `run()`, `paused`, `stopped` events

- [ ] **Step 1: Add imports at top**

```python
try:
    from ttag_dual_verify import (MultiReceiver, StabilityDetector,
                                    calc_temperature, adc_to_temperature, COEFFS)
except ImportError:
    MultiReceiver = None
```

- [ ] **Step 2: Write VerifyThread class**

```python
class VerifyThread(threading.Thread):
    """Background thread for multi-device verification"""

    def __init__(self, params, status_queue):
        super().__init__(daemon=True)
        self.params = params
        self.q = status_queue
        self.paused = threading.Event()
        self.paused.clear()
        self.stopped = threading.Event()
        self.records = {}  # did -> list of result dicts

    def _push(self, msg_type, data):
        try:
            self.q.put_nowait({'type': msg_type, **data})
        except queue.Full:
            pass

    def run(self):
        params = self.params
        devices = params['devices']
        device_ids = [d[0] for d in devices]
        device_info = {d[0]: {'protocol': d[1], 'step': d[2]} for d in devices}

        try:
            # Connect water bath
            self._push('log', {'text': 'Connecting water bath...'})
            wb = WaterBath(port=params['bath_port'])
            pv = wb.get_temperature()
            self._push('log', {'text': f'Water bath OK: PV={pv:.2f}°C' if pv else 'Water bath connected'})

            # Connect base station
            receiver = MultiReceiver(device_ids, port=params['port'],
                                     connect_to=params.get('connect_to'))
            receiver.start()
            self._push('log', {'text': 'Waiting for base station...'})
            found = set()
            for _ in range(40):
                time.sleep(0.5)
                if self.stopped.is_set():
                    return
                for did in device_ids:
                    if receiver.get_state(did).get('hits', 0) > 0:
                        found.add(did)
                if len(found) >= len(device_ids):
                    break
            self._push('log', {'text': f'Devices found: {len(found)}/{len(device_ids)}'})

            # Excel setup
            onedrive = os.path.join(os.path.expanduser('~'), 'OneDrive', '桌面')
            if not os.path.isdir(onedrive):
                onedrive = os.path.join(os.path.expanduser('~'), 'Desktop')
            dev_names = '_'.join(str(d) for d in sorted(device_ids))
            xlsx_path = os.path.join(onedrive, f'TTAG_双测_{dev_names}.xlsx')
            os.makedirs(os.path.dirname(xlsx_path), exist_ok=True)
            self._push('log', {'text': f'Excel: {os.path.basename(xlsx_path)}'})

            temps = params['temps']
            total = len(temps)
            t0 = time.time()
            all_results = {did: [] for did in device_ids}

            for i, target in enumerate(temps):
                if self.stopped.is_set():
                    break
                while self.paused.is_set():
                    time.sleep(0.5)
                    if self.stopped.is_set():
                        break
                if self.stopped.is_set():
                    break

                # Determine active devices
                active = []
                for did, proto, step in devices:
                    if step == 0 or abs(target % step) < 0.01 or abs(target % step - step) < 0.01:
                        active.append((did, proto))

                # Set water bath
                wb.set_temperature(target)

                # Wait for bath stability (simplified — reuse CalibrationThread logic)
                t1 = time.time()
                bath_ok = False
                while time.time() - t1 < 900:
                    if self.stopped.is_set():
                        break
                    pv = wb.get_temperature()
                    pwr = wb.get_status()
                    if pv is not None and abs(pv - target) <= params['bath_tolerance']:
                        time.sleep(2)
                        pv2 = wb.get_temperature()
                        if pv2 is not None and abs(pv2 - pv) < 0.15 and abs(pv2 - target) <= params['bath_tolerance']:
                            bath_ok = True
                            break
                    self._push('status', {
                        'phase': 'bath', 'target': target, 'pv': pv, 'pwr': pwr,
                        'done': i, 'total': total, 'elapsed': time.time() - t0,
                        'disp_i': i + 1,
                    })
                    time.sleep(0.5)

                if not bath_ok:
                    self._push('log', {'text': f'Bath timeout at {target}°C, skipping'})
                    continue

                # Data collection for active devices
                detectors = {did: StabilityDetector(
                    params['stability_samples'], params['stability_threshold']
                ) for did, _ in active}
                last_hits = {did: receiver.get_state(did).get('hits', 0) for did, _ in active}

                t2 = time.time()
                while time.time() - t2 < 240:
                    if self.stopped.is_set():
                        break
                    for did, _ in active:
                        st = receiver.get_state(did)
                        cur = st.get('hits', 0)
                        v = st.get('adc')
                        if v is not None and cur != last_hits[did]:
                            detectors[did].feed(v)
                            last_hits[did] = cur
                    all_stable = all(detectors[did].check()[0] for did, _ in active)
                    if all_stable:
                        break
                    self._push('status', {
                        'phase': 'adc', 'target': target,
                        'done': i, 'total': total, 'elapsed': time.time() - t0,
                        'disp_i': i + 1, 'pv': wb.get_temperature(),
                        'devices': {did: detectors[did].check() for did, _ in active},
                    })
                    time.sleep(0.3)

                # Calculate and record
                for did, proto in active:
                    _, mean, rng, n, _ = detectors[did].check()
                    st = receiver.get_state(did)
                    adc_mean = mean if mean is not None else st.get('adc')
                    tag_temp = st.get('temperature')
                    t_calc = calc_temperature(proto, adc_mean, tag_temp)
                    pv_now = wb.get_temperature()
                    error = t_calc - pv_now if (t_calc is not None and pv_now is not None) else None
                    passed = abs(error) <= 1.0 if error is not None else False
                    result = {
                        'target': target, 'pv': pv_now,
                        'adc_mean': adc_mean, 'adc_range': rng, 'adc_n': n,
                        't_calc': t_calc, 'error': error, 'passed': passed,
                        'time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                    }
                    all_results[did].append(result)
                    self._push('result', {'did': did, **result})

                # Write Excel (inlined for brevity — same logic as ttag_dual_verify.py)
                self._save_excel(xlsx_path, devices, all_results)

            self._push('complete', {'results': all_results, 'xlsx': xlsx_path})

        except Exception as e:
            self._push('error', {'text': str(e)})
            import traceback
            traceback.print_exc()
```

- [ ] **Step 3: Add `_save_excel` helper**

```python
def _save_excel(self, path, devices, all_results):
    from openpyxl import Workbook, load_workbook
    from openpyxl.styles import Font, Alignment, Border, Side, PatternFill

    thin = Border(left=Side(style='thin'), right=Side(style='thin'),
                   top=Side(style='thin'), bottom=Side(style='thin'))
    hdr_fill = PatternFill(start_color='4472C4', end_color='4472C4', fill_type='solid')
    ok_fill = PatternFill(start_color='C6EFCE', end_color='C6EFCE', fill_type='solid')
    ng_fill = PatternFill(start_color='FFC7CE', end_color='FFC7CE', fill_type='solid')

    if os.path.exists(path):
        wb = load_workbook(path)
    else:
        wb = Workbook()
        wb.remove(wb.active)

    for did, proto, _ in devices:
        sname = f'{did} {"ADC复测" if proto == "old_adc" else "温度复测"}'
        if sname not in wb.sheetnames:
            ws = wb.create_sheet(sname)
            ws.merge_cells('A1:J1')
            ws['A1'] = f'TTAG {did} Verify Results'
            ws['A1'].font = Font(bold=True, size=14)
            ws.merge_cells('A2:J2')
            ws['A2'] = f'±1.0°C  |  {"ADC→Poly" if proto == "old_adc" else "Direct Temp"}'
            hdrs = ['#','Target°C','Bath°C','Raw','Δ','n','Calc°C','Error°C','Pass','Time']
            for ci, h in enumerate(hdrs, 1):
                c = ws.cell(row=4, column=ci, value=h)
                c.font = Font(bold=True, size=11, color='FFFFFF')
                c.fill = hdr_fill
                c.alignment = Alignment(horizontal='center')
                c.border = thin
            for ci, w in enumerate([6,12,14,10,12,8,14,10,12,20], 1):
                ws.column_dimensions[chr(64+ci)].width = w

        ws = wb[sname]
        results = all_results.get(did, [])
        for j, r in enumerate(results):
            ri = ws.max_row + 1 if ws.max_row > 4 else 5
            vals = [len(results) if j == len(results)-1 else j+1,
                    r['target'], r['pv'] if r['pv'] else '',
                    r['adc_mean'] if r['adc_mean'] else '',
                    r['adc_range'] if r['adc_range'] else '',
                    r['adc_n'],
                    round(r['t_calc'],2) if r['t_calc'] else '',
                    round(r['error'],2) if r['error'] else '',
                    'YES' if r['passed'] else 'NO',
                    r['time']]
            for ci, v in enumerate(vals, 1):
                c = ws.cell(row=ri, column=ci, value=v)
                c.alignment = Alignment(horizontal='center')
                c.border = thin
                c.fill = ok_fill if r['passed'] else ng_fill

    wb.save(path)
```

- [ ] **Step 4: Verify thread starts and pushes status**

Run: `python ttag_cal_app.py`, switch to verify, add devices, set temps, click start. Thread starts, status appears in log.

- [ ] **Step 5: Commit**

```bash
git add ttag_cal_app.py
git commit -m "feat: add VerifyThread with multi-device stability, Excel output"
```

---

### Task 7: Update _poll_status for verify mode

**Files:**
- Modify: `ttag_cal_app.py:_poll_status()` and `_update_status()` (lines ~1645-1699)

**Interfaces:**
- Consumes: `self.status_queue`, existing `_update_status()` pattern
- Produces: multi-device status display, result table updates

- [ ] **Step 1: Add verify-specific status handling**

```python
def _poll_status(self):
    while True:
        try:
            msg = self.status_queue.get_nowait()
        except queue.Empty:
            break

        mtype = msg.get('type', 'status')
        if mtype == 'log':
            self._set_status(msg.get('text', ''))
        elif mtype == 'error':
            messagebox.showerror('Error', msg.get('text', ''))
            self._set_status(f'ERROR: {msg.get("text", "")}')
        elif mtype == 'status':
            self._update_verify_status(msg)
        elif mtype == 'result':
            self._add_table_row(msg)
        elif mtype == 'complete':
            self._on_verify_complete(msg)
        elif mtype == 'cal_status':
            self._update_status(msg)  # existing calibration handler

    self.after(200, self._poll_status)
```

- [ ] **Step 2: Implement verify status display**

```python
def _update_verify_status(self, s):
    done, total = s['done'], s['total']
    if total > 0:
        self.progress_var.set(done * 100 / total)
        self.progress_label.config(text=f'Point {s["disp_i"]}/{total}')
    elapsed = s.get('elapsed', 0)
    eta = (elapsed / max(done, 1) * (total - done)) if done > 0 else 0
    self.time_label.config(text=f'{elapsed/60:.0f}min | ETA {eta/60:.0f}min')

    lines = []
    pv = s.get('pv')
    target = s.get('target')
    lines.append(f'Bath: PV={pv:.4f}°C  Target={target}°C' if pv else f'Bath: Target={target}°C')

    devices = s.get('devices', {})
    for did, (stable, mean, rng, n, _) in devices.items():
        st_str = '● STABLE' if stable else '○ collecting'
        lines.append(f'  {did}: μ={mean or 0:.1f} Δ={rng} n={n} {st_str}')

    self._set_status('\n'.join(lines))
```

- [ ] **Step 3: Implement table row insertion**

```python
def _add_table_row(self, r):
    did = r['did']
    # Find or create tab for this device
    tab_name = str(did)
    existing_tabs = [self.data_notebook.tab(i, 'text') for i in range(self.data_notebook.tabs())]
    if tab_name not in existing_tabs:
        if 'No data' in existing_tabs:
            self.data_notebook.forget(0)  # remove placeholder
        frame = ttk.Frame(self.data_notebook)
        self.data_notebook.add(frame, text=tab_name)
        # Create Treeview
        tree = ttk.Treeview(frame, columns=('target','bath','raw','calc','error','pass_'),
                            show='headings', height=12)
        tree.heading('target', text='Target°C')
        tree.heading('bath', text='Bath°C')
        tree.heading('raw', text='Raw')
        tree.heading('calc', text='Calc°C')
        tree.heading('error', text='Error')
        tree.heading('pass_', text='Pass')
        tree.column('target', width=70); tree.column('bath', width=70)
        tree.column('raw', width=60); tree.column('calc', width=70)
        tree.column('error', width=60); tree.column('pass_', width=50)
        scroll = ttk.Scrollbar(frame, orient='vertical', command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)
        tree.pack(side='left', fill='both', expand=True)
        scroll.pack(side='right', fill='y')
        self._table_trees[did] = tree
        # Store reference
        if not hasattr(self, '_table_trees'):
            self._table_trees = {}

    tree = self._table_trees.get(did)
    if tree:
        icon = '✅' if r['passed'] else '❌'
        err_str = f'{r["error"]:+.2f}' if r['error'] is not None else '--'
        tree.insert('', 'end', values=(
            r['target'], f'{r["pv"]:.2f}' if r['pv'] else '--',
            r['adc_mean'] if r['adc_mean'] else '--',
            f'{r["t_calc"]:.2f}' if r['t_calc'] else '--',
            err_str, icon
        ))
        tree.yview_moveto(1)  # auto-scroll to latest
```

- [ ] **Step 4: Verify UI updates work**

Run a full verification — progress bar moves, status lines update, table rows appear in real-time.

- [ ] **Step 5: Commit**

```bash
git add ttag_cal_app.py
git commit -m "feat: add verify status display and real-time data table updates"
```

---

### Task 8: Load historical Excel into data table

**Files:**
- Modify: `ttag_cal_app.py` — add `_load_excel_data()` method

**Interfaces:**
- Consumes: existing data table tabs from Task 7
- Produces: `_load_historical_data()` bound to a button or File menu

- [ ] **Step 1: Add "Load Data" button to bottom panel**

```python
# In _build_data_table():
btn_row = ttk.Frame(self.bottom_frame)
btn_row.pack(fill='x', padx=4)
ttk.Button(btn_row, text='Load Excel...', command=self._load_excel_data).pack(side='left')
self._table_trees = {}
```

- [ ] **Step 2: Implement Excel loading**

```python
def _load_excel_data(self, path=None):
    if path is None:
        from tkinter import filedialog
        path = filedialog.askopenfilename(
            title='Load Verify Excel',
            filetypes=[('Excel files', '*.xlsx'), ('All files', '*.*')]
        )
    if not path:
        return
    from openpyxl import load_workbook
    wb = load_workbook(path)
    for sname in wb.sheetnames:
        if '复测' not in sname and 'Verify' not in sname:
            continue
        ws = wb[sname]
        # Create tab
        if sname not in [self.data_notebook.tab(i, 'text') for i in range(self.data_notebook.tabs())]:
            frame = ttk.Frame(self.data_notebook)
            self.data_notebook.add(frame, text=sname)
            tree = ttk.Treeview(frame, columns=('target','bath','raw','calc','error','pass_'),
                                show='headings', height=12)
            tree.heading('target', text='Target°C'); tree.heading('bath', text='Bath°C')
            tree.heading('raw', text='Raw'); tree.heading('calc', text='Calc°C')
            tree.heading('error', text='Error'); tree.heading('pass_', text='Pass')
            for col, w in [('target',70),('bath',70),('raw',60),('calc',70),('error',60),('pass_',50)]:
                tree.column(col, width=w)
            scroll = ttk.Scrollbar(frame, orient='vertical', command=tree.yview)
            tree.configure(yscrollcommand=scroll.set)
            tree.pack(side='left', fill='both', expand=True)
            scroll.pack(side='right', fill='y')
        else:
            # Find existing tree in this tab
            for idx in range(self.data_notebook.tabs()):
                if self.data_notebook.tab(idx, 'text') == sname:
                    frame = self.data_notebook.winfo_children()[idx]
                    tree = frame.winfo_children()[0]
                    # Clear existing
                    for item in tree.get_children():
                        tree.delete(item)
                    break
            else:
                continue

        for row in ws.iter_rows(min_row=5, values_only=True):
            if row[1] is None:
                continue
            passed = row[8] == 'YES' if row[8] else False
            tree.insert('', 'end', values=(
                row[1], f'{row[2]:.2f}' if row[2] else '--',
                row[3] if row[3] else '--',
                f'{row[6]:.2f}' if row[6] else '--',
                f'{row[7]:+.2f}' if row[7] else '--',
                '✅' if passed else '❌'
            ))
```

- [ ] **Step 3: Verify loading works**

Run app, click "Load Excel", select an existing verify Excel — tabs populate with historical data.

- [ ] **Step 4: Commit**

```bash
git add ttag_cal_app.py
git commit -m "feat: add historical Excel data loading into table tabs"
```

---

### Task 9: Integration test and hardening

**Files:**
- Modify: `ttag_cal_app.py`

- [ ] **Step 1: Test mode switching preserves state**

Switch calibrate → verify → calibrate. Verify that calibrate settings are preserved and functional.

- [ ] **Step 2: Test calibration mode still works end-to-end**

Run a single-point calibration — water bath sets, data collects, fit updates.

- [ ] **Step 3: Test verify mode with dummy data**

If no hardware: add `--demo` flag that feeds simulated data to VerifyThread.

- [ ] **Step 4: Handle edge cases**

- Window close during verify: `_on_close` checks `self.verify_thread and self.verify_thread.is_alive()`, asks for confirmation
- Empty device list: start button disabled with 0 devices
- Invalid device ID: red outline on entry, skip in _start_verify

- [ ] **Step 5: Commit**

```bash
git add ttag_cal_app.py
git commit -m "feat: add mode switching safety, close confirmation, input validation"
```
