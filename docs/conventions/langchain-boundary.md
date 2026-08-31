# langchain 使用边界

> 日期：2026-08-31 · 状态：已定
> 用途：记录 `langchain-master/` 在平台代码中的使用边界，供后续窗口参考，避免重复决策。

## 决定

`langchain-master/` 只作参考，不 import、不安装、不引入平台代码。

## 理由

- Agent 层价值在工具侧（run_sr / mta_grid / 掩码 / Slurm），循环本身仅需约 200 行状态机。
- langchain-core 依赖 pydantic≥2.7.4，与平台现状存在版本冲突，引入即连带依赖升级。
- 所需模式均可用自有代码实现；循环自持，行为可控、可单测。

## 已有自有实现

| 模式 | 落点 |
|---|---|
| 工具契约 / schema / manifest | `backend/tools/contract.py` |
| OpenAI 兼容线格式（tools / tool_calls） | 权威参考 = OpenAI API 规范，非 langchain |
| 工具异常回填、迭代上限 | `backend/agent/loop.py`（待建） |

## 边界

- Agent 编排内核必须自研，运行时不引入 langchain /langchain-core 依赖
- 例外：出现跨小时断点续跑 / 人审需求时，评估 LangGraph（替换薄层，非重写）。
- **断点恢复的具体落点**（详见 docs/knowledge/agent-orchestration-research.md §3.3 / §5）：
  - 若上 LangGraph，用 `checkpoint-sqlite`（`AsyncSqliteSaver` 嵌 FastAPI）+ `interrupt`/`Command`，跨进程恢复不需 Server。
  - **外部副作用（Slurm job）幂等层必须自研**：`job_id/状态` 幂等存入任务表，恢复时先查 squeue/sacct 再决定重跑或续查。这与选型无关，自研状态机同样要写。
