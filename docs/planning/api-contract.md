# 平台 API 契约（阶段5 · REST / SSE）

> 日期：2026-09-02 · 状态：**评审**（2026-09-16 按 §7 红线置回，待复核后改回「已定」）
>
> **挂起项一**：§3.3 新增「`suffix` 默认值来源」条款——省略/留空不再固定为内置 `"sr"`，改为读 `$SR_BUNDLE_DIR` 下 SR 团队配置文件里的 `<Suffix>`，`SR_SUFFIX_DEFAULT` 环境变量作废；同批把 agent 工具 `run_sr` 的归一化与 REST 入口对齐（此前工具既不 strip 也不给默认值，同一逻辑提交两入口指纹不同 → 幂等失效、重复投作业，属修缺陷）。
> **挂起项二（2026-09-17）**：新增 §3.5 `POST /api/scenes/resolve`（打开盘阵上 `SR_SCENES_ROOT` 之外的任意合法场景目录），并改 §3.4 `POST /api/masks` 的 body（新增 `lq_path`，legacy `scene_id` 保留）。同时约定 `lq_path` / `dir` / `input` / `mask_path` 一律回**盘阵 POSIX 形态**、提交侧两入口共用 `pathguard.normalize_submit_path`——这两条不改行为口径，只是把"同一场景两种写法算出两个指纹"的隐患收口。
> **须说明的流程偏差**：上述改动**已与本文档同批落到代码**（不是"先评审后写码"）。理由是它同时修一个现存缺陷（两入口指纹不一致），拆开会让仓库停在一个已知会重复投作业的中间态；09-17 那批同理，前端要用的字段与端点不一起落地就没法验收。请复核，通过后把状态改回「已定」。此前其余条款自 2026-09-02 起均未变（评审通过时的交付基线：后端 190 unittest + 前端 Vitest 114 + vue-tsc 零错误 + `.e2e/test-platform.js` 11 断言全绿）。
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
| `POST /api/scenes/resolve` | 手填/反推一个盘阵场景目录 → 与库行同形的 `{source,row,resolved}` | 3.5 |
| `GET /api/scenes/{id}/preview-tmp` | **拖拽入口专用**的临时预览 JPG（独立缓存根、1 天 TTL、每日 0 点清；不进库行，URL 不可静态映射） | 3.6 |
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
| `POST /api/masks` | 多边形 JSON + W/H → 栅格化写盘阵（原图目录）→ `{mask_path, lq_path, task_draft}`（body 现收 `lq_path`，legacy `scene_id` 保留） | 3.4 |

> **SR 最小原型（09-14）**：掩码来源改为「目录里已有的 `<输入影像名>_mask.tif`」，
> 提交时只带 `lq_path`，由后端推导并校验存在性
> （`services/scene_search.derived_mask_path`）；`POST /api/masks` 退居补充出口。
> **2026-09-17 恢复调用**：90% 的生产场景本来就没有掩码，查看器里现场画完要能直接
> 写回服务端场景目录，所以 `apiBakeMask` / `MaskBakeResult` 重新进 `lib/api.ts`
> （body 见 3.4），随后提交仍走同一条推导 —— 两条路写出去/找回来的是同一个文件名，
> 由后端 `scene_search.mask_stem` 单点保证。
> `POST /api/queue` 的响应另加两个**只读提示字段**（没有沙箱时出现，否则整个字段缺省）：
> `in_place: true` + `notice`（人话：「输出目录 = 输入目录（…）：SR 就地把结果写成
> `<输入名>_<suffix>.tif`，输入 tif 不改名也不删除；该目录里已存在同名输出时，旧输出先被改名为
> `<同名>_NOSR.tif` 再覆盖」）。
> 同一层意思也以 WARNING 写进作业日志的 audit 段（`build_batch_script`），两处都不能省——日志是
> 事后追责时唯一会去看的东西。
> 同时 `GET /api/scenes` 的扫描后缀扩到 `.jpg/.jpeg`（`scene_search._IMAGE_EXTS`）：
> 盘阵里本就是显示就绪图的 JPG 也作为场景行列出，行内 `hasPreview` 恒 `true`、
> `jpgUrl` **指向源文件本身**（前端因此跳过 `/api/scenes/{id}/preview` 懒生成），
> W/H 由 Pillow 读头得到；`GET …/preview` 对这种行直接回源字节。
> **收件规则是白名单（2026-09-16 改，取代此前逐条排除派生件的黑名单）**：
> `scene_search.is_scene_file()` 收一个文件，当且仅当两条同时成立——
> ① 所在目录是**场景目录**（内有 `<目录名>_meta.xml`，`is_scene_dir()`；SR 脚本本来就靠
> 它判 RC/SC，没有它的目录提交也跑不起来）；② 文件名 = `<目录名>.<ext>`（SC 步骤的输入）
> 或 `PAN.<ext>`（RC 步骤的输入）。于是这些一律不再入列表：SR 产物与输入备份
> （`_sr` / `_NOSR` / `_ori`）、云量图（`_cloud`）、缩略图（`_thumb`）、提交 SR 的输入掩膜
> （`_mask`）、后端自己烘焙的 `<basename>.preview.jpg` 缓存、`Debug/` 下的调试图。
> 旧规则每冒出一类派生件就得补一条：真机接上盘阵后 18 行里 16 行是脏数据。
> **代价**：没有 `<目录名>_meta.xml` 的目录整个不显示。
>
> **阶段6 增补（查看器上下文侧舱）**：`GET /api/scenes` 的 disk 行新增只读字段
> `lq_path` = 该 scene 文件**父目录**的绝对路径（= 场景目录，`run_sr` 的目录语义 lq_path；
> fake 行恒 `null`）。作用：前端把 `/api/queue` 行按 `params.lq_path` 相等 +
> `params.mask_path` 以 `<stem>_mask.tif` 结尾关联回当前 scene，做「当前场景最近任务」
> 展示。字段只读、不含文件名；viewer 任务区以外的页面不消费它。

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

**数据模型**：`store.sr_tasks`（阶段4 幂等表，既有列 id/fingerprint/job_id/status/params/config_xml/batch_script/log_dir/时间，2026-09-18 增 `started_at`/`finished_at` 本次运行时间窗 —— 老库打开时 `store._ensure_columns` 用 ALTER TABLE 补列、不回填）。`status` 列**语义升级**为"队列展示状态"（阶段5 后台校准器写入，见下）；幂等层不读它（只读 job_id），无回归风险。需给 store 补 `list_sr_tasks()`。

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

- `GET /api/queue` → `200 {"tasks":[{task_id, fingerprint, session_id, job_id, state, params:{lq_path,mask_path,sr_scale,suffix,gpu,cloud_limit,delete_ori,grid_align}, config_xml, batch_script, log_dir, created_at, updated_at, started_at, finished_at}, …]}`，按 created_at 倒序。`state` 取内存最近校准结果（缓存），无缓存则当场校准一次（squeue/sacct，镜像 `slurm.job_status`）。
  **两组时间戳别混**（2026-09-18 增 `started_at`/`finished_at`，见下「耗时」）：`created_at` = 这一行**第一次**提交的时刻（同一指纹重交复用同一行，不刷新）、`updated_at` = 最近一次写回，两者属**行**；`started_at` = 校准器首次观测到 RUNNING 的时刻（排队结束）、`finished_at` = 终态落库时刻，两者属**本次运行**。本次运行的起点/终点都没观测到就是 `null`（界面「—」），**不**退回 `created_at` 顶替。
- `POST /api/queue` — body（run_sr 参数，`lq_path` 必填，其余带默认）：
  `{lq_path, mask_path?, sr_scale?=2, suffix?="", gpu?=0, cloud_limit?=80, delete_ori?=false, grid_align?=true, options_yml?}`。
  直接调 `services.run_sr.submit_run_sr(params, store=default_store())`（幂等层既在：重复同参 → RESUMED_ACTIVE/COMPLETED 复用，失败才重跑，中断无 job_id → 409 报"勿盲重试"）。返回：
  `201 {"task_id", "job_id", "status":"SUBMITTED"|"RESUMED_ACTIVE"|"RESUMED_COMPLETED"|…, "state":…, "previous_state"?, "config_xml", "log_dir"}`。
  校验：`lq_path` 必填 + 绝对路径 + 不含 `<fake>`（复用 tools/run_sr `_bad_path` 语义）；mask_path 同规则；sbatch 不可用且未开 fake → 422「slurm not available … 需在盘阵机配置」。
  **`suffix` 的默认值来源（2026-09-16 改，见 §7 评审挂起）**：body 省略或留空 → 平台读 `$SR_BUNDLE_DIR` 下 SR 团队自己的配置文件（`sfsr_confgig_test_espan2_cuda1.xml`，兼容另一种拼写 `sfsr_config_…`）里的 `<Suffix>` 作默认值；文件缺失 / 坏 XML / 标签缺失或为空 / 值不合白名单 → 回落到内置 `"sr"`。**每次提交现读**，不缓存（`task_fingerprint` 把 `suffix` 文本哈希在内，缓存会让指纹变成进程启动时刻的函数 → 重启或双 worker 对同一逻辑提交得出不同指纹，正是幂等层要防的重复投作业）。显式传值仍走白名单 `^[A-Za-z0-9_-]{1,16}$`，不合法 400（`detail` 含「suffix 非法」）。**agent 工具 `run_sr` 共用同一套归一化**（`services/run_sr.py::normalize_suffix`），同一逻辑提交在两入口得到同一 `task_fingerprint`。原 `SR_SUFFIX_DEFAULT` 环境变量同日作废，设了不读。
- `POST /api/queue/{task_id}/cancel` → 查 task（404 无），`job_id` 非空则 `slurm.cancel` → `200 {"task_id","cancelled":bool,"state":…}`；无 job_id（中断遗留）→ 400。
- `GET /api/queue/events` — SSE：订阅所有任务的 `state` 变化。**驱动** = app 生命周期后台 asyncio 任务（§4.3）：周期（`SR_QUEUE_POLL_SEC`，缺省 2s）对每个 `job_id` 非空 task 调 `slurm.job_status`，状态与前值不同 → 更新内存缓存 + 写回 `sr_tasks.status` + 广播一帧。事件 schema：

```json
{"type":"job_update","task_id":3,"job_id":12345,"state":"RUNNING","prev_state":"PENDING","ok":true,"error":null,"updated_at":1789000000.12,"started_at":1789000000.12,"finished_at":null}
{"type":"job_update","task_id":3,"job_id":12345,"state":"FAILED","prev_state":"RUNNING","ok":false,"error":"exit 1","updated_at":1789000060.5,"started_at":1789000000.12,"finished_at":1789000060.5}
// 心跳（可选，防代理断链）：{"type":"ping"}
```

`updated_at`（2026-09-17 增）、`started_at`/`finished_at`（2026-09-18 增）= 这次写回 `sr_tasks`
的值，与 `GET /api/queue` 同名字段同源。它们必须随帧下发：客户端手上那份只来自 `GET`，而那次
GET 通常就发生在提交刚落库之后（两列时间窗还是 `NULL`）—— 只推 `state` 的话，任务一完成耗时列
就从运行中的正常值掉成「0 秒」（2026-09-17 真机）。写库失败时这些字段**缺席**（不发本地时钟值），
客户端保留旧快照，与 GET 的读数保持一致；因此客户端须按「字段可能不存在」实现。

**耗时（队列页那一列）**：`finished_at − started_at`，运行中的行用浏览器时钟现算。两个锚点都钉在
**真实转换点**上（首次看到 RUNNING / 看到 COMPLETED·FAILED），所以量的是**本次运行**、不含排队。
两个坑各自对应一次真机现象，改的时候别退回去：

- **不能拿 `created_at` 当起点**。一行 = 一个指纹，同一场景同一参数重交时幂等层复用同一行
  （`run_sr._resolve_existing`），`created_at` 停在**第一次**提交的时刻 —— 于是「耗时」量的是行龄。
  真机表现：昨天失败的那次重交后跑完，「耗时」显示「30 时 00 分」，实际只跑了 200 多秒
  （2026-09-18）。
- **重启（校准缓存为空）不能重算**。`state.task_cache` 是内存态，校准器原来只拿它当比较基准：
  sr-api 一重启，每行都被判成「状态变了」→ 全部写回 + 刷新时间戳，几天前跑完的行集体变行龄。
  现在基准缺失时回落到 `sr_tasks.status`（库里存的状态），只补发真正变了的行（2026-09-18）。
- 本次运行的起点没观测到（整段运行期间 sr-api 不在）→ `started_at` 为 `null`，界面显示「—」。
  概率低（校准时距 2s），且**不猜**：退回 `created_at` 就是把上面第一个坑请回来。
- 加这两列前建的行不回填，其耗时也显示「—」（`store._ensure_columns` 只补列不改数据）。

### 3.4 掩码（查看器 → SR 提交）

`POST /api/masks` — body（`lq_path` 与 `scene_id` 二选一，都给时 `lq_path` 优先）：
```json
{"lq_path":"/DiskArray/GSHC2IMPS/PRODUCT/2026/09/17/<生产编号>",
 "polygons":[{"label":"roi_1","points":[[x,y],…]}, …],
 "W": 24739, "H": 24199}
```
或 legacy 形态 `{"scene_id":"<阶段4 不透明 scene id>", "polygons":…, "W":…, "H":…}`。

- **路径语义（已与用户确认）**：`lq_path` 走 `pathguard.to_posix_array_path` +
  `ensure_allowed(kind="dir")`（Windows 形态 `W:\…` 也吃；白名单外 → 403，不是场景目录
  → 404 并列出试过的候选名）。legacy `scene_id` 经 `paths.scene_id_to_abs` 解析回场景文件
  绝对路径（fake/越界 → 404/400，需配 `SR_SCENES_ROOT`）。两条路都收敛到
  `scene_search.input_scene_path` —— 「是不是场景目录」只此一处判断。
- **落盘位置与命名**：掩码写进**该场景输入影像所在目录**，名字取
  `scene_search.mask_stem`（= **输入影像的 stem**，不是目录名）：SC 场景是
  `<目录名>.tif` → `<目录名>_mask.tif`；RC 场景输入叫 `PAN.tif` → `PAN_mask.tif`。
  与提交侧推导同源，否则 RC 场景写进去也白写（提交时去找的是另一个名字）。
  两份产物：`<stem>_mask.tif`（0/255 Deflate，`services.mask.write_mask_tif`）+
  `<stem>_mask.txt`（`write_mask_centroid_txt` 参考格式）。超分结果 `.tif` 也落同目录
  （run_sr/0817 脚本自身行为，本端点不处理）。
- 复用 `services.mask`：`rasterize_polygons(W,H, polygons→points)`（全分辨率 Pillow；24739×24199 ≈ 580MB 位图，盘阵机内存可扛，**不落浏览器**）。W/H 以 body 为准（= 元数据 W/H，不探 TIF）。
- 返回 `200 {"mask_path": "<绝对路径>", "mask_txt": "<绝对路径>", "lq_path": "<原图所在目录>", "task_draft": {lq_path, mask_path, …默认 sr 参数}}`。`task_draft` 供前端**预填**队列表单（不自动提交，Slurm 是真副作用；用户点提交才发 POST /api/queue）。
- 幂等：同 scene_id 重复提交 → 覆盖同名掩码（掩码生成无副作用风险，允许重复）。

### 3.5 盘阵任意场景目录（`POST /api/scenes/resolve`，2026-09-17）

用途：让 `SR_SCENES_ROOT`（datahub）之外的生产场景目录（真机在
`/DiskArray/GSHC2IMPS/PRODUCT/<年>/<月>/<日>/<卫星型号>/<段级目录>/<景级目录>`）
也能在查看器打开、画掩码、就地提交 SR，不必把数据搬进 datahub。前端两个入口
（场景库页检索条下的输入框、查看器工具栏的「盘阵场景」栏）共用一个 `ScenePathBar`。

请求体二选一：
```json
{"path": "W:\\GSHC2IMPS\\PRODUCT\\2026\\09\\02\\JL1KF02B03\\<段级目录>\\<景级目录>"}
```
```json
{"name": "<用户拖进来的文件名，可带后缀>", "date": "2026-09-02"}
```

- `path`：`W:\…`（经 `SR_DRIVE_MAP` 映射）或 `/DiskArray/…` 都吃，要写到**景级目录**这一层。
  **也可以直接给单个 `.tif` 文件路径**（2026-09-17 增补）——随手贴一张图也能看。此时走
  「裸 TIF」分支：`resolved.dir` = 该文件的父目录、`input` = 该文件；**父目录确实是合法场景
  目录**（`input_scene_path` 命中）时与目录形态完全等价，否则 `row.lq_path` 与 `mask_path`
  一起为 `null`、`sr_capable=false`（能看，但提交 SR 在盘阵上跑不起来）。给的文件后缀不是
  `.tif/.tiff`（`.jpg` 源、`meta.xml`、掩码…）→ **400** 说清，而不是掉进目录逻辑报「目录不存在」。
- `{name}`（`date` 可省）：查看器里选了本地影像后的反推路径。浏览器拿不到本地文件的
  绝对路径（`File` 只有 name/size/type），只能把**裸文件名**交给后端，由
  `backend/pathguard.infer_scene_paths` 反推候选目录 —— 命名规则与模板的唯一真源就在
  那里，**前端不自拼路径**（2026-09-17 前前端自己写死过一份，导致 `SR_SCENE_PATH_TEMPLATE`
  从未生效）。日期不给就由后端从文件名里的 14/8 位时间戳取，**取不到就报错让用户手粘**。
  候选按序（**两天 × 一条模板**，依次 stat）：
  1. `W:\GSHC2IMPS\PRODUCT\{y}\{m}\{d}\{sat}\{mid}\{name}` —— 生产树六层真形态，**成像日**；
  2. 同上，日期换成**次日**。
  名字里的 14 位是成像时刻，而盘阵按生产日建目录（夜里成像的景记在第二天），所以
  `{d}` 要试两天；模板不含日期占位符时两天渲染出同一条，去重后仍只有一条。旧扁平形态
  （`PRODUCT\{y}\{m}\{d}\{name}`，四段）那条兜底候选 2026-09-18 已删 —— 生产树上永远落空，
  只会让 404 的候选清单里多一条不符合六段格式的目录。
  配了 `SR_SCENE_PATH_TEMPLATE` 则只按配置的那一条走（同样给两天）。名字不符合生产命名规则
  （拆不出 `{sat}`/`{mid}`）时跳过第 1 条，不硬拼空段。各段的分隔符**下划线与空格都认**
  （2026-09-18 起）：盘阵上的真形态是下划线，而用户口径里出现过空格写法；段级目录名按
  **原文的分隔符**重建，拿空格名拼出下划线的段级目录必然 stat 不到。规则见
  [docs/sr_code/production-scene-naming.md](../sr_code/production-scene-naming.md)。
- 响应 `200 {"source":"manual", "row": <与 /api/scenes 行同形>, "resolved": {...}}`：
  `row.id` 是 `~` + base64url(绝对路径)（手工行形态，见 `api/paths.py`；库行 id 一字未变），
  `row.manual=true`、`jpgUrl` 在**库外**为 `null`（预览走 `GET /api/scenes/{id}/preview` 回
  JPEG 字节）、`row.W/H` 由 `preview_jpg.scene_dims` 回填。
  **`row.hasPreview` 一律填缓存到底在不在的真值**（库外没有静态 URL，但前端要靠它判断
  「这次会不会触发首次烘焙」并提示用户等待），`row.lq_path` 与 `resolved.sr_capable` 同源同真假。
  `resolved` = `{dir, input, input_name, mask_path, mask_exists, writable, sr_capable}`，
  路径一律**盘阵 POSIX 形态**（与提交侧归一化同一口径，前端 lq_path / mask_path 逐字比得上）。
  `writable` = 服务账号对该目录是否有写权限，提前告知好过提交后才发现写不了。
  `sr_capable` = 这个目录能不能提交 SR：目录形态**恒为 true**；裸 `.tif` 时看它父目录是不是
  合法场景目录。前端 `lqPath` 就是按它写的（`lqPath` 是「能否提交 SR」的唯一判据）。
- **不扫盘**：只 `stat` 用户给的目录，判定顺序是固定候选文件名（`<目录名>.tif/.tiff/.img`
  再 `PAN.*`），上界 6 次 stat，不 `ls`/`glob`/`rglob`/`iterdir`。盘阵数据量极大，
  列举一次就可能卡死；这条由测试用 `patch(Path, "rglob"/"glob"/"iterdir")` + `os.listdir`
  /`os.scandir`/`os.walk` 全打成 `AssertionError` 来钉。
- **判据**：目录含 `<目录名>_meta.xml` **且**含输入影像之一（与库内场景同一套判据，
  `scene_search.is_scene_dir` / `input_scene_path`）。缺 meta.xml 的场景 SR 脚本判不出
  RC/SC，本就不该放进来。
- 错误码分工：**400** 形态非法或压根反推不出来（相对路径 / `..` / UNC / 未知盘符 /
  日期格式 / 文件名里没有时间戳 / 名字不符合生产命名规则）· **403** 越
  `SR_ALLOWED_ROOTS` 白名单 · **404** 反推成立但盘阵上没有合法场景目录或没有输入影像 ·
  **422** 场景成立但读不到影像尺寸（前端开图要 W/H）。
  404 的 `detail` 必须列出试过哪些候选、各自为什么不行（前端原样渲染）——「猜错必须报错」
  的落地。`detail` 只能是**字符串**：`api.ts::http()` 把它直接塞进 `Error`，给对象
  用户看到的是 `[object Object]`。反推按两天找过（候选 > 1 条）时，`detail` 末尾补一句
  「成像日与次日都找过 —— 盘阵按生产日建目录，深夜成像的景常记在次日」（2026-09-18 增），
  否则用户看见两条只差一天的路径只会更懵。
  「为什么不行」按**粘错了哪一层**分派（2026-09-18 修）：`<目录名>_meta.xml` 的前缀是
  **完整生产名**（含 14 位成像时刻），所以粘日期目录（`…/PRODUCT/2026/09/18`，正是场景栏
  预填的那一层）得到的「缺 `18_meta.xml`」是句没有任何信息量的话。现在分层报：日期目录报
  「这是日期目录，场景目录在它下面 3 层 `<卫星型号>/<段级目录>/<景级目录>`」，卫星型号层
  报「还差 2 层」、段级目录报「还差 1 层」（段级名同样带 14 位成像时刻，光看名字会误报
  「缺 `<段级名>_meta.xml`」，所以按层数先认）、场景目录内部的子目录报「比场景目录还深」、
  名字压根不是生产名的报「不是完整生产名」。层与名字全部由 `pathguard` 的三个**纯词法**
  原语判（`production_tree_depth` / `flat_scene_layout` / `looks_like_scene_name`），
  **不参与准入** —— 准入始终只有一条：`<目录名>_meta.xml` 在不在。
- **`{name}` 可再带 `size_bytes`（2026-09-18，拖拽入口用）：把本地文件的字节数一起发过来，
  后端要求名字与字节数都对得上才认**（`_fingerprint_mismatch`）。**比的对象按拖入的是栅格
  还是 jpg 分岔**：栅格比「输入影像 stem + 字节数」，jpg 只比「场景目录名」。
  不传 `size_bytes` 则只看目录/影像存不存在（粘路径、第三方调用一切照旧）。
  - 栅格为什么两个都要：`input_scene_path` 的候选次序是 `<目录名>.{tif,tiff,img}` 在前、
    `PAN.{tif,tiff,img}` 在后，返回第一个存在的。RC 场景的输入影像是 `PAN.tif`，而同目录
    里往往还躺着 `<目录名>.tif`（上游 SC 步骤的产物）—— 光比字节数，用户拖进来的可能是
    另一张图；那之后画的掩码坐标会整片落在别的影像上。
  - 不符时**按这条候选不合格处理**（记进 `reasons` 后继续试下一条候选），最终仍是 404 且
    `detail` 里列出盘阵侧那个文件的名字与字节数，**不新增错误码**。
  - **`name` 以 `.jpg/.jpeg` 结尾时：名字比 `<场景目录名>`，字节数不比**（2026-09-18 订正）。
    盘阵那份 jpg 是**显示件**（8bit 就绪预览，见 `scene_search._IMAGE_EXTS`），SR 从不在
    它上面跑，与输入的 TIF 是两份产物、不可能同字节 —— 所以字节数那一半对它无意义。名字
    这一半**不能拿栅格输入的 stem 去比**：纯 RC 场景（目录里只有 `PAN.tif`）`inp.stem` 是
    `PAN`，而显示件叫 `<编号>.jpg`（生产全名），永远比不过 —— 那会让「拖 jpg」这条入口
    恰好在 SR 真要跑的场景上恒 404（初版实现如此，真机表现为「极少出现盘阵小标」）。
    判据只有一条：jpg 名（去后缀）== 场景目录名。默认模板下候选目录名就是由这个名字拼出来
    的，所以这条通常直接成立 —— 它挡的是「换了 `SR_SCENE_PATH_TEMPLATE`、场景目录改了命名」
    的部署；真正挡住派生件（`_cloud.jpg`、`.preview.jpg`）的是候选目录根本不存在。
    也**不泛化成「后缀不同就放行」**：那会连 `SC.tiff` 与 `SC.tif` 一起放过。
  - 名字里取不到 14/8 位成像时刻的 jpg（`PAN.jpg`、Windows 副本、别处导出的图）→ **400**，
    说清「平台不猜目录」以及该改拖哪一份。反推路径的唯一依据是文件名，没有日期就不知道该去
    `<年>/<月>/<日>` 哪一天找，猜一个就是拿别景的 `lq_path` 去提交。
  - `size_bytes` 非正整数 → **400**。`{path}` 分支是精确路径，不收这个字段。

### 3.6 拖拽入口的临时预览（`GET /api/scenes/{id}/preview-tmp`，2026-09-18）

用途：用户把盘阵上的 `.tif` **拖进查看器**时，不再让浏览器重新解码整幅原图（真机上一次
几十秒、几百 MB），改用服务端烘焙的 1/2 预览 JPG —— 与场景库打开同一张图的渲染路径、
同一套烘焙规则（`rule_stamp` 相同），所以两条链出来的字节一致。

**拖进来的是 `.jpg` 时这条端点不参与**（2026-09-18）：用户拖的那张就是他自己要看的那张，
再拿服务端 1/2 缩图顶掉反而降清，还白等一次解压采样 —— 关联成功后前端直接 `blob = file`
本地解码，像素与字节都来自用户那份。这条端点只服务「拖裸 `.tif` 反推命中」那条路。

与 `GET /api/scenes/{id}/preview` 的差别**只有落点**：

| | `/preview`（长期） | `/preview-tmp`（临时） |
|---|---|---|
| 落点 | 源同目录 `<stem>.preview.jpg`，或 `SR_PREVIEWS_ROOT` 镜像树 | `SR_TEMP_PREVIEWS_ROOT/<YYYY-MM-DD>/<sha256(源绝对路径)[:16]>.jpg` |
| 生命周期 | 跟场景数据长期存在 | **1 天**：每天本地 0 点整桶删除（服务启动时先清一次） |
| 响应头 | 无（静态 URL 那条走 nginx 的 `max-age=3600`） | `Cache-Control: no-store` |
| 消费方 | 场景库行、粘路径打开 | **只有**拖拽入口（`viewer.tryLinkScenes` 命中后升级 rec） |

- **不写生产数据目录**：拖进来的源可能是盘阵上任意一张图，这份缓存不该撒进场景目录。
  桶名是 ISO 日期、且桶里有本模块写的 `.sr-tmp-preview` 标记文件才会被删 —— 清理只认
  自己建的目录，根是符号链接/指向文件系统根时一律拒绝清理。
- **URL 不可静态映射**：临时根不在 nginx 的 `/disk-array/` alias 之下（那个 alias 只暴露
  `SR_SCENES_ROOT`），响应体本身就是那张 JPEG。nginx 对这条**不用改**：`location ~* /api/scenes/[^/]+/preview$`
  的 `$` 锚点本来就不匹配 `/preview-tmp`，它落到外层 `location /api/` 正好拿到 `no-store`
  的效果（别把 `$` 去掉）。
- **不进库行**：前端取这条用的是独立函数 `api.fetchTempSceneJpg`，**不改写** `row.hasPreview`
  / `row.jpgUrl` —— 那两个字段的语义是「生产 `<stem>.preview.jpg` 此刻在不在」，被临时
  路径置真之后再从场景库打开同一场景就会跳过懒生成、直接打一个 404 的静态 URL。
- 错误码与 `/preview` 同口径：`.jpg/.jpeg` 源直接回源字节；不可访问 **404**；烘焙失败 **422**。
- **清理不在请求路径里**：`purge_temp_previews` 要列举缓存根，而「不扫盘」是硬约束（见 3.5），
  所以它只出现在 `api/app.py` 的后台任务 `_tmp_preview_purge_loop` 里。

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
