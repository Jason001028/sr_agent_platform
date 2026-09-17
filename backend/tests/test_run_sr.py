"""Tests for run_sr (config XML, batch script, Slurm submit, status poll).

The dev machine has no Slurm, so "unavailable" paths are tested against the
real host; happy paths inject a fake command runner (run_cmd seam) and patch
slurm_available.
"""

import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest import mock

from backend.services import run_sr as svc
from backend.services import slurm
from backend.services import store as store_mod
from backend.tests import allowed_roots_env
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
        # delete_ori 已禁用：即使参数里带了 True，生成的 config 也必须是 False
        # （提交入口另有 400 拦截，这一层是兜底 —— SR 会不可恢复地删/覆盖原图）。
        self.assertEqual(tags["DeleteOriTifNeeded"], "False")

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
                     "SR_SLURM_MEM", "SR_SLURM_NODELIST", "SR_VERIFY_SCRIPT",
                     "SR_PYTHON", "SR_BUNDLE_DIR", "SR_SR_SCRIPT"):
            if name in os.environ:
                self._env[name] = os.environ.pop(name)
        self.addCleanup(self._restore_env)

    def _restore_env(self):
        for name in ("SR_SLURM_TIME", "SR_SLURM_CPUS", "SR_SLURM_PARTITION",
                     "SR_SLURM_MEM", "SR_SLURM_NODELIST", "SR_VERIFY_SCRIPT",
                     "SR_PYTHON", "SR_BUNDLE_DIR", "SR_SR_SCRIPT"):
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

    # ---- SR_SLURM_NODELIST: keep jobs off the node family the submitting host
    # cannot resolve (docs/status/slurm-acceptance.md §B0, 2026-09-14) ---------
    def test_nodelist_unset_emits_no_line(self):
        script = svc.build_batch_script("/w/cfg.xml", "/w")
        self.assertNotIn("--nodelist", script)

    def test_nodelist_env_emits_the_directive(self):
        os.environ["SR_SLURM_NODELIST"] = "node81-[129-162,165-183]"
        script = svc.build_batch_script("/w/cfg.xml", "/w")
        self.assertIn("#SBATCH --nodelist=node81-[129-162,165-183]", script)

    def test_nodelist_argument_beats_the_env(self):
        os.environ["SR_SLURM_NODELIST"] = "from-env"
        script = svc.build_batch_script("/w/cfg.xml", "/w", nodelist="node81-132")
        self.assertIn("#SBATCH --nodelist=node81-132", script)
        self.assertNotIn("from-env", script)

    def test_nodelist_blank_means_no_line(self):
        script = svc.build_batch_script("/w/cfg.xml", "/w", nodelist="   ")
        self.assertNotIn("--nodelist", script)

    def test_nodelist_rejects_injection(self):
        # the value is written verbatim, so anything that could end the
        # directive or start a new one has to be refused, not sanitised
        for bad in ("node81-132\n#SBATCH --gres=gpu:8", "node81 132",
                    "node81;id", "node81-132'", 'node81-132"/x', "node81-132&"):
            with self.assertRaises(ValueError):
                svc.build_batch_script("/w/cfg.xml", "/w", nodelist=bad)

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

    def test_in_place_run_warns_about_the_nosr_rename_in_the_log(self):
        # No sandbox → SR writes into the scene dir: the result tif lands next to
        # the input, and a file already sitting at that output path is renamed to
        # *_NOSR.tif before being overwritten (util.writeTiff renames the output
        # path, not the input), so the job log has to say so (plan §4.2, last
        # bullet). A 4.9 GB previous output is the file that gets renamed.
        script = svc.build_batch_script("/w/c.xml", "/w")
        self.assertIn("WARNING: no sandbox", script)
        self.assertIn("leaves the input", script)
        self.assertIn("_NOSR.tif", script)

    def test_sandboxed_script_has_no_in_place_warning(self):
        script = svc.build_batch_script(
            "/w/c.xml", "/w",
            sandbox_src="/DiskArray/tmp/wangrz/SCENE",
            sandbox_parent="/DiskArray/tmp/wangrz/sr_sandbox/abc123")
        self.assertNotIn("WARNING: no sandbox", script)

    def test_no_job_id_line_without_a_job_id(self):
        # Under Slurm the verifier reads $SLURM_JOB_ID; passing --job-id too
        # would make the two sources able to disagree.
        script = svc.build_batch_script("/w/c.xml", "/w")
        self.assertNotIn("--job-id", script)

    def test_job_id_is_baked_into_the_verifier_call(self):
        # Local execution has no $SLURM_JOB_ID, so the id carried in the script
        # text is the only thing that names the verdict file — the verifier must
        # be told it, and it must be the same id exit_code_file_for re-derives.
        script = svc.build_batch_script("/w/c.xml", "/w", job_id=41)
        self.assertIn("verify_sr_run.py --config /w/c.xml --sr-exit-code "
                      '"$_sr_rc" --job-id 41', script)

    def test_executor_is_echoed_for_the_log(self):
        self.assertIn('echo "SR_EXECUTOR=${SR_EXECUTOR:-slurm}"',
                      svc.build_batch_script("/w/c.xml", "/w"))

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
    """SR writes its result tif / Debug log / meta update into its DatarootLQ,
    so a submit must be able to run against a copy instead of the directory the
    user typed."""

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


#: 工具入口（`run_run_sr`）会过提交侧白名单 SR_ALLOWED_ROOTS —— 这些用例
#: 只想验证"能走到服务层"，所以用一个默认白名单内的路径。
SCENE = "/DiskArray/GSHC2IMPS/PRODUCT/2026/09/17/260318"


class TestToolRunSr(unittest.TestCase):
    def test_missing_lq_path(self):
        r = run_run_sr()
        self.assertFalse(r["ok"])
        self.assertIn("lq_path", r["error"])

    def test_bad_sr_scale(self):
        r = run_run_sr(lq_path=SCENE, sr_scale=0)
        self.assertFalse(r["ok"])
        self.assertIn("sr_scale", r["error"])

    def test_valid_but_slurm_unavailable(self):
        # dev machine: real slurm_available() is False → clean err
        r = run_run_sr(lq_path=SCENE)
        self.assertFalse(r["ok"])
        self.assertIn("slurm", r["error"])

    def test_delete_ori_is_rejected(self):
        # 参数校验先于调度器可用性：dev 机没有 sbatch，但这里必须先报 delete_ori
        r = run_run_sr(lq_path=SCENE, delete_ori=True)
        self.assertFalse(r["ok"])
        self.assertIn("delete_ori", r["error"])

    def test_bad_suffix_is_rejected_like_the_rest_entry_point(self):
        # 工具侧过去完全不校验 suffix —— 一个 `a/b` 会一路拼进输出文件名。
        for bad in ("a/b", "a b", "x" * 17, "掩码"):
            with self.subTest(suffix=bad):
                r = run_run_sr(lq_path=SCENE, suffix=bad)
                self.assertFalse(r["ok"])
                self.assertIn("suffix 非法", r["error"])

    def test_empty_suffix_is_defaulted_not_rejected(self):
        # 留空 = 取 SR 配置里的 <Suffix>，与 REST 入口同规矩：走到服务层，
        # 于是 err 只能是 dev 机没有 slurm 的那条。
        r = run_run_sr(lq_path=SCENE, suffix="   ")
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


def write_bundle_cfg(bundle_dir, body, name=None):
    """Write one candidate of the SR team's config into a fake bundle dir."""
    name = name or svc.BUNDLE_SUFFIX_CONFIG_NAMES[0]
    os.makedirs(bundle_dir, exist_ok=True)
    path = Path(bundle_dir, name)
    path.write_text(body, encoding="utf-8")
    return str(path)


SUFFIX_CFG = ("<?xml version='1.0' encoding='UTF-8'?>\n"
              "<SFSR_Config><Suffix>260318</Suffix></SFSR_Config>\n")


class TestSuffixResolution(unittest.TestCase):
    """默认后缀 = SR 团队那份配置里的 <Suffix>，读不到才回落内置值。

    这些分支必须单独钉住：开发机上真实 bundle 目录并不存在，只测"默认值是 sr"
    的话，即使读取逻辑整个写坏也会因为回落而通过。
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.bundle = self._tmp.name

    def test_reads_the_tag(self):
        write_bundle_cfg(self.bundle, SUFFIX_CFG)
        self.assertEqual(svc.read_bundle_suffix(bundle_dir=self.bundle), "260318")

    def test_absent_dir_and_file_are_none(self):
        self.assertIsNone(svc.read_bundle_suffix(bundle_dir=self.bundle))
        self.assertIsNone(
            svc.read_bundle_suffix(bundle_dir=os.path.join(self.bundle, "nope")))

    def test_malformed_xml_is_none(self):
        write_bundle_cfg(self.bundle, "<SFSR_Config><Suffix>260318")
        self.assertIsNone(svc.read_bundle_suffix(bundle_dir=self.bundle))

    def test_missing_or_empty_tag_is_none(self):
        for body in ("<?xml version='1.0'?><SFSR_Config><SRScale>2</SRScale>"
                     "</SFSR_Config>",
                     "<?xml version='1.0'?><SFSR_Config><Suffix/></SFSR_Config>",
                     "<?xml version='1.0'?><SFSR_Config><Suffix>  </Suffix>"
                     "</SFSR_Config>"):
            with self.subTest(body=body):
                write_bundle_cfg(self.bundle, body)
                self.assertIsNone(
                    svc.read_bundle_suffix(bundle_dir=self.bundle))

    def test_first_candidate_wins_and_a_broken_one_does_not_shadow(self):
        # 磁盘上的名字与契约文档记的不一致（confgig vs config），两个都探。
        first, second = svc.BUNDLE_SUFFIX_CONFIG_NAMES
        write_bundle_cfg(self.bundle, SUFFIX_CFG, name=first)
        write_bundle_cfg(
            self.bundle,
            "<?xml version='1.0'?><SFSR_Config><Suffix>999999</Suffix>"
            "</SFSR_Config>", name=second)
        self.assertEqual(svc.read_bundle_suffix(bundle_dir=self.bundle), "260318")
        # 第一个坏掉 → 回退到第二个，而不是直接放弃
        write_bundle_cfg(self.bundle, "<broken", name=first)
        self.assertEqual(svc.read_bundle_suffix(bundle_dir=self.bundle), "999999")

    def test_default_suffix_uses_the_file_then_falls_back(self):
        with mock.patch.dict(os.environ, {"SR_BUNDLE_DIR": self.bundle}):
            self.assertEqual(svc.default_suffix(), "sr")     # 无文件
            write_bundle_cfg(self.bundle, SUFFIX_CFG)
            self.assertEqual(svc.default_suffix(), "260318")
            # 文件里的值不合法 → 丢弃并回落（它是拼进输出文件名的，不能放行）
            for bad in ("a/b", "x" * 17, "掩码"):
                with self.subTest(value=bad):
                    write_bundle_cfg(
                        self.bundle,
                        f"<?xml version='1.0'?><SFSR_Config><Suffix>{bad}"
                        "</Suffix></SFSR_Config>")
                    self.assertEqual(svc.default_suffix(), "sr")

    def test_normalize_suffix_covers_both_entries(self):
        with mock.patch.dict(os.environ, {"SR_BUNDLE_DIR": self.bundle}):
            write_bundle_cfg(self.bundle, SUFFIX_CFG)
            for empty in (None, "", "   "):
                with self.subTest(value=empty):
                    self.assertEqual(svc.normalize_suffix(empty), "260318")
            self.assertEqual(svc.normalize_suffix("  t1  "), "t1")   # 显式值优先
            for bad in ("a/b", "../x", "a b", "x" * 17, "suffix;rm", "掩码"):
                with self.subTest(value=bad):
                    with self.assertRaises(ValueError) as ctx:
                        svc.normalize_suffix(bad)
                    self.assertIn("suffix 非法", str(ctx.exception))


class TestEntryPointParity(unittest.TestCase):
    """REST 与 agent 工具对同一个逻辑提交必须给出同一个指纹。

    幂等表按 task_fingerprint 建键，两个入口的默认值只要差一个字（过去 REST 是
    "sr"、工具是 ""），同一次提交就会变成两个作业，而不是复用旧作业。
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.bundle = os.path.join(self._tmp.name, "bundle")
        write_bundle_cfg(self.bundle, SUFFIX_CFG)
        # 场景目录：REST 入口会推导 <目录名>_mask.tif，没有就 400
        self.scene = os.path.join(self._tmp.name, "SCENE_L1_PAN")
        os.makedirs(self.scene)
        self.mask = os.path.join(self.scene, "SCENE_L1_PAN_mask.tif")
        Path(self.mask).write_bytes(b"")
        env = {"SR_BUNDLE_DIR": self.bundle, "SR_LOCKED_DIR": "",
               "SR_AGENT_DB": os.path.join(self._tmp.name, "db.sqlite"),
               **allowed_roots_env(self._tmp.name)}   # 提交侧白名单：临时目录
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_same_logical_submit_same_fingerprint(self):
        from backend.api.platform import _norm_sr_params

        captured = {}

        def spy(params, **kw):
            captured["tool"] = params
            raise RuntimeError("stop before the scheduler")   # 不需要真 sbatch

        with mock.patch.object(svc, "submit_run_sr", side_effect=spy):
            r = run_run_sr(lq_path=self.scene, mask_path=self.mask, suffix="")
        self.assertFalse(r["ok"])

        rest = _norm_sr_params({"lq_path": self.scene, "mask_path": self.mask,
                                "suffix": ""})
        self.assertEqual(captured["tool"]["suffix"], "260318")
        self.assertEqual(rest["suffix"], "260318")
        self.assertEqual(svc.task_fingerprint(captured["tool"]),
                         svc.task_fingerprint(rest))
        self.assertEqual(captured["tool"], rest)   # 连键值集合都一致，不只是哈希


class TestSubmitPathNormalization(unittest.TestCase):
    """同一个场景的两种写法必须归一成同一个字符串。

    用户从客户端粘的是 `W:\\GSHC2IMPS\\PRODUCT\\2026\\09\\17\\<编号>`，服务端
    内部一律用 `/DiskArray/...`。若两个入口各自处理（REST 走 `_bad_path`、工具
    原样透传），同一个场景的两种写法会算出两个 `task_fingerprint`，幂等层失效、
    同一次提交被投成两个作业。这里钉的就是「两入口同一结果 + 两写法同一结果」。
    """

    SCENE_NAME = "JL1KF02B03_PMS02_20260917124710_200536960_101_0005_001_L1_PAN"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name).resolve()
        self.bundle = os.path.join(self._tmp.name, "bundle")
        write_bundle_cfg(self.bundle, SUFFIX_CFG)
        self.scene = (self.root / "GSHC2IMPS" / "PRODUCT" / "2026" / "09"
                      / "17" / self.SCENE_NAME)
        self.scene.mkdir(parents=True)
        (self.scene / f"{self.SCENE_NAME}_meta.xml").write_text(
            "<?xml version='1.0'?><x/>", encoding="utf-8")
        self.mask = self.scene / f"{self.SCENE_NAME}_mask.tif"
        self.mask.write_bytes(b"")
        # 白名单同时收 W: 形态与「本机绝对路径」形态：真机上后者就是
        # /DiskArray/...（与默认白名单同源），开发机上是临时目录本身。两种写法
        # 等价这条用例必须两边都表达得出来，否则测的只是映射的一半。
        host = allowed_roots_env(self.root)
        env = {"SR_BUNDLE_DIR": self.bundle, "SR_LOCKED_DIR": "",
               "SR_AGENT_DB": os.path.join(self._tmp.name, "db.sqlite"),
               "SR_DRIVE_MAP": ";".join(
                   p for p in (f"W:={self.root.as_posix()}",
                               host.get("SR_DRIVE_MAP")) if p),
               "SR_ALLOWED_ROOTS": f"W:\\;{host['SR_ALLOWED_ROOTS']}"}
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)

    @property
    def win_path(self) -> str:
        """用户在客户端真正会粘的那一串。"""
        rel = self.scene.relative_to(self.root).as_posix()
        return "W:\\" + rel.replace("/", "\\")

    def _tool_params(self, **kw) -> dict:
        from backend.tools.run_sr import run_run_sr

        captured = {}

        def spy(params, **k):
            captured.update(params)
            raise RuntimeError("stop before the scheduler")   # 不需要真 sbatch

        with mock.patch.object(svc, "submit_run_sr", side_effect=spy):
            r = run_run_sr(**kw)
        self.assertFalse(r["ok"])
        return captured

    def _tool_result(self, **kw) -> dict:
        """工具入口的原样返回值 —— 要断言 error 文本时用它（`_tool_params`
        只回被捕获的参数，被拒的提交根本走不到捕获点）。"""
        from backend.tools.run_sr import run_run_sr

        with mock.patch.object(svc, "submit_run_sr",
                               side_effect=RuntimeError("no sbatch")):
            return run_run_sr(**kw)

    def test_windows_form_normalizes_to_array_posix(self):
        from backend.api.platform import _norm_sr_params

        rest = _norm_sr_params({"lq_path": self.win_path, "mask_path": None,
                                "suffix": ""})
        posix = self.scene.as_posix()
        self.assertEqual(rest["lq_path"], posix)
        self.assertNotIn("\\", rest["lq_path"])              # 反斜杠不进存储层
        self.assertEqual(rest["mask_path"], self.mask.as_posix())

    def test_two_spellings_are_one_submission(self):
        """两种写法 → 同一个指纹（幂等表按指纹建键，这条不成立就会重复投作业）。"""
        from backend.api.platform import _norm_sr_params

        win = _norm_sr_params({"lq_path": self.win_path, "suffix": ""})
        posix = _norm_sr_params({"lq_path": self.scene.as_posix(), "suffix": ""})
        self.assertEqual(win, posix)
        self.assertEqual(svc.task_fingerprint(win), svc.task_fingerprint(posix))

    def test_tool_entry_accepts_the_same_two_forms(self):
        a = self._tool_params(lq_path=self.win_path, suffix="")
        b = self._tool_params(lq_path=self.scene.as_posix(), suffix="")
        self.assertEqual(a, b)
        self.assertEqual(svc.task_fingerprint(a), svc.task_fingerprint(b))

    def test_both_entries_agree_on_one_submission(self):
        from backend.api.platform import _norm_sr_params

        tool = self._tool_params(lq_path=self.win_path, suffix="")
        rest = _norm_sr_params({"lq_path": self.win_path, "suffix": ""})
        self.assertEqual(tool, rest)
        self.assertEqual(svc.task_fingerprint(tool), svc.task_fingerprint(rest))

    def test_mask_path_is_normalized_too(self):
        # 显式掩码路径也可能被粘成 Windows 形态 —— 同样要落到存储层的 POSIX 形态
        win_mask = self.win_path + "\\" + self.mask.name
        tool = self._tool_params(lq_path=self.win_path, mask_path=win_mask,
                                 suffix="")
        self.assertEqual(tool["mask_path"], self.mask.as_posix())

    def test_outside_whitelist_rejected_by_both_entries(self):
        """白名单外的路径，两个入口都必须报错（同一套规则，不是各判各的）。"""
        from backend.api.platform import _norm_sr_params
        from fastapi import HTTPException

        outside = self.root.parent / "not-allowed" / self.SCENE_NAME
        outside.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(outside.parent, ignore_errors=True))
        win_out = "X:\\not-allowed\\" + self.SCENE_NAME
        # 研发机上把临时根的父目录补一条盘符映射，好让"白名单外"能用真实路径
        # 表达。盘符自映射那条要留着 —— 丢了的话 SR_ALLOWED_ROOTS 里的本机条目
        # 先解析失败，测的就不是"前缀比较"这一层了。
        maps = f"W:={self.root.as_posix()};X:={self.root.parent.as_posix()}"
        extra = allowed_roots_env(self.root).get("SR_DRIVE_MAP")
        if extra:
            maps += f";{extra}"
        with mock.patch.dict(os.environ, {"SR_DRIVE_MAP": maps}):
            r = self._tool_result(lq_path=win_out, suffix="")
            self.assertFalse(r["ok"])
            self.assertIn("allowed array prefix", r["error"])
            with self.assertRaises(HTTPException) as ctx:
                _norm_sr_params({"lq_path": win_out, "suffix": ""})
            self.assertEqual(ctx.exception.status_code, 400)
            self.assertIn("SR_ALLOWED_ROOTS", ctx.exception.detail)


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
