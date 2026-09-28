import ctypes as C
import json
from pathlib import Path
import time
import pytest
from boardcapture.cl_config import SDK, Control, Property, LIGHTS
from boardcapture.config import ConfigStore
from boardcapture.camera import SimCamera
from boardcapture.cl_preview import Preview
from boardcapture.engine import Engine


@pytest.fixture
def sdk():
    sdk = SDK(Path(__file__).parent / 'native_cl')
    sdk.dll.fake_setup_cl.argtypes = [C.c_int, C.c_int, C.c_int]
    sdk.dll.fake_setup_cl.restype = None
    sdk.dll.fake_cl_writes.argtypes = []
    sdk.dll.fake_cl_writes.restype = C.c_int
    sdk.dll.fake_setup_cl(0, 0, 0)
    yield sdk
    sdk.close()


@pytest.fixture
def control(sdk, tmp_path):
    events = []
    c = Control(lambda k, v: events.append((k, v)), tmp_path, lambda: sdk)
    c.connect(sdk.ports()[0], 'COM7')
    yield c, events
    c.close()


def test_native_abi_float_read_and_no_writes_on_connect(control, sdk):
    c, _ = control
    assert c.snapshot['values']['Gain'] == 1.25
    assert c.snapshot['values']['LightValueG'] == 18
    assert sdk.dll.fake_cl_writes() == 0


def test_plc_port_collision_prevents_open(sdk, tmp_path):
    c = Control(lambda *x: None, tmp_path, lambda: sdk)
    with pytest.raises(ValueError, match='PLC'):
        c.connect(sdk.ports()[0], 'COM011')
    assert not c.connected and sdk.handle is None


def test_write_requires_matching_readback_and_preserves_audit(control, sdk, tmp_path):
    c, events = control
    c.perform('apply', {'changes': {'Gain': 1.375, 'LightValueB': 21}})
    assert c.snapshot['values']['Gain'] == 1.375
    sdk.dll.fake_setup_cl(0, 1, 0)
    with pytest.raises(RuntimeError, match='回读未匹配'):
        c.perform('apply', {'changes': {'LightValueR': 35}})
    assert c.snapshot['values']['LightValueR'] == 36
    reports = [json.loads(p.read_text(encoding='utf-8')) for p in (tmp_path/'camera_calibration').glob('*.json')]
    assert {r['ok'] for r in reports} == {True, False}
    assert any(r.get('sdk_commands') for r in reports)


def test_partial_write_failure_reports_actual_values(control, sdk):
    c, _ = control
    sdk.dll.fake_setup_cl(int(Property.LightValueG), 0, 0)
    with pytest.raises(RuntimeError, match='injected'):
        c.perform('apply', {'changes': {'LightValueR': 25, 'LightValueG': 28, 'LightValueB': 27}})
    assert c.snapshot['values']['LightValueR'] == 25
    assert c.snapshot['values']['LightValueG'] == 18
    assert c.snapshot['values']['LightValueB'] == 17


@pytest.mark.parametrize('changes', [{'Gain': float('nan')}, {'LightValueR': -1},
    {'LightValueB': 10001}, {'Offset': 1.5}, {'TriggerMode': 0}, {'LightValueR': True}])
def test_invalid_settings_never_write(control, sdk, changes):
    c, _ = control
    with pytest.raises(ValueError):
        c.apply(changes)
    assert sdk.dll.fake_cl_writes() == 0


def test_native_write_allowlist_even_without_python_validation(control, sdk):
    for prop, value in [(Property.TriggerMode, 0), (Property.Gain, float('inf')),
                        (Property.UserSetDefault, 9), (Property.LightValueR, 10001)]:
        with pytest.raises(RuntimeError, match='not allowed'):
            sdk.set(prop.name, value)
    assert sdk.dll.fake_cl_writes() == 0


def test_read_failure_marks_old_snapshot_stale(control, sdk):
    c, events = control
    sdk.dll.fake_setup_cl(0, 0, 1)
    with pytest.raises(RuntimeError):
        c.read()
    assert c.last_read_time == 0
    assert any(k == 'cl_stale' for k, _ in events)


def test_dark_bright_sequence_and_store_ack_not_persistence(control, sdk):
    c, _ = control
    with pytest.raises(RuntimeError, match='停用'):
        c.perform('generate', {'kind': 2})
    c.perform('apply', {'changes': {'FFC_Enabled': 0}})
    with pytest.raises(RuntimeError, match='关闭RGB'):
        c.perform('generate', {'kind': 2})
    original = {k: c.snapshot['values'][k] for k in LIGHTS}
    c.perform('dark_lights', {})
    c.perform('dark_lights', {})  # repeated off does not lose the original light values
    assert all(c.snapshot['values'][k] == 0 for k in LIGHTS)
    assert '未提供完成标志' in c.perform('generate', {'kind': 2})
    with pytest.raises(RuntimeError, match='恢复RGB'):
        c.perform('generate', {'kind': 1})
    c.perform('restore_lights', {})
    assert {k: c.snapshot['values'][k] for k in LIGHTS} == original
    c.perform('generate', {'kind': 1})
    c.perform('apply', {'changes': {'FFC_Enabled': 1}})
    assert '仍需相机断电重启' in c.perform('save', {'slot': 2})
    assert c.snapshot['values']['UserSetDefault'] == 2


class FakeCamera(SimCamera):
    def start_preview(self):
        self.start()


@pytest.fixture
def engine(control, tmp_path):
    c, events = control
    store = ConfigStore(tmp_path/'settings.json')
    e = Engine(store, lambda k, v: events.append((k, v)), tmp_path/'data')
    e.cl_control = c
    e.camera = FakeCamera(); e.camera.connect({})
    yield e, events
    if e.cl_preview:
        e._stop_cl_preview()
    e.encoder.shutdown(); e.archiver.shutdown()


def wait_ready(preview):
    deadline = time.monotonic() + 3
    while not preview.ready() and time.monotonic() < deadline:
        time.sleep(.01)
    assert preview.ready()


def test_tuning_during_production_is_rejected_without_aborting_board(engine, sdk):
    e, events = engine
    e.auto = True
    e._command('cl_apply', {'changes': {'LightValueR': 10}})
    assert e.auto and sdk.dll.fake_cl_writes() == 0
    assert any(k == 'cl_done' and not v['ok'] for k, v in events)


def test_calibration_requires_live_frames_and_blocks_capture(engine, sdk, tmp_path):
    e, events = engine
    e._command('cl_generate', {'kind': 2})
    assert sdk.dll.fake_cl_writes() == 0
    before = e.store.snapshot()['output']['next_number']
    e._command('cl_preview_start', {})
    wait_ready(e.cl_preview)
    with pytest.raises(RuntimeError, match='停止校正预览'):
        e._command('arm', {})
    with pytest.raises(RuntimeError, match='停止校正预览'):
        e._start_capture()
    e._command('cl_save_preview', {})
    e._command('cl_preview_stop', {})
    assert e.cl_preview is None and not e.camera.working()
    assert e.store.snapshot()['output']['next_number'] == before
    assert len(list((tmp_path/'data'/'camera_calibration').glob('*.png'))) == 1
    assert not any(k == 'saved' for k, _ in events)


def test_no_frames_never_enables_calibration_or_silent_success(engine, monkeypatch):
    e, events = engine
    monkeypatch.setattr(e.camera, 'pop', lambda: None)
    e._command('cl_preview_start', {})
    assert not e.cl_preview.ready()
    e._command('cl_generate', {'kind': 2})
    assert events[-2][0] == 'cl_done' or any(k == 'cl_done' and not v['ok'] for k, v in events)
    e.cl_preview.error = 'no image timeout'
    e._tick()
    assert e.cl_preview is None


def test_preview_keeps_rgb_bytes_for_diagnostic_png(tmp_path):
    from PIL import Image
    cam = FakeCamera(); cam.connect({})
    preview = Preview(cam, lambda *x: None)
    try:
        preview.start(); wait_ready(preview)
        deadline = time.monotonic() + 2
        while preview.latest is None and time.monotonic() < deadline:
            time.sleep(.01)
        path = tmp_path/'strip.png'; preview.save(path)
        with Image.open(path) as im:
            assert im.getpixel((0, 0)) == (95, 70, 35)
            assert im.width == 320 and im.height <= 1024
    finally:
        preview.stop()


def test_gui_controls_and_live_preview_state(tmp_path):
    from PySide6.QtWidgets import QApplication
    from boardcapture.cl_config_gui import CameraCalibrationPanel
    app = QApplication.instance() or QApplication([])
    calls = []
    p = CameraCalibrationPanel(lambda *a, **kw: calls.append((a, kw)))
    try:
        s = {'cl_connected': True, 'camera': True, 'cl_preview': True}
        p.update_state(s)
        p.handle_event('cl_snapshot', {'values': {'FFC_Enabled': 0}, 'properties': [], 'read_at': 'test'})
        p.dark_ready.setChecked(True)
        assert not p.buttons['dark'].isEnabled()
        p.preview_received = True; p.refresh()
        assert p.buttons['dark'].isEnabled()
        p.update_state(dict(s, auto=True))
        assert not p.buttons['dark'].isEnabled() and not p.buttons['read'].isEnabled()
        p.update_state(s)
        p.handle_event('cl_stale', None)
        assert not p.buttons['ffc_on'].isEnabled() and p.buttons['read'].isEnabled()
    finally:
        p.close()
