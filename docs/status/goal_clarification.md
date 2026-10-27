# Durable Goal Clarification 集成与验收

日期：2026-10-27。实现已接入，真实模型与内网 GPU 验收待核。保留工作树原有修改，未执行 Git commit。

## 1. 本地审计与复用点

| 审计问题 | 修改前的实际实现 | 本次接入 |
|---|---|---|
| 是否已有用户等待状态 | 没有。`WAITING_EXTERNAL` 等外部作业；`NEEDS_APPROVAL` 映射到 `PAUSED`，不能承担任务澄清 | 独立的 `WAITING_USER` / `NEEDS_CLARIFICATION` |
| 工具后能否立即结束 Round | `run_loop.result_hook` 已能在工具结果持久化后返回结构化 Outcome | 沿用同一 Outcome/return 路径，澄清成功必定立即返回 |
| 消息如何写入和恢复 | `Store.append_message` 保存 wire 消息，`resume=True,prompt=None` 重读原会话 | 回答写同一消息表，关联 `goal_id/clarification_id`；原指令不重插 |
| Worker 如何处理等待 | `SCHEDULED` 与 `claim_goal` SQL 显式列出可领取状态，包括 `WAITING_EXTERNAL` | 两处均不加入 `WAITING_USER`，不会领取、重试或请求模型 |
| 可复用哪些并发机制 | `BEGIN IMMEDIATE`、嵌套 `worker_transaction`、`expected_version`、Lease Token、`MAX(seq)+1` | 请求/回答各在一笔事务内完成，Worker 仍受 fencing 保护 |
| 前端承载位置 | `/chat` 和 `AgentChatTab` 共用历史，没有澄清卡片 | 新组件复用两处页面及 `api.ts`，服务端状态驱动按钮 |

参考 [Pi Durable README](https://github.com/earendil-works/pi/blob/main/packages/durable/README.md) 的持久化与恢复边界、
[tool.ts](https://github.com/earendil-works/pi/blob/main/packages/durable/src/harness/tool.ts) 的调用身份与重放、
[agent-loop.ts](https://github.com/earendil-works/pi/blob/main/packages/agent/src/agent-loop.ts)、
[agent.ts](https://github.com/earendil-works/pi/blob/main/packages/agent/src/agent.ts) 和
[types.ts](https://github.com/earendil-works/pi/blob/main/packages/agent/src/types.ts) 的输入接收边界。
仅借鉴语义，不引入 Pi 依赖，也不把其输入队列称为现成的 Goal Clarification。

## 2. 修改文件

| 文件 | 关键改动 |
|---|---|
| `backend/tools/clarification.py` | Goal 控制工具、Schema、选项 ID/推荐选项/可回答性校验、提问策略；不进入全局业务工具注册表 |
| `backend/agent/round_outcome.py` / `loop.py` | 新 Outcome、Goal 动态暴露、整批校验、立即结束、调用身份、恢复 Checkpoint 的临时投影 |
| `backend/services/store.py` | 新表/部分唯一索引、请求与回答事务、状态入口保护、真实用户消息、取消失效处理 |
| `backend/services/goal_worker.py` | 处理 Outcome，从持久化声明/回执恢复；澄清不创建外部副作用 Intent |
| `backend/services/goal_runtime.py` | 读取/回答 API、会话筛选与澄清快照、等待状态下禁止绕过回答 |
| `frontend/src/lib/api.ts` | Goal/Clarification 类型及请求封装 |
| `frontend/src/components/GoalClarifications.vue` | 问题、选项、自由文本、防重、错误、已回答与失效状态 |
| `frontend/src/pages/ChatPage.vue` / `components/AgentChatTab.vue` | 接入同一卡片，控制工具不额外渲染空业务结果卡 |
| `backend/tests/test_goal_clarification.py` | 23 项功能/并发/故障/隔离测试 |
| `backend/tests/goal_clarification_fixture.py` / `.e2e/test-goal-clarification.js` | 真实 REST/SQLite/Worker/Chrome，Mock 模型和 Fake Executor 的四条浏览器验收链 |
| 文档索引、交接与 `docs/planning/api-contract.md` | 契约、流程与验收入口 |

`loop.py`、`goal_worker.py`、文档原有改动保留。已有 SR、PAN 修复、Viewer 快照、Runtime Status、结果预算、Skill 和工具描述改动未回滚。

## 3. 执行闭环

```mermaid
flowchart LR
  A[原 Goal 的有限 Round] --> B[request_clarification 单独调用]
  B --> C[持久化 Assistant 声明与 Tool 结果]
  C --> D[NEEDS_CLARIFICATION]
  D --> E[事务：问题 + WAITING_USER + requested 事件 + 释放 Lease]
  E --> F[用户选择或自由文本回答]
  F --> G[事务：真实用户消息 + ANSWERED + answered 事件 + QUEUED]
  G --> H[原 Worker 领取同一 Goal]
  H --> I[复用历史和 Checkpoint 继续执行]
  I --> J[原有服务端 Verifier 独立验收]
```

- 同批混入任何其他调用时，在声明/Intent/任何工具执行前拒绝整批，向模型给出单独成批的纠正信息。
- 控制工具使用现有 Registry 校验，不产生业务副作用；业务工具仍经原 Intent Ledger 和安全边界。
- Outcome 携带 `goal_id/run_id/request_key/tool_call_id/question/options/allow_free_text`；澄清永不成为完成候选。
- `request_key = session_id:assistant_ordinal:tool_call_id`；Assistant 保存 `goal_id/goal_run_id`。恢复隔离不同 Goal，也允许不同 Round 复用 Call ID。
- 回答后保留 `goal_id/session_id/checkpoint`，不重插初始指令。模型看到原历史、问题/工具结果、真实答案和最新 Checkpoint；内部关联字段不进入 provider wire 字段。
- 回答沿用提问时的 Viewer 观察，不被等待期间其他普通聊天的新快照替换。Checkpoint 只在模型请求临时投影，不反复写历史。
- 页面每 2.5 秒一次会话 Goal 快照请求刷新，不依赖 SSE 常连；提交直接采用服务端提交结果。切会话/卸载取消读取，丢弃迟到结果。HTTP 页面用 `crypto.getRandomValues` 生成幂等键，不依赖 `randomUUID`。

## 4. 数据库与事件

打开旧 SQLite 时通过 `CREATE TABLE/INDEX IF NOT EXISTS` 增量创建 `agent_goal_clarifications`，不重建原 Goal、消息、事件或 Intent 表；旧库增量升级有自动化覆盖。

保存 `clarification_id/goal_id/run_id/request_key/question/options_json/allow_free_text/recommended_option/status/answer_json/answer_key/message_seq/version/created_at/answered_at`。

- 请求状态为 `PENDING/ANSWERED/CANCELLED`。
- `UNIQUE(goal_id,request_key)` 防止同一次声明重复建问题；部分唯一索引 `UNIQUE(goal_id) WHERE status='PENDING'` 保证每 Goal 最多一个待回答问题。
- 创建事务：请求 + `RUNNING → WAITING_USER` + Checkpoint + Goal version + `clarification_requested`（含问题、选项、run_id、澄清 ID）+ 释放 Lease。
- 回答事务：校验 `PENDING + WAITING_USER` + 关联的真实 user 消息 + `ANSWERED` + `clarification_answered`（含答案、消息序号、澄清 ID）+ 同一 Goal `QUEUED`；Checkpoint 原样保留。
- 事件沿用每 Goal 单调 `seq`，不创建新事件流。取消 Goal 时待回答请求同事务改为 `CANCELLED`。
- 普通 resume/pause 或挂接外部作业不能绕过回答。同键同答案返回现有结果；不同键/内容竞争回答返回 409。终结后重放已成功的同一请求不产生状态变更。

## 5. 并发与故障结果

| 注入点/竞争 | 实测结果 |
|---|---|
| 声明已提交，Tool 结果未写即中止 | 新 Store/Worker 重读声明，校验并补写结果及问题，不再请求模型 |
| Tool 结果已提交，requested 事件插入后、事务提交前中止 | 问题/状态/事件全部回滚；重启从原声明完成提交，仅一个问题 |
| answered 事件后、事务提交前中止 | 用户消息/答案/排队一起回滚，可重新有效提交 |
| 回答提交后、Worker 领取前重启 | 同一 Goal 的 QUEUED、历史及 Checkpoint 可继续 |
| 两个 SQLite 连接同时回答 | 一次成功、一次冲突，仅一条答案消息和一次 answered 事件 |
| 重复提交/终态后重放 | 不重复消息或排队，不唤醒终态 Goal |
| 旧 Worker 过期 Token | 拒写问题、消息，不覆盖新状态 |
| WAITING_USER 反复 tick/刷新 | 模型调用数不增加，Worker 不可领取 |
| 混合批次、自然语言确认、无效 Schema/选项 | 不产生澄清等待或业务副作用 |
| 多 Goal 共用会话、同 Goal 多次澄清复用 Call ID | 不串问题，旧回答不被恢复为待回答 |
| 浏览器丢失已提交的回答响应 | 重读服务端为已回答，仅执行一次 |

Python 注入使用 `BaseException` 中止并重开 Store/Worker，覆盖明确的持久化窗口；不将它描述为生产服务强杀验收。

## 6. 运行命令与实测

Windows 先设置 `PYTHONUTF8=1`，将 `D:\Git\usr\bin;D:\Git\bin` 置于 PATH 前部。首次误用系统 Bash 的全量运行已终止，不计为通过。

```powershell
& '.\.venv\Scripts\python.exe' -m unittest backend.tests.test_goal_clarification -q
& '.\.venv\Scripts\python.exe' -m unittest backend.tests.test_goal_clarification backend.tests.test_goal_runtime backend.tests.test_agent_loop backend.tests.test_store backend.tests.test_tool_result_formatter backend.tests.test_tools_registry -q
& '.\.venv\Scripts\python.exe' -m pytest backend/tests -q
cd frontend
npm test -- --reporter=dot
npx vitest run src/stores/__tests__/chatImages.test.ts --reporter=dot
npm run typecheck
npm run build
cd ..
node .e2e/test-goal-clarification.js
& '.\.venv\Scripts\python.exe' -m compileall -q backend
git diff --check
```

- 澄清专项：23/23 通过。相关模块回归当次 130/130 通过，随后补充的等待期 Viewer 快照隔离用例也通过。
- 全量后端 pytest：**1027 通过、1 失败、5 跳过**，另有 168 个 subtest 通过，耗时 98.52 秒；涵盖 Runtime Status、工具描述、结果预算与参数校验。唯一失败原因见下文。
- 前端 Vitest：490 通过、1 失败（共 491）。失败为未改动的 `chatImages.test.ts:123` 旧断言，要求无图请求体只有 `content`，而当前发送已有 `viewer_context`；隔离复跑为 8 通过、同一项失败。未删除 Viewer 上下文以迁就旧断言。
- TypeScript、Vite build、Python compileall、diff 检查通过；Vite 仍报告既有大 chunk 警告。
- 浏览器四条链通过：选项/等待时刷新/双击防重/已回答刷新；自由文本完成；取消后失效；提交响应丢失后恢复服务端状态。

完整浏览器 Mock：四个原 Goal 中三个完成、一个取消；模型调用共 **10** 次、Fake Executor **3** 次，等待/刷新零额外模型调用。示例 ID 为 `4b710266c7e4437895d587392a1b552f`（选项）、`fc021134359e4426bf8c9b43278ba646`（文本）、`ed499cfdf1f849d5b07b891093d7d128`（取消）、`239fcd8f294f4566922789a4b7dc0ad7`（响应丢失）。临时数据库与截图由脚本保存在系统临时目录；脚本自身入库。

全量后端已定位失败：`test_goal_process.py::GoalProcessAcceptance::test_two_jobs_http_disconnect_and_hard_backend_restart`。隔离复跑仍失败；两次物理 SR 提交均完成，但当前内置 `run_sr` 使用 PAN 修复契约，旧 Fake 夹具仅生成通用 SR 产物，缺少 `Debug/_SRREPAIR_<fingerprint>.json`，Verifier 拒绝验收，最终耗尽预算。此用例不发起澄清；未绕过 Verifier、伪造修复凭据或修改已有 PAN 行为使其转绿。

## 7. 待验收与限制

- 环境未配置自定义 LLM 端点、模型或 API 凭据（只检查布尔值，未输出密钥）。真实模型的先查后问、问题质量、是否重复询问，仍需实际验收，Mock 不替代效果验收。
- 内网生产 GPU、盘阵权限、大图及生产进程强杀验收待核。原 UNKNOWN 外部作业继续安全阻断，不借澄清改为自动重投。
- 普通聊天沿用原同步/SSE 链路；未新增 Goal 创建页面，持久化任务仍通过既有 Goal API 创建。
- 卡片轮询约有最多 2.5 秒同步延迟，展示当前会话最近 500 个 Goal。原单进程会话锁及多进程共用会话限制沿用 Runtime 说明。
- PAN 夹具契约失败与前端旧断言尚未处理，不能声称全仓回归全绿。
