"""Mask generation: rasterize ROI polygons to a full-resolution binary mask.

The HTML viewer draws ROI polygons on the (downsampled) preview, maps vertex
coordinates back to original-image pixels, and hands them off as vector data.
This module turns that vector into the two artifacts `run_sr` consumes:

  掩码.tif        — uint8 raster, 0=background / 1=ROI, same WxH as the source.
  掩码01点阵.txt  — text dot-matrix (one line per row, W chars of '0'/'1').

Rasterization uses Pillow (already a dev dep). The intranet SR bundle ships
cv2, so `cv2.fillPoly` can replace `ImageDraw.polygon` later if it proves
faster on multi-hundred-MP masks.

Vector mask format (provisional, produced by the HTML viewer):

    {"width": W, "height": H,
     "polygons": [{"label": "roi", "points": [[x, y], ...]}, ...]}

where x = column (0..W-1), y = row (0..H-1), in original-image pixels.

Fill convention: vertices are pixel centers; the fill is inclusive of the
boundary (a square [2,2]..[7,7] covers columns 2..7 and rows 2..7).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw


def rasterize_polygons(width, height, polygons):
    """Rasterize a list of polygons into a binary mask (uint8 0/1).

    width/height — original-image size in pixels (mask output size).
    polygons     — list of polygons; each is a list of [x, y] vertices.
    Returns a (height, width) uint8 ndarray, 0=background, 1=inside a polygon.
    """
    img = Image.new("L", (width, height), 0)
    draw = ImageDraw.Draw(img)
    for poly in polygons:
        pts = [(int(x), int(y)) for x, y in poly]
        if len(pts) >= 3:
            draw.polygon(pts, fill=1)
    return np.asarray(img, dtype=np.uint8)


def write_mask_tif(mask, path):
    """Write the mask as a uint8 grayscale TIFF (0/1)."""
    Image.fromarray(np.asarray(mask, dtype=np.uint8)).save(path)


def write_mask_01txt(mask, path):
    """Write the mask as a 01 dot-matrix text (one line per row, no separator).

    NOTE: full-resolution text is W*H characters — a 24k x 24k mask is ~600 MB.
    Format is provisional pending confirmation against the existing mask tool.
    """
    m = np.asarray(mask, dtype=bool)
    rows = np.where(m, 49, 48).astype(np.uint8)  # b'1'=49, b'0'=48
    with open(path, "wb") as f:
        for row in rows:
            f.write(row.tobytes())
            f.write(b"\n")


def load_polygons_json(path):
    """Load the vector mask JSON emitted by the HTML viewer.

    Returns (width, height, polygons) with polygons = list of [x, y] lists.
    """
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    width = int(data["width"])
    height = int(data["height"])
    polygons = [p["points"] for p in data["polygons"]]
    return width, height, polygons


def generate_mask(width, height, polygons, tif_path, txt_path):
    """Rasterize polygons and write both 掩码.tif and 掩码01点阵.txt."""
    mask = rasterize_polygons(width, height, polygons)
    write_mask_tif(mask, tif_path)
    write_mask_01txt(mask, txt_path)
    return mask


if __name__ == "__main__":
    # Usage: python -m backend.services.mask <polygons.json> <out.tif> <out.txt>
    w, h, polys = load_polygons_json(sys.argv[1])
    mask = generate_mask(w, h, polys, sys.argv[2], sys.argv[3])
    print("mask {}x{} roi={}px -> {} / {}".format(
        w, h, int(mask.sum()), sys.argv[2], sys.argv[3]))
