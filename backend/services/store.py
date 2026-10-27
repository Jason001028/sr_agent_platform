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
              submitting a duplicate. 一行 = 一个**指纹**（不是一次运行），所以行上
              有两组时间戳，别混：created_at/updated_at 属于**行**（队列排序、每次
              写回），started_at/finished_at 属于**本次运行**（首次看到 RUNNING →
              终态；队列页「耗时」的唯一来源，2026-09-18 增）。
              preview_state/preview_note 是**产物预览急生成**的进度（2026-09-20 增）：
              NULL → running → done/skipped/failed，同样属于「本次运行」，重交即归零。

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
import threading
from contextlib import contextmanager, nullcontext
from functools import wraps

from backend.services.goal_lease import current_lease, LeaseLost


class _Transaction:
    """Legacy Store methods may commit; the outer fenced write owns commit."""
    def __init__(self, connection):
        self.connection = connection

    def execute(self, sql, *args):
        if sql.strip().upper().startswith("BEGIN"):
            return self.connection.execute("SELECT 1")
        return self.connection.execute(sql, *args)

    def commit(self):
        pass

    def rollback(self):
        pass

    def __getattr__(self, name):
        return getattr(self.connection, name)


def _fenced_write(method):
    @wraps(method)
    def write(self, *args, **kwargs):
        lease = current_lease.get()
        if lease is None:
            return method(self, *args, **kwargs)
        with lease.lock, self.worker_transaction():
            return method(self, *args, **kwargs)
    return write

DEFAULT_DB = "sr_agent.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tool_result_refs (
    ref_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    execution_key TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    raw_bytes INTEGER NOT NULL,
    body BLOB,
    intent_id TEXT,
    expires_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS tool_result_refs_session ON tool_result_refs(session_id);

CREATE TABLE IF NOT EXISTS scene_library (
    source_path TEXT PRIMARY KEY,
    tags TEXT NOT NULL,
    updated_at REAL NOT NULL
);

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
    updated_at   REAL NOT NULL,
    started_at   REAL,
    finished_at  REAL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_sr_tasks_fingerprint
    ON sr_tasks(fingerprint);
CREATE TABLE IF NOT EXISTS machines (
    id         TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    status     TEXT NOT NULL DEFAULT 'offline',
    metadata   TEXT NOT NULL DEFAULT '{}',
    last_seen  REAL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS plugins (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    version     TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    entrypoint  TEXT NOT NULL DEFAULT '',
    metadata    TEXT NOT NULL DEFAULT '{}',
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS machine_plugins (
    machine_id    TEXT NOT NULL,
    plugin_id     TEXT NOT NULL,
    desired_state TEXT NOT NULL DEFAULT 'disabled',
    actual_state  TEXT NOT NULL DEFAULT 'unknown',
    version       TEXT,
    last_error    TEXT,
    updated_at    REAL NOT NULL,
    PRIMARY KEY (machine_id, plugin_id)
);
CREATE TABLE IF NOT EXISTS plugin_operations (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    machine_id    TEXT NOT NULL,
    plugin_id     TEXT NOT NULL,
    action        TEXT NOT NULL,
    target_state  TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'PENDING',
    version       TEXT,
    request_id    TEXT,
    result        TEXT NOT NULL DEFAULT '{}',
    error         TEXT,
    created_at    REAL NOT NULL,
    updated_at    REAL NOT NULL,
    finished_at   REAL
);
CREATE INDEX IF NOT EXISTS idx_plugin_ops_machine_status
    ON plugin_operations(machine_id, status, created_at);
CREATE TABLE IF NOT EXISTS llm_usage (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id        TEXT NOT NULL,
    turn              INTEGER NOT NULL,
    call_index        INTEGER NOT NULL,
    prompt_tokens     INTEGER NOT NULL,
    completion_tokens INTEGER NOT NULL,
    total_tokens      INTEGER NOT NULL,
    source            TEXT NOT NULL DEFAULT 'provider',
    created_at        REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_llm_usage_session
    ON llm_usage(session_id, created_at, id);
CREATE TABLE IF NOT EXISTS agent_runs (
    run_id              TEXT PRIMARY KEY,
    session_id          TEXT NOT NULL,
    machine_id          TEXT,
    status              TEXT NOT NULL DEFAULT 'running',
    model               TEXT,
    prompt              TEXT,
    system_prompt_hash  TEXT,
    tool_manifest_hash  TEXT,
    answer              TEXT,
    error               TEXT,
    started_at          REAL NOT NULL,
    finished_at         REAL,
    metadata            TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_agent_runs_session
    ON agent_runs(session_id, started_at, run_id);
CREATE TABLE IF NOT EXISTS trajectory_events (
    seq               INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id          TEXT NOT NULL UNIQUE,
    run_id            TEXT NOT NULL,
    session_id        TEXT NOT NULL,
    turn              INTEGER,
    event_type        TEXT NOT NULL,
    parent_event_id   TEXT,
    tool_call_id      TEXT,
    tool_name         TEXT,
    status            TEXT,
    started_at        REAL,
    finished_at       REAL,
    duration_ms       INTEGER,
    payload           TEXT,
    created_at        REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_trajectory_events_run
    ON trajectory_events(run_id, seq);
CREATE INDEX IF NOT EXISTS idx_trajectory_events_session
    ON trajectory_events(session_id, seq);
CREATE TABLE IF NOT EXISTS trajectory_feedback (
    feedback_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id    TEXT NOT NULL,
    run_id        TEXT NOT NULL,
    rating        TEXT NOT NULL CHECK (rating IN ('positive', 'negative')),
    reason        TEXT,
    created_at    REAL NOT NULL,
    UNIQUE(session_id, run_id)
);
CREATE INDEX IF NOT EXISTS idx_trajectory_feedback_session
    ON trajectory_feedback(session_id, created_at, feedback_id);
CREATE TABLE IF NOT EXISTS agent_goals (
    goal_id TEXT PRIMARY KEY,
    session_id TEXT,
    request_key TEXT UNIQUE,
    title TEXT NOT NULL,
    status TEXT NOT NULL,
    phase TEXT NOT NULL DEFAULT 'queued',
    checkpoint TEXT NOT NULL DEFAULT '{}',
    version INTEGER NOT NULL DEFAULT 0,
    lease_owner TEXT,
    lease_until REAL,
    next_wake_at REAL,
    max_rounds INTEGER NOT NULL DEFAULT 20,
    rounds_used INTEGER NOT NULL DEFAULT 0,
    max_model_calls INTEGER NOT NULL DEFAULT 100,
    model_calls_used INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    finished_at REAL
);
CREATE INDEX IF NOT EXISTS idx_agent_goals_schedule
    ON agent_goals(status, next_wake_at, updated_at);
CREATE TABLE IF NOT EXISTS agent_goal_steps (
    step_id TEXT PRIMARY KEY,
    goal_id TEXT NOT NULL,
    ordinal INTEGER NOT NULL,
    name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'PENDING',
    tool_name TEXT,
    external_job_id INTEGER,
    verification TEXT NOT NULL DEFAULT '{}',
    error TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    UNIQUE(goal_id, ordinal)
);
CREATE TABLE IF NOT EXISTS agent_tool_intents (
    intent_id TEXT PRIMARY KEY,
    goal_id TEXT NOT NULL,
    step_id TEXT,
    tool_name TEXT NOT NULL,
    args TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    side_effect_class TEXT NOT NULL,
    replay_policy TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'INTENDED',
    external_job_id INTEGER,
    result TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    UNIQUE(goal_id, idempotency_key)
);
CREATE INDEX IF NOT EXISTS idx_agent_intents_reconcile
    ON agent_tool_intents(status, goal_id);
CREATE TABLE IF NOT EXISTS agent_goal_events (
    goal_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    run_id TEXT,
    step_id TEXT,
    payload TEXT NOT NULL DEFAULT '{}',
    created_at REAL NOT NULL,
    PRIMARY KEY(goal_id, seq)
);
"""

#: sr_tasks 里发布后新增的列。`CREATE TABLE IF NOT EXISTS` 对**已存在**的表一个字
#: 都不改，所以升级前建的库（生产上就有）只能靠 ALTER TABLE 补 —— 见 _ensure_columns。
#:
#: preview_state / preview_note（2026-09-20）：产物预览的服务端急生成状态。见
#: `list_preview_candidates` 那一段。补出来的列在老行里是 NULL，**这正是想要的**
#: —— NULL 的含义是「没生成过」，急生成循环只认这个值，所以升级当天的历史 COMPLETED
#: 行会被认领；挡它的是**年龄窗口**（`finished_at` 超出窗口就不生成），不是回填。
_SR_TASK_ADDED_COLUMNS = (("started_at", "REAL"), ("finished_at", "REAL"),
                          ("preview_state", "TEXT"), ("preview_note", "TEXT"))

#: sessions 里发布后新增的列。pinned（2026-10-19）：会话侧栏置顶。
#: 老行补出来是 0（未置顶），列表顺序与升级前一致。
_SESSION_ADDED_COLUMNS = (("pinned", "INTEGER NOT NULL DEFAULT 0"),)


def _add_missing_columns(db: sqlite3.Connection, table: str, columns) -> bool:
    have = {row[1] for row in db.execute(f"PRAGMA table_info({table})")}
    added = False
    for name, decl in columns:
        if name not in have:
            db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
            added = True
    return added


def _ensure_columns(db: sqlite3.Connection) -> None:
    """Add post-release columns to an existing DB.

    幂等：先问 PRAGMA 有什么，缺哪列补哪列。补出来的列在老行里是 NULL，**不回填**
    —— 那些行的 created_at 语义已被「复用行」污染（同一行被重交过，created_at 还是
    第一次提交的时刻），回填等于把一个错数固化进历史。老行的耗时显示「—」。
    sessions.pinned 是新列，DEFAULT 0 让老会话保持未置顶。
    """
    db.execute("BEGIN IMMEDIATE")
    try:
        _add_missing_columns(db, "sr_tasks", _SR_TASK_ADDED_COLUMNS)
        _add_missing_columns(db, "sessions", _SESSION_ADDED_COLUMNS)
        _add_missing_columns(db, "agent_goals", (("lease_token", "TEXT"),))
        db.commit()
    except Exception:
        db.rollback()
        raise


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
        self._local = threading.local()
        self._connections = set()
        self._connections_lock = threading.Lock()
        self._generation = 0

    @property
    def _conn(self):
        if getattr(self._local, "generation", None) != self._generation:
            return None
        return getattr(self._local, "conn", None)

    @_conn.setter
    def _conn(self, value):
        self._local.conn = value
        self._local.generation = self._generation
        if value is not None:
            with self._connections_lock:
                self._connections.add(value)

    def _db(self) -> sqlite3.Connection:
        if getattr(self._local, "transaction", None) is not None:
            return self._local.transaction
        if self._conn is None:
            parent = os.path.dirname(os.path.abspath(self.path))
            if parent:
                os.makedirs(parent, exist_ok=True)
            # One connection per thread: chat, REST and Goal workers must not
            # interleave BEGIN/COMMIT on the same connection. SQLite serializes
            # independent connections through BEGIN IMMEDIATE + busy timeout.
            self._conn = sqlite3.connect(self.path, timeout=10,
                                         check_same_thread=False)
            self._conn.executescript(_SCHEMA)
            _ensure_columns(self._conn)
        return self._conn

    @contextmanager
    def worker_transaction(self):
        """Fence the complete write, including nested legacy commit calls."""
        if getattr(self._local, "transaction", None) is not None:
            yield self._db()
            return
        lease = current_lease.get()
        with lease.lock if lease else nullcontext():
            db = self._db()
            db.execute("BEGIN IMMEDIATE")
            self._local.transaction = _Transaction(db)
            original_version = lease.version if lease else None
            try:
                deadline = None
                if lease is not None:
                    deadline = lease.check(self)["lease_until"]
                yield self._local.transaction
                # A long transaction can outlive the lease even while SQLite
                # prevents takeover. Releasing our token inside this transaction
                # is intentional, so check its admitted deadline at commit.
                if lease is not None and (lease.stopped.is_set() or time.time() >= deadline):
                    raise LeaseLost("goal lease expired before transaction commit")
                db.commit()
            except BaseException:
                db.rollback()
                if lease is not None:
                    lease.version = original_version
                raise
            finally:
                self._local.transaction = None

    def close_thread(self) -> None:
        """Release only this thread; a Goal round must not close HTTP handles."""
        if self._conn is not None:
            with self._connections_lock:
                self._connections.discard(self._conn)
            self._conn.close()
            self._conn = None

    def close(self) -> None:
        """Application/test teardown closes all per-thread connections."""
        with self._connections_lock:
            self._generation += 1
            for connection in self._connections:
                connection.close()
            self._connections.clear()
        self._conn = None

    # ---- sessions ---------------------------------------------------------

    def archive_tool_result(self, session_id, execution_key, tool_name, result, budget):
        """Bounded archive; durable results reference the existing intent receipt."""
        import hashlib
        import zlib
        from backend.services.tool_result_formatter import encode
        raw = encode(result).encode("utf-8")
        digest = hashlib.sha256(raw).hexdigest()
        ref_id = hashlib.sha256((session_id + execution_key + digest).encode()).hexdigest()[:32]
        with self.worker_transaction() as db:
            db.execute("DELETE FROM tool_result_refs WHERE expires_at < ? AND session_id NOT IN "
                       "(SELECT session_id FROM agent_goals WHERE session_id IS NOT NULL "
                       "AND status NOT IN ('COMPLETED','FAILED','CANCELLED','BLOCKED'))", (time.time(),))
            if db.execute("SELECT 1 FROM tool_result_refs WHERE ref_id=? AND session_id=?", (ref_id,session_id)).fetchone():
                return {"available": True, "id": ref_id}
            receipt = db.execute("SELECT i.intent_id, i.result FROM agent_tool_intents i "
                                 "JOIN agent_goals g ON g.goal_id=i.goal_id "
                                 "WHERE g.session_id=? AND i.idempotency_key=? AND i.external_job_id IS NULL "
                                 "AND i.status IN ('SUCCEEDED','FAILED')", (session_id, execution_key)).fetchone()
            intent_id = receipt[0] if receipt and receipt[1] and json.loads(receipt[1]) == result else None
            if not intent_id:
                used = db.execute("SELECT COALESCE(SUM(raw_bytes),0) FROM tool_result_refs WHERE body IS NOT NULL").fetchone()[0]
                if len(raw) > budget.archive_bytes or used + len(raw) > budget.archive_total_bytes:
                    return {"available": False, "reason": "archive_size_limit"}
            db.execute("INSERT INTO tool_result_refs VALUES (?,?,?,?,?,?,?,?,?)",
                       (ref_id, session_id, execution_key, tool_name, digest, len(raw),
                        None if intent_id else zlib.compress(raw), intent_id,
                        time.time() + budget.retention_days * 86400))
        return {"available": True, "id": ref_id}

    def read_tool_result(self, session_id, ref_id):
        import hashlib
        import zlib
        from backend.services.tool_result_formatter import encode
        db = self._db()
        row = db.execute("SELECT body,intent_id,sha256,expires_at FROM tool_result_refs "
                         "WHERE ref_id=? AND session_id=?", (ref_id,session_id)).fetchone()
        if not row:
            return None
        pinned = db.execute("SELECT 1 FROM agent_goals WHERE session_id=? "
                            "AND status NOT IN ('COMPLETED','FAILED','CANCELLED','BLOCKED')", (session_id,)).fetchone()
        if row[3] < time.time() and not pinned:
            return None
        if row[1]:
            receipt = db.execute("SELECT result FROM agent_tool_intents WHERE intent_id=?", (row[1],)).fetchone()
            if not receipt or not receipt[0]:
                return None
            value = json.loads(receipt[0])
            raw = encode(value).encode("utf-8")
        else:
            raw = zlib.decompress(row[0])
            value = json.loads(raw)
        return value if hashlib.sha256(raw).hexdigest() == row[2] else None

    def add_library_entries(self, entries: list[dict]) -> None:
        """Atomically merge batch tags; canonical source paths prevent duplicates."""
        with self.worker_transaction() as db:
            for entry in entries:
                old = db.execute("SELECT tags FROM scene_library WHERE source_path = ?",
                                 (entry["source_path"],)).fetchone()
                tags = json.loads(old[0]) if old else {}
                for category, values in entry["tags"].items():
                    tags[category] = list(dict.fromkeys(tags.get(category, []) + values))
                db.execute("INSERT INTO scene_library VALUES (?, ?, ?) "
                           "ON CONFLICT(source_path) DO UPDATE SET tags=excluded.tags, updated_at=excluded.updated_at",
                           (entry["source_path"], json.dumps(tags, ensure_ascii=False), time.time()))

    def list_library_entries(self) -> list[dict]:
        return [{"source_path": r[0], "tags": json.loads(r[1])} for r in
                self._db().execute("SELECT source_path, tags FROM scene_library ORDER BY updated_at DESC").fetchall()]

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
            "SELECT id, created_at, updated_at, status, metadata, pinned "
            "FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if row is None:
            return None
        return {"session_id": row[0], "created_at": row[1],
                "updated_at": row[2], "status": row[3],
                "metadata": json.loads(row[4] or "{}"),
                "pinned": bool(row[5])}

    def list_sessions(self, limit: int = 100) -> list[dict]:
        """会话列表：置顶在前，其余按最近活动倒序。"""
        rows = self._db().execute(
            "SELECT id, created_at, updated_at, status, pinned FROM sessions "
            "ORDER BY pinned DESC, updated_at DESC LIMIT ?", (limit,)).fetchall()
        return [{"session_id": r[0], "created_at": r[1],
                 "updated_at": r[2], "status": r[3],
                 "pinned": bool(r[4])} for r in rows]

    def set_session_status(self, session_id: str, status: str) -> None:
        db = self._db()
        db.execute(
            "UPDATE sessions SET status = ?, updated_at = ? WHERE id = ?",
            (status, time.time(), session_id))
        db.commit()

    def set_session_pinned(self, session_id: str, pinned: bool) -> dict | None:
        """置顶/取消置顶；会话不存在返回 None。不动 updated_at（活动时间）。"""
        db = self._db()
        cur = db.execute("UPDATE sessions SET pinned = ? WHERE id = ?",
                         (1 if pinned else 0, session_id))
        db.commit()
        return self.get_session(session_id) if cur.rowcount else None

    def delete_session(self, session_id: str) -> bool:
        """删除会话及其对话/轨迹数据；返回是否确实删到一行。

        队列任务（sr_tasks）是外部副作用的操作记录，不随会话删除；它们的
        session_id 只作溯源线索，指向已删会话时保持原值。
        """
        db = self._db()
        cur = db.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        if cur.rowcount == 0:
            db.commit()
            return False
        for table in ("messages", "llm_usage", "agent_runs",
                      "trajectory_events", "trajectory_feedback", "tool_result_refs"):
            db.execute(f"DELETE FROM {table} WHERE session_id = ?", (session_id,))
        db.commit()
        return True

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

    # ---- LLM token usage -------------------------------------------------

    def append_llm_usage(self, session_id: str, *, turn: int,
                         call_index: int, prompt_tokens: int,
                         completion_tokens: int, total_tokens: int,
                         source: str = "provider") -> dict:
        now = time.time()
        cur = self._db().execute(
            "INSERT INTO llm_usage (session_id, turn, call_index, prompt_tokens, "
            "completion_tokens, total_tokens, source, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (session_id, int(turn), int(call_index), int(prompt_tokens),
             int(completion_tokens), int(total_tokens), str(source), now),
        )
        self._db().commit()
        return {"usage_id": int(cur.lastrowid), "session_id": session_id,
                "turn": int(turn), "call_index": int(call_index),
                "prompt_tokens": int(prompt_tokens),
                "completion_tokens": int(completion_tokens),
                "total_tokens": int(total_tokens), "source": str(source),
                "created_at": now}

    def list_llm_usage(self, session_id: str) -> list[dict]:
        rows = self._db().execute(
            "SELECT id, session_id, turn, call_index, prompt_tokens, "
            "completion_tokens, total_tokens, source, created_at "
            "FROM llm_usage WHERE session_id = ? ORDER BY created_at, id",
            (session_id,)).fetchall()
        return [{"usage_id": r[0], "session_id": r[1], "turn": r[2],
                 "call_index": r[3], "prompt_tokens": r[4],
                 "completion_tokens": r[5], "total_tokens": r[6],
                 "source": r[7], "created_at": r[8]} for r in rows]

    def summarize_llm_usage(self, session_id: str) -> dict:
        events = self.list_llm_usage(session_id)
        prompt = sum(e["prompt_tokens"] for e in events)
        completion = sum(e["completion_tokens"] for e in events)
        total = sum(e["total_tokens"] for e in events)
        return {"prompt_tokens": prompt, "completion_tokens": completion,
                "total_tokens": total, "calls": len(events),
                "source": (events[-1]["source"] if events else "unavailable"),
                "events": events}

    # ---- Agent trajectory -----------------------------------------------

    def create_agent_run(self, session_id: str, *, machine_id: str | None = None,
                         model: str | None = None, prompt: str | None = None,
                         system_prompt_hash: str | None = None,
                         tool_manifest_hash: str | None = None,
                         metadata: dict | None = None) -> str:
        run_id = uuid.uuid4().hex
        now = time.time()
        self._db().execute(
            "INSERT INTO agent_runs (run_id, session_id, machine_id, status, model, "
            "prompt, system_prompt_hash, tool_manifest_hash, started_at, metadata) "
            "VALUES (?, ?, ?, 'running', ?, ?, ?, ?, ?, ?)",
            (run_id, session_id, machine_id, model, prompt,
             system_prompt_hash, tool_manifest_hash, now,
             json.dumps(metadata or {}, ensure_ascii=False)),
        )
        self._db().commit()
        return run_id

    def interrupt_open_agent_runs(self, session_id: str) -> list[str]:
        """Close runs left RUNNING by a crashed process as interrupted."""
        db = self._db()
        rows = db.execute(
            "SELECT run_id FROM agent_runs WHERE session_id = ? AND status = 'running'",
            (session_id,),
        ).fetchall()
        if not rows:
            return []
        now = time.time()
        message = "run interrupted before completion; outcome may be incomplete"
        ids = [row[0] for row in rows]
        db.executemany(
            "UPDATE agent_runs SET status = 'interrupted', error = ?, finished_at = ? "
            "WHERE run_id = ?",
            [(message, now, run_id) for run_id in ids],
        )
        db.commit()
        for run_id in ids:
            self.append_trajectory_event(
                run_id=run_id, session_id=session_id,
                event_id=uuid.uuid4().hex, event_type="run_end",
                status="interrupted", payload={"error": message},
                finished_at=now,
            )
        return ids

    def finish_agent_run(self, run_id: str, *, status: str,
                         answer: str | None = None, error: str | None = None,
                         finished_at: float | None = None) -> dict | None:
        finished = float(finished_at or time.time())
        db = self._db()
        db.execute(
            "UPDATE agent_runs SET status = ?, answer = ?, error = ?, finished_at = ? "
            "WHERE run_id = ?",
            (str(status), answer, error, finished, run_id),
        )
        db.commit()
        return self.get_agent_run(run_id)

    def get_agent_run(self, run_id: str) -> dict | None:
        row = self._db().execute(
            "SELECT run_id, session_id, machine_id, status, model, prompt, "
            "system_prompt_hash, tool_manifest_hash, answer, error, started_at, "
            "finished_at, metadata FROM agent_runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        if row is None:
            return None
        return {"run_id": row[0], "session_id": row[1], "machine_id": row[2],
                "status": row[3], "model": row[4], "prompt": row[5],
                "system_prompt_hash": row[6], "tool_manifest_hash": row[7],
                "answer": row[8], "error": row[9], "started_at": row[10],
                "finished_at": row[11], "metadata": json.loads(row[12] or "{}")}

    def list_agent_runs(self, session_id: str, *, limit: int = 50,
                        offset: int = 0) -> list[dict]:
        rows = self._db().execute(
            "SELECT run_id FROM agent_runs WHERE session_id = ? "
            "ORDER BY started_at, run_id LIMIT ? OFFSET ?",
            (session_id, int(limit), int(offset)),
        ).fetchall()
        return [self.get_agent_run(row[0]) for row in rows]

    def append_trajectory_event(self, *, run_id: str, session_id: str,
                                event_id: str, event_type: str,
                                turn: int | None = None,
                                parent_event_id: str | None = None,
                                tool_call_id: str | None = None,
                                tool_name: str | None = None,
                                status: str | None = None,
                                payload: dict | None = None,
                                started_at: float | None = None,
                                finished_at: float | None = None,
                                duration_ms: int | None = None) -> dict:
        now = time.time()
        cur = self._db().execute(
            "INSERT INTO trajectory_events (event_id, run_id, session_id, turn, "
            "event_type, parent_event_id, tool_call_id, tool_name, status, "
            "started_at, finished_at, duration_ms, payload, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (event_id, run_id, session_id, turn, event_type, parent_event_id,
             tool_call_id, tool_name, status,
             started_at, finished_at, duration_ms,
             json.dumps(payload, ensure_ascii=False) if payload is not None else None,
             now),
        )
        self._db().commit()
        return {"seq": int(cur.lastrowid), "event_id": event_id, "run_id": run_id,
                "session_id": session_id, "turn": turn, "event_type": event_type,
                "parent_event_id": parent_event_id, "tool_call_id": tool_call_id,
                "tool_name": tool_name, "status": status,
                "started_at": started_at, "finished_at": finished_at,
                "duration_ms": duration_ms, "payload": payload, "created_at": now}

    def list_trajectory_events(self, session_id: str, *, run_id: str | None = None,
                               limit: int = 500, offset: int = 0) -> list[dict]:
        where = "session_id = ?"
        params: list = [session_id]
        if run_id is not None:
            where += " AND run_id = ?"
            params.append(run_id)
        params.extend([int(limit), int(offset)])
        rows = self._db().execute(
            "SELECT seq, event_id, run_id, session_id, turn, event_type, "
            "parent_event_id, tool_call_id, tool_name, status, started_at, "
            "finished_at, duration_ms, payload, created_at "
            f"FROM trajectory_events WHERE {where} ORDER BY seq LIMIT ? OFFSET ?",
            tuple(params),
        ).fetchall()
        out = []
        for row in rows:
            out.append({"seq": row[0], "event_id": row[1], "run_id": row[2],
                        "session_id": row[3], "turn": row[4], "event_type": row[5],
                        "parent_event_id": row[6], "tool_call_id": row[7],
                        "tool_name": row[8], "status": row[9],
                        "started_at": row[10], "finished_at": row[11],
                        "duration_ms": row[12],
                        "payload": json.loads(row[13]) if row[13] else None,
                        "created_at": row[14]})
        return out

    def add_trajectory_feedback(self, session_id: str, run_id: str, *,
                                rating: str, reason: str | None = None) -> dict:
        if rating not in {"positive", "negative"}:
            raise ValueError("rating must be positive or negative")
        run = self.get_agent_run(run_id)
        if run is None:
            raise ValueError("run not found")
        if run["session_id"] != session_id:
            raise ValueError("run does not belong to session")
        now = time.time()
        try:
            cur = self._db().execute(
                "INSERT INTO trajectory_feedback "
                "(session_id, run_id, rating, reason, created_at) VALUES (?, ?, ?, ?, ?)",
                (session_id, run_id, rating, reason, now),
            )
            self._db().commit()
        except sqlite3.IntegrityError as exc:
            raise ValueError("feedback already submitted for this run") from exc
        return {"feedback_id": int(cur.lastrowid), "session_id": session_id,
                "run_id": run_id, "rating": rating, "reason": reason,
                "created_at": now}

    def list_trajectory_feedback(self, session_id: str) -> list[dict]:
        rows = self._db().execute(
            "SELECT feedback_id, session_id, run_id, rating, reason, created_at "
            "FROM trajectory_feedback WHERE session_id = ? "
            "ORDER BY created_at, feedback_id", (session_id,)
        ).fetchall()
        return [{"feedback_id": r[0], "session_id": r[1], "run_id": r[2],
                 "rating": r[3], "reason": r[4], "created_at": r[5]}
                for r in rows]

    # ---- machines / plugins ---------------------------------------------

    def upsert_machine(self, machine_id: str, *, name: str | None = None,
                       status: str = "online", metadata: dict | None = None,
                       touch: bool = True) -> dict:
        """Create or update a machine registration."""
        machine_id = str(machine_id).strip()
        if not machine_id:
            raise ValueError("machine_id is required")
        now = time.time()
        db = self._db()
        old = db.execute("SELECT name, metadata FROM machines WHERE id = ?",
                         (machine_id,)).fetchone()
        machine_name = str(name or (old[0] if old else machine_id))
        meta = metadata if metadata is not None else (
            json.loads(old[1]) if old else {})
        last_seen = now if touch else None
        db.execute(
            "INSERT INTO machines (id, name, status, metadata, last_seen, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET name=excluded.name, status=excluded.status, "
            "metadata=excluded.metadata, last_seen=COALESCE(excluded.last_seen, machines.last_seen), "
            "updated_at=excluded.updated_at",
            (machine_id, machine_name, status, json.dumps(meta, ensure_ascii=False),
             last_seen, now, now),
        )
        db.commit()
        return self.get_machine(machine_id)  # type: ignore[return-value]

    def get_machine(self, machine_id: str) -> dict | None:
        row = self._db().execute(
            "SELECT id, name, status, metadata, last_seen, created_at, updated_at "
            "FROM machines WHERE id = ?", (machine_id,)).fetchone()
        if row is None:
            return None
        return {"machine_id": row[0], "name": row[1], "status": row[2],
                "metadata": json.loads(row[3] or "{}"), "last_seen": row[4],
                "created_at": row[5], "updated_at": row[6]}

    def list_machines(self) -> list[dict]:
        rows = self._db().execute(
            "SELECT id, name, status, metadata, last_seen, created_at, updated_at "
            "FROM machines ORDER BY name, id").fetchall()
        return [{"machine_id": r[0], "name": r[1], "status": r[2],
                 "metadata": json.loads(r[3] or "{}"), "last_seen": r[4],
                 "created_at": r[5], "updated_at": r[6]} for r in rows]

    def upsert_plugin(self, plugin_id: str, *, name: str, version: str,
                      description: str = "", entrypoint: str = "",
                      metadata: dict | None = None) -> dict:
        now = time.time()
        db = self._db()
        db.execute(
            "INSERT INTO plugins (id, name, version, description, entrypoint, metadata, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET name=excluded.name, version=excluded.version, "
            "description=excluded.description, entrypoint=excluded.entrypoint, "
            "metadata=excluded.metadata, updated_at=excluded.updated_at",
            (plugin_id, name, version, description, entrypoint,
             json.dumps(metadata or {}, ensure_ascii=False), now, now),
        )
        db.commit()
        return self.get_plugin(plugin_id)  # type: ignore[return-value]

    def get_plugin(self, plugin_id: str) -> dict | None:
        row = self._db().execute(
            "SELECT id, name, version, description, entrypoint, metadata, created_at, updated_at "
            "FROM plugins WHERE id = ?", (plugin_id,)).fetchone()
        if row is None:
            return None
        return {"plugin_id": row[0], "name": row[1], "version": row[2],
                "description": row[3], "entrypoint": row[4],
                "metadata": json.loads(row[5] or "{}"),
                "created_at": row[6], "updated_at": row[7]}

    def list_plugins(self) -> list[dict]:
        rows = self._db().execute(
            "SELECT id, name, version, description, entrypoint, metadata, created_at, updated_at "
            "FROM plugins ORDER BY name, id").fetchall()
        return [{"plugin_id": r[0], "name": r[1], "version": r[2],
                 "description": r[3], "entrypoint": r[4],
                 "metadata": json.loads(r[5] or "{}"),
                 "created_at": r[6], "updated_at": r[7]} for r in rows]

    def set_machine_plugin(self, machine_id: str, plugin_id: str, *,
                           desired_state: str, actual_state: str | None = None,
                           version: str | None = None,
                           last_error: str | None = None) -> dict:
        if desired_state not in {"enabled", "disabled", "uninstalled"}:
            raise ValueError("invalid desired plugin state")
        now = time.time()
        db = self._db()
        if actual_state is None:
            db.execute(
                "INSERT INTO machine_plugins (machine_id, plugin_id, desired_state, actual_state, version, last_error, updated_at) "
                "VALUES (?, ?, ?, 'unknown', ?, ?, ?) "
                "ON CONFLICT(machine_id, plugin_id) DO UPDATE SET desired_state=excluded.desired_state, "
                "version=COALESCE(excluded.version, machine_plugins.version), "
                "last_error=excluded.last_error, updated_at=excluded.updated_at",
                (machine_id, plugin_id, desired_state, version, last_error, now),
            )
            db.commit()
            return self.get_machine_plugin(machine_id, plugin_id)  # type: ignore[return-value]
        db.execute(
            "INSERT INTO machine_plugins (machine_id, plugin_id, desired_state, actual_state, version, last_error, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(machine_id, plugin_id) DO UPDATE SET desired_state=excluded.desired_state, "
            "actual_state=excluded.actual_state, "
            "version=COALESCE(excluded.version, machine_plugins.version), "
            "last_error=excluded.last_error, updated_at=excluded.updated_at",
            (machine_id, plugin_id, desired_state, actual_state,
             version, last_error, now),
        )
        db.commit()
        return self.get_machine_plugin(machine_id, plugin_id)  # type: ignore[return-value]

    def get_machine_plugin(self, machine_id: str, plugin_id: str) -> dict | None:
        row = self._db().execute(
            "SELECT machine_id, plugin_id, desired_state, actual_state, version, last_error, updated_at "
            "FROM machine_plugins WHERE machine_id = ? AND plugin_id = ?",
            (machine_id, plugin_id)).fetchone()
        if row is None:
            return None
        return {"machine_id": row[0], "plugin_id": row[1],
                "desired_state": row[2], "actual_state": row[3],
                "version": row[4], "last_error": row[5], "updated_at": row[6]}

    def list_machine_plugins(self, machine_id: str | None = None) -> list[dict]:
        where = " WHERE machine_id = ?" if machine_id else ""
        args = (machine_id,) if machine_id else ()
        rows = self._db().execute(
            "SELECT machine_id, plugin_id, desired_state, actual_state, version, last_error, updated_at "
            f"FROM machine_plugins{where} ORDER BY machine_id, plugin_id", args).fetchall()
        return [{"machine_id": r[0], "plugin_id": r[1],
                 "desired_state": r[2], "actual_state": r[3],
                 "version": r[4], "last_error": r[5], "updated_at": r[6]}
                for r in rows]

    def create_plugin_operation(self, machine_id: str, plugin_id: str, *,
                                action: str, target_state: str,
                                version: str | None = None,
                                request_id: str | None = None) -> dict:
        now = time.time()
        cur = self._db().execute(
            "INSERT INTO plugin_operations (machine_id, plugin_id, action, target_state, status, version, request_id, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, 'PENDING', ?, ?, ?, ?)",
            (machine_id, plugin_id, action, target_state, version, request_id, now, now))
        self._db().commit()
        return self.get_plugin_operation(int(cur.lastrowid))  # type: ignore[return-value]

    def get_plugin_operation(self, operation_id: int) -> dict | None:
        row = self._db().execute(
            "SELECT id, machine_id, plugin_id, action, target_state, status, version, request_id, result, error, created_at, updated_at, finished_at "
            "FROM plugin_operations WHERE id = ?", (operation_id,)).fetchone()
        if row is None:
            return None
        return {"operation_id": row[0], "machine_id": row[1], "plugin_id": row[2],
                "action": row[3], "target_state": row[4], "status": row[5],
                "version": row[6], "request_id": row[7],
                "result": json.loads(row[8] or "{}"), "error": row[9],
                "created_at": row[10], "updated_at": row[11],
                "finished_at": row[12]}

    def list_plugin_operations(self, *, machine_id: str | None = None,
                               status: str | None = None,
                               limit: int = 100) -> list[dict]:
        clauses, args = [], []
        if machine_id:
            clauses.append("machine_id = ?"); args.append(machine_id)
        if status:
            clauses.append("status = ?"); args.append(status)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        args.append(int(limit))
        rows = self._db().execute(
            "SELECT id FROM plugin_operations" + where +
            " ORDER BY created_at DESC LIMIT ?", tuple(args)).fetchall()
        return [self.get_plugin_operation(int(r[0])) for r in rows]

    def claim_plugin_operations(self, machine_id: str, *,
                                limit: int = 20) -> list[dict]:
        """Atomically claim pending work for a machine Agent."""
        db = self._db()
        db.execute("BEGIN IMMEDIATE")
        try:
            rows = db.execute(
                "SELECT id FROM plugin_operations WHERE machine_id = ? AND status = 'PENDING' "
                "ORDER BY created_at LIMIT ?", (machine_id, int(limit))).fetchall()
            ids = [int(row[0]) for row in rows]
            now = time.time()
            for operation_id in ids:
                db.execute(
                    "UPDATE plugin_operations SET status='RUNNING', updated_at=? "
                    "WHERE id=? AND status='PENDING'", (now, operation_id))
            db.commit()
        except Exception:
            db.rollback()
            raise
        return [self.get_plugin_operation(operation_id) for operation_id in ids]

    def finish_plugin_operation(self, operation_id: int, *, status: str,
                                actual_state: str, result: dict | None = None,
                                error: str | None = None) -> dict | None:
        if status not in {"COMPLETED", "FAILED"}:
            raise ValueError("invalid plugin operation status")
        op = self.get_plugin_operation(operation_id)
        if op is None:
            return None
        now = time.time()
        db = self._db()
        db.execute(
            "UPDATE plugin_operations SET status=?, result=?, error=?, updated_at=?, finished_at=? WHERE id=?",
            (status, json.dumps(result or {}, ensure_ascii=False), error, now, now, operation_id))
        db.execute(
            "UPDATE machine_plugins SET actual_state=?, last_error=?, updated_at=? WHERE machine_id=? AND plugin_id=?",
            (actual_state, error, now, op["machine_id"], op["plugin_id"]))
        db.commit()
        return self.get_plugin_operation(operation_id)

    # ---- sr_tasks (idempotency for external Slurm jobs) ------------------

    _SR_TASK_COLS = ("id, fingerprint, session_id, job_id, status, params, "
                     "config_xml, batch_script, log_dir, created_at, updated_at, "
                     "started_at, finished_at, preview_state, preview_note")

    @staticmethod
    def _sr_task_row(row) -> dict:
        return {"task_id": row[0], "fingerprint": row[1], "session_id": row[2],
                "job_id": row[3], "status": row[4], "params": json.loads(row[5]),
                "config_xml": row[6], "batch_script": row[7], "log_dir": row[8],
                "created_at": row[9], "updated_at": row[10],
                "started_at": row[11], "finished_at": row[12],
                "preview_state": row[13], "preview_note": row[14]}

    def get_sr_task(self, fingerprint: str) -> dict | None:
        row = self._db().execute(
            f"SELECT {self._SR_TASK_COLS} "
            "FROM sr_tasks WHERE fingerprint = ?", (fingerprint,)).fetchone()
        return self._sr_task_row(row) if row is not None else None

    def get_sr_task_by_id(self, task_id: int) -> dict | None:
        """Look up a task by its primary key (queue REST endpoints)."""
        row = self._db().execute(
            f"SELECT {self._SR_TASK_COLS} "
            "FROM sr_tasks WHERE id = ?", (task_id,)).fetchone()
        return self._sr_task_row(row) if row is not None else None

    def list_sr_tasks(self, limit: int = 200) -> list[dict]:
        """All SR tasks (the shared queue), newest first."""
        rows = self._db().execute(
            f"SELECT {self._SR_TASK_COLS} "
            "FROM sr_tasks ORDER BY created_at DESC LIMIT ?",
            (limit,)).fetchall()
        return [self._sr_task_row(r) for r in rows]

    def set_sr_task_state(self, task_id: int, state: str, *,
                          mark_started: bool = False,
                          mark_finished: bool = False) -> dict:
        """Write back a queue display state (阶段5 校准器) + bump updated_at.

        The idempotency layer (submit_run_sr) never reads `status`, so this
        semantic upgrade is regression-free — see api-contract.md §3.3.

        `mark_started` / `mark_finished` 另外钉住**本次运行的时间窗**（队列页的
        「耗时」就是它两的差）：首次落库 RUNNING = 排队结束、真开始跑；落库
        COMPLETED/FAILED = 跑完。都是观测到的时刻，不是猜的 —— 没观测到就没有值。

        Returns the row **as read back after the write**, which is what callers must
        hand to clients: a caller that only forwards the new `state` leaves the
        client holding its own older snapshot of the row, and the elapsed column
        renders that snapshot's numbers (2026-09-17 的「0 秒」与 2026-09-18 的
        「几十小时」是同一个坑的两面：值要么缺席，要么是上一个快照的)。
        """
        db = self._db()
        now = time.time()
        sets = ["status = ?", "updated_at = ?"]
        vals: list = [state, now]
        if mark_started:
            sets.append("started_at = ?")
            vals.append(now)
        if mark_finished:
            sets.append("finished_at = ?")
            vals.append(now)
        vals.append(task_id)
        db.execute(f"UPDATE sr_tasks SET {', '.join(sets)} WHERE id = ?", vals)
        db.commit()
        row = self.get_sr_task_by_id(task_id)
        if row is None:      # 行在写回与读回之间消失（无删除 API，纯防御）
            return {"task_id": task_id, "status": state, "updated_at": now,
                    "started_at": now if mark_started else None,
                    "finished_at": now if mark_finished else None}
        return row

    # ---- 产物预览急生成（api/app.py::_eager_bake_tick 的队列） ---------------
    #
    # 为什么队列是**从库派生**的，而不是在状态转换点上入队：写终态的
    # `platform._task_state` 有**两个**调用者 —— 后台 `_poll_once` 与请求路径
    # `_task_view`（GET /api/queue）。谁先观测到 RUNNING→COMPLETED 谁把「变了」
    # 这个信号拿走，另一个看到的是「没变化」，挂在转换点上的入队钩子必然偶发漏生成。
    # 派生 + `claim_preview_bake` 的原子 CAS 之后，这个竞态在结构上不存在：
    # 急生成循环不关心「谁先看到」，它只关心「这行还没被认领」。
    #
    # preview_state 取值：NULL（没生成）→ running → done | skipped | failed | cleared。
    # 粒度是**行**（= 一个 task_fingerprint），所以同一 suffix 重跑必须重新武装，
    # 由 put_sr_task 的 UPDATE 分支负责（见那里的注释）。
    # `cleared` 是**人工**结局、不由预览生成流程写：场景库的「清除缓存」把文件删掉后
    # 标上它，让这行不再是候选（否则下一轮急生成会把文件重新生成回来，用户以为白清了）。
    # 写它的入口只有一个 —— `mark_preview_cleared`（CAS，见那里的注释）。

    #: 每轮最多看一眼多少行 COMPLETED 候选（不是每轮生成多少 —— 那恒为 1）。
    _PREVIEW_SCAN_LIMIT = 20

    def list_preview_candidates(self, *, max_age_sec: float,
                                limit: int | None = None) -> list[dict]:
        """等着做产物预览的 COMPLETED 行，最新跑完的在前。

        三条判据都是必须的：
          * `status='COMPLETED'` —— **FAILED 绝不生成**。`writeTiff` 先 rename 再写，
            失败的运行会在产物路径上留下半截文件，生成出来是坏图。
          * `preview_state IS NULL` —— 还没被认领过（认领即写 'running'）。
          * `finished_at >= ?` —— 年龄窗口。`finished_at IS NULL` 的行（加这两列之前
            建的、或整段运行期间 sr-api 不在场没观测到开始的）**一律排除**：那个值是
            NOT NULL 比较，NULL 天然不在窗口内，所以不用额外写条件，但这条判据是
            升级当天不把历史 COMPLETED 行全生成一遍的**唯一**屏障。
        """
        cutoff = time.time() - float(max_age_sec)
        rows = self._db().execute(
            f"SELECT {self._SR_TASK_COLS} FROM sr_tasks "
            "WHERE status = 'COMPLETED' AND preview_state IS NULL "
            "AND finished_at IS NOT NULL AND finished_at >= ? "
            "ORDER BY finished_at DESC LIMIT ?",
            (cutoff, int(limit or self._PREVIEW_SCAN_LIMIT))).fetchall()
        return [self._sr_task_row(r) for r in rows]

    def claim_preview_bake(self, task_id: int) -> dict | None:
        """认领一行的产物预览预览生成。抢到返回该行，被人抢在前面则 None。

        这是并发与重复认领的**唯一裁决点**：CAS 写 `preview_state='running'`，
        只有 `rowcount == 1` 才算抢到。`status='COMPLETED'` 一并写进 WHERE，是为了
        挡住「认领与重跑赛跑」—— 用户在这一行刚跑完、急生成还没动手时又交了同一个
        suffix，`put_sr_task` 会把 status 打回 submitted 并清 preview_state，
        此时这份认领必须失效（否则会去生成一个正在被重写的产物）。
        """
        db = self._db()
        cur = db.execute(
            "UPDATE sr_tasks SET preview_state = 'running', preview_note = NULL "
            "WHERE id = ? AND preview_state IS NULL AND status = 'COMPLETED'",
            (task_id,))
        db.commit()
        if cur.rowcount != 1:
            return None
        return self.get_sr_task_by_id(task_id)

    def set_preview_state(self, task_id: int, state: str,
                          note: str | None = None) -> dict | None:
        """写回急生成结局（done / skipped / failed）+ 人话说明。

        **不碰 updated_at**：那个列是「这行最近一次写回」的时刻，队列按它排序、
        界面上也有对应读数。预览生成没生成成与作业本身无关，抬它会让人以为作业动了。
        """
        db = self._db()
        db.execute("UPDATE sr_tasks SET preview_state = ?, preview_note = ? "
                   "WHERE id = ?", (state, note, task_id))
        db.commit()
        return self.get_sr_task_by_id(task_id)

    def mark_preview_cleared(self, task_id: int, note: str | None = None) -> bool:
        """把一行的预览状态标成 `cleared`（人工清了缓存），挡掉后续急生成认领。

        **这是 CAS，不能用 `set_preview_state` 顶替。** 后者是无条件 UPDATE，会把
        别人写下的 `'running'` 一起盖掉 —— 那个 running 属于一个**正在跑**的
        `_bake_product_preview`，它跑完还会调 `set_preview_state(..., 'done')` 把值
        写回来。于是库里说 done、盘上文件已被我们删掉，两边都以为自己是对的。
        抢不到就返回 False，由调用方如实报成「后台正在预览生成这一景」。

        WHERE 里的 `preview_state IS NULL OR preview_state != 'running'`：
        SQL 中 `NULL != 'running'` 求值为 NULL 而非 TRUE，只写后半句会把「从没生成过」
        的行整个漏掉 —— 而它们恰恰最需要被挡住（急生成下一轮就会认领，几秒后文件
        复活，用户以为清除没生效）。

        `status = 'COMPLETED'` 也是判据：正在排队/运行的同一 fingerprint 不该被标，
        否则那次运行跑完后不会再有自动预览（该行 preview_state 已被我们钉成非
        NULL）。用户清的是**旧缓存**，新的一次运行理应照常生成 —— 而重新提交会经
        `put_sr_task` 把 preview_state 归 NULL 重新武装，这条不冲突。

        不碰 updated_at，理由同 `set_preview_state`。
        """
        db = self._db()
        cur = db.execute(
            "UPDATE sr_tasks SET preview_state = 'cleared', preview_note = ? "
            "WHERE id = ? AND status = 'COMPLETED' "
            "AND (preview_state IS NULL OR preview_state != 'running')",
            (note, task_id))
        db.commit()
        return cur.rowcount == 1

    def put_sr_task(self, fingerprint: str, params: dict, *,
                    session_id: str | None = None, status: str = "submitted",
                    job_id: int | None = None, config_xml: str | None = None,
                    batch_script: str | None = None,
                    log_dir: str | None = None) -> dict:
        """Insert a task row, or update the existing row for this fingerprint.

        Used by run_sr to record the submit *intent* (job_id=None) before the
        sbatch side effect, then to record the resulting job_id. Returns the
        task row as read back.

        **UPDATE 分支恰好等于「又是一次真提交」**：run_sr 的幂等复用（RESUMED_ACTIVE /
        RESUMED_COMPLETED）在走到这里之前就返回了（submit_run_sr），所以能把本次运行
        的时间窗在这里清零 —— started_at / finished_at 归 NULL，等校准器首次看到
        RUNNING / 终态再钉。created_at 不动：它是这一行**第一次**提交的时刻，队列排序
        与「创建时间」列都靠它，而耗时已不再派生自它。

        `preview_state / preview_note` 一并归 NULL：**同一 suffix 重跑必须重新武装
        急生成**，否则第二次跑完永远停在旧的 `done` 上、盘上那份预览还是上一次的产物。
        这个重置依赖「提交发生在轮询观测到终态之前」—— 顺序天然成立（提交是同步的
        请求路径，终态要等调度器回话），但它是**隐含依赖**，所以写在这里。
        """
        now = time.time()
        db = self._db()
        existing = db.execute(
            "SELECT id FROM sr_tasks WHERE fingerprint = ?",
            (fingerprint,)).fetchone()
        if existing is None:
            db.execute(
                "INSERT INTO sr_tasks (fingerprint, session_id, job_id, status, "
                "params, config_xml, batch_script, log_dir, created_at, updated_at, "
                "started_at, finished_at, preview_state, preview_note) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, NULL)",
                (fingerprint, session_id, job_id, status,
                 json.dumps(params, ensure_ascii=False), config_xml,
                 batch_script, log_dir, now, now))
        else:
            db.execute(
                "UPDATE sr_tasks SET session_id = ?, job_id = ?, status = ?, "
                "params = ?, config_xml = ?, batch_script = ?, log_dir = ?, "
                "updated_at = ?, started_at = NULL, finished_at = NULL, "
                "preview_state = NULL, preview_note = NULL "
                "WHERE id = ?",
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

    # ---- durable goals ---------------------------------------------------
    def _goal_row(self, row):
        if row is None: return None
        return {"goal_id": row[0], "session_id": row[1], "request_key": row[2],
                "title": row[3], "status": row[4], "phase": row[5],
                "checkpoint": json.loads(row[6] or "{}"), "version": row[7],
                "lease_owner": row[8], "lease_until": row[9], "next_wake_at": row[10],
                "max_rounds": row[11], "rounds_used": row[12],
                "max_model_calls": row[13], "model_calls_used": row[14],
                "error": row[15], "created_at": row[16], "updated_at": row[17],
                "finished_at": row[18], "lease_token": row[19]}

    _GOAL_COLS = ("goal_id, session_id, request_key, title, status, phase, checkpoint, "
                  "version, lease_owner, lease_until, next_wake_at, max_rounds, "
                  "rounds_used, max_model_calls, model_calls_used, error, created_at, "
                  "updated_at, finished_at, lease_token")

    def get_goal(self, goal_id: str):
        return self._goal_row(self._db().execute(
            f"SELECT {self._GOAL_COLS} FROM agent_goals WHERE goal_id=?", (goal_id,)).fetchone())

    def get_goal_by_request_key(self, key: str):
        return self._goal_row(self._db().execute(
            f"SELECT {self._GOAL_COLS} FROM agent_goals WHERE request_key=?", (key,)).fetchone())

    def set_goal_session(self, goal_id: str, session_id: str) -> None:
        self._db().execute("UPDATE agent_goals SET session_id=?,updated_at=? WHERE goal_id=?", (session_id,time.time(),goal_id)); self._db().commit()

    def create_goal(self, *, title: str, session_id: str | None = None,
                    request_key: str | None = None, checkpoint: dict | None = None,
                    max_rounds: int = 20, max_model_calls: int = 100,
                    status: str = "QUEUED", phase: str = "queued") -> dict:
        now = time.time(); gid = uuid.uuid4().hex
        db = self._db(); db.execute("BEGIN IMMEDIATE")
        try:
            if request_key:
                old = self.get_goal_by_request_key(request_key)
                if old: db.rollback(); return old
            safe_cp = {k: v for k, v in (checkpoint or {}).items() if k == "acceptance"}
            db.execute(f"INSERT INTO agent_goals ({self._GOAL_COLS}) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                       (gid, session_id, request_key, title, status, phase,
                        json.dumps(safe_cp, ensure_ascii=False), 0, None, None, now,
                        max(1,int(max_rounds)), 0, max(1,int(max_model_calls)), 0, None, now, now, None, None))
            self._append_goal_event_tx(db, gid, "goal_created", {"status": status})
            db.commit()
        except Exception:
            db.rollback(); raise
        return self.get_goal(gid)

    def list_goals(self, *, limit: int = 100, statuses=None):
        q = f"SELECT {self._GOAL_COLS} FROM agent_goals"; args=[]
        if statuses:
            vals=list(statuses); q += " WHERE status IN (" + ",".join("?" for _ in vals) + ")"; args.extend(vals)
        q += " ORDER BY updated_at DESC LIMIT ?"; args.append(int(limit))
        return [self._goal_row(r) for r in self._db().execute(q, args).fetchall()]

    def _append_goal_event_tx(self, db, goal_id, event_type, payload=None, *, step_id=None, run_id=None):
        row = db.execute("SELECT COALESCE(MAX(seq),0)+1 FROM agent_goal_events WHERE goal_id=?", (goal_id,)).fetchone()
        seq = int(row[0]); now=time.time()
        db.execute("INSERT INTO agent_goal_events(goal_id,seq,event_type,run_id,step_id,payload,created_at) VALUES(?,?,?,?,?,?,?)",
                   (goal_id, seq, event_type, run_id, step_id, json.dumps(payload or {}, ensure_ascii=False), now))
        return seq

    def append_goal_event(self, goal_id, event_type, payload=None, *, step_id=None, run_id=None):
        db=self._db(); db.execute("BEGIN IMMEDIATE")
        try: seq=self._append_goal_event_tx(db, goal_id, event_type, payload, step_id=step_id, run_id=run_id); db.commit(); return seq
        except Exception: db.rollback(); raise

    def list_goal_events(self, goal_id, *, after: int = 0, limit: int = 500):
        rows=self._db().execute("SELECT seq,event_type,run_id,step_id,payload,created_at FROM agent_goal_events WHERE goal_id=? AND seq>? ORDER BY seq LIMIT ?", (goal_id,int(after),int(limit))).fetchall()
        return [{"goal_id":goal_id,"seq":r[0],"event_type":r[1],"run_id":r[2],"step_id":r[3],"payload":json.loads(r[4] or "{}"),"created_at":r[5]} for r in rows]

    def transition_goal(self, goal_id: str, status: str, *, phase: str | None=None,
                        checkpoint: dict | None=None, error: str | None=None,
                        next_wake_at: float | None=None, expected_version: int | None=None,
                        event_type: str | None=None, event_payload: dict | None=None,
                        lease_token=None, release=False, rounds_inc=0, model_calls_inc=0,
                        _verification=False) -> dict | None:
        valid = {"QUEUED","RUNNING","WAITING_EXTERNAL","VERIFYING","RETRY_WAIT","PAUSED","BLOCKED","COMPLETED","FAILED","CANCELLED"}
        if status not in valid: raise ValueError(f"invalid goal status: {status}")
        if status == "COMPLETED" and not _verification:
            raise ValueError("COMPLETED requires the backend Goal verifier")
        db=self._db(); db.execute("BEGIN IMMEDIATE")
        try:
            old=self.get_goal(goal_id)
            if not old: db.rollback(); return None
            if expected_version is not None and old["version"] != expected_version: db.rollback(); return None
            if lease_token is not None and (old["lease_token"] != lease_token or not old["lease_until"] or old["lease_until"] <= time.time()):
                raise LeaseLost("goal lease expired or replaced")
            now=time.time(); finished=now if status in {"COMPLETED","FAILED","CANCELLED","BLOCKED"} else None
            sets=["status=?","version=version+1","updated_at=?"]; vals=[status,now]
            sets += ["rounds_used=rounds_used+?", "model_calls_used=model_calls_used+?"]
            vals += [rounds_inc, model_calls_inc]
            if release or status in {"PAUSED", "CANCELLED", "BLOCKED", "COMPLETED", "FAILED"}:
                sets += ["lease_owner=NULL", "lease_token=NULL", "lease_until=NULL"]
            for col,val in (("phase",phase),("checkpoint",json.dumps(checkpoint,ensure_ascii=False) if checkpoint is not None else None),("error",error), ("next_wake_at",next_wake_at)):
                if val is not None: sets.append(col+"=?"); vals.append(val)
            if finished is not None: sets.append("finished_at=?"); vals.append(finished)
            vals.append(goal_id); db.execute("UPDATE agent_goals SET "+", ".join(sets)+" WHERE goal_id=?", vals)
            self._append_goal_event_tx(db, goal_id, event_type or "goal_status", {"status":status, **(event_payload or {})})
            db.commit()
        except Exception: db.rollback(); raise
        result = self.get_goal(goal_id)
        lease = current_lease.get()
        if lease is not None and lease.goal_id == goal_id:
            lease.version = result["version"]
        return result

    def claim_goal(self, goal_id: str, owner: str, *, lease_sec: float=60) -> dict | None:
        with self.worker_transaction() as db:
            now=time.time()
            cur=db.execute("UPDATE agent_goals SET lease_owner=?,lease_token=?,lease_until=?,version=version+1,updated_at=? WHERE goal_id=? AND status IN ('QUEUED','RUNNING','RETRY_WAIT','WAITING_EXTERNAL','VERIFYING') AND (next_wake_at IS NULL OR next_wake_at<=?) AND (lease_until IS NULL OR lease_until<=?)", (owner,uuid.uuid4().hex,now+lease_sec,now,goal_id,now,now))
            if not cur.rowcount: return None
            self._append_goal_event_tx(db, goal_id, "goal_claimed", {"owner":owner})
            return self.get_goal(goal_id)

    def renew_goal(self, goal_id, token, expected_version, *, lease_sec=60):
        with self.worker_transaction() as db:
            now=time.time()
            cur=db.execute("UPDATE agent_goals SET lease_until=? WHERE goal_id=? AND lease_token=? AND version=? AND lease_until>?", (now+lease_sec,goal_id,token,expected_version,now))
            return bool(cur.rowcount)

    def release_goal(self, goal_id, token, expected_version):
        with self.worker_transaction() as db:
            cur=db.execute("UPDATE agent_goals SET lease_owner=NULL,lease_token=NULL,lease_until=NULL WHERE goal_id=? AND lease_token=? AND version=? AND lease_until>?", (goal_id,token,expected_version,time.time()))
            return bool(cur.rowcount)

    def get_sr_task_by_job(self, job_id):
        row=self._db().execute("SELECT fingerprint FROM sr_tasks WHERE job_id=?", (job_id,)).fetchone()
        return self.get_sr_task(row[0]) if row else None

    def list_tool_intents(self, goal_id):
        keys=self._db().execute("SELECT idempotency_key FROM agent_tool_intents WHERE goal_id=? ORDER BY created_at,intent_id", (goal_id,)).fetchall()
        return [self.get_tool_intent(goal_id, r[0]) for r in keys]

    def complete_verified_goal(self, goal_id, verification, **kwargs):
        if verification.get("ok") is not True:
            raise ValueError("verification did not pass")
        return self.transition_goal(goal_id, "COMPLETED", _verification=True,
                                    event_type="goal_verified", event_payload=verification, **kwargs)

    def create_tool_intent(self, *, goal_id, tool_name, args, idempotency_key, side_effect_class="READ_ONLY", replay_policy="REPLAY_READ", step_id=None):
        iid=uuid.uuid4().hex; now=time.time(); db=self._db()
        db.execute("INSERT OR IGNORE INTO agent_tool_intents(intent_id,goal_id,step_id,tool_name,args,idempotency_key,side_effect_class,replay_policy,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)", (iid,goal_id,step_id,tool_name,json.dumps(args,sort_keys=True,ensure_ascii=False),idempotency_key,side_effect_class,replay_policy,now,now)); db.commit()
        return self.get_tool_intent(goal_id,idempotency_key)

    def get_tool_intent(self, goal_id, key):
        r=self._db().execute("SELECT intent_id,goal_id,step_id,tool_name,args,idempotency_key,side_effect_class,replay_policy,status,external_job_id,result,attempts,created_at,updated_at FROM agent_tool_intents WHERE goal_id=? AND idempotency_key=?",(goal_id,key)).fetchone()
        if not r:return None
        return {"intent_id":r[0],"goal_id":r[1],"step_id":r[2],"tool_name":r[3],"args":json.loads(r[4]),"idempotency_key":r[5],"side_effect_class":r[6],"replay_policy":r[7],"status":r[8],"external_job_id":r[9],"result":json.loads(r[10]) if r[10] else None,"attempts":r[11],"created_at":r[12],"updated_at":r[13]}

    def update_tool_intent(self, intent_id, *, status=None, external_job_id=None, result=None, attempts_inc=False):
        sets=[]; vals=[]
        if status is not None: sets.append("status=?"); vals.append(status)
        if external_job_id is not None: sets.append("external_job_id=?"); vals.append(external_job_id)
        if result is not None: sets.append("result=?"); vals.append(json.dumps(result,ensure_ascii=False))
        if attempts_inc: sets.append("attempts=attempts+1")
        sets.append("updated_at=?"); vals.append(time.time()); vals.append(intent_id)
        db = self._db()
        db.execute("UPDATE agent_tool_intents SET "+", ".join(sets)+" WHERE intent_id=?",vals)
        if external_job_id is not None:
            db.execute("UPDATE agent_goal_steps SET external_job_id=?,updated_at=? WHERE step_id=(SELECT step_id FROM agent_tool_intents WHERE intent_id=?)", (external_job_id,time.time(),intent_id))
        db.commit()

    def list_goal_steps(self, goal_id):
        rows=self._db().execute("SELECT step_id,goal_id,ordinal,name,status,tool_name,external_job_id,verification,error,created_at,updated_at FROM agent_goal_steps WHERE goal_id=? ORDER BY ordinal",(goal_id,)).fetchall()
        return [{"step_id":r[0],"goal_id":r[1],"ordinal":r[2],"name":r[3],"status":r[4],"tool_name":r[5],"external_job_id":r[6],"verification":json.loads(r[7] or "{}"),"error":r[8],"created_at":r[9],"updated_at":r[10]} for r in rows]

    def put_goal_step(self, goal_id, *, ordinal: int, name: str, status="PENDING", tool_name=None):
        sid=uuid.uuid4().hex; now=time.time(); db=self._db()
        db.execute("INSERT INTO agent_goal_steps(step_id,goal_id,ordinal,name,status,tool_name,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(goal_id,ordinal) DO UPDATE SET name=excluded.name,status=excluded.status,tool_name=excluded.tool_name,updated_at=excluded.updated_at",(sid,goal_id,int(ordinal),name,status,tool_name,now,now)); db.commit()
        return next((x for x in self.list_goal_steps(goal_id) if x["ordinal"]==int(ordinal)),None)


# Cover the actual write boundary, including session/trajectory/SR writes made
# indirectly by tools. Ordinary chat calls retain their existing API.
for _name in (
    "create_session", "set_session_status", "append_message", "append_llm_usage",
    "create_agent_run", "interrupt_open_agent_runs", "finish_agent_run",
    "append_trajectory_event", "set_goal_session", "append_goal_event",
    "transition_goal", "create_tool_intent", "update_tool_intent", "put_goal_step",
    "put_sr_task", "update_sr_task_job", "set_sr_task_state", "complete_verified_goal",
):
    setattr(Store, _name, _fenced_write(getattr(Store, _name)))
