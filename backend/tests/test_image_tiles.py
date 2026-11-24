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
from backend.services.raster_window import RasterReadError, RasterUnavailable, RasterWindowReader, _gdal
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

    asyncio.run(run())
