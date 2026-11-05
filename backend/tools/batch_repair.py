"""Planning only: no scan, detector loop, Goal creation or SR submission."""
from pathlib import Path

from backend.pathguard import ensure_allowed, PathDeniedError
from backend.services.spatial_identity import resource_identity
from .contract import tool, ok, err


@tool(
    name="prepare_batch_repair",
    description=(
        "为用户明确指定的 Viewer 影像集合生成结构化待修复草案，不扫描场景库，不执行 SR。"
        "传入 Viewer 已知的影像或场景引用，不允许猜路径；准备阶段不访问文件、不校验生产条件。evidence 只写已有检测依据与复核建议；"
        "没有检测结果时必须注明待检测。返回草案由用户加入工具栏、确认范围与执行策略后，"
        "经统一批次导入与 Batch Goal 创建执行。不要在 Agent 内循环处理长任务。"),
    params_schema={
        "type": "object", "additionalProperties": False,
        "properties": {"items": {"type": "array", "minItems": 1, "maxItems": 500, "description": "用户明确指定的影像集合与已有检测依据；不扫描或猜路径。",
            "items": {"type": "object", "additionalProperties": False,
                "properties": {"path": {"type": "string"}, "evidence": {"type": "string"}},
                "required": ["path", "evidence"]}}},
        "required": ["items"],
    },
)
def prepare_batch_repair(items):
    try:
        candidates = []
        seen = set()
        for item in items:
            # Pure planning: preserve explicit references, never read or authorize a file.
            path = item['path'].strip()
            evidence = item['evidence']
            if not path or len(path) > 4096 or not isinstance(evidence, str) or len(evidence) > 4096:
                return err('草案引用或依据无效')
            if path in seen:
                continue
            seen.add(path)
            name = path.replace('\\', '/').rstrip('/').split('/')[-1]
            candidates.append({"path": path, "name": name, "evidence": evidence})
        return ok({"kind": "batch_repair_draft.v1", "persisted": False,
                   "candidates": candidates, "requires_confirmation": False,
                   "submission_requires_scope_and_strategy": True})
    except (ValueError, OSError, PathDeniedError) as exc:
        return err(str(exc))


@tool(
    name="query_batch_repair",
    description="只读查询已知 Batch Goal ID 的权威快照，解释检测、提交、验收与异常；不控制或循环执行任务。演示验收不得解释为生产成功。",
    params_schema={"type": "object", "properties": {"goal_id": {"type": "string", "minLength": 1, "description": "已知批次 Goal ID，不猜最新任务。"}}, "required": ["goal_id"], "additionalProperties": False},
)
def query_batch_repair(goal_id, _store=None):
    from backend.services.store import Store
    from backend.services.goal_lease import current_tool
    execution = current_tool.get() or {}
    store = _store if _store is not None else execution.get("store")
    owned = store is None
    if owned:
        store = Store()
    try:
        snapshot = store.batch_snapshot(goal_id)
        return ok(snapshot) if snapshot else err("batch goal not found")
    finally:
        if owned:
            store.close()
