# P2 多尺度 Tile Viewer

Viewer 已接入 P1 的资产、栅格版本和 512px PNG。支持 Tile 的单波段 TIFF 可用工具栏「原生 1:1」直接定位到 L0，也可在任意缩放下滚轮查看局部。约 50K 的资产优先取 1/16 JPEG（合成样例 3092×3091），从画布首次出现概览就可操作。适配窗口、平移和缩放沿用现有视口规则。

## 实现

- `tileSource.ts` 用已有 `PreviewAsset` 验证实际 GDAL 元数据与响应，使用源身份、栅格版本、波段和拉伸版本作为浏览器缓存前缀。JPEG 全图回退尺寸超限时，可用单块覆盖全幅的粗级 PNG 建立概览。
- `tileScheduler.ts` 使用一个双格共享队列，最多 6 个 HTTP 请求。视口中心优先，其次是可见图块、移动方向和周边一圈。相同图块合并，离开工作集的请求可取消。可见块短暂失败后补试一次，底图持续显示。
- `tileRenderer.ts` 在视口 Canvas 中合成 JPEG、已有 LOD 和目标 LOD。按设备像素选择层级，在层级边界使用滞回。绘制从共同原图边界计算设备像素断点，奇数边缘最后一个采样有独立的裁剪宽高。Canvas 使用视口尺寸，背板 DPR 上限为 2。
- 首次概览约 160ms 显影。Tile 用 160ms 的局部 ease-out Alpha，跨 LOD 约 200ms，平移补图约 120ms。高速平移、热缓存、交替对比及减少动态效果设置可直接呈现。父层保留至当前目标覆盖稳定，过渡共用一次帧时钟，空闲时停止。
- Bitmap LRU 双格共享 128 MiB，按 RGBA 解码体积计费。目标及正在绘制的父层优先保留，进入超大视口时限制候选工作集。淘汰、版本更换、移除影像和卸载均释放不再引用的 Bitmap。JPEG 概览保留在现有 Viewer 状态中，单独记录其解码画布占用。
- `TifCanvas.vue` 以现有 Pane/ViewState 驱动图像帧。ROI、掩码、批注、差异叠加、鹰眼和巡览继续使用原坐标和业务状态。交替对比同时为两张已就绪的影像准备各自图块。自定义直方图拉伸和纯差异热图继续使用已有显示链路。

## 验收

用真实 P1 API 和生产构建的 Viewer 组件，加载 49475×49454 UInt16 BigTIFF Strip。操作包含全图、原生 1:1、连续跨 LOD 滚轮、平移、奇数右下角和双图切换。图块验收注入 90–210ms 的错序响应延迟和一张损坏 PNG，检查已有画面保留、失败后补图以及旧请求取消。原生比例下，跨图块边界两侧的画布像素与直接请求 P1 PNG 的像素逐点比较。

| 证据 | 链接 |
| --- | --- |
| Chrome 全图和原生 1:1 | [概览](../../.e2e/artifacts/tile-viewer/chrome-overview.png) · [原生](../../.e2e/artifacts/tile-viewer/chrome-native.png) |
| Chrome 奇数右下角 | [边缘](../../.e2e/artifacts/tile-viewer/chrome-edge.png) |
| Edge 双格，DPR 2 | [双格](../../.e2e/artifacts/tile-viewer/edge-dual.png) |
| 浏览器性能原始报告 | [Chrome](../../.e2e/artifacts/tile-viewer/chrome.json) · [Edge](../../.e2e/artifacts/tile-viewer/edge.json) |

### 性能

Windows 本机，Chrome/Edge 158，浏览器视口 1440×880 CSS 像素，服务端 JPEG 缓存已热。首次概览和第一块 Tile 从开始打开场景计时，L0 指标从点击原生 1:1 计时。Tile HTTP P95 取实际响应时间，测试注入的等待单独计入异常恢复时段。

| 指标 | Chrome，DPR 1 | Edge，DPR 2 |
| --- | ---: | ---: |
| 首次概览可见 | 873.2 ms | 942.8 ms |
| 第一块 Tile 开始显影 | 876.1 ms | 952.0 ms |
| 第一块 L0 可见 | 46.6 ms | 38.9 ms |
| 当前视口 L0 完全覆盖 | 270.9 ms | 351.7 ms |
| 错序加单块解码失败后的完全覆盖 | 1075.7 ms | 1107.2 ms |
| Tile HTTP P95 | 21.3 ms | 32.4 ms |
| 交互帧间隔 P95 | 6.3 ms | 18.7 ms |
| 51 个采样中超过 25 ms 的帧 | 0 | 0 |
| Tile Bitmap 缓存峰值 | 71.4 MiB | 128.0 MiB |
| 双格时 Tile Bitmap / 两份概览画布 | 66.9 / 41.5 MiB | 127.0 / 41.5 MiB |
| 删去第二个源时关闭的 Bitmap | 11 | 16 |

两浏览器的平移中心采样均无透明空帧，原生接缝两侧显示值均为 11 和 12，与 P1 PNG 一致。两浏览器均通过 ROI 撤销重做及 PAN/NOSR 合成配对的交替与巡览。DPR 2 的双格缓存接近 128 MiB，测得的回补时间也更长。帧间隔来自无头浏览器的交互 rAF 采样，显示器实际呈现帧率仍需现场测量。

同尺寸的无金字塔 DEFLATE Tiled 合成影像首次走粗级 PNG 回退，在 Chrome 约 4.41 秒建立 387×387 概览。缺少可复用 Overview 时，低倍全幅 RasterIO 仍需读取较多基础块。

Chrome 的空间批注及既有影像打开流程浏览器回归通过，覆盖双格、标记跟随、ROI、DPR 2、分段上传及预览切档。后端窗口、影像打开及 PreviewPlan 相关测试 36 项通过。前端类型检查、生产构建通过，全量测试 1239 项通过，`AgentToolPresentation` 保留已知基线失败。旧 `test-vue-viewer.js` 的稀疏解码路线断言及旧 `test-tour-compare.js` 的加载配对超时，均在独立 P1 工作树复现。

### 已修正的接入问题

- 实际 P1 栅格 JSON 用 `band_count`，前端旧类型声明写的是 `bandCount`。修正后，图库入口能启用真 Tile，并增加了契约回归。
- 将图像重绘合并到帧时钟时，旧多边形预绘制失去了 Vue 更新后的即时反馈。覆盖层恢复独立重绘，Tile 显影帧只重绘图像。
- 小图图库继续使用原有预载与 JPEG 缓存，兼容版本回退保留原始入口。图块完成后重新核对栅格版本，晚到的旧 Bitmap 直接关闭。
- 移除未显示的卡片可能没有视口重绘信号。现在独立监听有效源列表，下一帧关闭该版本的缓存 Bitmap。重新打开同一场景的更新版本会原位换源，延续当前视野。

### 复跑

从仓库根目录执行。先生成样例，分别启动 API 和前端夹具，再运行浏览器测试。

```sh
python -m scripts.benchmark_tiles .e2e/fixtures/p2-tiffs --generate-only
SR_E2E_TILE_SOURCE="$PWD/.e2e/fixtures/p2-tiffs/strip-49475x49454.tif" python .e2e/serve-image-open-api.py
node .e2e/serve-local-load.mjs
CHROME_PATH=/path/to/chrome node .e2e/test-tile-viewer.js
CHROME_PATH=/path/to/edge TILE_BROWSER=edge TILE_DPR=2 node .e2e/test-tile-viewer.js
```

本地 Worker 已用窗口读取构建整幅渐进预览，但当前任务消息协议与统计仍以整幅预览为单位。仅浏览器本地可读的 TIFF 沿用 P0 渐进链路；服务端能访问或已上传的单波段 TIFF 使用 P1。浏览器测量来自合成 TIFF 和本地磁盘，真实盘阵冷读、CentOS 7 及其 GDAL 环境继续沿用 P1 的现场验收计划。
