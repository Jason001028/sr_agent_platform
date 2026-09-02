"""Tests for the M1 agent loop (fake chat seam — no network, no API key).

The loop's `chat=` seam takes plain dicts, so these tests validate the
state-machine mechanics: registry lookup, tool-result backfeed, error
self-healing, iteration cap and clean error surfacing — plus P0① SQLite
persistence (messages committed before each step), resume and crash repair.
"""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
from PIL import Image

from backend.agent.loop import _repair_unclosed_turn, run_loop
from backend.config import Config
from backend.services.store import Store
from backend.tools.contract import list_tools, manifest


def cfg():
    return Config(llm_base_url="http://localhost/v1", llm_api_key="test",
                  llm_model="fake", llm_max_tokens=16, llm_temperature=0.0,
                  llm_timeout=5)


class TestRegistry(unittest.TestCase):
    def test_fix_bad_lines_registered(self):
        names = [t.name for t in list_tools()]
        self.assertIn("fix_bad_lines", names)

    def test_manifest_openai_shape(self):
        m = manifest()
        self.assertTrue(m)
        fn = m[0]["function"]
        self.assertIn("name", fn)
        self.assertIn("parameters", fn)


class TestLoop(unittest.TestCase):
    def test_final_answer_no_tools(self):
        def chat(messages, tools):
            return {"content": "好", "tool_calls": []}
        r = run_loop(cfg(), "hi", chat=chat)
        self.assertTrue(r["ok"])
        self.assertEqual(r["answer"], "好")
        self.assertEqual(r["turns"], 1)

    def test_tool_then_final(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "in.tif"
            arr = np.zeros((16, 16), dtype=np.uint16)
            arr[3] = 5000          # one bad row
            Image.fromarray(arr).save(p)

            calls = []

            def chat(messages, tools):
                calls.append(len(messages))
                if len(calls) == 1:
                    return {"content": None, "tool_calls": [{
                        "id": "call_1", "name": "fix_bad_lines",
                        "arguments": json.dumps({"input_path": str(p)})}]}
                return {"content": "已修复", "tool_calls": []}

            r = run_loop(cfg(), "修复坏行", chat=chat)
            self.assertTrue(r["ok"])
            self.assertEqual(r["answer"], "已修复")
            self.assertEqual(r["turns"], 2)
            tool_msg = [m for m in r["messages"] if m.get("role") == "tool"]
            self.assertEqual(len(tool_msg), 1)
            self.assertTrue(tool_msg[0]["content"]["ok"])
            self.assertEqual(tool_msg[0]["content"]["data"]["count"], 1)

    def test_tool_error_does_not_abort(self):
        # Model calls a tool that errors (missing file) → loop continues → final.
        def chat(messages, tools):
            if not any(m.get("role") == "tool" for m in messages):
                return {"content": None, "tool_calls": [{
                    "id": "call_1", "name": "fix_bad_lines",
                    "arguments": '{"input_path": "no_such.tif"}'}]}
            return {"content": "文件不存在，无法修复", "tool_calls": []}

        r = run_loop(cfg(), "修复", chat=chat)
        self.assertTrue(r["ok"])          # loop survived the tool error
        self.assertEqual(r["turns"], 2)
        tool_msg = [m for m in r["messages"] if m.get("role") == "tool"]
        self.assertFalse(tool_msg[0]["content"]["ok"])
        self.assertIn("not found", tool_msg[0]["content"]["error"])

    def test_unknown_tool_fed_back(self):
        def chat(messages, tools):
            if not any(m.get("role") == "tool" for m in messages):
                return {"content": None, "tool_calls": [{
                    "id": "c1", "name": "ghost_tool", "arguments": "{}"}]}
            return {"content": "用了不存在的工具", "tool_calls": []}

        r = run_loop(cfg(), "x", chat=chat)
        self.assertTrue(r["ok"])
        tool_msg = [m for m in r["messages"] if m.get("role") == "tool"]
        self.assertIn("unknown tool", tool_msg[0]["content"]["error"])

    def test_max_turns_caps_runaway(self):
        def chat(messages, tools):
            return {"content": None, "tool_calls": [{
                "id": f"c{len(messages)}", "name": "fix_bad_lines",
                "arguments": '{"input_path": "no_such.tif"}'}]}

        r = run_loop(cfg(), "x", max_turns=3, chat=chat)
        self.assertFalse(r["ok"])
        self.assertEqual(r["turns"], 3)
        self.assertIn("max_turns", r["error"])

    def test_llm_call_error_surfaces(self):
        def chat(messages, tools):
            raise ConnectionError("boom")

        r = run_loop(cfg(), "x", chat=chat)
        self.assertFalse(r["ok"])
        self.assertIn("boom", r["error"])


def temp_store():
    d = tempfile.TemporaryDirectory()
    return Store(os.path.join(d.name, "db.sqlite")), d


class TestRepair(unittest.TestCase):
    """Unclosed-turn repair (deepseek-harness repair.ts semantics)."""

    def test_unclosed_turn_gets_synthesized_result(self):
        msgs = [
            {"role": "system", "content": "s"},
            {"role": "user", "content": "u"},
            {"role": "assistant", "content": "",
             "tool_calls": [{"id": "c1", "type": "function",
                             "function": {"name": "run_sr", "arguments": "{}"}}]},
        ]
        fixed = _repair_unclosed_turn(msgs)
        self.assertEqual(len(fixed), 4)
        self.assertEqual(fixed[-1]["role"], "tool")
        self.assertEqual(fixed[-1]["tool_call_id"], "c1")
        payload = json.loads(fixed[-1]["content"])
        self.assertFalse(payload["ok"])
        self.assertIn("interrupted", payload["error"])
        self.assertIn("not blindly retry", payload["error"])

    def test_closed_turn_unchanged(self):
        msgs = [
            {"role": "assistant", "content": "",
             "tool_calls": [{"id": "c1", "type": "function",
                             "function": {"name": "x", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "c1", "content": "{}"},
        ]
        self.assertEqual(_repair_unclosed_turn(msgs), msgs)

    def test_final_answer_unchanged(self):
        msgs = [{"role": "assistant", "content": "final"}]
        self.assertEqual(_repair_unclosed_turn(msgs), msgs)

    def test_partial_multi_call_turn_repaired(self):
        # two calls, one result committed → the missing one is repaired
        msgs = [
            {"role": "assistant", "content": "",
             "tool_calls": [{"id": "a", "type": "function",
                             "function": {"name": "x", "arguments": "{}"}},
                            {"id": "b", "type": "function",
                             "function": {"name": "y", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "a", "content": "{}"},
        ]
        fixed = _repair_unclosed_turn(msgs)
        self.assertEqual(len(fixed), 3)
        self.assertEqual(fixed[-1]["tool_call_id"], "b")
        self.assertIn("interrupted", json.loads(fixed[-1]["content"])["error"])


class TestPersistence(unittest.TestCase):
    """P0① — store integration: messages persist; resume restores context."""

    def _store(self):
        self._tmp = tempfile.TemporaryDirectory()
        return Store(os.path.join(self._tmp.name, "db.sqlite"))

    def _cleanup(self, store):
        store.close()
        self._tmp.cleanup()

    def test_messages_persisted(self):
        store = self._store()
        try:
            def chat(messages, tools):
                return {"content": "好", "tool_calls": []}

            r = run_loop(cfg(), "hi", chat=chat, store=store)
            self.assertTrue(r["ok"])
            self.assertTrue(r["session_id"])
            roles = [m["role"] for m in store.get_messages(r["session_id"])]
            self.assertEqual(roles, ["system", "user", "assistant"])
        finally:
            self._cleanup(store)

    def test_tool_turn_messages_committed_in_order(self):
        # the assistant tool_calls message is committed before the tool result
        # (checkpoint before side effect), in wire format.
        store = self._store()
        try:
            def chat(messages, tools):
                if not any(m.get("role") == "tool" for m in messages):
                    return {"content": None, "tool_calls": [{
                        "id": "call_1", "name": "fix_bad_lines",
                        "arguments": '{"input_path": "no_such.tif"}'}]}
                return {"content": "done", "tool_calls": []}

            r = run_loop(cfg(), "x", chat=chat, store=store)
            self.assertTrue(r["ok"])
            persisted = store.get_messages(r["session_id"])
            roles = [m["role"] for m in persisted]
            self.assertEqual(roles, ["system", "user", "assistant", "tool",
                                     "assistant"])
            self.assertEqual(persisted[2]["tool_calls"][0]["id"], "call_1")
            self.assertEqual(persisted[3]["tool_call_id"], "call_1")
            self.assertIn("not found",
                          json.loads(persisted[3]["content"])["error"])
        finally:
            self._cleanup(store)

    def test_session_status_done(self):
        store = self._store()
        try:
            def chat(messages, tools):
                return {"content": "好", "tool_calls": []}

            r = run_loop(cfg(), "hi", chat=chat, store=store)
            self.assertEqual(store.get_session(r["session_id"])["status"], "done")
        finally:
            self._cleanup(store)

    def test_resume_repairs_unclosed_turn(self):
        # simulate a crash mid-tool: assistant tool_calls durable, result lost.
        store = self._store()
        try:
            sid = store.create_session()
            store.append_message(sid, {"role": "system", "content": "sys"})
            store.append_message(sid, {"role": "user", "content": "继续"})
            store.append_message(sid, {"role": "assistant", "content": "",
                "tool_calls": [{"id": "call_9", "type": "function",
                                "function": {"name": "fix_bad_lines",
                                             "arguments": "{}"}}]})

            seen = {}

            def chat(messages, tools):
                seen["messages"] = list(messages)   # snapshot before the loop mutates it
                return {"content": "已核实，继续", "tool_calls": []}

            with mock.patch("backend.agent.loop.call_tool") as mt:
                r = run_loop(cfg(), "继续", chat=chat, store=store,
                             session_id=sid, resume=True)
                self.assertTrue(r["ok"])
                self.assertEqual(r["session_id"], sid)
                # the model saw the synthesized repair result, not a re-run
                mt.assert_not_called()

            tool_msg = [m for m in seen["messages"]
                        if m["role"] == "tool"][-1]
            payload = json.loads(tool_msg["content"])
            self.assertFalse(payload["ok"])
            self.assertIn("interrupted", payload["error"])
            # the new prompt was appended as the next user message
            self.assertEqual(seen["messages"][-1],
                             {"role": "user", "content": "继续"})
            # transcript carries the repaired tool entry
            self.assertIn("interrupted",
                          [m for m in r["messages"]
                           if m.get("role") == "tool"][-1]["content"]["error"])
        finally:
            self._cleanup(store)

    def test_resume_completed_history_continues(self):
        store = self._store()
        try:
            sid = store.create_session()
            store.append_message(sid, {"role": "system", "content": "sys"})
            store.append_message(sid, {"role": "user", "content": "第一轮"})
            store.append_message(sid, {"role": "assistant",
                                       "content": "第一步结果"})

            calls = []

            def chat(messages, tools):
                calls.append(list(messages))   # snapshot (the list is mutated later)
                return {"content": "第二轮答复", "tool_calls": []}

            r = run_loop(cfg(), "第二轮", chat=chat, store=store,
                         session_id=sid, resume=True)
            self.assertTrue(r["ok"])
            self.assertEqual(r["turns"], 1)              # one new turn after resume
            contents = [m.get("content") for m in calls[0]]
            self.assertIn("第一步结果", contents)          # history carried
            self.assertEqual(calls[0][-1]["content"], "第二轮")
            self.assertIn("第一步结果",
                          [m.get("content") for m in r["messages"]])
        finally:
            self._cleanup(store)

    def test_resume_unknown_session_starts_fresh(self):
        store = self._store()
        try:
            def chat(messages, tools):
                return {"content": "新对话", "tool_calls": []}

            r = run_loop(cfg(), "hi", chat=chat, store=store,
                         session_id="ghost", resume=True)
            self.assertTrue(r["ok"])
            roles = [m["role"] for m in store.get_messages("ghost")]
            self.assertEqual(roles, ["system", "user", "assistant"])
        finally:
            self._cleanup(store)


def mock_cfg():
    return Config(llm_base_url="http://localhost/v1", llm_api_key="test",
                  llm_model="fake", llm_max_tokens=16, llm_temperature=0.0,
                  llm_timeout=5, llm_mock=True)


class TestMockChat(unittest.TestCase):
    """SR_LLM_MOCK fixed-script chat (api-contract.md §5.1): no endpoint, but the
    *real* search_scenes tool round trip runs so the SSE stream carries it."""

    def test_mock_runs_real_search_then_summarizes(self):
        def fake_call_tool(name, arguments_json):
            self.assertEqual(name, "search_scenes")
            return {"ok": True, "data": {
                "source": "disk", "count": 3,
                "results": [{"id": "s1"}, {"id": "s2"}, {"id": "s3"}]},
                "error": None}

        with mock.patch("backend.agent.loop.call_tool", fake_call_tool):
            r = run_loop(mock_cfg(), "帮我看看盘阵上有哪些场景")
        self.assertTrue(r["ok"])
        self.assertEqual(r["turns"], 2)
        self.assertIn("已检索盘阵场景 3 个", r["answer"])
        self.assertIn("source=disk", r["answer"])
        for sid in ("s1", "s2", "s3"):
            self.assertIn(sid, r["answer"])
        tools = [m for m in r["messages"] if m.get("role") == "tool"]
        self.assertEqual(len(tools), 1)
        self.assertEqual(tools[0]["name"], "search_scenes")

    def test_mock_err_result_still_answers(self):
        # search_scenes errors (scenes root missing) → mock still concludes
        def fake_call_tool(name, arguments_json):
            return {"ok": False, "data": None, "error": "boom"}

        with mock.patch("backend.agent.loop.call_tool", fake_call_tool):
            r = run_loop(mock_cfg(), "检索")
        self.assertTrue(r["ok"])
        self.assertEqual(r["turns"], 2)
        self.assertIn("已检索盘阵场景 0 个", r["answer"])


class TestOnStepSeam(unittest.TestCase):
    """on_step observation seam (api-contract.md §4.2) — events mirror the
    existing commit/return points; default None leaves the loop byte-identical."""

    def test_no_tool_final_emits_assistant(self):
        events = []

        def chat(messages, tools):
            return {"content": "直接答复", "tool_calls": []}

        r = run_loop(cfg(), "hi", chat=chat, on_step=events.append)
        self.assertTrue(r["ok"])
        self.assertEqual(events,
                         [{"type": "assistant", "content": "直接答复"}])

    def test_tool_roundtrip_event_order(self):
        events = []

        def chat(messages, tools):
            if not any(m.get("role") == "tool" for m in messages):
                return {"content": None, "tool_calls": [{
                    "id": "c1", "name": "fix_bad_lines",
                    "arguments": '{"input_path": "no_such.tif"}'}]}
            return {"content": "收尾", "tool_calls": []}

        r = run_loop(cfg(), "修复", chat=chat, on_step=events.append)
        self.assertTrue(r["ok"])
        types = [e["type"] for e in events]
        self.assertEqual(types, ["tool_call", "tool_result", "assistant"])
        self.assertEqual(events[0]["name"], "fix_bad_lines")
        self.assertEqual(events[0]["args"], {"input_path": "no_such.tif"})
        self.assertFalse(events[1]["ok"])
        self.assertIn("not found", events[1]["error"])
        self.assertEqual(events[2]["content"], "收尾")

    def test_llm_error_emits_error_event(self):
        events = []

        def chat(messages, tools):
            raise ConnectionError("boom")

        r = run_loop(cfg(), "x", chat=chat, on_step=events.append)
        self.assertFalse(r["ok"])
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["type"], "error")
        self.assertIn("boom", events[0]["error"])

    def test_max_turns_emits_error_event(self):
        events = []

        def chat(messages, tools):
            return {"content": None, "tool_calls": [{
                "id": f"c{len(messages)}", "name": "fix_bad_lines",
                "arguments": '{"input_path": "no_such.tif"}'}]}

        r = run_loop(cfg(), "x", max_turns=3, chat=chat, on_step=events.append)
        self.assertFalse(r["ok"])
        self.assertEqual(events[-1]["type"], "error")
        self.assertIn("max_turns", events[-1]["error"])


if __name__ == "__main__":
    unittest.main()
