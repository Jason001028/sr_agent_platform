"""Real loopback streaming: progress extends chat, never the bounded total/Goal slice."""
import asyncio
import json
import time
from dataclasses import replace
from unittest.mock import Mock

import pytest

from backend.agent.async_transport import request
from backend.agent.loop import _consume_stream_async, parse_response, run_loop
from backend.agent.request_budget import response_limits, timeout_for, timeout_message
from backend.config import Config, load_config


BASE = Config('http://127.0.0.1/v1', 'fixture', 'fake', 1024, 0, .15,
              llm_response_timeout=3.0, llm_first_output_timeout=1.5,
              llm_deadline_margin=.001, llm_thinking_profile='fast')


def test_defaults_separate_chat_progress_from_goal_deadline(monkeypatch):
    for key in ('SR_LLM_TIMEOUT', 'SR_LLM_RESPONSE_TIMEOUT', 'SR_LLM_FIRST_OUTPUT_TIMEOUT', 'SR_LLM_VISION_TIMEOUT'):
        monkeypatch.delenv(key, raising=False)
    cfg = load_config()
    assert response_limits(cfg) == {'total': 600, 'first_output': 180, 'idle': 60, 'adaptive': True}
    assert timeout_for(cfg, now=100) == 600
    assert timeout_for(cfg, deadline=140, now=100) == pytest.approx(39.9)
    assert response_limits(cfg, deadline=140)['total'] == 60
    assert response_limits(replace(cfg, llm_vision_timeout=300), has_images=True)['first_output'] == 300
    assert response_limits(cfg, streaming=False)['first_output'] == 600


@pytest.mark.parametrize('field,value', [('llm_response_timeout', 0), ('llm_first_output_timeout', -1), ('llm_timeout', float('nan'))])
def test_invalid_budgets_are_rejected(field, value):
    with pytest.raises(ValueError):
        replace(BASE, **{field: value})


@pytest.mark.parametrize('mode', ['progress', 'prefill', 'idle', 'first_heartbeat', 'idle_heartbeat', 'total', 'nonstream', 'durable', 'partial_tools', 'cancel'])
def test_progress_policy_closes_real_tcp_without_partial_execution(mode):
    async def scenario():
        closed = asyncio.Event()
        received = asyncio.Event()
        handlers = set()
        async def handle(reader, writer):
            handlers.add(asyncio.current_task())
            try:
                head = await reader.readuntil(b'\r\n\r\n')
                length = next(int(x.split(b':')[1]) for x in head.split(b'\r\n') if x.lower().startswith(b'content-length:'))
                await reader.readexactly(length)
                if mode == 'nonstream':
                    await asyncio.sleep(.3)
                    body = json.dumps({'choices': [{'finish_reason': 'stop', 'message': {'content': 'complete', 'tool_calls': []}}]}).encode()
                    writer.write(b'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: '+str(len(body)).encode()+b'\r\nConnection: close\r\n\r\n'+body)
                else:
                    writer.write(b'HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nConnection: close\r\n\r\n')
                    await writer.drain()
                    async def chunk(delta=None, *, finish=None, usage=False):
                        data = {'choices': [] if usage else [{'index': 0, 'delta': delta or {}, 'finish_reason': finish}]}
                        if usage:
                            data['usage'] = {'prompt_tokens': 10, 'completion_tokens': 2, 'total_tokens': 12}
                        writer.write(('data: '+json.dumps(data)+'\n\n').encode())
                        await writer.drain()
                    if mode in ('prefill', 'durable'):
                        await asyncio.sleep(.3)
                        await chunk({'content': 'complete'})
                    elif mode == 'partial_tools':
                        await chunk({'tool_calls': [{'index': 0, 'id': 'partial', 'function': {'name': 'run_sr', 'arguments': '{"lq_path":'}}]})
                        await reader.read(); closed.set(); return
                    elif mode in ('idle', 'cancel'):
                        await chunk({'content': 'begin'})
                        await reader.read(); closed.set(); return
                    elif mode in ('first_heartbeat', 'idle_heartbeat'):
                        if mode == 'idle_heartbeat':
                            await chunk({'content': 'begin'})
                        for _ in range(20):
                            await chunk(usage=True)  # Usage is not semantic generation progress.
                            await chunk({'role': 'assistant'})
                            await asyncio.sleep(.05)
                    else:
                        for _ in range(30 if mode == 'total' else 7):
                            await chunk({'reasoning_content': 'x'} if mode == 'total' else {'content': 'x'})
                            await asyncio.sleep(.08)
                    await chunk(finish='stop')
                    writer.write(b'data: [DONE]\n\n')
                await writer.drain()
                if mode in ('progress', 'prefill', 'nonstream'):
                    # Successful SSE must terminate its HTTP body; otherwise
                    # SDK post-[DONE] drain waits for an EOF we never send.
                    writer.close(); await writer.wait_closed(); closed.set()
                else:
                    # Failure tests need client EOF, never server self-close.
                    await reader.read(); closed.set()
            except (ConnectionError, asyncio.IncompleteReadError):
                closed.set()
            finally:
                writer.close()
                try:
                    await writer.wait_closed()
                except ConnectionError:
                    pass
                handlers.discard(asyncio.current_task())
        server = await asyncio.start_server(handle, '127.0.0.1', 0)
        cfg = replace(BASE, llm_base_url=f'http://127.0.0.1:{server.sockets[0].getsockname()[1]}/v1',
                      llm_response_timeout=.5 if mode == 'total' else 3.0,
                      llm_first_output_timeout=.3 if mode == 'first_heartbeat' else 1.5)
        try:
            task = asyncio.create_task(request(cfg, [], [],
                on_delta=None if mode == 'nonstream' else lambda e: received.set(),
                deadline=time.monotonic()+.09 if mode == 'durable' else None,
                consume=_consume_stream_async, parse=parse_response))
            if mode == 'cancel':
                await asyncio.wait_for(received.wait(), 1)
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            else:
                out = await task
                if mode in ('progress', 'prefill', 'nonstream'):
                    assert out['response_complete'], json.dumps(out, ensure_ascii=False)
                    assert out['content'] == ('xxxxxxx' if mode == 'progress' else 'complete')
                elif mode == 'durable':
                    assert out['response_error'] == 'DEADLINE_EXCEEDED'
                    assert out['model_timeout_seconds'] == BASE.llm_timeout
                else:
                    assert out['response_error'] == 'MODEL_TIMEOUT'
                    assert out['timeout_phase'] == ('first_output' if mode == 'first_heartbeat' else 'total' if mode == 'total' else 'idle')
                    assert '本次请求已取消' in timeout_message(out, cfg)
                    if 'heartbeat' in mode:
                        assert out['usage']['total_tokens'] == 12
                    if mode == 'partial_tools':
                        execute = Mock()
                        failed = run_loop(cfg, 'fixture', chat=lambda messages, tools: out, tool_executor=execute)
                        assert failed['error_class'] == 'MODEL_TIMEOUT'
                        execute.assert_not_called()
            await asyncio.wait_for(closed.wait(), 1.5)
        finally:
            server.close(); await server.wait_closed()
            for handler in handlers:
                handler.cancel()
            if handlers:
                await asyncio.gather(*handlers, return_exceptions=True)
    asyncio.run(scenario())
