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
import socket
import struct
import sys
import threading
import time
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


def parse_frame(data: bytes) -> TRGFrame:
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

        frame.station_id = struct.unpack_from('<H', data, pos)[0]
        pos += 2

        frame.func_code = data[pos]; pos += 1
        frame.sn        = data[pos]; pos += 1
        frame.tag_count = data[pos]; pos += 1

        for _ in range(frame.tag_count):
            if pos + 9 > len(data):
                break

            tag = TagData()

            tag.rssi     = data[pos]; pos += 1
            tag.tag_type = data[pos]; pos += 1

            # ID: 3 字节大端序
            tag.tag_id = (data[pos] << 16) | (data[pos + 1] << 8) | data[pos + 2]
            pos += 3

            # ADC: 2 字节小端序
            tag.adc = struct.unpack_from('<H', data, pos)[0]
            pos += 2

            # 保留
            tag.reserved = struct.unpack_from('<H', data, pos)[0]
            pos += 2

            if tag.adc == LOW_BATTERY_ADC:
                tag.low_battery = True
                tag.temperature = None
            else:
                tag.temperature = adc_to_temperature(tag.adc)

            frame.tags.append(tag)

        if pos < len(data):
            frame.checksum = data[pos]

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
        if not self.rules:
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
        # 1. ID 区间
        if self.rules:
            if not any(s <= tag.tag_id <= e for _, s, e in self.rules):
                return False

        # 2. 标签类型
        if self.type_filter is not None and tag.tag_type not in self.type_filter:
            return False

        # 3. ADC 范围（低电量除外）
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


def print_frame(frame: TRGFrame, stats: Stats, rule_filter: RuleFilter):
    """打印完整帧信息"""
    stats.frame_count += 1

    print()
    print("=" * 75)
    print(f"[{datetime.now().strftime('%H:%M:%S')}] "
          f"帧#{stats.frame_count} | 基站ID:{frame.station_id} "
          f"| SN:{frame.sn} | 标签数:{frame.tag_count} "
          f"| 数据:{frame.data_len}B | 运行:{stats.uptime()}")
    print("-" * 75)

    for i, tag in enumerate(frame.tags):
        stats.tag_count += 1

        if not rule_filter.check(tag):
            stats.filtered_count += 1
            ts = _temp_str(tag)
            print(f"  [SKIP] #{i + 1:<3} | ID:{tag.tag_id:>10} | {ts:>10} "
                  f"| RSSI:{tag.rssi:>3} [{_rssi_bar(tag.rssi)}] "
                  f"| ADC:{tag.adc:>5} | {_type_label(tag.tag_type)}")
            continue

        if tag.low_battery:
            stats.low_battery_count += 1
            print(f"  [BAT] #{i + 1:<3} | ID:{tag.tag_id:>10} | {'低电量':>10} "
                  f"| RSSI:{tag.rssi:>3} [{_rssi_bar(tag.rssi)}] "
                  f"| 0xFFFF | {_type_label(tag.tag_type)} | [WARN] 需更换电池")
            continue

        stats.pass_count += 1
        ts = _temp_str(tag)
        print(f"  [OK]  #{i + 1:<3} | ID:{tag.tag_id:>10} | {ts:>10} "
              f"| RSSI:{tag.rssi:>3} [{_rssi_bar(tag.rssi)}] "
              f"| ADC:{tag.adc:>5} | {_type_label(tag.tag_type)}")

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

    def __init__(self, host='0.0.0.0', port=20226, rule_filter=None, export_file=None):
        self.host        = host
        self.port        = port
        self.rule_filter = rule_filter or RuleFilter()
        self.export_file = export_file
        self.stats       = Stats()
        self.socket      = None
        self.running     = False
        self.buffer      = b''
        self.csv_fp      = None

    # ---- 启动 ----

    def start(self):
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

        if self.export_file:
            self.csv_fp = open(self.export_file, 'w', encoding='utf-8-sig')
            self.csv_fp.write("时间,基站ID,SN,标签ID,类型,温度,RSSI,ADC,低电量,通过过滤\n")
            print(f"[导出] -> {self.export_file}")

        self.rule_filter.list_rules()
        print()

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

            frame = parse_frame(frame_data)

            if not frame.valid:
                next_idx = self.buffer.find(FRAME_HEADER)
                if next_idx == -1 and len(self.buffer) > 1:
                    self.buffer = self.buffer[-1:]
                print("[协议] [WARN] 无效帧，尝试重新同步")
                continue

            print_frame(frame, self.stats, self.rule_filter)

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
        s = self.stats
        print(f"\n[统计] 总帧数:{s.frame_count} | 总标签数:{s.tag_count} | "
              f"通过:{s.pass_count} | 过滤:{s.filtered_count} | "
              f"低电量:{s.low_battery_count}\n")


# ============================================================
# 交互命令
# ============================================================
HELP_TEXT = """
交互命令:
  rule add <名称> <起始ID> <结束ID>  — 添加 ID 区间过滤规则
  rule del <名称>                      — 删除规则
  rule list                            — 列出所有规则
  stats                                — 显示统计
  export <文件名>                      — 开始导出 CSV
  export off                           — 停止导出
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

        else:
            print(f"未知命令: {action}，输入 help 查看帮助")


# ============================================================
# 主函数
# ============================================================
def main():
    parser = argparse.ArgumentParser(
        description='TRG TTag 产线监控 — TCP 网络监听版',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python ttag_monitor.py                                    # 默认 0.0.0.0:20226, 仅T型
  python ttag_monitor.py --port 8899                        # 自定义端口
  python ttag_monitor.py --type 0x00,0x03                   # 多种标签类型
  python ttag_monitor.py --rule A线 175000 175999            # 过滤 ID 区间
  python ttag_monitor.py --export result.csv                 # 导出 CSV
        """
    )
    parser.add_argument('--ip',     default='0.0.0.0', help='监听 IP (默认 0.0.0.0)')
    parser.add_argument('--port',   type=int, default=20226, help='监听端口 (默认 20226)')
    parser.add_argument('--rule',   nargs=3, action='append', default=[],
                        metavar=('NAME', 'START', 'END'),
                        help='添加过滤规则 (可多次使用)')
    parser.add_argument('--type',   default='0x00',
                        help='仅显示指定标签类型 (默认 0x00=T型, 用逗号分隔)')
    parser.add_argument('--export', default=None, help='导出 CSV 文件路径')

    args = parser.parse_args()

    # ---- 构建过滤器 ----
    rule_filter = RuleFilter()

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

    # ---- 启动 ----
    server = TRGServer(
        host=args.ip,
        port=args.port,
        rule_filter=rule_filter,
        export_file=args.export,
    )

    threading.Thread(target=interactive_loop, args=(server,), daemon=True).start()
    server.start()


if __name__ == '__main__':
    main()
