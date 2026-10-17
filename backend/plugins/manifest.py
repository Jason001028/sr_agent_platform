"""Static plugin manifest contract."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PluginManifest:
    id: str
    name: str
    version: str
    host_api_version: str
    enabled: bool
    backend_entry: str | None = None
    frontend_entry: str | None = None


def load_manifest(path: str | Path) -> PluginManifest:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("插件清单必须是 JSON 对象")
    required = ("id", "name", "version", "host_api_version", "enabled")
    missing = [key for key in required if key not in data]
    if missing:
        raise ValueError("插件清单缺少：" + ", ".join(missing))
    if not all(isinstance(data[key], str) and data[key] for key in required[:-1]):
        raise ValueError("插件清单的 id/name/version/host_api_version 必须是非空字符串")
    if not isinstance(data["enabled"], bool):
        raise ValueError("插件清单的 enabled 必须是布尔值")
    return PluginManifest(
        id=data["id"], name=data["name"], version=data["version"],
        host_api_version=data["host_api_version"], enabled=data["enabled"],
        backend_entry=data.get("backend_entry"), frontend_entry=data.get("frontend_entry"),
    )
