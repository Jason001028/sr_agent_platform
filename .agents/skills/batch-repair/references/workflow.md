# 流程细节

## 范围

- 最新 `repair_context` 为准。
- 本图→`current`；选中→`selection`；这批/左侧→`workspace`；草稿→`draft`；已知→`goal_id`。
- 空工作区不扩全库；只有明确要求才 `search_scenes(scope=library)`。
- 第 N 景=原始 ordinal（从 1 起，含排除项）；UI 重排/筛选不重编号；筛选后序号要明确说明。

## 诊断

- 范围不明：`get_repair_workspace`；已知 `goal_id` 只问进度：`query_batch_repair`；明确要准备：直接 `prepare_repair`。
- `list_repair_items(goal_id, status?, limit, cursor)`：`status` 逗号；`limit≤50`；第 N 景用 `cursor=N-1, limit=1` 且不传 status，核对 ordinal=N；末页 `next_cursor=null`。
- `get_repair_item(...)`：快查 `verify=false`；查验收失效 `verify=true` 看 `review_valid`。
- `sr_job_status(job_id)`：`UNKNOWN` 是证据不足，不是成功或“无任务”。
- 截断用 `read_tool_result` 按引用读，不重跑。

## 准备与启动

- `prepare_repair(scope, mode="production", strategy, detection_method, mask_policy)` 只冻结计划。`READY` 说明总数/可执行/排除/未解析并请用户去卡片确认；`BLOCKED` 说明缺项；不输出计划表或 `plan_id`。
- `smart` 固定掩码复核；`mask_policy="existing"` 仅单景。
- 卡片默认自动启动（「启动前确认」开启时等待点击「确认并启动」）；已启动的卡片只查状态，不调用 `start_repair`。
- `start_repair` 补充路径：有效确认、用户明确要求、计划未启动；只传 `plan_id`，省略 `plan_version` 取最新；不传路径/掩码/SR 参数/`approved`。
- 未确认请用户去卡片；`stale_plan` 省略版本再试一次；过期或确认过期重新准备；`reused=true` 说明复用。

## 监控与汇报

- 卡片启动后先读状态；点击不等于完成。
- `query_batch_repair` 看 counts/progress/failed/waiting；`list_repair_items` 过滤 `FAILED,BLOCKED,REVIEW_REJECTED` 后读详情。
- 分层解释提交/终态/契约/产物/人工审核；缺层写未知。
- 汇报：结论→计数→下一步；明确 SR 已提交/执行结束/契约验证/人工验收；`NO_ISSUE` 只是无候选；质检模式声明未生成计划、未提交 SR。
