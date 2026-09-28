from __future__ import annotations
import ctypes as C
import os
from pathlib import Path
import shutil
import threading
import time
import uuid
from .config import ROOT, ASSETS, atomic_json

class Info(C.Structure):
    _fields_ = [(k, C.c_uint32) for k in ("width", "height", "stride", "channels", "trigger", "multiply", "divide", "capture_signal")]

class Stats(C.Structure):
    _fields_ = [(k, C.c_uint64) for k in ("lines", "frames", "lost", "dropped", "flagged", "queued", "peak", "broken", "failed")]

def as_dict(struct):
    return {name: getattr(struct, name) for name, _ in struct._fields_}

class NativeCamera:
    def __init__(self, driver=None):
        if C.sizeof(C.c_void_p) != 8 or os.name != "nt":
            raise RuntimeError("相机SDK需要64位Windows和64位Python")
        self.handle = None
        self.dll_paths = []
        driver = Path(driver or ASSETS / "camera_driver")
        self.runtime_handles = []
        for folder in (driver,):
            if folder.is_dir():
                self.dll_paths.append(os.add_dll_directory(str(folder)))
        try:
            self.dll = C.CDLL(str(driver / "board_camera_bridge.dll"))
        except OSError as e:
            raise RuntimeError(f"IKap 1.7.3 DLL加载失败：{e}\n请完整解压658版，并安装附带的IKapLibrary 1.7.3及其64位运行库/采集卡驱动。") from e
        d = self.dll
        signatures = {
            "bc_abi": ([], C.c_int), "bc_count": ([], C.c_int),
            "bc_error": ([C.c_void_p], None),
            "bc_description": ([C.c_int, C.c_void_p, C.c_uint], C.c_int),
            "bc_open": ([C.c_int], C.c_void_p),
            "bc_prepare": ([C.c_void_p, C.c_uint, C.c_char_p, C.c_uint64, C.c_uint64, C.POINTER(Info)], C.c_int),
            "bc_start": ([C.c_void_p], C.c_int), "bc_stop": ([C.c_void_p], C.c_int),
            "bc_pop": ([C.c_void_p, C.c_void_p, C.c_uint64, C.POINTER(C.c_uint)], C.c_int),
            "bc_stats": ([C.c_void_p, C.POINTER(Stats)], None),
            "bc_working": ([C.c_void_p], C.c_int), "bc_close": ([C.c_void_p], None),
        }
        for name, (args, result) in signatures.items():
            fn = getattr(d, name)
            fn.argtypes, fn.restype = args, result
        if d.bc_abi() != 2:
            raise RuntimeError("相机桥接DLL版本不匹配，请重新完整解压软件")
        if hasattr(d,'bc_diagnostic'):
            d.bc_diagnostic.argtypes=[C.c_void_p,C.c_void_p,C.c_uint]
            d.bc_diagnostic.restype=None
        if hasattr(d, 'bc_start_preview'):
            d.bc_start_preview.argtypes = [C.c_void_p]
            d.bc_start_preview.restype = C.c_int

    def error(self):
        buf = C.create_string_buffer(256)
        self.dll.bc_error(buf)
        return buf.value.decode("utf-8", errors="replace") or "厂家SDK操作失败"

    def enumerate(self):
        count = self.dll.bc_count()
        if count < 0:
            raise RuntimeError("IKap采集卡枚举失败：" + self.error())
        found = []
        for i in range(count):
            buf = C.create_string_buffer(2048)
            if self.dll.bc_description(i, buf, len(buf)):
                found.append((i, buf.value.decode("utf-8", errors="replace")))
        return found

    def connect(self, config):
        devices = self.enumerate()
        serial = config["serial_number"].strip()
        matched = [(i, s) for i, s in devices if not serial or s.rsplit(" / ", 1)[-1] == serial]
        if len(matched) != 1:
            listing = "\n".join(s for _, s in devices)
            raise RuntimeError(f"发现{len(devices)}个IKap采集卡设备，匹配{len(matched)}个。请检查PCIe采集卡及IKap驱动，关闭IKapExpert；多个设备时填写末尾索引（0、1…）。\n{listing}\n{self.error()}")
        index, self.description = matched[0]
        self.handle = self.dll.bc_open(index)
        if not self.handle:
            raise RuntimeError(self.error())
        try:
            configured = str(config.get("grabber_config_file", "")).strip()
            if not configured:
                raise RuntimeError("请先选择厂家调通这台658相机后导出的 .vlcf 采集配置，再连接相机。")
            path = Path(configured)
            if not path.is_absolute():
                path = ROOT / path
            if path.suffix.lower() != ".vlcf" or not path.is_file():
                raise RuntimeError(f"采集配置不存在或不是 .vlcf 文件：{path}")
            # Bridge ABI is UTF-8; native bridge converts to a verified Windows
            # narrow/short filename before calling the vendor's char* API.
            calibration = str(path.resolve()).encode("utf-8")
            self.info = Info()
            ok = self.dll.bc_prepare(self.handle, config["chunk_lines"], calibration,
                config["queue_mb"] * 1024 * 1024, config["max_capture_bytes"], C.byref(self.info))
            if not ok:
                detail = self.error()
                if "Load .vlcf" in detail or "path" in detail.lower() or "filename" in detail.lower():
                    hint = "配置文件加载失败；请确认IKapExpert能打开同一文件。可将文件放到C:\\IKapConfig\\1.vlcf后重新选择。"
                elif "line scan" in detail or "image layout" in detail:
                    hint = "需使用线扫Mono8、RGB8或BGR8配置，并核对图像布局。"
                else:
                    hint = "请核对采集卡配置；以下为失败步骤和厂家错误码。"
                raise RuntimeError(f"IKap采集配置失败：{detail}\n{hint}\n原配置：{path}")
            if config["expected_width"] and self.info.width != config["expected_width"]:
                raise RuntimeError(f"相机实际宽度{self.info.width}，预期{config['expected_width']}；请核对相机及高级设置")
            self.buffer = C.create_string_buffer(self.info.stride * self.info.height)
            self.pixel_order = "RGB"  # Native adapter normalises BGR8; Mono8 is unchanged.
            self.description += f" · Camera Link · {'Mono8' if self.info.channels == 1 else 'RGB8'} · {path.name}"
        except Exception:
            self.close()
            raise

    def start(self):
        if not self.dll.bc_start(self.handle):
            raise RuntimeError("相机开始采集失败：" + self.error())

    def pop(self):
        lines = C.c_uint()
        result = self.dll.bc_pop(self.handle, self.buffer, len(self.buffer), C.byref(lines))
        if result < 0:
            raise RuntimeError("相机帧数据超出缓冲容量")
        return self.buffer.raw[:lines.value * self.info.stride] if result else None

    def start_preview(self):
        if not hasattr(self.dll, 'bc_start_preview'):
            raise RuntimeError('当前采集桥接DLL不支持校正预览，请完整解压新版658软件')
        if not self.dll.bc_start_preview(self.handle):
            raise RuntimeError('校正预览启动失败：' + self.error())

    def stats(self):
        s = Stats()
        self.dll.bc_stats(self.handle, C.byref(s))
        return as_dict(s)

    def working(self):
        return bool(self.dll.bc_working(self.handle))

    def diagnostics(self):
        if not self.handle or not hasattr(self.dll,'bc_diagnostic'):return ''
        buf=C.create_string_buffer(8192)
        self.dll.bc_diagnostic(self.handle,buf,len(buf))
        return buf.value.decode('utf-8',errors='replace')

    def stop(self):
        if not self.dll.bc_stop(self.handle):
            raise RuntimeError("相机停止采集失败：" + self.error())

    def close(self):
        if self.handle:
            self.dll.bc_close(self.handle)
            self.handle = None

class SimCamera:
    """Deterministic synthetic camera; can only be selected explicitly."""
    def connect(self, config):
        self.info = Info(320, 64, 960, 3, 0, 1, 1, 0)
        self.description = "模拟相机（不连接真实设备）"
        self.active = False
        self.counter = 0

    def start(self):
        self.active = True
        self.counter = 0
        self.next_frame = time.monotonic()

    def pop(self):
        if not self.active or time.monotonic() < self.next_frame:
            return None
        self.next_frame += 0.025
        self.counter += 1
        row = b"".join(bytes((min(255, 95+x//3), min(255, 70+x//3), min(255, 35+x//3))) for x in range(320))
        return row * 64

    def stats(self):
        return dict(lines=self.counter*64, frames=self.counter, lost=0, dropped=0, flagged=0,
                    queued=0, peak=61440, broken=0, failed=0)

    def working(self):
        return self.active

    def stop(self):
        self.active = False

    def close(self):
        self.active = False

class RawCapture:
    """Sequential disk writer; SDK callbacks never call Python or write to disk."""
    def __init__(self, camera, spool, config):
        self.camera = camera
        self.config = config
        self.id = time.strftime("%Y%m%d_%H%M%S_") + uuid.uuid4().hex[:10]
        self.path = Path(spool) / (self.id + ".raw")
        self.meta_path = self.path.with_suffix(".json")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.info = as_dict(camera.info)
        self.stop_event = threading.Event()
        self.error = ""
        self.written = 0
        self.started = self.last_frame = time.monotonic()
        self.meta = {"id": self.id, "raw_path": str(self.path), "info": self.info,
                     "pixel_order": getattr(camera, "pixel_order", config["pixel_order"]), "status": "capturing"}
        atomic_json(self.meta_path, self.meta)
        self.file = self.path.open("xb")
        self.thread = threading.Thread(target=self._write, name="RawDiskWriter", daemon=True)

    def start(self):
        self.thread.start()
        try:
            self.camera.start()
        except Exception:
            self.stop_event.set()
            self.thread.join(timeout=15)
            raise

    def _write(self):
        try:
            while True:
                # Observe the stop fence before popping. A final SDK frame can
                # arrive between an empty pop and StopCapture returning.
                stopped_before_pop = self.stop_event.is_set()
                block = self.camera.pop()
                if block is None:
                    if stopped_before_pop:
                        break
                    self.stop_event.wait(0.005)
                    continue
                if self.written + len(block) > self.config["max_capture_bytes"]:
                    raise RuntimeError("单板图像超过字节上限")
                self.file.write(block)
                self.written += len(block)
                self.last_frame = time.monotonic()
            self.file.flush()
            os.fsync(self.file.fileno())
        except Exception as e:
            self.error = str(e)
        finally:
            self.file.close()

    def finish(self, reason=""):
        try:
            self.camera.stop()
        except Exception as e:
            reason = reason or str(e)
        self.stop_event.set()
        self.thread.join(timeout=15)
        if self.thread.is_alive():
            raise RuntimeError("写盘线程未退出，原始暂存仍在写入；请检查磁盘，不能继续启动相机")
        stats = self.camera.stats()
        height, remainder = divmod(self.written, self.info["stride"])
        reasons = [x for x in (reason, self.error) if x]
        if not height or remainder or self.written != self.path.stat().st_size:
            reasons.append("原始图像长度不完整或没有收到图像")
        if stats["lines"] != height:
            reasons.append("接收行数与写盘行数不一致")
        for k in ("lost", "dropped", "flagged", "broken", "failed"):
            if stats[k]:
                reasons.append(f"{k}={stats[k]}")
        if not self.config["minimum_height"] <= height <= self.config["maximum_height"]:
            reasons.append(f"行高{height}不在{self.config['minimum_height']}～{self.config['maximum_height']}范围")
        self.meta.update(height=height, stats=stats, status="incomplete" if reasons else "ready",
                         reasons=reasons, bytes=self.written)
        if hasattr(self.camera,'diagnostics'):
            self.meta['camera_diagnostics']=self.camera.diagnostics()
        atomic_json(self.meta_path, self.meta)
        return self.meta_path, self.meta
