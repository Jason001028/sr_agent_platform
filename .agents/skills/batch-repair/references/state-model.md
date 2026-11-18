# 状态模型

## Item 状态

- 待处理：`PENDING`、`RETRY_WAIT`；准备：`OPENING`、`DETECTING`、`MASK_READY`；执行：`SUBMITTING`、`WAITING_SR`、`VERIFYING`。
- 收敛：`SUCCEEDED`（契约通过）、`NO_ISSUE`（无候选）、`FAILED`、`SKIPPED`；停留/人工：`BLOCKED`、`AWAITING_REVIEW`、`REVIEW_REJECTED`。
- `SUCCEEDED` ≠ 验收；看 `review_status` 与 `verification_layers.quality`。
- `NO_ISSUE` 只是无候选；实验性检测仍可能要求整景人工确认。
- `SKIPPED` 按 `exclusion_reason`：创建时排除不算失败；运行期常见 `user_cancelled`、`capacity_deadlock`。
- `BLOCKED` 不是终态失败，先看原因。

## 计数

- `total` / `eligible` / `detected` / `detection_pending` / `needs_repair` / `submitted` / `verified` / `verified_production|demo` / `converged` / `failed` / `blocked` / `excluded` / `skipped_runtime` / `waiting_sr` / `unknown_jobs`。
- `eligible` 排除创建时排除项；`verified_demo` 或 mock 不算生产质量。
- `progress` 分 `detection` / `submission` / `verification`，各含 `done` / `total` / `applicable` / `known`；`known=false` 表示检测未收齐。

## 验证层

`submit`=提交回执；`terminal`=执行终态；`contract`=产物契约；`artifacts`=产物发布/备份；`quality`=人工验收，唯一代表验收。`verification.ok=true` 只覆盖前四层；缺失层写未知。
