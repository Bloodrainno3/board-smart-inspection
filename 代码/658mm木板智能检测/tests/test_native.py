import ctypes as C
from pathlib import Path
import time
import pytest
from boardcapture.camera import NativeCamera, RawCapture
from boardcapture.config import defaults

@pytest.fixture
def camera(tmp_path):
    path=Path(__file__).parent/"native_test"
    if not (path/"board_camera_bridge.dll").exists():
        pytest.fail("Build native ABI tests first: tests/build_fake.cmd")
    cam=NativeCamera(driver=path)
    cam.dll.fake_setup.argtypes=[C.c_uint,C.c_uint,C.c_int]
    cam.dll.fake_setup.restype=None
    cam.dll.fake_bad_calls.argtypes=[]
    cam.dll.fake_bad_calls.restype=C.c_int
    cam.dll.fake_setup(0,3,1)
    cfg=defaults()["camera"]
    profile=tmp_path/'测试配置.vlcf';profile.write_text('mock configuration')
    cfg.update(expected_width=4,grabber_config_file=str(profile),minimum_height=1)
    cam.connect(cfg)
    yield cam,cfg
    cam.close()

def test_native_tail_copy_and_trigger_preserved(camera,tmp_path):
    cam,cfg=camera
    cap=RawCapture(cam,tmp_path,cfg)
    cap.start()
    mp,meta=cap.finish()
    assert meta["status"]=="ready"
    assert meta["height"]==3*64+2
    assert Path(meta["raw_path"]).read_bytes()==bytes([42])*(194*12)
    assert cam.info.trigger==0 and cam.info.multiply==0xFFFFFFFF
    assert cam.dll.fake_bad_calls()==0

@pytest.mark.parametrize("flags,key",[(1,"flagged"),(2,"lost"),(4,"broken"),(8,"failed")])
def test_native_faults_mark_incomplete(camera,tmp_path,flags,key):
    cam,cfg=camera;cam.dll.fake_setup(flags,3,1)
    cap=RawCapture(cam,tmp_path,cfg);cap.start();_,meta=cap.finish()
    assert meta["status"]=="incomplete"
    assert meta["stats"][key]>0

def test_native_queue_overflow_is_counted(camera):
    cam,cfg=camera
    assert cam.dll.bc_prepare(cam.handle,64,b"",768,100000,C.byref(cam.info))
    cam.start();cam.stop()
    stats=cam.stats()
    assert stats["dropped"]==130
    assert stats["peak"]==768
    assert len(cam.pop())==768
    assert cam.pop() is None
    assert cam.dll.fake_bad_calls()==0

def test_native_total_capture_limit(camera):
    cam,cfg=camera
    assert cam.dll.bc_prepare(cam.handle,64,b"",10000,768,C.byref(cam.info))
    cam.start();cam.stop()
    assert cam.stats()["dropped"]==130


def test_preview_limit_is_separate_and_production_limit_is_restored(camera):
    cam, cfg = camera
    assert cam.dll.bc_prepare(cam.handle, 64, b'', 10000, 768, C.byref(cam.info))
    cam.start_preview()
    assert cam.stats()['lines'] == 192
    assert cam.stats()['dropped'] == 0
    cam.stop()
    cam.start(); cam.stop()
    assert cam.stats()['dropped'] == 130

def test_native_configuration_failure_is_reported(camera):
    cam,cfg=camera;cam.dll.fake_setup(0,3,0)
    assert not cam.dll.bc_prepare(cam.handle,64,cfg['grabber_config_file'].encode('utf-8'),10000,100000,C.byref(cam.info))
    assert 'Load .vlcf configuration' in cam.error()

def test_configuration_under_chinese_directory(camera,tmp_path):
    cam,cfg=camera;cam.close()
    folder=tmp_path/'现场相机 配置';folder.mkdir()
    profile=folder/'中文采集配置.vlcf'
    profile.write_bytes((Path(__file__).parent/'fixtures/camera658_1.vlcf').read_bytes())
    original=profile.read_bytes()
    cam.connect(dict(cfg,grabber_config_file=str(profile)))
    assert cam.handle and profile.read_bytes()==original
    assert cam.dll.fake_bad_calls()==0

def test_configuration_missing_after_preflight_has_actionable_error(camera,tmp_path):
    cam,cfg=camera
    missing=str(tmp_path/'不存在.vlcf').encode('utf-8')
    assert not cam.dll.bc_prepare(cam.handle,64,missing,10000,100000,C.byref(cam.info))
    assert 'no longer exists' in cam.error()

def test_native_two_boards_have_no_previous_lines(camera,tmp_path):
    cam,cfg=camera
    for _ in range(2):
        cap=RawCapture(cam,tmp_path,cfg);cap.start();_,meta=cap.finish()
        assert meta["height"]==194
        assert meta["stats"]["frames"]==4

@pytest.mark.parametrize('fault,ready',[(8192,True),(16384,False)])
def test_stop_zero_line_unused_buffer_vs_invalid_full_buffer(camera,tmp_path,fault,ready):
    cam,cfg=camera;cam.dll.fake_setup(fault,3,1)
    cap=RawCapture(cam,tmp_path,cfg);cap.start();_,meta=cap.finish()
    assert meta['height']==192
    assert (meta['status']=='ready') is ready
    assert bool(meta['stats']['failed']) is not ready
    assert 'stopped buffer' in meta['camera_diagnostics']

def test_incomplete_capture_reports_reason_to_main_window(camera,tmp_path):
    from boardcapture.engine import Engine
    from boardcapture.config import ConfigStore
    cam,cfg=camera;cam.dll.fake_setup(16384,3,1)
    events=[];e=Engine(ConfigStore(tmp_path/'settings.json'),lambda k,v:events.append((k,v)),tmp_path/'data')
    try:
        e.auto=True;e.camera=cam
        e.capture=RawCapture(cam,tmp_path/'spool',cfg);e.capture.start()
        e._finish_capture()
        assert not e.auto and not e.pending
        errors=[v for k,v in events if k=='error']
        assert errors and 'failed=1' in errors[-1] and '.json' in errors[-1]
        assert any((tmp_path/'spool').glob('*.raw'))
    finally:e.encoder.shutdown();e.archiver.shutdown()

@pytest.mark.parametrize('flags',[0,16])
def test_stop_partial_with_or_without_callback_never_duplicates(camera,tmp_path,flags):
    cam,cfg=camera;cam.dll.fake_setup(flags,19,1)
    cap=RawCapture(cam,tmp_path,cfg);cap.start();_,meta=cap.finish()
    cam.stop()  # A second stop must not enqueue the tail again.
    assert meta['status']=='ready' and meta['height']==19*64+2
    assert cam.pop() is None and cam.dll.fake_bad_calls()==0

@pytest.mark.parametrize('depth,kind,width',[(8,0,7776),(8,1,15588),(8,3,7776)])
def test_658_width_and_pixel_layout(camera,tmp_path,depth,kind,width):
    cam,cfg=camera;cam.close();cam.dll.fake_setup(512,3,1)
    cam.dll.fake_format.argtypes=[C.c_int,C.c_int,C.c_int]
    cam.dll.fake_format(depth,kind,width)
    cfg.update(expected_width=0,pixel_order='BGR')
    cam.connect(cfg)
    cap=RawCapture(cam,tmp_path,cfg);cap.start();_,meta=cap.finish()
    assert meta['status']=='ready' and meta['info']['width']==width
    assert meta['pixel_order']=='RGB'
    channels=1 if kind==0 else 3
    data=Path(meta['raw_path']).read_bytes()
    assert len(data)==width*194*channels
    assert data[:3]==(bytes([30,20,10]) if kind==3 else bytes([10,20,30]))
    assert cam.dll.fake_bad_calls()==0

@pytest.mark.parametrize('flags',[32,64,128])
def test_bad_layout_or_allocation_rejected(camera,flags):
    cam,cfg=camera;cam.close();cam.dll.fake_setup(flags,3,1)
    with pytest.raises(RuntimeError,match='IKap采集配置失败'):cam.connect(cfg)
    assert cam.handle is None

@pytest.mark.parametrize('depth,kind',[(16,0),(8,2),(8,5)])
def test_unsupported_pixels_rejected(camera,depth,kind):
    cam,cfg=camera;cam.close()
    cam.dll.fake_format.argtypes=[C.c_int,C.c_int,C.c_int];cam.dll.fake_format(depth,kind,4)
    with pytest.raises(RuntimeError,match='IKap采集配置失败'):cam.connect(cfg)
    assert cam.handle is None

def test_configuration_required_and_wrong_width_rejected(camera):
    cam,cfg=camera;cam.close()
    with pytest.raises(RuntimeError,match='vlcf'):cam.connect(dict(cfg,grabber_config_file=''))
    with pytest.raises(RuntimeError,match='实际宽度'):cam.connect(dict(cfg,expected_width=658))
    assert cam.handle is None

def test_multiple_cards_require_selection_and_enumeration_error(camera):
    cam,cfg=camera;cam.close();cam.dll.fake_setup(2048,3,1)
    with pytest.raises(RuntimeError,match='匹配2个'):cam.connect(cfg)
    cam.connect(dict(cfg,serial_number='1'));cam.close()
    cam.dll.fake_setup(4096,3,1)
    with pytest.raises(RuntimeError,match='枚举失败'):cam.enumerate()

def test_start_and_stop_failure_reported(camera,tmp_path):
    cam,cfg=camera;cam.dll.fake_setup(256,3,1)
    with pytest.raises(RuntimeError,match='开始采集失败'):cam.start()
    assert not cam.working()
    cam.dll.fake_setup(1024,3,1)
    cap=RawCapture(cam,tmp_path,cfg);cap.start();_,meta=cap.finish()
    assert meta['status']=='incomplete' and meta['stats']['failed']>0
    assert 'IKapStopGrab' in meta['camera_diagnostics'] and '0x00000009' in meta['camera_diagnostics']


@pytest.mark.parametrize('trigger_mode',['photoelectric','plc_m020'])
def test_selected_trigger_invokes_native_sdk(camera,tmp_path,trigger_mode):
    from boardcapture.engine import Engine
    from boardcapture.config import ConfigStore
    from test_core import wait_until
    from PIL import Image
    cam,camera_config=camera
    store=ConfigStore(tmp_path/'settings.json');cfg=store.snapshot()
    cfg['camera']=camera_config;cfg['trigger']['mode']=trigger_mode
    cfg['vision']['enabled']=False
    cfg['output'].update(directory=str(tmp_path/'images'),minimum_free_gb=0)
    store.save(cfg);events=[]
    e=Engine(store,lambda k,v:events.append((k,v)),tmp_path/'data')
    e.simulation=True;e.plc='simulation';e.camera=cam;e.start()
    try:
        e.submit('arm');wait_until(lambda:e.auto and e.trigger.seen_low)
        assert not e.sim_status['capture_active']
        if trigger_mode=='plc_m020':
            # Only PLC M bits change: X inputs cannot be the source of this start.
            e.submit('sim_signal',capture_active=True)
        else:e.submit('sim_signal',front_sensor=True)
        wait_until(lambda:e.capture is not None and cam.stats()['frames']==3)
        assert cam.working()
        if trigger_mode=='plc_m020':
            e.submit('sim_signal',rear_sensor_seen=True);time.sleep(.13)
            assert e.capture is not None and cam.working()
            e.submit('sim_signal',capture_active=False,rear_sensor_seen=False)
        else:
            e.submit('sim_signal',front_sensor=False,rear_sensor=True)
            wait_until(lambda:e.trigger.rear_seen)
            e.submit('sim_signal',rear_sensor=False)
        wait_until(lambda:any(k=='saved' for k,v in events))
        with Image.open(tmp_path/'images'/'1.jpg') as im:assert im.size==(4,194)
        assert cam.dll.fake_bad_calls()==0
        assert not [v for k,v in events if k=='error']
    finally:e.submit('shutdown');e.join(8)


def test_photographed_ladder_confirmation_invokes_native_camera(camera,tmp_path):
    from test_plc_ladder import LadderTransport,controller
    from test_core import wait_until
    from PIL import Image
    cam,cfg=camera;t=LadderTransport()
    t.inputs(x1=True);assert not t.m[20]  # reproduce the field failure with M10=0
    t.inputs(x1=False)
    e,events=controller(tmp_path,t,cam,cfg);e.start()
    try:
        e.submit('arm');wait_until(lambda:t.m[10] and t.m[300])
        t.inputs(x1=True)  # camera must start even while M300 pulse is still high
        wait_until(lambda:e.capture is not None and cam.stats()['frames']==3)
        t.inputs(x1=False,x2=True);wait_until(lambda:e.status['rear_sensor_seen'])
        assert e.capture is not None
        t.inputs(x2=False);wait_until(lambda:any(k=='saved' for k,v in events))
        with Image.open(tmp_path/'images'/'1.jpg') as im:assert im.size==(4,194)
        wait_until(lambda:e.plc_pulse is None)
        assert not t.m[300] and cam.dll.fake_bad_calls()==0
        e.submit('disarm');wait_until(lambda:e.plc_pulse is None and not t.m[10])
        assert not t.m[400] and not [v for k,v in events if k=='error']
    finally:e.submit('shutdown');e.join(8)
