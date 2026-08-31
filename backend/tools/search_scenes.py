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
    "Search the satellite-scene archive (intranet disk array) for images. "
    "Returns scene metadata (id, path, satellite, sensor, date). "
    "When source is 'fake' the paths are placeholders for local testing only "
    "and must not be passed to run_sr."
)


@tool(
    name="search_scenes",
    description=_DESC,
    params_schema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Free-text substring on scene id/path, e.g. 'GF07'.",
            },
            "satellite": {
                "type": "string",
                "description": "Satellite id substring, e.g. 'GF07A03'.",
            },
            "date_from": {
                "type": "string",
                "description": "Inclusive lower bound, 'YYYY-MM-DD'.",
            },
            "date_to": {
                "type": "string",
                "description": "Inclusive upper bound, 'YYYY-MM-DD'.",
            },
            "limit": {
                "type": "integer", "minimum": 1, "maximum": 500, "default": 20,
                "description": "Max results to return.",
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
