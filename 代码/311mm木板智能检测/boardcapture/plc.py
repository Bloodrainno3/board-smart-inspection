"""Modbus RTU master; writes are restricted to explicit run confirmation commands."""
import struct
import time
import serial
from .config import MAPPING

def crc16(data):
    value = 0xFFFF
    for b in data:
        value ^= b
        for _ in range(8):
            value = (value >> 1) ^ (0xA001 if value & 1 else 0)
    return value

def packet(data):
    return data + struct.pack("<H", crc16(data))

class ModbusError(IOError):
    pass

class PLC:
    def __init__(self, config, transport=None):
        self.config = config
        s = config["serial"]
        self.slave = s["slave_id"]
        self.timeout = s["timeout_s"]
        self.gap = max(0.00175, 3.5 * 11 / s["baudrate"])
        self.last_io = 0.0
        self.last_raw_status = {}
        self.port = transport or serial.Serial(port=s["port"], baudrate=s["baudrate"],
            bytesize=8, parity=s["parity"], stopbits=s["stopbits"],
            timeout=self.timeout, write_timeout=self.timeout)

    def _read_exact(self, size):
        deadline, data = time.monotonic() + self.timeout, bytearray()
        while len(data) < size and time.monotonic() < deadline:
            part = self.port.read(size - len(data))
            if not part:
                break
            data.extend(part)
        if len(data) != size:
            raise ModbusError("PLC无响应或响应不完整：检查COM口、波特率、校验、站号及A/B接线")
        return bytes(data)

    def read_bits(self, area, address, count):
        if area not in ("coil", "discrete_input") or not 1 <= count <= 2000 or not 0 <= address <= 65536-count:
            raise ValueError("非法Modbus读取范围")
        function = 1 if area == "coil" else 2
        delay = self.gap - (time.monotonic() - self.last_io)
        if delay > 0:
            time.sleep(delay)
        self.port.reset_input_buffer()
        request = packet(struct.pack(">BBHH", self.slave, function, address, count))
        try:
            if self.port.write(request) != len(request):
                raise ModbusError("串口发送不完整")
            self.port.flush()
            header = self._read_exact(3)
            if header[0] != self.slave:
                raise ModbusError("PLC响应站号不匹配")
            if header[1] == function | 0x80:
                response = header + self._read_exact(2)
                if packet(response[:-2]) != response:
                    raise ModbusError("PLC异常帧CRC校验失败")
                raise ModbusError(f"PLC返回异常码{header[2]}：功能码{function:02d}，地址{address}")
            if header[1] != function or header[2] != (count + 7) // 8:
                raise ModbusError("PLC响应功能码/数据长度不匹配")
            response = header + self._read_exact(header[2] + 2)
            if packet(response[:-2]) != response:
                raise ModbusError("PLC响应CRC校验失败，请检查485干扰、接地及串口参数")
            return [bool(response[3 + i // 8] & (1 << (i % 8))) for i in range(count)]
        finally:
            self.last_io = time.monotonic()

    def read_status(self):
        # Reading/connecting never writes. Include the ladder's run interlock.
        groups = {}
        for key in MAPPING:
            d = self.config["devices"][key]
            groups.setdefault(d["modbus_area"], []).append((key, d))
        result = {}
        raw = {}
        for area, entries in groups.items():
            entries.sort(key=lambda x: x[1]["address"])
            # Split sparse customized maps to avoid reading undocumented address gaps.
            batches = []
            for entry in entries:
                if not batches or entry[1]["address"] > batches[-1][-1][1]["address"] + 1:
                    batches.append([])
                batches[-1].append(entry)
            for batch in batches:
                start = batch[0][1]["address"]
                bits = self.read_bits(area, start, batch[-1][1]["address"] - start + 1)
                for key, d in batch:
                    raw[key] = bits[d["address"] - start]
                    result[key] = bits[d["address"] - start] == d["active_high"]
        self.last_raw_status = raw
        return result

    def write_command(self, key, on):
        if key not in ('conveyor_start_command','conveyor_stop_command') or type(on) is not bool:
            raise ValueError('仅允许发送M300/M400运行确认命令')
        d=self.config['devices'][key]
        if d['modbus_area']!='coil' or not d['active_high']:
            raise ValueError('运行确认命令映射不正确')
        delay=self.gap-(time.monotonic()-self.last_io)
        if delay>0:time.sleep(delay)
        self.port.reset_input_buffer()
        request=packet(struct.pack('>BBHH',self.slave,5,d['address'],0xff00 if on else 0))
        try:
            if self.port.write(request)!=len(request):raise ModbusError('PLC命令发送不完整')
            self.port.flush()
            header=self._read_exact(2)
            response=header+self._read_exact(3 if header[1]==0x85 else 6)
            if packet(response[:-2])!=response:raise ModbusError('PLC命令响应CRC校验失败')
            if header[0]!=self.slave:raise ModbusError('PLC命令响应站号不匹配')
            if header[1]==0x85:raise ModbusError(f'PLC拒绝运行确认命令，异常码{response[2]}')
            if response!=request:raise ModbusError('PLC命令响应地址/值不匹配')
        finally:self.last_io=time.monotonic()

    def close(self):
        self.port.close()
