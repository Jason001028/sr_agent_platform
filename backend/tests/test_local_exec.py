"""Tests for the local executor (backend/services/local_exec.py).

No Slurm, no GPU, no real SR: every case runs a *short bash script* that stands
in for the generated batch script, and the terminal state is always read back
from a verdict file the script writes itself — which is exactly the contract
the real job follows (docs/sr_code/sr-pipeline-interface.md §2.3).

Covered: the single execution slot (a second submit reports PENDING, not
RUNNING), the persisted job-id sequence, verdict-file-driven terminal states,
`cancel` for both queued and running jobs, the restart case (no in-memory
record), and the child environment the SR interpreter needs.
"""

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from backend.config import sr_runtime
from backend.services import local_exec

#: Same resolution the executor uses (which skips the System32 WSL launcher on
#: Windows) — so "no bash here" skips instead of failing every case.
BASH = local_exec._bash_path()

_ENVS = ("SR_WORK_DIR", "SR_SLURM_WORK_DIR", "SR_PYTHON", "SR_LOCAL_GPU",
         "SR_BUNDLE_DIR", "SR_LOCAL_JOB_TIMEOUT_SEC", "SR_SR_SCRIPT",
         "SR_PREFLIGHT_IMPORTS", "SR_NVIDIA_SMI",
         "SR_PREFLIGHT_PYTHON_TIMEOUT_SEC", "SR_PREFLIGHT_GPU_TIMEOUT_SEC")


def verdict_script(scene: Path, job_id: int, *, sleep: float = 0,
                   verdict: int = 0) -> Path:
    """A stand-in for the generated batch script: does nothing but sleep and
    leave a verdict file, the way verify_sr_run.py does."""
    debug = scene / "Debug"
    path = scene / f"fake_{job_id}.sh"
    body = ["#!/bin/bash"]
    if sleep:
        body.append(f"sleep {sleep}")
    body += [
        f'mkdir -p "{debug.as_posix()}"',
        f'printf "job_id={job_id}\\nsr_exit_code=0\\nverdict={verdict}\\n'
        f'skip=0\\n" > "{(debug / f"_SREXIT_{job_id}.txt").as_posix()}"',
    ]
    path.write_text("\n".join(body) + "\n", encoding="utf-8")
    return path


def durable_script(scene: Path, job_id: int, starts: Path, *,
                   sleep: float = 0, verdict: int = 0) -> Path:
    """Like verdict_script, but records every physical start in ``starts``.

    Recovery tests assert one line: a resumed queued job must not be started
    twice by the old and new process.
    """
    debug = scene / "Debug"
    path = scene / f"durable_{job_id}.sh"
    body = [
        "#!/bin/bash",
        f'printf "start-{job_id}\\n" >> "{starts.as_posix()}"',
    ]
    if sleep:
        body.append(f"sleep {sleep}")
    body += [
        f'mkdir -p "{debug.as_posix()}"',
        f'printf "job_id={job_id}\\nsr_exit_code=0\\nverdict={verdict}\\n'
        f'skip=0\\n" > "{(debug / f"_SREXIT_{job_id}.txt").as_posix()}"',
    ]
    path.write_text("\n".join(body) + "\n", encoding="utf-8")
    return path


@unittest.skipUnless(BASH, "local executor runs bash scripts")
class LocalExecBase(unittest.TestCase):
    def setUp(self):
        try:
            self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        except TypeError:                        # Python < 3.10
            self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.scene = self.root / "SCENE"
        self.scene.mkdir()
        self.work = self.root / "work"
        self.work.mkdir()
        self._saved = {k: os.environ.get(k) for k in _ENVS}
        os.environ["SR_SLURM_WORK_DIR"] = str(self.work)
        os.environ.pop("SR_WORK_DIR", None)
        os.environ.pop("SR_LOCAL_GPU", None)
        os.environ.pop("SR_BUNDLE_DIR", None)
        os.environ.pop("SR_PYTHON", None)
        os.environ.pop("SR_LOCAL_JOB_TIMEOUT_SEC", None)
        local_exec.reset_preflight()
        local_exec._reset()
        self.addCleanup(self._restore)

    def _restore(self):
        self._kill_leftovers()
        local_exec._reset()
        local_exec.reset_preflight()
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self._tmp.cleanup()

    def _kill_leftovers(self, timeout=15.0):
        """Kill any child a failing test left behind, then wait for it to die.

        Without this a leaked job keeps the log file open and the temp-dir
        cleanup fails on Windows — turning one real failure into a cascade of
        unrelated teardown errors.
        """
        for jid in list(local_exec._JOBS):
            local_exec.cancel(jid)
        deadline = time.time() + timeout
        while time.time() < deadline:
            alive = any(
                (rec.get("proc") is not None and rec["proc"].poll() is None)
                or (rec.get("pid") and local_exec._pid_alive(rec["pid"]))
                for rec in list(local_exec._JOBS.values()))
            if not alive:
                return
            time.sleep(0.05)

    def exit_file(self, job_id: int) -> str:
        return str(self.scene / "Debug" / f"_SREXIT_{job_id}.txt")

    def status(self, job_id: int) -> dict:
        return local_exec.status(job_id, exit_code_file=self.exit_file(job_id))

    def wait_state(self, job_id: int, want, timeout=20.0) -> str:
        """Poll until the job reaches one of `want` (or timeout → last seen)."""
        want = {want} if isinstance(want, str) else set(want)
        deadline = time.time() + timeout
        last = None
        while time.time() < deadline:
            last = self.status(job_id)["state"]
            if last in want:
                return last
            time.sleep(0.05)
        self.fail(f"state never reached {want}; last={last!r}")
        return last

    def wait_terminal(self, job_id: int, timeout=20.0) -> str:
        """Poll until the job stops being PENDING/RUNNING (polling "not
        PENDING" is not enough: RUNNING is not PENDING and not terminal)."""
        return self.wait_state(job_id, {"COMPLETED", "FAILED", "CANCELLED",
                                        "TIMEOUT", "UNKNOWN"}, timeout)


class TestJobIds(LocalExecBase):
    def test_available_needs_no_scheduler(self):
        self.assertTrue(local_exec.available())

    def test_sequence_persists_across_a_restart(self):
        first = local_exec.reserve_job_id()
        self.assertEqual(local_exec.reserve_job_id(), first + 1)
        local_exec._reset()                      # sr-api restarted: memory gone
        self.assertEqual(local_exec.reserve_job_id(), first + 2)  # file carries on
        self.assertEqual((self.work / local_exec.SEQ_FILE).read_text().strip(),
                         str(first + 2))

    def test_ids_are_never_reused(self):
        ids = {local_exec.reserve_job_id() for _ in range(20)}
        self.assertEqual(len(ids), 20)

    def test_a_lost_counter_never_restarts_at_one(self):
        """计数器丢了（`/tmp` 被重启清掉 / work dir 换了）也不能从 1 重来。

        `_SREXIT_<job_id>.txt` 留在场景目录里，比计数器活得久；从 1 重号会让一个
        还没跑（或跑到一半就崩）的作业读到上一轮的 `verdict=0`，在队列里显示成
        COMPLETED。`SR_SLURM_WORK_DIR` 默认在 `/tmp`，所以这不是角落情形。
        """
        local_exec.reserve_job_id()                          # 先正常发过号
        (self.work / local_exec.SEQ_FILE).unlink()            # 计数器随后丢了
        local_exec._reset()
        self.assertGreater(local_exec.reserve_job_id(), 1_000_000)

    def test_a_corrupt_counter_never_restarts_at_one(self):
        (self.work / local_exec.SEQ_FILE).write_text("")      # 写一半断电的空文件
        local_exec._reset()
        self.assertGreater(local_exec.reserve_job_id(), 1_000_000)


class TestChildEnv(LocalExecBase):
    def test_drops_the_api_venv_and_activates_the_sr_one(self):
        os.environ["SR_PYTHON"] = "/opt/conda/envs/torch1/bin/python"
        os.environ["SR_LOCAL_GPU"] = "2"
        os.environ["VIRTUAL_ENV"] = "/opt/sr-venv"
        os.environ["PYTHONHOME"] = "/opt/sr-venv"
        os.environ["PYTHONPATH"] = "/opt/sr-venv/lib"
        self.addCleanup(lambda: [os.environ.pop(k, None)
                                 for k in ("VIRTUAL_ENV", "PYTHONHOME",
                                           "PYTHONPATH")])
        env = local_exec._child_env()
        # 1. the API venv must not leak into the py3.6 SR interpreter
        for leaked in ("VIRTUAL_ENV", "PYTHONHOME", "PYTHONPATH"):
            self.assertNotIn(leaked, env)
        # 2. activating the conda env == its bin dir first on PATH
        self.assertTrue(env["PATH"].startswith("/opt/conda/envs/torch1/bin"
                                                + os.pathsep))
        # 3. one card, named here because no scheduler is picking it
        self.assertEqual(env["CUDA_VISIBLE_DEVICES"], "2")
        # 4. the audit preamble echoes this
        self.assertEqual(env["SR_EXECUTOR"], "local")

    def test_extra_env_wins(self):
        env = local_exec._child_env({"CUDA_VISIBLE_DEVICES": "3"})
        self.assertEqual(env["CUDA_VISIBLE_DEVICES"], "3")


class TestSingleSlot(LocalExecBase):
    def test_second_job_queues_until_the_first_finishes(self):
        jid_a = local_exec.reserve_job_id()
        local_exec.submit(verdict_script(self.scene, jid_a, sleep=1.5),
                          job_id=jid_a)
        jid_b = local_exec.reserve_job_id()
        local_exec.submit(verdict_script(self.scene, jid_b, sleep=0),
                          job_id=jid_b)

        self.assertEqual(self.wait_state(jid_a, "RUNNING"), "RUNNING")
        # b is behind a in the single slot: queued, not running.
        self.assertEqual(self.status(jid_b), {
            "job_id": jid_b, "active": True, "state": "PENDING",
            "exit_code": None})

        self.assertEqual(self.wait_state(jid_a, "COMPLETED"), "COMPLETED")
        self.assertEqual(self.wait_state(jid_b, "RUNNING",
                                         timeout=10), "RUNNING")   # slot freed
        self.assertEqual(self.wait_state(jid_b, "COMPLETED"), "COMPLETED")

    def test_log_file_per_job(self):
        jid = local_exec.reserve_job_id()
        local_exec.submit(verdict_script(self.scene, jid), job_id=jid)
        self.wait_state(jid, "COMPLETED")
        logs = list(self.work.glob(f"*.{jid}.out"))
        self.assertEqual(len(logs), 1)


class TestTerminalState(LocalExecBase):
    def test_verdict_file_decides_completed(self):
        jid = local_exec.reserve_job_id()
        local_exec.submit(verdict_script(self.scene, jid), job_id=jid)
        self.wait_state(jid, "COMPLETED")
        st = self.status(jid)
        self.assertFalse(st["active"])
        self.assertEqual(st["exit_code"], "0:0")

    def test_a_zero_exit_without_a_verdict_is_unknown_not_completed(self):
        # The silent-failure case the whole verdict-file design exists for:
        # exit code 0 says nothing (§2.3). No verdict → UNKNOWN, never COMPLETED.
        jid = local_exec.reserve_job_id()
        script = self.scene / "noop.sh"
        script.write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
        local_exec.submit(script, job_id=jid)
        self.assertEqual(self.wait_terminal(jid), "UNKNOWN")

    def test_verdict_90_is_failed(self):
        jid = local_exec.reserve_job_id()
        local_exec.submit(verdict_script(self.scene, jid, verdict=90),
                          job_id=jid)
        self.wait_state(jid, "FAILED")

    def test_restart_falls_back_to_the_verdict_file(self):
        jid = local_exec.reserve_job_id()
        local_exec.submit(verdict_script(self.scene, jid), job_id=jid)
        self.wait_state(jid, "COMPLETED")
        local_exec._reset()                      # sr-api restarted
        st = self.status(jid)
        self.assertEqual(st["state"], "COMPLETED")   # on-disk verdict still wins

    def test_unlaunchable_script_is_unknown_not_pending(self):
        jid = local_exec.reserve_job_id()
        local_exec.submit(self.scene / "ghost.sh", job_id=jid)   # never created
        # bash itself reports "No such file or directory" and exits 127, so the
        # child *does* run and exit 0-ish: only the missing verdict file says
        # this produced nothing (the contract's §2.3 silent-failure case).
        self.assertEqual(self.wait_terminal(jid), "UNKNOWN")


class TestCancel(LocalExecBase):
    def test_cancel_a_queued_job(self):
        blocker = local_exec.reserve_job_id()
        local_exec.submit(verdict_script(self.scene, blocker, sleep=1.5),
                          job_id=blocker)
        queued = local_exec.reserve_job_id()
        local_exec.submit(verdict_script(self.scene, queued), job_id=queued)
        self.wait_state(blocker, "RUNNING")
        self.assertEqual(self.status(queued)["state"], "PENDING")

        self.assertTrue(local_exec.cancel(queued))
        st = self.status(queued)
        self.assertEqual(st["state"], "CANCELLED")
        self.assertFalse(st["active"])
        # the runner thread skipped it: no verdict file for a job that never ran
        self.assertFalse(Path(self.exit_file(queued)).exists())

    def test_cancel_a_running_job_frees_the_slot(self):
        long_job = local_exec.reserve_job_id()
        local_exec.submit(verdict_script(self.scene, long_job, sleep=30),
                          job_id=long_job)
        self.wait_state(long_job, "RUNNING")
        self.assertTrue(local_exec.cancel(long_job))
        self.wait_state(long_job, "CANCELLED")

        after = local_exec.reserve_job_id()       # the slot must be usable again
        local_exec.submit(verdict_script(self.scene, after), job_id=after)
        self.assertEqual(self.wait_state(after, "COMPLETED"), "COMPLETED")

    def test_cancel_unknown_job_is_false(self):
        self.assertFalse(local_exec.cancel(999))


class TestWorkDir(LocalExecBase):
    def test_new_work_dir_wins_and_legacy_still_falls_back(self):
        new = self.root / "new-work"
        new.mkdir()
        os.environ["SR_WORK_DIR"] = str(new)
        self.assertEqual(sr_runtime().work_dir, str(new))
        self.assertEqual(local_exec.reserve_job_id() and
                         (new / local_exec.SEQ_FILE).exists(), True)
        os.environ.pop("SR_WORK_DIR")
        self.assertEqual(sr_runtime().work_dir, str(self.work))


class TestDurability(LocalExecBase):
    def test_same_job_id_is_submitted_only_once(self):
        starts = self.scene / "starts.txt"
        jid = local_exec.reserve_job_id()
        script = durable_script(self.scene, jid, starts, sleep=0.2)
        local_exec.submit(script, job_id=jid)
        local_exec.submit(script, job_id=jid)      # replay of the same id
        self.assertEqual(self.wait_state(jid, "COMPLETED"), "COMPLETED")
        self.assertEqual(starts.read_text().splitlines(), [f"start-{jid}"])

    def test_running_child_is_adopted_after_restart(self):
        starts = self.scene / "starts.txt"
        jid = local_exec.reserve_job_id()
        local_exec.submit(durable_script(self.scene, jid, starts, sleep=2.0),
                          job_id=jid)
        self.assertEqual(self.wait_state(jid, "RUNNING"), "RUNNING")
        local_exec._reset()                        # API process restarted
        recovered = local_exec.recover()
        self.assertEqual(recovered["adopted"], 1)
        self.assertEqual(self.status(jid)["state"], "RUNNING")
        self.assertEqual(self.wait_state(jid, "COMPLETED", timeout=20),
                         "COMPLETED")
        self.assertEqual(starts.read_text().splitlines(), [f"start-{jid}"])

    def test_queued_job_resumes_after_restart_without_a_second_start(self):
        starts = self.scene / "starts.txt"
        blocker = local_exec.reserve_job_id()
        local_exec.submit(verdict_script(self.scene, blocker, sleep=2.0),
                          job_id=blocker)
        queued = local_exec.reserve_job_id()
        local_exec.submit(durable_script(self.scene, queued, starts),
                          job_id=queued)
        self.assertEqual(self.wait_state(blocker, "RUNNING"), "RUNNING")
        self.assertEqual(self.status(queued)["state"], "PENDING")

        local_exec._reset()                        # API process restarted
        recovered = local_exec.recover()
        self.assertEqual(recovered["adopted"], 1)
        self.assertEqual(recovered["resumed"], 1)
        self.assertEqual(self.wait_state(queued, "COMPLETED", timeout=20),
                         "COMPLETED")
        self.assertEqual(starts.read_text().splitlines(), [f"start-{queued}"])

    def test_an_interrupted_start_is_not_automatically_resubmitted(self):
        starts = self.scene / "starts.txt"
        jid = local_exec.reserve_job_id()
        script = durable_script(self.scene, jid, starts)
        now = time.time()
        (self.work / local_exec.STATE_FILE).write_text(
            '{"version": 1, "jobs": [{"job_id": %d, "script": "%s", '
            '"log": "%s", "pid": null, "state": "starting", '
            '"timed_out": false, "created_at": %s, "updated_at": %s}]}'
            % (jid, script.as_posix(), (self.work / "x.out").as_posix(),
               now, now), encoding="utf-8")
        local_exec._reset()
        recovered = local_exec.recover()
        self.assertEqual(recovered["resumed"], 0)
        self.assertFalse(starts.exists())
        self.assertEqual(self.status(jid)["state"], "UNKNOWN")

    def test_persist_failure_before_start_refuses_to_launch(self):
        starts = self.scene / "starts.txt"
        jid = local_exec.reserve_job_id()
        script = durable_script(self.scene, jid, starts)
        with mock.patch.object(local_exec, "_persist_locked",
                               return_value=False):
            with self.assertRaises(RuntimeError):
                local_exec.submit(script, job_id=jid)
        time.sleep(0.3)
        self.assertFalse(starts.exists())
        self.assertNotIn(jid, local_exec._JOBS)

    def test_persist_failure_after_popen_quarantines_and_blocks_the_next_scene(self):
        first_starts = self.scene / "first.txt"
        second_starts = self.scene / "second.txt"
        first = local_exec.reserve_job_id()
        second = local_exec.reserve_job_id()
        real_persist = local_exec._persist_locked
        calls = {"n": 0}

        def flaky():
            calls["n"] += 1
            if calls["n"] == 3:          # queued -> starting -> running
                return False
            return real_persist()

        with mock.patch.object(local_exec, "_persist_locked",
                               side_effect=flaky):
            local_exec.submit(durable_script(self.scene, first, first_starts,
                                              sleep=30), job_id=first)
            self.assertEqual(self.wait_state(first, "UNKNOWN", timeout=20),
                             "UNKNOWN")
        time.sleep(0.2)
        self.assertFalse(local_exec._pid_alive(
            local_exec._JOBS[first].get("pid")))
        self.assertFalse(local_exec._DISPATCH_BLOCKED.is_set())
        local_exec.resolve_quarantine(first)
        # A second scene awaits the single slot in the same process.
        local_exec.submit(durable_script(self.scene, second, second_starts),
                          job_id=second)
        self.assertEqual(self.wait_state(second, "COMPLETED", timeout=20),
                         "COMPLETED")
        self.assertTrue(second_starts.exists())

        blocker = local_exec.reserve_job_id()
        local_exec.submit(verdict_script(self.scene, blocker, sleep=2.0),
                          job_id=blocker)
        self.assertEqual(self.wait_state(blocker, "RUNNING"), "RUNNING")
        third_starts = self.scene / "third.txt"
        third = local_exec.reserve_job_id()
        local_exec.submit(durable_script(self.scene, third, third_starts),
                          job_id=third)
        self.assertEqual(self.status(third)["state"], "PENDING")
        # Recreate the crash window: the durable file says `starting` for the
        # first job while the third job is queued behind it.
        state = json.loads((self.work / local_exec.STATE_FILE).read_text(
            encoding="utf-8"))
        for row in state["jobs"]:
            if row["job_id"] == first:
                row.update(state="starting", pid=None, pid_start=None,
                           updated_at=time.time())
        (self.work / local_exec.STATE_FILE).write_text(
            json.dumps(state), encoding="utf-8")
        local_exec._reset()
        recovered = local_exec.recover()
        self.assertEqual(recovered["quarantined"], 1)
        time.sleep(0.5)
        self.assertFalse(third_starts.exists(), "quarantined slot was reused")
        self.assertEqual(self.status(third)["state"], "PENDING")
        self.assertEqual(local_exec.resolve_quarantine(first), 1)
        self.assertEqual(self.wait_state(third, "COMPLETED", timeout=20),
                         "COMPLETED")
        self.assertEqual(third_starts.read_text().splitlines(),
                         [f"start-{third}"])

    def test_quarantine_releases_when_the_verdict_appears(self):
        jid = local_exec.reserve_job_id()
        now = time.time()
        row = {
            "job_id": jid, "script": str(self.scene / "late.sh"),
            "log": str(self.work / "late.out"), "pid": None,
            "pid_start": None, "state": "starting",
            "timed_out": False, "cancel_requested": False,
            "created_at": now, "started_at": None, "updated_at": now,
        }
        (self.work / local_exec.STATE_FILE).write_text(
            json.dumps({"version": 1, "jobs": [row]}), encoding="utf-8")
        debug = self.scene / "Debug"
        debug.mkdir()
        (debug / f"_SREXIT_{jid}.txt").write_text(
            f"job_id={jid}\nsr_exit_code=0\nverdict=0\nskip=0\n",
            encoding="utf-8")
        local_exec._reset()
        self.assertEqual(local_exec.recover()["quarantined"], 1)
        self.assertFalse(local_exec._DISPATCH_BLOCKED.is_set())
        self.assertEqual(self.status(jid)["state"], "COMPLETED")
        self.assertTrue(local_exec._DISPATCH_BLOCKED.is_set())

    def test_pid_reuse_is_not_adopted_or_killed(self):
        jid = local_exec.reserve_job_id()
        now = time.time()
        row = {
            "job_id": jid, "script": str(self.scene / "other.sh"),
            "log": str(self.work / "other.out"), "pid": os.getpid(),
            "pid_start": "not-this-process", "state": "running",
            "timed_out": False, "cancel_requested": False,
            "created_at": now, "started_at": now, "updated_at": now,
        }
        (self.work / local_exec.STATE_FILE).write_text(
            json.dumps({"version": 1, "jobs": [row]}), encoding="utf-8")
        local_exec._reset()
        recovered = local_exec.recover()
        self.assertEqual(recovered["adopted"], 0)
        self.assertEqual(recovered["quarantined"], 0)
        self.assertEqual(self.status(jid)["state"], "UNKNOWN")
        self.assertTrue(local_exec._pid_alive(os.getpid()))
        self.assertTrue(local_exec.cancel(jid))       # must not signal it
        self.assertTrue(local_exec._pid_alive(os.getpid()))

    def test_cancel_intent_survives_restart_and_keeps_cleaning(self):
        starts = self.scene / "starts.txt"
        jid = local_exec.reserve_job_id()
        local_exec.submit(durable_script(self.scene, jid, starts, sleep=30),
                          job_id=jid)
        self.assertEqual(self.wait_state(jid, "RUNNING"), "RUNNING")
        pid = local_exec._JOBS[jid]["pid"]
        with mock.patch.object(local_exec, "_terminate"):   # pretend the
            self.assertTrue(local_exec.cancel(jid))         # kill is delayed
        state = json.loads((self.work / local_exec.STATE_FILE).read_text(
            encoding="utf-8"))
        saved = next(row for row in state["jobs"] if row["job_id"] == jid)
        self.assertTrue(saved["cancel_requested"])
        self.assertTrue(local_exec._pid_alive(pid))

        local_exec._reset()
        local_exec.recover()
        self.assertEqual(self.wait_state(jid, "CANCELLED", timeout=20),
                         "CANCELLED")
        deadline = time.time() + 10
        while local_exec._pid_alive(pid) and time.time() < deadline:
            time.sleep(0.05)
        self.assertFalse(local_exec._pid_alive(pid))

    def test_cancel_an_adopted_child_kills_the_tree(self):
        starts = self.scene / "starts.txt"
        jid = local_exec.reserve_job_id()
        local_exec.submit(durable_script(self.scene, jid, starts, sleep=30),
                          job_id=jid)
        self.assertEqual(self.wait_state(jid, "RUNNING"), "RUNNING")
        pid = local_exec._JOBS[jid]["pid"]
        local_exec._reset()
        local_exec.recover()
        self.assertTrue(local_exec.cancel(jid))
        self.assertEqual(self.wait_state(jid, "CANCELLED", timeout=20),
                         "CANCELLED")
        deadline = time.time() + 10
        while local_exec._pid_alive(pid) and time.time() < deadline:
            time.sleep(0.05)
        self.assertFalse(local_exec._pid_alive(pid))

    def test_timeout_kills_the_child_and_reports_timeout(self):
        os.environ["SR_LOCAL_JOB_TIMEOUT_SEC"] = "0.3"
        starts = self.scene / "starts.txt"
        jid = local_exec.reserve_job_id()
        local_exec.submit(durable_script(self.scene, jid, starts, sleep=30),
                          job_id=jid)
        self.assertEqual(self.wait_state(jid, "TIMEOUT", timeout=20), "TIMEOUT")
        self.assertEqual(starts.read_text().splitlines(), [f"start-{jid}"])


class TestPreflight(LocalExecBase):
    def setUp(self):
        super().setUp()
        self.bundle = self.root / "bundle"
        self.bundle.mkdir()
        (self.bundle / "code_0817_prod.py").write_text("# entry\n", encoding="utf-8")
        self.model = self.root / "Espan3.pth"
        self.model.write_bytes(b"weights")
        self.options = self.root / "options.yml"
        self.options.write_text(
            f"pretrain_model_G: {self.model.as_posix()}\n", encoding="utf-8")
        os.environ["SR_WORK_DIR"] = str(self.work)
        os.environ["SR_BUNDLE_DIR"] = str(self.bundle)
        os.environ["SR_PYTHON"] = sys.executable
        os.environ["SR_SR_SCRIPT"] = "code_0817_prod.py"
        patcher = mock.patch("backend.services.local_exec.SR_DEFAULT_OPTIONS_YML",
                             str(self.options))
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ["SR_PREFLIGHT_IMPORTS"] = "json"
        os.environ["SR_LOCAL_GPU"] = "0"
        os.environ["SR_NVIDIA_SMI"] = "fake-nvidia-smi"
        local_exec.reset_preflight()

    def _run(self, *, gpu_stdout="0, GPU-abc\n", gpu_timeout=False):
        real_run = subprocess.run

        def fake_run(cmd, **kwargs):
            if cmd and cmd[0] == "fake-nvidia-smi":
                if gpu_timeout:
                    raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout"))
                return SimpleNamespace(returncode=0, stdout=gpu_stdout,
                                       stderr="")
            return real_run(cmd, **kwargs)

        with mock.patch("backend.services.local_exec.subprocess.run",
                        side_effect=fake_run):
            return local_exec.preflight(refresh=True)

    def test_preflight_passes_and_is_cached(self):
        result = self._run()
        self.assertTrue(result["ok"], result)
        self.assertIsNone(result["reason"])
        self.assertEqual(set(result["checks"]),
                         {"bash", "work_dir", "python", "algorithm",
                          "model", "gpu"})
        with mock.patch("backend.services.local_exec._run_preflight",
                        side_effect=AssertionError("preflight repeated")):
            self.assertIs(local_exec.preflight(), result)

    def test_python_import_failure_is_reported(self):
        os.environ["SR_PREFLIGHT_IMPORTS"] = "module_that_does_not_exist"
        result = self._run()
        self.assertFalse(result["ok"])
        self.assertIn("SR_PYTHON 无法导入", result["reason"])

    def test_missing_model_weight_is_reported(self):
        self.model.unlink()
        result = self._run()
        self.assertFalse(result["ok"])
        self.assertIn("模型权重不存在", result["reason"])

    def test_gpu_probe_timeout_is_reported(self):
        result = self._run(gpu_timeout=True)
        self.assertFalse(result["ok"])
        self.assertIn("GPU 检查超时", result["reason"])

    def test_missing_work_dir_is_reported(self):
        os.environ["SR_WORK_DIR"] = str(self.root / "missing")
        result = self._run()
        self.assertFalse(result["ok"])
        self.assertIn("工作目录不存在或不可写", result["reason"])


if __name__ == "__main__":
    unittest.main()
