# Agent trajectory 记录计划

## 当前实现

Agent 的模型恢复上下文仍由 `store.messages` 提供；trajectory 是独立的追加式观测层。

- `agent_runs`：一次 `run_loop` 的模型、机器、prompt 摘要、工具清单哈希、最终状态和答案。
- `trajectory_events`：按 `seq` 追加的运行事件，包含 `run_start`、`turn_start`、
  `llm_request_start/end`、`assistant`、`tool_execution_start/end`、`turn_end`、
  `error`、`run_end`。
- 工具事件通过 `parent_event_id`、`tool_call_id` 和 `turn` 关联。
- payload 在写入前按敏感 key 脱敏，并限制为 32 KiB；超出部分保留预览、字节数和 SHA-256。
- 新回合开始时，上一条仍为 `running` 的 run 会被标记为 `interrupted`，避免崩溃后误判。

查询接口：

```text
GET /api/chat/sessions/{session_id}/trajectory
GET /api/chat/sessions/{session_id}/trajectory?run_id=...&limit=...&offset=...
```

现有聊天 SSE 和 `messages` 恢复协议不变。

## 反馈采集

每次 trajectory run 完成后，聊天页会显示一个可选的“有帮助/需改进”反馈入口。反馈按完整 run 关联，避免把单个工具步骤的局部结果误当成最终质量。

- `trajectory_feedback`：保存 `session_id`、`run_id`、二值评分、可选原因和提交时间。
- 同一个 run 只接受一次反馈，重复提交返回 `409`。
- 写入接口：`POST /api/chat/sessions/{session_id}/trajectory-feedback`。
- 反馈与 trajectory 通过 `run_id` 关联，可用于后续质量筛选、人工复核和 SFT 数据构建。

## 下一步

1. 增加 trajectory → SFT JSONL 的投影器，明确 prompt、assistant、tool call 和 tool result 的训练格式。
2. 增加人工/规则质量标签：工具选择、参数、最终结果、是否需要修复。
3. 增加数据集版本、脱敏策略、去重和 train/dev/test 划分。
4. 增加离线 replay 与固定评测集，再尝试 LoRA/QLoRA。

trajectory 只记录可观察行为，不保存隐藏思维链；训练数据导出必须再次经过筛选和质量标注。
