import asyncio
import json
import time
from backend.tests.test_batch_quality import quality
from backend.services import batch_quality,evidence_store as ledger
from backend.services.evidence_report import generate_window
from backend.services.evidence_analysis import analyze
import pytest


def setup_report(q,monkeypatch):
    monkeypatch.setenv('SR_LLM_MOCK','0');monkeypatch.setenv('SR_EVIDENCE_STORAGE_LIMIT_BYTES',str(1024**3))
    review=batch_quality.review_for_item(q.store,q.store.get_goal_item(q.goal['goal_id'],'item'))
    rid=ledger.create_report(q.store,q.goal,q.store.list_goal_items(q.goal['goal_id']),[review],'ready')
    task=ledger.claim(q.store);manifest=generate_window(q.store,task,0)
    ledger.finish(q.store,task,state='ready',manifest={'windows':[manifest]})
    return rid,task['row_id']


@pytest.mark.parametrize('stage',['evidence','analysis'])
def test_discovery_resumes_same_source_after_temporary_supersession(quality,monkeypatch,stage):
    from backend.services.evidence_worker import discover
    q=quality;monkeypatch.setenv('SR_LLM_MOCK','0');monkeypatch.setenv('SR_EVIDENCE_STORAGE_LIMIT_BYTES',str(1024**3))
    discover(q.store,(0,''))
    rid=q.store._db().execute('SELECT report_id FROM repair_evidence_reports WHERE goal_id=?',(q.goal['goal_id'],)).fetchone()[0]
    first=ledger.claim(q.store)
    if stage=='analysis':
        manifest=generate_window(q.store,first,0)
        assert ledger.finish(q.store,first,state='ready',manifest={'windows':[manifest]})
        ledger.queue_analysis(q.store,rid,first['row_id']);first=ledger.claim(q.store)
    current=q.store.get_goal_item(q.goal['goal_id'],'item')['status']
    with q.store.worker_transaction() as db:
        db.execute("UPDATE agent_goal_steps SET status='DETECTING' WHERE goal_id=? AND item_id='item'",(q.goal['goal_id'],))
    discover(q.store,(0,''))
    assert ledger.snapshot(q.store,rid)['status']=='superseded'
    assert not ledger.renew(q.store,first) and ledger.claim(q.store) is None
    with q.store.worker_transaction() as db:
        db.execute("UPDATE agent_goal_steps SET status=? WHERE goal_id=? AND item_id='item'",(current,q.goal['goal_id']))
    discover(q.store,(0,''))
    assert q.store._db().execute('SELECT COUNT(*) FROM repair_evidence_reports WHERE goal_id=?',(q.goal['goal_id'],)).fetchone()[0]==1
    assert ledger.snapshot(q.store,rid)['status']!='superseded'
    second=ledger.claim(q.store)
    assert second['task_id']==first['task_id'] and second['lease_token']!=first['lease_token']
    assert not ledger.finish(q.store,first,state='failed')
    if stage=='evidence':
        manifest=generate_window(q.store,second,0)
        assert ledger.finish(q.store,second,state='ready',manifest={'windows':[manifest]})
    else:
        async def transport(*args,**kwargs):return {'content':json.dumps({'assessment':'indeterminate',
            'evidence_ids':['context:output'],'observation':'纹理有限','recommendation':'人工核对','limitations':'仅此窗口'})}
        asyncio.run(analyze(q.store,rid,second['row_id'],task=second,transport=transport))
        assert ledger.page(q.store,rid)['rows'][0]['analysis_state']=='ready'


def test_returning_to_older_fingerprint_is_the_current_goal_report(tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from backend.api.evidence_reports import router
    from backend.services.store import Store
    store=Store(tmp_path/'reports.db')
    goal={'goal_id':'g'}
    binding={'output':{'width':32,'height':32},'regions':[{'candidate_id':'one','bbox':[0,0,1,1]}]}
    a=[{'item_id':'i','review_id':'a','binding':binding}];b=[{'item_id':'i','review_id':'b','binding':binding}]
    try:
        first=ledger.create_report(store,goal,[],a,'partial')
        newer=ledger.create_report(store,goal,[],b,'partial')
        assert ledger.snapshot(store,first)['status']=='superseded'
        assert ledger.create_report(store,goal,[],a,'partial')==first
        assert ledger.snapshot(store,newer)['status']=='superseded'
        assert ledger.snapshot(store,first)['revision']==1
        app=FastAPI();app.state.store=store;app.include_router(router)
        with TestClient(app) as client:
            assert client.get('/api/repair-evidence-reports/by-goal/g').json()['report']['report_id']==first
    finally:store.close()


def test_two_analysis_rows_share_a_model_lease_across_store_instances(quality,monkeypatch):
    import shutil
    from backend.services.store import Store
    from backend.services.evidence_report import root
    from backend.services.evidence_analysis import AnalysisDeferred
    q=quality;rid,row=setup_report(q,monkeypatch);other='other-row'
    with q.store.worker_transaction() as db:
        db.execute("""INSERT INTO repair_evidence_rows(report_id,row_id,ordinal,item_id,review_id,region_id,plan_json,sources_json,evidence_state,analysis_state,manifest_json,version)
            SELECT report_id,?,ordinal+1,item_id,review_id,region_id,plan_json,sources_json,evidence_state,analysis_state,manifest_json,version FROM repair_evidence_rows WHERE report_id=? AND row_id=?""",(other,rid,row))
    shutil.copytree(root()/rid/row,root()/rid/other)
    second=Store(q.store.path)
    async def scenario():
        entered=asyncio.Event();finish=asyncio.Event();calls=[]
        async def transport(*args,**kwargs):
            calls.append(1);entered.set();await finish.wait()
            return {'content':json.dumps({'assessment':'indeterminate','evidence_ids':['context:output'],
                'observation':'纹理有限','recommendation':'人工核对','limitations':'仅此窗口'})}
        first=asyncio.create_task(analyze(q.store,rid,row,transport=transport));await entered.wait()
        try:
            with pytest.raises(AnalysisDeferred):await analyze(second,rid,other,transport=transport)
            assert len(calls)==1
        finally:finish.set();await first
        await analyze(second,rid,other,transport=transport)
        assert len(calls)==2
    try:asyncio.run(scenario())
    finally:second.close()


def test_chat_in_another_instance_blocks_evidence_dispatch(quality,monkeypatch):
    import threading
    from backend.agent import async_transport
    from backend.agent.loop import _default_chat
    from backend.config import load_config
    from backend.services.store import Store
    from backend.services.evidence_analysis import AnalysisDeferred
    q=quality;rid,row=setup_report(q,monkeypatch);second=Store(q.store.path)
    entered=threading.Event();release=threading.Event();calls=[]
    async def network(*args,**kwargs):
        entered.set()
        while not release.is_set():await asyncio.sleep(.01)
        return {'content':'chat done','tool_calls':[]}
    monkeypatch.setattr(async_transport,'_request',network)
    async def forbidden(*args,**kwargs):calls.append(1);raise AssertionError('evidence reached shared model')
    async def scenario():
        chat=asyncio.create_task(asyncio.to_thread(_default_chat(load_config(),store=second),[],[]))
        try:
            assert await asyncio.to_thread(entered.wait,5)
            with pytest.raises(AnalysisDeferred):await analyze(q.store,rid,row,transport=forbidden)
            assert not calls
        finally:release.set();await chat
        assert q.store._db().execute('SELECT COUNT(*) FROM model_resource_leases').fetchone()[0]==0
    try:asyncio.run(scenario())
    finally:second.close()


def test_model_resource_contention_defers_the_worker_instead_of_failing(quality,monkeypatch):
    from types import SimpleNamespace
    from backend.config import load_config
    from backend.services import evidence_worker,model_resources
    from backend.services.store import Store
    q=quality;rid,row=setup_report(q,monkeypatch);second=Store(q.store.path)
    monkeypatch.setenv('SR_EVIDENCE_ANALYSIS_ENABLED','1')
    monkeypatch.setattr(evidence_worker,'admission',lambda *args,**kwargs:True)
    ledger.queue_analysis(q.store,rid,row);task=ledger.claim(q.store)
    key=model_resources.resource_key(load_config().llm_base_url)
    try:
        assert model_resources.acquire(second,key,'another-chat','chat',time.time()+60)
        asyncio.run(evidence_worker.run_task(SimpleNamespace(store=q.store),task))
        state,error=q.store._db().execute('SELECT state,error_json FROM repair_evidence_tasks WHERE task_id=?',(task['task_id'],)).fetchone()
        assert state=='queued' and json.loads(error)['code']=='waiting_model_idle'
        assert ledger.page(q.store,rid)['rows'][0]['analysis_state']=='queued'
        assert q.store._db().execute('SELECT COUNT(*) FROM repair_evidence_analysis_attempts WHERE report_id=?',(rid,)).fetchone()[0]==0
    finally:second.close()


def test_late_model_result_cannot_overwrite_new_row(quality,monkeypatch):
    q=quality;rid,row=setup_report(q,monkeypatch)
    ledger.queue_analysis(q.store,rid,row);task=ledger.claim(q.store)
    async def transport(*args,**kwargs):
        with q.store.worker_transaction() as db:
            db.execute("UPDATE repair_evidence_rows SET version=version+1,analysis_state='queued' WHERE report_id=? AND row_id=?",(rid,row))
        return {'content':json.dumps({'assessment':'indeterminate','evidence_ids':['context:output'],'observation':'纹理有限','recommendation':'人工核对','limitations':'仅此窗口'})}
    with pytest.raises(ValueError,match='obsolete_task'):asyncio.run(analyze(q.store,rid,row,transport=transport,task=task))
    assert q.store._db().execute('SELECT analysis_state FROM repair_evidence_rows WHERE report_id=? AND row_id=?',(rid,row)).fetchone()[0]=='queued'
    assert q.store._db().execute("SELECT COUNT(*) FROM repair_evidence_analyses WHERE report_id=?",(rid,)).fetchone()[0]==0


def test_failed_analysis_history_and_format_retry(quality,monkeypatch):
    q=quality;rid,row=setup_report(q,monkeypatch);calls=[]
    async def invalid(*args,**kwargs):calls.append(1);return {'content':'invalid JSON'}
    with pytest.raises(ValueError):asyncio.run(analyze(q.store,rid,row,transport=invalid))
    assert len(calls)==2
    assert q.store._db().execute('SELECT status FROM repair_evidence_analysis_attempts WHERE report_id=?',(rid,)).fetchone()[0]=='failed'
    assert ledger.page(q.store,rid)['rows'][0]['evidence_state']=='ready'


def test_corrupt_cached_asset_is_regenerated(quality,monkeypatch):
    q=quality;rid,row=setup_report(q,monkeypatch)
    from backend.services.evidence_report import root
    asset=root()/rid/row/'context'/'output.png';asset.write_bytes(b'broken')
    with q.store.worker_transaction() as db:
        version=db.execute('SELECT version FROM repair_evidence_rows WHERE report_id=? AND row_id=?',(rid,row)).fetchone()[0]
        db.execute("INSERT INTO repair_evidence_tasks(task_id,report_id,row_id,fingerprint,stage,state,expected_row_version) VALUES('retry',?,?,?,'evidence','queued',?)",(rid,row,'retry',version))
    task=ledger.claim(q.store);manifest=generate_window(q.store,task,0)
    assert asset.read_bytes().startswith(b'\x89PNG')
    assert all(a['sha256'] for a in manifest['assets'])


def test_cached_analysis_retry_restores_ready_without_a_model_call(quality,monkeypatch):
    q=quality;rid,row=setup_report(q,monkeypatch)
    calls=[]
    async def transport(*args,**kwargs):
        calls.append(1)
        return {'content':json.dumps({'assessment':'indeterminate','evidence_ids':['context:output'],
            'observation':'纹理有限','recommendation':'人工核对','limitations':'仅此窗口'})}
    asyncio.run(analyze(q.store,rid,row,transport=transport))
    with q.store.worker_transaction() as db:
        db.execute("UPDATE repair_evidence_rows SET analysis_state='not_requested' WHERE report_id=? AND row_id=?",(rid,row))
    ledger.queue_analysis(q.store,rid,row);task=ledger.claim(q.store)
    asyncio.run(analyze(q.store,rid,row,transport=transport,task=task))
    assert calls==[1]
    assert ledger.page(q.store,rid)['rows'][0]['analysis_state']=='ready'
    assert q.store._db().execute("SELECT COUNT(*) FROM repair_evidence_analyses WHERE report_id=?",(rid,)).fetchone()[0]==1
    assert q.store._db().execute("SELECT COUNT(*) FROM repair_evidence_analysis_attempts WHERE report_id=? AND status='cached'",(rid,)).fetchone()[0]==1


def test_model_format_retry_yields_to_foreground_and_remains_queued(quality,monkeypatch):
    from backend.services.evidence_analysis import AnalysisDeferred
    q=quality;rid,row=setup_report(q,monkeypatch)
    ledger.queue_analysis(q.store,rid,row);task=ledger.claim(q.store)
    calls=[]
    async def invalid(*args,**kwargs):calls.append(1);return {'content':'invalid JSON'}
    with pytest.raises(AnalysisDeferred,match='waiting_model_idle'):
        asyncio.run(analyze(q.store,rid,row,transport=invalid,task=task,can_dispatch=lambda:not calls))
    assert len(calls)==1
    assert ledger.defer(q.store,task,'waiting_model_idle')
    assert ledger.page(q.store,rid)['rows'][0]['analysis_state']=='queued'
    assert q.store._db().execute('SELECT COUNT(*) FROM repair_evidence_analysis_attempts WHERE report_id=?',(rid,)).fetchone()[0]==0


def test_format_repair_uses_one_total_deadline(quality,monkeypatch):
    q=quality;rid,row=setup_report(q,monkeypatch);deadlines=[]
    async def transport(*args,**kwargs):
        deadlines.append(kwargs['deadline'])
        return {'content':'invalid JSON' if len(deadlines)==1 else json.dumps({'assessment':'indeterminate',
            'evidence_ids':['context:output'],'observation':'纹理有限','recommendation':'人工核对','limitations':'仅此窗口'})}
    asyncio.run(analyze(q.store,rid,row,transport=transport))
    assert len(deadlines)==2 and deadlines[0]==deadlines[1]


@pytest.mark.parametrize('failure,code,state',[
    ('tool','model_tool_call','failed'),('incomplete','model_incomplete','failed'),
    ('refusal','model_refusal','unavailable'),('vision','vision_unsupported','unavailable'),
    ('timeout','model_timeout','failed'),('reference','fabricated_evidence_id','failed'),
])
def test_model_faults_preserve_evidence_and_classify_history(quality,monkeypatch,failure,code,state):
    from types import SimpleNamespace as NS
    q=quality;rid,row=setup_report(q,monkeypatch)
    async def transport(cfg,wire,tools,**kwargs):
        assert tools==[]
        if failure=='timeout':raise TimeoutError()
        if failure=='vision':return {'response_error':'vision_unsupported'}
        if failure=='reference':return {'content':json.dumps({'assessment':'indeterminate','evidence_ids':['invented'],
            'observation':'纹理有限','recommendation':'人工核对','limitations':'仅此窗口'})}
        return kwargs['parse'](NS(choices=[NS(finish_reason='length' if failure=='incomplete' else 'stop',
            message=NS(tool_calls=['forbidden'] if failure=='tool' else [],refusal='refused' if failure=='refusal' else None,content='{}'))]))
    with pytest.raises(ValueError if failure!='timeout' else TimeoutError):
        asyncio.run(analyze(q.store,rid,row,transport=transport))
    current=ledger.page(q.store,rid)['rows'][0]
    assert current['evidence_state']=='ready' and current['analysis_state']==state
    assert q.store._db().execute('SELECT error_code FROM repair_evidence_analysis_attempts WHERE report_id=?',(rid,)).fetchone()[0]==code
