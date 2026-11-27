"""Bounded display tiles and native DN for image-open asset capabilities."""
import asyncio
from collections import OrderedDict
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import asdict, dataclass
import hashlib
import io
import json
from threading import Lock
from typing import Literal, Optional

from fastapi import HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
from PIL import Image

from backend.pathguard import PathDeniedError
from backend.services.preview_jpg import PreviewError
from backend.services.raster_tone import Display, display_pixels, parse_display, sample_source_stats, stretch_stats
from backend.services.raster_window import (
    MAX_OUTPUT, RasterReadError, RasterUnavailable, RasterWindowReader, json_nodata, raster_revision,
)

TILE_SIZE = MAX_OUTPUT
STRETCH_VERSION = "source-tone-u16-stratified-v2"
TILE_RULE = "grid-nearest-area-png-v3"
TILE_HEADERS = ["ETag", "X-SR-Tile", "X-SR-Raster-Window"]


def tile_window(width, height, level, tx, ty):
    max_level = (max(width, height) - 1).bit_length()
    if not 0 <= level <= max_level or min(tx, ty) < 0:
        raise RasterReadError("Tile level or index is outside the raster")
    step = 1 << level
    span = TILE_SIZE * step
    x, y = tx * span, ty * span
    if x >= width or y >= height:
        raise RasterReadError("Tile index is outside the raster")
    w, h = min(span, width - x), min(span, height - y)
    return x, y, w, h, (w + step - 1) // step, (h + step - 1) // step


@dataclass
class Job:
    task: asyncio.Task
    consumers: int = 0
    started: bool = False


class ImageTiles:
    def __init__(self, manager, workers=2, max_pending=32, cache_bytes=32 * 1024**2):
        self.manager = manager
        self.reader = RasterWindowReader()
        self.pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="sr-tile")
        self.slots = asyncio.Semaphore(workers)
        self.max_pending = max_pending
        self.jobs = {}
        self.lock = Lock()
        self.tiles = OrderedDict()
        self.tile_bytes = 0
        self.cache_bytes = cache_bytes
        self.stats = OrderedDict()
        self.stats_inflight = {}

    async def close(self):
        await asyncio.to_thread(self.pool.shutdown, wait=True, cancel_futures=True)

    async def serve(self, request, key):
        job = self.jobs.get(key)
        if job is None:
            if len(self.jobs) >= self.max_pending:
                raise HTTPException(503, "Tile queue is full", headers={"Retry-After": "1"})
            job = self.jobs[key] = Job(asyncio.create_task(self._run(key)))
            job.task.add_done_callback(lambda task: self._finished(key, job, task))
        job.consumers += 1
        disconnect = asyncio.create_task(self._disconnect(request))
        try:
            await asyncio.wait((disconnect, job.task), return_when=asyncio.FIRST_COMPLETED)
            if disconnect.done():
                return Response(status_code=499)
            result = job.task.result()
            if isinstance(result, Response):
                return Response(result.body, status_code=result.status_code, headers=dict(result.headers))
            return result
        finally:
            disconnect.cancel()
            job.consumers -= 1
            if not job.consumers and not job.started and not job.task.done():
                self.jobs.pop(key)
                job.task.cancel()

    @staticmethod
    async def _disconnect(request):
        while (await request.receive())["type"] != "http.disconnect":
            pass

    def _finished(self, key, job, task):
        if self.jobs.get(key) is job:
            self.jobs.pop(key)
        if not task.cancelled():
            task.exception()

    async def _run(self, key):
        async with self.slots:
            self.jobs[key].started = True
            return await asyncio.get_running_loop().run_in_executor(self.pool, self.render, *key)

    def _source_stats(self, source, revision, band, info):
        key = revision, band, STRETCH_VERSION
        with self.lock:
            if key in self.stats:
                self.stats.move_to_end(key)
                return self.stats[key]
            existing = self.stats_inflight.get(key)
            if existing is None:
                future = self.stats_inflight[key] = Future()
        if existing is not None:
            return existing.result()
        try:
            result = sample_source_stats(self.reader, source, revision, band, info)
            with self.lock:
                self.stats[key] = result
                if len(self.stats) > 32:
                    self.stats.popitem(last=False)
            future.set_result(result)
            return result
        except Exception as exc:
            future.set_exception(exc)
            raise
        finally:
            with self.lock:
                del self.stats_inflight[key]

    def _tile(self, source, revision, band, info, level, tx, ty, display=Display(), sampling="nearest"):
        window = tile_window(info.width, info.height, level, tx, ty)
        area = sampling == "area" and level in (1, 2) and not info.categorical and display.mode != "binary"
        key = revision, band, TILE_RULE, STRETCH_VERSION, display.key, sampling, level, tx, ty
        tile_id = hashlib.sha256(repr(key).encode()).hexdigest()
        with self.lock:
            if key in self.tiles:
                self.tiles.move_to_end(key)
                return self.tiles[key]
        stats = None if display.mode in ("window", "binary") else self._source_stats(source, revision, band, info)
        if area:
            result = self.reader.read_area(source, *window, step=1 << level, band=band, revision=revision, info=info)
        else:
            result = self.reader.read_window(source, *window, band=band, revision=revision, grid_step=1 << level)
        pixels = display_pixels(result.dn, None if area else info.nodata, stats, display,
                                invert=info.photometric == 0)
        output = io.BytesIO()
        Image.fromarray(pixels).save(output, format="PNG", compress_level=1)
        data = output.getvalue()
        x, y, w, h, ow, oh = window
        metadata = dict(id=tile_id, x=x, y=y, sourceWidth=w, sourceHeight=h, width=ow,
                        height=oh, level=level, tileX=tx, tileY=ty, band=band,
                        resampling=result.resampling, requestedSampling=sampling, rasterSources=result.sources,
                        displayKey=display.key, stretchVersion=STRETCH_VERSION,
                        dnBytes=result.dn.nbytes, displayBytes=pixels.nbytes, decodedBytes=ow * oh * 4)
        value = data, metadata
        # The caller checks the same revision again before returning cached bytes.
        with self.lock:
            if key not in self.tiles and len(data) <= self.cache_bytes:
                self.tiles[key] = value
                self.tile_bytes += len(data)
                while self.tile_bytes > self.cache_bytes or len(self.tiles) > 512:
                    _, (removed, _) = self.tiles.popitem(last=False)
                    self.tile_bytes -= len(removed)
        return value

    def render(self, ident, kind, band, *coords):
        with self.manager.protecting(ident):
            try:
                meta, source, version = self.manager.asset_source(ident)
            except PathDeniedError as exc:
                raise HTTPException(403, "Source TIFF is outside the preview scope or unavailable") from exc
            if source.suffix.lower() not in (".tif", ".tiff"):
                raise HTTPException(415, "Window reads require TIFF")
            revision = raster_revision(source, version)
            try:
                info = self.reader.info(source, band, revision=revision)
                if kind == "tile":
                    data, headers = self._tile(source, revision, band, info, *coords)
                elif kind == "raw":
                    result = self.reader.read_window(source, *coords, band=band, revision=revision)
                    dn = result.dn.astype(result.dn.dtype.newbyteorder("<"), copy=False)
                    data = dn.tobytes()
                    headers = dict(zip(("x", "y", "width", "height"), result.window))
                    headers.update(band=band, dtype=dn.dtype.str, noData=json_nodata(info.nodata),
                                   dnBytes=len(data))
                elif kind == "stats":
                    headers = self._source_stats(source, revision, band, info).summary()
                else:
                    headers = asdict(info)
                    headers.update(nodata=json_nodata(info.nodata), band=band, tileSize=TILE_SIZE,
                                   maxLevel=(max(info.width, info.height) - 1).bit_length(),
                                   stretchVersion=STRETCH_VERSION, displayProfiles=True)
            except RasterUnavailable as exc:
                raise HTTPException(503, str(exc)) from exc
            except (RasterReadError, RuntimeError, PreviewError, OSError) as exc:
                if raster_revision(source) != revision:
                    raise HTTPException(409, "Source changed during window read") from exc
                message = str(exc) if isinstance(exc, RasterReadError) else "TIFF window read failed"
                raise HTTPException(422, message) from exc
            finally:
                if meta["uploaded"]:
                    self.reader.release(source)
            if raster_revision(source) != revision:
                raise HTTPException(409, "Source changed during window read")
            headers = dict(headers, sourceVersion=version, rasterVersion=revision)
            if kind in ("info", "stats"):
                return JSONResponse(headers, headers={"Cache-Control": "no-store"})
            if kind == "tile":
                return Response(data, media_type="image/png", headers={
                    "X-SR-Tile": json.dumps(headers, separators=(",", ":")),
                    "ETag": '"' + headers["id"] + '"',
                    "Cache-Control": "private, no-cache",
                })
            return Response(data, media_type="application/octet-stream", headers={
                "X-SR-Raster-Window": json.dumps(headers, separators=(",", ":")),
                "Cache-Control": "no-store",
            })


def add_tile_routes(router, service):
    @router.get("/assets/{ident}/tiles")
    async def info(ident: str, request: Request, band: int = Query(1, ge=1)):
        return await service.serve(request, (ident, "info", band))

    @router.get("/assets/{ident}/tiles/stats")
    async def stats(ident: str, request: Request, band: int = Query(1, ge=1)):
        return await service.serve(request, (ident, "stats", band))

    @router.get("/assets/{ident}/tiles/{level}/{tile_x}/{tile_y}")
    async def tile(ident: str, level: int, tile_x: int, tile_y: int, request: Request,
                   band: int = Query(1, ge=1), display: str = Query("equal", max_length=2800),
                   sampling: Literal["nearest", "area"] = Query("nearest"),
                   stretchVersion: Optional[str] = Query(None, max_length=80)):
        if stretchVersion is not None and stretchVersion != STRETCH_VERSION:
            raise HTTPException(409, "Tile display version changed")
        try:
            tone = parse_display(display)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        response = await service.serve(request, (ident, "tile", band, level, tile_x, tile_y, tone, sampling))
        etag = response.headers.get("ETag")
        if etag and any(tag.strip().removeprefix("W/") in (etag, "*")
                        for tag in request.headers.get("If-None-Match", "").split(",")):
            headers = dict(response.headers)
            headers.pop("content-length", None)
            return Response(status_code=304, headers=headers)
        return response

    @router.get("/assets/{ident}/raw-window")
    async def raw(ident: str, request: Request, x: int = Query(..., ge=0), y: int = Query(..., ge=0),
                  width: int = Query(..., ge=1, le=TILE_SIZE), height: int = Query(..., ge=1, le=TILE_SIZE),
                  band: int = Query(1, ge=1)):
        return await service.serve(request, (ident, "raw", band, x, y, width, height))
