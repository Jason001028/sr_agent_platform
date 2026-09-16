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


def write_jpg(path, name, w=8, h=6):
    p = Path(path) / name
    Image.fromarray(np.zeros((h, w), dtype=np.uint8)).save(p, format="JPEG")
    return p


_META_XML = ('<?xml version="1.0" encoding="UTF-8"?>'
             "<SolarAzimuth>181.79</SolarAzimuth>")


def write_scene(root, name, ext=".tif"):
    """造一个真机形态的场景目录，返回场景文件的路径。

    真机的场景判据是「目录里有 <目录名>_meta.xml」（SR 脚本靠它判 RC/SC，
    scene_search.is_scene_dir），所以夹具必须照此造：平铺的裸文件现在会被
    is_scene_file 整批挡掉。返回值是场景文件，派生件往 .parent 里塞。
    """
    d = Path(root) / name
    d.mkdir(parents=True, exist_ok=True)
    if ext.lower() in (".jpg", ".jpeg"):
        p = write_jpg(d, name + ext)
    else:
        p = write_tif(d, name + ext)
    (d / (name + "_meta.xml")).write_text(_META_XML, encoding="utf-8")
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


class TestImageScenes(unittest.TestCase):
    """§4.7：盘阵目录里的 .jpg/.jpeg 也算场景；派生件不算（白名单）。

    判据是「所在目录含 <目录名>_meta.xml」+「文件名叫 <目录名>.<ext> 或 PAN.<ext>」，
    见 scene_search.is_scene_file。
    """

    def test_jpg_and_jpeg_are_scenes(self):
        with tempfile.TemporaryDirectory() as d:
            write_scene(d, "GF07A03_PMS01_20260722125045", ".jpg")
            write_scene(d, "KF02B04_PMS05_20260810120000", ".JPEG")
            write_scene(d, "ZY302_MUX_20260805120000", ".tif")
            r = svc.search_scenes(d)
            self.assertEqual(r["scanned"], 3)
            exts = sorted(Path(s["path"]).suffix.lower() for s in r["results"])
            self.assertEqual(exts, [".jpeg", ".jpg", ".tif"])

    def test_jpg_metadata_and_filters_apply(self):
        with tempfile.TemporaryDirectory() as d:
            write_scene(d, "GF07A03_PMS01_20260722125045", ".jpg")
            write_scene(d, "KF02B04_PMS05_20260810120000", ".jpg")
            r = svc.search_scenes(d, satellite="KF02B04")
            self.assertEqual(r["scanned"], 2)
            self.assertEqual(len(r["results"]), 1)
            self.assertEqual(r["results"][0]["satellite"], "KF02B04")
            self.assertEqual(r["results"][0]["date"], "2026-08-10")

    def test_preview_cache_is_not_a_scene(self):
        """后端自己烘焙的 <stem>.preview.jpg 躺在场景目录里，不能被列成第二行。"""
        with tempfile.TemporaryDirectory() as d:
            p = write_scene(d, "GF07A03_PMS01_20260722125045")
            write_jpg(p.parent, p.stem + ".preview.jpg")
            r = svc.search_scenes(d)
            self.assertEqual(r["scanned"], 1)
            self.assertEqual(r["results"][0]["id"],
                             "GF07A03_PMS01_20260722125045")

    def test_mask_is_not_a_scene(self):
        """§4.3：<名字>_mask.tif 是「提交 SR」的输入，不该和它的场景并列成第二行。

        真机形态：场景与掩膜同名同目录（`<目录名>.tif` + `<目录名>_mask.tif`），所以
        排除一旦失效，页面上每个场景都会多出一行 — 卫星/传感器由掩膜文件名解析得到
        （satellite=<父目录名>、sensor='mask'），尺寸取掩膜 TIFF 头。
        """
        with tempfile.TemporaryDirectory() as d:
            p = write_scene(d, "GF07A03_PMS01_20260722125045")
            write_tif(p.parent, p.stem + "_mask.tif")
            q = write_scene(d, "KF02B04_PMS05_20260810120000", ".jpg")
            write_jpg(q.parent, q.stem + "_mask.jpg")
            r = svc.search_scenes(d)
            self.assertEqual(r["scanned"], 2)
            self.assertEqual(sorted(s["id"] for s in r["results"]),
                             ["GF07A03_PMS01_20260722125045",
                              "KF02B04_PMS05_20260810120000"])

    def test_derived_products_are_not_scenes(self):
        """一瓶装下真机看到的所有脏数据（2026-09-15：18 行里 16 行是这种）。

        SR 产物与输入备份（_sr/_NOSR/_ori）、云量图（_cloud）、缩略图（_thumb）
        都躺在同一个场景目录里 —— 白名单下它们连判据都不用各写一条。
        """
        with tempfile.TemporaryDirectory() as d:
            p = write_scene(d, "GF07A03_PMS01_20260722125045")
            for tail in ("_sr.tif", "_NOSR.tif", "_ori.tif"):
                write_tif(p.parent, p.stem + tail)
            for tail in ("_cloud.jpg", "_thumb.jpg", "_cloud.preview.jpg"):
                write_jpg(p.parent, p.stem + tail)
            r = svc.search_scenes(d)
            self.assertEqual(r["scanned"], 1)
            self.assertEqual(r["results"][0]["id"], p.stem)

    def test_debug_dir_is_not_a_scene(self):
        """场景目录下的 Debug/ 里是十几张调试图（图/中间件），一张都不该列。"""
        with tempfile.TemporaryDirectory() as d:
            p = write_scene(d, "GF07A03_PMS01_20260722125045")
            dbg = p.parent / "Debug"
            dbg.mkdir()
            for name in ("TiaoJiaoImage1.jpg", "TC_B1_inner.jpg",
                         "RC_B1_test1.tif", "B1_outerCMOS.jpg",
                         "seamTif_0_0.jpg"):
                write_jpg(dbg, name)
            r = svc.search_scenes(d)
            self.assertEqual(r["scanned"], 1)
            self.assertEqual(r["results"][0]["id"], p.stem)

    def test_rc_input_pan_is_a_scene(self):
        """RC 步骤的输入是 PAN.tif（util.get_l1_pan_tif_rcsc 的 RC 分支）。"""
        with tempfile.TemporaryDirectory() as d:
            p = write_scene(d, "GF07A03_PMS01_20260722125045")
            pan = write_tif(p.parent, "PAN.tif")
            self.assertTrue(svc.is_scene_file(pan))
            r = svc.search_scenes(d, query="pan")
            self.assertEqual(len(r["results"]), 1)
            self.assertEqual(r["results"][0]["id"], "PAN")

    def test_dir_without_meta_is_not_a_scene(self):
        """没有 <目录名>_meta.xml 的目录整个不显示（SR 提交也跑不起来）。"""
        with tempfile.TemporaryDirectory() as d:
            write_tif(d, "GF07A03_PMS01_20260722125045.tif")   # 平铺，无场景目录
            scene = write_scene(d, "KF02B04_PMS05_20260810120000")
            (scene.parent / "KF02B04_PMS05_20260810120000_meta.xml").unlink()
            r = svc.search_scenes(d)
            self.assertEqual(r["scanned"], 0)

    def test_scene_name_must_match_dirname(self):
        """判据是「名字等于目录名」，不是前缀/子串匹配 —— <目录名>_mask.tif 被挡。"""
        with tempfile.TemporaryDirectory() as d:
            p = write_scene(d, "GF07A03_PMS01_20260722125045")
            self.assertTrue(svc.is_scene_file(p))
            self.assertFalse(svc.is_scene_file(
                p.parent / "GF07A03_PMS01_20260722125045_L1_PAN.tif"))

    def test_is_scene_file_predicate(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertTrue(svc.is_scene_file(
                write_scene(d, "a_sat_sen_20260722125045")))
            self.assertTrue(svc.is_scene_file(
                write_scene(d, "b_sat_sen_20260722125045", ".tiff")))
            self.assertTrue(svc.is_scene_file(
                write_scene(d, "c_sat_sen_20260722125045", ".jpg")))
            self.assertFalse(svc.is_scene_file(write_jpg(d, "d.png")))
            self.assertFalse(svc.is_scene_file(Path(d) / "missing.jpg"))
            # 平铺在 root 下的裸文件：父目录是 root（无 <root名>_meta.xml）
            self.assertFalse(svc.is_scene_file(
                write_tif(d, "e_sat_sen_20260722125045.tif")))
            sub = Path(d) / "dir.jpg"          # 目录后缀像影像也不算
            sub.mkdir()
            self.assertFalse(svc.is_scene_file(sub))


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
            write_scene(d, "GF07A03_PMS01_20260722125045")
            write_scene(d, "KF02B04_PMS05_20260810120000")
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
            write_scene(d, "GF07A03_PMS01_20260722125045")
            write_scene(d, "KF02B04_PMS05_20260810120000")
            r = svc.search_scenes(d)
            self.assertEqual(r["source"], "disk")
            self.assertEqual(r["scanned"], 2)
            ids = {s["id"] for s in r["results"]}
            self.assertEqual(len(ids), 2)
            self.assertFalse(any(s["fake"] for s in r["results"]))

    def test_disk_with_query(self):
        with tempfile.TemporaryDirectory() as d:
            write_scene(d, "GF07A03_PMS01_20260722125045")
            write_scene(d, "KF02B04_PMS05_20260810120000")
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
            write_scene(d, "GF07A03_PMS01_20260722125045")
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
