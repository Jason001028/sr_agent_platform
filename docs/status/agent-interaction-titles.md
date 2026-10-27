# Agent 中文交互与会话标题（2026-10-27）

本轮实现默认中文提示词、中文执行概览、独立自动命名任务和手动改名。附件原文在 §6.3 开头截断，本报告按收到的要求实现，并补充异常恢复。

## 实现与使用

- `backend/agent/loop.py`：仅更换默认系统提示词，工具事件附带原调用 ID；不改变 `run_loop()` 参数、自定义提示词、历史重放、无 Prompt Resume、预算、Verifier、工具契约或 Goal 完成条件。
- `/chat` 和查看器 Agent 页签共用 `phaseLabel`，当前阶段在对应助手消息内展示（替代“推理中”），不再在消息列表顶部单独展示“思考与执行”。工具状态由原有卡片展示，按调用 ID 去重；无原始推理也可显示阶段。正文保持原有 50ms 批量流式合并。
- 原始推理仍由 `ReasoningBlock` 提供，仅开发者模式可见，默认折叠，标签为“深度思考中”，使用 10px 字号、弱辅助色和常规字重。退出开发者模式时隐藏标签和原始内容，当前任务阶段在普通模式下仍可见。原始推理不翻译、不落库，也不保证完全中文。
- `/chat` 历史行新增“重命名”；手动标题可在自动生成过程中提交。两处历史列表均显示持久化标题，旧会话显示短 ID。手动输入允许其他语言，限制 1—30 字符并拒绝控制字符。
- `PATCH /api/chat/sessions/{sid}` 接受 `{"title":"GF-2影像检索"}`，原 `{"pinned":true}` 保持兼容。列表返回 metadata；SSE 新增 `session_update` 返回临时名称。自动任务不延长聊天 SSE；客户端每 3 秒同步列表，过滤手动改名期间过期的读取结果。

## 持久化和并发

不增加表。首条 user 消息的 INSERT 和标题初始化处于同一 SQLite 写事务，metadata 使用 `json_set` / `json_remove` 定点更新，保留其他键与置顶字段，不修改活动时间。运行环境需 SQLite JSON1（验证命令：`python -c "import sqlite3; print(sqlite3.connect(':memory:').execute(\"select json_valid('{}')\").fetchone())"`）。

| 状态 | title_source | title_status | 含义 |
|---|---|---|---|
| 临时名称 | fallback | pending | 已保存首问，等待主回合结束 |
| 已领取 | fallback | processing | 90 秒 claim，有随机 token |
| 自动完成 | auto | ready | 一次成功提交后不再领取 |
| 失败回退 | fallback | failed | 确定性截取首问，不自动无限重试 |
| 手动名称 | manual | ready | 清除 claim，自动结果无权覆盖 |

领取和提交均为带条件的单条 UPDATE。提交检查来源、状态、token 和有效期；手动改名、过期响应、重复任务及删除会话都不能被旧结果覆盖。标题写入不增加消息、Trajectory、LLM 使用量或 Goal 状态。旧会话不批量补生成。

FastAPI lifespan 增加一个低优先级任务，每 2 秒最多领取一条，扫描最多 20 条 pending / 过期 processing。当前进程有聊天锁、尚未结束的聊天线程（包括 SSE 断开后仍执行的回合）、或最近 5 分钟有 running Agent Run 时暂缓。正常首轮结束、失败或客户端断开后均可恢复；进程崩溃的遗留 Run 最多延缓 5 分钟，过期 claim 可重新领取。任务仅持有应用 store/config，不依赖 HTTP 响应、Request 或临时数据库连接。关闭时等待正在进行的有界标题调用结束再关闭 Store。

默认只在主回合后调度；即使配置独立模型，本 P0 仍采用此保守时机。标题请求只发送首问文本（上限 2000 字符），剥离自动附加的查看器上下文与图片 data URL。不发送图片、工具结果或完整历史；纯图片直接使用“遥感影像分析”。

## 配置和兼容性

| 环境变量 | 默认值 | 用途 |
|---|---|---|
| SR_TITLE_MODEL | 主模型 | 未来可指定小型任务模型 |
| SR_TITLE_BASE_URL | 主端点 | 可指定独立推理服务 |
| SR_TITLE_API_KEY | 主服务密钥 | 独立服务凭据 |
| SR_TITLE_TIMEOUT | 10 秒 | 单请求 HTTP 超时，范围 1—30 秒 |
| SR_TITLE_EXTRA_BODY | 空 | 仅透传 reasoning_effort、chat_template_kwargs |

请求固定 `stream=false`、`temperature=0.2`、`max_tokens=96`，没有 tools，不进入 Agent Loop，SDK 重试为 0。优先请求 `response_format=json_object`；只有明确拒绝 response_format 的 400 才兼容重试一次，不做无限重试。最终 content 必须是 JSON 且 title 通过 Unicode NFKC、控制字符、长度、中文和异常包装校验；只返回 reasoning 没有 content 时回退。

部署中的 llama.cpp 版本和模板在本机不可访问，因此默认不猜测推理关闭参数。确认自己的构建支持后，可配置 `SR_TITLE_EXTRA_BODY='{"reasoning_effort":"none"}'`，或模板支持的 `{"chat_template_kwargs":{"enable_thinking":false}}`。不会改变主聊天的推理设置。

## 实际参考与取舍

- Pi 的 [AI types](https://github.com/earendil-works/pi/blob/main/packages/ai/src/types.ts) 区分 TextContent、ThinkingContent 和 ToolCall；[Agent types](https://github.com/earendil-works/pi/blob/main/packages/agent/src/types.ts)、[agent.ts](https://github.com/earendil-works/pi/blob/main/packages/agent/src/agent.ts)、[agent-loop.ts](https://github.com/earendil-works/pi/blob/main/packages/agent/src/agent-loop.ts) 将内容增量与工具生命周期事件分离。本项目保留 SSE / Pinia 归并模型，没有引入 Pi 框架或第二套执行状态机。
- Pi 的 [session-manager.ts](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/src/core/session-manager.ts)、[agent-session.ts](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/src/core/agent-session.ts) 和 [session-name 示例](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/examples/extensions/session-name.ts) 展示显示名独立于消息，以及命名后的通知；当前源码在 AgentSession.setSessionName 内调用 appendSessionInfo 并发布 session_info_changed。本项目使用 metadata / session_update，没有移植 JSONL 会话树、扩展命令系统。
- 查阅 [pi-title 包说明](https://pi.dev/packages/pi-title?name=pi-yi)，作为自动会话名称的产品参考；本次不声称验证其全部内部实现，不引入其依赖或周期性改名功能。
- Open WebUI 的 [routers/tasks.py](https://github.com/open-webui/open-webui/blob/main/backend/open_webui/routers/tasks.py) /title/completions 独立非流式任务、[utils/task.py](https://github.com/open-webui/open-webui/blob/main/backend/open_webui/utils/task.py) 的任务模型选择和 [config.py](https://github.com/open-webui/open-webui/blob/main/backend/open_webui/config.py) 的 JSON 标题模板，启发独立轻量调用。本项目只取轻量模式，不移植其配置数据库、用户权限、管线、全历史模板或其他生成任务。
- [llama.cpp server README](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md) 说明 reasoning_content、模板参数、reasoning_effort 与 JSON 输出能力。上游当前支持并不证明内网旧构建支持，故显式配置后才启用标题任务的推理控制。

## 验证

右侧 Agent 的场景检索结果采用单层卡片：标题、状态、总数仅显示一次，摘要单行，默认两个候选，候选行高 62px、缩略图 38px，文件名最多两行，参数和调用记录保持折叠。展开显示本次实际返回的候选数；总匹配数大于返回数时摘要明确说明，不伪造未返回的结果。候选进入可视区后才查询场景元数据并调用 `fetchExistingSceneJpg`，只复用现有 Blob 缓存或静态 JPEG，不触发 `/preview` 生成，不解码 TIF；缺图显示占位。未知档位的旧预览独立缓存，防止阻止查看器后续生成正确档位。相关前端回归 76 项通过，浏览器 Mock 验证默认两条、展开五条、62px 行高、图片加载、缺图占位和打开回调。

后端相关完整回归：192 项，成功（其中 1 项跳过）；前端相关回归：82 项全部通过。TypeScript 类型检查和 Vite 生产构建通过。已有 vendor 缺失 source map 与构建包体积提示不影响检查通过。未执行真实模型、内网 SR/GPU 或浏览器视觉验收。

新增 Mock 测试覆盖：首次持久化、重复输入、并发领取、过期 claim / 迟到响应、手动名称优先、删除竞态、其他 metadata / 置顶 / updated_at 保留、失败终态、纯图片、JSON 请求参数、无最终标题、历史兼容、繁忙时延迟调度、改名 API 与临时标题 SSE。

前端新增测试覆盖：无 reasoning 的阶段展示、原始推理与正文独立、阶段保留、重复工具事件去重、相同工具的不同调用、失败与断线状态，以及不信任助手自述成功。真实模型标题质量、内网 llama.cpp 请求参数和浏览器视觉验收仍待核。

运行命令（项目根目录；前端命令在 frontend 下）：

```text
python -m unittest backend.tests.test_agent_loop backend.tests.test_api_platform backend.tests.test_goal_runtime backend.tests.test_execution_safety backend.tests.test_tool_result_formatter backend.tests.test_session_titles backend.tests.test_session_titles_api -q
node node_modules/vitest/vitest.mjs run src/stores/__tests__/chatInteraction.test.ts src/stores/__tests__/chat.test.ts src/stores/__tests__/chatImages.test.ts src/lib/__tests__/api.test.ts
node node_modules/vue-tsc/bin/vue-tsc.js --noEmit
node node_modules/vite/bin/vite.js build
```
