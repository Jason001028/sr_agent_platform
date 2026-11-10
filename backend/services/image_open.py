"""Capability-scoped image selection, resumable uploads and expiring JPEG cache.

Preview access never grants scene/SR authority. Production sources are read-only;
only this service's marked cache directories are written or removed.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
import logging
import os
import re
import secrets
import shutil
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, Response
from PIL import Image
from starlette.concurrency import run_in_threadpool

from backend.api import paths
from backend.pathguard import ensure_preview_allowed, to_posix_array_path
from backend.services import preview_cache
from backend.services.preview_jpg import (build_preview_pixels, scene_dims, source_revision,
    cache_meta, versioned_preview_path, PREVIEW_RULE_VERSION)
from backend.services.source_match import verify_source

CHUNK = 4 * 1024 * 1024
MARKER = '.sr-image-open-v1'
SUFFIXES = {'.tif', '.tiff', '.jpg', '.jpeg', '.png'}


class ImageOpen:
    def __init__(self):
        database=os.environ.get('SR_AGENT_DB','')
        default_root=(Path(database).parent/'image-preview-cache') if database and database!=':memory:' and not os.environ.get('SR_TEMP_PREVIEWS_ROOT') else preview_cache.temp_previews_root()/'image-open'
        self.root = Path(os.environ.get('SR_IMAGE_CACHE_ROOT', str(default_root))).resolve()
        if self.root == Path(self.root.anchor):
            raise ValueError('SR_IMAGE_CACHE_ROOT cannot be a filesystem root')
        self.ttl = max(300, int(os.environ.get('SR_IMAGE_CACHE_TTL_SEC', '86400')))
        self.max_file = int(os.environ.get('SR_IMAGE_UPLOAD_MAX_BYTES', str(8 * 1024**3)))
        self.quota = int(os.environ.get('SR_IMAGE_CACHE_MAX_BYTES', str(32 * 1024**3)))
        self.lock = threading.RLock()
        self.quota_lock = threading.Lock()
        self.tickets = {}
        self.active = {}
        self.removing = set()
        self.upload_slots = asyncio.Semaphore(4)

    def directory(self, ident):
        if not re.fullmatch('[a-f0-9]{64}', ident):
            raise HTTPException(404, '缓存标识无效')
        directory = self.root / ident
        if directory.is_symlink() or directory.resolve().parent!=self.root:
            raise HTTPException(403, '缓存目录不能是符号链接')
        return directory

    def ticket(self, token):
        ticket = self.tickets.get(token)
        if not ticket or ticket['expires'] < time.time():
            raise HTTPException(410, '选择会话已过期，请重新选择影像')
        return ticket

    def create(self):
        with self.lock:
            now = time.time()
            self.tickets = {key: value for key, value in self.tickets.items() if value['expires'] > now and value['state'] != 'cancelled'}
            if len(self.tickets) >= 64:
                raise HTTPException(503, '文件选择会话已满，请稍后重试')
            token = secrets.token_hex(32)
            self.tickets[token] = dict(expires=now+7200, state='selecting', files=[])
            return dict(token=token, chunkBytes=CHUNK)

    def view(self, token, brief=False, seen=()):
        with self.lock:
            t = self.ticket(token)
            def visible(file,key):
                if key in ('fingerprint','busy','reserved'):return False
                if brief and key in ('asset','originalPath'):return False
                if key=='asset' and file.get('asset',{}).get('id') in seen:return False
                return True
            return dict(state=t['state'],error=t.get('error'), files=[{key: value for key, value in f.items()
                if visible(f,key)} for f in t['files']])

    def control(self, token, state, error=None):
        if state not in ('paused', 'running', 'cancelled','failed'):
            raise HTTPException(400, '加载状态无效')
        with self.lock:
            t = self.ticket(token)
            if t['state'] == 'cancelled' or (t['state']=='error' and state!='cancelled'):
                return self.view(token)
            t['state'] = 'error' if state == 'failed' else state
            if state == 'failed':
                t['error']=str(error or '本地组件选择或上传失败')[:1000]
                for entry in t['files']:
                    if entry['state'] != 'ready':
                        entry['state']='error';entry['error']=str(error or '本地组件上传失败')[:1000]
            discard=[entry['id'] for entry in t['files'] if not entry.get('asset')] if state=='cancelled' else []
            result=self.view(token)
        for ident in discard:
            self.remove_owned(ident)
        return result

    def save(self, ident, metadata):
        with self.protecting(ident):
            self._save(ident,metadata)

    def _save(self, ident, metadata):
        directory = self.directory(ident)
        if directory.exists() and not (directory/MARKER).is_file():
            raise HTTPException(409, '缓存目录已有非本组件数据')
        directory.mkdir(parents=True, exist_ok=True)
        (directory / MARKER).touch()
        tmp = directory / (secrets.token_hex(8)+'.tmp')
        tmp.write_text(json.dumps(metadata, ensure_ascii=False), encoding='utf-8')
        tmp.replace(directory / 'meta.json')

    def metadata(self, ident):
        directory = self.directory(ident)
        if directory.is_symlink() or not (directory / MARKER).is_file():
            raise HTTPException(404, '预览缓存不存在或已清理，请重新选择影像')
        try:
            meta = json.loads((directory / 'meta.json').read_text(encoding='utf-8'))
        except (OSError, ValueError):
            raise HTTPException(404, '预览缓存不存在或已清理')
        if meta['expires'] < time.time():
            raise HTTPException(410, '预览缓存已过期，请重新选择影像')
        return meta

    def asset(self, path, name=None, uploaded=False, ident=None):
        path = Path(path)
        if path.suffix.lower() not in SUFFIXES:
            raise HTTPException(415, '支持 TIFF、JPG、JPEG、PNG')
        dims = scene_dims(path)
        if not dims:
            # scene_dims intentionally only accepts TIFF in some deployments.
            try:
                with Image.open(path) as im:
                    dims = dict(W=im.width, H=im.height)
            except Exception as exc:
                raise HTTPException(422, f'无法读取影像尺寸：{exc}') from exc
        rev = source_revision(path)
        size = path.stat().st_size
        with path.open('rb') as stream:
            samples = []
            for offset in (0,max(0,(size-4096)//2),max(0,size-4096)):
                stream.seek(offset); samples.append(base64.b64encode(stream.read(4096)).decode())
        if source_revision(path) != rev:
            raise HTTPException(409, '源文件在读取期间变化')
        ident = ident or hashlib.sha256((str(path.resolve())+'|'+str(rev)).encode()).hexdigest()
        meta = dict(id=ident, name=name or path.name, path=str(path.resolve()), uploaded=uploaded,
                    W=dims['W'], H=dims['H'], sourceRev=rev, size=size,
                    fingerprint=dict(name=name or path.name,size=size,samples=samples), expires=time.time()+self.ttl)
        self.save(ident, meta)
        return self.public(meta)

    @staticmethod
    def public(meta):
        return {key: value for key, value in meta.items() if key != 'path'} | {
            'serverPath': None if meta['uploaded'] else meta['path']}

    def resolve(self, body):
        try:
            candidate = paths.scene_id_to_abs(body['sceneId'], paths.scenes_root(), allow_preview=True) if body.get('sceneId') else to_posix_array_path(body.get('path', ''))
            candidate = ensure_preview_allowed(candidate, kind='file')
            if body.get('fingerprint'):
                verify_source(candidate, body['fingerprint'])
        except Exception as exc:
            raise HTTPException(409, f'服务端无法直接读取此文件：{exc}') from exc
        return self.asset(candidate)

    def select(self, token, body):
        with self.lock:
            t = self.ticket(token)
            if t['state'] == 'cancelled':
                raise HTTPException(410, '选择已取消')
            if t['files']:
                raise HTTPException(409, '选择结果已经提交')
            if body.get('cancelled'):
                return self.control(token, 'cancelled')
            choices = body.get('files')
            if not isinstance(choices, list) or not 1 <= len(choices) <= 16:
                raise HTTPException(400, '一次可选择 1–16 张影像')
            files = []
            for choice in choices:
                if not isinstance(choice, dict):
                    raise HTTPException(400, '选择结果无效')
                name, size = choice.get('name', ''), choice.get('size', 0)
                if not isinstance(name, str) or len(name) > 255 or re.search(r'[\\/\x00-\x1f:]', name) or Path(name).suffix.lower() not in SUFFIXES:
                    raise HTTPException(400, '文件名或影像类型无效')
                if not isinstance(size, int) or not 0 < size < 2**53:
                    raise HTTPException(413, '文件大小无效')
                files.append(dict(id=secrets.token_hex(32), name=name, size=size, received=0, state='resolving',
                                  fingerprint=choice.get('fingerprint'), originalPath=choice.get('path')))
            t['files'] = files
            if t['state']!='paused':t['state'] = 'running'
        for entry in files:
            if entry['originalPath']:
                try:
                    if not entry['fingerprint']:
                        raise ValueError('缺少源内容核对')
                    entry['asset'] = self.resolve(dict(path=entry['originalPath'], fingerprint=entry['fingerprint']))
                    entry['state'] = 'ready'
                    continue
                except HTTPException as exc:
                    if exc.status_code != 409:
                        entry['state']='error';entry['error']=str(exc.detail);continue
                except ValueError:
                    pass
            entry['state'] = 'upload' if entry['size']<=self.max_file else 'error'
            if entry['state']=='error':
                entry['error']='服务端无法直读，且文件超出临时上传大小上限'
        return self.view(token)

    def put(self, token, ident, offset, data):
        with self.lock:
            t = self.ticket(token)
            if t['state'] == 'cancelled':
                raise HTTPException(410, '上传已取消')
            if t['state'] == 'paused':
                raise HTTPException(409, '上传已暂停')
            entry = next((f for f in t['files'] if f['id'] == ident), None)
            if not entry or entry.get('busy') or entry['state'] != 'upload' or offset != entry['received']:
                raise HTTPException(409, '上传偏移或文件状态已变化')
            if not data or offset+len(data) > entry['size']:
                raise HTTPException(400, '上传数据长度无效')
            entry['busy']=True
        try:
            return self._put(t,entry,token,ident,offset,data)
        finally:
            with self.lock:
                entry['busy']=False
                cancelled=t['state']=='cancelled'
            if cancelled:
                self.remove_owned(ident)

    def usage(self):
        if not self.root.exists():return 0
        used=0
        for directory in self.root.iterdir():
            if directory.is_symlink() or not directory.is_dir() or not (directory/MARKER).is_file():continue
            for folder in (directory,directory/'source'):
                try:
                    if folder.is_symlink() or not folder.is_dir():continue
                    for item in folder.iterdir():
                        if not item.is_symlink() and item.is_file():used+=item.stat().st_size
                except OSError:
                    continue  # Another cancelled/expired upload may have disappeared.
        return used

    def _put(self,t,entry,token,ident,offset,data):
        directory = self.directory(ident)
        if directory.exists() and not (directory/MARKER).is_file():
            raise HTTPException(409, '缓存目录已有非本组件数据')
        directory.mkdir(parents=True, exist_ok=True)
        (directory / MARKER).touch()
        if not entry.get('reserved'):
            with self.quota_lock:
                used=self.usage()
                free=shutil.disk_usage(self.root).free
                with self.lock:
                    remaining = sum(f['size']-f['received'] for ticket in self.tickets.values() for f in ticket['files']
                                    if f.get('reserved') and f['state']=='upload' and ticket['state']!='cancelled')
                    if used+remaining+entry['size'] > self.quota:
                        raise HTTPException(507, '临时预览缓存空间已满，请稍后重试')
                    if remaining+entry['size']+65536>free:
                        raise HTTPException(507,'服务端缓存磁盘空间不足')
                    entry['reserved'] = True
        source_dir = directory / 'source'
        if source_dir.is_symlink() or source_dir.resolve().parent!=directory.resolve():
            raise HTTPException(403, '上传目录不能是符号链接')
        source_dir.mkdir(exist_ok=True)
        part = source_dir / 'upload.part'
        with part.open('ab') as stream:
            if stream.tell() != offset:
                raise HTTPException(409, '上传文件偏移不一致')
            stream.write(data)
        with self.quota_lock:
            with self.lock:
                if t['state']=='cancelled':raise HTTPException(410,'上传已取消')
                entry['received'] += len(data)
        if entry['received'] == entry['size']:
            source = source_dir / ('source'+Path(entry['name']).suffix.lower())
            part.replace(source)
            try:
                if not isinstance(entry['fingerprint'],dict) or entry['fingerprint'].get('name')!=entry['name']:
                    raise ValueError('上传文件名与采样声明不符')
                verify_source(source, {**entry['fingerprint'],'name':source.name})
                asset = self.asset(source, entry['name'], uploaded=True, ident=ident)
                with self.lock:
                    if t['state']=='cancelled':raise HTTPException(410,'上传已取消')
                    entry['asset']=asset;entry['state'] = 'ready'
            except Exception as exc:
                entry['state'] = 'error'; entry['error'] = str(exc)
        return self.view(token)

    @contextmanager
    def protecting(self, ident):
        with self.lock:
            if ident in self.removing:
                raise HTTPException(410,'缓存正在清理，请重新选择')
            self.active[ident] = self.active.get(ident,0)+1
        try:
            yield
        finally:
            with self.lock:
                self.active[ident] -= 1
                if not self.active[ident]:
                    del self.active[ident]

    def preview(self, ident, div, ready_only=False):
        with self.protecting(ident):
            try:
                return self._preview(ident,div,ready_only)
            except HTTPException:
                raise
            except Exception as exc:
                raise HTTPException(422, f'服务端 JPG 生成失败：{exc}') from exc

    def _preview(self, ident, div, ready_only):
        started=time.monotonic();sample_ms=0
        from backend.services.preview_jpg import PREVIEW_DIVISORS
        if div not in PREVIEW_DIVISORS:
            raise HTTPException(400, '预览档位无效')
        meta = self.metadata(ident)
        source = Path(meta['path'])
        if not meta['uploaded']:
            source = ensure_preview_allowed(source, kind='file')
        elif source.parent != self.directory(ident)/'source' or source.is_symlink() or source.parent.is_symlink() or source.parent.resolve().parent!=self.directory(ident).resolve():
            raise HTTPException(403, '上传源路径无效')
        rev = source_revision(source)
        if rev != meta['sourceRev']:
            raise HTTPException(409, '源文件已变化，请重新选择')
        edge = min(4096, max(1, round(max(meta['W'],meta['H'])/div)))
        jpg = self.directory(ident) / f'preview-v1-{PREVIEW_RULE_VERSION}-{edge}.jpg'
        cache_kind='hit' if jpg.is_file() else 'miss'
        if jpg.is_symlink():
            raise HTTPException(403, '预览缓存不能是符号链接')
        if not jpg.is_file() and not meta['uploaded']:
            # Reuse already-baked production previews by immutable byte snapshot;
            # never re-read TIFF just to move the same JPG into an expiring cache.
            candidates=[]
            try:
                base=paths.preview_jpg_for(source,paths.scenes_root())
                candidates.extend((versioned_preview_path(base,rev,div),base))
                drop=paths.drop_preview_path(source)
                candidates.extend((versioned_preview_path(drop,rev,div),drop))
            except Exception:
                pass
            scale=min(1,edge/max(meta['W'],meta['H']))
            expected=(max(1,round(meta['W']*scale)),max(1,round(meta['H']*scale)))
            for candidate in dict.fromkeys(candidates):
                state=cache_meta(candidate,source_rev=rev)
                if not state or (state['w'],state['h'])!=expected:continue
                data=candidate.read_bytes()
                checked=cache_meta(io.BytesIO(data),source_rev=rev)
                if checked and (checked['w'],checked['h'])==expected and source_revision(source)==rev:
                    temp=jpg.with_name(jpg.name+'.'+secrets.token_hex(8)+'.tmp')
                    temp.write_bytes(data);temp.replace(jpg);cache_kind='production-reuse';break
        if ready_only and not jpg.is_file():
            elapsed=(time.monotonic()-started)*1000
            if elapsed>2000:logging.getLogger(__name__).warning('image_preview cache=miss check_ms=%.0f edge=%d div=%d',elapsed,edge,div)
            return None
        if not jpg.is_file():
            sampling=time.monotonic()
            if source.suffix.lower() in ('.tif','.tiff'):
                pixels = build_preview_pixels(source, max_edge=edge)
                image = Image.fromarray(pixels)
            else:
                with Image.open(source) as original:
                    original.draft('RGB',(edge,edge)); original.thumbnail((edge,edge))
                    image = original.convert('RGB')
            sample_ms=(time.monotonic()-sampling)*1000;cache_kind='generated'
            buf = io.BytesIO(); image.save(buf,format='JPEG',quality=85)
            if source_revision(source) != rev:
                raise HTTPException(409, '源文件在预览生成期间变化')
            temporary = jpg.with_suffix('.tmp'); temporary.write_bytes(buf.getvalue()); temporary.replace(jpg)
        data = jpg.read_bytes()
        if source_revision(source) != rev:
            raise HTTPException(409, '源文件已变化，请重新选择')
        with Image.open(io.BytesIO(data)) as result:
            headers = {'X-SR-Image-W':str(meta['W']), 'X-SR-Image-H':str(meta['H']),
                       'X-SR-Preview-W':str(result.width), 'X-SR-Preview-H':str(result.height),
                       'Cache-Control':'no-store'}
        elapsed=(time.monotonic()-started)*1000
        logging.getLogger(__name__).log(logging.WARNING if elapsed>2000 else logging.INFO,
            'image_preview cache=%s elapsed_ms=%.0f sample_ms=%.0f edge=%d div=%d bytes=%d',cache_kind,elapsed,sample_ms,edge,div,len(data))
        return Response(data,media_type='image/jpeg',headers=headers)

    def purge(self, now=None):
        now = time.time() if now is None else now
        if not self.root.exists():
            return
        with self.lock:
            protected = set(self.active)
            for t in self.tickets.values():
                if t['expires'] > now and t['state'] != 'cancelled':
                    protected.update(f['id'] for f in t['files'] if f['state'] == 'upload')
        for directory in self.root.iterdir():
            if not re.fullmatch('[a-f0-9]{64}',directory.name) or directory.is_symlink() or not (directory/MARKER).is_file() or directory.name in protected:
                continue
            try:
                expiry = json.loads((directory/'meta.json').read_text(encoding='utf-8'))['expires']
            except (OSError,ValueError,KeyError):
                expiry = (directory/MARKER).stat().st_mtime+self.ttl
            if expiry < now:
                self.remove_owned(directory.name)

    def remove_owned(self,ident):
        with self.lock:
            if ident in self.active or ident in self.removing or any(f['id']==ident and f.get('busy') for t in self.tickets.values() for f in t['files']):
                return
            self.removing.add(ident)
        try:
            directory=self.directory(ident)
            if directory.is_dir() and (directory/MARKER).is_file() and directory.resolve().parent==self.root:
                shutil.rmtree(directory)
        finally:
            with self.lock:self.removing.discard(ident)


def router_for(manager: ImageOpen):
    router = APIRouter(prefix='/api/image-open')

    async def body(request):
        data = bytearray()
        async for chunk in request.stream():
            data.extend(chunk)
            if len(data) > 512*1024:
                raise HTTPException(413, '选择信息过大')
        try:
            value = json.loads(data)
            if not isinstance(value,dict):
                raise ValueError()
            return value
        except (ValueError,TypeError):
            raise HTTPException(400, '选择信息无效')

    @router.post('/tickets')
    def create():
        return manager.create()

    @router.get('/tickets/{token}')
    def status(token: str,brief:bool=False,seen:str=''):
        ids=seen.split(',') if seen else []
        if len(ids)>16 or any(not re.fullmatch('[a-f0-9]{64}',ident) for ident in ids):
            raise HTTPException(400,'缓存标识无效')
        return manager.view(token,brief,ids)

    @router.post('/tickets/{token}/state')
    async def control(token: str, request: Request):
        value = await body(request)
        return await run_in_threadpool(manager.control,token,value.get('state'),value.get('error'))

    @router.post('/tickets/{token}/selection')
    async def select(token: str, request: Request):
        value = await body(request)
        return await request.app.state.heavy_work.resolve(manager.select,token,value)

    @router.put('/tickets/{token}/files/{ident}/chunks')
    async def upload(token: str, ident: str, request: Request, offset: int = 0, brief:bool=False):
        manager.directory(ident)
        await run_in_threadpool(manager.view,token)
        async with manager.upload_slots:
            data = bytearray()
            async for chunk in request.stream():
                data.extend(chunk)
                if len(data) > CHUNK:
                    raise HTTPException(413, '每块最多 4 MiB')
            result=await run_in_threadpool(manager.put,token,ident,offset,data)
            if brief:
                for file in result['files']:file.pop('asset',None);file.pop('originalPath',None)
            return result

    @router.post('/assets')
    async def resolve(request: Request):
        return await request.app.state.heavy_work.resolve(manager.resolve,await body(request))

    @router.get('/assets/{ident}/preview')
    async def preview(ident: str, request: Request, div: int = 2):
        cached = await request.app.state.heavy_work.read(manager.preview,ident,div,True)
        if cached is not None:
            return cached
        # A queued/shared raster job owns its cache lease even after this HTTP
        # consumer disconnects. Cleanup may not delete its uploaded source.
        lease=manager.protecting(ident);lease.__enter__()
        task=asyncio.create_task(request.app.state.heavy_work.run(manager.preview,ident,div,key=('image-open',ident,div)))
        def finished(future):
            lease.__exit__(None,None,None)
            if not future.cancelled():future.exception()
        task.add_done_callback(finished)
        return await asyncio.shield(task)

    @router.get('/assets/{ident}/preview-status')
    async def preview_status(ident: str, request: Request, div: int = 2):
        from backend.services.preview_jpg import PREVIEW_DIVISORS
        if div not in PREVIEW_DIVISORS:raise HTTPException(400,'预览档位无效')
        await run_in_threadpool(manager.metadata,ident)
        return {'state':request.app.state.heavy_work.states.get(('image-open',ident,div),'checking')}

    return router


async def purge_loop(manager):
    while True:
        try:
            await run_in_threadpool(manager.purge)
        except Exception:
            logging.getLogger(__name__).exception('image preview cache cleanup failed; retrying next cycle')
        await asyncio.sleep(300)
