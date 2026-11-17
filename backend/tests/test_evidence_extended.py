import json
import time
from concurrent.futures import ThreadPoolExecutor
import threading
import numpy as np
import pytest
import tifffile
from fastapi import FastAPI
from fastapi.testclient import TestClient
from backend.services import evidence_store as ledger
from backend.services.store import Store
from backend.services.evidence_alignment import pairing,aligned_window
from backend.api.evidence_reports import router


def report(store,count=65,regions=None):
    binding={'regions':regions if regions is not None else [{'candidate_id':str(i),'bbox':[0,0,0,0]} for i in range(count)],'output':{'width':32,'height':32}}
    return ledger.create_report(store,{'goal_id':'g'},[],[{'item_id':'i','review_id':'r','binding':binding}],'partial')


def test_filtered_cursor_and_unsupported_rows(tmp_path,monkeypatch):
    store=Store(tmp_path/'api.db');rid=report(store)
    try:
        first=ledger.page(store,rid);second=ledger.page(store,rid,cursor=first['next_cursor'])
        assert len(first['rows'])==30 and len(second['rows'])==30 and first['rows'][0]['row_id']!=second['rows'][0]['row_id']
        with store.worker_transaction() as db:
            db.execute("UPDATE repair_evidence_rows SET evidence_state='failed' WHERE report_id=? AND ordinal=1",(rid,));ledger.touch(db,rid)
        assert ledger.page(store,rid,evidence_state='failed')['total']==1
        with pytest.raises(ValueError,match='cursor_stale'):ledger.page(store,rid,cursor=first['next_cursor'])
        app=FastAPI();app.state.store=store;app.include_router(router)
        # Snapshot, pagination and export must never read source TIFFs.
        monkeypatch.setattr('backend.services.preview_jpg.probe_tiff',lambda *a:pytest.fail('source read on GET'))
        with TestClient(app) as client:
            assert client.get(f'/api/repair-evidence-reports/{rid}').status_code==200
            assert client.get(f'/api/repair-evidence-reports/{rid}/rows?limit=31').status_code==422
            assert client.get(f'/api/repair-evidence-reports/{rid}/rows?evidence_state=failed').json()['total']==1
            assert len(client.get(f'/api/repair-evidence-reports/{rid}/export').json()['rows'])==65
            assert client.get(f'/api/repair-evidence-reports/{rid}/rows?sort=DROP').status_code==422
        legacy=report(store,regions=[{}]);page=ledger.page(store,legacy)
        assert page['rows'][0]['evidence_state']=='unsupported'
        assert page['rows'][0]['error_code']=='unsupported_geometry'
    finally:store.close()


def test_two_workers_do_not_claim_same_task(tmp_path):
    path=tmp_path/'concurrent.db';seed=Store(path);rid=report(seed,count=1);seed.close()
    gate=threading.Barrier(2)
    def take():
        store=Store(path)
        try:
            store._db();gate.wait(timeout=5)
            return ledger.claim(store)
        finally:store.close()
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures=[pool.submit(take) for _ in range(2)];results=[f.result() for f in futures]
    assert sum(r is not None for r in results)==1


def geotags(scale,origin=(0,0),crs=32650):
    return [(33550,'d',3,(scale,scale,0),False),(33922,'d',6,(0,0,0,*origin,0),False),
            (34735,'H',12,(1,1,0,2,1025,0,1,1,3072,0,1,crs),False)]


def test_affine_noninteger_grid_and_pixel_centers(tmp_path):
    pan=tmp_path/'pan.tif';nosr=tmp_path/'nosr.tif'
    tifffile.imwrite(pan,np.zeros((30,30),np.float32),extratags=geotags(1))
    yy,xx=np.mgrid[:12,:12]
    tifffile.imwrite(nosr,(xx+2*yy).astype('float32'),extratags=geotags(2.5))
    matrix,source=pairing(pan,nosr,(30,30),(12,12));assert source=='geotiff_affine'
    result,valid,meta=aligned_window(nosr,[5,5,10,10],matrix,(12,12))
    py,px=np.mgrid[5:10,5:10]
    np.testing.assert_allclose(result,((px+.5)/2.5-.5)+2*((py+.5)/2.5-.5),atol=1e-5)
    assert valid.all() and meta['resampling']=='pixel_center_bilinear_valid_weight'
    tifffile.imwrite(nosr,np.zeros((12,12),np.float32),extratags=geotags(2.5,crs=32651))
    with pytest.raises(ValueError,match='alignment_unverified'):pairing(pan,nosr,(30,30),(12,12))


def test_manual_vectors_bound_to_saved_mask(tmp_path):
    from backend.services.mask import generate_mask,saved_geometry
    path=tmp_path/'mask.tif';points=[[1,1],[8,1],[8,8],[1,8]]
    generate_mask(32,32,[points],path,tmp_path/'mask.txt')
    geometry=saved_geometry(path)
    assert geometry['regions'][0]['points']==points
    assert geometry['regions'][0]['bbox']==[1,1,8,8]
    with path.open('ab') as f:f.write(b'replaced')
    with pytest.raises(ValueError,match='mask_geometry_stale'):saved_geometry(path)
