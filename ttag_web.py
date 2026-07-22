#!/usr/bin/env python3
"""
TRG TTag Web 仪表盘
====================
浏览器打开 http://localhost:8080 即可实时监控。

用法:
  python ttag_web.py                          # 默认端口 8080 & 20226
  python ttag_web.py --http-port 9000          # 自定义 Web 端口
  python ttag_web.py --tcp-port 20227          # 自定义 TCP 端口
  python ttag_web.py --ip 192.168.3.187        # 指定监听 IP (跳过交互)
  python ttag_web.py --type 0x00,0x03          # 多种标签类型

依赖: 纯 Python 标准库 (无 pip install)
"""

import argparse
import json
import queue
import socket
import struct
import sys
import threading
import time
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler

# ---- 从 ttag_monitor 导入核心组件 ----
from ttag_monitor import (
    FRAME_HEADER, LOW_BATTERY_ADC,
    TagData, TRGFrame, parse_frame, RuleFilter, Stats,
    adc_to_temperature, select_ip, list_ips,
)

# ============================================================
# 消息总线 (线程安全)
# ============================================================
_event_queue = queue.Queue(maxsize=200)

def broadcast(event: str, data: dict):
    """向所有 Web 客户端推送事件"""
    try:
        _event_queue.put_nowait((event, data))
    except queue.Full:
        try:
            _event_queue.get_nowait()
            _event_queue.put_nowait((event, data))
        except queue.Empty:
            pass


# ============================================================
# TCP 服务器 (复用 ttag_monitor 核心, 增加 Web hook)
# ============================================================
class WebTRGServer:
    """TRG TCP 服务器 — 解析帧并广播到 Web"""

    def __init__(self, host='0.0.0.0', port=20226, rule_filter=None):
        self.host        = host
        self.port        = port
        self.rule_filter = rule_filter or RuleFilter()
        self.stats       = Stats()
        self.socket      = None
        self.running     = False
        self.buffer      = b''
        self._connected  = False
        self._client_addr = None

    @property
    def connected(self):
        return self._connected

    def start(self):
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            self.socket.bind((self.host, self.port))
        except OSError:
            if self.host != '0.0.0.0':
                self.socket.bind(('0.0.0.0', self.port))
        self.socket.listen(5)
        self.socket.settimeout(1.0)
        self.running = True

        print(f"[TCP ] 监听 {self.host}:{self.port}, 等待 TRG 基站...")

        while self.running:
            try:
                client, addr = self.socket.accept()
                self._client_addr = f"{addr[0]}:{addr[1]}"
                self._connected = True
                print(f"[TCP ] 基站已连接: {self._client_addr}")
                broadcast("status", {"connected": True, "client": self._client_addr})
                self.buffer = b''
                self._handle_client(client, addr)
            except socket.timeout:
                continue
            except OSError:
                break
            finally:
                if self._connected:
                    self._connected = False
                    broadcast("status", {"connected": False, "client": None})

        if self.socket:
            self.socket.close()

    def _handle_client(self, client: socket.socket, addr):
        client.settimeout(5.0)
        try:
            while self.running:
                try:
                    data = client.recv(4096)
                except socket.timeout:
                    continue
                except OSError:
                    break
                if not data:
                    print(f"[TCP ] 基站 {addr[0]} 断开")
                    break
                self.buffer += data
                self._process_buffer()
        finally:
            client.close()

    def _process_buffer(self):
        while True:
            idx = self.buffer.find(FRAME_HEADER)
            if idx == -1:
                if len(self.buffer) > 1:
                    self.buffer = self.buffer[-1:]
                return
            if idx > 0:
                self.buffer = self.buffer[idx:]

            if len(self.buffer) < 4:
                return

            data_len = struct.unpack_from('<H', self.buffer, 2)[0]
            frame_len = 2 + 2 + data_len + 1

            if frame_len > 8192:
                self.buffer = self.buffer[2:]
                continue
            if len(self.buffer) < frame_len:
                return

            frame_data = self.buffer[:frame_len]
            self.buffer = self.buffer[frame_len:]

            frame = parse_frame(frame_data)
            if not frame.valid:
                continue

            # 统计
            self.stats.frame_count += 1
            for tag in frame.tags:
                self.stats.tag_count += 1
                if not self.rule_filter.check(tag):
                    self.stats.filtered_count += 1
                elif tag.low_battery:
                    self.stats.low_battery_count += 1
                else:
                    self.stats.pass_count += 1

            # 广播到 Web
            self._broadcast_frame(frame)

    def _broadcast_frame(self, frame: TRGFrame):
        tags_data = []
        for tag in frame.tags:
            passed = self.rule_filter.check(tag) and not tag.low_battery
            tags_data.append({
                "rssi":       tag.rssi,
                "tag_type":   tag.tag_type,
                "tag_id":     tag.tag_id,
                "adc":        tag.adc,
                "low_battery": tag.low_battery,
                "temperature": tag.temperature,
                "passed":     passed,
            })

        broadcast("frame", {
            "ts":          datetime.now().strftime('%H:%M:%S'),
            "station_id":  frame.station_id,
            "sn":          frame.sn,
            "tag_count":   frame.tag_count,
            "tags":        tags_data,
            "stats": {
                "frames":    self.stats.frame_count,
                "tags":      self.stats.tag_count,
                "passed":    self.stats.pass_count,
                "filtered":  self.stats.filtered_count,
                "low_battery": self.stats.low_battery_count,
                "uptime":    self.stats.uptime(),
            },
        })

    def stop(self):
        self.running = False
        if self.socket:
            self.socket.close()


# ============================================================
# HTTP 服务器 + SSE
# ============================================================
_SSE_CLIENTS = []
_SSE_LOCK = threading.Lock()

# ---- HTML 页面 (内嵌) ----
DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>TRG TTag 监控仪表盘</title>
<style>
:root {
  --bg: #0f1923;
  --card: #1a2736;
  --border: #2a3a4a;
  --text: #d0d8e0;
  --dim: #6a7a8a;
  --green: #00c853;
  --yellow: #ffc107;
  --red: #ff3d3d;
  --blue: #2196f3;
  --tag: #00bcd4;
}
* { margin:0; padding:0; box-sizing:border-box; }
body {
  background: var(--bg); color: var(--text);
  font-family: 'Consolas', 'Courier New', monospace;
  min-height: 100vh;
}

/* ---- 顶栏 ---- */
.topbar {
  background: var(--card); border-bottom: 1px solid var(--border);
  display: flex; align-items: center; gap: 24px;
  padding: 12px 24px; position: sticky; top: 0; z-index: 10;
}
.topbar h1 { font-size: 18px; letter-spacing: 2px; color: var(--tag); }
.status-dot { width: 10px; height: 10px; border-radius: 50%; display: inline-block; }
.status-dot.online { background: var(--green); box-shadow: 0 0 8px var(--green); }
.status-dot.offline { background: var(--red); box-shadow: 0 0 8px var(--red); }
.status-text { font-size: 13px; color: var(--dim); }

/* ---- 统计卡片 ---- */
.stat-cards {
  display: flex; gap: 16px; padding: 16px 24px; flex-wrap: wrap;
}
.stat-card {
  background: var(--card); border: 1px solid var(--border);
  border-radius: 6px; padding: 14px 20px; min-width: 110px;
}
.stat-card .value { font-size: 28px; font-weight: bold; color: var(--blue); }
.stat-card .label { font-size: 12px; color: var(--dim); margin-top: 4px; }
.stat-card.green .value { color: var(--green); }
.stat-card.red .value { color: var(--red); }
.stat-card.yellow .value { color: var(--yellow); }

/* ---- 主表格 ---- */
.table-wrap {
  margin: 0 24px 16px; background: var(--card);
  border: 1px solid var(--border); border-radius: 6px;
  overflow-y: auto; max-height: calc(100vh - 320px);
}
table { width: 100%; border-collapse: collapse; font-size: 13px; }
thead { position: sticky; top: 0; background: var(--card); z-index: 2; }
th {
  text-align: left; padding: 10px 14px; color: var(--dim);
  border-bottom: 2px solid var(--border); font-weight: normal;
  font-size: 11px; text-transform: uppercase; letter-spacing: 1px;
}
td { padding: 8px 14px; border-bottom: 1px solid var(--border); }
tr:hover { background: rgba(255,255,255,0.03); }

/* 状态标记 */
.badge {
  display: inline-block; padding: 3px 8px; border-radius: 3px;
  font-size: 11px; font-weight: bold;
}
.badge.pass { background: rgba(0,200,83,0.15); color: var(--green); }
.badge.skip { background: rgba(255,255,255,0.05); color: var(--dim); }
.badge.bat  { background: rgba(255,61,61,0.15); color: var(--red); }
.badge.t-type { background: rgba(0,188,212,0.15); color: var(--tag); }

/* RSSI 条 */
.rssi-bar { display: inline-flex; gap: 2px; align-items: center; }
.rssi-block {
  width: 6px; height: 14px; border-radius: 1px; background: #333;
}
.rssi-block.on { background: var(--green); }
.rssi-block.mid { background: var(--yellow); }
.rssi-block.low { background: var(--red); }

/* 帧头行 */
.frame-row td {
  background: rgba(33,150,243,0.06);
  color: var(--blue); font-size: 12px; padding: 6px 14px;
}

/* ---- 底栏 ---- */
.bottombar {
  padding: 10px 24px; font-size: 12px; color: var(--dim);
  border-top: 1px solid var(--border);
}

/* 空状态 */
.empty-state {
  text-align: center; padding: 60px 20px; color: var(--dim);
}
.empty-state .icon { font-size: 48px; margin-bottom: 12px; }

/* ---- 响应式 ---- */
@media (max-width: 768px) {
  .stat-cards { gap: 8px; padding: 10px; }
  .stat-card { padding: 10px 14px; min-width: 70px; }
  .stat-card .value { font-size: 20px; }
  .table-wrap { margin: 0 8px; }
  th, td { padding: 6px 8px; font-size: 11px; }
}
</style>
</head>
<body>

<div class="topbar">
  <h1>TTag Monitor</h1>
  <span id="status-dot" class="status-dot offline"></span>
  <span class="status-text" id="status-label">等待基站...</span>
  <span style="margin-left:auto;font-size:12px;color:var(--dim)" id="uptime">运行: 0s</span>
</div>

<div class="stat-cards">
  <div class="stat-card"><div class="value" id="s-frames">0</div><div class="label">帧数</div></div>
  <div class="stat-card"><div class="value" id="s-tags">0</div><div class="label">标签总数</div></div>
  <div class="stat-card green"><div class="value" id="s-passed">0</div><div class="label">通过</div></div>
  <div class="stat-card yellow"><div class="value" id="s-filtered">0</div><div class="label">过滤</div></div>
  <div class="stat-card red"><div class="value" id="s-bat">0</div><div class="label">低电量</div></div>
  <div class="stat-card"><div class="value" id="s-rate">--</div><div class="label">通过率</div></div>
</div>

<div class="table-wrap" id="table-wrap">
  <div class="empty-state" id="empty-hint">
    <div class="icon">[  ]</div>
    <div>等待 TRG 基站连接...</div>
    <div style="margin-top:8px;font-size:11px">基站连接后数据将实时显示</div>
  </div>
  <table id="tag-table" style="display:none">
    <thead>
      <tr>
        <th>时间</th><th>帧#</th><th>状态</th><th>标签 ID</th>
        <th>类型</th><th>ADC</th><th>温度</th>
        <th>RSSI</th><th>信号</th>
      </tr>
    </thead>
    <tbody id="tag-tbody"></tbody>
  </table>
</div>

<div class="bottombar" id="bottombar">
  就绪 — 打开浏览器即用，无需安装任何依赖
</div>

<script>
const MAX_ROWS = 500;
let frameIdx = 0;
let rowCount = 0;

function rssiBar(rssi) {
  let bars = '';
  const level = rssi > 180 ? 4 : rssi > 150 ? 3 : rssi > 120 ? 2 : rssi > 90 ? 1 : 0;
  let cls = 'on';
  for (let i = 0; i < 4; i++) {
    if (i >= level) cls = (i === 3 && level === 3) ? 'mid' : 'low';
    bars += `<span class="rssi-block ${i < level ? cls : ''}"></span>`;
  }
  return `<span class="rssi-bar">${bars} ${rssi}</span>`;
}

function typeLabel(tt) {
  return tt === 0 ? 'T型' : '0x' + tt.toString(16).toUpperCase().padStart(2, '0');
}

function addFrameRow(data) {
  frameIdx++;
  const ts = data.ts;
  const sn = data.sn;
  const tbody = document.getElementById('tag-tbody');

  // 帧头行
  let html = `<tr class="frame-row"><td>${ts}</td><td>#${frameIdx} SN:${sn}</td>`;
  html += `<td colspan="7">基站:${data.station_id} | 标签:${data.tag_count}</td></tr>`;

  for (const tag of data.tags) {
    let statusHtml, badgeCls;
    if (tag.low_battery) {
      statusHtml = 'BAT'; badgeCls = 'bat';
    } else if (tag.passed) {
      statusHtml = 'OK'; badgeCls = 'pass';
    } else {
      statusHtml = 'SKIP'; badgeCls = 'skip';
    }

    const temp = tag.low_battery ? '低电量'
      : (tag.temperature != null ? tag.temperature.toFixed(1) + '°C' : '---');
    const adc = tag.low_battery ? '0xFFFF' : tag.adc;

    html += `<tr>`;
    html += `<td></td><td></td>`;
    html += `<td><span class="badge ${badgeCls}">${statusHtml}</span></td>`;
    html += `<td>${tag.tag_id}</td>`;
    html += `<td><span class="badge t-type">${typeLabel(tag.tag_type)}</span></td>`;
    html += `<td>${adc}</td>`;
    html += `<td>${temp}</td>`;
    html += `<td>${tag.rssi}</td>`;
    html += `<td>${rssiBar(tag.rssi)}</td>`;
    html += `</tr>`;
  }

  tbody.insertAdjacentHTML('beforeend', html);

  // 修剪旧行
  while (tbody.children.length > MAX_ROWS * 2) {
    for (let i = 0; i < 10 && tbody.firstChild; i++) {
      tbody.removeChild(tbody.firstChild);
    }
  }

  // 自动滚动
  const wrap = document.getElementById('table-wrap');
  wrap.scrollTop = wrap.scrollHeight;
}

function updateStats(s) {
  document.getElementById('s-frames').textContent = s.frames;
  document.getElementById('s-tags').textContent = s.tags;
  document.getElementById('s-passed').textContent = s.passed;
  document.getElementById('s-filtered').textContent = s.filtered;
  document.getElementById('s-bat').textContent = s.low_battery;

  const total = s.passed + s.filtered + s.low_battery;
  const rate = total > 0 ? Math.round(s.passed * 100 / total) : 0;
  document.getElementById('s-rate').textContent = rate + '%';

  document.getElementById('uptime').textContent = '运行: ' + s.uptime;
}

function updateStatus(connected, client) {
  const dot = document.getElementById('status-dot');
  const label = document.getElementById('status-label');
  if (connected) {
    dot.className = 'status-dot online';
    label.textContent = '基站已连接: ' + client;
  } else {
    dot.className = 'status-dot offline';
    label.textContent = '等待基站...';
  }
}

// SSE
const es = new EventSource('/events');
let firstData = true;

es.addEventListener('frame', function(e) {
  if (firstData) {
    document.getElementById('empty-hint').style.display = 'none';
    document.getElementById('tag-table').style.display = '';
    firstData = false;
  }
  const data = JSON.parse(e.data);
  addFrameRow(data);
  if (data.stats) updateStats(data.stats);
});

es.addEventListener('status', function(e) {
  const data = JSON.parse(e.data);
  updateStatus(data.connected, data.client);
});

es.onerror = function() {
  updateStatus(false, null);
};
</script>
</body>
</html>"""


class SSERequestHandler(BaseHTTPRequestHandler):

    def log_message(self, format, *args):
        pass  # 静默 HTTP 日志

    def do_GET(self):
        if self.path == '/':
            self._serve_html()
        elif self.path == '/events':
            self._serve_sse()
        elif self.path == '/api/stats':
            self._serve_json(_get_stats())
        elif self.path == '/api/rules':
            self._serve_json(_get_rules())
        elif self.path.startswith('/api/export'):
            self._serve_csv()
        else:
            self.send_error(404)

    def do_POST(self):
        if self.path.startswith('/api/rules/add'):
            self._handle_add_rule()
        elif self.path.startswith('/api/rules/del'):
            self._handle_del_rule()
        else:
            self.send_error(404)

    # ---- 响应 ----

    def _serve_html(self):
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Cache-Control', 'no-cache')
        self.end_headers()
        self.wfile.write(DASHBOARD_HTML.encode('utf-8'))

    def _serve_sse(self):
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.send_header('Cache-Control', 'no-cache')
        self.send_header('Connection', 'keep-alive')
        self.send_header('X-Accel-Buffering', 'no')
        self.end_headers()

        with _SSE_LOCK:
            _SSE_CLIENTS.append(self)

        try:
            while True:
                try:
                    event, data = _event_queue.get(timeout=15)
                    payload = f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
                    self.wfile.write(payload.encode('utf-8'))
                    self.wfile.flush()
                except queue.Empty:
                    # 心跳
                    self.wfile.write(b": heartbeat\n\n")
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, OSError):
                    break
        finally:
            with _SSE_LOCK:
                if self in _SSE_CLIENTS:
                    _SSE_CLIENTS.remove(self)

    def _serve_json(self, data):
        body = json.dumps(data, ensure_ascii=False).encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_csv(self):
        self.send_response(200)
        self.send_header('Content-Type', 'text/csv; charset=utf-8')
        self.send_header('Content-Disposition',
                         f'attachment; filename="ttag_{datetime.now().strftime("%Y%m%d_%H%M%S")}.csv"')
        self.end_headers()
        self.wfile.write("时间,基站ID,SN,标签ID,类型,ADC,RSSI,低电量,通过过滤\n".encode('utf-8-sig'))
        # 从全局 server 读取最近标签数据导出
        # (简化实现: 返回空CSV框架, 后续可扩展)

    def _handle_add_rule(self):
        length = int(self.headers.get('Content-Length', 0))
        body = json.loads(self.rfile.read(length))
        name, start, end = body.get('name'), body.get('start'), body.get('end')
        ok = _global_rule_filter.add_rule(name, int(start), int(end))
        self._serve_json({"ok": ok})

    def _handle_del_rule(self):
        length = int(self.headers.get('Content-Length', 0))
        body = json.loads(self.rfile.read(length))
        _global_rule_filter.remove_rule(body.get('name', ''))
        self._serve_json({"ok": True})


# ---- 全局状态 ----
_global_server = None
_global_rule_filter = None

def _get_stats():
    if _global_server is None:
        return {"frames": 0, "tags": 0, "passed": 0, "filtered": 0, "low_battery": 0, "uptime": "0s"}
    s = _global_server.stats
    return {
        "frames":    s.frame_count,
        "tags":      s.tag_count,
        "passed":    s.pass_count,
        "filtered":  s.filtered_count,
        "low_battery": s.low_battery_count,
        "uptime":    s.uptime(),
        "connected": _global_server.connected,
    }

def _get_rules():
    if _global_rule_filter is None:
        return {"rules": [], "type_filter": None}
    rules = [{"name": n, "start": s, "end": e} for n, s, e in _global_rule_filter.rules]
    type_filter = (sorted(list(_global_rule_filter.type_filter))
                   if _global_rule_filter.type_filter else None)
    return {"rules": rules, "type_filter": type_filter}


# ============================================================
# 主函数
# ============================================================
def main():
    global _global_server, _global_rule_filter

    parser = argparse.ArgumentParser(
        description='TRG TTag Web 仪表盘',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python ttag_web.py                               # 默认端口
  python ttag_web.py --http-port 9000               # 自定义 Web 端口
  python ttag_web.py --tcp-port 20227               # 自定义 TCP 端口
  python ttag_web.py --ip 192.168.3.187             # 直接指定监听 IP
  python ttag_web.py --type 0x00,0x03               # 多种标签类型
        """
    )
    parser.add_argument('--http-port', type=int, default=8080, help='Web 服务端口 (默认 8080)')
    parser.add_argument('--tcp-port',  type=int, default=20226, help='TRG TCP 端口 (默认 20226)')
    parser.add_argument('--ip',        default=None, help='TCP 监听 IP (不指定则交互选择)')
    parser.add_argument('--type',      default='0x00', help='标签类型过滤 (默认 0x00=T型)')
    parser.add_argument('--rule',      nargs=3, action='append', default=[],
                        metavar=('NAME', 'START', 'END'), help='添加 ID 区间过滤')
    parser.add_argument('--no-browser', action='store_true', help='不自动打开浏览器')

    args = parser.parse_args()

    # ---- IP 选择 ----
    tcp_host = select_ip(args.ip)

    # ---- 过滤器 ----
    _global_rule_filter = RuleFilter()
    if args.type:
        type_set = set()
        for t in args.type.split(','):
            t = t.strip()
            type_set.add(int(t, 16) if t.lower().startswith('0x') else int(t))
        _global_rule_filter.type_filter = type_set
        names = [f"0x{tt:02X}" for tt in sorted(type_set)]
        print(f"[类型] 仅显示: {', '.join(names)}")

    for name, start, end in args.rule:
        _global_rule_filter.add_rule(name, int(start), int(end))

    # ---- TCP 服务器 (后台线程) ----
    _global_server = WebTRGServer(
        host=tcp_host,
        port=args.tcp_port,
        rule_filter=_global_rule_filter,
    )
    tcp_thread = threading.Thread(target=_global_server.start, daemon=True)
    tcp_thread.start()

    # ---- HTTP 服务器 (主线程) ----
    httpd = HTTPServer(('0.0.0.0', args.http_port), SSERequestHandler)
    print(f"[Web] 仪表盘 → http://localhost:{args.http_port}")
    print(f"[Web] 局域网 → http://{tcp_host if tcp_host != '0.0.0.0' else (list_ips() or ['localhost'])[0]}:{args.http_port}")
    print(f"[Web] 按 Ctrl+C 停止\n")

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[Web] 停止...")
    finally:
        _global_server.stop()
        httpd.shutdown()


if __name__ == '__main__':
    main()
