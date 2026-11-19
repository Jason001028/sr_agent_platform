---
name: batch-repair
description: "批量或单景修复的进度、失败、待审，或明确要求准备/启动计划时使用；仅质检模式也用于只读检测分页。不代确认、不提交 SR、不做人工验收。"
disable-model-invocation: false
---

# 批量修复工作流

常驻 Viewer 策略仍有效。界面计划卡的「确认并启动」是默认启动入口；本 Skill 负责诊断与解释，流程细节按需读取 references。

## 硬约束

1. 只问进度/失败/待审只做只读诊断；明确要求修复、准备或启动才 prepare。
2. 服务端只认界面卡片确认；不代确认，确认前不 start。
3. 不提交 SR、不调检测器或 MaskBuilder、不改掩码/参数/验收、不做人工质量决定。
4. `goal_id`、`item_id` 只来自工具或本轮上下文；第 N 景按原始 ordinal 定位。
5. 提交、执行、契约、人工验收分层汇报；前两层不算完成。
6. 只给结论/计数/下一步；不输出原始 JSON、路径、长表格或 `plan_id`。

## 模式

- `inspect_repair_workspace` 可用，且 `prepare_repair` / `start_repair` / `run_sr` 被禁用 → **仅质检**：只读检测分页（`cursor=0, limit=5`，沿 `next_cursor`）；收齐后界面出总表，只汇总候选、失败与跳过；中途结束说明已检/未检范围。
- 否则为**正常修复模式**；只读查询工具在两种模式下都可能存在。

## 流程

顺序：范围 → 诊断 → 准备 → 启动 → 监控 → 失败 → 汇报。`start_repair` 仅为卡片之外的补充路径；单景走同一链路。细节见 `references/workflow.md`，状态见 `state-model.md`，参数与计划状态见 `plan-and-tools.md`，失败处置见 `failure-playbook.md`。
