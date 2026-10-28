"""Real loopback TCP tests: cancellation must close an otherwise blocked HTTP read."""
import asyncio
import json
import time
from dataclasses import replace
import pytest
from backend.agent.async_transport import request
from backend.agent.loop import _consume_stream_async, parse_response
from backend.config import Config

CFG = Config("http://127.0.0.1/v1", "test", "fake", 4096, 0, 2)

@pytest.mark.parametrize("mode", ["slow_stream", "stall_stream", "nonstream", "cancel"])
def test_absolute_deadline_closes_tcp(mode):
    async def scenario():
        closed = asyncio.Event()
        tasks = set()
        async def handle(reader, writer):
            tasks.add(asyncio.current_task())
            try:
                headers = await reader.readuntil(b"\r\n\r\n")
                length = next(int(line.split(b":")[1]) for line in headers.split(b"\r\n") if line.lower().startswith(b"content-length:"))
                await reader.readexactly(length)
                if mode == "nonstream":
                    writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 10000\r\n\r\n")
                else:
                    writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nConnection: close\r\n\r\n")
                    writer.write(b'data: {"choices":[],"usage":{"prompt_tokens":10,"completion_tokens":20,"total_tokens":30}}\n\n')
                    chunk = b'data: {"choices":[{"index":0,"delta":{"reasoning_content":"x"},"finish_reason":null}]}\n\n'
                    for _ in range(50 if mode == "stall_stream" else 100):
                        writer.write(chunk)
                        await writer.drain()
                        await asyncio.sleep(.03)
                await writer.drain()
                await reader.read()
                closed.set()
            except (ConnectionError, asyncio.IncompleteReadError):
                closed.set()
            finally:
                writer.close()
                await writer.wait_closed()
                tasks.discard(asyncio.current_task())
        server = await asyncio.start_server(handle, '127.0.0.1', 0)
        cfg = replace(CFG, llm_base_url=f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}/v1")
        start = time.monotonic()
        try:
            received = asyncio.Event()
            task = asyncio.create_task(request(cfg, [], [], on_delta=None if mode == "nonstream" else lambda e: received.set(),
                                consume=_consume_stream_async, parse=parse_response))
            if mode == "cancel":
                await asyncio.wait_for(received.wait(), 2)
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            else:
                out = await task
                assert out['response_error'] == 'MODEL_TIMEOUT'
                if mode != 'nonstream':
                    assert out['usage']['total_tokens'] == 30
            assert time.monotonic()-start < 3.0
            await asyncio.wait_for(closed.wait(), 1.5)
        finally:
            server.close()
            await server.wait_closed()
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
    asyncio.run(scenario())


@pytest.mark.parametrize("adapter,field", [("reasoning_effort", "reasoning_effort"), ("chat_template", "chat_template_kwargs")])
def test_field_rejection_downgrades_with_reserved_attempt(adapter, field):
    async def scenario():
        bodies, reserves = [], []
        async def handle(reader, writer):
            h = await reader.readuntil(b"\r\n\r\n")
            n = next(int(x.split(b":")[1]) for x in h.split(b"\r\n") if x.lower().startswith(b"content-length:"))
            body = json.loads(await reader.readexactly(n)); bodies.append(body)
            rejected = field in body
            data = json.dumps({'error': {'message': f'unsupported {field}', 'type': 'invalid_request_error'}} if rejected else {'choices': [{'finish_reason': 'stop', 'message': {'content': 'done', 'tool_calls': []}}]}).encode()
            writer.write((f"HTTP/1.1 {'400 Bad Request' if rejected else '200 OK'}\r\nContent-Type: application/json\r\nContent-Length: {len(data)}\r\nConnection: close\r\n\r\n").encode()+data)
            await writer.drain(); writer.close(); await writer.wait_closed()
        server = await asyncio.start_server(handle, '127.0.0.1', 0)
        cfg = replace(CFG, llm_timeout=2, llm_base_url=f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}/v1", llm_thinking_adapter=adapter)
        try:
            out = await request(cfg, [], [], consume=_consume_stream_async, parse=parse_response,
                                fallback_reserve=lambda: reserves.append(1))
            assert out['content'] == 'done' and out['thinking_applied'] == 'unavailable'
            assert len(bodies) == 2 and len(reserves) == 1
            assert field not in bodies[1]
        finally:
            server.close(); await server.wait_closed()
    asyncio.run(scenario())


@pytest.mark.parametrize('ending', ['timeout', 'disconnect', 'cancel'])
def test_tcp_fallback_failure_retains_observations(ending):
    async def scenario():
        diagnostics, handlers = {}, set()
        second = asyncio.Event()
        async def handle(reader, writer):
            handlers.add(asyncio.current_task())
            try:
                headers = await reader.readuntil(b'\r\n\r\n')
                length = next(int(l.split(b':')[1]) for l in headers.split(b'\r\n') if l.lower().startswith(b'content-length:'))
                body = json.loads(await reader.readexactly(length))
                if 'reasoning_effort' in body:
                    data = b'{"error":{"message":"reasoning_effort unsupported","type":"invalid_request_error"}}'
                    writer.write(b'HTTP/1.1 400 Bad Request\r\nContent-Type: application/json\r\nConnection: close\r\nContent-Length: '+str(len(data)).encode()+b'\r\n\r\n'+data)
                    await writer.drain()
                else:
                    second.set()
                    if ending != 'disconnect': await reader.read()
            finally:
                writer.close()
                await writer.wait_closed()
        server = await asyncio.start_server(handle, '127.0.0.1', 0)
        cfg = replace(CFG, llm_timeout=.5, llm_thinking_adapter='reasoning_effort',
                      llm_base_url=f'http://127.0.0.1:{server.sockets[0].getsockname()[1]}/v1')
        try:
            task = asyncio.create_task(request(cfg, [], [], consume=_consume_stream_async,
                parse=parse_response, diagnostics=diagnostics))
            await asyncio.wait_for(second.wait(), 2)
            if ending == 'cancel': task.cancel()
            try: await task
            except (Exception, asyncio.CancelledError): pass
            assert diagnostics['request_attempts'] == 2
            assert diagnostics['capability_rejections'] == ['reasoning_effort']
            assert diagnostics['effective_thinking_parameters'] == {}
        finally:
            server.close()
            await server.wait_closed()
            if handlers: await asyncio.gather(*handlers, return_exceptions=True)
    asyncio.run(scenario())
