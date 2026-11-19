"""仅质检的差异图列：候选 bbox 在源像素，differenceMap 在分析网格。"""

import base64
import io

import numpy as np
from PIL import Image

from backend.tools.repair import _inspection_thumbnail


def _png_url(pixels):
    buffer = io.BytesIO()
    Image.fromarray(pixels).save(buffer, format='PNG')
    return 'data:image/png;base64,' + base64.b64encode(buffer.getvalue()).decode('ascii')


def _map_png(width=100, height=100):
    """A busy grayscale difference map as a 0..255 PNG data URL."""
    rng = np.random.default_rng(7)
    return _png_url(rng.integers(0, 256, size=(height, width), dtype=np.uint8))


def test_source_bbox_is_mapped_back_into_the_analysis_grid():
    # PAN 1000x1000 -> grid 100x100; bbox at source (500..600) is grid (49.5..59.5).
    detected = {
        'analysis': {
            'differenceMap': _map_png(),
            'grid_to_pan': {'scale': [10.0, 10.0], 'offset': [4.5, 4.5]},
        },
        'candidates': [{'bbox': [500, 500, 600, 600]}],
    }
    thumbnail = _inspection_thumbnail(detected)
    assert thumbnail and thumbnail.startswith('data:image/jpeg;base64,')


def test_unmappable_crop_falls_back_to_the_whole_map():
    # No trusted transform and a source-pixel box far outside the 100x100 grid:
    # the column must still show evidence instead of a dash.
    detected = {
        'analysis': {'differenceMap': _map_png()},
        'candidates': [{'bbox': [5000, 5000, 5100, 5100]}],
    }
    thumbnail = _inspection_thumbnail(detected)
    assert thumbnail and thumbnail.startswith('data:image/jpeg;base64,')


def test_clean_item_gets_the_whole_map():
    detected = {'analysis': {'differenceMap': _map_png()}, 'candidates': []}
    thumbnail = _inspection_thumbnail(detected)
    assert thumbnail and thumbnail.startswith('data:image/jpeg;base64,')


def test_near_flat_difference_is_stretched_for_display():
    # Background 5/255, candidate 13/255: without a display stretch the crop
    # would stay almost black in a 160px thumbnail.
    pixels = np.full((100, 100), 5, np.uint8)
    pixels[40:60, 40:60] = 13
    detected = {
        'analysis': {
            'differenceMap': _png_url(pixels),
            'grid_to_pan': {'scale': [1.0, 1.0], 'offset': [0.0, 0.0]},
        },
        'candidates': [{'bbox': [40, 40, 60, 60]}],
    }
    thumbnail = _inspection_thumbnail(detected)
    decoded = np.asarray(Image.open(
        io.BytesIO(base64.b64decode(thumbnail.split(',', 1)[1]))))
    assert decoded.max() > 200   # the candidate is pulled to near-white
    assert np.median(decoded) < 80  # the background stays black


def test_missing_difference_map_stays_blank():
    assert _inspection_thumbnail({'candidates': []}) is None
    assert _inspection_thumbnail({'analysis': {'differenceMap': 'not-an-image'}, 'candidates': []}) is None


def test_real_detection_candidate_carries_the_grid_transform(tmp_path):
    import tifffile

    from backend.services import batch_detector, batch_manifest

    width = height = 160
    pan_pixels = np.full((height, width), 100, np.uint16)
    for y in range(10, 150, 20):
        for x in range(10, 150, 20):
            pan_pixels[y:y + 10, x:x + 10] = 200
    pan = tmp_path / 'PAN.tif'
    nosr = tmp_path / 'scene_NOSR.tif'
    tifffile.imwrite(pan, pan_pixels)
    tifffile.imwrite(nosr, np.full((80, 80), 100, np.uint16))

    target = {**batch_manifest._image(pan), 'item_id': 'i'}
    reference = batch_manifest._image(nosr)
    detected = batch_detector.detect(target, reference, {
        'mode': 'diff', 'min_area_px': 1, 'blur': False,
        'open_kernel': 1, 'close_kernel': 1, 'tone_weight': True,
    })
    assert detected['candidates']
    assert detected['analysis']['grid_to_pan']['scale'][0] > 0
    thumbnail = _inspection_thumbnail(detected)
    assert thumbnail and thumbnail.startswith('data:image/jpeg;base64,')
