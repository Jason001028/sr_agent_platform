"""MTA-Grid origin planner (pure numpy, faithful port from SR_code).

Shift the SR tile-grid origin by (delta_y, delta_x) so grid lines hug the ROI
bbox and minimize the number of SR tiles. No I/O, no framework deps — this is
the kind of module the agent tool layer wraps.

Source of truth: SR_code/code_0820_prod_windows.py `_count_sr_tiles` /
`_grid_offset_planner` (verbatim logic, public names).
"""

from __future__ import annotations

import numpy as np


def count_sr_tiles(mask, wrap_top, wrap_btm, wrap_lt, wrap_rt, t_ht, t_wd):
    """Count SR tiles intersecting the mask (same metric as Phase A pre-scan)."""
    m = np.pad(mask, ((wrap_top, wrap_btm), (wrap_lt, wrap_rt)),
               mode="constant", constant_values=0)
    rows, cols = m.shape[0] // t_ht, m.shape[1] // t_wd
    if rows == 0 or cols == 0:
        return 0
    return int(m[:rows * t_ht, :cols * t_wd]
               .reshape(rows, t_ht, cols, t_wd).any(axis=(1, 3)).sum())


def grid_offset_planner(mask_binary, tl_obj, t_ht, t_wd, pad):
    """Pick grid offset (delta_y, delta_x) that minimizes SR tile count.

    Returns (delta_y, delta_x, stats); stats is None for an empty mask.
    Candidates: bbox edges aligned to grid lines (+ (0,0) fallback),
    constrained by pad/wrap slack. Never worse than no shift.
    """
    ys = np.flatnonzero(mask_binary.any(axis=1))
    if ys.size == 0:
        return 0, 0, None
    xs = np.flatnonzero(mask_binary.any(axis=0))
    y0, y1, x0, x1 = ys[0], ys[-1], xs[0], xs[-1]
    bh, bw = y1 - y0 + 1, x1 - x0 + 1
    roi = int(mask_binary.sum())

    wt, wb, wl, wr = (tl_obj["wrap_top"], tl_obj["wrap_btm"],
                      tl_obj["wrap_lt"], tl_obj["wrap_rt"])

    def count(wt_, wb_, wl_, wr_):
        return count_sr_tiles(mask_binary, wt_, wb_, wl_, wr_, t_ht, t_wd)

    n_before = count(wt, wb, wl, wr)

    # bbox top-left in wrap coords = (wt + y0, wl + x0); align it to grid lines.
    y_wrap, x_wrap = wt + y0, wl + x0
    dy_set = {y_wrap % t_ht, (y_wrap + bh) % t_ht}
    dy_set |= {v - t_ht for v in tuple(dy_set)}
    dx_set = {x_wrap % t_wd, (x_wrap + bw) % t_wd}
    dx_set |= {v - t_wd for v in tuple(dx_set)}
    cands = [(dy, dx)
             for dy in sorted(dy_set)
             for dx in sorted(dx_set)
             if wt - dy >= pad and wb + dy >= pad
             and wl - dx >= pad and wr + dx >= pad]
    cands.append((0, 0))  # fallback: no shift

    best = min(cands, key=lambda c: (count(wt - c[0], wb + c[0], wl - c[1], wr + c[1]),
                                     abs(c[0]) + abs(c[1])))
    dy, dx = best
    n_after = count(wt - dy, wb + dy, wl - dx, wr + dx)

    def halo(n):
        return (n * t_ht * t_wd - roi) / roi if roi else 0.0

    stats = dict(before_tiles=n_before, after_tiles=n_after,
                 saved_tiles=n_before - n_after,
                 before_halo=halo(n_before), after_halo=halo(n_after),
                 roi_pixels=roi, delta=(dy, dx))
    return dy, dx, stats
