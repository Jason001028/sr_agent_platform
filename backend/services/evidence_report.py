"""Evidence publication, independent of SR and human quality decisions."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import time
import numpy as np
from backend.services import batch_manifest, batch_quality, preview_jpg
from backend.services.evidence_geometry import digest, ratio_window
from backend.services.evidence_raster import read_window, display_images, EvidenceError
from backend.services import evidence_store as ledger
from backend.services.evidence_alignment import pairing, aligned_window


def root():
    return batch_manifest.artifact_root() / 'evidence'


def storage_reserve(store,key,task,quota,amount):
    with store.worker_transaction() as db:
        if not ledger.owned(db,task):raise EvidenceError('obsolete_task')
        previous=db.execute('SELECT bytes FROM repair_evidence_storage WHERE storage_key=?',(key,)).fetchone()
        used=db.execute('SELECT COALESCE(SUM(bytes),0) FROM repair_evidence_storage').fetchone()[0]
        legacy=db.execute("SELECT value FROM platform_settings WHERE key='evidence_storage_bytes'").fetchone()
        used+=int(legacy[0]) if legacy else 0
        if used-(previous[0] if previous else 0)+amount>quota:raise EvidenceError('storage_budget')
        db.execute("INSERT INTO repair_evidence_storage VALUES(?,?,'reserved',?,?) ON CONFLICT(storage_key) DO UPDATE SET bytes=excluded.bytes,state=excluded.state,lease_token=excluded.lease_token,updated_at=excluded.updated_at",
                   (key,amount,task['lease_token'],time.time()))


def storage_finish(store,key,task,amount,published):
    with store.worker_transaction() as db:
        db.execute('UPDATE repair_evidence_storage SET bytes=?,state=?,updated_at=? WHERE storage_key=? AND lease_token=?',
                   (amount,'published' if published else 'released',time.time(),key,task['lease_token']))


def generate_window(store, task, window_index):
    raw=store._db().execute('SELECT review_id,plan_json,sources_json FROM repair_evidence_rows WHERE report_id=? AND row_id=?',(task['report_id'],task['row_id'])).fetchone()
    review=batch_quality.get_review(store,raw[0])
    batch_quality.validate_current(store,review)
    plan,binding=json.loads(raw[1]),json.loads(raw[2])
    settings=ledger.snapshot(store,task['report_id'])['settings']
    from backend.services.evidence_config import load_settings
    settings=settings or load_settings()
    window=plan['windows'][window_index]
    pan=binding['output']; nosr=binding['nosr']
    transform,alignment_source=pairing(pan['path'],nosr['path'],(pan['width'],pan['height']),(nosr['width'],nosr['height']))
    destination=root()/task['report_id']/task['row_id']/window['id']
    if root().resolve() not in destination.resolve().parents:raise EvidenceError('invalid_evidence_path')
    storage_key=digest([task['report_id'],task['row_id'],window['id']])
    if (destination/'manifest.json').is_file():
        try:
            manifest=json.loads((destination/'manifest.json').read_text(encoding='utf-8'))
            if (manifest.get('schema_version')=='evidence.v2' and manifest.get('assets')
                    and all(a['file'] in {'nosr.png','before.png','output.png'} and hashlib.sha256((destination/a['file']).read_bytes()).hexdigest()==a['sha256'] for a in manifest['assets'])):
                return manifest
        except (OSError,ValueError,KeyError,TypeError):pass
    parent=destination.parent
    parent.mkdir(parents=True,exist_ok=True)
    quota=int(os.environ.get('SR_EVIDENCE_STORAGE_LIMIT_BYTES','0'))
    if quota<=0:
        raise EvidenceError('storage_quota_not_configured')
    reservation=16*1024*1024
    storage_reserve(store,storage_key,task,quota,reservation)
    published_bytes=0
    temp=None
    try:
        temp=Path(tempfile.mkdtemp(prefix='.pending-',dir=parent))
        roles={}
        for role,key in [('nosr','nosr'),('before','backup'),('output','output')]:
            source=binding.get(key)
            if source is None:
                continue
            requested=window['window']
            if role=='nosr':
                roles[role]=aligned_window(source['path'],requested,transform,(nosr['width'],nosr['height']),max_bytes=settings['max_read_bytes'])
            else:
                roles[role]=read_window(source['path'],requested,max_bytes=settings['max_read_bytes'])
        images,stretch=display_images(roles,max_edge=settings['context_edge'])
        assets=[]
        for role,im in images.items():
            name=role+'.png'; im.save(temp/name,format='PNG')
            a,v,meta=roles[role]
            values=a[v]
            assets.append({'file':name,'role':role,'sha256':hashlib.sha256((temp/name).read_bytes()).hexdigest(),
                'width':im.width,'height':im.height,'reader':meta,
                'metrics':{'valid_fraction':float(v.mean()),'mean':float(values.mean()) if values.size else None,
                           'std':float(values.std()) if values.size else None,
                           'p05':float(np.percentile(values,5)) if values.size else None,
                           'p95':float(np.percentile(values,95)) if values.size else None}})
        for key in ('nosr','backup','output'):
            if key in binding and preview_jpg.source_revision(Path(binding[key]['path']))!=binding[key]['revision']:
                raise EvidenceError('source_changed')
        paired_metrics={}
        if 'before' in roles and roles['before'][0].shape==roles['output'][0].shape:
            before,bv,_=roles['before'];after,av,_=roles['output'];valid=bv&av
            delta=after[valid]-before[valid]
            paired_metrics={'before_output_mae':float(np.abs(delta).mean()) if delta.size else None,
                            'before_output_mean_shift':float(delta.mean()) if delta.size else None,
                            'valid_pair_pixels':int(valid.sum()),'meaning':'DN change only; not quality acceptance'}
        manifest={'schema_version':'evidence.v2','window_id':window['id'],'coverage':plan['coverage'],
            'alignment_source':alignment_source,'alignment_warning':'同景比例网格，未证明严格空间配准' if alignment_source=='production_ratio' else '同 CRS 仿射映射；未验证影像内容配准精度',
            'stretch':stretch,'paired_metrics':paired_metrics,'assets':assets}
        (temp/'manifest.json').write_text(ledger.dumps(manifest),encoding='utf-8')
        # Serialize lease validation and the final short rename with takeover.
        with store.worker_transaction() as db:
            if not ledger.owned(db,task):raise EvidenceError('obsolete_task')
            if destination.exists():
                if root().resolve() not in destination.resolve().parents:raise EvidenceError('invalid_evidence_path')
                shutil.rmtree(destination)
            size=sum(p.stat().st_size for p in temp.iterdir() if p.is_file())
            os.replace(temp,destination)
            published_bytes=size
            db.execute("UPDATE repair_evidence_storage SET bytes=?,state='published',updated_at=? WHERE storage_key=? AND lease_token=?",
                       (size,time.time(),storage_key,task['lease_token']))
        return manifest
    finally:
        if not published_bytes:storage_finish(store,storage_key,task,0,False)
        if temp is not None and temp.exists():
            shutil.rmtree(temp)
