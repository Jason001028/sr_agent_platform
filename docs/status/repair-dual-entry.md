# 智能修复双入口（工具面板 / Agent）统一业务服务

日期：2026-11-13 · 状态：M1–M3 代码已实施，M4 联验命令见下；真实 SR/GPU 未验。

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
