# TTAG 无线温度标签 — 标定与监控工具链

TRG 无线温度标签（TTAG）全流程工具：基站监听 → 水浴控制 → 自动标定 → 多项式拟合 → 系数导出。

## 硬件

| 组件 | 型号 / 规格 |
|------|-------------|
| 温度标签 | TTAG 无线标签（NCP18XH103D03RB Murata 10KΩ NTC, B=3380K） |
| 基站 | TRG 无线基站（55-AA 协议, TCP Client → 连接 PC） |
| 水浴箱 | 力辰恒温水浴（Modbus RTU, 9600-8N1, 地址 1） |
| 分压电阻 | 6.2 KΩ 固定 |
| ADC 分辨率 | 10-bit（0–1023） |

## 快速开始（新电脑首次使用）

```bash
git clone https://github.com/Steven-washingmac/memor.git
cd memor
pip install -r requirements.txt
```

## 项目文件

```
memor/
├── ttag_monitor.py         # 基站监听 — 实时显示标签数据
├── ttag_calibration.py     # 自动标定 — 水浴控制 + ADC采集 + Excel输出
├── run_cal.py              # 一键启动器 — 自动检测续跑，预置稳定参数
├── water_bath_control.py   # 水浴控制 — Modbus RTU 读写（独立工具）
├── ttag_fitting.py         # 自动拟合 — 标定完成后自动执行，6/7 阶多项式
├── ttag_web.py             # Web 仪表盘 — 浏览器实时监控
├── ttag_fitting.m          # MATLAB 拟合（备用手动方案）
├── ntc_fitting.m           # MATLAB NTC 查表拟合
├── ntc_fit_6.m             # MATLAB NTC 6 阶拟合
├── ntcb.csv                # NTC 电阻-温度查表数据
├── requirements.txt        # Python 依赖清单
└── README.md
```

---

## 使用流程

### 第一步：验证硬件连接

```bash
# 1. 测试水浴箱（COM3 是默认串口，不是 COM3 的话加 --water-bath-port COM5）
python water_bath_control.py                # 读取当前温度
python water_bath_control.py 25.0           # 设定 25°C
python water_bath_control.py --monitor      # 持续监控（Ctrl+C 停止）

# 2. 测试基站数据
python ttag_monitor.py --device 230030                    # 服务端模式（基站主动连接 PC）
python ttag_monitor.py --connect 192.168.3.188:20226      # 客户端模式（PC 主动连接基站）
```

> **基站连接说明**：基站是 **TCP 客户端**，默认会主动连接 PC。因此 PC 端一般使用**服务端模式**（不加 `--connect`），监听 `0.0.0.0:20226` 等待基站连接即可。

### 第二步：运行标定

```bash
# === 推荐：先小范围测试（5°C → 8°C，约 16 个点，几分钟） ===
python ttag_calibration.py --device 230030 --start 5 --end 8 --step 0.2

# === 全量标定（5°C → 50°C，约 226 个点） ===
python ttag_calibration.py --device 230030 --start 5 --end 50 --step 0.2
```

**标定过程**：程序自动执行以下循环（每个温度点）：
1. 通过 Modbus 设定水浴目标温度
2. 等待水浴实际温度稳定（±0.1°C 以内）
3. 采集基站发来的标签 ADC 数据
4. 等待 ADC 稳定（滑动窗口内峰峰值 ≤ 2）
5. 记录数据 → 写入 Excel + CSV
6. 进入下一个温度点

**标定完成后**自动执行多项式拟合（6 阶、7 阶），生成拟合曲线图和系数文件。

### 第三步：查看结果

标定跑完后，程序自动输出以下文件：

| 文件 | 说明 |
|------|------|
| `cal_230030_0722_1530.xlsx` | 标定原始数据（每 5 个点存一次） |
| `cal_230030_0722_1530.csv` | 逐点后备数据（每个点立即写入，防崩溃） |
| `cal_230030_0722_1530_fit.png` | 拟合曲线对比图 + 误差分布 |
| `cal_230030_0722_1530_coeffs.txt` | 多项式系数存档 |

文件名格式：`cal_{设备ID}_{月日}_{时分}.xlsx`，每次运行生成新文件，不会覆盖历史数据。

---

## 参数速查

### `ttag_calibration.py` — 全部参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--device` | 必填 | 标签 ID |
| `--start` / `--end` | 5.0 / 50.0 | 温度范围 (°C) |
| `--step` | 0.2 | 温度步长 (°C) |
| `--bath-tolerance` | 0.1 | 水浴稳定容差 (°C) |
| `--stability-samples` | 10 | ADC 稳定所需样本数（仅统计新帧） |
| `--stability-threshold` | 2 | ADC 峰峰值阈值 |
| `--water-bath-port` | COM3 | 水浴串口 |
| `--ttag-port` | 20226 | 基站 TCP 端口 |
| `--connect IP:PORT` | — | 客户端模式（PC 主动连基站） |
| `--output` | 自动生成 | 指定输出文件名 |
| `--resume FILE` | — | 从已有文件断点续跑 |
| `--no-ttag` | — | 跳过 TTAG（仅测试水浴） |
| `--dry-run` | — | 预览标定计划，不实际执行 |

### `ttag_monitor.py` — 常用参数

| 参数 | 说明 |
|------|------|
| `--device ID` | 只显示指定标签 |
| `--type 0x00,0x01` | 过滤标签类型 |
| `--connect IP:PORT` | 客户端模式 |
| `--export file.csv` | 导出数据到 CSV |
| `--debug` | 显示所有帧（含解析失败） |

### `water_bath_control.py` — 用法

```bash
python water_bath_control.py            # 查看当前状态
python water_bath_control.py 30.0       # 设定 30°C
python water_bath_control.py --monitor  # 持续监控
```

---

## 断点续跑（`--resume`）

如果标定程序意外中断（如断电、Modbus 超时、Ctrl+C），可以接着上次的进度继续，无需从头开始：

```bash
# 从 Excel 或 CSV 文件续跑
python ttag_calibration.py --resume cal_230030_0722_1530.xlsx --end 50
python ttag_calibration.py --resume cal_230030_0722_1530.csv --end 50
```

程序会自动：
- 从文件中解析设备 ID、已完成温度点、步长
- 跳过已完成的点，从下一个温度继续
- 生成**带新时间戳**的输出文件，不覆盖原文件
- 仪表盘标题显示 `[续跑]` 标记，进度条计入已完成的点数

> **CSV 后备文件**的作用就在于此——即使意外崩溃导致 Excel 还没写出，CSV 也是逐点实时写入的，最多丢 1 个点。

---

## 自动拟合

标定程序完成后**自动调用** `ttag_fitting.py`，无需手动操作。

也可以对任意标定 Excel 单独运行：

```bash
python ttag_fitting.py cal_230030_0722_1530.xlsx
```

拟合逻辑：
1. 读取 Excel 中的 ADC 均值 和 水浴实际温度
2. 过滤异常点（ADC ≤ 0 或 ADC ≥ 1024）
3. ADC 归一化到 [-1, 1]
4. 分别用 6 阶和 7 阶多项式拟合
5. 选出误差更优的方案（要求 maxErr < 0.1°C）
6. 输出拟合曲线图 + 系数文件 + 可直接粘贴的 Python 温度转换函数

---

## 工作流程图

```
1. [Modbus]    设定水浴目标温度 → SV 寄存器 (0x010A)
2. [Modbus]    轮询 PV 寄存器 (0x0100) 直到 |PV - Target| ≤ 容差
3. [TCP 55-AA] 从基站采集标签 ADC 数据
4. [Stability] 滑动窗口验证 ADC 稳定 (峰峰值 ≤ 阈值)
5. [Record]    记录 (目标温度, 实际温度, ADC均值) → Excel + CSV
6. [Repeat]    步进到下一个温度点
7. [Auto-Fit]  标定结束 → 自动 6/7 阶多项式拟合 (maxErr < 0.1°C)
8. [Output]    输出拟合图 (*_fit.png) + 系数文件 (*_coeffs.txt)
```

---

## 协议参考

### 55-AA 帧结构

```
55 AA | Length | StationID | FuncCode | SN | TagCount | TagBlocks × N | Checksum
 2B   | 2B LE  |   2B LE   |    1B    | 1B |    1B    |    9B × N     |    1B
```

### 标签块（每标签 9 字节）

| 偏移 | 大小 | 字段 |
|------|------|------|
| +0 | 1B | RSSI（信号强度） |
| +1 | 1B | 标签类型 |
| +2 | 3B | 标签 ID（小端序, 低字节在前） |
| +5 | 2B | ADC 值（小端序） |
| +7 | 2B | 保留 |

- ADC = `0xFFFF` (65535) 表示低电量
- 标签 ID 示例：字节 `8E 82 03` = 0x03828E = 230030

### 水浴箱 Modbus 寄存器

| 寄存器 | 读写 | 缩放 | 说明 |
|--------|------|------|------|
| `0x0100` | 读 | ÷100 | PV — 当前实际温度（如 499 = 4.99°C） |
| `0x010A` | 写 | ×10 | SV — 目标设定温度（如 50 = 5.0°C） |
| `0x0105` | 读 | raw | 加热输出百分比 |

---

## 常见问题

**Q: 基站连不上？**
- 确认基站已上电，且连接到与 PC 同网段（`192.168.3.x`）
- 默认使用服务端模式（PC 监听 20226 端口，基站主动连接）
- 如果基站是服务端模式，改用 `--connect 基站IP:20226`

**Q: 水浴箱没有响应？**
- 确认串口号：`--water-bath-port COM5`（在设备管理器查看）
- 确认水浴箱 Modbus 地址为 1，波特率 9600-8N1

**Q: ADC 一直是 0 或 None？**
- 确认基站已连接（仪表盘显示 `[LINK]`）
- 确认标签设备 ID 正确
- 标签是否在基站接收范围内

**Q: 程序意外退出了怎么办？**
- CSV 文件已保存所有完成的数据点
- 用 `--resume` 继续：`python ttag_calibration.py --resume cal_xxx.csv --end 50`
