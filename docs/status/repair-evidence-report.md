# 修复图像证据报告：当前能力与验收

状态：工程预览可运行；完整 P0–P9 验收未完成，生产开关保持关闭。

## 可用能力

- 独立报告、区域、任务、分析 SQLite 表；幂等报告版本、任务租约令牌、行版本保护与重启领取。
- 最终质量审核绑定提供区域、NOSR、修复前备份、产物。报告不修改人工审核、Goal 完成或 SR 提交。
- inclusive bbox 转半开窗口；固定上下文及主体/边界窗口；超大区域标抽样。旧任务缺少几何不支持精确报告。
- 无压缩单波段 Classic TIFF / BigTIFF 局部列跨度读取；8/16/32 位、大小端、有符号及浮点、有效性与 NoData。
- 无损 PNG、共享百分位映射、逐角色 DN 指标、before/output 数值变化、SHA256、原子发布与空间配额。
- 最低优先级重任务队列；全局非终态 Job/Batch 计算阻止后台领取。报告协调器默认关闭。
- 专用只读多模态请求复用现有 Config/transport；真实图片水合、角色顺序、严格 JSON、证据引用验证、缺 before 的改善结论限制、成功分析缓存。
- `/repair-reports/:reportId`：30 行服务端分页、双图、展开三角色、状态、JSON 下载、单行证据/分析重试。
- Agent 卡与 Batch 面板共享报告引用。定位校验当前审核绑定，跨 Goal 请求带 review_id；手绘模式拒绝覆盖，历史失效保留归档裁片。

## 配置与回退

```text
SR_EVIDENCE_REPORT_ENABLED=0
SR_EVIDENCE_ANALYSIS_ENABLED=0
SR_EVIDENCE_STORAGE_LIMIT_BYTES=<必须显式配置正整数配额>
SR_EVIDENCE_MODEL_MAX_BYTES=8388608
```

读取预算目前固定：单角色单窗口 32 MiB，源边长 2048，上下文输出 512，细节边长 512、最多两个细节。规划文档中其余建议配置尚未全部接入，不应设置后假定生效。模型沿用 SR_LLM_*。前端资源以根路径部署，支持多层 history 路由刷新；子目录部署需独立配置 base 和 router。

回退关闭两个开关并重启，保留报告表与证据目录。不要删除生产 PAN、NOSR、Mask 或人工审核记录。

## 已运行检查

- 证据专项 34 项通过；加既有 HeavyWork 队列回归共 42 项通过。
- 相关后端组合回归 109 项通过、12 subtests 通过（证据专项包含于该组合，勿累加）。
- 前端全量 110 文件、1150 项通过；术语检查、类型检查、生产构建通过。
- 构建后浏览器检查：双图顺序、before 展开、刷新恢复、390px 窄屏通过。
- 合成 4096² uint16 图上 512² 窗口实际像素读取 524288 字节；五次热缓存样本见本地 work/evidence-benchmark.json。不是冷缓存或盘阵性能结果。
- multi_user_work 组合中 3 项遇到 Windows SQLite 文件占用清理异常，原因未确认，不计为通过。

## 复跑

```powershell
.\.venv\Scripts\python.exe -m pytest backend/tests/test_evidence_geometry.py backend/tests/test_evidence_raster.py backend/tests/test_evidence_report.py backend/tests/test_evidence_worker.py backend/tests/test_evidence_analysis.py backend/tests/test_batch_heavy_queue.py -q
.\.venv\Scripts\python.exe -m backend.tests.quality_browser_fixture --root work/evidence-acceptance --port 8797 --evidence
# 另开终端：
$env:SR_EVIDENCE_REPORT_ID=(Get-Content -Raw work/evidence-acceptance/fixtures.json | ConvertFrom-Json).report_id
node .e2e/test-repair-evidence.js
.\.venv\Scripts\python.exe -m backend.tests.benchmark_repair_evidence --root work/evidence-benchmark --output work/evidence-benchmark.json
```

夹具禁止真实 SR 提交，仅使用合成 TIFF 和合成推理产物。根目录使用项目虚拟环境，前端命令在 frontend/ 中运行。

## 未完成范围

完整验收仍需要：GeoTIFF 仿射/CRS 核验及非整数映射；孔洞与审批子集专项；旧几何缺失的逐行 unsupported 降级；手绘最终矢量持久化补齐；全局执行器 quarantine/generation 的明确公共快照；发现与任务租约多实例争用及恢复压力测试；失败分析历史、完整模型故障矩阵及共享 GPU 准入验证；筛选/排序游标、场景分组和独立前端报告 Store 专项；历史 before 原始图只读 Viewer；跨批次/快速点击/用户中途换图浏览器验收；A/B/C/D 五次冷/热性能对比；真实盘阵吞吐、真实模型视觉评测。

规划正文定义 P0–P8，没有定义 P9。P9 在此作为最终验收与交接，只有上述工程缺口完成后才能标记完整交付。真实盘阵与视觉评测须保留实机待核，不得由合成测试代替。
