"""Report persistence. No source-file access on snapshot and page reads."""
import json
import time
import uuid
from backend.services.evidence_geometry import digest, plan_region, RECIPE_VERSION

SCHEMA = '''
CREATE TABLE IF NOT EXISTS repair_evidence_reports (
 report_id TEXT PRIMARY KEY, goal_id TEXT NOT NULL, session_id TEXT,
 revision INTEGER NOT NULL, source_set_hash TEXT NOT NULL, recipe_version TEXT NOT NULL,
 status TEXT NOT NULL, compute_state TEXT NOT NULL, coverage_json TEXT NOT NULL,
 settings_json TEXT NOT NULL DEFAULT '{}',
 created_at REAL NOT NULL, updated_at REAL NOT NULL,
 UNIQUE(goal_id,source_set_hash,recipe_version));
CREATE INDEX IF NOT EXISTS evidence_goal_updated ON agent_goals(kind,updated_at,goal_id);
CREATE INDEX IF NOT EXISTS evidence_goal ON repair_evidence_reports(goal_id,updated_at);
CREATE TABLE IF NOT EXISTS repair_evidence_rows (
 report_id TEXT NOT NULL, row_id TEXT NOT NULL, ordinal INTEGER NOT NULL,
 item_id TEXT NOT NULL, review_id TEXT NOT NULL, region_id TEXT NOT NULL,
 plan_json TEXT NOT NULL, sources_json TEXT NOT NULL,
 evidence_state TEXT NOT NULL DEFAULT 'planned', analysis_state TEXT NOT NULL DEFAULT 'not_requested',
 manifest_json TEXT NOT NULL DEFAULT '{}', error_code TEXT,
 version INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(report_id,row_id));
CREATE INDEX IF NOT EXISTS evidence_ordinal ON repair_evidence_rows(report_id,ordinal);
CREATE TABLE IF NOT EXISTS repair_evidence_tasks (
 task_id TEXT PRIMARY KEY, report_id TEXT NOT NULL, row_id TEXT NOT NULL,
 fingerprint TEXT UNIQUE NOT NULL, stage TEXT NOT NULL, state TEXT NOT NULL,
 attempts INTEGER NOT NULL DEFAULT 0, next_wake_at REAL NOT NULL DEFAULT 0,
 lease_token TEXT, lease_until REAL, expected_row_version INTEGER NOT NULL,
 error_json TEXT);
CREATE INDEX IF NOT EXISTS evidence_due ON repair_evidence_tasks(state,next_wake_at,lease_until);
CREATE TABLE IF NOT EXISTS repair_evidence_analyses (
 analysis_id TEXT PRIMARY KEY, report_id TEXT NOT NULL, row_id TEXT NOT NULL,
 analysis_key TEXT NOT NULL, model_identity TEXT NOT NULL, result_json TEXT NOT NULL,
 status TEXT NOT NULL, created_at REAL NOT NULL,
 UNIQUE(report_id,row_id,analysis_key));
CREATE TABLE IF NOT EXISTS repair_evidence_analysis_attempts (
 attempt_id TEXT PRIMARY KEY, report_id TEXT NOT NULL, row_id TEXT NOT NULL,
 analysis_key TEXT, model_identity TEXT, status TEXT NOT NULL,
 error_code TEXT, result_json TEXT, created_at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS evidence_attempt_history ON repair_evidence_analysis_attempts(report_id,row_id,created_at);
CREATE TABLE IF NOT EXISTS repair_evidence_storage (
 storage_key TEXT PRIMARY KEY, bytes INTEGER NOT NULL, state TEXT NOT NULL,
 lease_token TEXT NOT NULL, updated_at REAL NOT NULL);
'''


def migrate(db):
    if 'settings_json' not in {r[1] for r in db.execute('PRAGMA table_info(repair_evidence_reports)')}:
        db.execute("ALTER TABLE repair_evidence_reports ADD COLUMN settings_json TEXT NOT NULL DEFAULT '{}'")


def dumps(value):
    return json.dumps(value,ensure_ascii=False,allow_nan=False)


def touch(db, report_id):
    db.execute('UPDATE repair_evidence_reports SET updated_at=MAX(updated_at+0.000001,?) WHERE report_id=?',(time.time(),report_id))


def report_compute_readiness(goal, items, executor_snapshot):
    if executor_snapshot.get('unknown') or executor_snapshot.get('quarantine'):
        return 'indeterminate'
    if executor_snapshot.get('active') or executor_snapshot.get('queued'):
        return 'computing'
    for item in items:
        cp=item.get('checkpoint') or {}
        state=cp.get('job_status',{}).get('state')
        if item['status']=='SUBMITTING' or state in {'UNKNOWN','quarantine'}:
            return 'indeterminate'
    waiting = {'AWAITING_MASK_REVIEW', 'WAITING_INPUT'}
    if any(i['status'] in {'PENDING','RETRY_WAIT','OPENING','DETECTING','WAITING_SR','VERIFYING','PREPARING'}
           or (i['status']=='MASK_READY' and mask_ready_to_submit(i.get('checkpoint',{}))) for i in items):
        return 'computing'
    if any(i['status'] in waiting or (i['status']=='MASK_READY' and i.get('checkpoint',{}).get('review_required')) for i in items):
        return 'waiting_input'
    if goal.get('status') in {'CANCELLING','CANCEL_REQUESTED'}:
        return 'indeterminate'
    terminal = {'AWAITING_REVIEW','SUCCEEDED','COMPLETED','NO_ISSUE','FAILED','SKIPPED','BLOCKED','REVIEW_REJECTED','CANCELLED'}
    if any(i['status'] not in terminal for i in items):
        return 'indeterminate'
    if any(i['status'] in {'NO_ISSUE','FAILED','SKIPPED','BLOCKED','REVIEW_REJECTED','CANCELLED'} for i in items):
        return 'partial'
    for i in items:
        layers=(i.get('checkpoint',{}).get('verification') or i.get('verification') or {}).get('layers',{})
        if not all(layers.get(k,{}).get('status')=='passed' for k in ('contract','artifacts')):
            return 'partial'
    return 'ready'


def mask_ready_to_submit(checkpoint):
    if not checkpoint.get('review_required'):
        return True
    review=checkpoint.get('review') or {}
    sha=(checkpoint.get('mask') or {}).get('sha256')
    return bool(sha and review.get('decision')=='approve' and review.get('mask_sha256')==sha)


def suspend_reports(db,goal_id,*,except_report=None):
    """Fence in-flight writers as soon as a report stops being current."""
    where="goal_id=? AND status!='superseded'";args=[goal_id]
    if except_report is not None:where+=' AND report_id!=?';args.append(except_report)
    db.execute(f"""UPDATE repair_evidence_tasks SET state='queued',lease_token=NULL,lease_until=NULL,next_wake_at=0
        WHERE state='running' AND report_id IN (SELECT report_id FROM repair_evidence_reports WHERE {where})""",args)
    db.execute(f"UPDATE repair_evidence_reports SET status='superseded',updated_at=MAX(updated_at+0.000001,?) WHERE {where}",(time.time(),*args))


def create_report(store, goal, items, reviews, compute_state):
    from backend.services.evidence_config import load_settings
    settings=load_settings()
    if compute_state not in {'ready','partial'}:
        raise ValueError('compute_not_ready')
    source_hash=digest({'reviews':[[r['item_id'],r['review_id'],r['binding']] for r in reviews],
                        'items':[[i['item_id'],i['status'],i.get('checkpoint',{}).get('quality_review_id')] for i in items],
                        'compute_state':compute_state,'settings':settings})
    now=time.time()
    with store.worker_transaction() as db:
        old=db.execute('SELECT report_id,status FROM repair_evidence_reports WHERE goal_id=? AND source_set_hash=? AND recipe_version=?',
                       (goal['goal_id'],source_hash,RECIPE_VERSION)).fetchone()
        if old:
            if old[1]=='superseded':
                suspend_reports(db,goal['goal_id'],except_report=old[0])
                # Also fence rows written by older deployments which suspended
                # reports without revoking running task tokens.
                db.execute("UPDATE repair_evidence_tasks SET state='queued',lease_token=NULL,lease_until=NULL,next_wake_at=0 WHERE report_id=? AND state='running'",(old[0],))
                db.execute("UPDATE repair_evidence_reports SET status='pending' WHERE report_id=?",(old[0],))
                touch(db,old[0])
            return old[0]
        rid=uuid.uuid4().hex
        revision=db.execute('SELECT COALESCE(MAX(revision),0)+1 FROM repair_evidence_reports WHERE goal_id=?',(goal['goal_id'],)).fetchone()[0]
        suspend_reports(db,goal['goal_id'])
        coverage={'total_scenes':len(items),'verified_scenes':len([r for r in reviews if r['binding'].get('kind')!='no-candidate']),
                  'failed_scenes':sum(i['status']=='FAILED' for i in items),
                  'skipped_scenes':sum(i['status']=='SKIPPED' for i in items),
                  'no_candidate_scenes':sum(r['binding'].get('kind')=='no-candidate' for r in reviews)}
        plans=[]
        for review in reviews:
            binding=review['binding']
            regions = binding.get('regions') or ([] if binding.get('kind')=='no-candidate' else [{}])
            for index, region in enumerate(regions):
                try:
                    plan=plan_region(review['item_id'],region,binding['output']['width'],binding['output']['height'],settings=settings)
                except (ValueError, KeyError, TypeError):
                    region_id = region.get('candidate_id') or 'legacy-geometry-'+str(index)
                    plan = {'row_id':digest([review['item_id'],region_id,region]), 'region_id':region_id,
                            'geometry':region,'windows':[],'coverage':'unavailable','error_code':'unsupported_geometry'}
                plan['scene_name'] = next((i.get('name') for i in items if i['item_id']==review['item_id']),None) or review['item_id']
                plans.append((review,plan))
        coverage['final_regions']=len(plans)
        db.execute('INSERT INTO repair_evidence_reports(report_id,goal_id,session_id,revision,source_set_hash,recipe_version,status,compute_state,coverage_json,created_at,updated_at,settings_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                   (rid,goal['goal_id'],goal.get('session_id'),revision,source_hash,RECIPE_VERSION,
                    'pending' if plans else 'partial',compute_state,dumps(coverage),now,now,dumps(settings)))
        for ordinal,(review,plan) in enumerate(plans,1):
            row=plan['row_id']
            db.execute('INSERT INTO repair_evidence_rows(report_id,row_id,ordinal,item_id,review_id,region_id,plan_json,sources_json) VALUES(?,?,?,?,?,?,?,?)',
                       (rid,row,ordinal,review['item_id'],review['review_id'],plan['region_id'],dumps(plan),dumps(review['binding'])))
            if plan.get('error_code'):
                db.execute("UPDATE repair_evidence_rows SET evidence_state='unsupported',error_code=? WHERE report_id=? AND row_id=?",(plan['error_code'],rid,row))
                continue
            db.execute('INSERT INTO repair_evidence_tasks(task_id,report_id,row_id,fingerprint,stage,state,expected_row_version) VALUES(?,?,?,?,?,?,?)',
                       (uuid.uuid4().hex,rid,row,digest([rid,row]),'evidence','queued',0))
    return rid


def claim(store, *, now=None, stages=('evidence','analysis')):
    now=time.time() if now is None else now
    with store.worker_transaction() as db:
        placeholders=','.join('?' for _ in stages)
        row=db.execute(f"""SELECT t.task_id,t.report_id,t.row_id,t.expected_row_version,t.stage
            FROM repair_evidence_tasks t JOIN repair_evidence_reports r ON r.report_id=t.report_id
            WHERE r.status!='superseded' AND t.stage IN ({placeholders}) AND
            ((t.state='queued' AND t.next_wake_at<=?) OR (t.state='running' AND t.lease_until<?))
            ORDER BY t.next_wake_at,t.task_id LIMIT 1""",(*stages,now,now)).fetchone()
        if not row:
            return None
        token=uuid.uuid4().hex
        db.execute("UPDATE repair_evidence_tasks SET state='running',lease_token=?,lease_until=?,attempts=attempts+1 WHERE task_id=?",(token,now+300,row[0]))
        return dict(zip(('task_id','report_id','row_id','expected_row_version','stage'),row),lease_token=token)


def owned(db, task):
    return bool(db.execute("""SELECT 1 FROM repair_evidence_tasks t
        JOIN repair_evidence_rows w ON w.report_id=t.report_id AND w.row_id=t.row_id
        JOIN repair_evidence_reports r ON r.report_id=t.report_id
        WHERE t.task_id=? AND t.lease_token=? AND t.state='running' AND t.lease_until>?
        AND w.version=? AND r.status!='superseded'""",
        (task['task_id'],task['lease_token'],time.time(),task['expected_row_version'])).fetchone())


def renew(store, task):
    with store.worker_transaction() as db:
        if not owned(db,task): return False
        db.execute('UPDATE repair_evidence_tasks SET lease_until=? WHERE task_id=?',(time.time()+300,task['task_id']))
    return True


def defer(store, task, reason='waiting_idle'):
    with store.worker_transaction() as db:
        if not owned(db,task): return False
        db.execute("UPDATE repair_evidence_tasks SET state='queued',next_wake_at=?,lease_token=NULL,lease_until=NULL,error_json=? WHERE task_id=?",
                   (time.time()+5,dumps({'code':reason}),task['task_id']))
    return True


def queue_analysis(store, report_id, row_id):
    with store.worker_transaction() as db:
        row=db.execute("SELECT version FROM repair_evidence_rows WHERE report_id=? AND row_id=? AND evidence_state='ready' AND analysis_state='not_requested'",(report_id,row_id)).fetchone()
        if not row: return
        fingerprint=digest([report_id,row_id,row[0],'analysis-auto'])
        db.execute("INSERT OR IGNORE INTO repair_evidence_tasks(task_id,report_id,row_id,fingerprint,stage,state,expected_row_version) VALUES(?,?,?,?,'analysis','queued',?)",
                   (uuid.uuid4().hex,report_id,row_id,fingerprint,row[0]))
        db.execute("UPDATE repair_evidence_rows SET analysis_state='queued' WHERE report_id=? AND row_id=?",(report_id,row_id))
        touch(db,report_id)


def finish(store, task, *, state, manifest=None, error=None):
    with store.worker_transaction() as db:
        if not owned(db, task):
            return False
        changed=db.execute('UPDATE repair_evidence_rows SET evidence_state=?,manifest_json=?,error_code=?,version=version+1 WHERE report_id=? AND row_id=? AND version=?',
                   (state,dumps(manifest or {}),error,task['report_id'],task['row_id'],task['expected_row_version'])).rowcount
        db.execute("UPDATE repair_evidence_tasks SET state=?,lease_token=NULL,lease_until=NULL WHERE task_id=?",('done' if changed else 'obsolete',task['task_id']))
        if changed and state!='ready':
            db.execute("UPDATE repair_evidence_rows SET analysis_state='stale' WHERE report_id=? AND row_id=? AND analysis_state='ready'",(task['report_id'],task['row_id']))
        if changed:touch(db,task['report_id'])
        return bool(changed)


def snapshot(store, report_id):
    raw=store._db().execute('SELECT report_id,goal_id,revision,status,compute_state,coverage_json,created_at,updated_at FROM repair_evidence_reports WHERE report_id=?',(report_id,)).fetchone()
    if raw is None:
        return None
    result=dict(zip(('report_id','goal_id','revision','status','compute_state','coverage','created_at','updated_at'),raw))
    result['coverage']=json.loads(result['coverage'])
    result['settings']=json.loads(store._db().execute('SELECT settings_json FROM repair_evidence_reports WHERE report_id=?',(report_id,)).fetchone()[0])
    states=dict(store._db().execute('SELECT evidence_state,COUNT(*) FROM repair_evidence_rows WHERE report_id=? GROUP BY evidence_state',(report_id,)))
    result['evidence_counts']=states
    result['analysis_counts']=dict(store._db().execute('SELECT analysis_state,COUNT(*) FROM repair_evidence_rows WHERE report_id=? GROUP BY analysis_state',(report_id,)))
    ready=states.get('ready',0); total=sum(states.values())
    analyzed=store._db().execute("SELECT COUNT(*) FROM repair_evidence_rows WHERE report_id=? AND analysis_state='ready'",(report_id,)).fetchone()[0]
    result['coverage'].update(evidence_ready=ready,analyzed=analyzed,unanalyzed=total-analyzed)
    result['coverage']['failed_or_stale']=sum(states.get(k,0) for k in ('failed','stale'))
    result['title']=(store.get_goal(result['goal_id']) or {}).get('title',result['goal_id'])
    if result['status']!='superseded':
        result['status']='processing' if any(k in states for k in ('planned','queued','waiting_idle','reading')) else ('ready' if ready==total and total else 'partial')
    return result


def page(store, report_id, offset=0, limit=30, *, evidence_state='', analysis_state='', sort='scene', cursor=None):
    import base64
    evidence_states={'','planned','queued','waiting_idle','reading','ready','unsupported','failed','stale'}
    analysis_states={'','not_requested','queued','waiting_idle','running','ready','unavailable','failed','stale'}
    if evidence_state not in evidence_states or analysis_state not in analysis_states or sort not in {'scene','priority'}:
        raise ValueError('invalid_filter')
    report=store._db().execute('SELECT revision,updated_at FROM repair_evidence_reports WHERE report_id=?',(report_id,)).fetchone()
    if report is None:raise KeyError('report_not_found')
    signature=digest([report_id,report,evidence_state,analysis_state,sort])
    if cursor:
        try:
            payload=json.loads(base64.urlsafe_b64decode(cursor.encode()))
            if payload['signature']!=signature:raise ValueError('cursor_stale')
            offset=int(payload['offset'])
        except (KeyError,TypeError,ValueError,UnicodeError) as exc:
            raise ValueError('cursor_stale') from exc
    limit=max(1,min(30,int(limit))); offset=max(0,int(offset))
    where='report_id=?';args=[report_id]
    for field,value in [('evidence_state',evidence_state),('analysis_state',analysis_state)]:
        if value:where+=' AND '+field+'=?';args.append(value)
    order="item_id,ordinal" if sort=='scene' else "CASE evidence_state WHEN 'stale' THEN 0 WHEN 'failed' THEN 1 WHEN 'unsupported' THEN 2 ELSE 3 END,item_id,ordinal"
    rows=store._db().execute(f'SELECT row_id,item_id,review_id,region_id,plan_json,evidence_state,analysis_state,manifest_json,error_code FROM repair_evidence_rows WHERE {where} ORDER BY {order} LIMIT ? OFFSET ?', (*args,limit,offset)).fetchall()
    result=[]
    for row in rows:
        obj=dict(zip(('row_id','item_id','review_id','region_id','plan','evidence_state','analysis_state','manifest','error_code'),row))
        for key in ('plan','manifest'):
            obj[key]=json.loads(obj[key])
        analysis=store._db().execute("SELECT result_json FROM repair_evidence_analyses WHERE report_id=? AND row_id=? AND status='ready' ORDER BY created_at DESC LIMIT 1",(report_id,obj['row_id'])).fetchone()
        obj['analysis']=json.loads(analysis[0]) if analysis and obj['evidence_state']=='ready' else None
        result.append(obj)
    total=store._db().execute('SELECT COUNT(*) FROM repair_evidence_rows WHERE '+where,args).fetchone()[0]
    report_total=store._db().execute('SELECT COUNT(*) FROM repair_evidence_rows WHERE report_id=?',(report_id,)).fetchone()[0]
    next_cursor=base64.urlsafe_b64encode(dumps({'signature':signature,'offset':offset+limit}).encode()).decode() if offset+limit<total else None
    return {'rows':result,'total':total,'report_total':report_total,'offset':offset,'limit':limit,'next_cursor':next_cursor,'page_version':signature}

