#!/usr/bin/env python3
"""
TTAG 温度标签标定系统 v3.0 — GUI 版
======================================
独立桌面应用：水浴控制 + TTAG 采集 + 实时拟合 + 数据导出
依赖: Python 3.8+ 标准库 + pyserial + openpyxl + numpy
打包: pyinstaller --onefile --windowed --name TTAG_Cal ttag_cal_app.py
"""
import sys, os, time, struct, socket, threading, queue, csv, json, configparser
from datetime import datetime
from collections import deque

# ---- 双设备复测模块 ----
try:
    from ttag_dual_verify import (DEVICE_TABLE, COEFFS, MultiReceiver,
                                   StabilityDetector, calc_temperature,
                                   adc_to_temperature)
except ImportError:
    DEVICE_TABLE = {}
    MultiReceiver = None
    StabilityDetector = None
    calc_temperature = None
    adc_to_temperature = None

# ---- 切换工作目录到脚本所在位置 ----
os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, '.')

import tkinter as tk
from tkinter import ttk, messagebox, filedialog, scrolledtext

# ---- 硬件依赖（打包时自动包含） ----
try:
    from water_bath_control import WaterBath
except ImportError:
    WaterBath = None
try:
    from ttag_monitor import FRAME_HEADER, parse_frame as monitor_parse_frame
except ImportError:
    FRAME_HEADER = bytes([0x55, 0xAA])
    def monitor_parse_frame(data):
        raise NotImplementedError("ttag_monitor not found")

try:
    import serial.tools.list_ports
except ImportError:
    serial = None

try:
    import numpy as np
except ImportError:
    np = None

try:
    from openpyxl import Workbook
except ImportError:
    Workbook = None


# ============================================================
# 配置文件持久化
# ============================================================
CONFIG_FILE = 'ttag_config.ini'

def load_config():
    c = configparser.ConfigParser()
    c.read(CONFIG_FILE, encoding='utf-8')
    return c

def save_config(section, key, value):
    c = load_config()
    if not c.has_section(section):
        c.add_section(section)
    c.set(section, key, str(value))
    with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
        c.write(f)

def get_config(section, key, default=''):
    c = load_config()
    try:
        return c.get(section, key)
    except Exception:
        return default


# ============================================================
# 帧解析（独立实现，不依赖 ttag_monitor）
# ============================================================
def parse_frame(data):
    """解析 55 AA 帧，返回 tag 列表 [{tag_id, adc, rssi, tag_type, temperature}]"""
    tags = []
    try:
        frame = monitor_parse_frame(data)
        if frame.valid:
            for tag in frame.tags:
                tags.append({'tag_id': tag.tag_id, 'adc': tag.adc,
                             'rssi': tag.rssi, 'tag_type': tag.tag_type,
                             'temperature': tag.temperature})
    except Exception:
        pass
    return tags


# ============================================================
# TTAG 接收器（独立实现）
# ============================================================
class TtagReceiver:
    def __init__(self, device_id, host='0.0.0.0', port=20226, connect_to=None):
        self.device_id = device_id
        self.host = host
        self.port = port
        self.connect_to = connect_to
        self.latest_adc = None
        self.latest_rssi = None
        self.latest_temperature = None  # 新协议
        self.last_seen = None
        self.hit_count = 0
        self.frame_count = 0
        self.running = False
        self.connected = False
        self.error_msg = ''
        self._lock = threading.Lock()

    def start(self):
        self.running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self.running = False

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
                self.connected = True
            except Exception as e:
                self.error_msg = f"无法连接基站 {self.connect_to}: {e}"
                sock.close()
                return
        else:
            try:
                sock.bind((self.host, self.port))
                sock.listen(5)
                sock.settimeout(1.0)
            except OSError as e:
                self.error_msg = f"端口 {self.port} 被占用: {e}"
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
                    self.connected = True
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
                    self.connected = False
                except socket.timeout:
                    continue
                except OSError:
                    break
        sock.close()
        self.connected = False

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
                        self.latest_temperature = t.get('temperature')
                        self.last_seen = time.time()
                        self.hit_count += 1

    def get_state(self):
        with self._lock:
            return {'adc': self.latest_adc, 'rssi': self.latest_rssi,
                    'temperature': self.latest_temperature,
                    'last_seen': self.last_seen, 'hits': self.hit_count,
                    'frames': self.frame_count}


# ============================================================
# ADC 稳定性检测器
# ============================================================
class AdcStabilityDetector:
    def __init__(self, min_samples=5, threshold=5):
        self.min_samples = min_samples
        self.threshold = threshold
        self._samples = []

    def feed(self, adc, ts=None):
        if ts is None:
            ts = time.time()
        self._samples.append((ts, adc))

    def check(self):
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
# 多模型拟合引擎
# ============================================================
class FittingEngine:
    """尝试多种模型拟合 ADC->Temperature 数据，返回结果列表"""

    @staticmethod
    def fit_all(adc_list, temp_list):
        """返回 [{model, order/params, max_err, mean_err, status, warning}]"""
        results = []
        if len(adc_list) < 5:
            return [{'model': '数据不足', 'max_err': 0, 'mean_err': 0,
                     'status': 'wait', 'warning': f'仅 {len(adc_list)} 个数据点，需要至少 5 个'}]

        adc_arr = np.array(adc_list, dtype=float)
        temp_arr = np.array(temp_list, dtype=float)

        # 检测离群点
        outliers = FittingEngine._detect_outliers(adc_arr, temp_arr)

        # 1. NTC B-参数模型
        results.append(FittingEngine._fit_b_parameter(adc_arr, temp_arr))

        # 2. 多项式 4/5/6/7 阶
        for order in [4, 5, 6]:
            if len(adc_list) < order + 3:
                results.append({
                    'model': f'{order}阶多项式', 'order': order,
                    'max_err': 0, 'mean_err': 0,
                    'status': 'skip', 'warning': f'数据点不足 {order+3} 个（当前 {len(adc_list)}）'
                })
                continue
            results.append(FittingEngine._fit_polynomial(adc_arr, temp_arr, order))

        # 3. 7阶（需要更多点）
        if len(adc_list) >= 10:
            results.append(FittingEngine._fit_polynomial(adc_arr, temp_arr, 7))
        else:
            results.append({
                'model': '7阶多项式', 'order': 7,
                'max_err': 0, 'mean_err': 0,
                'status': 'skip', 'warning': f'数据点不足 10 个，7阶不可靠'
            })

        # 4. 指数模型
        results.append(FittingEngine._fit_exponential(adc_arr, temp_arr))

        # 5. Steinhart-Hart
        results.append(FittingEngine._fit_steinhart(adc_arr, temp_arr))

        if outliers:
            for r in results:
                if r.get('warning'):
                    r['warning'] += ' | ' + outliers
                else:
                    r['warning'] = outliers

        return results

    @staticmethod
    def _detect_outliers(adc, temp):
        """简单离群检测：残差 > 3倍标准差"""
        if len(adc) < 10:
            return ''
        try:
            coeffs = np.polyfit(adc, temp, 3)
            poly = np.poly1d(coeffs)
            residuals = np.abs(temp - poly(adc))
            threshold = 3 * np.std(residuals)
            outliers = np.where(residuals > threshold)[0]
            if len(outliers) > 0:
                pts = [f'{temp[i]:.1f}°C' for i in outliers[:5]]
                return f'检测到 {len(outliers)} 个疑似离群点: {", ".join(pts)}'
        except Exception:
            pass
        return ''

    @staticmethod
    def _fit_polynomial(adc, temp, order):
        try:
            coeffs = np.polyfit(adc, temp, order)
            poly = np.poly1d(coeffs)
            pred = poly(adc)
            errs = np.abs(temp - pred)
            max_e = float(np.max(errs))
            mean_e = float(np.mean(errs))
            status = 'good' if max_e < 0.1 else ('warn' if max_e < 0.5 else 'bad')
            warning = ''
            if status == 'bad':
                warning = f'最大误差 {max_e:.3f}°C，不建议使用'
            elif status == 'warn':
                warning = f'最大误差 {max_e:.3f}°C，精度一般'
            # 低数据量时高阶警告
            if len(adc) < 30 and order >= 6:
                warning += ' | 数据量较少，高阶拟合可能过拟合'
            # 外推风险
            t_range = max(temp) - min(temp)
            if t_range < 30 and order >= 5:
                warning += f' | 温区仅 {t_range:.0f}°C，高阶外推风险高'
            return {
                'model': f'{order}阶多项式', 'order': order,
                'max_err': max_e, 'mean_err': mean_e,
                'status': status, 'warning': warning,
                'coeffs': coeffs.tolist() if hasattr(coeffs, 'tolist') else list(coeffs),
                'predict_adc': lambda adc_arr, c=coeffs: np.polyval(c, adc_arr),
            }
        except Exception as e:
            return {
                'model': f'{order}阶多项式', 'order': order,
                'max_err': 999, 'mean_err': 999,
                'status': 'error', 'warning': f'拟合失败: {e}'
            }

    @staticmethod
    def _fit_b_parameter(adc, temp):
        """NTC B-参数模型: T = 1/(1/T25 + ln(R/R25)/B)"""
        try:
            R_fixed = 6200.0
            R25 = 10000.0
            T25 = 298.15  # 25°C in Kelvin
            ADC_MAX = 1023.0

            # ADC -> Resistance
            r_ntc = R_fixed * (ADC_MAX / np.clip(adc, 1, ADC_MAX - 1) - 1.0)
            ln_r = np.log(np.clip(r_ntc, 1, 1e9))
            temp_k = temp + 273.15

            # 1/T = 1/T25 + (1/B)*ln(R/R25)
            y = 1.0 / temp_k - 1.0 / T25
            x = ln_r - np.log(R25)

            # 线性拟合求 B
            B_fit = 1.0 / (np.sum(x * y) / np.sum(x * x)) if np.sum(x * x) > 0 else 3380

            # 回算温度
            temp_pred_k = 1.0 / (1.0 / T25 + np.log(r_ntc / R25) / B_fit)
            temp_pred = temp_pred_k - 273.15

            errs = np.abs(temp - temp_pred)
            max_e = float(np.max(errs))
            mean_e = float(np.mean(errs))
            status = 'good' if max_e < 0.1 else ('warn' if max_e < 0.5 else 'bad')
            warning = ''
            if max_e > 0.3:
                warning = 'NTC 实际参数可能与标称值偏差较大'
            if max(temp) - min(temp) < 20:
                warning += ' | 温区太窄，B值拟合不准'

            # predict 闭包
            B_val = B_fit
            def _b_predict(adc_arr, Rf=R_fixed, R25v=R25, Bv=B_val):
                r = Rf * (1023.0 / np.clip(adc_arr, 1, 1022) - 1.0)
                tk = 1.0 / (1.0 / 298.15 + np.log(np.clip(r, 1, 1e9) / R25v) / Bv)
                return tk - 273.15

            return {
                'model': 'NTC B-参数', 'order': None,
                'max_err': max_e, 'mean_err': mean_e,
                'status': status, 'warning': warning,
                'params': {'B': round(B_fit, 1), 'R25': R25, 'R_fixed': R_fixed},
                'predict_adc': _b_predict,
            }
        except Exception as e:
            return {
                'model': 'NTC B-参数', 'order': None,
                'max_err': 999, 'mean_err': 999,
                'status': 'error', 'warning': f'B-参数拟合失败: {e}'
            }

    @staticmethod
    def _fit_steinhart(adc, temp):
        """Steinhart-Hart: 1/T = A + B*ln(R) + C*ln(R)^3"""
        try:
            R_fixed = 6200.0
            ADC_MAX = 1023.0
            r_ntc = R_fixed * (ADC_MAX / np.clip(adc, 1, ADC_MAX - 1) - 1.0)
            ln_r = np.log(np.clip(r_ntc, 1, 1e9))
            temp_k = temp + 273.15
            y = 1.0 / temp_k

            # 线性最小二乘: y = A + B*ln_r + C*ln_r^3
            X = np.column_stack([np.ones_like(ln_r), ln_r, ln_r ** 3])
            coeffs, residuals, rank, singular = np.linalg.lstsq(X, y, rcond=None)
            A, B, C = coeffs[0], coeffs[1], coeffs[2]

            temp_pred_k = 1.0 / (A + B * ln_r + C * ln_r ** 3)
            temp_pred = temp_pred_k - 273.15

            errs = np.abs(temp - temp_pred)
            max_e = float(np.max(errs))
            mean_e = float(np.mean(errs))
            status = 'good' if max_e < 0.1 else ('warn' if max_e < 0.5 else 'bad')
            warning = ''
            if max_e > 0.2:
                warning = f'Steinhart-Hart 误差偏大，可能是数据噪声'
            # 检查是否收敛
            if C == 0 or abs(C) < 1e-12:
                warning += ' | C 系数接近零，模型退化为对数线性'

            A_v, B_v, C_v = float(A), float(B), float(C)
            def _sh_predict(adc_arr, Rf=R_fixed, Av=A_v, Bv=B_v, Cv=C_v):
                r = Rf * (1023.0 / np.clip(adc_arr, 1, 1022) - 1.0)
                lnr = np.log(np.clip(r, 1, 1e9))
                tk = 1.0 / (Av + Bv * lnr + Cv * lnr ** 3)
                return tk - 273.15

            return {
                'model': 'Steinhart-Hart', 'order': None,
                'max_err': max_e, 'mean_err': mean_e,
                'status': status, 'warning': warning,
                'params': {'A': A_v, 'B': B_v, 'C': C_v},
                'predict_adc': _sh_predict,
            }
        except np.linalg.LinAlgError as e:
            return {
                'model': 'Steinhart-Hart', 'order': None,
                'max_err': 999, 'mean_err': 999,
                'status': 'error', 'warning': f'矩阵求解失败，数据可能线性相关: {e}'
            }
        except Exception as e:
            return {
                'model': 'Steinhart-Hart', 'order': None,
                'max_err': 999, 'mean_err': 999,
                'status': 'error', 'warning': f'Steinhart-Hart 拟合异常: {e}'
            }

    @staticmethod
    def _fit_exponential(adc, temp):
        """指数模型: T = a * exp(b * ADC) + c"""
        try:
            from scipy.optimize import curve_fit

            def exp_func(x, a, b, c):
                return a * np.exp(b * x) + c

            # 初始猜测
            p0 = [80, -0.001, -20]
            popt, _ = curve_fit(exp_func, adc, temp, p0=p0, maxfev=5000)
            pred = exp_func(adc, *popt)
            errs = np.abs(temp - pred)
            max_e = float(np.max(errs))
            mean_e = float(np.mean(errs))
            status = 'good' if max_e < 0.1 else ('warn' if max_e < 0.5 else 'bad')
            warning = ''
            if max_e > 0.3:
                warning = f'指数模型在该温区偏差较大'
            def _exp_predict(adc_arr, a=float(popt[0]), b=float(popt[1]), c=float(popt[2])):
                return a * np.exp(b * adc_arr) + c

            return {
                'model': '指数', 'order': None,
                'max_err': max_e, 'mean_err': mean_e,
                'status': status, 'warning': warning,
                'params': {'a': float(popt[0]), 'b': float(popt[1]), 'c': float(popt[2])},
                'predict_adc': _exp_predict,
            }
        except ImportError:
            return {
                'model': '指数', 'order': None,
                'max_err': 999, 'mean_err': 999,
                'status': 'error', 'warning': '需要 scipy 库（未安装）'
            }
        except Exception as e:
            return {
                'model': '指数', 'order': None,
                'max_err': 999, 'mean_err': 999,
                'status': 'error', 'warning': f'指数拟合不收敛: {e}'
            }


# ============================================================
# 标定后台线程
# ============================================================
class CalibrationThread(threading.Thread):
    """在后台运行标定循环，通过 queue 推送状态更新到 UI"""

    def __init__(self, params, status_queue):
        super().__init__(daemon=True)
        self.params = params
        self.q = status_queue
        self.paused = threading.Event()
        self.paused.set()  # 初始为运行状态
        self.stopped = threading.Event()
        self.records = []
        self.fit_results = []

    def run(self):
        params = self.params
        try:
            # 连接水浴
            self._push('log', '正在连接水浴箱...')
            try:
                wb = WaterBath(port=params['water_bath_port'])
            except Exception as e:
                self._push('error', f'水浴连接失败 ({params["water_bath_port"]}): {e}')
                return
            pv = wb.get_temperature()
            sv = wb.get_setpoint()
            self._push('log', f'水浴已连接: PV={pv:.2f}°C, SV={sv}°C' if pv else '水浴已连接(PV读取中...)')

            # 连接 TTAG
            ttag = None
            if not params.get('no_ttag'):
                ttag = TtagReceiver(
                    params['device_id'],
                    port=params['ttag_port'],
                    connect_to=params.get('connect_to')
                )
                ttag.start()
                ttag_err = ttag.error_msg
                if ttag_err:
                    self._push('log', f'TTAG 启动警告: {ttag_err}')
                self._push('log', f'等待基站连接 (设备 {params["device_id"]}, 端口 {params["ttag_port"]})...')
                for i in range(60):
                    if self.stopped.is_set():
                        break
                    time.sleep(0.5)
                    st = ttag.get_state()
                    if i % 4 == 0:  # 每 2 秒更新一次等待状态
                        self._push('log', f'等待基站... ({i*0.5:.0f}s) 已收 {st.get("frames", 0)} 帧')
                    if st.get('frames', 0) > 0:
                        self._push('log', f'基站已连接: {st["frames"]} 帧, {st["hits"]} 次命中')
                        break
                else:
                    self._push('log', f'警告: 30秒内未收到基站数据 (收到 {ttag.get_state().get("frames", 0)} 帧)')

            adc_det = AdcStabilityDetector(params['stability_samples'], params['stability_threshold'])
            records = list(params.get('prev_records', []))
            temps = params['temps']
            total = len(temps) + len(params.get('completed_set', []))
            t0_total = time.time()

            for i, target in enumerate(temps):
                # 检查暂停/停止
                while self.paused.is_set() is False and not self.stopped.is_set():
                    time.sleep(0.1)
                if self.stopped.is_set():
                    break

                wb.set_temperature(target)
                done = len(records)
                disp_i = i + 1 + len(params.get('completed_set', []))

                # ---- 等水浴稳定 ----
                t1 = time.time()
                bath_ok = False
                pv_first = None
                need_cool = False
                nudge_sv = None
                nudge_t = 0.0
                last_pwr_zero = 0.0

                while time.time() - t1 < 900 and not self.stopped.is_set():
                    while self.paused.is_set() is False and not self.stopped.is_set():
                        time.sleep(0.1)
                    if self.stopped.is_set():
                        break

                    pv = wb.get_temperature()
                    pwr = wb.get_status()
                    ts = ttag.get_state() if ttag else {}
                    elapsed_t = time.time() - t0_total
                    new_done = done - len(params.get('prev_records', []))
                    eta = (elapsed_t / max(new_done, 1)) * (total - done) if new_done > 0 else 0

                    # pv 无效时跳过本轮（Modbus 偶发错误）
                    if pv is None:
                        self._push('log', '⚠ 水浴读取异常，重试中...')
                        time.sleep(0.8)
                        continue

                    if pv_first is None:
                        pv_first = pv
                        need_cool = pv > target + params['bath_tolerance']

                    # nudge logic
                    if pv is not None and nudge_sv is None and time.time() - t1 > 20:
                        gap = abs(pv - target)
                        if not need_cool and pv < target - params['bath_tolerance'] and pwr is not None and pwr == 0:
                            if last_pwr_zero == 0:
                                last_pwr_zero = time.time()
                            elif time.time() - last_pwr_zero > 15 and gap > 0.15:
                                nudge_sv = min(100, target + 2.0)
                                wb.set_temperature(nudge_sv)
                                nudge_t = time.time()
                                last_pwr_zero = 0
                        elif need_cool and pv > target + params['bath_tolerance'] and pwr is not None and pwr == 0:
                            if last_pwr_zero == 0:
                                last_pwr_zero = time.time()
                            elif time.time() - last_pwr_zero > 15 and gap > 0.15:
                                nudge_sv = max(-30, target - 2.0)
                                wb.set_temperature(nudge_sv)
                                nudge_t = time.time()
                                last_pwr_zero = 0
                        else:
                            last_pwr_zero = 0

                    if nudge_sv is not None:
                        if abs(pv - target) <= params['bath_tolerance']:
                            wb.set_temperature(target)
                            nudge_sv = None
                        elif time.time() - nudge_t > 120:
                            wb.set_temperature(target)
                            nudge_sv = None

                    # reached
                    if pv is not None:
                        if need_cool:
                            reached = pv <= target + params['bath_tolerance']
                        else:
                            reached = pv >= target - params['bath_tolerance']

                        # 推送状态
                        self._push('status', {
                            'target': target, 'pv': pv, 'pwr': pwr,
                            'adc': ts.get('adc'), 'rssi': ts.get('rssi'),
                            'hits': ts.get('hits', 0), 'frames': ts.get('frames', 0),
                            'done': done, 'total': total, 'disp_i': disp_i,
                            'elapsed': elapsed_t, 'eta': eta,
                            'need_cool': need_cool, 'nudge_sv': nudge_sv,
                            'phase': 'bath', 'd': abs(pv - target) if pv else 0,
                            'reached': reached and abs(pv - target) <= params['bath_tolerance'],
                        })

                        # 稳定判定
                        if reached and abs(pv - target) <= params['bath_tolerance'] and time.time() - t1 > 10:
                            if nudge_sv is not None:
                                wb.set_temperature(target)
                                nudge_sv = None
                            pv_before = pv
                            time.sleep(2)
                            pv2 = wb.get_temperature()
                            if pv2 is not None:
                                drift = abs(pv2 - pv_before) if pv_before is not None else 999
                                if need_cool:
                                    ok = (pv2 <= target + params['bath_tolerance']
                                          and abs(pv2 - target) <= params['bath_tolerance']
                                          and drift < 0.03)
                                else:
                                    ok = (pv2 >= target - params['bath_tolerance']
                                          and abs(pv2 - target) <= params['bath_tolerance']
                                          and drift < 0.03)
                                if ok:
                                    bath_ok = True
                                    break
                    time.sleep(0.4)

                if not bath_ok:
                    # 超时：重试当前温度（最多3次），不跳过
                    retry = 0
                    while not bath_ok and retry < 3:
                        retry += 1
                        self._push('log', f'⚠ {target}°C 超时，第{retry}次重试...')
                        wb.set_temperature(target)
                        time.sleep(2)
                        t1 = time.time()
                        while time.time() - t1 < 900 and not self.stopped.is_set():
                            pv = wb.get_temperature()
                            if pv is None:
                                time.sleep(0.8)
                                continue
                            if pv_first is None:
                                pv_first = pv
                                need_cool = pv > target + params['bath_tolerance']
                            if need_cool:
                                reached = pv <= target + params['bath_tolerance']
                            else:
                                reached = pv >= target - params['bath_tolerance']
                            if reached and abs(pv - target) <= params['bath_tolerance'] and time.time() - t1 > 10:
                                time.sleep(2)
                                pv2 = wb.get_temperature()
                                if pv2 is not None:
                                    drift = abs(pv2 - pv)
                                    if need_cool:
                                        ok = (pv2 <= target + params['bath_tolerance']
                                              and abs(pv2 - target) <= params['bath_tolerance']
                                              and drift < 0.03)
                                    else:
                                        ok = (pv2 >= target - params['bath_tolerance']
                                              and abs(pv2 - target) <= params['bath_tolerance']
                                              and drift < 0.03)
                                    if ok:
                                        bath_ok = True
                                        break
                            time.sleep(0.4)
                    if not bath_ok:
                        self._push('log', f'❌ {target}°C 多次重试失败，跳过。检查水浴!')
                    continue

                # ---- 等 TTAG ADC 稳定 ----
                if ttag is not None:
                    adc_det.reset()
                    t2 = time.time()
                    adc_ok = False
                    last_hits = ttag.get_state().get('hits', 0)

                    while time.time() - t2 < 240 and not self.stopped.is_set():
                        while self.paused.is_set() is False and not self.stopped.is_set():
                            time.sleep(0.1)
                        if self.stopped.is_set():
                            break

                        pv = wb.get_temperature()
                        pwr = wb.get_status()
                        ts = ttag.get_state()
                        adc_v = ts.get('adc')
                        cur_hits = ts.get('hits', 0)
                        elapsed_t = time.time() - t0_total
                        new_done = done - len(params.get('prev_records', []))
                        eta = (elapsed_t / max(new_done, 1)) * (total - done) if new_done > 0 else 0

                        if adc_v is not None and cur_hits != last_hits:
                            adc_det.feed(adc_v)
                            last_hits = cur_hits

                        stable, mean, rng, n, span = adc_det.check()

                        self._push('status', {
                            'target': target, 'pv': pv, 'pwr': pwr,
                            'adc': adc_v, 'rssi': ts.get('rssi'),
                            'hits': ts.get('hits', 0), 'frames': ts.get('frames', 0),
                            'done': done, 'total': total, 'disp_i': disp_i,
                            'elapsed': elapsed_t, 'eta': eta,
                            'need_cool': need_cool, 'nudge_sv': nudge_sv,
                            'phase': 'adc', 'adc_n': n, 'adc_stable': stable,
                            'adc_mean': mean, 'adc_rng': rng,
                        })

                        if stable:
                            adc_mean, adc_range, adc_n = mean, rng, n
                            adc_ok = True
                            break
                        time.sleep(0.3)

                    if not adc_ok:
                        _, mean, rng, n, _ = adc_det.check()
                        ts2 = ttag.get_state()
                        adc_mean = mean if mean is not None else ts2.get('adc') if ts2.get('adc') is not None else 0
                        adc_range = rng if rng is not None else 0
                        adc_n = n
                    adc_elapsed = time.time() - t2
                else:
                    adc_mean = adc_range = adc_n = adc_elapsed = 0

                # ---- 记录 ----
                pv = wb.get_temperature()
                pv_now = pv if pv is not None else target
                ts_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                records.append({
                    'target': target, 'actual': pv_now,
                    'adc_mean': adc_mean, 'adc_range': adc_range,
                    'adc_n': adc_n, 'elapsed': adc_elapsed, 'ts': ts_str,
                })
                self.records = records

                # 每 5 个点重新拟合并更新曲线
                if len(records) % 5 == 0 and len(records) >= 5:
                    adcs = [r['adc_mean'] for r in records]
                    temps_r = [r['target'] for r in records]
                    self.fit_results = FittingEngine.fit_all(adcs, temps_r)
                    self._push('fit_update', self.fit_results)

                # 保存 CSV
                self._save_csv(records, params)
                if len(records) % 5 == 0:
                    self._save_excel(records, params)

                time.sleep(0.5)

            # 标定结束
            self._save_excel(records, params)
            adcs = [r['adc_mean'] for r in records]
            temps_r = [r['target'] for r in records]
            self.fit_results = FittingEngine.fit_all(adcs, temps_r)
            self._push('fit_update', self.fit_results)
            self._push('done', {'records': len(records), 'output': params['output']})

        except Exception as e:
            self._push('error', str(e))
        finally:
            # 无论如何都要保存数据
            try:
                self._save_excel(self.records, params)
                self._push('log', f'数据已保存: {len(self.records)} 个点')
            except Exception as ex:
                self._push('log', f'保存失败: {ex}')
            try:
                wb.close()
            except Exception:
                pass
            if ttag:
                ttag.stop()

    def _push(self, msg_type, data):
        try:
            self.q.put_nowait({'type': msg_type, 'data': data})
        except queue.Full:
            pass

    def _save_csv(self, records, params):
        csv_path = params['output'].replace('.xlsx', '.csv')
        try:
            file_exists = os.path.exists(csv_path)
            with open(csv_path, 'a', newline='', encoding='utf-8-sig') as f:
                writer = csv.writer(f)
                if not file_exists:
                    writer.writerow(["#", "TagID", "Target(C)", "Actual(C)", "ADC_Mean",
                                     "ADC_Range", "ADC_Samples", "StableTime(s)", "Timestamp", "Note"])
                r = records[-1]
                writer.writerow([len(records), params['device_id'], r['target'], r['actual'],
                                 round(r['adc_mean'], 1), r['adc_range'], r['adc_n'],
                                 round(r['elapsed']), r['ts'], ''])
        except Exception as e:
            self._push('log', f'CSV 写入失败: {e}')

    def _save_excel(self, records, params):
        if Workbook is None:
            return
        try:
            wb = Workbook()
            ws = wb.active
            ws.title = "TTAG Calibration"
            ws.append(["#", "TagID", "Target(C)", "Actual(C)", "ADC_Mean",
                       "ADC_Range", "ADC_Samples", "StableTime(s)", "Timestamp", "Note"])
            for i, r in enumerate(records, 1):
                ws.append([i, params['device_id'], r['target'], r['actual'],
                          round(r['adc_mean'], 1), r['adc_range'], r['adc_n'],
                          round(r['elapsed']), r['ts'], r.get('note', '')])
            wb.save(params['output'])
        except Exception as e:
            self._push('log', f'Excel 保存失败: {e}')

    def pause(self):
        self.paused.clear()

    def resume(self):
        self.paused.set()

    def stop(self):
        self.stopped.set()
        self.paused.set()  # 确保不会卡在等待


# ============================================================
# 实时曲线 Canvas
# ============================================================
class CurveCanvas(tk.Canvas):
    """用 tkinter Canvas 画实时 ADC-温度曲线"""

    def __init__(self, parent, **kwargs):
        super().__init__(parent, bg='white', highlightthickness=0, **kwargs)
        self.data_points = []  # [(adc, temp), ...]
        self.fit_line = None  # 当前选中的拟合线数据
        self.margin = {'left': 55, 'right': 20, 'top': 20, 'bottom': 35}
        self.bind('<Configure>', self._on_resize)

    def set_data(self, records):
        """设置数据点"""
        self.data_points = [(r['adc_mean'], r['target']) for r in records
                            if r.get('adc_mean', 0) > 0]
        self.draw()

    def set_fit(self, fit_result):
        """设置拟合线"""
        self.fit_line = fit_result
        self.draw()

    def _on_resize(self, event):
        self.draw()

    def draw(self):
        self.delete('all')
        w = self.winfo_width()
        h = self.winfo_height()
        if w < 50 or h < 50:
            return

        m = self.margin
        plot_w = w - m['left'] - m['right']
        plot_h = h - m['top'] - m['bottom']

        if not self.data_points:
            self.create_text(w // 2, h // 2, text='等待数据...', fill='gray', font=('', 12))
            return

        adcs = [p[0] for p in self.data_points]
        temps = [p[1] for p in self.data_points]
        adc_min, adc_max = min(adcs), max(adcs)
        temp_min, temp_max = min(temps), max(temps)

        # 留 5% 边距
        adc_range = max(adc_max - adc_min, 1)
        temp_range = max(temp_max - temp_min, 1)
        adc_pad = adc_range * 0.05
        temp_pad = temp_range * 0.05

        def tx(adc_val):
            return m['left'] + (adc_val - adc_min + adc_pad) / (adc_range + 2 * adc_pad) * plot_w

        def ty(temp_val):
            return m['top'] + (temp_max + temp_pad - temp_val) / (temp_range + 2 * temp_pad) * plot_h

        # 坐标轴
        self.create_line(m['left'], m['top'], m['left'], m['top'] + plot_h, fill='black', width=2)
        self.create_line(m['left'], m['top'] + plot_h, m['left'] + plot_w, m['top'] + plot_h, fill='black', width=2)

        # 轴标签
        self.create_text(w // 2, h - 5, text='ADC', font=('', 10))
        self.create_text(12, h // 2, text='T(°C)', font=('', 10), angle=90)

        # 网格
        for i in range(5):
            yy = m['top'] + plot_h * i / 4
            self.create_line(m['left'], yy, m['left'] + plot_w, yy, fill='#e0e0e0', dash=(2, 4))
            val = temp_max - (temp_max - temp_min) * i / 4
            self.create_text(m['left'] - 5, yy, text=f'{val:.0f}', anchor='e', font=('', 8))

        for i in range(5):
            xx = m['left'] + plot_w * i / 4
            self.create_line(xx, m['top'], xx, m['top'] + plot_h, fill='#e0e0e0', dash=(2, 4))
            val = adc_min + (adc_max - adc_min) * i / 4
            self.create_text(xx, m['top'] + plot_h + 3, text=f'{val:.0f}', anchor='n', font=('', 8))

        # 数据点
        for adc_v, temp_v in self.data_points:
            x, y = tx(adc_v), ty(temp_v)
            self.create_oval(x - 2, y - 2, x + 2, y + 2, fill='steelblue', outline='')

        # 拟合线
        if self.fit_line and self.fit_line.get('status') in ('good', 'warn'):
            try:
                fit_adcs = np.linspace(adc_min - adc_pad, adc_max + adc_pad, 200)
                predict_fn = self.fit_line.get('predict_adc')
                if predict_fn is not None:
                    fit_temps = predict_fn(fit_adcs)
                elif self.fit_line.get('coeffs') is not None:
                    # 兼容旧格式（多项式但没有 predict_adc）
                    fit_temps = np.polyval(self.fit_line['coeffs'], fit_adcs)
                else:
                    fit_temps = None

                if fit_temps is not None:
                    points = []
                    for i in range(len(fit_adcs)):
                        x, y = tx(fit_adcs[i]), ty(fit_temps[i])
                        if m['left'] <= x <= m['left'] + plot_w and m['top'] <= y <= m['top'] + plot_h:
                            points.extend([x, y])
                    if len(points) >= 4:
                        self.create_line(*points, fill='red', width=2, smooth=True)
            except Exception:
                pass


# ============================================================
# 主窗口
# ============================================================
class VerifyThread(threading.Thread):
    """Dual-device verification thread — runs water bath + base station + collects data"""
    def __init__(self, params, status_queue):
        super().__init__(daemon=True)
        self.params = params
        self.q = status_queue
        self.paused = threading.Event()
        self.paused.clear()
        self.stopped = threading.Event()

    def _push(self, msg_type, data):
        try:
            self.q.put_nowait({'type': msg_type, **data})
        except queue.Full:
            pass

    def run(self):
        import time
        from datetime import datetime
        params = self.params
        devices = params['devices']
        device_ids = [d[0] for d in devices]

        try:
            # Connect water bath
            self._push('log', {'text': 'Connecting water bath...'})
            wb = WaterBath(port=params['bath_port'])
            pv = wb.get_temperature()
            self._push('log', {'text': f'Water bath OK: PV={pv:.2f}C' if pv else 'Water bath connected'})

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
            onedrive = os.path.join(os.path.expanduser('~'), 'OneDrive', 'desktop')
            if not os.path.isdir(onedrive):
                onedrive = os.path.join(os.path.expanduser('~'), 'Desktop')
            dev_names = '_'.join(str(d) for d in sorted(device_ids))
            xlsx_path = os.path.join(onedrive, f'TTAG_dual_{dev_names}.xlsx')
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

                # Wait for stability
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
                    for did in device_ids:
                        all_results[did].append({
                            'target': target, 'pv': None, 'adc_mean': None, 'adc_range': None,
                            'adc_n': 0, 't_calc': None, 'error': None, 'passed': False,
                            'time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                        })
                    self._push('log', {'text': f'Bath timeout at {target}C, skipping'})
                    continue

                # Collect data from active devices
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

                # Record results
                for did, proto in active:
                    _, mean, rng, n, _ = detectors[did].check()
                    st = receiver.get_state(did)
                    adc_mean = mean if mean is not None else (st.get('adc') or 0)
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

                # Save Excel
                self._save_excel(xlsx_path, devices, all_results)

            self._push('complete', {'results': all_results, 'xlsx': xlsx_path})

        except Exception as e:
            self._push('error', {'text': str(e)})
            import traceback
            traceback.print_exc()

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
            sname = f'{did} {"ADC Verify" if proto == "old_adc" else "Temp Verify"}'
            if sname not in wb.sheetnames:
                ws = wb.create_sheet(sname)
                ws.merge_cells('A1:J1')
                ws['A1'] = f'TTAG {did} Verify Results'
                ws['A1'].font = Font(bold=True, size=14)
                ws.merge_cells('A2:J2')
                ws['A2'] = f'+/-1.0C  |  {"ADC to Polynomial" if proto == "old_adc" else "Direct Temperature"}'
                hdrs = ['#','Target C','Bath C','Raw','Delta','n','Calc C','Error C','Pass','Time']
                for ci, h in enumerate(hdrs, 1):
                    c = ws.cell(row=4, column=ci, value=h)
                    c.font = Font(bold=True, size=11, color='FFFFFF')
                    c.fill = hdr_fill
                    c.alignment = Alignment(horizontal='center')
                    c.border = thin
                widths = [6,12,14,10,10,6,12,10,12,20]
                for ci, w in enumerate(widths, 1):
                    ws.column_dimensions[chr(64+ci)].width = w

            ws = wb[sname]
            results = all_results.get(did, [])
            for j, r in enumerate(results):
                ri = 5 + j
                vals = [j+1, r['target'], r['pv'] if r['pv'] else '',
                        r['adc_mean'] if r['adc_mean'] else '',
                        r['adc_range'] if r['adc_range'] else '',
                        r['adc_n'],
                        round(r['t_calc'],2) if r['t_calc'] else '',
                        round(r['error'],2) if r['error'] else '',
                        'YES' if r['passed'] else 'NO',
                        r.get('time', '')]
                for ci, v in enumerate(vals, 1):
                    c = ws.cell(row=ri, column=ci, value=v)
                    c.alignment = Alignment(horizontal='center')
                    c.border = thin
                    c.fill = ok_fill if r['passed'] else ng_fill

        wb.save(path)


class MainWindow(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title('TTAG 温度标签标定系统 v3.0')
        self.geometry('1100x750')
        self.minsize(900, 600)

        self.cal_thread = None
        self.verify_thread = None
        self.status_queue = queue.Queue(maxsize=100)
        self.fit_results = []
        self.mode_var = tk.StringVar(value='calibrate')

        self._build_ui()
        self._load_config()

        # 每 200ms 从后台线程拉状态
        self._poll_status()

        self.protocol('WM_DELETE_WINDOW', self._on_close)

    # ========================================
    # UI 构建
    # ========================================
    def _build_ui(self):
        # Vertical split: controls | data table
        self.paned = tk.PanedWindow(self, orient=tk.VERTICAL, sashrelief=tk.RAISED, sashwidth=6)
        self.paned.pack(fill='both', expand=True)

        self.top_frame = ttk.Frame(self.paned, padding=8)
        self.bottom_frame = ttk.Frame(self.paned)

        self.paned.add(self.top_frame, minsize=200, stretch='always')
        self.paned.add(self.bottom_frame, minsize=100, stretch='always')

        main_frame = self.top_frame  # alias for existing code to work

        # Mode toggle bar
        mode_bar = ttk.Frame(main_frame)
        mode_bar.pack(fill='x', pady=(0, 6))

        ttk.Label(mode_bar, text='Mode:', font=('', 10)).pack(side='left', padx=(0, 8))

        ttk.Radiobutton(mode_bar, text='Calibrate (Single)', variable=self.mode_var,
                        value='calibrate', command=self._on_mode_change).pack(side='left', padx=3)

        ttk.Radiobutton(mode_bar, text='Verify (Multi-Device)', variable=self.mode_var,
                        value='verify', command=self._on_mode_change).pack(side='left', padx=3)

        # 顶部: 设置区
        self._build_settings(main_frame)

        # 中部: 控制按钮 + 状态
        self._build_controls(main_frame)

        # 底部: 曲线 + 拟合面板
        bottom = ttk.Frame(main_frame)
        bottom.pack(fill='both', expand=True, pady=(8, 0))

        # 左下: 曲线
        self.curve = CurveCanvas(bottom, width=600, height=300)
        self.curve.pack(side='left', fill='both', expand=True)

        # 右下: 拟合面板
        self._build_fit_panel(bottom)

        # Build data table in bottom frame
        self._build_data_table()

    def _build_settings(self, parent):
        """构建设置区域"""
        self.cal_frame = ttk.LabelFrame(parent, text='连接与参数', padding=8)
        self.cal_frame.pack(fill='x')

        # 第一行: 连接设置
        row1 = ttk.Frame(self.cal_frame)
        row1.pack(fill='x', pady=2)

        ttk.Label(row1, text='设备ID:').pack(side='left')
        self.device_id_var = tk.StringVar(value='195082')
        ttk.Entry(row1, textvariable=self.device_id_var, width=8).pack(side='left', padx=(2, 15))

        # 连接模式
        self.conn_mode_var = tk.StringVar(value='server')
        ttk.Radiobutton(row1, text='服务器监听', variable=self.conn_mode_var, value='server',
                        command=self._toggle_conn_mode).pack(side='left')
        ttk.Radiobutton(row1, text='连接基站', variable=self.conn_mode_var, value='client',
                        command=self._toggle_conn_mode).pack(side='left', padx=(0, 5))

        ttk.Label(row1, text='端口:').pack(side='left')
        self.ttag_port_var = tk.StringVar(value='20226')
        ttk.Entry(row1, textvariable=self.ttag_port_var, width=6).pack(side='left', padx=(2, 5))

        self.client_host_var = tk.StringVar(value='192.168.3.188')
        self.client_host_entry = ttk.Entry(row1, textvariable=self.client_host_var, width=14)
        ttk.Label(row1, text='IP:').pack(side='left')
        self.client_host_entry.pack(side='left', padx=(2, 15))

        ttk.Label(row1, text='串口:').pack(side='left')
        self.com_port_var = tk.StringVar(value='COM3')
        self.com_combo = ttk.Combobox(row1, textvariable=self.com_port_var, width=8,
                                       values=self._scan_ports())
        self.com_combo.pack(side='left', padx=(2, 3))
        ttk.Button(row1, text='扫描', width=4, command=self._refresh_ports).pack(side='left')

        self.bath_status_var = tk.StringVar(value='未连接')
        ttk.Label(row1, textvariable=self.bath_status_var, foreground='gray').pack(side='left', padx=(10, 0))

        # 第二行: 标定参数
        row2 = ttk.Frame(self.cal_frame)
        row2.pack(fill='x', pady=(6, 2))

        ttk.Label(row2, text='起始:').pack(side='left')
        self.start_var = tk.StringVar(value='47.0')
        ttk.Entry(row2, textvariable=self.start_var, width=6).pack(side='left', padx=(2, 5))
        ttk.Label(row2, text='°C  →').pack(side='left')

        ttk.Label(row2, text='结束:').pack(side='left', padx=(8, 0))
        self.end_var = tk.StringVar(value='9.0')
        ttk.Entry(row2, textvariable=self.end_var, width=6).pack(side='left', padx=(2, 5))
        ttk.Label(row2, text='°C').pack(side='left')

        ttk.Label(row2, text='步长:').pack(side='left', padx=(10, 0))
        self.step_var = tk.StringVar(value='0.2')
        ttk.Entry(row2, textvariable=self.step_var, width=5).pack(side='left', padx=(2, 5))
        ttk.Label(row2, text='°C').pack(side='left')

        ttk.Label(row2, text='容差±:').pack(side='left', padx=(10, 0))
        self.tol_var = tk.StringVar(value='0.10')
        ttk.Entry(row2, textvariable=self.tol_var, width=5).pack(side='left', padx=(2, 5))

        ttk.Label(row2, text='ADC样本:').pack(side='left', padx=(5, 0))
        self.adc_samples_var = tk.StringVar(value='5')
        ttk.Entry(row2, textvariable=self.adc_samples_var, width=4).pack(side='left', padx=(2, 5))

        ttk.Label(row2, text='阈值≤:').pack(side='left')
        self.adc_thresh_var = tk.StringVar(value='5')
        ttk.Entry(row2, textvariable=self.adc_thresh_var, width=4).pack(side='left', padx=(2, 0))

        self.save_config_btn = ttk.Button(row2, text='保存设置', command=self._save_config)
        self.save_config_btn.pack(side='right', padx=(10, 0))

        # Verify settings (new, hidden by default)
        self.verify_frame = ttk.LabelFrame(parent, text='Verify — Devices & Points', padding=8)
        # Don't pack it yet — hidden initially
        self._build_verify_panel()

    def _build_verify_panel(self):
        parent = self.verify_frame

        # Device list header
        hdr = ttk.Frame(parent)
        hdr.pack(fill='x')
        ttk.Label(hdr, text='Devices:', font=('', 10, 'bold')).pack(side='left')
        ttk.Button(hdr, text='+ Add Device', command=self._add_device_row).pack(side='right')

        # Scrollable device list area
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
        # Preload from DEVICE_TABLE
        for did, cfg in sorted(DEVICE_TABLE.items()):
            self._add_device_row(did, cfg['protocol'], cfg.get('step', 0))

        # Separator
        ttk.Separator(parent, orient='horizontal').pack(fill='x', pady=8)

        # Preset buttons
        preset_frame = ttk.Frame(parent)
        preset_frame.pack(fill='x', pady=(0, 4))
        ttk.Label(preset_frame, text='Presets:', font=('', 9)).pack(side='left', padx=(0, 8))

        presets = [
            ('Low (-20~0)', [-20, -15, -10, -5, 0]),
            ('Mid (0~40)', [0, 5, 10, 15, 20, 25, 30, 35, 40]),
            ('High (40~90)', [40, 50, 60, 70, 80, 90]),
            ('5 deg C Step', list(range(5, 91, 5))),
        ]
        for label, temps in presets:
            btn = ttk.Button(preset_frame, text=label,
                             command=lambda t=temps: self._preset_temps(t))
            btn.pack(side='left', padx=2)

        # Temp text area
        ttk.Label(parent, text='Temperature points (comma/space separated):',
                  font=('', 9)).pack(anchor='w', pady=(6, 2))
        self.temp_text = tk.Text(parent, height=3, width=70, font=('Consolas', 10))
        self.temp_text.pack(fill='x')
        self.temp_text.bind('<KeyRelease>', lambda e: self._update_temp_preview())

        # Preview line
        self.temp_preview_var = tk.StringVar(value='0 points')
        ttk.Label(parent, textvariable=self.temp_preview_var,
                  foreground='#666', font=('', 9)).pack(anchor='w', pady=(2, 0))

        # Parameter row
        param_row = ttk.Frame(parent)
        param_row.pack(fill='x', pady=6)

        ttk.Label(param_row, text='Tol:').pack(side='left')
        self.verify_tol_var = tk.StringVar(value='0.3')
        ttk.Entry(param_row, textvariable=self.verify_tol_var, width=5).pack(side='left', padx=(2, 12))

        ttk.Label(param_row, text='Samples:').pack(side='left')
        self.verify_samples_var = tk.StringVar(value='5')
        ttk.Entry(param_row, textvariable=self.verify_samples_var, width=4).pack(side='left', padx=(2, 12))

        ttk.Label(param_row, text='Threshold:').pack(side='left')
        self.verify_thresh_var = tk.StringVar(value='5')
        ttk.Entry(param_row, textvariable=self.verify_thresh_var, width=4).pack(side='left', padx=2)

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
        step_cb = ttk.Combobox(row_frame, textvariable=step_var, values=['0', '5', '10'],
                               width=6)
        step_cb.pack(side='left', padx=3)
        ttk.Label(row_frame, text='°C').pack(side='left')

        def remove():
            row_frame.destroy()
            self.device_rows.remove(row_data)
            self._renumber_devices()

        ttk.Button(row_frame, text='X', width=2, command=remove).pack(side='right', padx=3)

        row_data = {'frame': row_frame, 'id_var': id_var,
                    'proto_var': proto_var, 'step_var': step_var}
        self.device_rows.append(row_data)

    def _renumber_devices(self):
        for i, row in enumerate(self.device_rows, 1):
            for child in row['frame'].winfo_children():
                if isinstance(child, ttk.Label):
                    txt = child.cget('text')
                    if txt and txt.startswith('#'):
                        child.config(text=f'#{i}')
                        break

    def _preset_temps(self, temps):
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
            self.temp_preview_var.set('Invalid: non-numeric values found')
            return
        if not temps:
            self.temp_preview_var.set('0 points')
            return
        temps.sort()
        self.temp_preview_var.set(
            f'{len(temps)} points | {temps[0]:.1f} to {temps[-1]:.1f} deg C'
        )

    def _get_temp_points(self):
        """Parse temp_text and return sorted list of floats, or None if invalid."""
        raw = self.temp_text.get('1.0', 'end').strip()
        parts = raw.replace(',', ' ').split()
        try:
            return sorted([float(p) for p in parts])
        except ValueError:
            return None

    def _build_controls(self, parent):
        """控制按钮 + 进度/状态"""
        ctrl_frame = ttk.Frame(parent)
        self.ctrl_frame = ctrl_frame
        ctrl_frame.pack(fill='x', pady=(8, 4))

        self.start_btn = ttk.Button(ctrl_frame, text='▶ 开始标定', command=self._start)
        self.start_btn.pack(side='left', padx=(0, 5))

        self.pause_btn = ttk.Button(ctrl_frame, text='⏸ 暂停', command=self._pause, state='disabled')
        self.pause_btn.pack(side='left', padx=(0, 5))

        self.stop_btn = ttk.Button(ctrl_frame, text='■ 停止', command=self._stop, state='disabled')
        self.stop_btn.pack(side='left', padx=(0, 5))

        self.resume_btn = ttk.Button(ctrl_frame, text='📂 续跑...', command=self._resume_cal)
        self.resume_btn.pack(side='left', padx=(0, 15))

        self.progress_var = tk.DoubleVar(value=0)
        self.progress_bar = ttk.Progressbar(ctrl_frame, variable=self.progress_var, length=250)
        self.progress_bar.pack(side='left', padx=(0, 8))

        self.progress_label = ttk.Label(ctrl_frame, text='0/0')
        self.progress_label.pack(side='left', padx=(0, 15))

        self.time_label = ttk.Label(ctrl_frame, text='耗时: 0min')
        self.time_label.pack(side='left')

        # 状态区
        status_frame = ttk.LabelFrame(parent, text='实时状态', padding=6)
        status_frame.pack(fill='x')

        self.status_text = tk.Text(status_frame, height=3, width=80, state='disabled',
                                    font=('Consolas', 10), bg='#1e1e1e', fg='#d4d4d4')
        self.status_text.pack(fill='x')

    def _on_mode_change(self):
        mode = self.mode_var.get()
        if mode == 'calibrate':
            self.verify_frame.pack_forget()
            self.cal_frame.pack(fill='x', before=self.ctrl_frame)
            self.start_btn.config(text='Start Calibrate')
            self.data_notebook.tab(0, text='No data')
        else:
            self.cal_frame.pack_forget()
            self.verify_frame.pack(fill='x', before=self.ctrl_frame)
            self.start_btn.config(text='Start Verify')

    def _build_fit_panel(self, parent):
        """拟合结果面板"""
        fit_frame = ttk.LabelFrame(parent, text='拟合方案', padding=6)
        fit_frame.pack(side='right', fill='y', padx=(8, 0))

        # 可滚动框架
        self.fit_canvas = tk.Canvas(fit_frame, width=260, highlightthickness=0)
        scrollbar = ttk.Scrollbar(fit_frame, orient='vertical', command=self.fit_canvas.yview)
        self.fit_inner = ttk.Frame(self.fit_canvas)
        self.fit_inner.bind('<Configure>', lambda e: self.fit_canvas.configure(
            scrollregion=self.fit_canvas.bbox('all')))
        self.fit_canvas.create_window((0, 0), window=self.fit_inner, anchor='nw')
        self.fit_canvas.configure(yscrollcommand=scrollbar.set)

        self.fit_canvas.pack(side='left', fill='both', expand=True)
        scrollbar.pack(side='right', fill='y')

        # 默认占位
        ttk.Label(self.fit_inner, text='开始标定后\n自动显示拟合方案',
                  font=('', 9), foreground='gray').pack(pady=20)

        self.fit_var = tk.StringVar(value='')
        self.fit_var.trace_add('write', lambda *a: self._update_curve_with_fit())
        self.export_btn = ttk.Button(fit_frame, text='导出所选模型 →', command=self._export_fit, state='disabled')
        self.export_btn.pack(fill='x', pady=(6, 0))

    def _build_data_table(self):
        """Bottom panel: tabbed data table for verification results"""
        table_label = ttk.Label(self.bottom_frame, text='Data Table', font=('', 10, 'bold'))
        table_label.pack(anchor='w', padx=4, pady=(4, 0))

        self.data_notebook = ttk.Notebook(self.bottom_frame)
        self.data_notebook.pack(fill='both', expand=True, padx=4, pady=4)

        # Placeholder tab
        placeholder = ttk.Frame(self.data_notebook)
        self.data_notebook.add(placeholder, text='No data')
        ttk.Label(placeholder, text='Start verification to see data here',
                  foreground='gray').pack(expand=True)

    def _toggle_conn_mode(self):
        if self.conn_mode_var.get() == 'client':
            self.client_host_entry.config(state='normal')
        else:
            self.client_host_entry.config(state='disabled')

    # ========================================
    # 配置持久化
    # ========================================
    def _scan_ports(self):
        ports = []
        try:
            from serial.tools.list_ports import comports
            for p in comports():
                ports.append(p.device)
        except Exception:
            pass
        return ports if ports else ['COM1', 'COM2', 'COM3', 'COM4', 'COM5']

    def _refresh_ports(self):
        self.com_combo['values'] = self._scan_ports()

    def _load_config(self):
        c = load_config()
        if c.has_section('settings'):
            for key in ['device_id', 'com_port', 'ttag_port', 'conn_mode', 'client_host',
                        'start_temp', 'end_temp', 'step', 'tolerance',
                        'adc_samples', 'adc_threshold']:
                val = get_config('settings', key, '')
                if val:
                    if key == 'device_id':
                        self.device_id_var.set(val)
                    elif key == 'com_port':
                        self.com_port_var.set(val)
                    elif key == 'ttag_port':
                        self.ttag_port_var.set(val)
                    elif key == 'conn_mode':
                        self.conn_mode_var.set(val)
                        self._toggle_conn_mode()
                    elif key == 'client_host':
                        self.client_host_var.set(val)
                    elif key == 'start_temp':
                        self.start_var.set(val)
                    elif key == 'end_temp':
                        self.end_var.set(val)
                    elif key == 'step':
                        self.step_var.set(val)
                    elif key == 'tolerance':
                        self.tol_var.set(val)
                    elif key == 'adc_samples':
                        self.adc_samples_var.set(val)
                    elif key == 'adc_threshold':
                        self.adc_thresh_var.set(val)

    def _save_config(self):
        for key, var in [('device_id', self.device_id_var), ('com_port', self.com_port_var),
                          ('ttag_port', self.ttag_port_var), ('conn_mode', self.conn_mode_var),
                          ('client_host', self.client_host_var), ('start_temp', self.start_var),
                          ('end_temp', self.end_var), ('step', self.step_var),
                          ('tolerance', self.tol_var), ('adc_samples', self.adc_samples_var),
                          ('adc_threshold', self.adc_thresh_var)]:
            save_config('settings', key, var.get())
        messagebox.showinfo('已保存', '设置已保存到 ttag_config.ini')

    # ========================================
    # 标定控制
    # ========================================
    def _start(self):
        if self.mode_var.get() == 'verify':
            self._start_verify()
        else:
            self._start_cal()

    def _start_verify(self):
        # Parse devices
        devices = []
        for row in self.device_rows:
            try:
                did = int(row['id_var'].get())
            except ValueError:
                messagebox.showwarning('Invalid', f'Device ID must be numeric: {row["id_var"].get()}')
                return
            proto = row['proto_var'].get()
            step = int(row['step_var'].get())
            devices.append((did, proto, step))
        if not devices:
            messagebox.showwarning('No Devices', 'Add at least one device.')
            return

        # Parse temps
        temps = self._get_temp_points()
        if temps is None or not temps:
            messagebox.showwarning('Invalid', 'Temperature points contain errors or are empty.')
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

    def _pause(self):
        if self.mode_var.get() == 'verify' and self.verify_thread:
            if self.verify_thread.paused.is_set():
                self.verify_thread.paused.clear()
                self.pause_btn.config(text='Pause')
            else:
                self.verify_thread.paused.set()
                self.pause_btn.config(text='Resume')
        else:
            self._pause_cal()

    def _stop(self):
        if self.mode_var.get() == 'verify' and self.verify_thread:
            self.verify_thread.stopped.set()
            self.verify_thread.paused.clear()
        else:
            self._stop_cal()

    def _get_params(self):
        """从 UI 收集所有参数"""
        device_id = int(self.device_id_var.get())
        start = float(self.start_var.get())
        end = float(self.end_var.get())
        step = float(self.step_var.get())
        desc = start > end
        step_dir = -abs(step) if desc else abs(step)

        temps = []
        t = start
        while (t >= end - abs(step_dir) / 2) if desc else (t <= end + step_dir / 2):
            temps.append(round(t, 1))
            t += step_dir

        output_root = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'ADCTdata')
        run_ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        output_dir = os.path.join(output_root, run_ts)
        os.makedirs(output_dir, exist_ok=True)

        return {
            'device_id': device_id,
            'start': start, 'end': end, 'step': step,
            'temps': temps,
            'bath_tolerance': float(self.tol_var.get()),
            'stability_samples': int(self.adc_samples_var.get()),
            'stability_threshold': int(self.adc_thresh_var.get()),
            'water_bath_port': self.com_port_var.get(),
            'ttag_port': int(self.ttag_port_var.get()),
            'connect_to': (f'{self.client_host_var.get()}:{self.ttag_port_var.get()}'
                           if self.conn_mode_var.get() == 'client' else None),
            'no_ttag': False,
            'output': os.path.join(output_dir, f'cal_{device_id}.xlsx'),
            'prev_records': [],
            'completed_set': set(),
        }

    def _start_cal(self):
        if self.cal_thread and self.cal_thread.is_alive():
            messagebox.showwarning('已在运行', '标定程序正在运行中')
            return

        try:
            params = self._get_params()
        except ValueError as e:
            messagebox.showerror('参数错误', f'请检查输入参数:\n{e}')
            return

        if not params['temps']:
            messagebox.showerror('参数错误', f'没有要标定的温度点 (起始={params["start"]}, 结束={params["end"]})')
            return

        # 确认
        msg = f'将标定 {len(params["temps"])} 个点: {params["start"]} → {params["end"]} °C\n'
        msg += f'设备: {params["device_id"]}\n'
        msg += f'输出: {params["output"]}\n\n确定开始?'
        if not messagebox.askyesno('确认', msg):
            return

        self._save_config()

        self.status_queue = queue.Queue(maxsize=100)
        self.cal_thread = CalibrationThread(params, self.status_queue)
        self.cal_thread.start()

        self._set_running_state(True)
        self._log_status('标定已启动...')

    def _pause_cal(self):
        if self.cal_thread:
            if self.cal_thread.paused.is_set():
                self.cal_thread.pause()
                self.pause_btn.config(text='▶ 继续')
                self._log_status('已暂停')
            else:
                self.cal_thread.resume()
                self.pause_btn.config(text='⏸ 暂停')
                self._log_status('已继续')

    def _stop_cal(self):
        if self.cal_thread and self.cal_thread.is_alive():
            if messagebox.askyesno('确认', '确定停止标定? 已记录的数据将保留。'):
                self.cal_thread.stop()
                self._set_running_state(False)
                self._log_status('标定已停止')

    def _resume_cal(self):
        path = filedialog.askopenfilename(
            title='选择已有标定文件',
            filetypes=[('Excel/CSV', '*.xlsx;*.csv'), ('All', '*.*')],
            initialdir=os.path.join(os.path.dirname(os.path.abspath(__file__)), 'ADCTdata')
        )
        if not path:
            return

        # 读取已有数据
        try:
            if path.endswith('.csv'):
                with open(path, 'r', encoding='utf-8-sig') as f:
                    reader = csv.reader(f)
                    next(reader, None)
                    completed = []
                    prev_records = []
                    device_id = None
                    for row in reader:
                        try:
                            completed.append(float(row[2]))
                            if device_id is None:
                                device_id = int(row[1])
                            prev_records.append({
                                'target': float(row[2]), 'actual': float(row[3]),
                                'adc_mean': float(row[4]), 'adc_range': int(row[5]),
                                'adc_n': int(row[6]), 'elapsed': float(row[7]),
                                'ts': row[8] if len(row) > 8 else '',
                            })
                        except (ValueError, IndexError):
                            continue
            else:
                from openpyxl import load_workbook
                wb = load_workbook(path, read_only=True, data_only=True)
                ws = wb.active
                completed = []
                prev_records = []
                device_id = None
                for row in ws.iter_rows(min_row=2, values_only=True):
                    try:
                        completed.append(float(row[2]))
                        if device_id is None:
                            device_id = int(row[1])
                        prev_records.append({
                            'target': float(row[2]),
                            'actual': float(row[3]) if row[3] is not None else float(row[2]),
                            'adc_mean': float(row[4]) if row[4] is not None else 0,
                            'adc_range': int(row[5]) if row[5] is not None else 0,
                            'adc_n': int(row[6]) if row[6] is not None else 0,
                            'elapsed': float(row[7]) if row[7] is not None else 0,
                            'ts': str(row[8]) if row[8] is not None else '',
                        })
                    except (ValueError, TypeError):
                        continue
                wb.close()
        except Exception as e:
            messagebox.showerror('读取失败', f'无法读取文件:\n{e}')
            return

        if not completed:
            messagebox.showerror('读取失败', '文件中没有有效数据')
            return

        completed_set = set(completed)
        temp_min = min(completed)
        temp_max = max(completed)
        # 步长：取众数
        diffs = [round(completed[i+1] - completed[i], 2)
                 for i in range(len(completed)-1) if completed[i+1] - completed[i] > 0.01]
        step_abs = abs(max(set(diffs), key=diffs.count)) if diffs else 0.2

        # 用时间戳找真正的起止点（文件可能被温度排序过，行顺序不可靠）
        records_with_ts = [(r.get('ts', ''), r['target']) for r in prev_records]
        valid_ts = [(ts, t) for ts, t in records_with_ts if ts]
        if len(valid_ts) >= 2:
            valid_ts.sort(key=lambda x: x[0])  # 按时间排序
            first_chrono = valid_ts[0][1]   # 最早时间 = 标定起点
            last_chrono = valid_ts[-1][1]   # 最晚时间 = 标定终点
        else:
            # 无时间戳，用文件首尾行
            first_chrono = prev_records[0]['target']
            last_chrono = prev_records[-1]['target']

        ascending = last_chrono > first_chrono
        step = step_abs if ascending else -step_abs
        direction_word = '升温' if ascending else '降温'

        # ---- 用文件数据回填 UI ----
        if device_id:
            self.device_id_var.set(str(device_id))
        self.step_var.set(str(step_abs))

        # 续跑起点 = 文件中最后一个点 + 一步
        next_start = round(last_chrono + step, 1)

        # 第一步：询问是否沿用原标定计划
        direction_word = '升温' if ascending else '降温'
        choice = messagebox.askyesnocancel(
            '续跑 — 选择方式',
            f'文件: {os.path.basename(path)}\n'
            f'原标定: {first_chrono:.1f} → {last_chrono:.1f} °C ({direction_word})\n'
            f'区间: {temp_min:.1f} ~ {temp_max:.1f} °C, {len(completed)} 点, 步长 {step_abs}\n'
            f'设备: {device_id}\n\n'
            f'是否沿用原标定计划继续{direction_word}？\n\n'
            f'  [是] — 从 {next_start:.1f}°C 继续，只需输入新的结束温度\n'
            f'  [否] — 自己在界面上修改参数后再开始\n'
            f'  [取消] — 不续跑'
        )

        if choice is None:  # 取消
            return

        if choice:  # 是 — 沿用原计划，只需输入结束温度
            # 建议继续同方向推进 50 步
            if ascending:
                suggest_end = round(last_chrono + step * 50, 1)
                suggest_end = min(suggest_end, 100.0)
                end_prompt = f'原方向: {direction_word}\n'
                end_prompt += f'已有: {first_chrono:.1f} → {last_chrono:.1f} °C\n'
                end_prompt += f'从 {next_start:.1f} °C 继续升温\n\n'
                end_prompt += f'新的结束温度 (°C):'
            else:
                suggest_end = round(last_chrono + step * 50, 1)  # step is negative
                suggest_end = max(suggest_end, -30.0)
                end_prompt = f'原方向: {direction_word}\n'
                end_prompt += f'已有: {first_chrono:.1f} → {last_chrono:.1f} °C\n'
                end_prompt += f'从 {next_start:.1f} °C 继续降温\n\n'
                end_prompt += f'新的结束温度 (°C):'

            from tkinter import simpledialog
            end_str = simpledialog.askstring(
                '续跑 — 结束温度',
                end_prompt,
                initialvalue=str(suggest_end)
            )
            if end_str is None:
                return
            try:
                end = float(end_str)
            except ValueError:
                messagebox.showerror('输入错误', '请输入有效数字')
                return

            if (ascending and end <= last_chrono) or (not ascending and end >= last_chrono):
                messagebox.showerror('方向错误',
                    f'结束温度必须 {"大于" if ascending else "小于"} '
                    f'最后一点 {last_chrono} °C '
                    f'(原标定是{direction_word})')
                return

            self.start_var.set(str(next_start))
            self.end_var.set(str(end))
        else:  # 否 — 用户自己修改 UI 参数
            # 先把合理默认值设好
            if ascending:
                suggest_end = round(last_chrono + step * 50, 1)
                suggest_end = min(suggest_end, 100.0)
            else:
                suggest_end = round(last_chrono + step * 50, 1)
                suggest_end = max(suggest_end, -30.0)
            self.start_var.set(str(next_start))
            self.end_var.set(str(suggest_end))

            # 弹出小窗口让用户修改
            dialog = tk.Toplevel(self)
            dialog.title('续跑 — 自定义参数')
            dialog.geometry('350x220')
            dialog.resizable(False, False)
            dialog.transient(self)
            dialog.grab_set()

            ttk.Label(dialog, text=f'文件: {os.path.basename(path)}',
                      font=('', 9, 'bold')).pack(pady=(10, 5))
            ttk.Label(dialog, text=f'原标定: {first_chrono:.1f} → {last_chrono:.1f} °C ({direction_word})  '
                                   f'设备: {device_id}',
                      font=('', 9)).pack()

            form = ttk.Frame(dialog, padding=10)
            form.pack(fill='x', pady=10)

            ttk.Label(form, text='起始温度:').grid(row=0, column=0, sticky='e', pady=3)
            start_var = tk.StringVar(value=str(next_start))
            ttk.Entry(form, textvariable=start_var, width=10).grid(row=0, column=1, padx=5)

            ttk.Label(form, text='结束温度:').grid(row=1, column=0, sticky='e', pady=3)
            end_var = tk.StringVar(value=str(suggest_end))
            ttk.Entry(form, textvariable=end_var, width=10).grid(row=1, column=1, padx=5)

            ttk.Label(form, text='步长:').grid(row=2, column=0, sticky='e', pady=3)
            step_var = tk.StringVar(value=str(step_abs))
            ttk.Entry(form, textvariable=step_var, width=10).grid(row=2, column=1, padx=5)

            result = {'confirmed': False}

            def on_confirm():
                try:
                    s = float(start_var.get())
                    e = float(end_var.get())
                    st = float(step_var.get())
                    if s == e:
                        messagebox.showwarning('参数错误', '起始和结束温度不能相同', parent=dialog)
                        return
                    if st <= 0:
                        messagebox.showwarning('参数错误', '步长必须大于0', parent=dialog)
                        return
                    result['start'] = s
                    result['end'] = e
                    result['step'] = st
                    result['confirmed'] = True
                    dialog.destroy()
                except ValueError:
                    messagebox.showwarning('输入错误', '请输入有效数字', parent=dialog)

            def on_cancel():
                dialog.destroy()

            btn_frame = ttk.Frame(form)
            btn_frame.grid(row=3, column=0, columnspan=2, pady=(15, 0))
            ttk.Button(btn_frame, text='确认', command=on_confirm).pack(side='left', padx=5)
            ttk.Button(btn_frame, text='取消', command=on_cancel).pack(side='left', padx=5)

            dialog.wait_window()

            if not result['confirmed']:
                return

            next_start = result['start']
            end = result['end']
            step = result['step'] if result['start'] < result['end'] else -result['step']

            self.start_var.set(str(next_start))
            self.end_var.set(str(end))
            self.step_var.set(str(abs(step)))

        # 确认
        msg = (f'续跑计划:\n'
               f'  文件: {os.path.basename(path)}\n'
               f'  已有: {first_chrono:.1f} → {last_chrono:.1f} °C ({len(completed)} 点)\n'
               f'  新增: {next_start:.1f} → {end:.1f} °C\n'
               f'  步长: {abs(step):.1f} °C\n'
               f'  设备: {device_id}\n\n'
               f'新数据将写入同一文件。确定?')
        if not messagebox.askyesno('确认续跑', msg):
            return

        try:
            params = self._get_params()
        except ValueError as e:
            messagebox.showerror('参数错误', f'{e}')
            return

        # 使用已有文件的输出路径
        params['output'] = path.replace('.csv', '.xlsx') if path.endswith('.csv') else path
        params['prev_records'] = prev_records
        params['completed_set'] = completed_set
        params['temps'] = [t for t in params['temps'] if t not in completed_set]
        params['start'] = next_start
        params['end'] = end
        params['step'] = step

        if not params['temps']:
            messagebox.showinfo('已完成', '所有温度点均已完成，无需续跑')
            return

        self.status_queue = queue.Queue(maxsize=100)
        self.cal_thread = CalibrationThread(params, self.status_queue)
        self.cal_thread.start()

        self._set_running_state(True)
        self._log_status(f'续跑已启动: 从 {next_start}°C 继续, {len(params["temps"])} 个新点')

    def _set_running_state(self, running):
        if running:
            self.start_btn.config(state='disabled')
            self.resume_btn.config(state='disabled')
            self.pause_btn.config(state='normal', text='⏸ 暂停')
            self.stop_btn.config(state='normal')
            self.bath_status_var.set('运行中...')
        else:
            self.start_btn.config(state='normal')
            self.resume_btn.config(state='normal')
            self.pause_btn.config(state='disabled', text='⏸ 暂停')
            self.stop_btn.config(state='disabled')
            self.bath_status_var.set('已停止')

    # ========================================
    # 状态轮询
    # ========================================
    def _poll_status(self):
        """每 200ms 从后台线程拉状态"""
        try:
            while True:
                msg = self.status_queue.get_nowait()
                try:
                    self._handle_msg(msg)
                except Exception:
                    pass  # 单个消息处理失败不影响轮询
        except queue.Empty:
            pass

        self.after(200, self._poll_status)

    def _handle_msg(self, msg):
        msg_type = msg['type']
        # Support both CalibrationThread (wraps data with 'data' key)
        # and VerifyThread (unpacks data with **data pattern)
        if 'data' in msg:
            data = msg['data']
        else:
            data = {k: v for k, v in msg.items() if k != 'type'}

        if msg_type == 'status':
            self._update_status(data)
        elif msg_type == 'fit_update':
            self._update_fit_panel(data)
        elif msg_type == 'log':
            # CalibrationThread sends plain string, VerifyThread sends {'text': ...}
            if isinstance(data, dict) and 'text' in data:
                data = data['text']
            self._log_status(data)
        elif msg_type == 'done' or msg_type == 'complete':
            self._on_done(data)
        elif msg_type == 'result':
            # VerifyThread per-device result — log briefly
            did = data.get('did', '?')
            passed = 'PASS' if data.get('passed') else 'FAIL'
            self._log_status(f'[{did}] {data.get("target","?")}C: {passed}')
        elif msg_type == 'error':
            # CalibrationThread sends plain string, VerifyThread sends {'text': ...}
            if isinstance(data, dict) and 'text' in data:
                data = data['text']
            self._log_status(f'错误: {data}')
            messagebox.showerror('标定出错', str(data))
            self._set_running_state(False)

    def _update_status(self, s):
        """更新状态显示"""
        done, total = s['done'], s['total']
        if total > 0:
            self.progress_var.set(done * 100 / total)
            self.progress_label.config(text=f'第 {s["disp_i"]}/{total} 点')
        now = datetime.now().strftime('%H:%M:%S')
        self.time_label.config(text=f'{now} | 耗时: {s["elapsed"]/60:.0f}min  剩余: {s["eta"]/60:.0f}min')

        # 状态文本
        phase = '水浴稳定' if s['phase'] == 'bath' else 'ADC采集'
        cool_hint = ' [降温]' if s.get('need_cool') else ''
        nudge_hint = f' [推→{s["nudge_sv"]}°C]' if s.get('nudge_sv') else ''

        lines = []
        pv_str = f'{s["pv"]:.4f}' if s['pv'] is not None else '---'
        pwr_str = f'{s["pwr"]}%' if s['pwr'] is not None else '---'
        reached = ' ✓' if s.get('reached') else ''
        lines.append(f'水浴: PV={pv_str}°C  目标={s["target"]}°C  '
                     f'd={s["d"]:.4f}°C{reached}  输出={pwr_str}')

        adc_str = str(s['adc']) if s['adc'] is not None else '---'
        n_str = f'n={s.get("adc_n", 0)}' if s['phase'] == 'adc' else ''
        stable_mark = ' *STABLE*' if s.get('adc_stable') else ''
        rssi = s.get('rssi')
        if rssi is not None and rssi > 0:
            if rssi > 180:   rssi_str = f'RSSI={rssi}/255 [强]'
            elif rssi > 120: rssi_str = f'RSSI={rssi}/255 [中]'
            elif rssi > 60:  rssi_str = f'RSSI={rssi}/255 [弱]'
            else:            rssi_str = f'RSSI={rssi}/255 [差]'
        else:
            rssi_str = 'RSSI=---'
        lines.append(f'TTAG: ADC={adc_str}  {rssi_str}  '
                     f'命中={s.get("hits", 0)}  帧={s.get("frames", 0)}  {n_str}{stable_mark}')
        lines.append(f'阶段: {phase}{cool_hint}{nudge_hint}  目标={s["target"]}°C')

        self._set_status('\n'.join(lines))

    def _log_status(self, text):
        self._set_status(text)

    def _set_status(self, text):
        self.status_text.config(state='normal')
        self.status_text.delete('1.0', 'end')
        self.status_text.insert('1.0', text)
        self.status_text.config(state='disabled')

    def _update_fit_panel(self, fit_results):
        """更新拟合方案面板"""
        # 清除旧内容
        for w in self.fit_inner.winfo_children():
            w.destroy()

        self.fit_results = fit_results
        self.fit_var.set('')

        if not fit_results:
            ttk.Label(self.fit_inner, text='暂无拟合结果', foreground='gray').pack(pady=10)
            return

        # 标题
        ttk.Label(self.fit_inner, text='点击选择模型:', font=('', 10, 'bold')).pack(anchor='w', pady=(0, 5))

        status_colors = {'good': 'green', 'warn': 'orange', 'bad': 'red', 'error': 'red', 'skip': 'gray'}
        status_icons = {'good': '✅ 优', 'warn': '⚠ 可', 'bad': '❌ 差', 'error': '❌ 错', 'skip': '— 跳过'}

        for i, r in enumerate(fit_results):
            is_first_good = (i == 0 or (i > 0 and fit_results[i - 1].get('status') != 'good'
                                        and r.get('status') == 'good'))
            color = status_colors.get(r['status'], 'gray')
            icon = status_icons.get(r['status'], '?')

            frame = ttk.Frame(self.fit_inner)
            frame.pack(fill='x', pady=1)

            # 单选按钮
            rb = ttk.Radiobutton(frame, text='', variable=self.fit_var,
                                  value=r['model'], state='normal' if r['status'] not in ('error', 'skip') else 'disabled')
            rb.pack(side='left')

            # 模型名 + 误差
            text = f'{r["model"]}'
            if r['max_err'] < 900:
                text += f'  ({r["max_err"]:.3f}°C)'
            lbl = ttk.Label(frame, text=text, foreground=color, font=('', 9))
            lbl.pack(side='left')

            # 状态图标
            ttk.Label(frame, text=f' {icon}', foreground=color, font=('', 8)).pack(side='right')

            # 警告
            if r.get('warning'):
                warn_frame = ttk.Frame(self.fit_inner)
                warn_frame.pack(fill='x', padx=(25, 0))
                ttk.Label(warn_frame, text=f'⚠ {r["warning"]}', foreground='orange',
                          font=('', 7), wraplength=220).pack(anchor='w')

            # 自动预选第一个 good 的
            if r['status'] == 'good' and not self.fit_var.get():
                self.fit_var.set(r['model'])

        # 如果全都不好，预选第一个
        if not self.fit_var.get() and fit_results:
            self.fit_var.set(fit_results[0]['model'])

        self.export_btn.config(state='normal')

        # 更新曲线数据（实时追加）
        if self.cal_thread and self.cal_thread.records:
            self.curve.set_data(self.cal_thread.records)
        # 更新拟合曲线
        self._update_curve_with_fit()

    def _update_curve_with_fit(self):
        """根据选中的拟合模型更新曲线"""
        selected = self.fit_var.get()
        for r in self.fit_results:
            if r['model'] == selected:
                self.curve.set_fit(r)
                break
        else:
            self.curve.fit_line = None
            self.curve.draw()

    def _export_fit(self):
        """导出选定拟合模型"""
        selected = self.fit_var.get()
        if not selected:
            messagebox.showwarning('未选择', '请先选择一个拟合模型')
            return

        for r in self.fit_results:
            if r['model'] == selected:
                result = r
                break
        else:
            return

        # 保存拟合结果
        path = filedialog.asksaveasfilename(
            title='导出拟合结果',
            defaultextension='.txt',
            filetypes=[('Text', '*.txt'), ('All', '*.*')],
            initialdir=os.path.dirname(os.path.abspath(__file__))
        )
        if not path:
            return

        with open(path, 'w', encoding='utf-8') as f:
            f.write(f'TTAG 标定拟合结果\n')
            f.write(f'================\n')
            f.write(f'导出时间: {datetime.now()}\n')
            f.write(f'模型: {result["model"]}\n')
            if result.get('order'):
                f.write(f'阶数: {result["order"]}\n')
            f.write(f'最大误差: {result["max_err"]:.4f} °C\n')
            f.write(f'平均误差: {result["mean_err"]:.4f} °C\n')
            if result.get('coeffs'):
                f.write(f'系数: {result["coeffs"]}\n')
            if result.get('params'):
                f.write(f'参数: {result["params"]}\n')
            if result.get('warning'):
                f.write(f'警告: {result["warning"]}\n')

        messagebox.showinfo('导出完成', f'拟合结果已保存到:\n{path}')

    def _on_done(self, data):
        self._set_running_state(False)

        if 'xlsx' in data:
            # Verify completion
            xlsx = data.get('xlsx', '')
            results = data.get('results', {})
            total = sum(len(v) for v in results.values())
            self._log_status(f'Verify complete! {total} results → {xlsx}')
            messagebox.showinfo('Verify Complete',
                                f'Verification complete!\n\n'
                                f'{total} data points across {len(results)} devices\n'
                                f'Excel: {xlsx}')
        else:
            # Calibration done
            self._log_status(f'标定完成! {data["records"]} 个点 → {data["output"]}')

            # 更新曲线
            if self.cal_thread and self.cal_thread.records:
                self.curve.set_data(self.cal_thread.records)

            messagebox.showinfo('标定完成',
                                f'标定完成!\n\n{data["records"]} 个数据点\n'
                                f'输出: {data["output"]}\n\n'
                                f'请在右侧面板选择拟合模型并导出。')

    def _on_close(self):
        if self.cal_thread and self.cal_thread.is_alive():
            if messagebox.askyesno('确认退出', '标定正在运行中，确定退出?\n已记录的数据将保留。'):
                self.cal_thread.stop()
                time.sleep(0.5)
        self.destroy()


# ============================================================
# 入口
# ============================================================
def main():
    app = MainWindow()
    app.mainloop()


if __name__ == '__main__':
    main()