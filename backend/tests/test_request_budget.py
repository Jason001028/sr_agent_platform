from backend.tests.async_client_fixture import async_client
from dataclasses import replace
from types import SimpleNamespace as NS
from unittest.mock import Mock
import pytest
from backend.agent import loop
from backend.agent.request_budget import request_policy, timeout_for, DeadlineExceeded
from backend.config import Config
from backend.agent.round_outcome import RoundOutcome

CFG = Config("http://unused/v1", "x", "fake", 4096, 0, 60)

@pytest.mark.parametrize("profile,effort", [("fast", "low"), ("balanced", "medium"), ("deep", "high")])
def test_explicit_profiles(profile, effort):
    cfg = replace(CFG, llm_thinking_profile=profile, llm_thinking_adapter="reasoning_effort")
    p = request_policy(cfg)
    assert p["thinking_parameters"] == {"reasoning_effort": effort}
    assert p["max_tokens"] == 4096


def test_unverified_endpoint_sends_no_fields():
    p = request_policy(CFG)
    assert p["thinking_profile"] == "unavailable"
    assert p["thinking_parameters"] == {}


def test_latest_budget_and_margin():
    assert timeout_for(CFG, 20, 10) == pytest.approx(9.9)
    assert timeout_for(CFG, 20, 18) == pytest.approx(1.9)
    with pytest.raises(DeadlineExceeded):
        timeout_for(CFG, 20, 19.95)


def test_admission_does_not_reserve():
    chat, reserve = Mock(), Mock()
    out = loop.run_loop(CFG, "x", chat=chat, before_model=reserve, durable=True, max_seconds=.01)
    assert out["outcome"] == RoundOutcome.BUDGET_YIELD
    chat.assert_not_called()
    reserve.assert_not_called()


@pytest.mark.parametrize("stream", [False, True])
def test_timeout_no_integrity_retry(stream):
    chat = Mock(side_effect=TimeoutError("slow"))
    out = loop.run_loop(CFG, "x", chat=chat, durable=True, stream=stream, on_step=lambda e: None)
    assert out["outcome"] == RoundOutcome.RETRYABLE_ERROR
    assert out["error_class"] == "MODEL_TIMEOUT"
    assert chat.call_count == 1


def test_stream_timeout_closes_and_never_executes_partial_call():
    class Broken:
        closed = False
        def __iter__(self):
            yield NS(usage=None, choices=[NS(finish_reason=None, delta=NS(content=None, tool_calls=[], reasoning_content="private"))])
            raise TimeoutError("slow")
        def close(self): self.closed = True
    stream = Broken()
    out = loop._consume_stream(stream, None)
    assert stream.closed and out["response_error"] == "MODEL_TIMEOUT"
    chat = Mock(return_value=out)
    execute = Mock()
    result = loop.run_loop(CFG, "x", chat=chat, tool_executor=execute, durable=True)
    assert result["error_class"] == "MODEL_TIMEOUT" and chat.call_count == 1
    execute.assert_not_called()


def test_late_response_yields_without_side_effect(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(loop, "time", NS(monotonic=lambda: clock[0], time=lambda: clock[0]))
    def chat(messages, tools):
        clock[0] += 2
        return {"finish_reason": "tool_calls", "tool_calls": [{"id": "x", "name": "run_sr", "arguments": "{}"}]}
    execute = Mock()
    result = loop.run_loop(CFG, "x", chat=chat, durable=True, max_seconds=1, tool_executor=execute)
    assert result["outcome"] == RoundOutcome.BUDGET_YIELD
    execute.assert_not_called()


def test_stream_options_fallback_rechecks_timeout(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(loop, "time", NS(monotonic=lambda: clock[0], time=lambda: clock[0]))
    client = Mock()
    class Unsupported(Exception): status_code = 400
    def create(**kwargs):
        if "stream_options" in kwargs:
            clock[0] += 2
            raise Unsupported("stream_options unsupported")
        assert kwargs["timeout"] == pytest.approx(7.9)
        return iter([])
    client.chat.completions.create.side_effect = create
    reserve = Mock()
    loop._stream_chat(client, CFG, [], [], None, deadline=110, fallback_reserve=reserve)
    reserve.assert_called_once()


@pytest.mark.parametrize("limit,attempts", [(1, 1), (2, 2)])
def test_real_fallback_attempts_reserved_once(monkeypatch, limit, attempts):
    class Unsupported(Exception): status_code = 400
    def create(**kwargs):
        if "stream_options" in kwargs:
            raise Unsupported("unknown field stream_options")
        return iter([NS(usage=None, choices=[NS(finish_reason="stop", delta=NS(content="done", tool_calls=[]))])])
    client = Mock()
    client.chat.completions.create.side_effect = create
    monkeypatch.setattr("backend.agent.async_transport.AsyncOpenAI", lambda **kwargs: async_client(client))
    reserve = Mock()
    result = loop.run_loop(CFG, "x", durable=True, max_model_calls=limit,
                           before_model=reserve, on_step=lambda e: None)
    assert reserve.call_count == result["model_calls"] == attempts
    assert client.chat.completions.create.call_count == attempts
    assert result["outcome"] == (RoundOutcome.BUDGET_YIELD if limit == 1 else RoundOutcome.COMPLETED_CANDIDATE)


@pytest.mark.parametrize("category,status", [("AUTHENTICATION_ERROR", 401), ("INVALID_REQUEST", 400), ("SERVICE_UNAVAILABLE", 503), ("RATE_LIMITED", 429)])
def test_http_error_categories(category, status):
    from backend.agent.request_budget import error_class
    exc = Exception("service error")
    exc.status_code = status
    assert error_class(exc) == category


@pytest.mark.parametrize("stream_error", [None, "MODEL_TIMEOUT"])
def test_usage_retained_for_late_or_timeout_output(monkeypatch, stream_error):
    clock = [100.0]
    monkeypatch.setattr(loop, "time", NS(monotonic=lambda: clock[0], time=lambda: clock[0]))
    def chat(messages, tools):
        if stream_error is None:
            clock[0] += 2
        return {"finish_reason": "stop", "content": "late", "tool_calls": [],
                "response_error": stream_error,
                "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30}}
    result = loop.run_loop(CFG, "x", chat=chat, durable=True, max_seconds=1)
    assert result["usage"]["total_tokens"] == 30
    assert result["answer"] is None


@pytest.mark.parametrize("failure", [ConnectionError("lost"), TimeoutError("slow"), RuntimeError("service")])
def test_exception_unknown_usage(failure):
    result = loop.run_loop(CFG, "x", chat=Mock(side_effect=failure))
    assert result['usage']['unknown_calls'] == 1
    assert result['usage']['calls'] == 0


def test_local_preflight_has_no_unknown(monkeypatch):
    monkeypatch.setattr(loop.chat_images, 'hydrate_images', Mock(side_effect=ValueError('local image')))
    result = loop.run_loop(CFG, 'x')
    assert result['usage'].get('unknown_calls', 0) == 0


@pytest.mark.parametrize('ending', ['success', 'timeout', 'disconnect', 'budget'])
def test_fallback_diagnostics_and_usage(monkeypatch, tmp_path, ending):
    from backend.services.store import Store
    store = Store(str(tmp_path / 'test.sqlite'))
    sid = store.create_session()
    cfg = replace(CFG, llm_thinking_adapter='reasoning_effort')
    class Unsupported(Exception): status_code = 400
    calls = []
    def create(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1: raise Unsupported('reasoning_effort unsupported')
        if ending == 'timeout': raise TimeoutError('slow')
        if ending == 'disconnect': raise ConnectionError('lost')
        return NS(usage=NS(prompt_tokens=2, completion_tokens=3, total_tokens=5),
                  choices=[NS(finish_reason='stop', message=NS(content='done', tool_calls=[]))])
    client = Mock()
    client.chat.completions.create.side_effect = create
    monkeypatch.setattr('backend.agent.async_transport.AsyncOpenAI', lambda **kwargs: async_client(client))
    recorder = Mock(run_id='test')
    recorder.event.return_value = {'event_id':'e'}
    monkeypatch.setattr(loop, 'TrajectoryRecorder', lambda *args, **kwargs: recorder)
    try:
        result = loop.run_loop(cfg, 'x', store=store, session_id=sid,
                               durable=True, max_model_calls=1 if ending == 'budget' else 2)
        ends = [c for c in recorder.event.call_args_list if c.args[0] == 'llm_request_end']
        assert len(ends) == 1
        payload = ends[0].kwargs['payload']
        assert payload['capability_rejections'] == ['reasoning_effort']
        assert payload['request_attempts'] == len(calls)
        assert payload['request_config']['thinking_profile_requested'] == cfg.llm_thinking_profile
        assert result['usage']['unknown_calls'] == (1 if ending in ('success','budget') else 2)
        assert result['usage']['calls'] == int(ending == 'success')
        if ending != 'budget': assert payload['effective_thinking_parameters'] == {}
    finally: store.close()


@pytest.mark.parametrize('known', [False, True])
def test_unknown_summary_survives_reopen(tmp_path, known):
    from backend.services.store import Store
    path = str(tmp_path / 'usage.sqlite')
    store = Store(path)
    sid = store.create_session()
    result = loop.run_loop(CFG, 'x', store=store, session_id=sid,
                           chat=Mock(side_effect=ConnectionError('lost')))
    if known:
        loop.run_loop(CFG, 'next', store=store, session_id=sid, resume=True,
                      chat=lambda m,t: {'content':'ok', 'tool_calls':[], 'finish_reason':'stop',
                          'usage':{'prompt_tokens':2, 'completion_tokens':3, 'total_tokens':5}})
    store.close()
    reopened = Store(path)
    try:
        usage = reopened.summarize_llm_usage(sid)
        assert usage['unknown_calls'] == 1
        assert usage['usage_complete'] is False
        assert usage['calls'] == int(known)
        assert usage['total_tokens'] == (5 if known else 0)
    finally: reopened.close()
