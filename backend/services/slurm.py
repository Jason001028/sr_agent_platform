"""Thin Slurm client: submit / status / cancel via sbatch, squeue, scancel.

Hosts without Slurm (the dev machine) surface a clean RuntimeError instead of
a crash — the tool wrapper turns it into err() and the agent reports it,
never believing the job ran. The command runner is injectable (run_cmd=) so
tests can feed canned sbatch/squeue output without a scheduler.

How a job's state is resolved (slurm-integration.md §一, "C 方案")
----------------------------------------------------------------
The array server runs with `AccountingStorageType=accounting_storage/none`.
`sacct` therefore prints "Slurm accounting storage is disabled" and exits 1 —
there is no output to parse, ever — while `squeue` works fine and only knows
about *live* jobs. So:

    active   → squeue_status()                     (unchanged, real-machine OK)
    terminal → the verdict file the job itself wrote at
               <DatarootLQ>/Debug/_SREXIT_<job_id>.txt
               (SR_code/variants/verify_sr_run.py; path from run_sr.py)

`sacct_status` is kept for the fake scheduler and for any future cluster that
does enable accounting, but the real path no longer calls it: a query that
raises here used to escape into run_sr._resolve_existing and make a same-params
re-submit fail outright, neither reusing nor rerunning (§2.4-2).

`job_status` never raises once a scheduler is present: an unreadable, stale or
missing verdict file is a definite "UNKNOWN", never an exception.

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
    """(state, exit_code) from sacct, or (None, None) if the job is unknown.

    ⚠️ Unusable on the array server — accounting is disabled there, so sacct
    exits 1 with "Slurm accounting storage is disabled". The real path in
    job_status() deliberately does not call this; only the fake scheduler and
    tests do.
    """
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


def read_exit_code_file(path):
    """Parse a job verdict file; returns a dict, or None if unusable.

    Format is written by SR_code/variants/verify_sr_run.py:
        job_id=42 / sr_exit_code=0 / verdict=0|90 / skip=0|1 / ...
    Tolerates missing/extra keys and a truncated write by requiring only
    `verdict`; anything unreadable degrades to None (= no verdict).

    Encoding is pinned so this side does not depend on the *writer's* locale:
    the verifier runs in the job env (py3.6, --export=NONE drops LANG) and the
    `reason` field carries Chinese text. `errors="replace"` keeps a
    locale-encoded legacy file parseable too — every field we read is ASCII, so
    only the free-text reason can lose bytes, and it can never be the reason a
    finished job reads back as UNKNOWN.
    """
    if not path:
        return None
    try:
        with open(str(path), "r", encoding="utf-8", errors="replace") as f:
            data = f.read(8192)
    except Exception:                        # missing, unreadable, is-a-dir…
        return None
    fields = {}
    for line in data.splitlines():
        key, sep, value = line.partition("=")
        if sep:
            fields[key.strip()] = value.strip()
    try:
        verdict = int(fields["verdict"])
    except (KeyError, ValueError):
        return None
    try:
        sr_exit_code = int(fields["sr_exit_code"])
    except (KeyError, ValueError):
        sr_exit_code = None
    return {"verdict": verdict, "skip": fields.get("skip") == "1",
            "sr_exit_code": sr_exit_code, "job_id": fields.get("job_id")}


def job_status(job_id, run_cmd: Callable = _run, exit_code_file=None) -> dict:
    """Resolve a job's status: squeue (active), else its verdict file (terminal).

    Returns {"job_id", "active", "state", "exit_code"}; state UNKNOWN when not
    even the verdict file can speak for the job. `exit_code_file` comes from
    run_sr.exit_code_file_for() — the caller owns the <DatarootLQ> knowledge.

    Contract: never raises once a scheduler exists. The rest of the platform
    (run_sr._resolve_existing, api/platform._task_state) relies on getting a
    definite state back, because an exception there turns a replay into a hard
    error instead of a reuse-or-rerun decision.
    """
    _require_slurm()
    if _fake_enabled():
        # Fake scheduler keeps its own registry: squeue for live stages, sacct
        # for the terminal one. No verdict file is involved.
        try:
            st = squeue_status(job_id, run_cmd=run_cmd)
        except Exception:
            st = None
        if st:
            return {"job_id": job_id, "active": True, "state": st,
                    "exit_code": None}
        state, exit_code = sacct_status(job_id, run_cmd=run_cmd)
        if state:
            return {"job_id": job_id, "active": False, "state": state,
                    "exit_code": exit_code}
        return {"job_id": job_id, "active": None, "state": "UNKNOWN",
                "exit_code": None}

    try:
        st = squeue_status(job_id, run_cmd=run_cmd)
    except Exception:                        # squeue hiccup must not escape
        st = None
    if st:
        return {"job_id": job_id, "active": True, "state": st, "exit_code": None}
    return terminal_from_exit_file(job_id, exit_code_file)


def terminal_from_exit_file(job_id, exit_code_file) -> dict:
    """Terminal state from the job's own verdict file (or UNKNOWN).

    Public because it is the *one* place the verdict contract is interpreted:
    the local executor (backend/services/local_exec.py) has no scheduler to ask
    and must reach the same verdict from the same file. A second copy of this
    mapping would drift away from verify_sr_run.py's exit codes.
    """
    rec = read_exit_code_file(exit_code_file)
    # A recycled job id would otherwise read a stranger's verdict: the file
    # records the id it was written for, so insist that it matches.
    if rec is not None and rec.get("job_id") not in (None, str(job_id)):
        rec = None
    if rec is None:
        return {"job_id": job_id, "active": None, "state": "UNKNOWN",
                "exit_code": None}
    rc = rec["sr_exit_code"]
    if rec["verdict"] == 0:
        return {"job_id": job_id, "active": False, "state": "COMPLETED",
                "exit_code": "0:0"}
    # Contract not satisfied: a run that exited 0 without SRLOG/output is a
    # FAILURE here even though Slurm would call the step COMPLETED.
    return {"job_id": job_id, "active": False, "state": "FAILED",
            "exit_code": f"{rc if rc is not None else -1}:0"}


# Kept for callers/tests written against the old private name.
_terminal_from_exit_file = terminal_from_exit_file


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
