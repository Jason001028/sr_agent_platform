"""Actual image-open API fixture; no SR, queue or production paths."""
import os
import sys
from pathlib import Path
from contextlib import asynccontextmanager

root=Path(__file__).resolve().parents[1];sys.path.insert(0,str(root))
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from PIL import Image
import numpy as np
import uvicorn
from backend.services.heavy_work import HeavyWork
from backend.services.image_open import ImageOpen,router_for,purge_loop
from backend.services.image_tiles import TILE_HEADERS
from backend.services.preview_jpg import source_revision
from backend.services.source_match import verify_source
from backend.api.paths import scene_id_abs
import backend.services.image_open as image_module
from backend.tests import allowed_roots_env

fixture=root/'.e2e/fixtures/image-open-api';data=fixture/'data';data.mkdir(parents=True,exist_ok=True)
source=data/'native-source.tif'
Image.fromarray(np.arange(2300*2300,dtype=np.uint16).reshape(2300,2300)).save(source)
large = Path(os.environ['SR_E2E_TILE_SOURCE']).resolve() if os.environ.get('SR_E2E_TILE_SOURCE') else None
env=allowed_roots_env(data, *([large.parent] if large else []));env.update(
    SR_IMAGE_CACHE_ROOT=os.environ.get('SR_E2E_IMAGE_CACHE',str(fixture/'cache')),SR_PREVIEW_ROOTS='')
env['SR_DRIVE_MAP']=env.get('SR_DRIVE_MAP','')+f';W:={data};X:={data}'
os.environ.update(env)
@asynccontextmanager
async def lifespan(app):
    yield
    await app.state.image_open.tiles.close()
    await app.state.heavy_work.close()
app=FastAPI(lifespan=lifespan);app.state.heavy_work=HeavyWork();app.state.image_open=ImageOpen()
app.add_middleware(CORSMiddleware,allow_origins=['*'],allow_methods=['*'],allow_headers=['*'],expose_headers=['X-SR-Image-W','X-SR-Image-H','X-SR-Preview-W','X-SR-Preview-H','X-SR-Preview-Plan',*TILE_HEADERS])


@app.middleware('http')
async def fixture_resource_timing(request, call_next):
    response = await call_next(request)
    response.headers['Timing-Allow-Origin'] = '*'
    return response


app.include_router(router_for(app.state.image_open))
metrics={'decodes':0};build=image_module.build_preview_pixels
def counted(*args,**kwargs):metrics['decodes']+=1;return build(*args,**kwargs)
image_module.build_preview_pixels=counted
@app.get('/fixture/source.tif')
def image():return FileResponse(source,media_type='image/tiff')
@app.get('/fixture/stats')
def stats():return {**metrics,'sourceFiles':[p.name for p in data.iterdir()]}
@app.get('/fixture/tile-scenes')
def tile_scenes():
    if large is None:
        raise HTTPException(404, 'Set SR_E2E_TILE_SOURCE to the benchmark Strip TIFF')
    return [dict(id=scene_id_abs(path),name=path.name,W=w,H=h,hasPreview=False,lq_path=None)
            for path,w,h in ((large,49475,49454),(source,2300,2300))]
@app.get('/fixture/audit-scenes')
def audit_scenes():
    if large is None:
        raise HTTPException(404, 'Set SR_E2E_TILE_SOURCE to a fixture in the audit directory')
    return [dict(id=scene_id_abs(path),name=path.name,W=w,H=h,hasPreview=False,lq_path=None)
            for path,w,h in [(source,2300,2300), *((p,49475,49454) for p in large.parent.glob('*.tif'))]]
@app.get('/fixture/memory')
def memory():
    import psutil
    return dict(rss=psutil.Process().memory_info().rss, pngBytes=app.state.image_open.tiles.tile_bytes)
@app.post('/api/scenes/resolve')
def no_business_scene(body: dict):
    if large and os.environ.get("SR_E2E_AUDIT_RESOLVE") == "1" and body.get("name") == large.name:
        try:
            verify_source(large, body.get("source_fingerprint"))
        except ValueError as error:
            raise HTTPException(409, str(error)) from error
        return dict(row=dict(id=scene_id_abs(large),name=large.name,W=49475,H=49454,srcRev=source_revision(large)),
                    resolved=dict(dir=str(large.parent),sr_capable=False,preview_only=True,read_only=True,kind="input"))
    raise HTTPException(404,'fixture: source has no production scene')
uvicorn.run(app,host='127.0.0.1',port=8766,log_level='warning')
