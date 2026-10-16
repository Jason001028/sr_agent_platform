"""Tests for run_sr (config XML, run script, submit, status query).

Nothing here starts an SR run: the executor's six entry points are replaced by
the in-memory double in backend/tests/exec_double.py, so the cases are about the
task service — what files a submit writes, which run a replay reuses, where the
verdict file is looked for. The run script is a shell artefact and is asserted
by string, since pinning its exact shape is the only way to check a CentOS7 run
script from this machine.
"""

import json
import os
import re
import shutil
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest import mock

from backend.services import run_sr as svc
from backend.services import store as store_mod
from backend.tests import allowed_roots_env, make_scene_dir
from backend.tests.exec_double import fake_exec
from backend.tools.run_sr import run_run_sr

# full param dict shaped like the tool's, for fingerprint-stable submits
PARAMS = {"lq_path": "/data", "mask_path": None, "sr_scale": 2, "suffix": "t1",
          "gpu": 0, "cloud_limit": 80, "delete_ori": False, "grid_align": True,
          "options_yml": None}

#: A resolved nosr_swap_plan (run_sr.nosr_swap_plan) as the run script sees
#: it: absolute paths, the two scratch files under the scene's Debug/.
SWAP = {"body": "/D/scene/PAN.tif",
        "source": "/D/scene/PAN_NOSR.tif",
        "backup": "/D/scene/Debug/PAN.tif.srbak",
        "mask": "/D/scene/PAN_mask.tif",
        "mask_1x": "/D/scene/Debug/PAN_mask.tif.sr1x"}


def gradient_tif(path):
    import numpy as np
    from PIL import Image
    y, x = np.mgrid[0:8, 0:8]
    Image.fromarray((y + x).astype(np.uint16)).save(path)


def temp_dir():
    """Temp dir for a case that lets the tool wrapper open the default store.

    `run_run_sr` builds its own Store and never closes it, so on Windows the
    SQLite file can still be open when the dir is removed.
    """
    try:
        return tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
    except TypeError:                        # Python < 3.10
        return tempfile.TemporaryDirectory()


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

    def test_mask_path_override_wins(self):
        # The submit entries point <MaskPath> at the mask resampled to the
        # source's pixel size; params keeps the operator's own path.
        xml = svc.build_config_xml({"lq_path": "/s", "mask_path": "/s/PAN_mask.tif"},
                                   mask_path="/s/Debug/PAN_mask.tif.sr1x")
        tags = {e.tag: e.text for e in ET.fromstring(xml)}
        self.assertEqual(tags["MaskPath"], "/s/Debug/PAN_mask.tif.sr1x")


class TestNosrSwapPlan(unittest.TestCase):
    """这一跑读哪份、跑完落到哪个名字（run_sr.nosr_swap_plan）。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.scene = self.root / "SCENE_L1_PAN"
        self.scene.mkdir()
        # RC 场景：<SolarAzimuth> 空 ⇒ 输入是 PAN.tif（util.check_sr_previous_step）
        (self.scene / "SCENE_L1_PAN_meta.xml").write_text(
            "<meta><SolarAzimuth></SolarAzimuth></meta>", encoding="utf-8")
        (self.scene / "PAN.tif").write_bytes(b"body\n")
        (self.scene / "PAN_NOSR.tif").write_bytes(b"un-superresolved\n")
        (self.scene / "PAN_mask.tif").write_bytes(b"mask\n")
        self.mask = str(self.scene / "PAN_mask.tif")

    def plan(self, **kw):
        return svc.nosr_swap_plan(str(self.scene), self.mask, "260318", **kw)

    def test_body_and_source_come_from_the_scene(self):
        p = self.plan()
        self.assertEqual(p["body"], self.scene.as_posix() + "/PAN.tif")
        self.assertEqual(p["source"], self.scene.as_posix() + "/PAN_NOSR.tif")
        self.assertEqual(p["mask"], self.mask.replace("\\", "/"))

    def test_scratch_files_sit_in_debug(self):
        p = self.plan()
        self.assertEqual(p["backup"],
                         self.scene.as_posix() + "/Debug/PAN.tif.srbak")
        self.assertEqual(p["mask_1x"],
                         self.scene.as_posix() + "/Debug/PAN_mask.tif.sr1x")

    def test_product_stem_never_wins_over_the_input_stem(self):
        # <产物 stem>_NOSR 是上一次同一 suffix 的超分结果，不是未超分那份。
        # 两条名字同时在盘上时必须取输入 stem 那条。
        (self.scene / "PAN_260318_NOSR.tif").write_bytes(b"previous product\n")
        p = self.plan()
        self.assertEqual(p["source"], self.scene.as_posix() + "/PAN_NOSR.tif")

    def test_missing_un_superresolved_copy_is_refused_by_name(self):
        (self.scene / "PAN_NOSR.tif").unlink()
        with self.assertRaises(ValueError) as ctx:
            self.plan()
        self.assertIn("没有未超分那一份", str(ctx.exception))
        self.assertIn("PAN_NOSR.tif", str(ctx.exception))

    def test_missing_mask_is_refused(self):
        with self.assertRaises(ValueError) as ctx:
            svc.nosr_swap_plan(str(self.scene), str(self.scene / "none.tif"),
                               "260318")
        self.assertIn("掩码文件不存在", str(ctx.exception))

    def test_a_directory_that_is_not_a_scene_is_refused(self):
        d = self.root / "not_a_scene"
        d.mkdir()
        with self.assertRaises(ValueError) as ctx:
            svc.nosr_swap_plan(str(d), self.mask, "260318")
        self.assertIn("不是合法场景目录", str(ctx.exception))

    def test_empty_suffix_is_refused(self):
        # 空 suffix 时输出名 == 输入名，SR 的 writeTiff 会就地覆盖输入。
        for empty in ("", "   ", None):
            with self.subTest(value=empty):
                with self.assertRaises(ValueError):
                    svc.nosr_swap_plan(str(self.scene), self.mask, empty)

    def test_sc_scene_uses_the_directory_name(self):
        # SC 场景：<SolarAzimuth> 非空 ⇒ 输入是 <目录名>.tif
        (self.scene / "SCENE_L1_PAN_meta.xml").write_text(
            "<meta><SolarAzimuth>123.4</SolarAzimuth></meta>", encoding="utf-8")
        (self.scene / "SCENE_L1_PAN.tif").write_bytes(b"body\n")
        (self.scene / "SCENE_L1_PAN_NOSR.tif").write_bytes(b"un-superresolved\n")
        (self.scene / "PAN_NOSR.tif").unlink()
        mask = self.scene / "SCENE_L1_PAN_mask.tif"
        mask.write_bytes(b"mask\n")
        p = svc.nosr_swap_plan(str(self.scene), str(mask), "260318")
        self.assertEqual(p["body"], self.scene.as_posix() + "/SCENE_L1_PAN.tif")
        self.assertEqual(p["source"],
                         self.scene.as_posix() + "/SCENE_L1_PAN_NOSR.tif")


class TestBuildRunScript(unittest.TestCase):
    """The run script is asserted by string: it is a shell artefact, and the only
    way to check a CentOS7 run script from this machine is to pin its exact
    shape."""

    def setUp(self):
        self._env = {}
        for name in ("SR_VERIFY_SCRIPT", "SR_PREP_SCRIPT", "SR_PYTHON",
                     "SR_BUNDLE_DIR", "SR_SR_SCRIPT"):
            if name in os.environ:
                self._env[name] = os.environ.pop(name)
        self.addCleanup(self._restore_env)

    def _restore_env(self):
        for name in ("SR_VERIFY_SCRIPT", "SR_PREP_SCRIPT", "SR_PYTHON",
                     "SR_BUNDLE_DIR", "SR_SR_SCRIPT"):
            os.environ.pop(name, None)
        os.environ.update(self._env)

    def test_defaults(self):
        script = svc.build_run_script("/w/cfg.xml", job_id=1)
        self.assertIn("cd /DiskArray/ProductionSchedule/exe_CentOS7/SR_bundle/"
                      "mmsr_bundle/codes", script)
        self.assertIn("python code_0817_prod.py -f /w/cfg.xml", script)
        # no scheduler in the picture: nothing in the text asks one for anything
        self.assertNotIn("SBATCH", script)

    def test_python_and_bundle_dir_can_be_overridden(self):
        script = svc.build_run_script("/w/cfg.xml", job_id=1,
                                      python="/opt/venv/bin/python",
                                      bundle_dir="/opt/sr/bundle")
        self.assertIn("/opt/venv/bin/python", script)
        self.assertIn("cd /opt/sr/bundle", script)

    def test_job_id_is_required(self):
        # 校验器用它命名判定文件，而本机执行没有调度器环境变量可读：没有 job_id
        # 就写不出判定文件，也就没有任何终态可言。
        with self.assertRaises(ValueError) as ctx:
            svc.build_run_script("/w/cfg.xml", "/w")
        self.assertIn("job_id", str(ctx.exception))

    # ---- SR_SR_SCRIPT: run the deployment variant while the production
    # script stays untouched (docs/planning/sr-pipeline-restore-plan.md E-A) ---
    def test_sr_script_defaults_to_the_production_script(self):
        script = svc.build_run_script("/w/cfg.xml", job_id=1)
        self.assertIn("python code_0817_prod.py -f /w/cfg.xml", script)
        self.assertIn("echo SR_SCRIPT=code_0817_prod.py", script)

    def test_sr_script_env_switches_to_the_variant(self):
        os.environ["SR_SR_SCRIPT"] = "code_0817_prod_platform.py"
        script = svc.build_run_script("/w/cfg.xml", job_id=1)
        self.assertIn("python code_0817_prod_platform.py -f /w/cfg.xml", script)
        # and nothing may still point at the production script
        self.assertNotIn("code_0817_prod.py -f", script)

    def test_sr_script_argument_beats_the_env(self):
        os.environ["SR_SR_SCRIPT"] = "from-env.py"
        script = svc.build_run_script("/w/cfg.xml", job_id=1,
                                      sr_script="from-arg.py")
        self.assertIn("from-arg.py", script)
        self.assertNotIn("from-env.py", script)

    def test_never_assigns_cuda_visible_devices(self):
        # The card comes from the child's environment (SR_LOCAL_GPU, set by
        # local_exec), so an assignment here would be a second, disagreeing
        # source. The audit section only READS it — match assignments, not
        # mentions.
        script = svc.build_run_script("/w/c.xml", job_id=1)
        assignments = re.findall(r"^\s*(?:export\s+)?CUDA_VISIBLE_DEVICES\s*=",
                                 script, re.MULTILINE)
        self.assertEqual(assignments, [], "the script must not pick a GPU")

    def test_audit_preamble_records_the_run(self):
        script = svc.build_run_script("/w/c.xml", job_id=41,
                                      python="/opt/sr/bin/python")
        self.assertIn("JOB_ID=41", script)
        self.assertIn("CUDA_VISIBLE_DEVICES=", script)
        self.assertIn("SR_SCRIPT=", script)          # which SR script ran
        self.assertIn("SR_PYTHON=/opt/sr/bin/python", script)
        self.assertIn("$(hostname)", script)
        self.assertIn("/w/c.xml", script)
        self.assertIn("nvidia-smi --query-gpu=index,uuid", script)

    def test_the_header_says_what_runs_this_script(self):
        # 这段注释是"这个脚本在本机被谁拉起、拿哪张卡"的说明书：读日志的人先看到它。
        script = svc.build_run_script("/w/c.xml", job_id=1)
        self.assertIn("backend/services/local_exec.py", script)

    def test_pythonunbuffered_is_exported(self):
        self.assertIn("export PYTHONUNBUFFERED=1",
                      svc.build_run_script("/w/c.xml", job_id=1))

    def test_cd_failure_exits_nonzero(self):
        script = svc.build_run_script("/w/c.xml", job_id=1)
        cd_line = [ln for ln in script.splitlines() if ln.startswith("cd ")][0]
        self.assertIn("|| {", cd_line)
        self.assertIn("exit 1", cd_line)

    def test_no_set_e(self):
        # `set -e` would change the meaning of the `||`/pipeline lines below it.
        script = svc.build_run_script("/w/c.xml", job_id=1)
        self.assertNotIn("set -e", script)

    def test_verifier_runs_after_python_and_passes_its_exit_code_through(self):
        script = svc.build_run_script("/w/c.xml", job_id=1)
        lines = script.splitlines()
        run_i = lines.index("python code_0817_prod.py -f /w/c.xml")
        self.assertEqual(lines[run_i + 1], "_sr_rc=$?")
        self.assertIn("verify_sr_run.py --config /w/c.xml --sr-exit-code \"$_sr_rc\"",
                      lines[run_i + 3])
        # The verifier's own code is what the script exits with (90 = contract
        # not satisfied); taking it into a variable first keeps that explicit.
        self.assertEqual(lines[-2:], ["_v_rc=$?", "exit $_v_rc"])

    def test_the_verifier_is_always_told_the_job_id(self):
        # The verdict file is named after the run number, and with no scheduler
        # there is no environment variable to read it from — the id in the script
        # text is the only source, and it must be the same id
        # exit_code_file_for re-derives.
        script = svc.build_run_script("/w/c.xml", job_id=41)
        self.assertIn("verify_sr_run.py --config /w/c.xml --sr-exit-code "
                      '"$_sr_rc" --job-id 41', script)

    def test_no_swap_means_no_prep_and_no_landing(self):
        # submit_run_sr without a resolved plan produces the plain script: the
        # SR script reads whatever it resolves itself and nothing lands on the
        # body name.
        script = svc.build_run_script("/w/c.xml", job_id=1)
        self.assertNotIn("sr_prep_scene.py", script)
        self.assertNotIn("--land-on-body", script)

    def test_swap_runs_the_prep_between_cd_and_the_sr_script(self):
        script = svc.build_run_script("/w/c.xml", job_id=1, swap=SWAP)
        lines = script.splitlines()
        cd_i = [i for i, ln in enumerate(lines) if ln.startswith("cd ")][0]
        prep_i = [i for i, ln in enumerate(lines)
                  if "sr_prep_scene.py" in ln and "--body" in ln][0]
        run_i = [i for i, ln in enumerate(lines)
                 if "code_0817_prod.py -f /w/c.xml" in ln][0]
        self.assertLess(cd_i, prep_i)          # prep resolves after the cd
        self.assertLess(prep_i, run_i)         # and before the SR script
        self.assertEqual(
            lines[prep_i],
            "python sr_prep_scene.py --body %(body)s --nosr %(source)s"
            " --backup %(backup)s --mask %(mask)s --mask-1x %(mask_1x)s"
            " || _prep_rc=$?" % SWAP)

    def test_a_prep_failure_does_not_exit_before_the_verifier(self):
        # Exiting here would leave no verdict file at all — the platform reads
        # that as UNKNOWN and can never clear it. The prep's code goes into
        # _sr_rc and the verifier (which restores the body) still runs.
        script = svc.build_run_script("/w/c.xml", job_id=1, swap=SWAP)
        lines = script.splitlines()
        prep_i = [i for i, ln in enumerate(lines) if "sr_prep_scene.py" in ln][0]
        self.assertFalse(lines[prep_i].endswith("exit 1; }"))
        self.assertIn("_prep_rc=$?", lines[prep_i])
        self.assertIn('    _sr_rc=$_prep_rc', lines)
        ver_i = [i for i, ln in enumerate(lines) if "verify_sr_run.py" in ln][0]
        self.assertLess(prep_i, ver_i)

    def test_swap_is_skipped_from_the_sr_step_when_the_prep_failed(self):
        script = svc.build_run_script("/w/c.xml", job_id=1, swap=SWAP)
        lines = script.splitlines()
        self.assertIn("    python code_0817_prod.py -f /w/c.xml", lines)
        self.assertNotIn("python code_0817_prod.py -f /w/c.xml", lines)

    def test_swap_asks_the_verifier_to_land_the_product_on_the_body(self):
        script = svc.build_run_script("/w/c.xml", job_id=7, swap=SWAP)
        self.assertIn(
            "verify_sr_run.py --config /w/c.xml --sr-exit-code \"$_sr_rc\""
            " --land-on-body %(body)s --backup %(backup)s"
            " --mask-1x %(mask_1x)s --job-id 7" % SWAP, script)

    def test_prep_script_can_be_overridden(self):
        os.environ["SR_PREP_SCRIPT"] = "/opt/sr/bin/my_prep.py"
        script = svc.build_run_script("/w/c.xml", job_id=1, swap=SWAP)
        self.assertIn("/opt/sr/bin/my_prep.py --body", script)

    def test_verify_script_can_be_overridden(self):
        os.environ["SR_VERIFY_SCRIPT"] = "/opt/sr/bin/my_verify.py"
        script = svc.build_run_script("/w/c.xml", job_id=1)
        self.assertIn("/opt/sr/bin/my_verify.py --config /w/c.xml", script)

    def test_the_log_says_the_run_writes_in_place(self):
        # The job writes into the scene dir: the result tif lands next to the
        # input, and a file already sitting at that output path is renamed to
        # *_NOSR.tif before being overwritten (util.writeTiff renames the output
        # path, not the input), so the job log has to say so (plan §4.2, last
        # bullet). A 4.9 GB previous output is the file that gets renamed.
        script = svc.build_run_script("/w/c.xml", job_id=1)
        self.assertIn("IN PLACE", script)
        self.assertIn("input tif is not renamed or deleted", script)
        self.assertIn("_NOSR.tif", script)


class TestSubmitRunSr(unittest.TestCase):
    """submit_run_sr: assemble config + script, then hand the script over."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._work = tempfile.TemporaryDirectory()
        self.addCleanup(self._work.cleanup)
        self._env = mock.patch.dict(os.environ, {
            "SR_WORK_DIR": self._work.name,
            "SR_BUNDLE_DIR": os.path.join(self._tmp.name, "bundle")})
        self._env.start()
        self.addCleanup(self._env.stop)
        self.store = store_mod.Store(os.path.join(self._tmp.name, "db.sqlite"))
        self.addCleanup(self.store.close)
        self._exec = fake_exec(first_id=42)
        self.exec = self._exec.start()
        self.addCleanup(self._exec.stop)

    def test_no_usable_bash_is_refused_before_anything_happens(self):
        # No bash on this host means nothing could be started at all — and the
        # refusal comes before any side effect, so no task row is left behind
        # for a job that never existed.
        self.exec.bash_available = False
        with self.assertRaises(RuntimeError) as ctx:
            svc.submit_run_sr({"lq_path": "/data"}, store=self.store)
        self.assertIn("本机执行不可用", str(ctx.exception))
        self.assertEqual(self.exec.jobs, {})               # nothing was started
        self.assertEqual(self.store.list_sr_tasks(), [])   # nothing was recorded

    def test_happy_path_writes_the_files_and_starts_the_run(self):
        data = svc.submit_run_sr(PARAMS, store=self.store)
        self.assertEqual(data["status"], "SUBMITTED")
        self.assertEqual(data["job_id"], 42)
        self.assertTrue(Path(data["config_xml"]).exists())
        self.assertTrue(Path(data["batch_script"]).exists())
        script = Path(data["batch_script"]).read_text(encoding="utf-8")
        self.assertIn("JOB_ID=42", script)
        # the path goes into the text bash-ified: the script is run by bash,
        # which cannot take the Windows backslashes this test host produces
        self.assertIn("-f " + data["config_xml"].replace("\\", "/"), script)
        # the executor was handed exactly that script, under exactly that id
        self.assertEqual(self.exec.submitted(42)["script"],
                         data["batch_script"])
        task = self.store.get_sr_task(svc.task_fingerprint(PARAMS))
        self.assertEqual(task["job_id"], 42)
        self.assertEqual(task["status"], "submitted")
        self.assertEqual(task["batch_script"], data["batch_script"])

    def test_file_names_carry_suffix_fingerprint_and_job_id(self):
        """三样都得有，缺一样就会撞名。

        只用 suffix：不同场景撞（同一个 work dir 里互相覆盖）；只用指纹：同一次
        提交重跑撞（正在跑的那次的 config 被覆盖，而 exit_code_file_for 是从
        config 里读 <DatarootLQ> 去找判定文件的）。
        """
        data = svc.submit_run_sr(PARAMS, store=self.store)
        fp = svc.task_fingerprint(PARAMS)
        for key in ("config_xml", "batch_script"):
            name = Path(data[key]).name
            self.assertIn("t1", name)                       # suffix
            self.assertIn(fp[:svc.RUN_KEY_LEN], name)        # fingerprint
            self.assertIn("42", name)                        # job id
        # and the pair lives in the configured work dir, nothing else does
        self.assertEqual(Path(data["config_xml"]).parent, Path(self._work.name))

    def test_the_config_points_at_the_scene_it_will_write(self):
        # 就地跑：config 里的 DatarootLQ 就是用户填的那个目录（产物落在那里，
        # 判定文件的路径也从它推出来）。
        data = svc.submit_run_sr(PARAMS, store=self.store)
        self.assertEqual(svc.read_dataroot_lq(data["config_xml"]), PARAMS["lq_path"])


class TestRunSrIdempotency(unittest.TestCase):
    """P0② — the sr_tasks table prevents duplicate submission on replay.

    "Is a run still going" is answered by the executor; these cases ask the task
    service what it does with that answer, so the executor is the in-memory
    double. A finished run is expressed the way a real one expresses it — a
    verdict file in the scene's Debug/ — and that file is parsed by the real
    parser (see exec_double).
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._work = tempfile.TemporaryDirectory()
        self.scene = self._tmp.name
        env = mock.patch.dict(os.environ, {"SR_WORK_DIR": self._work.name})
        env.start()
        self.addCleanup(env.stop)
        self.addCleanup(self._tmp.cleanup)
        self.addCleanup(self._work.cleanup)
        self.store = store_mod.Store(os.path.join(self._tmp.name, "db.sqlite"))
        # addCleanup runs LIFO: register the temp dirs first so the SQLite handle
        # (store.close) is released before they are removed (Windows).
        self.addCleanup(self.store.close)
        self._exec = fake_exec(first_id=42)
        self.exec = self._exec.start()
        self.addCleanup(self._exec.stop)

    @property
    def params(self):
        """The same logical submit, but its scene is this case's temp dir."""
        return {**PARAMS, "lq_path": self.scene}

    def write_verdict(self, job_id, verdict=0, sr_exit_code=0):
        """Drop the job's own verdict file where the platform looks for it."""
        debug = Path(self.scene) / "Debug"
        debug.mkdir(parents=True, exist_ok=True)
        (debug / svc.EXIT_FILE_FMT.format(job_id=job_id)).write_text(
            "job_id=%s\nsr_exit_code=%s\nverdict=%s\nskip=0\n"
            % (job_id, sr_exit_code, verdict), encoding="utf-8")

    def test_fingerprint_stable_under_key_order(self):
        a = svc.task_fingerprint({"x": 1, "y": [1, 2]})
        b = svc.task_fingerprint({"y": [1, 2], "x": 1})
        self.assertEqual(a, b)

    def test_first_submit_creates_task_with_job_id(self):
        data = svc.submit_run_sr(self.params, store=self.store)
        self.assertEqual(data["status"], "SUBMITTED")
        self.assertEqual(data["job_id"], 42)
        task = self.store.get_sr_task(svc.task_fingerprint(self.params))
        self.assertIsNotNone(task)
        self.assertEqual(task["job_id"], 42)
        self.assertEqual(task["status"], "submitted")

    def test_replay_active_reuses_no_resubmit(self):
        # first submit lands job 42; a replay while it is still going → reuse.
        svc.submit_run_sr(self.params, store=self.store)

        data = svc.submit_run_sr(self.params, store=self.store)
        self.assertEqual(data["status"], "RESUMED_ACTIVE")
        self.assertEqual(data["job_id"], 42)
        self.assertIn(data["state"], ("PENDING", "RUNNING"))
        self.assertTrue(data["idempotent"])
        self.assertEqual(list(self.exec.jobs), [42])       # nothing new started

    def test_replay_contract_satisfied_reuses_no_resubmit(self):
        svc.submit_run_sr(self.params, store=self.store)
        self.write_verdict(42)

        data = svc.submit_run_sr(self.params, store=self.store)
        self.assertEqual(data["status"], "RESUMED_COMPLETED")
        self.assertEqual(data["job_id"], 42)
        self.assertEqual(data["exit_code"], "0:0")
        self.assertTrue(data["idempotent"])
        self.assertEqual(list(self.exec.jobs), [42])

    def test_replay_silent_failure_reruns_instead_of_caching_success(self):
        # §2.3 毒药: exit 0 but the contract was not met. This must NOT be
        # RESUMED_COMPLETED — the scene would stay un-super-resolved forever.
        svc.submit_run_sr(self.params, store=self.store)
        self.write_verdict(42, verdict=90, sr_exit_code=1)

        data = svc.submit_run_sr(self.params, store=self.store)
        self.assertEqual(data["status"], "SUBMITTED")
        self.assertEqual(data["job_id"], 43)          # a fresh job, not 42
        self.assertEqual(data["previous_state"], "FAILED")
        self.assertEqual(data["previous_exit_code"], "1:0")
        self.assertEqual(data["previous_job_id"], 42)
        self.assertEqual(sorted(self.exec.jobs), [42, 43])

    def test_replay_unknown_refuses_to_resubmit(self):
        # No verdict file and no process this host can identify: nothing proves
        # the first run is over, so a second one writing the same scene is
        # exactly what must not happen. The operator decides, not the replay.
        svc.submit_run_sr(self.params, store=self.store)
        self.exec.jobs.clear()                       # as if this process restarted

        with self.assertRaises(svc.SubmitConflict) as ctx:
            svc.submit_run_sr(self.params, store=self.store)
        self.assertEqual(ctx.exception.kind, "unknown")
        self.assertEqual(self.exec.jobs, {})

    def test_interrupted_submit_no_job_id_does_not_resubmit(self):
        # intent recorded, the launch outcome never persisted → unknown.
        self.store.put_sr_task(svc.task_fingerprint(self.params), self.params,
                               status="new", job_id=None)
        with self.assertRaises(svc.SubmitConflict) as ctx:
            svc.submit_run_sr(self.params, store=self.store)
        self.assertEqual(ctx.exception.kind, "interrupted")
        self.assertEqual(self.exec.jobs, {})          # nothing was started

    def test_different_params_are_distinct_jobs(self):
        a = svc.submit_run_sr({**self.params, "suffix": "a"}, store=self.store)
        b = svc.submit_run_sr({**self.params, "suffix": "b"}, store=self.store)
        self.assertEqual(a["job_id"], 42)
        self.assertEqual(b["job_id"], 43)
        self.assertIsNotNone(self.store.get_sr_task(
            svc.task_fingerprint({**self.params, "suffix": "a"})))
        self.assertIsNotNone(self.store.get_sr_task(
            svc.task_fingerprint({**self.params, "suffix": "b"})))


class TestJobStatusQuery(unittest.TestCase):
    """Terminal state comes from the run's own verdict file, not from a
    scheduler: accounting is disabled, so there is no accounting oracle to ask
    (sr-local-execution.md §2.4-2 / §一 "C 方案")."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.scene = self._tmp.name
        self._exec = fake_exec()
        self.exec = self._exec.start()
        self.addCleanup(self._exec.stop)

    def task(self, job_id=7, config_xml=None) -> dict:
        """A task row as the store hands it back: the run id and, when it has
        one, the config that this run's verdict path is derived from."""
        return {"job_id": job_id, "config_xml": config_xml}

    def config_for(self, scene=None) -> str:
        path = Path(self._tmp.name) / "run.xml"
        path.write_text(svc.build_config_xml({"lq_path": scene or self.scene}),
                        encoding="utf-8")
        return str(path)

    def verdict_file(self, job_id=7, verdict=0, sr_exit_code=0, skip=0):
        debug = Path(self.scene) / "Debug"
        debug.mkdir(parents=True, exist_ok=True)
        path = debug / svc.EXIT_FILE_FMT.format(job_id=job_id)
        path.write_text(
            "job_id=%s\nsr_exit_code=%s\nverdict=%s\nskip=%s\n"
            % (job_id, sr_exit_code, verdict, skip), encoding="utf-8")
        return str(path)

    def test_completed_from_the_verdict_file(self):
        self.verdict_file(job_id=7)
        st = svc.query_job_status(7, self.task(config_xml=self.config_for()))
        self.assertEqual(st["active"], False)
        self.assertEqual(st["state"], "COMPLETED")
        self.assertEqual(st["exit_code"], "0:0")

    def test_contract_not_satisfied_is_failed(self):
        # The silent-failure shape: exit 0, but the run's own verifier says the
        # contract was not met. An executor would call this COMPLETED; the
        # platform must not.
        self.verdict_file(job_id=7, verdict=90)
        st = svc.query_job_status(7, self.task(config_xml=self.config_for()))
        self.assertEqual(st["state"], "FAILED")
        self.assertEqual(st["exit_code"], "0:0")

    def test_nonzero_sr_exit_is_failed(self):
        self.verdict_file(job_id=7, sr_exit_code=3, verdict=90)
        st = svc.query_job_status(7, self.task(config_xml=self.config_for()))
        self.assertEqual(st["state"], "FAILED")
        self.assertEqual(st["exit_code"], "3:0")

    def test_verdict_for_another_job_is_ignored(self):
        # Job ids get recycled; a stranger's verdict must not be inherited.
        self.verdict_file(job_id=99)
        self.assertEqual(
            svc.query_job_status(7, self.task(config_xml=self.config_for()))
            ["state"], "UNKNOWN")

    def test_unknown_without_a_verdict_file(self):
        st = svc.query_job_status(7, self.task(config_xml=self.config_for()))
        self.assertEqual(st["state"], "UNKNOWN")
        self.assertIsNone(st["active"])               # not False: cannot tell

    def test_a_task_without_a_config_xml_does_not_raise(self):
        # Every row written before this schema existed, and any row whose config
        # cannot be read: no path to derive a verdict file from, so the answer is
        # the executor's own — never an exception that turns a status poll into a
        # 500.
        st = svc.query_job_status(7, self.task())
        self.assertEqual(st["state"], "UNKNOWN")

    def test_cancel_goes_to_the_executor(self):
        self.exec.submit("/w/run.sh", job_id=7)
        self.assertTrue(svc.cancel_run_sr({"job_id": 7}))
        self.assertEqual(
            svc.query_job_status(7, self.task())["state"], "CANCELLED")

    def test_confirmed_stopped_releases_an_unidentifiable_run(self):
        # The operator says they checked by hand: that is what moves a run the
        # platform itself cannot find any trace of.
        self.assertTrue(svc.cancel_run_sr({"job_id": 7}, confirmed_stopped=True))
        st = svc.query_job_status(7, self.task())
        self.assertEqual(st["state"], "CANCELLED")
        self.assertEqual(st["active"], False)

    def test_cancel_without_a_job_id_is_false(self):
        self.assertFalse(svc.cancel_run_sr({"job_id": None}))


#: 工具入口（`run_run_sr`）会过提交侧白名单 SR_ALLOWED_ROOTS —— 这些用例
#: 只想验证"能走到服务层"，所以用一个默认白名单内的路径。
SCENE = "/DiskArray/GSHC2IMPS/PRODUCT/2026/09/17/260318"


class ToolEntrySceneCase(unittest.TestCase):
    """工具入口的基类：一个本机上的真场景 + 把它放进白名单的环境。

    走到服务层的用例必须有个真场景目录 —— 提交侧会去 stat 它，确认这一跑要读的
    未超分那一份在场（见 services/run_sr.nosr_swap_plan）。

    执行器和幂等表都是本用例自己的：工作目录与 SR_AGENT_DB 各占一个临时目录，
    执行器入口换成替身（backend/tests/exec_double.py）。于是"工具入口把参数送到了
    服务层、作业真的起来了"这句话可以整串断言，而不用在研发机上跑 bash。
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.scene = make_scene_dir(self._tmp.name, "SCENE_L1_PAN", rc=True)
        self._work = temp_dir()
        self.addCleanup(self._work.cleanup)
        self._db = temp_dir()
        self.addCleanup(self._db.cleanup)
        env = {"SR_BUNDLE_DIR": os.path.join(self._tmp.name, "bundle"),
               "SR_LOCKED_DIR": "",
               "SR_WORK_DIR": self._work.name,
               "SR_AGENT_DB": os.path.join(self._db.name, "db.sqlite"),
               **allowed_roots_env(self._tmp.name)}
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)
        self._exec = fake_exec(first_id=42)
        self.exec = self._exec.start()
        self.addCleanup(self._exec.stop)

    @property
    def lq(self) -> str:
        return str(self.scene)

    def tasks(self) -> list:
        """这一跑留下的任务行（工具入口自己开的库，写完就丢）。"""
        return store_mod.default_store().list_sr_tasks()


class TestToolRunSr(ToolEntrySceneCase):
    def test_missing_lq_path(self):
        r = run_run_sr()
        self.assertFalse(r["ok"])
        self.assertIn("lq_path", r["error"])

    def test_bad_sr_scale(self):
        r = run_run_sr(lq_path=self.lq, sr_scale=0)
        self.assertFalse(r["ok"])
        self.assertIn("sr_scale", r["error"])

    def test_a_valid_submit_starts_one_run(self):
        r = run_run_sr(lq_path=self.lq)
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(r["data"]["job_id"], 42)
        self.assertEqual(list(self.exec.jobs), [42])
        self.assertIn("run_sr_", self.exec.submitted(42)["script"])

    def test_delete_ori_is_rejected(self):
        # 参数校验先于提交：delete_ori 在原型期禁用，拒绝发生在作业起来之前。
        r = run_run_sr(lq_path=self.lq, delete_ori=True)
        self.assertFalse(r["ok"])
        self.assertIn("delete_ori", r["error"])
        self.assertEqual(self.exec.jobs, {})

    def test_bad_suffix_is_rejected_like_the_rest_entry_point(self):
        # 工具侧过去完全不校验 suffix —— 一个 `a/b` 会一路拼进输出文件名。
        for bad in ("a/b", "a b", "x" * 17, "掩码"):
            with self.subTest(suffix=bad):
                r = run_run_sr(lq_path=self.lq, suffix=bad)
                self.assertFalse(r["ok"])
                self.assertIn("suffix 非法", r["error"])

    def test_empty_suffix_is_defaulted_not_rejected(self):
        # 留空 = 取 SR 配置里的 <Suffix>，与 REST 入口同规矩。临时 bundle 里没有
        # SR 团队那份配置，于是回落到内置值 —— 值本身不重要，重要的是它进了任务
        # 参数：两个入口就是靠这个字符串算出同一个指纹的。
        r = run_run_sr(lq_path=self.lq, suffix="   ")
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(self.tasks()[0]["params"]["suffix"], "sr")

    def test_missing_un_superresolved_copy_is_refused(self):
        # 缺未超分那一份 → err 指名缺的那份，不拿别的顶替（与 REST 入口同一条规则）。
        (self.scene / "PAN_NOSR.tif").unlink()
        r = run_run_sr(lq_path=self.lq)
        self.assertFalse(r["ok"])
        self.assertIn("没有未超分那一份", r["error"])
        self.assertIn("PAN_NOSR.tif", r["error"])
        self.assertEqual(self.exec.jobs, {})


class TestToolRunSrPathGuard(ToolEntrySceneCase):
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
        r = run_run_sr(lq_path=self.lq, mask_path="<fake>/mask.tif")
        self.assertFalse(r["ok"])
        self.assertIn("mask_path", r["error"])
        self.assertIn("fake", r["error"])

    def test_relative_mask_path_rejected(self):
        r = run_run_sr(lq_path=self.lq, mask_path="mask.tif")
        self.assertFalse(r["ok"])
        self.assertIn("mask_path", r["error"])
        self.assertIn("absolute", r["error"])

    def test_valid_paths_reach_the_service(self):
        # 合法绝对路径必须过校验、真的起一个作业 —— 即路径守卫没误伤。
        r = run_run_sr(lq_path=self.lq,
                       mask_path=str(self.scene / "PAN_mask.tif"))
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(len(self.exec.jobs), 1)

    def test_registry_dispatch_is_the_guarded_tool(self):
        # the loop executes tools through the registry (call_tool → t.run),
        # not run_run_sr directly — lock that the registered "run_sr" is the
        # path-guarded wrapper.
        from backend.agent.loop import call_tool
        r = call_tool("run_sr",
                      json.dumps({"lq_path": "<fake>/GF07A03_PMS01.tif"}))
        self.assertFalse(r["ok"])
        self.assertIn("fake", r["error"])
        self.assertEqual(self.exec.jobs, {})


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
        # 场景目录：REST 入口会推导 <目录名>_mask.tif，没有就 400；两个入口还要
        # 在那里找到未超分那一份（run_sr.nosr_swap_plan），没有就拦下。
        self.scene = str(make_scene_dir(self._tmp.name, "SCENE_L1_PAN"))
        self.mask = os.path.join(self.scene, "SCENE_L1_PAN_mask.tif")
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
            raise RuntimeError("stop before the executor")   # 不需要真起进程

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
        self.scene = make_scene_dir(
            self.root / "GSHC2IMPS" / "PRODUCT" / "2026" / "09" / "17",
            self.SCENE_NAME)
        self.mask = self.scene / f"{self.SCENE_NAME}_mask.tif"
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
            raise RuntimeError("stop before the executor")   # 不需要真起进程

        with mock.patch.object(svc, "submit_run_sr", side_effect=spy):
            r = run_run_sr(**kw)
        self.assertFalse(r["ok"])
        return captured

    def _tool_result(self, **kw) -> dict:
        """工具入口的原样返回值 —— 要断言 error 文本时用它（`_tool_params`
        只回被捕获的参数，被拒的提交根本走不到捕获点）。"""
        from backend.tools.run_sr import run_run_sr

        with mock.patch.object(svc, "submit_run_sr",
                               side_effect=RuntimeError("stop before the executor")):
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


class TestToolJobStatus(ToolEntrySceneCase):
    """sr_job_status：拿运行编号去任务表里找那一行，再问统一查询。

    编号本身说不清要读哪个场景的判定文件（那个路径是从任务自己的 config.xml
    推出来的），所以这一步不是查一张表就够了的事。
    """

    def test_missing_job_id(self):
        from backend.tools.sr_job_status import run_sr_job_status
        r = run_sr_job_status()
        self.assertFalse(r["ok"])
        self.assertIn("job_id", r["error"])

    def test_unknown_job_id(self):
        # 空库：没有任何任务行带这个编号。这不是"查不到状态"，而是"根本不知道
        # 你在说哪一次运行"，所以是 err，不会去问执行器。
        from backend.tools.sr_job_status import run_sr_job_status
        r = run_sr_job_status(job_id=1)
        self.assertFalse(r["ok"])
        self.assertIn("unknown job_id", r["error"])
        self.assertEqual(self.exec.jobs, {})

    def test_a_submitted_job_is_reported_with_its_scene(self):
        from backend.tools.sr_job_status import run_sr_job_status
        submitted = run_run_sr(lq_path=self.lq)
        self.assertTrue(submitted["ok"], submitted.get("error"))

        r = run_sr_job_status(job_id=submitted["data"]["job_id"])
        self.assertTrue(r["ok"], r.get("error"))
        self.assertIn(r["data"]["state"], ("PENDING", "RUNNING"))
        # 存进去的是存储层的 POSIX 形态（提交侧归一化过），同一个目录
        self.assertEqual(Path(r["data"]["lq_path"]), Path(self.lq))
        self.assertEqual(r["data"]["job_id"], submitted["data"]["job_id"])


if __name__ == "__main__":
    unittest.main()
