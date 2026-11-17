"""Read-only report resources and explicitly requested report planning."""
import json
from fastapi import APIRouter, Request, HTTPException, Query
from fastapi.responses import FileResponse
from backend.services import evidence_store as ledger, batch_quality
from backend.services.evidence_report import root

router=APIRouter(prefix='/api/repair-evidence-reports',tags=['repair-evidence'])


def require(store, rid):
    snap=ledger.snapshot(store,rid)
    if snap is None: raise HTTPException(404,'report_not_found')
    return snap


@router.get('/by-goal/{goal_id}')
def by_goal(goal_id: str,request: Request):
    import os
    store=request.app.state.store
    row=store._db().execute('SELECT report_id FROM repair_evidence_reports WHERE goal_id=? ORDER BY revision DESC LIMIT 1',(goal_id,)).fetchone()
    return {'report':ledger.snapshot(store,row[0]) if row else None,'enabled':os.environ.get('SR_EVIDENCE_REPORT_ENABLED')=='1'}


@router.post('/by-goal/{goal_id}')
def plan_historical(goal_id: str,request: Request):
    import os
    if os.environ.get('SR_EVIDENCE_REPORT_ENABLED')!='1':raise HTTPException(409,'evidence_disabled')
    store=request.app.state.store;goal=store.get_goal(goal_id)
    if not goal or goal.get('kind')!='batch':raise HTTPException(404,'batch_not_found')
    if goal.get('checkpoint',{}).get('mode')!='production':raise HTTPException(409,'production_only')
    items=[];ordinal=0
    while True:
        part=store.list_goal_items(goal_id,after_ordinal=ordinal,limit=1000);items.extend(part)
        if len(part)<1000:break
        ordinal=part[-1]['ordinal']
    readiness=ledger.report_compute_readiness(goal,items,{})
    if readiness not in {'ready','partial'}:raise HTTPException(409,readiness)
    reviews=[]
    for item in items:
        rid=item.get('checkpoint',{}).get('quality_review_id')
        review=batch_quality.get_review(store,rid) if rid else None
        if review:reviews.append(review)
    return {'report_id':ledger.create_report(store,goal,items,reviews,readiness)}


@router.get('/{report_id}')
def snapshot(report_id: str,request: Request):
    return require(request.app.state.store,report_id)


@router.get('/{report_id}/rows')
def rows(report_id: str,request: Request,offset: int=Query(0,ge=0),limit: int=Query(30,ge=1,le=30),
         evidence_state: str='', analysis_state: str='', sort: str='scene', cursor: str | None=Query(None,max_length=1024)):
    require(request.app.state.store,report_id)
    try:return ledger.page(request.app.state.store,report_id,offset,limit,evidence_state=evidence_state,analysis_state=analysis_state,sort=sort,cursor=cursor)
    except ValueError as exc:raise HTTPException(409 if str(exc)=='cursor_stale' else 422,str(exc))


@router.get('/{report_id}/export')
def export(report_id: str,request: Request):
    store=request.app.state.store
    snap=require(store,report_id)
    all_rows=[]
    for offset in range(0,snap['coverage']['final_regions'],30):
        all_rows.extend(ledger.page(store,report_id,offset)['rows'])
    return {'schema_version':'repair-evidence.v1','report':snap,'rows':all_rows}


@router.get('/{report_id}/rows/{row_id}/assets/{window_id}/{role}')
def asset(report_id: str,row_id: str,window_id: str,role: str,request: Request):
    store=request.app.state.store
    require(store,report_id)
    raw=store._db().execute('SELECT manifest_json FROM repair_evidence_rows WHERE report_id=? AND row_id=? AND evidence_state=?',(report_id,row_id,'ready')).fetchone()
    if raw is None: raise HTTPException(404,'asset_not_ready')
    manifest=json.loads(raw[0])
    window=next((w for w in manifest.get('windows',[]) if w['window_id']==window_id),None)
    record=next((a for a in window['assets'] if a['role']==role),None) if window else None
    if not record: raise HTTPException(404,'asset_not_found')
    base=root().resolve()
    path=(base/report_id/row_id/window_id/record['file']).resolve()
    if base not in path.parents or not path.is_file(): raise HTTPException(404,'asset_not_found')
    return FileResponse(path,media_type='image/png')

@router.get('/{report_id}/rows/{row_id}/locator')
def locator(report_id: str,row_id: str,request: Request):
    store=request.app.state.store
    snap=require(store,report_id)
    row=store._db().execute('SELECT item_id,review_id,region_id,sources_json FROM repair_evidence_rows WHERE report_id=? AND row_id=?',(report_id,row_id)).fetchone()
    if not row:raise HTTPException(404,'row_not_found')
    review=batch_quality.get_review(store,row[1])
    try:
        batch_quality.validate_current(store,review)
    except (ValueError,OSError,KeyError,TypeError):
        raise HTTPException(409,'历史来源已失效；请查看归档证据，禁止审核当前影像')
    binding=json.loads(row[3])
    return {'goal_id':snap['goal_id'],'item_id':row[0],'review_id':row[1],'region_id':row[2],
            'job_id':binding.get('job_id'),'source_set_hash':store._db().execute('SELECT source_set_hash FROM repair_evidence_reports WHERE report_id=?',(report_id,)).fetchone()[0],
            'output_revision':binding.get('output',{}).get('revision'),'nosr_revision':binding.get('nosr',{}).get('revision'),
            'before_revision':binding.get('backup',{}).get('revision'),'comparison_role':'output_vs_nosr'}


@router.get('/{report_id}/rows/{row_id}/comparison')
def comparison(report_id: str,row_id: str,request: Request,comparison_role: str='output_vs_before'):
    from pathlib import Path
    from backend.api import paths
    if comparison_role not in {'output_vs_before','output_vs_nosr'}:raise HTTPException(422,'invalid_comparison_role')
    store=request.app.state.store;require(store,report_id)
    raw=store._db().execute('SELECT sources_json,plan_json FROM repair_evidence_rows WHERE report_id=? AND row_id=?',(report_id,row_id)).fetchone()
    if not raw:raise HTTPException(404,'row_not_found')
    binding,plan=json.loads(raw[0]),json.loads(raw[1]);images=[]
    for name,role in [('output','pan'),('backup','old_latest') if comparison_role=='output_vs_before' else ('nosr','nosr')]:
        old=binding.get(name)
        if not old:raise HTTPException(409,'comparison_source_missing')
        try:
            actual=batch_quality._file(old['path'],raster=True)
            if actual['revision']!=old['revision'] or actual['image_key']!=old['image_key']:raise ValueError('stale')
        except (OSError,ValueError,KeyError):raise HTTPException(409,'comparison_source_stale')
        path=Path(old['path']);scene=Path(binding['output']['path']).parent.as_posix()
        images.append({'id':paths.scene_id_abs(path),'name':path.name,'kind':'input' if role=='pan' else 'product',
                       'exists':True,'W':old['width'],'H':old['height'],'srcRev':old['revision'],'sizeBytes':old['size'],
                       'rel':None,'hasPreview':False,'previewDiv':None,'mtime':None,'jpgUrl':None,
                       'tourIdentity':batch_quality.image_identity(path,scene,role)})
    return {'images':images,'scene_path':scene,'source_bbox':plan.get('geometry',{}).get('bbox'),
            'comparison_role':comparison_role,'read_only':True}

@router.post('/{report_id}/rows/{row_id}/retry')
async def retry(report_id: str,row_id: str,request: Request):
    import os,time,uuid
    if os.environ.get('SR_EVIDENCE_REPORT_ENABLED')!='1':raise HTTPException(409,'evidence_disabled')
    body=await request.json()
    if not isinstance(body,dict):raise HTTPException(422,'invalid_retry')
    stage=body.get('stage','evidence')
    if stage not in {'evidence','analysis'} or set(body)-{'stage'}:raise HTTPException(422,'invalid_retry')
    store=request.app.state.store
    snap=require(store,report_id)
    if snap['status']=='superseded':raise HTTPException(409,'report_superseded')
    if stage=='analysis' and os.environ.get('SR_EVIDENCE_ANALYSIS_ENABLED')!='1':raise HTTPException(409,'analysis_disabled')
    with store.worker_transaction() as db:
        row=db.execute('SELECT version,evidence_state FROM repair_evidence_rows WHERE report_id=? AND row_id=?',(report_id,row_id)).fetchone()
        if not row:raise HTTPException(404,'row_not_found')
        if stage=='analysis' and row[1]!='ready':raise HTTPException(409,'evidence_not_ready')
        if db.execute("SELECT 1 FROM repair_evidence_tasks WHERE report_id=? AND row_id=? AND state IN ('queued','running')",(report_id,row_id)).fetchone():return {'state':'queued'}
        if stage=='evidence' and row[1]=='ready':return {'state':'ready'}
        token=uuid.uuid4().hex
        db.execute('INSERT INTO repair_evidence_tasks(task_id,report_id,row_id,fingerprint,stage,state,expected_row_version) VALUES(?,?,?,?,?,?,?)', (token,report_id,row_id,token,stage,'queued',row[0]))
        if stage=='analysis':db.execute("UPDATE repair_evidence_rows SET analysis_state='queued' WHERE report_id=? AND row_id=?",(report_id,row_id))
        else:db.execute("UPDATE repair_evidence_rows SET evidence_state='queued' WHERE report_id=? AND row_id=?",(report_id,row_id))
        ledger.touch(db,report_id)
    return {'state':'queued'}


@router.get('/{report_id}/rows/{row_id}/history')
def history(report_id: str,row_id: str,request: Request):
    store=request.app.state.store
    require(store,report_id)
    attempts=store._db().execute('SELECT attempt_id,status,error_code,result_json,created_at FROM repair_evidence_analysis_attempts WHERE report_id=? AND row_id=? ORDER BY created_at DESC LIMIT 30',(report_id,row_id)).fetchall()
    return {'attempts':[{'attempt_id':a[0],'status':a[1],'error_code':a[2],'result':json.loads(a[3]) if a[3] else None,'created_at':a[4]} for a in attempts]}
