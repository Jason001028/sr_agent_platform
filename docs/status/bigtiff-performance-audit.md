# 4 GB BigTIFF 性能复核

审查起点为 `ab353c5`，包括 P1 `9e783e6` 和 P0 `efff3fa`。审查结果提交为 `3f393e4`（已同步到 `origin/main`），完整差异用 `git diff ab353c5 3f393e4` 查看。

## 问题与修改

| 优先级 | 问题 | 修改位置和依据 |
| --- | --- | --- |
| P0 | 旧粗 PNG 概览宽度只有 387 px，原生按钮需放大约 128 倍，64 倍上限会截断。概览宽高独立取整时，两轴的设备像素映射也略有不同。 | [viewer.ts](../../frontend/src/stores/viewer.ts) 根据源与概览的比例调整适配和缩放上限。[viewMath.ts](../../frontend/src/lib/viewMath.ts) 保留视口中心的源坐标，在原生档让两轴各对应一个设备像素。调度与渲染采用同一像素边界。 |
| P1 | 无 Overview 的 512×512 Tiled TIFF 以 L7 建立概览，128 倍采样仍触及许多完整数据块。DEFLATE 的 RasterIO 用了 3.70 s，拉伸与 PNG 编码约 8 ms。 | [tileSource.ts](../../frontend/src/lib/tileSource.ts) 依据内部块尺寸选择更稀疏的首屏锚点。[raster_window.py](../../backend/services/raster_window.py) 仅在无压缩 Tiled、基带最近邻且采样步长达到块尺寸时启用 GDAL 直接读取。保留 Strip JPEG 快路径。 |
| P1 | 不透明目标图块覆盖后底图仍重绘，调度器每帧重新筛缓存，鹰眼反复缩放相同底图。旧高速判定每帧需移动 100 CSS px。 | [tileRenderer.ts](../../frontend/src/lib/tileRenderer.ts) 缓存图块边界，完整覆盖时跳过父级查找与底图绘制。超过 1,000 CSS px/s 的平移简化新图块淡入。[tileScheduler.ts](../../frontend/src/lib/tileScheduler.ts) 在资产版本集合变化时清理缓存，仅排序缺失请求。[MiniNavigator.vue](../../frontend/src/components/MiniNavigator.vue) 缓存鹰眼尺寸底图。 |
| P1 | 仅本地可读的大 TIFF 在解析不到源时可能隐式上传，预览也允许申请较大的缓冲。 | [viewer.ts](../../frontend/src/stores/viewer.ts) 优先关联已验证源。未匹配且达到 1 GiB 的本地文件走 P0 渐进预览，最长边 4,096 px。[local_preview.ts](../../frontend/src/lib/local_preview.ts) 复用同一 File 的三段指纹计算。 |

## 样本与计时范围

Windows 11 Pro 26200，Ryzen 7 9700X，约 32 GB 内存，WD_BLACK SN7100 SSD，RTX 5070 与 Radeon 核显。Chrome 158.0.8127.36 和 Edge 158.0.4370.33 启用 GPU 合成，以无头模式运行，视口 1440×880 CSS px。物理显示器报告 160 Hz，测试约以 60 次/s 注入输入。帧间隔来自实际 Viewer Canvas 重绘，指针延迟也以 Canvas 调用为终点。该模式未测量屏幕实际呈现或 GPU 执行时长。服务端为 Python 3.12.14、NumPy 2.5.4、GDAL 3.11.4。

样本为 49475×49454 单波段 UInt16，DN 约 4.56 GiB。图像由确定性街区、屋顶、斜路、单像素线条和纹理构成。无压缩 Strip 为 4,893,547,736 B，无压缩 Tiled 为 4,933,138,972 B，DEFLATE 主文件为 85,229,369 B。第四份数据内容与 DEFLATE 相同，另有事先生成的 64 倍与 128 倍 Overview。Windows 进程 I/O 计数包含文件系统缓存命中，测量期间没有清除 OS 页缓存。区分了应用缓存首次请求、同进程重开和浏览器缓存状态。物理盘冷读与真实盘阵仍需测量。

## 首屏

T0 为开始打开场景，T1 为资产 API 返回，T2 为 JPEG 概览请求发出，粗 PNG 请求另记为 `bootstrap`，T3 为首个非零 Alpha 图像绘制。T4/T5 为目标图块开始请求/首个 ImageBitmap 绘制，T6 为目标图块完整覆盖且淡入结束。旧构建将粗 PNG 计入 T4/T5，且只把 387 px 概览显示为 387 CSS px。新构建填满视口内可用的 832 CSS px 高度，目标 LOD 和覆盖面积有差别。

| 布局 | 首开 T3，P2 → 本次 | 重开 T3，P2 → 本次 | 首开 T6，P2 → 本次 |
| --- | ---: | ---: | ---: |
| Strip / JPEG 概览 | 930 → 727 ms | 292 → 236 ms | 1,137 → 918 ms |
| 无 Overview 的未压缩 Tiled | 853 → 260 ms | 57 → 41 ms | 1,023 → 1,015 ms |
| 无 Overview 的 DEFLATE | 3,769 → 1,170 ms | 42 → 36 ms | 3,933 → 3,339 ms |
| 已有 Overview 的 DEFLATE | 60 → 75 ms | 36 → 49 ms | 229 → 261 ms |

首屏对照使用 Chrome DPR 1。DEFLATE 的新 T1/粗请求/T3/T4/T5/T6 分别为 29/123/1,170/1,116/1,994/3,339 ms。应用先提交目标请求，再绘制占位锚点。HTTP 样本记录发送到收到响应头的时间，Bitmap 样本记录解码等待，服务端另按源统计、RasterIO 和拉伸编码计时。粗图沿用 Tile API 的源身份和拉伸规则。

以新进程测量同一无压缩 Tiled L10，开启按需直接读取后，RasterIO 从约 210 ms 降至 26 ms，进程读取字节从约 1.27 GB 降至 4.64 MB。DEFLATE 从 L7 降到 L10 后，RasterIO 从约 3,700 ms 降至 918 ms，读取从约 87.1 MB 降为 16.5 MB。L10 锚点为 49×49 px，仅在没有 Overview 的大 Tiled 源上使用。

## 原生拖拽

Puppeteer 在视口内持续按住鼠标，按真实经过时间计算 130 CSS px 内往复轨迹。基线每组 1 轮，复测每组 2 轮，每种速度至少 10 秒。表中按 200 / 800 / 1,600 CSS px/s 排列，复测每项保留两轮范围。

| 浏览器 / DPR | P2 帧间隔 P95（ms） | 本次帧间隔 P95（ms） | Canvas drawImage 提交次数 |
| --- | --- | --- | ---: |
| Chrome / 1 | 27.7 / 27.3 / 27.9 | 27.0–27.8 / 28.2–28.6 / 26.7–28.0 | −10.8% |
| Chrome / 2 | 28.2 / 27.5 / 28.6 | 28.0–30.2 / 25.9–28.7 / 26.4–30.2 | −6.0% |
| Edge / 1 | 26.8 / 30.3 / 28.7 | 27.6–30.0 / 27.4–29.6 / 28.6–29.1 | −10.4% |
| Edge / 2 | 27.5 / 29.2 / 29.1 | 28.3–28.8 / 27.3–30.7 / 27.2–29.2 | −5.9% |

复测为 59.9–60.4 次 Viewer Canvas 更新/s，指针到下一次画布绘制 P95 为 6.2–6.7 ms，24 轮最终平移误差均为 0 CSS px，没有记录到 50 ms 以上的长任务。130 px 的原生轨迹完全命中可视图块，主要测量热拖拽。DPR 1 与 2 的主 Canvas 背板为 1014×832 与 2028×1664，RGBA 约 3.22 MiB 与 12.87 MiB。Edge DPR 2 的全图、中间倍率、双图同步和 ROI 各测了三种速度，P95 为 26.9–29.4 ms，双图两侧最终位移均无漂移。自动巡览另跑 10 秒。

每轮拖拽注入 601 次 PointerMove，同一区间 Viewer Canvas 重绘 601–610 帧（计时器记录的间隔为 600–609 个，帧/事件 1.000–1.015，最多把 1 帧的两次移动合并），即没有丢帧；帧间隔中位 15.2–15.4 ms、均值约 16.6 ms（60.0–60.8 fps）。P95 27–31 ms 来自注入节拍本身：Node 定时器与 CDP 往返使部分相邻事件间隔变长，而渲染是输入驱动的，下一帧跟着事件走。同一区间 `drawImage` 提交耗时 P95 为 0 ms（2D 光栅化转交合成器），长任务为 0，指针到下一帧 P95 6.2–6.7 ms，可见图块覆盖率 100%，取消请求为 0，新增图块请求最多 18 个（图块进入视野时）。因此在本测量方式下 16.7 ms 的帧间隔 P95 不可达：要让它低于 16.7 ms，只能让应用脱离输入自行跑 rAF，而任务书明确排除了空闲 rAF 采样。能证明流畅的指标（逐事件出帧、无长任务、输入延迟）全部满足。另有一组补充的原生拖拽（601 事件 / 605–610 帧，期间新增 12–18 个图块请求），结论相同。历史 P2 的 18.7 ms 来自更短的采样，持续拖拽对照没有复现该帧间隔。

## 内存与生命周期

Edge DPR 2 下把四种布局连续开关 4 轮，每轮固定做 18 次跨视口跳转并等 L0 覆盖。四张同时打开时 Bitmap 缓存峰值 134,217,728 B，正好是 128 MiB 预算；服务端缓存图块从 9.2 MB 长到 14.1 MB 后不再增长。每轮 `clearRecs` 后 `liveBitmapBytes`、预览输入与画布字节都回到 0，事件监听器回到 121、DOM 节点回到 408，说明调度器、预览画布和浏览器侧图块都能释放。轮与轮之间 `JSHeapUsedSize` 为 5.6 → 6.4 → 7.0 → 7.4 → 7.9 MB，renderer RSS 为 403 → 411 → 416 → 421 → 426 MB，每轮约 +0.5 MB / +5.5 MB，没有加速增长，更像分配器保留页而不是按图累积的引用；但 4 轮不足以宣称完全无泄漏。后端 RSS 稳定在 109–110 MB，未把整幅 TIFF 读进内存。

## DN、画质与显示

四种布局各用 HTTP `/raw-window` 对 6 个窗口的 1,417,480 个 UInt16 DN 逐值比较，覆盖左上、内部、条带和图块边界、四 Tile 交点及奇数右下角。DN 与生成公式完全一致。各布局的 L0、L1、L2 PNG 均与独立计算的全源拉伸和采样相符，右下角 PNG 为 323×302。相同区域的灰度 MAE（0–255）为 L0 0、L1 2.22、L2 4.75。旧全图 JPEG 沿用 P0 的拉伸和采样，实际与 L0 的 MAE 包含两个处理差异，另提供使用统一 LUT 的对照。

Chrome/Edge × DPR 1/2 在 Strip 源上通过 64×64 设备像素逐点比对。采样点在源坐标 (24576, 24576) 的四 Tile 交点，Canvas 与独立拼装的 P1 PNG 参考差异为 0。Chrome DPR 1 另外跑了其他三种布局。L0 的 1,048,576 B RGBA 纹理预算、262,144 B 灰度缓冲和 524,288 B UInt16 DN 各自计量。

Chrome 的实际文件句柄拖入中，同名已核验源在约 1.06 s 后进入 Tile 模式（`serverSourceVerified`，上传 0 B）。未映射的短文件名 `loose.tif` 走本地 P0 Worker：约 137 ms 提供渐进预览状态，约 1.78 s 生成 4096×4094 概览，全程不进入 Tile 模式。浏览器主线程观测到的最大 `Blob.arrayBuffer` 为 4 KiB，两条路径上传均为 0 B。中止本地解码后打开映射源时，取消条目未发布预览；连续拖入本地大图与映射大图时，被替换的旧条目同样没有发布半成品。

## 产物

基准 JSON、原生截图与 DN 校验在 `.e2e/fixtures/raster-audit-results/`：`before-*` / `after-*` 对应 P2 基线与本轮，`after-lifecycle.json` 是四轮开关内存，`large-drop.json` 是文件拖入，`after-edge-2-scenarios.json` / `after-edge-2-wide.json` 是 Edge DPR 2 的其余场景与宽扫。样本 TIFF 在 `.e2e/fixtures/raster-audit/`，不入库。

## 尚未验证与后续

- 真实盘阵、冷 OS 页缓存与 CentOS 7 的 GDAL 环境没有复测。本轮 Windows 进程 I/O 计数含文件系统缓存命中；DEFLATE 首开耗时主要来自 CPU 解压，不是冷盘读取，盘阵上的绝对值会不同。
- 无头模式记录的是 Canvas 提交节拍，未覆盖物理显示器 vsync、GPU 执行与屏幕实际呈现；上文的帧间隔结论要按「逐事件出帧」解读。
- 无 Overview 的 DEFLATE 首开 T6 仍为 3.34 s：目标 LOD 的 4 个 L6 象限合计仍要把整幅约 4.9 GB 的压缩数据解一遍（步长 64 小于 512 的块，每个块都要解码），而服务端图块线程池默认 2 线程（`SR_TILE_WORKERS` 可调到 4）。请求同时发出，墙钟却被 2 线程摊成两批；调高线程数是最可能缩短 T6 的下一步，但本轮没有复测，不能当作已验证收益。
- 服务端 `RasterWindowReader._open` 固定了 `NUM_THREADS=1`。GTiff 的多线程块解码本可把单次大窗口解压并行化，但显式 open option 会压过 `GDAL_NUM_THREADS`；要做对照实验得先把它改成可配置，再比较 T3/T6、CPU 与并发请求下的相互影响。本轮没有改。
- 任务书引用的 4.41 s 与本地 P2 基线 3,769 / 3,933 ms 不同（数据、机器与计时口径不同），本文一律以本地复测为准。
- 约 7,000 设备像素宽以上（如 8K 视口）在「原生」档会因 `maxVisible` 上限（96 个可见图块）自动退到更粗 LOD；本机 1440×880 × DPR 2（2880×1760 设备像素、24 个可见图块）不受影响。

## 复跑

命令从仓库根目录执行。需要已有 GDAL Python 环境、Pillow、NumPy、psutil，以及已安装依赖的前端和 .e2e。离线安装沿用 [P1 部署说明](raster-window-p1.md)。生成样本需约 10 GB 磁盘。各行分别执行。

```powershell
.venv/Scripts/python.exe -m scripts.audit_raster .e2e/fixtures/raster-audit --generate
npm.cmd --prefix frontend run build
$env:SR_E2E_TILE_SOURCE=(Resolve-Path .e2e/fixtures/raster-audit/strip.tif).Path; $env:SR_E2E_IMAGE_CACHE=(Join-Path (Get-Location) '.e2e/fixtures/raster-audit-results/cache'); $env:SR_E2E_AUDIT_RESOLVE='1'; .venv/Scripts/python.exe .e2e/serve-image-open-api.py
node .e2e/serve-local-load.mjs
$env:CHROME_PATH='C:/Program Files/Google/Chrome/Application/chrome.exe'; $env:TILE_DPR='1'; $env:AUDIT_PYTHON=(Resolve-Path .venv/Scripts/python.exe).Path; $env:AUDIT_ROUNDS='2'; $env:AUDIT_VERIFY_NATIVE='1'; node .e2e/audit-tile-drag.js
.venv/Scripts/python.exe -m scripts.audit_raster .e2e/fixtures/raster-audit --layout tiled --level 10
.venv/Scripts/python.exe -m scripts.audit_raster .e2e/fixtures/raster-audit --layout strip --api http://127.0.0.1:8766
```

Edge 改用本机 `msedge.exe`。分别以 `TILE_DPR=1` 和 `2` 测量。以 `AUDIT_TAG` 标识输出文件，`AUDIT_LAYOUTS` 选择样本，`AUDIT_MODES=fit,mid,split,roi,tour` 跑场景，`AUDIT_LIFECYCLE` 控制切换和清空轮数。可选的 `AUDIT_PYTHON` 用于记录 Chromium 进程 RSS。CentOS 7 可以用其专用 GDAL 环境执行同一 Python 诊断入口。浏览器可使用 `AUDIT_API` 与 `AUDIT_FIXTURE` 指向对应测试入口。真实数据应采用只读源和独立测试缓存。文件拖入另跑 `node .e2e/audit-large-drop.js <同名映射 TIFF> <改名副本>`，用 `AUDIT_DROP_RESULT` 落盘；对照无金字塔 T6 时可在服务端设 `SR_TILE_WORKERS=4`。
