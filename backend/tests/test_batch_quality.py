import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import uuid

import pytest

from backend.tests import allowed_roots_env
from backend.tests import test_pan_repair as repair_fixtures
from backend.services import batch_manifest, batch_mask, batch_quality, batch_worker, goal_worker
from backend.services.store import Store
from backend.services.goal_lease import GoalLease, current_lease


@pytest.fixture
def quality(tmp_path,monkeypatch):
    # Real TIFF, actual repair publication and deployed contract verifier, with
    # only GDAL/inference replaced by the existing bounded synthetic fixture.
    case=repair_fixtures.TestPanRepair(methodName='runTest');case.setUp()
    for key,value in {**allowed_roots_env(Path(tempfile.gettempdir())), 'SR_BATCH_ARTIFACT_DIR':tempfile.gettempdir()}.items():monkeypatch.setenv(key,value)
    monkeypatch.setenv('SR_AGENT_DB',str(tmp_path/'state.db'))
    target=batch_manifest._image(case.pan); reference=batch_manifest._image(case.nosr)
    case.run_repair()
    store=Store(tmp_path/'state.db')
    task=store.put_sr_task('fingerprint',case.params,status='COMPLETED',job_id=42,config_xml=str(case.config))
    cp={'phase':'VERIFYING','attempt':1,'target_image':target,'reference_image':reference,'scene_ref':str(case.scene),
        'mask':{'path':str(case.mask),'sha256':batch_mask._sha256(case.mask),'width':8,'height':8},
        'detection':{'provenance':'real','candidate_accounting':{'unprocessed':0},'candidates':[
            {'candidate_id':'r1','kind':'bbox','bbox':[3,3,3,3],'points':None}]},
        'sr_fingerprint':'fingerprint','job_id':42,'job_status':{'state':'COMPLETED','active':False,'exit_code':'0'}}
    goal=store.create_batch_goal(title='human',request_key='key',content_sha256='content',
        checkpoint={'mode':'production','workflow':batch_manifest.WORKFLOW},items=[
            {'item_id':'item','ordinal':1,'name':'scene','status':'PENDING','checkpoint':cp}])
    claimed=store.claim_goal(goal['goal_id'],'worker',lease_sec=600)
    lease=GoalLease(goal['goal_id'],claimed['lease_token'],claimed['version'])
    item=store.get_goal_item(goal['goal_id'],'item')
    item=store.transition_goal_item(goal['goal_id'],step_id=item['step_id'],expected_version=item['version'],status='VERIFYING',
        lease_token=lease.token,checkpoint=cp,external_job_id=42,capacity_reserved=1)
    state=SimpleNamespace(store=store)
    def verify():
        token=current_lease.set(lease)
        try:return batch_worker._verify_item(state,store.get_goal(goal['goal_id']),store.get_goal_item(goal['goal_id'],'item'),lease,{},'production')
        finally:current_lease.reset(token)
    verify()
    q=SimpleNamespace(store=store,goal=goal,case=case,lease=lease,verify=verify,state=state,cp=cp)
    try:yield q
    finally:store.close();case.doCleanups()


def review(q):return batch_quality.review_for_item(q.store,q.store.get_goal_item(q.goal['goal_id'],'item'))
def decision(q,action='region',value='approved',**kwargs):
    r=review(q)
    return batch_quality.decide(q.store,q.goal['goal_id'],'item',{
        'review_id':r['review_id'],'expected_version':r['version'],'request_key':uuid.uuid4().hex,
        'action':action,'decision':value,'region_id':'r1','reviewer':'测试审核人','reason':'人工检查',**kwargs})


def test_completed_sr_enters_review_releases_capacity_and_cannot_complete(quality):
    q=quality;i=q.store.get_goal_item(q.goal['goal_id'],'item')
    assert i['status']=='AWAITING_REVIEW' and i['capacity_reserved']==0
    assert review(q)['regions'][0]['decision']=='pending'
    assert not goal_worker.verify_goal(q.state,q.store.get_goal(q.goal['goal_id']))['ok']
    with pytest.raises(ValueError,match='human quality'):
        q.store.complete_verified_goal(q.goal['goal_id'],{'ok':True})


def test_all_regions_then_explicit_scene_confirmation_and_contract_recheck(quality):
    q=quality
    with pytest.raises(batch_quality.ReviewConflict,match='所有必要区域'):decision(q,'scene')
    decision(q)
    assert not batch_quality.quality_layer(q.store,q.store.get_goal_item(q.goal['goal_id'],'item'))['status']=='passed'
    decision(q,'scene')
    assert q.store.get_goal_item(q.goal['goal_id'],'item')['status']=='VERIFYING'
    q.verify()
    assert q.store.get_goal_item(q.goal['goal_id'],'item')['status']=='SUCCEEDED'
    assert goal_worker.verify_goal(q.state,q.store.get_goal(q.goal['goal_id']))['ok']
    token=current_lease.set(q.lease)
    try:
        assert batch_worker._settle(q.state,q.store.get_goal(q.goal['goal_id']),q.lease,{},'production')
    finally:current_lease.reset(token)
    assert q.store.get_goal(q.goal['goal_id'])['status']=='COMPLETED'


@pytest.mark.parametrize('which',['pan','nosr','mask','config'])
def test_replaced_files_invalidate_old_approvals(quality,which):
    q=quality;decision(q);decision(q,'scene')
    path=getattr(q.case,which)
    data=path.read_bytes();replacement=path.with_suffix('.replacement');replacement.write_bytes(data);os.replace(replacement,path)
    with pytest.raises((batch_quality.ReviewConflict,OSError)):review(q)
    assert batch_quality.quality_layer(q.store,q.store.get_goal_item(q.goal['goal_id'],'item'))['status']=='failed'
    assert not goal_worker.verify_goal(q.state,q.store.get_goal(q.goal['goal_id']))['ok']


def test_rejection_pending_idempotency_conflicts_and_persistence(quality):
    q=quality
    with pytest.raises(ValueError,match='原因'):decision(q,value='rejected',reason='')
    r=decision(q,value='rejected',request_key='one')
    assert r['regions'][0]['decision']=='rejected'
    # Same request key+payload returns the durable answer despite old version.
    same=decision(q,value='rejected',request_key='one',expected_version=0)
    assert same['version']==r['version']
    with pytest.raises(batch_quality.ReviewConflict):decision(q,value='pending',request_key='one')
    with pytest.raises(batch_quality.ReviewConflict):decision(q,expected_version=0)
    decision(q,value='pending');decision(q,value='rejected');decision(q,'scene','rejected')
    assert q.store.get_goal_item(q.goal['goal_id'],'item')['status']=='REVIEW_REJECTED'
    q.store.close_thread()
    assert review(q)['scene_decision']=='rejected'
    assert q.store._db().execute('SELECT COUNT(*) FROM batch_quality_events').fetchone()[0]==4


def test_job_identity_not_inherited(quality):
    q=quality;decision(q);decision(q,'scene')
    q.store._db().execute('UPDATE agent_goal_steps SET external_job_id=43 WHERE goal_id=?',(q.goal['goal_id'],));q.store._db().commit()
    with pytest.raises(batch_quality.ReviewConflict,match='Job'):review(q)


def test_changed_frozen_input_cannot_reuse_old_review(quality):
    q=quality;decision(q)
    item=q.store.get_goal_item(q.goal['goal_id'],'item');cp=item['checkpoint']
    cp['target_image']={**cp['target_image'],'image_key':'a-different-input'}
    q.store._db().execute('UPDATE agent_goal_steps SET checkpoint=? WHERE step_id=?',(json.dumps(cp),item['step_id']));q.store._db().commit()
    with pytest.raises(batch_quality.ReviewConflict,match='引用已改变'):review(q)


def test_quality_api_list_detail_audit_and_replaced_output(quality):
    from fastapi.testclient import TestClient
    from backend.api.app import create_app
    q=quality;app=create_app();app.state.store=q.store;client=TestClient(app)
    base=f'/api/goals/{q.goal["goal_id"]}'
    listing=client.get(base+'/quality-reviews')
    assert listing.status_code==200 and listing.json()['reviews'][0]['counts']['pending']==1
    detail=client.get(base+'/items/item/quality-review');assert detail.status_code==200
    d=detail.json();assert len(d['images'])==2 and d['images'][0]['tourIdentity']['role']=='pan'
    body={'review_id':d['review_id'],'expected_version':0,'request_key':'api1','action':'region','decision':'approved',
          'region_id':'r1','reviewer':'人工审核','reason':'纹理检查通过'}
    assert client.post(base+'/items/item/quality-review',json=body).status_code==200
    assert client.post(base+'/items/item/quality-review',json=body).status_code==200
    conflict={**body,'request_key':'api2','decision':'rejected'}
    assert client.post(base+'/items/item/quality-review',json=conflict).status_code==409
    assert len(client.get(base+'/items/item/quality-review').json()['audit'])==1
    old=q.case.pan;replacement=old.with_suffix('.replacement');replacement.write_bytes(old.read_bytes());os.replace(replacement,old)
    assert client.get(base+'/items/item/quality-review').status_code==409
    listing=client.get(base+'/quality-reviews').json()
    assert not listing['reviews'][0]['current']


def test_concurrent_review_cas_has_one_winner_and_one_audit_event(quality):
    from concurrent.futures import ThreadPoolExecutor
    q=quality;r=review(q)
    def submit(n):
        body={'review_id':r['review_id'],'expected_version':0,'request_key':f'parallel-{n}',
              'action':'region','decision':'approved' if n==0 else 'rejected','region_id':'r1','reviewer':'人工','reason':'并发检查'}
        try:batch_quality.decide(q.store,q.goal['goal_id'],'item',body);return 'ok'
        except batch_quality.ReviewConflict:return 'conflict'
        finally:q.store.close_thread()
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(submit,range(2)))
    assert sorted(results)==['conflict','ok']
    assert review(q)['version']==1
    assert q.store._db().execute('SELECT COUNT(*) FROM batch_quality_events').fetchone()[0]==1


def test_waiting_quality_does_not_stop_other_batch_items(monkeypatch):
    from backend.tests import test_batch_worker as fixtures
    case=fixtures.BatchWorkerTest(methodName='runTest');case.setUp()
    try:
        goal,_=case.make_goal(3)
        original=batch_worker._verify_item
        def verify(state,g,item,lease,cfg,mode):
            if item['ordinal']==1:
                cp={**item['checkpoint'],'phase':'AWAITING_REVIEW'}
                return state.store.transition_goal_item(g['goal_id'],step_id=item['step_id'],expected_version=item['version'],
                    status='AWAITING_REVIEW',lease_token=lease.token,checkpoint=cp,capacity_reserved=0,next_wake_at=None)
            return original(state,g,item,lease,cfg,mode)
        monkeypatch.setattr(batch_worker,'_verify_item',verify)
        case.run_until(lambda:[i['status'] for i in case.items(goal['goal_id'])]==['AWAITING_REVIEW','SUCCEEDED','SUCCEEDED'])
        assert len(case.submits)==3 and case.store.get_goal(goal['goal_id'])['status']!='COMPLETED'
        assert case.store.count_reserved_slots(goal['goal_id'])==0
    finally:case.tearDown()
