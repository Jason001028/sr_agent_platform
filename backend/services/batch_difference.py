"""Batch adapter to the SAME difference/geometry core as /api/diff/analyze.

Reads stripped TIFFs through the existing bounded reader. Only two common-grid
float arrays are retained; raw source buffers are limited by window_rows.
No viewer PNGs, full-resolution arrays, files or SR submissions here.
"""
from pathlib import Path
import time

import numpy as np

from backend.services import image_diff, batch_contracts as contracts, preview_jpg
from backend.services.auto_mask_geometry import bind_auto_mask
from backend.services.tour_identity import image_identity
from backend.services.spatial_identity import resource_identity

# Same relative aspect tolerance as frontend/lib/flicker.ts and tourCompare.ts.
# Production PAN/NOSR resolutions are not necessarily integer multiples.
PAN_NOSR_ASPECT_TOLERANCE = 0.01


def _check_grid_aspect(width, height, rw, rh):
    # Rounding either axis during production resampling must not reject a pair.
    cross_pan, cross_nosr = width * rh, height * rw
    deviation = abs(cross_pan - cross_nosr) / max(cross_pan, cross_nosr)
    if deviation > PAN_NOSR_ASPECT_TOLERANCE:
        raise contracts.DetectionError(
            f"PAN/NOSR 网格比例不一致：PAN {width}×{height}，NOSR {rw}×{rh}，"
            f"宽高比偏差 {deviation:.3%}，超过 {PAN_NOSR_ASPECT_TOLERANCE:.0%} 容差")


def sample_grid(path, width, height, shape, config):
    from backend.services import batch_detector as reader
    gh, gw = shape
    layout = reader._read_strip_layout(path, width, height)
    rows = min(reader._window_rows(config, width), max(1, (1 << 22) // width))
    fy = np.clip((np.arange(gh) + .5) * height / gh - .5, 0, height-1)
    fx = np.clip((np.arange(gw) + .5) * width / gw - .5, 0, width-1)
    if config.get("categorical"):
        # Scene labels must not bleed across boundaries through bilinear DN sampling.
        fy, fx = np.floor(fy + .5), np.floor(fx + .5)
    y0, x0 = np.floor(fy).astype(int), np.floor(fx).astype(int)
    y1, x1 = np.minimum(y0+1, height-1), np.minimum(x0+1, width-1)
    wx, wy = fx-x0, fy-y0
    samples = [np.empty((gh, gw), np.float32), np.empty((gh, gw), np.float32)]
    validity = [np.empty((gh, gw), bool), np.empty((gh, gw), bool)]
    start = 0
    for block in reader._iter_windows(path, layout, width, height, rows):
        if config.get("cancel_check"):
            config["cancel_check"]()
        end = start + len(block)
        for n, desired in enumerate((y0, y1)):
            indexes = np.flatnonzero((desired >= start) & (desired < end))
            if not len(indexes):
                continue
            left = block[(desired[indexes]-start)[:, None], x0].astype(np.float32)
            right = block[(desired[indexes]-start)[:, None], x1].astype(np.float32)
            lv, rv = np.isfinite(left), np.isfinite(right)
            nd = layout.get("nodata")
            if nd is not None and np.isfinite(nd):
                lv &= left != nd; rv &= right != nd
            valid = lv & (rv | (wx == 0))
            samples[n][indexes] = np.where(valid, np.nan_to_num(left)*(1-wx) + np.nan_to_num(right)*wx, 0)
            validity[n][indexes] = valid
        start = end
    if start != height:
        raise contracts.DetectionError("源 TIFF 行数与头信息不一致")
    valid = validity[0] & (validity[1] | (wy[:, None] == 0))
    result = samples[0]*(1-wy[:, None]) + samples[1]*wy[:, None]
    return np.where(valid, result, 0).astype(np.float32), valid


def validate_pair(target, reference):
    from backend.services import batch_detector as reader
    width, height = contracts.image_size(target)
    rw, rh = contracts.image_size(reference)
    pan, pi = reader._probe_image(target, "PAN", width, height)
    nosr, ni = reader._probe_image(reference, "NOSR", rw, rh)
    reader._check_reference_grid(pi, ni, width, height)
    if pan.parent.resolve() != nosr.parent.resolve():
        raise contracts.DetectionError("PAN/NOSR 不属于同一生产场景")
    _check_grid_aspect(width, height, rw, rh)
    identities = [image_identity(p, pan.parent.as_posix(), role) for p, role in ((pan,"pan"),(nosr,"nosr"))]
    for frozen, actual in zip((target, reference), identities):
        old = frozen.get("tourIdentity") or {}
        if old and (old.get("role") != actual["role"] or old.get("sequence") != actual["sequence"]):
            raise contracts.DetectionError("冻结的 PAN/NOSR 配对身份不一致")
        if not frozen.get("revision") or actual["sourceRevision"] != frozen["revision"]:
            raise contracts.DetectionError("PAN/NOSR 缺少冻结版本或版本已变化")
        if resource_identity(Path(frozen["path"]))["image_key"]!=frozen.get("image_key"):
            raise contracts.DetectionError("PAN/NOSR 资源身份与冻结输入不一致")
    x, y = [i.get("registration") for i in identities]
    if bool(x) != bool(y):
        raise contracts.DetectionError("PAN/NOSR 配准信息不对称，需人工核对")
    if x and (x["crs"] != y["crs"] or x["orientation"] != y["orientation"]
              or not np.allclose(x["extent"], y["extent"], rtol=0, atol=1e-8)):
        raise contracts.DetectionError("PAN/NOSR 空间范围或方向不一致")
    # Existing production contract explicitly allows tagless same-sequence,
    # proportional PAN/NOSR grids; record that assumption, never invent CRS.
    return pan, nosr, (width,height), (rw,rh), bool(x)


def detect(target, reference, base, config):
    started = time.perf_counter()
    pan, nosr, (w,h), (rw,rh), registered = validate_pair(target, reference)
    max_edge = image_diff.resolve_max_edge(config.get("max_edge", 2048))
    if config.get("method") == "smart":
        max_edge = min(2048, max_edge)
    shape = image_diff.common_grid_shape((h,w),(rh,rw),max_edge)
    a, va = sample_grid(pan,w,h,shape,config)
    b, vb = sample_grid(nosr,rw,rh,shape,config)
    result = image_diff.analyze_arrays(a,b,valid_a=va,valid_b=vb,
        method=config.get("method","absdiff"), sensitivity=config.get("sensitivity","standard"),
        reference_side="B", smart_options=config.get("smart_options"),
        threshold=config.get("threshold"), min_area_px=config.get("min_area_px",64),
        blur=config.get("blur",True), open_kernel=config.get("open_kernel",3),
        close_kernel=config.get("close_kernel",5), source="source-grid",
        auto_mask=True, auto_mask_limit=512, auto_mask_margin=config.get("margin","standard"),
        scale_a=(w/shape[1], h/shape[0]), scale_b=(rw/shape[1], rh/shape[0]))
    gh, gw = shape
    for key, ref in (("imageA",target),("imageB",reference)):
        width,height=contracts.image_size(ref)
        result[key]={"id":ref["image_key"],"sourceWidth":width,"sourceHeight":height,"gridWidth":gw,"gridHeight":gh}
    result["analysisGrid"]["mode"]="preview"  # affine grid convention; input is REAL DN, not preview JPG
    bind_auto_mask(result, ({"sourceRevision":target["revision"]},{"sourceRevision":reference["revision"]}))
    auto=result["autoMask"]
    candidates=[]; rejected=[]
    for p in auto["polygons"]:
        source=next(s for s in p["source_polygons"] if s["image_id"]==target["image_key"])
        if p["coverage_ratio"] != 1 or p["supersample_coverage_ratio"] != 1 or source["coverage_ratio"] != 1 or not source["drawable"]:
            rejected.append({"region_id":p["region_id"],"reason":"coverage_or_degeneracy"}); continue
        candidates.append({"candidate_id":p["region_id"],"kind":"polygon","points":source["points"],
                           "label":"difference", "auto_mask":{k:p[k] for k in (
                               "rank","coverage_ratio","area_ratio","buffer_px","needs_review","holes","fallback",
                               "merged_regions","full_image_area_ratio","soft_min_area_ratio","soft_target_met")}})
    for ref in (target,reference):
        if (preview_jpg.source_revision(Path(ref["path"])) != ref["revision"]
                or resource_identity(Path(ref["path"]))["image_key"]!=ref["image_key"]):
            raise contracts.DetectionError("检测期间源影像被替换")
    counts={"total":auto["total_regions"],"processed":len(candidates),
            "unprocessed":auto["unprocessed_regions"]+len(rejected),"rejected":rejected,
            "region_budget":512,"budget_exhausted":auto["truncated"]}
    return contracts.validate_detection_result({**base,"provenance":"real","detector_name":"shared-difference",
        "detector_version":"1.0.0","candidates":candidates,"candidate_accounting":counts,
        "source_revision":target["revision"],
        "requires_geometry_review":config.get("method") == "smart" or any(p["needs_review"] for p in auto["polygons"]),
        "geometry_diagnostics":[{k:p[k] for k in ("region_id","vertex_count","coverage_ratio","supersample_coverage_ratio","buffer_px","buffer_space","needs_review","diagnostics","area_ratio","holes","fallback")} for p in auto["polygons"]],
        "analysis":{"source":"real-dn-grid","size":[gw,gh],"parameters":result["parameters"],
                    "grid_to_pan":auto["sources"][0]["transform"],"grid_to_nosr":auto["sources"][1]["transform"],
                    "coverage_scope":"projected_analysis_pixel_centres","downsampled":gw<w or gh<h,
                    "parameter_version":auto["parameter_version"],"registration":"geotiff" if registered else "production-proportional-grid",
                    "valid_ratio":result["coverage"]["validRatio"],"region_accounting":result["regionAccounting"],"elapsed_ms":round((time.perf_counter()-started)*1000,2),
                    **({"algorithmVersion":result["algorithmVersion"], "scoreSemantics":result["scoreSemantics"],
                        "regionDiagnostics":result["regionDiagnostics"], "maskAvailability":result["maskAvailability"],
                        "sensorProfile":result["sensorProfile"], "calibrationStatus":result["calibrationStatus"],
                        "analysisPrecision":result["analysisPrecision"], "filterReasons":result["filterReasons"],
                        "normalization":result["normalization"], "sourceIdentity":{"pan":target["image_key"], "nosr":reference["image_key"]}}
                       if config.get("method") == "smart" else {})},
        "diagnostics":[*base["diagnostics"],{"code":"candidate_accounting",**counts}]},mode=config.get("execution_mode"))
