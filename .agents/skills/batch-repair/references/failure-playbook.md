# 失败处理

原则：先对账再定性；写操作只在用户明确要求后发生。

1. **用户已点计划卡**：卡片已完成确认与启动。先 `query_batch_repair` / `list_repair_items` 读状态，不调用 `start_repair`；暂时查不到就稍后再查，不改用 `run_sr`。
2. **prepare 返回 BLOCKED**：看 `targets.unresolved` 与 `execution.reason`；说明缺项请用户补齐，不启动，不说成修复失败。
3. **工作区为空或无绑定**：请用户打开/解析影像；不自动全库检索。
4. **快照被截断**：请用户缩小范围或分批；不静默规划子集。
5. **start 返回未确认**：请用户去计划卡确认，不重复调用。
6. **start 版本/状态错误**：`stale_plan` 省略 `plan_version` 再试一次，仍失败停止；`expired` / `confirmation_expired` 重新准备并确认；`goal_conflict` / `session_mismatch` 交用户处理，不绕过。
7. **Item FAILED**：依次看 `last_detection_error`、`last_submit_prepare_error`、`error`、`sr_status`、`verification_layers`，指出失败层级。
8. **Item BLOCKED**：容量、前置或未知副作用；读原因后等待或请用户处理，不重试。
9. **WAITING_SR + UNKNOWN**：用 `sr_job_status` 对账；仍未知说明证据不足，**不重新提交**。
10. **REVIEW_REJECTED**：给驳回区域数与前几条原因；返工由用户决定，不自动重跑。
11. **AWAITING_REVIEW**：说明待审区域；不代通过；需要时 `verify=true` 看 `review_valid`。
12. **SUCCEEDED 但 verification.ok=false**：按 `contract` / `artifacts` / `quality` 说明哪层失败，不算修复完成。
13. **NO_ISSUE**：只表示无候选，不等于无缺陷；仍以人工层为准。
14. **SKIPPED**：`excluded=true` 为创建时排除；否则看 `exclusion_reason`（`user_cancelled`、`capacity_deadlock`），不混为失败。
15. **列表为空或 ordinal 不符**：核对 `status`、`cursor`、是否误用筛选后序号；不猜 item_id。
16. **仅质检模式**：`inspect_repair_workspace` 从 `cursor=0, limit=4` 开始，沿 `next_cursor` 取完；收齐后界面展示总表，Agent 只汇总候选、失败与跳过；中途结束说明已检/未检范围。
17. **结果被截断**：`read_tool_result(result_id, path?)` 按引用分页；不根据截断内容下结论。

## 回复模板

1. 结论：当前在哪一层、能否继续。
2. 证据：计数、状态、具体原因（不贴原始 JSON/路径）。
3. 下一步：用户要做什么，或系统会继续做什么。
4. 边界：哪些未知、哪些动作不能由模型执行。
