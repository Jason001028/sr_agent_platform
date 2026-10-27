"""search_scenes — satellite-archive lookup agent tool (thin wrapper).

Defines the search interface; the service (backend/services/scene_search.py)
switches between the real disk-array backend (SR_SCENES_ROOT set) and a fake
backend for local development. Fake rows carry `fake: True` — the agent should
never hand placeholder paths to run_sr.
"""

from __future__ import annotations

import os
from datetime import datetime

from backend.services import scene_search as svc
from .contract import err, ok, tool

_DESC = (
    "只读检索 SR_SCENES_ROOT 下的盘阵场景库，适用于明确的场景搜索请求。"
    "当前 Viewer 本地或离线影像的信息应使用 get_current_image_info；检索零匹配不代表当前影像不存在。"
    "各条件同时生效，按日期及 id 降序返回；scanned 是库中场景数，count 是匹配总数，results 最多 limit 条。"
    "场景行含 id、path（影像文件路径）、satellite、sensor、date、size_bytes 和 fake，元数据解析不到时可为空。"
    "未配置有效场景根目录时返回 source=fake 的测试数据；fake 路径仅供测试，不得传给 run_sr。"
)


@tool(
    name="search_scenes",
    description=_DESC,
    params_schema={
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
        },
        "required": [],
    },
)
def run_search_scenes(**params) -> dict:
    try:
        query = params.get("query", "")
        satellite = params.get("satellite")
        date_from = params.get("date_from")
        date_to = params.get("date_to")
        limit = int(params.get("limit", 20))
    except (KeyError, TypeError, ValueError) as e:
        return err(f"bad params: {e}")

    if not (1 <= limit <= 500):
        return err("limit must be in 1..500")
    for name, value in (("date_from", date_from), ("date_to", date_to)):
        if value:
            try:
                datetime.strptime(str(value), "%Y-%m-%d")
            except ValueError:
                return err(f"{name} must be 'YYYY-MM-DD'")

    try:
        result = svc.search_scenes(
            os.environ.get("SR_SCENES_ROOT"), query=query, satellite=satellite,
            date_from=date_from, date_to=date_to, limit=limit)
    except Exception as e:  # noqa: BLE001 — contract boundary
        return err(f"{type(e).__name__}: {e}")
    return ok(result)
