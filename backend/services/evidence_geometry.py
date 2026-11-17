"""Deterministic evidence windows; inclusive region coordinates stay immutable."""
import hashlib
import json
import math

RECIPE_VERSION = 'repair-evidence.v2'


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     allow_nan=False, separators=(',', ':')).encode()).hexdigest()


def half_open(bbox):
    if len(bbox) != 4 or not all(math.isfinite(float(v)) for v in bbox):
        raise ValueError('invalid_geometry')
    x0, y0, x1, y1 = bbox
    if x1 < x0 or y1 < y0:
        raise ValueError('invalid_geometry')
    return [math.floor(x0), math.floor(y0), math.ceil(x1) + 1, math.ceil(y1) + 1]


def square(cx, cy, edge, width, height):
    x = min(max(0, math.floor(cx - edge / 2)), max(0, width - edge))
    y = min(max(0, math.floor(cy - edge / 2)), max(0, height - edge))
    return [x, y, min(width, x + edge), min(height, y + edge)]


def plan_region(item_id, region, width, height, *, settings=None):
    settings=settings or {'source_max_edge':2048,'detail_edge':512,'max_detail_windows':2}
    if min(width, height) < 1:
        raise ValueError('invalid_grid')
    bbox = region.get('bbox')
    if not bbox or not region.get('candidate_id'):
        raise ValueError('unsupported_geometry')
    x0, y0, x1, y1 = half_open(bbox)
    if x1 <= 0 or y1 <= 0 or x0 >= width or y0 >= height:
        raise ValueError('outside_grid')
    edge = min(settings['source_max_edge'], max(512, math.ceil(max(x1-x0, y1-y0)*1.25)))
    context = square((x0+x1)/2, (y0+y1)/2, edge, width, height)
    windows = [{'id':'context', 'window':context, 'reason':'roi_context'}]
    for name, cx, cy in [('body', (x0+x1)/2, (y0+y1)/2), ('boundary', x0, y0)][:settings['max_detail_windows']]:
        window = square(cx, cy, settings['detail_edge'], width, height)
        if not any(w['window'] == window for w in windows):
            windows.append({'id':name, 'window':window, 'reason':name})
    return {'row_id':digest([item_id, region['candidate_id'], region]),
            'region_id':region['candidate_id'], 'geometry':region,
            'coverage':'full_context' if context[0]<=x0 and context[1]<=y0 and context[2]>=x1 and context[3]>=y1 else 'sampled',
            'windows':windows}


def ratio_window(window, pan_size, nosr_size, *, verified=False):
    if not verified:
        raise ValueError('alignment_unverified')
    pw, ph = pan_size; nw, nh = nosr_size
    if min(pw, ph, nw, nh) < 1 or pw % nw or ph % nh or pw//nw != ph//nh:
        raise ValueError('alignment_unverified')
    return [math.floor(window[0]*nw/pw), math.floor(window[1]*nh/ph),
            math.ceil(window[2]*nw/pw), math.ceil(window[3]*nh/ph)]
