"""Batch Snapshot projection (PR F).

The Panel must never derive business progress from SSE event counts.  This
module turns one consistent read of a Batch Goal, its ``batch_item`` rows and
the relevant ``sr_tasks`` rows into the only authoritative progress document
the browser is allowed to display.

Everything here is pure: ``Store.batch_snapshot`` owns the SQLite read
transaction and passes plain rows in.  No file IO, no artifact paths, no
decoded TIFFs.
"""

from __future__ import annotations

import time
from typing import Any, Iterable

CONTRACT_VERSION = "batch_snapshot.v1"

CONVERGED = frozenset({"SUCCEEDED", "NO_ISSUE", "FAILED", "SKIPPED"})
DETECTION_PENDING = frozenset({"PENDING", "OPENING", "DETECTING", "RETRY_WAIT"})
PREPARING = frozenset({"OPENING", "DETECTING", "MASK_READY"})
ACTIVE_JOB_STATUSES = frozenset({"SUBMITTING", "WAITING_SR", "VERIFYING"})
ATTENTION_JOB_STATUSES = frozenset({"BLOCKED", "FAILED"})
# Producer card representative.  A DETECTING item is more informative than a
# prepared one, but OPENING is the freshest step of all.
CURRENT_PRIORITY = {
    "OPENING": 4,
    "DETECTING": 3,
    "SUBMITTING": 2,
    "MASK_READY": 1,
}

_BATCH_CONTROL_KEYS = (
    "pause_requested", "cancel_requested",
    "pause_requested_at", "cancel_requested_at",
    "last_action", "last_action_at",
)


def _cp(item: dict) -> dict:
    value = item.get("checkpoint")
    return value if isinstance(value, dict) else {}


def _verification(item: dict) -> dict:
    value = item.get("verification")
    return value if isinstance(value, dict) else {}


def is_excluded(item: dict) -> bool:
    """Creation-time exclusion: every QC line of the scene was excluded.

    Runtime skips keep their original ``source_lines`` and must not be counted
    as exclusions.
    """
    return item.get("status") == "SKIPPED" and not _cp(item).get("source_lines")


def _skip_reason(item: dict) -> str:
    return str(_cp(item).get("exclusion_reason") or "")


def verification_ok(item: dict) -> bool:
    return _verification(item).get("ok") is True


def item_job_id(item: dict) -> Any:
    value = item.get("external_job_id")
    if value is not None:
        return value
    return _cp(item).get("job_id")


def count_items(items: Iterable[dict]) -> dict:
    items = list(items)
    counts = {
        "total": len(items),
        "eligible": 0,
        "detected": 0,
        "detection_pending": 0,
        "needs_repair": 0,
        "submitted": 0,
        "verified": 0,
        "verified_demo": 0,
        "verified_production": 0,
        "verified_other": 0,
        "converged": 0,
        "no_issue": 0,
        "failed": 0,
        "blocked": 0,
        "excluded": 0,
        "skipped_runtime": 0,
        "cancelled": 0,
        "capacity_skipped": 0,
        "unknown_jobs": 0,
        "detecting": 0,
        "submitting": 0,
        "waiting_sr": 0,
        "verifying": 0,
        "in_flight_sr": 0,
    }
    for item in items:
        status = item.get("status")
        cp = _cp(item)
        excluded = is_excluded(item)
        if not excluded:
            counts["eligible"] += 1
        if status in DETECTION_PENDING:
            counts["detection_pending"] += 1
        detection = cp.get("detection")
        if isinstance(detection, dict) and detection:
            counts["detected"] += 1
            if detection.get("candidates"):
                counts["needs_repair"] += 1
        if item_job_id(item) is not None:
            counts["submitted"] += 1
        if status == "SUCCEEDED" and verification_ok(item):
            counts["verified"] += 1
            verification = _verification(item)
            scope = verification.get("success_scope")
            provenance = verification.get("provenance")
            if scope == "production_contract":
                counts["verified_production"] += 1
            elif scope == "demo_contract" or provenance == "mock":
                counts["verified_demo"] += 1
            else:
                counts["verified_other"] += 1
        if status in CONVERGED:
            counts["converged"] += 1
        if status == "NO_ISSUE":
            counts["no_issue"] += 1
        elif status == "FAILED":
            counts["failed"] += 1
        elif status == "BLOCKED":
            counts["blocked"] += 1
        elif status == "SKIPPED":
            if excluded:
                counts["excluded"] += 1
            else:
                counts["skipped_runtime"] += 1
                reason = _skip_reason(item)
                if reason == "user_cancelled":
                    counts["cancelled"] += 1
                elif reason == "capacity_deadlock":
                    counts["capacity_skipped"] += 1
        if status == "DETECTING" or status == "OPENING":
            counts["detecting"] += 1
        elif status == "SUBMITTING":
            counts["submitting"] += 1
        elif status == "WAITING_SR":
            counts["waiting_sr"] += 1
        elif status == "VERIFYING":
            counts["verifying"] += 1
        if status in ACTIVE_JOB_STATUSES:
            counts["in_flight_sr"] += 1
        if status == "WAITING_SR":
            sr_status = str(cp.get("sr_status") or "")
            if sr_status == "UNKNOWN" or int(cp.get("unknown_checks") or 0) > 0:
                counts["unknown_jobs"] += 1
    return counts


def progress_from_counts(counts: dict) -> dict:
    known = counts["detection_pending"] == 0
    eligible = counts["eligible"]
    repair = counts["needs_repair"]
    return {
        "detection": {
            "done": counts["detected"], "total": eligible,
            "applicable": eligible > 0, "known": known,
        },
        "submission": {
            "done": counts["submitted"], "total": repair,
            "applicable": repair > 0, "known": known,
        },
        "verification": {
            "done": counts["verified"], "total": repair,
            "applicable": repair > 0, "known": known,
        },
    }


def _item_summary(item: dict) -> dict:
    cp = _cp(item)
    detection = cp.get("detection") if isinstance(cp.get("detection"), dict) else {}
    mask = cp.get("mask") if isinstance(cp.get("mask"), dict) else {}
    target = cp.get("target_image") if isinstance(cp.get("target_image"), dict) else {}
    return {
        "item_id": item.get("item_id"),
        "ordinal": item.get("ordinal"),
        "name": item.get("name"),
        "status": item.get("status"),
        "item_version": item.get("version"),
        "attempt": int(cp.get("attempt") or 0),
        "updated_at": item.get("updated_at"),
        "scene_ref": cp.get("scene_ref"),
        "image_path": target.get("path"),
        "has_mask": bool(mask),
        "overlay_ready": bool(mask.get("overlay")),
        "candidate_count": len(detection.get("candidates") or []),
        "job_id": item_job_id(item),
        "sr_status": cp.get("sr_status"),
        "error": item.get("error"),
    }


def current_item(items: Iterable[dict]) -> dict | None:
    eligible = [
        item for item in items
        if item.get("status") in CURRENT_PRIORITY
    ]
    if not eligible:
        return None
    item = min(
        eligible,
        key=lambda value: (
            -CURRENT_PRIORITY.get(value.get("status"), 0),
            int(value.get("ordinal") or 0),
        ),
    )
    return _item_summary(item)


def _job_summary(item: dict, task: dict | None) -> dict:
    cp = _cp(item)
    task = task or {}
    target = cp.get("target_image") if isinstance(cp.get("target_image"), dict) else {}
    mask = cp.get("mask") if isinstance(cp.get("mask"), dict) else {}
    sr_status = cp.get("sr_status") or task.get("status")
    unknown = (
        str(sr_status or "") == "UNKNOWN"
        or int(cp.get("unknown_checks") or 0) > 0
    )
    return {
        "item_id": item.get("item_id"),
        "ordinal": item.get("ordinal"),
        "name": item.get("name"),
        "status": item.get("status"),
        "item_version": item.get("version"),
        "attempt": int(cp.get("attempt") or 0),
        "job_id": item_job_id(item) or task.get("job_id"),
        "sr_status": sr_status,
        "task_status": task.get("status"),
        "unknown": unknown,
        "submitted_at": cp.get("submitted_at") or task.get("created_at"),
        "updated_at": item.get("updated_at"),
        "error": item.get("error"),
        "image_path": target.get("path"),
        "has_mask": bool(mask),
        "overlay_ready": bool(mask.get("overlay")),
        "verification_ok": (
            verification_ok(item) if item.get("status") == "SUCCEEDED" else None
        ),
    }


def active_jobs(items: Iterable[dict], tasks: dict[str, dict] | None = None) -> list[dict]:
    tasks = tasks or {}
    result: list[dict] = []
    for item in items:
        status = item.get("status")
        cp = _cp(item)
        attention = (
            status in ATTENTION_JOB_STATUSES
            and bool(item_job_id(item) or cp.get("sr_fingerprint") or item.get("error"))
        )
        if status not in ACTIVE_JOB_STATUSES and not attention:
            continue
        result.append(_job_summary(item, tasks.get(cp.get("sr_fingerprint") or "")))
    result.sort(key=lambda value: int(value.get("ordinal") or 0))
    return result


def recent_jobs(items: Iterable[dict], tasks: dict[str, dict] | None = None,
                limit: int = 30) -> list[dict]:
    """Active Jobs first, then attention rows, then recently settled Jobs.

    The Panel shows the live queue by default; this list also lets the user see
    the completion tail without opening fifty Item records.
    """
    tasks = tasks or {}
    rows: list[tuple[tuple[int, float], dict]] = []
    for item in items:
        status = item.get("status")
        cp = _cp(item)
        if item_job_id(item) is None and status not in ATTENTION_JOB_STATUSES:
            continue
        if status in ACTIVE_JOB_STATUSES:
            rank = 0
        elif status in ATTENTION_JOB_STATUSES:
            rank = 1
        else:
            rank = 2
        updated = float(item.get("updated_at") or 0)
        rows.append(((rank, -updated),
                     _job_summary(item, tasks.get(cp.get("sr_fingerprint") or ""))))
    rows.sort(key=lambda value: value[0])
    return [row for _, row in rows[:max(1, int(limit))]]


def monitoring_done(goal: dict) -> bool:
    cp = goal.get("checkpoint") if isinstance(goal.get("checkpoint"), dict) else {}
    if "monitoring_done" in cp:
        return bool(cp.get("monitoring_done"))
    return bool(goal.get("monitor_done"))


def control_revision(goal: dict) -> int:
    cp = goal.get("checkpoint") if isinstance(goal.get("checkpoint"), dict) else {}
    try:
        return int(cp.get("control_revision") or 0)
    except (TypeError, ValueError):
        return 0


def build_snapshot(
    goal: dict,
    items: Iterable[dict],
    tasks: dict[str, dict] | None = None,
    *,
    last_seq: int = 0,
    server_time: float | None = None,
    report_available: bool = False,
    report_updated_at: float | None = None,
) -> dict:
    items = list(items)
    counts = count_items(items)
    cp = goal.get("checkpoint") if isinstance(goal.get("checkpoint"), dict) else {}
    batch_control = cp.get("batch_control") if isinstance(cp.get("batch_control"), dict) else {}
    now = time.time() if server_time is None else float(server_time)
    return {
        "contract_version": CONTRACT_VERSION,
        "goal": {
            "goal_id": goal.get("goal_id"),
            "session_id": goal.get("session_id"),
            "title": goal.get("title"),
            "kind": goal.get("kind"),
            "status": goal.get("status"),
            "phase": goal.get("phase"),
            "mode": cp.get("mode") or "demo",
            "version": goal.get("version"),
            "control_revision": control_revision(goal),
            "monitoring_done": monitoring_done(goal),
            "monitor_state": goal.get("monitor_state"),
            "last_seq": int(last_seq),
            "created_at": goal.get("created_at"),
            "updated_at": goal.get("updated_at"),
            "finished_at": goal.get("finished_at"),
            "batch_control": {
                key: batch_control.get(key)
                for key in _BATCH_CONTROL_KEYS
                if key in batch_control
            },
        },
        "counts": counts,
        "progress": progress_from_counts(counts),
        "producer": {
            "current": current_item(items),
            "detecting_count": counts["detecting"],
            "preparing_count": sum(
                1 for item in items if item.get("status") in PREPARING
            ),
            "prepared_count": sum(
                1 for item in items if item.get("status") == "MASK_READY"
            ),
        },
        "active_jobs": active_jobs(items, tasks),
        "recent_jobs": recent_jobs(items, tasks),
        "quality": quality_summary(items),
        "report": {
            "available": bool(report_available),
            "updated_at": report_updated_at,
        },
        "server_time": now,
    }


def quality_summary(items):
    rows=[]
    for item in items:
        cp=_cp(item)
        if cp.get("quality_review_id"):
            rows.append({"item_id":item["item_id"],"name":item["name"],"ordinal":item["ordinal"],
                "item_version":item["version"],
                "status":item["status"],"review_id":cp["quality_review_id"],"job_id":item_job_id(item),
                "region_count":len(cp.get("detection",{}).get("candidates",[]))})
    return {"waiting_scenes":sum(r["status"]=="AWAITING_REVIEW" for r in rows),
            "rejected_scenes":sum(r["status"]=="REVIEW_REJECTED" for r in rows),"items":rows}
