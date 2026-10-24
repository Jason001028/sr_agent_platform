"""Durable Goal REST/SSE routes; execution lives in goal_worker."""
from __future__ import annotations

import asyncio
import time
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from backend.services.goal_worker import validate_acceptance

router = APIRouter(prefix="/api/goals", tags=["goals"])
TERMINAL = {"COMPLETED", "FAILED", "CANCELLED", "BLOCKED"}
ALLOWED = {
    "QUEUED": {"RUNNING", "CANCELLED", "PAUSED"},
    "RUNNING": {"WAITING_EXTERNAL", "VERIFYING", "RETRY_WAIT", "COMPLETED", "FAILED", "BLOCKED", "PAUSED", "CANCELLED"},
    "WAITING_EXTERNAL": {"RUNNING", "VERIFYING", "FAILED", "BLOCKED", "PAUSED", "CANCELLED"},
    "VERIFYING": {"RUNNING", "PAUSED", "CANCELLED", "COMPLETED", "FAILED", "BLOCKED", "RETRY_WAIT"},
    "RETRY_WAIT": {"RUNNING", "BLOCKED", "CANCELLED", "PAUSED"},
    "PAUSED": {"QUEUED", "CANCELLED"},
}

@router.post("")
async def create_goal(request: Request):
    body=await request.json()
    if not isinstance(body, dict): raise HTTPException(400,"body must be an object")
    checkpoint=body.get("checkpoint") or {}
    if not isinstance(checkpoint, dict): raise HTTPException(400,"checkpoint must be an object")
    if "acceptance" in checkpoint:
        try: validate_acceptance(checkpoint["acceptance"])
        except ValueError as exc: raise HTTPException(400,str(exc)) from exc
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
    if old["status"] in TERMINAL: raise HTTPException(409,"goal is terminal")
    if not request.app.state.store.get_sr_task_by_job(int(job_id)):
        raise HTTPException(400,"job must have a durable sr_tasks record")
    cp={**(old.get("checkpoint") or {}),"external_job_id":int(job_id)}
    result=request.app.state.store.transition_goal(goal_id,"WAITING_EXTERNAL",phase="waiting_external",checkpoint=cp,next_wake_at=time.time(),event_type="external_job_attached",event_payload={"job_id":int(job_id)},expected_version=old["version"],release=True)
    if result is None: raise HTTPException(409,"goal changed; reload before attaching job")
    return result

def _transition(goal_id, request, target):
    store=request.app.state.store; old=store.get_goal(goal_id)
    if not old: raise HTTPException(404,"goal not found")
    if target not in ALLOWED.get(old["status"], set()): raise HTTPException(409,f"invalid transition {old['status']} -> {target}")
    result=store.transition_goal(goal_id,target,phase=target.lower(),event_type=f"goal_{target.lower()}",expected_version=old["version"])
    if result is None: raise HTTPException(409,"goal changed; reload before transition")
    return result

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

