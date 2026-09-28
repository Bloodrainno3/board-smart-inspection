"""Portable result loading and a local, explicit export of one problem sample."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import zipfile

ARTIFACTS = {'overlay': 'overlay.jpg', 'model_mask': 'model_mask_1024.png',
             'measurement_mask': 'mask_1024.png', 'source_preview': 'source_1024.jpg'}


def load_result_file(path):
    path = Path(path).resolve()
    result = json.loads(path.read_text(encoding='utf-8-sig'))
    if 'seams' not in result:
        raise ValueError('请选择 result.json 识别结果文件')
    result['result_file'] = str(path)
    for key, name in ARTIFACTS.items():
        local = path.parent / name
        if local.is_file():
            result[key] = str(local)
    source = Path(result['source'])
    if not source.is_absolute():
        result['source'] = str((path.parent / source).resolve())
    return result


def export_problem_sample(result, destination):
    """Export only the selected original and its recognition data, never other jobs."""
    destination = Path(destination)
    if destination.suffix.lower() != '.zip':
        raise ValueError('问题样本请保存为 .zip 文件')
    source = Path(result['source'])
    if not source.is_file():
        raise ValueError('找不到该结果对应的相机原图，请恢复原图或重新导入后导出：' + str(source))
    original_name = 'original' + source.suffix.lower()
    files = [(source, original_name)]
    portable = copy.deepcopy(result)
    portable['source'] = original_name
    portable['result_file'] = 'result.json'
    for key, name in ARTIFACTS.items():
        if result.get(key) and Path(result[key]).is_file():
            files.append((Path(result[key]), name))
            portable[key] = name
        else:
            portable.pop(key, None)
    csv_path = Path(result['result_file']).parent / 'parameters.csv'
    if csv_path.is_file():
        files.append((csv_path, 'parameters.csv'))
    if any(destination.resolve() == path.resolve() for path, _ in files):
        raise ValueError('不能覆盖样本原文件')
    destination.parent.mkdir(parents=True, exist_ok=True)
    manifest = {}
    with tempfile.NamedTemporaryFile(dir=destination.parent, prefix=destination.name + '.', suffix='.part', delete=False) as temp:
        partial = Path(temp.name)
    try:
        with zipfile.ZipFile(partial, 'w', zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
            for path, name in files:
                digest = hashlib.sha256()
                with path.open('rb') as incoming, archive.open(name, 'w', force_zip64=True) as output:
                    for block in iter(lambda: incoming.read(1024 * 1024), b''):
                        digest.update(block)
                        output.write(block)
                manifest[name] = digest.hexdigest()
            archive.writestr('result.json', json.dumps(portable, ensure_ascii=False, indent=2))
            archive.writestr('files_sha256.json', json.dumps(manifest, indent=2))
            archive.writestr('样本说明.txt', '本包由用户主动导出，包含所选相机原图、识别设置、结果及可用掩膜。\n'
                'original 文件为原始字节，未缩放或重压缩。解压后可在软件中加载 result.json 查看，或导入 original 图片重新识别。\n'
                '“未检出”只表示这次算法没有保留候选，不等于板材没有缝。\n')
        partial.replace(destination)
    finally:
        partial.unlink(missing_ok=True)
    return str(destination)
