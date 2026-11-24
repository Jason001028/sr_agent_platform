# 4 GB BigTIFF 性能复核

审查起点为 `ab353c5`，包括 P1 `9e783e6` 和 P0 `efff3fa`。开始时工作区干净，本地 HEAD 相对当时的 `origin/main` 领先 2 个提交。完整差异可用 `git diff ab353c5 HEAD` 查看。

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

复测为 59.9–60.4 次 Viewer Canvas 更新/s，指针到下一次画布绘制 P95 为 6.2–6.7 ms，24 轮最终平移误差均为 0 CSS px，没有记录到 50 ms 以上的长任务。130 px 的原生轨迹完全命中可视图块，主要测量热拖拽。DPR 1 与 2 的主 Canvas 背板为 1014×832 与 2028×1664，RGBA 约 3.22 MiB 与 12.87 MiB。Edge DPR 2 的全图、中间倍率、双图同步和 ROI 各测了三种速度，P95 为 26.9–29.4 ms，双图两侧最终位移均无漂移。自动巡览另跑 10 秒。历史 P2 的 18.7 ms 来自更短的采样，持续拖拽对照没有复现该帧间隔，也未达到 16.7 ms 目标。

## DN、画质与显示

四种布局各用 HTTP `/raw-window` 对 6 个窗口的 1,417,480 个 UInt16 DN 逐值比较，覆盖左上、内部、条带和图块边界、四 Tile 交点及奇数右下角。DN 与生成公式完全一致。各布局的 L0、L1、L2 PNG 均与独立计算的全源拉伸和采样相符，右下角 PNG 为 323×302。相同区域的灰度 MAE（0–255）为 L0 0、L1 2.22、L2 4.75。旧全图 JPEG 沿用 P0 的拉伸和采样，实际与 L0 的 MAE 包含两个处理差异，另提供使用统一 LUT 的对照。

Chrome/Edge × DPR 1/2 在 Strip 源上通过 64×64 设备像素逐点比对。采样点在源坐标 (24576, 24576) 的四 Tile 交点，Canvas 与独立拼装的 P1 PNG 参考差异为 0。Chrome DPR 1 另外跑了其他三种布局。L0 的 1,048,576 B RGBA 纹理预算、262,144 B 灰度缓冲和 524,288 B UInt16 DN 各自计量。

Chrome 的实际文件句柄拖入中，同名已核验源进入 Tile 模式。未映射的短文件名 `loose.tif` 走本地 P0 Worker，约 141 ms 提供渐进预览状态，约 1.59 s 生成 4096×4094 概览。浏览器主线程观测到的最大 Blob.arrayBuffer 为 4 KiB，两条路径上传均为 0 B。中止本地解码后打开映射源，取消条目未发布预览。

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

Edge 改用本机 `msedge.exe`。分别以 `TILE_DPR=1` 和 `2` 测量。以 `AUDIT_TAG` 标识输出文件，`AUDIT_LAYOUTS` 选择样本，`AUDIT_MODES=fit,mid,split,roi,tour` 跑场景，`AUDIT_LIFECYCLE` 控制切换和清空轮数。可选的 `AUDIT_PYTHON` 用于记录 Chromium 进程 RSS。CentOS 7 可以用其专用 GDAL 环境执行同一 Python 诊断入口。浏览器可使用 `AUDIT_API` 与 `AUDIT_FIXTURE` 指向对应测试入口。真实数据应采用只读源和独立测试缓存。
