# Agent 修复工作区只读感知（P0）

日期：2026-11-13。仅补观察能力；没有新增 SR 提交、文件覆盖或人工审批权限。未 Commit/Push。

## P0 补充：当前工作树的最新行为

- 每条用户消息的模型投影使用 `repair_context`：活动影像的 id/name/scene_id/image_key/source_revision；三类草稿各自的数量、版本与选中摘要；关联 goal_id/item_id；workspace_empty。无快照为 unknown（null），已冻结且没有影像、草稿、工作区成员或 Goal 才为空。已创建 Goal 但未打开图不算空工作区。
- 只保留必要的选中身份、显示格身份和附件映射；检测区域、掩码内容、草案候选路径及所有前端任务状态不注入模型。详情仍由只读工具读取。每条历史消息使用自己的冻结摘要，最新用户消息决定本轮指代。
- 原始 Manifest/Item 的 ordinal 从 **1** 开始，排除项仍占原编号。默认“第 N 景”先 `list_repair_items(goal_id, cursor=N-1, limit=1)`，不传 status，核对返回 ordinal=N，再用返回 item_id 调 `get_repair_item`。空结果或编号不符不得猜 item_id。UI 重排、分页、status 筛选不重编号；只有明确“失败项中的第三个”才按过滤结果逐页累计。歧义只在缺少 Goal、草稿/Batch 指代不明等确实需要时澄清。
- `list_repair_items` 返回 ordinal_base/order/status_filter/ordinals_renumbered，以及每项 excluded/exclusion_reason，明确区分原序号与过滤后位置。未增加关键词树、路由 LLM 或新的 Loop 控制逻辑。
- Spy 覆盖四种只读工具的完整实际绑定链，在空/非空快照下，`search_scenes._run`、场景检索服务、scan_root、工作区资源解析、Manifest load/import、Path.rglob 调用均为 **0**。已知 Goal/Item 即使 Viewer 为空也可以精确读取数据库。

五类验收入口：

```powershell
python -m backend.eval_repair_workspace --core-only --output .e2e/workspace-supplement-core-replay.json
python -m backend.eval_repair_workspace --core-only --live --baseline-ref <改造前提交> --output .e2e/workspace-supplement-live.json
```

五类固定回放为当前影像、修复清单、第三景、已知 Job、搜索其他场景；第三景用例**不提供 item_id**，必须先定位再读详情。本轮回放 5/5 预期入口可执行，共 6 次工具调用（第三景 2 次，其余各 1 次），额外工具 0、无关搜索 0；这些都是固定脚本链路验证，**不是模型选择准确率**。最新 50 项合成草稿投影为 498 字节，包含本轮新增的空状态和顺序契约（首轮为279字节，基线5967字节）。

真实内网模型验收 **未完成**：本地配置仍为 api.openai.com / gpt-4o-mini 且无 Key；文档中的内网部署地址 TCP 探测未建立连接。用户已明确回复“没有”可从开发机访问的内网模型地址。`.e2e/workspace-supplement-live.json` 保存 blocked 状态，工具选择准确率、误调用次数、额外工具开销均为 null，未用 Fake 数据替代。将来接入模型后仍使用 Fake Executor，不触发生产 SR/盘阵检索。

本轮补充专项及相关回归最终 **189 passed**（包含五类评测安全性回归），日志见 `.e2e/workspace-supplement-regression.log`。其余下文保留首轮交付记录；补充行为以本节为准。

## 工具契约

| 工具 | 参数 | 数据来源与返回 |
|---|---|---|
| `get_current_image_info` | 无 | 仅消息冻结的 current、selected、displayed、ROI/Mask/differences；本地未入库 TIFF 可查；不查 Batch 数据库、不扫描场景库。无快照返回明确错误；有空快照返回 image=null。 |
| `get_repair_workspace` | 无 | 消息冻结的活动影像、草案数量/版本、Viewer 序列勾选摘要、TXT 清单聚焦摘要、关联 Goal/当前 Item 引用；本会话 Goal 的实时数据库 counts/progress/审核数量和最多 3 项异常。 |
| `list_repair_items` | `goal_id`, `status?`, `limit=10`（1..50）, `cursor=0` | 复用 `Store.list_goal_items`，按 ordinal 升序，cursor 排他；多读一行判断 next_cursor，末页 null。Item/场景名、阶段、Job、人工审核摘要及错误。 |
| `get_repair_item` | `goal_id`, `item_id` | 精确 Item、SR Task、Quality Review 持久记录；PAN/NOSR image_key/revision、候选数、Mask 哈希/契约/构建器版本、Job ID/状态、五层验证、人工逐区计数及驳回原因。仅报告持久记录，不在查询中重新检查磁盘。 |
| `sr_job_status` | 现有整数 `job_id` | 仍复用 `run_sr.query_job_status`，精确 Task 查询与判定文件；Agent 会话入口先核对数据库归属。兼容没有 session_id 的旧 Batch 回执，通过 Item/Intent 与 Goal 的持久关联授权。 |
| `search_scenes` | 现有 Schema | 明确仅用于场景资源库发现。保留兼容 workspace 范围；冻结工作区为空时返回 count=0/scanned=0，禁止悄悄转全库。明确检索其他场景使用 scope=library。无消息快照的旧 CLI/REST 保留兼容全库入口。 |

`list/get` 每次先核验 Goal 的 kind=batch 和 session_id。UI 提供的权限、状态、路径不参与授权。详情只投影白名单字段；错误文本截断并过滤绝对路径/UNC。不返回 config_xml、params、执行目录、完整候选几何或 Reviewer 身份。完整结果仍由现有 Full Result / Model View、参数校验、预算和会话级 result_id 管理。

四种证据分别保留：Job ID/提交回执、持久执行状态、contract/artifacts 验证层、quality 人工审核层。缺失记录返回未知；demo_contract / mock 不解释为生产质量通过。`review_for_item(..., verify=False)` 只读数据库，避免查询工具触发文件重验。

## 上下文数据流

1. `chat.send` 已在发送时调用 `captureViewerContext`；本轮加强为 JSON 深复制，异步上传或切换 UI 不改变已取走的快照。
2. Viewer 提供稳定 image_key/source_revision、active/selected/displayed；已有批量草案 Store 提供数量与版本；序列选择 Panel 提供 total/selected/excluded/version；TXT Store 单独提供 source_name/total/聚焦行及版本。TXT 没有排除复选框，因此明确 `selection_semantics`，不把聚焦行数当成批量勾选数。
3. Batch Store 提供 Goal ID/当前 Overlay Item；Quality Panel 提供当前审核 Item，只有 Goal 一致时才选用。Panel 卸载注销自身观察 Provider，旧 Panel 的清理不能删除新 Provider。
4. `/api/platform/chat` 的现有 `validate_snapshot` → `run_loop(viewer_snapshot=...)` → messages.viewer_snapshot SQLite 持久化链路保留；本轮仅扩展校验投影字段。API、Worker 与 Viewer 主体无需重构。
5. Loop 按当前请求最后一条用户消息绑定 Registry。恢复 prompt=None 时仍取历史对应消息快照，不用调用方传来的最新浏览器快照；澄清恢复沿用原有消息绑定机制。
6. `project_messages` 仅投影 batch_draft 数量/版本，不投影候选完整清单和 workspace opaque ID 列表；工具回合不会重新注入完整清单。动态 Goal/Job 状态由工具现读。

草稿、Viewer 序列选择、TXT 清单是不同 UI 来源，不自动互相替代。已创建 Batch 的状态只信数据库；快照内的假终态会被忽略。没有当前图或关联批次时如实返回空/不可用，不猜最近任务。

## 修改文件

- `backend/services/repair_workspace.py`：三个消息绑定只读工具及紧凑投影。
- `backend/services/viewer_context.py`：快照校验、摘要投影、工具绑定、Job 会话约束；移除 current_info 的 Batch 查询。
- `backend/agent/loop.py`：仅增加必要的工具可用名单与绑定调用分派，不引入关键词树。
- `backend/services/store.py`：新增 Job 的会话归属查询，兼容旧 Task 与 Goal/Item/Intent 关联；不改变状态推进。
- `backend/tools/search_scenes.py`：职责说明及空工作区不扩域。
- `backend/services/scene_search.py`：旧递归 fallback 增加遍历条目预算。
- `frontend/src/lib/viewerContext.ts`：消息深复制、观察 Provider 安全注销、保留已注册 Store Provider。
- `frontend/src/stores/{batchDraft,batchGoal,batchQuality,qclist}.ts`：草稿/选择/审核 Item 元数据快照。
- `frontend/src/components/BatchRepairDraft.vue`：真实勾选状态/排除数与版本，不改执行入口。
- `backend/tests/test_repair_workspace.py`、`test_message_viewer_context.py`、`test_search_scenes.py`、`test_tool_description_contract.py`：新契约及兼容断言。
- `frontend/src/lib/__tests__/repairWorkspace.test.ts`：12/2 冻结、切换、Provider 生命周期与本地同名 TIFF 身份隔离。
- `backend/eval_repair_workspace.py`：固定中文前后对照评测；`.e2e/workspace-eval.json` 为回放原始结果。

## 性能与评测口径

当前影像、草稿、Batch 50 景进度、Item 详情都不再依赖场景库检索，也不读 Manifest。工作区摘要不包含完整 50 景清单；Item 页最多 50 项，异常最多 3 项，驳回原因最多 5 项，单个文本最多 512 字符，再经过模型结果预算。

旧 `scene_search.scan_root` 原先 `sorted(base.rglob('*'))`，每次先遍历并排序全部条目；结果 limit 不限制扫描。现改为流式遍历、最多访问 20,000 条目，超限明确失败，不把部分结果伪报为全库。当前没有统一可直接替换的持久资源索引；保留现有 `_workspace_rows` 精确解析。此预算只限制访问数量，不保证网络盘单个目录访问耗时；全库索引属于后续工作。

固定中文 9 例涵盖：本地图、12/2 草稿、50 景 Batch、失败、待审核、第三景、空工作区、已知 Job、明确搜索其他场景。命令：

```powershell
python -m backend.eval_repair_workspace --baseline-ref HEAD
# 配好真实模型后运行；仍全部走 Fake Executor，绝不调用生产工具：
python -m backend.eval_repair_workspace --baseline-ref <改造前提交> --live --output .e2e/workspace-live-eval.json
```

回放使用固定工具调用脚本及隔离 Fake Executor，基线源码通过 `git show` 在内存加载，不替换工作树。

| 回放指标 | HEAD 基线 | 本轮 |
|---|---:|---:|
| 预期工具实际可执行 | 3/9 | 9/9 |
| search_scenes 非搜索用例调用 | 0 | 0 |
| 工具调用总数 | 9 | 9 |

这是**工具契约可达性回放，不是模型选择准确率**。固定脚本不会自发误选搜索工具，因此 0→0 不能证明模型误调用减少。当前仅有默认公网端点且无 API Key，真实模型正确工具选择率、首次有效回答耗时、无依据检索/错误状态报告次数尚未取得；脚本保留真实调用列表/延时/答案供人工审核，错误状态报告字段不伪造分数。Fake 首次有效工具耗时不代表真实首次有效回答，也不代表内网性能。

同一 50 项合成草稿的用户消息投影，UTF-8 JSON 从 5,967 字节降至 279 字节（约减少 95.3%）；不包含系统提示，不等同实际模型 token 数或线上延迟。原始度量保存在评测 JSON 的 `draft50_projection_utf8_bytes`。

## 验证与边界

测试使用临时 SQLite、合成记录和 Fake 查询服务，不调用真实生产 SR。

- 工作区/描述/消息恢复/搜索/快照/Batch planning/Job 查询组合：125 passed，包含 Full Result 跨会话与大页预算专项。
- Batch Worker、人工质量、Goal Runtime、Registry 独立组合：98 passed。
- 前端全量：1,069 passed；类型检查及生产构建通过。vendor 缺少 geotiff.js.map 与大 chunk 提示不影响退出码。
- 较宽后端组合：306 passed、50 failed、1 skipped、1 error；相同顺序的 HEAD 内存基线为 305 passed、51 failed、1 skipped、1 error。当前失败测试名称全部存在于基线失败集合，没有新增失败名称。主要为 Windows SQLite 文件占用、测试环境变量/路径及 Executor 假设；部分代表用例单独运行可通过。不能据此宣称后端全绿。
- Agent Loop 事件专项存在 5 项 thinking_status 旧断言失败；HEAD 原码复跑同样 5 failed、2 passed。
- 初次 preview_scope 组合另有 9 项 Windows SQLite 清理文件占用，未改该测试体系。

原始日志：`.e2e/workspace-{focused,batch-regression,regression,baseline-wide,baseline-loop,frontend,build}.log`。HEAD 基线通过 `git show` 只在子进程内加载，对工作树未作还原。

已覆盖：本地 TIFF 查询与快照冻结、12 选中/2 排除摘要、50 项分页与重开数据库、第三项详情、人工质量与产物验证分离、跨会话拒绝、旧消息恢复不使用最新浏览器状态、空工作区不扩域、显式库搜索兼容、旧扫描预算与现有结果预算机制。

内网待核：真实盘阵冷读、20,000 条目预算是否适合现场规模、真实模型中文选择质量、真实浏览器刷新/Worker 进程重启联合验收、GPU/SR 产物质量与人工审核端到端。本轮数据库 reopen 测试不是完整 Worker 重启或生产验收。

## 下一阶段最小剩余工作

`prepare_repair` 可复用消息 image_key/revision、草稿 ID/version、分页 Item 身份，在服务端重新校验范围、冻结输入版本并生成可审阅计划。`start_repair` 需绑定该计划版本和既有人工确认入口，复用幂等 Batch 创建；生产准入、路径白名单、Mask 审批、SR 契约及质量人工复核仍各自保留。本轮不增加任何自动开始、覆盖或代审批动作。
