"""Shared scene-name parsing used by the HTTP resolver and batch import.

The batch Worker must never call the HTTP resolver: it has no request context and
must not depend on the API being alive.  This module keeps the one piece both
paths need — turning a production name into candidate scene directories and
stating whether a directory really is a submit-ready scene — in one place.
"""
from __future__ import annotations
from typing import Callable, Optional

from pathlib import Path

from backend.pathguard import (
    PathDeniedError,
    ensure_allowed,
    infer_scene_paths,
    parse_scene_date,
    scene_name_products,
)
from backend.services import scene_search


class SceneLookupError(ValueError):
    """A name cannot be turned into exactly one valid scene directory."""


def name_candidates(name: str, *, date: Optional[str] = None,
                    product: Optional[str] = None) -> dict:
    """Return the ordered candidates for a QC-TXT scene name.

    ``date`` defaults to the 14/8-digit timestamp inside the name.  ``product``
    is the optional image type from the QC line; when the name omits the final
    product segment, ``scene_name_products`` appends the accepted variants.
    """
    raw = str(name or "").strip()
    if not raw:
        raise SceneLookupError("场景名为空")
    iso = str(date or "").strip() or parse_scene_date(raw)
    if not iso:
        raise SceneLookupError("名字里没有 14/8 位成像时间戳，无法反推日期")
    products = scene_name_products(raw, product)
    if not products:
        raise SceneLookupError("无法生成场景名候选")
    base = infer_scene_paths(products[0][0], iso)
    tried = [(candidate, raw) for candidate in base]
    for stem, _code in products[1:]:
        tried += [(candidate, raw) for candidate in infer_scene_paths(stem, iso)]
    return {
        "date": iso,
        "products": products,
        "base_candidates": base,
        "candidates": tried,
    }


def resolve_scene_dir(name: str, *, date: Optional[str] = None,
                      product: Optional[str] = None) -> dict:
    """Resolve exactly one submit-ready scene directory.

    A submit-ready scene, for this platform, is a directory inside
    ``SR_ALLOWED_ROOTS`` that carries ``<dirname>_meta.xml`` and has an input
    raster.  Candidates are only stat'ed by fixed name; we never list a disk
    directory.  More than one live candidate is an error: silently choosing one
    would be the pairing guess the batch workflow explicitly forbids.
    """
    info = name_candidates(name, date=date, product=product)
    reasons: list[str] = []
    hits: dict[str, dict] = {}
    for candidate, _fingerprint_name in info["candidates"]:
        try:
            directory = ensure_allowed(candidate, kind="dir")
        except PathDeniedError as exc:
            reasons.append(f"{candidate}：{exc}")
            continue
        if not (directory / (directory.name + "_meta.xml")).is_file():
            reasons.append(f"{directory}：缺 {directory.name}_meta.xml")
            continue
        source = scene_search.input_scene_path(directory)
        if source is None:
            tried = "、".join(c.name for c in scene_search.input_candidates(directory))
            reasons.append(f"{directory}：目录里没有输入影像（找过 {tried}）")
            continue
        hits.setdefault(directory.as_posix(), {
            "dir": directory,
            "input": source,
            "date": info["date"],
            "product_candidates": [code for _stem, code in info["products"]],
        })
    if not hits:
        detail = "；".join(reasons) or "没有可用的反推候选"
        raise SceneLookupError("没找到合法场景目录 —— " + detail)
    if len(hits) > 1:
        raise SceneLookupError(
            "配对歧义：名字命中多个合法场景目录 —— " + "、".join(sorted(hits)))
    return next(iter(hits.values()))


def validated_tiff_directory(path: Path) -> Path:
    """Validate the production directory and its canonical input before listing."""
    directory = ensure_allowed(path, kind="dir").resolve(strict=True)
    ensure_allowed(directory, kind="dir")
    source = scene_search.input_scene_path(directory)
    meta = directory / f"{directory.name}_meta.xml"
    if (source is None or source.resolve(strict=True).parent != directory
            or meta.resolve(strict=True).parent != directory):
        raise PathDeniedError("无有效场景输入")
    ensure_allowed(source, kind="file")
    return directory


def direct_tiff(directory: Path, name: str) -> Path:
    candidate = directory / name
    if candidate.parent != directory or candidate.suffix.lower() not in (".tif", ".tiff"):
        raise PathDeniedError("TIFF 必须是场景目录的一级文件")
    if candidate.resolve(strict=True) != candidate:
        raise PathDeniedError("不接受 TIFF 符号链接")
    return ensure_allowed(candidate, kind="file")


def discover_tiffs(directory: Path, source_ref: Callable[[Path], str]) -> list[dict]:
    import os
    import time

    from backend.services.preview_jpg import source_key

    deadline = time.monotonic() + 1
    source = scene_search.input_scene_path(directory)
    files = []
    with os.scandir(directory) as entries:
        for index, entry in enumerate(entries):
            if index >= 256 or time.monotonic() >= deadline:
                return []
            if not entry.is_file(follow_symlinks=False) or Path(entry.name).suffix.lower() not in (".tif", ".tiff"):
                continue
            try:
                candidate = direct_tiff(directory, entry.name)
                info = candidate.stat()
            except (OSError, PathDeniedError, RuntimeError):
                continue
            kind, suffix = None, ""
            if candidate == source:
                kind = "input"
            elif source is not None:
                gf07_stage = scene_search.gf07_tiff_stage(directory, candidate, source)
                if gf07_stage:
                    kind, suffix = gf07_stage
                elif candidate.stem.lower() != "pan_ori":
                    stage = scene_search.stage_of_jpg(directory, source, candidate.stem)
                    if stage and stage[2] == candidate and stage[0] != "input":
                        kind, suffix = stage[:2]
            files.append({
                "name": entry.name, "id": source_ref(candidate), "size_bytes": info.st_size,
                "mtime_ms": info.st_mtime_ns // 1_000_000,
                "srcRev": f"{source_key(candidate)}-{info.st_size}-{info.st_mtime_ns}",
                "kind": kind, "suffix": suffix, "readOnly": True,
                "viewerRole": "reference" if candidate.stem.lower() == "pan_ori"
                              else "stage" if kind else "other",
            })
    return sorted(files, key=lambda item: item["name"].casefold()) if time.monotonic() < deadline else []
