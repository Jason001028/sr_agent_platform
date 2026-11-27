"""Source-wide, deterministic display mappings for native DN."""
from __future__ import annotations
from dataclasses import dataclass
from functools import lru_cache
import math
import re

import numpy as np


def valid_dn(dn, nodata):
    valid = np.isfinite(dn)
    if nodata is not None:
        valid &= dn != nodata
    return valid


@dataclass(frozen=True)
class Display:
    key: str = "equal"
    mode: str = "equal"
    low: float = 0
    high: float = 0
    curve: tuple = ()


def _number(value):
    mantissa, exponent = format(value, ".11e").split("e")
    exp = int(exponent)
    return f"{mantissa}e{exp:+d}"


def parse_display(value):
    if value in ("equal", "linear", "linear2", "sqrt", "log", "binary"):
        return Display(value, value)
    number = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"
    parts = value.split(":")
    if (len(parts) < 3 or len(parts) > 131 or parts[0] != "window"
            or (len(parts)-3) % 2 or any(not re.fullmatch(number, p) for p in parts[1:])):
        raise ValueError("Invalid tile display")
    raw = tuple(float(p) for p in parts[1:])
    if any(not math.isfinite(v) or abs(v) > 1e39 for v in raw):
        raise ValueError("Invalid tile display range")
    encoded = tuple(_number(v or 0) for v in raw)
    values = tuple(map(float, encoded))
    low, high = values[:2]
    if high <= low:
        raise ValueError("Invalid tile display range")
    curve = tuple(zip(values[2::2], values[3::2]))
    if curve and (curve[0][0] != 0 or curve[-1][0] != 1
                  or any(not 0 <= v <= 1 for p in curve for v in p)
                  or any(b[0] <= a[0] for a, b in zip(curve, curve[1:]))):
        raise ValueError("Invalid tile display curve")
    return Display("window:" + ":".join(encoded),
                   "window", low, high, curve)


@dataclass
class ToneStats:
    lo: float
    hi: float
    cdf: np.ndarray
    bins: np.ndarray
    p2: float
    p98: float
    total: int
    state: str
    bounds: tuple
    coverage: dict
    equal_u16: np.ndarray | None = None

    def __getitem__(self, index):
        # The original read-only audit uses stats[:2] for the DN range.
        return (self.lo, self.hi)[index]

    def summary(self):
        return dict(min=self.lo, max=self.hi, p2=self.p2, p98=self.p98, total=self.total,
                    hist=np.histogram(self.bins, bins=1024, range=self.bounds,
                                      weights=np.diff(self.cdf, prepend=0))[0].tolist(),
                    bounds=self.bounds, state=self.state, coverage=self.coverage)


def stretch_stats(samples, dtype=None, *, exhaustive=False, coverage=None):
    dtype = np.dtype(dtype if dtype is not None else samples.dtype)
    total = int(samples.size)
    lo, hi = (float(samples.min()), float(samples.max())) if total else (0., 0.)
    equal_u16 = None
    if total and dtype == np.dtype("uint16"):
        counts = np.bincount(samples.astype(np.intp), minlength=65536)
        occupied = np.flatnonzero(counts)
        cumulative = counts.cumsum()
        bins, cdf = occupied, cumulative[occupied]
        equal_u16 = np.rint(cumulative * (255 / total)).astype(np.uint8)
    else:
        bins, counts = np.unique(samples, return_counts=True)
        cdf = counts.cumsum()
    p2, p98 = (float(bins[np.searchsorted(cdf, math.ceil(total*p), side="left")])
               if total else 0. for p in (.02, .98))
    if exhaustive and total and hi == lo:
        state = "constant"
    elif not total or (not exhaustive and (total < 64 or p98-p2 < (8 if dtype.kind in "ui" else
            max(abs(lo), abs(hi), 1)*1e-6))):
        state = "low-confidence"
    else:
        state = "sampled" if not exhaustive else "complete"
    if dtype.kind in "ui" and (hi <= lo or state == "low-confidence"):
        limits = np.iinfo(dtype)
        bounds = (float(limits.min), float(limits.max))
    elif hi <= lo or state == "low-confidence":
        span = max(abs(lo), abs(hi), 1)
        bounds = (lo-span, hi+span)
    else:
        bounds = (lo, hi)
    return ToneStats(lo, hi, cdf, bins, p2, p98, total, state, bounds, coverage or {}, equal_u16)


def _linear_map(values, low, high, display, invert):
    t = np.clip((values-low)/(high-low), 0, 1)
    if display.curve:
        x, y = zip(*display.curve)
        t = np.interp(t, x, y)
    elif display.mode == "sqrt":
        t = np.sqrt(t)
    elif display.mode == "log":
        t = np.log1p(t) / math.log(2)
    return np.rint((1-t if invert else t) * 255).astype(np.uint8)


@lru_cache(maxsize=64)
def _manual_u16(display, invert):
    return _linear_map(np.arange(65536, dtype=np.float64), display.low, display.high, display, invert)


def display_pixels(dn, nodata, stats, display=Display(), *, invert=False):
    valid = valid_dn(dn, nodata)
    pixels = np.zeros(dn.shape, dtype=np.uint8)
    if display.mode == "binary":
        pixels[valid & (dn > 0)] = 255
        return pixels
    if display.mode == "window":
        if dn.dtype.kind == "u" and dn.dtype.itemsize == 2:
            pixels[valid] = _manual_u16(display, invert)[dn[valid]]
            return pixels
        low, high = display.low, display.high
    elif stats.state == "constant":
        pixels[valid] = (255 if invert else 0) if stats.lo == 0 else (127 if invert else 128)
        return pixels
    elif display.mode == "equal" and stats.state != "low-confidence":
        values = dn[valid]
        if stats.equal_u16 is not None:
            mapped = stats.equal_u16[values if values.dtype == np.uint16
                                     else np.clip(values, 0, 65535).astype(np.intp)]
        else:
            ids = np.searchsorted(stats.bins, values, side="right")
            mapped = np.rint(np.concatenate(([0], stats.cdf))[ids] * (255 / stats.total))
        pixels[valid] = 255-mapped if invert else mapped
        return pixels
    elif display.mode == "linear2" and stats.state != "low-confidence" and stats.p98 > stats.p2:
        low, high = stats.p2, stats.p98
    else:
        low, high = stats.bounds
    pixels[valid] = _linear_map(dn[valid].astype(np.float64), low, high, display, invert)
    return pixels


SAMPLE_LIMIT = 65536
BLOCK_LIMIT = 128
BLOCK_BYTES_LIMIT = 128 * 1024**2


def sample_source_stats(reader, source, revision, band, info):
    """Jittered strata; charge each selected physical TIFF block at full decoded size."""
    dtype = np.dtype(info.dtype)
    bw, bh = info.block_size
    block_bytes = bw * bh * dtype.itemsize * info.band_count
    w, h = info.width, info.height
    blocks, windows, parts = set(), [], []
    attempted = block_reads = 0

    def collect(x, y, width, height):
        nonlocal attempted, block_reads
        if (x, y, width, height) in windows:
            return False
        touched = {(bx, by) for by in range(y//bh, (y+height-1)//bh+1)
                   for bx in range(x//bw, (x+width-1)//bw+1)}
        count = block_reads + len(touched)
        if (count > BLOCK_LIMIT or count * block_bytes > BLOCK_BYTES_LIMIT
                or attempted + width*height > SAMPLE_LIMIT):
            return False
        dn = reader.read_window(source, x, y, width, height, band=band, revision=revision).dn
        blocks.update(touched)
        block_reads = count
        windows.append((x, y, width, height))
        attempted += width*height
        parts.append(dn[valid_dn(dn, info.nodata)])
        return True

    def summarize(exhaustive=False):
        count = len(windows)
        extent = [min(x for x, _, _, _ in windows), min(y for _, y, _, _ in windows),
                  max(x+ww for x, _, ww, _ in windows), max(y+hh for _, y, _, hh in windows)] if count else None
        return stretch_stats(np.concatenate(parts) if parts else np.empty(0, dtype), dtype,
                             exhaustive=exhaustive, coverage=dict(windows=count, extent=extent,
                                 attempted=attempted, blocks=len(blocks), blockReads=block_reads,
                                 physicalBlockBytes=block_reads*block_bytes))

    if w <= 512 and h <= 512 and collect(0, 0, w, h):
        return summarize(True)
    for strata in (8, 16):
        # Traverse alternating rows and columns across the whole frame even under a tight block budget.
        cells = [(i % strata, ((i // strata) + 3*(i % strata)) % strata) for i in range(strata**2)]
        for i, j in cells:
            x0, x1 = i*w//strata, (i+1)*w//strata
            y0, y1 = j*h//strata, (j+1)*h//strata
            width, height = min(16, x1-x0, bw), min(16, y1-y0, bh)
            if not width or not height:
                continue
            x = x0 + ((i*211+j*173+193) % 997)*(x1-x0-width)//997
            y = y0 + ((i*149+j*229+313) % 991)*(y1-y0-height)//991
            collect(x, y, min(width, bw-x%bw), min(height, bh-y%bh))
        result = summarize()
        if result.state != "low-confidence":
            break
    return result
