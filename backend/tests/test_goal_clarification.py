"""Deterministic clarification lifecycle, transactions, fencing and recovery."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.agent.loop import run_loop
from backend.agent.round_outcome import RoundOutcome
from backend.config import Config
from backend.services import goal_worker as worker, goal_runtime
from backend.services.goal_lease import GoalLease, LeaseLost, current_lease
from backend.services.store import Store, ClarificationConflict
from backend.tools.clarification import control_tool
from backend.tools.contract import Tool, ToolRegistry, ok


QUESTION = {"question": "处理哪个区域？", "options": [{"id": "a", "label": "区域 A"},
             {"id": "b", "label": "区域 B"}], "recommended_option": "a"}


class Crash(BaseException):
    pass


def call(name="request_clarification", args=None, cid="clarify_1"):
    return {"id": cid, "name": name, "arguments": json.dumps(QUESTION if args is None else args)}


def response(*calls):
    return {"finish_reason": "tool_calls", "content": None, "tool_calls": list(calls)}


class ClarificationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "db.sqlite"
        self.store = Store(self.path)
        self.cfg = Config("http://unused/v1", "test", "mock", 1024, 0.0, 1, True)
        self.observed = []
        self.executed = []
        self.registry = ToolRegistry([Tool("finish", "fake executor", {}, self.finish),
                                      Tool("run_sr", "fake SR", {}, lambda: self.executed.append("sr") or ok({}))])
        self.state = SimpleNamespace(store=self.store, cfg=self.cfg, tool_registry=self.registry,
                                     goal_chat=self.chat, chat_locks={})
        self.goal = self.store.create_goal(title="按指定区域完成处理", checkpoint={
            "acceptance": {"required_tools": ["finish"]}})

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def finish(self):
        self.executed.append("finish")
        return ok({"output": "fake artifact"})

    def chat(self, messages, tools):
        self.observed.append(messages)
        self.assertIn("request_clarification", [t["function"]["name"] for t in tools])
        self.assertFalse(any("goal_run_id" in m or "goal_id" in m or "clarification_id" in m for m in messages))
        answers = [m for m in messages if m["role"] == "user" and "针对澄清问题" in str(m["content"])]
        if not answers:
            return response(call())
        if not any(m["role"] == "tool" and "fake artifact" in m["content"] for m in messages):
            return response(call("finish", {}, "finish_1"))
        return {"finish_reason": "stop", "content": "处理完成", "tool_calls": []}

    async def tick(self):
        await worker.goal_tick(self.state)
        return self.store.get_goal(self.goal["goal_id"])

    def pending(self):
        return self.store.list_goal_clarifications(self.goal["goal_id"])[0]

    def answer(self, **kwargs):
        return self.store.respond_goal_clarification(self.goal["goal_id"], self.pending()["clarification_id"],
            idempotency_key=kwargs.pop("idempotency_key", "answer_1"), **kwargs)

    def restart(self):
        self.store.close()
        self.store = Store(self.path)
        self.state.store = self.store
        self.state.chat_locks = {}

    async def test_option_answer_restart_and_same_goal_completes(self):
        waiting = await self.tick()
        self.assertEqual(waiting["status"], "WAITING_USER")
        self.assertIsNone(waiting["lease_token"])
        self.assertEqual(len(self.observed), 1)
        item = self.pending()
        last = waiting["checkpoint"]["last_round"]
        self.assertEqual(last["outcome"], "NEEDS_CLARIFICATION")
        self.assertEqual(last["goal_id"], waiting["goal_id"])
        self.assertEqual(last["run_id"], item["run_id"])
        self.assertEqual(last["question"], QUESTION["question"])
        for _ in range(3):
            await self.tick()
        self.assertEqual(len(self.observed), 1)
        self.assertIsNone(self.store.claim_goal(waiting["goal_id"], "should-not-claim"))
        original_history = self.store.get_messages(waiting["session_id"])
        answer = self.answer(selected_option_id="b")
        self.assertEqual(answer["goal"]["status"], "QUEUED")
        self.assertEqual(answer["goal"]["checkpoint"], waiting["checkpoint"])
        self.restart()  # Answer committed, worker has not claimed it yet.
        verifying = await self.tick()
        self.assertEqual(verifying["status"], "VERIFYING")
        completed = await self.tick()
        self.assertEqual(completed["status"], "COMPLETED")
        self.assertEqual(completed["goal_id"], waiting["goal_id"])
        self.assertEqual(completed["session_id"], waiting["session_id"])
        self.assertEqual(completed["checkpoint"]["acceptance"], waiting["checkpoint"]["acceptance"])
        self.assertEqual(self.executed, ["finish"])
        history = self.store.get_messages(waiting["session_id"])
        self.assertEqual(history[:len(original_history)], original_history)
        self.assertIn('Internal Goal checkpoint', self.observed[-1][0]["content"])
        self.assertIn(item["clarification_id"], self.observed[-1][0]["content"])
        self.assertFalse(any('Internal Goal checkpoint' in str(m.get('content')) for m in history))
        users = [m for m in history if m["role"] == "user"]
        self.assertEqual(len(users), 2)
        self.assertEqual(users[-1]["clarification_id"], item["clarification_id"])
        self.assertIn("区域 B", users[-1]["content"])
        events = self.store.list_goal_events(waiting["goal_id"])
        self.assertEqual([e["seq"] for e in events], list(range(1, len(events) + 1)))
        requested = [e for e in events if e["event_type"] == "clarification_requested"]
        self.assertEqual(len(requested), 1)
        self.assertEqual(requested[0]["run_id"], item["run_id"])
        self.assertEqual(sum(e["event_type"] == "clarification_answered" for e in events), 1)
        self.assertEqual(self.pending()["status"], "ANSWERED")
        self.assertEqual(self.store.list_tool_intents(waiting["goal_id"])[0]["tool_name"], "finish")

    async def test_free_text_answer_continues_with_history_and_snapshot(self):
        sid = self.store.create_session()
        self.store.set_goal_session(self.goal["goal_id"], sid)
        snapshot = {"version": 1, "current": {"name": "original.tif"}, "attachment_count": 0}
        self.store.append_message(sid, {"role": "system", "content": "system"})
        self.store.append_message(sid, {"role": "user", "content": "已有背景", "viewer_snapshot": snapshot})
        await self.tick()
        self.answer(answer_text="只处理北侧区域")
        await self.tick()
        self.assertIn("只处理北侧区域", str(self.observed[-1]))
        self.assertIn("original.tif", str(self.observed[-1]))
        self.assertEqual((await self.tick())["status"], "COMPLETED")

    async def test_idempotency_accepts_once_and_rejects_other_payloads(self):
        goal = await self.tick()
        result = self.answer(selected_option_id="a")
        count = len(self.store.get_messages(goal["session_id"]))
        events = self.store.list_goal_events(goal["goal_id"])
        self.assertEqual(self.answer(selected_option_id="a"), result)
        for kwargs in ({"selected_option_id": "b"}, {"selected_option_id": "a", "idempotency_key": "another"}):
            with self.assertRaises(ClarificationConflict):
                self.answer(**kwargs)
        self.assertEqual(count, len(self.store.get_messages(goal["session_id"])))
        self.assertEqual(events, self.store.list_goal_events(goal["goal_id"]))

    async def test_two_connections_only_one_answer_wins(self):
        goal = await self.tick()
        cid = self.pending()["clarification_id"]
        barrier = threading.Barrier(2)
        def submit(option):
            store = Store(self.path)
            try:
                store.get_goal(goal["goal_id"])
                barrier.wait()
                try:
                    return store.respond_goal_clarification(goal["goal_id"], cid,
                        idempotency_key=option, selected_option_id=option)
                except ClarificationConflict:
                    return None
            finally:
                store.close()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(submit, ["a", "b"]))
        self.assertEqual(sum(r is not None for r in results), 1)
        self.assertEqual(sum(m.get("clarification_id") == cid for m in self.store.get_messages(goal["session_id"])), 1)

    async def test_invalid_answers_leave_everything_unchanged(self):
        goal = await self.tick()
        before = self.store.get_messages(goal["session_id"])
        for kwargs in ({}, {"answer_text": "  "}, {"selected_option_id": "missing"},
                       {"selected_option_id": 12}, {"answer_text": []}, {"idempotency_key": ""}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.answer(**kwargs)
        self.assertEqual(self.store.get_goal(goal["goal_id"]), goal)
        self.assertEqual(before, self.store.get_messages(goal["session_id"]))

    async def test_no_free_text_and_no_empty_choice(self):
        self.state.goal_chat = lambda *_: response(call(args={**QUESTION, "allow_free_text": False}))
        await self.tick()
        with self.assertRaises(ValueError):
            self.answer(answer_text="other")
        self.assertEqual(self.answer(selected_option_id="a")["goal"]["status"], "QUEUED")

    async def test_request_atomic_rollback_recovers_without_model(self):
        original = self.store._append_goal_event_tx
        def injected(db, gid, event, *args, **kwargs):
            result = original(db, gid, event, *args, **kwargs)
            if event == "clarification_requested":
                raise Crash("crash after event insert, before commit")
            return result
        with mock.patch.object(self.store, "_append_goal_event_tx", side_effect=injected):
            with self.assertRaises(Crash):
                await self.tick()
        self.assertEqual(self.store.list_goal_clarifications(self.goal["goal_id"]), [])
        self.assertEqual(self.store.get_goal(self.goal["goal_id"])["status"], "RUNNING")
        self.restart()
        self.assertEqual((await self.tick())["status"], "WAITING_USER")
        self.assertEqual(len(self.observed), 1)
        self.assertEqual(len(self.store.list_goal_clarifications(self.goal["goal_id"])), 1)
        await self.tick()
        self.assertEqual(len(self.observed), 1)

    async def test_crash_after_declaration_before_tool_result_recovers(self):
        original = self.store.append_message
        def injected(sid, message):
            result = original(sid, message)
            if message["role"] == "assistant" and message.get("tool_calls"):
                raise Crash("assistant declaration committed")
            return result
        with mock.patch.object(self.store, "append_message", side_effect=injected):
            with self.assertRaises(Crash):
                await self.tick()
        self.restart()
        goal = await self.tick()
        self.assertEqual(goal["status"], "WAITING_USER")
        self.assertEqual(len(self.observed), 1)
        tools = [m for m in self.store.get_messages(goal["session_id"]) if m["role"] == "tool"]
        self.assertEqual(len(tools), 1)
        self.assertTrue(json.loads(tools[0]["content"])["ok"])
        self.answer(answer_text="区域 C")
        self.assertEqual((await self.tick())["status"], "VERIFYING")

    async def test_answer_atomic_rollback_has_no_message_or_queue(self):
        goal = await self.tick()
        before = self.store.get_messages(goal["session_id"])
        original = self.store._append_goal_event_tx
        def injected(db, gid, event, *args, **kwargs):
            value = original(db, gid, event, *args, **kwargs)
            if event == "clarification_answered":
                raise Crash("answer transaction failed")
            return value
        with mock.patch.object(self.store, "_append_goal_event_tx", side_effect=injected):
            with self.assertRaises(Crash):
                self.answer(selected_option_id="a")
        self.assertEqual(before, self.store.get_messages(goal["session_id"]))
        self.assertEqual(self.store.get_goal(goal["goal_id"]), goal)
        self.assertEqual(self.pending()["status"], "PENDING")
        self.assertEqual(self.answer(selected_option_id="a")["goal"]["status"], "QUEUED")

    async def test_waiting_restart_no_llm_and_db_unique_pending(self):
        goal = await self.tick()
        item = self.pending()
        self.restart()
        await self.tick()
        self.assertEqual(len(self.observed), 1)
        self.assertEqual(self.pending(), item)
        with self.assertRaises(sqlite3.IntegrityError), self.store.worker_transaction() as db:
            db.execute("INSERT INTO agent_goal_clarifications "
                "(clarification_id,goal_id,request_key,question,options_json,allow_free_text,status,created_at) VALUES ('duplicate',?,'new','q','[]',1,'PENDING',0)", (goal["goal_id"],))

    async def test_cancelled_question_cannot_wake_and_resume_is_forbidden(self):
        goal = await self.tick()
        with self.assertRaises(ClarificationConflict):
            self.store.transition_goal(goal["goal_id"], "QUEUED")
        self.store.transition_goal(goal["goal_id"], "CANCELLED")
        self.assertEqual(self.pending()["status"], "CANCELLED")
        with self.assertRaises(ClarificationConflict):
            self.answer(selected_option_id="a")
        await self.tick()
        self.assertEqual(len(self.observed), 1)

    async def test_old_answer_retry_cannot_wake_terminal_goal(self):
        await self.tick()
        self.answer(selected_option_id="a")
        await self.tick()
        await self.tick()
        self.answer(selected_option_id="a")  # Same result, no state mutation.
        with self.assertRaises(ClarificationConflict):
            self.answer(selected_option_id="b", idempotency_key="old")
        self.assertEqual(self.store.get_goal(self.goal["goal_id"])["status"], "COMPLETED")

    async def test_expired_worker_cannot_create_or_override_waiting_state(self):
        goal = await self.tick()
        before = self.pending()
        scope = current_lease.set(GoalLease(goal["goal_id"], "expired-token", goal["version"] - 1))
        try:
            with self.assertRaises(LeaseLost):
                self.store.request_goal_clarification(goal["goal_id"], run_id="old", request_key="old",
                    clarification=QUESTION, expected_version=goal["version"] - 1, lease_token="expired-token")
            with self.assertRaises(LeaseLost):
                self.store.append_message(goal["session_id"], {"role": "user", "content": "stale"})
        finally:
            current_lease.reset(scope)
        self.assertEqual(self.pending(), before)
        self.assertEqual(self.store.get_goal(goal["goal_id"]), goal)

    async def test_mixed_batch_never_executes_sr_or_creates_question(self):
        for calls in ((call("run_sr", {}), call()), (call(), call("run_sr", {}))):
            with self.subTest(order=calls[0]["name"]):
                result = run_loop(self.cfg, "test", durable=True, registry=self.registry,
                    chat=lambda *_: response(*calls), max_turns=1)
                self.assertEqual(result["outcome"], RoundOutcome.RETRYABLE_ERROR)
                self.assertEqual(result["tool_calls"], 0)
        self.assertEqual(self.executed, [])

    async def test_natural_language_does_not_enter_waiting(self):
        self.state.goal_chat = lambda *_: {"finish_reason": "stop", "content": "需要用户确认区域", "tool_calls": []}
        self.assertEqual((await self.tick())["status"], "VERIFYING")
        self.assertEqual(self.store.list_goal_clarifications(self.goal["goal_id"]), [])

    async def test_normal_chat_does_not_expose_control_tool(self):
        def chat(messages, tools):
            self.assertNotIn("request_clarification", [t["function"]["name"] for t in tools])
            return {"finish_reason": "stop", "content": "普通回答", "tool_calls": []}
        result = run_loop(self.cfg, "hello", chat=chat, registry=ToolRegistry([control_tool()]))
        self.assertEqual(result["answer"], "普通回答")

    async def test_invalid_structured_request_does_not_wait(self):
        for args in ({"question": " "}, {**QUESTION, "recommended_option": "missing"},
                     {**QUESTION, "options": [{"id": "a", "label": "a"}] * 2},
                     {"question": "q", "options": [], "allow_free_text": False}):
            with self.subTest(args=args):
                result = run_loop(self.cfg, "test", durable=True, registry=self.registry,
                    chat=lambda *_: response(call(args=args)), max_turns=1)
                self.assertNotEqual(result["outcome"], RoundOutcome.NEEDS_CLARIFICATION)

    async def test_direct_waiting_transition_is_rejected(self):
        with self.assertRaises(ValueError):
            self.store.transition_goal(self.goal["goal_id"], "WAITING_USER")
        with self.assertRaises(ValueError):
            self.store.create_goal(title="invalid", status="WAITING_USER")

    async def test_second_question_can_reuse_call_id_without_reopening_old_request(self):
        await self.tick()
        first = self.pending()
        self.answer(selected_option_id="a")
        self.state.goal_chat = lambda *_: response(call(args={**QUESTION, "question": "处理精度？"}))
        self.assertEqual((await self.tick())["status"], "WAITING_USER")
        rows = self.store.list_goal_clarifications(self.goal["goal_id"])
        self.assertEqual([r["status"] for r in rows], ["ANSWERED", "PENDING"])
        self.assertNotEqual(rows[0]["request_key"], rows[1]["request_key"])
        self.store.respond_goal_clarification(self.goal["goal_id"], rows[1]["clarification_id"],
            idempotency_key="second", answer_text="高精度")
        self.state.goal_chat = self.chat
        await self.tick()
        self.assertEqual((await self.tick())["status"], "COMPLETED")
        self.assertEqual(self.store.list_goal_clarifications(self.goal["goal_id"])[0]["clarification_id"], first["clarification_id"])

    async def test_existing_database_adds_clarification_table_without_changing_goal(self):
        original = self.store.get_goal(self.goal["goal_id"])
        self.store._db().execute("DROP TABLE agent_goal_clarifications")
        self.store._db().commit()
        self.restart()
        self.assertEqual(self.store.get_goal(self.goal["goal_id"]), original)
        self.assertEqual((await self.tick())["status"], "WAITING_USER")

    async def test_new_goal_in_same_session_does_not_recover_another_goals_question(self):
        goal = await self.tick()
        self.answer(selected_option_id="a")
        await self.tick()
        await self.tick()
        other = self.store.create_goal(title="同一会话中的后续独立任务", session_id=goal["session_id"],
            checkpoint={"acceptance": {"required_tools": ["finish"]}})
        self.state.goal_chat = lambda *_: {"finish_reason": "stop", "content": "后续任务", "tool_calls": []}
        await worker.goal_tick(self.state)
        self.assertEqual(self.store.get_goal(other["goal_id"])["status"], "VERIFYING")
        self.assertEqual(self.store.list_goal_clarifications(other["goal_id"]), [])

    async def test_answer_keeps_question_snapshot_when_chat_changes_while_waiting(self):
        sid = self.store.create_session()
        self.store.set_goal_session(self.goal["goal_id"], sid)
        snapshot = {"version": 1, "current": {"name": "question.tif"}, "attachment_count": 0}
        self.store.append_message(sid, {"role": "system", "content": "system"})
        self.store.append_message(sid, {"role": "user", "content": self.goal["title"], "viewer_snapshot": snapshot})
        await self.tick()
        self.store.append_message(sid, {"role": "user", "content": "另一个普通聊天问题", "viewer_snapshot":
            {**snapshot, "current": {"name": "other.tif"}}})
        self.answer(selected_option_id="a")
        self.assertEqual(self.store.get_messages(sid)[-1]["viewer_snapshot"], snapshot)

    async def test_api_reads_validates_and_schedules_without_inline_model(self):
        goal = await self.tick()
        app = FastAPI()
        app.state.store = self.store
        app.include_router(goal_runtime.router)
        cid = self.pending()["clarification_id"]
        base = f"/api/goals/{goal['goal_id']}/clarifications"
        with TestClient(app) as client:
            self.assertEqual(client.get(base).json()[0]["status"], "PENDING")
            self.assertEqual(len(client.get("/api/goals", params={"session_id": goal["session_id"]}).json()), 1)
            self.assertEqual(client.get("/api/goals", params={"session_id": "other"}).json(), [])
            self.assertEqual(client.post(base + "/missing/respond", json={"idempotency_key": "k"}).status_code, 404)
            self.assertEqual(client.post(base + f"/{cid}/respond", json={"idempotency_key": "k"}).status_code, 400)
            self.assertEqual(client.post(f"/api/goals/{goal['goal_id']}/resume").status_code, 409)
            payload = {"idempotency_key": "k", "selected_option_id": "a"}
            r = client.post(base + f"/{cid}/respond", json=payload)
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r.json()["goal"]["status"], "QUEUED")
            self.assertEqual(len(self.observed), 1)
            self.assertEqual(client.post(base + f"/{cid}/respond", json=payload).status_code, 200)
            self.assertEqual(client.post(base + f"/{cid}/respond", json={**payload, "idempotency_key": "competing"}).status_code, 409)
            self.assertEqual(client.get(base).json()[0]["status"], "ANSWERED")


if __name__ == "__main__":
    unittest.main()
