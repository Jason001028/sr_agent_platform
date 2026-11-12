"""Synthetic TIFF HTTP harness. No real SR; run from repository root."""
import base64
import io
import os
from pathlib import Path
import sys
import tempfile

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import numpy as np
from PIL import Image
from scipy.ndimage import gaussian_filter
import tifffile
import uvicorn

from backend.tests import allowed_roots_env

work=Path(tempfile.mkdtemp(prefix='smart-compare-e2e-'))
scene=work/'SMART_SCENE';scene.mkdir()
(scene/'SMART_SCENE_meta.xml').write_text('<meta><SolarAzimuth/></meta>',encoding='utf-8')
reference=np.random.default_rng(841).uniform(18000,52000,(192,192)).astype(np.uint16)
from backend.services.image_diff import resize_to
target,_=resize_to((384,384),reference,np.ones(reference.shape,bool))
target[100:280,100:280]=gaussian_filter(target,3)[100:280,100:280]
paths=[scene/'PAN.tif',scene/'SMART_SCENE_NOSR.tif']
for p,a in zip(paths,(target.astype(np.uint16),reference)):tifffile.imwrite(p,a,rowsperstrip=16)
os.environ.update({**allowed_roots_env(work),'SR_SCENES_ROOT':str(work),'SR_AGENT_DB':str(work/'api.db'),
    'SR_LLM_MOCK':'1','SR_SLURM_FAKE':'1','SR_GOAL_WORKER':'0','SR_BATCH_ARTIFACT_DIR':str(work/'artifacts')})
from backend.api.app import create_app
from backend.api.paths import scene_id_abs
from backend.services.preview_jpg import source_revision
app=create_app()
fixtures=[]
for i,(p,a) in enumerate(zip(paths,(target,reference))):
    buf=io.BytesIO();Image.fromarray(np.clip((a-18000)/34000*255,0,255).astype('uint8')).save(buf,format='JPEG',quality=95)
    fixtures.append({'meta':{'name':p.name,'W':a.shape[1],'H':a.shape[0],'sceneId':scene_id_abs(p),
        'lqPath':scene.as_posix() if i==0 else None,'sceneDir':scene.as_posix(),
        'stageKind':'input' if i==0 else 'nosr','srcRev':source_revision(p)},
        'preview':'data:image/jpeg;base64,'+base64.b64encode(buf.getvalue()).decode()})
@app.get('/_smart_fixture')
def fixture():return fixtures
if __name__=='__main__':uvicorn.run(app,host='127.0.0.1',port=int(sys.argv[1]) if len(sys.argv)>1 else 8767)
