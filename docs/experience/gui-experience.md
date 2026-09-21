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
- **局限**：①只支持单波段（多波段 BSQ 布局复杂留给全量路径）；②稀疏抽样可能漏掉极细的线状目标（行间未采到）——大图定位可接受，精细查看靠导出的 **JPG 中间产物**（长边 8192；09-01 预览路径定案后不再做浏览器内全分辨率切片）。**无压缩+条带也意味着无需金字塔**：任意窗口都能直接字节切片。

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
- **放大看细节（已定，09-01）**：预览路径 = **JPG 中间产物**——网页/预览显示导出 JPG（长边 8192≈1/3），「按可视区按需读全分辨率」**已砍**（其前提被 JPG 产物路径取代）。若日后要网页内全分辨率，无压缩+条带下仍可直接字节切片，读量与屏幕分辨率成正比，**无需金字塔**，工作冻结非丢失。
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

## 9. 盘阵读 JPG 经验（阶段4 · 2026-09-02）

- **决策**：盘阵场景浏览器**不读原始 TIF**，改读服务器懒生成的 JPG（拉伸 **09-17 起为直方图均衡**，尺寸 **09-19 起为各边 ÷2…÷32 五档可选、默认 ÷4**；本条其余文字描述的 8192 长边 + 2% Linear 是当时规则，见 §9.1）。HttpSource/Range 方案废弃——本地 `tifDecode`/`sparse` 仍是浏览器读 TIF 的唯一路径（本地文件用），不与盘阵 JPG 混用。
- **服务端生成必须镜像前端稀疏语义**：预览/导出看起来要一致，就照抄前端映射——`ps=min(1,max_edge/max(W,H))`（**v3 起 `max_edge = round(max(W,H)/div)`**；v2 是固定的各边 1/2，旧规则是 8192 长边封顶）、`out(i,j)=src[round(i*(H-1)/(ph-1)), round(j*(W-1)/(pw-1))]`（端点对齐，杀右/下边条纹）；拉伸 **v2 起为直方图均衡**（旧为 2% Linear）+ WhiteIsZero 先反色；const 图规则与前端一致（全 0→黑，其他 const→128）。**同一组常量前后端各写一份，改任一端都要同步另一端的单测。**
- **逐条带抽读只碰采样行**：大图不整图载入——每采样行定位到所在条带（无压缩常一行一条带）读其字节跨距、只留采样列。这样进程内存与图幅解耦，1.78GB 图也只在几十秒内生成完。
- **route='jpg' rec 的关键是 stats 固定 0..255**：JPG 像素已是烘焙值，src 的 min/max 设成 0..255 → `linear` 拉伸恒等，不改线性模式下看到的就是服务器烤的那份；其余模式（平方根/对数/直方图均衡）在显示层做二次拉伸。**2026-09-17 改向**：此前 `paintStretch` 对 jpg 直接早退、拉伸下拉也禁用，导致场景图一个选项都用不了；现在场景照走同一条绘制链路，起手值为直方图均衡（`lib/scene.startStretch`）。代价：烘焙时按 2% 裁掉的两端拉不回来。
- **掩码换算用元数据 W/H，不探 TIF**：盘阵场景坐标回全分辨率 = `thumbToOrig`，scale 分母用服务端给的 W/H（元数据），不是本地 probe。
- **场景 id 用 base64url(rel path)**：URL 安全 + 无歧义；resolve 时 repad 补 '='、拒 `..`、realpath 校验必须在 scenes root 内（`<fake>` 占位路径同样拒）——fake 回退数据永远不许被当作真实文件去生成预览。

### 9.1 烘焙规则 v2 的四个坑（2026-09-17，尺寸口径已由 §9.2 取代）

规则改成 **各边 1/2 + 直方图均衡 + q85**（`PREVIEW_SCALE` / `stretch_equal` / `PREVIEW_JPG_QUALITY`）。
（**尺寸那半已过时**：09-19 起 1/2 只是最低档，见 §9.2。底下四个坑与拉伸那半仍然成立。）
尺寸那一条**没改采样算法**：原来就是 `ps = min(1, max_edge / max(W,H))`，把 `max_edge` 传成
`round(max(W,H) * 0.5)` 就是严格各 1/2。踩到的坑按「不查会白干」排序：

1. **Pillow 的 `MAX_IMAGE_PIXELS` 会让缓存永远判不中（真 bug，最隐蔽）**。默认 8948 万像素，
   超过 2 倍直接抛 `DecompressionBombError`。1/2 尺度把 2.4 万像素级的源烤成 1.5 亿像素 →
   `_cache_hit` 里的 `Image.open` 抛异常 → 按「缓存损坏」处理 → **每次打开都重烤一遍**，整条
   优化全部抵消，而日志里只有一句 bomb 警告。设 `Image.MAX_IMAGE_PIXELS = 1 << 30`。
2. **光看 mtime 判缓存一定会踩**：真机上换包后，旧的 8192+2% 图**比源文件新**，会被判有效而
   永不重烤（用户看到「什么都没变」）。规则戳写进 JPEG 注释（`comment=` 写 / `info["comment"]` 读），
   改尺寸/拉伸/质量任一都要 bump `PREVIEW_RULE_VERSION`。
3. **Windows 没有 `os.pread`**，句柄不能跨线程共享 → 并行读必须**每线程开自己的句柄**。
   另外 `ThreadPoolExecutor.map` 的结果**必须消费**（`list(...)`）：异常是在线程里抛的，
   不迭代就不会转交给调用方，会变成静默漏读。
4. **`Uint8ClampedArray` 是 ties-to-even 四舍五入，`astype(np.uint8)` 是截断** —— 直方图均衡的
   LUT 用后者会整图差 1 个灰阶。用 `np.rint`（同为 ties-to-even）与前端逐像素对齐。
   另外 `equal` 用的是 **min/max**（不是 `linear2` 的 p2/p98），常量图规则沿用前端：全零→0、其它→128。

**性能上被测量推翻的两个直觉**（别再按它们优化）：① 去掉 `stretch_2pct` 的 `astype(np.float64)`
副本只值 **0.131s**，不是热点（行块实现仍保留，那是 1.5 亿像素下的内存保障）；② 逐行 `seek+read`
换块读**更慢**（0.583s vs 0.324s，块读多读一倍字节），memmap / 整文件顺序读同样更慢。**逐行
1/2 采样只读源文件一半的字节，已经是最省的**。真正的收益在**延迟隐藏**：约 1.2 万次读，单次毫秒级
延迟串行就是十几秒，8 路并发压回一秒量级（页缓存命中时只快 1.4 倍，开发机上几乎量不出收益）。

### 9.2 档位可调 + 拖入改落盘阵（2026-09-19）

尺寸从写死的各边 1/2 改成**用户可调的 5 档**（÷2 · ÷4 · ÷8 · ÷16 · ÷32，默认 ÷4），
拖动条放在工具栏定位组件右侧；拖入链的产物从临时缓存改成**盘阵场景目录里的 `<stem>_preview.jpg`**。

**为什么必须往行上加 `previewDiv`（这一轮唯一的真陷阱）。** 前端那次「先打 `/preview` 重烤、
再取静态 URL」是**带条件**的（只在 `!row.hasPreview` 时才打），而 `hasPreview = jpg.is_file()`
**不认档位**。于是滑动条拉到 ÷8 之后：盘上那份 ÷4 的文件仍在 → `hasPreview` 仍为真 → 前端
跳过重烤 → 直接取静态 URL → **看到的还是旧档位那张图**，而且磁盘上那个文件一个字节都没变。
红的是**凡有静态 URL 的行**；库外手工行每次走 `/preview` 回字节，天然跟着档位走、不受影响。
所以每行要能回答「**盘上这份是各边除以几烤的**」，这就是 `previewDiv`（读 JPEG 注释戳，
只读头不解像素）。`previewDiv` 为 `null`（旧格式戳）同样按「不符」处理，代价是一次惰性重烤。

**别把「改写 row 字段」和「取这张图」混起来。** 拖入链用的是独立函数 `fetchDropSceneJpg`，
**不许**改写 `row.hasPreview` / `row.jpgUrl` / `row.previewDiv` —— 那三个字段锚在**生产那份
`<stem>.preview.jpg`** 上。被拖入那条链的产物置真之后，用户再从场景库打开同一场景就会跳过
懒生成、直接打一个 404 的静态 URL，图再也出不来。这条是硬约束，有测试钉着。

**`?div=N` 是击穿 nginx `max-age=3600` 的唯一手段。** 改档位后 URL 不变、内容变了，
查询串变了才能让浏览器重新取。但**只对平台自己烤的那份拼**（`.preview.jpg` 结尾）——
无条件拼会打红「源本身就是 JPG」那条行，而档位对显示件毫无意义。同理 `previewDiv` 对
`.jpg/.jpeg` 源恒为 `null`，前端若把 `null` 一律当「档位不符」，每次打开都会白打一次 `/preview`。
判据是 `isBakedPreviewUrl()`（`/\.preview\.jpe?g$/`）。

**试过又退回来的方案：把 `/preview` 的响应体直接当 blob。** 一次下载、不补静态 URL，
看着更省。实测被 `.e2e/test-scenes.js` 打红 —— 那条断言要求「静态读图走 nginx 的
`/disk-array/` alias 位」，几 MB 的 JPEG 不该占 API 进程的内存与带宽。改回
「先打 `/preview` 触发烘焙（字节丢掉）+ 再取静态 URL」：稳态下只有一次下载，
只有首次/换档那种要重烤的情形才两次。理由是**换档后的正确性已由 `?div=N` 保证**
—— 缓存键变了，第二次取的一定是新图。

**新增控件导致工具栏横向溢出。** 1366 屏（正文 1334px）下左右两个半区预算分别是
≈626px / ≈414px，插一条 range 正好放得下，但没有余量。e2e 里补了「工具栏不横向溢出
（`scrollWidth == clientWidth`）+ 没有控件被挤出右缘」两条断言，1366 视口下跑。

**e2e 钉档位不能用 `window.__viewer`**：它只在 `pages/ViewerPage.vue` 挂载，而烘焙发生在
`/scenes` 页。改用各脚本已有的 `page.evaluateOnNewDocument` 钩子写 `localStorage`。
**注入回调必须 try/catch** —— 它在 `about:blank` 上也跑，opaque origin 下 `localStorage`
抛 SecurityError，会撞上脚本尾部那条「无浏览器错误」断言。

**换包须知**：所有现存 `<stem>.preview.jpg` 的戳是 `v2`，新代码认 `v3` → 逐个场景首次打开时
惰性重烤一轮（把滑块停在 ÷2 也一样）；`frontend/dist` 与 `backend` **必须同包更新** ——
端点从 `preview-tmp` 改名 `preview-drop`，只换一个会 404。

### 9.3 显示源比较规则：谁清晰用谁（2026-09-20）

用户的原话是「拖入盘阵中的 `.jpg`/`.tif` 时，其目录下的 `PAN.jpg` 作为预生成的中间产物，
**分辨率不够**」。所以显示件 jpg 不再一律当基准，要跟同目录的栅格比一比。

**规则只有一条**（后端 `_raster_preview` 提供两边的尺寸，前端 `scene.ts::rasterPreviewWins`
判定，公式与文档、单测共用）：

```
round(max(rasterW, rasterH) / div) > max(jpgW, jpgH)  → 显示源换成服务端从栅格烤的那份
否则（含相等、含任一侧尺寸读不出）                     → 保持显示件 jpg（现状）
```

**严格大于，不是「不小于」。** 相等不算赢：服务端烤一份要读整幅栅格（几十秒 + GB 级读盘），
像素数不比现状多就没理由付这个代价。

**默认档位 ÷4 下这条基本不触发 —— 这是算术，不是 bug，验收口径别写错。** 24000 源 + 8192
显示件：÷2 → `12000 > 8192` **赢**，÷4 → `6000 > 8192` 输，÷8 → 输。于是：

- 验收只能写「这条比较规则本身」，**不能**写「jpg 行一律走服务端」。
- 现有 e2e 夹具（1600×800 栅格 + 800×400 jpg、640×320 + 320×160）在 ÷4 下**全部判 jpg 赢**
  → 那些断言**一字不改地继续绿**，这就是这轮改动的回归钉子（写新功能时先把它们跑一遍）。
- 要让规则真触发，得把滑块拉到 ÷2，或者造一份长边只有栅格 1/5 的 jpg（e2e 的 E4 就是这么造的）。

**比的是盘阵那份 jpg，不是用户拖进来那份本地文件。** 指纹对 jpg 行只比名字（见
§9.2 那条「别把改写 row 字段和取这张图混起来」的同源理由），用户本地那份可能另存过、缩过，
而平台口径是「盘阵上的才是基准」。e2e 夹具因此**故意**把本地那份造成 640×320、盘阵那份
320×160 —— 尺寸不同才分得清像素是从哪来的。

**三个字段的语义一个字不动。** `row.hasPreview` / `row.jpgUrl` / `row.previewDiv` 锚在
**显示件 jpg 自己**身上（§9.2 的红线）。栅格的状态走新加的只读字段 `rasterPreview`，
前端只写 `row.rasterPreview.hasPreview/.previewDiv`。把栅格的信息塞进那三个字段，会让
「从场景库打开同一场景」跳过懒生成、直接打一个指向不存在文件的静态 URL（§9.2 已经栽过一次）。

**换不换在后端定，不在前端定。** `/preview` 与 `/preview-drop` 各插一次同名栅格探测
（`sibling_raster_path`：固定候选名 `.tif/.tiff/.img` 逐个 `is_file()`，**不列举目录**），
命中就把源换成栅格再往下走。前端只管按档位取图，两条入口换出来的字节因此完全一致。
落点不需要变：`.preview.jpg` 对 `PAN.jpg` 与 `PAN.tif` 是同一个文件名，**两行共用同一份缓存**
（好处是不烤两次；代价见下）。

**留给下一轮的病：同一份落点会被不同档位的客户端互相顶掉。** 这个病今天就有
（两台机器的滑块停在不同档位就会互烤），工作流 B 只是把它拖进更多行。本轮不治理
（不引入带档位的落点 `<stem>.preview.div2.jpg`），在文档里点明，不装作没有。

**e2e 夹具的坑：盘阵场景目录要拼满三层。** `tree()` 返回的是段级之下的两层，写夹具时
少拼一层就会把 tif 与 `_meta.xml` 写进段级目录，反推看到的是一份「缺 `<目录名>_meta.xml`」
的目录，前后端都认不出这个场景（这个错真踩过一次，症状是 resolve 报「缺 meta」）。

---

## 10. 图像对比：两个格子与共享变换（2026-09-20）

需求是「一个画布里左右并排看两张图，滚轮与拖动同步作用两侧，拖进来的图落在鼠标所在那半」。
难点不在画两遍图，而在**把「视图变换」从单例拆成按侧，同时让既有消费点一处不改**。

### 10.1 一格一套变换，但用**该格自己的局部坐标**

`ViewState = {scale, ox, oy}` 原来是全局单例，现在每侧一份（`viewA`/`viewB`）。关键决定：
**每格的变换用该格自己的局部屏幕坐标**（原点 = 该格左上角），渲染时

```
save() → rect()+clip(pane.rect) → translate(pane.rect.x, pane.rect.y) → drawImage(...) → restore()
```

于是 `fitView(w,h,rect.w,rect.h)`、`locateView(scale,tx,ty,rect.w,rect.h)`、
`visibleThumbRect(view,rect.w,rect.h,…)`、`mouseToThumb(view,x,y,{left: rect.left+rect.x,…})`
**全部原样可用**，一个都不用改。这是「分屏不重写坐标数学」的全部前提。

`PaneRect` 与既有的 `Rect` **刻意不同名**：后者是缩略图**像素**的整数闭区间
（`visibleThumbRect` 用），前者是**屏幕坐标**的浮点矩形。同名同形会让前者的调用方读错。

### 10.2 踩到的坑：把「画布局部」当成了「格局部」（真 bug，e2e 抓出来的）

同步缩放的规则是「指针所在格用指针本身当锚点，另一格用**同一个归一化位置**当锚点」。
第一版写成了：

```ts
wheelZoomBoth(va, vb, ra, rb, mx, my, factor)   // 锚点：anchorAt(指针所在格, u, v)
anchorAt(rect, u, v) = [rect.x + u * rect.w, rect.y + v * rect.h]    // 错
```

`anchorAt` 加上 `rect.x` 是对「画布局部坐标」的直觉，但**每格的变换是格局部的**，
右格的 `ViewState.ox` 里没有那个左格宽。于是滚轮一滚，非指针那格的锚点整体平移一个左格宽，
图直接飞出视野；指针在右格时，连右格自己的锚点也是错的（右格的 `rect.x` = 左格宽，非 0）。

三处要记住：

1. **这个 bug 我自己的单测先替它背了书** —— 用例是按错语义写的，所以全绿。修法是把
   `anchorAt` 删掉、换成 `anchorAtLocal(rect,u,v) = [u*rect.w, v*rect.h]`，并在单测里补一条
   **反向断言**（旧公式算出来的 `ox` 与新公式至少差 1）——不然「改好了」与「这条用例本来就
   不会发现」长得一样。
2. 第一版修完**还是错的**：两个参数按「是不是 A 格」命名，指针在右格时 `normAnchor` 拿了
   另一格的矩形。改成按**角色**命名（`pPane`/`oPane` = 指针所在格 / 另一格，`pointerSide`
   由调用方显式给）之后才对。参数名骗人就会一路骗到运行结果。
3. **e2e 要能读到落点判据**，别在测试里自己乘一遍：分隔线位置暴露成 `splitX()`（画布局部
   坐标里的左格宽），`paneAtX(localX, splitX)` 与拖放落点判据共用它。

### 10.3 `activeId` 仍是「活动侧的 rec」

`view` 改成了按侧的可写 computed（`get`/`set` 看 `activeSide`），写入点全在 store 内（5 处），
读点一个不用改。但**没有**新增 `activeRecFor(side)`：`activeId` 保持「活动侧那张」的语义，
`placeRec(id, side)` 在激活开头把 `activeId` 与活动格对齐。于是掩码、ROI 统计、云量卡、
任务状态、待修复清单选中、状态栏、工具栏可用性（约 40 处）**全部自动跟随活动侧**，
一行都不用动。渲染器与拖放路由另走 `recForSide(side)`。

配套的两条纪律：

- **滚轮不改活动侧**（只是拿所在格当锚点参照系），否则缩放时掩码与云量卡会跟着闪。
- `setActiveSide` **刻意不调 `activate()`**：不重新解码、不重新适配、不惊动侧舱，只重置
  `marker`/ROI 选择/云量。两者共用一个私有 `switchActive`，避免两处漂移。

### 10.4 单屏快速路径必须**保留字面写法**

`render()` 里单屏分支不 save/clip/translate，`onWheel`/`onPan` 单屏分支也用原来的表达式、
**不进** `normAnchor`/`anchorAtLocal`。为什么不能统一：那套往返是「一次除法 + 一次乘法」，
IEEE double 下不保证回得来（`100/1314*1314 = 99.99999999999999`），而
`test-vue-viewer.js` 的 D/E 段是**直接采样画布中心 60×60 像素**断言位图的 ——
多一次亚像素偏移就会动到那些像素。把单屏塞进新数学，代价是既有一整套断言全部要重写，
收益是零。**新数学只服务分屏。**

### 10.5 适配：半幅是新视口，全幅的适配两边都不对

| 触发 | 行为 |
|---|---|
| 进分屏 | 左格接住当前这张、右格空；两侧各按**半幅**适配 |
| 双击 / 激活 / 单屏改尺寸 | 只适配活动格（与今天等价） |
| 解码/取图完成 | **显示这张 rec 的格子各适配一次**（`fitSidesShowing`），取代原来三处 `if (activeId === rec.id) fit()` —— 分屏下 rec 可能在**非活动侧**解码完成，旧守卫会让那格的变换停在 `{1,0,0}` |
| 拖分隔线 | 只改比例，**不重新适配**（露出的还是同一个变换）；抓取带另有 4px 判定阈值，见 §10.8 |
| 「回正」 | 比例回 0.5 且两侧重新适配 |
| 退出分屏 | 单幅显示此前活动侧那张，重新适配全幅 |

「拖分隔线不会误触发画布平移」是结构保证的：平移只在 `.view-canvas` 自己的 `mousedown` 里
置位，而分隔线抓取条在画布**之上**把事件吞掉。

### 10.6 待核：产物与输入是两套像素网格

`SR_code` 侧推断产物 tif 是输入影像尺寸的 **2 倍**（`code_0817_prod.py:566` 的 `scale: 2`、
`util.py:1521-1526`）。**待真机核对。** 若如此，分屏两侧是「同一地面区域、不同像素网格」，
对齐只能按**百分比**（`normAnchor` 那套归一化锚点正是为此选的），±1 像素级的比对做不到，
只有区域级 / 目视级。**这一点要写进给用户的说明里，别让人以为是对齐精度损失或 bug。**

### 10.7 换图时的视图保持：相对视野守恒（2026-09-20）

真机反馈：点选对比里每换一张图，缩放与位置都回到初始形态。根因是三处无条件 `fit()`
（`activate` 与 `afterPixels` 的既有写法）——那是为「新打开一张图该看全幅」写的，但对比
场景要的是**同一片地面来回看**，一 fit 就把用户摆好的位置抹了。

语义定为**按相对视野换算**（`lib/viewMath.ts::remapViewForImage`）：归一化坐标 = 缩略图像素
÷ 该图自己的缩略图尺寸，于是「归一化」等价于「整幅图的相对位置」，两张尺寸不同也指着同一片
地面。三条实现纪律：

- **以水平为准**：两格左右并排，宽度是稀缺维度 —— 归一化可视宽度铺满格子宽度，纵向按新图的
  比例自然延伸。同长宽比、不同分辨率时 `ox`/`oy` **一个数都不动**、`scale` 按像素比缩放
  （`scale × 图宽` 不变，这正是「同一片地面」的代数表达）。
- **两张缩略图尺寸逐字相同时原样返回入参**（`return view`，不做一次除法再乘回来）：
  IEEE double 下 `100/1314*1314 = 99.99999999999999`，不早退的话「同尺寸换图视图不变」这条
  断言只能写到 `1e-9`，而它本该是逐字相等。
- 退化输入（`scale<=0` / 宽或高为 0 / 格子尺寸为 0）**原样返回**：不动比乱动好。

**记账靠 `viewFor`，而不是「上一个 rec id」。** store 里一个普通对象记着**每格当前的
`ViewState` 是按哪个缩略图尺寸算出来的**（`null` = 这格空着 / 还没适配过）。为什么记尺寸：
真正决定「能不能 remap」的是「此前摆在这格的是多大的图」，而 rec id 会漏掉两类情况 ——
同一张图换格子（图没变、格子变了）、以及两格互换。判据 `compareOn && viewFor[side]` 为真
才 remap，否则老实 `fit`：

| 路径 | 动作 |
|---|---|
| `fit()` / `fitSide()` | 适配后写 `viewFor[side] = {w,h}`；rec 没 `thumb` 直接 return，**不写** |
| `activate`（换到另一张 / 已解码） | `fitAfterImageChange(activeSide)` —— 对比模式内 remap，其余 fit |
| `afterPixels`（像素到位） | 分屏两支各按格算；单屏那支仍是 `if (compareOn) … else fit()` |
| 进/退分屏、`removeRec` | 清掉对应那格的 `viewFor` |
| 换档位重取像素（同一张图） | 走 `afterPixels` → 尺寸**可能变了**，按 remap 处理（不是 fit） |

三条容易忽略的后果：

1. **空格落图仍然 fit** —— `viewFor.B` 为 `null`（右格刚进分屏时是空的），落进去的图经
   `fitView` 老实适配。`test-vue-viewer.js` 里那条既有断言钉的就是它，是刻意保住的。
2. **关闭模式一字不改** —— `compareOn === false` 时一律走 `fitView`，与改动前逐字等价；
   e2e 里专有一条「关闭模式换图必须 fit」的反向断言守着。
3. **两格互换按「视图跟格走」**（不跟图走）：互换是罕见路径（点清单里已在另一侧的那张），
   跟格走的语义可预期，代码也不用把 `view` 跟着换。要改的话两格都得同时 remap。

另外把「关掉的图」这条路补齐了：`removeRec` 清 `viewFor`（含「两格都空」那条分支），
不然「关掉 A 格、落一张新图进 A 格」会拿着一张早就不在格里的尺寸去 remap。

### 10.8 「在画面里拖动」与「换格 / 改分隔」的三种撞法（2026-09-21 真机反馈）

用户报：分屏对比时在右格画面里拖动换区，与「两图调换」撞了，前者高频触发后者。查下来是
**三条互不相干的通路**，各修各的。先说结论：平移本身换不了格 —— 唯一能换格的是
`placeRec` 的互换分支（§10.7 的「两格互换」），入口只有两个：把**正在另一侧
显示**的那张文件再拖到这一半、点文件列表里**正在另一侧显示**的那张卡片。用户对后者的
期望就是互换（「落点那半放新图、另一格接住原来那张」），**互换保留不动**。

（订正，2026-09-21：第一条入口当时**实际是断的** —— `.file-item` 没带 `draggable`，
左栏卡片压根发不出 `dragstart`，只有从资源管理器拖进来的文件走得到画布。所以那天「实测
能复现」的只有点卡片那一条；见 §10.9。）

**(1) 根因：解码遮罩是画布区里唯一带可选文字的一层，却没禁选中。** 遮罩
（`.decode-mask`）`position:absolute; inset:0` 盖住整块画布。代码事实：它当时没有
`user-select: none`，而画布区的其它层（`.cmp-overlay` 的标签/空位提示、`pointer-events: none`）
本来就不可能起选择。推断（Chrome 行为，本仓 e2e 抓不到 —— 遮罩只在小图解码那几毫秒里挂着，
脚本按不住）：在遮罩上按下拖动先给「正在读入图片…」这类文字拉出一个文本选择（画布收不到
`mousedown`，平移一动不动），此后**每一次**压在选中文字上的拖动都被当成「拖选中内容」走
原生 drag-and-drop → 页面收到 `dragover` → 落位提示「放在左侧 / 放在右侧」亮起。用户看到的
就是「在画面里拖一下，就冒出换格提示」。修法：`user-select: none`，**只禁选中、不放开点击**
（生成掩码 / 合并期间画布不该收 `mousedown`；`onCanvasDownDraw` 没有 busy 守卫）。

**(2) 落位提示只对真能落图的拖放亮（实测）。** `onDragOver` 原来无条件
`setDragHint(true, side)`，而 `onDrop` 对空 `files` 直接 return —— 页面内拖放（拖选中文字、
拖链接）能亮提示却什么都放不进来。判据：`e.dataTransfer.types` 里有没有 `'Files'`
（dragover 阶段看不到文件名，但看得到类型）。`preventDefault` 照旧无条件执行（不拦会导航）。
**订正（2026-09-21）**：这条判据现在是 `dragHasPayload` = `Files` **或**
`application/x-sr-rec`（左栏卡片自己带票，见 §10.9）—— 只认 `Files` 的话，卡片拖动这条
新路的落位提示就永远不亮。仍不认 `text/plain`：拖选文字照旧不亮。

**(3) 分隔线抓取带没有判定阈值，按下即按指针位置改比例（实测）。** 刚进分屏时线正好落在
画布正中，随手在中间按下拖动很容易压在 9px 抓取带上：那一下既不平移、比例又跟着指针跳 ——
画面里看到的是「图不动、左右在换」（§10.5 说的「抓取条在画布之上把事件吞掉」是刻意的，
问题是吞掉之后没有阈值）。改成：按下只记 `startX` / `startRatio`，**挪不到 4px 什么都不做**；
过阈值后按**位移增量**（`startRatio + Δx / 宽度`）改比例，线不再被吸到指针上（旧行为按指针
绝对位置算，按下点离中线 3px 就先多算 3px）。e2e 两条断言分别钉「阈值内不动」与「按位移不按
绝对位置」—— 同一次拖动期望值 0.5219，按绝对位置会得 0.5252。

e2e 断言（`test-vue-viewer.js` 分屏段）：叠加层文字上起手拖动「不产生文本选择 + 照样平移」、
不带 `Files` 的 dragover 不亮提示（紧跟一条真拖文件的对照断言，别把整条路一起关掉）、
抓取带阈值与位移增量。`lib/drag.js` 为此加了 `dragOverText`（只带 `text/plain` 的 dragover，
复刻页面内拖放那一种）。

---

### 10.9 左栏卡片拖进画布 + 中间产物只读（2026-09-21 用户反馈）

**(1) 卡片拖动这条链原本整条不存在。** 漏在两处，缺一处都等于零：`.file-item` 没有
`draggable`（浏览器连 `dragstart` 都不发），画布的门只认 `dataTransfer.types` 里的 `'Files'`。
修法用**自定义 MIME** `application/x-sr-rec` 传 rec id，而不是 `text/plain` —— 后者会让
「在画面上拖选一段文字」也被当成换图。`dragstart` 由卡片自己调
`store.startRecDrag(rec, e)` 填票（测的时候要**让卡片自己填**，脚本替它 `setData` 就把被测
那段绕过去了），`dragend` 收尾清落位提示；`onDrop` 在 `files.length` 那道早退**之前**先读
`REC_MIME`。落格仍由 `dropSide(e)` 给，分屏下按落点进格、对比模式下拖到画布外沿用那句 toast。

**(2) 同一景的三类图要在列表里分得开。** 名字行后跟三颗小标：`盘阵` + **序号** + **环节**
（`PAN` / `SR` / `NOSR`），序号相同 = 同一个场景目录。**序号的分组键是「场景目录」，不是
`lqPath`** —— 中间产物的 `lqPath` 被服务端置空（见下），拿它分组会把产物全归到 0 号。所以
`ViewerRec` 里另有一个与 `lqPath` 正交的 `sceneDir`（知道了就一定写，与能不能提交无关），
`FileList` 只在 `rec.sceneDir` 非空时渲染那颗序号标。环节标本身是纯函数
（`lib/stage.ts::stageLabel`）：本体看文件名是不是 `PAN`（RC 场景标 `PAN`，SC 的生产全名标
`本体` —— 四十来字塞进标签没意义），产物用后端给的 `suffix` 大写，上一次产物恒标 `NOSR`。

**(3) 中间产物是只读的，这件事必须让人一眼看出来。** 掩码与 SR 都建在**本体影像的网格**上，
产物是它的放大结果（各边 2×），在产物上画的坐标写到本体掩码文件上整片都是错的。
三颗修复入口（绘制掩码 / 保存掩码到盘阵 / 提交 SR）一律置灰，`title` 给出人话理由
（「本图像是中间产物（SR），仅用于对比，不作修复 —— SR 与掩码都建在本体影像的网格上，
请先打开本体」），卡片上另有一颗「仅对比，不作修复」的灰标。**按钮置灰只是那一层**：
store 里那三道门是兜底（键盘快捷键、程序化调用），服务端把 `row.lq_path` 置空才是第一道 ——
理由见 [api-contract.md](../planning/api-contract.md) §3.5：产物尺寸的 W/H 配上本体的
lq_path，会把一张产物尺寸的掩码**静默写到本体的掩码文件上**。

**(4) 拖 jpg 之后后台静默烤一份 `_preview.jpg`。** 不阻塞、不弹遮罩、不报进度，展示像素
仍用用户拖进来那张（那张更清晰，不该被服务端缩图顶掉）。落点与缓存那份 `.preview.jpg`
**是两套命名**，别混：见 [preview-bake-pipeline.md](../knowledge/preview-bake-pipeline.md) §4.10。

### 10.10 顶栏品牌区：平台名 + logo 预留区（2026-09-21 用户要求）

**(1) 名字只写一处。** `src/lib/brand.ts` 的 `APP_NAME`，顶栏与路由的 `document.title` 共用。
`frontend/index.html` 的 `<title>` 是 JS 起来之前的字面量、`import` 不进去，改名要一并改
（只影响首屏一瞬与禁用 JS 时）。仓库目录名、文档标题、`deploy/` 里的 `sr-agent-platform.conf`
沿用工程名不动 —— 那是工程标识，跟产品名不是一件事。

**(2) logo 不进仓库，它是「在服务器上换」的文件。** 正式素材在内网机上，所以
**部署态就只有「没有 logo」这一种**，改法全由这一条推出来：`<img>` 拿不到文件时由 `@error`
自摘，退回 `.brand-mark` 里那颗占位小方块（原来 `.brand::before` 那颗白点，搬进预留区居中）
—— 不摘就是一张破图。摘掉后不重试以免刷请求，服务器上补了要硬刷一次页面。src 用
`import.meta.env.BASE_URL + 'logo.png'` 拼（产物里编译成 `./logo.png`），与 `vite base:'./'`
同一条规则，解压到子目录也跟得上；**写死 `/logo.png` 就只在根部署对** —— 这是「产物能丢到
任意 root/子目录」那条既有约定在新增文件上的延续，不是特例。

**(3) 「预留」预留的是高度，不是宽度。** `.brand-mark` 是 `min-width: 28px` + `height: 28px`：
没有 logo 时占住 28×28；方形 mark 填进去名字**一个像素都不动**（实测两种状态名字 `left`
都是 62）；横长字标才向右撑开（120×28 → 名字移到 154）。素材不在手上，宽高比预不成一个死宽，
这是唯一诚实的做法；`max-width: 160px` 挡异常素材把名字顶出屏。

**(4) 字号只动一个。** 平台名 16 → 18px；`.nav-links a` 的 14px 一个字不动 —— 前者的角色是
「这是哪个平台」（锚点），后者是「去哪儿」（导航），两者不该同级。e2e 把四项逐个量了一遍。

**(5) 旁支：一条空跑了很久的断言。** `.e2e/check-frontend-build.js` 是 Phase 2 的验收脚本，
不在 `.e2e/package.json` 的任何 `test*` 里，没人随改动重跑 —— 它第 2 步钉着按钮文案
「选择 TIF」，而按钮从 `ba8e61d` 起就叫「选择影像…」，从那天起脚本每次都死在第 2 步，
第 3 步之后的解码/渲染断言等于长期没测。**没被任何脚本调用的验收脚本会烂掉**：判据取
「意图」（有选择入口）而不是「文案」，才不会随着改文案再烂一次。

---

## 11. 一句话备忘

> **浏览器看大 tif：懒读 IFD → 估内存 → 超限分块窗口读 + 现场降采样；非 8bit 一律 min-max 拉伸、alpha 永不缩放、WhiteIsZero 反色；失败看"错误文本 + 属性（bits/类型/压缩码）"定位，别猜。**
> **掩码：顶点=像素中心边界含入；扫描线逐多边形 even-odd + UNION（重叠区奇偶判反坑）；魔棒屏障在像素"之间"只拦跨越；浏览器直出掩码流式逐行 Deflate 可行；产物统一 掩码.tif(0/255) + 掩膜中心点坐标.txt(质心,CRLF)。**
> **盘阵（阶段4）：浏览器不读盘阵 TIF，读服务器烘焙 JPG（v3 = 各边 ÷2…÷32 五档可选、默认 ÷4 + 直方图均衡 + 规则戳写进 JPEG 注释，档位也进戳）——后端稀疏采样映射/拉伸/const 规则全镜像前端语义；scene id = base64url(rel)、路径白名单 realpath 校验拒 `../`/fake；jpg rec 的 stats 固定 0..255 让交互拉伸恒等；掩码换算走元数据 W/H；裸 `.tif` 能看但父目录不是场景目录就不能提交 SR；拖入链的产物落场景目录 `<stem>_preview.jpg`（写不进去才退临时缓存），前端靠行上 `previewDiv` 判「盘上那份是不是当前档位」。**
> **图像对比（阶段5）：一个画布、两个裁剪矩形 + 每侧一套 `ViewState`，**每格的变换用该格自己的局部坐标**（渲染时 `translate(rect.x, rect.y)`）——`fitView`/`locateView`/`visibleThumbRect`/`mouseToThumb` 因此原样复用；锚点一律走「归一化位置」而不是画布局部坐标（多加一个 `rect.x` 会让另一格整体平移一个左格宽，e2e 抓过一次）；`view` 是按侧的可写 computed，`activeId` 仍是「活动侧那张」所以约 40 处消费点零改动；**单屏快速路径保留字面写法**（`100/1314*1314 ≠ 100` 会扰动画布中心像素采样的既有断言）；拖放门只在对比模式下收窄到画布内，`.txt` 恒通、`dropEffect` 恒 `copy`。**
> **换图保持视图（阶段5，§10.7）：对比模式下换图按**相对视野**换算（归一化中心 + 归一化可视宽度守恒，以**水平**为准），靠 `viewFor` 记「这格的 `ViewState` 是按哪个缩略图尺寸算的」——记尺寸不记 rec id；同尺寸**逐字返回入参**（`100/1314*1314 ≠ 100`）；空格落图与关闭模式仍是 `fit`；取图侧另有「芯片去重 + 预览 blob 按字节封顶的 LRU + 进对比模式可预取（默认关，只取已烤好的那几档）」。**
> **画面里的拖动（阶段5，§10.8）：画布区里唯一带可选文字的那层（解码遮罩）必须 `user-select: none` —— 漏了就是「拖一次选中文字、此后每次拖动都被 Chrome 当原生拖放」，落位提示「放在左侧/右侧」跟着乱亮、平移还一动不动；落位提示只对真能落图的 dragover 亮（`dataTransfer.types` 带 `Files` **或** `application/x-sr-rec`；页面内拖选文字同样发 dragover 却什么都放不进来）；分隔线抓取带按下先不动，挪过 4px 再按**位移增量**改比例（按指针绝对位置会把按下点离中线那几像素先算进去）。**
> **左栏卡片与中间产物（阶段5，§10.9）：卡片拖进画布靠自定义 MIME `application/x-sr-rec` 自己带票（不用 `text/plain`，否则拖选文字也换图）；同一景的三颗小标「盘阵 + 序号 + 环节（PAN/SR/NOSR）」，**序号按场景目录分组**而不是按 `lqPath`（产物的 `lqPath` 被服务端置空）；中间产物只读 —— 掩码与 SR 都建在本体网格上，产物尺寸的掩码配本体的 lq_path 会**静默盖掉本体那份掩码**，所以服务端置空 `lq_path`（第一道）+ 前端置灰（第二道）；拖 jpg 之后后台静默烤 `<stem>_preview.jpg`（与缓存那份 `<stem>.preview.jpg` 是两套命名）。**
> **顶栏品牌区（§10.10）：平台名在 `src/lib/brand.ts` 写一处，`index.html` 的 `<title>` 要一并改（import 不进去）；logo 素材不进仓库，**部署态就是「没有 logo」**——`<img>` 报 error 时自摘退回占位小方块，别留破图；预留的是**高度**不是宽度（`.brand-mark` 28×28 min-width：方形 logo 填进去名字不动、横长字标向右撑开）；路径走 `BASE_URL + 'logo.png'`（`base:'./'` 下是 `./logo.png`），写死根路径就只在根部署对；改名与换 logo 都改不到两处「顺手的地方」——`index.html` 的字面量与服务器上的那个文件。**
