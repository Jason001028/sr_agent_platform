# 阶段4/5 提示词（新窗口粘贴用）→ **已归档：阶段4/5 均已实现完成**

> 日期：2026-09-02（阶段4 技术方向变更定案）· **2026-09-03 归档**：阶段4（盘阵读 JPG）与阶段5（平台 API + 聊天/队列）**均已完成离机实现并通过门禁**。
> 状态：已完成（2026-09-02）——仅剩**真机验收**，见 `docs/status/real-machine-acceptance.md` 一页纸（CentOS7/Win11 内网机）。
> 用途：（**归档**，供追溯）当初给新窗口执行阶段4/5 的提示词。新窗口**勿照抄重做**——已定决策/契约草案/验收清单在此留档，实现现状以 `frontend-migration.md` §2/§6 为准，测试计数以 `current-question.md` §6.3 测试基线为准。
> 归档前原文记录：阶段4 技术方向变更定案见下【阶段4】已定决策（09-02 晚：盘阵改读**服务器预生成 JPG**，废弃"HttpSource + nginx Range 读 TIF 字节"）；阶段5 见【阶段5】头部完成注记。

---

## 【阶段4 · 查看器数据路径（盘阵读 JPG）】

> 状态：**已完成（2026-09-02）**——盘阵读 JPG 已落地：后端 `/api/scenes` 检索补 W/H + `/api/scenes/{id}/preview` 懒生成 + `services/preview_jpg.py`（稀疏采样镜像）+ 路径白名单；前端 `/scenes` 页 + `route='jpg'` 同构 rec。
> 门禁：backend pytest 148 + 前端 Vitest 85（75 基线 + 10 scene）+ `.e2e/test-scenes.js` 45 断言全绿（实现以 `docs/planning/frontend-migration.md` §2 行4 为准，计数以 `docs/status/current-question.md` §6.3 为准）。
> 以下为历史提示词，仅剩**真机验收**项（`docs/status/real-machine-acceptance.md` 清单），新窗口勿重复离机实现。

（归档前原文）你是本项目续接会话。先读 `CLAUDE.md` → `docs/status/current-question.md` → `docs/experience/gui-experience.md` → `docs/planning/frontend-migration.md`（§2 阶段表）。阶段1-3 已完成。

> ⚠️ **前置纠偏**：`frontend-migration.md` §2 阶段表（及 `source.ts` 注释）里阶段4 的"HttpSource / Range 读取 / strip-窗口读取端点"描述**已过时**——09-02 已拍板改走"盘阵读 JPG"，下文【阶段4】"已定决策"为准。

**目标**：让查看器能浏览内网盘阵的遥感大图，并加盘阵检索/查看。显示 = **服务器预生成的 8192 JPG**，浏览器**不再对原始 TIF 做任何字节读取**。2026-09-02 已拍板改向（原 "HttpSource + nginx Range 稀疏读 TIF" 方案废弃），与 09-01"预览=JPG 中间产物"的原意对齐。

### 已定决策（不重议）
- **显示 = 服务器预生成 JPG**：CentOS7 盘阵机把 TIF **稀疏采样 + 2% Linear 拉伸**烤成 8192 长边 JPEG，落缓存；浏览器 `<img>`/canvas 加载。首次生成约几十秒（后台/懒生成），之后 nginx 静态直出（原生缓存）。
- **浏览器内不再读 TIF 字节**：`HttpSource` / Range 路径**砍掉，本阶段不实现**（`source.ts` 的 HttpSource 桩保留不动或删除均可，不被调用）。本地解码库（tifDecode/maskgen/sparse）**一律不动**。
- **本地文件路径保持现状**：仍走稀疏 TIF 读法 + 全交互拉伸 + exportToJpg，这是阶段1-3 已交付行为，零改动、无回归。
- 盘阵 JPG **只烤一种拉伸（2% Linear 默认）**：交互式拉伸下拉在盘阵场景**禁用**（tooltip 注明"盘阵 JPG 已烘焙 2% 线性拉伸"）；本地文件路径保留全部拉伸模式。
- **掩码**：在 JPG 画布上绘制 → 坐标按**场景元数据 W/H** 换算回全分辨率（thumbToOrig 逻辑不变，scale 来自元数据而非 probe）。阶段4 掩码仍前端直出 `掩码.tif` 下载；阶段5 才改为后端栅格化写盘阵。
- 导出 JPG：盘阵场景**不提供**（服务器已有即为交付物）；本地文件路径保留。
- 连通 = 直连 IP:端口设计；部署文档注明走端口转发时只改 URL。
- 盘阵结构未确认 → **双层保险**：nginx 整块暴露 + 后端路径白名单（部署时按实际收紧）。
- 硬约束照旧：本地文件路径仍受 2GB/16384 约束；第三方库本地 vendor；补丁版 utif.js 绝不重装。

### 现有代码状态
- 前端 Vue3 查看器（阶段3 完成）：六组件 + `stores/viewer.ts`（718 行，`decodeRec` 填充 DecodedRec{W/H/thumb/src/stats/route} + `paintStretch`）。本阶段新增**场景来源模式**：加载 JPG → 构造 route='jpg' 的同构 rec（src 取 JPG 画布像素，已烘焙拉伸）→ 掩码/平移/缩放/删除/合并全部复用。
- `frontend/src/lib/source.ts`：HttpSource 是桩 → **本阶段不需要**。
- `backend/services/scene_search.py`：已有 `search_scenes(...)`（递归扫 + fake 回退 + 文件名解析卫星/传感器/日期），120 行。backend 目前**无任何 FastAPI/REST**。
- 后端有 `services/mask.py`（Pillow，栅格化掩码）可参考 Pillow 用法；无 TIF 稀疏读取的 Python 实现（需新写，镜像前端 `parseStrips`/`sparseSample`/`stretch` 语义）。
- 测试基建：frontend Vitest 75 项 + `.e2e` puppeteer、backend pytest 114 项。

### 任务清单
1. **后端 FastAPI 最小骨架**（`backend/api/` 或 `backend/app.py`，遵循现有零框架依赖风格，config 沿用 `backend/config.py` env 式）：
   - `GET /api/scenes` 场景检索：包 `search_scenes`，每个场景补 `W`/`H`（优先伴生 `.hdr` ENVI 头，缺失则探测 TIF 头部；探测结果可缓存）+ `jpgUrl`（nginx 静态地址；JPG 未生成时为 null）
   - `GET /api/scenes/{id}/preview`：**懒生成** JPG（存在且 mtime 不旧于源就跳过）→ `FileResponse` 返回；nginx 可对该路径加缓存
   - **路径白名单校验**：返回/生成的 path 必须落在白名单根目录下；拒绝 `../` 穿越与白名单外绝对路径（`tools/run_sr.py` 已对 `<fake>` 路径拦截，检索层同样要防）
   - uvicorn 启动 + systemd unit 文件（nohup 备选）
2. **后端 JPG 生成服务**（`backend/services/preview_jpg.py`）：
   - 稀疏采样 = 前端语义的 Python 镜像：解析 TIFF 条带布局（纯 numpy 解析 IFD，不引新依赖）→ 每隔若干行抽一行 → 缩到长边 8192（上限 8192，源更小则原尺寸）
   - 拉伸 = 2% Linear（与前端 stretch 语义一致；灰/单波段 16bit 输入）；Pillow 编码 JPEG
   - 缓存幂等：落 `<源同目录>/<basename>.preview.jpg`（或独立缓存目录），存在且新于源则跳过；`jpgUrl` 由 nginx 静态托管
   - 单测：小 fixture TIF（如 256² 无压缩）→ JPG 生成正确、二次调用不重新生成、白名单外拒绝
3. **前端场景检索/查看 UI**：
   - 场景列表（新路由 `/scenes` 或查看器侧栏）：卫星/传感器/日期/关键词筛选（复用 search_scenes 参数）+ 行显示 W/H
   - 点击打开：`jpgUrl` 未就绪先调 `/preview` → `<img>` 加载 → 画到 canvas → 构造 route='jpg' 的同构 rec → 现有查看器全流程复用（掩码/合并/删除照旧）
   - 掩码换算：`thumbToOrig` 的 scale 用元数据 W/H 计算；本地文件路径仍走 probe
   - 盘阵场景禁掉拉伸下拉 + 导出按钮（tooltip 说明），本地场景照旧
4. **测试**：
   - 后端 pytest：`/api/scenes` 正常 + fake 回退（fake:true + 假尺寸）+ W/H 解析 + 白名单穿越拒绝 + `/preview` 生成/缓存幂等
   - 前端 Vitest：route='jpg' 的 rec 构造 + 掩码换算分支
   - `.e2e`：起本地静态服务（放一张真 JPG）顶替盘阵 → 打开 http 场景 → 查看器出图 + 掩码换算断言；本地文件路径回归（38 断言仍全绿）
5. **部署**：nginx 配置（盘阵目录静态托管 + JPG 缓存目录 + `/preview` 缓存）+ systemd + 离线部署文档
6. **真机验收（另排，不阻塞代码完成）**：CentOS7 起 nginx/FastAPI → Win11 浏览器开真实大图（1.1GB/1.78GB）→ 记录首次生成耗时、二次秒开、显示内存占用

### 验收（离机即算完成）
- 后端 pytest + 前端 Vitest + `.e2e`（本地静态服务顶替 nginx）全过
- **本地文件路径零回归**（75 Vitest + 38 .e2e 断言仍全绿，稀疏/交互拉伸/导出照旧）
- nginx 配置 + systemd + 部署文档交付
- 真机项列验收清单，注明"需在 CentOS7/Win11 内网机执行，重点测首次生成耗时与内存"

---

## 【阶段5 · 平台 API 层 + 聊天/共享队列】

> 状态：**已完成（2026-09-02）**——契约定稿 `api-contract.md`（已定），实现与门禁见
> 当轮计数：后端 190 + Vitest 114 + `.e2e/test-platform.js` 11 断言（现值为 `docs/status/current-question.md` §6.3）。
> 以下为历史提示词，仅剩**真机验收**项（§5.2 清单），新窗口勿重复离机实现。

你是本项目续接会话。先读 `CLAUDE.md` → `docs/status/current-question.md` → `docs/experience/gui-experience.md` → `docs/planning/frontend-migration.md` → `docs/planning/frontend-phase4-phase5-prompts.md`（本文件）。阶段1-4 已完成（含盘阵场景检索 + 读 JPG 查看 + 掩码；本地文件路径仍是稀疏 TIF 读法）。

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
