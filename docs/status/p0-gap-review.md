# P0 差距复核（2026-11-02）

> 对照文档：[docs/planning/1023.md](../planning/1023.md) 的 Harness P0 计划。
> 本复核只评估现有实现与测试，不包含旧版批量修复计划的 PR A～G，也不启动 Batch 开发。
> 基线：`main` @ `6ac0408`，工作树干净；本机 Windows 10 + Anaconda Python 3.11.3（仓库无 `.venv`）。

## 1. 总体结论

| 计划项 | 结论 | 主要依据 |
| --- | --- | --- |
| P0-0 统一 SR 状态 | 已实现，缺工具与队列的对拍测试 | `tools/sr_job_status.py`、`api/platform.py::_task_state` 都走 `run_sr.query_job_status()` |
| P0-1 Goal 数据模型 | 已实现；完整状态转移图未在 Store 层强制 | `agent_goals` / `agent_goal_steps` / `agent_tool_intents` / `agent_goal_events` 建表与迁移 |
| P0-2 Tool Intent | 核心已实现；四类副作用只用了两类，通用工具幂等键仍基于 tool call | `goal_worker._execute_tool()`、`run_sr.submit_run_sr()` |
| P0-3 Goal Worker | 已实现；进程级验收未稳定全绿 | `goal_worker.goal_loop()/goal_tick()/_process()`、Lease 与 WAITING_EXTERNAL |
| P0-4 Recovery/Retry | 部分：有预算与 RETRY_WAIT；无指数退避、连续故障熔断、运行截止时间 | `goal_worker` 的 `next_wake_at=now+1` 与总预算 |
| P0-5 Goal Verifier | 部分：通用验收已实现；领域验收缺失 | `validate_acceptance()`、`verify_goal()`、`sr_contract()` |
| P0-6 API / 前端 | 后端 API 与 SSE 齐；前端缺 Goal 状态展示与事件消费 | `/api/goals` 路由；前端只有 `GoalClarifications.vue` |
| P0-7 测试与验收 | 部分：Mock/Fake/故障注入在；固定验收集与指标未产出；进程验收红 | `test_goal_runtime.py` 等；见 §3 |

没有发现会直接破坏 P0 安全边界的实现缺口：UNKNOWN 不重投、`COMPLETED` 必须经独立 Verifier、关闭页面不取消 Goal、租约失权后拒绝写入，均有代码与测试支撑。剩余差距集中在「验收可证明性」和「无人值守的失败收敛」。

## 2. 逐项差距

### P0-0 统一 SR 状态

- 现有实现：`backend/tools/sr_job_status.py` 先按 `job_id` 取 `sr_tasks`，再调 `run_sr.query_job_status()`；队列侧 `api/platform.py::_task_state()` 调同一函数。
- 已满足：执行器路由、判定文件终态、判定文件缺失回 UNKNOWN、不复制第二套终态判定。
- 缺口：没有一条测试直接断言「同一 `job_id` 经工具与队列得到一致状态」；契约通过（`verify_sr_run.check_contract`）与业务验收的区分只在 Goal 侧体现。
- 最小补法：加一条共用 `sr_tasks` 夹具的对拍测试，不改实现。

### P0-1 Goal 数据模型

- 现有实现：`store.py` 的四张 Goal 表、`lease_token` 增量列、`transition_goal()` 的版本号与事件同事务写入。
- 已满足：Goal/Step/Intent/Event 持久化，状态与事件同事务，Version 与 Lease Fencing，旧表保留。
- 缺口：
  - `transition_goal()` 只校验状态枚举，不校验完整转移图；合法转移目前靠调用方自觉，`goal_runtime.ALLOWED` 只覆盖 API 控制入口。
  - 没有专门覆盖「旧库升级后 Goal 表结构」的迁移测试（澄清表有类似用例）。
- 风险：内部代码误调用可以把终态 Goal 改回可调度状态。当前调用路径都有 Lease/Version 约束，实际风险低。
- 最小补法：在 `transition_goal()` 增加一张显式转移表（允许 `BLOCKED` 的受控恢复除外），并补迁移用例。

### P0-2 Tool Intent 与副作用保护

- 现有实现：`goal_worker._execute_tool()` 在调用工具前写 Step/Intent 并置 `EXECUTING`；`run_sr` 在物理提交前用指纹预留；`_recover()` 遇到非只读的 `UNKNOWN/EXECUTING` 直接 `BLOCKED`。
- 已满足：意图先落库、Job ID 与 Intent/Step 同事务关联、UNKNOWN 不重投、只读回执可恢复。
- 缺口：
  - 计划列出的 `IDEMPOTENT_EFFECT` / `APPROVAL_REQUIRED` 两类没有实际使用；只有 `READ_ONLY` 与 `RECONCILE_REQUIRED`。
  - 通用工具（如 `fix_bad_lines` 带 `output_path`）的幂等键是 `session:assistant序号:tool_call_id`，不是规范化业务参数；SR 侧才用指纹。
  - 没有区分「重复请求」与「用户明确授权的再次执行」的机制。
- 风险：非 SR 的写副作用在崩溃后只能整体阻断，缺少按业务键对账的路径。
- 最小补法：为写文件类工具定义业务幂等键与 `APPROVAL_REQUIRED` 判定；不改变 SR 现有指纹链路。

### P0-3 Goal Worker 与后台续跑

- 现有实现：`goal_loop()` 单线程有界执行器；`claim_goal()` 原子领取；`WAITING_EXTERNAL` 只查外部状态；续租与失权检查覆盖模型调用前后和工具入口。
- 已满足：独立于 HTTP/SSE、持久化 `next_wake_at`、重启扫描非终态 Goal、等待期零模型调用、暂停/取消不强杀 SR 作业。
- 缺口：进程级验收（`test_goal_process.py`）不能稳定全绿，见 §3；真实进程强杀与跨进程接管未在真机验证。
- 最小补法：先判定失败是夹具契约还是产品竞态，再决定补夹具或补时序；不放宽断言。

### P0-4 Recovery、Retry 与熔断

- 现有实现：`RoundOutcome.RETRYABLE_ERROR` → `RETRY_WAIT`；重试等待固定 1 秒；总预算由 `max_rounds` / `max_model_calls` 限制；模型错误分类在 `request_budget.error_class()`。
- 已满足：模型瞬时错误可重试、总预算耗尽安全失败、外部失败与 UNKNOWN 分开处理。
- 缺口：
  - 没有指数退避；`RETRY_WAIT` 固定 1 秒后重新领取。
  - 没有「连续同一确定性故障」的熔断或停放；只靠总预算兜底。
  - 没有基础设施重试上限、连续异常恢复次数上限、可选运行截止时间。
- 风险：模型服务持续不可用时，会以 1 秒间隔消耗预算与日志，直到总预算耗尽。
- 最小补法：在 Checkpoint 记连续失败次数与上次错误类别，按次数做指数退避，达到阈值转 `BLOCKED` 或 `PAUSED`。

### P0-5 Goal Verifier

- 现有实现：`verify_goal()` 读取 Intent/Step、SR 任务与 `verify_sr_run.check_contract()`，校验 `min_sr_jobs`、`sr_scenes`、`required_tools`、非空 `artifacts` 与 `allow_skip`。
- 已满足：模型自然语言不能完成 Goal；`COMPLETED` 只走 `complete_verified_goal()`；`skip=1` 默认不算成功；判定文件/契约重新执行。
- 缺口：没有遥感领域验收（修复质量、NoData、报告内容）；当前只能从「文件存在且非空」推断产物，不能推断质量。
- 风险：显式验收条件写得太弱时，会得到「流程完成但质量未证」的 Goal。这是计划允许的边界，不是实现缺陷。
- 最小补法：定义领域 Verifier 接口，先覆盖可确定性检查（尺寸、波段、NoData、报告字段），不伪造质量结论。

### P0-6 Goal API、SSE 与前端

- 现有实现：`/api/goals` 创建/列表/查询、`/events?after=`、`/events/stream`、`/steps`、`/tool-intents`、`/external-job`、`/pause`、`/resume`、`/cancel`、澄清接口。
- 已满足：请求级 `Idempotency-Key`、事件严格递增 `seq`、断线后可按 `after` 回放（后端）、暂停/恢复/取消。
- 缺口：
  - 前端只有 `GoalClarifications.vue`；没有 Goal 状态、步骤、当前作业、失败原因的展示，也没有 `after=seq` 的消费逻辑。
  - `BLOCKED` 没有受控恢复入口；人工对账后只能新建 Goal。
- 风险：后端已具备无人值守能力，但用户在页面上看不到任务真实状态，P0-6 的验收条件未满足。
- 最小补法：在现有聊天页或侧舱增加只读 Goal 状态块，按 `after` 增量回放；不新增本地状态机。

### P0-7 测试、故障注入与固定验收集

- 现有实现：`test_goal_runtime.py`（31 项）、`test_goal_clarification.py`（23 项）、`test_goal_process.py`（3 项）、`test_sse_ownership.py`（5 项），覆盖 Lease、事务、重启、UNKNOWN、验收、SSE 断线等。
- 已满足：Mock LLM、Fake SR、进程级故障注入、确定性断言。
- 缺口：
  - 没有 30～50 条固定任务验收集，也没有 10 项指标的输出。
  - 场景 9（SSE 断线后按序号恢复）、10（LLM 不可用后恢复）、11（重复确定性错误熔断）、12（暂停/取消不改变 SR 作业）没有专项用例。
  - 场景 17（普通聊天与队列无回归）需以全量回归结果为准。
- 最小补法：先把现有用例整理成固定集并补上述四类，再输出指标表；不新增业务抽象。

## 3. 测试基线

本机命令与结果：

```text
python -m pytest backend/tests/test_goal_runtime.py backend/tests/test_run_sr.py \
  backend/tests/test_goal_process.py backend/tests/test_local_exec.py -q -p no:cacheprovider
# 148 passed, 2 failed in 94.61s
```

两项失败都在 `test_goal_process.py`：

- `test_old_process_cannot_submit_after_new_worker_takes_over`
- `test_two_jobs_http_disconnect_and_hard_backend_restart`

单独跑进程模块：

```text
python -m pytest backend/tests/test_goal_process.py -q -p no:cacheprovider
# 2 passed, 1 failed in 32.12s
# 失败点：等待期间 model_calls 由 2 变 3（test_goal_process.py:524）
```

单独跑 `test_old_process_cannot_submit_after_new_worker_takes_over`：**通过**（10.10s）。同一用例在组合运行中失败、在单跑中通过，说明当前进程级验收存在时序或测试间干扰，不能声明稳定全绿。另一次组合运行中，`test_two_jobs...` 的失败点在第二次重启后等待 `COMPLETED` 超时（`test_goal_process.py:550`），与旧计划记录的夹具契约差距一致：`ExternalServices.finish()` 写旧式 `<目录名>_<suffix>.tif` 与 SRLOG，而修复模式契约要求 PAN 备份与 `_SRREPAIR_*.json` 审计文件。

`test_local_exec.py` 在本机本次运行中全部通过；旧计划记录的 7 项失败未在本机复现。这只能说明当前环境未复现，不能据此认为环境问题已修复。

全量后端结果：

```text
python -m pytest backend/tests -q -p no:cacheprovider
# 6 failed, 1159 passed, 7 skipped in 351.88s
```

其中 5 项与 P0 无关：`test_agent_loop.py::TestOnStepSeam` 仍按旧事件序列断言，而当前循环会先发 `thinking_status`：

```text
FAILED test_agent_loop.py::TestOnStepSeam::test_no_tool_final_emits_assistant
FAILED test_agent_loop.py::TestOnStepSeam::test_non_stream_response_reasoning_emitted_once
FAILED test_agent_loop.py::TestOnStepSeam::test_streaming_chat_emits_phase_and_deltas
FAILED test_agent_loop.py::TestOnStepSeam::test_streaming_ignored_for_two_arg_chat
FAILED test_agent_loop.py::TestOnStepSeam::test_tool_roundtrip_event_order
# 5 failed, 2 passed
```

这是思考强度抽屉改动后测试未同步，属于普通聊天链路的已知回归，不计入 P0 实现缺口，但会挡住场景 17 的「无回归」结论。

第 6 项是 `test_two_jobs_http_disconnect_and_hard_backend_restart`，失败点在第二次重启后等待 `COMPLETED` 超时（`test_goal_process.py:550`）。本次全量运行中 `test_old_process_cannot_submit_after_new_worker_takes_over` 通过，而定向组合运行中它失败——进一步确认进程验收是顺序相关的，不是固定失败。

前端本轮未跑。前端的缺口由源码确认：`frontend/src` 中只有澄清组件读取 Goal，没有 Goal 状态/步骤/事件展示。

## 4. 计划验收场景对照（P0-7）

| 场景 | 覆盖情况 |
| --- | --- |
| 1. 意图入库后、外部执行前退出 | 已覆盖：`test_lease_loss_inside_submit_preserves_unknown_intent_for_recovery` |
| 2. 外部接受后、Job ID 入库前退出 | 已覆盖：`test_hard_exit_after_external_accept_before_receipt_never_resubmits` |
| 3. Job ID 已保存、等待期间重启 | 已覆盖：`test_full_three_step_restart_and_independent_acceptance` |
| 4. SR 已完成但 Goal 未更新时重启 | 已覆盖：`test_crash_after_receipt_before_message_recovers_without_resubmit` |
| 5. 相同 Goal 创建请求重复发送 | 已覆盖：`test_project_builtin_mock_runs_real_search_and_verifies_goal` 的同键复用断言 |
| 6. 两个 Worker 同时领取 | 已覆盖：`test_two_connections_have_exactly_one_claim_winner` |
| 7. 租约过期旧 Worker 写入 | 已覆盖：`test_expired_worker_rejected_without_takeover_and_with_takeover` |
| 8. 关闭浏览器后继续推进 | 已覆盖：`test_sse_disconnect_does_not_cancel_background_goal` |
| 9. SSE 断线后按序号恢复 | 部分：后端支持 `after`，无专项回放测试；前端无消费 |
| 10. LLM 暂时不可用后恢复 | 部分：只测了失败与预算耗尽，未测恢复成功 |
| 11. 重复确定性错误触发熔断 | 缺：没有熔断实现 |
| 12. 暂停/恢复/取消不改变 SR 作业 | 缺：没有专项用例 |
| 13. SR 提交成功但业务产物验收失败 | 已覆盖：`test_verifier_failure_reaches_model_as_internal_context` |
| 14. 预算耗尽安全停止 | 已覆盖：`test_total_budget_distinct_from_round_yield` |
| 15. UNKNOWN 不盲目重投 | 已覆盖：`test_unknown_does_not_submit_again` |
| 16. `skip=1` 不误报成功 | 已覆盖：`test_skipped_or_missing_products_are_not_claimed_success` |
| 17. 普通聊天与队列无回归 | 有回归：全量 6 项失败，5 项是 `thinking_status` 事件序列未同步，1 项是进程验收时序；均非 P0 代码缺陷 |
| 18. 旧 SQLite 数据库可迁移 | 部分：澄清表有迁移用例；Goal 表无专项用例 |

## 5. 建议的收口顺序

1. 先定位进程级验收失败是夹具契约、测试隔离还是产品竞态；不放宽断言、不改 Verifier。
2. 补 P0-4 的指数退避与连续失败熔断；这是「无人值守」唯一的确定性收敛缺口。
3. 补 P0-6 前端最小 Goal 状态展示与 `after=seq` 回放。
4. 把现有用例整理成 30～50 条固定验收集，补场景 9/10/12，输出 10 项指标。
5. 定义领域 Verifier 接口；真实遥感质量与 NoData 仍列为生产门槛。
6. 可选：Store 层转移图校验、`APPROVAL_REQUIRED` 与业务幂等键。

真实模型、真实 SR/GPU、跨进程强杀接管、盘阵权限与生产大图契约仍需在内网机验收，本机无法证明。

## 6. 文档对齐问题

- `docs/planning/1023.md` 正文是 Harness P0，但首行仍写着「P0 前端视觉改造任务：品牌主导航与影像操作工具栏」，与正文无关；建议删掉这三行残留标题。
- `docs/status/durable_goal_runtime.md` §8/§10 记录的测试数量是历史值；本轮实测以本文 §3 为准。
