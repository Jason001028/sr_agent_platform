"""Overview selection, pixel rules, and conservative fallback regression tests."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import tifffile

from backend.services import preview_jpg as pj


def fixture(path, *, subifds=True, big=False, overview=True, compressed=False):
    y, x = np.indices((257, 513))
    base = ((x * 3 + y * 5) % 4096).astype('uint16')
    with tifffile.TiffWriter(path, bigtiff=big) as writer:
        writer.write(base, subifds=2 if subifds and overview else None)
        if overview:
            for div in (2, 4):
                writer.write(base[::div, ::div], subfiletype=1,
                             compression='deflate' if compressed else None)
    return base


class TestOverview(unittest.TestCase):
    def test_selection_and_exact_dimensions(self):
        for sub in (True, False):
            for big in (True, False):
                with self.subTest(sub=sub, big=big), tempfile.TemporaryDirectory() as d:
                    p = Path(d) / 'p.tif'
                    base = fixture(p, subifds=sub, big=big)
                    original = pj.sample_strips
                    for div in pj.PREVIEW_DIVISORS:
                        edge = pj.preview_max_edge(p, div)
                        with patch.object(pj, 'sample_strips', wraps=original) as read:
                            out = pj.build_preview_pixels(p, edge)
                        level = 1 if div == 1 else (2 if div == 2 else 4)
                        self.assertEqual(read.call_args.args[2][256], base[::level, ::level].shape[1])
                        scale = edge / max(base.shape)
                        self.assertEqual(out.shape, tuple(max(1, round(v * scale)) for v in base.shape))
                        with patch.object(pj, '_overview_preview', return_value=None):
                            self.assertEqual(out.shape, pj.build_preview_pixels(p, edge).shape)

    def test_fallbacks_unchanged(self):
        for overview, compressed in ((False, False), (True, True)):
            with tempfile.TemporaryDirectory() as d:
                p = Path(d) / 'p.tif'
                fixture(p, overview=overview, compressed=compressed)
                with patch.object(pj, '_overview_preview', return_value=None):
                    expected = pj.build_preview_pixels(p)
                np.testing.assert_array_equal(pj.build_preview_pixels(p), expected)
                with patch.object(pj, '_overview_preview', side_effect=OSError('bad overview')):
                    np.testing.assert_array_equal(pj.build_preview_pixels(p), expected)

    def test_multipage_not_overview_and_cache(self):
        with tempfile.TemporaryDirectory() as d:
            p, jpg = Path(d) / 'p.tif', Path(d) / 'p.jpg'
            with tifffile.TiffWriter(p) as w:
                w.write(np.arange(256 * 256, dtype='uint16').reshape(256, 256))
                w.write(np.zeros((128, 128), dtype='uint16'))
            with patch.object(pj, '_overview_preview', return_value=None):
                expected = pj.build_preview_pixels(p)
            np.testing.assert_array_equal(pj.build_preview_pixels(p), expected)
            self.assertEqual(pj.ensure_preview_jpg(p, jpg)['status'], 'generated')
            self.assertEqual(pj.ensure_preview_jpg(p, jpg)['status'], 'cached')
            with patch.object(pj, 'PREVIEW_RULE_VERSION', 'v3'):
                self.assertIsNone(pj.cache_hit(jpg, 85, 2))

    def test_candidate_read_failure_falls_back(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'p.tif'
            fixture(p)
            with patch.object(pj, '_overview_preview', return_value=None):
                expected = pj.build_preview_pixels(p)
            original = pj.sample_strips
            def read(path, endian, scalars, arrays, edge, **kw):
                if scalars[256] < 513:
                    raise OSError('truncated overview')
                return original(path, endian, scalars, arrays, edge, **kw)
            with patch.object(pj, 'sample_strips', side_effect=read):
                np.testing.assert_array_equal(pj.build_preview_pixels(p), expected)

    def test_constant_whitezero_first_band(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'p.tif'
            with tifffile.TiffWriter(p) as w:
                for size, reduced in ((128, False), (64, True)):
                    arr = np.full((size, size), 100, dtype='uint16')
                    w.write(arr, subifds=1 if not reduced else None,
                            subfiletype=int(reduced), photometric='miniswhite')
            np.testing.assert_array_equal(pj.build_preview_pixels(p), np.full((64, 64), 127, dtype='uint8'))


if __name__ == '__main__':
    unittest.main()
