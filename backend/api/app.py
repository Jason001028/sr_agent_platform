"""FastAPI app — 阶段4 场景检索/预览 + 阶段5 平台 API（chat/queue/masks/tools）。

启动：`uvicorn backend.api.app:create_app --factory`（env 式 config）。
测试用 `from backend.api.app import create_app` 后自行注入 env 再构造。

Env
---
SR_SCENES_ROOT    盘阵场景根（unset → fake 回退）
SR_PREVIEWS_ROOT  可选预览缓存根（须在 scenes 根内）
SR_AGENT_DB       SQLite 路径（chat 会话 + sr_tasks 同一库）
SR_LLM_MOCK       =1 → 聊天走固定脚本假 LLM（api-contract.md §5.1）
SR_SLURM_FAKE     =1 → 提交走内存假调度器（§5.2）
SR_QUEUE_POLL_SEC 队列后台校准广播周期，缺省 2s
SR_API_HOST/PORT  仅 `python -m backend.api` 直启用
"""

from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from backend.api import paths
from backend.api.paths import PathDeniedError
from backend.api.platform import _task_state, router as platform_router
from backend.config import load_config
from backend.services import scene_search, store as store_mod
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
    """Wrap one search_scenes row with W/H + jpgUrl (opaque id, no abs file path).

    `id` 是供 /api/scenes/{id}/preview 解析的**不透明 id**（base64url rel），
    fake 行不可解析保留原串；`name` = 可读文件名（stem）。
    仅 disk 行补只读 `lq_path`（scene 文件**父目录**绝对路径，无文件名）——供前端
    viewer 上下文侧舱把 /api/queue 任务行关联回场景；场景文件本身绝对路径不泄露。
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
        # 阶段5 viewer 上下文侧舱任务关联用：scene 文件父目录（= run_sr 的 lq_path，
        # queue 行 params.lq_path 与之同值）。只读字段，仅 disk 行非空；fake 恒 null。
        "lq_path": None,
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
    row["lq_path"] = str(abs_path.parent)
    if abs_path.suffix.lower() in (".jpg", ".jpeg"):
        # 盘阵里的 JPG 就是显示就绪图本身（§4.7）：不需要烘焙预览，
        # jpgUrl 直接指向源文件，前端拿到即开（不再走 /preview 生成端点）。
        row["hasPreview"] = True
        row["jpgUrl"] = paths.rel_url(abs_path, root)
        return row
    jpg = paths.preview_jpg_path(abs_path, root)
    row["hasPreview"] = jpg.is_file()
    row["jpgUrl"] = paths.rel_url(jpg, root)
    return row


async def _poll_loop(state) -> None:
    """队列后台校准广播（api-contract.md §4.3）：周期扫 job_id 非空任务，
    slurm.job_status 状态变化 → 写回 sr_tasks.status + 广播 job_update."""
    while True:
        await asyncio.sleep(state.poll_sec)
        try:
            await asyncio.to_thread(_poll_once, state)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — 单轮失败不 kill 循环
            pass


def _poll_once(state) -> None:
    for task in state.store.list_sr_tasks():
        if task["job_id"] is not None:
            _task_state(state, task)


def create_app() -> FastAPI:
    root = paths.scenes_root()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.poller = asyncio.create_task(_poll_loop(app.state))
        yield
        app.state.poller.cancel()
        try:
            await app.state.poller
        except asyncio.CancelledError:
            pass

    app = FastAPI(title="sr_agent_platform api", version="0.5.0",
                  lifespan=lifespan)
    # 盘阵直连 IP:端口；前端另配 staticBase/apiBase，CORS 全放（内网）
    app.add_middleware(CORSMiddleware, allow_origins=["*"],
                       allow_methods=["*"], allow_headers=["*"])

    # 阶段5 运行时状态：create_app 即建（env 已就绪）；lifespan 只额外启轮询。
    app.state.store = store_mod.Store()          # SR_AGENT_DB，懒打开
    app.state.cfg = load_config()
    app.state.chat_locks = {}                    # session_id → asyncio.Lock
    app.state.subscribers = set()                # /api/queue/events 订阅者
    app.state.task_cache = {}                    # task_id → queue display state
    app.state.poll_sec = float(os.environ.get("SR_QUEUE_POLL_SEC", "2"))
    app.include_router(platform_router)

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
        if abs_path.suffix.lower() in (".jpg", ".jpeg"):
            # 源就是显示就绪图（§4.7）：直接回源文件，不必（也无法）烘焙预览。
            # 正常路径下前端拿 hasPreview/jpgUrl 走静态 URL，不会打到这里，
            # 这是契约兜底：/preview 对任何场景行都返回可显示的 JPEG。
            return FileResponse(str(abs_path), media_type="image/jpeg")
        jpg = paths.preview_jpg_path(abs_path, root)
        try:
            ensure_preview_jpg(str(abs_path), str(jpg))
        except PreviewError as e:
            raise HTTPException(status_code=422,
                                detail=f"预览生成失败：{e}") from e
        return FileResponse(str(jpg), media_type="image/jpeg")

    return app
