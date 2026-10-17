"""Agent tool library and built-in registry factory."""

from . import fix_bad_lines  # noqa: F401 — importing registers the tool
from . import run_sr  # noqa: F401 — importing registers the tool
from . import search_scenes  # noqa: F401 — importing registers the tool
from . import sr_job_status  # noqa: F401 — importing registers the tool
from .contract import (Tool, ToolRegistry, default_registry, err, list_tools,
                       manifest, ok, tool)


def build_builtin_registry() -> ToolRegistry:
    """Return an independent registry containing the built-in tools."""
    return default_registry()


__all__ = ["Tool", "ToolRegistry", "tool", "ok", "err", "list_tools",
           "manifest", "default_registry", "build_builtin_registry"]
