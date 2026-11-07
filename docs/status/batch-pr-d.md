# 批量修复 PR D 实施记录：Detector / MaskBuilder（2026-11-02）

> 状态：**第三轮复审「通过」—— PR D 完成；PR E 已随后实施，见 [batch-pr-e.md](batch-pr-e.md)。**
> 第三轮复审确认上一轮 6 项已通过，剩余 2 个 P1 + 1 个 P2（Predictor、复核期间输入漂移、NaN NoData JSON）已逐项补修并新增可复现测试，复审结论为通过。
> 依据：[1023.md](../planning/1023.md) 第二部分 D 与 K；前序成果见 [batch-pr-c.md](batch-pr-c.md)。
> 基线：`main` @ `1f6d49d`，本机 Windows + `.venv` Python 3.12.14。未执行真实 SR/GPU，未扫描盘阵，未覆盖既有未提交改动。PR D 补修与 PR E 在同一工作树完成后统一提交。
> 演示口径：`SR_BATCH_DETECTION=center` 的 MockDetector（`provenance=mock`）或 `real` 的条带窗口真实像素检测（`provenance=real`）+ 真实条带 Mask 文件 IO + Fake SR + MockVerifier。生产 `mode` 仍被显式拒绝。

## 1. 复审结论与本轮范围

第二轮复审结论仍为「不通过」：不能合并完整 PR D，也不能进入 PR E。上一版文档曾把 P1 2–4、P2 5–7 记为已关闭，复审证明该结论不成立（Pillow `crop()` 仍整图解码、异步复核只记录 Mask 身份、候选点重复叠加 `pane.rect`、叠加层不裁剪到格子、PNG 失败仍显示「真实检测」、NoData 列判定随窗口大小变化、清理会删活动临时目录）。第二轮补修后，第三轮复审确认整图解码、候选偏移、分屏越界、PNG 失败状态、跨窗口 NoData 与轮次锁均已有独立通过证据，但仍剩 3 项：合法 Deflate+Predictor=2 影像被静默误读、复核期间的输入漂移被记录为新基准、NaN NoData 进入 diagnostics 导致 Overlay 500。本轮按第三轮证据补修，以下「状态」仅表示**已补测试待复审**。

| 复审项 | 状态 | 本轮处理 |
| --- | --- | --- |
| P1 Predictor=2 被当成像素值，合法 Deflate 影像误读 | 第三轮复审已通过 | 解析标签 317；Predictor=2 在每行内按采样类型做模 2^bits 累计和还原（窗口始终整行对齐）；8×4 uint16 逐像素与 Pillow 独立解码一致，不再误判平列；Predictor=3 与浮点 Predictor=2 明确拒绝 |
| P1 复核期间 PAN/NOSR 漂移被记录为「已验证身份」 | 第三轮复审已通过 | 复核前先采集 PAN/NOSR/Mask/备份身份；复核结束再次确认冻结修订并采集，两次不一致即 `BLOCKED`；提交边界保留第三次复查。PAN/NOSR/Mask/备份四类「复核中变化」均有零提交零预约测试 |
| P2 NaN NoData 写入 diagnostics，Overlay API 无法序列化 | 第三轮复审已通过 | 非有限 NoData 规范为字符串 + `nodata_kind`；检测结果、Mask 结果与 `overlay.json` 均通过 `allow_nan=False`；真实 float32 TIFF → Mask → Overlay HTTP 200 端到端测试 |
| P1 1 `crop()` 整图解码，峰值随影像面积增长 | 第三轮复审已通过 | 改为纯 struct 解析经典/BigTIFF 条带布局，按 `window_rows` 直接读无压缩条带或流式解压 Deflate；不再调用 Pillow `crop()`。子进程峰值 RSS：8192×4096 与 8192×8192 均约 36 MiB |
| P1 2 异步复核只记录 Mask 身份 | 前一轮补修，仍有复核中窗口（本轮再修） | 复核结果记录 PAN/NOSR/Mask 的路径、大小、mtime_ns、revision 与 PAN 备份集合；提交预约前全部重验，任一漂移 `BLOCKED` 且零预约零提交。第三轮发现复核期间的变化仍会进入新基准，见本轮 P1 |
| P1 3 候选点重复叠加 `pane.rect` | 第三轮复审已通过 | 每格先按 `pane.rect` 建裁剪窗口；影像盒与候选点只用格内坐标；浏览器断言逐顶点屏幕坐标（含非零平移与分屏右格） |
| P1 4 叠加层不裁剪到格子、漏进邻格 | 第三轮复审已通过 | `.batch-pane` 即 `pane.rect` 裁剪窗口（`overflow:hidden`），放大后影像仍被裁在本格内；断言 B 格裁剪窗口不伸进 A 格 |
| P1 5 PNG 失败仍显示「真实检测」与候选 | 第三轮复审已通过 | 文档与 PNG 加载状态分离；PNG `error` 时隐藏候选与 provenance 芯片，显示「叠加层图片加载失败 + 重试」；重试带缓存键重新请求 PNG |
| P2 6 NoData 列判定随窗口大小变化 | 第三轮复审已通过 | 跨窗口累计每列有效像素数，扫描结束后统一按 `>=2` 判定；条带 1/2/4 行窗口结果一致 |
| P2 7 清理删除另一构建者的活动临时目录 | 第三轮复审已通过 | 每轮 `<round>.lock` 跨线程/跨进程非阻塞互斥，锁覆盖复用检查、清理、构建与 rename；第二个构建者得到可重试 `MaskError`，不删活动 `.tmp-*` |

D3 的完整范围（Item 跳转、刷新恢复、进度 Panel）仍不在本轮；本轮只做了复审要求的 Overlay 读路径与查看器叠加显示。PR E/F 仍待推进。

## 2. 契约

### 2.1 DetectionResult（`detection.v1`）

| 字段 | 约定 |
| --- | --- |
| `item_id` / `image_id` | 非空；`image_id` 取冻结 PAN 的 `image_key`（缺失时退回路径） |
| `image_size` | `{width, height}`，PAN 像素尺寸 |
| `coordinate_space` | 固定 `source_pixel`；`x` 为列、`y` 为行，零基 |
| `candidates` | `bbox` 用闭区间整数框 `[x0,y0,x1,y1]`；`polygon` 用顶点列表；支持 `candidate_id/score/label` |
| `detector_name` / `detector_version` | Mock：`deterministic-center` / `1.0.0`；真实：`flatline-scan` / `1.0.0` |
| `provenance` | `mock` 或 `real`；demo 两者都接受，production 拒绝 `mock` |
| `diagnostics` | 裁剪、窗口与模式诊断；不把诊断当成功 |

- BBox 越界显式裁剪并写 `candidate_clipped`；裁剪后为空的候选报错，不当作 `NO_ISSUE`。
- Polygon 保留原始顶点做栅格化；有效性检查与 BBox 用真实裁剪（Sutherland–Hodgman），越界只记录诊断，不再逐顶点夹紧后改变面积。
- Polygon 拒绝 NaN/Inf、非整数顶点、少于 3 点、零面积；`artifact` / `local_blur` 候选类型当前显式拒绝。
- `SR_BATCH_DETECTION=none` 返回成功空结果（`NO_ISSUE`）；`error` 抛错并走有界重试；未知模式报错。
- 真实通道要求冻结的 NOSR 参考影像：校验两侧修订、尺寸、波段数、样本类型，拒绝 tiled 与尺寸相同的参考网格。

### 2.2 MaskResult（`mask.v1`）

| 字段 | 约定 |
| --- | --- |
| `path` / `sha256` / `revision` | 最终发布路径、内容摘要、`preview_jpg.source_revision`（含路径哈希，发布后重算） |
| `width` / `height` | 必须等于 PAN 尺寸；不接受 NOSR 尺寸 |
| `dtype` / `bits` / `spp` | `uint8` / 8 / 1 |
| `background_value` / `foreground_value` | 0 / 255 |
| `foreground_pixels` | 必须大于 0；空掩码报错，不用空文件冒充成功 |
| `coordinate_space` | 固定 `source_pixel` |
| `item_id` / `source_image_key` / `source_revision` | 与冻结 Item、PAN 身份一致 |
| `provenance` / `detector_name` / `detector_version` / `detection_contract_version` | 从 DetectionResult 透传，供提交前复核 |
| `builder_name` / `builder_version` | 当前 `strip-mask-builder` / `1.0.0` |
| `overlay` | 轻量 PNG 引用：`path/sha256/width/height/scale/kind` |
| `diagnostics` | Detector 与 Builder 的诊断合并去重 |

### 2.3 Overlay 读契约（`overlay.v1`）

`GET /api/goals/{goal_id}/items/{item_id}/overlay` 只返回**已发布**的叠加层元数据；`.../overlay.png` 返回同目录 PNG。路由在 `goal_runtime` 中，解析 Goal/Item 后必须同时满足：

- 路径位于 `batch_manifest.artifact_root()` 下（越界 403），扩展名固定 `.png`；
- `overlay.json` 的契约版本、Item id、文件名与 checkpoint、Manifest 记录的 sha256 一致（不一致 409）；
- 对应的 `mask.tif` 存在且 sha256 与 Manifest、checkpoint 一致；
- 源影像 `image_key`/`revision` 可解析，`image_size` 与掩码宽高一致。

响应带 `source {image_key, revision, width, height}`、`image_size`、`coordinate_space`、`scale`、`provenance`、`candidates`、`diagnostics` 与 `mask/overlay` 摘要；PNG 响应 `Cache-Control: no-cache` + `ETag`。

## 3. 实现

### 3.1 D1 Detector

- **真实通道（`real`）**：`_probe_image` 校验冻结修订与 TIFF 头（尺寸、bits、spp、sample_format、compression），拒绝非单波段与 tiled；`_check_reference_grid` 复核 NOSR 不大于 PAN、分辨率更低且样本类型一致。
- **有条带窗口读取（P1 1 补修）**：`_read_strip_layout` 用 `preview_jpg` 的纯 struct IFD 解析取得条带偏移/字节数/采样类型，支持经典 TIFF 与 BigTIFF、8/16/32 位、uint/int/float、单波段 chunky；压缩仅接受无压缩或 Deflate（8/32946），其它压缩与 tiled 明确报错。`_iter_windows` 对无压缩条带按 `window_rows` 直接 `seek/read`，对 Deflate 条带以 1 MiB 输入块流式解压、每次最多产出一个窗口并拒绝超出条带尺寸的输出。**不再调用 Pillow `crop()`**，峰值由窗口而非影像面积决定。默认 256 行，可用 `SR_BATCH_DETECTION_WINDOW_ROWS` 或 Item `config.window_rows` 调整，上限 4096 行且单窗不超过 32M 像素。
- **Predictor（第三轮 P1 补修）**：读取标签 317。Predictor=1（无）原样解释；Predictor=2（水平差分）在每个整行窗口中按采样 dtype 做累加和，利用模 2^bits 的回绕还原原始采样值——窗口始终按行对齐，差分不会跨窗口；Predictor=3 以及浮点数据的 Predictor=2 明确报错，不再把残差当像素。8×4 uint16 的 Deflate+Predictor=2 影像与 Pillow 独立解码逐像素一致。
- **NoData 与列判定（P2 6 补修）**：从 IFD 标签 42113 读哨兵值；有哨兵值时用掩码统计；行判定要求至少 2 个有效像素；**列有效数跨窗口累加**，扫描结束后才按 `>=2` 判定，因此结果不随 `window_rows` 变化。诊断里记录 `nodata`。
- **JSON 安全诊断（第三轮 P2 补修）**：`nan`/`±inf` 哨兵不写入原始浮点，而是转成字符串（`"nan"`/`"+inf"`/`"-inf"`）并附 `nodata_kind`；有限值仍写数值。检测结果、Mask 结果与 `overlay.json` 因此都能在 `allow_nan=False` 下序列化。
- **候选坐标**：平线分组直接映射回 `source_pixel` 闭区间框（行组覆盖整宽，列组覆盖整高），去重并受 `MAX_CANDIDATES=256` 限制。
- **Mock 通道（`center`）**：确定性中心框，保持单参数 `detect(target)` 调用兼容既有测试；provenance 固定 `mock`，仅用于演示。
- **provenance gate**：worker 在检测后与提交前都校验；demo 接受 `real` 或 `mock`，production 拒绝 `mock`，不依赖 Detector 自报。

### 3.2 D2 MaskBuilder

- **条带写入**：经典小端 stripped TIFF，单波段 uint8、0/255、Deflate（stdlib `zlib`），默认 `rows_per_strip=256`；`SR_BATCH_MASK_ROWS_PER_STRIP` / `config.mask_rows_per_strip` 显式按十进制整数解析并范围检查（1–65536），非法或超界直接报错。峰值内存约为 `width × rows_per_strip`。
- **原始几何**：BBox 用 `ImageDraw.rectangle`（闭区间，支持最右下角 1×1）；Polygon 用 `ImageDraw.polygon` 并传入**未夹紧**的原始顶点，由光栅器按画布裁剪 —— 10×10 上 `[-5,5],[5,-5],[5,5]` 与直接 PIL 栅格化同样得到 36 像素，不再因逐顶点夹紧缩成 21 像素。
- **轻量 Overlay**：条带内按块最大值降采样到 `overlay_max_edge`（默认 2048，范围 16–8192），块最大值保证 1 像素候选不会在预览中丢失；覆盖整幅源影像范围，`scale = ceil(max(w,h)/max_edge)`。
- **流式校验**：`verify_mask` 逐条带解压并检查 0/255、尺寸、dtype、波段与前景数；非经典 stripped TIFF（BigTIFF、tiled、未知压缩）回退 Pillow，但受 `MAX_PILLOW_VERIFY_PIXELS` 限制，批处理路径不接受无界整图回退。
- **解码上限**：条带字节数、行数、偏移是否越界、解压后长度、`array` 数量（1,000,000）、单条带像素数（64M）都有上限；截断/超限的条带明确报错，不靠猜测继续。
- **原子发布**：在目标目录旁临时目录生成 `mask.tif`、`overlay.png`、`mask.json`、`overlay.json`，校验后一次 `os.rename` 发布；失败清理临时目录，不覆盖既有目录。
- **轮次互斥（P2 7 补修）**：`build_mask_result` 在复用检查、临时目录清理、构建与 rename 全程持有 `<round>.lock`（Windows `msvcrt.locking` / POSIX `flock`，非阻塞；同进程另有集合防护）。第二个构建者（旧 Lease 的 IO 仍未结束、恢复流程又启动同轮）得到可重试的 `MaskError`，不会删除对方的 `.tmp-*`；锁文件故意保留，避免 unlink 竞争。
- **重启复用**：重启后若同一轮已是完整发布状态（契约、候选摘要、Mask/Overlay 摘要均匹配），直接复用并记 `mask_bundle_reused`；候选变化、文件被篡改、bundle 不完整都判定为非法轮次并重建；只有持有轮次锁时才清理 `.tmp-*` 残留，因此被清理的必为死进程留下的目录。
- **发布后重算修订**：`source_revision` 含路径哈希，最终结果在 rename 后重算。

### 3.3 调度器接线与提交边界

- `_start_detection` 的 snapshot 增加 `item_id`、`reference_image`、`mode`、`mask_rows_per_strip`、`mask_overlay_max_edge`、`window_rows`；Goal 的 `mode` 在导入时冻结。
- `_detection_job` 先跑 provenance gate，再调 `build_mask_result`；空候选仍走 `NO_ISSUE`。
- **提交前复核移出调度线程**：`_start_submit_preparation` 把完整掩码校验交给 IO 执行器，调度线程继续推进其它 Item 的 Monitor 查询；超时按 `detection_timeout_sec` 退避重试，不重复提交。
- **提交边界身份（P1 2 补修）**：`_prepare_submission` 在复核完成后记录 PAN、NOSR、Mask 三个文件的 `{path,size,mtime_ns,revision}` 与场景目录下 PAN 备份集合；`_submit_ready` 在预约容量前重新采集并逐项比较，任一漂移 `BLOCKED`，不预约、不提交。覆盖「复核结果返回后、Lease/提交前」的替换窗口。
- **复核期间漂移（第三轮 P1 补修）**：`_prepare_submission` 在完整 Mask 校验**之前**先采集一次全部输入身份；校验结束再调用 `_open_item` 复核冻结修订、并第二次采集身份，两次不一致即 `BLOCKED`。这样「初始检查通过 → `verify_mask` 解码期间 PAN/NOSR 被替换或备份出现 → 采集身份」不再把新文件当成已验证基准。提交边界仍保留第三次复查，覆盖复核返回后的等待窗口。
- `mask_ready` 事件保留原字段，新增 `contract_version/coordinate_space/dtype/candidate_count/builder_*/detector_*/detection_contract_version/overlay`。

### 3.4 查看器叠加层（P1 3–5）

- `frontend/src/lib/batchOverlay.ts`：解析 `batch_goal`/`batch_item`，校验 `overlay.v1` 与 Goal/Item 身份，用 `origToThumb → thumbToScreen` 把源像素范围投影到**每一格自己的** `pane.view` + `pane.rect`；身份只认 `image_key`（预览 JPG 与源 TIFF 尺寸不同，尺寸不参与匹配）。
- `frontend/src/components/BatchDetectionOverlay.vue`：只在 `image_key` 匹配的格子绘制；每格先按真实 `pane.rect` 建 `overflow:hidden` 裁剪窗口，影像盒用 `view.ox/oy` 定位，候选点只算影像内坐标（`scale` 投影，不再叠加 view 平移或 pane.rect），因此不会双加偏移、也不会漏进相邻格。provenance 芯片区分「真实检测」与「Mock 检测 · 非真实结果」。
- **PNG 独立状态（P1 5 补修）**：文档加载成功 ≠ PNG 可显示。`<img>` 的 `load/error` 单独维护；`error` 时整格隐藏、不显示候选与 provenance 芯片，错误条显示「叠加层图片加载失败」并提供重试；重试通过 `pngEpoch` 追加缓存键，强制重新请求 PNG。
- 上一轮浏览器回归把「分屏右格少 `pane.rect`」误修成「投影再叠加 `pane.rect`」，第二轮复审用逐顶点坐标证明其为双加。本轮改为裁剪窗口承担 `pane.rect`、投影只算格内坐标，并在浏览器中断言实际顶点屏幕坐标与裁剪窗口尺寸。

## 4. 文件

| 文件 | 变更 |
| --- | --- |
| `backend/services/batch_contracts.py` | 冻结 `detection.v1` / `mask.v1` / `overlay.v1`；候选归一化、裁剪诊断、结果校验、provenance gate |
| `backend/services/batch_detector.py` | 纯 struct 条带/窗口真实检测（无 Pillow 整图解码）、Predictor=2 还原与 3/浮点拒绝、跨窗口 NoData 列统计、JSON 安全 NoData 诊断、网格/修订校验 + Mock 中心框 |
| `backend/services/batch_mask.py` | 条带 TIFF、流式校验、轻量 Overlay、原子发布、轮次锁、重启复用、解码上限、整数环境变量 |
| `backend/services/batch_worker.py` | 检测/掩码接线、异步提交复核、复核前后 PAN/NOSR/Mask/备份身份双采集与全量重验 |
| `backend/services/goal_runtime.py` | Overlay 元数据与 PNG 只读路由（路径保护、摘要与身份校验） |
| `frontend/src/lib/batchOverlay.ts`（新增） | 契约校验、身份匹配、逐格投影、加载失败收敛 |
| `frontend/src/components/BatchDetectionOverlay.vue`（新增） | 查看器叠加层组件 |
| `frontend/src/pages/ViewerPage.vue` | 挂载叠加层组件 |
| `frontend/src/lib/__tests__/batchOverlay.test.ts`（新增） | 26 项前端单测 |
| `.e2e/test-batch-overlay.js`（新增） | 浏览器回归：身份/裁剪窗口/顶点坐标/缩放平移/分屏/刷新/文档失败/PNG 失败/换图 |
| `backend/tests/test_batch_artifacts.py` | 57 项 D1/D2 验收（含窗口内存探针、跨窗口 NoData、轮次锁、Predictor 逐像素、NaN NoData HTTP） |
| `backend/tests/test_batch_worker.py` | 38 项调度与提交边界验收（含复核后与复核中 PAN/NOSR/Mask/备份漂移） |

## 5. 测试

本轮实际执行：

```text
# 后端目标套件
./.venv/Scripts/python.exe -m pytest \
  backend/tests/test_batch_artifacts.py backend/tests/test_batch_worker.py \
  backend/tests/test_batch_manifest.py -q

# 后端全量
./.venv/Scripts/python.exe -m pytest backend/tests -q

# 前端
cd frontend && npm test          # 自动跑 check:terms
cd frontend && npm run typecheck
cd frontend && npm run build
cd .e2e && node test-batch-overlay.js
```

结果（2026-11-02 第三轮补修后实跑）：

- 三个批处理后端套件：**109 passed**（57 + 38 + 14），19.00s，1 个 Starlette/httpx 弃用警告。
- 后端全量：**1285 passed / 7 skipped / 195 subtests passed**，另有 **5 个既有失败**全部在 `backend/tests/test_agent_loop.py` 的 `thinking_status` 事件顺序断言（期望 `["tool_call","tool_result","assistant"]`，实际多一条 `thinking_status`）。这些失败在 `main` @ `1f6d49d` 上同样存在，`backend/agent` 与测试文件均无本轮改动，属既有问题，不计入本轮修复结论。
- 前端：本轮未改前端逻辑，沿用上一轮 **628 passed / 43 files**；`vue-tsc --noEmit` 零错误；`check:terms` 通过。
- 浏览器回归：本轮未改查看器逻辑，`test-batch-overlay.js` 沿用上一轮通过结果（含裁剪窗口、逐顶点屏幕坐标、PNG 503）。

**P1 1 峰值内存实测**（子进程，`PeakWorkingSetSize`；8192 宽、uint8、单条带整幅图、32 行窗口）：

| 影像 | 峰值内存 |
| --- | --- |
| 8192×4096 | 36,200,448 B（34.5 MiB） |
| 8192×8192 | 36,179,968 B（34.5 MiB） |

影像面积翻倍，峰值不变；回归断言 `增长 < 16 MiB`。基线包含解释器、NumPy、Pillow 导入开销。第三轮复审另用 30000×30000 合成影像独立复跑，峰值 **39.90 MiB**（基线约 32.6–32.9 MiB），同一 Fixture 修复前为 901.34 MiB；本轮未再生成 900 MB 临时文件，固定窗口模型与上述两处探针共同覆盖「不按面积增长」。

`test_batch_artifacts.py` 新增/加强覆盖：

| 用例组 | 验证点 |
| --- | --- |
| 真实检测 | 真实像素读取与 source 坐标；`window_rows` 生效；NoData 行不被误报；修订变化/网格漂移/参考尺寸相等或更大/8 位不一致拒绝；多波段、tiled、不可读头均拒绝 |
| 有界读取（P1 1） | 每个窗口行数 ≤ `window_rows`、总行数/像素与原图一致；`Image.crop` 被替换为抛错仍能完成扫描；Deflate 条带与无压缩逐像素一致；LZW 明确拒绝 |
| Predictor（第三轮 P1） | 手写 Deflate+Predictor=2 TIFF：2 行窗口解出的 8×4 uint16 与 Pillow 独立解码逐像素一致，原图无平列时不再误报平列；Predictor=3 与浮点 Predictor=2 明确拒绝 |
| 内存探针（P1 1） | 子进程固定 32 行窗口，8192×4096 → 8192×8192 峰值增长 < 16 MiB |
| 跨窗口 NoData（P2 6） | 列有效数跨窗口累计；`window_rows=1/2/4` 对首列 `[7,0,7,0]` 都判为平线 |
| NaN NoData（第三轮 P2） | float32 `nan` 哨兵：检测结果 `allow_nan=False` 可序列化；真实 Mask 写出的 `overlay.json` 严格 JSON；Overlay HTTP 200 且诊断 `nodata="nan"` |
| 轮次锁（P2 7） | 同进程第二个构建者不删除活动 `.tmp-*`；另一进程持锁时得到可重试错误，终止后可重试成功 |
| Polygon 栅格化 | 10×10 对角线多边形 36 像素；跨条带（条带行 1/2/5/7）逐像素一致；角点与多边顶点与直接 PIL 一致 |
| Mask 配置 | 环境变量 `64` → 3 条带；`abc/0/-3/70000` 报错；Overlay 边 32 → scale 2；非法边报错 |
| 解码上限 | 超长/截断 Deflate、条带字节数/偏移越界、数组数量 1,000,000、Pillow 像素上限 |
| 重启复用 | 同摘要复用；候选变化重建；篡改 Mask 重建；不完整 bundle + 残留 `.tmp-*` 清理 |
| 叠加层 API | 元数据/PNG 200；越界路径 403；摘要漂移 409；Manifest Item 不符 409；无 Overlay 404 |

`test_batch_worker.py` 新增覆盖：提交复核跑在 IO 线程（调度线程 2s 内持续查询 SR，不额外提交）；复核后 Mask 变化 → `BLOCKED` 且零预约零提交；复核后 PAN 变化 / NOSR 变化 / 新出现 PAN 备份 → 均 `BLOCKED`、零预约零提交；**复核过程中（`verify_mask` 返回后、身份采集前）PAN/NOSR/Mask 变化或 PAN 备份出现 → 均 `BLOCKED`、零提交、零预约**；复核超时退避不重复提交；4 个 Item、并发上限 2 时检测峰值恰为 2；已发布未提交的 Mask 重启后复用同一 sha；崩溃在发布前时重建同轮且不残留临时目录。

浏览器回归覆盖：未绑定身份的本地影像不画；身份绑定后出现 provenance 芯片与候选；裁剪窗口逐格等于 `pane.rect` 且 `overflow:hidden`；缩放/平移/分屏后影像盒与**候选顶点实际屏幕坐标**逐项匹配（非只有数量）；放大后影像超出裁剪窗口仍被裁住；B 格裁剪窗口不伸进 A 格；刷新后 `<img>` 缓存键更新；文档 503 时无旧叠加层；**文档 200 但 PNG 503 时无候选、无 provenance 芯片、显示错误并可重试**；身份变化与真实换图后按格停画。

## 6. 已验证不变量

1. Mask 始终是 PAN 尺寸、单波段 uint8、0/255、至少一个前景像素。
2. 多边形栅格化不改原始几何；越界由光栅器裁剪，面积与直接栅格化一致。
3. 真实检测不调用 Pillow 整图解码：每个读取窗口 ≤ `window_rows` 行，内存由窗口而非影像面积决定；无压缩与 Deflate 条带都走同一条有界路径，其它压缩显式拒绝。
4. Predictor=2 在每个整行窗口内按采样 dtype 做模 2^bits 累加还原，与独立解码器逐像素一致；Predictor=3 与浮点 Predictor=2 明确拒绝。
5. NoData 列判定跨窗口累计，`window_rows` 取 1/2/4/默认值时结论一致；`nan`/`±inf` 哨兵写成字符串 + `nodata_kind`，不会以原始浮点进入 JSON。
7. 大图写入按条带分配；条带行数与 Overlay 边显式整数解析、范围受限，非法值报错。
8. Mask/Overlay/JSON 作为一个目录原子发布；半写失败不留可见最终产物，不覆盖既有目录。
9. 同一轮次的复用检查、残留 `.tmp-*` 清理、构建与 rename 全程持 `<round>.lock`；并发构建者得到可重试错误，不会删除活动临时目录。
10. 重启后只复用完整且摘要一致的轮次；非法轮次重建，临时目录清理。
11. demo 接受 `real` 或 `mock`，production 拒绝 `mock`；提交前再次核对契约、坐标空间、dtype、`item_id` 与 provenance。
12. 提交前复核不占用调度线程；复核前后各采集一次 PAN/NOSR/Mask 身份与 PAN 备份集合，复核后再次确认冻结修订；两次不一致、预约前再采集不一致，均 `BLOCKED` 且零预约零提交。
13. Overlay 只能读取工件根目录下、与 Goal/Item/Manifest/checkpoint 一致的已发布产物。
14. 查看器叠加层只画身份匹配的格子：裁剪窗口 = `pane.rect`，影像盒与候选点只用格内坐标，候选顶点与实际屏幕位置一致，不伸进邻格。
15. 叠加层 PNG 加载状态独立于文档状态；PNG `error` 时不显示候选与 provenance 芯片，只显示失败原因与重试。

## 7. 未覆盖的真实环境边界

- **真实检测算法与掩码质量**：`flatline-scan` 只检测平线/坏线特征，未验证真实模糊、云或其它缺陷的召回/精度；生产算法仍需替换。
- **真实 SR/GPU/盘阵**：未触发真实 SR、GPU、Slurm 或盘阵扫描；全部测试使用临时目录与合成影像。PR E 真实 SR 验收仍被 P1 关闭前的门槛挡住。
- **真实大 PAN 的内存峰值**：本轮子进程探针用 8192 宽合成影像、第三轮复审用 30000×30000 合成影像分别证明峰值不随面积增长，但均未在内网真实盘阵影像上实测；窗口行数/条带行数仍需按实际宽度调小。
- **压缩与布局覆盖**：真实通道支持经典/BigTIFF 头解析，但只对无压缩与 Deflate 条带做了有界读取测试；Predictor=2（整数）已实现并逐像素比对，Predictor=3（浮点）与浮点 Predictor=2 明确拒绝；LZW/JPEG 压缩、tiled、planar 多平面同样拒绝，真实影像若为这些布局需先转码或另行实现。
- **地理参考**：没有真实头信息，未做投影/仿射契约校验；NoData 只读标签 42113 的首个数值；NaN NoData 的端到端测试使用合成 float32 TIFF。
- **浏览器回归**：`test-batch-overlay.js` 只在本机 Chrome/Edge 跑过，未在内网真机与真实后端工件上跑；像素级叠合精度仍靠坐标断言，不靠肉眼比对。
- **PR E/F**：跨批共享容量、attempt 语义、Verifier 分层、50 景 Fake 验收与进度 Panel 未开始。

## 8. 下一步

- 第三轮复审已通过，PR D 关闭；PR E（提交/监控/分层验证/50 景 Fake 验收）已实施，见 [batch-pr-e.md](batch-pr-e.md)。
- D3 剩余部分（Item 跳转、刷新恢复、进度 Panel）与 PR F 一并规划；Overlay 读路径与查看器叠加层已先行落地。
- 真实 SR/GPU/盘阵准入必须单独确认：真实 Detector/Mask、真实产物契约、修复质量 Verifier 与 production 提交门同时到位后才能打开。


## 2026-11-07 — 查看器未发布 Overlay 的 404 提示修复

- 原因：自动跟随与手动定位无条件请求 Overlay，未使用 overlay_ready；未发布时后端按契约返回 404，错误提示持续保留。
- 修复：目标携带就绪标志，列表从 checkpoint.mask.overlay 推导；组件订阅同一 Item 的快照就绪状态，未发布不请求，发布后自动加载，不重复打开影像。保留真实加载错误、重试、旧 URL 与显式关闭行为。
- 验证：既有相关测试 68 项、新增组件测试 4 项通过；类型检查与生产构建通过。内网现场效果待核，需更新 frontend/dist。
