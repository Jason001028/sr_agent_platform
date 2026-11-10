# 批量智能修复工具栏任务中枢

## 2026-11-09：刷新后不自动显示逐景展示卡片

`BatchRepairPlayback` 原先仅以 `batch.goalId` 判断显示；F5 恢复上次批次后，即使 follow 关闭、playback 为 idle，也会在画布底部出现「逐景展示」。现仅在已关联批次且展示正在运行或已进入非 idle 阶段时显示；恢复批次本身不触发展示卡。任务面板的主动开启入口保留，运行中的初始异步等待、暂停/继续及完成状态仍显示。控制组件继续挂载，避免隐藏卡片后无法从任务面板启动。

组件显隐回归 4 项与既有展示 Store 回归 5 项通过。前端部署需更新 `frontend/dist`。

## 2026-11-07：极简平面化 Mission（当前实现）

本节替代下方历史布局说明。参考 `frontend/icon/batch_repair_prototype_bundle/`，现有「智能修复」入口打开序列选择；选择、任务监控与原影像工具共享一个外层滚动区，无内部队列滚动条。保留真实 Viewer 分组、默认全选、原核验与冻结创建链路。

监控仅由 Mission Header、Mission Focus、按需完整队列组成。Header 展示明确阶段的主数字及检测／SR 提交／验收三个图形节点；未知分母显示「统计中」，不适用显示「—」，不合成总体百分比。Focus 与完整队列互斥，不重复列出同一任务。异常默认最多三条，可直接展开完整原因和定位影像；不生成虚假作业 ID。关联到 Viewer 时沿用其数字标签，否则标注批次项序号。

四种主要状态：准备态显示紧凑勾选列表与唯一开始按钮；运行态显示阶段进度和当前任务；异常态显示异常主数字、少量异常及完整队列入口；完成态显示验收结果和「查看结果」，隐藏过期当前任务及过程轨迹。暂停、取消保持真实状态，不被异常计数覆盖。人工质量验收只在存在待验收项时按需展开原组件。

右上角统一设置包含批次选择、刷新、统计、完整队列、跟随、暂停／恢复／取消及报告／Agent 移交；事件历史仅在已有开发者模式中可开启。显示偏好存于 `sr.batch.mission.view.v1`，不写入执行策略。取消仍明确保留已提交外部 SR 的语义。折叠保留组件状态，220ms 收展、200ms 行进入，尊重 reduced-motion。328px／400px 均无横向溢出或多层滚动。

实现：`BatchTaskDock`、`BatchProgressPanel`、`BatchProgressTracks`、`BatchCurrentItem`、`BatchJobQueue`、`BatchItemsList`、`BatchControls`、`BatchResultReport`、`BatchRepairDraft`；新增共用行 `BatchMissionItem` 与纯快照投影 `lib/batchMission.ts`。移除重复摘要、独立 SR 队列、重复报告统计与空当前任务卡片。API 仅补齐已有作业字段类型；本轮未修改生产 Runtime、状态机或门禁。原 SSE／断线恢复、control_revision／409 重读、用户退出跟随、Viewer 定位、受控报告与结果保护沿用现有链路。

验证：typecheck、75 文件／923 项前端测试、build 通过；本地合成浏览器回归通过，覆盖选择单次提交、缺少 randomUUID、三阶段、27 条异常分页、独立原因／定位、显示偏好、控制 409、恢复、取消确认、报告、Agent 移交、断线及窄栏。人工验收浏览器夹具通过：2 个预置作业、5 次审阅、重启持久化、旧成果 409 拒绝、0 次新 SR 提交。夹具仅调整 Pillow 解码、短临时路径、静态 MIME 和启动等待以适配 Windows。未启动真实生产 SR／GPU，未验证内网大图生产运行；既有 geotiff sourcemap、jsdom Canvas 和构建 chunk 提示仍在。

截图：`.e2e/artifacts/batch-mission-{draft,running,failed,completed,328,400}.png`；测试日志：`batch-mission-{typecheck,frontend-tests,build}.log`；人工验收结果：`batch-quality-report.json`。未提交或清理工作树，其他既有及并行改动保留。

日期：2026-11-05。基于本地工作树实现，未 commit/push/reset/clean；未访问真实盘阵或启动真实 SR/GPU。

## 2026-11-06：序列选择与单次启动（当前界面）

本节替代下方三视图、草案编辑及二次冻结授权的界面说明。保留下方现有导入清单、智能修复、一键解析；「智能修复」始终展开工具栏、聚焦 Panel 并显示序列选择，不直接创建 Goal。Panel 不再重复提供导入或 Agent 整理按钮，旧草案存储仍保留。

用户反馈后的紧凑样式：序列行复用下方质检清单的 4px/7px 内边距、3px 行距及细边框；名称单行省略、悬停显示完整名称，不再显示「PAN 1 景」。右下角仅保留圆滑的「可修复 / 异常 / 核验中」状态标签，异常原因为标签 title 与可访问名称。启动 request_key 改用现有 HTTP 兼容的 `spatialId()`（getRandomValues + 时间/序号，缺少 crypto 时仍可生成），不依赖仅安全上下文提供的 `crypto.randomUUID()`；冻结结果与重试键的复用不变。

反馈修正验证：typecheck、全量 69 files / 855 tests、build 通过；浏览器额外断言行高不超过 42px、无景数文案、右下角状态标签，并实际移除 `crypto.randomUUID` 后点击开始，确认创建一次并进入任务监控。请求仅访问本地合成服务，未执行真实 SR/GPU。

列表按 Viewer 的 `sceneOrdinalOf` 与真实 `sceneDir` 分组、排序；NOSR/SR/本体卡片共用原编号，一组只发送一个 canonical `PAN.tif`，不以当前激活图或已加载像素限制范围。当前后端契约每个场景目录仅接收这一份 PAN 本体；不把数字组扩大为未授权的父目录扫描。只有 NOSR/SR 卡片打开时，也可核验同目录尚未打开的 PAN。

打开选择区后，复用 batch-import 进行逐组 PAN/NOSR、备份保护、权限、样本类型与尺寸核验。该接口仅冻结元数据，不创建 Goal、不检测或运行 SR；不可处理组禁用并显示原因，完整路径不在列表中出现。默认全选可处理组，支持逐组勾选、全选及取消全选。列表内部纵向滚动，唯一主操作固定在列表下方。关闭前的隐藏 Panel 不预先发起核验请求。

「开始修复 · 已选 N 组」一次执行所选集合重新核验和 Manifest 冻结，再创建现有 Batch Goal，策略固定 automatic、环境固定 production；不沿用旧草案的 demo/review 设置。冻结错误、数量不符或会话/范围变化均不得创建任务。网络失败重试复用同一冻结结果与 request_key；运行仍交给原 Worker、提交去重及结果保护。

创建后选择区切为任务摘要：真实检测/提交/验收进度、并行在途阶段及完成/失败/跳过/无需修复计数；逐景队列、异常、控制、报告统一在「查看详情」中延迟展开。COMPLETED 快照触发已有 Image Refresh 扫描，队列轮询与源版本校验继续复用，不自动切图。

**现有后端 `ensure_execution_allowed('production')` 仍明确拒绝生产批量执行，本轮未改后端、未开放门禁、未将演示结果当生产成功。** UI 对此显示中文拒绝原因；真实生产长任务需另行完成原后端验收与开放。

验证：typecheck、全量前端 69 files / 854 tests 通过；新增 9 项组件交互测试，并更新旧草案组件测试与 `.e2e/test-batch-panel.js` 的界面契约。本地合成服务浏览器回归通过（编号一致、未打开 PAN、全选/清选、单击创建、三条快照进度、409 控制、报告、跟随退出、刷新及 1024px 无横向溢出）。另用 agent-browser 检查 12 组长列表及主操作可见性。新截图使用 `.e2e/artifacts/batch-sequences-{draft,monitor,empty}.png`，未覆盖旧 quiet 截图。仅有既有 geotiff sourcemap、jsdom canvas 与大 chunk 警告。

## 2026-11-06：Quiet Mission Control 前端重构

本节替代下文旧版布局描述。工具栏顶部是批量智能修复，但**默认收起**：质检清单里「一键解析」左侧的「智能修复」按钮（或 Agent 移交）才把它放出来。展开后是三个下划线视图：待修复、执行监控、结果报告，共用一个固定高度的滚动区，切页时整栏高度不变。关联批次身份、状态和三项权威进度固定于滚动区域上方；暂停明确说明外部 SR 不会因此停止。三视图内容互斥，准备组件通过 KeepAlive 保留窗口内文本、冻结确认和设置。

质检 TXT 清单置顶且不再折叠（原先是一个 details 分区），「智能修复」开关就放在它的操作行里；待修复首屏的三个入口按钮等宽居中。

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

右侧只有「工具栏｜Agent」。`BatchTaskDock` 挂在工具栏（显隐见上一节，默认收起），固定摘要与 `.tools-scroll` 是 flex 兄弟区域；展开详情、原有影像工具共享后者，侧栏仍宽 400px。

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
