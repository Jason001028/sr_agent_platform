"""Durable Agent run and trajectory event recording.

The chat transcript remains the model-replay source of truth.  This module is
an append-only observation layer for recovery, audit and later dataset export.
Payloads are bounded and lightly redacted before they reach SQLite so a large
tool result cannot turn one request into an unbounded database write.
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from collections.abc import Mapping
from typing import Any


DEFAULT_MAX_BYTES = 32_000
_SENSITIVE_KEYS = {
    "api_key", "apikey", "authorization", "cookie", "password", "secret",
    "token", "access_token", "refresh_token", "private_key",
}


def stable_hash(value: Any) -> str:
    """Return a stable sha256 for a JSON-compatible value."""
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True,
                     separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _redact(value: Any, *, key: str | None = None) -> Any:
    if key and key.lower() in _SENSITIVE_KEYS:
        return "[REDACTED]"
    if isinstance(value, Mapping):
        return {str(k): _redact(v, key=str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact(v) for v in value]
    if isinstance(value, str):
        return value
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)


def bounded_payload(value: Any, *, max_bytes: int = DEFAULT_MAX_BYTES) -> dict:
    """Serialize a redacted payload and retain a deterministic preview/hash."""
    safe = _redact(value)
    raw = json.dumps(safe, ensure_ascii=False, separators=(",", ":"),
                     default=str)
    encoded = raw.encode("utf-8")
    if len(encoded) <= max_bytes:
        return {"value": safe, "truncated": False,
                "bytes": len(encoded), "sha256": hashlib.sha256(encoded).hexdigest()}
    clipped = encoded[:max_bytes].decode("utf-8", errors="ignore")
    return {"value": clipped, "truncated": True, "bytes": len(encoded),
            "sha256": hashlib.sha256(encoded).hexdigest()}


class TrajectoryRecorder:
    """Small synchronous adapter around Store's append-only trajectory API."""

    def __init__(self, store, *, session_id: str, prompt: str | None = None,
                 machine_id: str | None = None, model: str | None = None,
                 system_prompt: str | None = None, tools: list | None = None,
                 max_payload_bytes: int = DEFAULT_MAX_BYTES):
        self.store = store
        self.session_id = session_id
        self.max_payload_bytes = max_payload_bytes
        self.store.interrupt_open_agent_runs(session_id)
        self.run_id = store.create_agent_run(
            session_id,
            machine_id=machine_id,
            model=model,
            prompt=(bounded_payload(prompt, max_bytes=max_payload_bytes)["value"]
                    if prompt is not None else None),
            system_prompt_hash=stable_hash(system_prompt or ""),
            tool_manifest_hash=stable_hash(tools or []),
        )
        self._last_event_id: str | None = None
        self._started = time.time()
        self.event("run_start", status="running", payload={
            "model": model, "machine_id": machine_id,
        })

    def event(self, event_type: str, *, turn: int | None = None,
              parent_event_id: str | None = None,
              tool_call_id: str | None = None, tool_name: str | None = None,
              status: str | None = None, payload: Any = None,
              started_at: float | None = None, finished_at: float | None = None,
              duration_ms: int | None = None) -> dict:
        event_id = uuid.uuid4().hex
        item = self.store.append_trajectory_event(
            run_id=self.run_id, session_id=self.session_id,
            event_id=event_id, event_type=event_type, turn=turn,
            parent_event_id=parent_event_id, tool_call_id=tool_call_id,
            tool_name=tool_name, status=status,
            payload=bounded_payload(payload, max_bytes=self.max_payload_bytes)
            if payload is not None else None,
            started_at=started_at, finished_at=finished_at,
            duration_ms=duration_ms,
        )
        self._last_event_id = event_id
        return item

    def finish(self, *, status: str, answer: str | None = None,
               error: str | None = None) -> dict:
        finished = time.time()
        self.event("run_end", status=status, payload={
            "answer": answer, "error": error,
        }, started_at=self._started, finished_at=finished,
                   duration_ms=max(0, int((finished - self._started) * 1000)))
        return self.store.finish_agent_run(
            self.run_id, status=status, answer=answer, error=error,
            finished_at=finished)
