"""Agent tool contract and registry."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable, Iterable

from jsonschema.validators import validator_for


@dataclass
class Tool:
    name: str
    description: str
    params_schema: dict
    run: Callable[..., dict]
    version: str = "1"
    visibility: str = "public"
    timeout_sec: float | None = None
    permissions: tuple[str, ...] = ()
    result_max_bytes: int | None = None
    result_formatter: Callable | None = None
    result_protected_fields: tuple[str, ...] = ()

    def openai_function(self) -> dict:
        """OpenAI-compatible function-calling entry."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.params_schema,
            },
        }

    def manifest_entry(self) -> dict:
        """Flat manifest entry for API introspection."""
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.params_schema,
            "version": self.version,
            "visibility": self.visibility,
            "permissions": list(self.permissions),
        }


class ToolRegistry:
    """独立的工具集合与统一执行边界。"""

    def __init__(self, tools: Iterable[Tool] = ()):
        self._tools: dict[str, Tool] = {}
        for item in tools:
            self.register(item)

    def register(self, item: Tool) -> Tool:
        if not isinstance(item, Tool):
            raise TypeError("只能注册 Tool 实例")
        if item.name in self._tools:
            raise ValueError(f"工具已注册：{item.name}")
        validator_for(item.params_schema).check_schema(item.params_schema)
        if item.result_max_bytes is not None and item.result_max_bytes < 1024:
            raise ValueError("result_max_bytes must be >=1024")
        self._tools[item.name] = item
        return item

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def list(self) -> list[Tool]:
        return list(self._tools.values())

    def manifest(self) -> list[dict]:
        return [item.openai_function() for item in self._tools.values()]

    def validate(self, name: str, arguments=None) -> dict | None:
        """Return a safe diagnostic without executing or coercing arguments."""
        item = self.get(name)
        if item is None:
            return err(f"unknown tool: {name}")
        try:
            if arguments is None or arguments == "":
                payload = {}
            elif isinstance(arguments, (str, bytes, bytearray)):
                payload = json.loads(arguments)
            else:
                payload = arguments
        except (ValueError, TypeError):
            return validation_error("json_parse", "$", "JSON", "Provide a complete JSON object.")
        if not isinstance(payload, dict):
            return validation_error("schema_validation", "$", "type", "Provide a JSON object.")
        validator = validator_for(item.params_schema)(item.params_schema)
        failure = next(validator.iter_errors(payload), None)
        if failure is not None:
            path = list(failure.absolute_path)
            if failure.validator == "required" and isinstance(failure.instance, dict):
                path.extend(k for k in failure.validator_value if k not in failure.instance)
            field = "/" + "/".join(str(p) for p in path)
            return validation_error("schema_validation", field, failure.validator,
                                    "Correct this field to match the tool schema.")
        return None

    def invoke(self, name: str, arguments=None, *, source: str = "unknown") -> dict:
        """统一执行工具并返回稳定的 ok/data/error 结果。"""
        item = self.get(name)
        if item is None:
            return err(f"unknown tool: {name}")
        failure = self.validate(name, arguments)
        if failure is not None:
            return failure
        try:
            if arguments is None or arguments == "":
                payload = {}
            elif isinstance(arguments, (str, bytes, bytearray)):
                payload = json.loads(arguments)
            else:
                payload = arguments
            if not isinstance(payload, dict):
                return err("tool arguments must be a JSON object")
            from backend.services.goal_lease import current_lease, current_tool, LeaseLost
            lease, execution = current_lease.get(), current_tool.get()
            if lease is not None and execution is not None:
                lease.check(execution["store"])
            result = item.run(**payload)
            if not isinstance(result, dict) or not {"ok", "data", "error"}.issubset(result):
                return err(f"工具 {name} 返回了无效结果")
            return result
        except Exception as exc:  # noqa: BLE001 — contract boundary
            from backend.services.goal_lease import LeaseLost
            if isinstance(exc, LeaseLost):
                raise
            return err(f"{type(exc).__name__}: {exc}")


_REGISTRY: dict[str, Tool] = {}


def tool(name: str, description: str, params_schema: dict) -> Callable:
    """Decorator that registers a function as an agent-callable tool."""

    def decorator(fn: Callable[..., dict]) -> Callable[..., dict]:
        validator_for(params_schema).check_schema(params_schema)
        _REGISTRY[name] = Tool(name=name, description=description,
                               params_schema=params_schema, run=fn)
        return fn

    return decorator


def list_tools() -> list[Tool]:
    return list(_REGISTRY.values())


def manifest() -> list[dict]:
    """OpenAI-compatible tools array, ready to hand to the agent loop."""
    return [t.openai_function() for t in _REGISTRY.values()]


def default_registry() -> ToolRegistry:
    """构造当前内置工具的独立注册表快照。"""
    return ToolRegistry(list(_REGISTRY.values()))


def ok(data: Any) -> dict:
    return {"ok": True, "data": data, "error": None}


def err(message: str) -> dict:
    return {"ok": False, "data": None, "error": message}


def validation_error(category, field, constraint, message):
    result = err(message)
    result["diagnostic"] = {"category": category, "field": field,
                            "constraint": constraint, "message": message}
    return result
