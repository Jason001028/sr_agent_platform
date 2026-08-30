"""Agent tool contract + registry.

A tool is a thin, JSON-serializable function the agent (and later the REST
layer) can call. Every tool must expose:

  name          — unique snake_case identifier
  description   — one-line "when to use" hint for the LLM
  params_schema — JSON Schema describing the keyword arguments
  run(**kwargs) — returns {"ok": bool, "data": ..., "error": str|None}

The registry auto-collects every function decorated with @tool; manifest()
emits the OpenAI-compatible function-calling schema (tools[].function), which
is consumed verbatim by the agent loop and reused to generate REST endpoints.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass
class Tool:
    name: str
    description: str
    params_schema: dict
    run: Callable[..., dict]

    def openai_function(self) -> dict:
        """OpenAI-compatible function-calling entry (tools[].function)."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.params_schema,
            },
        }

    def manifest_entry(self) -> dict:
        """Flat manifest entry (for API docs / introspection)."""
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.params_schema,
        }


_REGISTRY: dict[str, Tool] = {}


def tool(name: str, description: str, params_schema: dict) -> Callable:
    """Decorator that registers a function as an agent-callable tool."""

    def decorator(fn: Callable[..., dict]) -> Callable[..., dict]:
        _REGISTRY[name] = Tool(name=name, description=description,
                               params_schema=params_schema, run=fn)
        return fn

    return decorator


def list_tools() -> list[Tool]:
    return list(_REGISTRY.values())


def manifest() -> list[dict]:
    """OpenAI-compatible tools array, ready to hand to the agent loop."""
    return [t.openai_function() for t in _REGISTRY.values()]


def ok(data: Any) -> dict:
    return {"ok": True, "data": data, "error": None}


def err(message: str) -> dict:
    return {"ok": False, "data": None, "error": message}
