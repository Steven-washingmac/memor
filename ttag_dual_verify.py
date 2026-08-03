#!/usr/bin/env python3
"""
TTAG 双设备复测程序
==================
一个水浴、一帧数据、多台设备同时复测。支持旧协议（ADC→多项式）和新协议（直接温度）。

用法:
  python ttag_dual_verify.py
"""

import sys
import os
import time
import struct
import socket
import threading
import argparse
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from water_bath_control import WaterBath
from ttag_monitor import FRAME_HEADER, parse_frame as monitor_parse_frame

# ============================================================
# 6 阶多项式系数（ADC → 温度）
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

# ============================================================
# 内置设备表
# ============================================================
DEVICE_TABLE = {
    195082: {'protocol': 'old_adc', 'name': '195082'},
    192084: {'protocol': 'old_adc', 'name': '192084'},
    192080: {'protocol': 'old_adc', 'name': '192080'},
    201154: {'protocol': 'new_direct', 'name': '201154'},
    207154: {'protocol': 'new_direct', 'name': '207154'},
}


def adc_to_temperature(adc):
    """Horner 法计算 ADC → 温度"""
    if adc <= 0 or adc >= 1024:
        return None
    x = adc
    t = COEFFS[0]
    for c in COEFFS[1:]:
        t = t * x + c
    return t


# ============================================================
# 帧解析（复用 monitor，按标签提取温度）
# ============================================================
def parse_frame_tags(data):
    """解析一帧，返回 [{tag_id, adc, rssi, tag_type, temperature}]"""
    tags = []
    try:
        frame = monitor_parse_frame(data)
        if frame.valid:
            for tag in frame.tags:
                tags.append({
                    'tag_id': tag.tag_id,
                    'adc': tag.adc,
                    'rssi': tag.rssi,
                    'tag_type': tag.tag_type,
                    'temperature': tag.temperature,
                })
    except Exception:
        pass
    return tags


# ============================================================
# 多设备基站接收器
# ============================================================
class MultiReceiver:
    """监听基站，跟踪多个目标设备"""

    def __init__(self, device_ids, host='0.0.0.0', port=20226, connect_to=None):
        self.device_ids = set(device_ids)
        self.host = host
        self.port = port
        self.connect_to = connect_to
        self._lock = threading.Lock()
        # 每设备状态
        self._state = {}
        for did in device_ids:
            self._state[did] = {'adc': None, 'rssi': None, 'temperature': None,
                                'hits': 0, 'last_seen': None}
        self.frame_count = 0
        self.running = False

    def start(self):
        self.running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self.running = False

    def get_state(self, device_id):
        with self._lock:
            return dict(self._state.get(device_id, {}))

    def get_all_states(self):
        s = {}
        with self._lock:
            s['frame_count'] = self.frame_count
            for did in self.device_ids:
                s[did] = dict(self._state.get(did, {}))
        return s

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
                print(f"  端口 {self.port} 被占用，自动切换客户端模式...")
                sock.close()
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                sock.settimeout(5)
                try:
                    sock.connect(('192.168.3.188', self.port))
                    sock.settimeout(2.0)
                    is_client = True
                except Exception:
                    print(f"  无法连接到基站 192.168.3.188:{self.port}")
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
            for t in parse_frame_tags(frame_data):
                if t['tag_id'] in self.device_ids:
                    did = t['tag_id']
                    if t['adc'] == 0xFFFF:
                        continue
                    with self._lock:
                        st = self._state[did]
                        st['adc'] = t['adc']
                        st['rssi'] = t['rssi']
                        st['temperature'] = t.get('temperature')
                        st['last_seen'] = time.time()
                        st['hits'] += 1


# ============================================================
# 稳定性检测器
# ============================================================
class StabilityDetector:
    def __init__(self, min_samples=5, threshold=5):
        self.min_samples = min_samples
        self.threshold = threshold
        self._samples = []

    def feed(self, value):
        self._samples.append((time.time(), value))

    def check(self):
        n = len(self._samples)
        if n < self.min_samples:
            elapsed = self._samples[-1][0] - self._samples[0][0] if n >= 2 else 0
            return False, None, None, n, elapsed
        recent = [v for _, v in self._samples]
        mean = sum(recent) / n
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
    os.system('cls' if sys.platform == 'win32' else 'clear')


def progress_bar(done, total, width=30):
    filled = int(width * done / total) if total > 0 else 0
    return '[' + '█' * filled + '-' * (width - filled) + ']'


# ============================================================
# 温度计算（按协议）
# ============================================================
def calc_temperature(device_id, protocol, adc_mean, tag_temp):
    """根据协议计算温度"""
    if protocol == 'new_direct':
        if tag_temp is not None:
            return tag_temp
        elif adc_mean is not None:
            raw = adc_mean
            if raw > 32767:
                raw -= 65536
            return raw / 10.0
        return None
    else:  # old_adc
        return adc_to_temperature(adc_mean) if adc_mean is not None else None


# ============================================================
# 核心：双设备复测
# ============================================================
def run_dual_verify(devices, points, connect_to=None, port=20226,
                    bath_port='COM3', stability_samples=5, stability_threshold=5,
                    bath_tolerance=0.3, excel_path=None):
    """
    devices: [(device_id, protocol), ...]
    points: [(target_temp, label), ...]
    """
    device_ids = [d[0] for d in devices]
    total_points = len(points)

    # ---- 连接水浴 ----
    print(f"\n[1/3] 连接水浴箱 ({bath_port}) ...")
    try:
        wb = WaterBath(port=bath_port)
        pv = wb.get_temperature()
        sv = wb.get_setpoint()
        pv_str = f"{pv:.2f}°C" if pv is not None else "?"
        sv_str = f"{sv:.1f}°C" if sv is not None else "?"
        print(f"      ✓ 已连接  PV={pv_str}  SV={sv_str}")
    except Exception as e:
        print(f"      ✗ 连接失败: {e}")
        return

    # ---- 连接基站 ----
    devices_str = ', '.join(str(d) for d in device_ids)
    print(f"\n[2/3] 连接基站 (设备 {devices_str}) ...")
    receiver = MultiReceiver(device_ids, port=port, connect_to=connect_to)
    receiver.start()
    # 等数据
    found = set()
    for _ in range(40):
        time.sleep(0.5)
        for did in device_ids:
            st = receiver.get_state(did)
            if st.get('hits', 0) > 0:
                found.add(did)
        if len(found) >= len(device_ids):
            break
    # 打印连接状态
    for did, proto in devices:
        st = receiver.get_state(did)
        if st.get('hits', 0) > 0:
            tag_t = st.get('temperature')
            if proto == 'new_direct':
                t_str = f"T={tag_t:.2f}°C" if tag_t is not None else "?"
                print(f"      ✓ {did} (新协议) {t_str}  RSSI={st['rssi']}  命中={st['hits']}")
            else:
                adc_v = st['adc']
                t_calc = adc_to_temperature(adc_v) if adc_v else None
                t_str = f"→ {t_calc:.2f}°C" if t_calc is not None else ""
                print(f"      ✓ {did} (旧ADC) ADC={adc_v}{t_str}  RSSI={st['rssi']}  命中={st['hits']}")
        else:
            print(f"      ⚠ {did} 30s 内未收到数据，请检查设备")

    t0_total = time.time()

    # ---- Excel ----
    if excel_path:
        xlsx_path = excel_path
    else:
        onedrive_desktop = os.path.join(os.path.expanduser('~'), 'OneDrive', '桌面')
        if not os.path.isdir(onedrive_desktop):
            onedrive_desktop = os.path.join(os.path.expanduser('~'), 'Desktop')
        dev_names = '_'.join(str(d) for d in sorted(device_ids))
        xlsx_path = os.path.join(onedrive_desktop, f'TTAG_双测_{dev_names}.xlsx')
    os.makedirs(os.path.dirname(xlsx_path), exist_ok=True)

    def _ensure_excel():
        from openpyxl import Workbook, load_workbook
        from openpyxl.styles import Font, Alignment, Border, Side, PatternFill
        thin = Border(left=Side(style='thin'), right=Side(style='thin'),
                       top=Side(style='thin'), bottom=Side(style='thin'))
        hdr_fill = PatternFill(start_color='4472C4', end_color='4472C4', fill_type='solid')

        if os.path.exists(xlsx_path):
            wb = load_workbook(xlsx_path)
        else:
            wb = Workbook()
            wb.remove(wb.active)

        # 每设备一个 Sheet
        sheets = {}
        for did, proto in devices:
            sname = f'{did} {"ADC复测" if proto == "old_adc" else "温度复测"}'
            if sname in wb.sheetnames:
                ws = wb[sname]
                nr = ws.max_row + 1
            else:
                ws = wb.create_sheet(sname)
                ws.merge_cells('A1:J1')
                ws['A1'] = f'TTAG {did} 复测验证结果'
                ws['A1'].font = Font(bold=True, size=14)
                ws['A1'].alignment = Alignment(horizontal='center')
                ws.merge_cells('A2:J2')
                proto_label = '旧协议 ADC→多项式' if proto == 'old_adc' else '新协议 直接温度'
                ws['A2'] = f'合格线: ±1.0°C  |  {proto_label}'
                ws['A2'].alignment = Alignment(horizontal='center')
                if proto == 'old_adc':
                    hdrs = ['序号', '目标(°C)', '水浴(°C)', 'ADC均值', 'ADC峰峰值',
                            '采样数', '计算温度(°C)', '误差(°C)', '通过(±1°C)', '测试时间']
                else:
                    hdrs = ['序号', '目标(°C)', '水浴(°C)', '温度原始值', '原始值波动',
                            '采样数', '标签温度(°C)', '误差(°C)', '通过(±1°C)', '测试时间']
                for ci, h in enumerate(hdrs, 1):
                    c = ws.cell(row=4, column=ci, value=h)
                    c.font = Font(bold=True, size=11, color='FFFFFF')
                    c.fill = hdr_fill
                    c.alignment = Alignment(horizontal='center')
                    c.border = thin
                widths = [6, 12, 14, 12, 12, 8, 14, 10, 12, 20]
                for ci, w in enumerate(widths, 1):
                    ws.column_dimensions[chr(64 + ci)].width = w
                nr = 5
            sheets[did] = (ws, nr)
        return wb, sheets

    def _append_row(wb, ws, row_num, data, ts_str, proto):
        from openpyxl.styles import Alignment, Border, Side, PatternFill
        ok_fill = PatternFill(start_color='C6EFCE', end_color='C6EFCE', fill_type='solid')
        ng_fill = PatternFill(start_color='FFC7CE', end_color='FFC7CE', fill_type='solid')
        thin = Border(left=Side(style='thin'), right=Side(style='thin'),
                       top=Side(style='thin'), bottom=Side(style='thin'))
        passed = data['passed']
        vals = [
            row_num - 4,
            data['target'],
            data['pv'] if data['pv'] is not None else '',
            data['adc_mean'] if data['adc_mean'] is not None else '',
            data['adc_range'] if data['adc_range'] is not None else '',
            data['adc_n'],
            round(data['t_calc'], 2) if data['t_calc'] is not None else '',
            round(data['error'], 2) if data['error'] is not None else '',
            'YES' if passed else 'NO',
            ts_str,
        ]
        for ci, v in enumerate(vals, 1):
            c = ws.cell(row=row_num, column=ci, value=v)
            c.alignment = Alignment(horizontal='center')
            c.border = thin
            c.fill = ok_fill if passed else ng_fill
        wb.save(xlsx_path)

    # ---- 确认开始 ----
    print(f"\n[3/3] 复测点清单:")
    for i, (t, label) in enumerate(points, 1):
        print(f"      {i:>2}. {t:>6.1f}°C  [{label}]")
    print()

    try:
        confirm = input("  开始复测? [y/N]: ").strip().lower()
        if confirm not in ('y', 'yes', '是'):
            print("  已取消")
            receiver.stop()
            wb.close()
            return
    except (EOFError, KeyboardInterrupt):
        print("\n  已取消")
        receiver.stop()
        wb.close()
        return

    # 初始化 Excel
    try:
        xl_wb, xl_sheets = _ensure_excel()
    except Exception as e:
        print(f"\n  ❌ 无法写入 Excel: {e}")
        receiver.stop()
        wb.close()
        return
    print(f"  数据文件: {xlsx_path} （测试过程中请勿打开此文件）\n")

    # ---- 逐点复测 ----
    all_results = {did: [] for did in device_ids}

    for i, (target, label) in enumerate(points):
        clear_screen()
        disp_i = i + 1
        done = i

        print("=" * 64)
        print(f"  TTAG 双测 | 设备 {devices_str} | 第 {disp_i}/{total_points} 点")
        print("=" * 64)
        print(f"  目标温度: {target}°C  [{label}]")
        print(f"  进度: {progress_bar(done, total_points)} {done * 100 // total_points}%")
        print("-" * 64)

        # ==== Step 1: 设定水浴 ====
        print(f"  设定水浴 SV={target}°C ...")
        wb.set_temperature(target)

        # ==== Step 2: 等待水浴稳定 ====
        t1 = time.time()
        bath_ok = False
        need_cool = False
        nudge_sv = None
        nudge_t = 0.0
        last_pwr_zero = 0.0

        while time.time() - t1 < 900:
            pv = wb.get_temperature()
            pwr = wb.get_status()

            if pv is not None and need_cool is False:
                need_cool = pv > target + bath_tolerance

            # Nudge
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

            # 显示
            print("=" * 64)
            print(f"  等待水浴稳定到 {target}°C ..."
                  f"{'[降温]' if need_cool else '[升温]'}"
                  f"{' [推→' + str(nudge_sv) + '°C]' if nudge_sv is not None else ''}")
            print(f"  进度: {progress_bar(done, total_points)} {done * 100 // total_points}% | "
                  f"已耗时 {int((time.time() - t0_total) / 60)}min")
            print("-" * 64)
            if pv is not None:
                d = abs(pv - target)
                reached = (pv >= target - bath_tolerance) if not need_cool else (pv <= target + bath_tolerance)
                bs = '[OK]' if (reached and d <= bath_tolerance) else '...'
                print(f"  水浴: PV={pv:.4f}°C  d={d:.4f}°C  {bs}  输出={pwr}%")
            # 显示各设备最新数据
            for did, proto in devices:
                st = receiver.get_state(did)
                tag_t = st.get('temperature')
                adc_v = st.get('adc')
                hits = st.get('hits', 0)
                if proto == 'new_direct' and tag_t is not None:
                    print(f"  {did}: T={tag_t:.2f}°C  hit={hits}")
                elif adc_v is not None:
                    t_calc = adc_to_temperature(adc_v) if adc_v else None
                    t_str = f"→ {t_calc:.2f}°C" if t_calc is not None else ""
                    print(f"  {did}: ADC={adc_v} {t_str}  hit={hits}")
                else:
                    print(f"  {did}: 等待数据...")
            print("=" * 64)

            # 稳定判定
            if pv is not None and time.time() - t1 > 10:
                if need_cool:
                    reached = pv <= target + bath_tolerance
                else:
                    reached = pv >= target - bath_tolerance
                plateau = (time.time() - t1 > 600 and abs(pv - target) <= 0.3 and reached)
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
                        drift_limit = 0.3 if plateau else 0.15
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
                            break
            time.sleep(0.4)

        if not bath_ok:
            print(f"\n  ❌ {target}°C 水浴超时未稳定，跳过")
            for did in device_ids:
                all_results[did].append({
                    'target': target, 'label': label,
                    'pv': pv, 'adc_mean': None, 'adc_range': None,
                    'adc_n': 0, 't_calc': None, 'error': None,
                    'passed': False, 'note': '水浴超时',
                })
            continue

        # ==== Step 3: 数据采集（多设备并行判稳）====
        detectors = {}
        last_hits = {}
        for did in device_ids:
            detectors[did] = StabilityDetector(stability_samples, stability_threshold)
            last_hits[did] = receiver.get_state(did).get('hits', 0)

        t2 = time.time()
        adc_results = {}  # did -> {adc_mean, adc_range, adc_n, t_calc, error, passed}

        while time.time() - t2 < 240:
            pv = wb.get_temperature()
            pwr = wb.get_status()
            adc_el = time.time() - t2

            for did in device_ids:
                st = receiver.get_state(did)
                cur_hits = st.get('hits', 0)
                adc_v = st.get('adc')
                if adc_v is not None and cur_hits != last_hits[did]:
                    detectors[did].feed(adc_v)
                    last_hits[did] = cur_hits

            # 检查所有设备稳定状态
            all_stable = True
            for did in device_ids:
                stable, mean, rng, n, _ = detectors[did].check()
                if not stable:
                    all_stable = False

            if all_stable and all(detectors[did].check()[3] >= stability_samples for did in device_ids):
                break

            # 显示
            clear_screen()
            print("=" * 64)
            print(f"  TTAG 双测 | 设备 {devices_str} | 第 {disp_i}/{total_points} 点")
            print("=" * 64)
            print(f"  采集数据 ... 目标={target}°C")
            print(f"  进度: {progress_bar(done, total_points)} {done * 100 // total_points}%")
            print("-" * 64)
            d = abs(pv - target) if pv is not None else 0
            pv_str = f"{pv:.4f}" if pv is not None else "?"
            print(f"  水浴: PV={pv_str}°C  d={d:.4f}°C  [OK]  加热={pwr}%")
            for did in device_ids:
                stable, mean, rng, n, _ = detectors[did].check()
                st = receiver.get_state(did)
                adc_v = st.get('adc')
                tag_t = st.get('temperature')
                proto = dict(devices)[did]
                st_str = '● STABLE' if stable else '○ 采集中'
                if adc_v is not None:
                    if proto == 'new_direct':
                        t_now_str = f"T={tag_t:.2f}°C" if tag_t is not None else ""
                    else:
                        t_calc = adc_to_temperature(adc_v)
                        t_now_str = f"→ {t_calc:.2f}°C" if t_calc is not None else ""
                    print(f"  {did}: raw={adc_v} n={n}  {st_str}  μ={mean or 0:.1f}  "
                          f"Δ={rng}  {t_now_str}  [{adc_el:.0f}s]")
                else:
                    print(f"  {did}: 等待数据...  [{adc_el:.0f}s]")
            # 上一个点结果
            for did in device_ids:
                if all_results[did]:
                    lr = all_results[did][-1]
                    if lr['error'] is not None:
                        err_str = f"{lr['error']:+.2f}°C"
                    else:
                        err_str = "N/A"
                    print(f"  {did} 上一点误差: {err_str}")
            print("=" * 64)

            # 无数据提示
            if adc_el > 20:
                all_hits = sum(receiver.get_state(did).get('hits', 0) for did in device_ids)
                if all_hits == 0:
                    print(f"     ⚠ 未收到任何帧！检查基站")

            time.sleep(0.3)

        # 检查是否所有设备都有数据
        missing = []
        for did in device_ids:
            _, _, _, n, _ = detectors[did].check()
            if n == 0:
                missing.append(did)
        if missing:
            print(f"\n  ⚠ 设备 {', '.join(str(d) for d in missing)} 4 分钟内未收到任何数据！")
            try:
                ans = input(f"  继续等待？[Y=等2分钟/n=跳过此点]: ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if ans not in ('n', 'no', '否'):
                # 再等 2 分钟
                extra_start = time.time()
                while time.time() - extra_start < 120:
                    for did in missing:
                        st = receiver.get_state(did)
                        cur_hits = st.get('hits', 0)
                        adc_v = st.get('adc')
                        if adc_v is not None and cur_hits != last_hits.get(did, 0):
                            detectors[did].feed(adc_v)
                            last_hits[did] = cur_hits
                    _, _, _, n_new, _ = detectors[missing[0]].check()
                    if n_new > 0 and len(missing) == 1:
                        break
                    if len(missing) > 1 and all(detectors[d].check()[3] > 0 for d in missing):
                        break
                    time.sleep(0.5)

        # 收集结果
        for did in device_ids:
            stable, mean, rng, n, _ = detectors[did].check()
            st = receiver.get_state(did)
            adc_mean = mean if mean is not None else st.get('adc', 0)
            adc_range = rng if rng is not None else 0
            adc_n = n
            tag_temp = st.get('temperature')
            proto = dict(devices)[did]
            t_calc = calc_temperature(did, proto, adc_mean, tag_temp)
            pv_final = wb.get_temperature()
            pv_now = pv_final if pv_final is not None else target
            error = t_calc - pv_now if (t_calc is not None and pv_now is not None) else None
            passed = abs(error) <= 1.0 if error is not None else False
            adc_results[did] = {
                'adc_mean': adc_mean, 'adc_range': adc_range, 'adc_n': adc_n,
                't_calc': t_calc, 'error': error, 'passed': passed,
            }

        # ==== Step 4: 记录 & 写入 Excel ====
        now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        for did, proto in devices:
            r = adc_results[did]
            result = {
                'target': target, 'label': label,
                'pv': wb.get_temperature() if wb.get_temperature() is not None else target,
                'adc_mean': r['adc_mean'], 'adc_range': r['adc_range'], 'adc_n': r['adc_n'],
                't_calc': r['t_calc'], 'error': r['error'], 'passed': r['passed'],
            }
            all_results[did].append(result)

            # 写 Excel
            ws, nr = xl_sheets[did]
            try:
                _append_row(xl_wb, ws, nr, result, now_str, proto)
                xl_sheets[did] = (ws, nr + 1)
            except Exception as e:
                print(f"  ⚠ {did} Excel 写入失败: {e}")
                # CSV 后备保存
                csv_path = xlsx_path.replace('.xlsx', f'_{did}_backup.csv')
                try:
                    import csv
                    is_new = not os.path.exists(csv_path)
                    with open(csv_path, 'a', newline='', encoding='utf-8-sig') as cf:
                        wf = csv.writer(cf)
                        if is_new:
                            wf.writerow(['序号','目标°C','水浴°C','原始值','波动','采样数','计算°C','误差°C','通过','时间'])
                        wf.writerow([len(all_results[did]), result['target'], result['pv'],
                                     result['adc_mean'], result['adc_range'], result['adc_n'],
                                     result['t_calc'], result['error'], 'YES' if result['passed'] else 'NO', now_str])
                    print(f"         → 已写入 CSV 备份: {os.path.basename(csv_path)}")
                except Exception as ce:
                    print(f"         → CSV 备份也失败: {ce}")

        # 单点结果
        print(f"\n  {'─'*50}")
        print(f"  📊 第 {disp_i} 点结果:")
        print(f"     目标温度:  {target}°C")
        print(f"     水浴实际:  {wb.get_temperature():.2f}°C" if wb.get_temperature() is not None else f"     水浴实际:  ?°C")
        for did, proto in devices:
            r = adc_results[did]
            icon = '✅' if r['passed'] else '❌'
            if proto == 'old_adc':
                print(f"     {did}: ADC={r['adc_mean']:.1f}  →  {r['t_calc']:.2f}°C" if r['t_calc'] is not None else f"     {did}: ADC={r['adc_mean']:.1f}  无数据")
            else:
                print(f"     {did}: T={r['t_calc']:.2f}°C" if r['t_calc'] is not None else f"     {did}: 无数据")
            print(f"           误差: {r['error']:+.2f}°C  {icon}" if r['error'] is not None else f"           误差: N/A  {icon}")
        print(f"  {'─'*50}")

        # 自动继续
        if disp_i < total_points:
            print(f"\n  3 秒后自动继续下一个点...")
            time.sleep(3)

    # ================================================================
    # 汇总
    # ================================================================
    clear_screen()
    print("=" * 64)
    print("  TTAG 双测 — 汇总")
    print(f"  设备: {devices_str} | 总耗时: {int((time.time() - t0_total) / 60)}min")
    print("=" * 64)
    for did, proto in devices:
        results = all_results[did]
        pass_count = sum(1 for r in results if r['passed'])
        fail_count = len(results) - pass_count
        proto_label = '旧ADC' if proto == 'old_adc' else '新温度'
        print(f"\n  {did} ({proto_label}):")
        print(f"    通过: {pass_count}/{len(results)}  未通过: {fail_count}/{len(results)}")
        errs = [r['error'] for r in results if r['error'] is not None]
        if errs:
            print(f"    误差: {min(errs):+.2f} ~ {max(errs):+.2f}°C")
    print("=" * 64)
    print(f"\n  结果已逐点保存至: {xlsx_path}")

    receiver.stop()
    wb.close()


# ============================================================
# 交互输入
# ============================================================
def input_devices():
    """交互式输入设备列表"""
    print()
    print("=" * 64)
    print("  TTAG 双设备复测程序")
    print("=" * 64)
    print()
    print("  已知设备:")
    for did, cfg in sorted(DEVICE_TABLE.items()):
        proto_str = '旧ADC(ADC→多项式)' if cfg['protocol'] == 'old_adc' else '新温度(直接读数)'
        print(f"    {did} → {proto_str}")
    print()

    while True:
        try:
            raw = input("  请输入设备号（空格分隔，如 '195082 201154'）: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n  已取消")
            return None

        parts = raw.split()
        if not parts:
            print("  输入为空")
            continue

        devices = []
        unknown = []
        for p in parts:
            try:
                did = int(p)
            except ValueError:
                print(f"  '{p}' 不是有效设备号")
                break
            if did in DEVICE_TABLE:
                devices.append((did, DEVICE_TABLE[did]['protocol']))
            else:
                unknown.append(did)
        else:
            if unknown:
                for did in unknown:
                    print(f"\n  设备 {did} 不在已知列表中，请选择协议:")
                    print(f"    [1] 旧协议 (ADC → 多项式 → 温度)")
                    print(f"    [2] 新协议 (标签直接报温度)")
                    while True:
                        try:
                            choice = input(f"  请选择 [{did}]: ").strip()
                            if choice == '1':
                                devices.append((did, 'old_adc'))
                                DEVICE_TABLE[did] = {'protocol': 'old_adc', 'name': str(did)}
                                break
                            elif choice == '2':
                                devices.append((did, 'new_direct'))
                                DEVICE_TABLE[did] = {'protocol': 'new_direct', 'name': str(did)}
                                break
                            else:
                                print("  输入 1 或 2")
                        except (EOFError, KeyboardInterrupt):
                            print("\n  已取消")
                            return None
            break

    if len(devices) < 2:
        print("  至少需要 2 个设备")
        return input_devices()

    print()
    print(f"  设备清单（{len(devices)} 台）:")
    for did, proto in devices:
        proto_str = '旧ADC' if proto == 'old_adc' else '新温度'
        print(f"    {did} → {proto_str}")
    print()

    try:
        ok = input("  确认? [Y/n]: ").strip().lower()
        if ok in ('n', 'no', '否'):
            return input_devices()
    except (EOFError, KeyboardInterrupt):
        print("\n  已取消")
        return None

    return devices


def input_points():
    """交互式输入温度点（复用 ttag_verify.py 逻辑）"""
    print()
    print("=" * 64)
    print("  设定复测温度点")
    print("=" * 64)
    print()
    print("  支持三种输入格式:")
    print("    1. 逐个列举:  -18, -10, 0, 20, 50, 80")
    print("    2. 范围+步长:  5 90 5    (起始 结束 步长)")
    print("    3. 预设分组:  low  /  mid  /  high")
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
        parts = raw.replace(',', ' ').split()

        if raw_lower == 'low':
            points = [(-18, '低温'), (-15, '低温'), (-10, '低温'), (-5, '低温'), (0, '低温')]
            break
        elif raw_lower == 'mid':
            points = [(5, '中温'), (10, '中温'), (20, '中温'), (30, '中温'), (40, '中温')]
            break
        elif raw_lower == 'high':
            points = [(50, '高温'), (60, '高温'), (70, '高温'), (75, '高温'), (80, '高温')]
            break

        try:
            nums = [float(p) for p in parts]
        except ValueError:
            print("  格式错误，请重新输入")
            continue

        for t in nums:
            if t < -30 or t > 100:
                print(f"  ⚠ {t}°C 超出水浴范围 (-30~100°C)，请修正")
                break
        else:
            if len(nums) == 3:
                start, end, step = nums[0], nums[1], nums[2]
                if step == 0:
                    print("  步长不能为 0")
                    continue
                descending = start > end
                step = -abs(step) if descending else abs(step)
                temps = []
                t = start
                while (t >= end - abs(step) / 2) if descending else (t <= end + step / 2):
                    temps.append(round(t, 1))
                    t += step
                temps.sort()
                print(f"\n  范围模式: {start} → {end}  步长 {abs(step):.1f}°C")
                print(f"  共生成 {len(temps)} 个温度点")
            elif len(nums) >= 2:
                temps = nums
            else:
                temps = nums

            points = []
            for t in temps:
                if t <= 0:
                    label = '低温'
                elif t <= 40:
                    label = '中温'
                else:
                    label = '高温'
                points.append((t, label))
            break

    print()
    print(f"  共 {len(points)} 个复测点（从低到高）:")
    for i, (t, label) in enumerate(points, 1):
        print(f"    {i:>2}. {t:>6.1f}°C  [{label}]")
    print()

    try:
        ok = input("  确认? [Y/n]: ").strip().lower()
        if ok in ('n', 'no', '否'):
            return input_points()
    except (EOFError, KeyboardInterrupt):
        print("\n  已取消")
        return None

    return points


# ============================================================
# 主入口
# ============================================================
def main():
    parser = argparse.ArgumentParser(description='TTAG 双设备复测程序')
    parser.add_argument('--connect', default=None, metavar='IP:PORT', help='Client 模式')
    parser.add_argument('--port', type=int, default=20226, help='TCP 端口')
    parser.add_argument('--bath-port', default='COM3', help='水浴串口')
    parser.add_argument('--bath-tolerance', type=float, default=0.3, help='水浴容差')
    parser.add_argument('--stability-samples', type=int, default=5, help='稳定样本数')
    parser.add_argument('--stability-threshold', type=int, default=5, help='峰峰值阈值')
    parser.add_argument('--excel', default=None, metavar='PATH', help='Excel 路径')
    args = parser.parse_args()

    # 输入设备
    devices = input_devices()
    if devices is None:
        return

    device_ids = [d[0] for d in devices]

    # Excel 路径
    if args.excel:
        excel_path = args.excel
    else:
        onedrive_desktop = os.path.join(os.path.expanduser('~'), 'OneDrive', '桌面')
        if not os.path.isdir(onedrive_desktop):
            onedrive_desktop = os.path.join(os.path.expanduser('~'), 'Desktop')
        dev_names = '_'.join(str(d) for d in sorted(device_ids))
        excel_path = os.path.join(onedrive_desktop, f'TTAG_双测_{dev_names}.xlsx')

    # 输入温度点
    points = input_points()
    if points is None:
        return

    # 开始
    while True:
        try:
            run_dual_verify(
                devices=devices,
                points=points,
                connect_to=args.connect,
                port=args.port,
                bath_port=args.bath_port,
                stability_samples=args.stability_samples,
                stability_threshold=args.stability_threshold,
                bath_tolerance=args.bath_tolerance,
                excel_path=excel_path,
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
    try:
        main()
    except BaseException as e:
        print(f"\n\n  ❌ 未捕获异常: {e}")
        import traceback
        traceback.print_exc()
        try:
            input("\n  按 Enter 退出...")
        except Exception:
            pass
