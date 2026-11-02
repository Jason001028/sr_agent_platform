"""PR C: deterministic batch Producer/Monitor, recovery and isolation.

All tests use tiny real TIFF fixtures, real Mask IO and the in-memory fake
scheduler.  No GPU, no production SR process and no disk-array scan is reached.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest import mock

from PIL import Image

from backend.services import (
    batch_detector, batch_manifest, batch_mask, batch_worker,
    run_sr as sr_service, slurm)
from backend.services.goal_runtime import _batch_item_checkpoint
from backend.services.store import Store
from backend.tests import allowed_roots_env

BASE = "JXGF07D03_PMS_20260101010101_200516571_101_{scene:04d}_001_L1"
DESC = "模糊 问题类型:模糊 影像类型:pan 行列号:12,34"


class BatchWorkerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "work").mkdir()
        env = {
            **allowed_roots_env(self.root),
            "SR_SCENE_PATH_TEMPLATE": str(self.root) + "/{name}",
            "SR_AGENT_DB": str(self.root / "test.db"),
            "SR_BATCH_ARTIFACT_DIR": str(self.root / "artifacts"),
            "SR_SLURM_FAKE": "1",
            "SR_SLURM_FAKE_T_MS": "0",
            "SR_EXECUTOR": "slurm",
            "SR_SLURM_WORK_DIR": str(self.root / "work"),
            "SR_BATCH_MONITOR_POLL_SEC": "0.01",
            "SR_BATCH_IO_TIMEOUT_SEC": "10",
        }
        self.env = mock.patch.dict(os.environ, env)
        self.env.start()
        slurm._fake_reset()
        self.store = Store(self.root / "test.db")
        self.state = SimpleNamespace(store=self.store)
        self.submits: list[str] = []
        self.queries: list[int] = []
        self.sbatch_error = False
        self.status = {"active": False, "state": "COMPLETED",
                       "exit_code": "0:0"}
        self.sbatch_patch = mock.patch(
            "backend.services.slurm.sbatch_submit", side_effect=self._sbatch)
        self.sbatch_patch.start()
        self.query_patch = mock.patch(
            "backend.services.run_sr.query_job_status",
            side_effect=self._query)
        self.query_patch.start()

    def tearDown(self):
        io_executor = getattr(self.state, "batch_io_executor", None)
        if io_executor is not None:
            io_executor.shutdown(wait=True, cancel_futures=True)
        self.query_patch.stop()
        self.sbatch_patch.stop()
        self.store.close()
        self.env.stop()
        self.tmp.cleanup()

    def _sbatch(self, script, *args, **kwargs):
        self.submits.append(str(script))
        if self.sbatch_error:
            raise RuntimeError("simulated accepted-without-job-id")
        return len(self.submits)

    def _query(self, job_id, task=None, run_cmd=None):
        self.queries.append(int(job_id))
        return {"job_id": int(job_id), **self.status}

    # ---- fixtures ------------------------------------------------------
    def scene(self, index: int) -> str:
        stem = BASE.format(scene=index)
        directory = self.root / (stem + "_PAN")
        directory.mkdir()
        (directory / (directory.name + "_meta.xml")).write_text(
            "<meta><SolarAzimuth>1</SolarAzimuth></meta>", encoding="utf-8")
        Image.new("I;16", (16, 16), 7).save(directory / "PAN.tif")
        Image.new("I;16", (8, 8), 7).save(
            directory / (directory.name + "_NOSR.tif"))
        return stem

    def qc(self, *names: str) -> str:
        return "".join(f"{name}\t{DESC}\t张三\n" for name in names)

    def make_goal(self, count=5, *, mode="demo", exclusions=None,
                  frozen_config=None):
        names = [self.scene(i) for i in range(1, count + 1)]
        imported = batch_manifest.import_manifest(
            text=self.qc(*names), mode=mode)
        manifest = batch_manifest.load_manifest(imported["manifest_ref"])
        planned = batch_manifest.plan_goal_items(manifest, exclusions or [])
        checkpoint = {
            "workflow": batch_manifest.WORKFLOW,
            "manifest_ref": imported["manifest_ref"],
            "manifest_sha256": manifest["manifest_sha256"],
            "mode": mode,
            "exclusions": exclusions or [],
            "batch_control": {"pause_requested": False,
                              "cancel_requested": False},
            "producer_status": "DISPATCHING",
            "report_ref": None,
        }
        items = [{
            "item_id": item["item_id"],
            "ordinal": item["ordinal"],
            "name": item["name"],
            "status": item["status"],
            "checkpoint": _batch_item_checkpoint(item),
            "resource_key": (item.get("target_image") or {}).get("image_key"),
            "next_wake_at": time.time() if item["status"] == "PENDING" else None,
        } for item in planned]
        goal = self.store.create_batch_goal(
            title="batch", request_key="req-" + str(time.time_ns()),
            content_sha256=imported["manifest_ref"], checkpoint=checkpoint,
            items=items, max_model_calls=0)
        if frozen_config:
            original = batch_manifest.load_manifest

            def load(ref):
                frozen = dict(original(ref))
                frozen["config"] = {**frozen["config"], **frozen_config}
                return frozen

            self.load_patch = mock.patch(
                "backend.services.batch_worker.batch_manifest.load_manifest",
                side_effect=load)
            self.load_patch.start()
            self.addCleanup(self.load_patch.stop)
        return goal, manifest

    # ---- execution helpers --------------------------------------------
    def tick(self):
        asyncio.run(batch_worker.batch_tick(self.state))

    def run_until(self, predicate, *, ticks=300, delay=0.02):
        for index in range(ticks):
            self.tick()
            if predicate():
                return index + 1
            time.sleep(delay)
        self.fail(f"condition not reached in {ticks} ticks: {predicate}")

    def items(self, goal_id):
        return self.store.list_goal_items(goal_id)

    def statuses(self, goal_id):
        return {item["status"] for item in self.items(goal_id)}

    def make_retries_due(self, goal_id):
        self.store._db().execute(
            "UPDATE agent_goal_steps SET next_wake_at=0 "
            "WHERE goal_id=? AND kind='batch_item' AND status='RETRY_WAIT'",
            (goal_id,))
        self.store._db().commit()

    def make_monitor_due(self, goal_id):
        """Skip the 1/2/4s query backoff so a bounded-retry test stays fast."""
        self.store._db().execute(
            "UPDATE agent_goal_steps SET next_wake_at=0 "
            "WHERE goal_id=? AND kind='batch_item' "
            "AND status IN ('WAITING_SR','VERIFYING')",
            (goal_id,))
        self.store._db().commit()

    def _submitted_count(self):
        return self.store._db().execute(
            "SELECT COUNT(*) FROM sr_tasks WHERE job_id IS NOT NULL").fetchone()[0]

    # ---- acceptance ----------------------------------------------------
    def test_five_scene_pipeline_completes_without_model_calls(self):
        goal, manifest = self.make_goal(5)
        self.run_until(
            lambda: self.store.get_goal(goal["goal_id"])["status"] == "COMPLETED")
        final = self.store.get_goal(goal["goal_id"])
        self.assertEqual(final["phase"], "done")
        self.assertEqual(final["max_model_calls"], 0)
        self.assertEqual(final["model_calls_used"], 0)
        self.assertEqual(final["rounds_used"], 0)
        self.assertEqual(len(self.submits), 5)
        self.assertEqual(self.statuses(goal["goal_id"]), {"SUCCEEDED"})
        tasks = self.store._db().execute(
            "SELECT COUNT(DISTINCT fingerprint),COUNT(*) FROM sr_tasks").fetchone()
        self.assertEqual(tasks, (5, 5))
        report = final["checkpoint"]["report"]
        self.assertEqual(report["provenance"], "mock")
        self.assertEqual(report["counts"], {"SUCCEEDED": 5})
        self.assertTrue(final["checkpoint"]["report_ref"].endswith(".json"))
        self.assertTrue(Path(final["checkpoint"]["report_ref"]).is_file())

    def test_n_plus_one_starts_before_first_job_is_terminal(self):
        goal, _ = self.make_goal(3)
        self.status = {"active": True, "state": "RUNNING", "exit_code": None}
        self.run_until(lambda: len(self.submits) >= 2)
        statuses = [item["status"] for item in self.items(goal["goal_id"])]
        self.assertEqual(statuses[:2], ["WAITING_SR", "WAITING_SR"])
        self.assertNotEqual(
            self.store.get_goal(goal["goal_id"])["status"], "COMPLETED")
        self.status = {"active": False, "state": "COMPLETED",
                       "exit_code": "0:0"}
        self.run_until(
            lambda: self.store.get_goal(goal["goal_id"])["status"] == "COMPLETED")
        self.assertEqual(len(self.submits), 3)

    def test_restart_does_not_resubmit_an_accepted_job(self):
        goal, _ = self.make_goal(3)
        self.run_until(lambda: len(self.submits) == 1)
        first = self.items(goal["goal_id"])[0]
        self.assertEqual(first["status"], "WAITING_SR")
        # New worker state over the same durable Store == process restart.
        self.state = SimpleNamespace(store=self.store)
        self.status = {"active": False, "state": "COMPLETED",
                       "exit_code": "0:0"}
        self.run_until(
            lambda: self.store.get_goal(goal["goal_id"])["status"] == "COMPLETED")
        self.assertEqual(len(self.submits), 3)
        jobs = self.store._db().execute(
            "SELECT COUNT(*),COUNT(DISTINCT job_id) FROM sr_tasks").fetchone()
        self.assertEqual(jobs, (3, 3))

    def test_accepted_without_job_id_blocks_and_never_resubmits(self):
        goal, _ = self.make_goal(
            2, frozen_config={"max_inflight_sr_jobs": 1})
        self.sbatch_error = True
        self.run_until(
            lambda: any(i["status"] == "BLOCKED"
                        for i in self.items(goal["goal_id"])))
        blocked = [i for i in self.items(goal["goal_id"])
                   if i["status"] == "BLOCKED"][0]
        self.assertTrue(blocked["capacity_reserved"])
        self.assertEqual(len(self.submits), 1)
        self.assertEqual(self.store.count_reserved_slots(goal["goal_id"]), 1)
        # Restart, then many more passes: the ledger still cannot confirm a job
        # id, so the item stays parked and no second physical submit happens.
        self.state = SimpleNamespace(store=self.store)
        for _ in range(20):
            self.tick()
            time.sleep(0.01)
        self.assertEqual(len(self.submits), 1)
        self.assertEqual(self.store.count_reserved_slots(goal["goal_id"]), 1)
        self.assertEqual(
            [i["status"] for i in self.items(goal["goal_id"])][0], "BLOCKED")

    def test_capacity_deadlock_is_finalized_with_blocked_report(self):
        goal, _ = self.make_goal(
            2, frozen_config={"max_inflight_sr_jobs": 1})
        gid = goal["goal_id"]
        self.sbatch_error = True
        self.run_until(
            lambda: self.store.get_goal(gid)["status"] == "BLOCKED")
        final = self.store.get_goal(gid)
        self.assertEqual(final["phase"], "blocked")
        self.assertEqual(
            final["checkpoint"]["blocked_reason"], "capacity_deadlock")
        items = self.items(gid)
        self.assertEqual({i["status"] for i in items},
                         {"BLOCKED", "SKIPPED"})
        skipped = [i for i in items if i["status"] == "SKIPPED"][0]
        self.assertEqual(skipped["checkpoint"]["exclusion_reason"],
                         "capacity_deadlock")
        # The unknown submit keeps its slot: reconciliation is manual.
        self.assertEqual(self.store.count_reserved_slots(gid), 1)
        self.assertEqual(len(self.submits), 1)
        report = final["checkpoint"]["report"]
        self.assertEqual(report["counts"]["BLOCKED"], 1)
        self.assertEqual(report["counts"]["SKIPPED"], 1)
        self.assertTrue(Path(final["checkpoint"]["report_ref"]).is_file())
        types = [e["event_type"]
                 for e in self.store.list_goal_events(gid)]
        self.assertIn("batch_report_updated", types)
        for _ in range(5):
            self.tick()
            time.sleep(0.01)
        self.assertEqual(self.store.get_goal(gid)["status"], "BLOCKED")
        self.assertEqual(len(self.submits), 1)

    def test_reservation_without_side_effect_is_released_and_retried(self):
        goal, _ = self.make_goal(1)
        with mock.patch(
                "backend.services.run_sr.submit_run_sr",
                side_effect=RuntimeError("crash before external effect")):
            self.run_until(
                lambda: any(i["status"] == "MASK_READY"
                            for i in self.items(goal["goal_id"])))
        item = self.items(goal["goal_id"])[0]
        self.assertEqual(item["status"], "MASK_READY")
        self.assertFalse(item["capacity_reserved"])
        self.assertEqual(self.store.count_reserved_slots(goal["goal_id"]), 0)
        self.assertEqual(self.submits, [])
        self.run_until(
            lambda: self.store.get_goal(goal["goal_id"])["status"] == "COMPLETED")
        self.assertEqual(len(self.submits), 1)

    def test_pause_stops_producer_but_monitor_keeps_the_accepted_job(self):
        goal, _ = self.make_goal(2)
        self.status = {"active": True, "state": "RUNNING", "exit_code": None}
        self.run_until(lambda: len(self.submits) == 1)
        self.store.transition_goal(goal["goal_id"], "PAUSED")
        before = len(self.queries)
        for _ in range(10):
            self.tick()
            time.sleep(0.02)
        self.assertEqual(self.store.get_goal(goal["goal_id"])["status"], "PAUSED")
        self.assertEqual(len(self.submits), 1)
        self.assertGreater(len(self.queries), before)
        statuses = [i["status"] for i in self.items(goal["goal_id"])]
        self.assertEqual(statuses, ["WAITING_SR", "PENDING"])
        self.status = {"active": False, "state": "COMPLETED",
                       "exit_code": "0:0"}
        self.run_until(
            lambda: self.items(goal["goal_id"])[0]["status"] == "SUCCEEDED")
        final = self.store.get_goal(goal["goal_id"])
        self.assertEqual(final["status"], "PAUSED")
        self.assertTrue(final["checkpoint"]["monitoring_done"])
        self.assertEqual([i["status"] for i in self.items(goal["goal_id"])],
                         ["SUCCEEDED", "PENDING"])

    def test_resume_after_pause_restarts_the_producer(self):
        goal, _ = self.make_goal(2)
        self.status = {"active": True, "state": "RUNNING", "exit_code": None}
        self.run_until(lambda: len(self.submits) == 1)
        self.store.transition_goal(goal["goal_id"], "PAUSED")
        for _ in range(3):
            self.tick()
            time.sleep(0.02)
        self.status = {"active": False, "state": "COMPLETED",
                       "exit_code": "0:0"}
        self.store.transition_goal(goal["goal_id"], "QUEUED",
                                   next_wake_at=time.time())
        self.run_until(
            lambda: self.store.get_goal(goal["goal_id"])["status"]
            == "COMPLETED")
        self.assertEqual(len(self.submits), 2)
        self.assertEqual(self.statuses(goal["goal_id"]), {"SUCCEEDED"})

    def test_monitor_query_reaches_old_paused_goal_behind_converged_backlog(self):
        goal, _ = self.make_goal(1)
        gid = goal["goal_id"]
        self.status = {"active": True, "state": "RUNNING", "exit_code": None}
        self.run_until(lambda: len(self.submits) == 1)
        self.store.transition_goal(gid, "PAUSED")
        job_id = int(self.items(gid)[0]["external_job_id"])
        db = self.store._db()
        db.execute("UPDATE agent_goals SET updated_at=? WHERE goal_id=?",
                   (time.time() - 1000, gid))
        db.commit()
        # 205 newer paused Goals whose monitoring already converged. The old
        # query took the 200 most recent rows before skipping them in Python,
        # so the older Goal with the live job was never selected.
        checkpoint = {"workflow": batch_manifest.WORKFLOW,
                      "manifest_ref": "backlog", "mode": "demo"}
        converged = []
        for index in range(205):
            backlog = self.store.create_batch_goal(
                title=f"backlog {index}", request_key=f"backlog-{index}",
                content_sha256=f"backlog-{index}", checkpoint=checkpoint,
                items=[{"item_id": f"b{index}", "ordinal": 1,
                        "name": f"b{index}", "status": "PENDING",
                        "checkpoint": {}, "next_wake_at": 0}],
                max_model_calls=0)
            converged.append((time.time() + index, backlog["goal_id"]))
        db.executemany(
            "UPDATE agent_goals SET status='PAUSED',monitor_done=1,"
            "monitor_state='PAUSED',updated_at=? WHERE goal_id=?",
            converged)
        db.commit()
        before = len(self.queries)
        self.tick()
        self.assertGreater(len(self.queries), before)
        self.assertIn(job_id, self.queries)
        paused = self.store.get_goal(gid)
        self.assertEqual(paused["status"], "PAUSED")
        self.assertFalse(paused["monitor_done"])

    def test_cancel_skips_unsubmitted_items_and_finishes_monitoring(self):
        goal, _ = self.make_goal(2)
        self.status = {"active": True, "state": "RUNNING", "exit_code": None}
        self.run_until(lambda: len(self.submits) == 1)
        self.store.transition_goal(goal["goal_id"], "CANCELLED")
        for _ in range(5):
            self.tick()
            time.sleep(0.02)
        statuses = [i["status"] for i in self.items(goal["goal_id"])]
        self.assertEqual(statuses, ["WAITING_SR", "SKIPPED"])
        self.assertEqual(
            self.items(goal["goal_id"])[1]["checkpoint"]["exclusion_reason"],
            "user_cancelled")
        self.assertEqual(len(self.submits), 1)
        self.status = {"active": False, "state": "COMPLETED",
                       "exit_code": "0:0"}
        self.run_until(
            lambda: self.items(goal["goal_id"])[0]["status"] == "SUCCEEDED")
        self.run_until(
            lambda: self.store.get_goal(goal["goal_id"])
            ["checkpoint"].get("monitoring_done"))
        final = self.store.get_goal(goal["goal_id"])
        self.assertEqual(final["status"], "CANCELLED")
        self.assertEqual(len(self.submits), 1)
        self.assertEqual([i["status"] for i in self.items(goal["goal_id"])],
                         ["SUCCEEDED", "SKIPPED"])

    def test_unknown_sr_state_converges_to_blocked_after_bounded_checks(self):
        goal, _ = self.make_goal(1)
        self.status = {"active": True, "state": "RUNNING", "exit_code": None}
        self.run_until(lambda: len(self.submits) == 1)
        self.status = {"active": None, "state": "UNKNOWN", "exit_code": None}
        self.run_until(
            lambda: self.items(goal["goal_id"])[0]["status"] == "BLOCKED",
            ticks=40)
        item = self.items(goal["goal_id"])[0]
        self.assertEqual(item["status"], "BLOCKED")
        self.assertTrue(item["capacity_reserved"])
        self.assertEqual(item["checkpoint"]["unknown_checks"], 3)
        self.assertEqual(
            self.store.get_goal(goal["goal_id"])["status"], "BLOCKED")

    def test_no_candidate_never_submits(self):
        goal, _ = self.make_goal(1)
        os.environ["SR_BATCH_DETECTION"] = "none"
        try:
            self.run_until(
                lambda: self.store.get_goal(goal["goal_id"])["status"]
                == "COMPLETED")
        finally:
            os.environ.pop("SR_BATCH_DETECTION", None)
        self.assertEqual(self.submits, [])
        self.assertEqual(self.items(goal["goal_id"])[0]["status"], "NO_ISSUE")
        report = self.store.get_goal(goal["goal_id"])["checkpoint"]["report"]
        self.assertEqual(report["counts"], {"NO_ISSUE": 1})

    def test_replaced_pan_is_blocked_before_submission(self):
        goal, _ = self.make_goal(1)
        pan = Path(self.items(goal["goal_id"])[0]["checkpoint"]
                   ["target_image"]["path"])
        time.sleep(0.01)
        Image.new("I;16", (16, 16), 9).save(pan)
        self.run_until(
            lambda: self.items(goal["goal_id"])[0]["status"] == "BLOCKED")
        item = self.items(goal["goal_id"])[0]
        self.assertIn("修订", item["error"])
        self.assertEqual(self.submits, [])
        self.assertEqual(
            self.store.get_goal(goal["goal_id"])["status"], "BLOCKED")

    def test_pan_drift_while_waiting_for_capacity_blocks_before_submit(self):
        goal, _ = self.make_goal(
            2, frozen_config={"max_inflight_sr_jobs": 1})
        gid = goal["goal_id"]
        self.status = {"active": True, "state": "RUNNING", "exit_code": None}
        self.run_until(lambda: len(self.submits) == 1)
        # The second scene finishes Mask IO but cannot submit while the first
        # job holds the only capacity slot.
        self.run_until(
            lambda: self.items(gid)[1]["status"] == "MASK_READY")
        pan = Path(self.items(gid)[1]["checkpoint"]["target_image"]["path"])
        time.sleep(0.01)
        Image.new("I;16", (16, 16), 9).save(pan)
        self.status = {"active": False, "state": "COMPLETED",
                       "exit_code": "0:0"}
        self.run_until(lambda: self.items(gid)[1]["status"] == "BLOCKED")
        blocked = self.items(gid)[1]
        self.assertIn("修订", blocked["error"])
        self.assertFalse(blocked["capacity_reserved"])
        self.assertEqual(len(self.submits), 1)

    def test_pan_drift_across_pause_and_resume_blocks_before_submit(self):
        goal, _ = self.make_goal(
            2, frozen_config={"max_inflight_sr_jobs": 1})
        gid = goal["goal_id"]
        self.status = {"active": True, "state": "RUNNING", "exit_code": None}
        self.run_until(lambda: len(self.submits) == 1)
        self.run_until(
            lambda: self.items(gid)[1]["status"] == "MASK_READY")
        self.store.transition_goal(gid, "PAUSED")
        self.tick()
        pan = Path(self.items(gid)[1]["checkpoint"]["target_image"]["path"])
        time.sleep(0.01)
        Image.new("I;16", (16, 16), 9).save(pan)
        # Finish the accepted job, then resume: the Producer reaches the
        # prepared item and must reject it at the submit boundary.
        self.status = {"active": False, "state": "COMPLETED",
                       "exit_code": "0:0"}
        self.store.transition_goal(gid, "QUEUED", next_wake_at=time.time())
        self.run_until(lambda: self.items(gid)[1]["status"] == "BLOCKED")
        self.assertIn("修订", self.items(gid)[1]["error"])
        self.assertEqual(len(self.submits), 1)

    def test_pan_drift_after_restart_blocks_before_submit(self):
        goal, _ = self.make_goal(
            2, frozen_config={"max_inflight_sr_jobs": 1})
        gid = goal["goal_id"]
        self.status = {"active": True, "state": "RUNNING", "exit_code": None}
        self.run_until(lambda: len(self.submits) == 1)
        self.run_until(
            lambda: self.items(gid)[1]["status"] == "MASK_READY")
        # New worker state over the same durable Store == process restart.
        self.state = SimpleNamespace(store=self.store)
        pan = Path(self.items(gid)[1]["checkpoint"]["target_image"]["path"])
        time.sleep(0.01)
        Image.new("I;16", (16, 16), 9).save(pan)
        self.status = {"active": False, "state": "COMPLETED",
                       "exit_code": "0:0"}
        self.run_until(lambda: self.items(gid)[1]["status"] == "BLOCKED")
        self.assertIn("修订", self.items(gid)[1]["error"])
        self.assertEqual(len(self.submits), 1)

    def test_mask_drift_after_mask_ready_blocks_before_submit(self):
        goal, _ = self.make_goal(
            2, frozen_config={"max_inflight_sr_jobs": 1})
        gid = goal["goal_id"]
        self.status = {"active": True, "state": "RUNNING", "exit_code": None}
        self.run_until(lambda: len(self.submits) == 1)
        self.run_until(
            lambda: self.items(gid)[1]["status"] == "MASK_READY")
        mask = Path(self.items(gid)[1]["checkpoint"]["mask"]["path"])
        Image.new("L", (16, 16), 255).save(mask)
        self.status = {"active": False, "state": "COMPLETED",
                       "exit_code": "0:0"}
        self.run_until(lambda: self.items(gid)[1]["status"] == "BLOCKED")
        blocked = self.items(gid)[1]
        self.assertIn("掩码", blocked["error"])
        self.assertFalse(blocked["capacity_reserved"])
        self.assertEqual(len(self.submits), 1)

    def test_creation_time_skipped_items_complete_without_submit(self):
        names = [self.scene(1), self.scene(2)]
        imported = batch_manifest.import_manifest(text=self.qc(*names))
        manifest = batch_manifest.load_manifest(imported["manifest_ref"])
        every_line = [line for item in manifest["items"]
                      for line in item["source_lines"]]
        planned = batch_manifest.plan_goal_items(manifest, every_line)
        self.assertEqual({item["status"] for item in planned}, {"SKIPPED"})
        checkpoint = {
            "workflow": batch_manifest.WORKFLOW,
            "manifest_ref": imported["manifest_ref"],
            "manifest_sha256": manifest["manifest_sha256"],
            "mode": "demo", "exclusions": every_line,
            "batch_control": {}, "producer_status": "DISPATCHING",
            "report_ref": None,
        }
        goal = self.store.create_batch_goal(
            title="empty", request_key="empty-" + str(time.time_ns()),
            content_sha256=imported["manifest_ref"], checkpoint=checkpoint,
            items=[{
                "item_id": item["item_id"], "ordinal": item["ordinal"],
                "name": item["name"], "status": item["status"],
                "checkpoint": _batch_item_checkpoint(item),
                "resource_key": None, "next_wake_at": None,
            } for item in planned], max_model_calls=0)
        self.run_until(
            lambda: self.store.get_goal(goal["goal_id"])["status"]
            == "COMPLETED")
        self.assertEqual(self.submits, [])
        self.assertEqual(
            self.store.get_goal(goal["goal_id"])["checkpoint"]["report"]
            ["counts"], {"SKIPPED": 2})

    def test_mask_writer_contract_and_atomic_publish(self):
        path = self.root / "artifacts" / "m" / "mask.tif"
        meta = batch_mask.build_mask(
            path, width=16, height=12,
            candidates=[{"bbox": [2, 2, 5, 5]}], provenance="mock")
        with Image.open(path) as image:
            self.assertEqual(image.size, (16, 12))
            self.assertEqual(image.mode, "L")
            self.assertEqual(image.getextrema(), (0, 255))
        self.assertGreater(meta["foreground_pixels"], 0)
        self.assertEqual(len(meta["sha256"]), 64)
        self.assertFalse(path.with_name("mask.tmp.tif").exists())

    def test_demo_gate_rejects_production_local_and_bad_work_dir(self):
        with self.assertRaises(batch_worker.BatchGateError):
            batch_worker.ensure_execution_allowed("production")
        with mock.patch.dict(os.environ, {"SR_EXECUTOR": "local"}):
            with self.assertRaises(batch_worker.BatchGateError):
                batch_worker.ensure_execution_allowed("demo")
        with mock.patch.dict(os.environ, {"SR_SLURM_FAKE": "0"}):
            with self.assertRaises(batch_worker.BatchGateError):
                batch_worker.ensure_execution_allowed("demo")
        with mock.patch.dict(os.environ, {"SR_SLURM_WORK_DIR": ""}):
            with self.assertRaises(batch_worker.BatchGateError):
                batch_worker.ensure_execution_allowed("demo")
        work = batch_worker.ensure_execution_allowed("demo")
        with self.assertRaises(batch_worker.BatchGateError):
            batch_worker._check_submit_receipt(
                {"batch_script": str(self.root / "outside.sh"), "job_id": 1},
                work)
        batch_worker._check_submit_receipt(
            {"batch_script": str(work / "ok.sh"), "job_id": 1}, work)

    def test_two_schedulers_do_not_submit_one_item_twice(self):
        goal, _ = self.make_goal(1)
        self.run_until(lambda: len(self.submits) == 1)
        asyncio.run(self._two_ticks())
        self.assertEqual(len(self.submits), 1)
        self.assertEqual(
            self.store._db().execute(
                "SELECT COUNT(*) FROM sr_tasks").fetchone()[0], 1)

    async def _two_ticks(self):
        await asyncio.gather(
            batch_worker.batch_tick(self.state),
            batch_worker.batch_tick(self.state))

    def test_detection_io_does_not_block_sr_status_queries(self):
        goal, _ = self.make_goal(2)
        self.status = {"active": True, "state": "RUNNING", "exit_code": None}
        self.run_until(lambda: len(self.submits) == 1)
        started = threading.Event()
        release = threading.Event()
        self.addCleanup(release.set)
        original = batch_detector.detect
        calls = []

        def blocking(target):
            calls.append(target)
            if len(calls) >= 1:
                started.set()
                release.wait(10)
            return original(target)

        with mock.patch.object(batch_detector, "detect", side_effect=blocking):
            self.tick()
            self.assertTrue(started.wait(5), "detection never started")
            before = len(self.queries)
            time.sleep(0.03)
            self.tick()
            self.assertGreater(
                len(self.queries), before,
                "SR status query was blocked behind detection IO")
            release.set()
            self.status = {"active": False, "state": "COMPLETED",
                           "exit_code": "0:0"}
            self.run_until(
                lambda: self.store.get_goal(goal["goal_id"])["status"]
                == "COMPLETED")

    def test_events_expose_the_pr_f_contract_in_transition_order(self):
        goal, _ = self.make_goal(1)
        self.run_until(
            lambda: self.store.get_goal(goal["goal_id"])["status"]
            == "COMPLETED")
        events = self.store.list_goal_events(goal["goal_id"], limit=1000)
        types = [event["event_type"] for event in events]
        for name in ("item_state_changed", "mask_ready",
                     "sr_submission_reserved", "sr_receipt_recorded",
                     "external_job_terminal", "item_verified",
                     "batch_phase_changed", "batch_report_updated",
                     "goal_verified"):
            self.assertIn(name, types)
        states = [event for event in events
                  if event["event_type"] == "item_state_changed"]
        self.assertEqual([event["payload"]["item_version"] for event in states],
                         list(range(1, len(states) + 1)))
        transitions = [event["payload"]["to"] for event in states]
        self.assertEqual(transitions, [
            "OPENING", "DETECTING", "MASK_READY", "SUBMITTING",
            "WAITING_SR", "VERIFYING", "SUCCEEDED"])
        mask_index = types.index("mask_ready")
        self.assertEqual(types[mask_index - 1], "item_state_changed")
        verified = [event for event in events
                    if event["event_type"] == "batch_report_updated"][-1]
        self.assertEqual(verified["payload"]["counts"], {"SUCCEEDED": 1})
        self.assertEqual(verified["payload"]["provenance"], "mock")

    def test_single_item_detection_error_is_retried_without_failing_the_batch(self):
        goal, _ = self.make_goal(2)
        failed_once = threading.Event()
        lock = threading.Lock()
        original = batch_detector.detect

        def flaky(target):
            with lock:
                first = not failed_once.is_set()
                if first:
                    failed_once.set()
            if first:
                raise RuntimeError("transient detector error")
            return original(target)

        with mock.patch.object(batch_detector, "detect", side_effect=flaky):
            for _ in range(120):
                self.make_retries_due(goal["goal_id"])
                self.tick()
                state = self.store.get_goal(goal["goal_id"])["status"]
                if state in {"COMPLETED", "FAILED", "BLOCKED"}:
                    break
                time.sleep(0.02)
        self.assertEqual(
            self.store.get_goal(goal["goal_id"])["status"], "COMPLETED")
        self.assertEqual(len(self.submits), 2)

    # ---- PR E: layered verifier / monitor budget / isolation -------------
    def test_demo_success_is_scoped_to_the_demo_contract(self):
        """E3: demo success names all five layers and never claims real repair."""
        goal, _ = self.make_goal(1)
        gid = goal["goal_id"]
        self.run_until(
            lambda: self.store.get_goal(gid)["status"] == "COMPLETED")
        item = self.items(gid)[0]
        verification = item["verification"]
        self.assertTrue(verification["ok"])
        self.assertEqual(verification["provenance"], "mock")
        self.assertEqual(verification["success_scope"], "demo_contract")
        self.assertEqual(verification["failed_layers"], [])
        layers = verification["layers"]
        self.assertEqual(layers["submit"]["status"], "passed")
        self.assertEqual(layers["terminal"]["status"], "passed")
        for name in ("contract", "artifacts", "quality"):
            self.assertEqual(
                layers[name]["status"], "not_applicable", name)
        self.assertIn("MockVerifier", layers["quality"]["reason"])
        events = self.store.list_goal_events(gid, limit=1000)
        verified = [e for e in events if e["event_type"] == "item_verified"][0]
        self.assertEqual(verified["payload"]["success_scope"], "demo_contract")
        goal_verified = [
            e for e in events if e["event_type"] == "goal_verified"][-1]
        self.assertTrue(goal_verified["payload"]["ok"])
        self.assertEqual(
            goal_verified["payload"]["success_scope"], "demo_contract")
        report = self.store.get_goal(gid)["checkpoint"]["report"]
        self.assertEqual(report["success_scope"], "demo_contract")

    def test_completed_job_with_nonzero_exit_is_not_a_verified_success(self):
        """E3: termination state alone is not a verdict; exit code must be 0."""
        goal, _ = self.make_goal(1)
        gid = goal["goal_id"]
        self.status = {"active": False, "state": "COMPLETED",
                       "exit_code": "1:0"}
        self.run_until(
            lambda: self.store.get_goal(gid)["status"] == "FAILED")
        item = self.items(gid)[0]
        self.assertEqual(item["status"], "FAILED")
        verification = item["verification"]
        self.assertFalse(verification["ok"])
        self.assertEqual(verification["failed_layers"], ["terminal"])
        self.assertEqual(
            verification["layers"]["terminal"]["status"], "failed")
        self.assertEqual(
            verification["layers"]["contract"]["status"], "skipped")
        self.assertEqual(len(self.submits), 1)
        self.assertEqual(
            self.store.get_goal(gid)["checkpoint"]["report"]["counts"],
            {"FAILED": 1})

    def test_status_query_failures_are_bounded_and_never_a_success(self):
        """E4/K4: first attempt + three retries, then park; no verdict."""
        goal, _ = self.make_goal(1)
        gid = goal["goal_id"]
        self.status = {"active": True, "state": "RUNNING", "exit_code": None}
        self.run_until(lambda: len(self.submits) == 1)
        calls = []

        def failing(job_id, task=None, run_cmd=None):
            calls.append(int(job_id))
            raise RuntimeError("squeue unavailable")

        with mock.patch(
                "backend.services.run_sr.query_job_status",
                side_effect=failing):
            for _ in range(10):
                self.make_monitor_due(gid)
                self.tick()
                item = self.items(gid)[0]
                if item["status"] == "BLOCKED":
                    break
                time.sleep(0.01)
        item = self.items(gid)[0]
        self.assertEqual(item["status"], "BLOCKED")
        self.assertEqual(item["checkpoint"]["monitor_failures"], 4)
        self.assertTrue(item["capacity_reserved"])
        self.assertIn("连续失败 4 次", item["error"])
        self.assertEqual(calls, [1, 1, 1, 1])
        self.assertEqual(len(self.submits), 1)
        self.assertEqual(self.store.get_goal(gid)["status"], "BLOCKED")

    def test_successful_query_resets_the_monitor_failure_cycle(self):
        """K4: one success ends the fault cycle and zeroes the counter."""
        goal, _ = self.make_goal(1)
        gid = goal["goal_id"]
        self.status = {"active": True, "state": "RUNNING", "exit_code": None}
        self.run_until(lambda: len(self.submits) == 1)
        outcomes = [
            RuntimeError("squeue unavailable"),
            RuntimeError("squeue unavailable"),
            {"job_id": 1, "active": True, "state": "RUNNING",
             "exit_code": None},
            RuntimeError("squeue unavailable"),
        ]

        def flaky(job_id, task=None, run_cmd=None):
            outcome = outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return dict(outcome)

        with mock.patch(
                "backend.services.run_sr.query_job_status",
                side_effect=flaky):
            # The first tick can land before the submit receipt updates the
            # next-wake time, so allow one extra tick for the four outcomes.
            for _ in range(5):
                self.make_monitor_due(gid)
                self.tick()
                time.sleep(0.01)
        item = self.items(gid)[0]
        self.assertEqual(item["status"], "WAITING_SR")
        self.assertEqual(item["checkpoint"]["monitor_failures"], 1)

    def test_production_contract_alone_cannot_declare_real_success(self):
        """E3: the quality layer is explicitly unverified until admitted."""
        from backend.services import goal_worker
        output = self.root / "repair.tif"
        output.write_bytes(b"not-a-real-repair")
        status = {"active": False, "state": "COMPLETED", "exit_code": "0:0"}
        task = {"job_id": 7, "config_xml": str(self.root / "cfg.xml"),
                "batch_script": str(self.root / "job.sh"), "log_dir": None}
        with mock.patch.object(
                goal_worker, "sr_contract",
                return_value={"ok": True, "skip": False,
                              "output": str(output), "reasons": []}):
            verification = batch_worker._verify_layers(
                status, "production", task, 7)
        self.assertFalse(verification["ok"])
        self.assertEqual(verification["failed_layers"], ["quality"])
        self.assertEqual(verification["success_scope"],
                         "production_contract")
        self.assertEqual(verification["layers"]["quality"]["status"],
                         "unverified")
        self.assertEqual(verification["layers"]["artifacts"]["status"],
                         "passed")

    def test_late_detection_result_does_not_overwrite_a_cancelled_item(self):
        """E4: a stale worker result is discarded, never rewrites the Item."""
        goal, _ = self.make_goal(1)
        gid = goal["goal_id"]
        started = threading.Event()
        release = threading.Event()
        self.addCleanup(release.set)
        original = batch_detector.detect

        def slow(target):
            started.set()
            release.wait(10)
            return original(target)

        with mock.patch.object(batch_detector, "detect", side_effect=slow):
            self.run_until(started.is_set, ticks=100)
            self.assertEqual(self.items(gid)[0]["status"], "DETECTING")
            self.store.transition_goal(gid, "CANCELLED")
            for _ in range(3):
                self.tick()
                time.sleep(0.01)
            self.assertEqual(self.items(gid)[0]["status"], "SKIPPED")
            release.set()
            # Let the late future finish, then keep monitoring.
            for _ in range(10):
                self.tick()
                time.sleep(0.02)
        item = self.items(gid)[0]
        self.assertEqual(item["status"], "SKIPPED")
        self.assertNotIn("mask", item["checkpoint"])
        self.assertNotIn("detection", item["checkpoint"])
        self.assertEqual(self.submits, [])

    def test_one_failed_job_does_not_block_unrelated_items(self):
        """E4: a failed scene converges alone; the rest still succeed."""
        goal, _ = self.make_goal(3)
        gid = goal["goal_id"]

        def query(job_id, task=None, run_cmd=None):
            if int(job_id) == 1:
                return {"job_id": 1, "active": False, "state": "FAILED",
                        "exit_code": "1:0"}
            return {"job_id": int(job_id), "active": False,
                    "state": "COMPLETED", "exit_code": "0:0"}

        with mock.patch(
                "backend.services.run_sr.query_job_status", side_effect=query):
            self.run_until(
                lambda: self.store.get_goal(gid)["status"] in
                {"COMPLETED", "FAILED"})
        items = self.items(gid)
        self.assertEqual(
            sorted(i["status"] for i in items),
            ["FAILED", "SUCCEEDED", "SUCCEEDED"])
        self.assertEqual(len(self.submits), 3)
        failed = [i for i in items if i["status"] == "FAILED"][0]
        self.assertEqual(
            failed["verification"]["failed_layers"], ["terminal"])
        for item in items:
            if item["status"] == "SUCCEEDED":
                self.assertTrue(item["verification"]["ok"])
        final = self.store.get_goal(gid)
        self.assertEqual(final["status"], "FAILED")
        self.assertEqual(
            final["checkpoint"]["report"]["counts"],
            {"FAILED": 1, "SUCCEEDED": 2})

    def test_fifty_scene_fake_sr_jobs_complete_without_duplicate_submits(self):
        """E acceptance: real Fake SR scheduler, 50 scenes, zero duplicates.

        No mocked ``sbatch_submit`` / ``query_job_status``: this exercises the
        real ``submit_run_sr`` ledger, the in-memory fake scheduler, durable
        restart and layered verification end to end.
        """
        self.query_patch.stop()
        self.sbatch_patch.stop()
        try:
            slurm._fake_reset()
            # Two 100ms stages keep several accepted jobs live long enough for
            # the concurrency/independent-completion samples below.
            os.environ["SR_SLURM_FAKE_T_MS"] = "100"
            goal, _ = self.make_goal(50)
            gid = goal["goal_id"]
            peak = 0
            independently_completed = False
            restarted = False
            for _ in range(2500):
                self.tick()
                items = self.items(gid)
                active = sum(
                    1 for item in items
                    if item["status"] in {"WAITING_SR", "VERIFYING"})
                peak = max(peak, active)
                if active and any(
                        item["status"] == "SUCCEEDED" for item in items):
                    independently_completed = True
                if not restarted and self._submitted_count() >= 50:
                    # Durable restart: only the ledger and Goal state survive.
                    self.state = SimpleNamespace(store=self.store)
                    restarted = True
                if (len(items) == 50
                        and all(item["status"] in {
                            "SUCCEEDED", "NO_ISSUE", "FAILED",
                            "BLOCKED", "SKIPPED"} for item in items)):
                    break
                time.sleep(0.01)
            final = self.store.get_goal(gid)
            self.assertEqual(final["status"], "COMPLETED")
            self.assertTrue(restarted, "the run never reached the restart point")
            items = self.items(gid)
            self.assertEqual({item["status"] for item in items},
                             {"SUCCEEDED"})
            # Every physical sbatch call is exactly one distinct ledger task.
            self.assertEqual(slurm._FAKE_SEQ[0], 50)
            self.assertEqual(len(slurm._FAKE_JOBS), 50)
            rows = self.store._db().execute(
                "SELECT COUNT(*), COUNT(DISTINCT fingerprint), "
                "COUNT(DISTINCT job_id) FROM sr_tasks").fetchone()
            self.assertEqual(rows, (50, 50, 50))
            self.assertGreaterEqual(peak, 2)
            self.assertTrue(independently_completed)
            for item in items:
                verification = item["verification"]
                self.assertTrue(verification["ok"], item["item_id"])
                self.assertEqual(verification["provenance"], "mock")
                self.assertEqual(verification["success_scope"],
                                 "demo_contract")
                self.assertEqual(verification["failed_layers"], [])
            report = final["checkpoint"]["report"]
            self.assertEqual(report["counts"], {"SUCCEEDED": 50})
            self.assertEqual(report["provenance"], "mock")
            self.assertEqual(report["success_scope"], "demo_contract")
            goal_verified = [
                event for event in
                self.store.list_goal_events(gid, limit=5000)
                if event["event_type"] == "goal_verified"][-1]
            self.assertTrue(goal_verified["payload"]["ok"])
            self.assertEqual(
                goal_verified["payload"]["success_scope"],
                "demo_contract")
            self.assertEqual(final["model_calls_used"], 0)
            self.assertEqual(final["rounds_used"], 0)
            for _ in range(5):
                self.tick()
                time.sleep(0.01)
            self.assertEqual(slurm._FAKE_SEQ[0], 50)
        finally:
            self.query_patch.start()
            self.sbatch_patch.start()

    # ---- PR D remediation: async submit verification / recovery ----------
    def test_submit_verification_runs_off_the_scheduler_thread(self):
        """P1 4: a large Mask verification must not stall the Monitor.

        The verification future is held open while the scheduler keeps ticking;
        SR status queries must keep happening, and no submit may leak out
        before the verification completes.
        """
        goal, _ = self.make_goal(2)
        self.status = {"active": True, "state": "RUNNING", "exit_code": None}
        self.run_until(lambda: len(self.submits) == 1)
        started = threading.Event()
        release = threading.Event()
        self.addCleanup(release.set)
        original = batch_worker._prepare_submission

        def blocking(snapshot):
            if not started.is_set():
                started.set()
                release.wait(10)
            return original(snapshot)

        with mock.patch.object(
                batch_worker, "_prepare_submission", side_effect=blocking):
            self.run_until(started.is_set, ticks=200)
            queries_before = len(self.queries)
            for _ in range(3):
                tick_start = time.monotonic()
                self.tick()
                self.assertLess(
                    time.monotonic() - tick_start, 2.0,
                    "scheduler thread blocked on submit verification")
                time.sleep(0.03)
            self.assertGreater(
                len(self.queries), queries_before,
                "Monitor status queries stalled behind submit verification")
            self.assertEqual(len(self.submits), 1)
            release.set()
            self.status = {"active": False, "state": "COMPLETED",
                           "exit_code": "0:0"}
            self.run_until(
                lambda: self.store.get_goal(goal["goal_id"])["status"]
                == "COMPLETED")
        self.assertEqual(len(self.submits), 2)

    def test_mask_change_after_verification_blocks_before_reservation(self):
        """P1 4: the commit window re-proves the Mask file identity."""
        goal, _ = self.make_goal(1)
        gid = goal["goal_id"]
        original = batch_worker._prepare_submission

        def tamper(snapshot):
            prepared = original(snapshot)
            # Append past the verified size: detection/verification are done,
            # so only the commit-window identity check can catch this.
            with open(prepared["mask_path"], "ab") as stream:
                stream.write(b"\x00")
            return prepared

        with mock.patch.object(
                batch_worker, "_prepare_submission", side_effect=tamper):
            self.run_until(
                lambda: any(i["status"] == "BLOCKED" for i in self.items(gid)),
                ticks=200)
        item = self.items(gid)[0]
        self.assertEqual(item["status"], "BLOCKED")
        self.assertIn("复核后发生变化", item["error"])
        self.assertEqual(self.submits, [])
        self.assertEqual(self.store.count_reserved_slots(gid), 0)

    def test_pan_change_after_verification_blocks_before_reservation(self):
        """P1 2: the commit window also re-proves the PAN identity."""
        goal, _ = self.make_goal(1)
        gid = goal["goal_id"]
        original = batch_worker._prepare_submission

        def tamper(snapshot):
            prepared = original(snapshot)
            # Detection + verification already ran; only the commit-window
            # PAN identity check can catch this replacement.
            target = snapshot["item"]["checkpoint"]["target_image"]
            with open(target["path"], "ab") as stream:
                stream.write(b"\x00")
            return prepared

        with mock.patch.object(
                batch_worker, "_prepare_submission", side_effect=tamper):
            self.run_until(
                lambda: any(i["status"] == "BLOCKED" for i in self.items(gid)),
                ticks=200)
        item = self.items(gid)[0]
        self.assertEqual(item["status"], "BLOCKED")
        self.assertIn("PAN 在复核后发生变化", item["error"])
        self.assertEqual(self.submits, [])
        self.assertEqual(self.store.count_reserved_slots(gid), 0)

    def test_nosr_change_after_verification_blocks_before_reservation(self):
        """P1 2: the commit window also re-proves the NOSR identity."""
        goal, _ = self.make_goal(1)
        gid = goal["goal_id"]
        original = batch_worker._prepare_submission

        def tamper(snapshot):
            prepared = original(snapshot)
            reference = snapshot["item"]["checkpoint"]["reference_image"]
            with open(reference["path"], "ab") as stream:
                stream.write(b"\x00")
            return prepared

        with mock.patch.object(
                batch_worker, "_prepare_submission", side_effect=tamper):
            self.run_until(
                lambda: any(i["status"] == "BLOCKED" for i in self.items(gid)),
                ticks=200)
        item = self.items(gid)[0]
        self.assertEqual(item["status"], "BLOCKED")
        self.assertIn("NOSR 在复核后发生变化", item["error"])
        self.assertEqual(self.submits, [])
        self.assertEqual(self.store.count_reserved_slots(gid), 0)

    def test_new_pan_backup_after_verification_blocks_before_reservation(self):
        """P1 2: a PAN backup appearing in the commit window blocks the submit."""
        goal, _ = self.make_goal(1)
        gid = goal["goal_id"]
        original = batch_worker._prepare_submission

        def tamper(snapshot):
            prepared = original(snapshot)
            scene = Path(snapshot["item"]["checkpoint"]["scene_ref"])
            (scene / "PAN_old_latest.tif").write_bytes(b"backup")
            return prepared

        with mock.patch.object(
                batch_worker, "_prepare_submission", side_effect=tamper):
            self.run_until(
                lambda: any(i["status"] == "BLOCKED" for i in self.items(gid)),
                ticks=200)
        item = self.items(gid)[0]
        self.assertEqual(item["status"], "BLOCKED")
        self.assertIn("PAN 备份 在复核后发生变化", item["error"])
        self.assertEqual(self.submits, [])
        self.assertEqual(self.store.count_reserved_slots(gid), 0)

    def _drift_during_verification(self, mutate, expected):
        """Replace an input while verify_mask() is running, then assert BLOCKED."""
        goal, _ = self.make_goal(1)
        gid = goal["goal_id"]
        original = batch_worker._prepare_submission
        verify = batch_mask.verify_mask

        def prepare(snapshot):
            checkpoint = snapshot["item"]["checkpoint"]

            def tamper(*args, **kwargs):
                checked = verify(*args, **kwargs)
                mutate(checkpoint)
                return checked

            with mock.patch.object(
                    batch_mask, "verify_mask", side_effect=tamper):
                return original(snapshot)

        with mock.patch.object(
                batch_worker, "_prepare_submission", side_effect=prepare):
            self.run_until(
                lambda: any(i["status"] == "BLOCKED" for i in self.items(gid)),
                ticks=200)
        item = self.items(gid)[0]
        self.assertEqual(item["status"], "BLOCKED")
        self.assertIn(expected, item["error"])
        self.assertEqual(self.submits, [])
        self.assertEqual(self.store.count_reserved_slots(gid), 0)

    def test_pan_change_during_verification_blocks_before_reservation(self):
        """P1: PAN replaced after the initial check but before identity capture."""
        def mutate(checkpoint):
            with open(checkpoint["target_image"]["path"], "ab") as stream:
                stream.write(b"\x00")

        self._drift_during_verification(mutate, "PAN")

    def test_nosr_change_during_verification_blocks_before_reservation(self):
        """P1: NOSR replaced while the Mask is being verified."""
        def mutate(checkpoint):
            with open(checkpoint["reference_image"]["path"], "ab") as stream:
                stream.write(b"\x00")

        self._drift_during_verification(mutate, "NOSR")

    def test_mask_change_during_verification_blocks_before_reservation(self):
        """P1: the Mask itself changes after verify_mask() returned."""
        def mutate(checkpoint):
            with open(checkpoint["mask"]["path"], "ab") as stream:
                stream.write(b"\x00")

        self._drift_during_verification(mutate, "掩码")

    def test_pan_backup_during_verification_blocks_before_reservation(self):
        """P1: a PAN backup appears while the Mask is being verified."""
        def mutate(checkpoint):
            scene = Path(checkpoint["scene_ref"])
            (scene / "PAN_old_latest.tif").write_bytes(b"backup")

        self._drift_during_verification(mutate, "PAN 备份")

    def test_submit_verification_timeout_backs_off_without_submitting(self):
        goal, _ = self.make_goal(
            1, frozen_config={"detection_timeout_sec": 0.5})
        gid = goal["goal_id"]
        entered = threading.Event()
        release = threading.Event()
        self.addCleanup(release.set)
        original = batch_worker._prepare_submission

        def slow(snapshot):
            entered.set()
            release.wait(10)
            return original(snapshot)

        with mock.patch.object(
                batch_worker, "_prepare_submission", side_effect=slow):
            self.run_until(entered.is_set, ticks=200)
            time.sleep(0.65)
            self.tick()
            item = self.items(gid)[0]
            self.assertEqual(item["status"], "MASK_READY")
            self.assertEqual(
                item["checkpoint"]["submit_prepare_failures"], 1)
            self.assertGreater(item["next_wake_at"], time.time())
            self.assertEqual(self.submits, [])
            self.assertEqual(self.store.count_reserved_slots(gid), 0)
            release.set()
        self.run_until(
            lambda: self.store.get_goal(gid)["status"] == "COMPLETED",
            ticks=600)
        self.assertEqual(len(self.submits), 1)

    def test_detection_concurrency_cap_bounds_parallel_pixel_io(self):
        goal, _ = self.make_goal(
            4, frozen_config={"detection_concurrency": 2,
                              "max_prepared_items": 8})
        lock = threading.Lock()
        state = {"active": 0, "peak": 0}
        release = threading.Event()
        self.addCleanup(release.set)
        original = batch_detector.detect

        def slow(target):
            with lock:
                state["active"] += 1
                state["peak"] = max(state["peak"], state["active"])
            release.wait(10)
            try:
                return original(target)
            finally:
                with lock:
                    state["active"] -= 1

        with mock.patch.object(batch_detector, "detect", side_effect=slow):
            for _ in range(10):
                self.tick()
                time.sleep(0.02)
            self.assertEqual(state["peak"], 2)
            self.assertEqual(self.submits, [])
            release.set()
        self.run_until(
            lambda: self.store.get_goal(goal["goal_id"])["status"]
            == "COMPLETED")
        self.assertEqual(len(self.submits), 4)

    def _restart_item_as(self, gid, *, status, round_no, phase):
        item = self.items(gid)[0]
        cp = dict(item["checkpoint"])
        cp["detection_round"] = round_no
        cp["phase"] = phase
        cp.pop("mask", None)
        cp.pop("detection", None)
        self.store._db().execute(
            "UPDATE agent_goal_steps SET status=?, checkpoint=?, next_wake_at=? "
            "WHERE step_id=?",
            (status, json.dumps(cp, ensure_ascii=False), time.time(),
             item["step_id"]))
        self.store._db().commit()
        return item

    def test_published_but_uncommitted_mask_is_reused_after_restart(self):
        """P2 5: a crash after publish but before the checkpoint commit reuses.

        Pre-publishing the round-1 bundle exactly simulates that window: the
        restarted worker replays the same detection round, validates the
        bundle by candidate/file digest and adopts it instead of orphaning it.
        """
        goal, _ = self.make_goal(1)
        gid = goal["goal_id"]
        item = self.items(gid)[0]
        cp = item["checkpoint"]
        target = {**cp["target_image"], "item_id": item["item_id"],
                  "reference_image": cp.get("reference_image")}
        detection = batch_detector.detect(target)
        path = batch_worker._mask_path(gid, item["item_id"], 1)
        published = batch_mask.build_mask_result(
            detection, target, {"path": path})
        stale_tmp = path.parent.with_name(path.parent.name + ".tmp-999-1")
        stale_tmp.mkdir(parents=True)
        (stale_tmp / "half.tif").write_bytes(b"x")

        self.state = SimpleNamespace(store=self.store)
        self.run_until(
            lambda: self.store.get_goal(gid)["status"] == "COMPLETED")
        self.assertEqual(len(self.submits), 1)
        final = self.items(gid)[0]
        self.assertEqual(
            final["checkpoint"]["mask"]["sha256"], published["sha256"])
        self.assertIn(
            "mask_bundle_reused",
            [d["code"] for d in final["checkpoint"]["mask"]["diagnostics"]])
        self.assertFalse(stale_tmp.exists())
        self.assertTrue(path.is_file())
        self.assertTrue((path.parent / "overlay.png").is_file())

    def test_crash_before_publish_rebuilds_the_same_round_without_leftovers(self):
        goal, _ = self.make_goal(1)
        gid = goal["goal_id"]
        self._restart_item_as(
            gid, status="DETECTING", round_no=1, phase="DETECTING")
        path = batch_worker._mask_path(gid, self.items(gid)[0]["item_id"], 1)
        stale_tmp = path.parent.with_name(path.parent.name + ".tmp-999-1")
        stale_tmp.mkdir(parents=True)

        self.state = SimpleNamespace(store=self.store)
        self.run_until(
            lambda: self.store.get_goal(gid)["status"] == "COMPLETED")
        self.assertEqual(len(self.submits), 1)
        final = self.items(gid)[0]
        self.assertTrue(
            final["checkpoint"]["mask"]["path"].endswith(
                "/detect-1/mask.tif"))
        self.assertNotIn(
            "mask_bundle_reused",
            [d["code"] for d in final["checkpoint"]["mask"]["diagnostics"]])
        self.assertFalse(stale_tmp.exists())

    def test_ordinary_goal_is_not_touched_by_the_batch_scheduler(self):
        ordinary = self.store.create_goal(title="ordinary", status="QUEUED")
        goal, _ = self.make_goal(1)
        self.run_until(
            lambda: self.store.get_goal(goal["goal_id"])["status"]
            == "COMPLETED")
        current = self.store.get_goal(ordinary["goal_id"])
        self.assertEqual(current["status"], "QUEUED")
        self.assertEqual(current["rounds_used"], 0)
        self.assertEqual(current["model_calls_used"], 0)


if __name__ == "__main__":
    unittest.main()
