"""Tests for the MTA-Grid planner (pure algorithm, faithful port from SR_code)."""

import unittest

import numpy as np

from backend.mta_grid.grid_planner import count_sr_tiles, grid_offset_planner


def _naive_count(mask, wt, wb, wl, wr, t_ht, t_wd):
    """Reference tile counter (plain loops) to cross-check the vectorized one."""
    m = np.pad(mask, ((wt, wb), (wl, wr)), mode="constant", constant_values=0)
    rows, cols = m.shape[0] // t_ht, m.shape[1] // t_wd
    n = 0
    for r in range(rows):
        for c in range(cols):
            if m[r * t_ht:(r + 1) * t_ht, c * t_wd:(c + 1) * t_wd].any():
                n += 1
    return n


class TestCountSrTiles(unittest.TestCase):
    def test_matches_naive_reference(self):
        rng = np.random.default_rng(0)
        for _ in range(200):
            h, w = rng.integers(1, 12, size=2)
            mask = rng.random((int(h), int(w))) > 0.6
            t_ht = int(rng.integers(1, 8))
            t_wd = int(rng.integers(1, 8))
            wt, wb = int(rng.integers(0, 8)), int(rng.integers(0, 8))
            wl, wr = int(rng.integers(0, 8)), int(rng.integers(0, 8))
            got = count_sr_tiles(mask, wt, wb, wl, wr, t_ht, t_wd)
            want = _naive_count(mask, wt, wb, wl, wr, t_ht, t_wd)
            self.assertEqual(got, want)


class TestGridOffsetPlanner(unittest.TestCase):
    def test_empty_mask_returns_none(self):
        dy, dx, stats = grid_offset_planner(
            np.zeros((8, 8), dtype=bool),
            {"wrap_top": 4, "wrap_btm": 4, "wrap_lt": 4, "wrap_rt": 4},
            4, 4, 0)
        self.assertEqual((dy, dx), (0, 0))
        self.assertIsNone(stats)

    def test_known_case(self):
        # 8x8 mask, 2x2 ROI at rows/cols [3,4]; tile 4x4; wrap 4; pad 0.
        # No shift -> 4 tiles; shift (-1,-1) -> single cell -> 1 tile.
        mask = np.zeros((8, 8), dtype=bool)
        mask[3:5, 3:5] = True
        dy, dx, stats = grid_offset_planner(
            mask,
            {"wrap_top": 4, "wrap_btm": 4, "wrap_lt": 4, "wrap_rt": 4},
            4, 4, 0)
        self.assertEqual((dy, dx), (-1, -1))
        self.assertEqual(stats["before_tiles"], 4)
        self.assertEqual(stats["after_tiles"], 1)
        self.assertEqual(stats["saved_tiles"], 3)

    def test_never_worse_and_consistent(self):
        rng = np.random.default_rng(1)
        for _ in range(200):
            h, w = rng.integers(3, 12, size=2)
            mask = rng.random((int(h), int(w))) > 0.7
            t_ht = int(rng.integers(1, 6))
            t_wd = int(rng.integers(1, 6))
            wt, wb = int(rng.integers(0, 6)), int(rng.integers(0, 6))
            wl, wr = int(rng.integers(0, 6)), int(rng.integers(0, 6))
            dy, dx, stats = grid_offset_planner(
                mask,
                {"wrap_top": wt, "wrap_btm": wb, "wrap_lt": wl, "wrap_rt": wr},
                t_ht, t_wd, 0)
            if stats is None:
                continue
            self.assertLessEqual(stats["after_tiles"], stats["before_tiles"])
            self.assertEqual(stats["saved_tiles"],
                             stats["before_tiles"] - stats["after_tiles"])
            reapplied = count_sr_tiles(mask, wt - dy, wb + dy, wl - dx, wr + dx,
                                       t_ht, t_wd)
            self.assertEqual(reapplied, stats["after_tiles"])


if __name__ == "__main__":
    unittest.main()
