"""Paired repair measurements on bounded pixel windows."""
from pathlib import Path

import numpy as np

from backend.services import evidence_raster

PROFILE_RADIUS = 6


def _edge_shape(profile, low, high):
    contrast = high - low
    normalized = (profile - low) / contrast
    crossing = []
    for level in (0.1, 0.9):
        indices = np.flatnonzero((normalized[:-1] < level) & (normalized[1:] >= level))
        if len(indices) != 1:
            return None
        i = indices[0]
        crossing.append(i + (level - normalized[i]) / (normalized[i + 1] - normalized[i]))
    if crossing[1] <= crossing[0]:
        return None
    halo = max(0.0, -float(normalized.min()), float(normalized.max()) - 1.0)
    return crossing[1] - crossing[0], halo


def _paired_edges(before, after, valid, inside, scale):
    records = []
    radius = PROFILE_RADIUS
    for old, new, ok, roi in ((before, after, valid, inside),
                               (before.T, after.T, valid.T, inside.T)):
        if old.shape[1] < 2 * radius + 1:
            continue
        diff = np.abs(np.diff(old, axis=1))
        core = diff[:, radius:-radius]
        if not core.size:
            continue
        peaks = ((core > 0.07 * scale)
                 & (core >= diff[:, radius-1:-radius-1])
                 & (core > diff[:, radius+1:1-radius]))
        ys, xs = np.nonzero(peaks & roi[:, radius:-radius-1])
        stride = max(1, (len(xs) + 1023) // 1024)
        for y, x in zip(ys[::stride], xs[::stride] + radius):
            sl = slice(x - radius, x + radius + 1)
            if not ok[y, sl].all():
                continue
            pair = [im[y, sl] for im in (old, new)]
            if pair[0][-1] < pair[0][0]:
                pair = [p[::-1] for p in pair]
            plateaus = [(float(np.median(p[:2])), float(np.median(p[-2:]))) for p in pair]
            if any(high - low < 0.25 * scale for low, high in plateaus):
                continue
            if any(max(np.ptp(p[:2]), np.ptp(p[-2:])) > 0.1 * scale for p in pair):
                continue
            shapes = [_edge_shape(p, *levels) for p, levels in zip(pair, plateaus)]
            if all(shape is not None for shape in shapes):
                records.append(shapes)
    return np.asarray(records)


def _metric(values):
    before, after = (float(value) for value in values)
    return {"before": before, "after": after, "delta": after - before}


def _alignment_shift(before, after, stable, scale):
    # Compare the same support for all shifts. A translated stable texture
    # must improve over the zero-offset score before it can veto the window.
    if min(before.shape) < 5:
        return None
    centre = stable[1:-1, 1:-1]
    support = centre.copy()
    for dy, dx in ((-1, 0), (1, 0), (0, -1), (0, 1)):
        support &= stable[1+dy:before.shape[0]-1+dy, 1+dx:before.shape[1]-1+dx]
    samples = before[1:-1, 1:-1][support]
    if samples.size < 32 or np.ptp(samples) < 0.05 * scale:
        return None
    scores = {}
    for dy, dx in ((0, 0), (-1, 0), (1, 0), (0, -1), (0, 1)):
        aligned = after[1+dy:before.shape[0]-1+dy, 1+dx:before.shape[1]-1+dx][support]
        scores[(dy, dx)] = float(np.mean(np.abs(samples - aligned)))
    shift = min(scores, key=scores.get)
    if scores[shift] < 0.8 * scores[(0, 0)] and scores[(0, 0)] > 0.01 * scale:
        return shift
    return (0, 0)


def _flat_metrics(before, after, valid, inside, scale):
    if min(before.shape) < 3:
        return {}
    centre = valid[1:-1, 1:-1] & inside[1:-1, 1:-1]
    offsets = ((slice(1, -1), slice(None, -2)), (slice(1, -1), slice(2, None)),
               (slice(None, -2), slice(1, -1)), (slice(2, None), slice(1, -1)))
    for sl in offsets:
        centre &= valid[sl] & inside[sl]
    old_neighbours = np.stack([before[sl] for sl in offsets])
    # Coarse texture gate retains alternating stripes while rejecting edges.
    flat = centre & (np.minimum(
        np.abs(old_neighbours[0] - old_neighbours[1]),
        np.abs(old_neighbours[2] - old_neighbours[3])) <= 0.08 * scale)
    if flat.sum() < 32:
        return {}
    residuals = [im[1:-1, 1:-1] - np.mean([im[sl] for sl in offsets], axis=0)
                 for im in (before, after)]
    noise = [np.median(np.abs(r[flat] - np.median(r[flat]))) for r in residuals]
    out = {"hf_mad_dn": _metric(noise)}
    for axis, name in ((1, "row_banding_dn"), (0, "column_banding_dn")):
        flags = flat if axis == 1 else flat.T
        usable = np.flatnonzero(flags.sum(axis=1) >= 8)
        if len(usable) >= 4:
            amplitudes = []
            for r in residuals:
                lines = r if axis == 1 else r.T
                sequence = [np.median(lines[i, flags[i]]) for i in usable]
                amplitudes.append(np.percentile(sequence, 90) - np.percentile(sequence, 10))
            out[name] = {**_metric(amplitudes), "lines": int(len(usable))}
    return out


def _measure(before, after, valid, inside, region_inside):
    common = valid & region_inside
    if common.sum() < 16:
        return {"evidence_gap": "insufficient_valid_pairs"}
    equal = np.array_equal(before[common], after[common])
    low, high = np.percentile(before[common], [1, 99])
    scale = max(float(high - low), 1.0)
    shift = _alignment_shift(before, after, valid & ~inside, scale)
    if shift and shift != (0, 0):
        return {"evidence_gap": "registration_shift", "shift_yx": list(shift)}
    result = {"valid_pixels": int(common.sum()), "evidence_gaps": []}
    if shift is None:
        result["evidence_gaps"].append("stable_texture_unavailable")
    metrics = result["metrics"] = _flat_metrics(before, after, valid, region_inside, scale)
    edges = _paired_edges(before, after, valid, region_inside, scale)
    result["matched_edges"] = len(edges)
    if len(edges) >= 3:
        widths, halos = np.median(edges, axis=0).T
        metrics["edge_width_px"] = _metric(widths)
        metrics["edge_halo_fraction"] = _metric(halos)
    else:
        result["evidence_gaps"].append("insufficient_matched_edges")
    gradients = []
    seams = []
    for image in (before, after):
        pairs = []
        borders = []
        for data, sample, mask, roi in ((image, valid, inside, region_inside),
                                         (image.T, valid.T, inside.T, region_inside.T)):
            delta = np.abs(np.diff(data, axis=1))
            readable = sample[:, :-1] & sample[:, 1:]
            pairs.append(delta[readable & roi[:, :-1] & roi[:, 1:]])
            borders.append(delta[readable & (mask[:, :-1] != mask[:, 1:])
                                 & (roi[:, :-1] | roi[:, 1:])])
        gradients.append(np.concatenate(pairs))
        seams.append(np.concatenate(borders))
    if gradients[0].size:
        result["gradient_p98"] = _metric([np.percentile(g, 98) for g in gradients])
    if seams[0].size >= 8:
        metrics["mask_boundary_jump_dn"] = _metric([np.median(v) for v in seams])
    result["conclusion"] = "unchanged" if equal and all(
        value["delta"] == 0 for value in metrics.values()) else "insufficient_evidence"
    if result["conclusion"] != "unchanged":
        result["evidence_gap"] = "uncalibrated_metrics"
    return result


def assess_repair_effect(item_id, job_id, binding):
    if str(job_id) != binding["job_id"]:
        raise ValueError("effect assessment Job mismatch")
    report = {
        "version": "paired-dn.v2", "item_id": item_id, "job_id": str(job_id),
        "mask_sha256": binding["mask"]["sha256"], "status": "measured",
        "regions": [], "calibration": "uncalibrated", "counts": {},
    }
    for candidate in binding["regions"]:
        box = candidate.get("bbox")
        if not box and candidate.get("points"):
            xs, ys = zip(*candidate["points"])
            box = [min(xs), min(ys), max(xs), max(ys)]
        region = {"region_id": candidate["candidate_id"], "bbox": box}
        report["regions"].append(region)
        if not box or len(box) != 4:
            region["evidence_gap"] = "missing_window"
            continue
        x0, y0, x1, y1 = box
        pad = PROFILE_RADIUS + 2
        window = (x0 - pad, y0 - pad, x1 + pad + 1, y1 + pad + 1)
        try:
            before, valid_before, meta = evidence_raster.read_window(
                Path(binding["backup"]["path"]), window)
            after, valid_after, after_meta = evidence_raster.read_window(
                Path(binding["output"]["path"]), window)
            mask, valid_mask, _ = evidence_raster.read_window(
                Path(binding["mask"]["path"]), window)
            inside = valid_mask & (mask > 0)
            left, top = meta["actual_window"][:2]
            crop = np.zeros(inside.shape, dtype=bool)
            crop[y0-top:y1-top+1, x0-left:x1-left+1] = True
            if candidate.get("points"):
                from PIL import Image, ImageDraw

                polygon = Image.new("1", (inside.shape[1], inside.shape[0]))
                ImageDraw.Draw(polygon).polygon(
                    [(x-left, y-top) for x, y in candidate["points"]], fill=1)
                crop &= np.asarray(polygon, dtype=bool)
            if meta["saturation_dn"] is not None:
                valid_before &= before < meta["saturation_dn"]
            if after_meta["saturation_dn"] is not None:
                valid_after &= after < after_meta["saturation_dn"]
            region.update(_measure(before, after, valid_before & valid_after,
                                   inside, inside & crop))
        except (ValueError, OSError) as exc:
            region["evidence_gap"] = str(exc)
    for region in report["regions"]:
        key = region.get("conclusion", "insufficient_evidence")
        report["counts"][key] = report["counts"].get(key, 0) + 1
    if not any("metrics" in region for region in report["regions"]):
        report["status"] = "blocked"
        report["evidence_gap"] = ("no_measurable_region" if report["regions"]
                                  else "missing_region_geometry")
    return report
