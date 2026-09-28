from __future__ import annotations
from datetime import datetime
import hashlib
import mmap
import os
from pathlib import Path
import shutil
import struct
import uuid
from PIL import Image
from .config import atomic_json

EXTENSIONS = {"JPG": ".jpg", "PNG": ".png", "BMP": ".bmp", "TIFF": ".tiff"}

def ensure_space(path, reserve_gb, additional=0):
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(path).free < reserve_gb * 1024**3 + additional:
        raise OSError(f"磁盘可用空间不足：{path}；需要保留{reserve_gb:g} GB")

def next_filename(output):
    if output["numbering"] == "timestamp":
        stem = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    else:
        n, end = output["next_number"], output["end_number"]
        step = 1 if output["numbering"] == "ascending" else -1
        if end is not None and (n-end)*step > 0:
            raise ValueError("已到达终止编号，请设置新的起始编号后再开启自动采集")
        if abs(n) > 999999999999999999:
            raise ValueError("编号已达到支持的范围，请设置新的起始编号")
        stem = str(n)
    path = Path(output["directory"]) / (stem + EXTENSIONS[output["format"]])
    # Do not silently overwrite the same numbered board saved in another format.
    for ext in EXTENSIONS.values():
        if path.with_suffix(ext).exists():
            raise FileExistsError(f"编号已存在：{path.with_suffix(ext)}。请修改下一编号或保存目录。")
    return path

def publish_exclusive(temp, target):
    # Windows MoveFile (os.rename) refuses existing destinations, including races.
    if os.name == "nt":
        os.rename(temp, target)
    else:
        os.link(temp, target)
        os.unlink(temp)

def _bmp(raw, target, meta):
    info, h = meta["info"], meta["height"]
    w, channels = info["width"], info["channels"]
    row_bytes = w*channels
    stride = (row_bytes+3) & ~3
    palette = b"".join(bytes((n, n, n, 0)) for n in range(256)) if channels == 1 else b""
    offset = 54 + len(palette)
    with target.open("xb") as dst, raw.open("rb") as src:
        dst.write(struct.pack("<2sIHHI", b"BM", offset+stride*h, 0, 0, offset))
        dst.write(struct.pack("<IiiHHIIiiII", 40, w, -h, 1, channels*8, 0, stride*h, 0, 0, 256 if channels == 1 else 0, 0))
        dst.write(palette)
        for _ in range(h):
            row = src.read(info["stride"])
            if len(row) != info["stride"]:
                raise IOError("原始暂存长度不足")
            row = row[:row_bytes]
            if channels == 3 and meta["pixel_order"] == "RGB":
                bgr = bytearray(row)
                bgr[0::3], bgr[2::3] = row[2::3], row[0::3]
                row = bgr
            dst.write(row)
            dst.write(b"\0"*(stride-row_bytes))
        dst.flush()
        os.fsync(dst.fileno())

def encode(raw, temp, meta, output):
    info, height = meta["info"], meta["height"]
    if raw.stat().st_size != height * info["stride"] or not height:
        raise ValueError("原始暂存大小与图像尺寸不一致")
    if output["format"] == "JPG" and max(info["width"], height) > 65500:
        raise ValueError("JPG单边尺寸最多65500像素；本张原始暂存已保留，请使用BMP/PNG/TIFF恢复")
    if output["format"] == "BMP":
        _bmp(raw, temp, meta)
    else:
        mode = "L" if info["channels"] == 1 else "RGB"
        rawmode = "L" if mode == "L" else meta["pixel_order"]
        with raw.open("rb") as f, mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as mapped:
            image = Image.frombytes(mode, (info["width"], height), mapped, "raw", rawmode, info["stride"], 1)
            try:
                kwargs = {"quality": output["jpeg_quality"], "subsampling": 0} if output["format"] == "JPG" else {}
                fmt = "JPEG" if output["format"] == "JPG" else output["format"]
                with temp.open("xb") as dest:
                    image.save(dest, format=fmt, **kwargs)
                    dest.flush()
                    os.fsync(dest.fileno())
            finally:
                image.close()
    # Verify the final header and file structure without loading another whole long image.
    with Image.open(temp) as check:
        if check.size != (info["width"], height):
            raise IOError("保存后的图像尺寸不正确")
        check.verify()

class ImageSaver:
    def __init__(self, store):
        self.store = store

    def save(self, meta_path, meta):
        if meta["status"] != "ready":
            raise ValueError("不完整采集不能作为正常图片保存")
        config = self.store.snapshot()
        output = config["output"]
        target = next_filename(output)
        raw = Path(meta["raw_path"])
        ensure_space(target.parent, output["minimum_free_gb"], raw.stat().st_size)
        temp = target.parent / ("." + uuid.uuid4().hex + ".partial")
        try:
            # Encoding must never hold the configuration lock used by PLC polling.
            encode(raw, temp, meta, output)
            publish_exclusive(temp, target)
        except Exception:
            temp.unlink(missing_ok=True)
            raise
        with self.store.lock:
            # Persist the counter only after the complete image is published.
            if output["numbering"] != "timestamp":
                output["next_number"] += 1 if output["numbering"] == "ascending" else -1
            try:
                self.store.save(config)
            except Exception as e:
                # Image is already durable; preserve raw + metadata and stop automatic mode.
                meta.update(status="published_counter_error", output_path=str(target), error=str(e))
                atomic_json(meta_path, meta)
                raise RuntimeError(f"图片已保存到{target}，但下一编号写入失败。请修正配置权限后设置下一编号。{e}") from e
            meta.update(status="saved", output_path=str(target))
            atomic_json(meta_path, meta)
            raw.unlink()
            return target, output["archive_directory"]

def archive_image(source, directory):
    """Copy and hash-verify in a separate worker; original always stays on local disk."""
    destination = Path(directory) / source.name
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.resolve() == source.resolve():
        raise ValueError("归档目录不能与图片保存目录相同")
    def digest(path):
        h = hashlib.sha256()
        with path.open("rb") as f:
            for block in iter(lambda: f.read(1024*1024), b""):
                h.update(block)
        return h.digest()
    if destination.exists():
        if digest(destination) == digest(source):
            return destination
        raise FileExistsError(f"归档目标同名但内容不同：{destination}")
    temp = destination.parent / ("." + uuid.uuid4().hex + ".partial")
    try:
        with source.open("rb") as src, temp.open("xb") as dst:
            shutil.copyfileobj(src, dst, 1024*1024)
            dst.flush()
            os.fsync(dst.fileno())
        if digest(temp) != digest(source):
            raise IOError("归档SHA256校验失败，本机图片已保留")
        publish_exclusive(temp, destination)
    finally:
        temp.unlink(missing_ok=True)
    return destination
