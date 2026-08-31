"""Bad detector-line / striping repair (pure numpy, Pillow for TIFF IO).

Satellite push-broom detectors occasionally output dead or stuck lines: a
whole row (or column) reads near-constant or zero while neighbors carry real
signal. This module detects such lines by comparing each line's robust level
(median) against its good neighbors, then replaces the line pixel-wise with
the per-column median of the good neighbor lines.

No cv2 dependency (not installed on the dev machine) — numpy + Pillow suffice,
matching backend/services/mask.py. OpenCV can replace Pillow for IO later if
format coverage demands it.

The tool wrapper (backend/tools/fix_bad_lines.py) is the thin agent-facing
shell; this module is the business logic.
"""

from __future__ import annotations

import numpy as np
from PIL import Image

# 1.4826 * MAD is a robust estimate of sigma for a Gaussian distribution.
_MAD_TO_SIGMA = 1.4826
# Floor on the robust sigma so near-uniform regions don't flag on tiny noise.
_SIGMA_FLOOR = 1e-6


def read_image(path) -> np.ndarray:
    """Read a TIFF/image into a numpy array (grayscale or RGB)."""
    with Image.open(path) as im:
        return np.asarray(im)


def write_image(path, arr) -> None:
    """Write a numpy array as a TIFF via Pillow (dtype preserved)."""
    Image.fromarray(arr).save(path)


def detect_bad_lines(arr, axis=0, window=5, threshold=3.0):
    """Return indices of bad lines (rows if axis==0, columns if axis==1).

    A line is flagged when its robust level (median) deviates from the median
    of its `window` good neighbors by more than `threshold` robust sigmas
    (1.4826 * MAD of the neighbor levels). Never raises; a flat image flags
    nothing. Consecutive bad-line clusters may under-report (inflated MAD) —
    acceptable for M1.

    axis==0 → lines are rows: reduce across columns (np.median(axis=1)).
    axis==1 → lines are columns: reduce across rows (np.median(axis=0)).
    """
    reduce_axis = 1 - axis
    stats = np.median(arr, axis=reduce_axis).astype(np.float64)
    n = stats.shape[0]
    bad = np.zeros(n, dtype=bool)
    for i in range(n):
        lo = max(0, i - window)
        hi = min(n, i + window + 1)
        nb = np.concatenate([stats[lo:i], stats[i + 1:hi]])
        if nb.size == 0:
            continue
        med = np.median(nb)
        mad = np.median(np.abs(nb - med))
        sigma = max(_MAD_TO_SIGMA * mad, _SIGMA_FLOOR)
        if abs(stats[i] - med) / sigma > threshold:
            bad[i] = True
    return np.flatnonzero(bad)


def fix_bad_lines(arr, axis=0, window=5, threshold=3.0):
    """Detect and replace bad lines; returns (fixed_array, bad_line_indices).

    Each bad line is replaced per-column (per-channel for RGB) with the median
    of the good neighbor lines within `window`, rounded to the source dtype.
    A copy is returned; the source array is never mutated.
    """
    bad = detect_bad_lines(arr, axis=axis, window=window, threshold=threshold)
    fixed = arr.copy()
    if bad.size == 0:
        return fixed, bad

    other = np.setdiff1d(np.arange(arr.shape[axis]), bad)
    for i in bad:
        lo = max(0, i - window)
        hi = min(arr.shape[axis], i + window + 1)
        refs = other[(other >= lo) & (other <= hi)]
        if refs.size == 0:
            refs = other
        if refs.size == 0:
            continue
        rep = np.rint(np.median(np.take(fixed, refs, axis=axis), axis=axis)
                      ).astype(arr.dtype)
        fixed[(slice(None),) * axis + (i,)] = rep
    return fixed, bad


def run(input_path, output_path=None, axis="row", window=5, threshold=3.0):
    """Orchestrate read → detect → replace → optional write.

    Returns a JSON-serializable stats dict (the tool wrapper passes it to ok()).
    """
    arr = read_image(input_path)
    fixed, bad = fix_bad_lines(arr, axis=0 if axis == "row" else 1,
                               window=int(window), threshold=float(threshold))
    stats = {
        "input_path": str(input_path),
        "size": list(arr.shape),
        "dtype": str(arr.dtype),
        "axis": axis,
        "bad_lines": [int(i) for i in bad],
        "count": int(bad.size),
    }
    if output_path:
        write_image(output_path, fixed)
        stats["output_path"] = str(output_path)
    return stats
