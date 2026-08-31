# sr_agent_gui 经验文档

> 本地 HTML 查看器（quick-look.html / tif_viewer/tif-viewer.html）读取遥感大 TIF 的踩坑与已验证方案。
> 适用环境：**离线内网 Windows（64GB RAM），浏览器为 Edge/Chrome，CDN 不可达**。
> 当前主产物：`tif_viewer/tif-viewer.html`（双引擎自动分派，本地 vendor 单目录）。

---

## 1. 硬性约束（先记住这三条，能解释 90% 的"怪问题"）

| 约束 | 事实 | 影响 |
|---|---|---|
| **单次分配 2GB 上限** | Edge 单次 `new Uint8Array` 上限约 2GB（2.0GB 成功，2.7GB/3.6GB 抛 `RangeError: Array buffer allocation failed`）。与总内存无关，64GB 也救不了。 | 30000×30000 RGB8 全量解码（2.7GB + 3.6GB 两段）**必然失败** |
| **CDN 不可达** | 内网机加载 jsdelivr 等 CDN 脚本失败 | 必须本地 vendor；最稳妥是内联进 HTML 单文件 |
| **位深/类型决定显示** | UTIF.js 与自写的 normalize 对不同位深支持差异极大 | 见 §3，全黑/全白多是这里来的 |
| **真实数据外网读不到** | 开发机是**外网机**；真实遥感图全在**内网机盘阵**，无法导出/复制，也无法读其文件头探测 | 文件结构（压缩/金字塔/分块/位深）只能靠**用户回传 ENVI 头信息**（Edit Headers：Compression/Interleave）或**尺寸推断法**（16bit=宽×高×2、32bit=宽×高×4：文件大小≈→无压缩无金字塔；>→带金字塔；<→有压缩）；功能以对未知结构鲁棒为前提，真实行为需用户实机确认 |

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
- **已确认（2026-08-28）**：GF07A03 / KF02B04 两个真实大图均为无压缩(1) + 条带1行，不在上述危险区。

### 3.9 稀疏条带预览 —— 无压缩大图的秒级概览
- **场景**：无压缩 + 条带（尤其条带1行）的单波段大图，旧路径把全图全量读一遍做预览，耗时 ∝ 字节；且 geotiff 窗口读在 1 行条带上每窗口触发 ~4096 次小 slice，双重慢。
- **原理**：无压缩下任意像素都能按字节定位（offset + row*rps + col*bpp），所以预览可以**只抽读 ph 行 × 抽样列**，每行一条 `file.slice()`，直接解析 8/16/32bit 的 uint/int/float 自然值，喂给统一的拉伸引擎（`computeStats` + `paintStretch` 原样复用）。
- **实现**：`parseStrips(file)` 自解析头部 IFD 得 273/279 偏移/长度数组（兼容 classic/BigTIFF，offset 用 LONG 4B / LONG8 8B）；`sparseCollect` 8 路并发切片 + 进度条；`stripPxVal` 按 bpp/sampleFormat 解析。
- **路由**：`isSparseCandidate` = 无压缩(259=1) + 非tiled + 单波段(277=1) + bits∈{8,16,32} + 解码字节 > `SPARSE_MIN=1e8`。命中则秒级；否则原分块/UTIF 路径不变。任何解析失败自动回退原路径。
- **实测**：134MB 8192² 条带1行 → **1.3s**（读 32MB/128MB，像素精确）；原全量 4.5s。big_u16 单大条带同样命中稀疏（1.5s）。
- **局限**：①只支持单波段（多波段 BSQ 布局复杂留给全量路径）；②稀疏抽样可能漏掉极细的线状目标（行间未采到）——大图定位可接受，精细查看靠后续「按可视区读全分辨率」。**无压缩+条带也意味着无需金字塔**：任意窗口都能直接字节切片。

---

## 4. 路由决策（tif-viewer.html 现状）

```
probe = GeoTIFF.fromBlob → getImage → tiffTags(262,259) → {W,H,spp,bits,sampleFormat,photometric,compression}

needGeo = 不是「8bit 无符号整数 & spp<=4 & photometric 正常」 || 解码缓冲>1.3e9 || RGBA>1.3e9

needGeo → decodeGeoTiff：
           ├─ isSparseCandidate（无压缩+条带+单波段+>1e8）→ 稀疏条带预览（秒级，长边 ≤8192≈原始 1/3，与导出 JPG 同清晰度）
           └─ 否则 → chunkedFull 分块读取（+进度条）   // 失败时若仍是小 8bit 再试 UTIF
否则   → decodeUtif（UTIF 全量 → ≤2048 缩略图）
```

- 常量：`PREVIEW_MAX=2048`（chunked/UTIF 路径）、`SPARSE_PREVIEW_MAX=8192`（稀疏路径，≈1/3）、`SAFE=1.3e9`、`SPARSE_MIN=1e8`、`chunk=4096`。
- 内存提示：稀疏预览 8192² 时 src(Float32)+canvas ≈0.5GB/图，避免同时开过多大图。
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

- **用户大图解码失败**：若属性里的 compression 是 JPEG/JPEG2000/LZMA/ZSTD，需评估补解码器（把对应解压码打进 geotiff 或用更全的 WASM 库）。两个真实大图已确认无压缩，不在危险区，但其他真实文件未知。
- **放大看细节**：稀疏预览给出秒级概览；缩放超过预览分辨率后需「按可视区按需读全分辨率」——无压缩+条带下直接字节切片，读量与屏幕分辨率成正比，**无需金字塔**。尚未实现。
- **真机验证掩码**：开发机 e2e 浏览器无法启动（Code: 0 环境问题），掩码集成目前只过 jsdom 接线冒烟 + Node 像素管线单测，需内网真机实测「生成掩码」大图全分辨率直出。

---

## 8. 掩码绘制/栅格化/魔棒经验（2026-08-31）

### 8.1 填充约定与 Pillow 行为（前端要对齐后端）

- **约定（前后端一致）**：顶点 = 像素中心、**边界含入**——方形 `[2,2]..[7,7]` 覆盖 6×6=36px；三角 `[1,1] [4,1] [1,4]` 覆盖 4+3+2+1=10px。
- **Pillow `ImageDraw.polygon` = Bresenham 描边 + 泛洪填充**，不是纯扫描线。行为探测：菱形在顶点行仍整跨填充、倒三角列 3/4 双列实填。→ 前端流式扫描线对任意多边形**逐像素复刻 Pillow 不可能**，只能力争几何正确：固定形状（方形/三角/菱形）逐像素一致；随机多边形仅边界 ≤1px 离散化差异（<2% 像素）。

### 8.2 扫描线稳健区间算法（rasterRows 的 rowIntervals）

- **菱形塌缩 bug**：扫描线恰好穿过顶点时，旧算法把整跨塌成点（该行只填 1 像素）。
- **修法**：逐多边形 even-odd + **UNION**——
  1. 事件 = 边与扫描线交点（`r>=lo && r<=hi` 含端点），去重排序；
  2. **半数规则** `(y0<=r<y1)||(y1<=r<y0)`（下半端点计入、上半不计）→ 每对事件用**中点**做半开射线计数判奇偶，避免端点歧义；
  3. **事件点（边界点）一律含入** `[x,x]`；**水平边恰在扫描线上 → 整段含入**；
  4. 各多边形结果 **UNION**（相接/重叠合并）。
- **重叠 bug**：所有多边形合在一起做 even-odd，重叠区（被 2 个多边形覆盖）奇偶变偶 → 判为外部。必须**逐多边形**奇偶再 UNION（= Pillow 逐多边形填充再 OR）。

### 8.3 魔棒 floodSelect 的屏障语义

- `edgeR[i]/edgeD[i]` 标记**相邻像素之间**的强梯度边界（`|gradient|² > edgeThresh²`），不是把两侧像素都设为禁区。BFS 只在**跨越**该边时拦截（同侧同色像素仍可达）。
- 早期实现把梯度大的**两侧像素都禁掉** → 同侧像素误排除（cnt 4 vs 期望 5）。改跨越判定后修正。
- 大缩略图只取种子周围 `WAND_WIN=4096²` 窗口做 getImageData（避免整幅内存压力）；选区加 1px 背景外框再洞填充/外轮廓追踪（贴边选区会开口）。

### 8.4 掩码产物格式（对齐参考文件定稿）

- `掩码.tif`：uint8 灰度、**0/255**、Deflate（classic TIFF 小端/8bit/压缩8）。前后端一致；`run_sr` 经 `util.read_img` + `cv2.threshold(>0)` 消费，0/1 等价，统一用 0/255。
- `掩膜中心点坐标.txt`（参考 `SR_code/JL1KF02B03_..._mask.txt`）：UTF-8、CRLF、全角头——`＃掩膜中心点坐标（X，Y）\r\n＃掩膜编号，X坐标，Y坐标\r\n` + `序号,质心X,质心Y`（质心 2 位小数）。**原「01点阵.txt」（W×H 字符）方案废弃**（24k² 图 ~600MB 且格式未对齐）。
- 质心 = 多边形**面积质心**（叉积法，退化→包围盒中心），不是点阵平均。

### 8.5 浏览器直出掩码可行（大图不必全量位图）

- 全分辨率栅格化**不建位图**：`rasterRows` 逐行事件扫描出每行区间 → 直接喂 `pako.Deflate` 压缩 → 内存只与压缩缓冲相关，与图幅无关 → 24739×24199 也扛得住。
- 进度按行批回调（`setTimeout(0)` 让出主线程）配进度条，页面不冻结。
- 浏览器端验证手段：`maskgen.js` UMD（`module.exports`）在 Node 直接 require 跑单测 + 与 Pillow 交叉比对；HTML 集成用 **jsdom** 冒烟（开发机无头 Edge 起不来）——`fsIO.init()` 的 `indexedDB` 在 jsdom 未定义抛错，但被其 `.catch` 吃掉（安全）；`downloadBlob` 覆盖后捕获两次下载断言文件名/格式。**jsdom 只验接线，像素/压缩管线靠 Node 层覆盖。**

---

## 9. 一句话备忘

> **浏览器看大 tif：懒读 IFD → 估内存 → 超限分块窗口读 + 现场降采样；非 8bit 一律 min-max 拉伸、alpha 永不缩放、WhiteIsZero 反色；失败看"错误文本 + 属性（bits/类型/压缩码）"定位，别猜。**
> **掩码：顶点=像素中心边界含入；扫描线逐多边形 even-odd + UNION（重叠区奇偶判反坑）；魔棒屏障在像素"之间"只拦跨越；浏览器直出掩码流式逐行 Deflate 可行；产物统一 掩码.tif(0/255) + 掩膜中心点坐标.txt(质心,CRLF)。**
