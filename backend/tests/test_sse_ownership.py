"""Actual worker-thread races, with direct SSE subscription closure."""
import asyncio
import threading
from unittest.mock import patch
from fastapi import HTTPException
from backend.tests.test_api_platform import PlatformBase
from backend.api.platform import chat_send

class TestOwnership(PlatformBase):
    def test_disconnect_and_concurrency(self):
        app = self._make_app()
        a, b = [app.state.store.create_session({'title':'test'}) for _ in range(2)]
        entered, release = threading.Event(), threading.Event()
        class Req:
            def __init__(self): self.app = app
            async def body(self): return b'{"content":"hi"}'
        def run(*args, session_id, on_step, **kwargs):
            if session_id == a:
                entered.set()
                assert release.wait(30)
            for _ in range(10000): on_step({'type':'token','delta':'x'})
            app.state.store.append_message(session_id, {'role':'assistant', 'content':'persisted'})
            return {'ok':True, 'answer':'done'}
        async def scenario():
            response = await chat_send(a, Req())
            await asyncio.to_thread(entered.wait, 2)
            assert entered.is_set()
            assert response.body_iterator.ag_frame.f_locals["q"].maxsize == 256
            await response.body_iterator.__anext__()
            await response.body_iterator.aclose()
            assert app.state.chat_locks[a].locked()
            with self.assertRaises(HTTPException) as e: await chat_send(a, Req())
            assert e.exception.status_code == 409
            other = await chat_send(b, Req())
            assert [x async for x in other.body_iterator]
            assert not app.state.chat_locks[b].locked()
            release.set()
            await asyncio.wait_for(asyncio.gather(*app.state.chat_workers), 3)
            assert not app.state.chat_locks[a].locked()
            assert app.state.store.get_messages(a)[-1]['content'] == 'persisted'
            next_run = await chat_send(a, Req())
            assert [x async for x in next_run.body_iterator]
        with patch('backend.api.platform.loop_mod.run_loop', side_effect=run):
            try: asyncio.run(scenario())
            finally: release.set()

    def test_start_failure_and_worker_error(self):
        app = self._make_app()
        sid = app.state.store.create_session()
        class Req:
            def __init__(self): self.app = app
            async def body(self): return b'{"content":"hi"}'
        async def scenario():
            with patch('threading.Thread.start', side_effect=RuntimeError('start')):
                with self.assertRaises(RuntimeError): await chat_send(sid, Req())
            assert not app.state.chat_locks[sid].locked()
            with patch('backend.api.platform.loop_mod.run_loop', side_effect=RuntimeError('worker')):
                response = await chat_send(sid, Req())
                frames = [x async for x in response.body_iterator]
                assert any('worker' in x for x in frames)
                assert not app.state.chat_locks[sid].locked()
        asyncio.run(scenario())



    def test_disconnect_before_first_event(self):
        app = self._make_app()
        sid = app.state.store.create_session({'title':'test'})
        release = threading.Event()
        class Req:
            def __init__(self): self.app = app
            async def body(self): return b'{"content":"hi"}'
        def run(*args, **kwargs):
            assert release.wait(10)
            return {'ok':True}
        async def scenario():
            with patch('backend.api.platform.loop_mod.run_loop', side_effect=run):
                response = await chat_send(sid, Req())
                await response.body_iterator.aclose()
                assert app.state.chat_locks[sid].locked()
                release.set()
                await asyncio.gather(*app.state.chat_workers)
                assert not app.state.chat_locks[sid].locked()
        try: asyncio.run(scenario())
        finally: release.set()

    def test_real_agent_loop_disconnect_during_model(self):
        app = self._make_app()
        sid = app.state.store.create_session({'title':'test'})
        entered, release = threading.Event(), threading.Event()
        class Req:
            def __init__(self): self.app = app
            async def body(self): return b'{"content":"hi"}'
        def chat(messages, tools, on_delta=None):
            entered.set()
            assert release.wait(15)
            if on_delta: on_delta({'type':'token','delta':'answer'})
            return {'content':'answer', 'tool_calls':[], 'finish_reason':'stop'}
        async def scenario():
            with patch('backend.agent.loop._default_chat', return_value=chat):
                response = await chat_send(sid, Req())
                await asyncio.to_thread(entered.wait, 3)
                assert entered.is_set()
                await response.body_iterator.__anext__()
                await response.body_iterator.aclose()
                with self.assertRaises(HTTPException): await chat_send(sid, Req())
                release.set()
                await asyncio.wait_for(asyncio.gather(*app.state.chat_workers), 10)
                assert not app.state.chat_locks[sid].locked()
                messages = app.state.store.get_messages(sid)
                assert [m['content'] for m in messages if m['role'] == 'assistant'] == ['answer']
        try: asyncio.run(scenario())
        finally: release.set()

    def test_terminal_result_survives_backpressure(self):
        app = self._make_app()
        class Req:
            def __init__(self): self.app = app
            async def body(self): return b'{"content":"hi"}'
        async def scenario(fail):
            sid = app.state.store.create_session({'title':'test'})
            def run(*args, on_step, **kwargs):
                for _ in range(2000): on_step({'type':'token','delta':'x'})
                on_step({'type':'error','error':'final failure'})
                return {'ok':not fail, 'answer':'final answer',
                        'error':'final failure' if fail else None}
            with patch('backend.api.platform.loop_mod.run_loop', side_effect=run):
                response = await chat_send(sid, Req())
                # Do not consume until the Worker has saturated delivery slots.
                await asyncio.gather(*app.state.chat_workers)
                frames = [x async for x in response.body_iterator]
                from backend.tests.test_api_platform import sse_events
                events = sse_events(''.join(frames))
                terminal = [e for e in events if e['type'] in ('error','turn_done')]
                assert len(terminal) == 1
                assert terminal[0]['type'] == ('error' if fail else 'turn_done')
                if fail: assert terminal[0]['error'] == 'final failure'
                else: assert terminal[0]['content'] == 'final answer'
        asyncio.run(scenario(True))
        asyncio.run(scenario(False))
