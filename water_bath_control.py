#!/usr/bin/env python3
"""
Lichen (力辰) Thermostatic Water Bath - Modbus RTU Controller
=============================================================
Controls a Lichen-brand thermostatic water bath via Modbus RTU over USB/RS-485.

Communication: 9600-8N1, Modbus address 1 (CH340 USB-serial adapter)

Registers (mapped through reverse engineering):
  0x0100  PV (Process Value) - Current actual temperature, read-only
          Unit: 0.01°C (read value / 100 = °C, e.g. 499 = 4.99°C)
  0x010A  SV (Set Value) - Target/setpoint temperature, writable
          Unit: 0.1°C (write value = temp * 10, e.g. 50 = 5.0°C)
  0x0105  Heating output status (read-only)

Usage:
  python water_bath_control.py              # Read current status
  python water_bath_control.py 25.0         # Set target to 25.0°C
  python water_bath_control.py --monitor    # Continuous monitoring mode

Dependencies:
  pip install pyserial
"""
import serial
import time
import sys
import argparse

PORT = 'COM3'
ADDR = 1
REG_PV = 0x0100   # 当前温度 PV (×0.01°C, 只读)
REG_SV = 0x010A   # 设定温度 SV (×0.1°C, 可写)
REG_CTRL = 0x0105  # 加热输出/状态

def crc16(data):
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return bytes([crc & 0xFF, (crc >> 8) & 0xFF])

class WaterBath:
    def __init__(self, port=PORT, addr=ADDR):
        self.ser = serial.Serial(port, 9600, timeout=1)
        self.addr = addr

    def read_reg(self, reg, count=1):
        req = bytes([self.addr, 0x03, (reg >> 8) & 0xFF, reg & 0xFF,
                     (count >> 8) & 0xFF, count & 0xFF])
        req += crc16(req)
        self.ser.reset_input_buffer()
        self.ser.write(req)
        time.sleep(0.15)
        resp = self.ser.read(64)
        if resp and len(resp) >= 5 and resp[1] == 0x03:
            values = []
            for i in range(0, resp[2], 2):
                values.append((resp[3+i] << 8) | resp[4+i])
            return values[0] if count == 1 else values
        return None

    def write_reg(self, reg, value):
        req = bytes([self.addr, 0x06, (reg >> 8) & 0xFF, reg & 0xFF,
                     (value >> 8) & 0xFF, value & 0xFF])
        req += crc16(req)
        self.ser.reset_input_buffer()
        self.ser.write(req)
        time.sleep(0.2)
        resp = self.ser.read(32)
        return resp and resp[1] == 0x06

    def get_temperature(self):
        """读取当前实际温度 PV (°C)，精度 0.01°C"""
        v = self.read_reg(REG_PV)
        return v / 100.0 if v is not None else None

    def get_setpoint(self):
        """读取设定温度 SV (°C)"""
        v = self.read_reg(REG_SV)
        return v / 10.0 if v is not None else None

    def get_status(self):
        """读取加热输出"""
        v = self.read_reg(REG_CTRL)
        return v

    def set_temperature(self, temp_c):
        """设置目标温度 (°C)，精度 0.1°C"""
        value = int(temp_c * 10)
        value = max(0, min(value, 1000))
        return self.write_reg(REG_SV, value)

    def close(self):
        self.ser.close()

# ============================================================
if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='力辰水浴箱控制')
    parser.add_argument('temp', nargs='?', type=float, default=None,
                        help='设定温度 (°C)，不指定则读取当前设定')
    parser.add_argument('--monitor', action='store_true',
                        help='持续监控模式')
    args = parser.parse_args()

    wb = WaterBath()

    if args.monitor:
        print("水浴箱持续监控 (Ctrl+C 退出)")
        print(f"{'时间':<12} {'当前(°C)':<12} {'设定(°C)':<12} {'加热(%)':<10}")
        print("-" * 50)
        try:
            while True:
                pv = wb.get_temperature()
                sv = wb.get_setpoint()
                pwr = wb.get_status()
                t = time.strftime('%H:%M:%S')
                pv_str = f"{pv:.2f}" if pv is not None else "?"
                sv_str = f"{sv:.1f}" if sv is not None else "?"
                pwr_str = f"{pwr}" if pwr is not None else "?"
                print(f"\r{t:<12} {pv_str:<12} {sv_str:<12} {pwr_str:<10}", end='')
                sys.stdout.flush()
                time.sleep(2)
        except KeyboardInterrupt:
            print("\n停止监控")
    elif args.temp is not None:
        print(f"当前温度: {wb.get_temperature():.2f}°C")
        print(f"当前设定: {wb.get_setpoint()}°C")
        print(f"设置目标温度: {args.temp}°C ...")
        ok = wb.set_temperature(args.temp)
        if ok:
            time.sleep(0.5)
            new_sv = wb.get_setpoint()
            print(f"设定成功! 新设定: {new_sv}°C")
        else:
            print("设定失败!")
    else:
        pv = wb.get_temperature()
        sv = wb.get_setpoint()
        pwr = wb.get_status()
        print(f"水浴箱当前状态:")
        print(f"  当前温度: {pv:.2f}°C")
        print(f"  设定温度: {sv}°C")
        print(f"  加热输出: {pwr}")

    wb.close()
