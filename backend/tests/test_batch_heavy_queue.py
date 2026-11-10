"""Real Batch ticks and the application raster queue share one event loop."""
import asyncio
import threading
import unittest
from unittest import mock

from backend.services import batch_worker
from backend.services.heavy_work import HeavyWork, WorkCapacityError


class BatchHeavyQueueTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from backend.tests import test_batch_worker as fixtures
        self.fixture = fixtures.BatchWorkerTest()
        self.fixture.setUp()
        self.state = self.fixture.state
        self.store = self.fixture.store
        self.goal, self.manifest = self.fixture.make_goal(1)
        self.gid = self.goal['goal_id']
        self.work = HeavyWork(limit=1)
        self.work.loop = asyncio.get_running_loop()
        self.state.batch_io_executor = self.work.background_executor()
        batch_worker._ensure_io_executor(self.state)
        self.releases = []

    async def asyncTearDown(self):
        for gate in self.releases:
            gate.set()
        await self.work.close()
        self.fixture.tearDown()

    def item(self):
        return self.fixture.items(self.gid)[0]

    async def ticks_until(self, predicate):
        async def run():
            while not predicate():
                await batch_worker.batch_tick(self.state)
        await asyncio.wait_for(run(), 20)

    async def barrier_until(self, predicate):
        async def run():
            while not predicate():
                await asyncio.sleep(0)
        await asyncio.wait_for(run(), 5)

    async def mask_ready(self):
        with mock.patch.object(batch_worker, '_start_submit_preparation', return_value=False):
            await self.ticks_until(lambda: self.item()['status'] == 'MASK_READY')

    async def occupy(self, *, fill=False):
        entered, release = threading.Event(), threading.Event()
        self.releases.append(release)
        def slow():
            entered.set()
            assert release.wait(20)
        active = asyncio.create_task(self.work.run(slow))
        self.assertTrue(await asyncio.to_thread(entered.wait, 5))
        queued = None
        if fill:
            queued = asyncio.create_task(self.work.run(lambda: None))
            await self.barrier_until(lambda: self.work.snapshot()['queued'] == 1)
        return active, queued, release

    def make_due(self):
        self.store._db().execute('UPDATE agent_goal_steps SET next_wake_at=0 WHERE goal_id=?', (self.gid,))
        self.store._db().commit()

    async def assert_recovers(self):
        self.make_due()
        await self.ticks_until(lambda: self.store.get_goal(self.gid)['status'] == 'COMPLETED')
        self.assertEqual(len(self.fixture.submits), 1)

    async def test_full_queue_defers_submit_verification_without_blocking_or_quality_failure(self):
        await self.mask_ready()
        original_mask = self.item()['checkpoint']['mask']
        active, queued, release = await self.occupy(fill=True)
        with mock.patch.object(batch_worker, '_prepare_submission', wraps=batch_worker._prepare_submission) as prepare:
            try:
                await self.ticks_until(lambda: self.item()['checkpoint'].get('io_capacity_waits', 0) == 1)
                item = self.item()
                self.assertEqual(item['status'], 'MASK_READY')
                self.assertEqual(item['checkpoint']['mask'], original_mask)
                self.assertEqual(item['checkpoint'].get('detection_failures', 0), 0)
                self.assertEqual(item['checkpoint'].get('submit_prepare_failures', 0), 0)
                self.assertGreater(item['next_wake_at'], 0)
                self.assertEqual(prepare.call_count, 0)
                self.assertEqual(self.fixture.submits, [])
            finally:
                release.set()
                await asyncio.gather(active, queued)
        await self.assert_recovers()

    async def test_full_queue_defers_detection_without_consuming_detection_attempts(self):
        active, queued, release = await self.occupy(fill=True)
        try:
            await self.ticks_until(lambda: self.item()['checkpoint'].get('io_capacity_waits', 0) == 1)
            item = self.item()
            self.assertEqual(item['status'], 'DETECTING')
            self.assertEqual(item['checkpoint'].get('detection_failures', 0), 0)
            self.assertEqual(item['checkpoint'].get('attempt', 0), 0)
            self.assertNotIn('detection', item['checkpoint'])
        finally:
            release.set()
            await asyncio.gather(active, queued)
        await self.assert_recovers()

    async def queued_expiry(self, preparation):
        if preparation:
            await self.mask_ready()
        active, _, release = await self.occupy()
        name = '_prepare_submission' if preparation else '_detection_job'
        with mock.patch.object(batch_worker, name, wraps=getattr(batch_worker, name)) as execute:
            try:
                jobs = self.state.batch_submit_jobs if preparation else self.state.batch_io_jobs
                await self.ticks_until(lambda: bool(jobs))
                await self.barrier_until(lambda: self.work.snapshot()['queued'] == 1)
                record = next(iter(jobs.values()))
                ticket = record['future']
                self.assertIsNone(record['timing']['started_at'])
                record['timing']['queued_at'] -= 100  # Longer than execution limit (10s), under queue limit (300s).
                await batch_worker.batch_tick(self.state)
                self.assertFalse(ticket.done())
                self.assertNotIn('detection_failures', self.item()['checkpoint'])
                self.assertNotIn('submit_prepare_failures', self.item()['checkpoint'])
                record['timing']['queued_at'] -= 300
                await self.ticks_until(lambda: ticket.cancelled())
                await self.barrier_until(lambda: self.work.snapshot()['queued'] == 0)
                self.assertEqual(self.item()['status'], 'MASK_READY' if preparation else 'DETECTING')
                self.assertEqual(self.item()['checkpoint'].get('io_capacity_waits'), 1)
                self.assertEqual(execute.call_count, 0)
            finally:
                release.set()
                await active
            # Draining the old queue must never execute the expired function.
            self.assertEqual(execute.call_count, 0)
        await self.assert_recovers()

    async def test_queued_detection_does_not_use_execution_timeout_and_expiry_removes_work(self):
        await self.queued_expiry(False)

    async def test_queued_verification_does_not_use_execution_timeout_and_expiry_removes_work(self):
        await self.queued_expiry(True)

    async def expiry_between_polls(self, preparation):
        if preparation:
            await self.mask_ready()
        active, _, release = await self.occupy()
        name = '_prepare_submission' if preparation else '_detection_job'
        with mock.patch.object(batch_worker, name, wraps=getattr(batch_worker, name)) as execute:
            jobs = self.state.batch_submit_jobs if preparation else self.state.batch_io_jobs
            try:
                await self.ticks_until(lambda: bool(jobs))
                await self.barrier_until(lambda: self.work.snapshot()['queued'] == 1)
                record = next(iter(jobs.values()))
                record['timing']['queued_at'] -= 400
            finally:
                release.set()
                await active
            # No collector tick between expiry and dispatch. The function
            # must still be rejected at entry rather than doing stale I/O.
            with self.assertRaises(WorkCapacityError):
                await asyncio.wrap_future(record['future'])
            self.assertEqual(execute.call_count, 0)
            await batch_worker.batch_tick(self.state)
            self.assertEqual(self.item()['checkpoint'].get('io_capacity_waits'), 1)
            self.assertEqual(self.item()['checkpoint'].get('detection_failures', 0), 0)
            self.assertEqual(self.item()['checkpoint'].get('submit_prepare_failures', 0), 0)
        await self.assert_recovers()

    async def test_detection_expired_between_collector_ticks_never_enters_algorithm(self):
        await self.expiry_between_polls(False)

    async def test_verification_expired_between_collector_ticks_never_reads_inputs(self):
        await self.expiry_between_polls(True)

    async def running_timeout(self, preparation):
        if preparation:
            await self.mask_ready()
        entered, release = threading.Event(), threading.Event()
        self.releases.append(release)
        name = '_prepare_submission' if preparation else '_detection_job'
        original = getattr(batch_worker, name)
        def slow(snapshot):
            entered.set()
            assert release.wait(20)
            return original(snapshot)
        with mock.patch.object(batch_worker, name, side_effect=slow) as execute:
            await self.ticks_until(entered.is_set)
            jobs = self.state.batch_submit_jobs if preparation else self.state.batch_io_jobs
            record = next(iter(jobs.values()))
            ticket = record['future']
            record['timing']['started_at'] -= 11
            try:
                for _ in range(3):
                    await batch_worker.batch_tick(self.state)
                self.assertTrue(record.get('timed_out'))
                self.assertFalse(ticket.cancel(), 'running native work must retain ownership')
                self.assertEqual(len(jobs), 1)
                self.assertEqual(execute.call_count, 1)
                self.assertEqual(self.item()['checkpoint'].get('detection_failures', 0), 0)
                self.assertEqual(self.item()['checkpoint'].get('submit_prepare_failures', 0), 0)
            finally:
                release.set()
            await asyncio.wrap_future(ticket)
            await batch_worker.batch_tick(self.state)
            cp = self.item()['checkpoint']
            self.assertEqual(cp.get('submit_prepare_failures' if preparation else 'detection_failures'), 1)
            self.assertEqual(self.fixture.submits, [])
            self.assertEqual(execute.call_count, 1)
        await self.assert_recovers()

    async def test_running_detection_timeout_does_not_spawn_another_native_job(self):
        await self.running_timeout(False)

    async def test_running_verification_timeout_does_not_spawn_another_native_job(self):
        await self.running_timeout(True)
