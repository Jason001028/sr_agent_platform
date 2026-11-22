"""Run with python -m backend.tests.benchmark_adaptive_polygon [--png file]."""
import argparse
from dataclasses import replace
import json
from pathlib import Path
from time import perf_counter

import numpy as np
from PIL import Image, ImageDraw

from backend.services.adaptive_polygon import CONFIG, POLICY, enclose, margin_radius
from backend.services.auto_mask_geometry import _measure, build_auto_mask
from backend.services.image_diff import _regions
from backend.services.mask import rasterize_polygons
from backend.services.repair_regions import consolidate


def run(png=None):
    examples = {
        "axis_rectangle": [[20, 20], [70, 20], [70, 50], [20, 50]],
        "rotated_45": [[20, 30], [30, 20], [75, 65], [65, 75]],
        "single_cut": [[20, 30], [30, 20], [70, 20], [70, 70], [20, 70]],
        "double_cut": [[20, 30], [30, 20], [60, 20], [70, 30], [70, 70], [20, 70]],
        "deep_u": [[15, 15], [25, 15], [25, 75], [65, 75], [65, 15],
                   [75, 15], [75, 85], [15, 85]],
    }
    canvas = Image.new('RGB', (300, 120 * len(examples)), 'white')
    results = []
    rotated = replace(CONFIG, max_vertices=4)
    for row, (name, shape) in enumerate(examples.items()):
        original = rasterize_polygons(100, 100, [shape]).astype(bool)
        labels, regions = _regions(original, original.astype(float))
        for column, method in enumerate(('axis_box', 'rotated_rectangle', 'sage6')):
            start = perf_counter()
            if column == 1:
                r = regions[0]
                options, _ = enclose(original, margin_radius(r.area, r.w, r.h, 'standard'),
                                     _measure, config=rotated)
                picked = options[:1]
            else:
                auto = build_auto_mask(labels, regions, policy=POLICY if column == 2 else None)
                if column == 0:
                    consolidate(auto, [{'source_size': [100, 100], 'transform': {
                        'scale': [1, 1], 'offset': [0, 0]}}])
                picked = auto['polygons']
            polygons = [entry['points'] for entry in picked if entry['points']]
            mask = rasterize_polygons(100, 100, polygons).astype(bool)
            target, actual = int(original.sum()), int(mask.sum())
            missed = int((original & ~mask).sum())
            results.append(dict(sample=name, method=method, vertices=list(map(len, polygons)),
                                rotation_deg=[o.get('rotation_deg', 0.) for o in picked], target_pixels=target,
                                mask_pixels=actual, area_ratio=actual/target,
                                extra_pixels=int((mask & ~original).sum()), missed_pixels=missed,
                                coverage=1-missed/target, elapsed_ms=round((perf_counter()-start)*1000, 2),
                                diagnostics=[d for o in picked for d in o.get('diagnostics', [])]
                                or ([] if polygons else ['unavailable'])))
            if png:
                image = Image.fromarray(np.where(original, 145, 245).astype('uint8')).convert('RGB')
                draw = ImageDraw.Draw(image)
                for points in polygons:
                    draw.line([tuple(p) for p in points + points[:1]], fill='red', width=1)
                canvas.paste(image, (column*100, row*120+20))
                ImageDraw.Draw(canvas).text((column*100, row*120), method, fill='black')
    if png:
        canvas.save(png)
    return results


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--png', type=Path)
    arguments = parser.parse_args()
    print(json.dumps(run(arguments.png), indent=2))
