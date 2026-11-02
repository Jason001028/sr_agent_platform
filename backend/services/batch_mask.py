"""Minimal real Mask IO for the batch pipeline (PR C).

The Mask contract is intentionally small and checkable:

* single-band ``uint8`` TIFF,
* background 0 / foreground 255,
* exactly the PAN width and height,
* at least one foreground pixel,
* published atomically (write a sibling temp file, then ``os.replace``).

This is real file IO, not a stub: the same artifact is what ``run_sr`` receives
through ``mask_path``.  Detection boxes are closed pixel boxes in PAN space and
are rasterized with the existing viewer-compatible fill convention
(``backend.services.mask``).
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import numpy as np
from PIL import Image

from backend.services import mask as mask_service
from backend.services import preview_jpg


class MaskError(RuntimeError):
    """The produced mask does not satisfy the contract."""


def _polygon(bbox):
    if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
        raise MaskError(f"候选框必须是 [x0,y0,x1,y1]：{bbox!r}")
    x0, y0, x1, y1 = (int(v) for v in bbox)
    if x1 < x0 or y1 < y0:
        raise MaskError(f"候选框坐标非法：{bbox!r}")
    # Closed box → pixel-center polygon. The fill is inclusive of the boundary
    # (mask.py's documented convention), matching the viewer's ROI rasterizer.
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_mask(path, *, width, height, candidates, provenance="mock") -> dict:
    """Rasterize candidates and publish one contract-checked mask TIFF.

    ``width``/``height`` must be the PAN dimensions (K6): the SR adapter
    compares the mask against the PAN, not against the lower-resolution NOSR.
    """
    width = int(width)
    height = int(height)
    if width <= 0 or height <= 0:
        raise MaskError(f"掩码尺寸非法：{width}x{height}")
    polygons = [_polygon(candidate["bbox"]) for candidate in candidates]
    if not polygons:
        raise MaskError("没有候选框，拒绝生成空掩码")

    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.stem + ".tmp" + out.suffix)
    try:
        arr = mask_service.rasterize_polygons(width, height, polygons)
        # rasterize_polygons returns 0/1; write_mask_tif promotes to 0/255 with
        # Deflate, the same shape the existing viewer/ROI path produces.
        mask_service.write_mask_tif(arr, tmp)
        _check(tmp, width, height)
        os.replace(tmp, out)
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass

    _check(out, width, height)
    return {
        "path": out.as_posix(),
        "sha256": _sha256(out),
        "revision": preview_jpg.source_revision(out),
        "width": width,
        "height": height,
        "bits": 8,
        "spp": 1,
        "candidates": len(polygons),
        "foreground_pixels": _foreground(out),
        "provenance": provenance,
    }


def verify_mask(path, *, width, height, sha256=None) -> dict:
    """Re-check a previously published mask before any SR submission.

    The Mask is a boundary artifact: waiting for capacity, a pause/resume or a
    process restart can all leave a ``MASK_READY`` item whose file was replaced
    or truncated.  The contract check is the same one ``build_mask`` applied at
    publish time, plus the frozen digest when the caller has one.
    """
    out = Path(path)
    if not out.is_file():
        raise MaskError(f"掩码文件不存在：{out}")
    _check(out, int(width), int(height))
    actual = _sha256(out)
    if sha256 is not None and actual != sha256:
        raise MaskError("掩码摘要与冻结值不一致")
    return {
        "path": out.as_posix(),
        "sha256": actual,
        "width": int(width),
        "height": int(height),
        "bits": 8,
        "spp": 1,
        "foreground_pixels": _foreground(out),
    }


def _read(path: Path):
    with Image.open(path) as image:
        return image.copy()


def _check(path: Path, width: int, height: int) -> None:
    image = _read(path)
    try:
        if image.size != (width, height):
            raise MaskError(
                f"掩码尺寸 {image.size} 与 PAN {(width, height)} 不一致")
        if image.mode != "L":
            raise MaskError(f"掩码必须是单波段 uint8，实际 {image.mode}")
        arr = np.asarray(image)
        if arr.ndim != 2:
            raise MaskError(f"掩码必须是二维单波段，实际 ndim={arr.ndim}")
        values = set(np.unique(arr).tolist())
        if not values.issubset({0, 255}):
            raise MaskError(f"掩码只允许 0/255，实际 {sorted(values)[:8]}")
        if not values - {0}:
            raise MaskError("掩码没有前景像素")
    finally:
        image.close()


def _foreground(path: Path) -> int:
    image = _read(path)
    try:
        return int(np.count_nonzero(np.asarray(image)))
    finally:
        image.close()
