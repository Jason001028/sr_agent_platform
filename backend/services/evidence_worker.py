"""Opt-in, durable evidence coordinator. Scheduling never owns SR or human review."""
import asyncio
import json
import logging
import os
import time
from backend.services import evidence_store as ledger, batch_quality
from backend.services.evidence_report import generate_window

log = logging.getLogger(__name__)


def busy(store):
    return bool(store._db().execute("SELECT 1 FROM sr_tasks WHERE status NOT IN ('COMPLETED','FAILED','CANCELLED','TIMEOUT') LIMIT 1").fetchone()
        or store._db().execute("SELECT 1 FROM agent_goal_steps WHERE kind='batch_item' AND status IN ('SUBMITTING','WAITING_SR','VERIFYING','PENDING','RETRY_WAIT','OPENING','DETECTING') LIMIT 1").fetchone()
        or store._db().execute("""SELECT 1 FROM agent_goal_steps WHERE kind='batch_item' AND status='MASK_READY' AND
            (COALESCE(json_extract(checkpoint,'$.review_required'),0)=0 OR
             (json_extract(checkpoint,'$.review.decision')='approve' AND
              json_extract(checkpoint,'$.mask.sha256') IS NOT NULL AND
              json_extract(checkpoint,'$.mask.sha256')!='' AND
              json_extract(checkpoint,'$.review.mask_sha256')=json_extract(checkpoint,'$.mask.sha256')))
            LIMIT 1""").fetchone())


def admission(state, *, model=False, running=False):
    from backend.config import sr_runtime
    from backend.services import local_exec
    if busy(state.store): return False
    if sr_runtime().executor == 'local':
        snapshot = local_exec.activity_snapshot()
        if snapshot['active'] or snapshot['unknown'] or snapshot['quarantine']: return False
    work = state.heavy_work
    if any(work.queues[:2]) or (not running and work.active): return False
    if model and (any(work.queues) or getattr(state,'chat_workers',set())): return False
    return True


def discover(store, since):
    goals=store._db().execute("SELECT goal_id,updated_at FROM agent_goals WHERE kind='batch' AND (updated_at>? OR (updated_at=? AND goal_id>?)) ORDER BY updated_at,goal_id LIMIT 16",(since[0],since[0],since[1])).fetchall()
    for gid,updated in goals:
        goal=store.get_goal(gid)
        items=[]; ordinal=0
        while True:
            part=store.list_goal_items(gid,after_ordinal=ordinal,limit=1000)
            items.extend(part)
            if len(part)<1000: break
            ordinal=part[-1]['ordinal']
        readiness=ledger.report_compute_readiness(goal,items,{})
        if readiness in {'ready','partial'} and goal.get('checkpoint',{}).get('mode')=='production':
            reviews=[]
            for item in items:
                rid=item.get('checkpoint',{}).get('quality_review_id')
                review=batch_quality.get_review(store,rid) if rid else None
                if review: reviews.append(review)
            ledger.create_report(store,goal,items,reviews,readiness)
        else:
            with store.worker_transaction() as db:
                ledger.suspend_reports(db,gid)
    return (goals[-1][1],goals[-1][0]) if goals else since


async def heartbeat(store, task):
    while True:
        await asyncio.sleep(30)
        if not ledger.renew(store,task): return


async def run_task(state, task):
    store=state.store
    beat=asyncio.create_task(heartbeat(store,task))
    try:
        if task['stage']=='analysis':
            if os.environ.get('SR_EVIDENCE_ANALYSIS_ENABLED')!='1' or not admission(state,model=True):
                ledger.defer(store,task,'waiting_model_idle');return
            from backend.services.evidence_analysis import analyze
            await analyze(store,task['report_id'],task['row_id'],task=task,
                          can_dispatch=lambda: admission(state,model=True))
            with store.worker_transaction() as db:
                if ledger.owned(db,task):
                    db.execute("UPDATE repair_evidence_tasks SET state='done',lease_token=NULL,lease_until=NULL WHERE task_id=?",(task['task_id'],))
            return
        raw=store._db().execute('SELECT plan_json FROM repair_evidence_rows WHERE report_id=? AND row_id=?',(task['report_id'],task['row_id'])).fetchone()
        manifests=[]
        for index,_ in enumerate(json.loads(raw[0])['windows']):
            if not admission(state) or not ledger.renew(store,task):
                ledger.defer(store,task);return
            def read_one():
                # Recheck after queue admission; a direct SR can arrive while waiting.
                if not admission(state,running=True):raise ValueError('waiting_idle')
                if not ledger.owned(store._db(),task):raise ValueError('obsolete_task')
                return generate_window(store,task,index)
            native=asyncio.create_task(state.heavy_work.run(read_one,evidence=True))
            try:
                manifests.append(await asyncio.shield(native))
            except asyncio.CancelledError:
                # Keep heartbeat and the native slot until actual I/O is finished.
                try: await asyncio.shield(native)
                except Exception: pass
                ledger.defer(store,task,'shutdown')
                raise
        if ledger.finish(store,task,state='ready',manifest={'windows':manifests}) and os.environ.get('SR_EVIDENCE_ANALYSIS_ENABLED')=='1':
            ledger.queue_analysis(store,task['report_id'],task['row_id'])
    except asyncio.CancelledError:
        ledger.defer(store,task,'shutdown')
        raise
    except Exception as exc:
        code=str(exc) if isinstance(exc,ValueError) and len(str(exc))<80 else type(exc).__name__
        if code in {'waiting_idle','waiting_model_idle'}: ledger.defer(store,task,code)
        elif task['stage']=='analysis':
            with store.worker_transaction() as db:
                if ledger.owned(db,task):
                    db.execute("UPDATE repair_evidence_tasks SET state='failed',error_json=?,lease_token=NULL,lease_until=NULL WHERE task_id=?",(ledger.dumps({'code':code}),task['task_id']))
        else:
            stage='stale' if isinstance(exc,batch_quality.ReviewConflict) or code=='source_changed' else 'unsupported' if code in {'unsupported_format','alignment_unverified'} else 'failed'
            ledger.finish(store,task,state=stage,error=code)
        log.warning('evidence task failed: %s',type(exc).__name__)
    finally:
        beat.cancel()
        try:await beat
        except asyncio.CancelledError:pass


async def evidence_loop(state):
    if os.environ.get('SR_EVIDENCE_REPORT_ENABLED')!='1': return
    saved=state.store._db().execute("SELECT value FROM platform_settings WHERE key='evidence_scan_since'").fetchone()
    since=tuple(json.loads(saved[0])) if saved else (time.time(),'')
    while True:
        try:
            if admission(state):
                since=discover(state.store,since)
                with state.store.worker_transaction() as db:
                    db.execute("INSERT INTO platform_settings(key,value,updated_at) VALUES('evidence_scan_since',?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at",(json.dumps(since),time.time()))
                analysis_enabled=os.environ.get('SR_EVIDENCE_ANALYSIS_ENABLED')=='1'
                if analysis_enabled:
                    pending=state.store._db().execute("SELECT w.report_id,w.row_id FROM repair_evidence_rows w JOIN repair_evidence_reports r ON r.report_id=w.report_id WHERE w.evidence_state='ready' AND w.analysis_state='not_requested' AND r.status!='superseded' LIMIT 16").fetchall()
                    for rid,rowid in pending:ledger.queue_analysis(state.store,rid,rowid)
                task=ledger.claim(state.store,stages=('evidence','analysis') if analysis_enabled else ('evidence',))
                if task:await run_task(state,task)
        except asyncio.CancelledError:raise
        except Exception as exc:log.warning('evidence coordinator failed: %s',type(exc).__name__)
        await asyncio.sleep(5)
