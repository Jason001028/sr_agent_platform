"""Expose the local-executor restart acceptance gap without using SR/GPU.

This is a safety regression, not a passing autonomous-restart acceptance:
losing the in-memory process record blocks the Goal even if a real child is
still running and later writes a successful verdict.
"""

import asyncio
import json
import os
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest import mock

from backend.services import goal_worker, local_exec, run_sr
from backend.services.store import Store
from backend.tools.contract import ToolRegistry


class LocalRestartAcceptanceGap(unittest.IsolatedAsyncioTestCase):
    async def test_running_child_becomes_unknown_and_goal_stays_blocked_after_completion(
        self,
    ):
        if not local_exec.available():
            self.skipTest("a real Bash is required")
        with tempfile.TemporaryDirectory() as td, mock.patch.dict(
            os.environ,
            {
                "SR_EXECUTOR": "local",
                "SR_SLURM_WORK_DIR": str(Path(td) / "work"),
                "SR_BUNDLE_DIR": td,
                "SR_PYTHON": "",
                "SR_GOAL_POLL_SEC": "0",
            },
        ):
            root = Path(td)
            scene = root / "scene"
            scene.mkdir()
            started, release = root / "started", root / "release"
            debug = scene / "Debug"
            verdict = debug / "_SREXIT_70001.txt"
            script = root / "job.sh"
            script.write_text(
                f'#!/bin/bash\nprintf "physical-start\\n" >> "{started.as_posix()}"\n'
                f'while [ ! -f "{release.as_posix()}" ]; do sleep 0.02; done\n'
                f'mkdir -p "{debug.as_posix()}"\n'
                f'printf "job_id=70001\\nsr_exit_code=0\\nverdict=0\\nskip=0\\n" > "{verdict.as_posix()}"\n',
                encoding="utf-8",
            )
            config = root / "job.xml"
            config.write_text(
                f"<SFSR_Config><DatarootLQ>{scene.as_posix()}</DatarootLQ></SFSR_Config>",
                encoding="utf-8",
            )
            store = Store(root / "goals.db")
            local_exec._reset()
            process = None
            try:
                local_exec.submit(script, job_id=70001)
                deadline = time.monotonic() + 4
                while not started.exists() and time.monotonic() < deadline:
                    await asyncio.sleep(0.02)
                self.assertTrue(
                    started.exists(), "real Bash did not start the fixture script"
                )
                process = local_exec._JOBS[70001]["proc"]
                self.assertIsNone(process.poll())
                task = {"job_id": 70001, "config_xml": str(config)}
                self.assertEqual(
                    run_sr.query_job_status(70001, task=task)["state"], "RUNNING"
                )
                store.put_sr_task(
                    "local-restart-gap",
                    {"lq_path": str(scene)},
                    job_id=70001,
                    status="RUNNING",
                    config_xml=str(config),
                )
                goal = store.create_goal(
                    title="Local restart acceptance",
                    checkpoint={"acceptance": {"min_sr_jobs": 1}},
                )
                sid = store.create_session()
                store.set_goal_session(goal["goal_id"], sid)
                store.transition_goal(
                    goal["goal_id"],
                    "WAITING_EXTERNAL",
                    checkpoint={**goal["checkpoint"], "external_job_id": 70001},
                )

                # This models the exact loss of _JOBS on an API process restart;
                # the operating-system child is deliberately kept alive.
                local_exec._reset()
                unknown = run_sr.query_job_status(70001, task=task)
                self.assertEqual(unknown["state"], "UNKNOWN")
                self.assertIsNone(process.poll())
                state = SimpleNamespace(store=store, tool_registry=ToolRegistry())
                await goal_worker.goal_tick(state)
                blocked = store.get_goal(goal["goal_id"])
                self.assertEqual(blocked["status"], "BLOCKED")
                before = store.list_goal_events(goal["goal_id"])
                self.assertTrue(
                    any(e["event_type"] == "external_job_unknown" for e in before)
                )
                release.touch()
                await asyncio.to_thread(process.wait, 5)
                self.assertTrue(verdict.exists())
                completed = run_sr.query_job_status(70001, task=task)
                self.assertEqual(completed["state"], "COMPLETED")
                await goal_worker.goal_tick(state)
                self.assertEqual(store.get_goal(goal["goal_id"])["status"], "BLOCKED")
                self.assertEqual(store.list_goal_events(goal["goal_id"]), before)
                self.assertEqual(started.read_text().splitlines(), ["physical-start"])
                self.assertEqual(store.get_goal(goal["goal_id"])["model_calls_used"], 0)
                destination = os.environ.get("P0_TEST_EVIDENCE_DIR")
                if destination:
                    folder = Path(destination)
                    folder.mkdir(parents=True, exist_ok=True)
                    (folder / "local_restart_gap.json").write_text(
                        json.dumps(
                            {
                                "acceptance": "NOT_PASSED_AUTOMATIC_LOCAL_RESTART_CONTINUATION",
                                "fixture": "real Bash child; simulated loss of API in-memory registry; no production SR",
                                "state_after_registry_loss": unknown,
                                "state_after_child_completion": completed,
                                "goal_after_child_completion": store.get_goal(
                                    goal["goal_id"]
                                ),
                                "events": before,
                                "physical_starts": 1,
                            },
                            ensure_ascii=False,
                            indent=2,
                        ),
                        encoding="utf-8",
                    )
            finally:
                release.touch()
                if process is None and 70001 in local_exec._JOBS:
                    process = local_exec._JOBS[70001]["proc"]
                if process is not None:
                    if process.poll() is None:
                        local_exec._terminate(process)
                    await asyncio.to_thread(process.wait, 5)
                local_exec._reset()
                store.close()


if __name__ == "__main__":
    unittest.main()
