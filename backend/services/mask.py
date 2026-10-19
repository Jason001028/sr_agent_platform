"""Mask generation: rasterize ROI polygons to a full-resolution binary mask.

The HTML viewer draws ROI polygons on the (downsampled) preview, maps vertex
coordinates back to original-image pixels, and hands them off as vector data.
This module turns that vector into the two artifacts `run_sr` consumes:

  掩码.tif        — uint8 grayscale TIFF (0/255, Deflate), same WxH as source.
  掩膜中心点坐标.txt — centroid dot-matrix, reference format (see below).

Rasterization uses Pillow (already a dev dep). The intranet SR bundle ships
cv2, so `cv2.fillPoly` can replace `ImageDraw.polygon` later if it proves
faster on multi-hundred-MP masks.

Vector mask format (provisional, produced by the HTML viewer):

    {"width": W, "height": H,
     "polygons": [{"label": "roi", "points": [[x, y], ...]}, ...]}

where x = column (0..W-1), y = row (0..H-1), in original-image pixels.

Fill convention: vertices are pixel centers; the fill is inclusive of the
boundary (a square [2,2]..[7,7] covers columns 2..7 and rows 2..7).

The 掩膜中心点坐标 txt follows the reference file
SR_code/JL1KF02B03_..._mask.txt (UTF-8, CRLF, full-width header):

    ＃掩膜中心点坐标（X，Y）
    ＃掩膜编号，X坐标，Y坐标
    1,25413.59,10197.36
    ...

i.e. one row per mask region: 序号,质心X(列),质心Y(行), centroids to 2dp.
`run_sr` reads the mask via util.read_img + cv2.threshold(>0), so 0/255 and
0/1 are equivalent to it.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

# 参考格式头两行（全角字符、CRLF）——与 tif_viewer/maskgen.js MASK_TXT_HEADER 一致。
MASK_TXT_HEADER = "＃掩膜中心点坐标（X，Y）\r\n＃掩膜编号，X坐标，Y坐标\r\n"


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


def polygon_centroid(poly):
    """Area centroid (cx, cy) of a polygon (list of [x, y]).

    Matches maskgen.js polygonCentroid: degenerate (colinear/point) input
    falls back to the bounding-box center.
    """
    n = len(poly)
    x = [float(p[0]) for p in poly]
    y = [float(p[1]) for p in poly]
    area = 0.0
    cx = cy = 0.0
    for i in range(n):
        j = (i + 1) % n
        cross = x[i] * y[j] - x[j] * y[i]
        area += cross
        cx += (x[i] + x[j]) * cross
        cy += (y[i] + y[j]) * cross
    area *= 0.5
    if abs(area) < 1e-12:
        return (min(x) + max(x)) / 2.0, (min(y) + max(y)) / 2.0
    return cx / (6 * area), cy / (6 * area)


def write_mask_tif(mask, path):
    """Write the mask as a uint8 0/255 grayscale TIFF (Deflate)."""
    arr = np.asarray(mask, dtype=np.uint8)
    if arr.max() <= 1:  # 0/1 → 0/255（与前端 maskgen.js buildTiff 一致）
        arr = arr * 255
    Image.fromarray(arr).save(path, compression="tiff_deflate")


def write_mask_centroid_txt(polygons, path):
    """Write 掩膜中心点坐标 txt in the reference format.

    polygons — list of [x, y] vertex lists. One row per polygon (regardless of
    rasterization, matching maskgen.js buildMaskTxt): 序号,质心X,质心Y (2dp).
    """
    lines = [MASK_TXT_HEADER]
    for i, poly in enumerate(polygons):
        cx, cy = polygon_centroid(poly)
        lines.append("{},{:.2f},{:.2f}\r\n".format(i + 1, cx, cy))
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write("".join(lines))


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
    """Rasterize polygons and write both 掩码.tif and 掩膜中心点坐标.txt."""
    mask = rasterize_polygons(width, height, polygons)
    write_mask_tif(mask, tif_path)
    write_mask_centroid_txt(polygons, txt_path)
    return mask


if __name__ == "__main__":
    # Usage: python -m backend.services.mask <polygons.json> <out.tif> <out.txt>
    w, h, polys = load_polygons_json(sys.argv[1])
    mask = generate_mask(w, h, polys, sys.argv[2], sys.argv[3])
    print("mask {}x{} roi={}px -> {} / {}".format(
        w, h, int(mask.sum()), sys.argv[2], sys.argv[3]))
