# 批量修复 PR F 实施记录：进度 Panel、事件同步与跟随（2026-11-03）

> 状态：**已实施；快照 / 控制 API / 事件分页 / Batch Store / Panel / Overlay 跟随 / 50 景 Fake 验收全部完成。**
> 只证明 demo/Fake SR 的监控事实链，不证明真实 SR/GPU 修复质量；production 提交门仍关闭。
> 前序见 [batch-pr-c.md](batch-pr-c.md)、[batch-pr-d.md](batch-pr-d.md)、[batch-pr-e.md](batch-pr-e.md)。
> 基线：`main` @ `1bc2bea`，本机 Windows；未执行真实 SR/GPU，未扫描盘阵，未读写真实生产数据。

## 1. 目标与八项修订

目标是一个可信的异步生产监控工作台：所有进度数字来自一次一致的快照读取，SSE 只提示「有新事实」并提供时间线，不按事件条数累加业务进度。实施前按八项修订收敛：

| # | 修订 | 落地 |
| --- | --- | --- |
| 1 | 控制版本独立于事件流与 `goal.version` | `checkpoint.control_revision` 只在用户控制事务内自增；Monitor 写 `monitoring_done` 不碰它 |
| 2 | 批次关联不盲取最新一条 | URL `batch_goal` > 用户本地记录 > 同一会话的活动批次；无会话不自动绑定，跨会话的过期本地记录不采纳 |
| 3 | 自动跟随默认关闭 | 选图、缩放、平移、分屏、切侧都会使 `viewer.userIntentSeq` 加一并立即退出跟随；绘制期间只暂停导航 |
| 4 | R=0 的进度语义 | 检测未完成时显示「暂无需修复项·统计中」，完成后才显示「不适用」；分母增长导致比例下降是正常数据 |
| 5 | 快照水位与已消费游标分离 | 重连时先重读快照，再从 `consumedSeq` 用 `after` 分页追平，绝不直接跳到 `last_seq` |
| 6 | 首屏信息优先级 | 概览/统计 → 三维进度 → Producer 当前项 → Monitor 队列；事件时间线默认折叠 |
| 7 | 并行检测的表达 | 主卡片显示代表项，并写明「另有 N 景同时检测中」与在途总数 |
| 8 | 先处理基线差异 | 修正 Fake SR 作业寿命与 mock 查询耗尽后计数失真的测试夹具，再开始 Panel |

## 2. 快照契约 `batch_snapshot.v1`

`backend/services/batch_snapshot.py` 是纯投影，`Store.batch_snapshot()` 在同一个 SQLite 读事务内取出 Goal、全部 `batch_item` 与相关 `sr_tasks`，返回唯一可信的进度文档：

- `counts`：总景数、检测/提交/验证计数、失败/受阻/排除/跳过、状态未知、在途 SR。
- `progress.detection/submission/verification`：每轨 `done/total/applicable/known`。`applicable=false` 表示分母为 0；`known=false` 表示检测尚未完成，0 分子不能当成定论。
- `producer`：代表项（`OPENING` > `DETECTING` > `SUBMITTING` > `MASK_READY`）、`detecting_count`、`preparing_count`、`prepared_count`。
- `active_jobs` 与 `recent_jobs`：先活动、再异常、最后最近结束的尾部；带 Job ID、SR/任务状态、未知标记、失败原文与影像路径。两者都只投影，不读产物文件。
- `goal.control_revision`、`goal.monitoring_done`、`goal.last_seq`（快照水位）。
- `report.available/updated_at`：由受控报告入口 `stat` 得出，前端不构造服务端路径。

REST：`GET /api/goals?kind=batch&limit=`、`GET /api/goals/{id}/batch-snapshot`、`GET /api/goals/{id}/batch-report`。非 batch Goal 返回 404，报告路径由服务端拼接并校验 artifact root。

## 3. 用户控制与并发

`POST /api/goals/{id}/{pause|resume|cancel}` 接受 `{expected_version}`（也接受 `expected_revision`），在 `BEGIN IMMEDIATE` 内比较 `control_revision`：

- 不一致 → 409 `stale_revision`，返回最新 `control_revision`；前端重读快照并要求用户确认。
- 缺少版本 → 400 `missing_revision`。
- 幂等：已暂停再暂停、已取消再取消返回 `changed=false`；终态批次不可暂停 → 409 `invalid_state`。
- 用户动作追加事件 `batch_user_control`；Monitor 继续写 `batch_control_changed`，两者分开统计。
- 暂停/取消只停止派发，不终止已经提交的外部 SR 作业；UI 与返回文案都写明这一点。

调度器每次领取会提升 `goal.version`，因此控制版本不使用 `goal.version`。测试覆盖「快照后 Monitor 写入 checkpoint，再暂停」不产生假冲突。

## 4. 事件分页与追平

`GET /api/goals/{id}/events` 支持 `after`（正向追平）与 `before`（向历史翻页），两者互斥；上限 1000，默认 200。返回 `next_after/has_more` 与 `next_before/has_earlier`。

前端维护两个数：

- `snapshot.goal.last_seq`：这组统计对应到的事件水位；
- `consumedSeq`：已经真正并入时间线的位置。

挂载时先读快照，再用 `before=last_seq+1` 取最近 20 条历史；SSE 从 `consumedSeq` 开始。断流重连固定执行「重读快照 → 从 `consumedSeq` 分页追平 → 重开 SSE」。追平遇到游标不前进、页数上限或服务端提前说没有更多，保留游标并标记中断，绝不把游标直接推到快照水位。

## 5. Panel 与查看器跟随

`ContextPanel` 增加第三个「批次」Tab，仅在存在可关联批次时出现；批次消失时退回 Agent。Pane 使用 `v-show` 常驻，切换 Tab 不打断事件流。

- `BatchOverview`：真实状态/阶段、`DEMO / MOCK` 徽标、9 项紧凑统计。
- `BatchProgressTracks`：三维进度条 + 事实数字；不伪造百分比。
- `BatchCurrentItem`：代表项、在途数量、只用现成 JPG 的缩略图（不生成预览、不解码 TIFF）；「定位影像」「查看掩码叠加」。
- `BatchJobQueue`：活动/异常/尾部、Job ID、时间、状态未知标记、失败原文可展开、「仅异常」过滤。
- `BatchControls`：暂停/继续/取消（取消需二次确认）、只读报告；控制冲突与外部作业不终止提示。
- `BatchEventTimeline`：默认折叠、最新在前、`loadEarlier`、原始 payload 按需展开。

自动跟随默认关闭。开启后只在当前代表项变化时打开对应影像，并以 `origin='follow'` 设置 `viewer.batchOverlayTarget`，因此不会把跟随自己当成用户操作。用户任何明确视图操作都会退出跟随；绘制模式期间导航暂停，但不改写 ROI、掩码或批注。叠加层仍以 `overlay.v1` 文档身份校验为准；显式关闭后 URL 里的初始目标也不能复活它。

批次切换与跟随取图都按「代次」校验：`attach/associate` 各自持有加载代次，快照、近期事件、追平、控制响应在提交前都要核对代次与 Goal ID；同批次刷新另有递增序号，旧响应不能回退水位。跟随打开会把 Goal、当前项、`userIntentSeq` 与绘制状态封进 `stillWanted` 守卫，场景 store 在解析完成和 JPG 取回后各检查一次，查看器在解码完成后、`activate` 之前再检查一次；过期时撤掉刚入列的卡片，不改激活目标，因此迟到的图不会抢走用户已经切到的视图。

## 6. 文件

| 文件 | 变更 |
| --- | --- |
| `backend/services/batch_snapshot.py`（新增） | 纯投影、进度语义、当前项、活动队列、`recent_jobs`、控制版本 |
| `backend/services/store.py` | 分页事件、快照读事务、`batch_control` 事务 CAS 与用户事件 |
| `backend/services/goal_runtime.py` | 快照/报告路由、`kind=batch` 过滤、分页参数、控制 409/400、SSE 终态按 `monitoring_done` |
| `backend/tests/test_batch_snapshot.py`（新增） | 投影语义、REST 一致性、分页、控制并发、SSE 终态、`recent_jobs` 排序 |
| `backend/tests/test_batch_worker.py` | 修正 Fake SR 寿命与 mock 查询耗尽后的计数夹具 |
| `frontend/src/lib/api.ts` | 快照/事件/控制/报告类型与端点，SSE 裸 JSON 帧，`HttpError.detail` |
| `frontend/src/lib/batchUi.ts`（新增） | 状态/阶段/进度文字、时间、事件摘要与色调 |
| `frontend/src/lib/batchEventSync.ts`（新增） | 事件去重、`after` 追平、快照刷新判定、批次关联 |
| `frontend/src/stores/batchGoal.ts`（新增） | 快照同步、SSE 重连、控制、关联、跟随 |
| `frontend/src/stores/viewer.ts` / `scenes.ts` | 用户意图序号、叠加层目标与显式关闭、静默打开 |
| `frontend/src/components/Batch*.vue`（新增） | Panel 六个组件与 Tab 集成 |
| `frontend/src/components/BatchDetectionOverlay.vue` / `NoticeModal.vue` | 面板目标优先、原始 JSON 展开 |
| `.e2e/test-batch-panel.js`（新增） | 浏览器验收，见 §7 |

## 7. 测试

实际命令与结果：

```text
python -m pytest backend/tests/test_batch_snapshot.py -q
# 25 passed in 9.47s

python -m pytest backend/tests/test_batch_snapshot.py \
  backend/tests/test_batch_worker.py backend/tests/test_goal_runtime.py -q
# 102 passed in 88.44s

python -m pytest backend/tests/test_batch_worker.py -q -k fifty -v
# 1 passed, 45 deselected in 26.64s
# test_fifty_scene_fake_sr_jobs_complete_without_duplicate_submits

python -m pytest backend/tests -q
# 5 failed, 1318 passed, 7 skipped in 546.57s (0:09:06)
# 5 个失败全部是既有问题：backend/tests/test_agent_loop.py 的 TestOnStepSeam
# thinking_status 事件顺序断言；main @ 1bc2bea 基线为 1293 passed，
# 本轮新增 test_batch_snapshot.py 的 25 条后为 1318 passed。

cd frontend && npm run typecheck
# 零错误

cd frontend && npx vitest run src/stores/__tests__/batchGoal.test.ts
# 10 passed（含 A/B 快照乱序、同批次刷新乱序、跟随迟到打开 3 条竞态用例）

cd frontend && npm test
# check:terms 通过；46 files / 658 tests passed in 12.69s

cd frontend && npm run build
# built in 8.94s（仅既有 chunk >500 kB 警告）

cd .e2e && node test-batch-panel.js
# Batch Panel browser checks passed: tab gating via URL, 50-scene snapshot
# tracks/current/queue, collapsed event timeline with raw payload,
# control 409 then success notice, controlled report modal, follow exits on user intent
```

浏览器验收使用 `frontend/dist` + 本地假服务（`test-batch-panel.js`），提供 50 景快照、20 条事件、受控报告与两次控制请求（先 409、后成功）。它是 Panel 的渲染与交互验收，不是 50 景运行链路验收；后者由后端 `test_fifty_scene_fake_sr_jobs_complete_without_duplicate_submits` 覆盖。

## 8. 已验证不变量

1. 业务进度只来自快照；SSE 条数不参与计算。
2. `control_revision` 只随用户控制变化；Monitor 更新与调度器 `goal.version` 提升都不会造成控制冲突。
3. 控制请求带版本；不一致返回 409 并重读，不静默覆盖他人操作。
4. 事件重连从已消费游标追平；页数上限或游标不前进时保留断点，不跳水位。
5. R=0 且检测未完成显示「暂无需修复项·统计中」；完成后的 0 才显示「不适用」。
6. 批次自动关联不跨会话、不盲取列表第一条；过期本地记录会回退到同会话活动批次。
7. 跟随默认关闭，任何用户视图操作立即退出；绘制期间不改写 ROI/掩码/批注。
8. 叠加层仍按 `overlay.v1` 身份校验；显式关闭后 URL 初始目标不复活。
9. Panel 不直接读 `artifact_root`、不解码 TIFF；缩略图只取现成 JPG。
10. 报告只经受控只读端点获取，前端不拼接服务端文件路径。

## 9. 未覆盖的真实环境边界

- **真实 SR/GPU/盘阵**：50 景验收仍为 Fake 调度器与合成数据；真实并发、真实作业时长、真实阵列对账未验收。
- **真实进度规模**：浏览器验收用假快照模拟 50 景，未在真实 50 景批次上长时间观察 Panel 的刷新、重连与内存表现。
- **SSE 代理行为**：只验证了本地直连与假服务；nginx/内网代理下的长连断开、缓冲与超时未演练。
- **Last-Event-ID**：服务端仍只支持 `after` 查询参数，未实现 `Last-Event-ID` 头。
- **真实修复质量**：demo 成功仍是 `demo_contract`/`provenance=mock`；production 质量层与提交门未开放。
- **ROI 编辑中的跟随**：单元测试覆盖「绘制时暂停、退出后跟上」，未在真实 ROI 绘制与掩码编辑上做浏览器验收。
- **报告内容**：受控入口只验证了存在性与摘要；真实报告字段、分页与超大 JSON 的展示未验收。
- **新批次的实时发现**：`associate` 只在 Panel 挂载、切换会话或手动重试时重读批次列表；同一会话运行期间由别处新建的批次不会自动出现在已打开的 Panel，需切换会话或刷新页面。本轮未实现创建入口与推送通知。
- **事件历史超过尾部缓冲**（已知限制，未修）：时间线合并后只保留最新 500 条，累计更早的历史会被裁掉；「加载更早」仍以当前最早一条为游标，可能重复请求同一页。历史分页需要与实时尾部缓冲分开维护游标，本轮按优先级延期。

## 10. 下一步

- 在内网机用真实 50 景批次观察 Panel 长连、重连与快照刷新；确认 nginx 对 SSE 的缓冲与超时配置。
- 真实 SR 准入仍需单独确认 Detector/Mask、产物契约与质量 Verifier；在此之前 production 批量仍拒绝执行。
- 若后续需要 `Last-Event-ID`，应在服务端与 `subscribeGoalEvents` 同时改造，不在前端猜事件。

## 11. 只读复核后的竞态修订（2026-11-03）

复核结论为 PR F 基本通过，主要遗留集中在前端异步请求。按快速原型目标处理前两项，第三项延期。

| # | 问题 | 修订 |
| --- | --- | --- |
| 1（P1） | 切换批次后，A 的迟到快照可能覆盖 B 的统计与控制版本 | `attach/associate/refreshSnapshot/catchUp/control/loadEarlier` 均携带代次与 Goal ID，响应提交前校验；同批次刷新另有递增序号；追平任务按代次隔离，不被新批次的 `catchUp` 复用 |
| 2（P2） | 退出跟随后，迟到的打开请求仍会改动查看器 | `openQuiet` 接受 `stillWanted` 守卫；场景 store 在解析后、JPG 取回后各检查一次，并把守卫传给 `viewer.openSceneJpg`，在 JPG 解码完成后、`activate` 之前再检查一次；守卫校验 Goal、当前项、`userIntentSeq`、绘制状态与跟随开关 |
| 3（P2） | 「加载更早」累计 500 条后游标不前进 | 未修，记为已知限制（见 §9）；需要历史游标与实时尾部缓冲分离 |

新增测试（`frontend/src/stores/__tests__/batchGoal.test.ts`，10 passed）：A/B 快照乱序返回、同批次新旧刷新乱序、跟随取图途中用户操作后不调用 `openSceneJpg` 且不设置叠加层。复核指出查看器内「解码后仍无条件 `activate`」的窗口，现已把 `stillWanted` 传进 `openSceneJpg` 并在 `applySceneJpgToRec` 之后、`activate` 之前检查；该分支依赖 JPG 解码，未新增单测（现有用例在调用 `openSceneJpg` 前即命中守卫），由本轮 `typecheck`、全量测试、`build` 与浏览器验收覆盖其余路径。

复核给出的后端专项结果为 **102 passed in 97.74s**，与本记录 §7 一致。本轮修订后已重跑前端全量（658 passed / 12.69s）、`typecheck`、`build`（8.94s）与 `test-batch-panel.js`（通过），未重跑后端全量。
