# 1023 P0 源码差距审计与落地记录

| 领域 | 当前实现 | 缺口/风险 | P0 最小改造 |
|---|---|---|---|
| SR 状态 | `services/run_sr.py::query_job_status` 已按 `SR_EXECUTOR` 路由，并读取统一 verdict 文件；队列平台调用同一函数 | 工具入口曾直接调用 Slurm，可能与 local 执行器不一致 | `tools/sr_job_status.py` 先按 job id 关联 `sr_tasks`，复用 `query_job_status`；无历史任务时保留旧回退 |
| 会话恢复 | `sessions/messages/agent_runs/trajectory_events` 可恢复聊天历史 | 会话不是独立业务任务，SSE 断线不能表达长期生命周期 | 新增 `agent_goals`，保存状态、checkpoint、版本、lease、预算和唤醒时间 |
| 外部副作用 | `sr_tasks` 在提交前建指纹行，提交后保存 job id；未知 job 已拒绝盲重交 | 无通用工具意图账本，其他副作用缺少统一审计 | 新增 `agent_tool_intents`，保存规范化参数、幂等键、重放策略、尝试次数和外部 job |
| 事件 | `trajectory_events` 绑定 agent run | 没有 Goal 级严格递增序号，前端无法可靠断点回放 | 新增 `agent_goal_events(goal_id, seq)`，所有状态迁移与事件同事务提交 |
| 调度 | 队列有后台轮询，但聊天执行依赖请求/SSE | 关闭浏览器后没有 Goal worker | 新增 `services/goal_runtime.py`，lifespan 启动 bounded worker；WAITING_EXTERNAL 只轮询真实 SR 状态，不调用 LLM |
| 验收 | SR verdict 文件可判定执行契约 | 执行终态不等于业务目标完成 | Goal 先进入 `VERIFYING`，只有 checkpoint 的确定性 verifier 标记 `verified=true` 才 COMPLETED |
| API | 现有 chat/queue REST/SSE | 缺 Goal 创建、查询、事件、暂停/恢复/取消 | 新增 `/api/goals` 及 `/events`，请求级 `Idempotency-Key` 去重 |

## 状态与安全边界

Goal 状态使用 `QUEUED/RUNNING/WAITING_EXTERNAL/VERIFYING/RETRY_WAIT/PAUSED/BLOCKED/COMPLETED/FAILED/CANCELLED`。`UNKNOWN` 外部状态进入 `BLOCKED`，不会再次提交；暂停或取消只改变 Goal，不强杀已经提交的 SR 作业。SQLite `BEGIN IMMEDIATE`、版本号和 worker lease 防止同一 Goal 被并发推进。

## 已知接入点

当前 Goal API 提供持久化生命周期与外部作业等待。模型 Round 的具体编排继续由现有 `backend/agent/loop.py` 负责，后续可在 `goal_runtime` 的 `RUNNING` 分支接入有限预算的 round adapter，而无需改变数据库契约或现有聊天路径。
