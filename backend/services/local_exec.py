"""Local executor: run the generated run script as a plain child process.

The platform runs SR **on the API host** with the SR production conda
interpreter (``SR_PYTHON``). What the API hands the child is
``run_sr.build_run_script``'s output — a self-contained bash script that runs
input prep, the SR script and the contract verifier in that order.

    available() -> bool
    reserve_job_id(work_dir=None) -> int
    submit(script_path, env=None, job_id=None, run=None) -> int
    status(job_id, exit_code_file=None) -> dict
    cancel(job_id) -> bool
    release_unknown(job_id, note=None) -> bool

Four deliberate properties:

* **Single slot, serial, cross-process.** One SR job at a time, because the
  point of this deployment is one GPU and one scene directory. The slot is a
  lock *file* under the work dir (``.exec.lock``, created ``O_EXCL``), not just
  a ``threading.Lock``: the same host may run more than one API process by
  accident (a stale one during a restart, a second uvicorn), and a lock that
  only exists inside one interpreter cannot see the other. See ``_take_slot``
  and ``_lock_holder_alive``.
* **job_id is persisted before it is used.** ``.local_job_seq`` in the work dir
  (read → +1 → write under a file lock) keeps ids unique across an ``sr-api``
  restart. Uniqueness matters because the verdict file is named after the run
  number: a recycled id would let one run read another run's verdict. When that
  file is missing or unreadable the counter falls back to the clock rather than
  restarting at 1 — the verdict files live in the scene directories and outlive
  the work dir (``/tmp`` by default), so restarting at 1 *is* the recycled-id
  case.
* **The terminal state is the verdict file**, never the process exit code. SR
  has silent-failure paths that ``exit(0)`` without producing anything
  (contract §2.3), so a finished process with no verdict is a failed run.
* **What the run was survives a restart.** Every submit writes
  ``runs/<job_id>.json`` — script, log, scene, the API pid and, once launched,
  the child pid with its process creation stamp — and the runner keeps it
  current. After a restart, ``status()`` reads that record instead of shrugging:
  a child this host can still see is RUNNING, a child that is confirmed gone
  with no verdict is FAILED, and a record whose launch was never confirmed is
  UNKNOWN — never silently "rerunnable".

Not a scheduler: no queue limits, no priorities, no cross-host anything. One API
worker is a deployment requirement, documented in deploy/sr-api.service.
"""

from __future__ import annotations

import errno
import json
import os
import shutil
import signal
import subprocess
import threading
import time
from pathlib import Path

from backend.config import sr_runtime

from . import sr_result

#: Job-id sequence file, under SR_WORK_DIR. Persisted so ids never repeat after
#: a restart (a repeat would alias two runs onto one verdict file).
SEQ_FILE = ".local_job_seq"
#: Cross-process execution slot. Held for the whole life of the SR child.
LOCK_FILE = ".exec.lock"
#: Per-run records (``runs/<job_id>.json``): what survives a restart.
RUN_DIR = "runs"

#: How often a queued job re-tries the execution slot.
SLOT_POLL_SEC = 0.2

#: How long to wait for a signalled child before escalating to SIGKILL.
TERM_GRACE_SEC = 10.0

#: Record lifecycle. `outcome` is set only when the run ended, and it is what a
#: restarted process reads to tell "the operator cancelled it" apart from "it
#: ended and produced nothing".
_ST_QUEUED = "queued"
_ST_LAUNCHING = "launching"
_ST_RUNNING = "running"
_ST_DONE = "done"

_OUT_CANCELLED = "cancelled"
_OUT_LAUNCH_FAILED = "launch_failed"
_OUT_RELEASED = "released"
_OUT_EXITED = "exited"

#: Protects _JOBS and this process's own file writes. Held only for dict / short
#: IO work, never while a child process runs.
_LOCK = threading.Lock()

#: job_id -> {"script", "log", "proc" (None until started), "cancelled",
#: "done", "launch_error", "run"}. Empty after a restart, which is fine:
#: status() falls back to the run record and the verdict file, both on disk.
_JOBS: dict[int, dict] = {}


# --------------------------------------------------------------------------
# process identity — "is the pid I recorded still the process I launched?"
# --------------------------------------------------------------------------

def _proc_alive(pid) -> bool:
    """True when a process with this pid exists.

    POSIX uses signal 0. Windows must NOT: there ``os.kill`` has no signal
    semantics and would terminate the process, so liveness is read off the same
    handle query that yields the identity.
    """
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    if os.name != "posix":
        return _proc_identity(pid) is not None
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True                       # exists, owned by someone else
    except OSError as e:                  # noqa: BLE001 — e.g. no /proc mount
        return e.errno not in (errno.ESRCH,)
    return True


def _proc_identity(pid) -> str | None:
    """A token that is stable for one process and differs across pid reuse.

    The kernel's process creation stamp: ``/proc/<pid>/stat`` field 22 on Linux,
    ``GetProcessTimes``' creation FILETIME on Windows. ``None`` = the process is
    gone, or this host cannot tell us (no ``/proc``, no permission).

    Callers must treat ``None`` as "cannot prove anything", never as "different":
    the safe direction when the question is "may I start a second SR job that
    writes the same directory" is to assume the old one is still there.
    """
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return None
    if pid <= 0:
        return None
    if os.name == "posix":
        try:
            with open("/proc/%d/stat" % pid, "rb") as fh:
                data = fh.read(4096).decode("latin-1")
        except OSError:
            return None
        # field 2 (comm) may contain spaces and parentheses: split after the
        # LAST ')' so the field indexes line up regardless.
        tail = data.rsplit(")", 1)[-1].split()
        # tail[0] is field 3, so field 22 (starttime, in clock ticks) is tail[19].
        if len(tail) < 20:
            return None
        return "proc:%s" % tail[19]
    return _win_proc_identity(pid)


def _win_proc_identity(pid) -> str | None:
    try:
        import ctypes
        import ctypes.wintypes as wintypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        # PROCESS_QUERY_LIMITED_INFORMATION: enough for GetProcessTimes, works
        # for processes owned by another account (unlike QUERY_INFORMATION).
        handle = k32.OpenProcess(0x1000, False, int(pid))
        if not handle:
            return None
        try:
            created = wintypes.FILETIME()
            exited = wintypes.FILETIME()
            kernel = wintypes.FILETIME()
            user = wintypes.FILETIME()
            if not k32.GetProcessTimes(handle, ctypes.byref(created),
                                       ctypes.byref(exited),
                                       ctypes.byref(kernel),
                                       ctypes.byref(user)):
                return None
            return "win:%d:%d" % (created.dwHighDateTime, created.dwLowDateTime)
        finally:
            k32.CloseHandle(handle)
    except Exception:  # noqa: BLE001 — identity is best-effort, never fatal
        return None


def _same_process(pid, identity) -> bool:
    """True when `pid` is alive *and* still the process that `identity` names.

    A pid on its own is not evidence: pids are recycled, and killing a recycled
    pid would take out an unrelated process. When either side of the comparison
    is unknown the answer is True — see `_proc_identity`.
    """
    if not _proc_alive(pid):
        return False
    if not identity:
        return True
    now = _proc_identity(pid)
    return True if now is None else now == identity


# --------------------------------------------------------------------------
# bash discovery (Windows ships a bash.exe that is not bash)
# --------------------------------------------------------------------------

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
    """True when a usable bash exists — local execution needs nothing else.

    Bash is the one real dependency of this executor, so it is what the gate
    checks: without it ``submit`` could only start something that dies instantly
    and leaves no verdict file. Telling the caller *before* the task is created
    keeps a missing interpreter apart from a silent SR failure.
    """
    return _bash_path() is not None


def _reset() -> None:
    """Test hook: forget every in-memory job (on-disk state is left alone)."""
    with _LOCK:
        _JOBS.clear()


def _work_dir(work_dir=None) -> Path:
    return Path(work_dir or sr_runtime().work_dir)


# --------------------------------------------------------------------------
# job-id allocation
# --------------------------------------------------------------------------

def reserve_job_id(work_dir=None) -> int:
    """Allocate the next job id and persist the counter.

    Separate from ``submit`` because the id has to exist *before* the run script
    is written: the verifier is called with ``--job-id <id>`` so it names its
    verdict file after this run, and ``run_sr.exit_code_file_for`` re-derives
    that same name from the same id. Reserving first is what keeps the two sides
    agreeing.

    Atomic across processes: the read-modify-write happens under an exclusive
    lock file, so two API processes — or two threads — can never draw the same
    id. Two runs sharing an id would share a verdict file, and one of them would
    read the other's outcome.
    """
    work = _work_dir(work_dir)
    with _LOCK:
        work.mkdir(parents=True, exist_ok=True)
        seq_file = work / SEQ_FILE
        with _seq_lock(work):
            try:
                n = int(seq_file.read_text(encoding="utf-8").strip())
            except (OSError, ValueError):
                # Nothing to continue from: a fresh work dir, or the file was
                # lost or corrupted (SR_WORK_DIR defaults to /tmp, which a
                # reboot clears). Restarting at 1 would recycle ids whose
                # verdict files are still lying in the scene directories —
                # those outlive the work dir, and a recycled id would read a
                # stranger's verdict and report a run that never happened as
                # COMPLETED. Fall back to the clock, which cannot collide with
                # an id already handed out.
                n = int(time.time())
            n += 1
            seq_file.write_text(f"{n}\n", encoding="utf-8")
            return n


class _seq_lock:
    """Exclusive ``.local_job_seq.lock`` around one read-modify-write."""

    def __init__(self, work: Path):
        self.path = Path(work) / (SEQ_FILE + ".lock")
        self.fd = None

    def __enter__(self):
        deadline = time.monotonic() + 10.0
        while True:
            try:
                self.fd = os.open(str(self.path),
                                  os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
                return self
            except FileExistsError:
                # Holder crashed mid-write: the stamp is only ever held for
                # microseconds, so a lock file older than a few seconds is
                # debris, not a live writer.
                try:
                    age = time.time() - self.path.stat().st_mtime
                except OSError:
                    age = 0.0
                if age > 30.0:
                    try:
                        self.path.unlink()
                    except OSError:
                        pass
                if time.monotonic() > deadline:
                    raise RuntimeError(
                        f"job-id counter lock stuck: {self.path} (remove it "
                        "after confirming no sr-api process holds it)")
                time.sleep(0.02)

    def __exit__(self, *exc):
        if self.fd is not None:
            try:
                os.close(self.fd)
            except OSError:
                pass
        try:
            self.path.unlink()
        except OSError:
            pass
        return False


# --------------------------------------------------------------------------
# persisted run records — what a restart has to go on
# --------------------------------------------------------------------------

def _run_record_path(work: Path, job_id: int) -> Path:
    return Path(work) / RUN_DIR / f"{int(job_id)}.json"


def _write_run_record(work: Path, job_id: int, data: dict) -> None:
    """Atomically (over)write one run record. Never raises into the runner."""
    path = _run_record_path(work, job_id)
    tmp = path.with_name(path.name + ".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        os.replace(str(tmp), str(path))
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass


def read_run_record(job_id, work_dir=None) -> dict | None:
    """The persisted record for one run, or None when there is none.

    Public because ``run_sr`` needs it to answer "did the previous run of this
    task really stop?" without guessing.
    """
    work = _work_dir(work_dir)
    try:
        with open(_run_record_path(work, int(job_id)), "r",
                  encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError, TypeError):
        return None
    return data if isinstance(data, dict) else None


def _patch_run_record(work: Path, job_id: int, **fields) -> None:
    """Merge fields into the run record, creating it if it is missing."""
    record = read_run_record(job_id, work_dir=work) or {"job_id": int(job_id)}
    record.update(fields)
    _write_run_record(work, job_id, record)


# --------------------------------------------------------------------------
# the cross-process execution slot
# --------------------------------------------------------------------------

def _lock_payload(job_id: int, script: str, run: dict | None) -> dict:
    return {
        "job_id": int(job_id),
        "script": str(script),
        "scene": (run or {}).get("scene"),
        "fingerprint": (run or {}).get("fingerprint"),
        "api_pid": os.getpid(),
        "api_identity": _proc_identity(os.getpid()),
        "child_pid": None,
        "child_identity": None,
        "taken_at": time.time(),
    }


def _lock_holder_alive(payload: dict) -> bool:
    """Is the slot still in use, according to the lock file's own record?

    Two questions, in order of what actually holds the GPU:

    1. The **child** — an SR process that outlived its API (killed, crashed,
       OOM) keeps writing the scene, so its presence alone means "busy". The
       identity comparison is what stops a recycled pid from counting.
    2. The **API** that took the slot but has not launched yet — it is about to.
    """
    if _same_process(payload.get("child_pid"), payload.get("child_identity")):
        return True
    if _same_process(payload.get("api_pid"), payload.get("api_identity")):
        return True
    return False


def _take_slot(work: Path, job_id: int, script: str, run: dict | None) -> bool:
    """Try once to acquire the execution slot. False = someone else holds it.

    The lock file is created with ``O_EXCL``: creation *is* the acquisition, so
    two processes racing cannot both win. A file whose holder is provably gone
    (see ``_lock_holder_alive``) is debris and is cleared.
    """
    path = work / LOCK_FILE
    payload = _lock_payload(job_id, script, run)
    for attempt in (0, 1):
        try:
            fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    held = json.load(fh)
            except (OSError, ValueError):
                held = {}
            if isinstance(held, dict) and _lock_holder_alive(held):
                return False
            if attempt:
                return False          # lost the race to clear it
            try:
                path.unlink()
            except OSError:
                return False
            continue
        except OSError:
            return False
        try:
            os.write(fd, json.dumps(payload, ensure_ascii=False).encode("utf-8"))
        finally:
            os.close(fd)
        return True
    return False


def _update_slot(work: Path, **fields) -> None:
    """Publish the child pid into the slot record we already hold."""
    path = work / LOCK_FILE
    try:
        with open(path, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
    except (OSError, ValueError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    payload.update(fields)
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        os.replace(str(tmp), str(path))
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass


def _release_slot(work: Path, job_id: int) -> None:
    """Drop the slot, but only if this run still owns it."""
    path = work / LOCK_FILE
    try:
        with open(path, "r", encoding="utf-8") as fh:
            held = json.load(fh)
    except (OSError, ValueError):
        return
    if isinstance(held, dict) and held.get("job_id") not in (None, int(job_id)):
        return
    try:
        path.unlink()
    except OSError:
        pass


# --------------------------------------------------------------------------
# launching
# --------------------------------------------------------------------------

def _child_env(extra: dict | None = None) -> dict:
    """Environment for the SR child — the shell-activation equivalent.

    The API process runs in ``/opt/sr-venv`` (py3.9) while the job needs the SR
    production env (py3.6 + torch + GDAL). Those two must not mix:

    1. Drop ``VIRTUAL_ENV`` / ``PYTHONHOME`` / ``PYTHONPATH`` — inherited, they
       would make the conda interpreter import the API venv's packages.
    2. Prepend the interpreter's own directory to ``PATH`` (what activating the
       conda env does).
    3. Set ``CUDA_VISIBLE_DEVICES`` from ``SR_LOCAL_GPU`` — the API launches the
       job itself, so nothing else picks the card, and the platform variant
       reads the variable instead of hardcoding "0".

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


def _signal_tree(pid: int) -> None:
    """SIGTERM / taskkill a process *and its descendants*, by pid.

    The script bash runs is only the wrapper: what must actually die is the
    python process it starts (which is what holds the GPU). POSIX gets the tree
    from the process group — the child is started with ``start_new_session=True``
    so it leads its own group and ``killpg`` reaches every descendant. Windows
    has no process groups to signal, so it needs ``taskkill /T`` for the same
    effect; a bare ``terminate()`` there would orphan the python child, which
    then keeps running (and holding the log file) while the slot already reports
    free.
    """
    try:
        if os.name == "posix":
            os.killpg(int(pid), signal.SIGTERM)
        else:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(int(pid))],
                           capture_output=True, check=False)
    except Exception:  # noqa: BLE001 — already gone, or not ours to signal
        pass


def _kill_tree(pid: int) -> None:
    """The unignorable version, for a child that outlived the grace period."""
    try:
        if os.name == "posix":
            os.killpg(int(pid), signal.SIGKILL)
        else:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(int(pid))],
                           capture_output=True, check=False)
    except Exception:  # noqa: BLE001
        pass


def _terminate_proc(proc) -> None:
    """Stop a child this process launched, then make sure it is really gone."""
    try:
        pgid = os.getpgid(proc.pid) if os.name == "posix" else proc.pid
    except Exception:  # noqa: BLE001 — already reaped
        return
    _signal_tree(pgid)
    try:
        proc.wait(timeout=TERM_GRACE_SEC)
        return
    except Exception:  # noqa: BLE001 — wedged; escalate rather than hang
        _kill_tree(pgid)
    try:
        proc.wait(timeout=TERM_GRACE_SEC)
    except Exception:  # noqa: BLE001
        pass


def _terminate_pid(pid, identity) -> bool:
    """Stop a run this process did not launch (post-restart cancel).

    Refuses when the pid no longer matches the recorded creation stamp: that pid
    now belongs to something else, and killing it would be worse than leaving
    the operator to look. Returns True when the process is confirmed gone.
    """
    if not _same_process(pid, identity):
        return not _proc_alive(pid)
    _signal_tree(int(pid))
    deadline = time.monotonic() + TERM_GRACE_SEC
    while time.monotonic() < deadline:
        if not _same_process(pid, identity):
            return True
        time.sleep(0.1)
    # Still there after the grace period: escalate, then report the outcome
    # honestly rather than claiming a stop that did not happen. TERM is
    # ignorable, and the run script's grandchildren are not obliged to honour it.
    _kill_tree(int(pid))
    for _ in range(50):
        if not _same_process(pid, identity):
            return True
        time.sleep(0.1)
    return False


def submit(script_path, env=None, job_id=None, run=None) -> int:
    """Queue the script and return its job id.

    ``job_id`` may be pre-allocated with :func:`reserve_job_id` (run_sr does
    this, because the id is baked into the script text before it runs); when
    omitted a fresh one is drawn here.

    ``run`` carries what identifies this run afterwards — ``scene``,
    ``fingerprint``, ``config_xml`` — into the persisted record and the slot
    file.

    Returns immediately — the child starts on a daemon thread that first waits
    for the execution slot, so a queued job reports PENDING until it really runs.
    """
    script = str(script_path)
    jid = int(job_id) if job_id is not None else reserve_job_id()
    work = _work_dir()
    log_path = work / f"{Path(script).stem}.out"
    run = dict(run or {})
    with _LOCK:
        _JOBS[jid] = {"script": script, "log": str(log_path), "proc": None,
                      "cancelled": False, "done": False, "launch_error": None,
                      "run": run}
    _write_run_record(work, jid, {
        "job_id": jid, "state": _ST_QUEUED, "outcome": None, "note": None,
        "script": script, "log": str(log_path),
        "config_xml": run.get("config_xml"), "scene": run.get("scene"),
        "fingerprint": run.get("fingerprint"),
        "api_pid": os.getpid(), "api_identity": _proc_identity(os.getpid()),
        "child_pid": None, "child_identity": None,
        "created_at": time.time(), "started_at": None, "finished_at": None,
    })
    threading.Thread(target=_run_job, args=(jid, script, work, log_path, env),
                     name=f"sr-local-{jid}", daemon=True).start()
    return jid


def _run_job(jid: int, script: str, work: Path, log_path: Path, env) -> None:
    """Runner thread: claim the slot, start the child, wait, release."""
    while True:
        with _LOCK:
            rec = _JOBS.get(jid)
            if rec is None or rec["cancelled"]:
                _patch_run_record(work, jid, state=_ST_DONE,
                                  outcome=_OUT_CANCELLED,
                                  note="排队期间被取消，没有启动",
                                  finished_at=time.time())
                return                      # cancelled while still queued
            run = rec["run"]
        if _take_slot(work, jid, script, run):
            break
        time.sleep(SLOT_POLL_SEC)

    proc = None
    launch_error = None
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_fp = open(log_path, "wb")
        try:
            # Absolute path, not the bare name: see _bash_path (Windows would
            # otherwise launch the System32 WSL stub).
            bash = _bash_path()
            if bash is None:
                raise RuntimeError("no bash on PATH — cannot run the run script")
            # cwd: the bundle when it exists (the run script also cds there),
            # else the API process's own cwd. Never a hard failure — on the dev
            # machine there is no bundle dir to cd to.
            bundle = sr_runtime().bundle_dir
            cwd = bundle if bundle and os.path.isdir(bundle) else None
            # The record says "launching" before the exec, so a restart landing
            # in the middle of this cannot read the run as still queued (nothing
            # would ever start it) — see _record_state.
            _patch_run_record(work, jid, state=_ST_LAUNCHING,
                              started_at=time.time())
            proc = subprocess.Popen(
                [bash, str(script)], stdout=log_fp, stderr=subprocess.STDOUT,
                env=_child_env(env), cwd=cwd,
                start_new_session=(os.name == "posix"))
        finally:
            log_fp.close()
    except Exception as e:  # noqa: BLE001 — unlaunchable: record it, do not guess
        launch_error = f"{type(e).__name__}: {e}"
        try:
            with open(log_path, "ab") as fh:
                fh.write(f"local_exec: launch failed: {launch_error}\n"
                         .encode("utf-8"))
        except OSError:
            pass

    if proc is None:
        with _LOCK:
            rec = _JOBS.get(jid)
            if rec is not None:
                rec["done"] = True
                rec["launch_error"] = launch_error
        _release_slot(work, jid)
        _patch_run_record(work, jid, state=_ST_DONE,
                          outcome=_OUT_LAUNCH_FAILED, note=launch_error,
                          finished_at=time.time())
        return

    with _LOCK:
        rec = _JOBS.get(jid)
        if rec is not None:
            rec["proc"] = proc
            cancelled = rec["cancelled"]
        else:
            cancelled = True
    child_identity = _proc_identity(proc.pid)
    _update_slot(work, child_pid=proc.pid, child_identity=child_identity,
                 started_at=time.time())
    _patch_run_record(work, jid, state=_ST_RUNNING, child_pid=proc.pid,
                      child_identity=child_identity)
    if cancelled:                              # cancelled mid-launch
        _terminate_proc(proc)
    try:
        proc.wait()
    finally:
        with _LOCK:
            rec = _JOBS.get(jid)
            if rec is not None:
                rec["done"] = True
        _release_slot(work, jid)
        _patch_run_record(
            work, jid, state=_ST_DONE,
            outcome=_OUT_CANCELLED if cancelled else _OUT_EXITED,
            note="已取消" if cancelled else None, finished_at=time.time())


# --------------------------------------------------------------------------
# status
# --------------------------------------------------------------------------

def _record_state(job_id: int, exit_code_file) -> dict:
    """State for a run this process has no memory of.

    This is the restart path, and it answers from two on-disk facts only: the
    run record (was a child ever launched, and is that exact process still
    there?) and the verdict file the run writes when it is done.

    The distinctions that matter — each one prevents a specific bad outcome:

    * a child this host can still see → RUNNING, never "free";
    * a child confirmed gone with no verdict → FAILED (it ended and produced
      nothing usable), which is rerunnable;
    * a run that was **launching** when the API died → UNKNOWN: it may have
      started. Claiming "failed, safe to rerun" here is exactly the double-write
      the slot lock exists to prevent, so it stays blocked until an operator
      confirms.
    """
    st = sr_result.terminal_from_exit_file(job_id, exit_code_file)
    if st["state"] != "UNKNOWN":
        return st
    record = read_run_record(job_id)
    if record is None:
        return st                                   # nothing to go on → UNKNOWN
    state = record.get("state")
    outcome = record.get("outcome")
    if _same_process(record.get("child_pid"), record.get("child_identity")):
        return {"job_id": job_id, "active": True, "state": "RUNNING",
                "exit_code": None,
                "note": "重启前启动的子进程仍在运行（已按进程创建时间核对身份）"}
    if outcome in (_OUT_CANCELLED, _OUT_RELEASED):
        return {"job_id": job_id, "active": False, "state": "CANCELLED",
                "exit_code": None, "note": record.get("note")}
    if outcome == _OUT_LAUNCH_FAILED:
        return {"job_id": job_id, "active": False, "state": "FAILED",
                "exit_code": None,
                "note": record.get("note") or "启动失败，进程没有跑起来"}
    if record.get("child_pid") or state == _ST_DONE:
        # Launched, that exact process is gone, and there is no verdict: the run
        # ended without producing a usable result. Not COMPLETED, and not
        # UNKNOWN either — we can see the process is not there.
        return {"job_id": job_id, "active": False, "state": "FAILED",
                "exit_code": None,
                "note": record.get("note")
                        or "进程已退出，但没有写结果文件（无有效产物）"}
    if state == _ST_QUEUED:
        # Queued in a process that is gone: the runner thread died with it, so
        # nothing will ever start this run. Say so instead of showing PENDING
        # forever — a restart has no way to resume an in-process queue.
        return {"job_id": job_id, "active": False, "state": "FAILED",
                "exit_code": None,
                "note": "排队中被 sr-api 重启打断，没有启动；可以直接重投"}
    # state == "launching" with no child pid: the exec may or may not have
    # happened and the pid was never published.
    return {"job_id": job_id, "active": None, "state": "UNKNOWN",
            "exit_code": None,
            "note": "这次运行是否真的启动过无法确认（重启打断了记录）；"
                    "先核实没有残留进程，确认后在队列里取消再重投"}


def status(job_id, exit_code_file=None) -> dict:
    """{"job_id", "active", "state", "exit_code"} — plus a `note` when useful.

    PENDING while still queued behind the slot, RUNNING while the child lives;
    once it is gone, the verdict file decides COMPLETED / FAILED. A cancelled run
    with no verdict of its own reports CANCELLED (a cancelled run never gets a
    verdict); a verdict file still wins over everything, because it is the run's
    own recorded outcome.

    With no in-memory record (sr-api restarted, or this is another process) the
    run record and the verdict file answer together — see `_record_state`.
    `active` is None (not False) when the answer is genuinely unknown: callers
    must not read that as "over".
    """
    with _LOCK:
        rec = _JOBS.get(job_id)
    if rec is not None and rec["proc"] is None and not rec["done"]:
        if rec["cancelled"]:
            return {"job_id": job_id, "active": False, "state": "CANCELLED",
                    "exit_code": None, "note": "排队期间被取消，没有启动"}
        return {"job_id": job_id, "active": True, "state": "PENDING",
                "exit_code": None}
    if rec is not None and rec["proc"] is not None and rec["proc"].poll() is None:
        return {"job_id": job_id, "active": True, "state": "RUNNING",
                "exit_code": None}
    st = sr_result.terminal_from_exit_file(job_id, exit_code_file)
    if st["state"] != "UNKNOWN":
        return st
    if rec is not None:
        if rec["cancelled"]:
            return {"job_id": job_id, "active": False, "state": "CANCELLED",
                    "exit_code": None, "note": "已取消"}
        if rec.get("launch_error"):
            return {"job_id": job_id, "active": False, "state": "FAILED",
                    "exit_code": None,
                    "note": f"启动失败：{rec['launch_error']}"}
        if rec["done"]:
            return {"job_id": job_id, "active": False, "state": "FAILED",
                    "exit_code": None,
                    "note": "进程已退出，但没有写结果文件（无有效产物）"}
    return _record_state(job_id, exit_code_file)


# --------------------------------------------------------------------------
# cancel
# --------------------------------------------------------------------------

def _mark_cancelled(work: Path, job_id: int, note: str) -> None:
    _patch_run_record(work, job_id, state=_ST_DONE, outcome=_OUT_CANCELLED,
                      note=note, finished_at=time.time())


def cancel(job_id) -> bool:
    """Cancel a queued or running run. False when it is not ours to cancel.

    Covers the post-restart case too: the persisted record names the child pid
    and its creation stamp, so a run started by a previous API process can still
    be stopped — but only after the stamp matches, so a recycled pid is never
    killed. Returns False when the stop could not be confirmed, and the record
    is left alone: reporting a stop that did not happen is how a slot looks free
    while an SR process is still writing the scene.
    """
    with _LOCK:
        rec = _JOBS.get(job_id)
        if rec is not None:
            rec["cancelled"] = True
            proc = rec["proc"]
    if rec is not None:
        if proc is not None and proc.poll() is None:
            _terminate_proc(proc)     # the runner records the end on wait()
        return True

    work = _work_dir()
    record = read_run_record(job_id)
    if record is None:
        return False
    pid, ident = record.get("child_pid"), record.get("child_identity")
    if pid:
        if not _terminate_pid(pid, ident):
            return False
        _mark_cancelled(work, job_id, "已取消（子进程已停止）")
        return True
    if record.get("state") in (_ST_QUEUED, _ST_DONE):
        _mark_cancelled(work, job_id, "已取消（进程已经不在）")
        return True
    # "launching" with no pid: there is nothing this process can prove or
    # signal. release_unknown is the operator's route out of it.
    return False


def release_unknown(job_id, note: str = "操作员核实无残留进程后释放") -> bool:
    """Mark a run whose launch could not be confirmed as stopped.

    The escape hatch for the UNKNOWN case `_record_state` reports: the platform
    cannot prove whether that run ever started, so it refuses to re-submit the
    same task on its own. An operator who has checked by hand
    (``ps -ef | grep <script>``) calls this — through the queue's cancel
    endpoint with ``confirmed_stopped`` — and the row becomes CANCELLED, which
    un-blocks the re-submit.

    Deliberately explicit and separate from `cancel`: nothing here claims a
    process was signalled.
    """
    work = _work_dir()
    _patch_run_record(work, job_id, state=_ST_DONE, outcome=_OUT_RELEASED,
                      note=note, finished_at=time.time())
    return True
