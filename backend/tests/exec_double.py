"""本地执行器的内存替身（backend/services/local_exec.py 的六个入口）。

面向 API 层的用例问的不是「进程有没有起来」，而是「任务服务、队列视图、SSE 广播
拿到一个状态之后做什么」。真去跑 bash 子进程会让每个用例都依赖 bash 在不在、跑多
快、机器上有没有卡；这些用例因此把执行器的入口换成一个状态机：

    submit → PENDING ──(一格)──→ RUNNING ──(一格)──→ COMPLETED

时钟是真的（time.monotonic），所以「有没有亲眼看到 PENDING 再看 RUNNING」取决于
一格够不够被采样到：长度取 `SR_TEST_EXEC_STAGE_MS`，默认 500ms（队列轮询约 30ms
一次）。要「停在 PENDING 等被取消」就把这一格调得比整个用例长。

替身只回答「有个进程在跑 / 它是这么结束的」；`status` 之后的每一层 —— 显示状态机
`_queue_state`、sr_tasks 写回、SSE 帧、幂等复用 —— 跑的都是真代码。调用签名
（`job_id`、`exit_code_file`）与真执行器一致，调用方少传一个参数照样会挂。

判定文件这一层不替身：`status` 先问 `sr_result.terminal_from_exit_file`，判定文件
在就直接按它答 —— 与真执行器同一条顺序、同一个解析器，只是「进程还在不在」换成
了这里的时钟。于是「上一次跑完了没有」这类用例（幂等复用）写一个真判定文件就行，
问的仍是平台与校验器之间那份真契约。

真执行器自己的行为不在这个模块的射程内：单槽、取消、重启记录、判定文件见
test_local_exec.py；真 bash + 真校验器 + 真判定文件见 test_local_chain.py。
"""

import contextlib
import os
import time
from unittest import mock

from backend.services import local_exec
from backend.services import sr_result

#: 一格的毫秒数。只影响这个替身，与生产配置无关。
STAGE_ENV = "SR_TEST_EXEC_STAGE_MS"
DEFAULT_STAGE_MS = 500

#: 被替换的六个入口 —— 就是 run_sr 会调的那六个。
ENTRY_POINTS = ("available", "reserve_job_id", "submit", "status", "cancel",
                "release_unknown")


class FakeExecutor:
    """A run directory that reports PENDING → RUNNING → COMPLETED on a clock."""

    def __init__(self, stage_sec=None, first_id=1):
        if stage_sec is None:
            stage_sec = float(os.environ.get(STAGE_ENV, DEFAULT_STAGE_MS)) / 1000.0
        self.stage_sec = stage_sec
        #: Set False to make the platform answer "本机执行不可用" the way it does
        #: on a host with no usable bash.
        self.bash_available = True
        self._next_id = int(first_id)
        self.jobs: dict[int, dict] = {}

    # ---- the entry points run_sr calls ----------------------------------

    def available(self) -> bool:
        return self.bash_available

    def reserve_job_id(self, work_dir=None) -> int:
        jid = self._next_id
        self._next_id += 1
        return jid

    def submit(self, script_path, env=None, job_id=None, run=None) -> int:
        jid = int(job_id) if job_id is not None else self.reserve_job_id()
        self.jobs[jid] = {"script": str(script_path), "run": dict(run or {}),
                          "started_at": time.monotonic(), "cancelled": False,
                          "forced": None}
        return jid

    def status(self, job_id, exit_code_file=None) -> dict:
        jid = int(job_id)
        st = sr_result.terminal_from_exit_file(jid, exit_code_file)
        if st["state"] != "UNKNOWN":
            return st                 # the run's own verdict file decides
        rec = self.jobs.get(jid)
        if rec is None:
            # Nothing was ever handed to this executor: the same "cannot tell"
            # the real one reports (active is None, not False).
            return {"job_id": jid, "active": None, "state": "UNKNOWN",
                    "exit_code": None}
        if rec["forced"]:
            return {"job_id": jid, "active": False, "state": rec["forced"],
                    "exit_code": None,
                    "note": rec.get("note") or "替身：这个运行以失败结束"}
        # The verdict file outranks everything else, so a run that finished
        # stays COMPLETED even if a cancel arrives afterwards.
        if time.monotonic() - rec["started_at"] >= 2 * self.stage_sec:
            return {"job_id": jid, "active": False, "state": "COMPLETED",
                    "exit_code": "0:0"}
        if rec["cancelled"]:
            return {"job_id": jid, "active": False, "state": "CANCELLED",
                    "exit_code": None, "note": rec.get("note")}
        elapsed = time.monotonic() - rec["started_at"]
        return {"job_id": jid, "active": True,
                "state": "PENDING" if elapsed < self.stage_sec else "RUNNING",
                "exit_code": None}

    def cancel(self, job_id) -> bool:
        rec = self.jobs.get(int(job_id))
        if rec is None:
            return False                      # not this executor's to cancel
        rec["cancelled"] = True
        return True

    def release_unknown(self, job_id, note: str = "操作员核实无残留进程后释放") -> bool:
        jid = int(job_id)
        rec = self.jobs.setdefault(
            jid, {"script": None, "run": {}, "started_at": time.monotonic(),
                  "cancelled": False, "forced": None})
        rec["cancelled"] = True
        rec["note"] = note
        return True

    # ---- what a case uses to steer --------------------------------------

    def finish(self, job_id, state="FAILED", note=None) -> None:
        """Make that run end with `state` (FAILED) right now."""
        rec = self.jobs[int(job_id)]
        rec["forced"] = state
        if note:
            rec["note"] = note

    def submitted(self, job_id) -> dict:
        """The record of one submit, for "did the platform pass what it should"."""
        return self.jobs[int(job_id)]


class fake_exec:
    """Replace the executor's entry points for the duration of a case.

    Usable as a context manager or as start()/stop() around a whole TestCase;
    `self.fake` is the :class:`FakeExecutor` the case then steers.
    """

    def __init__(self, **kw):
        self.fake = FakeExecutor(**kw)
        self._stack = contextlib.ExitStack()

    def start(self) -> FakeExecutor:
        for name in ENTRY_POINTS:
            self._stack.enter_context(
                mock.patch.object(local_exec, name, getattr(self.fake, name)))
        return self.fake

    def stop(self) -> None:
        self._stack.close()

    def __enter__(self) -> FakeExecutor:
        return self.start()

    def __exit__(self, *exc) -> bool:
        self.stop()
        return False
