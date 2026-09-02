"""Minimal agent loop (M1) — a self-written state machine over the tool registry.

No langchain/langgraph dependency (agent-orchestration-research.md §5 M1).
Mechanisms borrowed, not code:
  * tool failure → fed back to the model as a tool result so it self-heals
    (langgraph prebuilt tool_node._handle_tool_error); the loop never aborts
    on a tool error.
  * iteration cap to stop runaway loops (langgraph recursion_limit / RetryPolicy).
  * append-only transcript (deepseek-harness event-log idea) — the message list
    is the single source of truth, persisted to SQLite (backend.services.store)
    before every side effect so a crashed process can resume the conversation.

Persistence (§5.1 / §6.3): pass a `store` and each message is committed before
the step that depends on it — the assistant's tool_calls message is durable
*before* the tools run ("checkpoint before side effect"), so a crash mid-tool
leaves a repairable unclosed turn rather than a committed result the model
never saw. `session_id` + `resume=True` continue a persisted session (the
interface P1⑦ CLI --resume will surface).

M1 is deliberately serial and blocking (exit code = loop outcome); FastAPI/SSE
and external-job idempotency (run_sr task table) are separate layers.
"""

from __future__ import annotations

import json

from openai import OpenAI

from backend.config import Config
from backend.tools.contract import list_tools

DEFAULT_SYSTEM_PROMPT = (
    "You are the SR (super-resolution) agent for a remote-sensing processing "
    "platform. Use the provided tools when a step of the user's request needs "
    "one; when done, answer the user concisely in Chinese."
)

_REPAIR_ERROR = (
    "interrupted: the previous run crashed while this tool executed; outcome "
    "unknown — do not blindly retry. Check the external system (e.g. "
    "sr_job_status) or ask the user before resubmitting.")

# SSE 观察事件类型（api-contract.md §4.2）：run_loop 不改状态机行为，
# 只是在这些既有 return/commit 点同步回调 on_step，供 REST 层桥接 SSE。
_EVENT_TYPES = ("tool_call", "tool_result", "assistant", "error")


def build_client(cfg: Config) -> OpenAI:
    """OpenAI SDK client pointed at any OpenAI-compatible endpoint."""
    return OpenAI(base_url=cfg.llm_base_url, api_key=cfg.llm_api_key,
                  timeout=cfg.llm_timeout)


def _mock_chat(cfg: Config):
    """Fixed-script fake LLM (SR_LLM_MOCK=1, api-contract.md §5.1).

    Deterministic, endpoint-free, and it *uses the real search_scenes tool* so
    the SSE stream carries a real tool_call/tool_result round trip:
      1. first assistant turn declares tool_call search_scenes (no args);
      2. once the tool result is fed back, it answers with a summary built
         from that result.
    It replaces only the LLM call layer — the loop state machine is untouched
    (still locked by the existing 18 tests)."""
    def chat(messages, tools):
        tool_results = [m for m in messages if m.get("role") == "tool"]
        if not tool_results:
            return {"content": None, "tool_calls": [{
                "id": "call_mock_search", "name": "search_scenes",
                "arguments": "{}"}]}
        # deterministic final answer from the last tool result
        ids, n, source = [], 0, "?"
        try:
            payload = json.loads(tool_results[-1]["content"])
            if payload.get("ok") and payload.get("data"):
                data = payload["data"]
                source = data.get("source", "?")
                n = data.get("count", len(data.get("results", [])))
                for s in data.get("results", [])[:5]:
                    ids.append(s.get("id", ""))
        except (TypeError, ValueError):
            pass
        text = (f"已检索盘阵场景 {n} 个（mock 模型，source={source}）"
                + (f"：{', '.join(ids)}。" if ids else "。"))
        return {"content": text, "tool_calls": []}
    return chat


def _parse_args(arguments) -> dict:
    try:
        v = json.loads(arguments or "{}")
        return v if isinstance(v, dict) else {"raw": v}
    except (TypeError, ValueError):
        return {"raw": arguments}


def parse_response(resp) -> dict:
    """Normalize an OpenAI chat-completions response to plain data."""
    msg = resp.choices[0].message
    tool_calls = []
    for tc in msg.tool_calls or []:
        tool_calls.append({"id": tc.id, "name": tc.function.name,
                           "arguments": tc.function.arguments})
    return {"content": msg.content, "tool_calls": tool_calls}


def call_tool(name, arguments_json) -> dict:
    """Execute a registered tool by name; never raises (contract boundary).

    Returns the contract's ok/err dict verbatim — an err() result is fed back
    to the model as ordinary tool output so it can correct course.
    """
    for t in list_tools():
        if t.name == name:
            try:
                return t.run(**json.loads(arguments_json or "{}"))
            except Exception as e:  # noqa: BLE001
                return {"ok": False, "data": None,
                        "error": f"{type(e).__name__}: {e}"}
    return {"ok": False, "data": None, "error": f"unknown tool: {name}"}


def _default_chat(cfg: Config):
    if cfg.llm_mock:
        return _mock_chat(cfg)
    client = build_client(cfg)

    def chat(messages, tools):
        resp = client.chat.completions.create(
            model=cfg.llm_model, messages=messages, tools=tools or None,
            max_tokens=cfg.llm_max_tokens, temperature=cfg.llm_temperature)
        return parse_response(resp)

    return chat


def _repair_unclosed_turn(messages: list[dict]) -> list[dict]:
    """Close a turn left open by a crash (deepseek-harness repair.ts).

    A crash mid-tool leaves the last assistant tool_calls without its tool
    results. Resume must not blindly re-run those tools — the side effect may
    already be committed (run_sr may have submitted a Slurm job). Each missing
    call is closed with a synthesized "result unknown — do not blindly retry"
    tool message so the model decides next (poll sr_job_status / ask the user).
    """
    fixed = list(messages)
    result_ids = {m.get("tool_call_id") for m in fixed if m.get("role") == "tool"}
    for i in range(len(fixed) - 1, -1, -1):
        m = fixed[i]
        if m.get("role") == "assistant" and m.get("tool_calls"):
            for tc in m["tool_calls"]:
                cid = tc["id"]
                if cid not in result_ids:
                    fixed.append({"role": "tool", "tool_call_id": cid,
                                  "content": json.dumps(
                                      {"ok": False, "data": None,
                                       "error": _REPAIR_ERROR},
                                      ensure_ascii=False)})
                    result_ids.add(cid)
            break  # only the most recent assistant turn can be open
    return fixed


def _transcript_from(messages: list[dict]) -> list[dict]:
    """Project persisted wire-format messages onto the loop's transcript shape
    (assistant + tool entries). system/user are context, not events — matching
    the fresh-run transcript. Reverses the OpenAI wire tool_calls nesting and
    parses tool content back to a dict."""
    transcript = []
    calls: dict[str, str] = {}
    for m in messages:
        if m["role"] == "assistant":
            tcs = [{"id": tc["id"], "name": tc["function"]["name"],
                    "arguments": tc["function"]["arguments"]}
                   for tc in m.get("tool_calls") or []]
            for tc in tcs:
                calls[tc["id"]] = tc["name"]
            transcript.append({"role": "assistant", "content": m.get("content"),
                               "tool_calls": tcs})
        elif m["role"] == "tool":
            try:
                content = json.loads(m["content"])
            except (TypeError, ValueError):
                content = m["content"]
            transcript.append({"role": "tool", "name": calls.get(m["tool_call_id"]),
                               "tool_call_id": m["tool_call_id"], "content": content})
    return transcript


def run_loop(cfg, prompt, *, max_turns=8, system_prompt=DEFAULT_SYSTEM_PROMPT,
             chat=None, verbose=False, store=None, session_id=None,
             resume=False, on_step=None):
    """Run the loop; returns {"ok", "turns", "messages", "answer", "error",
    "session_id"}.

    `chat(messages, tools)` → {"content", "tool_calls":[{id,name,arguments}]}
    is injectable for tests; the default builds an OpenAI client from cfg
    (or a fixed-script fake chat when cfg.llm_mock).

    `on_step(ev)` — optional observation seam (api-contract.md §4.2): called
    synchronously at the existing commit/return points with {"type": ...} events
    (tool_call / tool_result / assistant / error) so the REST layer can bridge
    them to SSE. Default None → byte-identical to the pre-seam behavior; the
    loop state machine is unchanged.

    `store` (backend.services.store.Store) enables SQLite persistence: every
    message is committed before the step that depends on it (checkpoint before
    side effect), so the session survives a process restart. `session_id` +
    `resume=True` continue a persisted session: history is reloaded and an
    unclosed turn (crash mid-tool) is closed with a synthetic "outcome unknown"
    result instead of blindly re-running the tool.
    """
    if chat is None:
        chat = _default_chat(cfg)

    if store is not None and session_id is None:
        session_id = store.create_session()

    if store is not None and session_id is not None and resume:
        persisted = store.get_messages(session_id)
        if persisted:
            # repair an unclosed turn first, then continue the conversation
            # with the caller's new prompt as the next user message.
            messages = _repair_unclosed_turn(persisted)
            messages.append({"role": "user", "content": prompt})
            store.append_message(session_id, messages[-1])
            transcript = _transcript_from(messages)
        else:
            messages = None
            transcript = []
    else:
        messages = None
        transcript = []

    if messages is None:
        messages = [{"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt}]
        transcript = []
        if store is not None:
            store.append_message(session_id, messages[0])
            store.append_message(session_id, messages[1])

    tools = [t.openai_function() for t in list_tools()]

    def commit(msg: dict):
        """Append to the wire list and checkpoint to the store immediately."""
        messages.append(msg)
        if store is not None:
            store.append_message(session_id, msg)

    for turn in range(1, max_turns + 1):
        try:
            out = chat(messages, tools)
        except Exception as e:  # noqa: BLE001 — surface cleanly, no retry loop
            error = f"{type(e).__name__}: {e}"
            if verbose:
                print(f"[turn {turn}] LLM call failed: {error}")
            transcript.append({"role": "error", "error": error, "turn": turn})
            if on_step is not None:
                on_step({"type": "error", "error": error, "turn": turn})
            if store is not None:
                store.set_session_status(session_id, "error")
            return {"ok": False, "turns": turn, "messages": transcript,
                    "answer": None, "error": error, "session_id": session_id}

        content = out.get("content")
        tool_calls = out.get("tool_calls") or []

        if verbose:
            print(f"[turn {turn}] assistant: {content!r} "
                  f"tool_calls={len(tool_calls)}")

        if not tool_calls:
            commit({"role": "assistant", "content": content})
            transcript.append({"role": "assistant", "content": content})
            if on_step is not None and content:
                on_step({"type": "assistant", "content": content})
            if store is not None:
                store.set_session_status(session_id, "done")
            return {"ok": True, "turns": turn, "messages": transcript,
                    "answer": content or "", "error": None,
                    "session_id": session_id}

        # Checkpoint BEFORE the tools run: the assistant's intent is durable
        # before any external side effect, so a crash mid-tool leaves a
        # repairable unclosed turn, never a committed result the model missed.
        commit({
            "role": "assistant", "content": content or "",
            "tool_calls": [
                {"id": tc["id"], "type": "function",
                 "function": {"name": tc["name"], "arguments": tc["arguments"]}}
                for tc in tool_calls],
        })
        transcript.append({"role": "assistant", "content": content,
                           "tool_calls": tool_calls})
        if on_step is not None and content:
            on_step({"type": "assistant", "content": content})

        for tc in tool_calls:
            if on_step is not None:
                on_step({"type": "tool_call", "name": tc["name"],
                         "args": _parse_args(tc["arguments"])})
            result = call_tool(tc["name"], tc["arguments"])
            if verbose:
                status = "ok" if result["ok"] else "ERR: " + (result["error"] or "")
                print(f"[turn {turn}] tool {tc['name']} → {status}")
            if on_step is not None:
                on_step({"type": "tool_result", "name": tc["name"],
                         "ok": bool(result["ok"]), "data": result["data"],
                         "error": result["error"]})
            commit({"role": "tool", "tool_call_id": tc["id"],
                    "content": json.dumps(result, ensure_ascii=False)})
            transcript.append({"role": "tool", "name": tc["name"],
                               "tool_call_id": tc["id"], "content": result})

    error = f"max_turns={max_turns} reached"
    if verbose:
        print(f"[loop] {error}")
    transcript.append({"role": "error", "error": error, "turn": max_turns})
    if on_step is not None:
        on_step({"type": "error", "error": error, "turn": max_turns})
    if store is not None:
        store.set_session_status(session_id, "error")
    return {"ok": False, "turns": max_turns, "messages": transcript,
            "answer": None, "error": error, "session_id": session_id}
