"""Camera-side CLConfigurator control. Never writes settings on connection."""
from __future__ import annotations
import ctypes as C
from datetime import datetime
from enum import IntEnum
import math
import os
from pathlib import Path
import re
import time
import uuid
from .config import ASSETS, atomic_json


class Property(IntEnum):
    LightValueScale = 257
    LightValueR = 258
    LightValueG = 259
    LightValueB = 260
    PixelFormat = 268
    LineRate = 269
    Offset = 270
    Gain = 271
    TriggerMode = 272
    TestPattern = 273
    MirrorEnabled = 274
    BinarizationThreshold = 275
    UserSetDefault = 276
    UserSetLoad = 277
    UserSetStore = 278
    FFC_UserSet = 279
    FFC_Generate = 280
    FFC_Algorithm = 281
    FFC_Enabled = 282


LIGHTS = ('LightValueR', 'LightValueG', 'LightValueB')
RANGES = {**{k: (0, 10000) for k in LIGHTS}, 'Offset': (0, 255), 'Gain': (1, 20),
          'FFC_Algorithm': (0, 255), 'FFC_Enabled': (0, 1)}
LABELS = dict(zip(LIGHTS, ('红光 R', '绿光 G', '蓝光 B')),
              Gain='响应增益', Offset='响应偏移', FFC_Enabled='平场校正启用',
              FFC_Algorithm='校正算法', FFC_UserSet='当前校正集',
              UserSetDefault='默认用户配置集', PixelFormat='相机像素格式（原始值）',
              LineRate='行频 Hz', TriggerMode='触发方式（0自触发/1外触发）',
              TestPattern='测试图样', MirrorEnabled='镜像', BinarizationThreshold='二值化阈值',
              LightValueScale='旧版灯光量程标志')


def decode(raw):
    for encoding in ('utf-8', 'gb18030'):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            pass
    return raw.decode('utf-8', errors='replace')


class Port(C.Structure):
    _fields_ = [('vendor', C.c_char * 128), ('identifier', C.c_char * 128)]


class Value(C.Structure):
    _fields_ = [('type', C.c_uint), ('extra', C.c_int), ('value', C.c_double)]


class SDK:
    def __init__(self, driver=None):
        if os.name != 'nt' or C.sizeof(C.c_void_p) != 8:
            raise RuntimeError('CLConfigurator SDK需要64位Windows')
        driver = Path(driver or ASSETS / 'camera_driver')
        self.dll_path = os.add_dll_directory(str(driver))
        self.handle = None
        try:
            self.dll = C.CDLL(str(driver / 'cl_config_bridge.dll'))
        except OSError as e:
            self.dll_path.close()
            raise RuntimeError('CLConfigurator SDK加载失败，请完整解压新版658软件并安装64位VC++运行库：' + str(e)) from e
        signatures = {
            'cc_abi': ([], C.c_int),
            'cc_error': ([C.c_void_p, C.c_uint], None),
            'cc_port': ([C.c_uint, C.POINTER(Port)], C.c_int),
            'cc_open': ([C.POINTER(Port)], C.c_void_p),
            'cc_close': ([C.c_void_p], None),
            'cc_read': ([C.c_void_p, C.c_void_p, C.c_uint, C.POINTER(Value), C.c_uint, C.POINTER(C.c_uint)], C.c_int),
            'cc_set': ([C.c_void_p, C.c_uint, C.c_double], C.c_int),
        }
        for name, (args, result) in signatures.items():
            fn = getattr(self.dll, name)
            fn.argtypes, fn.restype = args, result
        if self.dll.cc_abi() != 1:
            raise RuntimeError('相机校正桥接DLL版本不匹配')

    def error(self):
        text = C.create_string_buffer(4096)
        self.dll.cc_error(text, len(text))
        return decode(text.value)

    def ports(self):
        result = []
        for i in range(128):
            port = Port()
            status = self.dll.cc_port(i, C.byref(port))
            if status < 0:
                raise RuntimeError(self.error())
            if not status:
                break
            # Preserve original byte strings for the vendor's char* API.
            result.append({'vendor': decode(port.vendor), 'identifier': decode(port.identifier),
                           'vendor_hex': port.vendor.hex(), 'identifier_hex': port.identifier.hex()})
        return result

    def connect(self, port):
        self.close()
        desc = Port(bytes.fromhex(port['vendor_hex']), bytes.fromhex(port['identifier_hex']))
        self.handle = self.dll.cc_open(C.byref(desc))
        if not self.handle:
            raise RuntimeError(self.error())

    def read(self):
        raw = C.create_string_buffer(1024 * 1024)
        values = (Value * 512)()
        count = C.c_uint()
        if not self.dll.cc_read(self.handle, raw, len(raw), values, len(values), C.byref(count)):
            raise RuntimeError(self.error())
        properties = []
        for v in values[:count.value]:
            try:
                name = Property(v.type).name
            except ValueError:
                name = f'Property_{v.type}'
            value = float(v.value) if name == 'Gain' else int(v.value)
            properties.append(dict(name=name, value=value, extra=v.extra, type=v.type))
        return {'raw': decode(raw.value), 'properties': properties,
                'values': {p['name']: p['value'] for p in properties if p['extra'] == 0}}

    def set(self, name, value):
        if not self.dll.cc_set(self.handle, int(Property[name]), float(value)):
            raise RuntimeError(self.error())

    def close(self):
        if self.handle:
            self.dll.cc_close(self.handle)
            self.handle = None


def com_number(text):
    match = re.search(r'(?i)(?:^|[^A-Z0-9])COM0*(\d+)(?:$|[^0-9])', str(text))
    return int(match.group(1)) if match else None


class Control:
    def __init__(self, emit, data_root, sdk_factory=SDK):
        self.emit, self.data_root, self.sdk_factory = emit, Path(data_root), sdk_factory
        self.sdk = None
        self.port = None
        self.snapshot = None
        self.saved_lights = None
        self.last_read_time = 0.0

    @property
    def connected(self):
        return self.port is not None

    def _sdk(self):
        if self.sdk is None:
            self.sdk = self.sdk_factory()
        return self.sdk

    def enumerate(self):
        ports = self._sdk().ports()
        self.emit('cl_ports', ports)
        return ports

    def connect(self, port, plc_port):
        if not isinstance(port, dict) or port not in self._sdk().ports():
            raise ValueError('控制端口已变化，请重新搜索并选择相机控制端口')
        if com_number(port['identifier']) is not None and com_number(port['identifier']) == com_number(plc_port):
            raise ValueError('这个COM口已配置为PLC通讯口，不能作为相机校正端口')
        self.close()
        self.sdk.connect(port)
        self.port = port
        try:
            return self.read()
        except Exception:
            self.close()
            raise

    def close(self):
        if self.sdk:
            self.sdk.close()
        self.port = self.snapshot = self.saved_lights = None
        self.last_read_time = 0.0
        self.emit('cl_connection', {'connected': False})

    def read(self):
        if not self.connected:
            raise RuntimeError('请先连接相机控制端口')
        # Old readbacks must not remain labelled current after a failed refresh.
        self.last_read_time = 0.0
        try:
            snapshot = self.sdk.read()
            if not any(k in snapshot['values'] for k in (*LIGHTS, 'FFC_Enabled')):
                raise RuntimeError('端口没有返回可识别的相机灯光或校正参数，请核对端口')
        except Exception:
            self.emit('cl_stale', None)
            raise
        snapshot.update(port=self.port, read_at=datetime.now().astimezone().isoformat(timespec='seconds'))
        self.snapshot = snapshot
        self.last_read_time = time.monotonic()
        self.emit('cl_snapshot', snapshot)
        return snapshot

    def apply(self, changes, trace=None):
        if not changes or any(k not in RANGES for k in changes):
            raise ValueError('不支持的相机参数')
        for key, value in changes.items():
            lo, hi = RANGES[key]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not lo <= value <= hi:
                raise ValueError(f'{LABELS.get(key,key)}超出范围 {lo}～{hi}')
            if key != 'Gain' and value != int(value):
                raise ValueError(f'{key}必须为整数')
        current = self.read()['values']
        missing = set(changes) - current.keys()
        if missing:
            raise RuntimeError('相机未回读这些参数，未执行写入：' + '、'.join(sorted(missing)))
        # Legacy firmware may advertise the old percentage range.
        light_max = 100 if current.get('LightValueScale') == 100 else 10000
        if any(v > light_max for k, v in changes.items() if k in LIGHTS):
            raise ValueError(f'相机回读的灯光量程为0～{light_max}')
        for key, value in changes.items():
            self.sdk.set(key, value)
            if trace is not None:
                trace.append({'property': key, 'requested': value, 'sdk_ack': True})
        actual = self.read()['values']
        bad = [k for k, v in changes.items() if k not in actual or not math.isclose(float(actual[k]), v, abs_tol=1e-4)]
        if bad:
            raise RuntimeError('SDK已应答，但回读未匹配：' + '、'.join(bad) + '。以相机回读值为准')
        return '已写入并回读确认；尚未保存为上电默认配置'

    def perform(self, action, args):
        trace = []
        report = {'operation': action, 'requested': args, 'before': self.snapshot, 'sdk_commands': trace,
                  'started_at': datetime.now().astimezone().isoformat(), 'ok': False}
        try:
            if action == 'apply':
                message = self.apply(args['changes'], trace)
            elif action == 'dark_lights':
                v = self.read()['values']
                if any(k not in v for k in LIGHTS):
                    raise RuntimeError('相机没有回读完整RGB灯光值，不能关闭并恢复灯光')
                if self.saved_lights is None:
                    self.saved_lights = {k: v[k] for k in LIGHTS}
                message = self.apply({k: 0 for k in LIGHTS}, trace) + '；已记住关灯前RGB值'
            elif action == 'restore_lights':
                if self.saved_lights is None:
                    raise RuntimeError('尚未记录关灯前RGB值')
                message = self.apply(self.saved_lights, trace)
                self.saved_lights = None
            elif action == 'generate':
                kind = args.get('kind')
                if type(kind) is not int or kind not in (1, 2):
                    raise ValueError('校正类型只能是暗场或亮场')
                v = self.read()['values']
                if v.get('FFC_Enabled') != 0:
                    raise RuntimeError('生成校正前，请先停用平场校正并回读确认')
                if kind == 2 and any(v.get(k) != 0 for k in LIGHTS):
                    raise RuntimeError('暗场校正前，请先关闭RGB灯光并遮挡外界光')
                if kind == 1 and (any(k not in v for k in LIGHTS) or not any(v[k] > 0 for k in LIGHTS)):
                    raise RuntimeError('亮场校正前，请先恢复RGB灯光并放置均匀白色目标')
                self.sdk.set('FFC_Generate', kind)
                trace.append({'property': 'FFC_Generate', 'requested': kind, 'sdk_ack': True})
                message = ('暗场' if kind == 2 else '亮场') + '校正命令已应答；SDK未提供完成标志，请保持采图并按厂家要求等待校正稳定，再执行下一步'
                self.read()
            elif action == 'save':
                slot = args.get('slot')
                if type(slot) is not int or not 0 <= slot <= 8:
                    raise ValueError('用户配置集编号必须为0～8')
                self.read()
                for key in ('UserSetStore', 'UserSetDefault'):
                    self.sdk.set(key, slot)
                    trace.append({'property': key, 'requested': slot, 'sdk_ack': True})
                actual = self.read()['values']
                if 'UserSetDefault' in actual and actual['UserSetDefault'] != slot:
                    raise RuntimeError('保存命令已应答，但默认配置集回读不一致')
                message = f'保存到用户配置集{slot}、设为默认的命令已应答；持久化仍需相机断电重启后重新读取核验'
            else:
                raise ValueError('未知校正操作')
            report.update(ok=True, message=message)
            return message
        except Exception as e:
            report['error'] = str(e)
            # A sequence can partially succeed. Always expose actual state.
            try:
                self.read()
            except Exception as read_error:
                report['read_error'] = str(read_error)
            raise
        finally:
            report['after'] = self.snapshot
            report['readback_current'] = bool(self.last_read_time)
            report['finished_at'] = datetime.now().astimezone().isoformat()
            try:
                path = self.data_root / 'camera_calibration' / (datetime.now().strftime('%Y%m%d_%H%M%S_') + uuid.uuid4().hex[:8] + '.json')
                atomic_json(path, report)
                self.emit('cl_audit', str(path))
            except Exception as e:
                self.emit('cl_notice', '校正记录写盘失败：' + str(e))

    def export(self):
        snapshot = self.read()
        path = self.data_root / 'camera_calibration' / ('相机参数_' + datetime.now().strftime('%Y%m%d_%H%M%S_%f') + '.json')
        atomic_json(path, snapshot)
        return path
