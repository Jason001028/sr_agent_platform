"""PR F: authoritative batch snapshot, paged events and user control.

The projection is pure; the store tests use a real SQLite database.  Nothing
here decodes a TIFF, touches GPU/SR, or scans the disk array.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest import mock

from backend.services import batch_snapshot
from backend.services.store import BatchControlConflict, Store


# ---- pure projection -------------------------------------------------------

class SnapshotProjectionTests(unittest.TestCase):
    def item(self, ordinal, status, *, detection=None, job_id=None,
             verification=None, cp_extra=None, excluded=False):
        cp = {
            "phase": status,
            "attempt": 0,
            "name": f"scene-{ordinal}",
            "target_image": {"path": f"D:/scenes/scene-{ordinal}/PAN.tif"},
            "source_lines": [] if excluded else [f"line-{ordinal}"],
        }
        if detection is not None:
            cp["detection"] = detection
        if job_id is not None:
            cp["job_id"] = job_id
        if cp_extra:
            cp.update(cp_extra)
        return {
            "item_id": f"inv-{ordinal}", "ordinal": ordinal,
            "name": f"scene-{ordinal}", "status": status,
            "checkpoint": cp, "version": 1, "error": None,
            "verification": verification or {}, "updated_at": 1.0,
            "external_job_id": None,
        }

    def goal(self, checkpoint=None):
        return {
            "goal_id": "g", "session_id": "s", "title": "batch",
            "kind": "batch", "status": "RUNNING", "phase": "detecting",
            "version": 9, "checkpoint": checkpoint or {},
            "monitor_done": 0, "monitor_state": None,
            "created_at": 1.0, "updated_at": 2.0, "finished_at": None,
        }

    def test_repair_ratio_stays_unknown_while_detection_is_incomplete(self):
        """Revision 4: R=0 must not be rendered as a settled 0%."""
        items = [
            self.item(1, "DETECTING"),
            self.item(2, "PENDING"),
        ]
        counts = batch_snapshot.count_items(items)
        progress = batch_snapshot.progress_from_counts(counts)
        self.assertEqual(counts["eligible"], 2)
        self.assertEqual(counts["needs_repair"], 0)
        self.assertFalse(progress["detection"]["known"])
        self.assertFalse(progress["submission"]["applicable"])
        self.assertEqual(progress["submission"]["total"], 0)

        items[1]["status"] = "NO_ISSUE"
        items[1]["checkpoint"]["detection"] = {"candidates": []}
        items[0]["status"] = "NO_ISSUE"
        items[0]["checkpoint"]["detection"] = {"candidates": [], "mask": None}
        progress = batch_snapshot.progress_from_counts(
            batch_snapshot.count_items(items))
        self.assertTrue(progress["detection"]["known"])
        self.assertFalse(progress["submission"]["applicable"])
        self.assertFalse(progress["verification"]["applicable"])

    def test_ratio_may_fall_when_repair_count_grows(self):
        items = [self.item(1, "DETECTING", detection={"candidates": [1]})]
        first = batch_snapshot.progress_from_counts(
            batch_snapshot.count_items(items))
        self.assertEqual(first["submission"], {
            "done": 0, "total": 1, "applicable": True, "known": False})
        items[0]["status"] = "SUCCEEDED"
        items[0]["checkpoint"]["job_id"] = 7
        items[0]["verification"] = {"ok": True, "success_scope": "demo_contract"}
        items.append(self.item(2, "DETECTING", detection={"candidates": [2, 3]}))
        second = batch_snapshot.progress_from_counts(
            batch_snapshot.count_items(items))
        # Denominator grew from 1 to 2 and the ratio fell; that is real data,
        # not a reason to hold a fake fixed denominator.
        self.assertEqual(second["submission"]["total"], 2)
        self.assertEqual(second["submission"]["done"], 1)
        self.assertFalse(second["submission"]["known"])

    def test_counts_separate_demo_verification_from_production(self):
        items = [
            self.item(1, "SUCCEEDED",
                      detection={"candidates": [1]}, job_id=1,
                      verification={"ok": True, "success_scope": "demo_contract",
                                    "provenance": "mock"}),
            self.item(2, "WAITING_SR", job_id=2,
                      cp_extra={"sr_status": "UNKNOWN", "unknown_checks": 2}),
            self.item(3, "MASK_READY",
                      cp_extra={"sr_fingerprint": "fp-3"}),
        ]
        counts = batch_snapshot.count_items(items)
        self.assertEqual(counts["verified"], 1)
        self.assertEqual(counts["verified_demo"], 1)
        self.assertEqual(counts["verified_production"], 0)
        self.assertEqual(counts["unknown_jobs"], 1)
        self.assertEqual(counts["waiting_sr"], 1)
        self.assertEqual(counts["in_flight_sr"], 1)
        snapshot = batch_snapshot.build_snapshot(self.goal(), items)
        self.assertEqual(snapshot["producer"]["prepared_count"], 1)
        self.assertEqual(snapshot["producer"]["preparing_count"], 1)
        self.assertEqual([job["ordinal"] for job in snapshot["active_jobs"]],
                         [2])

    def test_excluded_line_is_not_runtime_skipped(self):
        items = [
            self.item(1, "SKIPPED", excluded=True,
                      cp_extra={"exclusion_reason": "全部行已排除"}),
            self.item(2, "SKIPPED",
                      cp_extra={"exclusion_reason": "user_cancelled"}),
        ]
        counts = batch_snapshot.count_items(items)
        self.assertEqual(counts["excluded"], 1)
        self.assertEqual(counts["skipped_runtime"], 1)
        self.assertEqual(counts["cancelled"], 1)
        self.assertEqual(counts["eligible"], 1)

    def test_current_item_reports_representative_and_in_flight_counts(self):
        """Revision 7: multiple detecting Items show a representative + count."""
        items = [
            self.item(1, "DETECTING", cp_extra={"attempt": 2}),
            self.item(2, "DETECTING"),
            self.item(3, "MASK_READY",
                      cp_extra={"mask": {"sha256": "x", "overlay": {"path": "o"}}}),
        ]
        producer = batch_snapshot.build_snapshot(
            self.goal(), items)["producer"]
        self.assertEqual(producer["current"]["ordinal"], 1)
        self.assertEqual(producer["current"]["attempt"], 2)
        self.assertEqual(producer["detecting_count"], 2)
        self.assertEqual(producer["preparing_count"], 3)
        self.assertEqual(producer["prepared_count"], 1)

    def test_recent_jobs_orders_active_attention_then_settled(self):
        """The Panel queue shows in-flight first, then issues, then the tail.
        Items that never reached an external Job are not part of that queue."""
        failed = self.item(3, "FAILED")
        failed["error"] = "detector crashed"
        items = [
            self.item(1, "PENDING"),
            self.item(2, "SUCCEEDED", detection={"candidates": [1]}, job_id=20),
            failed,
            self.item(4, "WAITING_SR", job_id=21),
            self.item(5, "SUCCEEDED", detection={"candidates": [1]}, job_id=22),
        ]
        jobs = batch_snapshot.build_snapshot(self.goal(), items)["recent_jobs"]
        self.assertEqual([job["ordinal"] for job in jobs], [4, 3, 2, 5])
        self.assertEqual(jobs[0]["job_id"], 21)
        self.assertTrue(jobs[1]["error"])
        # A bounded tail keeps the payload small without hiding active work.
        self.assertEqual(
            [job["ordinal"] for job in batch_snapshot.recent_jobs(items, limit=2)],
            [4, 3])

    def test_monitoring_done_prefers_checkpoint_over_column(self):
        goal = self.goal({"monitoring_done": True})
        self.assertTrue(batch_snapshot.monitoring_done(goal))
        goal = self.goal({})
        goal["monitor_done"] = 1
        self.assertTrue(batch_snapshot.monitoring_done(goal))


# ---- store + REST ----------------------------------------------------------

class BatchSnapshotStoreTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.env = mock.patch.dict(os.environ, {
            "SR_BATCH_ARTIFACT_DIR": str(self.root / "artifacts"),
            "SR_AGENT_DB": str(self.root / "test.db"),
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        self.store = Store(self.root / "test.db")
        self.addCleanup(self.store.close)

    def make_goal(self, items):
        checkpoint = {
            "workflow": "batch", "mode": "demo", "manifest_ref": "m",
            "batch_control": {"pause_requested": False,
                              "cancel_requested": False},
            "control_revision": 0,
        }
        return self.store.create_batch_goal(
            title="batch", request_key="req-" + str(time.time_ns()),
            content_sha256="content", checkpoint=checkpoint, items=items,
            max_model_calls=0)

    def item(self, ordinal, status="PENDING", *, checkpoint=None):
        cp = checkpoint or {
            "phase": status, "attempt": 0, "name": f"scene-{ordinal}",
            "source_lines": [f"line-{ordinal}"],
            "target_image": {"path": f"D:/scenes/scene-{ordinal}/PAN.tif"},
        }
        return {
            "item_id": f"item-{ordinal}", "ordinal": ordinal,
            "name": f"scene-{ordinal}", "status": status,
            "checkpoint": cp, "resource_key": f"key-{ordinal}",
            "next_wake_at": None if status == "SKIPPED" else time.time(),
        }

    def update_item(self, goal_id, ordinal, *, status=None, checkpoint=None,
                    external_job_id=None):
        db = self.store._db()
        sets = ["version=version+1"]
        args = []
        if status is not None:
            sets.append("status=?"); args.append(status)
        if checkpoint is not None:
            sets.append("checkpoint=?")
            args.append(json.dumps(checkpoint, ensure_ascii=False))
        if external_job_id is not None:
            sets.append("external_job_id=?"); args.append(external_job_id)
        args.extend([goal_id, ordinal])
        db.execute(
            "UPDATE agent_goal_steps SET " + ",".join(sets) +
            " WHERE goal_id=? AND ordinal=?", args)
        db.commit()

    def client(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from backend.services import goal_runtime
        app = FastAPI()
        app.state.store = self.store
        app.include_router(goal_runtime.router)
        return TestClient(app)

    def snapshot(self, client, goal_id):
        response = client.get(f"/api/goals/{goal_id}/batch-snapshot")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_snapshot_is_consistent_and_lists_excluded_items(self):
        excluded = {
            "phase": "SKIPPED", "attempt": 0, "name": "scene-3",
            "source_lines": [], "exclusion_reason": "全部行已排除",
        }
        goal = self.make_goal([
            self.item(1), self.item(2),
            self.item(3, "SKIPPED", checkpoint=excluded),
        ])
        gid = goal["goal_id"]
        with self.client() as client:
            snapshot = self.snapshot(client, gid)
        self.assertEqual(snapshot["contract_version"], "batch_snapshot.v1")
        self.assertEqual(snapshot["goal"]["kind"], "batch")
        self.assertEqual(snapshot["goal"]["control_revision"], 0)
        self.assertFalse(snapshot["goal"]["monitoring_done"])
        self.assertEqual(snapshot["counts"]["total"], 3)
        self.assertEqual(snapshot["counts"]["eligible"], 2)
        self.assertEqual(snapshot["counts"]["excluded"], 1)
        self.assertEqual(snapshot["progress"]["detection"]["total"], 2)
        self.assertFalse(snapshot["progress"]["detection"]["known"])
        events = self.store.list_goal_events(gid)
        self.assertEqual(snapshot["goal"]["last_seq"], events[-1]["seq"])
        self.assertFalse(snapshot["report"]["available"])

    def test_snapshot_projects_current_item_and_active_jobs(self):
        goal = self.make_goal([self.item(1), self.item(2), self.item(3)])
        gid = goal["goal_id"]
        self.update_item(gid, 1, status="DETECTING", checkpoint={
            "phase": "DETECTING", "attempt": 1, "source_lines": ["line-1"],
            "detection": {"candidates": [{"x": 1}, {"x": 2}]},
            "target_image": {"path": "D:/scenes/scene-1/PAN.tif"},
        })
        self.update_item(gid, 2, status="WAITING_SR", external_job_id=42,
                         checkpoint={
                             "phase": "WAITING_SR", "attempt": 1,
                             "source_lines": ["line-2"], "job_id": 42,
                             "sr_status": "RUNNING",
                             "submitted_at": 3.0,
                             "target_image": {"path": "D:/scenes/scene-2/PAN.tif"},
                         })
        self.update_item(gid, 3, status="DETECTING", checkpoint={
            "phase": "DETECTING", "attempt": 1, "source_lines": ["line-3"],
            "detection": {"candidates": []},
            "target_image": {"path": "D:/scenes/scene-3/PAN.tif"},
        })
        with self.client() as client:
            snapshot = self.snapshot(client, gid)
        self.assertEqual(snapshot["producer"]["current"]["ordinal"], 1)
        self.assertEqual(snapshot["producer"]["detecting_count"], 2)
        self.assertEqual(snapshot["counts"]["needs_repair"], 1)
        self.assertEqual(snapshot["counts"]["detected"], 2)
        self.assertEqual(snapshot["counts"]["in_flight_sr"], 1)
        jobs = snapshot["active_jobs"]
        self.assertEqual([job["ordinal"] for job in jobs], [2])
        self.assertEqual(jobs[0]["job_id"], 42)
        self.assertFalse(jobs[0]["unknown"])

    def test_report_endpoint_uses_server_side_path(self):
        goal = self.make_goal([self.item(1)])
        gid = goal["goal_id"]
        report_dir = self.root / "artifacts" / "reports"
        report_dir.mkdir(parents=True)
        (report_dir / f"{gid}.json").write_text(
            json.dumps({"provenance": "mock", "counts": {"SUCCEEDED": 1}}),
            encoding="utf-8")
        with self.client() as client:
            snapshot = self.snapshot(client, gid)
            self.assertTrue(snapshot["report"]["available"])
            response = client.get(f"/api/goals/{gid}/batch-report")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["provenance"], "mock")
            missing = client.get("/api/goals/does-not-exist/batch-report")
            self.assertEqual(missing.status_code, 404)

    def test_list_goals_filters_by_kind_before_limit(self):
        batch = self.make_goal([self.item(1)])
        self.store.create_goal(title="chat")
        with self.client() as client:
            batches = client.get("/api/goals", params={"kind": "batch"}).json()
            chats = client.get("/api/goals", params={"kind": "chat"}).json()
        self.assertEqual([g["goal_id"] for g in batches], [batch["goal_id"]])
        self.assertEqual([g["title"] for g in chats], ["chat"])

    def test_snapshot_404s_for_an_ordinary_goal(self):
        chat = self.store.create_goal(title="chat")
        with self.client() as client:
            response = client.get(
                f"/api/goals/{chat['goal_id']}/batch-snapshot")
        self.assertEqual(response.status_code, 404)


class BatchEventPageTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.store = Store(self.root / "test.db")
        self.addCleanup(self.store.close)
        self.goal = self.store.create_goal(title="events")

    def add(self, count):
        for _ in range(count):
            self.store.append_goal_event(
                self.goal["goal_id"], "batch_phase_changed", {"ok": True})

    def page(self, **params):
        return self.store.list_goal_events_page(self.goal["goal_id"], **params)

    def test_forward_pages_never_skip_or_duplicate(self):
        self.add(25)
        first = self.page(after=0, limit=10)
        self.assertEqual([e["seq"] for e in first["events"]],
                         list(range(1, 11)))
        self.assertTrue(first["has_more"])
        self.assertEqual(first["next_after"], 10)
        # ``create_goal`` appends ``goal_created`` as seq 1.
        self.assertEqual(first["last_seq"], 26)
        second = self.page(after=first["next_after"], limit=10)
        self.assertEqual([e["seq"] for e in second["events"]],
                         list(range(11, 21)))
        self.assertTrue(second["has_more"])
        third = self.page(after=second["next_after"], limit=10)
        self.assertEqual([e["seq"] for e in third["events"]],
                         list(range(21, 27)))
        self.assertFalse(third["has_more"])
        self.assertEqual(third["next_after"], 26)

    def test_backward_pages_are_recent_first_and_keep_ascending_order(self):
        self.add(25)
        newest = self.page(before=26, limit=10)
        self.assertEqual([e["seq"] for e in newest["events"]],
                         list(range(16, 26)))
        self.assertTrue(newest["has_earlier"])
        self.assertEqual(newest["next_before"], 16)
        older = self.page(before=newest["next_before"], limit=10)
        self.assertEqual([e["seq"] for e in older["events"]],
                         list(range(6, 16)))
        oldest = self.page(before=older["next_before"], limit=10)
        self.assertEqual([e["seq"] for e in oldest["events"]],
                         list(range(1, 6)))
        self.assertFalse(oldest["has_earlier"])

    def test_both_cursors_are_rejected(self):
        with self.assertRaises(ValueError):
            self.page(after=1, before=5)


class BatchControlTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.env = mock.patch.dict(os.environ, {
            "SR_BATCH_ARTIFACT_DIR": str(self.root / "artifacts"),
            "SR_AGENT_DB": str(self.root / "test.db"),
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        self.store = Store(self.root / "test.db")
        self.addCleanup(self.store.close)
        self.goal = self.store.create_batch_goal(
            title="batch", request_key="req-control", content_sha256="content",
            checkpoint={
                "workflow": "batch", "mode": "demo", "manifest_ref": "m",
                "batch_control": {"pause_requested": False,
                                  "cancel_requested": False},
                "control_revision": 0,
            },
            items=[{
                "item_id": "item-1", "ordinal": 1, "name": "scene-1",
                "status": "PENDING", "resource_key": "key-1",
                "next_wake_at": time.time(),
                "checkpoint": {"phase": "PENDING", "attempt": 0,
                               "source_lines": ["line-1"]},
            }], max_model_calls=0)
        self.gid = self.goal["goal_id"]

    def client(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from backend.services import goal_runtime
        app = FastAPI()
        app.state.store = self.store
        app.include_router(goal_runtime.router)
        return TestClient(app)

    def set_goal(self, **fields):
        db = self.store._db()
        sets = ",".join(f"{key}=?" for key in fields)
        db.execute(f"UPDATE agent_goals SET {sets} WHERE goal_id=?",
                   [*fields.values(), self.gid])
        db.commit()

    def test_pause_uses_control_revision_not_the_tick_version(self):
        """The scheduler bumps goal.version every tick; control must not."""
        self.set_goal(version=17)
        with self.client() as client:
            response = client.post(
                f"/api/goals/{self.gid}/pause",
                json={"expected_version": 0})
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertTrue(body["changed"])
        self.assertEqual(body["control_revision"], 1)
        goal = self.store.get_goal(self.gid)
        self.assertEqual(goal["status"], "PAUSED")
        self.assertEqual(goal["checkpoint"]["control_revision"], 1)
        self.assertTrue(
            goal["checkpoint"]["batch_control"]["pause_requested"])
        events = self.store.list_goal_events(self.gid)
        self.assertEqual(events[-1]["event_type"], "batch_user_control")
        self.assertEqual(events[-1]["payload"]["action"], "pause")

    def test_monitor_checkpoint_write_does_not_consume_the_revision(self):
        """Revision 1: a Monitor ``monitoring_done`` write is not user control.

        The test performs the same read-modify-write the Monitor does between
        the Panel's snapshot read and its Pause request.  The revision the
        Panel holds must still be current.
        """
        with self.client() as client:
            before = client.get(
                f"/api/goals/{self.gid}/batch-snapshot").json()
            revision = before["goal"]["control_revision"]
            goal = self.store.get_goal(self.gid)
            cp = dict(goal["checkpoint"])
            cp.update({"monitoring_done": False, "sr_status": "RUNNING",
                       "monitor_write": True})
            self.set_goal(checkpoint=json.dumps(cp), monitor_done=1)
            response = client.post(
                f"/api/goals/{self.gid}/pause",
                json={"expected_version": revision})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["control_revision"], revision + 1)

    def test_stale_revision_conflicts_and_reload_sees_new_value(self):
        with self.client() as client:
            first = client.post(f"/api/goals/{self.gid}/pause",
                                json={"expected_version": 0})
            self.assertEqual(first.status_code, 200)
            stale = client.post(f"/api/goals/{self.gid}/pause",
                                json={"expected_version": 0})
            self.assertEqual(stale.status_code, 409)
            detail = stale.json()["detail"]
            self.assertEqual(detail["reason"], "stale_revision")
            self.assertEqual(detail["control_revision"], 1)
            snapshot = client.get(
                f"/api/goals/{self.gid}/batch-snapshot").json()
            self.assertEqual(snapshot["goal"]["control_revision"], 1)
            self.assertEqual(snapshot["goal"]["status"], "PAUSED")

    def test_resume_requires_the_paused_state_and_is_idempotent(self):
        with self.client() as client:
            noop = client.post(f"/api/goals/{self.gid}/resume",
                               json={"expected_version": 0})
            self.assertEqual(noop.status_code, 200)
            self.assertFalse(noop.json()["changed"])
            self.assertEqual(noop.json()["control_revision"], 0)
            client.post(f"/api/goals/{self.gid}/pause",
                        json={"expected_version": 0})
            resumed = client.post(f"/api/goals/{self.gid}/resume",
                                  json={"expected_version": 1})
            self.assertEqual(resumed.status_code, 200)
            self.assertEqual(resumed.json()["control_revision"], 2)
            self.assertEqual(self.store.get_goal(self.gid)["status"], "QUEUED")
            again = client.post(f"/api/goals/{self.gid}/resume",
                                json={"expected_version": 2})
            self.assertEqual(again.status_code, 200)
            self.assertFalse(again.json()["changed"])

    def test_cancel_is_terminal_and_not_immediately_repeatable(self):
        with self.client() as client:
            first = client.post(f"/api/goals/{self.gid}/cancel",
                                json={"expected_version": 0})
            self.assertEqual(first.status_code, 200)
            self.assertEqual(self.store.get_goal(self.gid)["status"],
                             "CANCELLED")
            again = client.post(f"/api/goals/{self.gid}/cancel",
                                json={"expected_version": 1})
            self.assertEqual(again.status_code, 200)
            self.assertFalse(again.json()["changed"])

    def test_missing_or_invalid_revision_is_rejected(self):
        with self.client() as client:
            missing = client.post(f"/api/goals/{self.gid}/pause", json={})
            self.assertEqual(missing.status_code, 400)
            self.assertEqual(missing.json()["detail"]["reason"],
                             "missing_revision")
            wrong = client.post(f"/api/goals/{self.gid}/pause",
                                json={"expected_version": "0"})
            self.assertEqual(wrong.status_code, 400)

    def test_terminal_batch_cannot_be_paused(self):
        self.set_goal(status="COMPLETED", phase="done")
        with self.client() as client:
            response = client.post(f"/api/goals/{self.gid}/pause",
                                   json={"expected_version": 0})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"]["reason"], "invalid_state")

    def test_store_control_rejects_unknown_action(self):
        with self.assertRaises(ValueError):
            self.store.batch_control(self.gid, "explode", 0)

    def test_monitor_event_does_not_change_control_revision(self):
        self.store.batch_control(self.gid, "pause", 0)
        self.store.append_goal_event(
            self.gid, "batch_control_changed",
            {"status": "PAUSED", "monitoring_done": True})
        self.assertEqual(
            batch_snapshot.control_revision(self.store.get_goal(self.gid)), 1)
        events = self.store.list_goal_events(self.gid)
        self.assertEqual(
            [e["event_type"] for e in events[-2:]],
            ["batch_user_control", "batch_control_changed"])


class StreamTerminalTests(unittest.TestCase):
    def test_batch_stream_waits_for_monitor_settlement(self):
        from backend.services.goal_runtime import _stream_finished
        cancelled = {"kind": "batch", "status": "CANCELLED", "checkpoint": {}}
        self.assertFalse(_stream_finished(cancelled))
        cancelled["checkpoint"]["monitoring_done"] = True
        self.assertTrue(_stream_finished(cancelled))
        self.assertTrue(_stream_finished(
            {"kind": "chat", "status": "COMPLETED", "checkpoint": {}}))
        self.assertFalse(_stream_finished(
            {"kind": "batch", "status": "RUNNING", "checkpoint": {}}))


if __name__ == "__main__":
    unittest.main()
