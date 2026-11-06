"""Detector implementations for the batch pipeline (D1).

Two explicit channels:

* ``SR_BATCH_DETECTION=center`` (default) — the deterministic MockDetector.
  One predictable center box; ``provenance=mock``.  It exists so the pipeline,
  Mask contract and SR handoff can be exercised without a model, GPU or a
  disk-array scan, and it must never be reported as a real finding.
* ``SR_BATCH_DETECTION=real`` — a real, bounded-window TIFF reader.  It checks
  the frozen PAN/NOSR revisions and grid relation, then reads the actual PAN
  pixels window by window (peak memory is ``window_rows × width``) and emits
  source-pixel candidates for flat rows/columns (the classic stuck-line
  signature).  ``provenance=real`` is truthful: real pixels were read.  It does
  **not** claim production detection accuracy; a later algorithm can replace
  it behind the same contract.

The result is a closed, zero-based pixel box in PAN space (x = column,
y = row).  ``batch_contracts`` validates it before the worker stores it.
"""

from __future__ import annotations

import math
import os
import zlib
from pathlib import Path

import numpy as np

from backend.services import batch_contracts as contracts
from backend.services import preview_jpg

# Re-exported so callers keep importing the error from this module.
DetectionError = contracts.DetectionError

REAL_DETECTOR_NAME = "flatline-scan"
REAL_DETECTOR_VERSION = "1.0.0"

DEFAULT_WINDOW_ROWS = 256
MAX_WINDOW_ROWS = 4096
# One decoded window stays around 32M pixels (~64 MiB for uint16).
MAX_WINDOW_PIXELS = 1 << 25
MAX_CANDIDATES = 256


def _detection_mode(config):
    raw = (config or {}).get("mode")
    if raw is None:
        raw = os.environ.get("SR_BATCH_DETECTION") or "center"
    return str(raw).strip().lower()


def _window_rows(config, width):
    raw = (config or {}).get("window_rows")
    if raw is None:
        raw = os.environ.get("SR_BATCH_DETECTION_WINDOW_ROWS")
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        rows = DEFAULT_WINDOW_ROWS
    else:
        try:
            rows = int(str(raw).strip(), 10)
        except (TypeError, ValueError) as exc:
            raise DetectionError(
                f"window_rows 必须是整数：{raw!r}") from exc
    if rows <= 0:
        raise DetectionError(f"window_rows 必须是正整数：{raw!r}")
    rows = min(rows, MAX_WINDOW_ROWS)
    if width > MAX_WINDOW_PIXELS:
        raise DetectionError(
            f"影像宽度 {width} 超出有界窗口预算；拒绝整行解码")
    return max(1, min(rows, MAX_WINDOW_PIXELS // width))


def _probe_image(ref, label, width, height):
    """Validate one frozen input against the TIFF on disk; return its info.

    The frozen revision is the file identity agreed at import time.  A changed
    file must block the item instead of silently detecting on a different
    image.  Only the header is read here; pixels are read later, per window.
    """
    path = Path(contracts.require_string(ref.get("path"), f"{label}.path"))
    if not path.is_file():
        raise DetectionError(f"{label} 文件不存在：{path}")
    revision = ref.get("revision")
    if revision and preview_jpg.source_revision(path) != revision:
        raise DetectionError(f"{label} 修订已变化，拒绝按冻结清单检测")
    try:
        info = preview_jpg.probe_tiff(path)
    except Exception as exc:  # noqa: BLE001 — any header IO failure is a fact
        raise DetectionError(
            f"{label} TIFF 头不可读：{type(exc).__name__}: {exc}") from exc
    if (info["W"], info["H"]) != (width, height):
        raise DetectionError(
            f"{label} 尺寸 {(info['W'], info['H'])} 与冻结值 "
            f"{(width, height)} 不一致")
    for key, frozen in (("W", "width"), ("H", "height"), ("bits", "bits"),
                        ("spp", "spp"), ("sample_format", "sample_format"),
                        ("compression", "compression")):
        expected = ref.get(frozen)
        if expected is not None and info.get(key) != expected:
            raise DetectionError(
                f"{label} {key}={info.get(key)!r} 与冻结值 {expected!r} 不一致")
    if info.get("spp") != 1:
        raise DetectionError(f"{label} 必须是单波段，实际 {info.get('spp')}")
    if info.get("tiled"):
        raise DetectionError(f"{label} 是 tiled TIFF，不在有界窗口读取范围内")
    return path, info


def _check_reference_grid(target_info, reference_info, width, height):
    """Re-assert the manifest's PAN/NOSR relation against the actual headers."""
    ref_width, ref_height = reference_info["W"], reference_info["H"]
    if not (ref_width <= width and ref_height <= height):
        raise DetectionError(
            f"NOSR 尺寸 {ref_width}×{ref_height} 大于 PAN {width}×{height}")
    if (ref_width, ref_height) == (width, height):
        raise DetectionError("NOSR 分辨率必须低于 PAN")
    if (target_info["bits"], target_info["sample_format"]) != (
            reference_info["bits"], reference_info["sample_format"]):
        raise DetectionError("PAN 与 NOSR 样本类型不一致")


def _groups(flags):
    """Return inclusive ``(start, end)`` runs of True, in source coordinates."""
    groups = []
    start = None
    for index, flag in enumerate(flags):
        if flag and start is None:
            start = index
        elif not flag and start is not None:
            groups.append((start, index - 1))
            start = None
    if start is not None:
        groups.append((start, len(flags) - 1))
    return groups


_COMPRESSION_NONE = 1
_COMPRESSION_DEFLATE = {8, 32946}       # Deflate / Adobe Deflate
_SUPPORTED_BITS = (8, 16, 32)
_SUPPORTED_SAMPLE_FORMAT = (1, 2, 3)     # uint / int / float
_DECOMPRESS_CHUNK = 1 << 20              # 每次最多读 1 MiB 压缩数据
_DEFLATE_SLACK = 1 << 16                 # 压缩流允许的最大装箱开销
_IFD_NODATA = 42113
_IFD_PREDICTOR = 317
_PREDICTOR_NONE = 1
_PREDICTOR_HORIZONTAL = 2


def _nodata_from_ifd(path, scalars, arrays, endian):
    """Read the GDAL NoData tag (42113) from the parsed IFD.

    A NoData-filled line is empty area, not a stuck detector line: the sentinel
    must be excluded from the flatness statistics instead of being reported as
    a finding.  Only the header is parsed here; no pixels are decoded.
    """
    try:
        if _IFD_NODATA in arrays:
            count, size, _ = arrays[_IFD_NODATA]
            if size != 1 or count > 1024:
                return None
            values = preview_jpg._read_array(path, arrays[_IFD_NODATA], endian)
            raw = bytes(int(v) & 0xFF for v in values)
        elif _IFD_NODATA in scalars:
            raw = bytes([int(scalars[_IFD_NODATA]) & 0xFF])
        else:
            return None
    except Exception:  # noqa: BLE001 — tag access failure just means "unknown"
        return None
    text = raw.decode("latin-1", "replace").split("\x00")[0].strip()
    text = text.split()[0] if text else ""
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _nodata_diagnostic(nodata):
    """Return ``(json_value, kind)`` for a NoData sentinel.

    ``json.dumps`` accepts NaN by default but Starlette's response encoder does
    not; a NaN sentinel must never reach the Overlay API as a raw float.  The
    numeric case stays numeric for existing readers, non-finite values become
    a string plus an explicit kind.
    """
    if nodata is None:
        return None, "none"
    value = float(nodata)
    if math.isfinite(value):
        return value, "number"
    if math.isnan(value):
        return "nan", "nan"
    return ("+inf" if value > 0 else "-inf"), "infinity"


def _ifd_scalar(path, scalars, arrays, tag, endian, *, label, default=None):
    """Resolve one IFD tag to an int (array tags use their first value)."""
    if tag in scalars:
        return int(scalars[tag])
    if tag in arrays:
        values = preview_jpg._read_array(path, arrays[tag], endian)
        if values.size == 0:
            raise DetectionError(f"TIFF 的 {label} 为空")
        return int(values[0])
    if default is not None:
        return default
    raise DetectionError(f"TIFF 缺 {label}")


def _ifd_int_array(path, scalars, arrays, tag, endian, *, label, expected):
    """Resolve StripOffsets/StripByteCounts and require exactly ``expected``."""
    if tag in arrays:
        count = arrays[tag][0]
        if count != expected:
            raise DetectionError(
                f"TIFF 的 {label} 数量 {count} 与条带数 {expected} 不一致")
        return [int(v) for v in
                preview_jpg._read_array(path, arrays[tag], endian)]
    if tag in scalars and expected == 1:
        return [int(scalars[tag])]
    raise DetectionError(f"TIFF 的 {label} 数量与条带数 {expected} 不一致")


def _read_strip_layout(path, width, height):
    """Parse the classic/BigTIFF strip layout without decoding any pixels.

    Only chunky, single-band, stripped images with no compression or Deflate
    are accepted.  Predictor 1 (none) and 2 (horizontal differencing) are
    decoded; Predictor 3 and float Predictor 2 are rejected instead of being
    silently misread.  Every later read is bounded by ``rows_per_window``;
    this layout just supplies offsets, byte counts and the sample dtype.
    """
    try:
        is_big, endian, ifd_off = preview_jpg._parse_tiff_header(path)
        scalars, arrays = preview_jpg._read_ifd(path, is_big, endian, ifd_off)
        if 322 in scalars or 322 in arrays:       # TileWidth
            raise DetectionError("PAN 是 tiled TIFF，不在有界窗口读取范围内")
        spp = _ifd_scalar(
            path, scalars, arrays, 277, endian, label="SamplesPerPixel",
            default=1)
        if spp != 1:
            raise DetectionError(f"真实检测只支持单波段，实际 spp={spp}")
        planar = _ifd_scalar(
            path, scalars, arrays, 284, endian, label="PlanarConfiguration",
            default=1)
        if planar != 1:
            raise DetectionError(
                f"真实检测只支持 chunky（planar={planar}）")
        compression = _ifd_scalar(
            path, scalars, arrays, 259, endian, label="Compression",
            default=1)
        if compression != _COMPRESSION_NONE and compression not in _COMPRESSION_DEFLATE:
            raise DetectionError(
                f"不支持的 TIFF 压缩方式 {compression}（仅 none/Deflate）")
        bits = _ifd_scalar(
            path, scalars, arrays, 258, endian, label="BitsPerSample",
            default=8)
        sample_format = _ifd_scalar(
            path, scalars, arrays, 339, endian, label="SampleFormat", default=1)
        if bits not in _SUPPORTED_BITS:
            raise DetectionError(f"仅支持 8/16/32bit 采样（bits={bits}）")
        if sample_format not in _SUPPORTED_SAMPLE_FORMAT:
            raise DetectionError(
                f"不支持的 SampleFormat={sample_format}")
        predictor = _ifd_scalar(
            path, scalars, arrays, _IFD_PREDICTOR, endian,
            label="Predictor", default=_PREDICTOR_NONE)
        if predictor not in (_PREDICTOR_NONE, _PREDICTOR_HORIZONTAL):
            raise DetectionError(
                f"不支持的 TIFF Predictor={predictor}（仅 1/2；"
                "浮点 Predictor=3 尚未实现）")
        if predictor == _PREDICTOR_HORIZONTAL and sample_format == 3:
            raise DetectionError(
                "浮点 TIFF 的 Predictor=2 不在支持范围")
        dtype = preview_jpg._np_dtype(bits, sample_format, endian)
        rows_per_strip = _ifd_scalar(
            path, scalars, arrays, 278, endian, label="RowsPerStrip",
            default=height)
        if rows_per_strip <= 0:
            raise DetectionError(f"RowsPerStrip 非法：{rows_per_strip}")
        strips = (height + rows_per_strip - 1) // rows_per_strip
        offsets = _ifd_int_array(
            path, scalars, arrays, 273, endian,
            label="StripOffsets", expected=strips)
        counts = _ifd_int_array(
            path, scalars, arrays, 279, endian,
            label="StripByteCounts", expected=strips)
    except preview_jpg.PreviewError as exc:
        raise DetectionError(f"PAN TIFF 条带布局不可读：{exc}") from exc
    row_bytes = width * dtype.itemsize
    strip_rows = []
    for index in range(strips):
        rows = min(rows_per_strip, height - index * rows_per_strip)
        expected_bytes = rows * row_bytes
        if counts[index] < (expected_bytes if compression == _COMPRESSION_NONE
                            else 0):
            raise DetectionError(
                f"条带 {index} 字节数 {counts[index]} 小于未压缩尺寸 "
                f"{expected_bytes}，文件可能被截断")
        if compression != _COMPRESSION_NONE and \
                counts[index] > expected_bytes + _DEFLATE_SLACK:
            raise DetectionError(
                f"条带 {index} 压缩字节数 {counts[index]} 超出尺寸预算 "
                f"{expected_bytes}")
        strip_rows.append(rows)
    return {
        "endian": endian,
        "big": is_big,
        "dtype": dtype,
        "compression": compression,
        "predictor": predictor,
        "rows_per_strip": rows_per_strip,
        "strips": strips,
        "strip_rows": strip_rows,
        "offsets": offsets,
        "counts": counts,
        "row_bytes": row_bytes,
        "nodata": _nodata_from_ifd(path, scalars, arrays, endian),
    }


def _uncompressed_windows(f, *, offset, rows, row_bytes, rows_per_window,
                          dtype, width, strip_index):
    """Yield row windows straight from the file — never more than one window."""
    for local in range(0, rows, rows_per_window):
        take = min(rows_per_window, rows - local)
        f.seek(offset + local * row_bytes)
        data = f.read(take * row_bytes)
        if len(data) != take * row_bytes:
            raise DetectionError(
                f"条带 {strip_index} 读取不完整："
                f"{len(data)}/{take * row_bytes} 字节")
        yield np.frombuffer(data, dtype=dtype).reshape(take, width)


def _deflate_windows(f, *, offset, count, expected, row_bytes, rows_per_window,
                     dtype, width, strip_index):
    """Stream-decompress one Deflate strip with bounded output.

    The decompressor is asked for at most one window of output at a time, and
    any byte beyond the expected strip size is rejected.  Peak memory is the
    window plus one read chunk, never the whole strip or image.
    """
    window_bytes = row_bytes * rows_per_window
    f.seek(offset)
    remaining = count
    dec = zlib.decompressobj()
    pending = b""
    produced = 0
    buffer = bytearray()

    def windows(*, flush=False):
        while len(buffer) >= window_bytes:
            chunk = bytes(buffer[:window_bytes])
            del buffer[:window_bytes]
            yield np.frombuffer(chunk, dtype=dtype).reshape(-1, width)
        if flush and buffer:
            if len(buffer) % row_bytes:
                raise DetectionError(
                    f"条带 {strip_index} 解压长度不是整行")
            chunk = bytes(buffer)
            buffer.clear()
            yield np.frombuffer(chunk, dtype=dtype).reshape(-1, width)

    while True:
        yield from windows()
        if dec.eof:
            break
        if not pending:
            if remaining <= 0:
                break
            data = f.read(min(_DECOMPRESS_CHUNK, remaining))
            if not data:
                break
            remaining -= len(data)
            pending = bytes(data)
            continue
        space = min(window_bytes, expected - produced)
        if space < 1:
            space = 1  # 已满尺寸：再多产出 1 字节即判越界
        out = dec.decompress(pending, space)
        pending = dec.unconsumed_tail
        if produced + len(out) > expected:
            raise DetectionError(
                f"条带 {strip_index} 解压结果超出尺寸预算 {expected}")
        buffer += out
        produced += len(out)
    if produced != expected:
        raise DetectionError(
            f"条带 {strip_index} 解压长度 {produced} 与预期 {expected} 不一致")
    if not dec.eof:
        raise DetectionError(f"条带 {strip_index} 的 Deflate 流不完整")
    yield from windows(flush=True)


def _iter_windows(path, layout, width, height, rows_per_window):
    """Yield ``(rows, width)`` pixel windows in source order.

    This is the only pixel reader used by real detection.  It seeks per strip
    and reads/decompresses one window at a time, so the decoded block never
    exceeds ``rows_per_window × width`` regardless of image height.  TIFF
    Predictor=2 is undone per row after decompression; the windows are always
    row-aligned, so the cumulative sum never crosses a window boundary.
    """
    predictor = layout.get("predictor", _PREDICTOR_NONE)

    def decoded(blocks):
        for block in blocks:
            if predictor == _PREDICTOR_HORIZONTAL:
                # Modular addition in the sample dtype is exactly TIFF's
                # horizontal differencing inverse (wraps at 2**bits).
                yield np.cumsum(block, axis=1, dtype=block.dtype)
            else:
                yield block

    with open(path, "rb") as f:
        file_size = os.fstat(f.fileno()).st_size
        for index, rows in enumerate(layout["strip_rows"]):
            offset = layout["offsets"][index]
            count = layout["counts"][index]
            if offset < 0 or count < 0 or offset + count > file_size:
                raise DetectionError(
                    f"条带 {index} 偏移/长度越界（文件可能被截断）")
            if layout["compression"] == _COMPRESSION_NONE:
                yield from decoded(_uncompressed_windows(
                    f, offset=offset, rows=rows,
                    row_bytes=layout["row_bytes"],
                    rows_per_window=rows_per_window,
                    dtype=layout["dtype"], width=width, strip_index=index))
            else:
                yield from decoded(_deflate_windows(
                    f, offset=offset, count=count,
                    expected=rows * layout["row_bytes"],
                    row_bytes=layout["row_bytes"],
                    rows_per_window=rows_per_window,
                    dtype=layout["dtype"], width=width, strip_index=index))


def _scan_flat_lines(path, width, height, rows_per_window):
    """Bounded-window scan of real pixels, accumulated across the whole image.

    A line is "flat" when every valid pixel on it has the same value (and there
    are at least two of them) — the signature of a stuck/dead detector line.
    NoData sentinels do not vote.  Column validity is counted over the whole
    image (not judged per window), so the result does not depend on
    ``window_rows``.  This is an IO prototype, not an accuracy claim.
    """
    layout = _read_strip_layout(path, width, height)
    nodata = layout["nodata"]
    rows_flat = []
    column_min = None
    column_max = None
    column_valid_count = None
    seen_rows = 0
    for pixels in _iter_windows(path, layout, width, height, rows_per_window):
        if pixels.ndim != 2 or pixels.shape[1] != width:
            raise DetectionError(
                f"窗口形状 {pixels.shape} 与 PAN 宽度 {width} 不一致")
        seen_rows += pixels.shape[0]
        is_float = pixels.dtype.kind == "f"
        if nodata is None:
            rows_flat.extend(
                (pixels.min(axis=1) == pixels.max(axis=1)).tolist())
            window_min = pixels.min(axis=0)
            window_max = pixels.max(axis=0)
            window_count = np.full(width, pixels.shape[0], dtype=np.int64)
        else:
            if is_float and math.isnan(nodata):
                valid = ~np.isnan(pixels)
            else:
                valid = pixels != nodata
            hi = np.inf if is_float else np.iinfo(pixels.dtype).max
            lo = -np.inf if is_float else np.iinfo(pixels.dtype).min
            row_has = valid.sum(axis=1) >= 2
            row_min = np.where(valid, pixels, hi).min(axis=1)
            row_max = np.where(valid, pixels, lo).max(axis=1)
            rows_flat.extend((row_has & (row_min == row_max)).tolist())
            window_min = np.where(valid, pixels, hi).min(axis=0)
            window_max = np.where(valid, pixels, lo).max(axis=0)
            window_count = valid.sum(axis=0).astype(np.int64)
        if column_min is None:
            column_min = np.asarray(window_min, dtype=np.float64)
            column_max = np.asarray(window_max, dtype=np.float64)
            column_valid_count = window_count
        else:
            column_min = np.minimum(column_min, window_min)
            column_max = np.maximum(column_max, window_max)
            column_valid_count += window_count
    if column_min is None:  # pragma: no cover — height is validated positive
        raise DetectionError("PAN 为空，无法检测")
    if seen_rows != height:
        raise DetectionError(
            f"PAN 实际解码 {seen_rows} 行与冻结值 {height} 不一致")
    columns_flat = (
        (column_valid_count >= 2) & (column_min == column_max)).tolist()
    return rows_flat, columns_flat, nodata


def _flatline_candidates(rows_flat, columns_flat, width, height, nodata=None):
    nodata_value, nodata_kind = _nodata_diagnostic(nodata)
    diagnostics = [{
        "code": "flatline_scan",
        "flat_rows": sum(1 for flag in rows_flat if flag),
        "flat_columns": sum(1 for flag in columns_flat if flag),
        "nodata": nodata_value,
        "nodata_kind": nodata_kind,
    }]
    candidates = []
    seen = set()
    truncated = False

    def add(bbox, label):
        nonlocal truncated
        key = tuple(bbox)
        if key in seen:
            return
        if len(candidates) >= MAX_CANDIDATES:
            truncated = True
            return
        seen.add(key)
        number = len(candidates) + 1
        candidates.append({
            "candidate_id": f"{label}-{number:04d}",
            "kind": "bbox",
            "bbox": list(bbox),
            "points": None,
            "score": None,
            "label": label,
        })

    for start, end in _groups(rows_flat):
        add([0, start, width - 1, end], "flat_row")
    for start, end in _groups(columns_flat):
        add([start, 0, end, height - 1], "flat_column")
    if truncated:
        diagnostics.append({
            "code": "candidate_limit",
            "limit": MAX_CANDIDATES,
        })
    return candidates, diagnostics


def _detect_real(image_ref, reference_ref, width, height, base, config):
    if not isinstance(reference_ref, dict):
        raise DetectionError("真实检测需要冻结的 NOSR 参考影像")
    ref_width, ref_height = contracts.image_size(reference_ref)
    target_path, target_info = _probe_image(
        image_ref, "PAN", width, height)
    reference_path, reference_info = _probe_image(
        reference_ref, "NOSR", ref_width, ref_height)
    _check_reference_grid(target_info, reference_info, width, height)

    rows_per_window = _window_rows(config, width)
    rows_flat, columns_flat, nodata = _scan_flat_lines(
        target_path, width, height, rows_per_window)
    candidates, diagnostics = _flatline_candidates(
        rows_flat, columns_flat, width, height, nodata)
    base.update({
        "provenance": "real",
        "detector_name": REAL_DETECTOR_NAME,
        "detector_version": REAL_DETECTOR_VERSION,
        "reference_image_key": reference_ref.get("image_key"),
        "reference_revision": reference_ref.get("revision"),
        "reference_image_size": {
            "width": ref_width, "height": ref_height},
        "window_rows": rows_per_window,
        "candidates": candidates,
        "diagnostics": base["diagnostics"] + diagnostics,
    })
    return contracts.validate_detection_result(
        base, mode=(config or {}).get("execution_mode"))


def detect(image_ref, reference_ref=None, config=None):
    """Return a validated DetectionResult for one frozen PAN target.

    ``image_ref`` is the frozen target metadata produced by the manifest
    (``width``, ``height``, ``image_key``, ``revision``, ``path``, ``item_id``).
    ``reference_ref`` is the frozen NOSR metadata; the mock channel records it,
    the real channel validates its revision/grid and reads the PAN pixels.
    ``config`` may carry ``mode`` (center/none/error/real) and ``execution_mode``
    (demo/production).

    ``SR_BATCH_DETECTION=none`` returns a successful empty result (NO_ISSUE);
    ``error`` raises so the worker applies its bounded retry.  ``center`` is the
    mock demo channel; ``real`` is the bounded-window TIFF detector.
    """
    if not isinstance(image_ref, dict):
        raise DetectionError("image_ref 必须是冻结影像元数据对象")
    width, height = contracts.image_size(image_ref)
    item_id = contracts.require_string(image_ref.get("item_id"), "item_id")
    image_id = (image_ref.get("image_key") or image_ref.get("path")
                or image_ref.get("image_id"))
    image_id = contracts.require_string(image_id, "image_id")
    if reference_ref is None:
        reference_ref = image_ref.get("reference_image")
    config = config or image_ref.get("detection_config") or {}
    mode = _detection_mode(config)
    execution_mode = (config or {}).get("execution_mode")
    diagnostics = []
    base = {
        "contract_version": contracts.DETECTION_CONTRACT_VERSION,
        "item_id": item_id,
        "image_id": image_id,
        "image_key": image_ref.get("image_key"),
        "image_size": {"width": width, "height": height},
        "coordinate_space": contracts.COORDINATE_SPACE,
        "detector_name": contracts.DETECTOR_NAME,
        "detector_version": contracts.DETECTOR_VERSION,
        "provenance": "mock",
        "candidates": [],
        "diagnostics": diagnostics,
    }
    if isinstance(reference_ref, dict):
        base["reference_image_key"] = reference_ref.get("image_key")
        base["reference_revision"] = reference_ref.get("revision")
    if mode == "none":
        diagnostics.append({
            "code": "no_candidate",
            "reason": "detection_mode_none",
        })
        return contracts.validate_detection_result(
            base, mode=execution_mode)
    if mode == "error":
        raise DetectionError("SR_BATCH_DETECTION=error")
    if mode == "diff":
        from backend.services import batch_difference
        if not isinstance(reference_ref, dict):
            raise DetectionError("差异检测需要 NOSR 参考影像")
        return batch_difference.detect(image_ref, reference_ref, base, config)
    if mode == "real":
        return _detect_real(
            image_ref, reference_ref, width, height, base, config)
    if mode != "center":
        raise DetectionError(f"未知检测模式：{mode}")

    # Keep the center box at least one pixel in from each edge when the image is
    # large enough; tiny images fall back to the full frame instead of an
    # inverted (empty) box.
    margin_x = max(1, width // 4)
    margin_y = max(1, height // 4)
    x0, y0 = margin_x, margin_y
    x1, y1 = width - 1 - margin_x, height - 1 - margin_y
    if x1 < x0 or y1 < y0:
        x0 = y0 = 0
        x1, y1 = width - 1, height - 1
        diagnostics.append({"code": "center_box_fallback"})
    base["candidates"] = [{
        "candidate_id": "c1",
        "kind": "bbox",
        "bbox": [x0, y0, x1, y1],
        "points": None,
        "score": None,
        "label": "center",
    }]
    return contracts.validate_detection_result(base, mode=execution_mode)
