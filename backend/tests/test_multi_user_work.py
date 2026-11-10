"""Concurrent requests share one ASGI loop; gates control native I/O."""
import asyncio
import io
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

import httpx
import numpy as np
import tifffile
from PIL import Image

from backend.api.app import create_app
from backend.api.paths import scene_id_abs, preview_jpg_for
from backend.services import preview_jpg as pj
from backend.services.heavy_work import HeavyWork
from backend.tests import allowed_roots_env


class WorkQueueTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancelled_database_wait_drains_before_store_shutdown(self):
        from backend.services.async_work import run_sync
        entered, release, exited = threading.Event(), threading.Event(), threading.Event()
        def database_call():
            entered.set()
            assert release.wait(10)
            exited.set()
        task = asyncio.create_task(run_sync(database_call))
        try:
            self.assertTrue(await asyncio.to_thread(entered.wait, 5))
            task.cancel()
            barrier = asyncio.Event()
            asyncio.get_running_loop().call_soon(barrier.set)
            await barrier.wait()
            self.assertFalse(task.done())
        finally:
            release.set()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(exited.is_set())

    async def test_cancelled_owner_failure_and_following_work(self):
        work = HeavyWork(limit=2)
        entered, release = threading.Event(), threading.Event()
        calls = []

        def slow():
            calls.append(1)
            entered.set()
            if not release.wait(10):
                raise AssertionError("test gate expired")
            raise ValueError("failed job")

        first = asyncio.create_task(work.run(slow, key="same"))
        try:
            self.assertTrue(await asyncio.to_thread(entered.wait, 5))
            second = asyncio.create_task(work.run(slow, key="same"))
            # An event-loop barrier, not a timing-dependent sleep.
            barrier = asyncio.Event()
            asyncio.get_running_loop().call_soon(barrier.set)
            await barrier.wait()
            first.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await first
            self.assertEqual(work.active, 1)
            following = asyncio.create_task(work.run(lambda: 42))
            release.set()
            with self.assertRaises(ValueError):
                await second
            self.assertEqual(await following, 42)
            self.assertEqual(calls, [1])
            self.assertEqual(await work.run(lambda: 7, key="same"), 7)
        finally:
            release.set()
            await work.close()
        self.assertEqual(work.snapshot()["active"], 0)

    async def test_bounded_queue_and_background_progress(self):
        work = HeavyWork(limit=8)
        entered, release = threading.Event(), threading.Event()
        order = []
        def slow():
            entered.set()
            assert release.wait(10)
        running = asyncio.create_task(work.run(slow))
        try:
            self.assertTrue(await asyncio.to_thread(entered.wait, 5))
            bg = asyncio.create_task(work.run(lambda: order.append("bg"), background=True))
            front = [asyncio.create_task(work.run(lambda i=i: order.append(i))) for i in range(7)]
            barrier = asyncio.Event()
            asyncio.get_running_loop().call_soon(barrier.set)
            await barrier.wait()
            with self.assertRaises(Exception) as err:
                await work.run(lambda: None)
            self.assertEqual(err.exception.status_code, 503)
            release.set()
            await asyncio.gather(running, bg, *front)
            self.assertLessEqual(order.index("bg"), 4)
        finally:
            release.set()
            await work.close()


class PreviewConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.env = mock.patch.dict(os.environ, {
            **allowed_roots_env(self.root), "SR_SCENES_ROOT": str(self.root),
            "SR_AGENT_DB": str(self.root / "test.sqlite"), "SR_LLM_MOCK": "1",
            "SR_PREVIEWS_ROOT": "", "SR_PREVIEW_ROOTS": ""})
        self.env.start()
        self.source = self.root / "a.tif"
        tifffile.imwrite(self.source, np.arange(8192, dtype=np.uint16).reshape(64, 128))
        self.app = create_app()
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://test")
        self.url = f"/api/scenes/{scene_id_abs(self.source)}/preview"

    async def asyncTearDown(self):
        await self.client.aclose()
        await self.app.state.heavy_work.close()
        self.app.state.store.close()
        self.env.stop()
        self.tmp.cleanup()

    async def test_slow_preview_keeps_queries_controls_and_cached_image_responsive(self):
        # Build a different tier before blocking the raster worker.
        self.assertEqual((await self.client.get(self.url + "?div=8")).status_code, 200)
        entered, release = threading.Event(), threading.Event()
        original = pj.build_preview_pixels
        def slow(*args, **kwargs):
            entered.set()
            assert release.wait(10), "native I/O gate expired"
            return original(*args, **kwargs)
        with mock.patch.object(pj, "build_preview_pixels", side_effect=slow) as build:
            first = asyncio.create_task(self.client.get(self.url + "?div=4"))
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 5))
                others = [asyncio.create_task(self.client.get(self.url + "?div=4")) for _ in range(6)]
                for endpoint in ("/api/health", "/api/chat/sessions", "/api/queue", "/api/work-status", self.url + "?div=8"):
                    result = await asyncio.wait_for(self.client.get(endpoint), 5)
                    self.assertEqual(result.status_code, 200, result.text)
                control = await asyncio.wait_for(self.client.post("/api/scenes/restore-hidden", json={}), 5)
                self.assertEqual(control.status_code, 200, control.text)
                self.assertFalse(first.done())
            finally:
                release.set()
            responses = await asyncio.gather(first, *others)
            self.assertTrue(all(r.status_code == 200 for r in responses))
            self.assertTrue(all(r.content == responses[0].content for r in responses))
            self.assertEqual(build.call_count, 1)
        self.assertGreater(self.app.state.heavy_work.deduplicated, 0)

    async def test_many_slow_resolves_do_not_saturate_ordinary_request_pool(self):
        from backend.api import app as app_module
        entered, release = threading.Event(), threading.Event()
        original = app_module._cached_dims
        active = 0
        peak = 0
        guard = threading.Lock()
        def slow(path):
            nonlocal active, peak
            with guard:
                active += 1
                peak = max(peak, active)
            entered.set()
            assert release.wait(10)
            try:
                return original(path)
            finally:
                with guard:
                    active -= 1
        with mock.patch.object(app_module, "_cached_dims", side_effect=slow):
            tasks = [asyncio.create_task(self.client.post("/api/scenes/resolve", json={"path": str(self.source)}))
                     for _ in range(44)]
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 5))
                for endpoint in ("/api/health", "/api/chat/sessions", "/api/work-status"):
                    self.assertEqual((await asyncio.wait_for(self.client.get(endpoint), 5)).status_code, 200)
                self.assertLessEqual(peak, 2)
            finally:
                release.set()
            results = await asyncio.gather(*tasks)
            self.assertTrue(all(r.status_code == 200 for r in results))

    async def test_background_database_waits_do_not_block_page_requests(self):
        from backend.services.goal_worker import goal_tick
        from backend.services.batch_worker import batch_tick
        entered, release = threading.Event(), threading.Event()
        guard = threading.Lock()
        count = 0
        def slow_list(**kwargs):
            nonlocal count
            with guard:
                count += 1
                if count == 2:
                    entered.set()
            assert release.wait(10)
            return []
        with mock.patch.object(self.app.state.store, "list_goals", side_effect=slow_list):
            tasks = [asyncio.create_task(goal_tick(self.app.state)), asyncio.create_task(batch_tick(self.app.state))]
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 5))
                for endpoint in ("/api/health", "/api/chat/sessions"):
                    self.assertEqual((await asyncio.wait_for(self.client.get(endpoint), 5)).status_code, 200)
            finally:
                release.set()
                await asyncio.gather(*tasks)

    async def test_tiers_and_source_versions_do_not_replace_each_other(self):
        with mock.patch.object(pj, "build_preview_pixels", wraps=pj.build_preview_pixels) as build:
            a = await self.client.get(self.url + "?div=4")
            b = await self.client.get(self.url + "?div=8")
            again = await self.client.get(self.url + "?div=4")
            self.assertEqual(a.content, again.content)
            self.assertEqual(build.call_count, 2)
            self.assertNotEqual(a.content, b.content)
            tifffile.imwrite(self.source, np.zeros((128, 256), dtype=np.uint16))
            updated = await self.client.get(self.url + "?div=4")
            self.assertEqual(updated.status_code, 200)
            with Image.open(io.BytesIO(updated.content)) as im:
                self.assertEqual(im.size, (64, 32))
            self.assertEqual(build.call_count, 3)

    async def test_source_change_during_generation_is_rejected_and_retry_succeeds(self):
        original = pj.build_preview_pixels
        def changed(*args, **kwargs):
            pixels = original(*args, **kwargs)
            tifffile.imwrite(self.source, np.zeros((128, 256), dtype=np.uint16))
            return pixels
        with mock.patch.object(pj, "build_preview_pixels", side_effect=changed):
            response = await self.client.get(self.url + "?div=4")
        self.assertEqual(response.status_code, 422)
        retry = await self.client.get(self.url + "?div=4")
        self.assertEqual(retry.status_code, 200)

    async def test_versioned_previews_are_cleared_by_existing_guarded_clear(self):
        from backend.services.preview_clear import clear_dir_previews
        await self.client.get(self.url + "?div=4")
        await self.client.get(self.url + "?div=8")
        result = clear_dir_previews(self.root)
        self.assertEqual(len(result["removed"]), 3)
        self.assertTrue(self.source.is_file())

    async def test_generic_cleanup_only_removes_old_signed_variants(self):
        from backend.services.preview_cache import purge_generic_versions
        await self.client.get(self.url + "?div=4")
        variant = next(self.root.glob("v_*_preview.jpg"))
        os.utime(variant, (1, 1))
        unsigned = self.root / ("v_" + "a" * 24 + "_preview.jpg")
        unsigned.write_bytes(b"user data")
        os.utime(unsigned, (1, 1))
        self.assertEqual(purge_generic_versions(self.root), 1)
        self.assertFalse(variant.exists())
        self.assertTrue(unsigned.exists())
        self.assertTrue(self.source.exists())

    async def test_http_cancel_keeps_generation_owned_and_status_visible(self):
        entered, release = threading.Event(), threading.Event()
        original = pj.build_preview_pixels
        def slow(*args, **kwargs):
            entered.set()
            assert release.wait(10)
            return original(*args, **kwargs)
        with mock.patch.object(pj, "build_preview_pixels", side_effect=slow) as build:
            first = asyncio.create_task(self.client.get(self.url + "?div=4"))
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 5))
                status = await self.client.get(self.url.replace("/preview", "/preview-status") + "?div=4")
                self.assertEqual(status.json()["state"], "running")
                first.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await first
                second = asyncio.create_task(self.client.get(self.url + "?div=4"))
                self.assertEqual(self.app.state.heavy_work.active, 1)
            finally:
                release.set()
            self.assertEqual((await second).status_code, 200)
            self.assertEqual(build.call_count, 1)
        status = await self.client.get(self.url.replace("/preview", "/preview-status") + "?div=4")
        self.assertEqual(status.json()["state"], "ready")

    async def test_background_and_interactive_share_generated_tier(self):
        entered, release = threading.Event(), threading.Event()
        original = pj.build_preview_pixels
        def slow(*args, **kwargs):
            entered.set()
            assert release.wait(10)
            return original(*args, **kwargs)
        work = self.app.state.heavy_work
        work.loop = asyncio.get_running_loop()
        executor = work.background_executor()
        with mock.patch.object(pj, "build_preview_pixels", side_effect=slow) as build:
            background = asyncio.wrap_future(executor.submit(
                pj.ensure_preview_jpg, self.source, preview_jpg_for(self.source, self.root), 4))
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 5))
                opening = asyncio.create_task(self.client.get(self.url + "?div=4"))
                self.assertEqual((await self.client.get("/api/health")).status_code, 200)
            finally:
                release.set()
            await background
            self.assertEqual((await opening).status_code, 200)
            self.assertEqual(build.call_count, 1)

    async def test_mirror_and_drop_destinations_reuse_one_generation(self):
        mirror = self.root / "previews"
        with mock.patch.dict(os.environ, {"SR_PREVIEWS_ROOT": str(mirror)}), \
                mock.patch.object(pj, "build_preview_pixels", wraps=pj.build_preview_pixels) as build:
            first = await self.client.get(self.url + "?div=4")
            dropped = await self.client.get(self.url.replace("/preview", "/preview-drop") + "?div=4")
            self.assertEqual(first.status_code, 200, first.text)
            self.assertEqual(dropped.status_code, 200, dropped.text)
            self.assertEqual(first.content, dropped.content)
            self.assertEqual(build.call_count, 1)

    async def test_warm_drop_and_temporary_fallback_bypass_busy_raster_queue(self):
        from backend.api import app as app_module
        for temporary in (False, True):
            # Use a new revision so each subcase starts cold.
            tifffile.imwrite(self.source, np.full((64, 128), int(temporary), dtype=np.uint16))
            drop = self.url.replace('/preview', '/preview-drop') + '?div=4'
            entered, release = threading.Event(), threading.Event()
            def slow():
                entered.set()
                assert release.wait(10)
            with mock.patch.dict(os.environ, {"SR_TEMP_PREVIEWS_ROOT": str(self.root / 'tmp')}), \
                    mock.patch.object(app_module.os, 'access', return_value=not temporary):
                warm = await self.client.get(drop)
                self.assertEqual(warm.status_code, 200, warm.text)
                occupying = asyncio.create_task(self.app.state.heavy_work.run(slow))
                try:
                    self.assertTrue(await asyncio.to_thread(entered.wait, 5))
                    with mock.patch.object(pj, 'build_preview_pixels', side_effect=AssertionError('cache must not decode')):
                        response = await asyncio.wait_for(self.client.get(drop), 5)
                    self.assertEqual(response.content, warm.content)
                    self.assertEqual(response.headers.get('X-SR-Preview-Fallback'), 'tmp' if temporary else None)
                    self.assertEqual(response.headers.get('Cache-Control'), 'no-store')
                    self.assertFalse(occupying.done())
                finally:
                    release.set()
                    await occupying

    async def test_cold_drop_requests_share_generation_and_cancelled_caller_detaches(self):
        entered, release = threading.Event(), threading.Event()
        original = pj.build_preview_pixels
        def slow(*args, **kwargs):
            entered.set()
            assert release.wait(10)
            return original(*args, **kwargs)
        work = self.app.state.heavy_work
        drop = self.url.replace('/preview', '/preview-drop') + '?div=4'
        with mock.patch.object(pj, 'build_preview_pixels', side_effect=slow) as build:
            first = asyncio.create_task(self.client.get(drop))
            others = []
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 5))
                others = [asyncio.create_task(self.client.get(drop)) for _ in range(4)]
                async def shared():
                    while work.deduplicated < 4:
                        await asyncio.sleep(0)
                await asyncio.wait_for(shared(), 5)
                first.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await first
            finally:
                release.set()
            responses = await asyncio.gather(*others)
            self.assertTrue(all(r.status_code == 200 for r in responses))
            self.assertTrue(all(r.content == responses[0].content for r in responses))
            self.assertEqual(build.call_count, 1)

    async def test_sse_delivers_and_unsubscribes_while_preview_is_running(self):
        from backend.api.platform import _broadcast
        started, received = asyncio.Event(), asyncio.Event()
        disconnect = asyncio.Event()
        chunks = []
        async def receive():
            await disconnect.wait()
            return {"type": "http.disconnect"}
        async def send(message):
            if message["type"] == "http.response.start":
                started.set()
            elif message["type"] == "http.response.body":
                chunks.append(message.get("body", b""))
                received.set()
        scope = dict(type="http", asgi={"version": "3.0", "spec_version": "2.3"},
                     method="GET", scheme="http", path="/api/queue/events", raw_path=b"/api/queue/events",
                     query_string=b"", headers=[], server=("test", 80), client=("test", 1), root_path="")
        stream = asyncio.create_task(self.app(scope, receive, send))
        entered, release = threading.Event(), threading.Event()
        def slow():
            entered.set()
            assert release.wait(10)
        heavy = asyncio.create_task(self.app.state.heavy_work.run(slow))
        try:
            await asyncio.wait_for(started.wait(), 5)
            self.assertTrue(await asyncio.to_thread(entered.wait, 5))
            _broadcast(self.app.state, {"type": "job_update", "task_id": 1})
            await asyncio.wait_for(received.wait(), 5)
            self.assertIn(b"job_update", b"".join(chunks))
            self.assertFalse(heavy.done())
        finally:
            disconnect.set()
            release.set()
            await asyncio.wait_for(stream, 5)
            await heavy
        self.assertEqual(len(self.app.state.subscribers), 0)
