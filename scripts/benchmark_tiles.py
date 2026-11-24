"""Run from the repository root with python -m scripts.benchmark_tiles."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
import io
import json
import os
from pathlib import Path
import random
from threading import Event, Thread
from time import perf_counter

import numpy as np
from osgeo import gdal
from PIL import Image
import psutil

from backend.services.image_tiles import ImageTiles, tile_window
from backend.services.preview_jpg import build_preview_pixels
from backend.services.raster_window import RasterWindowReader, raster_revision


def generate(path, width, height, layout):
    options = ["BIGTIFF=YES", "TILED=" + ("YES" if layout == "tiled" else "NO")]
    options += (["BLOCKXSIZE=512", "BLOCKYSIZE=512", "COMPRESS=DEFLATE", "PREDICTOR=2"]
                if layout == "tiled" else ["BLOCKYSIZE=8"])
    ds = gdal.GetDriverByName("GTiff").Create(str(path), width, height, 1, gdal.GDT_UInt16, options)
    band = ds.GetRasterBand(1)
    x = np.arange(width, dtype=np.uint32)
    for y in range(0, height, 256):
        rows = np.arange(y, min(y + 256, height), dtype=np.uint32)[:, None]
        band.WriteArray((x * 11 + rows * 13).astype(np.uint16), 0, y)
    ds.FlushCache()
    band = ds = None


def measure(label, fn, items, concurrency=1, background=None):
    process = psutil.Process()
    before = process.io_counters()
    baseline = process.memory_info().rss
    peak = [baseline]
    stop = Event()

    def sample():
        while not stop.wait(.005):
            peak[0] = max(peak[0], process.memory_info().rss)

    sampler = Thread(target=sample)
    sampler.start()
    started = perf_counter()

    def timed(item):
        start = perf_counter()
        result = fn(item)
        return (perf_counter() - start) * 1000, result

    try:
        context = ThreadPoolExecutor(1) if background else nullcontext()
        with context as preview_pool, ThreadPoolExecutor(concurrency) as pool:
            preview = preview_pool.submit(background) if background else None
            values = list(pool.map(timed, items))
            wall = perf_counter() - started
            jpeg = preview.result() if preview else None
    finally:
        stop.set()
        sampler.join()
    after = process.io_counters()
    read = getattr(after, "read_chars", after.read_bytes) - getattr(before, "read_chars", before.read_bytes)
    disk = after.read_bytes - before.read_bytes if hasattr(after, "read_chars") else None
    times, hits = zip(*values)
    result = dict(case=label, requests=len(times), concurrency=concurrency,
                  p50_ms=round(float(np.percentile(times, 50)), 2),
                  p95_ms=round(float(np.percentile(times, 95)), 2),
                  per_sec=round(len(times) / wall, 2), read_bytes=read, disk_read_bytes=disk,
                  peak_rss_mib=round(peak[0] / 1024**2, 2),
                  rss_delta_mib=round((peak[0] - baseline) / 1024**2, 2),
                  cache_hit_rate=round(sum(hits) / len(hits), 3) if hits[0] is not None else None)
    if jpeg:
        result.update(preview_bytes=jpeg[0], preview_ms=jpeg[1])
    print(json.dumps(result), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--width", type=int, default=49475)
    parser.add_argument("--height", type=int, default=49454)
    parser.add_argument("--requests", type=int, default=32)
    parser.add_argument("--generate-only", action="store_true")
    parser.add_argument("--source", type=Path, action="append", help="Benchmark existing TIFFs read-only")
    parser.add_argument("--jpeg-source", type=Path, help="TIFF for the concurrent legacy JPEG workload")
    parser.add_argument("--drop-file-cache", action="store_true",
                        help="Ask Linux to drop this file's page cache before the first ROI")
    args = parser.parse_args()
    if args.drop_file_cache and not hasattr(os, "posix_fadvise"):
        parser.error("--drop-file-cache requires posix_fadvise")
    cases = [(None, path) for path in args.source] if args.source else [
        (layout, args.directory / f"{layout}-{args.width}x{args.height}.tif")
        for layout in ("strip", "tiled")
    ]
    if not args.source:
        args.directory.mkdir(parents=True, exist_ok=True)
    for layout, path in cases:
        if layout and not path.exists():
            staging = path.with_suffix(".partial")
            generate(staging, args.width, args.height, layout)
            staging.replace(path)
        if args.generate_only:
            continue
        reader = RasterWindowReader()
        info = reader.info(path)
        width, height = info.width, info.height
        version = raster_revision(path)
        print(json.dumps(dict(layout=info.layout, bytes=path.stat().st_size,
                              block=info.block_size, compression=info.compression,
                              width=width, height=height, gdal=gdal.VersionInfo(), numpy=np.__version__)), flush=True)
        cols, rows = max(1, width // 512), max(1, height // 512)
        rng = random.Random(42)
        grid = [(x, y) for y in range(rows) for x in range(cols)]
        random_tiles = rng.sample(grid, min(len(grid), args.requests))
        adjacent = [(x, y) for y in range(rows // 2, rows) for x in range(cols)][:args.requests]

        def read(level, xy):
            w = tile_window(width, height, level, *xy)
            return reader.read_window(path, *w, revision=version, grid_step=1 << level).dn

        def assert_dn(x, y, dn):
            if layout:
                expected = (np.arange(x, x + dn.shape[1], dtype=np.uint32) * 11
                            + np.arange(y, y + dn.shape[0], dtype=np.uint32)[:, None] * 13).astype(np.uint16)
                np.testing.assert_array_equal(dn, expected)

        def l0(xy):
            x, y = (512 * value for value in xy)
            assert_dn(x, y, read(0, xy))

        def roi(_):
            x, y = min(20000, width - 1), min(15000, height - 1)
            assert_dn(x, y, reader.read_window(path, x, y, 512, 512, revision=version).dn)

        if args.drop_file_cache:
            with path.open("rb") as stream:
                os.posix_fadvise(stream.fileno(), 0, 0, os.POSIX_FADV_DONTNEED)
        measure("L0 arbitrary ROI, fresh handle", roi, [None])
        measure("L0 arbitrary ROI, repeated handle", roi, [None] * args.requests)
        for level in range(1, min(3, (max(width, height) - 1).bit_length()) + 1):
            indices = [(x % max(1, cols >> level), y % max(1, rows >> level)) for x, y in random_tiles]

            def lower_lod(xy):
                w = tile_window(width, height, level, *xy)
                assert read(level, xy).shape == (w[5], w[4])

            measure(f"L{level}", lower_lod, indices)
        for workers in (1, 2, 4, 8):
            for label, coords in (("random", random_tiles), ("adjacent", adjacent)):
                measure(label, l0, coords, workers)
        service = ImageTiles(None)
        service.reader = reader

        def display(xy):
            key_count = len(service.tiles)
            service._tile(path, version, 1, info, 0, *xy)
            return len(service.tiles) == key_count

        measure("PNG cold LRU", display, random_tiles)
        measure("PNG hot LRU", display, random_tiles)

        jpeg_source = args.jpeg_source
        if jpeg_source is None and layout:
            jpeg_source = args.directory / f"strip-{args.width}x{args.height}.tif"

        def preview():
            start = perf_counter()
            pixels = build_preview_pixels(jpeg_source, max_edge=2048)
            output = io.BytesIO()
            Image.fromarray(pixels).save(output, format="JPEG", quality=85)
            return output.tell(), round((perf_counter() - start) * 1000, 2)

        # Separate executor mirrors the API resource split. Use uncached tile
        # coordinates even though the OS may retain previously read source data.
        other = [(x, y) for x, y in adjacent if (x, y) not in random_tiles]

        def concurrent_display(xy):
            service._tile(path, version, 1, info, 0, *xy)

        if jpeg_source is not None and other:
            measure("cold PNG + old JPEG", concurrent_display, other, 2, preview)
        edge = ((width - 1) // 512, (height - 1) // 512)
        l0(edge)


if __name__ == "__main__":
    main()
