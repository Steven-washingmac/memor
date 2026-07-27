#!/usr/bin/env python3
"""
TTAG 复测程序
=============
交互式验证已拟合的 ADC→温度 函数。
- 用户指定复测温度点
- 自动控制水浴箱到目标温度
- 等待水浴稳定 + ADC 稳定
- ADC 代入拟合函数 → 计算温度 → 与水浴实际温度对比
- 每个点误差须在 ±1°C 以内

用法:
  python ttag_verify.py
  python ttag_verify.py --device 195082 --connect 192.168.3.188:20226
"""

import sys
import os
import time
import struct
import socket
import threading
import argparse
from datetime import datetime
from collections import deque

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from water_bath_control import WaterBath
from ttag_monitor import FRAME_HEADER, parse_frame as monitor_parse_frame

# ============================================================
# 195082 拟合函数 — 6阶多项式 T = f(ADC)
# 标定日期: 2026-07-27 | 数据点: 499 | R² = 0.999986
# ADC 范围: 219 ~ 938 | 温度范围: -20 ~ 80°C
# ============================================================
COEFFS = [
    -1.371175217700e-15,   # a6
     3.869551921400e-12,   # a5
    -4.224867633800e-09,   # a4
     2.033094200100e-06,   # a3
    -2.263539449500e-04,   # a2
    -2.511256935300e-01,   # a1
     1.322510386300e+02,   # a0
]


def adc_to_temperature(adc):
    """ADC → 温度 (°C)，Horner 法求 6 阶多项式"""
    if adc <= 0 or adc >= 1024:
        return None
    x = adc
    t = COEFFS[0]
    for c in COEFFS[1:]:
        t = t * x + c
    return t


# ============================================================
# TTAG 接收器（复用 ttag_calibration.py）
# ============================================================
def parse_frame(data):
    tags = []
    try:
        frame = monitor_parse_frame(data)
        if frame.valid:
            for tag in frame.tags:
                tags.append({
                    'tag_id': tag.tag_id, 'adc': tag.adc,
                    'rssi': tag.rssi, 'tag_type': tag.tag_type,
                })
    except Exception:
        pass
    return tags


class TtagReceiver:
    def __init__(self, device_id, host='0.0.0.0', port=20226, connect_to=None):
        self.device_id = device_id
        self.host = host
        self.port = port
        self.connect_to = connect_to
        self.latest_adc = None
        self.latest_rssi = None
        self.last_seen = None
        self.hit_count = 0
        self.frame_count = 0
        self.running = False
        self._lock = threading.Lock()

    def start(self):
        self.running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self.running = False

    def get_state(self):
        with self._lock:
            return {
                'adc': self.latest_adc,
                'rssi': self.latest_rssi,
                'last_seen': self.last_seen,
                'hits': self.hit_count,
                'frames': self.frame_count,
            }

    def _run(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        is_client = False

        if self.connect_to:
            parts = self.connect_to.split(':')
            target_host = parts[0]
            target_port = int(parts[1]) if len(parts) > 1 else self.port
            sock.settimeout(5)
            try:
                sock.connect((target_host, target_port))
                sock.settimeout(2.0)
                is_client = True
            except Exception:
                print(f"  无法连接到基站 {self.connect_to}")
                return
        else:
            try:
                sock.bind((self.host, self.port))
                sock.listen(5)
                sock.settimeout(1.0)
            except OSError:
                print(f"  端口 {self.port} 被占用")
                sock.close()
                return

        buffer = b''
        while self.running:
            if is_client:
                try:
                    data = sock.recv(4096)
                    if not data:
                        break
                    buffer += data
                    buffer = self._process(buffer)
                except socket.timeout:
                    continue
                except OSError:
                    break
            else:
                try:
                    client, addr = sock.accept()
                    print(f"  基站已连接: {addr[0]}:{addr[1]}")
                    client.settimeout(5)
                    while self.running:
                        try:
                            data = client.recv(4096)
                            if not data:
                                break
                            buffer += data
                            buffer = self._process(buffer)
                        except socket.timeout:
                            continue
                    client.close()
                    buffer = b''
                except socket.timeout:
                    continue
                except OSError:
                    break
        sock.close()

    def _process(self, buffer):
        while True:
            idx = buffer.find(FRAME_HEADER)
            if idx == -1:
                return buffer[-1:] if len(buffer) > 1 else buffer
            if idx > 0:
                buffer = buffer[idx:]
            if len(buffer) < 4:
                return buffer
            data_len = struct.unpack_from('<H', buffer, 2)[0]
            frame_len = 2 + 2 + data_len + 1
            if frame_len > 8192:
                buffer = buffer[2:]
                continue
            if len(buffer) < frame_len:
                return buffer
            frame_data = buffer[:frame_len]
            buffer = buffer[frame_len:]
            self.frame_count += 1
            for t in parse_frame(frame_data):
                if t['tag_id'] == self.device_id and t['adc'] != 0xFFFF:
                    with self._lock:
                        self.latest_adc = t['adc']
                        self.latest_rssi = t['rssi']
                        self.last_seen = time.time()
                        self.hit_count += 1


# ============================================================
# ADC 稳定性检测（复用 ttag_calibration.py）
# ============================================================
class AdcStabilityDetector:
    """基于样本计数的 ADC 稳定性检测"""

    def __init__(self, min_samples=5, threshold=5):
        self.min_samples = min_samples
        self.threshold = threshold
        self._samples = []

    def feed(self, adc, ts=None):
        if ts is None:
            ts = time.time()
        self._samples.append((ts, adc))

    def check(self):
        """返回 (stable, mean, peak_to_peak, n_samples, elapsed_sec)"""
        n = len(self._samples)
        if n < self.min_samples:
            elapsed = self._samples[-1][0] - self._samples[0][0] if n >= 2 else 0
            return False, None, None, n, elapsed
        adcs = [a for _, a in self._samples]
        recent = adcs[-self.min_samples:]
        mean = sum(recent) / len(recent)
        rng = max(recent) - min(recent)
        elapsed = self._samples[-1][0] - self._samples[0][0] if n >= 2 else 0
        stable = rng <= self.threshold
        return stable, mean, rng, n, elapsed

    def reset(self):
        self._samples.clear()


# ============================================================
# 终端显示
# ============================================================
def clear_screen():
    os.system('cls' if os.name == 'nt' else 'clear')


def progress_bar(done, total, width=30):
    filled = width * done // max(total, 1)
    return '[' + '#' * filled + '-' * (width - filled) + ']'


# ============================================================
# 复测主逻辑
# ============================================================
def run_verify(device_id, points, connect_to=None, port=20226,
               bath_port='COM3', adc_samples=5, adc_threshold=5,
               bath_tolerance=0.1):
    """
    执行复测验证。

    参数:
        device_id:      TTAG 标签 ID
        points:         温度点列表 [(target_temp, label), ...]
        connect_to:     "ip:port" 客户端模式连接基站
        port:           TCP 端口
        bath_port:      水浴串口
        adc_samples:    ADC 稳定所需最小样本数
        adc_threshold:  ADC 峰峰值阈值
        bath_tolerance: 水浴稳定容差
    """
    total_points = len(points)

    print()
    print("=" * 64)
    print("  TTAG 复测程序")
    print(f"  设备: {device_id} | 复测点: {total_points} 个 | 合格线: ±1.0°C")
    print(f"  拟合: 6阶多项式 (ADC 219~938, T -20~80°C)")
    print(f"  水浴容差: ±{bath_tolerance}°C | ADC样本: ≥{adc_samples} 峰峰值≤{adc_threshold}")
    print("=" * 64)

    # ---- 连接水浴 ----
    print(f"\n[1/3] 连接水浴箱 ({bath_port}) ...")
    try:
        wb = WaterBath(port=bath_port)
        pv = wb.get_temperature()
        sv = wb.get_setpoint()
        print(f"      ✓ 已连接  PV={pv:.2f}°C  SV={sv}°C")
    except Exception as e:
        print(f"      ✗ 连接失败: {e}")
        return

    # ---- 连接 TTAG ----
    print(f"\n[2/3] 连接基站 (设备 {device_id}) ...")
    ttag = TtagReceiver(device_id, port=port, connect_to=connect_to)
    ttag.start()
    # 等数据
    for _ in range(40):
        time.sleep(0.5)
        st = ttag.get_state()
        if st.get('hits', 0) > 0:
            break
    st = ttag.get_state()
    if st.get('hits', 0) > 0:
        adc_v = st['adc']
        t_calc = adc_to_temperature(adc_v) if adc_v else None
        t_str = f" → {t_calc:.2f}°C" if t_calc is not None else ""
        print(f"      ✓ 已连接  ADC={adc_v}{t_str}  RSSI={st['rssi']}  "
              f"命中={st['hits']}次")
    else:
        print(f"      ⚠ 30s 内未收到数据，请检查基站。继续等待...")

    adc_det = AdcStabilityDetector(adc_samples, adc_threshold)
    results = []
    t0_total = time.time()

    # ---- 初始化 Excel（提前建好，每点立即写入）----
    script_dir = os.path.dirname(os.path.abspath(__file__))
    xlsx_path = os.path.join(script_dir, 'ADCTdata', f'verify_{device_id}.xlsx')
    os.makedirs(os.path.dirname(xlsx_path), exist_ok=True)

    def _ensure_excel():
        """确保 Excel 文件存在且表头就绪，返回 (workbook, sheet, next_row)"""
        from openpyxl import Workbook, load_workbook
        from openpyxl.styles import Font, Alignment, Border, Side, PatternFill
        if os.path.exists(xlsx_path):
            wb = load_workbook(xlsx_path)
            ws = wb.active
            nr = ws.max_row + 1
        else:
            wb = Workbook()
            ws = wb.active
            ws.title = 'TTAG Verify'
            ws.merge_cells('A1:J1')
            ws['A1'] = f'TTAG {device_id} 复测验证结果'
            ws['A1'].font = Font(bold=True, size=14)
            ws['A1'].alignment = Alignment(horizontal='center')
            ws.merge_cells('A2:J2')
            ws['A2'] = f'合格线: ±1.0°C  |  拟合: 6阶多项式 (R²=0.999986)'
            ws['A2'].alignment = Alignment(horizontal='center')
            hdrs = ['序号', '目标(°C)', '水浴实际(°C)', 'ADC均值', 'ADC峰峰值',
                    '采样数', '计算温度(°C)', '误差(°C)', '通过(±1°C)', '测试时间']
            hdr_fill = PatternFill(start_color='4472C4', end_color='4472C4', fill_type='solid')
            thin = Border(left=Side(style='thin'), right=Side(style='thin'),
                          top=Side(style='thin'), bottom=Side(style='thin'))
            for ci, h in enumerate(hdrs, 1):
                c = ws.cell(row=4, column=ci, value=h)
                c.font = Font(bold=True, size=11, color='FFFFFF')
                c.fill = hdr_fill
                c.alignment = Alignment(horizontal='center')
                c.border = thin
            nr = 5
        return wb, ws, nr

    def _append_to_excel(wb, ws, row, r, ts_str):
        """写一行数据到 Excel 并立即保存"""
        from openpyxl.styles import Alignment, Border, Side, PatternFill
        ok_fill = PatternFill(start_color='C6EFCE', end_color='C6EFCE', fill_type='solid')
        ng_fill = PatternFill(start_color='FFC7CE', end_color='FFC7CE', fill_type='solid')
        thin = Border(left=Side(style='thin'), right=Side(style='thin'),
                       top=Side(style='thin'), bottom=Side(style='thin'))
        passed = r['passed']
        vals = [row - 4, r['target'], r['pv'] if r['pv'] is not None else '',
                r['adc_mean'] if r['adc_mean'] is not None else '',
                r['adc_range'] if r['adc_range'] is not None else '',
                r['adc_n'],
                round(r['t_calc'], 2) if r['t_calc'] is not None else '',
                round(r['error'], 2) if r['error'] is not None else '',
                'YES' if passed else 'NO', ts_str]
        for ci, v in enumerate(vals, 1):
            c = ws.cell(row=row, column=ci, value=v)
            c.alignment = Alignment(horizontal='center')
            c.border = thin
            c.fill = ok_fill if passed else ng_fill
        widths = [6, 12, 14, 10, 12, 8, 14, 10, 12, 20]
        for ci, w in enumerate(widths, 1):
            ws.column_dimensions[chr(64 + ci)].width = w
        wb.save(xlsx_path)

    # ---- 确认开始 ----
    print(f"\n[3/3] 复测点清单:")
    for i, (t, label) in enumerate(points, 1):
        t_calc_est = adc_to_temperature(500)
        print(f"      {i:>2}. {t:>6.1f}°C  [{label}]")
    print()

    try:
        confirm = input("  开始复测? [y/N]: ").strip().lower()
        if confirm not in ('y', 'yes', '是'):
            print("  已取消")
            ttag.stop()
            wb.close()
            return
    except (EOFError, KeyboardInterrupt):
        print("\n  已取消")
        ttag.stop()
        wb.close()
        return

    # ================================================================
    # 逐点复测
    # ================================================================
    # 初始化 Excel
    xl_wb, xl_ws, xl_row = _ensure_excel()

    for i, (target, label) in enumerate(points):
        clear_screen()
        disp_i = i + 1
        done = i  # 已完成的点数

        print("=" * 64)
        print(f"  TTAG 复测程序 | 设备 {device_id} | "
              f"第 {disp_i}/{total_points} 点")
        print("=" * 64)
        print(f"  [{disp_i}/{total_points}] 目标温度: {target}°C  [{label}]")
        print(f"  进度: {progress_bar(done, total_points)} "
              f"{done * 100 // total_points}%")
        print("-" * 64)

        # ---- Step 1: 设置水浴 ----
        print(f"  [水浴] 设定 SV={target}°C ...")
        for attempt in range(5):
            ok = wb.set_temperature(target)
            if ok:
                break
            print(f"         重试 {attempt + 2}/5...")
            time.sleep(1)
        time.sleep(1)
        sv_check = wb.get_setpoint()
        print(f"  [水浴] SV={sv_check}°C")

        # ---- Step 2: 等待水浴稳定（完整逻辑来自 ttag_calibration.py）----
        t1 = time.time()
        bath_ok = False
        pv_first = None
        need_cool = False
        nudge_sv = None
        nudge_t = 0.0
        last_pwr_zero = 0.0

        while time.time() - t1 < 900:  # 15 分钟超时
            pv = wb.get_temperature()
            pwr = wb.get_status()
            ts = ttag.get_state()

            # 水浴偶尔丢包返回 None，跳过本次循环
            if pv is None:
                time.sleep(0.5)
                continue

            # 确定方向
            if pv_first is None and pv is not None:
                pv_first = pv
                need_cool = pv > target + bath_tolerance

            # ==== 推一把 (nudge) ====
            if pv is not None and nudge_sv is None and time.time() - t1 > 20:
                gap = abs(pv - target)
                if not need_cool and pv < target - bath_tolerance and pwr is not None and pwr == 0:
                    if last_pwr_zero == 0:
                        last_pwr_zero = time.time()
                    elif time.time() - last_pwr_zero > 15 and gap > 0.15:
                        nudge_sv = min(100, target + 2.0)
                        wb.set_temperature(nudge_sv)
                        nudge_t = time.time()
                        last_pwr_zero = 0
                elif need_cool and pv > target + bath_tolerance and pwr is not None and pwr == 0:
                    if last_pwr_zero == 0:
                        last_pwr_zero = time.time()
                    elif time.time() - last_pwr_zero > 15 and gap > 0.15:
                        nudge_sv = max(-30, target - 2.0)
                        wb.set_temperature(nudge_sv)
                        nudge_t = time.time()
                        last_pwr_zero = 0
                else:
                    last_pwr_zero = 0

            if nudge_sv is not None and pv is not None:
                if abs(pv - target) <= bath_tolerance:
                    wb.set_temperature(target)
                    nudge_sv = None
                elif time.time() - nudge_t > 120:
                    wb.set_temperature(target)
                    nudge_sv = None

            # 刷新显示
            clear_screen()
            print("=" * 64)
            print(f"  TTAG 复测程序 | 设备 {device_id} | "
                  f"第 {disp_i}/{total_points} 点")
            print("=" * 64)
            print(f"  等待水浴稳定到 {target}°C ...{'[降温]' if need_cool else '[升温]'}"
                  f"{' [推→' + str(nudge_sv) + '°C]' if nudge_sv is not None else ''}")
            print(f"  进度: {progress_bar(done, total_points)} "
                  f"{done * 100 // total_points}% | "
                  f"已耗时 {int((time.time() - t0_total) / 60)}min")
            print("-" * 64)
            if pv is not None:
                d = abs(pv - target)
                if need_cool:
                    reached = pv <= target + bath_tolerance
                else:
                    reached = pv >= target - bath_tolerance
                bs = '[OK]' if (reached and d <= bath_tolerance) else '...'
                print(f"  水浴: PV={pv:.4f}°C  d={d:.4f}°C  {bs}  输出={pwr}%")
            adc_cur = ts.get('adc')
            n_frames = ts.get('frames', 0)
            conn = '[LINK]' if n_frames > 0 else '[WAIT]'
            print(f"  TTAG: ADC={adc_cur}  RSSI={ts.get('rssi')}  "
                  f"命中={ts.get('hits', 0)}  {conn}")
            if results:
                last_r = results[-1]
                print(f"  已复测: {len(results)} 点 | "
                      f"上一点误差: {last_r['error']:+.2f}°C")
            print("=" * 64)

            # 方向性稳定判定 + 二次确认
            if pv is not None and time.time() - t1 > 10:
                # 触底判定
                plateau = (time.time() - t1 > 600
                           and abs(pv - target) <= 0.3
                           and reached)
                normal_ok = (reached and abs(pv - target) <= bath_tolerance)

                if plateau or normal_ok:
                    if nudge_sv is not None:
                        wb.set_temperature(target)
                        nudge_sv = None
                    pv_before = pv
                    time.sleep(2)
                    pv2 = wb.get_temperature()
                    if pv2 is not None:
                        drift = abs(pv2 - pv_before) if pv_before is not None else 999
                        drift_limit = 0.1 if plateau else 0.05
                        if need_cool:
                            ok = (pv2 <= target + bath_tolerance
                                  and abs(pv2 - target) <= max(bath_tolerance, 0.3 if plateau else 0)
                                  and drift < drift_limit)
                        else:
                            ok = (pv2 >= target - bath_tolerance
                                  and abs(pv2 - target) <= max(bath_tolerance, 0.3 if plateau else 0)
                                  and drift < drift_limit)
                        if ok:
                            bath_ok = True
                            if plateau:
                                print(f"  ⚡ 触底判定: PV={pv2:.3f}°C, "
                                      f"距目标 {abs(pv2 - target):.3f}°C, 接受")
                            break
            time.sleep(0.4)

        if not bath_ok:
            print(f"\n  ❌ {target}°C 水浴超时未稳定，跳过")
            results.append({
                'target': target, 'label': label,
                'pv': pv, 'adc_mean': None, 'adc_range': None,
                'adc_n': 0, 't_calc': None, 'error': None,
                'passed': False, 'note': '水浴超时',
            })
            continue

        # ---- Step 3: 等待 ADC 稳定 ----
        adc_det.reset()
        t2 = time.time()
        adc_ok = False
        last_hits = ttag.get_state().get('hits', 0)

        while time.time() - t2 < 240:  # 4 分钟超时
            pv = wb.get_temperature()
            pwr = wb.get_status()
            ts = ttag.get_state()
            adc_v = ts.get('adc')
            cur_hits = ts.get('hits', 0)
            adc_el = time.time() - t2

            if adc_v is not None and cur_hits != last_hits:
                adc_det.feed(adc_v)
                last_hits = cur_hits

            stable, mean, rng, n, span = adc_det.check()

            clear_screen()
            print("=" * 64)
            print(f"  TTAG 复测程序 | 设备 {device_id} | "
                  f"第 {disp_i}/{total_points} 点")
            print("=" * 64)
            print(f"  采集 ADC 数据 ... 目标={target}°C")
            print(f"  进度: {progress_bar(done, total_points)} "
                  f"{done * 100 // total_points}%")
            print("-" * 64)
            d = abs(pv - target) if pv is not None else 0
            pv_str = f"{pv:.4f}" if pv is not None else "?"
            print(f"  水浴: PV={pv_str}°C  d={d:.4f}°C  [OK]  加热={pwr}%")
            if adc_v is not None:
                st_str = '● STABLE' if stable else '○ 采集中'
                fresh = '(旧)' if n == 0 else f'n={n}'
                t_now = adc_to_temperature(adc_v)
                t_now_str = f"→ {t_now:.2f}°C" if t_now is not None else ""
                print(f"  TTAG: ADC={adc_v} {fresh}  {st_str}  "
                      f"μ={mean or 0:.1f}  Δ={rng}  {t_now_str}  [{adc_el:.0f}s]")
                if n == 0 and adc_el > 15:
                    n_frames = ts.get('frames', 0)
                    if n_frames == 0:
                        print(f"     ⚠ 未收到任何帧！基站未连接？")
                    elif ts.get('hits', 0) == 0:
                        print(f"     ⚠ 收到帧但无目标标签 {device_id}！")
            else:
                print(f"  TTAG: 等待基站数据...")
            print("-" * 64)
            if results:
                last_r = results[-1]
                print(f"  已复测: {len(results)} 点 | "
                      f"上一点误差: {last_r['error']:+.2f}°C"
                      if last_r['error'] is not None else
                      f"  已复测: {len(results)} 点")
            print("=" * 64)

            if stable:
                adc_mean, adc_range, adc_n = mean, rng, n
                adc_ok = True
                break
            time.sleep(0.3)

        if not adc_ok:
            _, mean, rng, n, _ = adc_det.check()
            ts2 = ttag.get_state()
            adc_mean = mean if mean is not None else ts2.get('adc', 0)
            adc_range = rng if rng is not None else 0
            adc_n = n

        # ---- Step 4: 计算温度 & 对比 ----
        pv_final = wb.get_temperature()
        pv_now = pv_final if pv_final is not None else target
        t_calculated = adc_to_temperature(adc_mean) if adc_mean is not None else None

        if t_calculated is not None and pv_now is not None:
            error = t_calculated - pv_now
        else:
            error = None

        passed = abs(error) <= 1.0 if error is not None else False
        status_icon = '✅' if passed else '❌'

        results.append({
            'target': target, 'label': label,
            'pv': pv_now, 'adc_mean': adc_mean, 'adc_range': adc_range,
            'adc_n': adc_n, 't_calc': t_calculated, 'error': error,
            'passed': passed, 'note': '',
        })

        # 立即写 Excel（每点都存，不会因后续崩溃丢失数据）
        now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        try:
            _append_to_excel(xl_wb, xl_ws, xl_row, results[-1], now_str)
            xl_row += 1
        except Exception as e:
            print(f"  ⚠ Excel 写入失败: {e}")

        # 单点结果
        print(f"\n  {'─'*50}")
        print(f"  📊 第 {disp_i} 点结果:")
        print(f"     目标温度:  {target}°C")
        print(f"     水浴实际:  {pv_now:.2f}°C")
        print(f"     ADC 均值:  {adc_mean:.1f}  (Δ={adc_range}, n={adc_n})")
        print(f"     计算温度:  {t_calculated:.2f}°C" if t_calculated is not None else f"     计算温度:  N/A")
        print(f"     误  差:    {error:+.2f}°C  {status_icon}" if error is not None else f"     误  差:    N/A")
        print(f"  {'─'*50}")

        if disp_i < total_points:
            input(f"\n  按 Enter 继续下一个点...")

    # ================================================================
    # 汇总
    # ================================================================
    clear_screen()
    print("=" * 64)
    print("  TTAG 复测程序 — 汇总")
    print(f"  设备: {device_id} | "
          f"总耗时: {int((time.time() - t0_total) / 60)}min")
    print("=" * 64)
    print(f"  {'#':<4} {'目标°C':<9} {'水浴°C':<9} {'ADC':<7} "
          f"{'计算°C':<9} {'误差°C':<9} {'±1°C?':<8}")
    print(f"  {'-'*56}")

    pass_count = 0
    fail_count = 0
    for j, r in enumerate(results, 1):
        pv_s = f"{r['pv']:.2f}" if r['pv'] is not None else "?"
        tc_s = f"{r['t_calc']:.2f}" if r['t_calc'] is not None else "?"
        err_s = f"{r['error']:+.2f}" if r['error'] is not None else "?"
        adc_s = f"{r['adc_mean']:.1f}" if r['adc_mean'] is not None else "?"
        flag = '✅ 通过' if r['passed'] else '❌ 超标'
        if r['passed']:
            pass_count += 1
        else:
            fail_count += 1
        print(f"  {j:<4} {r['target']:<9} {pv_s:<9} {adc_s:<7} "
              f"{tc_s:<9} {err_s:<9} {flag}   {r['label']}")

    print(f"  {'-'*56}")
    print(f"  通过: {pass_count}/{len(results)}  |  "
          f"未通过: {fail_count}/{len(results)}")
    print("=" * 64)
    print(f"\n  结果已在测试过程中逐点保存至: {xlsx_path}")

    ttag.stop()
    wb.close()


# ============================================================
# 交互式输入温度点
# ============================================================
def input_points():
    """交互式让用户输入复测温度点"""
    print()
    print("=" * 64)
    print("  TTAG 复测程序 — 设定复测温度点")
    print("=" * 64)
    print()
    print("  请输入要复测的温度点（°C），用逗号或空格分隔。")
    print("  示例: -18, -10, 0, 10, 25, 40, 55, 70, 80")
    print("  或者输入 'low'  'mid'  'high' 使用预设分组")
    print()

    while True:
        try:
            raw = input("  复测温度点: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n  已取消")
            return None

        if not raw:
            print("  输入为空，请重新输入")
            continue

        raw_lower = raw.lower()

        if raw_lower == 'low':
            points = [(-18, '低温'), (-15, '低温'), (-10, '低温'),
                      (-5, '低温'), (0, '低温')]
            break
        elif raw_lower == 'mid':
            points = [(5, '中温'), (10, '中温'), (20, '中温'),
                      (30, '中温'), (40, '中温')]
            break
        elif raw_lower == 'high':
            points = [(50, '高温'), (60, '高温'), (70, '高温'),
                      (75, '高温'), (80, '高温')]
            break

        # 手动解析
        try:
            parts = raw.replace(',', ' ').split()
            temps = []
            for p in parts:
                t = float(p)
                if t < -30 or t > 100:
                    print(f"  ⚠ {t}°C 超出水浴范围 (-30~100°C)，请修正")
                    raise ValueError()
                temps.append(t)

            if not temps:
                print("  未解析到有效温度点，请重新输入")
                continue

            # 按温度从低到高排序（复测从低温开始）
            temps.sort()
            points = []
            for t in temps:
                if t <= -10:
                    label = '低温'
                elif t <= 0:
                    label = '低温'
                elif t <= 40:
                    label = '中温'
                else:
                    label = '高温'
                points.append((t, label))
            break

        except ValueError:
            continue

    # 确认
    print()
    print(f"  共 {len(points)} 个复测点（从低到高）:")
    for i, (t, label) in enumerate(points, 1):
        print(f"    {i:>2}. {t:>6.1f}°C  [{label}]")
    print()

    try:
        ok = input("  确认? [Y/n]: ").strip().lower()
        if ok in ('n', 'no', '否'):
            return input_points()  # 重新输入
    except (EOFError, KeyboardInterrupt):
        print("\n  已取消")
        return None

    return points


# ============================================================
# 主入口
# ============================================================
def main():
    parser = argparse.ArgumentParser(
        description='TTAG 复测程序 — 交互式验证 ADC→温度 拟合函数',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python ttag_verify.py
  python ttag_verify.py --device 195082
  python ttag_verify.py --device 195082 --connect 192.168.3.188:20226
  python ttag_verify.py --bath-port COM4
        """
    )
    parser.add_argument('--device', type=int, default=195082,
                        help='TTAG 标签 ID (默认 195082)')
    parser.add_argument('--connect', default=None, metavar='IP:PORT',
                        help='Client 模式：主动连接基站')
    parser.add_argument('--port', type=int, default=20226,
                        help='TCP 端口 (默认 20226)')
    parser.add_argument('--bath-port', default='COM3',
                        help='水浴箱串口 (默认 COM3)')
    parser.add_argument('--adc-samples', type=int, default=5,
                        help='ADC 稳定所需样本数 (默认 5)')
    parser.add_argument('--adc-threshold', type=int, default=5,
                        help='ADC 峰峰值阈值 (默认 5)')
    parser.add_argument('--bath-tolerance', type=float, default=0.1,
                        help='水浴稳定容差 (默认 0.1)')
    args = parser.parse_args()

    # ---- 交互式输入温度点 ----
    points = input_points()
    if points is None:
        return

    # ---- 开始复测 ----
    while True:
        try:
            run_verify(
                device_id=args.device,
                points=points,
                connect_to=args.connect,
                port=args.port,
                bath_port=args.bath_port,
                adc_samples=args.adc_samples,
                adc_threshold=args.adc_threshold,
                bath_tolerance=args.bath_tolerance,
            )
        except Exception as e:
            print(f"\n\n  ❌ 程序异常退出: {e}")
            import traceback
            traceback.print_exc()
            break

        print()
        try:
            again = input("  是否继续测试其他温度点？[y/N]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if again not in ('y', 'yes', '是'):
            break

        points = input_points()
        if points is None:
            break

    print()
    try:
        input("  按 Enter 退出...")
    except Exception:
        pass


if __name__ == '__main__':
    main()
