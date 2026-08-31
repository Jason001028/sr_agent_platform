# Agent 编排框架调研：langchain / langgraph / deepseek-harness 对比

> 日期：2026-08-31 · 状态：已定
> 用途：为平台 Agent 层选型提供依据，并作为从三个参考项目中借鉴模块实现方法的索引。
> 阅读顺序：先读 §1 结论摘要，再按需读各维度和 §6 借鉴清单。

---

## 1. 结论摘要

三个参考项目对本平台的定位：**langchain 是参考书，langgraph 是断点恢复时的替换薄层，deepseek-harness 是设计蓝本（不移植）**。

- **Agent 编排内核维持自研**（现有 `backend/tools/contract.py` 工具契约 + 自写状态机），运行时**不引入 langchain / langchain-core / langgraph 依赖**（延续 langchain-boundary.md 决策）。
- **断点恢复（跨小时 + 人审）**是三个需求中唯一需要现成轮子的维度。若确认需要，评估对象是 **LangGraph**：`checkpoint-sqlite` + `interrupt`/`Command`，唯一开箱即用且 FastAPI in-process 友好（`AsyncSqliteSaver`）的库级方案。langchain 本体无断点能力。
- **无论如何必自研**：外部副作用（Slurm GPU job）的**幂等层**。checkpoint 只存图内状态，不感知外部 job；崩溃重放会重复提交任务。这层是所有方案（langgraph / 自研 / harness）的共同缺口，且与选型无关。
- deepseek-harness **不移植**（Node+TS+Cordis 生态与 FastAPI + Python 工具组合不匹配），但其"事件溯源日志做断点"与"崩溃时未闭合 turn 补合成错误"两个设计可直接对标。

> **工具粒度边界**：`SR_code/code_0820_prod_windows.py` 的主流程（config → 输入发现 → 掩码读取 → MTA-Grid 规划 → pad → 逐 tile 超分 → 回填）是一条**固定顺序的子进程管线**，它只是 Agent 工具库中 `run_sr` 这一个工具的**内部实现**。工具库 = 流程级操作集合（run_sr / 批量 / 检索 / cv 修复），各工具的 `run()` 内部可有任意复杂流水线；Agent 编排层处理的是"选哪个工具、按什么顺序、失败怎么恢复"，两者是不同层级，后者不会因前者简单而变小。

---

## 2. 三框架本质定位

| 框架 | 本质 | 形态 | 对本平台 |
|---|---|---|---|
| langchain-master | 工具契约 + LLM 适配库；其 agent 循环（`create_agent`）内部就是构建 LangGraph StateGraph | 库 | 参考书 |
| langgraph-main | 状态图编排 + checkpoint 断点恢复的库，唯一自带断点基础设施 | 库 | 断点恢复时的替换薄层 |
| deepseek-harness | 完整可运行产品（Node ≥22 + Cordis 插件树），Python SDK 仅 stdio 薄壳 | 产品 | 设计蓝本，不可移植 |

关键事实：langchain 本体**没有断点恢复**（`langchain_core` 全文无 checkpoint），`create_agent` 的 `checkpointer=` 参数直接透传给 langgraph。因此"断点恢复"在 langchain 方案里必然落到 langgraph，或完全自研。

---

## 3. 四维对比

### 3.1 长任务规划执行

| 项目 | 能力 | 机制 | 适配性 |
|---|---|---|---|
| langchain | 中 | 无独立 planner；`TodoListMiddleware`（[todo.py:174](langchain-master/libs/langchain_v1/langchain/agents/middleware/todo.py#L174)）是"LLM 自维护 todos"的弱规划，靠 prompt；`create_agent` 循环实际是 langgraph 图（[factory.py:1185](langchain-master/libs/langchain_v1/langchain/agents/factory.py#L1185)） | 无增量价值 |
| langgraph | 高 | `StateGraph`：State 用 `TypedDict + Annotated[key, reducer]`（[state.py:131](langgraph-main/libs/langgraph/langgraph/graph/state.py#L131)）；节点+条件路由可成环、`Send` 并行 fan-out、子图 | 匹配"掩码→规划→推理→报告"多阶段流水线，尤其支持"报告不合格→回退重规划"循环 |
| deepseek-harness | 高 | goal-round-driver 自动续跑 + plan-mode/todo/goal/schedule 日志外挂，属产品形态 | 思路可借鉴，形态绑定 |

### 3.2 自主工具选择

| 项目 | 能力 | 机制 | 借鉴点 |
|---|---|---|---|
| langchain | 高（契约层） | `@tool`（[convert.py:77](langchain-master/libs/core/langchain_core/tools/convert.py#L77)）docstring+签名 → pydantic 推断 schema；`convert_to_openai_tool` 序列化（[function_calling.py:515](langchain-master/libs/core/langchain_core/utils/function_calling.py#L515)）可剥离 | 与现有 `contract.py` 同源，无新信息 |
| langgraph | 高 | `ToolNode` 并行执行 tool_calls（[tool_node.py:743](langgraph-main/libs/prebuilt/langgraph/prebuilt/tool_node.py#L743)）；`handle_tool_errors` 把异常转 `ToolMessage(status="error")` 回喂模型自愈（[tool_node.py:394](langgraph-main/libs/prebuilt/langgraph/prebuilt/tool_node.py#L394)）；节点级 `RetryPolicy`/`TimeoutPolicy` | **工具异常回填**语义值得抄 |
| deepseek-harness | 高 | `ctx.tools` 注册表（[tools/src/index.ts:780](deepseek-harness-master/packages/core/tools/src/index.ts#L780)）；执行前门控瀑布 allow/deny/ask→审批→guard；MCP 桥自动注册 | **执行前门控**（可审计/可拦截）可借鉴 |

### 3.3 断点恢复（核心需求）

| 项目 | 能力 | 机制 |
|---|---|---|
| langchain | 低（无） | 无 checkpoint，需引 langgraph |
| langgraph | 高（图内） | 每 superstep `_put_checkpoint` 落盘（[pregel/_loop.py:1081](langgraph-main/libs/langgraph/langgraph/pregel/_loop.py#L1081)）；`put_writes` 存中间写（`checkpoints`+`writes` 两张表，[sqlite/__init__.py:142](langgraph-main/libs/checkpoint-sqlite/langgraph/checkpoint/sqlite/__init__.py#L142)）；`interrupt()` 抛 `GraphInterrupt`（[types.py:851](langgraph-main/libs/langgraph/langgraph/types.py#L851)），同 `thread_id` + `Command(resume=...)` 恢复、节点重放；time-travel 用 `get_state`/`update_state`。**跨进程恢复不需 Server**：任意进程 + 同一 sqlite + `thread_id`；`AsyncSqliteSaver`（[aio.py:38](langgraph-main/libs/checkpoint-sqlite/langgraph/checkpoint/sqlite/aio.py#L38)）嵌 FastAPI  |
| deepseek-harness | 高（会话级） | append-only 事件日志（`session.jsonl.zstd`），存 `tool/call`、`tool/result`、`request/header`（配置+工具 schema 快照）；LLM 历史不单独存，`deriveMessages()` 从日志投影（[session/src/index.ts:724](deepseek-harness-master/packages/core/session/src/index.ts#L724)）；强一致 checkpoint 三个时机：每次模型请求前/每个工具副作用前/agent 每步前（[checkpoint-policy/src/index.ts:63](deepseek-harness-master/packages/session/session-checkpoint-policy/src/index.ts#L63)）；崩溃修复 `interruptedTurnClosers()` 把未闭合 turn 补合成错误 `tool/result`（[repair.ts:28](deepseek-harness-master/packages/core/session/src/repair.ts#L28)）。**局限**：`LocalJobRegistry` 纯内存，后台子进程重启即失 |

**共同边界（决定成败）**：checkpoint 存的是图内/会话内状态，**不感知外部 Slurm job**。进程在 `run_sr` 工具执行中崩溃 → 重放会重跑该节点 = 重复提交 job。必须把 `job_id/状态` 幂等存入 state 或 SQLite 任务表，恢复时先查 squeue/sacct 再决定重跑或续查。这一层与选型无关，自研和 langgraph 都要写。

### 3.4 业务适配性

| 项目 | 强适配点 | 硬伤 |
|---|---|---|
| langchain | 工具契约思想已被 `contract.py` 吸收 | 断点=0；依赖重（langgraph≥1.2.11 + pydantic≥2.7.4，与现状冲突已记录） |
| langgraph | checkpoint-sqlite + interrupt 是"跨小时断点+人审"的现成答案；FastAPI in-process 直接调 | 强制 langchain-core + pydantic 版本；sqlite-vec 原生扩展需内网备轮子；prebuilt API 迁移中（create_agent 正搬去 langchain 包）版本碎片化 |
| deepseek-harness | 断点/工具抽象的设计蓝本；Python SDK 可带单文件 Node exe 做黑盒 POC | Node+Cordis+TS 与 FastAPI+Python 工具+单人维护不匹配；移植=引入 Node 子进程+工具 TS 化+重写 LLM 适配器 |

---

## 4. 断点恢复生命周期（langgraph 参考）

```
thread_id=SR-001, checkpointer=AsyncSqliteSaver
invoke(...) → 节点执行 → put_writes(中间写) → _put_checkpoint(每superstep)
  → interrupt("审批推理计划?") → 抛 GraphInterrupt → 状态已落盘 → 返回
        ↓（进程可重启；外部 GPU job 由平台自己轮询）
同 thread_id invoke(Command(resume=...)) → get_tuple 读最新checkpoint
  → 重放被打断节点 → 续跑 → END 或新 interrupt
```

---

## 5. 落地建议

1. **M1（秒级验证闭环）**：维持现状决策——不引任何库。自写状态机 + `contract.py` 工具契约。工具串行阻塞 + 退出码判定，无需图路由。断点恢复用 SQLite 消息持久化（任务表本就计划 SQLite）。
2. **M3（生产端到端 / 跨小时断点确认后）**：上 LangGraph，仅作编排薄层替换（非重写）。`AsyncSqliteSaver` 嵌 FastAPI，`interrupt` 做人审，`Command` 恢复。省掉"每步自动 checkpoint（含 pending writes）+ interrupt/恢复原语 + time-travel"，这些自写最难做对。
3. **必写层（现在就该设计）**：Slurm job 外部副作用幂等层。`job_id` 写入任务表，恢复时先查 squeue/sacct。建议 `backend/services/runner.py`（或对应任务表）按可幂等恢复设计。
4. **deepseek-harness**：不移植。仅借鉴设计（见 §6）。

---

## 6. 借鉴清单（从三个项目里抄实现方法）

> 用法：每条含代码位置 + 借鉴内容 + 落到平台的位置。只借鉴机制，不引依赖。

### 6.1 工具契约 / schema

| 来源 | 代码 | 借鉴内容 | 落点 |
|---|---|---|---|
| langchain-core | [convert.py:77](langchain-master/libs/core/langchain_core/tools/convert.py#L77) | `@tool` 装饰器 + docstring→pydantic schema | 已由 `backend/tools/contract.py` 实现，无新信息 |
| langchain-core | [function_calling.py:515](langchain-master/libs/core/langchain_core/utils/function_calling.py#L515) | BaseTool → OpenAI tool JSON 序列化（约 50 行） | 可选剥离；`contract.py` 手写 schema 已产出 OpenAI 兼容清单，**收益低** |
| langchain-core | [base.py:1756](langchain-master/libs/core/langchain_core/tools/base.py#L1756) | `InjectedToolCallId` 运行时注入 tool_call_id，日志关联 | 自研时在 tool 调用记录中带上 call_id |

### 6.2 工具执行 / 错误处理

| 来源 | 代码 | 借鉴内容 | 落点 |
|---|---|---|---|
| langgraph-prebuilt | [tool_node.py:394](langgraph-main/libs/prebuilt/langgraph/prebuilt/tool_node.py#L394) | `handle_tool_errors`：工具异常 → `ToolMessage(status="error")` 回喂模型自愈，不中断循环 | `agent/loop.py` 异常分支（与 `contract.py` 的 `ok/err` 配合） |
| langgraph-prebuilt | [tool_node.py:743](langgraph-main/libs/prebuilt/langgraph/prebuilt/tool_node.py#L743) | `ToolNode` 并行执行 + `InjectedState`/`InjectedStore` 注入 | 平台工具并行度低，可先忽略；保留注入思路 |
| langgraph | [types.py:418](langgraph-main/libs/langgraph/langgraph/types.py#L418) | 节点级 `RetryPolicy`（backoff/jitter/retry_on）+ `recursion_limit` 防死循环 | `loop.py` 加迭代上限 + 重试策略 |

### 6.3 断点恢复

| 来源 | 代码 | 借鉴内容 | 落点 |
|---|---|---|---|
| langgraph-checkpoint | [sqlite/__init__.py:142](langgraph-main/libs/checkpoint-sqlite/langgraph/checkpoint/sqlite/__init__.py#L142) | `checkpoints`+`writes` 两表：一表存每步图状态快照，一表存未完成中间写 | 若自研，任务表可仿此结构（状态快照表 + pending writes 表） |
| langgraph | [types.py:851](langgraph-main/libs/langgraph/langgraph/types.py#L851) | `interrupt()` + `Command(resume=...)` 人审原语 | M3 评估；自研时可用"等待外部确认"状态模拟 |
| deepseek-harness | [checkpoint-policy/src/index.ts:63](deepseek-harness-master/packages/session/session-checkpoint-policy/src/index.ts#L63) | 强一致 checkpoint 时机：每次模型请求前 / 每个工具副作用前 / agent 每步前 | **最值得借鉴**：断点粒度 = 副作用前 |
| deepseek-harness | [repair.ts:28](deepseek-harness-master/packages/core/session/src/repair.ts#L28) | 崩溃修复：未闭合 turn 补合成"结果未知、勿盲目重试"错误，不截断 | 自研恢复逻辑按此处理中断的工具调用 |
| deepseek-harness | [session/src/index.ts:724](deepseek-harness-master/packages/core/session/src/index.ts#L724) | LLM 历史不单独存，由事件日志投影重建 | 自研持久化可参考（存事件，不存消息数组） |

### 6.4 规划 / 编排思路

| 来源 | 代码 | 借鉴内容 | 落点 |
|---|---|---|---|
| langgraph | [state.py:131](langgraph-main/libs/langgraph/langgraph/graph/state.py#L131) | `TypedDict + Annotated[key, reducer]` 状态建模 + 条件路由 | M3 评估；自研阶段仅需顺序状态机 |
| deepseek-harness | [tools/src/index.ts:780](deepseek-harness-master/packages/core/tools/src/index.ts#L780) | 工具注册表 + 模型侧白名单投影（只暴露 name/description/parameters） | 与 `contract.py` manifest 思路一致，已实现 |
| deepseek-harness | 执行前门控瀑布（allow/deny/ask） | 工具执行前可审计/可拦截 | 平台多人共享队列场景下，可做人审门控 |

---

## 7. 关键文件索引

- 工具契约：`backend/tools/contract.py`（自研，langchain `@tool` 同源）
- 任务表 / runner：`backend/services/runner.py`（幂等恢复层落点）
- Agent 循环：`backend/agent/loop.py`（待建，自研状态机；异常分支借鉴 §6.2）
- SR 子进程管线：`SR_code/code_0820_prod_windows.py`（`run_sr` 工具的 `run()` 内部实现，非 Agent 编排层）
