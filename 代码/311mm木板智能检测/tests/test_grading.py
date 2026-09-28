import json, time, csv
from pathlib import Path
import numpy as np
import pytest
from PIL import Image
from PySide6.QtCore import QProcess
from PySide6.QtWidgets import QApplication
from boardcapture.config import ConfigStore, atomic_json
from boardcapture.grading import (preprocess,model_manifest,create_request,run_worker,load_records,select_images)
from boardcapture.gui import Window

def test_training_preprocess_and_checkpoint_class_order():
    # Independent reference: same Pillow resize and channel normalization as training.
    pixels=np.random.default_rng(22).integers(0,256,(173,451,3),dtype=np.uint8)
    im=Image.fromarray(pixels)
    import torch
    resized=np.asarray(im.resize((224,224),Image.Resampling.BILINEAR)).copy()
    tensor=torch.from_numpy(resized).permute(2,0,1).float().div_(255)
    tensor=(tensor-torch.tensor([.485,.456,.406])[:,None,None])/torch.tensor([.229,.224,.225])[:,None,None]
    np.testing.assert_array_equal(preprocess(im)[0],tensor.numpy())
    m=model_manifest()['materials']
    assert m['hongxiang']['classes']==['qian','shen']
    assert m['ouxiang']['classes']==['qian','shen','shuangse','zhong']
    assert m['semu']['labels']==['ABCD类','E类']
    assert m['shanhetao']['classes']==['bai','qian','shen','zhong']

def test_directory_output_guard_and_manual_request(tmp_path):
    source=tmp_path/'images';source.mkdir();im=source/'板子.jpg';Image.new('RGB',(48,80)).save(im)
    with pytest.raises(ValueError):create_request([im],'hongxiang',source/'results',source)
    with pytest.raises(ValueError):create_request([im],'unknown',tmp_path/'out',source)
    assert select_images(source)==[str(im)]
    req=create_request([im,im],'semu',tmp_path/'out',source)
    assert len(json.loads(req.read_text(encoding='utf-8'))['files'])==1
    assert not (req.parent/'items').exists()

def test_import_preserves_field_parameters_and_copies_camera_asset(tmp_path,monkeypatch):
    from boardcapture import import_settings as module
    from boardcapture.config import defaults
    old_root=tmp_path/'旧软件';new_root=tmp_path/'新软件'
    cfg=defaults();cfg['serial'].update(port='COM12',baudrate=115200,parity='E')
    cfg['output'].update(directory=str(old_root/'images'),next_number=83,numbering='descending')
    cfg['vision']['model_path']='missing/old/model.onnx'
    cfg['vision']['calibration']={'id':'field-calibration','mm_per_pixel_y':.12}
    cfg['processing'].update(results_directory=str(old_root/'online_results'),auto_grade=True,material='ouxiang')
    key='grabber_config_file' if 'grabber_config_file' in cfg['camera'] else 'calibration_file'
    asset=old_root/'_internal'/'camera.vlcf' if key=='grabber_config_file' else old_root/'_internal'/'camera.calib'
    asset.parent.mkdir(parents=True);asset.write_bytes(b'field camera parameters')
    cfg['camera'][key]=asset.name
    source=old_root/'config/settings.json';atomic_json(source,cfg)
    monkeypatch.setattr(module,'ROOT',new_root)
    path=module.import_settings(source);result=json.loads(path.read_text(encoding='utf-8'))
    assert result['serial']==cfg['serial']
    assert result['output']['next_number']==83 and result['output']['numbering']=='descending'
    assert result['output']['directory']==str(new_root/'images')
    assert result['vision']['calibration']==cfg['vision']['calibration']
    assert result['processing']['results_directory']==str(new_root/'online_results')
    assert result['processing']['auto_grade'] and result['processing']['material']=='ouxiang'
    assert Path(result['camera'][key]).read_bytes()==asset.read_bytes()
    assert result['vision']['model_path']!=cfg['vision']['model_path']

def test_partial_failure_retry_pause_and_csv(tmp_path):
    source=tmp_path/'原图';source.mkdir()
    good=source/'彩色木皮.jpg';Image.new('RGB',(37,85),(148,104,51)).save(good)
    broken=source/'损坏.jpg';broken.write_bytes(b'bad jpeg')
    request=create_request([good,broken],'ouxiang',tmp_path/'结果',source)
    assert run_worker(request)==0
    records=load_records(request.parent);assert [r['status'] for r in records]==['done','error']
    assert sum(p['probability'] for p in records[0]['probabilities'])==pytest.approx(1,abs=1e-6)
    assert records[0]['grade'] in ['浅色','深色','双色','中色']
    csv_path=request.parent/'分级汇总.csv'
    with csv_path.open(encoding='utf-8-sig',newline='') as f:assert len(list(csv.reader(f)))==3
    first=(request.parent/'items/000001.json').stat().st_mtime_ns
    (request.parent/'cancel.request').touch();assert run_worker(request)==0
    assert json.loads((request.parent/'progress.json').read_text(encoding='utf-8'))['status']=='paused'
    Image.new('RGB',(43,61),(172,120,71)).save(broken)
    (request.parent/'cancel.request').unlink();assert run_worker(request)==0
    assert all(r['status']=='done' for r in load_records(request.parent))
    assert (request.parent/'items/000001.json').stat().st_mtime_ns==first
    # Detect a changed model instead of silently resuming with another checkpoint.
    req=json.loads(request.read_text(encoding='utf-8'));req['model_sha256']='wrong';atomic_json(request,req)
    assert run_worker(request)==1
    assert json.loads((request.parent/'progress.json').read_text(encoding='utf-8'))['status']=='error'

def test_manual_window_and_capture_concurrently(tmp_path,monkeypatch):
    app=QApplication.instance() or QApplication([])
    store=ConfigStore(tmp_path/'config/settings.json');cfg=store.snapshot()
    cfg['output'].update(directory=str(tmp_path/'相机原图'),minimum_free_gb=0)
    cfg['camera'].update(minimum_height=1,max_capture_bytes=8*1024*1024)
    cfg['vision']['enabled']=False;store.save(cfg)
    # Isolate acquisition spool/queues from developer and field data.
    from boardcapture import gui,engine
    original_engine=engine.Engine
    monkeypatch.setattr(gui,'Engine',lambda store,emit:original_engine(store,emit,tmp_path/'data'))
    w=Window(store,start_engine=True);g=w.grading_window
    def wait(check,seconds=40):
        end=time.monotonic()+seconds
        while time.monotonic()<end:
            app.processEvents()
            if check():return
            time.sleep(.01)
        raise AssertionError('timed out: '+g.status.text())
    try:
        w.open_grading_window();app.processEvents();assert g.isWindow() and g.isVisible()
        (tmp_path/'分级原图').mkdir()
        im=tmp_path/'分级原图/待分级.jpg';Image.new('RGB',(224,224),(153,101,58)).save(im)
        g.output.setText(str(tmp_path/'分级输出'));g.select_files([str(im)])
        assert g.process.state()==QProcess.ProcessState.NotRunning
        assert not (tmp_path/'分级输出').exists()
        w.engine.submit('simulation',enabled=True);w.engine.submit('plc_connect');w.engine.submit('camera_connect')
        wait(lambda:w.engine.camera is not None and w.engine.plc is not None)
        w.engine.submit('arm');wait(lambda:w.engine.auto and w.engine.trigger.seen_low and w.engine.plc_pulse is None)
        g.start_selected();wait(lambda:g.process.state()!=QProcess.ProcessState.NotRunning)
        w.engine.submit('sim_signal',capture_active=True,front_sensor=True)
        wait(lambda:w.engine.capture is not None)
        assert w.main_tabs.isEnabled() and g.isEnabled()
        # Closing the grading window must not stop its explicitly started worker.
        g.close();app.processEvents();assert not g.isVisible()
        time.sleep(.15)
        w.engine.submit('sim_signal',front_sensor=False,rear_sensor=True,rear_sensor_seen=True)
        time.sleep(.15)
        w.engine.submit('sim_signal',capture_active=False,rear_sensor=False)
        wait(lambda:(tmp_path/'相机原图/1.jpg').exists())
        wait(lambda:g.process.state()==QProcess.ProcessState.NotRunning)
        wait(lambda:g.table.rowCount()==1)
        assert g.records[0]['status']=='done'
        assert store.snapshot()['output']['next_number']==2
        assert len(list((tmp_path/'相机原图').iterdir()))==1
        assert not store.snapshot()['vision']['enabled']
        w.open_grading_window();assert g.isVisible()
    finally:
        g.shutdown();w.engine.submit('shutdown');w.engine.join(10)
        w.close();app.processEvents()
