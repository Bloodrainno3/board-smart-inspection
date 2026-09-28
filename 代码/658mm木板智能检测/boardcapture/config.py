"""Validated, BOM-tolerant configuration; never silently switches to simulation."""
from __future__ import annotations
import copy
import json
import os
from pathlib import Path
import sys
import threading
import time
import uuid

ROOT = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[1]
ASSETS = Path(getattr(sys, "_MEIPASS", ROOT))
MAPPING = {
    "emergency_ok": ("X000", "discrete_input", 13312),
    "front_sensor": ("X001", "discrete_input", 13313),
    "rear_sensor": ("X002", "discrete_input", 13314),
    "capture_active": ("M020", "coil", 20),
    "rear_sensor_seen": ("M021", "coil", 21),
    "conveyor_running": ("M010", "coil", 10),
    "conveyor_start_command": ("M300", "coil", 300),
    "conveyor_stop_command": ("M400", "coil", 400),
}

def defaults():
    from .processing_config import defaults as processing_defaults
    return {
        "processing": processing_defaults(ROOT),
        "version": 3,
        "trigger": {"mode": "plc_m020", "debounce_ms": 20},
        "control": {"confirm_run_on_arm": True, "command_pulse_ms": 300, "response_timeout_s": 2.0},
        "serial": {"port": "COM7", "baudrate": 19200, "bytesize": 8, "parity": "O",
                   "stopbits": 1, "slave_id": 1, "timeout_s": 0.3, "poll_interval_ms": 40},
        "devices": {key: {"symbol": sym, "modbus_area": area, "address": addr, "active_high": True}
                    for key, (sym, area, addr) in MAPPING.items()},
        "camera": {"serial_number": "", "expected_width": 0, "chunk_lines": 64,
                   "grabber_config_file": "",
                   "queue_mb": 512, "max_capture_bytes": 1500000000, "pixel_order": "RGB",
                   "post_trigger_ms": 106, "max_capture_seconds": 120,
                   "no_frame_timeout_s": 10, "minimum_height": 4000, "maximum_height": 100004},
        "output": {"directory": str(ROOT / "images"), "format": "JPG", "jpeg_quality": 95,
                   "numbering": "ascending", "next_number": 1, "end_number": None,
                   "minimum_free_gb": 5, "archive_directory": ""},
        "vision": {"enabled": False, "manual_window_v1": True, "model_path": str(ASSETS / "models" / "best.onnx"),
                   "results_directory": str(ROOT / "results"), "threshold": .5, "max_seams": 2,
                   "min_row_coverage": .08, "threads": 2, "material": "混合板材", "calibration": None},
    }

def merge(base, incoming):
    for k, v in incoming.items():
        if k in base and isinstance(base[k], dict) and isinstance(v, dict):
            merge(base[k], v)
        else:
            base[k] = copy.deepcopy(v)
    return base

def integer(value, label, lo, hi):
    if isinstance(value, bool) or not isinstance(value, int) or not lo <= value <= hi:
        raise ValueError(f"{label}必须是 {lo}～{hi} 的整数")

def validate(c):
    from .processing_config import validate as validate_processing
    if "processing" in c:validate_processing(c["processing"],c["output"]["directory"])
    trigger = c.get('trigger', {'mode':'plc_m020','debounce_ms':20})
    if trigger['mode'] not in ('photoelectric','plc_m020'):
        raise ValueError('触发方式必须为X1/X2光电或PLC M020')
    integer(trigger['debounce_ms'], '光电消抖毫秒', 0, 500)
    control=c['control']
    if not isinstance(control['confirm_run_on_arm'],bool): raise ValueError('PLC运行确认必须为true或false')
    integer(control['command_pulse_ms'],'PLC确认脉冲毫秒',100,1000)
    if not .5 <= control['response_timeout_s'] <= 10: raise ValueError('PLC运行确认超时超出范围')
    s = c["serial"]
    integer(s["slave_id"], "PLC站号", 1, 247)
    integer(s["baudrate"], "波特率", 1200, 115200)
    if s["parity"] not in ("N", "E", "O") or s["bytesize"] != 8 or s["stopbits"] not in (1, 2):
        raise ValueError("串口必须使用8位数据、N/E/O校验和1或2停止位")
    if not 0.05 <= s["timeout_s"] <= 5 or not 10 <= s["poll_interval_ms"] <= 2000:
        raise ValueError("串口超时或轮询间隔超出范围")
    used = set()
    for key in MAPPING:
        d = c["devices"][key]
        integer(d["address"], d["symbol"] + "地址", 0, 65535)
        if d["modbus_area"] not in ("discrete_input", "coil"):
            raise ValueError(f"{key}的Modbus区域不正确")
        pair = (d["modbus_area"], d["address"])
        if pair in used:
            raise ValueError("PLC信号不能映射到同一个地址")
        used.add(pair)
        if not isinstance(d["active_high"], bool):
            raise ValueError("信号极性必须为true或false")
        if key in ('conveyor_start_command','conveyor_stop_command') and (d['modbus_area']!='coil' or not d['active_high']):
            raise ValueError('M300/M400命令必须使用线圈区域、高电平有效')
    out, cam = c["output"], c["camera"]
    if out["numbering"] not in ("ascending", "descending", "timestamp"):
        raise ValueError("命名方式不正确")
    integer(out["next_number"], "下一编号", -999999999999999999, 999999999999999999)
    if out["end_number"] is not None:
        integer(out["end_number"], "终止编号", -999999999999999999, 999999999999999999)
    if out["format"] not in ("JPG", "PNG", "BMP", "TIFF"):
        raise ValueError("不支持的图像格式")
    integer(out["jpeg_quality"], "JPG质量", 1, 100)
    if not str(out["directory"]).strip():
        raise ValueError("必须设置图片保存目录")
    if not 0 <= out["minimum_free_gb"] <= 1000:
        raise ValueError("磁盘保留空间必须在0～1000 GB")
    integer(cam["chunk_lines"], "分块行数", 1, 4096)
    if not isinstance(cam.get("grabber_config_file", ""), str):
        raise ValueError("IKap配置文件路径必须为文本")
    if cam["serial_number"] and (not str(cam["serial_number"]).isdigit()):
        raise ValueError("采集卡索引填写0、1等数字；只有一个设备时可留空")
    integer(cam["expected_width"], "预期宽度", 0, 65535)
    integer(cam["queue_mb"], "缓存MB", 16, 2048)
    integer(cam["max_capture_bytes"], "单板字节上限", 1024, 2000000000)
    integer(cam["post_trigger_ms"], "尾部续采毫秒", 0, 5000)
    integer(cam["minimum_height"], "最小有效行数", 1, 1000000)
    integer(cam["maximum_height"], "最大有效行数", cam["minimum_height"], 1000000)
    if not 1 <= cam["max_capture_seconds"] <= 3600 or not 1 <= cam["no_frame_timeout_s"] <= 300:
        raise ValueError("采集/无图像超时超出范围")
    if cam["pixel_order"] not in ("BGR", "RGB"):
        raise ValueError("颜色顺序必须为BGR或RGB")
    v = c.get('vision')
    if v:
        integer(v['threads'], '识别CPU线程', 1, 8)
        integer(v['max_seams'], '每板最多缝数', 1, 20)
        if not .01 <= v['threshold'] <= .99 or not .001 <= v['min_row_coverage'] <= 1:
            raise ValueError('识别阈值或行覆盖率超出范围')
        if not str(v['results_directory']).strip(): raise ValueError('识别结果目录不能为空')
        original = Path(out['directory']).resolve()
        results = Path(v['results_directory']).resolve()
        if results == original or original in results.parents:
            raise ValueError('识别结果目录应在原图目录之外，保持JPG目录仅含原图')

def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with tmp.open("x", encoding="utf-8", newline="\n") as f:
            json.dump(value, f, ensure_ascii=False, indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        deadline = time.monotonic() + 1.0
        while True:
            try:
                os.replace(tmp, path)
                break
            except PermissionError as error:
                # Windows readers briefly hold files without FILE_SHARE_DELETE.
                # Retain the old complete JSON until the atomic replacement succeeds.
                if os.name != 'nt' or getattr(error, 'winerror', None) not in (5, 32, 33) or time.monotonic() >= deadline:
                    raise
                time.sleep(.01)
    finally:
        tmp.unlink(missing_ok=True)

class ConfigStore:
    def __init__(self, path=None):
        self.path = Path(path or ROOT / "config" / "settings.json")
        self.lock = threading.RLock()
        self.notices = []
        self.data = defaults()
        if self.path.exists():
            try:
                incoming = json.loads(self.path.read_text(encoding="utf-8-sig"))
                if not isinstance(incoming, dict):
                    raise ValueError("配置根节点应为对象")
                self.data = merge(self.data, incoming)
                migrate_manual = not incoming.get('vision', {}).get('manual_window_v1', False)
                if migrate_manual:
                    self.data['vision']['enabled'] = False
                    self.data['vision']['manual_window_v1'] = True
                    self.notices.append('已切换为默认只采图；板缝识别在独立窗口手动启动，可另行开启自动识别。')
                # Repair only known missing/null signals; keep explicit custom addresses.
                for key, (sym, area, addr) in MAPPING.items():
                    if self.data["devices"][key].get("address") is None:
                        self.data["devices"][key]["address"] = addr
                        self.notices.append(f"已修复空映射：{sym}={addr}")
                validate(self.data)
                if migrate_manual:
                    self.save(self.data)
            except Exception as e:
                raise ValueError(f"配置读取失败：{self.path}\n{e}\n原文件已保留，请修正后重启。") from e
        else:
            self.save(self.data)

    def snapshot(self):
        with self.lock:
            return copy.deepcopy(self.data)

    def save(self, value):
        validate(value)
        with self.lock:
            atomic_json(self.path, value)
            self.data = copy.deepcopy(value)

    def save_vision(self, value):
        # Do not overwrite the concurrently advancing image number.
        with self.lock:
            cfg = copy.deepcopy(self.data)
            cfg['vision'] = copy.deepcopy(value)
            self.save(cfg)
