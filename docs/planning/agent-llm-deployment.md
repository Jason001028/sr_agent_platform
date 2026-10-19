# Agent 本地大模型部署 —— 选型 / 接缝验收 / 阻塞认知

> 日期：2026-09-09 · 状态：**草稿**（评审中；结论未拍板，待补硬件与规模参数后回填）
> 目标读者：后续 Agent 会话 / 前后端接缝实现。范围：Agent 对话 LLM 的**本地化部署决策**——它决定 agent 页调试、接口标准、工具集成标准与框架生态的走向。
> 前置：阶段 5 REST/SSE 已交付（`backend/api` + 前端 `/chat`）；阶段 6 viewer 右侧上下文侧舱已落地（`ContextPanel`/`RoiToolsTab`/`AgentChatTab`，Agent 侧已实现"同 session + 自动附上下文快照"，但**没有真模型只能跑 mock**）。

## 0. 一句话总结

后端已把 Agent LLM 做成**端点无关**（OpenAI 兼容 wire + env 配置），因此"换本地模型"不是前后端代码改造；但 **Agent 功能要在离机盘阵机上真正点亮，完全取决于"本地部署 + 选型 + 工具调用验收"这条未决工作流**——这是当前**主要障碍**，也是后续 agent 页接口 / 工具 / 生态标准的实际决定者。本文件把这些决策点、验收法、排期写下来，避免部署问题被淹没在前后端功能推进里。

## 1. 已锁定的契约面（不需要再决策，本地模型必须迁就它们，而非反之）

| 层面 | 现状落点 | 结论 |
|---|---|---|
| LLM 接口 | `OpenAI(base_url, api_key)`，env：`SR_LLM_BASE_URL / API_KEY / MODEL / MAX_TOKENS / TEMPERATURE / TIMEOUT / MOCK` | `backend/config.py` + `backend/agent/loop.py`；本地端点 api_key 留空即可 |
| 工具集成 | `name + arguments(json)` 函数调用；manifest 由 `backend/tools/contract.py` 生成 | 模型必须能稳定 follow function-calling |
| 网页契约 | 聊天 REST + 单回合 SSE；事件形状见 api-contract §3.2；mock 假 LLM 走真实 `search_scenes` 工具回合（§5.1） | 本地模型上线后**不改**前端 / SSE / ChatMsg |
| 会话/持久化 | 追加式 transcript → SQLite（`backend/services/store.py`）；"副作用前落库" | 与模型无关 |
| 框架生态 | Agent 是自研 M1 薄状态机，**不运行时引 langchain/langgraph**；vendored 仓库（langchain-master/langgraph-main/deepseek-harness-master）仅作参考 | 参考 `docs/knowledge/agent-orchestration-research.md` §5(M1) 与 `docs/conventions/langchain-boundary.md` |

> 推论：选型不需要回答"前端/后端要不要重写"（不重写）；只需要回答三件事——**硬件、serving 层、模型本体**。

## 2. 当前主要障碍：本地部署未落地（为什么它是阻塞）

- AgentChatTab / /chat 在盘阵离机环境的真实价值，**被"有没有一个可达的本地模型端点"完全 gate**：现在只有 `SR_LLM_MOCK=1` 假脚本能跑，工具回合真，但结论假。
- 本地部署决策链条环环相扣、且悬而未决：
  1. **硬件未知**：无 GPU 型号 / 显存数 → 无法定模型体量；
  2. **serving 层未知**：vLLM / Ollama / llama.cpp-server 对"严格 OpenAI 兼容 + 工具支持"的程度差别大；
  3. **模型本体未知**：候选（Qwen / GLM / Llama 指令版）必须先过"工具调用可靠性"这关。
- 这三件事一天不拍板，Agent 后续的调试 / 接口微调 / 工具 schema 演进就都在"假跑"上推演，属于**纸上谈兵**——必须把它作为近期主线，而不是夹在前端功能后面顺手做。

## 3. 需要决策的三个维度（开放项，决定后回填）

1. **硬件预算**（阻塞其它两项）：目标盘阵机 GPU 型号 / 显存；是否与 SR 超分管线（SR_code 生产管线）**同卡争抢** → 决定最大能跑多大体量、是否需 Ollama（省显存调度）还是 vLLM（吞吐/工具支持）。
2. **serving 层**：vLLM（严格 OpenAI 兼容、tools 支持最好，推荐度最高）/ Ollama（省事）/ llama.cpp-server。
3. **模型本体**：选型标准 = **function-calling 可靠性 + 中文 + SR 域**，不是通用榜单分。默认**不需要 vision**（Agent 是对话，不读图）。

## 4. 选型验收法（用自己的 agent 当试金石，别用通用 benchmark）

同一套 `backend/tools/contract.py` 工具清单，对每个候选模型跑固定剧本，量三项指标：

1. 工具参数 JSON 解析成功率（`search_scenes → 总结` 单跳）；
2. 多跳闭环（再叠 `run_sr` 一类的依赖链）能否自圆其说；
3. 工具失败回喂后能否自我修复（loop 把 tool error 回喂模型，不许盲重试），以及是否"自说自话不回工具"。

冒烟入口（现成）：

```bash
python -m backend.agent --max-turns 8 "在盘阵里找一张…"   # 真端点：设好 SR_LLM_*
SR_LLM_MOCK=1 python -m backend.agent --tools            # 先确认 manifest 可出
```

端到端（本地模型起后再做）：起后端 FastAPI → 前端 `/chat` 与 viewer Agent 侧舱真会话 → SSE 工具回合可视化（对齐阶段 5/6 的验收口径）。

## 5. 决策的连锁影响（为什么说它"决定后续标准"）

| 若部署方向是… | 对 agent 页调试 | 对工具集成 | 对框架生态 |
|---|---|---|---|
| OpenAI 兼容 + 工具调用稳定（目标态） | 现状即最终形态，调试 = 追 SSE 事件 | 维持 `name+arguments` manifest，无需兜底 | 保持无 langchain/langgraph，模型可随时换 |
| serving 层 tools 支持差 | 需加提示词内嵌 tools 或换 vLLM，页面侧新增兼容分支 | 工具契约被稀释 | 被迫引入适配层，违反"薄"原则 |

> 上下文长度：场景 / 工具结果可能很长 → 影响 `max_tokens`、SSE 消息裁剪、是否需要分页；属 env/契约层可调项，别在设计期锁死。

## 6. 近期排期建议

1. **主线（阻塞项）**：确认硬件 → 选 serving + 候选模型 → 按 §4 冒烟验收 → 定档 → env 指向本地端点，端到端点亮 Agent。
2. **空档才碰（不阻塞）**：前端纯皮肤（图标/品牌/空态图，皮肤层不依赖模型）。
3. **明确不做**：新功能、新后端契约、UI 结构重构；运行时引 langchain/langgraph。

## 7. 待拍板（开放问题）

- [ ] 目标盘阵机 GPU 型号 / 显存；是否与 SR 超分管线同卡。
- [ ] 模型体量倾向（30B+ 级 vs 7B~14B 级）。
- [ ] serving 层倾向（vLLM / Ollama / 待定）。
- [ ] 是否已有可用的离线真机可跑真模型（还是继续 mock 推进 UI）。
- [ ] 期望时间窗（决定主线与皮肤线怎么排）。

## 8. 关联文档

- 接口契约：`docs/planning/api-contract.md`（§3.2 聊天 / §5.1 mock）
- 前端阶段线：`docs/planning/frontend-migration.md`（阶段 6 侧舱已落地）
- Agent 架构取舍：`docs/knowledge/agent-orchestration-research.md`（M1 / 不引 langchain）
- 边界约定：`docs/conventions/langchain-boundary.md`
- 生态名词科普 + 选型对照（§3/§7 开放项的 2026-09-09 方向：3090+CentOS7 → GGUF Q4_K_M + llama.cpp）：`docs/knowledge/llm-serving-gguf-glossary.md`
- 代码：`backend/config.py` · `backend/agent/loop.py` · `backend/tools/contract.py` · `frontend/src/components/AgentChatTab.vue`
