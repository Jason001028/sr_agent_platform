"""Small durable Goal runtime.

The runtime deliberately owns only durable scheduling and reconciliation.  The
existing chat loop and SR executor remain the implementations of model/tool
work; this layer keeps their lifecycle alive after an HTTP request ends.
"""
from __future__ import annotations

import asyncio, os, time, uuid
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from backend.services import run_sr
from backend.agent import loop as agent_loop

router = APIRouter(prefix="/api/goals", tags=["goals"])
TERMINAL = {"COMPLETED", "FAILED", "CANCELLED", "BLOCKED"}
ALLOWED = {
    "QUEUED": {"RUNNING", "CANCELLED", "PAUSED"},
    "RUNNING": {"WAITING_EXTERNAL", "VERIFYING", "RETRY_WAIT", "COMPLETED", "FAILED", "BLOCKED", "PAUSED", "CANCELLED"},
    "WAITING_EXTERNAL": {"VERIFYING", "FAILED", "BLOCKED", "PAUSED", "CANCELLED"},
    "VERIFYING": {"COMPLETED", "FAILED", "BLOCKED", "RETRY_WAIT"},
    "RETRY_WAIT": {"RUNNING", "BLOCKED", "CANCELLED", "PAUSED"},
    "PAUSED": {"QUEUED", "CANCELLED"},
}

def run_round(state, goal):
    cp = goal.get("checkpoint") or {}
    sid = goal.get("session_id") or state.store.create_session({"goal_id": goal["goal_id"]})
    if not goal.get("session_id"): state.store.set_goal_session(goal["goal_id"], sid)
    result = agent_loop.run_loop(cfg=state.cfg, prompt=None if goal.get("session_id") else goal["title"],
        max_turns=min(8, max(1, goal.get("max_rounds", 20)-goal.get("rounds_used", 0))),
        store=state.store, session_id=sid, resume=bool(goal.get("session_id")),
        registry=state.tool_registry, context=state.context)
    answer = result.get("answer") or ""
    if result.get("error"): return "FAILED", {"error": result["error"], "session_id": sid}
    if "job_id" in answer.lower() or cp.get("external_job_id"):
        return "WAITING_EXTERNAL", {"session_id":sid, "answer":answer, **cp}
    return "COMPLETED_CANDIDATE", {"session_id":sid, "answer":answer, **cp}

def _body(request: Request):
    return request.state  # marker for type checkers

def _job_task(store, job_id):
    for task in store.list_sr_tasks(limit=10000):
        if task.get("job_id") == job_id: return task
    return None

def reconcile_goal(state, goal):
    """Advance a waiting goal using the same SR status source as queue API."""
    cp = goal.get("checkpoint") or {}; job_id = cp.get("external_job_id")
    if goal["status"] != "WAITING_EXTERNAL" or job_id is None: return goal
    task = _job_task(state.store, int(job_id))
    if not task:
        return state.store.transition_goal(goal["goal_id"], "BLOCKED", phase="reconcile", error="external job record not found", event_type="external_job_unknown")
    try: st = run_sr.query_job_status(job_id, task=task)
    except Exception as exc:
        return state.store.transition_goal(goal["goal_id"], "BLOCKED", phase="reconcile", error=f"status query failed: {exc}", event_type="external_job_unknown")
    if st.get("active"):
        state.store.transition_goal(goal["goal_id"], "WAITING_EXTERNAL", phase="waiting_external", next_wake_at=time.time()+float(os.getenv("SR_GOAL_POLL_SEC","5")), event_type="external_job_waiting", event_payload={"job_id":job_id,"state":st.get("state")})
        return state.store.get_goal(goal["goal_id"])
    if st.get("state") == "COMPLETED":
        # A verifier can later add product-specific checks; a scheduler terminal
        # state alone is intentionally represented as VERIFYING first.
        return state.store.transition_goal(goal["goal_id"], "VERIFYING", phase="verifying", checkpoint={**cp,"external_state":st}, event_type="external_job_terminal", event_payload={"job_id":job_id,"state":"COMPLETED"})
    return state.store.transition_goal(goal["goal_id"], "FAILED", phase="failed", error=f"external job state={st.get('state')}", event_type="external_job_failed", event_payload={"job_id":job_id,"state":st.get("state")})

async def goal_tick(state):
    """One bounded scheduler pass; safe to call from app lifespan or tests."""
    now=time.time()
    for goal in state.store.list_goals(limit=100):
        if goal["status"] in TERMINAL or (goal.get("next_wake_at") and goal["next_wake_at"] > now): continue
        if goal["status"] == "WAITING_EXTERNAL": reconcile_goal(state, goal)
        elif goal["status"] == "QUEUED": state.store.transition_goal(goal["goal_id"], "RUNNING", phase="running", event_type="goal_started")
        elif goal["status"] == "RUNNING":
            outcome, cp = run_round(state, goal)
            target = "VERIFYING" if outcome == "COMPLETED_CANDIDATE" else outcome
            state.store.transition_goal(goal["goal_id"], target, phase=outcome.lower(), checkpoint=cp, event_type="round_finished", event_payload={"outcome":outcome})
        elif goal["status"] == "VERIFYING":
            # Deterministic verification hook: callers may set checkpoint.verified.
            cp=goal.get("checkpoint") or {}
            if cp.get("verified") is True and cp.get("verified_by") == "sr_result_verifier": state.store.transition_goal(goal["goal_id"], "COMPLETED", phase="completed", event_type="goal_verified")
            elif cp.get("verification_error"): state.store.transition_goal(goal["goal_id"], "FAILED", phase="verification_failed", error=str(cp["verification_error"]), event_type="goal_verification_failed")

async def goal_loop(state):
    while True:
        await goal_tick(state)
        await asyncio.sleep(float(os.getenv("SR_GOAL_POLL_SEC", "5")))

@router.post("")
async def create_goal(request: Request):
    body=await request.json()
    title=str(body.get("title") or body.get("prompt") or "")
    if not title: raise HTTPException(400,"title/prompt is required")
    key=request.headers.get("Idempotency-Key") or body.get("request_key")
    return request.app.state.store.create_goal(title=title, session_id=body.get("session_id"), request_key=key, checkpoint=body.get("checkpoint"), max_rounds=body.get("max_rounds",20), max_model_calls=body.get("max_model_calls",100))

@router.get("")
def list_goals(request: Request, limit: int=100): return request.app.state.store.list_goals(limit=max(1,min(limit,500)))

@router.get("/{goal_id}")
def get_goal(goal_id: str, request: Request):
    goal=request.app.state.store.get_goal(goal_id)
    if not goal: raise HTTPException(404,"goal not found")
    return goal

@router.get("/{goal_id}/events")
def events(goal_id: str, request: Request, after: int=0, limit: int=500):
    if not request.app.state.store.get_goal(goal_id): raise HTTPException(404,"goal not found")
    return {"goal_id":goal_id,"events":request.app.state.store.list_goal_events(goal_id,after=after,limit=min(limit,1000))}

@router.get("/{goal_id}/events/stream")
async def event_stream(goal_id: str, request: Request, after: int=0):
    if not request.app.state.store.get_goal(goal_id): raise HTTPException(404,"goal not found")
    async def gen():
        cursor=int(after)
        while True:
            rows=request.app.state.store.list_goal_events(goal_id,after=cursor,limit=100)
            if rows:
                for row in rows:
                    cursor=row["seq"]
                    import json
                    yield "data: "+json.dumps(row,ensure_ascii=False)+"\n\n"
            goal=request.app.state.store.get_goal(goal_id)
            if goal and goal["status"] in TERMINAL and not rows: break
            if await request.is_disconnected(): break
            await asyncio.sleep(1)
    return StreamingResponse(gen(),media_type="text/event-stream",headers={"Cache-Control":"no-cache","X-Accel-Buffering":"no"})

@router.get("/{goal_id}/steps")
def steps(goal_id: str, request: Request):
    if not request.app.state.store.get_goal(goal_id): raise HTTPException(404,"goal not found")
    return request.app.state.store.list_goal_steps(goal_id)

@router.post("/{goal_id}/tool-intents")
async def tool_intent(goal_id: str, request: Request):
    if not request.app.state.store.get_goal(goal_id): raise HTTPException(404,"goal not found")
    body=await request.json(); name=str(body.get("tool_name") or "")
    if not name or "args" not in body: raise HTTPException(400,"tool_name and args are required")
    key=str(body.get("idempotency_key") or "")
    if not key: raise HTTPException(400,"idempotency_key is required")
    return request.app.state.store.create_tool_intent(goal_id=goal_id,step_id=body.get("step_id"),tool_name=name,args=body["args"],idempotency_key=key,side_effect_class=body.get("side_effect_class","READ_ONLY"),replay_policy=body.get("replay_policy","REPLAY_READ"))

@router.post("/{goal_id}/external-job")
async def external_job(goal_id: str, request: Request):
    """Associate an already submitted SR job after its id is durably known."""
    body=await request.json(); job_id=body.get("job_id")
    if job_id is None: raise HTTPException(400,"job_id is required")
    old=request.app.state.store.get_goal(goal_id)
    if not old: raise HTTPException(404,"goal not found")
    cp={**(old.get("checkpoint") or {}),"external_job_id":int(job_id)}
    return request.app.state.store.transition_goal(goal_id,"WAITING_EXTERNAL",phase="waiting_external",checkpoint=cp,next_wake_at=time.time(),event_type="external_job_attached",event_payload={"job_id":int(job_id)})

def _transition(goal_id, request, target):
    store=request.app.state.store; old=store.get_goal(goal_id)
    if not old: raise HTTPException(404,"goal not found")
    if target not in ALLOWED.get(old["status"], set()): raise HTTPException(409,f"invalid transition {old['status']} -> {target}")
    return store.transition_goal(goal_id,target,phase=target.lower(),event_type=f"goal_{target.lower()}")

@router.post("/{goal_id}/pause")
def pause(goal_id: str, request: Request): return _transition(goal_id,request,"PAUSED")
@router.post("/{goal_id}/resume")
def resume(goal_id: str, request: Request): return _transition(goal_id,request,"QUEUED")
@router.post("/{goal_id}/cancel")
def cancel(goal_id: str, request: Request):
    old=request.app.state.store.get_goal(goal_id)
    if not old: raise HTTPException(404,"goal not found")
    if old["status"] in TERMINAL: return old
    return request.app.state.store.transition_goal(goal_id,"CANCELLED",phase="cancelled",event_type="goal_cancelled")

