"""Actual field threshold masks; no new network inference or original-JPG claim."""
from pathlib import Path
import cv2
import numpy as np
import pytest
from boardcapture.boundary_regions import detect_regions
from boardcapture.geometry import diagonal_intersection,detect_board_top

FIXTURES=Path(__file__).parent/'fixtures/field_wide_20260918'


@pytest.mark.parametrize('board,wide_interval',[(1,(4495,4515)),(2,(2345,2365)),
                                               (3,(4515,4535)),(4,(2334,2354))])
def test_field_wide_band_and_narrow_seam(board,wide_interval):
    image=cv2.imdecode(np.fromfile(FIXTURES/f'board_{board}.jpg',np.uint8),1)
    raw=cv2.imdecode(np.fromfile(FIXTURES/f'model_{board}.png',np.uint8),0)>0
    groups,truncated,info=detect_regions(raw,image)
    assert len(groups)==2 and not truncated
    paired=[g for g in groups if g['paired_boundary']]
    assert len(paired)==1
    g=paired[0];center=diagonal_intersection(g['quad'])
    # Coarse visually checked intervals on the supplied 1024-wide previews.
    assert wide_interval[0]<center[1]<wide_interval[1]
    assert 95<np.mean(g['quad'][[2,3],1])-np.mean(g['quad'][[0,1],1])<115
    assert g['quad'][:,0].max()-g['quad'][:,0].min()>425
    assert g['region'].sum()>g['model_region'].sum()*10
    top,_=detect_board_top(image,{'scale_x':1,'scale_y':1})
    assert top['valid'] and abs(top['slope'])<.15 and top['fit_rms_px']<4
    assert 0<top['slope']*center[0]+top['intercept_px']<150
