# current_question — 当前问题解决时间线（显示遥感图像）

> 用途：**窗口交接文档**。下一个 Claude 窗口先读本文件，再读 `sr_agent_gui_experience.md`（经验），即可无断点继续。

---

## 当前状态（一句话）

**真实文件布局已确认**：GF07A03（1.11GB）与 KF02B04（1.78GB）均为**无压缩 · 条带1行**的单波段 16bit 影像。已实现**稀疏条带预览**（无压缩大图秒级出预览，只抽读少数条带做字节切片，不碰全文件）：134MB 测试图从全量 4.5s → **1.3s（读 32MB/128MB）**，预计真实 1.1GB 文件从 ~2 分钟降到秒级。**下一步**：放大看细节时按可视窗口读全分辨率（无压缩切片，无需金字塔）。

## 时间线

### 2026-08-26 · 起点：CDN 不可达
- 用户报错：`geotiff.js 加载失败（CDN 不可达）`。内网机加载 jsdelivr 失败。
- 修复 quick-look.html：移除 jQuery（改原生 JS）、修 `toRGBA8` 返回 Uint8Array 而 ImageData 需 Uint8ClampedArray 的兼容问题、**pako+UTIF 内联进 HTML 单文件（零外部引用，自包含）**。

### 2026-08-27 上午 · 方案定型 + 撞上 2GB 上限
- 用户选择「UTIF 单库简化版（冻结等待）」。目标：10000² 内 UTIF 全量，12000² 以上分块。
- 实测撞墙：`解码失败，Array buffer allocation failed`。
- `.e2e/probe.js` 确认：**Edge 单次分配上限 ~2GB（2.0GB 成功，2.7/3.6GB 抛 RangeError）**，64GB 内存无关。30000² RGB8 全量解码（2.7GB+3.6GB）必然失败。
- 引入 **geotiff.js 分块回退**（本地 vendor），构建双引擎架构。

### 2026-08-27 下午 · 本次会话：修 400MB 全黑 + 收口大图解码失败
1. **复现全黑**：生成 float32/uint32 测试图 → UTIF 路径 100% 全黑。根因：**UTIF.toRGBA8 灰度只支持 bps 1/2/8/16，32bit 落空全 0**。
2. **定位第二个全黑 bug（最难）**：16bit RGB 输出 `[0,255,0,0,…]`——**alpha 被拉伸系数缩放**（scale≈0.0039 → alpha 0 → 全透明黑）。修：alpha 永不缩放，comps<4 强制 255。
3. **修浮点/uint32 全白**：统一 min-max 线性拉伸（跳过 NaN/Inf），8bit 原样直连。
4. **geotiff 惰性 fileDirectory 陷阱**：getFileDirectory 是惰性桩、无 getField/getTag → 自写 `tiffTags()` 直接解析 TIFF/BigTIFF 头部 tag 262(photometric)/259(compression)。
5. **修 WhiteIsZero 反色**（photometric=0 → 输出反相）。
6. **修 E2E 陷阱**：页面清空 input.value → 从 `window.recs[].file` 读文件。
7. **验证通过**：
   - `test-types.js`：8 种数据类型（8bit RGB / 16bit 灰度 / 16bit RGB / uint32 / float32×3 / WhiteIsZero 反色）全部通过。
   - `test-regress.js`：8192² uint16 灰度 134MB → 分块多窗口 + 进度 100% + 渐变正确（4.5s）；UTIF 分配失败自动回退 geotiff 通过。
8. 文档落盘：`sr_agent_gui_experience.md`（经验）、`.gitignore`（屏蔽 test-tifs/.e2e/*.zip）、内存文件更新。

### 2026-08-28 · 稀疏条带预览（大图加速第一刀）
- 用户回传探针：GF07A03、KF02B04 均为**无压缩 · 条带1行**。
- 2 分钟之谜根因确认：旧路径把全图全量读一遍做预览，耗时 ∝ 字节；且 geotiff 窗口读在 1 行条带上每窗口触发 ~4096 次小 slice（42 窗口 × 4096 ≈ 17 万次切片读）。
- 实现 `parseStrips`（头部解析条带偏移/长度数组，兼容 classic/BigTIFF）+ `sparseCollect`（按条带字节切片，抽 ph 行 × 抽样列，8 路并发，进度条）；`stripPxVal` 支持 8/16/32bit 的 uint/int/float。
- 路由：`无压缩 + 条带 + 单波段 + >100MB` → 稀疏预览；否则原分块/UTIF 路径不变。
- E2E `test-sparse.js` 全过（像素精确 + Deflate 回退）；`test-regress/stretch/stretch8/locator/types/probe-list` 全过无回归。
- 134MB 8192² 测试图：稀疏 **1.3s**（读 32MB/128MB）；原全量 4.5s。big_u16 也自动转稀疏（1.5s）。

## 下一步（给新窗口）

> **环境约束（已写入记忆）**：开发机是**外网机**，真实遥感图全在**内网机盘阵**，无法导出/复制/读头探测。文件结构只能靠**用户回传 ENVI 头信息**（Edit Headers：Compression/Interleave）或**尺寸推断法**。不能要求用户给文件路径。

1. **等用户回传**（两种途径，用户二选一）：
   - 用当前 `tif_viewer/utif-viewer.html` 打开 (a) 400MB 小图、(b) 报解码失败的大图，把状态栏**完整报错文本**发来（含 `[属性 W×H，bits/spp，类型，压缩code]`）。
   - 或回传 ENVI 头信息：**Compression 字段** + 文件字节大小 + 宽×高 + 有没有 `.ovr`（用户已确认 Interleave=BSQ，单波段下不是瓶颈）。
2. 看 compression 编码：
   - 若为 1/5/8/32946（无/LZW/Deflate/旧 deflate）→ 不该失败，需进一步查。
   - 若为 7/34712/34925/50000（JPEG/JPEG2000/LZMA/ZSTD）→ 补解码器或换库。
3. 若 400MB 小图仍异常，对照 §3 经验核对数据流。

## 新需求（2026-08-27 晚，待开工）

用户要求**显示拉伸（ENVI 风格）+ 像素定位**——**已实现并全部 E2E 通过**（见 §8 时间线追加）。
加速大图读取（进行中）：

- 用户场景：单波段灰度/DEM，**要放大看细节**，期望 ≤1 分钟。
- **真实文件布局已确认**（GF07A03/KF02B04 均无压缩·条带1行）→ **无需金字塔**：任意窗口都可直接字节切片。
- ✅ 已完成：**稀疏条带预览**（秒级概览，见 2026-08-28 时间线）。
- ⬜ **待做：按可视区按需读全分辨率瓦片**（放大看细节）：缩放超过预览分辨率时，把可视窗口对应的条带切片读出并叠加绘制；读量与屏幕分辨率成正比，无压缩直接切片。设计要点：渲染时先画预览底图，再叠加已加载的细节瓦片；滚动/缩放触发新窗口读取并去抖；缓存已读瓦片。
- 逻辑验证用本地 test-tifs/sparse；真实行为需用户实机确认。

## 关键文件

- 主交付物：`tif_viewer/utif-viewer.html`（UTIF 小图 / geotiff 分块大图 / **稀疏条带预览** 三路分派）
- 经验文档：`sr_agent_gui_experience.md`
- E2E：`.e2e/test-sparse.js`（稀疏）、`test-regress.js`、`test-types.js`、`test-stretch.js`、`test-locator.js`（puppeteer-core + 无头 Edge）
- 测试图：`test-tifs/`、`test-tifs/types/`、`test-tifs/sparse/`
- 内存：`~/.claude/projects/.../memory/`（browser-2gb-alloc-cap、local-vendor-libs-for-viewers、intranet-data-inaccessible）
