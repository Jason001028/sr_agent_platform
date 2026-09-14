"""Disk-array scene preview JPEG generation (server-side, 09-02 decision).

Mirrors the browser sparse-preview semantics of frontend tifDecode
(parseStrips / sparseSample / stretchRgba) so the baked preview looks like the
local sparse preview / exported JPG:

    ps   = min(1, 8192 / max(W, H))
    pw   = round(W * ps),  ph = round(H * ps)
    out(i, j) = src[ round(i * (H-1) / (ph-1)), round(j * (W-1) / (pw-1)) ]

  * Sampling is row-wise: for each sampled row read its byte span from the
    containing strip and keep only the sampled columns — never the whole file
    (the same trick as the browser sparse path).
  * Display = 2% Linear stretch (p2..p98 → 0..255), WhiteIsZero (photometric 0)
    inverted first, matching stretchRgba + invert; constant images follow the
    frontend rule (all-zero → black, other constant → mid-gray 128).
  * Encoded as grayscale JPEG via Pillow (already a dev dep).

Pure-struct TIFF header/IFD parsing — no new runtime dependency, classic TIFF
(42) and BigTIFF (43) both handled. The reader targets the confirmed real
layout (compression=1, one strip per row, single band); compressed / tiled /
multi-band scenes fall back to Pillow for *small* files only (Pillow decodes
fully — a 1.1 GB scene would blow memory) and raise otherwise.

Cache policy lives at the call site (api layer): the caller derives jpg_path;
ensure_preview_jpg() skips generation when an existing file is not older than
the source.
"""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image

PREVIEW_MAX_EDGE = 8192          # 与前端 SPARSE_PREVIEW_MAX / JPG_MAX 一致
PREVIEW_JPG_QUALITY = 90         # 服务端预览质量（显示用）
PILLOW_FALLBACK_MAX_PX = 1 << 26  # 67M px：Pillow 兜底只敢接小文件（整图解码）

# TIFF type sizes（与前端 parseStrips tsize 表一致；index 0 无效）
_TYPESIZE = (None, 1, 1, 2, 4, 8, 1, 1, 2, 4, 8, 4, 8,
             None, None, None, 8, 8, 8)
_TAG_W = 256
_TAG_H = 257
_TAG_BITS = 258
_TAG_COMP = 259
_TAG_PHOTO = 262
_TAG_OFFS = 273
_TAG_BYTES = 279
_TAG_RPS = 278
_TAG_SPP = 277
_TAG_SF = 339
_TAG_PLANAR = 284
_TAG_TILE_W = 322
_TAG_TILE_H = 323

_ENVI_DIM_RE = re.compile(r"^\s*(samples|lines|bands)\s*=\s*(\d+)", re.I)


class PreviewError(Exception):
    """Raised when a scene cannot produce a preview (reason in message)."""


# --------------------------------------------------------------------------
# ENVI header dimensions（优先：很多盘阵场景带 .hdr）
# --------------------------------------------------------------------------
def dims_from_envi_header(path) -> dict | None:
    """Read samples/lines/bands from `<basename>.hdr`; None if absent/unusable."""
    hdr = Path(path).with_suffix(".hdr")
    if not hdr.is_file():
        return None
    try:
        text = hdr.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    dims: dict = {}
    for line in text.splitlines():
        m = _ENVI_DIM_RE.match(line)
        if m:
            key = m.group(1).lower()
            dims[key] = int(m.group(2))
    if dims.get("samples", 0) > 0 and dims.get("lines", 0) > 0:
        return {"W": dims["samples"], "H": dims["lines"],
                "bands": dims.get("bands", 1)}
    return None


# --------------------------------------------------------------------------
# TIFF header / IFD 解析（纯 struct，classic + BigTIFF）
# --------------------------------------------------------------------------
def _parse_tiff_header(path) -> tuple[bool, str, int]:
    """Return (is_big, endian, ifd_offset). Raises PreviewError if not TIFF."""
    with open(path, "rb") as f:
        head = f.read(16)
    if len(head) < 8:
        raise PreviewError("文件过小，无法解析 TIFF 头")
    if head[0:2] == b"II":
        endian = "little"
    elif head[0:2] == b"MM":
        endian = "big"
    else:
        raise PreviewError("不是 TIFF 文件（缺 II/MM 字节序标记）")
    magic = int.from_bytes(head[2:4], endian)
    if magic == 42:
        is_big, need = False, 8
    elif magic == 43:
        is_big, need = True, 16
    else:
        raise PreviewError(f"不是 TIFF/BigTIFF（magic={magic}）")
    if len(head) < need:
        raise PreviewError("TIFF 头不完整")
    ifd_off = int.from_bytes(
        head[8:16] if is_big else head[4:8], endian)
    return is_big, endian, ifd_off


def _read_ifd(path, is_big, endian, ifd_off) -> tuple[dict, dict]:
    """Parse one IFD. Returns (scalars, array_refs).

    scalar_tags: tag → int  （count == 1 的标量）
    array_refs:  tag → (count, item_size, data_bytes | file_offset)
                inline 数组给字节，外部数组给文件偏移（读取按需）。
    """
    with open(path, "rb") as f:
        f.seek(ifd_off)
        if is_big:
            cnt = int.from_bytes(f.read(8), endian)
            esz, val_at, inline = 20, 12, 8
        else:
            cnt = int.from_bytes(f.read(2), endian)
            esz, val_at, inline = 12, 8, 4
        raw = f.read(cnt * esz)
    scalars, arrays = {}, {}
    for i in range(cnt):
        e = i * esz
        tag = int.from_bytes(raw[e:e + 2], endian)
        typ = int.from_bytes(raw[e + 2:e + 4], endian)
        if not (0 < typ < len(_TYPESIZE)):
            continue
        size = _TYPESIZE[typ]
        c = int.from_bytes(raw[e + 4:e + (8 if is_big else 4) + 4], endian) \
            if is_big else int.from_bytes(raw[e + 4:e + 8], endian)
        if c <= 0:
            continue
        valsz = size * c
        if valsz <= inline:
            data = bytes(raw[e + val_at:e + val_at + valsz])
            if c == 1:
                scalars[tag] = int.from_bytes(data[:size], endian)
            else:
                arrays[tag] = (c, size, data)
        else:
            off = int.from_bytes(
                raw[e + val_at:e + val_at + (8 if is_big else 4)], endian)
            if c == 1:
                with open(path, "rb") as f:
                    f.seek(off)
                    scalars[tag] = int.from_bytes(f.read(size), endian)
            else:
                arrays[tag] = (c, size, off)
    return scalars, arrays


def _read_array(path, ref, endian) -> np.ndarray:
    """Resolve array_ref (count, size, bytes|offset) to int64 ndarray."""
    c, size, loc = ref
    if isinstance(loc, (bytes, bytearray)):
        data = bytes(loc)
    else:
        with open(path, "rb") as f:
            f.seek(loc)
            data = f.read(c * size)
    if len(data) < c * size:
        raise PreviewError("TIFF 数组越界/不完整")
    if size == 1:
        dt = np.dtype("u1")
    elif size == 2:
        dt = np.dtype("<u2" if endian == "little" else ">u2")
    elif size == 4:
        dt = np.dtype("<u4" if endian == "little" else ">u4")
    elif size == 8:
        dt = np.dtype("<u8" if endian == "little" else ">u8")
    else:
        raise PreviewError(f"不支持数组元素大小 {size}")
    return np.frombuffer(data, dtype=dt).astype(np.int64)


def probe_tiff(path) -> dict:
    """Dimension / layout probe of a TIFF header (never decodes pixels).

    Returns {W,H,bits,spp,compression,sample_format,photometric,big,tiled,
             planar}. Raises PreviewError if not a readable TIFF.
    """
    p = Path(path)
    if not p.is_file():
        raise PreviewError("场景文件不存在")
    is_big, endian, ifd_off = _parse_tiff_header(path)
    scalars, arrays = _read_ifd(path, is_big, endian, ifd_off)
    W, H = scalars.get(_TAG_W, 0), scalars.get(_TAG_H, 0)
    if not W or not H:
        raise PreviewError("TIFF 缺 ImageWidth/ImageLength")
    bits = scalars.get(_TAG_BITS)
    if bits is None and _TAG_BITS in arrays:   # BitsPerSample 多采样点 → 取第一个
        bits = int(_read_array(path, arrays[_TAG_BITS], endian)[0])
    return {
        "W": W, "H": H,
        "bits": bits if bits is not None else 8,
        "spp": scalars.get(_TAG_SPP, 1),
        "compression": scalars.get(_TAG_COMP, 1),
        "sample_format": scalars.get(_TAG_SF, 1),
        "photometric": scalars.get(_TAG_PHOTO),
        "big": is_big,
        "tiled": (_TAG_TILE_W in scalars or _TAG_TILE_W in arrays),
        "planar": scalars.get(_TAG_PLANAR, 1),
    }


def scene_dims(path) -> dict | None:
    """W/H for a scene path: ENVI .hdr preferred, else TIFF header probe.

    JPG/JPEG（最小原型 §4.7 盘阵里的显示就绪图）走 Pillow 头解析：只读标记段取
    W/H，不解码像素（`Image.open` 本身是懒的，几十 MB 的图也是毫秒级）。
    """
    dims = dims_from_envi_header(path)
    if dims:
        return {"W": dims["W"], "H": dims["H"]}
    suffix = Path(path).suffix.lower()
    if suffix in (".tif", ".tiff"):
        try:
            info = probe_tiff(path)
            return {"W": info["W"], "H": info["H"]}
        except PreviewError:
            return None
    if suffix in (".jpg", ".jpeg"):
        try:
            with Image.open(path) as im:
                return {"W": im.width, "H": im.height}
        except Exception:  # noqa: BLE001 — 坏文件/非图片：无尺寸比抛错好
            return None
    return None


# --------------------------------------------------------------------------
# 稀疏采样（前端 sparseSample / stretchMap 语义的 Python 镜像）
# --------------------------------------------------------------------------
def _np_dtype(bits: int, sf: int, endian: str) -> np.dtype:
    if bits == 8:
        return np.dtype("u1" if sf == 1 else "i1")
    if bits == 16:
        kind = "u2" if sf == 1 else ("i2" if sf == 2 else "f2")
        return np.dtype((("<" if endian == "little" else ">") + kind))
    if bits == 32:
        kind = "u4" if sf == 1 else ("i4" if sf == 2 else "f4")
        return np.dtype((("<" if endian == "little" else ">") + kind))
    raise PreviewError(f"仅支持 8/16/32bit 采样（bits={bits}）")


def sample_strips(path, endian, scalars, arrays, max_edge: int):
    """Sparse-sample an uncompressed single-band strip TIFF to ≤max_edge edge.

    Returns stretched uint8 grayscale (ph, pw). Raises PreviewError when the
    layout is not uncompressed single-band strips.
    """
    W = scalars.get(_TAG_W, 0)
    H = scalars.get(_TAG_H, 0)
    spp = scalars.get(_TAG_SPP, 1)
    bits = scalars.get(_TAG_BITS)
    if bits is None and _TAG_BITS in arrays:
        bits = int(_read_array(path, arrays[_TAG_BITS], endian)[0])
    bits = bits or 8
    sf = scalars.get(_TAG_SF, 1)
    comp = scalars.get(_TAG_COMP, 1)
    photo = scalars.get(_TAG_PHOTO)
    planar = scalars.get(_TAG_PLANAR, 1)
    tiled = (_TAG_TILE_W in scalars or _TAG_TILE_W in arrays)
    if spp != 1:
        raise PreviewError(f"仅支持单波段（spp={spp}）")
    if comp != 1:
        raise PreviewError(f"仅支持无压缩（compression={comp}）")
    if tiled:
        raise PreviewError("不支持 tiled 布局")
    if planar != 1:
        raise PreviewError(f"仅支持 chunky（planar={planar}）")
    bpp = bits // 8
    if bits % 8 or not (1 <= bpp <= 8):
        raise PreviewError(f"采样位深不支持（bits={bits}）")
    rps = scalars.get(_TAG_RPS)
    if not rps:
        rps = H                     # 缺 RowsPerStrip → 整幅单条带
    def strip_ints(tag: int, label: str):
        """StripOffsets/ByteCounts 可能是 count>1 数组或 count==1 标量。"""
        if tag in arrays:
            return _read_array(path, arrays[tag], endian)
        if tag in scalars:
            return np.array([scalars[tag]], dtype=np.int64)
        raise PreviewError(f"缺 {label}")

    offs = strip_ints(_TAG_OFFS, "StripOffsets")
    lens = strip_ints(_TAG_BYTES, "StripByteCounts")
    n_strips = int(np.ceil(H / rps))
    if len(offs) < n_strips or len(lens) < n_strips:
        raise PreviewError("条带数量与图像高不匹配")

    ps = min(1.0, max_edge / max(W, H))
    pw = max(1, int(round(W * ps)))
    ph = max(1, int(round(H * ps)))
    row_bytes = W * bpp
    dt = _np_dtype(bits, sf, endian)

    out = np.empty((ph, pw), dtype=dt)
    col_idx = (np.round(np.arange(pw) * (W - 1) / (pw - 1)).astype(np.int64)
               if pw > 1 else np.zeros(1, dtype=np.int64))
    row_idx = [int(round(i * (H - 1) / (ph - 1))) for i in range(ph)]

    with open(path, "rb") as f:
        for i, r in enumerate(row_idx):
            si = r // rps
            row_off = (r % rps) * row_bytes
            avail = int(lens[si])
            if row_off >= avail:
                raise PreviewError("条带偏移越界")
            length = min(avail - row_off, row_bytes)
            f.seek(int(offs[si]) + row_off)
            data = f.read(length)
            if len(data) < row_bytes and len(data) < length:
                raise PreviewError("读取条带失败（文件截断？）")
            arr = np.frombuffer(data, dtype=dt, count=len(data) // bpp)
            if arr.size < W:
                raise PreviewError("条带宽度小于图像宽度")
            out[i] = arr[col_idx]
    u8 = stretch_2pct(out)
    if photo == 0:
        u8 = 255 - u8
    return u8, ph, pw


def stretch_2pct(arr: np.ndarray) -> np.ndarray:
    """2% Linear stretch（p2..p98 → 0..255；镜像 stretchMap linear2）。

    常量/退化图沿用前端规则：全零 → 黑 0，其它常量 → 中灰 128。
    """
    flat = arr.reshape(-1).astype(np.float64)
    finite = flat[np.isfinite(flat)]
    if finite.size == 0:
        return np.zeros(arr.shape, dtype=np.uint8)
    p2, p98 = np.percentile(finite, [2.0, 98.0])
    if p98 > p2:
        v = (flat - p2) / (p98 - p2) * 255.0
        np.clip(v, 0, 255, out=v)
        return v.reshape(arr.shape).astype(np.uint8)
    return np.full(arr.shape, 0 if p2 == 0 else 128, dtype=np.uint8)


# --------------------------------------------------------------------------
# 出口：Pillow 兜底 / 生成 / 幂等
# --------------------------------------------------------------------------
def _pillow_preview(path, max_edge: int) -> np.ndarray:
    """Pillow fallback for small / non-sparse TIFFs (full decode is guarded)."""
    try:
        with Image.open(path) as im:
            if im.width * im.height > PILLOW_FALLBACK_MAX_PX:
                raise PreviewError(
                    "Pillow 兜底仅限小文件；该图须走无压缩条带稀疏采样")
            im = im.convert("L")
            if max(im.width, im.height) > max_edge:
                im.thumbnail((max_edge, max_edge), Image.LANCZOS)
            return np.asarray(im, dtype=np.uint8)
    except PreviewError:
        raise
    except Exception as e:  # noqa: BLE001 — Pillow 失败转业务错误
        raise PreviewError(f"Pillow 兜底失败：{e}") from e


def build_preview_pixels(path, max_edge: int = PREVIEW_MAX_EDGE) -> np.ndarray:
    """uint8 grayscale preview pixels (h×w) for a scene TIFF.

    Prefers the sparse strip sampler (real-array layout); falls back to Pillow
    for anything the sampler can't handle (compressed / tiled / multi-band),
    as long as it is small enough. Structural errors (corrupt file, unsupported
    bit depth …) raise so the caller reports the real cause.
    """
    p = Path(path)
    if not p.is_file():
        raise PreviewError("场景文件不存在")
    if p.suffix.lower() not in (".tif", ".tiff"):
        raise PreviewError(f"仅支持 TIFF 生成预览（{p.suffix}）")
    is_big, endian, ifd_off = _parse_tiff_header(path)
    scalars, arrays = _read_ifd(path, is_big, endian, ifd_off)
    try:
        pixels, _ph, _pw = sample_strips(path, endian, scalars, arrays,
                                         max_edge)
        return pixels
    except PreviewError as e:
        if is_uncompressed_strip_error(str(e)):
            return _pillow_preview(path, max_edge)
        raise


def ensure_preview_jpg(source_path, jpg_path, max_edge: int = PREVIEW_MAX_EDGE,
                       quality: int = PREVIEW_JPG_QUALITY) -> dict:
    """Generate preview JPEG if missing/stale; idempotent (cache by caller).

    Returns {"status": "generated"|"cached", "path", "w", "h", "error": None}.
    Raises PreviewError on generation failure.
    """
    src, dst = Path(source_path), Path(jpg_path)
    if dst.is_file() and os.path.getmtime(dst) >= os.path.getmtime(src):
        try:
            with Image.open(dst) as im:
                return {"status": "cached", "path": str(dst),
                        "w": im.width, "h": im.height, "error": None}
        except Exception:  # noqa: BLE001 — 缓存损坏按重新生成处理
            pass
    pixels = build_preview_pixels(str(src), max_edge)
    h, w = pixels.shape
    dst.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=dst.name + ".", suffix=".tmp",
                               dir=str(dst.parent))
    try:
        with os.fdopen(fd, "wb") as f:
            Image.fromarray(pixels, mode="L").save(f, format="JPEG",
                                                   quality=quality)
        os.replace(tmp, dst)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
    return {"status": "generated", "path": str(dst), "w": w, "h": h,
            "error": None}


def is_uncompressed_strip_error(msg: str) -> bool:
    return any(k in msg for k in ("无压缩", "tiled", "单波段", "chunky"))
