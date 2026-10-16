"""Tests for the local executor (backend/services/local_exec.py).

No GPU, no real SR: every case runs a *short bash script* that stands in for the
generated run script, and the terminal state is always read back from a verdict
file the script writes itself — which is exactly the contract the real run
follows (docs/sr_code/sr-pipeline-interface.md §2.3).

Covered: the single execution slot (a second submit reports PENDING, not
RUNNING), the persisted job-id sequence, verdict-file-driven terminal states,
`cancel` for both queued and running jobs, the restart case (no in-memory
record, so the run record on disk and the verdict file answer together),
`release_unknown` as the operator's way out of an unconfirmable launch, a stale
slot lock, and the child environment the SR interpreter needs.
"""

import json
import os
import tempfile
import time
import unittest
from pathlib import Path

from backend.services import local_exec

#: Same resolution the executor uses (which skips the System32 WSL launcher on
#: Windows) — so "no bash here" skips instead of failing every case.
BASH = local_exec._bash_path()

#: Everything the executor's config reads. setUp clears all of them, so a value
#: left over on the developer's machine (SR_EXECUTOR, say) cannot steer a case.
_ENVS = ("SR_WORK_DIR", "SR_SLURM_WORK_DIR", "SR_EXECUTOR", "SR_PYTHON",
         "SR_LOCAL_GPU", "SR_BUNDLE_DIR", "SR_LOCKED_DIR")


def verdict_script(scene: Path, job_id: int, *, sleep: float = 0,
                   verdict: int = 0) -> Path:
    """A stand-in for the generated run script: does nothing but sleep and
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
        for name in _ENVS:
            os.environ.pop(name, None)
        os.environ["SR_WORK_DIR"] = str(self.work)
        local_exec._reset()
        self.addCleanup(self._restore)

    def _restore(self):
        self._kill_leftovers()
        local_exec._reset()
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
            if not any(rec["proc"] is not None and rec["proc"].poll() is None
                       for rec in list(local_exec._JOBS.values())):
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
                                        "UNKNOWN"}, timeout)


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
        COMPLETED。`SR_WORK_DIR` 默认在 `/tmp`，所以这不是角落情形。
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

    def test_the_run_record_names_the_log_file(self):
        # One log per run, named after the script (which carries the job id), and
        # the record is where the queue page finds it — a log the record does not
        # name is a log nobody can open.
        jid = local_exec.reserve_job_id()
        script = verdict_script(self.scene, jid)
        local_exec.submit(script, job_id=jid)
        self.wait_state(jid, "COMPLETED")
        log = self.work / f"{script.stem}.out"
        self.assertTrue(log.exists())
        self.assertEqual(local_exec.read_run_record(jid)["log"], str(log))


class TestTerminalState(LocalExecBase):
    def test_verdict_file_decides_completed(self):
        jid = local_exec.reserve_job_id()
        local_exec.submit(verdict_script(self.scene, jid), job_id=jid)
        self.wait_state(jid, "COMPLETED")
        st = self.status(jid)
        self.assertFalse(st["active"])
        self.assertEqual(st["exit_code"], "0:0")

    def test_a_zero_exit_without_a_verdict_is_failed_not_completed(self):
        # The silent-failure case the whole verdict-file design exists for:
        # exit code 0 says nothing (§2.3). The child is confirmed gone and left
        # no verdict, so the run produced nothing usable — FAILED, never
        # COMPLETED, and not UNKNOWN either: UNKNOWN is for "cannot tell", and
        # here the run record says the process ended.
        jid = local_exec.reserve_job_id()
        script = self.scene / "noop.sh"
        script.write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
        local_exec.submit(script, job_id=jid)
        self.assertEqual(self.wait_terminal(jid), "FAILED")

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

    def test_unlaunchable_script_is_failed_not_pending(self):
        jid = local_exec.reserve_job_id()
        local_exec.submit(self.scene / "ghost.sh", job_id=jid)   # never created
        # bash itself reports "No such file or directory" and exits 127, so the
        # child *does* run and end: no verdict file follows, which is the
        # contract's §2.3 silent-failure case, reported as FAILED (rerunnable).
        self.assertEqual(self.wait_terminal(jid), "FAILED")


class TestRunRecords(LocalExecBase):
    """What a run record on disk answers once this process has no memory of it.

    sr-api restarting mid-queue is the case these pin: a run that is over must
    not stay blocked forever, and a run whose launch cannot be confirmed must
    not be declared safe to rerun.
    """

    def record(self, job_id: int, **fields) -> None:
        data = {"job_id": job_id}
        data.update(fields)
        local_exec._write_run_record(self.work, job_id, data)

    def test_record_path_is_what_read_run_record_uses(self):
        self.record(6, state=local_exec._ST_QUEUED)
        self.assertIsNotNone(local_exec.read_run_record(6))

    def test_a_queued_record_says_failed_and_is_rerunnable(self):
        # The runner thread died with the API, so nothing will start this run.
        # Showing PENDING forever would be a lie with no way out.
        self.record(7, state=local_exec._ST_QUEUED)
        st = self.status(7)
        self.assertEqual(st["state"], "FAILED")
        self.assertIs(st["active"], False)
        self.assertIn("重投", st["note"])

    def test_a_launching_record_without_a_pid_is_unknown(self):
        # Popen may or may not have happened. FAILED here would be the platform
        # claiming "safe to rerun" about a run that may be writing the scene
        # right now, so the answer is UNKNOWN and `active` is not False.
        self.record(8, state=local_exec._ST_LAUNCHING)
        st = self.status(8)
        self.assertEqual(st["state"], "UNKNOWN")
        self.assertIsNone(st["active"])

    def test_a_finished_record_without_a_verdict_is_failed(self):
        self.record(9, state=local_exec._ST_DONE, outcome=local_exec._OUT_EXITED,
                    child_pid=999999999)
        st = self.status(9)
        self.assertEqual(st["state"], "FAILED")
        self.assertIs(st["active"], False)
        self.assertIn("无有效产物", st["note"])

    def test_a_running_child_from_a_previous_process_is_still_running(self):
        jid = local_exec.reserve_job_id()
        local_exec.submit(verdict_script(self.scene, jid, sleep=30), job_id=jid)
        self.assertEqual(self.wait_state(jid, "RUNNING"), "RUNNING")
        deadline = time.time() + 10.0
        while time.time() < deadline:             # pid is published right after
            child = (local_exec.read_run_record(jid) or {}).get("child_pid")
            if child:
                break
            time.sleep(0.05)
        self.assertTrue(child, "run record never named the child process")

        local_exec._reset()                      # sr-api restarted mid-run
        self.record(jid, state=local_exec._ST_RUNNING, child_pid=child,
                    child_identity=local_exec._proc_identity(child))
        st = self.status(jid)
        self.assertEqual(st["state"], "RUNNING")
        self.assertIs(st["active"], True)
        local_exec.cancel(jid)

    def test_release_unknown_clears_the_block(self):
        self.record(10, state=local_exec._ST_LAUNCHING)
        self.assertEqual(self.status(10)["state"], "UNKNOWN")

        self.assertTrue(local_exec.release_unknown(10, note="操作员已核实"))
        st = self.status(10)
        self.assertEqual(st["state"], "CANCELLED")   # no longer answers UNKNOWN
        self.assertIs(st["active"], False)
        self.assertEqual(st["note"], "操作员已核实")

    def test_release_unknown_writes_a_record_even_when_there_is_none(self):
        # An id the platform has no record for is exactly the case the escape
        # hatch exists for; refusing it would make UNKNOWN a dead end.
        self.assertFalse(local_exec._run_record_path(self.work, 11).exists())
        self.assertTrue(local_exec.release_unknown(11))
        self.assertEqual(self.status(11)["state"], "CANCELLED")


class TestSlotLock(LocalExecBase):
    def test_a_lock_whose_holder_is_gone_is_reclaimed(self):
        # A crash between taking the slot and launching leaves the lock file
        # behind. The slot must come back on its own, or nothing ever runs
        # again — but only once the holder is provably gone.
        payload = {"job_id": 1, "api_pid": 999999999, "api_identity": None,
                   "child_pid": 999999999, "child_identity": None}
        self.assertFalse(local_exec._lock_holder_alive(payload))
        (self.work / local_exec.LOCK_FILE).write_text(json.dumps(payload),
                                                      encoding="utf-8")

        jid = local_exec.reserve_job_id()
        local_exec.submit(verdict_script(self.scene, jid), job_id=jid)
        self.assertEqual(self.wait_state(jid, "COMPLETED"), "COMPLETED")

    def test_a_live_lock_is_respected(self):
        # The other direction: this process is alive and named in the lock, so
        # a submit must NOT barge in — that is the whole point of the lock.
        payload = {"job_id": 1, "api_pid": os.getpid(),
                   "api_identity": local_exec._proc_identity(os.getpid()),
                   "child_pid": None, "child_identity": None}
        self.assertTrue(local_exec._lock_holder_alive(payload))
        (self.work / local_exec.LOCK_FILE).write_text(json.dumps(payload),
                                                      encoding="utf-8")

        jid = local_exec.reserve_job_id()
        local_exec.submit(verdict_script(self.scene, jid), job_id=jid)
        self.assertEqual(self.wait_state(jid, "PENDING", timeout=3.0), "PENDING")


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


if __name__ == "__main__":
    unittest.main()
