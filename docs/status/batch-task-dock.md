# 批量智能修复工具栏任务中枢

日期：2026-11-05。基于本地工作树实现，未 commit/push/reset/clean；未访问真实盘阵或启动真实 SR/GPU。

## 2026-11-06：Quiet Mission Control 前端重构

本节替代下文旧版布局描述。工具栏仍置顶批量智能修复，默认展开三个下划线视图：待修复、执行监控、结果报告。关联批次身份、状态和三项权威进度固定于滚动区域上方；暂停明确说明外部 SR 不会因此停止。三视图内容互斥，准备组件通过 KeepAlive 保留窗口内文本、冻结确认和设置。

待修复首屏只显示工作清单计数、需复核计数、最多三条影像及三个核心入口。搜索、确认/排除/复核、TXT 粘贴、身份依据和执行策略收进二级详情；正式 Batch Item 单独按需加载。核验后仍须确认冻结范围才能创建 Goal。

监控复用 batchGoal Snapshot、事件流及控制接口。三条轨道独立显示检测、SR 提交、结果验收；仅展示当前检测和后台活跃 Job，最近重要事件最多三条。批次选择、刷新与跟随收进查看设置；完整队列及事件历史按需展开。

报告仅在 snapshot.report.available 为真时显示汇总及最多三条近期关注结果，无报告只显示一句空态。验收计数使用 counts.verified，不使用 submitted；需复核的数字明确限定为 unknown_jobs，不冒充所有人工掩码复核总数。完整报告继续走受控 batch-report API，TXT 同步与批量处置在详情内独立呈现。

点选清单归对比工具（仅对比上下文显示），差异候选归差异定位，质检 TXT 归数据来源，ROI/云量/场景队列归影像工具，均通过独立工具分区访问。Agent 移交切换已有标签并填入批次 ID 与清单/快照上下文，由用户发送；无当前影像也可讨论批次，不触发 Worker 或 SR。

视觉使用局部雾白令牌与既有 motion tokens；保留 400px 侧栏。验证：前端全量 61 files / 777 passed，typecheck、build 通过（保留已有 sourcemap / chunk 警告）。`node .e2e/test-batch-panel.js` 使用合成快照覆盖互斥视图、冻结授权、409 控制重读、报告 API、Agent 无图移交、1024px 窗口及报告空态。截图见 `.e2e/artifacts/batch-quiet-*.png`。工作区其他并行改动保留，未访问真实生产数据或启动 SR/GPU。

## 本地能力审阅与取舍

| 原实现 | 本地状态与复用方式 |
| --- | --- |
| PR C | Producer / Monitor、SQLite 恢复、Fake SR 已存在，继续作为唯一执行器 |
| PR D | 冻结输入、检测/掩码契约、受控只读 Overlay 与 Viewer 身份校验已存在，直接复用 |
| PR E | 提交去重、外部监控与分层验收已存在；production 门仍关闭 |
| PR F | Snapshot、SSE 通知、事件分页、独立 control_revision、控制和跟随已存在；第三个 Tab 被本次双 Tab 布局替代 |
| 原缺口 | 草案入口、显式 Viewer 集合导入、执行策略确认、人工掩码复核门禁、Agent 草案移交及有界历史分页 |

未改动本地已有的巡览、背景、历史导航和差异定位实现。原质检 TXT 管理器仍保留，其标题明确为「质检 TXT 清单」，与新草案准备区区分。

## 当前交互

右侧只有「工具栏｜Agent」。`BatchTaskDock` 常驻工具栏，固定摘要与 `.tools-scroll` 是 flex 兄弟区域；展开详情、原有影像工具共享后者，侧栏仍宽 400px。

- 无批次为单行新建入口；详情默认折叠。
- 摘要只展示 Snapshot 的检测轨、提交数、验收数和异常，不合成总完成率。断流保留旧快照并提示数据可能滞后。
- 详情分「待修复｜执行监控｜结果报告」。执行监控继续使用原 Panel；队列列表限高，完整清单每页 20 项；最近事件最多 10 条，历史每次替换一页，历史游标与实时尾部缓冲分离。
- 用户切换批次保留选择，并更新 `batch_goal` URL、移除旧 `batch_item`；创建成功也走相同选择流程。后台事件不切 Tab。
- Agent 保留聊天上下文。`prepare_batch_repair` 只对明确给出的真实 PAN 路径生成结构化草案，用户主动点击才移交工具栏；`query_batch_repair` 读取权威 Snapshot。关联批次在 Agent 中显示真实 Goal ID 和查看入口。

## 准备、冻结和执行

草案为窗口内 Pinia 状态，界面明确标注未服务端持久化。支持搜索、状态筛选、本页多选、确认、排除、需复核；不会把该清单当作已提交 SR 队列。

Viewer 单图/勾选组加入时，`lqPath` 仅作为场景目录传给可信 `/api/scenes/resolve`，使用返回的 `resolved.input`；不拼文件路径、不生成缩略图。只有已确认候选进入冻结导入。也可粘贴明确指定的质检 TXT。

扩展原 `POST /api/goals/batch-imports`：可携带 `sources: [{path,name,evidence}]` 和 `strategy: automatic | review`。显式路径不走文件名反推，仍使用原授权根检查、PAN/NOSR 检查、资源身份采集、去重诊断、内容寻址 Manifest。显式集合以 explicit_sources_v1 写入 UTF-8 源工件摘要，策略写入冻结 config。原 TXT 契约保持可用。

核验展示服务端冻结清单与诊断，用户勾选范围/策略确认后才调用 `POST /api/goals`，使用稳定 request_key 重试。生产门保持原行为，DEMO / MOCK 始终标明演示，不宣称真实生产验收。

人工策略在创建时写入 Item 的 `review_required`。Producer 仅挑选已批准的相同掩码，提交边界再次检查；未批准时停在 MASK_READY，Monitor 不停止。复核门禁复用原 Worker，不新增执行器。

新增 `POST /api/goals/{goal}/items/{item}/review`：`{expected_version, decision: approve | exclude}`。SQLite 事务校验 Item 版本、状态、复核要求、尚未预留提交容量、Goal 非终态。批准绑定具体 mask SHA256；排除标记 SKIPPED 并保留审计事件，不删除历史。旧版、已提交/历史项、重复复核返回 409，界面重读提示。批次 pause/resume/cancel 继续使用独立 control_revision。

冻结执行清单仅分页读取，支持影像/只读 Overlay 与已检测 bbox 定位；定位不修改 ROI/Mask。自动跟随默认关闭，只在代表 Item 身份变化时导航，同 Item 版本变化不重复取图；原 userIntentSeq 与迟到响应守卫保留。

## 验证记录

修改前：前端全量 58 files / 756 passed；后端 Snapshot + Manifest 39 passed。既有 geotiff sourcemap 缺失为警告。

修改后组合后端：127 passed，覆盖 Manifest、Snapshot、Worker（含 50 景 Fake SR）、规划工具、工具描述契约、Viewer 上下文、Registry 与 Plugins。另补跑人工掩码门禁专项通过 1 项、Manifest / 规划工具补跑 19 项通过。前端全量 60 files / 764 passed；其后补充默认十条与历史分页边界回归，Batch Store 专项 13 项通过。typecheck、build 均通过（build 仅既有大 chunk 提示）。最终 Mock 浏览器闭环通过。

浏览器 `.e2e/test-batch-panel.js` 覆盖：双 Tab、固定摘要、三条权威进度、并发当前检测与外部 SR、事件展开、控制 409 后重试、受控报告、跟随退出、冻结后确认才创建、切 Tab 保留状态、刷新恢复。

`BATCH_MOCK_SERVER_ONLY=1 node .e2e/test-batch-panel.js` 可启动独立合成快照服务，便于 agent-browser 视觉检查。截图在 `.e2e/artifacts/batch-task-dock.png`。

## 保持明确的边界

未冻结的草案与粘贴文本不跨整页刷新持久化；冻结 Manifest 与已创建 Goal 按服务端事实恢复。人工审批目前逐项操作，未增加批量批准接口。真实盘阵、内网代理 SSE、真实 Detector/SR/GPU 和修复质量仍需内网验收，生产提交门未开放。
