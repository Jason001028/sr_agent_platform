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
* 广播 = 进程内 set[subscriber]，每订阅者持 (注册时运行 loop, asyncio.Queue)；
  `_broadcast` 从任意线程用 call_soon_threadsafe 入队，断开的订阅者被丢弃。

说明：为保持单个请求内清晰，各同步端点的参数统一命名 `request: Request`，
运行时状态解包成局部 `state = request.app.state`（Starlette State）；helper
一律收 `state`，不再出现 app/Request/State 混用。
"""

from __future__ import annotations

import asyncio
import json
import uuid

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from backend.agent import loop as loop_mod
from backend.api import paths
from backend.api.paths import PathDeniedError
from backend.services import mask as mask_svc
from backend.services import run_sr as run_sr_svc
from backend.services import slurm
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


def _task_state(state, task: dict) -> tuple[str, bool]:
    """Calibrate one sr_task row against the scheduler; returns (state, changed).

    Caches the queue display state in state.task_cache and writes it back to
    sr_tasks.status on change, broadcasting a job_update frame. A task with no
    job_id (interrupted submit) has no scheduler record — surface its stored
    status verbatim and never invent a terminal state for it.
    """
    task_id = task["task_id"]
    cache = state.task_cache
    if task["job_id"] is None:
        return (task.get("status") or "UNKNOWN").upper(), False
    prev = cache.get(task_id)
    try:
        st = slurm.job_status(task["job_id"])
    except Exception:  # noqa: BLE001 — scheduler hiccup → keep last known
        return prev or "UNKNOWN", False
    state_name = _queue_state(st)
    if state_name == prev:
        return state_name, False
    cache[task_id] = state_name
    try:
        state.store.set_sr_task_state(task_id, state_name)
    except Exception:  # noqa: BLE001 — DB write must not break the list
        pass
    _broadcast(state, {"type": "job_update", "task_id": task_id,
                       "job_id": task["job_id"], "state": state_name,
                       "prev_state": prev,
                       "ok": state_name in ("PENDING", "RUNNING", "COMPLETED"),
                       "error": None
                       if state_name not in ("FAILED", "UNKNOWN")
                       else f"state={state_name}"})
    return state_name, True


def _task_view(state, task: dict) -> dict:
    """Project an sr_task row onto the /api/queue response shape (params subset)."""
    p = task.get("params") or {}
    state_name, _ = _task_state(state, task)
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
        "config_xml": task["config_xml"], "batch_script": task["batch_script"],
        "log_dir": task["log_dir"],
        "created_at": task["created_at"], "updated_at": task["updated_at"],
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
def _norm_sr_params(body: dict) -> dict:
    """Normalize POST /api/queue body onto the run_sr param dict + validate.

    Mirrors tools/run_sr.run_run_sr exactly (same defaults / same key set), so
    a REST submit and a tool submit of identical params produce the SAME
    task_fingerprint — the idempotency layer is shared across both entries.
    """
    try:
        lq_path = str(body["lq_path"]).strip()
        sr_scale = int(body.get("sr_scale", 2))
        gpu = int(body.get("gpu", 0))
        cloud_limit = int(body.get("cloud_limit", 80))
        suffix = str(body.get("suffix") or "")
    except (KeyError, TypeError, ValueError) as e:
        raise HTTPException(status_code=400, detail=f"参数非法：{e}") from e
    if not lq_path:
        raise HTTPException(status_code=400, detail="lq_path 必填")
    bad = run_sr_tool._bad_path(lq_path)
    if bad:
        raise HTTPException(status_code=400, detail=f"lq_path {bad}")
    mask_path = body.get("mask_path")
    if mask_path:
        bad = run_sr_tool._bad_path(str(mask_path).strip())
        if bad:
            raise HTTPException(status_code=400, detail=f"mask_path {bad}")
    if sr_scale < 1:
        raise HTTPException(status_code=400, detail="sr_scale 必须 >= 1")
    if gpu < 0:
        raise HTTPException(status_code=400, detail="gpu 必须 >= 0")
    if not (0 <= cloud_limit <= 100):
        raise HTTPException(status_code=400, detail="cloud_limit 须在 0..100")
    return {
        "lq_path": lq_path,
        "mask_path": str(mask_path).strip() if mask_path else None,
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
        state_name, _ = _task_state(state, task)
    out = {"task_id": task["task_id"], "job_id": data["job_id"],
           "status": data.get("status"), "state": state_name,
           "config_xml": data.get("config_xml"), "log_dir": data.get("log_dir")}
    for k in ("previous_state", "previous_exit_code", "previous_job_id"):
        if k in data:
            out[k] = data[k]
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
    cancelled = slurm.cancel(task["job_id"])
    state_name, _ = _task_state(state, task)
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

    root = paths.scenes_root()
    if root is None:
        raise HTTPException(status_code=404,
                            detail="盘阵未配置（SR_SCENES_ROOT），无场景可掩码")
    scene_id = body.get("scene_id")
    if not isinstance(scene_id, str) or not scene_id:
        raise HTTPException(status_code=400, detail="scene_id 必填")
    try:
        scene_abs = paths.scene_id_to_abs(scene_id, root)
    except PathDeniedError as e:
        raise HTTPException(status_code=404, detail=f"场景不可访问：{e}") from e

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
    stem = scene_abs.stem
    tif_path = out_dir / f"{stem}_mask.tif"
    txt_path = out_dir / f"{stem}_mask.txt"
    try:
        mask_svc.generate_mask(W, H, polygons, str(tif_path), str(txt_path))
    except Exception as e:  # noqa: BLE001 — 栅格化/写盘失败
        raise HTTPException(status_code=422,
                            detail=f"掩码生成失败：{type(e).__name__}: {e}") from e

    lq_path = str(out_dir)
    draft = {"lq_path": lq_path, "mask_path": str(tif_path),
             "sr_scale": 2, "suffix": "", "gpu": 0, "cloud_limit": 80,
             "delete_ori": False, "grid_align": True}
    return {"mask_path": str(tif_path), "mask_txt": str(txt_path),
            "lq_path": lq_path, "task_draft": draft}
