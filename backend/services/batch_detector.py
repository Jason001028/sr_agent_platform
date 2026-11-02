"""PR C's deterministic Detector stand-in.

The real blur/cloud detector is PR D's job.  This module deliberately produces
one *predictable* center box so the pipeline, Mask contract and SR handoff can
be exercised without a model, GPU or disk-array scan.  ``provenance`` is always
``mock`` and is copied into the item checkpoint and the batch report; it must
never be mistaken for a production detection result.

A candidate is a closed, zero-based pixel box ``[x0, y0, x1, y1]`` in PAN
pixel space.  The Mask writer consumes exactly this shape.
"""

from __future__ import annotations

import os


class DetectionError(RuntimeError):
    """The item cannot produce a valid candidate box."""


def _dimension(value, label):
    if type(value) is not int or value <= 0:
        raise DetectionError(f"{label} 不是正整数：{value!r}")
    return value


def detect(target_image: dict) -> dict:
    """Return a deterministic detection result for one PAN target.

    ``SR_BATCH_DETECTION=none`` produces a successful empty result (NO_ISSUE);
    ``SR_BATCH_DETECTION=error`` raises, which the worker turns into a bounded
    retry.  Any other value uses the center box.
    """
    width = _dimension(target_image.get("width"), "PAN 宽度")
    height = _dimension(target_image.get("height"), "PAN 高度")
    mode = (os.environ.get("SR_BATCH_DETECTION") or "center").strip().lower()
    base = {
        "provenance": "mock",
        "method": mode,
        "width": width,
        "height": height,
        "image_key": target_image.get("image_key"),
        "revision": target_image.get("revision"),
    }
    if mode == "none":
        return {**base, "candidates": []}
    if mode == "error":
        raise DetectionError("SR_BATCH_DETECTION=error")
    if mode != "center":
        raise DetectionError(f"未知检测模式：{mode}")

    # Leave a border so the box is strictly inside the PAN and never depends on
    # image content. For tiny fixtures (16x16) this is still at least 1 pixel.
    margin_x = max(1, width // 4)
    margin_y = max(1, height // 4)
    x0, y0 = margin_x, margin_y
    x1, y1 = width - 1 - margin_x, height - 1 - margin_y
    if x1 < x0 or y1 < y0:
        x0 = y0 = 0
        x1, y1 = width - 1, height - 1
    return {**base, "candidates": [{"bbox": [x0, y0, x1, y1]}]}
