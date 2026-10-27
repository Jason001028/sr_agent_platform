"""sr_job_status — poll an SR job's state (thin wrapper over the job service).

Companion to run_sr: the agent submits a job, then polls it here until terminal
(COMPLETED/FAILED) before reporting the result.
"""

from __future__ import annotations

from backend.services import run_sr, store as store_mod
from .contract import err, ok, tool


@tool(
    name="sr_job_status",
    description=(
        "Poll the status of an SR job (submit via run_sr). Returns "
        "active/state/exit_code: a queued job reports PENDING, a running one "
        "RUNNING; a finished job reports COMPLETED or FAILED plus the exit "
        "code. State UNKNOWN means the platform has no record of the job — "
        "check before resubmitting; CANCELLED means it was stopped."
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
    try:
        task = store_mod.default_store().get_sr_task_by_job(job_id)
        # The service owns Executor dispatch even when no task receipt exists.
        data = run_sr.query_job_status(job_id, task=task)
    except Exception as e:  # noqa: BLE001 — contract boundary
        return err(f"{type(e).__name__}: {e}")
    return ok(data)
