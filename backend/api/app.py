"""FastAPI app — 阶段4 场景检索/预览 + 阶段5 平台 API（chat/queue/masks/tools）。

启动：`uvicorn backend.api.app:create_app --factory`（env 式 config）。
测试用 `from backend.api.app import create_app` 后自行注入 env 再构造。

Env
---
SR_SCENES_ROOT    盘阵场景根（unset → fake 回退）
SR_PREVIEWS_ROOT  可选预览缓存根（须在 scenes 根内，长期缓存）
SR_TEMP_PREVIEWS_ROOT 拖拽入口的**临时**预览缓存根（1 天 TTL，每日 0 点清）；
                  默认系统临时目录，生产须显式配到真实磁盘上
                  （见 services/preview_cache.py 的约定）
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
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from backend.api import paths
from backend.api.paths import PathDeniedError
from backend.api.platform import (
    _json_body, _task_state, derived_mask_path, router as platform_router)
from backend.config import load_config
from backend.pathguard import (
    ensure_allowed, flat_scene_layout, infer_scene_paths, is_within,
    looks_like_scene_name, parse_scene_date, production_tree_depth,
    strip_raster_ext, to_posix_array_path)
from backend.services import preview_cache
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
    # lq_path 一律盘阵 POSIX 形态：它会与 /api/queue 行 params.lq_path（提交侧
    # 归一化的 POSIX 值）逐字比对，宿主形态在 Windows 开发机上永远比不中。
    # Linux 上 as_posix() 与 str() 同值，生产行为不变。
    row["lq_path"] = abs_path.parent.as_posix()
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
        # 同 _scene_row：lq_path 一律 POSIX（它会被原样带进提交表单）。
        "lq_path": dir_path.as_posix(),
        "manual": True,
    }
    # hasPreview 一律填真值（缓存到底在不在），与是否在库内无关：库外没有静态
    # URL，但前端要靠它判断「这次会不会触发首次烘焙」并提示用户等待，所以不能
    # 因为「库外用不上」就一律留 False。
    jpg = paths.preview_jpg_for(abs_path, root)
    row["hasPreview"] = jpg.is_file()
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
    """拖拽入口的双指纹：**文件名 + 字节数**都吻合才算同一个文件。
    返回人话原因（并入 404 的候选清单），None = 吻合。

    为什么名字这一半不能省：`scene_search.input_candidates` 的次序是
    `<目录名>.{tif,tiff,img}` 在前、`PAN.{tif,tiff,img}` 在后，
    `input_scene_path` 返回第一个存在的。RC 场景的输入影像是 `PAN.tif`，但同
    目录里往往还躺着 `<目录名>.tif`（上游 SC 步骤的产物）。用户拖的若是后者，
    只比字节数就可能通过 —— 而 SR 在盘阵上跑的是 `PAN.tif`，用户画的掩码坐标
    会整片落在另一张图上。名字对不上一律不认，宁可退回浏览器本地解码。
    """
    want = strip_raster_ext(name)
    if inp.stem != want:
        return (f"{inp.parent}：目录里的输入影像是 {inp.name}，与拖入的 {want} "
                "不是同一个文件（SR 在这个目录上跑的是前者）")
    # 拖进来的是 JPG：名字对上了就算同一个场景。字节数那一半对 JPEG 无意义 ——
    # 盘阵上那份 <目录名>.jpg 是**另一份产物**（8bit 显示就绪的预览，见
    # scene_search._IMAGE_EXTS），与输入的 TIF 不可能同字节，比下去只会恒不通过，
    # 白白把「拖 jpg 进查看器」这条入口堵死。
    # 只对 jpg 放行、不泛化成「后缀不同就放行」：那会连 SC.tiff 与 SC.tif 也放过，
    # 而拖错后缀（本机另存过一份 TIFF）恰恰是这一层要挡的。
    if Path(name).suffix.lower() in (".jpg", ".jpeg"):
        return None
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
        从文件名里取）。`{name}` 可再带 `size_bytes`（拖拽入口用，见
        `_fingerprint_mismatch`）：给了就要求候选目录里的输入影像**同名且字节
        数一致**，否则这条候选记原因后跳过（最终仍是 404 并列出原因）；不给
        则只看目录/影像存不存在。`{path}` 分支是精确路径，不收这个字段。
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
            tried = [posix]
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
                raise HTTPException(
                    status_code=400,
                    detail=f"反推路径失败：文件名里没有 14/8 位成像时间戳"
                           f"（{name}）—— 请把该场景目录粘进「盘阵场景」栏打开")
            try:
                tried = infer_scene_paths(name, date)
            except PathDeniedError as e:
                raise HTTPException(status_code=400,
                                    detail=f"反推路径失败：{e}") from e
            if not tried:
                raise HTTPException(
                    status_code=400,
                    detail=f"反推路径失败：{name} 不符合生产命名规则"
                           "（缺段号/景号段，拆不出卫星型号与段级目录）—— "
                           "请把该场景目录粘进「盘阵场景」栏打开")
            for cand in tried:
                try:
                    ensure_allowed(cand)
                except PathDeniedError as e:
                    raise HTTPException(
                        status_code=403,
                        detail=f"路径不在允许的盘阵前缀内：{e}") from e

        reasons: list[str] = []
        hit: tuple[Path, Path] | None = None
        for cand in tried:
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
            if size_bytes is not None and name:
                why = _fingerprint_mismatch(inp, name, size_bytes)
                if why:
                    # 指纹不认就当作**这条候选**不合格，接着试下一条（旧扁平
                    # 模板），不要立刻 404 —— 报错口径仍由下面统一出。
                    reasons.append(why)
                    continue
            hit = (d, inp)
            break
        if hit is None:
            raise HTTPException(status_code=404,
                                detail="没找到合法场景目录 —— "
                                       + "；".join(reasons))
        d, inp = hit
        dims = _cached_dims(inp)
        if not dims:
            raise HTTPException(
                status_code=422,
                detail=f"场景成立但读不到影像尺寸（{inp}）—— 前端开图需要 W/H")
        row = _manual_row(inp, d, root)
        row["W"], row["H"] = dims["W"], dims["H"]
        mask_path = derived_mask_path(str(d))
        return {
            "source": "manual",
            "row": row,
            "resolved": {
                # 盘阵 POSIX 形态（与 mask_path / 提交侧归一化同一口径）：
                # dir 会被原样带进队列表单的 lq_path，宿主形态在开发机上与
                # 归一化结果对不上。Linux 上 as_posix() 与 str() 同值。
                "dir": d.as_posix(),
                "input": inp.as_posix(),
                "input_name": inp.name,
                "mask_path": mask_path,
                "mask_exists": Path(mask_path).is_file(),
                # 服务账号对场景目录的写权限：meta.xml 回写、Debug/ 日志、掩码、
                # 预览缓存四处都要写。提前告知，好过提交后 422。
                "writable": os.access(str(d), os.W_OK),
                # 目录分支走到这里就已经确认是合法场景目录（有 meta.xml 且有
                # 输入影像），所以恒为 True。裸 .tif 分支才可能是 False。
                "sr_capable": True,
            },
        }

    @app.get("/api/scenes/{scene_id}/preview")
    def preview(scene_id: str):
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
        jpg = paths.preview_jpg_for(abs_path, root)
        try:
            ensure_preview_jpg(str(abs_path), str(jpg))
        except PreviewError as e:
            raise HTTPException(status_code=422,
                                detail=f"预览生成失败：{e}") from e
        return FileResponse(str(jpg), media_type="image/jpeg")

    @app.get("/api/scenes/{scene_id}/preview-tmp")
    def preview_tmp(scene_id: str):
        """拖拽入口专用的**临时**预览图（默认 1 天 TTL，见 services/preview_cache）。

        与 `/preview` 只差落点：那份长期缓存写源同目录或 `SR_PREVIEWS_ROOT` 的
        镜像树，跟着场景数据活；这份写 `SR_TEMP_PREVIEWS_ROOT/<今天>/<hash>.jpg`，
        第二天 0 点整桶删掉。**烘焙规则完全相同**，所以同一场景两条链出来的
        字节是一样的，观感一致。

        为什么不复用 `/preview` 加个 query 参数：`/preview` 的语义（含
        `hasPreview`/`jpgUrl` 静态 URL）绑死在「生产 `<stem>.preview.jpg` 在不
        在」上，拖拽的源多半落在库外、不该往生产数据目录撒文件。分成两个端点，
        前端也就不会把临时字节写进库行的状态里。

        `Cache-Control: no-store`：URL 按 scene id 稳定、内容跨天会变，不加这句
        浏览器会按启发式缓存把隔夜的字节端上来（桶都删了还能看见旧的）。
        """
        headers = {"Cache-Control": "no-store"}
        try:
            abs_path = paths.scene_id_to_abs(scene_id, root)
        except PathDeniedError as e:
            raise HTTPException(status_code=404,
                                detail=f"场景不可访问：{e}") from e
        if abs_path.suffix.lower() in (".jpg", ".jpeg"):
            # 源本身就是显示就绪图：回源文件即可（build_preview_pixels 只认
            # TIFF，不给这行短路会抛「仅支持 TIFF 生成预览」）。
            return FileResponse(str(abs_path), media_type="image/jpeg",
                                headers=headers)
        jpg = preview_cache.tmp_preview_path(abs_path)
        try:
            ensure_preview_jpg(str(abs_path), str(jpg))
        except PreviewError as e:
            raise HTTPException(status_code=422,
                                detail=f"预览生成失败：{e}") from e
        return FileResponse(str(jpg), media_type="image/jpeg",
                            headers=headers)

    return app
