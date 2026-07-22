#!/usr/bin/env python3
"""TTAG 标定启动器 — 自动检测续跑、配置稳定参数"""
import os, sys, glob

os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, '.')

# ---- 配置 ----
DEVICE = 230030
END_TEMP = 8.0
STEP = 0.2
BATH_TOLERANCE = 0.1
ADC_WINDOW = 3.0
ADC_THRESHOLD = 5
WATER_BATH_PORT = 'COM3'
TTAG_PORT = 20226

# ---- 检测已有文件 ----
existing = glob.glob('calibration_data.xlsx') + glob.glob('cal_*.xlsx') + glob.glob('cal_*.csv')
resume_file = None
if existing:
    latest = max(existing, key=os.path.getmtime)
    print(f'发现已有标定文件: {latest}')
    try:
        from openpyxl import load_workbook
        wb = load_workbook(latest, read_only=True, data_only=True)
        ws = wb.active
        rows = list(ws.iter_rows(min_row=2, values_only=True))
        wb.close()
        if rows:
            last = rows[-1]
            last_target = float(last[2]) if last[2] is not None else 0
            next_temp = round(last_target + STEP, 1)
            print(f'  已完成 {len(rows)} 点, 上次: {last_target}C')
            if next_temp <= END_TEMP:
                resume_file = latest
                print(f'  => 将从 {next_temp}C 继续, 剩余 {round((END_TEMP - next_temp) / STEP + 1)} 点')
            else:
                print(f'  => 已完成全部 {END_TEMP}C 范围')
    except Exception as e:
        print(f'  无法读取文件: {e}')

if resume_file:
    cmd = (f'python -u ttag_calibration.py'
           f' --resume "{resume_file}"'
           f' --end {END_TEMP}'
           f' --step {STEP}'
           f' --bath-tolerance {BATH_TOLERANCE}'
           f' --stability-window {ADC_WINDOW}'
           f' --stability-threshold {ADC_THRESHOLD}'
           f' --water-bath-port {WATER_BATH_PORT}'
           f' --ttag-port {TTAG_PORT}')
else:
    cmd = (f'python -u ttag_calibration.py'
           f' --device {DEVICE}'
           f' --start 5.0'
           f' --end {END_TEMP}'
           f' --step {STEP}'
           f' --bath-tolerance {BATH_TOLERANCE}'
           f' --stability-window {ADC_WINDOW}'
           f' --stability-threshold {ADC_THRESHOLD}'
           f' --water-bath-port {WATER_BATH_PORT}'
           f' --ttag-port {TTAG_PORT}')

print(f'\n启动命令:')
print(f'  {cmd}')
print()
os.system(cmd)
