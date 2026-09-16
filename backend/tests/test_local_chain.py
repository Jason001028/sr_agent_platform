"""End-to-end rehearsal of the local (no-Slurm) submit chain.

Everything in this file is real except the *SR half*: `submit_run_sr` writes a
real config.xml and a real batch script with `build_batch_script`, `local_exec`
runs that script with a real bash, the real `SR_code/variants/verify_sr_run.py`
evaluates the contract inside the job, and the terminal state is read back from
the real verdict file. Only `code_0817_prod.py` is replaced by a stub that
produces the artefacts the contract checks (SUPER_RESOLUTION aside).

That is the point: the parts that have never run on the dev machine are the
*wiring* ones (config → script → bash → verifier → verdict file → status), and
those are exactly what this pins. What remains machine-specific afterwards is
the interpreter (conda py3.6 + torch/GDAL) and the GPU — not the chain.

Cross-checked with docs/planning/sr-minimal-prototype-plan.md §5.1 (the "new
tests" list) and §5.2 A-段 (the same steps, by hand, on node81-135).
"""

import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from backend.services import local_exec
from backend.services import run_sr as svc
from backend.services import store as store_mod

REPO_ROOT = Path(__file__).resolve().parents[2]
VERIFIER = REPO_ROOT / "SR_code" / "variants" / "verify_sr_run.py"

#: Name the batch script runs in place of the production SR script.
STUB_NAME = "stub_sr.py"

#: Stand-in for code_0817_prod.py — the SR half of the chain, artefacts only.
#:
#: Naming rules (SC vs RC input, suffix, Debug/) are re-derived here rather than
#: imported from verify_sr_run.py: the production script does not import the
#: verifier either, so importing it would only prove the verifier agrees with
#: itself. Env knobs: SR_STUB_SILENT=1 exits 0 without artefacts (the silent
#: failure the verdict file exists to catch), SR_STUB_SLEEP=<s> keeps the child
#: alive so RUNNING is observable.
STUB_SR = r'''#!/usr/bin/env python
"""Stub SR: writes what the in-job contract verifier checks. Not a real run."""

import os
import re
import sys
import time
import xml.etree.ElementTree as ET


def main(argv):
    cfg = argv[argv.index("-f") + 1]
    root = ET.parse(cfg).getroot()
    lq = (root.findtext("DatarootLQ") or "").replace("\\", "/")
    suffix = root.findtext("Suffix") or ""
    time.sleep(float(os.environ.get("SR_STUB_SLEEP") or 0))
    if os.environ.get("SR_STUB_SILENT") == "1":
        sys.stdout.write("stub: exit 0 without artefacts (silent failure)\n")
        return 0
    name = os.path.basename(lq.rstrip("/"))
    meta = ""
    meta_path = os.path.join(lq, name + "_meta.xml")
    if os.path.isfile(meta_path):
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = f.read()
    # util.get_l1_pan_tif_rcsc: an EMPTY <SolarAzimuth> means the RC step, whose
    # input is PAN.tif; anything else is SC and uses the directory name.
    stem = "PAN" if re.search(r"<SolarAzimuth>\s*</SolarAzimuth>", meta) else name
    debug = os.path.join(lq, "Debug")
    os.makedirs(debug, exist_ok=True)
    with open(os.path.join(debug, stem + "_SRLOG.txt"), "w", encoding="utf-8") as f:
        f.write("stub SR: simulated run\nRun finished.")
    out_name = stem + ("_" + suffix if suffix else "") + ".tif"
    with open(os.path.join(lq, out_name), "wb") as f:
        f.write(b"stub-output\n")
    sys.stdout.write("stub SR wrote %s\n" % out_name)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
'''

_REAL_BUILD_SCRIPT = svc.build_batch_script


def _host_script(*args, **kwargs) -> str:
    """`build_batch_script`, with separators made bash-safe for the dev host.

    The generator embeds host-native paths (`str(Path)`); the interpreter is
    bash, where a backslash is an escape, so a Windows-written script needs
    `C:/x`, not `C:\\x`. On the SR host (Linux) this replacement is the identity
    — the text the machine runs is untouched, which is why the adapter lives
    here in the test rather than in the generator.
    """
    return _REAL_BUILD_SCRIPT(*args, **kwargs).replace("\\", "/")


class LocalChainTests(unittest.TestCase):
    """submit → bash → stub SR → verifier → verdict file → terminal state."""

    def setUp(self):
        try:
            self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        except TypeError:                        # Python < 3.10
            self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.scene = root / "SCENE_L1_PAN"
        self.bundle = root / "bundle"
        self.work = root / "work"
        for d in (self.scene, self.bundle, self.work):
            d.mkdir(parents=True)
        # The job's two programs: the real verifier + the stub SR.
        shutil.copy(str(VERIFIER), str(self.bundle / "verify_sr_run.py"))
        (self.bundle / STUB_NAME).write_text(STUB_SR, encoding="utf-8")
        # A scene shaped like the pipeline's: input, meta (non-empty azimuth →
        # SC step), the mask the minimal prototype reads from the directory.
        (self.scene / "SCENE_L1_PAN_meta.xml").write_text(
            "<meta><SolarAzimuth>123.4</SolarAzimuth></meta>", encoding="utf-8")
        (self.scene / "SCENE_L1_PAN.tif").write_bytes(b"input\n")
        (self.scene / "SCENE_L1_PAN_mask.tif").write_bytes(b"mask\n")
        self.opt_yml = root / "opt.yml"
        self.opt_yml.write_text("name: test\ntiftype: .tif\n", encoding="utf-8")

        self._store_tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.store = store_mod.Store(
            os.path.join(self._store_tmp.name, "db.sqlite"))

        self._saved_env = {k: os.environ.get(k) for k in self._ENV}
        os.environ.update({
            "SR_EXECUTOR": "local",
            "SR_BUNDLE_DIR": str(self.bundle),
            "SR_PYTHON": sys.executable,
            "SR_SR_SCRIPT": STUB_NAME,
            "SR_VERIFY_SCRIPT": "verify_sr_run.py",
            "SR_SLURM_WORK_DIR": str(self.work),
        })
        # Neither of these belongs on the local path: the sandbox would move the
        # product away from the scene dir, and a locked dir would add a rule
        # this rehearsal is not about.
        for name in ("SR_SANDBOX_ROOT", "SR_LOCKED_DIR", "SR_SLURM_NODELIST"):
            os.environ.pop(name, None)
        local_exec._reset()
        self.addCleanup(self._restore)

    _ENV = ("SR_EXECUTOR", "SR_BUNDLE_DIR", "SR_PYTHON", "SR_SR_SCRIPT",
            "SR_VERIFY_SCRIPT", "SR_SLURM_WORK_DIR", "SR_SANDBOX_ROOT",
            "SR_LOCKED_DIR", "SR_SLURM_NODELIST", "SR_STUB_SILENT",
            "SR_STUB_SLEEP")

    def _restore(self):
        for jid in list(local_exec._JOBS):
            local_exec.cancel(jid)
        deadline = time.time() + 15
        while time.time() < deadline:
            if not any(rec["proc"] is not None and rec["proc"].poll() is None
                       for rec in list(local_exec._JOBS.values())):
                break
            time.sleep(0.05)
        local_exec._reset()
        for k, v in self._saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self._tmp.cleanup()
        self._store_tmp.cleanup()

    # ---- helpers ---------------------------------------------------------
    def params(self) -> dict:
        return {"lq_path": str(self.scene),
                "mask_path": str(self.scene / "SCENE_L1_PAN_mask.tif"),
                "sr_scale": 2, "suffix": "sr", "gpu": 0, "cloud_limit": 80,
                "delete_ori": False, "grid_align": True,
                "options_yml": str(self.opt_yml)}

    def submit(self, params: dict | None = None) -> dict:
        with mock.patch.object(svc, "build_batch_script", _host_script):
            return svc.submit_run_sr(params if params is not None else self.params(),
                                     store=self.store)

    def verdict_path(self, job_id) -> Path:
        return self.scene / "Debug" / ("_SREXIT_%s.txt" % job_id)

    def wait_terminal(self, job_id, res, seen=None, timeout=60.0) -> dict:
        """Poll like the queue page does; `seen` collects the states observed."""
        task = {"config_xml": res["config_xml"]}
        deadline = time.time() + timeout
        last = None
        while time.time() < deadline:
            last = svc.query_job_status(job_id, task=task)
            if seen is not None:
                seen.add(last["state"])
            if not last["active"]:
                return last
            time.sleep(0.02)
        self.fail("job %s never reached a terminal state; last=%r"
                  % (job_id, last))

    # ---- the rehearsal ---------------------------------------------------
    def test_submit_runs_the_script_and_completes(self):
        """The whole chain, once: SUBMITTED → RUNNING → COMPLETED."""
        os.environ["SR_STUB_SLEEP"] = "0.4"         # make RUNNING observable
        res = self.submit()
        self.assertEqual(res["status"], "SUBMITTED")
        self.assertFalse(res["idempotent"])
        job_id = res["job_id"]

        seen = set()
        st = self.wait_terminal(job_id, res, seen=seen)
        self.assertEqual(st["state"], "COMPLETED", st)
        self.assertEqual(st["exit_code"], "0:0")
        self.assertIn("RUNNING", seen)              # not a verdict-file shortcut

        # The job's own artefacts, written next to the input (plan §1.1).
        self.assertTrue((self.scene / "SCENE_L1_PAN_SRLOG.txt").is_file()
                        or (self.scene / "Debug"
                            / "SCENE_L1_PAN_SRLOG.txt").is_file())
        self.assertTrue((self.scene / "SCENE_L1_PAN_sr.tif").is_file())
        text = self.verdict_path(job_id).read_text(encoding="utf-8")
        self.assertIn("verdict=0", text)
        self.assertIn("job_id=%s" % job_id, text)

        # The script really ran the *variant* name we asked for, in the bundle.
        log = Path(local_exec._JOBS[job_id]["log"]).read_text(
            encoding="utf-8", errors="replace")
        self.assertIn("SR_SCRIPT=%s" % STUB_NAME, log)
        self.assertIn("SR_EXECUTOR=local", log)
        self.assertIn("stub SR wrote SCENE_L1_PAN_sr.tif", log)

    def test_exit_0_without_artefacts_is_failed_not_completed(self):
        """The silent-failure case (plan §6.4): exit 0, nothing produced."""
        os.environ["SR_STUB_SILENT"] = "1"
        res = self.submit()
        st = self.wait_terminal(res["job_id"], res)
        self.assertEqual(st["state"], "FAILED", st)
        self.assertTrue(self.verdict_path(res["job_id"]).is_file(),
                        "a failed contract must still leave a verdict file")
        self.assertIn("verdict=90",
                      self.verdict_path(res["job_id"]).read_text(encoding="utf-8"))

    def test_resubmitting_finished_params_reuses_the_job(self):
        """Idempotency through the real executor: no second job, no new id."""
        res = self.submit()
        self.wait_terminal(res["job_id"], res)
        again = self.submit()
        self.assertEqual(again["status"], "RESUMED_COMPLETED")
        self.assertEqual(again["job_id"], res["job_id"])
        self.assertTrue(again["idempotent"])
        self.assertEqual(len(local_exec._JOBS), 1)

    def test_the_bundle_config_suffix_reaches_the_artefact_name(self):
        """An omitted suffix resolves from the SR config file — end to end.

        The only test that shows the value really travels to the product name:
        bundle config file → normalize_suffix("") → <Suffix> in the generated
        config.xml → the filename the SR script writes. Everything upstream of
        the script is the real code path; the stub is the SR half, as in the
        rest of this file. Both entry points (REST / agent tool) hand over the
        same "" here, which is what makes them agree on the fingerprint.
        """
        (self.bundle / svc.BUNDLE_SUFFIX_CONFIG_NAMES[0]).write_text(
            "<?xml version='1.0' encoding='UTF-8'?>\n"
            "<SFSR_Config><Suffix>260318</Suffix></SFSR_Config>\n",
            encoding="utf-8")
        params = self.params()
        params["suffix"] = svc.normalize_suffix("")
        self.assertEqual(params["suffix"], "260318")   # not the built-in "sr"

        res = self.submit(params)
        st = self.wait_terminal(res["job_id"], res)
        self.assertEqual(st["state"], "COMPLETED", st)
        self.assertTrue((self.scene / "SCENE_L1_PAN_260318.tif").is_file())
        self.assertFalse((self.scene / "SCENE_L1_PAN_sr.tif").exists())
        self.assertIn("<Suffix>260318</Suffix>",
                      Path(res["config_xml"]).read_text(encoding="utf-8"))

    def test_config_and_script_land_where_the_queue_view_looks(self):
        """The paths the platform re-derives later must exist up front."""
        res = self.submit()
        self.wait_terminal(res["job_id"], res)
        cfg = Path(res["config_xml"])
        self.assertTrue(cfg.is_file())
        self.assertTrue(Path(res["batch_script"]).is_file())
        self.assertEqual(str(Path(res["log_dir"])), str(self.work))
        # exit_code_file_for() reads DatarootLQ back out of this very config —
        # that is how the queue view finds the verdict file without a handshake.
        self.assertEqual(svc.read_dataroot_lq(str(cfg)), str(self.scene))


if __name__ == "__main__":
    unittest.main()
