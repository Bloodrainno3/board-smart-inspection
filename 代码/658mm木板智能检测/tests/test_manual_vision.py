import copy,json,time
from pathlib import Path
from PySide6.QtWidgets import QApplication
from boardcapture.config import ConfigStore,defaults,atomic_json
from boardcapture.gui import Window
from boardcapture.vision_jobs import VisionQueue

def test_default_and_old_config_migrate_once(tmp_path):
    cfg=defaults();assert cfg['vision']['enabled'] is False
    cfg['vision'].pop('manual_window_v1');cfg['vision']['enabled']=True
    cfg['serial']['port']='COM12';cfg['output']['next_number']=317
    path=tmp_path/'settings.json';atomic_json(path,cfg)
    store=ConfigStore(path)
    assert store.snapshot()['vision']['enabled'] is False
    assert store.snapshot()['serial']['port']=='COM12'
    assert store.snapshot()['output']['next_number']==317
    v=store.snapshot()['vision'];v['enabled']=True;store.save_vision(v)
    assert ConfigStore(path).snapshot()['vision']['enabled'] is True

def test_independent_window_select_does_not_run_until_clicked(tmp_path):
    app=QApplication.instance() or QApplication([])
    w=Window(ConfigStore(tmp_path/'settings.json'),start_engine=False)
    calls=[];w.engine.vision.enqueue=lambda path,settings:calls.append((path,settings))
    try:
        assert not w.vision_window.isVisible()
        w.open_vision_window();app.processEvents()
        assert w.vision_window.isWindow() and w.vision_window.isVisible()
        panel=w.vision_panel
        panel.select_files([tmp_path/'1.jpg',tmp_path/'2.jpg'])
        assert calls==[] and not w.store.snapshot()['vision']['enabled']
        panel.start_selected_button.click()
        assert len(calls)==2 and not panel.start_selected_button.isEnabled()
        panel.start_selected_button.click();assert len(calls)==2
        w.vision_window.close();app.processEvents()
        assert not w.vision_window.isVisible()
        w.open_vision_window();assert w.vision_panel is panel
    finally:
        w.close();w.engine.encoder.shutdown();w.engine.archiver.shutdown()

def test_restart_pending_jobs_wait_for_explicit_resume(tmp_path,monkeypatch):
    q=VisionQueue(tmp_path,lambda *_:None)
    for name,status in [('one','running'),('two','queued')]:
        atomic_json(q.root/name/'state.json',{'status':status})
    # Observe scheduling without launching a model process.
    monkeypatch.setattr(q,'run',lambda:q.stop_event.wait(5))
    q.start()
    try:
        jobs,counts=q.snapshot();assert jobs==[] and counts['held']==2
        q.retry_errors()
        jobs,counts=q.snapshot();assert len(jobs)==2 and counts['held']==0
    finally:q.close()

def test_saved_board_only_auto_enqueues_when_enabled(tmp_path):
    from boardcapture.engine import Engine
    store=ConfigStore(tmp_path/'settings.json');events=[]
    e=Engine(store,lambda *x:events.append(x),tmp_path/'data')
    try:
        calls=[];e.processing.enqueue=lambda *x:calls.append(x)
        # Invoke the actual image-saved callback, keeping capture hardware out of this test.
        e.enqueue_recognition(str(tmp_path/'1.jpg'))
        assert len(calls)==1 and not calls[0][1]['vision']['enabled']
        v=store.snapshot()['vision'];v['enabled']=True;store.save_vision(v)
        e.enqueue_recognition(str(tmp_path/'2.jpg'))
        assert len(calls)==2 and calls[1][1]['vision']['enabled']
    finally:e.encoder.shutdown();e.archiver.shutdown()
