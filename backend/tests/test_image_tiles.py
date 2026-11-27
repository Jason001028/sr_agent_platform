import asyncio
from contextlib import asynccontextmanager
import io
import json
import os
from threading import Event

from fastapi import FastAPI
from fastapi.testclient import TestClient
import numpy as np
from PIL import Image
import pytest
import tifffile

from backend.services.heavy_work import HeavyWork
from backend.services.image_open import ImageOpen, router_for
from backend.services.image_tiles import ImageTiles, tile_window
from backend.services.raster_window import (
    RasterReadError, RasterUnavailable, RasterWindowReader, _gdal, _has_overview_threshold,
)
from backend.tests import allowed_roots_env


@pytest.fixture
def gdal():
    pytest.importorskip("osgeo.gdal_array")
    return _gdal()


@pytest.fixture
def image_api(tmp_path, monkeypatch):
    data = tmp_path / "sources"
    data.mkdir()
    for key, value in allowed_roots_env(data).items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("SR_IMAGE_CACHE_ROOT", str(tmp_path / "cache"))
    manager = ImageOpen()

    @asynccontextmanager
    async def lifespan(app):
        yield
        await manager.tiles.close()
        await app.state.heavy_work.close()

    app = FastAPI(lifespan=lifespan)
    app.state.heavy_work = HeavyWork()
    app.include_router(router_for(manager))
    with TestClient(app) as client:
        yield data, client, manager


@pytest.mark.parametrize("dtype", ["uint8", ">u2", "int16", "float32"])
def test_raw_dn_and_clipped_edge(gdal, tmp_path, dtype):
    source = tmp_path / "strip.tif"
    dn = (np.arange(31 * 43).reshape(31, 43) - 50).astype(dtype)
    tifffile.imwrite(source, dn, byteorder=">" if dtype == ">u2" else "<", rowsperstrip=7)
    reader = RasterWindowReader()
    result = reader.read_window(source, 19, 12, 512, 512)
    np.testing.assert_array_equal(result.dn, dn[12:, 19:])
    assert result.window == (19, 12, 24, 19)
    assert result.dn.dtype == dn.dtype.newbyteorder("=")
    assert result.info.block_size == (43, 7)
    assert result.info.layout == "strip"
    result = reader.read_window(source, 2, 5, 13, 11, 7, 5)
    xs = (2 * np.arange(7) + 1) * 13 // 14 + 2
    ys = (2 * np.arange(5) + 1) * 11 // 10 + 5
    np.testing.assert_array_equal(result.dn, dn[np.ix_(ys, xs)])
    reader.release(source)


def test_averaging_keeps_nodata_and_native_dtype(gdal, tmp_path):
    source = tmp_path / "average.tif"
    ds = gdal.GetDriverByName("GTiff").Create(str(source), 16, 16, 2, gdal.GDT_Float32,
                                             ["TILED=YES", "BLOCKXSIZE=16", "BLOCKYSIZE=16"])
    band = ds.GetRasterBand(2)
    band.SetNoDataValue(-999)
    for x, y, data in ((0, 0, [[2, -999], [6, 10]]), (2, 0, [[-999, -999], [-999, -999]])):
        band.WriteArray(np.array(data, dtype=np.float32), x, y)
    band = ds = None
    reader = RasterWindowReader()
    result = reader.read_window(source, 0, 0, 4, 2, 2, 1, band=2, resampling="average")
    assert result.info.nodata == -999
    assert result.dn.dtype == np.float32
    np.testing.assert_array_equal(result.dn, [[6, -999]])
    assert reader.read_window(source, 0, 0, 4, 2, 3, 1, band=2, resampling="bilinear").dn.shape == (1, 3)
    reader.release(source)


def test_signed_byte_uses_native_dn_and_rejects_legacy_unsigned_interpolation(gdal, tmp_path):
    source = tmp_path / "signed.tif"
    dn = np.tile(np.array([-120, 100, -5, 9, -4], dtype=np.int8), (5, 1))
    tifffile.imwrite(source, dn, compression="deflate")
    reader = RasterWindowReader()
    result = reader.read_window(source, 0, 0, 5, 5)
    assert result.info.dtype == "int8"
    np.testing.assert_array_equal(result.dn, dn)
    result = reader.read_window(source, 0, 0, 5, 5, 3, 3, grid_step=2)
    np.testing.assert_array_equal(result.dn, dn[np.ix_([1, 3, 4], [1, 3, 4])])
    if _has_overview_threshold(gdal):
        result = reader.read_window(source, 0, 0, 4, 2, 2, 1, resampling="average", grid_step=2)
        np.testing.assert_array_equal(result.dn, [[-10, 2]])
    else:
        with pytest.raises(RasterReadError, match="native Int8"):
            reader.read_window(source, 0, 0, 4, 2, 2, 1, resampling="average", grid_step=2)
    reader.release(source)


@pytest.mark.parametrize(("step", "method", "expected"), [
    (2, "average", [[42005, 42010, 42010, 42009], [42008, 65535, 42011, 42012],
                    [42010, 42007, 42011, 42009], [42007, 42012, 42012, 42007]]),
    (2, "bilinear", [[42007, 42009, 42009, 42011], [42008, 42010, 42010, 42011],
                     [42008, 42006, 42012, 42009], [42009, 42010, 42011, 42007]]),
    (4, "average", [[42008, 42010], [42009, 42009]]),
    (4, "bilinear", [[42008, 42010], [42009, 42010]]),
])
def test_u16_average_bilinear_reference_dn(gdal, tmp_path, step, method, expected):
    source = tmp_path / "u16.tif"
    y, x = np.indices((9, 11))
    dn = (42000 + (x * 7 + y * 13) % 19).astype(np.uint16)
    dn[2:5, 2:5] = 65535
    tifffile.imwrite(source, dn, extratags=[(42113, "s", 0, "65535", False)])
    reader = RasterWindowReader()
    result = reader.read_window(source, 0, 0, 8, 8, 8//step, 8//step, resampling=method, grid_step=step)
    np.testing.assert_array_equal(result.dn, expected)
    reader.release(source)


def test_grid_partial_footprints_and_overview(gdal, tmp_path):
    source = tmp_path / "odd.tif"
    width, height = 1031, 1027
    dn = (np.arange(width * height, dtype=np.uint32) % 60000).astype(np.uint16).reshape(height, width)
    tifffile.imwrite(source, dn, tile=(128, 128), compression="deflate")
    reader = RasterWindowReader()
    window = tile_window(width, height, 1, 1, 1)
    assert window == (1024, 1024, 7, 3, 4, 2)
    result = reader.read_window(source, *window, grid_step=2)
    np.testing.assert_array_equal(result.dn, dn[np.ix_([1025, 1026], [1025, 1027, 1029, 1030])])
    reader.release(source)
    ds = gdal.Open(str(source), gdal.GA_Update)
    assert ds.BuildOverviews("NEAREST", [3]) == 0
    ds.GetRasterBand(1).GetOverview(0).Fill(987)
    ds = None
    # A non-dyadic overview must not change the sample phase of a tile.
    result = reader.read_window(source, 0, 0, 16, 16, 4, 4, grid_step=4)
    np.testing.assert_array_equal(result.dn, dn[2:16:4, 2:16:4])
    reader.release(source)
    ds = gdal.Open(str(source), gdal.GA_Update)
    assert ds.BuildOverviews("NEAREST", []) == 0
    assert ds.BuildOverviews("NEAREST", [2]) == 0
    ds.GetRasterBand(1).GetOverview(0).Fill(321)
    ds = None
    result = reader.read_window(source, *window, grid_step=2)
    assert result.info.overviews == ((516, 514),)
    np.testing.assert_array_equal(result.dn, np.full((2, 4), 321))
    np.testing.assert_array_equal(reader.read_window(source, 0, 0, 2, 2).dn, dn[:2, :2])
    reader.release(source)


def test_stripped_l4_ignores_older_external_overview_with_repeated_rows(gdal, tmp_path):
    source = tmp_path / "PAN.tif"
    y, x = np.indices((1027, 1031), dtype=np.uint32)
    dn = ((17 * y + 23 * x) % 65000).astype(np.uint16)
    tifffile.imwrite(source, dn, rowsperstrip=17, metadata=None)
    ds = gdal.Open(str(source), gdal.GA_ReadOnly)
    assert ds.BuildOverviews("NEAREST", [16]) == 0
    ds = None
    overview = source.with_name(source.name + ".ovr")
    ds = gdal.Open(str(overview), gdal.GA_Update)
    y, x = np.indices((ds.RasterYSize, ds.RasterXSize), dtype=np.uint16)
    repeated = (x + y % 8 * 31) % 256
    ds.GetRasterBand(1).WriteArray(repeated)
    ds = None

    reader = RasterWindowReader()
    mtime = source.stat().st_mtime_ns
    os.utime(overview, ns=(mtime, mtime + 10**9))
    fresh = reader.read_window(source, 0, 0, 1024, 1024, 64, 64, grid_step=16)
    assert fresh.sources == ("overview:65x65",)
    np.testing.assert_array_equal(fresh.dn, repeated[:64, :64])
    reader.release(source)

    os.utime(overview, ns=(mtime, mtime - 10**9))
    fixed = reader.read_window(source, 0, 0, 1024, 1024, 64, 64, grid_step=16)
    assert fixed.sources == ("base",)
    np.testing.assert_array_equal(fixed.dn, dn[8:1024:16, 8:1024:16])
    np.testing.assert_array_equal(reader.read_window(source, 8, 8, 64, 64, grid_step=1).dn,
                                  dn[8:72, 8:72])
    reader.release(source)


def test_coarse_direct_io_preserves_uncompressed_tile_sample_phase(gdal, tmp_path):
    source = tmp_path / "direct.tif"
    width, height = 291, 299
    dn = np.arange(width * height, dtype=np.uint32).reshape(height, width).astype(np.uint16)
    tifffile.imwrite(source, dn, tile=(16, 16))
    reader = RasterWindowReader()
    window = tile_window(width, height, 5, 0, 0)
    result = reader.read_window(source, *window, grid_step=32)
    x = np.array([(left + min(left + 32, width)) // 2 for left in range(0, width, 32)])
    y = np.array([(top + min(top + 32, height)) // 2 for top in range(0, height, 32)])
    np.testing.assert_array_equal(result.dn, dn[np.ix_(y, x)])
    reader.release(source)


def test_jpeg_tiff_grid_uses_decoded_base_samples(gdal, tmp_path):
    source = tmp_path / "jpeg.tif"
    ds = gdal.GetDriverByName("GTiff").Create(str(source), 257, 259, 1, gdal.GDT_Byte,
                                             ["TILED=YES", "COMPRESS=JPEG"])
    y, x = np.indices((259, 257))
    assert ds.GetRasterBand(1).WriteArray(((x*31 + y*17) % 251).astype(np.uint8)) == 0
    ds = None
    ds = gdal.Open(str(source))
    base = ds.ReadAsArray()
    ds = None
    reader = RasterWindowReader()
    result = reader.read_window(source, 0, 0, 256, 256, 32, 32, grid_step=8)
    assert result.sources == ("base",)
    np.testing.assert_array_equal(result.dn, base[4:256:8, 4:256:8])
    reader.release(source)


@pytest.mark.parametrize("layout", ["strip", "deflate-strip", "tile", "deflate-bigtiff"])
@pytest.mark.parametrize("overview", [None, "internal", "external"])
def test_strict_grid_resampling_against_overview_free_reference(gdal, tmp_path, layout, overview):
    source = tmp_path / "grid.tif"
    w, h = 2055, 39
    y, x = np.indices((h, w))
    dn = (42000 + (x * 7 + y * 13) % 19).astype(np.uint16)
    dn[:8, 8:16] = 65535
    dn[8:16, :24] = 42011
    options = (["TILED=YES", "BLOCKXSIZE=32", "BLOCKYSIZE=32"]
               if layout in ("tile", "deflate-bigtiff") else ["BLOCKYSIZE=7"])
    if layout.startswith("deflate"):
        options.append("COMPRESS=DEFLATE")
    if layout == "deflate-bigtiff":
        options.append("BIGTIFF=YES")
    ds = gdal.GetDriverByName("GTiff").Create(str(source), w, h, 1, gdal.GDT_UInt16, options)
    band = ds.GetRasterBand(1)
    band.SetNoDataValue(65535)
    band.SetScale(0.25)
    band.SetOffset(-10)
    assert band.WriteArray(dn) == 0
    band = ds = None

    reader = RasterWindowReader()
    before = reader.read_window(source, 0, 0, 16, 16, 4, 4, grid_step=4).dn
    if overview:
        ds = gdal.Open(str(source), gdal.GA_Update if overview == "internal" else gdal.GA_ReadOnly)
        assert ds.BuildOverviews("NEAREST", [2, 3]) == 0
        ds = None
        ds = gdal.Open(str(source if overview == "internal" else source.with_suffix(".tif.ovr")), gdal.GA_Update)
        band = ds.GetRasterBand(1)
        matched = band.GetOverview(0) if overview == "internal" else band
        other = band.GetOverview(1) if overview == "internal" else band.GetOverview(0)
        assert matched.Fill(321) == other.Fill(987) == 0
        matched = other = band = ds = None
        # The same reader must observe the revision change, including a new .ovr.
        np.testing.assert_array_equal(reader.read_window(source, 0, 0, 16, 16, 4, 4, grid_step=4).dn, 321)
    np.testing.assert_array_equal(before, dn[2:16:4, 2:16:4])

    reference = gdal.GetDriverByName("MEM").Create("", w, h, 1, gdal.GDT_UInt16)
    ref = reference.GetRasterBand(1)
    ref.SetNoDataValue(65535)
    assert ref.WriteArray(dn) == 0
    for tx in (0, 3, 4):
        tile = tile_window(w, h, 0, tx, 0)
        sx, sy, sw, sh, _, _ = tile
        native = reader.read_window(source, *tile, grid_step=1)
        np.testing.assert_array_equal(native.dn, dn[sy:sy+sh, sx:sx+sw])
        assert native.dn.dtype == np.uint16
        assert (native.info.scale, native.info.offset, native.info.nodata) == (0.25, -10, 65535)
        assert native.sources == ("base",)

    for level in (1, 2):
        step = 2 ** level
        for tx in range((w + 512 * step - 1) // (512 * step)):
            tile = tile_window(w, h, level, tx, 0)
            sx, sy, sw, sh, ow, oh = tile
            for name, alg in (("nearest", gdal.GRIORA_NearestNeighbour),
                              ("average", gdal.GRIORA_Average), ("bilinear", gdal.GRIORA_Bilinear)):
                actual = reader.read_window(source, *tile, grid_step=step, resampling=name)
                if overview and name == "nearest":
                    np.testing.assert_array_equal(actual.dn, 321)
                    assert actual.sources == ("overview:1028x20",)
                    continue
                expected = np.empty((oh, ow), dtype=np.uint16)
                for j in (0, oh - 1):
                    dh = oh - 1 if j == 0 else 1
                    rh = min(dh * step, sh - j * step)
                    full_cols, tail = divmod(sw, step)
                    if full_cols:
                        expected[j:j+dh, :full_cols] = ref.ReadAsArray(
                            sx, sy+j*step, full_cols*step, rh, full_cols, dh, resample_alg=alg)
                    if tail:
                        expected[j:j+dh, -1:] = ref.ReadAsArray(
                            sx+full_cols*step, sy+j*step, tail, rh, 1, dh, resample_alg=alg)
                np.testing.assert_array_equal(actual.dn, expected)
                repeated = reader.read_window(source, *tile, grid_step=step, resampling=name)
                np.testing.assert_array_equal(repeated.dn, actual.dn)
                assert actual.sources == ("base",)
        if not overview:
            for name, top, expected in (("average", 0, 65535), ("bilinear", 10, 42011)):
                result = reader.read_window(source, 8, top, step, step, 1, 1, resampling=name, grid_step=step)
                assert result.dn.item() == expected
    if overview:
        for name in ("average", "bilinear"):
            if _has_overview_threshold(gdal):
                assert reader.read_window(source, 0, 0, 16, 16, 4, 4, resampling=name).dn.shape == (4, 4)
            else:
                with pytest.raises(RasterReadError, match="use an explicit tile grid"):
                    reader.read_window(source, 0, 0, 16, 16, 4, 4, resampling=name)
    reader.release(source)


def test_oversized_compressed_block_is_rejected_before_decode(gdal, tmp_path):
    source = tmp_path / "large-block.tif"
    ds = gdal.GetDriverByName("GTiff").Create(
        str(source), 8192, 8192, 1, gdal.GDT_UInt16,
        ["TILED=YES", "BLOCKXSIZE=8192", "BLOCKYSIZE=8192", "COMPRESS=DEFLATE", "SPARSE_OK=YES"],
    )
    ds = None
    reader = RasterWindowReader()
    with pytest.raises(RasterReadError, match="decoding block"):
        reader.read_window(source, 0, 0, 512, 512)
    reader.release(source)


def test_missing_gdal_leaves_legacy_jpeg_available(image_api, monkeypatch):
    data, client, manager = image_api
    source = data / "source.tif"
    tifffile.imwrite(source, np.arange(32 * 32, dtype=np.uint16).reshape(32, 32))
    ident = manager.asset(source)["id"]
    base = f"/api/image-open/assets/{ident}"

    def unavailable():
        raise RasterUnavailable("GDAL unavailable")

    monkeypatch.setattr("backend.services.raster_window._gdal", unavailable)
    assert client.get(base + "/tiles/0/0/0").status_code == 503
    assert client.get(base + "/preview?div=2").status_code == 200


def test_binary_mask_tiles_keep_black_white_without_stretch_or_averaging(gdal, image_api):
    data, client, manager = image_api
    source = data / 'mask.tif'
    dn = np.zeros((8, 8), dtype=np.uint8)
    dn[:, 2:4] = 1
    dn[:, 4:6] = 255
    dn[:, 6:] = 99
    tifffile.imwrite(source, dn, compression='deflate',
                     extratags=[(42113, 's', 0, '99', False)])
    asset = client.post('/api/image-open/assets', json={'path': str(source)}).json()
    base = f"/api/image-open/assets/{asset['id']}/tiles"
    expected = np.where((dn > 0) & (dn != 99), 255, 0).astype(np.uint8)
    native = client.get(base + '/0/0/0?display=binary')
    assert native.status_code == 200
    np.testing.assert_array_equal(np.asarray(Image.open(io.BytesIO(native.content))), expected)
    overview = client.get(base + '/1/0/0?display=binary&sampling=area')
    assert overview.status_code == 200
    assert json.loads(overview.headers['X-SR-Tile'])['resampling'] == 'nearest'
    np.testing.assert_array_equal(np.asarray(Image.open(io.BytesIO(overview.content))), expected[1::2, 1::2])
    assert not manager.tiles.stats


def test_real_api_png_dn_jpeg_and_version(gdal, image_api):
    data, client, manager = image_api
    source = data / "test.tif"
    dn = np.tile(np.arange(512, dtype=np.uint16), (515, 3))[:, :1033]
    tifffile.imwrite(source, dn, rowsperstrip=8)
    asset = client.post("/api/image-open/assets", json={"path": str(source)}).json()
    base = f"/api/image-open/assets/{asset['id']}"
    info = client.get(base + "/tiles").json()
    assert (info["width"], info["height"], info["tileSize"], info["dtype"]) == (1033, 515, 512, "uint16")
    first = client.get(base + "/tiles/0/0/0")
    second = client.get(base + "/tiles/0/1/0")
    assert first.status_code == second.status_code == 200
    assert first.headers["content-type"] == "image/png"
    assert first.content == second.content  # Same DN + shared stats.
    assert first.headers["etag"] != second.headers["etag"]
    assert Image.open(io.BytesIO(first.content)).size == (512, 512)
    header = json.loads(second.headers["X-SR-Tile"])
    assert (header["x"], header["y"], header["dnBytes"], header["displayBytes"], header["decodedBytes"]) == (
        512, 0, 524288, 262144, 1048576)
    assert header["sourceVersion"] == asset["sourceRev"]
    assert client.get(base + "/tiles/0/0/0", headers={"If-None-Match": first.headers["etag"]}).status_code == 304
    edge = client.get(base + "/tiles/1/1/0")
    assert Image.open(io.BytesIO(edge.content)).size == (5, 258)
    assert client.get(base + "/tiles/0/3/0").status_code == 422
    assert client.get(base + "/tiles/0/0/0?band=2").status_code == 422
    assert client.get(base + "/raw-window?x=0&y=0&width=513&height=1").status_code == 422
    raw = client.get(base + "/raw-window?x=512&y=0&width=512&height=512")
    assert json.loads(raw.headers["X-SR-Raster-Window"])["rasterVersion"] == header["rasterVersion"]
    np.testing.assert_array_equal(np.frombuffer(raw.content, "<u2").reshape(512, 512), dn[:512, 512:1024])
    jpg = client.get(base + "/preview?div=2")
    assert jpg.status_code == 200 and jpg.headers["content-type"] == "image/jpeg"
    assert list(data.iterdir()) == [source]
    # A sidecar changes tile identity even when P0's asset revision is identical.
    sidecar = source.with_suffix(".tif.aux.xml")
    sidecar.write_text("<PAMDataset></PAMDataset>", encoding="utf-8")
    assert client.get(base + "/tiles/0/0/0").headers["etag"] != first.headers["etag"]
    stat = source.stat()
    os.utime(source, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
    assert client.get(base + "/tiles/0/0/0").status_code == 409
    assert len(manager.tiles.stats) == 2


def test_same_tile_shares_work_and_disconnected_queued_job_is_cancelled():
    class Request:
        def __init__(self):
            self.disconnected = asyncio.Event()

        async def receive(self):
            await self.disconnected.wait()
            return {"type": "http.disconnect"}

    async def run():
        service = ImageTiles(None, workers=1)
        entered, release = Event(), Event()
        calls = []

        def render(key):
            calls.append(key)
            entered.set()
            assert release.wait(5)
            return "done"

        service.render = render
        try:
            req = Request()
            first = asyncio.create_task(service.serve(req, ("one",)))
            second = asyncio.create_task(service.serve(Request(), ("one",)))
            assert await asyncio.to_thread(entered.wait, 5)
            abandoned = Request()
            queued = asyncio.create_task(service.serve(abandoned, ("queued",)))
            await asyncio.sleep(0)
            abandoned.disconnected.set()
            assert (await queued).status_code == 499
            req.disconnected.set()
            assert (await first).status_code == 499
            release.set()
            assert await second == "done"
            assert calls == ["one"]
        finally:
            release.set()
            await service.close()

    # Keep the main thread's default loop available to later Python 3.9 fixtures.
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(run())
    finally:
        loop.close()


def test_u16_native_window_tone_cache_and_raw(gdal, image_api):
    from backend.services.raster_tone import BLOCK_BYTES_LIMIT, BLOCK_LIMIT, SAMPLE_LIMIT
    data, client, manager = image_api
    source = data / "weak.tif"
    dn = np.full((1025, 1027), 21000, dtype=np.uint16)
    dn[300, 509:516] = np.arange(21700, 21707)
    dn[300, 516] = 65535
    tifffile.imwrite(source, dn, rowsperstrip=16, extratags=[(42113, "s", 0, "65535", False)])
    base = "/api/image-open/assets/" + manager.asset(source)["id"]
    stats = client.get(base+"/tiles/stats").json()
    assert stats["state"] == "low-confidence"
    assert stats["total"] > 0
    assert stats["coverage"]["blockReads"] <= BLOCK_LIMIT
    assert stats["coverage"]["physicalBlockBytes"] <= BLOCK_BYTES_LIMIT
    assert stats["coverage"]["attempted"] <= SAMPLE_LIMIT
    assert sum(stats["hist"]) == stats["total"]
    old = client.get(base+"/tiles/0/0/0")
    assert client.get(base+"/tiles/0/0/0?stretchVersion=obsolete").status_code == 409
    assert np.asarray(Image.open(io.BytesIO(old.content)))[300, 509] != 128
    left = client.get(base+"/tiles/0/0/0?display=window:21700:21706")
    right = client.get(base+"/tiles/0/1/0?display=window:21700:21706")
    assert left.headers["etag"] != old.headers["etag"]
    same = client.get(base+"/tiles/0/0/0?display=window:21700.0:2.1706e4",
                      headers={"If-None-Match": left.headers["etag"]})
    assert same.status_code == 304
    a, b = (np.asarray(Image.open(io.BytesIO(resp.content))) for resp in (left, right))
    assert np.r_[a[300,509:], b[300,:5]].tolist() == [0,42,85,128,170,212,255,0]
    raw = client.get(base+"/raw-window?x=509&y=300&width=8&height=1")
    np.testing.assert_array_equal(np.frombuffer(raw.content,"<u2"), dn[300,509:517])
    assert json.loads(left.headers["X-SR-Tile"])["displayKey"] == "window:2.17000000000e+4:2.17060000000e+4"
    for bad in ("window:1:1", "window:0:inf", "window:0:5:0:2:1:0"):
        assert client.get(base+"/tiles/0/0/0", params={"display":bad}).status_code == 422


@pytest.mark.parametrize("level", [1,2])
@pytest.mark.parametrize("layout", ["strip","tiled-deflate"])
def test_area_adjacent_tiles_odd_footprint_and_overview(gdal, image_api, level, layout):
    data, client, manager = image_api
    source = data / "area.tif"
    w,h=2051,19
    y,x=np.indices((h,w))
    dn=(1000+3*x+11*y+(x%2)*300).astype(np.uint16)
    dn[:4,:4]=65535
    dn[5,8]=65535
    with tifffile.TiffWriter(source) as file:
        storage={"rowsperstrip":4} if layout=="strip" else {"tile":(128,128),"compression":"deflate"}
        file.write(dn, **storage, subifds=1,
                   extratags=[(42113,"s",0,"65535",False)])
        file.write(np.full(((h+1)//2,(w+1)//2), 9999, np.uint16),
                   rowsperstrip=4, subfiletype=1)
    base="/api/image-open/assets/"+manager.asset(source)["id"]
    step=2**level
    outputs=[]
    for tx in range((w+512*step-1)//(512*step)):
        response=client.get(base+f"/tiles/{level}/{tx}/0?sampling=area&display=window:1000:8000")
        assert response.status_code==200,response.text
        header=json.loads(response.headers["X-SR-Tile"])
        assert header["resampling"]=="area"
        assert header["rasterSources"]==["base"]
        outputs.append(np.asarray(Image.open(io.BytesIO(response.content))))
    expected=[]
    for yy in range(0,h,step):
        row=[]
        for xx in range(0,w,step):
            block=dn[yy:yy+step,xx:xx+step]
            good=block[block!=65535]
            row.append(np.rint(np.clip((good.mean()-1000)/7000,0,1)*255) if good.size else 0)
        expected.append(row)
    np.testing.assert_array_equal(np.concatenate(outputs,axis=1),expected)
    nearest=client.get(base+f"/tiles/{level}/0/0")
    assert json.loads(nearest.headers["X-SR-Tile"])["rasterSources"] == ["overview:1026x10"]
    l0=client.get(base+"/tiles/0/0/0?sampling=area")
    assert json.loads(l0.headers["X-SR-Tile"])["resampling"]=="nearest"
    assert l0.content==client.get(base+"/tiles/0/0/0").content


def test_palette_uses_nearest_even_when_area_is_requested(gdal, image_api):
    data,client,manager=image_api
    source=data/"classes.tif"
    dn=(np.indices((16,16)).sum(axis=0)%2).astype(np.uint8)
    colors=np.zeros((3,256),dtype=np.uint16)
    colors[:,1]=65535
    tifffile.imwrite(source,dn,photometric="palette",colormap=colors)
    base="/api/image-open/assets/"+manager.asset(source)["id"]
    assert client.get(base+"/tiles").json()["categorical"]
    result=client.get(base+"/tiles/1/0/0?sampling=area")
    assert json.loads(result.headers["X-SR-Tile"])["resampling"]=="nearest"


def test_tone_constant_requires_exhaustive_source_and_quantiles():
    from backend.services.raster_tone import Display, display_pixels, stretch_stats
    samples=np.full(256,12000,np.uint16)
    assert stretch_stats(samples).state=="low-confidence"
    assert stretch_stats(samples, exhaustive=True).state=="constant"
    assert display_pixels(samples,None,stretch_stats(samples,exhaustive=True))[0]==128
    stats=stretch_stats(np.arange(65536,dtype=np.uint16))
    assert (stats.p2,stats.p98)==(1310,64225)
    values=np.array([0,30000,65535],np.uint16)
    assert display_pixels(values,65535,stats,Display("linear2","linear2")).tolist()==[0,116,0]


def test_compressed_u16_preview_keeps_native_values_until_mapping(tmp_path):
    from backend.services.preview_jpg import build_preview_pixels
    source=tmp_path/"pillow-native.tif"
    dn=np.tile(np.repeat(np.array([1000,2000,4000,8000],np.uint16),64),(32,1))
    tifffile.imwrite(source,dn,compression="deflate",photometric="minisblack")
    for edge in (256,128):
        preview=build_preview_pixels(source,edge)
        values=preview[preview.shape[0]//2,edge//8::edge//4]
        assert len(set(values.tolist()))==4
        assert np.all(np.diff(values.astype(int))>0)
