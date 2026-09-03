# 平台 API 契约（阶段5 · REST / SSE）

> 日期：2026-09-02 · 状态：**已定**（评审通过；实现已按本文档交付——后端 190 unittest + 前端 Vitest 114 + vue-tsc 零错误 + `.e2e/test-platform.js` 11 断言全绿。红线不变：后续改动契约仍须**先改本文档并置回「评审」**，通过后再改代码）
> 目标读者：阶段5 实现会话（后端 FastAPI + 前端 Vue3）。范围：把既有后端（agent loop + 4 工具 + `sr_tasks` + slurm）暴露成网页可调 REST/SSE，交付 聊天 / 共享任务队列 / 查看器画完掩码提交 SR。
> 前置：阶段4 已完成（FastAPI 骨架 `backend/api/app.py`：`/api/scenes` + `/api/scenes/{id}/preview` + 路径白名单；前端 `/scenes` 页 + route='jpg' rec + `/chat` `/queue` 占位路由）。

## 0. 一句话总结

后端 FastAPI 新增四组端点——**聊天**（会话 REST + 单回合 SSE）、**工具直调**、**共享任务队列**（REST 提交/取消 + SSE 状态广播）、**掩码落盘**；前端三个页面（聊天/队列/查看器掩码提交按钮）。离机可全测：mock LLM（`SR_LLM_MOCK=1`）+ 假调度器（`SR_SLURM_FAKE=1`）+ fake 场景回退，真模型/真 Slurm 项另排。

## 1. 通用约定

- **Base**：所有端点挂在既有 FastAPI app 上（`backend/api/app.py::create_app`，前缀 `/api`）。CORS 全放（内网直连 IP:端口；端口转发只改前端 `__SR_CFG__.apiBase`，同阶段4）。
- **鉴权**：内网无鉴权；`run_sr`/掩码的副作用由**幂等表 + 路径白名单**兜底，不做用户门控（M2 再议）。
- **请求/响应**：JSON（UTF-8）。错误统一 `{"detail": "<人话>"}` + HTTP 状态码；工具/命令层结果遵循 `{"ok": bool, "data": …, "error": str|null}`（复用 tools.contract 约定）。
- **错误码表**：

| 码 | 含义 |
|---|---|
| 200 | OK（GET 列表 / SSE 正常结束） |
| 201 | 创建成功（会话 / 队列任务） |
| 400 | 参数非法（缺字段、类型错、路径非绝对/含 `<fake>` 等） |
| 404 | 资源不存在（会话 / 队列任务 / 场景不可访问） |
| 409 | 会话正忙（同一会话已有回合在跑，串行拒绝）；或重复提交被幂等层判定"上次中断、勿盲重试" |
| 422 | 生成/提交失败（预览生成失败 / sbatch 失败 / slurm 不可用） |

- **SSE 帧**：每事件一行 `data: {json}\n\n`（UTF-8、`Cache-Control: no-cache`、`Content-Type: text/event-stream`、`X-Accel-Buffering: no`）。事件不含业务 id 后缀（MVP 无多路），前端按帧 JSON 的 `type` 分派。**所有事件共用首字段 `type`**，可向后扩展。
- **运行模式 env**（延续 config.py env 风格，进程启动即读）：

| env | 缺省 | 作用 |
|---|---|---|
| `SR_AGENT_DB` | `sr_agent.db` | SQLite 路径（chat 会话 + sr_tasks 同一库） |
| `SR_LLM_MOCK` | 未设 | `=1` 时聊天走假 LLM（§5.1），不连任何端点 |
| `SR_SLURM_FAKE` | 未设 | `=1` 时提交走内存假调度器（§5.2），无 sbatch 也能跑通 submit→status→cancel |
| `SR_SCENES_ROOT` | 未设 | 盘阵场景根；未设 → fake 回退（阶段4 已有） |
| `SR_LLM_BASE_URL/API_KEY/MODEL/…` | 见 config.py | 真模型时沿用既有 loop 配置 |

## 2. 端点清单总览

| 方法 路径 | 用途 | 章节 |
|---|---|---|
| `GET /api/health` | 存活 + 数据源（已有，返回 `{ok,source}`） | — |
| `GET /api/scenes` · `GET /api/scenes/{id}/preview` | 场景检索/懒生成（阶段4 已有，不改） | — |
| `GET /api/tools` | 工具清单（manifest 机械生成，供 UI/文档） | 3.1 |
| `POST /api/tools/{name}` | 直调单个工具（绕过 LLM；validate + 白名单照常） | 3.1 |
| `POST /api/chat/sessions` | 新建会话 → `201 {session_id}` | 3.2 |
| `GET /api/chat/sessions` | 会话列表（新建入口 + resume 用） | 3.2 |
| `GET /api/chat/sessions/{id}/messages` | 历史消息（刷新可恢复渲染） | 3.2 |
| `POST /api/chat/sessions/{id}/messages` | 发一条用户消息 → **SSE**（一请求一回合） | 3.2 |
| `GET /api/queue` | 队列列表（sr_tasks 全量 + 实时状态） | 3.3 |
| `POST /api/queue` | 提交 SR 作业（表单=run_sr 参数，幂等） | 3.3 |
| `POST /api/queue/{task_id}/cancel` | scancel 取消 | 3.3 |
| `GET /api/queue/events` | SSE：队列状态变化广播 | 3.3 |
| `POST /api/masks` | 多边形 JSON + W/H → 栅格化写盘阵（原图目录）→ `{mask_path, lq_path, task_draft}` | 3.4 |

## 3. 端点细节

### 3.1 工具

`GET /api/tools` → `200 {"tools": [{name, description, parameters, …}, …]}`（`contract.manifest()` 展平后返回）。

`POST /api/tools/{name}` — body = 工具参数 JSON 对象（键名 = JSON Schema properties 键）。校验并调 `contract` 注册的 `run`，**永远返回 `{"ok":bool,"data":…,"error":…}`**，HTTP 恒 200（ok=false 是业务结果不是传输错误），仅参数无法解析时 400。未知工具 404。直调不落会话、不走 LLM。
> run_sr / search_scenes / sr_job_status / fix_bad_lines 四者均可用本端点单测；前端"试跑"暂不提供入口（保留接口）。

### 3.2 聊天（Agent 对话，mock LLM MVP）

**会话模型**：单会话 + 「新建」按钮。会话 = `store.sessions` 一行；消息 = OpenAI wire 格式落 `store.messages`（与 `loop.run_loop` 持久化同一套，天然支持 resume）。前端刷新 → `GET messages` 恢复历史；切回旧会话 → `POST messages` 时服务端按 `resume=True` 续接（loop 既有语义：崩溃留下的未完成回合以"结果未知勿盲重试"修复）。

- `POST /api/chat/sessions` → `201 {"session_id": "…hex"}`（`store.create_session()`）。
- `GET /api/chat/sessions` → `200 {"sessions":[{session_id, created_at, updated_at, status}]}`（按 updated_at 倒序）。
- `GET /api/chat/sessions/{id}/messages` → `200 {"session_id", "status", "messages":[…]}`；消息对前端投影为：
  `{seq, role, content?, tool_calls?:[{name,arguments}] | null, tool_name?, ok?}`（role ∈ user/assistant/tool；tool 消息带 `name`/`ok`）。404 = 会话不存在。
- `POST /api/chat/sessions/{id}/messages` — body `{"content":"<用户话术>"}`，**`Content-Type: text/event-stream`，POST 即流**。行为：
  1. 会话存在且无并发回合 → 建占位（可复用 loop 的 `store.set_session_status(…,"active")`）→ 上锁；
  2. 线程池跑 `run_loop(cfg, content, store=…, session_id=…, resume=True, on_event=bridge)`，事件经 `asyncio.Queue` 转 SSE 帧逐条下发（`emit` 缝见 §4.2）；
  3. 结束：`turn_done`（带最终文本），**之后关流并释放会话锁**。LLM 调用/循环异常 → `error` 事件 + `HTTP` 结束后前端据 `type:"error"` 渲染失败态（SSE 已开不可改 HTTP 码）。
  - **并发**：同一 session 同时两个回合 → 立即 `409 {"detail":"会话正忙"}`（前端据此禁用发送按钮 + 显示"正在思考"）。
  - 会话不存在 → 404。`content` 空 → 400。
- **前端注意**：`EventSource` 只支持 GET，故用 `fetch`（method POST）读 `response.body`（ReadableStream）手工切 SSE 帧。

**SSE 事件 schema（聊天）**——每帧一行 JSON：

```json
{"type":"turn_start","run_id":"<uuid>","session_id":"<hex>"}
{"type":"tool_call","name":"search_scenes","args":{...}}     // assistant 回合声明调用工具
{"type":"tool_result","name":"search_scenes","ok":true,"data":{...}}
{"type":"assistant","content":"已检索到 N 个场景：…"}          // 纯文本中间回复（可选）
{"type":"turn_done","content":"最终回复文本","error":null}
{"type":"error","error":"<msg>"}                              // LLM 失败/循环超限，turn_done 不出现
// 预留 token 流式：{"type":"token","delta":"…"} 加入后前端按 token 渲染（MVP 不实现）
```

### 3.3 共享任务队列（仅 SR 作业 · 服务端唯一事实源）

**数据模型**：`store.sr_tasks`（阶段4 幂等表，既有列 id/fingerprint/job_id/status/params/config_xml/batch_script/log_dir/时间）。`status` 列**语义升级**为"队列展示状态"（阶段5 后台校准器写入，见下）；幂等层不读它（只读 job_id），无回归风险。需给 store 补 `list_sr_tasks()`。

**队列状态机**（Slurm 无百分比 → 状态机即进度）：

```
submit_run_sr 返回 → 队列状态：
  SUBMITTING     刚提交（job_id 已录，尚未校准）
  └─ 校准（slurm.job_status）：
       squeue PENDING            → PENDING
       squeue RUNNING/其它 active → RUNNING
       sacct  COMPLETED          → COMPLETED
       sacct  FAILED/CANCELLED/TIMEOUT/OOM… → FAILED
       sacct 无记录              → UNKNOWN（保留，可重跑）
```

- `GET /api/queue` → `200 {"tasks":[{task_id, fingerprint, session_id, job_id, state, params:{lq_path,mask_path,sr_scale,suffix,gpu,cloud_limit,delete_ori,grid_align}, config_xml, batch_script, log_dir, created_at, updated_at}, …]}`，按 created_at 倒序。`state` 取内存最近校准结果（缓存），无缓存则当场校准一次（squeue/sacct，镜像 `slurm.job_status`）。
- `POST /api/queue` — body（run_sr 参数，`lq_path` 必填，其余带默认）：
  `{lq_path, mask_path?, sr_scale?=2, suffix?="", gpu?=0, cloud_limit?=80, delete_ori?=false, grid_align?=true, options_yml?}`。
  直接调 `services.run_sr.submit_run_sr(params, store=default_store())`（幂等层既在：重复同参 → RESUMED_ACTIVE/COMPLETED 复用，失败才重跑，中断无 job_id → 409 报"勿盲重试"）。返回：
  `201 {"task_id", "job_id", "status":"SUBMITTED"|"RESUMED_ACTIVE"|"RESUMED_COMPLETED"|…, "state":…, "previous_state"?, "config_xml", "log_dir"}`。
  校验：`lq_path` 必填 + 绝对路径 + 不含 `<fake>`（复用 tools/run_sr `_bad_path` 语义）；mask_path 同规则；sbatch 不可用且未开 fake → 422「slurm not available … 需在盘阵机配置」。
- `POST /api/queue/{task_id}/cancel` → 查 task（404 无），`job_id` 非空则 `slurm.cancel` → `200 {"task_id","cancelled":bool,"state":…}`；无 job_id（中断遗留）→ 400。
- `GET /api/queue/events` — SSE：订阅所有任务的 `state` 变化。**驱动** = app 生命周期后台 asyncio 任务（§4.3）：周期（`SR_QUEUE_POLL_SEC`，缺省 2s）对每个 `job_id` 非空 task 调 `slurm.job_status`，状态与前值不同 → 更新内存缓存 + 写回 `sr_tasks.status` + 广播一帧。事件 schema：

```json
{"type":"job_update","task_id":3,"job_id":12345,"state":"RUNNING","prev_state":"PENDING","ok":true,"error":null}
{"type":"job_update","task_id":3,"job_id":12345,"state":"FAILED","prev_state":"RUNNING","ok":false,"error":"exit 1"}
// 心跳（可选，防代理断链）：{"type":"ping"}
```

### 3.4 掩码（查看器 → SR 提交）

`POST /api/masks` — body：
```json
{"scene_id":"<阶段4 不透明 scene id>",
 "polygons":[{"label":"roi_1","points":[[x,y],…]}, …],
 "W": 24739, "H": 24199}
```
- **路径语义（已与用户确认）**：服务端按 `scene_id` 经 `paths.scene_id_to_abs` 解析回**场景文件绝对路径**（fake/越界 → 404/400，白名单照旧）。掩码落盘到**该场景文件所在目录**：`<原图目录>/<stem>_mask.tif`（0/255 Deflate，`services.mask.write_mask_tif`）+ `<stem>_mask.txt`（`write_mask_centroid_txt` 参考格式）。超分结果 `.tif` 也落同目录（run_sr/0817 脚本自身行为，本端点不处理）。
- 复用 `services.mask`：`rasterize_polygons(W,H, polygons→points)`（全分辨率 Pillow；24739×24199 ≈ 580MB 位图，盘阵机内存可扛，**不落浏览器**）。W/H 以 body 为准（= 元数据 W/H，不探 TIF）。
- 返回 `200 {"mask_path": "<绝对路径>", "mask_txt": "<绝对路径>", "lq_path": "<原图所在目录>", "task_draft": {lq_path, mask_path, …默认 sr 参数}}`。`task_draft` 供前端**预填**队列表单（不自动提交，Slurm 是真副作用；用户点提交才发 POST /api/queue）。
- 幂等：同 scene_id 重复提交 → 覆盖同名掩码（掩码生成无副作用风险，允许重复）。

## 4. 关键实现机制（契约约束）

### 4.1 会话串行 + 线程桥
- 进程内 `dict[session_id → asyncio.Lock]`；锁占用期间再 POST → 409。
- `run_loop` 是同步阻塞函数 → `asyncio.to_thread` 跑；内部事件经回调推 `asyncio.Queue`，主协程 `while` 逐帧 `yield` 成 SSE（`StreamingResponse`）。

### 4.2 loop 观察缝（新增 `on_step`，**不改变行为**）
`run_loop(…, on_step: Callable[[dict], None] | None = None)`：在既有各 return/commit 点**同步调用** `on_step({"type":…, …})`，事件与 SSE schema 对齐（turn_start 在循环首轮前、tool_call/tool_result 在 call_tool 前后、assistant 在纯文本答复时、turn_done/error 在返回前）。默认 `None` → 与现状逐字节一致，**18 个既有 loop 测试不因本改动变更**。mock LLM 只替换 `_default_chat`，不动状态机。

### 4.3 队列后台校准广播
- app lifespan 启动一个 asyncio 任务：`while` sleep `SR_QUEUE_POLL_SEC`，扫 `sr_tasks` 中 `job_id IS NOT NULL` 的行 → `slurm.job_status` → 状态变化时更新 `sr_tasks.status` + 内存缓存 + 广播。
- 广播 = 进程内 `set[asyncio.Queue]`（每 `/api/queue/events` 连接一个）；写入失败（连接断开）→ 丢弃该连接。**单进程部署**（uvicorn 单 worker），多 worker 需外部队列——文档注明（M2 再议）。

### 4.4 测试隔离
- 测试里 `create_app()` 前设 env（`SR_AGENT_DB=临时文件`、`SR_SCENES_ROOT=临时目录`、`SR_LLM_MOCK=1`、`SR_SLURM_FAKE=1`），与阶段4 test_api.py 同款模式。

## 5. 假实现规格（离机验收基准）

### 5.1 mock LLM（`SR_LLM_MOCK=1`）
`_default_chat` 返回假 `chat(messages, tools)`，**固定脚本**、不解析用户话术：
1. 首个回合：声明 tool_call `search_scenes`（无参数，走既有 fake/真实盘阵后端，返回真结果/假结果皆可）；
2. 收到工具结果后：回最终回复，内容 = 「已检索盘阵场景 N 个（mock 模型）。」+ 前几个场景 id 摘要。
确定性 → SSE 每回合固定产出 `turn_start/tool_call/tool_result/turn_done` 全套事件，e2e 可断言。**不替换状态机**（loop.py 行为由既有测试锁住）。

### 5.2 假调度器（`SR_SLURM_FAKE=1`）
`services/slurm.py` 顶层加一个假实现门：无 sbatch（或 env 强制）时 `sbatch_submit` 返回自增 job_id 并登记，`squeue_status`/`sacct_status` 按**可配延时推进状态**（`SR_SLURM_FAKE_T_MS`，缺省 ~1200ms 后 PENDING→RUNNING→COMPLETED，exit 0），`scancel` 置 CANCELLED/FAILED。`config.xml`/batch 脚本/sr_tasks 表照常落盘（校验路径、幂等、写表全走真实逻辑）——只替换"调度器"那一层。镜像阶段4 场景 fake 的既有风格（env 门 + 注释明示 fake）。

### 5.3 契约测试（本轮验收）
- 后端 pytest：chat 新建/历史/SSE 事件序列（mock LLM）/会话并发 409/不存在 404；tools manifest + 直调 run_sr（fake slurm）幂等；queue list/submit(SUBMITTED)/重复提交(RESUMED_*)/cancel/events 广播状态推进；masks 落原图目录 + 白名单越界 404 + fake 场景 404。
- 前端 Vitest：SSE 帧解析器；chat/queue store 的乐观更新与事件归并。
- `.e2e`：真 uvicorn（`SR_LLM_MOCK=1 SR_SLURM_FAKE=1` + 临时 db + 临时 scenes root）驱动前端——聊天发一条 → SSE 全事件 → 消息渲染；队列提交一张假任务 → events 推到 COMPLETED；查看器掩码提交按钮 → POST /api/masks → 表单预填。本地文件路径回归（route 非 jpg）零改动仍绿。
- **真机另排清单**（不阻塞离机）：真 LLM `SR_LLM_*` 指向内网端点、真 Slurm 提交/取消、真实盘阵掩码落点核对（ENVI 打开确认掩码与 0817 消费路径一致）。

## 6. 部署注意（nginx 反向代理 SSE）

- `/api/` 反代需对 SSE 端点禁用缓冲 + 放宽读超时：
  `location /api/ { proxy_pass …; proxy_buffering off; proxy_cache off; proxy_read_timeout 3600s; proxy_set_header Connection ''; }`（阶段4 nginx.conf 已 `proxy_read_timeout 600s`，需加 buffering off；仅对 `StreamingResponse` 生效，普通 JSON 不受影响）。
- systemd `sr-api.service` 增补 env：`SR_AGENT_DB=/DiskArray/…/sr_agent.db`、`SR_LLM_MOCK=0`、`SR_SLURM_FAKE=0`（真机显式关 fake，防误开）。
- `requirements-api.txt` 增补 `openai>=1.40,<2`（阶段5 起 API 进程直接 import loop → openai；**版本锁死**，aiohttp 3.8.3 不兼容 3.x）。

## 7. 变更流程（红线）

契约改动（新增/删端点、改请求/响应/SSE 事件字段、改状态机映射）→ **先改本文档、标注状态=评审，评审通过再改代码**。前端 SSE 渲染与后端推送共用本 schema，两侧各写一份 parser/emitter 的单元测试锁字段。
