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
  run's verdict. When that file is missing or unreadable the counter falls back
  to the clock rather than restarting at 1 — the verdict files live in the scene
  directories and outlive ``SR_SLURM_WORK_DIR`` (``/tmp`` by default), so
  restarting at 1 *is* the recycled-id case.
* **The terminal state is still the verdict file**, never the process exit
  code. SR has silent-failure paths that ``exit(0)`` without producing anything
  (contract §2.3), so ``status()`` delegates to
  ``slurm.terminal_from_exit_file`` — one mapping, shared with the Slurm path,
  tied to ``SR_code/variants/verify_sr_run.py``.
* **The queue survives a restart.** ``.local_jobs.json`` is written before a
  child starts: queued records are dispatched again, live PIDs are adopted by
  a watcher that keeps the single slot, and an interrupted launch is never
  blindly replayed.
* **Production preflight is cached.** ``preflight()`` checks bash, the work
  directory, the SR interpreter's imports, the algorithm entry, the model
  weights and the GPU once per env snapshot; plan-card polling does not
  re-import torch or call ``nvidia-smi``.
* **Timeout/cancel kill the tree.** ``SR_LOCAL_JOB_TIMEOUT_SEC`` and
  ``cancel()`` use the same process-group termination as the Slurm launcher.

Not a scheduler: no queue limits, no priorities, no cross-host anything. If a
second worker host ever needs this, that is the moment to go back to Slurm.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import signal
import subprocess
import threading
import time
from pathlib import Path

from backend.config import SR_DEFAULT_OPTIONS_YML, sr_runtime

from . import slurm

log = logging.getLogger(__name__)

#: Job-id sequence file, under the work directory. Persisted so ids never
#: repeat after a restart (a repeat would alias two runs onto one verdict file).
SEQ_FILE = ".local_job_seq"

#: Durable queue/process table. Written before a child is started, so a
#: service restart can resume queued jobs and adopt live children instead of
#: reporting them UNKNOWN.
STATE_FILE = ".local_jobs.json"
STATE_VERSION = 1
#: Terminal records are kept long enough to answer post-restart status reads.
RETENTION_SEC = 7 * 24 * 3600.0

#: Protects _JOBS, the sequence file and the state file. Held only for
#: dict/IO work, never while a child process runs.
_LOCK = threading.Lock()

#: The single execution slot. A runner thread takes it before starting the
#: child and releases it after wait() — so pending jobs sit here instead of
#: competing for the GPU. A recovered live child is represented by a watcher
#: thread that holds this same slot until the process exits.
_SLOT = threading.Lock()

#: Cleared while an interrupted launch (``starting``/``quarantine``) cannot be
#: reconciled. Queued runners wait here so an unverified child can never share
#: the GPU with the next scene; ``resolve_quarantine`` (or a verdict file seen
#: by status) sets it again.
_DISPATCH_BLOCKED = threading.Event()
_DISPATCH_BLOCKED.set()

#: job_id -> {"job_id", "script", "log", "pid", "state", "proc" (None until
#: started in this process), "cancelled", "done", "timed_out", "_dispatched"}.
#: Loaded from STATE_FILE on first use after a restart.
_JOBS: dict[int, dict] = {}
_STATE_LOADED = False
_LOADED_WORK_DIR: str | None = None
#: Incremented by ``_reset``. A runner that was already queued in the old
#: process must not start after a simulated restart (threads die in reality).
_EPOCH = 0


def activity_snapshot():
    """Memory-only admission facts; never reconcile jobs or touch source files."""
    import hashlib
    with _LOCK:
        rows = sorted((str(jid),str(rec.get('state')),bool(rec.get('done')))
                      for jid,rec in _JOBS.items())
        uncertain = not _STATE_LOADED or not _DISPATCH_BLOCKED.is_set()
        pending = [rec for rec in _JOBS.values() if not rec.get('done')]
        return {'active':len(pending), 'quarantine':any(rec.get('state') in ('starting','quarantine') for rec in pending),
                'unknown':uncertain, 'generation':hashlib.sha256(repr((_EPOCH,rows)).encode()).hexdigest()}

#: Cached local production preflight. Keyed by the env values that affect it,
#: so the plan card can poll status without re-importing torch or calling
#: nvidia-smi on every request.
_PREFLIGHT: dict[tuple, dict] = {}
_PREFLIGHT_LOCK = threading.Lock()
PREFLIGHT_IMPORTS_DEFAULT = "torch,osgeo,numpy"


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
    """Test/restart hook: forget in-memory jobs.

    The sequence file and the durable queue stay on disk; the next
    ``recover()`` or status read reloads them, which models an ``sr-api``
    restart rather than a data wipe.
    """
    global _STATE_LOADED, _LOADED_WORK_DIR, _EPOCH
    with _LOCK:
        _JOBS.clear()
        _STATE_LOADED = False
        _LOADED_WORK_DIR = None
        _EPOCH += 1
        _DISPATCH_BLOCKED.set()


def _state_path(work_dir=None) -> Path:
    return Path(work_dir or sr_runtime().work_dir) / STATE_FILE


def _record_view(rec: dict) -> dict:
    return {key: rec.get(key) for key in (
        "job_id", "script", "log", "pid", "pid_start", "state", "timed_out",
        "cancel_requested", "created_at", "started_at", "updated_at")}


def _persist_locked() -> bool:
    """Write the durable queue atomically. Caller holds ``_LOCK``.

    Returns False when the write failed. A caller that is about to start a
    child must treat that as fatal: a stale ``queued`` record plus a live child
    is exactly the double-execution window a later restart would reopen.
    """
    path = _state_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(
            {"version": STATE_VERSION,
             "jobs": [_record_view(rec) for _, rec in sorted(_JOBS.items())]},
            ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)
        return True
    except OSError as exc:
        log.warning("local_exec: cannot persist queue at %s: %s", path, exc)
        return False


def _ensure_loaded() -> None:
    """Load the durable queue once per process/work-dir (or after ``_reset``)."""
    global _STATE_LOADED, _LOADED_WORK_DIR, _EPOCH
    work_dir = sr_runtime().work_dir
    with _LOCK:
        if _STATE_LOADED and _LOADED_WORK_DIR == work_dir:
            return
        if _STATE_LOADED:
            # A different work dir means a different queue; never reuse
            # in-memory job ids across it.
            _JOBS.clear()
            _EPOCH += 1
        _STATE_LOADED = True
        _LOADED_WORK_DIR = work_dir
        path = _state_path(work_dir)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(data, dict) or data.get("version") != STATE_VERSION:
            return
        for row in data.get("jobs") or []:
            try:
                jid = int(row["job_id"])
                state = str(row.get("state") or "queued")
                now = time.time()
                _JOBS[jid] = {
                    "job_id": jid,
                    "script": str(row.get("script") or ""),
                    "log": str(row.get("log") or ""),
                    "pid": int(row["pid"]) if row.get("pid") else None,
                    "pid_start": (str(row["pid_start"])
                                  if row.get("pid_start") else None),
                    "state": state,
                    "proc": None,
                    "cancelled": (state == "cancelled"
                                  or bool(row.get("cancel_requested"))),
                    "cancel_requested": bool(row.get("cancel_requested")),
                    "done": state == "exited",
                    "timed_out": bool(row.get("timed_out")),
                    "created_at": float(row.get("created_at") or now),
                    "started_at": (float(row["started_at"])
                                   if row.get("started_at") else None),
                    "updated_at": float(row.get("updated_at") or now),
                    "_dispatched": False,
                }
            except (KeyError, TypeError, ValueError):
                continue
        if any(rec.get("state") in ("starting", "quarantine")
               for rec in _JOBS.values()):
            # Do not let a new submit run before recover() reconciles the
            # interrupted launch window.
            _DISPATCH_BLOCKED.clear()


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
            # Nothing to continue from: a fresh work dir, or the file was lost or
            # corrupted (SR_SLURM_WORK_DIR defaults to /tmp, which a reboot
            # clears). Restarting at 1 would recycle ids whose verdict files are
            # still lying in the scene directories — those outlive the work dir,
            # and a recycled id would read a stranger's verdict and report a job
            # that never ran as COMPLETED. Fall back to the clock, which cannot
            # collide with an id already handed out.
            n = int(time.time())
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


def _pid_alive(pid) -> bool:
    """Best-effort liveness check for an adopted child after a restart."""
    if not pid:
        return False
    pid = int(pid)
    if os.name == "posix":
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True                    # exists but owned by someone else
        except OSError:
            return False
        return True
    try:                                   # Windows: no signal-0 equivalent
        import ctypes
        kernel32 = ctypes.windll.kernel32
        kernel32.OpenProcess.restype = ctypes.c_void_p
        kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int,
                                         ctypes.c_uint32]
        kernel32.GetExitCodeProcess.argtypes = [
            ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = kernel32.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFO
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return False
            return code.value == 259       # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    except Exception:                      # noqa: BLE001 — liveness is best effort
        return False


def _process_identity(pid) -> dict | None:
    """Identify a process beyond its PID.

    PID reuse is the reason a bare ``kill(pid, 0)`` is not enough: the platform
    must not adopt, cancel or time out a stranger. POSIX uses ``/proc`` start
    time plus the command line; Windows uses the process creation time.
    """
    if not pid:
        return None
    pid = int(pid)
    if os.name == "posix":
        try:
            stat = Path(f"/proc/{pid}/stat").read_text(
                encoding="utf-8", errors="replace")
            # Fields after the parenthesised comm: state is field 3, so
            # starttime (field 22) is index 19 of the remainder.
            start = stat[stat.rfind(")") + 2:].split()[19]
            cmdline = Path(f"/proc/{pid}/cmdline").read_bytes().replace(
                b"\x00", b" ").decode("utf-8", "replace")
            return {"start": start, "cmdline": cmdline}
        except (OSError, IndexError, ValueError):
            return None
    try:
        import ctypes

        class FileTime(ctypes.Structure):
            _fields_ = [("low", ctypes.c_uint32), ("high", ctypes.c_uint32)]

        kernel32 = ctypes.windll.kernel32
        kernel32.OpenProcess.restype = ctypes.c_void_p
        kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int,
                                         ctypes.c_uint32]
        kernel32.GetProcessTimes.argtypes = [
            ctypes.c_void_p, ctypes.POINTER(FileTime), ctypes.POINTER(FileTime),
            ctypes.POINTER(FileTime), ctypes.POINTER(FileTime)]
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return None
        try:
            created, exited, kernel, user = (FileTime(), FileTime(),
                                             FileTime(), FileTime())
            if not kernel32.GetProcessTimes(
                    handle, ctypes.byref(created), ctypes.byref(exited),
                    ctypes.byref(kernel), ctypes.byref(user)):
                return None
            start = (int(created.high) << 32) | int(created.low)
            return {"start": str(start), "cmdline": None}
        finally:
            kernel32.CloseHandle(handle)
    except Exception:                      # noqa: BLE001 — identity is best effort
        return None


def _save_pid_identity(pid) -> str | None:
    ident = _process_identity(pid)
    return str(ident["start"]) if ident else None


def _identity_state(rec: dict) -> str:
    """``dead`` | ``mismatch`` | ``unknown`` | ``match`` for a recorded PID.

    ``mismatch`` means the PID now belongs to another process (never signal
    it). ``unknown`` means the PID exists but its identity cannot be read; the
    safe response is to keep the slot blocked, not to guess.
    """
    pid = rec.get("pid")
    if not pid or not _pid_alive(pid):
        return "dead"
    ident = _process_identity(pid)
    saved = rec.get("pid_start")
    if not ident or not saved:
        return "unknown"
    if str(ident["start"]) != str(saved):
        return "mismatch"              # PID reused
    cmdline = ident.get("cmdline")
    script = rec.get("script")
    if cmdline is not None and script:
        if Path(str(script)).name not in cmdline:
            return "mismatch"
    return "match"


def _identity_matches(rec: dict) -> bool:
    return _identity_state(rec) == "match"


def _terminate_pid(pid) -> None:
    """Kill an adopted child's whole process group (no Popen handle needed)."""
    if not pid:
        return
    pid = int(pid)
    try:
        if os.name == "posix":
            os.killpg(os.getpgid(pid), signal.SIGTERM)
        else:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                           capture_output=True, check=False)
    except Exception:                      # noqa: BLE001 — already gone
        try:
            os.kill(pid, signal.SIGTERM)
        except Exception:                  # noqa: BLE001
            pass


def _kill_pid(pid) -> None:
    """Hard-kill an adopted child's process group after SIGTERM was ignored."""
    if not pid:
        return
    pid = int(pid)
    try:
        if os.name == "posix":
            os.killpg(os.getpgid(pid), signal.SIGKILL)
        else:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                           capture_output=True, check=False)
    except Exception:                      # noqa: BLE001 — already gone
        try:
            os.kill(pid, signal.SIGKILL)
        except Exception:                  # noqa: BLE001
            pass


def _job_timeout_sec() -> float:
    """Optional wall-clock cap for a local child (0/absent = no timeout)."""
    raw = os.environ.get("SR_LOCAL_JOB_TIMEOUT_SEC")
    try:
        value = float(raw) if raw not in (None, "") else 0.0
    except (TypeError, ValueError):
        return 0.0
    return value if value > 0 else 0.0


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

    The durable queue record is written *before* the runner thread starts, so
    a restart can resume a queued job. Re-submitting the same id is a no-op:
    the existing queued/running/terminal record is returned and no second
    child is started.
    """
    _ensure_loaded()
    script = str(script_path)
    jid = int(job_id) if job_id is not None else reserve_job_id()
    with _LOCK:
        if jid in _JOBS:
            return jid                            # idempotent replay
        now = time.time()
        for old, rec in list(_JOBS.items()):
            if (rec.get("state") in ("exited", "cancelled")
                    and now - float(rec.get("updated_at") or now)
                    > RETENTION_SEC):
                _JOBS.pop(old, None)
        log_path = (Path(sr_runtime().work_dir)
                    / f"{Path(script).stem}.{jid}.out")
        _JOBS[jid] = {
            "job_id": jid, "script": script, "log": str(log_path),
            "pid": None, "state": "queued", "proc": None,
            "cancelled": False, "done": False, "timed_out": False,
            "created_at": now, "started_at": None, "updated_at": now,
            "_dispatched": True,
        }
        if not _persist_locked():
            _JOBS.pop(jid, None)
            raise RuntimeError(
                "local executor cannot persist its queue; refusing to start "
                "a job that a restart could replay")
        epoch = _EPOCH
    threading.Thread(target=_run_job, args=(jid, script, log_path, env, epoch),
                     name=f"sr-local-{jid}", daemon=True).start()
    return jid


def _mark_exited_locked(jid: int, *, timed_out: bool = False) -> None:
    """Finish a record. Caller holds ``_LOCK``."""
    rec = _JOBS.get(jid)
    if rec is None:
        return
    rec.update(state="exited", done=True, timed_out=timed_out,
               updated_at=time.time())
    _persist_locked()


def _run_job(jid: int, script: str, log_path: Path, env,
             epoch: int | None = None) -> None:
    """Slot-holding runner thread: start the child, wait for it, free the slot."""
    _DISPATCH_BLOCKED.wait()                      # quarantined launch window
    with _SLOT:                                   # single slot: one job at a time
        if epoch is not None and epoch != _EPOCH:
            return                                # old process: never start it
        with _LOCK:
            rec = _JOBS.get(jid)
            if rec is None or rec.get("cancelled") or rec.get("state") != "queued":
                return                                # cancelled while queued
            # Mark the launch window before Popen. A crash between Popen and
            # the running/PID write is ambiguous: recovery must not re-run it
            # (the child may exist) and reports UNKNOWN instead.
            rec.update(state="starting", updated_at=time.time())
            if not _persist_locked():
                # No child has started yet. Never proceed with a stale queued
                # record on disk; the next restart would replay it.
                log.error("local_exec: refusing to start job %s: durable queue "
                          "write failed", jid)
                rec.update(state="exited", done=True, updated_at=time.time())
                return
        log_fp = None
        timed_out = False
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
                if epoch is None or epoch == _EPOCH:
                    _mark_exited_locked(jid)
            return
        persist_failed = False
        with _LOCK:
            rec = _JOBS.get(jid)
            if epoch is not None and epoch != _EPOCH:
                rec = None                    # old process: do not touch the new queue
            if rec is not None:
                rec.update(proc=proc, pid=proc.pid,
                           pid_start=_save_pid_identity(proc.pid),
                           state="running",
                           started_at=time.time(), updated_at=time.time())
                persist_failed = not _persist_locked()
            cancelled = rec is None or rec.get("cancelled")
        if persist_failed:
            # The child is already running but the durable record is still
            # ``starting``. Kill it now; a restart will see the ambiguous
            # window and quarantine instead of replaying.
            log.error("local_exec: cannot persist running job %s; "
                      "terminating it", jid)
            _terminate(proc)
            try:
                log_fp.close()
            except OSError:
                pass
            with _LOCK:
                if _JOBS.get(jid) is not None:
                    # We cannot prove on disk that the child was killed; keep
                    # the launch window isolated until a verdict or an operator
                    # confirms it, and block the next scene.
                    _DISPATCH_BLOCKED.clear()
                    _JOBS[jid].update(state="quarantine", done=False,
                                      updated_at=time.time())
                    _persist_locked()
            return
        if cancelled:                             # cancelled mid-launch
            _terminate(proc)
        timeout = _job_timeout_sec()
        deadline = time.monotonic() + timeout if timeout > 0 else None
        try:
            while proc.poll() is None:
                with _LOCK:
                    rec = _JOBS.get(jid)
                    cancel_requested = bool(rec and rec.get("cancel_requested"))
                if cancel_requested:
                    _terminate(proc)              # whole tree, then free the slot
                    break
                if deadline is not None and time.monotonic() >= deadline:
                    timed_out = True
                    _terminate(proc)
                    break
                try:
                    proc.wait(timeout=0.2)
                except subprocess.TimeoutExpired:
                    continue
        finally:
            log_fp.close()
            with _LOCK:
                if (epoch is None or epoch == _EPOCH) and _JOBS.get(jid) is not None:
                    _JOBS[jid]["proc"] = proc
                    _mark_exited_locked(jid, timed_out=timed_out)


def _watch_adopted(jid: int, ready: threading.Event) -> None:
    """Hold the slot for a child that outlived the API process.

    Recovery cannot use Popen.wait() on a foreign process, so this watcher
    owns the single slot until the recorded PID disappears (or its timeout
    expires), which is what keeps a resumed queued job from running in
    parallel with it.
    """
    with _SLOT:
        ready.set()
        with _LOCK:
            rec = _JOBS.get(jid)
            started = float((rec or {}).get("started_at")
                            or (rec or {}).get("created_at") or time.time())
        timeout = _job_timeout_sec()
        deadline = started + timeout if timeout > 0 else None
        while True:
            with _LOCK:
                rec = _JOBS.get(jid)
                if not rec or rec.get("state") != "running":
                    break
                pid = rec.get("pid")
                cancel_requested = bool(rec.get("cancel_requested"))
                identity = _identity_state(rec)
            if identity in ("dead", "mismatch"):
                # Gone, or the PID now belongs to an unrelated process: never
                # signal it. The verdict file (or its absence) decides.
                break
            if identity == "unknown":
                # Cannot prove ownership: keep the slot blocked instead of
                # killing a stranger or freeing the GPU.
                time.sleep(0.2)
                continue
            if cancel_requested or (deadline is not None
                                    and time.time() >= deadline):
                _terminate_pid(pid)
                dead = False
                for _ in range(50):           # up to ~5s for the tree to die
                    if not _pid_alive(pid):
                        dead = True
                        break
                    time.sleep(0.1)
                if not dead:
                    _kill_pid(pid)            # SIGTERM was ignored
                    for _ in range(50):
                        if not _pid_alive(pid):
                            dead = True
                            break
                        time.sleep(0.1)
                if not dead:
                    # Never free the slot while a process may still own the
                    # GPU. Keep the launch window quarantined for a human.
                    log.error("local_exec: job %s did not die after cancel/"
                              "timeout; quarantined", jid)
                    with _LOCK:
                        _DISPATCH_BLOCKED.clear()
                        _JOBS[jid].update(state="quarantine", done=False,
                                          updated_at=time.time())
                        _persist_locked()
                    return
                with _LOCK:
                    _mark_exited_locked(
                        jid, timed_out=bool(deadline is not None
                                            and time.time() >= deadline
                                            and not cancel_requested))
                return
            time.sleep(0.2)
        with _LOCK:
            _mark_exited_locked(jid)


def recover() -> dict:
    """Reload the durable queue after a restart.

    Queued records are dispatched again (the child never started, so this is
    not a duplicate submission). Live running PIDs are adopted and a watcher
    holds the single slot while they run. Dead running PIDs fall back to the
    verdict file through :func:`status`; they are never blindly re-submitted.
    """
    _ensure_loaded()
    adopt: list[int] = []
    resume: list[dict] = []
    quarantine: list[int] = []
    with _LOCK:
        now = time.time()
        for jid, rec in list(_JOBS.items()):
            state = rec.get("state")
            if state in ("starting", "quarantine"):
                # The process died inside the Popen window. It may have
                # started; never replay it blindly and never let the next
                # scene take the slot until the window is reconciled.
                log.warning(
                    "local_exec: job %s interrupted while starting; "
                    "quarantined until its verdict appears or an operator "
                    "calls resolve_quarantine()", jid)
                rec.update(state="quarantine", updated_at=now)
                continue
            if state == "running":
                identity = _identity_state(rec)
                if identity == "dead":
                    rec.update(state="exited", done=True, pid=None,
                               updated_at=now)
                elif identity == "unknown":
                    # Alive but unwitnessed: we cannot prove this PID is ours.
                    # Do not free the slot or signal the PID on a guess.
                    log.warning(
                        "local_exec: job %s has a live PID without a readable "
                        "identity; quarantined", jid)
                    rec.update(state="quarantine", updated_at=now)
                    continue
                elif identity == "mismatch":
                    # Start time/command line differ: the child is gone and the
                    # PID now belongs to an unrelated process. Never signal it.
                    rec.update(state="exited", done=True, updated_at=now)
            if (rec.get("state") in ("exited", "cancelled")
                    and now - float(rec.get("updated_at") or now) > RETENTION_SEC):
                _JOBS.pop(jid, None)
        for rec in sorted(_JOBS.values(), key=lambda row: row["job_id"]):
            state = rec.get("state")
            if state == "quarantine":
                quarantine.append(rec["job_id"])
            elif (state == "running" and rec.get("pid")
                    and not rec.get("_adopted")):
                rec["_adopted"] = True
                adopt.append(rec["job_id"])
            elif (state == "queued" and not rec.get("cancelled")
                    and not rec.get("_dispatched")):
                rec["_dispatched"] = True
                resume.append(dict(rec))
        if quarantine:
            _DISPATCH_BLOCKED.clear()
        _persist_locked()
        epoch = _EPOCH
    # Watchers must own the slot before queued siblings are dispatched.
    events = []
    for jid in adopt:
        event = threading.Event()
        events.append(event)
        threading.Thread(target=_watch_adopted, args=(jid, event),
                         name=f"sr-local-adopt-{jid}", daemon=True).start()
    for event in events:
        # A real restart hands the slot to the watcher immediately; the short
        # wait only orders a queued resume behind it.
        event.wait(timeout=1.0)
    for rec in resume:
        threading.Thread(target=_run_job, args=(
            rec["job_id"], rec["script"], Path(rec["log"]), None, epoch),
            name=f"sr-local-{rec['job_id']}", daemon=True).start()
    return {"adopted": len(adopt), "resumed": len(resume),
            "quarantined": len(quarantine)}


def resolve_quarantine(job_id: int | None = None) -> int:
    """Release a quarantine after the operator reconciles the launch window.

    This never signals a PID (the PID may not be ours). Call only after
    checking ``ps -ef`` / the scene directory; if a verdict file exists, a
    normal ``status()`` call releases the quarantine automatically.
    """
    _ensure_loaded()
    released = 0
    with _LOCK:
        for rec in _JOBS.values():
            if rec.get("state") not in ("quarantine", "starting"):
                continue
            if job_id is not None and int(rec["job_id"]) != int(job_id):
                continue
            rec.update(state="exited", done=True, updated_at=time.time())
            released += 1
        if not any(rec.get("state") in ("quarantine", "starting")
                   for rec in _JOBS.values()):
            _DISPATCH_BLOCKED.set()
        _persist_locked()
    return released


def status(job_id, exit_code_file=None) -> dict:
    """{"job_id", "active", "state", "exit_code"} — same shape as slurm's.

    PENDING while still queued behind the slot, RUNNING while the child lives;
    once it is gone, the verdict file decides COMPLETED / FAILED. A cancelled
    job with no verdict of its own reports CANCELLED (the user's act is the
    fact); a verdict file still wins over it, because it is the job's own
    recorded outcome.

    With no in-memory record (sr-api restarted) the durable queue is reloaded
    first; only an unknown job id falls back to the verdict file. Unreadable →
    UNKNOWN, never a guess.
    """
    _ensure_loaded()
    with _LOCK:
        rec = _JOBS.get(job_id)
        if rec is not None:
            state = rec.get("state")
            proc = rec.get("proc")
            cancelled = bool(rec.get("cancelled"))
            timed_out = bool(rec.get("timed_out"))
    if rec is not None:
        if state in ("quarantine", "starting") and not rec.get("_dispatched"):
            verdict = slurm.terminal_from_exit_file(job_id, exit_code_file)
            if verdict["state"] != "UNKNOWN":
                resolve_quarantine(job_id)
                return verdict
            return {"job_id": job_id, "active": False, "state": "UNKNOWN",
                    "exit_code": None}
        if state in ("queued", "starting") and not cancelled:
            return {"job_id": job_id, "active": True, "state": "PENDING",
                    "exit_code": None}
        if state == "cancelled":
            return {"job_id": job_id, "active": False, "state": "CANCELLED",
                    "exit_code": None}
        if proc is not None and proc.poll() is None:
            return {"job_id": job_id, "active": True, "state": "RUNNING",
                    "exit_code": None}
        if state == "running" and proc is None and _identity_state(rec) == "match":
            # Adopted after a restart: no Popen handle, but the OS child runs
            # and its identity (start time/cmdline) still matches ours.
            return {"job_id": job_id, "active": True, "state": "RUNNING",
                    "exit_code": None}
    st = slurm.terminal_from_exit_file(job_id, exit_code_file)
    if st["state"] == "UNKNOWN" and rec is not None:
        if timed_out:
            return {"job_id": job_id, "active": False, "state": "TIMEOUT",
                    "exit_code": None}
        if cancelled:
            return {"job_id": job_id, "active": False, "state": "CANCELLED",
                    "exit_code": None}
    return st


def cancel(job_id) -> bool:
    """Cancel a queued or running job. False when it is not ours to cancel."""
    _ensure_loaded()
    with _LOCK:
        rec = _JOBS.get(job_id)
        if rec is None:
            return False
        # Persist the *intent* for every state: a running job's cancellation
        # must survive an API restart before the child exit is confirmed.
        rec["cancelled"] = True
        rec["cancel_requested"] = True
        if rec.get("state") == "queued":
            rec["state"] = "cancelled"
        rec["updated_at"] = time.time()
        _persist_locked()
        proc = rec.get("proc")
        pid = rec.get("pid")
        state = rec.get("state")
    if proc is not None and proc.poll() is None:
        _terminate(proc)          # the runner thread sees poll() != None and frees the slot
    elif state == "running" and pid is not None and _identity_matches(rec):
        _terminate_pid(pid)       # adopted child after a restart, identity checked
    return True


# ---------------------------------------------------------------------------
# production preflight (local executor)
# ---------------------------------------------------------------------------

def _explicit_work_dir() -> str:
    return ((os.environ.get("SR_WORK_DIR") or "").strip()
            or (os.environ.get("SR_SLURM_WORK_DIR") or "").strip())


def _env_seconds(name: str, default: float) -> float:
    raw = os.environ.get(name)
    try:
        value = float(raw) if raw not in (None, "") else default
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def _resolve_executable(value: str) -> str | None:
    if not value:
        return None
    path = Path(value)
    if path.is_absolute() or "/" in value or "\\" in value:
        return str(path) if path.is_file() and os.access(path, os.X_OK) else None
    return shutil.which(value)


def _find_key(data, key):
    if isinstance(data, dict):
        if key in data:
            return data[key]
        for value in data.values():
            found = _find_key(value, key)
            if found is not None:
                return found
    elif isinstance(data, list):
        for value in data:
            found = _find_key(value, key)
            if found is not None:
                return found
    return None


def _preflight_key(runtime) -> tuple:
    return (
        runtime.work_dir, runtime.python, runtime.bundle_dir,
        os.environ.get("SR_SR_SCRIPT") or "",
        runtime.local_gpu,
        SR_DEFAULT_OPTIONS_YML,
        os.environ.get("SR_NVIDIA_SMI") or "",
        os.environ.get("SR_PREFLIGHT_IMPORTS") or PREFLIGHT_IMPORTS_DEFAULT,
        os.environ.get("SR_PREFLIGHT_PYTHON_TIMEOUT_SEC") or "",
        os.environ.get("SR_PREFLIGHT_GPU_TIMEOUT_SEC") or "",
    )


def _fail_preflight(checks: dict, name: str, message: str) -> dict:
    checks[name] = {"ok": False, "detail": message}
    return {"ok": False, "reason": f"本机生产执行预检未通过：{message}",
            "checks": checks, "checked_at": time.time()}


def _run_preflight(runtime) -> dict:
    checks: dict[str, dict] = {}

    bash = _bash_path()
    if not bash:
        return _fail_preflight(checks, "bash", "未找到可用 bash")
    checks["bash"] = {"ok": True, "detail": bash}

    raw_work = _explicit_work_dir()
    if not raw_work:
        return _fail_preflight(
            checks, "work_dir", "未配置 SR_WORK_DIR（或旧 SR_SLURM_WORK_DIR）")
    work = Path(raw_work).resolve()
    if not work.is_dir() or not os.access(work, os.W_OK):
        return _fail_preflight(
            checks, "work_dir", f"工作目录不存在或不可写：{work}")
    checks["work_dir"] = {"ok": True, "detail": str(work)}

    python = _resolve_executable(runtime.python)
    if not python:
        return _fail_preflight(
            checks, "python", f"SR_PYTHON 不可执行：{runtime.python!r}")
    modules = [part.strip() for part in
               (os.environ.get("SR_PREFLIGHT_IMPORTS")
                or PREFLIGHT_IMPORTS_DEFAULT).split(",") if part.strip()]
    timeout = _env_seconds("SR_PREFLIGHT_PYTHON_TIMEOUT_SEC", 30.0)
    bundle = Path(runtime.bundle_dir)
    try:
        proc = subprocess.run(
            [python, "-c", "import " + ", ".join(modules)],
            env=_child_env(), cwd=str(bundle) if bundle.is_dir() else None,
            capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return _fail_preflight(
            checks, "python",
            f"SR_PYTHON 导入 {', '.join(modules)} 超时（>{timeout:g}s）")
    except OSError as exc:
        return _fail_preflight(checks, "python", f"SR_PYTHON 无法启动：{exc}")
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()[-300:]
        return _fail_preflight(
            checks, "python",
            f"SR_PYTHON 无法导入 {', '.join(modules)}：{detail or '未知错误'}")
    checks["python"] = {"ok": True, "detail": python}

    bundle = Path(runtime.bundle_dir)
    if not bundle.is_dir():
        return _fail_preflight(
            checks, "algorithm", f"SR_BUNDLE_DIR 不存在：{bundle}")
    sr_script = os.environ.get("SR_SR_SCRIPT") or "code_0817_prod.py"
    entry = Path(sr_script)
    if not entry.is_absolute():
        entry = bundle / sr_script
    if not entry.is_file():
        return _fail_preflight(
            checks, "algorithm", f"算法入口不存在：{entry}")
    repo = Path(__file__).resolve().parents[2]
    for name in ("repair_sr.py", "verify_sr_run.py"):
        script = repo / "SR_code" / "variants" / name
        if not script.is_file():
            return _fail_preflight(
                checks, "algorithm", f"批量修复脚本缺失：{script}")
    checks["algorithm"] = {"ok": True, "detail": str(entry)}

    options = Path(SR_DEFAULT_OPTIONS_YML)
    if not options.is_file():
        return _fail_preflight(checks, "model", f"模型配置不存在：{options}")
    try:
        import yaml
        data = yaml.safe_load(options.read_text(encoding="utf-8"))
    except Exception as exc:               # noqa: BLE001 — report, do not crash
        return _fail_preflight(
            checks, "model", f"模型配置无法读取：{options}（{exc}）")
    model_value = _find_key(data, "pretrain_model_G")
    if not model_value:
        return _fail_preflight(
            checks, "model", f"模型配置缺少 pretrain_model_G：{options}")
    model = Path(str(model_value))
    if not model.is_absolute():
        model = options.parent / model
        if not model.is_file():
            model = bundle / str(model_value)
    if not model.is_file():
        return _fail_preflight(checks, "model", f"模型权重不存在：{model}")
    checks["model"] = {"ok": True, "detail": str(model)}

    gpu_timeout = _env_seconds("SR_PREFLIGHT_GPU_TIMEOUT_SEC", 5.0)
    nvidia_smi = os.environ.get("SR_NVIDIA_SMI") or shutil.which("nvidia-smi")
    if not nvidia_smi:
        return _fail_preflight(checks, "gpu", "未找到 nvidia-smi，无法确认 GPU")
    try:
        proc = subprocess.run(
            [nvidia_smi, "--query-gpu=index,uuid", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=gpu_timeout)
    except subprocess.TimeoutExpired:
        return _fail_preflight(
            checks, "gpu", f"GPU 检查超时（>{gpu_timeout:g}s，nvidia-smi 无响应）")
    except OSError as exc:
        return _fail_preflight(checks, "gpu", f"GPU 检查无法启动：{exc}")
    if proc.returncode != 0:
        return _fail_preflight(
            checks, "gpu", (proc.stderr or proc.stdout or "").strip()[-300:]
            or "nvidia-smi 执行失败")
    available = set()
    for line in (proc.stdout or "").splitlines():
        available.update(part.strip() for part in line.split(",") if part.strip())
    wanted = [part.strip() for part in runtime.local_gpu.split(",") if part.strip()]
    missing = [item for item in wanted if item not in available]
    if missing:
        return _fail_preflight(
            checks, "gpu",
            f"SR_LOCAL_GPU={runtime.local_gpu!r} 中的 {missing} 不在可用 GPU "
            f"{sorted(available)} 中")
    checks["gpu"] = {"ok": True, "detail": runtime.local_gpu}

    return {"ok": True, "reason": None, "checks": checks,
            "checked_at": time.time()}


def preflight(refresh: bool = False) -> dict:
    """Validate the local production runtime, cached per env snapshot.

    Plan-card polling calls this several times; only a cache miss (first plan
    preparation, or a relevant env change) runs the Python import and
    nvidia-smi probes again.
    """
    runtime = sr_runtime()
    key = _preflight_key(runtime)
    with _PREFLIGHT_LOCK:
        cached = _PREFLIGHT.get(key)
        if cached is not None and not refresh:
            return cached
        result = _run_preflight(runtime)
        _PREFLIGHT.clear()
        _PREFLIGHT[key] = result
        if not result["ok"]:
            log.warning("local_exec preflight: %s", result["reason"])
        return result


def reset_preflight() -> None:
    """Test hook: forget cached preflight results."""
    with _PREFLIGHT_LOCK:
        _PREFLIGHT.clear()
