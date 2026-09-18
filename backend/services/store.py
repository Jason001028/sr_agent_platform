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
    updated_at   REAL NOT NULL,
    started_at   REAL,
    finished_at  REAL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_sr_tasks_fingerprint
    ON sr_tasks(fingerprint);
"""

#: sr_tasks 里发布后新增的列。`CREATE TABLE IF NOT EXISTS` 对**已存在**的表一个字
#: 都不改，所以升级前建的库（生产上就有）只能靠 ALTER TABLE 补 —— 见 _ensure_columns。
_SR_TASK_ADDED_COLUMNS = (("started_at", "REAL"), ("finished_at", "REAL"))


def _ensure_columns(db: sqlite3.Connection) -> None:
    """Add the post-release sr_tasks columns to an existing DB.

    幂等：先问 PRAGMA 有什么，缺哪列补哪列。补出来的列在老行里是 NULL，**不回填**
    —— 那些行的 created_at 语义已被「复用行」污染（同一行被重交过，created_at 还是
    第一次提交的时刻），回填等于把一个错数固化进历史。老行的耗时显示「—」。
    """
    have = {row[1] for row in db.execute("PRAGMA table_info(sr_tasks)")}
    added = False
    for name, decl in _SR_TASK_ADDED_COLUMNS:
        if name not in have:
            db.execute(f"ALTER TABLE sr_tasks ADD COLUMN {name} {decl}")
            added = True
    if added:
        db.commit()


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
            # check_same_thread=False: 阶段5 API 跨线程共用同一 Store
            # （SSE chat 在 to_thread 里写、sync 端点在 threadpool、poller 在
            # event loop），SQLite 自带行锁 + busy timeout 兜底并发写。
            self._conn = sqlite3.connect(self.path, timeout=10,
                                         check_same_thread=False)
            self._conn.executescript(_SCHEMA)
            _ensure_columns(self._conn)
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

    _SR_TASK_COLS = ("id, fingerprint, session_id, job_id, status, params, "
                     "config_xml, batch_script, log_dir, created_at, updated_at, "
                     "started_at, finished_at")

    @staticmethod
    def _sr_task_row(row) -> dict:
        return {"task_id": row[0], "fingerprint": row[1], "session_id": row[2],
                "job_id": row[3], "status": row[4], "params": json.loads(row[5]),
                "config_xml": row[6], "batch_script": row[7], "log_dir": row[8],
                "created_at": row[9], "updated_at": row[10],
                "started_at": row[11], "finished_at": row[12]}

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
                "started_at, finished_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL)",
                (fingerprint, session_id, job_id, status,
                 json.dumps(params, ensure_ascii=False), config_xml,
                 batch_script, log_dir, now, now))
        else:
            db.execute(
                "UPDATE sr_tasks SET session_id = ?, job_id = ?, status = ?, "
                "params = ?, config_xml = ?, batch_script = ?, log_dir = ?, "
                "updated_at = ?, started_at = NULL, finished_at = NULL "
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
