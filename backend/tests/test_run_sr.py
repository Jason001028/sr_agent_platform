"""Tests for run_sr (config XML, batch script, Slurm submit, status poll).

The dev machine has no Slurm, so "unavailable" paths are tested against the
real host; happy paths inject a fake command runner (run_cmd seam) and patch
slurm_available.
"""

import os
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest import mock

from backend.services import run_sr as svc
from backend.services import slurm
from backend.tools.run_sr import run_run_sr


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
                try:
                    fake = fake_run_script(("Submitted batch job 42\n", "", 0))
                    data = svc.submit_run_sr(
                        {"lq_path": "/data", "suffix": "t1"}, run_cmd=fake)
                    self.assertEqual(data["job_id"], 42)
                    self.assertEqual(data["status"], "SUBMITTED")
                    self.assertTrue(Path(data["config_xml"]).exists())
                    self.assertTrue(Path(data["batch_script"]).exists())
                    script = Path(data["batch_script"]).read_text(encoding="utf-8")
                    self.assertIn("--partition=gpu", script)
                finally:
                    del os.environ["SR_SLURM_WORK_DIR"]
                    del os.environ["SR_SLURM_PARTITION"]

    def test_sbatch_failure_raises(self):
        with mock.patch.object(slurm, "slurm_available", lambda: True):
            with tempfile.TemporaryDirectory() as d:
                os.environ["SR_SLURM_WORK_DIR"] = d
                try:
                    fake = fake_run_script(("", "invalid account", 1))
                    with self.assertRaises(RuntimeError) as ctx:
                        svc.submit_run_sr({"lq_path": "/data"}, run_cmd=fake)
                    self.assertIn("sbatch failed", str(ctx.exception))
                finally:
                    del os.environ["SR_SLURM_WORK_DIR"]


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
