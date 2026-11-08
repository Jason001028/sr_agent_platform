"""Pure, bounded analysis-grid geometry. No files, SR or Batch dependencies.

All radii and quality metrics refer to analysis pixels (not source DN pixels).
Pixel centres and inclusive integer PIL fill match services.mask. A separate
4x footprint check is required too; it never substitutes for the integer check.
"""
from __future__ import annotations

import hashlib
import json
import base64
import io
import time

import numpy as np
from PIL import Image, ImageDraw
from scipy.ndimage import binary_fill_holes, distance_transform_edt, label
from scipy.spatial import ConvexHull, QhullError

from backend.services.mask import rasterize_polygons

VERSION = "auto-diff-v2-repair"
MAX_REGIONS = 16


def _measure(points, original):
    h, w = original.shape
    filled = rasterize_polygons(w, h, [points]).astype(bool)
    count = int(original.sum())
    coverage = int((filled & original).sum()) / count
    # Stripe the supersampling allocation: <= 4*w * 512 bytes, independent
    # of region height. Same footprint convention as image_diff._polygon_iou.
    covered = 0
    for y in range(0, h, 128):
        rows = min(128, h - y)
        im = Image.new("L", (w * 4, rows * 4))
        ImageDraw.Draw(im).polygon(
            [((float(x) + .5) * 4, (float(yy) - y + .5) * 4) for x, yy in points], fill=1)
        footprint = np.asarray(im).reshape(rows, 4, w, 4).any(axis=(1, 3))
        covered += int((footprint & original[y:y + rows]).sum())
    return coverage, covered / count, int(filled.sum()) / count


def build_auto_mask(labels, regions, *, margin="standard", limit=MAX_REGIONS):
    """One polygon per *sorted independent label*, never a mixed mask contour.

    Callers supply regions with label/x/y/w/h/area attributes. Coordinates in
    returned points are local analysis-grid pixel centres. Source transforms
    and immutable input identities are bound by the API after input preparation.
    """
    from backend.services.image_diff import _trace_outer, _rdp

    if margin not in ("tight", "standard", "loose"):
        raise ValueError("autoMaskMargin must be tight, standard or loose")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 512:
        raise ValueError("auto mask region limit must be in 1..512")
    started = time.perf_counter()
    output = []
    height, width = labels.shape
    for rank, r in enumerate(regions[:limit], 1):
        tick = time.perf_counter()
        # Include enough background for the maximum buffer + retries.
        x0, y0 = max(0, r.x - 24), max(0, r.y - 24)
        x1, y1 = min(width, r.x + r.w + 24), min(height, r.y + r.h + 24)
        original = labels[y0:y1, x0:x1] == r.label
        filled = binary_fill_holes(original)
        holes = filled & ~original
        hole_area = int(holes.sum())
        hole_count = int(label(holes, structure=np.ones((3, 3), bool))[1])
        filled_area = int(filled.sum())
        base = min(max(1., .05 * np.sqrt(filled_area)), 20., max(1., .10 * min(r.w, r.h)))
        radius = max(1., min(20., base * {"tight": .75, "standard": 1., "loose": 1.5}[margin]))
        # Padding makes distance-to-foreground well defined for edge-touching shapes.
        distances = distance_transform_edt(~np.pad(filled, 1))[1:-1, 1:-1]
        area_limit = 1.8 if r.area >= 64 else max(1.8, (r.area + 8 * np.sqrt(r.area) + 16) / r.area)
        large_hole = hole_area > max(16, .10 * filled_area)
        best = None
        fallback = None
        retries = 0
        for retry in range(3):
            buffered = distances <= radius + retry
            contour = _trace_outer(buffered)
            candidates = []
            for eps in (.5, 1., 1.5, 2., 3., 4., 6., 8., 12., 20., 32.):
                poly = _rdp(contour, eps)
                if 3 <= len(poly) <= 24:
                    cov, super_cov, ratio = _measure(poly, original)
                    if cov == 1 and super_cov == 1:
                        candidates.append((poly, cov, super_cov, ratio))
            if candidates:
                # Target 16, prefer a passing area ratio over fewer vertices.
                candidates.sort(key=lambda c: (c[3] > area_limit, len(c[0]) > 16, c[3]))
                best = candidates[0]
                retries = retry
                if best[3] <= area_limit:
                    break
        if best is None or best[3] > area_limit:
            # Hull is a conservative fallback, still independently raster checked.
            yy, xx = np.nonzero(distances <= radius + 2)
            cloud = np.column_stack((xx, yy))
            try:
                hull = cloud[ConvexHull(cloud).vertices].tolist()
            except QhullError:
                hull = []
            if len(hull) < 3 or len(hull) > 24:
                hull = [[int(xx.min()), int(yy.min())], [int(xx.max()), int(yy.min())],
                        [int(xx.max()), int(yy.max())], [int(xx.min()), int(yy.max())]]
                hull_kind = "convex_bbox"
            else:
                hull_kind = "convex_hull"
            metrics = _measure(hull, original)
            if best is None or (metrics[0] == 1 and metrics[1] == 1 and metrics[2] < best[3]):
                best = (hull, *metrics)
                fallback = hull_kind
                retries = 2
        poly, coverage, super_coverage, ratio = best
        diagnostics = []
        if coverage != 1 or super_coverage != 1:
            diagnostics.append("coverage_conflict")
        if ratio > area_limit:
            diagnostics.append("area_ratio_conflict")
        if large_hole:
            diagnostics.append("large_hole_filled")
        if fallback == "convex_bbox":
            diagnostics.append("vertex_limit_fallback")
        digest = hashlib.sha256(np.packbits(original).tobytes() + f"{r.x},{r.y},{r.w},{r.h}".encode()).hexdigest()[:16]
        output.append({
            "region_id": digest, "rank": rank, "coordinate_space": "analysis_pixel",
            "analysis_size": [width, height],
            "analysis_seed": [int(np.nonzero(original)[1][0] + x0), int(np.nonzero(original)[0][0] + y0)],
            "points": [[int(x + x0), int(y + y0)] for x, y in poly],
            "vertex_count": len(poly), "detected_area_px": int(r.area), "coverage_ratio": coverage,
            "supersample_coverage_ratio": super_coverage, "area_ratio": ratio,
            "area_ratio_limit": float(area_limit), "buffer_px": float(radius + retries),
            "buffer_space": "analysis_pixel", "fallback": fallback,
            "needs_review": bool(diagnostics), "diagnostics": diagnostics,
            "holes": {"count": hole_count, "area_px": hole_area, "filled": bool(hole_area),
                      "large": large_hole},
            "elapsed_ms": round((time.perf_counter() - tick) * 1000, 2),
        })
    return {"parameter_version": VERSION, "margin": margin, "polygons": output,
            "analysis_size": [width, height], "coordinate_space": "analysis_pixel",
            "total_regions": len(regions), "processed_regions": len(output),
            "unprocessed_regions": max(0, len(regions) - len(output)),
            "truncated": len(regions) > limit,
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 2)}


def bind_auto_mask(result, specs, *, preview_grid=None, source_windows=None):
    """Attach source identity and grid->source affine transforms for BOTH sides.

    Pre-cropped browser PNGs must declare their full grid and crop origin.
    Missing/invalid transforms disable drawing rather than guessing coordinates.
    """
    auto = result.get("autoMask")
    if auto is None:
        return
    started = time.perf_counter()
    g = result["analysisGrid"]
    transforms = []
    for side, spec in zip(("A", "B"), specs):
        meta = result["image" + side]
        sw, sh = meta.get("sourceWidth"), meta.get("sourceHeight")
        if any(v is not None and (not isinstance(v, (int, float)) or not np.isfinite(v)
                                 or int(v) != v or v <= 0 or v > 10_000_000) for v in (sw, sh)):
            raise ValueError("invalid source dimensions for autoMask")
        transform = None
        if sw and sh:
            sw, sh = int(sw), int(sh)
            if g.get("mode") == "source":
                aw, ah = g["imageASource"]
                ox, oy, rw, rh = g["sourceRoi"]
                if source_windows:
                    tx, ty, right, bottom = source_windows[0 if side == "A" else 1]
                    rw, rh = right - tx, bottom - ty
                    sx = (rw - 1) / (g["width"] - 1) if g["width"] > 1 else 0
                    sy = (rh - 1) / (g["height"] - 1) if g["height"] > 1 else 0
                else:
                    sx = (rw - 1) / (g["width"] - 1) * sw / aw if g["width"] > 1 else 0
                    sy = (rh - 1) / (g["height"] - 1) * sh / ah if g["height"] > 1 else 0
                    tx, ty = ox * sw / aw, oy * sh / ah
            else:
                pg = preview_grid or {"width": meta["gridWidth"], "height": meta["gridHeight"], "x": 0, "y": 0}
                fw, fh = float(pg["width"]), float(pg["height"])
                ox, oy = float(pg.get("x", 0)), float(pg.get("y", 0))
                if not (np.isfinite([fw, fh, ox, oy]).all() and fw > 0 and fh > 0 and ox >= 0 and oy >= 0 and
                        ox + meta["gridWidth"] <= fw and oy + meta["gridHeight"] <= fh):
                    raise ValueError("invalid autoMaskGrid")
                sx, sy = sw / fw, sh / fh
                # sampleGray/_resize_pair use half-pixel centres, not cell edges.
                tx, ty = (ox + g["origin"][0] + .5) * sx - .5, (oy + g["origin"][1] + .5) * sy - .5
            transform = {"scale": [sx, sy], "offset": [tx, ty]}
        transforms.append({"side": side, "image_id": meta.get("id"),
                           "scene_id": spec.get("sceneId"), "source_revision": spec.get("sourceRevision"),
                           "pixel_key": spec.get("pixelKey"), "source_size": [sw, sh],
                           "transform": transform})
    from backend.services.repair_regions import consolidate
    consolidate(auto, transforms)
    auto["sources"] = transforms
    auto["analysis_id"] = hashlib.sha256(json.dumps({
        "mask": result["binaryMask"], "parameters": result["parameters"],
        "sources": transforms, "size": auto["analysis_size"], "margin": auto["margin"],
        "version": VERSION,
    }, sort_keys=True).encode()).hexdigest()[:24]
    mask = np.asarray(Image.open(io.BytesIO(base64.b64decode(result["binaryMask"].split(",", 1)[1])))) > 0
    labels, _ = label(mask, structure=np.ones((3, 3), bool))
    for polygon in auto["polygons"]:
        polygon["sources"] = transforms
        seed_x, seed_y = polygon["analysis_seed"]
        target_labels = [labels[y, x] for x, y in polygon.get("analysis_seeds", [[seed_x, seed_y]])]
        # Actual source rasterizer recheck, not a promise that affine rounding
        # cannot affect coverage. Only source pixels corresponding to original
        # analysis pixel centres are asserted (not unobserved sub-grid defects).
        source_polygons = []
        for source in transforms:
            t = source["transform"]
            if not t:
                continue
            sx, sy = t["scale"]; ox, oy = t["offset"]
            sw, sh = source["source_size"]
            points = [[max(0, min(int(sw) - 1, int(np.floor(x*sx + ox + .5)))),
                       max(0, min(int(sh) - 1, int(np.floor(y*sy + oy + .5))))]
                      for x, y in polygon["points"]]
            xmin = min(p[0] for p in points); xmax = max(p[0] for p in points)
            # Allocate one source scanline, never a full source mask.
            covered = total = 0
            for y in range(mask.shape[0]):
                xs = np.flatnonzero(labels[y] == target_labels[0] if len(target_labels) == 1
                                    else np.isin(labels[y], target_labels))
                if not xs.size:
                    continue
                source_y = max(0, min(int(sh) - 1, int(np.floor(y*sy + oy + .5))))
                row = rasterize_polygons(xmax - xmin + 1, 1,
                    [[[x - xmin, yy - source_y] for x, yy in points]])[0]
                sample_x = np.clip(np.floor(xs*sx + ox + .5).astype(np.int64), 0, sw-1) - xmin
                inside = (sample_x >= 0) & (sample_x < len(row))
                covered += int(row[sample_x[inside]].sum()); total += len(xs)
            area2 = sum(points[i][0]*points[(i+1) % len(points)][1] - points[(i+1) % len(points)][0]*points[i][1]
                        for i in range(len(points)))
            source_polygons.append({"image_id": source["image_id"], "points": points,
                                    "coordinate_space": "source_pixel", "coverage_ratio": covered / total,
                                    "drawable": abs(area2) > 0, "coverage_scope": "projected_analysis_pixel_centres"})
        polygon["source_polygons"] = source_polygons
        if any(not p["drawable"] or p["coverage_ratio"] != 1 for p in source_polygons):
            polygon["needs_review"] = True
            polygon["diagnostics"].append("source_projection_conflict")
    auto["projection_elapsed_ms"] = round((time.perf_counter() - started) * 1000, 2)
    auto["elapsed_ms"] = round(auto["elapsed_ms"] + auto["projection_elapsed_ms"], 2)
