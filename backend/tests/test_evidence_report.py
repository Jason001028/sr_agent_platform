import json
import time
import pytest
from backend.tests.test_batch_quality import quality
from backend.services import batch_quality
from backend.services import evidence_store as ledger
from backend.services.evidence_report import generate_window
from backend.services.store import Store


def test_real_binding_publication(quality,monkeypatch):
    q=quality
    monkeypatch.setenv('SR_EVIDENCE_STORAGE_LIMIT_BYTES',str(1024**3))
    review=batch_quality.review_for_item(q.store,q.store.get_goal_item(q.goal['goal_id'],'item'))
    items=q.store.list_goal_items(q.goal['goal_id'])
    assert ledger.report_compute_readiness(q.goal,items,{})=='ready'
    rid=ledger.create_report(q.store,q.goal,items,[review],'ready')
    assert rid==ledger.create_report(q.store,q.goal,items,[review],'ready')
    task=ledger.claim(q.store)
    manifest=generate_window(q.store,task,0)
    assert [a['role'] for a in manifest['assets']]==['nosr','before','output']
    assert ledger.finish(q.store,task,state='ready',manifest={'windows':[manifest]})
    assert not ledger.finish(q.store,task,state='failed')
    data=ledger.page(q.store,rid)
    assert data['total']==1 and data['rows'][0]['evidence_state']=='ready'
    assert 'path' not in json.dumps(data)
    assert batch_quality.get_review(q.store,review['review_id'])['scene_decision']=='pending'


def test_lease_takeover_and_migration(tmp_path):
    store=Store(tmp_path/'test.db')
    try:
        goal={'goal_id':'g','session_id':None}
        binding={'regions':[{'candidate_id':str(i),'bbox':[0,0,0,0]} for i in range(65)],'output':{'width':10,'height':10}}
        rid=ledger.create_report(store,goal,[],[{'item_id':'i','review_id':'r','binding':binding}],'partial')
        assert [len(ledger.page(store,rid,o)['rows']) for o in (0,30,60)]==[30,30,5]
        task=ledger.claim(store,now=1)
        with store.worker_transaction() as db:
            db.execute("UPDATE repair_evidence_tasks SET next_wake_at=1000 WHERE state='queued'")
        newer=ledger.claim(store,now=302)
        assert newer['task_id']==task['task_id'] and newer['lease_token']!=task['lease_token']
        assert not ledger.finish(store,task,state='ready')
    finally:store.close()
    store=Store(tmp_path/'test.db')
    try:assert ledger.snapshot(store,rid)['coverage']['final_regions']==65
    finally:store.close()


@pytest.mark.parametrize('status,executor,expected',[
 ('SUBMITTING',{},'indeterminate'),('DETECTING',{},'computing'),
 ('AWAITING_MASK_REVIEW',{},'waiting_input'),('FAILED',{},'partial'),
 ('AWAITING_REVIEW',{'quarantine':True},'indeterminate')])
def test_readiness(status,executor,expected):
    assert ledger.report_compute_readiness({},[{'status':status}],executor)==expected
