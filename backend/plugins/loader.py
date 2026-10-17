"""Discover trusted source plugin manifests without importing plugin code."""

from __future__ import annotations

from pathlib import Path

from .manifest import PluginManifest, load_manifest


def discover_manifests(root: str | Path) -> tuple[list[PluginManifest], list[str]]:
    base = Path(root)
    found: list[PluginManifest] = []
    errors: list[str] = []
    seen: set[str] = set()
    if not base.is_dir():
        return found, errors
    for path in sorted(base.glob("*/manifest.json")):
        try:
            item = load_manifest(path)
            if item.id in seen:
                raise ValueError(f"重复插件 ID：{item.id}")
            seen.add(item.id)
            found.append(item)
        except (OSError, ValueError, TypeError) as exc:
            errors.append(f"{path}: {exc}")
    return found, errors
