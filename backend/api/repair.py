"""REST entry for the unified Repair Application Service.

Both the smart-repair tool panel and the Agent end up here; the endpoints only
translate JSON to the service and map its conflict reasons to HTTP.  They never
touch TIFFs, masks, SR executors or Batch workers directly.

Trust boundary
--------------
``session_id`` is not a capability token: it must name a session that already
exists server-side, and every plan/Goal read is filtered by it.  The caller
cannot supply ``confirmed_by``; the server records ``user:<session>``.
"""
from __future__ import annotations

import re

from fastapi import APIRouter, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from backend.services import repair_application as svc
from backend.services.store import RepairPlanConflict

router = APIRouter(prefix="/api/repairs", tags=["repairs"])

_PLAN_ID_RE = re.compile(r"^plan_[0-9a-f]{24}$")
_MAX_SOURCES = 500
_MAX_TEXT = 4096
_MODES = {"demo", "production"}
_STRATEGIES = {"automatic", "review"}
_DETECTIONS = {"absdiff", "ssim", "smart"}
_MASK_POLICIES = {"auto", "existing"}
_SCOPES = {"current", "selection", "workspace", "draft"}


def _store(request: Request):
    return request.app.state.store


async def _body(request: Request) -> dict:
    try:
        body = await request.json()
    except (ValueError, TypeError) as exc:
        raise HTTPException(400, "body must be valid JSON") from exc
    if not isinstance(body, dict):
        raise HTTPException(400, "body must be an object")
    return body


def _plan_id(value: str) -> str:
    if not isinstance(value, str) or not _PLAN_ID_RE.fullmatch(value):
        raise HTTPException(400, "invalid plan_id")
    return value


def _session_id(store, value, *, required: bool) -> str | None:
    """A session id must be an existing server-side session, not a free claim."""
    if value is None:
        if required:
            raise HTTPException(400, "session_id is required")
        return None
    if not isinstance(value, str) or not value.strip() or len(value) > 128:
        raise HTTPException(400, "session_id must be a non-empty string")
    value = value.strip()
    if store.get_session(value) is None:
        raise HTTPException(403, "unknown session")
    return value


def _enum(value, allowed, label, default=None):
    if value is None:
        return default
    if not isinstance(value, str) or value.strip().lower() not in allowed:
        raise HTTPException(400, f"{label} is invalid")
    return value.strip().lower()


def _optional_int(value, label, *, minimum=0):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise HTTPException(400, f"{label} must be an integer >= {minimum}")
    return value


def _sources(value):
    if value is None:
        return None
    if not isinstance(value, list) or not value or len(value) > _MAX_SOURCES:
        raise HTTPException(400, "sources must be a non-empty array (max 500)")
    out = []
    for entry in value:
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
            raise HTTPException(400, "each source must contain a path")
        path = entry["path"].strip()
        if not path or len(path) > _MAX_TEXT:
            raise HTTPException(400, "source path is empty or too long")
        name = entry.get("name")
        evidence = entry.get("evidence")
        if name is not None and not isinstance(name, str):
            raise HTTPException(400, "source name must be a string")
        if evidence is not None and not isinstance(evidence, str):
            raise HTTPException(400, "source evidence must be a string")
        out.append({"path": path,
                    "name": (name or "")[:_MAX_TEXT],
                    "evidence": (evidence or "")[:_MAX_TEXT]})
    return out


def _validate_exclusions(value):
    if value is None:
        return None
    if not isinstance(value, list) or len(value) > _MAX_SOURCES:
        raise HTTPException(400, "exclusions must be an array (max 500)")
    return value


def _owned_plan(store, plan_id: str, session_id: str | None) -> dict:
    """Read a plan only inside the caller's existing session."""
    plan = store.get_repair_plan(plan_id)
    if plan is None:
        raise HTTPException(404, "repair plan not found")
    if plan.get("session_id") is not None and plan["session_id"] != session_id:
        # Do not disclose whether another session's plan exists.
        raise HTTPException(404, "repair plan not found")
    return plan


def _map_error(exc: Exception):
    if isinstance(exc, KeyError):
        raise HTTPException(404, "repair plan not found") from exc
    if isinstance(exc, RepairPlanConflict):
        raise HTTPException(409, {"message": str(exc),
                                  "reason": exc.reason}) from exc
    if isinstance(exc, svc.RepairBlocked):
        raise HTTPException(409, {"message": str(exc),
                                  "reason": exc.reason}) from exc
    if isinstance(exc, svc.RepairError):
        raise HTTPException(400, str(exc)) from exc
    if isinstance(exc, (ValueError, OSError)):
        raise HTTPException(409, str(exc)) from exc
    raise exc


@router.post("/prepare")
async def prepare(request: Request):
    body = await _body(request)
    store = _store(request)
    session_id = _session_id(store, body.get("session_id"), required=True)
    scope = _enum(body.get("scope"), _SCOPES, "scope", "workspace")
    mode = _enum(body.get("mode"), _MODES, "mode", "demo")
    strategy = _enum(body.get("strategy"), _STRATEGIES, "strategy", "automatic")
    detection = _enum(body.get("detection_method"), _DETECTIONS,
                      "detection_method", "absdiff")
    mask_policy = _enum(body.get("mask_policy"), _MASK_POLICIES,
                        "mask_policy", "auto")
    title = body.get("title")
    if title is not None and (not isinstance(title, str) or len(title) > 200):
        raise HTTPException(400, "title must be a string (max 200)")
    manifest_ref = body.get("manifest_ref")
    if manifest_ref is not None and not isinstance(manifest_ref, str):
        raise HTTPException(400, "manifest_ref must be a string")
    for key in ("workspace", "current"):
        value = body.get(key)
        if value is not None and not isinstance(value, dict):
            raise HTTPException(400, f"{key} must be an object")
    selected_images = body.get("selected_images")
    if selected_images is not None and not isinstance(selected_images, list):
        raise HTTPException(400, "selected_images must be an array")
    mask_path = body.get("mask_path")
    if mask_path is not None and not isinstance(mask_path, str):
        raise HTTPException(400, "mask_path must be a string")
    try:
        plan = await run_in_threadpool(
            svc.prepare_repair, store,
            session_id=session_id,
            workspace=body.get("workspace"),
            current=body.get("current"),
            selected_images=selected_images,
            scope=scope, sources=_sources(body.get("sources")),
            manifest_ref=manifest_ref,
            exclusions=_validate_exclusions(body.get("exclusions")),
            mode=mode, strategy=strategy, detection_method=detection,
            smart_options=body.get("smart_options"),
            mask_policy=mask_policy, mask_path=mask_path,
            title=title, created_by="tool_panel")
    except Exception as exc:  # noqa: BLE001 — service is the contract boundary
        _map_error(exc)
    return plan


@router.post("/{plan_id}/confirm")
async def confirm(plan_id: str, request: Request):
    plan_id = _plan_id(plan_id)
    body = await _body(request)
    store = _store(request)
    session_id = _session_id(store, body.get("session_id"), required=True)
    try:
        _owned_plan(store, plan_id, session_id)
        # confirmed_by is deliberately ignored: it is not a user credential.
        return await run_in_threadpool(
            svc.confirm_repair, store, plan_id, session_id=session_id)
    except Exception as exc:  # noqa: BLE001
        _map_error(exc)


@router.post("/{plan_id}/start")
async def start(plan_id: str, request: Request):
    plan_id = _plan_id(plan_id)
    body = await _body(request)
    store = _store(request)
    session_id = _session_id(store, body.get("session_id"), required=True)
    workspace_version = _optional_int(body.get("workspace_version"),
                                     "workspace_version")
    plan_version = _optional_int(body.get("plan_version"), "plan_version",
                                 minimum=1)
    workspace = body.get("workspace")
    if workspace is not None and not isinstance(workspace, dict):
        raise HTTPException(400, "workspace must be an object")
    try:
        _owned_plan(store, plan_id, session_id)
        return await run_in_threadpool(
            svc.start_repair, store, plan_id, session_id=session_id,
            workspace=workspace, workspace_version=workspace_version,
            plan_version=plan_version)
    except Exception as exc:  # noqa: BLE001
        _map_error(exc)


@router.get("")
def list_plans(request: Request, session_id: str | None = None,
               limit: int = 20):
    store = _store(request)
    session_id = _session_id(store, session_id, required=True)
    rows = store.list_repair_plans(
        session_id=session_id, limit=max(1, min(limit, 100)))
    return {"plans": [svc.public_plan(row) for row in rows]}


@router.get("/{plan_id}")
def status(plan_id: str, request: Request, session_id: str | None = None):
    plan_id = _plan_id(plan_id)
    store = _store(request)
    session_id = _session_id(store, session_id, required=True)
    _owned_plan(store, plan_id, session_id)
    return svc.get_repair_status(store, plan_id)


@router.get("/{plan_id}/result")
def result(plan_id: str, request: Request, session_id: str | None = None):
    plan_id = _plan_id(plan_id)
    store = _store(request)
    session_id = _session_id(store, session_id, required=True)
    _owned_plan(store, plan_id, session_id)
    return svc.get_repair_result(store, plan_id)
