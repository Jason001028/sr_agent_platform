"""Tests for the M1 agent loop (fake chat seam — no network, no API key).

The loop's `chat=` seam takes plain dicts, so these tests validate the
state-machine mechanics: registry lookup, tool-result backfeed, error
self-healing, iteration cap and clean error surfacing.
"""

import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

from backend.agent.loop import run_loop
from backend.config import Config
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


if __name__ == "__main__":
    unittest.main()
