"""Disk-array scene preview JPEG generation (server-side).

规则 v3（2026-09-20 起）：**长宽各为源图的 1/div**，显示值用**直方图均衡**。
div 由调用方按用户的滑块档位传入（`div ∈ PREVIEW_DIVISORS`，缺省 `LEGACY_PREVIEW_DIV`）。

    ps   = min(1, max_edge / max(W, H))，max_edge = round(max(W,H) / div)
    pw   = round(W * ps),  ph = round(H * ps)
    out(i, j) = src[ round(i * (H-1) / (ph-1)), round(j * (W-1) / (pw-1)) ]

  * 采样逐行进行：只读采样行的条带字节、行内只留采样列，绝不用整个文件（与浏览器
    稀疏路径同一手法）。1/div 采样下恰好只读源文件 1/div 的字节 —— 这是所有读法里最
    省的（块读 / memmap / 整文件顺序读都会多读整数倍，实测更慢）。
  * 读取按行分段并行，**每线程开自己的句柄**（Windows 没有 os.pread，句柄不能共享）。
    这条是为**延迟**而非带宽优化的：1/2 尺度要发约 1.2 万次读，盘阵上单次读若有
    毫秒级延迟，串行就是十几秒，8 路并发把它压回一秒量级；页缓存命中时它只快
    1.4 倍，所以开发机上几乎量不出收益。
  * Display = 直方图均衡，逐式镜像前端 tifDecode 的 stretchMap(mode='equal') +
    computeStats（min..max → 1024 bin 直方图 → CDF 查表）；WhiteIsZero
    (photometric 0) inverted first, matching stretchRgba + invert；常量图沿用前端
    规则（全零 → 黑 0，其它常量 → 中灰 128）。
  * Encoded as grayscale JPEG via Pillow (already a dev dep).

**缓存失效靠写进 JPEG 注释的规则戳**，不是只看 mtime：升级后旧规则烤出来的图
mtime 比源新，只看 mtime 会把它判为有效而永不重烤，真机上换包后看不到任何变化。
规则戳 = PREVIEW_RULE_VERSION + div + quality，改规则/改档位/改质量都要跟着 bump。

Pure-struct TIFF header/IFD parsing — no new runtime dependency, classic TIFF
(42) and BigTIFF (43) both handled. The reader targets the confirmed real
layout (compression=1, one strip per row, single band); compressed / tiled /
multi-band scenes fall back to Pillow for *small* files only (Pillow decodes
fully — a 1.1 GB scene would blow memory) and raise otherwise.

Cache policy lives at the call site (api layer): the caller derives jpg_path;
ensure_preview_jpg() skips generation when an existing file is not older than
the source *and* carries the current rule stamp.
"""

from __future__ import annotations

import os
import re
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
from PIL import Image

#: 预览下采样档位：预览长宽各为源图的 1/div。**档位的唯一真源** ——
#: HTTP 层的 `?div=` 与前端滑块都只能取这里面的值。
PREVIEW_DIVISORS = (2, 4, 8, 16, 32)

#: 缺档位参数时用的值。取 2（逐字节等于 v2 那套「各边 1/2」）而不是产品的默认 4：
#: 只换 backend、没换 dist 的部署不会因为少一个 query 参数就把盘上所有预览判废。
#: 「产品默认 ÷4」只活在前端的 `DEFAULT_PREVIEW_DIV` 一个常量里。
LEGACY_PREVIEW_DIV = 2

PREVIEW_JPG_QUALITY = 85         # 服务端预览质量（显示用；1/2 尺度下 q90 约 117MB）
PILLOW_FALLBACK_MAX_PX = 1 << 26  # 67M px：Pillow 兜底只敢接小文件（整图解码）

#: 规则版本戳：改动「尺寸 / 档位 / 拉伸 / 质量」任一规则都必须 bump，否则旧缓存不会失效。
#: v3（2026-09-20）：尺度从写死的 1/2 变成可调档位，戳里必须带上 div，
#: 否则同一落点上换个档位会被判成缓存命中 —— 界面滑了、盘上的图一个字节都不变。
PREVIEW_RULE_VERSION = "v3"
#: 并行读的行数阈值：小图线程开销盖过收益，保持串行（也让小 fixture 的测试确定）。
PARALLEL_MIN_ROWS = 512
#: 并行度。盘阵若是单块 HDD，磁头竞争可能让更高并发反而更慢，故取保守的 8。
READ_THREADS = 8
#: 直方图均衡的桶数，与前端 computeStats 的 BINS 一致。
EQUAL_BINS = 1024
#: 拉伸的行块大小（4M px ≈ 32MB float64）：避免在 1.5 亿像素量级上整图转 float64。
_STRETCH_CHUNK = 1 << 22

# Pillow 默认的像素上限（8948 万）是防「小文件声明巨大尺寸」的炸弹的，但它会
# **误伤我们自己的产物**：1/2 尺度下 2.4 万像素级的源烤出来就是 1.5 亿像素，超过
# 1× 阈值只是警告，超过 2×（1.79 亿）会直接抛 DecompressionBombError —— 那会让
# cache_hit 的 Image.open 失败、缓存永远判不中，于是每次打开都重烤一遍。
# 这里放到 1<<30（约 10.7 亿，仍能挡住声明 21 亿像素以上的文件），给合法产物留
# 出 10 倍余量。本模块打开的都是本地盘阵上的可信文件。
Image.MAX_IMAGE_PIXELS = 1 << 30

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


def preview_max_edge(source_path, div: int = LEGACY_PREVIEW_DIV) -> int:
    """本次烘焙的长边上限 = 源图长边 / div（不封顶）。

    只读头 / `.hdr` 拿尺寸，极便宜（`scene_dims`），不解码任何像素。

    `div` 非法一律抛 `PreviewError`：HTTP 层会先校验成 400，走到这儿还不合法就是
    调用方漏了校验 —— 宁可报错，也不要拿一个奇怪的尺寸烤出一张图来。
    """
    if div not in PREVIEW_DIVISORS:
        raise PreviewError(
            f"下采样档位非法：{div}（只认 {'/'.join(map(str, PREVIEW_DIVISORS))}）")
    src = Path(source_path)
    if not src.is_file():
        raise PreviewError("场景文件不存在")
    dims = scene_dims(src)
    if not dims:
        raise PreviewError(f"无法读取图像尺寸：{src.name}")
    return max(1, int(round(max(dims["W"], dims["H"]) / div)))


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

    def read_rows(start: int, end: int) -> None:
        """把采样行 [start, end) 读进 out。

        每段开自己的文件句柄 —— 线程间不能共享（seek 位置是句柄状态），而
        Windows 没有 os.pread，所以这是唯一可移植的并行读法。
        """
        with open(path, "rb") as f:
            for i in range(start, end):
                r = row_idx[i]
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

    n_rows = len(row_idx)
    if n_rows >= PARALLEL_MIN_ROWS:
        step = -(-n_rows // READ_THREADS)          # ceil，均分成 READ_THREADS 段
        spans = [(s, min(s + step, n_rows)) for s in range(0, n_rows, step)]
        with ThreadPoolExecutor(max_workers=len(spans)) as ex:
            # 必须消费 map 的结果：异常在线程里抛出，迭代时才转交给调用方
            list(ex.map(lambda se: read_rows(*se), spans))
    else:
        read_rows(0, n_rows)

    u8 = stretch_equal(out)
    if photo == 0:
        u8 = 255 - u8
    return u8, ph, pw


def _finite_of(chunk: np.ndarray, is_float: bool) -> np.ndarray:
    """统计用视图：丢掉非有限值（镜像 computeStats 里 `v===v && isFinite(v)` 跳过）。"""
    if not is_float:
        return chunk
    mask = np.isfinite(chunk)
    return chunk[mask] if not mask.all() else chunk


def stretch_equal(arr: np.ndarray) -> np.ndarray:
    """直方图均衡（镜像前端 stretchMap(mode='equal') + computeStats）。

    逐式对齐前端，两处公式故意不同、不要"顺手统一"：
      * 直方图入桶：`q = ((v - lo) * BINS / (hi - lo)) | 0`（computeStats 的 sc）
      * 查表下标：  `k = ((v - lo) / (hi - lo) * (BINS - 1)) | 0`（stretchMap 的 t）
    常量/退化图沿用前端规则：全零 → 黑 0，其它常量 → 中灰 128。

    按行块处理：1/2 尺度下 out 有 1.5 亿像素，整图 astype(np.float64) 会产生
    1.2GB 副本，分块后额外峰值只有 ~32MB。
    """
    flat = arr.reshape(-1)
    is_float = arr.dtype.kind == "f"
    if is_float:
        finite = flat[np.isfinite(flat)]
        if finite.size == 0:
            return np.zeros(arr.shape, dtype=np.uint8)
        lo, hi = float(finite.min()), float(finite.max())
    else:
        lo, hi = float(flat.min()), float(flat.max())

    out = np.empty(flat.shape, dtype=np.uint8)
    if not hi > lo:
        out.fill(0 if lo == 0 else 128)
        return out.reshape(arr.shape)

    hist = np.zeros(EQUAL_BINS, dtype=np.int64)
    sc = EQUAL_BINS / (hi - lo)
    for s in range(0, flat.size, _STRETCH_CHUNK):
        c = np.asarray(_finite_of(flat[s:s + _STRETCH_CHUNK], is_float),
                       dtype=np.float64)
        if c.size == 0:
            continue
        q = ((c - lo) * sc).astype(np.int64)
        np.clip(q, 0, EQUAL_BINS - 1, out=q)
        hist += np.bincount(q, minlength=EQUAL_BINS)

    total = int(hist.sum())
    if total == 0:
        return np.zeros(arr.shape, dtype=np.uint8)
    # 前端把结果写进 Uint8ClampedArray，那是「四舍五入（ties-to-even）」；
    # 直接 astype(uint8) 是截断，会整体差 1 个灰阶。np.rint 同为 ties-to-even。
    lut = np.clip(np.rint(np.cumsum(hist) / total * 255.0), 0, 255).astype(np.uint8)

    for s in range(0, flat.size, _STRETCH_CHUNK):
        c = np.asarray(flat[s:s + _STRETCH_CHUNK], dtype=np.float64)
        t = (c - lo) / (hi - lo)
        np.clip(t, 0.0, 1.0, out=t)
        # NaN 在剪裁后仍是 NaN，而 `(NaN * n) | 0` 在前端是 0 → 这里同样落到 0 号桶
        np.nan_to_num(t, copy=False, nan=0.0, posinf=1.0, neginf=0.0)
        k = (t * (EQUAL_BINS - 1)).astype(np.int64)
        np.clip(k, 0, EQUAL_BINS - 1, out=k)
        out[s:s + _STRETCH_CHUNK] = lut[k]
    return out.reshape(arr.shape)


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


def build_preview_pixels(path, max_edge: int | None = None) -> np.ndarray:
    """uint8 grayscale preview pixels (h×w) for a scene TIFF.

    max_edge 省略时按 `LEGACY_PREVIEW_DIV`（各边 1/2）从源图尺寸推出 —— 只有直接
    调本函数的地方（测试）会走那个分支；`ensure_preview_jpg` 一定显式算好再传进来。

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
    if max_edge is None:
        max_edge = preview_max_edge(p)
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


def rule_stamp(quality: int = PREVIEW_JPG_QUALITY,
               div: int = LEGACY_PREVIEW_DIV) -> bytes:
    """当前烘焙规则的签名（写进 JPEG 注释，用来判缓存是否还符合现规则）。

    **div 进戳**（v3 起）：同一个落点上换个档位必须判废重烤 —— 只按落点判的话，
    界面上的滑块动了、盘上那张图一个字节都不会变。v2 那代戳是 `…:half+equal:…`，
    与这里的 `…:div2+equal:…` 字符串本就不同，所以 v2 产物一律重烤一轮（预期内）。
    """
    return f"srprev:{PREVIEW_RULE_VERSION}:div{div}+equal:q{quality}".encode("ascii")


#: 规则戳的形态。`ver` 宽松匹配（v1 那代是 `8192+linear2`，本式匹配不上）。
_STAMP_RE = re.compile(r"^srprev:(?P<ver>[^:]+):div(?P<div>\d+)\+equal:q(?P<q>\d+)$")


def stamp_div(stamp) -> int | None:
    """解出这份产物**是按哪一档烤的**；解不出（缺戳 / 旧格式 / 非法档位）→ None。

    场景行的 `previewDiv` 用它：前端据「盘上这份的档位 ≠ 当前档位」决定要不要重烤。
    v2 那代戳解不出 div —— 返回 None 会让前端重烤一次，这正是想要的
    （那代图迟早要按新档位重烤，而且换包后本来就要烤一轮）。
    """
    if isinstance(stamp, (bytes, bytearray)):
        try:
            stamp = bytes(stamp).decode("ascii")
        except UnicodeDecodeError:
            return None
    m = _STAMP_RE.match(str(stamp or "").strip())
    if not m:
        return None
    div = int(m.group("div"))
    return div if div in PREVIEW_DIVISORS else None


def _comment_of(jpg_path):
    """JPEG 注释原文（规范化成 str）；读不出 → None。

    只读文件头，**不解码像素**。`info["comment"]` 由 Pillow 从 COM 段取，形态随
    Pillow 版本可能是 bytes 也可能是 str，所以在这里统一成 str —— 下游（`stamp_div`
    / `has_rule_stamp`）就不必各写一遍 bytes 分支。多段 COM 时 Pillow 可能给出
    bytes 以外的形态（元组/列表），那种情况 `str()` 之后前缀判不过、就是「判不出」，
    对调用方都是安全的保守结论。
    """
    try:
        with Image.open(jpg_path) as im:
            stamp = im.info.get("comment")
    except Exception:  # noqa: BLE001 — 读不出就当「不知道」
        return None
    if stamp is None:
        return None
    if isinstance(stamp, (bytes, bytearray)):
        try:
            return bytes(stamp).decode("ascii")
        except UnicodeDecodeError:
            return None
    return str(stamp)


def preview_div_of(jpg_path) -> int | None:
    """盘上某个预览 JPG 是按哪一档烤的；文件不在 / 读不出 / 旧格式 → None。

    只 `Image.open` 读头拿注释，**不解码像素** —— 与 `scene_dims` 同级，可以在
    列表路径上逐行调。
    """
    return stamp_div(_comment_of(jpg_path))


#: 规则戳的前缀。三代戳都以此开头 —— v1 那代是 `srprev:8192+linear2`（`_STAMP_RE`
#: 那套严格形态匹配不上它，但前缀照样在），所以「这份文件是不是我们烤的」只判前缀。
_STAMP_PREFIX = "srprev:"


def has_rule_stamp(jpg_path) -> bool:
    """这份 JPG 带不带**本平台的烘焙规则戳**（只读头，不解码像素）。

    判据取「前缀是 `srprev:`」而不是 `_STAMP_RE` 那套完整形态：后者认不出 v1 那代
    （`srprev:8192+linear2` 没有 `:divN+equal:qN` 尾巴），而 v1/v2 烤的图同样是
    我们写的、同样该被清理。读不出注释（不是 JPEG / 没戳 / 文件不在）→ False。

    只有一处调用方：**删除前**的判据（`services/preview_clear.py`）。它的意义是
    「只删自己烤出来的那份」—— 盘阵上别人手放的 `*_preview.jpg`、以及万一后缀
    恰好叫 `preview` 的**产物**（`<源 stem>_preview.jpg` 会与产物同名），都没有这
    个戳，因此一个都不会被误删。
    """
    return str(_comment_of(jpg_path) or "").strip().startswith(_STAMP_PREFIX)


def cache_hit(dst: Path, quality: int, div: int) -> dict | None:
    """缓存可用则返回 {w, h}，否则 None（缺戳 / 旧规则 / 换档 / 损坏一律按需重烤）。

    公开（无下划线）是因为**产物急烤**（`api/app.py::_eager_bake_tick`）要在动手读
    大图之前先问一遍：用户可能刚打开过这份产物、盘上那份就是当前档位的 —— 那时
    再读一遍 GB 级文件纯属白干。判据本身与惰性路径**逐字相同**，没有第二套。
    """
    try:
        with Image.open(dst) as im:
            stamp = im.info.get("comment")
            w, h = im.width, im.height
    except Exception:  # noqa: BLE001 — 缓存损坏按重新生成处理
        return None
    if stamp != rule_stamp(quality, div):
        return None
    return {"w": w, "h": h}


def write_preview_jpg(jpg_path, pixels, *,
                      quality: int = PREVIEW_JPG_QUALITY,
                      div: int = LEGACY_PREVIEW_DIV) -> dict:
    """把灰度像素落成预览 JPEG（先写临时文件再 `os.replace`，原子替换）。

    从 `ensure_preview_jpg` 里抽出来，是为了让**产物急烤**能在「像素已读出」与
    「落盘」之间插一次源文件复核（见 `api/app.py::_eager_bake_tick`）：作业重跑会
    覆盖同一个产物路径，若不复核就可能把一份半截产物的图永久留在盘上，而缓存判据
    是「不比源旧」，它不会自愈。

    规则戳按 `div` 写进 JPEG 注释 —— 与 `ensure_preview_jpg` 同一件事，别在这里
    另起一套。返回 `{path, w, h}`。
    """
    dst = Path(jpg_path)
    h, w = pixels.shape
    dst.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=dst.name + ".", suffix=".tmp",
                               dir=str(dst.parent))
    try:
        with os.fdopen(fd, "wb") as f:
            Image.fromarray(pixels).save(f, format="JPEG", quality=quality,
                                         comment=rule_stamp(quality, div))
        os.replace(tmp, dst)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
    return {"path": str(dst), "w": w, "h": h}


def ensure_preview_jpg(source_path, jpg_path, div: int = LEGACY_PREVIEW_DIV,
                       quality: int = PREVIEW_JPG_QUALITY) -> dict:
    """Generate preview JPEG if missing/stale; idempotent (cache by caller).

    命中要求两条同时成立：**不比源旧**，且**规则戳等于当前规则**（含档位）。只看
    mtime 会让升级前烤的图永远不重烤 —— 它的 mtime 就是比源新。

    `div` 是**尺寸与规则戳的共同来源**（不是分开的两个参数）：两者只要有一处不同步，
    就会出现「尺寸换了但戳没换 → 缓存永远命中 → 界面滑了盘上不动」这种哑火，所以
    干脆不给它们分开的机会。

    Returns {"status": "generated"|"cached", "path", "w", "h", "error": None}.
    Raises PreviewError on generation failure.
    """
    src, dst = Path(source_path), Path(jpg_path)
    max_edge = preview_max_edge(src, div)      # 顺带校验 div 合法
    if dst.is_file() and os.path.getmtime(dst) >= os.path.getmtime(src):
        hit = cache_hit(dst, quality, div)
        if hit is not None:
            return {"status": "cached", "path": str(dst),
                    "w": hit["w"], "h": hit["h"], "error": None}
    pixels = build_preview_pixels(str(src), max_edge)
    out = write_preview_jpg(dst, pixels, quality=quality, div=div)
    return {"status": "generated", "path": out["path"],
            "w": out["w"], "h": out["h"], "error": None}


def is_uncompressed_strip_error(msg: str) -> bool:
    return any(k in msg for k in ("无压缩", "tiled", "单波段", "chunky"))
