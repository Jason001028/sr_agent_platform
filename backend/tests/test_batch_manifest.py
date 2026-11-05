"""PR B: frozen QC TXT manifests, batch Goal/Item atomicity and API contract."""

from __future__ import annotations

import asyncio
import base64
import json
import os
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from backend.services import batch_manifest, goal_runtime, goal_worker
from backend.services.store import BatchConflict, Store
from backend.tests import allowed_roots_env

BASE = "JXGF07D03_PMS_20260101010101_200516571_101_{scene:04d}_001_L1"
DESC = "模糊 问题类型:模糊 影像类型:pan 行列号:12,34"


class BatchManifestTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.env = {
            **allowed_roots_env(self.root),
            "SR_SCENE_PATH_TEMPLATE": str(self.root) + "/{name}",
            "SR_AGENT_DB": str(self.root / "test.db"),
            "SR_BATCH_ARTIFACT_DIR": str(self.root / "artifacts"),
        }
        self.patch = mock.patch.dict(os.environ, self.env)
        self.patch.start()
        self.store = Store(self.root / "test.db")

    def tearDown(self):
        self.store.close()
        self.patch.stop()
        self.tmp.cleanup()

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

    def import_text(self, text: str, **kwargs) -> dict:
        return batch_manifest.import_manifest(text=text, **kwargs)

    def test_review_strategy_is_frozen_into_created_items_and_rest_cas(self):
        name = self.scene(803)
        app = FastAPI()
        app.state.store = self.store
        app.include_router(goal_runtime.router)
        with TestClient(app) as client:
            imported = client.post("/api/goals/batch-imports", json={
                "text": "", "strategy": "review", "sources": [{"path": str(self.root / (name + "_PAN") / "PAN.tif")}]})
            self.assertEqual(imported.status_code, 200, imported.text)
            created = client.post("/api/goals", json={"manifest_ref": imported.json()["manifest_ref"], "request_key": "review-rest"})
            self.assertEqual(created.status_code, 200, created.text)
            gid = created.json()["goal_id"]
            item = self.store.list_goal_items(gid)[0]
            self.assertTrue(item["checkpoint"]["review_required"])
            endpoint = f"/api/goals/{gid}/items/{item['item_id']}/review"
            self.assertEqual(client.post(endpoint, json={"decision": "approve"}).status_code, 400)
            # Pending input has not been detected: it cannot be pre-approved.
            self.assertEqual(client.post(endpoint, json={"expected_version": item["version"], "decision": "approve"}).status_code, 409)

    def test_explicit_viewer_sources_never_infer_names(self):
        name = self.scene(801)
        path = self.root / (name + "_PAN") / "PAN.tif"
        with mock.patch.object(batch_manifest.scene_resolve, "resolve_scene_dir",
                               side_effect=AssertionError("must not infer")):
            result = self.import_text("", sources=[{"path": str(path), "name": "Viewer PAN"}], strategy="review")
        self.assertFalse(result["rejected"])
        frozen = batch_manifest.load_manifest(result["manifest_ref"])
        self.assertEqual(frozen["items"][0]["target_image"]["path"], path.as_posix())
        self.assertEqual(frozen["config"]["strategy"], "review")
        automatic = self.import_text("", sources=[{"path": str(path), "name": "Viewer PAN"}])
        self.assertNotEqual(automatic["manifest_ref"], result["manifest_ref"])

    def test_explicit_viewer_sources_reject_non_pan_and_duplicate_scene(self):
        name = self.scene(802)
        directory = self.root / (name + "_PAN")
        source = {"path": str(directory / "PAN.tif"), "name": name}
        duplicate = self.import_text("", sources=[source, source])
        self.assertTrue(duplicate["rejected"])
        with self.assertRaises(batch_manifest.ManifestConflict):
            batch_manifest.plan_goal_items(batch_manifest.load_manifest(duplicate["manifest_ref"]), [])
        wrong = self.import_text("", sources=[{"path": str(directory / (directory.name + "_NOSR.tif"))}])
        self.assertTrue(wrong["rejected"])
        with self.assertRaises(batch_manifest.ManifestError):
            self.import_text("", sources="not an array")
        with self.assertRaises(batch_manifest.ManifestError):
            self.import_text("", strategy={})

    def test_fifty_line_freeze_is_content_addressed(self):
        names = [self.scene(i) for i in range(1, 51)]
        result = self.import_text(self.qc(*names))
        self.assertFalse(result["rejected"])
        self.assertEqual(result["counts"]["items"], 50)
        self.assertEqual(result["counts"]["errors"], 0)
        self.assertEqual(
            [item["ordinal"] for item in result["items"]], list(range(1, 51)))
        frozen = batch_manifest.load_manifest(result["manifest_ref"])
        self.assertEqual(len(frozen["items"]), 50)
        self.assertTrue(
            (batch_manifest.artifact_root() / result["manifest_ref"]
             / "original.bin").is_file())
        # Same bytes freeze to the same key; no second directory is invented.
        again = self.import_text(self.qc(*names))
        self.assertEqual(again["manifest_ref"], result["manifest_ref"])

    def test_batch_goals_do_not_starve_ordinary_goals(self):
        chat = self.store.create_goal(title="ordinary", status="QUEUED")
        checkpoint = {"workflow": batch_manifest.WORKFLOW}
        for index in range(200):
            self.store.create_batch_goal(
                title=f"batch-{index}", request_key=f"batch-{index}",
                content_sha256=f"{index:064x}", checkpoint=checkpoint,
                items=[{
                    "item_id": f"item-{index}", "ordinal": 1,
                    "name": f"scene-{index}", "status": "PENDING",
                    "checkpoint": {"phase": "PENDING"},
                }])
        db = self.store._db()
        db.execute("UPDATE agent_goals SET updated_at=updated_at+1000 "
                   "WHERE kind='batch'")
        db.commit()

        # The regression: 200 newer Batch Goals fill the unfiltered window and
        # the ordinary Goal is invisible to the worker.
        unfiltered = self.store.list_goals(limit=200, statuses={"QUEUED"})
        self.assertEqual(len(unfiltered), 200)
        self.assertNotIn(chat["goal_id"], {g["goal_id"] for g in unfiltered})

        # Filtering inside SQL, before LIMIT, keeps normal scheduling alive.
        filtered = self.store.list_goals(
            limit=200, statuses={"QUEUED"}, exclude_kinds=("batch",))
        self.assertEqual([g["goal_id"] for g in filtered], [chat["goal_id"]])
        self.assertEqual(filtered[0]["kind"], "chat")
        self.assertEqual(self.store.get_goal(
            unfiltered[0]["goal_id"])["kind"], "batch")

    def test_worker_requests_non_batch_candidates(self):
        class RecordingStore:
            def __init__(self):
                self.kwargs = None

            def list_goals(self, **kwargs):
                self.kwargs = kwargs
                return []

        state = SimpleNamespace(store=RecordingStore())
        asyncio.run(goal_worker.goal_tick(state))
        self.assertEqual(state.store.kwargs, {
            "limit": 200,
            "statuses": goal_worker.SCHEDULED,
            "exclude_kinds": ("batch",),
        })

    def test_tampered_manifest_and_corrupt_source_are_rejected(self):
        name = self.scene(1)
        result = self.import_text(self.qc(name))
        ref = result["manifest_ref"]
        directory = batch_manifest.artifact_root() / ref
        manifest_path = directory / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(manifest["config"]["max_inflight_sr_jobs"], 3)

        # Keep the self-reported hash; change execution config. A loader that
        # only compares the reported hash would accept this.
        manifest["config"]["max_inflight_sr_jobs"] = 99
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaises(batch_manifest.ManifestError) as caught:
            batch_manifest.load_manifest(ref)
        self.assertIn("摘要不一致", str(caught.exception))

        # Re-importing the same bytes must validate the existing directory,
        # not silently reuse or overwrite corrupted content.
        with self.assertRaises(batch_manifest.ManifestError):
            self.import_text(self.qc(name))
        self.assertEqual(
            json.loads(manifest_path.read_text(encoding="utf-8"))
            ["config"]["max_inflight_sr_jobs"], 99)

    def test_corrupt_original_document_is_rejected(self):
        name = self.scene(1)
        ref = self.import_text(self.qc(name))["manifest_ref"]
        original = batch_manifest.artifact_root() / ref / "original.bin"
        original.write_bytes(original.read_bytes() + b"\n")
        with self.assertRaises(batch_manifest.ManifestError) as caught:
            batch_manifest.load_manifest(ref)
        self.assertIn("原始清单工件", str(caught.exception))

    def test_gbk_and_bom_are_decoded_like_the_browser_importer(self):
        name = self.scene(1)
        raw = self.qc(name).encode("gbk")
        result = batch_manifest.import_manifest(data=raw, encoding="auto")
        self.assertEqual(result["encoding"], "gbk")
        self.assertEqual(result["counts"]["items"], 1)
        bom = b"\xef\xbb\xbf" + self.qc(name).encode("utf-8")
        result = batch_manifest.import_manifest(data=bom)
        self.assertEqual(result["encoding"], "utf-8")
        self.assertEqual(result["counts"]["items"], 1)

    def test_bom_with_invalid_utf8_is_a_400_not_a_server_error(self):
        raw = b"\xef\xbb\xbf\xff"
        with self.assertRaises(batch_manifest.ManifestError):
            batch_manifest.decode_bytes(raw, "auto")
        app = FastAPI()
        app.state.store = self.store
        app.include_router(goal_runtime.router)
        with TestClient(app) as client:
            response = client.post(
                "/api/goals/batch-imports",
                json={"content_base64": base64.b64encode(raw).decode("ascii")})
        self.assertEqual(response.status_code, 400, response.text)
        self.assertIn("UTF-8", response.text)

    def test_invalid_and_duplicate_lines_are_numbered_and_block_creation(self):
        good = self.scene(1)
        text = (
            f"{good}\t{DESC}\t张三\n"          # line 1
            f"{good}\t{DESC}\t李四\n"          # line 2 duplicate
            "这一行没有制表符\n"                  # line 3 invalid
            f"{good}\t修复通过\n"               # line 4 status line
        )
        result = self.import_text(text)
        self.assertTrue(result["rejected"])
        self.assertEqual(result["counts"]["items"], 1)
        diagnostics = {(d["line"], d["code"]) for d in result["diagnostics"]}
        self.assertIn((2, "duplicate_name"), diagnostics)
        self.assertIn((3, "invalid_columns"), diagnostics)
        self.assertIn((4, "status_line_ignored"), diagnostics)
        manifest = batch_manifest.load_manifest(result["manifest_ref"])
        with self.assertRaises(batch_manifest.ManifestConflict) as caught:
            batch_manifest.plan_goal_items(manifest, [])
        self.assertEqual(
            {d["line"] for d in caught.exception.diagnostics}, {2, 3})
        # Explicitly walking the bad lines keeps identity, not silence.
        planned = batch_manifest.plan_goal_items(
            manifest, [{"line": 2, "reason": "重复"}, {"line": 3, "reason": "残行"}])
        self.assertEqual(planned[0]["status"], "PENDING")
        self.assertEqual(planned[0]["source_lines"], [1])
        skipped = batch_manifest.plan_goal_items(
            manifest, [{"line": 1, "reason": "重复"},
                       {"line": 2, "reason": "重复"},
                       {"line": 3, "reason": "残行"}])
        self.assertEqual(skipped[0]["status"], "SKIPPED")
        self.assertEqual(skipped[0]["exclusion_reason"], "重复")

    def test_two_names_for_one_scene_are_a_duplicate_scene(self):
        stem = self.scene(1)
        # The QC name may or may not carry the product segment; both forms
        # point at the same validated directory and must not become two Items.
        result = self.import_text(self.qc(stem, stem + "_PAN"))
        self.assertTrue(result["rejected"])
        self.assertEqual(result["counts"]["items"], 1)
        diagnostic = next(d for d in result["diagnostics"]
                          if d["code"] == "duplicate_scene")
        self.assertEqual(diagnostic["line"], 2)
        self.assertEqual(result["items"][0]["source_lines"], [1, 2])
        manifest = batch_manifest.load_manifest(result["manifest_ref"])
        planned = batch_manifest.plan_goal_items(manifest, [2])
        self.assertEqual(planned[0]["source_lines"], [1])
        self.assertEqual(len(planned), 1)

    def test_unresolved_pair_is_a_line_diagnostic(self):
        stem = self.scene(1)
        directory = self.root / (stem + "_PAN")
        (directory / "PAN_old_2.tif").write_bytes(b"old")
        result = self.import_text(self.qc(stem))
        self.assertTrue(result["rejected"])
        self.assertEqual(result["diagnostics"][0]["code"], "scene_unresolved")
        self.assertIn("PAN 备份", result["diagnostics"][0]["message"])

    def test_store_idempotency_is_content_sensitive_and_atomic(self):
        name = self.scene(1)
        manifest = batch_manifest.load_manifest(
            self.import_text(self.qc(name))["manifest_ref"])
        items = batch_manifest.plan_goal_items(manifest, [])
        checkpoint = {"workflow": batch_manifest.WORKFLOW,
                      "manifest_ref": manifest["manifest_sha256"]}
        step_items = [{
            "item_id": items[0]["item_id"], "ordinal": 1,
            "name": items[0]["name"], "status": "PENDING",
            "checkpoint": {"phase": "PENDING"},
            "resource_key": items[0]["target_image"]["image_key"],
        }]
        first = self.store.create_batch_goal(
            title="batch", request_key="key-1", content_sha256="a" * 64,
            checkpoint=checkpoint, items=step_items)
        second = self.store.create_batch_goal(
            title="batch", request_key="key-1", content_sha256="a" * 64,
            checkpoint=checkpoint, items=step_items)
        self.assertEqual(first["goal_id"], second["goal_id"])
        self.assertEqual(len(self.store.list_goals()), 1)
        with self.assertRaises(BatchConflict):
            self.store.create_batch_goal(
                title="batch", request_key="key-1", content_sha256="b" * 64,
                checkpoint=checkpoint, items=step_items)

        # A failure while writing Items rolls the Goal back, so the same key
        # can be retried instead of leaving a runnable half-batch.
        bad = [{**step_items[0], "status": "BROKEN"}]
        with self.assertRaises(ValueError):
            self.store.create_batch_goal(
                title="half", request_key="key-2", content_sha256="c" * 64,
                checkpoint=checkpoint, items=bad)
        self.assertIsNone(self.store.get_goal_by_request_key("key-2"))
        retried = self.store.create_batch_goal(
            title="half", request_key="key-2", content_sha256="c" * 64,
            checkpoint=checkpoint, items=step_items)
        self.assertEqual(len(self.store.list_goal_items(retried["goal_id"])), 1)

    def test_old_step_rows_migrate_without_becoming_batch_items(self):
        db_path = self.root / "old.db"
        db = sqlite3.connect(db_path)
        db.executescript(
            "CREATE TABLE agent_goals ("
            "goal_id TEXT PRIMARY KEY, session_id TEXT, request_key TEXT UNIQUE,"
            "title TEXT NOT NULL, status TEXT NOT NULL,"
            "phase TEXT NOT NULL DEFAULT 'queued',"
            "checkpoint TEXT NOT NULL DEFAULT '{}',"
            "version INTEGER NOT NULL DEFAULT 0, lease_owner TEXT, lease_until REAL,"
            "next_wake_at REAL, max_rounds INTEGER NOT NULL DEFAULT 20,"
            "rounds_used INTEGER NOT NULL DEFAULT 0,"
            "max_model_calls INTEGER NOT NULL DEFAULT 100,"
            "model_calls_used INTEGER NOT NULL DEFAULT 0, error TEXT,"
            "created_at REAL NOT NULL, updated_at REAL NOT NULL, finished_at REAL,"
            "lease_token TEXT);"
            "CREATE TABLE agent_goal_steps ("
            "step_id TEXT PRIMARY KEY, goal_id TEXT NOT NULL, ordinal INTEGER NOT NULL,"
            "name TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'PENDING', tool_name TEXT,"
            "external_job_id INTEGER, verification TEXT NOT NULL DEFAULT '{}', error TEXT,"
            "created_at REAL NOT NULL, updated_at REAL NOT NULL, UNIQUE(goal_id, ordinal));")
        db.execute(
            "INSERT INTO agent_goals(goal_id,title,status,created_at,updated_at) "
            "VALUES('g1','old','QUEUED',1,1)")
        batch_checkpoint = json.dumps({
            "workflow": batch_manifest.WORKFLOW,
            "batch_content_sha256": "d" * 64,
        })
        db.executemany(
            "INSERT INTO agent_goals(goal_id,title,status,checkpoint,"
            "created_at,updated_at) VALUES(?,?,?,?,?,?)",
            [(f"b{index}", f"batch-{index}", "QUEUED", batch_checkpoint,
              10 + index, 10 + index) for index in range(200)])
        db.execute(
            "INSERT INTO agent_goal_steps(step_id,goal_id,ordinal,name,status,"
            "verification,created_at,updated_at) VALUES('s1','g1',1,'old','SUCCEEDED','{}',1,1)")
        db.commit()
        db.close()
        old_store = Store(db_path)
        try:
            self.assertEqual(old_store.get_goal("g1")["kind"], "chat")
            self.assertEqual(old_store.get_goal("b0")["kind"], "batch")
            # The 200 old Batch rows are newer than the ordinary Goal. The
            # unfiltered window hides it; the pre-LIMIT kind filter restores it.
            unfiltered = old_store.list_goals(
                limit=200, statuses={"QUEUED"})
            self.assertEqual(len(unfiltered), 200)
            self.assertNotIn("g1", {g["goal_id"] for g in unfiltered})
            self.assertEqual(
                [g["goal_id"] for g in old_store.list_goals(
                    limit=200, statuses={"QUEUED"}, exclude_kinds=("batch",))],
                ["g1"])
            steps = old_store.list_goal_steps("g1")
            self.assertEqual(steps[0]["kind"], "tool")
            self.assertEqual(steps[0]["checkpoint"], {})
            self.assertEqual(steps[0]["version"], 0)
            self.assertEqual(old_store.list_goal_items("g1"), [])
            # The generic agent still writes into the migrated table.
            old_store.put_goal_step("g1", ordinal=2, name="search_scenes")
            self.assertEqual(old_store.list_goal_steps("g1")[1]["kind"], "tool")
        finally:
            old_store.close()

        # A DB that already ran the earlier chat-only migration must also be
        # repaired on the next open, not only when the kind column is added.
        db = sqlite3.connect(db_path)
        db.execute("UPDATE agent_goals SET kind='chat' WHERE goal_id='b0'")
        db.commit()
        db.close()
        repaired = Store(db_path)
        try:
            self.assertEqual(repaired.get_goal("b0")["kind"], "batch")
        finally:
            repaired.close()

    def test_api_import_create_idempotency_and_items_projection(self):
        names = [self.scene(1), self.scene(2)]
        app = FastAPI()
        app.state.store = self.store
        app.include_router(goal_runtime.router)
        with TestClient(app) as client:
            imported = client.post("/api/goals/batch-imports",
                                   json={"text": self.qc(*names)})
            self.assertEqual(imported.status_code, 200, imported.text)
            body = imported.json()
            self.assertFalse(body["rejected"])
            self.assertEqual(len(body["items"]), 2)
            ref = body["manifest_ref"]

            created = client.post(
                "/api/goals", json={"manifest_ref": ref, "mode": "demo"},
                headers={"Idempotency-Key": "same"})
            self.assertEqual(created.status_code, 200, created.text)
            goal = created.json()
            self.assertEqual(goal["status"], "QUEUED")
            self.assertEqual(goal["checkpoint"]["workflow"],
                             batch_manifest.WORKFLOW)
            self.assertEqual(
                goal["checkpoint"]["batch_content_sha256"],
                batch_manifest.canonical_hash({
                    "manifest_ref": ref,
                    "manifest_sha256": goal["checkpoint"]["manifest_sha256"],
                    "exclusions": [],
                    "mode": "demo",
                    "title": "批量影像修复 · 2 景",
                }))
            again = client.post(
                "/api/goals", json={"manifest_ref": ref, "mode": "demo"},
                headers={"Idempotency-Key": "same"})
            self.assertEqual(again.json()["goal_id"], goal["goal_id"])
            self.assertEqual(len(self.store.list_goals()), 1)

            items = client.get(f"/api/goals/{goal['goal_id']}/items")
            self.assertEqual(items.status_code, 200)
            self.assertEqual(len(items.json()["items"]), 2)
            self.assertEqual(items.json()["workflow"], batch_manifest.WORKFLOW)

            # Reusing the key with another manifest is a 409, not a silent
            # second batch under the same idempotency identity.
            other = client.post("/api/goals/batch-imports",
                                json={"text": self.qc(self.scene(3))}).json()
            conflict = client.post(
                "/api/goals", json={"manifest_ref": other["manifest_ref"],
                                    "mode": "demo"},
                headers={"Idempotency-Key": "same"})
            self.assertEqual(conflict.status_code, 409)
            self.assertIn("different batch content", conflict.text)

    def test_api_conflicting_exclusions_return_diagnostics(self):
        name = self.scene(1)
        text = self.qc(name) + f"{name}\t{DESC}\t李四\n"
        app = FastAPI()
        app.state.store = self.store
        app.include_router(goal_runtime.router)
        with TestClient(app) as client:
            imported = client.post("/api/goals/batch-imports",
                                   json={"text": text}).json()
            self.assertTrue(imported["rejected"])
            blocked = client.post(
                "/api/goals", json={"manifest_ref": imported["manifest_ref"]},
                headers={"Idempotency-Key": "dup"})
            self.assertEqual(blocked.status_code, 409)
            self.assertEqual(blocked.json()["detail"]["diagnostics"][0]["line"], 2)
            accepted = client.post(
                "/api/goals",
                json={"manifest_ref": imported["manifest_ref"],
                      "exclusions": [{"line": 2, "reason": "重复记录"}]},
                headers={"Idempotency-Key": "dup"})
            self.assertEqual(accepted.status_code, 200, accepted.text)
            self.assertEqual(len(self.store.list_goal_items(
                accepted.json()["goal_id"])), 1)


if __name__ == "__main__":
    unittest.main()
