import cv2
import numpy as np
import pytest
from boardcapture.boundary_regions import detect_regions
from boardcapture.vision import select_seams
from boardcapture.geometry import diagonal_intersection
from boardcapture.geometry import detect_board_top


def scene():
    im=np.full((2000,1024,3),(18,35,15),np.uint8)
    im[100:1900,200:800]=(60,130,190)
    return im


def test_tilted_thin_seam_survives_legacy_row_rejection():
    im=scene();mask=np.zeros(im.shape[:2],np.uint8)
    cv2.line(mask,(210,900),(790,960),1,2)
    _,legacy,_=select_seams(mask.astype(np.float32),min_row_coverage=.08)
    assert not legacy
    groups,truncated,_=detect_regions(mask>0,im)
    assert len(groups)==1 and not truncated and not groups[0]['paired_boundary']
    assert groups[0]['model_region'].sum()==mask.sum()


def boundary_sample(band=True,upper=True):
    im=scene();mask=np.zeros(im.shape[:2],np.uint8)
    poly=np.array([[210,900],[790,929],[790,1029],[210,1000]],np.int32)
    if band:cv2.fillConvexPoly(im,poly,(45,90,155))
    if upper:
        for x0,x1 in [(210,280),(410,620),(700,790)]:
            cv2.line(mask,(x0,round(900+(x0-210)*.05)),(x1,round(900+(x1-210)*.05)),1,2)
    cv2.line(mask,tuple(poly[3]),tuple(poly[2]),1,2)
    return im,mask,poly


def test_fragmented_edges_fill_only_an_image_supported_band():
    im,mask,quad=boundary_sample()
    groups,truncated,info=detect_regions(mask>0,im)
    assert len(groups)==1 and groups[0]['paired_boundary'] and not truncated
    assert diagonal_intersection(groups[0]['quad'])==pytest.approx(diagonal_intersection(quad),abs=2)
    assert groups[0]['region'].sum()>groups[0]['model_region'].sum()*10
    assert info['boundary_pair_checks'][0]['accepted']


def test_two_lines_without_distinct_band_are_not_filled():
    im,mask,_=boundary_sample(band=False)
    groups,truncated,_=detect_regions(mask>0,im)
    assert len(groups)==2 and not any(g['paired_boundary'] for g in groups)
    assert sum(g['region'].sum() for g in groups)==mask.sum()


def test_single_edge_cannot_invent_a_wide_seam():
    im,mask,_=boundary_sample(upper=False)
    groups,_,_=detect_regions(mask>0,im)
    assert len(groups)==1 and not groups[0]['paired_boundary']
    assert groups[0]['region'].sum()==mask.sum()


def test_pairing_happens_before_candidate_limit():
    im,mask,_=boundary_sample()
    cv2.line(mask,(210,400),(790,425),1,3)
    groups,truncated,_=detect_regions(mask>0,im,max_seams=2)
    assert len(groups)==2 and not truncated
    assert sum(g['paired_boundary'] for g in groups)==1


def test_distant_seams_cannot_be_paired():
    im=scene();mask=np.zeros(im.shape[:2],np.uint8)
    cv2.line(mask,(210,500),(790,525),1,3);cv2.line(mask,(210,1500),(790,1525),1,3)
    groups,_,_=detect_regions(mask>0,im)
    assert len(groups)==2 and not any(g['paired_boundary'] for g in groups)


def test_leaning_board_side_is_not_used_as_board_top():
    im=np.full((3000,1024,3),(18,35,15),np.uint8)
    quad=np.array([[420,120],[860,145],[700,2800],[260,2775]],np.int32)
    cv2.fillConvexPoly(im,quad,(60,130,190))
    top,_=detect_board_top(im,{'scale_x':1,'scale_y':1})
    assert top['valid']
    assert top['slope']*640+top['intercept_px']==pytest.approx(132.5,abs=3)
