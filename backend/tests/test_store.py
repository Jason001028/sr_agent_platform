"""Tests for the SQLite session store (P0① sessions + messages + sr_tasks)."""

import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from backend.services.store import Store, default_db_path, default_store


def make_store():
    d = tempfile.TemporaryDirectory()
    return Store(os.path.join(d.name, "db.sqlite")), d


class TestDefaultPaths(unittest.TestCase):
    def test_default_db_path_env(self):
        os.environ["SR_AGENT_DB"] = "/tmp/x.sqlite"
        try:
            self.assertEqual(default_db_path(), "/tmp/x.sqlite")
        finally:
            del os.environ["SR_AGENT_DB"]

    def test_default_db_path_fallback(self):
        self.assertEqual(default_db_path(), "sr_agent.db")

    def test_default_store_is_lazy(self):
        # building a default store must not touch disk
        store = default_store()
        self.assertIsNone(store._conn)
        store.close()


class TestSessions(unittest.TestCase):
    def setUp(self):
        self.store, self._tmp = make_store()

    def tearDown(self):
        self.store.close()
        self._tmp.cleanup()

    def test_create_and_get(self):
        sid = self.store.create_session({"who": "test"})
        s = self.store.get_session(sid)
        self.assertEqual(s["session_id"], sid)
        self.assertEqual(s["status"], "active")
        self.assertEqual(s["metadata"]["who"], "test")

    def test_unknown_session_is_none(self):
        self.assertIsNone(self.store.get_session("nope"))

    def test_set_status(self):
        sid = self.store.create_session()
        self.store.set_session_status(sid, "done")
        self.assertEqual(self.store.get_session(sid)["status"], "done")

    def test_list_sessions_newest_first(self):
        a = self.store.create_session()
        b = self.store.create_session()
        ids = [s["session_id"] for s in self.store.list_sessions()]
        self.assertIn(a, ids)
        self.assertIn(b, ids)
        self.assertEqual(ids[0], b)  # most recently touched first


class TestMessages(unittest.TestCase):
    def setUp(self):
        self.store, self._tmp = make_store()
        self.sid = self.store.create_session()

    def tearDown(self):
        self.store.close()
        self._tmp.cleanup()

    def test_wire_format_roundtrip(self):
        msgs = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": None,
             "tool_calls": [{"id": "c1", "type": "function",
                             "function": {"name": "x", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "c1", "content": '{"ok": true}'},
        ]
        for m in msgs:
            self.store.append_message(self.sid, m)
        self.assertEqual(self.store.get_messages(self.sid), msgs)

    def test_order_preserved(self):
        for i in range(5):
            self.store.append_message(self.sid, {"role": "user", "content": str(i)})
        got = self.store.get_messages(self.sid)
        self.assertEqual([m["content"] for m in got], ["0", "1", "2", "3", "4"])

    def test_sessions_isolated(self):
        other = self.store.create_session()
        self.store.append_message(self.sid, {"role": "user", "content": "a"})
        self.store.append_message(other, {"role": "user", "content": "b"})
        self.assertEqual(len(self.store.get_messages(self.sid)), 1)
        self.assertEqual(len(self.store.get_messages(other)), 1)

    def test_empty_session(self):
        self.assertEqual(self.store.get_messages(self.sid), [])


class TestSrTasks(unittest.TestCase):
    """P0② backing — sr_tasks table used for run_sr idempotency."""

    def setUp(self):
        self.store, self._tmp = make_store()

    def tearDown(self):
        self.store.close()
        self._tmp.cleanup()

    def test_intent_then_job(self):
        # run_sr records intent (job_id None) before sbatch, then the job_id.
        self.store.put_sr_task("fp1", {"lq_path": "/a"}, status="new", job_id=None)
        t = self.store.get_sr_task("fp1")
        self.assertIsNone(t["job_id"])
        self.assertEqual(t["status"], "new")

        self.store.update_sr_task_job("fp1", job_id=7, status="submitted")
        t = self.store.get_sr_task("fp1")
        self.assertEqual(t["job_id"], 7)
        self.assertEqual(t["status"], "submitted")
        self.assertEqual(t["params"], {"lq_path": "/a"})

    def test_upsert_same_fingerprint_is_one_row(self):
        self.store.put_sr_task("fp2", {"lq_path": "/a"}, status="new", job_id=None)
        self.store.put_sr_task("fp2", {"lq_path": "/a"}, status="submitted", job_id=9)
        t = self.store.get_sr_task("fp2")
        self.assertEqual(t["job_id"], 9)

    def test_distinct_fingerprints_distinct_tasks(self):
        self.store.put_sr_task("x", {"lq_path": "/a"}, status="submitted", job_id=1)
        self.store.put_sr_task("y", {"lq_path": "/b"}, status="submitted", job_id=2)
        self.assertIsNotNone(self.store.get_sr_task("x"))
        self.assertIsNotNone(self.store.get_sr_task("y"))
        self.assertEqual(self.store.get_sr_task("x")["job_id"], 1)
        self.assertEqual(self.store.get_sr_task("y")["job_id"], 2)

    def test_unknown_fingerprint_none(self):
        self.assertIsNone(self.store.get_sr_task("missing"))

    def test_optional_sidecar_paths(self):
        self.store.put_sr_task("fp3", {"lq_path": "/a"}, status="submitted",
                               job_id=5, config_xml="/w/cfg.xml",
                               batch_script="/w/run.sh", log_dir="/w")
        t = self.store.get_sr_task("fp3")
        self.assertEqual(t["config_xml"], "/w/cfg.xml")
        self.assertEqual(t["batch_script"], "/w/run.sh")
        self.assertEqual(t["log_dir"], "/w")


class TestSrTasksQueueApi(unittest.TestCase):
    """阶段5 补充 — queue REST 端点依赖的查询/状态写回层."""

    def setUp(self):
        self.store, self._tmp = make_store()

    def tearDown(self):
        self.store.close()
        self._tmp.cleanup()

    def test_list_sr_tasks_newest_first(self):
        self.store.put_sr_task("q1", {"lq_path": "/a"}, status="new", job_id=None)
        self.store.update_sr_task_job("q1", job_id=1, status="submitted")
        self.store.put_sr_task("q2", {"lq_path": "/b"}, status="new", job_id=None)
        self.store.update_sr_task_job("q2", job_id=2, status="submitted")
        ids = [t["task_id"] for t in self.store.list_sr_tasks()]
        self.assertEqual(ids, sorted(ids, reverse=True))   # newest first
        by_id = {t["task_id"]: t for t in self.store.list_sr_tasks()}
        self.assertEqual(by_id[ids[0]]["job_id"], 2)       # q2 submitted last

    def test_get_sr_task_by_id(self):
        self.store.put_sr_task("q3", {"lq_path": "/c"}, status="new", job_id=None)
        self.store.update_sr_task_job("q3", job_id=3, status="submitted")
        by_fp = self.store.get_sr_task("q3")
        by_pk = self.store.get_sr_task_by_id(by_fp["task_id"])
        self.assertEqual(by_pk["fingerprint"], "q3")
        self.assertEqual(by_pk["job_id"], 3)
        self.assertIsNone(self.store.get_sr_task_by_id(99999))

    def test_set_sr_task_state(self):
        self.store.put_sr_task("q4", {"lq_path": "/d"}, status="new", job_id=None)
        self.store.update_sr_task_job("q4", job_id=4, status="submitted")
        t = self.store.get_sr_task("q4")
        written = self.store.set_sr_task_state(t["task_id"], "RUNNING")
        got = self.store.get_sr_task("q4")
        self.assertEqual(got["status"], "RUNNING")
        # the idempotency layer reads only job_id — status writeback is opaque to it
        self.assertEqual(got["job_id"], 4)
        # 返回值 = 这次写回后从库里读回的整行。调用方（api.platform._task_state）要拿
        # 它去广播/回显：客户端只有拿到写之后的值，才算得对耗时列。
        self.assertEqual(written["updated_at"], got["updated_at"])
        self.assertGreaterEqual(written["updated_at"], t["updated_at"])
        # 没让钉运行窗的写回就不动那两列
        self.assertIsNone(got["started_at"])
        self.assertIsNone(got["finished_at"])

    def test_run_window_is_stamped_only_when_asked(self):
        self.store.put_sr_task("q5", {"lq_path": "/e"}, status="new", job_id=None)
        self.store.update_sr_task_job("q5", job_id=5, status="submitted")
        tid = self.store.get_sr_task("q5")["task_id"]

        running = self.store.set_sr_task_state(tid, "RUNNING", mark_started=True)
        self.assertIsNotNone(running["started_at"])
        self.assertIsNone(running["finished_at"], "还没跑完，没有终点")

        done = self.store.set_sr_task_state(tid, "COMPLETED", mark_finished=True)
        self.assertEqual(done["started_at"], running["started_at"], "起点不回退/不改写")
        self.assertGreaterEqual(done["finished_at"], running["started_at"])

    def test_resubmit_resets_the_run_window_but_keeps_created_at(self):
        """重交（同一指纹复用同一行）时必须把运行窗清零。

        否则新一次跑会带着**上一次**的起点/终点进队列页：起点是上次的，终点是这次
        的，差值就是行龄 —— 用户看到的「大几十个小时」（2026-09-18）。
        created_at 不动：它是这一行第一次提交的时刻，队列排序靠它。
        """
        self.store.put_sr_task("q6", {"lq_path": "/f"}, status="new", job_id=None)
        self.store.update_sr_task_job("q6", job_id=6, status="submitted")
        t = self.store.get_sr_task("q6")
        self.store.set_sr_task_state(t["task_id"], "RUNNING", mark_started=True)
        self.store.set_sr_task_state(t["task_id"], "FAILED", mark_finished=True)

        self.store.put_sr_task("q6", {"lq_path": "/f"}, status="new", job_id=None)
        again = self.store.get_sr_task("q6")
        self.assertIsNone(again["started_at"])
        self.assertIsNone(again["finished_at"])
        self.assertEqual(again["created_at"], t["created_at"])

    def test_existing_db_without_the_run_columns_is_migrated(self):
        """升级前的库（sr_tasks 已存在、没有这两列）打开时补列。

        生产上的 sr_agent.db 就是这种：`CREATE TABLE IF NOT EXISTS` 对已存在的表
        一个字都不改，不补列的话每次读写都撞 "no such column: started_at"。
        """
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        path = os.path.join(d.name, "old.sqlite")
        old = sqlite3.connect(path)
        old.executescript(
            "CREATE TABLE sr_tasks ("
            " id INTEGER PRIMARY KEY AUTOINCREMENT, fingerprint TEXT NOT NULL,"
            " session_id TEXT, job_id INTEGER, status TEXT NOT NULL,"
            " params TEXT NOT NULL, config_xml TEXT, batch_script TEXT,"
            " log_dir TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL);"
            "CREATE UNIQUE INDEX idx_sr_tasks_fingerprint"
            " ON sr_tasks(fingerprint);")
        old.execute("INSERT INTO sr_tasks (fingerprint, status, params,"
                    " created_at, updated_at) VALUES ('old', 'COMPLETED', '{}',"
                    " 100, 101)")
        old.commit()
        old.close()

        store = Store(path)
        self.addCleanup(store.close)
        legacy = store.get_sr_task("old")
        self.assertEqual(legacy["status"], "COMPLETED")
        self.assertIsNone(legacy["started_at"], "老行不回填：那会把错的数固化成历史")
        self.assertIsNone(legacy["finished_at"])

        store.put_sr_task("new", {"lq_path": "/a"}, status="new", job_id=None)   # 补过列才写得进
        row = store.get_sr_task("new")
        store.set_sr_task_state(row["task_id"], "RUNNING", mark_started=True)
        self.assertIsNotNone(store.get_sr_task("new")["started_at"])


if __name__ == "__main__":
    unittest.main()
