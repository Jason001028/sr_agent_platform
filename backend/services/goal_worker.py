"""Bounded durable rounds, external reconciliation and independent acceptance."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import json
import logging
import os
from pathlib import Path
import time
import uuid

from backend.agent import loop as agent_loop
from backend.agent.round_outcome import RoundOutcome as Outcome
from backend.config import sr_runtime
from backend.services import run_sr
from backend.services.goal_lease import (
    GoalLease,
    LeaseLost,
    current_lease,
    current_tool,
)

log = logging.getLogger(__name__)
SCHEDULED = {"QUEUED", "RUNNING", "RETRY_WAIT", "WAITING_EXTERNAL", "VERIFYING"}


def _transition(store, goal, target, **kwargs):
    lease = current_lease.get()
    return store.transition_goal(
        goal["goal_id"],
        target,
        expected_version=lease.version,
        lease_token=lease.token,
        **kwargs,
    )


def _encoded_result_view(store, sid, name, call_id, result, ordinal=None):
    from backend.services.tool_results import ToolResults
    from backend.services.tool_result_formatter import encode
    count = ordinal if ordinal is not None else sum(m["role"] == "assistant" for m in store.get_messages(sid))
    return encode(ToolResults(store, sid).model_view(name, result, f"{sid}:{count}:{call_id}"))


def _intent_key(store, sid, call_id):
    count = sum(m["role"] == "assistant" for m in store.get_messages(sid))
    return f"{sid}:{count}:{call_id}"


def _execute_tool(state, goal, tc, invoke):
    store = state.store
    lease = current_lease.get()
    lease.check(store)
    args = agent_loop._parse_args(tc["arguments"])
    readonly = tc["name"] in {"search_scenes", "sr_job_status", "read_tool_result"} or (
        tc["name"] == "fix_bad_lines" and not args.get("output_path")
    )
    # Unknown extension tools are conservatively non-replayable.
    key = _intent_key(store, goal["session_id"], tc["id"])
    old = store.get_tool_intent(goal["goal_id"], key)
    if old:
        if old["result"] and {"ok", "data", "error"} <= old["result"].keys():
            return old["result"]
        if not readonly:
            return {
                "ok": False,
                "data": None,
                "error": "unreconciled previous side effect",
                "control": "BLOCKED",
            }
    ordinal = len(store.list_tool_intents(goal["goal_id"])) + 1
    with store.worker_transaction():
        step = store.put_goal_step(
            goal["goal_id"],
            ordinal=ordinal,
            name=tc["name"],
            tool_name=tc["name"],
            status="RUNNING",
        )
        intent = store.create_tool_intent(
            goal_id=goal["goal_id"],
            step_id=step["step_id"],
            tool_name=tc["name"],
            args=args,
            idempotency_key=key,
            side_effect_class="READ_ONLY" if readonly else "RECONCILE_REQUIRED",
            replay_policy="REPLAY_READ" if readonly else "RECONCILE_BEFORE_REPLAY",
        )
        store.update_tool_intent(
            intent["intent_id"], status="EXECUTING", attempts_inc=True
        )
    scope = current_tool.set({"store": store, "intent": intent})
    try:
        lease.check(store)  # after the model and immediately before invocation
        result = invoke()
    finally:
        current_tool.reset(scope)
    lease.check(store)
    known = store.get_tool_intent(goal["goal_id"], key)
    data = result.get("data") or {}
    job_id = (
        data.get("job_id")
        if tc["name"] == "run_sr" and isinstance(data, dict) and result.get("ok")
        else None
    )
    # An interrupted submission has a durable sr_tasks row with no receipt.
    uncertain = False
    if tc["name"] == "run_sr" and not result.get("ok"):
        fingerprint = (known.get("result") or {}).get("fingerprint")
        task = store.get_sr_task(fingerprint) if fingerprint else None
        uncertain = bool(task and task["job_id"] is None)
        uncertain = uncertain or result.get("error_class") == "external_unknown"
    with store.worker_transaction():
        store.update_tool_intent(
            intent["intent_id"],
            status=(
                "UNKNOWN"
                if uncertain
                else (
                    "SUBMITTED"
                    if job_id is not None
                    else ("SUCCEEDED" if result.get("ok") else "FAILED")
                )
            ),
            result=result,
            external_job_id=job_id,
        )
        store.put_goal_step(
            goal["goal_id"],
            ordinal=ordinal,
            name=tc["name"],
            tool_name=tc["name"],
            status=(
                "WAITING_EXTERNAL"
                if job_id is not None
                else ("SUCCEEDED" if result.get("ok") else "FAILED")
            ),
        )
        store._db().execute(
            "UPDATE agent_goal_steps SET external_job_id=? WHERE step_id=?",
            (job_id, step["step_id"]),
        )
        store.append_goal_event(
            goal["goal_id"],
            "tool_finished",
            {"tool": tc["name"], "ok": result.get("ok"), "job_id": job_id},
            step_id=step["step_id"],
        )
    if uncertain:
        result = {**result, "control": "BLOCKED"}
    return result


def _tool_control(tc, result):
    controls = {
        o.value: o
        for o in (
            Outcome.CONTINUE,
            Outcome.NEEDS_APPROVAL,
            Outcome.RETRYABLE_ERROR,
            Outcome.FAILED,
            Outcome.BLOCKED,
        )
    }
    if result.get("control") in controls:
        return {
            "outcome": controls[result["control"]],
            "error_class": result.get("error_class", "tool_control"),
        }
    data = result.get("data") or {}
    if (
        tc["name"] == "run_sr"
        and result.get("ok")
        and isinstance(data, dict)
        and data.get("job_id") is not None
    ):
        return {
            "outcome": Outcome.WAITING_EXTERNAL,
            "external_job_id": int(data["job_id"]),
            "tool_call_id": tc["id"],
        }
    return None


def run_round(state, goal):
    store = state.store
    sid = goal.get("session_id")
    with store.worker_transaction():
        if not sid:
            sid = store.create_session({"goal_id": goal["goal_id"]})
            store.set_goal_session(goal["goal_id"], sid)
        cp = dict(goal["checkpoint"])
        if not cp.get("input_admitted"):
            history = store.get_messages(sid)
            if not history:
                store.append_message(
                    sid, {"role": "system", "content": agent_loop.DEFAULT_SYSTEM_PROMPT}
                )
            if not any(
                m["role"] == "user" and m.get("content") == goal["title"]
                for m in history
            ):
                store.append_message(sid, {"role": "user", "content": goal["title"]})
            if cp.get("acceptance"):
                store.append_message(
                    sid,
                    {
                        "role": "system",
                        "content": "Internal Goal acceptance criteria: "
                        + json.dumps(cp["acceptance"], ensure_ascii=False),
                    },
                )
            cp["input_admitted"] = True
        store.set_session_status(sid, "active")
        _transition(
            store,
            goal,
            "RUNNING",
            checkpoint=cp,
            rounds_inc=1,
            event_type="round_started",
        )
    goal = {**goal, "session_id": sid}
    remaining = goal["max_model_calls"] - goal["model_calls_used"]

    def reserve_model():
        _transition(
            store, goal, "RUNNING", model_calls_inc=1, event_type="model_call_reserved"
        )

    return agent_loop.run_loop(
        cfg=state.cfg,
        prompt=None,
        max_turns=max(1, int(os.getenv("SR_GOAL_ROUND_TURNS", "4"))),
        max_model_calls=min(
            remaining, max(1, int(os.getenv("SR_GOAL_ROUND_MODEL_CALLS", "4")))
        ),
        max_tool_calls=max(1, int(os.getenv("SR_GOAL_ROUND_TOOL_CALLS", "16"))),
        max_seconds=max(0.01, float(os.getenv("SR_GOAL_ROUND_SECONDS", "60"))),
        store=store,
        session_id=sid,
        resume=True,
        durable=True,
        registry=state.tool_registry,
        chat=getattr(state, "goal_chat", None),
        before_model=reserve_model,
        goal_status_provider=lambda: current_lease.get().check(store),
        goal_id=goal["goal_id"],
        tool_executor=lambda tc, invoke: _execute_tool(state, goal, tc, invoke),
        result_hook=_tool_control,
    )


def sr_contract(task, status):
    """Re-run the deployed pure-stdlib verifier against current files."""
    script = Path(os.getenv("SR_VERIFY_SCRIPT", "verify_sr_run.py"))
    if (task.get('params') or {}).get('repair'):
        script = Path(__file__).resolve().parents[2] / 'SR_code/variants/verify_sr_run.py'
    if not script.is_absolute():
        script = Path(sr_runtime().bundle_dir) / script
    if not script.is_file():
        script = (
            Path(__file__).resolve().parents[2] / "SR_code/variants/verify_sr_run.py"
        )
    spec = importlib.util.spec_from_file_location("sr_goal_contract", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    try:
        rc = int(str(status.get("exit_code")).split(":")[0])
    except (TypeError, ValueError):
        rc = None
    verdict = module.check_contract(task.get("config_xml"), rc, task["job_id"])
    return {
        "ok": verdict.ok,
        "skip": verdict.skip,
        "output": verdict.output,
        "reasons": verdict.reasons,
    }


def reconcile_goal(state, goal):
    store = state.store
    cp = dict(goal["checkpoint"])
    job_id = cp.get("external_job_id")
    task = store.get_sr_task_by_job(job_id) if job_id is not None else None
    if not task:
        return _transition(
            store,
            goal,
            "BLOCKED",
            error="external job record not found",
            release=True,
            event_type="external_job_unknown",
        )
    status = run_sr.query_job_status(job_id, task=task)
    if status.get("active"):
        return _transition(
            store,
            goal,
            "WAITING_EXTERNAL",
            release=True,
            next_wake_at=time.time() + float(os.getenv("SR_GOAL_POLL_SEC", "5")),
            event_type="external_job_waiting",
            event_payload=status,
        )
    if status.get("state") == "UNKNOWN" or status.get("active") is None:
        return _transition(
            store,
            goal,
            "BLOCKED",
            error="external SR state UNKNOWN",
            release=True,
            event_type="external_job_unknown",
            event_payload=status,
        )
    contract = (
        sr_contract(task, status)
        if status["state"] == "COMPLETED"
        else {"ok": False, "reasons": ["SR job " + status["state"]]}
    )
    result = {
        "ok": bool(contract["ok"]),
        "data": {**status, "contract": contract},
        "error": None if contract["ok"] else "; ".join(contract["reasons"]),
    }
    cp.pop("external_job_id", None)
    cp["external_result"] = result
    with store.worker_transaction():
        sid = goal["session_id"]
        cid = "external_" + uuid.uuid4().hex
        store.append_message(
            sid,
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": cid,
                        "type": "function",
                        "function": {
                            "name": "sr_job_status",
                            "arguments": json.dumps({"job_id": job_id}),
                        },
                    }
                ],
            },
        )
        store.append_message(
            sid,
            {
                "role": "tool",
                "tool_call_id": cid,
                "content": _encoded_result_view(store, sid, "sr_job_status", cid, result),
            },
        )
        for intent in store.list_tool_intents(goal["goal_id"]):
            if intent["external_job_id"] == job_id:
                store.update_tool_intent(
                    intent["intent_id"],
                    status="SUCCEEDED" if contract["ok"] else "FAILED",
                    result=result,
                )
                store._db().execute(
                    "UPDATE agent_goal_steps SET status=?,verification=?,updated_at=? WHERE step_id=?",
                    (
                        "SUCCEEDED" if contract["ok"] else "FAILED",
                        json.dumps(contract),
                        time.time(),
                        intent["step_id"],
                    ),
                )
        return _transition(
            store,
            goal,
            "RUNNING",
            checkpoint=cp,
            release=True,
            next_wake_at=time.time(),
            phase="external_result_ready",
            event_type="external_job_terminal",
            event_payload=result,
        )


def validate_acceptance(spec):
    """Reject criteria that could vacuously prove an empty Goal complete."""
    if not isinstance(spec, dict):
        raise ValueError("acceptance must be an object")
    if set(spec) - {
        "min_sr_jobs",
        "required_tools",
        "artifacts",
        "allow_skip",
        "sr_scenes",
    }:
        raise ValueError("unsupported acceptance criterion")
    minimum = spec.get("min_sr_jobs", 0)
    if type(minimum) is not int or minimum < 0:
        raise ValueError("min_sr_jobs must be a non-negative integer")
    for key in ("required_tools", "artifacts", "sr_scenes"):
        values = spec.get(key, [])
        if not isinstance(values, list) or any(
            not isinstance(v, str) or not v.strip() for v in values
        ):
            raise ValueError(key + " must be a list of non-empty strings")
    if "allow_skip" in spec and type(spec["allow_skip"]) is not bool:
        raise ValueError("allow_skip must be a boolean")
    if not minimum and not any(
        spec.get(k) for k in ("required_tools", "artifacts", "sr_scenes")
    ):
        raise ValueError("explicit business acceptance criteria required")
    return spec


def verify_goal(state, goal):
    """Acceptance is explicit input; evidence is re-read by backend services."""
    try:
        spec = validate_acceptance(goal["checkpoint"].get("acceptance"))
    except ValueError as exc:
        return {"ok": False, "blocked": True, "reasons": [str(exc)]}
    intents = state.store.list_tool_intents(goal["goal_id"])
    reasons = []
    succeeded = {i["tool_name"] for i in intents if i["status"] == "SUCCEEDED"}
    for name in spec.get("required_tools", []):
        if name not in succeeded:
            reasons.append("required tool not successful: " + name)
    if any(
        i["status"] in {"UNKNOWN", "EXECUTING", "INTENDED", "SUBMITTED"}
        for i in intents
    ):
        return {"ok": False, "blocked": True, "reasons": ["unreconciled tool intent"]}
    count = 0
    verified_scenes = set()
    for job_id in {
        i["external_job_id"] for i in intents if i["external_job_id"] is not None
    }:
        task = state.store.get_sr_task_by_job(job_id)
        if not task:
            return {"ok": False, "blocked": True, "reasons": ["missing SR task"]}
        status = run_sr.query_job_status(job_id, task=task)
        if status["state"] == "UNKNOWN":
            return {
                "ok": False,
                "blocked": True,
                "reasons": ["SR state UNKNOWN during acceptance"],
            }
        check = sr_contract(task, status)
        if (
            status["state"] == "COMPLETED"
            and check["ok"]
            and (not check["skip"] or spec.get("allow_skip") is True)
        ):
            count += 1
            verified_scenes.add(
                str(task["params"].get("lq_path", "")).replace("\\", "/").rstrip("/")
            )
        else:
            reasons.extend(check["reasons"] or ["SR result failed or policy skipped"])
    if count < int(spec.get("min_sr_jobs", 0)):
        reasons.append("insufficient verified SR jobs")
    for scene in spec.get("sr_scenes", []):
        if scene.replace("\\", "/").rstrip("/") not in verified_scenes:
            reasons.append("required SR scene not verified: " + scene)
    for filename in spec.get("artifacts", []):
        path = Path(filename)
        if not path.is_absolute() or not path.is_file() or path.stat().st_size == 0:
            reasons.append("missing or empty artifact: " + filename)
    return {"ok": not reasons, "reasons": reasons, "verified_sr_jobs": count}


def _recover_clarification(store, goal):
    """Replay a persisted control declaration, never a new model request."""
    from backend.tools.clarification import NAME, control_tool
    from backend.tools.contract import ToolRegistry
    sid = goal.get("session_id")
    if not sid:
        return None
    history = store.get_messages(sid)
    known = {c["request_key"]: c for c in store.list_goal_clarifications(goal["goal_id"])}
    ordinal = 0
    for index, message in enumerate(history):
        if message["role"] != "assistant":
            continue
        ordinal += 1
        if message.get("goal_id") != goal["goal_id"]:
            continue
        calls = message.get("tool_calls") or []
        if len(calls) != 1 or calls[0].get("function", {}).get("name") != NAME:
            continue
        call = calls[0]
        key = f"{sid}:{ordinal}:{call['id']}"
        if key in known:
            continue
        result = ToolRegistry([control_tool()]).invoke(NAME, call["function"].get("arguments"))
        if not result["ok"]:
            continue
        receipt = None
        for following in history[index + 1:]:
            if following["role"] != "tool":
                break
            if following.get("tool_call_id") == call["id"]:
                receipt = following
                break
        if receipt:
            try:
                if not json.loads(receipt["content"]).get("ok"):
                    continue
            except (ValueError, AttributeError):
                continue
        lease = current_lease.get()
        with store.worker_transaction():
            if receipt is None:
                store.append_message(sid, {"role": "tool", "tool_call_id": call["id"],
                    "content": _encoded_result_view(store, sid, NAME, call["id"], result, ordinal)})
            return store.request_goal_clarification(goal["goal_id"], run_id=message.get("goal_run_id"),
                request_key=key, clarification=result["data"], expected_version=lease.version, lease_token=lease.token)
    return None


def _recover(state, goal):
    """Restore receipts before repairing history; never replay unknown writes."""
    store = state.store
    clarification = _recover_clarification(store, goal)
    if clarification:
        return clarification
    intents = store.list_tool_intents(goal["goal_id"])
    for intent in intents:
        if (
            intent["status"] in {"UNKNOWN", "EXECUTING"}
            and intent["side_effect_class"] != "READ_ONLY"
            and intent["external_job_id"] is None
        ):
            return _transition(
                store,
                goal,
                "BLOCKED",
                release=True,
                error="interrupted side effect requires reconciliation",
                event_type="tool_recovery_unknown",
            )
    # A crash can happen after any tool receipt but before its transcript write.
    # Match the exact assistant declaration, including batch calls never started.
    sid = goal.get("session_id")
    messages = store.get_messages(sid) if sid else []
    ids = {m.get("tool_call_id") for m in messages if m["role"] == "tool"}
    by_key = {i["idempotency_key"]: i for i in intents}
    with store.worker_transaction():
        count = 0
        for message in messages:
            if message["role"] != "assistant":
                continue
            count += 1
            for tc in message.get("tool_calls") or []:
                if tc["id"] in ids:
                    continue
                intent = by_key.get(f"{sid}:{count}:{tc['id']}")
                result = intent.get("result") if intent else None
                if (
                    result
                    and "ok" not in result
                    and intent["external_job_id"] is not None
                ):
                    result = {
                        "ok": True,
                        "data": {
                            "job_id": intent["external_job_id"],
                            "status": "RECOVERED",
                        },
                        "error": None,
                    }
                if not result or "ok" not in result:
                    result = {
                        "ok": False,
                        "data": None,
                        "error": (
                            "interrupted read-only call"
                            if intent
                            else "not executed before interruption; safe to request again"
                        ),
                    }
                store.append_message(
                    sid,
                    {
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": _encoded_result_view(store, sid, tc["function"]["name"], tc["id"], result, count),
                    },
                )
        pending = [
            i
            for i in intents
            if i["status"] == "SUBMITTED" and i["external_job_id"] is not None
        ]
        if pending:
            intent = pending[0]
            return _transition(
                store,
                goal,
                "WAITING_EXTERNAL",
                release=True,
                next_wake_at=time.time(),
                checkpoint={
                    **goal["checkpoint"],
                    "external_job_id": intent["external_job_id"],
                },
                event_type="external_job_recovered",
            )
    return None


def _process(state, claimed, lease):
    scope = current_lease.set(lease)
    store = state.store
    try:
        goal = lease.check(store)
        if goal["status"] == "WAITING_EXTERNAL":
            return reconcile_goal(state, goal)
        if goal["status"] == "VERIFYING":
            verdict = verify_goal(state, goal)
            with store.worker_transaction():
                if verdict["ok"]:
                    store.set_session_status(goal["session_id"], "done")
                    return store.complete_verified_goal(
                        goal["goal_id"],
                        verdict,
                        expected_version=lease.version,
                        lease_token=lease.token,
                        release=True,
                    )
                if verdict.get("blocked"):
                    return _transition(
                        store,
                        goal,
                        "BLOCKED",
                        release=True,
                        error="; ".join(verdict["reasons"]),
                        event_type="goal_verification_blocked",
                        event_payload=verdict,
                    )
                store.append_message(
                    goal["session_id"],
                    {
                        "role": "system",
                        "content": "Internal Goal verification failed: "
                        + json.dumps(verdict, ensure_ascii=False),
                    },
                )
                return _transition(
                    store,
                    goal,
                    "RUNNING",
                    release=True,
                    next_wake_at=time.time(),
                    event_type="goal_verification_retry",
                    event_payload=verdict,
                )
        recovered = _recover(state, goal)
        if recovered:
            return recovered
        if (
            goal["rounds_used"] >= goal["max_rounds"]
            or goal["model_calls_used"] >= goal["max_model_calls"]
        ):
            return _transition(
                store,
                goal,
                "FAILED",
                release=True,
                error="Goal total budget exhausted",
                event_type="goal_budget_exhausted",
            )
        result = run_round(state, goal)
        outcome = result["outcome"]
        target = {
            Outcome.CONTINUE: "RUNNING",
            Outcome.BUDGET_YIELD: "RUNNING",
            Outcome.WAITING_EXTERNAL: "WAITING_EXTERNAL",
            Outcome.NEEDS_CLARIFICATION: "WAITING_USER",
            Outcome.COMPLETED_CANDIDATE: "VERIFYING",
            Outcome.NEEDS_APPROVAL: "PAUSED",
            Outcome.RETRYABLE_ERROR: "RETRY_WAIT",
            Outcome.FAILED: "FAILED",
            Outcome.BLOCKED: "BLOCKED",
        }[outcome]
        latest_cp = store.get_goal(goal["goal_id"])["checkpoint"]
        totals = latest_cp.get("usage", {})
        cp = {
            **latest_cp,
            "last_round": {
                k: v for k, v in result.items() if k not in {"messages", "answer"}
            },
            "usage": {
                "tool_calls": totals.get("tool_calls", 0) + result["tool_calls"],
                "elapsed_seconds": totals.get("elapsed_seconds", 0)
                + result["elapsed_seconds"],
                "total_tokens": totals.get("total_tokens", 0)
                + result["usage"]["total_tokens"],
            },
        }
        if result.get("external_job_id") is not None:
            cp["external_job_id"] = result["external_job_id"]
        if outcome == Outcome.NEEDS_CLARIFICATION:
            return store.request_goal_clarification(goal["goal_id"], run_id=result.get("run_id"),
                request_key=result["request_key"], clarification=result["clarification"], checkpoint=cp,
                expected_version=lease.version, lease_token=lease.token)
        return _transition(
            store,
            goal,
            target,
            checkpoint=cp,
            error=result.get("error"),
            release=True,
            next_wake_at=time.time() + (1 if target == "RETRY_WAIT" else 0),
            phase=outcome.value,
            event_type="round_finished",
            event_payload={
                "outcome": outcome,
                "model_calls": result["model_calls"],
                "tool_calls": result["tool_calls"],
            },
        )
    except LeaseLost:
        log.info("Goal %s lost its lease; discarded worker output", lease.goal_id)
    except Exception as exc:
        log.exception("Goal %s failed during worker pass", lease.goal_id)
        try:
            _transition(
                store,
                claimed,
                "BLOCKED",
                release=True,
                error=f"worker exception: {type(exc).__name__}: {exc}",
                event_type="worker_error",
            )
        except LeaseLost:
            pass
    finally:
        current_lease.reset(scope)
        store.close_thread()


async def goal_tick(state):
    """One serial pass; model work lives in a dedicated bounded executor."""
    if not hasattr(state, "goal_tick_lock"):
        state.goal_tick_lock = asyncio.Lock()
    async with state.goal_tick_lock:
        executor = getattr(state, "goal_executor", None)
        owned = executor is None
        if owned:
            executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="goal")
        try:
            for goal in state.store.list_goals(limit=100, statuses=SCHEDULED):
                if not hasattr(state, "chat_locks"):
                    state.chat_locks = {}
                session_lock = (
                    state.chat_locks.setdefault(goal["session_id"], asyncio.Lock())
                    if goal.get("session_id")
                    else None
                )
                if session_lock is not None and session_lock.locked():
                    continue
                seconds = max(0.1, float(os.getenv("SR_GOAL_LEASE_SEC", "60")))
                claimed = state.store.claim_goal(
                    goal["goal_id"], "worker:" + uuid.uuid4().hex, lease_sec=seconds
                )
                if not claimed:
                    continue
                lease = GoalLease(
                    claimed["goal_id"], claimed["lease_token"], claimed["version"]
                )
                if not claimed.get("session_id"):
                    scope = current_lease.set(lease)
                    try:
                        with state.store.worker_transaction():
                            sid = state.store.create_session(
                                {"goal_id": claimed["goal_id"]}
                            )
                            state.store.set_goal_session(claimed["goal_id"], sid)
                        claimed["session_id"] = sid
                    finally:
                        current_lease.reset(scope)
                    session_lock = state.chat_locks.setdefault(sid, asyncio.Lock())
                await session_lock.acquire()
                work = asyncio.get_running_loop().run_in_executor(
                    executor, _process, state, claimed, lease
                )
                try:
                    while not work.done():
                        done, _ = await asyncio.wait({work}, timeout=seconds / 3)
                        if done:
                            break
                        with lease.lock:
                            if not state.store.renew_goal(
                                lease.goal_id,
                                lease.token,
                                lease.version,
                                lease_sec=seconds,
                            ):
                                lease.stopped.set()
                    await work
                finally:
                    lease.stopped.set()
                    state.store.release_goal(lease.goal_id, lease.token, lease.version)
                    session_lock.release()
        finally:
            if owned:
                executor.shutdown(wait=False, cancel_futures=True)


async def goal_loop(state):
    state.goal_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="goal")
    try:
        while True:
            try:
                await goal_tick(state)
            except Exception:
                log.exception("Goal scheduler pass failed; retrying next pass")
            await asyncio.sleep(max(0.05, float(os.getenv("SR_GOAL_POLL_SEC", "5"))))
    finally:
        state.goal_executor.shutdown(wait=False, cancel_futures=True)
