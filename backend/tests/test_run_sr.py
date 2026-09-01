"""Tests for run_sr (config XML, batch script, Slurm submit, status poll).

The dev machine has no Slurm, so "unavailable" paths are tested against the
real host; happy paths inject a fake command runner (run_cmd seam) and patch
slurm_available.
"""

import json
import os
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest import mock

from backend.services import run_sr as svc
from backend.services import slurm
from backend.services import store as store_mod
from backend.tools.run_sr import run_run_sr

# full param dict shaped like the tool's, for fingerprint-stable submits
PARAMS = {"lq_path": "/data", "mask_path": None, "sr_scale": 2, "suffix": "t1",
          "gpu": 0, "cloud_limit": 80, "delete_ori": False, "grid_align": True,
          "options_yml": None}


def gradient_tif(path):
    import numpy as np
    from PIL import Image
    y, x = np.mgrid[0:8, 0:8]
    Image.fromarray((y + x).astype(np.uint16)).save(path)


def fake_run_script(*results):
    """Command runner returning canned CompletedProcess in call order."""
    it = iter(results)

    def _run(cmd, timeout=30):
        out, err, rc = next(it)
        return subprocess.CompletedProcess(cmd, rc, stdout=out, stderr=err)

    return _run


def dispatch_runner(sbatch_outputs=("Submitted batch job 42\n",),
                    squeue_out="", sacct_out=""):
    """Runner that dispatches on the subcommand, records calls, and yields
    sbatch outputs in order. squeue_out/sacct_out feed the status polls."""
    calls = []
    it = iter(sbatch_outputs)

    def _run(cmd, timeout=30):
        name = cmd[0]
        calls.append(name)
        if name == "sbatch":
            out = next(it)
            return subprocess.CompletedProcess(cmd, 0, stdout=out, stderr="")
        if name == "squeue":
            return subprocess.CompletedProcess(cmd, 0, stdout=squeue_out, stderr="")
        if name == "sacct":
            return subprocess.CompletedProcess(cmd, 0, stdout=sacct_out, stderr="")
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="unknown cmd")

    _run.calls = calls
    return _run


def temp_store():
    """A Store on a temp path, hermetic per test."""
    d = tempfile.TemporaryDirectory()
    return store_mod.Store(os.path.join(d.name, "db.sqlite")), d


class TestBuildConfigXml(unittest.TestCase):
    def test_required_tags(self):
        xml = svc.build_config_xml({"lq_path": "/data/scene"})
        root = ET.fromstring(xml)
        tags = {e.tag for e in root}
        self.assertIn("DatarootLQ", tags)
        self.assertIn("SRScale", tags)
        self.assertIn("OPT", tags)
        self.assertNotIn("MaskPath", tags)          # optional, omitted
        self.assertNotIn("GridAlign", tags)         # default on, omitted

    def test_mask_and_grid_align(self):
        xml = svc.build_config_xml({"lq_path": "/s", "mask_path": "/s/m.tif",
                                    "grid_align": False})
        root = ET.fromstring(xml)
        tags = {e.tag: e.text for e in root}
        self.assertEqual(tags["MaskPath"], "/s/m.tif")
        self.assertEqual(tags["GridAlign"], "false")

    def test_values(self):
        xml = svc.build_config_xml({"lq_path": "/s", "sr_scale": 4,
                                    "suffix": "abc", "gpu": 2,
                                    "cloud_limit": 90, "delete_ori": True})
        root = ET.fromstring(xml)
        tags = {e.tag: e.text for e in root}
        self.assertEqual(tags["DatarootLQ"], "/s")
        self.assertEqual(tags["SRScale"], "4")
        self.assertEqual(tags["Suffix"], "abc")
        self.assertEqual(tags["GPUIDS"], "2")
        self.assertEqual(tags["CloudLimit"], "90")
        self.assertEqual(tags["DeleteOriTifNeeded"], "True")

    def test_well_formed(self):
        xml = svc.build_config_xml({"lq_path": "/s"})
        ET.fromstring(xml)                          # must not raise


class TestBuildBatchScript(unittest.TestCase):
    def test_defaults(self):
        script = svc.build_batch_script("/w/cfg.xml", "/w")
        self.assertIn("--gres=gpu:1", script)
        self.assertIn("cd /DiskArray/ProductionSchedule", script)
        self.assertIn("python code_0817_prod.py -f /w/cfg.xml", script)
        self.assertNotIn("--partition", script)

    def test_partition_and_python(self):
        script = svc.build_batch_script("/w/cfg.xml", "/w", python="/opt/venv/bin/python",
                                        partition="gpu", gres=4)
        self.assertIn("--gres=gpu:4", script)
        self.assertIn("--partition=gpu", script)
        self.assertIn("/opt/venv/bin/python", script)


class TestSubmitRunSr(unittest.TestCase):
    def test_unavailable_raises_clear_error(self):
        # real host: no sbatch on the dev machine
        with self.assertRaises(RuntimeError) as ctx:
            svc.submit_run_sr({"lq_path": "/data"})
        self.assertIn("slurm not available", str(ctx.exception))

    def test_happy_path_with_fake_sbatch(self):
        with mock.patch.object(slurm, "slurm_available", lambda: True):
            with tempfile.TemporaryDirectory() as d:
                os.environ["SR_SLURM_WORK_DIR"] = d
                os.environ["SR_SLURM_PARTITION"] = "gpu"
                store, tmp = temp_store()
                try:
                    fake = fake_run_script(("Submitted batch job 42\n", "", 0))
                    data = svc.submit_run_sr(
                        {"lq_path": "/data", "suffix": "t1"}, run_cmd=fake,
                        store=store)
                    self.assertEqual(data["job_id"], 42)
                    self.assertEqual(data["status"], "SUBMITTED")
                    self.assertTrue(Path(data["config_xml"]).exists())
                    self.assertTrue(Path(data["batch_script"]).exists())
                    script = Path(data["batch_script"]).read_text(encoding="utf-8")
                    self.assertIn("--partition=gpu", script)
                finally:
                    del os.environ["SR_SLURM_WORK_DIR"]
                    del os.environ["SR_SLURM_PARTITION"]
                    store.close()
                    tmp.cleanup()

    def test_sbatch_failure_raises(self):
        with mock.patch.object(slurm, "slurm_available", lambda: True):
            with tempfile.TemporaryDirectory() as d:
                os.environ["SR_SLURM_WORK_DIR"] = d
                store, tmp = temp_store()
                try:
                    fake = fake_run_script(("", "invalid account", 1))
                    with self.assertRaises(RuntimeError) as ctx:
                        svc.submit_run_sr({"lq_path": "/data"}, run_cmd=fake,
                                          store=store)
                    self.assertIn("sbatch failed", str(ctx.exception))
                finally:
                    del os.environ["SR_SLURM_WORK_DIR"]
                    store.close()
                    tmp.cleanup()


class TestRunSrIdempotency(unittest.TestCase):
    """P0② — the sr_tasks table prevents duplicate submission on replay."""

    def setUp(self):
        patcher = mock.patch.object(slurm, "slurm_available", lambda: True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self._tmp = tempfile.TemporaryDirectory()
        self.store = store_mod.Store(os.path.join(self._tmp.name, "db.sqlite"))
        # addCleanup runs LIFO: register tmp.cleanup first so the SQLite handle
        # (store.close) is released before the temp dir is removed (Windows).
        self.addCleanup(self._tmp.cleanup)
        self.addCleanup(self.store.close)

    def test_fingerprint_stable_under_key_order(self):
        a = svc.task_fingerprint({"x": 1, "y": [1, 2]})
        b = svc.task_fingerprint({"y": [1, 2], "x": 1})
        self.assertEqual(a, b)

    def test_first_submit_creates_task_with_job_id(self):
        runner = dispatch_runner()
        data = svc.submit_run_sr(PARAMS, run_cmd=runner, store=self.store)
        self.assertEqual(data["status"], "SUBMITTED")
        self.assertEqual(data["job_id"], 42)
        task = self.store.get_sr_task(svc.task_fingerprint(PARAMS))
        self.assertIsNotNone(task)
        self.assertEqual(task["job_id"], 42)
        self.assertEqual(task["status"], "submitted")

    def test_replay_active_reuses_no_resubmit(self):
        # first submit lands job 42; a replay finds it RUNNING → reuse.
        first = dispatch_runner()
        svc.submit_run_sr(PARAMS, run_cmd=first, store=self.store)

        runner = dispatch_runner(squeue_out="RUNNING\n")
        data = svc.submit_run_sr(PARAMS, run_cmd=runner, store=self.store)
        self.assertEqual(data["status"], "RESUMED_ACTIVE")
        self.assertEqual(data["job_id"], 42)
        self.assertEqual(data["state"], "RUNNING")
        self.assertTrue(data["idempotent"])
        self.assertEqual(runner.calls, ["squeue"])     # no sbatch, no sacct

    def test_replay_completed_reuses_no_resubmit(self):
        first = dispatch_runner()
        svc.submit_run_sr(PARAMS, run_cmd=first, store=self.store)

        runner = dispatch_runner(sacct_out="COMPLETED 0:0\n")
        data = svc.submit_run_sr(PARAMS, run_cmd=runner, store=self.store)
        self.assertEqual(data["status"], "RESUMED_COMPLETED")
        self.assertEqual(data["job_id"], 42)
        self.assertTrue(data["idempotent"])
        self.assertEqual(runner.calls, ["squeue", "sacct"])  # no second sbatch

    def test_replay_failed_reruns_with_new_job(self):
        first = dispatch_runner()
        svc.submit_run_sr(PARAMS, run_cmd=first, store=self.store)

        runner = dispatch_runner(sbatch_outputs=("Submitted batch job 43\n",),
                                 sacct_out="FAILED 1:0\n")
        data = svc.submit_run_sr(PARAMS, run_cmd=runner, store=self.store)
        self.assertEqual(data["status"], "SUBMITTED")
        self.assertEqual(data["job_id"], 43)          # a fresh job, not 42
        self.assertEqual(data["previous_state"], "FAILED")
        self.assertEqual(data["previous_exit_code"], "1:0")
        self.assertEqual(data["previous_job_id"], 42)
        self.assertEqual(runner.calls, ["squeue", "sacct", "sbatch"])

    def test_replay_unknown_reruns(self):
        first = dispatch_runner()
        svc.submit_run_sr(PARAMS, run_cmd=first, store=self.store)

        runner = dispatch_runner(sbatch_outputs=("Submitted batch job 43\n",))
        data = svc.submit_run_sr(PARAMS, run_cmd=runner, store=self.store)
        self.assertEqual(data["status"], "SUBMITTED")
        self.assertEqual(data["job_id"], 43)
        self.assertEqual(data["previous_state"], "UNKNOWN")
        self.assertEqual(runner.calls, ["squeue", "sacct", "sbatch"])

    def test_interrupted_submit_no_job_id_does_not_resubmit(self):
        # intent recorded, sbatch outcome never persisted → outcome unknown.
        self.store.put_sr_task(svc.task_fingerprint(PARAMS), PARAMS,
                               status="new", job_id=None)
        runner = dispatch_runner()
        with self.assertRaises(RuntimeError) as ctx:
            svc.submit_run_sr(PARAMS, run_cmd=runner, store=self.store)
        self.assertIn("interrupted", str(ctx.exception))
        self.assertEqual(runner.calls, [])            # no scheduler call, no sbatch

    def test_different_params_are_distinct_jobs(self):
        params_a = {**PARAMS, "suffix": "a"}
        params_b = {**PARAMS, "suffix": "b"}
        runner = dispatch_runner(sbatch_outputs=("Submitted batch job 42\n",
                                                 "Submitted batch job 43\n"))
        a = svc.submit_run_sr(params_a, run_cmd=runner, store=self.store)
        b = svc.submit_run_sr(params_b, run_cmd=runner, store=self.store)
        self.assertEqual(a["job_id"], 42)
        self.assertEqual(b["job_id"], 43)
        self.assertIsNotNone(
            self.store.get_sr_task(svc.task_fingerprint(params_a)))
        self.assertIsNotNone(
            self.store.get_sr_task(svc.task_fingerprint(params_b)))
        self.assertEqual(runner.calls.count("sbatch"), 2)


class TestSlurmStatus(unittest.TestCase):
    def test_active_from_squeue(self):
        with mock.patch.object(slurm, "slurm_available", lambda: True):
            fake = fake_run_script(("PENDING\n", "", 0))
            st = slurm.job_status(7, run_cmd=fake)
            self.assertEqual(st, {"job_id": 7, "active": True,
                                  "state": "PENDING", "exit_code": None})

    def test_terminal_from_sacct(self):
        with mock.patch.object(slurm, "slurm_available", lambda: True):
            fake = fake_run_script(("", "", 0), ("COMPLETED 0:0\n", "", 0))
            st = slurm.job_status(7, run_cmd=fake)
            self.assertEqual(st["active"], False)
            self.assertEqual(st["state"], "COMPLETED")
            self.assertEqual(st["exit_code"], "0:0")

    def test_unknown(self):
        with mock.patch.object(slurm, "slurm_available", lambda: True):
            fake = fake_run_script(("", "", 0), ("", "", 0))
            st = slurm.job_status(7, run_cmd=fake)
            self.assertEqual(st["state"], "UNKNOWN")
            self.assertIsNone(st["active"])

    def test_cancel_ok(self):
        with mock.patch.object(slurm, "slurm_available", lambda: True):
            fake = fake_run_script(("", "", 0))
            self.assertTrue(slurm.cancel(7, run_cmd=fake))


class TestToolRunSr(unittest.TestCase):
    def test_missing_lq_path(self):
        r = run_run_sr()
        self.assertFalse(r["ok"])
        self.assertIn("lq_path", r["error"])

    def test_bad_sr_scale(self):
        r = run_run_sr(lq_path="/data", sr_scale=0)
        self.assertFalse(r["ok"])
        self.assertIn("sr_scale", r["error"])

    def test_valid_but_slurm_unavailable(self):
        # dev machine: real slurm_available() is False → clean err
        r = run_run_sr(lq_path="/data")
        self.assertFalse(r["ok"])
        self.assertIn("slurm", r["error"])


class TestToolRunSrPathGuard(unittest.TestCase):
    """P0③ — run_sr must reject search_scenes fake / non-absolute paths."""

    def test_fake_lq_path_rejected(self):
        r = run_run_sr(lq_path="<fake>/GF07A03_PMS01_20260722125045.tif")
        self.assertFalse(r["ok"])
        self.assertIn("lq_path", r["error"])
        self.assertIn("fake", r["error"])

    def test_relative_lq_path_rejected(self):
        r = run_run_sr(lq_path="GF07A03_20260722.tif")
        self.assertFalse(r["ok"])
        self.assertIn("absolute", r["error"])

    def test_fake_mask_path_rejected(self):
        r = run_run_sr(lq_path="/DiskArray/real/scene",
                       mask_path="<fake>/mask.tif")
        self.assertFalse(r["ok"])
        self.assertIn("mask_path", r["error"])
        self.assertIn("fake", r["error"])

    def test_relative_mask_path_rejected(self):
        r = run_run_sr(lq_path="/DiskArray/real/scene", mask_path="mask.tif")
        self.assertFalse(r["ok"])
        self.assertIn("mask_path", r["error"])
        self.assertIn("absolute", r["error"])

    def test_valid_paths_reach_the_service(self):
        # valid absolute paths pass validation → the err is the host's "no
        # slurm" (dev machine), proving the path guard did not reject.
        r = run_run_sr(lq_path="/DiskArray/real/scene",
                       mask_path="/DiskArray/real/mask.tif")
        self.assertFalse(r["ok"])
        self.assertIn("slurm", r["error"])

    def test_registry_dispatch_is_the_guarded_tool(self):
        # the loop executes tools through the registry (call_tool → t.run),
        # not run_run_sr directly — lock that the registered "run_sr" is the
        # path-guarded wrapper.
        from backend.agent.loop import call_tool
        r = call_tool("run_sr",
                      json.dumps({"lq_path": "<fake>/GF07A03_PMS01.tif"}))
        self.assertFalse(r["ok"])
        self.assertIn("fake", r["error"])


class TestToolJobStatus(unittest.TestCase):
    def test_missing_job_id(self):
        from backend.tools.sr_job_status import run_sr_job_status
        r = run_sr_job_status()
        self.assertFalse(r["ok"])
        self.assertIn("job_id", r["error"])

    def test_slurm_unavailable(self):
        from backend.tools.sr_job_status import run_sr_job_status
        r = run_sr_job_status(job_id=1)
        self.assertFalse(r["ok"])
        self.assertIn("slurm", r["error"])


if __name__ == "__main__":
    unittest.main()
