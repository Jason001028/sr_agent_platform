# P2.2 Tile 画质保真

实现从 `36e975d` 开始。原始审计在开发工作区可读，其中的固定 ROI 与数据已用于复现。此次提交只包含修复、回归、可复跑脚本和本次证据。

## 实现与兼容

- Viewer 的默认均衡、P2–P98、手动黑白场及曲线均读取原生 DN 对应的 PNG Tile。主画布在切换色调时只绘制匹配当前配置的目标块或候补层。手势期间最多每轮稳定输入切换一次配置，过期请求取消并核对响应。保留原 P0 JPEG 分析缩略图，巡览、ROI、批注与掩码使用原有坐标和业务路径。Tile 渲染器按需取同色调的全源粗层。普通 JPEG、未映射 TIFF 和旧服务端沿用原有路径。 [Viewer 拉伸](../../frontend/src/stores/viewer.ts) · [画布与候补](../../frontend/src/components/TifCanvas.vue) · [TileRenderer](../../frontend/src/lib/tileRenderer.ts)
- 手动 UInt16 窗按归一化配置复用 65536 项 LUT。默认均衡按源与波段缓存 65536 桶 DN 统计和累计映射。用确定性空间分层取样，样本数最多 65536，最多计 128 次 TIFF 块触及，并以 128 MiB 解码块体积约束。采样不足或近常量先扩样，仍低置信度时使用统一回退窗口，只有预算允许完整读取且有效值一致时才判常量。有限哨兵和非有限值统一剔除。只有显式同景且 DN 类型、反相、比例、偏移与单位可比时，才共享手动黑白场。 [色调服务](../../backend/services/raster_tone.py) · [Tile API](../../backend/services/image_tiles.py)
- L1/L2 可请求 Area，直接在基带域分别对齐 2×2、4×4 原点网格，排除 NoData，按边缘实际有效像元数归一化。生产默认最近邻。已标记的类别、调色板和 1-bit 数据继续最近邻，未标记的分类栅格应使用默认策略。Area 在目前缺少生成核/NoData 可验证元数据的情况下读取基带。L0 保持原生直读，`/raw-window` 返回原始类型与 DN。 [RasterIO](../../backend/services/raster_window.py)
- P0 Pillow 灰度兜底在原始 DN 上进行采样，之后再映射为 8-bit JPEG。UInt16 经 Pillow 的 Float32 Lanczos 时所有整数 DN 都可精确表示。P0 长边、面积预算及稀疏 Overview 路径保留。旧 JPEG 规则版本升级为 v5，防止命中旧饱和结果。 [Preview JPEG](../../backend/services/preview_jpg.py)

`GET /api/image-open/assets/{id}/tiles/{level}/{x}/{y}` 接受 `sampling=nearest|area`、`display=equal|linear|linear2|sqrt|log|window:<black>:<white>`，以及用于固定版本的可选 `stretchVersion`。Curve 可追加归一化坐标对。PNG 元数据返回 `displayKey`、`stretchVersion`、`requestedSampling`、`resampling`、`rasterSources` 和 `rasterVersion`，用于区分请求策略与实际读取来源。Raster 修订、显示、采样和算法版本进入缓存身份与 ETag。`/tiles/stats` 返回全源统计、有效样本数、采样范围和置信状态。ROI 自动生成并锁定 LUT 是可选项，本次提供全源统计和手动窗。

## 同一 ROI 的实测

Windows 本地盘，GDAL 3.11.5，Chromium 157。合成 **49475×49454 UInt16 BigTIFF** 为无压缩 32 行条带。使用原审计的 ROI 原点 **(24576,24576)**，宽高 1024，加入等相位与反相位细纹理、周期真横条带、单像元道路和跨 L0 Tile 边界的弱信号。真 PAN 未提供。

| 路径 | 细道连续率 | 真条带幅值 | 两种相位的假条带幅值 |
|---|---:|---:|---:|
| L1 最近邻 | 0% | 4000 DN | 4000 / 4000 DN |
| L1 Area | 100% | 4000 DN | 0 / 0 DN |
| L2 最近邻 | 100% | 4000 DN | 4000 / 4000 DN |
| L2 Area | 100% | 4000 DN | 0 / 0 DN |

面积平均的 L2 道路虽过阈值，细线幅值仍会随足迹平均衰减。L2 最近邻的成功来自固定相位命中。两种策略用同 ROI、同格网、同色调和 PNG。Area 与一次性参考平均逐值一致。该组中 Area 已消除构造的假条带，因此没有加 Gaussian。 [采样对照图](tile-fidelity-p22/sampling-comparison.png)

同一个跨块弱纹理 ROI 在默认均衡及 P2–P98 下都显示成七个 255。手动 DN 窄窗 21700–21706 时，浏览器完整 L0 的七个原生像素分别为 **0、42、85、128、170、212、255**，alpha 均为 255。实际拖动、LOD 往返、过期响应和双图共享使用同一套程序路径。 [原始 UInt16 ROI](tile-fidelity-p22/raw-roi.tif) · [色调对照](tile-fidelity-p22/tone-comparison.png) · [默认均衡](tile-fidelity-p22/viewer-before-default.png) · [手动黑白场](tile-fidelity-p22/viewer-after-manual.png)

### 读取成本

下表是同一个试验进程首次生成目标 PNG 的调用数据，色调统计已单独测量。OS 页缓存未清空，读取字节为 Windows 进程 I/O 计数，可包含文件系统缓存或元数据。热请求各重复 7 次。 [后台原始数据](tile-fidelity-p22/backend.json)

| 路径 | 首次生成 ms | 读取字节 | 热命中 ms |
|---|---:|---:|---:|
| L1 最近邻 | 77.93 | 1556608 | 0.014 |
| L1 Area | 123.61 | 2097152 | 0.019 |
| L2 最近邻 | 27.82 | 2094080 | 0.024 |
| L2 Area | 216.94 | 8388608 | 0.021 |

后台峰值约 100.1 MiB。固定的四组冷热序列命中 **28/32（87.5%）**，热命中读取为 0。初次源统计采了 8192 个有效 DN，覆盖 42 个物理条带，保守解码块体积 132988800 B，实际进程读取 16384 B，约 63.7 ms。Area 的额外读取成本是生产继续使用最近邻的一个原因。

实际 Viewer 从打开到首个完整原生视口约 **1885 ms**，点击原生后约 **328 ms**，连续平移 120 帧的 rAF 间隔 P95 **16.7 ms**。Tile 位图缓存峰值约 **24.6 MiB**。测试人为迟发 6 个旧色调响应，响应释放后仍保持上述七级灰阶。rAF 测量截至浏览器背板，采样未测盘阵冷盘和显示器呈现延迟。 [浏览器原始数据](tile-fidelity-p22/browser.json)

## 复跑

从仓库根目录分别执行以下单行。Python 使用 GDAL、NumPy、Pillow、tifffile、psutil 和现有 API 依赖环境。生成的全图约 4.9 GB，需独立测试盘空间。

```powershell
python -m scripts.benchmark_tile_fidelity .e2e/fixtures/p22/strip-p22.tif docs/status/tile-fidelity-p22 --generate
$env:SR_E2E_TILE_SOURCE=(Resolve-Path .e2e/fixtures/p22/strip-p22.tif).Path; $env:SR_E2E_IMAGE_CACHE=(Join-Path (Get-Location) '.e2e/fixtures/p22-cache'); python .e2e/serve-image-open-api.py
node .e2e/serve-local-load.mjs
node .e2e/test-tile-fidelity-p22.js docs/status/tile-fidelity-p22
python -m pytest backend/tests/test_image_tiles.py backend/tests/test_preview_jpg.py backend/tests/test_preview_overview.py backend/tests/test_preview_plan.py backend/tests/test_image_open.py -q
npm --prefix frontend test -- --maxWorkers=2 src/lib/__tests__/tileViewer.test.ts src/lib/__tests__/viewMath.test.ts src/lib/__tests__/jpegBitmap.test.ts src/stores/__tests__/histogramStretch.test.ts
```

后端 **88 项及 4 个子测试通过**，前端重点覆盖 **80 项通过**，类型检查及生产夹具构建通过。前端全量 **1244 项通过**，确认的既有失败为 `AgentToolPresentation`。DPR 1、2、3 分辨率回归通过。已有 Edge Tile 回归通过，覆盖原生接缝、迟到及坏块恢复、双格、交替巡览移动、ROI 撤销重做和缓存清理。真实 PAN、生产 Overview 元数据和冷盘读取仍需部署环境验证。
