# current_question — 当前问题解决时间线（显示遥感图像）

> 用途：**窗口交接文档**。下一个 Claude 窗口先读本文件，再读 `docs/experience/gui-experience.md`（经验），即可无断点继续。

---

## 当前状态（一句话）

**真实文件布局已确认**：GF07A03（1.11GB）与 KF02B04（1.78GB）均为**无压缩 · 条带1行**的单波段 16bit 影像。已实现**稀疏条带预览**（无压缩大图秒级出预览，只抽读少数条带做字节切片，不碰全文件）：134MB 测试图从全量 4.5s → **1.3s（读 32MB/128MB）**，预计真实 1.1GB 文件从 ~2 分钟降到秒级。**2026-08-29 已修 JPG 导出三 bug**（p2 崩溃 / 右下滑条纹 / 分辨率 2048→8192）；随后**稀疏条带预览显示分辨率提到 8192**（`SPARSE_PREVIEW_MAX`，≈原始 1/3，与导出 JPG 同清晰度，解决"网页显示不如本地 JPG 清晰"）。**下一步**：放大看细节时按可视窗口读全分辨率（无压缩切片，无需金字塔）；真机验证导出与 8192 显示。**2026-08-31 前端已落地掩码绘制**（tif_viewer「绘制掩码」模式：矩形/多边形画 ROI → 导出矢量 JSON → `python -m backend.services.mask` 出掩码.tif + 01点阵.txt，详见下文「掩码绘制」）。另注意：开发机 e2e 浏览器仍无法启动（环境问题，见 08-29 条目）。

## 平台化技术栈方向（2026-08-30 · 定调讨论）

> 结论先行：前端 React+TS（**移植不重写** tif-viewer 逻辑）；后端 FastAPI + 自建作业队列 + 工具库；**LangChain 非地基，后置为可选薄层**。四问已答：LLM 底座**未定**（按 OpenAI 兼容写，可替换）、**无 SLURM**（自建队列）、cv 工具箱**从零开发**、近期**先包工具库（P1）**。

### 技术栈决策

| 层 | 选型 | 理由 / 要点 |
|---|---|---|
| 前端 | React + TypeScript（Vite 构建 + Nginx 静态托管，离线 vendor） | 多视图 + 共享状态 + 实时进度是 React 主场；`naming-conventions §4` 已预留 `TifCanvas.tsx`/`tifDecode.ts` 结构 |
| 前端迁移 | **移植不重写** tif-viewer | 116KB HTML 里已踩完坑的解码/拉伸/稀疏条带逻辑抽成**框架无关 TS 模块**，React 应用 import 它，别重写 |
| 后端框架 | FastAPI（REST + SSE） | Agent/SR/cv 工具全在 Python，避免双语言；SSE 推任务进度 |
| 作业队列 | 自建：FastAPI(API+SSE) + **独立 worker 进程**（跑 GPU）+ SQLite 作业表 | 无 SLURM；SR 是阻塞 GPU 进程，必须独立进程跑；单机 MVP 用 SQLite 免装 Redis，多机/高并发再升 Redis+Celery/ARQ |
| LLM 底座 | **未定**，统一按 OpenAI 兼容 `/v1/chat/completions` + tools 参数写 | 底座可换（Ollama→vLLM/DeepSeek 不改业务码）；有第二台 GPU→vLLM+Qwen2.5-14B，仅单 3060→Ollama+Qwen2.5-7B Q4 与 SR 错峰 |
| Agent 层 | 先不碰 LangChain 全家桶；原生 function-calling + 自写 ~200 行状态机 | 撞到跨小时级断点续跑/人审再上 **LangGraph**（此时是替换薄层，非重写）；`langchain-master` 当参考不引为依赖 |

### 优先级 P0–P4

- **P0 阻塞**：定 LLM 底座 + 定作业队列实现 + 盘点 SR/cv 工具能否无头调用。
- **P1 工具库先行（当前在做）**：见下，纯价值零 LLM。
- **P2 FastAPI 骨架**：REST + SSE + 作业队列，工具暴露成端点（无 LLM 即可联调）。
- **P3 React+TS 前端骨架**：聊天 + 队列 + 移植 TIF 查看器 + 盘阵检索/查看 UI。
- **P4 Agent 编排层**：最后加，native function-calling 状态机或 LangGraph。

### P1 交付物（工具库）

1. `tools/` 包 + **工具契约**：`name` / `description` / JSON-schema params / `run(**params)->result`。
2. 首批工具：
   - **SR 侧（已有代码纯包装）**：`grid_offset_planner`（code_0820 里纯 numpy，直接抽最快见成效）、`run_sr_inference`（包 `code_0820_prod_windows.py` 的 main 为无头调用）、`run_all_folders`（内网，需回传/重写）、掩码生成（掩码.tif + 01点阵.txt）。
   - **cv 侧（从零，先做一个定契约）**：如 `fix_bad_lines`（坏行/条带修复，OpenCV/numpy）。
   - **盘阵检索**：`search_scenes`（先定接口 + 本地假实现，真机再接盘阵）。
3. 注册表（在 `tools/contract.py` 内，`@tool` 装饰器自动收集）→ `manifest()` 生成 OpenAI 兼容工具清单 JSON（日后直接喂 agent function-calling + 自动生成 API 文档）。

### P1 进展（2026-08-30 → 08-31 · 粒度校正）

> **校正**：`grid_offset_planner` 是 `run_sr` 全流程里的局部函数，**不该当 agent 工具**。工具粒度 = 流程级操作（run_sr / 批量 / 检索 / cv 修复），内部积木归 `mta_grid/`/`services/`。

- ✅ `backend/mta_grid/grid_planner.py`：忠实移植 `_count_sr_tiles` / `_grid_offset_planner`（纯 numpy，作 `run_sr` 的积木）。纯算法测试仍全过。
- ✅ `backend/tools/contract.py`：工具契约 `Tool` + `@tool` 注册表 + `ok/err` + `manifest()`（契约成立，首个真实工具待流程级）。
- ❌ 已移除 `backend/tools/grid_planner.py`（误把积木当工具的包装）。
- **分层定论**：`services/`（流程编排，`run_sr` 在此组合 mta_grid + 推理 + util）→ `tools/`（流程级 agent 工具，薄壳调 services）→ `mta_grid/`（纯算法积木）。

### 掩码绘制（前端已落地 · 2026-08-31）

- **需求**（gui-requirements G1/P0）：本地 HTML 交互式绘制 ROI 掩码，输出 `掩码.tif`（**分辨率与原图一致**）+ `掩码01点阵.txt`。
- **核心矛盾**：预览/JPG 是降采样（稀疏 8192≈1/3、其余 2048）+ 8bit 拉伸，而掩码要全分辨率。**JPG 的有损/拉伸不影响掩码几何（掩码只关心位置 0/1），只有分辨率影响精度。**
- **设计倾向**：HTML 只出**矢量多边形**（在预览画布上画，顶点坐标按 scale 映射回原图像素）；**全分辨率栅格化放 Python**（numpy + tifffile/cv2 填充多边形 → 原尺寸 掩码.tif），因为 24739×24199 掩码 ≈ 6 亿像素，浏览器扛不起、且 `run_sr` 本来就要在 Python 拿掩码。
- **已定**：① 栅格化 **Python 从零写**（`backend/services/mask.py`，Pillow 填充，已实现 + 12 测试全过）；② 精度**混合**——先在 8192 预览粗画，后续做「按可视区读全分辨率」后放大精画。
- **矢量格式（临时，HTML 产出）**：`{"width":W,"height":H,"polygons":[{"label":"roi","points":[[x,y],...]}]}`，x=列、y=行（原图像素坐标）。填充约定：顶点为像素中心、**边界含入**（方形 [2,2]..[7,7] 覆盖 6×6）。
- **已验证**：`python -m backend.services.mask p.json mask.tif mask.txt` 冒烟通过（多边形→掩码.tif + 01点阵.txt）。
- **✅ 前端已实现（tif_viewer/tif-viewer.html）**：工具栏「绘制掩码」按钮 → 进入绘制模式（光标变十字）→ 顶部浮动面板选「矩形/多边形」工具，在叠加层 `#drawCanvas` 上画多个 ROI（矩形拖拽、多边形点击加点 + 双击/回车/右键闭合 + Esc 取消、撤销/清空）。顶点存**缩略图坐标**，渲染时按 `(ox,oy,scale)` 映射回屏幕，随平移/缩放走。
- **坐标映射**：导出时 `orig = thumb × (W-1)/(tw-1)`（与稀疏采样 `mapX/mapY` 同源），`buildMaskJson()` 产出 `{width,height,polygons:[{label,points:[[x,y]…]}]}` → 「导出掩码JSON」下载 `<名字>.mask.json`。E2E 钩子已挂 `window.__viewer.{enterDraw,exitDraw,buildMaskJson,exportMaskJson,thumbToOrig,getRois}`。
- **端到端已验证**：前端形状的 JSON（含 label 字段）经 `python -m backend.services.mask` 冒烟 → 掩码.tif + 01点阵.txt（20×10 双 ROI=61px 正确）；HTML 内联 JS 经 `new Function` 语法检查通过。
- **剩余**：①「按可视区读全分辨率」的放大精画路径（当前仅预览粗画，符合已定混合精度）；② 01点阵.txt 全分辨率 ≈ W×H 字符（24k² 图 ~600MB），格式待与现有掩码工具对齐确认。

### 下一步（平台侧）

1. 确认 LLM 底座可选项（内网是否有第二台 GPU 服务器 / 仅单 3060）。
2. 定作业队列最小实现（建议 SQLite + 独立 worker 进程）。
3. 盘点内网 SR 工具清单（run_all_folders / 掩码工具在不在本仓库、能否无头跑）。
4. 动手 P1：先抽 `grid_offset_planner` → 定工具契约 → 做第一个 cv 工具 `fix_bad_lines`。

> 注：`~/.claude/.../memory/` 当前为空，而 docs 里引用过 browser-2gb-alloc-cap / local-vendor-libs-for-viewers / intranet-data-inaccessible 三条记忆，疑似换机后未重建，待补。

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
8. 文档落盘：`docs/experience/gui-experience.md`（经验）、`.gitignore`（屏蔽 test-tifs/.e2e/*.zip）、内存文件更新。

### 2026-08-28 · 稀疏条带预览（大图加速第一刀）
- 用户回传探针：GF07A03、KF02B04 均为**无压缩 · 条带1行**。
- 2 分钟之谜根因确认：旧路径把全图全量读一遍做预览，耗时 ∝ 字节；且 geotiff 窗口读在 1 行条带上每窗口触发 ~4096 次小 slice（42 窗口 × 4096 ≈ 17 万次切片读）。
- 实现 `parseStrips`（头部解析条带偏移/长度数组，兼容 classic/BigTIFF）+ `sparseCollect`（按条带字节切片，抽 ph 行 × 抽样列，8 路并发，进度条）；`stripPxVal` 支持 8/16/32bit 的 uint/int/float。
- 路由：`无压缩 + 条带 + 单波段 + >100MB` → 稀疏预览；否则原分块/UTIF 路径不变。
- E2E `test-sparse.js` 全过（像素精确 + Deflate 回退）；`test-regress/stretch/stretch8/locator/types/probe-list` 全过无回归。
- 134MB 8192² 测试图：稀疏 **1.3s**（读 32MB/128MB）；原全量 4.5s。big_u16 也自动转稀疏（1.5s）。

### 2026-08-29 · JPG 导出三 bug 解耦修复（稀疏大图）
- 用户报：导出 JPG 报 `Cannot read properties of undefined(reading 'p2')`；预览/导出分辨率停在 2048×2003（位图过低，想要 ~1/3）；右/下大片拉伸条纹。要求解耦分步排查。
- **Bug A（导出崩溃，根因）**：`sparseCollect` 的 silent 返回 `nbands:1`，而 `exportToJpg` 读 `c.nb` → 未定义 → `stretchRgba` 误走 3 波段分支 → `stretchMap` 在 `st[1]`（不存在）上读 `p2` → 崩溃。修：silent 返回补 `nb:1`；另给 `stretchMap`/`stretchRgba` 加兜底（统计缺失时按值截断返回，绝不抛错）。
- **Bug C（右/下条纹，根因）**：`sparseSample` 用 `ceil(W/pw)` 定步长 + clamp 到 W-1 → 右/下各 ~145 列/行重复最后像素。修：改目标→源线性最近邻映射 `round(j*(W-1)/(pw-1))`，端点精确对齐。
- **Bug B（分辨率）**：用户看到的 2048×2003 是预览（`PREVIEW_MAX=2048` 的产物）；导出因 Bug A 从未成功。修：默认导出长边 `JPG_MAX` 16384→**8192**（24739 宽 → 8192×8013 ≈ 原始 1/3），同时规避 16384² 画布面积/toBlob 边界；失败降档重试同步 8192→4096。
- 同步：`docs/knowledge/jpg-export-background.md` 默认值/降档说明；`.e2e/test-sparse.js` 注释（新映射下断言仍成立，`round(i*8191/2047)=4i`）。
- ⚠️ **待验证**：开发机 e2e 浏览器启动仍失败（puppeteer-core 25.9.0 + 无头 Edge，Code: 0，环境问题，与本次改动无关）；真机（内网）验证：打开 24739×24199 图 → 预览无条纹、2% 线性导出得 8192×8013 JPG 且无报错。

### 2026-08-29 · 网页显示清晰度 = 本地 JPG（稀疏预览 2048→8192）

- 用户报："同样的 jpg，本地目录预览清晰度远超 html 网页显示"。根因：导出 JPG 是 8192（≈1/3），而**网页稀疏预览仍是 2048**（`PREVIEW_MAX`），所以页面图被放大看就糊。
- 方案（用户选定「网页按 8192≈1/3 直接显示」）：新增 `SPARSE_PREVIEW_MAX=8192`，仅**稀疏条带路径**预览目标长边 2048→8192；`sparseSample` 默认目标改为该常量。chunked/UTIF 路径保持 2048（压缩/小图预览仍快）。
- 代价与约束：稀疏预览 8192² 时 src(Float32)+canvas ≈ **0.5GB/图**，避免同时开过多大图（已写入 gui-experience §4 内存提示）。
- 同步：`.e2e/test-sparse.js` 与 `.e2e/test-regress.js` 预览尺寸断言 2048→8192（测试图为 8192²，无压缩命中稀疏，全分辨率恒等映射）；`docs/knowledge/jpg-export-background.md` 预览降采样说明改口（稀疏 8192 / 其余 2048）。
- ⚠️ **待真机验证**：8192² 稀疏预览在真实 24739×24199 图上的内存/耗时；e2e 本机仍无法启动（环境问题）。

## 下一步（给新窗口）

> **环境约束（已写入记忆）**：开发机是**外网机**，真实遥感图全在**内网机盘阵**，无法导出/复制/读头探测。文件结构只能靠**用户回传 ENVI 头信息**（Edit Headers：Compression/Interleave）或**尺寸推断法**。不能要求用户给文件路径。

1. **等用户回传**（两种途径，用户二选一）：
   - 用当前 `tif_viewer/tif-viewer.html` 打开 (a) 400MB 小图、(b) 报解码失败的大图，把状态栏**完整报错文本**发来（含 `[属性 W×H，bits/spp，类型，压缩code]`）。
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

- 主交付物：`tif_viewer/tif-viewer.html`（UTIF 小图 / geotiff 分块大图 / **稀疏条带预览** 三路分派）
- 经验文档：`docs/experience/gui-experience.md`
- E2E：`.e2e/test-sparse.js`（稀疏）、`test-regress.js`、`test-types.js`、`test-stretch.js`、`test-locator.js`（puppeteer-core + 无头 Edge）
- 测试图：`test-tifs/`、`test-tifs/types/`、`test-tifs/sparse/`
- 内存：`~/.claude/projects/.../memory/`（browser-2gb-alloc-cap、local-vendor-libs-for-viewers、intranet-data-inaccessible）
