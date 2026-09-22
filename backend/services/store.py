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
              preview_state/preview_note 是**产物预览急烤**的进度（2026-09-20 增）：
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
#:
#: preview_state / preview_note（2026-09-20）：产物预览的服务端急烤状态。见
#: `list_preview_candidates` 那一段。补出来的列在老行里是 NULL，**这正是想要的**
#: —— NULL 的含义是「没烤过」，急烤循环只认这个值，所以升级当天的历史 COMPLETED
#: 行会被认领；挡它的是**年龄窗口**（`finished_at` 超出窗口就不烤），不是回填。
_SR_TASK_ADDED_COLUMNS = (("started_at", "REAL"), ("finished_at", "REAL"),
                          ("preview_state", "TEXT"), ("preview_note", "TEXT"))


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

    # ---- 产物预览急烤（api/app.py::_eager_bake_tick 的队列） ---------------
    #
    # 为什么队列是**从库派生**的，而不是在状态转换点上入队：写终态的
    # `platform._task_state` 有**两个**调用者 —— 后台 `_poll_once` 与请求路径
    # `_task_view`（GET /api/queue）。谁先观测到 RUNNING→COMPLETED 谁把「变了」
    # 这个信号拿走，另一个看到的是「没变化」，挂在转换点上的入队钩子必然偶发漏烤。
    # 派生 + `claim_preview_bake` 的原子 CAS 之后，这个竞态在结构上不存在：
    # 急烤循环不关心「谁先看到」，它只关心「这行还没被认领」。
    #
    # preview_state 取值：NULL（没烤）→ running → done | skipped | failed | cleared。
    # 粒度是**行**（= 一个 task_fingerprint），所以同一 suffix 重跑必须重新武装，
    # 由 put_sr_task 的 UPDATE 分支负责（见那里的注释）。
    # `cleared` 是**人工**结局、不由烘焙流程写：场景库的「清除缓存」把文件删掉后
    # 标上它，让这行不再是候选（否则下一轮急烤会把文件重新烤回来，用户以为白清了）。
    # 写它的入口只有一个 —— `mark_preview_cleared`（CAS，见那里的注释）。

    #: 每轮最多看一眼多少行 COMPLETED 候选（不是每轮烤多少 —— 那恒为 1）。
    _PREVIEW_SCAN_LIMIT = 20

    def list_preview_candidates(self, *, max_age_sec: float,
                                limit: int | None = None) -> list[dict]:
        """等着做产物预览的 COMPLETED 行，最新跑完的在前。

        三条判据都是必须的：
          * `status='COMPLETED'` —— **FAILED 绝不烤**。`writeTiff` 先 rename 再写，
            失败的运行会在产物路径上留下半截文件，烤出来是坏图。
          * `preview_state IS NULL` —— 还没被认领过（认领即写 'running'）。
          * `finished_at >= ?` —— 年龄窗口。`finished_at IS NULL` 的行（加这两列之前
            建的、或整段运行期间 sr-api 不在场没观测到开始的）**一律排除**：那个值是
            NOT NULL 比较，NULL 天然不在窗口内，所以不用额外写条件，但这条判据是
            升级当天不把历史 COMPLETED 行全烤一遍的**唯一**屏障。
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
        """认领一行的产物预览烘焙。抢到返回该行，被人抢在前面则 None。

        这是并发与重复认领的**唯一裁决点**：CAS 写 `preview_state='running'`，
        只有 `rowcount == 1` 才算抢到。`status='COMPLETED'` 一并写进 WHERE，是为了
        挡住「认领与重跑赛跑」—— 用户在这一行刚跑完、急烤还没动手时又交了同一个
        suffix，`put_sr_task` 会把 status 打回 submitted 并清 preview_state，
        此时这份认领必须失效（否则会去烤一个正在被重写的产物）。
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
        """写回急烤结局（done / skipped / failed）+ 人话说明。

        **不碰 updated_at**：那个列是「这行最近一次写回」的时刻，队列按它排序、
        界面上也有对应读数。预览烤没烤成与作业本身无关，抬它会让人以为作业动了。
        """
        db = self._db()
        db.execute("UPDATE sr_tasks SET preview_state = ?, preview_note = ? "
                   "WHERE id = ?", (state, note, task_id))
        db.commit()
        return self.get_sr_task_by_id(task_id)

    def mark_preview_cleared(self, task_id: int, note: str | None = None) -> bool:
        """把一行的预览状态标成 `cleared`（人工清了缓存），挡掉后续急烤认领。

        **这是 CAS，不能用 `set_preview_state` 顶替。** 后者是无条件 UPDATE，会把
        别人写下的 `'running'` 一起盖掉 —— 那个 running 属于一个**正在跑**的
        `_bake_product_preview`，它跑完还会调 `set_preview_state(..., 'done')` 把值
        写回来。于是库里说 done、盘上文件已被我们删掉，两边都以为自己是对的。
        抢不到就返回 False，由调用方如实报成「后台正在烘焙这一景」。

        WHERE 里的 `preview_state IS NULL OR preview_state != 'running'`：
        SQL 中 `NULL != 'running'` 求值为 NULL 而非 TRUE，只写后半句会把「从没烤过」
        的行整个漏掉 —— 而它们恰恰最需要被挡住（急烤下一轮就会认领，几秒后文件
        复活，用户以为清除没生效）。

        `status = 'COMPLETED'` 也是判据：正在排队/运行的同一 fingerprint 不该被标，
        否则那次运行跑完后不会再有自动预览（该行 preview_state 已被我们钉成非
        NULL）。用户清的是**旧缓存**，新的一次运行理应照常烤 —— 而重新提交会经
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
        急烤**，否则第二次跑完永远停在旧的 `done` 上、盘上那份预览还是上一次的产物。
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
