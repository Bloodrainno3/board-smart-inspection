import json
import time
from pathlib import Path
import cv2
import numpy as np
import pytest
from boardcapture.geometry import diagonal_intersection,order_quad,detect_board_top,calibrate,validate_profile
from boardcapture.vision import Predictor,normalize,tile_starts,select_seams
from boardcapture.vision_jobs import VisionQueue
from boardcapture.config import ConfigStore,defaults,atomic_json


def test_non_parallelogram_diagonals_not_vertex_mean():
    quad=[[0,0],[10,0],[8,6],[2,6]]
    assert diagonal_intersection(quad)==pytest.approx([5,3.75])
    assert diagonal_intersection(quad)!=np.mean(quad,axis=0).tolist()
    with pytest.raises(ValueError):diagonal_intersection([[0,0],[1,0],[2,0],[3,0]])


def test_thin_sloping_quad_not_crossed():
    pts=np.array([[10,50],[910,10],[910,14],[10,54]],np.float32)
    for shuffled in (pts,pts[[2,0,3,1]]):
        q=order_quad(shuffled)
        assert q.tolist()==pts.tolist()
        assert diagonal_intersection(q)==pytest.approx([460,32])


def test_normalization_rgb_and_overlap_last_tile():
    x=normalize(np.array([[[10,20,30]]],np.uint8))
    assert x[0,:,0,0]==pytest.approx((np.array([30,20,10])/255-[.485,.456,.406])/[.229,.224,.225])
    starts=tile_starts(2500)
    assert starts==[0,768,1476]
    covered=np.zeros(2500,bool)
    for y in starts:covered[y:y+1024]=True
    assert covered.all()


def test_board_top_and_missing_leading_background():
    im=np.zeros((3000,1024,3),np.uint8);im[:]=[18,35,15]
    im[120:2900,200:800]=[60,130,190]
    top,_=detect_board_top(im,{'scale_x':3,'scale_y':4})
    assert top['valid']
    assert top['intercept_px']==pytest.approx(480,abs=5)
    im[:120,200:800]=[60,130,190]
    top,_=detect_board_top(im,{'scale_x':3,'scale_y':4})
    assert not top['valid']


def test_uncalibrated_never_returns_mm_and_board_origin(tmp_path):
    image=np.zeros((3000,1024,3),np.uint8);image[:]=[18,35,15];image[120:2900,200:800]=[60,130,190]
    path=tmp_path/'板图.jpg';cv2.imencode('.jpg',image)[1].tofile(str(path))
    predictor=object.__new__(Predictor);predictor.manifest={'checkpoint_sha256':'test','onnx_sha256':'test'}
    prob=np.zeros((3000,1024),np.float32);prob[1500:1520,205:795]=.95
    predictor.probability=lambda image,progress:prob.copy()
    settings={'threshold':.5,'max_seams':2,'min_row_coverage':.08}
    result=predictor.analyze(path,tmp_path/'out',settings)
    s=result['seams'][0]
    assert s['distance_from_board_top_mm'] is None
    assert s['distance_from_board_top_px']==pytest.approx(s['center_px'][1]-120,abs=3)
    profile={'id':'test','image_width':1024,'mm_per_pixel_x':.1,'mm_per_pixel_y':.2}
    r=predictor.analyze(path,tmp_path/'out2',settings,profile)
    assert r['seams'][0]['distance_from_board_top_mm']==pytest.approx(r['seams'][0]['distance_from_board_top_px']*.2)
    assert not validate_profile(profile,3672)[0]
    assert sorted(p.name for p in tmp_path.glob('*.jpg'))==['板图.jpg']


def test_multiple_candidates_flag_review():
    prob=np.zeros((3000,1024),np.float32)
    for y in (500,1500,2500):prob[y:y+20,100:900]=.9
    _,groups,truncated=select_seams(prob)
    assert len(groups)==2 and truncated


def test_a4_and_measured_square_calibration(tmp_path):
    im=np.zeros((2000,1200,3),np.uint8);im[:]=[15,35,10]
    im[200:1388,180:1020]=255
    for row in range(7):
        for col in range(7):
            if (row+col)%2==0:im[450+row*80:450+(row+1)*80,320+col*80:320+(col+1)*80]=0
    path=tmp_path/'标定.jpg';cv2.imencode('.jpg',im)[1].tofile(str(path))
    profile,_,_=calibrate(path)
    assert profile['mm_per_pixel_x']==pytest.approx(.25,abs=.001)
    assert profile['mm_per_pixel_y']==pytest.approx(.25,abs=.001)
    assert len(profile['checker_corners_px'])==36 and not profile['warnings']
    measured,_,_=calibrate(path,square_mm=10)
    assert measured['mm_per_pixel_y']==pytest.approx(.125,abs=.001)
    assert measured['method']=='measured_checker_square'


def test_save_vision_preserves_image_number(tmp_path):
    store=ConfigStore(tmp_path/'config.json')
    v=store.snapshot()['vision']
    cfg=store.snapshot();cfg['output']['next_number']=999;store.save(cfg)
    v['threshold']=.6;store.save_vision(v)
    assert store.snapshot()['output']['next_number']==999
    v['results_directory']=store.snapshot()['output']['directory']
    with pytest.raises(ValueError):store.save_vision(v)


def test_worker_error_durable_and_does_not_fault_capture(tmp_path):
    events=[]
    q=VisionQueue(tmp_path,lambda k,d:events.append((k,d)))
    v=defaults()['vision'];v['model_path']=str(tmp_path/'missing.onnx');v['results_directory']=str(tmp_path/'results')
    q.enqueue(tmp_path/'missing.jpg',v)
    deadline=time.monotonic()+15
    try:
        while time.monotonic()<deadline and not any(k=='vision_error' for k,_ in events):time.sleep(.05)
        states=[json.loads(p.read_text()) for p in q.root.glob('*/state.json')]
        assert states and states[0]['status']=='error'
        assert any(k=='vision_error' for k,_ in events)
        assert not any(k=='error' for k,_ in events)
    finally:q.close()
