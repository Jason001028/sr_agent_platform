# Tool Result Budget

> 日期：2026-10-27 · 状态：已定（Mock / Fake Executor 验证；真实模型与内网 SR 待核）

## 数据边界

`backend/services/tool_result_formatter.py` 只构造模型视图，不调用工具、不修改原始结果。
`backend/services/tool_results.py` 管理会话内引用、受控回查和历史工具消息投影。

调用顺序为：完整性检查 → 参数 Schema 校验 → 现有业务及 Lease 检查 → Tool Intent / 真实执行 → 原始回执 → Formatter → 有界工具消息。
`result_hook`、SSE 结果和调用方 transcript 继续接收原始结果。轨迹记录模型视图及其引用，沿用现有脱敏与轨迹大小限制。
Goal Verifier 仍读取 Intent、SR 任务、判定文件及产物；模型摘要不是验收依据。

短结果保留 `ok/data/error` 结构。超长结果增加 `_tool_result_budget`，明确记录截断、原始字节数、预算、工具类型、完整结果引用。
关键业务值逐字段保留；关键值本身超限时返回明确的 `critical_fields_exceed_budget`，指示回查且不得据此推断成功或重新提交。
JSON 按字段、数组元素及文本片段缩减，再完整序列化；不截取序列化 JSON。

## 配置

各项在回合开始读取；无额外 Tokenizer 依赖。

| 环境变量 | 默认值 | 作用 |
|---|---:|---|
| `SR_TOOL_RESULT_MAX_BYTES` | 12000 | 单条模型工具消息字节上限 |
| `SR_TOOL_RESULT_MAX_TOKENS` | 4000 | Token 保守估算预算 |
| `SR_TOOL_RESULT_SCENE_ITEMS` | 12 | 场景模型视图最多展示条数 |
| `SR_TOOL_RESULT_ARCHIVE_BYTES` | 8388608 | 单个新增完整结果快照最多 8MiB |
| `SR_TOOL_RESULT_ARCHIVE_TOTAL_BYTES` | 268435456 | 新增快照原始字节总配额 256MiB |
| `SR_TOOL_RESULT_RETENTION_DAYS` | 30 | 引用保留期 |

UTF-8 字节数作为字节级 Tokenizer 的保守 Token 上界，不宣称是实际 Token 数；实际消息限制取两个预算较小值。
因此默认最多 4000 字节。预算低于 1024 或存档配置非正值时，在执行工具前拒绝配置。
`Tool.result_max_bytes` 可进一步收紧单工具预算，`result_formatter` 可提供专属视图，`result_protected_fields` 可增加保护字段。
专属 Formatter 不能覆盖受保护事实。
专属 Formatter 的返回值先经过完整 JSON 序列化、反序列化和 UTF-8 校验，再进入字段保护与预算缩减。
函数执行或返回值校验失败时，模型视图记录 `formatter_error` 并回退通用 Formatter，不影响原始结果回调。

本机制限制工具结果，不压缩用户/系统/助手历史，也不实现 Context Compaction、模型总窗口规划或历史工具消息总量压缩。

## 摘要策略

- `search_scenes`：保留 backend 顺序，展示完整场景记录，不展示半条记录；保留总数、来源、扫描数、查询条件、可见条数、遗漏条数及下一页读取参数。
  原工具只返回 `limit` 内的快照，`outside_result` 明确标注原始快照外的命中数；这部分需缩小检索条件，不能假称已保存所有匹配条目。
- SR 状态：保护 `job_id/state/active/exit_code/verdict/skip/reason/contract` 及业务错误、幂等和必要产物引用。
  `UNKNOWN`、失败、运行中和合法跳过保持不同语义。
- 日志文本：优先 ERROR / WARNING / FATAL、首部和尾部，附原始字节数、行数及至少省略的字节数；UTF-8 边界安全。
- 其他 JSON、数组、嵌套对象：逐级缩减，并独立保留关键字段；包含业务判定的数组作为整体保护，放不下则明确报告预算异常。

## 完整结果与恢复

`tool_result_refs` 保存引用元数据。会话、助手消息序号、Tool Call ID 共同关联执行实例，结果哈希区分不同回执。
稳定的已完成且无外部 Job ID 的 Intent 回执只记录指向现有 Intent 的引用，不重复保存正文。
其他超长结果按有限配额保存 zlib 压缩快照；会改变的 SR 提交回执使用快照，避免终态回灌覆盖历史引用。
原有 Tool Intent 及可信业务记录不受新增快照配额影响。

过期引用在后续存档时清理；会话删除时同步删除引用。尚未结束的 Goal 所属会话保留引用，以支持恢复。
每次回查校验会话和内容哈希；过期、缺失、越界或被覆盖的回执返回明确错误。
超过快照配额或存档不可用时，模型视图明确给出 `full_result.available=false`，不会因此重新执行真实工具。
不带持久化 Store 的运行仅支持当前运行内、同样受配额控制的内存引用，元信息明确标注其寿命。

新工具消息只保存 Model View。恢复时直接复用已保存视图；历史完整工具消息和恢复产生的回执在送入模型前也经过相同 Formatter。
既有历史 SQLite 消息不批量重写。模型调用声明及 Tool Call ID 不变，恢复不通过重跑工具补齐摘要。

## 受控读取

回合使用现有 ToolRegistry 增加 `read_tool_result`，闭包绑定当前会话；不建立第二套注册表。
它接受 `result_id`、JSON Pointer `path`、非负 `offset` 和 `limit`（1–20）。
数组按元素、对象按键分页，文本按字符读取（每次至多 1000 字符）；每页仍受同一个 Formatter 限制。
分页先按预算选择完整记录、键值或字符，再生成 `shown/next_offset/has_more`；游标只消费实际展示的内容。
分页结果不再经过通用结构缩减。单条记录超过预算时，返回 `page_item_exceeds_budget`，展示数为零、游标保持原位，提示改用更深的 JSON Pointer。
单条过大时可用更深路径或缩小 `limit` 读取，截断元信息继续指向原始引用。
返回值沿用现有轨迹字段脱敏策略。该工具不接受服务器文件路径，不执行原工具，也不跨会话读取。

## 确定性验证

`backend/tests/test_tool_result_formatter.py` 覆盖 Formatter、预算、引用隔离、存档配额、过期与 Goal 保留、重启回查、旧历史投影、Schema 边界及 Fake SR Durable 恢复。
Fake SR 恢复测试保留完整 Intent 回执，验证物理提交一次，并由既有独立 Verifier 完成验收。

实测命令：

```powershell
python -m pytest backend/tests/test_tool_result_formatter.py backend/tests/test_execution_safety.py backend/tests/test_agent_loop.py backend/tests/test_goal_runtime.py backend/tests/test_runtime_context.py backend/tests/test_skills.py -q --tb=short
python -m pytest backend/tests -q --tb=short
python -m compileall -q backend/agent/loop.py backend/tools/contract.py backend/services/store.py backend/services/goal_worker.py backend/services/tool_result_formatter.py backend/services/tool_results.py backend/tests/test_tool_result_formatter.py
git diff --check
```

相关回归 131 项通过，其中 Formatter 与结果管理测试 34 项。全量实测为 906 通过、23 跳过、7 失败、1 错误，尚未全绿。
失败包含 5 项本机 Executor 不可用、Windows 子进程停止超时（含 teardown 错误），以及一次进程接管测试中等待模型开始的 5 秒超时。
进程接管测试单独复跑通过；另一次全量中的心跳计时失败也单独复跑通过，最终全量该心跳测试通过。这两项计时失败的原因未确定，不据此宣称为既有环境问题。
`compileall` 与差异空白检查通过。真实模型与内网 SR/GPU 未执行。

3000 条模拟场景记录：完整结果 324854 字节，默认模型视图 1916 字节，显示 12 条并明确省略 2988 条。
约 1.3MB 的模拟失败 SR 日志：模型视图 763 字节，仍保留 FAILED、Job ID、退出码和 verdict。
这些是指定测试输入的大小，不代表真实模型 Token 数或推理耗时。
