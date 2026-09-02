"""Tests for preview_jpg service (ENVI dims, TIFF probe, sparse sampling,
stretch, Pillow fallback, cache idempotency)."""

import os
import tempfile
import unittest
from pathlib import Path

import numpy as np
import tifffile

from backend.services.preview_jpg import (PreviewError, build_preview_pixels,
                                          ensure_preview_jpg, probe_tiff,
                                          scene_dims)


def make_strip_tif(dirp, name, w, h, bits=16, dtype=None, **kw):
    """Uncompressed single-band strip TIFF with a determinable ramp."""
    p = Path(dirp) / name
    if dtype is None:
        dtype = np.uint16 if bits == 16 else np.uint8
    arr = (np.arange(w * h).reshape(h, w) % 4096).astype(dtype)
    if "photometric" not in kw:
        kw["photometric"] = "minisblack"
    tifffile.imwrite(p, arr, **kw)
    return p, arr


class TestSceneDims(unittest.TestCase):
    def test_envi_hdr_first(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "GF07A03_20260801.tif"
            p.write_bytes(b"")          # tif 可空：hdr 优先，不探测
            Path(d, "GF07A03_20260801.hdr").write_text(
                "samples = 3200\nlines = 2000\nbands = 1\n")
            self.assertEqual(scene_dims(str(p)),
                             {"W": 3200, "H": 2000})

    def test_no_hdr_probes_tiff(self):
        with tempfile.TemporaryDirectory() as d:
            p, _ = make_strip_tif(d, "GF07A03_PMS01_20260722.tif", 320, 640)
            self.assertEqual(scene_dims(str(p)), {"W": 320, "H": 640})

    def test_unreadable_returns_none(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "x.tif"
            p.write_bytes(b"not a tiff")
            self.assertIsNone(scene_dims(str(p)))

    def test_non_tif_without_hdr(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "x.img"
            p.write_bytes(b"whatever")
            self.assertIsNone(scene_dims(str(p)))


class TestProbe(unittest.TestCase):
    def test_probe_strip_layout(self):
        with tempfile.TemporaryDirectory() as d:
            p, _ = make_strip_tif(d, "s.tif", 800, 600)
            info = probe_tiff(str(p))
            self.assertEqual(info["W"], 800)
            self.assertEqual(info["H"], 600)
            self.assertEqual(info["bits"], 16)
            self.assertEqual(info["compression"], 1)
            self.assertFalse(info["tiled"])

    def test_probe_missing_file_raises(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(PreviewError):
                probe_tiff(str(Path(d) / "nope.tif"))


class TestBuildPreview(unittest.TestCase):
    def test_small_no_downscale(self):
        with tempfile.TemporaryDirectory() as d:
            p, _ = make_strip_tif(d, "s.tif", 320, 640)
            px = build_preview_pixels(str(p))
            self.assertEqual(px.dtype, np.uint8)
            self.assertEqual(px.shape, (640, 320))  # h, w

    def test_long_edge_capped_at_8192(self):
        # 12000 wide → pw = 8192, ph scaled; 采样只读几行，fixture 很小
        with tempfile.TemporaryDirectory() as d:
            p, _ = make_strip_tif(d, "big.tif", 12000, 60)
            px = build_preview_pixels(str(p))
            h, w = px.shape
            self.assertEqual(w, 8192)
            self.assertEqual(h, 41)   # round(60 * 8192/12000) = round(40.96)
            self.assertLessEqual(max(w, h), 8192)

    def test_const_all_zero_maps_black(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "c.tif"
            tifffile.imwrite(p, np.zeros((50, 50), dtype=np.uint16))
            px = build_preview_pixels(str(p))
            self.assertEqual(int(px.min()), 0)
            self.assertEqual(int(px.max()), 0)

    def test_const_mid_maps_gray(self):
        # 恒定值无 p2/p98 → 中间灰（128），避免整图溢出为纯黑/白
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "c.tif"
            tifffile.imwrite(p, np.full((50, 50), 5000, dtype=np.uint16))
            px = build_preview_pixels(str(p))
            self.assertEqual(int(px.min()), 128)
            self.assertEqual(int(px.max()), 128)

    def test_single_strip_scalar_tags(self):
        # 小图单条带：StripOffsets/StripByteCounts 以 count==1 标量存
        with tempfile.TemporaryDirectory() as d:
            p, _ = make_strip_tif(d, "tiny.tif", 16, 16)
            px = build_preview_pixels(str(p))
            self.assertEqual(px.shape, (16, 16))

    def test_compressed_small_falls_back_to_pillow(self):
        with tempfile.TemporaryDirectory() as d:
            p, _ = make_strip_tif(d, "z.tif", 64, 64,
                                  compression="deflate")
            px = build_preview_pixels(str(p))   # 小文件走 Pillow 兜底
            self.assertEqual(px.dtype, np.uint8)
            self.assertEqual(px.shape, (64, 64))

    def test_tiled_layout_rejected(self):
        # tile=(16,16) → sample_strips 报 tiled → Pillow 兜底仍成（小文件）
        with tempfile.TemporaryDirectory() as d:
            p, _ = make_strip_tif(d, "t.tif", 128, 128,
                                  tile=(32, 32), compression=None)
            px = build_preview_pixels(str(p))
            self.assertEqual(px.shape, (128, 128))


class TestEnsurePreviewJpg(unittest.TestCase):
    def test_generate_then_cache_idempotent(self):
        with tempfile.TemporaryDirectory() as d:
            p, _ = make_strip_tif(d, "GF07A03_PMS01_20260722.tif", 320, 640)
            jpg = Path(d, "GF07A03_PMS01_20260722.preview.jpg")
            r1 = ensure_preview_jpg(str(p), str(jpg))
            self.assertEqual(r1["status"], "generated")
            self.assertEqual((r1["w"], r1["h"]), (320, 640))
            mtime1 = jpg.stat().st_mtime

            os.utime(jpg, (mtime1, mtime1))  # 归一化，便于比较
            r2 = ensure_preview_jpg(str(p), str(jpg))
            self.assertEqual(r2["status"], "cached")
            self.assertEqual(jpg.stat().st_mtime, mtime1)

            with open(jpg, "rb") as f:
                self.assertTrue(f.read(2).startswith(b"\xff\xd8"))  # JPEG SOI

    def test_stale_source_regenerates(self):
        with tempfile.TemporaryDirectory() as d:
            p, _ = make_strip_tif(d, "GF07A03_PMS01_20260722.tif", 64, 64)
            jpg = Path(d, "GF07A03_PMS01_20260722.preview.jpg")
            ensure_preview_jpg(str(p), str(jpg))
            old = jpg.stat().st_mtime
            # 源更新（mtime 推后）→ 缓存视为过期
            old_mtime = p.stat().st_mtime
            os.utime(p, (old_mtime + 5, old_mtime + 5))
            r = ensure_preview_jpg(str(p), str(jpg))
            self.assertEqual(r["status"], "generated")
            self.assertNotEqual(jpg.stat().st_mtime, old)

    def test_missing_source_raises(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(PreviewError):
                ensure_preview_jpg(str(Path(d) / "nope.tif"),
                                   str(Path(d) / "nope.preview.jpg"))


if __name__ == "__main__":
    unittest.main()
