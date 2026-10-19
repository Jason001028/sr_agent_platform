"""Plugin catalog and per-machine desired state.

The first implementation treats the existing Agent tools as built-in plugins.
The catalog is deliberately separate from ``backend.tools.contract``: the
registry remains the execution mechanism, while this module owns machine
assignment, desired state and operation records. A remote machine Agent can
consume the same operation queue without the API process executing arbitrary
remote commands.
"""

from __future__ import annotations

import re
import uuid

from .store import Store

LOCAL_MACHINE_ID = "local"
MACHINE_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,80}$")

BUILTIN_PLUGINS = (
    {
        "plugin_id": "search_scenes",
        "name": "场景检索",
        "version": "1.0.0",
        "description": "检索盘阵场景并返回影像元数据。",
        "entrypoint": "backend.tools.search_scenes:run_search_scenes",
    },
    {
        "plugin_id": "run_sr",
        "name": "超分任务",
        "version": "1.0.0",
        "description": "提交遥感影像超分任务。",
        "entrypoint": "backend.tools.run_sr:run_run_sr",
    },
    {
        "plugin_id": "sr_job_status",
        "name": "任务状态",
        "version": "1.0.0",
        "description": "查询超分任务状态。",
        "entrypoint": "backend.tools.sr_job_status:run_sr_job_status",
    },
    {
        "plugin_id": "fix_bad_lines",
        "name": "坏线修复",
        "version": "1.0.0",
        "description": "修复遥感影像坏线。",
        "entrypoint": "backend.tools.fix_bad_lines:run_fix_bad_lines",
    },
    {
        "plugin_id": "token_visualizer",
        "name": "Token 可视化",
        "version": "1.0.0",
        "description": "展示当前 Agent 会话的输入、输出和总 token 用量。",
        "entrypoint": "frontend:chat-token-usage",
        "metadata": {"kind": "observability", "route": "/chat"},
    },
)

VALID_ACTIONS = {"install", "enable", "disable", "uninstall"}
ACTION_STATE = {
    "install": "enabled",
    "enable": "enabled",
    "disable": "disabled",
    "uninstall": "uninstalled",
}


def validate_machine_id(machine_id: str) -> str:
    value = str(machine_id or "").strip()
    if not MACHINE_ID_RE.fullmatch(value):
        raise ValueError("machine_id must contain only letters, digits, _, ., :, or -")
    return value


def sync_catalog(store: Store) -> None:
    for p in BUILTIN_PLUGINS:
        store.upsert_plugin(**p)


def ensure_machine(store: Store, machine_id: str, *, name: str | None = None,
                   status: str = "online", metadata: dict | None = None,
                   default_state: str = "disabled") -> dict:
    machine_id = validate_machine_id(machine_id)
    machine = store.upsert_machine(machine_id, name=name, status=status,
                                   metadata=metadata)
    for p in BUILTIN_PLUGINS:
        if store.get_machine_plugin(machine_id, p["plugin_id"]) is None:
            initial = "enabled" if machine_id == LOCAL_MACHINE_ID else default_state
            store.set_machine_plugin(
                machine_id, p["plugin_id"], desired_state=initial,
                actual_state=initial, version=p["version"])
    return machine


def machine_id_from_header(value: str | None) -> str:
    return validate_machine_id(value or LOCAL_MACHINE_ID)


def enabled_plugin_ids(store: Store, machine_id: str) -> set[str]:
    rows = store.list_machine_plugins(machine_id)
    return {r["plugin_id"] for r in rows
            if r["desired_state"] == "enabled"
            and r["actual_state"] == "enabled"}


def request_operation(store: Store, machine_id: str, plugin_id: str, *,
                      action: str, version: str | None = None) -> dict:
    machine_id = validate_machine_id(machine_id)
    if action not in VALID_ACTIONS:
        raise ValueError(f"unsupported plugin action: {action}")
    plugin = store.get_plugin(plugin_id)
    if plugin is None:
        raise ValueError(f"unknown plugin: {plugin_id}")
    if store.get_machine(machine_id) is None:
        ensure_machine(store, machine_id)
    target = ACTION_STATE[action]
    selected_version = version or plugin["version"]
    store.set_machine_plugin(machine_id, plugin_id,
                             desired_state=target, version=selected_version)
    op = store.create_plugin_operation(
        machine_id, plugin_id, action=action, target_state=target,
        version=selected_version, request_id=uuid.uuid4().hex)
    # Built-ins on the API host are already present. Apply their logical state
    # immediately; remote machines remain PENDING for their Agent to claim.
    if machine_id == LOCAL_MACHINE_ID:
        store.set_machine_plugin(machine_id, plugin_id,
                                 desired_state=target,
                                 actual_state=target,
                                 version=selected_version)
        store.finish_plugin_operation(
            op["operation_id"], status="COMPLETED", actual_state=target,
            result={"mode": "in_process", "version": selected_version})
    return store.get_plugin_operation(op["operation_id"])  # type: ignore[return-value]


def catalog_with_machine_state(store: Store, machine_id: str) -> list[dict]:
    ensure_machine(store, machine_id)
    states = {r["plugin_id"]: r for r in store.list_machine_plugins(machine_id)}
    out = []
    for plugin in store.list_plugins():
        row = dict(plugin)
        row.update(states.get(plugin["plugin_id"], {
            "machine_id": machine_id, "desired_state": "disabled",
            "actual_state": "unknown", "version": None,
            "last_error": None, "updated_at": None,
        }))
        out.append(row)
    return out
