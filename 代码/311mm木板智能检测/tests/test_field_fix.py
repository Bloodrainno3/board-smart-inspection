import json,time
import pytest
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QPalette,QColor
from PySide6.QtCore import Qt
from boardcapture.config import ConfigStore,defaults,validate
from boardcapture.engine import Engine,Trigger
from boardcapture.triggers import PhotoelectricTrigger
from boardcapture.gui import Window
from test_core import wait_until


def signals(front=False,rear=False,emergency=True,m020=False):
    return dict(emergency_ok=emergency,front_sensor=front,rear_sensor=rear,capture_active=m020,rear_sensor_seen=False)


def test_direct_photoelectric_m020_stuck_low():
    t=PhotoelectricTrigger(0)
    assert t.update(signals(),0,False,100) is None
    assert t.update(signals(True),1,False,100)=='start'
    assert t.update(signals(False),2,True,100) is None  # board in sensor gap
    assert t.update(signals(False),3,True,100) is None
    assert t.update(signals(False,True),4,True,100) is None
    assert t.update(signals(False,False),5,True,100) is None
    assert t.update(signals(),5.11,True,100)=='finish'
    assert t.update(signals(),6,False,100) is None
    assert t.update(signals(True),7,False,100)=='start'


def test_direct_requires_clear_area_before_first_board():
    t=PhotoelectricTrigger(0)
    assert t.update(signals(True,True),0,False,0) is None
    assert t.update(signals(True),1,False,0) is None
    assert not t.seen_low
    t.update(signals(),2,False,0)
    assert t.update(signals(True),3,False,0)=='start'


def test_direct_rear_bounce_and_emergency():
    t=PhotoelectricTrigger(0);t.update(signals(),0,False,100)
    t.update(signals(True),1,False,100);t.update(signals(True,True),2,True,100)
    t.update(signals(False,False),3,True,100)
    assert t.update(signals(False,True),3.05,True,100) is None
    assert t.deadline is None
    assert t.update(signals(False,True,False),3.06,True,100)=='emergency'
    assert not t.seen_low


def test_direct_debounce_ignores_short_front_pulse():
    t=PhotoelectricTrigger(20)
    t.update(signals(),0,False,0);t.update(signals(),.021,False,0)
    assert t.update(signals(True),1,False,0) is None
    assert t.update(signals(),1.01,False,0) is None
    assert t.update(signals(),1.04,False,0) is None
    assert t.update(signals(True),2,False,0) is None
    assert t.update(signals(True),2.021,False,0)=='start'


def test_invalid_trigger_never_silently_falls_back():
    cfg=defaults();cfg['trigger']['mode']='auto_guess'
    with pytest.raises(ValueError):validate(cfg)


@pytest.mark.parametrize('legacy_config',[False,True])
def test_default_and_legacy_config_use_original_plc_trigger(tmp_path,legacy_config):
    path=tmp_path/'settings.json'
    if legacy_config:
        cfg=defaults();cfg.pop('trigger')
        path.write_text(json.dumps(cfg),encoding='utf-8-sig')
    store=ConfigStore(path)
    assert store.snapshot()['trigger']['mode']=='plc_m020'
    engine=Engine(store,lambda *args:None,tmp_path/'data')
    try:assert isinstance(engine.trigger,Trigger)
    finally:engine.encoder.shutdown();engine.archiver.shutdown()


@pytest.mark.parametrize('trigger_mode',['photoelectric','plc_m020'])
def test_controller_three_complete_boards_and_diagnostics(tmp_path,trigger_mode):
    store=ConfigStore(tmp_path/'settings.json');cfg=store.snapshot()
    cfg['trigger']['mode']=trigger_mode
    cfg['output'].update(directory=str(tmp_path/'images'),minimum_free_gb=0)
    cfg['camera'].update(minimum_height=1,max_capture_bytes=4*1024*1024)
    cfg['vision']['enabled']=False;store.save(cfg)
    events=[];e=Engine(store,lambda k,v:events.append((k,v)),tmp_path/'data');e.start()
    try:
        e.submit('simulation',enabled=True);e.submit('plc_connect');e.submit('camera_connect')
        wait_until(lambda:e.camera and e.plc)
        e.submit('arm');wait_until(lambda:e.auto and e.trigger.seen_low)
        for i in range(3):
            e.submit('sim_signal',front_sensor=True,capture_active=trigger_mode=='plc_m020')
            wait_until(lambda:e.capture is not None)
            e.submit('sim_signal',front_sensor=False)
            time.sleep(.13)
            assert e.capture is not None
            e.submit('sim_signal',rear_sensor=True)
            time.sleep(.13)
            e.submit('sim_signal',rear_sensor=False,capture_active=False)
            wait_until(lambda:sum(k=='saved' for k,v in events)==i+1)
            wait_until(lambda:e.trigger.seen_low)
        e.submit('export_diagnostics');wait_until(lambda:any(k=='diagnostics_exported' for k,v in events))
        report=json.loads(next((tmp_path/'data'/'diagnostics').glob('*.json')).read_text(encoding='utf-8'))
        assert report['changes']['front_sensor']==6 and report['changes']['rear_sensor']==6
        if trigger_mode=='photoelectric':assert report['changes']['capture_active']==0
        assert len(report['signal_history'])>=10
        assert sorted(p.name for p in (tmp_path/'images').iterdir())==['1.jpg','2.jpg','3.jpg']
        assert not [v for k,v in events if k=='error']
        assert any('准备调用相机StartCapture' in v['message'] for k,v in events if k=='log')
    finally:e.submit('shutdown');e.join(8)


@pytest.mark.parametrize('plc,camera,auto,capturing',[
    (False,False,False,False),
    (True,True,False,False),
    (True,True,True,False),
    (True,True,True,True),
])
def test_windows_dark_palette_and_mouse_navigation_in_all_states(tmp_path,plc,camera,auto,capturing):
    app=QApplication.instance() or QApplication([])
    dark=QPalette();dark.setColor(QPalette.ColorRole.Window,QColor('#101010'));dark.setColor(QPalette.ColorRole.WindowText,QColor('white'))
    app.setPalette(dark)
    window=Window(ConfigStore(tmp_path/'settings.json'),start_engine=False)
    try:
        from PySide6.QtTest import QTest
        window.show();app.processEvents()
        assert app.style().objectName().lower()=='fusion'
        assert app.palette().color(QPalette.ColorRole.Window).lightness()>220
        state=dict(plc=plc,camera=camera,auto=auto,capture=capturing,saving=0,archives=0,simulation=False,lines=0)
        window.update_state(state)
        for index in range(3):
            QTest.mouseClick(window.settings_tabs.tabBar(),Qt.MouseButton.LeftButton,pos=window.settings_tabs.tabBar().tabRect(index).center())
            app.processEvents();assert window.settings_tabs.currentIndex()==index
        for index in (1,0,1):
            QTest.mouseClick(window.main_tabs.tabBar(),Qt.MouseButton.LeftButton,pos=window.main_tabs.tabBar().tabRect(index).center())
            app.processEvents();assert window.main_tabs.currentIndex()==index
        assert window.vision_panel.isEnabled()
        editable=not (plc or camera or auto or capturing)
        assert window.port.isEnabled()==editable and window.next_number.isEnabled()==editable
        window.on_event('plc',signals(True))
        assert 'ON' in window.signal_lamps['front_sensor'].text()
        assert '#dcf5e7' in window.signal_lamps['front_sensor'].styleSheet()
        window.update_state(dict(state,plc=False,camera=False,auto=False,capture=False))
        assert window.port.isEnabled() and window.trigger_mode.isEnabled()
    finally:window.close();window.engine.encoder.shutdown();window.engine.archiver.shutdown()
