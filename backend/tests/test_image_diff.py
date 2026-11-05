"""Tests for backend/services/image_diff.py — 只读差异分析管线。

覆盖四类基本行为：两张完全相同的图不出候选；局部方块差异能稳定定位到
包围盒；散点噪声被最小面积/形态学滤掉而连续块保留；SSIM 与像素差分输出
同一套结构且阈值互不影响。全部走合成数组，不依赖盘阵与预览缓存。
"""

import base64
import io
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PIL import Image, TiffImagePlugin

from backend.services import image_diff


def gradient(h=96, w=128):
    y = np.linspace(0.05, 0.95, h, dtype=np.float32)[:, None]
    x = np.linspace(0.0, 1.0, w, dtype=np.float32)[None, :]
    return np.clip(0.4 * y + 0.6 * x, 0.0, 1.0).astype(np.float32)


class ArrayAnalysisTest(unittest.TestCase):
    def test_identical_images_have_no_regions(self):
        a = gradient()
        out = image_diff.analyze_arrays(a, a.copy())
        self.assertEqual(out["regions"], [])
        self.assertEqual(out["analysisGrid"]["width"], a.shape[1])
        self.assertEqual(out["coverage"]["validRatio"], 1.0)
        self.assertTrue(out["differenceMap"].startswith("data:image/png;base64,"))
        self.assertTrue(out["binaryMask"].startswith("data:image/png;base64,"))

    def test_validity_map_distinguishes_nodata_from_zero_difference(self):
        a = gradient()
        a[10:20, 30:40] = np.nan
        out = image_diff.analyze_arrays(a, a.copy())
        def png(key):
            return np.asarray(Image.open(io.BytesIO(base64.b64decode(out[key].split(",", 1)[1]))))
        valid = png("validityMap")
        self.assertEqual(valid.shape, a.shape)
        np.testing.assert_array_equal(valid > 0, np.isfinite(a))
        self.assertTrue((png("differenceMap") == 0).all())
        self.assertTrue((png("binaryMask") == 0).all())
        self.assertEqual(out["regions"], [])
        self.assertEqual(out["coverage"]["validPixels"], int(np.isfinite(a).sum()))

    def test_local_block_difference_is_located(self):
        a = gradient()
        b = a.copy()
        b[30:50, 40:70] += 0.45
        out = image_diff.analyze_arrays(b, a)  # 换序也应得到同一块
        self.assertEqual(len(out["regions"]), 1)
        reg = out["regions"][0]
        x, y, w, h = reg["bbox"]
        # 高斯平滑会让边界略微外扩，但中心与尺度必须落在方块上。
        self.assertLessEqual(abs((x + w / 2) - 55), 4)
        self.assertLessEqual(abs((y + h / 2) - 40), 4)
        self.assertGreater(reg["areaPx"], 400)
        self.assertGreater(reg["meanDiff"], 0.2)
        self.assertGreater(reg["maxDiff"], 0.4)
        self.assertGreater(len(reg["contour"]), 3)
        self.assertEqual(reg["id"], "r001")
        self.assertEqual(reg["rank"], 1)

    def test_sparse_noise_filtered_but_block_kept(self):
        a = gradient()
        b = a.copy()
        # 孤立单点在开运算 / 最小面积两道关卡上都应被滤掉。
        b[4::8, 4::8] += 0.8
        b[60:84, 90:114] += 0.5
        out = image_diff.analyze_arrays(
            a, b, sensitivity="standard", min_area_px=64,
            open_kernel=3, close_kernel=3,
        )
        self.assertEqual(len(out["regions"]), 1, out["regions"])
        reg = out["regions"][0]
        cx = reg["bbox"][0] + reg["bbox"][2] / 2
        cy = reg["bbox"][1] + reg["bbox"][3] / 2
        self.assertLessEqual(abs(cx - 102), 4)
        self.assertLessEqual(abs(cy - 72), 4)
        self.assertGreaterEqual(reg["areaPx"], 64)

    def test_threshold_override_can_empty_result(self):
        a = gradient()
        b = a.copy()
        b[30:50, 40:70] += 0.3
        out = image_diff.analyze_arrays(a, b, threshold=0.95)
        self.assertEqual(out["regions"], [])
        self.assertEqual(out["parameters"]["threshold"], 0.95)

    def test_ssim_pipeline_shares_structure(self):
        a = gradient()
        b = a.copy()
        b[30:50, 40:70] += 0.45
        out = image_diff.analyze_arrays(a, b, method="ssim")
        self.assertEqual(out["method"], "ssim")
        self.assertEqual(out["parameters"]["method"], "ssim")
        self.assertGreaterEqual(len(out["regions"]), 1)
        reg = out["regions"][0]
        self.assertGreater(reg["meanDiff"], 0.2)
        self.assertGreaterEqual(reg["rankScore"], 0)
        self.assertGreater(reg["areaPx"], 400)
        self.assertIn(out["parameters"]["backend"], ("scipy", "scipy+skimage"))
        # 两种方法的阈值预设互不相等：SSIM 的全盘差异通常高于 absdiff。
        self.assertNotEqual(
            image_diff.THRESHOLD_PRESETS["absdiff"]["standard"],
            image_diff.THRESHOLD_PRESETS["ssim"]["standard"],
        )

    def test_nodata_pixels_excluded(self):
        a = gradient()
        b = a.copy()
        valid_a = np.ones_like(a, dtype=bool)
        valid_b = np.ones_like(b, dtype=bool)
        valid_b[:, :64] = False
        b[:, :64] += 0.5
        out = image_diff.analyze_arrays(a, b, valid_a=valid_a, valid_b=valid_b)
        self.assertEqual(out["regions"], [])
        self.assertAlmostEqual(out["coverage"]["validRatio"], 0.5, places=2)

    def test_finite_nodata_sentinel_excluded(self):
        a = gradient()
        b = a.copy()
        b[0:20, 0:20] = -9999.0          # 有限哨兵，不是 NaN
        b[40:60, 40:70] += 0.5
        out = image_diff.analyze_arrays(a, b, nodata_b=-9999.0, min_area_px=64)
        self.assertEqual(len(out["regions"]), 1)
        reg = out["regions"][0]
        self.assertGreater(reg["bbox"][0], 30)
        self.assertTrue(np.isfinite(reg["meanDiff"]))
        self.assertAlmostEqual(out["coverage"]["validRatio"],
                               1 - 400 / (96 * 128), places=3)

    def test_ssim_with_nan_does_not_leak(self):
        a = gradient()
        b = a.copy()
        b[10:20, 10:20] = np.nan
        b[40:60, 40:70] += 0.5
        out = image_diff.analyze_arrays(
            a, b, method="ssim", blur=False, min_area_px=64)
        self.assertGreaterEqual(len(out["regions"]), 1)
        self.assertTrue(all(np.isfinite(r["meanDiff"]) for r in out["regions"]))

    def test_l_shape_contour_is_not_bbox(self):
        a = gradient(60, 60)
        b = a.copy()
        b[10:30, 10:16] += 0.6           # 竖条
        b[24:30, 10:28] += 0.6           # 横脚，L 形，bbox 18×20
        out = image_diff.analyze_arrays(a, b, min_area_px=64)
        self.assertEqual(len(out["regions"]), 1)
        reg = out["regions"][0]
        self.assertEqual(reg["contourKind"], "exact")
        self.assertEqual(reg["holes"], 0)
        xs = [p[0] for p in reg["contour"]]
        ys = [p[1] for p in reg["contour"]]
        bbox_area = (max(xs) - min(xs)) * (max(ys) - min(ys))
        pts = reg["contour"]
        poly_area = abs(sum(
            pts[i][0] * pts[(i + 1) % len(pts)][1]
            - pts[(i + 1) % len(pts)][0] * pts[i][1]
            for i in range(len(pts)))) / 2
        self.assertGreater(poly_area, reg["areaPx"] * 0.6)
        self.assertLess(poly_area, bbox_area * 0.85)

    def test_ring_reports_holes_and_outer_contour(self):
        a = gradient(60, 60)
        b = a.copy()
        b[10:40, 10:40] += 0.6
        b[18:32, 18:32] -= 0.6
        out = image_diff.analyze_arrays(a, b, min_area_px=64)
        self.assertEqual(len(out["regions"]), 1)
        reg = out["regions"][0]
        self.assertEqual(reg["contourKind"], "outer")
        self.assertEqual(reg["holes"], 1)
        self.assertTrue(any("孔洞" in w for w in out["warnings"]))

    def test_rdp_closed_polygon_keeps_corners(self):
        poly = [(0, 0), (0, 10), (10, 10), (10, 0)]
        out = image_diff._rdp(poly, 1.0)
        self.assertEqual(len(out), 4)

    def test_small_square_is_exact(self):
        a = gradient(40, 40)
        b = a.copy()
        b[10:20, 10:20] += 0.6
        out = image_diff.analyze_arrays(a, b, min_area_px=16)
        self.assertEqual(len(out["regions"]), 1)
        self.assertEqual(out["regions"][0]["contourKind"], "exact")

    def test_complex_outline_never_claims_exact_without_coverage(self):
        a = gradient(512, 512)
        b = a.copy()
        b[40:472, 40:46] += 0.6              # 竖脊
        for y in range(40, 472, 8):
            b[y:y + 3, 46:472] += 0.6        # 梳齿，外形点数远超上限
        out = image_diff.analyze_arrays(a, b, min_area_px=64)
        self.assertTrue(out["regions"])
        reg = max(out["regions"], key=lambda r: r["areaPx"])
        self.assertLessEqual(len(reg["contour"]), image_diff.MAX_CONTOUR_POINTS)
        self.assertGreaterEqual(len(reg["contour"]), 3)
        self.assertNotEqual(reg["contourKind"], "bbox")
        # binaryMask 是精确连通域；返回轮廓若标 exact，覆盖率必须达标。
        raw = base64.b64decode(out["binaryMask"].split(",", 1)[1])
        mask = np.asarray(Image.open(io.BytesIO(raw))) > 127
        x, y, w, h = reg["bbox"]
        sub = mask[y:y + h, x:x + w]
        local = [(px - x, py - y) for px, py in reg["contour"]]
        iou = image_diff._polygon_iou(local, sub)
        if reg["contourKind"] == "exact":
            self.assertGreaterEqual(iou, image_diff.CONTOUR_MIN_IOU - 1e-6)
        else:
            self.assertEqual(reg["contourKind"], "approx")

    def test_roi_coords_are_local(self):
        a = gradient(60, 60)
        b = a.copy()
        b[20:40, 20:40] += 0.6
        out = image_diff.analyze_arrays(
            a, b, roi={"x": 10, "y": 12, "w": 40, "h": 40}, min_area_px=64)
        self.assertEqual(out["analysisGrid"]["origin"], [10, 12])
        self.assertEqual(out["analysisGrid"]["width"], 40)
        self.assertEqual(out["analysisGrid"]["height"], 40)
        self.assertEqual(len(out["regions"]), 1)
        x, y, w, h = out["regions"][0]["bbox"]
        self.assertLess(x + w, 40)       # ROI 局部坐标，不是全图坐标
        self.assertLess(y + h, 40)


def write_tiff(path: Path, arr: np.ndarray, nodata: str | None = None) -> Path:
    info = TiffImagePlugin.ImageFileDirectory_v2()
    if nodata is not None:
        info[42113] = nodata              # GDAL_NODATA
    Image.fromarray(arr).save(path, tiffinfo=info, compression="raw")
    return path


def write_float_tiff(path: Path, arr: np.ndarray) -> Path:
    """最小无压缩条带 float32 TIFF 写入器（测试用，不依赖 tifffile）。

    ``arr`` 可以是 ``(H, W)`` 或 ``(H, W, N)``；多波段时 BitsPerSample /
    SampleFormat 按 TIFF 惯例写成数组，正好覆盖「数组形式被误读成标量」的分支。
    """
    if arr.ndim == 2:
        arr = arr[:, :, None]
    h, w, spp = arr.shape
    data = np.ascontiguousarray(arr, dtype="<f4").tobytes()
    entries = [
        (256, 4, [w]), (257, 4, [h]), (258, 3, [32] * spp),
        (259, 3, [1]), (262, 3, [1]), (273, 4, [0]), (277, 3, [spp]),
        (278, 4, [h]), (279, 4, [len(data)]), (284, 3, [1]),
        (339, 3, [3] * spp),
    ]
    ifd_size = 2 + 12 * len(entries) + 4
    cursor = 8 + ifd_size
    extra = {}
    for tag, typ, vals in entries:
        nbytes = len(vals) * (2 if typ == 3 else 4)
        if nbytes > 4:
            extra[tag] = (cursor, vals, typ)
            cursor += nbytes
    pix_off = cursor
    entries[5] = (273, 4, [pix_off])
    buf = bytearray(b"II" + struct.pack("<HI", 42, 8))
    buf += struct.pack("<H", len(entries))
    for tag, typ, vals in entries:
        buf += struct.pack("<HHI", tag, typ, len(vals))
        if tag in extra:
            buf += struct.pack("<I", extra[tag][0])
        elif typ == 3:
            buf += struct.pack("<" + "H" * len(vals), *vals).ljust(4, b"\0")
        else:
            buf += struct.pack("<" + "I" * len(vals), *vals)[:4]
    buf += struct.pack("<I", 0)
    for tag in sorted(extra):
        off, vals, typ = extra[tag]
        buf += struct.pack("<" + ("H" if typ == 3 else "I") * len(vals), *vals)
    buf += data
    Path(path).write_bytes(bytes(buf))
    return Path(path)


class SourceWindowTest(unittest.TestCase):
    def test_window_is_clamped_and_nodata_excluded(self):
        with tempfile.TemporaryDirectory() as tmp:
            arr = np.full((80, 100), 100, dtype=np.int16)
            arr[60:70, 70:90] = 120
            arr[65:70, 75:80] = -9999         # 窗口内的哨兵
            p = write_tiff(Path(tmp) / "a.tif", arr, nodata="-9999")
            out, valid, dims, rect = image_diff.read_source_window(
                p, 70, 60, 170, 160)
            self.assertEqual(dims, (100, 80))
            self.assertEqual(rect, (70, 60, 100, 80))
            self.assertEqual(out.shape, (20, 30))
            self.assertEqual(int((~valid).sum()), 25)
            self.assertGreater(float(out.max()), 110)

    def test_synthetic_tiff_gdal_nodata(self):
        with tempfile.TemporaryDirectory() as tmp:
            arr = np.full((40, 50), -9999, dtype=np.int16)
            arr[10:20, 10:20] = 150
            p = write_tiff(Path(tmp) / "b.tif", arr, nodata="-9999")
            out, valid, dims, rect = image_diff.read_source_window(p, 0, 0, 50, 40)
            self.assertEqual(dims, (50, 40))
            self.assertEqual(rect, (0, 0, 50, 40))
            self.assertAlmostEqual(float(valid.mean()), 100 / (40 * 50), places=4)
            self.assertFalse(valid[0, 0])
            self.assertTrue(valid[15, 15])

    def test_nan_nodata_tag_keeps_finite_pixels(self):
        arr = np.full((32, 32), 12.0, dtype=np.float32)
        arr[5, 5] = np.nan
        with tempfile.TemporaryDirectory() as tmp:
            p = write_tiff(Path(tmp) / "nan.tif", arr, nodata="nan")
            out, valid, dims, rect = image_diff.read_source_window(p, 0, 0, 32, 32)
        self.assertEqual(dims, (32, 32))
        self.assertEqual(rect, (0, 0, 32, 32))
        self.assertEqual(int(valid.sum()), 32 * 32 - 1)
        self.assertFalse(valid[5, 5])
        self.assertTrue(np.all(np.isfinite(out[valid])))
        self.assertAlmostEqual(float(out[valid][0]), 12.0, places=5)

    def test_multiband_sample_format_array_is_parsed_and_rejected(self):
        arr = np.zeros((40, 50, 3), dtype=np.float32)
        arr[:, :, 0] = 100.0
        arr[:, :, 1] = 200.0
        arr[:, :, 2] = 300.0
        with tempfile.TemporaryDirectory() as tmp:
            p = write_float_tiff(Path(tmp) / "rgb.tif", arr)
            info = image_diff.preview_jpg.probe_tiff(p)
            self.assertEqual(info["spp"], 3)
            self.assertEqual(info["bits"], 32)
            self.assertEqual(info["sample_format"], 3)   # 以前会被默认成 1
            with self.assertRaises(image_diff.DiffError) as ctx:
                image_diff.read_source_window(p, 0, 0, 50, 40)
        self.assertIn("单波段", str(ctx.exception))

    def test_single_band_float_tiff_reads_true_dn(self):
        arr = np.full((40, 50), 100.0, dtype=np.float32)
        arr[10:20, 10:20] = 250.0
        with tempfile.TemporaryDirectory() as tmp:
            p = write_float_tiff(Path(tmp) / "f.tif", arr)
            self.assertEqual(image_diff.preview_jpg.probe_tiff(p)["sample_format"], 3)
            out, valid, dims, rect = image_diff.read_source_window(p, 0, 0, 50, 40)
        self.assertTrue(valid.all())
        self.assertAlmostEqual(float(out[15, 15]), 250.0, places=3)
        self.assertAlmostEqual(float(out[0, 0]), 100.0, places=3)

    def test_source_roi_returns_actual_bounds(self):
        from backend.api import app as app_module
        with tempfile.TemporaryDirectory() as tmp:
            a = np.full((80, 100), 100, dtype=np.int16)
            a[60:70, 70:90] = 140
            b = np.full((80, 100), 100, dtype=np.int16)
            pa = write_tiff(Path(tmp) / "a.tif", a)
            pb = write_tiff(Path(tmp) / "b.tif", b)
            body = {
                "precision": "source-roi",
                "method": "absdiff",
                "minAreaPx": 16,
                "roi": {"x": 70, "y": 60, "w": 100, "h": 100},  # 右下越界
                "image_a": {"id": 1, "name": "A", "sceneId": "sa"},
                "image_b": {"id": 2, "name": "B", "sceneId": "sb"},
            }
            with patch.object(app_module, "_diff_source_path",
                              side_effect=lambda sid, root: pa if sid == "sa" else pb), \
                    patch.object(app_module, "scene_dims", return_value={"W": 100, "H": 80}):
                out = app_module._diff_source_roi(body, None)
            grid = out["analysisGrid"]
            self.assertEqual(grid["sourceRoi"], [70, 60, 30, 20])
            self.assertEqual(grid["origin"], [70, 60])
            self.assertEqual(grid["width"], 30)
            self.assertEqual(grid["height"], 20)
            self.assertTrue(any("夹紧" in w for w in out["warnings"]))
            self.assertEqual(len(out["regions"]), 1)
            x, y, w, h = out["regions"][0]["bbox"]
            self.assertLessEqual(x + w, 30)
            self.assertLessEqual(y + h, 20)


class ResizeTest(unittest.TestCase):
    def test_nodata_not_mixed_into_resampled_values(self):
        b = np.full((10, 10), 100.0, dtype=np.float32)
        b[:5, :] = -9999.0                       # 有限 NoData 哨兵
        valid = b != -9999.0
        rb, rv = image_diff._resize_pair((20, 20), b, valid)
        self.assertEqual(rb.shape, (20, 20))
        self.assertTrue(np.all(rb[rv] == np.float32(100.0)))
        self.assertFalse(np.any(np.abs(rb) > 1000.0))
        self.assertGreater(int((~rv).sum()), 0)
        # 有效区实际相同 → 重采样后不应凭空出现差异区域。
        a = np.full((20, 20), 100.0, dtype=np.float32)
        out = image_diff.analyze_arrays(
            a, rb, valid_a=np.ones((20, 20), dtype=bool), valid_b=rv,
            min_area_px=16)
        self.assertEqual(out["regions"], [])
        self.assertLess(out["coverage"]["validRatio"], 1.0)


def rgba_png(gray01):
    """0..1 灰度 → 前端实际发来的 RGBA PNG data URL（alpha=有效性）。"""
    gray = np.clip(np.round(gray01 * 255.0), 0, 255).astype(np.uint8)
    alpha = np.full_like(gray, 255)
    arr = np.stack([gray, gray, gray, alpha], axis=-1)
    buf = io.BytesIO()
    Image.fromarray(arr, mode="RGBA").save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


class SceneIdPayloadTest(unittest.TestCase):
    """只传 sceneId 的默认分析路径：先规划受限共同网格，再采样。"""

    def test_large_scene_previews_are_sampled_to_common_grid(self):
        a = gradient(1200, 600)
        b = a.copy()
        b[200:260, 100:180] += 0.5

        def load_scene(sid):
            arr = a if sid == "a" else b
            return arr, np.ones(arr.shape, dtype=bool), [f"scene {sid}"]

        out = image_diff.analyze_payload({
            "method": "absdiff", "minAreaPx": 16, "maxEdge": 256,
            "image_a": {"id": 1, "name": "A", "sceneId": "a"},
            "image_b": {"id": 2, "name": "B", "sceneId": "b"},
        }, load_scene=load_scene)
        grid = out["analysisGrid"]
        self.assertLessEqual(max(grid["width"], grid["height"]), 256)
        self.assertEqual(grid["width"], 128)     # 600×1200 → 长边 256
        self.assertEqual(grid["height"], 256)
        self.assertGreaterEqual(len(out["regions"]), 1)
        self.assertTrue(any("共同分析网格" in w for w in out["warnings"]))

    def test_aspect_ratio_mismatch_is_rejected_before_analysis(self):
        a = gradient(600, 600)
        b = gradient(600, 300)

        def load_scene(sid):
            arr = a if sid == "a" else b
            return arr, np.ones(arr.shape, dtype=bool), []

        with self.assertRaises(image_diff.DiffError) as ctx:
            image_diff.analyze_payload({
                "maxEdge": 256,
                "image_a": {"id": 1, "name": "A", "sceneId": "a"},
                "image_b": {"id": 2, "name": "B", "sceneId": "b"},
            }, load_scene=load_scene)
        self.assertIn("宽高比", str(ctx.exception))


class PayloadTest(unittest.TestCase):
    def test_payload_decodes_frontend_pngs(self):
        a = gradient(64, 64)
        b = a.copy()
        b[20:36, 20:44] += 0.5
        out = image_diff.analyze_payload({
            "method": "absdiff",
            "sensitivity": "standard",
            "minAreaPx": 16,
            "image_a": {
                "id": 1, "name": "PAN", "sourceWidth": 640, "sourceHeight": 640,
                "thumbWidth": 256, "thumbHeight": 256, "grayPng": rgba_png(a),
            },
            "image_b": {
                "id": 2, "name": "PAN_old", "sourceWidth": 640, "sourceHeight": 640,
                "thumbWidth": 256, "thumbHeight": 256, "grayPng": rgba_png(b),
            },
        })
        self.assertEqual(out["operation"], "difference-localization")
        self.assertEqual(out["imageA"]["name"], "PAN")
        self.assertEqual(out["source"], "preview")
        self.assertTrue(any("预览分析" in w for w in out["warnings"]))
        self.assertEqual(len(out["regions"]), 1)
        reg = out["regions"][0]
        self.assertGreaterEqual(reg["bbox"][2], 20)
        self.assertGreaterEqual(reg["bbox"][3], 14)

    def test_payload_rejects_missing_image_and_oversized_grid(self):
        a = gradient(32, 32)
        with self.assertRaises(image_diff.DiffError):
            image_diff.analyze_payload({"image_a": {"grayPng": rgba_png(a)}})
        with self.assertRaises(image_diff.DiffError):
            image_diff.analyze_payload({
                "image_a": {"grayPng": rgba_png(a)},
                "image_b": {"grayPng": rgba_png(a)},
                "maxEdge": 8,
            })


if __name__ == "__main__":
    unittest.main()
