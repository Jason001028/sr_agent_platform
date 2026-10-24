"""Subprocess entry point for Goal acceptance; all external services are fake.

Only the test transport adapters live here. The child runs the real FastAPI
app, OpenAI client, Loop, Store, SR submission service and Goal worker.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
from urllib.request import Request, urlopen

from backend.api.app import create_app
from backend.services import goal_worker, run_sr
from backend.tools import build_builtin_registry
from backend.tools.contract import Tool, ToolRegistry, err, ok


def external(base, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    request = Request(
        base + path, data=data, headers={"Content-Type": "application/json"}
    )
    with urlopen(request, timeout=10) as response:
        return json.load(response)


def fixture_app(settings):
    base = settings["external_url"]

    def submit(script_path, **kwargs):
        receipt = external(
            base, "/submit", {"config_xml": str(Path(script_path).with_suffix(".xml"))}
        )
        if settings.get("crash_after_accept"):
            # The external server has fsynced its ledger and acknowledged the
            # request, but submit_run_sr has not yet received this return value.
            os._exit(86)
        return receipt["job_id"]

    run_sr.slurm.slurm_available = lambda: True
    run_sr.slurm.sbatch_submit = submit
    run_sr.query_job_status = lambda job_id, **kwargs: external(base, f"/jobs/{job_id}")
    app = create_app()
    builtins = build_builtin_registry()

    def report():
        tasks = app.state.store.list_sr_tasks()
        if len(tasks) != 2 or any(not t["job_id"] for t in tasks):
            return err("two durable SR receipts are required")
        output = Path(settings["report"])
        output.write_text(
            json.dumps({"job_ids": sorted(t["job_id"] for t in tasks)}),
            encoding="utf-8",
        )
        return ok({"report": str(output)})

    app.state.tool_registry = ToolRegistry(
        [
            Tool(
                "search_scenes",
                "Search test scenes",
                {"type": "object"},
                lambda: ok(
                    {
                        "source": "process-test",
                        "results": [
                            {"id": Path(p).name, "lq_path": p}
                            for p in settings["scenes"]
                        ],
                    }
                ),
            ),
            builtins.get("run_sr"),
            Tool(
                "finish_report",
                "Write the report after both SR jobs finish",
                {"type": "object"},
                report,
            ),
        ]
    )
    return app


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--settings", required=True)
    parser.add_argument("--mode", choices=("tick", "serve"), required=True)
    parser.add_argument("--port", type=int, default=0)
    args = parser.parse_args()
    settings = json.loads(Path(args.settings).read_text(encoding="utf-8"))
    app = fixture_app(settings)
    if args.mode == "serve":
        import uvicorn

        uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
    else:
        try:
            asyncio.run(goal_worker.goal_tick(app.state))
        finally:
            app.state.store.close()


if __name__ == "__main__":
    main()
