"""Read-only viewer context contract for trusted extensions."""

from __future__ import annotations

from typing import Any

SCHEMA_VERSION = 1


def validate_viewer_context(value: Any) -> tuple[bool, str | None]:
    if not isinstance(value, dict):
        return False, "context must be a JSON object"
    if value.get("schema_version") != SCHEMA_VERSION:
        return False, f"unsupported schema_version: {value.get('schema_version')!r}"
    for key in ("scene", "source", "image", "coordinates", "capabilities"):
        if not isinstance(value.get(key), dict):
            return False, f"context.{key} must be an object"
    scene = value["scene"]
    if scene.get("id") is not None and not isinstance(scene.get("id"), str):
        return False, "context.scene.id must be a string or null"
    image = value["image"]
    for key in ("width", "height", "display_width", "display_height"):
        n = image.get(key)
        if not isinstance(n, int) or isinstance(n, bool) or n <= 0:
            return False, f"context.image.{key} must be a positive integer"
    if value.get("roi") is not None and not isinstance(value["roi"], dict):
        return False, "context.roi must be an object or null"
    return True, None


def normalize_viewer_context(value: dict) -> dict:
    """Return a JSON-safe copy after validation by the caller."""
    ok, error = validate_viewer_context(value)
    if not ok:
        raise ValueError(error)
    return dict(value)
