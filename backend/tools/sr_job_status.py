"""sr_job_status — poll a Slurm job's state (thin wrapper over services.slurm).

Companion to run_sr: the agent submits a job, then polls it here until
terminal (COMPLETED/FAILED with exit code) before reporting the result.
"""

from __future__ import annotations

from backend.services import slurm
from .contract import err, ok, tool


@tool(
    name="sr_job_status",
    description=(
        "Poll the status of a Slurm job (submit via run_sr). Returns "
        "active/state/exit_code: active jobs show a Slurm state like PENDING "
        "or RUNNING; terminal jobs show COMPLETED/FAILED plus the exit code. "
        "State UNKNOWN means the scheduler has no record of the job."
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
        data = slurm.job_status(job_id)
    except Exception as e:  # noqa: BLE001 — contract boundary
        return err(f"{type(e).__name__}: {e}")
    return ok(data)
