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

import inspect
import json
import time
import uuid

from openai import OpenAI

from backend.config import Config
from backend.services import chat_images
from backend.services import plugins as plugin_svc
from backend.services.trajectory import TrajectoryRecorder
from backend.tools.contract import ToolRegistry, default_registry
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
# llm_phase / reasoning / token 是 2026-10-21 增的模型增量事件：仅在有观察者
# 时由流式适配层产生；工具状态机与落库形状一个字未动。
_EVENT_TYPES = ("tool_call", "tool_result", "assistant", "error",
                "llm_phase", "reasoning", "token")


def build_client(cfg: Config) -> OpenAI:
    """OpenAI SDK client pointed at any OpenAI-compatible endpoint."""
    return OpenAI(base_url=cfg.llm_base_url, api_key=cfg.llm_api_key,
                  timeout=cfg.llm_timeout)


def _mock_emit(on_delta, event: dict) -> None:
    if on_delta is not None:
        on_delta(event)


def _mock_emit_reasoning(on_delta, text: str, sink: list[str]) -> None:
    """模拟 Qwen 的 reasoning_content 增量；非流式（on_delta=None）则什么都不发，
    保持老事件序列与落库形状逐字不变。"""
    if on_delta is None:
        return
    sink.append(text)
    on_delta({"type": "reasoning", "delta": text})


def _mock_emit_text(on_delta, text: str) -> None:
    if on_delta is None:
        return
    step = max(1, (len(text) + 3) // 4)      # four deterministic deltas
    for i in range(0, len(text), step):
        on_delta({"type": "token", "delta": text[i:i + step]})


def _mock_chat(cfg: Config):
    """Fixed-script fake LLM (SR_LLM_MOCK=1, api-contract.md §5.1).

    Deterministic, endpoint-free, and it *uses the real search_scenes tool* so
    the SSE stream carries a real tool_call/tool_result round trip:
      1. first assistant turn declares tool_call search_scenes (no args);
      2. once the tool result is fed back, it answers with a summary built
         from that result.
    It replaces only the LLM call layer — the loop state machine is untouched
    (still locked by the existing 18 tests).

    When a delta callback is supplied it also emits the same *streaming* events
    a real Qwen endpoint would (reasoning → tokens → tool_call), so the SSE /
    frontend tests can lock the streaming path without a model endpoint.
    """
    def chat(messages, tools, on_delta=None):
        reasoning: list[str] = []
        tool_results = [m for m in messages if m.get("role") == "tool"]
        if not tool_results:
            _mock_emit_reasoning(on_delta, "先检索盘阵场景。", reasoning)
            _mock_emit(on_delta, {"type": "tool_call", "name": "search_scenes"})
            return {"content": None, "tool_calls": [{
                "id": "call_mock_search", "name": "search_scenes",
                "arguments": "{}"}],
                "reasoning": "".join(reasoning) or None}
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
        _mock_emit_reasoning(on_delta, "整理检索结果并生成答复。", reasoning)
        _mock_emit_text(on_delta, text)
        return {"content": text, "tool_calls": [],
                "reasoning": "".join(reasoning) or None}
    return chat


def _parse_args(arguments) -> dict:
    try:
        v = json.loads(arguments or "{}")
        return v if isinstance(v, dict) else {"raw": v}
    except (TypeError, ValueError):
        return {"raw": arguments}


def _normalize_usage(raw) -> dict | None:
    """Normalize OpenAI-compatible usage, or return None when unavailable."""
    if raw is None:
        return None
    def value(name: str) -> int:
        if isinstance(raw, dict):
            v = raw.get(name)
        else:
            v = getattr(raw, name, None)
        try:
            return max(0, int(v or 0))
        except (TypeError, ValueError):
            return 0
    prompt = value("prompt_tokens")
    completion = value("completion_tokens")
    total = value("total_tokens") or prompt + completion
    if not (prompt or completion or total):
        return None
    return {"prompt_tokens": prompt, "completion_tokens": completion,
            "total_tokens": total, "source": "provider"}


def _empty_usage() -> dict:
    return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
            "calls": 0, "source": "unavailable", "events": []}


def _usage_public(usage: dict) -> dict:
    return {k: usage[k] for k in
            ("prompt_tokens", "completion_tokens", "total_tokens",
             "calls", "source")}


def _field(obj, name: str):
    """Read a declared *or extra* field from an OpenAI SDK object.

    ``reasoning_content`` is not part of the OpenAI schema, so it arrives as a
    pydantic extra: newer SDKs expose it via attribute access, older ones only
    through ``model_extra``. Two spellings are in the wild (llama.cpp / vLLM
    use ``reasoning_content``; some gateways use ``reasoning``)."""
    if obj is None:
        return None
    value = getattr(obj, name, None)
    if value is not None:
        return value
    extra = getattr(obj, "model_extra", None)
    if isinstance(extra, dict):
        return extra.get(name)
    return None


def _text_field(obj, name: str) -> str:
    value = _field(obj, name)
    return value if isinstance(value, str) else ""


def _accepts_on_delta(chat) -> bool:
    """Whether an injectable chat callable opted into the streaming seam.

    Existing test doubles / custom chats take ``(messages, tools)`` only; they
    keep working untouched because run_loop falls back to that call shape.
    """
    try:
        params = inspect.signature(chat).parameters
    except (TypeError, ValueError):
        return False
    if "on_delta" in params:
        return True
    return any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values())


def _emit_delta(on_delta, event: dict) -> None:
    """Observation must never break the model call (client may have gone away)."""
    if on_delta is None:
        return
    try:
        on_delta(event)
    except Exception:  # noqa: BLE001
        pass


def _delta_sink(on_step):
    """Turn raw model deltas into the public SSE vocabulary.

    Phase transitions are emitted once per LLM call: reasoning → ``thinking``,
    answer text → ``answering``, tool-call argument fragments → ``tool_call``.
    This keeps "what the model is doing" separate from "what the tool did".
    """
    state = {"phase": None, "reasoning": False}

    def sink(event: dict) -> None:
        if on_step is None:
            return
        kind = event.get("type")
        delta = event.get("delta")
        if kind == "reasoning":
            if state["phase"] != "thinking":
                state["phase"] = "thinking"
                on_step({"type": "llm_phase", "phase": "thinking"})
            state["reasoning"] = True
            if delta:
                on_step({"type": "reasoning", "delta": delta})
        elif kind == "token":
            if state["phase"] != "answering":
                state["phase"] = "answering"
                on_step({"type": "llm_phase", "phase": "answering"})
            if delta:
                on_step({"type": "token", "delta": delta})
        elif kind == "tool_call":
            if state["phase"] != "tool_call":
                state["phase"] = "tool_call"
                on_step({"type": "llm_phase", "phase": "tool_call",
                         "name": event.get("name")})

    return sink, state


def _consume_stream(stream, on_delta) -> dict:
    """Accumulate an OpenAI-compatible chunk stream into one response dict.

    Content and reasoning are forwarded as deltas; tool-call fragments are
    reassembled by ``index`` (name once, arguments concatenated). The returned
    shape matches ``parse_response`` so the loop state machine cannot tell the
    difference.
    """
    content_parts: list[str] = []
    reasoning_parts: list[str] = []
    calls: dict[int, dict] = {}
    usage = None
    try:
        for chunk in stream:
            chunk_usage = getattr(chunk, "usage", None)
            if chunk_usage is not None:
                usage = chunk_usage
            choices = getattr(chunk, "choices", None) or []
            if not choices:
                continue                    # include_usage final chunk
            delta = getattr(choices[0], "delta", None)
            if delta is None:
                continue

            reasoning = (_text_field(delta, "reasoning_content")
                         or _text_field(delta, "reasoning"))
            if reasoning:
                reasoning_parts.append(reasoning)
                _emit_delta(on_delta, {"type": "reasoning", "delta": reasoning})

            text = _text_field(delta, "content")
            if text:
                content_parts.append(text)
                _emit_delta(on_delta, {"type": "token", "delta": text})

            for part in getattr(delta, "tool_calls", None) or []:
                index = getattr(part, "index", 0)
                if not isinstance(index, int):
                    index = 0
                slot = calls.setdefault(
                    index, {"id": "", "name": "", "arguments": ""})
                call_id = getattr(part, "id", None)
                if call_id:
                    slot["id"] = call_id
                fn = getattr(part, "function", None)
                if fn is None:
                    continue
                name = getattr(fn, "name", None) or ""
                if not slot["name"]:
                    if name:
                        slot["name"] = name
                        _emit_delta(on_delta,
                                    {"type": "tool_call", "name": name})
                elif name and name != slot["name"]:
                    slot["name"] += name       # fragmented name (rare)
                args = getattr(fn, "arguments", None)
                if args:
                    slot["arguments"] += args
    finally:
        close = getattr(stream, "close", None)
        if callable(close):
            try:
                close()
            except Exception:  # noqa: BLE001
                pass

    tool_calls = []
    for index in sorted(calls):
        slot = calls[index]
        if not slot["name"]:
            continue
        tool_calls.append({
            "id": slot["id"] or "call_" + uuid.uuid4().hex,
            "name": slot["name"],
            "arguments": slot["arguments"] or "{}",
        })
    return {"content": "".join(content_parts) or None,
            "tool_calls": tool_calls,
            "reasoning": "".join(reasoning_parts) or None,
            "usage": _normalize_usage(usage)}


def _stream_chat(client, cfg: Config, wire, tools, on_delta) -> dict:
    """One streaming chat-completions call, aggregated for the loop."""
    params = dict(
        model=cfg.llm_model, messages=wire, tools=tools or None,
        max_tokens=cfg.llm_max_tokens, temperature=cfg.llm_temperature,
        stream=True)
    try:
        # include_usage is what makes the metrics row survive the switch to
        # streaming (usage only arrives in the final chunk when requested).
        stream = client.chat.completions.create(
            **params, stream_options={"include_usage": True})
    except Exception as exc:
        # Some OpenAI-compatible servers reject stream_options. The request is
        # read-only and no token has been produced yet, so one retry without
        # the field is safe. Other errors surface unchanged.
        if getattr(exc, "status_code", None) not in (400, 422):
            raise
        stream = client.chat.completions.create(**params)
    return _consume_stream(stream, on_delta)


def parse_response(resp) -> dict:
    """Normalize an OpenAI chat-completions response to plain data."""
    msg = resp.choices[0].message
    tool_calls = []
    for tc in msg.tool_calls or []:
        tool_calls.append({"id": tc.id, "name": tc.function.name,
                           "arguments": tc.function.arguments})
    reasoning = (_text_field(msg, "reasoning_content")
                 or _text_field(msg, "reasoning"))
    return {"content": msg.content, "tool_calls": tool_calls,
            "reasoning": reasoning or None,
            "usage": _normalize_usage(getattr(resp, "usage", None))}


def call_tool(name, arguments_json, registry: ToolRegistry | None = None,
              allowed_names: set[str] | None = None) -> dict:
    """Execute a tool through the shared registry boundary."""
    if allowed_names is not None and name not in allowed_names:
        return {"ok": False, "data": None,
                "error": f"tool disabled on current machine: {name}"}
    active = registry or default_registry()
    return active.invoke(name, arguments_json, source="agent")


def _default_chat(cfg: Config):
    if cfg.llm_mock:
        return _mock_chat(cfg)
    client = build_client(cfg)
    # 一次回合内同一张截图的 data URL 只拼一次（每个 LLM 调用都会过一遍水合）。
    image_cache: dict[str, str] = {}

    def chat(messages, tools, on_delta=None):
        # 截图在库里只有相对 URL，到这一步才现读盘换成 data URL（§3.2.1）。
        # mock 路在上面的 return 就走掉了 —— 它不读图，也不该碰盘。
        wire = chat_images.hydrate_images(messages, cache=image_cache)
        if on_delta is not None:
            return _stream_chat(client, cfg, wire, tools, on_delta)
        resp = client.chat.completions.create(
            model=cfg.llm_model, messages=wire, tools=tools or None,
            max_tokens=cfg.llm_max_tokens, temperature=cfg.llm_temperature)
        return parse_response(resp)

    return chat


def _repair_unclosed_turn(messages: list[dict]) -> list[dict]:
    """Close a turn left open by a crash (deepseek-harness repair.ts).

    A crash mid-tool leaves the last assistant tool_calls without its tool
    results. Resume must not blindly re-run those tools — the side effect may
    already be committed (run_sr may have submitted an SR job). Each missing
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


def run_loop(cfg=None, prompt=None, *, max_turns=8, system_prompt=DEFAULT_SYSTEM_PROMPT,
             chat=None, verbose=False, store=None, session_id=None,
             resume=False, on_step=None, registry: ToolRegistry | None = None,
             context=None, machine_id=None, stream=None):
    """Run the loop; returns {"ok", "turns", "messages", "answer", "error",
    "session_id"}.

    `prompt` is either a plain string or an OpenAI content-block list (a user
    turn with screenshots, see services/chat_images.py). Both shapes are stored
    as the user message verbatim; only the wire call sees images.

    `chat(messages, tools)` → {"content", "tool_calls":[{id,name,arguments}]}
    is injectable for tests; the default builds an OpenAI client from cfg
    (or a fixed-script fake chat when cfg.llm_mock).

    `on_step(ev)` — optional observation seam (api-contract.md §4.2): called
    synchronously at the existing commit/return points with {"type": ...} events
    (tool_call / tool_result / assistant / error) so the REST layer can bridge
    them to SSE. Default None → byte-identical to the pre-seam behavior; the
    loop state machine is unchanged.

    `stream` — ask the chat layer for model deltas (default: ``cfg.llm_stream``,
    itself default-on). Only meaningful when an ``on_step`` observer exists and
    the chat callable accepts ``on_delta``; otherwise the call shape stays
    ``chat(messages, tools)``. Streaming adds ``llm_phase`` / ``reasoning`` /
    ``token`` observation events *before* the unchanged assistant/tool events.
    Nothing extra is written to the store: the assistant message still contains
    only content + tool_calls.

    `store` (backend.services.store.Store) enables SQLite persistence: every
    message is committed before the step that depends on it (checkpoint before
    side effect), so the session survives a process restart. `session_id` +
    `resume=True` continue a persisted session: history is reloaded and an
    unclosed turn (crash mid-tool) is closed with a synthetic "outcome unknown"
    result instead of blindly re-running the tool.
    """
    if context is not None:
        if cfg is None:
            cfg = context.config
        if store is None:
            store = context.store
        if registry is None:
            registry = context.tool_registry
    if cfg is None:
        raise ValueError("run_loop requires cfg or context")

    if chat is None:
        chat = _default_chat(cfg)

    if stream is None:
        stream = bool(getattr(cfg, "llm_stream", True))
    # Streaming only buys something when someone is watching; the CLI (no
    # observer) keeps the one-shot call and avoids parsing a stream for nothing.
    stream = bool(stream and on_step is not None and _accepts_on_delta(chat))

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

    allowed_names = None
    if store is not None and machine_id:
        allowed_names = plugin_svc.enabled_plugin_ids(store, machine_id)
    active_registry = registry or default_registry()
    tools = active_registry.manifest()
    if registry is None and allowed_names is not None:
        tools = [t.openai_function() for t in list_tools() if t.name in allowed_names]

    trajectory = None
    if store is not None and session_id is not None:
        trajectory = TrajectoryRecorder(
            store, session_id=session_id, prompt=prompt,
            machine_id=machine_id, model=getattr(cfg, "llm_model", None),
            system_prompt=system_prompt, tools=tools)

    def finish_run(result: dict) -> dict:
        if trajectory is not None:
            status = "succeeded" if result.get("ok") else "failed"
            trajectory.finish(status=status, answer=result.get("answer"),
                              error=result.get("error"))
            result["run_id"] = trajectory.run_id
        return result

    def commit(msg: dict):
        """Append to the wire list and checkpoint to the store immediately."""
        messages.append(msg)
        if store is not None:
            store.append_message(session_id, msg)

    usage = _empty_usage()
    call_index = 0

    for turn in range(1, max_turns + 1):
        if trajectory is not None:
            trajectory.event("turn_start", turn=turn, status="running")
        llm_started = time.time()
        llm_event = None
        if trajectory is not None:
            llm_event = trajectory.event(
                "llm_request_start", turn=turn, status="started",
                payload={"message_count": len(messages), "tool_count": len(tools)},
                started_at=llm_started)
        on_delta, delta_state = (None, None)
        if stream:
            on_delta, delta_state = _delta_sink(on_step)
        try:
            if on_delta is not None:
                out = chat(messages, tools, on_delta=on_delta)
            else:
                out = chat(messages, tools)
        except Exception as e:  # noqa: BLE001 — surface cleanly, no retry loop
            error = f"{type(e).__name__}: {e}"
            if trajectory is not None:
                ended = time.time()
                trajectory.event("llm_request_end", turn=turn,
                                 parent_event_id=llm_event["event_id"] if llm_event else None,
                                 status="failed", payload={"error": error},
                                 started_at=llm_started, finished_at=ended,
                                 duration_ms=max(0, int((ended - llm_started) * 1000)))
                trajectory.event("error", turn=turn, status="failed",
                                 payload={"error": error})
            if verbose:
                print(f"[turn {turn}] LLM call failed: {error}")
            transcript.append({"role": "error", "error": error, "turn": turn})
            if on_step is not None:
                on_step({"type": "error", "error": error, "turn": turn})
            if store is not None:
                store.set_session_status(session_id, "error")
            return finish_run({"ok": False, "turns": turn, "messages": transcript,
                               "answer": None, "error": error,
                               "session_id": session_id, "usage": usage})

        content = out.get("content")
        tool_calls = out.get("tool_calls") or []
        llm_finished = time.time()
        if trajectory is not None:
            trajectory.event(
                "llm_request_end", turn=turn,
                parent_event_id=llm_event["event_id"] if llm_event else None,
                status="succeeded", payload={"tool_call_count": len(tool_calls)},
                started_at=llm_started, finished_at=llm_finished,
                duration_ms=max(0, int((llm_finished - llm_started) * 1000)))
        # 非流式响应也可能带回 reasoning_content（llama.cpp / vLLM 的
        # message.reasoning_content）。流式路已在 delta 里发过，这里只补没发过的。
        reasoning = out.get("reasoning")
        if reasoning and on_step is not None and (
                delta_state is None or not delta_state["reasoning"]):
            on_step({"type": "llm_phase", "phase": "thinking"})
            on_step({"type": "reasoning", "delta": reasoning})

        call_usage = _normalize_usage(out.get("usage"))
        if call_usage is not None:
            call_index += 1
            event = {**call_usage, "turn": turn, "call_index": call_index}
            usage["prompt_tokens"] += call_usage["prompt_tokens"]
            usage["completion_tokens"] += call_usage["completion_tokens"]
            usage["total_tokens"] += call_usage["total_tokens"]
            usage["calls"] += 1
            usage["source"] = call_usage["source"]
            usage["events"].append(event)
            if store is not None and session_id is not None:
                try:
                    store.append_llm_usage(session_id, **event)
                except Exception:  # noqa: BLE001 — metrics must not break chat
                    pass
            if on_step is not None:
                on_step({"type": "usage", "usage": event,
                         "aggregate": _usage_public(usage)})
            if trajectory is not None:
                trajectory.event("usage", turn=turn,
                                 parent_event_id=llm_event["event_id"] if llm_event else None,
                                 status="recorded", payload=event)

        if verbose:
            print(f"[turn {turn}] assistant: {content!r} "
                  f"tool_calls={len(tool_calls)}")

        if not tool_calls:
            commit({"role": "assistant", "content": content})
            transcript.append({"role": "assistant", "content": content})
            if trajectory is not None:
                trajectory.event("assistant", turn=turn, status="completed",
                                 payload={"content": content})
                trajectory.event("turn_end", turn=turn, status="completed",
                                 payload={"has_tool_calls": False})
            if on_step is not None and content:
                on_step({"type": "assistant", "content": content})
            if store is not None:
                store.set_session_status(session_id, "done")
            return finish_run({"ok": True, "turns": turn, "messages": transcript,
                               "answer": content or "", "error": None,
                               "session_id": session_id, "usage": usage})

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
        assistant_event = None
        if trajectory is not None:
            assistant_event = trajectory.event(
                "assistant", turn=turn, status="tool_calls",
                payload={"content": content, "tool_calls": tool_calls})
        if on_step is not None and content:
            on_step({"type": "assistant", "content": content})

        for tc in tool_calls:
            tool_started = time.time()
            tool_event = None
            if trajectory is not None:
                tool_event = trajectory.event(
                    "tool_execution_start", turn=turn,
                    parent_event_id=assistant_event["event_id"] if assistant_event else None,
                    tool_call_id=tc.get("id"), tool_name=tc.get("name"),
                    status="started", payload={"arguments": _parse_args(tc.get("arguments"))},
                    started_at=tool_started)
            if on_step is not None:
                on_step({"type": "tool_call", "name": tc["name"],
                         "args": _parse_args(tc["arguments"])})
            if registry is None:
                # Preserve the two-argument seam used by existing callers/tests.
                    if allowed_names is None:
                        result = call_tool(tc["name"], tc["arguments"])
                    else:
                        result = call_tool(tc["name"], tc["arguments"],
                                           allowed_names=allowed_names)
            else:
                result = call_tool(tc["name"], tc["arguments"],
                                   registry=active_registry,
                                   allowed_names=allowed_names)
            if trajectory is not None:
                tool_finished = time.time()
                trajectory.event(
                    "tool_execution_end", turn=turn,
                    parent_event_id=tool_event["event_id"] if tool_event else None,
                    tool_call_id=tc.get("id"), tool_name=tc.get("name"),
                    status="succeeded" if result.get("ok") else "failed",
                    payload={"result": result}, started_at=tool_started,
                    finished_at=tool_finished,
                    duration_ms=max(0, int((tool_finished - tool_started) * 1000)))
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
        if trajectory is not None:
            trajectory.event("turn_end", turn=turn, status="completed",
                             payload={"has_tool_calls": True,
                                      "tool_call_count": len(tool_calls)})

    error = f"max_turns={max_turns} reached"
    if verbose:
        print(f"[loop] {error}")
    transcript.append({"role": "error", "error": error, "turn": max_turns})
    if on_step is not None:
        on_step({"type": "error", "error": error, "turn": max_turns})
    if store is not None:
        store.set_session_status(session_id, "error")
    if trajectory is not None:
        trajectory.event("error", turn=max_turns, status="failed",
                         payload={"error": error})
    return finish_run({"ok": False, "turns": max_turns, "messages": transcript,
                       "answer": None, "error": error,
                       "session_id": session_id, "usage": usage})
