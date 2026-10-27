"""Request-only observations built from the loop's existing counters and Goal row."""

from __future__ import annotations

import json
import re


RECENT_TOOL_LIMIT = 4
_POLICY = (
    "Backend runtime observation, not authorization. Model-call counts and remaining "
    "budget exclude this request; loop_turn/goal_round include the current turn/round. "
    "Recent tool statuses describe returns in this loop, not business acceptance."
)


def build_runtime_status(*, durable, turn, max_turns, model_calls, tool_calls,
                         elapsed_seconds, max_model_calls=None, max_tool_calls=None,
                         max_seconds=None, goal=None, recent_tools=()):
    """All call counts are consumed attempts *before* the upcoming model request.

    Only durable loops enforce the separate call/time limits. A supplied Goal
    must be a fresh, lease-checked row read before reserving this model request.
    No Goal tool/time/token limit exists, so none is inferred from checkpoints.
    """
    def count(used, limit):
        return used if limit is None else f"{used}/{limit}"

    status = {"execution_mode": "durable_goal" if durable else "chat"}
    if durable and goal is not None:
        status.update(
            goal_status=goal["status"],
            goal_phase=goal["phase"][:64],
            goal_round=count(goal["rounds_used"], goal["max_rounds"]),
            goal_future_rounds_remaining=max(0, goal["max_rounds"] - goal["rounds_used"]),
            goal_model_calls_remaining=max(0, goal["max_model_calls"] - goal["model_calls_used"]),
        )
    model_limit = (min(max_turns, max_model_calls)
                   if durable and max_model_calls is not None else None)
    elapsed = round(max(0, elapsed_seconds), 1)
    status.update(
        loop_turn=count(turn, max_turns),
        model_calls_this_round=count(model_calls, model_limit),
        tool_calls_this_round=count(tool_calls, max_tool_calls if durable else None),
        elapsed_seconds=(f"{elapsed:.1f}/{max_seconds:g}"
                         if durable and max_seconds is not None else elapsed),
    )
    # Names originate in model declarations, so keep only bounded identifiers.
    status["recent_tools"] = [
        {"name": re.sub(r"[^A-Za-z0-9_.-]", "_", name)[:64], "status": outcome}
        for name, outcome in list(recent_tools)[-RECENT_TOOL_LIMIT:]
    ]
    return status


def project_runtime_status(messages, status):
    """Copy the existing Viewer/result projection; never touch durable messages."""
    block = "\n\n" + _POLICY + "\n<runtime_status>\n" + "\n".join(
        f"{key}: {json.dumps(value, ensure_ascii=False)}" for key, value in status.items()
    ) + "\n</runtime_status>"
    projected = [dict(message) for message in messages]
    if projected and projected[0].get("role") == "system":
        projected[0]["content"] += block
    else:
        projected.insert(0, {"role": "system", "content": block.lstrip()})
    return projected
