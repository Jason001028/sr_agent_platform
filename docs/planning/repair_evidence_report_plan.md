# Agent 修复图像证据报告实施规划

> 日期：2026-11-17 · 状态：评审（用户已认可功能方向；本文为开发交接规划，功能尚未实现）
>
> 目标：修复批次计算结束后，生成可持久化、可逐区复核的结构化报告。每行左侧为同位置 NOSR / 修复产物局部双图，右侧为多模态分析、建议和 Viewer 定位入口；每页最多 30 行。生成证据和模型分析不得成为检测、掩码落盘、SR 提交或人工质量验收的前置条件。
>
> 当前交付：仅规划文档。本文中的新增模块、表、接口、配置和测试均是建议实现契约，不代表仓库已有能力。实现者先完成 §12 的逐步工作包，再按 §13 验收。真实盘阵吞吐、真实模型视觉准确性均为待核。

## 1. 阅读顺序与范围

### 1.1 开工必读

1. `AGENTS.md`。
2. `docs/status/current-question.md`、`docs/experience/gui-experience.md`。
3. `docs/status/repair-dual-entry.md`：面板与 Agent 共享 Repair Application Service，计划确认、执行和验收契约。
4. `docs/status/batch-human-quality.md`、`docs/status/smart-compare.md`：区域坐标、候选不等于缺陷、算法证据的精度与局限。
5. `docs/sr_code/pan_repair_contract.md`、`docs/sr_code/production-scene-naming.md`：PAN 发布、NOSR 和修复前备份身份。
6. `docs/status/multi_user_preview_scheduling.md`、`docs/status/multi-user-api-blocking.md`：有界重任务调度和 API 事件循环边界。
7. `docs/conventions/terminology-conventions.md`、`docs/conventions/current-state-conventions.md`、`docs/conventions/motion-conventions.md`。

用户明确要求本规划包含逐步实施和检查。因此下文列工作包；实施完成后，状态文档写真实当前能力，待核项保持待核，不把计划写成已完成事实。

### 1.2 首版范围

- 面板自动修复与 Agent 发起修复共用报告，不建立第二条 SR 执行链。
- 首先覆盖有服务端源文件、真实产物验证通过、有当前质量审核绑定的 production 修复 Item。
- 候选掩码逐区通过后，以最终用于 SR 的区域为报告区域。被驳回区域不进入修复效果分析。
- 手绘掩码通过同一单 Item Batch Goal 路线接入；必须保留最终矢量区域与 ID。几何缺失的旧任务显示不支持精确区域报告，不能为出报告重新扫描整幅掩码。
- 不要求影像已在 Viewer 打开，取图直接依赖服务端绑定；只存在于浏览器、服务端无法读取的本地源显示不可用原因。
- 批次部分失败仍提供报告，失败、跳过、无候选、未分析项有准确计数。
- 模型结论是辅助复核意见，不批准掩码、不批准产物、不修改质量等级、不自动重跑 SR。
- 不增加训练、SR 算法改造、云服务依赖、CDN、新编排框架或外部向量数据库。
- 首版不含 PDF / Excel 导出；保留版本化 JSON 数据下载能力，方便审计和调试。

## 2. 用户体验与业务规则

### 2.1 入口和版式

- Agent 修复卡与面板任务中枢增加「图像证据报告」入口，共用同一报告 ID。
- 建议使用独立路由 `/repair-reports/:reportId`，足够宽的内容区展示长表；280/320px 侧栏只放报告状态和入口。
- 不在聊天消息内输出 30 行 Markdown 表格。聊天仅显示结构化报告入口和简短覆盖摘要。
- 每页最多 30 行，数据服务端分页。30 是显示限制，不是默认只分析 30 个区域。
- 行按景分组，稳定键为 `report_id + row_id`，不能用序号或文件名充当身份。
- 左侧 NOSR 在左、产物在右，顺序和标签固定。桌面每张显示约 160–200 CSS px，点击展开原始证据图。
- 右侧列名「修复观察与建议」，允许改善、残留问题、疑似新增伪影、无法判断，不强迫每行发现伪影。
- 每行有「定位复核」；展开可看修复前 PAN、数值指标、观察范围、采样尺度、证据缺失原因。
- 窄屏改为同行上下排列或卡片；长文件名省略但可查看全文；固定图片宽高避免加载时跳动。
- 无模型时仍展示已生成的图像与指标，分析栏明确显示「模型未配置 / 暂未分析」。

### 2.2 报告头部与覆盖

头部至少显示：批次名称、报告版本、计算状态、证据状态、模型名称、生成时间、数据当前性、覆盖统计。

统计字段分开计算，不能使用「成功率」混合表达：

- 总景数、产物验证通过景数、失败景数、跳过景数、无候选景数。
- 最终修复区域总数、已规划区域数、证据就绪区域数、已分析区域数、未分析区域数、失败 / 过期区域数。
- 本页行数、筛选后行数、全部报告行数。
- 每行实际检查窗口数与 ROI 覆盖性质：完整上下文 / 抽样细节 / 仅预览。

示例：「27 景，46 个修复区域；已分析 40 区域，6 区域待处理；当前第 1 页，共 30 行」。如果受分析预算限制，只分析前若干区域，必须列出未分析数和原因。候选之外的区域不属于首版检查覆盖，报告不得写整景全面质检通过。

### 2.3 双图与三图的结论边界

- `NOSR → 产物`：观察相对低分辨率输入的结构一致性及疑似新增伪影。
- `修复前 PAN → 产物`：评价本次修复的改善、残留或新增问题。
- 仅有 NOSR / 产物时，禁止输出「相比修复前明显改善」；缺修复前证据须写明。
- 无真实高分辨率真值时，禁止输出「恢复了真实细节」「已证明没有幻觉」。
- 数值变好不等于产物质量通过；模型意见不写入人工审核决定。
- 模型分析完成不改变 Goal 完成判定、`monitoring_done`、verified 数和人工质量计数。

## 3. 已核对的代码接缝

以下事实来自当前仓库阅读；开工时重新核对函数签名和工作树，保留用户已有未提交修改。

| 代码位置 | 当前能力 | 本功能的接入方式 |
|---|---|---|
| `backend/services/repair_application.py` | 面板 / Agent 共享计划、确认、启动、状态 | 暴露报告引用；不另起修复链 |
| `backend/services/batch_worker.py` | 检测、Mask、提交、监控、五层验证、确定性批次报告 | 读取已持久化状态判断计算完成，不把证据变成阶段门禁 |
| `backend/services/batch_mask.py` | 有界内存逐条带栅格化掩码 | 复用冻结几何；栅格化过程不额外读取 PAN / NOSR |
| `backend/services/auto_mask_geometry.py` | 候选几何、来源绑定、源坐标映射 | 统一区域身份和坐标；不重复实现候选检测 |
| `backend/services/batch_quality.py::make_binding` | 绑定 input、nosr、output、backup、mask、receipt、regions | 证据来源首选；修复前图由已验证 backup 取得 |
| `backend/services/batch_quality.py::validate_current` | 源和审核绑定有效性检查，包含较重检查 | 在证据准备后台复用，不能在报告列表轮询里反复完整调用 |
| `backend/services/image_diff.py::read_source_window` | 无压缩单波段条带 TIFF 有界窗口，返回实际窗口 | 当前按采样行读取整行；新增证据读取器优化局部列读取 |
| `backend/services/heavy_work.py::HeavyWork` | 应用级单槽栅格重任务，交互 / 后台两级队列 | 增加真正最低优先级证据任务，不能直接使用现有后台公平轮转 |
| `backend/api/app.py::lifespan` | 管理轮询、预览、Batch 等后台任务与关闭 | 管理证据任务循环、回收、停止顺序 |
| `backend/services/store.py` | SQLite schema、线程独立连接、事务、Goal / Item / 审核持久化 | 增加独立报告表与短事务，不复用 Goal 租约写报告 |
| `backend/services/goal_runtime.py` | `/api/goals` 路由、质量文档和批次报告 | 保留现有确定性报告；新证据报告独立路由 |
| `backend/agent/loop.py::_default_chat` | 图像水合后调用模型 transport | 提取可复用只读模型调用接缝，避免从服务层依赖整个私有 Loop |
| `backend/agent/async_transport.py` | 模型请求、超时、取消、端点兼容 | 复用预算与端点配置；证据分析禁用工具调用 |
| `backend/services/chat_images.py` | 聊天截图存相对 URL，调用时转 data URL | 参考边界；当前聊天 URL 解析与图片数量约束不能直接用于证据三图 |
| `frontend/src/stores/diff.ts::autoDraw` | 自动绘制、逐区通过、最终加入可编辑掩码 | 从最终提交的几何获取区域，不从动画帧截图 |
| `frontend/src/stores/batchMaskReview.ts` | 候选逐区审批 | 最终通过集合为报告几何来源 |
| `frontend/src/stores/batchQuality.ts` | 加载产物 / NOSR、同步对比、区域定位、审核 | 复用 Viewer 定位；扩展历史只读和修复前图比较 |
| `frontend/src/lib/batchDock.ts` | `requestQualityReview(itemId, regionId)` | 现有请求缺显式 goalId；需补跨批次安全定位 |
| `frontend/src/components/BatchProgressPanel.vue` | 消费质量复核请求 | 核对 Goal → Item → Region 的加载顺序 |
| `frontend/src/components/AgentRepairPlanCard.vue` | 修复计划、运行、成果、复核入口 | 增加轻量报告入口，不塞长表 |
| `backend/tests/quality_browser_fixture.py` | 合成修复、实际应用、审核夹具；禁止 SR 提交 | 扩展证据报告浏览器夹具 |

注意：`backend/api/app.py::_thumbnail_url` 的同景共享缩略图仅用于浏览，不能充当 input / output / NOSR 各阶段的分析证据。

## 4. 总体架构与计算完成判定

### 4.1 两条独立流程

```text
既有流程：计划确认 → 检测 → 区域确认 → Mask → SR → 产物验证 → 人工质量验收
                                            ↓ 已持久化事实
证据流程：发现可报告批次 → 冻结区域与来源 → 等待空闲 → 裁片 → 指标 → 模型分析 → 分页报告
```

首版以独立后台协调器低频查询数据库，不在检测 / 提交调用栈同步裁图。可以在既有轻量事件里携带报告可用提示，但正确性不依赖事件一定被消费；协调器须能从数据库重建遗漏任务。

### 4.2 不等待人工验收才生成

`Goal COMPLETED` 和 `monitoring_done=true` 不是生成报告的唯一前提。部分 Goal 会保持 `waiting_quality_review`，正需要报告辅助验收。

实现纯函数 `report_compute_readiness(goal, items, executor_snapshot)`，返回：

```text
computing | waiting_input | ready | partial | indeterminate
```

- `computing`：本批次仍有可执行的检测、提交、运行、产物验证等计算工作。
- `waiting_input`：仍有等待掩码决定 / 补齐输入的项；首版不称完整报告，可按用户请求提供已有部分。
- `ready`：本批次没有未结束的计算，所有可报告项已具备经过验证的产物，人工验收可尚未完成。
- `partial`：计算已停止，部分项失败、跳过、受阻或无候选，已成功项可以报告。
- `indeterminate`：存在 SUBMITTING 结果不明、UNKNOWN 外部作业、执行器 quarantine 等，不能假定磁盘已空闲。

不能只判断 Goal phase 字符串。结合当前 `MONITORED`、`PRE_SUBMIT`、`PREPARING`、checkpoint 的 contract / artifacts 验证层及真实执行器状态建立状态矩阵；人工待审、无候选待审与掩码待审须分开。

取消批次仅在已确认外部执行停止后允许报告已完成项，不能把 cancellation requested 当实际停止。

### 4.3 自动创建与重建

- 面板与 Agent 均在符合上述条件时自动创建或复用报告。
- 协调器每轮仅取有限数量候选，使用 DB 索引和分页；不递归盘阵、不每轮扫描全部历史 Goal。
- 通过持久化扫描水位和最近状态变更查增量；首次开启仅处理启用后批次，历史报告由显式请求生成。
- 批次等待人工处理后又继续计算时，报告标记 `partial` 或 `superseded`，增量部分产生新报告版本；旧版本保留。
- 不使用聊天 SSE 驱动证据任务生命周期；关页面不停止后台持久任务。

## 5. 数据契约与持久化

### 5.1 标识和版本

- `report_id`：服务端生成的不透明 ID。
- `row_id`：由 item_id、最终区域 ID 和 geometry hash 稳定派生；位置排行变化不改 ID。
- `source_set_hash`：规范化序列化的 Job / 审核绑定、所有角色文件版本、mask hash、最终几何及配准来源摘要。
- `evidence_key`：来源身份 + source_set_hash + 实际窗口 + 输出尺寸 + 配准策略 + 拉伸参数 + 编码 / 读取器版本。
- `analysis_key`：有序证据文件 sha256 + 数值指标版本 / 数据 + prompt 版本 + schema 版本 + 模型配置身份。
- 路径 basename 不参与唯一身份；同名 `PAN.tif` 不同景必须完全隔离。
- 图片 hash 针对已生成的小文件；不为每个 ROI 新增一次整景 TIFF sha256。源身份沿用现有 revision / image_key / receipt，读取前后核验。
- API 给相对资源 URL 和不透明角色引用；客户端不能提交任意盘阵路径、文件 URL 或模型地址。

### 5.2 建议新增表

采用现有 Store schema / migration 风格，字段可以按实现收敛；以下语义必须保留。

| 表（建议名） | 核心字段 / 约束 |
|---|---|
| `repair_evidence_reports` | report_id PK；goal_id；所属 session / 现有作用域；revision；source_set_hash；status；compute_state；coverage_json；settings_json；created_at / updated_at；UNIQUE(goal_id, source_set_hash, recipe_version) |
| `repair_evidence_rows` | report_id + row_id 联合 PK；item_id；job_id；review_id；region_id；ordinal；geometry_json；sources_json；evidence_manifest_json；evidence_state；analysis_state；latest_analysis_id；error_code；version |
| `repair_evidence_tasks` | task_id PK；report_id；row_id；stage；fingerprint UNIQUE；state；attempts；next_wake_at；lease_owner / lease_token / lease_until；expected_row_version；error_json |
| `repair_evidence_analyses` | analysis_id PK；report_id / row_id；analysis_key；model_identity；prompt_version；schema_version；evidence_hashes_json；result_json；status；timings_json；created_at；UNIQUE(report_id, row_id, analysis_key) |

建议索引：报告 goal_id / updated_at；任务 state / next_wake_at / lease_until；行 report_id / ordinal；分析 report_id / row_id。

大图片与 base64 不进 SQLite。证据 manifest 和分析输入摘要大小受限，异常日志不带 API key 和 base64。

### 5.3 文件布局（建议）

```text
<SR_BATCH_ARTIFACT_DIR>/evidence/<report_id>/<row_id>/<evidence_key>/
    manifest.json
    context_nosr.png
    context_before.png          # 有经过验证的备份时存在
    context_output.png
    detail_01_nosr.png
    detail_01_before.png
    detail_01_output.png
    pair_thumb.jpg             # 仅供 UI；模型读各角色独立 PNG
```

- 图片先写同目录临时文件；全部完成且校验成功后原子发布目录或 manifest。DB 最后置 ready。
- 中断后没有 ready manifest 的文件不当成有效证据；恢复可复用已验证完整目录。
- manifest 记录尺寸、编码、文件 sha256、角色、actual_window、像素尺度、有效范围、显示映射、几何版本、配准来源和读取预算。
- 模型用无损 PNG，避免 JPEG 压缩伪影影响判断；UI 合成缩略图可以 JPEG。
- 不将证据写进生产场景目录，不修改 PAN、NOSR、备份或 Mask。

### 5.4 状态拆分

- 证据：`planned / queued / waiting_idle / reading / ready / unsupported / failed / stale`。
- 分析：`not_requested / queued / waiting_idle / running / ready / unavailable / failed / stale`。
- 报告：`pending / processing / ready / partial / failed / superseded`。
- ready 表示该阶段完成，不表示质量通过。报告全失败才 failed，部分缺失用 partial。
- 原始 JSON 结构不合法与模型拒绝 / 不支持图像单独错误码；不能用虚构结论补齐。
- 重试分析产生新的 analysis 记录，保留已有结果；源或最终几何变化产生新证据 / 报告版本。

## 6. 区域规划、坐标、尺度与显示映射

### 6.1 在最终几何上规划

1. 从最终提交 Mask 对应的候选集合 / 手绘矢量得到 `region_id`、源 bbox、polygon、source revision。
2. 核对该集合与当前质量审核绑定一致。新近通过的掩码子集替换初始候选后，只能使用新集合。
3. 保留 polygon 及孔洞语义；裁片是外围窗口，原区域轮廓独立叠加。不能因为裁正方形扩大实际 SR 掩码。
4. 旧任务缺少几何时 `unsupported_geometry`，提供现有成果查看入口；不在请求里解码整幅 Mask 找连通域。

### 6.2 明确两种坐标约定

当前区域 bbox 使用包含终点的像素坐标，原始读取窗口使用半开区间。统一转换在纯函数内完成：

```text
区域 bbox [x0,y0,x1,y1] inclusive
读取窗口 [floor(x0),floor(y0),ceil(x1)+1,ceil(y1)+1) half-open
宽度 = read_x1 - read_x0；高度同理
```

polygon 顶点和像素中心约定沿用现有自动掩码 / Pillow 栅格化契约。所有后续映射使用返回的实际读取窗口，不用被影像边界裁掉之前的请求值。

### 6.3 上下文与细节窗口

初始默认值作为待实测参数，写入报告 settings，不宣称最优：

- 上下文目标 512×512；以 ROI bbox 中心扩展为正方形，外扩系数 1.25，最小源边长 512，最大源边长 2048。
- 不超过 2048 的 ROI 可用一张上下文覆盖；超出上限时标记上下文不完整，另提供已有整景预览作导航，不能把它作为细节证据。
- 细节窗口原始 PAN 网格 512×512。首版每 ROI 最多 2 个窗口：一个主体代表位置、一个修复边界代表位置。
- 点位选择可用冻结 Mask 几何、已有候选评分和有界局部梯度；不能为了排序先读整景，也不能只选最“好看”的位置。
- 稳定记录选点规则、坐标与选择原因；重复任务得到一致窗口。
- 细长、大面积或多块区域无法由两个窗口覆盖时，`coverage=sampled`，明确未检查部分；更多窗口通过显式预算扩展。
- 边缘处优先平移窗口以保持尺寸；仍不足时补边，valid mask 中标为无效。补边像素不计入统计，不作为模型证据。
- UI 固定正方形容器与真实图像比例分开，使用等比缩放 / 补边，不挤压地物。

### 6.4 三角色配对

- 坐标基准为冻结的 PAN 网格；before 与 output 应匹配尺寸 / 网格，仍须核验。
- NOSR 使用实际空间映射。优先验证可用 GeoTIFF 仿射 / CRS，映射像素中心后做有界连续值重采样。
- 只存在项目既有“同景比例网格”约定时，记录 `alignment_source=production_ratio`、警告与实际尺寸比例；不能写成严格配准已证实。
- 非整数比例、旋转或 CRS 差异须使用经过验证的变换；首版读取器不支持时返回 alignment_unverified，不静默按宽高比强配。
- NOSR 放大仅为对照显示，不称新增真实分辨率。manifest 分别记录源像素间隔、输出采样间隔、重采样方法。
- 细节图 output / before 优先保留原始像素；上下文缩小时使用明确抗混叠方法，不能用稀疏隔点抽样制造混叠。

### 6.5 显示灰度与指标

- 原始 DN 保留在短生命周期数组中。亮度、有效性、纹理等指标基于原 DN 或明确的共同分析尺度，不使用 UI 的 JPEG。
- before / output 共享一套映射；优先从两者共同有效、受控采样计算线性百分位黑白场，并冻结参数。
- NOSR 使用同一映射须先核对 DN 定标 / 位深 / Photometric。不能证实可比时单独标注归一化，仅作结构观察，不输出辐射变化结论。
- 默认不对每张图独立直方图均衡；不随用户当前 Viewer 拉伸改变历史证据。
- 常值、NaN、Inf、NoData、WhiteIsZero、负值和浮点影像明确处理，所有缺失值在 JSON 写 null / 状态，禁止 NaN JSON。
- 用户在 Viewer 调整拉伸仅改变查看，不回写既有报告。要换证据显示规则时生成新 recipe / 缓存键。

## 7. TIFF 读取与缓存效率

### 7.1 新增专用读取器，避免影响现有检测

建议新文件 `backend/services/evidence_raster.py`，接口：

```python
read_evidence_window(source_ref, window, *, max_source_edge,
                     max_read_bytes, cancel_check) -> WindowPixels
```

`WindowPixels` 至少含 pixels、valid、source_dims、actual_window、requested_window、dtype、read_bytes、sample_scale、reader_version、warnings。

首版只支持当前主生产格式：无压缩、单波段条带、明确位深 / SampleFormat。压缩 / tiled 不隐式全图解码，返回 unsupported；可另开经过预算检查的块读取适配器。

### 7.2 无压缩局部列读取

复用 / 提取 `preview_jpg` 已有 TIFF 头、IFD、大小端和 dtype 解析。避免复制一套未经测试的 TIFF parser。

对于连续单波段条带：

```text
strip_index = source_y // rows_per_strip
row_in_strip = source_y % rows_per_strip
row_bytes = source_width * bytes_per_sample
offset = strip_offset[strip_index] + row_in_strip * row_bytes + x0 * bytes_per_sample
length = (x1 - x0) * bytes_per_sample
```

- 计算用足够宽的整数；检查 offset / length 在 strip byte count 与实际文件大小内。
- 每行只读取窗口连续列，读满校验，遇到截断立即失败。
- 不支持位打包、混合位深、多波段或非预期 planar 布局时显式拒绝。
- 打开文件句柄前后核对 source revision；操作结束后再次核验路径和句柄身份，防止发布替换期间混用不同版本。
- 每源版本缓存有限数量解析后的 IFD / offsets，按版本失效；限制缓存项数，避免每个 ROI 重读大型 IFD。
- 窗口和角色顺序处理，编码后释放原 DN；不同时保留 30 行全部数组。
- 磁盘读取合并只能在同源、窗口相邻、额外字节不超过预算时启用；首版可不实现合并。

### 7.3 初始预算与文件缓存

建议初始约束：最大源边长 2048；单次角色窗口实际读取预算 32 MiB；单窗口工作内存预算 64 MiB；每 ROI 最多 1 上下文 + 2 细节；图像模型请求总编码字节另设 8 MiB 上限。

预算需包含 DN、valid、重采样临时数组、编码副本，不只计算最终 PNG 大小。浮点 / 64 位数据单独检查。上限不足时减小窗口或返回明确预算状态，不能无标记地下采样。

证据缓存只按 evidence_key 命中；取已有 manifest 的小文件不读生产 TIFF。报告 GET 不触发任何大图读取。

已发布报告的证据默认保留，不使用随预览清理一起删除的缓存目录。首版不做自动删除；提供占用统计 / 配额，配额不足时停止新证据任务并显示 storage_budget_exceeded。后续清理必须跳过被报告引用的证据，先显式定义保留策略。

## 8. 调度、恢复与资源隔离

### 8.1 重要约束：异步本身不能保证效率

独立线程仍会与 SR / 检测竞争盘阵；本地多模态端点还可能与 SR 共用 GPU。首版应采用“计算结束后 + 全局空闲才领取”，不以后台线程数代替资源准入。

### 8.2 共享栅格队列的最低优先级

当前 `HeavyWork` 在交互任务连续执行四次后允许普通后台任务执行。证据不能放到这个普通后台优先级里，否则会插入检测和提交前复核之间。

建议为 `HeavyWork` 增加显式 `priority='evidence'` 的第三队列，保留旧调用兼容：

1. 原交互 / 普通后台的既有调度规则保持一致。
2. evidence 仅在前两队列为空、没有普通栅格任务执行且 `can_run_evidence()` 为真时执行。
3. 每次最多入共享队列一个有界窗口操作；其余任务留持久化队列，不能让 30 个证据请求占满共享队列容量。
4. 入队时和真正开始执行前都检查准入。准入失效返回 deferred，恢复 waiting_idle，不计失败次数。
5. 新修复工作出现时，取消尚未运行的证据 ticket；正在运行的窗口在行 / 小块边界协作停止。
6. 函数仍持有原始文件读取或编码线程时，不得因 HTTP 超时 / asyncio cancel 释放共享容量。遵循现有 HeavyWork 的实际工作占槽语义。
7. 等待空闲时避免 drain 忙循环；由任务完成 / 状态变化唤醒，或有限间隔重新检查。

### 8.3 全局忙碌判据

`can_run_evidence()` 只读有界状态，不扫描盘阵：

- 存在 OPENING / DETECTING / 提交准备 / SUBMITTING / WAITING_SR / VERIFYING 等活动计算时禁止领取。
- 执行器有 queued / running / 身份不明 / quarantine 工作时禁止领取。
- 掩码编辑和单纯等待人工审核不算持续运行的计算；用户开始实际栅格化 / 提交时立即体现到准入。
- 不只检查当前报告 Goal；其他 Goal、直接队列提交、正在运行的单景 SR 都必须检查。
- `UNKNOWN` 不能被当作空闲；显示等待原因，用户处理既有执行状态后再继续。
- 新增作业登记即提高忙碌代号（generation），证据工作领取后再次验证代号；不得等待下次两秒轮询才发现前台工作。

实现时用统一只读快照 / 忙碌代号连接已有调度器；不要在多处各写不一致的 busy 布尔值。

### 8.4 模型任务

- 模型并发默认 1，与栅格读取分阶段；调用模型时不占栅格线程槽。
- 默认等待全局 SR 空闲，以覆盖本机模型共用 GPU 的场景。若部署明确为独立硬件，后续才能配置放宽。
- 用户主动聊天优先，后台分析不能挤满模型并发额度；新增模型准入器时覆盖普通聊天和证据任务的两条路径。
- 后台请求必须有总时限、取消令牌和大小预算。取消发出不等于端点停止 GPU 计算，日志区分取消请求与确认结束；首版不得声称远端可即时抢占。
- 首版自动分析不开放无界 Agent 工具循环。每 ROI 一组固定证据、最多两组补充细节，固定调用次数和 token / 时间上限。
- 初始每组为同位置的三角色图（缺 before 时两图），一次请求只分析一组；最多 1 组上下文 + 2 组细节。每行所有网络调用合计最多 6 次，包含瞬态重试和格式修正；总墙钟预算初始 10 分钟，后续按真实模型调整。耗尽预算保留已有分析并标 partial，不继续追加调用。
- 各组观察由程序按 evidence ID 合并、去重并限长，形成一行结果，不增加无预算的最终模型总结调用。前台工作导致的等待空闲不消耗模型请求墙钟预算，已发请求消耗预算。

### 8.5 持久任务与关闭

- 短事务认领任务：条件更新 state + lease_token + lease_until，返回当前任务；claim 与执行分开。
- 读取 / 编码 / 模型调用在事务外执行；提交结果时校验 token、row version、source_set_hash。
- 租约过期任务可重领；旧 worker 的迟到结果不能覆盖新结果。读取中的临时文件按发布规则恢复。
- 默认错误重试最多 2 次（首次之后）；等待空闲、取消让出资源不计入失败次数。指数退避，持久化 next_wake_at。
- 客户端关闭不取消已持久化任务；关闭服务时停止领取、通知协作退出、等待正在读取任务真正结束 / 有界中止，然后关闭执行器，最后关闭 Store。
- 后台循环在 `app.py` lifespan 管理；不要将未退出线程遗留到 Store 关闭之后。

## 9. 多模态分析契约

### 9.1 图像传输

- 新增只读 `evidence_analysis.py`，使用现有 Config 的模型端点和 transport。
- 从受控 evidence ID 解析已验证的文件，只在请求边界转 data URL。
- 不直接调用聊天附件上传 API，不扩大全局聊天“两张图”限制，不把证据图片复制进用户聊天消息。
- 现有 `hydrate_images` 只识别聊天 URL，且缺图可能替换为文字；证据任务必须有自己的严格解析：所需图缺失就标 partial / unavailable，不能悄悄作为纯文本视觉分析继续。
- 建议从 `_default_chat` 提取窄的公共模型请求接口，保留原聊天路径、超时和测试行为；服务层不要依赖完整 Agent Loop。
- 发给模型的图片使用独立角色图，不使用并排后被压缩的 UI 缩略图。请求前核对端点能接收所需图片数量及编码。
- 不发送整景数组，不一次塞入 30 行图；模型上下文只包含当前 ROI 的证据和短指标。

### 9.2 输入包

包含 report / row / source_set 标识、角色与采样尺度、geometry 与覆盖信息、配准可信状态、有效比例、缺失证据、指标及有序图像引用。

数值指标优先复用同口径已有结果；新增 before/output 指标只能在相同网格和共同有效区域计算。初始可提供亮度差、方差 / 梯度变化、边界内外变化诊断，并写清公式和适用条件。

原检测 PAN/NOSR SSIM 不能冒充修复后的评分。跨分辨率直接比较的统计不能用于证明细节恢复。没有经过校准的伪影阈值时，提供诊断值而不是“通过 / 不通过”。

### 9.3 输出 schema（建议 v1）

```json
{
  "schema_version": "repair-evidence-analysis.v1",
  "row_id": "opaque-row-id",
  "assessment": "suspected_new_artifact",
  "priority": "high",
  "observations": [
    {
      "kind": "suspected_ringing",
      "description": "高反差边缘附近出现疑似亮暗带，建议放大核查。",
      "evidence_ids": ["detail_01_before", "detail_01_output"],
      "location": {
        "evidence_id": "detail_01_output",
        "box_normalized": [0.55, 0.2, 0.85, 0.8]
      }
    }
  ],
  "recommendation": "inspect_boundary",
  "reason": "该位置涉及修复边界，当前证据不足以判定质量通过。",
  "limitations": ["仅检查本 ROI 的两个抽样细节窗口。"]
}
```

- assessment 枚举：`improvement_observed / residual_issue / suspected_new_artifact / no_obvious_issue / insufficient_evidence`。
- priority：`high / medium / low / unknown`，用于复核排序，不是质量等级。
- recommendation 只允许只读检查动作：`inspect_detail / inspect_boundary / verify_alignment / review_before_after / insufficient_evidence / no_specific_action`。
- observations 最多 3 条，单条描述设长度上限；reason、limitations 同样有限。
- `location` 可空。非空时归一化框须在 [0,1]、顺序合法、关联已有 evidence ID。服务端根据 manifest 换算源坐标，不采用模型自报的 TIFF 坐标。
- 非法位置不绘制；保留经过校验的文本和错误标记，不能默认在图像中心补一个框。
- 框是可疑位置提示，不变为 SR Mask。粗位置可用时才显示，不能要求编码器完成像素级分割。
- 不要求模型给百分比置信度；可显示证据充分 / 受限 / 不足，由确定性缺失、尺度与配准规则约束。

### 9.4 服务端校验与结论约束

- 所有 row_id、evidence_id、枚举、长度、数值范围由服务端验证；模型不能指定新路径、Job 或跳转 URL。
- JSON 无效时最多一次格式修正请求，使用同一证据和明确预算；仍失败显示 analysis_invalid，保留脱敏错误摘要。
- 缺 before 时，服务端禁止 improvement_observed；配准未证实时禁止高确定性结构差异结论，至少追加 limitation。
- 模型仅能引用实际输入的指标；不接受模型编造的新数值写入指标表。
- tools 为空且拒绝任何 tool_calls；提示词要求“只读分析，不生成执行指令”。
- 模型输入中的文件名 / 元信息仅作数据，不当作指令；输出不渲染 HTML，不解释为脚本。
- 存储 model 名、端点配置身份摘要、prompt / schema 版本、证据 hash、耗时、重试次数。配置摘要不含 secret。
- 报告汇总和排序由程序计算，不额外让模型改写所有行，避免产生无证据的跨行结论。

## 10. API 与 Agent 工具

以下路由是新增建议，前缀与项目实际 router 风格对齐后固定在 api-contract 中。

| 方法与路由 | 语义 |
|---|---|
| `POST /api/goals/{goal_id}/evidence-reports` | 幂等创建 / 获取本次来源版本报告；只排队，返回 202 或已有资源引用 |
| `GET /api/goals/{goal_id}/evidence-reports` | 有界列出报告版本及当前性，默认最新 |
| `GET /api/evidence-reports/{report_id}` | 小体积头部、覆盖统计、阶段状态与 recipe |
| `GET /api/evidence-reports/{report_id}/rows?cursor=...&limit=30&filter=...` | 服务端分页行；limit 最大 30，稳定排序，返回下一游标 |
| `GET /api/evidence-reports/{report_id}/rows/{row_id}` | 单行、证据 manifest、分析与历史引用 |
| `GET /api/evidence-reports/{report_id}/assets/{asset_id}` | 仅返回已发布小图；ETag 为内容 hash，正确 MIME，禁止任意路径 |
| `POST /api/evidence-reports/{report_id}/rows/{row_id}/retry` | 对失败阶段幂等重试；body 带 stage、expected_version、request_key |
| `GET /api/evidence-reports/{report_id}/export` | 结构化 JSON；不包含 base64、secret 或无必要的绝对路径 |

要求：

- 复用现有会话 / Goal 作用域检查，不臆造仓库已有登录鉴权；从 Agent 工具访问时必须核对会话绑定。
- asset_id 仅从报告 manifest 解析，最终 resolved path 必须在受控 evidence root 内；拒绝目录穿越和符号链接越界。
- GET 无重计算副作用，不能因取一页列表触发验证整幅 Mask、重新散列大图或生成全景预览。
- POST body 大小和枚举有界；来源由服务端已有绑定解析，不能让客户端传文件路径。
- 快照查询用小 SQL / 缓存；重检查提交到后台。若源当前性无法确认，显示 unchecked，不写 current=true。
- 历史证据可读不代表当前生产文件仍匹配；返回 `source_currentness=current/stale/unchecked`，并分别标明当前人工审核状态。
- 首版报告页采用可取消的 2–5 秒小快照轮询；ready / failed 后停止。不要依赖 Goal SSE 在终态后继续发送报告事件。
- 快速重复请求、换页、切报告、销毁组件均有 generation / AbortController，旧响应不得覆盖新页面。

Agent 建议新增只读工具：`get_repair_evidence_report`、`list_repair_evidence_rows`、`get_repair_evidence_row`。默认查询本会话 Goal，返回摘要和证据引用，不返回大数组 / base64。

若后续让通用聊天 Agent 实际看这些图，必须完成独立图像水合契约和预算，不把工具 JSON 内的 URL 当作“模型已经看到了图片”。首版报告的专用多模态分析器即完成视觉分析，不依赖这项扩展。

## 11. Viewer 定位与人工审核联动

### 11.1 定位载荷

每行由服务端返回结构化引用，不让模型构造 URL：

```text
goal_id、item_id、job_id、review_id、region_id、source_set_hash、
output_revision、nosr_revision、before_revision、source_bbox、comparison_role
```

`comparison_role` 为 output_vs_nosr / output_vs_before。建议默认定位 output_vs_nosr，展开修复变化时可切 output_vs_before。

### 11.2 跨批次加载顺序

1. 点击后先路由进入 Viewer；如果已有手绘草稿，沿用现有保护，不覆盖草稿。
2. 加载请求指定的 goal_id，等待快照确认。
3. 再加载 item / review，校验 review_id、Job 和来源版本。
4. 加载两角色图像，等待对比画面、布局稳定后定位 region。
5. 加载过程中检查用户意图代号；用户切换影像时停止旧定位。

现有 `requestQualityReview(itemId, regionId)` 缺 goal_id，不能直接从任意历史报告调用。扩展为兼容旧调用的载荷或增加独立请求函数；在 `BatchProgressPanel.vue` 消费端实现上述顺序。

### 11.3 历史与版本失效

- 当前可审核绑定匹配时，进入既有人审流程；报告不直接提供新的批准 API。
- 已审核或历史报告仍可查看归档裁片；如果当前 PAN 已被替换，不将最新 PAN 当作旧报告产物打开。
- 原始图仍存在但版本过期时，禁止编辑当前审核，仅允许明确的历史只读视图。
- 原始图不存在则保持证据查看，并显示无法定位原始影像的原因。
- 人工审核状态从现有审核表读取，不写进不可变模型结论；用户驳回不会悄悄改写模型原话。

## 12. 逐步实施工作包

按依赖顺序完成。每包结束必须记录：代码文件、实际运行的检查、结果、未覆盖范围。不得以 mock 通过宣称真实模型准确或盘阵性能通过。

### P0：确认基线与实现契约

操作：

1. `git status --short`，记录已有用户修改；只修改本功能所需内容。
2. 阅读 §1 文档和 §3 代码，核对最新函数签名。
3. 建立计算就绪状态矩阵，覆盖人工掩码待审、质量待审、无候选、混合失败、UNKNOWN、取消与恢复。
4. 确认生产 TIFF 格式、模型端点图片能力、备份绑定、同景空间映射；无法验证项标待核。
5. 固定 API JSON、schema、错误码和默认预算；前后端共享字段语义。

检查：不启动真实 SR。生成接口示例和状态表；现有修复流程的行为不因文档或 schema 定义改变。

### P1：纯几何规划与契约测试

建议新增 `backend/services/evidence_geometry.py`、`backend/tests/test_evidence_geometry.py`。

操作：

1. 实现最终区域提取、稳定 row_id、inclusive → half-open 转换。
2. 实现上下文正方形、贴边平移 / padding、主体 / 边界细节窗口选择。
3. 定义 PAN → NOSR 映射和配准证据状态；不支持的变换返回显式结果。
4. 实现证据 manifest schema 和所有预算检查，不读影像。

检查：左上 / 右下角、1 像素、细长区域、超大区域、多边形、相邻候选、审批子集、相同名称不同景、非整数尺寸比例、部分越界；重复规划稳定；不改原 Mask 几何。

### P2：有界原始窗口读取和显示图

建议新增 `backend/services/evidence_raster.py`、`backend/tests/test_evidence_raster.py`。

操作：

1. 提取可复用 TIFF metadata 解析，保持旧 `read_source_window` 行为。
2. 实现局部字节跨度读取、条带 / 文件边界检查、取消检查。
3. 实现 DN / valid、上下文抗混叠、NOSR 映射、共享线性拉伸和无损 PNG 编码。
4. 保存原始指标与显示参数，独立生成 UI pair_thumb。
5. 记录实际 read_bytes、读取 / 重采样 / 编码耗时和峰值数组尺寸。

检查：Classic TIFF / BigTIFF、大小端、8/16/32 位、浮点、RowsPerStrip=1 / 多行 / 单大条带、WhiteIsZero、NoData、NaN / Inf、常值、截断、文件替换、unsupported 格式、无效空间映射。

用独立小图解码器作像素预期值对照；通过包装文件读取统计字节，证明不是读取整行。不能只断言输出 shape。

### P3：数据库、缓存和 API 骨架

建议新增 `backend/services/evidence_store.py`、`backend/services/evidence_report.py`、`backend/api/evidence_reports.py`；schema 接入 `store.py`。

操作：

1. 实现可重复执行 migration，不破坏已有 DB。
2. 实现报告幂等创建、行与任务规划、短事务认领、token / version 校验。
3. 实现临时目录发布、manifest 校验、版本化缓存和占用预算。
4. 实现小快照、分页、资源、retry / export 路由。
5. 先用合成裁片返回 ready，不接真实模型；模型状态明确 not_requested。

检查：重复创建复用、不同源版本产生新版本、跨景隔离、崩溃恢复、租约争用、迟到结果、路径越界、asset 缺失、30 / 31 / 65 行分页、空报告、GET 无源 TIFF read、JSON 无 base64 / NaN / secret。

### P4：最低优先级调度与批次发现

建议新增 `backend/services/evidence_worker.py`，在 HeavyWork 与 lifespan 中最小接线。

操作：

1. 实现 §4 就绪判定和有限增量发现；优先从持久审核绑定补齐任务，避免在关键路径新增磁盘动作。
2. 实现 §8 第三优先级和全局 busy 快照 / generation；保持已有两级队列行为。
3. 按单窗口领取证据操作，遇前台任务让出；磁盘 / 模型阶段分开。
4. 实现服务重启恢复、停止领取、实际线程结束后关闭 Store。
5. 集成功能开关和等待原因，默认关闭时不查询历史、不读源图、不调用模型。

检查：前台检测 / 提交 / SR 忙时证据读取字节为 0；证据窗口运行中收到新任务，最多当前有界单元后停止；异步超时不释放运行线程槽；其他 Goal 和直接队列任务同样阻止领取；UNKNOWN / quarantine 不误判空闲；报告故障不改变 SR 结果。

### P5：有界多模态分析

建议新增 `backend/services/evidence_analysis.py`、`backend/tests/test_evidence_analysis.py`。

操作：

1. 提取公共模型请求接缝，使用实际 Config / transport，不复制 API key 配置逻辑。
2. 实现严格 evidence ID → 文件 → data URL 水合、请求大小检查。
3. 编写版本化提示词和 JSON schema；tools 禁用，默认逐 ROI 调用。
4. 实现服务端语义约束、一次格式修正、失败分类、缓存和分析历史。
5. 对共享 GPU / 前台聊天增加模型准入；新 SR 任务出现后不继续派发后台分析。

检查：模拟端点必须断言图片真实进入请求且角色顺序正确；缺图、不支持 vision、非法 JSON、伪造 evidence_id、缺 before 却声称改善、无效定位框、超时、工具调用、重复分析缓存全部覆盖。所有结论仍不调用 qualityDecide 或 start_repair。

没有可达真实模型时完成上述工程接线和 unavailable 展示；真实视觉评测保持待核，不能用固定假描述替代生产分析。

### P6：长表报告与入口

建议新增：

- `frontend/src/lib/repairEvidence.ts`：API、类型、错误映射。
- `frontend/src/stores/repairEvidence.ts`：分页、筛选、轮询、重试、取消与过期响应保护。
- `frontend/src/pages/RepairEvidenceReportPage.vue`。
- `frontend/src/components/RepairEvidenceRow.vue`：双图、观察、建议、展开、定位。
- 对应组件 / Store 测试。

操作：

1. 注册独立路由、报告入口；面板与 Agent 卡共用报告 ID。
2. 实现最多 30 行服务端分页、场景分组、优先级 / 状态筛选；排序变化时游标绑定版本。
3. 使用 `<img loading="lazy">` 和固定尺寸；只取当前页，离开页面停止轮询 / 请求。
4. 展开显示三角色证据和限制；模型文字按普通文本处理，禁止原始 HTML。
5. 报告未完成、partial、无模型、无证据、stale、retry 状态全部有可用界面。

检查：0 / 1 / 30 / 31 / 65 行，长景名、重复 PAN.tif、不同图像尺度、图片加载失败、切页时迟到响应、刷新恢复报告、窄屏无横向溢出、图片加载不导致明显布局跳动；不下载未展开行的全部细节图。

### P7：Viewer 定位和人工验收闭环

操作：

1. 扩展携带 goal_id / review_id / source_set 的定位载荷，兼容当前调用。
2. 实现跨路由、跨批次顺序加载，以及 Viewer 用户意图代号检查。
3. 默认 output / NOSR 同步定位；提供 output / before 的只读对照。
4. 复用既有人工质量决定，不建第二份审核状态。
5. 明确历史图失效、手绘草稿、原始图丢失的行为。

检查：从 A 报告跳入当前展示 B 批次的 Viewer，最终必须定位 A 的准确区域；快速点击两个区域后只响应最后意图；用户中途换图不被拉回；版本替换不能误审新图；历史证据可查看但不能改变当前审核。

### P8：性能、浏览器、真实环境与交付

操作：

1. 扩展 `backend/tests/quality_browser_fixture.py` 或新增专用夹具，使用实际应用、合成三角色 TIFF、合成审核，显式禁止真实 SR 提交。
2. 新增 `.e2e/test-repair-evidence.js`，沿用 `.e2e/launchBrowser.js`，验证表格、分页、展开、跨批次定位和刷新恢复。
3. 新增 `backend/tests/benchmark_repair_evidence.py`，输出机器可读 JSON；记录实际读取字节和阶段耗时。
4. 按 §13 对比功能关闭 / 开启等待 / 真正空闲生成 / 生成中插入新任务。
5. 内网执行真实盘阵与真实视觉验收；未完成时明确列阻塞因素和可复跑命令。
6. 更新状态与 API 文档、配置说明；交付开关、回退方法和未核清单。

完成标准：工程测试和性能预算通过；真实环境未验证时仅标“工程完成、生产视觉 / 性能待核”。不得写“生产质量验收通过”。

## 13. 测试矩阵与性能验收

### 13.1 必须覆盖的故障

| 情况 | 预期 |
|---|---|
| 用户快速修改 / 驳回候选掩码 | 仅最终几何进入证据，无动画帧取图 |
| 相同 basename、不同景 | 证据与跳转完全隔离 |
| 修复前备份与本次 Job 不匹配 | before 不可用，禁止改善结论 |
| PAN 被发布替换或 NOSR 漂移 | stale，不混用不同版本 |
| 文件读取中断 / 编码失败 / 磁盘满 | 无 ready 半文件，修复不受影响 |
| 后台任务租约过期 / 重启 / 重复领取 | 幂等，旧结果不能覆盖新结果 |
| 无候选 / 全驳回 / 全失败 | 正确空态与计数，不伪造修复效果 |
| 等待质量验收 | 可以出报告，不等待最终 Goal 完成 |
| 等待掩码审核 / UNKNOWN 作业 | 不误判计算结束或全局空闲 |
| 模型未配 / 不接图 / 超时 | 证据仍可看，分析状态准确 |
| JSON 非法 / 引用伪造 / 模型工具调用 | 拒绝或受限修正，不产生副作用 |
| 人工决定与模型意见冲突 | 两者独立保留，人工流程仍是唯一验收写入来源 |
| Goal SSE 已结束 | 报告通过自身状态接口继续更新 |

### 13.2 性能测量方式

以下为建议工程验收目标，须用基线实测验证，不能当成已有性能承诺。

1. 同机器、同夹具、同执行器，关闭真实 GPU 提交；使用相同可重复合成检测 / 作业负载。
2. 分开记录冷缓存和热缓存；每种情形至少 5 次，比较中位数与 p95，不混合两类结果。
3. 先记录自然波动；若基线 p95 本身不稳定，应增加样本 / 排查后台干扰，不用一次结果判定。
4. 场景 A：证据功能关闭；B：功能开启但检测 / 提交忙碌；C：空闲实际出证据；D：证据生成中插入前台计算。
5. 记录检测耗时、Mask 落盘、提交准备和提交等待、API 小快照 p95、事件循环延迟、RSS、读取字节、队列等待、每窗口执行时间。

初始通过条件：

- 场景 B 的证据 TIFF 读取为 0、后台模型调用为 0；不能新增同步整图 hash 或解码。
- B 相对 A 的检测 / Mask / 提交关键阶段中位数目标增幅 ≤5%；超出时分析样本与开销来源，不能隐藏退化。最终阈值由内网基线确认。
- 2048² 或更小窗口的读取量满足配置预算；典型局部窗口读取量随窗口面积而非整景宽度增长，IFD 元数据开销单独记录。
- 证据任务内存受单窗口预算约束，不能随 30 / 300 行线性保留 DN 数组。
- D 前台只等待已开始的有界操作结束 / 协作取消，不等待整页或整份报告；记录该等待实际时长。目标普通 512 细节单元 p95 ≤500ms，盘阵未达到时缩小单元或保持功能关闭，不能宣称硬实时保证。
- 小快照 GET 不读取源 TIFF 内容，不重复完整校验 Mask；无活跃报告时轮询停止。
- 不能通过同时启动多个原始读取线程换取更低报告耗时。

### 13.3 真实模型必要性评估

建立同一批 ROI 的三组对照：确定性指标 / 模板、只给指标的文本模型、指标 + 图像的多模态模型。

- 样本含：正常纹理、真实残留模糊、边缘振铃、接缝、低纹理地物、亮度变化、配准疑点、无效像素、被缩小后难以辨认的细节。
- 人工给出可复核疑点与证据范围，记录模型误报、漏报、无法判断是否合理、建议是否可执行。
- 观察新增伪影发现、证据引用准确性、定位偏差和人工复核时间，不只评价语言流畅度。
- 数据未标注或样本有限时仅报告案例，不能声称整体生产准确率。
- 若多模态未优于文本对照，保留图像报告，分析功能保持实验标记，不用更长文字掩盖无增益。

## 14. 配置、命令、部署与回退

### 14.1 建议配置

| 配置 | 建议初值 | 语义 |
|---|---|---|
| `SR_EVIDENCE_REPORT_ENABLED` | `0`（开发 / 验收启用为 1） | 协调器和报告自动发现；不开启时既有修复行为一致 |
| `SR_EVIDENCE_ANALYSIS_ENABLED` | `0` | 独立控制模型分析；证据可单独验收 |
| `SR_EVIDENCE_CONTEXT_EDGE` | `512` | 上下文输出尺寸 |
| `SR_EVIDENCE_SOURCE_MAX_EDGE` | `2048` | 单窗口最大源边长 |
| `SR_EVIDENCE_DETAIL_EDGE` | `512` | 原始 PAN 细节窗口边长 |
| `SR_EVIDENCE_MAX_DETAIL_WINDOWS` | `2` | 单 ROI 细节数量 |
| `SR_EVIDENCE_MAX_READ_BYTES` | `33554432` | 单角色单窗口读预算 |
| `SR_EVIDENCE_MODEL_MAX_BYTES` | `8388608` | 单模型请求编码图片预算 |
| `SR_EVIDENCE_MODEL_CONCURRENCY` | `1` | 后台模型并发，必须校验上限 |
| `SR_EVIDENCE_STORAGE_LIMIT_BYTES` | 部署显式配置 | 受控证据目录空间配额；超额停止新任务 |

其余轮询、超时、重试和模型调用次数作为有界配置或内部常量；实现者固定默认值并写入配置文档。页面 30 行上限由服务端强制，不作为任意可调高的开关。

模型继续使用现有 `SR_LLM_*` 配置，不复制一套 secret。功能开启不修改 `SR_BATCH_PRODUCTION`、执行器准入或任何用户确认要求。

### 14.2 验证命令

以下新测试文件须在对应工作包创建，不能对不存在文件运行后声称通过。先核对解释器：本工作区有 `.venv/Scripts/python.exe`，系统 `python` 可能指向 WindowsApps 启动别名；下列 `python` 指项目已激活且可运行的解释器，未激活时用 `.\.venv\Scripts\python.exe` 替换。仓库根目录执行后端命令：

```powershell
python -m pytest backend/tests/test_evidence_geometry.py backend/tests/test_evidence_raster.py
python -m pytest backend/tests/test_evidence_report.py backend/tests/test_evidence_worker.py backend/tests/test_evidence_analysis.py
python -m pytest backend/tests/test_batch_heavy_queue.py backend/tests/test_multi_user_work.py backend/tests/test_batch_quality.py backend/tests/test_repair_application.py backend/tests/test_chat_images.py
```

开工核对既有测试文件名；API / Store / 路由变化后补跑对应既有回归。前端在 `frontend/` 执行：

```powershell
npm run check:terms
npm run typecheck
npm test -- --maxWorkers=2 --minWorkers=1
npm run build
```

构建后在根目录运行新增 `.e2e/test-repair-evidence.js`。夹具沿用 `python -m backend.tests.quality_browser_fixture --root <临时目录> --port 8797` 的合成生产审核模式，证据扩展参数由 P8 定义；不能用 `--mask-review` 的待提交状态冒充修复产物分析。

基准命令建议定义为：

```powershell
python -m backend.tests.benchmark_repair_evidence --root <临时目录> --output <基准JSON路径>
```

所有开发和浏览器夹具显式禁止真实提交器调用。真实内网验收仅使用既有授权与准入，不能为了跑报告测试擅自提交真实 GPU 作业。

### 14.3 部署与回退

- 新表采用向后兼容增量迁移；部署前沿用项目 DB 备份流程。
- 后端先部署、重启、检查 migration 和 worker 关闭行为，再发布前端 dist。
- 先仅开启证据报告验证取图和调度，再启用模型分析；内网性能 / 视觉未验收时保持实验标识。
- 回退优先关闭两个开关；已发布报告和图片保留，既有修复和审核继续可用。
- 不删除已有表、审核记录、生产影像或备份来回退。
- 更新 `docs/planning/api-contract.md`、`docs/status/current-question.md`、`docs/README.md`；复杂实现经验另写对应 experience 文档，勿把待核结果写成生产承诺。

## 15. 给实现 Agent 的交付清单

- [ ] 报告与已有确定性批次报告分开，两个修复入口共用同一实现。
- [ ] 区域取自最终提交 Mask 的冻结几何，不依赖 Viewer 已打开或截图动画。
- [ ] NOSR / before / output 来源与 Job / 审核绑定可核验。
- [ ] 原始局部读取经过字节量证明，未回退整图解码。
- [ ] 同位置、尺度、有效范围、拉伸、配准来源都写入 manifest。
- [ ] 证据调度低于既有交互、检测、提交与 SR；异常不阻塞修复。
- [ ] 数据持久化、幂等、租约恢复、原子发布、过期结果保护通过。
- [ ] 模型实际收到图片，缺图时结论受限；无工具权限，无审核副作用。
- [ ] 每页最多 30 行、完整覆盖计数、分页和未分析状态明确。
- [ ] 跨批次 Viewer 定位准确，版本过期与手绘草稿受保护。
- [ ] 无模型仍能查看证据；模型失败能单行重试且保留分析历史。
- [ ] 合成端到端、浏览器、性能对比及已有受影响回归完成。
- [ ] 内网盘阵、真实模型评测、未支持格式分别标实测 / 待核。
- [ ] 最终交接列明实际执行命令、检查结果、开关状态、剩余限制。

后续增强：争议区域的有界主动补证据、候选外抽查、同类区域归组和更丰富的人工反馈分析。上述增强在首版闭环和性能验收完成后评估，不作为首版必须引入的无界 Agent 调查流程。
