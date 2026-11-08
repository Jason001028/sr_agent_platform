"""Final repair geometry uses full-image scale and retains detected pixels."""
import numpy as np

from backend.services.image_diff import analyze_payload, _b64_png
from backend.services.mask import rasterize_polygons


def analyze(mask, *, grid=None, source=(1000, 1000)):
    h, w = mask.shape
    spec = {"sourceWidth": source[0], "sourceHeight": source[1]}
    return analyze_payload({
        "image_a": {**spec, "id": 1, "grayPng": _b64_png(np.zeros((h,w), np.uint8))},
        "image_b": {**spec, "id": 2, "grayPng": _b64_png(mask.astype(np.uint8)*255)},
        "autoMask": True, "autoMaskGrid": grid,
        "minAreaPx": 1, "blur": False, "openKernel": 1, "closeKernel": 1,
    })["autoMask"]


def test_nearby_spots_merge_but_distant_spot_stays_separate():
    mask = np.zeros((1000,1000), bool)
    mask[100:104,100:104] = True
    mask[110:114,115:119] = True
    mask[800:804,800:804] = True
    auto = analyze(mask)
    assert len(auto["polygons"]) == 2
    assert sorted(p["merged_regions"] for p in auto["polygons"]) == [1,2]
    rendered = np.zeros_like(mask)
    for p in auto["polygons"]:
        assert p["vertex_count"] == 4
        assert p["full_image_area_ratio"] >= .02
        assert p["needs_review"] and "repair_region_expanded" in p["diagnostics"]
        assert all(s["coverage_ratio"] == 1 for s in p["source_polygons"])
        rendered |= rasterize_polygons(1000,1000,[p["source_polygons"][0]["points"]]).astype(bool)
    assert not (mask & ~rendered).any()


def test_cropped_analysis_uses_full_image_area_and_expands_outside_crop():
    mask = np.zeros((100,100), bool)
    mask[40:43,40:43] = True
    auto = analyze(mask, grid={"width": 1000,"height":1000,"x":400,"y":400})
    p = auto["polygons"][0]
    points = p["source_polygons"][0]["points"]
    x0, y0 = points[0]; x1, y1 = points[2]
    assert (x1-x0)*(y1-y0) >= .02*1000*1000
    assert x0 < 400 or x1 > 499
    assert p["source_polygons"][0]["coverage_ratio"] == 1


def test_image_edge_expansion_is_shifted_inward():
    mask = np.zeros((1000,1000), bool)
    mask[:3,:3] = True
    p = analyze(mask)["polygons"][0]
    points = p["source_polygons"][0]["points"]
    assert points[0] == [0,0]
    assert all(0 <= x < 1000 and 0 <= y < 1000 for x,y in points)
    assert p["full_image_area_ratio"] >= .02
    assert p["source_polygons"][0]["coverage_ratio"] == 1


def test_long_scratch_expands_short_axis_without_ballooning_the_area():
    mask = np.zeros((1000,1000), bool)
    mask[500:502,20:980] = True
    p = analyze(mask)["polygons"][0]
    assert .02 <= p["full_image_area_ratio"] < .025
    assert p["source_polygons"][0]["coverage_ratio"] == 1
