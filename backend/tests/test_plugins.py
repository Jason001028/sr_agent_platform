"""Plugin catalog, per-machine state and control-plane API tests."""

import os
import tempfile
import unittest
from unittest import mock

from fastapi.testclient import TestClient

from backend.api.app import create_app
from backend.services.plugins import (LOCAL_MACHINE_ID, ensure_machine,
                                      request_operation, sync_catalog)
from backend.services.store import Store


class TestPluginStore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(os.path.join(self.tmp.name, "plugins.sqlite"))
        sync_catalog(self.store)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_local_action_completes_and_remote_action_is_pending(self):
        ensure_machine(self.store, LOCAL_MACHINE_ID, default_state="enabled")
        local = request_operation(self.store, LOCAL_MACHINE_ID, "run_sr",
                                  action="disable")
        self.assertEqual(local["status"], "COMPLETED")
        self.assertEqual(
            self.store.get_machine_plugin(LOCAL_MACHINE_ID, "run_sr")["actual_state"],
            "disabled")

        remote = request_operation(self.store, "node-1", "run_sr",
                                   action="install")
        self.assertEqual(remote["status"], "PENDING")
        claimed = self.store.claim_plugin_operations("node-1")
        self.assertEqual(len(claimed), 1)
        self.assertEqual(claimed[0]["status"], "RUNNING")
        self.assertEqual(len(self.store.list_plugin_operations(
            machine_id="node-1", status="PENDING")), 0)

    def test_operation_result_updates_actual_state(self):
        op = request_operation(self.store, "node-2", "run_sr", action="enable")
        done = self.store.finish_plugin_operation(
            op["operation_id"], status="COMPLETED", actual_state="enabled",
            result={"agent": "node-2"})
        self.assertEqual(done["status"], "COMPLETED")
        self.assertEqual(self.store.get_machine_plugin(
            "node-2", "run_sr")["actual_state"], "enabled")

    def test_llm_usage_summary_preserves_provider_counts(self):
        sid = self.store.create_session()
        self.store.append_llm_usage(
            sid, turn=1, call_index=1, prompt_tokens=120,
            completion_tokens=30, total_tokens=150)
        summary = self.store.summarize_llm_usage(sid)
        self.assertEqual(summary["prompt_tokens"], 120)
        self.assertEqual(summary["completion_tokens"], 30)
        self.assertEqual(summary["total_tokens"], 150)
        self.assertEqual(summary["calls"], 1)


class TestPluginApi(unittest.TestCase):
    def test_machine_plugin_action_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {"SR_AGENT_DB": os.path.join(tmp, "api.sqlite"),
                   "SR_SCENES_ROOT": tmp}
            with mock.patch.dict(os.environ, env, clear=False):
                app = create_app()
                try:
                    with TestClient(app) as client:
                        machines = client.get("/api/machines")
                        self.assertEqual(machines.status_code, 200)
                        self.assertTrue(any(
                            m["machine_id"] == LOCAL_MACHINE_ID
                            for m in machines.json()["machines"]))

                        plugins = client.get(
                            "/api/plugins?machine_id=local")
                        self.assertEqual(plugins.status_code, 200)
                        self.assertEqual(len(plugins.json()["plugins"]), 5)

                        denied = client.post(
                            "/api/machines/node-3/plugins/run_sr/actions",
                            json={"action": "enable"})
                        self.assertEqual(denied.status_code, 403)

                        op = client.post(
                            "/api/machines/node-3/plugins/run_sr/actions",
                            headers={"X-Developer-Mode": "1"},
                            json={"action": "enable"})
                        self.assertEqual(op.status_code, 200)
                        self.assertEqual(op.json()["status"], "PENDING")
                        pending = client.get(
                            "/api/machines/node-3/plugin-operations")
                        self.assertEqual(len(pending.json()["operations"]), 1)
                finally:
                    app.state.store.close()
