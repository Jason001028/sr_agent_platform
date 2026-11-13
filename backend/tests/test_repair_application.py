"""Unified Repair Application Service: one plan/confirmation/Goal contract.

Uses tiny real TIFF/mask fixtures and the Fake SR gate.  No GPU, no production
SR process and no directory listing is reached.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest import mock

from PIL import Image

from backend.api import paths
from backend.services import batch_manifest, batch_worker, repair_application as svc
from backend.services import scene_search
from backend.services.store import Store, RepairPlanConflict
from backend.services.viewer_context import bind_registry, validate_snapshot
from backend.tools import build_builtin_registry
from backend.tests import allowed_roots_env

BASE = "JXGF07D03_PMS_20260101010101_200516571_101_{scene:04d}_001_L1"


class RepairApplicationTest(unittest.TestCase):
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
        }
        self.env = mock.patch.dict(os.environ, env)
        self.env.start()
        self.store = Store(self.root / "test.db")
        self.session_id = self.store.create_session()

    def tearDown(self):
        self.store.close()
        self.env.stop()
        self.tmp.cleanup()

    # ---- fixtures ------------------------------------------------------
    def scene(self, index: int):
        stem = BASE.format(scene=index)
        directory = self.root / (stem + "_PAN")
        directory.mkdir()
        (directory / (directory.name + "_meta.xml")).write_text(
            "<meta><SolarAzimuth/></meta>", encoding="utf-8")
        Image.new("I;16", (16, 16), 7).save(directory / "PAN.tif")
        Image.new("I;16", (8, 8), 7).save(
            directory / (directory.name + "_NOSR.tif"))
        return directory, directory / "PAN.tif"

    def workspace(self, *scenes):
        return {
            "version": 7,
            "total": len(scenes),
            "items": [
                {"scene_id": paths.scene_id_abs(inp), "name": inp.parent.name}
                for _, inp in scenes
            ],
        }

    def save_mask(self, scene_dir: Path):
        mask = Image.new("L", (16, 16), 0)
        for x in range(2, 8):
            for y in range(3, 9):
                mask.putpixel((x, y), 255)
        mask.save(scene_search.derived_mask_path(scene_dir.as_posix()))

    def prepared(self, *scenes, session="s1", **kwargs):
        plan = svc.prepare_repair(
            self.store, session_id=session,
            workspace=self.workspace(*scenes), scope="workspace",
            mode="demo", **kwargs)
        return plan

    # ---- plan / confirmation / idempotent start ------------------------
    def test_prepare_uses_workspace_without_searching_the_library(self):
        scene_dir, inp = self.scene(1)
        with mock.patch.object(
                scene_search, "search_scenes",
                side_effect=AssertionError("must not search")):
            plan = self.prepared((scene_dir, inp))
        self.assertEqual(plan["status"], "READY")
        self.assertEqual(plan["targets"]["applicable"], 1)
        self.assertTrue(plan["execution"]["allowed"])
        stored = self.store.get_repair_plan(plan["plan_id"])
        self.assertIsNotNone(stored)
        self.assertEqual(stored["status"], "READY")

    def test_mixed_workspace_plans_resolvable_targets_and_starts(self):
        scene_dir, inp = self.scene(12)
        missing = paths.scene_id_abs(self.root / "missing_scene" / "PAN.tif")
        workspace = {
            "version": 4, "total": 2, "local_only": 0,
            "items": [
                {"scene_id": paths.scene_id_abs(inp), "name": "ok"},
                {"scene_id": missing, "name": "broken"},
            ],
        }
        plan = svc.prepare_repair(
            self.store, session_id="s1", workspace=workspace,
            scope="workspace", mode="demo")
        self.assertEqual(plan["targets"]["total"], 1)
        self.assertEqual(plan["targets"]["unresolved"][0]["name"], "broken")
        svc.confirm_repair(self.store, plan["plan_id"], session_id="s1")
        # Extra/unresolvable workspace cards do not block a confirmed plan;
        # the plan's real target is still present.
        started = svc.start_repair(
            self.store, plan["plan_id"], session_id="s1",
            workspace=workspace, workspace_version=4)
        self.assertTrue(started["goal"]["goal_id"])

    def test_truncated_workspace_is_never_silently_subsetted(self):
        scene_dir, inp = self.scene(13)
        with self.assertRaisesRegex(svc.RepairError, "截断"):
            svc.prepare_repair(
                self.store, session_id="s1", mode="demo", scope="workspace",
                workspace={"version": 1, "total": 500, "truncated": True,
                           "items": [{"scene_id": paths.scene_id_abs(inp),
                                      "name": "only first"}]})

    def test_local_only_workspace_is_blocked_with_a_reason(self):
        with self.assertRaisesRegex(svc.RepairError, "可信盘阵路径"):
            svc.prepare_repair(
                self.store, session_id="s1", scope="workspace",
                workspace={"version": 1, "total": 0, "items": [],
                           "local_only": 2},
                mode="demo")

    def test_start_requires_a_server_recorded_user_confirmation(self):
        scene_dir, inp = self.scene(2)
        plan = self.prepared((scene_dir, inp))
        with self.assertRaises(RepairPlanConflict) as raised:
            svc.start_repair(self.store, plan["plan_id"], session_id="s1")
        self.assertEqual(raised.exception.reason, "confirmation_required")
        svc.confirm_repair(self.store, plan["plan_id"], session_id="s1")
        started = svc.start_repair(self.store, plan["plan_id"], session_id="s1")
        self.assertTrue(started["goal"]["goal_id"])
        again = svc.start_repair(self.store, plan["plan_id"], session_id="s1")
        self.assertTrue(again["reused"])
        self.assertEqual(again["goal"]["goal_id"], started["goal"]["goal_id"])
        # Only one physical Goal row exists for the plan.
        self.assertEqual(
            len(self.store.list_goals(kinds=["batch"])), 1)

    def test_reprepare_bumps_version_and_old_version_cannot_start(self):
        scene_dir, inp = self.scene(14)
        plan = self.prepared((scene_dir, inp))
        self.assertEqual(plan["version"], 1)
        self.store.update_repair_plan_status(
            plan["plan_id"], "SUPERSEDED", "test")
        again = self.prepared((scene_dir, inp))
        self.assertEqual(again["plan_id"], plan["plan_id"])
        self.assertEqual(again["version"], 2)
        svc.confirm_repair(self.store, plan["plan_id"], session_id="s1")
        with self.assertRaises(RepairPlanConflict) as raised:
            svc.start_repair(self.store, plan["plan_id"], session_id="s1",
                             plan_version=1)
        self.assertEqual(raised.exception.reason, "stale_plan")
        started = svc.start_repair(self.store, plan["plan_id"],
                                   session_id="s1", plan_version=2)
        self.assertTrue(started["goal"]["goal_id"])

    def test_lost_start_response_recovers_the_same_goal(self):
        scene_dir, inp = self.scene(15)
        plan = self.prepared((scene_dir, inp))
        svc.confirm_repair(self.store, plan["plan_id"], session_id="s1")
        started = svc.start_repair(self.store, plan["plan_id"], session_id="s1")
        goal_id = started["goal"]["goal_id"]
        # Simulate "Goal committed, response lost before goal_id was recorded".
        db = self.store._db()
        db.execute("UPDATE repair_plans SET status='CONFIRMED', goal_id=NULL "
                   "WHERE plan_id=?", (plan["plan_id"],))
        db.commit()
        recovered = svc.start_repair(
            self.store, plan["plan_id"], session_id="s1")
        self.assertTrue(recovered["reused"])
        self.assertEqual(recovered["goal"]["goal_id"], goal_id)
        self.assertEqual(len(self.store.list_goals(kinds=["batch"])), 1)

    def test_changed_source_revision_invalidates_the_confirmation(self):
        scene_dir, inp = self.scene(3)
        plan = self.prepared((scene_dir, inp))
        svc.confirm_repair(self.store, plan["plan_id"], session_id="s1")
        Image.new("I;16", (16, 16), 9).save(inp)
        later = time.time() + 5
        os.utime(inp, (later, later))
        with self.assertRaises(RepairPlanConflict) as raised:
            svc.start_repair(self.store, plan["plan_id"], session_id="s1")
        self.assertEqual(raised.exception.reason, "stale_source")

    def test_changed_workspace_invalidates_the_confirmation(self):
        a_dir, a_inp = self.scene(4)
        b_dir, b_inp = self.scene(5)
        plan = self.prepared((a_dir, a_inp), (b_dir, b_inp))
        svc.confirm_repair(self.store, plan["plan_id"], session_id="s1")
        with self.assertRaises(RepairPlanConflict) as raised:
            svc.start_repair(
                self.store, plan["plan_id"], session_id="s1",
                workspace=self.workspace((a_dir, a_inp)),
                workspace_version=8)
        self.assertEqual(raised.exception.reason, "stale_workspace")

    def _resource_goal(self, key, item_id, created_at=None):
        goal = self.store.create_batch_goal(
            title=key, request_key="rk-" + key, content_sha256="c-" + key,
            checkpoint={}, items=[{
                "item_id": item_id, "ordinal": 1, "name": key,
                "status": "PENDING", "checkpoint": {},
                "resource_key": "pan-key",
            }], max_model_calls=0)
        if created_at is not None:
            db = self.store._db()
            db.execute("UPDATE agent_goals SET created_at=? WHERE goal_id=?",
                       (created_at, goal["goal_id"]))
            db.commit()
        return goal["goal_id"]

    def test_resource_claim_is_atomic_across_plans(self):
        a = self._resource_goal("goal-a", "item-a")
        b = self._resource_goal("goal-b", "item-b")
        self.assertTrue(self.store.claim_batch_resource(a, "item-a", "pan-key"))
        self.assertEqual(self.store._db().execute(
            "SELECT status FROM batch_resource_locks WHERE resource_key=?",
            ("pan-key",)).fetchone()[0], "ACTIVE")
        self.assertFalse(self.store.claim_batch_resource(b, "item-b", "pan-key"))
        self.assertTrue(self.store.claim_batch_resource(a, "item-a", "pan-key"))
        self.store.release_batch_resource(a, "item-a", "pan-key")
        self.assertEqual(self.store._db().execute(
            "SELECT status FROM batch_resource_locks WHERE resource_key=?",
            ("pan-key",)).fetchone()[0], "RELEASED")
        self.assertTrue(self.store.claim_batch_resource(b, "item-b", "pan-key"))

    def test_settled_resource_allows_only_newer_confirmed_plan(self):
        a = self._resource_goal("goal-settled-a", "item-a", created_at=100.0)
        concurrent = self._resource_goal(
            "goal-concurrent", "item-c", created_at=110.0)
        later = self._resource_goal("goal-later", "item-l", created_at=300.0)
        self.assertTrue(self.store.claim_batch_resource(a, "item-a", "pan-key"))
        # Simulate the owner Item succeeding and the Goal completing.
        db = self.store._db()
        db.execute("UPDATE agent_goal_steps SET status='SUCCEEDED',"
                   "updated_at=200 WHERE goal_id=? AND item_id='item-a'", (a,))
        db.execute("UPDATE agent_goals SET status='COMPLETED' WHERE goal_id=?",
                   (a,))
        db.execute("UPDATE batch_resource_locks SET status='SETTLED',"
                   "settled_at=200 WHERE resource_key='pan-key'")
        db.commit()
        # A plan that existed beside the repair must not take over.
        self.assertFalse(self.store.claim_batch_resource(
            concurrent, "item-c", "pan-key"))
        # A plan created after the repair settled may legitimately re-repair.
        self.assertTrue(self.store.claim_batch_resource(
            later, "item-l", "pan-key"))

    def test_unknown_external_outcome_keeps_the_pan_locked(self):
        a = self._resource_goal("goal-unknown-a", "item-a")
        b = self._resource_goal("goal-unknown-b", "item-b", created_at=10**9)
        self.assertTrue(self.store.claim_batch_resource(a, "item-a", "pan-key"))
        db = self.store._db()
        db.execute("UPDATE agent_goal_steps SET status='BLOCKED',"
                   "capacity_reserved=1,updated_at=5 WHERE goal_id=? "
                   "AND item_id='item-a'", (a,))
        db.execute("UPDATE agent_goals SET status='BLOCKED' WHERE goal_id=?",
                   (a,))
        db.commit()
        self.assertFalse(self.store.claim_batch_resource(
            b, "item-b", "pan-key"))

    def test_same_count_different_workspace_set_invalidates_the_plan(self):
        a_dir, a_inp = self.scene(21)
        b_dir, b_inp = self.scene(22)
        plan = self.prepared((a_dir, a_inp))
        svc.confirm_repair(self.store, plan["plan_id"], session_id="s1")
        other = {
            "version": 7, "total": 2, "local_only": 0,
            "items": [
                {"scene_id": paths.scene_id_abs(a_inp), "name": "a"},
                {"scene_id": paths.scene_id_abs(b_inp), "name": "b"},
            ],
        }
        # The executable target is still present, but the full workspace
        # identity set changed; the old confirmation must not be reused.
        with self.assertRaises(RepairPlanConflict) as raised:
            svc.start_repair(
                self.store, plan["plan_id"], session_id="s1",
                workspace=other, workspace_version=7)
        self.assertEqual(raised.exception.reason, "stale_workspace")

    def test_truncated_start_workspace_is_refused(self):
        scene_dir, inp = self.scene(23)
        plan = self.prepared((scene_dir, inp))
        svc.confirm_repair(self.store, plan["plan_id"], session_id="s1")
        with self.assertRaises(RepairPlanConflict) as raised:
            svc.start_repair(
                self.store, plan["plan_id"], session_id="s1",
                workspace={"version": 7, "total": 500, "truncated": True,
                           "items": [{"scene_id": paths.scene_id_abs(inp),
                                      "name": "a"}]})
        self.assertEqual(raised.exception.reason, "stale_workspace")

    def test_production_mode_is_refused_without_bypassing_the_gate(self):
        scene_dir, inp = self.scene(6)
        plan = svc.prepare_repair(
            self.store, session_id="s1", workspace=self.workspace(
                (scene_dir, inp)), scope="workspace", mode="production")
        self.assertFalse(plan["execution"]["allowed"])
        svc.confirm_repair(self.store, plan["plan_id"], session_id="s1")
        with self.assertRaises(svc.RepairBlocked):
            svc.start_repair(self.store, plan["plan_id"], session_id="s1")
        self.assertEqual(self.store.list_goals(kinds=["batch"]), [])

    def test_manual_mask_becomes_a_mask_ready_batch_item(self):
        scene_dir, inp = self.scene(7)
        self.save_mask(scene_dir)
        plan = svc.prepare_repair(
            self.store, session_id="s1", scope="current",
            current={"scene_id": paths.scene_id_abs(inp),
                     "name": scene_dir.name},
            mode="demo", mask_policy="existing")
        self.assertEqual(plan["status"], "READY")
        self.assertEqual(plan["scope"], "single")
        self.assertEqual(plan["strategy"]["mask"], "existing")
        svc.confirm_repair(self.store, plan["plan_id"], session_id="s1")
        started = svc.start_repair(self.store, plan["plan_id"], session_id="s1")
        items = self.store.list_goal_items(started["goal"]["goal_id"])
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["status"], "MASK_READY")
        mask = items[0]["checkpoint"]["mask"]
        artifact_root = batch_manifest.artifact_root().resolve()
        self.assertIn(str(artifact_root), str(Path(mask["path"]).resolve()))
        # The existing submit-boundary verification accepts this item.
        batch_worker._verify_submission_inputs(items[0], "demo")
        self.assertEqual(
            items[0]["checkpoint"]["detection"]["detector_name"],
            "existing-human-mask")

    def test_empty_manual_mask_is_rejected_without_creating_a_goal(self):
        scene_dir, inp = self.scene(16)
        Image.new("L", (16, 16), 0).save(
            scene_search.derived_mask_path(scene_dir.as_posix()))
        plan = svc.prepare_repair(
            self.store, session_id="s1", scope="current",
            current={"scene_id": paths.scene_id_abs(inp),
                     "name": scene_dir.name},
            mode="demo", mask_policy="existing")
        svc.confirm_repair(self.store, plan["plan_id"], session_id="s1")
        with self.assertRaisesRegex(svc.RepairError, "为空"):
            svc.start_repair(self.store, plan["plan_id"], session_id="s1")
        self.assertEqual(self.store.list_goals(kinds=["batch"]), [])
        leftovers = [p.name for p in
                     (batch_manifest.artifact_root() / "work").rglob(".mask-*")]
        self.assertEqual(leftovers, [])

    def test_manual_mask_must_belong_to_the_target_scene(self):
        scene_dir, inp = self.scene(17)
        other_dir, _ = self.scene(18)
        foreign = other_dir / "PAN_mask.tif"
        Image.new("L", (16, 16), 255).save(foreign)
        plan = svc.prepare_repair(
            self.store, session_id="s1", scope="current",
            current={"scene_id": paths.scene_id_abs(inp),
                     "name": scene_dir.name},
            mode="demo", mask_policy="existing",
            mask_path=foreign.as_posix())
        self.assertEqual(plan["status"], "BLOCKED")
        self.assertIn("目标场景目录", " ".join(
            row.get("message", "") for row in plan["blocking"]))

    def test_status_and_result_read_the_goal_not_chat_text(self):
        scene_dir, inp = self.scene(8)
        plan = self.prepared((scene_dir, inp))
        svc.confirm_repair(self.store, plan["plan_id"], session_id="s1")
        svc.start_repair(self.store, plan["plan_id"], session_id="s1")
        status = svc.get_repair_status(self.store, plan["plan_id"])
        self.assertIsNotNone(status["goal"])
        self.assertEqual(len(status["items"]), 1)
        self.assertIn("image_path", status["items"][0])
        # A missing convergence marker is not "done"; the projection says so
        # explicitly instead of leaving null for the UI to interpret.
        self.assertIs(status["goal"]["monitoring_done"], False)
        self.assertIs(status["goal"]["monitor_done"], False)
        self.assertIsNone(status["items"][0]["output_path"])
        self.assertFalse(status["acceptance"]["computed"])
        self.assertEqual(status["quality_reviews"],
                         {"approved": 0, "rejected": 0, "pending": 0})
        self.assertEqual(status["quality_regions"],
                         {"approved": 0, "rejected": 0, "pending": 0, "total": 0})
        result = svc.get_repair_result(self.store, plan["plan_id"])
        self.assertFalse(result["production_verified"])
        self.assertFalse(result["quality_passed"])
        self.assertEqual(result["state"], "running")
        self.assertEqual(result["success_scope"], "demo_contract")

    def test_status_projects_only_the_current_review_and_verified_output(self):
        scene_dir, inp = self.scene(24)
        plan = self.prepared((scene_dir, inp))
        svc.confirm_repair(self.store, plan["plan_id"], session_id="s1")
        started = svc.start_repair(self.store, plan["plan_id"], session_id="s1")
        goal_id = started["goal"]["goal_id"]
        item = self.store.list_goal_items(goal_id)[0]
        cp = dict(item["checkpoint"])
        cp["quality_review_id"] = "rev-current"
        verification = {
            "ok": False,
            "layers": {
                "artifacts": {"status": "passed",
                              "path": (self.root / "out" / "PAN.tif").as_posix()},
                "quality": {"status": "unverified"},
            },
        }
        db = self.store._db()
        db.execute(
            "UPDATE agent_goal_steps SET status='AWAITING_REVIEW',"
            "checkpoint=?,verification=? WHERE goal_id=? AND item_id=?",
            (json.dumps(cp, ensure_ascii=False),
             json.dumps(verification, ensure_ascii=False),
             goal_id, item["item_id"]))
        now = time.time()
        db.execute(
            "INSERT INTO batch_quality_reviews(review_id,goal_id,item_id,job_id,"
            "binding,regions,scene_decision,version,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            ("rev-current", goal_id, item["item_id"], str(item.get("external_job_id") or ""),
             "{}", json.dumps([
                 {"region_id": "r1", "decision": "approved"},
                 {"region_id": "r2", "decision": "pending"}]),
             "pending", 1, now, now))
        # A superseded review row (previous Job) must not contribute.
        db.execute(
            "INSERT INTO batch_quality_reviews(review_id,goal_id,item_id,job_id,"
            "binding,regions,scene_decision,version,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            ("rev-stale", goal_id, item["item_id"], "old-job",
             "{}", json.dumps([{"region_id": "old", "decision": "approved"}]),
             "approved", 1, now, now))
        db.execute("UPDATE agent_goals SET status='COMPLETED',monitor_done=1 "
                   "WHERE goal_id=?", (goal_id,))
        db.commit()
        status = svc.get_repair_status(self.store, plan["plan_id"])
        self.assertEqual(status["items"][0]["output_path"],
                         (self.root / "out" / "PAN.tif").as_posix())
        self.assertEqual(status["items"][0]["quality_review_id"], "rev-current")
        self.assertEqual(status["quality_reviews"],
                         {"approved": 0, "rejected": 0, "pending": 1})
        self.assertEqual(status["quality_regions"],
                         {"approved": 1, "rejected": 0, "pending": 1, "total": 2})
        self.assertTrue(status["acceptance"]["computed"])
        # Demo results can never become a human-quality acceptance.
        self.assertFalse(status["acceptance"]["quality_passed"])
        result = svc.get_repair_result(self.store, plan["plan_id"])
        self.assertEqual(result["state"], "settled")
        self.assertFalse(result["quality_passed"])

    def test_verified_output_requires_a_passed_artifacts_layer(self):
        self.assertIsNone(svc.verified_output_path({}))
        self.assertIsNone(svc.verified_output_path({
            "status": "SUCCEEDED",
            "verification": {"layers": {"artifacts": {
                "status": "not_applicable", "path": "/original/PAN.tif"}}}}))
        # A failed Item never becomes a viewable result, even with a stale
        # passed artifacts layer.
        self.assertIsNone(svc.verified_output_path({
            "status": "FAILED",
            "verification": {"layers": {"artifacts": {
                "status": "passed", "path": "/repaired/PAN.tif"}}}}))
        self.assertEqual(svc.verified_output_path({
            "status": "AWAITING_REVIEW",
            "verification": {"layers": {"artifacts": {
                "status": "passed", "path": "/repaired/PAN.tif"}}}}),
            "/repaired/PAN.tif")

    def test_rest_entry_uses_the_same_plan_and_goal(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from backend.api import repair as repair_api
        scene_dir, inp = self.scene(10)
        app = FastAPI()
        app.state.store = self.store
        app.include_router(repair_api.router)
        sid = self.session_id
        with TestClient(app) as client:
            prepared = client.post("/api/repairs/prepare", json={
                "session_id": sid, "mode": "demo",
                "sources": [{"path": inp.as_posix(), "name": scene_dir.name,
                             "evidence": "Viewer 序列 1"}]})
            self.assertEqual(prepared.status_code, 200, prepared.text)
            plan_id = prepared.json()["plan_id"]
            early = client.post(f"/api/repairs/{plan_id}/start",
                                json={"session_id": sid})
            self.assertEqual(early.status_code, 409, early.text)
            self.assertEqual(early.json()["detail"]["reason"],
                             "confirmation_required")
            confirmed = client.post(f"/api/repairs/{plan_id}/confirm",
                                    json={"session_id": sid})
            self.assertEqual(confirmed.status_code, 200, confirmed.text)
            self.assertEqual(
                self.store.get_repair_plan(plan_id)["confirmed_by"],
                f"user:{sid}")
            started = client.post(f"/api/repairs/{plan_id}/start",
                                  json={"session_id": sid})
            self.assertEqual(started.status_code, 200, started.text)
            goal_id = started.json()["goal"]["goal_id"]
            status = client.get(f"/api/repairs/{plan_id}",
                                params={"session_id": sid})
            self.assertEqual(status.status_code, 200, status.text)
            self.assertEqual(status.json()["goal"]["goal_id"], goal_id)
            self.assertIn("monitor_done", status.json()["goal"])
            # Another existing session cannot read or confirm this plan.
            other = self.store.create_session()
            hidden = client.get(f"/api/repairs/{plan_id}",
                                params={"session_id": other})
            self.assertEqual(hidden.status_code, 404, hidden.text)
            forged = client.post(f"/api/repairs/{plan_id}/confirm",
                                 json={"session_id": other,
                                       "confirmed_by": "admin"})
            self.assertEqual(forged.status_code, 404, forged.text)

    def test_rest_rejects_unknown_session_and_forged_actor(self):
        scene_dir, inp = self.scene(11)
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from backend.api import repair as repair_api
        app = FastAPI()
        app.state.store = self.store
        app.include_router(repair_api.router)
        with TestClient(app) as client:
            unknown = client.post("/api/repairs/prepare", json={
                "session_id": "not-a-real-session", "mode": "demo",
                "sources": [{"path": inp.as_posix(), "name": scene_dir.name,
                             "evidence": "x"}]})
            self.assertEqual(unknown.status_code, 403, unknown.text)
            sid = self.session_id
            prepared = client.post("/api/repairs/prepare", json={
                "session_id": sid, "mode": "demo",
                "sources": [{"path": inp.as_posix(), "name": scene_dir.name,
                             "evidence": "x"}]}).json()
            confirmed = client.post(
                f"/api/repairs/{prepared['plan_id']}/confirm",
                json={"session_id": sid, "confirmed_by": "admin"})
            self.assertEqual(confirmed.status_code, 200, confirmed.text)
            # The client-supplied actor is ignored.
            self.assertEqual(
                self.store.get_repair_plan(prepared["plan_id"])["confirmed_by"],
                f"user:{sid}")

    # ---- Agent tools ---------------------------------------------------
    def test_agent_prepare_then_start_shares_the_plan_contract(self):
        scene_dir, inp = self.scene(9)
        snapshot = validate_snapshot({
            "version": 1,
            "captured_at": "send-time",
            "current": {"id": 1, "name": scene_dir.name,
                        "scene_id": paths.scene_id_abs(inp)},
            "selected_ids": [],
            "displayed": [],
            "workspace": {"version": 3, "items": [
                {"scene_id": paths.scene_id_abs(inp),
                 "name": scene_dir.name}], "total": 1},
        })
        registry = bind_registry(build_builtin_registry(), snapshot,
                                 store=self.store, session_id="s1")
        prepared = registry.invoke("prepare_repair", {"scope": "workspace"})
        self.assertTrue(prepared["ok"], prepared)
        plan_id = prepared["data"]["plan_id"]
        before = registry.invoke("start_repair", {"plan_id": plan_id})
        self.assertFalse(before["ok"])
        self.assertEqual(before.get("error_class"), "confirmation_required")
        svc.confirm_repair(self.store, plan_id, session_id="s1")
        started = registry.invoke("start_repair", {"plan_id": plan_id})
        self.assertTrue(started["ok"], started)
        reused = registry.invoke("start_repair", {"plan_id": plan_id})
        self.assertTrue(reused["data"]["reused"])
        self.assertEqual(reused["data"]["goal_id"],
                         started["data"]["goal_id"])
        listed = registry.invoke("get_repair_workspace", {})
        self.assertTrue(listed["ok"])
        self.assertEqual(listed["data"]["total"], 1)


if __name__ == "__main__":
    unittest.main()
