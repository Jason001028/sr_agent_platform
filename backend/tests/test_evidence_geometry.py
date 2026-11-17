import copy
import pytest
from backend.services.evidence_geometry import half_open, plan_region, ratio_window


def test_one_pixel_and_stability():
    r={'candidate_id':'r','bbox':[0,0,0,0],'points':None}
    frozen=copy.deepcopy(r)
    a=plan_region('a',r,100,100)
    assert half_open(r['bbox'])==[0,0,1,1]
    assert a==plan_region('a',r,100,100)
    assert r==frozen
    assert a['row_id']!=plan_region('b',r,100,100)['row_id']
    assert a['coverage']=='full_context'


@pytest.mark.parametrize('bbox',[[99,99,99,99],[1,1,90,2],[-1,-1,5,5],[0,0,2999,2999]])
def test_edges(bbox):
    a=plan_region('a',{'candidate_id':'r','bbox':bbox},3000,3000)
    for w in a['windows']:
        x0,y0,x1,y1=w['window']
        assert 0<=x0<x1<=3000 and 0<=y0<y1<=3000
        assert max(x1-x0,y1-y0)<=2048


def test_mapping_refuses_unverified():
    with pytest.raises(ValueError,match='alignment_unverified'):
        ratio_window([0,0,100,100],(1000,1000),(333,333),verified=True)
    assert ratio_window([2,2,5,5],(100,100),(50,50),verified=True)==[1,1,3,3]
