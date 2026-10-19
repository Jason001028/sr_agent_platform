"""Tests for the in-job SR contract verifier (SR_code/variants/verify_sr_run.py).

The verifier lives outside the backend package and runs under the SR job
interpreter (py3.6, stdlib only), so it is loaded by path via importlib — an
`import` statement would neither find it nor prove it stays dependency-free.

Every branch of the three-condition contract is pinned here, plus the naming
contract with backend/services/run_sr.py: the platform recomputes the verdict
file path from config.xml, so if the two ever drift, terminal job states stop
resolving and the idempotency layer silently degrades.
"""

import importlib.util
import io
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[2]
VERIFIER_PATH = REPO_ROOT / "SR_code" / "variants" / "verify_sr_run.py"

#: verdict-file name fragment, used to make the "write is denied" test target
#: exactly the verdict write and nothing else.
EXIT_MARK = "_SREXIT_"

CONFIG_TEMPLATE = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    "<SFSR_Config>\n"
    "  <DatarootLQ>{lq}</DatarootLQ>\n"
    "  <Suffix>{suffix}</Suffix>\n"          # "" renders as <Suffix /> (empty)
    "  <OPT>{opt}</OPT>\n"
    "</SFSR_Config>\n"
)
OPT_YML = "name: test\nscale: 2\nt_ht: 1000\ntiftype: .tif\n"
META_SC = "<meta><SolarAzimuth>123.4</SolarAzimuth></meta>"
META_RC = "<meta><SolarAzimuth></SolarAzimuth></meta>"


def load_verifier():
    spec = importlib.util.spec_from_file_location("verify_sr_run", VERIFIER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


verify = load_verifier()


def run_cli(*args, env=None):
    """Run the verifier as a subprocess (proves the CLI + exit codes)."""
    return subprocess.run(
        [sys.executable, str(VERIFIER_PATH)] + list(args),
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=60, env=env)


class ContractFixture:
    """A throwaway scene dir laid out exactly like the pipeline's.

    scene_dir/
      <scene>_meta.xml          SolarAzimuth decides RC vs SC
      Debug/<scene>_SRLOG.txt   the log the contract reads the last line of
      <scene>_<suffix>.tif      the output (SC step)
    """

    def __init__(self, tmpdir, scene="SCENE_L1_PAN", suffix="t1", meta=META_SC):
        self.tmp = Path(tmpdir)
        self.scene = scene
        self.suffix = suffix
        self.lq = self.tmp / scene
        self.lq.mkdir(parents=True, exist_ok=True)
        (self.lq / "Debug").mkdir(exist_ok=True)
        (self.lq / (scene + "_meta.xml")).write_text(meta, encoding="utf-8")
        self.opt = self.tmp / "opt.yml"
        self.opt.write_text(OPT_YML, encoding="utf-8")
        self.config = self.tmp / "cfg.xml"
        self.config.write_text(
            CONFIG_TEMPLATE.format(lq=self.lq, suffix=suffix, opt=self.opt),
            encoding="utf-8")

    # ---- artefact helpers ------------------------------------------------
    @property
    def img_name(self):
        """Input name per util.get_l1_pan_tif_rcsc (RC → PAN.tif)."""
        meta = (self.lq / (self.scene + "_meta.xml")).read_text(encoding="utf-8")
        empty_solar = re.search(r"<SolarAzimuth>\s*</SolarAzimuth>", meta)
        return "PAN.tif" if empty_solar else self.scene + ".tif"

    @property
    def srlog(self):
        return self.lq / "Debug" / (self.img_name[:-4] + "_SRLOG.txt")

    @property
    def output(self):
        stem = self.img_name[:-4]
        name = stem + "_" + self.suffix if self.suffix else stem
        return self.lq / (name + ".tif")

    def write_srlog(self, body):
        self.srlog.write_text(body, encoding="utf-8")
        return self.srlog

    def write_output(self, size=64):
        self.output.write_bytes(b"x" * size)
        return self.output

    def touch_newer_than_config(self, *paths):
        """Push mtimes past config.xml so the staleness filter is happy."""
        stamp = self.config.stat().st_mtime + 5
        for p in paths:
            os.utime(str(p), (stamp, stamp))

    def check(self, sr_exit_code=0, job_id=42):
        return verify.check_contract(str(self.config), sr_exit_code,
                                     job_id=job_id)


class VerifyContractTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.fx = ContractFixture(self._tmp.name)

    # ---- thin delegation to the fixture (keeps the cases readable) -------
    def write_srlog(self, body):
        return self.fx.write_srlog(body)

    def write_output(self, size=64):
        return self.fx.write_output(size)

    def touch_newer_than_config(self, *paths):
        return self.fx.touch_newer_than_config(*paths)

    def check(self, sr_exit_code=0, job_id=42):
        return self.fx.check(sr_exit_code, job_id=job_id)

    @property
    def srlog(self):
        return self.fx.srlog

    @property
    def output(self):
        return self.fx.output

    def assertSatisfied(self, verdict):
        self.assertTrue(verdict.ok, "expected satisfied, got %r" % (verdict.reasons,))

    def assertFailed(self, verdict, needle=None):
        self.assertFalse(verdict.ok, "expected failure, got ok")
        self.assertTrue(verdict.reasons, "a failure must say why")
        if needle:
            self.assertIn(needle, verdict.reason_text)

    # ---- the happy path --------------------------------------------------
    def test_sc_input_with_suffix_satisfies_all_three(self):
        self.write_srlog("junk\nRun finished.")
        self.write_output()
        self.touch_newer_than_config(self.srlog, self.output)
        v = self.check(0)
        self.assertSatisfied(v)
        self.assertFalse(v.skip)
        self.assertEqual(Path(v.output), self.fx.output)

    def test_rc_input_name_derivation_uses_pan_tif(self):
        fx = ContractFixture(self._tmp.name, scene="RC_SCENE", meta=META_RC,
                             suffix="t1")
        self.assertEqual(fx.img_name, "PAN.tif")
        self.assertEqual(fx.srlog.name, "PAN_SRLOG.txt")
        self.assertEqual(fx.output.name, "PAN_t1.tif")
        fx.write_srlog("x\nRun finished.")
        fx.write_output()
        fx.touch_newer_than_config(fx.srlog, fx.output)
        self.assertSatisfied(fx.check(0))

    def test_sc_input_name_derivation_uses_scene_dir_name(self):
        self.assertEqual(self.fx.img_name, self.fx.scene + ".tif")
        self.assertEqual(self.fx.srlog.name, self.fx.scene + "_SRLOG.txt")

    def test_empty_suffix_output_keeps_input_stem(self):
        # <Suffix /> means None to util.get_cfg_value, so no "_" is appended and
        # the output name equals the input name — the known-destructive case.
        fx = ContractFixture(self._tmp.name, scene="NOSUF", suffix="")
        self.assertEqual(fx.output.name, "NOSUF.tif")
        fx.write_srlog("x\nRun finished.")
        fx.write_output()
        fx.touch_newer_than_config(fx.srlog, fx.output)
        self.assertSatisfied(fx.check(0))

    def test_non_empty_suffix_is_joined_with_underscore(self):
        self.assertEqual(self.fx.output.name, self.fx.scene + "_t1.tif")

    # ---- condition 1: exit code -----------------------------------------
    def test_nonzero_exit_fails(self):
        self.write_srlog("x\nRun finished.")
        self.write_output()
        self.touch_newer_than_config(self.srlog, self.output)
        self.assertFailed(self.check(1), "退出码")

    def test_missing_exit_code_fails(self):
        self.write_srlog("x\nRun finished.")
        self.write_output()
        self.touch_newer_than_config(self.srlog, self.output)
        self.assertFailed(self.check(None), "未提供 SR 退出码")

    # ---- condition 2: SRLOG presence, freshness and last line ------------
    def test_missing_srlog_is_failed_even_with_exit_zero(self):
        # the silent-failure shape: exit(0) long before SRLOG creation
        self.assertFailed(self.check(0), "缺 SRLOG")

    def test_stale_srlog_from_an_earlier_run_is_rejected(self):
        self.write_srlog("x\nRun finished.")
        self.write_output()
        old = self.fx.config.stat().st_mtime - 3600
        os.utime(str(self.fx.srlog), (old, old))
        os.utime(str(self.fx.output), (old, old))
        self.assertFailed(self.check(0), "残留")

    def test_last_line_without_trailing_newline_is_accepted(self):
        # code_0817_prod.py:630 writes "\nRun finished." with NO trailing \n
        self.write_srlog("noise\n\nRun finished.")
        self.write_output()
        self.touch_newer_than_config(self.fx.srlog, self.fx.output)
        self.assertSatisfied(self.check(0))

    def test_last_line_behind_the_tail_window_is_found(self):
        # > 4KB of nvidia-smi output before the marker: readlines()[-1] territory
        self.write_srlog("noise\n" + ("0, GPU-abc, 1234 MiB\n" * 500) + "\nRun finished.")
        self.assertGreater(self.fx.srlog.stat().st_size, 4096)
        self.write_output()
        self.touch_newer_than_config(self.fx.srlog, self.fx.output)
        self.assertSatisfied(self.check(0))

    def test_trailing_blank_lines_after_the_marker_are_ignored(self):
        self.write_srlog("x\nRun finished.\n\n\n")
        self.write_output()
        self.touch_newer_than_config(self.fx.srlog, self.fx.output)
        self.assertSatisfied(self.check(0))

    def test_extra_text_after_the_marker_is_a_failure(self):
        # "Run finished." followed by more output: the marker is no longer the
        # last line, which is exactly what the tail read must notice.
        self.write_srlog("x\nRun finished.\nsome late traceback\n")
        self.write_output()
        self.touch_newer_than_config(self.fx.srlog, self.fx.output)
        self.assertFailed(self.check(0), "末行")

    def test_wrong_last_line_fails(self):
        self.write_srlog("x\nalready SRed before")
        self.write_output()
        self.touch_newer_than_config(self.fx.srlog, self.fx.output)
        self.assertFailed(self.check(0), "末行")

    # ---- policy skip (E9) -------------------------------------------------
    def test_cloud_skip_is_legal_without_an_output_tif(self):
        self.write_srlog("CloudPercent 95 > CloudLimit 80, SR not needed.\n"
                         "Run skipped: cloud limit exceeded\n")
        self.touch_newer_than_config(self.fx.srlog)
        v = self.check(0)
        self.assertSatisfied(v)
        self.assertTrue(v.skip)
        self.assertFalse(self.fx.output.exists())     # no artefact is correct

    def test_skip_with_nonzero_exit_still_fails(self):
        self.write_srlog("Run skipped: cloud limit exceeded\n")
        self.touch_newer_than_config(self.fx.srlog)
        self.assertFailed(self.check(1), "退出码")

    def test_skip_marker_only_counts_at_the_end(self):
        self.write_srlog("Run skipped: earlier\nmore work\n")
        self.touch_newer_than_config(self.fx.srlog)
        self.assertFailed(self.check(0), "末行")

    # ---- condition 3: the output tif -------------------------------------
    def test_missing_output_tif_fails(self):
        self.write_srlog("x\nRun finished.")
        self.touch_newer_than_config(self.fx.srlog)
        self.assertFailed(self.check(0), "缺输出 tif")

    def test_zero_byte_output_tif_fails(self):
        self.write_srlog("x\nRun finished.")
        self.write_output(size=0)
        self.touch_newer_than_config(self.fx.srlog, self.fx.output)
        self.assertFailed(self.check(0), "0 字节")

    def test_stale_output_tif_is_rejected(self):
        self.write_srlog("x\nRun finished.")
        self.touch_newer_than_config(self.fx.srlog)
        self.write_output()
        old = self.fx.config.stat().st_mtime - 3600
        os.utime(str(self.fx.output), (old, old))
        self.assertFailed(self.check(0), "残留")

    # ---- broke inputs: never raise, always conclude -----------------------
    def test_unreadable_meta_converges_to_failure(self):
        # No meta.xml → the SRLOG/output names cannot be derived at all, so the
        # verifier must still conclude (not raise) even with a clean exit 0.
        (self.fx.lq / (self.fx.scene + "_meta.xml")).unlink()
        self.assertFailed(self.check(0), "meta.xml")

    def test_meta_without_solar_azimuth_tag_fails(self):
        (self.fx.lq / (self.fx.scene + "_meta.xml")).write_text(
            "<meta><Other>1</Other></meta>", encoding="utf-8")
        self.assertFailed(self.check(0), "SolarAzimuth")

    def test_missing_config_fails_without_raising(self):
        v = verify.check_contract(str(self.fx.tmp / "nope.xml"), 0, job_id=1)
        self.assertFailed(v, "config.xml")

    def test_config_without_dataroot_lq_fails(self):
        self.fx.config.write_text(
            '<?xml version="1.0"?><SFSR_Config><Suffix>t1</Suffix></SFSR_Config>',
            encoding="utf-8")
        self.assertFailed(self.check(0), "DatarootLQ")

    def test_config_without_opt_fails(self):
        self.fx.config.write_text(
            '<?xml version="1.0"?><SFSR_Config><DatarootLQ>%s</DatarootLQ>'
            "<Suffix>t1</Suffix></SFSR_Config>" % (self.fx.lq,),
            encoding="utf-8")
        self.assertFailed(self.check(0), "OPT")

    def test_nonexistent_lq_path_fails(self):
        self.fx.config.write_text(
            CONFIG_TEMPLATE.format(lq="/no/such/scene", suffix="t1",
                                   opt=self.fx.opt), encoding="utf-8")
        self.assertFailed(self.check(0), "DatarootLQ 不是目录")

    def test_unreadable_opt_yml_falls_back_to_tif(self):
        # OPT exists in the config but its file does not: tiftype falls back to
        # .tif, so a normal ".tif" output still validates.
        self.fx.config.write_text(
            CONFIG_TEMPLATE.format(lq=self.fx.lq, suffix="t1",
                                   opt="/no/such/opt.yml"), encoding="utf-8")
        self.write_srlog("x\nRun finished.")
        self.write_output()
        self.touch_newer_than_config(self.fx.srlog, self.fx.output)
        self.assertSatisfied(self.check(0))

    def test_unwritable_debug_dir_does_not_raise(self):
        # Permission denied on the verdict-file write must not bubble out of
        # check_contract (writing is best-effort; the platform then reruns).
        v = self.check(0)
        real_open = io.open

        def denied(path, mode="r", *a, **kw):
            if ".txt" in str(path) and EXIT_MARK in str(path):
                raise IOError("permission denied")
            return real_open(path, mode, *a, **kw)

        with mock.patch("builtins.open", denied):
            written = verify.write_exit_code_file(v, 42, 0)
        self.assertIsNone(written)

    # ---- CLI / exit codes -------------------------------------------------
    def test_cli_exits_zero_on_satisfied(self):
        self.write_srlog("x\nRun finished.")
        self.write_output()
        self.touch_newer_than_config(self.fx.srlog, self.fx.output)
        proc = run_cli("--config", str(self.fx.config), "--sr-exit-code", "0",
                       "--job-id", "42")
        self.assertEqual(proc.returncode, verify.EXIT_CONTRACT_OK, proc.stderr)

    def test_cli_exits_90_and_names_the_missing_condition_on_stderr(self):
        proc = run_cli("--config", str(self.fx.config), "--sr-exit-code", "0",
                       "--job-id", "42")
        self.assertEqual(proc.returncode, verify.EXIT_CONTRACT_FAIL)
        self.assertIn("SRLOG", proc.stderr)

    def test_cli_job_id_defaults_to_slurm_job_id(self):
        self.write_srlog("x\nRun finished.")
        self.write_output()
        self.touch_newer_than_config(self.fx.srlog, self.fx.output)
        env = dict(os.environ, SLURM_JOB_ID="777")
        proc = run_cli("--config", str(self.fx.config), "--sr-exit-code", "0",
                       env=env)
        self.assertEqual(proc.returncode, 0)
        self.assertTrue((self.fx.lq / "Debug" / "_SREXIT_777.txt").is_file())


EXIT_MARK = "_SREXIT_"


class VerdictFileTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.fx = ContractFixture(self._tmp.name)

    def verdict_path(self, job_id=42):
        return self.fx.lq / "Debug" / ("_SREXIT_%s.txt" % job_id)

    def test_written_file_carries_the_platform_fields(self):
        self.fx.write_srlog("x\nRun finished.")
        self.fx.write_output()
        self.fx.touch_newer_than_config(self.fx.srlog, self.fx.output)
        v = self.fx.check(0, job_id=42)
        path = verify.write_exit_code_file(v, 42, 0)
        self.assertEqual(Path(path), self.verdict_path(42))
        fields = dict(line.split("=", 1)
                      for line in Path(path).read_text(encoding="utf-8").splitlines())
        self.assertEqual(fields["job_id"], "42")
        self.assertEqual(fields["verdict"], "0")
        self.assertEqual(fields["sr_exit_code"], "0")
        self.assertEqual(fields["skip"], "0")
        self.assertEqual(fields["reason"], "")

    def test_failure_verdict_is_written_too(self):
        v = self.fx.check(0, job_id=7)                 # no SRLOG at all
        path = verify.write_exit_code_file(v, 7, 0)
        text = Path(path).read_text(encoding="utf-8")
        self.assertIn("verdict=90", text)
        self.assertIn("SRLOG", text)

    def test_skip_is_recorded(self):
        self.fx.write_srlog("Run skipped: cloud limit exceeded\n")
        self.fx.touch_newer_than_config(self.fx.srlog)
        v = self.fx.check(0, job_id=8)
        path = verify.write_exit_code_file(v, 8, 0)
        self.assertIn("skip=1", Path(path).read_text(encoding="utf-8"))

    def test_no_job_id_means_no_file_and_no_raise(self):
        v = self.fx.check(0, job_id=None)
        self.assertIsNone(verify.write_exit_code_file(v, None, 0))

    def test_write_creates_the_debug_dir(self):
        (self.fx.lq / "Debug").rmdir()
        v = self.fx.check(0, job_id=9)
        path = verify.write_exit_code_file(v, 9, 0)
        self.assertTrue(Path(path).is_file())


#: Child program for the C-locale case: write a verdict whose `reason` is
#: non-ASCII, then report the encoding the platform default resolved to.
#: Raw string on purpose — the escapes below belong to the *child's* source.
_CHILD_WRITE_PROBE = r"""
import importlib.util, locale, sys

spec = importlib.util.spec_from_file_location("verify_sr_run", %(verifier)r)
v = importlib.util.module_from_spec(spec)
spec.loader.exec_module(v)

verdict = v.Verdict()
verdict.lq_path = sys.argv[1]
verdict.fail("缺少 SRLOG — the job never reached the log stage")
# check_contract() normally joins reasons into reason_text; set it here so the
# written body really carries non-ASCII (an empty reason would make this probe
# pass under any encoding, i.e. prove nothing).
verdict.reason_text = "; ".join(verdict.reasons)
path = v.write_exit_code_file(verdict, 4242, 0)
sys.stderr.write("preferred=%%s\n" %% locale.getpreferredencoding(False))
sys.stderr.write("path=%%s\n" %% (path,))
"""


class VerdictFileEncodingTests(unittest.TestCase):
    """The verdict file must not depend on either side's locale.

    The verifier runs *inside the job*: Slurm's --export=NONE has dropped LANG,
    and the SR interpreter is py3.6, whose `open()` default is then plain ASCII.
    `reason` carries Chinese text, so a locale-encoded write raises — and the
    `except` in write_exit_code_file turns that into "no file", which the
    platform reads as UNKNOWN for a job that actually failed the contract. That
    is the exact silent failure the verdict file exists to rule out, which is
    why the encoding is pinned in the source rather than left to the environment.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.fx = ContractFixture(self._tmp.name)

    def verdict_path(self, job_id):
        return self.fx.lq / "Debug" / ("_SREXIT_%s.txt" % job_id)

    def test_write_survives_an_ascii_default_locale(self):
        """Re-run the write under LC_ALL=C — the real job environment.

        POSIX-only: Windows has no locale that forces a non-UTF-8 default (its
        ANSI code page always decodes the ASCII field set), so the portable half
        of this lock is test_failure_verdict_is_written_too reading utf-8.
        """
        if os.name != "posix":
            self.skipTest("forcing a non-UTF-8 default encoding needs LC_ALL")
        child = Path(self._tmp.name) / "write_under_c_locale.py"
        child.write_text(_CHILD_WRITE_PROBE % {"verifier": str(VERIFIER_PATH)},
                         encoding="utf-8")
        env = dict(os.environ, LC_ALL="C", LANG="C",
                   PYTHONCOERCECLOCALE="0", PYTHONUTF8="0")
        proc = subprocess.run([sys.executable, str(child), str(self.fx.lq)],
                              capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=60, env=env)
        found = re.search(r"preferred=(\S+)", proc.stderr or "")
        self.assertIsNotNone(found, proc.stderr)
        if found.group(1).lower().replace("-", "") == "utf8":
            self.skipTest("interpreter kept UTF-8 (%s)" % found.group(1))
        path = self.verdict_path(4242)
        self.assertTrue(path.is_file(),
                        "no verdict file under %s\n%s" % (found.group(1),
                                                          proc.stderr))
        text = path.read_text(encoding="utf-8")     # utf-8 or the test fails
        self.assertIn("verdict=90", text)
        self.assertIn("缺少 SRLOG", text)

    def test_reader_accepts_a_legacy_locale_encoded_file(self):
        """A verdict written by an unpinned build (GBK) still resolves.

        On the API host the default encoding is UTF-8, where decoding those
        bytes raises — before `errors="replace"` that degraded to UNKNOWN, i.e.
        a failed job reported as "no verdict". Every field the platform acts on
        is ASCII, so only the free-text reason may lose bytes.
        """
        from backend.services import slurm as svc

        path = self.verdict_path(9)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(("job_id=9\nsr_exit_code=0\nverdict=90\nskip=0\n"
                          "reason=缺少 SRLOG\n").encode("gbk"))
        st = svc.terminal_from_exit_file(9, str(path))
        self.assertEqual(st["state"], "FAILED")
        self.assertEqual(st["exit_code"], "0:0")

    def test_reader_accepts_a_utf8_file_written_under_any_locale(self):
        """The counterpart: what the pinned verifier writes is what we read."""
        from backend.services import slurm as svc

        path = self.verdict_path(7)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes("job_id=7\nsr_exit_code=0\nverdict=0\nskip=1\n"
                         "reason=云量超限，跳过\n".encode("utf-8"))
        st = svc.terminal_from_exit_file(7, str(path))
        self.assertEqual(st["state"], "COMPLETED")
        self.assertEqual(st["exit_code"], "0:0")


class NamingContractTests(unittest.TestCase):
    """The path run_sr.py recomputes must equal the one the job writes."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def test_run_sr_agrees_with_the_verifier(self):
        from backend.services import run_sr as svc

        fx = ContractFixture(self._tmp.name)
        expected = verify.exit_code_file_for(str(fx.lq), 4242)
        actual = svc.exit_code_file_for(str(fx.config), 4242)
        self.assertIsNotNone(actual)
        self.assertEqual(
            Path(actual).name, Path(expected).name,
            "verdict file name drifted between the verifier and run_sr")
        self.assertEqual(Path(actual).name, svc.EXIT_FILE_FMT.format(job_id=4242))
        self.assertEqual(Path(actual).name,
                         verify.EXIT_FILE_FMT.format(job_id=4242))
        self.assertEqual(Path(actual).parent.name, "Debug")
        self.assertEqual(str(Path(actual).parent.parent), str(fx.lq))

    def test_run_sr_returns_none_when_undecidable(self):
        from backend.services import run_sr as svc

        self.assertIsNone(svc.exit_code_file_for("/no/such/cfg.xml", 1))
        fx = ContractFixture(self._tmp.name)
        self.assertIsNone(svc.exit_code_file_for(str(fx.config), None))

    def test_markers_shared_with_run_sr(self):
        from backend.services import run_sr as svc

        self.assertEqual(verify.RUN_FINISHED_MARKER, svc.RUN_FINISHED_MARKER)
        self.assertEqual(verify.RUN_SKIPPED_PREFIX, svc.RUN_SKIPPED_PREFIX)


if __name__ == "__main__":
    unittest.main()
