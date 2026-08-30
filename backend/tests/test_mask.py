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
                                   rasterize_polygons, write_mask_01txt,
                                   write_mask_tif)


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
    def test_01txt_roundtrip(self):
        mask = rasterize_polygons(6, 6, [[[1, 1], [4, 1], [4, 4], [1, 4]]])
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "mask.txt"
            write_mask_01txt(mask, p)
            rows = p.read_text(encoding="ascii").splitlines()
            back = np.array([[1 if ch == "1" else 0 for ch in row]
                             for row in rows], dtype=np.uint8)
            np.testing.assert_array_equal(back, mask)

    def test_tif_roundtrip(self):
        mask = rasterize_polygons(6, 6, [[[1, 1], [4, 1], [4, 4], [1, 4]]])
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "mask.tif"
            write_mask_tif(mask, p)
            back = np.asarray(Image.open(p))
            np.testing.assert_array_equal(back, mask)


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
            txt_path = d / "mask01.txt"
            mask = generate_mask(w, h, polys, tif_path, txt_path)

            self.assertTrue(tif_path.exists())
            self.assertTrue(txt_path.exists())
            self.assertEqual(int(mask.sum()), 36)
            np.testing.assert_array_equal(np.asarray(Image.open(tif_path)), mask)


if __name__ == "__main__":
    unittest.main()
