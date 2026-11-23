"""Agent tools for the unified Repair Application Service.

``prepare_repair`` and ``start_repair`` are two mutually exclusive steps:
prepare validates the current workspace and returns a plan (no SR), while start
only launches a plan that a **real user** has already confirmed server-side.
Neither tool accepts free-form filesystem paths or an ``approved`` flag.

The read-only tools (``get_repair_workspace`` / ``list_repair_items`` /
``get_repair_item``) reuse the same Goal/Item rows the tool panel displays.
"""
from __future__ import annotations

from backend.services import repair_application as svc
from backend.services import repair_workspace as workspace_tools
from backend.services import store as store_mod
from backend.services.goal_lease import current_tool, LeaseLost
from .contract import Tool, err, ok, tool

BOUND_NAMES = {
    "prepare_repair", "start_repair", "get_repair_workspace",
    "list_repair_items", "get_repair_item",
}


def _unbound_store():
    execution = current_tool.get() or {}
    if execution.get("store") is not None:
        return execution["store"], False
    return store_mod.default_store(), True


class _Context:
    def __init__(self, *, store=None, session_id=None, workspace=None,
                 current=None, selected_images=None, batch_draft=None, selected_truncated=False,
                 preferences=None, raster_runner=None):
        self.store = store
        self.session_id = session_id
        self.workspace = workspace
        self.current = current
        self.selected_images = selected_images
        self.batch_draft = batch_draft
        self.selected_truncated = selected_truncated
        self.preferences = preferences or {}
        self.raster_runner = raster_runner

    def close(self):
        pass


def _workspace_ids(workspace):
    ids = []
    for item in (workspace or {}).get("items") or []:
        if isinstance(item, dict) and isinstance(item.get("scene_id"), str):
            ids.append(item["scene_id"])
    return ids


def _draft_sources(draft):
    rows = []
    for row in (draft or {}).get("candidates") or []:
        if not isinstance(row, dict):
            continue
        if row.get("status") == "已排除":
            continue
        path = row.get("path")
        if not isinstance(path, str) or not path.strip():
            continue
        rows.append({
            "path": path.strip(),
            "name": str(row.get("name") or "").strip() or "草稿影像",
            "evidence": str(row.get("evidence") or "当前修复草稿；待检测"),
        })
    return rows


def _prepare(ctx, scope, mode, strategy, detection_method, mask_policy, title,
             source='agent'):
    owned = False
    store = ctx.store
    if store is None:
        store, owned = _unbound_store()
    try:
        if ctx.preferences.get('inspection_only'):
            return err('本条消息为仅质检模式，请使用 inspect_repair_workspace；不能准备或启动 SR')
        if scope == 'selection' and ctx.selected_truncated:
            return err('多选影像快照被截断，请缩小选择范围后重新准备，不能静默只规划前一部分')
        if (scope != "draft" and ctx.workspace is None and ctx.current is None
                and not ctx.selected_images):
            return err(
                "这条消息没有绑定 Viewer 工作区；请先打开本地、上传或服务器影像，"
                "不要用场景库检索代替当前工作区。")
        kwargs = {}
        if ctx.preferences:
            detection_method=ctx.preferences['detection_method']
            kwargs.update(tone_weight=ctx.preferences['tone_weight'],skip_completed=ctx.preferences['skip_completed'])
        if mode != 'production':
            return err('Agent 协助与自动修复统一使用真实修复（production），不支持新建演示计划。')
        if scope == "draft":
            sources = _draft_sources(ctx.batch_draft)
            if not sources:
                return err("当前修复草稿为空；请先在 Viewer 中加入待修复影像")
            kwargs["sources"] = sources
            scope = "workspace"
        plan = svc.prepare_repair(
            store, session_id=ctx.session_id, workspace=ctx.workspace,
            current=ctx.current, selected_images=ctx.selected_images,
            scope=scope, mode=mode, strategy=strategy,
            detection_method=detection_method, mask_policy=mask_policy,
            title=title, created_by=source, raster_runner=ctx.raster_runner, **kwargs)
        return ok(plan)
    except (svc.RepairError, ValueError) as exc:
        return err(str(exc))
    finally:
        if owned:
            try:
                store.close()
            except Exception:  # noqa: BLE001
                pass


def _start(ctx, plan_id, plan_version):
    if ctx.preferences.get('inspection_only'):
        return err('本条消息为仅质检模式，禁止启动 SR')
    owned = False
    store = ctx.store
    if store is None:
        store, owned = _unbound_store()
    try:
        existing = store.get_repair_plan(plan_id)
        if (isinstance(existing, dict) and existing.get('session_id') in (None, ctx.session_id)
                and existing.get('mode') != 'production'):
            return err('历史演示计划不能用于真实自动修复，请重新准备 production 计划并确认。')
        result = svc.start_repair(
            store, plan_id, session_id=ctx.session_id,
            workspace=ctx.workspace,
            workspace_version=(ctx.workspace or {}).get("version"),
            plan_version=plan_version)
        goal = result["goal"]
        return ok({
            "kind": "repair_start_receipt.v1",
            "plan_id": result["plan"]["plan_id"],
            "plan_status": result["plan"]["status"],
            "goal_id": goal["goal_id"],
            "title": goal.get("title"),
            "status": goal.get("status"),
            "reused": bool(result.get("reused")),
            "mode": result["plan"].get("mode"),
            "targets": result["plan"].get("targets", {}).get("applicable"),
            "message": "修复任务已创建并交给后台批次运行时，聊天断线不会停止它。",
        })
    except KeyError:
        return err("repair plan not found")
    except (svc.RepairError, ValueError) as exc:
        reason = getattr(exc, "reason", None) or getattr(exc, "code", None)
        result = err(str(exc))
        result["error_class"] = reason or "repair_error"
        return result
    finally:
        if owned:
            try:
                store.close()
            except Exception:  # noqa: BLE001
                pass


def _workspace_view(ctx):
    owned = False
    store = ctx.store
    if store is None:
        store, owned = _unbound_store()
    try:
        items = []
        for item in (ctx.workspace or {}).get("items") or []:
            if not isinstance(item, dict):
                continue
            items.append({"scene_id": item.get("scene_id"),
                          "name": item.get("name")})
        selected = [img for img in (ctx.selected_images or [])
                    if isinstance(img, dict)]
        goals = []
        plans = []
        if store is not None and ctx.session_id is not None:
            try:
                plans = [{
                    "plan_id": row["plan_id"], "status": row.get("status"),
                    "scope": row.get("scope"), "mode": row.get("mode"),
                    "targets": {
                        "total": (row.get("targets") or {}).get("total"),
                        "applicable": (row.get("targets") or {}).get("applicable"),
                    },
                    "goal_id": row.get("goal_id"),
                    "created_at": row.get("created_at"),
                } for row in store.list_repair_plans(
                    session_id=ctx.session_id, limit=10)]
            except LeaseLost:
                raise
            except Exception:  # noqa: BLE001 — a dead plan list is not fatal
                plans = []
            try:
                rows = store.list_goals(limit=10, kinds=["batch"],
                                        session_id=ctx.session_id)
                goals = [{"goal_id": row["goal_id"], "title": row.get("title"),
                          "status": row.get("status"),
                          "created_at": row.get("created_at")}
                         for row in rows]
            except LeaseLost:
                raise
            except Exception:  # noqa: BLE001 — a dead goal list is not fatal
                goals = []
        return ok({
            "kind": "repair_workspace.v1",
            "session_id": ctx.session_id,
            "workspace_version": (ctx.workspace or {}).get("version"),
            "items": items[:200],
            "total": int((ctx.workspace or {}).get("total") or len(items)),
            "local_only": int((ctx.workspace or {}).get("local_only") or 0),
            "truncated": bool((ctx.workspace or {}).get("truncated")),
            "current": ctx.current,
            "selected": selected[:50],
            "batch_draft": ctx.batch_draft,
            "plans": plans,
            "goals": goals,
        })
    except (ValueError, OSError) as exc:
        return err(str(exc))
    finally:
        if owned:
            try:
                store.close()
            except Exception:  # noqa: BLE001
                pass


def _goal_items(ctx, goal_id, item_id=None, *, status=None, limit=10, cursor=0,
                verify=True):
    owned = False
    store = ctx.store
    if store is None:
        store, owned = _unbound_store()
    try:
        goal = store.get_goal(goal_id)
        if not goal:
            return err("batch goal not found")
        if (ctx.session_id is not None and goal.get("session_id") is not None
                and goal["session_id"] != ctx.session_id):
            return err("当前会话没有可读取的关联批次")
        if item_id is not None:
            item = store.get_goal_item(goal_id, item_id)
            if not item:
                return err("batch item not found")
            return ok(workspace_tools.item_detail(store, item, verify=verify))
        return ok(workspace_tools.items_page(store, goal_id, status=status,
                                             limit=limit, cursor=cursor))
    except (ValueError, OSError, KeyError) as exc:
        return err(str(exc))
    finally:
        if owned:
            try:
                store.close()
            except Exception:  # noqa: BLE001
                pass


# ---------------------------------------------------------------------------
# registered tools
# ---------------------------------------------------------------------------

_PREPARE_DESC = (
    "Agent协助与工具栏自动修复统一准备真实SR计划（production），不提供Demo/Fake回退。"
    "根据当前可信 Viewer 工作区（当前影像 / 多选影像 / 左侧工作区或已冻结清单）"
    "准备一份修复计划，并持久化计划、目标身份与执行策略。"
    "只做目标校验与计划冻结，绝不提交 SR、不改原始影像、不启动后台修复。"
    "默认使用当前工作区，禁止为识别当前影像或清单而再次检索场景库。"
    "支持本地TIFF、上传资源及允许目录中的服务器影像，不要求资源进入盘阵场景库。"
    "没有后端源文件绑定或PAN/NOSR契约时仍返回资源评估计划及具体缺项；此类计划不可启动SR。"
    "消息中的资源引用仅用于观察与规划，不授予任意文件访问权限。"
    "返回 plan_id、targets、strategy、mode、execution 与 confirmation_required；"
    "targets 含 total/excluded/applicable/items/unresolved，items 每项含 item_id/ordinal/name/scene_ref/status；不存在 targets.ids。"
    "如返回 land_constraint_unavailable，读取 blocking 中的具体原因；受阻计划不能确认或启动。"
    "可提示使用 Viewer 自动绘制并核对、保存掩码。仅在用户明确选择后，使用已有掩码另建当前景计划。"
    "归档结果读取 /data/targets/items；启动仅需 plan_id 和版本，不需要逐景提取 ID。"
    "计划必须由真实用户在界面上确认后，才能用 start_repair 启动。"
)

_START_DESC = (
    "启动一份已经通过服务端真实用户确认的修复计划，创建或复用现有 Batch Goal。"
    "只接受 prepare_repair 返回的 plan_id 与计划版本；不接受 approved=true、"
    "不接受路径、掩码或 SR 参数，也不直接调用检测、掩码或 SR 执行器。"
    "服务端会验证计划未过期、目标与冻结清单未变化、源文件修订一致、会话权限与用户确认记录；"
    "重复调用、模型重试或响应丢失时会复用同一个 Goal，不会产生第二次物理 SR 提交。"
    "production 模式若尚未准入会明确拒绝，不得绕过。"
    "返回 goal_id、状态与是否复用；后台执行由现有 Batch Worker 持续驱动。"
)

_GET_WORKSPACE_DESC = (
    "零控制参数只读查询本条消息冻结的 Viewer 工作区候选集、当前影像、多选影像、"
    "本地资源数量，以及本会话最近的批量修复 Goal。"
    "用于解释“这批图/当前影像”指的是什么；不检索场景库、不读取像素、不启动或控制任务。"
)

_LIST_ITEMS_DESC = (
    "只读查询一个已知 Batch Goal 的任务项列表与状态摘要，用于解释进度和失败原因。"
    "按冻结 Manifest 原始 ordinal 升序，含排除/SKIPPED 项；status 支持英文逗号分隔，"
    "cursor 是排他原始 ordinal，多读一项判断 next_cursor/has_more。"
    "需要权威 goal_id；不猜最新任务，不按聊天文本推断成功，也不控制或提交作业。"
    "演示/Fake SR 结果不等于生产质量验收。"
)

_GET_ITEM_DESC = (
    "只读查询一个已知 Batch Goal 中某一项的状态、作业、掩码、人工复核与验证摘要。"
    "默认 verify=true 校验审核绑定与文件修订是否仍有效，返回 review_valid 与失效原因；"
    "区域详情最多列出前 20 条决定（rejected 优先），不含候选几何或审核人身份。"
    "用于回答“第三景为什么还没完成”；不重新检测、不重新提交、不修改任务状态。"
)

_PREPARE_PARAMS = {
    "type": "object",
    "properties": {
        "scope": {
            "type": "string",
            "enum": ["current", "selection", "workspace", "draft"],
            "default": "workspace",
            "description": "修复目标：current=当前活动影像，selection=当前多选影像，workspace=左侧全部影像资源（含本地/上传），draft=当前修复草稿清单（已排除项跳过）。",
        },
        "mode": {
            "type": "string", "enum": ["production"], "default": "production",
            "description": "与自动修复一致，使用真实 SR 执行（production）；必须经用户确认及现有生产准入，受阻时不回退模拟。",
        },
        "strategy": {
            "type": "string", "enum": ["automatic", "review"], "default": "automatic",
            "description": "掩码策略：automatic 自动生成，review 在提交前等待人工复核；智能对比固定进入 review。",
        },
        "detection_method": {
            "type": "string", "enum": ["absdiff", "ssim", "smart"], "default": "ssim",
            "description": "检测方法：ssim 结构差异（默认）、absdiff 像素差异、smart 实验性智能对比。",
        },
        "mask_policy": {
            "type": "string", "enum": ["auto", "existing"], "default": "auto",
            "description": "掩码来源：auto 使用现有检测与 MaskBuilder，existing 复用已保存且服务端校验过的人工掩码（仅单景）。",
        },
        "title": {"type": "string", "description": "可选的任务标题，最长 200 字；省略时按目标数量生成。"},
    },
    "required": [],
    "additionalProperties": False,
}

_START_PARAMS = {
    "type": "object",
    "properties": {
        "plan_id": {"type": "string", "minLength": 1,
                    "description": "prepare_repair 返回的计划 ID；不能编造，也不能用场景名代替。"},
        "plan_version": {"type": "integer", "minimum": 1,
                         "description": "可选的计划版本；与服务端不一致时拒绝启动并要求重新确认。"},
    },
    "required": ["plan_id"],
    "additionalProperties": False,
}

_GET_WORKSPACE_PARAMS = {"type": "object", "properties": {},
                         "required": [], "additionalProperties": False}
_LIST_ITEMS_PARAMS = {
    "type": "object",
    "properties": {
        "goal_id": {"type": "string", "minLength": 1, "maxLength": 512,
                    "description": "已知 Batch Goal ID；不猜最新任务。"},
        "status": {"type": "string", "maxLength": 200,
                   "description": "精确持久状态，多个用英文逗号分隔，如 FAILED,AWAITING_REVIEW；定位原始第N景时不传。"},
        "limit": {"type": "integer", "minimum": 1, "maximum": 50,
                  "description": "每页数量，最多50；定位原始序号时取1。"},
        "cursor": {"type": "integer", "minimum": 0,
                   "description": "排他原始ordinal；初始0，第N景用N-1，后续页用next_cursor。"},
    },
    "required": ["goal_id"], "additionalProperties": False,
}
_GET_ITEM_PARAMS = {
    "type": "object",
    "properties": {
        "goal_id": {"type": "string", "minLength": 1, "maxLength": 512,
                    "description": "已知 Batch Goal ID；不猜最新任务。"},
        "item_id": {"type": "string", "minLength": 1, "maxLength": 512,
                    "description": "该 Goal 内的任务项 ID；可从 list_repair_items 取得。"},
        "verify": {"type": "boolean", "default": True,
                   "description": "默认 true：校验审核绑定与文件修订是否仍有效（只读摘要，不执行 SR），结果见 review_valid/review_invalid_reason；false 只读数据库，review_valid 为 null。"},
    },
    "required": ["goal_id", "item_id"], "additionalProperties": False,
}


@tool(name="prepare_repair", description=_PREPARE_DESC,
      params_schema=_PREPARE_PARAMS)
def prepare_repair(scope="workspace", mode="production", strategy="automatic",
                   detection_method="ssim", mask_policy="auto", title=None):
    return _prepare(_Context(), scope, mode, strategy, detection_method,
                    mask_policy, title)


@tool(name="start_repair", description=_START_DESC, params_schema=_START_PARAMS)
def start_repair(plan_id, plan_version=None):
    return _start(_Context(), plan_id, plan_version)


@tool(name="get_repair_workspace", description=_GET_WORKSPACE_DESC,
      params_schema=_GET_WORKSPACE_PARAMS)
def get_repair_workspace():
    return _workspace_view(_Context())


@tool(name="list_repair_items", description=_LIST_ITEMS_DESC,
      params_schema=_LIST_ITEMS_PARAMS)
def list_repair_items(goal_id, status=None, limit=10, cursor=0):
    return _goal_items(_Context(), goal_id, status=status, limit=limit, cursor=cursor)


@tool(name="get_repair_item", description=_GET_ITEM_DESC,
      params_schema=_GET_ITEM_PARAMS)
def get_repair_item(goal_id, item_id, verify=True):
    return _goal_items(_Context(), goal_id, item_id, verify=verify)


# ---------------------------------------------------------------------------
# bound variants (workspace/session frozen per message)
# ---------------------------------------------------------------------------

_INSPECTION_ROW_FIELDS = ('item_id', 'name', 'ordinal', 'status',
                          'candidate_count', 'candidates_truncated')
_INSPECTION_DIAGNOSTIC_FIELDS = ('line', 'severity', 'code', 'name', 'message')
# Local difference crop shown in the UI. The full difference map is a
# multi-megabyte PNG; only a bounded JPEG around the first candidate travels
# with the page. The model view strips every image (see inspection_result_view).
_INSPECTION_THUMB_PX = 160
_INSPECTION_THUMB_MAX_BYTES = 12000


def _inspection_thumbnail(detected):
    """Crop of the difference map around the first candidate, as a data JPEG.

    Around the ranked candidate keeps the comparison local to the area under
    review; clean items get the whole (downscaled) map so the column still
    shows the evidence behind "no candidate". A missing or unusable map returns
    None (UI shows its dash placeholder); a crop that cannot be established
    falls back to the whole map instead of removing the evidence entirely.

    **坐标口径**：候选的 ``bbox`` 在 PAN 源像素里（``source_pixel``），而
    ``differenceMap`` 是下采样后的分析网格（长边 ≤2048）。两者不是同一坐标系，
    必须先用 ``analysis.grid_to_pan`` 的仿射变换反算回网格；直接把源坐标当网格
    坐标裁剪，真实大图里靠右下角的候选会整框出界，差异图列就会时有时无。
    """
    analysis = detected.get('analysis') if isinstance(detected, dict) else None
    if not isinstance(analysis, dict):
        return None
    encoded = analysis.get('weightedDifferenceMap') or analysis.get('differenceMap')
    if not isinstance(encoded, str) or not encoded.startswith('data:image/'):
        return None
    try:
        import base64
        import io

        import numpy as np
        from PIL import Image

        from backend.services import image_diff

        gray, _valid = image_diff.decode_gray_png(encoded)
        height, width = gray.shape
        box = None
        for candidate in detected.get('candidates') or []:
            bbox = candidate.get('bbox') if isinstance(candidate, dict) else None
            if isinstance(bbox, (list, tuple)) and len(bbox) == 4:
                box = [float(value) for value in bbox]
                break
        if box is not None:
            transform = analysis.get('grid_to_pan')
            scale = transform.get('scale') if isinstance(transform, dict) else None
            offset = transform.get('offset') if isinstance(transform, dict) else None
            usable = (isinstance(scale, (list, tuple)) and len(scale) == 2
                      and isinstance(offset, (list, tuple)) and len(offset) == 2
                      and all(isinstance(value, (int, float)) and not isinstance(value, bool)
                              and np.isfinite(value) for value in (*scale, *offset))
                      and scale[0] > 0 and scale[1] > 0)
            if usable:
                box = [(box[0] - offset[0]) / scale[0], (box[1] - offset[1]) / scale[1],
                       (box[2] - offset[0]) / scale[0], (box[3] - offset[1]) / scale[1]]
            else:
                # 没有可信变换就不硬裁：整幅图仍能说明差异在哪。
                box = None
        if box is not None:
            x0, y0, x1, y1 = (int(round(value)) for value in box)
            pad = max(8, int(max(x1 - x0, y1 - y0) * 0.25))
            x0, y0 = max(0, x0 - pad), max(0, y0 - pad)
            x1, y1 = min(width, x1 + pad), min(height, y1 + pad)
            if x1 - x0 < 2 or y1 - y0 < 2:
                box = None
        if box is None:
            x0, y0, x1, y1 = 0, 0, width, height
        crop = np.clip(gray[y0:y1, x0:x1], 0, 1).astype(np.float32)
        # 表格缩略图只做显示层对比度拉伸：接近灰底的真实差异往往只占几个 DN，
        # 缩到 160px 后几乎看不出来。按非零像素的 1%..99.5% 分位拉满，零点保持黑；
        # 分析数值、阈值、候选资格与掩码都不受这里影响。
        positive = crop[crop > 0]
        if positive.size:
            sample = positive[::max(1, positive.size // 200_000)]
            lo = float(np.percentile(sample, 1.0))
            hi = float(np.percentile(sample, 99.5))
            if np.isfinite(lo) and np.isfinite(hi) and hi - lo > 1e-6:
                crop = np.clip((crop - lo) / (hi - lo), 0, 1)
        crop = np.rint(crop * 255).astype(np.uint8)
        image = Image.fromarray(crop)
        # 末档 64px/40 是兜底：异常噪声图在 96px 下也可能超过 12KB 的 base64 预算。
        for edge, quality in ((_INSPECTION_THUMB_PX, 70), (128, 55), (96, 45), (64, 40)):
            view = image.copy()
            view.thumbnail((edge, edge), Image.BILINEAR)
            buffer = io.BytesIO()
            view.save(buffer, format='JPEG', quality=quality)
            raw = buffer.getvalue()
            if len(raw) * 4 // 3 <= _INSPECTION_THUMB_MAX_BYTES:
                return 'data:image/jpeg;base64,' + base64.b64encode(raw).decode('ascii')
        return None
    except Exception:
        return None


def inspection_result_view(result):
    """Compact model view; the UI keeps the full stored page.

    Preview URLs and candidate geometry dominate the byte budget without
    helping the agent summarize a QC page. Keeping the outcome fields for
    every row also means a failed scene never silently disappears.
    """
    data = result.get('data') if isinstance(result, dict) else None
    if not isinstance(data, dict) or data.get('kind') != 'repair_inspection.v1':
        return result
    rows = []
    for row in data.get('items') or []:
        if not isinstance(row, dict):
            continue
        kept = {key: row[key] for key in _INSPECTION_ROW_FIELDS if key in row}
        if row.get('reason'):
            kept['reason'] = row['reason']
        rows.append(kept)
    diagnostics = [row for row in data.get('diagnostics') or [] if isinstance(row, dict)]
    view = dict(result)
    view['data'] = {**data, 'items': rows,
                    'diagnostics': [{k: row[k] for k in _INSPECTION_DIAGNOSTIC_FIELDS
                                     if k in row} for row in diagnostics[:8]]}
    if len(diagnostics) > 8:
        view['data']['diagnostics_total'] = len(diagnostics)
    return view


def inspection_tool(snapshot,store,session_id,*,raster_runner=None):
    """Read-only, paged DN inspection. No MaskBuilder, repair plan or Goal."""
    from backend.services import batch_manifest,batch_detector,agent_repair_settings
    from backend.api.paths import scene_id_abs
    from urllib.parse import quote
    settings=agent_repair_settings.normalize((snapshot or {}).get('agent_repair_settings')) or {}
    frozen=snapshot or {};manifests={}
    def run(scope='workspace',cursor=0,limit=5):
        if scope not in ('workspace','current','selection') or type(cursor) is not int or cursor<0 or type(limit) is not int or not 1<=limit<=5:
            return err('质检分页参数无效，limit 为1–5')
        if scope=='selection' and frozen.get('selected_truncated'):return err('多选影像已截断，请缩小选择范围')
        if scope=='workspace' and not (frozen.get('workspace') or {}).get('items') and frozen.get('current'):scope='current'
        try:
            if scope not in manifests:
                def prepare():
                    sources,*_=svc._workspace_sources(frozen.get('workspace'),frozen.get('current'),frozen.get('selected_images'),scope)
                    if not sources:raise ValueError('没有可质检的服务端影像，请先解析影像与 NOSR 配对')
                    imported=batch_manifest.import_manifest(text='',sources=sources,mode='production',
                        detection_method=settings.get('detection_method','ssim'),tone_weight=settings.get('tone_weight',True),
                        skip_completed=settings.get('skip_completed',True))
                    return batch_manifest.load_manifest(imported['manifest_ref'])
                manifests[scope]=raster_runner(prepare) if raster_runner else prepare()
            manifest=manifests[scope];items=manifest['items'];rows=[]
            for item in items[cursor:cursor+limit]:
                def inspect(item=item):
                    target={**item['target_image'],'item_id':item['item_id']}
                    return batch_detector.detect(target,item['reference_image'],{**item['config']['detection'],'execution_mode':'production'})
                try:
                    detected=raster_runner(inspect) if raster_runner else inspect()
                    candidates=detected.get('candidates') or []
                    images={'pan': '/api/scenes/'+quote(scene_id_abs(item['target_image']['path']),safe='')+'/preview?div=16',
                        'nosr':'/api/scenes/'+quote(scene_id_abs(item['reference_image']['path']),safe='')+'/preview?div=16'}
                    # Only a bounded local crop, never the full difference PNG.
                    thumbnail=_inspection_thumbnail(detected)
                    if thumbnail:
                        images['difference']=thumbnail
                    rows.append({'item_id':item['item_id'],'name':item['name'],'ordinal':item['ordinal'],
                        'status':'candidates' if candidates else 'no_candidate','candidate_count':len(candidates),
                        'images':images,
                        'source':'real-dn-grid','candidates':[{'candidate_id':c['candidate_id'],'bbox':c.get('bbox'),'label':c.get('label')} for c in candidates[:12]],
                        'candidates_truncated':len(candidates)>12})
                except Exception as exc:
                    rows.append({'item_id':item['item_id'],'name':item['name'],'status':'failed','reason':str(exc)[:300]})
            next_cursor=cursor+len(rows)
            return ok({'kind':'repair_inspection.v1','inspection_only':True,'detection_method':settings.get('detection_method','ssim'),
                'tone_weight':settings.get('tone_weight',True),'scope':scope,'cursor':cursor,
                'total':len(items),'items':rows,'diagnostics':manifest.get('diagnostics',[]),
                'has_more':next_cursor<len(items),'next_cursor':next_cursor if next_cursor<len(items) else None,
                'note':'只读检测候选，不代表确认缺陷；未生成掩码、修复计划、Goal 或 SR 作业'})
        except (ValueError,OSError) as exc:return err(str(exc))
    return Tool(name='inspect_repair_workspace',description='仅质检：对本消息冻结的 Viewer PAN/NOSR 读取真实 DN 并复用差异检测。整批使用 limit=5，从 cursor=0 按 next_cursor 继续直到 has_more=false。直接调用工具，避免逐页播报快照字段、游标和分页计划。全部分页收齐后界面自动展示总表。返回候选与失败/跳过事实，不生成掩码、计划或 SR 作业。',
        result_protected_fields=('next_cursor',), result_record_fields=('items',),
        result_formatter=inspection_result_view,
        params_schema={'type':'object','properties':{'scope':{'type':'string','enum':['workspace','current','selection']},
            'cursor':{'type':'integer','minimum':0},'limit':{'type':'integer','minimum':1,'maximum':5}},'additionalProperties':False},run=run,timeout_sec=180)


def bound_tool(name: str, *, workspace=None, current=None, selected_images=None,
               batch_draft=None, session_id=None, store=None, selected_truncated=False,
               preferences=None, raster_runner=None) -> Tool:
    """Return one tool bound to this message's frozen Viewer context."""
    context = _Context(store=store, session_id=session_id,
                       workspace=workspace, current=current,
                       selected_images=selected_images,
                       batch_draft=batch_draft, selected_truncated=selected_truncated,
                       preferences=preferences, raster_runner=raster_runner)
    if name == "prepare_repair":
        def run(scope="workspace", mode="production", strategy="automatic",
                detection_method="ssim", mask_policy="auto", title=None):
            return _prepare(context, scope, mode, strategy, detection_method,
                            mask_policy, title)
        base = prepare_repair
    elif name == "start_repair":
        def run(plan_id, plan_version=None):
            return _start(context, plan_id, plan_version)
        base = start_repair
    elif name == "get_repair_workspace":
        def run():
            return _workspace_view(context)
        base = get_repair_workspace
    elif name == "list_repair_items":
        def run(goal_id, status=None, limit=10, cursor=0):
            return _goal_items(context, goal_id, status=status, limit=limit, cursor=cursor)
        base = list_repair_items
    elif name == "get_repair_item":
        def run(goal_id, item_id, verify=True):
            return _goal_items(context, goal_id, item_id, verify=verify)
        base = get_repair_item
    else:
        raise KeyError(name)
    run.__module__ = __name__
    descriptions = {
        "prepare_repair": _PREPARE_DESC,
        "start_repair": _START_DESC,
        "get_repair_workspace": _GET_WORKSPACE_DESC,
        "list_repair_items": _LIST_ITEMS_DESC,
        "get_repair_item": _GET_ITEM_DESC,
    }
    schemas = {
        "prepare_repair": _PREPARE_PARAMS,
        "start_repair": _START_PARAMS,
        "get_repair_workspace": _GET_WORKSPACE_PARAMS,
        "list_repair_items": _LIST_ITEMS_PARAMS,
        "get_repair_item": _GET_ITEM_PARAMS,
    }
    return Tool(name=name, description=descriptions[name],
                params_schema=schemas[name], run=run)
