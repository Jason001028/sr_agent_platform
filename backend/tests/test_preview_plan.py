from pathlib import Path
from unittest.mock import patch

import pytest

from backend.services import preview_jpg as pj
from backend.tests.test_preview_jpg import make_strip_tif


@pytest.mark.parametrize("div,size,limits", [
    (1, (16387, 16380), ["long-edge", "area"]),
    (2, (16387, 16380), ["area"]),
    (4, (12369, 12364), []),
])
def test_near_square_preview_plan(div, size, limits):
    plan = pj.preview_plan(49475, 49454, div, 32768)
    assert (plan["width"], plan["height"]) == size
    assert plan["requestedDiv"] == div
    assert plan["limits"] == limits
    assert plan["scaleX"] == size[0] / 49475
    assert plan["scaleY"] == size[1] / 49454
    assert plan["pixels"] == size[0] * size[1] <= pj.PREVIEW_CANVAS_AREA_MAX
    assert plan["decodedBytes"] == plan["pixels"] * 4


@pytest.mark.parametrize("source,size", [
    ((32768, 8192), (32768, 8192)),
    ((8192, 32768), (8192, 32768)),
    ((32768, 8193), (32766, 8192)),
    ((100000, 1), (32768, 1)),
])
def test_integer_canvas_bounds(source, size):
    plan = pj.preview_plan(*source, 1, 32768)
    assert (plan["width"], plan["height"]) == size
    assert plan["pixels"] <= pj.PREVIEW_CANVAS_AREA_MAX


def test_preview_source_dimensions_prefer_the_tiff_grid(tmp_path):
    source, _ = make_strip_tif(str(tmp_path), "source.tif", 200, 180)
    source.with_suffix(".hdr").write_text("ENVI\nsamples = 999\nlines = 888\nbands = 1\n")
    assert pj.scene_dims(source) == {"W": 999, "H": 888}
    assert pj.scene_dims(source, prefer_tiff=True) == {"W": 200, "H": 180}


def test_collapsed_tiers_keep_one_immutable_variant(tmp_path):
    source, _ = make_strip_tif(str(tmp_path), "source.tif", 200, 180)
    jpg = tmp_path / "source_preview.jpg"
    with patch.object(pj, "configured_preview_max_edge", return_value=64), patch.object(
        pj, "build_preview_pixels", wraps=pj.build_preview_pixels
    ) as sample:
        one = pj.ensure_preview_jpg(source, jpg, div=1)
        two = pj.ensure_preview_jpg(source, jpg, div=2)
        assert one["path"] == two["path"]
        assert one["status"] == "generated"
        assert two["status"] == "cached"
        assert len(list(tmp_path.glob("v_*_preview.jpg"))) == 1
        assert sample.call_count == 1
        four = pj.ensure_preview_jpg(source, jpg, div=4)
        assert four["path"] != one["path"]
        assert (four["w"], four["h"]) == (50, 45)
        assert Path(one["path"]).is_file()
        assert sample.call_count == 2
