"""Test-only actual app + synthetic repaired TIFFs and human-review ledger.

Uses real difference core, real MaskBuilder, actual repair composer and deployed
SR contract verifier. GDAL/interpolation and inference are tiny CPU fixtures;
SR submission is forbidden. Never import this module in production.
"""
import argparse
import json
import os
from pathlib import Path
import xml.etree.ElementTree as ET

import numpy as np
import tifffile

from backend.tests import allowed_roots_env
from backend.tests.test_pan_repair import FakeGdal, Raster


class ReadRaster:
    def __init__(self,path):
        self.pixels=tifffile.imread(path)
        self.RasterYSize,self.RasterXSize=self.pixels.shape
        self.RasterCount=1; self.DataType=1 if self.pixels.dtype==np.uint8 else 2
    def GetRasterBand(self,_):return self
    def ReadAsArray(self,x,y,w,h):return np.array(self.pixels[y:y+h,x:x+w])
    def FlushCache(self):pass


class TinyGdal(FakeGdal):
    def Open(self,path,_=0):
        try:return Raster(path)
        except ValueError:return ReadRaster(path)


def seed(store,root,count=2):
    from backend.services import batch_manifest,batch_detector,batch_mask,batch_worker,run_sr
    from backend.services.goal_runtime import _batch_item_checkpoint
    from backend.services.goal_lease import GoalLease,current_lease
    from SR_code.variants import repair_sr
    import sys
    from SR_code.variants import verify_sr_run
    sys.modules['verify_sr_run']=verify_sr_run
    scenes=[]
    for i in range(count):
        scene=root/f'JXGF07D03_PMS_20260101010101_200516571_101_{i+1:04d}_001_L1_PAN'
        scene.mkdir(parents=True,exist_ok=True)
        a=np.full((128,128),12,np.uint16);a[20:40,20:40]=77;a[70:95,80:105]=77
        tifffile.imwrite(scene/'PAN.tif',a)
        tifffile.imwrite(scene/(scene.name+'_NOSR.tif'),np.full((64,64),12,np.uint16))
        (scene/(scene.name+'_meta.xml')).write_text('<meta><SolarAzimuth/></meta>',encoding='utf-8')
        scenes.append(scene)
    imported=batch_manifest.import_manifest(text='',mode='production',sources=[{'path':str(s/'PAN.tif')} for s in scenes])
    assert not imported['rejected'],imported
    manifest=batch_manifest.load_manifest(imported['manifest_ref'])
    planned=batch_manifest.plan_goal_items(manifest,[])
    goal=store.create_batch_goal(title='真实几何 + 人工验收（合成测试）',request_key='quality-browser',content_sha256=imported['manifest_ref'],
        checkpoint={'mode':'production','workflow':batch_manifest.WORKFLOW,'manifest_ref':imported['manifest_ref']},
        items=[{'item_id':p['item_id'],'ordinal':p['ordinal'],'name':p['name'],'status':'PENDING','checkpoint':_batch_item_checkpoint(p)} for p in planned])
    claimed=store.claim_goal(goal['goal_id'],'fixture',lease_sec=600)
    lease=GoalLease(goal['goal_id'],claimed['lease_token'],claimed['version'])
    token=current_lease.set(lease)
    try:
        for p in planned:
            item=store.get_goal_item(goal['goal_id'],p['item_id']);cp=dict(item['checkpoint'])
            target={**cp['target_image'],'item_id':item['item_id']}
            detection=batch_detector.detect(target,cp['reference_image'],{'mode':'diff','blur':False,'min_area_px':1,'open_kernel':1,'close_kernel':1})
            mask=batch_mask.build_mask_result(detection,target,{'path':str(root/'artifacts'/'work'/item['item_id']/'mask.tif')})
            option=root/f'options-{item["ordinal"]}.yml';option.write_text('tiftype: .tif\nscale: 2\n',encoding='utf-8')
            params=run_sr.repair_request({'lq_path':cp['scene_ref'],'mask_path':mask['path'],'suffix':'260318','sr_scale':2,'options_yml':str(option)})
            config=root/f'job-{item["ordinal"]}.xml';config.write_text(run_sr.build_config_xml(params),encoding='utf-8')
            def inference(argv):
                private=Path(ET.parse(argv[-1]).getroot().findtext('DatarootLQ'))
                tifffile.imwrite(private/'PAN_260318.tif',np.full((128,128),99,np.uint16))
                (private/'Debug').mkdir(exist_ok=True)
                (private/'Debug'/'PAN_SRLOG.txt').write_text('Run finished.\n',encoding='utf-8')
            repair_sr.run(config,'synthetic-inference.py',gdal=TinyGdal(),execute=inference)
            fingerprint=run_sr.task_fingerprint(params);jid=8100+item['ordinal']
            store.put_sr_task(fingerprint,params,status='COMPLETED',job_id=jid,config_xml=str(config))
            cp.update(mask=mask,detection=detection,sr_fingerprint=fingerprint,job_id=jid,attempt=1,
                job_status={'state':'COMPLETED','active':False,'exit_code':'0'},phase='VERIFYING')
            item=store.transition_goal_item(goal['goal_id'],step_id=item['step_id'],expected_version=item['version'],status='VERIFYING',
                lease_token=lease.token,checkpoint=cp,external_job_id=jid,capacity_reserved=1)
            from types import SimpleNamespace
            batch_worker._verify_item(SimpleNamespace(store=store),store.get_goal(goal['goal_id']),item,lease,{},'production')
            assert store.get_goal_item(goal['goal_id'],item['item_id'])['status']=='AWAITING_REVIEW'
    finally:
        current_lease.reset(token);store.release_goal(goal['goal_id'],lease.token,lease.version)
    return {'goal_id':goal['goal_id'],'items':[p['item_id'] for p in planned]}


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--root',required=True);parser.add_argument('--port',type=int,required=True);args=parser.parse_args()
    root=Path(args.root).resolve();root.mkdir(parents=True,exist_ok=True)
    os.environ.update({**allowed_roots_env(root),'SR_AGENT_DB':str(root/'app.db'),'SR_BATCH_ARTIFACT_DIR':str(root/'artifacts'),
        'SR_SCENES_ROOT':str(root/'empty'),'SR_EXECUTOR':'local','SR_LLM_MOCK':'1','SR_BATCH_POLL_SEC':'0.1',
        'SR_BATCH_MONITOR_POLL_SEC':'0.1','SR_BATCH_LEASE_SEC':'1','SR_QUEUE_POLL_SEC':'60'})
    from backend.api.app import create_app
    from backend.services import run_sr
    def forbidden(*args,**kwargs):raise AssertionError('quality test MUST NOT submit SR')
    run_sr.submit_run_sr=forbidden
    run_sr.query_job_status=lambda jid,**kw:{'job_id':jid,'state':'COMPLETED','active':False,'exit_code':'0'}
    app=create_app()
    fixtures=root/'fixtures.json'
    if not fixtures.exists():fixtures.write_text(json.dumps(seed(app.state.store,root),ensure_ascii=False),encoding='utf-8')
    from fastapi.responses import FileResponse
    dist=Path(__file__).resolve().parents[2]/'frontend'/'dist'
    @app.get('/{asset:path}')
    def frontend(asset:str):
        path=(dist/asset).resolve()
        if dist not in path.parents or not path.is_file():path=dist/'index.html'
        return FileResponse(path)
    import uvicorn
    uvicorn.run(app,host='127.0.0.1',port=args.port,log_level='warning')


if __name__=='__main__':main()
