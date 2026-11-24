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

from backend.services import adaptive_polygon
from backend.services.mask import rasterize_polygons

VERSION = "auto-diff-v2-repair"
MAX_REGIONS = 16
MAX_BATCH_REGIONS = 2048


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


def build_auto_mask(labels, regions, *, margin="standard", limit=MAX_REGIONS, policy=None):
    """One polygon per *sorted independent label*, never a mixed mask contour.

    Callers supply regions with label/x/y/w/h/area attributes. Coordinates in
    returned points are local analysis-grid pixel centres. Source transforms
    and immutable input identities are bound by the API after input preparation.
    """
    from backend.services.image_diff import _trace_outer, _rdp

    if margin not in ("tight", "standard", "loose"):
        raise ValueError("autoMaskMargin must be tight, standard or loose")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_BATCH_REGIONS:
        raise ValueError(f"auto mask region limit must be in 1..{MAX_BATCH_REGIONS}")
    policy = adaptive_polygon.geometry_policy(policy)
    if policy == adaptive_polygon.POLICY:
        return _build_adaptive(labels, regions, margin, limit)
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


def _build_adaptive(labels, regions, margin, limit):
    from backend.services.repair_regions import group_regions

    start = time.perf_counter()
    h, w = labels.shape
    output = []

    extra_regions = limit - min(limit, len(regions))
    small_fits = {}

    def fit(members, partition=None):
        x0 = max(0, min(r.x for r in members) - 24)
        y0 = max(0, min(r.y for r in members) - 24)
        x1 = min(w, max(r.x + r.w for r in members) + 24)
        y1 = min(h, max(r.y + r.h for r in members) + 24)
        target = np.isin(labels[y0:y1, x0:x1], [r.label for r in members])
        if partition:
            left, top, right, bottom = partition
            target[:, :max(0, left-x0)] = False
            target[:, max(0, right-x0):] = False
            target[:max(0, top-y0)] = False
            target[max(0, bottom-y0):] = False
        count = int(target.sum())
        target_width = max(r.x+r.w for r in members) - min(r.x for r in members)
        target_height = max(r.y+r.h for r in members) - min(r.y for r in members)
        radius = adaptive_polygon.margin_radius(count, target_width, target_height, margin)
        # Equal local bitmaps have the same legal bounds and greedy solution.
        # Keep the cache local and bounded; source coordinates are translated on read.
        key = (target.shape, radius, np.packbits(target).tobytes()) if target.size <= 4096 else None
        fit_result = small_fits.get(key)
        if fit_result is None:
            fit_result = adaptive_polygon.enclose(target, radius, _measure)
            if not fit_result[0] and count:
                rows = np.flatnonzero(target.any(axis=1))
                columns = np.flatnonzero(target.any(axis=0))
                top, bottom = map(int, rows[[0, -1]])
                left, right = map(int, columns[[0, -1]])
                area = (right-left+1)*(bottom-top+1)
                if right > left and bottom > top and area <= count * (1 + adaptive_polygon.CONFIG.excess_review):
                    points = [[left, top], [right, top], [right, bottom], [left, bottom]]
                    coverage, footprint, ratio = _measure(points, target)
                    if coverage == footprint == 1:
                        fit_result = ([{"points": points, "vertex_count": 4,
                            "coverage_ratio": coverage, "supersample_coverage_ratio": footprint,
                            "margin_coverage_ratio": 1., "area_ratio": ratio, "excess_area_ratio": ratio-1.,
                            "polygon_area_px": area, "target_area_px": count, "buffer_px": 0.,
                            "fallback": "verified_analysis_rectangle"}], fit_result[1]+1)
            if key is not None and len(small_fits) < 256:
                small_fits[key] = fit_result
        options, tested = fit_result
        translated = [{**option, "points": [[x+x0, y+y0] for x, y in option["points"]]} for option in options]
        return translated, tested, count

    for group in group_regions(labels, regions[:limit]):
        tick = time.perf_counter()
        options, tested, count = fit(group)
        proposals = [(group, options, tested, count, None)]
        if len(group) > 1:
            singles = [([r], *fit([r]), None) for r in group]
            if (not options or all(parts for _, parts, _, _, _ in singles) and
                    options[0]["polygon_area_px"] > 1.15 * sum(parts[0]["polygon_area_px"]
                                                               for _, parts, _, _, _ in singles)):
                proposals = singles
        elif extra_regions and options and options[0]["excess_area_ratio"] > adaptive_polygon.CONFIG.excess_review:
            r = group[0]
            x, y, right, bottom = r.x, r.y, r.x+r.w, r.y+r.h
            mx, my = x+r.w//2, y+r.h//2
            splits = [((x, y, mx, bottom), (mx, y, right, bottom)),
                      ((x, y, right, my), (x, my, right, bottom))]
            if extra_regions >= 2 and min(r.w, r.h) >= 4:
                pixels = labels[y:bottom, x:right] == r.label
                row_change = np.diff(pixels.sum(axis=1))
                cut = y + int(np.argmax(np.abs(row_change))) + 1
                # The broad section retains one ROI; split the two narrow arms.
                if row_change[cut-y-1] > 0:
                    splits.append(((x, y, mx, cut), (mx, y, right, cut), (x, cut, right, bottom)))
                else:
                    splits.append(((x, y, right, cut), (x, cut, mx, bottom), (mx, cut, right, bottom)))
            alternatives = []
            for boxes in splits:
                parts = [(group, *fit(group, box), box) for box in boxes]
                if all(choices for _, choices, _, _, _ in parts):
                    cost = sum(choices[0]["polygon_area_px"] for _, choices, _, _, _ in parts)
                    alternatives.append((cost, parts))
            if alternatives:
                cost, parts = min(alternatives, key=lambda entry: entry[0])
                if cost < .85 * options[0]["polygon_area_px"]:
                    proposals = parts
                    extra_regions -= len(parts) - 1
        for members, options, attempts, count, partition in proposals:
            seeds = []
            digest = hashlib.sha256()
            for r in members:
                original = labels[r.y:r.y+r.h, r.x:r.x+r.w] == r.label
                if partition:
                    left, top, right, bottom = partition
                    crop = original[top-r.y:bottom-r.y, left-r.x:right-r.x]
                    y, x = np.unravel_index(crop.argmax(), crop.shape)
                    seeds.append([int(x+left), int(y+top)])
                    digest.update(repr(partition).encode())
                else:
                    y, x = np.unravel_index(original.argmax(), original.shape)
                    seeds.append([int(x+r.x), int(y+r.y)])
                digest.update(f"{r.x},{r.y},{r.w},{r.h}:".encode() + np.packbits(original).tobytes())
            best = options[0] if options else {"points": [], "vertex_count": 0,
                "coverage_ratio": 0., "supersample_coverage_ratio": 0., "area_ratio": None,
                "buffer_px": 0., "polygon_area_px": 0, "target_area_px": count}
            diagnostics = []
            if not options:
                diagnostics.append("adaptive_enclosure_failed")
            elif best["excess_area_ratio"] > adaptive_polygon.CONFIG.excess_review:
                diagnostics.append("adaptive_excess_area")
            if options and best["margin_coverage_ratio"] != 1:
                diagnostics.append("adaptive_margin_incomplete")
            output.append({**best, "region_id": digest.hexdigest()[:16], "rank": len(output)+1,
                "coordinate_space": "analysis_pixel", "analysis_size": [w, h],
                "analysis_seed": seeds[0], "analysis_seeds": seeds, "detected_area_px": count,
                "analysis_bounds": [min(r.x for r in members), min(r.y for r in members),
                                    max(r.x+r.w for r in members), max(r.y+r.h for r in members)],
                "geometry_method": "supporting_half_planes", "evaluated_candidates": attempts,
                "buffer_space": "analysis_pixel",
                "fallback": best.get("fallback") or ("margin_reduced" if options and not best["buffer_px"] else None),
                "needs_review": bool(diagnostics),
                "diagnostics": diagnostics, "drawable": bool(options),
                "holes": {"count": 0, "area_px": 0, "filled": False, "large": False},
                "elapsed_ms": round((time.perf_counter()-tick)*1000, 2)})
            if len(options) > 1:
                output[-1]["_projection_alternatives"] = [option["points"] for option in options[1:9]
                    if option["polygon_area_px"] <= 1.10 * best["polygon_area_px"]]
            if partition:
                output[-1]["analysis_partition"] = list(partition)
                r = members[0]
                yy, xx = np.unravel_index(original.argmax(), original.shape)
                output[-1]["analysis_member_seeds"] = [[int(r.x+xx), int(r.y+yy)]]
            output[-1].pop("score", None)
    return {"parameter_version": adaptive_polygon.POLICY, "geometry_policy": adaptive_polygon.POLICY,
            "margin": margin, "polygons": output, "analysis_size": [w, h],
            "coordinate_space": "analysis_pixel", "total_regions": len(regions),
            "processed_regions": min(len(regions), limit), "detected_regions": min(len(regions), limit),
            "unprocessed_regions": max(0, len(regions)-limit), "truncated": len(regions)>limit,
            "elapsed_ms": round((time.perf_counter()-start)*1000, 2)}


def _adaptive_source_polygon(polygon, alternatives, source, evidence, top, left):
    sx, sy = source["transform"]["scale"]
    ox, oy = source["transform"]["offset"]
    sw, sh = source["source_size"]

    def project(values, scale, offset, size):
        return np.clip(np.floor(values * scale + offset + .5).astype(np.int64), 0, size - 1)

    doc = {"image_id": source["image_id"], "points": [], "coordinate_space": "source_pixel",
           "coverage_ratio": 0., "drawable": False, "coverage_scope": "projected_analysis_pixel_centres"}
    evidence_rows = np.flatnonzero(evidence.any(axis=1))
    if not len(evidence_rows) or sx <= 0 or sy <= 0:
        return {**doc, "reason": "source_projection_empty"}
    sample_rows = project(evidence_rows + top, sy, oy, sh)
    seen = set()
    first_error = None

    def check(points):
        if len(points) < 4 or len(points) > 6:
            return "source_projection_degenerate"
        error = adaptive_polygon.verify_export(points, sh, rasterize_polygons)
        if error:
            return error
        # Export verification established that these spans match the TIFF rasterizer.
        ymin = min(p[1] for p in points)
        ymax = max(p[1] for p in points)
        if sample_rows[0] < ymin or sample_rows[-1] > ymax:
            return "source_projection_coverage"
        span_left, span_right = adaptive_polygon.convex_spans(points, sample_rows)
        for start in range(0, len(evidence_rows), 128):
            block_rows = evidence_rows[start:start+128]
            dy, dx = np.nonzero(evidence[block_rows])
            sample_x = project(dx + left, sx, ox, sw)
            if not np.all((sample_x >= span_left[start+dy]) & (sample_x <= span_right[start+dy])):
                return "source_projection_coverage"
        return None

    for index, candidate in enumerate((polygon["points"], *alternatives)):
        vertices = np.asarray(candidate)
        points = adaptive_polygon.canonical_polygon(np.column_stack((
            project(vertices[:, 0], sx, ox, sw), project(vertices[:, 1], sy, oy, sh))))
        key = tuple(map(tuple, points))
        if key in seen:
            continue
        seen.add(key)
        error = check(points)
        if not error:
            fallback = {"fallback": "verified_alternative"} if index else {}
            return {**doc, "points": points, "coverage_ratio": 1., "drawable": True, **fallback}
        if first_error is None:
            first_error = error
            doc["points"] = points

    # A tight raster rectangle is only eligible within the original fitted
    # footprint budget. Sparse or truly degenerate candidates stay blocked.
    columns = np.flatnonzero(evidence.any(axis=0))
    xmin, xmax = project(columns[[0, -1]] + left, sx, ox, sw).tolist()
    ymin, ymax = int(sample_rows[0]), int(sample_rows[-1])
    limit = polygon["polygon_area_px"] * max(1., sx * sy) * 1.10
    if xmax > xmin and ymax > ymin and (xmax-xmin+1)*(ymax-ymin+1) <= limit:
        rectangle = [[xmin, ymin], [xmax, ymin], [xmax, ymax], [xmin, ymax]]
        if not check(rectangle):
            return {**doc, "points": rectangle, "coverage_ratio": 1., "drawable": True,
                    "fallback": "verified_tight_rectangle"}
    return {**doc, "reason": first_error}


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
    adaptive = auto.get("geometry_policy") == adaptive_polygon.POLICY
    if not adaptive:
        consolidate(auto, transforms)
    auto["sources"] = transforms
    auto["analysis_id"] = hashlib.sha256(json.dumps({
        "mask": result["binaryMask"], "parameters": result["parameters"],
        "sources": transforms, "size": auto["analysis_size"], "margin": auto["margin"],
        "version": VERSION,
        **({"geometry_policy": adaptive_polygon.POLICY, "config": vars(adaptive_polygon.CONFIG),
            "source_projection_version": 2} if adaptive else {}),
    }, sort_keys=True).encode()).hexdigest()[:24]
    mask = np.asarray(Image.open(io.BytesIO(base64.b64decode(result["binaryMask"].split(",", 1)[1])))) > 0
    labels, _ = label(mask, structure=np.ones((3, 3), bool))
    for polygon in auto["polygons"]:
        polygon["sources"] = transforms
        alternatives = polygon.pop("_projection_alternatives", ())
        if adaptive and not polygon["drawable"]:
            polygon["source_polygons"] = []
            continue
        seed_x, seed_y = polygon["analysis_seed"]
        target_labels = [labels[y, x] for x, y in polygon.get("analysis_seeds", [[seed_x, seed_y]])]
        # Actual source rasterizer recheck, not a promise that affine rounding
        # cannot affect coverage. Only source pixels corresponding to original
        # analysis pixel centres are asserted (not unobserved sub-grid defects).
        source_polygons = []
        partition = polygon.get("analysis_partition", polygon.get("analysis_bounds"))
        left, top, right, bottom = partition or (0, 0, mask.shape[1], mask.shape[0])
        if adaptive:
            tile = labels[top:bottom, left:right]
            evidence = tile == target_labels[0] if len(target_labels) == 1 else np.isin(tile, target_labels)
        for source in transforms:
            t = source["transform"]
            if not t:
                continue
            if adaptive:
                projected = _adaptive_source_polygon(polygon, alternatives, source, evidence, top, left)
                source_polygons.append(projected)
                reason = projected.get("reason")
                if reason and reason not in polygon["diagnostics"]:
                    polygon["diagnostics"].append(reason)
                continue
            sx, sy = t["scale"]; ox, oy = t["offset"]
            sw, sh = source["source_size"]
            points = [[max(0, min(int(sw) - 1, int(np.floor(x*sx + ox + .5)))),
                       max(0, min(int(sh) - 1, int(np.floor(y*sy + oy + .5))))]
                      for x, y in polygon["points"]]
            xmin = min(p[0] for p in points); xmax = max(p[0] for p in points)
            # Allocate one source scanline, never a full source mask.
            covered = total = 0
            for y in range(top, bottom):
                label_window = labels[y, left:right]
                xs = left + np.flatnonzero(label_window == target_labels[0] if len(target_labels) == 1
                                           else np.isin(label_window, target_labels))
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
                                    "coordinate_space": "source_pixel", "coverage_ratio": covered / total if total else 0.,
                                    "drawable": abs(area2) > 0,
                                    "coverage_scope": "projected_analysis_pixel_centres"})
        polygon["source_polygons"] = source_polygons
        if adaptive:
            polygon["drawable"] = any(p["drawable"] for p in source_polygons)
        if any(not p["drawable"] or p["coverage_ratio"] != 1 for p in source_polygons):
            polygon["needs_review"] = True
            polygon["diagnostics"].append("source_projection_conflict")
    auto["projection_elapsed_ms"] = round((time.perf_counter() - started) * 1000, 2)
    auto["elapsed_ms"] = round(auto["elapsed_ms"] + auto["projection_elapsed_ms"], 2)
