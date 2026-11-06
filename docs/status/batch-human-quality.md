# Batch 真实差异检测与人工质量验收

本轮已落地本地测试闭环：共享检测 → 真实 Mask TIF → 沿用 SR 生命周期 → 逐区域人工审核 → 整景确认 → 独立 Goal 验证。真实生产提交门仍关闭；未执行内网真实数据/GDAL/GPU 单景 SR 联验，不能据此宣布生产闭环验收完成。

## 源码审计与改动

- `image_diff.py` 原有 `analyze_arrays` 是 HTTP 与 Batch 共用的检测核心；新增 `batch_difference.py` 只负责受限条带读取、共同分析网格、身份/配准核对与结果适配。没有第二套差异算法。
- `auto_mask_geometry.py` 继续复用连通域、欧氏膨胀、孔洞诊断、轮廓追踪、RDP、正式栅格化与 4× 覆盖检查。HTTP 默认前 16 区域语义保持；Batch 使用 512 区域预算。超限或失败均记入候选统计，禁止部分 Mask 静默提交。
- `batch_detector.py` 增加共享差异 `diff` 模式，保留 Mock 测试路线；`batch_manifest.py` 冻结 production 检测参数，`batch_worker.py` 强制真实路径、检查来源与候选完整性。
- `batch_mask.py` 沿用现有条带 MaskBuilder 和 SR 的 PAN 尺寸、uint8 0/255 TIF 契约，增加影像身份检查。未改 `run_sr.py`、Executor、Agent Loop。
- `batch_quality.py` 新增持久审核与绑定验证；`store.py` 增量建表、状态支持和最终完成守卫；`goal_worker.py`、`batch_worker.py` 在生产验收时要求人工记录。
- `goal_runtime.py` 增加审核 API；`batch_snapshot.py` 增加紧凑统计。旧掩码提交前复核继续使用原接口，与本次产物质量验收分开。
- 前端新增 `lib/batchQuality.ts`、`stores/batchQuality.ts`、`BatchQualityPanel.vue`、`BatchQualityOverlay.vue`；接入原 `BatchProgressPanel.vue`、`ViewerPage.vue`，扩展 API 类型、事件刷新和状态标签。复用现有分屏、联动 Camera、闪烁与预览加载，无新增一级页面或 Viewer 重构。

## 坐标、输入与资源边界

条带读取源 DN，在最多 2048 边长的共同分析网格检测，再通过显式变换转成源 PAN 像素坐标。记录源尺寸、分析尺寸、PAN/NOSR 变换、NoData、有效像素、算法参数、耗时、区域数、顶点、覆盖、面积、孔洞及回退诊断。MaskBuilder 消费源像素多边形，不依赖 Viewer。

要求冻结来源仍有效、同景 PAN/NOSR 身份、尺寸比例精确一致。有 GeoTIFF 元数据时核对 CRS、方向和范围；无标签时沿用现有生产比例网格约定，并明确记录该假设，不能证明未知影像已配准。无有效数据、输入漂移或不支持的 TIFF 编码会失败，不当成无缺陷。保守几何进入既有提交前复核。

100% 覆盖指已检测分析区域及投影后的采样像素覆盖，不代表源全分辨率检测召回率；下采样可能遗漏细小异常。当前读取器支持范围沿用现有条带 TIFF 实现，未增加全幅解码兜底。

## 人工验收协议

SR 契约通过后 Item 进入 `AWAITING_REVIEW`，释放容量，Producer/Monitor 继续其他景。审核拒绝为 `REVIEW_REJECTED`，等待人工处理，不重投 SR。所有区域通过后仍需显式整景确认，随后 Worker 再验证、置成功；生产 Goal 和 Store 最终完成入口都检查审核，不接受调用方伪造的 `ok=true`。

审核绑定原输入、NOSR、Job/fingerprint/参数、Mask 文件及内容哈希、配置及修复回执哈希、备份和本次新 PAN 产物身份、区域几何哈希。替换文件、换 Job、换输入、改配置或几何后旧通过失效。文件身份采用现有资源版本机制，未对所有 GB 级影像逐字节哈希。

新增 API：

- `GET /api/goals/{goal_id}/quality-reviews`：逐景列表、当前有效性、区域统计。
- `GET /api/goals/{goal_id}/items/{item_id}/quality-review`：绑定、区域决定、审计和受控预览引用。
- `POST` 同一路径：区域或整景的 `approved/rejected/pending`，携带审核人、原因、版本和幂等键；驳回必须填原因。事务内 CAS，重复请求返回已保存结果，旧版本冲突返回 409。

工具栏原任务面板展示待验收景/区域与通过/驳回数。选景加载该次 Job 的 PAN 和对应 NOSR，双图同步视野并定位掩码区域；支持轮廓、半透明覆盖、闪烁。异步加载和人工决定都校验当前影像/版本；草稿编辑时不强制覆盖。审核人是显式记录字段，项目现有系统没有新增账号认证，因此不能视为身份认证或电子签名。

## 本地验证证据

- 后端最终扩展回归：358 passed、42 subtests，人工审核专测 12 passed。覆盖共享核心、25 区域突破 HTTP 16 限制、真实 Mask 像素覆盖、NoData/漂移、预算拒绝、并发 CAS、幂等、重启、产物替换、未审核不能完成及等待时其他景推进。
- 前端：66 文件、832 tests passed；类型检查与生产构建通过。保留原有依赖 sourcemap/chunk 大小、测试 Canvas 和 httpx 弃用提示。
- 实际后端浏览器联测：2 Job × 2 区域、真实 SQLite/API/Mask TIF/修复回执/契约验证，CPU 合成推理夹具；5 次人工操作，重启恢复、同步分屏与闪烁、拒绝后无重投、产物替换 409。最终 Goal BLOCKED（第二景人工拒绝），新增 SR 提交 0，浏览器错误 0。报告和截图在 `.e2e/artifacts/batch-quality-*`；脚本 `.e2e/test-batch-quality.js`，夹具 `backend/tests/quality_browser_fixture.py`。
- 新代码实测内存：8192×4096 / 8192×8192 源 TIFF，128 分析边长、32 行读取窗口，进程峰值约 66.68 / 66.75 MiB；这是受限网格测试，不是 2048 网格或真实内网生产吞吐指标。耗时与区域诊断由实际检测结果记录，不沿用旧合成性能数字。

## 尚需收尾与内网准入

1. 用一景受控真实数据核对无标签配准约定、NoData、细小异常召回、最大分析网格内存和真实掩码覆盖；按场景调参，不能以面积比充当检测准确率。
2. 内网 GDAL/GPU 单景 SR 联验：从实际提交到新 PAN/备份/回执，再人工逐区域确认。当前联测使用已完成 CPU 夹具 Job，不证明真实 GPU 提交成功。
3. 校验大景预览、同步定位、现场多人操作、恢复与产物替换；Mask 完整性复核会读取 Mask 内容，超大 Mask 的验证延迟仍须现场测量。
4. 验收后再设计受限 production 准入；当前提交边界仍拒绝 production，允许共享检测、私有 Mask 构建和已有 Job 的监控/审核，不开放全批生产。

保留原有工作树修改；未 Git Commit/Push。测试辅助清理曾被自动审批拒绝，已通过会话中断停止辅助服务，相关临时目录保留。
