"""Frozen artifact contracts for the batch repair pipeline (PR D1/D2).

The scheduler exchanges small JSON objects, never pixels:

* ``DetectionResult`` — what a Detector found on the frozen PAN target.
* ``MaskResult`` — the published mask file plus everything needed to audit it.

Coordinates are zero-based source pixels: ``x`` is the column, ``y`` is the
row.  A bbox is a closed integer pixel box ``[x0, y0, x1, y1]``; both ends are
included, matching ``backend.services.mask`` and the viewer rasterizer.  This is
the same convention ``run_sr`` consumes, so a mask built here can be submitted
without a coordinate translation step.

The contracts are deliberately strict: a detector that claims ``real`` in demo
mode, a mask without foreground, a candidate that falls completely outside the
image, or a NaN/Inf polygon vertex is an error, never a silent success.  The
large payload (the TIFF itself) never enters SQLite or SSE; only this small
reference object travels through the store and events.
"""

from __future__ import annotations

import math
import re

DETECTION_CONTRACT_VERSION = "detection.v1"
MASK_CONTRACT_VERSION = "mask.v1"
OVERLAY_CONTRACT_VERSION = "overlay.v1"
COORDINATE_SPACE = "source_pixel"

DETECTOR_NAME = "deterministic-center"
DETECTOR_VERSION = "1.0.0"
MASK_BUILDER_NAME = "strip-mask-builder"
MASK_BUILDER_VERSION = "1.0.0"

MASK_DTYPE = "uint8"
BACKGROUND_VALUE = 0
FOREGROUND_VALUE = 255

SUPPORTED_CANDIDATE_KINDS = ("bbox", "polygon")
# Reserved for later detectors; accepting them now would let an unimplemented
# candidate shape reach the mask writer and silently produce a wrong mask.
RESERVED_CANDIDATE_KINDS = ("artifact", "local_blur")
PROVENANCES = ("mock", "real")

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class ContractError(ValueError):
    """An artifact does not satisfy the frozen contract."""


class DetectionError(ContractError):
    """The detector could not produce a valid DetectionResult."""


class MaskError(ContractError):
    """The mask builder or verifier rejected an artifact."""


# ---------------------------------------------------------------------------
# scalar helpers
# ---------------------------------------------------------------------------

def positive_int(value, label):
    """Return a real positive int; reject bools, floats with a fraction and NaN."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ContractError(f"{label} 必须是正整数：{value!r}")
    if isinstance(value, float):
        if not math.isfinite(value) or not value.is_integer():
            raise ContractError(f"{label} 必须是正整数：{value!r}")
    number = int(value)
    if number <= 0:
        raise ContractError(f"{label} 必须是正整数：{value!r}")
    return number


def _integer(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ContractError(f"{label} 必须是整数：{value!r}")
    if isinstance(value, float):
        if not math.isfinite(value) or not value.is_integer():
            raise ContractError(f"{label} 必须是整数：{value!r}")
    return int(value)


def require_string(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ContractError(f"{label} 必须是非空字符串：{value!r}")
    return value


def image_size(ref):
    """Read ``(width, height)`` from a frozen image metadata object."""
    if not isinstance(ref, dict):
        raise ContractError("影像元数据必须是对象")
    size = ref.get("image_size")
    if isinstance(size, dict):
        width = size.get("width", size.get("W"))
        height = size.get("height", size.get("H"))
    else:
        width = ref.get("width", ref.get("W"))
        height = ref.get("height", ref.get("H"))
    return positive_int(width, "影像宽度"), positive_int(height, "影像高度")


# ---------------------------------------------------------------------------
# candidates
# ---------------------------------------------------------------------------

def _normalize_point(point):
    if not isinstance(point, (list, tuple)) or len(point) != 2:
        raise ContractError(f"多边形顶点必须是 [x, y]：{point!r}")
    return [_integer(point[0], "顶点 x"), _integer(point[1], "顶点 y")]


def _polygon_area(points):
    area = 0
    for index, (x0, y0) in enumerate(points):
        x1, y1 = points[(index + 1) % len(points)]
        area += x0 * y1 - x1 * y0
    return abs(area) / 2.0


def _clip_polygon_half_plane(points, inside, intersect):
    """One Sutherland–Hodgman step; returns the vertices still inside."""
    if not points:
        return []
    output = []
    previous = points[-1]
    previous_inside = inside(previous)
    for current in points:
        current_inside = inside(current)
        if current_inside:
            if not previous_inside:
                output.append(intersect(previous, current))
            output.append(current)
        elif previous_inside:
            output.append(intersect(previous, current))
        previous, previous_inside = current, current_inside
    return output


def clip_polygon(points, width, height):
    """True polygon clip against the closed pixel rectangle ``[0,w-1]×[0,h-1]``.

    Clipping each vertex independently is **not** a polygon clip: it can remove
    a whole corner of the shape (a 10×10 triangle covering 36 pixels can shrink
    to 21).  Sutherland–Hodgman keeps the actual intersection so the rasterizer
    sees the same visible area it would have seen without clipping.
    """
    def vertical_edge(previous, current, x):
        if current[0] == previous[0]:
            return [float(x), float(previous[1])]
        t = (x - previous[0]) / (current[0] - previous[0])
        return [float(x), previous[1] + t * (current[1] - previous[1])]

    def horizontal_edge(previous, current, y):
        if current[1] == previous[1]:
            return [float(previous[0]), float(y)]
        t = (y - previous[1]) / (current[1] - previous[1])
        return [previous[0] + t * (current[0] - previous[0]), float(y)]

    vertices = [[float(x), float(y)] for x, y in points]
    for inside, intersect in (
            (lambda point: point[0] >= 0,
             lambda previous, current: vertical_edge(previous, current, 0)),
            (lambda point: point[0] <= width - 1,
             lambda previous, current: vertical_edge(
                 previous, current, width - 1)),
            (lambda point: point[1] >= 0,
             lambda previous, current: horizontal_edge(previous, current, 0)),
            (lambda point: point[1] <= height - 1,
             lambda previous, current: horizontal_edge(
                 previous, current, height - 1))):
        vertices = _clip_polygon_half_plane(vertices, inside, intersect)
        if not vertices:
            return []

    # Clipping can emit the same corner twice; drop consecutive duplicates so
    # the area/degeneracy checks see a real polygon.
    deduped = []
    for point in vertices:
        if (not deduped
                or abs(point[0] - deduped[-1][0]) > 1e-9
                or abs(point[1] - deduped[-1][1]) > 1e-9):
            deduped.append(point)
    if (len(deduped) > 1
            and abs(deduped[0][0] - deduped[-1][0]) <= 1e-9
            and abs(deduped[0][1] - deduped[-1][1]) <= 1e-9):
        deduped.pop()
    return deduped


def normalize_candidate(candidate, width, height, index):
    """Return ``(normalized_candidate, diagnostics)``.

    Out-of-bounds geometry is clipped and recorded; a candidate that disappears
    after clipping is an error, not an empty (NO_ISSUE) result.  Rounded boxes
    keep the closed-box convention, so a 1x1 box at the last pixel is valid.

    A polygon keeps its *original* vertices in the returned candidate: the
    rasterizer clips to the bounded strip canvas, which preserves the exact
    visible area.  ``bbox`` is the bounding box of the true clipped polygon, so
    metadata and the Viewer still describe the on-image part.
    """
    if not isinstance(candidate, dict):
        raise ContractError(f"候选必须是对象：{candidate!r}")
    kind = candidate.get("kind")
    if kind is None:
        if "bbox" in candidate:
            kind = "bbox"
        elif "points" in candidate:
            kind = "polygon"
        else:
            raise ContractError("候选必须带 kind、bbox 或 points")
    if kind in RESERVED_CANDIDATE_KINDS:
        raise ContractError(f"候选类型 {kind!r} 已预留但尚未实现")
    if kind not in SUPPORTED_CANDIDATE_KINDS:
        raise ContractError(f"未知候选类型：{kind!r}")
    candidate_id = str(candidate.get("candidate_id") or f"cand-{index + 1}")
    diagnostics = []
    if kind == "bbox":
        bbox = candidate.get("bbox")
        if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
            raise ContractError(f"候选框必须是 [x0,y0,x1,y1]：{bbox!r}")
        x0, y0, x1, y1 = (_integer(value, "候选框坐标") for value in bbox)
        if x1 < x0 or y1 < y0:
            raise ContractError(f"候选框坐标非法：{bbox!r}")
        clipped = [
            max(0, x0), max(0, y0),
            min(width - 1, x1), min(height - 1, y1),
        ]
        if clipped[0] > clipped[2] or clipped[1] > clipped[3]:
            raise ContractError(f"候选框完全越界：{bbox!r}")
        if clipped != [x0, y0, x1, y1]:
            diagnostics.append({
                "code": "candidate_clipped",
                "candidate_id": candidate_id,
                "from": [x0, y0, x1, y1],
                "to": clipped,
            })
        points = None
        bbox_out = clipped
    else:
        raw_points = candidate.get("points")
        if not isinstance(raw_points, (list, tuple)) or len(raw_points) < 3:
            raise ContractError("多边形至少需要 3 个顶点")
        points = [_normalize_point(point) for point in raw_points]
        if len({tuple(point) for point in points}) < 3 or _polygon_area(points) == 0:
            raise ContractError(f"多边形退化：{raw_points!r}")
        clipped_points = clip_polygon(points, width, height)
        if len(clipped_points) < 3 or _polygon_area(clipped_points) <= 0:
            raise ContractError(f"多边形完全越界或裁剪后退化：{raw_points!r}")
        if any(x < 0 or x > width - 1 or y < 0 or y > height - 1
               for x, y in points):
            diagnostics.append({
                "code": "polygon_clipped",
                "candidate_id": candidate_id,
                "from": points,
                "to": clipped_points,
            })
        # Keep the detector's original geometry for rasterization; only the
        # metadata bbox is clipped.  PIL clips drawing to the strip canvas.
        xs = [point[0] for point in clipped_points]
        ys = [point[1] for point in clipped_points]
        bbox_out = [
            max(0, math.floor(min(xs))),
            max(0, math.floor(min(ys))),
            min(width - 1, math.ceil(max(xs))),
            min(height - 1, math.ceil(max(ys))),
        ]

    score = candidate.get("score")
    if score is not None:
        if (isinstance(score, bool) or not isinstance(score, (int, float))
                or not math.isfinite(score)):
            raise ContractError(f"候选 score 非法：{score!r}")
        score = float(score)
    label = candidate.get("label")
    if label is not None and not isinstance(label, str):
        raise ContractError(f"候选 label 必须是字符串：{label!r}")
    return ({
        "candidate_id": candidate_id,
        "kind": kind,
        "bbox": bbox_out,
        "points": points,
        "score": score,
        "label": label,
    }, diagnostics)


def normalize_candidates(candidates, width, height):
    if not isinstance(candidates, (list, tuple)):
        raise ContractError("candidates 必须是列表")
    width = positive_int(width, "图像宽度")
    height = positive_int(height, "图像高度")
    normalized = []
    diagnostics = []
    for index, candidate in enumerate(candidates):
        item, item_diagnostics = normalize_candidate(
            candidate, width, height, index)
        normalized.append(item)
        diagnostics.extend(item_diagnostics)
    return normalized, diagnostics


def candidate_geometry(candidate):
    """Return ``("bbox", [x0,y0,x1,y1])`` or ``("polygon", points)``."""
    if candidate["kind"] == "bbox":
        return "bbox", candidate["bbox"]
    return "polygon", [list(point) for point in candidate["points"]]


# ---------------------------------------------------------------------------
# DetectionResult
# ---------------------------------------------------------------------------

def validate_detection_result(result, *, width=None, height=None, mode=None):
    """Validate and normalize a DetectionResult.

    ``mode`` is the execution mode (``demo`` / ``production``).  A demo batch
    may exercise a real (deterministic, CPU-only) detector, but production must
    reject ``mock``: a MockDetector must never be able to claim a production
    result by changing one field.  The mask and every downstream event inherit
    this provenance, so a demo result is never reported as a production repair.
    """
    if not isinstance(result, dict):
        raise ContractError("DetectionResult 必须是对象")
    if result.get("contract_version") != DETECTION_CONTRACT_VERSION:
        raise ContractError(
            "DetectionResult 版本必须是 "
            f"{DETECTION_CONTRACT_VERSION}：{result.get('contract_version')!r}")
    if result.get("coordinate_space") != COORDINATE_SPACE:
        raise ContractError(
            "DetectionResult 坐标空间必须是 "
            f"{COORDINATE_SPACE}：{result.get('coordinate_space')!r}")
    size = result.get("image_size")
    if not isinstance(size, dict):
        raise ContractError("DetectionResult.image_size 必须是对象")
    result_width = positive_int(size.get("width", size.get("W")), "检测宽度")
    result_height = positive_int(size.get("height", size.get("H")), "检测高度")
    if width is not None and positive_int(width, "期望宽度") != result_width:
        raise ContractError("DetectionResult 宽度与冻结输入不一致")
    if height is not None and positive_int(height, "期望高度") != result_height:
        raise ContractError("DetectionResult 高度与冻结输入不一致")

    item_id = require_string(result.get("item_id"), "item_id")
    image_id = require_string(result.get("image_id"), "image_id")
    provenance = require_string(result.get("provenance"), "provenance")
    if provenance not in PROVENANCES:
        raise ContractError(f"未知 provenance：{provenance!r}")
    if mode == "production" and provenance == "mock":
        raise DetectionError("production 模式拒绝 provenance=mock 的检测结果")
    detector_name = require_string(result.get("detector_name"), "detector_name")
    detector_version = require_string(
        result.get("detector_version"), "detector_version")

    candidates, diagnostics = normalize_candidates(
        result.get("candidates", []), result_width, result_height)
    existing = result.get("diagnostics") or []
    if not isinstance(existing, list):
        raise ContractError("DetectionResult.diagnostics 必须是列表")
    return {
        **result,
        "contract_version": DETECTION_CONTRACT_VERSION,
        "item_id": item_id,
        "image_id": image_id,
        "image_size": {"width": result_width, "height": result_height},
        "coordinate_space": COORDINATE_SPACE,
        "candidates": candidates,
        "candidate_count": len(candidates),
        "detector_name": detector_name,
        "detector_version": detector_version,
        "provenance": provenance,
        "diagnostics": list(existing) + diagnostics,
    }


# ---------------------------------------------------------------------------
# MaskResult
# ---------------------------------------------------------------------------

def _sha256_value(value, label):
    if not isinstance(value, str) or not _SHA256_RE.match(value):
        raise ContractError(f"{label} 必须是 64 位小写十六进制 sha256：{value!r}")
    return value


def _positive_count(value, label):
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ContractError(f"{label} 必须是正整数：{value!r}")
    return value


def validate_mask_result(result):
    """Validate a MaskResult produced by ``batch_mask.build_mask_result``."""
    if not isinstance(result, dict):
        raise ContractError("MaskResult 必须是对象")
    if result.get("contract_version") != MASK_CONTRACT_VERSION:
        raise ContractError(
            "MaskResult 版本必须是 "
            f"{MASK_CONTRACT_VERSION}：{result.get('contract_version')!r}")
    if result.get("coordinate_space") != COORDINATE_SPACE:
        raise ContractError(
            "MaskResult 坐标空间必须是 "
            f"{COORDINATE_SPACE}：{result.get('coordinate_space')!r}")
    path = require_string(result.get("path"), "mask.path")
    sha256 = _sha256_value(result.get("sha256"), "mask.sha256")
    width = positive_int(result.get("width"), "mask.width")
    height = positive_int(result.get("height"), "mask.height")
    if result.get("dtype") != MASK_DTYPE:
        raise ContractError(f"mask.dtype 必须是 {MASK_DTYPE}")
    if result.get("bits") != 8:
        raise ContractError("mask.bits 必须是 8")
    if result.get("spp") != 1:
        raise ContractError("mask.spp 必须是 1")
    if result.get("background_value") != BACKGROUND_VALUE:
        raise ContractError(f"mask.background_value 必须是 {BACKGROUND_VALUE}")
    if result.get("foreground_value") != FOREGROUND_VALUE:
        raise ContractError(f"mask.foreground_value 必须是 {FOREGROUND_VALUE}")
    foreground = _positive_count(
        result.get("foreground_pixels"), "mask.foreground_pixels")
    item_id = require_string(result.get("item_id"), "mask.item_id")
    source_image_key = require_string(
        result.get("source_image_key"), "mask.source_image_key")
    source_revision = require_string(
        result.get("source_revision"), "mask.source_revision")
    provenance = require_string(result.get("provenance"), "mask.provenance")
    if provenance not in PROVENANCES:
        raise ContractError(f"未知 mask.provenance：{provenance!r}")
    builder_name = require_string(
        result.get("builder_name"), "mask.builder_name")
    builder_version = require_string(
        result.get("builder_version"), "mask.builder_version")
    detector_name = require_string(
        result.get("detector_name"), "mask.detector_name")
    detector_version = require_string(
        result.get("detector_version"), "mask.detector_version")
    if result.get("detection_contract_version") != DETECTION_CONTRACT_VERSION:
        raise ContractError(
            "mask.detection_contract_version 必须是 "
            f"{DETECTION_CONTRACT_VERSION}")

    overlay = result.get("overlay")
    if not isinstance(overlay, dict):
        raise ContractError("mask.overlay 必须是对象")
    overlay_path = require_string(overlay.get("path"), "overlay.path")
    overlay_sha = _sha256_value(overlay.get("sha256"), "overlay.sha256")
    overlay_width = positive_int(overlay.get("width"), "overlay.width")
    overlay_height = positive_int(overlay.get("height"), "overlay.height")
    overlay_scale = positive_int(overlay.get("scale"), "overlay.scale")
    if overlay.get("kind") != "png":
        raise ContractError("overlay.kind 目前只支持 png")

    diagnostics = result.get("diagnostics") or []
    if not isinstance(diagnostics, list):
        raise ContractError("mask.diagnostics 必须是列表")
    return {
        **result,
        "contract_version": MASK_CONTRACT_VERSION,
        "path": path,
        "sha256": sha256,
        "width": width,
        "height": height,
        "dtype": MASK_DTYPE,
        "bits": 8,
        "spp": 1,
        "background_value": BACKGROUND_VALUE,
        "foreground_value": FOREGROUND_VALUE,
        "foreground_pixels": foreground,
        "item_id": item_id,
        "source_image_key": source_image_key,
        "source_revision": source_revision,
        "provenance": provenance,
        "builder_name": builder_name,
        "builder_version": builder_version,
        "detector_name": detector_name,
        "detector_version": detector_version,
        "detection_contract_version": DETECTION_CONTRACT_VERSION,
        "overlay": {
            **overlay,
            "path": overlay_path,
            "sha256": overlay_sha,
            "width": overlay_width,
            "height": overlay_height,
            "scale": overlay_scale,
            "kind": "png",
        },
        "diagnostics": diagnostics,
    }
