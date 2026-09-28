"""Manual recovery of durable raw spools, preserving their quality status."""
import json
from pathlib import Path
import uuid
from .storage import encode, EXTENSIONS, publish_exclusive

def recover(meta_path, directory, format="BMP"):
    meta_path=Path(meta_path).resolve()
    meta=json.loads(meta_path.read_text(encoding="utf-8-sig"))
    raw=Path(meta["raw_path"])
    if not raw.is_file():
        raw=meta_path.with_suffix(".raw")
    if not raw.is_file():
        raise FileNotFoundError("找不到原始暂存文件")
    stride=meta["info"]["stride"]
    height,rest=divmod(raw.stat().st_size,stride)
    if rest or not height:
        raise ValueError("原始文件不足整行或为空，不能恢复")
    meta["height"]=height
    prefix="recovered_" if meta.get("status")=="ready" else "incomplete_recovered_"
    dest=Path(directory).resolve()
    dest.mkdir(parents=True,exist_ok=True)
    target=dest/(prefix+meta_path.stem+EXTENSIONS[format])
    if target.exists():
        raise FileExistsError(f"恢复目标已存在：{target}")
    temp=dest/("."+uuid.uuid4().hex+".partial")
    try:
        encode(raw,temp,meta,{"format":format,"jpeg_quality":95})
        publish_exclusive(temp,target)
    finally:
        temp.unlink(missing_ok=True)
    return target
