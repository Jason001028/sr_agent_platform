# 通用影像预览（盘阵外 TIFF）P1

> 日期：2026-10-31 · 状态：服务端与前端换档位已落地；真机盘阵外源待核
>
> 本文说明「盘阵外授权 TIFF 也能预览」这条能力的边界、实现、实测数据与剩余限制。
> 盘阵内的业务链路保持原样，未重构 Viewer、Agent Loop 或 SR Executor。

## 1. 目标与边界

需求是让预览能力与「这张图是不是盘阵场景」解耦：

- 盘阵外、服务端已授权的 TIFF：能打开、缩放、平移、下采样，沿用 Overview 优先与回退；
- 盘阵内的场景：业务链路不变（检索、预览、掩码、提交 SR、产物验收）；
- 浏览器本地 TIFF：仍走既有前端解码，不上传、不改解码框架；
- 普通 JPG/PNG：仍走原加载链，不套 TIFF Overview 逻辑。

权限按**能力**拆，而不是按「来源是不是盘阵」一刀切：

| 能力 | 盘阵内合法场景 | 仅 `SR_PREVIEW_ROOTS` 内的源 |
| --- | --- | --- |
| 预览 / 缩放 / 平移 / ROI / Agent 读图 | 是 | 是 |
| 写掩码、提交 SR、场景检索、产物验收 | 是 | 否 |
| 场景目录解析（`POST /api/scenes/resolve` 的目录形态） | 是 | 否（只接受单张 TIFF） |

`capabilities.preview / roi / agent` 对能打开的影像恒为真；`mask_write /
sr_submit / scene_search` 只由「合法场景目录 + 盘阵前缀」决定。

## 2. 实现

### 2.1 两套前缀

`backend/pathguard.py` 新增：

- `SR_PREVIEW_ROOTS`：盘阵之外、**只读预览**的根，`;` 分隔，缺省空；
- `preview_roots()` / `is_preview_allowed()` / `is_preview_only()`；
- `ensure_preview_allowed()`：查看链路专用，接受「盘阵前缀 ∪ 预览前缀」。

`ensure_allowed()`、`normalize_submit_path()` 的语义未变，仍只认
`SR_ALLOWED_ROOTS`。提交 SR、写掩码、siblings、清除预览缓存、场景检索都继续走后者。

### 2.2 解析与能力字段

`backend/api/app.py`：

- `POST /api/scenes/resolve` 的 `{path}` 分支改用 `ensure_preview_allowed`；
  单张 `.tif/.tiff` 可以来自盘阵外；
- 目录形态仍由 `ensure_allowed` 校验：目录解析代表「可提交场景」，盘阵外目录直接 403；
- 裸 TIFF 的 `sr_capable` / `lq_path` / `mask_path` 增加「文件在盘阵前缀内」这一道门；
- 响应的 `resolved` 增加 `preview_only` 与显式 `capabilities`，调用方不必从来源反推。

**同名栅格的替换只对显示件 JPG 生效。** `sibling_raster_path` 的候选顺序是
`.tif` 在前，对 `same.tiff` 会先命中 `same.tif`；它只用于「这张 jpg 要不要改从
同名栅格生成」。`/preview`、`/spatial-identity`、`/preview-drop` 统一走
`_display_sibling_raster()`：非 `.jpg/.jpeg` 原样保留，且替换后的实际读取路径
再按对应白名单校验一次（预览链路 `ensure_preview_allowed`，拖入写盘链
`ensure_allowed`），符号链接逃逸同样拒绝。

`backend/api/paths.py`：

- `scene_id_to_abs(..., allow_preview=True)` 只给查看链路（`/preview`、
  `/spatial-identity`）使用；默认值仍是严格盘阵前缀；
- `preview_jpg_for()` 增加第三个落点分支：仅预览源的预览 JPG 不写回外部目录。

### 2.3 缓存布局

| 来源 | 预览 JPG 落点 |
| --- | --- |
| 场景根内 | 既有 `<源同目录>/<stem>_preview.jpg` 或 `SR_PREVIEWS_ROOT` 镜像树 |
| 盘阵前缀内、场景根外 | 既有源同目录（零回归） |
| 仅在 `SR_PREVIEW_ROOTS` 内 | `SR_PREVIEW_CACHE_ROOT/<sha256(绝对路径)[:16]>_<stem>_preview.jpg` |

缺省缓存根是 `<SR_TEMP_PREVIEWS_ROOT>/generic`，未配临时根时是系统临时目录下的
`sr-tmp-previews/generic`。文件名带源路径哈希，两个目录里的同名 `a.tif` 不会共用缓存。

**同 stem 的 `.tif` / `.tiff` 并存时，落点改用
`<stem>_<ext>_<源路径哈希>_preview.jpg`。** 名字只由 stem 拼时两份源共用一份可
覆盖缓存：A 生成完、B 覆盖，A 的响应在发送前可能已经读到 B 的像素。源身份戳
只能保证「生成前」核对过一次，管不住这段窗口，所以冲突时从落点上把两份源分开。
身份段必须含**源路径哈希**：只拼扩展名的 `_tif` 本身可以是另一份源的 stem ——
`same.tif` 的冲突名 `same_tif_preview.jpg` 正好与 `same_tif.tif` 的原名相同，
三份并存时仍会撞。是否冲突只看**固定候选**的 `is_file()`（`same.tif` 与
`same.tiff` 两三个名字），不列目录；没有冲突时保持原名，既有缓存、URL 与
e2e 断言逐一不变。代价是升级前留下的那份共享 `<stem>_preview.jpg` 不再被读取，
不会自动删除（它带本平台规则签名，可用「清除预览」清掉）。

缓存判据在原有「mtime 不比源旧 + 规则签名」之外，规则戳尾部追加
`;src=<路径哈希>-<字节数>-<mtime_ns>`。只靠 mtime 会被「保留时间戳的复制」骗过：
源换了一份内容、mtime 不前移时旧预览仍会命中。规则版本仍是 v4，`;src=` 是可选
尾巴；升级后旧戳没有这一节，会被判废并**一次性**重生成一轮（惰性，不阻塞启动）。
源在生成途中被改写时不落盘，返回 `PreviewError("源在预览生成途中被改写")`。

**行构造也按实际源核对缓存。** `_scene_row` / `_manual_row` / `_raster_preview` /
siblings 的 `item()` 不再用 `jpg.is_file()` 当 `hasPreview`：它们调用
`preview_jpg.cache_meta(jpg, source_rev=源身份)`，版本、档位、质量与 `;src=` 任一
不符就回 `hasPreview=false`、`previewDiv=null`。否则旧戳（没有 `;src=`）或换过源的
缓存仍会以 `hasPreview=true` 通向静态 URL，前端跳过 `/preview` 直接展示错图。
行上同时下发 `srcRev`（源身份），前端预览 blob 缓存键带上它，源换了一份就自然失效。

端点回图改为**字节快照**：`/preview` 与 `/preview-drop` 生成/命中后把 JPEG 读进
内存（预览是压缩显示件，不是整幅原图），核对戳里的**档位与源身份**后一次性返回，
不再交一个「发送时才按路径打开」的 `FileResponse`。只核对档位不够：同档位的另一
份源写进同一个落点时，档位天然相等、图却是错的。源身份取生成后现算的
`source_revision`，与戳里 `;src=` 逐字相同才放行。这样「生成 → 响应发送」期间
即使同名路径被另一请求改写，本次响应也不受影响；真被抢换则整轮再生一次，
`/preview` 两次都对不上回 503，`/preview-drop` 转入临时兜底落点再试。

### 2.4 前端

`frontend/src/components/ScenePathBar.vue` 只改提示文案：路径栏也接受服务端已授权的
盘阵外 TIFF，并说明它是只读预览、不能提交 SR。打开链路复用既有
`resolvePath → fetchSceneJpg → viewer.openSceneJpg`，没有新增 Viewer 或解码分支。

`resolved.preview_only` 随 `SceneOpenMeta` 进入查看器记录（`ViewerRec.previewOnly`）。
工具栏换档位时，仅预览源走只读的 `/preview`；盘阵场景仍走会写回场景目录的
`/preview-drop`。此前两者都走 `/preview-drop`，外部 TIFF 换档位必然 404，界面只剩
「预览切换失败」而旧像素不动。

取图流程也按「档位与字节一致」收紧（`frontend/src/lib/api.ts`）：

- **需要生成预览时**（`previewNeedsBake`）直接用 `/preview` 的响应体作为图像字节，
  不再「先打端点、丢掉响应、再按静态 URL 取一次」。静态 URL 的 `?div=N` 只改变
  浏览器缓存键，不约束盘上内容 —— 两次请求之间另一客户端换档位，第二次取到的
  就是别的档位，却按本档位的键缓存。
- **档位已对上、走静态直出时**，取到字节后读 JPEG 注释里的规则签名，核对档位与
  `srcRev`；对不上（被换档 / 换源 / 旧缓存无签名）就丢弃，退回 `/preview` 的
  响应体。核对只看文件头，不解码像素；不是 JPEG 的字节交给下游解码报错。
- 同一源、同一档位的本地 Blob 缓存键带 `srcRev`（见 2.3），源换一份就自然失效。

## 3. 测试与实测

### 3.1 后端

新增 `backend/tests/test_preview_scope.py`（22 条），覆盖：

- 预览前缀能读、不能提交；`normalize_submit_path` 拒绝外部路径；
- 外部 TIFF 解析出 `sr_capable=false`、`lq_path=null`、`preview_only=true`、
  `capabilities.sr_submit=false`；
- 外部预览落隔离缓存根、源目录不产生 `_preview.jpg`、同档位第二次命中缓存；
- 外部 TIFF 带 Overview 与不带 Overview 都走同一条 `/preview` 并返回正确尺寸；
- 同名不同目录的两份源得到不同 id 与两份缓存，内容不串源；
- 显式 `same.tiff` 不会落到同目录 `same.tif` 上；盘阵内两扩展名**各有独立落点**、
  内容不串，且端点 A 返回后再跑 B，A 的响应字节仍是自己的图；
- `same.tif` + `same.tiff` + `same_tif.tif` 三份并存时落点仍互不相同，三份缓存
  内容不串源；
- 受控交错：A 生成后、A 读快照前 B 覆盖同一落点 → 快照按源身份判废重生成，A 仍
  返回自己的像素（旧实现只核对档位，会返回 B 的图）；
- 行构造按实际源核对缓存：没有 `;src=` 的旧缓存不再回 `hasPreview=true`，
  源保留 mtime 被换内容后行也判废；手工行同样受这套判据约束；
- 源保留 mtime 被替换成另一份内容时，缓存判废并重生成；
- 显示件 jpg 的同名栅格若是指向前缀外的符号链接，预览 404；
- 配了 `SR_SCENES_ROOT` 时外部源仍不落进场景镜像树；
- 盘阵内、场景根外的手工路径仍落源同目录（回归）；
- 盘阵外目录不参与场景解析；两块前缀都不在的路径 403；
- siblings / 清除预览 / SR 提交对仅预览源保持拒绝；只读空间标识可用；
- 绝对路径 id 只有显式 `allow_preview=True` 才能解析。

`backend/tests/test_preview_jpg.py` 另补两条边界：64×1 源在 ÷4 档位下不再
除零，以及源保留 mtime 被替换后重生成；生成文件规则戳的断言改为带源身份。

顺带订正 `backend/tests/test_scene_resolve.py` 的一条旧断言：`PREVIEW_DIVISORS`
早已包含 `1`（原尺寸），该用例仍把 `1` 当非法档位，属于测试未跟上实现。

运行结果：

- 新增文件：`22 passed, 1 skipped`（符号链接用例在无权限的机器上跳过）；
- 相关专项（新增 + siblings + api + api_platform + preview_jpg + preview_clear +
  preview_cache + preview_overview + 场景解析 + 清除预览）：`383 passed, 5 skipped`；
- 全套后端（复核修复后）：`1152 passed, 6 failed, 7 skipped`。失败落在本轮未改动的
  两个模块：5 条 `test_agent_loop` 的 `thinking_status` 多一帧；1 条
  `test_goal_process` 等待进程验收超时（只跑该文件也复现，属既有环境性失败）。
  预览相关专项全绿。

### 3.2 前端

`npm test`：38 个文件、`575 passed`（含 `check:terms`）；新增仅预览源换档位必须走
`/preview` 的回归用例、「源身份进预览 blob 缓存键」的两条单测，以及「需要生成时直接
用 `/preview` 响应体」「静态落点签名对不上则退回端点」的取图回归。`npm run typecheck`
零错误。

### 3.3 性能（本地合成图，页面缓存未清）

4096×4096 uint16 合成阶梯图，Overview 为 nearest 抽取的 ÷2/4/8/16。计时含读头、
采样/Overview、均衡、JPEG 编码与写盘；每项单次，存在噪声，不是稳定基准。

| 档位 | 无 Overview 冷生成 ms | 有 Overview 冷生成 ms | 有 Overview 峰值 MiB |
| ---: | ---: | ---: | ---: |
| 1 | 1391 | 1274 | 281 |
| 2 | 390 | 376 | 213 |
| 4 | 194 | 145 | 87 |
| 8 | 94 | 92 | 55 |
| 16 | 60 | 76 | 47 |
| 32 | 58 | 57 | 46 |

盘阵外源的专项测量（同一 4096² 图，`div=4`，隔离缓存）：

- 无 Overview：冷生成 204 ms，缓存命中 13.7 ms，JPEG 28.7 KiB；
- 有 Overview：冷生成 143 ms，缓存命中 14.9 ms，JPEG 28.7 KiB；
- 两种情况下源目录都没有新增文件，缓存文件名带源路径哈希。

小图在页面缓存命中时，Overview 的收益主要来自少读条带，输出数组、均衡与编码成本
不变；÷1 没有可用 Overview 层，仍按全分辨率处理。真实 GB 级影像与冷盘阵延迟待真机验证。

## 4. 配置

```bash
# 盘阵外只读预览前缀（; 分隔），缺省空
SR_PREVIEW_ROOTS=/data/external;/mnt/archive

# 盘阵外预览缓存根；缺省 = <SR_TEMP_PREVIEWS_ROOT>/generic
SR_PREVIEW_CACHE_ROOT=/var/cache/sr-preview-generic
```

`SR_PREVIEW_ROOTS` 只影响查看链路。不配置时，Resolve / siblings 等路径的额外
白名单检查会直接短路，不增加盘阵内链路的 stat 次数（既有「不扫盘」测试仍通过）。

## 5. 剩余限制

- 浏览器本地 TIFF 没有接 Overview 优先：现有 `chunked` 解码只读全分辨率，
  本地预览也拿不到源文件的 SubIFD，除非改前端解码；本轮判断复用成本不低，未做。
- 盘阵外的目录形态不解析：要打开外部 TIFF 请给单张文件路径；目录解析仍代表
  「可提交场景」，不放开。
- 盘阵外的普通 JPG/PNG 不新增服务端入口；本地图片继续走原加载链。
- 隔离缓存不会随 `SR_TEMP_PREVIEWS_ROOT` 的每日清理删除；源文件删除后缓存会留下，
  目前靠人工清理，后续可加按源不存在的回收。
- 源身份用「路径哈希 + 字节数 + mtime_ns」。同尺寸、同时间戳、不同内容的替换仍
  无法识破 —— 那需要全文件哈希，在 GB 级源上不能每次缓存检查都算。
- 旧版规则戳没有 `;src=`，升级后首次打开每个场景会重生成一轮预览（一次性成本）。
  此前同 stem `.tif/.tiff` 共用的那份 `<stem>_preview.jpg` 也不再被读取，但仍留在
  盘上（可被「清除预览」按规则签名清掉），没有自动迁移/删除。
- `/preview` 改成字节快照后，单次响应会把一张预览 JPEG 读进服务进程内存（压缩显示件，
  通常是几 MB 级；÷1 大图会更大）。这是换「响应不被并发覆盖」的代价。前端「需要
  生成」时也直接消费这份响应体，同样是一次完整 JPEG 经过 API 进程（此前是丢掉响应、
  再由 nginx 直出第二遍，换来的是档位可能被抢换的风险）。
- 前端静态取图后的签名复核要把整份 JPEG 读进内存（`arrayBuffer`）才能读注释；对
  常规几 MB 显示件可忽略，÷1 大图会多一份瞬时副本。
- 同一源换档位仍原地覆盖同一路径：响应现在持有快照、不会串字节，但客户端之间会互相
  顶掉文件，后打开的一方触发重生成 —— 这是既有的「一个落点 + 戳判档位」设计，未改。
- 真机验证未做：外网开发机读不到内网盘阵，真实压缩/Tiled TIFF、复杂 NoData、
  GB 级冷读收益与 `SR_PREVIEW_ROOTS` 的实际目录布局都待内网机确认。

## 6. 未改动

未重构 Viewer、Agent Loop、SR Executor；未修改生产源 TIFF；未生成金字塔；未清理
既有未提交修改；未执行 Git Commit / Push。
