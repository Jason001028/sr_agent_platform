"""Agent tool library.

Importing this package registers every tool in the registry. Tools are
workflow-level operations (run_sr, batch run, scene search, cv repair) — NOT
internal building blocks like the MTA-Grid planner (that lives in mta_grid/ and
is composed by the run_sr service). No tools registered yet; the first real
tool will be a workflow-level one.
"""

from .contract import Tool, err, list_tools, manifest, ok, tool

__all__ = ["Tool", "tool", "ok", "err", "list_tools", "manifest"]
