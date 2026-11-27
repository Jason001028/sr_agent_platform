"""Bounded display tiles and native DN for image-open asset capabilities."""
import asyncio
from collections import OrderedDict
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import contextmanager
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
from backend.services.raster_tone import Display, display_pixels, parse_display, sample_source_stats
from backend.services.raster_window import (
    MAX_OUTPUT, MAX_TILE_BATCH, RasterReadError, RasterUnavailable, RasterWindow, RasterWindowReader,
    json_nodata, raster_revision,
)

TILE_SIZE = MAX_OUTPUT
STRETCH_VERSION = "source-tone-u16-stratified-v2"
TILE_RULE = "grid-nearest-area-png-v3"
TILE_HEADERS = ["ETag", "X-SR-Tile", "X-SR-Raster-Window", "Retry-After"]
CACHE_CONTROL = "private, no-cache"
WINDOW_ERRORS = (RasterReadError, RuntimeError, PreviewError, OSError)


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
    result: asyncio.Future
    priority: int
    consumers: int = 0
    started: bool = False


class ImageTiles:
    def __init__(self, manager, workers=2, max_pending=32, cache_bytes=32 * 1024**2):
        self.manager = manager
        self.reader = RasterWindowReader()
        self.pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="sr-tile")
        self.workers = workers
        self.running = set()
        self.background = 0
        self.dispatch_scheduled = False
        self.closing = False
        self.max_pending = max_pending
        self.jobs = {}
        self.lock = Lock()
        self.tiles = OrderedDict()
        self.tile_bytes = 0
        self.cache_bytes = cache_bytes
        self.stats = OrderedDict()
        self.stats_inflight = {}
        self.strip_assets = OrderedDict()

    async def close(self):
        self.closing = True
        for job in self.jobs.values():
            if not job.started:
                job.result.cancel()
        await asyncio.gather(*self.running)
        await asyncio.to_thread(self.pool.shutdown, wait=True, cancel_futures=True)

    async def serve(self, request, key, *, priority=1):
        job = self.jobs.get(key)
        if job is None:
            # Keep admission space as well as an execution slot for visible L0.
            reserve = min(self.workers, self.max_pending - 1)
            limit = self.max_pending if priority == 0 else self.max_pending - reserve
            if self.closing or len(self.jobs) >= limit:
                raise HTTPException(503, "Tile queue is full", headers={"Retry-After": "1"})
            job = self.jobs[key] = Job(asyncio.get_running_loop().create_future(), priority)
        else:
            job.priority = min(job.priority, priority)
        job.consumers += 1
        self._schedule()
        disconnect = asyncio.create_task(self._disconnect(request))
        try:
            await asyncio.wait((disconnect, job.result), return_when=asyncio.FIRST_COMPLETED)
            if disconnect.done() or job.result.cancelled():
                return Response(status_code=499)
            result = job.result.result()
            if isinstance(result, Response):
                return Response(result.body, status_code=result.status_code, headers=dict(result.headers))
            return result
        finally:
            disconnect.cancel()
            job.consumers -= 1
            if not job.consumers and not job.started:
                self.jobs.pop(key)
                job.result.cancel()

    @staticmethod
    async def _disconnect(request):
        while (await request.receive())["type"] != "http.disconnect":
            pass

    def _schedule(self):
        if not self.closing and not self.dispatch_scheduled:
            self.dispatch_scheduled = True
            asyncio.get_running_loop().call_soon(self._dispatch)

    def _dispatch(self):
        self.dispatch_scheduled = False
        while not self.closing and len(self.running) < self.workers:
            pending = [(key, job) for key, job in self.jobs.items() if not job.started]
            ready = min(pending, key=lambda item: item[1].priority, default=None)
            if ready is None:
                return
            key, job = ready
            background = job.priority > 0
            if background and self.background >= max(1, self.workers - 1):
                return
            batch = self._batch(key, job, pending)
            for _, item in batch:
                item.started = True
            self.background += background
            self.running.add(asyncio.create_task(self._run(batch, background)))

    def _batch(self, key, job, pending):
        # Join already queued horizontal L0 neighbours at the same priority.
        # Start the leading request immediately even when no neighbours are queued.
        if len(key) != 9 or key[1] != "tile" or key[3] != 0:
            return [(key, job)]
        with self.lock:
            if (key[0], key[2]) not in self.strip_assets:
                return [(key, job)]
        candidates = {}
        for other, item in pending:
            if item.priority == job.priority and other[:4] == key[:4] and other[5:8] == key[5:8]:
                candidates.setdefault(other[4], (other, item))
        batch = [(key, job)]
        left = right = key[4]
        while len(batch) < MAX_TILE_BATCH:
            if right + 1 in candidates:
                right += 1
                batch.append(candidates[right])
            elif left - 1 in candidates:
                left -= 1
                batch.append(candidates[left])
            else:
                break
        return batch

    async def _run(self, batch, background):
        try:
            loop = asyncio.get_running_loop()
            keys = [key for key, job in batch]
            if len(keys) == 1:
                results = [await loop.run_in_executor(self.pool, self.render, *keys[0])]
            else:
                results = await loop.run_in_executor(
                    self.pool, self._render_tiles, keys[0][0], keys[0][2], [key[3:] for key in keys],
                )
        except Exception as exc:
            results = [exc] * len(batch)
        for (key, job), result in zip(batch, results):
            self.jobs.pop(key)
            if isinstance(result, Exception):
                job.result.set_exception(result)
                job.result.exception()  # Last consumer may already have disconnected.
            else:
                job.result.set_result(result)
        self.background -= background
        self.running.remove(asyncio.current_task())
        self._schedule()

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

    @staticmethod
    def _tile_key(revision, band, level, tx, ty, display, sampling):
        return revision, band, TILE_RULE, STRETCH_VERSION, display.key, sampling, level, tx, ty

    @staticmethod
    def _tile_id(key):
        return hashlib.sha256(repr(key).encode()).hexdigest()

    def _tile(self, source, revision, band, info, level, tx, ty, display=Display(), sampling="nearest",
              *, result=None):
        window = tile_window(info.width, info.height, level, tx, ty)
        area = sampling == "area" and level in (1, 2) and not info.categorical and display.mode != "binary"
        key = self._tile_key(revision, band, level, tx, ty, display, sampling)
        with self.lock:
            if key in self.tiles:
                self.tiles.move_to_end(key)
                return self.tiles[key]
        stats = None if display.mode in ("window", "binary") else self._source_stats(source, revision, band, info)
        if result is None:
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
        metadata = dict(id=self._tile_id(key), x=x, y=y, sourceWidth=w, sourceHeight=h, width=ow,
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

    @contextmanager
    def _asset_raster(self, ident, band):
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
                with self.lock:
                    asset_band = ident, band
                    if info.layout == "strip" and info.compression == "NONE" and info.band_count == 1 and info.photometric == 1:
                        self.strip_assets[asset_band] = None
                        self.strip_assets.move_to_end(asset_band)
                        if len(self.strip_assets) > 128:
                            self.strip_assets.popitem(last=False)
                    else:
                        self.strip_assets.pop(asset_band, None)
                yield source, version, revision, info
            except RasterUnavailable as exc:
                raise HTTPException(503, str(exc)) from exc
            except WINDOW_ERRORS as exc:
                if raster_revision(source) != revision:
                    raise HTTPException(409, "Source changed during window read") from exc
                raise self._window_error(exc) from exc
            finally:
                if meta["uploaded"]:
                    self.reader.release(source)
            if raster_revision(source) != revision:
                raise HTTPException(409, "Source changed during window read")

    @staticmethod
    def _window_error(exc):
        return HTTPException(422, str(exc) if isinstance(exc, RasterReadError) else "TIFF window read failed")

    def _native_spans(self, source, revision, band, info, missing):
        rows = []
        for tx, window in sorted(missing.items()):
            if not rows or tx != rows[-1][-1][0] + 1:
                rows.append([])
            rows[-1].append((tx, window))
        results = {}
        for row in rows:
            if len(row) < 2:
                continue
            x, y, _, h, *_ = row[0][1]
            end, _, width, *_ = row[-1][1]
            try:
                dn = self.reader.read_native_strip_span(source, x, y, end + width - x, h,
                                                        band=band, revision=revision)
            except WINDOW_ERRORS:
                continue  # An individual malformed strip/window must not fail its neighbours.
            if dn is None:
                continue
            for tx, (left, top, w, h, _, _) in row:
                part = dn[:, left - x:left - x + w]
                results[tx] = RasterWindow(part, (left, top, w, h), info, band, "nearest")
        return results

    def _render_tiles(self, ident, band, requests):
        responses = {}
        missing = {}
        with self._asset_raster(ident, band) as (source, version, revision, info):
            for index, (level, tx, ty, display, sampling, validators) in enumerate(requests):
                try:
                    window = tile_window(info.width, info.height, level, tx, ty)
                except RasterReadError as exc:
                    responses[index] = self._window_error(exc)
                    continue
                key = self._tile_key(revision, band, level, tx, ty, display, sampling)
                if validators:
                    etag = '"' + self._tile_id(key) + '"'
                    if etag in validators or "*" in validators:
                        responses[index] = Response(status_code=304, headers={
                            "ETag": etag, "Cache-Control": CACHE_CONTROL,
                        })
                        continue
                if len(requests) > 1:
                    with self.lock:
                        if key not in self.tiles:
                            missing[tx] = window
            merged = self._native_spans(source, revision, band, info, missing)
            for index, (level, tx, ty, display, sampling, _) in enumerate(requests):
                if index in responses:
                    continue
                try:
                    data, headers = self._tile(source, revision, band, info, level, tx, ty, display, sampling,
                                               result=merged.get(tx))
                except WINDOW_ERRORS as exc:
                    responses[index] = self._window_error(exc)
                    continue
                headers = dict(headers, sourceVersion=version, rasterVersion=revision)
                responses[index] = Response(data, media_type="image/png", headers={
                    "X-SR-Tile": json.dumps(headers, separators=(",", ":")),
                    "ETag": '"' + headers["id"] + '"',
                    "Cache-Control": CACHE_CONTROL,
                })
        return [responses[index] for index in range(len(requests))]

    def render(self, ident, kind, band, *coords):
        if kind == "tile":
            return self._render_tile(ident, band, *coords)
        with self._asset_raster(ident, band) as (source, version, revision, info):
            if kind == "raw":
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
        headers = dict(headers, sourceVersion=version, rasterVersion=revision)
        if kind in ("info", "stats"):
            return JSONResponse(headers, headers={"Cache-Control": "no-store"})
        return Response(data, media_type="application/octet-stream", headers={
            "X-SR-Raster-Window": json.dumps(headers, separators=(",", ":")),
            "Cache-Control": "no-store",
        })

    def _render_tile(self, ident, band, level, tx, ty, display=Display(), sampling="nearest", validators=()):
        result, = self._render_tiles(ident, band, [(level, tx, ty, display, sampling, validators)])
        if isinstance(result, Exception):
            raise result
        return result


def add_tile_routes(router, service):
    @router.get("/assets/{ident}/tiles")
    async def info(ident: str, request: Request, band: int = Query(1, ge=1)):
        return await service.serve(request, (ident, "info", band))

    @router.get("/assets/{ident}/tiles/stats")
    async def stats(ident: str, request: Request, band: int = Query(1, ge=1)):
        return await service.serve(request, (ident, "stats", band), priority=2)

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
        validators = frozenset(tag.strip().removeprefix("W/") for tag in
                               request.headers.get("If-None-Match", "").split(",") if tag.strip())
        prefetch = request.headers.get("X-SR-Tile-Priority") == "prefetch"
        priority = 2 if prefetch else 0 if level == 0 else 1
        return await service.serve(request, (ident, "tile", band, level, tile_x, tile_y, tone, sampling, validators),
                                   priority=priority)

    @router.get("/assets/{ident}/raw-window")
    async def raw(ident: str, request: Request, x: int = Query(..., ge=0), y: int = Query(..., ge=0),
                  width: int = Query(..., ge=1, le=TILE_SIZE), height: int = Query(..., ge=1, le=TILE_SIZE),
                  band: int = Query(1, ge=1)):
        return await service.serve(request, (ident, "raw", band, x, y, width, height))
