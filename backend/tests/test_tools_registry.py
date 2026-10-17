import unittest

from backend.tools.contract import Tool, ToolRegistry
from backend.tools import build_builtin_registry


class TestToolRegistry(unittest.TestCase):
    def test_builtin_registry_is_independent_snapshot(self):
        first = build_builtin_registry()
        second = build_builtin_registry()
        self.assertIsNot(first, second)
        self.assertEqual([t.name for t in first.list()],
                         [t.name for t in second.list()])

    def test_duplicate_names_are_rejected(self):
        tool = Tool("echo", "echo", {"type": "object"}, lambda: {"ok": True, "data": 1, "error": None})
        registry = ToolRegistry([tool])
        with self.assertRaises(ValueError):
            registry.register(tool)

    def test_invoke_accepts_json_object_and_preserves_result_contract(self):
        registry = ToolRegistry([
            Tool("echo", "echo", {"type": "object"},
                 lambda value=0: {"ok": True, "data": value, "error": None})
        ])
        self.assertEqual(registry.invoke("echo", '{"value": 7}', source="test"),
                         {"ok": True, "data": 7, "error": None})
        self.assertFalse(registry.invoke("echo", "[]")["ok"])

    def test_unknown_and_exception_are_contract_errors(self):
        registry = ToolRegistry([
            Tool("explode", "explode", {"type": "object"},
                 lambda: (_ for _ in ()).throw(RuntimeError("boom")))
        ])
        self.assertIn("unknown tool", registry.invoke("missing")["error"])
        self.assertIn("RuntimeError: boom", registry.invoke("explode")["error"])

    def test_manifest_contains_extension_metadata(self):
        registry = ToolRegistry([
            Tool("quality", "quality", {"type": "object"}, lambda: {"ok": True, "data": None, "error": None},
                 version="2", permissions=("scene:read",))
        ])
        entry = registry.list()[0].manifest_entry()
        self.assertEqual(entry["version"], "2")
        self.assertEqual(entry["permissions"], ["scene:read"])


if __name__ == "__main__":
    unittest.main()
