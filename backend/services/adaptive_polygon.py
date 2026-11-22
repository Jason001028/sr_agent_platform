"""Deterministic low-vertex convex enclosures on a local pixel-centre grid."""
from dataclasses import dataclass
from itertools import product

import numpy as np
from scipy.ndimage import distance_transform_edt
from scipy.spatial import ConvexHull, QhullError

POLICY = "adaptive-4to6-v1"
LEGACY = "repair-4to6-soft2pct-v1"


@dataclass(frozen=True)
class GeometryConfig:
    max_vertices: int = 6
    directions: int = 48
    gains: tuple = (.10, .08)
    vertex_cost: float = .015
    shape_cost: float = .025
    excess_review: float = .8
    adjustments: tuple = (0., .5, 1., 1.5)
    max_pixels: int = 4_500_000
    max_projection_pixels: int = 24_000_000


CONFIG = GeometryConfig()


def margin_radius(count, width, height, margin):
    base = min(max(1., .05 * np.sqrt(count)), 20., max(1., .10 * min(width, height)))
    return max(1., min(20., base * {"tight": .75, "standard": 1., "loose": 1.5}[margin]))


def geometry_policy(value):
    if value is None:
        return LEGACY
    if value not in (LEGACY, POLICY):
        raise ValueError("unknown autoMaskGeometryPolicy")
    return value


def area(poly):
    return abs(float(np.dot(poly[:, 0], np.roll(poly[:, 1], -1))
                     - np.dot(poly[:, 1], np.roll(poly[:, 0], -1)))) / 2


def canonical_polygon(points):
    """Remove quantization duplicates/collinear vertices and recheck convexity."""
    poly = np.asarray(points, dtype=np.int64)
    old = np.roll(poly, 1, axis=0)
    poly = poly[np.any(poly != old, axis=1)]
    while len(poly) >= 3:
        delta = np.roll(poly, -1, axis=0) - poly
        previous = np.roll(delta, 1, axis=0)
        cross = previous[:, 0] * delta[:, 1] - previous[:, 1] * delta[:, 0]
        if np.any(cross == 0):
            poly = poly[cross != 0]
            continue
        if (not (np.all(cross > 0) or np.all(cross < 0))
                or len(np.unique(poly, axis=0)) != len(poly)):
            return []
        if cross[0] < 0:
            poly = poly[::-1]
        # Equal turn signs alone can also describe a self-crossing ordering.
        edges = np.roll(poly, -1, axis=0) - poly
        offsets = poly[None, :, :] - poly[:, None, :]
        if np.any(edges[:, None, 0]*offsets[:, :, 1] - edges[:, None, 1]*offsets[:, :, 0] < 0):
            return []
        first = np.lexsort((poly[:, 0], poly[:, 1]))[0]
        return np.roll(poly, -first, axis=0).tolist()
    return []


def clip_convex_polygon(poly, normal, limit):
    clipped = []
    prior = poly[-1]
    previous = prior @ normal - limit
    for point in poly:
        distance = point @ normal - limit
        if (distance > 1e-9) != (previous > 1e-9):
            clipped.append(prior + (point - prior) * previous / (previous - distance))
        if distance <= 1e-9:
            clipped.append(point)
        prior, previous = point, distance
    return np.asarray(clipped)


def _hull(mask):
    ys = np.flatnonzero(mask.any(axis=1))
    rows = mask[ys]
    left, right = rows.argmax(axis=1), mask.shape[1] - 1 - rows[:, ::-1].argmax(axis=1)
    extreme = np.concatenate((np.column_stack((left, ys)), np.column_stack((right, ys))))
    extreme = np.unique(extreme, axis=0)
    try:
        if len(extreme) > 2:
            extreme = extreme[ConvexHull(extreme).vertices]
    except QhullError:
        extreme = extreme[[0, -1]]
    # Each input pixel represents a unit square centred at its integer index.
    corners = (extreme[:, None, :] + np.array(list(product((-.5, .5), repeat=2)))[None]).reshape(-1, 2)
    return corners[ConvexHull(corners).vertices]


def _directions(hull, limit):
    edges = np.roll(hull, -1, axis=0) - hull
    angles = np.unique(np.round(np.r_[0., np.arctan2(edges[:, 0], -edges[:, 1]) % (2*np.pi)], 10))
    if len(angles) > limit:
        angles = angles[np.linspace(0, len(angles)-1, limit, dtype=int)]
    return angles


def _rectangle(hull, angles, padding):
    best = None
    for theta in np.unique(np.round(angles % (np.pi / 2), 10)):
        u = np.array([np.cos(theta), np.sin(theta)])
        v = np.array([-u[1], u[0]])
        axes = np.column_stack((u, v))
        projection = hull @ axes
        lo, hi = projection.min(axis=0) - padding, projection.max(axis=0) + padding
        key = (round(float(np.prod(hi - lo)), 6), min(theta, np.pi / 2 - theta), theta)
        if best is None or key < best[0]:
            best = key, np.array([lo, [hi[0], lo[1]], hi, [lo[0], hi[1]]]) @ axes.T
    return best[1], float(np.degrees(best[0][2]))


def best_supporting_cut(poly, hull, angles, padding, vertices):
    options = []
    for theta in angles:
        normal = np.array([np.cos(theta), np.sin(theta)])
        clipped = clip_convex_polygon(poly, normal, float((hull @ normal).max()) + padding)
        if len(clipped) == vertices and area(clipped) + 1e-7 < area(poly):
            options.append((area(clipped), theta, clipped))
    return [entry[2] for entry in sorted(options, key=lambda entry: entry[:2])[:3]]


def _penalty(points):
    poly = np.asarray(points, dtype=float)
    edges = np.roll(poly, -1, axis=0) - poly
    lengths = np.linalg.norm(edges, axis=1)
    unit = edges / lengths[:, None]
    angles = np.degrees(np.arccos(np.clip(-(unit * np.roll(unit, 1, axis=0)).sum(axis=1), -1, 1)))
    return max(0., 2 - lengths.min()) + max(0., 30 - angles.min()) / 30


def convex_spans(points, rows):
    """Inclusive integer spans used by the browser's adaptive TIFF export."""
    left = np.full(len(rows), np.inf)
    right = np.full(len(rows), -np.inf)
    for (x, y), (xx, yy) in zip(points, points[1:] + points[:1]):
        active = (rows >= min(y, yy)) & (rows <= max(y, yy))
        if yy == y:
            left[active] = np.minimum(left[active], min(x, xx))
            right[active] = np.maximum(right[active], max(x, xx))
        else:
            edge = x + (rows[active] - y) * (xx - x) / (yy - y)
            left[active] = np.minimum(left[active], edge)
            right[active] = np.maximum(right[active], edge)
    return np.floor(left + .5).astype(int), np.ceil(right - .5).astype(int)


def verify_export(points, source_height, rasterize, *, config=CONFIG):
    xmin, xmax = min(p[0] for p in points), max(p[0] for p in points)
    ymin, ymax = min(p[1] for p in points), max(p[1] for p in points)
    width, height = xmax - xmin + 1, ymax - ymin + 1
    if width * height > config.max_projection_pixels:
        return "source_export_budget"
    stripe = max(1, min(128, 524288 // width))
    for y in range(ymin, ymax+1, stripe):
        count = min(stripe, ymax-y+1)
        rows = np.arange(y, y+count)
        left, right = convex_spans(points, rows)
        columns = np.arange(xmin, xmax+1)
        expected = (columns >= left[:, None]) & (columns <= right[:, None])
        guard = int(y+count < source_height)
        actual = rasterize(width, count+guard, [[[x-xmin, yy-y] for x, yy in points]])[:count]
        if not np.array_equal(actual, expected):
            return "source_export_raster_conflict"
    return None


def enclose(target_mask, radius, measure, *, config=CONFIG, exclusion_mask=None):
    """Return measured alternatives. Only passing 4–6 vertex masks are eligible."""
    target = np.asarray(target_mask, dtype=bool)
    exclusion = None if exclusion_mask is None else np.asarray(exclusion_mask, dtype=bool)
    if exclusion is not None:
        if exclusion.shape != target.shape:
            raise ValueError("exclusion mask must match the target grid")
        if (exclusion & target).any():
            return [], 0
    count = int(target.sum())
    if not count or target.size > config.max_pixels or min(target.shape) < 2:
        return [], 0
    distance = distance_transform_edt(~target) if radius > 0 else None
    tested = 0
    for buffer in dict.fromkeys((radius, 0.)):
        safe = distance <= buffer if buffer else target
        safe_count = int(safe.sum())
        hull = _hull(safe)
        angles = _directions(hull, config.directions)
        evaluated = {}
        for adjustment in config.adjustments:
            rect, rotation = _rectangle(hull, angles, adjustment)
            fives = best_supporting_cut(rect, hull, angles, adjustment, 5) if config.max_vertices >= 5 else []
            sixes = best_supporting_cut(fives[0], hull, angles, adjustment, 6) if fives and config.max_vertices >= 6 else []
            for poly in [rect, *fives, *sixes]:
                # Clip to the local legal domain before quantization.
                for normal, bound in (([1, 0], target.shape[1]-1), ([-1, 0], 0),
                                      ([0, 1], target.shape[0]-1), ([0, -1], 0)):
                    poly = clip_convex_polygon(poly, np.asarray(normal), bound)
                    if len(poly) < 3:
                        break
                if len(poly) < 3:
                    continue
                points = canonical_polygon(np.floor(poly + .5))
                if not 4 <= len(points) <= config.max_vertices or tuple(map(tuple, points)) in evaluated:
                    continue
                tested += 1
                coverage, footprint, ratio = measure(points, target)
                if coverage != 1 or footprint != 1:
                    continue
                if exclusion is not None:
                    from backend.services.mask import rasterize_polygons

                    if rasterize_polygons(target.shape[1], target.shape[0], [points])[exclusion].any():
                        continue
                margin_coverage = coverage if not buffer else measure(points, safe)[0]
                pixels = round(ratio * count)
                entry = {"points": points, "vertex_count": len(points),
                         "rotation_deg": rotation, "coverage_ratio": coverage,
                         "supersample_coverage_ratio": footprint, "area_ratio": ratio,
                         "polygon_area_px": pixels, "target_area_px": count,
                         "excess_area_ratio": (pixels - count) / count,
                         "margin_coverage_ratio": margin_coverage, "buffer_px": buffer,
                         "score": max(0, pixels - safe_count) / safe_count
                                  + config.vertex_cost * (len(points) - 4)
                                  + config.shape_cost * _penalty(points)}
                evaluated[tuple(map(tuple, points))] = entry
        if evaluated:
            options = sorted(evaluated.values(), key=lambda e: (
                e["margin_coverage_ratio"] != 1, e["score"], e["vertex_count"], e["points"]))
            comparable = [e for e in options if e["margin_coverage_ratio"] == options[0]["margin_coverage_ratio"]]
            baseline = next((e for e in comparable if e["vertex_count"] == 4), None)
            picked = baseline or comparable[0]
            for vertices, gain in zip((5, 6), config.gains):
                option = next((e for e in comparable if e["vertex_count"] == vertices), None)
                if option and option["score"] < picked["score"]:
                    extra = max(1, picked["polygon_area_px"] - safe_count)
                    if (picked["polygon_area_px"] - option["polygon_area_px"]) / extra >= gain:
                        picked = option
            for option in options:
                option["optimization_gain"] = (max(0, baseline["polygon_area_px"] - option["polygon_area_px"])
                                               / baseline["polygon_area_px"] if baseline else 0.)
            return [picked, *(e for e in options if e is not picked)], tested
    return [], tested
