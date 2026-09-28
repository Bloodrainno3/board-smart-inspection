"""Bounded rolling strip for calibration; no production files or numbering."""
from collections import deque
import io
import threading
import time
import numpy as np
from PIL import Image


class Preview:
    def __init__(self, camera, emit, no_frame_timeout=10):
        self.camera, self.emit = camera, emit
        self.timeout = no_frame_timeout
        self.error = ''
        self.stop_event = threading.Event()
        self.lock = threading.Lock()
        self.latest = None
        self.thread = None
        self.last_frame = 0.0
        self.lines = 0

    def start(self):
        self.camera.start_preview()
        self.started = time.monotonic()
        self.last_frame = self.started
        self.thread = threading.Thread(target=self._run, name='CalibrationPreview', daemon=True)
        self.thread.start()

    def _run(self):
        info = self.camera.info
        limit = info.stride * 1024
        chunks = deque()
        total = 0
        last_emit = 0.0
        try:
            while not self.stop_event.is_set():
                data = self.camera.pop()
                now = time.monotonic()
                if now - self.started > 300:
                    raise RuntimeError('校正预览已运行5分钟，已结束；如需继续请重新启动预览')
                if not data:
                    if now - self.last_frame > self.timeout:
                        raise RuntimeError('校正预览没有收到图像，请核对行触发/编码器；软件不会改写现场触发方式')
                    self.stop_event.wait(.005)
                    continue
                if len(data) % info.stride:
                    raise RuntimeError('校正预览收到不完整图像行')
                self.lines += len(data) // info.stride
                self.last_frame = now
                chunks.append(data[-limit:])
                total += len(chunks[-1])
                while total > limit and len(chunks) > 1:
                    total -= len(chunks.popleft())
                if now - last_emit >= .5:
                    last_emit = now
                    raw = b''.join(chunks)
                    height = len(raw) // info.stride
                    mode = 'RGB' if info.channels == 3 else 'L'
                    img = Image.frombytes(mode, (info.width, height), raw, 'raw', mode, info.stride)
                    with self.lock:
                        self.latest = img
                    display = img.copy()
                    display.thumbnail((900, 420))
                    a = np.asarray(img.convert('RGB'))
                    # Show measured values only; never normalize/white-balance the preview.
                    means = a.mean(axis=(0, 1)).round(1).tolist()
                    clipping = (a >= 254).mean(axis=(0, 1)) * 100
                    png = io.BytesIO()
                    display.save(png, format='PNG')
                    self.emit('cl_preview', {'png': png.getvalue(), 'means': means,
                        'clipping': clipping.round(2).tolist(), 'lines': self.lines,
                        'width': info.width, 'height': height, 'display_only': True})
        except Exception as e:
            self.error = str(e)

    def ready(self):
        return self.lines > 0 and not self.error and time.monotonic() - self.last_frame < min(2, self.timeout)

    def save(self, path):
        with self.lock:
            if self.latest is None:
                raise RuntimeError('预览尚未收到图像')
            img = self.latest.copy()
        with open(path, 'xb') as f:
            img.save(f, format='PNG')
        return img.size

    def stop(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(3)
            if self.thread.is_alive():
                raise RuntimeError('校正预览线程尚未退出，不能开始生产采图')
        self.camera.stop()
