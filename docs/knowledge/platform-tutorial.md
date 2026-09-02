# 从 0 开发一套遥感影像处理平台 —— 前后端背景知识教程

> 日期：2026-09-02 · 状态：草稿 · 分类：docs/knowledge（背景知识 / 技术实现说明）
> 读者：**有 Web 基础（HTTP、HTML/CSS/JS）但没接触过 Vue3、FastAPI、遥感大图与本仓库**的人。
> 目标：读完你能看懂本仓库的前后端代码各自解决什么问题、为什么长成现在这样，并掌握一条"从 0 把它做出来"的思考主线。
> 与其它文档的关系：仓库里现成的文档大多是**档案/踩坑记录**（按时间倒排、零散）；本教程把它们梳理成一条有因果的阅读线。最细的事实仍以档案为准：踩坑看 [docs/experience/gui-experience.md](docs/experience/gui-experience.md)，下一步接着干看 [docs/status/current-question.md](docs/status/current-question.md)，阶段 4/5 的需求和已定决策看 [docs/planning/frontend-phase4-phase5-prompts.md](docs/planning/frontend-phase4-phase5-prompts.md)。

---

## 目录

1. 这套系统是做什么的
2. 三个"地心引力"约束（不看这个看不懂任何设计）
3. 技术选型 + 仓库地图
4. 阶段 1：把"看图"的核心算法抽成与框架无关的 TS
5. 阶段 2：Vue3 工程骨架（路由 / 状态 / 本地 vendor / 离线构建）
6. 阶段 3：把查看器交互层"移植不重写"进 Vue
7. 阶段 4：后端 FastAPI 第一次进场 —— 盘阵场景读服务器 JPG
8. 阶段 5：平台 API 层 + 聊天 / 共享队列（REST / SSE，进行中）
9. 横向主题一：状态管理 —— Pinia store 到底管什么
10. 横向主题二：三层测试各测什么
11. 横向主题三：离线内网怎么部署（nginx / systemd / 环境变量）
12. 现在你可以动手了（改动清单 + 红线）

---

## 1. 这套系统是做什么的

一句话：给遥感影像 **"看得见、圈得中、算得上"**。

- **看得见**：在网页里打开单张几百 MB～1.8GB 的 16bit 灰度卫星图，秒级看到全貌。
- **圈得中**：在图上圈出要处理的区域（矩形 / 多边形 / 魔棒），得到"哪里要处理"的全分辨率掩码。
- **算得上**：把低分辨率影像用超分模型（SR）放大重建。模型跑在**内网的 GPU 集群**上（Slurm 调度作业），网页只负责"提交作业、看进度、取结果"，不自己算。

它面向一个朴素的使用者：遥感业务人员。未来是多人共用同一条 GPU 作业队列。

**四个页面对应四条能力**：

| 页面（路由） | 能力 | 状态 |
|---|---|---|
| `/viewer` 查看器 | 打开**本地文件**的 TIF，全交互看 + 画掩码 + 导出 JPG | 阶段 3 完成 |
| `/scenes` 盘阵场景 | 检索**内网盘阵图库**并打开（读服务器烘焙 JPG） | 阶段 4 完成 |
| `/chat` 聊天 | 用自然语言让 Agent 干活（Agent 会自己调工具） | 阶段 5 规划中 |
| `/queue` 任务队列 | 多人共享的 SR 作业队列（提交 / 取消 / 实时状态） | 阶段 5 规划中 |

**一条典型主流程**（等你做到最后会非常眼熟）：

```
遥感业务人员
 ├─ 在 /scenes 检索到某景图 → 打开看 8192 概览（服务器预生成的 JPG）
 ├─ 在图上画矩形圈出河流区域 → 得到掩码（0/255 + 质心 txt）
 ├─ 在 /queue 提交 SR 作业（或让 /chat 里的 Agent 调 run_sr 提交）
 └─ 队列页实时看到 PENDING → RUNNING → COMPLETED，取超分结果
```

> 别急着记流程图。先记住结论：**这本质是一个普通的「前端 SPA + 后端 API」网站**，难的部分全在"被浏览的数据特别大、环境特别受限"，也就是下一章。

---

## 2. 三个"地心引力"约束

> 这一章是整个仓库的"为什么"。本仓库 90% 的怪设计和阶段划分，都能用下面三条解释。后文每讲一个技术，都会回指它。

### 约束 1：浏览器一次内存分配约 2GB 封顶，Canvas 画布面积上限 16384²

**事实**：Edge/Chrome 里 `new Uint8Array(...)` 单次分配约 2GB 就抛 `RangeError: Array buffer allocation failed`——和电脑有没有 64GB 内存无关。Canvas 画布边长也有约 16384 的软上限。

**推论**：一张 2.4 万 × 2.4 万像素的 16bit 灰度图，光"像素数据"就要 `W×H×2 ≈ 1.2GB`，转成 RGBA 再乘 4 ≈ 2.4GB——**网页根本扛不住整幅解码**。所以本系统走"先出个概览，别碰全图"的路：要么只抽样读一部分行（稀疏条带），要么读服务器预先烤好的缩小 JPG。

这直接决定了阶段 1 的**解码分派决策树**、阶段 4 的**服务器预生成 JPG**、以及"浏览器的活尽量少干、重活留给后端"的整体分工。

### 约束 2：内网离线、CDN 不可达，第三方库必须本地内置

**事实**：部署/使用环境是内网，`jsdelivr` 之类的 CDN 根本加载不到。任何 `import 'https://cdn...'` 都会白屏。

**推论**：前端所有第三方库（TIFF 解码、压缩等）都必须**本地 vendor**（源码或压缩文件放进 `frontend/src/vendor/`，随构建打进产物）。也正因为"库一旦进去就不好换、改起来很贵"，才有了贯穿阶段 1→3 的纪律：**先把核心算法抽成与框架无关的 TS 并写像素级黄金测试，UI 层移植而不是重写**——换 UI 框架不动算法，换不了库也不至于推倒重来。

### 约束 3：真实数据全在内网盘阵机，外网开发机读不到

**事实**：开发这台是**外网机**；真实 1.1GB / 1.78GB 的大图全在内网 CentOS7 盘阵机，**无法导出、无法探文件头**。文件结构只能靠用户回传 ENVI 头信息（压缩方式 / 波段布局）或尺寸推断。

**推论**：开发机上没法真机联调，所以几乎所有"要接真实外部系统"的地方都做了**可测试的替身**（本仓库的核心方法论，贯穿始终）：

| 真实系统 | 开发机替身 | 触发器 |
|---|---|---|
| 盘阵图库 | 确定性假数据（`fake:true` 标记） | 未设 `SR_SCENES_ROOT` |
| LLM（Agent 大脑） | 固定脚本假 chat（mock） | `SR_LLM_MOCK=1`（阶段 5） |
| Slurm GPU 调度器 | 内存假调度器 | `SR_SLURM_FAKE=1`（阶段 5） |
| 命令行执行 | 注入假 `run_cmd` | 测试代码 monkeypatch |

**关键纪律**：假替身必须**显式标记**（`fake:true`、路径含 `<fake>`），并且真实消费端要**拒绝假数据**——否则开发机上"跑通了"的假流程，真机上会把占位路径当真交给 Slurm。

> 三条记牢，下面一路都会舒服。现在开始"从 0 造系统"。

---

## 3. 技术选型 + 仓库地图

### 3.1 为什么是这四样

| 层 | 选型 | 一句话理由 |
|---|---|---|
| 前端框架 | Vue3 + TypeScript（Vite 构建） | 多个页面共享同一个查看器画布 + 一套状态，正是响应式/共享 store 擅长的事 |
| 后端 | FastAPI（REST + SSE） | Agent / SR / 图像工具全是 Python，REST + 服务端推送 SSE 一个框架搞定 |
| 作业调度 | 保留 Slurm | 4 张 GPU 已经在用 Slurm 排队；平台只是 Slurm 的"网页客户端" |
| 任务记录 | SQLite | 单机、无需装服务；`sr_tasks` 表顺带当"幂等表"防重复提交 |

两个**明确不选**、免得你困惑：
- **不用 Spring Boot / 大型全家桶**：公司无统一技术标准，遥感后端全在 Python，再引一套 Java 门面只增维护成本。
- **不引入 LangChain/LangGraph 打底**：Agent 流程很短，自己写 ~200 行状态机更可控；等真遇到"跨小时断点续跑 / 人工审核"再考虑上 LangGraph 那层薄封装。

选型的完整推演见 [docs/status/current-question.md](docs/status/current-question.md) §3.1 的决策表。

### 3.2 仓库长什么样（先认路）

```
sr_agent_platform/
├─ frontend/                 前端（Vue3 + TS，阶段 2 起的全部前端活）
│  ├─ src/
│  │  ├─ pages/             每个路由一个页面（Viewer / Scenes / Chat / Queue）
│  │  ├─ components/        查看器的六块 UI 组件（画布/工具栏/文件列表…）
│  │  ├─ stores/            Pinia 状态（viewer / scenes / chat / queue）
│  │  ├─ lib/               与框架无关的 TS 核心（tifDecode / maskgen / scene …）
│  │  ├─ vendor/            本地内置第三方库（utif / geotiff / pako）+ 接线
│  │  ├─ router/            路由表
│  │  └─ main.ts            入口（装 Pinia + 路由 + vendor）
│  ├─ scripts/              gen-fixtures / package-offline（离线打包）
│  └─ fixtures/             入库的小测试图
├─ backend/                  后端（Python，阶段 4 起有 API）
│  ├─ api/                  FastAPI：app.py 组装端点、paths.py 路径白名单
│  ├─ agent/                Agent 自写状态机 loop.py + CLI
│  ├─ tools/                给 Agent（和将来 REST）用的"工具"注册表 + 4 个工具
│  ├─ services/             流程层：run_sr / slurm / store / mask / scene_search / preview_jpg
│  ├─ mta_grid/             纯算法基础模块（被 run_sr 调用，不是工具）
│  ├─ config.py             环境变量配置（一处定义，处处读取）
│  └─ tests/                pytest
├─ docs/                     全部文档（按类目：planning / experience / knowledge / status…）
├─ deploy/                   nginx.conf / systemd 单元 / 依赖清单 / 部署 README
├─ .e2e/                    浏览器端到端测试（gitignore，本机资产）
├─ test-tifs/                本地大测试图（gitignore）
└─ CLAUDE.md                 项目导航（新会话先读这个 + docs 索引）
```

> 想最快建立直觉：`frontend/src/lib` 是**纯函数算法**（在哪都能测），`backend/services` 是**流程编排**（薄、可注入假依赖），`backend/tools` 是**给 LLM 的薄壳**，`backend/api` 是**给网页的薄壳**。一层层往上叠，每层都薄。

---

## 4. 阶段 1：把"看图"的核心算法抽成与框架无关的 TS

### 4.1 为什么要"先抽算法，后上 UI"

最早的查看器是一个**单文件 HTML**（`tif_viewer/tif-viewer.html`，已冻结），里面算法和 DOM 写在一起。要把它变成 Vue 网站时立下纪律：**移植不重写**。

- 算法层（读 TIF、亮度拉伸、稀疏采样、掩码栅格化）已经踩光了坑，**逐字节保留**，只挪位置。
- 把它抽成**与框架无关的 TS**：不 import Vue、不碰 DOM、不碰 canvas——这样能在 Node 里直接跑，做**像素级黄金对照测试**（输入一张已知图，断言输出每个像素对不对）。

仓库体现：`frontend/src/lib/tifDecode.ts`、`maskgen.ts`、`viewMath.ts`、`source.ts`。UI 层（页面/组件/store）只 import 这些纯函数，不重写它们的内部逻辑。

### 4.2 你需要的遥感/TIF 最小词汇表

读算法前先把这几个词弄懂（每个都一句话）：

| 词 | 是什么 | 为什么重要 |
|---|---|---|
| TIF / BigTIFF | 图像容器格式。BigTIFF 是大文件版（用 8 字节偏移量） | 格式不同，解析偏移量的代码不同 |
| 位深 bits | 每像素几位。卫星图常是 **16bit**（0~65535）灰度 | 值域大，直接显示会一片黑 → 要"拉伸" |
| 条带 strip | TIF 按"若干行一包"存像素（strip per row = 每行一包） | 真实大图**无压缩 + 每行一条带** → 能按字节精确定位某一行 |
| 无压缩 (1) | compression=1，像素原样躺着 | 没压缩 = 可按需 slice 任意区域，**不用建金字塔** |
| 压缩 8 | Deflate 压缩（掩码产物用它） | 掩码很扁，压一下文件小 |
| photometric | 颜色语义：0=WhiteIsZero（白=0，要反色）、1=BlackIsZero | 忘了反色图会"颜色反转" |
| 单波段 | 一幅灰度图（1 个通道） | 决定用哪种解码路径 |

> 真机的两张代表图：GF07A03（1.11GB）、KF02B04（1.78GB），都是**无压缩 + 每行一条带 + 单波段 16bit**。因为规整，才敢走"稀疏抽样预览"而不做金字塔。

### 4.3 看大图的解码分派（仓库里最核心的一段判断）

浏览器读一张 TIF，**不能一张图一条路走到底**，得先探个头、再按情况选路。伪代码：

```
探文件头 → 拿到 {W,H,位深,类型,压缩,photometric,是否BigTIFF}

if 是"小的 8bit 无符号整数"且不超内存  → UTIF 整图解码（最快）
else if 压缩/tiled/多波段 大图          → geotiff 分块整图（一块块读 + 进度条）
else if 无压缩 + 条带 + 单波段 + 大图    → 稀疏条带预览（只抽读若干行，秒级）
```

- **为什么判断"解码字节数"**：`解码字节 ≈ W×H×通道数×(bits/8)`，超过安全线（仓库常量 `SAFE=1.3e9`）就得走分块/稀疏，不然撞上约束 1 的 2GB。
- **稀疏条带为什么快**：真实图每行一条带、无压缩 → 想抽第 k 行，能直接算出它在文件里的字节偏移，只读那一段。预览目标长边 `SPARSE_PREVIEW_MAX=8192`（约原图 1/3 清晰度）。
- **BigTIFF 的坑**：本地内置的 UTIF 库**解不了 BigTIFF**（解码后宽高变 0）。所以探测到 `big` 就强制走 geotiff 分块——这是"移植不重写"里唯一一次允许行为不同，因为原 HTML 在那条件下本来就是坏的。

对应代码：决策函数在 [frontend/src/lib/decode.ts](frontend/src/lib/decode.ts)（`needGeo` + `decodeOne`）；稀疏采样细节在 [frontend/src/lib/tifDecode.ts](frontend/src/lib/tifDecode.ts)。

### 4.4 亮度拉伸：为什么"原样显示"会一片黑

16bit 图值域 0~65535，而屏幕只有 0~255。直接映射绝大多数像素都会很暗。所以显示前要**拉伸**：把"大多数像素所在的值区间"映射到 0~255。

- **min-max 线性拉伸**：用整幅图最小/最大值做线性映射。
- **2% Linear**：取 2% 分位和 98% 分位做两端，**抗个别极亮/极暗像素把整体带崩**（卫星图常见）。这也是后面盘阵 JPG 烘焙用的默认档。
- 拉伸只作用于**颜色通道**，alpha 永远不缩放——曾经有个全黑 bug 就是 alpha 被拉伸系数乘没了（值 ~0.0039 × 255 ≈ 0 → 整图透明全黑）。这条写进了 [gui-experience.md](docs/experience/gui-experience.md) §3 当教训。

> 阶段 1 的产出形态就是"一沓纯 TS 模块 + 一堆黄金单测"。**这阶段没有界面、没有后端**，纯打地基。这正是它的价值：后面 UI 怎么改，像素对错的基准都锁在这了。

---

## 5. 阶段 2：Vue3 工程骨架

有了纯算法，第二步是搭一个**能跑的 Vue3 网站**并让算法在里面可调用。

### 5.1 为什么是 Vue3 + Vite + Pinia + vue-router

- **Vue3 `<script setup lang="ts">`**：单文件组件（模板 + 逻辑 + 样式一个 `.vue`）。
- **Pinia**：跨页面/组件共享状态。后面你会看到，查看器画布、当前图、掩码多边形、队列任务——这些"大家都要读、谁都能改"的状态都集中到 store。
- **vue-router**：`/viewer` `/scenes` `/chat` `/queue` 四个页面。注意它是 **history 模式**（URL 没有 `#`），所以生产环境 nginx 要配"找不到就回 `index.html`"（SPA 回退，见 §11）。
- **Vite**：开发时热更新，构建时打包成一个纯静态站（配 nginx 托管即可）。

### 5.2 浏览器环境 ≠ Node：给"会碰系统能力"的代码开注入缝

纯函数（`lib/`）不碰 DOM/canvas 所以能单测；但真要"建一个 canvas、写一个文件、读一个本地文件"这些浏览器能力时，直接 `document`/`canvas` 会让 Node 测试没法跑。做法是**把系统能力做成依赖注入**：

- `lib/browserKit.ts`：一个能创建 canvas 的"工具箱"，测试时注入假实现，真跑时注入 `document.createElement`。
- `lib/saver.ts`：保存文件的三种降级方案（File System Access → IndexedDB → 传统下载），全封起来。
- `lib/source.ts`：抽象"给一个偏移量，读一段字节"——本地文件一种实现，将来 HTTP 又是一种实现（阶段 4 时这个 HttpSource 被砍了，见 §7，但抽象保留）。

> 通用教训：**要测的东西尽量是纯函数；躲不开的系统能力尽量留成参数/接口，而不是在模块里直接用全局**。

### 5.3 本地 vendor：没有 CDN 的世界怎么引第三方库

没有 CDN，第三方库就得"住在自己家"。做法：

1. 库文件（压缩版或源码）放进 `frontend/src/vendor/`（如 `utif.js`、`geotiff.min.js`、`pako.min.js`）。
2. 在 [main.ts](frontend/src/main.ts) 用一行 import 完成"接线"，顺序有讲究：`pako → utif → geotiff`（因为 geotiff 解 Deflate 要依赖 pako）。
3. 构建时这些文件随打包进入产物 → **产物自带全部依赖、离线可用**。离线包脚本 [frontend/scripts/package-offline.sh](frontend/scripts/package-offline.sh) 负责把前后端产物打成可直接拷去内网的包。

> 有个版本红线：项目里打过补丁的 `utif.js` **绝不重装**——一重装补丁就丢、坑就回来（[CLAUDE.md](CLAUDE.md) 与记忆索引里都记着）。

### 5.4 git 边界

大测试图（`test-tifs/`）和浏览器端到端测试（`.e2e/`）都是**本机资产、不入 git**（体积大 + 涉及本机浏览器路径）。代码仓库里只留小 fixture（`frontend/fixtures/`）。新人 clone 下来不会自带几百 MB 测试图——这没问题，测试脚本本来就不依赖它们。

---

## 6. 阶段 3：把查看器交互层"移植不重写"进 Vue

阶段 2 有了壳和算法，阶段 3 把原 HTML 查看器的**交互层**（工具栏、画布事件、状态切换）搬进来。

### 6.1 职责怎么切（六个组件 + 一个 store）

| 块 | 职责 |
|---|---|
| `components/TifCanvas.vue` | 双画布（影像画布 + 掩码叠加画布）+ 平移/缩放事件 + 绘制模式事件 |
| `components/Toolbar.vue` | 选文件 / 拉伸下拉 / 输出目录 / 定位 X·Y / 绘制掩码 / 生成掩码 |
| `components/FileList.vue` | 左侧已打开文件列表 + 每个文件的布局探测 / JPG 状态 / 移除 |
| `components/DrawPanel.vue` | 掩码工具（矩形 / 多边形 / 魔棒 / 删除 / 容差）+ 合并重叠 + 撤销清空 + 导出 |
| `components/DecodeOverlay.vue` | 解码遮罩 + 进度条 |
| `components/StatusBar.vue` | 底部状态栏（当前图 W/H、拉伸、坐标等） |
| `stores/viewer.ts` | **总调度**：文件列表、当前激活图、视图变换、解码、拉伸、掩码、导出…… |

关键理念：**组件不互相调用逻辑，只向 store 发请求；画布变化统一由 store 用一个 `renderTick` 计数器通知重绘**。这样任何组件改了状态，重绘只有一个入口，不会"A 改 B 猜 C 画"乱成一团。

> "状态放哪"的答案：凡是**跨组件共享、会被并发修改**的放 store；只在本组件内、跟别人无关的临时 UI 态可以留在组件里。

### 6.2 打开一张图后发生了什么（rec 模型）

打开本地文件 = 探测头部 → 按 §4.3 决策树解码 → 产出一个 **rec**（一条"图记录"）：`W/H`、缩略图画布 `thumb`、像素数组 `src`、统计 `stats`、走的是哪条路 `route`、状态文案等。当前屏幕显示的是"当前激活的 rec"，掩码画在上面。看 [stores/viewer.ts](frontend/src/stores/viewer.ts) 里 `openSceneJpg` / `decodeRec` 的形状你会很熟。

### 6.3 Web 异步 UI 的通用教训（本仓库踩过的）

- **进度回调用"当前图"守卫**：解码是异步的，用户在等的时候可能切换了图 → 只有"回调里的图还是当前激活图"才刷新 UI，否则丢弃。否则旧图进度会把新图界面刷乱。
- **令牌守卫防重复副作用**：导出 JPG 是重操作，用 `_jpgToken` 标记当前这次导出；用户中途又点一次/换了图，旧 token 作废。另一个：canvas `toBlob` 遇到"内存不够"抛错时，自动把导出尺寸从 8192 **降档到 4096 重试一次**。
- **异步删除要防误删**：删掩码区域先闪红 200ms 再真删，回调里校验"还是同一张图"才执行——防止闪烁期间切图误删。
- **"掩码合并"这种重活要分批让出主线程**：一次算几千万像素会卡死页面，改成每批 `yield` 一次、配进度条。合并期间再点别的就丢弃结果。

> 这些都是**通用 Web 异步 UI**的教训，跟遥感无关，值得记。

### 6.4 掩码的画与存（前后端一致约定）

在预览图上画的多边形，坐标最终要换算回**全分辨率**（缩略图坐标 → 原图像素坐标，`thumbToOrig`）。产物格式前后端统一：

- `掩码.tif`：uint8 灰度，像素只有 **0/255**（0=不处理、255=要处理），Deflate 压缩；和原图同尺寸。
- `掩膜中心点坐标.txt`：参考文件的固定格式（UTF-8、CRLF、两行全角表头），每区域一行 `序号,质心X,质心Y`（面积质心，2 位小数）。

填充约定要**前后端一致**：顶点是像素中心、边界像素**含在内**。所以"栅格化多边形"这个函数前端 [maskgen.ts](frontend/src/lib/maskgen.ts) 和后端 [services/mask.py](backend/services/mask.py) **各实现一份，再互相逐像素对比测试**——两边必须一致，因为前端也可能直接出掩码下载，后端也要能出。

---

## 7. 阶段 4：后端 FastAPI 第一次进场 —— 盘阵场景读服务器 JPG

前三个阶段全是"本地文件路径"：浏览器自己读本地 TIF。阶段 4 才把**内网盘阵**接进来，也是**后端第一次有 HTTP API**。

### 7.1 为什么盘阵场景是"读服务器预生成的 JPG"，而不是读 TIF

盘阵的真图有 1.1~1.8GB。若让浏览器去读，立刻撞约束 1（2GB / 16384²），而且要在浏览器里写一套"按 Range 读 TIF 字节"的复杂逻辑（原计划 HttpSource，后来砍了）。改成更简单的分工：

1. **显示 = 服务器预生成的 8192 长边 JPG**（稀疏采样 + 2% 线性拉伸已"烤"进像素里），浏览器只 `<img>`/画到 canvas。
2. 浏览器**不再碰盘阵的原始 TIF 字节**。← 这一刀把 2GB/16384 两个约束在盘阵场景下直接消掉了。
3. **本地文件路径保持原样**（仍走稀疏 TIF 读法）——阶段 1~3 交付的行为零改动，两种数据源并存。

后端也因此第一次上场：它能在盘阵机本地读 TIF、烤 JPG、托管给网页。**"重活往后端挪"**在盘阵场景成为现实。

对应决策在 [docs/status/current-question.md](docs/status/current-question.md) §3.4 + §9，和 gui-experience §9。

### 7.2 FastAPI 最小骨架长什么样

后端 [backend/api/app.py](backend/api/app.py) 是一个函数工厂 `create_app()`，所有路由挂在 `/api` 下，配置全靠环境变量（见 §11）。两个核心端点：

- `GET /api/scenes`：盘阵图库检索。逐行返回场景元数据，并补上 `W/H`（优先读伴生的 `.hdr` ENVI 头；没有就探测 TIF 头并做 LRU 缓存）+ `jpgUrl`（nginx 静态地址；还没生成就是 null）。
- `GET /api/scenes/{id}/preview`：**懒生成**——JPG 不存在（或比源文件旧）就先在服务器烤一张，再返回。烤完落盘，下次 nginx 静态直出，秒开。

场景 id 用 **base64url(相对路径)** 做不透明 id，前端只拿 id，不知道也不该知道盘阵绝对路径。

### 7.3 路径白名单：为什么必须有 + 怎么做

后端暴露盘阵文件，最怕的是被人构造 `../` 或传绝对路径去读白名单外的文件。安全做法（[backend/api/paths.py](backend/api/paths.py)）：

- 所有返回/生成路径必须 **realpath 后落在场景根目录内**；拒绝 `..` 穿越、白名单外绝对路径、以及 `<fake>` 占位。
- scene id 解码时补回 `=` 填充、拒绝坏 id、再 realpath 校验一次。
- **fake 数据永不允许被当真去生成预览**——开发机上能出假数据，真机上绝不能因此去碰文件。

> 通用教训：**凡是对外部暴露"文件/命令"的能力，第一件事是画清能碰的边界，然后在边界上拒绝一切越界输入**。run_sr 侧同理：`lq_path`/`mask_path` 必须绝对路径且不含 `<fake>`。

### 7.4 "同构 rec"——让掩码/缩放全复用的大招

盘阵打开的场景，和本地文件路径打开的图，在内部被造**同一种 rec**（route = `'jpg'`）。区别只在：

- `src` 来自 JPG 画布像素，且 `stats` 被固定成 0..255 → 任何交互拉伸都退化为"恒等"，不会把服务器烤好的 2% 拉伸再拉一遍（`paintStretch` 对 jpg 直接早退）。
- 掩码换算回全分辨率时，`scale` 用**元数据 W/H**（服务端给的），而不是本地探 TIF。

好处：平移、缩放、画掩码、合并、删除……**全复用**，前端几乎没为盘阵单独写交互逻辑。这就是"算法层/状态层同构、只有数据来源不同"带来的收益。

### 7.5 前后端各写一份的"镜像常量"——改一边必须改另一边

服务器烤 JPG 的采样和拉伸语义，必须和本地路径的"看起来一致"。所以后端 [preview_jpg.py](backend/services/preview_jpg.py) **镜像前端**的映射：

- 稀疏采样：目标列 `round(j*(W-1)/(pw-1))`（端点对齐，杀右/下边条纹——有个 bug 是用了 `ceil` 导致右边最后一列重复像素）。
- 拉伸：2% Linear；WhiteIsZero 先反色；常量图退化为"全 0→黑、其它常量→128"。
- 只读采样行所在的条带（大图不整幅载入 → 服务器内存和 1.78GB 图幅解耦）。

**同一组常量前后端各写一份，改任一端必须同步另一端并跑两端单测**——这条写进了经验文档。这类"双实现"很容易慢慢跑偏，靠像素对照测试锁住。

---

## 8. 阶段 5：平台 API 层 + 聊天 / 共享队列（进行中）

> 状态提示：本节描述的契约处于**评审中**（[docs/planning/api-contract.md](docs/planning/api-contract.md)），代码尚未落盘。理解"要做什么、为什么这么设计"即可，细节以定稿契约为准。

前几章已经有 Agent（`backend/agent`）、4 个工具、任务幂等表、Slurm 客户端——但它们只被命令行（`python -m backend.agent "..."`）用过。阶段 5 把它们**暴露成网页可调的 REST/SSE**。

### 8.1 Agent 是"自写的状态机"，不是魔法

后端 Agent 核心 [backend/agent/loop.py](backend/agent/loop.py) 是一个自己写的循环：拿着用户消息和"工具清单"，问大模型"要不要调工具、调哪个、参数是什么"；大模型要调 → 执行工具 → 把结果喂回去 → 大模型再回复。反复直到大模型说"我说人话总结"为止。

工具是"注册表"式的（[backend/tools/contract.py](backend/tools/contract.py)）：每个工具用装饰器声明名字/描述/参数 JSON Schema + 一个 `run(**params) -> {ok, data, error}`。注册表能自动导出 OpenAI 兼容的工具清单——**同一份清单既喂给大模型做 function calling，又用来生成给网页的 REST 端点**（阶段 5 的 `/api/tools/{name}`）。

第一批 4 个工具：`search_scenes`（检索盘阵）、`run_sr`（提交超分作业到 Slurm）、`sr_job_status`（查作业状态）、`fix_bad_lines`（修复坏行——真实图像处理工具）。

> 为什么自写而不引 LangChain：流程很短（消息往返 + 工具分发 + 迭代上限），自写可读可测；引全家桶反而学一套框架。等未来要"跨小时断点续跑、人工审核"再考虑加薄层 LangGraph。

### 8.2 mock LLM：没有真模型时怎么"演"完整对话

Agent 要接大模型，但开发机（外网机）没配模型端点 / 不想每次测试都联网。于是给 LLM 调用层留注入点（`_default_chat`），并加一个环境开关：`SR_LLM_MOCK=1` 时返回**假 chat**，按**固定脚本**演——先声明调 `search_scenes`（离线也有真/假数据可返回），再回一句总结。这样：

- 网页聊天**不依赖任何模型**也能演示完整"调工具 → 回结果 → 总结"的循环；
- 端到端测试能**确定性地**断言 SSE 事件序列（tool_call → tool_result → turn_done）。

红线：mock **只替换 LLM 调用层，不动状态机逻辑**——loop.py 行为仍被 18 个既有测试锁死（约束 3 方法论：替身只替"外部那层"）。

### 8.3 SSE：怎么让网页"实时看到"Agent 一步步在干嘛

Agent 跑一圈要几十秒，用户不可能干等。用 **SSE**（Server-Sent Events）把过程"推"给网页：

```
POST /api/chat/sessions/{id}/messages     ← 前端发一条消息
响应是 text/event-stream，一帧一行 data:{json}\n\n：
  {"type":"turn_start",   ...}
  {"type":"tool_call",    "name":"run_sr","args":{...}}
  {"type":"tool_result",  "name":"run_sr","ok":true,"data":{...}}
  {"type":"turn_done",    "content":"已提交作业 12345"}
（预留 {"type":"token","delta":"…"} 给将来的 token 流式渲染）
```

实现要点（契约里写清了）：
- **POST 即流**：一次请求 = 一回合，请求的响应体就是 SSE 流。因为同步的 `run_loop` 是阻塞的，要丢到线程池跑，事件经 `asyncio.Queue` 桥回主协程逐帧下发。
- **同一个会话同时只允许一个回合**（并发就 409"会话正忙"）——消息是有顺序的，不能两个人同时往一个对话里插话。
- **前端不能用 `EventSource`**（它只支持 GET），要用 `fetch` 发 POST 再自己读 `response.body` 流、手工切 SSE 帧。为此会写一个可单测的帧解析器。
- **聊天会话 = 单会话 + 「新建」按钮**，持久化到 SQLite：刷新页面从历史恢复，切回旧会话继续聊（复用 loop 既有的 resume 语义）。

### 8.4 共享队列：任务表 + 幂等 + 状态机

网页提交一个 SR 作业 = 调后端 `submit_run_sr`，它干的事（[services/run_sr.py](backend/services/run_sr.py)）：

1. 把高层参数（`lq_path`/`mask_path`/`sr_scale`/`gpu`/`cloud_limit`…）拼成一个 `config.xml` + 一个 Slurm 批处理脚本；
2. **先查 `sr_tasks` 幂等表**（按参数指纹）：同参已有作业且还在跑/已完成 → **复用，不重复提交**（返回 RESUMED_*）；只有失败/状态未知才重跑；上次提交被中断（没有 job_id）→ 明确报错"勿盲重试"。
3. sbatch 提交 → 记下 job_id。

网页队列页（`/queue`）：
- 列出所有任务 + 实时状态。状态来自后台一个**校准器**周期对每个活动任务调 `squeue/sacct`（因为 Slurm 自己会变状态，服务器不轮询就不知道），状态变化通过 `/api/queue/events` 的 SSE **广播**给所有正在看的网页。
- 状态是"状态机"不是百分比：`SUBMITTING → PENDING → RUNNING → COMPLETED/FAILED`。

> 为什么"假 Slurm"能离机测：开发机没有 sbatch。规划加一个 `SR_SLURM_FAKE=1` 的内存假调度器——`config.xml`/脚本/幂等表**全走真实逻辑**，只把"squeue/sacct 返回什么"换成可配置推进的假结果。跟 §7 场景 fake 同一方法论。

### 8.5 查看器画完掩码 → 提交 SR

闭环的最后一块：查看器在图上圈完区域后"一键提交超分"。因为掩码栅格化（全分辨率 0/255）是重活且要在盘阵机上落盘，规划为：

1. 查看器把多边形（已按元数据 W/H 换算回全分辨率）+ 场景 id POST `/api/masks`；
2. 后端栅格化掩码，写到**原图所在目录**：`<原图目录>/<stem>_mask.tif` + `_mask.txt`（用户确认：掩码与超分结果都落在原图同一目录）；
3. 返回 `mask_path` 和 `lq_path`，前端用它**预填队列表单**，用户点「提交」才真正提交（Slurm 是真副作用，不自动发射）。

---

## 9. 横向主题一：状态管理 —— Pinia store 到底管什么

如果你跟着时间线读到这里，会看到 store 反复出现。横向收个尾，把"状态管理"这件事讲透：

**什么时候需要共享 store**（本项目三条都命中）：
- 多个页面/组件要读**同一份数据**（查看器画布、队列任务）；
- **谁都能改**它，改完别人要看到（多人队列、当前激活图）；
- 有**异步 + 并发**，改错顺序会乱（解码进度、SSE 事件到达）。

**Store 的三种模式**，本项目正好都用上：

| 模式 | 例子 |
|---|---|
| **服务器状态缓存**：从后端拉，存一份本地副本 | `stores/scenes.ts` 拉 `/api/scenes` |
| **本地重型状态 + 操作**：复杂交互逻辑集中调度 | `stores/viewer.ts`（图列表/视图/掩码/导出） |
| **即将的 SSE 归并**：事件流 → 归并进状态 → 渲染 | `stores/chat.ts` / `stores/queue.ts`（阶段 5 占位） |

**"乐观更新 + 回滚"**：交互优先按"预期结果"立刻刷新界面（感觉快），请求失败再回滚。前提是**服务端是唯一事实源**（多人队列尤其如此），前端缓存随时能被刷新覆盖。

**数据流画个图**（以阶段 5 队列为例，先有概念）：

```
后端 Slurm 状态变化
  → 后台校准器轮询 → 状态变化
  → SSE 广播一帧 job_update
  → 前端 fetch 流读到帧
  → queue store 更新 job.status
  → Vue 响应式重渲染列表行
```

---

## 10. 横向主题二：三层测试各测什么

> 仓库三大测试体系，每层测的东西不同、互相不替代。搞清分工能省大量时间。

| 层 | 工具 | 测什么 | 为什么放这层 |
|---|---|---|---|
| **单元（纯函数）** | Vitest（前端） | 像素级解码/拉伸/掩码对照、URL 拼装、SSE 帧解析 | 纯函数无副作用、快、能断言"每个像素对不对" |
| **后端测试** | pytest | 流程逻辑 + **注入假依赖**：假 slurm 命令、假 LLM、假场景、临时 SQLite | 测"编排逻辑"而非真系统；对外副作用全部可注入 |
| **浏览器端到端** | puppeteer-core + 无头 Edge | 真页面加载、点按钮、真 canvas 出图、route 接线 | 只有真浏览器能覆盖"组件 ↔ store ↔ 画布"的接线 |
| **类型检查** | `vue-tsc --noEmit` | 全前端类型正确 | TS 项目的地基 |

**测试方法论三条铁律**（从本仓库能直接抄）：
1. **能测纯函数就别测浏览器**：掩码栅格化这种像素逻辑，放 Node 里逐像素断言，别放 e2e。
2. **给外部副作用留注入缝**：命令行执行器、LLM 客户端、调度器、数据库路径——全都能换成假的（对应约束 3 的替身思想）。
3. **golden 对照**：测"已知输入 → 每个像素的期望值"，而不是"大概变亮了"。

> 浏览器 e2e 有个真实坑：无头浏览器常"闪退、退出码 0、无任何报错"。仓库解法是每次用一个**独立的临时浏览器配置目录**（[.e2e/launchBrowser.js](.e2e/launchBrowser.js)），避免浏览器单实例目录锁。

---

## 11. 横向主题三：离线内网怎么部署

部署形态：**内网 CentOS7 盘阵机**上，一台起 nginx + 一个 FastAPI 进程（systemd 托管），网页是 nginx 托管的静态产物。全部离线安装，所以依赖清单要显式。

### 11.1 环境变量是唯一的"配置口"

后端所有可变配置都从环境变量读（[backend/config.py](backend/config.py)）：场景根 `SR_SCENES_ROOT`、预览缓存 `SR_PREVIEWS_ROOT`、LLM `SR_LLM_*`、Slurm `SR_BUNDLE_DIR` 等。所以**同一份代码在开发机（fake）和真机（disk）之间零改动切换**——只改环境变量。systemd 单元里把 env 写死，见 [deploy/sr-api.service](deploy/sr-api.service)。

### 11.2 nginx 管三件事

1. **静态托管前端**：`/` 指向前端构建产物，SPA 的 history 路由配"找不到文件回 `index.html`"。
2. **反代 `/api/` 到 `127.0.0.1:8000`**：前端 `fetch('/api/...')` 同源出去，nginx 转给 FastAPI。
3. **静态托管盘阵 JPG**：`/disk-array/` alias 到场景目录——预览 JPG 一旦生成，直接由 nginx 静态吐出（原生缓存，极快），不经过 Python。

**SSE 特别坑**：nginx 默认会缓冲代理响应，SSE 是"要持续往客户端推"，必须对相关 `/api/` 路径关掉缓冲并放宽读超时：`proxy_buffering off; proxy_cache off; proxy_read_timeout 3600s;`——不然网页收不到逐帧事件（见 [deploy/nginx.conf](deploy/nginx.conf) 与契约 §6）。

### 11.3 为什么交付物里常有"真机另排"

因为约束 3（开发机读不到真机数据/没真 Slurm），能离机自证的是：pytest + Vitest + e2e（用假后端/假调度器）+ 部署件。**真机项单独列验收清单**（首次 JPG 生成耗时、真 Slurm 提交、真模型调用），标注"需在 CentOS7/Win11 内网执行"。这是**诚实的交付边界**：能自动化的全自动化，剩下的写明让真机验收——不是偷懒，是外网机物理上做不到。

---

## 12. 现在你可以动手了

读完就上手的最佳路径：

1. **跑一遍现有测试**建立"绿"的体感：后端 `python -m pytest backend/tests -q`、前端 `npm test` + `npm run typecheck`。
2. **本地起后端** `python -m backend.api`（未设 `SR_SCENES_ROOT` → 自动 fake），`curl /api/health`、`/api/scenes` 看返回。
3. **本地起前端** `npm run dev` 打开 `/viewer`（拖一张 `frontend/fixtures/` 里的小 TIF）和 `/scenes`。
4. 找一个你想改的小点（比如给列表加一列），按"改 lib 纯函数 → 跑 Vitest → 接线 store/组件 → 跑 typecheck/e2e"的顺序推进。

**改动时对照这张清单**（本仓库最容易踩的联动）：

| 你改了什么 | 必须同步 | 红线 |
|---|---|---|
| 前端拉伸/稀疏映射常量 | 后端 `preview_jpg.py` 镜像逻辑 + **两端单测** | 改一端必须改另一端 |
| REST / SSE 契约（端点、字段、事件） | `docs/planning/api-contract.md` 先改，再改代码 | **契约文档先于代码** |
| 掩码填充/产物约定 | 前端 `maskgen.ts` 与后端 `mask.py` | 必须逐像素一致 |
| vendor 库 | 谁都不许"顺手重装" | 补丁版 `utif.js` 绝不重装 |
| 后端运行所需依赖 | `deploy/requirements-api.txt` | `openai` 锁 `>=1.40,<2`（aiohttp 3.8.3 不兼容 3.x） |
| 数据源的假替身 | 别让 fake 路径流进真实消费端 | `<fake>` 一律拒绝 |

**贯穿全文的三句话**（值得贴屏幕上）：
1. **大对象要么不碰、要么分块/抽样碰**——浏览器 2GB、Canvas 16384² 永远在背后盯着你。
2. **把重活留给能在数据旁边干活的机器**——盘阵 JPG 由盘阵机烤，网页只读结果。
3. **真实系统进不来时，造一个显式标记、能被拒绝的替身来测**——替身只替外部那层，自己的逻辑照真跑。

---

> 附：本篇是"教程视角"的梳理，具体数字/细节若与档案冲突，以档案为准（踩坑 [gui-experience.md](docs/experience/gui-experience.md)、状态 [current-question.md](docs/status/current-question.md)、阶段需求 [frontend-phase4-phase5-prompts.md](docs/planning/frontend-phase4-phase5-prompts.md)）。
