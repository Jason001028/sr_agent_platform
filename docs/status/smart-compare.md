# Smart Compare 接入与审查报告

日期：2026-11-12。状态：本地合成数据功能验收，生产校准及内网真实影像待验。

## 功能与操作

Viewer 顶栏「差异定位」新增第三种独立方法「智能对比」，保留 AbsDiff 默认选择和 SSIM 的旧计算口径。选 PAN 本体为 A、同景 NOSR 为 B，开始分析即可得到真实 DN 网格的连续热力图。也支持倒序进行全图网格分析，方差比始终是 NOSR/PAN；原图精检及原始尺度补扫要求 A 为 PAN。

热力图为固定蓝青—黄—橙红色标，异常强度 0–1，默认叠加不透明度 60%。显示阈值、不透明度、边界显隐、原图标记/独立热力图/叠加只改显示层。智能评分不使用旧的亮度归因显示权重。选择候选后强调相应范围，查看方差比、纹理损失、场景证据与可选频域诊断；上一/下一异常及恢复视野沿用 Viewer Camera。

原图精检入口包括候选、当前视野和既有框选 ROI。结果保留精检来源，允许返回网格快照。单次原图 ROI 边长 3–2048，超限明确拒绝，请放大视野或缩小框选范围；不静默下采样后宣称原图精度。使用方差、多尺度窗口与形态学所需 Halo 读取，B 通过实际源窗口与像素中心坐标映射到 A。原始尺度补扫按 512² 核心块顺序读取带 Halo 的上下文，每请求最多 64 块；最大值投影到有界概览保留小异常。补扫概览的区域统计来自概览，只有评分来自原始尺度，`analysisPrecision` 明确区分；补扫不是精确 Mask。

完成原始 DN 精检且轮廓可靠后，可选择单个或多个候选「加入修复掩码」。复用原 `autoDraw` 的源文件复查、覆盖/源坐标检查、草稿保护、SVG 暂存与原子向量事务；保留手工编辑内容，支持整批撤销。合并后的保守修复区域必须整体选中，不能通过选择一个小候选暗中纳入未选择的其他候选。含孔洞、不精确轮廓或概览精度不足的候选不开放精确加入。生成的仍是待人工复核的草案，不提交 SR。

开发者模式高级设置中启用三算法对照：共享同一份 DN、有效像素和网格，串行受控计算三种方法。返回区域数、面积、覆盖率、独有像素、像素 Mask IoU 及耗时，可切换缓存结果显示。这里的“并行对照”是产品对照功能，执行策略有意采用串行，以免同时持有三套工作数组。

## 审计与模块

初始分支 `main`，既有代码无未提交修改，仅内部参考脚本未跟踪。已读取 `SR_code/recovered_blur_detection_29_images.py` 的局部方差与纹理检测实现，独立实现新的计算；未复制其经验阈值、路径、生产 XML 写入或多进程方式。参考脚本保持原样且未加入提交。

复用 `image_diff.analyze_arrays` 的方法校验、有效像素、ROI、连通域、轮廓、孔洞和 PNG 输出；复用 `batch_difference` 的条带读取、2048 网格、版本/资源身份与生产比例网格检查；复用 `auto_mask_geometry`、`repair_regions` 的投影及覆盖守卫。HTTP 延续现有 `HeavyWork` 单执行队列，未创建新任务队列或 TIFF 缓存。

新增 `backend/services/blur_detection.py`（纯算法）与 `smart_compare.py`（有界读取、精检、补扫、配对身份与对照编排）。扩展 `image_diff.py`、`api/app.py`、Batch manifest/adapter/quality、`goal_runtime.py` 与已有 Viewer 观察工具描述。前端扩展 diff 类型/store/render、原面板、候选/Overlay/图例、Batch 选择器与页面卸载取消。

Mask 验收补足了 `/siblings?tour=true&known=…` 的显式来源别名：服务端只对已点名且白名单核对的相同栅格返回 aliases。前端使用别名后仍逐项核对版本、尺寸、环节和已有角色证书，避免绝对 Scene ID 与库内规范 ID 不同造成误拒。

## 算法与参数

版本 `smart-compare-0.1-experimental`。局部统计在 float64 中计算；NoData/NaN/Inf 通过共同有效集合加权，不把无效位置当 0 DN。方差比为 `Var(NOSR)/max(Var(PAN), epsilon)`；原始 DN 不经过显示拉伸或百分位裁剪。默认窗口 15，允许 3–63 的奇数；第二尺度为 `2*window+1`。同时计算细/粗尺度方差关系和水平、垂直相邻像素粗糙度，组合方差损失与纹理损失作为连续证据。

场景模式扣除正的全局 log 方差比中位数，以共同有效且有纹理、邻域支持充分的像素计算 Q99 归一化尺度（下限 0.25）。返回实际基准与尺度，避免极端值任意拉伸整幅。设置 `calibrationScale` 时使用固定尺度和零 log 比基准；补扫默认固定尺度 1，以保持块间一致。这些是可复现的实验参数，不是生产校准。

灵敏度候选阈值：低 0.70、标准 0.50、高 0.30。沿用最小面积默认 64 个分析像素和已有形态学、区域预算。区域同时返回分位评分、方差比、纹理损失、周边对比、紧凑度、长宽比、方向和条带标记。`difference_only / blur_candidate / joint_confirmed` 为证据类别，`joint_confirmed` 也不代表人工确认；排序沿用强度与面积的联合分值。

已知 JL1KF01/JL1KF02/JL1GF04 与未知传感器均有配置入口，当前都明确使用通用实验参数，未声称拥有生产阈值。频域复核默认关闭；启用时仅对候选窗口做有界 Hann 加窗径向高频能量诊断，不能作为否决条件。

可选 `sceneMasks` 接口支持 cloud/water/shadow 的 `{sceneId, sourceRevision}` 来源引用。只读白名单解析，校验源版本、GeoTIFF CRS/范围/方向及有效像素后才使用；缺失、读失败、配准未证实分别返回 unverified 状态。可用掩码内部保留候选，默认评分乘 0.5 并记录重叠/低可信。当前 UI 的开关控制“可用时降权”；没有自动猜测云水，也未根据未经确认的 MSS 波段构造水体指数。

原始方差比在区域统计中保留；栅格证据提供明确编码的 log2 方差比与纹理损失 PNG，显示编码存在截断范围，不冒充无损原始统计图。评分不是概率，跨影像比较必须使用相同校准及分析尺度。

## API、资源与隔离

沿用 `POST /api/diff/analyze`：`method=smart`，`smartOptions={window,sensor,frequencyReview,sceneSuppression,calibrationScale?}`。`precision=source-grid` 为默认可信源读取；`source-roi` 携带源 A 坐标 `roi={x,y,w,h}`；`source-tiled` 为当前范围原始尺度补扫。显式 `precision=preview` 可传共同网格 PNG，必须提供 `referenceSide=A/B`；服务端强制标成 preview，禁止通过 body.source 冒充原始 DN，且不开放精确 Mask。

两张 source 输入均需 `sceneId/sourceRevision`。读取前后复核源版本与资源身份，核对场景、生产本体/NOSR 文件名、尺寸比例、样本类型及可用 GeoTIFF 配准。无 GeoTIFF 时使用原项目的“已核对同景生产比例网格”约定，明确警告其不是严格空间配准证明。

保持旧响应字段；Smart 追加版本、评分语义、归一化、证据图、场景可用性、传感器来源、区域诊断、精度和来源身份。候选区域 ID 由内容和位置生成，不依赖排行。HTTP 最多展示 200 个候选，自动几何仍遵守旧预算/孔洞/覆盖规则；源坐标投影和精确资格分别记录。

请求体流式限制 48 MiB，PNG 在解码前限制边长 4096，全图 Smart 网格不超过 2048。工作队列沿用现有容量；120 秒请求预算，断连、超时或取消向源网格读取/补扫循环传递协作取消信号。已经进入 SciPy 的单个计算块不会被强杀；队列槽一直保留到工作返回。前端 AbortController 与请求序号/参数签名双重阻止旧结果回填；离开 Viewer 取消在途分析。没有新增服务端结果共享缓存，浏览器仅保留当前结果、当前精检前快照和最多一套三方法对照；重新分析/清除释放引用。

## Agent 与 Batch

复用 `get_current_image_info` 和每条消息冻结的 `differences` 观察：智能对比只提供最多 20 个优先候选及简要证据、选择状态、方法/精度/版本、场景状态和精检状态。没有 PNG 或像素数组进入 LLM。继续使用既有会话绑定、快照预算、工具权限与 ROI 观察；工具描述明确候选非确认缺陷，不得据热力图自动 SR 或修改质量等级。

Batch 导入新增可选 `detection_method=smart`、`smart_options`，前端既有批量草案可选方法；不选择时仍是原 AbsDiff 默认。参数、算法版本和结果摘要保存在原冻结 manifest/checkpoint 中，旧批次无新字段仍可恢复。Smart 强制采用已有 review 策略，候选几何要求人工复核后才可提交；产物沿用原人工质量审核。实验 Smart 的“未检出候选”不能替代质量通过，最终质量守卫会明确拒绝自动结案；需要人工核对或已校准规则后再处理该类批次。没有更改 Agent Loop、Executor 或 SR 生产算法。

## 验证与实测

本地 Windows / Python 3.14.3，测试隔离环境 `.venv-smart`。合成 TIFF 来自 NumPy/Pillow/tifffile，未使用内网真实影像或 GDAL/GPU。

- 前端全量 93 个测试文件、1047 项通过；新增显示像素测试后相关 64 项通过，类型检查通过，生产构建通过。已有 vendor sourcemap 缺失与大 bundle 提示仍存在。
- 后端相关算法、HTTP、源读取、Mask、Batch manifest/worker/quality、Viewer 观察和配对回归共 191 项通过；后续新增超时/别名守卫的结果见本轮最终运行记录。
- 新算法合成覆盖同图、局部高斯模糊、median 非线性平滑、亮度/对比度、锐化、噪声/椒盐、低纹理水域、云/阴影/条带、高 DN、无效像素、常值与角色反转。除明示的定位/低误报断言外，其他场景仅验证数值稳定与实验状态，不宣称生产召回率。
- HTTP 验证三方法兼容、source-grid/source-roi/source-tiled、三算法共享读取、版本漂移、输入预算和参数拒绝。原始补扫跨 512 块接缝与单块评分差异在 1 个显示字节内。
- 真实 HTTP + 构建后 Viewer 浏览器验收：计算得到候选 → 原图精检 → 加入可编辑 Mask → 整批撤销 → 三算法对照；显示阈值和不透明度不重发分析请求。只使用合成 TIFF，未执行 SR。

性能脚本 `.e2e/benchmark-smart-compare.py` 包含 PNG 编码与自动几何生成，5ms 采样进程 RSS（绝非精确堆内存审计）；输入数组预先生成，不含盘阵 I/O/HTTP/浏览器：

| 分析网格 | 实测耗时 ms | 基线 RSS MiB | 采样峰值 RSS MiB | 增量 MiB |
| --- | ---: | ---: | ---: | ---: |
| 512² | 206.71 | 65.55 | 105.05 | 39.50 |
| 1024² | 691.78 | 78.64 | 242.94 | 164.29 |
| 2048² | 2568.58 | 103.04 | 789.18 | 686.14 |

没有给出性能提升百分比；真实大 TIFF、盘阵吞吐与生产服务进程峰值内存仍需内网实测。

复现：先安装现有后端依赖及测试用 pytest/tifffile/httpx/psutil；运行 `python -m pytest backend/tests/test_blur_detection.py backend/tests/test_smart_compare.py backend/tests/test_smart_compare_api.py -q`。前端执行 `npm test`、`npm run typecheck`、`npm run build`。浏览器验收先从仓库根运行 `python .e2e/smart-compare-server.py`，再运行 `node .e2e/test-smart-compare.js`；该服务仅创建临时合成场景与 Fake 调度环境。性能执行 `python .e2e/benchmark-smart-compare.py`。

## 内网待验与局限

- 核对真实 PAN/NOSR 配准、非整数分辨率、源 DN 定标、云水阴影来源与多个传感器的阈值校准；正常去噪/重采样/超分也可能出现纹理损失。
- 全局中位数基准可能压低整景一致模糊；固定尺度也尚未生产校准。2048 网格可能漏掉细小异常，补扫受明确块数预算限制，不是无限制整景原始尺度扫描。
- Native ROI 沿用既有无压缩单波段条带 TIFF 读取范围；压缩、tiled、GDAL 专用编码与真实大文件未新增全量解码兜底。读取失败明确报错，不能称为“无异常”。
- 验证内网机器的 Canvas、缩放/分屏/交替/鹰眼、颜色可读性、真实并发取消与任务恢复；本地已有相关回归不等于内网全部验收。
- 完成真实单景 SR 后的候选 Mask—成果—人工逐区域审核联验。本轮未运行生产 SR，未修改 req/meta/qual XML，也没有生产准确率结论。
