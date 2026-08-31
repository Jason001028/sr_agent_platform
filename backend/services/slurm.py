"""Thin Slurm client: submit / status / cancel via sbatch, squeue, sacct, scancel.

Hosts without Slurm (the dev machine) surface a clean RuntimeError instead of
a crash — the tool wrapper turns it into err() and the agent reports it,
never believing the job ran. The command runner is injectable (run_cmd=) so
tests can feed canned sbatch/squeue/sacct output without a scheduler.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from typing import Callable

_JOB_RE = re.compile(r"Submitted batch job (\d+)", re.IGNORECASE)


def slurm_available() -> bool:
    """True if the sbatch binary is on PATH (the scheduler host)."""
    return shutil.which("sbatch") is not None


def _run(cmd, timeout=30):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def _require_slurm():
    if not slurm_available():
        raise RuntimeError("slurm not available on this host (sbatch not found)")


def sbatch_submit(script_path, run_cmd: Callable = _run) -> int:
    """Submit a batch script; returns the job_id (int) or raises RuntimeError."""
    _require_slurm()
    proc = run_cmd(["sbatch", str(script_path)])
    if proc.returncode != 0:
        raise RuntimeError(
            f"sbatch failed rc={proc.returncode}: "
            f"{(proc.stderr or proc.stdout).strip()}")
    m = _JOB_RE.search(proc.stdout or "")
    if not m:
        raise RuntimeError(f"unrecognized sbatch output: {proc.stdout.strip()}")
    return int(m.group(1))


def squeue_status(job_id, run_cmd: Callable = _run):
    """Slurm state from squeue, or None once the job left the queue."""
    _require_slurm()
    proc = run_cmd(["squeue", "--job", str(job_id), "--noheader", "--format=%T"])
    if proc.returncode != 0:
        raise RuntimeError(f"squeue failed: {proc.stderr.strip()}")
    line = (proc.stdout or "").strip()
    return line.split()[-1] if line else None


def sacct_status(job_id, run_cmd: Callable = _run):
    """(state, exit_code) from sacct, or (None, None) if the job is unknown."""
    _require_slurm()
    proc = run_cmd(["sacct", "--job", str(job_id), "--noheader",
                    "--format=State%20,ExitCode%10", "-n"])
    if proc.returncode != 0:
        raise RuntimeError(f"sacct failed: {proc.stderr.strip()}")
    for line in (proc.stdout or "").strip().splitlines():
        parts = line.split()
        if len(parts) >= 2:
            return parts[0], parts[1]
    return None, None


def job_status(job_id, run_cmd: Callable = _run) -> dict:
    """Resolve a job's status: squeue (active) first, then sacct (terminal).

    Returns {"job_id", "active", "state", "exit_code"}; state UNKNOWN when the
    scheduler has no record of the job.
    """
    _require_slurm()
    try:
        st = squeue_status(job_id, run_cmd=run_cmd)
    except RuntimeError:
        st = None
    if st:
        return {"job_id": job_id, "active": True, "state": st, "exit_code": None}
    state, exit_code = sacct_status(job_id, run_cmd=run_cmd)
    if state:
        return {"job_id": job_id, "active": False, "state": state,
                "exit_code": exit_code}
    return {"job_id": job_id, "active": None, "state": "UNKNOWN",
            "exit_code": None}


def cancel(job_id, run_cmd: Callable = _run) -> bool:
    """Cancel a job via scancel; returns True on success."""
    _require_slurm()
    proc = run_cmd(["scancel", str(job_id)])
    return proc.returncode == 0
