"""Real geometry measurements, including official mask rasterizer cross-checks."""
import json

import numpy as np
import pytest
from scipy.ndimage import label

from backend.services import image_diff
from backend.services.auto_mask_geometry import build_auto_mask, bind_auto_mask
from backend.services.mask import rasterize_polygons


def shapes():
    y, x = np.mgrid[:180, :220]
    return {
        "L": ((x >= 25) & (x < 55) & (y >= 25) & (y < 140)) | ((x >= 25) & (x < 155) & (y >= 115) & (y < 140)),
        "crescent": ((x - 100)**2 + (y - 85)**2 < 60**2) & ((x - 122)**2 + (y - 73)**2 > 53**2),
        "scratch": (abs(y - .65*x - 10) < 3) & (x > 25) & (x < 170),
        "ellipse": ((x - 110)/65)**2 + ((y - 85)/38)**2 < 1,
        "ring": ((x - 100)**2 + (y - 85)**2 < 60**2) & ((x - 100)**2 + (y - 85)**2 > 45**2),
        "spot": (x >= 40) & (x <= 41) & (y >= 30) & (y <= 31),
        "edge": (x < 35) & (y < 32),
    }


def geometry(mask, margin="standard"):
    labels, regions = image_diff._regions(mask, mask.astype(float))
    regions = image_diff._pick_regions(regions, 1)
    return build_auto_mask(labels, regions, margin=margin), labels, regions


@pytest.mark.parametrize("name", list(shapes()))
def test_shapes_complete_coverage(name):
    mask = shapes()[name]
    auto, labels, regions = geometry(mask)
    assert len(auto["polygons"]) == len(regions)
    for p, region in zip(auto["polygons"], regions):
        official = rasterize_polygons(mask.shape[1], mask.shape[0], [p["points"]]).astype(bool)
        original = labels == region.label
        assert not (original & ~official).any()
        assert p["coverage_ratio"] == p["supersample_coverage_ratio"] == 1
        assert 3 <= p["vertex_count"] <= 24
        assert p["area_ratio"] <= p["area_ratio_limit"] or p["needs_review"]
        print(json.dumps({"shape": name, **{k: p[k] for k in (
            "vertex_count", "coverage_ratio", "area_ratio", "fallback", "needs_review", "elapsed_ms")}}))
    if name == "ring":
        assert auto["polygons"][0]["holes"]["large"]
        assert auto["polygons"][0]["needs_review"]


def test_separated_labels_sorted_and_top16():
    mask = np.zeros((220, 220), bool)
    for i in range(20):
        x, y = 10 + (i % 5)*40, 10 + (i // 5)*45
        mask[y:y+15, x:x+30] = True  # 10px gaps
    auto, _, regions = geometry(mask)
    assert auto["truncated"] and len(auto["polygons"]) == 16
    assert [p["rank"] for p in auto["polygons"]] == list(range(1, 17))
    assert len({p["region_id"] for p in auto["polygons"]}) == 16
    for p, r in zip(auto["polygons"], regions):
        assert p["coverage_ratio"] == 1
        assert min(x for x, y in p["points"]) <= r.x
    assert auto["polygons"][0]["region_id"] == geometry(mask)[0]["polygons"][0]["region_id"]


def test_vertex_area_conflict_is_explicit():
    mask = np.zeros((240, 240), bool)
    mask[20:220, 20:23] = True
    for y in range(20, 220, 8):
        mask[y:y+3, 20:220] = True
    auto, _, _ = geometry(mask)
    p = auto["polygons"][0]
    assert p["needs_review"] and "area_ratio_conflict" in p["diagnostics"]
    assert p["coverage_ratio"] == 1


def test_failed_simplification_falls_back_without_losing_pixels(monkeypatch):
    monkeypatch.setattr(image_diff, "_rdp", lambda points, eps: points[:2])
    auto, _, _ = geometry(shapes()["ellipse"])
    p = auto["polygons"][0]
    assert p["fallback"] in ("convex_hull", "convex_bbox")
    assert p["coverage_ratio"] == p["supersample_coverage_ratio"] == 1


@pytest.mark.parametrize("margin", ["tight", "standard", "loose"])
def test_margin_never_loses_coverage(margin):
    auto, _, _ = geometry(shapes()["scratch"], margin)
    assert all(p["coverage_ratio"] == p["supersample_coverage_ratio"] == 1 for p in auto["polygons"])


def test_optional_payload_and_crop_source_transform():
    a = np.zeros((80, 100), np.float32)
    b = a.copy(); b[20:50, 30:70] = 1
    spec = {"id": 3, "name": "PAN", "sourceWidth": 4000, "sourceHeight": 3200,
            "sceneId": "scene", "sourceRevision": "v1", "pixelKey": "snapshot"}
    payload = {"image_a": {**spec, "grayPng": image_diff._b64_png((a*255).astype('uint8'))},
               "image_b": {**spec, "id": 4, "grayPng": image_diff._b64_png((b*255).astype('uint8'))},
               "blur": False, "openKernel": 1, "closeKernel": 1}
    assert "autoMask" not in image_diff.analyze_payload(payload)
    result = image_diff.analyze_payload({**payload, "autoMask": True,
        "autoMaskGrid": {"width": 1000, "height": 800, "x": 100, "y": 200}})
    auto = result["autoMask"]
    assert auto["sources"][0]["transform"] == {"scale": [4, 4], "offset": [401.5, 801.5]}
    assert auto["sources"][0]["pixel_key"] == "snapshot"
    assert auto["analysis_id"]
    p = auto["polygons"][0]
    source_poly = p["source_polygons"][0]
    assert source_poly["coverage_ratio"] == 1 and source_poly["drawable"]
    full = rasterize_polygons(4000, 3200, [source_poly["points"]])
    yy, xx = np.nonzero(b)
    assert full[yy*4 + 802, xx*4 + 402].all()
    # Real ROI has different B source dimensions, offset is scaled too.
    result["analysisGrid"].update(mode="source", sourceRoi=[40, 50, 200, 160], imageASource=[4000, 3200])
    bind_auto_mask(result, (spec, spec))
    assert result["autoMask"]["sources"][0]["transform"] == {"scale": [199/99, 159/79], "offset": [40, 50]}
    result["imageB"].update(sourceWidth=2000, sourceHeight=1600)
    bind_auto_mask(result, (spec, spec), source_windows=([40, 50, 240, 210], [20, 25, 120, 105]))
    assert result["autoMask"]["sources"][1]["transform"] == {"scale": [1, 1], "offset": [20, 25]}


@pytest.mark.parametrize("size,offset", [([231, 177], [3, 5]), ([37, 33], [0, 0])])
def test_noninteger_source_mapping_actual_pil_coverage(size, offset):
    mask = shapes()["crescent"][:80, :100]
    result = image_diff.analyze_arrays(np.zeros(mask.shape), mask.astype(float),
        auto_mask=True, min_area_px=1, blur=False, open_kernel=1, close_kernel=1)
    for side in ("A", "B"):
        result["image" + side] = {"id": 1 if side == "A" else 2,
            "sourceWidth": size[0], "sourceHeight": size[1], "gridWidth": 100, "gridHeight": 80}
    bind_auto_mask(result, ({}, {}), preview_grid={"width": 110, "height": 90, "x": offset[0], "y": offset[1]})
    yy, xx = np.nonzero(mask)
    sx, sy = size[0]/110, size[1]/90
    tx, ty = (offset[0]+.5)*sx-.5, (offset[1]+.5)*sy-.5
    for p in result["autoMask"]["polygons"]:
        projected = p["source_polygons"][0]
        official = rasterize_polygons(*size, [projected["points"]])
        expected = official[np.clip(np.floor(yy*sy+ty+.5).astype(int), 0, size[1]-1),
                            np.clip(np.floor(xx*sx+tx+.5).astype(int), 0, size[0]-1)]
        assert projected["coverage_ratio"] == expected.mean()


def test_invalid_margin_is_client_error():
    with pytest.raises(image_diff.DiffError):
        image_diff.analyze_arrays(np.zeros((8, 8)), np.ones((8, 8)), auto_mask=True, auto_mask_margin="bad")
