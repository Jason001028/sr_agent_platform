"""PR C: deterministic Batch Producer / Monitor.

One Batch Scheduler drives every Batch Goal.  Each pass is monitor-first and
admits at most one new producer step, which is what keeps N+1 progress possible
without a model, a budget or a second scheduler thread.

Execution isolation
-------------------
The scheduler thread never waits for detection or Mask IO.  Detection+Mask run
on a dedicated ``batch_io_executor``; the scheduler only polls finished futures
in later passes, re-validates the Item version and the Goal lease, and commits
the result itself.  A slow detector therefore cannot delay SR status queries.
The futures are in-process only; a ``DETECTING`` Item left by a restart is safe
to re-run because detection and Mask IO have no external side effect.

Pause / cancel
--------------
When a user pauses or cancels a Batch Goal, the Producer stops (no new
detection, no new SR submission).  The Monitor keeps running under a restricted
``claim_goal_monitor`` lease: already-accepted external jobs still converge, and
the Goal status is never rewritten.  This is deliberately in PR C rather than
PR E/F: without it, a paused batch would lose track of accepted jobs.

Submission identity
-------------------
``reserve_item_submission`` writes the same Tool Intent + ``sr_tasks`` ledger
that the generic tool boundary uses.  The intent key embeds the SR task
fingerprint, and the intent is committed *before* the external submit.  Recovery
always reconciles those records first; it never blindly resubmits.  An item
whose external outcome cannot be confirmed becomes ``BLOCKED`` and keeps its
capacity slot reserved across restarts.

Event contract (stable for PR F)
--------------------------------
All state changes and their events share one Store transaction:

``item_state_changed``       item_id, item_version, attempt, from, to
``mask_ready``               item_id, path, sha256, width, height, provenance
``sr_submission_reserved``   item_id, intent_key, fingerprint, attempt
``sr_receipt_recorded``      item_id, job_id, fingerprint, status, recovered
``external_job_terminal``    item_id, job_id, state, exit_code, provenance
``item_verified``            item_id, ok, provenance, state, reasons
``batch_phase_changed``      status, phase, counts
``batch_control_changed``    status, monitoring_done, counts
``batch_report_updated``     full report object
``goal_verified``            batch verification + report (existing event)

``provenance`` is ``mock`` in demo mode and must never be read as a production
detector/verifier result.
"""

from __future__ import annotations

import asyncio
import functools
import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import time
import uuid

from backend.config import sr_runtime
from backend.pathguard import PathDeniedError, ensure_allowed, normalize_submit_path
from backend.services import batch_detector, batch_manifest, batch_mask, preview_jpg
from backend.services import run_sr as sr_service
from backend.services.goal_lease import GoalLease, current_lease, current_tool

EVT_ITEM_STATE = "item_state_changed"
EVT_MASK_READY = "mask_ready"
EVT_SR_RESERVED = "sr_submission_reserved"
EVT_SR_RECEIPT = "sr_receipt_recorded"
EVT_JOB_TERMINAL = "external_job_terminal"
EVT_ITEM_VERIFIED = "item_verified"
EVT_BATCH_PHASE = "batch_phase_changed"
EVT_BATCH_REPORT = "batch_report_updated"
EVT_BATCH_CONTROL = "batch_control_changed"

CLAIMABLE = {"QUEUED", "RUNNING", "RETRY_WAIT", "WAITING_EXTERNAL", "VERIFYING"}
MONITORED = {"SUBMITTING", "WAITING_SR", "VERIFYING"}
PREPARING = {"OPENING", "DETECTING", "MASK_READY"}
CONVERGED = {"SUCCEEDED", "NO_ISSUE", "FAILED", "SKIPPED"}
PARKED = {"BLOCKED"}
PRE_SUBMIT = {"PENDING", "RETRY_WAIT", "OPENING", "DETECTING", "MASK_READY"}

MAX_UNKNOWN_CHECKS = 3
MAX_DETECTION_ATTEMPTS = 3


class BatchConfigError(RuntimeError):
    """A frozen batch config is malformed; do not guess an execution limit."""


class BatchGateError(RuntimeError):
    """Demo/production isolation refused this execution path."""


class ItemBlocked(RuntimeError):
    """A deterministic item-level refusal that must not be retried blindly."""


# ---------------------------------------------------------------------------
# configuration and gates
# ---------------------------------------------------------------------------

def _env_float(name, default):
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return float(default)


def batch_config(manifest):
    """Read the frozen limits, with PR C defaults for keys PR B did not write.

    Values are validated as real ints (``bool`` is rejected) so a manifest
    cannot turn ``max_inflight_sr_jobs`` into a free-form value.
    """
    raw = manifest.get("config") or {}
    if not isinstance(raw, dict):
        raise BatchConfigError("manifest config 必须是对象")

    def integer(name, default, low, high):
        value = raw.get(name, default)
        if type(value) is not int or not low <= value <= high:
            raise BatchConfigError(
                f"{name} 必须是 {low}..{high} 的整数：{value!r}")
        return value

    def seconds(name, default):
        value = raw.get(name, default)
        if type(value) is bool or not isinstance(value, (int, float)):
            raise BatchConfigError(f"{name} 必须是正数：{value!r}")
        value = float(value)
        if value <= 0:
            raise BatchConfigError(f"{name} 必须是正数：{value!r}")
        return value

    # ``max_attempts_per_item`` is frozen for PR D/F; PR C physically submits
    # an item at most once and a failed verification is final for this item.
    return {
        "max_inflight_sr_jobs": integer("max_inflight_sr_jobs", 1, 1, 64),
        "max_prepared_items": integer("max_prepared_items", 1, 1, 64),
        "detection_concurrency": integer("detection_concurrency", 1, 1, 16),
        "max_attempts_per_item": integer("max_attempts_per_item", 1, 1, 10),
        "monitor_poll_interval": seconds(
            "monitor_poll_interval",
            _env_float("SR_BATCH_MONITOR_POLL_SEC", 5.0)),
        "detection_timeout_sec": seconds(
            "detection_timeout_sec",
            _env_float("SR_BATCH_IO_TIMEOUT_SEC", 30.0)),
    }


def ensure_execution_allowed(mode):
    """Refuse to mix demo and production execution paths.

    The fake scheduler is only reachable through the Slurm executor; with
    ``SR_EXECUTOR=local`` the same submit would start a real child process, so
    the fake flag alone is not enough evidence.  The work directory must be an
    explicit, writable, non-array directory because ``submit_run_sr`` writes the
    generated config and batch script there.
    """
    runtime = sr_runtime()
    if mode == "production":
        raise BatchGateError(
            "production batch execution is not enabled in PR C "
            "(real Detector/Verifier arrives in PR D/F)")
    if mode != "demo":
        raise BatchGateError(f"unsupported batch mode: {mode!r}")
    if runtime.executor != "slurm":
        raise BatchGateError(
            f"demo batch requires SR_EXECUTOR=slurm, got {runtime.executor!r}")
    if not runtime.fake:
        raise BatchGateError("demo batch requires SR_SLURM_FAKE=1")
    raw = (os.environ.get("SR_SLURM_WORK_DIR") or "").strip()
    if not raw:
        raise BatchGateError(
            "demo batch requires an explicit SR_SLURM_WORK_DIR")
    root = Path(raw).resolve()
    if not root.is_dir() or not os.access(root, os.W_OK):
        raise BatchGateError(f"SR_SLURM_WORK_DIR is not a writable directory: {root}")
    if "DiskArray" in root.parts:
        raise BatchGateError(
            "demo batch work directory must not be the production array")
    return root


def _check_submit_receipt(result, work_root):
    script = Path(str(result.get("batch_script") or "")).resolve()
    if not (script == work_root or work_root in script.parents):
        raise BatchGateError(
            f"submit escaped the demo work directory: {script}")
    if result.get("job_id") in (None, ""):
        raise BatchGateError("submit returned no job id")


# ---------------------------------------------------------------------------
# deterministic item pipeline
# ---------------------------------------------------------------------------

def _item_attempt(item):
    return int((item.get("checkpoint") or {}).get("attempt") or 0)


def _io_key(goal_id, item):
    return f"{goal_id}:{item['item_id']}:{item['version']}"


def _detection_job(snapshot):
    detection = batch_detector.detect(snapshot["target_image"])
    candidates = detection.get("candidates") or []
    if not candidates:
        return {"detection": detection, "mask": None}
    mask = batch_mask.build_mask(
        snapshot["mask_path"],
        width=snapshot["target_image"]["width"],
        height=snapshot["target_image"]["height"],
        candidates=candidates,
        provenance=detection.get("provenance") or "mock",
    )
    return {"detection": detection, "mask": mask}


def _ensure_io_executor(state):
    executor = getattr(state, "batch_io_executor", None)
    if executor is None:
        executor = ThreadPoolExecutor(
            max_workers=max(2, int(os.environ.get("SR_BATCH_IO_WORKERS", "2"))),
            thread_name_prefix="batch-io")
        state.batch_io_executor = executor
    if not hasattr(state, "batch_io_jobs"):
        state.batch_io_jobs = {}
    return executor


def _mask_path(goal_id, item_id, detection_round):
    root = batch_manifest.artifact_root() / "work" / goal_id / item_id
    return root / f"detect-{int(detection_round)}" / "mask.tif"


def _open_item(snapshot):
    """Validate the frozen inputs immediately before detection.

    Reading the TIFF header is small IO; the expensive decode happens on the
    IO executor.  Any drift between the frozen revision and the file now on
    disk blocks the item instead of silently repairing a different image.
    """
    target = snapshot["target_image"]
    reference = snapshot["reference_image"]
    path = Path(target["path"])
    try:
        ensure_allowed(path, kind="file")
    except PathDeniedError as exc:
        raise ItemBlocked(f"PAN 不在允许的盘阵前缀内：{exc}") from exc
    if not path.is_file():
        raise ItemBlocked(f"PAN 文件不存在：{path}")
    revision = preview_jpg.source_revision(path)
    if target.get("revision") and revision != target["revision"]:
        raise ItemBlocked("PAN 输入修订已变化，拒绝按冻结清单继续")
    info = preview_jpg.probe_tiff(path)
    for key, frozen in (("W", "width"), ("H", "height"), ("bits", "bits"),
                        ("spp", "spp"), ("compression", "compression"),
                        ("sample_format", "sample_format")):
        if key in info and target.get(frozen) is not None:
            if info[key] != target[frozen]:
                raise ItemBlocked(
                    f"PAN {key}={info[key]!r} 与冻结值 {target[frozen]!r} 不一致")
    ref_path = Path(reference["path"])
    try:
        ensure_allowed(ref_path, kind="file")
    except PathDeniedError as exc:
        raise ItemBlocked(f"NOSR 不在允许的盘阵前缀内：{exc}") from exc
    if not ref_path.is_file():
        raise ItemBlocked(f"NOSR 文件不存在：{ref_path}")
    if (reference.get("revision")
            and preview_jpg.source_revision(ref_path) != reference["revision"]):
        raise ItemBlocked("NOSR 输入修订已变化，拒绝按冻结清单继续")
    scene = Path(snapshot["scene_ref"])
    for backup in (scene / "PAN_old_latest.tif", scene / "PAN_old_2.tif"):
        if backup.exists():
            raise ItemBlocked(f"已存在 PAN 备份，拒绝重复修复：{backup.name}")
    return info


def _start_detection(state, goal, item, lease, cfg):
    """Record DETECTING and hand detection+Mask to the IO executor."""
    cp = dict(item["checkpoint"])
    cp["phase"] = "DETECTING"
    cp["detection_round"] = int(cp.get("detection_round") or 0) + 1
    round_no = cp["detection_round"]
    updated = state.store.transition_goal_item(
        goal["goal_id"], step_id=item["step_id"],
        expected_version=item["version"], status="DETECTING",
        lease_token=lease.token, checkpoint=cp, next_wake_at=time.time())
    if not updated:
        return False
    snapshot = {
        "goal_id": goal["goal_id"],
        "item_id": item["item_id"],
        "version": updated["version"],
        "attempt": int(cp.get("attempt") or 0),
        "scene_ref": cp.get("scene_ref"),
        "target_image": cp.get("target_image"),
        "reference_image": cp.get("reference_image"),
        "mask_path": _mask_path(goal["goal_id"], item["item_id"], round_no).as_posix(),
    }
    executor = _ensure_io_executor(state)
    try:
        future = executor.submit(_detection_job, snapshot)
    except Exception as exc:  # executor shutdown is a worker bug, not an item fact
        _retry_detection(state, goal, updated, lease, cfg,
                         f"检测任务启动失败：{type(exc).__name__}: {exc}")
        return False
    state.batch_io_jobs[_io_key(goal["goal_id"], updated)] = {
        "future": future,
        "started": time.monotonic(),
        "goal_id": goal["goal_id"],
        "item_id": item["item_id"],
        "version": updated["version"],
        "attempt": snapshot["attempt"],
    }
    return True


def _retry_detection(state, goal, item, lease, cfg, reason):
    cp = dict(item["checkpoint"])
    failures = int(cp.get("detection_failures") or 0) + 1
    cp["detection_failures"] = failures
    cp["last_detection_error"] = reason
    if failures >= MAX_DETECTION_ATTEMPTS:
        return state.store.transition_goal_item(
            goal["goal_id"], step_id=item["step_id"],
            expected_version=item["version"], status="FAILED",
            lease_token=lease.token, checkpoint=cp,
            error=reason, capacity_reserved=0)
    delay = 2 ** (failures - 1)
    return state.store.transition_goal_item(
        goal["goal_id"], step_id=item["step_id"],
        expected_version=item["version"], status="RETRY_WAIT",
        lease_token=lease.token, checkpoint=cp, error=reason,
        next_wake_at=time.time() + delay)


def _collect_detections(state, goal, lease, cfg, *, allow_write=True):
    """Commit finished IO futures. Never waits; stale results are discarded."""
    jobs = getattr(state, "batch_io_jobs", None) or {}
    gid = goal["goal_id"]
    for key, record in list(jobs.items()):
        if record["goal_id"] != gid:
            continue
        future = record["future"]
        if not future.done():
            if (time.monotonic() - record["started"]
                    > cfg["detection_timeout_sec"]):
                jobs.pop(key, None)
                future.cancel()
                if allow_write:
                    item = state.store.get_goal_item(gid, record["item_id"])
                    if (item and item["version"] == record["version"]
                            and item["status"] == "DETECTING"):
                        _retry_detection(state, goal, item, lease, cfg,
                                         "检测超时")
            continue
        jobs.pop(key, None)
        item = state.store.get_goal_item(gid, record["item_id"])
        if (not item or item["version"] != record["version"]
                or item["status"] != "DETECTING"):
            continue                       # cancelled, retried or superseded
        if not allow_write:
            continue
        error = future.exception()
        if error is not None:
            _retry_detection(state, goal, item, lease, cfg,
                             f"检测/掩码失败：{type(error).__name__}: {error}")
            continue
        payload = future.result()
        detection = payload["detection"]
        cp = dict(item["checkpoint"])
        cp["detection"] = detection
        if not detection.get("candidates"):
            cp["phase"] = "NO_ISSUE"
            state.store.transition_goal_item(
                gid, step_id=item["step_id"], expected_version=item["version"],
                status="NO_ISSUE", lease_token=lease.token, checkpoint=cp,
                verification={"ok": True, "provenance": detection.get("provenance"),
                              "reason": "no_candidate"},
                capacity_reserved=0,
                extra_events=[(EVT_ITEM_VERIFIED, {
                    "item_id": item["item_id"], "ok": True,
                    "provenance": detection.get("provenance"),
                    "state": "NO_ISSUE"})])
            continue
        mask = payload["mask"]
        cp["mask"] = mask
        cp["phase"] = "MASK_READY"
        payload_event = {
            "item_id": item["item_id"], "path": mask["path"],
            "sha256": mask["sha256"], "width": mask["width"],
            "height": mask["height"], "spp": mask["spp"],
            "foreground_pixels": mask["foreground_pixels"],
            "provenance": mask.get("provenance"),
        }
        state.store.transition_goal_item(
            gid, step_id=item["step_id"], expected_version=item["version"],
            status="MASK_READY", lease_token=lease.token, checkpoint=cp,
            next_wake_at=time.time(),
            extra_events=[(EVT_MASK_READY, payload_event)])
    return None


def _advance_open(state, goal, item, lease, cfg):
    cp = dict(item["checkpoint"])
    try:
        _open_item(cp)
    except ItemBlocked as exc:
        cp["phase"] = "BLOCKED"
        state.store.transition_goal_item(
            goal["goal_id"], step_id=item["step_id"],
            expected_version=item["version"], status="BLOCKED",
            lease_token=lease.token, checkpoint=cp, error=str(exc),
            capacity_reserved=0)
        return False
    return _start_detection(state, goal, item, lease, cfg)


def _produce_one(state, goal, lease, cfg):
    """Advance at most one Item. Returns True when a producer step happened."""
    store = state.store
    gid = goal["goal_id"]
    items = store.list_goal_items(gid)
    now = time.time()

    opening = next((i for i in items if i["status"] == "OPENING"), None)
    if opening:
        return _advance_open(state, goal, opening, lease, cfg)

    jobs = getattr(state, "batch_io_jobs", None) or {}
    orphan = next(
        (i for i in items if i["status"] == "DETECTING"
         and _io_key(gid, i) not in jobs), None)
    if orphan:
        # Read-only detection is safe to replay after a restart.
        return _start_detection(state, goal, orphan, lease, cfg)

    capacity_full = (store.count_reserved_slots(gid)
                     >= cfg["max_inflight_sr_jobs"])
    ready = None
    if not capacity_full:
        ready = next(
            (i for i in items if i["status"] == "MASK_READY"
             and (i["next_wake_at"] is None or i["next_wake_at"] <= now)), None)
    if ready:
        return _submit_ready(state, goal, ready, lease, cfg)

    prepared = sum(1 for i in items if i["status"] in PREPARING)
    if prepared >= cfg["max_prepared_items"]:
        return False
    pending_jobs = sum(1 for r in jobs.values() if r["goal_id"] == gid)
    if pending_jobs >= cfg["detection_concurrency"]:
        return False
    candidate = next(
        (i for i in items if i["status"] in {"PENDING", "RETRY_WAIT"}
         and (i["next_wake_at"] is None or i["next_wake_at"] <= now)), None)
    if candidate is None:
        return False
    cp = dict(candidate["checkpoint"])
    cp["phase"] = "OPENING"
    updated = store.transition_goal_item(
        gid, step_id=candidate["step_id"],
        expected_version=candidate["version"], status="OPENING",
        lease_token=lease.token, checkpoint=cp, next_wake_at=now)
    if not updated:
        return False
    return _advance_open(state, goal, updated, lease, cfg)


def _submit_params(item):
    cp = item["checkpoint"]
    mask = cp.get("mask") or {}
    if not mask.get("path"):
        raise ItemBlocked("MASK_READY 项缺少掩码工件")
    config = cp.get("config") or {}
    return sr_service.repair_request({
        "lq_path": normalize_submit_path(cp["scene_ref"]),
        "mask_path": mask["path"],
        "sr_scale": int(config.get("sr_scale") or 2),
        "suffix": sr_service.normalize_suffix(None),
        "gpu": 0,
        "cloud_limit": 80,
        "delete_ori": False,
        "grid_align": True,
        "options_yml": None,
        "repair": True,
    })


def _verify_submission_inputs(item):
    """Re-check the frozen PAN/NOSR and Mask at the submit boundary.

    ``_open_item`` runs before detection; a ``MASK_READY`` item can still wait
    for capacity, be paused and resumed, or survive a restart.  All of those
    windows allow the source files or the Mask on disk to change between
    detection and submission, so the frozen revision and the Mask contract are
    verified again here — immediately before the capacity reservation.
    """
    cp = item["checkpoint"]
    target = cp.get("target_image") or {}
    mask = cp.get("mask") or {}
    try:
        _open_item(cp)
    except ItemBlocked:
        raise
    except Exception as exc:  # noqa: BLE001 — any IO fact becomes an item fact
        raise ItemBlocked(
            f"提交前复核输入失败：{type(exc).__name__}: {exc}") from exc
    if not mask.get("path"):
        raise ItemBlocked("MASK_READY 项缺少掩码工件")
    if target.get("width") is None or target.get("height") is None:
        raise ItemBlocked("MASK_READY 项缺少冻结的 PAN 尺寸")
    try:
        artifact_root = batch_manifest.artifact_root().resolve()
        mask_path = Path(mask["path"]).resolve()
        if artifact_root not in mask_path.parents:
            raise ItemBlocked("掩码路径不在批次工件目录内")
        checked = batch_mask.verify_mask(
            mask_path, width=target["width"], height=target["height"],
            sha256=mask.get("sha256"))
    except ItemBlocked:
        raise
    except Exception as exc:  # noqa: BLE001 — MaskError and IO errors alike
        raise ItemBlocked(
            f"提交前复核掩码失败：{type(exc).__name__}: {exc}") from exc
    if (mask.get("foreground_pixels") is not None
            and checked["foreground_pixels"] != int(mask["foreground_pixels"])):
        raise ItemBlocked("掩码前景像素数与冻结值不一致")


def _submit_ready(state, goal, item, lease, cfg):
    store = state.store
    gid = goal["goal_id"]
    work_root = ensure_execution_allowed(
        (goal["checkpoint"] or {}).get("mode") or "demo")
    try:
        _verify_submission_inputs(item)
        params = _submit_params(item)
    except ItemBlocked as exc:
        cp = dict(item["checkpoint"])
        cp["phase"] = "BLOCKED"
        store.transition_goal_item(
            gid, step_id=item["step_id"], expected_version=item["version"],
            status="BLOCKED", lease_token=lease.token, checkpoint=cp,
            error=str(exc), capacity_reserved=0)
        return False
    fingerprint = sr_service.task_fingerprint(params)
    attempt = _item_attempt(item) + 1
    intent_key = f"batch:{gid}:{item['item_id']}:a{attempt}:{fingerprint}"
    cp = dict(item["checkpoint"])
    cp["sr_fingerprint"] = fingerprint
    reserved = store.reserve_item_submission(
        gid, step_id=item["step_id"], expected_version=item["version"],
        lease_token=lease.token, intent_key=intent_key, intent_args=params,
        max_inflight=cfg["max_inflight_sr_jobs"], checkpoint=cp)
    if reserved is None:
        return False
    scope = current_tool.set({"store": store, "intent": reserved["intent"]})
    try:
        result = sr_service.submit_run_sr(params, store=store)
    except sr_service.SubmissionUnknown:
        _reconcile_submitting(state, goal, lease)
        return False
    except Exception as exc:  # noqa: BLE001 — ledger decides what is safe next
        # The ledger is authoritative: if run_sr committed a task row the item
        # is blocked; if nothing was committed it returns to MASK_READY.
        _reconcile_submitting(state, goal, lease)
        return False
    finally:
        current_tool.reset(scope)
    _check_submit_receipt(result, work_root)
    job_id = result.get("job_id")
    current = store.get_goal_item(gid, item["item_id"])
    if not current or current["status"] != "SUBMITTING":
        return False
    cp = dict(current["checkpoint"])
    cp.update({
        "phase": "WAITING_SR",
        "job_id": job_id,
        "sr_status": result.get("status"),
        "config_xml": result.get("config_xml"),
        "batch_script": result.get("batch_script"),
        "log_dir": result.get("log_dir"),
    })
    store.transition_goal_item(
        gid, step_id=current["step_id"],
        expected_version=current["version"], status="WAITING_SR",
        lease_token=lease.token, checkpoint=cp, external_job_id=job_id,
        capacity_reserved=1,
        next_wake_at=time.time() + cfg["monitor_poll_interval"],
        extra_events=[(EVT_SR_RECEIPT, {
            "item_id": item["item_id"], "job_id": job_id,
            "fingerprint": current["checkpoint"].get("sr_fingerprint"),
            "status": result.get("status"),
            "provenance": "fake" if sr_runtime().fake else "slurm",
        })])
    return True


def _reconcile_submitting(state, goal, lease):
    """Resolve every SUBMITTING item from the durable SR ledger, then park.

    The order is fixed: adopt an existing job id first, return a provably
    side-effect-free item to MASK_READY second, and only mark BLOCKED when the
    records cannot confirm the outcome. No path here calls submit again.
    """
    store = state.store
    gid = goal["goal_id"]
    changed = False
    for item in store.list_goal_items(gid, statuses=("SUBMITTING",)):
        cp = dict(item["checkpoint"])
        fingerprint = cp.get("sr_fingerprint")
        intent_key = cp.get("intent_key")
        task = store.get_sr_task(fingerprint) if fingerprint else None
        intent = (store.get_tool_intent(gid, intent_key)
                  if intent_key else None)
        job_id = task.get("job_id") if task else None
        if job_id:
            cp.update({"phase": "WAITING_SR", "job_id": job_id,
                       "sr_status": "RECOVERED"})
            if intent and not intent.get("external_job_id"):
                store.update_tool_intent(
                    intent["intent_id"], status="SUBMITTED",
                    external_job_id=int(job_id),
                    result={"job_id": int(job_id)})
            store.transition_goal_item(
                gid, step_id=item["step_id"], expected_version=item["version"],
                status="WAITING_SR", lease_token=lease.token, checkpoint=cp,
                external_job_id=int(job_id), capacity_reserved=1,
                next_wake_at=time.time() + 1,
                extra_events=[(EVT_SR_RECEIPT, {
                    "item_id": item["item_id"], "job_id": int(job_id),
                    "fingerprint": fingerprint, "status": "RECOVERED",
                    "recovered": True})])
            changed = True
            continue
        if task is None and (intent is None or intent["status"] == "INTENDED"):
            # Nothing was committed to sr_tasks, so no external submit can have
            # happened. The INTENDED intent is reused by the next reservation.
            cp["phase"] = "MASK_READY"
            cp.pop("submitted_at", None)
            store.transition_goal_item(
                gid, step_id=item["step_id"], expected_version=item["version"],
                status="MASK_READY", lease_token=lease.token, checkpoint=cp,
                capacity_reserved=0, next_wake_at=time.time())
            changed = True
            continue
        cp["phase"] = "BLOCKED"
        cp["sr_status"] = "UNKNOWN"
        cp["blocked_reason"] = "submission outcome could not be confirmed"
        store.transition_goal_item(
            gid, step_id=item["step_id"], expected_version=item["version"],
            status="BLOCKED", lease_token=lease.token, checkpoint=cp,
            error=("外部提交可能已发生但未取得 Job ID；已保留容量位，"
                   "请先核对调度器/ SR 记录，不要盲目重投"),
            capacity_reserved=1,
            extra_events=[(EVT_BATCH_CONTROL, {
                "item_id": item["item_id"], "reason": "submission_unknown"})])
        changed = True
    return changed


# ---------------------------------------------------------------------------
# monitor
# ---------------------------------------------------------------------------

def _verify_item(state, goal, item, lease, cfg, mode):
    store = state.store
    gid = goal["goal_id"]
    cp = dict(item["checkpoint"])
    status = cp.get("job_status")
    if not status:
        task = store.get_sr_task(cp.get("sr_fingerprint") or "")
        job_id = item.get("external_job_id") or cp.get("job_id")
        if not task or job_id is None:
            cp["phase"] = "BLOCKED"
            return store.transition_goal_item(
                gid, step_id=item["step_id"], expected_version=item["version"],
                status="BLOCKED", lease_token=lease.token, checkpoint=cp,
                error="外部作业记录或 Job ID 缺失", capacity_reserved=1)
        status = sr_service.query_job_status(job_id, task=task)
        if status.get("active"):
            return store.transition_goal_item(
                gid, step_id=item["step_id"], expected_version=item["version"],
                status="WAITING_SR", lease_token=lease.token,
                next_wake_at=time.time() + cfg["monitor_poll_interval"],
                capacity_reserved=1)
        if status.get("state") == "UNKNOWN" or status.get("active") is None:
            return store.transition_goal_item(
                gid, step_id=item["step_id"], expected_version=item["version"],
                status="WAITING_SR", lease_token=lease.token,
                next_wake_at=time.time() + cfg["monitor_poll_interval"],
                capacity_reserved=1)
        cp["job_status"] = status

    job_id = item.get("external_job_id") or cp.get("job_id")
    if mode == "demo":
        ok = status.get("state") == "COMPLETED"
        verification = {
            "ok": bool(ok), "provenance": "mock",
            "state": status.get("state"), "job_id": job_id,
            "exit_code": status.get("exit_code"),
            "reasons": [] if ok else [f"SR job {status.get('state')}"],
        }
    else:
        from backend.services import goal_worker
        task = store.get_sr_task(cp.get("sr_fingerprint") or "")
        contract = (goal_worker.sr_contract(task, status)
                    if status.get("state") == "COMPLETED"
                    else {"ok": False,
                          "reasons": [f"SR job {status.get('state')}"]})
        verification = {"provenance": "contract", **contract}
    cp["phase"] = "SUCCEEDED" if verification["ok"] else "FAILED"
    cp["verification"] = verification
    return store.transition_goal_item(
        gid, step_id=item["step_id"], expected_version=item["version"],
        status="SUCCEEDED" if verification["ok"] else "FAILED",
        lease_token=lease.token, checkpoint=cp, verification=verification,
        error=None if verification["ok"] else "; ".join(
            verification.get("reasons") or ["verification failed"]),
        capacity_reserved=0,
        extra_events=[(EVT_ITEM_VERIFIED, {
            "item_id": item["item_id"], "ok": verification["ok"],
            "provenance": verification.get("provenance"),
            "state": status.get("state"), "job_id": job_id})])


def _monitor_item(state, goal, item, lease, cfg, mode):
    store = state.store
    gid = goal["goal_id"]
    cp = dict(item["checkpoint"])
    if item["status"] == "VERIFYING":
        return _verify_item(state, goal, item, lease, cfg, mode)
    job_id = item.get("external_job_id") or cp.get("job_id")
    fingerprint = cp.get("sr_fingerprint")
    task = store.get_sr_task(fingerprint) if fingerprint else None
    if not task or job_id is None:
        cp["phase"] = "BLOCKED"
        return store.transition_goal_item(
            gid, step_id=item["step_id"], expected_version=item["version"],
            status="BLOCKED", lease_token=lease.token, checkpoint=cp,
            error="找不到该 Item 对应的 SR 任务记录", capacity_reserved=1)
    status = sr_service.query_job_status(job_id, task=task)
    if status.get("active"):
        cp["sr_status"] = status.get("state")
        cp.pop("unknown_checks", None)
        return store.transition_goal_item(
            gid, step_id=item["step_id"], expected_version=item["version"],
            status="WAITING_SR", lease_token=lease.token, checkpoint=cp,
            capacity_reserved=1,
            next_wake_at=time.time() + cfg["monitor_poll_interval"])
    if status.get("state") == "UNKNOWN" or status.get("active") is None:
        checks = int(cp.get("unknown_checks") or 0) + 1
        cp["unknown_checks"] = checks
        cp["sr_status"] = "UNKNOWN"
        if checks >= MAX_UNKNOWN_CHECKS:
            cp["phase"] = "BLOCKED"
            return store.transition_goal_item(
                gid, step_id=item["step_id"],
                expected_version=item["version"], status="BLOCKED",
                lease_token=lease.token, checkpoint=cp,
                error="外部 SR 状态连续 UNKNOWN，需人工核对",
                capacity_reserved=1)
        return store.transition_goal_item(
            gid, step_id=item["step_id"], expected_version=item["version"],
            status="WAITING_SR", lease_token=lease.token, checkpoint=cp,
            capacity_reserved=1,
            next_wake_at=time.time() + cfg["monitor_poll_interval"])
    cp.update({"phase": "VERIFYING", "job_status": status,
               "sr_status": status.get("state"), "unknown_checks": 0})
    verifying = store.transition_goal_item(
        gid, step_id=item["step_id"], expected_version=item["version"],
        status="VERIFYING", lease_token=lease.token, checkpoint=cp,
        capacity_reserved=1, next_wake_at=time.time(),
        extra_events=[(EVT_JOB_TERMINAL, {
            "item_id": item["item_id"], "job_id": job_id,
            "state": status.get("state"),
            "exit_code": status.get("exit_code"),
            "provenance": "mock" if mode == "demo" else "contract"})])
    if verifying:
        _verify_item(state, goal, verifying, lease, cfg, mode)
    return verifying


def _monitor_due(state, goal, lease, cfg, mode):
    items = state.store.list_due_goal_items(
        goal["goal_id"], statuses=("SUBMITTING", "WAITING_SR", "VERIFYING"))
    for item in items:
        if item["status"] == "SUBMITTING":
            _reconcile_submitting(state, goal, lease)
            continue
        _monitor_item(state, goal, item, lease, cfg, mode)
    return len(items)


# ---------------------------------------------------------------------------
# aggregation
# ---------------------------------------------------------------------------

def _build_report(goal, items, counts, mode):
    return {
        "workflow": goal["checkpoint"].get("workflow"),
        "manifest_ref": goal["checkpoint"].get("manifest_ref"),
        "mode": mode,
        "provenance": "mock" if mode == "demo" else "contract",
        "generated_at": time.time(),
        "counts": counts,
        "items": [{
            "item_id": item["item_id"],
            "name": item["name"],
            "status": item["status"],
            "attempt": _item_attempt(item),
            "external_job_id": item.get("external_job_id"),
            "verification": item.get("verification"),
            "error": item.get("error"),
            "capacity_reserved": item.get("capacity_reserved"),
        } for item in items],
    }


def _write_report(goal_id, report):
    root = batch_manifest.artifact_root() / "reports"
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{goal_id}.json"
    tmp = root / f"{goal_id}.tmp"
    tmp.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    os.replace(tmp, path)
    return path.as_posix()


def _verify_batch(items, mode):
    effective = [i for i in items if i["status"] != "SKIPPED"]
    bad = [i for i in effective if i["status"] not in {"SUCCEEDED", "NO_ISSUE"}]
    if bad:
        return {
            "ok": False,
            "provenance": "mock" if mode == "demo" else "contract",
            "reasons": [f"{i['item_id']} is {i['status']}" for i in bad],
        }
    return {
        "ok": True,
        "provenance": "mock" if mode == "demo" else "contract",
        "items": len(effective),
        "skipped": len(items) - len(effective),
    }


def _block_capacity_deadlock(state, goal, lease, cfg, mode, items, counts):
    """Park a batch whose capacity is permanently held by unknown submits.

    A ``BLOCKED`` item deliberately keeps its capacity slot across restarts so
    an unknown external submit cannot be duplicated.  When all slots are held
    that way and no monitored item can release one, no remaining item can ever
    be submitted.  Waiting forever hides the failure; the batch is therefore
    finalized as ``BLOCKED``, the unreachable items become explicit
    ``SKIPPED`` rows, and the report records why.  The reserved slots are not
    released: recovery requires a human to reconcile the scheduler/SR records,
    after which the remaining scenes are imported as a new batch.
    """
    store = state.store
    gid = goal["goal_id"]
    jobs = getattr(state, "batch_io_jobs", None) or {}
    for key, record in list(jobs.items()):
        if record.get("goal_id") == gid:
            jobs.pop(key, None)
            record["future"].cancel()
    for item in items:
        if item["status"] in CONVERGED or item["status"] in PARKED:
            continue
        cp = dict(item["checkpoint"])
        cp["phase"] = "SKIPPED"
        cp["exclusion_reason"] = "capacity_deadlock"
        store.transition_goal_item(
            gid, step_id=item["step_id"], expected_version=item["version"],
            status="SKIPPED", lease_token=lease.token, checkpoint=cp,
            capacity_reserved=0,
            event_payload={"reason": "capacity_deadlock"})
    items = store.list_goal_items(gid)
    counts = store.goal_item_status_counts(gid)
    report = _build_report(goal, items, counts, mode)
    try:
        report_ref = _write_report(gid, report)
    except OSError:
        report_ref = None
    cp = dict(goal["checkpoint"])
    cp.update({
        "report": report, "report_ref": report_ref, "phase": "blocked",
        "producer_status": "DONE", "monitoring_done": True,
        "blocked_reason": "capacity_deadlock", "counts": counts,
    })
    store.transition_goal(
        gid, "BLOCKED", checkpoint=cp, phase="blocked",
        expected_version=lease.version, lease_token=lease.token,
        release=True,
        error=("无法确认的外部提交占满了并发容量，且没有可释放容量的活动 Job；"
               "剩余 Item 已标记为 capacity_deadlock，请先核对调度器/SR 记录，"
               "再重新导入剩余景"),
        event_type=EVT_BATCH_PHASE,
        event_payload={"status": "BLOCKED", "phase": "blocked",
                       "counts": counts, "reason": "capacity_deadlock"},
        extra_events=[(EVT_BATCH_REPORT, report)])
    return True


def _settle(state, goal, lease, cfg, mode):
    store = state.store
    gid = goal["goal_id"]
    counts = store.goal_item_status_counts(gid)
    items = store.list_goal_items(gid)
    remaining = {status for status in counts
                 if status not in CONVERGED and status not in PARKED}
    if remaining:
        monitored = sum(counts.get(name, 0) for name in MONITORED)
        held_by_blocked = sum(
            1 for item in items
            if item["status"] == "BLOCKED" and item.get("capacity_reserved"))
        if (monitored == 0
                and held_by_blocked >= cfg["max_inflight_sr_jobs"]):
            # Every slot is held by an unconfirmable submit and no active job
            # can release one: no remaining item can ever be submitted.
            return _block_capacity_deadlock(
                state, goal, lease, cfg, mode, items, counts)
        reserved = store.count_reserved_slots(gid)
        capacity_full = reserved >= cfg["max_inflight_sr_jobs"]
        prepared = sum(counts.get(name, 0) for name in PREPARING)
        can_prepare = (
            prepared < cfg["max_prepared_items"]
            and (counts.get("PENDING", 0) + counts.get("RETRY_WAIT", 0)) > 0)
        can_submit = (not capacity_full) and counts.get("MASK_READY", 0) > 0
        # "Draining" means the Producer genuinely cannot advance. An inflight
        # job alone must not stop N+1: keep dispensing until capacity and the
        # prepared window are both saturated.
        draining = not (can_prepare or can_submit)
        target = "WAITING_EXTERNAL" if draining else "RUNNING"
        phase = "draining" if draining else "dispatching"
        wake = (time.time() + cfg["monitor_poll_interval"]
                if draining else time.time())
        changed = goal["status"] != target or goal["phase"] != phase
        store.arm_goal(
            gid, lease_token=lease.token, expected_version=lease.version,
            status=target, phase=phase, next_wake_at=wake,
            event_type=EVT_BATCH_PHASE if changed else None,
            event_payload={"status": target, "phase": phase,
                           "counts": counts} if changed else None)
        return False

    # Every item reached a terminal/parked state. Verify, then aggregate with
    # the documented priority: BLOCKED > FAILED > COMPLETED.
    if goal["status"] != "VERIFYING":
        goal = store.transition_goal(
            gid, "VERIFYING", phase="verifying", next_wake_at=time.time(),
            expected_version=lease.version, lease_token=lease.token,
            event_type=EVT_BATCH_PHASE,
            event_payload={"status": "VERIFYING", "phase": "verifying",
                           "counts": counts})
        if goal is None:
            return False
    cp = dict(goal["checkpoint"])
    report = _build_report(goal, items, counts, mode)
    cp.update({"producer_status": "DONE", "counts": counts,
               "monitoring_done": True})
    blocked = [i for i in items if i["status"] == "BLOCKED"]
    failed = [i for i in items if i["status"] == "FAILED"]
    if blocked:
        cp["report"] = report
        cp["phase"] = "blocked"
        store.transition_goal(
            gid, "BLOCKED", checkpoint=cp, phase="blocked",
            expected_version=lease.version, lease_token=lease.token,
            release=True, error="; ".join(
                f"{i['item_id']}: {i.get('error') or 'blocked'}" for i in blocked),
            event_type=EVT_BATCH_PHASE,
            event_payload={"status": "BLOCKED", "phase": "blocked",
                           "counts": counts},
            extra_events=[(EVT_BATCH_REPORT, report)])
        return True
    if failed:
        cp["report"] = report
        cp["phase"] = "failed"
        store.transition_goal(
            gid, "FAILED", checkpoint=cp, phase="failed",
            expected_version=lease.version, lease_token=lease.token,
            release=True, error="; ".join(
                f"{i['item_id']}: {i.get('error') or 'failed'}" for i in failed),
            event_type=EVT_BATCH_PHASE,
            event_payload={"status": "FAILED", "phase": "failed",
                           "counts": counts},
            extra_events=[(EVT_BATCH_REPORT, report)])
        return True
    verification = _verify_batch(items, mode)
    try:
        report_ref = _write_report(gid, report)
    except OSError:
        report_ref = None
    cp.update({"report": report, "report_ref": report_ref,
               "phase": "done"})
    store.complete_verified_goal(
        gid, {**verification, "report_ref": report_ref},
        checkpoint=cp, phase="done",
        expected_version=lease.version, lease_token=lease.token, release=True,
        extra_events=[(EVT_BATCH_REPORT, report)])
    return True


# ---------------------------------------------------------------------------
# per-Goal passes
# ---------------------------------------------------------------------------

def _load(goal):
    manifest = batch_manifest.load_manifest(
        (goal["checkpoint"] or {}).get("manifest_ref") or "")
    return manifest, str(manifest.get("mode") or "demo")


def _process_batch(state, claimed, lease, allow_produce):
    scope = current_lease.set(lease)
    store = state.store
    try:
        goal = lease.check(store)
        manifest, mode = _load(goal)
        cfg = batch_config(manifest)
        if allow_produce:
            ensure_execution_allowed(mode)
        # A resumed Goal must not inherit a stale monitoring_done marker from
        # an earlier pause; otherwise a later pause would skip its accepted
        # jobs. Producer activity re-opens the monitoring window.
        if (goal.get("checkpoint") or {}).get("monitoring_done"):
            cp = dict(goal["checkpoint"])
            cp["monitoring_done"] = False
            store.patch_goal_checkpoint(
                goal["goal_id"], cp, lease_token=lease.token,
                expected_version=lease.version, monitor_done=False,
                monitor_state=goal["status"])
        _reconcile_submitting(state, goal, lease)
        _collect_detections(state, goal, lease, cfg)
        _monitor_due(state, goal, lease, cfg, mode)
        produced = False
        if allow_produce:
            produced = _produce_one(state, goal, lease, cfg)
        _settle(state, goal, lease, cfg, mode)
        return {"produced": bool(produced)}
    except LeaseLost:
        return {"produced": False}
    finally:
        current_lease.reset(scope)
        store.close_thread()


def _skip_pre_submit(state, goal, lease):
    """Cancelled items that never entered the external window are SKIPPED."""
    for item in state.store.list_goal_items(goal["goal_id"]):
        if item["status"] not in PRE_SUBMIT:
            continue
        cp = dict(item["checkpoint"])
        cp["phase"] = "SKIPPED"
        cp["exclusion_reason"] = "user_cancelled"
        state.store.transition_goal_item(
            goal["goal_id"], step_id=item["step_id"],
            expected_version=item["version"], status="SKIPPED",
            lease_token=lease.token, checkpoint=cp,
            capacity_reserved=0,
            event_payload={"reason": "user_cancelled"})


def _process_batch_monitor(state, claimed, lease):
    """PAUSED/CANCELLED: reconcile accepted jobs, never submit or rewrite status."""
    scope = current_lease.set(lease)
    store = state.store
    try:
        goal = lease.check(store)
        manifest, mode = _load(goal)
        try:
            cfg = batch_config(manifest)
        except BatchConfigError:
            cfg = {"max_inflight_sr_jobs": 1, "max_prepared_items": 1,
                   "detection_concurrency": 1, "max_attempts_per_item": 1,
                   "monitor_poll_interval": _env_float(
                       "SR_BATCH_MONITOR_POLL_SEC", 5.0),
                   "detection_timeout_sec": _env_float(
                       "SR_BATCH_IO_TIMEOUT_SEC", 30.0)}
        # Reconcile first: a cancelled batch may still own an accepted job.
        _reconcile_submitting(state, goal, lease)
        if goal["status"] == "CANCELLED":
            _skip_pre_submit(state, goal, lease)
            _collect_detections(state, goal, lease, cfg, allow_write=False)
        else:
            _collect_detections(state, goal, lease, cfg)
        _monitor_due(state, goal, lease, cfg, mode)

        counts = store.goal_item_status_counts(goal["goal_id"])
        active = any(counts.get(name) for name in MONITORED)
        cp = dict(goal["checkpoint"])
        was_done = bool(cp.get("monitoring_done"))
        status_changed = cp.get("monitor_status") != goal["status"]
        cp["monitoring_done"] = not active
        cp["monitor_counts"] = counts
        cp["monitor_status"] = goal["status"]
        if not active and (not was_done or status_changed):
            store.patch_goal_checkpoint(
                goal["goal_id"], cp, lease_token=lease.token,
                expected_version=lease.version, monitor_done=True,
                monitor_state=goal["status"],
                event_type=EVT_BATCH_CONTROL,
                event_payload={
                    "status": goal["status"], "monitoring_done": True,
                    "counts": counts})
        else:
            store.patch_goal_checkpoint(
                goal["goal_id"], cp, lease_token=lease.token,
                expected_version=lease.version, monitor_done=bool(not active),
                monitor_state=goal["status"])
        return {"produced": False}
    except LeaseLost:
        return {"produced": False}
    finally:
        current_lease.reset(scope)
        store.close_thread()


# ---------------------------------------------------------------------------
# scheduling loop
# ---------------------------------------------------------------------------

async def _run_leased(state, executor, fn, claimed, lease, seconds):
    loop = asyncio.get_running_loop()
    work = loop.run_in_executor(executor, fn, state, claimed, lease)
    try:
        while not work.done():
            done, _ = await asyncio.wait({work}, timeout=seconds / 3)
            if done:
                break
            with lease.lock:
                if not state.store.renew_goal(
                        lease.goal_id, lease.token, lease.version,
                        lease_sec=seconds):
                    lease.stopped.set()
        return await work
    finally:
        lease.stopped.set()
        state.store.release_goal(lease.goal_id, lease.token, lease.version)


def _rotate(goals, state):
    """Round-robin the producer starting point across Goals."""
    ordered = sorted(goals, key=lambda goal: goal["goal_id"])
    if not ordered:
        return ordered
    index = int(getattr(state, "batch_rr", 0)) % len(ordered)
    state.batch_rr = index + 1
    return ordered[index:] + ordered[:index]


async def batch_tick(state):
    """One serial Batch pass: monitor first, then at most one producer step."""
    if not hasattr(state, "batch_tick_lock"):
        state.batch_tick_lock = asyncio.Lock()
    async with state.batch_tick_lock:
        executor = getattr(state, "batch_executor", None)
        owned = executor is None
        if owned:
            executor = ThreadPoolExecutor(max_workers=1,
                                          thread_name_prefix="batch")
        try:
            seconds = max(0.5, float(os.getenv("SR_BATCH_LEASE_SEC", "60")))
            produced = False
            active = _rotate(
                state.store.list_goals(
                    limit=200, statuses=CLAIMABLE, kinds=("batch",)), state)
            for goal in active:
                claimed = state.store.claim_goal(
                    goal["goal_id"], "batch:" + uuid.uuid4().hex,
                    lease_sec=seconds)
                if not claimed:
                    continue
                lease = GoalLease(
                    claimed["goal_id"], claimed["lease_token"],
                    claimed["version"])
                try:
                    result = await _run_leased(
                        state, executor,
                        functools.partial(_process_batch,
                                          allow_produce=not produced),
                        claimed, lease, seconds)
                except Exception:
                    logging.getLogger(__name__).exception(
                        "Batch Goal %s failed during a producer pass",
                        claimed["goal_id"])
                    continue
                produced = produced or bool(
                    (result or {}).get("produced"))
            # Monitor-only pass: paused/cancelled Goals keep converging without
            # being re-claimed as runnable. Already-converged control rows are
            # filtered out in SQL before LIMIT, so a large finished backlog
            # cannot hide an older Goal that still owns a live job.
            for goal in _rotate(
                    state.store.list_batch_monitor_goals(limit=200), state):
                claimed = state.store.claim_goal_monitor(
                    goal["goal_id"], "batch-monitor:" + uuid.uuid4().hex,
                    lease_sec=seconds)
                if not claimed:
                    continue
                lease = GoalLease(
                    claimed["goal_id"], claimed["lease_token"],
                    claimed["version"])
                try:
                    await _run_leased(
                        state, executor, _process_batch_monitor, claimed,
                        lease, seconds)
                except Exception:
                    logging.getLogger(__name__).exception(
                        "Batch Goal %s failed during a monitor-only pass",
                        claimed["goal_id"])
        finally:
            if owned:
                executor.shutdown(wait=True, cancel_futures=True)


async def batch_loop(state):
    state.batch_executor = ThreadPoolExecutor(
        max_workers=1, thread_name_prefix="batch")
    _ensure_io_executor(state)
    try:
        while True:
            try:
                await batch_tick(state)
            except Exception:
                import logging
                logging.getLogger(__name__).exception(
                    "Batch scheduler pass failed; retrying next pass")
            await asyncio.sleep(max(
                0.05, float(os.getenv("SR_BATCH_POLL_SEC", "1"))))
    finally:
        state.batch_executor.shutdown(wait=False, cancel_futures=True)
        io_executor = getattr(state, "batch_io_executor", None)
        if io_executor is not None:
            io_executor.shutdown(wait=False, cancel_futures=True)
