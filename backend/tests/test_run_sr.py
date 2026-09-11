"""Tests for run_sr (config XML, batch script, Slurm submit, status poll).

The dev machine has no Slurm, so "unavailable" paths are tested against the
real host; happy paths inject a fake command runner (run_cmd seam) and patch
slurm_available.
"""

import json
import os
import re
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
    """The batch script is asserted by string: it is a shell artefact, and the
    only way to check a CentOS7 job script from this machine is to pin its
    exact shape."""

    def setUp(self):
        self._env = {}
        for name in ("SR_SLURM_TIME", "SR_SLURM_CPUS", "SR_SLURM_PARTITION",
                     "SR_SLURM_MEM", "SR_VERIFY_SCRIPT", "SR_PYTHON",
                     "SR_BUNDLE_DIR", "SR_SR_SCRIPT"):
            if name in os.environ:
                self._env[name] = os.environ.pop(name)
        self.addCleanup(self._restore_env)

    def _restore_env(self):
        for name in ("SR_SLURM_TIME", "SR_SLURM_CPUS", "SR_SLURM_PARTITION",
                     "SR_SLURM_MEM", "SR_VERIFY_SCRIPT", "SR_PYTHON",
                     "SR_BUNDLE_DIR", "SR_SR_SCRIPT"):
            os.environ.pop(name, None)
        os.environ.update(self._env)

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

    # ---- SR_SR_SCRIPT: run the deployment variant while the production
    # script stays untouched (docs/planning/sr-pipeline-restore-plan.md E-A) ---
    def test_sr_script_defaults_to_the_production_script(self):
        script = svc.build_batch_script("/w/cfg.xml", "/w")
        self.assertIn("python code_0817_prod.py -f /w/cfg.xml", script)
        self.assertIn('echo "SR_SCRIPT=code_0817_prod.py"', script)

    def test_sr_script_env_switches_to_the_variant(self):
        os.environ["SR_SR_SCRIPT"] = "code_0817_prod_slurm.py"
        script = svc.build_batch_script("/w/cfg.xml", "/w")
        self.assertIn("python code_0817_prod_slurm.py -f /w/cfg.xml", script)
        # and nothing may still point at the production script
        self.assertNotIn("code_0817_prod.py", script)

    def test_sr_script_argument_beats_the_env(self):
        os.environ["SR_SR_SCRIPT"] = "from-env.py"
        script = svc.build_batch_script("/w/cfg.xml", "/w",
                                        sr_script="from-arg.py")
        self.assertIn("from-arg.py", script)
        self.assertNotIn("from-env.py", script)

    # ---- P2 additions ----------------------------------------------------
    def test_env_driven_limits_and_job_name(self):
        os.environ["SR_SLURM_TIME"] = "04:30:00"
        os.environ["SR_SLURM_CPUS"] = "8"
        os.environ["SR_SLURM_PARTITION"] = "gpu"
        script = svc.build_batch_script("/w/run_sr_t1.xml", "/w")
        self.assertIn("#SBATCH --time=04:30:00", script)
        self.assertIn("#SBATCH --cpus-per-task=8", script)
        self.assertIn("#SBATCH --partition=gpu", script)
        self.assertIn("#SBATCH --job-name=run_sr_t1", script)   # suffix in name

    def test_limits_fall_back_to_defaults(self):
        script = svc.build_batch_script("/w/cfg.xml", "/w")
        self.assertIn("#SBATCH --time=02:00:00", script)
        self.assertIn("#SBATCH --cpus-per-task=4", script)

    def test_export_none_is_set(self):
        self.assertIn("#SBATCH --export=NONE", svc.build_batch_script("/w/c.xml", "/w"))

    def test_mem_only_when_configured(self):
        script = svc.build_batch_script("/w/c.xml", "/w")
        self.assertNotIn("--mem", script)
        os.environ["SR_SLURM_MEM"] = "64G"
        self.assertIn("#SBATCH --mem=64G", svc.build_batch_script("/w/c.xml", "/w"))

    def test_never_assigns_cuda_visible_devices(self):
        # E2/E4: any assignment here would clobber what --gres allocated. The
        # audit section only READS it, so match assignments, not mentions.
        script = svc.build_batch_script("/w/c.xml", "/w")
        assignments = re.findall(r"^\s*(?:export\s+)?CUDA_VISIBLE_DEVICES\s*=",
                                 script, re.MULTILINE)
        self.assertEqual(assignments, [], "the script must not pick a GPU")

    def test_audit_preamble_records_the_allocation(self):
        script = svc.build_batch_script("/w/c.xml", "/w",
                                        python="/opt/sr/bin/python")
        self.assertIn("SLURM_JOB_ID=", script)
        self.assertIn("SLURM_JOB_GPUS=", script)
        self.assertIn("CUDA_VISIBLE_DEVICES=", script)
        self.assertIn("SR_SCRIPT=", script)          # which SR script ran
        self.assertIn("$(hostname)", script)
        self.assertIn("/opt/sr/bin/python", script)
        self.assertIn("/w/c.xml", script)
        self.assertIn("nvidia-smi --query-gpu=index,uuid", script)

    def test_pythonunbuffered_is_exported(self):
        self.assertIn("export PYTHONUNBUFFERED=1",
                      svc.build_batch_script("/w/c.xml", "/w"))

    def test_cd_failure_exits_nonzero(self):
        script = svc.build_batch_script("/w/c.xml", "/w")
        cd_line = [ln for ln in script.splitlines() if ln.startswith("cd ")][0]
        self.assertIn("|| {", cd_line)
        self.assertIn("exit 1", cd_line)

    def test_no_set_e(self):
        # `set -e` would change the meaning of the `||`/pipeline lines below it.
        script = svc.build_batch_script("/w/c.xml", "/w")
        self.assertNotIn("set -e", script)

    def test_verifier_runs_after_python_and_passes_its_exit_code_through(self):
        script = svc.build_batch_script("/w/c.xml", "/w")
        lines = script.splitlines()
        run_i = lines.index("python code_0817_prod.py -f /w/c.xml")
        self.assertEqual(lines[run_i + 1], "_sr_rc=$?")
        self.assertIn("verify_sr_run.py --config /w/c.xml --sr-exit-code \"$_sr_rc\"",
                      lines[run_i + 3])
        self.assertEqual(lines[-1], "exit $?")

    def test_verify_script_can_be_overridden(self):
        os.environ["SR_VERIFY_SCRIPT"] = "/opt/sr/bin/my_verify.py"
        script = svc.build_batch_script("/w/c.xml", "/w")
        self.assertIn("/opt/sr/bin/my_verify.py --config /w/c.xml", script)

    def test_job_name_is_slurm_safe(self):
        script = svc.build_batch_script("/w/weird name/../x.xml", "/w",
                                        job_name="run sr/../t1")
        name = [ln for ln in script.splitlines()
                if ln.startswith("#SBATCH --job-name=")][0].split("=", 1)[1]
        self.assertNotIn(" ", name)
        self.assertNotIn("/", name)
        self.assertTrue(name)


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


class TestSandboxPaths(unittest.TestCase):
    """SR writes to its DatarootLQ (input renamed to *_NOSR.tif), so a submit
    must be able to run against a copy instead of the directory the user typed."""

    def setUp(self):
        self._old = os.environ.pop("SR_SANDBOX_ROOT", None)
        self.addCleanup(self._restore)

    def _restore(self):
        os.environ.pop("SR_SANDBOX_ROOT", None)
        if self._old is not None:
            os.environ["SR_SANDBOX_ROOT"] = self._old

    def test_off_by_default(self):
        self.assertIsNone(svc.sandbox_scene_paths("/D/x/SCENE", "ab" * 32))

    def test_scene_keeps_the_source_basename(self):
        # the SC step reads <basename>.tif out of DatarootLQ — renaming the copy
        # would change which file the job opens.
        got = svc.sandbox_scene_paths("/D/x/JXGF07A03_101_001_L1_PAN", "c0ffee" * 8,
                                      sandbox_root="/tmp/sbx/")
        self.assertEqual(got["parent"], "/tmp/sbx/" + "c0ffee" * 2)
        self.assertEqual(got["scene"],
                         got["parent"] + "/JXGF07A03_101_001_L1_PAN")

    def test_key_is_the_fingerprint_prefix(self):
        a = svc.sandbox_scene_paths("/D/x/S", "a" * 64, sandbox_root="/tmp/s")
        b = svc.sandbox_scene_paths("/D/x/S", "b" * 64, sandbox_root="/tmp/s")
        self.assertNotEqual(a["parent"], b["parent"])

    def test_env_supplies_the_root(self):
        os.environ["SR_SANDBOX_ROOT"] = "/D/tmp/sbx"
        got = svc.sandbox_scene_paths("/D/x/S", "a" * 64)
        self.assertEqual(got["root"], "/D/tmp/sbx")

    def test_relative_root_rejected(self):
        with self.assertRaises(ValueError):
            svc.sandbox_scene_paths("/D/x/S", "a" * 64, sandbox_root="tmp/sbx")

    def test_shell_metacharacters_in_root_rejected(self):
        # the root lands inside a generated `rm -rf "<root>/<key>"` line
        for bad in ('/D/sbx"; rm -rf /', "/D/sbx`id`", "/D/sbx/../..", "/D/a b"):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    svc.sandbox_scene_paths("/D/x/S", "a" * 64, sandbox_root=bad)

    def test_non_hex_key_rejected(self):
        with self.assertRaises(ValueError):
            svc.sandbox_scene_paths("/D/x/S", 'a"; rm -rf /', sandbox_root="/D/s")


class TestSandboxWiring(unittest.TestCase):
    """config.xml and the job script must agree on where the copy lives — that
    agreement is the whole mechanism (nothing is handed back at runtime)."""

    def setUp(self):
        self._old = os.environ.pop("SR_SANDBOX_ROOT", None)
        self._work = tempfile.TemporaryDirectory()
        os.environ["SR_SLURM_WORK_DIR"] = self._work.name
        patcher = mock.patch.object(slurm, "slurm_available", lambda: True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self._work.cleanup)
        self.addCleanup(self._restore)
        self.store, self._db = temp_store()
        self.addCleanup(self._db.cleanup)
        self.addCleanup(self.store.close)

    def _restore(self):
        os.environ.pop("SR_SANDBOX_ROOT", None)
        os.environ.pop("SR_SLURM_WORK_DIR", None)
        if self._old is not None:
            os.environ["SR_SANDBOX_ROOT"] = self._old

    def _submit(self, params):
        fake = fake_run_script(("Submitted batch job 42\n", "", 0))
        return svc.submit_run_sr(params, run_cmd=fake, store=self.store)

    def test_config_dataroot_points_at_the_copy(self):
        os.environ["SR_SANDBOX_ROOT"] = "/D/tmp/sbx"
        out = self._submit({"lq_path": "/D/prod/SCENE_A", "suffix": "t1"})
        self.assertEqual(svc.read_dataroot_lq(out["config_xml"]),
                         "/D/tmp/sbx/" + svc.task_fingerprint(
                             {"lq_path": "/D/prod/SCENE_A", "suffix": "t1"})[:12]
                         + "/SCENE_A")

    def test_verdict_file_resolves_inside_the_sandbox(self):
        # path is asserted by tail, not prefix: exit_code_file_for goes through
        # Path(), which renders with the local separator on this Windows dev box
        # and with `/` on the Linux target. The target is what has to be right.
        os.environ["SR_SANDBOX_ROOT"] = "/D/tmp/sbx"
        out = self._submit({"lq_path": "/D/prod/SCENE_A", "suffix": "t1"})
        got = svc.exit_code_file_for(out["config_xml"], 42)
        self.assertIn("sbx", got.split(os.sep))
        self.assertTrue(got.endswith(os.sep.join(
            ["SCENE_A", "Debug", "_SREXIT_42.txt"])), got)

    def test_batch_script_copies_before_running_sr(self):
        os.environ["SR_SANDBOX_ROOT"] = "/D/tmp/sbx"
        out = self._submit({"lq_path": "/D/prod/SCENE_A", "suffix": "t1"})
        script = Path(out["batch_script"]).read_text(encoding="utf-8")
        self.assertIn('SBX_SRC="/D/prod/SCENE_A"', script)
        self.assertIn('SBX_SCENE="$SBX_PARENT/$(basename "$SBX_SRC")"', script)
        self.assertIn('cp -a "$SBX_SRC" "$SBX_PARENT/"', script)
        self.assertIn('rm -rf "$SBX_PARENT"', script)
        # the copy must happen before the SR script *runs* — the audit preamble
        # also mentions the script name, so anchor on the invocation instead.
        self.assertLess(script.index('cp -a "$SBX_SRC"'),
                        script.index("-f " + out["config_xml"]))

    def test_batch_script_never_reuses_a_stale_copy(self):
        # a marker-file shortcut would make a re-run execute against the old
        # copy of a source that has since been fixed.
        os.environ["SR_SANDBOX_ROOT"] = "/D/tmp/sbx"
        out = self._submit({"lq_path": "/D/prod/SCENE_A", "suffix": "t1"})
        script = Path(out["batch_script"]).read_text(encoding="utf-8")
        self.assertNotIn(".ready", script)
        self.assertLess(script.index('rm -rf "$SBX_PARENT"'),
                        script.index('cp -a "$SBX_SRC"'))

    def test_half_copied_sandbox_is_refused(self):
        os.environ["SR_SANDBOX_ROOT"] = "/D/tmp/sbx"
        out = self._submit({"lq_path": "/D/prod/SCENE_A", "suffix": "t1"})
        script = Path(out["batch_script"]).read_text(encoding="utf-8")
        self.assertIn('if [ ! -d "$SBX_SCENE" ]; then', script)

    def test_sandbox_off_runs_in_place(self):
        out = self._submit({"lq_path": "/D/prod/SCENE_A", "suffix": "t1"})
        self.assertEqual(svc.read_dataroot_lq(out["config_xml"]), "/D/prod/SCENE_A")
        script = Path(out["batch_script"]).read_text(encoding="utf-8")
        self.assertNotIn("cp -a ", script)
        self.assertNotIn("SBX_PARENT", script)

    def test_same_suffix_different_scenes_do_not_share_a_config(self):
        # both jobs write config.xml into one work dir; sharing a path would make
        # the second submit repoint the first task's verdict lookup.
        a = self._submit({"lq_path": "/D/prod/SCENE_A", "suffix": "t1"})
        b = self._submit({"lq_path": "/D/prod/SCENE_B", "suffix": "t1"})
        self.assertNotEqual(a["config_xml"], b["config_xml"])
        self.assertEqual(svc.read_dataroot_lq(a["config_xml"]), "/D/prod/SCENE_A")
        self.assertEqual(svc.read_dataroot_lq(b["config_xml"]), "/D/prod/SCENE_B")

    def test_bad_sandbox_root_stops_the_submit_before_sbatch(self):
        os.environ["SR_SANDBOX_ROOT"] = "/D/sbx; rm -rf /"
        runner = dispatch_runner()
        with self.assertRaises(ValueError):
            svc.submit_run_sr({"lq_path": "/D/prod/SCENE_A"}, run_cmd=runner,
                              store=self.store)
        self.assertEqual(runner.calls, [])       # nothing reached Slurm


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

    def write_verdict(self, lq_path, job_id, verdict=0, sr_exit_code=0):
        """Drop the job's own verdict file where the platform looks for it."""
        debug = Path(lq_path) / "Debug"
        debug.mkdir(parents=True, exist_ok=True)
        (debug / svc.EXIT_FILE_FMT.format(job_id=job_id)).write_text(
            "job_id=%s\nsr_exit_code=%s\nverdict=%s\nskip=0\n"
            % (job_id, sr_exit_code, verdict), encoding="utf-8")

    def test_replay_contract_satisfied_reuses_no_resubmit(self):
        params = {**PARAMS, "lq_path": self._tmp.name}
        first = dispatch_runner()
        svc.submit_run_sr(params, run_cmd=first, store=self.store)
        self.write_verdict(self._tmp.name, 42)

        runner = dispatch_runner()
        data = svc.submit_run_sr(params, run_cmd=runner, store=self.store)
        self.assertEqual(data["status"], "RESUMED_COMPLETED")
        self.assertEqual(data["job_id"], 42)
        self.assertTrue(data["idempotent"])
        self.assertEqual(runner.calls, ["squeue"])       # no sacct, no sbatch

    def test_replay_silent_failure_reruns_instead_of_caching_success(self):
        # §2.3 毒药: exit 0 but the contract was not met. This must NOT be
        # RESUMED_COMPLETED — the scene would stay un-super-resolved forever.
        params = {**PARAMS, "lq_path": self._tmp.name}
        first = dispatch_runner()
        svc.submit_run_sr(params, run_cmd=first, store=self.store)
        self.write_verdict(self._tmp.name, 42, verdict=90)

        runner = dispatch_runner(sbatch_outputs=("Submitted batch job 43\n",))
        data = svc.submit_run_sr(params, run_cmd=runner, store=self.store)
        self.assertEqual(data["status"], "SUBMITTED")
        self.assertEqual(data["job_id"], 43)
        self.assertEqual(data["previous_state"], "FAILED")
        self.assertEqual(runner.calls, ["squeue", "sbatch"])

    def test_replay_failed_reruns_with_new_job(self):
        params = {**PARAMS, "lq_path": self._tmp.name}
        first = dispatch_runner()
        svc.submit_run_sr(params, run_cmd=first, store=self.store)
        self.write_verdict(self._tmp.name, 42, verdict=90, sr_exit_code=1)

        runner = dispatch_runner(sbatch_outputs=("Submitted batch job 43\n",))
        data = svc.submit_run_sr(params, run_cmd=runner, store=self.store)
        self.assertEqual(data["status"], "SUBMITTED")
        self.assertEqual(data["job_id"], 43)          # a fresh job, not 42
        self.assertEqual(data["previous_state"], "FAILED")
        self.assertEqual(data["previous_exit_code"], "1:0")
        self.assertEqual(data["previous_job_id"], 42)
        self.assertEqual(runner.calls, ["squeue", "sbatch"])

    def test_replay_unknown_reruns(self):
        # No verdict file and nothing in squeue: accounting is disabled, so
        # there is no oracle left — rerun rather than claim success.
        params = {**PARAMS, "lq_path": self._tmp.name}
        first = dispatch_runner()
        svc.submit_run_sr(params, run_cmd=first, store=self.store)

        runner = dispatch_runner(sbatch_outputs=("Submitted batch job 43\n",))
        data = svc.submit_run_sr(params, run_cmd=runner, store=self.store)
        self.assertEqual(data["status"], "SUBMITTED")
        self.assertEqual(data["job_id"], 43)
        self.assertEqual(data["previous_state"], "UNKNOWN")
        self.assertEqual(runner.calls, ["squeue", "sbatch"])

    def test_replay_with_unwritable_lq_path_is_unknown_not_an_error(self):
        # lq_path on a host that cannot read it (the dev machine's case): the
        # verdict file is unreachable, which must degrade to a rerun, never to
        # an exception that leaves the task neither reused nor rerun.
        first = dispatch_runner()
        svc.submit_run_sr(PARAMS, run_cmd=first, store=self.store)  # lq_path=/data
        runner = dispatch_runner(sbatch_outputs=("Submitted batch job 43\n",))
        data = svc.submit_run_sr(PARAMS, run_cmd=runner, store=self.store)
        self.assertEqual(data["status"], "SUBMITTED")
        self.assertEqual(data["previous_state"], "UNKNOWN")

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
    """Terminal state comes from the job's verdict file, not from sacct: the
    array server runs with accounting disabled, so sacct has no output to give
    (slurm-integration.md §2.4-2 / §一 "C 方案")."""

    def setUp(self):
        patcher = mock.patch.object(slurm, "slurm_available", lambda: True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def verdict_file(self, job_id=7, verdict=0, sr_exit_code=0, skip=0):
        path = Path(self._tmp.name) / svc.EXIT_FILE_FMT.format(job_id=job_id)
        path.write_text(
            "job_id=%s\nsr_exit_code=%s\nverdict=%s\nskip=%s\n"
            % (job_id, sr_exit_code, verdict, skip), encoding="utf-8")
        return str(path)

    def test_active_from_squeue(self):
        fake = fake_run_script(("PENDING\n", "", 0))
        st = slurm.job_status(7, run_cmd=fake)
        self.assertEqual(st, {"job_id": 7, "active": True,
                              "state": "PENDING", "exit_code": None})

    def test_terminal_satisfied_from_verdict_file(self):
        fake = fake_run_script(("", "", 0))
        st = slurm.job_status(7, run_cmd=fake, exit_code_file=self.verdict_file())
        self.assertEqual(st["active"], False)
        self.assertEqual(st["state"], "COMPLETED")
        self.assertEqual(st["exit_code"], "0:0")

    def test_terminal_contract_not_satisfied_is_failed(self):
        # The silent-failure shape: exit 0, but the job's own verifier says the
        # contract was not met. Slurm would call this COMPLETED; we must not.
        fake = fake_run_script(("", "", 0))
        st = slurm.job_status(7, run_cmd=fake,
                              exit_code_file=self.verdict_file(verdict=90))
        self.assertEqual(st["state"], "FAILED")
        self.assertEqual(st["exit_code"], "0:0")

    def test_terminal_nonzero_exit_is_failed(self):
        fake = fake_run_script(("", "", 0))
        st = slurm.job_status(7, run_cmd=fake,
                              exit_code_file=self.verdict_file(sr_exit_code=3,
                                                               verdict=90))
        self.assertEqual(st["state"], "FAILED")
        self.assertEqual(st["exit_code"], "3:0")

    def test_sacct_is_never_consulted(self):
        runner = dispatch_runner()
        slurm.job_status(7, run_cmd=runner, exit_code_file=self.verdict_file())
        self.assertEqual(runner.calls, ["squeue"])

    def test_verdict_for_another_job_is_ignored(self):
        # Job ids get recycled; a stranger's verdict must not be inherited.
        fake = fake_run_script(("", "", 0))
        st = slurm.job_status(7, run_cmd=fake,
                              exit_code_file=self.verdict_file(job_id=99))
        self.assertEqual(st["state"], "UNKNOWN")

    def test_unknown_without_a_verdict_file(self):
        fake = fake_run_script(("", "", 0))
        st = slurm.job_status(7, run_cmd=fake, exit_code_file=None)
        self.assertEqual(st["state"], "UNKNOWN")
        self.assertIsNone(st["active"])

    def test_missing_verdict_file_is_unknown_not_an_exception(self):
        fake = fake_run_script(("", "", 0))
        st = slurm.job_status(7, run_cmd=fake,
                              exit_code_file=str(Path(self._tmp.name) / "nope"))
        self.assertEqual(st["state"], "UNKNOWN")

    def test_truncated_verdict_file_is_unknown(self):
        path = Path(self._tmp.name) / "trunc.txt"
        path.write_text("job_id=7\nsr_exit", encoding="utf-8")
        fake = fake_run_script(("", "", 0))
        st = slurm.job_status(7, run_cmd=fake, exit_code_file=str(path))
        self.assertEqual(st["state"], "UNKNOWN")

    def test_squeue_failure_degrades_to_the_verdict_file(self):
        # squeue raising must not escape job_status (it used to reach
        # run_sr._resolve_existing and turn a replay into a hard error).
        def boom(cmd, timeout=30):
            raise subprocess.TimeoutExpired(cmd, timeout)

        st = slurm.job_status(7, run_cmd=boom,
                              exit_code_file=self.verdict_file())
        self.assertEqual(st["state"], "COMPLETED")

    def test_job_status_never_raises_on_junk_input(self):
        for bad in (None, "", str(Path(self._tmp.name)), "/dev/null"):
            with self.subTest(path=bad):
                fake = fake_run_script(("", "", 0))
                st = slurm.job_status(7, run_cmd=fake, exit_code_file=bad)
                self.assertEqual(st["state"], "UNKNOWN")

    def test_cancel_ok(self):
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
