"""阶段5 API：工具直调 + 聊天(SSE) + 共享任务队列(REST/SSE) + 掩码落盘。

契约基准：docs/planning/api-contract.md（§2 端点清单总览 / §3 细节 / §4 机制）。
挂载在 create_app 返回的 FastAPI 实例上；运行时状态挂在 app.state
（store/cfg/会话锁/SSE 订阅者/任务状态缓存），由 create_app 初始化、lifespan
启动轮询。各端点只依赖 env + request.app.state —— 模块层不持有跨 app 状态。

实现要点
--------
* 聊天 SSE：`run_loop` 是同步阻塞函数 → `asyncio.to_thread` 跑；on_step 缝
  经 `loop.call_soon_threadsafe` 把事件推给本连接 asyncio.Queue，主协程逐帧
  yield 成 SSE（api-contract.md §4.1/§4.2）。会话串行：进程内 per-session
  asyncio.Lock，占用期再 POST → 409。
* 队列状态：SUBMITTING/PENDING/RUNNING/COMPLETED/FAILED/UNKNOWN（§3.3）。
  sr_tasks.status 列语义升级为展示状态（幂等层只读 job_id，无回归）。
  GET /api/queue 对每个 job_id 非空任务当场校准一次（假调度器下每次可见推进），
  变化写回 DB + 广播；lifespan 轮询（SR_QUEUE_POLL_SEC）同理驱动广播。
  校准只写**真的变了**的状态：比较基准是内存缓存，缺失时回落到库里存的状态，
  所以重启后的第一轮不会把整表已终态的老行重新写一遍。
* 队列「耗时」= 本次运行的时间窗（sr_tasks.started_at → finished_at，见
  _task_state 的两个锚点），不是行的年龄 —— 一行 = 一个指纹，重复提交复用同一行，
  行的 created_at 停在第一次提交（2026-09-18 修的正是把它当耗时用）。
* 广播 = 进程内 set[subscriber]，每订阅者持 (注册时运行 loop, asyncio.Queue)；
  `_broadcast` 从任意线程用 call_soon_threadsafe 入队，断开的订阅者被丢弃。

说明：为保持单个请求内清晰，各同步端点的参数统一命名 `request: Request`，
运行时状态解包成局部 `state = request.app.state`（Starlette State）；helper
一律收 `state`，不再出现 app/Request/State 混用。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import stat
import tempfile
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from backend.agent import loop as loop_mod
from backend.api import paths
from backend.api.paths import PathDeniedError
from backend.config import sr_runtime
from backend.pathguard import (
    ensure_allowed, normalize_submit_path, to_posix_array_path)
from backend.services import local_exec
from backend.services import mask as mask_svc
from backend.services import run_sr as run_sr_svc
from backend.services import scene_search
from backend.services import slurm
#: 掩码命名的唯一实现搬到了 services（REST 与 agent 工具两个入口都要用同一份
#: 推导，见 scene_search.derived_mask_path）；这里转出给既有调用方
#: （本模块 bake_mask / _norm_sr_params、api/app.py::resolve）。
from backend.services.scene_search import derived_mask_path, mask_stem
from backend.tools import run_sr as run_sr_tool
from backend.tools.contract import list_tools

router = APIRouter()

_SENTINEL = object()


class _Subscriber:
    """One /api/queue/events connection: (注册时运行 loop, asyncio.Queue).

    Plain object so it is hashable (kept in a set); broadcast enqueues via its
    loop with call_soon_threadsafe (safe from any thread)."""

    __slots__ = ("loop", "q")

    def __init__(self, loop, q):
        self.loop = loop
        self.q = q


# --------------------------------------------------------------------------
# SSE framing + 广播
# --------------------------------------------------------------------------
def _frame(event: dict) -> str:
    return "data: " + json.dumps(event, ensure_ascii=False) + "\n\n"


def _sse_headers() -> dict:
    return {
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",     # nginx: 关缓冲，SSE 逐帧透传
        "Connection": "keep-alive",
    }


def _broadcast(state, event: dict) -> None:
    """Fan one event out to every connected /api/queue/events subscriber.

    Thread-safe: subscribers enqueue via the loop they were registered on
    (call_soon_threadsafe), so callers from threadpool / poller / event loop
    all work. A subscriber whose loop is gone (connection dropped) is dropped.
    """
    payload = _frame(event)
    for sub in list(state.subscribers):
        try:
            sub.loop.call_soon_threadsafe(sub.q.put_nowait, payload)
        except Exception:  # noqa: BLE001 — dead connection, drop it
            state.subscribers.discard(sub)


#: States a cancel can leave alone. Anything else — including UNKNOWN, which is
#: what a restarted sr-api reports for a job it no longer has a record of — must
#: not be answered with a bare "cancelled: false" (see cancel_queue).
_TERMINAL_STATES = ("COMPLETED", "FAILED", "CANCELLED")

#: 会写 finished_at 的显示态。UNKNOWN 不算：调度器一时没有记录、判定文件还没落盘
#: 都可能是**假的**「已结束」，之后还会翻成 COMPLETED/FAILED（§一 "C 方案"）。
_RUN_ENDED_STATES = ("COMPLETED", "FAILED")


def _queue_state(st: dict) -> str:
    """Map a slurm.job_status dict onto the queue display state machine (§3.3).

    squeue PENDING → PENDING；其它 active → RUNNING；sacct COMPLETED →
    COMPLETED；其它 terminal（FAILED/CANCELLED/TIMEOUT/OOM…）→ FAILED；
    调度器无记录 → UNKNOWN。
    """
    raw = (st.get("state") or "").upper()
    if raw == "UNKNOWN":
        return "UNKNOWN"
    if st.get("active"):
        return "PENDING" if raw == "PENDING" else "RUNNING"
    return "COMPLETED" if raw == "COMPLETED" else "FAILED"


def _task_state(state, task: dict) -> tuple[str, bool, dict | None]:
    """Calibrate one sr_task row against the scheduler.

    Returns (state, changed, fresh_row) — `fresh_row` 是这次**写回后**从库里读回的
    那一行（没写就是 None）。调用方必须拿它去答：传进来的 `task` 是写之前的快照，
    用它算/回显时间戳，第一次 GET 会给出写之前的值，与随后的 SSE 帧、下一次 GET
    都对不上。

    Caches the queue display state in state.task_cache and writes it back to
    sr_tasks.status on change, broadcasting a job_update frame. A task with no
    job_id (interrupted submit) has no scheduler record — surface its stored
    status verbatim and never invent a terminal state for it.

    比较基准是内存缓存，缓存缺失（sr-api 刚重启）回落到**库里存的状态**：不回落的
    话重启后每一行都被判成「状态变了」，于是每个已跑完的老行都被写回一次、updated_at
    被抬到「现在」，耗时列集体变成行龄（2026-09-18 实测：30 小时前跑完的行显示
    「30 时 00 分」）。回落之后，重启只补发真正变了的那些行。
    """
    task_id = task["task_id"]
    cache = state.task_cache
    if task["job_id"] is None:
        return (task.get("status") or "UNKNOWN").upper(), False, None
    prev = cache.get(task_id) or ((task.get("status") or "").upper() or None)
    try:
        # Terminal states come from the job's own verdict file, which lives in
        # the task's <DatarootLQ>/Debug/ — hence query_job_status(task=…)
        # rather than a bare slurm.job_status (§一 "C 方案").
        st = run_sr_svc.query_job_status(task["job_id"], task=task)
    except Exception:  # noqa: BLE001 — scheduler hiccup → keep last known
        return prev or "UNKNOWN", False, None
    state_name = _queue_state(st)
    if state_name == prev:
        return state_name, False, None
    cache[task_id] = state_name
    fresh = None
    try:
        # 本次运行的时间窗就钉在这两个转换点上：首次看到 RUNNING = 排队结束、真开始
        # 跑；看到终态 = 跑完。`prev != "RUNNING"` 让重排队（Slurm requeue）只以
        # 后一次 RUNNING 为起点，而重启后 prev 来自库里存的状态 —— 库已经是 RUNNING
        # 的（重启前就观测到过）不会被重新计时。
        fresh = state.store.set_sr_task_state(
            task_id, state_name,
            mark_started=state_name == "RUNNING" and prev != "RUNNING",
            mark_finished=state_name in _RUN_ENDED_STATES)
    except Exception:  # noqa: BLE001 — DB write must not break the list
        pass
    frame = {"type": "job_update", "task_id": task_id,
             "job_id": task["job_id"], "state": state_name,
             "prev_state": prev,
             "ok": state_name in ("PENDING", "RUNNING", "COMPLETED"),
             "error": None
             if state_name not in ("FAILED", "UNKNOWN")
             else f"state={state_name}"}
    # 帧必须带上这次写库的时间戳（updated_at + 耗时用的 started_at/finished_at）。
    # 只推 state 的话，客户端手上的还是上一次 GET 的快照，耗时列要么退化成「0 秒」
    # （2026-09-17 真机），要么停在上一次运行的数字上。写库成功才带：写失败时库里
    # 没变，凭本地时钟发一个只会让界面与库对不上。
    if fresh is not None:
        for col in ("updated_at", "started_at", "finished_at"):
            frame[col] = fresh.get(col)
    _broadcast(state, frame)
    return state_name, True, fresh


def _run_dataroot(task: dict) -> str | None:
    """The directory the job actually works in — its private copy when the
    sandbox is on, otherwise lq_path itself.

    Surfaced because the two differ: the queue row shows the path the user
    typed, but with SR_SANDBOX_ROOT set the product lands in the copy, so
    "where is my result" has to be answerable from the API. Never raises —
    the sandbox env may have changed since the task was created.
    """
    p = task.get("params") or {}
    try:
        sandbox = run_sr_svc.sandbox_scene_paths(p.get("lq_path"),
                                                 task.get("fingerprint"))
    except ValueError:
        return None
    return (sandbox or {}).get("scene") or p.get("lq_path")


def _task_view(state, task: dict) -> dict:
    """Project an sr_task row onto the /api/queue response shape (params subset)."""
    state_name, _, fresh = _task_state(state, task)
    if fresh is not None:
        task = fresh          # 刚写回的行：传进来那份是写之前的快照
    p = task.get("params") or {}
    return {
        "task_id": task["task_id"], "fingerprint": task["fingerprint"],
        "session_id": task["session_id"], "job_id": task["job_id"],
        "state": state_name,
        "params": {
            "lq_path": p.get("lq_path"),
            "mask_path": p.get("mask_path"),
            "sr_scale": p.get("sr_scale", 2),
            "suffix": p.get("suffix", ""),
            "gpu": p.get("gpu", 0),
            "cloud_limit": p.get("cloud_limit", 80),
            "delete_ori": p.get("delete_ori", False),
            "grid_align": p.get("grid_align", True),
        },
        "run_dataroot": _run_dataroot(task),
        "config_xml": task["config_xml"], "batch_script": task["batch_script"],
        "log_dir": task["log_dir"],
        "created_at": task["created_at"], "updated_at": task["updated_at"],
        # 本次运行的时间窗（首次观测到 RUNNING → 终态落库）。NULL = 没观测到开始
        # （整段运行期间 sr-api 不在场，或这一行是加这两列之前建的）—— 客户端显示
        # 「—」，不拿 created_at 顶替：那个值是**行**的生日，复用行会退化成行龄。
        "started_at": task["started_at"], "finished_at": task["finished_at"],
    }


# --------------------------------------------------------------------------
# 3.1 工具
# --------------------------------------------------------------------------
@router.get("/api/tools")
def list_tools_endpoint():
    return {"tools": [t.manifest_entry() for t in list_tools()]}


@router.post("/api/tools/{name}")
async def call_tool_endpoint(name: str, request: Request):
    tool = next((t for t in list_tools() if t.name == name), None)
    if tool is None:
        raise HTTPException(status_code=404, detail=f"未知工具：{name}")
    raw = await request.body()
    try:
        body = json.loads(raw or b"{}")
    except ValueError:
        raise HTTPException(status_code=400, detail="请求体须为合法 JSON 对象")
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="请求体须为 JSON 对象")
    try:
        return tool.run(**body)
    except Exception as e:  # noqa: BLE001 — 参数级错误
        raise HTTPException(status_code=400,
                            detail=f"参数错误：{type(e).__name__}: {e}") from e


# --------------------------------------------------------------------------
# 3.2 聊天
# --------------------------------------------------------------------------
def _session_or_404(state, session_id: str) -> dict:
    sess = state.store.get_session(session_id)
    if sess is None:
        raise HTTPException(status_code=404, detail=f"会话不存在：{session_id}")
    return sess


def _project_message(seq: int, wire: dict, name_of_call: dict) -> dict:
    """Map one wire message onto the frontend GET /messages projection."""
    role = wire["role"]
    if role == "user":
        return {"seq": seq, "role": "user", "content": wire.get("content", "")}
    if role == "assistant":
        tcs = [{"name": tc["function"]["name"],
                "arguments": loop_mod._parse_args(tc["function"]["arguments"])}
               for tc in wire.get("tool_calls") or []]
        return {"seq": seq, "role": "assistant",
                "content": wire.get("content"),
                "tool_calls": tcs or None}
    if role == "tool":
        try:
            payload = json.loads(wire["content"])
        except (TypeError, ValueError):
            payload = {"ok": False, "data": None, "error": str(wire["content"])}
        return {"seq": seq, "role": "tool",
                "tool_name": name_of_call.get(wire.get("tool_call_id")),
                "tool_call_id": wire.get("tool_call_id"),
                "content": payload.get("content") or payload.get("error"),
                "ok": bool(payload.get("ok"))}
    return {"seq": seq, "role": role}


@router.post("/api/chat/sessions", status_code=201)
def create_session(request: Request):
    return {"session_id": request.app.state.store.create_session()}


@router.get("/api/chat/sessions")
def list_sessions(request: Request):
    return {"sessions": request.app.state.store.list_sessions()}


@router.get("/api/chat/sessions/{session_id}/messages")
def get_messages(session_id: str, request: Request):
    state = request.app.state
    _session_or_404(state, session_id)
    wire_msgs = state.store.get_messages(session_id)
    # tool 行要还原工具名：先扫一遍建立 assistant tool_calls id → name 映射
    name_of_call = {}
    for wire in wire_msgs:
        for tc in (wire.get("tool_calls") or []):
            name_of_call[tc.get("id")] = tc["function"]["name"]
    projected = [_project_message(seq, wire, name_of_call)
                 for seq, wire in enumerate(wire_msgs, start=1)
                 if wire["role"] in ("user", "assistant", "tool")]
    sess = state.store.get_session(session_id)
    return {"session_id": session_id, "status": sess["status"],
            "messages": projected}


@router.post("/api/chat/sessions/{session_id}/messages")
async def chat_send(session_id: str, request: Request):
    """发一条用户消息，POST 即流：SSE 逐事件返回一个回合的全部产出。

    一请求一回合（run_loop 一次）；mock/真实 LLM 均在此执行。同一会话并发
    回合 → 409（前端据此禁用发送 + 显示"正在思考"）。会话不存在 → 404。
    """
    state = request.app.state
    _session_or_404(state, session_id)
    raw = await request.body()
    try:
        body = json.loads(raw or b"{}")
    except ValueError:
        raise HTTPException(status_code=400, detail="请求体须为合法 JSON 对象")
    content = body.get("content") if isinstance(body, dict) else None
    if not isinstance(content, str) or not content.strip():
        raise HTTPException(status_code=400, detail="content 必填且非空")

    lock = state.chat_locks.setdefault(session_id, asyncio.Lock())
    if lock.locked():                    # 无 await 间隙 → 单事件循环内原子
        raise HTTPException(status_code=409,
                            detail="会话正忙（同一会话已有回合在跑）")
    await lock.acquire()

    async def gen():
        q: asyncio.Queue = asyncio.Queue()
        loop = asyncio.get_running_loop()
        run_id = uuid.uuid4().hex

        def bridge(ev: dict):
            """on_step → SSE 事件；剥掉 loop 内部多余字段（turn），对齐 §3.2."""
            t = ev.get("type")
            out = {}
            if t == "tool_call":
                out = {"type": "tool_call", "name": ev["name"], "args": ev["args"]}
            elif t == "tool_result":
                out = {"type": "tool_result", "name": ev["name"],
                       "ok": ev.get("ok"), "data": ev.get("data"),
                       "error": ev.get("error")}
            elif t == "assistant":
                out = {"type": "assistant", "content": ev.get("content")}
            elif t == "error":
                out = {"type": "error", "error": ev.get("error")}
            if out:
                loop.call_soon_threadsafe(q.put_nowait, out)

        def thread_main() -> dict:
            try:
                return loop_mod.run_loop(
                    state.cfg, content, store=state.store,
                    session_id=session_id, resume=True, on_step=bridge)
            except Exception as e:  # noqa: BLE001 — 流上收尾，不让线程挂死
                bridge({"type": "error", "error": f"{type(e).__name__}: {e}"})
                return {"ok": False, "error": f"{type(e).__name__}: {e}",
                        "answer": None}
            finally:
                loop.call_soon_threadsafe(q.put_nowait, _SENTINEL)

        try:
            yield _frame({"type": "turn_start", "run_id": run_id,
                          "session_id": session_id})
            result_box: dict = {}
            worker = asyncio.create_task(asyncio.to_thread(
                lambda: result_box.update({"r": thread_main()})))
            while True:
                item = await q.get()
                if item is _SENTINEL:
                    break
                yield item if isinstance(item, str) else _frame(item)
            await worker
            r = result_box.get("r", {})
            if r.get("ok") and not r.get("error"):
                yield _frame({"type": "turn_done",
                              "content": r.get("answer"), "error": None})
            # 其它路径：run_loop 已通过 on_step 发过 error 帧，turn_done 不出现
        finally:
            lock.release()

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers=_sse_headers())


# --------------------------------------------------------------------------
# 3.3 共享任务队列
# --------------------------------------------------------------------------
#: <Suffix> validation and the default both live in the service module now
#: (run_sr_svc.SUFFIX_RE / .normalize_suffix): the agent tool has to apply the
#: exact same rules, and two copies would drift into two different task
#: fingerprints for the same logical submit.


def _leaf(path) -> str:
    """Last path component, on either separator.

    Array paths are POSIX and arrive POSIX-shaped, but this code also runs on
    the Windows dev machine (tests, local preview), where os.path.basename
    would still be right — splitting on both keeps it right either way.
    """
    return str(path).replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]


def _norm_dir(path) -> str:
    """Trailing-slash-insensitive, symlink-resolved form for path comparison."""
    return os.path.realpath(str(path).replace("\\", "/").rstrip("/") or "/")


def _mask_target_dir(body: dict) -> Path:
    """`POST /api/masks` 要往哪儿写掩码 —— 返回**输入影像**的绝对路径。

    两种入口，都要过白名单：

    * `lq_path`：盘阵上任意一个合法场景目录（手工行用；Windows 形态 `W:\\...`
      也吃）。**不要求 SR_SCENES_ROOT**，这正是"打开任意场景目录"的前提。
      路径本身要存在（kind="dir"）。
    * `scene_id`：库行的不透明 id，沿用旧语义（需要 SR_SCENES_ROOT）。

    两条路都收敛到 `scene_search.input_scene_path`，掩码文件名再由
    `mask_stem` 取 —— 目录是不是场景目录，判断只此一处。
    """
    lq_path = body.get("lq_path")
    if isinstance(lq_path, str) and lq_path.strip():
        try:
            d = ensure_allowed(to_posix_array_path(lq_path), kind="dir")
        except PathDeniedError as e:
            raise HTTPException(status_code=403,
                                detail=f"路径不可用：{e}") from e
        inp = scene_search.input_scene_path(d)
        if inp is None:
            cands = "、".join(c.name for c in scene_search.input_candidates(d))
            raise HTTPException(
                status_code=404,
                detail=f"不是合法场景目录：{d}"
                       f"（需含 <目录名>_meta.xml，且含 {cands} 之一）")
        return inp
    root = paths.scenes_root()
    if root is None:
        raise HTTPException(status_code=404,
                            detail="盘阵未配置（SR_SCENES_ROOT），无场景可掩码")
    scene_id = body.get("scene_id")
    if not isinstance(scene_id, str) or not scene_id:
        raise HTTPException(status_code=400, detail="scene_id 或 lq_path 必填")
    try:
        return paths.scene_id_to_abs(scene_id, root)
    except PathDeniedError as e:
        raise HTTPException(status_code=404, detail=f"场景不可访问：{e}") from e




def _norm_sr_params(body: dict) -> dict:
    """Normalize POST /api/queue body onto the run_sr param dict + validate.

    Mirrors tools/run_sr.run_run_sr exactly (same defaults / same key set), so
    a REST submit and a tool submit of identical params produce the SAME
    task_fingerprint — the idempotency layer is shared across both entries.
    That parity is enforced, not just asserted in prose: both entries default
    the suffix through run_sr_svc.normalize_suffix and a test pins the two
    fingerprints equal (tests/test_run_sr.py::TestEntryPointParity).
    (The two extra rules below — locked directory, derived mask — are REST-only:
    they belong to the human-facing submit form, not to the agent tool.)

    Three prototype rules from docs/planning/sr-minimal-prototype-plan.md §4.3:

    * **Locked directory.** With SR_LOCKED_DIR set, `lq_path` must be that
      directory. The prototype is pinned to one test scene dir; a typo in a
      free-text box would otherwise aim a job at another scene.
    * **Derived mask.** A submit that carries no `mask_path` gets
      `<lq_path>/<input image stem>_mask.tif` *if that file exists*, else 400.
      Never a silent full-image run — the operator would believe the mask
      applied. The stem comes from the scene's input image (SC scenes:
      `<dirname>.tif`; RC scenes: `PAN.tif`), not from the directory name —
      see services/scene_search.mask_stem.
    * **Non-empty suffix.** An omitted one resolves through
      `run_sr_svc.normalize_suffix` to the `<Suffix>` in the SR team's own
      config inside SR_BUNDLE_DIR (falling back to "sr"); an explicit one must
      match run_sr_svc.SUFFIX_RE. Empty is never passed through: it means
      "rename the input".
    """
    rt = sr_runtime()
    try:
        lq_path = str(body["lq_path"]).strip()
        sr_scale = int(body.get("sr_scale", 2))
        gpu = int(body.get("gpu", 0))
        cloud_limit = int(body.get("cloud_limit", 80))
        suffix = str(body.get("suffix") or "").strip()
    except (KeyError, TypeError, ValueError) as e:
        raise HTTPException(status_code=400, detail=f"参数非法：{e}") from e
    if not lq_path:
        raise HTTPException(status_code=400, detail="lq_path 必填")
    bad = run_sr_tool._bad_path(lq_path)
    if bad:
        raise HTTPException(status_code=400, detail=f"lq_path {bad}")
    # 归一化到盘阵 POSIX 绝对路径（`W:\...` 也吃）。与 agent 工具入口共用
    # pathguard.normalize_submit_path —— 两个入口对同一次提交必须算出同一个
    # task_fingerprint，否则幂等层失效、重复投作业。
    try:
        lq_path = normalize_submit_path(lq_path)
    except PathDeniedError as e:
        raise HTTPException(status_code=400, detail=f"lq_path 不可用：{e}") from e
    if rt.locked_dir and _norm_dir(lq_path) != _norm_dir(rt.locked_dir):
        raise HTTPException(
            status_code=400,
            detail=f"lq_path 已锁死为 {rt.locked_dir}（SR_LOCKED_DIR），不接受 {lq_path}")
    mask_path = body.get("mask_path")
    if mask_path:
        bad = run_sr_tool._bad_path(str(mask_path).strip())
        if bad:
            raise HTTPException(status_code=400, detail=f"mask_path {bad}")
        try:
            mask_path = normalize_submit_path(str(mask_path).strip())
        except PathDeniedError as e:
            raise HTTPException(status_code=400,
                                detail=f"mask_path 不可用：{e}") from e
    else:
        mask_path = derived_mask_path(lq_path)
        if not os.path.isfile(mask_path):
            raise HTTPException(
                status_code=400,
                detail=f"该目录缺少掩码文件：{mask_path}"
                       f"（掩码须与影像同目录、按输入影像命名 "
                       f"{mask_stem(lq_path)}_mask.tif）")
    try:
        suffix = run_sr_svc.normalize_suffix(suffix)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if sr_scale < 1:
        raise HTTPException(status_code=400, detail="sr_scale 必须 >= 1")
    if gpu < 0:
        raise HTTPException(status_code=400, detail="gpu 必须 >= 0")
    if not (0 <= cloud_limit <= 100):
        raise HTTPException(status_code=400, detail="cloud_limit 须在 0..100")
    # 原型期禁用（见 run_sr.DELETE_ORI_MSG）。在这里拦是为了给出 400 + 人话原因，
    # 而不是让它落到 submit 里变成 422 的 "ValueError: ..."。
    if body.get("delete_ori"):
        raise HTTPException(status_code=400, detail=run_sr_svc.DELETE_ORI_MSG)
    return {
        "lq_path": lq_path,
        "mask_path": mask_path,
        "sr_scale": sr_scale, "suffix": suffix, "gpu": gpu,
        "cloud_limit": cloud_limit,
        "delete_ori": bool(body.get("delete_ori", False)),
        "grid_align": bool(body.get("grid_align", True)),
        "options_yml": body.get("options_yml"),
    }


async def _json_body(request: Request) -> dict:
    raw = await request.body()
    try:
        body = json.loads(raw or b"{}")
    except ValueError:
        raise HTTPException(status_code=400, detail="请求体须为合法 JSON 对象")
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="请求体须为 JSON 对象")
    return body


@router.get("/api/queue")
def list_queue(request: Request):
    state = request.app.state
    tasks = [_task_view(state, t) for t in state.store.list_sr_tasks()]
    return {"tasks": tasks}


@router.post("/api/queue", status_code=201)
async def submit_queue(request: Request):
    state = request.app.state
    body = await _json_body(request)
    params = _norm_sr_params(body)
    try:
        data = run_sr_svc.submit_run_sr(params, store=state.store)
    except RuntimeError as e:
        msg = str(e)
        if "interrupted" in msg:
            raise HTTPException(
                status_code=409,
                detail="上次提交中断、job_id 未录——勿盲重试，请先核对调度器") from e
        raise HTTPException(status_code=422, detail=msg) from e
    except Exception as e:  # noqa: BLE001 — 其余一律生成/提交失败
        raise HTTPException(status_code=422,
                            detail=f"{type(e).__name__}: {e}") from e

    task = state.store.get_sr_task(run_sr_svc.task_fingerprint(params))
    if data.get("status") == "SUBMITTED":
        state_name = "SUBMITTING"              # 刚提交，等校准器/GET 推进
        state.store.set_sr_task_state(task["task_id"], "SUBMITTING")
        state.task_cache[task["task_id"]] = "SUBMITTING"
    else:                                       # RESUMED_ACTIVE / RESUMED_COMPLETED
        state_name, *_ = _task_state(state, task)
    out = {"task_id": task["task_id"], "job_id": data["job_id"],
           "status": data.get("status"), "state": state_name,
           "config_xml": data.get("config_xml"), "log_dir": data.get("log_dir")}
    for k in ("previous_state", "previous_exit_code", "previous_job_id"):
        if k in data:
            out[k] = data[k]
    # 就地写入提示（工作单 §4.2 最后一条）：没有沙箱时 SR 直接写 lq_path——输出 tif
    # 落在场景目录里，输入 tif 本身不改名也不删除（Suffix 恒非空、delete_ori 已禁用）；
    # 只有该目录里已存在同名输出时，SR 才把旧输出改名为 <输出名>_NOSR.tif 再覆盖
    # （util.writeTiff 的改名对象是输出路径上的同名文件，不是输入）。同一句话也写进
    # 作业日志（run_sr.build_batch_script 的 audit 段），两处都不能省。
    if run_sr_svc.sandbox_scene_paths(params["lq_path"],
                                      run_sr_svc.task_fingerprint(params)) is None:
        out["in_place"] = True
        out["notice"] = (
            f"输出目录 = 输入目录（{params['lq_path']}）：SR 就地把结果写成 "
            f"<输入名>_{params['suffix']}.tif，输入 tif 不改名也不删除；"
            "该目录里已存在同名输出时，旧输出先被改名为 <同名>_NOSR.tif 再覆盖")
    return out


@router.post("/api/queue/{task_id}/cancel")
def cancel_queue(task_id: int, request: Request):
    state = request.app.state
    task = state.store.get_sr_task_by_id(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"队列任务不存在：{task_id}")
    if task["job_id"] is None:
        raise HTTPException(status_code=400,
                            detail="该任务无 job_id（中断遗留），无法取消")
    # Which "scheduler" owns the job follows SR_EXECUTOR — the local executor
    # has its own process table; scancel knows nothing about it.
    if sr_runtime().executor == "local":
        cancelled = local_exec.cancel(task["job_id"])
    else:
        cancelled = slurm.cancel(task["job_id"])
    state_name, *_ = _task_state(state, task)
    if not cancelled and state_name not in _TERMINAL_STATES:
        # 200 + cancelled=false 会被当成"已经停下了"。走到这里说明本进程没有该 job
        # 的记录（sr-api 重启过），而子进程可能还在就地写 lq_path —— 操作员据此重新
        # 提交就会有两个 SR 进程同写一个目录。宁可给 409 也不要这个 200。
        script = os.path.basename(task.get("batch_script") or "") or "run_sr"
        raise HTTPException(
            status_code=409,
            detail=f"取消失败：本进程没有 job {task['job_id']} 的记录（sr-api 可能重启过），"
                   f"子进程可能仍在运行。先 `ps -ef | grep {script}` 确认，再手工 kill；"
                   "确认停止前不要重复提交同一目录")
    return {"task_id": task_id, "cancelled": cancelled, "state": state_name}


@router.get("/api/queue/events")
async def queue_events(request: Request):
    """SSE：订阅所有任务状态变化（lifespan 轮询 / GET 校准广播驱动）。"""
    state = request.app.state
    q: asyncio.Queue = asyncio.Queue()
    sub = _Subscriber(asyncio.get_running_loop(), q)
    state.subscribers.add(sub)

    async def gen():
        try:
            while True:
                yield await q.get()
        finally:
            state.subscribers.discard(sub)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers=_sse_headers())


# --------------------------------------------------------------------------
# 3.4 掩码落盘
# --------------------------------------------------------------------------
@router.post("/api/masks")
async def bake_mask(request: Request):
    state = request.app.state
    body = await _json_body(request)

    scene_abs = _mask_target_dir(body)

    try:
        W = int(body["W"])
        H = int(body["H"])
    except (KeyError, TypeError, ValueError):
        raise HTTPException(status_code=400, detail="W/H 必填且为整数")
    if W <= 0 or H <= 0:
        raise HTTPException(status_code=400, detail="W/H 须为正整数")
    polys_raw = body.get("polygons")
    if not isinstance(polys_raw, list) or not polys_raw:
        raise HTTPException(status_code=400,
                            detail="polygons 须为至少一个多边形")
    polygons: list[list[list[float]]] = []
    for poly in polys_raw:
        if isinstance(poly, dict):
            poly = poly.get("points", [])
        if not isinstance(poly, list) or len(poly) < 3:
            raise HTTPException(status_code=400,
                                detail="每个多边形须 ≥3 个顶点")
        pts = []
        for v in poly:
            if not (isinstance(v, (list, tuple)) and len(v) >= 2):
                raise HTTPException(status_code=400,
                                    detail="多边形顶点须为 [x, y]")
            pts.append([float(v[0]), float(v[1])])
        polygons.append(pts)

    out_dir = scene_abs.parent
    # 与 derived_mask_path 同源（同一个 mask_stem）：写出去的名字和提交时去找的
    # 名字必须是同一个，否则 RC（PAN.tif）场景写进去也白写。
    stem = mask_stem(out_dir)
    tif_path = out_dir / f"{stem}_mask.tif"
    txt_path = out_dir / f"{stem}_mask.txt"
    try:
        mask_svc.generate_mask(W, H, polygons, str(tif_path), str(txt_path))
    except Exception as e:  # noqa: BLE001 — 栅格化/写盘失败
        raise HTTPException(status_code=422,
                            detail=f"掩码生成失败：{type(e).__name__}: {e}") from e

    # 回给前端的一律是盘阵 POSIX 形态（与提交侧 _norm_sr_params 存进 params 的
    # 那个字符串逐字节相同）。这里若回宿主形态，同一份掩码在"写入响应"与
    # "提交时推导"两条路上就是两个字符串，前端拿哪个显示都对不上。
    lq_path = out_dir.as_posix()
    mask_path = derived_mask_path(lq_path)     # 与上面写出去的必然是同一份
    # Draft prefilled into the queue form — never an empty suffix (§4.3: the
    # output name would equal the input name and SR would rename the input).
    # The value comes from the SR team's own config (services/run_sr.py::
    # default_suffix), so the form shows the suffix the submit would get.
    draft = {"lq_path": lq_path, "mask_path": mask_path,
             "sr_scale": 2, "suffix": run_sr_svc.default_suffix(), "gpu": 0,
             "cloud_limit": 80, "delete_ori": False, "grid_align": True}
    return {"mask_path": mask_path, "mask_txt": txt_path.as_posix(),
            "lq_path": lq_path, "task_draft": draft}


# --------------------------------------------------------------------------
# 3.7 《待修复清单》写回
# --------------------------------------------------------------------------
#: 允许写回的后缀。本端点的语义是**覆盖** —— 不设后缀门就等于「盘阵上任何存在的
#: 文件都能被它改写」，一颗写错的 bug 足以盖掉 .tif 或 meta.xml。
_QC_SUFFIXES = (".txt",)
#: mtime 护栏容差（秒）：网络盘与 FAT 的时间戳粒度能到两秒。
_QC_MTIME_TOL = 2.0


def _fmt_mtime(ts) -> str:
    """护栏拒绝时给用户看的时间（拿不到就退化成原值）。"""
    try:
        return datetime.fromtimestamp(float(ts)).strftime("%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError, OSError, OverflowError):
        return str(ts)


def _write_atomic(dst: Path, data: bytes) -> None:
    """原子替换写：临时文件落在**同目录**（同文件系统才 replace 得动）→ 把原文件的
    权限位搬过来 → os.replace。

    不直接 `open(dst, "wb")` 就地写：这份 txt 是操作员一下午的标记，写到一半失败
    （磁盘满、进程被杀）会留下一个被截断的清单，那比写不进去严重得多。
    照 services/preview_jpg.py 那套（后端唯一的原子写先例）。

    ⚠️ 权限位能保留，**属主不能** —— 替换后文件归跑 API 的 nginx，nginx 无权 chown
    回去。质检那边还要直接改这份 txt 的话，清单所在目录得给组写权限（见 deploy/README.md）。
    """
    fd, tmp = tempfile.mkstemp(dir=str(dst.parent),
                               prefix=dst.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        # mkstemp 出来的是 0600，直接 replace 会把原文件的权限一起换掉
        os.chmod(tmp, stat.S_IMODE(os.stat(str(dst)).st_mode))
        os.replace(tmp, str(dst))
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


@router.post("/api/qclist/write")
async def write_qclist(request: Request):
    """把《待修复清单》整份原地写回盘阵（契约见 docs/planning/api-contract.md §3.7）。

    为什么不让浏览器写：File System Access API 在规范里是 `[SecureContext]` 标的，
    Chrome 只在 https / localhost 的页面上暴露它，而真机是 nginx `listen 80` 的
    `http://内网IP` —— 那条路在真机上永远走不通。后端本来就以 nginx 身份写盘阵
    （掩码、SR 产物、烘焙 JPG），改走它既能在 http 下工作，也顺带把 GBK 清单写回
    GBK 做对了（浏览器编不出 GBK，旧前端只能降级成 UTF-8+BOM）。

    请求侧被拒一律 400（detail 说清是哪一种），只有写盘本身的 OSError 是 422 ——
    前端只把 detail 原样显示，分类没有消费者，不照搬 §3.5 那套 400/403/404。
    """
    body = await _json_body(request)

    raw_path = body.get("path")
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise HTTPException(status_code=400, detail="path 必填")
    try:
        target = ensure_allowed(to_posix_array_path(raw_path), kind="file")
    except PathDeniedError as e:
        raise HTTPException(status_code=400,
                            detail=f"写回目标不可用：{e}") from e
    if target.suffix.lower() not in _QC_SUFFIXES:
        raise HTTPException(
            status_code=400,
            detail=f"只能写回 {'、'.join(_QC_SUFFIXES)} 文件：{target}")

    text = body.get("text")
    if not isinstance(text, str):
        raise HTTPException(status_code=400, detail="text 必填且为字符串")
    enc = body.get("encoding", "utf-8")
    if enc not in ("utf-8", "gbk"):
        raise HTTPException(
            status_code=400,
            detail=f"encoding 只支持 utf-8 / gbk（收到 {enc!r}）")

    # —— 护栏：清单在**导入之后**被别人改过就不写 ——
    # 质检那边一天里会更新好几版，照写会把他们的新行整段盖掉。前端传的是导入那个
    # File 的 lastModified；不带这个字段（curl / e2e）护栏自动跳过。
    mtime = body.get("mtime")
    if mtime is not None:
        if isinstance(mtime, bool) or not isinstance(mtime, (int, float)):
            raise HTTPException(status_code=400, detail="mtime 须为数字（秒）")
        try:
            disk_mtime = target.stat().st_mtime
        except OSError as e:
            raise HTTPException(status_code=422,
                                detail=f"读目标文件状态失败：{e}") from e
        if abs(disk_mtime - float(mtime)) > _QC_MTIME_TOL:
            raise HTTPException(
                status_code=400,
                detail=f"这份清单在导入之后被改过（盘阵上 {_fmt_mtime(disk_mtime)}，"
                       f"你导入的是 {_fmt_mtime(mtime)}）—— 可能质检那边又更新了一版，"
                       f"重新导入再同步")

    # —— 写前查权限 ——
    # 原子替换**只需要目录可写**（文件自己的 mode 拦不住 os.replace），所以两个都得
    # 显式查一遍；不查的话用户拿到的是 EACCES 原文，看不出是目录还是文件的问题。
    if not os.access(str(target.parent), os.W_OK):
        raise HTTPException(status_code=400,
                            detail=f"目录不可写：{target.parent}")
    if not os.access(str(target), os.W_OK):
        raise HTTPException(status_code=400, detail=f"文件不可写：{target}")

    try:
        data = text.encode(enc)
    except UnicodeEncodeError as e:
        raise HTTPException(
            status_code=400,
            detail=f"正文里有 {enc} 编不出来的字符（第 {e.start} 个字符起）—— "
                   f"要按 {enc} 写回就得先把那些字改掉") from e

    try:
        _write_atomic(target, data)
    except OSError as e:
        raise HTTPException(
            status_code=422,
            detail=f"写盘失败：{type(e).__name__}: {e} —— "
                   f"多为目录不可写或文件被占用") from e

    return {"path": target.as_posix(), "bytes": len(data), "encoding": enc}
