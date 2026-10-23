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
  左侧列表顶部有一排图标键：多选、收藏选中（置顶且活过批量清理）、移除选中、清理冗余、
  清理全部、撤回（见 §6.5）。
  右侧「上下文侧舱」含 `[ROI/工具]` 与 `[Agent]` 两个页签。
- **`/scenes` 场景库** —— 盘阵场景检索（卫星 / 传感器 / 日期 / 关键词）、行内显示 W/H、
  打开（首次访问懒生成预览）、清除预览缓存（选定 / 全部）。
- **`/queue` 共享队列** —— 提交 SR、状态随 SSE 推进、取消作业、耗时与产物目录。
  ⚠️ 这条 SSE 是 `fetch` + `ReadableStream` 拼的**文档级长连接**，所以
  `stores/queue.ts` 在 `window` 上挂了 `pagehide`/`pageshow`：**整页导航时 Vue 的
  `onUnmounted` 不跑**，不在 `pagehide` 里显式收流的话，每导航一次就漏一条连接，占满
  Chrome 每源 6 条之后页面会卡在「加载中…」而**后端日志全干净**（2026-09-28 的事故，
  机制见 [gui-experience §9.9](../experience/gui-experience.md)）。改这个 store 时别把
  那两个监听摘掉。
- **`/chat` 对话** —— agent 循环 + 工具调用（`search_scenes` / `run_sr` / `sr_job_status` /
  `fix_bad_lines` / 掩码栅格化），SSE 推送 `tool_call` / `tool_result` / `assistant`。
  **流式输出**（2026-10-21）：缺省开启，模型正文逐 token 上屏、Qwen 推理走可折叠的
  「思考过程」块；`llm_phase` / `reasoning` / `token` 为独立事件，推理**不落库**。
  服务端 `SR_LLM_STREAM=0` 是总开关，右上角设置浮层可按浏览器再关（下一回合生效）。
  支持**截图附件**（2026-10-20）：左侧影像卡片右上角的相机键或输入区相机键，把查看器当前
  可见画面（所见即所得）带进对话，每条最多 2 张；图片作为 `image_url` 内容块真正交给多模态
  模型，契约见 [api-contract](../planning/api-contract.md) §3.2.1，取舍与边界见
  [gui-experience §12](../experience/gui-experience.md)。

掩码产物：浏览器直接生成 `掩码.tif`（值域 0/255）+ `掩膜中心点坐标.txt`，
也可导出矢量 JSON 交后端栅格化（[mask.py](../../backend/services/mask.py)）。

## 2. 当前路线

- **SR 提交 = 本机 conda 直跑**（2026-09-14 定，`SR_EXECUTOR=local`）：后端在 node81-135 上用 SR 生产
  conda 解释器直接起 bash 子进程，读锁定目录里已有的 `.tif` 与 `<目录名>_mask.tif`，产物写回同目录。
  配置 XML / 审计段 / 契约校验器 / 退出码文件整套原样复用。整条链与运行期规则见
  [sr-local-execution.md](sr-local-execution.md)，工作单见
  [sr-minimal-prototype-plan.md](../planning/sr-minimal-prototype-plan.md)。
- **作业终态读退出码文件**，不读调度器账务（真机 `AccountingStorageType=none`，`sacct` 永久不可用）。
  退出码 0 不等于成功；契约不满足时校验器以退出码 90 结束。
- **调度器路线冻结**（2026-09-14 中止）：单卡用 `CUDA_VISIBLE_DEVICES` 绑定即可，无需调度。
  `backend/services/slurm.py`、`SR_SLURM_*` 环境变量与 `SR_EXECUTOR=slurm` 分支保留为存量，
  上线路线不读。⚠️ 仓库默认值 `SR_DEFAULT_EXECUTOR` 仍为 `"slurm"`
  （[config.py](../../backend/config.py)），真机靠 env 覆盖为 `local`。
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
   经 09-18、09-20 两轮改动，**未复核该现象是否仍存在** —— 下次真机提交时顺带确认。
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

- **预览预览生成**：规则签名 `srprev:v3:div<N>+equal:q<Q>`；前端档位 各边 ÷2 · ÷4 · ÷8 · ÷16 · ÷32，
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
- **队列耗时口径**：纯算力时长（`started_at` → `finished_at`），不含排队；重启后基准回落库中状态。
- **任务表唯一键**：`sr_tasks.task_fingerprint`（参数内容 sha256），同参数重交复用同一行。

### 6.4 一键解析（《待修复清单》批量预览生成，2026-09-27）

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
- **先排满占位卡、再原地补齐**（2026-10-21）：点击的同步前缀里按清单行一次插入「排队中」
  的占位卡（每行一张本体，名字取行名，`pending` 标记），解析成功后 `fillPendingCard` 原地
  补齐身份并改用权威名，NOSR 卡 `insertSceneCard(..., afterId)` 插在本体后面；解析失败
  原地标红、原因进 `pendingError`，停止时没轮到的写「未开始」（`finishPendingCards`）。
  占位卡在 `activate` / `startRecDrag` / `removeRec` 三个入口挡掉，卡片不渲染删除与截图键。
  入场延迟由 `lib/motion.ts::cascadeDelay(i, n)` 计算：前 6 张按 300ms 逐条固定累计
  （5 × 300ms + 120ms 淡入 ≤ 1800ms 窗口）；第 7 张起返回 `undefined` —— 不做入场动画、
  直接显示，**不压缩前面的步长**。
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
  坐标。顺序照旧是「列,行」（同 `QcListPanel.onRow`）。没导入清单时不动框。
- **真机单景均耗时 = ___ 秒**（待填；含一次 resolve + 两次预览生成读盘，÷4 的 4 万² 景按
  §3.2 / preview-bake-pipeline 的量级预估是几十秒）。几十景一批就是十几分钟起 ——
  按钮的提示语里已经写明，不等同于「卡住了」。

### 6.5 左侧列表工具条（2026-10-21）

- **一颗内容宽的白色胶囊**（`FileListTools.vue`），里面六颗 26px 正圆图标键：多选 · 收藏选中 ·
  移除选中 · 清理冗余 · 清理全部 · 撤回。可见文字为零，功能说明全在 `title` / `aria-label`；
  跑批期间整排禁用，列表为空时全灰（多选键也不例外）。胶囊吸顶，外层铺 chrome 底色挡滚动卡片。
- **图标 16px**：黑色 lucide 素材，源文件在 `frontend/icon/`，复制到 `frontend/public/icon/`
  才能被 `AppIcon` 取到；`<img>` 里的 `currentColor` 取不到按钮的 `color`，状态只靠圆底与描边。
- **界面叫「清理冗余」不叫「清理失败」**（用户 2026-10-21 定）；它清 `statusCls === 'err'`
  的卡，含一键解析失败后留下的红占位卡。收藏的失败卡保留。
- **收藏置顶且活过三处清理**：`clearRecs` / `removeRedundant` / `removeAll` 都跳过收藏；
  收藏只在渲染时排序（`recsOrdered`），取消收藏回原位。序号 Map 仍按 `sceneDir` 粘着发号。
- **多选点卡 = 勾选不激活**（避免为收藏触发 ≈800MB 的取图）；批量清理走 `dropRecs`，
  不逐张 `removeRec`（那会反复激活取图），也不自动打开幸存卡。
- **撤回只撤列表结构**（成员/顺序/收藏/活动侧/视图/序号），深度 10，一键解析开跑时清空；
  快照共享 rec 对象，不重新解码。键盘：Esc 退出多选、Delete 移除选中、Ctrl/⌘+Z 撤回。
- 设计取舍与踩坑见 [gui-experience.md §13](../experience/gui-experience.md)。

### 6.3 测试基线

**2026-10-20 全套重跑（全部绿）**：

- 后端：**797 passed / 5 skipped**（`python -m pytest backend/`，115.2s；同日上午加聊天截图
  前为 767 / 5 —— 差额（+30）是 `test_chat_images.py` 20 条与 `test_api_platform.py` 的
  10 条图片用例）。
- 前端：**465 passed**（Vitest，24 个文件）+ `vue-tsc --noEmit` 零错误 + `npm run build` 通过；
  截图功能新增 `chatShot.test.ts` 8 条、`chatImages.test.ts` 9 条、`chatImageUrl.test.ts` 4 条。
- 浏览器回归（`.e2e/`，先 `cd frontend && npm run build`）：
  `test-scenes.js` **132** · `test-vue-viewer.js` **190** · `test-manual-scene.js` **296**
  （09-24 为 214 —— 新增 M 段「一键解析」整段：按序入列 / 两卡同序号 / 懒卡不带像素 /
  点亮联动 / 失败账 / 中途停止）· `test-platform.js` **34**（2026-10-20 新增 E 段「聊天截图」
  8 条：相机键 → 托盘上限/删除 → 页内导航保留 → 发送 → 刷新后走会话图片端点）。
- ⚠️ **有一族脚本当前跑不起来，不是回归**：`.e2e/` 下 **14 个**脚本把仓库路径写死成搬家前的
  `D:/BaiduNetdiskDownload/sr_agent_platform/…`（判据：`grep -l "BaiduNetdiskDownload/sr_agent_platform" .e2e/*.js`
  命中 14 个，含 `test-regress.js`、`test-locator.js`、`test-norm.js`、`test-u16.js`、
  `test-stretch*.js` 与 `debug-*`/`probe-fd*` 那批探针）。**活着的四套就是上面列的这四个**
  （都按 `__dirname` 定位，搬家不影响）。历史经验里若引用了那一族当金丝雀，按这条订正。
- 更旧的值（58 / 65 / 22 / 197 / 720 / 393 / 214 等）已过期，不要据此判断回归。

**2026-10-21（动效令牌 + 一键解析占位卡）**：前端 **470 passed**（25 个文件；新增
`motion.test.ts` 3 条、`qcbatch.test.ts` 的 `onFail` 2 条）+ `vue-tsc --noEmit` 零错误 +
`npm run build` 通过 + `check:terms` 通过（101 个文件）。`test-manual-scene.js` 的 M 段已按
占位卡口径改写，整轮 **299 项断言全绿** —— 但这是把 §A 末尾两条 1366 工具栏宽度断言
**临时跳过**之后跑到的：当前工作区与 HEAD 都稳定报 `scrollW=1399 / clientW=1366`、
溢出控件是 `set-btn`（暂未处理，与本轮改动无关）。

**2026-10-21（一键解析入场：前 6 张 300ms，之后省动画）**：`CARD_CASCADE_MS` 600 → 1800，
新增 `CARD_STEP_MS = 300`，`cascadeDelay` 由 `t²` 改为线性 —— 前 6 张每张比上一张固定晚
300ms（5 × 300ms + 120ms 淡入 = 1620ms ≤ 1800ms）；第 7 张起返回 `undefined`，不做入场
动画、直接显示。**不按份数摊薄步长**（一度试过 `1680ms/(n-1)` 的等距压缩，判定不对：
批次大时前几张的节奏会被拖模糊，不如省掉尾巴）。`motion.test.ts` 原「先快后慢」用例换成
前缀 + 尾部断言，该文件共 7 项 → 前端 **482 passed**（含流式新增用例）+
`vue-tsc --noEmit` 零错误 + `npm run build` 通过。

**2026-10-21（Agent 附文默认不上屏）**：`AgentChatTab.vue` 的自动附文改为**仅开发者模式**
渲染 —— 气泡下一块浅米色（`--warn-bg` / `--warn-line`）`<details>` 折叠面板，默认收起，
普通模式完全不渲染；`ChatPage.vue` 保持只显示原话。发送给 Agent 的 payload 形状不变，但
`buildContextNote` **不再带 `scene_id`**（长串盘阵路径对 Agent 无用，认场景靠工具）——
图像名 / 原图尺寸 / 显示尺寸 / 拉伸 / ROI 统计照旧。`agentContext.test.ts` 把原「含
scene=s-123」改成断言不含 `scene`。前端仍 **472 passed** + `vue-tsc --noEmit` 零错误 +
`npm run build` 通过。

**2026-10-21（工具条胶囊 + 一键解析入场曲线）**：列表工具条收进内容宽的白色胶囊（三层阴影，
按钮 26px 正圆）；一键解析改为从右侧 16px 滑入，逐卡延迟由均分改为按 `t²` 分配（整批先快后慢，
窗口 600ms）；`motion.test.ts` 新增 2 条（相邻间隔随时间拉开、`CARD_ENTER_MS` 等于
style.css 的 `--motion-fade`）→ 前端 **472 passed** + `vue-tsc --noEmit` 零错误 +
`npm run build` 通过 + `check:terms` 通过。

**2026-10-21（左侧列表工具条）**：前端仍 **470 passed** + `vue-tsc --noEmit` 零错误 +
`npm run build` 通过。`test-manual-scene.js` 新增 M9（工具条 6 键 / 多选不激活 / 收藏置顶 /
Ctrl+Z 撤回收藏 / 清理全部只留收藏 / clearRecs 保留收藏 / 取消收藏后才清得掉），整轮
**322 项断言全绿** —— 同样是在临时跳过 §A 那两条 1366 宽度断言之后跑到的（同一条
`set-btn` 溢出，与本轮无关；E4 偶发一次时序失败，重跑即绿）。

**2026-10-21（聊天流式输出）**：后端 **806 passed / 5 skipped**；前端 **482 passed**（26 个文件，
新增 `streaming.test.ts` 3 条与 `chat.test.ts` 5 条流式归并用例）+ `vue-tsc --noEmit` 零错误 +
`npm run build` 通过。浏览器回归：`test-platform.js` **36 项全绿**（新增 2 条流式断言 ——
推理折叠块 ×2、刷新后思考内容不恢复；其余三套本轮未重跑）。接口与事件表见
[api-contract §3.2 / §4.2](../planning/api-contract.md)，取舍见
[gui-experience §14](../experience/gui-experience.md)。**真机（内网 Qwen 端点）的
流式观感与 `SR_LLM_STREAM=0` 退路仍待验收。**

**2026-10-23（右侧 Agent 面板补会话操作）**：`AgentChatTab.vue` 头部去掉「会话 xxxxxxxx」
（对操作没有帮助，当前会话在历史列表里高亮即可辨认），换成**三个 16px 图标键**
（`circle-plus` / `clock` / `circle-x`，素材在 `frontend/icon/`，已复制到 `public/icon/` 随
构建发布）：

- **新建**（circle-plus）—— 直接 `chat.newSession()`，原会话保留在历史里；
- **历史**（clock）—— 下拉菜单列出全部会话（置顶标、更新时间；当前会话高亮），点一行即
  `chat.selectSession()` 切换；
- **删除当前**（circle-x）—— `window.confirm` 二次确认，文案与 /chat 行内删除同一口径
  （消息/用量/轨迹一并删除）；删完按 store 规则切到列表第一条，空了就新建。

发送中三个键全禁用（与 /chat 一致）。与 /chat 共用同一个 chat store，两边的当前会话、
列表、删除结果实时一致。仍然**不 init**：进查看器不自动挑会话、首问才 `apiCreateSession`；
历史列表只在**打开菜单时** `refreshSessions()`（查看器不为一块常驻面板每次进页多打一趟
`/api/chat/sessions`）。没有当前会话时历史照常可切，删除键置灰。三个键只在 Agent 可用
（当前图有 `sceneId`）时出现 —— 本地 / 离线图上面建会话没有落点。`ChatPage` 未改动。

验证：`vue-tsc --noEmit` 零错误、前端 **482 passed**、`npm run build` 通过、`check:terms` 通过；
另用假后端（本地 http + `window.__SR_CFG__` 注入）跑了一轮临时探针：头部不再出现会话 id、
历史菜单两行（置顶在前、当前高亮）、切会话后消息区换成该会话历史、新建后消息区清空并在
菜单里高亮、删除当前会话发出 DELETE 并自动切到列表第一条。

**2026-10-23（查看器底部状态栏极简化）**：`StatusBar.vue` 由原来那条罗列文件名 /
W×H / probe（bits/spp/sf/ph/comp）/ 路由 / 预览尺寸 / layout 的元信息条，改成 **26px 单行三段**：
左＝操作状态，中＝缩放比例，右＝光标像素坐标。被删的字段在左侧卡片与右侧侧舱各有出处，
这条只回答「图现在怎么样、我看到的是哪一像素」。口径：

- **缩放 100% = 原图 1 像素对屏幕 1 像素**。`view.scale` 是「屏幕 / 预览图像素」，
  状态栏乘 `预览宽 ÷ 原图宽` 换算回原图基准；不足 10% 保留一位小数（fit 到全景时不会显示 0%）。
- **像素坐标是原图全分辨率**，与工具栏定位、掩码文件同一套；指针不在这张图上（画布留白 /
  分屏的另一侧 / 移出窗口）显示「—」。坐标由 `TifCanvas` 的 window mousemove 写进 store 新的
  `cursor`/`setCursor`（同像素不重复赋值），切图 / 切活动侧 / 进出分屏、鼠标离开画布、卸载时清空。
- 左段优先级：遮罩标题（取图 / 解码 / 合并 / 写掩码，带进度）＞ 绘制态（工具名，多边形附已点数）＞
  rec 自身状态（`statusCls === 'ok'` 收敛成「已就绪」，完整耗时留在 `title` 里）。
- 有空卡（排队 / 待打开，无像素）时照常显示，缩放与像素为「—」；**只有列表里一张活动图都没有时才整条不渲染**。
- 分屏时状态段前加 `[左]/[右]`；缩放与像素**只报活动侧**，指针挪到另一格不改口径
  （先点那半切活动侧）。

e2e 同步：`check-frontend-build.js` 原来等 `.rec-bar` 出现 `256×128`，改为等「已就绪」；
顺带修了它早已失效的「四个导航项」断言（现为五个，改成按**字号**断言，个数只要求 ≥4）。
`.e2e/vue-lib.js` 的 `launchBrowser` 改为委托 `launchBrowser.js`（Chrome 优先 + 独立 profile）：
原先写死 Edge 路径，本机复现 `Code: 0` 起不来，`qa-theme` / `shot-theme` 整条空转。
本轮前端 **482 passed** + `vue-tsc --noEmit` 零错误 + `npm run build` 通过 + `check:terms` 通过；
浏览器回归 `test-vue-viewer` **190 项全绿**、`check-frontend-build` **全绿**。
`qa-theme` 已能启动，但整套主题断言还停在旧配色（藏青导航、白色工具栏等），**20/29 通过**；
其中「状态栏白底」现在拿到 `null`，是因为它不传图、按新口径整条不渲染 —— 属于预期，不是回归。

**2026-10-23（查看器移除「盘阵场景」栏）**：`ViewerPage` 去掉横贯整页的 `ScenePathBar`，
打开盘阵场景统一走「场景库」页，或把本地 tif/jpg 拖进画布按文件名反推关联。只服务于这条栏的
`viewer.openScenePath` 与 `window.__viewer.openScenePath` 钩子一并删除；`Toolbar` 与 viewer
store 里「用上面的『盘阵场景』栏打开」「请在『盘阵场景』栏粘贴…」等文案改指「场景库」。
顺带补了路径栏的一处忙态缺口：`scenes.resolvePath` 现在一进门就占 `openingId` 哨兵
（`'__pathbar__'`），resolve 往返期间按钮即禁用 —— 原来要等 `open(row)` 才置位，连点会发
两趟请求，`test-scenes` 的路径栏收尾判据也会把「刚点下去」误读成「已收尾」。
前端 **482 passed** + `vue-tsc --noEmit` 零错误 + `npm run build` 通过 + `check:terms` 通过
（107 个文件）；浏览器回归 `test-vue-viewer` **190**、`test-scenes` **132** 全绿。
`test-manual-scene` 仍卡在既有的 1366 工具栏溢出（`scrollW=1399`，溢出控件 `set-btn`）；
临时跳过那两条后跑到 M 段，止于橙描边颜色的既有 0.992 时序抖动（重跑会换到另一条时序失败，
与本轮改动无关）。

## 7. 去哪查什么

| 查什么 | 去哪 |
|---|---|
| 某个决定 / 某次事故的经过 | [timeline-archive.md](timeline-archive.md) |
| 真机验收怎么勾 | [real-machine-acceptance.md](real-machine-acceptance.md) |
| 真机怎么部署、起不来怎么办 | [real-machine-bringup.md](real-machine-bringup.md) · [ADHD 动作版](real-machine-bringup-adhd.md) · [deploy/README.md](../../deploy/README.md) |
| 踩坑与硬约束 | [gui-experience.md](../experience/gui-experience.md) |
| 平台 API 契约 | [api-contract.md](../planning/api-contract.md) |
| 预览预览生成端到端链路 | [preview-bake-pipeline.md](../knowledge/preview-bake-pipeline.md) |
| SR 算法 / 调用契约 / 移植环境 | [docs/sr_code/](../sr_code/) |
| 生产场景命名与路径反推 | [production-scene-naming.md](../sr_code/production-scene-naming.md) |
| 系统理解前后端设计取舍 | [platform-tutorial.md](../knowledge/platform-tutorial.md) |
| 文档分类规则 | [docs/README.md](../README.md) |
| 环境与真机事实（跨会话记忆） | `~/.claude/projects/…/memory/MEMORY.md` |
