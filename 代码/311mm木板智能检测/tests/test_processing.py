import copy,json,socket,time
from pathlib import Path
import pytest
from PIL import Image
from boardcapture.config import ConfigStore,validate
from boardcapture.processing import Processing
from boardcapture.result_stream import Journal,TcpServer,JsonSocket

def wait(predicate,timeout=15):
    end=time.monotonic()+timeout
    while time.monotonic()<end:
        if predicate():return
        time.sleep(.03)
    raise AssertionError('timeout')

def settings(tmp_path):
    store=ConfigStore(tmp_path/'settings.json');cfg=store.snapshot()
    cfg['output']['directory']=str(tmp_path/'images');cfg['processing']['results_directory']=str(tmp_path/'online')
    cfg['vision']['enabled']=False;store.save(cfg);return store

def test_four_modes_unique_board_and_config_snapshot(tmp_path):
    store=settings(tmp_path);p=Processing(store,tmp_path/'data',lambda *x:None);p.journal=Journal(tmp_path/'events.sqlite')
    image=tmp_path/'1.jpg';Image.new('RGB',(64,128)).save(image)
    for seam,grade in [(False,False),(True,False),(False,True),(True,True)]:
        cfg=store.snapshot();cfg['vision']['enabled']=seam;cfg['processing']['auto_grade']=grade
        p.enqueue(image,cfg,False)
        cfg['processing']['material']='semu' # Must not mutate a queued board's settings.
        p._accept(p.incoming.get_nowait())
    jobs=p.journal.jobs('queued')
    assert [j['modules'] for j in jobs]==[[],['seam'],['grading'],['seam','grading']]
    assert len({j['board_id'] for j in jobs})==4
    assert all(j['processing']['material']=='hongxiang' for j in jobs)
    assert len(p.journal.after(0,20))==4
    # Reaccepting the same durable request does not create another board/job.
    p.journal.add_job(jobs[0]);assert len(p.journal.jobs('queued'))==4

def test_review_flags_null_distance_simulation_and_restart(tmp_path):
    store=settings(tmp_path);p=Processing(store,tmp_path/'data',lambda *x:None);p.journal=Journal(tmp_path/'events.sqlite')
    image=tmp_path/'1.jpg';Image.new('RGB',(32,32)).save(image)
    cfg=store.snapshot();cfg['vision']['enabled']=True;cfg['processing']['auto_grade']=True
    p.enqueue(image,cfg,True);p._accept(p.incoming.get_nowait());req=p.journal.jobs('queued')[0]
    p._complete(req,{'seam':{'status':'done','result':{'status':'review_required','warnings':['未标定'],
        'seams':[{'distance_from_board_top_mm':None,'coordinate_valid':True}]}},
        'grading':{'status':'done','result':{'confidence':.4,'grade':'浅色'}}})
    event=p.journal.after(1)[0]
    assert event['requires_review'] and 'simulation' in event['review_reasons'] and 'grading_low_confidence' in event['review_reasons']
    assert event['results']['seam']['result']['seams'][0]['distance_from_board_top_mm'] is None
    p.enqueue(image,cfg);p._accept(p.incoming.get_nowait());p.journal.hold_interrupted()
    assert len(p.journal.jobs('held'))==1
    p.journal.resume();assert p.journal.jobs('queued')[0]['resumed'] is True

def connect(server,client='consumer',**kwargs):
    sock=socket.create_connection(server.server_address,timeout=2);sock.settimeout(2);io=JsonSocket(sock)
    io.send(dict(type='subscribe',client_id=client,**kwargs));return sock,io,io.receive()

def test_tcp_ack_reconnect_fragmentation_and_dedup(tmp_path):
    j=Journal(tmp_path/'events.sqlite');s=TcpServer(j,'127.0.0.1',0)
    try:
        a=j.publish('board/one',{'type':'board.saved','board_id':'board'})
        assert j.publish('board/one',{'type':'board.saved'})['event_id']==a['event_id']
        b=j.publish('board/two',{'type':'board.completed','board_id':'board'})
        sock,io,hello=connect(s);assert hello['type']=='hello'
        assert io.receive()['seq']==a['seq']
        ack=(json.dumps({'type':'ack','seq':a['seq']})+'\n').encode()
        sock.sendall(ack[:7]);time.sleep(.02);sock.sendall(ack[7:])
        assert io.receive()['seq']==b['seq'];sock.close() # no ACK for b
        wait(lambda:not s.clients)
        sock,io,hello=connect(s)
        replay=io.receive();assert replay['event_id']==b['event_id']
        io.send({'type':'ack','seq':b['seq']});wait(lambda:j.cursor('consumer')==b['seq']);sock.close()
        wait(lambda:not s.clients)
        # An independent consumer receives its own complete history.
        sock,io,hello=connect(s,'second');assert io.receive()['seq']==a['seq'];sock.close()
    finally:s.stop()

def test_tcp_bad_token_cursor_and_port_fail_do_not_corrupt_events(tmp_path):
    j=Journal(tmp_path/'events.sqlite');s=TcpServer(j,'127.0.0.1',0,'0123456789abcdef')
    try:
        sock,io,response=connect(s);assert response['type']=='error';sock.close()
        sock,io,response=connect(s,token='0123456789abcdef',after_seq=99);assert response['type']=='error';sock.close()
        assert j.latest()==0
        with pytest.raises(OSError):TcpServer(j,*s.server_address)
    finally:s.stop()

def test_network_config_requires_token_for_lan(tmp_path):
    store=settings(tmp_path);cfg=store.snapshot();cfg['processing']['tcp_host']='0.0.0.0'
    with pytest.raises(ValueError):validate(cfg)
    cfg['processing']['tcp_token']='0123456789abcdef';validate(cfg)
    cfg['processing']['tcp_port']=65801
    with pytest.raises(ValueError):validate(cfg)

def test_mode_summary_updates_when_legacy_vision_checkbox_changes(tmp_path):
    store=settings(tmp_path);events=[];p=Processing(store,tmp_path/'data',lambda k,v:events.append((k,v)))
    p.journal=Journal(tmp_path/'events.sqlite');p._state()
    cfg=store.snapshot()['vision'];cfg['enabled']=True;store.save_vision(cfg);p._state()
    assert len(events)==2 and events[-1][1]['mode']==[True,False]


def test_actual_automatic_seam_and_grading_worker(tmp_path):
    store=settings(tmp_path);cfg=store.snapshot();cfg['vision']['enabled']=True;cfg['processing']['auto_grade']=True
    # Small image exercises the real exported models and the frozen-worker-compatible entrypoint.
    image=tmp_path/'1.jpg';Image.new('RGB',(1024,1024),(150,100,60)).save(image)
    events=[];p=Processing(store,tmp_path/'data',lambda k,v:events.append((k,v)));p.start()
    try:
        p.enqueue(image,cfg,True)
        wait(lambda:any(k=='processing_event' and v['type']=='board.completed' for k,v in events),90)
        result=next(v for k,v in events if k=='processing_event' and v['type']=='board.completed')
        assert result['results']['seam']['status']=='done',result
        assert result['results']['grading']['status']=='done',result
        assert result['simulation'] and result['requires_review']
        assert (Path(cfg['processing']['results_directory'])/result['board_id']/'board_result.json').is_file()
        types={v['type'] for k,v in events if k=='processing_event'}
        assert types=={'board.saved','seam.completed','grading.completed','board.completed'}
    finally:p.close()

def test_gui_mode_switch_updates_capture_settings_and_preserves_number(tmp_path):
    from PySide6.QtWidgets import QApplication
    from boardcapture.gui import Window
    app=QApplication.instance() or QApplication([])
    store=settings(tmp_path);w=Window(store,start_engine=False)
    try:
        panel=w.processing_panel
        for index,flags in enumerate([(False,False),(True,False),(False,True),(True,True)]):
            cfg=store.snapshot();cfg['output']['next_number']=873;store.save(cfg)
            panel.mode.setCurrentIndex(index);panel.apply_mode()
            c=store.snapshot()
            assert (c['vision']['enabled'],c['processing']['auto_grade'])==flags
            assert w.vision_panel.enabled.isChecked()==flags[0]
            assert c['output']['next_number']==873
            assert panel.material.isEnabled()==flags[1]
    finally:w.close();w.engine.encoder.shutdown();w.engine.archiver.shutdown()


def test_journal_creation_failure_is_reported_without_blocking_caller(tmp_path):
    store=settings(tmp_path);blocked=tmp_path/'blocked';blocked.write_text('not a directory')
    events=[];p=Processing(store,blocked,lambda k,v:events.append((k,v)))
    try:
        p.start()
        wait(lambda:any(k=='processing_error' for k,v in events))
        assert p.thread.is_alive() and p.journal is None
    finally:p.close()


def test_atomic_progress_update_survives_windows_reader_lock(tmp_path):
    import ctypes,os,threading
    if os.name!='nt':pytest.skip('Windows file sharing')
    from boardcapture.config import atomic_json
    path=tmp_path/'progress.json';atomic_json(path,{'status':'old'})
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.CreateFileW.argtypes=[ctypes.c_wchar_p,ctypes.c_uint32,ctypes.c_uint32,ctypes.c_void_p,ctypes.c_uint32,ctypes.c_uint32,ctypes.c_void_p]
    kernel.CreateFileW.restype=ctypes.c_void_p
    kernel.CloseHandle.argtypes=[ctypes.c_void_p]
    handle=kernel.CreateFileW(str(path),0x80000000,1,None,3,0,None)
    assert handle!=ctypes.c_void_p(-1).value
    timer=threading.Timer(.1,lambda:kernel.CloseHandle(handle));timer.start()
    try:atomic_json(path,{'status':'done'})
    finally:timer.join()
    assert json.loads(path.read_text(encoding='utf-8'))=={'status':'done'}
    assert not list(tmp_path.glob('*.tmp'))
