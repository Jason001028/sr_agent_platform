# P1 差异定位 → 自动绘制 Mask 多边形

> 2026-11-06：本地实现及合成影像验证完成。原工作树没有本文件，本轮按用户给出的 P1 要求建立契约与实施记录。真实内网大图与检测质量待核。

## 1. 源码审计与范围

- `backend/api/app.py` 的 `/api/diff/analyze` 在工作线程运行，默认预览 PNG 与 `precision=source-roi` 共用 `image_diff.analyze_arrays`。
- `backend/services/image_diff.py` 使用 SciPy 8 邻域 labels，按 `_pick_regions`（平均差异 × 面积平方根）排序；`_trace_outer` 是 Moore 外轮廓，`_rdp` 是闭合 RDP。旧轮廓仅有 IoU 校验，不能据此认定带安全边距的完整覆盖。
- `backend/services/mask.py::rasterize_polygons` 的权威语义为整数像素中心、边界含入、PIL polygon。4× 覆盖仅作补充，不能替代正式整数填充检查。
- 前端 `stores/diff.ts` 已有差异显示、预览/ROI 精算、结果与像素代次隔离；`Toolbar.vue` 实际只有差异定位与结果显隐，**没有「延续到下一景」按钮**。本轮自动绘制键紧邻差异定位，不虚构或新增巡览延续流程。
- Mask 没有独立 Store：`ViewerRec.maskRois` 保存缩略图坐标数组，`addMaskPolygon` 是现有添加入口；原 `undoRoi` 仅 pop，文件列表的 undo 与 Mask 无关。原编辑器支持添加、选择、删除、魔棒、合并，没有独立顶点拖动编辑器，也没有 16/24 顶点上限。本轮自动几何单独限制 ≤24，保留手绘既有语义。
- `stageKind=input` 是后端确认的可修复本体，包含 RC 的 PAN.tif 与 SC 的生产全名；不能以文件名或同目录判断。自动绘制要求该本体在实际分析 A/B 中、具备绑定它的 autoMask 源坐标映射、源版本与尺寸一致，并在预备/提交向量前两次用服务端 siblings 复核 kind、Scene ID、源版本与尺寸。2026-11-07 修正：普通打开/拖入/差异候选建卡不一定有 `tourIdentity`，其缺失不再判为「只有 NOSR / SR」；若提供巡览证据，角色、序列和尺寸仍须一致，不忽略明确矛盾。undefined 在旧手绘入口可视为本体，但不满足自动绘制条件。
- `thumbToOrig`（现有 Mask JSON/TIFF/盘阵写入入口复用）采用端点对齐 `(W−1)/(tw−1)`。自动区域通过新增精确逆函数 `sourceToMaskThumb` 入库，确保未来导出仍是后端核验过的整数源顶点。
- 不生成最终 mask.tif，不调用 run_sr，不改变 Batch Worker、调度或 SR 生产契约。

## 2. 可选接口

`POST /api/diff/analyze` 原请求/原字段保持兼容。仅请求 `autoMask:true` 时增加可选 `autoMask` 响应。Viewer 的预览与 ROI 精算均请求此字段；旧服务不返回时隐藏操作键。

新增请求字段：

```json
{
  "autoMask": true,
  "autoMaskMargin": "standard",
  "autoMaskGrid": {"width": 2048, "height": 1536, "x": 100, "y": 200},
  "image_a": {"sourceRevision": "源版本", "pixelKey": "前端冻结像素签名"},
  "image_b": {"sourceRevision": "源版本", "pixelKey": "前端冻结像素签名"}
}
```

`autoMaskGrid` 是**裁剪前完整分析网格**与上传 PNG 左上角，源 ROI 路径不用它。无裁剪的旧调用可以不传。尺寸、偏移非法时报 400，不推测位置。

`autoMask` 包含稳定 `analysis_id`、`parameter_version`、`margin`、`analysis_size`、`coordinate_space=analysis_pixel`、`polygons`、`sources`、`truncated`、几何与源映射耗时。

每个 Polygon 包含 `region_id/rank`、分析尺寸、points、vertex_count、coverage_ratio、supersample_coverage_ratio、area_ratio/area_ratio_limit、buffer_px/buffer_space、fallback、needs_review、holes、diagnostics、来源身份和 `source_polygons`。后者按 A/B image_id 分别给出整数源顶点、源栅格覆盖率和 drawable，不在前端猜 PAN 一定是 A。

`sources` 冻结 image_id、scene_id、source_revision、pixel_key、source_size 与 grid→source scale/offset。

### 坐标口径

- `points`、边距及面积指标均属于**分析网格**，零基像素中心。它们不是未经变换的原 TIFF 坐标。
- 预览上传的中心映射与 `sampleGray/_resize_pair` 一致：`source=(grid+scope+0.5)*sourceSize/fullGridSize−0.5`，再四舍五入、夹到源边界。
- 原始 ROI 按 `read_source_window` 的端点采样映射：`source=roiStart+grid*(roiSize−1)/(gridSize−1)`；A/B 均使用 API 实际读取并夹紧的各自 ROI 范围。
- 后端将源多边形逐行交给正式 PIL 栅格化，复核原分析区域投影到源图后的像素中心是否全部覆盖；仅分配一条源扫描线。
- `coverage_scope=projected_analysis_pixel_centres`：这是分析区域的覆盖保证，不是对预览未观察到的源图细节、真实伪影召回率或精度的保证。B 在不同网格重采样时，几何映射表达共同 ROI 范围，不表示每个灰度值来自单个原始 DN 像元。

## 3. 几何、安全边距与复核

纯接口为 `backend/services/auto_mask_geometry.py::build_auto_mask(labels, sorted_regions, margin=...)`；可供未来 Batch MaskBuilder 复用，本轮未接入 Batch。

1. 排序后前 16 个独立连通域逐个裁局部窗口，保留稳定区域 hash；不对混合 mask 追踪一个外轮廓。
2. 小孔洞填充；记录孔洞数量、面积和是否为大孔洞。大孔洞定义为 `hole_area > max(16, 0.10*filled_area)`，始终待复核。
3. 标准边距 `m=min(max(1,0.05*sqrt(filled_area)),20,max(1,0.10*bbox_short_side))`，单位是分析像素。紧/标准/松倍率 0.75/1/1.5，初始边距限制 1–20；覆盖不足时再加 1/2px，实际 buffer_px 如实返回。
4. 对填孔区域做欧氏距离膨胀、外轮廓和闭合 RDP；目标 16 顶点，最多 24。优先满足面积限制，再优先 ≤16 顶点。
5. 每次简化同时核对正式整数 PIL 覆盖与 4× 子像素 footprint 覆盖，二者必须 100%。超采样按 128 行条带分配，不创建全幅 4× 大数组。
6. 必要时使用凸包；凸包仍超顶点限制时回退凸包外接框并明确标记 `vertex_limit_fallback`。不以裁掉候选像素降低面积比。
7. 面积比分母为**原连通域面积**，不是填孔后面积。面积 ≥64px² 时要求 ≤1.8；更小区域上限 `max(1.8,(area+8*sqrt(area)+16)/area)`，允许安全边距的离散像素开销。超限、孔洞冲突、源投影冲突均有 diagnostics/needs_review。
8. 前端只绘制分析、超采样、源映射覆盖均为 100% 且不退化的区域；其余明确提示未绘制。面积或孔洞冲突但完整覆盖的区域保留可编辑多边形，动画描边与完成提示标注待复核，不声称精确原始边界。

## 4. UI、事务与来源

- 差异定位旁增加 28px 工具键，复用 --tb-*；无新 Panel，不要求热力图可见。
- 缺 PAN、本体未就绪、无可信配对、源尺寸/版本变化、旧分析、未闭合手绘草稿时禁用并给 title 原因。手绘草稿由用户完成或取消，不自动丢弃。
- 点击与动画提交前各调用一次现有纯只读 `/siblings?tour=true&known=...`：校验源文件 exists、srcRev、W/H、本体环节及配对角色/序列。5 秒超时；取消会 Abort，请求迟到不能污染新事务。失败禁用当前结果入口，重新加载并分析后恢复。
- 激活对应 PAN，进入既有 Mask 编辑模式；SVG 按区域顺序描边后填充，总时长约 3.2s，最多 16 区域自动缩短单区域时长。减少动态效果时直接一次性提交。动画不逐帧重绘 TIFF Canvas。
- 动画期间仅保存临时 SVG 向量，结束且所有身份/草稿/原 Mask 复核通过后，走与 addMaskPolygon 相同的 changeMasks 事务入口，一次赋值写入整批。切图、重新分析、清除结果、退出编辑或 Esc 取消，均不留下部分写入。
- 自动多边形的 `autoDiff` 元数据记录 source=auto_diff、analysis_id、image_id、group_id、region_id、parameter_version、initial_points、needs_review。数据仍为既有 Poly 数组；Mask 导出契约不变。
- 重复生成替换同 analysis_id 的未编辑自动区域，手绘与其它分析区域保留；坐标已修改的自动区域保留且不重复生成同 region_id。合并产生的新几何沿用既有编辑语义，作为人工几何保留。显式删除后的候选在再次执行生成命令时可重新生成。
- 新增独立 MaskHistory，最多 40 步小型向量快照，不碰列表撤销；自动批次、手绘添加、矩形、魔棒、删除、合并、清空均记录操作。整批撤销/重做按钮及 Ctrl+Z / Ctrl+Shift+Z / Ctrl+Y；未跟踪的直接坐标修改会阻止旧快照覆盖，避免丢失修改。
- 已绘制 Mask 与差异结果分离，清除差异不删除 Mask。当前 Mask 原本即是会话内编辑数据，本轮未新增跨页面/跨刷新持久化。

## 5. 实际修改文件

| 范围 | 文件 |
| --- | --- |
| 后端几何 | 新增 backend/services/auto_mask_geometry.py；修改 backend/services/image_diff.py、backend/api/app.py |
| 类型与坐标 | frontend/src/lib/diffLocate.ts、viewMath.ts；新增 lib/maskHistory.ts；api.ts 的 siblings 调用增加可选 AbortSignal |
| 编排与 Mask | frontend/src/stores/diff.ts、viewer.ts |
| UI | 新增 components/AutoMaskOverlay.vue；修改 Toolbar.vue、TifCanvas.vue、DrawPanel.vue（含窄视口换行，避免重做键挤出屏幕） |
| 测试 | 新增 backend/tests/test_auto_mask_geometry.py、lib/__tests__/maskHistory.test.ts、.e2e/test-mask-auto-draw.js；扩展 test_image_diff.py、diffStore.test.ts、polygonDrawing.test.ts |
| 原浏览器回归夹具 | .e2e/test-diff-visualization.js 补齐此前缺失的 srcRev/tourIdentity 可信配对证据；原测试断言保留，没有放宽 precisePairReason |

## 6. 本轮验证与实测

```text
cd frontend
npm run typecheck
npm test -- --reporter=dot
npm run build
# 65 files / 828 tests passed；术语扫描230文件通过；类型检查和构建通过

.venv/Scripts/python.exe -m pytest backend/tests/test_auto_mask_geometry.py backend/tests/test_image_diff.py backend/tests/test_mask.py backend/tests/test_pan_repair.py backend/tests/test_run_sr.py -q -p no:cacheprovider
# 176 passed / 42 subtests passed

.venv/Scripts/python.exe -m pytest backend/tests/test_api.py backend/tests/test_api_platform.py backend/tests/test_sr_verify.py -q -p no:cacheprovider
# 185 passed / 2 skipped / 29 subtests passed

node .e2e/test-mask-auto-draw.js
# 实际构建页面 + 当前 Python analyze_payload/几何（非伪造几何响应）；场景目录端点为合成夹具
node .e2e/test-diff-visualization.js
# 显隐缓存、热力/ROI/NoData像素、Camera、分屏/交替、候选定位、1366/1920通过
```

新增后端 17 个参数化用例，覆盖 L/月牙/斜线/椭圆/圆环/小点/边缘、10px 分离与前16、强制简化失败后的凸包回退、顶点/面积冲突、三档边距、默认旧接口、视野裁剪、源网格非整数比例与真实 PIL 再核验。原始 ROI 用例同步验证实际夹紧范围的 autoMask 映射。

新增前端覆盖整批撤销/重做、替换幂等、保留手绘/编辑几何、端点往返、旧后端、源文件丢失、动画中切图/改尺寸/改源版本/清除/草稿/重分析、减少动态效果和旧服务器核验迟到不影响新事务。

以下为**本轮新代码实际运行**的 220×180 合成形状样本，每种一个连通域（不是旧规划里的性能数字）：

| 形状 | 顶点 | 正式/4×覆盖 | 面积比 | 几何耗时ms | 结果 |
| --- | --- | --- | --- | --- | --- |
| L | 6 | 100% / 100% | 1.2094 | 17.13 | 无回退 |
| 月牙 | 21 | 100% / 100% | 1.2857 | 13.53 | 无回退，≤24 |
| 斜线 | 4 | 100% / 100% | 1.3547 | 9.85 | 无回退 |
| 椭圆 | 12 | 100% / 100% | 1.1341 | 11.94 | 无回退 |
| 圆环 | 8 | 100% / 100% | 2.6172 | 61.36 | 大孔洞/面积冲突，待复核 |
| 2×2 小点 | 4 | 100% / 100% | 2.0000 | 1.24 | 按明确小区域规则放宽 |
| 贴边块 | 4 | 100% / 100% | 1.0607 | 2.39 | 无回退 |

浏览器闭环使用 256×256 合成预览，3 个独立区域，顶点 4/4/12；分析与源映射中心覆盖均100%，面积比约1.139/1.144/1.120，无失败回退。新几何及投影约31–33ms（不含启动 Python、请求传输、完整差异计算与3.2s动画）。最新原始数据见 `.e2e/artifacts/mask-auto-draw-report.json`，动画/完成截图见同目录 `mask-auto-draw-animation.png`、`mask-auto-draw-complete.png`；原显示回归本轮截图另存 `mask-auto-draw-regression-1366.png` / `1920.png`。

测试环境存在已有 geotiff source-map 缺失、jsdom Canvas 提示、Starlette httpx 弃用和 Vite bundle-size 提示；无本轮失败。两个后端 skipped 沿用既有条件，未改断言。没有执行 Git Commit/Push。

## 7. 剩余边界

- 没有内网真实大图视觉/吞吐验收；时间数字不能推广成生产性能结论。
- 预览差异来自显示灰度；100%覆盖/面积比描述几何，不代表真实缺陷检测准确率，也不保证未采样源像元的召回。
- 没有跨整个3.2s持有文件锁；前后两次版本核验与本地像素代次隔离保护编辑写入，未来真正导出/提交仍走既有源版本与 SR 契约复核。
- 大孔洞以单外多边形填充后标待复核；不支持带洞多环可编辑几何。真实复杂区域可能只有保守凸包/外接框，标记冲突，不伪称精确。
- 仅复用既有选择/删除/合并/重绘编辑能力；本轮没有引入通用顶点拖动编辑器或 Batch 自动掩码链路。

## 2026-11-07：PAN 本体缺巡览元数据时的误判修正

`autoTarget` 原要求 A/B 里的 `input` 同时具有 `tourIdentity.role=pan`，但正常打开、拖入和差异候选建卡不保证携带巡览证书。现允许已确认的 input 使用原 Scene ID / revision / source_size / pixel_key / 坐标映射进入既有两次只读服务端复核；有巡览证据时仍检查已知角色与尺寸，并与服务端角色/序列核对。服务端类型与本地类型不符仍拒绝。A/B 不含本体时明确提示选择本体重新分析，不把当前活动的第三张 PAN 当作旧结果的绘制网格。

验证：新增 5 项专项（PAN 在 A、PAN 在 B、两图均缺巡览证书、服务端判 NOSR 时拒绝、第三张 PAN 不替代 A/B）；差异 Store 43 项通过，全量前端 73 files / 907 tests、typecheck、build 通过。`node .e2e/test-mask-auto-draw.js` 移除两张图的本地证书，服务端仍回可信类型和证书，实际调用 Python 差异/几何服务并验证 3 区域绘制、两次核验、手绘保留、整批撤销/重做、重复替换、Esc、源版本变化和旧接口。浏览器错误与禁止调用均为 0；不生成最终 Mask TIF，不运行生产 SR/GPU。脚本优先使用 SR_TEST_PYTHON 或项目 .venv，缺少 .venv 时使用 PATH 的 python。
