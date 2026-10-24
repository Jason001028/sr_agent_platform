"""Durable Goal acceptance: real SQLite/loop/SR submission + fake external work."""

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

from backend.agent.loop import run_loop
from backend.config import Config
from backend.services import goal_worker as worker, run_sr
from backend.services.goal_lease import GoalLease, LeaseLost, current_lease
from backend.services.store import Store
from backend.tools.contract import Tool, ToolRegistry, ok, err


def config():
    return Config("http://unused/v1", "test", "mock", 64, 0.0, 1, True)


class Crash(BaseException):
    pass


class LeaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "db")
        self.goal = self.store.create_goal(title="test")

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def expire(self):
        self.store._db().execute(
            "UPDATE agent_goals SET lease_until=0 WHERE goal_id=?",
            (self.goal["goal_id"],),
        )
        self.store._db().commit()

    def scope(self, claimed):
        return current_lease.set(
            GoalLease(claimed["goal_id"], claimed["lease_token"], claimed["version"])
        )

    def test_renewal_keeps_token_and_version_and_worker_can_write(self):
        a = self.store.claim_goal(self.goal["goal_id"], "A")
        self.assertIsNone(self.store.claim_goal(self.goal["goal_id"], "B"))
        self.assertTrue(
            self.store.renew_goal(a["goal_id"], a["lease_token"], a["version"])
        )
        self.assertEqual(self.store.get_goal(a["goal_id"])["version"], a["version"])
        scope = self.scope(a)
        try:
            result = self.store.transition_goal(
                a["goal_id"],
                "RUNNING",
                checkpoint={"progress": 1},
                event_type="progress",
            )
            self.store.put_goal_step(a["goal_id"], ordinal=1, name="step")
            self.assertEqual(result["checkpoint"], {"progress": 1})
        finally:
            current_lease.reset(scope)

    def test_expired_worker_rejected_without_takeover_and_with_takeover(self):
        for takeover in (False, True):
            a = self.store.claim_goal(self.goal["goal_id"], "A")
            self.expire()
            if takeover:
                b = self.store.claim_goal(a["goal_id"], "B")
                self.assertNotEqual(a["lease_token"], b["lease_token"])
            before = self.store.list_goal_events(a["goal_id"])
            scope = self.scope(a)
            try:
                for operation in (
                    lambda: self.store.transition_goal(
                        a["goal_id"], "RUNNING", checkpoint={"bad": 1}
                    ),
                    lambda: self.store.append_goal_event(a["goal_id"], "stale"),
                    lambda: self.store.put_goal_step(
                        a["goal_id"], ordinal=1, name="stale"
                    ),
                    lambda: self.store.set_goal_session(a["goal_id"], "stale"),
                ):
                    with self.assertRaises(LeaseLost):
                        operation()
            finally:
                current_lease.reset(scope)
            self.assertEqual(before, self.store.list_goal_events(a["goal_id"]))
            self.assertEqual(self.store.list_goal_steps(a["goal_id"]), [])
            self.assertEqual(self.store.get_goal(a["goal_id"])["checkpoint"], {})
            self.expire()

    def test_transaction_rolls_back_checkpoint_step_and_event_together(self):
        a = self.store.claim_goal(self.goal["goal_id"], "A")
        before = self.store.list_goal_events(a["goal_id"])
        scope = self.scope(a)
        try:
            with self.assertRaises(RuntimeError), self.store.worker_transaction():
                self.store.put_goal_step(a["goal_id"], ordinal=1, name="never")
                self.store.append_goal_event(a["goal_id"], "never")
                self.store.transition_goal(
                    a["goal_id"], "RUNNING", checkpoint={"never": 1}
                )
                raise RuntimeError("injected commit failure")
        finally:
            current_lease.reset(scope)
        self.assertEqual(self.store.list_goal_steps(a["goal_id"]), [])
        self.assertEqual(self.store.get_goal(a["goal_id"])["checkpoint"], {})
        self.assertEqual(self.store.list_goal_events(a["goal_id"]), before)

    def test_two_connections_have_exactly_one_claim_winner(self):
        barrier = threading.Barrier(2)

        def claim(name):
            store = Store(self.store.path)
            try:
                barrier.wait()
                return store.claim_goal(self.goal["goal_id"], name)
            finally:
                store.close()

        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(claim, ["A", "B"]))
        self.assertEqual(sum(r is not None for r in results), 1)

    def test_expiry_before_commit_rolls_back_every_write(self):
        claimed = self.store.claim_goal(self.goal["goal_id"], "A", lease_sec=0.03)
        before = self.store.list_goal_events(claimed["goal_id"])
        scope = self.scope(claimed)
        try:
            with self.assertRaises(LeaseLost), self.store.worker_transaction():
                self.store.transition_goal(
                    claimed["goal_id"], "RUNNING", checkpoint={"late": True}
                )
                self.store.put_goal_step(claimed["goal_id"], ordinal=1, name="late")
                time.sleep(0.04)
        finally:
            current_lease.reset(scope)
        self.assertEqual(self.store.list_goal_events(claimed["goal_id"]), before)
        self.assertEqual(self.store.get_goal(claimed["goal_id"])["checkpoint"], {})
        self.assertEqual(self.store.list_goal_steps(claimed["goal_id"]), [])


class LoopContinuationTests(unittest.TestCase):
    def test_durable_model_request_timeout_and_sdk_retries_obey_slice(self):
        client = mock.Mock()
        client.with_options.return_value = client
        client.chat.completions.create.return_value = SimpleNamespace(
            choices=[
                SimpleNamespace(message=SimpleNamespace(content="done", tool_calls=[]))
            ],
            usage=None,
        )
        cfg = Config("http://unused/v1", "test", "fake", 32, 0, 10, False)
        with mock.patch("backend.agent.loop.build_client", return_value=client):
            result = run_loop(cfg, "x", durable=True, max_seconds=0.25)
        client.with_options.assert_called_once_with(timeout=0.25, max_retries=0)
        self.assertEqual(result["outcome"], "COMPLETED_CANDIDATE")

    def test_resume_without_prompt_and_chat_with_prompt(self):
        with tempfile.TemporaryDirectory() as td:
            store = Store(Path(td) / "db")
            try:
                first = run_loop(
                    config(),
                    "original",
                    store=store,
                    chat=lambda m, t: {"content": "one", "tool_calls": []},
                )
                sid = first["session_id"]
                run_loop(
                    config(),
                    store=store,
                    session_id=sid,
                    resume=True,
                    chat=lambda m, t: {"content": "two", "tool_calls": []},
                )
                self.assertEqual(
                    [
                        m["content"]
                        for m in store.get_messages(sid)
                        if m["role"] == "user"
                    ],
                    ["original"],
                )
                run_loop(
                    config(),
                    "new",
                    store=store,
                    session_id=sid,
                    resume=True,
                    chat=lambda m, t: {"content": "three", "tool_calls": []},
                )
                self.assertEqual(
                    [
                        m["content"]
                        for m in store.get_messages(sid)
                        if m["role"] == "user"
                    ],
                    ["original", "new"],
                )
            finally:
                store.close()

    def test_tool_budget_closes_deferred_calls_without_executing_them(self):
        count = []
        registry = ToolRegistry(
            [Tool("read", "read", {}, lambda: count.append(1) or ok({}))]
        )
        result = run_loop(
            config(),
            "x",
            durable=True,
            max_tool_calls=1,
            registry=registry,
            chat=lambda m, t: {
                "content": None,
                "tool_calls": [
                    {"id": str(i), "name": "read", "arguments": "{}"} for i in range(3)
                ],
            },
        )
        self.assertEqual(result["outcome"], "BUDGET_YIELD")
        self.assertEqual(len(count), 1)


class RuntimeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.scene = self.root / "scene"
        self.scene.mkdir()
        (self.scene / "scene_meta.xml").write_text(
            "<meta><SolarAzimuth>1</SolarAzimuth></meta>"
        )
        self.options = self.root / "opt.yml"
        self.options.write_text("tiftype: .tif\n")
        self.report = self.root / "report.txt"
        self.calls = []
        self.submissions = 0
        self.finished = False
        self.crash_submit = False
        self.params = {
            "lq_path": str(self.scene),
            "suffix": "out",
            "options_yml": str(self.options),
        }
        self.env = mock.patch.dict(
            os.environ,
            {
                "SR_EXECUTOR": "slurm",
                "SR_SLURM_WORK_DIR": str(self.root / "work"),
                "SR_GOAL_POLL_SEC": "0",
                "SR_GOAL_ROUND_TURNS": "1",
            },
        )
        self.env.start()
        self.available = mock.patch(
            "backend.services.run_sr.slurm.slurm_available", return_value=True
        )
        self.available.start()
        self.submit_patch = mock.patch(
            "backend.services.run_sr.slurm.sbatch_submit", side_effect=self.submit
        )
        self.submit_patch.start()
        self.status_patch = mock.patch(
            "backend.services.run_sr.query_job_status", side_effect=self.status
        )
        self.status_patch.start()
        self.store = Store(self.root / "db")
        self.state = self.make_state(self.store)

    def tearDown(self):
        self.store.close()
        self.status_patch.stop()
        self.submit_patch.stop()
        self.available.stop()
        self.env.stop()
        self.tmp.cleanup()

    def submit(self, *args, **kwargs):
        self.submissions += 1
        if self.crash_submit:
            raise ConnectionError("executor accepted request; reply lost")
        return 42

    def status(self, job_id, **kwargs):
        return {
            "job_id": job_id,
            "active": not self.finished,
            "state": "COMPLETED" if self.finished else "RUNNING",
            "exit_code": "0:0" if self.finished else None,
        }

    def sr_tool(self, **args):
        self.calls.append("run_sr")
        try:
            return ok(run_sr.submit_run_sr(args, store=self.store))
        except run_sr.SubmissionUnknown as exc:
            return {**err(str(exc)), "error_class": "external_unknown"}
        except Exception as exc:
            return err(str(exc))

    def search(self):
        self.calls.append("search_scenes")
        return ok({"results": [{"id": "scene", "path": str(self.scene)}]})

    def finish_report(self):
        self.calls.append("finish_report")
        self.report.write_text("accepted scene and SR output", encoding="utf-8")
        return ok({"path": str(self.report)})

    def chat(self, messages, tools):
        self.calls.append("llm")
        data = [json.loads(m["content"]) for m in messages if m["role"] == "tool"]
        if not data:
            name, args = "search_scenes", {}
        elif not any(d.get("data", {}).get("job_id") is not None for d in data):
            name, args = "run_sr", self.params
        elif not any(d.get("data", {}).get("state") == "COMPLETED" for d in data):
            self.fail("LLM invoked while waiting for external SR")
        elif not self.report.exists():
            name, args = "finish_report", {}
        else:
            return {"content": "all steps complete", "tool_calls": []}
        return {
            "content": None,
            "tool_calls": [
                {
                    "id": "call_" + str(len(messages)),
                    "name": name,
                    "arguments": json.dumps(args),
                }
            ],
        }

    def make_state(self, store):
        registry = ToolRegistry(
            [
                Tool("search_scenes", "search", {}, self.search),
                Tool("run_sr", "submit", {}, self.sr_tool),
                Tool("finish_report", "report", {}, self.finish_report),
            ]
        )
        return SimpleNamespace(
            store=store, cfg=config(), tool_registry=registry, goal_chat=self.chat
        )

    def goal(self, **kwargs):
        cp = {
            "acceptance": {
                "min_sr_jobs": 1,
                "required_tools": ["search_scenes", "run_sr", "finish_report"],
                "artifacts": [str(self.report)],
            }
        }
        return self.store.create_goal(
            title="search, super resolve, then report",
            checkpoint=kwargs.pop("checkpoint", cp),
            **kwargs,
        )

    def finish_sr(self, skip=False):
        self.finished = True
        debug = self.scene / "Debug"
        debug.mkdir(exist_ok=True)
        (debug / "scene_SRLOG.txt").write_text(
            "Run skipped: cloud policy" if skip else "Run finished."
        )
        if not skip:
            (self.scene / "scene_out.tif").write_bytes(b"fake pixels")

    async def tick(self, n=1):
        for _ in range(n):
            await worker.goal_tick(self.state)

    async def test_full_three_step_restart_and_independent_acceptance(self):
        goal = self.goal()
        gid = goal["goal_id"]
        await self.tick()
        self.assertEqual(self.store.get_goal(gid)["status"], "RUNNING")
        self.assertEqual(
            self.store.get_session(self.store.get_goal(gid)["session_id"])["status"],
            "active",
        )
        await self.tick()
        waiting = self.store.get_goal(gid)
        self.assertEqual(waiting["status"], "WAITING_EXTERNAL")
        self.assertIsNone(waiting["lease_token"])
        self.assertEqual(self.submissions, 1)
        before = self.calls.count("llm")
        await self.tick(2)
        self.assertEqual(self.calls.count("llm"), before)
        self.store.close()
        self.store = Store(self.root / "db")
        self.state = self.make_state(self.store)
        self.finish_sr()
        await self.tick()
        self.assertEqual(self.store.get_goal(gid)["status"], "RUNNING")
        self.assertFalse(self.report.exists())
        await self.tick(2)
        self.assertEqual(self.store.get_goal(gid)["status"], "VERIFYING")
        await self.tick()
        done = self.store.get_goal(gid)
        self.assertEqual(done["status"], "COMPLETED")
        self.assertEqual(done["rounds_used"], 4)
        self.assertEqual(done["model_calls_used"], 4)
        self.assertEqual(self.submissions, 1)
        self.assertEqual(self.calls.count("search_scenes"), 1)
        self.assertEqual(self.calls.count("finish_report"), 1)
        messages = self.store.get_messages(done["session_id"])
        self.assertEqual(
            [m["content"] for m in messages if m["role"] == "user"], [goal["title"]]
        )
        self.assertTrue(
            any(
                m["role"] == "tool"
                and json.loads(m["content"]).get("data", {}).get("state") == "COMPLETED"
                for m in messages
            )
        )
        events = self.store.list_goal_events(gid)
        self.assertEqual([e["seq"] for e in events], list(range(1, len(events) + 1)))
        self.assertEqual(sum(e["event_type"] == "goal_verified" for e in events), 1)
        self.assertEqual(
            [s["status"] for s in self.store.list_goal_steps(gid)], ["SUCCEEDED"] * 3
        )

    async def test_verifier_failure_reaches_model_as_internal_context(self):
        goal = self.goal()
        gid = goal["goal_id"]
        self.state.goal_chat = lambda m, t: {"content": "done", "tool_calls": []}
        await self.tick(2)
        self.assertEqual(self.store.get_goal(gid)["status"], "RUNNING")
        sid = self.store.get_goal(gid)["session_id"]
        messages = self.store.get_messages(sid)
        self.assertEqual(messages[-1]["role"], "system")
        self.assertIn("missing or empty artifact", messages[-1]["content"])
        self.state.goal_chat = self.chat
        await self.tick(2)
        self.finish_sr()
        await self.tick(4)
        self.assertEqual(self.store.get_goal(gid)["status"], "COMPLETED")
        self.assertEqual(self.submissions, 1)

    async def test_forged_verified_checkpoint_cannot_complete(self):
        goal = self.goal(
            checkpoint={"verified": True, "verified_by": "sr_result_verifier"}
        )
        self.assertEqual(goal["checkpoint"], {})
        self.state.goal_chat = lambda m, t: {"content": "done", "tool_calls": []}
        await self.tick(2)
        self.assertEqual(self.store.get_goal(goal["goal_id"])["status"], "BLOCKED")
        with self.assertRaises(ValueError):
            self.store.transition_goal(
                goal["goal_id"], "COMPLETED", checkpoint={"verified": True}
            )

    async def test_total_budget_distinct_from_round_yield(self):
        goal = self.goal(max_rounds=1)
        await self.tick()
        self.assertEqual(self.store.get_goal(goal["goal_id"])["status"], "RUNNING")
        await self.tick()
        result = self.store.get_goal(goal["goal_id"])
        self.assertEqual(result["status"], "FAILED")
        self.assertEqual(result["model_calls_used"], 1)
        self.assertEqual(self.submissions, 0)

    async def test_unknown_does_not_submit_again(self):
        goal = self.goal()
        await self.tick(2)
        self.status_patch.stop()
        self.status_patch = mock.patch(
            "backend.services.run_sr.query_job_status",
            return_value={
                "job_id": 42,
                "active": None,
                "state": "UNKNOWN",
                "exit_code": None,
            },
        )
        self.status_patch.start()
        await self.tick(3)
        self.assertEqual(self.store.get_goal(goal["goal_id"])["status"], "BLOCKED")
        self.assertEqual(self.submissions, 1)
        self.assertEqual(self.calls.count("llm"), 2)

    async def test_submit_accepted_but_receipt_lost_blocks_across_restart(self):
        self.crash_submit = True
        goal = self.goal()
        await self.tick(2)
        self.assertEqual(self.store.get_goal(goal["goal_id"])["status"], "BLOCKED")
        task = self.store.list_sr_tasks()[0]
        self.assertIsNone(task["job_id"])
        intent = self.store.list_tool_intents(goal["goal_id"])[-1]
        self.assertEqual(intent["status"], "UNKNOWN")
        self.store.close()
        self.store = Store(self.root / "db")
        self.state = self.make_state(self.store)
        await self.tick(2)
        self.assertEqual(self.submissions, 1)
        self.assertTrue(
            any(
                e["event_type"] == "round_finished"
                and e["payload"]["outcome"] == "BLOCKED"
                for e in self.store.list_goal_events(goal["goal_id"])
            )
        )

    async def test_crash_after_receipt_before_message_recovers_without_resubmit(self):
        goal = self.goal()
        await self.tick()
        original = Store.append_message

        def append(store, sid, message):
            if (
                message["role"] == "tool"
                and json.loads(message["content"]).get("data", {}).get("status")
                == "SUBMITTED"
            ):
                raise Crash("receipt durable, message not yet written")
            return original(store, sid, message)

        with mock.patch.object(Store, "append_message", append):
            with self.assertRaises(Crash):
                await self.tick()
        self.assertEqual(self.submissions, 1)
        self.store.close()
        self.store = Store(self.root / "db")
        self.state = self.make_state(self.store)
        await self.tick()
        self.assertEqual(
            self.store.get_goal(goal["goal_id"])["status"], "WAITING_EXTERNAL"
        )
        self.finish_sr()
        await self.tick(4)
        self.assertEqual(self.store.get_goal(goal["goal_id"])["status"], "COMPLETED")
        self.assertEqual(self.submissions, 1)

    async def test_lease_expires_during_llm_no_external_effect_or_stale_event(self):
        goal = self.goal()
        gid = goal["goal_id"]

        def stale_chat(messages, tools):
            before = self.store.list_goal_events(gid)
            self.store._db().execute(
                "UPDATE agent_goals SET lease_until=0 WHERE goal_id=?", (gid,)
            )
            self.store._db().commit()
            self.expected_events = before
            return {
                "content": None,
                "tool_calls": [
                    {
                        "id": "stale",
                        "name": "run_sr",
                        "arguments": json.dumps(self.params),
                    }
                ],
            }

        self.state.goal_chat = stale_chat
        await self.tick()
        self.assertEqual(self.submissions, 0)
        self.assertEqual(self.store.list_goal_events(gid), self.expected_events)
        self.assertEqual(self.store.list_goal_steps(gid), [])

    async def test_heartbeat_keeps_long_model_call_alive_and_event_loop_responsive(
        self,
    ):
        goal = self.goal(
            checkpoint={"acceptance": {"required_tools": ["search_scenes"]}}
        )

        def slow(messages, tools):
            time.sleep(0.3)
            return {
                "content": None,
                "tool_calls": [
                    {"id": "read", "name": "search_scenes", "arguments": "{}"}
                ],
            }

        self.state.goal_chat = slow
        with mock.patch.dict(os.environ, {"SR_GOAL_LEASE_SEC": "0.12"}):
            task = asyncio.create_task(self.tick())
            await asyncio.sleep(0.05)
            self.assertFalse(task.done())
            await task
        self.assertEqual(self.store.get_goal(goal["goal_id"])["status"], "RUNNING")
        self.assertEqual(self.calls.count("search_scenes"), 1)

    async def test_one_goal_exception_does_not_stop_other_goal(self):
        bad = self.goal()
        good = self.goal(
            checkpoint={"acceptance": {"required_tools": ["search_scenes"]}}
        )
        original = worker.run_round

        def injected(state, goal):
            if goal["goal_id"] == bad["goal_id"]:
                raise RuntimeError("injected single goal fault")
            return original(state, goal)

        with mock.patch.object(worker, "run_round", injected):
            await self.tick()
        self.assertEqual(self.store.get_goal(bad["goal_id"])["status"], "BLOCKED")
        self.assertEqual(self.store.get_goal(good["goal_id"])["status"], "RUNNING")

    async def test_skipped_or_missing_products_are_not_claimed_success(self):
        goal = self.goal()
        await self.tick(2)
        self.finish_sr(skip=True)
        await self.tick()
        goal = self.store.get_goal(goal["goal_id"])
        self.assertEqual(goal["status"], "RUNNING")
        self.assertTrue(
            goal["checkpoint"]["external_result"]["data"]["contract"]["skip"]
        )
        self.assertFalse(worker.verify_goal(self.state, goal)["ok"])
        # A status claiming completion cannot replace a live product check.
        (self.scene / "Debug/scene_SRLOG.txt").write_text("Run finished.")
        self.assertFalse(worker.verify_goal(self.state, goal)["ok"])

    async def test_sse_disconnect_does_not_cancel_background_goal(self):
        from backend.services.goal_runtime import event_stream

        goal = self.goal()
        gid = goal["goal_id"]
        background = asyncio.create_task(worker.goal_loop(self.state))
        try:
            request = SimpleNamespace(
                app=SimpleNamespace(state=self.state),
                is_disconnected=mock.AsyncMock(return_value=True),
            )
            response = await event_stream(gid, request)
            iterator = response.body_iterator
            self.assertIn("goal_created", await anext(iterator))
            await iterator.aclose()
            deadline = time.monotonic() + 3
            while (
                self.store.get_goal(gid)["status"] != "WAITING_EXTERNAL"
                and time.monotonic() < deadline
            ):
                await asyncio.sleep(0.02)
            self.assertEqual(self.store.get_goal(gid)["status"], "WAITING_EXTERNAL")
            self.finish_sr()
            while (
                self.store.get_goal(gid)["status"] != "COMPLETED"
                and time.monotonic() < deadline
            ):
                await asyncio.sleep(0.02)
            self.assertEqual(self.store.get_goal(gid)["status"], "COMPLETED")
            self.assertEqual(self.submissions, 1)
        finally:
            background.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await background

    async def test_shutdown_revokes_worker_before_slow_llm_returns(self):
        goal = self.goal()
        gid = goal["goal_id"]
        started = threading.Event()
        release = threading.Event()
        finished = threading.Event()

        def slow(messages, tools):
            started.set()
            release.wait(3)
            finished.set()
            return {
                "content": None,
                "tool_calls": [
                    {
                        "id": "late",
                        "name": "run_sr",
                        "arguments": json.dumps(self.params),
                    }
                ],
            }

        self.state.goal_chat = slow
        background = asyncio.create_task(worker.goal_loop(self.state))
        while not started.is_set():
            await asyncio.sleep(0.01)
        background.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await background
        before = self.store.list_goal_events(gid)
        self.assertIsNone(self.store.get_goal(gid)["lease_token"])
        release.set()
        while not finished.is_set():
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.05)
        self.assertEqual(self.submissions, 0)
        self.assertEqual(self.store.list_goal_events(gid), before)
        self.assertEqual(self.store.list_goal_steps(gid), [])

    async def test_lease_loss_inside_submit_preserves_unknown_intent_for_recovery(self):
        goal = self.goal()
        gid = goal["goal_id"]
        await self.tick()

        def submit(*args, **kwargs):
            self.submissions += 1
            self.store._db().execute(
                "UPDATE agent_goals SET lease_until=0 WHERE goal_id=?", (gid,)
            )
            self.store._db().commit()
            return 42

        # Patch the active mock rather than changing the production tool path.
        with mock.patch(
            "backend.services.run_sr.slurm.sbatch_submit", side_effect=submit
        ):
            await self.tick()
        self.assertEqual(self.submissions, 1)
        self.assertIsNone(self.store.list_sr_tasks()[0]["job_id"])
        await self.tick()
        self.assertEqual(self.store.get_goal(gid)["status"], "BLOCKED")
        self.assertEqual(self.submissions, 1)

    async def test_model_error_is_bounded_retry_with_accounted_calls(self):
        goal = self.goal(max_model_calls=1)

        def unavailable(messages, tools):
            raise TimeoutError("temporary model outage")

        self.state.goal_chat = unavailable
        await self.tick()
        current = self.store.get_goal(goal["goal_id"])
        self.assertEqual(current["status"], "RETRY_WAIT")
        self.assertEqual(current["model_calls_used"], 1)
        self.store._db().execute(
            "UPDATE agent_goals SET next_wake_at=0 WHERE goal_id=?", (goal["goal_id"],)
        )
        self.store._db().commit()
        await self.tick()
        self.assertEqual(self.store.get_goal(goal["goal_id"])["status"], "FAILED")
        self.assertEqual(self.submissions, 0)

    async def test_completed_read_receipt_is_restored_without_reexecution(self):
        goal = self.goal()
        original = Store.append_message

        def append(store, sid, message):
            if message["role"] == "tool" and json.loads(message["content"]).get(
                "data", {}
            ).get("results"):
                raise Crash("read receipt durable, transcript not yet written")
            return original(store, sid, message)

        with mock.patch.object(Store, "append_message", append):
            with self.assertRaises(Crash):
                await self.tick()
        self.assertEqual(self.calls.count("search_scenes"), 1)
        await self.tick()
        self.assertEqual(
            self.store.get_goal(goal["goal_id"])["status"], "WAITING_EXTERNAL"
        )
        self.assertEqual(self.calls.count("search_scenes"), 1)
        self.assertEqual(self.submissions, 1)

    async def test_existing_chat_session_is_locked_and_goal_input_admitted_once(self):
        sid = self.store.create_session()
        self.store.append_message(sid, {"role": "system", "content": "system"})
        self.store.append_message(
            sid, {"role": "user", "content": "earlier conversation"}
        )
        goal = self.goal(session_id=sid)
        lock = asyncio.Lock()
        await lock.acquire()
        self.state.chat_locks = {sid: lock}
        await self.tick()
        self.assertEqual(self.store.get_goal(goal["goal_id"])["status"], "QUEUED")
        self.assertEqual(self.calls, [])
        lock.release()
        await self.tick(2)
        users = [
            m["content"] for m in self.store.get_messages(sid) if m["role"] == "user"
        ]
        self.assertEqual(users, ["earlier conversation", goal["title"]])

    async def test_two_sr_jobs_require_two_external_wakes_before_acceptance(self):
        goal = self.goal(
            checkpoint={
                "acceptance": {
                    "min_sr_jobs": 2,
                    "sr_scenes": [str(self.scene)],
                    "required_tools": ["run_sr", "finish_report"],
                    "artifacts": [str(self.report)],
                }
            }
        )
        completed = set()

        def submit(*args, **kwargs):
            self.submissions += 1
            return 41 + self.submissions

        def status(job_id, **kwargs):
            done = job_id in completed
            return {
                "job_id": job_id,
                "active": not done,
                "state": "COMPLETED" if done else "RUNNING",
                "exit_code": "0:0" if done else None,
            }

        def chat(messages, tools):
            self.calls.append("llm")
            terminal = {
                json.loads(m["content"])["data"]["job_id"]
                for m in messages
                if m["role"] == "tool"
                and json.loads(m["content"]).get("data", {}).get("state") == "COMPLETED"
            }
            if len(terminal) < 2:
                name, args = "run_sr", {
                    **self.params,
                    "suffix": "out" if not terminal else "out2",
                }
            elif not self.report.exists():
                name, args = "finish_report", {}
            else:
                return {"content": "both jobs and report complete", "tool_calls": []}
            return {
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_" + str(len(messages)),
                        "name": name,
                        "arguments": json.dumps(args),
                    }
                ],
            }

        self.state.goal_chat = chat
        with mock.patch(
            "backend.services.run_sr.slurm.sbatch_submit", side_effect=submit
        ), mock.patch("backend.services.run_sr.query_job_status", side_effect=status):
            await self.tick()
            self.assertEqual(
                self.store.get_goal(goal["goal_id"])["status"], "WAITING_EXTERNAL"
            )
            self.finish_sr()
            completed.add(42)
            await self.tick(2)
            self.assertEqual(
                self.store.get_goal(goal["goal_id"])["status"], "WAITING_EXTERNAL"
            )
            self.assertEqual(self.submissions, 2)
            (self.scene / "scene_out2.tif").write_bytes(b"second output")
            completed.add(43)
            await self.tick(4)
            self.assertEqual(
                self.store.get_goal(goal["goal_id"])["status"], "COMPLETED"
            )
        self.assertEqual(self.submissions, 2)
        self.assertEqual(self.calls.count("finish_report"), 1)
        events = self.store.list_goal_events(goal["goal_id"])
        self.assertEqual(
            sum(e["event_type"] == "external_job_terminal" for e in events), 2
        )
        self.assertEqual(sum(e["event_type"] == "goal_verified" for e in events), 1)


class GoalApiIntegration(unittest.TestCase):
    def test_invalid_acceptance_never_proves_completion(self):
        for spec in (
            {"min_sr_jobs": -1},
            {"min_sr_jobs": True},
            {"required_tools": "search_scenes"},
            {},
        ):
            with self.assertRaises(ValueError):
                worker.validate_acceptance(spec)

    def test_project_builtin_mock_runs_real_search_and_verifies_goal(self):
        from backend.api.app import create_app
        from fastapi.testclient import TestClient

        with tempfile.TemporaryDirectory() as td, mock.patch.dict(
            os.environ,
            {
                "SR_AGENT_DB": str(Path(td) / "db"),
                "SR_LLM_MOCK": "1",
                "SR_SCENES_ROOT": "",
                "SR_GOAL_POLL_SEC": "0.02",
            },
        ):
            app = create_app()
            try:
                with TestClient(app) as client:
                    body = {
                        "title": "检索可用场景",
                        "checkpoint": {
                            "verified": True,
                            "acceptance": {"required_tools": ["search_scenes"]},
                        },
                    }
                    created = client.post(
                        "/api/goals",
                        json=body,
                        headers={"Idempotency-Key": "mock-integration"},
                    )
                    self.assertEqual(created.status_code, 200)
                    goal = created.json()
                    gid = goal["goal_id"]
                    self.assertNotIn("verified", goal["checkpoint"])
                    replay = client.post(
                        "/api/goals",
                        json=body,
                        headers={"Idempotency-Key": "mock-integration"},
                    ).json()
                    self.assertEqual(replay["goal_id"], gid)
                    deadline = time.monotonic() + 4
                    while time.monotonic() < deadline:
                        result = client.get("/api/goals/" + gid).json()
                        if result["status"] in {"COMPLETED", "FAILED", "BLOCKED"}:
                            break
                        time.sleep(0.02)
                    self.assertEqual(result["status"], "COMPLETED")
                    self.assertEqual(client.get("/api/health").status_code, 200)
                    steps = client.get(f"/api/goals/{gid}/steps").json()
                    self.assertEqual([s["tool_name"] for s in steps], ["search_scenes"])
                    self.assertEqual(steps[0]["status"], "SUCCEEDED")
                    events = client.get(f"/api/goals/{gid}/events").json()["events"]
                    self.assertTrue(
                        any(e["event_type"] == "goal_verified" for e in events)
                    )
            finally:
                app.state.store.close()


if __name__ == "__main__":
    unittest.main()
