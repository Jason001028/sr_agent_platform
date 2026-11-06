"""Bounded-memory Mask IO for the batch pipeline (D2).

The Mask contract is fixed by ``batch_contracts``:

* single-band ``uint8`` TIFF,
* background 0 / foreground 255,
* exactly the PAN width and height,
* at least one foreground pixel,
* classic stripped TIFF, Deflate or uncompressed,
* published atomically (a temp file, then ``os.replace``; or a temp directory,
  then one directory rename).

The writer rasterizes candidates strip by strip, so peak memory is bounded by
``width * rows_per_strip`` plus the lightweight overlay, not by the full image.
This is what lets a large PAN produce a mask on the backend without loading a
full-size array.  The reader mirrors the writer and verifies strips
incrementally; unsupported TIFF layouts fall back to Pillow for legacy masks.

BBoxes use Pillow's inclusive ``rectangle``, so a 1x1 box at the last pixel is a
real foreground pixel.  Polygons use the same ``ImageDraw.polygon`` primitive as
``backend.services.mask``, keeping the viewer/ROI fill convention.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import struct
import threading
import time
import zlib

import numpy as np
from PIL import Image, ImageDraw

from backend.services import batch_contracts as contracts
from backend.services import preview_jpg

# Re-exported so callers keep importing the error from this module.
MaskError = contracts.MaskError

_DEFAULT_ROWS_PER_STRIP = 256
_DEFAULT_OVERLAY_MAX_EDGE = 2048
_COMPRESSION_NONE = 1
_COMPRESSION_DEFLATE = 8

# Resource bounds.  The verifier must never turn a claimed strip size or a
# deflate stream into an unbounded allocation; the Pillow fallback is a
# whole-image decode, so it is refused above the same small-file budget the
# preview path uses.
MAX_ROWS_PER_STRIP = 65536
MIN_OVERLAY_MAX_EDGE = 16
MAX_OVERLAY_MAX_EDGE = 8192
MAX_STRIP_PIXELS = 1 << 26          # 67M px ≈ 128 MiB for uint16 / 67 MiB uint8
MAX_PILLOW_VERIFY_PIXELS = 1 << 26
# Deflate worst-case expansion for incompressible data is ~input + input/1000
# + 12 bytes; the extra MiB leaves room for zlib framing and tiny strips.
_MAX_DEFLATE_OVERHEAD = 1 << 20


class _UnsupportedTiff(Exception):
    """The TIFF layout is valid but outside the streaming verifier."""


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------

def _positive_int(value, label, default=None):
    if value is None:
        if default is None:
            raise MaskError(f"{label} 必须是正整数")
        return default
    try:
        return contracts.positive_int(value, label)
    except contracts.ContractError as exc:
        raise MaskError(str(exc)) from exc


def _bounded_int(value, label, low, high):
    """Validate one configuration integer and its documented range."""
    try:
        number = contracts.positive_int(value, label)
    except contracts.ContractError as exc:
        raise MaskError(str(exc)) from exc
    if not low <= number <= high:
        raise MaskError(f"{label} 必须在 {low}..{high} 之间：{number}")
    return number


def _env_int(name, default, low, high):
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    text = raw.strip()
    try:
        # ``positive_int`` deliberately rejects strings, so parse first: the
        # environment is always a string, and a value like "64" is legal.
        value = int(text, 10)
    except ValueError as exc:
        raise MaskError(f"{name} 配置非法：需要整数，实际 {text!r}") from exc
    return _bounded_int(value, name, low, high)


def _normalized_candidates(candidates, width, height):
    try:
        return contracts.normalize_candidates(candidates, width, height)
    except contracts.ContractError as exc:
        raise MaskError(str(exc)) from exc


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


# ---------------------------------------------------------------------------
# strip writer
# ---------------------------------------------------------------------------

def _strip_image(width, strip_height, candidates, start_row):
    """Rasterize one horizontal strip in local (strip-row) coordinates."""
    image = Image.new(
        "L", (width, strip_height), contracts.BACKGROUND_VALUE)
    draw = ImageDraw.Draw(image)
    end_row = start_row + strip_height - 1
    for candidate in candidates:
        kind, geometry = contracts.candidate_geometry(candidate)
        if kind == "bbox":
            x0, y0, x1, y1 = geometry
            y0 = max(y0, start_row)
            y1 = min(y1, end_row)
            if y0 > y1:
                continue
            draw.rectangle(
                [x0, y0 - start_row, x1, y1 - start_row],
                fill=contracts.FOREGROUND_VALUE,
            )
        else:
            points = [(x, y - start_row) for x, y in geometry]
            if (max(y for _, y in points) < 0
                    or min(y for _, y in points) >= strip_height):
                continue
            draw.polygon(points, fill=contracts.FOREGROUND_VALUE)
    return image


def _overlay_rows(arr, width, start, strip_height, scale, overlay_width):
    """Yield ``(overlay_y, row_max)`` for one strip.

    Block-max (not point sampling) so a 1-pixel candidate still shows up in the
    preview.  Memory stays at one overlay row plus the strip's pixel buffer.
    """
    first = start // scale
    last = (start + strip_height - 1) // scale
    padded_width = overlay_width * scale
    for overlay_y in range(first, last + 1):
        row_start = max(start, overlay_y * scale)
        row_end = min(start + strip_height, (overlay_y + 1) * scale)
        block = arr[(row_start - start) * width:(row_end - start) * width]
        block = block.reshape(row_end - row_start, width)
        if block.shape[1] < padded_width:
            padded = np.zeros(
                (block.shape[0], padded_width), dtype=np.uint8)
            padded[:, :block.shape[1]] = block
            block = padded
        block = block.reshape(block.shape[0], overlay_width, scale)
        yield overlay_y, block.max(axis=(0, 2))


def _write_ifd(stream, ifd_offset, width, height, rows_per_strip,
               offsets, counts, compression):
    entries = [
        (256, 4, 1, width),          # ImageWidth (LONG)
        (257, 4, 1, height),         # ImageLength (LONG)
        (258, 3, 1, 8),              # BitsPerSample (SHORT)
        (259, 3, 1, compression),    # Compression (SHORT)
        (262, 3, 1, 1),              # Photometric = BlackIsZero
        (273, 4, len(offsets), 0),   # StripOffsets (patched below)
        (277, 3, 1, 1),              # SamplesPerPixel
        (278, 4, 1, rows_per_strip),  # RowsPerStrip
        (279, 4, len(counts), 0),    # StripByteCounts (patched below)
        (284, 3, 1, 1),              # PlanarConfiguration = chunky
        (339, 3, 1, 1),              # SampleFormat = unsigned integer
    ]
    entry_count = len(entries)
    ifd_size = 2 + 12 * entry_count + 4
    arrays_offset = ifd_offset + ifd_size
    # A LONG with count=1 is stored inline in the IFD value field; only a real
    # array (count>1) points at a separate block after the IFD.
    if len(offsets) > 1:
        entries[5] = (273, 4, len(offsets), arrays_offset)
        entries[8] = (279, 4, len(counts),
                      arrays_offset + 4 * len(offsets))
    else:
        entries[5] = (273, 4, 1, offsets[0])
        entries[8] = (279, 4, 1, counts[0])

    stream.write(struct.pack("<H", entry_count))
    for tag, field_type, count, value in entries:
        stream.write(struct.pack("<HHI", tag, field_type, count))
        if field_type == 3 and count == 1:
            stream.write(struct.pack("<H", value) + b"\x00\x00")
        else:
            stream.write(struct.pack("<I", value))
    stream.write(struct.pack("<I", 0))  # next IFD
    if len(offsets) > 1:
        stream.write(struct.pack("<" + "I" * len(offsets), *offsets))
        stream.write(struct.pack("<" + "I" * len(counts), *counts))


def _write_strips(path, *, width, height, candidates, rows_per_strip,
                  overlay_max_edge, overlay_path):
    if overlay_path is not None:
        scale = max(1, math.ceil(max(width, height) / overlay_max_edge))
        overlay_width = max(1, math.ceil(width / scale))
        overlay_height = max(1, math.ceil(height / scale))
        overlay = np.zeros((overlay_height, overlay_width), dtype=np.uint8)
    else:
        # The legacy ``build_mask`` call has no preview; skip the overlay
        # allocation and block-max work entirely.
        scale = 1
        overlay_width = overlay_height = 0
        overlay = None
    foreground = 0
    offsets = []
    counts = []
    with open(path, "wb") as stream:
        # Classic little-endian TIFF header; the IFD offset is patched later.
        stream.write(b"II" + struct.pack("<H", 42) + struct.pack("<I", 0))
        for start in range(0, height, rows_per_strip):
            strip_height = min(rows_per_strip, height - start)
            image = _strip_image(width, strip_height, candidates, start)
            raw = image.tobytes()
            if len(raw) != strip_height * width:
                raise MaskError("条带字节数与尺寸不一致")
            arr = np.frombuffer(raw, dtype=np.uint8)
            foreground += int(np.count_nonzero(arr))
            if overlay is not None:
                for overlay_y, row in _overlay_rows(
                        arr, width, start, strip_height, scale, overlay_width):
                    overlay[overlay_y] = np.maximum(overlay[overlay_y], row)
            compressed = zlib.compress(raw, 9)
            offsets.append(stream.tell())
            counts.append(len(compressed))
            stream.write(compressed)
        if offsets and offsets[-1] + counts[-1] >= 2 ** 32:
            raise MaskError("掩码超过经典 TIFF 4GB 上限，需 BigTIFF")
        ifd_offset = stream.tell()
        # IFD + the strip offset/count arrays must also fit a 32-bit offset.
        tail = 2 + 12 * 11 + 4 + 8 * len(offsets)
        if ifd_offset + tail >= 2 ** 32:
            raise MaskError("掩码超过经典 TIFF 4GB 上限，需 BigTIFF")
        _write_ifd(stream, ifd_offset, width, height, rows_per_strip,
                   offsets, counts, _COMPRESSION_DEFLATE)
        stream.seek(4)
        stream.write(struct.pack("<I", ifd_offset))
    if overlay is not None:
        Image.fromarray(overlay).save(overlay_path, format="PNG")
    return {
        "foreground_pixels": foreground,
        "overlay_width": overlay_width,
        "overlay_height": overlay_height,
        "overlay_scale": scale,
        "compression": _COMPRESSION_DEFLATE,
        "strip_count": len(offsets),
    }


# ---------------------------------------------------------------------------
# streaming verifier
# ---------------------------------------------------------------------------

def _read_ifd(stream, endian, ifd_offset):
    stream.seek(ifd_offset)
    raw_count = stream.read(2)
    if len(raw_count) != 2:
        raise MaskError("TIFF IFD 截断")
    entry_count = struct.unpack(endian + "H", raw_count)[0]
    entries = {}
    for _ in range(entry_count):
        raw = stream.read(12)
        if len(raw) != 12:
            raise MaskError("TIFF IFD 截断")
        tag, field_type, count = struct.unpack(endian + "HHI", raw[:8])
        entries[tag] = (field_type, count, raw[8:12])
    return entries


def _scalar(entries, endian, tag, default=None):
    item = entries.get(tag)
    if item is None:
        return default
    field_type, count, value = item
    if count != 1:
        raise MaskError(f"TIFF tag {tag} 不是标量")
    if field_type == 3:
        return struct.unpack(endian + "H", value[:2])[0]
    if field_type == 4:
        return struct.unpack(endian + "I", value)[0]
    if field_type == 1:
        return value[0]
    raise _UnsupportedTiff(f"TIFF tag {tag} 类型 {field_type}")


def _array(stream, endian, item, label, *, limit):
    field_type, count, value = item
    if field_type == 3:
        fmt, size = "H", 2
    elif field_type == 4:
        fmt, size = "I", 4
    else:
        raise _UnsupportedTiff(f"TIFF {label} 类型 {field_type}")
    # ``count`` is attacker-controlled until checked against the layout the
    # caller expects; never use it to size an unbounded read.
    if count < 0 or count > limit:
        raise MaskError(
            f"TIFF {label} 数量 {count} 超出预期上限 {limit}")
    if count == 1:
        return [struct.unpack(endian + fmt, value[:size])[0]]
    offset = struct.unpack(endian + "I", value)[0]
    stream.seek(offset)
    data = stream.read(size * count)
    if len(data) != size * count:
        raise MaskError(f"TIFF {label} 截断")
    return list(struct.unpack(endian + fmt * count, data))


def _decompress_strip(data, expected, index):
    """Deflate one strip with a hard output cap; never expand past expected."""
    try:
        decompressor = zlib.decompressobj()
        out = decompressor.decompress(data, expected + 1)
    except zlib.error as exc:
        raise MaskError(f"条带 {index} 解压失败：{exc}") from exc
    if len(out) > expected or decompressor.unconsumed_tail:
        raise MaskError(
            f"条带 {index} 解压结果超出尺寸预算 "
            f"（预期 {expected} 字节，已解出 {len(out)}）")
    if not decompressor.eof:
        raise MaskError(f"条带 {index} 解压流不完整")
    return out


def _verify_stream_tiff(path, width, height):
    """Verify a classic stripped TIFF without loading the whole image."""
    with open(path, "rb") as stream:
        file_size = os.fstat(stream.fileno()).st_size
        header = stream.read(8)
        if len(header) < 8:
            raise MaskError("掩码不是完整 TIFF")
        if header[:2] == b"II":
            endian = "<"
        elif header[:2] == b"MM":
            endian = ">"
        else:
            raise _UnsupportedTiff("不是 TIFF 字节序")
        version = struct.unpack(endian + "H", header[2:4])[0]
        if version == 43:
            raise _UnsupportedTiff("BigTIFF")
        if version != 42:
            raise _UnsupportedTiff(f"未知 TIFF 版本 {version}")
        ifd_offset = struct.unpack(endian + "I", header[4:8])[0]
        entries = _read_ifd(stream, endian, ifd_offset)

        actual_width = _scalar(entries, endian, 256)
        actual_height = _scalar(entries, endian, 257)
        if (actual_width, actual_height) != (width, height):
            raise MaskError(
                f"掩码尺寸 {(actual_width, actual_height)} 与 PAN "
                f"{(width, height)} 不一致")
        bits = _scalar(entries, endian, 258, 8)
        compression = _scalar(entries, endian, 259, _COMPRESSION_NONE)
        photometric = _scalar(entries, endian, 262, 1)
        samples = _scalar(entries, endian, 277, 1)
        rows_per_strip = _scalar(entries, endian, 278, actual_height)
        planar = _scalar(entries, endian, 284, 1)
        sample_format = _scalar(entries, endian, 339, 1)
        if 322 in entries or 323 in entries:
            raise _UnsupportedTiff("tiled TIFF")
        if bits != 8:
            raise MaskError(f"掩码必须是 8 位，实际 {bits}")
        if samples != 1:
            raise MaskError(f"掩码必须是单波段，实际 {samples}")
        if planar != 1:
            raise MaskError("掩码必须是 chunky 布局")
        if sample_format != 1:
            raise MaskError("掩码必须是无符号整数")
        if photometric != 1:
            raise MaskError("掩码必须是 BlackIsZero 光度")
        if compression not in (_COMPRESSION_NONE, _COMPRESSION_DEFLATE):
            raise _UnsupportedTiff(f"不支持的压缩 {compression}")

        if not isinstance(rows_per_strip, int) or rows_per_strip <= 0:
            raise MaskError("RowsPerStrip 非法")
        expected = math.ceil(actual_height / rows_per_strip)
        if rows_per_strip * actual_width > MAX_STRIP_PIXELS:
            raise _UnsupportedTiff(
                "RowsPerStrip 过大，流式校验单条带超出内存预算")
        offsets_item = entries.get(273)
        counts_item = entries.get(279)
        if offsets_item is None or counts_item is None:
            raise _UnsupportedTiff("缺少 StripOffsets/StripByteCounts")
        offsets = _array(stream, endian, offsets_item, "StripOffsets",
                         limit=expected)
        counts = _array(stream, endian, counts_item, "StripByteCounts",
                        limit=expected)
        if len(offsets) != expected or len(counts) != expected:
            raise MaskError(
                f"条带数量 {len(offsets)}/{len(counts)} 与预期 {expected} 不一致")

        foreground = 0
        for index, (offset, count) in enumerate(zip(offsets, counts)):
            rows = min(rows_per_strip, actual_height - index * rows_per_strip)
            expected_bytes = rows * actual_width
            if offset < 0 or count < 0 or offset + count > file_size:
                raise MaskError("条带偏移/长度越出文件范围")
            # A compressed strip can legitimately be slightly larger than the
            # raw bytes (incompressible data + zlib framing), but not without
            # bound; this prevents a claimed multi-GB strip from being read.
            if count > expected_bytes + _MAX_DEFLATE_OVERHEAD:
                raise MaskError(
                    f"条带 {index} 长度 {count} 超出解压预算 {expected_bytes}")
            stream.seek(offset)
            data = stream.read(count)
            if len(data) != count:
                raise MaskError("条带截断")
            if compression == _COMPRESSION_DEFLATE:
                data = _decompress_strip(data, expected_bytes, index)
            if len(data) != expected_bytes:
                raise MaskError("条带长度与尺寸不一致")
            if data.translate(None, b"\x00\xff"):
                raise MaskError("掩码只允许 0/255")
            foreground += data.count(b"\xff")
        if foreground <= 0:
            raise MaskError("掩码没有前景像素")
        return {
            "contract_version": contracts.MASK_CONTRACT_VERSION,
            "coordinate_space": contracts.COORDINATE_SPACE,
            "width": actual_width,
            "height": actual_height,
            "dtype": contracts.MASK_DTYPE,
            "bits": 8,
            "spp": 1,
            "background_value": contracts.BACKGROUND_VALUE,
            "foreground_value": contracts.FOREGROUND_VALUE,
            "foreground_pixels": foreground,
            "compression": ("deflate" if compression == _COMPRESSION_DEFLATE
                            else "none"),
            "strip_count": len(offsets),
        }


def _check_with_pillow(path, width, height):
    if width * height > MAX_PILLOW_VERIFY_PIXELS:
        raise MaskError(
            "掩码布局需要 Pillow 整图解码，超出有界内存预算；"
            "批量路径只接受经典 stripped TIFF")
    try:
        with Image.open(path) as image:
            if image.size != (width, height):
                raise MaskError(
                    f"掩码尺寸 {image.size} 与 PAN {(width, height)} 不一致")
            if image.width * image.height > MAX_PILLOW_VERIFY_PIXELS:
                raise MaskError("掩码整图解码超出有界内存预算")
            if image.mode != "L":
                raise MaskError(f"掩码必须是单波段 uint8，实际 {image.mode}")
            arr = np.asarray(image)
            if arr.ndim != 2:
                raise MaskError(f"掩码必须是二维单波段，实际 ndim={arr.ndim}")
            values = set(np.unique(arr).tolist())
            if not values.issubset({0, 255}):
                raise MaskError(f"掩码只允许 0/255，实际 {sorted(values)[:8]}")
            foreground = int(np.count_nonzero(arr))
            if foreground <= 0:
                raise MaskError("掩码没有前景像素")
    except MaskError:
        raise
    except Exception as exc:
        raise MaskError(
            f"无法读取掩码 TIFF：{type(exc).__name__}: {exc}") from exc
    return {
        "contract_version": contracts.MASK_CONTRACT_VERSION,
        "coordinate_space": contracts.COORDINATE_SPACE,
        "width": width,
        "height": height,
        "dtype": contracts.MASK_DTYPE,
        "bits": 8,
        "spp": 1,
        "background_value": contracts.BACKGROUND_VALUE,
        "foreground_value": contracts.FOREGROUND_VALUE,
        "foreground_pixels": foreground,
        "compression": "pillow",
        "strip_count": None,
    }


# ---------------------------------------------------------------------------
# public API
# ---------------------------------------------------------------------------

def build_mask(path, *, width, height, candidates, provenance="mock",
               rows_per_strip=None, overlay_path=None, overlay_max_edge=None,
               source=None, detection=None) -> dict:
    """Rasterize candidates and publish one contract-checked mask TIFF.

    ``width``/``height`` must be the PAN dimensions (K6): the SR adapter
    compares the mask against the PAN, not against the lower-resolution NOSR.

    ``overlay_path`` and the ``source``/``detection`` metadata are optional so
    the PR C call shape (and its tests) keeps working; ``build_mask_result`` is
    the full D2 entry point and always publishes the overlay and manifests.
    """
    width = _positive_int(width, "掩码宽度")
    height = _positive_int(height, "掩码高度")
    rows_per_strip = _bounded_int(
        rows_per_strip, "每条约带行数", 1, MAX_ROWS_PER_STRIP) if (
            rows_per_strip is not None) else _env_int(
                "SR_BATCH_MASK_ROWS_PER_STRIP", _DEFAULT_ROWS_PER_STRIP,
                1, MAX_ROWS_PER_STRIP)
    overlay_max_edge = _bounded_int(
        overlay_max_edge, "Overlay 最大边长",
        MIN_OVERLAY_MAX_EDGE, MAX_OVERLAY_MAX_EDGE) if (
            overlay_max_edge is not None) else _env_int(
                "SR_BATCH_MASK_OVERLAY_MAX_EDGE", _DEFAULT_OVERLAY_MAX_EDGE,
                MIN_OVERLAY_MAX_EDGE, MAX_OVERLAY_MAX_EDGE)
    rows_per_strip = min(rows_per_strip, height)
    if width * rows_per_strip > MAX_STRIP_PIXELS:
        # The writer is bounded per strip, but an explicit configuration can
        # still ask for a strip that is effectively a whole-image allocation.
        rows_per_strip = max(1, MAX_STRIP_PIXELS // width)
    if height <= 0 or rows_per_strip <= 0:
        raise MaskError("条带行数计算非法")
    normalized, diagnostics = _normalized_candidates(
        candidates, width, height)
    if not normalized:
        raise MaskError("没有候选框，拒绝生成空掩码")

    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.stem + ".tmp" + out.suffix)
    overlay_out = Path(overlay_path) if overlay_path else None
    tmp_overlay = None
    if overlay_out is not None:
        overlay_out.parent.mkdir(parents=True, exist_ok=True)
        tmp_overlay = overlay_out.with_name(
            overlay_out.stem + ".tmp" + overlay_out.suffix)
    try:
        write = _write_strips(
            tmp, width=width, height=height, candidates=normalized,
            rows_per_strip=rows_per_strip,
            overlay_max_edge=overlay_max_edge,
            overlay_path=tmp_overlay)
        try:
            checked = _verify_stream_tiff(tmp, width, height)
        except _UnsupportedTiff as exc:
            raise MaskError(f"写入的掩码布局不受支持：{exc}") from exc
        if checked["foreground_pixels"] != write["foreground_pixels"]:
            raise MaskError("掩码前景像素数与写入结果不一致")
        # Publish the preview first and the authoritative mask last, so a
        # failed preview replacement never leaves a mask that looks published.
        if tmp_overlay is not None:
            os.replace(tmp_overlay, overlay_out)
        os.replace(tmp, out)
    finally:
        for temp in (tmp, tmp_overlay):
            if temp is None:
                continue
            try:
                if temp.exists():
                    temp.unlink()
            except OSError:
                pass

    result = {
        "contract_version": contracts.MASK_CONTRACT_VERSION,
        "path": out.as_posix(),
        "sha256": _sha256(out),
        "revision": preview_jpg.source_revision(out),
        "width": width,
        "height": height,
        "dtype": contracts.MASK_DTYPE,
        "bits": 8,
        "spp": 1,
        "background_value": contracts.BACKGROUND_VALUE,
        "foreground_value": contracts.FOREGROUND_VALUE,
        "coordinate_space": contracts.COORDINATE_SPACE,
        "foreground_pixels": write["foreground_pixels"],
        "candidates": len(normalized),
        "candidate_count": len(normalized),
        "builder_name": contracts.MASK_BUILDER_NAME,
        "builder_version": contracts.MASK_BUILDER_VERSION,
        "provenance": provenance,
        "diagnostics": diagnostics,
        "compression": "deflate",
        "strip_count": write["strip_count"],
    }
    if overlay_out is not None:
        result["overlay"] = {
            "path": overlay_out.as_posix(),
            "sha256": _sha256(overlay_out),
            "revision": preview_jpg.source_revision(overlay_out),
            "width": write["overlay_width"],
            "height": write["overlay_height"],
            "scale": write["overlay_scale"],
            "kind": "png",
        }
    if source:
        result["source_image_key"] = source.get("image_key")
        result["source_revision"] = source.get("revision")
    if detection:
        result["item_id"] = detection.get("item_id")
        result["detection_contract_version"] = detection.get("contract_version")
        result["detector_name"] = detection.get("detector_name")
        result["detector_version"] = detection.get("detector_version")
    return result


def verify_mask(path, *, width, height, sha256=None) -> dict:
    """Re-check a previously published mask before any SR submission.

    The Mask is a boundary artifact: waiting for capacity, a pause/resume or a
    process restart can all leave a ``MASK_READY`` item whose file was replaced
    or truncated.  The contract check is the same one ``build_mask`` applied at
    publish time, plus the frozen digest when the caller has one.

    Classic stripped TIFFs are verified strip by strip; anything else falls back
    to Pillow so the PR C fixture (a plain Pillow-written mask) still works.
    """
    out = Path(path)
    if not out.is_file():
        raise MaskError(f"掩码文件不存在：{out}")
    width = _positive_int(width, "掩码宽度")
    height = _positive_int(height, "掩码高度")
    try:
        checked = _verify_stream_tiff(out, width, height)
    except _UnsupportedTiff:
        checked = _check_with_pillow(out, width, height)
    actual = _sha256(out)
    if sha256 is not None and actual != sha256:
        raise MaskError("掩码摘要与冻结值不一致")
    return {
        **checked,
        "path": out.as_posix(),
        "sha256": actual,
        "revision": preview_jpg.source_revision(out),
    }


def _write_json(path, document):
    body = json.dumps(
        document, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(body, encoding="utf-8")
        os.replace(tmp, path)
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass


def _combine_diagnostics(*groups):
    combined = []
    seen = set()
    for group in groups:
        for entry in group:
            key = json.dumps(entry, ensure_ascii=False, sort_keys=True)
            if key in seen:
                continue
            seen.add(key)
            combined.append(entry)
    return combined


def candidate_digest(detection) -> str:
    """Stable digest of everything that determines the mask raster.

    Used by restart recovery: a published bundle may be reused only when the
    freshly computed detection would rasterize exactly the same candidates.
    """
    payload = {
        key: detection.get(key)
        for key in ("item_id", "image_id", "image_size", "coordinate_space",
                    "detector_name", "detector_version", "provenance",
                    "candidates")
    }
    body = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _lock_fd(fd):
    """Take a non-blocking exclusive OS lock on one byte of ``fd``."""
    if os.name == "nt":
        import msvcrt
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
    else:
        import fcntl
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock_fd(fd):
    if os.name == "nt":
        import msvcrt
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    else:
        import fcntl
        fcntl.flock(fd, fcntl.LOCK_UN)


_ROUND_LOCKS_GUARD = threading.Lock()
_ROUND_LOCKS: set[str] = set()


class _RoundLock:
    """Exclusive ownership of one mask round's publish window.

    The lock spans reuse-check, temp cleanup, build and the final rename.  A
    second builder — another thread, or another process such as a new worker
    recovering while the old Lease's IO is still running — must get a
    retryable error.  It must never delete the first builder's active
    ``.tmp-*`` directory: with the lock held, any remaining temp directory is
    provably stale (its owner died and the OS released its lock).

    The sibling ``<round>.lock`` file is intentionally never deleted: removing
    a lock file another process may already have opened would let two builders
    lock different inodes under the same name.  It is a zero-byte anchor.
    """

    def __init__(self, final_dir):
        self.final_dir = Path(final_dir)
        self.lock_path = self.final_dir.with_name(
            self.final_dir.name + ".lock")
        self.key = os.path.abspath(str(self.lock_path))
        self.fd = None

    def __enter__(self):
        with _ROUND_LOCKS_GUARD:
            if self.key in _ROUND_LOCKS:
                raise MaskError("同一轮次的掩码正在另一个构建任务中，稍后重试")
            self.lock_path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(self.lock_path, os.O_RDWR | os.O_CREAT, 0o600)
            try:
                _lock_fd(fd)
            except OSError as exc:
                os.close(fd)
                raise MaskError(
                    "同一轮次的掩码正在被另一进程构建，稍后重试") from exc
            _ROUND_LOCKS.add(self.key)
            self.fd = fd
        return self

    def __exit__(self, exc_type, exc, tb):
        with _ROUND_LOCKS_GUARD:
            _ROUND_LOCKS.discard(self.key)
            if self.fd is not None:
                try:
                    _unlock_fd(self.fd)
                finally:
                    os.close(self.fd)
                    self.fd = None
        return False


def _round_lock(final_dir):
    return _RoundLock(final_dir)


def _cleanup_bundle_temps(final_dir: Path):
    """Remove temp directories left by a process that died mid-publish.

    Must only be called while holding this round's ``_RoundLock``; under that
    invariant a live builder cannot own a matching ``.tmp-*`` directory.
    """
    prefix = final_dir.name + ".tmp-"
    try:
        children = list(final_dir.parent.iterdir())
    except OSError:
        return
    for child in children:
        if child.is_dir() and child.name.startswith(prefix):
            shutil.rmtree(child, ignore_errors=True)


def _reuse_mask_bundle(final_dir, mask_name, detection, width, height,
                       item_id, image_key, revision):
    """Validate a complete published bundle, or return ``None`` + a reason.

    A process can die after ``os.rename`` published the artifact directory but
    before the Item checkpoint recorded ``MASK_READY``.  Regenerating that
    round would orphan the old directory (and could produce a mask with a
    different digest).  Reuse is allowed only when every identity, contract and
    content digest still matches the freshly computed detection.
    """
    mask_path = final_dir / mask_name
    overlay_path = final_dir / "overlay.png"
    mask_doc = _read_json(final_dir / "mask.json")
    overlay_doc = _read_json(final_dir / "overlay.json")
    if not (mask_path.is_file() and overlay_path.is_file()
            and isinstance(mask_doc, dict) and isinstance(overlay_doc, dict)):
        return None, "bundle_incomplete"
    if (mask_doc.get("contract_version") != contracts.MASK_CONTRACT_VERSION
            or overlay_doc.get("contract_version")
            != contracts.OVERLAY_CONTRACT_VERSION):
        return None, "contract_version_mismatch"
    if mask_doc.get("item_id") != item_id:
        return None, "item_mismatch"
    source = mask_doc.get("source") or {}
    if (source.get("image_key") != image_key
            or source.get("revision") != revision
            or source.get("width") != width
            or source.get("height") != height):
        return None, "source_mismatch"
    mask_meta = mask_doc.get("mask") or {}
    overlay_meta = mask_doc.get("overlay") or {}
    detection_meta = mask_doc.get("detection") or {}
    if Path(str(mask_meta.get("file") or "")).name != mask_path.name:
        return None, "mask_file_mismatch"
    if Path(str(overlay_meta.get("file") or "")).name != overlay_path.name:
        return None, "overlay_file_mismatch"
    if detection_meta.get("candidate_digest") != candidate_digest(detection):
        return None, "candidate_mismatch"
    for key in ("detector_name", "detector_version", "provenance"):
        if detection_meta.get(key) != detection.get(key):
            return None, "detector_mismatch"
    if (detection_meta.get("contract_version")
            != contracts.DETECTION_CONTRACT_VERSION):
        return None, "detection_contract_mismatch"

    overlay_width = overlay_meta.get("width")
    overlay_height = overlay_meta.get("height")
    overlay_scale = overlay_meta.get("scale")
    if not all(isinstance(value, int) and value > 0
               for value in (overlay_width, overlay_height, overlay_scale)):
        return None, "overlay_contract_mismatch"

    mask_sha = _sha256(mask_path)
    if mask_sha != mask_meta.get("sha256"):
        return None, "mask_digest_mismatch"
    overlay_sha = _sha256(overlay_path)
    if overlay_sha != overlay_meta.get("sha256"):
        return None, "overlay_digest_mismatch"
    try:
        checked = _verify_stream_tiff(mask_path, width, height)
    except _UnsupportedTiff:
        try:
            checked = _check_with_pillow(mask_path, width, height)
        except MaskError:
            return None, "mask_contract_mismatch"
    except MaskError:
        # A published bundle that no longer satisfies the mask contract is an
        # invalid round: rebuild it instead of blocking the Item forever.
        return None, "mask_contract_mismatch"
    if checked["foreground_pixels"] != mask_meta.get("foreground_pixels"):
        return None, "foreground_mismatch"
    try:
        with Image.open(overlay_path) as image:
            if image.size != (overlay_width, overlay_height):
                return None, "overlay_size_mismatch"
    except Exception:  # noqa: BLE001 — a broken preview is not a reusable bundle
        return None, "overlay_unreadable"
    if (overlay_doc.get("item_id") != item_id
            or overlay_doc.get("image_size") != {
                "width": width, "height": height}
            or overlay_doc.get("scale") != overlay_scale):
        return None, "overlay_contract_mismatch"

    diagnostics = _combine_diagnostics(
        detection.get("diagnostics") or [], mask_doc.get("diagnostics") or [],
        [{"code": "mask_bundle_reused"}])
    result = {
        **checked,
        "path": mask_path.as_posix(),
        "sha256": mask_sha,
        "revision": preview_jpg.source_revision(mask_path),
        "item_id": item_id,
        "source_image_key": image_key,
        "source_revision": revision,
        "provenance": detection["provenance"],
        "detection_contract_version": detection["contract_version"],
        "detector_name": detection["detector_name"],
        "detector_version": detection["detector_version"],
        "candidates": len(detection["candidates"]),
        "candidate_count": len(detection["candidates"]),
        "builder_name": contracts.MASK_BUILDER_NAME,
        "builder_version": contracts.MASK_BUILDER_VERSION,
        "diagnostics": diagnostics,
        "overlay": {
            "path": overlay_path.as_posix(),
            "sha256": overlay_sha,
            "revision": preview_jpg.source_revision(overlay_path),
            "width": overlay_meta["width"],
            "height": overlay_meta["height"],
            "scale": overlay_meta["scale"],
            "kind": "png",
        },
        "reused": True,
    }
    return contracts.validate_mask_result(result), None


def build_mask_result(detection_result, source_metadata, config=None) -> dict:
    """D2 contract entry point: build and atomically publish a mask bundle.

    ``source_metadata`` is the frozen PAN metadata (``item_id``, ``image_key``,
    ``revision``, ``width``, ``height``).  ``config`` carries ``path`` (the mask
    file inside a new artifact directory) plus optional ``rows_per_strip``,
    ``overlay_max_edge`` and ``execution_mode``.

    The bundle is written into a sibling temp directory and published with one
    ``os.rename``.  A half-written mask, overlay or manifest is therefore never
    visible as a published artifact.  ``source_revision`` is recomputed after
    the rename because ``preview_jpg`` hashes the final path into the revision.
    """
    config = dict(config or {})
    if not isinstance(source_metadata, dict):
        raise MaskError("source_metadata 必须是对象")
    if not isinstance(detection_result, dict):
        raise MaskError("detection_result 必须是对象")
    width, height = contracts.image_size(source_metadata)
    item_id = contracts.require_string(
        source_metadata.get("item_id"), "item_id")
    image_key = contracts.require_string(
        source_metadata.get("image_key"), "image_key")
    revision = contracts.require_string(
        source_metadata.get("revision"), "revision")

    execution_mode = config.get("execution_mode")
    try:
        detection = contracts.validate_detection_result(
            detection_result, width=width, height=height, mode=execution_mode)
    except contracts.DetectionError:
        raise
    except contracts.ContractError as exc:
        raise MaskError(str(exc)) from exc
    if detection["item_id"] != item_id:
        raise MaskError("DetectionResult.item_id 与冻结项不一致")
    if detection.get("detector_name")=="shared-difference" and (
            detection.get("image_key")!=image_key or detection.get("image_id")!=image_key):
        raise MaskError("差异多边形属于另一份源影像，拒绝栅格化")

    path_value = config.get("path")
    if not path_value:
        raise MaskError("MaskBuilder 需要 config['path']")
    path = Path(path_value)
    if not path.name:
        raise MaskError("MaskBuilder 的 config['path'] 必须是文件路径")
    final_dir = path.parent
    with _round_lock(final_dir):
        return _publish_mask_bundle(
            path=path, detection=detection, source_metadata=source_metadata,
            config=config, final_dir=final_dir, width=width, height=height,
            item_id=item_id, image_key=image_key, revision=revision)


def _publish_mask_bundle(*, path, detection, source_metadata, config,
                         final_dir, width, height, item_id, image_key,
                         revision):
    """Build + publish one round while holding its ``_RoundLock``."""
    _cleanup_bundle_temps(final_dir)
    rebuild_reason = None
    if final_dir.exists():
        reused, reason = _reuse_mask_bundle(
            final_dir, path.name, detection, width, height,
            item_id, image_key, revision)
        if reused is not None:
            return reused
        # A published-but-uncommitted round (process died before the checkpoint
        # commit) can be rebuilt safely: the directory lives under the batch
        # artifact root and the item still owns this detection round.
        rebuild_reason = reason
        shutil.rmtree(final_dir, ignore_errors=True)
    tmp_dir = final_dir.with_name(
        f"{final_dir.name}.tmp-{os.getpid()}-{time.time_ns()}")
    tmp_dir.mkdir(parents=True, exist_ok=False)
    published = False
    try:
        mask = build_mask(
            tmp_dir / path.name,
            width=width, height=height,
            candidates=detection["candidates"],
            provenance=detection["provenance"],
            rows_per_strip=config.get("rows_per_strip"),
            overlay_path=tmp_dir / "overlay.png",
            overlay_max_edge=config.get("overlay_max_edge"),
            source=source_metadata,
            detection=detection,
        )
        overlay = mask["overlay"]
        diagnostics = _combine_diagnostics(
            detection["diagnostics"], mask["diagnostics"])
        if rebuild_reason is not None:
            diagnostics = _combine_diagnostics(
                diagnostics,
                [{"code": "mask_bundle_rebuilt", "reason": rebuild_reason}])
        overlay_doc = {
            "contract_version": contracts.OVERLAY_CONTRACT_VERSION,
            "item_id": item_id,
            "image_size": {"width": width, "height": height},
            "coordinate_space": contracts.COORDINATE_SPACE,
            "scale": overlay["scale"],
            "overlay": {
                "file": "overlay.png",
                "sha256": overlay["sha256"],
                "width": overlay["width"],
                "height": overlay["height"],
            },
            "mask": {"file": path.name, "sha256": mask["sha256"]},
            "candidates": detection["candidates"],
            "provenance": detection["provenance"],
            "detector_name": detection["detector_name"],
            "detector_version": detection["detector_version"],
            "diagnostics": diagnostics,
        }
        mask_doc = {
            "contract_version": contracts.MASK_CONTRACT_VERSION,
            "item_id": item_id,
            "mask": {
                "file": path.name,
                "sha256": mask["sha256"],
                "width": width,
                "height": height,
                "dtype": contracts.MASK_DTYPE,
                "spp": 1,
                "foreground_pixels": mask["foreground_pixels"],
                "coordinate_space": contracts.COORDINATE_SPACE,
            },
            "overlay": {
                "file": "overlay.png",
                "sha256": overlay["sha256"],
                "width": overlay["width"],
                "height": overlay["height"],
                "scale": overlay["scale"],
            },
            "source": {
                "image_key": image_key,
                "revision": revision,
                "width": width,
                "height": height,
            },
            "detection": {
                "contract_version": detection["contract_version"],
                "detector_name": detection["detector_name"],
                "detector_version": detection["detector_version"],
                "provenance": detection["provenance"],
                "candidate_digest": candidate_digest(detection),
            },
            "builder": {
                "name": contracts.MASK_BUILDER_NAME,
                "version": contracts.MASK_BUILDER_VERSION,
            },
            "diagnostics": diagnostics,
        }
        _write_json(tmp_dir / "overlay.json", overlay_doc)
        _write_json(tmp_dir / "mask.json", mask_doc)
        os.rename(tmp_dir, final_dir)
        published = True
    finally:
        if not published:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    final_mask = final_dir / path.name
    final_overlay = final_dir / "overlay.png"
    result = {
        **mask,
        "path": final_mask.as_posix(),
        "revision": preview_jpg.source_revision(final_mask),
        "item_id": item_id,
        "source_image_key": image_key,
        "source_revision": revision,
        "provenance": detection["provenance"],
        "detection_contract_version": detection["contract_version"],
        "detector_name": detection["detector_name"],
        "detector_version": detection["detector_version"],
        "diagnostics": _combine_diagnostics(
            detection["diagnostics"], mask["diagnostics"],
            [{"code": "mask_bundle_rebuilt", "reason": rebuild_reason}]
            if rebuild_reason is not None else []),
        "overlay": {
            **mask["overlay"],
            "path": final_overlay.as_posix(),
            "revision": preview_jpg.source_revision(final_overlay),
        },
        "reused": False,
    }
    return contracts.validate_mask_result(result)
