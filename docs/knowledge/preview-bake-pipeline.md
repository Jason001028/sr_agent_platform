# 盘阵场景预览烘焙管线 背景知识

> 2026-09-17 / 已定（2026-09-20 增补：§4.1 `rasterPreview`、§4.8 落点共用、§4.9 产物急烤）
>
> **目标读者**：要改这条链路上任何一段的开发者（后端 `preview_jpg.py` / `api/app.py`，前端 `api.ts` / `stores/viewer.ts` / `stores/scenes.ts`）。
> **一句话摘要**：盘阵场景不把原始 TIF 交给浏览器，而是在源文件同目录先烤一张降采样（各边 ÷2…÷32 可选，2026-09-19 起由工具栏滑块定）、直方图均衡过的灰度 JPEG，浏览器读那张 JPEG。2026-09-20 起多两件事：作业转 COMPLETED 时服务端**顺手把产物那一份烤掉**（§4.9），显示件 jpg 在同目录有位更清晰的栅格时**改从栅格烤**（§4.1 末、§4.8）。
> **阅读顺序**：先看 §3 的两张流程图建立整体印象，再按需读 §4 的对应小节；§5 收录了改这条链路时最容易判断错的几个点。
>
> **范围**：本文覆盖「用户给出一个盘阵路径或文件名」到「图像画到画布上」的完整链路。
> **不覆盖**：本地文件路径（`选择 TIF…` / 拖入）的 UTIF / geotiff 分块 / 稀疏条带三路分派——那条路仍然在浏览器里直接读源 TIF，与本文的烘焙产物无关，两条路的常量也不共用。

---

## 1. 一句话总结

浏览器只拿一张服务端预先烤好的 JPEG；烘焙在源文件所在目录就地完成并就地缓存，缓存是否可复用由写在 JPEG 注释里的**规则签名**决定，不由修改时间决定。

---

## 2. 需要的背景概念（按重要程度排列）

### 2.1 为什么浏览器不能直接读盘阵原始 TIF

浏览器有两项硬上限：单次 `ArrayBuffer` 分配约 2GB、Canvas 面积上限 16384²。盘阵上的真实场景是 1.1~1.8GB、2.4 万像素级，像素数据本身约 `W×H×2 ≈ 1.2GB`，转 RGBA 后约 2.4GB，两者都在上限之外。因此浏览器侧不存在「整幅解码」这个选项。

（历史方案 `HttpSource + nginx Range` 曾计划让浏览器按需 Range 读 TIF 字节，已废弃。现在盘阵路径完全不碰原始 TIF 字节。）

### 2.2 真实盘阵 TIFF 的布局

已确认的形态是：**无压缩（`compression=1`）+ 每行一条带 + 单波段**，且多数场景目录带一个 ENVI `.hdr`（含 `samples` / `lines` / `bands`）。这个布局是稀疏采样可行的前提——知道每行的条带偏移后，就能只读需要的那些行，而不必解码整幅。

不满足该布局的文件（压缩、tiled、多波段、planar≠1）走 Pillow 兜底，且只对像素数不超过 67M 的小文件开放。

### 2.3 稀疏采样的语义

目标像素 `(i, j)` 取源图 `(round(i*(H-1)/(ph-1)), round(j*(W-1)/(pw-1)))`。分子分母都用 `n-1`，即**端点对齐**：首行取首行、末行取末行。若改成 `ceil` 之类的写法，最右列/最下行会重复取样，在图上表现为边缘条纹。

缩放比 `ps = min(1, max_edge / max(W, H))`。本文这条链路传入的 `max_edge = round(max(W,H) / div)`，
得到长宽各 1/`div`（2026-09-19 之前 `div` 固定为 2，现由滑块定，见 §4.4）。

### 2.4 拉伸

16bit 影像值域 0~65535，屏幕只有 0~255，显示前必须把「大多数像素所在区间」映射到 0~255。

- **2% 线性**：取 2% / 98% 分位作为两端。
- **直方图均衡**：取整幅 min/max（不是分位），建 1024 桶累积直方图，按 CDF 重映射。

本链路用直方图均衡。两侧（后端 `stretch_equal`、前端 `stretchMap(mode='equal')`）是逐式镜像的两份实现，见 §4.5。

### 2.5 缓存签名

烘焙产物就地覆盖，文件名不随规则变（一条规则：`<源 stem>_preview.jpg`，见 §4.8）。因此「这份缓存还符不符合当前规则」不能靠文件名区分，靠的是写在 JPEG 注释段里的一个 ASCII 串：

```
srprev:v3:div4+equal:q85
        │   │   │        └ quality → 改 PREVIEW_JPG_QUALITY 要 bump
        │   │   └ 拉伸规则（现只有 equal）
        │   └ 档位 → 改档位集合/换算要 bump（2026-09-19 加）
        └ 规则版本号 PREVIEW_RULE_VERSION
```

Pillow 用 `Image.save(..., comment=...)` 写、`Image.open(...).info["comment"]` 读。

---

## 3. 流程总览

### 3.1 端到端

```mermaid
flowchart TD
    IN1["/scenes 页：检索到一行，点「打开」"] --> ROW
    IN2["路径栏：粘场景目录，或粘单张 .tif"] --> RESOLVE["POST /api/scenes/resolve"]
    RESOLVE --> ROW["场景行 row<br/>id / W / H / hasPreview / jpgUrl / lq_path"]

    ROW --> FETCH{"fetchSceneJpg 走哪一支"}

    FETCH -->|"无 jpgUrl（库外）"| P1["GET /api/scenes/{id}/preview<br/>后端按需烘焙，直接回字节"]
    FETCH -->|"有 jpgUrl，无缓存（库内首开）"| P2["GET /api/scenes/{id}/preview<br/>这一步只为把缓存烤出来"] --> P3
    FETCH -->|"有 jpgUrl，有缓存"| P3["GET 静态 URL /disk-array/.../*_preview.jpg<br/>nginx 直接返回"]

    P1 --> BLOB["JPEG 字节"]
    P3 --> BLOB
    P3 -.->|"404"| GONE2["row.purged = true<br/>那一格的「打开」变成不可点的灰块「已自动清除」<br/>预览列同口径显示「已清除」"]
    BLOB --> DISP["decodeJpgToCanvas → getImageData → sceneDecodePixels<br/>stats 固定 0..255，rec.route = 'jpg'"]
```

两条入口的差别只在 row 从哪来（`GET /api/scenes` 检索 vs `POST /api/scenes/resolve`），之后的取字节与显示完全共用。
失败那条岔路（虚线）**只认一种 404**：**盘阵静态链**（`/disk-array/…`）上的 404 —— 静态 URL 由 nginx 直出，
文件不在就是 nginx 的 404，这才是「盘阵上没有这个文件」的实证（`lib/api.ts::isSceneGone` = 404 且 URL 落在
`/disk-array/` 之下）。**打后端的 `/api/` 请求回 404 不算**：非 JSON 的错误体说明这次请求**没走到后端**
（`isProxyMiss`，多半是 nginx 反代配置，见 timeline-archive 09-24 条），与盘上有没有这一景无关。
**422 也不走这条路** —— 它是「源还在、只是烤不出来」，照旧当错误报出来（拿一个猜的原因盖住真故障更糟）。
细节见 §4.1 的 `purged`。

灰块因此**只有一个来源：点过、且真的撞上了静态链的 404**。（2026-09-22 起还有过另一半 —— 渲染时按年龄
**推定**：老景 + 盘上一份预览都没有 + 超过 3 天。那条口径 2026-09-24 已整条删除，理由见 §4.1。）
另外，**打开成功**（走哪条入口都一样）按 `lq_path` 把列表里同一景那一行翻回来，不让列表继续印一个刚
落空的结论。

### 3.2 resolve 的分派与结果码

```mermaid
flowchart TD
    Q["POST /api/scenes/resolve"] --> BODY{"请求体是 path 还是 name"}

    BODY -->|"name（+ 可选 date）"| INFER["infer_scene_paths 反推候选目录"]
    BODY -->|"path"| NORM["to_posix_array_path 归一<br/>把 Windows 形态与 /DiskArray 形态合成同一口径"]
    NORM --> ALLOW{"ensure_allowed<br/>前缀白名单"}
    ALLOW -->|否| E403["403"]
    ALLOW -->|是| KIND{"该路径是什么"}
    KIND -->|目录| DIR["按目录处理"]
    KIND -->|".tif / .tiff 文件"| BARE["_resolve_bare_tif<br/>不做场景目录判定"]
    KIND -->|其它文件| E400A["400：不是 .tif/.tiff"]

    INFER --> DIR
    DIR --> HIT{"候选里有没有合法场景目录<br/>有 dir_meta.xml 且有输入影像"}
    HIT -->|都没有| E404["404：逐个列出每个候选的原因"]
    HIT -->|命中| DIMS
    BARE --> DIMS{"读得到 W/H 吗"}

    DIMS -->|否| E422["422：前端开图需要 W/H"]
    DIMS -->|是| OK["200：row + resolved"]
```

`name` 分支的日期可省略，由后端从文件名的 14/8 位成像时间戳里取；前端不重复实现这套正则（命名规则的唯一真源在 `backend/pathguard.py`）。

反推可能得到多个候选，逐个检查：先确认是目录，再确认 `scene_search.input_scene_path` 认得出输入影像；认不出时按原因分别说清——目录名就是完整生产名时是「缺 `dir_meta.xml`」，否则是「目录里没有输入影像」，**粘错了层**（日期目录 / 卫星型号层 / 段级目录 / 场景目录内部的子目录 / 名字不是生产名）则各报「差在哪一层」，见 [api-contract.md](../planning/api-contract.md) 的 404 一段。全部候选都不中才 404，并把每个候选的原因一并写进 `detail`——不静默换一条路径。

### 3.3 烘焙与缓存判定

```mermaid
flowchart TD
    S["ensure_preview_jpg(src, dst)"] --> M{"dst 存在，且<br/>mtime 不比源旧"}
    M -->|否| EDGE
    M -->|是| STAMP{"_cache_hit：<br/>JPEG 注释等于当前规则签名"}
    STAMP -->|是| CACHED["status = cached，直接复用"]
    STAMP -->|"否（缺签名 / 旧规则 / 文件损坏）"| EDGE

    EDGE["preview_max_edge：只读头或 .hdr 取 W/H<br/>max_edge = round(max(W,H) × 0.5)"] --> LAY{"布局是否为<br/>无压缩 + 条带 + 单波段 + 非 tiled + chunky"}
    LAY -->|是| SPARSE["sample_strips<br/>只 seek 到采样行，行内只留采样列"]
    LAY -->|否| SMALL{"像素数不超过 67M"}
    SMALL -->|是| PILLOW["_pillow_preview<br/>整图解码 + LANCZOS"]
    SMALL -->|否| FAIL["PreviewError → HTTP 422"]

    SPARSE --> EQ["stretch_equal<br/>min/max → 1024 桶 CDF，行块处理"]
    PILLOW --> EQ
    EQ --> INV["photometric 为 0 时做 255 - u8 反相"]
    INV --> ENC["Pillow 存灰度 JPEG，quality=85，comment=规则签名"]
    ENC --> ATOMIC["写临时文件 → os.replace 原子替换 dst"]
```

---

## 4. 关键实现与设计取舍

### 4.1 场景行 row 的字段语义

同一个 row 里混着三类信息，改动时最容易弄混：

| 字段 | 含义 | 真值来源 |
|---|---|---|
| `W` / `H` | **元数据尺寸**，即源图尺寸，不是 JPEG 的像素尺寸 | `scene_dims`：优先 `.hdr`，否则 TIFF 头探测 |
| `hasPreview` | 缓存文件**此刻在不在** | `jpg_path.is_file()`。库内库外都填真值 |
| `previewDiv` | 盘上那份缓存**是各边除以几烤的**（2026-09-19 新增）。读不出戳 → `null` | `preview_div_of()` 读 JPEG 注释戳 `div<N>`，只读头不解像素 |
| `jpgUrl` | nginx 静态 URL，**只有源在 `SR_SCENES_ROOT` 之下时才有值**；库内的库行给的是「缓存该在的位置」，文件可能还没生成 | `rel_url()` |
| `rasterPreview` | **只读**，2026-09-20 新增：描述同目录那份同名栅格（`{rel, id, name, rasterW, rasterH, jpgW, jpgH, hasPreview, previewDiv, jpgUrl}`）。**只有源是 `.jpg/.jpeg` 的行非空**，其余行恒 `null`；没同名栅格、或两侧尺寸任一侧读不出 → 同样 `null`。`jpgUrl` 只在栅格落在 `SR_SCENES_ROOT` 之下才给。**它不参与上面那三行的语义** —— 上面的 `hasPreview`/`jpgUrl`/`previewDiv` 锚在显示件 jpg 自己身上，栅格的状态单独放这里 | `_raster_preview()` |
| `lq_path` | 提交 SR 时用的目录。**这是「能不能提交」的判据** | 见 §4.2 |
| `sr_capable` | 与 `lq_path` 同源同真假的显式标志 | 见 §4.2 |
| `purged` | **前端独有，后端不返回**（2026-09-22 新增，判据 09-24 收紧）：打开这一行时在**盘阵静态链**（`/disk-array/…`）上撞了 404（这一景的文件取不到了，多半是生产数据已被自动清除）。标记后那一行的「打开」换成不可点的灰块「已自动清除」，预览列同口径显示「已清除」。**只活在前端这一次会话**，重新检索整批换行时自然消失 | 无（`stores/scenes.ts::open` 的 catch 里按 `lib/api.ts::isSceneGone` 置位） |

要点：`jpgUrl` 有值不等于缓存已存在，所以前端判断「这次会不会触发烘焙」用的是 `hasPreview`，不是 `jpgUrl` 是否为空。

**`purged` 的判据只有一条**（`isSceneGone`）：**404 且 URL 落在 `/disk-array/` 之下**。静态链上文件不在
就是 404，那是实打实的「取不到」；而 422 是「源还在、只是烤不出来」（档位非法 / 多波段 / 条带读失败）——
把它也算成「已自动清除」就是拿一个**猜的原因**盖住真故障。同理，**打后端的 `/api/` 请求回的 404 也不算**
（`isProxyMiss`：响应体不是后端给的 JSON ⇒ 这次请求没走到后端，多半是 nginx 反代配置）。标记是**前端的
经历**（试过一次、撞上了），不是盘上的事实，所以不进后端响应；文件名对不上、文件被删、被挪走都长得一样，
文案只陈述「取不到」，不写死原因。

**灰块只有一个来源**：上面这条实测标记（2026-09-24 起）。2026-09-22 到 09-24 之间还有过另一半 —— 渲染时
按年龄**推定**（`lib/scene.ts::presumedPurged`：老景 + 盘上一份预览都没有 + 年龄 > `PURGED_AGE_DAYS` = 3 天）。
该函数与那个常量已随本轮**整条删除**，理由是 [timeline-archive.md](../status/timeline-archive.md) 09-24 条里
写的那两条：列表里的行在**检索那一刻**源文件都在盘上（`is_scene_file` 要求 `is_file()`），而「盘上没有
预览」恰恰是**还没打开过的新景**的常态（平台只为产物急烤、从不预烤输入影像）—— 用推定去挡「打开」，
等于封掉「选景 → 打开 → 画掩码 → 提交 SR」这条主链路。

**打开成功会把结论翻回来**：无论走哪条入口，`open()` 末尾按 `lq_path` 把列表里同一景那一行翻回来
（`hasPreview=true` / `previewDiv=当前档位` / `purged=false`），不让列表继续印一个刚落空的结论。

**剩下的一条窄缝**：库行还没烤过预览、而源文件已被清掉时，`/preview` 回的是 422 而不是 404，这条路不会亮
灰块（前端只认静态链上的 404）—— 要覆盖得让后端在源文件不存在时回 404（[current-question.md](../status/current-question.md)
§3.3 第 1 条）。

**但只看 `hasPreview` 不够**（2026-09-19 起）：它不认档位。用户把工具栏那条档位拖动条从 ÷4 拉到 ÷8
之后，盘上那份 ÷4 的缓存**仍在**（`hasPreview` 仍为真），前端若据此跳过重烤就会直接取静态 URL ——
界面滑了，盘上那张图一个字节都不变，用户看到的还是旧档位。所以判据是
`hasPreview && previewDiv === 当前档位`；`previewDiv` 为 `null`（旧格式戳、或戳读不出来）同样算「不符」，
触发一次惰性重烤。这条只在**有静态 URL 的行**上成立，库外手工行每次走 `/preview` 回字节，天然跟着档位走。

源文件本身就是 `.jpg/.jpeg` 时（盘阵里的显示就绪图），不烘焙：`hasPreview` 恒真、`previewDiv` 恒 `null`
（档位对显示件没有意义）、`jpgUrl` 直接指向源文件，`/preview` 也直接回该文件。前端靠
`isBakedPreviewUrl()`（`/[_\.]preview\.jpe?g$/i`）把这类行与「平台自己烤的」区分开，否则会把恒为 `null`
的 `previewDiv` 一律当成「档位不符」，每次打开都白打一次 `/preview`。

**2026-09-20 起这条多一个岔路（工作流 B）**：显示件 jpg 若是「盘阵预生成的中间产物、分辨率不够」，
而同一目录躺着一位更清晰的同名栅格（`PAN.tif`），显示源就改从栅格烤。判据只有一条：

```
round(max(rasterW, rasterH) / div) > max(jpgW, jpgH)   → 换成服务端从栅格烤的那份
否则（含相等、含任一侧尺寸读不出）                    → 保持显示件 jpg（现状）
```

三点必须记住：

- 比的是**盘阵那份 jpg** 的尺寸，不是用户本地那份（拖入时指纹对 jpg 只比名字，本地副本可能另存过）。
- **严格大于**：相等不算赢 —— 烤一份要读整幅栅格，像素数不比现状多就没理由付这个代价。
- **`row.hasPreview` / `row.jpgUrl` / `row.previewDiv` 一个字节都不改**：它们永远描述显示件 jpg 自己
  （§9.2 红线）。栅格的状态走 `rasterPreview`，前端只写 `row.rasterPreview.hasPreview/.previewDiv`。

**默认档位 ÷4 下这条基本不触发**，这不是 bug 而是算术：24000 源 + 8192 显示件时，
÷2 → `round(24000/2)=12000 > 8192` 赢，÷4 → `6000 > 8192` 输，÷8 → 输。所以验收口径不能说
「jpg 行一律走服务端」，只能说这条比较规则本身；现有 e2e 夹具（1600×800 栅格 + 800×400 jpg、
640×320 + 320×160）在 ÷4 下全部判 jpg 赢 —— **那些断言一字不改地继续绿，就是这次的回归钉子**。

换不换由**后端**在 `/preview` 与 `/preview-drop` 里各插一次同名栅格探测决定（`sibling_raster_path`，
固定候选名逐个 `is_file()`，不列举目录），前端只管按档位取图 —— 两条入口换出来的字节因此完全一致。

### 4.2 裸 `.tif` 入口与「能不能提交 SR」

路径栏接受单张 `.tif` 文件。这类输入只保证「能看」，不保证「能提交 SR」，两者必须显式分开：

- 目录分支走到 200 时，已经确认过有 `dir_meta.xml` 且目录里有输入影像，所以 `sr_capable` 恒为真。
- 裸 `.tif` 分支不做任何场景目录判定。只有当**父目录恰好是合法场景目录、且该目录的输入影像就是这个文件**时（逐项比对用 `os.path.samefile`，因为 Windows 上 `Path.__eq__` 是大小写敏感的字符串比较，会把小写盘符判成不同文件），`sr_capable` 才为真；否则 `sr_capable = false`、`row.lq_path = null`、`resolved.mask_path = null`。

前端据此决定：`lqPath` 为空时不写进 rec，文件列表里「提交 SR」保持禁用，并给出提示。这条判断在 `stores/viewer.ts` 里还有一处配套约束——`tryLinkScenes` 对 `route === 'jpg'` 的 rec 直接早退，不再按文件名反推目录。原因是裸 `.tif` 的父目录可能不是场景目录，按文件名反推有命中**另一个**目录的可能，那会让用户拿着错的 `lq_path` 去跑 SR。

### 4.3 取字节的两条路

- **库外（裸 `.tif`）**：没有可以映射成静态 URL 的位置，走 `GET /api/scenes/{id}/preview`，后端烘焙完直接 `FileResponse` 回字节。
- **库内**：优先走 nginx 静态 URL `/disk-array/.../*_preview.jpg`，由 nginx 原生缓存承担重复请求。缓存尚未生成时，先打一次 `/preview` 把它烤出来，再取静态 URL——这一步是必须的，因为 nginx 不会去触发后端生成。

场景 id 有两种形态，靠前缀区分：库行是 `base64url(rel)`，手工行（resolve 出来的）是 `~` + `base64url(绝对路径)`。后者经 pathguard 的前缀白名单校验，`..`、白名单外的绝对路径、非绝对路径一律拒绝。

### 4.4 烘焙的尺寸、采样与并行

尺寸：`max_edge = max(1, round(max(W, H) / div))`，`div` 取值 ∈ `PREVIEW_DIVISORS = (2, 4, 8, 16, 32)`
（各边除以几）。缩放比仍走原来的 `ps = min(1, max_edge / max(W,H))` 公式，采样算法本身没有改动
——换规则只改了 `max_edge` 的算法。不封顶，因此 ÷2 下 24739×24199 → 12370×12100，÷4 下 → 6185×6049。

`div` 由调用方（HTTP 层）从查询参数取，缺省 **2**（`LEGACY_PREVIEW_DIV`，与 2026-09-19 之前的行为逐字节相同）。
**「默认 ÷4」只活在前端那一个常量里**（`lib/scene.ts::DEFAULT_PREVIEW_DIV`）—— 它是 UI 默认值，
不是烘焙契约；后端不认识"当前档位"，每一档都得由请求明确带上。两个名字不重名（一个 `DEFAULT_`、
一个 `LEGACY_`）是有意的，免得看代码的人以为后端也有个"默认档"。

采样：`sample_strips` 按 `row_idx` 逐行读，每行只 `seek` 到该行的条带偏移、只保留 `col_idx` 指定的列。
÷2 采样下恰好读到源文件一半的字节，这是几种读法里读得最少的：块读、`memmap`、整文件顺序读都会多读一倍。
档位越低（÷ 得越多）读得越少、烤得越快，这就是这条拖动条能换来「首次打开耗时/体积」的物理来源。

并行：仅当采样行数 ≥ `PARALLEL_MIN_ROWS`（512）时才并行，避免小图上线程开销盖过收益（也让小 fixture 的测试结果确定）。并行度 `READ_THREADS = 8`。

两个实现约束：

- **每段开自己的文件句柄**。线程之间不能共享句柄，因为 seek 位置是句柄状态，而 Windows 没有 `os.pread`——这是唯一可移植的并行读法。
- **必须消费 `ex.map` 的返回值**。异常在线程里抛出，只有迭代时才转交给调用方；不消费就等于静默吞掉。

这条并行为延迟而非带宽优化：1/2 尺度要发约 1.2 万次读，盘阵上单次读若有毫秒级延迟，串行就是十几秒；8 路并发把它压回一秒量级。页缓存命中时收益只有约 1.4 倍，所以开发机上几乎量不出效果。

### 4.5 拉伸：直方图均衡与前后端逐式对齐

`stretch_equal` 逐式镜像前端 `computeStats` + `stretchMap(mode='equal')`：

```
lo, hi = min/max(有限值)                    # 是 min/max，不是 p2/p98
若 hi <= lo: 全零图 → 0，其它常量图 → 128
BINS = 1024
直方图入桶:  q = ((v - lo) * BINS / (hi - lo)) | 0        # 对应 computeStats
查表下标:    k = ((v - lo) / (hi - lo) * (BINS - 1)) | 0  # 对应 stretchMap
out = cdf[k] / total * 255
```

两处公式**故意不同**（一个用 `BINS`、一个用 `BINS - 1`），这是前端既有实现的原样，不要在任一端把它们「顺手统一」。

三个必须保留的实现细节：

- **按行块处理**（`_STRETCH_CHUNK = 4M px`）。1/2 尺度下输出有约 1.5 亿像素，整图 `astype(np.float64)` 会产生 1.2GB 副本；分块后额外峰值约 32MB。
- **`np.rint` 而非 `astype(np.uint8)`**。前端把结果写进 `Uint8ClampedArray`，那是四舍五入（ties-to-even）；`astype` 是截断，会整体差 1 个灰阶。
- **NaN 落到 0 号桶**。前端 `(NaN * n) | 0` 得到 0，这里用 `np.nan_to_num` 对齐。

`photometric == 0`（WhiteIsZero）在查表之后做 `255 - u8` 反相，与前端 `stretchRgba` 的先反色一致。

### 4.6 缓存失效：规则签名优先于 mtime

`ensure_preview_jpg` 的命中需要两条**同时**成立：产物不比源旧，且签名等于当前规则。只判 mtime 会在换包后失效——升级前烤的图 mtime 就是比源新，只看 mtime 会把它判为有效而永不重烤，用户看不到任何变化。

签名由 `rule_stamp(quality, div)` 生成，形如 `srprev:v3:div4+equal:q85`。`PREVIEW_RULE_VERSION`、
`PREVIEW_JPG_QUALITY`、以及**档位 `div`** 都是它的一部分。**改动尺寸、拉伸、质量或档位中的任何一项，
都要 bump 对应的部分**，否则旧缓存不会失效。

档位进戳（2026-09-19）取代了此前那条「临时路径要单独设 max_edge 就得独立一套规则戳」的约定 ——
不要再按那条旧字面去实现两套戳，`div` 进同一个戳就够了：同源同落点、`div` 不同 → 戳不同 → 必须重烤，
这正是档位这条子逻辑的判据。版本号从 `v2` bump 到 `v3`，且**没有为 `div==2`（旧默认档）留兼容拼法**
—— 2026-09-19 起前端默认档已是 ÷4，这一轮注定要重烤，留特例只是白增一条分支。

一个相关的坑：Pillow 默认 `MAX_IMAGE_PIXELS` 是 8948 万，超过 2 倍直接抛 `DecompressionBombError`。
÷2 尺度下 2.4 万像素级的源烤出来正好是 1.5 亿像素，会让 `_cache_hit` 里的 `Image.open` 抛异常、
缓存永远判不中，于是每次打开都重烤。本模块把它提到 `1 << 30`（约 10.7 亿，仍能挡住声明 21 亿像素以上的文件）。
档位调低后这个上限不再吃紧，但保留它 —— 滑块是用户可调的，别把「用户停在 ÷2」当成不可能。

### 4.7 显示层

`openSceneJpg` 拿到 JPEG 字节后：`decodeJpgToCanvas` 解码 → `getImageData` → `sceneDecodePixels`。后者用 `computeStats(src, tw, th, 1, 0, 255)`，把 stats 固定成 0..255——这样 `linear` 拉伸是恒等映射，在 `linear` 模式下看到的就是服务器烤的那份像素。

rec 的 `route` 记为 `'jpg'`，`layout` 记为「盘阵 JPG（1/4 尺度 + 直方图均衡，服务端已烘焙）」
—— 括号里那个尺度取**当前档位**（`viewer.previewDiv`），不再是写死的「1/2」。

起手拉伸为 `SCENE_START_STRETCH = 'equal'`。由于底图已经均衡过，这一步是**再均衡一次**，灰度分布已近似均匀，基本是恒等映射。这个常量与本地图的工具栏模式解耦，只影响某张图**首次**绘制（之后以 `rec.paintedMode` 为准）。

掩码与 ROI 的坐标换算走的是**元数据 `W`/`H`**，不是 JPEG 的像素尺寸（`thumbPolysToOrig` 的 scale 取自元数据）。因此烘焙尺寸变化不影响掩码落点。

### 4.8 落点与原子写

**文件名只有一条规则**（2026-09-22 起，`paths.preview_jpg_name`）：

```text
<源栅格 stem>_preview.jpg
```

三条链（惰性打开 / 急烤 / 拖入）落的是同一个名字，**差别只剩目录**。档位不参与命名，
只进规则戳（换档位原地覆盖同一份）。

- 源在 `SR_SCENES_ROOT` 之下 → `preview_jpg_path`：默认 `<源同目录>/<stem>_preview.jpg`；若配了 `SR_PREVIEWS_ROOT`（必须在 scenes root 之下，否则 nginx 单根 alias 覆盖不到）则搬到 `<previews_root>/<rel 目录>/<stem>_preview.jpg`，URL 不变。
- 源在库外（粘路径 / 裸 `.tif` 打开）→ 恒为 `<源同目录>/<stem>_preview.jpg`。这类不走 nginx 静态 URL，不需要在 URL 层面可映射。
- **拖拽入口**（`GET /api/scenes/{id}/preview-drop`）→ 也是这个名字（`paths.drop_preview_path`），
  差别只有一条：**不吃 `SR_PREVIEWS_ROOT`**，恒落源同目录 —— 拖入链要的是「烤一次长期可用」，
  搬去缓存根就又变成缓存了。响应带 `Cache-Control: no-store`。

**改名前的点号那份（`<stem>.preview.jpg`）不再由任何链产出，也没有任何读者**：改名把三条链的
落点统一之后，旧文件由 `paths.legacy_preview_path` 从新落点反推（同目录、同源 stem，只差一个
字符），在每条链**处理到那份栅格时**顺手删掉 —— 烤之前删一次（`_bake_product_preview` /
`_bake_nosr_preview`）、命中缓存时也删（`/preview` 与 `/preview-drop` 两个端点各一次），
失败不报错（`_sweep_legacy_preview` 只吞 `OSError`）。**没有全盘清扫**：平台不列目录（硬约束），
所以没被任何链碰过的场景目录里那份点号文件会一直留着 —— 它不影响任何判定，只是占地方。

拖拽那份 2026-09-19 改成了落盘阵（此前落在 `SR_TEMP_PREVIEWS_ROOT` 的当天桶里，次日 0 点整桶删）。
改的理由：当天有效意味着**每天第一次拖入都要重烤一遍**（几十秒），而这份产物本来就与场景数据
同生命周期。落进场景目录后烤一次长期可用，反复打开同一场景不再重烤。

带下划线是为了不被 `scene_search.is_scene_file` 的白名单收成一行场景（它只认「文件名 == 目录名
或 `PAN`」，`_preview.jpg` 两个都不沾边 —— 有测试钉着），也与真机上源文件自带的那份同名 `.jpg`
（`<stem>.jpg`）区分开，不会被误判成「源本身就是显示件」。代价是它不吃 `SR_PREVIEWS_ROOT`，
所以在配了镜像树的部署下同一场景会有两份缓存，且盘上多出的这一份没有清理者（原地覆盖，不堆积）。

**这份预览被拖回来时按它代表的栅格认（2026-09-21 晚）**：它落在场景目录里，长得就跟一份
「场景里的 jpg」一样，用户会顺手把它再拖进去 —— 而 `preview` 原本在
`scene_search._NON_STAGE_TAILS` 里，于是「平台写下的文件、平台自己不认」，404 还列出两条
自己拼出来的假路径（`…_preview/…_preview`）。现在 `preview` 是那份名单里**唯一可以剥**的
尾巴：`strip_preview_tail` 把 `<栅格 stem>_preview` 还原成 `<栅格 stem>`，`de_suffixed_stems`
与 `stage_of_jpg` 各用一次 → `<目录名>_preview.jpg` 等价于拖本体显示件（可提交 SR），
`<目录名>_sr_preview.jpg` 等价于拖那份产物（`lq_path` 仍为空）。只剥一层，剥完仍在名单里
（`<目录名>_cloud_preview`）照旧不认。

**栅格行与 jpg 行共用同一份落点（2026-09-20）**：文件名只由**源 stem** 拼（`preview_jpg_name`），
对 `PAN.tif` 与 `PAN.jpg` 是**同一个文件名**（2026-09-22 改名后仍成立）。工作流 B 把 jpg 源的烘焙换到栅格上之后，
两行命中的是同一份缓存 —— 好处是不会烤两次、用户从哪一行打开看到的字节都一样；
代价是**同一份落点会被不同档位的客户端互相顶掉**（这个病今天就有，工作流 B 只是把它拖进更多行）。
本轮不治理 div 抖动（不引入带档位的落点 `<stem>_preview.div2.jpg`），在文档里点明，不装作没有。

**落盘阵失败时兜底**：写盘阵要求服务账号（`User=nginx`）对场景目录有写权限，这一条在真机上仍是
待确认项。所以先 `os.access(dir, W_OK)` 预判，不可写或写失败 → 退回原来的临时缓存落点
`SR_TEMP_PREVIEWS_ROOT/<YYYY-MM-DD>/<sha256(源绝对路径)[:16]>.jpg`（按日期分桶、每天 0 点整桶删，
`preview_cache.purge_temp_previews` 由 `api/app.py` 的后台任务 `_tmp_preview_purge_loop` 驱动），
并回响应头 `X-SR-Preview-Fallback: tmp` 让前端如实说明；**两条都失败才 422**。
保留兜底的意义是：权限没配好也只是「没落盘阵」，不会退化成每次拖入几十秒的浏览器本地解码。
清理只删「桶名是 ISO 日期、且桶里带 `.sr-tmp-preview` 标记文件」的目录 —— 标记文件把
「这个目录是我们建的」变成可判定的事实，比任何路径白名单都可靠。

写盘走「临时文件 → `os.replace`」：`os.replace` 在 POSIX 与 Windows 上都是原子替换，读者不会读到半个文件。临时文件建在目标同目录（`tempfile.mkstemp` 的 `dir`），保证与目标同一文件系统。

### 4.9 产物急烤（2026-09-20）

前三节说的都是**惰性**烘焙：用户打开时才烤。产物那一份多一条**主动**路径 —— 作业转
COMPLETED 之后由后台循环顺手烤掉，用户跑完立刻打开时盘上已经有了。

**为什么只烤产物**：输入影像与「NOSR」两份「用户到底要不要看」在打开之前无从知道（对比 UI
还没做），而产物是刚跑完的、几乎一定会被打开。三份落点天然独立（各由自己的源 stem 拼出
`<stem>_preview.jpg`，见 §4.8 那条唯一规则），各烤各的、互不覆盖，所以**不新增任何
烘焙入口**：三类图各拿自己的 id 调现有的 `GET /api/scenes/{id}/preview?div=N` 就行。
（2026-09-21 起多烤一份**未超分**的，但走的是另一个函数，见 §4.11 —— 上面这三份的惰性
地位一个字没变。）

**急烤的落点与惰性那条链现在是同一个文件**（2026-09-22 起；此前是「点号那份再镜像一份下划线
件」，`_mirror_preview_name` 已删）：用户跑完立刻打开命中的就是急烤那份，字节不差 ——
`test_api_platform.py::TestProductPreviewBake::test_the_eager_bake_and_a_lazy_open_are_the_same_file`
钉的就是这条，顺带钉「一个场景目录里只有一份预览名」。改名前的点号那份在这一轮里被顺手删掉
（§4.8 末段）。

状态机（两列 `preview_state` / `preview_note`，随 `GET /api/queue` 每行带出）：

```
NULL ──claim──> running ──> done
                     ├──> skipped（note = "<slug>: <人话>"）
                     └──> failed （note = "failed: <异常原文>"）
```

slug 六个：`no_suffix`（任务行里拼不出产物名）、`sandbox`（跑在沙箱私有副本上，或沙箱根
配置不可用）、`product_missing`（产物候选名逐个 `is_file()` 都不在 —— 云限额跳过那种
「合法 COMPLETED 但没产物」走的也是这条，所以 note **必须列出试过的候选名**）、
`unwritable`（产物目录对服务账号不可写）、`source_changed`（像素已读出、落盘前复核发现
产物被改写 → 一个字节都不写）、`failed`。

关键结构选择：**不挂在状态转换上，改成从库派生**。写终态的 `platform._task_state` 有两个
调用者 —— 后台 `_poll_once` 与**请求路径** `_task_view`（`GET /api/queue`），谁先看到
RUNNING→COMPLETED 谁把 `changed` 拿走，在那里挂入队钩子必然偶发漏烤。改成「`sr_tasks` 里
`status='COMPLETED' AND preview_state IS NULL`」之后，这个竞态在结构上不存在。

数字（改动前先看这几个，它们解释了几条看着保守的设计）：

| 项 | 值 | 出处 |
|---|---|---|
| 并发 | **1**（每轮至多烤一件，单消费者） | 设计选择 |
| 峰值内存 | 约 400MB / 份（÷4 烤 40000² 产物） | `app.py::_eager_bake_tick` 的注释 |
| 每轮扫描上限 | 20 行（`_EAGER_SCAN_LIMIT`，模块常量，不开 env） | 同上 |
| 年龄窗口 | 缺省 86400s（`SR_PRODUCT_PREVIEW_MAX_AGE_SEC`） | §1 env 表 |
| 急烤档位 | 缺省 4，`0` = 关（`SR_PRODUCT_PREVIEW_DIV`） | 同上 |
| 磁盘增量 | 每份产物一张灰度 q85 JPEG，量级与惰性路径同（未单独实测） | 推算 |

每轮只烤一件是**有意选的**：并发会把内存乘上去、把盘阵带宽占满；代价只是积压时靠后那几件
晚几分钟烤好，而它们本来就是等用户打开的。急烤**不占任何 semaphore**（不与惰性路径共享锁）：
同档位时它的 `cache_hit` 通常命中用户刚烤的那份，不同档位就两份都留（§4.8 那条 div 抖动）。

四个容易写错的地方：

- **沙箱判据必须是 `_run_dataroot(task)`，不能直接比 `SR_SANDBOX_ROOT`**：前者内部走
  `run_sr.sandbox_scene_paths`，那条在 `SR_EXECUTOR=local` 时恒返回 `None` —— 真机当前正是
  「配了 `SR_SANDBOX_ROOT` + local executor」这条路线，直接比 env 会把本可以烤的产物判成
  「沙箱内」而**永不烤**。有专门的回归钉子（`SR_SANDBOX_ROOT` 在 + `SR_EXECUTOR=local` → 照烤）。
- **只烤 COMPLETED，FAILED 绝不烤**：`writeTiff` 先改名再写，失败的运行会在产物路径上留下
  半截文件，烤出来是坏图。这条同时进候选查询与 `claim` 的 `WHERE`。
- **落盘前复核 `(mtime, size)`**：同一 suffix 重跑会覆盖同一个产物路径，不复核就可能把一张
  半截产物的图永久留在盘上 —— 缓存判据是「不比源旧」，新的 mtime 可能仍晚于刚写的 jpg，
  它**不会自愈**。为此 `preview_jpg.py` 把 `cache_hit`（原 `_cache_hit`，去掉下划线是因为急烤要在
  读大图**之前**先问一遍）与 `write_preview_jpg`
  从 `ensure_preview_jpg` 里抽了出来：**纯重构，缓存规则与 `rule_stamp()` 一字未动**，
  `test_preview_jpg.py` 就是这次的回归钉子。
- **迁移不回填**：两列走既有 `_ensure_columns`（PRAGMA → ALTER TABLE，幂等），老行一律 `NULL`，
  **故意不补值**。加上年龄窗口那道条件（`finished_at IS NOT NULL AND finished_at >= cutoff`），
  这是升级当天不把历史 COMPLETED 行全烤一遍的**唯一**屏障 —— 所以升级日的做法是先
  `SR_PRODUCT_PREVIEW_DIV=0` 起一次确认无异常，再打开（见 [current-question.md](../status/current-question.md)）。

可见性：`GET /api/queue` 每行多出 `preview_state` / `preview_note`（两列与作业状态**无关**，
别耦合成一个状态机），广播走**新的帧类型 `preview_update`**（不混进 `job_update` —— 那是
「作业状态变了」，混在一起前端收到就得重取整行；`task_id` 对不上的帧要原样不动）。
`set_preview_state` **不碰 `updated_at`**，`put_sr_task` 的 UPDATE 分支**清这两列**（同一 suffix
重跑必须重新武装，否则第二次跑完永远停在旧 `done`）。

### 4.10 拖入 jpg 的后台静默烤（2026-09-21）

用户把场景目录里的 jpg（显示件或中间产物）拖进查看器，关联成功之后**顺手**在场景目录里
留下 `<该环节栅格的 stem>_preview.jpg`。这一节只讲那条烘焙，认环节的判据见
[api-contract.md](../planning/api-contract.md) §3.5。

**落点就是 §4.8 那条唯一规则 `<源栅格 stem>_preview.jpg`** —— 与惰性/急烤两份同名（2026-09-22
前拖入链是唯一用下划线的那条，改名后三条链一致）：

| 谁写的 | 名字 | 谁在读 |
|---|---|---|
| `ensure_preview_jpg`（惰性 / 急烤） | `<stem>_preview.jpg` | 服务端自己（`hasPreview` / `previewDiv` / 静态 `jpgUrl` 命中） |
| `POST /api/scenes/{id}/preview-drop` | 同一个名字 | 同一套判据；另外 `ls` 一眼就知道这份图有预览 |

三条链同名之后，读判据也必须跟着走，否则就会出现「写了却没人认」的空转：

- 服务端 `hasPreview` / `previewDiv`（行与 `/siblings` 两处）与静态 `jpgUrl`
  都取自同一份落点，改名后自动一致；
- 前端 `lib/scene.ts::isBakedPreviewUrl` 认结尾 `[_\.]preview\.jpe?g`，据此决定拼不拼
  `?div=`（击穿 nginx 的 `max-age=3600`）以及换档位后要不要重烤（`previewNeedsBake`）。
  **点号那代一并认下**：静态 URL 是后端给的，两边版本错开一档时认得出比认不出安全
  （认不出会把 `?div=` 吞掉，换档位后最长一小时看到旧图）。

`_preview.jpg` 仍在 `scene_search.is_scene_file` 的白名单**之外**（它要求文件名等于目录名或
`PAN`，`_preview.jpg` 两个都不沾边 —— 有测试钉着），所以它不会被收成一行新场景。

**静默**是用户口径，也是实现约束（`stores/viewer.ts::bakeDropPreview`）：拖进来的 jpg 若
判本地那份赢（§4.6，本地那份更清晰时），展示像素用**用户拖进来那张原图**，同时
`void fetch(...).catch(() => {})` 发一跳 —— 不 `await`、不占遮罩、不给 `onPhase`、不看结果。
两个直接后果：

- **不阻塞**：用户拿到画面不用等一次读大图（真机上产物的 ÷2 也是几十秒量级），像素也
  不降清。烤失败只是盘阵上少一份预览，不该在用户眼前报错。
- **e2e 的写法**：因为它不是请求-响应式的，`.e2e/test-manual-scene.js` E2/L4 只能**轮询
  落盘**（`waitNode(() => jpegSize(p) !== null)`），断言的是「文件最终在」+「烤的是哪一份
  栅格」（按尺寸区分：本体 1600×800 ÷2 = 800×400，产物 3200×1600 ÷2 = 1600×800，差一倍）。

**为什么必须走服务端**：真机页面是 `http://内网IP`，SecureContext 的浏览器 API（`showSaveFilePicker`
一类）在那里根本不存在，页面无权往盘阵写任何东西。写盘阵只有一条路：交给后端（跑在盘阵
那台机器上、以 nginx 身份写）。

**只在「本地那份赢」时分岔**：判服务端那份赢时（§4.6）本来就发过 `/preview-drop` 了，
同一条请求不重复发 —— 那一路的字节日志本来就没人用，不额外多跳。

### 4.11 未超分那一份：名字口径与两处触发（触发一 2026-09-21 / 口径订正与触发二 2026-09-24）

**未超分那份**（下称 NOSR）是一份栅格 TIF，不是它烤出来的 jpg。用户在界面上见到的 NOSR 一律
指这份栅格或其预览。

#### 名字口径（2026-09-24 订正）

**未超分那份 = 输入影像的 stem + `_NOSR`**，扩展名 `.tif` / `.tiff`：

| 场景 | 输入件 | 未超分那份 |
|---|---|---|
| SC 步 | `<目录名>.tif` | `<目录名>_NOSR.tif` |
| RC 步 | `PAN.tif` | `PAN_NOSR.tif` |

从 `SR_code/util.py::writeTiff` 的改名规则推导出的 `<产物 stem>_NOSR.tif`（如
`PAN_260318_NOSR.tif`）**退为次选**，仍留在候选清单最后一位 —— 那份也可能真在盘上，
命中了就如实报出它的名字。

订正的理由是这两处口径本来各说各的、彼此对不上：

- 09-21 那版 `_bake_nosr_preview` 把名字**硬编码成 `PAN_NOSR.tif`**（RC 的名字）。SC 场景的目录里
  没有 `PAN` 这个名字，于是永远走 `skipped: … 里没有 PAN_NOSR.tif`。
- `/siblings` 的 NOSR 那一项只认 writeTiff 那条规则（`nosr_path_for`），用户的目录里没有那个
  名字，于是恒报 `nosr.exists=false`，对比条那颗 NOSR 芯片永远打不开。

两处现在读同一份清单：`scene_search.nosr_candidates(dir, input, product)` —— 有序、**只拼名字**，
只对用户明确给的那个目录下的固定候选名做 `is_file()`，不列举（口径见
[scene-io.md](io-pipeline/scene-io.md) §2.1）。
基准名取自 `stage_bases`（`<目录名>` / `PAN` / 上游遗留件），所以 SC、RC 与混合目录同一条路。
`/siblings` 把它整份报给前端（`nosrCandidates`），拿到真机 `ls -l` 可据此校准**顺序**，
但不影响能否命中。

#### 触发一：超分跑完顺带烤（2026-09-21）

用户口径：**超分跑完就顺手把未超分那份的预览也烤出来** —— 「将 `PAN_NOSR.tif` 按照全局采样率
进行下采样，不考虑其他分支」。

实现是急烤那一轮里多一个函数（`app.py::_bake_nosr_preview`），源名走上面那份候选清单，
跟在产物后面、同一个线程、仍然串行：

| 项 | 值 |
|---|---|
| 源 | 候选清单里第一个存在的 `<场景目录>/…_NOSR.tif`（`.tiff` 也认） |
| 落点 | `<场景目录>/<该栅格 stem>_preview.jpg` —— 就是 §4.8 那条唯一规则（`paths.drop_preview_path`） |
| 档位 | **全局**那一档（`SR_PRODUCT_PREVIEW_DIV`，与产物同一次 tick 读的同一个值；`0` = 关） |
| 命中判定 | 与惰性路径/产物那份**同一个** `cache_hit`：盘上那份已是当前档位就不重读 |

两条边界，都是有意的：

- **不看沙箱**：这份栅格是盘阵上的既有文件，与这次跑在盘阵还是私有副本上无关 —— 沙箱跑时它
  照样在、照样该烤。产物那一份必须判沙箱，因为它才刚被写出来。
- **不动行的结论**：`preview_state` / `preview_note` 那一列描述的是**产物**预览，一个字段说不出
  两份文件的结局。这一份只写盘 + 往 stdout 打一行 `[nosr-preview] task=<id> <状态>`，
  状态四种（`baked:` / `cached:` / `skipped:` / `failed:`）。**`skipped:` 要报出试过哪些名字** ——
  一个名字都不在盘上时，只有把清单打出来才看得出是名字不对还是那份本就不存在。

#### 触发二：拖入显示件时前端预热（2026-09-24，用户口径）

用户要的路径：拖入关联盘阵场景的 `<目录名>.jpg`（本体显示件）或产物显示件 → 平台**自动**去
找该场景目录下那份 NOSR 栅格、烤成 jpg → 之后这份 NOSR 显示件出现在界面上时带 `NOSR` 标。

实现是前端一条静默预热（`stores/viewer.ts::warmNosrPreview`），在 `tryLinkScenes` 里紧跟
§4.10 那条 `bakeDropPreview` 之后：

| 项 | 值 |
|---|---|
| 触发条件 | 拖进来的是**显示件**（`localJpg`）、有关联上的 `sceneId`、且这份自己不是 NOSR |
| 不触发 | 从场景库打开、从路径栏打开（用户口径：只有拖入算） |
| 怎么找 | `GET /api/scenes/{id}/siblings` → 取 `kind === 'nosr'` 且 `exists` 的那一项 |
| 怎么烤 | `fetchSceneJpg`（既有惰性链）→ 服务端 `ensure_preview_jpg`，落点就是**打开它时读的那一份** |
| 记账 | 按 `sceneDir`（缺则 `sceneId`）去重，**烤上了才记账** |
| 落空 | 那份栅格不在盘上 → 什么都不做、界面上什么都不出现；也不记账，之后再拖入还能再试 |

与 §4.10 那条的区别：`bakeDropPreview` 烤的是**拖进来那份自己的栅格**，这条烤的是**同景的另一份**。
两条都静默、都不 `await`、都不占遮罩、失败都不报错。落点同样是 §4.8 那条唯一规则，
所以用户之后手动把这份 jpg 拖回来时，看到的与就地烤出来的是一份文件。

**去重为什么按「成功才记账」**：盘上的栅格是后来才出现的（比如这份景刚超分完、或者运维刚补上
那份 NOSR）。若一拖进来就记账，那次落空会把这一景永久锁死，之后拖多少次都不会再试。

#### 界面上的 NOSR

- **卡片标**：环节标落在序号之后（`FileList.vue` 的 `.stage`），文案 `NOSR`、配色是琥珀那族
  （`.stage.nosr`），与本体那族的青绿（`.stage.input`）区分。标可点 → 开进当前活动格。
- **两义同一个标**：输入 stem 那份与 writeTiff 那份都标 `NOSR`，工具提示把两义都说出来
  （`FileList.vue::STAGE_TITLE`）。
- **等待表示**：打开它时与打开任何产物完全一样（同一套遮罩 + 阶段文案），不因为它是另一份就换一套。
- **只读**：中间产物不作修复（`RO_TITLE`）—— 掩码与 SR 都建在本体影像的网格上。

### 4.12 人工清除缓存（2026-09-22）

上面三条链把预览 JPG **只增不减**地写在盘阵上（打开时惰性烤 / 拖入链 / 超分跑完急烤）。
换档位或换规则之后想立刻看新效果、或者就想回收盘阵空间，此前只能逐个场景点开等它重烤。
现在场景库（`/scenes`）表格卡片头部多了一条工具行：**左侧全选 + 「已选 N 项」，右侧
「清除选定」「全部清除」**。这条功能是**对生产数据目录的写操作**，判据与范围都按
「宁可不删，也不删错」设计。

| 项 | 值 |
|---|---|
| 端点 | `POST /api/scenes/clear-preview`，体 `{"ids": [...]}`（一次 ≤ 500 条，超出 400） |
| 服务端一半 | `backend/services/preview_clear.py`（纯逻辑，只列用户点名的那一层目录） |
| 用户口径 | 「清除选定」= 勾上的那些行；「全部清除」= **当前检索结果**（受筛选影响，到 `limit` 截断线为止），且**要输入确认词 `清除全部`** |
| 清完的行 | **从列表移除**（局部，不重新检索；命中计数跟着递减，重新检索即回来） |
| 库里的状态 | 该目录的 `sr_tasks` 行写成 `preview_state='cleared'`，**不自动重烤**，下次打开惰性重烤 |
| 灰度 | 无开关；判据不满足就只是「跳过」，明细里逐条给原因 |

**判据（四道锁，缺一不删）** —— 只列**该场景目录这一层**（`iterdir`，不递归、不扫
`SR_SCENES_ROOT`；配了 `SR_PREVIEWS_ROOT` 时镜像树里对应的那一层也清）：

1. 名字是 `<stem>_preview.jpg`（或改名前的点号件 `<stem>.preview.jpg`，同一份缓存的旧名）；
2. **JPEG 注释带本平台的规则戳**（`preview_jpg.has_rule_stamp`，只判 `srprev:` 前缀，
   三代戳都认）。盘阵上别人手放的同名图、以及万一后缀恰好叫 `preview` 的**产物**，
   都没有这个戳；
3. `scene_search.is_scene_file` 不认它是场景源 —— 防「目录名以 `_preview` 结尾时把场景源
   自己删了」；
4. 不是符号链接、是普通文件。

**与急烤的竞态**：删除前先把该目录所有 `COMPLETED` 任务行写 `cleared`，挡住后续认领
（这是**先写库再删文件**的顺序，反过来就等于删完几十秒后又被烤回来）。走的是一条 CAS
（`store.mark_preview_cleared`），不能拿无条件的 `set_preview_state` 顶替 —— 那会把别人
写下的 `running` 一起盖掉。若该目录**此刻**有任务处于 `running`，整景计「跳过」并写明原因。
残余窗口（认领恰好发生在读状态与删除之间）消不掉，模块 docstring 里写明了，明细里也如实
呈现「删了之后文件可能又出现」。

**回不来的与回得来的**：删掉的是缓存，`<场景>.tif` / `<编号>_mask.tif` / 源即显示件的
`.jpg` 一律不碰（判据 3、4 就是为它们设的）。缓存本身**下次打开重烤即可恢复**，所以这不是
数据损失，代价是那一次几十秒的烘焙。**空目录不删** —— 镜像树骨架留着，下次烘焙直接落进去。

**结论四值怎么分（`cleared` / `nothing` / `skipped` / `failed`）**：一个目录里可能「删掉了
两份、跳过了第三份（没有规则戳 / 是场景源）」，这时结论是 `cleared`，**没删的那份另在 `reason`
与明细里点名**（用户不会为了查漏再去展开明细）。反过来，目录里躺着同名件、却一个都不是我们的
缓存时，**不能报成 `nothing`**：`nothing` 说的是「盘上本来就没有」，那是假话，而且前端按结论
决定行去留 —— 报 `nothing` 会把这一行摘掉，可盘上那份文件明明还在，重新检索又出现「已生成」，
看起来像清除没生效。所以 `nothing` 只在「一个同名件都没有」时用，其余没删成整条计 `skipped`。

**前端这一层（三件事，缺了功能会「看起来没生效」）**：

- 行是**页面局部移除**的（`cleared` / `nothing` 摘行，`skipped` / `failed` **留行**让人看见），
  表头 chip 上的「命中 M」跟着递减；空表时文案说的是「已从列表
  移除（场景与源文件都还在盘阵上），重新检索即回来」，不是「没有匹配场景」；
- 本地的预览 blob 缓存按 scene id 前缀失效（`forgetScenePreviewBlobs`，见契约 §3.8.2），
  否则同一会话内点开还是那份旧字节；
- **取静态图那一下绕开浏览器 HTTP 缓存**（`fetchSceneJpg` 在本次真的触发过烘焙时给
  `cache: 'no-store'`）。`?div=N` 那条击穿只对「档位变了」有效，而清除缓存是**同一个档位
  重烤**，URL 逐字不变 —— 不绕的话 nginx 的 `max-age=3600` 会把旧字节端上来，
  用户会以为清了个寂寞。这条同样修好了规则戳换代（v2→v3 原地重烤）那条老路。

---

### 4.13 早期方案的取舍（2026-08-27 → 09-01，已收口）

- **显示亮度拉伸（ENVI 风格）与像素定位**：已实现并通过端到端测试。
- **加速大图读取**：真实文件结构已确认（无压缩、每行一个条带）→ 不需要金字塔，任意窗口可以直接按
  字节切片读取；稀疏条带预览已完成，预览分辨率曾提升至长边 8192。
- **2026-09-01 拍板 = JPG 中间产物**：网页显示导出的 JPG（长边 8192 ≈ 原图 1/3），不再在浏览器内对
  原始 TIF 做全分辨率切片读取。
- **已放弃的方案**：「按可视区域按需读取全分辨率瓦片」（缩放超过预览分辨率时，按可视窗口把条带切片
  叠加上去绘制）。其前提条件已被 JPG 中间产物方案取代。若将来重拾，在无压缩条带下仍可直接按字节
  切片实现、无需金字塔 —— 相关工作只是冻结，没有丢失。
- **取舍说明**：JPG 为 8bit 有损 + 8192 ≈ 1/3 分辨率，网页内「查看超过 1/3 的细节」这一能力被放弃，
  更细的细节只能查看 JPG 产物或原始文件；掩码保持「预览粗画 → `thumbToOrig` 换算回全分辨率」。
- **后续修订**：09-17 起烘焙规则改为 v2（各边 1/2 + 直方图均衡），09-19 起改为用户可调档位
  （v3，默认各边 ÷4）—— 上述「8192」为当时口径，现行口径见 §4.4 与 §4.5。

## 5. 常见问题

**Q：为什么缓存判定要读 JPEG 注释，不能只比尺寸？**
比尺寸判不出拉伸规则的变化；而且长边恰好为 16384 的图两种规则烤出的尺寸相同。注释签名把尺寸、拉伸、质量三者一起覆盖了。

**Q：`hasPreview` 为什么库外也要填真值？**
库外用不上静态 URL，但前端要靠它判断「这次会不会触发烘焙」并提示用户等待。若因为「用不上」就一律留 false，第二次打开会重复提示。

**Q：改了烘焙规则，需要清理盘阵上的旧缓存吗？**
不需要。旧图没有新签名，首次打开时会被判定失效并原地重烤。代价是每张图在升级后第一次打开会慢一次。
2026-09-19 的档位改版就是这种情况：盘上所有预览的戳是 `v2`，新代码认 `v3`，
于是逐个场景首次打开时重烤一轮（**惰性**，不是一次性全量；把滑块停在 ÷2 也一样会重烤）。
静态 URL 那条另有一层 nginx 的 `max-age=3600`，靠前端给 URL 拼 `?div=N` 击穿 —— 档位变了查询串
就变，浏览器拿不到旧档位那张。
**想立刻全清也可以走人工入口**（2026-09-22 起）：场景库的「清除选定 / 全部清除」，见 §4.12。

**Q：`/preview` 返回的尺寸和 `row.W/H` 不一致，是 bug 吗？**
不是。`row.W/H` 是元数据尺寸（源图尺寸），JPEG 是各边 1/`div`。前端按元数据换算掩码坐标正是依赖这一点。

**Q：为什么档位要读盘上那份 JPEG 的注释戳，前端直接记住自己选了哪档不行吗？**
不行。前端记住的只是"这次会话里选了哪档"，不知道**盘上那份**是哪档烤的 —— 换台机器、换个浏览器、
或者别人先烤过，前端的记忆就是错的，于是会端着旧档位那张图当新的用。戳是唯一的事实来源。
`previewDiv` 为 `null`（旧格式戳 / 读不出）同样按"不符"处理，代价只是一次惰性重烤。

**Q：拖拽命中为什么需要「文件名 + 字节数」两个指纹？**
同一个场景目录里可能躺着不止一张图：RC 场景的输入影像是 `PAN.tif`，而 `input_scene_path` 的候选次序是 `<目录名>.tif` 在前，返回的是它。只比字节数，用户拖进来的可能是一张**不是 SR 实际会读**的影像，那之后画的掩码坐标会整片落在别的图上。判定在服务端（`_fingerprint_mismatch`），对不上就 404 + 列出原因，前端退回浏览器本地解码。

比的对象按拖进来的是栅格还是 jpg 分岔：栅格比「输入影像 stem + 字节数」；**拖 jpg 时只比「名字 == 场景目录名」**——jpg 是显示件，SR 从不在它上面跑，与输入 TIF 是两份产物（字节数必然不等），而拿它的名字去比输入影像的 stem 会在纯 RC 场景（目录里只有 `PAN.tif`）恒 404。详见 [api-contract.md](../planning/api-contract.md) §3.5。

**Q：拖拽命中之后，本地文件那条解码还跑吗？**
不跑。`viewer.activate` 先 `await tryLinkScenes(rec)`，命中就直接返回 —— 真机上一次全图解码几十秒、几百 MB，而服务端那份预览已经在手上。也顺带避开了「本地图先画出来、几百毫秒后又被 JPG 换掉」的闪烁。没命中才走本地解码。

**Q：什么时候会走 Pillow 兜底？**
采样器判定布局不支持（压缩、tiled、多波段、planar≠1）时。兜底只对像素数 ≤ 67M 的文件开放，因为 Pillow 会整图解码，1.1GB 的场景会直接打爆内存。超过 67M 的不支持布局会以 `PreviewError` 上报 422，而不是勉强解码。

**Q：并行读为什么是 8 路？**
盘阵若是单块 HDD，磁头竞争可能让更高并发反而更慢，因此取了保守值。这个数字同时受「盘阵实际存储形态」影响，属于需要在真机上确认的项。

**Q：作业跑完了，为什么产物预览还是没烤（`preview_state` 是 `skipped`）？**
照 `preview_note` 的 slug 对号入座，见 §4.9：`sandbox` = 这次跑在沙箱私有副本上，盘阵里根本没有
产物（切回 slurm 提交时这是**正常结局**，不是故障）；`product_missing` = note 里列的那几个候选名
一个都不在 —— 先分辨是「名字猜错了」还是「作业本身没产出」（云限额跳过是合法 COMPLETED，
本来就不会有产物）；`unwritable` = 产物目录对服务账号不可写，兜底留给打开时那条
`X-SR-Preview-Fallback` 路径；`source_changed` = 产物在烘焙途中被改写，本次一个字节都没写。
`preview_state` 是 `null` 则说明这一行从没进过急烤队列：年龄窗口之外的老行（迁移不回填）、
或者急烤被 `SR_PRODUCT_PREVIEW_DIV=0` 关掉了。

**Q：急烤会不会把用户打开这件事拖慢？**
不会**等**它：两条路不共享锁、互不阻塞，用户打开时走的仍是同一套 `ensure_preview_jpg`。
但会**抢盘**：急烤要把整个产物读一遍（÷4 下峰值内存约 400MB、GB 级读盘），同一时刻打开
别的场景可能略慢 —— 这正是「每轮一件、并发 1」的由来，它把这份争用压到最小，不能消除。
档位相同时用户命中急烤刚写的那份（快），档位不同就再烤一份、落点被覆盖（§4.8 那条
div 抖动）——代价是多花一次几十秒，不是错误结果。

**Q：这条链路的常量与浏览器侧的 8192 是什么关系？**
无关，是两条独立的路。`SPARSE_PREVIEW_MAX` / `JPG_MAX` / 导出降档用的 `_exportCap` 属于本地文件路径的浏览器侧预览与导出，仍然适用 8192 长边；本文这条路的尺寸由 `div` 档位决定（`max_edge = round(max(W,H) / div)`），不受那组常量影响。

---

## 相关文档

- [docs/status/timeline-archive.md](../status/timeline-archive.md) —— 历史时间线，其中记录了烘焙规则 v2 的六项决策与验证数据
- [docs/experience/gui-experience.md](../experience/gui-experience.md) —— §9 与 §9.1 收录了这条链路改版时遇到的具体问题；§10 是**图像对比**（一个画布两个格子）的经验，与本文的关系在下面这一段
- **本文与图像对比的关系（2026-09-20 一句话）**：对比功能自己**不烘焙**，它烤的就是本文这条路 —— 想看同场景的三类图（输入 / 本轮超分产物 / NOSR）时，前端拿 `GET /api/scenes/{id}/siblings` 给出的 id 直接调本文的 `GET /api/scenes/{id}/preview?div=N`，三条入口共用同一套档位与规则戳，落点各归各的（`<各族自己的源 stem>_preview.jpg`，见 §4.8）；前端消费口径见契约 §3.8.1，两个格子的坐标与共享变换见 `gui-experience.md` §10。
- **前端这一层怎么少烤几次（2026-09-20 补）**：客户端有「预览 blob 的字节封顶 LRU + 已开图去重 + 进对比模式的后台预取（默认关）」三件事，**预取的合格项判据就是「盘上已有一份现成预览且档位对得上」**，所以预取只可能命中本文写下的文件、永不触发烘焙。键与边界见契约 §3.8.2；为什么以**水平**归一化范围为准、`viewFor` 怎么记账见 `gui-experience.md` §10.7。
- [docs/planning/api-contract.md](../planning/api-contract.md) —— §3.5 是场景接口的契约描述
- [docs/knowledge/jpg-export-background.md](jpg-export-background.md) —— 浏览器侧的 JPG 导出，与本文的产物是两回事
- [docs/knowledge/platform-tutorial.md](platform-tutorial.md) —— §7 从架构角度说明盘阵场景为何改为读服务端 JPG
