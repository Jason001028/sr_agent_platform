import tempfile
import unittest
from pathlib import Path

from backend.agent.loop import run_loop
from backend.config import Config
from backend.runtime_context import AppContext
from backend.services.store import Store
from backend.tools.contract import Tool, ToolRegistry


class TestRuntimeContext(unittest.TestCase):
    def test_agent_can_run_from_explicit_context(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = Config("http://llm/v1", "", "mock", 16, 0.0, 5.0, True)
            store = Store(Path(td) / "agent.db")
            registry = ToolRegistry([
                Tool("echo", "echo", {"type": "object"},
                     lambda value="": {"ok": True, "data": value, "error": None})
            ])
            context = AppContext(cfg, store, registry, Path(td), 2.0)

            def chat(messages, tools):
                return {"finish_reason": "stop", "content": "完成", "tool_calls": []}

            result = run_loop(prompt="测试", context=context, chat=chat)
            self.assertTrue(result["ok"])
            self.assertEqual(result["answer"], "完成")
            self.assertIsNotNone(result["session_id"])
            store.close()

    def test_context_keeps_registry_and_store_explicit(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = Config("http://llm/v1", "", "mock", 16, 0.0, 5.0, True)
            context = AppContext(
                cfg, Store(Path(td) / "agent.db"), ToolRegistry(), None, 2.0)
            self.assertIs(context.config, cfg)
            self.assertIsInstance(context.store, Store)
            self.assertIsInstance(context.tool_registry, ToolRegistry)
            context.store.close()


if __name__ == "__main__":
    unittest.main()
