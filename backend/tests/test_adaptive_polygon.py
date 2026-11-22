import numpy as np
import pytest

from backend.services.adaptive_polygon import POLICY, enclose, verify_export
from backend.services.auto_mask_geometry import _measure, build_auto_mask
from backend.services.image_diff import _regions
from backend.services.mask import rasterize_polygons


@pytest.mark.parametrize("expected,points", [
    (4, [[20, 20], [70, 20], [70, 50], [20, 50]]),
    (4, [[20, 30], [30, 20], [75, 65], [65, 75]]),
    (5, [[20, 30], [30, 20], [70, 20], [70, 70], [20, 70]]),
    (6, [[20, 30], [30, 20], [60, 20], [70, 30], [70, 70], [20, 70]]),
])
def test_vertex_selection(expected, points):
    original = rasterize_polygons(100, 100, [points]).astype(bool)
    options, tested = enclose(original, 1, _measure)
    result = options[0]
    assert tested > 0
    assert result["vertex_count"] == expected
    assert result["supersample_coverage_ratio"] == 1
    actual = rasterize_polygons(100, 100, [result["points"]])
    assert actual[original].all()
    assert actual.sum() == result["polygon_area_px"]
    if expected > 4:
        assert result["optimization_gain"] > 0
    if points[0] == [20, 30] and expected == 4:
        assert result["rotation_deg"] == pytest.approx(45)


def test_seeded_shapes_are_deterministic_when_drawable():
    rng = np.random.default_rng(17)
    for _ in range(12):
        original = np.zeros((48, 56), dtype=bool)
        for x,y in rng.integers(0, 32, (8, 2)):
            original[y:y+5, x:x+7] = True
        options, count = enclose(original, 1, _measure)
        assert (options, count) == enclose(original, 1, _measure)
        for option in options:
            points = option["points"]
            assert 4 <= len(set(map(tuple, points))) == len(points) <= 6
            assert rasterize_polygons(56, 48, [points])[original].all()


def test_split_retains_original_pixel_union():
    original = np.zeros((120,120), bool)
    original[10:100, 10:17] = True
    original[10:100, 90:97] = True
    original[92:100, 10:97] = True
    labels, regions = _regions(original, original.astype(float))
    auto = build_auto_mask(labels, regions, policy=POLICY)
    assert auto["geometry_policy"] == POLICY
    safe = [p for p in auto["polygons"] if p["drawable"]]
    assert rasterize_polygons(120, 120, [p["points"] for p in safe])[original].all()
    assert len(safe) in (2, 3) or all(p["needs_review"] for p in safe)


def test_export_gate_measures_pillow_corner_disagreement():
    points = [[3, 0], [13, 1], [21, 6], [22, 10], [20, 11]]
    assert verify_export(points, 24, rasterize_polygons) == "source_export_raster_conflict"


def test_adaptive_paired_source_grids_and_policy_identity():
    from backend.services.image_diff import analyze_payload, _b64_png

    original = np.zeros((80, 100), np.uint8)
    original[22:48, 24:61] = 255
    specs = [
        {"id": i, "name": name, "sourceWidth": w, "sourceHeight": h, "grayPng": _b64_png(pixels)}
        for i, name, w, h, pixels in [
            (1, "NOSR", 183, 149, np.zeros_like(original)),
            (2, "PAN", 383, 277, original),
        ]
    ]
    payload = {"image_a": specs[0], "image_b": specs[1], "autoMask": True,
               "method": "absdiff", "blur": False, "openKernel": 1, "closeKernel": 1,
               "autoMaskGrid": {"width": 110, "height": 90, "x": 3, "y": 5}}
    legacy = analyze_payload(payload)["autoMask"]
    result = analyze_payload({**payload, "autoMaskGeometryPolicy": POLICY})["autoMask"]
    assert result["analysis_id"] != legacy["analysis_id"]
    polygon = result["polygons"][0]
    assert polygon["vertex_count"] == 4
    y, x = np.nonzero(original)
    for source, projected in zip(result["sources"], polygon["source_polygons"]):
        assert projected["drawable"]
        sw, sh = source["source_size"]
        t = source["transform"]
        xs = np.floor(x * t["scale"][0] + t["offset"][0] + .5).astype(int)
        ys = np.floor(y * t["scale"][1] + t["offset"][1] + .5).astype(int)
        assert rasterize_polygons(sw, sh, [projected["points"]])[ys, xs].all()


def test_30_degree_strip_is_not_axis_aligned():
    angle = np.deg2rad(30)
    y, x = np.mgrid[:100, :100] - 50
    mask = (abs(x*np.cos(angle) + y*np.sin(angle)) < 30) & (abs(y*np.cos(angle) - x*np.sin(angle)) < 5)
    fitted, _ = enclose(mask, 1, _measure)
    assert fitted[0]["rotation_deg"] == pytest.approx(30, abs=3)
    assert rasterize_polygons(100, 100, [fitted[0]["points"]])[mask].all()
