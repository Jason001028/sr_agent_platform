"""Tests for mask generation (polygon rasterization + artifact writers).

Fill convention (Pillow ImageDraw.polygon): vertices are pixel centers and the
fill is inclusive of the boundary — a square [2,2]..[7,7] covers 6x6 = 36 px.
"""

import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

from backend.services.mask import (generate_mask, load_polygons_json,
                                   polygon_centroid, rasterize_polygons,
                                   write_mask_centroid_txt, write_mask_tif)


class TestRasterizePolygons(unittest.TestCase):
    def test_single_square(self):
        mask = rasterize_polygons(10, 10, [[[2, 2], [7, 2], [7, 7], [2, 7]]])
        self.assertEqual(mask.shape, (10, 10))
        self.assertEqual(int(mask.sum()), 36)          # 6x6 inclusive
        self.assertEqual(mask[2, 2], 1)                # interior
        self.assertEqual(mask[7, 7], 1)                # boundary inclusive
        self.assertEqual(mask[0, 0], 0)                # exterior
        self.assertEqual(mask[9, 9], 0)                # exterior

    def test_triangle(self):
        # right triangle, legs along the axes: inclusive fill = 4+3+2+1 = 10
        mask = rasterize_polygons(8, 8, [[[1, 1], [4, 1], [1, 4]]])
        self.assertEqual(int(mask.sum()), 10)
        self.assertEqual(mask[1, 1], 1)

    def test_multiple_disjoint(self):
        mask = rasterize_polygons(12, 12, [
            [[1, 1], [4, 1], [4, 4], [1, 4]],
            [[7, 7], [10, 7], [10, 10], [7, 10]],
        ])
        self.assertEqual(int(mask.sum()), 32)          # 16 + 16
        self.assertEqual(mask[5, 5], 0)                # gap between them

    def test_empty_polygon_list(self):
        mask = rasterize_polygons(6, 6, [])
        self.assertEqual(int(mask.sum()), 0)

    def test_degenerate_polygon_skipped(self):
        mask = rasterize_polygons(6, 6, [[[1, 1], [2, 2]]])  # < 3 vertices
        self.assertEqual(int(mask.sum()), 0)


class TestWriters(unittest.TestCase):
    def test_centroid_txt_matches_reference_format(self):
        polys = [[[2, 2], [7, 2], [7, 7], [2, 7]]]
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "mask.txt"
            write_mask_centroid_txt(polys, p)
            # newline="" 保留原始 \r\n（read_text 默认通用换行会翻译掉 \r）
            with p.open(encoding="utf-8", newline="") as f:
                text = f.read()
            self.assertTrue(text.startswith(
                "＃掩膜中心点坐标（X，Y）\r\n＃掩膜编号，X坐标，Y坐标\r\n"))
            self.assertTrue(text.endswith("1,4.50,4.50\r\n"), repr(text[-30:]))
            # 无裸 \n（换行必须是 \r\n）
            self.assertIsNone(__import__("re").search(r"[^\r]\n", text))
            self.assertIn("\r\n", text)

    def test_centroid_txt_numbering_multiple(self):
        polys = [
            [[0, 0], [4, 0], [4, 4], [0, 4]],
            [[10, 10], [14, 10], [14, 14], [10, 14]],
        ]
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "mask.txt"
            write_mask_centroid_txt(polys, p)
            with p.open(encoding="utf-8", newline="") as f:
                text = f.read()
            self.assertIn("1,2.00,2.00\r\n", text)
            self.assertIn("2,12.00,12.00\r\n", text)

    def test_tif_roundtrip_0_255(self):
        mask = rasterize_polygons(6, 6, [[[1, 1], [4, 1], [4, 4], [1, 4]]])
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "mask.tif"
            write_mask_tif(mask, p)
            back = np.asarray(Image.open(p))
            np.testing.assert_array_equal(back, mask * 255)   # 0/1 → 0/255

    def test_tif_write_255_passthrough(self):
        mask = (np.arange(9).reshape(3, 3) > 0).astype(np.uint8) * 255
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "mask.tif"
            write_mask_tif(mask, p)
            back = np.asarray(Image.open(p))
            np.testing.assert_array_equal(back, mask)


class TestPolygonCentroid(unittest.TestCase):
    def test_centroids(self):
        self.assertEqual(polygon_centroid([[2, 2], [7, 2], [7, 7], [2, 7]]), (4.5, 4.5))
        cx, cy = polygon_centroid([[0, 0], [6, 0], [0, 6]])
        self.assertAlmostEqual(cx, 2.0)
        self.assertAlmostEqual(cy, 2.0)

    def test_degenerate_falls_back_to_bbox_center(self):
        self.assertEqual(polygon_centroid([[1, 1], [3, 1]]), (2.0, 1.0))


class TestGenerateMask(unittest.TestCase):
    def test_writes_both_files_and_loads_json(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            json_path = d / "polygons.json"
            json_path.write_text(
                '{"width": 10, "height": 10, '
                '"polygons": [{"label": "roi", '
                '"points": [[2, 2], [7, 2], [7, 7], [2, 7]]}]}',
                encoding="utf-8")

            w, h, polys = load_polygons_json(json_path)
            self.assertEqual((w, h), (10, 10))
            self.assertEqual(len(polys), 1)

            tif_path = d / "mask.tif"
            txt_path = d / "mask.txt"
            mask = generate_mask(w, h, polys, tif_path, txt_path)

            self.assertTrue(tif_path.exists())
            self.assertTrue(txt_path.exists())
            self.assertEqual(int(mask.sum()), 36)
            np.testing.assert_array_equal(np.asarray(Image.open(tif_path)), mask * 255)
            with txt_path.open(encoding="utf-8", newline="") as f:
                txt = f.read()
            self.assertIn("1,4.50,4.50\r\n", txt)


if __name__ == "__main__":
    unittest.main()
