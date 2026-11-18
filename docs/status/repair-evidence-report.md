# 修复图像证据报告：当前能力与验收

状态：工程预览可运行；工程进度估计约 70%（非验收通过率）。完整 P0–P9 验收未完成，生产开关保持关闭。工作包与剩余实施顺序见 [实施规划 §0](../planning/repair_evidence_report_plan.md)。

## 可用能力

- 独立报告、区域、任务、分析 SQLite 表；幂等报告版本、任务租约令牌、行版本保护与重启领取。
- 最终质量审核绑定提供区域、NOSR、修复前备份、产物。报告不修改人工审核、Goal 完成或 SR 提交。
- inclusive bbox 转半开窗口；固定上下文及主体/边界窗口；超大区域标抽样。旧任务缺少几何不支持精确报告。
- 同 CRS GeoTIFF 仿射与非整数网格按像素中心配对；无 GeoTIFF 证据时仅允许既有整数比例网格并显示限制，不支持 CRS 重投影。
- 新保存的手绘掩码有绑定文件版本的几何记录；历史无几何任务逐行显示 unsupported，不扫描整幅 Mask 补猜。
- 无压缩单波段 Classic TIFF / BigTIFF 局部列跨度读取；8/16/32 位、大小端、有符号及浮点、有效性与 NoData。
- 无损 PNG、共享百分位映射、逐角色 DN 指标、before/output 数值变化、SHA256、原子发布与空间配额。
- 最低优先级重任务队列；全局非终态 Job/Batch 计算及已批准待提交 Mask 阻止后台领取。本地执行器通过内存 activity_snapshot 提供 active/unknown/quarantine/generation。报告协调器默认关闭。
- 专用只读多模态请求复用现有 Config/transport；真实图片水合、角色顺序、严格 JSON、证据引用验证、缺 before 的改善结论限制、成功分析缓存。
- 首次模型请求与一次格式修正共用 60 秒 deadline；每次派发重新检查准入。前台任务占用时回到队列，不记为模型失败；缓存复用经租约/行版本检查恢复 ready。超时、拒绝、图像不支持、工具请求、不完整输出及引用错误分别记录。
- 共用同一平台 SQLite 的实例使用 `model_resource_leases` / `model_resource_waiters` 进行模型资源互斥。同一规范化模型 base URL 为单槽，普通聊天、持久 Agent 请求、证据分析与自动标题共同参与；聊天等待登记对其他实例可见，后台任务不抢占等待聊天。租约有效期覆盖绝对请求 deadline 加 30 秒清理余量；取消先完成传输清理再按 token 释放，崩溃通过过期恢复。不同数据库之间不提供此互斥。
- 批次暂时不满足报告条件时，报告标为 superseded 并撤销运行中任务令牌；同来源指纹恢复时复用原报告 ID/revision、已完成行和分析，未完成任务重新入队。变化的来源仍建立新报告；批次入口优先选择当前有效报告，避免较高 revision 的历史报告遮住恢复报告。
- `/repair-reports/:reportId`：30 行服务端分页、双图、展开三角色、状态、JSON 下载、单行证据/分析重试。
- 独立 `repairEvidence` Store 管理筛选、排序游标、轮询和晚到响应；分析记录按需加载，展示失败与缓存复用，重试失败时保留最近成功意见并标注当前状态。切页、换报告或离开时取消历史请求。
- Agent 卡与 Batch 面板共享报告引用。定位校验当前审核绑定，跨 Goal 请求带 review_id；手绘模式拒绝覆盖，历史失效保留归档裁片。

## 配置与回退

```text
SR_EVIDENCE_REPORT_ENABLED=0
SR_EVIDENCE_ANALYSIS_ENABLED=0
SR_EVIDENCE_STORAGE_LIMIT_BYTES=<必须显式配置正整数配额>
SR_EVIDENCE_MODEL_MAX_BYTES=8388608
```

`evidence_config.load_settings` 校验并冻结报告预算：默认单角色单窗口 32 MiB、源边长 2048、上下文输出 512、细节边长 512、最多两个细节。对应 `SR_EVIDENCE_CONTEXT_EDGE`（64–1024）、`SOURCE_MAX_EDGE`（512–2048）、`DETAIL_EDGE`（64–512）、`MAX_DETAIL_WINDOWS`（0–2）、`MAX_READ_BYTES`（4096–33554432）均使用同一 `SR_EVIDENCE_` 前缀。`MODEL_CONCURRENCY` 仅接受 1；资源互斥由共享 SQLite 租约执行，真实部署与共享 GPU 行为仍需实机验收。模型沿用 SR_LLM_*。前端资源以根路径部署，支持多层 history 路由刷新；子目录部署需独立配置 base 和 router。

回退关闭两个开关并重启，保留报告表与证据目录。不要删除生产 PAN、NOSR、Mask 或人工审核记录。

## 已运行检查

- 证据几何、读取、报告、调度、分析、扩展、恢复与既有 HeavyWork 队列组合共 63 项通过（包含模型故障矩阵与已批准 Mask 准入）。
- 加模型资源多进程互斥、跨实例聊天/分析争用、超时/取消释放、TCP transport 与标题回归的组合 101 项通过；另有 worker 资源争用回队列专项。覆盖同来源报告暂停/恢复、证据与分析任务续跑、旧令牌阻写、较旧 revision 恢复后的有效报告查找。上述数量包含部分相同测试，勿累加。
- Agent Loop、SSE ownership 与 Goal runtime 扩展回归为 65 项通过、5 项未通过；5 项均为 `TestOnStepSeam` 对 `thinking_status` 事件的断言不一致，仓库原版 Loop 同样复现。不列为本功能全量验收通过。
- 前端报告 Store 6 项通过，覆盖分析记录按需加载/失效与分页轮询；术语检查、类型检查、生产构建通过。此处专项结果不代表全量前端回归。
- 构建后浏览器检查：双图顺序、before 展开、按需分析记录空态、刷新恢复、390px 窄屏通过。
- 合成 4096² uint16 图上 512² 窗口实际像素读取 524288 字节；五次热缓存样本见本地 work/evidence-benchmark.json。不是冷缓存或盘阵性能结果。
- multi_user_work 组合中 3 项遇到 Windows SQLite 文件占用清理异常，原因未确认，不计为通过。

## 复跑

```powershell
python -m pytest backend/tests/test_evidence_geometry.py backend/tests/test_evidence_raster.py backend/tests/test_evidence_report.py backend/tests/test_evidence_worker.py backend/tests/test_evidence_analysis.py backend/tests/test_evidence_extended.py backend/tests/test_evidence_recovery.py backend/tests/test_batch_heavy_queue.py -q
$taskEvidenceRoot = Join-Path ([IO.Path]::GetPathRoot((Get-Location).Path)) ('er-' + [guid]::NewGuid().ToString('N').Substring(0,6))
python -m backend.tests.quality_browser_fixture --root $taskEvidenceRoot --port 8797 --evidence
# 另开终端：
$taskEvidenceRoot = '<上一个终端实际使用的短临时目录>'
$env:SR_EVIDENCE_REPORT_ID=(Get-Content -Raw (Join-Path $taskEvidenceRoot 'fixtures.json') | ConvertFrom-Json).report_id
node .e2e/test-repair-evidence.js
python -m backend.tests.benchmark_repair_evidence --root work/evidence-benchmark --output work/evidence-benchmark.json
```

夹具禁止真实 SR 提交，仅使用合成 TIFF 和合成推理产物。先核对 `python --version` 和依赖；工作区没有虚拟环境目录时不要使用不存在的 `.venv` 路径。前端命令在 frontend/ 中运行。

Windows 的合成修复夹具会创建多层生产长文件名路径，包含第二批次子目录；使用盘符根下的短测试目录，默认用户 TEMP 路径也可能过长。避免在工作区深层目录触发 WinError 3 / 长路径限制。夹具失败不应解释为报告或真实 SR 的验收结果。

## 未完成范围

完整验收仍需要：孔洞与最终审批子集组合、before/output 空间映射边界；手绘与旧几何缺失的端到端报告专项；发现/任务/模型准入的长时多实例压力；执行器 generation 贯穿任务派发的验证；共享 GPU 与前台聊天准入实测；31/65 行筛选分页和历史记录故障态浏览器验证；历史 before 只读 Viewer、跨批次/快速点击/用户中途换图及布局变动的组合验收；A/B/C/D 各五次冷/热性能对比；真实盘阵吞吐、真实模型视觉评测。不同 CRS 重投影仍明确不支持。

规划 §12 的 P9 为最终验收与交接，只有工程缺口及必要验收完成后才能标记完整交付。真实盘阵与视觉评测须保留实机待核，不得由合成测试代替。
