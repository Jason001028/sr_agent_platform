"""fix_bad_lines — first real workflow-level agent tool (thin wrapper).

Business logic lives in backend/services/fix_bad_lines.py; this module is the
agent-facing shell: LLM description + JSON-schema params (the contract) and
the ok/err boundary. Contract errors never raise — the loop feeds err() back
to the model for self-healing (see agent/loop.py).
"""

from __future__ import annotations

from backend.services import fix_bad_lines as svc
from .contract import err, ok, tool


@tool(
    name="fix_bad_lines",
    description=(
        "Repair bad detector lines / striping in a satellite TIFF: detect rows or "
        "columns whose intensity is a dead/stuck outlier vs their good neighbors "
        "and replace each with the per-column median of neighboring good lines. "
        "Returns the fixed line indices; writes output_path if given."
    ),
    params_schema={
        "type": "object",
        "properties": {
            "input_path": {
                "type": "string",
                "description": "Path to the input TIFF/image.",
            },
            "output_path": {
                "type": "string",
                "description": "Optional output path; omitted → stats only, no file written.",
            },
            "axis": {
                "type": "string", "enum": ["row", "column"], "default": "row",
                "description": "Orientation of the bad lines: row = horizontal (whole rows are bad).",
            },
            "window": {
                "type": "integer", "minimum": 1, "default": 5,
                "description": "Half-width of the good-neighbor window used for reference stats.",
            },
            "threshold": {
                "type": "number", "minimum": 0, "default": 3.0,
                "description": "Robust z-score above which a line is flagged bad.",
            },
        },
        "required": ["input_path"],
    },
)
def run_fix_bad_lines(**params) -> dict:
    try:
        input_path = str(params["input_path"])
        output_path = params.get("output_path")
        axis = params.get("axis", "row")
        window = int(params.get("window", 5))
        threshold = float(params.get("threshold", 3.0))
    except (KeyError, TypeError, ValueError) as e:
        return err(f"bad params: {e}")

    if axis not in ("row", "column"):
        return err("axis must be 'row' or 'column'")
    if window < 1:
        return err("window must be >= 1")
    if threshold < 0:
        return err("threshold must be >= 0")

    try:
        stats = svc.run(input_path, output_path=output_path, axis=axis,
                        window=window, threshold=threshold)
    except FileNotFoundError:
        return err(f"input not found: {input_path}")
    except Exception as e:  # noqa: BLE001 — contract boundary: tool never raises
        return err(f"{type(e).__name__}: {e}")
    return ok(stats)
