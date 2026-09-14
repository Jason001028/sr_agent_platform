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

import os
import tempfile
import time
import unittest
from pathlib import Path

from backend.services import local_exec

#: Same resolution the executor uses (which skips the System32 WSL launcher on
#: Windows) — so "no bash here" skips instead of failing every case.
BASH = local_exec._bash_path()

_ENVS = ("SR_SLURM_WORK_DIR", "SR_PYTHON", "SR_LOCAL_GPU", "SR_BUNDLE_DIR")


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
        os.environ.pop("SR_LOCAL_GPU", None)
        os.environ.pop("SR_BUNDLE_DIR", None)
        os.environ.pop("SR_PYTHON", None)
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
        self.assertEqual(local_exec.reserve_job_id(), 1)
        self.assertEqual(local_exec.reserve_job_id(), 2)
        local_exec._reset()                      # sr-api restarted: memory gone
        self.assertEqual(local_exec.reserve_job_id(), 3)   # file carries on
        self.assertEqual((self.work / local_exec.SEQ_FILE).read_text().strip(), "3")

    def test_ids_are_never_reused(self):
        ids = {local_exec.reserve_job_id() for _ in range(20)}
        self.assertEqual(len(ids), 20)


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


if __name__ == "__main__":
    unittest.main()
