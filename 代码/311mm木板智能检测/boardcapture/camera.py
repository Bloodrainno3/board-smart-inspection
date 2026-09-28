from __future__ import annotations
import ctypes as C
import os
from pathlib import Path
import shutil
import threading
import time
import uuid
from .config import ASSETS, atomic_json

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
        for folder in (driver, Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32" / "Npcap"):
            if folder.is_dir():
                self.dll_paths.append(os.add_dll_directory(str(folder)))
        # The vendor calls LoadLibrary("wpcap.dll") internally. AddDllDirectory alone
        # does not affect that legacy call; preload both Npcap dependencies explicitly.
        npcap = Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32" / "Npcap"
        if (npcap / "Packet.dll").is_file() and (npcap / "wpcap.dll").is_file():
            try:
                self.runtime_handles = [C.WinDLL(str(npcap / name)) for name in ("Packet.dll", "wpcap.dll")]
            except OSError as e:
                raise RuntimeError(f"Npcap加载失败：{e}") from e
        try:
            self.dll = C.CDLL(str(driver / "board_camera_bridge.dll"))
        except OSError as e:
            raise RuntimeError(f"厂家DLL加载失败：{e}\n请保留完整camera_driver目录，并安装64位Npcap（WinPcap兼容模式）。") from e
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
        if d.bc_abi() != 1:
            raise RuntimeError("相机桥接DLL版本不匹配，请重新完整解压软件")

    def error(self):
        buf = C.create_string_buffer(256)
        self.dll.bc_error(buf)
        return buf.value.decode("utf-8", errors="replace") or "厂家SDK操作失败"

    def enumerate(self):
        count = self.dll.bc_count()
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
            raise RuntimeError(f"发现{len(devices)}台相机，匹配{len(matched)}台。请检查供电、网段、关闭MLines；多相机时填写序列号。\n{listing}\n{self.error()}")
        index, self.description = matched[0]
        self.handle = self.dll.bc_open(index)
        if not self.handle:
            raise RuntimeError(self.error())
        try:
            calibration = b""
            if config["calibration_enabled"]:
                path = Path(config["calibration_file"])
                if not path.is_absolute():
                    path = ASSETS / path
                if not path.is_file():
                    raise RuntimeError(f"校正文件不存在：{path}")
                # SDK accepts the Windows ANSI path used by its C++ reference.
                calibration = str(path.resolve()).encode("mbcs", errors="strict")
            self.info = Info()
            ok = self.dll.bc_prepare(self.handle, config["chunk_lines"], calibration,
                config["queue_mb"] * 1024 * 1024, config["max_capture_bytes"], C.byref(self.info))
            if not ok:
                raise RuntimeError("相机分块/校正配置失败：" + self.error())
            if config["expected_width"] and self.info.width != config["expected_width"]:
                raise RuntimeError(f"相机实际宽度{self.info.width}，预期{config['expected_width']}；请核对相机及高级设置")
            self.buffer = C.create_string_buffer(self.info.stride * self.info.height)
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

    def stats(self):
        s = Stats()
        self.dll.bc_stats(self.handle, C.byref(s))
        return as_dict(s)

    def working(self):
        return bool(self.dll.bc_working(self.handle))

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
                     "pixel_order": config["pixel_order"], "status": "capturing"}
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
        atomic_json(self.meta_path, self.meta)
        return self.meta_path, self.meta
