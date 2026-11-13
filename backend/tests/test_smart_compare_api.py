import json
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from backend.tests.test_smart_compare import pair


@pytest.fixture
def client(tmp_path, monkeypatch):
    from backend.tests import allowed_roots_env
    for key,value in {**allowed_roots_env(tmp_path),'SR_AGENT_DB':str(tmp_path/'api.db'),
                      'SR_LLM_MOCK':'1','SR_SLURM_FAKE':'1','SR_GOAL_WORKER':'0'}.items():monkeypatch.setenv(key,value)
    from backend.api.app import create_app
    with TestClient(create_app()) as result:yield result


def test_http_smart_grid_roi_and_compare(client,pair):
    _,body,resolve=pair
    with patch('backend.api.app._diff_source_path',side_effect=lambda sid,root:resolve(sid)):
        for extra in ({},{'compare':True},{'precision':'source-roi','roi':{'x':40,'y':40,'w':112,'h':112}}, {'precision':'source-tiled'}):
            response=client.post('/api/diff/analyze',json={**body,**extra})
            assert response.status_code==200,response.text
            result=response.json()
            assert result['method']=='smart' and result['regions']
            assert result['sourceIdentity']['referenceSide']=='B'


def test_http_parameter_and_size_guards(client,pair):
    _,body,resolve=pair
    with patch('backend.api.app._diff_source_path',side_effect=lambda sid,root:resolve(sid)):
        assert client.post('/api/diff/analyze',json={**body,'smartOptions':{'window':14}}).status_code==400
        assert client.post('/api/diff/analyze',json={**body,'image_a':{**body['image_a'],'sourceRevision':'changed'}}).status_code==400
        for options in ({'calibrationScale':True}, {'calibrationScale':{}}, {'window':4}):
            assert client.post('/api/diff/analyze',json={**body,'smartOptions':options}).status_code==400
        for extra in ({'precision':'unknown'}, {'compare':True,'precision':'source-roi'},
                      {'compare':True,'precision':'source-tiled'}):
            assert client.post('/api/diff/analyze',json={**body,**extra}).status_code==400
    assert client.post('/api/diff/analyze',json=[]).status_code==400
    assert client.post('/api/diff/analyze',content=b'x'*(48*1024*1024+1)).status_code==413


def test_http_legacy_contract_is_unchanged(client):
    import numpy as np
    from backend.services.image_diff import _b64_png
    image={'grayPng':_b64_png(np.arange(256,dtype=np.uint8).reshape(16,16)), 'sourceWidth':16,'sourceHeight':16}
    for method in ('absdiff','ssim'):
        response=client.post('/api/diff/analyze',json={'method':method,'image_a':image,'image_b':image})
        assert response.status_code==200,response.text
        assert response.json()['regions']==[]
        assert 'algorithmVersion' not in response.json()


def test_queue_timeout_is_explicit(client,pair):
    from unittest.mock import AsyncMock
    import asyncio
    _,body,_=pair
    with patch.object(client.app.state.heavy_work,'run',new=AsyncMock(side_effect=asyncio.TimeoutError)):
        response=client.post('/api/diff/analyze',json=body)
        assert response.status_code==504


def test_named_sibling_aliases_bind_the_same_source(client,pair):
    from backend.api.paths import scene_id_abs
    paths,_,_=pair
    # A metadata file makes this a production RC scene for /siblings.
    (paths[0].parent/(paths[0].parent.name+'_meta.xml')).write_text('<meta><SolarAzimuth/></meta>',encoding='utf8')
    sid=scene_id_abs(paths[0])
    response=client.get('/api/scenes/'+sid+'/siblings',params=[('tour','true'),('known',scene_id_abs(paths[1]))])
    assert response.status_code==200,response.text
    items=response.json()['tourItems']
    assert any(sid in i.get('aliases',[]) and i['kind']=='input' for i in items)
