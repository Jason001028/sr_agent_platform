# current_question — 当前问题解决时间线（显示遥感图像）

> 用途：**窗口交接文档**。下一个 Claude 窗口先读本文件，再读 `sr_agent_gui_experience.md`（经验），即可无断点继续。

---

## 当前状态（一句话）

内网机（64GB，离线，Edge）本地 HTML 查看器读遥感大 TIF：**400MB 小图全黑已定位并修复**（位深限制 + alpha bug）；**大图"解码失败"的根因未最终确认**——嫌疑是用户真实文件的压缩格式（JPEG/JPEG2000/LZMA/ZSTD）不被 geotiff 支持，**等待用户回传真实报错文本**。

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

## 下一步（给新窗口）

1. **等用户回传**：让用户用当前 `tif_viewer/utif-viewer.html` 重新打开 (a) 400MB 小图、(b) 报解码失败的大图，把状态栏**完整报错文本**发来（含 `[属性 W×H，bits/spp，类型，压缩code]`）。
2. 看 compression 编码：
   - 若为 1/5/8/32946（无/LZW/Deflate/旧 deflate）→ 不该失败，需进一步查。
   - 若为 7/34712/34925/50000（JPEG/JPEG2000/LZMA/ZSTD）→ 补解码器或换库。
3. 若 400MB 小图仍异常，对照 §3 经验核对数据流。

## 关键文件

- 主交付物：`tif_viewer/utif-viewer.html`（双引擎：UTIF 小图 / geotiff 分块大图）
- 经验文档：`sr_agent_gui_experience.md`
- E2E：`.e2e/test-types.js`、`.e2e/test-regress.js`（puppeteer-core + 无头 Edge）
- 测试图：`test-tifs/`、`test-tifs/types/`
- 内存：`~/.claude/projects/.../memory/`（browser-2gb-alloc-cap、local-vendor-libs-for-viewers）
