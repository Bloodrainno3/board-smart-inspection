"""Regression based on the user's photographed ladder, via real RTU framing."""
import struct,time
import pytest
from boardcapture.config import ConfigStore,defaults
from boardcapture.plc import PLC,packet,ModbusError
from boardcapture.plc_control import CommandPulse,START,STOP
from boardcapture.engine import Engine
from boardcapture.camera import SimCamera
from test_core import wait_until


class LadderTransport:
    def __init__(self,ack='good'):
        self.x={0:True,1:False,2:False}
        self.m={10:False,20:False,21:False,300:False,400:False}
        self.last_x1=self.last_m300=False
        self.requests=[];self.response=b'';self.closed=False;self.ack=ack

    def scan(self):
        x,m=self.x,self.m
        if x[0] and not m[400] and m[300] and not self.last_m300:m[10]=True
        if m[400] or not x[0]:m[10]=False
        if m[10] and x[1] and not self.last_x1:m[20]=True
        if m[20] and x[2]:m[21]=True
        if m[21] and not x[2]:m[20]=m[21]=False
        if not m[10] or not x[0]:m[20]=m[21]=False
        self.last_x1=x[1];self.last_m300=m[300]

    def inputs(self,**values):
        for key,value in values.items():self.x[int(key[1:])]=value
        self.scan()

    def reset_input_buffer(self):self.response=b''
    def flush(self):pass
    def close(self):self.closed=True
    def read(self,n):
        n=min(n,2);out,self.response=self.response[:n],self.response[n:];return out
    def write(self,request):
        assert packet(request[:-2])==request
        self.requests.append(request)
        slave,fn,addr,value=struct.unpack('>BBHH',request[:-2])
        self.scan()
        if fn==5:
            assert addr in (300,400) and value in (0,0xff00)
            self.m[addr]=bool(value);self.scan();self.response=request
            if value and self.ack!='good':
                mode=self.ack;self.ack='good'
                if mode=='missing':self.response=b''
                if mode=='crc':self.response=request[:-1]+bytes([request[-1]^255])
                if mode=='echo':self.response=packet(struct.pack('>BBHH',slave,5,addr+1,value))
                if mode=='exception':self.response=packet(bytes([slave,0x85,2]))
        else:
            assert fn in (1,2)
            data=bytearray((value+7)//8)
            for i in range(value):
                bit=self.m.get(addr+i,False) if fn==1 else self.x.get(addr+i-13312,False)
                if bit:data[i//8]|=1<<(i%8)
            self.response=packet(bytes([slave,fn,len(data)])+data)
        return len(request)


def controller(tmp_path,transport,camera=None,camera_config=None):
    store=ConfigStore(tmp_path/'settings.json');cfg=store.snapshot()
    cfg['serial']['timeout_s']=.05
    cfg['output'].update(directory=str(tmp_path/'images'),minimum_free_gb=0)
    cfg['vision']['enabled']=False
    if camera_config:cfg['camera']=camera_config
    else:cfg['camera'].update(minimum_height=1,max_capture_bytes=4*1024*1024)
    store.save(cfg)
    if camera is None:camera=SimCamera();camera.connect(cfg['camera'])
    events=[];engine=Engine(store,lambda k,v:events.append((k,v)),tmp_path/'data')
    engine.plc=PLC(cfg,transport=transport);engine.camera=camera
    return engine,events


def test_ladder_requires_m300_m10_before_x1_can_set_m20():
    t=LadderTransport();plc=PLC(defaults(),transport=t)
    t.inputs(x1=True);assert not plc.read_status()['capture_active']
    assert not any(r[1]==5 for r in t.requests)  # read/connect never confirms run
    t.inputs(x1=False);plc.write_command(START,True);plc.write_command(START,False)
    assert plc.read_status()['conveyor_running']
    t.inputs(x1=True);assert plc.read_status()['capture_active']
    t.inputs(x1=False,x2=True);assert plc.read_status()['rear_sensor_seen']
    t.inputs(x2=False);assert not plc.read_status()['capture_active']
    plc.write_command(STOP,True);plc.write_command(STOP,False)
    assert not plc.read_status()['conveyor_running']


@pytest.mark.parametrize('mode',['crc','echo','exception','missing'])
def test_write_command_requires_valid_plc_ack(mode):
    with pytest.raises(ModbusError):PLC(defaults(),transport=LadderTransport(mode)).write_command(START,True)


@pytest.mark.parametrize('key',['capture_active','rear_sensor_seen','conveyor_running','emergency_ok'])
def test_never_force_internal_ladder_or_input_bits(key):
    t=LadderTransport()
    with pytest.raises(ValueError):PLC(defaults(),transport=t).write_command(key,True)
    assert not t.requests


def test_command_feedback_timeout_and_clear():
    writes=[];pulse=CommandPulse(lambda k,v:writes.append((k,v)),START,defaults()['control'],0)
    status=dict(emergency_ok=True,conveyor_stop_command=False,front_sensor=False,rear_sensor=False,capture_active=False,conveyor_running=False)
    pulse.tick(status,.06);pulse.tick(status,.4)
    with pytest.raises(RuntimeError,match='M010'):pulse.tick(status,2.5)
    pulse.cancel();assert writes==[(START,False),(START,True),(START,False),(START,False)]


def test_start_rejects_x000_low_without_writes(tmp_path):
    t=LadderTransport();t.inputs(x0=False);e,events=controller(tmp_path,t);e.start()
    try:
        e.submit('arm');wait_until(lambda:any(k=='error' for k,v in events))
        assert not e.auto and not any(r[1]==5 for r in t.requests)
        assert 'X000' in next(v for k,v in events if k=='error')
    finally:e.submit('shutdown');e.join(8)


def test_missing_start_ack_clears_command_and_never_retries_on(tmp_path):
    t=LadderTransport('missing');e,events=controller(tmp_path,t);e.start()
    try:
        e.submit('arm');wait_until(lambda:any(k=='error' for k,v in events))
        time.sleep(.1)
        writes=[struct.unpack('>BBHH',r[:-2])[2:] for r in t.requests if r[1]==5]
        assert writes==[(300,0),(300,0xff00),(300,0)]
        assert not e.auto and not t.m[300]
    finally:e.submit('shutdown');e.join(8)


def test_stop_preempts_start_pulse_and_resets_m10(tmp_path):
    t=LadderTransport();e,events=controller(tmp_path,t);e.start()
    try:
        e.submit('arm');wait_until(lambda:t.m[300])
        e.submit('disarm')
        wait_until(lambda:not e.auto and e.plc_pulse is None and not t.m[10])
        assert not t.m[300] and not t.m[400]
        assert not [v for k,v in events if k=='error']
    finally:e.submit('shutdown');e.join(8)


def test_shutdown_clears_outstanding_start_pulse(tmp_path):
    t=LadderTransport();e,events=controller(tmp_path,t);e.start()
    try:
        e.submit('arm');wait_until(lambda:t.m[300])
    finally:e.submit('shutdown');e.join(8)
    assert not t.m[300] and t.closed and not e.is_alive()


def test_old_config_adds_run_confirmation_without_changing_serial_or_counter(tmp_path):
    from boardcapture.config import atomic_json
    cfg=defaults();cfg.pop('control')
    for key in ('conveyor_running',START,STOP):cfg['devices'].pop(key)
    cfg['serial']['port']='COM11';cfg['serial']['parity']='E';cfg['output']['next_number']=123
    path=tmp_path/'settings.json';atomic_json(path,cfg)
    result=ConfigStore(path).snapshot()
    assert result['control']['confirm_run_on_arm']
    assert result['devices'][START]['address']==300 and result['devices']['conveyor_running']['address']==10
    assert result['serial']==cfg['serial'] and result['output']['next_number']==123


def test_opening_permission_switch_cancels_high_start_pulse(tmp_path):
    t=LadderTransport();e,events=controller(tmp_path,t);e.start()
    try:
        e.submit('arm');wait_until(lambda:t.m[300])
        t.inputs(x0=False)
        wait_until(lambda:any(k=='error' for k,v in events))
        assert not t.m[300] and not t.m[10] and not e.auto
    finally:e.submit('shutdown');e.join(8)


def test_run_confirmation_opt_out_only_reads(tmp_path):
    t=LadderTransport();e,events=controller(tmp_path,t)
    cfg=e.store.snapshot();cfg['control']['confirm_run_on_arm']=False;e.store.save(cfg)
    e.start()
    try:
        e.submit('arm');wait_until(lambda:e.auto and e.trigger.seen_low)
        e.submit('disarm');wait_until(lambda:not e.auto)
        assert not any(r[1]==5 for r in t.requests)
    finally:e.submit('shutdown');e.join(8)


def test_external_stop_does_not_leave_auto_waiting_forever(tmp_path):
    t=LadderTransport();e,events=controller(tmp_path,t);e.start()
    try:
        e.submit('arm');wait_until(lambda:e.auto and t.m[10] and e.plc_pulse is None)
        t.m[400]=True;t.scan()
        wait_until(lambda:not e.auto)
        assert any(k=='error' and 'M010' in v for k,v in events)
    finally:e.submit('shutdown');e.join(8)
