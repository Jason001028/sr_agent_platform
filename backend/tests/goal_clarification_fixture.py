"""Local browser fixture: real REST/SQLite/worker, deterministic model/executor."""
from backend.api.app import create_app as platform_app
from backend.tools.contract import Tool, ToolRegistry, ok


def create_app():
    app = platform_app()
    app.state.fixture_calls = 0
    app.state.fixture_executions = 0

    def finish():
        app.state.fixture_executions += 1
        return ok({"artifact": "browser-test-only"})

    def chat(messages, tools):
        app.state.fixture_calls += 1
        answer = any(m["role"] == "user" and "针对澄清问题" in str(m.get("content")) for m in messages)
        done = any(m["role"] == "tool" and "browser-test-only" in m["content"] for m in messages)
        if done:
            return {"finish_reason": "stop", "content": "已按补充信息完成任务", "tool_calls": []}
        name = "fixture_finish" if answer else "request_clarification"
        args = '{}' if answer else '{"question":"请选择需要处理的区域","options":[{"id":"north","label":"北侧区域"},{"id":"south","label":"南侧区域"}],"recommended_option":"north","allow_free_text":true}'
        return {"finish_reason": "tool_calls", "content": None,
                "tool_calls": [{"id": "fixture_" + name, "name": name, "arguments": args}]}

    app.state.tool_registry = ToolRegistry([Tool("fixture_finish", "fake executor", {}, finish)])
    app.state.goal_chat = chat

    @app.get('/test/clarification-state')
    def state():
        return {"model_calls": app.state.fixture_calls, "executions": app.state.fixture_executions}

    return app
