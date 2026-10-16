"""Terminal-state contract for an SR run: parse the job's own verdict file.

The job writes its verdict itself (`SR_code/variants/verify_sr_run.py`), next to
the SRLOG in the scene's `Debug/` dir, keyed by run number:

    <DatarootLQ>/Debug/_SREXIT_<job_id>.txt
        job_id=42 / sr_exit_code=0 / verdict=0|90 / skip=0|1 / ...

That file, not the child's exit code, is what decides COMPLETED vs FAILED. SR
has silent-failure paths that exit 0 without producing anything (contract §2.3),
so "the process exited 0" says nothing about whether the run did its job.

This module is the **one** place that contract is interpreted. It is
deliberately free of any launcher, scheduler or process knowledge: the executor
(`local_exec`) owns processes, this owns verdicts, and a second copy of the
mapping would drift away from the exit codes the verifier actually writes.

Kept free of launcher references so the mapping can be reasoned about (and
tested) on its own.
"""

from __future__ import annotations


def read_exit_code_file(path):
    """Parse a job verdict file; returns a dict, or None if unusable.

    Format is written by SR_code/variants/verify_sr_run.py:
        job_id=42 / sr_exit_code=0 / verdict=0|90 / skip=0|1 / ...
    Tolerates missing/extra keys and a truncated write by requiring only
    `verdict`; anything unreadable degrades to None (= no verdict).

    Encoding is pinned so this side does not depend on the *writer's* locale:
    the verifier runs in the job env (py3.6) with the environment the launcher
    hands it, and the `reason` field carries Chinese text. `errors="replace"`
    keeps a locale-encoded legacy file parseable too — every field we read is
    ASCII, so only the free-text reason can lose bytes, and it can never be the
    reason a finished job reads back as UNKNOWN.
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


def terminal_from_exit_file(job_id, exit_code_file) -> dict:
    """Terminal state from the job's own verdict file (or UNKNOWN).

    Returns {"job_id", "active", "state", "exit_code"} — the shape every status
    caller in the platform reads. UNKNOWN means the file is missing, unreadable
    or speaks for a different run; it never means "probably fine".
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
    # FAILURE here even though the process exited cleanly.
    return {"job_id": job_id, "active": False, "state": "FAILED",
            "exit_code": f"{rc if rc is not None else -1}:0"}


# Kept for callers/tests written against the old private name.
_terminal_from_exit_file = terminal_from_exit_file
