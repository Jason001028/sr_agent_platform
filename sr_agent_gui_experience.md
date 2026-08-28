# sr_agent_gui 经验文档

> 本地 HTML 查看器（quick-look.html / tif_viewer/utif-viewer.html）读取遥感大 TIF 的踩坑与已验证方案。
> 适用环境：**离线内网 Windows（64GB RAM），浏览器为 Edge/Chrome，CDN 不可达**。
> 当前主产物：`tif_viewer/utif-viewer.html`（双引擎自动分派，本地 vendor 单目录）。

---

## 1. 硬性约束（先记住这三条，能解释 90% 的"怪问题"）

| 约束 | 事实 | 影响 |
|---|---|---|
| **单次分配 2GB 上限** | Edge 单次 `new Uint8Array` 上限约 2GB（2.0GB 成功，2.7GB/3.6GB 抛 `RangeError: Array buffer allocation failed`）。与总内存无关，64GB 也救不了。 | 30000×30000 RGB8 全量解码（2.7GB + 3.6GB 两段）**必然失败** |
| **CDN 不可达** | 内网机加载 jsdelivr 等 CDN 脚本失败 | 必须本地 vendor；最稳妥是内联进 HTML 单文件 |
| **位深/类型决定显示** | UTIF.js 与自写的 normalize 对不同位深支持差异极大 | 见 §3，全黑/全白多是这里来的 |

---

## 2. 浏览器看大图的正确姿势

1. **不要全量解码**。先懒读 IFD（`GeoTIFF.fromBlob(file).getImage()`，只读几 KB 头部）拿到 W/H/spp/bits/sampleFormat。
2. 按 `W*H*spp*(bits/8)` 估解码缓冲、按 `W*H*4` 估 RGBA 输出，任一 > `SAFE=1.3e9` 就走**分块窗口读取**。
3. 分块参数：窗口 4096px，`image.readRasters({window:[x0,y0,x1,y1], samples, interleave:true})`，每块现场降到 ≤2048 预览画布；块间 `setTimeout(r,0)` 让出主线程，配进度条，页面不冻结。
4. UTIF 全量路径里 catch `RangeError`，匹配 `/array buffer/i` → 自动重探并回退 geotiff 分块。

---

## 3. 关键 Bug 与根因（按"踩坑顺序"排）

### 3.1 400MB 小图全黑 —— UTIF.toRGBA8 位深限制（用户报的问题一）
- **根因**：UTIF.js `toRGBA8` 对灰度（intp==1）只支持 bps 1/2/8/16，**32bit（float32/uint32）落到全 0 → 全黑**。用 float32/uint32 测试图可复现（100% 全黑）。
- **修复**：路由改为"只有小的 8bit 无符号整数走 UTIF；16/32bit、浮点、多波段一律走 geotiff 分块 + min-max 拉伸"。

### 3.2 alpha 被拉伸系数缩放 —— 第二个全黑 bug（最难查的一个）
- **现象**：geotiff 路径读 16bit RGB，输出 `[0,255,0,0,…]` —— **alpha 通道 = 0 → 整图全透明 → 显示为黑**。
- **根因**：旧 `normalizeRGBA` 把 alpha 也乘了拉伸系数 scale；16bit 的 scale = 255/65535 ≈ 0.0039，255×0.0039 ≈ 0。
- **修复**：alpha 独立处理；`comps<4` 时 alpha 强制 255，永不缩放。**教训：拉伸只作用在颜色通道，alpha 永远是 alpha。**

### 3.3 浮点/uint32 直接映射 → 全白
- **根因**：旧逻辑对"值域宽"的浮点直接用 scale=1 → 大值被 clamp 成 255 → 全白；Uint32Array 直接没处理。
- **修复**：非 8bit 统一 min-max 线性拉伸到 0~255，跳过 NaN/Inf；常量图退化处理（全零→黑，常量非零→中灰）。

### 3.4 geotiff 惰性 fileDirectory（API 陷阱）
- `image.getFileDirectory()` 返回的是**惰性桩**：`deferredFields`/`actualizedFields` 全空，实例上**没有** `getField`/`getTag`，`fd.fileDirectory` 也是 undefined。
- **修复**：自写 `tiffTags(file)`，只读文件头部 1MB，按 TIFF/BigTIFF 字节布局直接解析 tag 262（photometric）和 259（compression）。
- **BigTIFF 要点**：magic 43 vs classic 42；IFD offset 在 byte8 vs byte4；entry 20 字节 vs 12 字节；type 大小表 `[0,1,1,2,4,8,1,1,2,4,8,4,8,0,0,0,8,8,8]`；inline 值 vs offset 值。

### 3.5 geotiff readRasters 返回"自然类型"数组
- 根据 sampleFormat（1=uint, 2=int, 3=float）返回对应类型：8bit→`Uint8Array`、16bit→`Uint16Array`、32bit→`Uint32Array`、浮点→`Float32Array`。
- 所以"8bit 直连映射，其余 min-max 拉伸"可统一用 `raster instanceof Uint8Array` 判断直连与否。

### 3.6 Photometric 0 = WhiteIsZero 必须反色
- tag 262：0=WhiteIsZero（白=0，需输出 255-v 反相），1=BlackIsZero（正常），2/3=RGB/Palette，5=CMYK。
- 解析后塞进 `image._ph`，`getSamplePlan()` 据此决定 `invert`。

### 3.7 页面清空 input.value（自动化测试陷阱）
- 页面在 `input.onchange` 末尾执行 `e.target.value = ''`，上传后 `fileInput.files` 是空——**E2E 里读取文件必须从 `window.recs[...].file` 拿，不能从 input.files 读**。

### 3.8 geotiff 不支持的压缩格式（用户报的问题二，待确认）
- geotiff.js 内置解码器只覆盖：无压缩 / LZW / Deflate（含旧 deflate 32946）/ PackBits。
- **JPEG(7)、JPEG2000、LZMA(34925)、ZSTD(50000) 等不支持 → 会解码失败**。
- 现在的页面失败时会显示**具体错误 + 文件属性**（W×H、bits/spp、sampleFormat、compression 编码），据此可判定是否要补解码器。
- 另一条路：UTIF.js 的 `_decompress` 已 patch 兼容 cmpr 8 / 32946（旧 deflate）。

---

## 4. 路由决策（utif-viewer.html 现状）

```
probe = GeoTIFF.fromBlob → getImage → tiffTags(262,259) → {W,H,spp,bits,sampleFormat,photometric,compression}

needGeo = 不是「8bit 无符号整数 & spp<=4 & photometric 正常」 || 解码缓冲>1.3e9 || RGBA>1.3e9

needGeo → decodeGeoTiff（分块+进度条）   // 失败时若仍是小 8bit 再试 UTIF
否则   → decodeUtif（UTIF 全量 → ≤2048 缩略图）
```

- 常量：`PREVIEW_MAX=2048`、`SAFE=1.3e9`、`chunk=4096`。
- 脚本引入顺序：`pako.min.js → utif.js → geotiff.min.js`（deflate 解压依赖 pako）。

---

## 5. 已验证的数据类型矩阵（全部 E2E 通过）

| 文件 | 类型 | 路径 | 验证点 |
|---|---|---|---|
| rgb8.tif | 8bit RGB | UTIF | 正常显示 |
| gray16.tif | 16bit 灰度 | geotiff | 拉伸显示 |
| u16_rgb.tif | 16bit RGB | geotiff | 不透明（防 alpha bug） |
| u16_whitelzero.tif | 16bit 灰度 WhiteIsZero | geotiff | 反色（左亮右暗） |
| u32_gray.tif | uint32 全值域 | geotiff | 拉伸不白 |
| f32_gray.tif | float32 0-10000（DEM 型） | geotiff | 拉伸不黑不白 |
| f32_reflect.tif | float32 0-1 | geotiff | 直连观感 |
| f32_rgb.tif | float32 RGB | geotiff | 拉伸显示 |
| big_u16.tif | 8192² uint16 灰度 134MB | geotiff 分块 | 进度 100%、多窗口、渐变正确 |
| （stub）UTIF.decodeImage 抛 RangeError | 8bit RGB | 回退 geotiff | 自动兜底 |

---

## 6. 测试方法论

- **E2E 自动化**：`.e2e/` 下 `puppeteer-core` + 无头 Edge（`C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe`），`file://` 打开页面 → `input.uploadFile()` → 轮询 `.status` 文本 → 读 `window.activeRec.thumb` 画布统计（mean/left/right/blackPct）断言。
  - `test-types.js`：数据类型矩阵回归。
  - `test-regress.js`：大图分块 + 分配失败回退回归。
- **测试图生成**：python + numpy + tifffile，注意 `photometric` 枚举要写整数（`photometric=0`，写 `'min-is-white'` 会报错）：
  ```bash
  python -c "import numpy as np,tifffile; N=8192; y,x=np.mgrid[0:N,0:N]; tifffile.imwrite('big_u16.tif',(x/(N-1)*65535).astype(np.uint16))"
  ```
- **Bash cwd 陷阱**：工具 shell 的 cwd 在多次调用间保持 → 生成文件/跑 node 时先 `cd` 到明确目录（两次因此把文件写到 `.e2e/test-tifs/types/` 去了）。测试脚本一律写成 .js 文件再跑，别用内联 `node -e` 传 Windows 反斜杠路径（转义易错）。

---

## 7. 待确认 / 未决问题

- **用户大图解码失败**：若属性里的 compression 是 JPEG/JPEG2000/LZMA/ZSTD，需评估补解码器（把对应解压码打进 geotiff 或用更全的 WASM 库）。
- 无概览金字塔的大图按 4096 窗口逐块读，速度取决于 IO；如需更快可考虑先读概览层（若文件带 pyramid）。

---

## 8. 一句话备忘

> **浏览器看大 tif：懒读 IFD → 估内存 → 超限分块窗口读 + 现场降采样；非 8bit 一律 min-max 拉伸、alpha 永不缩放、WhiteIsZero 反色；失败看"错误文本 + 属性（bits/类型/压缩码）"定位，别猜。**
