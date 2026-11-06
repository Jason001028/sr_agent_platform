# 普通模型响应：按进展超时

日期：2026-11-06。已修改本地实现，真实内网模型延迟待核；本轮未修改运行中的服务或执行 SR/GPU。

## 原因与新行为

原 `async_transport.request` 在请求开始时固定 `end = started + SR_LLM_TIMEOUT`，默认 60 秒。它覆盖服务排队、prefill、推理和完整输出；即使持续生成 token 也会被 `asyncio.wait_for` 取消。极速档只影响 Thinking 协议参数，`adapter=none` 时并不发送降档字段；端点接受字段也不证明真实生效，因此不能靠“极速”保证 60 秒完成。

普通 Chat（包括工具栏“交给 Agent 整理 / 总结”）现使用：

| 参数 | 默认 | 作用 |
| --- | --- | --- |
| `SR_LLM_FIRST_OUTPUT_TIMEOUT` | 180 秒 | 从本次传输开始到首段有效正文、推理、工具声明/参数；兼容回退不重置 |
| `SR_LLM_TIMEOUT` | 60 秒 | 首段后连续没有有效进展的时长；Goal 仍将它作为单请求上限 |
| `SR_LLM_RESPONSE_TIMEOUT` | 600 秒 | 一次模型响应的有限总墙钟上限，包括兼容回退 |
| `SR_LLM_VISION_TIMEOUT` | 180 秒 | 图文请求首段等待的最小值，总响应仍不越过总上限 |

仅有 SSE 注释心跳、空 delta、role 或 usage 帧不算生成进展，不能续期。有效工具参数片段可以续期，但残缺调用不会执行。连接建立最多 15 秒，并受更早的总期限约束。持续输出仍受 600 秒总上限约束，避免只有推理而永不结束的请求永久运行。

非流式无法观察模型首 token，使用有限总响应预算；不伪造“仍在输出”的判断。推荐保留默认流式。以上是每次模型请求的策略，不是整个多工具 Agent 会话无限续期。

有 Round deadline 的 Durable Goal 不启用普通聊天续期：仍取原模型单请求上限与剩余轮次预算的较小值。SDK 读超时和外层定时器先后顺序不同，也按实际选择的截止约束分类为 BUDGET_YIELD。没有重写 Worker、Lease、Tool Intent 或外部作业对账。

对直接构造 `Config` 的嵌入调用方，两项新增字段为 None 时保留旧的绝对调用上限；平台入口 `load_config` 默认启用上述策略。所有时限必须为有限正数。

## 取消、诊断与输入优化

`_ProgressStream` 对真实异步读设置首段/停滞期限，外层仍保留总 deadline；取消会关闭流及 HTTP 客户端。失败不自动重发整个模型请求，避免重放工具副作用。已有用量观察保留，未知用量不估算。

Trajectory 补充 `model_first_output_timeout_seconds`、`model_idle_timeout_seconds`、`timeout_phase`、`timeout_limit_seconds`；界面错误文字区分首段、停滞、总时限和连接问题，不再只说“60 秒完整输出超时”。

批次草案移交对重复检测依据去重，并以带 columns 的表格数组携带所有候选。名称、完整路径、状态、依据和核验异常均保留；不默默只发送前 N 景。提示优先给出 3–6 点归纳、按问题分组，不逐景重复输入。这降低冗余输入和复述，不改变执行授权，仍不由 Agent 提交 SR。

## 生效与验证

配置在 API 启动时读取。部署更新后端后重启 sr-api；可用 systemd drop-in 显式设置上述三个参数。仓库 nginx 模板的 `/api/` 已是关闭缓冲、3600 秒 read timeout，本轮未修改它；实际部署的代理配置仍以服务器为准。

基线：原 request-budget + async-transport 为 46 passed / 1 skipped。新增真实 loopback TCP 场景覆盖：持续慢输出、慢首段、停滞、仅心跳、推理总上限、非流式、Durable、部分工具参数、显式取消；失败场景检查服务端观察到客户端连接关闭，而不是只断言函数返回快。另测试默认/非法配置、图文首段、Goal 不续期，以及 50 景移交无身份丢失的去重。

没有连接真实模型。真实排队时间、prefill 吞吐、Thinking 档位效果和任务成功率需内网观测；本次不宣称模型推理速度提升。
