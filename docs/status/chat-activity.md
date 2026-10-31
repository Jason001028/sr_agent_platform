# Agent 当前任务进度文案

日期：2026-10-31。

Viewer Agent 和 Chat 页共用 chat store 中的临时 `activity`，由
`frontend/src/lib/chatActivity.ts` 归并现有 SSE 事件。不新增后端协议、模型调用或数据库字段。

- 发送时冻结附件是否存在与来源 ROI 标记，生成初始分析文案。
- `llm_phase` 切换模型输出阶段；`tool_call` / `tool_result` 使用调用 ID 跟踪未完成工具。
- 工具返回后显示结合结果分析；失败结果显示分析失败原因，不宣称执行成功。
- 正文到达显示整理回答；重复 reasoning/token 保持相同状态引用，不触发进度更新。
- 终态事件禁止后续增量重新激活；请求 finally 清空活动，历史消息不加载临时进度。

`ReasoningBlock` 右侧、无正文等待提示与工具执行期间的兜底提示均使用同一活动文案。
未知工具使用通用描述。不解析原始思考，不根据耗时模拟步骤或完成百分比。
普通模型思考内部没有可观察执行节点时，文案保持不变。

回归测试：`frontend/src/lib/__tests__/chatActivity.test.ts`。
真实模型与浏览器视觉验收待核。
