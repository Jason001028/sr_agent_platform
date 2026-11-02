"""Shared scene-name parsing used by the HTTP resolver and batch import.

The batch Worker must never call the HTTP resolver: it has no request context and
must not depend on the API being alive.  This module keeps the one piece both
paths need — turning a production name into candidate scene directories and
stating whether a directory really is a submit-ready scene — in one place.
"""
from __future__ import annotations

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


def name_candidates(name: str, *, date: str | None = None,
                    product: str | None = None) -> dict:
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


def resolve_scene_dir(name: str, *, date: str | None = None,
                      product: str | None = None) -> dict:
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
