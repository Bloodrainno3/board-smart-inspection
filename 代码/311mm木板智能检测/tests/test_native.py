import ctypes as C
from pathlib import Path
import time
import pytest
from boardcapture.camera import NativeCamera, RawCapture
from boardcapture.config import defaults

@pytest.fixture
def camera():
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
    cfg.update(expected_width=4,calibration_enabled=False,minimum_height=1)
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
    assert cam.info.trigger==1 and cam.info.multiply==2 and cam.info.divide==25 and cam.info.capture_signal==1
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

def test_native_calibration_failure_is_reported(camera):
    cam,cfg=camera;cam.dll.fake_setup(0,3,0)
    assert not cam.dll.bc_prepare(cam.handle,64,b"test.calib",10000,100000,C.byref(cam.info))

def test_native_two_boards_have_no_previous_lines(camera,tmp_path):
    cam,cfg=camera
    for _ in range(2):
        cap=RawCapture(cam,tmp_path,cfg);cap.start();_,meta=cap.finish()
        assert meta["height"]==194
        assert meta["stats"]["frames"]==4


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
