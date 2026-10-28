"""Cancellable network boundary; synchronous Loop callers already run off the API loop."""
import asyncio
import time
import ssl
import httpx

# Load platform trust once, outside request timing (Windows certificate loading is blocking).
_TLS_CONTEXT = ssl.create_default_context()

from openai import AsyncOpenAI, APITimeoutError

from backend.agent.request_budget import DeadlineExceeded, request_policy, timeout_for


async def request(cfg, wire, tools, *, on_delta=None, request_config=None,
                  deadline=None, fallback_reserve=None, consume, parse, diagnostics=None):
    diagnostics = diagnostics if diagnostics is not None else {}
    diagnostics.update(request_attempts=0, capability_rejections=[])
    started = time.monotonic()
    # Chat has a per-call wall-clock limit too. Durable uses the earlier limit.
    end = min(started + cfg.llm_timeout,
              deadline - cfg.llm_deadline_margin if deadline is not None else float('inf'))
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
                params['timeout'] = min(timeout_for(cfg, deadline), end-time.monotonic())
                if params['timeout'] <= 0:
                    raise DeadlineExceeded('request deadline reached before fallback')
                try:
                    # Binding errors happen before an attempted async dispatch.
                    call = client.chat.completions.create(**params)
                    diagnostics['request_attempts'] += 1
                    response = await call
                    break
                except Exception as exc:
                    if getattr(exc, 'status_code', None) not in (400, 422):
                        raise
                    text = str(exc)
                    rejected = next((field for field in ('reasoning_effort', 'chat_template_kwargs', 'stream_options')
                                     if field in text and (field in params or field in params.get('extra_body', {}))), None)
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
                out = await consume(response, on_delta, end, started, observed)
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
        except (asyncio.TimeoutError, APITimeoutError):
            return dict(content=None, tool_calls=[], response_complete=False,
                        response_error=('DEADLINE_EXCEEDED' if deadline is not None and deadline-cfg.llm_deadline_margin <= started+cfg.llm_timeout else 'MODEL_TIMEOUT'), usage=observed.get('usage'),
                        **diagnostics)
