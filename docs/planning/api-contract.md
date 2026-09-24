# 平台 API 契约（阶段5 · REST / SSE）

> 日期：2026-09-02 · 状态：**评审**（2026-09-16 按 §7 红线置回，待复核后改回「已定」）
>
> **挂起项一**：§3.3 新增「`suffix` 默认值来源」条款——省略/留空不再固定为内置 `"sr"`，改为读 `$SR_BUNDLE_DIR` 下 SR 团队配置文件里的 `<Suffix>`，`SR_SUFFIX_DEFAULT` 环境变量作废；同批把 agent 工具 `run_sr` 的归一化与 REST 入口对齐（此前工具既不 strip 也不给默认值，同一逻辑提交两入口指纹不同 → 幂等失效、重复投作业，属修缺陷）。
> **挂起项二（2026-09-17）**：新增 §3.5 `POST /api/scenes/resolve`（打开盘阵上 `SR_SCENES_ROOT` 之外的任意合法场景目录），并改 §3.4 `POST /api/masks` 的 body（新增 `lq_path`，legacy `scene_id` 保留）。同时约定 `lq_path` / `dir` / `input` / `mask_path` 一律回**盘阵 POSIX 形态**、提交侧两入口共用 `pathguard.normalize_submit_path`——这两条不改行为口径，只是把"同一场景两种写法算出两个指纹"的隐患收口。
> **挂起项三（2026-09-20）**：① 新增 §3.8 `GET /api/scenes/{id}/siblings`（一个场景的三类图：输入影像 / 本轮超分产物 / NOSR），纯只读、永不烘焙；② 新增 §4.5 **产物预览急烤队列**（作业转 COMPLETED 后服务端顺手烤产物那一份，从库派生而非挂在状态转换上），`/api/queue` 每行随之多出 `preview_state` / `preview_note` 两列与新的 SSE 帧 `preview_update`（§3.3）；③ 新增 §4.6 **显示源比较规则**：拖入/打开的 `.jpg` 显示件在同目录有位更清晰的栅格、且当前档位下服务端从它烤出来的比它更清晰时，显示源换成服务端那份（`/preview` 与 `/preview-drop` 各插一次同名栅格探测，落点与 `?div=` 全部照旧），jpg 行上因此多出只读的 `rasterPreview` 字段（§3.5）。**`hasPreview` / `jpgUrl` / `previewDiv` 三个字段的语义一个字未动**。同时记两个 env（§1）。
> **挂起项四（2026-09-20 第二轮）**：后端**零改动、零新增端点**。这一轮只把前端的取图纪律写进契约：① 新增 §3.8.2 —— 预览 blob 的**本地缓存键**（`{id}|{div}|jpg`，源是显示件且同名栅格胜出时另一支用 `{id}|{div}|ras:{栅格名}`，两支不能串味）与**预取边界**（只取 `/siblings` 里 `exists && hasPreview && previewDiv === div` 且没有已开 rec 的那几项，**绝不触发服务端烘焙**；开关默认关、持久化在 `sr.viewer.cmpPrefetch`）；② 同一节记下 `openSceneSibling` 的**去重**口径（命中已开的 rec → 一次 `/siblings` + 零次 `/preview`）。`/preview` 与 `/siblings` 的**请求与响应一字未改**。
> **挂起项五（2026-09-21）**：① §3.5 `POST /api/scenes/resolve` 的 `{name}` 分支**也认中间产物**（`<目录名>_<suffix>.jpg` / `<目录名>_<suffix>_NOSR.jpg`）——新增 jpg 专属的第二阶段候选（去尾段反推，仅前一阶段全落空时展开），`resolved` 新增 `kind` / `suffix`，且 `kind != 'input'` 时 `row.lq_path = null`、`sr_capable = false`、`mask_path = null`（`row` 同时改为描述**该环节自己**那份栅格）；② `suffix` 的 400 文案改为实话（可拖的不止显示件）；③ 新增 `backend/pathguard.scene_name_layers` 的段数下界修正（六段名走进 `seps[_SCENE_IDX]` 越界 → 本该 400 的输入变 500，是这条新候选暴露出的既有缺陷）。**`/siblings` 一个字段都没加** —— 计划里提过给每项补 `suffix`，落地时发现响应顶层本来就有 `suffix` / `suffixFrom`，前端 `openSceneSibling` 用的就是它，再加一份是重复。
> **挂起项六（2026-09-21 第二轮，真机反馈）**：§3.5 `{name}` 分支**再认一种名字** —— 平台自己烤的那份预览 `<栅格 stem>_preview.jpg`（拖入链写进场景目录的，`preview-drop` 的产物）。`preview` 一直在 `scene_search._NON_STAGE_TAILS` 里，于是「平台写下的文件、平台自己不认」，真机上拖它回来得到的是一屏「目录不存在 + 两条自己拼出来的假路径」（`…_preview/…_preview`）。现在 `preview` 是那份名单里**唯一可以剥**的尾巴（`scene_search.strip_preview_tail`，`de_suffixed_stems` 与 `stage_of_jpg` 各剥一次），剥一层为止：`<目录名>_preview` → 本体、`<目录名>_sr_preview` → 那份产物；剥完仍在名单里（`<目录名>_cloud_preview`）照旧 404。另四个尾段（cloud/thumb/mask/ori）剥不得 —— 它们剥掉会正好落到真实场景目录上。前端只改一句失败弹窗的文案。
> **挂起项七（2026-09-21 第三轮，真机反馈）**：§3.5 `{name}` 分支新增**可选字段 `anchor`** —— 拖拽入口把「用户当前打开着的场景目录」一并发过来（前端 `sceneAnchors`：最近显示过的那一景 → A 格 → B 格，上限 3 条），后端按序在这些目录里认这份 jpg（判据与 `scan` 段完全相同，只是不过 `_fingerprint_mismatch`，真门仍是「这一环节自己的栅格躺在同级」）。**只给名字里没有场景身份的那一类用**：RC 场景的产物叫 `PAN_<suffix>.jpg`（产物名按输入影像名拼，RC 的输入是 `PAN.tif`），既无卫星段也无成像时刻，反推那一步就 400 —— 用户真机上拖它进来正是这个现象。名字自己能反推时 anchor 连一次 stat 都不花；坏值一律跳过并把原因追加进 400 的 `detail`（只有白名单是硬的），不新增错误码、不改 `/siblings`。**这不是「平台猜目录」**：目录来自用户自己打开的上下文，不是从文件名推的。另有前端一句弹窗文案同步改写（原文说「能关联的 jpg 只有名字与场景目录名一致的那份」，对 RC 产物是假话）。
> **挂起项九（2026-09-21 第五轮 → 2026-09-22 收口，真机反馈）**：预览文件名**全平台统一成一条规则** `<源栅格 stem>_preview.jpg`（`paths.preview_jpg_name`），三条烘焙链（急烤 / 惰性打开 / 拖入）落同一个名字，差别只剩目录。09-21 那版是「点号那份不动、再 `copyfile` 一份下划线同名件」（`app.py::_mirror_preview_name`）—— 那份镜像同日被删：它把「一份栅格两个文件」从缓存层搬到了每一份产物上，用户要的是**只有一个名字**。读判据同步搬过去（`hasPreview`/`previewDiv`/静态 `jpgUrl` 从同一份落点算；前端 `isBakedPreviewUrl` 认结尾 `[_\.]preview\.jpe?g`），旧的点号文件由 `_sweep_legacy_preview` 在每条链处理到那份栅格时顺手删掉，没有全盘清扫（平台不列目录）。详见 §4.5 与 [preview-bake-pipeline §4.8/§4.9/§4.10](../knowledge/preview-bake-pipeline.md)。
> **挂起项八（2026-09-21 第四轮，真机口径）**：§4.5 的急烤队列**顺带**多烤一份未超分的预览 —— 同一轮 tick 在产物之后把 `<场景目录>/PAN_NOSR.tif` 按**全局档位**下采样成 `<场景目录>/PAN_NOSR_preview.jpg`（落点复用拖入链的 `<源 stem>_preview.jpg` 规则）。**不是新的烘焙入口、不动任何响应字段、不动产物的状态机**：源是固定名字，不判沙箱（那份栅格是盘阵上的既有文件），结局只写盘 + 一行 stdout（`[nosr-preview] task=<id> <状态>`）。名字按用户口径钉死；仓库 `SR_code/util.py::writeTiff` 推出来的 `<产物 stem>_NOSR.tif` 与它对不上，属**待核**（记在 current-question）。
> **挂起项十（2026-09-24 用户口径，订正上面的挂起项八）**：① **NOSR 那一份的名字口径订正** —— 未超分那份 = **输入影像的 stem + `_NOSR`**（SC 场景 `<目录名>_NOSR.tif`，RC 场景 `PAN_NOSR.tif`）；`SR_code/util.py::writeTiff` 推出来的 `<产物 stem>_NOSR.tif` **退为次选**，留在候选清单最后。候选由 `scene_search.nosr_candidates()` **一处**产出，`/siblings` 与急烤那条链读同一份 —— 挂起项八那版把名字**硬编码成 RC 的名字** `PAN_NOSR.tif`，SC 场景因此永远走 `skipped:`，而 `/siblings` 又只认次选那条，两处彼此对不上。② `/api/scenes/{id}/siblings` 响应**新增 `nosrCandidates`**（与 `productCandidates` 同形制、顺序即优先级），`nosr` 项**不再依赖 `suffix`**（名字由输入影像的 stem 拼，有测试钉着）。③ §3.5 `{name}` 分支**再认两种名字**：裸 `<目录名>_NOSR.jpg` 判 `('nosr','')`；`de_suffixed_stems` 的 `_NOSR` 尾段改在切段循环**内**剥（原先在循环外预剥，`<目录名>_NOSR` 的**真名**反而一条候选都进不去）。④ 前端在**拖入显示件**时对同景那一份多做一次静默预热（`stores/viewer.ts::warmNosrPreview`，命中才记账）—— 拖入不再只烤「自己那份」。详见 [preview-bake-pipeline §4.11](../knowledge/preview-bake-pipeline.md)。
> **须说明的流程偏差**：上述改动**已与本文档同批落到代码**（不是"先评审后写码"）。理由是它同时修一个现存缺陷（两入口指纹不一致），拆开会让仓库停在一个已知会重复投作业的中间态；09-17、09-20 两批同理，前端要用的字段与端点不一起落地就没法验收（09-20 那批还带着 §4.5 那个后台循环，文档与循环必须同批，否则运维会照着一份没写急烤的契约去配 env）。请复核，通过后把状态改回「已定」。此前其余条款自 2026-09-02 起均未变（评审通过时的交付基线：后端 190 unittest + 前端 Vitest 114 + vue-tsc 零错误 + `.e2e/test-platform.js` 11 断言全绿）。
> 目标读者：阶段5 实现会话（后端 FastAPI + 前端 Vue3）。范围：把既有后端（agent loop + 4 工具 + `sr_tasks` + slurm）暴露成网页可调 REST/SSE，交付 聊天 / 共享任务队列 / 查看器画完掩码提交 SR。
> 前置：阶段4 已完成（FastAPI 骨架 `backend/api/app.py`：`/api/scenes` + `/api/scenes/{id}/preview` + 路径白名单；前端 `/scenes` 页 + route='jpg' rec + `/chat` `/queue` 占位路由）。

## 0. 一句话总结

后端 FastAPI 新增四组端点——**聊天**（会话 REST + 单回合 SSE）、**工具直调**、**共享任务队列**（REST 提交/取消 + SSE 状态广播）、**掩码落盘**；前端三个页面（聊天/队列/查看器掩码提交按钮）。离机可全测：mock LLM（`SR_LLM_MOCK=1`）+ 假调度器（`SR_SLURM_FAKE=1`）+ fake 场景回退，真模型/真 Slurm 项另排。

## 1. 通用约定

- **Base**：所有端点挂在既有 FastAPI app 上（`backend/api/app.py::create_app`，前缀 `/api`）。CORS 全放（内网直连 IP:端口；端口转发只改前端 `__SR_CFG__.apiBase`，同阶段4）。
- **鉴权**：内网无鉴权；`run_sr`/掩码的副作用由**幂等表 + 路径白名单**兜底，不做用户门控（M2 再议）。
- **请求/响应**：JSON（UTF-8）。错误统一 `{"detail": "<人话>"}` + HTTP 状态码；工具/命令层结果遵循 `{"ok": bool, "data": …, "error": str|null}`（复用 tools.contract 约定）。
- **错误码表**：

| 码 | 含义 |
|---|---|
| 200 | OK（GET 列表 / SSE 正常结束） |
| 201 | 创建成功（会话 / 队列任务） |
| 400 | 参数非法（缺字段、类型错、路径非绝对/含 `<fake>` 等） |
| 404 | 资源不存在（会话 / 队列任务 / 场景不可访问） |
| 409 | 会话正忙（同一会话已有回合在跑，串行拒绝）；或重复提交被幂等层判定"上次中断、勿盲重试" |
| 422 | 生成/提交失败（预览生成失败 / sbatch 失败 / slurm 不可用） |

- **SSE 帧**：每事件一行 `data: {json}\n\n`（UTF-8、`Cache-Control: no-cache`、`Content-Type: text/event-stream`、`X-Accel-Buffering: no`）。事件不含业务 id 后缀（MVP 无多路），前端按帧 JSON 的 `type` 分派。**所有事件共用首字段 `type`**，可向后扩展。
- **运行模式 env**（延续 config.py env 风格，进程启动即读）：

| env | 缺省 | 作用 |
|---|---|---|
| `SR_AGENT_DB` | `sr_agent.db` | SQLite 路径（chat 会话 + sr_tasks 同一库） |
| `SR_LLM_MOCK` | 未设 | `=1` 时聊天走假 LLM（§5.1），不连任何端点 |
| `SR_SLURM_FAKE` | 未设 | `=1` 时提交走内存假调度器（§5.2），无 sbatch 也能跑通 submit→status→cancel |
| `SR_SCENES_ROOT` | 未设 | 盘阵场景根；未设 → fake 回退（阶段4 已有） |
| `SR_PRODUCT_PREVIEW_DIV` | `4` | **产物预览急烤**用哪一档（见 §4.5）；取值不在 `PREVIEW_DIVISORS = (2,4,8,16,32)` 里 → 当 `0` 处理并写一行启动日志，**不抛异常**；`0` = 关掉急烤（只剩惰性路径）。与前端 `DEFAULT_PREVIEW_DIV`（同为 4）**互不联动**——浏览器档位存在 localStorage，服务端看不见，这正是必须有服务端默认档的原因 |
| `SR_PRODUCT_PREVIEW_MAX_AGE_SEC` | `86400` | 只烤 `finished_at` 落在这个窗口内的行；挡掉升级当天把历史 COMPLETED 行全烤一遍。`finished_at IS NULL` 的一律不烤 |
| `SR_LLM_BASE_URL/API_KEY/MODEL/…` | 见 config.py | 真模型时沿用既有 loop 配置 |

## 2. 端点清单总览

| 方法 路径 | 用途 | 章节 |
|---|---|---|
| `GET /api/health` | 存活 + 数据源（已有，返回 `{ok,source}`） | — |
| `GET /api/scenes` · `GET /api/scenes/{id}/preview` | 场景检索/懒生成（阶段4 已有；**2026-09-19 起接 `div` 档位参数**、行上多 `previewDiv`；**2026-09-20 起 jpg 行多只读字段 `rasterPreview`**，见 §4.6） | — |
| `POST /api/scenes/resolve` | 手填/反推一个盘阵场景目录 → 与库行同形的 `{source,row,resolved}`（2026-09-20 起 `row` 也带 `rasterPreview`） | 3.5 |
| `GET /api/scenes/{id}/preview-drop` | **拖拽入口专用**的预览 JPG（落盘阵场景目录 `<stem>_preview.jpg`；目录不可写时兜底到临时缓存并回 `X-SR-Preview-Fallback: tmp`；不进库行，URL 不可静态映射） | 3.6 |
| `GET /api/scenes/{id}/siblings` | **只读**诊断：一个场景的三类图（输入 / 本轮超分产物 / NOSR `_NOSR`）各叫什么、在不在、各是什么 id —— 供下一轮对比视图消费；NOSR 那份的候选清单（`nosrCandidates`）也从这里出 | 3.8 |
| `POST /api/scenes/clear-preview` | **删盘阵文件**（本契约里唯一一条）：场景库「清除选定 / 全部清除」按场景目录清掉预览 JPG 缓存，逐条回报；判据与边界见章节 | 3.9 |
| `GET /api/tools` | 工具清单（manifest 机械生成，供 UI/文档） | 3.1 |
| `POST /api/tools/{name}` | 直调单个工具（绕过 LLM；validate + 白名单照常） | 3.1 |
| `POST /api/chat/sessions` | 新建会话 → `201 {session_id}` | 3.2 |
| `GET /api/chat/sessions` | 会话列表（新建入口 + resume 用） | 3.2 |
| `GET /api/chat/sessions/{id}/messages` | 历史消息（刷新可恢复渲染） | 3.2 |
| `POST /api/chat/sessions/{id}/messages` | 发一条用户消息 → **SSE**（一请求一回合） | 3.2 |
| `GET /api/queue` | 队列列表（sr_tasks 全量 + 实时状态） | 3.3 |
| `POST /api/queue` | 提交 SR 作业（表单=run_sr 参数，幂等） | 3.3 |
| `POST /api/queue/{task_id}/cancel` | scancel 取消 | 3.3 |
| `GET /api/queue/events` | SSE：队列状态变化广播 | 3.3 |
| `POST /api/masks` | 多边形 JSON + W/H → 栅格化写盘阵（原图目录）→ `{mask_path, lq_path, task_draft}`（body 现收 `lq_path`，legacy `scene_id` 保留） | 3.4 |
| `POST /api/qclist/write` | 《待修复清单》原地写回盘阵（真机 http 下浏览器写不了盘阵文件） | 3.7 |

> **SR 最小原型（09-14）**：掩码来源改为「目录里已有的 `<输入影像名>_mask.tif`」，
> 提交时只带 `lq_path`，由后端推导并校验存在性
> （`services/scene_search.derived_mask_path`）；`POST /api/masks` 退居补充出口。
> **2026-09-17 恢复调用**：90% 的生产场景本来就没有掩码，查看器里现场画完要能直接
> 写回服务端场景目录，所以 `apiBakeMask` / `MaskBakeResult` 重新进 `lib/api.ts`
> （body 见 3.4），随后提交仍走同一条推导 —— 两条路写出去/找回来的是同一个文件名，
> 由后端 `scene_search.mask_stem` 单点保证。
> `POST /api/queue` 的响应另加两个**只读提示字段**（没有沙箱时出现，否则整个字段缺省）：
> `in_place: true` + `notice`（人话：「输出目录 = 输入目录（…）：SR 就地把结果写成
> `<输入名>_<suffix>.tif`，输入 tif 不改名也不删除；该目录里已存在同名输出时，旧输出先被改名为
> `<同名>_NOSR.tif` 再覆盖」）。
> 同一层意思也以 WARNING 写进作业日志的 audit 段（`build_batch_script`），两处都不能省——日志是
> 事后追责时唯一会去看的东西。
> 同时 `GET /api/scenes` 的扫描后缀扩到 `.jpg/.jpeg`（`scene_search._IMAGE_EXTS`）：
> 盘阵里本就是显示就绪图的 JPG 也作为场景行列出，行内 `hasPreview` 恒 `true`、
> `jpgUrl` **指向源文件本身**（前端因此跳过 `/api/scenes/{id}/preview` 懒生成），
> W/H 由 Pillow 读头得到；`GET …/preview` 对这种行直接回源字节。
> **收件规则是白名单（2026-09-16 改，取代此前逐条排除派生件的黑名单）**：
> `scene_search.is_scene_file()` 收一个文件，当且仅当两条同时成立——
> ① 所在目录是**场景目录**（内有 `<目录名>_meta.xml`，`is_scene_dir()`；SR 脚本本来就靠
> 它判 RC/SC，没有它的目录提交也跑不起来）；② 文件名 = `<目录名>.<ext>`（SC 步骤的输入）
> 或 `PAN.<ext>`（RC 步骤的输入）。于是这些一律不再入列表：SR 产物与输入备份
> （`_sr` / `_NOSR` / `_ori`）、云量图（`_cloud`）、缩略图（`_thumb`）、提交 SR 的输入掩膜
> （`_mask`）、后端自己烘焙的 `<basename>_preview.jpg` 缓存、`Debug/` 下的调试图。
> 旧规则每冒出一类派生件就得补一条：真机接上盘阵后 18 行里 16 行是脏数据。
> **代价**：没有 `<目录名>_meta.xml` 的目录整个不显示。
>
> **阶段6 增补（查看器上下文侧舱）**：`GET /api/scenes` 的 disk 行新增只读字段
> `lq_path` = 该 scene 文件**父目录**的绝对路径（= 场景目录，`run_sr` 的目录语义 lq_path；
> fake 行恒 `null`）。作用：前端把 `/api/queue` 行按 `params.lq_path` 相等 +
> `params.mask_path` 以 `<stem>_mask.tif` 结尾关联回当前 scene，做「当前场景最近任务」
> 展示。字段只读、不含文件名；viewer 任务区以外的页面不消费它。

## 3. 端点细节

### 3.1 工具

`GET /api/tools` → `200 {"tools": [{name, description, parameters, …}, …]}`（`contract.manifest()` 展平后返回）。

`POST /api/tools/{name}` — body = 工具参数 JSON 对象（键名 = JSON Schema properties 键）。校验并调 `contract` 注册的 `run`，**永远返回 `{"ok":bool,"data":…,"error":…}`**，HTTP 恒 200（ok=false 是业务结果不是传输错误），仅参数无法解析时 400。未知工具 404。直调不落会话、不走 LLM。
> run_sr / search_scenes / sr_job_status / fix_bad_lines 四者均可用本端点单测；前端"试跑"暂不提供入口（保留接口）。

### 3.2 聊天（Agent 对话，mock LLM MVP）

**会话模型**：单会话 + 「新建」按钮。会话 = `store.sessions` 一行；消息 = OpenAI wire 格式落 `store.messages`（与 `loop.run_loop` 持久化同一套，天然支持 resume）。前端刷新 → `GET messages` 恢复历史；切回旧会话 → `POST messages` 时服务端按 `resume=True` 续接（loop 既有语义：崩溃留下的未完成回合以"结果未知勿盲重试"修复）。

- `POST /api/chat/sessions` → `201 {"session_id": "…hex"}`（`store.create_session()`）。
- `GET /api/chat/sessions` → `200 {"sessions":[{session_id, created_at, updated_at, status}]}`（按 updated_at 倒序）。
- `GET /api/chat/sessions/{id}/messages` → `200 {"session_id", "status", "messages":[…]}`；消息对前端投影为：
  `{seq, role, content?, tool_calls?:[{name,arguments}] | null, tool_name?, ok?}`（role ∈ user/assistant/tool；tool 消息带 `name`/`ok`）。404 = 会话不存在。
- `POST /api/chat/sessions/{id}/messages` — body `{"content":"<用户话术>"}`，**`Content-Type: text/event-stream`，POST 即流**。行为：
  1. 会话存在且无并发回合 → 建占位（可复用 loop 的 `store.set_session_status(…,"active")`）→ 上锁；
  2. 线程池跑 `run_loop(cfg, content, store=…, session_id=…, resume=True, on_event=bridge)`，事件经 `asyncio.Queue` 转 SSE 帧逐条下发（`emit` 缝见 §4.2）；
  3. 结束：`turn_done`（带最终文本），**之后关流并释放会话锁**。LLM 调用/循环异常 → `error` 事件 + `HTTP` 结束后前端据 `type:"error"` 渲染失败态（SSE 已开不可改 HTTP 码）。
  - **并发**：同一 session 同时两个回合 → 立即 `409 {"detail":"会话正忙"}`（前端据此禁用发送按钮 + 显示"正在思考"）。
  - 会话不存在 → 404。`content` 空 → 400。
- **前端注意**：`EventSource` 只支持 GET，故用 `fetch`（method POST）读 `response.body`（ReadableStream）手工切 SSE 帧。

**SSE 事件 schema（聊天）**——每帧一行 JSON：

```json
{"type":"turn_start","run_id":"<uuid>","session_id":"<hex>"}
{"type":"tool_call","name":"search_scenes","args":{...}}     // assistant 回合声明调用工具
{"type":"tool_result","name":"search_scenes","ok":true,"data":{...}}
{"type":"assistant","content":"已检索到 N 个场景：…"}          // 纯文本中间回复（可选）
{"type":"turn_done","content":"最终回复文本","error":null}
{"type":"error","error":"<msg>"}                              // LLM 失败/循环超限，turn_done 不出现
// 预留 token 流式：{"type":"token","delta":"…"} 加入后前端按 token 渲染（MVP 不实现）
```

### 3.3 共享任务队列（仅 SR 作业 · 服务端唯一事实源）

**数据模型**：`store.sr_tasks`（阶段4 幂等表，既有列 id/fingerprint/job_id/status/params/config_xml/batch_script/log_dir/时间，2026-09-18 增 `started_at`/`finished_at` 本次运行时间窗 —— 老库打开时 `store._ensure_columns` 用 ALTER TABLE 补列、不回填）。`status` 列**语义升级**为"队列展示状态"（阶段5 后台校准器写入，见下）；幂等层不读它（只读 job_id），无回归风险。需给 store 补 `list_sr_tasks()`。

**队列状态机**（Slurm 无百分比 → 状态机即进度）：

```
submit_run_sr 返回 → 队列状态：
  SUBMITTING     刚提交（job_id 已录，尚未校准）
  └─ 校准（slurm.job_status）：
       squeue PENDING            → PENDING
       squeue RUNNING/其它 active → RUNNING
       sacct  COMPLETED          → COMPLETED
       sacct  FAILED/CANCELLED/TIMEOUT/OOM… → FAILED
       sacct 无记录              → UNKNOWN（保留，可重跑）
```

- `GET /api/queue` → `200 {"tasks":[{task_id, fingerprint, session_id, job_id, state, params:{lq_path,mask_path,sr_scale,suffix,gpu,cloud_limit,delete_ori,grid_align}, config_xml, batch_script, log_dir, created_at, updated_at, started_at, finished_at, preview_state, preview_note}, …]}`，按 created_at 倒序。`state` 取内存最近校准结果（缓存），无缓存则当场校准一次（squeue/sacct，镜像 `slurm.job_status`）。
  **`preview_state` / `preview_note`（2026-09-20 增）**：产物预览急烤的结局，见 §4.5。
  `preview_state ∈ {null, "running", "done", "skipped", "failed", "cleared"}`（null = 从没烤过；
  `cleared`（2026-09-22 增）= 场景库「清除缓存」把它人工清了，**只由那条端点写**，见 §3.9），
  `preview_note` 形如 `"<slug>: <人话>"`，slug 固定为 `sandbox` / `product_missing` /
  `unwritable` / `source_changed` / `no_suffix` / `failed`（`cleared` 的 note 是
  `cleared: <时间> 场景库人工清除缓存，下次打开会重新烘焙`，不属于上面这组 slug）。
  两列与作业状态**无关**
  （COMPLETED 也可能没烤成），客户端别把两者耦合成一个状态机。
  **两组时间戳别混**（2026-09-18 增 `started_at`/`finished_at`，见下「耗时」）：`created_at` = 这一行**第一次**提交的时刻（同一指纹重交复用同一行，不刷新）、`updated_at` = 最近一次写回，两者属**行**；`started_at` = 校准器首次观测到 RUNNING 的时刻（排队结束）、`finished_at` = 终态落库时刻，两者属**本次运行**。本次运行的起点/终点都没观测到就是 `null`（界面「—」），**不**退回 `created_at` 顶替。
- `POST /api/queue` — body（run_sr 参数，`lq_path` 必填，其余带默认）：
  `{lq_path, mask_path?, sr_scale?=2, suffix?="", gpu?=0, cloud_limit?=80, delete_ori?=false, grid_align?=true, options_yml?}`。
  直接调 `services.run_sr.submit_run_sr(params, store=default_store())`（幂等层既在：重复同参 → RESUMED_ACTIVE/COMPLETED 复用，失败才重跑，中断无 job_id → 409 报"勿盲重试"）。返回：
  `201 {"task_id", "job_id", "status":"SUBMITTED"|"RESUMED_ACTIVE"|"RESUMED_COMPLETED"|…, "state":…, "previous_state"?, "config_xml", "log_dir"}`。
  校验：`lq_path` 必填 + 绝对路径 + 不含 `<fake>`（复用 tools/run_sr `_bad_path` 语义）；mask_path 同规则；sbatch 不可用且未开 fake → 422「slurm not available … 需在盘阵机配置」。
  **`suffix` 的默认值来源（2026-09-16 改，见 §7 评审挂起）**：body 省略或留空 → 平台读 `$SR_BUNDLE_DIR` 下 SR 团队自己的配置文件（`sfsr_confgig_test_espan2_cuda1.xml`，兼容另一种拼写 `sfsr_config_…`）里的 `<Suffix>` 作默认值；文件缺失 / 坏 XML / 标签缺失或为空 / 值不合白名单 → 回落到内置 `"sr"`。**每次提交现读**，不缓存（`task_fingerprint` 把 `suffix` 文本哈希在内，缓存会让指纹变成进程启动时刻的函数 → 重启或双 worker 对同一逻辑提交得出不同指纹，正是幂等层要防的重复投作业）。显式传值仍走白名单 `^[A-Za-z0-9_-]{1,16}$`，不合法 400（`detail` 含「suffix 非法」）。**agent 工具 `run_sr` 共用同一套归一化**（`services/run_sr.py::normalize_suffix`），同一逻辑提交在两入口得到同一 `task_fingerprint`。原 `SR_SUFFIX_DEFAULT` 环境变量同日作废，设了不读。
- `POST /api/queue/{task_id}/cancel` → 查 task（404 无），`job_id` 非空则 `slurm.cancel` → `200 {"task_id","cancelled":bool,"state":…}`；无 job_id（中断遗留）→ 400。
- `GET /api/queue/events` — SSE：订阅所有任务的 `state` 变化。**驱动** = app 生命周期后台 asyncio 任务（§4.3）：周期（`SR_QUEUE_POLL_SEC`，缺省 2s）对每个 `job_id` 非空 task 调 `slurm.job_status`，状态与前值不同 → 更新内存缓存 + 写回 `sr_tasks.status` + 广播一帧。事件 schema：

```json
{"type":"job_update","task_id":3,"job_id":12345,"state":"RUNNING","prev_state":"PENDING","ok":true,"error":null,"updated_at":1789000000.12,"started_at":1789000000.12,"finished_at":null}
{"type":"job_update","task_id":3,"job_id":12345,"state":"FAILED","prev_state":"RUNNING","ok":false,"error":"exit 1","updated_at":1789000060.5,"started_at":1789000000.12,"finished_at":1789000060.5}
// 产物预览急烤（2026-09-20 增，§4.5）：**独立帧类型**，不与 job_update 混
{"type":"preview_update","task_id":3,"state":"done","note":null}
{"type":"preview_update","task_id":4,"state":"skipped","note":"product_missing: 试过 xxx_260318.tif / xxx_260318.tiff，都不存在"}
// 心跳（可选，防代理断链）：{"type":"ping"}
```

`preview_update`（2026-09-20 增）与 `job_update` **分开是刻意的**：预览烤没烤成与作业状态
无关（COMPLETED 的作业也可能因沙箱 / 产物缺失 / 目录不可写而没烤），合成一个事件就得同时
处理两套字段、调用点也得判"这一帧到底带没带 state"。`state` 取 `sr_tasks.preview_state`
的值（`running` 只在重新 GET 时才可能看到），`note` 与 `GET` 的 `preview_note` 同源。
前端按 `task_id` 归并；**无匹配 task 就不动**（急烤的认领与广播都在后端，前端可能还没把
这个任务拉进列表，权威始终在 `GET /api/queue`）。

`updated_at`（2026-09-17 增）、`started_at`/`finished_at`（2026-09-18 增）= 这次写回 `sr_tasks`
的值，与 `GET /api/queue` 同名字段同源。它们必须随帧下发：客户端手上那份只来自 `GET`，而那次
GET 通常就发生在提交刚落库之后（两列时间窗还是 `NULL`）—— 只推 `state` 的话，任务一完成耗时列
就从运行中的正常值掉成「0 秒」（2026-09-17 真机）。写库失败时这些字段**缺席**（不发本地时钟值），
客户端保留旧快照，与 GET 的读数保持一致；因此客户端须按「字段可能不存在」实现。

**耗时（队列页那一列）**：`finished_at − started_at`，运行中的行用浏览器时钟现算。两个锚点都钉在
**真实转换点**上（首次看到 RUNNING / 看到 COMPLETED·FAILED），所以量的是**本次运行**、不含排队。
两个坑各自对应一次真机现象，改的时候别退回去：

- **不能拿 `created_at` 当起点**。一行 = 一个指纹，同一场景同一参数重交时幂等层复用同一行
  （`run_sr._resolve_existing`），`created_at` 停在**第一次**提交的时刻 —— 于是「耗时」量的是行龄。
  真机表现：昨天失败的那次重交后跑完，「耗时」显示「30 时 00 分」，实际只跑了 200 多秒
  （2026-09-18）。
- **重启（校准缓存为空）不能重算**。`state.task_cache` 是内存态，校准器原来只拿它当比较基准：
  sr-api 一重启，每行都被判成「状态变了」→ 全部写回 + 刷新时间戳，几天前跑完的行集体变行龄。
  现在基准缺失时回落到 `sr_tasks.status`（库里存的状态），只补发真正变了的行（2026-09-18）。
- 本次运行的起点没观测到（整段运行期间 sr-api 不在）→ `started_at` 为 `null`，界面显示「—」。
  概率低（校准时距 2s），且**不猜**：退回 `created_at` 就是把上面第一个坑请回来。
- 加这两列前建的行不回填，其耗时也显示「—」（`store._ensure_columns` 只补列不改数据）。

**SSE 消费端契约（2026-09-22 订正，服务端一字未改）** —— 改的是前端怎么用这条流：

- **订阅常驻在应用外壳上**（`App.vue` 挂载时 `queue.connect()`），不再由队列页 / 查看器侧舱
  各自的挂载生命周期决定。理由：右下角「任务跑完了」的提醒要在用户**不在**队列页时也能到
  （提交完切去查看器看影像是常态，此前那种状态下这条流根本没人订阅）。队列页与侧舱那两个
  调用点**原样保留**，`stores/queue` 里按引用计数收（归零才真的断）—— 它们卸载时不会把外壳
  那一份带走，重复调用也不会叠加出第二条连接。
- **这条流没有心跳帧**：上面 schema 里那行 `{"type":"ping"}` 服务端**不发**（`platform.py` 的
  `_broadcast` 只广播业务帧，没有定时心跳）。因此「连接还活着」在客户端**只能由 fetch 拿到
  响应头**证明（`subscribeQueueEvents` 的 `onOpen`）；对端把流关掉时 `readSseStream` 是**正常
  返回**、不抛错，只有 `onClose` 才算得出「掉线」。重连与断线条都建立在这两个回调上；将来
  服务端若真加了心跳帧，`stepSse`/`readSseStream` 直接丢弃即可，不必改消费端。
- **掉线自动重连**：退避 2s 起、每次翻倍、封顶 30s（`nextBackoff`），连上即复位到 2s。
- **重连成功后必须重新 `GET /api/queue` 校准一次**：这条流**没有历史回放**（没有 Last-Event-ID
  之类的续传），断着的那段时间里状态变化全丢了。服务端「重启后按库里存的状态重判、只补发
  真正变了的行」（见上「重启（校准缓存为空）不能重算」）正为此准备 —— 两边口径要一起看。
- **终态 → 右下角提醒**（`lib/notices.ts` 纯函数 + `stores/notices.ts` 栈）：只对 `COMPLETED` /
  `FAILED` 弹，中间态与 `CANCELLED`/`UNKNOWN` 都不弹。文案**不编产物名** —— `job_update` 帧
  里没有产物文件名，所以标题只报 `task_id`，第二行取列表行的 `lq_path` 末段（场景目录名）+
  `sr_scale`；**行还没 GET 回来时只报 task_id**（订阅是常驻的，这次会话可能还没拉过队列），
  失败原因优先用帧里的 `error`，没有才回落到 `log_dir` 末段 / 「原因见队列页」。
- **提醒去重键 `<task_id>:<state>`，一次会话内有效**。刷新页面即清空：刷新期间跑完的任务
  **不补弹**（不引入跨会话状态，回到队列页看列表即可 —— 用户口径 2026-09-22）。

### 3.4 掩码（查看器 → SR 提交）

`POST /api/masks` — body（`lq_path` 与 `scene_id` 二选一，都给时 `lq_path` 优先）：
```json
{"lq_path":"/DiskArray/GSHC2IMPS/PRODUCT/2026/09/17/<生产编号>",
 "polygons":[{"label":"roi_1","points":[[x,y],…]}, …],
 "W": 24739, "H": 24199}
```
或 legacy 形态 `{"scene_id":"<阶段4 不透明 scene id>", "polygons":…, "W":…, "H":…}`。

- **路径语义（已与用户确认）**：`lq_path` 走 `pathguard.to_posix_array_path` +
  `ensure_allowed(kind="dir")`（Windows 形态 `W:\…` 也吃；白名单外 → 403，不是场景目录
  → 404 并列出试过的候选名）。legacy `scene_id` 经 `paths.scene_id_to_abs` 解析回场景文件
  绝对路径（fake/越界 → 404/400，需配 `SR_SCENES_ROOT`）。两条路都收敛到
  `scene_search.input_scene_path` —— 「是不是场景目录」只此一处判断。
- **落盘位置与命名**：掩码写进**该场景输入影像所在目录**，名字取
  `scene_search.mask_stem`（= **输入影像的 stem**，不是目录名）：SC 场景是
  `<目录名>.tif` → `<目录名>_mask.tif`；RC 场景输入叫 `PAN.tif` → `PAN_mask.tif`。
  与提交侧推导同源，否则 RC 场景写进去也白写（提交时去找的是另一个名字）。
  两份产物：`<stem>_mask.tif`（0/255 Deflate，`services.mask.write_mask_tif`）+
  `<stem>_mask.txt`（`write_mask_centroid_txt` 参考格式）。超分结果 `.tif` 也落同目录
  （run_sr/0817 脚本自身行为，本端点不处理）。
- 复用 `services.mask`：`rasterize_polygons(W,H, polygons→points)`（全分辨率 Pillow；24739×24199 ≈ 580MB 位图，盘阵机内存可扛，**不落浏览器**）。W/H 以 body 为准（= 元数据 W/H，不探 TIF）。
- 返回 `200 {"mask_path": "<绝对路径>", "mask_txt": "<绝对路径>", "lq_path": "<原图所在目录>", "task_draft": {lq_path, mask_path, …默认 sr 参数}}`。`task_draft` 供前端**预填**队列表单（不自动提交，Slurm 是真副作用；用户点提交才发 POST /api/queue）。
- 幂等：同 scene_id 重复提交 → 覆盖同名掩码（掩码生成无副作用风险，允许重复）。

### 3.5 盘阵任意场景目录（`POST /api/scenes/resolve`，2026-09-17）

用途：让 `SR_SCENES_ROOT`（datahub）之外的生产场景目录（真机在
`/DiskArray/GSHC2IMPS/PRODUCT/<年>/<月>/<日>/<卫星型号>/<段级目录>/<景级目录>`）
也能在查看器打开、画掩码、就地提交 SR，不必把数据搬进 datahub。前端两个入口
（场景库页检索条下的输入框、查看器工具栏的「盘阵场景」栏）共用一个 `ScenePathBar`。

请求体二选一：
```json
{"path": "W:\\GSHC2IMPS\\PRODUCT\\2026\\09\\02\\JL1KF02B03\\<段级目录>\\<景级目录>"}
```
```json
{"name": "<用户拖进来的文件名，可带后缀>", "date": "2026-09-02"}
```

- `path`：`W:\…`（经 `SR_DRIVE_MAP` 映射）或 `/DiskArray/…` 都吃，要写到**景级目录**这一层。
  **也可以直接给单个 `.tif` 文件路径**（2026-09-17 增补）——随手贴一张图也能看。此时走
  「裸 TIF」分支：`resolved.dir` = 该文件的父目录、`input` = 该文件；**父目录确实是合法场景
  目录**（`input_scene_path` 命中）时与目录形态完全等价，否则 `row.lq_path` 与 `mask_path`
  一起为 `null`、`sr_capable=false`（能看，但提交 SR 在盘阵上跑不起来）。给的文件后缀不是
  `.tif/.tiff`（`.jpg` 源、`meta.xml`、掩码…）→ **400** 说清，而不是掉进目录逻辑报「目录不存在」。
- `{name}`（`date` 可省）：查看器里选了本地影像后的反推路径。浏览器拿不到本地文件的
  绝对路径（`File` 只有 name/size/type），只能把**裸文件名**交给后端，由
  `backend/pathguard.infer_scene_paths` 反推候选目录 —— 命名规则与模板的唯一真源就在
  那里，**前端不自拼路径**（2026-09-17 前前端自己写死过一份，导致 `SR_SCENE_PATH_TEMPLATE`
  从未生效）。日期不给就由后端从文件名里的 14/8 位时间戳取，**取不到就报错让用户手粘**。
  候选按序（**两天 × 一条模板**，依次 stat）：
  1. `W:\GSHC2IMPS\PRODUCT\{y}\{m}\{d}\{sat}\{mid}\{name}` —— 生产树六层真形态，**成像日**；
  2. 同上，日期换成**次日**。
  名字里的 14 位是成像时刻，而盘阵按生产日建目录（夜里成像的景记在第二天），所以
  `{d}` 要试两天；模板不含日期占位符时两天渲染出同一条，去重后仍只有一条。旧扁平形态
  （`PRODUCT\{y}\{m}\{d}\{name}`，四段）那条兜底候选 2026-09-18 已删 —— 生产树上永远落空，
  只会让 404 的候选清单里多一条不符合六段格式的目录。
  配了 `SR_SCENE_PATH_TEMPLATE` 则只按配置的那一条走（同样给两天）。名字不符合生产命名规则
  （拆不出 `{sat}`/`{mid}`）时跳过第 1 条，不硬拼空段。各段的分隔符**下划线与空格都认**
  （2026-09-18 起）：盘阵上的真形态是下划线，而用户口径里出现过空格写法；段级目录名按
  **原文的分隔符**重建，拿空格名拼出下划线的段级目录必然 stat 不到。规则见
  [docs/sr_code/production-scene-naming.md](../sr_code/production-scene-naming.md)。
  - **第二阶段候选（2026-09-21，仅 jpg）**：中间产物的文件名是**在生产全名后面再粘一段**
    （`<目录名>_sr.jpg`），按原名反推出的目录名会带上那条尾巴（`…/<目录名>_sr`），盘阵上
    没有那个目录。所以 jpg 还会按「去掉尾段」的名字再反推一遍 —— 逐段切最多**两段**，
    每切一段给一条候选（切一段与切两段两种读法都成立：`<目录名>_sr_2.jpg` 可能是
    `<目录名>` 的 `sr_2` 产物，也可能是 `<目录名>_sr` 的 `2` 产物；`sr_2` 这类**带下划线的
    suffix** 因此也能整段切掉，按段数猜会切错，见 `scene_search.de_suffixed_stems`）。
    尾段过不了 `SUFFIX_RE` 的字符约束（`- 副本` 这类带空格的、`.preview`）就停止下切。
    **`_NOSR` 与 `preview` 由这一轮的切负责剥**（2026-09-24 订正）：两者都是「剥掉才回到
    真名」的环节尾段，所以都在切段循环**内**处理，不在循环外预剥 —— 预剥会让
    `<目录名>_NOSR` 剩下的真名一条候选都进不去（`de_suffixed_stems` 的文档记了这个坑）。
    **只在前一阶段全落空时才展开**：常见情形（本体显示件、`.tif` 反推、粘路径）一个 stat
    都不多花 —— 钉住探测量上限的那几条用例正走在上面。
  - **平台自烤的那份预览也认（2026-09-21 晚）**：`<栅格 stem>_preview.jpg` 是拖入链
    自己写进场景目录的（`GET /api/scenes/{id}/preview-drop`，见
    [preview-bake-pipeline §4.8](../knowledge/preview-bake-pipeline.md)），它末尾的
    `preview` 同样在 `_NON_STAGE_TAILS` 名单里。那份名单是防**盘阵侧**派生件的
    （`<目录名>_cloud.jpg` 这类同级真有栅格，剥掉会正好落到真实场景目录上 → 云量图被
    认成本体），可平台自己产出的这份是干净的尾段。所以 `preview` 是名单里**唯一可以剥**
    的一个：剥掉后正是那份栅格的 stem（`<目录名>_preview` → `<目录名>`＝本体，
    `<目录名>_sr_preview` → `<目录名>_sr`＝产物），环节照旧由
    `scene_search.stage_of_jpg` 判。**只剥一层**，剥完仍在名单里（`<目录名>_cloud_preview`）
    照旧不认。（`_NOSR` 不在这份名单里 —— 它是一段真的环节尾段，由上一段的切段逻辑剥掉。）
- 响应 `200 {"source":"manual", "row": <与 /api/scenes 行同形>, "resolved": {...}}`：
  `row.id` 是 `~` + base64url(绝对路径)（手工行形态，见 `api/paths.py`；库行 id 一字未变），
  `row.manual=true`、`jpgUrl` 在**库外**为 `null`（预览走 `GET /api/scenes/{id}/preview` 回
  JPEG 字节）、`row.W/H` 由 `preview_jpg.scene_dims` 回填。
  **`row.hasPreview` 一律填缓存到底在不在的真值**（库外没有静态 URL，但前端要靠它判断
  「这次会不会触发首次烘焙」并提示用户等待），`row.lq_path` 与 `resolved.sr_capable` 同源同真假。
  **`row.previewDiv`**（2026-09-19）填盘上那份 `<stem>_preview.jpg` **是在哪一档烤的**，
  读不出戳 → `null` —— 前端拿它 + 全局档位一起判「要不要重烤」，只看 `hasPreview` 会在
  改档位后端着旧档位那张图上桌（见 3.6）。
  **`row.rasterPreview`（2026-09-20 增）**：源是显示件 jpg、且同目录有位更清晰的栅格时，
  附上供前端判「谁清晰」的尺寸对照（字段与判据见 §4.6）；`{path}` 分支给的是单个文件、
  不附带。**注意这一条在 `{path}` 与 `{name}` 两个分支里算的是不同的东西**：`{path}` 打开
  一个目录时，`row` 描述的是**栅格输入影像**（`input_scene_path` 命中的那份 tif），它的
  `rasterPreview` 恒为 `null`（那份 tif 自己就是源）；只有拖 jpg（`{name}` 以 `.jpg` 结尾）
  那一路才会非空 —— 而它比的是**盘阵上那份同名 jpg**（`<场景目录>/<name>`）的尺寸，不是
  用户本地拖进来那份的：指纹对 jpg 行只比名字（见上），本地那份可能另存过、缩过，平台口径
  是「盘阵上的才是基准」。盘阵上那份 jpg 被删/改名而只剩本地副本时读不出尺寸 → `null` →
  前端老实显示本地那份（保守方向是对的）。
  **`row` 描述的是「用户拖进来的那一个环节」自己那份栅格**（2026-09-21）：本体的显示件
  指向本体输入影像，中间产物指向 `<目录名>_<suffix>.tif`（见下面「中间产物也能关联」）。
  `W`/`H` 因此是那一份的尺寸 —— 产物的各边是本体的倍数，拿本体的尺寸建画布整张比例都是错的。
  `resolved` = `{dir, input, input_name, mask_path, mask_exists, writable, sr_capable,
  kind, suffix}`，路径一律**盘阵 POSIX 形态**（与提交侧归一化同一口径，前端 lq_path /
  mask_path 逐字比得上）。
  `writable` = 服务账号对该目录是否有写权限，提前告知好过提交后才发现写不了。
  `sr_capable` = 这个目录能不能提交 SR：目录形态**恒为 true**；裸 `.tif` 时看它父目录是不是
  合法场景目录，**中间产物恒为 false**（见下条）。前端 `lqPath` 就是按它写的（`lqPath` 是
  「能否提交 SR」的唯一判据）。
  `resolved.input` / `input_name` **恒指本体输入影像**，即便这次拖进来的是产物 —— 它的语义
  是「SR 跑的是哪份文件」，不是「这一行描述哪张图」（后者看 `row`）。
  - **`kind`**（2026-09-21 增）= 这次拖进来的影像是场景里的哪个环节，取值
    `input` / `product` / `nosr`；`{path}` 分支与 `.tif` 反推恒为 `input`。
  - **`suffix`** = 中间产物那一段后缀（`product` / `nosr` 时非空，本体为空串），**从文件名
    本身切**，不查任务库也不查配置：SR 常在平台外跑，库里没有记录时照样得认得出。
  - **中间产物的三条例外**（`kind != 'input'`，同一件事的三种表现，缺一不可）：
    `row.lq_path = null`、`resolved.sr_capable = false`、`resolved.mask_path = null`
    且 `mask_exists = false`。原因是**掩码与 SR 都建在本体影像的网格上**，产物是它的放大
    结果，在产物上画的坐标写到本体掩码文件上整片都是错的。第一道锁在服务端（这里），第二道
    在前端 `stageKind`（`lib/stage.ts`）。**`lq_path` 置空是这道锁的本体**：前端
    `bakeMaskToServer` 是拿 `rec.lqPath` + `rec.W/H` 去 POST `/api/masks` 的，产物尺寸的
    W/H 配上本体的 lq_path 会把一张产物尺寸的掩码**静默写到本体的掩码文件上**（§3.4 那段按
    lq_path 找输入影像的逻辑）。
- **不扫盘**：只 `stat` 用户给的目录，判定顺序是固定候选文件名（`<目录名>.tif/.tiff/.img`
  再 `PAN.*`），上界 6 次 stat，不 `ls`/`glob`/`rglob`/`iterdir`。盘阵数据量极大，
  列举一次就可能卡死；这条由测试用 `patch(Path, "rglob"/"glob"/"iterdir")` + `os.listdir`
  /`os.scandir`/`os.walk` 全打成 `AssertionError` 来钉。
  环节判定（`stage_of_jpg`）**不额外探测本体**（本体的判据在 `is_scene_file` 与
  `_fingerprint_mismatch` 里已经写过一遍），产物最多多 2 次 `is_file()`；整条请求的
  `Path.stat` 总次数另有上限钉着（`TestNeverListsDirectories` ≤30、jpg 那条 ≤35）。
- **判据**：目录含 `<目录名>_meta.xml` **且**含输入影像之一（与库内场景同一套判据，
  `scene_search.is_scene_dir` / `input_scene_path`）。缺 meta.xml 的场景 SR 脚本判不出
  RC/SC，本就不该放进来。
- 错误码分工：**400** 形态非法或压根反推不出来（相对路径 / `..` / UNC / 未知盘符 /
  日期格式 / 文件名里没有时间戳 / 名字不符合生产命名规则）· **403** 越
  `SR_ALLOWED_ROOTS` 白名单 · **404** 反推成立但盘阵上没有合法场景目录、没有输入影像，
  或拖进来的 jpg **不是本景的输入件或中间产物**（名字切不干净，或那条环节的同名栅格不在）·
  **422** 场景成立但读不到**该环节那份栅格**的尺寸（前端开图要 W/H）。
  404 的 `detail` 必须列出试过哪些候选、各自为什么不行（前端原样渲染）——「猜错必须报错」
  的落地。`detail` 只能是**字符串**：`api.ts::http()` 把它直接塞进 `Error`，给对象
  用户看到的是 `[object Object]`。反推按两天找过（候选 > 1 条）时，`detail` 末尾补一句
  「成像日与次日都找过 —— 盘阵按生产日建目录，深夜成像的景常记在次日」（2026-09-18 增），
  否则用户看见两条只差一天的路径只会更懵。
  「为什么不行」按**粘错了哪一层**分派（2026-09-18 修）：`<目录名>_meta.xml` 的前缀是
  **完整生产名**（含 14 位成像时刻），所以粘日期目录（`…/PRODUCT/2026/09/18`，正是场景栏
  预填的那一层）得到的「缺 `18_meta.xml`」是句没有任何信息量的话。现在分层报：日期目录报
  「这是日期目录，场景目录在它下面 3 层 `<卫星型号>/<段级目录>/<景级目录>`」，卫星型号层
  报「还差 2 层」、段级目录报「还差 1 层」（段级名同样带 14 位成像时刻，光看名字会误报
  「缺 `<段级名>_meta.xml`」，所以按层数先认）、场景目录内部的子目录报「比场景目录还深」、
  名字压根不是生产名的报「不是完整生产名」。层与名字全部由 `pathguard` 的三个**纯词法**
  原语判（`production_tree_depth` / `flat_scene_layout` / `looks_like_scene_name`），
  **不参与准入** —— 准入始终只有一条：`<目录名>_meta.xml` 在不在。
- **`{name}` 可再带 `size_bytes`（2026-09-18，拖拽入口用）：把本地文件的字节数一起发过来，
  后端要求名字与字节数都对得上才认**（`_fingerprint_mismatch`）。**比的对象按拖入的是栅格
  还是 jpg 分岔**：栅格比「输入影像 stem + 字节数」，jpg 只比「场景目录名」。
  不传 `size_bytes` 则只看目录/影像存不存在（粘路径、第三方调用一切照旧）。
  - 栅格为什么两个都要：`input_scene_path` 的候选次序是 `<目录名>.{tif,tiff,img}` 在前、
    `PAN.{tif,tiff,img}` 在后，返回第一个存在的。RC 场景的输入影像是 `PAN.tif`，而同目录
    里往往还躺着 `<目录名>.tif`（上游 SC 步骤的产物）—— 光比字节数，用户拖进来的可能是
    另一张图；那之后画的掩码坐标会整片落在别的影像上。
  - 不符时**按这条候选不合格处理**（记进 `reasons` 后继续试下一条候选），最终仍是 404 且
    `detail` 里列出盘阵侧那个文件的名字与字节数，**不新增错误码**。
  - **`name` 以 `.jpg/.jpeg` 结尾时：名字比 `<场景目录名>`，字节数不比**（2026-09-18 订正）。
    盘阵那份 jpg 是**显示件**（8bit 就绪预览，见 `scene_search._IMAGE_EXTS`），SR 从不在
    它上面跑，与输入的 TIF 是两份产物、不可能同字节 —— 所以字节数那一半对它无意义。名字
    这一半**不能拿栅格输入的 stem 去比**：纯 RC 场景（目录里只有 `PAN.tif`）`inp.stem` 是
    `PAN`，而显示件叫 `<编号>.jpg`（生产全名），永远比不过 —— 那会让「拖 jpg」这条入口
    恰好在 SR 真要跑的场景上恒 404（初版实现如此，真机表现为「极少出现盘阵小标」）。
    判据只有一条：jpg 名（去后缀）== 场景目录名。默认模板下候选目录名就是由这个名字拼出来
    的，所以这条通常直接成立 —— 它挡的是「换了 `SR_SCENE_PATH_TEMPLATE`、场景目录改了命名」
    的部署；真正挡住派生件（`_cloud.jpg`、`_preview.jpg`）的是候选目录根本不存在。
    也**不泛化成「后缀不同就放行」**：那会连 `SC.tiff` 与 `SC.tif` 一起放过。
    名字比的是**场景目录名**，不是栅格输入的 stem（见上一条）—— 目录由**名字**锁死，
    环节由 `stage_of_jpg` 判（见下）。
  - **中间产物名（2026-09-21 增）**：拖 `2026-09-21` 起不止认显示件，也认它的中间产物
    —— 用户想「把跑出来的产物拖进来看一眼」是自然动作。可拖的四类名字：
    `<目录名>.jpg`（本体显示件）、`<目录名>_<suffix>.jpg`（本轮超分产物）、
    `<目录名>_NOSR.jpg`（未超分那份，2026-09-24 用户口径）、
    `<目录名>_<suffix>_NOSR.jpg`（writeTiff 改名留下的上一次产物，标同一个 `NOSR` 标；
    两义与候选次序见 [preview-bake-pipeline §4.11](../knowledge/preview-bake-pipeline.md)）。
    **真门是「同级栅格真的在」**（`<目录>/<名>.tif|.tiff`，按 `_PRODUCT_EXT_ORDER` 试）：
    「名字切得干净」只说明它长得像产物名，而一个场景目录里躺着十几样东西，
    `<目录名>_cloud.jpg` 同样切得干净 —— 只有「它有一份同名栅格」才说明这份 jpg 是某个
    **环节影像的显示件**。另配一份小名单 `_NON_STAGE_TAILS`（`cloud`/`thumb`/`mask`/`ori`/
    `preview`）挡住那些真有同名栅格的派生件（云量图在真机上确实存在）。
    两头都不成立 → 这条候选不合格，原因记进 `reasons`，最终 404。
  - 名字里取不到 14/8 位成像时刻的 jpg（`PAN.jpg`、Windows 副本、别处导出的图）→ **400**，
    说清「平台不猜目录」以及该改拖哪一份。反推路径的唯一依据是文件名，没有日期就不知道该去
    `<年>/<月>/<日>` 哪一天找，猜一个就是拿别景的 `lq_path` 去提交。
  - `size_bytes` 非正整数 → **400**。`{path}` 分支是精确路径，不收这个字段。
- **`{name}` 还可带 `anchor`（2026-09-21 增，拖拽入口用）：一个或几个盘阵目录**，
  即**用户当前打开着的场景**（前端 `sceneAnchors([最近显示过的 sceneDir, A 格, B 格])`，
  上限 3 条）。它不是新的一类反推，而是给「名字里没有场景身份」的那类 jpg 留的唯一出口：
  **RC 场景的产物叫 `PAN_<suffix>.jpg`**（产物名按**输入影像名**拼，RC 的输入是 `PAN.tif`，
  见 [sr-pipeline-overview §4.6](../sr_code/sr-pipeline-overview.md)），这个名字里既没有
  卫星段也没有成像时刻，反推不出它是哪一天哪一景的目录 —— 那一步就 400 了。
  - **只在名字自己反推不出来时才用**（`parse_scene_date` 取不到时间戳、且后缀是
    `.jpg/.jpeg`）：有成像时刻的名字照旧走上面那套候选，**anchor 连一次 stat 都不花**。
    于是「正常生产名」与「锚定」永不打架 —— 名字能反推时同名 anchor 一律让位。
  - 判定就是**在锚定目录里认这份 jpg**，判据与 `{name}` 分支里 `scan` 那一段**完全相同**
    （`scene_search.stage_of_jpg`），唯一的差别是**不过 `_fingerprint_mismatch`**：那条比的是
    「jpg 名 == 场景目录名」，而 `PAN_260318.jpg` 恰恰不是目录名 —— 真门是
    「这一环节自己的栅格躺在同级」（`<锚定目录>/PAN_260318.tif` 在）。目录由**用户自己
    打开的**那一景给定，所以这不是「猜目录」：盘的哪个目录来自前端上下文，不是从名字推的。
  - **按序取第一个成立的**（顺序＝优先级：最近显示过的那一景 → A 格 → B 格）。
    一个都不成立 → 退回原来的 400，**并把每个锚定目录各自为什么不行追加进 `detail`**
    （「目录不存在」/「不是可提交的场景目录」/「这一景里没有 `PAN_260318.tif/.tiff`」）。
  - 坏值一律**跳过并记原因，不 400 也不 403**：它不是断言而是提示，不该因为前端多塞了一个
    陈旧目录就把整次拖拽打回。唯一不松的是白名单 —— 越 `SR_ALLOWED_ROOTS` 的目录直接丢弃
    （记原因），不 stat。接受单个字符串或字符串数组，数组只取前 4 条。
  - **锚定命中后 `resolved.dir` = 那个锚定目录**，其余收尾与反推那条路共用同一段代码
    （`kind`/`suffix`/`row` 描述该环节自己那份栅格、`lq_path` 在产物上置空等照旧）。

### 3.6 拖拽入口的预览（`GET /api/scenes/{id}/preview-drop`，2026-09-19 定名）

用途：用户把盘阵上的 `.tif` **拖进查看器**时，不再让浏览器重新解码整幅原图（真机上一次
几十秒、几百 MB），改用服务端烘焙的下采样预览 JPG —— 与场景库打开同一张图的渲染路径、
同一套烘焙规则（`rule_stamp` 相同），所以两条链出来的字节一致。

**拖进来的是 `.jpg` 时这条端点默认不参与**（2026-09-18）：用户拖的那张就是他自己要看的那张，
再拿服务端缩图顶掉反而降清，还白等一次解压采样 —— 关联成功后前端直接 `blob = file`
本地解码，像素与字节都来自用户那份。这条端点只服务「拖裸 `.tif` 反推命中」那条路。
**例外（2026-09-20，见 §4.6）**：同目录有位更清晰的栅格、且当前档位下服务端从它烤出来的
比这张 jpg 更清晰时，jpg 那一路也走这条端点 —— 用户报的「盘阵那份预生成显示件分辨率不够」
说的就是这种情况，而它是**按尺寸算出来的**、不是「jpg 就一律」。

**两条端点同款的一处前置（2026-09-20）**：请求里的源是 `.jpg/.jpeg` 时，先
`scene_search.sibling_raster_path(源)` 探一次同名栅格，命中就把源换成那份栅格再往下走。
`/preview` 与 `/preview-drop` 各插三行、逻辑相同。**其余一切照旧**：落点规则不变
（`preview_jpg_name` 只由源 stem 拼，对 jpg 与 tif 是同一个文件名，栅格行与 jpg 行因此
  **共用同一份落点**）、
`?div=` 语义不变、`X-SR-Preview-Fallback` 兜底不变、也没有多出一次列举目录 ——
探的是固定候选名（`.tif/.tiff/.img`，见 `_RASTER_EXT_ORDER`），逐个 `is_file()`。
探不到栅格就**逐字节维持原行为**（回源字节）。也就是说：换不换只有后端知道，前端只管
按 `?div=` 取图。

**2026-09-19 两处改动**：落点从临时缓存改成**盘阵场景目录**（`<源同目录>/<stem>_preview.jpg`），
端点随之从 `preview-tmp` **改名 `preview-drop`**（再叫 tmp 就是撒谎，它写的是永久文件）；
同时两条端点都开始接 `div` 查询参数（见下）。

与 `GET /api/scenes/{id}/preview` 的差别：

| | `/preview`（生产缓存） | `/preview-drop`（拖入） |
|---|---|---|
| 落点 | 源同目录 `<stem>.preview.jpg`，或 `SR_PREVIEWS_ROOT` 镜像树 | **恒为**源同目录 `<stem>_preview.jpg` |
| `SR_PREVIEWS_ROOT` | 吃（配了就落镜像树） | **不吃**（恒落源同目录） |
| 生命周期 | 跟场景数据长期存在 | 跟场景数据长期存在（**原地覆盖，不堆积**） |
| 清理者 | 无（同上，靠覆盖） | **无任何清理者** |
| 响应头 | 无（静态 URL 那条走 nginx 的 `max-age=3600`） | `Cache-Control: no-store` + 见下「兜底响应头」 |
| 消费方 | 场景库行、粘路径打开 | **只有**拖拽入口（`viewer.tryLinkScenes` 命中后升级 rec） |

- **为什么带下划线**：`<stem>_preview.jpg` 与源文件同目录，而 `scene_search.is_scene_file`
  是「文件名 == 目录名 或 `PAN`」的白名单 —— 用下划线就不必再动那个白名单；同时也与真机上
  源文件自带的那份同名 `.jpg`（`<stem>.jpg`）区分开，不会被当成"源本身就是显示件"。
  已有测试钉住它**不会**被列成一行场景。
- **代价（明知故犯）**：它不吃 `SR_PREVIEWS_ROOT`，所以同一个场景在配了 `SR_PREVIEWS_ROOT`
  的部署下会有**两份缓存**（镜像树里一份 `.preview.jpg`、场景目录里一份 `_preview.jpg`），
  且盘上多出的这一份**没有任何清理者**（原地覆盖不堆积，但要知道它在）。换名成
  `<stem>.preview.jpg` 能省掉这两条，2026-09-19 讨论后仍选了 `_preview.jpg`。
- **落盘阵失败时兜底到临时缓存**：落盘阵要求服务账号（`User=nginx`）对场景目录有写权限，
  这一条在真机上仍是待确认项。所以先 `os.access(dir, W_OK)` 预判，不可写或写失败 →
  退回 `SR_TEMP_PREVIEWS_ROOT/<YYYY-MM-DD>/<sha256(源绝对路径)[:16]>.jpg`（旧的 tmp 落点，
  1 天 TTL、每天 0 点整桶删除），并回响应头 **`X-SR-Preview-Fallback: tmp`**。
  前端认到这个头就在提示文案里如实说明「该场景目录不可写，预览暂时落在服务器临时缓存」。
  **两条都失败才 422**。`X-SR-Preview-Fallback` 已加进 CORSMiddleware 的 `expose_headers`
  —— 不补这个头，异源部署与 e2e 里前端恒读到 `null`，这条设计就是死的。
- **`div` 查询参数（两条端点都有，2026-09-19）**：下采样档位，取值 ∈ `preview_jpg.PREVIEW_DIVISORS`
  = `(2, 4, 8, 16, 32)`，含义是**各边除以 N**（`preview_max_edge = round(long_edge / div)`），
  缺省 **2**。非法值 → **400**。缺省是 2 而不是 4，是因为这一层是**烘焙契约**、要与
  `LEGACY_PREVIEW_DIV` 逐字节对齐；前端那个「默认 ÷4」只是 UI 默认值，活在前端常量里。
- **档位是全局的**：工具栏定位组件右侧那条 5 档拖动条（默认 ÷4，存 `localStorage` 的
  `sr.previewDiv`）是平台级的「预览烘焙精度」，**拖入 / 场景库打开 / 粘盘阵路径打开**
  三条入口都按当前档位烤，由前端在请求里带上 `div`。后端自己不认识"当前档位"。
- **规则戳随之 bump 到 v3**：`srprev:v3:div<N>+equal:q<Q>`（旧的是 `srprev:v2:equal:q<Q>`）。
  档位进戳，是「同源同落点、div 不同 → 必须重烤」的判据；**不加 `div==2` 的特例拼法**。
- **行上新增 `previewDiv`**：`GET /api/scenes` 与 `POST /api/scenes/resolve` 的每行多一个
  `previewDiv`，值是**盘上那份预览 JPG 是在哪一档烤的**（读 JPEG 注释戳，只读头不解像素），
  读不出/无戳 → `null`；源本身就是 `.jpg/.jpeg` 的行恒 `null`（档位对显示件无意义）。
  按 `(path, mtime)` 缓存。**`hasPreview` / `jpgUrl` 的语义一个字不动**。
- **为什么必须有 `previewDiv`**：`hasPreview` 只答「盘上那份在不在」，不认档位；而前端那次
  「重烤再取图」是带条件的。缺了它，改档位后盘上文件仍在 → `hasPreview` 仍为真 → 前端
  跳过重烤 → 用户看到的还是旧档位那张图。**库外手工行**（`jpgUrl` 为 `null`，每次走
  `/preview` 回字节）天然不受影响，红的是**凡有静态 URL 的行**。
- **不进库行的那部分语义照旧**：前端取这条用的是独立函数 `api.fetchDropSceneJpg`，
  **不改写** `row.hasPreview` / `row.jpgUrl` / `row.previewDiv` —— 那三个字段锚在**生产那份
  `<stem>_preview.jpg`** 上，被这条链的产物置真之后，用户再从场景库打开同一场景就会跳过
  懒生成、直接打一个 404 的静态 URL。（有测试钉着这条。）
- **URL 不可静态映射**：响应体本身就是那张 JPEG。nginx 对这条**不用改**：`location ~* /api/scenes/[^/]+/preview$`
  的 `$` 锚点本来就不匹配 `/preview-drop`，它落到外层 `location /api/` 正好拿到 `no-store`
  的效果（别把 `$` 去掉）。location 匹配也**不看查询串**，加 `div` 同样不影响。
- 错误码：`.jpg/.jpeg` 源直接回源字节；不可访问 **404**；`div` 非法 **400**；
  两条落点都写不进去 **422**（detail 带两边的原因）。
- **清理不在请求路径里**：`purge_temp_previews` 要列举缓存根，而「不扫盘」是硬约束（见 3.5），
  所以它只出现在 `api/app.py` 的后台任务 `_tmp_preview_purge_loop` 里，且只管**兜底**那一份。

### 3.7 《待修复清单》写回（`POST /api/qclist/write`，2026-09-18）

> **状态：已定**（同日按上面这份评审稿落地）。后端 `backend/api/platform.py` +
> `backend/tests/test_api_platform.py::TestQcListWrite`；前端同步删掉 FSA 那条路
> （`stores/qclist.ts` 的文件选择器/句柄、`lib/qclist.ts::encodeQcText`）；写盘第一次进
> 回归：`.e2e/test-manual-scene.js` §H 走「导入 GBK 清单 → 粘路径 → 同步 → 从磁盘读回」。

用途：查看器的《待修复清单》面板把操作员标好的处置结果**原地写回**盘阵上那份 txt。

初版走浏览器 File System Access API（`showOpenFilePicker` + `createWritable`），2026-09-18
在真机上直接不可用：那个 API 在规范里是 `[SecureContext]` 标的，Chrome 只在 `https://`、
`http://localhost`、`http://127.0.0.1` 的页面上把它挂到 `window` 上，而真机是 nginx
`listen 80` 的 `http://内网IP`。清单本来就在盘阵上，而后端 `User=nginx` 本来就写得进去
（掩码、SR 产物、烘焙 JPG 全是它写的），所以改成后端写盘：http 下也能用，还顺带把
「GBK 清单写回 GBK」做对了（浏览器编不出 GBK，旧代码只能降级成 UTF-8+BOM）。

body：
```json
{"path":"W:\\GSHC2IMPS\\PRODUCT\\2026\\09\\17\\待修复清单.txt",
 "text":"<整份文档：上半部分逐字保留、下半部分只含终态行>",
 "encoding":"gbk",
 "mtime":1758171234.0}
```

- **路径语义**：`pathguard.to_posix_array_path` → `ensure_allowed(posix, kind="file")` —— 与
  `/api/scenes/resolve` 的 path 分支、`/api/masks` 的 `lq_path` 分支**同一套**（`W:\…` 靠
  `SR_DRIVE_MAP` 译成服务端形态，白名单靠 `SR_ALLOWED_ROOTS`）。**必须已存在、且必须是文件**：
  不让这个端点凭空造文件（路径敲错一个字，盘阵上就多一个垃圾文件）。
- **只收 `.txt`**（大小写不敏感）：本端点的语义是「覆盖」，不设后缀门就等于「盘阵上任何存在的
  文件都能被它改写」—— 一颗写错的 bug 足以盖掉 `.tif` 或 `meta.xml`。
- **`mtime` 护栏（可选）**：带了就与 `os.stat(target).st_mtime` 比，差超过 2 秒（网络盘/FAT 的
  时间戳粒度）一律拒。挡的是「操作员导入之后，质检那边又更新了一版清单」—— 照写会把他们的新行
  整段盖掉。前端传的是导入那个 `File` 的 `lastModified`；不带这个字段（curl、e2e）护栏自动跳过。
- **写前查权限**：目标目录与目标文件各做一次 `os.access(…, os.W_OK)`，不可写就直接拒并说清是
  哪一个，别让用户拿到 EACCES 原文。注意**原子替换只需要目录可写**（文件自己的 mode 拦不住
  `os.replace`），所以这两道检查不是冗余。
- **落盘**：`tempfile.mkstemp(dir=目标同目录)` → `os.chmod` 保留原权限位 → `os.replace` 原子替换
  （`services/preview_jpg.py` 那套）。先写临时文件再替换，是为了不出现「写了一半的清单」——
  这份 txt 是操作员一下午的标记，被截断比写不进去严重得多。
- **已知副作用**：替换后文件的**属主变成跑 API 的 nginx**（nginx 无权 chown 回去），权限位保留。
  质检部门若还要直接改这份 txt，清单所在目录要给组写权限 —— 见 `deploy/README.md`。
- **编码**：`encoding ∈ {"utf-8","gbk"}`（缺省 `utf-8`），由**后端**编码。正文里有 GBK 表示不了
  的字符 → 400。前端不再插手编码，只把导入时认出来的编码原样传过来（`lib/qclist.ts::decodeQcBytes`）。
- **`text` 不设大小上限**：清单是文本、且由本平台自己生成，人造上限只会误伤长清单。
- **错误码不分类**：请求侧被拒一律 **400**（detail 说清是路径形态、白名单、不存在、非 `.txt`、
  编码还是 mtime 冲突）；写盘 `OSError` → **422**。与 §3.5 那套 400/403/404 分门别类不同 ——
  前端只把 detail 原样显示，分类没有消费者。`PathDeniedError` 一律转 400。
- 返回 `200 {"path":"<posix 绝对路径>","bytes":1234,"encoding":"gbk"}`。
- **回归**：写盘这一步以前进不了自动化（`showOpenFilePicker` 是系统弹窗，见 §4 那条注记），
  改成 HTTP 之后 `.e2e/test-manual-scene.js` 整条能走通：导入 → 设路径 → 同步 → 从磁盘读回比对。

### 3.8 一个场景的三类图（`GET /api/scenes/{id}/siblings`，2026-09-20）

用途：把「这个场景的**输入影像**、**本轮超分产物**、**NOSR**各叫什么、在不在、各自的
场景 id 是什么」一次交代清楚。翻看器对比功能（下轮）要同时取这三张图，这个端点就是它的
取名与存在性来源；也是 NOSR 那份候选清单（`nosrCandidates`）的出口 —— 真机上命中的是哪个
名字，看这里。

**纯只读，四条永不**：永不烘焙、永不写盘、永不列举目录、永不改任何状态。全部探测都是
**固定候选名**的 `is_file()` / `stat()` —— 与 §3.5 的「不扫盘」是同一条纪律，实现里没有
`os.listdir/scandir/walk`、也没有 `Path.glob/rglob/iterdir`（测试 `TestNeverListsDirectories`
把这个端点一起罩住，`Path.stat` 次数另设上限）。

参数 `suffix?`。取值顺序**三级**，并把用了哪一级如实回报在 `suffixFrom` 里：

| 顺序 | 来源 | `suffixFrom` |
|---|---|---|
| 1 | `?suffix=`（调用方断言；过 `run_sr.SUFFIX_RE = ^[A-Za-z0-9_-]{1,16}$`，不合法 → **400**） | `"query"` |
| 2 | 该 `lq_path` **最近一条 COMPLETED** 任务的 `params.suffix`（跑的就是它，最权威） | `"task"` |
| 3 | `run_sr.default_suffix()`（配置文件里的 `<Suffix>`，读不出/不合法则回落 `DEFAULT_SUFFIX`） | `"default"` |
| — | 三级都拿不到 → **只有产物那一类**不出现（拼不出名字就不编）；`nosr` 与 suffix 无关，照旧出现 | `null` |

- **必须回报 `suffixFrom`**：「按配置猜出来的名字」与「真跑过的名字」在响应里长得一样，
  不标出来，前端（和人）就没法知道这个 `_260318.tif` 是实事求是还是碰运气。
- **只认 COMPLETED**：FAILED 的行已经动过产物路径（`writeTiff` 先改名再写），名字拼得出
  来，但内容是半截 —— 拿它去拼产物名，界面就会把半截图当成「本次结果」。这与 §4.5 急烤
  「只烤 COMPLETED」是同一条理由。
- 匹配用**归一化后的 POSIX 路径**在 `list_sr_tasks()` 的内存结果上过滤，不走 SQL `LIKE`：
  生产目录名全是下划线，而 `_` 在 `LIKE` 里是通配符，靠转义兜着不如不写这条 SQL。

响应（`200`）：

```json
{"sceneId":"…","lqPath":"/DiskArray/…/A_B_…_001","suffix":"260318","suffixFrom":"task",
 "div":4,
 "items":[
   {"kind":"input","id":"…","name":"PAN.tif","rel":"…/PAN.tif",
    "exists":true,"sizeBytes":123456789,"mtime":1758171234.0,
    "W":24000,"H":24000,"hasPreview":false,"previewDiv":null,"jpgUrl":null},
   {"kind":"product","…":"…"},
   {"kind":"nosr","…":"…"}],
 "productCandidates":["A_B_…_001_260318.tif","A_B_…_001_260318.tiff"],
 "nosrCandidates":["A_B_…_001_NOSR.tif","A_B_…_001_NOSR.tiff",
                   "A_B_…_001_260318_NOSR.tif","A_B_…_001_260318_NOSR.tiff"]}
```

- `kind ∈ {"input","product","nosr"}`，**顺序固定**（输入、本轮超分产物、NOSR）。
- `id` 是**该图自己的场景 id**，可以直接拿去调 `GET /api/scenes/{id}/preview?div=N` ——
  三类图各有自己的 `<源 stem>_preview.jpg` 落点（各由自己的源拼），天然不撞名，所以**这个端点不新增任何烘焙入口**。
  落在 `SR_SCENES_ROOT` 之下时 `id` 走库内编码（base64url 相对路径）、`rel` 有值；
  之外走手工行编码（`~` + base64url 绝对路径）、`rel` 为 `null`。
- `exists: false` 的项**照样返回**（名字、id、`productCandidates` 都有）。**「找不到」本身就是
  回答**，前端不必再问一次；`W/H/sizeBytes/mtime` 与 `hasPreview/previewDiv/jpgUrl` 在
  `exists: false` 时一律 `null`/`false`。尺寸探测走 `_cached_dims`、档位走
  `_cached_preview_div`（读 JPEG 注释戳），与 §3.5/§3.6 同一套缓存。
- `productCandidates` = `scene_search.product_candidates()` 拼出的**全部候选名**，用于说明
  「试过哪些」。拼法必须字面切片 `输入名[:-4]`，**不能用 `Path.with_suffix`** —— SR 侧
  `variants/verify_sr_run.py::output_path_for` 用的就是 `img_name[:-4]`，输入名是 `.tiff`
  时两者不等。
- `nosrCandidates` = `scene_search.nosr_candidates()` 拼出的**全部候选名**，**顺序即优先级**
  （2026-09-24 用户口径）：先试 `<输入 stem>_NOSR.tif/.tiff`（SC 场景即 `<目录名>_NOSR.tif`，
  RC 场景即 `PAN_NOSR.tif`）—— 用户口径里「未超分那份」就是它；最后才试
  `<产物 stem>_NOSR.tif/.tiff`（`SR_code/util.py::writeTiff` 的改名规则推出来的**上一次产物**，
  只在同一 suffix 跑过两次以上时才存在）。两者同时存在时先认输入 stem 那条，命中哪个由该项的
  `name` 说明。**`nosr` 那一项与 `suffix` 无关**（名字由输入影像的 stem 拼），所以拼不出
  suffix 时它照旧出现，只有 `product` 那一类消失。**真机上实际命中哪个名字尚无实证** ——
  候选两条都试、命中即如实回报，拿到真机 `ls -l` 后按这份清单校准**顺序**即可，不影响能否命中。
  两义都标 `NOSR` 标，见 [preview-bake-pipeline §4.11](../knowledge/preview-bake-pipeline.md)。
- `div` = 服务端**急烤**档位（§4.5），**仅供界面标注**，不参与任何前端决策 —— 前端认的
  是用户滑块那个档位，服务端看不见它（这正是必须有服务端默认档的原因）。
- **拼完的名字再过一道 `pathguard.ensure_allowed`**：`SUFFIX_RE` 挡得住分隔符，挡不住
  「全是合法字符、却拼到白名单外」。这一道不过 → **403**（detail 说清是哪个前缀）。
- 场景不可访问（`PathDeniedError`）→ **404**，与 §3.5/§3.6 同款。
- **回归钉子**：`.e2e/` 与 `backend/tests/test_scene_siblings.py` 跑完要断言目录里
  **没多出** `_preview.jpg`、`stat` 次数没涨 —— 这个端点一个字都不许写盘。

#### 3.8.1 前端怎么用它（2026-09-20 接上）

客户端在 `frontend/src/lib/api.ts`：`apiSceneSiblings(cfg, sceneId, suffix?)`，URL 由
`lib/scene.ts::sceneSiblingsUrl` 拼。**它自己不取图**：拿到 item 之后走
`GET /api/scenes/{item.id}/preview?div=N`（§3.5）—— 三类图各有自己的 id，所以
**这次接入没有新增任何取图路径，也没有新增任何烘焙入口**。

- `siblingRow(res, item)` 把一类图装成 `fetchSceneJpg` 认的**库行**。它只读
  `id / name / W / H / hasPreview / previewDiv / jpgUrl / rasterPreview` 八个字段，
  所以其余字段（`satellite/sensor/date/size_bytes/fake/lq_path`）是**为了满足类型**而补的，
  在这条路上不参与任何判断。**这条是契约**：`fetchSceneJpg` 将来若要读新字段，
  要么这里一起补，要么显式声明「库外的行没有这个字段」。
- `rasterPreview` 恒 `null`：那是「源是显示件 jpg 且同目录配着同名栅格」才有的东西
  （§4.6），三类图都不适用；不看它就走「按自己的 id 烤自己那份预览」的正路。
- `jpgUrl` 为空（场景不在 `SR_SCENES_ROOT` 之下、取不到静态 URL）时，`fetchSceneJpg`
  走「响应体本身就是 JPEG」那条支 —— 与手工场景同一条路，不需要额外分支。
- **`exists:false` 与 `productCandidates` 给 UI 的用法**：点某一类图时若
  `!item.id || !item.exists`，界面**不打一次「没有」，而是把试过哪些名字一并说出来**
  （`productCandidates.join(' / ')`）；`suffix` 为 `null` 时换成「这个场景还没有可用的
  suffix，拼不出产物名」——那是另一种「没有」，原因不同就得说不同的话。
- **打开成功但 `suffixFrom !== 'query'` 时要出提示**：`task` / `default` 两级的名字与
  用户断言的看起来一样，不提示就分不清「真跑过的」与「按配置猜的」。提示文案里带上
  `suffix` 与来源级别。
- **`W`/`H` 为 `null` 不许开**（`exists:true` 也可能读不出尺寸）：掩码换算按 `rec.W/H`
  建画布，0 会让落点全错，所以这一条是**硬门**，不是提示。
- 打开时 `meta = {name: stem, W, H, sceneId: item.id, lqPath: res.lqPath}`，
  `name` 取**去掉扩展名的 stem**（与场景库那些行一个口径）。`lqPath` 用**场景目录**
  （不是那一类图自己的路径）—— 产物的 rec 因此也拿到盘阵关联，掩码写回才有落点。
- **`div` 字段前端不看**：它是服务端急烤的档位，仅供标注；前端认的是用户滑块那个档位。

#### 3.8.2 前端侧的取图纪律：blob 缓存、去重与预取边界（2026-09-20）

这一节写的全是**客户端行为**（`lib/api.ts` / `lib/blobCache.ts` / `stores/viewer.ts`），
服务端那两条端点一个字节都没改。列出来是因为它们决定了「什么样的请求会打到服务端」，
而这正是运维在盘阵上看得见的东西。

- **预览 blob 本地缓存**：模块级单例，**按字节封顶的 LRU**（`PREVIEW_BLOB_CACHE_MAX = 128MB`）。
  上限按字节而不是条数，因为条数在大图上完全不代表内存（÷2 档一条几 MB 到几十 MB）。
  缓存的是**压缩态 blob**、不是解码后的位图：rec 的像素本来就常驻（`thumb` 画布 4 B/px +
  `src` Float32Array 4 B/px，尺寸是服务端 jpg 的原生尺寸、不封顶），再缓存位图只会翻倍，
  而收益只覆盖「关掉再打开」这一种情形。**单条就超过上限的不进缓存**（放了也会立刻被自己
  挤出去）。暴露 `previewCacheStats()` / `clearPreviewCache()`（设置浮层那行读数与「清空」）
  与 `watchPreviewCache(cb)`（内容一变就通知，返回退订）——设置浮层那行读数必须是**实时**的，
  理由见本文末那条订正（2026-09-22）。
- **缓存键**：`previewCacheKey(row, div[, raster])` = `${id}|${div}|jpg` 或
  `${id}|${div}|ras:${栅格名}`。**必须把「取的是哪一份」编进键**：同一条行 id + 同档位，
  在「同名栅格胜出」（§4.6）时端上来的是栅格那份、另一张图的字节流，与源 jpg 那份不是
  同一串字节。`ras:jpg` 与前缀 `jpg` 因此刻意不同名。
- **命中缓存时照做网络路径的两行副作用**（`row.hasPreview = true`；有 `jpgUrl` 时
  `row.previewDiv = div`）：`previewNeedsBake` 靠它们判断，少写就会出现「盘上明明有这一档
  的预览，却每次都判成要重烤」。命中时**不调 `onPhase`** —— 本来就没有烘焙，不该弹
  「首次打开正在烘焙」那句。
- **芯片入口去重**（`openSceneSibling`）：先拿 `/siblings`（必须问，才知道对应哪条 rec），
  目标项若已有 rec 开着（判据与 `openSceneJpg` 的 `findRecByMeta` 同源：认 `sceneId`）
  就直接切过去，**不发 `/preview`**。于是「同一枚芯片连点两次」= 一次 `/siblings` +
  零次 `/preview`；`test-manual-scene.js` K 段钉着这个增量。
  **芯片打开也要带环节**（2026-09-21）：产物项用 `item.kind` 当 `stageKind`、响应顶层的
  `suffix` 当后缀（不额外加字段，见挂起项五）。不这么做的话，同一份产物会变成「拖进来
  不能改、芯片打开能改」两个说法 —— 前后端各一道锁的前提是同一份图在哪条路口走进来都
  被认成同一类。
- **对比模式后台预取**：用户开关（**默认关**，持久化在 `localStorage['sr.viewer.cmpPrefetch']`），
  进对比模式（或对比模式内换了活动侧那张图）时触发，顺序取、并发 1、失败静默、不占遮罩、
  不建 rec、不进点选清单。**合格项的判据是 `exists && id && name && W/H 非空 &&
  hasPreview && previewDiv === div` 且没有已开 rec** —— 这条判据本身就是「服务端已有一份
  现成预览」的定义，于是**结构性地保证预取永不触发烘焙**（会重烤的那几类留给用户点击时再烤）。
  真机上不允许出现「我什么都没点，盘阵却在读大图」；`test-manual-scene.js` K2b/K2c 用
  同一个场景把这条边界的两侧各钉一次（盘上没有现成预览 → `/preview` 增量 0；补一份现成的
  → 恰好 +1 且就是那一份的 URL）。
- **预取要把结果说出来**（2026-09-22 订正）：设置浮层里那颗开关下面多一行回执，
  四种结局各一句话 —— `siblings` 没查到、没有合格项（「另两类在盘上还没有现成预览」）、
  正在取第 i/N 项、取完（「已预取 N 项」，有没取到的记 `N/总数`）。理由：合格项是
  「盘上已有一份现成预览」，**第一次打开某个场景时这个集合本来就是空的**，缓存行会如实
  停在原处；不写出来，用户分不清「没东西可预取」与「预取坏了」（这正是那天用户报的那一条）。
  同一颗开关关掉时 `stopPrefetch()` 把回执擦掉。
- **离开对比模式即作废**：`stopPrefetch()` 清掉去重表、擦掉回执行并推进代数计数
  （`prefetchGen`），在飞的预取发现代数变了就不再开始下一项。**不用 `AbortController`**：
  取图那两个 API 不收 `AbortSignal`（要兼容静态 URL 那条支路），掐不断在飞的那个请求。
- **缓存读数必须是实时的**（2026-09-22 订正，与上面那条同源）：设置浮层那行
  「本地预览缓存 N 项 / X MB」原来只在**打开浮层时**读一次快照。可改这份缓存的按钮
  （预取开关）就在那一行上面：用户点开开关、盯着紧挨着的数字，数字永远停在打开时那一次
  （走拖入链进来时那份缓存本来就是空的 → **永远是「0 项」**），只能得出「预取没生效」——
  用户 2026-09-22 报的就是这一条。现在缓存内容一变就通知（`watchPreviewCache`），
  浮层在一开一关之外**跟着涨**。做法上刻意没有把 `stats()` 挂成响应式：那样每次取图都要
  重算一次渲染；改成「内容变了才响一次」的回调，事件数与缓存的实际出入同阶。
  开发机上已实测复现并验证：面板开着不动，预取落地那一刻行里的项数 +1，
  且与 `previewCacheStats()` 的真值逐字一致（`test-manual-scene.js` K2b/K2c）。

### 3.9 人工清除预览缓存（`POST /api/scenes/clear-preview`，2026-09-22）

场景库表格卡片头部那条工具行（「清除选定」/「全部清除」）的服务端一半。**这是本契约里
唯一一条会删除盘阵上文件的端点**，判据与回报都按「宁可不删，也不删错」写，机制细节见
[preview-bake-pipeline.md](../knowledge/preview-bake-pipeline.md) §4.12。

```http
POST /api/scenes/clear-preview
{ "ids": ["<scene id>", "…"] }        // 库行 id 与 `~` 开头的手工行 id 都吃；≤ 500 条
→ 200 {"results":[{id, status, reason, dir, removed:[{name,dir}],
                   skipped:[{name,reason,dir}], failed:[{name,reason,dir}], marked}],
       "summary":{cleared, nothing, skipped, failed, dirs, files, marked}}
```

- **`status`** 四值：`cleared`（删到了东西；**同目录里还有同名件没被认成缓存时另带 `reason`**）/
  `nothing`（盘上本来就没有这一景的缓存，**且一个同名件都没有**）/
  `skipped`（**整个目录一个文件都没删**：id 不可访问 —— 白名单外 / 文件不存在；该景**此刻**有任务
  `preview_state='running'`；或目录里只有同名外来件。见下）/
  `failed`（该删但删不掉，逐条给原因）。**逐条回报、逐条尽力**：一个目录删不掉不影响其他目录，
  一条坏 id 也不影响其他 id（坏 id 不致 5xx）。
- **`nothing` 与 `skipped` 不能混**（前端的行去留按结论分：`cleared`/`nothing` 摘行，`skipped`/`failed`
  留行）。目录里躺着 `<stem>_preview.jpg` 但不是我们的缓存（没规则戳 / 是场景源）时报 `nothing`
  是假话，还会把行从列表里摘掉 —— 盘上那份文件明明还在，重新检索又出现「已生成」，看起来像
  清除没生效。所以只有「一个同名件都没有」才叫 `nothing`。
- **按目录去重**：同一景的两行（`<目录名>.tif` 与 `PAN.tif`）共用一个目录，只清一次；
  `summary.dirs` 是去重后的目录数，`summary.files` 是真正删掉的文件数。
- **删什么**：该场景目录（配了 `SR_PREVIEWS_ROOT` 时含镜像树里对应的那一层）里
  `<stem>_preview.jpg` / `<stem>.preview.jpg`，且 **JPEG 注释带 `srprev:` 规则戳**、
  不是场景源（`is_scene_file`）、不是符号链接。**不递归、不扫根、不删空目录。**
  场景源 `.tif` / `<编号>_mask.tif` / 源即显示件那份 `.jpg` 一律不碰。
- **库里怎么记**：该目录所有 `COMPLETED` 任务行写 `preview_state='cleared'`
  （CAS，`preview_state IS NULL OR != 'running'`），挡住急烤的后续认领；`running` 的那一行
  不覆盖。**不自动重烤** —— 下次打开走惰性路径重烤。
- **`ids` 非空数组且 ≤ 500**（超出 400，不静默截断）：一次动几十上百个生产目录的请求，
  宁可让调用方分批，也不替它决定砍掉哪一半。
- 删除动作在客户端也有对应记账：命中的 scene id 前缀丢本地 blob 缓存，且**本次真的重烤过**
  时取静态图带 `cache:'no-store'` 绕开浏览器 HTTP 缓存（§3.8.2 那条 `?div=N` 只击穿换档位，
  击穿不了同档位重烤）。

## 4. 关键实现机制（契约约束）

### 4.1 会话串行 + 线程桥
- 进程内 `dict[session_id → asyncio.Lock]`；锁占用期间再 POST → 409。
- `run_loop` 是同步阻塞函数 → `asyncio.to_thread` 跑；内部事件经回调推 `asyncio.Queue`，主协程 `while` 逐帧 `yield` 成 SSE（`StreamingResponse`）。

### 4.2 loop 观察缝（新增 `on_step`，**不改变行为**）
`run_loop(…, on_step: Callable[[dict], None] | None = None)`：在既有各 return/commit 点**同步调用** `on_step({"type":…, …})`，事件与 SSE schema 对齐（turn_start 在循环首轮前、tool_call/tool_result 在 call_tool 前后、assistant 在纯文本答复时、turn_done/error 在返回前）。默认 `None` → 与现状逐字节一致，**18 个既有 loop 测试不因本改动变更**。mock LLM 只替换 `_default_chat`，不动状态机。

### 4.3 队列后台校准广播
- app lifespan 启动一个 asyncio 任务：`while` sleep `SR_QUEUE_POLL_SEC`，扫 `sr_tasks` 中 `job_id IS NOT NULL` 的行 → `slurm.job_status` → 状态变化时更新 `sr_tasks.status` + 内存缓存 + 广播。
- 广播 = 进程内 `set[asyncio.Queue]`（每 `/api/queue/events` 连接一个）；写入失败（连接断开）→ 丢弃该连接。**单进程部署**（uvicorn 单 worker），多 worker 需外部队列——文档注明（M2 再议）。

### 4.4 测试隔离
- 测试里 `create_app()` 前设 env（`SR_AGENT_DB=临时文件`、`SR_SCENES_ROOT=临时目录`、`SR_LLM_MOCK=1`、`SR_SLURM_FAKE=1`），与阶段4 test_api.py 同款模式。

### 4.5 产物预览急烤队列（2026-09-20）

用途：作业转 COMPLETED 之后，**产物那一份**的预览由服务端顺手烤掉 —— 用户跑完立刻打开
时不必等一次几十秒的读盘 + 采样，同档位下直接命中盘上那份。

**只烤产物**，输入影像与 `_NOSR` 两份不烤：那两份「用户到底要不要看」在打开之前无法知道
（对比 UI 还没做），而产物是刚跑完的、几乎一定会被打开。三类落点天然独立，各烤各的。

**关键结构选择：不挂在状态转换上，改成从库派生。** `platform._task_state` 是唯二写终态的
地方，但它的调用者有两个 —— 后台 `_poll_once` 与**请求路径** `_task_view`（`GET /api/queue`）。
谁先观测到 RUNNING→COMPLETED 谁把 `changed` 拿走，另一个看到的是「没变化」，在那里挂入队
钩子必然偶发漏烤；而它又有请求路径调用者，也不能在那里做重活。改成「`sr_tasks` 里有
COMPLETED 且 `preview_state IS NULL` 的行」之后，这个竞态**在结构上不存在**了。

- 后台循环 `_eager_preview_loop(state)`（`app.py`，加入 `lifespan` 的 `app.state.tasks`，
  与 `_poll_loop`、临时预览清扫同列）每轮 `await asyncio.to_thread(_eager_bake_tick, state)`；
  **第一件事是 sleep**，不与启动抢盘；单轮异常吞掉、不 kill 循环（与 `_poll_loop` 同形）。
- `_eager_bake_tick` 每轮**至多烤一件**（单消费者、并发 1）：
  1. `div = _product_preview_div()`，为 0 直接返回。**每次 tick 重读 env**，不在 `create_app`
     里快照（测试要能按用例改档位，真机上关/开也不必重启）。
  2. `store.list_preview_candidates(max_age_sec, limit=20)`。
  3. 逐行 `store.claim_preview_bake(task_id)`；**抢到第一条就只烤这一条**然后返回。
  4. 对抢到的那条，按下面状态机走到哪个结局就是哪个。
- 为什么每轮只一件：÷4 烤一份 40000² 产物的峰值内存约 400MB、要把整个文件读一遍，
  并发会把内存乘上去、把盘阵带宽占满。代价只是「积压时靠后那几件晚几分钟烤好」，
  而它们本来就是等用户打开的。每轮**扫描上限** 20 是 `app.py` 模块常量，不开 env
  （它只影响积压时每轮多花几次 SQL，配错没有意义）。
- **不占任何全局 semaphore**：急烤与惰性路径不共享锁。同档位时急烤的 `cache_hit` 通常
  命中用户刚烤的那份，不同档位就两份都留（那就是 §六.5 点明的 div 抖动，今天已有）。

状态机（`preview_state` / `preview_note` 两列，`GET /api/queue` 每行带出，见 §3.3）：

```
NULL ──claim──> running ──> done
                     ├──> skipped（note = "<slug>: <人话>"）
                     └──> failed （note = "failed: <异常原文>"）
```

| slug | 触发条件 |
|---|---|
| `no_suffix` | 任务行的 `params` 里没有 `lq_path`/`suffix`，拼不出产物名 |
| `sandbox` | 这次跑在沙箱私有副本上（产物不在盘阵），或沙箱根配置不可用以致无法确定落在哪 |
| `product_missing` | 输入影像找不到，或产物候选名**逐个 `is_file()` 都不在** |
| `unwritable` | 产物所在目录对服务账号不可写 |
| `source_changed` | 像素已读出、落盘之前复核发现产物被改写 → **一个字节都不写** |
| `failed` | `PreviewError` / `OSError` / 其他异常原文（后台循环不被一行拖死） |

- **沙箱判据必须是 `_run_dataroot(task)`，不能直接比 `SR_SANDBOX_ROOT`**：`_run_dataroot`
  内部走 `run_sr.sandbox_scene_paths`，而那条在 `SR_EXECUTOR=local` 时恒返回 `None` ——
  真机当前正是「配了 `SR_SANDBOX_ROOT` + local executor」这条路线，直接比 env 会把本可以
  烤的产物判成「沙箱内」而永不烤。反过来真在沙箱里跑时，产物落在私有副本上，烤了用户
  也看不到，还往临时盘撒文件。这条有专门的回归钉子（`SR_SANDBOX_ROOT` 在 + `SR_EXECUTOR=local`
  → **照烤**）。
- `product_missing` 的 note **必须列出试过的候选名**：COMPLETED 但没有产物是**正常结局**
  （云限额的 `Run skipped:` 就判成合法 COMPLETED），运维看到「跳过」得能立刻分辨是
  「名字猜错了」还是「作业本身没产出」。
- **目录不可写就不退回临时缓存**，如实跳过：那份按天清，而急烤的意义是长期命中；更要命的
  是急烤**没有 HTTP 响应头**能告诉用户「这次退化了」，静默退化等于骗人。兜底留给打开时那条
  `X-SR-Preview-Fallback` 路径。
- **只烤 COMPLETED，FAILED 绝不烤**：`writeTiff` 先改名再写，失败的运行会在产物路径上留下
  半截文件，烤出来是坏图。这一条同时写进了候选查询与 `claim` 的 `WHERE`。
- **`claim` 是并发与重复认领的唯一裁决点**：CAS
  `UPDATE … SET preview_state='running', preview_note=NULL WHERE id=? AND preview_state IS NULL
  AND status='COMPLETED'`，只有 `rowcount == 1` 才算抢到。`status='COMPLETED'` 一并进 `WHERE`
  是为了挡住「认领与重跑赛跑」：用户在这一行刚跑完、急烤还没动手时又交了同一个 suffix，
  `put_sr_task` 会把 status 打回 `submitted` 并清 `preview_state`，此时这份认领必须失效。
- **`put_sr_task` 的 UPDATE 分支清 `preview_state`/`preview_note`**（与清 `started_at`/
  `finished_at` 同一处理）：同一 suffix 重跑必须**重新武装**急烤，否则第二次跑完永远停在
  上一次的 `done`。
- **`set_preview_state` 不碰 `updated_at`**：那个列是「这行最近一次写回」的时刻，队列按它
  排序、界面上有读数；预览烤没烤成与作业本身无关，抬它会让人以为作业动了。
- **广播走新帧类型 `preview_update`**，不混进 `job_update` —— 后者是「作业状态变了」，
  混在一起前端收到就得重取整行。前端收到 `task_id` 对不上的帧**必须原样不动**（见 §3.3）。
- **落盘前复核 `(mtime, size)`**：同一 suffix 重跑会覆盖同一个产物路径。不复核的话，一次
  「读的时候是旧产物、写的时候新产物已经在写」会把一张半截图永久留在盘上，而缓存判据是
  「不比源旧」—— 新的 mtime 可能仍晚于我们刚写的 jpg，它**不会自愈**。为此
  `preview_jpg.py` 把 `cache_hit`（原 `_cache_hit`）与 `write_preview_jpg` 从 `ensure_preview_jpg` 里抽了出来
  （**纯重构**：缓存规则、`rule_stamp()` 一字未动，现有 `test_preview_jpg.py` 是这次的
  回归钉子）。
- **迁移不回填**：`preview_state`/`preview_note` 走既有 `_ensure_columns`（PRAGMA → ALTER
  TABLE，幂等），与 `started_at`/`finished_at` 同款，**故意不给老行补值**。加上
  `finished_at >= now - SR_PRODUCT_PREVIEW_MAX_AGE_SEC` 这道年龄窗口，升级当天不会把历史
  COMPLETED 行全烤一遍 —— 这是唯一的屏障。
- 两个 env 见 §1：`SR_PRODUCT_PREVIEW_DIV`（缺省 4、**0 = 关**、非法值当 0 且不抛异常）与
  `SR_PRODUCT_PREVIEW_MAX_AGE_SEC`（缺省 86400）。急烤的 4 与前端 `DEFAULT_PREVIEW_DIV = 4`
  是**两个独立的 4，互不联动**。

**顺带烤未超分那一份（2026-09-21 增 / 名字口径 2026-09-24 订正，用户口径，不算新的烘焙入口）**：
同一轮 tick 在产物之后多烤一份 `<场景目录>/<那一份栅格 stem>_preview.jpg`（源 = 候选清单里
第一个存在的 `…_NOSR.tif`，档位取同一个全局值，命中判定同一个 `cache_hit`）。
三处与产物那一份**故意不同**：源是一份**候选清单**（`scene_search.nosr_candidates`，只拼名字）；
**不判沙箱**（这份栅格是盘阵上的既有文件，与这次跑在盘阵还是私有副本上无关）；
**不动 `preview_state`/`preview_note`**（那一列描述的是产物预览，一个字段说不出两份文件的
结局），只写盘 + 往 stdout 打一行 `[nosr-preview] task=<id> <状态>`，**`skipped:` 要报出试过哪些
名字**（一个名字都不在盘上时，只有把清单打出来才看得出是名字不对还是那份本就不存在）。
候选清单与次序见 §3.8 的 `nosrCandidates`（2026-09-24 前这里是硬编码的 RC 名字
`PAN_NOSR.tif`，SC 场景因此永远烤不出来）。**没有任何响应字段为它变化**：`/siblings` 的
「NOSR」那一项找的仍是同一份 `<栅格 stem>_preview.jpg` 落点（2026-09-22 起与其余落点同一规则）。

前端**拖入显示件**时也会对同一份做一次静默预热（2026-09-24 增，`stores/viewer.ts::warmNosrPreview`，
命中才记账），见 [preview-bake-pipeline §4.11](../knowledge/preview-bake-pipeline.md)。

**预览文件名统一（2026-09-22，用户口径）**：三条烘焙链（惰性打开 / 急烤 / 拖入）落同一个名字
`<源栅格 stem>_preview.jpg`（`paths.preview_jpg_name`），改名前的点号那份
（`<stem>.preview.jpg`）不再由任何链产出。改名不是改个字符串：读判据跟着走才是同一件事 ——
服务端的 `hasPreview`/`previewDiv`/静态 `jpgUrl` 都从同一份落点算（自动一致），前端
`isBakedPreviewUrl` 认结尾 `[_\.]preview\.jpe?g`（点号那代一并认下：静态 URL 是后端给的，
版本错开一档时认得出比认不出安全），决定拼不拼 `?div=` 与换档后要不要重烤。旧文件由
`_sweep_legacy_preview` 在每条链处理到那份栅格时顺手删掉（烤之前一次、命中缓存一次），
失败不报错；**没有全盘清扫**（平台不列目录），没被任何链碰过的目录里那份会留着。

### 4.6 显示源比较规则：谁清晰用谁（2026-09-20）

背景：拖进查看器的 `.jpg`（真机上盘阵场景目录里那份预生成的显示件，如 `PAN.jpg`，长边约
8192）有时**不够清** —— 同目录配着的栅格（`PAN.tif`）比它大得多。用户拍板：**取两者中更
清晰的那张**，没有配套栅格就**回退显示件本身**。

唯一口径（前端 `scene.ts::rasterPreviewWins` 与文档、测试共用这一条）：

```
同目录存在同名 .tif/.tiff/.img
  且 round(max(rasterW, rasterH) / div) > max(jpgW, jpgH)
      → 显示源改用服务端从栅格烤出来的那份（落点与栅格行同一份 `<stem>_preview.jpg`）
  否则 → 保持显示源 jpg（现状）
不存在同名栅格 / 任一侧尺寸读不出 / div 不在 PREVIEW_DIVISORS 里
      → 保持显示源 jpg（保守）
```

- **严格大于**：相等不算赢。服务端烤一份要读整幅栅格，像素数不比现状多就没理由付这个代价。
- **比的是盘阵那份 jpg 的尺寸**，不是用户拖进来那份本地文件的尺寸：平台口径是「盘阵上的
  才是基准」，用户本地那份可能另存过（测试夹具就故意让两份尺寸不同，好分清像素来源）。
- **预期要如实告知：默认档位 ÷4 下这条规则基本不触发。** 24000 源 + 8192 显示件时，
  ÷2 → 12000 赢、÷4 → 6000 输、÷8 → 3000 输。所以验收口径**不能**写「jpg 行一律走服务端」，
  只能写这条比较规则本身，并且**现有 e2e 夹具在 ÷4 下全部判 jpg 赢、断言一字不改地继续绿**
  —— 那就是这次改动的回归钉子。
- 后端 `_raster_preview(abs_path, root)` 产出的 `rasterPreview` 对象挂在 jpg 行上
  （见 §3.5）：`{rel, id, name, rasterW, rasterH, jpgW, jpgH, hasPreview, previewDiv, jpgUrl}`。
  两侧尺寸都走现有的 `_cached_dims` 缓存（不新开探测）；`jpgUrl` **只在栅格落在
  `SR_SCENES_ROOT` 之下才给**（库外没有静态 URL，与今天库外行完全一致）。
- **`row.hasPreview` / `row.jpgUrl` / `row.previewDiv` 的语义一个字不动**：那三个字段锚在
  **显示件 jpg 自己**身上（`gui-experience.md` §9.2 的红线）。栅格的状态单独放在
  `rasterPreview` 里。前端的「重烤再取图」只写 `row.rasterPreview.hasPreview/.previewDiv`。
- 前端判定所需的两个尺寸**全在 resolve 响应里**，不需要第二次往返。`previewNeedsBake`
  里前缀一个栅格分支（判据与栅格行相同：`!rp.hasPreview || rp.previewDiv !== div`），
  于是 `ScenesPage.vue` 的按钮文案、`fetchSceneJpg` 的预判、`openScenePath` 三处自动同步。
- **`?div=` 抖动**（明知故犯，§六.5）：栅格行与 jpg 行走的是**同一份落点**
  （`preview_jpg_name` 只由源 stem 拼，对 jpg 与 tif 是同一个文件名），所以两个档位的客户端会
  互相顶掉同一份文件 —— 这个病今天就在，工作流 B 只是把它拖进更多行。本轮在文档里点明，
  不装作没有。

### 4.7 拖放门的模式差异（图像对比，2026-09-20）

拖放（把图从文件管理器拖进查看器）不是端点，但**它的门开在哪儿是与模式有关的契约**，
而且只在前端，后端看不见 —— 所以写在这里，免得改的时候只看后端。

| 阶段 | 关闭模式（默认） | 对比模式（点选 / 分屏） |
|---|---|---|
| `dragover` | `preventDefault()` + `dropEffect='copy'` | 同左，**一字不差** |
| 落位提示 | 无 | 画布内：分屏时按落点指左/右格，点选时整块画布；画布外：不提示 |
| `drop` 落点 | 全窗口都收（侧栏、工具栏、画布都行） | **只在画布矩形内收**，画布外影像一律不收 |
| 影像落在哪 | 顶掉当前这张（单幅） | 分屏：落点那一半；点选：顶掉当前这张 |
| `*.txt` | 喂《待修复清单》 | **同左，任何模式、任何位置都喂** |

三条实现约束，都是踩过或差点踩到的：

- **`dropEffect` 任何模式、任何位置都保持 `copy`。** 画布外卖成 `'none'` 能拿到系统的
  「禁止」光标，但按规范它同时会**抑制 `drop` 事件** —— 而 `.txt` 拖放必须在任何模式、
  任何位置都能进待修复清单。且 `dragover` 阶段 Chrome 不暴露文件名（只有 drop 阶段有），
  没法按文件类型区分。所以视觉提示只由 overlay 负责，真正的门在 `drop` 处理里。
- **落位提示是纯本地状态，一个请求都不发**：由 store 里一个 500ms 定时器收尾（每次
  `dragover` 续期），并在 `drop` / `dragend` / window `blur` 时立即清。e2e 用真
  DragEvent 单独发 `dragover` 断言过「请求数 0 条」（`test-manual-scene.js` J 段）——
  这也是为什么模拟器里 `dragover` 与 `drop` 必须是两个入口：提示在 `onDrop` 里被同步
  清掉，要观察它就只能在 drop 之前单独问一次。
- **`.txt` 先分流、且不分模式**：`.txt` 与影像在同一份 `dataTransfer.files` 里，必须
  **先**把 `.txt` 交给待修复清单，**再**判影像的落点门 —— 顺序反了，画布外拖一份
  `.txt` 会被那道门连坐吞掉。

- **关闭模式下全窗口拖放行为一个字不改**：这是既有能力（拖到侧栏也能打开），
  对比模式的门是**加法**，不是把门改窄。e2e 两侧都钉了（关闭模式拖侧栏仍打开文件）。
- **正好压在分隔线上算右格**（`paneAtX`: `localX < splitX ? 'A' : 'B'`）：与渲染侧的
  命中判定同一个口径，两处不一致会让「提示说右边、图却进了左边」。

## 5. 假实现规格（离机验收基准）

### 5.1 mock LLM（`SR_LLM_MOCK=1`）
`_default_chat` 返回假 `chat(messages, tools)`，**固定脚本**、不解析用户话术：
1. 首个回合：声明 tool_call `search_scenes`（无参数，走既有 fake/真实盘阵后端，返回真结果/假结果皆可）；
2. 收到工具结果后：回最终回复，内容 = 「已检索盘阵场景 N 个（mock 模型）。」+ 前几个场景 id 摘要。
确定性 → SSE 每回合固定产出 `turn_start/tool_call/tool_result/turn_done` 全套事件，e2e 可断言。**不替换状态机**（loop.py 行为由既有测试锁住）。

### 5.2 假调度器（`SR_SLURM_FAKE=1`）
`services/slurm.py` 顶层加一个假实现门：无 sbatch（或 env 强制）时 `sbatch_submit` 返回自增 job_id 并登记，`squeue_status`/`sacct_status` 按**可配延时推进状态**（`SR_SLURM_FAKE_T_MS`，缺省 ~1200ms 后 PENDING→RUNNING→COMPLETED，exit 0），`scancel` 置 CANCELLED/FAILED。`config.xml`/batch 脚本/sr_tasks 表照常落盘（校验路径、幂等、写表全走真实逻辑）——只替换"调度器"那一层。镜像阶段4 场景 fake 的既有风格（env 门 + 注释明示 fake）。

### 5.3 契约测试（本轮验收）
- 后端 pytest：chat 新建/历史/SSE 事件序列（mock LLM）/会话并发 409/不存在 404；tools manifest + 直调 run_sr（fake slurm）幂等；queue list/submit(SUBMITTED)/重复提交(RESUMED_*)/cancel/events 广播状态推进；masks 落原图目录 + 白名单越界 404 + fake 场景 404。
- 前端 Vitest：SSE 帧解析器；chat/queue store 的乐观更新与事件归并。
- `.e2e`：真 uvicorn（`SR_LLM_MOCK=1 SR_SLURM_FAKE=1` + 临时 db + 临时 scenes root）驱动前端——聊天发一条 → SSE 全事件 → 消息渲染；队列提交一张假任务 → events 推到 COMPLETED；查看器掩码提交按钮 → POST /api/masks → 表单预填。本地文件路径回归（route 非 jpg）零改动仍绿。
- **真机另排清单**（不阻塞离机）：真 LLM `SR_LLM_*` 指向内网端点、真 Slurm 提交/取消、真实盘阵掩码落点核对（ENVI 打开确认掩码与 0817 消费路径一致）。

## 6. 部署注意（nginx 反向代理 SSE）

- `/api/` 反代需对 SSE 端点禁用缓冲 + 放宽读超时：
  `location /api/ { proxy_pass …; proxy_buffering off; proxy_cache off; proxy_read_timeout 3600s; proxy_set_header Connection ''; }`（阶段4 nginx.conf 已 `proxy_read_timeout 600s`，需加 buffering off；仅对 `StreamingResponse` 生效，普通 JSON 不受影响）。
- systemd `sr-api.service` 增补 env：`SR_AGENT_DB=/DiskArray/…/sr_agent.db`、`SR_LLM_MOCK=0`、`SR_SLURM_FAKE=0`（真机显式关 fake，防误开）。
- `requirements-api.txt` 增补 `openai>=1.40,<2`（阶段5 起 API 进程直接 import loop → openai；**版本锁死**，aiohttp 3.8.3 不兼容 3.x）。

## 7. 变更流程（红线）

契约改动（新增/删端点、改请求/响应/SSE 事件字段、改状态机映射）→ **先改本文档、标注状态=评审，评审通过再改代码**。前端 SSE 渲染与后端推送共用本 schema，两侧各写一份 parser/emitter 的单元测试锁字段。
