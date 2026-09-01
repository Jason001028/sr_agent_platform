"""SQLite session store — the checkpoints+writes pattern (agent-orchestration-research.md §6.3).

Three tables behind one Store:

  sessions  — one row per agent conversation (thread); status + metadata.
  messages  — append-only wire-format message log (the checkpoint). Each append
              is an autocommit transaction, so a message is durable before the
              next step runs — "checkpoint before side effect" (deepseek-harness
              checkpoint-policy). A resumed loop reconstructs the exact OpenAI
              conversation from this log (agent/loop.py).
  sr_tasks  — idempotency table for external Slurm side effects (§5.3): run_sr
              records its intent here before sbatch and its job_id after, so a
              crashed-loop replay looks the job up in squeue/sacct instead of
              submitting a duplicate.

Shape borrowed from langgraph checkpoint-sqlite (state snapshot + pending
writes); stored payloads are the OpenAI wire-format messages themselves, not
framework state. No framework deps. The SQLite connection is opened lazily —
constructing a Store touches no disk.

DB path: SR_AGENT_DB env var, default "sr_agent.db" in the working directory.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid

DEFAULT_DB = "sr_agent.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id         TEXT PRIMARY KEY,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    status     TEXT NOT NULL DEFAULT 'active',
    metadata   TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS messages (
    seq        INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    role       TEXT NOT NULL,
    payload    TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_session
    ON messages(session_id, seq);
CREATE TABLE IF NOT EXISTS sr_tasks (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    fingerprint  TEXT NOT NULL,
    session_id   TEXT,
    job_id       INTEGER,
    status       TEXT NOT NULL,
    params       TEXT NOT NULL,
    config_xml   TEXT,
    batch_script TEXT,
    log_dir      TEXT,
    created_at   REAL NOT NULL,
    updated_at   REAL NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_sr_tasks_fingerprint
    ON sr_tasks(fingerprint);
"""


def default_db_path() -> str:
    """DB path from SR_AGENT_DB, else sr_agent.db in the working directory."""
    return os.environ.get("SR_AGENT_DB", DEFAULT_DB)


def default_store() -> "Store":
    """A Store at the configured path. Cheap to call: the connection is lazy,
    so building one in a tool wrapper never touches disk until a write happens.
    """
    return Store()


class Store:
    """SQLite session / message / task store (lazy connection)."""

    def __init__(self, path=None):
        self.path = str(path or default_db_path())
        self._conn: sqlite3.Connection | None = None

    def _db(self) -> sqlite3.Connection:
        if self._conn is None:
            parent = os.path.dirname(os.path.abspath(self.path))
            if parent:
                os.makedirs(parent, exist_ok=True)
            self._conn = sqlite3.connect(self.path)
            self._conn.executescript(_SCHEMA)
        return self._conn

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    # ---- sessions ---------------------------------------------------------

    def create_session(self, metadata: dict | None = None) -> str:
        """Insert a session row and return its id."""
        sid = uuid.uuid4().hex
        now = time.time()
        db = self._db()
        db.execute(
            "INSERT INTO sessions (id, created_at, updated_at, status, metadata) "
            "VALUES (?, ?, ?, 'active', ?)",
            (sid, now, now, json.dumps(metadata or {}, ensure_ascii=False)))
        db.commit()
        return sid

    def get_session(self, session_id: str) -> dict | None:
        row = self._db().execute(
            "SELECT id, created_at, updated_at, status, metadata "
            "FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if row is None:
            return None
        return {"session_id": row[0], "created_at": row[1],
                "updated_at": row[2], "status": row[3],
                "metadata": json.loads(row[4] or "{}")}

    def list_sessions(self, limit: int = 100) -> list[dict]:
        rows = self._db().execute(
            "SELECT id, created_at, updated_at, status FROM sessions "
            "ORDER BY updated_at DESC LIMIT ?", (limit,)).fetchall()
        return [{"session_id": r[0], "created_at": r[1],
                 "updated_at": r[2], "status": r[3]} for r in rows]

    def set_session_status(self, session_id: str, status: str) -> None:
        db = self._db()
        db.execute(
            "UPDATE sessions SET status = ?, updated_at = ? WHERE id = ?",
            (status, time.time(), session_id))
        db.commit()

    # ---- messages (append-only wire-format log) --------------------------

    def append_message(self, session_id: str, message: dict) -> int:
        """Persist one wire-format message; durable on return."""
        db = self._db()
        now = time.time()
        cur = db.execute(
            "INSERT INTO messages (session_id, role, payload, created_at) "
            "VALUES (?, ?, ?, ?)",
            (session_id, message.get("role", ""),
             json.dumps(message, ensure_ascii=False), now))
        db.execute("UPDATE sessions SET updated_at = ? WHERE id = ?",
                   (now, session_id))
        db.commit()
        return cur.lastrowid

    def get_messages(self, session_id: str) -> list[dict]:
        """All wire-format messages for a session, in append order."""
        rows = self._db().execute(
            "SELECT payload FROM messages WHERE session_id = ? ORDER BY seq",
            (session_id,)).fetchall()
        return [json.loads(r[0]) for r in rows]

    # ---- sr_tasks (idempotency for external Slurm jobs) ------------------

    def get_sr_task(self, fingerprint: str) -> dict | None:
        row = self._db().execute(
            "SELECT id, fingerprint, session_id, job_id, status, params, "
            "config_xml, batch_script, log_dir, created_at, updated_at "
            "FROM sr_tasks WHERE fingerprint = ?", (fingerprint,)).fetchone()
        if row is None:
            return None
        return {"task_id": row[0], "fingerprint": row[1], "session_id": row[2],
                "job_id": row[3], "status": row[4], "params": json.loads(row[5]),
                "config_xml": row[6], "batch_script": row[7], "log_dir": row[8],
                "created_at": row[9], "updated_at": row[10]}

    def put_sr_task(self, fingerprint: str, params: dict, *,
                    session_id: str | None = None, status: str = "submitted",
                    job_id: int | None = None, config_xml: str | None = None,
                    batch_script: str | None = None,
                    log_dir: str | None = None) -> dict:
        """Insert a task row, or update the existing row for this fingerprint.

        Used by run_sr to record the submit *intent* (job_id=None) before the
        sbatch side effect, then to record the resulting job_id. Returns the
        task row as read back.
        """
        now = time.time()
        db = self._db()
        existing = db.execute(
            "SELECT id FROM sr_tasks WHERE fingerprint = ?",
            (fingerprint,)).fetchone()
        if existing is None:
            db.execute(
                "INSERT INTO sr_tasks (fingerprint, session_id, job_id, status, "
                "params, config_xml, batch_script, log_dir, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (fingerprint, session_id, job_id, status,
                 json.dumps(params, ensure_ascii=False), config_xml,
                 batch_script, log_dir, now, now))
        else:
            db.execute(
                "UPDATE sr_tasks SET session_id = ?, job_id = ?, status = ?, "
                "params = ?, config_xml = ?, batch_script = ?, log_dir = ?, "
                "updated_at = ? WHERE id = ?",
                (session_id, job_id, status,
                 json.dumps(params, ensure_ascii=False), config_xml,
                 batch_script, log_dir, now, existing[0]))
        db.commit()
        return self.get_sr_task(fingerprint) or {"task_id": existing[0] if existing else None}

    def update_sr_task_job(self, fingerprint: str, *, job_id: int,
                           status: str, config_xml: str | None = None,
                           batch_script: str | None = None,
                           log_dir: str | None = None) -> None:
        """Record the result of a submit: the job_id the scheduler returned."""
        db = self._db()
        db.execute(
            "UPDATE sr_tasks SET job_id = ?, status = ?, config_xml = ?, "
            "batch_script = ?, log_dir = ?, updated_at = ? WHERE fingerprint = ?",
            (job_id, status, config_xml, batch_script, log_dir, time.time(),
             fingerprint))
        db.commit()
