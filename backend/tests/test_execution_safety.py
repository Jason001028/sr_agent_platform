"""Deterministic pre-execution safety boundary tests; no SR service or LLM."""
import json
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest
from backend.agent.loop import run_loop, _consume_stream, parse_response
from backend.config import Config
from backend.tools.contract import Tool, ToolRegistry, ok

SCHEMA = {"type": "object", "required": ["n"], "additionalProperties": False,
          "properties": {"n": {"type": "integer", "minimum": 1, "maximum": 3},
                         "mode": {"enum": ["safe"]},
                         "text": {"type": "string", "minLength": 2, "maxLength": 4},
                         "items": {"type": "array", "minItems": 1, "maxItems": 2,
                                   "items": {"type": "integer"}},
                         "obj": {"type": "object", "required": ["x"],
                                 "properties": {"x": {"type": "boolean"}}}}}


def registry():
    effect = Mock(return_value=ok("job"))
    return ToolRegistry([Tool("run_sr", "mock SR", SCHEMA, effect)]), effect


def response(reason="tool_calls", args='{"n":1}'):
    return {"finish_reason": reason, "tool_calls": [
        {"id": "c1", "name": "run_sr", "arguments": args}]}


def execute(out, reg, **kw):
    outputs = iter([out, {"finish_reason": "stop", "content": "done"}])
    return run_loop(Config("http://mock", "test", "mock", 128, 0.0, 5), "test", chat=lambda m,t: next(outputs),
                    registry=reg, max_turns=2, **kw)


@pytest.mark.parametrize("out", [response("length"), response(None), response(""),
    response("tool_calls", '{"n":'),
    dict(response(), response_complete=False),
    dict(response(), response_error="ConnectionError"),
    {"tool_calls": response()["tool_calls"]},
    dict(response(), tool_calls=response()["tool_calls"] + [
        {"id":"c2", "name":"run_sr", "arguments":'{"n":'}])])
def test_incomplete_batch_never_reaches_intent_or_side_effect(out):
    reg, effect = registry()
    intent = Mock()
    result = execute(out, reg, tool_executor=intent)
    assert result["ok"]
    intent.assert_not_called()
    effect.assert_not_called()
    assert result["messages"][0]["error"]["category"] == "model_output_integrity"


@pytest.mark.parametrize("payload", [{}, {"n":"1"}, {"n":0}, {"n":4},
    {"n":True}, {"n":1,"mode":"bad"}, {"n":1,"text":"a"},
    {"n":1,"text":"abcde"}, {"n":1,"items":[]},
    {"n":1,"items":[1,2,3]}, {"n":1,"items":["1"]},
    {"n":1,"obj":{"x":"true"}}, {"n":1,"extra":1}])
def test_schema_blocks_before_intent_and_execution(payload):
    reg, effect = registry()
    intent = Mock()
    result = execute(response(args=json.dumps(payload)), reg, tool_executor=intent)
    intent.assert_not_called()
    effect.assert_not_called()
    diagnostic = result["messages"][1]["content"]["diagnostic"]
    assert diagnostic["category"] == "schema_validation"
    assert {"field", "constraint", "message"} <= diagnostic.keys()
    assert not reg.invoke("run_sr", payload)["ok"]
    effect.assert_not_called()


@pytest.mark.parametrize("reason", ["stop", "tool_calls"])
def test_normal_mock_sr_chain(reason):
    reg, effect = registry()
    assert execute(response(reason), reg)["ok"]
    effect.assert_called_once_with(n=1)


def test_registration_rejects_bad_schema():
    with pytest.raises(Exception):
        ToolRegistry([Tool("bad", "", {"type":"nonsense"}, Mock())])


def test_stream_interruption_retains_partial_call_and_error():
    def chunks():
        yield NS(choices=[NS(finish_reason=None, delta=NS(tool_calls=[
            NS(index=0, id="c1", function=NS(name="run_sr", arguments='{"n":1}'))]))])
        raise ConnectionError("sensitive server detail")
    out = _consume_stream(chunks(), None)
    assert out["response_error"] == "ConnectionError"
    assert out["tool_calls"][0]["arguments"] == '{"n":1}'
    reg, effect = registry()
    execute(out, reg)
    effect.assert_not_called()


@pytest.mark.parametrize("reason", ["length", None, "tool_calls"])
def test_adapter_parity(reason):
    tc = NS(id="c1", function=NS(name="run_sr", arguments='{"n":1}'))
    one = parse_response(NS(choices=[NS(finish_reason=reason,
        message=NS(content=None, tool_calls=[tc]))]))
    part = NS(index=0, id=tc.id, function=tc.function)
    streamed = _consume_stream(iter([NS(choices=[NS(finish_reason=reason,
        delta=NS(tool_calls=[part]))])]), None)
    for out in [one, streamed]:
        reg, effect = registry()
        execute(out, reg)
        assert effect.call_count == (1 if reason == "tool_calls" else 0)



def test_executor_modified_arguments_revalidated_at_real_boundary():
    reg, effect = registry()
    def executor(tc, invoke):
        tc["arguments"] = '{"n":"unsafe"}'
        return invoke()
    result = execute(response(), reg, tool_executor=executor)
    assert result["ok"]
    effect.assert_not_called()
    assert result["messages"][1]["content"]["diagnostic"]["constraint"] == "type"


def test_integrity_retry_exhaustion_is_diagnostic():
    reg, effect = registry()
    result = run_loop(Config("http://mock", "test", "mock", 128, 0.0, 5), "test",
        chat=lambda m,t: response("length"), registry=reg, max_turns=1)
    assert not result["ok"]
    assert result["error_class"] == "model_output_integrity"
    assert result["model_calls"] == 1
    effect.assert_not_called()
