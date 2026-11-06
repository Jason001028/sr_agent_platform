"""Cancellable network boundary; synchronous Loop callers already run off the API loop."""
from dataclasses import replace
import asyncio
import time
import ssl
import httpx

# Load platform trust once, outside request timing (Windows certificate loading is blocking).
_TLS_CONTEXT = ssl.create_default_context()
# Warm the same path for httpcore: its first AsyncHTTPTransport construction
# imports the package lazily (~0.2s on Windows) and would otherwise be charged
# to the round slice of the first durable request after process start.
_WARM_TRANSPORT = httpx.AsyncHTTPTransport(verify=_TLS_CONTEXT)

from openai import AsyncOpenAI, APITimeoutError

from backend.agent.request_budget import DeadlineExceeded, request_policy, timeout_for, response_limits


def _meaningful(chunk):
    """Heartbeats, role-only deltas and usage frames cannot renew a request."""
    for choice in getattr(chunk, 'choices', None) or []:
        if getattr(choice, 'finish_reason', None):
            return True
        delta = getattr(choice, 'delta', None)
        if delta is None:
            continue
        if any(isinstance(getattr(delta, key, None), str) and getattr(delta, key)
               for key in ('content', 'reasoning_content', 'reasoning')):
            return True
        for part in getattr(delta, 'tool_calls', None) or []:
            fn = getattr(part, 'function', None)
            if getattr(part, 'id', None) or getattr(fn, 'name', None) or getattr(fn, 'arguments', None):
                return True
    return False


class _ProgressStream:
    def __init__(self, stream, *, started, end, limits, diagnostics):
        self.stream, self.started, self.end = stream, started, end
        self.limits, self.diagnostics = limits, diagnostics
        self.last_progress = None

    async def __aiter__(self):
        iterator = self.stream.__aiter__()
        while True:
            now = time.monotonic()
            phase = 'first_output' if self.last_progress is None else 'idle'
            limit = self.limits[phase]
            phase_end = (self.started if self.last_progress is None else self.last_progress) + limit
            wait = min(self.end, phase_end) - now
            try:
                if wait <= 0:
                    raise asyncio.TimeoutError()
                chunk = await asyncio.wait_for(iterator.__anext__(), wait)
            except StopAsyncIteration:
                return
            except asyncio.TimeoutError:
                total = self.end <= phase_end
                self.diagnostics.update(timeout_phase='total' if total else phase,
                    timeout_limit_seconds=self.limits['total'] if total else limit)
                raise
            if _meaningful(chunk):
                self.last_progress = time.monotonic()
            yield chunk

    async def close(self):
        close = getattr(self.stream, 'close', None)
        if callable(close):
            await close()


async def request(cfg, wire, tools, *, on_delta=None, request_config=None,
                  deadline=None, fallback_reserve=None, consume, parse, diagnostics=None):
    diagnostics = diagnostics if diagnostics is not None else {}
    diagnostics.pop('timeout_phase', None)
    diagnostics.pop('timeout_limit_seconds', None)
    diagnostics.update(request_attempts=0, capability_rejections=[])
    has_images = any(isinstance(m.get('content'), list) and any(
        isinstance(b, dict) and b.get('type') == 'image_url' for b in m['content']) for m in wire)
    # Ordinary visual chat may spend much longer on prefill. Never extend a Goal slice.
    if has_images and deadline is None and cfg.llm_response_timeout is None:
        cfg = replace(cfg, llm_timeout=max(cfg.llm_timeout, cfg.llm_vision_timeout))
    limits = response_limits(cfg, deadline=deadline, has_images=has_images, streaming=on_delta is not None)
    diagnostics.update(model_timeout_seconds=limits['total'],
        model_first_output_timeout_seconds=limits['first_output'], model_idle_timeout_seconds=limits['idle'])
    started = time.monotonic()
    # Chat has a per-call wall-clock limit too. Durable uses the earlier limit.
    end = min(started + limits['total'],
              deadline - cfg.llm_deadline_margin if deadline is not None else float('inf'))
    round_limited = deadline is not None and deadline - cfg.llm_deadline_margin <= started + limits['total']
    if end <= started:
        raise DeadlineExceeded('request deadline reached before transport')
    observed = {}
    policy = request_config or request_policy(cfg, deadline)
    params = dict(model=cfg.llm_model, messages=wire, tools=tools or None,
                  max_tokens=cfg.llm_max_tokens, temperature=cfg.llm_temperature,
                  stream=on_delta is not None, **policy['thinking_parameters'])
    if on_delta is not None:
        params['stream_options'] = {'include_usage': True}
    capability_rejections = diagnostics['capability_rejections']

    async with AsyncOpenAI(base_url=cfg.llm_base_url, api_key=cfg.llm_api_key,
                           max_retries=0, timeout=cfg.llm_timeout,
                           http_client=httpx.AsyncClient(verify=_TLS_CONTEXT)) as client:
        async def execute():
            while True:
                diagnostics['effective_thinking_parameters'] = {k: params[k] for k in policy['thinking_parameters'] if k in params}
                diagnostics['thinking_applied'] = ('unverified' if policy['thinking_parameters'] and not any(f != 'stream_options' for f in capability_rejections) else 'unavailable')
                remaining = min(timeout_for(cfg, deadline), end-time.monotonic())
                if remaining <= 0:
                    raise DeadlineExceeded('request deadline reached before fallback')
                if limits['adaptive']:
                    params['timeout'] = httpx.Timeout(min(remaining, max(limits['first_output'], limits['idle'])),
                                                     connect=min(15.0, remaining))
                else:
                    params['timeout'] = remaining
                try:
                    # Binding errors happen before an attempted async dispatch.
                    call = client.chat.completions.create(**params)
                    diagnostics['request_attempts'] += 1
                    if limits['adaptive']:
                        first_wait = min(end, started + limits['first_output']) - time.monotonic()
                        try:
                            response = await asyncio.wait_for(call, max(0, first_wait))
                        except asyncio.TimeoutError:
                            diagnostics.update(timeout_phase='first_output', timeout_limit_seconds=limits['first_output'])
                            raise
                    else:
                        response = await call
                    break
                except Exception as exc:
                    if getattr(exc, 'status_code', None) not in (400, 422):
                        raise
                    text = str(exc)
                    rejected = next((field for field in ('reasoning_effort', 'chat_template_kwargs', 'stream_options')
                                     if field in text and (field in params or field in params.get('extra_body', {}) or field in params.get('extra_body', {}).get('chat_template_kwargs', {}))), None)
                    if rejected is None:
                        raise
                    if not any(word in text.lower() for word in ("unsupported", "unknown", "unrecognized", "not supported", "unexpected", "extra_forbidden")):
                        raise
                    capability_rejections.append(rejected)
                    if time.monotonic() >= end:
                        raise DeadlineExceeded("request deadline reached before fallback")
                    # A field-specific rejection is capability evidence; auth/5xx never downgrade.
                    timeout_for(cfg, deadline)
                    if fallback_reserve is not None:
                        fallback_reserve()
                    if rejected in params:
                        del params[rejected]
                    else:
                        params.pop('extra_body', None)
            diagnostics['effective_thinking_parameters'] = {k: params[k] for k in policy['thinking_parameters'] if k in params}
            if on_delta is not None:
                stream = _ProgressStream(response, started=started, end=end, limits=limits,
                                         diagnostics=diagnostics) if limits['adaptive'] else response
                out = await consume(stream, on_delta, end, started, observed)
                # The SDK can surface its read timeout just before wait_for's
                # callback runs. Classify by the bound that won, not a clock race.
                if round_limited and out.get('response_error') == 'MODEL_TIMEOUT':
                    out['response_error'] = 'DEADLINE_EXCEEDED'
            else:
                out = parse(response)
                observed['usage'] = out.get('usage')
            out.update(diagnostics)
            out['capability_rejections'] = capability_rejections
            out['thinking_applied'] = 'unverified' if policy['thinking_parameters'] and not any(
                f != 'stream_options' for f in capability_rejections) else 'unavailable'
            return out

        try:
            # wait_for cancels the actual async HTTP task, including a blocked stream read.
            return await asyncio.wait_for(execute(), timeout=max(0, end-time.monotonic()))
        except (asyncio.TimeoutError, APITimeoutError) as exc:
            if time.monotonic() >= end:
                diagnostics.update(timeout_phase='total', timeout_limit_seconds=limits['total'])
            elif isinstance(getattr(exc, '__cause__', None), (httpx.ConnectTimeout, httpx.PoolTimeout)):
                diagnostics.update(timeout_phase='connection', timeout_limit_seconds=min(15.0, limits['total']))
            elif 'timeout_phase' not in diagnostics:
                diagnostics.update(timeout_phase='first_output', timeout_limit_seconds=limits['first_output'])
            return dict(content=None, tool_calls=[], response_complete=False,
                        response_error=('DEADLINE_EXCEEDED' if deadline is not None and deadline-cfg.llm_deadline_margin <= started+limits['total'] else 'MODEL_TIMEOUT'), usage=observed.get('usage'),
                        **diagnostics)
