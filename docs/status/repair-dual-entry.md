# 自动修复双入口（工具面板 / Agent）统一业务服务

日期：2026-11-13 · 状态：M1–M3 代码已实施，M4 联验命令见下；真实 SR/GPU 未验。

## 2026-11-16：生产批量执行改为显式准入

- 触发链路：`prepare_repair` → `probe_execution('production')` → `batch_worker.ensure_execution_allowed('production')`。旧实现对该模式无条件抛 `BatchGateError("production batch execution is not enabled yet ...")`，`execution.allowed` 因此为 false，计划卡显示「暂不可执行」。
- 现在由 `SR_BATCH_PRODUCTION=1` 显式开关解禁，不再强制 Slurm：`SR_EXECUTOR=slurm` 时要求 `SR_SLURM_FAKE` 非 1，`SR_EXECUTOR=local` 时要求主机有可用 bash；两种执行器都要求 `SR_SLURM_WORK_DIR` 存在且可写。任一条件不满足仍拒绝，并在计划卡给出具体原因。
- 默认未设开关时行为不变（拒绝 production）；`deploy/sr-api.service` 已写入 `SR_BATCH_PRODUCTION=1`。真机若用 drop-in 覆盖，新增 `/etc/systemd/system/sr-api.service.d/50-production.conf`，不改部署后的单元正文；改后 `systemctl daemon-reload && systemctl restart sr-api`。
- 已准备但未启动的计划无需重新准备：`get_repair_status` 会用当前闸门重算 `execution`，计划卡刷新即可从「暂不可执行」变为可确认；已确认计划仍受原有确认有效期约束。
- Local 可靠性：新增执行器中立工作目录 `SR_WORK_DIR`（旧 `SR_SLURM_WORK_DIR` 继续兼容）；`.local_jobs.json` 持久化排队/进程表，服务启动先恢复 queued、按进程起始时间+cmdline 校验身份后接管 running，同一 job_id 不重复启动；派发前写盘失败直接拒绝启动，`Popen` 后写 running 失败立即杀子进程并进入 quarantine，阻塞后续排队任务直到判定文件出现或人工 `resolve_quarantine()`；取消意图持久化 `cancel_requested`，重启后继续终止并确认进程树退出；`SR_LOCAL_JOB_TIMEOUT_SEC` 到点同样杀整棵进程树；生产预检覆盖 bash、工作目录、Python 导入、算法入口、模型权重与 GPU，结果按 env 缓存，计划卡轮询不重复探测。`deploy/sr-api.service` 使用 `KillMode=process` 让本机子进程跨重启存活。
- 本轮只跑合成 bash 夹具与自动化测试，未提交真实 GPU 作业；真实单景/批量验收步骤见 [sr-local-execution.md](sr-local-execution.md) §4.1，全部标为待核。
- 这次只改准入判断，没有跳过计划确认、PAN 资源锁、人工区域/整景质量验收或既有五层验证。本轮未提交真实 GPU 作业，内网单景联验结论仍需另行补记。

## 2026-11-16：Agent协助与自动修复统一真实修复口径

- 常规业务入口统一 `production`：前端共享 `repairExecution.ts`；工具栏自动修复固定真实SR、不再展示Demo模式选择；Agent协助提示明确 `mode=production`，模型工具schema仅允许production，工具默认值和REST准备计划默认值均改为production。
- 两入口仍复用同一个 Repair Application / 用户确认 / start / Batch Goal服务，没有另建执行器。生产条件未满足时明确保留受阻计划，不自动切Demo；面板也拒绝服务端意外返回的Demo计划。
- 旧的未提交浏览器草稿恢复时取消Demo偏好、保留候选身份。已落库的Demo计划/Goal不改写为生产；Agent计划卡不再启动待确认的旧Demo，Agent start工具同样拒绝旧Demo计划，必须重新准备并确认真实计划。已运行/完成的旧Demo仍如实保留历史标识和进度，不能冒充真实成果。
- **生产Batch硬门禁当时仍关闭**（`batch_worker.ensure_execution_allowed('production')`）；同日随后改为 `SR_BATCH_PRODUCTION` 显式准入，见本节上方。本条操作本身没有绕过既有准入，也没有将开发机配置改成可执行生产SR。内部显式Demo REST/服务路径仅保留开发测试与历史兼容，不作为Agent/工具栏业务回退。

测试：后端计划与工具契约42项、前端全量1111项通过，typecheck与build通过；专项验证Agent/REST缺省均为production、拒绝Demo、生产受阻不回退模拟，以及在隔离模拟门禁下复用确认/Goal幂等链；未启动真实SR/GPU。

## 2026-11-16：普通工具 Activity Timeline 视觉减法

- Viewer Agent栏与 `/chat` 共用 `AgentToolPresentation`、`AgentActivityTimeline` 与纯函数 `projectAgentActivity`；移除两套按工具名匹配结果的卡片模板。普通步骤为12px低对比文字、14px状态图标、1px连接线，单行28px；无大面积底色/描边/阴影。原始模型推理仍由 `ReasoningBlock` 管理，不以工具摘要代替思考。
- 优先按 toolCallId 关联并去重；旧无ID记录按同名待匹配调用的顺序关联，不合并合法重复调用。显式ID不匹配时不借用其他调用的结果；缺少结果或明确成功证据时显示“结果未确认”。传输包装成功不能覆盖业务 `ok=false`。
- 连续普通步骤归为完成计数，运行中默认展开、阶段结束后自动折叠；失败/未确认/正在运行步骤在收起时仍可见。用户手动选择存于有界会话内存Map，重连/历史替换/切换页面不强制收回；整页刷新使用默认折叠策略，不新增持久化字段。折叠180ms，只有运行点轻微呼吸，减少动效设置关闭动画。
- 场景候选、修复计划、批次草案回执、空间批注继续由既有业务组件展示；对应成功步骤不在普通轨迹重复展示。人工澄清沿用页面现有 `GoalClarifications`。场景打开、确认启动和复核接口不变；开发者详情可查看参数、调用ID、原始结果。
- 本轮不改Agent Loop、SSE协议、Store消息归并/落库或后端。原工作树其余未提交修改保留。`executionOverview`复用同一工具状态投影，动作中文名集中在 `chatActivity`。

验证：前端全量 **1,108 passed**（`npm test -- --maxWorkers=2 --minWorkers=1`），typecheck、生产build与diff检查通过。覆盖同名并发/乱序结果、缺ID旧会话、重复事件与历史恢复、失败/未确认状态、自动/手动折叠、业务组件及场景打开交互。浏览器隔离夹具 `.e2e/activity-timeline-preview.html` 复用 `repair-plan-vite.config.mjs`（复制到 `frontend/__activity-timeline-preview.html` 后以5174测试，结束删除临时HTML）；280px窄栏实测折叠28px、步骤28px、背景透明、无横向溢出，完成后自动收起，失败行不隐藏。[折叠截图](../../.e2e/artifacts/activity-timeline/collapsed-280.png)、[展开截图](../../.e2e/artifacts/activity-timeline/expanded-280.png)、[失败截图](../../.e2e/artifacts/activity-timeline/failed-280.png)。未连接生产接口、未启动SR/GPU。

## 2026-11-16：Agent 修复计划卡交互与视觉收口

- Agent 栏和 Chat 页共用 `AgentRepairPlanCard`：雾白卡片、深松绿主按钮，默认六项对齐摘要（目标数、范围、检测、掩码、模式、状态）。仅保留“确认并启动 · 启动演示 / 启动 SR”，沿用服务端 confirm → start，不再提供重复的确认按钮。启动后单卡切换进度/成果，生产质量复核入口保留。
- “查看全部 N 景”打开200ms侧抽屉，生产 ordinal 排序并显示真实 Viewer 数字标签；未打开的景标为 —，不猜标签。长名省略/title完整名、列表独立滚动，支持 Escape、焦点约束/归还、背景 inert 与减少动效设置。
- `GET /api/repairs/{plan_id}/targets?session_id=…&version=…&offset=…&limit=100` 是只读清单分页：读取已有计划、沿用会话隔离、版本不符409，不碰影像/执行器。超过模型视图50项上限的清单可完整读取。
- prepare/read工具显示中文名、真实成功/业务失败状态和一行摘要；同回合重复读取合并，技术JSON只在开发者详情显示。拥有结构化计划的同一用户回合中，Agent长说明默认折叠；提示词同时要求不再重复输出逐景Markdown表格，原文仍可展开。
- 历史消息接口补充结构化工具 `data`，可从同会话归档恢复旧的截断计划，不修改原始消息。卡片刷新后按REST服务端计划/Goal恢复，首次核对完成前禁用启动；快速双击去重、确认返回版本变化与409均要求用户重新核对。
- 浏览器隔离夹具：[.e2e/repair-plan-preview.html](../../.e2e/repair-plan-preview.html)、[.e2e/repair-plan-vite.config.mjs](../../.e2e/repair-plan-vite.config.mjs)。从仓库根复制HTML到 `frontend/__repair-plan-preview.html`，在frontend运行 `npx vite --config ../.e2e/repair-plan-vite.config.mjs --host 127.0.0.1 --port 5174`，打开 `/__repair-plan-preview.html?width=280&mode=production`；完成后只删除该临时HTML。夹具使用真实卡片组件、模拟Store/API，不允许提交SR。
- 浏览器实测280px/320px宽度下计划卡均高292px、无横向溢出；27行列表可独立滚动（当次视口列表高度450、内容1377），文件名省略。截图：[窄卡](../../.e2e/artifacts/repair-plan-ui/card-280.png)、[27景抽屉](../../.e2e/artifacts/repair-plan-ui/targets-27.png)。

验证：前端全量 **1,094 passed**（默认并发曾遇到宿主Node OOM，改用 `npm test -- --maxWorkers=2 --minWorkers=1` 完成）；后端计划/工具结果/消息快照/契约组合 **110 passed**，新增超50项清单分页专项 **1 passed**。类型检查与生产构建通过。所有确认/启动测试使用模拟接口或Fake，不运行生产SR/GPU；内网真实模型与部署后复核仍待核。部署需后端更新重启并发布前端dist。

## 2026-11-16：Agent 工具结果回读路径纠正

- 现场模型调用 `read_tool_result(path="/data/targets/ids")`，但修复计划真实契约为 `targets.items[].item_id`。prepare 工具说明和截断结果的 `read_paths` 明确提供 `/data/targets/items`，启动只需计划 ID 与版本，不要求提取所有影像 ID。
- 回读仍严格按实际 JSON 结构定位，不添加虚假 `ids` 别名。不存在/非法路径返回有界的 `requested_path / resolved_path / available_paths`（只给脱敏后的字段路径和类型，不展开值），提示按实际路径纠正；错误明确属于回读，不据此判断启动失败或重新提交。不可用/过期/跨会话引用继续拒绝。
- JSON Pointer 数组索引拒绝负数、前导零、加号；转义只接受 `~0 / ~1`。保留完整记录分页、字节预算及不前进游标规则。
- 聊天栏与 Agent 栏调用历史按 tool_call_id 匹配并展示各次参数；成功回读提供非空页摘要，旧空摘要显示“调用成功（未提供摘要）”，避免第一条记录空白。
- 回归包含真实归档的27景分页、错误路径恢复、只执行一次原工具、会话隔离、转义/预算边界，以及脚本化模型工具循环纠正。真实内网模型行为待核；需更新后端并重启、发布前端构建。

验证：后端 `test_tool_result_formatter / test_tool_description_contract / test_repair_application / test_message_viewer_context` **109 passed**；前端全量 **1,085 passed**，类型检查与生产构建通过。工作区已有 `WORKSPACE_MAX_BYTES=240000` 修改保留；资源裁剪测试显式设置小预算，不再依赖生产预算值。未运行真实 SR 或连接内网现场模型。

## 2026-11-16：本机 Demo 与旧 Slurm Fake 解耦

- `SR_EXECUTOR=local` 的 `mode=demo` 使用独立 `services/demo_exec.py`，不要求安装 Slurm、不要求 `SR_SLURM_FAKE=1`，也不调用真实 `submit_run_sr` / 本机子进程。显式旧 `slurm + fake` 配置继续兼容；下文“demo 必须 Slurm”的历史描述仅适用于旧路径。
- 新增可选 `SR_DEMO_WORK_DIR`，默认系统临时目录下的 `sr-platform-demo`。演示回执与模拟状态基于 SQLite `sr_tasks`，不依赖进程内 Fake 注册表；模拟 Job ID 在 JavaScript 安全整数范围内，带独立任务指纹标记，恢复和重复提交复用回执。
- 沿用计划确认、Goal/Item、资源锁、提交 Intent 和五层验收；演示仍为 `demo_contract / mock`，没有 SR 输出。禁止真实提交器接收演示标记；禁用模拟任务完成后的成果预览生成和真实成果验收/发现。
- 执行环境不可用的计划卡显示“暂不可执行”，不再同时显示“待确认”；后端原因不再附带 Python 异常类名。
- **生产 Batch 门禁当时仍关闭**；同日随后改为 `SR_BATCH_PRODUCTION` 显式准入（见本节顶部）。本次修复本机部署的 Demo 可用性，不代表真实 GPU / 本机生产批处理已完成准入。需更新后端并重启、发布前端构建，旧计划建议重新准备；内网验收待核。

验证：`test_batch_worker / test_repair_application / test_run_sr` 共 **182 passed**；新增本机 Demo 联验与 `test_image_refresh / test_goal_runtime` 共 **35 passed**；回执落盘后复跑两个本机专项 **2 passed**（与前述有重复，不累加）。前端计划卡 **7 passed**，类型检查、生产构建和 `git diff --check` 通过。测试使用合成小 TIFF，未启动真实 SR、GPU 或 Slurm。

## 2026-11-14：入口故障修复与非盘阵资源规划

- Agent 的三个同名只读工具此前由 `tools/repair.py` 和 `services/repair_workspace.py` 重复绑定，导致模型请求前抛 `工具已注册`。现在只读查询由后者统一绑定，保留分页、原始 ordinal、会话隔离及预算；prepare/start 仍走统一应用服务。Registry 继续严格拒绝重名，显式插件工具不会被内置实现覆盖。
- 工具面板不再把 `execution.allowed=false` 当作计划准备失败：有效计划正常保留展示，执行条件未满足时给出中文说明并禁用启动，不更改执行器、不绕过 Fake/production 门禁。
- Store 原来将所有新计划写为 READY，忽略 BLOCKED；现在正确持久化 READY/BLOCKED，未满足输入的资源评估计划不能确认或启动。
- Viewer 消息快照新增独立的 `workspace.resources`（本地影像身份、版本、上传/预览资产ID和尺寸），场景库候选 `items` 保持原语义。两者都有数量/字节预算；不传 File、像素、磁盘路径或前端权限声明。
- 截断标记拆分：`workspace.truncated` 只表示**场景候选 items** 被丢掉（修复计划据此拒绝静默规划前缀）；本地资源被预算裁剪只记 `resources_truncated`，不再把场景齐全的工作区判成“快照被截断”。`get_repair_workspace` 同时返回 `scenes` / `local_only` / `resources_truncated`，避免把“景数”和本地资源数混成一个 total。
- `prepare_repair` 支持本地、上传/预览资源及混合工作区的持久化评估计划。不再要求所有资源先关联盘阵；缺少后端源文件绑定和 PAN/NOSR/元数据/Mask 契约时返回具体待办，标记 BLOCKED / sr_execution=false。混合工作区不静默遗漏本地图，不扫描盘阵寻找替代资源。仅有旧版 local_only 数量、没有资源身份时提示更新快照。
- 普通允许目录中的完整场景输入，使用已有绝对资源引用即可准备可执行输入计划，不要求位于 SR_SCENES_ROOT；资源准入仍由允许根目录和实际输入契约决定。**本轮没有实现任意本地单 TIFF 直接执行现有生产 SR，也没有新增上传 PAN/NOSR 配对适配器。**
- Agent 资源评估卡显示识别到的目标总数，即使当前可执行数量为0。计划身份、会话与源版本仍冻结保存，后续输入补齐需重新准备。

验证：后端 `test_repair_application / test_repair_workspace / test_tool_description_contract / test_message_viewer_context / test_store / test_batch_manifest / test_tool_job_status / test_tool_result_formatter` 组合 **178 passed**；前端全量 **1,082 passed**，类型检查与生产构建通过。日志 `.e2e/repair-fixes-regression.log`、`.e2e/repair-fixes-frontend.log`。所有 SR 测试使用隔离输入与 Fake/Mock，未触发真实生产 SR。真实内网部署及模型、GPU质量验收仍待核。

部署需同时更新后端、重启 API，并发布最新前端 dist；仅改前端不能解决 Registry 冲突。下文“本轮测试未执行”等为前期历史记录，本次结果以本节为准。

## 1. 一句话

工具面板和 Agent 都通过 `backend/services/repair_application.py` 准备计划、记录用户确认、创建或复用**同一个 Batch Goal**；检测、Mask、SR 提交、监控、验收仍在 `batch_worker` / `run_sr` / `Store` 里，没有第二条执行链。

## 2. 调用链

**工具面板**

```
BatchRepairDraft.vue
  → POST /api/repairs/prepare      (sources + mode + strategy + detection)
  → 用户第二次点击 = 真实确认
  → POST /api/repairs/{id}/confirm (服务端写 repair_plans.confirmation)
  → POST /api/repairs/{id}/start   (校验后创建/复用 Goal)
  → batchGoal.selectGoal(goal_id)  → 既有 Progress Panel / SSE
```

**Agent**

```
Agent 消息（含 viewer_snapshot）
  → prepare_repair(scope=current|selection|workspace)
  → 计划卡 AgentRepairPlanCard：确认计划 / 确认并启动
  → start_repair(plan_id, plan_version?)
  → 同一个 Repair Application Service → 同一个 Goal
  → 只读查询：get_repair_workspace / list_repair_items / get_repair_item
```

**旧 REST 契约**

`POST /api/goals` 的 batch 分支现在必须携带已确认的 `plan_id`，并委托给
`repair_application.start_repair()`；不再接受「冻结清单直接建 Goal」。
因此面板、Agent、旧调用方只有一份确认与 Batch Goal 创建逻辑。

## 3. 计划与确认契约

- `repair_plans` 表只存计划与确认，不存任务进度；进度真源仍是
  `agent_goals` / `agent_goal_steps` / `sr_tasks`。
- `plan_id = "plan_" + sha256(绑定内容)[:24]`，绑定：会话、冻结清单、模式、策略、
  检测方法、Mask 策略、人工掩码身份、工作区场景集合。
- 计划有效期默认 3600s（`SR_REPAIR_PLAN_TTL_SEC`），确认有效期默认 900s。
- 启动前重新校验：清单内容摘要、目标 item 集合、PAN/NOSR 修订（size+mtime_ns，
  **不读整文件 SHA256**）、备份是否已存在、工作区目标是否仍在、执行门禁。
- 重复 `start` 用同一个 `request_key = repair:<plan_id>` 调
  `Store.create_batch_goal`；Goal 创建成功但响应丢失时，下一次调用复用同一行。
- 模型说“用户已确认”不算授权；只有 `POST /api/repairs/{id}/confirm` 写下的记录
  才能让 `start_repair` 通过。

## 4. 单景人工 Mask

- 单景人工修复表达为**单 Item Batch Goal**，Item 直接以 `MASK_READY` 创建。
- 已保存的人工掩码在 `start` 时复制进
  `<SR_BATCH_ARTIFACT_DIR>/work/<plan_id>/<item_id>/manual-1/mask.tif`，用既有
  `batch_mask.verify_mask` 做契约校验，并写入诚实的
  `existing-human-mask` detection（`provenance=real`）。
- 不运行检测器，不改写 `batch_worker` 的检测/提交状态机；Producer 直接进入
  既有提交前复核与 `run_sr.submit_run_sr`。

## 5. 工具边界

| 工具 | 副作用 | 说明 |
|---|---|---|
| `prepare_repair` | 只写计划与冻结清单 | 不提交 SR，不接受任意路径，不调用 `search_scenes` |
| `start_repair` | 创建/复用 Goal | 只接受已确认计划；不接受 `approved=true` |
| `get_repair_workspace` | 无 | 当前消息工作区 + 本会话最近批次 |
| `list_repair_items` | 无 | 已知 Goal 的 Item 状态计数 |
| `get_repair_item` | 无 | 单项状态、作业、复核、验证摘要 |

`prepare_repair` 绑定消息冻结的工作区；`start_repair` 同时比较计划记录的工作区
场景集合，清单被增删后旧确认会返回 `stale_workspace`。

## 6. 生产限制

- `start_repair` 启动前调用既有 `batch_worker.ensure_execution_allowed(mode)`。
- `production` 直接返回 `execution_blocked`，不会创建 Goal，也不会提交 SR。
- `demo` 仍要求 `SR_EXECUTOR=slurm`、`SR_SLURM_FAKE=1`、显式
  `SR_SLURM_WORK_DIR`（与既有 Fake SR 验收完全同一道门）。
- Demo 成果仍标 `demo_contract` / `provenance=mock`；`get_repair_result` 固定
  返回 `production_verified=false`、`quality_passed=false`。

## 7. 验证命令

> 2026-11-13 PR 3.5：本会话执行环境拒绝 `python` / `npm`（安全检查不可用），
> 以下命令**未运行**，不得据此宣称验收通过；需在可执行权限的会话中复跑。

```bash
cd backend && python -m pytest \
  tests/test_repair_application.py tests/test_batch_planning_tools.py \
  tests/test_tool_description_contract.py tests/test_batch_manifest.py \
  tests/test_batch_worker.py tests/test_goal_runtime.py -q
cd frontend && npm test -- --run \
  src/components/__tests__/AgentRepairPlanCard.test.ts \
  src/lib/__tests__/batchSequencePanel.test.ts
cd frontend && npm run typecheck
```

跨入口联验（同一冻结清单、面板与 Agent 各自发起、重复 start 只关联一个 Goal）
由 `test_repair_application.py::test_agent_prepare_then_start_shares_the_plan_contract`
与 `test_start_requires_a_server_recorded_user_confirmation` 覆盖。

## 7.5 PR 3.5 可靠性收尾（2026-11-13）

- **旧 `/api/goals` 不再能绕过确认**：batch 分支必须携带 `plan_id`
  （已服务端确认的计划）与匹配的会话；production 在创建阶段即被
  `ensure_execution_allowed` 拒绝，不会留下一个随后被 Worker 阻塞的 Goal。
- **可信会话**：REST 的 `session_id` 必须是服务端已存在的会话；计划读取按会话隔离，
  跨会话返回 404。`confirmed_by` 由服务端写为 `user:<session>`，客户端传入值被忽略。
  ⚠️ 这仍是**归属隔离**，不是生产认证：知道他人会话 id 的调用方仍可能冒用。
  生产发布前必须由反向代理 / 登录态提供可信用户身份。
- **工作区身份**：计划分别保存可执行目标 `scene_ids` 与完整工作区
  `identity_ids + identity_digest`（仅由场景 id 集合生成，不受显示名/本地图数量影响）。
  工作区 scope 启动时校验摘要；同数量不同集合会失效。混合工作区只要求目标子集，
  不会因无法解析的卡片阻断有效目标；截断快照在准备和启动两侧都拒绝。
- **计划版本**：同一计划绑定重新准备时 `version+1` 并清除旧确认；
  `start` 校验 `plan_version`，旧版本返回 `stale_plan`。
- **Goal 内容匹配 / 响应丢失恢复**：`agent_goals.checkpoint` 存
  `repair_plan_id`、`repair_plan_version`、`repair_plan_binding`；恢复时先按
  `request_key` 找回 Goal 并校验这三项，再走源一致性检查（成功修复后的 PAN/备份
  不会把可恢复的启动误判为过期）。
- **跨计划 PAN 独占**：`batch_resource_locks` 使用
  `ACTIVE / SETTLED / RELEASED` 三态。提交前用 `BEGIN IMMEDIATE` + 主键原子认领；
  另一执行中的计划得到 `BLOCKED`。成功为 `SETTLED`（记录解决时间），
  失败/取消/未提交为 `RELEASED`；只有**创建时间晚于上一次成功**的新确认计划才能
  接管 `SETTLED`，并发旧计划不能。未知外部结果（`BLOCKED` + 占位）保持 `ACTIVE`。
- **混合/截断工作区**：计划只要求目标场景仍在当前工作区（子集），并比较
  `workspace.total`；`truncated=true` 的候选集直接拒绝准备，不静默规划前 200 景。
- **人工 Mask**：先复制到临时文件、流式校验、`os.replace` 原子发布；空 Mask、
  跨场景 Mask、过期 Mask 均在创建 Goal 前拒绝。
- **计划投影**：`get_repair_status` / `get_repair_result` 同时给出
  `monitor_done`（真实列）与 `monitoring_done`（快照口径）。

## 7.6 PR 4 实时任务卡（2026-11-13）

- `AgentRepairPlanCard.vue` 在原计划卡上扩展为**单卡三阶段**：
  `plan`（目标/策略/确认/启动）→ `running`（状态、总进度、检测/SR/验收三轨、当前景）
  → `done`（真实结果摘要与审核计数）。
- 数据只来自两处：共享的 `batchGoal` Store（已有 SSE / `batch-snapshot`）和
  `GET /api/repairs/{plan_id}`。卡片不累计事件、不建立第二条 SSE、不维护第二套
  Goal 状态；Store 未关联时只在挂载/可见性恢复时读取权威 REST 状态。
- 同一 `plan_id` 的重复 prepare 结果只渲染最后一张卡（`AgentChatTab` 去重）。
- 结果摘要是确定性中文模板，条件为“Goal 终态 + monitoring_done”，
  区分演示契约验证、失败/受阻、等待人工质量复核、已通过/驳回；不调用 LLM、
  不新增聊天消息。刷新页面后重新读取状态即可重建同一摘要。
- `查看任务` 打开已有 Batch Panel 并定位 Goal；`查看成果` 通过 Goal Item 的
  `target_image.path` 打开真实影像；`开始复核` 打开待复核项与已有质量验收区块。
- `get_repair_status` 增加 Item `image_path / overlay_ready / review_required /
  phase / candidate_count` 与 `quality_reviews`（approved/rejected/pending）的轻量投影；
  plan_id/goal_id 进入工具结果 critical 字段，避免模型视图截断后卡片失去身份。
- 仍不提供第二套任务页、审核面板或执行链；production 门禁不变。

## 7.7 PR 5 修复成果联动与人工验收闭环（2026-11-14）

- **完成态只认显式收敛**：`AgentRepairPlanCard` 不再用
  `monitoring_done !== false`；必须是 Goal 终态 **且** `monitoring_done === true`。
  `get_repair_status` 的 Goal 投影由 `batch_snapshot.monitoring_done()` 统一给出，
  缺失/`None` 一律为 false，`get_repair_result.state` 同源。
- **产物身份**：状态投影新增 `items[].output_path`，只取
  `verification.layers.artifacts.status == "passed"` 的路径。Demo/Fake SR 该层为
  `not_applicable`，因此没有「查看成果」，也不会退回原始 PAN 或备份；不会扫目录找最新影像。
  `items[].quality_review_id` 标明该 Item 当前绑定的审核对象。
- **开始复核只对真实可审核产物出现**：条件是 `quality_review_id` 存在且 Item 为
  `AWAITING_REVIEW / REVIEW_REJECTED`；提交前 `MASK_READY` 掩码复核不再计入。
- **Agent → Viewer 直达**：`lib/batchDock.ts` 的 `requestQualityReview(itemId)` 只发布一个
  轻量意图；既有 Batch Panel 展开人工质量区块，既有 `batchQuality` Store 选中该 Item、
  加载 NOSR/SR 双图、定位第一个 `pending` 区域。没有第二套审核 UI/状态机。
- **Viewer → Agent 反向同步**：审核决策仍写 `batch_quality_reviews` 并追加
  `quality_review_changed` Goal 事件；共享 `batchGoal` Store 刷新快照后，任务卡按
  `last_seq` 重新读取 `GET /api/repairs/{plan_id}`。卡片新增区域级计数
  `quality_regions`（approved/rejected/pending/total）与 `quality_reviews` 场景计数，
  计数只包含当前 Item 绑定的审核，重新修复后的旧审核行不计入。
- **LTD 无候选审核**：合并 origin 的 `no-candidate` 整景人工确认后，
  `quality_products` 只统计真正生成了 SR 产物的审核；无候选场景仍计入
  `quality_reviews` 与验收门禁，但不会在任务卡里被写成「修复产物」。
- **确定性总结**继续区分：SR 执行中 → 计算结束（演示契约 / 文件契约）→ 等待人工质量复核
  → 部分驳回 → 全部必要区域通过 → 失败/受阻。`get_repair_result.quality_passed`
  只可能在 production + COMPLETED + 无 pending/rejected 时为 true，演示恒为 false。
- 区域快捷定位：既有区域下拉保留，新增编号胶囊与「下一待审区域」；左侧仍为本次 Job
  产物、右侧 NOSR（沿用现有双图配准与 Mask 叠加，不重写解码/差分）。
- 本轮执行环境仍拒绝 `python` / `npm`（`Auto mode is unavailable`），下列测试**未运行**：

```bash
cd backend && python -m pytest tests/test_repair_application.py \
  tests/test_batch_snapshot.py tests/test_batch_worker.py -q
cd frontend && npm test -- --run \
  src/components/__tests__/AgentRepairPlanCard.test.ts \
  src/stores/__tests__/batchQuality.test.ts
cd frontend && npm run typecheck
```

## 8. 仍未收口

1. 真实内网 SR/GPU、真实 50 景规模、nginx SSE 未在本轮复跑。
2. production 批量执行门仍关闭，`batch_worker` 的质量 Verifier 仍为
   `unverified`。
3. 聊天里的轻量任务引用卡只做到计划卡与回执；实时任务卡、动画、自动结题报告
   留到下一轮。
4. 人工 Mask 单景路径的演示闭环已用既有 `verify_mask` / `_verify_submission_inputs`
   静态验证；尚未在真实大图上跑一次 Fake SR 全流程。
