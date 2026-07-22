#!/usr/bin/env python3
"""
TRG TTag 产线监控工具
======================
监听 TCP 端口，接收 TRG 测温基站 55 AA 协议帧
-> 类型过滤 → ID区间过滤 → 实时显示 + 可选 CSV 导出

当前状态：仅显示原始 ADC 值，温度转换待下一阶段校准后启用。

用法:
  python ttag_monitor.py                            # 默认: 0.0.0.0:20226, 仅T型(0x00)
  python ttag_monitor.py --port 8899                # 自定义端口
  python ttag_monitor.py --type 0x00,0x03           # 显示多种标签类型
  python ttag_monitor.py --rule A线 175000 175999    # 添加 ID 区间过滤
  python ttag_monitor.py --export result.csv         # 导出 CSV
"""

import argparse
import os
import socket
import struct
import sys
import threading
import time
from collections import deque
from datetime import datetime

# ============================================================
# 协议常量
# ============================================================
FRAME_HEADER  = b'\x55\xAA'
LOW_BATTERY_ADC = 0xFFFF


# ============================================================
# 温度转换（占位）
# ============================================================
# NTC 参数（硬件: NCP18XH103D03RB, Murata 10KΩ, B=3380K, R_fixed=6.2KΩ）
# 下一阶段：用户提供已知参考温度 → 拟合 ADC↔温度 关系

def adc_to_temperature(adc: int):
    """占位：下阶段用实测数据拟合 ADC→温度 关系"""
    if adc == LOW_BATTERY_ADC:
        return None
    return None


# ============================================================
# 协议解析
# ============================================================
class TagData:
    """单个标签的解析结果"""
    __slots__ = ('rssi', 'tag_type', 'tag_id', 'adc', 'reserved',
                 'temperature', 'low_battery', 'valid')

    def __init__(self):
        self.rssi        = 0
        self.tag_type    = 0
        self.tag_id      = 0
        self.adc         = 0
        self.reserved    = 0
        self.temperature = None
        self.low_battery = False
        self.valid       = True


class TRGFrame:
    """完整的数据帧"""
    __slots__ = ('raw', 'data_len', 'station_id', 'func_code', 'sn',
                 'tag_count', 'tags', 'checksum', 'valid')

    def __init__(self):
        self.raw        = b''
        self.data_len   = 0
        self.station_id = 0
        self.func_code  = 0
        self.sn         = 0
        self.tag_count  = 0
        self.tags       = []
        self.checksum   = 0
        self.valid      = True


def parse_frame(data: bytes, debug: bool = False) -> TRGFrame:
    """
    解析一帧 TRG 协议数据

    帧格式:
       55 AA | 长度 | 基站ID | 功能码 | SN | 标签数 | 标签块×N | 校验
       2B    | 2B   | 2B     | 1B     | 1B | 1B     | (1+1+3+2+2)×N | 1B

    标签块: 1B(RSSI) + 1B(类型) + 3B(ID 大端) + 2B(ADC 小端) + 2B(保留)
    """
    frame = TRGFrame()
    frame.raw = data

    if len(data) < 14:
        frame.valid = False
        return frame

    try:
        pos = 0
        header = data[pos:pos + 2]
        pos += 2
        if header != FRAME_HEADER:
            frame.valid = False
            return frame

        frame.data_len = struct.unpack_from('<H', data, pos)[0]
        pos += 2

        # 预期总长：header(2) + length(2) + payload(data_len) + checksum(1)
        expected_len = 2 + 2 + frame.data_len + 1
        if len(data) != expected_len:
            if debug:
                print(f"  [DEBUG] 帧长度不匹配: 实际{len(data)}B, 预期{expected_len}B "
                      f"(data_len={frame.data_len})")

        frame.station_id = struct.unpack_from('<H', data, pos)[0]
        pos += 2

        frame.func_code = data[pos]; pos += 1
        frame.sn        = data[pos]; pos += 1
        frame.tag_count = data[pos]; pos += 1

        # 预期标签块总字节数: (1+1+3+2+2) * tag_count = 9 * tag_count
        expected_tag_bytes = 9 * frame.tag_count

        if debug:
            tag_start = pos
            print(f"  [DEBUG] 帧hex: {data.hex(' ')}")
            print(f"  [DEBUG] 基站ID={frame.station_id} func={frame.func_code:02X} "
                  f"SN={frame.sn} tag_count={frame.tag_count} "
                  f"data_len={frame.data_len}")
            print(f"  [DEBUG] 标签块起始偏移={tag_start}, 预期{expected_tag_bytes}B")

        for i in range(frame.tag_count):
            if pos + 9 > len(data):
                if debug:
                    print(f"  [DEBUG] 标签#{i}: 剩余{len(data)-pos}B不足9B, 终止")
                break

            tag_start_pos = pos

            tag = TagData()

            tag.rssi     = data[pos]; pos += 1
            tag.tag_type = data[pos]; pos += 1

            # ID: 3 字节小端序 (低字节在前)
            id_b0 = data[pos]; id_b1 = data[pos + 1]; id_b2 = data[pos + 2]
            tag.tag_id = id_b0 | (id_b1 << 8) | (id_b2 << 16)
            pos += 3

            # ADC: 2 字节小端序
            adc_raw = data[pos:pos+2]
            tag.adc = struct.unpack_from('<H', data, pos)[0]
            pos += 2

            # 保留
            tag.reserved = struct.unpack_from('<H', data, pos)[0]
            pos += 2

            if debug:
                tag_bytes = data[tag_start_pos:pos]
                print(f"  [DEBUG]   #{i} raw={tag_bytes.hex(' ')} "
                      f"→ RSSI={tag.rssi} type=0x{tag.tag_type:02X} "
                      f"ID={tag.tag_id} (0x{tag.tag_id:06X}) "
                      f"ADC={tag.adc} (raw={adc_raw.hex(' ')}) "
                      f"reserved=0x{tag.reserved:04X}")

            if tag.adc == LOW_BATTERY_ADC:
                tag.low_battery = True
                tag.temperature = None
            else:
                tag.temperature = adc_to_temperature(tag.adc)

            frame.tags.append(tag)

        if pos < len(data):
            frame.checksum = data[pos]
            # 计算 payload 校验和 (station_id 到最后一个标签块结束的所有字节)
            payload_bytes = data[4:4 + frame.data_len]
            calc_checksum = sum(payload_bytes) & 0xFF
            if frame.checksum != calc_checksum and debug:
                print(f"  [DEBUG] 校验和不匹配: 收到=0x{frame.checksum:02X}, "
                      f"计算=0x{calc_checksum:02X} (payload sum mod 256)")

    except Exception:
        frame.valid = False

    return frame


# ============================================================
# 规则过滤
# ============================================================
class RuleFilter:
    """三层过滤：标签类型 → ID 区间 → ADC 范围"""

    def __init__(self):
        self.rules       = []        # [(name, start_id, end_id), ...]
        self.type_filter = None      # set of allowed tag types, None = 全部
        self.device_id   = None      # 指定单个设备 ID，None = 不过滤
        self.adc_min     = 0
        self.adc_max     = 0xFFFF

    # ---- ID 区间规则 ----

    def add_rule(self, name: str, start_id: int, end_id: int) -> bool:
        for n, s, e in self.rules:
            if not (end_id < s or start_id > e):
                print(f"[规则] [WARN] 区间 [{start_id}, {end_id}] 与「{n}」[{s}, {e}] 重叠!")
                return False
        self.rules.append((name, start_id, end_id))
        print(f"[规则] [OK] 添加:「{name}」[{start_id} ~ {end_id}]")
        return True

    def remove_rule(self, name: str):
        self.rules = [(n, s, e) for n, s, e in self.rules if n != name]
        print(f"[规则] [DEL] 删除:「{name}」")

    def list_rules(self):
        if self.device_id is not None:
            print(f"[设备] 仅显示 ID: {self.device_id}")
        elif not self.rules:
            print("[规则] (空，不过滤 ID)")
        for name, s, e in sorted(self.rules, key=lambda x: x[1]):
            print(f"  「{name}」: {s} ~ {e} ({e - s + 1} 个)")

        if self.type_filter is not None:
            names = [f"0x{t:02X}" for t in sorted(self.type_filter)]
            print(f"[类型] 仅显示: {', '.join(names)}")
        else:
            print("[类型] 全部显示")
        print("[ADC ] 范围过滤: 已关闭 (暂不转换温度)")

    # ---- 过滤判断 ----

    def check(self, tag: TagData) -> bool:
        # 1. 指定设备 ID
        if self.device_id is not None:
            if tag.tag_id != self.device_id:
                return False

        # 2. ID 区间
        if self.rules:
            if not any(s <= tag.tag_id <= e for _, s, e in self.rules):
                return False

        # 3. 标签类型
        if self.type_filter is not None and tag.tag_type not in self.type_filter:
            return False

        # 4. ADC 范围（低电量除外）
        if not tag.low_battery and not (self.adc_min <= tag.adc <= self.adc_max):
            return False

        return True


# ============================================================
# 统计
# ============================================================
class Stats:
    def __init__(self):
        self.frame_count      = 0
        self.tag_count        = 0
        self.pass_count       = 0
        self.filtered_count   = 0
        self.low_battery_count = 0
        self.start_time       = time.time()

    def uptime(self) -> str:
        elapsed = time.time() - self.start_time
        if elapsed < 60:
            return f"{elapsed:.0f}s"
        elif elapsed < 3600:
            return f"{elapsed / 60:.1f}m"
        return f"{elapsed / 3600:.1f}h"


# ============================================================
# 稳定性检测（标定模式）
# ============================================================
class StabilityDetector:
    """滑窗检测 ADC 是否在指定时间内稳定

    稳定条件：在 window_sec 秒内，ADC 最大值-最小值 ≤ threshold
    """

    def __init__(self, window_sec: float = 10.0, adc_threshold: int = 2):
        self.window_sec = window_sec
        self.adc_threshold = adc_threshold
        self._history = {}          # tag_id → deque of (timestamp, adc)
        self._last_stable = {}      # tag_id → (adc_mean, adc_range)

    def feed(self, tag_id: int, adc: int, timestamp: float = None) -> None:
        """喂入一个新的 ADC 采样点"""
        if timestamp is None:
            timestamp = time.time()
        if tag_id not in self._history:
            self._history[tag_id] = deque()
        self._history[tag_id].append((timestamp, adc))
        # 清除过期数据
        cutoff = timestamp - self.window_sec
        while self._history[tag_id] and self._history[tag_id][0][0] < cutoff:
            self._history[tag_id].popleft()

    def check(self, tag_id: int) -> tuple:
        """返回 (is_stable, adc_mean, adc_range, sample_count, span_sec)

        is_stable: 是否稳定
        adc_mean: 窗口内 ADC 均值
        adc_range: 窗口内 ADC 极差（max-min）
        sample_count: 窗口内采样数
        span_sec: 窗口实际时间跨度
        """
        if tag_id not in self._history:
            return (False, None, None, 0, 0.0)
        data = self._history[tag_id]
        if len(data) < 3:
            return (False, None, None, len(data), 0.0)
        times = [t for t, _ in data]
        adcs  = [a for _, a in data]
        span = times[-1] - times[0]
        adc_range = max(adcs) - min(adcs)
        adc_mean  = sum(adcs) / len(adcs)
        is_stable = (span >= self.window_sec * 0.8 and
                     adc_range <= self.adc_threshold)
        if is_stable:
            self._last_stable[tag_id] = (adc_mean, adc_range)
        return (is_stable, round(adc_mean, 1), adc_range, len(data), span)

    def last_stable(self, tag_id: int):
        """获取最近一次判定稳定时的 ADC 均值和极差"""
        return self._last_stable.get(tag_id)

    def reset(self, tag_id: int = None):
        """重置历史（切换到下一个温度点时调用）"""
        if tag_id is None:
            self._history.clear()
            self._last_stable.clear()
        else:
            self._history.pop(tag_id, None)
            self._last_stable.pop(tag_id, None)


# ============================================================
# 终端显示
# ============================================================

def _rssi_bar(rssi: int) -> str:
    if rssi > 180:   return "[===]"
    if rssi > 150:   return "[== ]"
    if rssi > 120:   return "[=  ]"
    if rssi > 90:    return "[   ]"
    return "[--]"


def _type_label(tt: int) -> str:
    return "T型" if tt == 0x00 else f"0x{tt:02X}"


def _temp_str(tag: TagData) -> str:
    if tag.low_battery:
        return "低电量"
    if tag.temperature is not None:
        return f"{tag.temperature:6.1f}°C"
    return "---"


def _print_dashboard(frame: TRGFrame, stats: Stats, rule_filter: RuleFilter,
                     stability: StabilityDetector = None):
    """设备仪表盘：清屏后持续刷新指定设备状态"""
    device_id = rule_filter.device_id

    # 从帧中找到目标设备
    found = None
    for tag in frame.tags:
        stats.tag_count += 1
        if tag.tag_id == device_id:
            found = tag
            stats.pass_count += 1
            break

    # 保存最新值
    if found:
        rule_filter._last_adc = found.adc
        rule_filter._last_rssi = found.rssi
        # 更新稳定性检测
        if stability is not None:
            stability.feed(found.tag_id, found.adc)
            is_stable, adc_mean, adc_range, n, span = stability.check(found.tag_id)
            if is_stable:
                rule_filter._stable_str = f"● 稳定 | 均值={adc_mean:.1f} | 波动=±{adc_range} | n={n} | {span:.1f}s"
            elif n >= 3:
                rule_filter._stable_str = f"○ 采集中 | 波动=±{adc_range} | n={n} | {span:.1f}s / {stability.window_sec:.0f}s"
            else:
                rule_filter._stable_str = f"○ 预热中 | n={n}"
    elif stability is not None:
        stability.feed(device_id, 0)

    stable_str = getattr(rule_filter, '_stable_str', '○ 等待首次数据...')
    last_adc = getattr(rule_filter, '_last_adc', None)
    last_rssi = getattr(rule_filter, '_last_rssi', 0)
    last_seen = getattr(rule_filter, '_last_seen', '')

    if found:
        last_seen = datetime.now().strftime('%H:%M:%S')
        rule_filter._last_seen = last_seen
    else:
        last_seen = getattr(rule_filter, '_last_seen', '—')

    ts = datetime.now().strftime('%H:%M:%S')

    os.system('cls' if os.name == 'nt' else 'clear')
    sys.stdout.write("=" * 55 + "\n")
    sys.stdout.write(f"  设备 {device_id} 实时监测  |  {ts}  |  运行 {stats.uptime()}\n")
    sys.stdout.write("=" * 55 + "\n")
    if last_adc is not None:
        rssi_bar_str = _rssi_bar(last_rssi)
        sys.stdout.write(f"  ADC:  {last_adc:<6}    RSSI:  {last_rssi}/255 {rssi_bar_str}\n")
        sys.stdout.write(f"  状态: {stable_str}\n")
    else:
        # 列出当前帧中所有标签，帮助判断 230030 是否在线
        nearby = [f"{t.tag_id}(RSSI{t.rssi})" for t in frame.tags]
        sys.stdout.write(f"  >>> 等待 {device_id} 的信号...\n")
        if nearby:
            sys.stdout.write(f"  当前帧内的设备: {', '.join(nearby[:10])}\n")
    sys.stdout.write(f"  最后更新: {last_seen}  |  帧#{stats.frame_count}  |  命中 {stats.pass_count} 次\n")
    sys.stdout.write("=" * 55 + "\n")
    sys.stdout.write("  Ctrl+C 退出\n")
    sys.stdout.flush()


def print_frame(frame: TRGFrame, stats: Stats, rule_filter: RuleFilter,
                stability: StabilityDetector = None, debug: bool = False):
    """打印完整帧信息。若 stability 非空，喂入 ADC 并显示稳定状态。
    设备模式 (device_id 指定) 使用仪表盘持续刷新。"""
    stats.frame_count += 1

    # ── 设备仪表盘模式 ──
    if rule_filter.device_id is not None:
        _print_dashboard(frame, stats, rule_filter, stability)
        return

    # ── 普通模式：逐帧打印 ──
    print()
    print("=" * 75)
    print(f"[{datetime.now().strftime('%H:%M:%S')}] "
          f"帧#{stats.frame_count} | 基站ID:{frame.station_id} "
          f"| SN:{frame.sn} | 标签数:{frame.tag_count} "
          f"| 数据:{frame.data_len}B | 运行:{stats.uptime()}")
    print("-" * 75)

    for i, tag in enumerate(frame.tags):
        stats.tag_count += 1

        # 每个标签附加原始 9 字节 hex（默认显示）
        tag_hex_str = ""
        if i < len(frame.raw):
            # 标签块起始偏移 = 9 + i*9 (header 2 + length 2 + station 2 + func 1 + SN 1 + count 1 + i*9)
            offset = 9 + i * 9
            end = min(offset + 9, len(frame.raw))
            tag_bytes = frame.raw[offset:end]
            tag_hex_str = f" | hex=[{tag_bytes.hex(' ')}]"

        if not rule_filter.check(tag):
            stats.filtered_count += 1
            # 如果是指定设备模式，被过滤的直接跳过不显示
            if rule_filter.device_id is None:
                ts = _temp_str(tag)
                print(f"  [SKIP] #{i + 1:<3} | ID:{tag.tag_id:>10} | {ts:>10} "
                      f"| RSSI:{tag.rssi:>3} [{_rssi_bar(tag.rssi)}] "
                      f"| ADC:{tag.adc:>5} | {_type_label(tag.tag_type)}{tag_hex_str}")
            continue

        if tag.low_battery:
            stats.low_battery_count += 1
            print(f"  [BAT] #{i + 1:<3} | ID:{tag.tag_id:>10} | {'低电量':>10} "
                  f"| RSSI:{tag.rssi:>3} [{_rssi_bar(tag.rssi)}] "
                  f"| 0xFFFF | {_type_label(tag.tag_type)} | [WARN] 需更换电池{tag_hex_str}")
            continue

        stats.pass_count += 1

        # ── 稳定性检测 ──
        stability_info = ""
        if stability is not None:
            stability.feed(tag.tag_id, tag.adc)
            is_stable, adc_mean, adc_range, n, span = stability.check(tag.tag_id)
            if is_stable:
                stability_info = (f" ● 稳定 | μ={adc_mean:.1f} | Δ={adc_range} "
                                  f"| n={n} | {span:.1f}s")
            elif n >= 3:
                stability_info = (f" ○ 未稳 | Δ={adc_range} | n={n} "
                                  f"| {span:.1f}s / {stability.window_sec:.0f}s")

        ts = _temp_str(tag)
        print(f"  [OK]  #{i + 1:<3} | ID:{tag.tag_id:>10} | {ts:>10} "
              f"| RSSI:{tag.rssi:>3} [{_rssi_bar(tag.rssi)}] "
              f"| ADC:{tag.adc:>5} | {_type_label(tag.tag_type)}"
              f"{stability_info}{tag_hex_str}")

    # 通过率
    print("-" * 75)
    total    = stats.tag_count
    passed   = stats.pass_count
    filtered = stats.filtered_count
    low      = stats.low_battery_count
    rate     = passed * 100 // max(total, 1)
    print(f"  总计:{total} | 通过:{passed} | 过滤:{filtered} "
          f"| 低电量:{low} | 通过率:{rate}%")


# ============================================================
# TCP 服务器
# ============================================================
class TRGServer:

    def __init__(self, host='0.0.0.0', port=20226, rule_filter=None, export_file=None,
                 stability: StabilityDetector = None, cal_file: str = None,
                 debug: bool = False, connect_to: str = None):
        self.host        = host
        self.port        = port
        self.rule_filter = rule_filter or RuleFilter()
        self.export_file = export_file
        self.stability   = stability
        self.cal_file    = cal_file
        self.debug       = debug
        self.connect_to  = connect_to  # None=Server模式, "ip:port"=Client模式
        self.stats       = Stats()
        self.socket      = None
        self.running     = False
        self.buffer      = b''
        self.csv_fp      = None
        self.cal_fp      = None

    # ---- 启动 ----

    def start(self):
        if self.connect_to:
            self._start_as_client()
        else:
            self._start_as_server()

    def _start_as_client(self):
        """TCP Client 模式：主动连接基站"""
        parts = self.connect_to.split(':')
        target_host = parts[0]
        target_port = int(parts[1]) if len(parts) > 1 else 20226

        self.running = True

        print(f"[网络] TCP Client 模式 -> 连接 {target_host}:{target_port}")
        print("[网络] 等待连接... (Ctrl+C 停止)\n")

        self._setup_files()

        try:
            while self.running:
                try:
                    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    sock.settimeout(5)
                    sock.connect((target_host, target_port))
                    print(f"\n[网络] 已连接到基站 {target_host}:{target_port}")
                    self.buffer = b''
                    self.socket = sock
                    self._handle_client(sock, (target_host, target_port))
                except socket.timeout:
                    continue
                except OSError as e:
                    print(f"[网络] 连接失败: {e}, 5秒后重试...")
                    time.sleep(5)
                    continue
        except KeyboardInterrupt:
            print("\n[网络] 停止...")
        finally:
            self.stop()

    def _setup_files(self):
        """初始化 CSV 和标定文件"""
        if self.export_file:
            self.csv_fp = open(self.export_file, 'w', encoding='utf-8-sig')
            self.csv_fp.write("时间,基站ID,SN,标签ID,类型,温度,RSSI,ADC,低电量,通过过滤\n")
            print(f"[导出] -> {self.export_file}")

        if self.cal_file:
            file_exists = os.path.isfile(self.cal_file)
            self.cal_fp = open(self.cal_file, 'a', encoding='utf-8-sig')
            if not file_exists or os.path.getsize(self.cal_file) == 0:
                self.cal_fp.write("标签ID,ADC均值,ADC极差,水浴温度(°C),记录时间,备注\n")
            print(f"[标定] 数据记录 -> {self.cal_file}")

        self.rule_filter.list_rules()
        print()

    def _start_as_server(self):
        """TCP Server 模式：等待基站连接"""
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

        try:
            self.socket.bind((self.host, self.port))
        except OSError:
            if self.host != '0.0.0.0':
                print(f"[网络] 绑定 {self.host} 失败，回退到 0.0.0.0...")
                self.socket.bind(('0.0.0.0', self.port))

        self.socket.listen(5)
        self.socket.settimeout(1.0)
        self.running = True

        print(f"[网络] TCP 服务器已启动 -> {self.host}:{self.port}")
        print("[网络] 等待 TRG 基站连接... (Ctrl+C 停止)\n")

        try:
            hostname = socket.gethostname()
            for ip in socket.gethostbyname_ex(hostname)[2]:
                print(f"       本机 IP: {ip}")
        except Exception:
            pass
        print()

        self._setup_files()

        try:
            while self.running:
                try:
                    client, addr = self.socket.accept()
                    print(f"\n[网络] TRG 基站已连接: {addr[0]}:{addr[1]}")
                    self.buffer = b''
                    self._handle_client(client, addr)
                except socket.timeout:
                    continue
                except OSError:
                    break
        except KeyboardInterrupt:
            print("\n[网络] 停止服务器...")
        finally:
            self.stop()

    # ---- 客户端处理 ----

    def _handle_client(self, client: socket.socket, addr):
        client.settimeout(10.0)
        try:
            while self.running:
                try:
                    data = client.recv(4096)
                except socket.timeout:
                    continue
                except OSError:
                    break

                if not data:
                    print(f"\n[网络] 基站 {addr[0]} 断开连接")
                    break

                self.buffer += data
                self._process_buffer()
        finally:
            client.close()

    # ---- 帧同步与解码 ----

    def _process_buffer(self):
        while True:
            idx = self.buffer.find(FRAME_HEADER)
            if idx == -1:
                if len(self.buffer) > 1:
                    self.buffer = self.buffer[-1:]
                return

            if idx > 0:
                print(f"[协议] 丢弃 {idx} 字节无效数据 (同步帧头)")
                self.buffer = self.buffer[idx:]

            if len(self.buffer) < 4:
                return

            data_len = struct.unpack_from('<H', self.buffer, 2)[0]
            frame_len = 2 + 2 + data_len + 1  # header + length + payload + checksum

            if frame_len > 8192:
                print(f"[协议] [WARN] 帧长度异常 ({data_len}B)，跳过")
                self.buffer = self.buffer[2:]
                continue

            if len(self.buffer) < frame_len:
                return

            frame_data = self.buffer[:frame_len]
            self.buffer = self.buffer[frame_len:]

            frame = parse_frame(frame_data, debug=self.debug)

            if not frame.valid:
                next_idx = self.buffer.find(FRAME_HEADER)
                if next_idx == -1 and len(self.buffer) > 1:
                    self.buffer = self.buffer[-1:]
                print("[协议] [WARN] 无效帧，尝试重新同步")
                continue

            print_frame(frame, self.stats, self.rule_filter, self.stability,
                       debug=self.debug)

            if self.csv_fp:
                self._export_frame(frame)

    # ---- CSV 导出 ----

    def _export_frame(self, frame: TRGFrame):
        ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        for tag in frame.tags:
            passed = self.rule_filter.check(tag) and not tag.low_battery
            self.csv_fp.write(
                f"{ts},{frame.station_id},{frame.sn},"
                f"{tag.tag_id},0x{tag.tag_type:02X},"
                f"{tag.temperature or ''},"
                f"{tag.rssi},{tag.adc},"
                f"{1 if tag.low_battery else 0},"
                f"{1 if passed else 0}\n"
            )
        self.csv_fp.flush()

    # ---- 停止 ----

    def stop(self):
        self.running = False
        if self.socket:
            self.socket.close()
        if self.csv_fp:
            self.csv_fp.close()
        if self.cal_fp:
            self.cal_fp.close()
        s = self.stats
        print(f"\n[统计] 总帧数:{s.frame_count} | 总标签数:{s.tag_count} | "
              f"通过:{s.pass_count} | 过滤:{s.filtered_count} | "
              f"低电量:{s.low_battery_count}\n")


# ============================================================
# 交互命令
# ============================================================
def _write_cal_record(server: TRGServer, tag_id: int, adc_mean: float,
                      adc_range: int, bath_temp: float, note: str = ""):
    """写一条标定记录到 cal_fp"""
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    line = f"{tag_id},{adc_mean:.1f},{adc_range},{bath_temp:.2f},{ts},{note}\n"
    if server.cal_fp:
        server.cal_fp.write(line)
        server.cal_fp.flush()
    print(f"[标定] ✓ 已记录 — ID:{tag_id}  ADC={adc_mean:.1f}  Δ={adc_range}  "
          f"水浴={bath_temp:.2f}°C  @ {ts}")


HELP_TEXT = """
交互命令:
  rule add <名称> <起始ID> <结束ID>  — 添加 ID 区间过滤规则
  rule del <名称>                      — 删除规则
  rule list                            — 列出所有规则
  stats                                — 显示统计
  export <文件名>                      — 开始导出 CSV
  export off                           — 停止导出

  —— 标定模式 (需 --calibrate) ——
  stable                               — 查看所有标签稳定状态
  rec <水浴温度> [标签ID]              — 记录当前稳定 ADC 值
  cal reset                            — 重置稳定性检测（换温度点后使用）
  cal list                             — 查看已记录数据

  clear                                — 清屏
  help                                 — 显示此帮助
  quit / exit                          — 退出
"""


def interactive_loop(server: TRGServer):
    print(HELP_TEXT)
    while server.running:
        try:
            cmd = input().strip()
        except (EOFError, KeyboardInterrupt):
            server.running = False
            break
        except OSError:
            break

        if not cmd:
            continue

        parts  = cmd.split()
        action = parts[0].lower()

        if action in ('quit', 'exit'):
            server.running = False
            break

        elif action == 'help':
            print(HELP_TEXT)

        elif action == 'stats':
            s = server.stats
            print(f"运行:{s.uptime()} | 帧:{s.frame_count} | 标签:{s.tag_count} "
                  f"| 通过:{s.pass_count} | 过滤:{s.filtered_count} "
                  f"| 低电量:{s.low_battery_count}")

        elif action == 'rule':
            if len(parts) < 2:
                print("用法: rule <add|del|list> ...")
                continue
            sub = parts[1].lower()
            if sub == 'add' and len(parts) >= 5:
                try:
                    s, e = int(parts[3]), int(parts[4])
                    if s > e:
                        print("[规则] [WARN] 起始编号不能大于结束编号")
                    else:
                        server.rule_filter.add_rule(parts[2], s, e)
                except ValueError:
                    print("[规则] [WARN] 编号必须是整数")
            elif sub == 'del' and len(parts) >= 3:
                server.rule_filter.remove_rule(parts[2])
            elif sub == 'list':
                server.rule_filter.list_rules()
            else:
                print("用法: rule add <名称> <起始ID> <结束ID>")
                print("      rule del <名称>")
                print("      rule list")

        elif action == 'clear':
            print("\033[2J\033[H", end="")

        elif action == 'export':
            if len(parts) >= 2:
                if parts[1] == 'off':
                    if server.csv_fp:
                        server.csv_fp.close()
                        server.csv_fp = None
                    print("[导出] 已停止")
                else:
                    if server.csv_fp:
                        server.csv_fp.close()
                    server.export_file = parts[1]
                    server.csv_fp = open(parts[1], 'w', encoding='utf-8-sig')
                    server.csv_fp.write("时间,基站ID,SN,标签ID,类型,温度,RSSI,ADC,低电量,通过过滤\n")
                    print(f"[导出] -> {parts[1]}")

        # ── 标定命令 ──
        elif action == 'rec':
            if server.stability is None:
                print("[标定] 请用 --calibrate 参数启动标定模式")
                continue
            if len(parts) < 2:
                print("用法: rec <水浴温度> [标签ID]")
                print("      rec 25.0          — 记录第一个稳定标签")
                print("      rec 25.0 175642   — 记录指定标签")
                continue
            try:
                bath_temp = float(parts[1])
            except ValueError:
                print(f"[标定] [WARN] 温度格式错误: {parts[1]}")
                continue

            target_id = int(parts[2]) if len(parts) >= 3 else None
            recorded = False

            if target_id is not None:
                # 记录指定标签
                last = server.stability.last_stable(target_id)
                if last is None:
                    print(f"[标定] [WARN] 标签 {target_id} 尚未稳定，请等待")
                else:
                    adc_mean, adc_range = last
                    _write_cal_record(server, target_id, adc_mean, adc_range, bath_temp)
                    recorded = True
            else:
                # 自动找第一个已稳定的标签
                found_stable = []
                for tag_id in server.stability._history:
                    is_stable, adc_mean, adc_range, _, _ = server.stability.check(tag_id)
                    if is_stable:
                        found_stable.append((tag_id, adc_mean, adc_range))
                if not found_stable:
                    # 尝试用 last_stable
                    for tag_id, last in list(server.stability._last_stable.items()):
                        found_stable.append((tag_id, last[0], last[1]))
                if not found_stable:
                    print("[标定] [WARN] 没有稳定标签，请等待 ○→●")
                elif len(found_stable) == 1:
                    tid, am, ar = found_stable[0]
                    _write_cal_record(server, tid, am, ar, bath_temp)
                    recorded = True
                else:
                    print(f"[标定] 发现 {len(found_stable)} 个稳定标签，请指定 ID:")
                    for tid, am, ar in found_stable:
                        print(f"       ID:{tid}  ADC={am:.1f}  Δ={ar}")

            if recorded:
                server.stability.reset()  # 记录后重置，准备下一个温度点
                print("[标定] 已重置稳定性检测，可开始下一个温度点")

        elif action == 'stable':
            if server.stability is None:
                print("[标定] 请用 --calibrate 参数启动标定模式")
                continue
            if not server.stability._history:
                print("[标定] 尚无数据")
                continue
            print(f"{'标签ID':<12} {'ADC均值':<10} {'ADC极差':<10} {'采样数':<8} {'时间跨度':<10} {'状态':<8}")
            print("-" * 60)
            for tag_id in sorted(server.stability._history.keys()):
                is_stable, adc_mean, adc_range, n, span = server.stability.check(tag_id)
                status = "● 稳定" if is_stable else "○ 未稳"
                adc_mean_str = f"{adc_mean:.1f}" if adc_mean is not None else "---"
                adc_range_str = str(adc_range) if adc_range is not None else "---"
                print(f"{tag_id:<12} {adc_mean_str:<10} {adc_range_str:<10} {n:<8} {span:.1f}s{'':<5} {status:<8}")

        elif action == 'cal':
            if len(parts) < 2:
                print("用法: cal reset — 重置稳定性检测")
                print("      cal list  — 查看标定记录文件")
                continue
            sub = parts[1].lower()
            if sub == 'reset':
                if server.stability:
                    server.stability.reset()
                print("[标定] 稳定性检测已重置")
            elif sub == 'list':
                if server.cal_file and os.path.isfile(server.cal_file):
                    print(f"[标定] 记录文件: {server.cal_file}")
                    with open(server.cal_file, 'r', encoding='utf-8-sig') as f:
                        lines = f.readlines()
                    if len(lines) <= 1:
                        print("       (空)")
                    else:
                        for line in lines[:1]:  # header
                            print(f"       {line.rstrip()}")
                        print(f"       ... 共 {len(lines)-1} 条记录")
                        for line in lines[-5:]:  # last 5
                            print(f"       {line.rstrip()}")
                else:
                    print("[标定] 未指定标定文件，请用 --cal-output 参数")

        else:
            print(f"未知命令: {action}，输入 help 查看帮助")


# ============================================================
# IP 选择
# ============================================================
def list_ips() -> list:
    """列出本机所有 IPv4 地址"""
    ips = []
    try:
        hostname = socket.gethostname()
        for ip in socket.gethostbyname_ex(hostname)[2]:
            if ip != '127.0.0.1':
                ips.append(ip)
    except Exception:
        pass
    return ips


def select_ip(specified: str = None) -> str:
    """交互式选择监听 IP。

    如果命令行已指定 --ip (非默认值) 则直接使用；
    否则列出本机 IP 让用户选择，默认监听所有接口 (0.0.0.0)。
    """
    if specified:
        return specified

    ips = list_ips()
    print("=" * 60)
    print("  选择监听 IP")
    print("=" * 60)
    print()
    print("  本机可用 IP:")
    if not ips:
        print("    (未检测到网络接口)")
    else:
        for i, ip in enumerate(ips, 1):
            print(f"    [{i}] {ip}")
    print()
    print("  提示: TRG 基站需要配置为连接到这个 IP")
    print(f"    [0] 0.0.0.0 — 监听所有接口 (推荐)")
    print()

    while True:
        try:
            choice = input(f"  请选择 [{', '.join(str(i) for i in range(len(ips)+1))}] (默认 0): ").strip()
            if choice == '' or choice == '0':
                return '0.0.0.0'
            idx = int(choice)
            if 1 <= idx <= len(ips):
                return ips[idx - 1]
        except (ValueError, KeyboardInterrupt):
            pass
        print("  无效选择，请重试")


# ============================================================
# 主函数
# ============================================================
def main():
    parser = argparse.ArgumentParser(
        description='TRG TTag 产线监控 — TCP 网络监听版',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python ttag_monitor.py                                    # 交互选择 IP, 默认端口
  python ttag_monitor.py --ip 192.168.3.187                  # 指定监听 IP
  python ttag_monitor.py --port 8899                        # 自定义端口
  python ttag_monitor.py --type 0x00,0x03                   # 多种标签类型
  python ttag_monitor.py --rule A线 175000 175999            # 过滤 ID 区间
  python ttag_monitor.py --export result.csv                 # 导出 CSV
        """
    )
    parser.add_argument('--ip',     default=None,
                        help='监听 IP (不指定则交互选择)')
    parser.add_argument('--port',   type=int, default=20226, help='监听端口 (默认 20226)')
    parser.add_argument('--rule',   nargs=3, action='append', default=[],
                        metavar=('NAME', 'START', 'END'),
                        help='添加过滤规则 (可多次使用)')
    parser.add_argument('--type',   default='0x00',
                        help='仅显示指定标签类型 (默认 0x00=T型, 用逗号分隔)')
    parser.add_argument('--export', default=None, help='导出 CSV 文件路径')
    parser.add_argument('--calibrate', action='store_true',
                        help='启用标定模式：检测 ADC 稳定后可记录')
    parser.add_argument('--cal-output', default='calibration_data.csv',
                        help='标定数据输出文件 (默认 calibration_data.csv)')
    parser.add_argument('--stability-window', type=float, default=10.0,
                        help='稳定判定时间窗口/秒 (默认 10)')
    parser.add_argument('--stability-threshold', type=int, default=2,
                        help='稳定判定 ADC 极差阈值 (默认 2)')
    parser.add_argument('--debug', action='store_true',
                        help='调试模式：显示原始 hex 和校验和验证')
    parser.add_argument('--connect', default=None, metavar='IP:PORT',
                        help='Client 模式：主动连接基站 (如 192.168.3.188:20226)')
    parser.add_argument('--device', type=int, default=None,
                        help='仅显示指定设备 ID (如 230030)')

    args = parser.parse_args()

    # ---- 选择监听 IP ----
    host = select_ip(args.ip)

    # ---- 构建过滤器 ----
    rule_filter = RuleFilter()

    # 指定设备 ID
    if args.device is not None:
        rule_filter.device_id = args.device
    else:
        # 交互式输入
        try:
            dev_input = input("请输入要筛选的设备 ID (留空显示全部): ").strip()
            if dev_input:
                rule_filter.device_id = int(dev_input)
        except (EOFError, KeyboardInterrupt):
            pass

    # 标签类型
    if args.type:
        type_set = set()
        for t in args.type.split(','):
            t = t.strip()
            type_set.add(int(t, 16) if t.lower().startswith('0x') else int(t))
        rule_filter.type_filter = type_set
        names = [f"0x{tt:02X}" for tt in sorted(type_set)]
        print(f"[类型] 仅显示: {', '.join(names)}")

    # ID 区间规则
    for name, start, end in args.rule:
        rule_filter.add_rule(name, int(start), int(end))

    # ---- 标定模式 ----
    stability = None
    cal_file = None
    if args.calibrate:
        stability = StabilityDetector(
            window_sec=args.stability_window,
            adc_threshold=args.stability_threshold,
        )
        cal_file = args.cal_output
        print(f"[标定] 模式已启用 | 窗口={args.stability_window}s "
              f"| ADC阈值=±{args.stability_threshold} | 输出={cal_file}")

    # ---- 启动 ----
    server = TRGServer(
        host=host,
        port=args.port,
        rule_filter=rule_filter,
        export_file=args.export,
        stability=stability,
        cal_file=cal_file,
        debug=args.debug,
        connect_to=args.connect,
    )

    threading.Thread(target=interactive_loop, args=(server,), daemon=True).start()
    server.start()


if __name__ == '__main__':
    main()
