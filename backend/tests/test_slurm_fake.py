"""Tests for the fake scheduler (api-contract.md §5.2, SR_SLURM_FAKE=1).

The scheduler layer is replaced by an in-memory registry driven by a fake
monotonic clock, so the PENDING→RUNNING→COMPLETED progression is deterministic
and instant (no real sleeps). Everything upstream — submit_run_sr path
validation, config.xml/batch writes, the sr_tasks table — runs the real code
under test in test_run_sr.py.
"""

import os
import unittest
from unittest import mock

from backend.services import slurm


class _Clock:
    """Mutable monotonic clock for deterministic fake-stage transitions."""

    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class FakeSchedulerTest(unittest.TestCase):
    def setUp(self):
        os.environ["SR_SLURM_FAKE"] = "1"
        os.environ["SR_SLURM_FAKE_T_MS"] = "1000"   # 1s per PENDING/RUNNING stage
        slurm._fake_reset()
        self.clock = _Clock()
        self._mono = mock.patch("backend.services.slurm.time.monotonic",
                                self.clock)
        self._mono.start()

    def tearDown(self):
        self._mono.stop()
        os.environ.pop("SR_SLURM_FAKE", None)
        os.environ.pop("SR_SLURM_FAKE_T_MS", None)
        slurm._fake_reset()

    def test_fake_makes_slurm_available_without_sbatch(self):
        self.assertTrue(slurm.slurm_available())

    def test_submit_pending_running_completed(self):
        jid = slurm.sbatch_submit("/w/run.sh")
        self.assertEqual(jid, 1)

        st = slurm.job_status(jid)                     # t=0 → PENDING
        self.assertTrue(st["active"])
        self.assertEqual(st["state"], "PENDING")

        self.clock.advance(1.0)                        # t=1s → RUNNING
        st = slurm.job_status(jid)
        self.assertTrue(st["active"])
        self.assertEqual(st["state"], "RUNNING")

        self.clock.advance(1.0)                        # t=2s → COMPLETED
        st = slurm.job_status(jid)
        self.assertFalse(st["active"])
        self.assertEqual(st["state"], "COMPLETED")
        self.assertEqual(st["exit_code"], "0:0")

    def test_squeue_view_follows_progression(self):
        jid = slurm.sbatch_submit("/w/run.sh")
        self.assertEqual(slurm.squeue_status(jid), "PENDING")
        self.clock.advance(1.0)
        self.assertEqual(slurm.squeue_status(jid), "RUNNING")
        self.clock.advance(1.0)
        self.assertIsNone(slurm.squeue_status(jid))    # left the queue
        state, code = slurm.sacct_status(jid)
        self.assertEqual(state, "COMPLETED")

    def test_cancel_sets_cancelled(self):
        jid = slurm.sbatch_submit("/w/run.sh")
        self.assertTrue(slurm.cancel(jid))
        # cancelled job leaves squeue → sacct reports CANCELLED
        self.assertIsNone(slurm.squeue_status(jid))
        state, _ = slurm.sacct_status(jid)
        self.assertEqual(state, "CANCELLED")

    def test_cancel_unknown_job_false(self):
        self.assertFalse(slurm.cancel(999))

    def test_unknown_job_is_unknown_state(self):
        st = slurm.job_status(42)
        self.assertIsNone(st["active"])
        self.assertEqual(st["state"], "UNKNOWN")

    def test_job_ids_monotonic(self):
        a = slurm.sbatch_submit("/w/a.sh")
        b = slurm.sbatch_submit("/w/b.sh")
        self.assertEqual((a, b), (1, 2))


if __name__ == "__main__":
    unittest.main()
