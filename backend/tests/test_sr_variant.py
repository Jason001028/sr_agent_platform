"""Tests for the Slurm deployment-variant generator.

The generator lives outside the backend package (SR_code/ is not a package),
so it is loaded by path via importlib rather than by an import statement.

What is pinned here:
  * every anchor in the edit table hits the real source exactly once;
  * the checked-in variant does not reintroduce the four Slurm blockers
    (hard-coded CUDA_VISIBLE_DEVICES, `systemctl stop slurmd`, `gpu_count != 4`)
    and keeps the contract markers ("Run finished.", exit(3));
  * generation is deterministic and --check is a byte comparison;
  * a bad anchor is a hard failure (exit 2), never a silent skip.
"""

import importlib.util
import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
GENERATOR_PATH = REPO_ROOT / "SR_code" / "tools" / "gen_slurm_variant.py"
SOURCE_PATH = REPO_ROOT / "SR_code" / "code_0817_prod.py"
VARIANTS_DIR = REPO_ROOT / "SR_code" / "variants"
VARIANT_PATH = VARIANTS_DIR / "code_0817_prod_slurm.py"
PROVENANCE_PATH = VARIANTS_DIR / "code_0817_prod_slurm.provenance.json"
GITATTRIBUTES_PATH = VARIANTS_DIR / ".gitattributes"


def load_generator():
    spec = importlib.util.spec_from_file_location("gen_slurm_variant", GENERATOR_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


gen = load_generator()


def run_generator(*args):
    """Run the generator CLI; returns CompletedProcess with text output."""
    return subprocess.run(
        [sys.executable, str(GENERATOR_PATH)] + list(args),
        cwd=str(REPO_ROOT), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=120)


def code_only(text):
    """Drop whole-line comments so 'mentioning' cannot be confused with 'doing'."""
    return "\n".join(line for line in text.splitlines()
                     if not line.lstrip().startswith("#"))


class EditTableTests(unittest.TestCase):
    def setUp(self):
        self.source = gen.read_source(str(SOURCE_PATH))

    def test_every_anchor_hits_exactly_once(self):
        for edit_id, anchor, replacement, expected in gen.EDITS:
            with self.subTest(edit=edit_id):
                self.assertEqual(
                    self.source.count(anchor), expected,
                    "%s: anchor should appear %d time(s) in %s" %
                    (edit_id, expected, gen.SOURCE_REL))
                self.assertNotEqual(replacement, anchor)

    def test_edit_ids_are_e1_through_e9_in_order(self):
        self.assertEqual([e[0] for e in gen.EDITS],
                         ["E%d" % n for n in range(1, 10)])

    def test_check_anchors_reports_mismatch(self):
        bad = list(gen.EDITS) + [("E99", "no such line in the source\n", "x", 1)]
        problems = gen.check_anchors(self.source, bad)
        self.assertEqual([p[0] for p in problems], ["E99"])

    def test_normalization_makes_crlf_irrelevant(self):
        crlf = self.source.replace("\n", "\r\n")
        self.assertEqual(gen.normalize(crlf), self.source)
        self.assertEqual(gen.build_artifacts(gen.normalize(crlf)),
                         gen.build_artifacts(self.source))


class VariantContentTests(unittest.TestCase):
    def setUp(self):
        self.variant = VARIANT_PATH.read_text(encoding="utf-8")
        self.code = code_only(self.variant)

    def test_no_cuda_visible_devices_assignment(self):
        # Reading the variable (gpuid) is fine; assigning it would defeat --gres.
        self.assertNotIn("os.environ['CUDA_VISIBLE_DEVICES'] =", self.variant)
        self.assertNotIn('os.environ["CUDA_VISIBLE_DEVICES"] =', self.variant)
        self.assertIsNone(re.search(r"CUDA_VISIBLE_DEVICES[^\n]*=", self.code),
                          "variant still assigns CUDA_VISIBLE_DEVICES")

    def test_no_slurmd_stop(self):
        self.assertEqual(self.variant.count("systemctl stop slurmd"), 0)
        self.assertEqual(self.code.count("systemctl"), 0)

    def test_no_gpu_count_not_equal_4(self):
        self.assertEqual(self.variant.count("gpu_count != 4"), 0)
        self.assertIn("gpu_count < 1", self.variant)

    def test_contract_markers_survive(self):
        self.assertIn("Run finished.", self.variant)
        self.assertIn("exit(3)", self.variant)

    def test_cloud_skip_writes_an_explicit_terminal_line(self):
        # E9: a policy skip must be distinguishable from a silent failure.
        self.assertIn("Run skipped:", self.variant)
        self.assertIn("exit(0)", self.variant)

    def test_module_level_library_load_is_guarded(self):
        self.assertIn("except Exception:\n    lib = None", self.variant)

    def test_variant_uses_lf_only(self):
        self.assertNotIn(b"\r", VARIANT_PATH.read_bytes())

    def test_banner_quotes_the_real_source_hash(self):
        source_sha = gen.sha256_text(gen.read_source(str(SOURCE_PATH)))
        self.assertIn(source_sha, self.variant)
        self.assertIn("GENERATED FILE — DO NOT EDIT", self.variant)

    def test_source_file_is_untouched(self):
        # The variant must never be produced by editing the original in place.
        self.assertIn("lib = npct.load_library(", gen.read_source(str(SOURCE_PATH)))
        self.assertNotIn("GENERATED FILE", gen.read_source(str(SOURCE_PATH)))


class ArtifactTests(unittest.TestCase):
    def test_gitattributes_pins_lf(self):
        self.assertEqual(GITATTRIBUTES_PATH.read_text(encoding="utf-8"),
                         "* text eol=lf\n")

    def test_provenance_has_no_timestamp_and_lists_every_edit(self):
        data = json.loads(PROVENANCE_PATH.read_text(encoding="utf-8"))
        # An exact key set is the test: a timestamp field could not sneak in.
        self.assertEqual(sorted(data),
                         ["diff", "edits", "generator", "generator_version",
                          "source", "variant"])
        self.assertEqual([e["id"] for e in data["edits"]],
                         ["E%d" % n for n in range(1, 10)])
        for entry in data["edits"]:
            self.assertEqual(entry["occurrences"], 1)

    def test_provenance_hashes_match_the_files_on_disk(self):
        data = json.loads(PROVENANCE_PATH.read_text(encoding="utf-8"))
        for key in ("variant", "diff"):
            rel = data[key]["path"].split("/")[-1]
            text = (VARIANTS_DIR / rel).read_text(encoding="utf-8")
            self.assertEqual(gen.sha256_text(text), data[key]["sha256"], key)
        self.assertEqual(
            gen.sha256_text(gen.read_source(str(SOURCE_PATH))),
            data["source"]["sha256"])

    def test_diff_is_a_unified_diff_of_source_and_variant(self):
        diff = (VARIANTS_DIR / "code_0817_prod_slurm.diff").read_text(encoding="utf-8")
        lines = diff.splitlines()
        self.assertTrue(lines[0].startswith("--- " + gen.SOURCE_REL), lines[0])
        self.assertTrue(lines[1].startswith("+++ "), lines[1])
        self.assertTrue(any(l.startswith("@@") for l in lines))
        self.assertIn("systemctl stop slurmd", diff)  # the removal is visible
        self.assertNotIn("\r", diff)


class DeterminismTests(unittest.TestCase):
    def test_build_is_pure(self):
        source = gen.read_source(str(SOURCE_PATH))
        self.assertEqual(gen.build_artifacts(source), gen.build_artifacts(source))

    def test_check_passes_on_repo_artifacts(self):
        result = run_generator("--check")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_two_cli_runs_produce_identical_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            self.assertEqual(run_generator("--out-dir", str(out)).returncode, 0)
            first = {p.name: p.read_bytes() for p in sorted(out.iterdir())}
            self.assertTrue(first, "generator wrote no artifacts")
            for path in out.iterdir():
                path.unlink()
            self.assertEqual(run_generator("--out-dir", str(out)).returncode, 0)
            second = {p.name: p.read_bytes() for p in sorted(out.iterdir())}
            self.assertEqual(first, second)

    def test_check_detects_a_hand_edit(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            self.assertEqual(run_generator("--out-dir", str(out)).returncode, 0)
            target = out / "code_0817_prod_slurm.py"
            target.write_text(target.read_text(encoding="utf-8") + "# hand edit\n",
                              encoding="utf-8")
            result = run_generator("--check", "--out-dir", str(out))
            self.assertEqual(result.returncode, 1)
            self.assertIn("STALE", result.stderr)


class BadAnchorTests(unittest.TestCase):
    """A missing anchor must stop the run, not produce a half-built variant."""

    def test_missing_anchor_exits_2_without_writing(self):
        with tempfile.TemporaryDirectory() as tmp:
            broken = Path(tmp) / "code_0817_prod.py"
            broken.write_text(
                gen.read_source(str(SOURCE_PATH)).replace("    gpuid = '0'\n", ""),
                encoding="utf-8")
            out = Path(tmp) / "out"
            result = run_generator("--source", str(broken), "--out-dir", str(out))
            self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
            self.assertIn("anchor mismatch", result.stderr)
            self.assertIn("E3", result.stderr)
            self.assertFalse(out.exists(), "nothing may be written on a bad anchor")

    def test_duplicate_anchor_exits_2(self):
        with tempfile.TemporaryDirectory() as tmp:
            broken = Path(tmp) / "code_0817_prod.py"
            text = gen.read_source(str(SOURCE_PATH))
            # Duplicating the E6 anchor makes its hit count 2, not 1.
            broken.write_text(
                text + "\n    if gpu_available is False or gpu_count != 4:\n",
                encoding="utf-8")
            result = run_generator("--source", str(broken),
                                   "--out-dir", str(Path(tmp) / "out"))
            self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
            self.assertIn("E6", result.stderr)
            self.assertIn("expected 1 occurrence(s), found 2", result.stderr)


if __name__ == "__main__":
    unittest.main()
