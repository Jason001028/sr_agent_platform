# Runtime Status

> 日期：2026-10-27 · 状态：已定（真实模型效果与内网 SR/GPU 验收待核）

## 执行链路与注入边界

普通聊天通过 `backend/api/platform.py` 在线程内执行 `run_loop()`；Durable Goal 的实际入口为
`backend/api/app.py` lifespan → `goal_worker.goal_loop()` → `goal_tick()` → `_process()` →
`run_round()` → `run_loop()`。`goal_runtime.py` 提供 REST/SSE 接口。

`backend/agent/runtime_status.py` 提供状态构建与消息投影纯函数。`run_loop()` 每次模型请求前，
在原有 Lease、轮次预算检查之后、`before_model()` 预留模型调用之前读取状态。
Durable Goal 的 `goal_status_provider` 每次通过 `current_lease.get().check(store)` 取得最新数据库行；
暂停、取消、租约过期或版本/Token 变化仍由既有 Lease 机制阻止执行。

Viewer 的 `project_messages()`、`POLICY`、冻结快照工具绑定和历史工具结果投影先正常完成。
Runtime Status 在这些投影基础上复制消息列表和消息字典，仅向当前请求的第一条 System Message
追加说明与 `<runtime_status>` 块。下一次请求从原有投影重新构建，不累计状态块。
其他消息、工具声明和工具结果保持原格式。

Runtime Status 不调用 `store.append_message()`，不进入聊天历史、返回的 transcript、Round Checkpoint
或工具结果。现有 `llm_request_start` 轨迹事件记录结构化状态字段，经 `TrajectoryRecorder` 的脱敏、
大小限制处理；其中不包含完整提示词、Viewer 快照、工具参数、原始输出或 Lease Token。

## 字段与统计口径

| 字段 | 来源与含义 |
|---|---|
| `execution_mode` | `run_loop(durable=...)`：`chat` / `durable_goal` |
| `loop_turn` | 当前 `run_loop()` 的 `turn/max_turns`，包含本次即将执行的 Turn |
| `model_calls_this_round` | 原有 `model_calls`；不含本次尚未预留的请求。Durable 显示已用/有效上限，普通聊天只显示已用 |
| `tool_calls_this_round` | 原有 `tool_count`，含返回校验错误的工具尝试。Durable 才显示执行中的 `max_tool_calls` 上限 |
| `elapsed_seconds` | 原有 `round_started` 与 `time.monotonic()` 之差，显示到 0.1 秒；Durable 才附上 `max_seconds` |
| `goal_status` / `goal_phase` | 本次 Lease 校验返回的 Goal 数据库字段；不根据消息或结果推断 |
| `goal_round` | 持久化 `rounds_used/max_rounds`，包含已登记的当前 Round，与 `loop_turn` 分开 |
| `goal_future_rounds_remaining` | `max_rounds - rounds_used`，表示当前 Round 以外还能启动多少轮 |
| `goal_model_calls_remaining` | `max_model_calls - model_calls_used`，为预留本次请求之前的全局剩余额度 |
| `recent_tools` | 当前 `run_loop()` 内最后 4 条工具返回的名称及 `success/error`；名称限 64 字符并限制字符集 |

Durable 有效模型调用上限为 `min(max_turns, max_model_calls)`。Worker 传入的 `max_model_calls`
已取本轮配置与 Goal 全局剩余模型调用数的较小值。第一次模型请求看到本轮已用 `0`；进入模型适配器前，
既有 `reserve_model()` 完成持久化预留，`model_calls` 加一，失败请求照常计费。

普通聊天的 `max_model_calls/max_tool_calls/max_seconds` 不参与执行限制，因此状态也不显示这些上限。
没有 Goal 提供函数的直接 `durable=True` 调用只提供本轮已知字段，不猜测全局 Goal 状态。
Goal 没有全局工具、时间或 Token 上限字段，状态不从 Checkpoint 的累计 usage 推造这些预算。

`goal_phase` 如实读取保存值。例如第一轮可能是 `goal_status=RUNNING`、`goal_phase=queued`，
因为登记 Round 的既有转换保留该字段。状态展示不修改状态机或阶段。

`success/error` 仅描述工具返回的 `ok`，不代表 SR 外部作业完成或业务验收结论。恢复执行时，
最近工具列表和本轮计数重新开始，历史证据仍使用既有消息及工具结果投影。

## 模型请求示例

以下两例对应 `backend/tests/test_runtime_status.py` 中实际传给 Mock LLM 并由断言检查的状态块。
System Message 同时说明：模型调用计数和剩余模型预算是本次预留之前的值，Turn/Round 包含当前项；
状态观察不授予权限，工具成功不等于业务验收通过。

普通聊天第二次请求：

```yaml
<runtime_status>
execution_mode: "chat"
loop_turn: "2/3"
model_calls_this_round: 1
tool_calls_this_round: 2
elapsed_seconds: 4.0
recent_tools: [{"name": "read", "status": "success"}, {"name": "fail", "status": "error"}]
</runtime_status>
```

Durable Goal 第三轮中的第二次请求：

```yaml
<runtime_status>
execution_mode: "durable_goal"
goal_status: "RUNNING"
goal_phase: "evidence_ready"
goal_round: "3/5"
goal_future_rounds_remaining: 2
goal_model_calls_remaining: 4
loop_turn: "2/3"
model_calls_this_round: "1/3"
tool_calls_this_round: "1/6"
elapsed_seconds: "3.0/20"
recent_tools: [{"name": "read", "status": "success"}]
</runtime_status>
```

## 执行与恢复保护

Runtime Status 只辅助模型决策。执行控制继续依赖原有 Lease Token/版本校验、Tool Intent Ledger、
Schema 校验、响应完整性保护、模型/工具/时间预算、工具结果预算、Round Outcome 和独立业务验收。
`WAITING_EXTERNAL` 由现有 Worker 查询与唤醒，不通过额外模型调用刷新状态。
终态结果正常回灌历史后，新的 Round 读取最新 Goal 行，包括 `external_result_ready` 阶段。

Viewer 状态仍属于消息级观察，Runtime Status 不复制快照，也不把 `pixels_loaded` 或附件数量
转换为“模型已看到图像”的结论。实际图片水合继续由既有模型适配器处理。

## 自动化验证与边界

专项命令（PowerShell）：

```powershell
& '.\.venv\Scripts\python.exe' -m pytest backend/tests/test_runtime_status.py backend/tests/test_agent_loop.py backend/tests/test_goal_runtime.py backend/tests/test_execution_safety.py backend/tests/test_tool_result_formatter.py backend/tests/test_message_viewer_context.py -q
```

实测：166 passed，15.92 秒；包含 Runtime Status 专项 20 项。现有 FastAPI/Starlette TestClient
依赖产生 1 条弃用警告。

全量命令与 Windows 环境前提：

```powershell
$env:PYTHONUTF8='1'
$env:PATH='D:\Git\usr\bin;D:\Git\bin;'+$env:PATH
& '.\.venv\Scripts\python.exe' -m pytest backend/ -q --tb=short
```

实测：994 passed、5 skipped、1 failed，另有 156 subtests passed，87.82 秒。
未通过项为 `GoalProcessAcceptance.test_two_jobs_http_disconnect_and_hard_backend_restart`：
Fake SR 终态夹具缺少 PAN 修复契约要求的 `_SRREPAIR_*.json`，独立验收记录缺文件、SR 数量不足，
最终 Goal 全局预算耗尽，测试等待 COMPLETED 超时。隔离副本中的无 Runtime Status 代码基线
（保留当前系统提示词）同一用例也在相同位置失败，15.54 秒。该项不标记通过，Runtime Status 不绕过验收。
Windows 默认解析到 `System32\bash.exe` 时打包及脚本测试还会失败，应使用上述 Git Bash PATH。

测试检查实际模型消息和 SQLite：普通聊天无虚假 Goal/预算字段；工具执行后计数、耗时、最近返回更新；
Goal 读取实时阶段及有效预算；暂停/恢复、等待/唤醒重新读取状态；历史、Checkpoint、Tool Intent 与结果
不含临时状态；Viewer 快照不漂移或重复；暂停、版本、Token、过期失效均阻止工具执行与陈旧写入；
响应截断不执行工具且仍记录 Token usage；结果预算及结果引用有效；模型、工具、时间预算仍会让出执行；
流式/非流式、内置 Mock、SDK 请求路径均有覆盖。

真实模型上的重复调用率、任务成功率、长任务表现和提示词 Token 开销尚未评估；真实内网 SR/GPU
执行待核。显示耗时采用 0.1 秒精度，执行预算仍使用原有精度。单个长工具调用的中断、SDK 的既有兼容性
重试和模型服务行为遵循原有实现，本功能不扩展执行控制。
