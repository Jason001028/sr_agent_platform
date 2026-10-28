# 请求预算与 Thinking 控制（2026-10-28）

## 源码审计与基线

本地工作树为依据，未覆盖原有未提交修改，未提交 Git。
REST/SSE → `run_loop` → `_default_chat` → `_stream_chat` / 非流式 create；
Durable Worker `run_round` 从 SR_GOAL_ROUND_* 读取有限预算，通过 before_model
调用 reserve_model，以现有事务及 Lease fencing 更新 Goal 全局模型调用数。
Worker 将 BUDGET_YIELD 映射 RUNNING，RETRYABLE_ERROR 映射 RETRY_WAIT，
WAITING_EXTERNAL 保持等待外部并继续对账；验收仍由 Worker 决定。
本地服务源码没有 Goal `deadline_at` 的执行约束；Lease 的 lease_until 不是任务预算。

原始问题：Durable 客户端仅按初始 Round 秒数设置 timeout；SDK 普通聊天默认重试；
流式异常被统一包装为 response_interrupted 并进入完整性重试；stream_options
回退未重新检查剩余时间或预留实际请求次数。Runtime Status 已有请求临时投影，直接复用。
改前 `python -m pytest backend/tests/test_runtime_status.py backend/tests/test_goal_runtime.py -q`
为 50 passed。不是实际模型性能基线。

独立标题生成走 services/session_titles.py，已有 SR_TITLE_TIMEOUT（最多 30s）、
max_retries=0 和 response_format 专项回退，不属于 Agent Round；本次未改变它。
SR_LLM_* 由 Config 启动快照读取，普通聊天无 Round 时间限制。

## 实现和生效边界

- `backend/agent/request_budget.py`：单调时钟请求预算、最小 Thinking 映射、诊断类别。
- `backend/config.py`：新增下列显式配置，默认保留模型推理行为和生成 Token 上限。
- `backend/agent/loop.py`：Round 单一 deadline；请求前重新计算 timeout；不足时不预留；
  流式迭代检查 deadline 并在 finally close；SDK max_retries=0；兼容回退重新预算和计数。
- 专项测试在 `backend/tests/test_request_budget.py`；更新原 Runtime Status 字段和旧超时断言。

| 配置 | 默认值 | 含义 |
|---|---|---|
| SR_LLM_DEADLINE_MARGIN | 0.1 秒 | 响应处理及提交预留，部署时按实测调大 |
| SR_LLM_THINKING_PROFILE | balanced | fast / balanced / deep，由开发或评测显式选择 |
| SR_LLM_THINKING_ADAPTER | none | none / reasoning_effort / chat_template |

reasoning_effort 映射 low / medium / high；chat_template 映射
`extra_body.chat_template_kwargs.enable_thinking`（fast false，其余 true，后两档不代表独立强度）。
**配置适配器只是运维声明协议可用，不是能力探测或有效性证据。** 默认 none 不发送 Thinking
字段并展示 unavailable。400/422 明确报告 unknown/unsupported 等字段拒绝时，
可移除被拒绝的 reasoning_effort、chat_template_kwargs 或 stream_options 再请求。
每次降级重新检查时间并预留调用；不对认证、限流、5xx 或一般参数值错误降级。
结果记录 capability_rejections；thinking_applied 仅标 unverified 或 unavailable，
成功返回不等于真实推理档位已生效。
max_tokens 不降档，不将 completion_tokens 冒充 reasoning_tokens。

本机源码核对：OpenAI 1.109.1，HTTPX 0.28.1，httpx/_config.py 的 Timeout
分别控制 connect/read/write/pool 操作，并非整个调用墙钟截止。
复审修复后，生产网络路径使用 `async_transport.py` 的 AsyncOpenAI/HTTPX，
在整个请求、所有兼容回退和流消费外层使用 asyncio.wait_for，超时取消实际 HTTP Task，
流在 finally 中 await close，客户端在 async context 中关闭。普通聊天也有每次调用
SR_LLM_TIMEOUT 的墙钟上限，Durable 取其与 Round deadline 减余量的较早者。
同步 Loop 通过 asyncio.run 驱动这段可取消传输，API/Worker 保留原有线程执行边界；
没有把不可取消的同步网络调用放在线程中伪造超时返回。

这提供可取消的网络 Deadline，**仍不是整个 Agent 的硬实时保证**：Python 同步冷启动、
SDK 序列化、消息/图片水合、阻塞的 on_delta、自定义同步 chat、工具和数据库提交不能被
asyncio 定时器抢占；取消及连接清理也需要调度时间。TLS 信任加载在模块初始化时缓存。
普通聊天上限按每次模型调用计算，不是对整个多工具聊天设置总墙钟限制。
原同步 `_stream_chat` 仅保留为兼容测试辅助，生产 `_default_chat` 不再调用它。

## Runtime Status 和诊断

仍在每次 run_loop 请求前 build_runtime_status → project_runtime_status，复制消息后
附加至首条 system；不进入历史、Checkpoint 或压缩输入。新增示意：

```text
thinking_profile: "unavailable"
execution_priority: "minimal_necessary_action"
remaining_round_seconds_at_request: 28.0
```

最后一个字段仅在 Durable 中存在，是请求时快照。已有模式、Round/Turn、Goal 全局及
Round 调用计数和近期工具结果保留。简短行为规则加入基础系统提示词。
旧 `chat(messages, tools)`、可选 on_delta 接口保留；显式声明 request_config/deadline
的适配器可以接收预算，自定义同步函数不能被运行时强制取消。

| 诊断 | 处理 |
|---|---|
| DEADLINE_EXCEEDED | Durable BUDGET_YIELD，不进入完整性重试，不执行迟到工具 |
| MODEL_TIMEOUT | RETRYABLE_ERROR，当前 Loop 不重试，Worker 按现有总预算有限恢复 |
| RESPONSE_INTERRUPTED | 原有有限完整性重试；下一请求仍检查时间与次数 |
| OUTPUT_LENGTH | 保留 P0 完整批次拒绝和有限重试 |
| INVALID_RESPONSE | 保留结束原因、工具身份及 JSON 校验；无工具副作用 |
| AUTHENTICATION_ERROR / INVALID_REQUEST | 明确报告认证/参数问题，不作响应中断处理 |
| CONNECTION_ERROR / SERVICE_UNAVAILABLE / RATE_LIMITED | 当前 Loop 不重复请求，Durable 复用 RETRY_WAIT |

Lease 仍在请求前后及事务提交处检查；未重写 Tool Intent 和未知外部 SR 作业对账。
Trajectory 请求开始记录模型、请求参数、配置 timeout、剩余时间、能力状态；请求结束记录
耗时、finish_reason、诊断及完整性重试计数。流式记录首个事件、正文和工具声明时间，
从请求开始计时；无相应事件为 null。未持久化推理内容；没有充分事件证据时不估算
纯推理阶段耗时，也不伪造独立 Reasoning Token。
Usage 在判定迟到或超时输出能否执行之前记录；流式取消保留已收到的 Provider Usage。
没有用量的尝试标 unknown，聚合提供 unknown_calls/usage_complete=false，数值只是已知用量小计。

## 回归与内网验收

专项及相关 P0 回归命令：

```powershell
python -m pytest backend/tests/test_request_budget.py backend/tests/test_runtime_status.py backend/tests/test_goal_runtime.py backend/tests/test_agent_loop.py backend/tests/test_goal_clarification.py backend/tests/test_viewer_context.py backend/tests/test_message_viewer_context.py -q
python -m pytest backend/tests/test_async_transport.py -q
```

首轮 134 passed；最终复跑结果见本轮交付。覆盖新鲜时间、无预算不预留、两种 chat 注入、
超时不完整性重试、迟到工具不执行、流关闭、回退重新计算、Profile 参数及既有 Goal/Viewer 保护。
没有执行 SR GPU 作业，也没有执行整个项目测试集。

复审新增真实 loopback TCP 测试（不连接模型）：持续慢流、临近截止停流、非流式慢响应、
显式 task 取消、Thinking 字段拒绝并降级。前四类检查服务端观察到 EOF/连接重置；
不会仅凭函数快速返回推断底层请求已取消。同步 SDK Fake 已迁移到异步 SDK 测试边界。
复审最终通过数及已知测试不稳定情况以本轮交付为准。

真实模型能力与 A/B/C/D **尚未验收**。现有 config_locations.md 只记录用户回传的模型
路径和启动配置，没有可用于验证 Thinking 的服务版本/能力响应；曾回传 max_tokens=512
覆盖 4096，当前实际值仍须内网确认。不能据此声称延迟或成功率已改善。

沿用 `backend/eval_viewer_context.py` / Prompt Lab 和 Trajectory，固定模型、工具清单、
输入快照、采样参数、输出 Token 上限；扩充现有四个案例为以下八类：元数据、尺寸、
无像素地物识别拒答、显式场景搜索、已有 SR Job、多步只读、复杂规划、Durable 恢复。
每组重复至少三次，人工审核正确率；不要只按 Loop ok 计任务成功。

| 组别 | 验收设置 | 结果 |
|---|---|---|
| A | 修改前本地源码快照（包含原有未提交成果） | 待内网执行 |
| B | 新 Deadline，adapter=none；评测中消融新增状态字段 | 待内网执行 |
| C | B + 经端点验证的 Profile，继续消融新增字段 | 待内网执行 |
| D | 完整实现 | 待内网执行 |

没有为消融增加生产旁路开关；需在评测上下文临时替换投影，保留既有 Runtime Status。
先取得 `systemctl cat llama-cpp`、版本/构建号和有效 API 配置；依次测不带字段、关闭、
降强度、默认推理（只测服务明确支持者）。检查实际阶段事件和完整工具 JSON，而不是
仅以 HTTP 200 认定生效。记录超时率、业务完成率、首工具正确率、模型调用数、请求及
阶段耗时、Provider 用量、截断/无效次数；缺失指标标 null。FAST 正确率下降则回退
balanced/none，保留相同 Deadline。真实结果回填本文，不以 Mock 速度替代真实评测。
