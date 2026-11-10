import base64
import asyncio
import io
import os
import tempfile
import time
import unittest
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, Mock, AsyncMock

import numpy as np
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from backend.services.image_open import ImageOpen, MARKER, router_for, purge_loop
from backend.services.heavy_work import HeavyWork
from backend.services.preview_jpg import ensure_preview_jpg
from backend.tests import allowed_roots_env


def fingerprint(path):
    data=path.read_bytes();size=len(data)
    return dict(name=path.name,size=size,samples=[base64.b64encode(data[n:n+4096]).decode()
        for n in (0,max(0,(size-4096)//2),max(0,size-4096))])


class ImageOpenTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.data=self.root/'data';self.data.mkdir()
        env=allowed_roots_env(self.data)
        env.update(SR_IMAGE_CACHE_ROOT=str(self.root/'cache'),SR_PREVIEW_ROOTS='',SR_IMAGE_CACHE_TTL_SEC='300')
        env['SR_DRIVE_MAP']=env.get('SR_DRIVE_MAP','')+f';W:={self.data};X:={self.data}'
        self.env=patch.dict(os.environ,env);self.env.start()
        self.manager=ImageOpen()
        @asynccontextmanager
        async def lifespan(app):
            yield
            await app.state.heavy_work.close()
        app=FastAPI(lifespan=lifespan);app.state.heavy_work=HeavyWork();app.include_router(router_for(self.manager))
        self.client=TestClient(app);self.client.__enter__()
        self.source=self.data/'image.tif';Image.new('I;16',(32,16),7).save(self.source)

    def tearDown(self):
        self.client.__exit__(None,None,None);self.env.stop();self.temp.cleanup()

    def ticket(self):
        return self.client.post('/api/image-open/tickets').json()['token']

    def select(self,token,path=None):
        body=dict(name=self.source.name,size=self.source.stat().st_size,fingerprint=fingerprint(self.source))
        if path:body['path']=path
        response=self.client.post(f'/api/image-open/tickets/{token}/selection',json={'files':[body]})
        self.assertEqual(response.status_code,200,response.text)
        return response.json()['files'][0]

    def test_mapped_source_does_not_upload_or_write_production_directory(self):
        token=self.ticket();entry=self.select(token,'X:\\image.tif')
        self.assertEqual(entry['state'],'ready');self.assertEqual(entry['received'],0)
        asset=entry['asset'];self.assertFalse(asset['uploaded'])
        response=self.client.get(f"/api/image-open/assets/{asset['id']}/preview?div=2")
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.headers['content-type'],'image/jpeg')
        self.assertEqual(Image.open(io.BytesIO(response.content)).size,(16,8))
        self.assertEqual(list(self.data.iterdir()),[self.source])

    def test_status_reports_queue_state_without_starting_preview_or_reading_source_pixels(self):
        asset=self.manager.asset(self.source);ident=asset['id']
        with patch('backend.services.image_open.build_preview_pixels',side_effect=AssertionError('status must not generate')):
            self.client.app.state.heavy_work.states[('image-open',ident,2)]='queued'
            response=self.client.get(f'/api/image-open/assets/{ident}/preview-status?div=2')
            self.assertEqual(response.json(),{'state':'queued'})
            self.assertEqual(self.client.get(f'/api/image-open/assets/{ident}/preview-status?div=3').status_code,400)

    def test_browser_or_unmapped_path_upload_becomes_read_only_cached_jpg(self):
        token=self.ticket();entry=self.select(token,'C:\\Users\\anyone\\image.tif')
        self.assertEqual(entry['state'],'upload')
        data=self.source.read_bytes();split=len(data)//2
        url=f"/api/image-open/tickets/{token}/files/{entry['id']}/chunks"
        self.assertEqual(self.client.put(url+'?offset=0',content=data[:split]).status_code,200)
        self.assertEqual(self.client.put(url+'?offset=0',content=data[split:]).status_code,409)
        self.client.post(f'/api/image-open/tickets/{token}/state',json={'state':'paused'})
        self.assertEqual(self.client.put(url+f'?offset={split}',content=data[split:]).status_code,409)
        self.client.post(f'/api/image-open/tickets/{token}/state',json={'state':'running'})
        response=self.client.put(url+f'?offset={split}',content=data[split:])
        asset=response.json()['files'][0]['asset']
        self.assertTrue(asset['uploaded']);self.assertIsNone(asset['serverPath'])
        self.assertNotIn('sceneId',asset);self.assertNotIn('lqPath',asset)
        preview=self.client.get(f"/api/image-open/assets/{asset['id']}/preview?div=4")
        self.assertEqual(preview.status_code,200,preview.text)
        self.assertEqual(Image.open(io.BytesIO(preview.content)).size,(8,4))

    def test_large_preview_is_bounded_and_same_effective_size_reuses_cache(self):
        with patch('backend.services.image_open.scene_dims',return_value={'W':49474,'H':49454}):
            asset=self.manager.asset(self.source)
        with patch('backend.services.image_open.build_preview_pixels',return_value=np.zeros((4,4),dtype=np.uint8)) as build:
            for div in (1,2):
                response=self.client.get(f"/api/image-open/assets/{asset['id']}/preview?div={div}")
                self.assertEqual(response.status_code,200,response.text)
            build.assert_called_once_with(self.source,max_edge=4096)

    def test_wrong_fingerprint_uploads_instead_of_linking_another_file(self):
        token=self.ticket();fp=fingerprint(self.source);fp['samples'][0]=''
        response=self.client.post(f'/api/image-open/tickets/{token}/selection',json={'files':[
            {'path':'W:\\image.tif','name':self.source.name,'size':self.source.stat().st_size,'fingerprint':fp}]})
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['files'][0]['state'],'upload')

    def test_native_selection_does_not_clear_a_pause_requested_while_dialog_was_open(self):
        token=self.ticket();self.client.post(f'/api/image-open/tickets/{token}/state',json={'state':'paused'})
        self.select(token)
        self.assertEqual(self.client.get(f'/api/image-open/tickets/{token}?brief=true').json()['state'],'paused')

    def test_unicode_path_and_filename_are_preserved(self):
        self.source=self.data/'中文 影像.TIF';Image.new('I;16',(32,16),7).save(self.source)
        entry=self.select(self.ticket(),'X:\\中文 影像.TIF')
        self.assertEqual(entry['state'],'ready');self.assertEqual(entry['asset']['name'],self.source.name)

    def test_windows_unicode_filename_longer_than_linux_leaf_limit_uploads_to_opaque_name(self):
        token=self.ticket();name='影'*200+'.tif';fp=fingerprint(self.source);fp['name']=name
        selected=self.client.post(f'/api/image-open/tickets/{token}/selection',json={'files':[{'name':name,'size':self.source.stat().st_size,'fingerprint':fp}]}).json()['files'][0]
        response=self.client.put(f"/api/image-open/tickets/{token}/files/{selected['id']}/chunks?offset=0",content=self.source.read_bytes())
        asset=response.json()['files'][0]['asset']
        self.assertEqual(asset['name'],name);self.assertEqual(asset['fingerprint']['name'],name)
        self.assertEqual(self.client.get(f"/api/image-open/assets/{asset['id']}/preview").status_code,200)

    def test_expiry_only_removes_owned_inactive_cache(self):
        asset=self.manager.asset(self.source);directory=self.manager.directory(asset['id'])
        other=self.manager.root/('a'*64);other.mkdir();(other/'keep.tif').write_bytes(b'production')
        future=time.time()+self.manager.ttl+1
        with self.manager.protecting(asset['id']):
            self.manager.purge(future);self.assertTrue(directory.exists())
        self.manager.purge(future)
        self.assertFalse(directory.exists());self.assertTrue((other/'keep.tif').exists());self.assertTrue(self.source.exists())

    def test_cancel_and_capabilities_prevent_late_or_cross_ticket_upload(self):
        token=self.ticket();entry=self.select(token)
        other=self.ticket()
        suffix=f"/files/{entry['id']}/chunks?offset=0"
        self.assertEqual(self.client.put('/api/image-open/tickets/'+other+suffix,content=b'x').status_code,409)
        self.client.post(f'/api/image-open/tickets/{token}/state',json={'state':'cancelled'})
        self.assertEqual(self.client.put('/api/image-open/tickets/'+token+suffix,content=b'x').status_code,410)

    def test_source_change_invalidates_cached_preview(self):
        asset=self.manager.asset(self.source)
        self.client.get(f"/api/image-open/assets/{asset['id']}/preview?div=2")
        self.source.write_bytes(self.source.read_bytes()+b'changed')
        self.assertEqual(self.client.get(f"/api/image-open/assets/{asset['id']}/preview?div=2").status_code,409)

    def test_existing_verified_production_jpg_is_reused_without_reading_tiff_pixels(self):
        ensure_preview_jpg(self.source,self.source.with_name('image_preview.jpg'),div=2)
        asset=self.manager.asset(self.source)
        with patch('backend.services.image_open.build_preview_pixels',side_effect=AssertionError('must reuse JPG')):
            response=self.client.get(f"/api/image-open/assets/{asset['id']}/preview?div=2")
            self.assertEqual(response.status_code,200,response.text)
            self.assertEqual(Image.open(io.BytesIO(response.content)).size,(16,8))

    def test_unc_requires_explicit_mapping_and_checks_complete_share_boundary(self):
        with patch.dict(os.environ,{'SR_UNC_MAP':f'\\\\host\\share={self.data}'}):
            self.assertEqual(self.select(self.ticket(),'\\\\host\\share\\image.tif')['state'],'ready')
            self.assertEqual(self.select(self.ticket(),'\\\\host\\share2\\image.tif')['state'],'upload')

    def test_cache_disk_scan_does_not_hold_ticket_status_lock(self):
        token=self.ticket();entry=self.select(token);entered=threading.Event();release=threading.Event()
        def slow_usage():
            entered.set();release.wait(3);return 0
        with patch.object(self.manager,'usage',side_effect=slow_usage),ThreadPoolExecutor(2) as pool:
            upload=pool.submit(self.manager.put,token,entry['id'],0,self.source.read_bytes())
            self.assertTrue(entered.wait(1))
            try:self.assertEqual(pool.submit(self.manager.view,token).result(timeout=1)['state'],'running')
            finally:release.set()
            self.assertEqual(upload.result(timeout=3)['files'][0]['state'],'ready')


class CleanupLoopTests(unittest.IsolatedAsyncioTestCase):
    async def test_transient_cleanup_failure_does_not_stop_periodic_cleanup(self):
        manager=Mock()
        manager.purge.side_effect=[OSError('temporary filesystem failure'),None]
        sleep=AsyncMock(side_effect=[None,asyncio.CancelledError()])
        with patch('backend.services.image_open.asyncio',SimpleNamespace(sleep=sleep)),self.assertLogs('backend.services.image_open',level='ERROR'):
            with self.assertRaises(asyncio.CancelledError):
                await purge_loop(manager)
        self.assertEqual(manager.purge.call_count,2)


if __name__=='__main__':unittest.main()
