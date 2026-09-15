"""run_sr — submit a super-resolution job to Slurm (thin wrapper).

Business logic in backend/services/run_sr.py: high-level params → config.xml
+ batch script → sbatch (idempotent via the sr_tasks table). Returns a job_id;
the agent polls it with the sr_job_status tool. Async by design (the job runs
on the CentOS7 array server).

Placeholder / non-absolute paths are rejected here as err() — search_scenes
fake results carry `<fake>/...` paths that must never reach Slurm. The err is
fed back to the model (contract boundary), so it can change the params and
retry; it never raises.
"""

from __future__ import annotations

import os

from backend.services import run_sr as svc
from backend.services import store as store_mod
from .contract import err, ok, tool

_DESC = (
    "Submit a remote-sensing super-resolution (SR) job to Slurm. Assembles an "
    "SFSR config from high-level params (L1 input dir, optional mask, SR "
    "scale, output suffix) and returns a job_id — NOT the result. Poll the "
    "result with sr_job_status. Requires the Slurm scheduler host (sbatch)."
)


def _bad_path(path: str) -> str | None:
    """Why a path cannot be handed to run_sr, or None if it is acceptable.

    Rejects search_scenes fake rows (`<fake>/...`) and relative paths — Slurm
    jobs run on the array server where only absolute real-array paths make
    sense. This is validation returning an err (the loop self-heals), not an
    exception.
    """
    if not path:
        return None
    if "<fake>" in path:
        return ("is a fake/placeholder path from search_scenes fake results — "
                "pick a real scene path on the array")
    if not os.path.isabs(path):
        return "must be an absolute path"
    return None


@tool(
    name="run_sr",
    description=_DESC,
    params_schema={
        "type": "object",
        "properties": {
            "lq_path": {
                "type": "string",
                "description": "L1 PAN image directory (DatarootLQ), e.g. a scene dir on the array.",
            },
            "mask_path": {
                "type": "string",
                "description": "Optional ROI mask TIFF; omitted → full-image SR.",
            },
            "sr_scale": {
                "type": "integer", "minimum": 1, "default": 2,
                "description": "Super-resolution scale factor.",
            },
            "suffix": {
                "type": "string", "default": "",
                "description": "Output filename suffix.",
            },
            "gpu": {
                "type": "integer", "minimum": 0, "default": 0,
                "description": "GPU id requested (GPUIDS).",
            },
            "cloud_limit": {
                "type": "integer", "minimum": 0, "maximum": 100, "default": 80,
                "description": "Max cloud percent; above this the job skips the scene.",
            },
            "delete_ori": {
                "type": "boolean", "default": False,
                "description": "Disabled in this prototype: passing true is "
                               "rejected (SR would delete or overwrite the "
                               "original in place, with no backup).",
            },
            "grid_align": {
                "type": "boolean", "default": True,
                "description": "Enable MTA-grid offset alignment (default on).",
            },
            "options_yml": {
                "type": "string",
                "description": "Optional path to the SFSR options .yml (network config).",
            },
        },
        "required": ["lq_path"],
    },
)
def run_run_sr(**params) -> dict:
    try:
        lq_path = str(params["lq_path"]).strip()
        sr_scale = int(params.get("sr_scale", 2))
        gpu = int(params.get("gpu", 0))
        cloud_limit = int(params.get("cloud_limit", 80))
        suffix = str(params.get("suffix") or "")
    except (KeyError, TypeError, ValueError) as e:
        return err(f"bad params: {e}")

    if not lq_path:
        return err("lq_path is required")
    bad = _bad_path(lq_path)
    if bad:
        return err(f"lq_path {bad}")
    mask_path = params.get("mask_path")
    if mask_path:
        bad = _bad_path(str(mask_path).strip())
        if bad:
            return err(f"mask_path {bad}")
    if sr_scale < 1:
        return err("sr_scale must be >= 1")
    if gpu < 0:
        return err("gpu must be >= 0")
    if not (0 <= cloud_limit <= 100):
        return err("cloud_limit must be in 0..100")

    params = {
        "lq_path": lq_path, "mask_path": params.get("mask_path"),
        "sr_scale": sr_scale, "suffix": suffix, "gpu": gpu,
        "cloud_limit": cloud_limit,
        "delete_ori": bool(params.get("delete_ori", False)),
        "grid_align": bool(params.get("grid_align", True)),
        "options_yml": params.get("options_yml"),
    }
    try:
        data = svc.submit_run_sr(params, store=store_mod.default_store())
    except Exception as e:  # noqa: BLE001 — contract boundary
        return err(f"{type(e).__name__}: {e}")
    return ok(data)
