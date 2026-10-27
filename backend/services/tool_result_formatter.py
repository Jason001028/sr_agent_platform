"""Bounded model-only tool views. Execution results are never mutated here."""
from __future__ import annotations

import copy
import json
import os
from dataclasses import dataclass

from backend.services.trajectory import _redact

META = "_tool_result_budget"
# These values are copied verbatim, including nested contract/verdict records.
CRITICAL = frozenset({"ok", "error", "error_class", "control", "job_id", "state", "status",
    "active", "exit_code", "verdict", "skip", "reason", "reasons", "contract", "idempotent",
    "reuse", "fingerprint", "artifacts", "outputs", "output_path", "config_xml", "batch_script",
    "log_dir", "source", "count", "scanned", "total", "query", "filters", "pagination",
    "offset", "next_offset", "has_more", "result_id", "path", "keys"})


def encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def size(value):
    return len(encode(value).encode("utf-8"))


@dataclass(frozen=True)
class ResultBudget:
    max_bytes: int = 12000
    max_tokens: int = 4000
    scene_items: int = 12
    archive_bytes: int = 8 * 1024 * 1024
    archive_total_bytes: int = 256 * 1024 * 1024
    retention_days: int = 30

    def __post_init__(self):
        if self.limit < 1024 or min(self.scene_items, self.archive_bytes,
                                  self.archive_total_bytes, self.retention_days) < 1:
            raise ValueError("tool result budgets must allow >=1024 bytes/tokens and positive archive limits")

    @property
    def limit(self):
        # Conservative byte-level upper bound, NOT a claim of measured tokens.
        return min(self.max_bytes, self.max_tokens)

    @classmethod
    def from_env(cls):
        return cls(**{key: int(os.environ.get("SR_TOOL_RESULT_" + key.upper(), default))
                      for key, default in cls().__dict__.items()})


def _critical(value, path="", depth=0, extra=()):
    out = {}
    if isinstance(value, dict):
        for key, item in value.items():
            pointer = path + "/" + key.replace("~", "~0").replace("/", "~1")
            if key in CRITICAL or key in extra:
                out[pointer] = item
            elif isinstance(item, dict):
                out.update(_critical(item, pointer, depth + 1, extra))
            elif isinstance(item, (list, tuple)):
                # Arrays containing business verdicts are atomic; never omit a
                # failed/unknown element while presenting the rest as complete.
                pending = list(item)
                while pending:
                    child = pending.pop()
                    if isinstance(child, dict):
                        if any(k in CRITICAL or k in extra for k in child):
                            out[pointer] = item
                            break
                        pending.extend(child.values())
                    elif isinstance(child, (list, tuple)):
                        pending.extend(child)
    return out


def _text(text, count):
    raw = text.encode("utf-8")
    if len(raw) <= count:
        return text
    lines = text.splitlines()
    important = [line for line in lines if any(word in line.upper() for word in ("ERROR", "WARNING", "FATAL"))]
    snippet = "\n".join(important[:4] + lines[:2] + lines[-4:])
    # Include both ends even for a single extremely long line. Never split UTF-8.
    sample = snippet.encode("utf-8")
    half = max(0, (count - 40) // 2)
    head = sample[:half].decode("utf-8", "ignore")
    tail = sample[-half:].decode("utf-8", "ignore") if half else ""
    return {"head": head, "tail": tail,
            "omitted_bytes_at_least": max(0, len(raw) - len(head.encode("utf-8")) - len(tail.encode("utf-8"))),
            "original_bytes": len(raw), "original_lines": len(lines), "truncated": True}


def _clip(value, width, text_bytes, depth=0):
    if depth > 12:
        return {"omitted": "depth_limit"}
    if isinstance(value, dict):
        return {k: _clip(v, width, text_bytes, depth + 1)
                for k, v in list(value.items())[:width]}
    if isinstance(value, (list, tuple)):
        return [_clip(v, width, text_bytes, depth + 1) for v in value[:width]]
    if isinstance(value, str):
        return _text(value, text_bytes)
    return value


def format_result(name, result, *, budget=None, reference=None, arguments=None, tool=None):
    """Return valid JSON data within budget, or explicit critical-field overflow.

    Short results keep the existing ok/data/error protocol byte-for-byte in meaning.
    Long results always declare that the model view is incomplete.
    """
    budget = budget or ResultBudget()
    limit = min(budget.limit, getattr(tool, "result_max_bytes", None) or budget.limit)
    if limit < 1024:
        raise ValueError("tool result_max_bytes must be >=1024")
    safe = _redact(result)
    if not isinstance(safe, dict):
        safe = {"ok": True, "data": safe, "error": None}
    original_bytes = size(safe)
    scene_limit = (name == "search_scenes" and isinstance(safe.get("data"), dict)
                   and isinstance(safe["data"].get("results"), list)
                   and len(safe["data"]["results"]) > budget.scene_items)
    if original_bytes <= limit and not scene_limit:
        return safe
    metadata = {"version": 1, "truncated": True, "result_type": type(safe.get("data")).__name__, "original_bytes": original_bytes,
                "byte_limit": limit, "token_estimate": "utf8_bytes_upper_bound",
                "full_result": reference or {"available": False, "reason": "no_result_store"},
                "read_tool": "read_tool_result", "tool": name}
    critical_input = safe
    if name == "search_scenes" and isinstance(safe.get("data"), dict):
        critical_input = {**safe, "data": {k:v for k,v in safe["data"].items() if k != "results"}}
    critical = _critical(critical_input, extra=getattr(tool, "result_protected_fields", ()))
    candidate = safe
    custom = getattr(tool, "result_formatter", None)
    if custom:
        try:
            # Validate the complete return value before it enters protection,
            # clipping or final serialization. Round-trip also removes aliases
            # and guarantees a plain, acyclic JSON tree for those later steps.
            candidate = json.loads(encode(custom(copy.deepcopy(safe))))
            size(candidate)  # Includes UTF-8 encoding, not only JSON encoding.
            candidate = _redact(candidate)
        except Exception as exc:
            metadata["formatter_error"] = type(exc).__name__
            candidate = safe
    if name == "search_scenes" and isinstance(safe.get("data"), dict):
        candidate = copy.deepcopy(safe)
        data = candidate["data"]
        rows = data.get("results", [])
        if isinstance(rows, list):
            visible = rows[:budget.scene_items]
            # Preserve the backend's deterministic order and complete selected rows.
            data["results"] = visible
            data["pagination"] = {"available_in_result": len(rows), "shown": len(visible),
                "omitted": len(rows) - len(visible), "has_more": len(rows) > len(visible),
                "archive_total": data.get("count", len(rows)),
                "next": {"path": "/data/results", "offset": len(visible), "limit": budget.scene_items},
                "outside_result": max(0, data.get("count", len(rows)) - len(rows)),
                "note": "Read the saved result for returned rows; narrow the search for archive matches beyond the original limit."}
            metadata["query"] = arguments or {}
    # Protect critical values in place, regardless of their position in the result.
    def protect(view):
        for pointer, value in critical.items():
            parts = pointer.split('/')[1:]
            cursor = view
            for part in parts[:-1]:
                part = part.replace('~1', '/').replace('~0', '~')
                if not isinstance(cursor.get(part), dict):
                    cursor[part] = {}
                cursor = cursor[part]
            cursor[parts[-1].replace('~1', '/').replace('~0', '~')] = copy.deepcopy(value)
        return view
    if isinstance(candidate, dict):
        candidate = protect(candidate)
        candidate[META] = metadata
        if size(candidate) <= limit:
            return candidate
    for width, text_bytes in ((16, 800), (8, 400), (4, 200), (1, 80), (0, 0)):
        view = _clip(candidate, width, text_bytes)
        if not isinstance(view, dict):
            view = {"data": view}
        view = protect(view)
        view[META] = metadata
        if size(view) <= limit:
            # Selected scene rows must retain IDs and metadata without partial records.
            if name == "search_scenes":
                rows = ((candidate.get("data") or {}).get("results") or [])
                shown = []
                view.setdefault("data", {})["results"] = shown
                page = copy.deepcopy((candidate.get("data") or {}).get("pagination", {}))
                view["data"]["pagination"] = page
                for row in rows:
                    shown.append(row)
                    page.update(shown=len(shown), omitted=page.get("available_in_result", 0)-len(shown),
                                has_more=page.get("available_in_result", 0)>len(shown))
                    page["next"]["offset"] = len(shown)
                    if size(view) > limit:
                        shown.pop()
                        break
                page.update(shown=len(shown), omitted=page.get("available_in_result", 0)-len(shown),
                            has_more=page.get("available_in_result", 0)>len(shown))
                page["next"]["offset"] = len(shown)
                if size(view) > limit:
                    continue
            return view
    # Never silently crop a business verdict/error or mislabel execution as failed.
    metadata.pop("query", None)
    metadata["budget_error"] = "critical_fields_exceed_budget"
    metadata["message"] = "Critical values are not shown; inspect the saved result. Do not infer success or resubmit."
    view = {"ok": safe.get("ok") if isinstance(safe, dict) else None,
            "data": None, "error": None, META: metadata}
    if size(view) > limit:
        raise ValueError("result reference exceeds tool budget")
    return view
