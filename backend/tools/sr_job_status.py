"""sr_job_status — poll an SR job's state (thin wrapper over the task service).

Companion to run_sr: the agent submits a job, then polls it here until terminal
(COMPLETED/FAILED) before reporting the result.

The tool takes a `job_id` (unchanged, so existing prompts and callers keep
working) but does NOT ask the executor about a bare number. A run number alone
says nothing about *which scene's* verdict file to read — that path is derived
from the task's own config.xml — so the task record is located first and the
unified query (`services.run_sr.query_job_status`) is called with it. An id with
no task record comes back as an explicit unknown-task result: nothing is created
and no process is touched.
"""

from __future__ import annotations

from backend.services import run_sr as svc
from backend.services import store as store_mod
from .contract import err, ok, tool


def _task_for_job_id(job_id: int) -> dict | None:
    """The sr_task row that owns this run number, or None.

    Scans rather than indexing: `job_id` is a per-run number, not a key of the
    table (the key is the param fingerprint, so a re-submitted task keeps its row
    and takes a new number). Rows are few — the queue page lists all of them.
    """
    try:
        store = store_mod.default_store()
        tasks = store.list_sr_tasks()
    except Exception:  # noqa: BLE001 — no store → indistinguishable from no row
        return None
    return next((t for t in tasks if t.get("job_id") == job_id), None)


@tool(
    name="sr_job_status",
    description=(
        "Poll the status of an SR job (submit via run_sr). Returns "
        "active/state/exit_code: a job still queued behind the running one "
        "reports PENDING, a running one RUNNING; a finished job reports "
        "COMPLETED or FAILED. State UNKNOWN means the platform cannot tell "
        "whether that job is still running — check before resubmitting; "
        "CANCELLED means it was stopped."
    ),
    params_schema={
        "type": "object",
        "properties": {
            "job_id": {
                "type": "integer",
                "description": "Job id returned by run_sr.",
            },
        },
        "required": ["job_id"],
    },
)
def run_sr_job_status(**params) -> dict:
    try:
        job_id = int(params["job_id"])
    except (KeyError, TypeError, ValueError) as e:
        return err(f"bad params: {e}")
    task = _task_for_job_id(job_id)
    if task is None:
        return err(f"unknown job_id {job_id}: no task record carries it "
                   "(it was never submitted by this platform, or the database "
                   "was replaced)")
    try:
        data = svc.query_job_status(job_id, task=task)
    except Exception as e:  # noqa: BLE001 — contract boundary
        return err(f"{type(e).__name__}: {e}")
    return ok(dict(data, task_id=task["task_id"], lq_path=(
        task.get("params") or {}).get("lq_path")))
