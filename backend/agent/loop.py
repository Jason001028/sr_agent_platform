"""Minimal agent loop (M1) — a self-written state machine over the tool registry.

No langchain/langgraph dependency (agent-orchestration-research.md §5 M1).
Mechanisms borrowed, not code:
  * tool failure → fed back to the model as a tool result so it self-heals
    (langgraph prebuilt tool_node._handle_tool_error); the loop never aborts
    on a tool error.
  * iteration cap to stop runaway loops (langgraph recursion_limit / RetryPolicy).
  * append-only transcript (deepseek-harness event-log idea) — the message list
    is the single source of truth for persistence later.

M1 is deliberately serial and blocking (exit code = loop outcome); FastAPI/SSE,
SQLite persistence and external-job idempotency are later layers.
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


def build_client(cfg: Config) -> OpenAI:
    """OpenAI SDK client pointed at any OpenAI-compatible endpoint."""
    return OpenAI(base_url=cfg.llm_base_url, api_key=cfg.llm_api_key,
                  timeout=cfg.llm_timeout)


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
    client = build_client(cfg)

    def chat(messages, tools):
        resp = client.chat.completions.create(
            model=cfg.llm_model, messages=messages, tools=tools or None,
            max_tokens=cfg.llm_max_tokens, temperature=cfg.llm_temperature)
        return parse_response(resp)

    return chat


def run_loop(cfg, prompt, *, max_turns=8, system_prompt=DEFAULT_SYSTEM_PROMPT,
             chat=None, verbose=False):
    """Run the loop; returns {"ok", "turns", "messages", "answer", "error"}.

    `chat(messages, tools)` → {"content", "tool_calls":[{id,name,arguments}]}
    is injectable for tests; the default builds an OpenAI client from cfg.
    """
    if chat is None:
        chat = _default_chat(cfg)

    messages = [{"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt}]
    transcript = []
    tools = [t.openai_function() for t in list_tools()]

    for turn in range(1, max_turns + 1):
        try:
            out = chat(messages, tools)
        except Exception as e:  # noqa: BLE001 — surface cleanly, no retry loop
            error = f"{type(e).__name__}: {e}"
            if verbose:
                print(f"[turn {turn}] LLM call failed: {error}")
            transcript.append({"role": "error", "error": error, "turn": turn})
            return {"ok": False, "turns": turn, "messages": transcript,
                    "answer": None, "error": error}

        content = out.get("content")
        tool_calls = out.get("tool_calls") or []

        if verbose:
            print(f"[turn {turn}] assistant: {content!r} "
                  f"tool_calls={len(tool_calls)}")

        if not tool_calls:
            transcript.append({"role": "assistant", "content": content})
            return {"ok": True, "turns": turn, "messages": transcript,
                    "answer": content or "", "error": None}

        messages.append({
            "role": "assistant", "content": content or "",
            "tool_calls": [
                {"id": tc["id"], "type": "function",
                 "function": {"name": tc["name"], "arguments": tc["arguments"]}}
                for tc in tool_calls],
        })
        transcript.append({"role": "assistant", "content": content,
                           "tool_calls": tool_calls})

        for tc in tool_calls:
            result = call_tool(tc["name"], tc["arguments"])
            if verbose:
                status = "ok" if result["ok"] else "ERR: " + (result["error"] or "")
                print(f"[turn {turn}] tool {tc['name']} → {status}")
            messages.append({"role": "tool", "tool_call_id": tc["id"],
                             "content": json.dumps(result, ensure_ascii=False)})
            transcript.append({"role": "tool", "name": tc["name"],
                               "tool_call_id": tc["id"], "content": result})

    error = f"max_turns={max_turns} reached"
    if verbose:
        print(f"[loop] {error}")
    transcript.append({"role": "error", "error": error, "turn": max_turns})
    return {"ok": False, "turns": max_turns, "messages": transcript,
            "answer": None, "error": error}
