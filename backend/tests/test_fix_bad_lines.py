"""Tests for bad-line repair (detection + replacement + tool wrapper).

Uses a synthetic smooth ramp (y + x) — clean by construction, so injected
dead/stuck lines are unambiguous outliers.
"""

import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

from backend.services import fix_bad_lines as svc
from backend.tools.fix_bad_lines import run_fix_bad_lines


def gradient(n=64):
    """Smooth uint16 ramp 0..2(n-1); every line is a valid signal line."""
    y, x = np.mgrid[0:n, 0:n]
    return (y + x).astype(np.uint16)


class TestDetect(unittest.TestCase):
    def test_clean_image_flags_nothing(self):
        self.assertEqual(svc.detect_bad_lines(gradient()).size, 0)

    def test_detect_bad_rows(self):
        arr = gradient()
        arr[10] = 0          # dead row
        arr[40] = 60000      # stuck-bright row
        bad = svc.detect_bad_lines(arr, axis=0)
        self.assertEqual(sorted(int(i) for i in bad), [10, 40])

    def test_detect_bad_columns(self):
        arr = gradient()
        arr[:, 15] = 0
        bad = svc.detect_bad_lines(arr, axis=1)
        self.assertEqual([int(i) for i in bad], [15])

    def test_high_threshold_flags_nothing(self):
        arr = gradient()
        arr[10] = 0
        self.assertEqual(
            svc.detect_bad_lines(arr, axis=0, threshold=1000).size, 0)


class TestFix(unittest.TestCase):
    def test_fix_bad_rows_recovers_gradient(self):
        orig = gradient()
        arr = orig.copy()
        arr[10] = 0
        arr[40] = 60000
        fixed, bad = svc.fix_bad_lines(arr, axis=0)
        self.assertEqual(sorted(int(i) for i in bad), [10, 40])
        # repaired lines land within a couple of DN of the original ramp
        self.assertTrue(np.allclose(fixed[10], orig[10], atol=2))
        self.assertTrue(np.allclose(fixed[40], orig[40], atol=2))

    def test_fix_preserves_good_lines(self):
        arr = gradient()
        arr[10] = 0
        fixed, _ = svc.fix_bad_lines(arr, axis=0)
        np.testing.assert_array_equal(fixed[5], arr[5])
        np.testing.assert_array_equal(fixed[20], arr[20])

    def test_clean_input_unchanged_copy(self):
        arr = gradient()
        fixed, bad = svc.fix_bad_lines(arr)
        self.assertEqual(bad.size, 0)
        np.testing.assert_array_equal(fixed, arr)
        fixed[0, 0] = 999    # must not mutate the source
        self.assertNotEqual(arr[0, 0], 999)

    def test_fix_bad_columns(self):
        orig = gradient()
        arr = orig.copy()
        arr[:, 15] = 0
        fixed, bad = svc.fix_bad_lines(arr, axis=1)
        self.assertEqual([int(i) for i in bad], [15])
        self.assertTrue(np.allclose(fixed[:, 15], orig[:, 15], atol=2))


class TestTool(unittest.TestCase):
    def _write(self, d, arr):
        p = Path(d) / "in.tif"
        Image.fromarray(arr).save(p)
        return p

    def test_tool_ok_with_output(self):
        arr = gradient()
        arr[10] = 0
        arr[40] = 60000
        with tempfile.TemporaryDirectory() as d:
            src = self._write(d, arr)
            out = Path(d) / "out.tif"
            res = run_fix_bad_lines(input_path=str(src), output_path=str(out))
            self.assertTrue(res["ok"])
            self.assertEqual(res["data"]["count"], 2)
            self.assertTrue(out.exists())
            back = np.asarray(Image.open(out))
            np.testing.assert_array_equal(back, svc.fix_bad_lines(arr)[0])

    def test_tool_ok_no_output(self):
        with tempfile.TemporaryDirectory() as d:
            src = self._write(d, gradient())
            res = run_fix_bad_lines(input_path=str(src))
            self.assertTrue(res["ok"])
            self.assertEqual(res["data"]["count"], 0)
            self.assertNotIn("output_path", res["data"])

    def test_tool_missing_file_returns_err(self):
        res = run_fix_bad_lines(input_path="nope.tif")
        self.assertFalse(res["ok"])
        self.assertIn("not found", res["error"])

    def test_tool_bad_axis_returns_err(self):
        with tempfile.TemporaryDirectory() as d:
            src = self._write(d, gradient())
            res = run_fix_bad_lines(input_path=str(src), axis="diagonal")
            self.assertFalse(res["ok"])
            self.assertIn("axis", res["error"])


if __name__ == "__main__":
    unittest.main()
