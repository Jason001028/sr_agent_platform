"""Tests for scene search (filename parsing, fake/disk backends, filters, tool)."""

import os
import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

from backend.services import scene_search as svc
from backend.tools.search_scenes import run_search_scenes


def write_tif(path, name):
    p = Path(path) / name
    Image.fromarray(np.zeros((4, 4), dtype=np.uint16)).save(p)
    return p


class TestParseFilename(unittest.TestCase):
    def test_full_naming(self):
        meta = svc.parse_filename(
            "JL1KF02B03_PMS05_20260722125045_200524168_102_0034_001_L1_PAN_mask.txt")
        self.assertEqual(meta["satellite"], "JL1KF02B03")
        self.assertEqual(meta["sensor"], "PMS05")
        self.assertEqual(meta["date"], "2026-07-22")

    def test_date_only(self):
        meta = svc.parse_filename("GF07A03_20260801.tif")
        self.assertEqual(meta["satellite"], "GF07A03")
        self.assertEqual(meta["sensor"], "20260801")   # not a real sensor, but kept
        self.assertEqual(meta["date"], "2026-08-01")

    def test_no_timestamp(self):
        meta = svc.parse_filename("scan_plain.tif")
        self.assertEqual(meta["date"], None)
        self.assertEqual(meta["satellite"], "scan")     # first `_`-token

    def test_invalid_timestamp_ignored(self):
        meta = svc.parse_filename("GF07A03_20261399_000000.tif")  # month 13
        self.assertEqual(meta["date"], None)


class TestFakeScenes(unittest.TestCase):
    def test_shape(self):
        rows = svc.fake_scenes(8)
        self.assertEqual(len(rows), 8)
        for r in rows:
            self.assertTrue(r["fake"])
            self.assertEqual(r["size_bytes"], 0)
            self.assertRegex(r["date"], r"\d{4}-\d{2}-\d{2}")

    def test_deterministic(self):
        self.assertEqual(svc.fake_scenes(5), svc.fake_scenes(5))

    def test_ids_unique(self):
        rows = svc.fake_scenes(12)
        ids = [r["id"] for r in rows]
        self.assertEqual(len(ids), len(set(ids)))


class TestSearch(unittest.TestCase):
    def test_no_root_falls_back_to_fake(self):
        r = svc.search_scenes(None)
        self.assertEqual(r["source"], "fake")
        self.assertTrue(r["count"] > 0)

    def test_satellite_filter(self):
        r = svc.search_scenes(None, satellite="GF07A03")
        self.assertEqual(r["source"], "fake")
        self.assertTrue(r["count"] > 0)
        for s in r["results"]:
            self.assertIn("gf07a03", s["satellite"].lower())

    def test_query_filter(self):
        r = svc.search_scenes(None, query="KF02B04")
        self.assertTrue(r["count"] > 0)
        for s in r["results"]:
            self.assertIn("kf02b04", s["id"].lower())

    def test_sensor_filter(self):
        r = svc.search_scenes(None, sensor="PMS05")
        self.assertTrue(r["count"] > 0)
        for s in r["results"]:
            self.assertIn("pms05", s["sensor"].lower())

    def test_sensor_filter_disk(self):
        with tempfile.TemporaryDirectory() as d:
            write_tif(d, "GF07A03_PMS01_20260722125045.tif")
            write_tif(d, "KF02B04_PMS05_20260810120000.tif")
            r = svc.search_scenes(d, sensor="PMS05")
            self.assertEqual(r["source"], "disk")
            self.assertEqual(len(r["results"]), 1)
            self.assertIn("KF02B04", r["results"][0]["id"])

    def test_date_range_filter(self):
        r = svc.search_scenes(None, date_from="2026-07-22", date_to="2026-07-24")
        self.assertTrue(r["count"] > 0)
        for s in r["results"]:
            self.assertGreaterEqual(s["date"], "2026-07-22")
            self.assertLessEqual(s["date"], "2026-07-24")

    def test_date_range_disjoint_returns_none(self):
        # a range disjoint from the fake pool proves filtering actually applies
        r = svc.search_scenes(None, date_from="2026-09-01", date_to="2026-09-30")
        self.assertEqual(r["count"], 0)

    def test_limit(self):
        r = svc.search_scenes(None, limit=3)
        self.assertLessEqual(len(r["results"]), 3)

    def test_scan_disk_backend(self):
        with tempfile.TemporaryDirectory() as d:
            write_tif(d, "GF07A03_PMS01_20260722125045.tif")
            write_tif(d, "KF02B04_PMS05_20260810120000.tif")
            r = svc.search_scenes(d)
            self.assertEqual(r["source"], "disk")
            self.assertEqual(r["scanned"], 2)
            ids = {s["id"] for s in r["results"]}
            self.assertEqual(len(ids), 2)
            self.assertFalse(any(s["fake"] for s in r["results"]))

    def test_disk_with_query(self):
        with tempfile.TemporaryDirectory() as d:
            write_tif(d, "GF07A03_PMS01_20260722125045.tif")
            write_tif(d, "KF02B04_PMS05_20260810120000.tif")
            r = svc.search_scenes(d, query="KF02B04")
            self.assertEqual(r["source"], "disk")
            self.assertEqual(len(r["results"]), 1)


class TestTool(unittest.TestCase):
    def test_ok_no_args(self):
        r = run_search_scenes()
        self.assertTrue(r["ok"])
        self.assertEqual(r["data"]["source"], "fake")
        self.assertTrue(r["data"]["count"] > 0)

    def test_ok_with_filters(self):
        r = run_search_scenes(query="GF07", limit=5)
        self.assertTrue(r["ok"])
        self.assertLessEqual(len(r["data"]["results"]), 5)

    def test_bad_limit(self):
        r = run_search_scenes(limit=0)
        self.assertFalse(r["ok"])
        self.assertIn("limit", r["error"])

    def test_bad_date(self):
        r = run_search_scenes(date_from="2026/08/01")
        self.assertFalse(r["ok"])
        self.assertIn("date_from", r["error"])

    def test_env_root_respected(self):
        with tempfile.TemporaryDirectory() as d:
            write_tif(d, "GF07A03_PMS01_20260722125045.tif")
            os.environ["SR_SCENES_ROOT"] = d
            try:
                r = run_search_scenes()
            finally:
                del os.environ["SR_SCENES_ROOT"]
            self.assertTrue(r["ok"])
            self.assertEqual(r["data"]["source"], "disk")
            self.assertEqual(r["data"]["count"], 1)


if __name__ == "__main__":
    unittest.main()
