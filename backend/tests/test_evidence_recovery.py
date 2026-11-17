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
