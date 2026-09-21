"""FastAPI app — 阶段4 场景检索/预览 + 阶段5 平台 API（chat/queue/masks/tools）。

启动：`uvicorn backend.api.app:create_app --factory`（env 式 config）。
测试用 `from backend.api.app import create_app` 后自行注入 env 再构造。

Env
---
SR_SCENES_ROOT    盘阵场景根（unset → fake 回退）
SR_PREVIEWS_ROOT  可选预览缓存根（须在 scenes 根内，长期缓存）
SR_TEMP_PREVIEWS_ROOT 拖拽入口的**兜底**预览缓存根（1 天 TTL，每日 0 点清）；
                  仅在场景目录不可写时才用，默认系统临时目录
                  （见 services/preview_cache.py 的约定）

预览下采样档位：三条入口（拖入 / 场景库 / 粘盘阵路径）都由查询参数 `div` 指定，
取值限 `preview_jpg.PREVIEW_DIVISORS`（各边 ÷2…÷32），缺省 2（= 旧行为，逐字节不变）。
「默认 ÷4」只是**前端**的 UI 默认值，不是本层的契约。
SR_AGENT_DB       SQLite 路径（chat 会话 + sr_tasks 同一库）
SR_LLM_MOCK       =1 → 聊天走固定脚本假 LLM（api-contract.md §5.1）
SR_SLURM_FAKE     =1 → 提交走内存假调度器（§5.2）
SR_QUEUE_POLL_SEC 队列后台校准广播周期，缺省 2s
SR_PRODUCT_PREVIEW_DIV
                  作业转 COMPLETED 后服务端主动烤**产物**预览用哪一档（÷N）；
                  **0 = 关**（只留打开时的惰性路径），缺省 4。取值非法当 0 处理。
                  与前端 DEFAULT_PREVIEW_DIV 是两个独立的 4，互不联动 —— 浏览器
                  的档位在 localStorage，服务端看不见，所以急烤必须有服务端默认档。
SR_PRODUCT_PREVIEW_MAX_AGE_SEC
                  只烤 `finished_at` 在这个窗口内的 COMPLETED 行，缺省 86400。
                  这是升级当天不把历史 COMPLETED 行全烤一遍的唯一屏障。
SR_API_HOST/PORT  仅 `python -m backend.api` 直启用
"""

from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from backend.api import paths
from backend.api.paths import PathDeniedError
from backend.api.platform import (
    _broadcast, _json_body, _run_dataroot, _task_state, derived_mask_path,
    router as platform_router)
from backend.config import load_config
from backend.pathguard import (
    ensure_allowed, flat_scene_layout, infer_scene_paths, is_within,
    looks_like_scene_name, parse_scene_date, production_tree_depth,
    strip_raster_ext, to_posix_array_path)
from backend.services import preview_cache
from backend.services import run_sr as run_sr_svc
from backend.services import scene_search, store as store_mod
from backend.services.preview_jpg import (PREVIEW_DIVISORS, PREVIEW_JPG_QUALITY,
                                          PreviewError, build_preview_pixels,
                                          cache_hit, ensure_preview_jpg,
                                          preview_div_of, preview_max_edge,
                                          scene_dims, write_preview_jpg)

# W/H 探测缓存：LRU，key = (绝对路径, mtime)，header 级读取很廉价但盘阵文件多
_DIMS_CACHE: dict[tuple[str, float], dict] = {}
_DIMS_CACHE_MAX = 1024

_FAKE_W = [20000, 4096, 12000, 8192, 16000, 24000]
_FAKE_H = [20000, 2048, 6000, 8192, 8000, 12000]


def _cached_dims(abs_path: Path) -> dict | None:
    """影像尺寸（带 LRU 缓存）；**文件不在/读不出 → None**，不抛。

    None 而不是抛异常，是和 `scene_dims` 对齐的：这个函数的语义是「能读就读，
    读不出就不知道」，而「不知道」在调用方那里都有明确且正确的去向（行不带 W/H、
    比较判不出就不换图）。以前这里是直接 stat，调用方全都先验证过文件存在所以
    从没撞上；`_raster_preview` 是第一个可能拿到不存在路径的调用方
    （盘阵那份显示件 jpg 被删/改名，用户手上只有本地副本）—— 那种情况该退化成
    「不换图」，不该把整个 resolve 打成 500。
    """
    try:
        mtime = abs_path.stat().st_mtime
    except OSError:
        return None
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


# 预览档位缓存：LRU，key = (预览 JPG 绝对路径, mtime)。与 _DIMS_CACHE 同一范式 ——
# 只读 JPEG 注释头、不解码像素，但列表路径上逐行都要来一次，仍值得缓存。
# 注意 `None` 是**合法结果**（「不知道是哪一档」），所以判命中必须用 `in` 而不是 .get()。
_DIV_CACHE: dict[tuple[str, float], int | None] = {}
_DIV_CACHE_MAX = 1024


def _cached_preview_div(jpg: Path) -> int | None:
    """盘上这份预览 JPG 是按哪一档烤的；不存在 / 旧格式 / 读不出 → None。

    None 对前端的含义是「不知道是哪一档」→ 按当前档位重烤一轮。v2 那代戳解不出
    div，所以换包后每个场景首次打开都会重烤一次（惰性，预期内）。
    """
    try:
        mtime = jpg.stat().st_mtime
    except OSError:
        return None
    key = (str(jpg), mtime)
    if key in _DIV_CACHE:
        return _DIV_CACHE[key]
    if len(_DIV_CACHE) >= _DIV_CACHE_MAX:
        _DIV_CACHE.clear()
    div = preview_div_of(jpg)
    _DIV_CACHE[key] = div
    return div


def _check_div(div: int) -> int:
    """校验下采样档位，非法 → 400（而不是烘出一张奇怪尺寸的图）。"""
    if div not in PREVIEW_DIVISORS:
        raise HTTPException(
            status_code=400,
            detail=f"下采样档位非法：{div}（只认 "
                   f"{'/'.join(map(str, PREVIEW_DIVISORS))}）")
    return div


def _fake_dims(sid: str) -> tuple[int, int]:
    """Deterministic placeholder W/H for fake rows (no real file)."""
    acc = sum(ord(c) for c in sid)
    return _FAKE_W[acc % len(_FAKE_W)], _FAKE_H[acc % len(_FAKE_H)]


def _norm_dir(path) -> str:
    """目录路径归一成 POSIX、去尾斜杠 —— 跨来源比对用（提交侧存的是 POSIX 形态）。"""
    return str(path or "").replace("\\", "/").rstrip("/")


def _raster_preview(abs_path: Path, root: Path | None) -> dict | None:
    """显示件 jpg 的「同名栅格」预览信息；不适用 → None。

    盘阵里预生成的显示件（`PAN.jpg` / `<编号>.jpg`，长边约 8192）配着一张同名栅格
    （`PAN.tif` / `<编号>.tif`）。服务端从那张栅格烤出来的图可能比这张 jpg 更清晰
    （`max_edge = round(max(W,H)/div)` 与这张 jpg 的长边比大小，见 api-contract
    「显示源比较规则」）——所以这一项存在的意义就是**把比较所需的两个尺寸交给前端**：
    栅格 W/H 与 jpg W/H 都得后端读（前端拿不到库外文件，也不该为此多发一次请求）。

    落点与栅格行**同一份** `<stem>.preview.jpg`（`with_suffix` 对 jpg 与 tif 是同一个
    文件名），所以 `jpgUrl` 指的就是栅格行会用的那个 URL —— 同一份缓存，不重复烤。

    `rel` 只在栅格落在 SR_SCENES_ROOT 之下才有（库外没有静态 URL，与行本身的
    `rel/jpgUrl` 同一条规则）；`id` 跟着走：库内用 rel 的 id，库外用 `~` 形态的
    绝对路径 id，两种 `/api/scenes/{id}/preview` 都认。

    尺寸任一侧读不出 → None（**保守**：宁可继续显示那张 jpg，也不拿一个算不准的
    比较结果把图换掉）。
    """
    if abs_path.suffix.lower() not in (".jpg", ".jpeg"):
        return None
    raster = scene_search.sibling_raster_path(abs_path)
    if raster is None:
        return None
    rdims = _cached_dims(raster)
    jdims = _cached_dims(abs_path)
    if not rdims or not jdims:
        return None
    jpg = paths.preview_jpg_for(raster, root)
    out = {
        "id": paths.scene_id_abs(raster),
        "name": raster.name,
        "rel": None, "jpgUrl": None,
        "rasterW": rdims["W"], "rasterH": rdims["H"],
        "jpgW": jdims["W"], "jpgH": jdims["H"],
        "hasPreview": jpg.is_file(),
        "previewDiv": _cached_preview_div(jpg),
    }
    if root is not None and is_within(raster, root):
        out["rel"] = paths.rel_of_scene(raster, root)
        out["id"] = paths.scene_id(out["rel"])
        if is_within(jpg, root):
            out["jpgUrl"] = paths.rel_url(jpg, root)
    return out


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
        # 盘上那份预览是按哪一档烤的（null = 没有/旧格式/读不出）。前端据它与
        # 当前档位比对，决定要不要重烤 —— hasPreview 不认档位，只看它不够。
        "previewDiv": None,
        # 阶段5 viewer 上下文侧舱任务关联用：scene 文件父目录（= run_sr 的 lq_path，
        # queue 行 params.lq_path 与之同值）。只读字段，仅 disk 行非空；fake 恒 null。
        "lq_path": None,
        # 显示件 jpg 的同名栅格（见 _raster_preview）。**只对 jpg 源非空**，其余
        # 行恒 null —— 它是「要不要改从栅格烤」的比较依据，不是这一行自己的预览。
        "rasterPreview": None,
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
    # lq_path 一律盘阵 POSIX 形态：它会与 /api/queue 行 params.lq_path（提交侧
    # 归一化的 POSIX 值）逐字比对，宿主形态在 Windows 开发机上永远比不中。
    # Linux 上 as_posix() 与 str() 同值，生产行为不变。
    row["lq_path"] = abs_path.parent.as_posix()
    if abs_path.suffix.lower() in (".jpg", ".jpeg"):
        # 盘阵里的 JPG 就是显示就绪图本身（§4.7）：不需要烘焙预览，
        # jpgUrl 直接指向源文件，前端拿到即开（不再走 /preview 生成端点）。
        row["hasPreview"] = True
        row["jpgUrl"] = paths.rel_url(abs_path, root)
        # previewDiv 留 None：这不是烤出来的预览，不参与档位（前端靠 jpgUrl
        # 是不是 `.preview.jpg` 就能分辨，所以这里不需要额外哨兵值）。
        #
        # 上面三个字段的语义**一个字都不动**（gui-experience §9.2 的红线：这张 jpg
        # 就是这一行的显示源）。同目录若有同名栅格，它烤出来可能更清晰 —— 那件事
        # 由 rasterPreview 单独表达，由前端按「谁清晰用谁」决定，**不改这里的指向**。
        row["rasterPreview"] = _raster_preview(abs_path, root)
        return row
    jpg = paths.preview_jpg_path(abs_path, root)
    row["hasPreview"] = jpg.is_file()
    row["jpgUrl"] = paths.rel_url(jpg, root)
    row["previewDiv"] = _cached_preview_div(jpg)
    return row


def _same_file(a: Path, b: Path) -> bool:
    """两个路径是否指向同一个文件。

    不能直接用 `Path.__eq__`：它在 Windows 上是**大小写敏感**的字符串比较，而
    文件系统不是 —— 用户把盘符写成小写就会假阴性（把可提交的场景判成不可提交）。
    先试 `samefile`（stat 级判定，最准），失败再退回规范化字符串比较。
    """
    try:
        return os.path.samefile(a, b)
    except OSError:
        return os.path.normcase(str(a)) == os.path.normcase(str(b))


def _manual_row(abs_path: Path, dir_path: Path, root: Path | None) -> dict:
    """手工场景行（用户手填/反推的盘阵路径），与 `/api/scenes` 行同形。

    差别只有三处：`id` 是 `~` + base64url(绝对路径)（库行是 rel 的 base64url，
    两者靠前缀区分）；`rel` 只在源恰好落在 SR_SCENES_ROOT 之下时才有；`manual`
    恒为 True。后端不列举任何目录，`W/H` 来自文件头探测。
    """
    meta = scene_search.parse_filename(abs_path)
    row = {
        "id": paths.scene_id_abs(abs_path),
        "name": abs_path.stem,
        "satellite": meta["satellite"],
        "sensor": meta["sensor"],
        "date": meta["date"],
        "size_bytes": abs_path.stat().st_size,
        "fake": False,
        "W": None, "H": None,
        "rel": None, "jpgUrl": None, "hasPreview": False,
        # 同 _scene_row：盘上那份预览是按哪一档烤的（null = 没有/旧格式/读不出）。
        "previewDiv": None,
        # 同 _scene_row：lq_path 一律 POSIX（它会被原样带进提交表单）。
        "lq_path": dir_path.as_posix(),
        "manual": True,
        # 同 _scene_row：显示件 jpg 的同名栅格（只对 jpg 源非空）。
        "rasterPreview": _raster_preview(abs_path, root),
    }
    # hasPreview 一律填真值（缓存到底在不在），与是否在库内无关：库外没有静态
    # URL，但前端要靠它判断「这次会不会触发首次烘焙」并提示用户等待，所以不能
    # 因为「库外用不上」就一律留 False。
    jpg = paths.preview_jpg_for(abs_path, root)
    row["hasPreview"] = jpg.is_file()
    row["previewDiv"] = _cached_preview_div(jpg)
    if root is not None and is_within(abs_path, root):
        # 恰好也在场景库根之下（例如用户手填了库内路径）：静态预览 URL 可用，
        # 与库行行为对齐，省掉一次 /preview 生成。
        row["rel"] = paths.rel_of_scene(abs_path, root)
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


# --------------------------------------------------------------------------
# 产物预览急烤（api-contract.md §4.x）
# --------------------------------------------------------------------------
#: 急烤的默认档位（÷4）。与前端 `DEFAULT_PREVIEW_DIV` **数值相同但互不联动**：
#: 浏览器的档位在 localStorage 里，服务端看不见 —— 这就是急烤必须有自己缺省的原因。
#: 两者改一个不会带动另一个，要同步得两边都改。
DEFAULT_PRODUCT_PREVIEW_DIV = 4

#: 急烤只处理 `finished_at` 落在这么久以内的 COMPLETED 行。
DEFAULT_PRODUCT_PREVIEW_MAX_AGE_SEC = 86400.0

#: 每轮最多扫多少条候选（不是每轮烤多少 —— 那恒为 1）。不开成 env：配置面越小越好。
_EAGER_SCAN_LIMIT = 20


def _product_preview_div() -> int:
    """产物急烤用哪一档；**0 = 关**。

    **每次调用读 env**，不要在 create_app 里快照 —— 测试要能按用例改档位，真机上
    也不必为了关掉急烤而重启（改 service 文件后 restart 是常规操作，但少一次总好）。

    非法值（不在 `PREVIEW_DIVISORS` 里、或不是数字）**当 0 处理**而不是抛异常：
    配置写错不该让服务起不来，代价只是「这轮不烤」。
    """
    raw = (os.environ.get("SR_PRODUCT_PREVIEW_DIV") or "").strip()
    if not raw:
        return DEFAULT_PRODUCT_PREVIEW_DIV
    try:
        div = int(raw)
    except ValueError:
        return 0
    return div if div in PREVIEW_DIVISORS else 0


def _product_preview_max_age() -> float:
    """急烤的年龄窗口（秒）。读不出 / 负数 → 默认值。

    这是升级当天不把历史 COMPLETED 行全烤一遍的**唯一**屏障：`preview_state` 补列
    后老行全是 NULL，而 NULL 的含义就是「没烤过」。
    """
    raw = (os.environ.get("SR_PRODUCT_PREVIEW_MAX_AGE_SEC") or "").strip()
    try:
        val = float(raw) if raw else DEFAULT_PRODUCT_PREVIEW_MAX_AGE_SEC
    except ValueError:
        return DEFAULT_PRODUCT_PREVIEW_MAX_AGE_SEC
    return val if val > 0 else DEFAULT_PRODUCT_PREVIEW_MAX_AGE_SEC


def _source_sig(path: Path) -> tuple[float, int]:
    """源文件的身份 (mtime, size)，用来在落盘前复核它有没有被改写。"""
    st = path.stat()
    return (st.st_mtime, st.st_size)


async def _eager_preview_loop(state) -> None:
    """产物预览急烤的后台循环。

    与 `_poll_loop` 同形（睡一轮 → `to_thread` 干一轮 → 单轮失败不 kill 循环）。
    **第一件事是 sleep**，不和启动抢盘：服务刚起来还要做别的（校准队列、清缓存桶），
    而这里一动就是要读遍一个 GB 级文件。

    `SR_QUEUE_POLL_SEC` 在这里复用为「两件之间的间隔」：烤一份 40000² 产物真机要
    几十秒，2 秒的间隔等于每 ~30 秒一件，够快也不会把磁盘占满。
    """
    while True:
        await asyncio.sleep(state.poll_sec)
        try:
            await asyncio.to_thread(_eager_bake_tick, state)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — 单轮失败不 kill 循环
            pass


def _eager_bake_tick(state) -> None:
    """烤**一件**产物的预览（同步函数，由 `to_thread` 调用）。

    每轮只烤一件是**有意选的**：÷4 烤一份 40000² 产物的峰值内存约 400MB、要把整个
    文件读一遍，并发会把内存乘上去、把盘阵的带宽占满。单消费者 + 并发 1 的代价只是
    「一次性完成 20 个作业时最后一件要等十分钟」，而那是可解释的。
    """
    div = _product_preview_div()
    if not div:
        return
    for cand in state.store.list_preview_candidates(
            max_age_sec=_product_preview_max_age(), limit=_EAGER_SCAN_LIMIT):
        task = state.store.claim_preview_bake(cand["task_id"])
        if task is None:
            continue        # 被别处抢先认领，或这行在认领的空隙里被重交了
        _bake_product_preview(state, task, div)
        return


def _finish_preview(state, task: dict, state_name: str, note: str) -> None:
    """写回急烤结局并广播。任何结局都要走这里，不然行会永远停在 `running`。"""
    row = None
    try:
        row = state.store.set_preview_state(task["task_id"], state_name, note)
    except Exception:  # noqa: BLE001 — 写库失败不该让这一轮炸掉
        return
    if row is None:
        return
    # 单独的帧类型，不混进 job_update：那是「作业状态变了」。预览烤好了是另一件事，
    # 混在一起前端收到就得重取整行，而预览与作业状态无关。
    _broadcast(state, {"type": "preview_update", "task_id": row["task_id"],
                       "state": row.get("preview_state"),
                       "note": row.get("preview_note")})


def _bake_product_preview(state, task: dict, div: int) -> None:
    """把一条 COMPLETED 任务的**产物**预览烤出来（其余两类走惰性路径）。

    只烤产物，不烤输入影像、不烤 `_NOSR`：那两份的「用户到底要不要看」在打开之前
    无法知道，而产物是刚刚跑完的、几乎一定会被打开。三个落点天然独立
    （`<stem>.preview.jpg` / `<stem>_<suffix>.preview.jpg` / `…_NOSR.preview.jpg`），
    各烤各的，互不覆盖。
    """
    params = task.get("params") or {}
    lq_path = params.get("lq_path")
    suffix = str(params.get("suffix") or "")
    if not lq_path or not suffix:
        _finish_preview(state, task, "skipped",
                        "no_suffix: 任务行里没有 lq_path/suffix，拼不出产物名")
        return

    # 沙箱判据**必须是 `_run_dataroot`**，不能直接比 `SR_SANDBOX_ROOT`。
    # `_run_dataroot` 内部走 run_sr.sandbox_scene_paths，而那条在 SR_EXECUTOR=local
    # 时恒返回 None —— 真机当前正是「配了 SR_SANDBOX_ROOT + local executor」这条
    # 路线，直接比 env 会把本可以烤的产物判成「沙箱内」而永不烤。反过来，真在沙箱
    # 里跑时产物落在私有副本上，烤了用户也看不到，还往临时盘撒文件。
    dataroot = _run_dataroot(task)
    if dataroot is None:
        # `_run_dataroot` 只在沙箱开着、且 SR_SANDBOX_ROOT / fingerprint 不合法时
        # 返回 None。这时产物落在哪**不可知**，宁可如实跳过也不猜。
        _finish_preview(state, task, "skipped",
                        "sandbox: 沙箱根配置不可用，无法确定产物落在盘阵还是副本里，未烤")
        return
    if _norm_dir(dataroot) != _norm_dir(lq_path):
        _finish_preview(state, task, "skipped",
                        f"sandbox: 这次跑在沙箱私有副本上（{_norm_dir(dataroot)}），"
                        "盘阵里没有产物，未烤")
        return

    scene_dir = Path(_norm_dir(dataroot))
    inp = scene_search.input_scene_path(scene_dir)
    if inp is None:
        _finish_preview(state, task, "skipped",
                        f"product_missing: {scene_dir} 里找不到输入影像"
                        "（缺 <目录名>_meta.xml 或缺影像文件）")
        return

    cands = scene_search.product_candidates(inp, suffix)
    product = next((c for c in cands if c.is_file()), None)
    if product is None:
        # COMPLETED 但没有产物是**正常结局**（云限额跳过就是一个合法 COMPLETED），
        # 所以必须把试过的候选名报出来，好让运维一眼分辨「名字猜错了」还是
        # 「作业本身没产出」。
        _finish_preview(state, task, "skipped",
                        "product_missing: 试过 "
                        + " / ".join(c.name for c in cands) + "，都不存在")
        return

    # 目录不可写就如实跳过，**不退回 SR_TEMP_PREVIEWS_ROOT**：那份按天清，而急烤
    # 的意义是长期命中；更要命的是急烤**没有 HTTP 响应头**能告诉用户「这次退化了」，
    # 静默退化等于骗人。留给打开时的兜底路径去处理。
    if not os.access(str(product.parent), os.W_OK):
        _finish_preview(state, task, "skipped",
                        f"unwritable: {product.parent} 对服务账号不可写，"
                        "留给打开时的兜底路径")
        return

    jpg = paths.preview_jpg_for(product, state.scenes_root)
    # 用户在急烤动手之前先打开过这份产物：盘上那份已是当前档位，重烤纯属白干
    # （几十秒 + 读遍 GB 级文件）。判据与惰性路径**同一个函数**，没有第二套。
    try:
        if jpg.is_file() and os.path.getmtime(jpg) >= os.path.getmtime(product):
            hit = cache_hit(jpg, PREVIEW_JPG_QUALITY, div)
            if hit is not None:
                _finish_preview(state, task, "done",
                                f"cached: 盘上那份已是 ÷{div}"
                                f"（{hit['w']}×{hit['h']}）")
                return
    except OSError:
        pass        # 读不了 mtime 就当没命中，往下走正常流程

    try:
        before = _source_sig(product)
        max_edge = preview_max_edge(product, div)
        pixels = build_preview_pixels(str(product), max_edge)
        # **落盘前复核**：同一 suffix 重跑会覆盖同一个产物路径。若不复核，一次
        # 「读的时候是旧产物、写的时候新产物已经在写」就会把一张半截图永久留在盘上，
        # 而缓存判据是「不比源旧」—— 新的 mtime 可能仍晚于我们刚写的 jpg，它不会自愈。
        if _source_sig(product) != before:
            _finish_preview(state, task, "skipped",
                            "source_changed: 产物在烘焙途中被改写，本次一个字节都没写")
            return
        out = write_preview_jpg(jpg, pixels, div=div)
        _finish_preview(state, task, "done",
                        f"baked: ÷{div}（{out['w']}×{out['h']}）")
    except (PreviewError, OSError) as e:
        _finish_preview(state, task, "failed", f"failed: {e}")
    except Exception as e:  # noqa: BLE001 — 后台循环不该被一行拖死
        _finish_preview(state, task, "failed", f"failed: {type(e).__name__}: {e}")


def _latest_completed_suffix(store, lq_path: str) -> str | None:
    """该场景目录最近一条 **COMPLETED** 任务的 suffix；没有则 None。

    只认 COMPLETED：FAILED 已经动过产物路径，名字拼得出来但内容是半截 —— 让它进
    界面只会让人以为「这就是本次结果」。

    比对用归一化后的 POSIX 路径（提交侧存进 params 的就是 POSIX 形态，Windows 开发
    机上的宿主形态永远比不中）。走 `list_sr_tasks` 的内存过滤而不是 SQL LIKE：路径里
    的下划线在 LIKE 里是通配符，生产目录名全是下划线，靠转义兜着不如不写这条 SQL。
    """
    want = _norm_dir(lq_path)
    for row in store.list_sr_tasks():
        if (row.get("status") or "").upper() != "COMPLETED":
            continue
        p = row.get("params") or {}
        if _norm_dir(p.get("lq_path")) != want:
            continue
        s = p.get("suffix")
        if s:
            return str(s)
    return None


def _seconds_to_next_midnight(now: datetime | None = None) -> float:
    """到下一个**本地** 0 点的秒数（临时预览缓存的清扫时刻）。

    用 `combine(明天, min.time())` 而不是「now + 1 天再抹掉时分秒」：后者是在
    加了 24 小时之后取的日期，夏令时切换日会多/少一小时。这里只是决定什么时候
    删过期桶，差一小时无害，但算对更省事。下界 1s，避免时钟跳变导致的忙循环。
    """
    now = now or datetime.now()
    nxt = datetime.combine(now.date() + timedelta(days=1), datetime.min.time())
    return max(1.0, (nxt - now).total_seconds())


async def _tmp_preview_purge_loop() -> None:
    """临时预览缓存清扫（services/preview_cache.py）：**启动先清一次**，
    之后每天本地 0 点清一次，只留当天的桶。

    为什么放在后台任务里而不是请求路径上：`purge_temp_previews` 要 iterdir
    缓存根，而 `tests/test_scene_resolve.py::TestNeverListsDirectories` 在请求
    作用域内把 `os.listdir/scandir/walk` 与 `Path.rglob/glob/iterdir` 全打成了
    AssertionError ——「禁止扫盘」是硬约束，请求里碰不得。

    「算下一个 0 点」也放进 try：它若抛异常，任务会带着异常静默死掉，之后再没
    人清理（服务照跑，缓存无限涨）。单轮失败只该跳过这一轮。
    """
    while True:
        try:
            await asyncio.to_thread(preview_cache.purge_temp_previews)
            delay = _seconds_to_next_midnight()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — 清理失败不 kill 循环
            delay = 3600.0   # 算不出 0 点就退化成每小时试一次
        await asyncio.sleep(delay)


def _fingerprint_mismatch(inp: Path, name: str, size_bytes: int) -> str | None:
    """拖拽入口的身份门：拖进来的是**栅格**时比「名字 + 字节数」，是 **jpg** 时只比名字。
    返回人话原因（并入 404 的候选清单），None = 吻合。

    栅格为什么名字这一半不能省：`scene_search.input_candidates` 的次序是
    `<目录名>.{tif,tiff,img}` 在前、`PAN.{tif,tiff,img}` 在后，
    `input_scene_path` 返回第一个存在的。RC 场景的输入影像是 `PAN.tif`，但同
    目录里往往还躺着 `<目录名>.tif`（上游 SC 步骤的产物）。用户拖的若是后者，
    只比字节数就可能通过 —— 而 SR 在盘阵上跑的是 `PAN.tif`，用户画的掩码坐标
    会整片落在另一张图上。名字对不上一律不认，宁可退回浏览器本地解码。

    **栅格与 JPG 比的是两个不同的对象**，别混：

    * 拖进来的是栅格（.tif/.tiff/.img）：比 `<输入影像 stem>` + 字节数。两份都
      是栅格产物，字节数这一半是有意义的。
    * 拖进来的是 JPG：比 `<场景目录名>`，且**只**比它。盘阵上那份 jpg 是显示件
      （8bit 就绪的预览，见 `scene_search._IMAGE_EXTS`），SR 从来不在它上面跑，
      所以拿它的名字去比栅格输入的 stem 是比错了对象 —— 纯 RC 场景（目录里只有
      `PAN.tif`）下 `inp.stem` 是 `PAN`，而显示件叫 `<目录名>.jpg`（生产全名），
      永远比不过，于是「拖 jpg」这条入口恰恰在 SR 真要跑的那些场景上恒 404
      （2026-09-18 复现确认）。字节数那一半对 JPG 本就无意义（两份不同产物），
      照旧不比。
    """
    want = strip_raster_ext(name)
    if Path(name).suffix.lower() in (".jpg", ".jpeg"):
        # 比的是**场景目录名**。默认模板下候选目录名就是由这个名字（去后缀）拼出来
        # 的，所以 dir 存在时这条必然成立 —— 它挡的不是「这份 jpg 不属于这个场景」，
        # 而是「换了 SR_SCENE_PATH_TEMPLATE、场景目录改了命名」的部署（那种情况下
        # `<目录名>.jpg` 与推断出的目录名对不上）。真正把派生件（`_cloud.jpg`、
        # `.preview.jpg`）挡在外面的是候选目录根本不存在。
        # 大小写按 `scene_search.is_scene_file` 的口径（Windows 上用户拖进来的名字
        # 大小写不保证）。
        if want.lower() == inp.parent.name.lower():
            return None
        return (f"{inp.parent}：拖入的 {want} 与场景目录名 {inp.parent.name} "
                f"不是同一个名字（盘阵上这份显示件叫 {inp.parent.name}.jpg）")
    if inp.stem != want:
        return (f"{inp.parent}：目录里的输入影像是 {inp.name}，与拖入的 {want} "
                "不是同一个文件（SR 在这个目录上跑的是前者）")
    # 这一层不泛化成「后缀不同就放行」：那会连 SC.tiff 与 SC.tif 也放过，而拖错
    # 后缀（本机另存过一份 TIFF）恰恰是字节数这一半要挡的。
    try:
        actual = inp.stat().st_size
    except OSError as e:
        return f"{inp.parent}：读不到输入影像的大小（{e}）"
    if actual != size_bytes:
        return (f"{inp.parent}：输入影像 {inp.name} 的字节数与拖入的文件不一致"
                f"（盘阵 {actual} vs 拖入 {size_bytes}）")
    return None


#: 六层生产树里日期目录以下的三层；场景目录就是最深那层，目录名即**完整生产名**。
_SCENE_LEVELS = ("<卫星型号>", "<段级目录>", "<景级目录>")
#: 摆一个完整生产名，比抽象描述好认（用户回传的真机形态）。
_SCENE_NAME_EXAMPLE = "JXGF07D03_PMS_20260622052600_200516571_101_0006_001_L1_MSS"

#: 被粘目录在生产树里的位置（404 措辞按它分派）。
_LEVEL_DAY = "day"      # 日期目录 `<年>/<月>/<日>`
_LEVEL_SAT = "sat"      # 卫星型号层
_LEVEL_MID = "mid"      # 段级产品目录（景级目录名去掉景号那段）
_LEVEL_SCENE = "scene"  # 景级 = 场景目录
_LEVEL_BELOW = "below"  # 场景目录内部的子目录
_LEVEL_FLAT = "flat"    # 旧扁平形态里的场景目录（`<年>/<月>/<日>/<生产名>`）

_LEVEL_DESC = {
    _LEVEL_DAY: "这是日期目录（<年>/<月>/<日>）",
    _LEVEL_SAT: "这是生产树的卫星型号层",
    _LEVEL_MID: "这是段级产品目录（景级目录名去掉「景号」那一段所得）",
}
#: 各层之下、直到景级场景目录的那几层（404 消息里要指名道姓地列出来）。
_LEVEL_BELOW_LEVELS = {
    _LEVEL_DAY: _SCENE_LEVELS,
    _LEVEL_SAT: _SCENE_LEVELS[1:],
    _LEVEL_MID: _SCENE_LEVELS[2:],
}


def _dir_level(d: Path) -> str | None:
    """被粘目录在生产树里的位置 —— 纯词法（pathguard 两个原语），不 stat、不列举。"""
    depth = production_tree_depth(d)
    if depth is None:
        return None
    if flat_scene_layout(d):
        return _LEVEL_FLAT if depth == 1 else _LEVEL_BELOW
    return {0: _LEVEL_DAY, 1: _LEVEL_SAT, 2: _LEVEL_MID,
            3: _LEVEL_SCENE}.get(depth, _LEVEL_BELOW)


def _why_not_scene_dir(d: Path) -> str:
    """目录在、却不是场景目录时，说清**差在哪一层**（并进 404 的原因清单）。

    这里以前一律报「缺 <目录名>_meta.xml」，于是粘日期目录
    （`…/PRODUCT/2026/09/18`）得到的是「缺 18_meta.xml」：`<目录名>_meta.xml`
    的前缀是**完整生产名**（含 14 位成像时刻），`18` 这类前缀根本不可能构成它，
    用户读完也仍不知道该怎么办。

    层数由 `production_tree_depth` + `flat_scene_layout`（纯词法）判、名字由
    `looks_like_scene_name` 判，**两者都不参与准入** —— 准入仍是
    `<目录名>_meta.xml` 在不在（与 SR 的 `osp.basename(lq_path) + "_meta.xml"`
    同一口径，非生产树部署里的短名场景目录照样认）。
    """
    name = d.name
    level = _dir_level(d)
    # 目录就该是场景目录（扁平形态的它 / 六层树的景级层），或名字本身就是完整
    # 生产名 → 只差一份 meta，照实说。**段级层是唯一的例外**：它的名字也带 14 位
    # 成像时刻，光看名字会误报「缺 <段级名>_meta.xml」。
    if level in (_LEVEL_FLAT, _LEVEL_SCENE) or (
            level is None and looks_like_scene_name(name)):
        return f"{d}：缺 {name}_meta.xml（SR 靠它判 RC/SC，没有就跑不起来）"
    if level in _LEVEL_DESC:
        below = _LEVEL_BELOW_LEVELS[level]
        return (f"{d}：{_LEVEL_DESC[level]}，场景目录在它下面 {len(below)} 层 "
                f"{'/'.join(below)} —— 目录名 = 完整生产名（如 "
                f"{_SCENE_NAME_EXAMPLE}），里面的输入影像与 <目录名>_meta.xml 才成套")
    if level == _LEVEL_BELOW:
        return (f"{d}：这是场景目录内部的子目录 —— 请直接粘场景目录本身"
                f"（目录名 = 完整生产名，如 {_SCENE_NAME_EXAMPLE}）")
    return (f"{d}：目录名「{name}」不是完整生产名（生产名含 14 位成像时刻，如 "
            f"{_SCENE_NAME_EXAMPLE}）—— 场景目录是 {'/'.join(_SCENE_LEVELS)} 里"
            f"最深那层，<目录名>_meta.xml 的前缀取的就是它")


def create_app() -> FastAPI:
    root = paths.scenes_root()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # 任务列表而不是单个 poller：漏 cancel 一个，它会在 app 关闭后继续跑
        # （测试里就是往下一个用例的 env 上写）。
        app.state.tasks = [
            asyncio.create_task(_poll_loop(app.state)),
            asyncio.create_task(_tmp_preview_purge_loop()),
            asyncio.create_task(_eager_preview_loop(app.state)),
        ]
        yield
        for t in app.state.tasks:
            t.cancel()
        for t in app.state.tasks:
            try:
                await t
            except asyncio.CancelledError:
                pass

    app = FastAPI(title="sr_agent_platform api", version="0.5.0",
                  lifespan=lifespan)
    # 盘阵直连 IP:端口；前端另配 staticBase/apiBase，CORS 全放（内网）。
    # expose_headers 必须显式给：跨源响应里前端**读不到**自定义头（CORS 规范），
    # `allow_headers` 管的是请求方向，管不到这个。e2e 与前后端分源的部署都是跨源，
    # 不列这里的话 X-SR-Preview-Fallback 恒为 null，那条提示就是死代码。
    app.add_middleware(CORSMiddleware, allow_origins=["*"],
                       allow_methods=["*"], allow_headers=["*"],
                       expose_headers=["X-SR-Preview-Fallback"])

    # 阶段5 运行时状态：create_app 即建（env 已就绪）；lifespan 只额外启轮询。
    app.state.store = store_mod.Store()          # SR_AGENT_DB，懒打开
    app.state.cfg = load_config()
    app.state.chat_locks = {}                    # session_id → asyncio.Lock
    app.state.subscribers = set()                # /api/queue/events 订阅者
    app.state.task_cache = {}                    # task_id → queue display state
    app.state.poll_sec = float(os.environ.get("SR_QUEUE_POLL_SEC", "2"))
    # 场景根快照：`/api/scenes` 一直用的是 create_app 时的这个值，急烤循环在模块级
    # 函数里跑、拿不到闭包，所以放到 state 上让两条路看同一个根。
    app.state.scenes_root = root
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

    def _resolve_bare_tif(src: Path) -> dict:
        """裸 `.tif` 文件路径的 resolve 结果（与目录分支同形的响应）。

        用户可能只想看一张图，并不关心它是不是一个可提交的 SR 场景。所以这里
        **不做场景目录判定**：目录里的输入影像、meta.xml、掩码一概不要求。

        代价是「能不能提交 SR」必须显式区分：`sr_capable` 只在**父目录恰好是合法
        场景目录、且该目录的输入影像就是这一个文件**时才为真。否则用户随手粘一个
        tif 就会拿到一条可提交的 rec，提交后 SR 在盘阵上跑不起来。
        `row.lq_path` 与它同源同真假 —— 场景库入口直接拿的是 row.lq_path。
        """
        dims = _cached_dims(src)
        if not dims:
            raise HTTPException(
                status_code=422,
                detail=f"读不到影像尺寸（{src}）—— 前端开图需要 W/H")
        parent = src.parent
        scene_dir = None
        inp = scene_search.input_scene_path(parent)
        if inp is not None and _same_file(inp, src):
            scene_dir = parent
        row = _manual_row(src, parent, root)
        row["W"], row["H"] = dims["W"], dims["H"]
        if scene_dir is None:
            # 与 sr_capable 同源：没有 scene_dir 就没有可提交的目录。
            row["lq_path"] = None
        mask_path = derived_mask_path(str(scene_dir)) if scene_dir else None
        return {
            "source": "manual",
            "row": row,
            "resolved": {
                "dir": parent.as_posix(),
                "input": src.as_posix(),
                "input_name": src.name,
                "mask_path": mask_path,
                "mask_exists": bool(mask_path) and Path(mask_path).is_file(),
                "writable": os.access(str(parent), os.W_OK),
                "sr_capable": scene_dir is not None,
            },
        }

    @app.post("/api/scenes/resolve")
    async def resolve_scene(request: Request):
        """把用户给的盘阵路径（或「文件名 + 日期」）解析成一条可打开的场景行。

        路径只能来自用户输入：后端**只 stat 用户给的那一个目录，绝不列举**
        （盘阵数据量极大）。猜错必须报错 —— 200 只在真的找到一个合法场景目录
        时才返回，其余一律 4xx，`detail` 里写清试过哪些候选、各自为什么不行。

        请求体二选一：`{path}`（`W:\\GSHC2IMPS\\...` 或 `/DiskArray/...`）或
        `{name}`（+ 可选 `date = YYYY-MM-DD`，不给就由后端从文件名里的成像
        时间戳自己取）。

        `{path}` 还接受**单个 `.tif/.tiff` 文件**（用户只想看一张图，不关心它
        是不是可提交场景）：不做场景目录判定，但 `resolved.sr_capable` /
        `row.lq_path` 只在父目录确实是合法场景目录时才给，免得被误当可提交场景。

        错误码分工：**400** 形态非法或压根反推不出来（相对路径 / `..` / UNC /
        未知盘符 / 日期格式 / 名字里没有时间戳 / 名字不符合生产命名规则 /
        路径指向非 TIFF 文件）· **403** 越白名单 · **404** 反推成立但盘阵上
        没有合法场景目录或没有输入影像 · **422** 场景成立但读不到影像尺寸
        （前端开图要 W/H）。

        请求体：`{path}` 精确路径，或 `{name, date?}` 裸文件名（日期可由后端
        从文件名里取；它是**成像日**，反推会在这一天和次日各找一次 —— 盘阵按
        生产日建目录，深夜成像的景记在第二天）。`{name}` 可再带 `size_bytes`
        （拖拽入口用，见 `_fingerprint_mismatch`）：给了就要求**名字与字节数都对得上** —— 栅格名
        比输入影像的 stem、jpg 名比场景目录名（两份产物，比的对象不同）—— 否则
        这条候选记原因后跳过（最终仍是 404 并列出原因）；不给则只看目录/影像存
        不存在。`{path}` 分支是精确路径，不收这个字段。
        """
        body = await _json_body(request)
        # 拖拽入口的可选双指纹（{name} 分支才用得上，见 _fingerprint_mismatch）：
        # 给了就必须是正整数；不给则一切照旧（粘路径 / 场景库不走这里）。
        size_bytes = body.get("size_bytes")
        if size_bytes is not None and (
                isinstance(size_bytes, bool) or not isinstance(size_bytes, int)
                or size_bytes <= 0):
            raise HTTPException(
                status_code=400,
                detail=f"size_bytes 须为正整数（收到 {size_bytes!r}）")
        name = ""       # 仅 name 分支赋值；path 分支是精确路径，无需指纹
        # 第二阶段的候选（(目录, 指纹名) 两元组）：拖进来的 jpg 是中间产物时才非空，
        # 且**只在第一阶段全落空时**才展开（见下面两处 scan 调用）。在这里先置空
        # 是因为 path 分支不经过构造它的那段代码。
        late: list[tuple[str, str]] = []
        # 404 的补充说明：反推按「成像日 + 次日」找了**两天**时，得让用户知道
        # 这件事，否则他看见两条只差一天的路径只会更懵（见 infer_scene_paths）。
        day_note = ""
        raw_path = body.get("path")
        if isinstance(raw_path, str) and raw_path.strip():
            try:
                posix = to_posix_array_path(raw_path)
            except PathDeniedError as e:
                raise HTTPException(status_code=400,
                                    detail=f"路径形态非法：{e}") from e
            try:
                ensure_allowed(posix)
            except PathDeniedError as e:
                raise HTTPException(
                    status_code=403,
                    detail=f"路径不在允许的盘阵前缀内：{e}") from e
            target = Path(posix)
            if target.is_file():
                # 裸文件形态：只接受 TIFF。别的文件（.jpg 源、meta.xml、掩码…）
                # 一律 400 说清，而不是掉进下面按目录处理的逻辑里报「目录不存在」。
                if target.suffix.lower() not in (".tif", ".tiff"):
                    raise HTTPException(
                        status_code=400,
                        detail=f"路径指向文件 {target.name}，但它不是 .tif/.tiff"
                               " —— 请粘场景目录，或粘单个 .tif 文件路径")
                return _resolve_bare_tif(target)
            tried = [(posix, "")]
        else:
            name, date = body.get("name") or "", body.get("date")
            if not isinstance(name, str) or not name.strip():
                raise HTTPException(
                    status_code=400,
                    detail="需给 path，或给 name（可选 date = YYYY-MM-DD）")
            if not isinstance(date, str) or not date.strip():
                # 日期可由后端自己从文件名取 —— 前端不必再实现一套同样的正则
                date = parse_scene_date(name) or ""
            if not date:
                # 拖进来的是 jpg：反推的唯一依据就是文件名，而这份名字里没有生产
                # 全名（含 14 位成像时刻）—— 也就无从知道该去 `<年>/<月>/<日>` 哪
                # 一天找。平台**不猜目录**（猜错就是拿着另一景的 lq_path 提交），
                # 所以这里如实说清「认不出来 + 该怎么办」。
                if Path(name).suffix.lower() in (".jpg", ".jpeg"):
                    raise HTTPException(
                        status_code=400,
                        detail=f"这张 jpg 的名字里没有生产全名（缺 14 位成像时刻）："
                               f"{name} —— 平台不猜目录。能关联的 jpg 只有名字与场景"
                               f"目录名一致的那份（<目录名>.jpg），以及它的中间产物"
                               f"（SC 场景叫 <目录名>_<suffix>.jpg；RC 场景的产物叫"
                               f" PAN_<suffix>.jpg，那个名字里同样没有成像时刻，"
                               f"认不出是哪一景）。两份都要求同级有同名栅格；"
                               f"改过名、另存过、或叫 PAN.jpg 这类都不认。"
                               f"请改拖那几份，或把该场景目录粘进「盘阵场景」栏打开")
                raise HTTPException(
                    status_code=400,
                    detail=f"反推路径失败：文件名里没有 14/8 位成像时间戳"
                           f"（{name}）—— 请把该场景目录粘进「盘阵场景」栏打开")
            try:
                tried = [(c, name) for c in infer_scene_paths(name, date)]
            except PathDeniedError as e:
                raise HTTPException(status_code=400,
                                    detail=f"反推路径失败：{e}") from e
            if not tried:
                raise HTTPException(
                    status_code=400,
                    detail=f"反推路径失败：{name} 不符合生产命名规则"
                           "（缺段号/景号段，拆不出卫星型号与段级目录）—— "
                           "请把场景目录粘进「盘阵场景」栏打开")
            if len(tried) > 1:
                day_note = ("（成像日与次日都找过 —— 盘阵按生产日建目录，"
                            "深夜成像的景常记在次日）")
            # 第二阶段的候选（**只在第一阶段全落空时才展开**，见下面 scan 的两处调用）：
            # 拖进来的 jpg 若是中间产物（`<目录名>_sr.jpg` / `<目录名>_sr_NOSR.jpg`），
            # 多出来的尾段会让反推出的目录名带上尾巴（`…/<目录名>_sr`）—— 盘阵上
            # 根本没有那个目录，真正该去的是**去掉尾段**的那条。尾段两种形态各给一条
            # 候选：去掉末尾一段、以及先去 `_NOSR` 再去末尾一段（`sr_2` 这类带下划线的
            # suffix 也能整段切掉 —— 按段数猜会切错）。
            #
            # 名字仍由 pathguard 同一套模板渲染（日期 / 卫星型号 / 段级目录照旧反推），
            # 前端与这里都不自拼路径；尾巴先过 `jpg_stage_name` 那套字符约束，切不出
            # 干净尾段（` - 副本`、`.preview` 那种）就一条候选都不生成。
            #
            # 为什么分两阶段而不是一开始就一起试：第一阶段（今天的全部行为）命中的
            # 常见情形**一个 stat 都不多花** —— 钉住探测量上限的那几条用例正走在上面。
            if Path(name).suffix.lower() in (".jpg", ".jpeg"):
                for stem in scene_search.de_suffixed_stems(name):
                    for cand in infer_scene_paths(stem + Path(name).suffix, date):
                        if all(cand != c for c, _ in tried) and \
                                all(cand != c for c, _ in late):
                            late.append((cand, stem + Path(name).suffix))
            for cand, _ in tried:
                try:
                    ensure_allowed(cand)
                except PathDeniedError as e:
                    raise HTTPException(
                        status_code=403,
                        detail=f"路径不在允许的盘阵前缀内：{e}") from e

        reasons: list[str] = []

        # 命中四元组：场景目录 / 本体输入影像 / 环节 / 该环节**自己的栅格**。
        # 环节只对拖进来的 jpg 有第三种取值；.tif 反推与粘路径恒为 'input'。
        hit: tuple[Path, Path, str, str, Path] | None = None

        def scan(cands: list[tuple[str, str]]
                 ) -> tuple[Path, Path, str, str, Path] | None:
            """按顺序挑第一条成立的候选；每条不成立的原因都记进 `reasons`。

            候选是 `(目录, 指纹用的名字)` 两元组。名字那一半是给
            `_fingerprint_mismatch` 用的：**它按后缀分流**，去尾变体必须连原后缀
            一起给（`<目录名>.jpg`），给裸 stem 会掉进「栅格」那一支去比字节数，
            于是每条候选都被判「字节数不符」—— 明明名字都对上了。
            """
            for cand, fp_name in cands:
                try:
                    ensure_allowed(cand)
                except PathDeniedError as e:
                    raise HTTPException(
                        status_code=403,
                        detail=f"路径不在允许的盘阵前缀内：{e}") from e
                d = Path(cand)
                if not d.is_dir():
                    reasons.append(f"{d}：目录不存在")
                    continue
                inp = scene_search.input_scene_path(d)
                if inp is None:
                    if not (d / (d.name + "_meta.xml")).is_file():
                        reasons.append(_why_not_scene_dir(d))
                    else:
                        names = "、".join(c.name
                                          for c in scene_search.input_candidates(d))
                        reasons.append(f"{d}：目录里没有输入影像（找过 {names}）")
                    continue
                if size_bytes is not None and fp_name:
                    why = _fingerprint_mismatch(inp, fp_name, size_bytes)
                    if why:
                        # 指纹不认就当作**这条候选**不合格，接着试下一条（次日那条），
                        # 不要立刻 404 —— 报错口径仍由下面统一出。
                        reasons.append(why)
                        continue
                kind, suffix, raster = "input", "", inp
                if fp_name and Path(fp_name).suffix.lower() in (".jpg", ".jpeg"):
                    # 拖 jpg：除了目录还得认**这份 jpg 是哪个环节**。目录名对不上
                    # 环节名（`_cloud` / `.preview` / `- 副本` 那些），或该环节自己
                    # 的栅格不在同级 → 这条候选不合格（见 scene_search.stage_of_jpg）。
                    # 判据喂的是**用户拖进来那个名字**（`name`，带着尾段），不是
                    # `fp_name`：后者是反推目录用的去尾名（见上面 late 的构造），
                    # 拿它去判环节，任何产物都会被认成本体的显示件。
                    stage = scene_search.stage_of_jpg(d, inp, Path(name).stem)
                    if stage is None:
                        reasons.append(
                            f"{d}：{Path(name).name} 这个名字不是本景的输入件或"
                            f"中间产物（输入件叫 {d.name}.jpg，SC 场景的中间产物"
                            f"叫 <目录名>_<suffix>.jpg / <目录名>_<suffix>_NOSR.jpg，"
                            f"且同级要有同名栅格）")
                        continue
                    kind, suffix, raster = stage
                return d, inp, kind, suffix, raster
            return None

        hit = scan(tried)
        if hit is None and late:
            hit = scan(late)
        if hit is None:
            raise HTTPException(status_code=404,
                                detail="没找到合法场景目录 —— "
                                       + "；".join(reasons) + day_note)
        d, inp, kind, suffix, raster = hit
        dims = _cached_dims(raster)
        if not dims:
            raise HTTPException(
                status_code=422,
                detail=f"场景成立但读不到影像尺寸（{raster}）—— 前端开图需要 W/H")
        # 这一行描述的是**这一环节自己的栅格**（本体 / 产物 / 上一次产物）：产物的
        # W/H 是本体的倍数，拿本体的尺寸建画布，整张图的比例都是错的。
        row = _manual_row(raster, d, root)
        row["W"], row["H"] = dims["W"], dims["H"]
        if kind != "input":
            # **中间产物不可修复**，而这条服务的形状是「能不能提交 / 掩码写哪」，
            # 所以在这里就把口径定死，不指望前端记得住：
            # `lq_path` 置空 → 前端那颗「提交 SR」与「保存掩码到盘阵」都拿不到落点
            # （`bakeMaskToServer` 是拿 rec.lqPath + rec.W/H 去 POST /api/masks 的，
            # 产物尺寸的掩码配本体的 lq_path，后端会把一张产物尺寸的掩码静默写到
            # 本体的掩码文件上）。前端另有一道 stageKind 门，这里是第一道。
            row["lq_path"] = None
        # 拖 jpg 进来时这一行描述的是**栅格**（`_manual_row(raster, ...)`，上面的
        # id/W/H/hasPreview 全都指向那份 tif/jpg 同名的栅格），所以 `_manual_row` 里
        # 那句按栅格算的 rasterPreview 必然是 None（它只对 jpg 源非空）。
        #
        # 但「要不要改从栅格烤」这个比较恰恰是这条入口最需要的：用户拖进来的
        # `<目录名>.jpg` 就是盘阵那份 8192 显示件，而更清晰的底图是同一目录的栅格。
        # 比较的对象只能是**盘阵那张 jpg**（`d / name`）的尺寸，不能拿用户本地那份
        # 来量 —— 指纹对 jpg 只比名字，本地那份可能另存过、缩过（见
        # `_fingerprint_mismatch`），平台口径是「盘阵上的才是基准」。
        #
        # 盘阵上没有这份 jpg（删了/改名了，只有本地副本）时 `_cached_dims` 读不出
        # → None → 前端老实显示本地那份。保守方向是对的。
        if Path(name).suffix.lower() in (".jpg", ".jpeg"):
            row["rasterPreview"] = _raster_preview(d / Path(name).name, root)
        # 中间产物没有「可提交的场景」这一说（上面把 row.lq_path 置了空），掩码路径
        # 也就无从推起 —— 推出来的那份是本体的掩码，与这张产物毫无关系。
        mask_path = derived_mask_path(str(d)) if kind == "input" else None
        return {
            "source": "manual",
            "row": row,
            "resolved": {
                # 盘阵 POSIX 形态（与 mask_path / 提交侧归一化同一口径）：
                # dir 会被原样带进队列表单的 lq_path，宿主形态在开发机上与
                # 归一化结果对不上。Linux 上 as_posix() 与 str() 同值。
                "dir": d.as_posix(),
                # 本体的输入影像：**恒指本体**（`inp`），即便这次拖进来的是产物。
                # 它的语义是「SR 跑的是哪份文件」，不是「这一行描述哪张图」——
                # 后者看 row（row 指向环节自己的栅格）。
                "input": inp.as_posix(),
                "input_name": inp.name,
                "mask_path": mask_path,
                "mask_exists": bool(mask_path) and Path(mask_path).is_file(),
                # 服务账号对场景目录的写权限：meta.xml 回写、Debug/ 日志、掩码、
                # 预览缓存四处都要写。提前告知，好过提交后 422。
                "writable": os.access(str(d), os.W_OK),
                # 目录分支走到这里就已经确认是合法场景目录（有 meta.xml 且有
                # 输入影像），所以恒为 True。裸 .tif 分支才可能是 False。
                # **例外**：拖进来的中间产物（kind != 'input'）恒为 False —— 那一类
                # 不是可修复对象，前端据此不给提交/写掩码的入口。
                "sr_capable": kind == "input",
                # 这一次拖进来的影像是场景里的哪个环节（2026-09-21 起中间产物也能
                # 关联）。三种取值，只有 `input` 是可修复对象 —— 掩码与 SR 都建在
                # 本体影像的网格上，产物的尺寸是它的倍数（见 lib/stage.ts）。
                "kind": kind,
                # 中间产物的 suffix：从**文件名本身**切出来的那一段（不查任务库也
                # 不查配置 —— SR 常在平台外跑，库里没记录照样得认得出）。本体为空串。
                "suffix": suffix,
            },
        }

    @app.get("/api/scenes/{scene_id}/preview")
    def preview(scene_id: str, div: int = Query(2)):
        """场景预览 JPG（响应体即字节）。`?div=` 选下采样档位（各边 ÷div）。

        落点：源同目录（或 `SR_PREVIEWS_ROOT` 镜像树）的 `<stem>.preview.jpg`，
        **与档位无关** —— 换档位是原地覆盖同一份，靠戳里的 div 判废重烤。
        """
        div = _check_div(div)
        try:
            abs_path = paths.scene_id_to_abs(scene_id, root)
        except PathDeniedError as e:
            raise HTTPException(status_code=404,
                                detail=f"场景不可访问：{e}") from e
        # 源是显示件 jpg、但同目录配着同名栅格：改从**栅格**烤。落点不需要变 ——
        # `with_suffix(".preview.jpg")` 对 `PAN.jpg` 与 `PAN.tif` 是同一个文件名，
        # 也就是栅格行用的那一份，所以「前端调 jpg 行的 id」与「栅格行自己打开」
        # 命中同一份缓存，不会烤两次。换不换由后端在这一层决定，前端不必知道。
        # 前端只在「栅格烤出来更清晰」时才走到这里（见 api-contract「显示源比较规则」）。
        raster = scene_search.sibling_raster_path(abs_path)
        if raster is not None:
            abs_path = raster
        if abs_path.suffix.lower() in (".jpg", ".jpeg"):
            # 源就是显示就绪图（§4.7）：直接回源文件，不必（也无法）烘焙预览。
            # 正常路径下前端拿 hasPreview/jpgUrl 走静态 URL，不会打到这里，
            # 这是契约兜底：/preview 对任何场景行都返回可显示的 JPEG。
            return FileResponse(str(abs_path), media_type="image/jpeg")
        jpg = paths.preview_jpg_for(abs_path, root)
        try:
            ensure_preview_jpg(str(abs_path), str(jpg), div=div)
        except PreviewError as e:
            raise HTTPException(status_code=422,
                                detail=f"预览生成失败：{e}") from e
        return FileResponse(str(jpg), media_type="image/jpeg")

    @app.get("/api/scenes/{scene_id}/preview-drop")
    def preview_drop(scene_id: str, div: int = Query(2)):
        """拖入链的预览图：**写回源所在的盘阵场景目录**，`<stem>_preview.jpg`。

        与 `/preview` 的有意差别是落点：那份是「平台自己的缓存」（源同目录或
        `SR_PREVIEWS_ROOT`），这份落进生产场景目录、跟着场景数据长期活 ——
        拖入的场景就该烤一次长期可用，而不是每天第一次拖入重烤一遍。

        兜底：场景目录不可写（服务账号没有写权限）时退回
        `SR_TEMP_PREVIEWS_ROOT/<今天>/`，并回 `X-SR-Preview-Fallback: tmp`
        让界面如实说明「这次没落盘阵」。两条都失败才 422。

        `Cache-Control: no-store`：URL 按 scene id 稳定、内容随档位变，不加这句
        浏览器会按启发式缓存端上旧档位的字节。
        """
        div = _check_div(div)
        headers = {"Cache-Control": "no-store"}
        try:
            abs_path = paths.scene_id_to_abs(scene_id, root)
        except PathDeniedError as e:
            raise HTTPException(status_code=404,
                                detail=f"场景不可访问：{e}") from e
        # 同 /preview：显示件 jpg 若配着同名栅格，改从栅格烤。拖入链的落点是
        # `<stem>_preview.jpg`，对 jpg 与 tif 也是同一个文件名。
        raster = scene_search.sibling_raster_path(abs_path)
        if raster is not None:
            abs_path = raster
        if abs_path.suffix.lower() in (".jpg", ".jpeg"):
            # 源本身就是显示就绪图：回源文件即可（build_preview_pixels 只认
            # TIFF，不给这行短路会抛「仅支持 TIFF 生成预览」）。
            return FileResponse(str(abs_path), media_type="image/jpeg",
                                headers=headers)
        main_jpg = paths.drop_preview_path(abs_path)
        # 主落点与兜底包在**同一个 try** 里：兜底自己也要 mkdir + 原子写，临时根
        # 同样可能不可写（配到只读盘、磁盘满），漏掉就是 500 逃出请求。
        try:
            if not os.access(str(main_jpg.parent), os.W_OK):
                raise PreviewError(f"场景目录不可写：{main_jpg.parent}")
            ensure_preview_jpg(str(abs_path), str(main_jpg), div=div)
        except (PreviewError, OSError) as e:
            try:
                tmp_jpg = preview_cache.tmp_preview_path(abs_path)
                ensure_preview_jpg(str(abs_path), str(tmp_jpg), div=div)
            except (PreviewError, OSError) as e2:
                raise HTTPException(
                    status_code=422,
                    detail=f"预览生成失败：场景目录不可写（{e}）；"
                           f"临时缓存也不可用（{e2}）") from e2
            headers["X-SR-Preview-Fallback"] = "tmp"
            return FileResponse(str(tmp_jpg), media_type="image/jpeg",
                                headers=headers)
        return FileResponse(str(main_jpg), media_type="image/jpeg",
                            headers=headers)

    @app.get("/api/scenes/{scene_id}/siblings")
    def scene_siblings(scene_id: str, request: Request,
                       suffix: str | None = Query(default=None)):
        """这个场景的三类图：输入影像 / 本次产物 / 上一次产物。

        **纯只读**：固定候选名的 `is_file()` + 尺寸探测 + 读 JPEG 注释里的档位。
        永不烘焙、永不写盘、永不列举目录 —— 它回答的是「三份各叫什么、在不在、
        各自的场景 id 是什么」，**找不到也是回答**（`productCandidates` 说明试过
        哪些名字，`exists: false` 说明结果）。

        每一类都直接拿它自己的 id 调 `GET /api/scenes/{id}/preview?div=N` 就能看图
        （三类各有自己的 `<stem>.preview.jpg` 落点，天然不撞名），所以这个端点
        **不新增任何烘焙入口**。

        `suffix` 取值顺序：`?suffix=`（用户断言）→ 该 `lq_path` 最近一条 COMPLETED
        任务的 `params.suffix`（权威：跑的就是它）→ `run_sr.default_suffix()`
        （配置缺省）。**`suffixFrom` 如实回报用到的是哪一个** —— 「按配置猜的名字」
        与「真跑过的名字」看起来一样，不标出来就分不清。没有可用的 suffix 时
        product/nosr 两类**不出现**（拼不出名字就不编）。

        LAST-PRODUCT 那一类的名字是 `SR_code/util.py::writeTiff` 的改名规则推出来的
        （它改的是**输出路径**，且只在同一 suffix 跑过两次以上时才存在）——真机
        尚未实证，所以这里把拼出来的候选名一并回报，好让它能被核对。
        """
        try:
            abs_path = paths.scene_id_to_abs(scene_id, root)
        except PathDeniedError as e:
            raise HTTPException(status_code=404,
                                detail=f"场景不可访问：{e}") from e
        scene_dir = abs_path.parent
        suffix_from: str | None = None
        if suffix:
            if not run_sr_svc.SUFFIX_RE.match(suffix):
                raise HTTPException(
                    status_code=400,
                    detail=f"suffix 非法：{suffix!r}（{run_sr_svc.SUFFIX_RULE}）")
            suffix_from = "query"
        else:
            suffix = _latest_completed_suffix(request.app.state.store,
                                              scene_dir.as_posix())
            suffix_from = "task" if suffix else None
            if not suffix:
                suffix = run_sr_svc.default_suffix()
                suffix_from = "default" if suffix else None

        def item(kind: str, p: Path | None) -> dict:
            """一类图。`p` 为 None = 这一类拼不出名字（没有可用的 suffix）。"""
            row = {"kind": kind, "id": None, "name": None, "rel": None,
                   "exists": False, "sizeBytes": None, "mtime": None,
                   "W": None, "H": None,
                   "hasPreview": False, "previewDiv": None, "jpgUrl": None}
            if p is None:
                return row
            row["name"] = p.name
            if root is not None and is_within(p, root):
                row["rel"] = paths.rel_of_scene(p, root)
                row["id"] = paths.scene_id(row["rel"])
            else:
                row["id"] = paths.scene_id_abs(p)
            try:
                st = p.stat()
            except OSError:
                st = None
            row["exists"] = st is not None
            if st is not None:
                row["sizeBytes"] = st.st_size
                row["mtime"] = st.st_mtime
                dims = _cached_dims(p)
                if dims:
                    row["W"], row["H"] = dims["W"], dims["H"]
            jpg = paths.preview_jpg_for(p, root)
            row["hasPreview"] = jpg.is_file()
            row["previewDiv"] = _cached_preview_div(jpg)
            if root is not None and is_within(jpg, root):
                row["jpgUrl"] = paths.rel_url(jpg, root)
            return row

        inp = scene_search.input_scene_path(scene_dir)
        items = [item("input", inp)]
        cand_names: list[str] = []
        if inp is not None and suffix:
            cands = scene_search.product_candidates(inp, suffix)
            cand_names = [c.name for c in cands]
            # 拼完的名字再过一道白名单：SUFFIX_RE 挡得住分隔符，挡不住「全是合法
            # 字符、却拼到白名单外」的想象。suffix 会进文件名，两道是纪律。
            for c in cands:
                try:
                    ensure_allowed(c.as_posix())
                except PathDeniedError as e:
                    raise HTTPException(
                        status_code=403,
                        detail=f"产物路径不在允许的盘阵前缀内：{e}") from e
            # 没有一个存在时回报第一个候选（`.tif`）并置 exists: false —— 报错口径
            # 由 productCandidates + exists 一起给出，不在这里编一个"差不多"的路径。
            product = next((c for c in cands if c.is_file()), cands[0])
            items.append(item("product", product))
            items.append(item("nosr", scene_search.nosr_path_for(product)))
        return {
            "sceneId": scene_id,
            "lqPath": scene_dir.as_posix(),
            "suffix": suffix, "suffixFrom": suffix_from,
            # 服务端急烤用的档位，仅供界面标注「服务端已烤成 ÷N」——
            # **不参与任何前端决策**（前端的档位是用户滑块那个）。
            "div": _product_preview_div(),
            "items": items,
            "productCandidates": cand_names,
        }

    return app
