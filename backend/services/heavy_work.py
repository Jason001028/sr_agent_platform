"""Application-owned, bounded work queue; waiting callers consume no threads.

One executing raster job is intentional: a single preview can use hundreds of
MB. Four interactive jobs may pass a background job, then background gets a turn.
Cancellation detaches a caller, never releases capacity held by native work.
"""
import asyncio
import logging
import time
from collections import deque, OrderedDict
from concurrent.futures import ThreadPoolExecutor, Future

from fastapi import HTTPException

log = logging.getLogger(__name__)


class WorkCapacityError(HTTPException):
    """Admission failure, never an image-quality or execution failure."""
    def __init__(self, detail="预览与分析队列已满，请稍后重试"):
        super().__init__(503, detail, headers={"Retry-After": "2"})


class WorkFuture(Future):
    """Batch ownership: cancel removes queued work; running work cannot cancel."""
    def __init__(self):
        super().__init__()
        self.queued_at = time.monotonic()
        self.started_at = None


class HeavyWork:
    def __init__(self, limit=32):
        self.limit = limit
        self.queues = (deque(), deque())
        self.inflight = {}
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="raster")
        self.reader = ThreadPoolExecutor(max_workers=2, thread_name_prefix="preview-cache")
        self.read_slots = asyncio.Semaphore(2)
        self.resolver = ThreadPoolExecutor(max_workers=2, thread_name_prefix="scene-resolve")
        self.resolve_slots = asyncio.Semaphore(2)
        self.drain = None
        self.closed = False
        self.active = 0
        self.deduplicated = 0
        self.completed = 0
        self.failed = 0
        self.states = OrderedDict()
        self.loop_delay_ms = 0.0
        self.loop = None

    async def read(self, fn, *args):
        return await self._read(self.reader, self.read_slots, fn, args)

    async def resolve(self, fn, *args):
        return await self._read(self.resolver, self.resolve_slots, fn, args)

    async def _read(self, executor, slots, fn, args):
        async with slots:
            # Shield native I/O and retain its slot until the thread exits.
            task = asyncio.get_running_loop().run_in_executor(executor, fn, *args)
            try:
                return await asyncio.shield(task)
            except asyncio.CancelledError:
                await asyncio.shield(task)
                raise

    async def run(self, fn, *args, key=None, background=False, _ticket=None):
        self.loop = asyncio.get_running_loop()
        if _ticket is not None and _ticket.cancelled():
            return None
        if self.closed:
            raise WorkCapacityError("重任务调度正在关闭")
        if key is not None and key in self.inflight:
            self.deduplicated += 1
            return await asyncio.shield(self.inflight[key])
        if sum(map(len, self.queues)) >= self.limit:
            raise WorkCapacityError()
        future = asyncio.get_running_loop().create_future()
        # A disconnected sole caller must not produce an unobserved exception.
        future.add_done_callback(lambda f: f.exception() if not f.cancelled() else None)
        if key is not None:
            self.inflight[key] = future
            self.states[key] = "queued"
            while len(self.states) > 128:
                self.states.popitem(last=False)
        self.queues[int(background)].append((fn, args, key, future, time.monotonic(), _ticket))
        if self.drain is None or self.drain.done():
            self.drain = asyncio.create_task(self._drain())
        return await asyncio.shield(future)

    async def _drain(self):
        streak = 0
        while any(self.queues):
            bg = bool(self.queues[1]) and (not self.queues[0] or streak >= 4)
            fn, args, key, future, queued, ticket = self.queues[int(bg)].popleft()
            if ticket is not None and not ticket.set_running_or_notify_cancel():
                future.cancel()
                continue
            streak = 0 if bg else streak + 1
            started = time.monotonic()
            self.active = 1
            if key is not None:
                self.states[key] = "running"
            try:
                def execute():
                    if ticket is not None:
                        ticket.started_at = time.monotonic()
                    return fn(*args)
                result = await asyncio.get_running_loop().run_in_executor(self.executor, execute)
            except Exception as exc:
                self.failed += 1
                if key is not None:
                    self.states[key] = "failed"
                future.set_exception(exc)
            else:
                self.completed += 1
                if key is not None:
                    self.states[key] = "ready"
                future.set_result(result)
            finally:
                self.active = 0
                if key is not None:
                    self.inflight.pop(key, None)
                log.info("raster_work wait_ms=%.0f run_ms=%.0f background=%s queued=%d",
                         (started-queued)*1000, (time.monotonic()-started)*1000,
                         bg, sum(map(len, self.queues)))

    def snapshot(self):
        return dict(active=self.active, queued=sum(map(len, self.queues)),
                    capacity=1, queue_limit=self.limit, deduplicated=self.deduplicated,
                    completed=self.completed, failed=self.failed)

    async def monitor(self):
        self.loop = asyncio.get_running_loop()
        while True:
            start = time.monotonic()
            await asyncio.sleep(1)
            self.loop_delay_ms = max(0, (time.monotonic() - start - 1) * 1000)

    def background_executor(self):
        """Executor-shaped bridge for the existing fenced Batch producer.

        Its monitor keeps its own executor; only raster detection and mask
        verification enter this queue. No thread is allocated while waiting.
        """
        owner = self
        class Adapter:
            def submit(self, fn, *args):
                ticket = WorkFuture()
                async def dispatch():
                    try:
                        result = await owner.run(fn, *args, background=True, _ticket=ticket)
                    except asyncio.CancelledError:
                        ticket.cancel()
                    except Exception as exc:
                        if not ticket.cancelled():
                            ticket.set_exception(exc)
                    else:
                        if not ticket.cancelled():
                            ticket.set_result(result)
                def cancelled(f):
                    if f.cancelled():
                        owner.loop.call_soon_threadsafe(owner._remove_ticket, ticket)
                ticket.add_done_callback(cancelled)
                asyncio.run_coroutine_threadsafe(dispatch(), owner.loop)
                return ticket

            def shutdown(self, **kwargs):
                pass  # Application lifespan owns HeavyWork and drains it.
        return Adapter()

    def _remove_ticket(self, ticket):
        for queue in self.queues:
            for job in list(queue):
                if job[-1] is ticket:
                    queue.remove(job)
                    job[3].cancel()
                    return

    async def close(self):
        self.closed = True
        if self.drain is not None:
            await asyncio.shield(self.drain)
        self.executor.shutdown(wait=True)
        self.reader.shutdown(wait=True)
        self.resolver.shutdown(wait=True)
