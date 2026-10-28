"""search_scenes — satellite-archive lookup agent tool (thin wrapper).

Defines the search interface; the service (backend/services/scene_search.py)
switches between the real disk-array backend (SR_SCENES_ROOT set) and a fake
backend for local development. Fake rows carry `fake: True` — the agent should
never hand placeholder paths to run_sr.

Two scopes (2026-10-28):

  * workspace — the images in the message's Viewer workspace snapshot. This is
    the default. Items are opaque scene ids; every id is re-resolved through
    backend.api.paths (whitelist + existence) before it becomes a row, so the
    snapshot never grants path authority.
  * library — the whole SR_SCENES_ROOT archive. The model must only ask for it
    when the user explicitly widens the scope. With no workspace snapshot at
    all (CLI, REST /api/tools/search_scenes, old messages) the tool falls back
    to library and says so in `scope_reason`; it never pretends to be scoped.

Both scopes exclude scenes the user hid from the scene library (persisted in
SQLite, see Store.hide_scenes). The exclusion is applied before filtering and
limiting, so `count` is the real visible total.
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

from backend.api.paths import PathDeniedError, scene_id_to_abs
from backend.services import scene_search as svc
from backend.services import store as store_mod
from .contract import Tool, err, ok, tool

_DESC = (
    "只读检索遥感场景库，返回可用影像的元数据。默认检索当前 Viewer 工作区"
    "（workspace）：用户从场景库打开或手工解析过的那些影像。只有用户明确要求"
    "“检索整个场景库/盘阵”时才传 scope=library 检索 SR_SCENES_ROOT 全库。"
    "两种范围都排除用户已从场景库隐藏的场景。工作区为空（左侧没有场景身份影像）"
    "或这条消息没有工作区快照时按全库检索，并在 scope_reason 里说明原因。"
    "当前 Viewer 本地或离线影像的信息"
    "应使用 get_current_image_info；检索零匹配不代表当前影像不存在。"
    "各条件同时生效；workspace 按工作区顺序返回，library 按日期及 id 降序返回。"
    "scanned 是参与检索的场景数，count 是匹配总数，results 最多 limit 条。"
    "结果带 scope（实际使用的范围）、scope_reason（为何回退）、workspace_version、"
    "excluded_hidden（被隐藏排除的数量）。场景行含 id、path（影像文件路径）、"
    "satellite、sensor、date、size_bytes 和 fake，元数据解析不到时可为空。"
    "未配置有效场景根目录时返回 source=fake 的测试数据；fake 路径仅供测试，"
    "不得传给 run_sr。"
)

_PARAMS = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": "对场景 id 或影像文件路径作不区分大小写的子串匹配，例如 GF07；省略或空串不限制。",
        },
        "satellite": {
            "type": "string",
            "description": "对解析出的卫星标识作不区分大小写的子串匹配，例如 GF07A03；省略或空串不限制。",
        },
        "date_from": {
            "type": "string",
            "description": "日期下界（含当天），格式 YYYY-MM-DD；省略或空串不限制下界，指定后排除日期未知的场景。",
        },
        "date_to": {
            "type": "string",
            "description": "日期上界（含当天），格式 YYYY-MM-DD；省略或空串不限制上界，指定后排除日期未知的场景。",
        },
        "limit": {
            "type": "integer", "minimum": 1, "maximum": 500, "default": 20,
            "description": "results 的条数上限，1..500，省略为 20；count 仍是匹配总数，模型收到的结果还可能受结果预算截断。",
        },
        "scope": {
            "type": "string", "enum": ["workspace", "library"], "default": "workspace",
            "description": "检索范围：workspace=当前 Viewer 工作区（默认）；library=整个场景库。仅当用户明确要求检索全库时才用 library。",
        },
    },
    "required": [],
}


def _row_from_path(abs_path: Path) -> dict:
    """把一个真实的场景输入文件包成与库检索同形的行。"""
    meta = svc.parse_filename(abs_path)
    return {**meta, "id": abs_path.stem, "path": str(abs_path),
            "size_bytes": abs_path.stat().st_size, "fake": False}


def _hidden_from_default_store() -> set[str]:
    """未显式注入隐藏集合时（REST / CLI）从同一个平台库读。

    只在库文件已经存在时读：没有库的新环境不该因为一次只读检索就凭空建库。
    读来的 Store 用完就 close()：这条路在 REST / CLI 里没有应用生命周期兜底，
    漏一个 sqlite 句柄会让 Windows 上删临时库失败（测试收尾会撞 WinError 32）。
    """
    store = None
    try:
        if not os.path.exists(store_mod.default_db_path()):
            return set()
        store = store_mod.default_store()
        return {e["scene_dir"] for e in store.list_hidden_scenes()}
    except Exception:  # noqa: BLE001 — 隐藏集合读不到不该让检索失败
        return set()
    finally:
        if store is not None:
            try:
                store.close()
            except Exception:  # noqa: BLE001
                pass


def _workspace_rows(workspace: dict, root,
                    hidden: set[str]) -> tuple[list[dict], list[dict], int]:
    """工作区场景 id → 行。返回 (命中前的候选行, 无法解析的项, 被隐藏排除的数)。

    只信场景 id 的位置（parent），不信快照里的任何路径：解析走
    `scene_id_to_abs`（白名单 + 存在性），再要求那个目录满足场景目录判据，
    最后把行归一到**场景输入影像**（`input_scene_path`）。这样从产物/NOSR
    卡片进来的同一景也只算一景、返回同一个 id。
    """
    rows: list[dict] = []
    unresolved: list[dict] = []
    hidden_hits = 0
    seen: set[str] = set()
    for item in workspace.get("items") or []:
        name = item.get("name") or item.get("scene_id") or "未命名影像"
        try:
            abs_path = scene_id_to_abs(item["scene_id"], root)
        except (PathDeniedError, KeyError, OSError) as e:
            unresolved.append({"name": name, "reason": str(e)})
            continue
        scene_dir = abs_path.parent
        if svc.dir_key(scene_dir) in hidden:
            hidden_hits += 1
            continue
        inp = svc.input_scene_path(scene_dir)
        if inp is None or not svc.is_scene_file(inp):
            unresolved.append({"name": name,
                               "reason": "不是可检索的场景目录（缺少 meta.xml 或输入影像）"})
            continue
        if svc.dir_key(inp.parent) in hidden:
            hidden_hits += 1
            continue
        key = svc.dir_key(inp)
        if key in seen:
            continue
        seen.add(key)
        rows.append(_row_from_path(inp))
    return rows, unresolved, hidden_hits


def _run(params: dict, *, workspace=None, hidden_dirs=None) -> dict:
    try:
        if not isinstance(params, dict):
            raise TypeError("arguments must be an object")
        query = params.get("query", "")
        satellite = params.get("satellite")
        sensor = params.get("sensor")
        date_from = params.get("date_from")
        date_to = params.get("date_to")
        limit = int(params.get("limit", 20))
        scope = params.get("scope") or "workspace"
    except (KeyError, TypeError, ValueError) as e:
        return err(f"bad params: {e}")

    if scope not in ("workspace", "library"):
        return err("scope must be 'workspace' or 'library'")
    if not (1 <= limit <= 500):
        return err("limit must be in 1..500")
    for name, value in (("date_from", date_from), ("date_to", date_to)):
        if value:
            try:
                datetime.strptime(str(value), "%Y-%m-%d")
            except ValueError:
                return err(f"{name} must be 'YYYY-MM-DD'")

    hidden = hidden_dirs if hidden_dirs is not None else _hidden_from_default_store()
    root = os.environ.get("SR_SCENES_ROOT")
    workspace = workspace or None

    # 空工作区（左侧列表里一张有场景身份的图都没有）没有可限定的范围：
    # 按库检索并如实说明，否则模型只会拿到 0 条、以为盘阵上没有这一景。
    if scope == "workspace" and workspace is not None and workspace.get("items"):
        rows, unresolved, excluded_hidden = _workspace_rows(
            workspace, root, hidden)
        matched = [r for r in rows
                   if svc.matches(r, query, satellite, sensor, date_from, date_to)]
        # 工作区顺序本身就是语义（左侧列表怎么看就怎么返回），不按日期重排。
        return ok({
            "source": "disk",
            "scope": "workspace",
            "scope_reason": None,
            "workspace_version": workspace.get("version"),
            "workspace_truncated": bool(workspace.get("truncated")),
            "workspace_local_only": int(workspace.get("local_only") or 0),
            "scanned": len(rows),
            "count": len(matched),
            "excluded_hidden": excluded_hidden,
            "unresolved_count": len(unresolved),
            "unresolved": unresolved[:10],
            "results": matched[:limit],
        })

    # library：显式扩大范围，或消息里根本没有工作区快照（CLI / REST / 旧消息）。
    try:
        result = svc.search_scenes(
            root, query=query, satellite=satellite,
            date_from=date_from, date_to=date_to, limit=limit,
            exclude_dirs=hidden)
    except Exception as e:  # noqa: BLE001 — contract boundary
        return err(f"{type(e).__name__}: {e}")
    result = dict(result)
    result["scope"] = "library"
    if workspace is None:
        result["scope_reason"] = "no_workspace_snapshot"
    elif scope == "library":
        result["scope_reason"] = "user_requested_library"
    else:
        result["scope_reason"] = "empty_workspace"
    result["workspace_version"] = (workspace or {}).get("version")
    return ok(result)


@tool(name="search_scenes", description=_DESC, params_schema=_PARAMS)
def run_search_scenes(**params) -> dict:
    """未绑定工作区的入口（CLI、REST `/api/tools/search_scenes`、旧消息）。"""
    return _run(params)


def bound_tool(*, workspace=None, hidden_dirs=None) -> Tool:
    """把工作区快照与隐藏集合绑进工具，供 `services.viewer_context.bind_registry`。

    `run.__name__` / `__module__` 保持与内置工具一致：工具契约测试与清单按这两项
    认「哪个模块实现了这个工具」，绑定不该改变身份。
    """
    frozen_workspace = workspace
    frozen_hidden = hidden_dirs

    def run_search_scenes(**params):
        return _run(params, workspace=frozen_workspace, hidden_dirs=frozen_hidden)

    run_search_scenes.__module__ = __name__
    return Tool(name="search_scenes", description=_DESC, params_schema=_PARAMS,
                run=run_search_scenes)
