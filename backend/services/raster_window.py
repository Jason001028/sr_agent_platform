"""Read-only TIFF RasterIO. Coordinates use half-open source pixel edges."""
from __future__ import annotations

from collections import OrderedDict
from contextlib import contextmanager
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
from backend.services.raster_tone import valid_dn

RESAMPLING = {"nearest": "NearestNeighbour", "average": "Average", "bilinear": "Bilinear"}
MAX_OUTPUT = 512
MAX_TILE_BATCH = 4
BLOCK_CACHE_BYTES = 128 * 1024**2
MAX_BLOCK_BYTES = 32 * 1024**2
TIFF_FILL_ORDER = 266
MIN_GDAL = 3040100
GDAL_OVERVIEW_THRESHOLD = 3090000


class RasterUnavailable(Exception):
    pass


class RasterReadError(ValueError):
    pass


@lru_cache(maxsize=1)
def _gdal():
    requirement = "Tile API requires GDAL >= 3.4.1 with matching NumPy bindings"
    try:
        from osgeo import gdal, gdal_array
        if int(gdal.VersionInfo()) < MIN_GDAL:
            raise RasterUnavailable(requirement)
        # Exercise the NumPy bridge, including the high bit of a native DN.
        driver = gdal.GetDriverByName("MEM")
        if driver is None or gdal_array.GDALTypeCodeToNumericTypeCode(gdal.GDT_UInt16) is None:
            raise RasterUnavailable(requirement)
        with _raster_io(gdal, {}):
            ds = driver.Create("", 2, 1, 1, gdal.GDT_UInt16)
            expected = np.array([[0, 65535]], dtype=np.uint16)
            if ds is None or ds.GetRasterBand(1).WriteArray(expected) != gdal.CE_None:
                raise RasterUnavailable(requirement)
            result = ds.ReadAsArray()
        if result is None or result.dtype != expected.dtype or not np.array_equal(result, expected):
            raise RasterUnavailable(requirement)
    except (ImportError, OSError, RuntimeError, AttributeError, ValueError) as exc:
        raise RasterUnavailable(requirement) from exc
    gdal.SetCacheMax(min(gdal.GetCacheMax(), BLOCK_CACHE_BYTES))
    return gdal


@lru_cache(maxsize=1)
def _has_overview_threshold(gdal):
    return int(gdal.VersionInfo()) >= GDAL_OVERVIEW_THRESHOLD


@contextmanager
def _raster_io(gdal, options):
    if _has_overview_threshold(gdal):
        with gdal.config_options(options), gdal.ExceptionMgr():
            yield
        return
    # Old Python bindings have process-wide exception mode. Leave it alone
    # and use RasterIO's error return, checking it at the call site.
    saved = {key: gdal.GetThreadLocalConfigOption(key) for key in options}
    try:
        for key, value in options.items():
            gdal.SetThreadLocalConfigOption(key, value)
        yield
    finally:
        for key, value in saved.items():
            gdal.SetThreadLocalConfigOption(key, value)


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


def _stale_external_overview(path):
    try:
        return Path(path + ".ovr").stat().st_mtime_ns < Path(path).stat().st_mtime_ns
    except FileNotFoundError:
        return False


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
    photometric: int | None = None
    scale: float = 1
    offset: float = 0
    unit: str = ""
    categorical: bool = False


@dataclass(frozen=True)
class RasterWindow:
    dn: np.ndarray
    window: tuple
    info: RasterInfo
    band: int
    resampling: str
    sources: tuple = ("base",)


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


@dataclass
class _OpenedRaster:
    revision: str
    dataset: object
    probe: dict
    sparse: SparseStrips | None
    isolated_level: str | None = None
    isolated_dataset: object = None


class RasterWindowReader:
    def __init__(self):
        self.local = local()

    @staticmethod
    def _open_dataset(path, overview_level=None):
        gdal = _gdal()
        options = ["NUM_THREADS=1"]
        if overview_level is not None:
            options.append(f"OVERVIEW_LEVEL={overview_level}")
        with _raster_io(gdal, {"GDAL_ENABLE_TIFF_SPLIT": "FALSE"}):
            ds = gdal.OpenEx(path, gdal.OF_RASTER | gdal.OF_READONLY | gdal.OF_VERBOSE_ERROR,
                             allowed_drivers=["GTiff"], open_options=options)
        if ds is None:
            raise RasterReadError("Cannot open TIFF for window reads")
        return ds

    def _open(self, source, revision):
        handles = getattr(self.local, "handles", None)
        if handles is None:
            handles = self.local.handles = OrderedDict()
        path = str(Path(source).resolve())
        if revision is None:
            revision = raster_revision(path)
        if path in handles and handles[path].revision == revision:
            handles.move_to_end(path)
            return handles[path]
        handles.pop(path, None)
        # GDAL can attach an older external pyramid to a replacement TIFF of
        # the same dimensions. An aligned grid alone cannot validate its pixels.
        ds = self._open_dataset(path, "NONE" if _stale_external_overview(path) else None)
        probe = probe_tiff(path)
        sparse = SparseStrips.open(path, probe)
        opened = handles[path] = _OpenedRaster(revision, ds, probe, sparse)
        if len(handles) > 2:
            handles.popitem(last=False)
        return opened

    def _isolated_band(self, opened, band, selected, index):
        level = "NONE" if index is None else f"{index}only"
        if opened.isolated_level != level:
            ds = self._open_dataset(opened.dataset.GetDescription(), level)
            opened.isolated_dataset, opened.isolated_level = ds, level
        isolated = opened.isolated_dataset.GetRasterBand(band)
        if (isolated is None or isolated.GetOverviewCount() != 0
                or (isolated.XSize, isolated.YSize, isolated.DataType)
                != (selected.XSize, selected.YSize, selected.DataType)):
            raise RasterReadError("Cannot isolate the requested TIFF level")
        return isolated

    def release(self, source):
        if hasattr(self.local, "handles"):
            self.local.handles.pop(str(Path(source).resolve()), None)

    def _band(self, source, band, revision):
        gdal = _gdal()
        from osgeo import gdal_array
        opened = self._open(source, revision)
        ds, probe, sparse = opened.dataset, opened.probe, opened.sparse
        if not 1 <= band <= ds.RasterCount:
            raise RasterReadError("Band is outside the raster")
        raster = ds.GetRasterBand(band)
        native = gdal_array.GDALTypeCodeToNumericTypeCode(raster.DataType)
        if raster.DataType == gdal.GDT_Byte and raster.GetMetadataItem("PIXELTYPE", "IMAGE_STRUCTURE") == "SIGNEDBYTE":
            native = np.int8
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
        scale, offset = raster.GetScale(), raster.GetOffset()
        categorical = (probe["bits"] == 1 or raster.GetColorInterpretation() == gdal.GCI_PaletteIndex
                       or bool(raster.GetCategoryNames()))
        info = RasterInfo(ds.RasterXSize, ds.RasterYSize, ds.RasterCount, dtype.name,
                          raster.GetNoDataValue(), "tiled" if probe["tiled"] else "strip",
                          ds.GetMetadataItem("COMPRESSION", "IMAGE_STRUCTURE") or "NONE",
                          block, tuple((o.XSize, o.YSize) for o in overviews), probe["photometric"],
                          scale if scale is not None and math.isfinite(scale) else 1,
                          offset if offset is not None and math.isfinite(offset) else 0,
                          raster.GetUnitType() or "", categorical)
        return raster, info, overviews, sparse, bytes_per_pixel, opened

    def info(self, source, band=1, *, revision=None):
        _gdal()
        return self._band(source, band, revision)[1]

    def read_preview(self, source, width, height):
        """Sample band one into the delivered preview grid, with bounded GTiff blocks."""
        gdal = _gdal()
        with _raster_io(gdal, {"GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
                               "GDAL_PAM_ENABLED": "NO", "GDAL_NUM_THREADS": "1"}):
            ds = self._open_dataset(str(source))
            band = ds.GetRasterBand(1)
            candidates = [band] + [band.GetOverview(i) for i in range(band.GetOverviewCount())]
            if any(math.prod(b.GetBlockSize()) * max(1, (gdal.GetDataTypeSize(b.DataType) + 7) // 8)
                   * ds.RasterCount > MAX_BLOCK_BYTES for b in candidates if b is not None):
                raise RasterReadError("TIFF decoding block exceeds 32 MiB")
            result = band.ReadAsArray(0, 0, band.XSize, band.YSize, width, height,
                                      resample_alg=gdal.GRIORA_NearestNeighbour)
            if result is None or result.shape != (height, width) or result.dtype.kind not in "uif":
                raise RasterReadError("Unsupported TIFF preview samples")
            return result, band.GetNoDataValue()

    def read_native_strip_span(self, source, x, y, width, height, *, band, revision):
        """Read a bounded horizontal run of native tiles when sparse Strip I/O applies."""
        _, info, _, sparse, _, _ = self._band(source, band, revision)
        if sparse is None:
            return None
        if (not 0 <= x < x + width <= info.width or not 0 <= y < y + height <= info.height
                or width > MAX_TILE_BATCH * MAX_OUTPUT or height > MAX_OUTPUT):
            raise RasterReadError("Invalid native Strip span")
        return sparse.read(source, info, x, y, width, height, width, height)

    def read_window(self, source, x, y, width, height, output_width=None, output_height=None,
                    band=1, resampling="nearest", *, revision=None, grid_step=None):
        """Clip right/bottom, preserve DN dtype, and optionally resize with RasterIO.

        A tile grid uses fixed-size source footprints and clips the final pixel
        footprint independently on each axis.
        """
        gdal = _gdal()
        raster, info, overviews, sparse, bytes_per_pixel, opened = self._band(source, band, revision)
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
        if resampling != "nearest" and info.dtype == "int8" and raster.DataType == gdal.GDT_Byte:
            raise RasterReadError("Average/Bilinear requires a GDAL build with native Int8 for this signed TIFF")
        algorithm = getattr(gdal, "GRIORA_" + RESAMPLING[resampling])
        has_threshold = _has_overview_threshold(gdal)
        may_have_hidden_overviews = "JPEG" in info.compression
        if (not has_threshold and not grid_step and resampling != "nearest"
                and (ow < w or oh < h) and (overviews or may_have_hidden_overviews)):
            raise RasterReadError("Automatic TIFF overview selection for resized Average/Bilinear windows "
                                  "requires GDAL >= 3.9; use an explicit tile grid")
        sources = set()

        def sections(start, size, count):
            if grid_step and count > 1 and size % grid_step:
                full = (count - 1) * grid_step
                return ((start, full, count - 1, 0), (start + full, size - full, 1, count - 1))
            return ((start, size, count, 0),)

        def read(sx, sy, sw, sh, dw, dh):
            # Pick only overviews whose pixel grid aligns with this source window.
            # The generic window service lets GDAL pick for arbitrary resize ratios.
            selected = raster
            overview_index = None
            if grid_step and resampling == "nearest":
                scale = grid_step
                while scale > 1:
                    reduced = ((info.width + scale - 1) // scale,
                               (info.height + scale - 1) // scale)
                    overview_index = next((i for i, o in enumerate(overviews)
                                           if (o.XSize, o.YSize) == reduced), None)
                    if overview_index is not None:
                        selected = overviews[overview_index]
                        sources.add(f"overview:{selected.XSize}x{selected.YSize}")
                        sx, sy = sx // scale, sy // scale
                        sw, sh = (sw + scale - 1) // scale, (sh + scale - 1) // scale
                        break
                    scale //= 2
            if selected is raster:
                sources.add("base" if grid_step or (sw, sh) == (dw, dh) or not overviews else "gdal-auto")
            if (selected is raster and sparse is not None and resampling == "nearest"
                    and (grid_step or not overviews or (sw, sh) == (dw, dh))):
                return sparse.read(source, info, sx, sy, sw, sh, dw, dh)
            # A small threshold disables implicit selection on the strict tile
            # grid. GDAL permits a threshold below 1 to force the base band.
            options = {"GDAL_OVERVIEW_OVERSAMPLING_THRESHOLD": "0.001"} if grid_step and has_threshold else {}
            # At coarse steps a TIFF block contributes only a few samples.
            # Read these directly for uncompressed tiled input, keeping the
            # block cache for nearby native tiles and all compressed reads.
            if (selected is raster and info.layout == "tiled" and info.compression == "NONE"
                    and resampling == "nearest" and grid_step and grid_step >= max(info.block_size)):
                options["GTIFF_DIRECT_IO"] = "YES"
            candidates = (selected,) if grid_step or (sw, sh) == (dw, dh) else (selected, *overviews)
            if any(math.prod(b.GetBlockSize()) * bytes_per_pixel > MAX_BLOCK_BYTES for b in candidates):
                raise RasterReadError("TIFF decoding block exceeds 32 MiB")
            if (grid_step and not has_threshold and (dw < sw or dh < sh)
                    and (overviews or may_have_hidden_overviews)):
                selected = self._isolated_band(opened, band, selected, overview_index)
            with _raster_io(gdal, options):
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
        return RasterWindow(dn, (x, y, w, h), info, band, resampling, tuple(sorted(sources)))

    def read_area(self, source, x, y, w, h, ow, oh, *, step, band, revision, info):
        """Average globally aligned source footprints in bounded native windows."""
        if (step not in (2, 4) or not 0 <= x < x+w <= info.width or not 0 <= y < y+h <= info.height
                or (ow, oh) != ((w+step-1)//step, (h+step-1)//step) or max(ow, oh) > MAX_OUTPUT):
            raise RasterReadError("Invalid Area tile footprint")
        raster, _, _, sparse, _, _ = self._band(source, band, revision)
        gdal = _gdal()
        dn = np.empty((oh, ow), dtype=np.float64)
        cols = np.arange(0, w, step)
        for top in range(0, h, MAX_OUTPUT):
            height = min(MAX_OUTPUT, h-top)
            if sparse is not None:
                block = sparse.read(source, info, x, y+top, w, height, w, height)
            else:
                with _raster_io(gdal, {}):
                    block = raster.ReadAsArray(x, y+top, w, height, w, height)
                if block is None:
                    raise RasterReadError("TIFF window read failed")
            valid = valid_dn(block, info.nodata)
            rows = np.arange(0, height, step)
            sums = np.add.reduceat(np.add.reduceat(np.where(valid, block, 0), rows, axis=0,
                                                   dtype=np.float64), cols, axis=1)
            counts = np.add.reduceat(np.add.reduceat(valid, rows, axis=0, dtype=np.int32), cols, axis=1)
            out = dn[top//step:top//step+len(rows)]
            out.fill(np.nan)
            np.divide(sums, counts, out=out, where=counts != 0)
        return RasterWindow(dn, (x, y, w, h), info, band, "area", ("base",))
