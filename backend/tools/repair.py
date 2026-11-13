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
                 current=None, selected_images=None, batch_draft=None):
        self.store = store
        self.session_id = session_id
        self.workspace = workspace
        self.current = current
        self.selected_images = selected_images
        self.batch_draft = batch_draft

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
        if (scope != "draft" and ctx.workspace is None and ctx.current is None
                and not ctx.selected_images):
            return err(
                "这条消息没有绑定 Viewer 工作区；请先从场景库打开影像或勾选工作区，"
                "不要用场景库检索代替当前工作区。")
        kwargs = {}
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
            title=title, created_by=source, **kwargs)
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
    owned = False
    store = ctx.store
    if store is None:
        store, owned = _unbound_store()
    try:
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


def _goal_items(ctx, goal_id, item_id=None):
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
            cp = item.get("checkpoint") or {}
            return ok({
                "kind": "repair_item_detail.v1",
                "goal_id": goal_id,
                "item_id": item["item_id"],
                "ordinal": item["ordinal"],
                "name": item["name"],
                "status": item["status"],
                "attempt": int(cp.get("attempt") or 0),
                "error": item.get("error"),
                "job_id": item.get("external_job_id") or cp.get("job_id"),
                "sr_status": cp.get("sr_status"),
                "scene_ref": cp.get("scene_ref"),
                "mask_ready": bool(cp.get("mask")),
                "overlay_ready": bool((cp.get("mask") or {}).get("overlay")),
                "review": cp.get("review"),
                "review_required": bool(cp.get("review_required")),
                "quality_review_id": cp.get("quality_review_id"),
                "output_path": svc.verified_output_path(item),
                "verification": item.get("verification") or {},
                "last_detection_error": cp.get("last_detection_error"),
                "last_submit_prepare_error": cp.get("last_submit_prepare_error"),
            })
        items = store.list_goal_items(goal_id, limit=500)
        return ok({
            "kind": "repair_items.v1",
            "goal_id": goal_id,
            "status": goal.get("status"),
            "counts": store.goal_item_status_counts(goal_id),
            "items": [{
                "item_id": item["item_id"],
                "ordinal": item["ordinal"],
                "name": item["name"],
                "status": item["status"],
                "attempt": int((item.get("checkpoint") or {}).get("attempt") or 0),
                "job_id": item.get("external_job_id")
                or (item.get("checkpoint") or {}).get("job_id"),
                "error": item.get("error"),
            } for item in items],
        })
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
    "根据当前可信 Viewer 工作区（当前影像 / 多选影像 / 左侧工作区或已冻结清单）"
    "准备一份修复计划，并持久化计划、目标身份与执行策略。"
    "只做目标校验与计划冻结，绝不提交 SR、不改原始影像、不启动后台修复。"
    "默认使用当前工作区，禁止为识别当前影像或清单而再次检索场景库。"
    "只接受工作区里的盘阵场景身份，不接受任意文件路径；本地图片没有可信路径时会明确阻断。"
    "返回 plan_id、targets、strategy、mode、execution 与 confirmation_required；"
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
    "本地无盘阵身份的数量，以及本会话最近的批量修复 Goal。"
    "用于解释“这批图/当前影像”指的是什么；不检索场景库、不读取像素、不启动或控制任务。"
)

_LIST_ITEMS_DESC = (
    "只读查询一个已知 Batch Goal 的任务项列表与状态计数，用于解释进度和失败原因。"
    "需要权威 goal_id；不猜最新任务，不按聊天文本推断成功，也不控制或提交作业。"
    "演示/Fake SR 结果不等于生产质量验收。"
)

_GET_ITEM_DESC = (
    "只读查询一个已知 Batch Goal 中某一项的状态、作业、掩码就绪、人工复核与验证摘要。"
    "用于回答“第三景为什么还没完成”；不重新检测、不重新提交、不修改任务状态。"
)

_PREPARE_PARAMS = {
    "type": "object",
    "properties": {
        "scope": {
            "type": "string",
            "enum": ["current", "selection", "workspace", "draft"],
            "default": "workspace",
            "description": "修复目标：current=当前活动影像，selection=当前多选影像，workspace=左侧工作区全部盘阵场景，draft=当前修复草稿清单（已排除项跳过）。",
        },
        "mode": {
            "type": "string", "enum": ["demo", "production"], "default": "demo",
            "description": "执行模式；demo 使用现有 Fake SR 安全链路，production 受现有准入限制，未开放时会被明确拒绝。",
        },
        "strategy": {
            "type": "string", "enum": ["automatic", "review"], "default": "automatic",
            "description": "掩码策略：automatic 自动生成，review 在提交前等待人工复核；智能对比固定进入 review。",
        },
        "detection_method": {
            "type": "string", "enum": ["absdiff", "ssim", "smart"], "default": "absdiff",
            "description": "检测方法：absdiff 像素差异、ssim 结构差异、smart 实验性智能对比。",
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
    "properties": {"goal_id": {"type": "string", "minLength": 1,
                               "description": "已知 Batch Goal ID；不猜最新任务。"}},
    "required": ["goal_id"], "additionalProperties": False,
}
_GET_ITEM_PARAMS = {
    "type": "object",
    "properties": {
        "goal_id": {"type": "string", "minLength": 1,
                    "description": "已知 Batch Goal ID；不猜最新任务。"},
        "item_id": {"type": "string", "minLength": 1,
                    "description": "该 Goal 内的任务项 ID；可从 list_repair_items 取得。"},
    },
    "required": ["goal_id", "item_id"], "additionalProperties": False,
}


@tool(name="prepare_repair", description=_PREPARE_DESC,
      params_schema=_PREPARE_PARAMS)
def prepare_repair(scope="workspace", mode="demo", strategy="automatic",
                   detection_method="absdiff", mask_policy="auto", title=None):
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
def list_repair_items(goal_id):
    return _goal_items(_Context(), goal_id)


@tool(name="get_repair_item", description=_GET_ITEM_DESC,
      params_schema=_GET_ITEM_PARAMS)
def get_repair_item(goal_id, item_id):
    return _goal_items(_Context(), goal_id, item_id)


# ---------------------------------------------------------------------------
# bound variants (workspace/session frozen per message)
# ---------------------------------------------------------------------------

def bound_tool(name: str, *, workspace=None, current=None, selected_images=None,
               batch_draft=None, session_id=None, store=None) -> Tool:
    """Return one tool bound to this message's frozen Viewer context."""
    context = _Context(store=store, session_id=session_id,
                       workspace=workspace, current=current,
                       selected_images=selected_images,
                       batch_draft=batch_draft)
    if name == "prepare_repair":
        def run(scope="workspace", mode="demo", strategy="automatic",
                detection_method="absdiff", mask_policy="auto", title=None):
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
        def run(goal_id):
            return _goal_items(context, goal_id)
        base = list_repair_items
    elif name == "get_repair_item":
        def run(goal_id, item_id):
            return _goal_items(context, goal_id, item_id)
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
