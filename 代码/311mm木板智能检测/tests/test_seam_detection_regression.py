"""Regression: uncertain wood colour must not veto a neural seam prediction."""
import cv2
import numpy as np
import pytest
from boardcapture.vision import Predictor
from boardcapture.geometry import detect_board_top


def analyze(tmp_path, image, prob, **settings):
    source = tmp_path / 'wide.png'
    cv2.imencode('.png', image)[1].tofile(str(source))
    predictor = object.__new__(Predictor)
    predictor.manifest = {'checkpoint_sha256': 'test', 'onnx_sha256': 'test'}
    predictor.probability = lambda image, progress: prob.copy()
    return predictor.analyze(source, tmp_path / 'out',
                             {'threshold': .5, 'min_row_coverage': .08, 'max_seams': 2, **settings})


def disconnected_board():
    image = np.full((3000, 1024, 3), (18, 35, 15), np.uint8)
    image[120:900, 200:800] = (60, 130, 190)
    image[1400:2900, 200:800] = (60, 130, 190)
    return image


def test_wide_gap_survives_when_colour_footprint_excludes_it(tmp_path):
    image = disconnected_board()
    prob = np.zeros(image.shape[:2], np.float32)
    prob[950:1390, 205:795] = .95
    _, footprint = detect_board_top(image, {'scale_x': 1, 'scale_y': 1})
    assert not footprint[950:1390].any()  # The R4 pre-filter removed ALL these pixels.
    result = analyze(tmp_path, image, prob)
    assert result['seam_count'] == 1
    seam = result['seams'][0]
    assert seam['center_px'][1] == pytest.approx(1169.5, abs=1)
    assert seam['segmented_area_px'] == 440 * 590
    assert not seam['coordinate_valid']  # Board-top estimate is below the seam.
    assert seam['distance_from_board_top_px'] is None
    assert seam['distance_from_board_top_mm'] is None
    assert seam['board_footprint_conflict']
    assert result['detection_diagnostics']['colour_filter_applied'] is False


def test_partial_footprint_cannot_move_center_to_bottom_edge(tmp_path):
    image = disconnected_board()
    prob = np.zeros(image.shape[:2], np.float32)
    prob[950:1410, 205:795] = .95
    result = analyze(tmp_path, image, prob)
    assert result['seam_count'] == 1
    assert result['seams'][0]['center_px'][1] == pytest.approx(1179.5, abs=1)
    assert result['seams'][0]['segmented_area_px'] == 460 * 590


@pytest.mark.parametrize('kind,stage', [('empty','no_model_pixels'),
    ('edge','edge_margin_only'),('narrow','horizontal_support_filter')])
def test_no_detection_explains_the_stage(tmp_path,kind,stage):
    image=disconnected_board();prob=np.zeros(image.shape[:2],np.float32)
    if kind=='edge':prob[5:20,205:795]=.95
    if kind=='narrow':prob[1500:1600,480:520]=.95
    result=analyze(tmp_path,image,prob)
    assert result['seam_count']==0 and result['status']=='review_required'
    assert result['detection_diagnostics']['stage']==stage
    assert result['detection_diagnostics']['explanation'] in result['warnings']


def test_missing_board_top_does_not_hide_model_seam(tmp_path):
    image=np.full((3000,1024,3),80,np.uint8)
    prob=np.zeros(image.shape[:2],np.float32);prob[950:1300,205:795]=.95
    result=analyze(tmp_path,image,prob)
    assert result['seam_count']==1 and not result['board_top']['valid']
    assert result['seams'][0]['distance_from_board_top_px'] is None


def test_sample_export_preserves_original_and_can_be_relocated(tmp_path):
    import zipfile,json,hashlib
    from boardcapture.result_io import export_problem_sample,load_result_file
    result=analyze(tmp_path,disconnected_board(),np.zeros((3000,1024),np.float32))
    archive=tmp_path/'问题样本.zip'
    export_problem_sample(result,archive)
    with zipfile.ZipFile(archive) as z:
        assert z.testzip() is None
        assert z.read('original.png')==(tmp_path/'wide.png').read_bytes()
        manifest=json.loads(z.read('files_sha256.json'))
        assert all(hashlib.sha256(z.read(name)).hexdigest()==digest for name,digest in manifest.items())
        z.extractall(tmp_path/'relocated')
    loaded=load_result_file(tmp_path/'relocated/result.json')
    assert loaded['source']==str(tmp_path/'relocated/original.png')
    assert loaded['model_mask']==str(tmp_path/'relocated/model_mask_1024.png')
    assert loaded['detection_diagnostics']==result['detection_diagnostics']
    assert not list(tmp_path.glob('*.part'))


def test_sample_export_missing_original_does_not_publish_zip(tmp_path):
    from boardcapture.result_io import export_problem_sample
    with pytest.raises(ValueError,match='找不到'):
        export_problem_sample({'source':str(tmp_path/'absent.jpg')},tmp_path/'sample.zip')
    assert not (tmp_path/'sample.zip').exists()


def test_no_detection_ui_is_not_a_no_seam_judgement(tmp_path):
    from PySide6.QtWidgets import QApplication
    from boardcapture.config import ConfigStore
    from boardcapture.gui import Window
    result=analyze(tmp_path,disconnected_board(),np.zeros((3000,1024),np.float32))
    app=QApplication.instance() or QApplication([])
    window=Window(ConfigStore(tmp_path/'settings.json'),start_engine=False)
    try:
        panel=window.vision_panel;panel.add_result(result);app.processEvents()
        assert '未检出' in panel.table.item(0,0).text()
        assert '无缝' not in panel.table.item(0,0).text()
        assert '模型预测 0 像素' in panel.details.text()
        panel.view_mode.setCurrentIndex(1)
        assert not panel.view.sceneRect().isEmpty()
    finally:
        window.close();window.engine.encoder.shutdown();window.engine.archiver.shutdown()
