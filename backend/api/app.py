"""FastAPI skeleton — 阶段4：盘阵场景检索 + 懒生成预览 JPG。

启动：`uvicorn backend.api.app:create_app --factory`（env 式 config）。
测试用 `from backend.api.app import create_app` 后自行注入 env 再构造。

Env
---
SR_SCENES_ROOT    盘阵场景根（unset → fake 回退）
SR_PREVIEWS_ROOT  可选预览缓存根（须在 scenes 根内）
SR_API_HOST/PORT  仅 `python -m backend.api` 直启用
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from backend.api import paths
from backend.api.paths import PathDeniedError
from backend.services import scene_search
from backend.services.preview_jpg import (PreviewError, ensure_preview_jpg,
                                          scene_dims)

# W/H 探测缓存：LRU，key = (绝对路径, mtime)，header 级读取很廉价但盘阵文件多
_DIMS_CACHE: dict[tuple[str, float], dict] = {}
_DIMS_CACHE_MAX = 1024

_FAKE_W = [20000, 4096, 12000, 8192, 16000, 24000]
_FAKE_H = [20000, 2048, 6000, 8192, 8000, 12000]


def _cached_dims(abs_path: Path) -> dict | None:
    mtime = abs_path.stat().st_mtime
    key = (str(abs_path), mtime)
    hit = _DIMS_CACHE.get(key)
    if hit is not None:
        return hit
    if len(_DIMS_CACHE) >= _DIMS_CACHE_MAX:
        _DIMS_CACHE.clear()
    dims = scene_dims(str(abs_path))
    if dims is not None:
        _DIMS_CACHE[key] = dims
    return dims


def _fake_dims(sid: str) -> tuple[int, int]:
    """Deterministic placeholder W/H for fake rows (no real file)."""
    acc = sum(ord(c) for c in sid)
    return _FAKE_W[acc % len(_FAKE_W)], _FAKE_H[acc % len(_FAKE_H)]


def _scene_row(scene: dict, root: Path | None) -> dict:
    """Wrap one search_scenes row with W/H + jpgUrl (opaque id, no abs path).

    `id` 是供 /api/scenes/{id}/preview 解析的**不透明 id**（base64url rel），
    fake 行不可解析保留原串；`name` = 可读文件名（stem）。
    """
    stem = scene["id"]
    row = {
        "id": stem,
        "name": stem,
        "satellite": scene.get("satellite"),
        "sensor": scene.get("sensor"),
        "date": scene.get("date"),
        "size_bytes": scene.get("size_bytes", 0),
        "fake": bool(scene.get("fake")),
        "W": None, "H": None,
        "rel": None, "jpgUrl": None, "hasPreview": False,
    }
    if scene.get("fake") or root is None:
        w, h = _fake_dims(stem)
        row["W"], row["H"] = w, h
        return row

    # disk 行：路径必须过白名单（扫描本身已保证在根内，双保险再拦一次）
    abs_path = paths.ensure_within(scene["path"], root)
    dims = _cached_dims(abs_path)
    if dims:
        row["W"], row["H"] = dims["W"], dims["H"]
    rel = paths.rel_of_scene(abs_path, root)
    row["rel"] = rel
    row["id"] = paths.scene_id(rel)
    jpg = paths.preview_jpg_path(abs_path, root)
    row["hasPreview"] = jpg.is_file()
    row["jpgUrl"] = paths.rel_url(jpg, root)
    return row


def create_app() -> FastAPI:
    root = paths.scenes_root()

    app = FastAPI(title="sr_agent_platform api", version="0.4.0")
    # 盘阵直连 IP:端口；前端另配 staticBase/apiBase，CORS 全放（内网）
    app.add_middleware(CORSMiddleware, allow_origins=["*"],
                       allow_methods=["*"], allow_headers=["*"])

    @app.get("/api/health")
    def health():
        return {"ok": True, "source": "disk" if root else "fake"}

    @app.get("/api/scenes")
    def list_scenes(
        query: str = "",
        satellite: str | None = Query(default=None),
        sensor: str | None = Query(default=None),
        date_from: str | None = Query(default=None, alias="dateFrom"),
        date_to: str | None = Query(default=None, alias="dateTo"),
        limit: int = Query(default=20, ge=1, le=500),
    ):
        out = scene_search.search_scenes(
            str(root) if root else None, query=query, satellite=satellite,
            sensor=sensor, date_from=date_from, date_to=date_to, limit=limit)
        rows = [_scene_row(s, root) for s in out["results"]]
        return {"source": out["source"], "scanned": out["scanned"],
                "count": out["count"], "results": rows}

    @app.get("/api/scenes/{scene_id}/preview")
    def preview(scene_id: str):
        if root is None:
            raise HTTPException(status_code=404,
                                detail="盘阵未配置，无场景可预览")
        try:
            abs_path = paths.scene_id_to_abs(scene_id, root)
        except PathDeniedError as e:
            raise HTTPException(status_code=404,
                                detail=f"场景不可访问：{e}") from e
        jpg = paths.preview_jpg_path(abs_path, root)
        try:
            ensure_preview_jpg(str(abs_path), str(jpg))
        except PreviewError as e:
            raise HTTPException(status_code=422,
                                detail=f"预览生成失败：{e}") from e
        return FileResponse(str(jpg), media_type="image/jpeg")

    return app
