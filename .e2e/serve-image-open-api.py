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
import backend.services.image_open as image_module
from backend.tests import allowed_roots_env

fixture=root/'.e2e/fixtures/image-open-api';data=fixture/'data';data.mkdir(parents=True,exist_ok=True)
source=data/'native-source.tif'
Image.fromarray(np.arange(2300*2300,dtype=np.uint16).reshape(2300,2300)).save(source)
env=allowed_roots_env(data);env.update(SR_IMAGE_CACHE_ROOT=str(fixture/'cache'),SR_PREVIEW_ROOTS='')
env['SR_DRIVE_MAP']=env.get('SR_DRIVE_MAP','')+f';W:={data};X:={data}'
os.environ.update(env)
@asynccontextmanager
async def lifespan(app):
    yield
    await app.state.heavy_work.close()
app=FastAPI(lifespan=lifespan);app.state.heavy_work=HeavyWork();app.state.image_open=ImageOpen()
app.add_middleware(CORSMiddleware,allow_origins=['*'],allow_methods=['*'],allow_headers=['*'],expose_headers=['X-SR-Image-W','X-SR-Image-H','X-SR-Preview-W','X-SR-Preview-H'])
app.include_router(router_for(app.state.image_open))
metrics={'decodes':0};build=image_module.build_preview_pixels
def counted(*args,**kwargs):metrics['decodes']+=1;return build(*args,**kwargs)
image_module.build_preview_pixels=counted
@app.get('/fixture/source.tif')
def image():return FileResponse(source,media_type='image/tiff')
@app.get('/fixture/stats')
def stats():return {**metrics,'sourceFiles':[p.name for p in data.iterdir()]}
@app.post('/api/scenes/resolve')
def no_business_scene():raise HTTPException(404,'fixture: source has no production scene')
uvicorn.run(app,host='127.0.0.1',port=8766,log_level='warning')
