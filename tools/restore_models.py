"""Restore verified Release models to both applications without replacing custom weights."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
PROJECTS = ("311mm木板智能检测", "658mm木板智能检测")


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def restore(archive: Path) -> None:
    manifest = json.loads((ROOT / "model-assets.json").read_text(encoding="utf-8"))
    if sha256(archive) != manifest["archive_sha256"]:
        raise ValueError("模型包 SHA-256 不一致，请重新下载对应 Release 附件。")
    expected = manifest["files"]
    for name in expected:
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts or "\\" in name or ":" in name:
            raise ValueError(f"无效模型路径：{name}")
    targets = [ROOT / "代码" / project / "models" for project in PROJECTS]
    for target in targets:
        if not target.is_dir():
            raise FileNotFoundError(f"缺少项目模型目录：{target}")
        for name, info in expected.items():
            dest = target / name
            if dest.exists() and sha256(dest) != info["sha256"]:
                raise FileExistsError(f"已有不同模型，未覆盖。请先备份或移走：{dest}")

    # Verify every member before changing either application's models.
    with tempfile.TemporaryDirectory(prefix="board-models-") as staging:
        stage = Path(staging)
        with zipfile.ZipFile(archive) as source:
            names = source.namelist()
            if len(names) != len(expected) or set(names) != set(expected):
                raise ValueError("模型包文件清单不一致。")
            for name, info in expected.items():
                entry = source.getinfo(name)
                if entry.file_size != info["bytes"]:
                    raise ValueError(f"模型大小不符：{name}")
                staged = stage / name
                staged.parent.mkdir(parents=True, exist_ok=True)
                with source.open(entry) as src, staged.open("xb") as dst:
                    shutil.copyfileobj(src, dst, 1024 * 1024)
                if sha256(staged) != info["sha256"]:
                    raise ValueError(f"模型校验失败：{name}")
                print(f"Verified: {name}", flush=True)
        for target in targets:
            for name, info in expected.items():
                dest = target / name
                dest.parent.mkdir(parents=True, exist_ok=True)
                if dest.exists():
                    if sha256(dest) != info["sha256"]:
                        raise FileExistsError(f"模型在恢复期间发生变化，未覆盖：{dest}")
                    continue
                # Same-directory temporary file ensures readers see a complete model.
                with tempfile.NamedTemporaryFile(dir=dest.parent, suffix=".tmp", delete=False) as stream:
                    pending = Path(stream.name)
                try:
                    shutil.copyfile(stage / name, pending)
                    # Windows rename refuses replacement of an existing destination.
                    if dest.exists():
                        raise FileExistsError(f"目标文件已存在，未覆盖：{dest}")
                    pending.rename(dest)
                finally:
                    pending.unlink(missing_ok=True)
            print(f"Restored: {target.parent.name}", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True, help="Release 中的模型 ZIP 路径")
    args = parser.parse_args()
    try:
        restore(args.archive.resolve())
    except (OSError, ValueError, KeyError, zipfile.BadZipFile) as error:
        parser.exit(1, f"{error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
