"""Generate and measure the BigTIFF audit fixtures without touching production files."""
import argparse
import io
import json
from pathlib import Path
from time import perf_counter
from urllib.request import Request, urlopen

import numpy as np
from osgeo import gdal
from PIL import Image
import psutil

from backend.api.paths import scene_id_abs
from backend.services.image_tiles import ImageTiles, display_pixels, tile_window
from backend.services.preview_jpg import PREVIEW_JPG_QUALITY
from backend.services.raster_window import raster_revision

W, H = 49475, 49454
LAYOUTS = {
    "strip": ["BLOCKYSIZE=8"],
    "tiled": ["TILED=YES", "BLOCKXSIZE=512", "BLOCKYSIZE=512"],
    "deflate": ["TILED=YES", "BLOCKXSIZE=512", "BLOCKYSIZE=512", "COMPRESS=DEFLATE", "PREDICTOR=2"],
}


def pixels(x, y, w, h):
    x = np.arange(x, x + w, dtype=np.uint32)[None, :]
    y = np.arange(y, y + h, dtype=np.uint32)[:, None]
    # Repeated city blocks, roof stripes, sloped roads and single-pixel features.
    blocks = ((x % 2048 < 1280) & (y % 2048 < 1536))
    roofs = (x % 192 < 136) & (y % 144 < 96) & blocks
    roads = ((x + 2 * y) % 8192 < 80) | (x % 2048 < 52)
    lines = ((x + 2 * y) % 8192 == 80) | ((x % 53 == 0) & (y % 43 == 0))
    texture = ((x ^ y) & 15) * 130
    return (3500 + (x // 64 * 19 + y // 64 * 31) % 7000 + texture
            + blocks * 6000 + roofs * (21000 + (y % 12) * 400) + roads * 27000 + lines * 21000).astype(np.uint16)


def generate(root):
    root.mkdir(parents=True, exist_ok=True)
    for name, options in LAYOUTS.items():
        path = root / (name + ".tif")
        if path.exists():
            continue
        staging = path.with_suffix(".partial")
        ds = gdal.GetDriverByName("GTiff").Create(str(staging), W, H, 1, gdal.GDT_UInt16,
                                                 ["BIGTIFF=YES", *options])
        band = ds.GetRasterBand(1)
        for y in range(0, H, 128):
            band.WriteArray(pixels(0, y, W, min(128, H - y)), 0, y)
        band = ds = None
        staging.replace(path)
    # A separate test asset shares the compressed fixture bytes. Its existing
    # external overview is built once in this generated-data directory.
    overview = root / "overview.tif"
    if not overview.exists():
        import shutil
        shutil.copyfile(root / "deflate.tif", overview)
    if not overview.with_suffix(".tif.ovr").exists():
        ds = gdal.Open(str(overview), gdal.GA_ReadOnly)
        ds.BuildOverviews("NEAREST", [64, 128])
        ds = None


def audit(path, level):
    process = psutil.Process()
    service = ImageTiles(None)
    reader = service.reader
    rev = raster_revision(path)
    info = reader.info(path, revision=rev)
    window = tile_window(info.width, info.height, level, 0, 0)
    before = process.io_counters()
    rss = process.memory_info().rss
    start = perf_counter()
    stats = service._source_stats(path, rev, 1, info)
    sampled = perf_counter()
    result = reader.read_window(path, *window, revision=rev, grid_step=2 ** level)
    decoded = perf_counter()
    display = display_pixels(result.dn, info.nodata, stats)
    buf = io.BytesIO()
    Image.fromarray(display).save(buf, format="PNG", compress_level=1)
    encoded = perf_counter()
    after = process.io_counters()
    print(json.dumps(dict(layout=path.stem, file_bytes=path.stat().st_size, level=level,
                          overviews=info.overviews, block=info.block_size,
                          samples_ms=1000 * (sampled - start), read_ms=1000 * (decoded - sampled),
                          stretch_png_ms=1000 * (encoded - decoded), png_bytes=buf.tell(),
                          process_read_bytes=getattr(after, "read_chars", after.read_bytes) -
                          getattr(before, "read_chars", before.read_bytes),
                          rss_before=rss, rss_after=process.memory_info().rss)), flush=True)
    for x, y in ((0, 0), (20000, 15000), (240, 511), (500, 500), (W - 340, H - 314)):
        samples = reader.read_window(path, x, y, 512, 512, revision=rev).dn
        np.testing.assert_array_equal(samples, pixels(x, y, samples.shape[1], samples.shape[0]))


def audit_api(path, base):
    body = json.dumps({"sceneId": scene_id_abs(path.resolve())}).encode()
    with urlopen(Request(base + "/api/image-open/assets", body, {"Content-Type": "application/json"})) as response:
        asset = json.load(response)
    endpoint = base + "/api/image-open/assets/" + asset["id"]

    def get(relative):
        with urlopen(endpoint + relative) as response:
            return response.read(), response.headers

    verified = 0
    for x, y in ((0, 0), (20000, 15000), (240, 511), (500, 500),
                 (24556, 24556), (W - 340, H - 314)):
        data, headers = get(f"/raw-window?x={x}&y={y}&width=512&height=512")
        meta = json.loads(headers["X-SR-Raster-Window"])
        assert meta["dtype"] == "<u2"
        w, h = min(512, W - x), min(512, H - y)
        dn = np.frombuffer(data, "<u2").reshape(h, w)
        np.testing.assert_array_equal(dn, pixels(x, y, w, h))
        verified += dn.size

    # A fixed display window isolates the PNG grid from automatic tone sampling.
    def display(array):
        return np.rint(array / 257.0).astype(np.uint8)

    errors = {}
    l0 = display(pixels(24576, 24576, 512, 512))
    for level in (0, 1, 2):
        step = 2 ** level
        coord = 48 // step
        data, headers = get(f"/tiles/{level}/{coord}/{coord}?display=window:0:65535")
        meta = json.loads(headers["X-SR-Tile"])
        assert meta["x"] == meta["y"] == 24576 and meta["level"] == level
        png = np.asarray(Image.open(io.BytesIO(data)))
        if step == 1:
            expected = l0
        else:
            expected = display(pixels(24576, 24576, step*512, step*512)[step//2::step, step//2::step])
        np.testing.assert_array_equal(png, expected)
        projected = png[:512//step, :512//step].repeat(step,axis=0).repeat(step,axis=1)
        errors[level] = float(np.mean(np.abs(l0.astype(int)-projected)))
    data, _ = get("/tiles/0/96/96?display=window:0:65535")
    edge = np.asarray(Image.open(io.BytesIO(data)))
    np.testing.assert_array_equal(edge, display(pixels(49152,49152,323,302)))
    jpeg = same_stretch_jpeg = None
    if path.stem == "strip":
        data, _ = get("/preview?div=16")
        image = np.asarray(Image.open(io.BytesIO(data)))
        yy = np.arange(24576,25088)*image.shape[0]//H
        xx = np.arange(24576,25088)*image.shape[1]//W
        jpeg = float(np.mean(np.abs(l0.astype(int)-image[np.ix_(yy,xx)])))
        # Encode an 8×8-aligned patch of the legacy sampling grid using the
        # same window so the comparison isolates downsampling and JPEG loss.
        y0, x0 = int(yy[0] // 8 * 8), int(xx[0] // 8 * 8)
        ys = np.rint(np.arange(y0, (yy[-1] // 8 + 1) * 8) * (H - 1) / (image.shape[0] - 1)).astype(int)
        xs = np.rint(np.arange(x0, (xx[-1] // 8 + 1) * 8) * (W - 1) / (image.shape[1] - 1)).astype(int)
        dn = pixels(int(xs[0]), int(ys[0]), int(xs[-1]-xs[0]+1), int(ys[-1]-ys[0]+1))
        ref = display(dn[np.ix_(ys-ys[0], xs-xs[0])])
        encoded = io.BytesIO()
        Image.fromarray(ref).save(encoded, format="JPEG", quality=PREVIEW_JPG_QUALITY)
        rendered = np.asarray(Image.open(encoded))
        same_stretch_jpeg = float(np.mean(np.abs(l0.astype(int)-rendered[np.ix_(yy-y0,xx-x0)])))
    print(json.dumps(dict(layout=path.stem, checked_dn=verified, display_matches=True,
                          edge_shape=edge.shape, mae_vs_l0=errors, jpeg_mae_vs_l0=jpeg,
                          jpeg_same_stretch_mae_vs_l0=same_stretch_jpeg)), flush=True)


if __name__ == "__main__":
    gdal.UseExceptions()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--generate", action="store_true")
    parser.add_argument("--layout", choices=[*LAYOUTS, "overview"], default="deflate")
    parser.add_argument("--level", type=int, default=7)
    parser.add_argument("--api", help="Fixture/API origin for HTTP DN and PNG verification")
    args = parser.parse_args()
    if args.generate:
        generate(args.directory)
    elif args.api:
        audit_api(args.directory / (args.layout + ".tif"), args.api.rstrip("/"))
    else:
        audit(args.directory / (args.layout + ".tif"), args.level)
