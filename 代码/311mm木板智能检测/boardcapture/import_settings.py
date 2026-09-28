"""Import same-camera field settings into a separately named release."""
import copy
import hashlib
import json
from pathlib import Path
import shutil
import time
from .config import ROOT,ASSETS,defaults,merge,validate,atomic_json

def import_settings(source):
    source=Path(source).resolve();old=json.loads(source.read_text(encoding='utf-8-sig'))
    if not isinstance(old,dict):raise ValueError('配置文件必须为JSON对象')
    is_658='grabber_config_file' in defaults()['camera']
    if ('grabber_config_file' in old.get('camera',{}))!=is_658:
        raise ValueError('相机版本不一致：311mm与658mm的配置不能互相导入')
    old_root=source.parent.parent if source.parent.name=='config' else source.parent
    cfg=merge(defaults(),copy.deepcopy(old))
    # Internal model paths always refer to the model shipped in this release.
    cfg['vision']['model_path']=str(ASSETS/'models'/'best.onnx')
    for group,key,folder in [('output','directory','images'),('vision','results_directory','results'),
                             ('processing','results_directory','online_results')]:
        value=Path(cfg[group][key])
        if not value.is_absolute():value=old_root/value
        if value==old_root or old_root in value.parents:cfg[group][key]=str(ROOT/folder)
    if is_658:cfg['vision']['manual_window_v1']=True
    key='grabber_config_file' if is_658 else 'calibration_file'
    original=cfg['camera'].get(key,'')
    asset=None
    if original:
        path=Path(original)
        if not path.is_absolute():
            candidates=[old_root/path,old_root/'_internal'/path]
            path=next((p for p in candidates if p.is_file()),candidates[0])
        if not path.is_file():raise ValueError(f'关联的相机配置文件不存在：{path}，请先从现场旧版本找到该文件')
        suffix=path.suffix
        asset=ROOT/'config'/'imported'/('camera_'+hashlib.sha256(path.read_bytes()).hexdigest()[:12]+suffix)
        cfg['camera'][key]=str(asset)
    validate(cfg)
    target=ROOT/'config'/'settings.json';target.parent.mkdir(parents=True,exist_ok=True)
    if target.exists():
        backup=ROOT/'config'/'history'/('settings_'+time.strftime('%Y%m%d_%H%M%S')+'_'+str(time.time_ns())+'.json')
        backup.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(target,backup)
    if asset:
        asset.parent.mkdir(parents=True,exist_ok=True)
        if path.resolve()!=asset.resolve():shutil.copyfile(path,asset)
    atomic_json(target,cfg)
    return target
