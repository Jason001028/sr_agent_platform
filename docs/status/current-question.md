# current_question — 平台现状与交接入口

> 用途：交接文档。新会话先读本文件，再读 [gui-experience.md](../experience/gui-experience.md)（踩坑经验），
> 即可无断点接着干。
>
> 本文件只写**现在是什么**。变更经过见 [timeline-archive.md](timeline-archive.md)（历史时间线），
> 真机勾选项见 [real-machine-acceptance.md](real-machine-acceptance.md)（验收单）。
> 日期：2026-09-23 · 状态：已定。

---

## 1. 平台是什么，现在能做什么

遥感影像超分（SR）处理平台。浏览器查看盘阵上的大 TIF、绘制掩码、提交 SR 作业、跟踪共享队列。
前端 Vue3 + TypeScript（Vite 构建，Nginx 静态托管，第三方库全部本地内置）；
后端 FastAPI + SQLite；超分算法调用 SR 生产脚本，不重写。

四条链路：

- **`/viewer` 查看器** —— 两种数据源（本地 TIF 文件 / 盘阵场景 JPG）。功能：亮度拉伸、像素定位、
  ROI 统计、掩码绘制（矩形 / 多边形 / 魔棒 / 合并重叠 / 删除）、图像对比（点选 / 分屏）、
  《待修复清单》导入与写回、提交 SR。右侧「上下文侧舱」含 `[ROI/工具]` 与 `[Agent]` 两个页签。
- **`/scenes` 场景库** —— 盘阵场景检索（卫星 / 传感器 / 日期 / 关键词）、行内显示 W/H、
  打开（首次访问懒生成预览）、清除预览缓存（选定 / 全部）。
- **`/queue` 共享队列** —— 提交 SR、状态随 SSE 推进、取消作业、耗时与产物目录。
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

1. `<输入名>_<suffix>_NOSR.tif` 的拼法系从 `SR_code/util.py` 推导，无真机实证。
   缓解：候选名做成元组，`/api/scenes/{id}/siblings` 回报 `productCandidates`，拿到 `ls -l` 后校准。
2. 产物 tif 尺寸为输入影像的 **2 倍**（`code_0817_prod.py` 的 `scale: 2`），无真机实证。
   若成立，分屏对比两侧是「同一地面区域、不同像素网格」，对齐只能按百分比，±1 像素级比对做不到。
3. 生产树目录日期取「成像日 / 次日」中命中者，现按较年轻者推定，需一批真实目录名验证。
4. 服务账号 `User=nginx` 对盘阵场景目录有无 `unlink` 权限未验证 —— 场景库「清除缓存」依赖它，
   无权时该功能只逐条报失败，不删任何文件。
5. **2026-09-11 生产文件覆盖事故待善后**：`$BUNDLE/code_0817_prod.py` 被 28703 字节版本覆盖，
   旧版（33477 字节）无备份、不可恢复。待答：谁读这份文件？

### 3.3 已记录，暂不处理

1. `/preview` 在源文件不存在时回 422（而非 404），故「3 天以内的新景、文件已被清」这一路
   走不到「已自动清除」灰块。改造只需后端一行，但需重新部署。
2. 09-15 记录：退出码文件已判 FAILED，而 `GET /api/queue` 仍报 RUNNING。此后 `_task_state`
   经 09-18、09-20 两轮改动，**未复核该现象是否仍存在** —— 下次真机提交时顺带确认。
3. [.e2e/qa-theme.js](../../.e2e/qa-theme.js) 有 6 条陈旧失败断言（期望值是 2026-09-09 改版前的
   白色 chrome 与渐变主按钮）。已确认非当轮引入，未改动；改脚本期望值还是改主题需人定。
4. `test-platform.js` 的 `[D]`「耗时列」存在**间歇性**失败（09-22 两轮复跑，一次红一次绿，
    与当轮改动无关），未定位。
5. 预览内存不释放：每个文件的预览 `Float32Array` 常驻，各边 ÷4 约 145MB/张、÷2 约 600MB/张；
    分屏按设计同时保留两张，点选清单鼓励多开。当前只记录，无 LRU 释放。
6. 同一场景的 `<目录名>.jpg` 与 `<目录名>.tif` 两行预览相同（工作流 B 使两行落到同一份落点），
    且该落点会被不同档位的客户端互相顶掉（div 抖动）。去重与带档位落点留待对比相关的一轮一并定。
7. `SR_SCENES_ROOT` 是**单根**，没有多根写法；符号链接只有建在根本身才能通过路径校验。
8. 场景检索采用白名单：所在目录须含 `<目录名>_meta.xml`，且文件名为 `<目录名>.<ext>` 或 `PAN.<ext>`。
    **代价**：没有该 xml 的目录整个不显示（已接受）。

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

- **预览烘焙**：规则戳 `srprev:v3:div<N>+equal:q<Q>`；前端档位 各边 ÷2 · ÷4 · ÷8 · ÷16 · ÷32，
  默认 ÷4（`SR_PRODUCT_PREVIEW_DIV` 默认 4，`0` = 关闭）——**前端档位与该 env 是两个独立的 4，互不联动**。
  预览落点统一为 `<源 stem>_preview.jpg`。
- **掩码**：值域 0/255（0 = 不处理，255 = 处理）；`run_sr` 用 `cv2.threshold(>0)` 读取。
  矢量格式 `{"width":W,"height":H,"polygons":[{"label":"roi","points":[[x,y],...]}]}`，
  x 为列、y 为行，均为原图像素坐标。
- **SR 产物命名**：`<源 stem>_<suffix>.tif`；未超分那份为 `<源 stem>_<suffix>_NOSR.tif`（拼法待真机确认，见 §3.2）。
- **场景判据**：目录须含 `<目录名>_meta.xml`；场景文件为 `<目录名>.<ext>`（SC 步输入）或
  `PAN.<ext>`（RC 步输入）。派生件按 `p.stem == p.parent.name` 排除。
- **路径映射**：`SR_DRIVE_MAP` 默认 `W:=/DiskArray`；`SR_ALLOWED_ROOTS` 默认 `/DiskArray`。
- **「已自动清除」推定阈值**：`PURGED_AGE_DAYS = 3`（老景 + 盘上无任何预览 + 年龄 > 3 天）。
  该标记只活在前端单次会话，重新检索即消失。
- **队列耗时口径**：纯算力时长（`started_at` → `finished_at`），不含排队；重启后基准回落库中状态。
- **任务表唯一键**：`sr_tasks.task_fingerprint`（参数内容 sha256），同参数重交复用同一行。

### 6.3 测试基线（2026-09-22 实测）

- 后端：**696 passed / 5 skipped**（`python -m pytest backend/`）。
- 前端：**394 passed** + `vue-tsc` 零错误 + `npm run build`（Vitest）。
- 浏览器回归（`.e2e/`，先 `cd frontend && npm run build`）：
  `test-scenes.js` **130** 断言 · `test-manual-scene.js` **202** · `test-platform.js` **26** ·
  `test-vue-viewer.js` **190**（沿用当天更早记录）。
  更旧的值（58 / 65 / 22 / 197 等）已过期，不要据此判断回归。

## 7. 去哪查什么

| 查什么 | 去哪 |
|---|---|
| 某个决定 / 某次事故的经过 | [timeline-archive.md](timeline-archive.md) |
| 真机验收怎么勾 | [real-machine-acceptance.md](real-machine-acceptance.md) |
| 真机怎么部署、起不来怎么办 | [real-machine-bringup.md](real-machine-bringup.md) · [ADHD 动作版](real-machine-bringup-adhd.md) · [deploy/README.md](../../deploy/README.md) |
| 踩坑与硬约束 | [gui-experience.md](../experience/gui-experience.md) |
| 平台 API 契约 | [api-contract.md](../planning/api-contract.md) |
| 预览烘焙端到端链路 | [preview-bake-pipeline.md](../knowledge/preview-bake-pipeline.md) |
| SR 算法 / 调用契约 / 移植环境 | [docs/sr_code/](../sr_code/) |
| 生产场景命名与路径反推 | [production-scene-naming.md](../sr_code/production-scene-naming.md) |
| 系统理解前后端设计取舍 | [platform-tutorial.md](../knowledge/platform-tutorial.md) |
| 文档分类规则 | [docs/README.md](../README.md) |
| 环境与真机事实（跨会话记忆） | `~/.claude/projects/…/memory/MEMORY.md` |
