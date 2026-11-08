"""Turn detected contours into simple repair regions in the full-image grid."""
from __future__ import annotations

import hashlib
import math

SOFT_AREA_RATIO = .02


def fit_box(bounds, target_area, domain):
    """Expand without losing the seed, translating expansion at image edges."""
    left, top, right, bottom = domain
    x0, y0, x1, y1 = bounds
    side = math.sqrt(target_area)
    w, h = max(1., x1-x0), max(1., y1-y0)
    if w*h < target_area:
        if w >= side:
            h = max(h, target_area/w)
        elif h >= side:
            w = max(w, target_area/h)
        else:
            w = h = side
    w, h = min(right-left, w), min(bottom-top, h)
    if w*h < target_area:
        w = min(right-left, max(w, target_area/max(h, 1)))
        h = min(bottom-top, max(h, target_area/max(w, 1)))
    x = min(max((x0+x1-w)/2, left), right-w)
    y = min(max((y0+y1-h)/2, top), bottom-h)
    return [max(left, math.floor(x)), max(top, math.floor(y)),
            min(right, math.ceil(x+w)), min(bottom, math.ceil(y+h))]


def consolidate(auto, transforms):
    """Merge nearby small proposals, then expand toward a full-image 2% goal.

    Cropped analyses retain their affine transform: the denominator is never
    the ROI area. Final source polygons are checked by bind_auto_mask.
    """
    sources = [s for s in transforms if s.get('transform')]
    if not sources or not auto['polygons']:
        return
    source = sources[0]
    sw, sh = source['source_size']
    sx, sy = source['transform']['scale']
    ox, oy = source['transform']['offset']
    if sx <= 0 or sy <= 0:
        return
    domain = [-ox/sx, -oy/sy, (sw-1-ox)/sx, (sh-1-oy)/sy]
    target = SOFT_AREA_RATIO * sw * sh / (sx*sy)
    gap = max(2., math.sqrt(target)*.2)
    groups = []
    for p in auto['polygons']:
        xs, ys = zip(*p['points'])
        groups.append({'bounds': [min(xs), min(ys), max(xs), max(ys)], 'members': [p]})
    # Never merge distant defects merely because their expanded boxes touch.
    changed = True
    while changed:
        changed = False
        for i, a in enumerate(groups):
            for j in range(i+1, len(groups)):
                b = groups[j]
                ax, ay, ar, ab = a['bounds']; bx, by, br, bb = b['bounds']
                distance = math.hypot(max(0, ax-br, bx-ar), max(0, ay-bb, by-ab))
                if distance > gap or min((ar-ax)*(ab-ay), (br-bx)*(bb-by)) >= target:
                    continue
                a['bounds'] = [min(ax,bx), min(ay,by), max(ar,br), max(ab,bb)]
                a['members'] += b['members']
                groups.pop(j)
                changed = True
                break
            if changed:
                break
    output = []
    for rank, group in enumerate(groups, 1):
        members = group['members']
        p = dict(members[0])
        x0,y0,x1,y1 = fit_box(group['bounds'], target, domain)
        p['points'] = [[x0,y0],[x1,y0],[x1,y1],[x0,y1]]
        p['vertex_count'] = 4
        p['analysis_seeds'] = [seed for m in members for seed in m.get('analysis_seeds', [m['analysis_seed']])]
        p['region_id'] = hashlib.sha256('|'.join(sorted(m['region_id'] for m in members)).encode()).hexdigest()[:16]
        p['rank'] = rank
        p['merged_regions'] = len(members)
        p['soft_min_area_ratio'] = SOFT_AREA_RATIO
        p['full_image_area_ratio'] = round((x1-x0)*(y1-y0)*sx*sy/(sw*sh), 6)
        p['soft_target_met'] = p['full_image_area_ratio'] >= SOFT_AREA_RATIO - 1e-6
        p['diagnostics'] = list(dict.fromkeys(d for m in members for d in m['diagnostics']
                                            if d not in ('area_ratio_conflict', 'vertex_limit_fallback', 'coverage_conflict')))
        detected_area = sum(max(1., m.get('detected_area_px', 1)) for m in members)
        p['area_ratio'] = (x1-x0+1)*(y1-y0+1)/detected_area
        p['area_ratio_limit'] = max(1.8, p['area_ratio'])
        if p['area_ratio'] > 1.8:
            p['diagnostics'].append('repair_region_expanded')
        if not p['soft_target_met']:
            p['diagnostics'].append('soft_area_target_unmet')
        p['needs_review'] = bool(p['diagnostics'])
        p['coverage_ratio'] = p['supersample_coverage_ratio'] = 1.
        p['fallback'] = 'repair_rectangle'
        p['holes'] = {'count': sum(m['holes']['count'] for m in members),
                      'area_px': sum(m['holes']['area_px'] for m in members),
                      'filled': any(m['holes']['filled'] for m in members),
                      'large': any(m['holes']['large'] for m in members)}
        output.append(p)
    auto['polygons'] = output
    auto['detected_regions'] = auto['processed_regions']
    auto['processed_regions'] = len(output)
    auto['total_regions'] = len(output) + auto['unprocessed_regions']
    auto['geometry_policy'] = 'repair-4to6-soft2pct-v1'
