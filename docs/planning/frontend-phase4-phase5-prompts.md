# 阶段4/5 提示词（新窗口粘贴用）

> 日期：2026-09-02 · 状态：技术形态已由多轮提问定案
> 用途：新窗口执行阶段4（查看器数据路径）、阶段5（平台 API 层 + 聊天/队列）时粘贴的提示词。
> 前置：阶段1-3 已完成（`frontend/` 完整 Vue3 查看器 + `tifDecode/maskgen/source` + 51 Vitest + `.e2e` 回归；backend 有 agent/tools/services，114 pytest）。

---

## 【阶段4 · 查看器数据路径】

你是本项目续接会话。先读 `CLAUDE.md` → `docs/status/current-question.md` → `docs/experience/gui-experience.md` → `docs/planning/frontend-migration.md`（§2 阶段表）。阶段1-3 已完成。

**目标**：让查看器能从内网盘阵通过 HTTP 读大图，并加盘阵检索/查看。= 前端 `HttpSource` + 后端场景索引 API + nginx Range 托管 + 检索 UI。

### 已定决策（不重议）
- 字节读取 = **nginx 静态 + HTTP Range**：CentOS7 上 nginx 把盘阵目录映射成 URL 前缀（原生支持 Range），`HttpSource` 直接 Range 读。后端**零字节代码**，只出一个场景索引 API。
- 连通 = 直连 IP:端口设计；部署文档注明走端口转发时只改 URL。
- 盘阵结构未确认 → **双层保险**：nginx 整块暴露 + 后端路径白名单（部署时按实际收紧）。
- 开发测试 = 逻辑层用本地静态服务/测试图；真机验收在 CentOS7 或 Win11 内网机（两者都能直接访问盘阵）。
- 硬约束照旧：浏览器 2GB 分配上限、Canvas 16384²、CDN 不可达（本地 vendor）、补丁版 utif.js 绝不重装。

### 现有代码状态
- `frontend/src/lib/source.ts`：`HttpSource` 是桩（read 抛"尚未实现"）。`Source` 接口 = `read(offset,len)→Promise<ArrayBuffer>`，`FileSource` 已实现。tifDecode 全部只依赖 Source 接口。
- `backend/services/scene_search.py`：已有 `search_scenes(root, query, satellite, date_from, date_to, limit)`（盘阵递归扫 + fake 回退），120 行。backend 目前**无任何 FastAPI/REST**。
- 测试基建：frontend Vitest 51 项、`.e2e` puppeteer（file:// 直测）、backend pytest 114 项。

### 任务清单
1. **后端最小 FastAPI 骨架**（`backend/api/` 或 `backend/app.py`，遵循现有零框架依赖风格）：
   - `GET /api/scenes` 场景检索（包 `search_scenes`；`SR_SCENES_ROOT` env 指盘阵根）
   - **路径白名单校验**：返回的 path 必须落在白名单根目录下；拒绝 `../` 穿越与白名单外绝对路径（注意 `tools/run_sr.py` 已对 `<fake>` 路径做拦截，检索层同样要防）
   - uvicorn 启动 + systemd unit 文件（nohup 备选）
2. **前端 `HttpSource`**（`frontend/src/lib/source.ts`）：
   - `fetch` + `Range: bytes=offset-(offset+len-1)`，206 处理；非 206/404/服务器不支持 Range 的错误处理
   - 复用 `source.read` 契约，**tifDecode 零改动**；`parseStrips`/`sparseSample` 直接可用
3. **盘阵检索/查看 UI**：场景列表 + 卫星/传感器/日期/关键词筛选 + 点击打开（HttpSource URL → 查看器走 probe→稀疏/分块）
4. **测试**：
   - `HttpSource` 单测：Node 里起本地 Range 静态服务器（Node `http`+`fs` 手写 Range 即可）验证 offset/len/越界/206/404
   - 后端 pytest：`/api/scenes` 正常 + fake 回退 + 路径白名单穿越拒绝
   - `.e2e`：起本地静态服务 → 打开 http 场景 → 查看器出图
5. **部署**：nginx 配置（盘阵目录 `location` + Range 默认支持）+ systemd + 离线部署文档
6. **真机验收（另排，不阻塞代码完成）**：CentOS7 起 nginx/FastAPI → Win11 浏览器打开盘阵大图

### 验收（离机即算完成）
- `HttpSource` 单测 + 后端 pytest + `.e2e`（本地静态服务顶替 nginx）全过
- nginx 配置 + systemd + 部署文档交付
- 真机项列验收清单，注明"需在 CentOS7/Win11 内网机执行"

---

## 【阶段5 · 平台 API 层 + 聊天/共享队列】

你是本项目续接会话。先读 `CLAUDE.md` → `docs/status/current-question.md` → `docs/experience/gui-experience.md` → `docs/planning/frontend-migration.md` → `docs/planning/frontend-phase4-phase5-prompts.md`（本文件）。阶段1-4 已完成（含 HttpSource + 盘阵检索）。

**目标**：把已有后端（agent loop + 4 工具 + sr_tasks + slurm）暴露成网页可调的 REST/SSE，实现两个页面：**聊天**（Agent 对话，MVP 用 mock 模型）与**共享任务队列**（SR 作业），并支持查看器画完掩码一键提交 SR。

### 已定决策（不重议）
- 队列 = **仅 SR 作业**（`run_sr` → Slurm），`sr_tasks` 表承载；服务端唯一事实源。
- SR 提交**三入口**：① 队列页表单（字段 = run_sr 参数）② 聊天 Agent 调 `run_sr` 工具 ③ 查看器画完掩码一键提交（前端多边形 JSON → 后端 `services/mask.py` 栅格化掩码.tif → `run_sr`）。
- 本地模型 MVP **用 mock**（`SR_LLM_MOCK=1` 注入假 LLM），真模型后配（`SR_LLM_BASE_URL` 指向内网本地 Ollama/vLLM 端点，内网不上外网）。
- SSE **先事件级**（`tool_call`/`tool_result`/`assistant`/`turn_done`），接口预留 token 流式扩展。
- 聊天会话 = **单会话 + 新建按钮**，持久化到 `sr_agent.db`，刷新可恢复（store 已支持 sessions + resume）。
- 完成判定 = **离机全测**（mock + fake slurm + 契约文档）；真机（真模型/真 Slurm）另排。
- 部署 = 脚本 + nginx + systemd + 文档（离线 CentOS7）。
- 硬约束照旧。

### 现有代码状态
- `backend/agent/loop.py`：自写状态机（openai 客户端，`build_client(cfg)` + `_default_chat(cfg)` 返回 `chat(messages, tools)`），18 测试。**mock 注入点 = `_default_chat`**（`SR_LLM_MOCK=1` 返回假 chat，按固定脚本应答工具调用/最终回复）。
- `backend/tools/`：`contract.py`（`@tool` 注册表 + `manifest()` 生成 OpenAI 兼容 schema）+ 4 工具：`search_scenes` / `run_sr` / `fix_bad_lines` / `sr_job_status`。
- `backend/services/store.py`：`sr_tasks` 表（fingerprint/job_id/status/params/config_xml/batch_script/log_dir）。
- `backend/services/slurm.py`：`sbatch_submit`/`squeue_status`/`sacct_status`/`job_status`/`cancel`（scancel）。`run_sr.submit_run_sr(params, run_cmd=...)` 支持 fake slurm 注入（现有 32 测试这么用）。
- backend **无 FastAPI**。
- frontend：`ChatPage.vue` / `QueuePage.vue` 是占位页；`stores/chat.ts` / `stores/queue.ts` 有设计约束注释。

### 第一步：写 REST/SSE 契约文档（先文档后代码）
在 `docs/planning/` 落 `api-contract.md`：端点路径/方法/请求响应 JSON/SSE 事件 schema/错误码。评审后再写代码。

### SSE 事件 schema 草案（可扩展 token 流式）
```json
// 每个事件一行 JSON（data: {...}\n\n）
{"type":"turn_start","run_id":"...","session_id":"..."}
{"type":"tool_call","name":"run_sr","args":{...}}
{"type":"tool_result","name":"run_sr","ok":true,"data":{...}}
{"type":"assistant","content":"已提交 SR 作业 12345"}
{"type":"turn_done","content":"最终回复文本"}
// 预留流式：{"type":"token","delta":"..."} 加入则前端按 token 渲染
```

### 任务清单
1. **契约文档**（先，见上）
2. **FastAPI 骨架** + uvicorn + systemd（沿用 `backend/config.py` env 风格）
3. **工具端点**：`POST /api/tools/{name}`（从 `tools.manifest()` 机械生成，或显式列 4 个）
4. **聊天**：
   - `POST /api/chat/sessions`（新建，返回 session_id）· `GET /api/chat/sessions/{id}/messages`（历史）
   - `POST /api/chat/sessions/{id}/messages`：跑 `run_loop`，用 **FastAPI `StreamingResponse` 直接回 SSE**（POST 即流；MVP 最简，一请求一回合）
   - **mock LLM**：`SR_LLM_MOCK=1` 时 `_default_chat` 返回假 chat（按固定脚本先调工具再回最终回复），端到端可测
   - 并发：同会话串行；同步 `run_loop` 用线程池跑（`asyncio.to_thread` / `ThreadPoolExecutor`），事件经 `asyncio.Queue` 转 SSE
5. **队列**：
   - `GET /api/queue`（`sr_tasks` 列表，含 Slurm 状态）
   - `POST /api/queue`（表单：`lq_path/mask_path/sr_scale/suffix/gpu/cloud_limit` → `submit_run_sr` 幂等）
   - `POST /api/queue/{id}/cancel`（`slurm.cancel`）
   - SSE 推状态变化（PENDING/RUNNING/COMPLETED/FAILED；Slurm 无百分比，进度=状态机）
6. **掩码→SR**：
   - `POST /api/masks`（多边形 JSON + W/H → `services/mask.py` 栅格化 → 写盘阵路径，返回 `mask_path`）
   - 查看器"提交 SR"按钮（用当前 `rois` 画布坐标 → 后端全分辨率）
7. **前端**：`ChatPage`（SSE 事件渲染 + 新建会话）、`QueuePage`（列表 + 表单 + 取消 + SSE 状态）、`ViewerPage` 掩码提交按钮
8. **部署** + 文档（脚本 + nginx + systemd）
9. **验收（离机）**：契约文档定稿 + 全部 pytest（含 mock LLM 的 loop 端到端 + fake slurm）+ `.e2e`（假后端驱动前端）；真机项列清单（真模型配 `SR_LLM_*` 指向内网端点、真 Slurm 提交）

### 阶段5 红线
- 契约文档先于代码；改动契约先改文档。
- mock 模型只替换 LLM 调用层，**不动状态机逻辑**（`loop.py` 行为仍被 18 测试锁住）。
- `run_sr` 幂等层（`sr_tasks`）是既有实现，REST 层直接调 `submit_run_sr`，不另写提交逻辑。
- SSE 事件 schema 是前端渲染与后端推送的共享契约，预留 `token` 类型但 MVP 不实现。
