"""Thin Slurm client: submit / status / cancel via sbatch, squeue, sacct, scancel.

Hosts without Slurm (the dev machine) surface a clean RuntimeError instead of
a crash — the tool wrapper turns it into err() and the agent reports it,
never believing the job ran. The command runner is injectable (run_cmd=) so
tests can feed canned sbatch/squeue/sacct output without a scheduler.

Fake scheduler (api-contract.md §5.2): when `SR_SLURM_FAKE=1` the *scheduler
layer* is replaced by an in-memory job registry driven by monotonic time —
sbatch_submit returns an incrementing job_id, squeue/sacct report
PENDING→RUNNING→COMPLETED as time passes (SR_SLURM_FAKE_T_MS, default 1200ms,
per stage), scancel sets CANCELLED. Everything upstream still runs for real:
submit_run_sr validates paths, builds config.xml/batch scripts and writes the
sr_tasks table exactly as on the array server — only the scheduler is faked.
The env gate mirrors the phase-4 fake-scenes style (scene_search).
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from typing import Callable

_JOB_RE = re.compile(r"Submitted batch job (\d+)", re.IGNORECASE)

# ---- fake scheduler state (SR_SLURM_FAKE=1) ------------------------------
_FAKE_JOBS: dict[int, dict] = {}   # job_id -> {"start": monotonic, "cancelled": bool}
_FAKE_SEQ = [0]


def _fake_enabled() -> bool:
    return os.environ.get("SR_SLURM_FAKE") == "1"


def _fake_stage_ms() -> float:
    """Per-stage dwell (PENDING and RUNNING each last this long)."""
    return float(os.environ.get("SR_SLURM_FAKE_T_MS", "1200"))


def _fake_reset() -> None:
    """Test hook: drop all fake jobs and restart the job_id counter."""
    _FAKE_JOBS.clear()
    _FAKE_SEQ[0] = 0


def _fake_active_state(job_id: int) -> str | None:
    """squeue view of a fake job, or None once it left the queue."""
    rec = _FAKE_JOBS.get(job_id)
    if rec is None or rec["cancelled"]:
        return None
    elapsed = time.monotonic() - rec["start"]
    t = _fake_stage_ms() / 1000.0
    if elapsed < t:
        return "PENDING"
    if elapsed < 2 * t:
        return "RUNNING"
    return None                    # terminal stage reached → left the queue


def slurm_available() -> bool:
    """True if a scheduler is reachable: the real sbatch binary, or the fake
    in-memory scheduler when SR_SLURM_FAKE=1 (submit_run_sr gates on this)."""
    if _fake_enabled():
        return True
    return shutil.which("sbatch") is not None


def _run(cmd, timeout=30):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def _require_slurm():
    if not slurm_available():
        raise RuntimeError("slurm not available on this host (sbatch not found)")


def sbatch_submit(script_path, run_cmd: Callable = _run) -> int:
    """Submit a batch script; returns the job_id (int) or raises RuntimeError."""
    if _fake_enabled():
        _FAKE_SEQ[0] += 1
        jid = _FAKE_SEQ[0]
        _FAKE_JOBS[jid] = {"start": time.monotonic(), "cancelled": False}
        return jid
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
    if _fake_enabled():
        return _fake_active_state(job_id)
    _require_slurm()
    proc = run_cmd(["squeue", "--job", str(job_id), "--noheader", "--format=%T"])
    if proc.returncode != 0:
        raise RuntimeError(f"squeue failed: {proc.stderr.strip()}")
    line = (proc.stdout or "").strip()
    return line.split()[-1] if line else None


def sacct_status(job_id, run_cmd: Callable = _run):
    """(state, exit_code) from sacct, or (None, None) if the job is unknown."""
    if _fake_enabled():
        rec = _FAKE_JOBS.get(job_id)
        if rec is None:
            return None, None
        if rec["cancelled"]:
            return "CANCELLED", "0:0"
        elapsed = time.monotonic() - rec["start"]
        if elapsed >= 2 * (_fake_stage_ms() / 1000.0):
            return "COMPLETED", "0:0"
        return None, None          # still queued/running → no sacct record yet
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
    if _fake_enabled():
        rec = _FAKE_JOBS.get(job_id)
        if rec is None:
            return False
        rec["cancelled"] = True
        return True
    _require_slurm()
    proc = run_cmd(["scancel", str(job_id)])
    return proc.returncode == 0
