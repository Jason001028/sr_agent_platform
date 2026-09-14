"""Local executor: run the generated batch script as a plain child process.

The minimal prototype runs SR **on this host** with the SR production conda
interpreter (``SR_PYTHON``) instead of handing it to Slurm — see
docs/planning/sr-minimal-prototype-plan.md §1.2/§1.3. What changes is only the
launcher: the script Slurm would have received (`run_sr.build_batch_script`) is
valid bash on its own (`#SBATCH` lines are comments to bash), so `bash <script>`
reuses the whole chain unchanged — config.xml assembly, the audit preamble, the
contract verifier, and the exit-code file that decides the terminal state.

Interface mirrors ``backend/services/slurm.py`` so ``run_sr`` can pick either
one by ``SR_EXECUTOR``:

    available() -> bool
    submit(script_path, env=None, job_id=None) -> int
    status(job_id, exit_code_file=None) -> dict
    cancel(job_id) -> bool

Three deliberate properties:

* **Single slot, serial.** One job at a time, enforced by a module-level
  ``threading.Lock`` the runner thread must hold for the whole life of the
  child. A second submit queues (status PENDING) instead of starting a second
  SR process, because the whole point of this prototype is one GPU and one
  scene directory.
* **job_id is persisted before it is used.** ``.local_job_seq`` in
  ``SR_SLURM_WORK_DIR`` (read → +1 → write under the same lock) keeps ids
  unique across an ``sr-api`` restart. Uniqueness matters because the verdict
  file is named after the job id: a recycled id would let one run read another
  run's verdict.
* **The terminal state is still the verdict file**, never the process exit
  code. SR has silent-failure paths that ``exit(0)`` without producing anything
  (contract §2.3), so ``status()`` delegates to
  ``slurm.terminal_from_exit_file`` — one mapping, shared with the Slurm path,
  tied to ``SR_code/variants/verify_sr_run.py``.

Not a scheduler: no queue limits, no priorities, no cross-host anything. If a
second worker host ever needs this, that is the moment to go back to Slurm.
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import threading
from pathlib import Path

from backend.config import sr_runtime

from . import slurm

#: Job-id sequence file, under SR_SLURM_WORK_DIR. Persisted so ids never repeat
#: after a restart (a repeat would alias two runs onto one verdict file).
SEQ_FILE = ".local_job_seq"

#: Protects _JOBS and the sequence file. Held only for dict/IO work, never
#: while a child process runs.
_LOCK = threading.Lock()

#: The single execution slot. A runner thread takes it before starting the
#: child and releases it after wait() — so pending jobs sit here instead of
#: competing for the GPU.
_SLOT = threading.Lock()

#: job_id -> {"script", "log", "proc" (None until started), "cancelled",
#: "done" (runner thread finished without leaving a live child)}.
#: Empty after a restart, which is fine: status() falls back to the verdict
#: file, which is on disk.
_JOBS: dict[int, dict] = {}


def _wsl_stub(path: str) -> bool:
    """True for ``%SystemRoot%\\System32\\bash.exe`` — the WSL launcher, not bash."""
    root = os.environ.get("SystemRoot") or os.environ.get("windir") or r"C:\Windows"
    system32 = os.path.normcase(os.path.join(root, "System32"))
    return os.path.normcase(os.path.dirname(os.path.abspath(path))) == system32


def _bash_path() -> str | None:
    """Absolute path of a bash that can actually run a script, or None.

    Windows ships a ``bash.exe`` in System32 that is **not** bash: it is the WSL
    launcher, which exits non-zero ("no installed distributions") unless a
    distro happens to be installed. ``CreateProcess`` searches System32 *before*
    PATH — unlike ``shutil.which`` — so a bare ``["bash", …]`` picks that stub
    over the real Git/MSYS bash sitting on PATH. The dev machine has both; the
    SR host (CentOS7, ``/bin/bash``) has neither problem. Hence: scan PATH
    ourselves, skip the stub, hand Popen an absolute path.
    """
    if os.name == "posix":
        return shutil.which("bash") or "/bin/bash"
    for entry in (os.environ.get("PATH") or "").split(os.pathsep):
        if not entry:
            continue
        candidate = os.path.join(entry.strip('"'), "bash.exe")
        if os.path.isfile(candidate) and not _wsl_stub(candidate):
            return candidate
    return None


def available() -> bool:
    """True when a usable bash exists — local execution needs no scheduler.

    Bash is the one real dependency of this executor, so it is what the gate
    checks: without it ``submit`` could only start something that dies instantly
    and leaves no verdict file (reported as UNKNOWN, which reads like a silent
    SR failure rather than a missing interpreter). Telling the caller *before*
    the job is created keeps those two apart.

    Same shape as ``slurm.slurm_available`` so run_sr's availability gate reads
    either executor identically.
    """
    return _bash_path() is not None


def _reset() -> None:
    """Test hook: forget every job (the sequence file is left alone)."""
    with _LOCK:
        _JOBS.clear()


def reserve_job_id(work_dir=None) -> int:
    """Allocate the next job id and persist the counter.

    Separate from ``submit`` because the id has to exist *before* the script is
    written: the verifier is called with ``--job-id <id>`` so it names its
    verdict file after this job, and ``run_sr.exit_code_file_for`` re-derives
    that same name from the same id. Reserving first is what keeps the two
    sides agreeing.
    """
    work = Path(work_dir or sr_runtime().slurm_work_dir)
    with _LOCK:
        work.mkdir(parents=True, exist_ok=True)
        seq_file = work / SEQ_FILE
        try:
            n = int(seq_file.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            n = 0
        n += 1
        seq_file.write_text(f"{n}\n", encoding="utf-8")
        return n


def _child_env(extra: dict | None = None) -> dict:
    """Environment for the SR child — the shell-activation equivalent.

    The API process runs in ``/opt/sr-venv`` (py3.9) while the job needs the SR
    production env (py3.6 + torch + GDAL). Those two must not mix:

    1. Drop ``VIRTUAL_ENV`` / ``PYTHONHOME`` / ``PYTHONPATH`` — inherited, they
       would make the conda interpreter import the API venv's packages.
    2. Prepend the interpreter's own directory to ``PATH`` (what activating the
       conda env does).
    3. Set ``CUDA_VISIBLE_DEVICES`` from ``SR_LOCAL_GPU`` — with no scheduler
       there is nothing else to pick the card, and the deployment variant reads
       the variable instead of hardcoding "0" (E1/E2).
    4. Mark ``SR_EXECUTOR=local`` so the script's audit preamble records which
       launcher produced the log.

    Everything else is inherited as-is: SR's torch/GDAL are linked against
    system libraries, so ``LD_LIBRARY_PATH`` and friends must survive.
    """
    rt = sr_runtime()
    env = dict(os.environ)
    for name in ("VIRTUAL_ENV", "PYTHONHOME", "PYTHONPATH"):
        env.pop(name, None)
    py_dir = os.path.dirname(str(rt.python or ""))
    if py_dir:
        env["PATH"] = py_dir + os.pathsep + env.get("PATH", "")
    env["CUDA_VISIBLE_DEVICES"] = str(rt.local_gpu)
    env["SR_EXECUTOR"] = "local"
    if extra:
        env.update({str(k): str(v) for k, v in extra.items()})
    return env


def _terminate(proc) -> None:
    """Signal the child's whole process *tree*, not just the bash wrapper.

    The script bash runs is only the wrapper: what must actually die is the
    python process it starts (which is what holds the GPU). POSIX gets this from
    the process group — the child is started with ``start_new_session=True``, so
    it leads its own group and ``killpg`` reaches every descendant. Windows has
    no process groups to signal, so it needs ``taskkill /T`` for the same effect;
    a bare ``terminate()`` there would orphan the python child, which then keeps
    running (and holding the log file) while the slot already reports free.
    """
    try:
        if os.name == "posix":
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        else:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                           capture_output=True, check=False)
    except Exception:  # noqa: BLE001 — already gone, or not ours to signal
        try:
            proc.terminate()
        except Exception:  # noqa: BLE001
            pass
    try:
        proc.wait(timeout=10)
    except Exception:  # noqa: BLE001 — a wedged child must not hang the slot
        try:
            proc.kill()
        except Exception:  # noqa: BLE001
            pass


def submit(script_path, env=None, job_id=None) -> int:
    """Queue the script and return its job id.

    ``job_id`` may be pre-allocated with :func:`reserve_job_id` (run_sr does
    this, because the id is baked into the script text before it runs); when
    omitted a fresh one is drawn here.

    Returns immediately — the child starts on a daemon thread that first waits
    for the single slot, so a queued job reports PENDING until it really runs.
    """
    script = str(script_path)
    jid = int(job_id) if job_id is not None else reserve_job_id()
    log_path = Path(sr_runtime().slurm_work_dir) / f"{Path(script).stem}.{jid}.out"
    with _LOCK:
        _JOBS[jid] = {"script": script, "log": str(log_path), "proc": None,
                      "cancelled": False, "done": False}
    threading.Thread(target=_run_job, args=(jid, script, log_path, env),
                     name=f"sr-local-{jid}", daemon=True).start()
    return jid


def _run_job(jid: int, script: str, log_path: Path, env) -> None:
    """Slot-holding runner thread: start the child, wait for it, free the slot."""
    with _SLOT:                                   # single slot: one job at a time
        with _LOCK:
            rec = _JOBS.get(jid)
            if rec is None or rec["cancelled"]:
                return                                # cancelled while queued
        log_fp = None
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_fp = open(log_path, "wb")
            # Absolute path, not the bare name: see _bash_path (Windows would
            # otherwise launch the System32 WSL stub).
            bash = _bash_path()
            if bash is None:
                raise RuntimeError("no bash on PATH — cannot run the batch script")
            # cwd: the bundle when it exists (same place the batch script cds
            # into), else the API process's own cwd. Never a hard failure — on
            # the dev machine there is no bundle dir to cd to.
            bundle = sr_runtime().bundle_dir
            cwd = bundle if bundle and os.path.isdir(bundle) else None
            proc = subprocess.Popen(
                [bash, script], stdout=log_fp, stderr=subprocess.STDOUT,
                env=_child_env(env), cwd=cwd,
                start_new_session=(os.name == "posix"))
        except Exception as e:  # noqa: BLE001 — unlaunchable: no verdict → UNKNOWN
            if log_fp is not None:
                try:
                    log_fp.write(f"local_exec: launch failed: "
                                 f"{type(e).__name__}: {e}\n".encode("utf-8"))
                except OSError:
                    pass
                log_fp.close()
            with _LOCK:
                if _JOBS.get(jid) is not None:
                    _JOBS[jid]["done"] = True
            return
        with _LOCK:
            rec = _JOBS.get(jid)
            if rec is not None:
                rec["proc"] = proc
                cancelled = rec["cancelled"]
            else:
                cancelled = True
        if cancelled:                             # cancelled mid-launch
            _terminate(proc)
        try:
            proc.wait()
        finally:
            log_fp.close()
            with _LOCK:
                if _JOBS.get(jid) is not None:
                    _JOBS[jid]["done"] = True


def status(job_id, exit_code_file=None) -> dict:
    """{"job_id", "active", "state", "exit_code"} — same shape as slurm's.

    PENDING while still queued behind the slot, RUNNING while the child lives;
    once it is gone, the verdict file decides COMPLETED / FAILED. A cancelled
    job with no verdict of its own reports CANCELLED (the user's act is the
    fact); a verdict file still wins over it, because it is the job's own
    recorded outcome.

    With no in-memory record (sr-api restarted) the verdict file alone answers
    — as long as the caller can name it. Unreadable → UNKNOWN, never a guess.
    """
    with _LOCK:
        rec = _JOBS.get(job_id)
    if rec is not None:
        proc = rec["proc"]
        if proc is not None and proc.poll() is None:
            return {"job_id": job_id, "active": True, "state": "RUNNING",
                    "exit_code": None}
        if proc is None and not rec["done"] and not rec["cancelled"]:
            return {"job_id": job_id, "active": True, "state": "PENDING",
                    "exit_code": None}
    st = slurm.terminal_from_exit_file(job_id, exit_code_file)
    if st["state"] == "UNKNOWN" and rec is not None and rec["cancelled"]:
        return {"job_id": job_id, "active": False, "state": "CANCELLED",
                "exit_code": None}
    return st


def cancel(job_id) -> bool:
    """Cancel a queued or running job. False when it is not ours to cancel."""
    with _LOCK:
        rec = _JOBS.get(job_id)
        if rec is None:
            return False
        rec["cancelled"] = True
        proc = rec["proc"]
    if proc is not None and proc.poll() is None:
        _terminate(proc)          # the runner thread sees poll() != None and frees the slot
    return True
