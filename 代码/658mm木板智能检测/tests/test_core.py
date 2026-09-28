import copy
import json
from pathlib import Path
import struct
import threading
import time
import pytest
from PIL import Image
from boardcapture.config import ConfigStore, defaults, atomic_json, validate, MAPPING
from boardcapture.plc import PLC, packet, crc16, ModbusError
from boardcapture.engine import Trigger, Engine
from boardcapture.camera import RawCapture, SimCamera
from boardcapture.storage import ImageSaver, encode, archive_image, next_filename

def config(tmp_path):
    store=ConfigStore(tmp_path/"settings.json")
    cfg=store.snapshot()
    cfg['trigger']['mode']='plc_m020'
    cfg["output"].update(directory=str(tmp_path/"图片"),minimum_free_gb=0)
    cfg["camera"].update(minimum_height=1,expected_width=320,max_capture_bytes=1024*1024)
    store.save(cfg)
    return store

def raw_image(tmp_path,status="ready",stride=12,channels=3):
    tmp_path.mkdir(parents=True,exist_ok=True)
    raw=tmp_path/"test.raw"
    width=3 if channels==3 else 9
    row=bytes((220,80,30))*3 if channels==3 else bytes(range(9))
    raw.write_bytes((row+b"\0"*(stride-len(row)))*7)
    meta={"raw_path":str(raw),"info":{"width":width,"height":64,"stride":stride,"channels":channels},
          "height":7,"pixel_order":"RGB","status":status}
    path=raw.with_suffix(".json")
    atomic_json(path,meta)
    return path,meta

def test_bom_and_null_mapping(tmp_path):
    cfg=defaults()
    for value in cfg["devices"].values():value["address"]=None
    path=tmp_path/"settings.json"
    path.write_text(json.dumps(cfg),encoding="utf-8-sig")
    store=ConfigStore(path)
    assert len(store.notices)==len(MAPPING)
    assert store.snapshot()["devices"]["front_sensor"]["address"]==13313
    store.save(store.snapshot())
    assert not path.read_bytes().startswith(b"\xef\xbb\xbf")

def test_corrupt_config_never_resets_or_simulates(tmp_path):
    path=tmp_path/"settings.json";path.write_text("broken")
    with pytest.raises(ValueError):ConfigStore(path)
    assert path.read_text()=="broken"

def test_custom_mapping_survives(tmp_path):
    c=defaults();c["devices"]["front_sensor"]["address"]=55
    path=tmp_path/"settings.json";atomic_json(path,c)
    assert ConfigStore(path).snapshot()["devices"]["front_sensor"]["address"]==55

@pytest.mark.parametrize("value",[None,-1,65536,True,"13312"])
def test_bad_map_rejected(value):
    c=defaults();c["devices"]["front_sensor"]["address"]=value
    with pytest.raises(ValueError):validate(c)

class Transport:
    def __init__(self,mode="good"):
        self.mode=mode;self.requests=[];self.response=b"";self.closed=False
    def reset_input_buffer(self):self.response=b""
    def write(self,data):
        self.requests.append(data)
        slave,fn,addr,count=struct.unpack(">BBHH",data[:-2])
        assert data==packet(data[:-2])
        assert fn in (1,2)
        if self.mode=="exception": self.response=packet(bytes([slave,fn|0x80,2]))
        else: self.response=packet(bytes([slave,fn,(count+7)//8])+bytes([5 if fn==2 else 3]))
        if self.mode=="crc":self.response=self.response[:-1]+bytes([self.response[-1]^255])
        if self.mode=="station":self.response=packet(bytes([slave+1])+self.response[1:-2])
        if self.mode=="timeout":self.response=b""
        return len(data)
    def read(self,n):
        # Exercise serial fragmentation (one byte per call).
        n=min(n,1);part,self.response=self.response[:n],self.response[n:];return part
    def flush(self):pass
    def close(self):self.closed=True

def test_modbus_known_crc_and_exact_requests():
    assert packet(bytes.fromhex("01030000000a")).hex()=="01030000000ac5cd"
    t=Transport();p=PLC(defaults(),transport=t)
    s=p.read_status()
    assert s==dict(emergency_ok=True,front_sensor=False,rear_sensor=True,capture_active=True,rear_sensor_seen=True,
                   conveyor_running=True,conveyor_start_command=True,conveyor_stop_command=True)
    assert [r[:6].hex() for r in t.requests]==["010234000003","0101000a0001","010100140002","0101012c0001","010101900001"]

@pytest.mark.parametrize("mode",["crc","station","timeout","exception"])
def test_serial_errors(mode):
    with pytest.raises(ModbusError):PLC(defaults(),transport=Transport(mode)).read_status()

def status(active=False,emergency=True):
    return dict(capture_active=active,emergency_ok=emergency)

def test_trigger_requires_idle_and_only_one_start():
    t=Trigger()
    assert t.update(status(True),0,False,100) is None
    assert t.update(status(False),1,False,100) is None
    assert t.update(status(True),2,False,100)=="start"
    assert t.update(status(True),3,True,100) is None
    assert t.update(status(False),4,True,100) is None
    assert t.update(status(False),4.11,True,100)=="finish"

def test_tail_bounce_and_emergency():
    t=Trigger();t.update(status(False),0,False,100)
    assert t.update(status(True),1,False,100)=="start"
    t.update(status(False),2,True,100)
    assert t.update(status(True),2.05,True,100) is None
    assert t.deadline is None
    assert t.update(status(True,False),2.1,True,100)=="emergency"
    assert not t.seen_low


def test_writer_drains_tail_if_stop_occurs_after_empty_pop(tmp_path):
    class StopRaceCamera:
        def __init__(self):
            base=SimCamera();base.connect(defaults()['camera'])
            self.info=base.info;self.empty=threading.Event();self.pending=None;self.first=True
        def start(self):pass
        def pop(self):
            if self.first:
                self.first=False;self.empty.set()
                assert capture.stop_event.wait(3)
                return None  # stale empty observation from before the SDK flush
            value,self.pending=self.pending,None
            return value
        def stop(self):self.pending=bytes([42])*(self.info.stride*2)
        def stats(self):
            return dict(lines=2,frames=1,lost=0,dropped=0,flagged=0,queued=len(self.pending or b''),peak=self.info.stride*2,broken=0,failed=0)
    cam=StopRaceCamera();cfg=defaults()['camera'];cfg['minimum_height']=1
    capture=RawCapture(cam,tmp_path,cfg);capture.start()
    assert cam.empty.wait(3)
    _,meta=capture.finish()
    assert meta['status']=='ready' and meta['height']==2 and meta['stats']['queued']==0

@pytest.mark.parametrize("format",["JPG","PNG","BMP","TIFF"])
@pytest.mark.parametrize("channels",[1,3])
def test_formats_color_stride_no_sidecars(tmp_path,format,channels):
    store=config(tmp_path);cfg=store.snapshot();cfg["output"]["format"]=format;store.save(cfg)
    meta_path,meta=raw_image(tmp_path/"spool",channels=channels)
    path,_=ImageSaver(store).save(meta_path,meta)
    with Image.open(path) as img:
        assert img.size==(meta["info"]["width"],7)
        pixel=img.getpixel((0,0))
        if channels==3: assert all(abs(a-b)<5 for a,b in zip(pixel,(220,80,30)))
        else: assert abs(pixel-0)<3
    assert list(path.parent.iterdir())==[path]
    assert not Path(meta["raw_path"]).exists()
    assert store.snapshot()["output"]["next_number"]==2

@pytest.mark.parametrize("mode,start,names",[("ascending",42,["42.jpg","43.jpg","44.jpg"]),("descending",9,["9.jpg","8.jpg","7.jpg"]),("descending",0,["0.jpg","-1.jpg","-2.jpg"])])
def test_arbitrary_numbering_persists(tmp_path,mode,start,names):
    store=config(tmp_path);c=store.snapshot();c["output"].update(numbering=mode,next_number=start);store.save(c)
    for i,expected in enumerate(names):
        mp,m=raw_image(tmp_path/f"raw{i}")
        path,_=ImageSaver(store).save(mp,m);assert path.name==expected
        store=ConfigStore(store.path)

def test_collision_never_overwrites_or_consumes_number(tmp_path):
    store=config(tmp_path);out=Path(store.snapshot()["output"]["directory"]);out.mkdir()
    (out/"1.jpg").write_bytes(b"existing")
    mp,m=raw_image(tmp_path/"raw")
    with pytest.raises(FileExistsError):ImageSaver(store).save(mp,m)
    assert (out/"1.jpg").read_bytes()==b"existing"
    assert store.snapshot()["output"]["next_number"]==1
    assert Path(m["raw_path"]).exists()

def test_exhausted_number_range(tmp_path):
    store=config(tmp_path);o=store.snapshot()["output"]
    o.update(numbering="descending",next_number=0,end_number=1)
    with pytest.raises(ValueError):next_filename(o)

def test_save_failure_preserves_raw_counter_cleans_partial(tmp_path,monkeypatch):
    store=config(tmp_path);mp,m=raw_image(tmp_path/"raw")
    def fail(*args,**kwargs):raise OSError("disk full")
    monkeypatch.setattr(Image.Image,"save",fail)
    with pytest.raises(OSError):ImageSaver(store).save(mp,m)
    assert store.snapshot()["output"]["next_number"]==1
    assert Path(m["raw_path"]).exists()
    assert not list(Path(store.snapshot()["output"]["directory"]).iterdir())

def test_incomplete_does_not_use_number(tmp_path):
    store=config(tmp_path);mp,m=raw_image(tmp_path/"raw",status="incomplete")
    with pytest.raises(ValueError):ImageSaver(store).save(mp,m)
    assert store.snapshot()["output"]["next_number"]==1

def test_jpeg_height_limit_retains_raw(tmp_path):
    store=config(tmp_path);mp,m=raw_image(tmp_path/"raw")
    m["height"]=65501;Path(m["raw_path"]).write_bytes(b"\0"*(65501*12))
    with pytest.raises(ValueError,match="65500"):ImageSaver(store).save(mp,m)
    assert Path(m["raw_path"]).exists()

def test_encoding_does_not_block_config_poll(tmp_path,monkeypatch):
    store=config(tmp_path);mp,m=raw_image(tmp_path/"raw")
    from boardcapture import storage
    original=storage.encode
    entered=threading.Event();release=threading.Event()
    def slow(*args):entered.set();release.wait(3);return original(*args)
    monkeypatch.setattr(storage,"encode",slow)
    thread=threading.Thread(target=ImageSaver(store).save,args=(mp,m));thread.start()
    assert entered.wait(2)
    try:
        assert store.lock.acquire(timeout=.1)
        store.lock.release()
    finally:release.set();thread.join(3)

def test_archive_verified_original_retained(tmp_path):
    src=tmp_path/"1.jpg";src.write_bytes(b"test"*1024)
    target=archive_image(src,tmp_path/"archive")
    assert target.read_bytes()==src.read_bytes()
    assert archive_image(src,tmp_path/"archive")==target
    target.write_bytes(b"other")
    with pytest.raises(FileExistsError):archive_image(src,tmp_path/"archive")
    assert src.exists()

def test_raw_writer_and_tail(tmp_path):
    cam=SimCamera();cfg=defaults()["camera"];cfg["minimum_height"]=1;cam.connect(cfg)
    cap=RawCapture(cam,tmp_path,cfg);cap.start();time.sleep(.12)
    mp,meta=cap.finish()
    assert meta["status"]=="ready"
    assert meta["height"]>0
    assert meta["bytes"]==meta["height"]*meta["info"]["stride"]

def test_counter_commit_failure_keeps_published_image(tmp_path,monkeypatch):
    store=config(tmp_path);mp,m=raw_image(tmp_path/"raw")
    def fail(*args):raise PermissionError("config read-only")
    monkeypatch.setattr(store,"save",fail)
    with pytest.raises(RuntimeError,match="图片已保存"):ImageSaver(store).save(mp,m)
    assert (tmp_path/"图片"/"1.jpg").exists()
    assert Path(m["raw_path"]).exists()
    assert json.loads(mp.read_text(encoding="utf-8"))["status"]=="published_counter_error"

def test_manual_recovery_keeps_incomplete_label_and_original(tmp_path):
    from boardcapture.recovery import recover
    mp,m=raw_image(tmp_path/"raw",status="incomplete")
    result=recover(mp,tmp_path/"recovered","BMP")
    assert result.name.startswith("incomplete_recovered_")
    assert Path(m["raw_path"]).exists()
    with Image.open(result) as im:assert im.size==(3,7)
    with pytest.raises(FileExistsError):recover(mp,tmp_path/"recovered","BMP")

def test_engine_emergency_does_not_save_normal_image(tmp_path):
    store=config(tmp_path);events=[]
    engine=Engine(store,lambda kind,data:events.append((kind,data)),tmp_path/"data")
    engine.start()
    try:
        engine.submit("simulation",enabled=True);engine.submit("plc_connect");engine.submit("camera_connect")
        wait_until(lambda:engine.camera is not None)
        engine.submit("arm");time.sleep(.1)
        engine.submit("sim_signal",capture_active=True)
        wait_until(lambda:engine.capture is not None)
        time.sleep(.05)
        engine.submit("sim_signal",emergency_ok=False)
        wait_until(lambda:engine.capture is None and not engine.auto)
        assert not [e for e in events if e[0]=="saved"]
        assert store.snapshot()["output"]["next_number"]==1
        metas=list((tmp_path/"data"/"spool").glob("*.json"))
        assert metas and json.loads(metas[0].read_text(encoding="utf-8"))["status"]=="incomplete"
    finally:
        engine.submit("shutdown");engine.join(5)

def wait_until(predicate,seconds=5):
    deadline=time.monotonic()+seconds
    while time.monotonic()<deadline:
        if predicate():return
        time.sleep(.01)
    raise AssertionError("wait timed out")

def test_engine_end_to_end_three_boards(tmp_path):
    store=config(tmp_path);events=[]
    engine=Engine(store,lambda kind,data:events.append((kind,data)),tmp_path/"data")
    engine.start()
    try:
        engine.submit("simulation",enabled=True);engine.submit("plc_connect");engine.submit("camera_connect")
        wait_until(lambda:engine.camera is not None and engine.plc is not None)
        engine.submit("arm");time.sleep(.1)
        for i in range(3):
            engine.submit("sim_signal",capture_active=True)
            wait_until(lambda:engine.capture is not None)
            time.sleep(.1)
            engine.submit("sim_signal",capture_active=False)
            wait_until(lambda:len([e for e in events if e[0]=="saved"])==i+1)
        assert sorted(p.name for p in (tmp_path/"图片").iterdir())==["1.jpg","2.jpg","3.jpg"]
        assert store.snapshot()["output"]["next_number"]==4
        assert not [e for e in events if e[0]=="error"]
    finally:
        engine.submit("shutdown");engine.join(5)
        assert not engine.is_alive()

def test_next_board_can_start_during_previous_counter_commit(tmp_path):
    from concurrent.futures import Future
    store=config(tmp_path)
    out=tmp_path/"图片";out.mkdir();(out/"1.jpg").write_bytes(b"already published")
    engine=Engine(store,lambda *args:None,tmp_path/"data")
    engine.simulation=True
    engine.connect_camera()
    pending=Future();engine.pending.append(pending)
    try:
        engine._start_capture()
        assert engine.capture is not None
        engine._finish_capture("test cleanup")
    finally:
        engine.camera.close()
        engine.encoder.shutdown()
        engine.archiver.shutdown()
