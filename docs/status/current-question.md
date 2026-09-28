# current_question — 平台现状与交接入口

> 用途：交接文档。新会话先读本文件，再读 [gui-experience.md](../experience/gui-experience.md)（踩坑经验），
> 即可无断点接着干。
>
> 本文件只写**现在是什么**。变更经过见 [timeline-archive.md](timeline-archive.md)（历史时间线），
> 真机勾选项见 [real-machine-acceptance.md](real-machine-acceptance.md)（验收单）。
> 日期：2026-09-24 · 状态：已定。

---

## 1. 平台是什么，现在能做什么

遥感影像超分（SR）处理平台。浏览器查看盘阵上的大 TIF、绘制掩码、提交 SR 作业、跟踪共享队列。
前端 Vue3 + TypeScript（Vite 构建，Nginx 静态托管，第三方库全部本地内置）；
后端 FastAPI + SQLite；超分算法调用 SR 生产脚本，不重写。

四条链路：

- **`/viewer` 查看器** —— 两种数据源（本地 TIF 文件 / 盘阵场景 JPG）。功能：亮度拉伸、像素定位、
  ROI 统计、掩码绘制（矩形 / 多边形 / 魔棒 / 合并重叠 / 删除）、图像对比（点选 / 分屏）、
  《待修复清单》导入与写回、提交 SR —— 行内「打开」按**那一行的影像类型**补出清单上惯例省掉的
  产品段（`…_001_L1` → `…_001_L1_PAN`，没写就 `_PAN`，见
  [api-contract](../planning/api-contract.md) §3.5 的 `product`）。拖入盘阵场景的显示件时会自动预热同景的**未超分那份**
  （NOSR），它出现在列表里时带 `NOSR` 标（与本体标区分、可点开进当前活动格）。
  清单导入后多一颗橘色「**一键解析**」：按行序逐景把本体 jpg 与 NOSR jpg 一次性生成到盘上，
  每景两张卡**共用同一个序号**依次入列（生下来不带像素、点开才取图），失败逐条记账标红、
  可随时停；点清单里任意一行，左侧那两张卡橘色点亮并滚过去（见 §6.4）。
  右侧「上下文侧舱」含 `[ROI/工具]` 与 `[Agent]` 两个页签。
- **`/scenes` 场景库** —— 盘阵场景检索（卫星 / 传感器 / 日期 / 关键词）、行内显示 W/H、
  打开（首次访问懒生成预览）、清除预览缓存（选定 / 全部）。列表是「上次检索」那一刻的
  快照、**跨刷新保留**，按「检索」才读盘阵当前事实（口径见 §6.2）。
- **`/queue` 共享队列** —— 提交 SR、状态随 SSE 推进、取消作业、耗时与产物目录。
  ⚠️ 这条 SSE 是 `fetch` + `ReadableStream` 拼的**文档级长连接**，所以
  `stores/queue.ts` 在 `window` 上挂了 `pagehide`/`pageshow`：**整页导航时 Vue 的
  `onUnmounted` 不跑**，不在 `pagehide` 里显式收流的话，每导航一次就漏一条连接，占满
  Chrome 每源 6 条之后页面会卡在「加载中…」而**后端日志全干净**（2026-09-28 的事故，
  机制见 [gui-experience §9.9](../experience/gui-experience.md)）。改这个 store 时别把
  那两个监听摘掉。
- **`/chat` 对话** —— agent 循环 + 工具调用（`search_scenes` / `run_sr` / `sr_job_status` /
  `fix_bad_lines` / 掩码栅格化），SSE 推送 `tool_call` / `tool_result` / `assistant`。

掩码产物：浏览器直接生成 `掩码.tif`（值域 0/255）+ `掩膜中心点坐标.txt`，
也可导出矢量 JSON 交后端栅格化（[mask.py](../../backend/services/mask.py)）。

## 2. 当前路线

- **SR 提交 = 本机 conda 直跑**（2026-09-14 定，`SR_EXECUTOR=local`）：后端在 node81-135 上用 SR 生产
  conda 解释器直接起进程，读锁定目录里已有的 `.tif` 与 `<目录名>_mask.tif`，产物写回同目录。
  `sbatch` 换 `bash`，配置 XML / 审计段 / 契约校验器 / 退出码文件整套原样复用。
  工作单见 [sr-minimal-prototype-plan.md](../planning/sr-minimal-prototype-plan.md)。
- **作业终态读退出码文件**，不读 `sacct`（真机 `AccountingStorageType=none`，`sacct` 永久不可用）。
  退出码 0 不等于成功；契约不满足时校验器以退出码 90 结束。
- **Slurm 冻结**（2026-09-14 中止）：单卡分配改用 `CUDA_VISIBLE_DEVICES` 即可，无需调度。
  代码分支、部署变体、校验器、验收清单保留为存量，重启时从
  [slurm-acceptance.md](slurm-acceptance.md) 接着跑。⚠️ 仓库默认值 `SR_DEFAULT_EXECUTOR` 仍为 `"slurm"`
  （[config.py](../../backend/config.py)），真机靠 env 覆盖。
- **LLM 走 OpenAI 兼容接口**（`SR_LLM_BASE_URL` / `_API_KEY` / `_MODEL`），底座未定，内网尚无可用端点。
  未配端点时用 `SR_LLM_MOCK=1` 的固定脚本跑通链路。
- **数据来源两棵树**：
  `SR_SCENES_ROOT`（当前指 datahub，供场景库检索）；
  生产树 `/DiskArray/GSHC2IMPS/PRODUCT/<年>/<月>/<日>/<卫星型号>/<段级目录>/<景级目录>`，
  可不搬数据直接在查看器打开，靠 [pathguard.py](../../backend/pathguard.py) 从文件名反推路径
  （规则见 [production-scene-naming.md](../sr_code/production-scene-naming.md)）。

## 3. 已知未收口

### 3.1 阻塞 A 段收尾

1. **需要一个未超分过的场景**。job 1 已证「提交链路 + conda 环境 + 变体 + 单卡绑定」全通，
   但现场景属于已超分状态，SR 脚本判定 `already SRed before` 后提前返回，未产出新产物，
   校验器据契约判 FAILED。二选一：换一个体积落在 `util.py` 三个区间内的场景目录；
   或把该目录的 `<目录名>_NOSR.tif` 改名还原为 `<目录名>.tif`（**先备份现有超分产物**）。

### 3.2 待真机确认（尚无实证）

1. **未超分那份在真机上实际叫什么名字**（⏱口径本身 2026-09-24 已定，不再是问题）。
   未超分那份 = **输入影像的 stem + `_NOSR`**（SC 即 `<目录名>_NOSR.tif`，RC 即 `PAN_NOSR.tif`）；
   从 `SR_code/util.py::writeTiff` 推导出的 `<产物 stem>_NOSR.tif` 退为**次选**候选，仍在清单里
   （那份也可能真在盘上）。候选有序、只拼名字不 stat，命中哪一个**如实报出名字**，不猜 ——
   `/api/scenes/{id}/siblings` 的 `nosrCandidates` 就是这份清单，拿到真机 `ls -l` 可据此校准
   **顺序**（不影响能否命中）。详见 [preview-bake-pipeline.md](../knowledge/preview-bake-pipeline.md) §4.11；
   验收动作见 [real-machine-acceptance.md](real-machine-acceptance.md) §C 末条。
2. 产物 tif 尺寸为输入影像的 **2 倍**（`code_0817_prod.py` 的 `scale: 2`），无真机实证。
   若成立，分屏对比两侧是「同一地面区域、不同像素网格」，对齐只能按百分比，±1 像素级比对做不到。
3. 生产树目录日期取「成像日 / 次日」中命中者，现按较年轻者推定，需一批真实目录名验证。
4. 服务账号 `User=nginx` 对盘阵场景目录有无 `unlink` 权限未验证 —— 场景库「清除缓存」依赖它，
   无权时该功能只逐条报失败，不删任何文件。
5. **2026-09-11 生产文件覆盖事故待善后**：`$BUNDLE/code_0817_prod.py` 被 28703 字节版本覆盖，
   旧版（33477 字节）无备份、不可恢复。待答：谁读这份文件？

### 3.3 已记录，暂不处理

1. `/preview` 在源文件不存在时回 422（而非 404）：这一路点下去拿到的是「预览生成失败：场景文件
   不存在」，前端只认**盘阵静态链**上的 404，所以不会亮灰块（也不会标 `purged`）。改造只需后端
   一行（`app.py::preview` 在 `abs_path.is_file()` 为假时回 404），但需重新部署，见 §4.1。
2. 09-15 记录：退出码文件已判 FAILED，而 `GET /api/queue` 仍报 RUNNING。此后 `_task_state`
   经 09-18、09-20 两轮改动，**未复核该现象是否仍存在** —— 下次真机提交时同时确认。
3. [.e2e/qa-theme.js](../../.e2e/qa-theme.js) 有 6 条陈旧失败断言（期望值是 2026-09-09 改版前的
   白色 chrome 与渐变主按钮）。已确认非当轮引入，未改动；改脚本期望值还是改主题需人定。
4. `test-platform.js` 的 `[D]`「耗时列」存在**间歇性**失败（09-22 两轮复跑，一次红一次绿，
    与当轮改动无关），未定位。09-27 复跑 4 次**全红**（含 `git stash` 掉当轮前端改动、重建
    dist 的对照组同样红）—— 至少在本机它更像稳定红：`waitFor` 等的是 `.qp-tbl tbody td.elapsed`，
    即队列表里得有一行。诊断时先看 `/queue` 那次 `page.goto` 之后 `queue.tasks` 是不是空的。
5. 预览内存不释放：每个文件的预览 `Float32Array` 常驻，各边 ÷4 约 145MB/张、÷2 约 600MB/张；
    分屏按设计同时保留两张，点选清单鼓励多开。当前只记录，无 LRU 释放。
6. 同一场景的 `<目录名>.jpg` 与 `<目录名>.tif` 两行预览相同（工作流 B 使两行落到同一份落点），
    且该落点会被不同档位的客户端互相顶掉（div 抖动）。去重与带档位落点留待对比相关的一轮一并定。
7. `SR_SCENES_ROOT` 是**单根**，没有多根写法；符号链接只有建在根本身才能通过路径校验。
8. 场景检索采用白名单：所在目录须含 `<目录名>_meta.xml`，且文件名为 `<目录名>.<ext>` 或 `PAN.<ext>`。
    **代价**：没有该 xml 的目录整个不显示（已接受）。
9. `deploy/nginx.conf` 里 `location /disk-array/` 内那条 `location ~* \.preview\.jpg$`
   （给预览 JPG 加 `max-age=3600`）**在 09-22 预览名收口成 `_preview.jpg` 之后就不再匹配**，
   该缓存策略事实上失效（现在预览 JPG 不带 `Cache-Control`，靠浏览器启发式缓存兜着）。
   要么把正则改回匹配 `_preview.jpg`，要么删掉这条子 location，需人定。

## 4. 下一步

### 4.1 开发机可做（无已知红项）

- **P1 待办四项**（[agent-orchestration-research.md](../knowledge/agent-orchestration-research.md) §5 承诺范围内）：
  1. 配置体验：支持 `.env` 加载（不依赖第三方库）+ `.env.example`；把 `system_prompt` / `max_turns`
     从 `loop.py` 挪进配置。
  2. 瞬时错误重试：模型返回 429 / 超时时现在直接终止循环，需补退避重试（当前只做了迭代上限）。
  3. 测试缺口：loop 处理畸形响应 / 空 choices 的用例；`fix_bad_lines` 的 uint8 / float 数据类型用例。
  4. CLI 的 `--resume <session_id>`（接口已就绪）。
- 后端一行改造：`/preview` 在源文件不存在时回 404（见 §3.3「`/preview` 回 422」条）。

### 4.2 必须在内网机做

逐项判据与勾选见 [real-machine-acceptance.md](real-machine-acceptance.md)。

- 上传本轮的 `frontend/dist` 与 `backend` 两个包（**必须同包更新**）到 node81-135。
- A 段收尾：造一个未超分过的场景，跑出真实产物（见 §3.1）。
- 未超分那份（NOSR）的拖入预热 + 真机名字校准（09-24 新增，判据见 §3.2 第 1 条与验收单 §C 末条）。
- 阶段 4 场景验收、阶段 5 掩码 → SR 真链、SSE 长连。
- 配置 LLM 端点后跑通真实 `/chat` 闭环。

## 5. 环境与硬约束

- **开发机是外网机，真实遥感图全部在内网机盘阵上**，无法导出、复制或读取文件头来探测结构。
  文件结构只能靠用户回传的 ENVI 头信息（Edit Headers 的 Compression / Interleave 字段）或按已知信息
  推断尺寸。**不能要求用户提供文件路径。**
- **内网 CDN 不可达**，第三方库必须本地内置。
- **浏览器单次内存分配约 2GB，Canvas 面积上限 16384²。** 大图必须走稀疏采样或分块，不可整图解码。
- **后端禁止扫盘**：代码中不得出现 `ls` / `glob` / `rglob` / `iterdir`，只允许 `stat` 用户明确给出的
  那一个路径。测试有用例把 `os.listdir` / `scandir` / `walk` 打成 `AssertionError` 来固定这条。
- **真机页面是 http，不是 SecureContext**：`showOpenFilePicker`、剪贴板等 API 一律不存在。
  需要写盘阵的功能必须走后端（服务账号 `nginx`）。
- **nginx：`/api/` 下再套 location 时，子块里必须重写一遍 `proxy_pass`**。nginx 只选一个 location，
  嵌套块**不继承** `proxy_pass`（final-location-handling 指令），漏了就是 nginx 自己回 404 HTML
  —— 2026-09-23 线上「老景打不开」正是这条（见 timeline-archive 09-24 条）。落盘后按
  [deploy/README.md](../../deploy/README.md) §四 的 `/preview` 一条自查（`ct` 必须是 `application/json`）。
- **浏览器 e2e 可用**：`.e2e/launchBrowser.js`（puppeteer-core + 无头浏览器，每次使用独立的临时配置目录）。
  候选顺序 **Chrome 优先** —— 本机 Edge 与正在运行的 Edge 实例握手会 `Code: 0` 闪退；
  换浏览器设 `SR_E2E_BROWSER`。

## 6. 现值速查

### 6.1 真机配置与仓库默认值的偏差（2026-09-16 经 `systemctl show` / `nginx -T` 核对）

迁移代码后须照此表在真机重建 drop-in，否则会退回仓库默认值。

| 项 | 仓库默认 | 真机值 | 载体 |
|---|---|---|---|
| `SR_BUNDLE_DIR` | `/DiskArray/ProductionSchedule/exe_CentOS7/SR_bundle/mmsr_bundle/codes` | `/DiskArray/tmp/wangrz/mmsr_bundle_240617/codes` | `/etc/systemd/system/sr-api.service.d/srscript.conf` |
| `SR_SR_SCRIPT` | `code_0817_prod.py` | `code_0817_prod_slurm.py` | 同上 |
| `SR_SCENES_ROOT` | 无（不设则 `source:fake`） | `/DiskArray/tmp/wangrz/datahub` | `/etc/systemd/system/sr-api.service.d/scenes.conf` |
| nginx `alias` | `deploy/nginx.conf` 中为 `/data/scenes/` | `/DiskArray/tmp/wangrz/datahub`（与上一行同值） | `/etc/nginx/conf.d/sr-agent-platform.conf` |

> 生产盘阵根确认为 `/DiskArray/GSHC2IMPS/`（层级 年/月/日/生产编号）；它与 `datahub` 的关系
> （拷贝还是软链）本阶段不追。

### 6.2 关键常量与口径

- **预览生成预览**：规则签名 `srprev:v3:div<N>+equal:q<Q>`；前端档位 各边 ÷2 · ÷4 · ÷8 · ÷16 · ÷32，
  默认 ÷4（`SR_PRODUCT_PREVIEW_DIV` 默认 4，`0` = 关闭）——**前端档位与该 env 是两个独立的 4，互不联动**。
  预览落点统一为 `<源 stem>_preview.jpg`。
- **掩码**：值域 0/255（0 = 不处理，255 = 处理）；`run_sr` 用 `cv2.threshold(>0)` 读取。
  矢量格式 `{"width":W,"height":H,"polygons":[{"label":"roi","points":[[x,y],...]}]}`，
  x 为列、y 为行，均为原图像素坐标。
- **SR 产物命名**：`<源 stem>_<suffix>.tif`；未超分那份为 **`<输入 stem>_NOSR.tif`**
  （SC 即 `<目录名>_NOSR.tif`），`<产物 stem>_NOSR.tif` 为次选。两义都标 `NOSR`，见 §3.2 与
  [preview-bake-pipeline.md](../knowledge/preview-bake-pipeline.md)。
- **场景判据**：目录须含 `<目录名>_meta.xml`；场景文件为 `<目录名>.<ext>`（SC 步输入）或
  `PAN.<ext>`（RC 步输入）。派生件按 `p.stem == p.parent.name` 排除。
- **路径映射**：`SR_DRIVE_MAP` 默认 `W:=/DiskArray`；`SR_ALLOWED_ROOTS` 默认 `/DiskArray`。
- **「已清除」灰块的判据只有一条（2026-09-24 起）**：打开这一行时在**盘阵静态链**
  （`/disk-array/…`）上撞了 404（`lib/api.ts::isSceneGone` = 404 且 URL 落在 `/disk-array/`）。
  打后端的 `/api/` 请求回 404 **不算**（那说明请求没走到后端，见 `isProxyMiss`）；按年龄的推定
  已于 09-24 整条删除。该标记只活在前端单次会话，重新检索即消失。
- **场景库列表 = 「上次检索」那一刻的快照，跨刷新保留（2026-09-28 起）**：整页刷新**不再**
  自动重检索，页面回到刷新前那一份列表（含「已从列表移除 N 行」的清除结果）；只有按「检索」
  才读盘阵的当前事实（届时被清掉的行回来、预览列显示「未生成」）。快照活在 `sessionStorage`
  （键 `sr.scenes.snapshot`，组装/解析见 `lib/scene.ts::scenesSnapshotOf` / `parseScenesSnapshot`）
  —— **刻意不用 localStorage**：它跨天留存，一份隔夜的旧列表会冒充刚扫过的盘阵；sessionStorage
  随标签页关闭即失效。判据是「上次检索 HH:MM」那一行读数 + 空表文案分两种。检索失败时快照整份
  作废（下次进入照旧自动检索，宁可重来也不留一份来路不明的列表）。清缓存的**边界**没变：清的
  始终是盘阵上那份 `<源 stem>_preview.jpg`，行去留只影响页面。
- **底栏只显示图名（2026-09-28 起）**：`StatusBar.vue` 常驻的只有 `activeRec.name`
  （分屏时前面挂 `[左]`/`[右]`）。尺寸、探针、路由、预览像素尺度、布局这几项挪进这条的**悬停
  title** —— 尺寸与布局左栏那张文件卡上本来就有（[FileList.vue](../../frontend/src/components/FileList.vue)
  的 meta 行），路由与预览尺度是排障项，不该常驻占一行。同日起布局文案「盘阵 JPG（N 尺度 +
  直方图均衡）」不再带「服务端已生成」：它只落在左栏卡的布局行，说清像素是什么即可，生成预览方
  是谁对看图的人没有信息量。
- **队列耗时口径**：纯算力时长（`started_at` → `finished_at`），不含排队；重启后基准回落库中状态。
- **任务表唯一键**：`sr_tasks.task_fingerprint`（参数内容 sha256），同参数重交复用同一行。

### 6.4 一键解析（《待修复清单》批量生成预览，2026-09-27）

- **不新增后端端点**：每景就是既有三条链按序走一遍 —— `POST /api/scenes/resolve`
  （吃那一行的影像类型补产品段）→ `GET /api/scenes/{id}/preview` → `GET /api/scenes/{id}/siblings`
  （取未超分那份的名字与尺寸）→ 再 `/preview` 一次。**刻意不做 batch 端点**：`preview` 是
  sync def、进了 anyio 线程池，掐响应停不下服务端已经在生成的那一份；而「试过哪些候选、
  各自为什么不行」的唯一真源在 `scene_search` / `resolve_scene`，批量端点重写一遍就是
  多一个「静默换路径」的入口。驱动的循环在 [stores/qclist.ts](../../frontend/src/stores/qclist.ts)
  （`bakeAll`）+ [lib/qcbatch.ts](../../frontend/src/lib/qcbatch.ts)（`runSceneBake`，**并发恒为 1**）。
- **每景两张卡、共用同一个序号**：本体卡的 `sceneDir = resolved.dir`，NOSR 卡的
  `sceneDir = siblings.lqPath`（两者都是同一个场景目录的 `as_posix()`），序号由
  `viewer.sceneOrdinalOf` 按 `sceneDir` 分组发号 —— 先清空再按行序入列，序号天然是 1,1,2,2,…
- **NOSR 卡的 `lqPath` 取场景目录、不是 `null`**：与「场景芯片」那条入口（`openSceneSibling`）
  逐字对齐，否则同一种图两条入口进来会长得不一样。「不能修复」由 `stageKind` 那道门管
  （`isIntermediateStage` 三处），不看 `lqPath`。
- **卡片「生下来不带像素」**：批量只把 jpg 生成到盘上，入列的卡 `thumb/src = null`，第一次点开
  才 `fetchSceneJpg` + `applySceneJpgToRec`（此时命中服务端缓存，秒出）。算式：各边 ÷4 的
  4 万² 景生成出 10000²，`applySceneJpgToRec` 同时留下 `thumb`（canvas RGBA ≈400MB）与
  `src`（Float32Array），**≈800MB/卡** —— 一批十几景 ×2 张装不下（浏览器单次分配 ~2GB）。
- **取消 = AbortController，且如实告知**：`resolve` 能掐（`apiResolveScene` 收 signal），
  生成图那两条 HTTP **掐不掉** —— 所以在飞的那一景让它生成完（落盘正是要的缓存），文案写
  「正在停止（等这一景生成完…）」。被掐掉的 resolve 是**取消**、不是这一景失败，不记进失败账。
- **失败与「缺失」分两张表**：`bakeFails`（标红 + 记「第几步 + 后端原话」）与 `bakeNotes`
  （「盘上没有未超分那份」这类盘上的事实）。**两张都不写 `statuses`** —— 那份会被
  `buildQcDoc` 写回盘阵上的 .txt、还驱动 `counts.done`，生成图失败不是质检结论。
- **进度不弹遮罩、不动 `busy`**：遮罩是模态的，会把用户正在看的图挡住、工具栏锁几十景。
  进度只走面板上那一行字 + 按钮三态（待命 / 停止 / 停止中）。
- **切图预填坐标**（2026-09-28）：切到某张卡时，把清单里对应那一行的「行列号」**预填**进工具栏
  定位框（`Toolbar.vue` 一个 watch，键 = `activeRec.sceneDir ?? lqPath`，与面板那条自动选中同源）
  —— 只填**不跳**；这一行没有行列号就清空。本体与产物卡同一个 `sceneDir`，所以同景各卡预填同一个
  坐标。没导入清单时不动框。
- **坐标口径 = (X=列, Y=行)，与原文同序**（2026-09-28 真机实测订正，**此前按「行,列」理解是错的**）：
  清单那对数的**第一个数是列(X)**，第二个才是行(Y)（名字叫「行列号」，写出来却是 X,Y 序）。所以
  面板显示、点行跳转（`locatePixel(it.col, it.row)`）、工具条预填（`col,row`）三处都**照抄原文顺序**，
  谁也别自己翻。判定办法只有真机落点：把原文那串填进定位框点「定位」，看红叉落哪 —— 别照注释想
  当然（这条注释写反过，被下游抄了三处，见 [timeline-archive.md](timeline-archive.md) 09-28 条）。
- **真机单景均耗时 = ___ 秒**（待填；含一次 resolve + 两次生成预览读盘，÷4 的 4 万² 景按
  §3.2 / preview-bake-pipeline 的量级预估是几十秒）。几十景一批就是十几分钟起 ——
  按钮的提示语里已经写明，不等同于「卡住了」。

### 6.3 测试基线

**2026-09-28 全套重跑（全部绿）**：

- 后端：**735 passed / 5 skipped / 130 subtests**（`python -m pytest backend/`，21.7s；09-24 为
  720 —— 本轮「一键解析」相关的路径/命名/候选清单用例净增 15 条）。
- 前端：**434 passed** + `vue-tsc --noEmit` 零错误 + `npm run build`（Vitest，17 个文件；09-24
  为 393 —— 新增 `qcbatch.test.ts` 29 条纯逻辑，`queue.test.ts` 40 条含本轮新增的
  `pagehide`/`pageshow` 收流用例；09-28 场景库快照新增 `scene.test.ts` 快照组装/解析 10 条）。
- 浏览器回归（`.e2e/`，先 `cd frontend && npm run build`）：
  `test-scenes.js` **136** · `test-vue-viewer.js` **190** · `test-manual-scene.js` **296**
  （09-24 为 214 —— 新增 M 段「一键解析」整段：按序入列 / 两卡同序号 / 懒卡不带像素 /
  点亮联动 / 失败账 / 中途停止）· `test-platform.js` **26**。
- ⚠️ **有一族脚本当前跑不起来，不是回归**：`.e2e/` 下 **14 个**脚本把仓库路径写死成搬家前的
  `D:/BaiduNetdiskDownload/sr_agent_platform/…`（判据：`grep -l "BaiduNetdiskDownload/sr_agent_platform" .e2e/*.js`
  命中 14 个，含 `test-regress.js`、`test-locator.js`、`test-norm.js`、`test-u16.js`、
  `test-stretch*.js` 与 `debug-*`/`probe-fd*` 那批探针）。**活着的四套就是上面列的这四个**
  （都按 `__dirname` 定位，搬家不影响）。历史经验里若引用了那一族当金丝雀，按这条订正。
- 更旧的值（58 / 65 / 22 / 197 / 720 / 393 / 214 等）已过期，不要据此判断回归。

## 7. 去哪查什么

| 查什么 | 去哪 |
|---|---|
| 某个决定 / 某次事故的经过 | [timeline-archive.md](timeline-archive.md) |
| 真机验收怎么勾 | [real-machine-acceptance.md](real-machine-acceptance.md) |
| 真机怎么部署、起不来怎么办 | [real-machine-bringup.md](real-machine-bringup.md) · [ADHD 动作版](real-machine-bringup-adhd.md) · [deploy/README.md](../../deploy/README.md) |
| 踩坑与硬约束 | [gui-experience.md](../experience/gui-experience.md) |
| 平台 API 契约 | [api-contract.md](../planning/api-contract.md) |
| 预览生成预览端到端链路 | [preview-bake-pipeline.md](../knowledge/preview-bake-pipeline.md) |
| SR 算法 / 调用契约 / 移植环境 | [docs/sr_code/](../sr_code/) |
| 生产场景命名与路径反推 | [production-scene-naming.md](../sr_code/production-scene-naming.md) |
| 系统理解前后端设计取舍 | [platform-tutorial.md](../knowledge/platform-tutorial.md) |
| 文档分类规则 | [docs/README.md](../README.md) |
| 环境与真机事实（跨会话记忆） | `~/.claude/projects/…/memory/MEMORY.md` |
