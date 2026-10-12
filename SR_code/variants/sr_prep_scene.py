#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Land the un-superresolved copy on the body name, before the SR script runs.

Runs *inside the batch job*, right after ``cd $SR_BUNDLE_DIR`` and before
``code_0817_prod*.py -f <config.xml>``. The SR script resolves its input itself
and the config.xml carries only the scene directory, so the way to make it read
the un-superresolved copy is to put that copy at the body name:

    1. keep one backup of the body,
    2. copy the un-superresolved raster over the body name,
    3. resample the mask to the input's pixel size.

Step 2 is a copy, not a rename: the archive copy stays where it is. Step 1
creates the backup only when the backup is absent, so a job killed before it
finishes leaves the *pre-run* body in the backup and a retry cannot overwrite
it. ``verify_sr_run.py`` is what consumes the backup: it lands the product on
the body name and drops the backup on success, and restores the body from the
backup when the run fails or is skipped by policy.

Step 3 exists because the mask is authored at the pixel size of the raster the
operator drew on, which need not be the size of the SR input. Shrinking takes
the maximum of each block, so an ROI pixel never disappears; growing takes the
nearest pixel; a mask already at the input's size is copied through unchanged.

Both paths come from the caller (``backend/services/run_sr.py`` bakes them in as
absolute POSIX paths at generation time — the job carries ``--export=NONE``, so
nothing set on the API host survives into it).

Exit codes: 0 = the scene is ready, 91 = not (nothing may run against it).
"""

import argparse
import os
import shutil
import sys

import numpy as np

try:
    from osgeo import gdal
except ImportError:                         # the SR env always has GDAL
    gdal = None

__version__ = "1.0.0"

EXIT_OK = 0
EXIT_PREP_FAIL = 91

#: GDAL's identity geotransform — the value a raster carries when it has none.
#: Compared against so a mask never gets handed a made-up grid.
_DEFAULT_GT = (0.0, 1.0, 0.0, 0.0, 0.0, 1.0)


def _log(msg):
    sys.stderr.write("sr_prep_scene: %s\n" % (msg,))


# ---------------------------------------------------------------------------
# step 1/2 -- body and its backup
# ---------------------------------------------------------------------------

def keep_backup(body, backup):
    """Copy the pre-run body aside once.  Returns "kept" or "exists"."""
    if os.path.exists(backup):
        return "exists"
    shutil.copy2(body, backup)
    return "kept"


def land_source(body, nosr):
    """Overwrite the body with the un-superresolved copy."""
    shutil.copy2(nosr, body)


# ---------------------------------------------------------------------------
# step 3 -- mask at the input's pixel size
# ---------------------------------------------------------------------------

def _resample_axis(arr, axis, n):
    """Resize one axis to n samples: block max when shrinking, nearest else.

    ``np.maximum.reduceat`` treats bounds[i]..bounds[i+1] as one block, so the
    block boundaries come from evenly dividing the source length. Shrinking
    (m > n) makes every step at least one pixel, which is what keeps the blocks
    non-empty; a window is never dropped.
    """
    m = arr.shape[axis]
    if n == m:
        return arr
    if m > n:
        bounds = np.floor(np.arange(n + 1) * (float(m) / n)).astype(np.int64)
        np.clip(bounds, 0, m, out=bounds)
        return np.maximum.reduceat(arr, bounds[:-1].tolist(), axis=axis)
    idx = np.minimum(((np.arange(n) + 0.5) * (float(m) / n)).astype(np.int64),
                     m - 1)
    return np.take(arr, idx, axis=axis)


def resample_mask(mask_path, src_path, out_path):
    """Write `mask_path` at the pixel size of `src_path` into `out_path`.

    `src_path` is the raster the SR script will read (the un-superresolved
    copy), so the result fits the input exactly. The output carries the
    source's geotransform and projection when the source has them, so the mask
    sits on the same grid as that input. Returns "copied" or "resampled".
    """
    if gdal is None:
        raise IOError("GDAL 不可用：作业环境缺 osgeo")
    src = gdal.Open(str(src_path), gdal.GA_ReadOnly)
    if src is None:
        raise IOError("输入影像不可读: %s" % (src_path,))
    width, height = src.RasterXSize, src.RasterYSize
    src_gt, src_proj = src.GetGeoTransform(), src.GetProjection()
    src = None

    handle = gdal.Open(str(mask_path), gdal.GA_ReadOnly)
    if handle is None:
        raise IOError("掩码不可读: %s" % (mask_path,))
    band = handle.GetRasterBand(1)
    arr = band.ReadAsArray()
    datatype, nodata = band.DataType, band.GetNoDataValue()
    handle = None
    if arr is None:
        raise IOError("掩码没有像素数据: %s" % (mask_path,))
    if np.ma.isMaskedArray(arr):
        # A nodata-carrying mask: fill with background before resampling, so a
        # hole can never be promoted into an ROI pixel by the block max.
        arr = arr.filled(0)

    if arr.shape[0] == height and arr.shape[1] == width:
        shutil.copyfile(str(mask_path), str(out_path))
        return "copied"

    out = _resample_axis(arr, 1, width)
    out = np.ascontiguousarray(_resample_axis(out, 0, height))

    if os.path.exists(out_path):
        gdal.Unlink(str(out_path))          # the GTiff driver cannot overwrite
    ds = gdal.GetDriverByName("GTiff").Create(
        str(out_path), width, height, 1, datatype,
        options=["COMPRESS=DEFLATE"])       # what backend/services/mask.py writes
    if ds is None:
        raise IOError("掩码临时件写不出去: %s" % (out_path,))
    if src_gt and tuple(src_gt) != _DEFAULT_GT:
        ds.SetGeoTransform(src_gt)
    if src_proj:
        ds.SetProjection(src_proj)
    out_band = ds.GetRasterBand(1)
    if nodata is not None:
        out_band.SetNoDataValue(nodata)
    out_band.WriteArray(out)
    out_band.FlushCache()
    ds = None                               # flush + close
    return "resampled"


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="sr_prep_scene.py",
        description="Land the un-superresolved copy on the body name and "
                    "resample the mask to that input's pixel size.")
    ap.add_argument("--body", required=True,
                    help="the input image the SR script resolves")
    ap.add_argument("--nosr", required=True,
                    help="the un-superresolved copy")
    ap.add_argument("--backup", required=True,
                    help="where the pre-run body is kept")
    ap.add_argument("--mask", required=True,
                    help="the operator's mask")
    ap.add_argument("--mask-1x", required=True, dest="mask_1x",
                    help="where the input-sized mask is written")
    args = ap.parse_args(argv)

    try:
        # Both scratch files live in the scene's Debug/ dir, which the SR script
        # only creates later (when it writes its SRLOG).
        debug_dir = os.path.dirname(args.backup)
        if debug_dir:
            os.makedirs(debug_dir, exist_ok=True)
        _log("backup %s: %s" % (keep_backup(args.body, args.backup),
                                args.backup))
        land_source(args.body, args.nosr)
        _log("body landed from %s" % (args.nosr,))
        _log("mask %s -> %s" % (resample_mask(args.mask, args.nosr,
                                              args.mask_1x), args.mask_1x))
    except Exception as exc:                # noqa: BLE001 -- report, never raise
        _log("场景准备失败 %s: %s" % (type(exc).__name__, exc))
        return EXIT_PREP_FAIL
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
