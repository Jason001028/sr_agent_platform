"""Read-only TIFF RasterIO. Coordinates use half-open source pixel edges."""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from functools import lru_cache
import hashlib
import math
from pathlib import Path
from threading import local

import numpy as np

from backend.services.preview_jpg import (
    _TAG_RPS, _np_dtype, _parse_tiff_header, _read_ifd, _strip_tables, probe_tiff, source_revision,
)

RESAMPLING = {"nearest": "NearestNeighbour", "average": "Average", "bilinear": "Bilinear"}
MAX_OUTPUT = 512
BLOCK_CACHE_BYTES = 128 * 1024**2
MAX_BLOCK_BYTES = 32 * 1024**2
TIFF_FILL_ORDER = 266


class RasterUnavailable(Exception):
    pass


class RasterReadError(ValueError):
    pass


@lru_cache(maxsize=1)
def _gdal():
    try:
        from osgeo import gdal, gdal_array
    except (ImportError, OSError) as exc:
        raise RasterUnavailable("Tile API requires GDAL with matching NumPy bindings") from exc
    if int(gdal.VersionInfo()) < 3090000 or gdal_array.GDALTypeCodeToNumericTypeCode(gdal.GDT_UInt16) is None:
        raise RasterUnavailable("Tile API requires GDAL >= 3.9 with NumPy bindings")
    gdal.SetCacheMax(min(gdal.GetCacheMax(), BLOCK_CACHE_BYTES))
    return gdal


def json_nodata(value):
    return str(value) if value is not None and not math.isfinite(value) else value


def raster_revision(source, version=None):
    """Extend the P0 revision to cover replacements and existing GDAL sidecars."""
    path = Path(source)
    stamps = [version if version is not None else source_revision(path)]
    for suffix in ("", ".ovr", ".aux.xml", ".msk", ".msk.ovr"):
        try:
            stat = path.with_name(path.name + suffix).stat()
            stamps.append((stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns))
        except FileNotFoundError:
            stamps.append(None)
    return hashlib.sha256(repr(stamps).encode()).hexdigest()


@dataclass(frozen=True)
class RasterInfo:
    width: int
    height: int
    band_count: int
    dtype: str
    nodata: float | None
    layout: str
    compression: str
    block_size: tuple
    overviews: tuple


@dataclass(frozen=True)
class RasterWindow:
    dn: np.ndarray
    window: tuple
    info: RasterInfo
    band: int
    resampling: str


@dataclass(frozen=True)
class SparseStrips:
    dtype: np.dtype
    rows_per_strip: int
    offsets: np.ndarray
    lengths: np.ndarray

    @classmethod
    def open(cls, path, probe):
        if not (probe["compression"] == 1 and not probe["tiled"] and probe["spp"] == 1
                and probe["photometric"] == 1 and probe["sample_format"] in (1, 2, 3)
                and probe["bits"] in (8, 16, 32)):
            return None
        is_big, endian, offset = _parse_tiff_header(path)
        scalars, arrays = _read_ifd(path, is_big, endian, offset)
        if scalars.get(TIFF_FILL_ORDER, 1) != 1:
            return None
        offsets, lengths = _strip_tables(path, endian, scalars, arrays)
        dtype = _np_dtype(probe["bits"], probe["sample_format"], endian)
        return cls(dtype, scalars.get(_TAG_RPS) or probe["H"], offsets, lengths)

    def read(self, path, info, x, y, w, h, ow, oh):
        itemsize = self.dtype.itemsize
        rows = y + (2 * np.arange(oh) + 1) * h // (2 * oh)
        cols = (2 * np.arange(ow) + 1) * w // (2 * ow)
        left, right = int(cols[0]), int(cols[-1]) + 1
        indices = cols - left
        length = (right - left) * itemsize
        out = np.empty((oh, ow), dtype=info.dtype)
        with open(path, "rb", buffering=0) as stream:
            for i, row in enumerate(rows.tolist()):
                strip, row_in_strip = divmod(row, self.rows_per_strip)
                start = (row_in_strip * info.width + x + left) * itemsize
                if strip >= len(self.offsets) or strip >= len(self.lengths):
                    raise RasterReadError("Missing TIFF strip")
                offset, count = int(self.offsets[strip]), int(self.lengths[strip])
                if offset == count == 0:
                    out[i].fill(info.nodata if info.nodata is not None else 0)
                    continue
                if offset <= 0 or start + length > count:
                    raise RasterReadError("Invalid TIFF strip")
                stream.seek(offset + start)
                data = stream.read(length)
                if len(data) != length:
                    raise RasterReadError("Truncated TIFF strip")
                values = np.frombuffer(data, self.dtype)
                out[i] = values if w == ow else values[indices]
        return out


class RasterWindowReader:
    def __init__(self):
        self.local = local()

    def _open(self, source, revision):
        gdal = _gdal()
        handles = getattr(self.local, "handles", None)
        if handles is None:
            handles = self.local.handles = OrderedDict()
        path = str(Path(source).resolve())
        if revision is None:
            revision = raster_revision(path)
        if path in handles and handles[path][0] == revision:
            handles.move_to_end(path)
            return handles[path][1:]
        handles.pop(path, None)
        with gdal.config_options({"GDAL_ENABLE_TIFF_SPLIT": "FALSE"}), gdal.ExceptionMgr():
            ds = gdal.OpenEx(path, gdal.OF_RASTER | gdal.OF_READONLY | gdal.OF_VERBOSE_ERROR,
                             allowed_drivers=["GTiff"], open_options=["NUM_THREADS=1"])
        if ds is None:
            raise RasterReadError("Cannot open TIFF for window reads")
        probe = probe_tiff(path)
        sparse = SparseStrips.open(path, probe)
        handles[path] = revision, ds, probe, sparse
        if len(handles) > 2:
            handles.popitem(last=False)
        return ds, probe, sparse

    def release(self, source):
        if hasattr(self.local, "handles"):
            self.local.handles.pop(str(Path(source).resolve()), None)

    def _band(self, source, band, revision):
        from osgeo import gdal_array
        ds, probe, sparse = self._open(source, revision)
        if not 1 <= band <= ds.RasterCount:
            raise RasterReadError("Band is outside the raster")
        raster = ds.GetRasterBand(band)
        native = gdal_array.GDALTypeCodeToNumericTypeCode(raster.DataType)
        if native is None or np.dtype(native).kind not in "uif" or np.dtype(native).itemsize > 4:
            raise RasterReadError("Unsupported TIFF DN type")
        dtype = np.dtype(native)
        block = tuple(raster.GetBlockSize())
        factor = ds.RasterCount if probe["planar"] == 1 else 1
        bytes_per_pixel = dtype.itemsize * factor
        if sparse is not None and sparse.dtype.name != dtype.name:
            sparse = None
        if sparse is None and math.prod(block) * bytes_per_pixel > MAX_BLOCK_BYTES:
            raise RasterReadError("TIFF decoding block exceeds 32 MiB")
        overviews = tuple(raster.GetOverview(i) for i in range(raster.GetOverviewCount()))
        info = RasterInfo(ds.RasterXSize, ds.RasterYSize, ds.RasterCount, dtype.name,
                          raster.GetNoDataValue(), "tiled" if probe["tiled"] else "strip",
                          ds.GetMetadataItem("COMPRESSION", "IMAGE_STRUCTURE") or "NONE",
                          block, tuple((o.XSize, o.YSize) for o in overviews))
        return raster, info, overviews, sparse, bytes_per_pixel

    def info(self, source, band=1, *, revision=None):
        _gdal()
        return self._band(source, band, revision)[1]

    def read_window(self, source, x, y, width, height, output_width=None, output_height=None,
                    band=1, resampling="nearest", *, revision=None, grid_step=None):
        """Clip right/bottom, preserve DN dtype, and optionally resize with RasterIO.

        A tile grid uses fixed-size source footprints and clips the final pixel
        footprint independently on each axis.
        """
        gdal = _gdal()
        raster, info, overviews, sparse, bytes_per_pixel = self._band(source, band, revision)
        if not (0 <= x < info.width and 0 <= y < info.height and width > 0 and height > 0):
            raise RasterReadError("Window is outside the raster")
        if resampling not in RESAMPLING:
            raise RasterReadError("Unsupported resampling")
        w, h = min(width, info.width - x), min(height, info.height - y)
        ow = w if output_width is None else output_width
        oh = h if output_height is None else output_height
        if not (1 <= ow <= MAX_OUTPUT and 1 <= oh <= MAX_OUTPUT):
            raise RasterReadError("Window output is limited to 512 pixels per axis")
        if (ow, oh) == (w, h):
            resampling = "nearest"
        algorithm = getattr(gdal, "GRIORA_" + RESAMPLING[resampling])

        def sections(start, size, count):
            if grid_step and count > 1 and size % grid_step:
                full = (count - 1) * grid_step
                return ((start, full, count - 1, 0), (start + full, size - full, 1, count - 1))
            return ((start, size, count, 0),)

        def read(sx, sy, sw, sh, dw, dh):
            # Pick only overviews whose pixel grid aligns with this source window.
            # The generic window service lets GDAL pick for arbitrary resize ratios.
            selected = raster
            if grid_step and resampling == "nearest":
                scale = grid_step
                while scale > 1:
                    reduced = ((info.width + scale - 1) // scale,
                               (info.height + scale - 1) // scale)
                    matching = next((o for o in overviews if (o.XSize, o.YSize) == reduced), None)
                    if matching is not None:
                        selected = matching
                        sx, sy = sx // scale, sy // scale
                        sw, sh = (sw + scale - 1) // scale, (sh + scale - 1) // scale
                        break
                    scale //= 2
            if (selected is raster and sparse is not None and resampling == "nearest"
                    and (grid_step or not overviews or (sw, sh) == (dw, dh))):
                return sparse.read(source, info, sx, sy, sw, sh, dw, dh)
            # A small threshold disables implicit selection on the strict tile
            # grid. GDAL permits a threshold below 1 to force the base band.
            options = {"GDAL_OVERVIEW_OVERSAMPLING_THRESHOLD": "0.001"} if grid_step else {}
            candidates = (selected,) if grid_step or (sw, sh) == (dw, dh) else (selected, *overviews)
            if any(math.prod(b.GetBlockSize()) * bytes_per_pixel > MAX_BLOCK_BYTES for b in candidates):
                raise RasterReadError("TIFF decoding block exceeds 32 MiB")
            with gdal.config_options(options), gdal.ExceptionMgr():
                result = selected.ReadAsArray(sx, sy, sw, sh, dw, dh, resample_alg=algorithm)
            if result is None:
                raise RasterReadError("TIFF window read failed")
            return result

        xs, ys = sections(x, w, ow), sections(y, h, oh)
        if len(xs) == len(ys) == 1:
            dn = read(x, y, w, h, ow, oh)
        else:
            dn = np.empty((oh, ow), dtype=info.dtype)
            for sy, sh, dh, dy in ys:
                for sx, sw, dw, dx in xs:
                    dn[dy:dy + dh, dx:dx + dw] = read(sx, sy, sw, sh, dw, dh)
        return RasterWindow(dn, (x, y, w, h), info, band, resampling)
