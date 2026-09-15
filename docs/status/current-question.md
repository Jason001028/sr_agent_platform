# current_question — 当前问题解决时间线（显示遥感图像）

> 用途：交接文档。下一个 Claude 会话先读本文件，再读 `docs/experience/gui-experience.md`（踩坑经验），即可无缝接着干。

---

## 1. 当前状态（摘要）

- **查看器已可用（08-29）**：`tif_viewer/tif-viewer.html` 能正常打开、预览本地遥感图像，用户实测等待时间可以接受。
- **真实文件结构已确认**：GF07A03（1.11GB）与 KF02B04（1.78GB）都是**无压缩、每行一个条带（strip）**的单波段 16bit 影像。因为没压缩、条带又很规整，就不用做金字塔（预先生成多级缩小图来加速浏览），直接采用**稀疏条带预览**（只抽样读取部分行和列，秒级出图；134MB 测试图从整图解码 4.5s 降到 1.3s，预计真实 1.1GB 文件从约 2 分钟降到秒级）。
- **预览方案拍板（09-01）= JPG 中间产物**：网页/预览显示导出的 JPG（长边 8192 像素 ≈ 原图 1/3），放弃了在浏览器内对原始 TIF 做全分辨率切片读取的做法（决策记录见 §3.4）。
- **掩码绘制已实现（08-31）**：tif_viewer「绘制掩码」模式（矩形/多边形/魔棒三种工具）；浏览器**直接生成** `掩码.tif`（像素值只有 0 和 255，0=不处理、255=要处理）+ `掩膜中心点坐标.txt`，也可以导出矢量 JSON 交给 `python -m backend.services.mask` 处理后端生成（详见 §3.3）。
- **魔棒/合并/删除打磨（09-01）**：魔棒改成**自适应区域生长**（修复了「总是选出一块默认大小的椭圆」的问题）；新增「合并重叠」按钮（把重叠或相互接触的区域并成一个连通区域）与「删除」工具（点中后区域柔和红闪一下再移除）。详见 §3.3。
- **SR 部署方案拍板（08-31）**：目标机器就是那台 CentOS7 盘阵机；生产环境跑 `realesrgan_trt` 这套超分流程；继续保留 Slurm；SR 采用**裸机 + Slurm** 的方式部署（TRT 版本锁定，不加 Docker 容器）；程序包（bundle）/模型权重/编译好的库文件（.so）/TensorRT 引擎文件全都放在 `/DiskArray`（Linux 下读写明显更快），打包时直接挂载复用、代码路径零改动（详见 §3.2）。
- **Agent 最小原型 M1（08-31 晚）**：`backend/agent/loop.py` 自己写的运行循环 + 第一个真实工具 `fix_bad_lines`（修复坏行/条带）；**M1 待完善的 P0①②③ 已补齐（09-01）**——SQLite 会话持久化、run_sr 防重复提交保护、假路径拦截，**114 个测试全部通过**（详见 §2.5）。
- **阶段3 查看器 UI 组件化完成（09-01）**：把 tif-viewer.html 的交互层**移植而不是重写**到 Vue3——拆成 TifCanvas/Toolbar/FileList/DrawPanel/DecodeOverlay/StatusBar 六个组件 + `stores/viewer.ts` 统一调度 + `lib/{viewMath,browserKit,decode,exportJpg,saver}` 工具模块 + `window.__viewer` 调试钩子；核心算法逐字节保留，只把交互方式改成 Vue3 的惯用写法；**BigTIFF 读取方案修复**（本地内置的 UTIF 库解不了 BigTIFF，强制改走 geotiff.js 分块读取，这是本阶段唯一的行为差异）。75 个 Vitest 测试 + vue-tsc 类型检查零错误 + 浏览器回归测试 38 条断言全部通过（详见 §4 时间线）。
- **阶段4 盘阵场景读 JPG 完成（09-02）**：浏览器**不再读盘阵原始 TIF**——后端新增 `backend/api/` FastAPI（`GET /api/scenes` 检索 + 每场景补 W/H【优先 .hdr，缺则探 TIF 头，缓存】+ jpgUrl；`GET /api/scenes/{id}/preview` **懒生成** JPG 幂等缓存）+ `backend/services/preview_jpg.py`（**镜像前端稀疏采样语义**的纯 numpy 条带抽读：`round(j*(W-1)/(pw-1))` 端点对齐 + 2% Linear + WhiteIsZero 反色 + Pillow JPEG；const 图 0→黑 / 其他→128）+ 路径白名单（`paths.py`：拒绝 `../` 穿越 / 白名单外绝对路径 / fake 占位）。前端新增 `/scenes` 盘阵场景页（卫星/传感器/日期/关键词筛选 + 行显示 W/H → 打开：jpgUrl 未就绪先调 /preview → JPG→canvas → **route='jpg' 同构 rec**，掩码按元数据 W/H 用 thumbToOrig 换算）+ 查看器拉伸下拉/导出在盘阵场景**禁用**（tooltip「已烘焙 2% 线性拉伸」）。**本地文件路径零改动**（稀疏 TIF 读法照旧）。后端 148 pytest + 前端 85 Vitest（75 基线 + 10 scene）+ vue-tsc 全绿。部署件 `deploy/nginx.conf`（`/disk-array/` alias 静态托管 + `/api/` 反代 + 缓存头）+ `deploy/sr-api.service`（systemd）+ `deploy/README.md`（阶段4 章节）+ `requirements-api.txt` 已交付；离线包脚本已含 backend。
- **阶段5 平台 API 层完成（09-02）**：契约定稿 `docs/planning/api-contract.md`（状态→**已定**）。后端 FastAPI 新增 `backend/api/platform.py` 四组端点——`/api/tools`（manifest + 直调）、`/api/chat/*`（会话 REST + 单回合 **SSE**：loop 加 `on_step` 观察缝【不改变行为】+ mock LLM `SR_LLM_MOCK=1` 固定脚本先 search_scenes 再总结）、`/api/queue*`（共享 SR 队列 REST 提交/取消 + `GET /api/queue/events` SSE 状态广播；store 补 list/状态写回，slurm 加 `SR_SLURM_FAKE` 假调度器可配速推进）、`/api/masks`（多边形+W/H → Pillow 全分辨率栅格化写 `<原图目录>/<stem>_mask.tif`+`_mask.txt` → 返 task_draft）。前端新增 `/chat` 聊天页（会话侧栏 + SSE 事件归并渲染 + 刷新恢复历史）、`/queue` 共享队列页（提交表单 + 状态徽标随 SSE 实时刷 + 取消）、查看器「提交 SR」按钮（route='jpg' 画掩码 → 烘焙落盘 → 跳 `/queue` 预填**不自动提交**）。**门禁全绿**：后端 190 unittest（+42）+ 前端 Vitest **114** + vue-tsc 零错误 + `npm run build`；`.e2e/test-platform.js` **11 断言**真 uvicorn（mock+fake）驱动前端 A 聊天 SSE/历史 → B 队列 COMPLETED → C 掩码→SR 预填+确认，无浏览器错误/无外部请求。部署件同步：nginx `/api/` 反代 `proxy_buffering off`+清 `Connection`+`proxy_read_timeout 3600s`；`sr-api.service` 补 `SR_AGENT_DB`/`SR_LLM_MOCK=0`/`SR_SLURM_FAKE=0`；`requirements-api.txt` 补 `openai>=1.40,<2`（1.x 锁死）。
- **阶段6 viewer 上下文侧舱完成（09-09）**：`/viewer` 右侧新增可折叠固定「上下文侧舱」（默认收起、展开状态 `sr.viewer.ctxRailOpen` localStorage 持久化、绘制掩码模式不自动收起；宽 268 与左侧栏镜像，DOM=[ctx-toggle 26][ctx-rail]）。`[ROI/工具]` tab（默认）= 当前文件 ROI 列表（#编号 / 原图 bbox 尺寸 / 鞋带估算面积）+ 点选后在**当前显示层（stretch 后 8bit thumb 像素）**的确定性统计 n/min/max/mean/std + 亮像元(≥200)/过曝(≥250)%——`lib/roiStats.buildStats` 纯函数（复用 maskgen.rowIntervals 与掩码栅格化同源判定；bbox 面积 > STAT_MAX_PX=4e6 自动等距抽行 stride；加权亮度 Y；alpha=0 不计）+「任务状态」（scene lqPath 关联 `/api/queue` + SSE 实时、FAILED 现场原因走前端 failReason 映射、无 sceneId 即断开/隐藏）；`[Agent]` tab = 仅盘阵场景可用（本地/离线禁用并给原因），**与 /chat 同一 chat store/会话/后端**（不 init、完整会话管理只在 /chat，角落「去 Chat 页继续 ↗」），首问自动 apiCreateSession，每次发送自动附只读上下文（scene_id/原图 W×H/显示尺寸/拉伸 + 选中 ROI 确定性统计，`agentPayload = question + CTX_DIVIDER + buildContextNote`，气泡内按分隔线视觉分开），工具回合折成单行摘要。配套：`/api/scenes` 盘阵行补只读 `lq_path`（= scene 文件父目录，run_sr 目录语义；契约 api-contract.md「阶段6 增补」）、scenes/queue/viewer 三 store 接线、viewer store `selRoi/selectRoi/roiStats`（bbox 局部 getImageData + poly 平移，整图不拷贝）+ 换图/删 ROI/stretch 的失效钩子、TifCanvas 非绘制模式画选中 ROI 高亮（白 halo + 令牌青绿）。验证：后端 190 + 前端 Vitest **140**（114 基线 + roiStats 14 / agentContext 7 / queue tasksForScene 等 +5）+ vue-tsc 零错误 + `npm run build`。
- **真机部署完成、Windows 可访问（09-07/08，node81-135）**：后端 `sr-api`（systemd，开机自启）+ 前端 `nginx`（:80，开机自启，已删出厂 default.conf 由本站点接管）均 running；Windows 浏览器直接开 `http://10.10.81.135` 可访问各页面（查看器 /scenes /chat /queue），查看器能稀疏读真实大 TIF。部署操作手册：`docs/status/real-machine-bringup.md`（ADHD 动作版：`real-machine-bringup-adhd.md`）。期间排掉两个启动坑（`User=` 行尾注释致 217/USER、py3.9 缺 eval-type-backport），均已入症状表归档。
- **遗留一：真实盘阵根未定位**：默认 `SR_SCENES_ROOT=/data/scenes` 在 node81-135 **不存在**（`ls` 报无此目录）→ `/api/scenes` 返回 `source:fake` 的 12 条占位（`fake:true` / 0B）→ `/scenes` 页面全是假数据、场景打不开、查看器「提交 SR」灰掉（route=sparse 无 sceneId，设计如此）。真实 590MB `JL1KF02B01_PMS03_...` 那类数据在别处（曾从某入口在 viewer 打开过一张真图，来源未确认）。**待办**：确认真实场景根路径（在本机哪个挂载点，还是数据在别的机器）→ 把后端 `SR_SCENES_ROOT` 与 nginx `alias` 改成同值 → restart 后复核 `/api/scenes` 出真实行。
- **遗留二：真机阶段4/5 验收清单（§6.3–6.5）未跑**：需先定位真实盘阵根再逐项执行（首次 JPG 生成耗时/内存、真实掩码烘焙落盘 ENVI 核对、真 Slurm 提交/取消、SSE 长连）。进度快照见 §6.0。
- **遗留三：Slurm 接入待真机执行（09-10，第三批）**：探针（P1）与实测参数（P2）已回传，**本轮交付的是执行侧**——部署变体（`SR_code/variants/code_0817_prod_slurm.py`，锚点替换生成）+ 作业内契约校验器（`verify_sr_run.py`，退出码 0/90）+ 批脚本（`--gres=gpu:1` / `--export=NONE` / 两行调用）+ 退出码文件定终态（`slurm.py` 不再依赖 `sacct`）+ 分阶段验收清单（`docs/status/slurm-acceptance.md`，A 探针→B 裸 Slurm 冒烟→C 单场景真 SR→D 平台四条结论）。**真机命令由用户执行、输出贴回后判读**；文档侧已回填契约 v1.5 与 `deploy/README.md` §七。待跑：验收单 A/B/C/D。**09-14 需求收敛**：所有作业**只跑在同一台 4×3090 物理机内**、不调度到其他服务器，Slurm 的角色收窄为「本机排队 + 按单卡分配 GPU」；部署方向二选一（① 复用现有集群 + `--nodelist` 锁定那台机器 / ② 本机自建单节点 Slurm）——**当天即收口：两条都不走，Slurm 全线中止**（判据见下条）。Slurm 侧交付物（变体 `code_0817_prod_slurm.py` + 校验器 + 验收清单）**保留为存量**，重启 Slurm 时从 `slurm-acceptance.md` 接着跑。
- **09-14 路线改向：Slurm 中止 → 本机 conda 直跑**（工作单 [`docs/planning/sr-minimal-prototype-plan.md`](planning/sr-minimal-prototype-plan.md)，状态「已定」）：前端点「提交 SR」→ 后端在 node81-135 用 SR 生产 conda 解释器**直接起进程**（`SR_EXECUTOR=local`），读**锁定目录**里已有的 `.tif` + `<目录名>_mask.tif`，产物写回同目录；`sbatch` 换 `bash`，配置 XML / 审计段 / 契约校验器 / 退出码文件整套原样复用。中止判据（§1.3，已在真机核实）：node81-135 在集群里是 `gpu:4 down`、DOWN 节点不会被分配作业；自建单节点 Slurm 的 6818 端口被 `slurmd` 占用；单卡分配用 `CUDA_VISIBLE_DEVICES` 即可。**代码侧 8 项改动已全部落地**（commit `8c197fc`，41 文件 +1981/−988）：新增 `backend/services/local_exec.py`（单槽串行 + job_id 持久化）、`SR_LOCKED_DIR` 锁死 + 掩码自动推导 + `suffix` 白名单、浏览器端 JPG 导出与 FS Access「输出目录」授权**整条链路删除**、本地 `.jpg` 与盘阵 `.jpg` 均可预览。
- **09-15 开发机收口：红测试修复 + 真机预演 + 浏览器回归全绿**（人不在内网机，能做的一次做完）：`8c197fc` 之后开发机其实是 **338 passed / 8 failed**（① `bash` 被 Windows 的 WSL 壳截胡；② 退出码文件没锁编码 → 真机上中文 `reason=` 写不进去、异常被吞 → **失败被误报成 UNKNOWN**）；修完再加 `backend/tests/test_local_chain.py`（4 例）把本地执行链**整条跑通**（真 config/批脚本/bash/校验器/退出码文件，仅 SR 算法换 stub）。浏览器三套回归（此前**从未在浏览器里验过**）全部复跑修正：`test-scenes.js` 按当前实现重建 58 断言、`test-platform.js` 修 3 处被 8c197fc 改掉的前置 12 断言、`test-vue-viewer.js` 删掉已取消的导出段 35 断言。同时修掉一个产品缺陷（`<目录名>_mask.tif` 被当成场景列出）并把 `.e2e/` 脚本纳入版本库。详见 §4 时间线。
- **下一步**：① 真机**三项只读确认**（工作单 §3：锁定目录拼写 `datahub`/`databub`、`SR_SR_SCRIPT` 是否指到变体、前端产物送机通道）；② 按工作单 §6 追加 env（`SR_EXECUTOR=local` / `SR_LOCKED_DIR` / `SR_LOCAL_GPU` / `SR_SUFFIX_DEFAULT` / `SR_SR_SCRIPT`→变体 / `SR_PYTHON`，**`SR_SANDBOX_ROOT` 必须不设**）后跑 §5.2 A/B 段验收；⚠️ 验收前**重新拷一次** `SR_code/variants/verify_sr_run.py`（已改为 17164B/`5fa627d8…`）；③ 定位真实场景根 → `SR_SCENES_ROOT` 与 nginx `alias` 同值 → 复核 `/api/scenes`（进度 §6.0）；④ 配 LLM key 跑通真实 /chat 闭环（§2.6）；⑤ 开发机推进 P1 ④⑤⑥⑦（§2.5）——开发机侧**已无已知红项**。
- **约束提醒**：开发机的浏览器 e2e 测试**已恢复可用**（`.e2e/launchBrowser.js` 每次用独立的临时浏览器配置目录；**候选顺序 Chrome 优先**——本机 Edge 与正在运行的 Edge 实例握手会 `Code: 0` 闪退）；真实图片都在内网盘阵，外网开发机读不到（见 §5.1）。
- **遗留三：Slurm 接入待真机执行（09-10，第三批）**：探针（P1）与实测参数（P2）已回传，**本轮交付的是执行侧**——部署变体（`SR_code/variants/code_0817_prod_slurm.py`，锚点替换生成）+ 作业内契约校验器（`verify_sr_run.py`，退出码 0/90）+ 批脚本（`--gres=gpu:1` / `--export=NONE` / 两行调用）+ 退出码文件定终态（`slurm.py` 不再依赖 `sacct`）+ 分阶段验收清单（`docs/status/slurm-acceptance.md`，A 探针→B 裸 Slurm 冒烟→C 单场景真 SR→D 平台四条结论）。**真机命令由用户执行、输出贴回后判读**；文档侧已回填契约 v1.5 与 `deploy/README.md` §七。待跑：验收单 A/B/C/D。**09-14 需求收敛**：所有作业**只跑在同一台 4×3090 物理机内**、不调度到其他服务器，Slurm 的角色收窄为「本机排队 + 按单卡分配 GPU」；部署方向二选一（① 复用现有集群 + `--nodelist` 锁定那台机器 / ② 本机自建单节点 Slurm），**方向未定**，判据 = `slurm-acceptance.md §B0` 的三条只读命令（已有半条反证：A 记录里 `node81-133/134/135/136` 是 `down`，本机 node81-135 是 `down*`）。
- **SR 最小原型首跑（09-15，本机直跑 SR，不走 Slurm）**：需求收敛为「前端点提交 → 后端在 node81-135 上用**生产 conda 解释器直接跑 SR**，读锁定目录里的 `.tif` + 目录里已有的 `<目录名>_mask.tif`，产物写回同目录」。代码侧八项已落地（执行器二选一 `SR_EXECUTOR=slurm|local`、`SR_LOCKED_DIR` 路径锁、掩码改目录推导、前端删「输出目录」+ 自动 JPG 链路、放宽本地 `.jpg`、盘阵 `.jpg` 场景可列出）。**09-15 首次提交成功**：`POST /api/queue` → `HTTP 201`、`task_id=1` / `job_id=1` / `state=SUBMITTING` / `in_place:true`。**终态已验（09-15 晚）= FAILED，判据正确**：作业确实跑完了，审计段证实 `SR_EXECUTOR=local`、`SR_SR_SCRIPT=code_0817_prod_slurm.py`（变体在用）、`CUDA_VISIBLE_DEVICES=0` + 真实 GPU UUID、conda 解释器真的起来了——**「后端能调起 conda 解释器」已由实测确认**；但 SR 自身**没干活**：场景 9/12 就已超分过，`util.py:1009-1011` 判定 `already SRed before` 后提前 `return`（`exit()` 被注释掉，源码标 `# huai`），未建新 SRLOG；校验器发现 SRLOG 早于 config.xml、判为上次残留 → 契约不满足 → 退出码文件写非 0 verdict。**契约判据在此首次生效**：若沿用 sacct 时代的判据，这一次会被标成 COMPLETED 并固化。**新阻塞 = 需要一个未超分过的场景**才能真跑出产物；工作单 = `docs/planning/sr-minimal-prototype-plan.md`，实况见 §4 时间线 2026-09-15。**另有一处未收敛**：退出码文件已判 FAILED，`GET /api/queue` 仍报 `RUNNING`（详见 §4 时间线判读节第 3 条）。
- **下一步**：① **A 段收尾只剩一件事：造一个未超分过的场景**——job 1 已证「提交链路 + conda 环境 + 变体 + 单卡绑定」全通，缺的只是让 SR 真干活（现场景 9/12 已超分 → `already SRed before` 提前 return）。二选一：换一个 `<目录名>.tif` 体积落在 `util.py:1006` 三个区间内的场景目录；或把该目录的 `<目录名>_NOSR.tif`（= 9/12 前的原始输入）改名还原回 `<目录名>.tif`（**先备份现有的超分产物**）。跑通后再定要不要重打/传 `frontend/dist`（**09-15 已在开发机重打完成**，见 §4 时间线）；② `SR_SCENES_ROOT=/data/scenes` 不覆盖 datahub → 场景列表看不到它（`/scenes` 页是「提交 SR」按钮的唯一入口，所以这个 env 必须配）。**09-15 定：本阶段不维护场景检索**，只把根指向 `datahub` + nginx `alias` 同值、接受同景多行，优先保证提交链路通；收件规则的缺口与后续修法见 §6.0「下一步行动」第 1 条（`SR_SCENES_ROOT` 是**单根**，没有多根写法；符号链接**只有建在根本身**才通过 `paths._is_within` 的 realpath 比较，建在根内会被判越权）；③ 定位真实场景根 → 改 `SR_SCENES_ROOT` + nginx `alias`（同值）→ 复核 /api/scenes（进度 §6.0）；④ 数据就位后按 §6.3–6.5 真机验收；⑤ commit 待归档变更（清单 §6.0）；⑥ 配 LLM key 跑通真实 /chat 闭环（§2.6）；⑦ 开发机推进 P1 ④⑤⑥⑦（§2.5）。
- **约束提醒**：开发机的浏览器 e2e 测试**已恢复可用**（`.e2e/launchBrowser.js` 每次用独立的临时浏览器配置目录，彻底解决「浏览器闪退、退出码 0 无任何报错」的问题）；真实图片都在内网盘阵，外网开发机读不到（见 §5.1）。

## 2. 里程碑计划与待办

### 2.1 平台里程碑（M1 / M2 / M3）

- **M1 验证闭环（当前）**：让 Agent 以秒级速度跑通一个完整的验证闭环（提出问题 → Agent 调工具 → 给出结果）。Agent 层自写状态机 + `contract.py` 定义工具接口约定，不引入 langchain / langgraph；SQLite 会话持久化 + run_sr 防重复提交保护已就位（见 §2.5）。
- **M2 批量队列 / 可视化**：多人共享队列、任务进度可视化（由 P2/P3 覆盖）。
- **M3 生产端到端**：跨小时的断点恢复 + 人工审核，按需引入一层薄的 LangGraph。
- **依据**：里程碑的阶段名来自 `docs/planning/web-plan.md`；Agent 层的具体范围与行为以 `docs/knowledge/agent-orchestration-research.md` §5/§6 为准（08-31 定稿，已取代 web-plan.md 里选 LangChain 的旧方案）。

### 2.2 优先级 P0–P4

- **P0 阻塞项**：定 LLM 底座（确认内网有没有第二台 GPU）+ Slurm 接入验证（sbatch 提交作业 / squeue 轮询队列）。注：0817 与 0820 两版的合并已取消，两个版本按运行环境选脚本（run_sr 已按 0817 版提交）。
- **P1 工具库先行（当前在做）**：见 §2.3，纯功能价值、完全不依赖 LLM。
- **P2 FastAPI 骨架**：REST + SSE + Slurm 客户端（提交/轮询/取消）+ SQLite 作业表，把工具暴露成接口端点。
- **P3 Vue3+TS 前端骨架**：聊天 + 共享队列 + 移植 TIF 查看器（本地 File + 盘阵 HTTP 双数据源）+ 盘阵检索/查看的界面。
- **P4 Agent 编排层**：最后加，用原生 function-calling（让大模型自己选工具调用）状态机或 LangGraph。

### 2.3 P1 交付物（工具库）

1. `tools/` 包 + **工具接口约定**：每个工具声明 `name`（名字）/ `description`（说明）/ `JSON-schema params`（参数格式）/ `run(**params)->result`（执行函数）。
2. 首批工具：
   - **SR 侧（对已有代码做纯包装）**：`run_sr_inference`（把 `code_0817_prod.py`（Linux 生产版）包装成无界面 / Slurm 调用）、`run_all_folders`（批量跑整个目录，在内网，需要用户回传信息或重写）、掩码生成（`掩码.tif` + `掩膜中心点坐标.txt`）。
   - **cv 侧（从零开始，先做一个来定接口约定）**：比如 `fix_bad_lines`（修复坏行/条带，用 OpenCV/numpy）。
   - **盘阵检索**：`search_scenes`（先定接口 + 本地假实现，真机上再接盘阵）。
3. **注册表**（在 `tools/contract.py` 内，用 `@tool` 装饰器自动收集所有工具）→ `manifest()` 生成一份 OpenAI 兼容的工具清单 JSON（以后直接喂给 agent 的 function-calling，还能自动生成 API 文档）。

### 2.4 P1 进展（2026-08-30 → 08-31）

> **修正**：`grid_offset_planner` 只是 `run_sr` 整个流程里的内部函数，**不把它当 agent 工具**。工具的粒度应该定在「整个流程」这一层（跑超分 / 批量处理 / 检索 / cv 修复），内部的基础模块归到 `mta_grid/` 和 `services/`。

- ✅ `backend/mta_grid/grid_planner.py`：把 `_count_sr_tiles` / `_grid_offset_planner` 这两个内部函数原样移植过来（纯 numpy 实现，作为 `run_sr` 的基础模块）。纯算法测试仍然全过。
- ✅ `backend/tools/contract.py`：定义工具接口约定 `Tool` + `@tool` 装饰器自动登记 + `ok/err` 返回约定 + `manifest()` 生成工具清单（接口约定已定好，等流程级工具来用）。
- ❌ 已删除 `backend/tools/grid_planner.py`（之前错误地把「基础模块」当成了「工具」来包装）。
- **分层确定**：`services/`（负责流程编排，`run_sr` 在这里把 mta_grid + 推理 + 工具函数组合起来）→ `tools/`（放流程级的 agent 工具，只做薄封装、调用 services）→ `mta_grid/`（放纯算法基础模块）。
- ✅ **agent 最小原型 M1（2026-08-31 晚）**：`backend/agent/loop.py` 自己写了一个运行状态机（工具传参出错时自动纠正参数重试 + 限制最大迭代轮数，不引入 langchain/langgraph）+ `backend/config.py`（读环境变量 `SR_LLM_BASE_URL/API_KEY/MODEL/...` 来配置）+ 命令行入口 `python -m backend.agent [--tools|--json|--max-turns] "prompt"`。**第一个真实流程级工具 `fix_bad_lines`**（`services/fix_bad_lines.py` 用纯 numpy 检测坏行/条带，并用上下相邻行的中值替换修复，Pillow 负责读写图；`tools/fix_bad_lines.py` 是薄封装）。36 个测试全过（包含 6 个循环场景：直接回答 / 调工具后回答 / 工具出错不中断 / 未知工具 / 超过迭代上限 / API 报错）。
- ✅ **`search_scenes` 盘阵检索工具（2026-08-31 晚）**：定好接口 + 两套后端实现——如果设置了 `SR_SCENES_ROOT` 且目录存在，就去扫描真实目录（递归 `scan_root`，并从文件名里解析卫星/传感器/日期，兼容 `JL1KF02B03_PMS05_20260722125045...` 这种命名规则）；如果没设置或目录不存在，则回退到**可预测的假数据**（带 `fake:true` 标记，防止 agent 把占位路径当成真实路径交给 run_sr）。支持过滤条件：query 子串 / satellite / date_from~date_to / limit。新增 18 个测试（现共 **54 个全过**）。真机接入盘阵只需要把 `SR_SCENES_ROOT` 指向挂载点。
- ✅ **`run_sr` + `sr_job_status`（2026-08-31 晚）**：以异步 Slurm 作业的形式运行超分（生产环境 = 用 Slurm 提交 0817 版脚本，输入 = 把高层参数拼成 XML 配置）。`services/slurm.py` 是薄封装客户端（封装 sbatch 提交 / squeue 查看队列 / sacct 查历史 / scancel 取消，命令执行器 `run_cmd` 可以替换成假的来跑测试）；`services/run_sr.py` 接收高层参数（lq_path / mask_path / sr_scale / suffix / gpu / cloud_limit / delete_ori / grid_align / options_yml）→ 拼成 `<SFSR_Config>` XML + 一个作业脚本（申请 1 张显卡 `--gres=gpu:1`、进入程序目录、运行 `code_0817_prod.py -f`）→ sbatch 提交后拿到 job_id。`sr_job_status` 负责轮询状态（先用 squeue 看是否还在排队/运行，结束后用 sacct 拿最终状态和退出码）。**本机没有 sbatch 命令时会返回明确的错误**（不会误以为 SR 已经跑起来了）；真机需要设置 `SR_BUNDLE_DIR/SR_PYTHON/SR_SLURM_WORK_DIR/SR_SLURM_PARTITION` 这些环境变量。新增 18 个测试（现共 **74 个全过**）。

### 2.5 M1 待完善（P0 已完成 09-01 · P1 待接续）

> **P0①②③ 已补齐（2026-09-01，114 个测试全过）**：对照 `agent-orchestration-research.md` §5 的 M1 承诺已兑现（会话持久化、防重复提交保护），还补了假路径拦截。P1 ④⑤⑥⑦ 留作待办，全部都可以在开发机上做，不依赖内网。

**P0 · 已完成（M1 范围内真实存在的缺口）**

1. ✅ **会话持久化**（§5.1 / §6.3）：`backend/services/store.py`（SQLite 建三张表：sessions 会话 / messages 消息 / sr_tasks 超分任务，数据库连接按需懒创建）。保存进度的时机 = **每个回合开始前**（借鉴 deepseek-harness 的「副作用前保存」：助手要调用工具时，在工具真正执行**之前**先把记录写入数据库，这样即使中途崩溃，也只会留下一个可以修复的「未完成回合」）；`loop.py` 已接入、消息都会落库，新增 `session_id` + `resume=True` 的续接接口（给 P1⑦ 的 CLI `--resume` 打基础）；恢复会话时按 deepseek-harness 项目 repair.ts 的语义，生成「上次结果未知、不要盲目重试」的提示，**不重新执行工具**。CLI 增加 `--db` 参数。16 个测试。
2. ✅ **run_sr 防重复提交保护**（§5.3）：sr_tasks 表里存 job_id，用参数内容的 sha256 指纹做去重；`submit_run_sr` **先查表再提交**——如果这条任务已经有 job_id，就先 squeue/sacct 查状态：还在运行（active）或已完成（COMPLETED）就直接复用（返回 RESUMED_ACTIVE / RESUMED_COMPLETED，**不重复提交**），失败（FAILED）/状态未知（UNKNOWN）才重跑；如果上次提交被中断（没有 job_id），返回错误而不是盲目重试。修了 `run_cmd=None` 会覆盖默认 `_run` 的潜伏 bug。8 个测试锁住「不重复提交」这个行为。
3. ✅ **run_sr 假路径拦截**（§6.2 handle_tool_errors 的语义）：`lq_path`/`mask_path` 里含有 `<fake>` 标记或不是绝对路径 → 返回错误（不抛异常，模型会根据错误回填、改成正确的参数重试），并新增「拒绝假路径」的测试用例。

**P1 · 待办（健壮性/体验）**

4. **配置体验**：支持 `.env` 文件加载配置（不依赖任何库，约 20 行代码）+ 提供 `.env.example` 样例入库；把 `system_prompt`/`max_turns` 挪进配置文件（现在写死在 `loop.py` 里）。
5. **瞬时错误重试**：大模型偶尔返回 429（请求太频繁）/超时时，现在会直接把循环杀掉。需要补上「重试 N 次、每次退避等待」再放弃（参考 LangGraph 的 RetryPolicy——目前只做了迭代上限，还没做重试）。
6. **测试缺口**：补 loop 处理畸形响应/空 choices 的测试用例（现在代码能兜住但没有测试锁住）；补 `fix_bad_lines` 的 uint8/float 数据类型用例（现在只测了 uint16）。
7. **CLI 的 `--resume <session_id>`**（依赖第 1 项，接口已就绪）。

**P2 · 边界外 / 靠真机（暂不做）**

- 结构化的分块进度回传（格式如 `{"tiles_done": 已完成块数, "tiles_total": 总块数}`）——需要改 0817 版脚本，让它在作业内部写进度文件（服务器侧改动）。
- 工具执行的权限门控/审计（参考 deepseek-harness 的 allow/deny/ask 机制）——多人共享队列时才需要，放到 M2。
- 真机验证：LLM key 跑通真实闭环、在真实机器上提交 Slurm 作业、盘阵的 `SR_SCENES_ROOT` 检索。

### 2.6 下一步（平台侧）

> 开发机上可以推进的新工作 = §2.5 的 **P1 ④⑤⑥⑦**；其余是配置/真机事项。

1. **配 LLM 端点跑通真实闭环**（M1 已经具备，只差 key）：设置 `SR_LLM_BASE_URL` / `SR_LLM_API_KEY` / `SR_LLM_MODEL` 后运行 `python -m backend.agent "把 test-tifs 下这张图修一下坏行"`。key 通过环境变量传入（目前没有 `.env` 文件，见 09-01 条目）。
2. **Slurm 接入验证**（内网真机）：用 sbatch 提交 0817 版脚本 + squeue/sacct 轮询，跑通一条真实的 job.xml 作业（0817 与 0820 两版的合并已经取消，两个版本按运行环境选脚本）。
3. **run_sr 真机验证**（CentOS7 盘阵机）：设置 `SR_BUNDLE_DIR/SR_PYTHON/SR_SLURM_WORK_DIR/SR_SLURM_PARTITION` 后，用一个测试用的配置 xml 跑通 sbatch→squeue→sacct 一条真实作业；确认 `code_0817_prod.py` 的 `<MaskPath>` 能正确读取 0/255 掩码。
4. **继续 P1**：评估 `run_all_folders` 能否无界面运行（在内网，需要用户回传信息或重写）；`mta_grid` 已移植完成，run_sr 的分块进度回传需要等 Slurm 作业内部加上结构化 JSON（现在 0817 版没有进度条）。
5. **确认 LLM 底座的可选项**（内网有没有第二台 GPU）——决定 M3 生产环境的底座，M1 不依赖这个决定。

> 记忆索引：`~/.claude/projects/.../memory/MEMORY.md`（现有 6 条：local-vendor-libs-for-viewers 查看器要用本地内置库 / browser-2gb-alloc-cap 浏览器单次分配上限 / intranet-data-inaccessible 真实数据在内网读不到 / real-files-uncompressed-1row-strips 真实大图无压缩且每行一个条带 / openai-version-pin openai 版本要锁 1.x / phase4-disk-array-reads-jpg 盘阵改读服务器预生成 8192 JPG）。

## 3. 背景决策

### 3.1 平台化技术栈方向（2026-08-30 定调）

> 结论先行：前端用 Vue3 + TS（把 tif-viewer 的逻辑**移植而不是重写**）；后端用 FastAPI + 自建作业队列 + 工具库；**LangChain 不是地基**，推迟为以后可选的薄层。四个关键问题已答：LLM 底座**未定**（统一按 OpenAI 兼容接口写，随时可换）、**保留 SLURM**（用 Slurm 调度 GPU，FastAPI 当它的客户端）、cv 工具箱**从零开发**、近期**先做工具库（P1）**。

> **修正（2026-09-01）**：前端从 React+TS 改为 **Vue3+TS**（`.vue` 单文件组件 + `<script setup lang="ts">` 写法）；「移植不重写」的决定和「抽成与框架无关的 TS 模块」的做法保持不变。

> 公司没有统一的技术标准（08-31 确认）：老员工（谢志兵）说之前那个 Vue2 + SpringBoot 工程只是从 GitHub 克隆来的开源代码，可用可不用；Nginx/Redis 都是可选项。技术栈完全自由，维持上面的选型；用 Spring Boot 做门面层的折中方案作废。

**技术栈决策**

| 层 | 选型 | 理由 / 要点 |
|---|---|---|
| 前端 | Vue3 + TypeScript（Vite 构建 + Nginx 静态托管，第三方库全部本地内置、离线可用） | 多视图 + 共享状态 + 实时进度正是 Vue3 擅长的地方；`naming-conventions §4` 已预留 TifCanvas.vue/tifDecode.ts 的结构 |
| 前端迁移 | **移植不重写** tif-viewer | 把 116KB HTML 里已经踩过所有坑的解码/亮度拉伸/稀疏条带读取逻辑，抽成**与框架无关的 TS 模块**，让 Vue3 应用直接 import 使用 |
| 后端框架 | FastAPI（REST + SSE） | Agent/SR/cv 工具全在 Python 侧，避免维护两种语言；SSE 用来向浏览器推送任务进度 |
| 作业队列 | **Slurm 调度 GPU**（sbatch 提交 code_0817）+ FastAPI 作为 **Slurm 客户端**（轮询 squeue/sacct）+ SQLite 记录作业 | 4 张 RTX 3090 计划保留 Slurm，SR 脚本本身就集成了 Slurm（有健康检查闸门，GPU 数量不对就停掉 slurmd）；SR 是长时间占用 GPU 的进程，需要靠 Slurm 排队；CPU 小任务（如掩码栅格化）可以自建轻量队列或也走 Slurm；SQLite 记任务元数据，Redis 以后需要再加 |
| LLM 底座 | **未定**，统一按 OpenAI 兼容的 `/v1/chat/completions` + tools 参数来写 | 底座可以随时换（从 Ollama 换到 vLLM/DeepSeek 都不改业务代码）；如果有第二台 GPU 就用 vLLM + Qwen2.5-14B；如果只有单张 3060 就用 Ollama + Qwen2.5-7B 量化版，与 SR 错开时间用 |
| Agent 层 | 先不碰 LangChain 全家桶；用原生 function-calling + 自己写约 200 行状态机 | 等真遇到跨小时的断点续跑/人工审核再上 **LangGraph**（那时只是替换薄层，不是重写）；`langchain-master` 只当参考、不引为依赖；边界见 docs/conventions/langchain-boundary.md |

**前端实现约束（2026-08-31 · 两轮问答确认）**

| 约束 | 结论 |
|---|---|
| 浏览器 | 只需要支持 Chrome/Edge → FS Access、WebGL2 这些新特性可以放心用，不用做降级兼容 |
| 查看器数据源 | 本地 File 和盘阵 HTTP **两种数据源并存** → 解码层抽成统一的 `source.read(offset,len)→Promise<ArrayBuffer>` 接口（FileSource / HttpSource 两个实现），IFD/稀疏/分块/亮度拉伸逻辑共用 |
| 掩码 | 键盘鼠标 + 像素坐标（不做 GIS 地理坐标），掩码就是像素坐标数组，存在后端 |
| 任务队列 | 多人**共享同一条队列** → 前端实时同步 + 乐观更新（先按预期刷新界面、失败再回滚）+ 并发处理；服务端是唯一权威数据源 |
| 实时通道 | `pushClient` 单独一个模块做抽象，**先做 SSE**（对反向代理最友好、单向推状态够用），以后可换成 WebSocket；操作类请求走 REST |
| 构建/部署 | 公司没有统一标准；产物自带全部依赖（第三方库本地内置、离线可用，Vite 构建，Nginx 托管） |
| 时间预算 | 1-2 个月 → 能做出完整平台（对话 + 队列 + 掩码 + 失败诊断） |

**前端迁移策略（2026-09-01 更新）**

- **HTML 侧收尾状态**：功能集已定稿（掩码「删除」+「合并重叠」09-01 已提交 `d9a6055`），tif-viewer.html **冻结**，不再新增功能；只剩真机实测（内网盘阵）。
- **冻结后新增功能直接进 Vue3（P3）**：盘阵 HTTP 读图（HttpSource）、多视图、任务队列，都不再加进 HTML。
- **阶段3 组件化已提前完成（09-01，不等 P2）**：交互层已移植不重写到 Vue3（六组件 + store + lib + `window.__viewer`）；HttpSource 接线 + 多视图 + 任务队列 = P3 在现有 Vue3 查看器上继续，不重做。
- **可提前项**：`tifDecode.ts` 抽取 + Node 像素级黄金对照单测（阶段1 完成，为移植备好回归基准）；阶段3 浏览器回归已全绿。

### 3.2 SR 部署定论（2026-08-31 · 两版对比 + 部署形态）

- **目标机**：现成 CentOS7 盘阵那台机器，程序包（mmsr_bundle）/模型权重/`ImgHistMatch.so` 库文件/TRT 引擎文件全在 `/DiskArray`（Windows 和 Linux 都能访问，Linux 本地读写明显更快）→ **打包方式 = 挂载复用，代码路径零改动**。
- **生产跑 `realesrgan_trt`** → 依赖 `torch_tensorrt` 库 + TRT 引擎文件（**版本锁定**，引擎文件在 `/DiskArray/.../trt/t2trt_fp16_realESRGAN_1640.trt`）；**`ImgHistMatch.so` 只有 restormer 模型用，生产环境可以忽略**。
- **部署形态**：SR = **裸机 + Slurm（不加 Docker 容器）**——TRT 版本锁定 + 机器固定不换 + Slurm 本来就在裸机上起作业，加 Docker 只会增加耦合；平台服务（FastAPI/Nginx）可以选 Docker 或裸机 venv + systemd 两种方式。
- **两版对比**（`SR_code/`）：`code_0817_prod.py`（Linux 生产版：校验 GPU 数量，`gpu_count!=4` 就停掉 slurmd、路径硬编码 /DiskArray、没有进度条）vs `code_0820_prod_windows.py`（Windows 开发分支：库加载容错处理、进度条 + 五段耗时剖析、去掉了 Slurm 联动、只要 GPU≥1 张）。**核心处理流程逐行一致 → 合并**：以 0817 为底，把 0820 的进度条/剖析功能并回来，再加结构化进度 JSON（`{"tiles_done":N,"tiles_total":M}`）供队列日志跟踪（tail）后通过 SSE 推给前端。
- **代码小改动**：`CUDA_VISIBLE_DEVICES` 从硬编码 `"0"` 改成**交给 Slurm 分配**（`--gres=gpu:1` 按作业申请）；清掉 `__main__` 里残留的 `"1"`。
- **Slurm 接入定论（2026-09-10，取代上面「两版对比→合并」与「代码小改动」两条）**：
  - **两版合并已取消**：`code_0817_prod.py` 作为**唯一真源**逐字节不动；Slurm 上跑的可运行版本由锚点替换生成器机械产出到 `SR_code/variants/code_0817_prod_slurm.py`（差异 E1–E9，见 `docs/sr_code/sr-slurm-deploy-variant.md`）。**不上机手改副本**——手改无法审计，真源更新后也无法可靠重构。
  - **选卡交给 `--gres`**：批脚本 `#SBATCH --gres=gpu:1` 申请、Slurm 注入 `CUDA_VISIBLE_DEVICES`（真机实测注入的是**整数**，如 `0`）。变体已删除全部 `CUDA_VISIBLE_DEVICES` 赋值与读取 `<GPUIDS>` 选卡的逻辑，`<GPUIDS>` 降级为**审计字段**。
  - **GPU 数量守卫已由变体删除**：判据 `!= 4` → `< 1`、计数改用 `torch.cuda.device_count()`、**不再执行 `systemctl stop slurmd.service`**（原脚本在单卡作业下必然 `exit(3)`，服务以 root 跑时还会真把节点从调度池摘掉）。
  - **终态不看 `sacct`**：真机 `AccountingStorageType=accounting_storage/none` → `sacct` 永久不可用。改由作业内契约校验器 `verify_sr_run.py` 写**退出码文件** `<DatarootLQ>/Debug/_SREXIT_<job_id>.txt`、平台读盘判终态；**退出码 0 不等于成功**，契约不满足时校验器以**退出码 90** 结束（契约 v1.5 §2.3 / §2.4）。
  - 部署与验收命令：`deploy/README.md` §七「Slurm 接入」、`docs/status/slurm-acceptance.md`（A 探针 → B 裸 Slurm 冒烟 → C 单场景真 SR → D 平台四条结论）。

### 3.3 掩码绘制（前端已落地 · 2026-08-31）

- **需求**（gui-requirements G1/P0）：在本地 HTML 页面里用交互方式绘制 ROI（感兴趣区域）掩码，输出 `掩码.tif`（**分辨率和原图一致**）+ `掩膜中心点坐标.txt`。
- **核心矛盾**：预览/JPG 是降采样过的（稀疏路径到 8192 ≈ 原图 1/3，其余路径到 2048）+ 8bit 亮度拉伸，但掩码需要全分辨率。JPG 有损压缩/亮度拉伸不影响掩码的几何形状（掩码只关心某个位置要不要处理，即 0/1），只有分辨率影响精度。
- **设计**：HTML 只负责生成**矢量多边形**（在预览画布上画，顶点坐标按缩放比例换算回原图像素坐标）；全分辨率栅格化（把矢量转成像素）最初计划放在 Python 侧（6 亿像素，当时怀疑浏览器扛不住）。**08-31 已验证浏览器能扛住**：栅格化按行流式处理（`rasterRows` 逐行扫描生成事件，不是一次性建整张位图）+ pako 库做 Deflate 压缩，内存占用只跟压缩缓冲有关 → **浏览器直接生成成了默认路径**，Python 路径保留为备选。
- **已定**：① 栅格化前后端都实现（`tif_viewer/maskgen.js` 前端流式 + `backend/services/mask.py` 后端用 Pillow，12 个测试全过、两边产物逐像素交叉验证一致）；② 精度采用**混合方案**——先在 8192 预览上粗略画，再用 `thumbToOrig` 换算回全分辨率（09-01 拍板：**不做**「按可视区域读全分辨率做放大精画」，预览就是 JPG 中间产物，见 §3.4）。
- **矢量格式**：`{"width":W,"height":H,"polygons":[{"label":"roi","points":[[x,y],...]}]}`，x 表示列、y 表示行（都是原图像素坐标）。填充约定：顶点是像素的中心、**边界像素包含在内**（例如方形 `[2,2]..[7,7]` 覆盖的是 6×6 的像素区域）。
- ✅ **前端已实现**（tif_viewer/tif-viewer.html）：工具栏点「绘制掩码」→ 进入绘制模式（光标变十字）→ 顶部浮动面板选择「矩形/多边形/魔棒」工具，在叠加层 `#drawCanvas` 上画多个 ROI（矩形直接拖拽、多边形点击加点、双击/回车/右键闭合、Esc 取消，支持撤销/清空）。顶点按**缩略图坐标**保存，渲染时根据 `(ox,oy,scale)` 换算回屏幕坐标，跟随平移/缩放。
- **魔棒**（2026-08-31 新增）：以点击点为种子，取周围 `WAND_WIN=4096` 的窗口 → `floodSelect`（以颜色容差 + 边缘亮度变化做边界阻挡，从种子向外生长选区）→ 填充内部空洞 + 追踪外轮廓 → `simplifyPoly(0.5)` 简化轮廓 → 转成缩略图坐标的 ROI。适合常见地物（相同颜色的连通区域一键圈选）。
- **魔棒自适应区域生长**（2026-09-01，修复「总选出一块默认大小椭圆」）：旧的 `floodSelect` 用「固定容差对比种子像素」判定，遇到有纹理/颜色渐变的地物会被切碎成很多小区域。改成**自适应区域生长**：用种子点周围 7×7 窗口初始化统计量（均值/平方和）→ 生长窗口取 `max(tol, 2.5*spread)`，其中 spread 是逐个像素的 RGB 颜色距离的均方根（分母用 `√cnt`，**不是 `√(3cnt)`**——修掉了一个 √3 维度不匹配的 bug；对灰色像素，RGB 距离恰好等于亮度差×√3）→ 生长过程中增量重算（每 256 个像素算一次，窗口只增不减）。边缘屏障：相邻像素亮度差 > `WAND_EDGE` 就强制停止（阈值从 32 调到 64，避免纹理内部被切碎）；`WAND_MAX_PX=8e6` 防止选区无限膨胀失控。验证：默认容差 tol=20 时，±15/±30/±40 的纹理、颜色漂移、边界种子都能 100% 覆盖，背景 150 零泄漏；4096² 全窗口约 1.2s。**已知局限**：接近背景色的纹理（区域内 ±30 的纹理 vs 背景只差 10-20）用任何单一容差都分不开（色差 < 2.5×spread，降低 tol 也没用）。
- **区域重叠与「合并重叠」按钮**（2026-09-01）：`掩码.tif` 本来就是所有 0/1 像素的**并集**（`rowIntervals` 会把相接/重叠的区间跨多边形合并）→ 所以重叠/相接的多边形天然会合成一个连通区域，像素层面不会重复。用户选择**手动合并**而不是生成时自动合并（这样不改变输出区域的数量）：`maskgen.js` 新增 `rasterMask`（栅格化并集）+ `connectedComponents`（用 BFS 广度优先遍历给连通区域编号）+ `mergeConnected`（把多边形向外扩 1px → 并集栅格化 → 填洞 → 逐个连通区重新追踪轮廓 → 再缩回 1px）；tif-viewer 的「合并重叠」按钮调用它，完成后弹出「X 个区域 → Y 个连通区」的小提示。
- **合并改成分批执行 + 进度条**（2026-09-01）：真实图的缩略图约 8192×8013（6500 万像素），原来 `mergeConnected` 在主线程同步执行会卡住界面好几秒。重构为：生成器 `mergeConnectedGen`（每处理一批就 `yield {phase, progress}`，把控制权还给主线程，界面保持响应）+ 同步版本 `mergeConnected`（供 Node/测试一次性跑到完成，结果不变）+ 协程版本 `mergeConnectedAsync`（用 setTimeout 驱动，带 `onPhase`/`onProgress` 回调）。浏览器端 `mergeRois` 接上四阶段遮罩进度条（栅格化 → 洞填充 → 连通域 → 轮廓提取）+ 合并期间禁用按钮 + **归属校验**（合并期间如果切换了图片，就丢弃这次结果，避免写错图）。顺带的优化：每个连通区域只在它自己的**最小外接矩形子图（外加 1px 背景）**里重新追踪轮廓，不再整图重扫/整图分配内存（原来每个区域都会新建一张 65MB 的整图数组）。
- **删除工具**（2026-09-01）：`drawTool='del'` 模式下，点击做**命中检测**（用 `pointInPoly` 射线法判断点在不在多边形内），悬停高亮，命中后先 200ms 柔和红色（#d8605a）闪烁再移除。异步移除是为了防误删：`delClick` 记录当时的 `owner=activeRec`，`setTimeout` 回调里只有 `activeRec===owner` 才真正删除（闪烁期间如果切图/清空就跳过不删）。
- **浏览器直接生成掩码**（2026-08-31，不需要 Python）：「生成掩码」按钮 → ROI 用 `thumbToOrig` 换算回原图像素坐标 → `MaskGen.buildTiff`（`rasterRows` 逐行栅格化 + pako 做 Deflate 压缩 → 输出 classic TIFF：小端字节序、8bit、压缩方法 8）→ 下载 `掩码.tif`（**0/255**）+ `掩膜中心点坐标.txt`（各区域的质心坐标列表，2 位小数，CRLF 换行）。E2E 测试钩子已挂在 `window.__viewer.{enterDraw,exitDraw,buildMaskJson,exportMaskJson,thumbToOrig,getRois,genMask,wandSelect,maskGen}`。
- **产物格式**（2026-08-31 对照参考文件定稿）：txt 文件参考 `SR_code/JL1KF02B03_..._mask.txt`——开头是 `＃掩膜中心点坐标（X，Y）\r\n＃掩膜编号，X坐标，Y坐标\r\n`，后面每行是 `序号,质心X,质心Y`（2 位小数、CRLF 换行、UTF-8 编码）；`掩码.tif` 前后端统一用 **0/255**（`run_sr` 用 OpenCV 的 `cv2.threshold(>0)` 读取，0/1 表达效果等价）。原来「01点阵.txt」（W×H 的字符阵列，24k² 的图约 600MB）的方案**废弃**。
- **端到端已验证**：maskgen 的 Node 单元测试 **12 项全过**（包括与 Pillow 逐像素对比、随机多边形只有边界 ≤1px 的离散化差异、纹理整块点选、三组 merge：重叠→1 区 / 相接→合并 / 包含→吞噬）；后端 `test_mask.py` **12 项全过**（txt 与参考文件逐字节对齐、tif 0/255 读写往返）；jsdom 冒烟测试 **5 项全过**（页面加载 + genMask 直接生成并下载两次 + wandSelect 添加 ROI + 合并按钮接线 + 删除命中/闪烁/移除）。
- **剩余事项**：① 放大精画路径**已砍**（09-01 预览方案定为 JPG 中间产物，不做浏览器内全分辨率读取；保持 8192 预览粗画 → `thumbToOrig` 换算回全分辨率，符合已定的混合精度）；② 真机浏览器实测（内网盘阵：真实的 2.4 万像素级大图）。开发机浏览器 e2e 已恢复（`launchBrowser.js` 用临时配置目录），Vue3 查看器的浏览器回归 38 条断言已覆盖接线与行为，但真实大图的内存/耗时仍需真机确认。

### 3.4 新需求（2026-08-27 晚）——已收口（09-01 预览路径定案）

- **显示亮度拉伸（ENVI 风格）+ 像素定位**：✅ 已实现并全部通过 E2E 测试（见时间线）。
- **加速大图读取**：真实文件结构已确认（GF07A03/KF02B04 都是无压缩、每行一个条带 → 不需要金字塔，任意窗口可以直接按字节切片读取）；**稀疏条带预览**（秒级概览）✅ 已完成，预览分辨率提到 8192（`SPARSE_PREVIEW_MAX`），与导出 JPG 同样清晰。
- **预览方案拍板（2026-09-01）= JPG 中间产物**：网页/预览显示导出的 JPG（长边 8192 ≈ 原图 1/3），不再在浏览器内对原始 TIF 做全分辨率切片读取。
- 因此「按可视区域按需读取全分辨率瓦片」的方案**已砍**（原来的设想是：缩放超过预览分辨率时，按可视窗口把条带切片叠加上去绘制，读取量与屏幕分辨率成正比，无压缩可以直接切片、无需金字塔）——它的前提条件被 JPG 中间产物方案取代了。如果以后真要在网页里看全分辨率，无压缩条带下仍然可以直接按字节切片捡回来（不需要金字塔，这些工作只是冻结，没有丢失）。
- **取舍说清楚**：JPG 是 8bit 有损 + 8192≈1/3 分辨率，网页内「查看超过 1/3 的细节」这一需求被放弃，更细的细节只能看 JPG 产物或原始文件；掩码保持「8192 预览粗画 → `thumbToOrig` 换算回全分辨率」，放弃「放大精画」的升级（不影响掩码产物本身的精度）。

## 4. 时间线

### 2026-08-26 · 起点：CDN 不可达
- 用户报错：`geotiff.js` 加载失败（**CDN 无法访问**）。内网机器从 jsdelivr 加载脚本失败。
- 修复 quick-look.html：移除 jQuery（改用原生 JS）、修复 `toRGBA8` 返回 Uint8Array 而 ImageData 需要 Uint8ClampedArray 的类型兼容问题、把 pako + UTIF 两个库**内联进 HTML 单文件**（零外部引用，完全自包含）。

### 2026-08-27 上午 · 方案定型 + 2GB 分配上限
- 用户选择「UTIF 单库简化版（先冻结等待）」。目标：10000² 以内的图用 UTIF 整图解码，12000² 以上用分块解码。
- 实测失败：报「Array buffer allocation failed」（申请内存失败）。
- `.e2e/probe.js` 探测确认：**Edge 单次内存分配上限约 2GB**（2.0GB 能成功，2.7/3.6GB 抛 RangeError），跟机器有没有 64GB 内存无关。30000² 的 RGB8 整图解码（需要 2.7GB+3.6GB）必然失败。
- 引入 **geotiff.js 分块读取**作为后备方案（库文件本地内置），形成「UTIF + geotiff.js」两套引擎的架构。

### 2026-08-27 下午 · 修 400MB 全黑 + 收口大图解码失败
1. **复现全黑**：生成 float32/uint32 测试图 → UTIF 路径 100% 全黑。根因：`UTIF.toRGBA8` 对灰度图只支持每采样点 1/2/8/16 位（bps），32bit 数据落空变成全 0。
2. **定位第二个全黑 bug**：16bit RGB 输出 `[0,255,0,0,…]`——alpha 通道被亮度拉伸系数缩放了（scale≈0.0039 → alpha 变 0 → 整图透明全黑）。修：alpha 永远不做拉伸缩放，通道数 <4 时强制设为 255（完全不透明）。
3. **修浮点/uint32 全白**：统一用 min-max 线性拉伸（跳过 NaN/Inf 这类无效值），8bit 数据原样直接输出。
4. **geotiff 的惰性 fileDirectory 陷阱**：`getFileDirectory` 是个惰性占位对象，没有 `getField`/`getTag` 方法 → 自己写了 `tiffTags()` 直接解析 TIFF/BigTIFF 头部的 tag：262 是光度解释（photometric，说明像素值如何对应颜色）、259 是压缩方式（compression）。
5. **修 WhiteIsZero 反色**（photometric=0 表示白色用 0 表示，之前没处理导致颜色反转）。
6. **修 E2E 测试陷阱**：页面会清空 input.value → 测试要从 `window.recs[].file` 读文件。
7. **验证通过**：
   - `test-types.js`：8 种数据类型（8bit RGB / 16bit 灰度 / 16bit RGB / uint32 / float32×3 / WhiteIsZero 反色）全部通过。
   - `test-regress.js`：8192² uint16 灰度 134MB → 分块多窗口 + 进度 100% + 渐变色正确（4.5s）；UTIF 内存分配失败自动回退 geotiff.js 也通过。
8. **文档落盘**：`docs/experience/gui-experience.md`（经验）、`.gitignore`（屏蔽 test-tifs/.e2e/*.zip）、个人记忆文件（memory）更新。

### 2026-08-28 · 稀疏条带预览（大图加速）
- 用户回传探测结果：GF07A03、KF02B04 都是**无压缩、每行一个条带**。
- 「打开要 2 分钟」之谜根因确认：旧路径把整张图全量读一遍来做预览，耗时正比于文件字节数；而且 geotiff 的窗口读取在每行一个条带的情况下，每个窗口要触发约 4096 次小切片读取（42 个窗口 × 4096 ≈ 17 万次切片读）。
- 实现 `parseStrips`（解析文件头部，读出每条条带的偏移和长度数组，兼容 classic/BigTIFF）+ `sparseCollect`（按条带做字节切片，每隔 ph 行抽一行、每隔若干列抽一列，8 路并发读取，带进度条）；`stripPxVal` 支持 8/16/32bit 的 uint/int/float 各种类型。
- 分流规则：**无压缩 + 条带结构 + 单波段 + 大于 100MB** → 走稀疏预览；否则保持原来的分块/UTIF 路径不变。
- E2E `test-sparse.js` 全过（像素精确 + Deflate 压缩读取回退）；`test-regress/stretch/stretch8/locator/types/probe-list` 全过、无回归。
- 134MB 8192² 测试图：稀疏预览 **1.3s**（只读 32MB/128MB）；原来全量解码 4.5s。big_u16 也会自动走稀疏路径（1.5s）。

### 2026-08-29 · JPG 导出三 bug 解耦修复（稀疏大图）
- 用户报：导出 JPG 时报 `Cannot read properties of undefined(reading 'p2')`；预览/导出分辨率停在 2048×2003（图像太糊，想要约 1/3 清晰度）；右/下边有大片拉伸条纹。要求把问题解耦、分步排查。
- **Bug A（导出崩溃，根因）**：`sparseCollect` 静默返回时带的是 `nbands:1`，而 `exportToJpg` 读的是 `c.nb` → 取到 undefined → `stretchRgba` 误走了「3 波段」分支 → `stretchMap` 在 `st[1]`（不存在）上读 `p2` → 崩溃。修：静默返回补上 `nb:1`；再给 `stretchMap`/`stretchRgba` 加兜底（统计信息缺失时按值截断返回，绝不抛错）。
- **Bug C（右/下条纹，根因）**：`sparseSample` 用 `ceil(W/pw)` 定步长 + 把坐标限制在 W-1 → 右边/下边各约 145 列/行重复了最后一个像素。修：改成「目标→源」的线性最近邻映射 `round(j*(W-1)/(pw-1))`，两端像素精确对齐。
- **Bug B（分辨率）**：用户看到的 2048×2003 其实是预览图（`PREVIEW_MAX=2048` 的产物）；导出因为 Bug A 从未成功过。修：默认导出长边 `JPG_MAX` 从 16384 改成 **8192**（24739 宽 → 8192×8013 ≈ 原图 1/3），同时避开 16384² 的画布面积/toBlob 限制；导出失败时自动降档重试（8192→4096）。
- 同步：`docs/knowledge/jpg-export-background.md` 的默认值/降档说明；`.e2e/test-sparse.js` 的注释（新映射下断言仍成立，`round(i*8191/2047)=4i`）。
- ⚠️ **待验证**：开发机 e2e 浏览器启动仍失败（puppeteer-core 25.9.0 + 无界面 Edge，退出码 0，属环境问题、与本次改动无关）；真机（内网）验证：打开 24739×24199 的图 → 预览无条纹、**用「2% Linear（2% 线性拉伸）」导出 8192×8013 的 JPG** 且无报错。

### 2026-08-29 · 网页显示清晰度对齐本地 JPG（稀疏预览 2048→8192）
- 用户报：「同样的 jpg，本地目录预览清晰度远超 html 网页显示」。根因：导出 JPG 是 8192（约 1/3 清晰度），而网页的稀疏预览仍然是 2048（`PREVIEW_MAX`），页面图一放大就糊了。
- 方案（用户选定「网页直接按 8192≈1/3 显示」）：新增常量 `SPARSE_PREVIEW_MAX=8192`，只把**稀疏条带路径**的预览目标长边从 2048 提到 8192；`sparseSample` 的默认目标改成这个常量。分块/UTIF 路径保持 2048（压缩/小图预览仍然很快）。
- 代价与约束：稀疏预览到 8192² 时，源数据（Float32）+ 画布约 **0.5GB/图**，避免同时打开太多大图（已写入 gui-experience §4 的内存提示）。
- 同步：`.e2e/test-sparse.js` 与 `.e2e/test-regress.js` 的预览尺寸断言从 2048 改成 8192（测试图就是 8192²，无压缩命中稀疏路径，是 1:1 恒等映射）；`docs/knowledge/jpg-export-background.md` 的预览降采样说明改口（稀疏 8192 / 其余 2048）。
- ⚠️ **待真机验证**：8192² 稀疏预览在真实 24739×24199 图上的内存/耗时；e2e 本机仍无法启动（环境问题）。

### 2026-08-31 · 前端约束问答 + SR 部署定论（平台化决策）
- **前端约束两轮问答**（见 §3.1「前端实现约束」表）：只支持 Chromium 内核、查看器双数据源（本地 File + 盘阵 HTTP，解码层加 `source` 统一接口）、掩码用键鼠操作 + 像素坐标、多人共享队列、`pushClient` 推送模块（先做 SSE）、Vite 构建的自包含产物、1-2 个月做出完整平台。
- **无公司技术标准确认**：老员工（谢志兵）说之前那个 Vue2+SpringBoot 工程只是从 GitHub 克隆的开源代码，可用可不用，Nginx/Redis 都是可选 → 技术栈完全自由，维持 Vue3+TS+FastAPI；Spring Boot 门面层的折中方案作废。
- **SR 两版对比**（`SR_code/code_0817_prod.py` vs `code_0820_prod_windows.py`）：核心处理流程逐行一致；差异 = 库加载的宽容处理、进度条 + 五段耗时剖析、与 Slurm 的联动（GPU 数不对就停掉 slurmd）、GPU 数量校验（要求 ==4 张 vs 只要 ≥1 张）。合并策略见 §3.2「SR 部署定论」。
- **部署形态定论**：目标机器就是现成 CentOS7 盘阵那台（程序包已跑通）；生产跑 `realesrgan_trt`（TRT 版本锁定）；计划保留 Slurm；SR 用裸机 + Slurm（不加 Docker）；程序包/权重/库文件（.so）/TRT 引擎文件全在 `/DiskArray`（Linux 读写明显更快）。详见 §3.2。

### 2026-09-01 · M1 待完善 P0①②③ 补齐（会话持久化 + run_sr 幂等 + 假路径拦截）

- ✅ **P0① SQLite 会话持久化**：`backend/services/store.py`（sessions + messages + sr_tasks 三张表，数据库连接按需懒创建）。保存进度时机 = **每个回合开始前**（借鉴 deepseek-harness 的「副作用前保存」：助手要调用工具时，在工具真正执行**之前**先把记录写入数据库，这样即使崩溃，也只会留下一个可以修复的「未完成回合」）。`loop.py` 已接入、消息都会落库，新增 `session_id` + `resume=True` 的续接接口（给 P1⑦ 的 CLI `--resume` 打基础）；恢复会话时按 repair.ts 的语义生成「上次结果未知、不要盲目重试」的提示，**不重新执行工具**。CLI 增加 `--db` 参数。16 个测试。
- ✅ **P0② run_sr 防重复提交保护**：sr_tasks 表用参数的 sha256 指纹作为任务标识；`submit_run_sr` **先查表再提交**——如果已有 job_id，先 squeue/sacct 查状态：还在运行（active）或已完成（COMPLETED）就复用（返回 RESUMED_*，**不重复提交**），失败（FAILED）/状态未知（UNKNOWN）才重跑；上次提交被中断（没有 job_id）→ 返回错误而不是盲目重试。修了 `run_cmd=None` 会覆盖默认 `_run` 的潜伏 bug。8 个测试锁住「不重复提交」。
- ✅ **P0③ 假路径拦截**：`run_sr` 拒绝 `lq_path`/`mask_path` 里含 `<fake>` 标记或不是绝对路径 → 返回错误（模型会回填修正参数重试，不抛异常）；修了 `@tool` 装饰器误挂 `_bad_path` 导致注册表分发时报 TypeError 的 bug，并加了注册表的回归测试。
- 修复：openai 3.x 与 aiohttp 3.8.3 不兼容 → 把 openai 锁到 `>=1.40,<2`（装了 1.109.1）；resume 测试用「聊天接口的测试桩 + 引用快照」来断言；Windows 临时目录按 LIFO 顺序清理（先关掉 store 再删目录）。
- 端到端冒烟测试：喂假路径 → 返回错误 → 模型自我修正换成真实路径 → 恰好只提交一次 sbatch → 任务记录里的 job_id 写入数据库。
- 提交：`034081c`（12 个文件，+1093/-61 行，包含此前未提交的 gui-experience / current-question 预览定案改动）。
- **api_key 接入**：目前没有 `.env` / 配置文件，CLI 直接读环境变量 `SR_LLM_BASE_URL` / `SR_LLM_API_KEY` / `SR_LLM_MODEL`（在 `backend/config.py` 里，base_url 默认 `https://api.openai.com/v1`）；P1④ 会补上 `.env` 加载，省去手动敲环境变量。
- **文档依据澄清**：M1/M2/M3 的**阶段名**源自 `docs/planning/web-plan.md`（M1 验证闭环 / M2 批量队列可视化 / M3 生产端到端）；**Agent 层的具体范围和具体行为**以 `docs/knowledge/agent-orchestration-research.md` §5/§6 为准（08-31 定稿：自写状态机、不引第三方库，已取代 web-plan.md 里选 LangChain 的旧方案）。

### 2026-09-01 · 魔棒自适应修复 + 掩码区域合并/删除工具

- ✅ **魔棒自适应区域生长**：重写了 `maskgen.js` 的 `floodSelect`——7×7 种子窗口做运行统计 + `max(tol, 2.5*spread)` 自适应窗口（spread 的分母修正为 `√cnt`，解决 √3 维度不匹配）、每 256 个像素增量重算一次、边缘屏障 `WAND_EDGE` 从 32 调到 64。纹理/漂移/边界种子 100% 覆盖，背景 150 零泄漏；详见 §3.3。
- ✅ **「合并重叠」按钮**：`maskgen.js` 新增 `rasterMask` / `connectedComponents` / `mergeConnected`（1px 向外扩 → 并集栅格化 → 填洞 → 逐个连通区重新追踪轮廓 → 收缩回来）。用户选定**手动合并**（不改变输出区域的数量）。tif-viewer 加 `mergeRoi` 按钮 + `mergeRois()` 函数。
- ✅ **「删除」工具**：`drawTool='del'` 点击做命中检测（`pointInPoly` 射线法）→ 悬停高亮 → 200ms 柔和红色（#d8605a）闪烁 → 移除；`delClick` 捕获当时的 `owner=activeRec`，防止闪烁期间切图/清空误删。
- ✅ **测试**：`.e2e/test-maskgen.js` **12 项全过**（纹理整块点选 + 三组 merge：重叠→1 区、相接→合并 / 分离→2 区、包含→吞噬）；`.e2e/test-html-mask-smoke.js` **5 项全过**（合并按钮接线 + 删除命中/闪烁/移除；4 个异步断言 `okA` 全部加了 `await`，消除并发时的共享状态竞争）；后端 `python -m pytest backend/tests -q` **114 全过**（无回归）。
- ⚠️ **提交状态**：本轮改动 `tif_viewer/maskgen.js`（+142 行）、`tif_viewer/tif-viewer.html`（+83 行），加上先前未提交的 `CLAUDE.md` / `docs/README.md` / `current-question.md` 三个文档，共 **5 个文件未提交**；`.e2e/` 测试按 gitignore 不纳入版本库。真机浏览器实测等内网（开发机 e2e 浏览器仍无法启动）。

### 2026-09-01 · 阶段3 查看器 UI 组件化（HTML 交互层 → Vue3，移植不重写）

- **范围**：把 tif-viewer.html 的交互层整体迁到 Vue3——阶段1 抽出来的 `tifDecode.ts`/`maskgen.ts`/`source.ts` **不动**，只把交互和调度逻辑用 Vue3 的惯用写法重写。
- ✅ **六个组件**：`TifCanvas.vue`（viewCanvas + drawCanvas 双画布，支持平移/缩放/定位/改尺寸 + 掩码画布事件 + 并发冲突防护）、`Toolbar.vue`（选文件 / 拉伸下拉 / 输出目录三态切换 / 自动JPG / 定位 X·Y / 绘制掩码 / 生成掩码）、`FileList.vue`（侧栏列表 + 即探 layout（选中即探测文件信息）+ jpg 状态/重新导出 + ×移除 + 收起）、`DrawPanel.vue`（矩形/多边形/魔棒/删除/容差/合并重叠/撤销/清空/导出掩码JSON/完成 + 合并四阶段进度）、`DecodeOverlay.vue`（遮罩 + 进度条 + 百分比）、`StatusBar.vue`（rec-bar，兼容旧 HTML 版格式）。
- ✅ **store 统一调度**：`stores/viewer.ts`（addFiles 添加文件 / activate 激活 / decodeRec 解码 / paintStretch 亮度拉伸 / fit/pan/zoom 视图操作 / locatePixel 像素定位 / 掩码全套 / 导出队列 / fsIO 文件读写），用 `renderTick` 计数器触发 TifCanvas 重绘（等价于 HTML 版直接调 render()）；大对象字段用 `markRaw`/`shallowRef` 做响应式性能优化。
- ✅ **浏览器层 lib**：`browserKit.ts`（真实 canvas 的依赖注入）、`decode.ts`（`needGeo` 按规则选择稀疏/分块/UTIF 解码方案 + 内存分配失败自动回退）、`exportJpg.ts`（collect 收集 → stretch 拉伸 → toBlob → saver 保存的串行导出流程）、`saver.ts`（FS Access / IndexedDB / downloadSaver 逐级降级 + `_logLock` 保证日志串行）。
- ✅ **E2E 钩子**：`src/viewer/e2eHooks.ts` 挂载 `window.__viewer`（**始终暴露**，真机验收也靠它），行为与 HTML 版的钩子对齐 + 新增 `setSparseMin`（可改写 `SPARSE_MIN`，默认 1e8 不变）+ `recs()`/`activeRec()` 摘要快照。
- ✅ **并发冲突防护逐条照搬**：`delClick` 记录 owner + 200ms 柔和红闪（闪烁期间切图/清空不误删）、合并期间切图就丢弃结果 + 按钮禁用、导出用 `_jpgToken` 令牌守卫 + 遇到 RangeError 就 `_exportCap=4096` 降档重试一次、`rasterRows` 每批让出主线程、解码进度只有 `rec===activeRec`（还是当前图）才刷新界面。
- ✅ **BigTIFF 读取方案修复**（本阶段唯一行为偏差）：`needGeo` 探测到 `probe.big`（大文件标记）时强制走 geotiff.js 分块读取——本地内置的 UTIF 解不了 BigTIFF（解码后 width/height 丢失，HTML 原版在同样条件下会产出 0×0 的缩略图）；`bigtiff_strips` 测试样例解码正确（256×256 渐变图，route='chunked'）。新增 4 项 needGeo 路由的 Node 测试。
- ✅ **验证**：`vue-tsc --noEmit` 类型检查零错误；**75 个 Vitest 测试全绿**（tifDecode 30→35 + viewMath 17 + maskgen 17 + source 4 + exportJpg 2，其中掩码 17 项黄金对照测试无回归）；`npm run build` 构建成功；`.e2e/check-frontend-build.js`（旧 HTML 版的选择器）全过（`tif-canvas` 类 + `.rec-bar` 格式保持兼容）；新 `.e2e/test-vue-viewer.js` **38 条断言全过**——启动初始化 30 个钩子齐全 / 解码路由 + 像素（utif、chunked×2、bigtiff、sparse 四条路径）/ 稀疏路径用 setSparseMin / 亮度拉伸重绘 / 像素定位 + 7s 过期 / 掩码冒烟（enterDraw 进入绘制 → commitRect 提交矩形 → 2 个 ROI → buildMaskJson 反算 → genMask 两次下载 mask.tif 280B + mask.txt 104B → merge 合并 → del 红闪 3670px → 200ms 后移除 → undo/clear 撤销/清空）/ 导出降档（假的 saver 第一次抛内存分配错误 → writeJpg 重试两次成功）。
- ⚠️ **开发机浏览器 e2e 已恢复可用**：`launchBrowser.js` 每次用独立的临时浏览器配置目录（profile），彻底解决 Edge「配置目录被单实例占用」导致的退出码 0 闪退问题；旧笔记里「e2e 浏览器无法启动」（08-29/08-31 条目）作废。
- ⚠️ **仍待真机（内网盘阵）验证**：真实 24739×24199 大图的稀疏预览 8192 不崩、掩码直出 `掩码.tif` + `掩膜中心点坐标.txt`、**2% 线性拉伸导出 JPG 8192×8013** 无条纹；代码只能保证按常量分块与行为等价（这是跨阶段的红线要求）。

### 2026-09-02 · 阶段4 盘阵场景读 JPG（废弃 HttpSource/Range，改服务器预生成 JPG）

- **决策改向**：盘阵数据路径从「前端 HttpSource + nginx Range 读 TIF 字节」改为「**服务器预生成 JPG**」——`source.ts` 的 HttpSource 桩废弃不实现，浏览器对盘阵场景只 `<img>`/canvas 加载服务器烘焙的 8192 长边 JPG。理由 = 与 09-01「预览=JPG 中间产物」原意对齐 + 首次生成懒加载几十秒后 nginx 静态直出 + 不占浏览器 2GB/16384² 预算。
- ✅ **后端 FastAPI**（`backend/api/`）：`GET /api/scenes` 包 `search_scenes`（新增 sensor 过滤），每行补 `W/H`（优先伴生 `.hdr` ENVI 头，缺则纯结构探测 TIF 头，`(path,mtime)` LRU 缓存）+ `jpgUrl`（nginx 静态地址，未生成 null）+ opaque base64 scene id（`scene_id(rel)`）；`GET /api/scenes/{id}/preview` 懒生成 → `FileResponse`。路径白名单 `paths.py`：`ensure_within` realpath 含于根 + 拒 `../`/白名单外/fake/非文件，`scene_id_to_abs` 拒坏 id。CORS `*`。`python -m backend.api` 直启 / systemd 走 `uvicorn backend.api.app:create_app --factory`。
- ✅ **预览 JPG 服务**（`backend/services/preview_jpg.py`）：sparse 采样 = 前端语义 Python 镜像（ps=`min(1,8192/max(W,H))`、映射 `round(j*(W-1)/(pw-1))` 端点对齐，逐条带读行字节跨距、只留采样列，绝不整图载入）；拉伸 = 2% Linear + WhiteIsZero 先反色 + const 图规则（全 0→黑、其他 const→128）→ Pillow 灰 JPEG(q90)；缓存 = 幂等，落源同目录 `<basename>.preview.jpg`（或 SR_PREVIEWS_ROOT，须在 scenes root 内）；压缩/tiled/多波段小文件兜底 Pillow（`PILLOW_FALLBACK_MAX_PX=1<<26`），大文件不支持则明确报错。
- ✅ **前端**：`lib/scene.ts`（`__SR_CFG__` 配置 + URL/解码 helper：JPG rgba→1-band src + stats 固定 0..255 → 交互拉伸恒等，`thumbPolysToOrig` 元数据 W/H 换算）+ `stores/scenes.ts` + `pages/ScenesPage.vue`（新路由 `/scenes`）+ viewer store `openSceneJpg`（route='jpg' 同构 rec：layout 显示「盘阵 JPG（已烘焙 2% 线性拉伸）」、thumb=canvas、掩码复用；`paintStretch` 对 jpg 早退、导出禁用）+ Toolbar 拉伸下拉场景禁用（固定显示「2% 线性」+ tooltip）+ FileList 盘阵 chip。
- ✅ **测试**：backend pytest 148 全过（新增 `test_api.py` 16 + `test_preview_jpg.py` 12 + sensor 过滤；fake 回退/白名单穿越/预览幂等/hdr 优先）；前端 Vitest 85 全过（scene.test.ts 10：config/query/预览 id 转义/1-band 提取/route-jpg 同构 rec/掩码换算端点）+ `vue-tsc --noEmit` 零错误；`.e2e` 全绿——`test-scenes.js` **45 断言**（本地静态服务顶替 nginx 的 /disk-array + 真 FastAPI uvicorn：打开→懒生成→静态读 JPG→/viewer rec→掩码按元数据换算）+ `test-vue-viewer.js` **42 断言**（本地文件路径回归，零 pageerror）。
- ✅ **部署**：`deploy/nginx.conf`（`location /disk-array/ { alias <root>/; }` 静态托管 + `.preview.jpg` 缓存头 + `location /api/` 反代 127.0.0.1:8000 `proxy_read_timeout 600s` + SPA 尾斜杠规则含 /scenes）；`deploy/sr-api.service`（systemd：uvicorn + SR_SCENES_ROOT/SR_PREVIEWS_ROOT env）；`deploy/requirements-api.txt`（fastapi/uvicorn/numpy/pillow）；`deploy/README.md` 重写为阶段2/4 双段部署文档；`package-offline.sh` 增补 backend + 后端部署件进离线包。
- ⚠️ **待真机（内网 CentOS7 + Win11）验收**：首次 JPG 生成的耗时/内存（1.1GB/1.78GB 真实图）、二次秒开、显示占用、掩码按元数据 W/H 的换算精度；清单见 §5.2。

### 2026-09-10 · 平台自建 SR 沙箱（SR_SANDBOX_ROOT）

起因是一个提问：**「就不能前端填个盘阵路径，配好直接跑吗？」** —— 能，[QueuePage.vue:116](frontend/src/pages/QueuePage.vue#L116)
的手填 `lq_path` 输入框早就在，`/api/queue` 也直接收这个路径，不经过 `/api/scenes`。卡住的只有一件事：
**SR 对她的 `DatarootLQ` 不是只读的** —— [`util.py:1305`](SR_code/util.py#L1305) 的 `writeTiff` 写产物前必先把
**输入**改名成 `*_NOSR.tif`（与 `Suffix` / `DeleteOriTifNeeded` 都无关）。所以「前端填一个路径」的字面含义是
「那个目录会被改写」，而计划把首次生产写权放在阶段 6。

三条路摆出来（平台自建副本 / 手工拷一次 / 直接填生产目录），选了**平台自建**。

改动：`SR_SANDBOX_ROOT` → 作业第一步 `cp -a` 一份私有副本，config 的 `DatarootLQ` 指向副本。
关键点是这个路径**由后端在 sbatch 之前就写进 config.xml**，而 `exit_code_file_for()` 事后从同一份 config
反推退出码文件位置 —— 所以终态读取完全不受影响，作业侧不需要任何回传握手。不配这项 = 旧行为。

同一批还修了两个会咬人的：① 配置文件按 `run_sr_<suffix>_<指纹前12位>.xml` 命名（原先同 suffix 的不同任务
共用一份 config.xml，第二个提交会把第一个任务的终态查询指到别的场景去）；② `/api/queue` 每行新增
`run_dataroot` 字段说明产物到底落在哪（开沙箱时 `params.lq_path` 是用户填的原路径，两者不同）。

踩到的坑：最初写了 `.ready` 标记文件跳过重复复制，随后意识到**同一任务失败重跑时，源修好了、跑的却是旧副本** ——
省几分钟赔一下午，删掉。改成每次无条件重拷。

### 2026-09-11 · Slurm 第四批真机推进（env 就位）+ 一次生产文件覆盖事故

**推进到哪**（`docs/status/slurm-acceptance.md` §0 前置）：

- ✅ **§0.3 env 全绿**：systemd drop-in `/etc/systemd/system/sr-api.service.d/10-slurm.conf` 九个变量 + 主文件四个，
  共 13 项经 `/proc/<PID>/environ` 确认进了**运行中的进程**（`active (running)`）。
- ✅ **§0.2 变体就位**：`code_0817_prod_slurm.py`(30695B / `041bea73b36f`) 与 `verify_sr_run.py`(16419B / `92e400a71635`)
  已在 `$BUNDLE`，与仓库**逐字节相同**，`head` 见 `GENERATED FILE` 横幅。
- ✅ **§0.3 后半**：`$WORK`/`$SANDBOX` 建好、`chown nginx:nginx`、`sudo -u nginx touch` 得 `WRITE OK`。
- ⬜ **A/B/C/D 全部未跑**。下一步 = §A 探针（只读）。

**两处订正 / 坑**：

1. **分区名 `gpup` → `gpu`（文档错值，已全仓订正）。** `sinfo -h -o "%P"` 实测 = `centos7 / deicc / gpu / gpu* / test`
   （星号=默认分区），**没有 `gpup`**。早先 `slurm-integration.md` §2.7 那条「用户回传 `gpup`」是转述错误。
   已改 `deploy/sr-api.service` / `deploy/README.md` §7.1 / `slurm-integration.md` / `slurm-acceptance.md` /
   `sr-pipeline-restore-plan.md` / 两个测试夹具。**教训：真机当场跑的探查命令优先于文档里记的历史「实测值」**，
   纠正用户前先让他把探查命令跑一遍。
2. **`systemctl show -p Environment | tr ' ' '\n' | grep '^Environment='` 恒得 1 条** —— `show` 只在**第一个**值前
   打印 `Environment=`，其余是裸的 `KEY=VAL`。该管道曾让人误判「env 没生效」白查半天。已把正确查法写进
   `slurm-acceptance.md` §0.3。

**⚠️ 事故：`$BUNDLE/code_0817_prod.py` 被覆盖（2026-09-11 09:55，不可恢复）**

| | 覆盖前 | 现在 |
|---|---|---|
| 字节 | 33477 | **28703** |
| sha256(去 CR) | `cfbf0bda4804` | `03c9b4fc3f64` |
| mtime | 8月15 14:55 | 9月11 09:55 |
| 权限 | 755 | 777 → 已 `chmod 755` 归一 |

- 起因：仓库 `SR_code/code_0817_prod.py`（文档认定的**唯一真源**，`sr-slurm-deploy-variant.md` §1/§3）
  来自 `centos7/code/` 那份；`$BUNDLE` 里那份是**没同步过的旧版**。用户把两边对齐了。
- `find /DiskArray/ProductionSchedule/exe_CentOS7 -name 'code_0817_prod*.py'` 只剩改后两份，**旧版连同 `.bak` 都不在**，
  且内网机传不出文件 → **已不可恢复**。
- **性质**：这是本任务首次**覆盖生产既有文件**（此前全是「只加不改」：并置变体 + 加校验器 + 写我们自己的 unit）。
  两种解释后果不同：源树→部署副本的**漂移修正**（方向对，但属生产变更，需团队知情认账）；
  或把未评审版本**推上生产**（事故）。
- **未决**：(a) `centos7/code/` 的完整路径 + 那份的 size/sha/mtime（确认真源没被动）；
  (b) **平时是谁、用什么命令跑生产超分？有没有调度脚本指向哪个路径的 `code_0817_prod.py`？**
  (b) 无答案前**不得进 §C（单场景真 SR）** —— 那是唯一会真跑超分、真写盘阵的一步。

### 2026-09-14 · Slurm 路线中止，最小原型落地（commit `8c197fc`）

- **决策**：不走 Slurm，改成「后端在本机用 SR 生产 conda 解释器直接起进程」。工作单 = `docs/planning/sr-minimal-prototype-plan.md`（已定），判据与已定决策见该文 §1.2/§1.3。Slurm 侧交付物（变体 + 校验器 + `slurm-acceptance.md`）保留为存量，不做删除。
- **落地（8 项，全部在 `8c197fc`）**：`backend/services/local_exec.py`（单槽串行执行器，接口与 `slurm.py` 对齐：`available/submit/status/cancel`；job_id 持久化在 `SR_SLURM_WORK_DIR/.local_job_seq`；终态仍读退出码文件，复用 `slurm.terminal_from_exit_file`，不另写一份映射）· `SR_EXECUTOR`/`SR_LOCKED_DIR`/`SR_LOCAL_GPU`/`SR_SUFFIX_DEFAULT` 四个配置项 · `/api/queue` 的锁定目录 400 + 掩码自动推导（`<lq_path>/<leaf>_mask.tif`）+ `suffix` 白名单（空后缀=把输入改名，属破坏性配置）· 盘阵目录 `.jpg` 可列出（排除自产的 `.preview.jpg`）· 本地 `.jpg` 可打开 · 前端删掉整个浏览器端 JPG 导出与 FS Access 授权链路 · 队列页 `lq_path`/`mask_path` 改只读。
- **真机未做**：§5.2 A/B 段验收与 §6 的 env 全部未执行（人不在内网机）。

### 2026-09-15 · 最小原型的红测试修复（开发机）+ 一处真机风险

- **实测**：`8c197fc` 之后本机是 **338 passed / 8 failed** —— 与工作单 §5.1「保持全绿」直接冲突，说明这批新测试**在开发机上从未真正跑过**。两个根因：
  1. **`bash` 被 Windows 的 WSL 壳截胡**（7 个红）：`Popen(["bash", …])` 走 `CreateProcess`，而它**先搜 System32、后搜 PATH**（与 `shutil.which` 不同），于是命中 `C:\Windows\System32\bash.exe`（WSL 启动器，实测 rc=1「未安装分发」），而不是 PATH 上的 Git Bash。修：`local_exec._bash_path()` 自己扫 PATH 并跳过 System32 那个，交给 `Popen` 绝对路径；`available()` 改为「解析得到可用 bash」才为真（否则只会在启动后才失败、且表现为 UNKNOWN，像 SR 静默失败）。
  2. **退出码文件没锁编码**（1 个红，**同时是真机风险**）：`verify_sr_run.py` 写、`slurm.read_exit_code_file` 读都走平台默认编码。真机上批脚本带 `--export=NONE` 会丢掉 `LANG`，而 SR 的 conda 是 **py3.6** → `open()` 默认 ASCII → `reason=` 字段里的中文写不进去 → 异常被 `except` 吞掉 → **退出码文件根本不落盘** → 平台读到 UNKNOWN 而不是 FAILED。这正是 §6.4「静默失败不再被标成成功」要防的失败模式。修：写侧 `encoding="utf-8"`、读侧 `encoding="utf-8", errors="replace"`（ASCII 字段不受影响，兼容旧文件），OPT yml 读取同样锁定。
- **新增回归测试**：POSIX 下用 `LC_ALL=C` 起子进程复现作业环境（Windows 强制不出非 UTF-8 默认编码 → skip）；读侧分别接受 GBK 旧文件与 UTF-8 新文件。子进程探针**先手动跑通过**（否则它会在 CentOS7 上才第一次执行——第一版就踩了非 raw 字符串把 `\n` 提前展开的坑）。
- **⚠️ 变更影响（上机前必读）**：`verify_sr_run.py` 由 16419B / `92e400a7…` 变为 **17164B / `5fa627d8…`**。机上那份是旧版、行为正确但少这两处编码锁定 → **下次上机要重新拷一次**（变体 `code_0817_prod_slurm.py` 与其 sha 未动）。`slurm-acceptance.md` §0.2 的比对值已就地标注。
- 结果：`python -m pytest backend/tests -q` = **348 passed / 1 skipped / 0 failed**；耗时 148s → 9s（此前 7 个红用例每个都在等 20s 超时）。

#### 同日续：真机预演（T1-3）+ 三套浏览器回归复绿（T1-4）

人不在内网机，能做的都做完了：把「本机执行链」整条跑通，再把 `8c197fc` 之后**从未在浏览器里验过**的回归全部复跑修正。

- ✅ **`backend/tests/test_local_chain.py`（新增 4 例）——本机把本地执行链跑通**。除 SR 算法外全部是真的：真 `config.xml` + 真 `build_batch_script` + 真 bash + 真 `SR_code/variants/verify_sr_run.py` + 真退出码文件 + 真幂等表；只有 `code_0817_prod.py` 换 stub（`SR_SR_SCRIPT=stub_sr.py`，stub 自己按 PAC/RC 规则命名产物）。四条断言链：① `SUBMITTED→RUNNING→COMPLETED`、`exit_code=0:0`、SRLOG + `<stem>_sr.tif` 落地、退出码文件含 `verdict=0`；② **静默失败**（exit 0 但无产物）→ `FAILED` 且仍写退出码文件（`verdict=90`）；③ 同参重提 → `RESUMED_COMPLETED`、job_id 不变、`_JOBS` 仅 1 条；④ 队列页要回读的路径（config/batch script/log_dir/`read_dataroot_lq`）提交即存在。**意义**：真机上剩下的不确定性收窄到解释器（conda py3.6 + torch/GDAL）与 GPU，不再是接线。
- ✅ **浏览器回归三套全绿**（前置 `npm run build`；命令见 §5.3）：
  - `.e2e/test-scenes.js` **58 断言** —— 原文件（45 断言）随 `.e2e/` 被 gitignore 丢失，本日**按当前实现重建**。fixture 故意让 `.hdr`（3200×2000）与 TIFF/Pillow 实测尺寸（900×450 / 400×200）**不一致**，这样「掩码必须按元数据 W/H 换算」那条断言是可被证伪的，不是恒真。跨页状态断言走场景页「去查看器」**页内跳转**——`page.goto` 会整页重载丢掉 pinia store。
  - `.e2e/test-platform.js` **12 断言** —— 8c197fc 改了产品行为，修三处前置：提交需自带 `<目录名>_mask.tif`（§4.3 起掩码按约定推导、缺文件直接 400）、按钮文案 `提交到 Slurm`→`提交 SR`（改成两步式）、[C] 段打开场景必须带 `lqPath`（否则 `srReady` 为假、按钮禁用）。末尾那条「掩码任务 COMPLETED」的失败**不是回归**：同参重提被幂等层命中 `RESUMED_COMPLETED`（§4.3 起前端恒传 `mask_path: null` → 指纹必然相同），已改为「先断言不产生第二行，再改倍率→真新建任务」。
  - `.e2e/test-vue-viewer.js` **35 断言** —— 删掉针对**已取消功能**的整段导出/降档重试断言（`setSaver`/`reExportJpg`/`scanPendingExports`/`jpgStatus` 已随 §4.5 从 `e2eHooks.ts` 移除），钩子清单同步为现役 22 个。留着的旧断言只会长期报红。
- ⚠️ **本机浏览器回归必须用 Chrome**：Edge 与用户正在运行的 Edge 实例握手会 `Failed to launch the browser process: Code: 0`（临时 profile 挡不住）。已把 `launchBrowser.js` 候选顺序改为 **Chrome 优先**（`SR_E2E_BROWSER` 仍排最前可覆盖）。
- 🐛 **发现并修掉一个产品缺陷：`<目录名>_mask.tif` 被当成场景列出**。`scene_search.is_scene_file` 原先只排除 `.preview.jpg`；真机形态是掩膜与场景**同名同目录**，于是每个已带掩膜的目录都会在 `/scenes` 多出一行——卫星/传感器由掩膜文件名解析（`satellite=<父目录名>`、`sensor='mask'`）、尺寸取掩膜 TIFF 头、「提交 SR」还可点（同目录 → 同指纹 → 幂等命中，不至于重复跑，但列表是脏的）。修：`is_scene_file` 增加 `_DERIVED_STEM_SUFFIX = "_mask"` 判定（只认**结尾**标记，`GF07A03_mask_PMS01_….tif` 仍是场景），后端 +3 例、`test-scenes.js` 的【已知问题】**镜像断言**改为「掩膜未出现在列表」（5 行 → 3 行）。
- 🗂 **`.e2e/` 从「整目录忽略」改为「只忽略依赖/大图」**：`test-scenes.js` 就是这么丢的。现在 `*.js` / `lib/` / `package.json` / `package-lock.json` / `*.py` 入库，`node_modules/`、`fixtures/`、`*.tif`、`*.jpg` 仍忽略。**后果**：以后清理工作区不会再丢掉可复跑的回归资产。
- 全绿实测：后端 **354 passed / 1 skipped**（348 + 4 链路 + 2 掩膜）、前端 Vitest **160 passed**、浏览器 **58 + 12 + 35 断言**。
- 📝 **文档债（已修一部分，剩下的下次）**：本次顺手改了 `current-question.md`、`api-contract.md`、`frontend-migration.md`、`platform-tutorial.md`、`project-deep-dive.md`（对外摘要里「用 Slurm 提交」已改为本机直跑）与 `CLAUDE.md`。**未改**：`docs/knowledge/interview/` 下的专题篇（`http-sse.md` Q24 / `db-storage.md` / `fastapi-rest.md` / `python-concurrency.md`）仍以 `sbatch` 讲幂等与「先记意图后记结果」。机制本身没变（执行器接口对齐、`slurm.py` 作为存量保留且仍是 `SR_EXECUTOR=slurm` 的代码路径），但叙述该补一句"提交动作经可切换执行器、默认 bash"。另有历史条目里的旧计数（`frontend-migration.md` §阶段5、`frontend-phase4-phase5-prompts.md` 门禁）按"当时实测"保留不改。

### 2026-09-09 · 阶段6 查看器上下文侧舱（右侧 [ROI/工具] + [Agent]）

- **范围（L1 确定性 + L2 Agent 解读）**：见 §1「阶段6」bullet 与 `docs/planning/api-contract.md`「阶段6 增补」blockquote；需求要点 = 右侧固定侧舱、默认收起、展开态持久化、ROI 统计只对**当前显示层**做确定性计算（绝不让 LLM 编数字）、Agent 与 /chat 同会话不另起炉灶。
- ✅ **L1 ROI 统计**：`lib/roiStats.ts` 纯函数 `buildStats(displayImg, poly, {hi,clip,maxPx})`（复用 `maskgen.rowIntervals`，与掩码栅格化像元集合严格一致；bbox 局部裁剪 → 平移 poly → 单遍扫描；亮像元阈值 STAT_HI=200 / 过曝 STAT_CLIP=250 / 抽行上限 STAT_MAX_PX=4e6，模块常量可覆写）+ `roiOrigGeom`（顶点 thumbToOrig → 原图 bbox 含端点 + 鞋带面积，编号列表几何）。viewer store 加 `selRoi/selectRoi/clearRoiSel/refreshRoiStats/roiSelIndex`（按对象身份选中，删/切图自动失效）→ `roiStatsOnCanvas` bbox `getImageData` + poly 平移省整图拷贝 → TifCanvas 非绘制模式画选中高亮。
- ✅ **L1 任务状态**：`stores/queue.ts` 加 `tasksForScene(tasks,{lqPath,stem})`（normDir 去尾分隔符 + `pathLeafOf(mask)===stem_mask.tif` 双匹配、created_at 倒序）与运行期 `failReason` 映射（SSE `job_update` FAILED 带 error 才捕获；历史 FAILED 回退「见 log_dir」）。RoiToolsTab 只在 `activeRec.sceneId` 在场才 connect+list（本地 TIF/离线零网络，页面离开断开）。
- ✅ **L2 Agent tab**：`lib/agentContext.ts`（`buildContextNote`/`roiStatLine`/`CTX_DIVIDER`/`agentPayload`，数字全确定性）；`AgentChatTab.vue` 复用 chat store（无 init；首问 newSession → send(agentPayload)），工具回合单行摘要、气泡内分隔上下文、禁用原因就地显示。
- ✅ **接线**：ViewerPage 布局行 `[FileList][TifCanvas][ContextPanel]`；三个组件 ContextPanel（rail+toggle+localStorage）/RoiToolsTab/AgentChatTab；`ViewerRec.lqPath` 全链路（后端 `lq_path` → scene.ts → scenes store → viewer openSceneJpg）。
- ✅ **验证**：前端 Vitest **140**（新增 roiStats.test 14 / agentContext.test 7 / queue.test +5）；vue-tsc 零错误；`npm run build` 通过；后端 190（test_api 断言 lq_path=父目录 / fake→None）。提交 = 阶段6 单 commit（+ 其前置的已暂存设计令牌 restyle 单 commit）。
- ⚠️ **真机/浏览器回归遗留**：阶段6 不改 `.rec-bar`/Toolbar/`window.__viewer`，开发机 e2e 预期不受影响——**2026-09-15 已复跑**（`test-vue-viewer` 35 + `test-scenes` 58 + `test-platform` 12 断言全绿；见 §4 09-15 条）；RoiToolsTab/AgentChatTab 均为新组件，尚未有 jsdom 渲染测试（纯逻辑已由纯函数测试覆盖）。

### 2026-09-15 · SR 最小原型：首次本机直跑提交（A 段进行中）

> 工作单：`docs/planning/sr-minimal-prototype-plan.md`（八项）。Slurm 路线**已中止**（09-14 commit
> `8c197fc`），改为 `SR_EXECUTOR=local`——后端用 `subprocess` 在本机直接拉 `bash` 跑同一份生成脚本
> （`#SBATCH` 行对 bash 就是注释，脚本原样复用），单槽串行、job_id 从 1 起。

- ✅ **后端部署到位**：7 个文件拷进 `<APP>/backend`（机上核对 `run_sr.py` 里 `local_exec` 10 处、
  `config.py` 里 `SR_EXECUTOR` 2 处），随后 `chown -R nginx:nginx <APP>/backend`。
- ✅ **三项 env 生效**（缺一不可）：`SR_EXECUTOR=local`、`SR_LOCAL_GPU=0`、`SR_LOCKED_DIR=<场景目录>`
  —— 用 `systemctl show -p Environment sr-api | tr ' ' '\n' | grep SR_` 核对。
- ✅ **提交前预检**：`<目录名>_meta.xml` 的 `SolarAzimuth=181.7911`（非空 → **SC 步** → 输入取
  `<目录名>.tif`，**不是** `PAN.tif`）；该 tif 与 `<目录名>_mask.tif` 均为 **30837×30948** 逐像素一致
  （`PAN.tif` 18688×15800 不参与 RC 以外的流程）。
- ✅ **首次提交成功**：`curl -X POST http://127.0.0.1:8000/api/queue -d '{"lq_path":"/DiskArray/tmp/wangrz/datahub/JL1KF02B03_…_L1_PAN"}'`
  → `HTTP 201`：`task_id=1` / `job_id=1` / `state=SUBMITTING` / `in_place:true` + 覆盖提示；
  配置文件 `<WORK>/run_sr_sr_46c50fa2d6da.xml`。作业日志 = `<WORK>/run_sr_sr_46c50fa2d6da.1.out`。
- **终态已验（09-15 晚）= FAILED，判读见下一节**。退出码文件 `_SREXIT_1.txt` 已写出（`verdict=90`）。
  但 SR 没干活——详见「2026-09-15（判读）· job 1 终态」。
- **未收敛（待核）**：退出码文件已判 FAILED，`GET /api/queue` 本轮两次复核均报 `RUNNING`，
  `updated_at` 停在 11:40:43 未再变化。详见下一节第 3 条。
- ⚠️ **本次两个新坑（都已入症状表 + `deploy/README.md` §5.2 / §5.3.1）**：
  1. **拷完 `backend/` 忘了 `chown nginx:nginx`** → 服务以 `User=nginx` 跑，读不到 root 属主的新文件
     → import 阶段 `PermissionError: … backend/api/app.py` → 崩溃重启循环，`curl :8000/api/health`
     返回 `000`。**这不是代码坏了**，补 chown 即恢复；`systemctl is-active` 在 auto-restart 间隙
     可能恰好打印 `active`，**判定看 `health=200`**。
  2. **drop-in 缺 `[Service]` 段头** → systemd **不报错、不警告，静默忽略整个文件**。机上遗留的
     `override.conf` 就是这样（三行裸 `Environment=`），`SR_EXECUTOR=local` 从未生效——本该本机直跑，
     实际会退回默认 `slurm` 去 `sbatch`。**`systemctl cat` 会照常打印死行，判定生效只看 `systemctl show`。**
- ⚠️ **作业运行期间禁止 `systemctl restart sr-api`**：单元没设 `KillMode`，systemd 默认杀整个 cgroup，
  会把正在跑的 SR 子进程一起杀掉。所以机上那份坏 `override.conf` 的清理要等作业跑完再做。
- ⬜ **A 段收尾三件待办**：① ~~作业终态未验~~ → **已验（见下节）**；② ~~`frontend/dist` 没重打~~
  → **09-15 已在开发机重打完成**（前端 160 Vitest + vue-tsc + `vite build` 全绿，见下节），
  剩「传到机器上」（共享粘贴板只能传文本、拷不了目录树，需另想办法）；③ `SR_SCENES_ROOT=/data/scenes`
  不覆盖 datahub 目录 → 场景列表里看不到它，查看器也就没有「提交 SR」按钮（**B 段前提**，见下节的新约束）。

### 2026-09-15（判读）· job 1 终态：FAILED（判据正确）

> 真机命令由用户执行、输出贴回后判读（本窗口到不了 node81-135：`curl 10.10.81.135` → `000`/rc=28）。

**结论：作业跑完了，契约判定为不满足 → 退出码文件写非 0 verdict。此次失败由输入条件（场景已超分）决定，不是链路故障。**

1. **审计段（`<WORK>/run_sr_sr_46c50fa2d6da.1.out`）一次性证实四件事**，全部从「推测」升级为「事实」：

   | 项 | 实测值 | 意义 |
   |---|---|---|
   | `SR_EXECUTOR` | `local` | 本地执行器生效，没走 sbatch |
   | `SR_SR_SCRIPT` | `code_0817_prod_slurm.py` | **变体在用**（原脚本 `:146`/`:644` 不会再覆盖 `CUDA_VISIBLE_DEVICES`） |
   | `CUDA_VISIBLE_DEVICES` | `0` + `GPU:aae2108-78cf-f65a-07e4-9ae0f98ef805` | 真绑到一张卡，UUID 可核 |
   | `SR_PYTHON` | `/run/media/root/SSD/program/anaconda/…/torch1.9.1py36/bin/python` | **conda 解释器真的起来了**（跑到了 SC 分支判断、调了 nvml、打印 SolarAzimuth） |

   `SLURM_JOB_ID=` / `JOB_GPUS=` 为空是应该的（本地执行器没有 Slurm）。
   `SR_PYTHON` 的挂载前缀经 2026-09-15 `ls -d` 复核落定：`/run/media/root/SSD` 与
   `/run/media/root/SSD/workspace/wangrz/sr-agent-platform` 均存在，`/media/node81-135/SSD`
   及其下同名路径不存在 ⇒ 文档原有的 `/run/media/root/SSD/…` 前缀成立。

2. **SR 自身没干活——不是崩溃，是生产脚本自己提前返回**。日志两行：

   ```text
   SolarAzimuth:181.7911 < SolarAzimuth>          ← 判定为 SC 步
   /DiskArray/…/JL1KF02B03_…_L1_PAN already SRed before
   ```

   对应 [util.py:1003-1011](../../SR_code/util.py#L1003-L1011) 的 SC 分支：`<目录名>.tif` 存在，但体积
   落在三个接受区间（`0–1.1` / `1.5–1.7` / `3.8–4.1` GB）之外 → 判定「已超分过」→
   **`return`，不处理、不建新 SRLOG**（`exit()` 被注释掉，源码里标着 `# huai`）。
   证据：`Debug/` 里的 `_SRLOG.txt` 与 `_NOSR.tif` 都是 **9月12** 的，说明该场景 9/12 就已超分。
   这条正是 `slurm-integration.md §2.3` 列为「**不是 exit 而是 return** 的陷阱」的那一条，真机撞上了。

3. **待核：平台状态未收敛**。退出码文件已判 FAILED，`GET /api/queue` 本轮两次复核却都报 `RUNNING`，
   `updated_at` 停在 11:40:43 未再变化。按 [local_exec.py:257-271](../../backend/services/local_exec.py#L257-L271)
   的分支：只有当本地子进程**仍存活**（`proc.poll() is None`）时才返回 `RUNNING`，且此时**不读退出码文件**；
   进程一旦回收，才落到 `slurm.terminal_from_exit_file`（FAILED）。⇒ 需先分清是那个 `bash` 仍在跑，
   还是已回收但记录未更新。**未定案前不能断言状态机能收敛。**

4. **判据正确兜住**。校验器输出：

   ```text
   sr SR contract NOT satisfied: SRLOG 早于 config.xml, 是上次残留
   ```

   它拿 `_SRLOG.txt` 的 mtime 与 config.xml（今天 11:36）比对，判定那是**上次残留**、拒绝计入本次成果
   → 契约不满足 → `_SREXIT_1.txt` 写非 0 verdict。**换成 sacct 时代的判据，这次会被标成 COMPLETED
   并永久固化成一个假成功**（§2.3「闭环毒药」）；契约判据避免了这一结果，这是它在真机的首次生效。

5. **判读时的两处订正**：
   - `_SREXIT_1.txt` 的**前导下划线是真实的**——`EXIT_FILE_FMT` 在两边都是 `"_SREXIT_{job_id}.txt"`
     （`run_sr.py:72` / `verify_sr_run.py:81`），贴回文本里掉下划线是转录噪声，**不是命名不一致的 bug**。
   - `systemctl show -p Environment --value` 在本机 systemd 版本上**不支持 `--value`**，会报
     `无法识别的选项`；按 `slurm-acceptance.md §0.3` 的查法（不带 `--value` + `tr ' ' '\n'`）。
     另：**队列 JSON 的 `lq_path` 已经把锁定目录给全了**，查路径不必再绕 `SR_LOCKED_DIR`。

6. **开发机侧同步收尾**（本窗口）：后端 `python -m pytest backend/tests -q` → **346 passed**；
   前端 `npm test` → **160 passed**（11 文件）+ `vue-tsc --noEmit` 零错误 + `vite build` 成功（15.7s）。
   `frontend/dist` 已重打，**待传**。

7. **下一步（唯一阻塞）**：让 SR 真干活需要**一个没超分过的场景**。二选一：
   - 换一个 `<目录名>.tif` 体积落在 `util.py:1006` 三个区间内的场景目录；或
   - 把本目录的 `<目录名>_NOSR.tif`（= 9/12 超分**之前**的原始输入）改名还原回 `<目录名>.tif`
     ——**必须先备份现存的超分产物**（POSIX rename 会覆盖同名文件）。

   顺带：`SR_SCENES_ROOT` 是**单根**（`paths.py:34-39` 只读一个值，无多根写法）；符号链接**只有建在根
   本身**（或祖先）才通得过 `paths._is_within` 的两边 `.resolve()` 比较，**建在根内会被判越权**——
   这条把「建符号链接」这个选项收窄成了一句话。

## 5. 交接（给新窗口）

### 5.1 环境约束

- 开发机是**外网机**（能上网），真实遥感图全部在**内网机的盘阵**上，无法导出/复制/读取文件头来探测结构。文件结构只能靠**用户回传 ENVI 头信息**（Edit Headers 里的 Compression/Interleave 字段）或**根据已知信息推断尺寸**。不能要求用户提供文件路径。
- 开发机浏览器 e2e **可用**（`.e2e/launchBrowser.js`：puppeteer-core 驱动无界面 Chrome/Edge + 每次用独立临时配置目录）；**候选顺序 Chrome 优先**（2026-09-15 实测：本机 Edge 与正在运行的 Edge 实例握手失败 `Code: 0`，Chrome 通过；要换浏览器设 `SR_E2E_BROWSER`）；浏览器单次内存分配约 2GB、Canvas 画布面积上限 16384²、CDN 无法访问（第三方库必须本地内置）。

### 5.2 下一步（给新窗口）

> 真机部分已整理成带成功标准/耗时的打勾清单，见 **§6 真机一次出差：验收一页纸**；以下为要点速览。

1. **阶段4 真机验收**（需在 **CentOS7/Win11 内网机**执行，重点测首次生成耗时与内存）：
   - 部署：按 `deploy/README.md` 在盘阵机起 nginx + systemd 的 FastAPI（`SR_SCENES_ROOT` 指真实挂载点）；开发机先 `npm run build && npm run package:offline` 拷包。
   - 用真实大图（GF07A03 1.11GB / KF02B04 1.78GB，24739×24199 级）验证：
     a. `GET /api/scenes` 返回每行正确 W/H + jpgUrl；`curl` 一个 `/preview` **记录首次生成耗时**与 FastAPI RSS 内存峰值（预期几十秒内、进程内存不失控——逐条带抽读只碰采样行）；
     b. 再请求一次同 id 的 `/preview` → 立即返回（幂等缓存命中）；`/disk-array/<rel>` 静态直出带缓存头；
     c. Win11 浏览器开 `http://<内网机IP>/scenes` → 打开真实场景 → canvas 出图、文件列表「盘阵」chip、拉伸下拉禁用（tooltip）、二次秒开；
     d. 画一个矩形掩码 → 生成 `掩码.tif`，用 ENVI 打开核对区域位置是否按元数据 W/H 换算正确（在 JPG 上画 → 全分辨率落点）；
     e. `journalctl -u sr-api` 无越权访问告警；构造 `../` 越权 id/URL 应 403/404。
   - 阶段3 收尾项一并做：真实 24739×24199 大图本地文件路径的稀疏预览 8192 不崩、掩码直出、2% 线性导出 JPG 8192×8013 无条纹（本地文件路径零回归的红线）。
2. **阶段5 真机验收**（开发机已离机全绿：后端 354 + Vitest 160 + `.e2e/test-platform.js` 12 断言；契约 = `docs/planning/api-contract.md`，提示词见 `frontend-phase4-phase5-prompts.md`【阶段5】）：
   - **真 LLM**：`SR_LLM_BASE_URL/API_KEY/MODEL` 指内网端点 + `SR_LLM_MOCK=0`，/chat 发一条 → 真实工具调用 + 最终回复；刷新恢复历史；
   - **真 Slurm**：`SR_SLURM_FAKE=0`，/queue 提交 → sbatch 真实作业 → SSE `job_update` 推进到「完成」（**终态读退出码文件，不是 `sacct`**——真机账务关闭，见 §3.2「Slurm 接入定论」）；装变体 + 校验器 + 六项 env 见 `deploy/README.md` §七，分阶段验收（含四条链路结论）见 `docs/status/slurm-acceptance.md`；取消按钮 scancel 生效；
   - **盘阵掩码落点**：/viewer 打开真实场景画掩码 →「提交 SR」→ ENVI 打开 `<原图目录>/<stem>_mask.tif` 核对区域与 0817 消费路径一致（`_mask.txt` 质心坐标）；
   - **nginx SSE 长连**：/chat 与 /queue 经反代挂 10min+ 无断流/攒批（心跳/断链重连正常）；
   - **systemd 权限**：`nginx` 用户能读盘阵、写预览 JPG 缓存与 `SR_AGENT_DB`（见 deploy/README §三）。
3. **当前路线（最小原型）上机前**：`docs/planning/sr-minimal-prototype-plan.md` §3 三项只读确认 → §6 六项 env（`SR_SANDBOX_ROOT` **不设**）+ `systemctl restart sr-api` → §5.2 A/B 段验收。⚠️ **改过的 `SR_code/variants/verify_sr_run.py` 需重新拷回真机**（16419B/`92e400a7…` → **17164B/`5fa627d8…`**，编码锁定两处），验收 D 步之前必须重拷。
4. **开发机可推进**：P1 ④⑤⑥⑦（见 §2.5，.env / 瞬时错误重试 / 测试缺口 / CLI --resume）；配 LLM key 跑通真实闭环（§2.6）。本地执行链已验证，浏览器回归已全绿 —— 开发机侧**没有已知红项**。

### 5.3 关键文件

- 查看器交付物：`tif_viewer/tif-viewer.html`（冻结）→ Vue3 版 `frontend/src/`（pages/ScenesPage.vue + components/ + stores/{viewer,scenes}.ts + lib/{tifDecode,maskgen,scene,source,viewMath}.ts）
- 阶段4 后端：`backend/api/app.py`（FastAPI：/api/scenes + /preview）、`backend/api/paths.py`（白名单 + scene id codec）、`backend/services/preview_jpg.py`（稀疏采样 + 2% 拉伸 + Pillow 缓存）
- 阶段4 部署：`deploy/nginx.conf`、`deploy/sr-api.service`、`deploy/requirements-api.txt`、`deploy/README.md`；离线包 `frontend/scripts/package-offline.sh`
- 阶段5 契约/实现：`docs/planning/api-contract.md`（**已定**）、`backend/api/platform.py`（chat/queue/tools/masks + SSE 广播）+ `backend/api/app.py`（lifespan 轮询）、`frontend/src/{lib/api.ts, stores/{chat,queue}.ts, pages/{ChatPage,QueuePage}.vue}`、查看器「提交 SR」（`stores/viewer.ts::submitSr` + Toolbar 按钮）、`.e2e/test-platform.js`（12 断言）
- 阶段5 部署增补：nginx `/api/` 反代 `proxy_buffering off`+`proxy_read_timeout 3600s`（SSE）；`sr-api.service` env（`SR_AGENT_DB`/`SR_LLM_MOCK=0`/`SR_SLURM_FAKE=0` + Slurm 六项 `SR_PYTHON`/`SR_BUNDLE_DIR`/`SR_SLURM_WORK_DIR`/`SR_SLURM_PARTITION`/`SR_SLURM_TIME`/`SR_SLURM_CPUS`）；`requirements-api.txt` 补 `openai>=1.40,<2`
- Slurm 接入件：`SR_code/variants/{code_0817_prod_slurm.py,verify_sr_run.py,.diff,.provenance.json}` + 生成器 `SR_code/tools/gen_slurm_variant.py`、只读探针 `deploy/slurm/probe_slurm.sh`；运行期 `backend/services/{run_sr,slurm}.py`；验收清单 `docs/status/slurm-acceptance.md`、差异表 `docs/sr_code/sr-slurm-deploy-variant.md`、契约 `docs/sr_code/sr-pipeline-interface.md` v1.5
- 阶段6 上下文侧舱：`frontend/src/components/{ContextPanel,RoiToolsTab,AgentChatTab}.vue` + `lib/{roiStats,agentContext}.ts`（buildStats / tasksForScene / CTX_DIVIDER）+ viewer store 选中/统计钩子 + `/api/scenes` `lq_path`（阶段6 增补见 api-contract.md）
- 经验文档：`docs/experience/gui-experience.md`
- 真机预演（无内网机时可跑）：`backend/tests/test_local_chain.py`（4 例，除 SR 算法外全真：真 config/批脚本/bash/校验器/退出码文件；`code_0817_prod.py` 换 stub）
- E2E 测试：`.e2e/`（**2026-09-15 起入库**，只忽略 `node_modules/`+`fixtures/`+大图）——`test-vue-viewer.js` **35 断言**本地文件回归 · `test-scenes.js` **58 断言**场景 http 打开 · `test-platform.js` **12 断言** REST/SSE 全链路；跑法 `cd .e2e && node test-<name>.js`（前置 `cd frontend && npm run build`；puppeteer-core + 无界面 Chrome + 本地静态服务顶替 nginx + uvicorn 起真后端）
- 测试图：`test-tifs/`（gitignore）、`frontend/fixtures/`（入库小图）
- 记忆：`~/.claude/projects/.../memory/MEMORY.md`（6 条索引：local-vendor / browser-2gb / intranet-data / real-files-1row-strips / openai-pin / **phase4-disk-array-reads-jpg**）

## 6. 真机一次出差：验收一页纸（CentOS7 盘阵机）

> 目的：把文档里「真机项另排」一次性收口。从零部署 → 阶段4 场景 → 阶段5 调度/掩码/SSE，
> 预计一个工作日。真 LLM **无可用端点** → 标灰为决策点，先验不依赖模型的项。
> 部署细节以 `deploy/README.md` 为准；跑完把每项记录誊回本文件 §5.2 并勾掉。
> 本机（node81-135）实测运行步骤——路径已按 workspace 修正、含 Windows 访问：`docs/status/real-machine-bringup.md`。
> 图例：`[ ]` 待勾 · `⏱` 参考耗时 · `✓=` 成功标准 · `记录:` 实测值（耗时/内存/异常）。

### 6.0 部署进度快照（node81-135，2026-09-07/08 实测）

| 里程碑 | 状态 | 说明 |
|---|---|---|
| nginx 安装（无 yum 源 → U 盘 rpm） | 完成（09-05） | 见 §6.2 与 deploy/README §二 ⚠️ |
| Python≥3.8 venv + 后端依赖 | 完成（09-05） | `/opt/sr-venv`（py3.9）；另需 eval-type-backport（见 §6.2 补注） |
| sr-api 起服务（systemd，开机自启） | 完成（09-07） | 曾 217/USER（`User=` 行尾注释）+ py3.9 注解崩溃，均已修复并归档 |
| nginx 站点 + Windows 访问 | 完成（09-07/08） | `http://10.10.81.135` 可开页面；需删出厂 default.conf |
| /api/scenes 真实数据 | **未完成** | `/data/scenes` 不存在 → `source:fake` 12 条占位；待真实根路径 |
| **SR 最小原型（本机直跑，不走 Slurm）** | **提交通（09-15）· 终态已验 = FAILED（判据正确）· 平台状态未收敛（待核）· 差一个未超分场景** | `SR_EXECUTOR=local` / `SR_LOCAL_GPU=0` / `SR_LOCKED_DIR` 生效；`POST /api/queue` → `201`（`task_id=1` / `job_id=1` / `in_place:true`）。**job 1 判读（09-15 晚）**：审计段证实 `SR_SR_SCRIPT=code_0817_prod_slurm.py`（变体在用）+ `CUDA_VISIBLE_DEVICES=0` + 真实 GPU UUID + conda 解释器起来了 → **提交链路与 conda 调用已通**；但 SR 未干活——场景 9/12 已超分，`util.py:1009-1011` 判 `already SRed before` 提前 `return`、未建新 SRLOG → 校验器判 SRLOG 为残留 → 契约不满足 → **FAILED；契约判据避免了把这次记成假成功**（详见 §4 时间线「2026-09-15（判读）」）。**另：`GET /api/queue` 仍报 `RUNNING`，与退出码文件不一致，待核**。**待办**：① 造一个未超分过的场景真跑一次；② `frontend/dist` 已重打进开发机、**待传**（粘贴板传不了目录树）；③ `SR_SCENES_ROOT` 不覆盖 datahub 目录（**B 段前提**；单根、软链须建在根本身）。工作单 `docs/planning/sr-minimal-prototype-plan.md` |
| ~~提交 SR 端到端（Slurm 路线）~~ | **路线已中止（09-14）** | 代码/脚本/文档仍在（`SR_EXECUTOR=slurm` 分支、变体、校验器、`slurm-acceptance.md` 的 A–D 清单），但**不再按此推进**。**§0 前置四项曾全绿**（上车 / 变体就位 / 13 项 env / 目录与可写性，见 §4 时间线 2026-09-11），A/B/C/D 一步未跑。判据（终态读退出码文件）仍适用于本地执行器 |
| 平台侧沙箱（`SR_SANDBOX_ROOT`） | 完成（09-10 代码侧 · 09-11 真机侧） | 每个作业自建 `lq_path` 副本，SR 只写副本 → 前端填**生产路径**也不会动生产数据。见 deploy/README §7.5。真机目录已建 + `nginx` 可写（`WRITE OK`） |
| ⚠️ `$BUNDLE/code_0817_prod.py` 被覆盖 | **事故待善后** | 09-11 09:55 由 33477B/`cfbf0bda` 覆盖为 28703B/`03c9b4fc`，**旧版无备份、不可恢复**。待答：谁读这份文件？（详见 §4 时间线 2026-09-11） |
| 本会话代码/文档变更归档 | **未 commit** | 清单见下方 |

**下一步行动**
1. **`SR_SCENES_ROOT` 的定位（09-15 定）**：本阶段**不维护场景检索**，优先保证「提交 SR」链路跑通。据此：

   - 这个 env **不能不配**：`/scenes` 盘阵场景页的行来自 `/api/scenes`，而查看器的「提交 SR」按钮只对 `route='jpg' && lqPath` 的记录可用，`lqPath` 又只由 disk 场景行提供——**场景列表是那个按钮的唯一入口**。
   - 但也不必现在就挑生产根。暂定指向 `/DiskArray/tmp/wangrz/datahub/`，nginx `alias` 取同值。该目录下会把同一景列成多行（`_NOSR` / `_mask` / `_old` / `_ori` 等派生件都会被当场景收进来，根因见下条），**本阶段接受**。
   - `SR_PREVIEWS_ROOT` 不设，预览 JPG 写在源文件旁边，正好落在 alias 根内。
   - **与既有记录的关系**：生产盘阵根此前确认为 `/DiskArray/GSHC2IMPS/`（层级 年/月/日/生产编号），`datahub` 更像临时工作区；两者关系（拷贝？软链？）按本阶段目标不必先弄清。此前在 viewer 开过的那张 590MB `JL1KF02B01_...` 来源仍未确认。

   **已知缺口（本阶段不修，先记录）**：`scene_search.is_scene_file()`（[scene_search.py:37-44](../../backend/services/scene_search.py#L37-L44)）的收件规则只有「后缀白名单 + 排除 `.preview.jpg`」，**没有**「文件名与目录同名」或「同目录存在 `<目录名>_meta.xml`」判据（grep 全 `backend/`，`_meta.xml` 只出现在校验器的测试里）。后果：一个含 `.tif/.jpg` 的目录被整体递归扫成场景行，派生件一并计入，同一景出多行且 `parse_filename` 给出的 satellite/sensor/date 完全相同。`_scene_row` 的 `lq_path`（= 场景文件父目录）因此有指错目录的风险——在 datahub 这类扁平目录里各行 parent 相同、侥幸无害。另：`scan_root()` 每次请求全树 `rglob` + 逐文件 `stat()`，无缓存。**B 段跑通后若要恢复场景检索的可用性，先补收件规则，再谈换根。**
2. 改两处并**保持同值**：后端 `SR_SCENES_ROOT`（`/etc/systemd/system/sr-api.service`）+ nginx `alias`（`/etc/nginx/conf.d/sr-agent-platform.conf`）→ `systemctl daemon-reload && systemctl restart sr-api` + `systemctl reload nginx` → `curl http://127.0.0.1:8000/api/scenes` 复核出真实行（非 fake）。
3. 数据就位后按 §6.3–6.5 逐项真机验收。
4. commit 下列待归档变更：`deploy/sr-api.service`（User= 行尾注释修复）、`deploy/requirements-api.txt`（+`eval-type-backport; python_version<"3.10"`）、`docs/status/real-machine-bringup.md` 与 `-adhd.md`（新增症状行）、`deploy/README.md`（真机落地、无 yum 源 ⚠️）、本会话 memory 更新。

> 启动两个坑的完整排障（217/USER、py3.9 eval-type-backport）见 `real-machine-bringup.md` §5 症状表第 2/3 行。

### 6.1 出发前（开发机）
- [ ] **打包拷包**（⏱30′）· 开发机 `npm run build && npm run package:offline` → 离线包 + 后端依赖 wheel 拷 U 盘 · ✓= 包内含 `dist/ backend/ nginx.conf sr-api.service requirements-api.txt` · 记录:
- [ ] **确认目标图**（⏱5′）· 从盘阵挑两张真实大图（GF07A03 1.11GB / KF02B04 1.78GB），记下它们在第几行 · ✓= 知道 scene 名即可 curl 到 · 记录:

### 6.2 首次部署（CentOS7）
- [ ] **装 nginx + Python≥3.8 venv**（⏱30′）· 装 nginx（本机实测**无任何 yum 源**：`There are no enabled repos`，EPEL 不可达 → 内网 yum 镜像 / U 盘 rpm，见 deploy/README §二 ⚠️）；⚠️ CentOS7 自带 `python3`=3.6.8 **不可用**（后端整链要求 ≥3.8，3.6 上 pip 只会解析到 openai 0.10.5 等 2021 旧版）。conda 频道 `cgwx-anaconda` 404 死、`defaults` 断网、本机包缓存不全 → **造不出新 conda 环境**（09-05 实测 `create`/`clone` 均 HTTP 报错）——实际走法：拿现成 py3.8+ 解释器建 venv，`<conda>/envs/destriping_py39/bin/python -m venv /opt/sr-venv`（只借解释器，ExecStart 默认即此路径零改动），再 `/opt/sr-venv/bin/pip install -i http://nexus.jl1.cn/repository/cgwx-pypi/simple --trusted-host nexus.jl1.cn -r requirements-api.txt`（装法与镜像见 deploy/README §三）· ✓= 装完版本正常（实测 fastapi 0.128.8 / openai 1.109.1 / numpy 2.0.2 / pillow 11.3.0，非 0.10.5 时代） · 记录:
- [ ] **放 nginx.conf**（⏱15′）· 拷到 conf.d，按真机改 `root`（dist 目录）、`alias`（盘阵根）、`proxy_pass` · ✓= `nginx -t` 通过 → `systemctl reload nginx` · 记录:
- [ ] **放 sr-api.service**（⏱20′）· 拷 systemd；改 `WorkingDirectory` / `SR_SCENES_ROOT`（=nginx alias 同值）/ `SR_AGENT_DB` 父目录归属 / `ExecStart` venv 路径；**保持 `SR_LLM_MOCK=0` `SR_SLURM_FAKE=0` 不改** · ✓= `systemctl enable --now sr-api` 起来，`chown -R nginx` 库目录可写 · 记录:
- [ ] **探活**（⏱10′）· `curl /api/health`、`/api/scenes`（有行、非 fake）、`/api/queue` · ✓= 三接口 200 且 scenes 返回真实 W/H · 记录:

### 6.3 阶段4 场景验收（真机唯一不可替代项）
- [ ] **场景行元数据**（⏱10′）· curl 目标图对应行 · ✓= `W/H` 与 ENVI 头一致、`jpgUrl` 非空或为 null（未生成前） · 记录:
- [ ] **首访预览生成**（⏱依赖图大小）· curl `/api/scenes/<id>/preview` 并计时 · ✓= 几十秒内返回、期间 `ps` 记 FastAPI RSS 峰值不失控（逐条带只碰采样行） · 记录耗时 / 内存:
- [ ] **二次幂等 + 静态直出**（⏱10′）· 再 curl 同 id preview；curl `/disk-array/<rel>` · ✓= 二次秒回；静态带缓存头 200 · 记录:
- [ ] **浏览器出图**（⏱30′）· Win11 开 `/scenes` → 打开真实场景 · ✓= canvas 出图、文件列表「盘阵」chip、拉伸下拉禁用（tooltip「已烘焙 2% 线性」）、二次秒开 · 记录:
- [ ] **越权拦截**（⏱10′）· 拼 `../` 越权 id / URL 各打一发 · ✓= 403/404；`journalctl -u sr-api` 无越权告警 · 记录:
- [ ] **本地文件回归收尾**（⏱30′）· 真实大图走「选择 TIF…」本地路径 · ✓= 稀疏预览 8192 不崩、画掩码直出正常、2% Linear 导出 8192×8013 无条纹 · 记录:

### 6.4 阶段5 调度验收（SR_SLURM_FAKE=0 真调度）

> ⚠️ **判据已改（09-10，取代原文的 squeue/sacct）**：真机 `AccountingStorageType=none`，`sacct` 永久
> 不可用，**终态一律读作业写的退出码文件** `<DatarootLQ>/Debug/_SREXIT_<job_id>.txt`
> （`verdict=0` → COMPLETED；其中 `skip=1` 的云量跳过也是 COMPLETED，`verdict!=0` → FAILED）。
> 四条链路结论（静默失败不再被标成成功 / 幂等回归 / 云量跳过 / SSE）的分步命令 + 成功判据 + 失败处置
> 见 **`docs/status/slurm-acceptance.md` §D**（本节保留一页纸勾选，细节不在此重复）。

- [ ] **前置：装变体 + 九项 env**（⏱30′）· 按 `deploy/README.md` §7.1——变体与 `verify_sr_run.py` **并置**在 `$SR_BUNDLE_DIR`（**不覆盖** `code_0817_prod.py`，靠 `SR_SR_SCRIPT` 指过去）+ 核对 `SR_PYTHON` / `SR_BUNDLE_DIR` / `SR_SLURM_WORK_DIR` / `SR_SLURM_PARTITION` / `SR_SLURM_TIME` / `SR_SLURM_CPUS` / `SR_SR_SCRIPT` / `SR_VERIFY_SCRIPT` / `SR_SANDBOX_ROOT`（末项装法见 §7.5：建目录 + `chown nginx`）· ✓= `head -3 code_0817_prod_slurm.py` 见到 `GENERATED FILE` 横幅、`code_0817_prod.py` 未被改动；`probe_slurm.sh` 除 sacct 两行外无 FAIL · 记录:
- [ ] **真实提交 + 推进**（⏱20′）· `/queue` 手填 lq_path + **非空 suffix** 提交。**配了 `SR_SANDBOX_ROOT` 就可以直接填生产路径**——平台在作业第一步自建副本，生产目录只读（§7.5）；没配就填测试目录 · ✓= sbatch 起真实作业，徽标按 提交中→排队/运行中→**完成** 推进；`/api/queue` 的 `run_dataroot` 指出产物位置，其状态与退出码文件一致；**提交前后 `lq_path` 目录 `ls` 一致（没多出 `*_NOSR.tif`）** · 记录 job_id / 耗时:
- [ ] **静默失败不再被标成成功**（⏱15′）· 构造一个必然缺 SRLOG 的任务（如 SC 步目录里没有 `<目录名>.tif`）· ✓= 退出码文件 `verdict=90`、平台显示 **FAILED**、同参数重投返回 `SUBMITTED`（**不是** `RESUMED_COMPLETED`）· 记录:
- [ ] **幂等回归**（⏱10′）· 同参数连投两次 · ✓= 活跃期 `RESUMED_ACTIVE`、终态 `RESUMED_COMPLETED`，**两次都不产生第二个 job_id** · 记录:
- [ ] **云量跳过**（⏱10′）· 提交一个 `cloud_limit` 必然触发的任务 · ✓= COMPLETED、SRLOG 末行 `Run skipped:`、再投为 `RESUMED_COMPLETED`（**不被反复重投**）· 记录:
- [ ] **取消**（⏱10′）· 提交一个会排队的作业 → 点取消 · ✓= scancel 生效、状态变失败/取消 · 记录:
- [ ] **重启校准**（⏱10′）· `systemctl restart sr-api` → 刷新 /queue · ✓= 内存缓存丢后 GET /api/queue 当场校准（读退出码文件），已完成作业仍显示 完成 · 记录:

### 6.5 阶段5 掩码→SR 真链 + SSE 长连
- [ ] **掩码落盘 + ENVI 核对**（⏱30′）· 真实场景画矩形掩码 →「提交 SR」→ 落盘原图目录 · ✓= ENVI 打开 `<stem>_mask.tif` 区域位置正确（JPG 上画→全分辨率落点）+ `_mask.txt` 质心可读；顺带用带 MaskPath 的真作业确认 0817 消费链一致 · 记录:
- [ ] **SSE 长连**（⏱30′，可挂后台）· 临时开 `SR_LLM_MOCK=1` 或挂 `/api/queue/events`，经 nginx 连 10min+ · ✓= 事件逐帧到达不攒批、断链后前端重连正常、无 502 · 记录:

### 6.6 决策点（标灰，不阻塞；当天记结论）
- [ ] **LLM 底座拍板** · 内网有无第二台 GPU → vLLM+Qwen / Ollama+Qwen / 第三方端点（§2.6⑤）· ✓= 出结论即可 · 结论:
- [ ] **上线还差的两件事记账** · 真 LLM 复测一轮 /chat（配好端点后）；产物回到用户（队列「完成」之后的目录/对比/失败诊断）+ 多人权限要不要（M2）· ✓= 列进 §5.2 · 结论:
