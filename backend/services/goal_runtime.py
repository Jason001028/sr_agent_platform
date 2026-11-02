"""Durable Goal REST/SSE routes; execution lives in goal_worker."""
from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import json
from pathlib import Path
import time
from urllib.parse import quote
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse

from backend.services import batch_manifest
from backend.services.goal_worker import validate_acceptance
from backend.services.store import BatchConflict, ClarificationConflict

router = APIRouter(prefix="/api/goals", tags=["goals"])
TERMINAL = {"COMPLETED", "FAILED", "CANCELLED", "BLOCKED"}
ALLOWED = {
    "QUEUED": {"RUNNING", "CANCELLED", "PAUSED"},
    "RUNNING": {"WAITING_EXTERNAL", "WAITING_USER", "VERIFYING", "RETRY_WAIT", "COMPLETED", "FAILED", "BLOCKED", "PAUSED", "CANCELLED"},
    "WAITING_USER": {"CANCELLED"},
    "WAITING_EXTERNAL": {"RUNNING", "VERIFYING", "FAILED", "BLOCKED", "PAUSED", "CANCELLED"},
    "VERIFYING": {"RUNNING", "PAUSED", "CANCELLED", "COMPLETED", "FAILED", "BLOCKED", "RETRY_WAIT"},
    "RETRY_WAIT": {"RUNNING", "BLOCKED", "CANCELLED", "PAUSED"},
    "PAUSED": {"QUEUED", "CANCELLED"},
}

def _batch_item_checkpoint(item: dict) -> dict:
    return {
        "phase": item["status"],
        "attempt": 0,
        "source_lines": item.get("source_lines") or [],
        "name": item.get("name"),
        "scene_ref": item.get("scene_ref"),
        "target_image": item.get("target_image"),
        "reference_image": item.get("reference_image"),
        "config": item.get("config") or {},
        "exclusion_reason": item.get("exclusion_reason") or "",
    }


def _create_batch_goal(request: Request, body: dict):
    key = request.headers.get("Idempotency-Key") or body.get("request_key")
    if not key:
        raise HTTPException(400, "Idempotency-Key is required for a batch Goal")
    ref = str(body.get("manifest_ref") or "")
    try:
        manifest = batch_manifest.load_manifest(ref)
        exclusions = batch_manifest.normalize_exclusions(body.get("exclusions"))
        planned = batch_manifest.plan_goal_items(manifest, exclusions)
    except batch_manifest.ManifestConflict as exc:
        raise HTTPException(409, {
            "message": str(exc), "diagnostics": exc.diagnostics}) from exc
    except batch_manifest.ManifestError as exc:
        raise HTTPException(400, str(exc)) from exc
    # The execution mode is part of the frozen import. Accepting a different
    # mode here would let a demo manifest be relabelled as production (or the
    # reverse) without re-freezing.
    mode = str(manifest.get("mode") or "demo")
    requested_mode = str(body.get("mode") or mode).strip().lower()
    if requested_mode != mode:
        raise HTTPException(400, "mode does not match the frozen manifest")
    title = str(body.get("title") or "").strip()
    if not title:
        title = "批量影像修复 · %d 景" % len(planned)
    if len(title) > 200:
        raise HTTPException(400, "title is too long (max 200 characters)")
    content_sha256 = batch_manifest.canonical_hash({
        "manifest_ref": ref,
        "manifest_sha256": manifest.get("manifest_sha256"),
        "exclusions": exclusions,
        "mode": mode,
        "title": title,
    })
    checkpoint = {
        "workflow": batch_manifest.WORKFLOW,
        "manifest_ref": ref,
        "manifest_sha256": manifest.get("manifest_sha256"),
        "mode": mode,
        "exclusions": exclusions,
        "batch_control": {"pause_requested": False, "cancel_requested": False},
        "producer_status": "DISPATCHING",
        "report_ref": None,
    }
    items = [{
        "item_id": item["item_id"],
        "ordinal": item["ordinal"],
        "name": item["name"],
        "status": item["status"],
        "checkpoint": _batch_item_checkpoint(item),
        "resource_key": (item.get("target_image") or {}).get("image_key"),
        "next_wake_at": time.time() if item["status"] == "PENDING" else None,
    } for item in planned]
    try:
        return request.app.state.store.create_batch_goal(
            title=title, session_id=body.get("session_id"), request_key=key,
            content_sha256=content_sha256, checkpoint=checkpoint, items=items,
            max_model_calls=0)
    except BatchConflict as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("")
async def create_goal(request: Request):
    body=await request.json()
    if not isinstance(body, dict): raise HTTPException(400,"body must be an object")
    if body.get("workflow") == batch_manifest.WORKFLOW or body.get("manifest_ref"):
        return _create_batch_goal(request, body)
    checkpoint=body.get("checkpoint") or {}
    if not isinstance(checkpoint, dict): raise HTTPException(400,"checkpoint must be an object")
    if "acceptance" in checkpoint:
        try: validate_acceptance(checkpoint["acceptance"])
        except ValueError as exc: raise HTTPException(400,str(exc)) from exc
    title=str(body.get("title") or body.get("prompt") or "")
    if not title: raise HTTPException(400,"title/prompt is required")
    key=request.headers.get("Idempotency-Key") or body.get("request_key")
    return request.app.state.store.create_goal(title=title, session_id=body.get("session_id"), request_key=key, checkpoint=body.get("checkpoint"), max_rounds=body.get("max_rounds",20), max_model_calls=body.get("max_model_calls",100))


@router.post("/batch-imports")
async def batch_import(request: Request):
    """Freeze a QC TXT import. This endpoint never creates or runs a Goal."""
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(400, "body must be an object")
    text = body.get("text")
    encoded = body.get("content_base64")
    if text is not None and encoded is not None:
        raise HTTPException(400, "provide text or content_base64, not both")
    try:
        if encoded is not None:
            if not isinstance(encoded, str) or len(encoded) > 3 * 1024 * 1024:
                raise HTTPException(400, "content_base64 is missing or too large")
            try:
                data = base64.b64decode(encoded, validate=True)
            except (binascii.Error, ValueError) as exc:
                raise HTTPException(400, "content_base64 is not valid base64") from exc
            result = batch_manifest.import_manifest(
                data=data, encoding=str(body.get("encoding") or "auto"),
                date=body.get("date"), product=body.get("product"),
                mode=str(body.get("mode") or "demo"))
        elif text is not None:
            if not isinstance(text, str):
                raise HTTPException(400, "text must be a string")
            result = batch_manifest.import_manifest(
                text=text, date=body.get("date"), product=body.get("product"),
                mode=str(body.get("mode") or "demo"))
        else:
            raise HTTPException(400, "text or content_base64 is required")
    except batch_manifest.ManifestError as exc:
        raise HTTPException(400, str(exc)) from exc
    return result

@router.get("")
def list_goals(request: Request, limit: int=100, session_id: str | None=None, include_clarifications: bool=False):
    store = request.app.state.store
    goals = store.list_goals(limit=max(1,min(limit,500)), session_id=session_id)
    if include_clarifications:
        return [{**g, "clarifications": store.list_goal_clarifications(g["goal_id"])} for g in goals]
    return goals


@router.get("/{goal_id}/clarifications")
def clarifications(goal_id: str, request: Request):
    store = request.app.state.store
    if not store.get_goal(goal_id):
        raise HTTPException(404, "goal not found")
    return store.list_goal_clarifications(goal_id)


@router.post("/{goal_id}/clarifications/{clarification_id}/respond")
async def respond(goal_id: str, clarification_id: str, request: Request):
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(400, "body must be an object")
    try:
        return request.app.state.store.respond_goal_clarification(goal_id, clarification_id,
            idempotency_key=request.headers.get("Idempotency-Key") or body.get("idempotency_key"),
            selected_option_id=body.get("selected_option_id"), answer_text=body.get("answer_text"))
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ClarificationConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

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


@router.get("/{goal_id}/items")
def items(goal_id: str, request: Request, after_ordinal: int = 0,
          status: str | None = None, limit: int = 200):
    """Business projection of batch Item steps; never returns artifact bytes."""
    goal = request.app.state.store.get_goal(goal_id)
    if not goal:
        raise HTTPException(404, "goal not found")
    statuses = [s for s in (status or "").split(",") if s] or None
    items = request.app.state.store.list_goal_items(
        goal_id, statuses=statuses, after_ordinal=after_ordinal, limit=limit)
    return {
        "goal_id": goal_id,
        "workflow": (goal.get("checkpoint") or {}).get("workflow"),
        "items": items,
        "next_after_ordinal": items[-1]["ordinal"] if items else after_ordinal,
    }

def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _batch_overlay_refs(goal_id: str, item_id: str, request: Request):
    """Resolve one published batch Overlay through the artifact-root guard.

    The endpoint never accepts a path from the client.  The path comes from the
    Item checkpoint, must resolve inside the batch artifact root, and its
    content digest must match the manifest written next to it.  Only the small
    overlay PNG is served; the full-size Mask is not exposed to the Viewer.
    """
    store = request.app.state.store
    goal = store.get_goal(goal_id)
    if not goal:
        raise HTTPException(404, "goal not found")
    item = store.get_goal_item(goal_id, item_id)
    if not item:
        raise HTTPException(404, "batch item not found")
    cp = item.get("checkpoint") or {}
    mask = cp.get("mask") or {}
    overlay_ref = mask.get("overlay") or {}
    overlay_value = overlay_ref.get("path")
    if not overlay_value:
        raise HTTPException(404, "item has no published Overlay")
    root = batch_manifest.artifact_root().resolve()
    overlay_path = Path(str(overlay_value)).resolve()
    if root not in overlay_path.parents:
        raise HTTPException(403, "Overlay path is outside the batch artifact root")
    if overlay_path.suffix.lower() != ".png" or not overlay_path.is_file():
        raise HTTPException(404, "Overlay file is missing")
    doc_path = overlay_path.parent / "overlay.json"
    try:
        doc = json.loads(doc_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise HTTPException(409, "Overlay manifest is unreadable") from exc
    if not isinstance(doc, dict):
        raise HTTPException(409, "Overlay manifest is malformed")
    if doc.get("contract_version") != "overlay.v1":
        raise HTTPException(409, "Overlay manifest version is unsupported")
    if doc.get("item_id") != item_id:
        raise HTTPException(409, "Overlay does not belong to this Item")
    doc_overlay = doc.get("overlay") or {}
    if Path(str(doc_overlay.get("file") or "")).name != overlay_path.name:
        raise HTTPException(409, "Overlay manifest names a different file")
    actual_sha = _sha256_file(overlay_path)
    if actual_sha != overlay_ref.get("sha256"):
        raise HTTPException(409, "Overlay digest does not match the checkpoint")
    if actual_sha != doc_overlay.get("sha256"):
        raise HTTPException(409, "Overlay digest does not match its manifest")
    doc_mask = doc.get("mask") or {}
    mask_name = Path(str(doc_mask.get("file") or "")).name
    mask_path = overlay_path.parent / mask_name
    if not mask_name or not mask_path.is_file():
        raise HTTPException(409, "Mask referenced by the Overlay is missing")
    mask_sha = _sha256_file(mask_path)
    if mask_sha != doc_mask.get("sha256") or mask_sha != mask.get("sha256"):
        raise HTTPException(409, "Mask digest does not match the Overlay/checkpoint")
    source_image_key = mask.get("source_image_key")
    source_revision = mask.get("source_revision")
    if not source_image_key or not source_revision:
        raise HTTPException(409, "Overlay is not bound to a frozen source image")
    image_size = doc.get("image_size") or {}
    if (image_size.get("width") != mask.get("width")
            or image_size.get("height") != mask.get("height")):
        raise HTTPException(409, "Overlay image size disagrees with the Mask")
    return item, mask, doc, overlay_path


@router.get("/{goal_id}/items/{item_id}/overlay")
def item_overlay(goal_id: str, item_id: str, request: Request):
    """Overlay metadata bound to Goal/Item/source identity.

    The Viewer matches ``source.image_key`` and ``source.revision`` against the
    image it is displaying; a newer PAN after a successful repair must not draw
    an older Overlay over it.
    """
    item, mask, doc, overlay_path = _batch_overlay_refs(
        goal_id, item_id, request)
    overlay = doc.get("overlay") or {}
    return {
        "contract_version": doc.get("contract_version"),
        "goal_id": goal_id,
        "item_id": item_id,
        "item_version": item.get("version"),
        "item_status": item.get("status"),
        "source": {
            "image_key": mask.get("source_image_key"),
            "revision": mask.get("source_revision"),
            "width": mask.get("width"),
            "height": mask.get("height"),
        },
        "image_size": doc.get("image_size"),
        "coordinate_space": doc.get("coordinate_space"),
        "scale": doc.get("scale"),
        "provenance": doc.get("provenance"),
        "detector_name": doc.get("detector_name"),
        "detector_version": doc.get("detector_version"),
        "candidates": doc.get("candidates") or [],
        "diagnostics": doc.get("diagnostics") or [],
        "mask": {
            "sha256": mask.get("sha256"),
            "width": mask.get("width"),
            "height": mask.get("height"),
            "foreground_pixels": mask.get("foreground_pixels"),
        },
        "overlay": {
            "url": (f"/api/goals/{quote(goal_id, safe='')}/items/"
                    f"{quote(item_id, safe='')}/overlay.png"),
            "sha256": overlay.get("sha256"),
            "width": overlay.get("width"),
            "height": overlay.get("height"),
            "scale": doc.get("scale"),
            "kind": "png",
        },
    }


@router.get("/{goal_id}/items/{item_id}/overlay.png")
def item_overlay_png(goal_id: str, item_id: str, request: Request):
    item, mask, doc, overlay_path = _batch_overlay_refs(
        goal_id, item_id, request)
    sha = (doc.get("overlay") or {}).get("sha256") or ""
    return FileResponse(
        overlay_path,
        media_type="image/png",
        headers={"Cache-Control": "no-cache", "ETag": f'"{sha}"'},
    )


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
    if old["status"] == "WAITING_USER": raise HTTPException(409,"answer the pending clarification first")
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

