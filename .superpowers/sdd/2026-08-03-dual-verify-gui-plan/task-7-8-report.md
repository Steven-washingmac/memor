# Tasks 7+8 Report: Status Display + Data Table Live Updates + Historical Excel Loading

## Status: Complete

## Changes Made

All changes are in `C:\Users\王应浩\OneDrive\桌面\git\ttag_cal_app.py`.

### Part A: Updated `_handle_msg` routing

Modified the message dispatcher to distinguish verify-mode messages from calibration-mode messages:

- `status` messages are routed to `_update_verify_status()` when `self.verify_thread` is alive, otherwise to `_update_status()` (calibration).
- `result` messages now call `_add_table_row()` for live table updates (replaces the old brief-log-only behavior).
- `complete` messages now call `_on_verify_complete()` (separated from `done` which remains `_on_done` for calibration).
- `log` and `error` handling is unchanged (already handles both thread formats).

### Part B: `_update_verify_status()`

New method that updates the progress bar, time label, and status text area for verify mode:

- Shows progress as `Point X/N` with ETA calculation.
- Displays bath PV and target temperature.
- Shows per-device stability status (mean ADC, range, sample count, STABLE/collecting).

### Part C: `_add_table_row()`

New method that appends live result rows to per-device Treeview tabs in `self.data_notebook`:

- Creates a new tab per device DID (removes "No data" placeholder on first result).
- Inserts rows with columns: Target C, Bath C, Raw, Calc C, Error, Pass.
- Auto-scrolls to the latest row with `yview_moveto(1)`.
- Manages tabs via `self._table_trees` dict.

### Part D: `_on_verify_complete()`

New method that handles verify completion:

- Resets start/pause/stop button states.
- Shows the Excel path in the status area.
- Displays a messagebox with the total data point count and device count.

### Part E: Load Excel button in `_build_data_table()`

Added a button row above the data notebook with a "Load Excel..." button wired to `_load_excel_data()`.

### Part F: `_load_excel_data()`

New method that loads historical verify Excel files:

- Opens a file dialog for `.xlsx` files.
- Iterates each sheet (one per device) using openpyxl.
- Creates Treeview tabs if needed, or clears and repopulates existing ones.
- Reads from row 5 (skipping header rows), using the `row[8] == 'YES'` convention for pass/fail.

## Verification

```
python -c "import py_compile; py_compile.compile('ttag_cal_app.py', doraise=True); print('OK')"
```
Result: **OK** -- compiles without errors.

## Concerns

1. **`_on_close` does not stop verify thread**: The existing `_on_close` only handles `self.cal_thread`. If the user closes the window while a verify is running, the VerifyThread daemon will be terminated by the OS on process exit, but `water_bath_control.WaterBath.close()` won't be called, potentially leaving the water bath in its last-set state. This is a pre-existing gap, not introduced by Tasks 7+8.

2. **`_load_excel_data` assumes sheet naming convention**: The method splits the sheet name by spaces and uses the first token as the device ID (`did = sname.split()[0]`). This matches the VerifyThread's `_save_excel` naming convention (`f'{did} {"ADC Verify" if ... else "Temp Verify"}'`). If sheets are named differently, device ID tabs may not align with live tabs.

3. **No "No data" placeholder restoration**: After all tabs are removed (first result arrives), there's no empty-state recovery. This is cosmetic and acceptable for the current workflow.
