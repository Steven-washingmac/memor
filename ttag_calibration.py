#!/usr/bin/env python3
"""
TTAG 自动标定系统 (v2)
整合: 水浴箱控制(Modbus) + TTAG监测(TCP 55AA) + 实时仪表盘 + Excel输出

用法:
  python ttag_calibration.py --device 230030 --start 5 --end 8 --step 0.2
  python ttag_calibration.py --device 230030 --start 5 --end 50 --step 0.2 --bath-tolerance 0.05
"""
import sys, os, time, struct, socket, threading, argparse
from collections import deque
from datetime import datetime
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from water_bath_control import WaterBath
from ttag_monitor import FRAME_HEADER, parse_frame as monitor_parse_frame
from ttag_fitting import fit_calibration

def parse_frame(data):
    """用 ttag_monitor 的解析，返回简化格式"""
    tags = []
    try:
        frame = monitor_parse_frame(data)
        if frame.valid:
            for tag in frame.tags:
                tags.append({'tag_id': tag.tag_id, 'adc': tag.adc,
                            'rssi': tag.rssi, 'tag_type': tag.tag_type})
    except Exception:
        pass
    return tags


class TtagReceiver:
    def __init__(self, device_id, host='0.0.0.0', port=20226, connect_to=None):
        self.device_id = device_id
        self.host = host; self.port = port
        self.connect_to = connect_to  # None=Server监听, "ip:port"=Client连接基站
        self.latest_adc = None; self.latest_rssi = None
        self.last_seen = None; self.hit_count = 0; self.frame_count = 0
        self.running = False
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
            # 客户端模式：主动连接基站
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
            # 服务端模式：监听等待基站连接
            try:
                sock.bind((self.host, self.port))
                sock.listen(5)
                sock.settimeout(1.0)
                print(f"  TTAG 服务端已启动: {socket.gethostbyname(socket.gethostname())}:{self.port}")
                print(f"  等待基站连接...")
            except OSError:
                print(f"  端口 {self.port} 被占用，尝试连接基站...")
                sock.close()
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(5)
                try:
                    sock.connect(('192.168.3.188', self.port))
                    sock.settimeout(2.0)
                    is_client = True
                except Exception:
                    print("  无法连接基站，退出")
                    return

        buffer = b''
        while self.running:
            if is_client:
                try:
                    data = sock.recv(4096)
                    if not data: break
                    buffer += data
                    buffer = self._process(buffer)
                except socket.timeout: continue
                except OSError: break
            else:
                try:
                    client, addr = sock.accept()
                    print(f"  基站已连接: {addr[0]}:{addr[1]}")
                    client.settimeout(5)
                    while self.running:
                        try:
                            data = client.recv(4096)
                            if not data: break
                            buffer += data
                            buffer = self._process(buffer)
                        except socket.timeout: continue
                    client.close(); buffer = b''
                except socket.timeout: continue
                except OSError: break
        sock.close()

    def _process(self, buffer):
        while True:
            idx = buffer.find(FRAME_HEADER)
            if idx == -1: return buffer[-1:] if len(buffer) > 1 else buffer
            if idx > 0: buffer = buffer[idx:]
            if len(buffer) < 4: return buffer
            data_len = struct.unpack_from('<H', buffer, 2)[0]
            frame_len = 2 + 2 + data_len + 1
            if frame_len > 8192: buffer = buffer[2:]; continue
            if len(buffer) < frame_len: return buffer
            frame_data = buffer[:frame_len]; buffer = buffer[frame_len:]
            self.frame_count += 1
            for t in parse_frame(frame_data):
                if t['tag_id'] == self.device_id and t['adc'] != 0xFFFF:
                    with self._lock:
                        self.latest_adc = t['adc']
                        self.latest_rssi = t['rssi']
                        self.last_seen = time.time()
                        self.hit_count += 1

    def get_state(self):
        with self._lock:
            return {'adc': self.latest_adc, 'rssi': self.latest_rssi,
                    'last_seen': self.last_seen, 'hits': self.hit_count,
                    'frames': self.frame_count}


class AdcStabilityDetector:
    def __init__(self, window_sec=10.0, threshold=2):
        self.window_sec = window_sec; self.threshold = threshold
        self._history = deque()

    def feed(self, adc, ts=None):
        if ts is None: ts = time.time()
        self._history.append((ts, adc))
        cutoff = ts - self.window_sec
        while self._history and self._history[0][0] < cutoff:
            self._history.popleft()

    def check(self):
        if len(self._history) < 3:
            return False, None, None, len(self._history)
        adcs = [a for _, a in self._history]
        span = self._history[-1][0] - self._history[0][0]
        rng = max(adcs) - min(adcs)
        mean = sum(adcs) / len(adcs)
        stable = span >= self.window_sec * 0.8 and rng <= self.threshold
        return stable, mean, rng, len(self._history)

    def reset(self):
        self._history.clear()


def progress_bar(done, total, width=30):
    filled = width * done // max(total, 1)
    return '[' + '#' * filled + '-' * (width - filled) + ']'


def main():
    p = argparse.ArgumentParser(description='TTAG 自动标定系统 v2')
    p.add_argument('--device', type=int, required=True, help='TTAG 设备 ID')
    p.add_argument('--start', type=float, default=5.0, help='起始温度 (默认 5)')
    p.add_argument('--end', type=float, default=50.0, help='结束温度 (默认 50)')
    p.add_argument('--step', type=float, default=0.2, help='温度步进 (默认 0.2)')
    p.add_argument('--ttag-port', type=int, default=20226, help='TRG 基站端口')
    p.add_argument('--connect', default=None, metavar='IP:PORT',
                   help='客户端模式连接基站 (如 192.168.3.188:20226)')
    p.add_argument('--water-bath-port', default='COM3', help='水浴箱串口')
    p.add_argument('--stability-window', type=float, default=3.0, help='ADC稳定窗口(秒)')
    p.add_argument('--stability-threshold', type=int, default=2, help='ADC稳定阈值')
    p.add_argument('--bath-tolerance', type=float, default=0.1, help='水浴稳定容差')
    p.add_argument('--output', default=None, help='输出 Excel 文件 (默认自动生成)')
    p.add_argument('--dry-run', action='store_true')
    p.add_argument('--no-ttag', action='store_true')
    args = p.parse_args()

    if args.output is None:
        ts = datetime.now().strftime('%m%d_%H%M')
        args.output = f'cal_{args.device}_{ts}.xlsx'

    temps = []
    t = args.start
    while t <= args.end + args.step / 2:
        temps.append(round(t, 1))
        t += args.step

    if args.dry_run:
        print(f"标定点: {len(temps)} 个, {args.start}->{args.end}°C, 步进 {args.step}°C")
        print(f"水浴容差: +-{args.bath_tolerance}°C | ADC窗口: {args.stability_window}s 阈值<={args.stability_threshold}")
        for i, t in enumerate(temps):
            print(f"  {i+1}. {t}°C", end="  ")
            if (i+1) % 10 == 0: print()
        print()
        return

    # 初始化
    print("连接水浴箱 COM3 ...")
    wb = WaterBath(port=args.water_bath_port)
    print(f"水浴当前: {wb.get_temperature():.3f}°C, 设定: {wb.get_setpoint()}°C")

    ttag = None
    if not args.no_ttag:
        print()
        ttag = TtagReceiver(args.device, port=args.ttag_port, connect_to=args.connect)
        ttag.start()
        print(f"等待基站连接 (设备 {args.device})...")
        # 等待基站连接，最多等 30 秒
        for _ in range(60):
            time.sleep(0.5)
            st = ttag.get_state()
            if st.get('frames', 0) > 0 or st.get('hits', 0) > 0:
                print(f"已接收 {st.get('frames')} 帧, {st.get('hits')} 次命中设备 {args.device}")
                break
        else:
            print(f"30秒内未收到数据, 请检查基站是否连接")
            st = ttag.get_state()
            print(f"  已收帧: {st.get('frames')}, 命中: {st.get('hits')}")
        print()
        time.sleep(1)

    adc_det = AdcStabilityDetector(args.stability_window, args.stability_threshold)
    records = []
    t0_total = time.time()

    def save_excel():
        try:
            from openpyxl import Workbook
            wb_xl = Workbook()
            ws = wb_xl.active
            ws.title = "TTAG Calibration"
            ws.append(["#", "TagID", "Target(C)", "Actual(C)", "ADC_Mean",
                       "ADC_Range", "ADC_Samples", "StableTime(s)", "Timestamp", "Note"])
            for i, r in enumerate(records, 1):
                ws.append([i, args.device, r['target'], r['actual'],
                          round(r['adc_mean'], 1), r['adc_range'], r['adc_n'],
                          round(r['elapsed']), r['ts'], r.get('note', '')])
            wb_xl.save(args.output)
        except Exception as e:
            pass

    try:
        for i, target in enumerate(temps):
            wb.set_temperature(target)
            done = len(records)
            total = len(temps)

            # ---- 等水浴稳定 ----
            t1 = time.time()
            bath_ok = False
            while time.time() - t1 < 900:
                pv = wb.get_temperature()
                pwr = wb.get_status()
                ts = ttag.get_state() if ttag else {}
                elapsed_t = time.time() - t0_total
                eta = (elapsed_t / max(done, 1)) * (total - done) if done > 0 else 0

                os.system('cls' if os.name == 'nt' else 'clear')
                print("=" * 65)
                print(f"  TTAG 自动标定 | 设备 {args.device} | "
                      f"{args.start}->{args.end}°C | 步进 {args.step}°C")
                print("=" * 65)
                print(f"  [{i+1}/{total}] 等待水浴稳定到 {target}°C ...")
                print(f"  进度: {progress_bar(done, total)} {done*100//total}% | "
                      f"耗时 {elapsed_t/60:.0f}min | 剩余 {eta/60:.0f}min")
                print("-" * 65)
                if pv is not None:
                    d = abs(pv - target)
                    bs = '[OK]' if d <= args.bath_tolerance else '...'
                    print(f"  水浴: PV={pv:.4f}°C  目标={target}°C  "
                          f"d={d:.4f}°C  {bs}  加热={pwr}%")
                adc_v = ts.get('adc')
                n_frames = ts.get('frames', 0)
                conn = '[LINK]' if n_frames > 0 else '[WAIT]'
                print(f"  TTAG: ADC={adc_v}  RSSI={ts.get('rssi')}  hits={ts.get('hits',0)}  frames={n_frames}  {conn}")
                print("-" * 65)
                if records:
                    last = records[-1]
                    print(f"  已记录: {done} 点 | 上一点: {last['target']}°C ADC={last['adc_mean']:.1f}")
                print("=" * 65)

                if pv is not None and abs(pv - target) <= args.bath_tolerance and time.time() - t1 > 5:
                    bath_ok = True
                    break
                time.sleep(0.4)
            if not bath_ok:
                continue

            # ---- 等 TTAG ADC 稳定 ----
            if ttag is not None:
                adc_det.reset()
                t2 = time.time()
                adc_ok = False
                while time.time() - t2 < 300:
                    pv = wb.get_temperature()
                    pwr = wb.get_status()
                    ts = ttag.get_state()
                    adc_v = ts.get('adc')
                    et = time.time() - t0_total
                    eta_ = (et / max(done, 1)) * (total - done) if done > 0 else 0
                    adc_el = time.time() - t2

                    os.system('cls' if os.name == 'nt' else 'clear')
                    print("=" * 65)
                    print(f"  TTAG 自动标定 | 设备 {args.device} | "
                          f"{args.start}->{args.end}°C | 步进 {args.step}°C")
                    print("=" * 65)
                    print(f"  [{i+1}/{total}] 等待 ADC 稳定 ...")
                    print(f"  进度: {progress_bar(done, total)} {done*100//total}% | "
                          f"耗时 {et/60:.0f}min | 剩余 {eta_/60:.0f}min")
                    print("-" * 65)
                    d = abs(pv - target) if pv is not None else 0
                    pv_str = f"{pv:.4f}" if pv is not None else "?"
                    print(f"  水浴: PV={pv_str}°C  目标={target}°C  "
                          f"d={d:.4f}°C  [OK]  加热={pwr}%")

                    if adc_v is not None:
                        adc_det.feed(adc_v)
                        stable, mean, rng, n = adc_det.check()
                        st = '*STABLE*' if stable else '-acq-'
                        n_frames = ts.get('frames', 0)
                        conn = '[LINK]' if n_frames > 0 else '[WAIT]'
                        print(f"  TTAG: ADC={adc_v}  RSSI={ts.get('rssi')}  "
                              f"{st}  avg={mean or 0:.1f}  d={rng}  n={n}  [{adc_el:.0f}s]  {conn}")
                    else:
                        print(f"  TTAG: 等待基站数据...")
                    print("-" * 65)
                    if records:
                        last = records[-1]
                        print(f"  已记录: {done} 点 | 上一点: {last['target']}°C ADC={last['adc_mean']:.1f}")
                    print("=" * 65)

                    if adc_v is not None and stable:
                        adc_mean, adc_range, adc_n = mean, rng, n
                        adc_ok = True
                        break
                    time.sleep(0.15)

                if not adc_ok:
                    ts2 = ttag.get_state()
                    adc_mean = ts2.get('adc') or 0
                    adc_range = 0
                    adc_n = 0
                adc_elapsed = time.time() - t2
            else:
                adc_mean = adc_range = adc_n = adc_elapsed = 0

            # ---- 记录 ----
            pv_now = wb.get_temperature() or target
            ts_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            records.append({
                'target': target, 'actual': pv_now,
                'adc_mean': adc_mean, 'adc_range': adc_range,
                'adc_n': adc_n, 'elapsed': adc_elapsed, 'ts': ts_str,
            })
            if len(records) % 5 == 0:
                save_excel()
            time.sleep(0.5)

    except KeyboardInterrupt:
        pass
    finally:
        save_excel()
        if ttag: ttag.stop()
        wb.close()

    print(f"\n标定结束. {len(records)} 个点 -> {args.output}")
    if records:
        adcs = [r['adc_mean'] for r in records]
        print(f"ADC 范围: {min(adcs):.0f} ~ {max(adcs):.0f}")

        # ---- 自动拟合 ----
        print(f"\n{'='*50}")
        print(f"  自动拟合 ADC → Temperature")
        print(f"{'='*50}")
        try:
            result = fit_calibration(args.output)
            if result:
                print(f"\n  ✅ {result['order']}阶多项式, maxErr={result['max_err']:.4f}°C")
            else:
                print(f"\n  ⚠️ 自动拟合未成功，可用 MATLAB ttag_fitting.m 手动处理")
        except Exception as e:
            print(f"\n  ⚠️ 自动拟合出错: {e}")
            print(f"  可手动运行: python ttag_fitting.py {args.output}")


if __name__ == '__main__':
    main()
