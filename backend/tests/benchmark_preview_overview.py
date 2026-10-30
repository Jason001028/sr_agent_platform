"""Run: python -m backend.tests.benchmark_preview_overview > results.json

Fresh-process JPEG cache miss/hit; OS page cache is deliberately not evicted.
Uses only generated fixtures, never production imagery.
"""
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

import numpy as np
import psutil
import tifffile
from PIL import Image
from backend.services import preview_jpg as pj


def worker(path, div, legacy):
    jpg = Path(path).with_suffix('.jpg')
    context = patch.object(pj, '_overview_preview', return_value=None) if legacy else patch.object(pj, 'READ_THREADS', pj.READ_THREADS)
    with context:
        start = time.perf_counter()
        pj.ensure_preview_jpg(path, jpg, div=div)
        miss = time.perf_counter() - start
        start = time.perf_counter()
        pj.ensure_preview_jpg(path, jpg, div=div)
        hit = time.perf_counter() - start
    mem = psutil.Process().memory_info()
    print(json.dumps(dict(miss_s=miss, hit_s=hit,
                          peak_mib=getattr(mem, 'peak_wset', mem.rss) / 2**20)))


def main():
    rows = []
    with tempfile.TemporaryDirectory() as d:
        y, x = np.indices((4096, 4096), dtype='uint16')
        base = ((x // 8 + y // 8) % 4096).astype('uint16')
        del x, y
        for overview in (False, True):
            path = Path(d) / 'image.tif'
            with tifffile.TiffWriter(path) as w:
                w.write(base, subifds=4 if overview else None)
                if overview:
                    for factor in (2, 4, 8, 16):
                        w.write(base[::factor, ::factor], subfiletype=1)
            for div in pj.PREVIEW_DIVISORS:
                images, results = [], []
                for legacy in (True, False):
                    path.with_suffix('.jpg').unlink(missing_ok=True)
                    result = subprocess.check_output([sys.executable, '-m', __name__ if __name__ != '__main__' else 'backend.tests.benchmark_preview_overview', str(path), str(div), str(int(legacy))], text=True)
                    results.append(json.loads(result))
                    with Image.open(path.with_suffix('.jpg')) as im:
                        images.append(np.array(im, dtype='float32'))
                delta = images[0] - images[1]
                rows.append(dict(overview=overview, div=div, legacy=results[0], fast=results[1], mae=float(np.abs(delta).mean()), rmse=float(np.sqrt((delta**2).mean())), max_error=float(np.abs(delta).max())))
        print(json.dumps(rows, indent=2))


if __name__ == '__main__':
    if len(sys.argv) > 1:
        worker(sys.argv[1], int(sys.argv[2]), bool(int(sys.argv[3])))
    else:
        main()
