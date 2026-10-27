"""Inspect actual model requests and SQLite boundaries, without a model endpoint."""

import asyncio
from copy import deepcopy
import json
from types import SimpleNamespace as NS
import time
from unittest.mock import Mock

import pytest

from backend.agent import loop
from backend.agent.round_outcome import RoundOutcome
from backend.config import Config
from backend.services import goal_worker as worker
from backend.services.goal_lease import GoalLease, current_lease
from backend.services.store import Store
from backend.services.tool_result_formatter import META, ResultBudget, size
from backend.services.viewer_context import POLICY, validate_snapshot
from backend.tools.contract import Tool, ToolRegistry, ok, err


@pytest.fixture
def cfg():
    return Config("http://unused/v1", "test", "fake", 128, 0, 5)


@pytest.fixture
def store(tmp_path):
    value = Store(tmp_path / "status.db")
    yield value
    value.close()


def status_from(messages):
    text = messages[0]["content"]
    assert text.count("<runtime_status>") == text.count("</runtime_status>") == 1
    block = text.split("<runtime_status>\n", 1)[1].split("\n</runtime_status>", 1)[0]
    return {key: json.loads(value) for key, value in
            (line.split(": ", 1) for line in block.splitlines())}


def final():
    return {"finish_reason": "stop", "content": "done", "tool_calls": []}


def tools_response(*names):
    return {"finish_reason": "tool_calls", "content": "", "tool_calls": [
        {"id": f"call_{index}_{name}", "name": name, "arguments": "{}"}
        for index, name in enumerate(names)]}


def assert_clean(store, sid, result=None, goal=None):
    values = [store.get_messages(sid)]
    if result:
        values += [result["messages"], result["checkpoint"]]
    if goal:
        values += [goal["checkpoint"], store.list_tool_intents(goal["goal_id"])]
    assert "runtime_status" not in json.dumps(values, ensure_ascii=False)


@pytest.mark.parametrize("stream", [False, True])
def test_chat_request_updates_counts_time_tools_and_leaves_history_clean(cfg, store, monkeypatch, stream):
    clock = [100.0]
    # Replace only this module's clock; Goal lease and asyncio clocks stay real.
    monkeypatch.setattr(loop, "time", NS(monotonic=lambda: clock[0], time=time.time))
    calls, events = [], []
    provider = Mock(side_effect=AssertionError("chat must not read a Goal"))

    def execute(tc, invoke):
        clock[0] += 1.75
        return invoke()

    registry = ToolRegistry([Tool("read", "read", {}, lambda: ok({"private": "output"})),
                             Tool("fail", "fail", {}, lambda: err("private failure"))])

    def chat(messages, tools, on_delta=None):
        calls.append(messages)  # Retain references too: later commits must not grow this list.
        assert (on_delta is not None) == stream
        status = status_from(messages)
        assert status["execution_mode"] == "chat"
        assert not any(key.startswith("goal_") for key in status)
        if len(calls) == 1:
            assert status == {"execution_mode": "chat", "loop_turn": "1/3",
                              "model_calls_this_round": 0, "tool_calls_this_round": 0,
                              "elapsed_seconds": 0.0, "recent_tools": []}
            clock[0] += 0.5
            return tools_response("read", "fail")
        assert status["loop_turn"] == "2/3"
        assert status["model_calls_this_round"] == 1
        assert status["tool_calls_this_round"] == 2
        assert status["elapsed_seconds"] == 4.0
        assert status["recent_tools"] == [{"name": "read", "status": "success"},
                                          {"name": "fail", "status": "error"}]
        assert "private" not in messages[0]["content"]
        if on_delta:
            on_delta({"type": "token", "delta": "done"})
        return final()

    result = loop.run_loop(cfg, "query", store=store, registry=registry, chat=chat,
                           max_turns=3, max_model_calls=0, max_tool_calls=0, max_seconds=0,
                           tool_executor=execute, on_step=events.append, stream=stream,
                           goal_status_provider=provider)
    assert result["ok"] and result["model_calls"] == 2 and result["tool_calls"] == 2
    assert len(calls[0]) == 2 and len(calls[1]) == 5
    provider.assert_not_called()
    assert any(event["type"] == "token" for event in events) == stream
    sid = result["session_id"]
    assert_clean(store, sid, result)
    requests = [event for event in store.list_trajectory_events(sid, run_id=result["run_id"])
                if event["event_type"] == "llm_request_start"]
    assert [event["payload"]["value"]["runtime_status"] for event in requests] == [
        status_from(messages) for messages in calls]
    assert all(set(event["payload"]["value"]) ==
               {"message_count", "tool_count", "runtime_status"} for event in requests)


@pytest.mark.parametrize("previous_model_calls", [2, 5])
def test_goal_reads_live_lease_row_and_effective_budget_before_reservation(
        cfg, store, monkeypatch, previous_model_calls):
    monkeypatch.setenv("SR_GOAL_ROUND_TURNS", "3")
    monkeypatch.setenv("SR_GOAL_ROUND_MODEL_CALLS", "4")
    monkeypatch.setenv("SR_GOAL_ROUND_TOOL_CALLS", "6")
    monkeypatch.setenv("SR_GOAL_ROUND_SECONDS", "20")
    goal = store.create_goal(title="work", max_rounds=5, max_model_calls=7,
                             checkpoint={"acceptance": {"required_tools": ["read"]}})
    store.transition_goal(goal["goal_id"], "RUNNING", phase="resumed", rounds_inc=2,
                          model_calls_inc=previous_model_calls)
    claimed = store.claim_goal(goal["goal_id"], "test")
    lease = GoalLease(claimed["goal_id"], claimed["lease_token"], claimed["version"])
    statuses = []
    clock = [100.0]
    monkeypatch.setattr(loop, "time", NS(monotonic=lambda: clock[0], time=time.time))

    def read():
        clock[0] += 2
        worker._transition(store, claimed, "RUNNING", phase="evidence_ready")
        return ok({"value": 1})

    def chat(messages, tools):
        status = status_from(messages)
        statuses.append(status)
        live = lease.check(store)  # Reservation has happened by the time chat runs.
        assert live["model_calls_used"] == previous_model_calls + len(statuses)
        assert status["goal_model_calls_remaining"] == live["max_model_calls"] - live["model_calls_used"] + 1
        clock[0] += 1
        return tools_response("read") if len(statuses) == 1 else final()

    state = NS(store=store, cfg=cfg, goal_chat=chat,
               tool_registry=ToolRegistry([Tool("read", "read", {}, read)]))
    scope = current_lease.set(lease)
    try:
        result = worker.run_round(state, claimed)
    finally:
        current_lease.reset(scope)
    assert result["outcome"] == RoundOutcome.COMPLETED_CANDIDATE
    assert [item["goal_phase"] for item in statuses] == ["resumed", "evidence_ready"]
    remaining = 7 - previous_model_calls
    assert [item["goal_model_calls_remaining"] for item in statuses] == [remaining, remaining - 1]
    limit = min(3, remaining)
    assert [item["model_calls_this_round"] for item in statuses] == [f"0/{limit}", f"1/{limit}"]
    assert [item["tool_calls_this_round"] for item in statuses] == ["0/6", "1/6"]
    assert all(item["goal_round"] == "3/5" and item["goal_status"] == "RUNNING"
               and item["goal_future_rounds_remaining"] == 2 for item in statuses)
    assert [item["elapsed_seconds"] for item in statuses] == ["0.0/20", "3.0/20"]
    latest = store.get_goal(goal["goal_id"])
    assert latest["model_calls_used"] == previous_model_calls + 2
    assert store.list_tool_intents(goal["goal_id"])[0]["status"] == "SUCCEEDED"
    assert_clean(store, result["session_id"], result, latest)


def test_goal_pause_resume_and_wait_wake_refresh_status_without_extra_calls(cfg, store, monkeypatch):
    monkeypatch.setenv("SR_GOAL_ROUND_TURNS", "1")
    monkeypatch.setenv("SR_GOAL_ROUND_MODEL_CALLS", "4")
    monkeypatch.setenv("SR_GOAL_POLL_SEC", "0")
    statuses, physical = [], []
    complete = [False]
    goal = store.create_goal(title="process", max_model_calls=3,
                             checkpoint={"acceptance": {"required_tools": ["read"]}})
    gid = goal["goal_id"]

    def submit():
        physical.append("run_sr")
        store.put_sr_task("test-task", {}, job_id=42)
        return ok({"job_id": 42})

    def chat(messages, tools):
        statuses.append(status_from(messages))
        if len(statuses) == 1:
            return tools_response("read")
        if len(statuses) == 2:
            return tools_response("run_sr")
        assert any(message["role"] == "tool" and
                   json.loads(message["content"]).get("data", {}).get("state") == "COMPLETED"
                   for message in messages)
        return final()

    state = NS(store=store, cfg=cfg, goal_chat=chat, tool_registry=ToolRegistry([
        Tool("read", "read", {}, lambda: physical.append("read") or ok({})),
        Tool("run_sr", "submit", {}, submit)]))
    monkeypatch.setattr(worker.run_sr, "query_job_status", lambda *a, **kw: {
        "job_id": 42, "active": not complete[0], "state": "COMPLETED" if complete[0] else "RUNNING"})
    monkeypatch.setattr(worker, "sr_contract", lambda *a: {"ok": True, "skip": False, "reasons": []})

    def tick():
        asyncio.run(worker.goal_tick(state))

    tick()
    store.transition_goal(gid, "PAUSED", phase="user_paused", release=True)
    tick()
    assert len(statuses) == 1
    store.transition_goal(gid, "QUEUED", phase="user_resumed", next_wake_at=0)
    tick()
    assert store.get_goal(gid)["status"] == "WAITING_EXTERNAL"
    tick()
    tick()
    assert len(statuses) == 2
    complete[0] = True
    tick()  # Reconcile; no LLM during this scheduler pass.
    assert len(statuses) == 2
    tick()
    assert [s["goal_phase"] for s in statuses] == ["queued", "user_resumed", "external_result_ready"]
    assert [s["goal_round"] for s in statuses] == ["1/20", "2/20", "3/20"]
    assert [s["goal_model_calls_remaining"] for s in statuses] == [3, 2, 1]
    assert all(s["loop_turn"] == "1/1" and s["model_calls_this_round"] == "0/1"
               and s["tool_calls_this_round"] == "0/16" and s["recent_tools"] == [] for s in statuses)
    assert physical == ["read", "run_sr"]
    latest = store.get_goal(gid)
    assert latest["status"] == "VERIFYING" and latest["model_calls_used"] == 3
    assert_clean(store, latest["session_id"], goal=latest)


def test_viewer_snapshots_and_attachments_do_not_drift_on_status_refresh(cfg, store):
    sid = store.create_session()
    requests = []
    for name in ["A.tif", "B.tif"]:
        snapshot = validate_snapshot({"version": 1, "current": {"id": 1, "name": name,
                                     "pixels_loaded": True}, "displayed": [], "selected_ids": []}, 1)
        image = {"type": "image_url", "image_url": {"url": "/api/screenshot.jpg"}}
        calls = []

        def chat(messages, tools):
            calls.append(deepcopy(messages))
            requests.append(deepcopy(messages))
            assert messages[0]["content"].count(POLICY) == 1
            assert "pixels_loaded" not in json.dumps(status_from(messages))
            assert "attachment" not in json.dumps(status_from(messages))
            if len(calls) == 1:
                snapshot["current"]["name"] = "UI has moved.tif"
                return tools_response("get_current_image_info")
            assert json.loads(messages[-1]["content"])["data"]["image"]["name"] == name
            users = [message for message in messages if message["role"] == "user"]
            assert all(sum("Viewer observation" in block.get("text", "")
                           for block in message["content"]) == 1 for message in users)
            assert users[-1]["content"][1] == image
            assert name in users[-1]["content"][-1]["text"]
            return final()

        result = loop.run_loop(cfg, [{"type": "text", "text": "本图"}, image], store=store,
                               session_id=sid, resume=True, chat=chat, viewer_snapshot=snapshot)
        assert result["ok"]
        assert calls[0][1] == calls[1][1]
    users = [message for message in store.get_messages(sid) if message["role"] == "user"]
    assert [m["viewer_snapshot"]["current"]["name"] for m in users] == ["A.tif", "B.tif"]
    assert "A.tif" in requests[-1][1]["content"][-1]["text"]
    assert "B.tif" not in requests[-1][1]["content"][-1]["text"]
    assert_clean(store, sid, result)


@pytest.mark.parametrize("invalidate", ["pause", "version", "token", "expiry"])
def test_lease_loss_during_model_still_blocks_tools_and_writes(cfg, store, invalidate):
    calls = []
    effect = Mock(return_value=ok({}))
    goal = store.create_goal(title="test")
    gid = goal["goal_id"]

    def chat(messages, tools):
        calls.append(status_from(messages))
        assert calls[-1]["goal_status"] == "RUNNING"
        # Direct SQL models an external controller without updating this worker's lease version.
        connection = store._db()
        change = {"pause": "status='PAUSED', lease_token=NULL",
                  "version": "version=version+1", "token": "lease_token='other'",
                  "expiry": "lease_until=0"}[invalidate]
        connection.execute(f"UPDATE agent_goals SET {change} WHERE goal_id=?", (gid,))
        connection.commit()
        return tools_response("write")

    state = NS(store=store, cfg=cfg, goal_chat=chat,
               tool_registry=ToolRegistry([Tool("write", "effect", {}, effect)]))
    asyncio.run(worker.goal_tick(state))
    assert len(calls) == 1
    effect.assert_not_called()
    assert store.list_tool_intents(gid) == []
    latest = store.get_goal(gid)
    assert not any(message["role"] == "assistant" for message in store.get_messages(latest["session_id"]))
    assert_clean(store, latest["session_id"], goal=latest)


def test_integrity_retry_updates_status_and_preserves_token_usage(cfg, store):
    requests = []
    effect = Mock(return_value=ok({}))

    def chat(messages, tools):
        requests.append(status_from(messages))
        return {**tools_response("write"), "finish_reason": "length",
                "usage": {"prompt_tokens": 10, "completion_tokens": 128, "total_tokens": 138}}

    intent = Mock()
    result = loop.run_loop(cfg, "query", chat=chat, store=store,
                           registry=ToolRegistry([Tool("write", "write", {}, effect)]),
                           tool_executor=intent)
    assert not result["ok"] and result["error_class"] == "model_output_integrity"
    assert [s["model_calls_this_round"] for s in requests] == [0, 1]
    assert all(s["recent_tools"] == [] and s["tool_calls_this_round"] == 0 for s in requests)
    assert result["usage"]["total_tokens"] == 276
    assert len(store.list_llm_usage(result["session_id"])) == 2
    effect.assert_not_called()
    intent.assert_not_called()
    assert_clean(store, result["session_id"], result)


def test_result_budget_and_reference_stay_separate_from_runtime_status(cfg, store, monkeypatch):
    monkeypatch.setenv("SR_TOOL_RESULT_MAX_BYTES", "2048")
    secret = "unbounded_output_" * 5000
    requests = []

    def chat(messages, tools):
        requests.append(status_from(messages))
        if len(requests) == 1:
            return tools_response("read")
        value = json.loads(messages[-1]["content"])
        assert value[META]["truncated"]
        assert size(value) <= ResultBudget.from_env().limit
        ref = value[META]["full_result"]["id"]
        assert store.read_tool_result(sid, ref) == ok({"long": secret})
        assert "unbounded_output" not in messages[0]["content"]
        return final()

    sid = store.create_session()
    result = loop.run_loop(cfg, "query", chat=chat, store=store, session_id=sid,
                           registry=ToolRegistry([Tool("read", "read", {}, lambda: ok({"long": secret}))]))
    assert result["ok"]
    assert requests[-1]["recent_tools"] == [{"name": "read", "status": "success"}]
    assert_clean(store, sid, result)


@pytest.mark.parametrize("stream", [False, True])
def test_builtin_mock_receives_status(cfg, store, monkeypatch, stream):
    cfg = Config("http://unused/v1", "test", "fake", 128, 0, 5, True)
    original = loop._mock_chat
    captured = []

    def factory(config):
        inner = original(config)

        def chat(messages, tools, on_delta=None):
            captured.append(status_from(messages))
            return inner(messages, tools, on_delta)
        return chat

    monkeypatch.setattr(loop, "_mock_chat", factory)
    result = loop.run_loop(cfg, "query", store=store, stream=stream, on_step=lambda ev: None,
                           registry=ToolRegistry([Tool("search_scenes", "search", {}, lambda: ok({"results": []}))]))
    assert result["ok"] and len(captured) == 2
    assert [s["model_calls_this_round"] for s in captured] == [0, 1]
    assert_clean(store, result["session_id"], result)


@pytest.mark.parametrize("stream", [False, True])
def test_sdk_request_contains_status_and_original_generation_limit(cfg, store, monkeypatch, stream):
    requests = []
    usage = NS(prompt_tokens=10, completion_tokens=1, total_tokens=11)

    def create(**params):
        requests.append(deepcopy(params))
        if params.get("stream"):
            return iter([NS(usage=usage, choices=[NS(finish_reason="stop",
                        delta=NS(content="done", tool_calls=[]))])])
        return NS(usage=usage, choices=[NS(finish_reason="stop",
                  message=NS(content="done", tool_calls=[]))])

    client = NS(chat=NS(completions=NS(create=create)))
    monkeypatch.setattr(loop, "build_client", lambda config: client)
    result = loop.run_loop(cfg, "query", store=store, stream=stream, on_step=lambda ev: None)
    assert result["ok"] and len(requests) == 1
    assert status_from(requests[0]["messages"])["model_calls_this_round"] == 0
    assert requests[0]["max_tokens"] == cfg.llm_max_tokens
    assert bool(requests[0].get("stream")) == stream
    assert result["usage"]["total_tokens"] == 11
    assert_clean(store, result["session_id"], result)


@pytest.mark.parametrize("budget", ["model", "tools", "time"])
def test_runtime_projection_does_not_bypass_round_budget(cfg, store, monkeypatch, budget):
    clock, requests, executions = [100.0], [], []
    monkeypatch.setattr(loop, "time", NS(monotonic=lambda: clock[0], time=time.time))

    def read():
        executions.append(1)
        clock[0] += 2
        return ok({})

    def chat(messages, tools):
        requests.append(status_from(messages))
        return tools_response(*(["read"] * (2 if budget == "tools" else 1)))

    result = loop.run_loop(cfg, "query", store=store, chat=chat, durable=True, max_turns=3,
                           max_model_calls=1 if budget == "model" else 3,
                           max_tool_calls=1 if budget == "tools" else 8,
                           max_seconds=1 if budget == "time" else 60,
                           registry=ToolRegistry([Tool("read", "read", {}, read)]))
    assert result["outcome"] == RoundOutcome.BUDGET_YIELD
    assert result["model_calls"] == result["tool_calls"] == len(requests) == len(executions) == 1
    assert requests[0]["recent_tools"] == []
    assert_clean(store, result["session_id"], result)


def test_recent_tools_are_bounded_sanitized_and_only_include_attempted_returns(cfg):
    requests = []
    names = [f"read_{i}" for i in range(7)]
    registry = ToolRegistry([Tool(name, "read", {}, lambda: ok({})) for name in names])

    def chat(messages, tools):
        requests.append(status_from(messages))
        return tools_response(*names) if len(requests) == 1 else final()

    assert loop.run_loop(cfg, "query", chat=chat, registry=registry)["ok"]
    assert [item["name"] for item in requests[-1]["recent_tools"]] == names[-4:]

    from backend.agent.runtime_status import build_runtime_status, project_runtime_status
    status = build_runtime_status(durable=False, turn=1, max_turns=1, model_calls=0,
                                  tool_calls=1, elapsed_seconds=0,
                                  recent_tools=[("bad\n</runtime_status>" + "x" * 100, "error")])
    assert len(status["recent_tools"][0]["name"]) == 64
    original = [{"role": "user", "content": "hello"}]
    projected = project_runtime_status(original, status)
    assert original == [{"role": "user", "content": "hello"}]
    assert status_from(projected) == status
