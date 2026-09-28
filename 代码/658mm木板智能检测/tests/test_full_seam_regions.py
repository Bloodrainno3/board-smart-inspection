import cv2,numpy as np,pytest
from boardcapture.vision import Predictor
from boardcapture.geometry import detect_board_top


def scene():
    im=np.full((3000,1024,3),(18,35,15),np.uint8)
    im[120:2900,200:800]=(60,130,190)
    return im


def analyze(tmp_path,image,prob):
    path=tmp_path/'board.png';cv2.imencode('.png',image)[1].tofile(str(path))
    p=object.__new__(Predictor);p.manifest={'checkpoint_sha256':'test','onnx_sha256':'test'}
    p.probability=lambda image,progress:prob.copy()
    return p.analyze(path,tmp_path/'out',{'threshold':.5,'max_seams':2,'min_row_coverage':.08})


def test_dark_wide_seam_keeps_full_model_mask_instead_of_only_bottom(tmp_path):
    im=scene();im[1500:1660,205:795]=(20,25,30)
    prob=np.zeros(im.shape[:2],np.float32);prob[1500:1670,205:795]=.95
    r=analyze(tmp_path,im,prob)
    assert r['seam_count']==1
    s=r['seams'][0]
    assert s['center_px'][1]==pytest.approx(1584.5,abs=1)
    assert s['segmented_area_px']==590*170


def test_region_retains_connected_tip_below_row_coverage_threshold(tmp_path):
    im=scene();prob=np.zeros(im.shape[:2],np.float32)
    prob[1400:1520,480:540]=.9;prob[1500:1520,205:795]=.9
    r=analyze(tmp_path,im,prob)
    assert min(p[1] for p in r['seams'][0]['corners_px'])==pytest.approx(1400,abs=1)


def test_full_width_dark_gap_does_not_move_board_top_to_lower_piece(tmp_path):
    im=scene();im[900:1070,200:800]=(20,25,30)
    top,_=detect_board_top(im,{'scale_x':1,'scale_y':1})
    assert top['valid'] and top['intercept_px']==pytest.approx(120,abs=2)


def test_edge_only_model_is_not_claimed_as_full_area(tmp_path):
    im=scene();im[1500:1660,205:795]=(20,25,30)
    prob=np.zeros(im.shape[:2],np.float32);prob[1660:1670,205:795]=.95
    r=analyze(tmp_path,im,prob)
    assert r['seams'][0]['center_px'][1]==pytest.approx(1664.5,abs=1)
    assert r['seams'][0]['segmented_area_px']==5900
    raw=cv2.imdecode(np.fromfile(r['model_mask'],dtype=np.uint8),cv2.IMREAD_GRAYSCALE)
    assert not raw[1500:1660].any() and raw[1660:1670,205:795].all()


def test_complete_component_is_not_counted_twice(tmp_path):
    im=scene();prob=np.zeros(im.shape[:2],np.float32)
    prob[1400:1420,205:795]=.9;prob[1500:1520,205:795]=.9
    prob[1420:1500,480:540]=.9
    r=analyze(tmp_path,im,prob)
    assert r['seam_count']==1
    assert r['seams'][0]['center_px'][1]==pytest.approx(1459.5,abs=1)


def test_result_view_can_compare_raw_mask_and_full_region(tmp_path):
    from PySide6.QtWidgets import QApplication
    from boardcapture.config import ConfigStore
    from boardcapture.gui import Window
    im=scene();prob=np.zeros(im.shape[:2],np.float32);prob[1500:1670,205:795]=.95
    r=analyze(tmp_path,im,prob)
    app=QApplication.instance() or QApplication([])
    window=Window(ConfigStore(tmp_path/'settings.json'),start_engine=False)
    try:
        panel=window.vision_panel;panel.add_result(r)
        for i in range(4):
            panel.view_mode.setCurrentIndex(i);app.processEvents()
            assert not panel.view.sceneRect().isEmpty()
        assert panel.view_mode.currentData()=='source_preview'
        old=dict(r);old.pop('model_mask');old.pop('source_preview');old.pop('measurement_mask')
        panel.add_result(old);assert panel.view_mode.currentData()=='overlay'
    finally:window.close();window.engine.encoder.shutdown();window.engine.archiver.shutdown()
