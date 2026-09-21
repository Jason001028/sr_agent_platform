# current_question — 当前问题解决时间线（显示遥感图像）

> 用途：交接文档。下一个 Claude 会话先读本文件，再读 `docs/experience/gui-experience.md`（踩坑经验），即可无缝接着干。

---

## 1. 当前状态（摘要）

- **查看器已可用（08-29）**：`tif_viewer/tif-viewer.html` 能正常打开、预览本地遥感图像，用户实测等待时间可以接受。
- **真实文件结构已确认**：GF07A03（1.11GB）与 KF02B04（1.78GB）都是**无压缩、每行一个条带（strip）**的单波段 16bit 影像。因为没压缩、条带又很规整，就不用做金字塔（预先生成多级缩小图来加速浏览），直接采用**稀疏条带预览**（只抽样读取部分行和列，秒级出图；134MB 测试图从整图解码 4.5s 降到 1.3s，预计真实 1.1GB 文件从约 2 分钟降到秒级）。
- **预览方案拍板（09-01）= JPG 中间产物**：网页/预览显示导出的 JPG（长边 8192 像素 ≈ 原图 1/3），放弃了在浏览器内对原始 TIF 做全分辨率切片读取的做法（决策记录见 §3.4）。**尺寸与拉伸规则已于 09-17 改成 v2：各边 1/2 + 直方图均衡**（见本文件末条摘要与 §4 该日条目）；正文描述的 8192 是当时的规则，浏览器侧导出链已在 09-14 整体删除。
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
- **下一步**：① 真机**三项只读确认**（工作单 §3：锁定目录拼写 `datahub`/`databub`、`SR_SR_SCRIPT` 是否指到变体、前端产物送机通道）；② 按工作单 §6 追加 env（`SR_EXECUTOR=local` / `SR_LOCKED_DIR` / `SR_LOCAL_GPU` / `SR_SR_SCRIPT`→变体 / `SR_PYTHON`，**`SR_SANDBOX_ROOT` 必须不设**；此处原列的 `SR_SUFFIX_DEFAULT` **2026-09-16 已作废、不要设**，后缀默认值改由平台读 SR 配置文件里的 `<Suffix>`）后跑 §5.2 A/B 段验收；⚠️ 验收前**重新拷一次** `SR_code/variants/verify_sr_run.py`（已改为 17164B/`5fa627d8…`）；③ 定位真实场景根 → `SR_SCENES_ROOT` 与 nginx `alias` 同值 → 复核 `/api/scenes`（进度 §6.0）；④ 配 LLM key 跑通真实 /chat 闭环（§2.6）；⑤ 开发机推进 P1 ④⑤⑥⑦（§2.5）——开发机侧**已无已知红项**。
- **约束提醒**：开发机的浏览器 e2e 测试**已恢复可用**（`.e2e/launchBrowser.js` 每次用独立的临时浏览器配置目录；**候选顺序 Chrome 优先**——本机 Edge 与正在运行的 Edge 实例握手会 `Code: 0` 闪退）；真实图片都在内网盘阵，外网开发机读不到（见 §5.1）。
- **遗留三：Slurm 接入待真机执行（09-10，第三批）**：探针（P1）与实测参数（P2）已回传，**本轮交付的是执行侧**——部署变体（`SR_code/variants/code_0817_prod_slurm.py`，锚点替换生成）+ 作业内契约校验器（`verify_sr_run.py`，退出码 0/90）+ 批脚本（`--gres=gpu:1` / `--export=NONE` / 两行调用）+ 退出码文件定终态（`slurm.py` 不再依赖 `sacct`）+ 分阶段验收清单（`docs/status/slurm-acceptance.md`，A 探针→B 裸 Slurm 冒烟→C 单场景真 SR→D 平台四条结论）。**真机命令由用户执行、输出贴回后判读**；文档侧已回填契约 v1.5 与 `deploy/README.md` §七。待跑：验收单 A/B/C/D。**09-14 需求收敛**：所有作业**只跑在同一台 4×3090 物理机内**、不调度到其他服务器，Slurm 的角色收窄为「本机排队 + 按单卡分配 GPU」；部署方向二选一（① 复用现有集群 + `--nodelist` 锁定那台机器 / ② 本机自建单节点 Slurm），**方向未定**，判据 = `slurm-acceptance.md §B0` 的三条只读命令（已有半条反证：A 记录里 `node81-133/134/135/136` 是 `down`，本机 node81-135 是 `down*`）。
- **SR 最小原型首跑（09-15，本机直跑 SR，不走 Slurm）**：需求收敛为「前端点提交 → 后端在 node81-135 上用**生产 conda 解释器直接跑 SR**，读锁定目录里的 `.tif` + 目录里已有的 `<目录名>_mask.tif`，产物写回同目录」。代码侧八项已落地（执行器二选一 `SR_EXECUTOR=slurm|local`、`SR_LOCKED_DIR` 路径锁、掩码改目录推导、前端删「输出目录」+ 自动 JPG 链路、放宽本地 `.jpg`、盘阵 `.jpg` 场景可列出）。**09-15 首次提交成功**：`POST /api/queue` → `HTTP 201`、`task_id=1` / `job_id=1` / `state=SUBMITTING` / `in_place:true`。**终态已验（09-15 晚）= FAILED，判据正确**：作业确实跑完了，审计段证实 `SR_EXECUTOR=local`、`SR_SR_SCRIPT=code_0817_prod_slurm.py`（变体在用）、`CUDA_VISIBLE_DEVICES=0` + 真实 GPU UUID、conda 解释器真的起来了——**「后端能调起 conda 解释器」已由实测确认**；但 SR 自身**没干活**：场景 9/12 就已超分过，`util.py:1009-1011` 判定 `already SRed before` 后提前 `return`（`exit()` 被注释掉，源码标 `# huai`），未建新 SRLOG；校验器发现 SRLOG 早于 config.xml、判为上次残留 → 契约不满足 → 退出码文件写非 0 verdict。**契约判据在此首次生效**：若沿用 sacct 时代的判据，这一次会被标成 COMPLETED 并固化。**新阻塞 = 需要一个未超分过的场景**才能真跑出产物；工作单 = `docs/planning/sr-minimal-prototype-plan.md`，实况见 §4 时间线 2026-09-15。**另有一处未收敛**：退出码文件已判 FAILED，`GET /api/queue` 仍报 `RUNNING`（详见 §4 时间线判读节第 3 条）。
- **下一步**：① **A 段收尾只剩一件事：造一个未超分过的场景**——job 1 已证「提交链路 + conda 环境 + 变体 + 单卡绑定」全通，缺的只是让 SR 真干活（现场景 9/12 已超分 → `already SRed before` 提前 return）。二选一：换一个 `<目录名>.tif` 体积落在 `util.py:1006` 三个区间内的场景目录；或把该目录的 `<目录名>_NOSR.tif`（= 9/12 前的原始输入）改名还原回 `<目录名>.tif`（**先备份现有的超分产物**）。跑通后再定要不要重打/传 `frontend/dist`（**09-15 已在开发机重打完成**，见 §4 时间线）；② `SR_SCENES_ROOT=/data/scenes` 不覆盖 datahub → 场景列表看不到它（`/scenes` 页是「提交 SR」按钮的唯一入口，所以这个 env 必须配）。**09-15 定：本阶段不维护场景检索**，只把根指向 `datahub` + nginx `alias` 同值、接受同景多行，优先保证提交链路通；收件规则的缺口与后续修法见 §6.0「下一步行动」第 1 条（`SR_SCENES_ROOT` 是**单根**，没有多根写法；符号链接**只有建在根本身**才通过 `paths._is_within` 的 realpath 比较，建在根内会被判越权）；③ 定位真实场景根 → 改 `SR_SCENES_ROOT` + nginx `alias`（同值）→ 复核 /api/scenes（进度 §6.0）；④ 数据就位后按 §6.3–6.5 真机验收；⑤ commit 待归档变更（清单 §6.0）；⑥ 配 LLM key 跑通真实 /chat 闭环（§2.6）；⑦ 开发机推进 P1 ④⑤⑥⑦（§2.5）。
- **约束提醒**：开发机的浏览器 e2e 测试**已恢复可用**（`.e2e/launchBrowser.js` 每次用独立的临时浏览器配置目录，彻底解决「浏览器闪退、退出码 0 无任何报错」的问题）；真实图片都在内网盘阵，外网开发机读不到（见 §5.1）。
- **盘阵任意场景目录（09-17，开发机）**：`SR_SCENES_ROOT`（datahub）之外的合法场景目录（真机生产树 `/DiskArray/GSHC2IMPS/PRODUCT/<年>/<月>/<日>/<生产编号>`）现在也能在查看器打开、画掩码、写回该目录、就地提交 SR，不必先把数据搬进 datahub。两个入口（场景库页检索条下的输入框、查看器工具栏的「盘阵场景」栏）共用 `ScenePathBar.vue`，用户粘 Windows 形态 `W:\…`，后端按 `SR_DRIVE_MAP`（默认 `W:=/DiskArray`）译成服务端形态；`SR_ALLOWED_ROOTS`（默认 `/DiskArray`）做前缀白名单。**绝不扫盘**：只 `stat` 用户给的目录，测试用 `patch(Path, "rglob"/"glob"/"iterdir")` + `os.listdir`/`scandir`/`walk` 打成 `AssertionError` 钉住。新增 `POST /api/scenes/resolve`（§3.5）、`backend/pathguard.py`；同时修正一处会必然 400 的缺陷 —— `bake_mask` 按输入影像命名、`derived_mask_path` 按目录名命名，RC（`PAN.tif`）场景两者不同名，现由 `scene_search.mask_stem` 单点同源。验证：后端 454 passed / 1 skipped、前端 177 passed + `vue-tsc` 零错误、e2e `test-manual-scene.js` **37 断言**（新）与 `test-scenes.js` 61 / `test-platform.js` 18 无回归。真机待确认 5 项见 §4 该日条目（**第一项 `SR_LOCKED_DIR` 未清会让所有新场景提交 400**）。
- **反推模板修正为生产树六层（09-17，开发机）**：上一条的「拖入本地 `.jpg` → 自动关联盘阵目录」在生产树上**永远落空** —— 反推模板是 `PRODUCT\{y}\{m}\{d}\{name}`（四层），真机是 `PRODUCT\<年>\<月>\<日>\<卫星型号>\<段级目录>\<景级目录>`（六层），少两层。按用户给的 9 段生产命名规范做成纯词法反推：`{sat}` = 名字第 1 段，`{mid}`（段级）= 景级名去掉景号那一段，`{y}/{m}/{d}` 取 14 位成像时刻前 8 位。顺带收口一处双真源：模板原先前后端各写一份，前端写死的那份让 `SR_SCENE_PATH_TEMPLATE` **从未生效**；现只留在 `backend/pathguard.py`。验证：后端 **464 passed / 1 skipped**、前端 171 passed + `vue-tsc` 零错误 + `npm run build`、e2e 39/61/18 断言。真机待确认：「段级 = 景级去景号」目前只有一组真机样本，其它卫星/产品待核。规则全文 [docs/sr_code/production-scene-naming.md](sr_code/production-scene-naming.md)，时间线见 §4。
- **队列页三处前端缺陷修复（09-17，开发机）**：① 「耗时」列在 SR 跑完后从正常数值掉成「0 秒」—— 根因是 SSE `job_update` 帧不带 `updated_at`，前端那份时间戳冻结在提交时的 GET 快照（`updated_at == created_at`），运行中靠本地现算看不出、一进终态就归零；现由 `store.set_sr_task_state` 返回写入的时间戳、`_task_state` 广播时带出、前端 `mergeJobUpdate` 一并写回（写库失败则不发，避免界面与库对不上）。② 耗时列与创建时间列加 `nowrap` + `tabular-nums`，不再折行；参数列加 `overflow-wrap: anywhere` 吸收宽度。③ 新增 `--page-w: 1360px` 令牌，场景库 / 队列 / 聊天三页同宽（原先 1360 / 1240 / 1240，各写各的）。验证：后端 **466 passed / 1 skipped**、前端 **175 passed** + `vue-tsc` 零错误 + `npm run build`、e2e 39/61/**21**（`test-platform.js` 新增 3 条断言并做过反证：摘掉前端写回即红在「页内 0 秒 / 接口 1 秒」）。**交付须同时更新 `dist` 与 `backend` 两个包**，只换 dist 修不好。同日续：那条页宽断言当时只比 `max-width` **字符串**、且跑在 800px 默认视口下（那个宽度里三页都被视口压扁，等于没测），已改成 1600 视口**实测正文盒** + 「顶住 `--page-w` 上限」两条，`test-platform.js` 现 **22** 断言；同时发现盘上 `dist` 停在 09-15（产物仍是 1180/1240/1240），已重建。时间线见 §4「页宽断言加严」条。
- **盘阵场景拉伸放开（09-17，开发机）**：盘阵 JPG 的拉伸下拉原先是**禁用**的、固定停在「2% 线性」（`Toolbar` 的 `:disabled` + `paintStretch` 对 `route==='jpg'` 直接早退），场景图一个模式都换不了 —— 而同一条像素管线的本地 JPG（`route==='img'`）从来可以随便拉，闸门给的理由（「别对已烘焙图二次拉伸」）在数据形态上不成立。现按用户决策放开：**盘阵场景以直方图均衡起手**（`lib/scene.startStretch`，换起手值只改这一个常量），拉伸成为**每张图各自的属性**（`rec.paintedMode`：切走再回来不重置、图与图之间不互相覆盖），且场景改模式**不写回全局**，免得看过一张场景图就改掉本地 TIF 的起手值。代价：服务器按 2% 裁掉的两端拉不回来。验证：前端 **178 passed** + `vue-tsc` 零错误 + `npm run build`、e2e 39/**65**/21（`test-scenes.js` 新增 4 条断言，反证过：把 jpg 早退回填即红在「下拉动了、画面没动」）。**真机待确认：8192 长边场景每次打开/换模式都要重算一遍全图，性能与内存需实测**（开发机 fixture 只有 1600×800）。时间线见 §4。
- **烘焙规则 v2（09-17，开发机）**：服务端烤的预览图由「长边封顶 8192 + 2% 线性」改成「**长宽各为源图的 1/2 + 直方图均衡**」，且**能粘单个 `.tif` 文件路径**打开（裸 TIF 在盘阵任意目录都行）。查看器/场景库不再直接阅读原始 TIF，一律先读这张同目录的 `<stem>.preview.jpg`。规则戳写进 JPEG 注释（`srprev:v2:half+equal:q85`）—— 只看 mtime 的话，真机上换包后旧图 mtime 比源新会被判有效而**永不重烤**。顺带修掉两个真 bug：Pillow 的 `MAX_IMAGE_PIXELS` 默认上限会让 1/2 尺度的大图（1.5 亿像素）抛 `DecompressionBombError` → 缓存永远判不中 → 每次打开都重烤；裸 TIF 会被 `tryLinkScenes` 拿裸文件名反推目录（可能命中**另一个**目录，提交 SR 就是错的）。验证：后端 **490 passed / 1 skipped**、前端 **183 passed** + `vue-tsc` 零错误 + `npm run build`、e2e **48**/65/22/35 全绿。**真机待确认：① 12370×12100 的浏览器内存（峰值可能 1.2–1.8GB）② q85 下 100.7MB 的下载耗时 ③ 单块 HDD 上 8 线程是否反而更慢**。时间线见 §4。
- **队列「耗时」改成本次运行时长（09-18，开发机）**：用户报「耗时列出现大几十个小时，实际单次最多 200 多秒」。定位到耗时量的是**行的年龄**（`updated_at − created_at`），两个触发点都实测复现。① **复用行**：`sr_tasks` 按 `task_fingerprint` 唯一，同场景同参数重交会复用同一行、真重跑，而 `created_at` 停在**第一次**提交的时刻。② **后端重启**：校准器的比较基准 `state.task_cache` 是内存态，重启后为空 → 每行都被判成「状态变了」→ 写回 + 刷时间戳，几天前跑完的行集体变行龄（**真机上部署一次就中一条**）。按用户决策把口径改成**纯算力、不含排队**：新增 `started_at`/`finished_at` 两列（首次观测到 RUNNING → 终态落库），耗时 = 两者之差；重启时基准回落到库里存的状态；老库 ALTER TABLE 补列、**不回填**（老行显示「—」）。验证：后端 **539 passed / 3 skipped**、前端 **190 passed** + `vue-tsc` 零错误 + `npm run build`、e2e 全绿。时间线见 §4。

- **像素定位框收「X,Y」整串（09-18，开发机）**：查看器那两个 `type="number"` 框会把「30766.11,21862.51」
  判为非法、值直接变空，粘进去等于什么都没发生。改成 text + `inputmode="decimal"`，粘进来自动拆到 X/Y
  两框（逗号半角全角、空白都认）；**恰好两个数**才认，三个数（连编号一起拷的整行掩膜记录）原样留着并
  报「坐标对认不出来」，绝不截一半去定位。验证：vitest **196**、vue-tsc + build、e2e `test-vue-viewer`
  **38**。时间线见 §4。

- **工具页文字精简 + 查看器左栏加宽（09-18，开发机）**：按用户列出的四处收掉说明文字 —— 场景库表下那段
  「场景 JPG 为服务器烘焙…」整段删、队列页标题的「SR 作业（slurm / 假调度器）」（口径已过时，现走本机直跑）
  与提交按钮旁那段「提交是真实副作用…」一起删、查看器右栏「统计作用于当前显示层…」一行删（云量的免责注解
  保留）、盘阵场景栏那条长提示压成两行并把「W:\ 形态」改写成「路径认两种写法：W:\…（盘阵）与 /DiskArray/…」。
  另把查看器左栏加宽（生产全名与尺寸/布局说明挤在一起），最终与右栏一起定在 400 —— 见下一条。验证：vitest **196**、
  vue-tsc + build、e2e 65/22/38/83 全绿，主题截图逐页看过。时间线见 §4。

- **查看器接入《待修复清单》（09-18，开发机）**：ROI/工具 组置顶新增清单面板 —— 导入质检部门的标准两段式
  .txt（上半部分问题行**逐字保留**、下半部分是我们自己补的处置结果），逐条看伪影的行列号 / 影像类型 / 责任人；
  选中行走三段式进度条（已绘制掩码 / 已提交任务 / 已修复）+ 人工终态（驳回 / 非模糊通过），页脚「同步至指定
  文档」原地覆盖写回（上半部分与原文逐字相同、下半部分**只含终态行**，未完成的行省略、中间态不落文档）。打开
  场景按目录名自动选中对应行、提交 SR 自动推进到「已提交任务」。同时左右栏都由 268 → **400**（对齐），左栏文件卡加
  小图预览横幅。验证：vitest **229**、vue-tsc + build、e2e 22/65/83/58 全绿。**待留意：两栏都开时 chrome 占
  852px，1366 屏上画布只剩 ~510px**。写盘那一步当天已从 FSA 改走后端（见 §4 末段），不再靠「真机手验
  一个点不通的入口」。时间线见 §4。

- **图名日期与目录日期差一天（09-18，开发机，用户报的重大 bug）**：用户报「图名含 0917 的、大部分所属目录
  为 0918，导致大部分图匹配不到盘阵」。根因是**日期取错了口径**：`infer_scene_paths` 拿文件名里那 14 位
  **成像**时刻当目录日期，而盘阵按**生产日**建目录，夜里成像的景记在第二天（`…_20260917124710_…` 躺在
  `2026/09/18/` 下）—— 只拼成像日那一条路径，于是大半落空。现每天给两条候选：**成像日在前、次日在后**，
  两天都在就命中成像日；404 的 `detail` 末尾补一句「成像日与次日都找过 —— 盘阵按生产日建目录，深夜成像的
  景常记在次日」。同一条弹窗里那个「第二个目录」是**旧扁平形态**兜底候选（`PRODUCT\<年>\<月>\<日>\<景级名>`，
  只有四段），生产树上永远落空、只会把人看懵 —— 按用户指示**整条删掉**，平铺部署从此只能用路径打开（诊断
  措辞仍由 `flat_scene_layout` 按层数认出）。验证：后端 **545 passed / 3 skipped**、e2e `test-manual-scene`
  **83** 断言全绿（夹具已从平铺改成六层生产树）。文档同步：`docs/sr_code/production-scene-naming.md` §三/§四、
  `docs/planning/api-contract.md` §3.5、`deploy/sr-api.service` 的模板注释。时间线见 §4。

- **预览烘焙档位改成用户可调（09-19，开发机）**：服务端烤预览的尺度原先是写死的各边 1/2（2.4 万像素级的源
  烤出来 1.5 亿像素、q85 上百 MB，首次打开要读一遍整幅大图）。现改为工具栏**定位组件右侧**一条 5 档拖动条
  （各边 ÷2 · ÷4 · ÷8 · ÷16 · ÷32，**默认 ÷4**），作用域是**全局** —— 拖入 / 场景库 / 粘盘阵路径三条入口
  都按当前档位烤；档位进规则戳（`v2` → `v3`：`srprev:v3:div<N>+equal:q<Q>`），换档即重烤。同时拖入链的产物
  从临时缓存改成**盘阵场景目录里的 `<stem>_preview.jpg`**（端点随之改名 `preview-tmp` → `preview-drop`），
  不再是「当天有效、次日删」，同一场景反复打开不再重烤；临时缓存**保留为兜底**（场景目录写不进去时才用，
  回响应头 `X-SR-Preview-Fallback: tmp`）。行上新增 `previewDiv`（读 JPEG 注释戳）—— 没有它，`hasPreview`
  认不出档位，滑了滑块还会端着旧档位那张图上桌。验证：后端 **567 passed / 4 skipped**、前端 **238 passed**
  + vue-tsc + build、e2e 105/65/58/22 全绿。**换包须知：盘上旧缓存（`v2` 戳）会逐个场景首次打开时惰性重烤
  一轮；`frontend/dist` 与 `backend` 必须同包更新（端点改名，只换一个会 404）**。时间线见 §4。

- **三类图服务端烘焙 + 产物急烤 + 显示源比较规则（09-20，开发机）**：为查看器的**对比**功能铺服务端这一半
  （对比 UI 本轮不做，用户随后单独提需求）。三件事：
  ① **三类图各有落点、不新增烘焙端点** —— 输入影像 `<stem>.preview.jpg`、本次产物 `<stem>_<suffix>.preview.jpg`、
  上一次产物 `<stem>_<suffix>_NOSR.preview.jpg`，各拿自己的场景 id 调现有 `GET /api/scenes/{id}/preview?div=N` 即可。
  命名推导（`product_candidates` / `nosr_path_for` / `sibling_raster_path`）全部落在 `scene_search.py`，
  与 `input_candidates` 同纪律：**只拼名字 + 只 stat，绝不列举目录**。新增只读端点
  `GET /api/scenes/{id}/siblings`（§3.8）把三类图一次交代清楚（`suffix` 三级取值 `?suffix=` → 最近 COMPLETED 任务
  → 配置缺省，`suffixFrom` 如实回报是哪一个；找不到也返回 `exists:false` + `productCandidates`）。
  ② **产物急烤**：作业转 COMPLETED 后由后台循环顺手烤掉产物那份（只烤产物，输入与 `_NOSR` 不烤）。**不挂在状态
  转换上、改成从库派生**——`platform._task_state` 有后台 `_poll_once` 与请求路径 `_task_view` 两个调用者，挂那里必然
  偶发漏烤；改成「`status='COMPLETED' AND preview_state IS NULL`」后竞态在结构上不存在。两列 `preview_state`/
  `preview_note` 随 `GET /api/queue` 带出，广播走新帧 `preview_update`（不混进 `job_update`）。状态机
  `NULL → running → done|skipped|failed`，slug 六个（`no_suffix`/`sandbox`/`product_missing`/`unwritable`/
  `source_changed`/`failed`）。两个 env：`SR_PRODUCT_PREVIEW_DIV`（默认 4、**0 = 关**、非法值当 0 不抛异常）与
  `SR_PRODUCT_PREVIEW_MAX_AGE_SEC`（默认 86400）；它们与前端那个「默认 ÷4」是**两个独立的 4，互不联动**。
  ③ **显示源比较规则（工作流 B）**：拖入/打开的 `.jpg` 显示件在同目录有位更清晰的栅格、且
  `round(max(rasterW,rasterH)/div) > max(jpgW,jpgH)`（**严格大于**）时，显示源换成服务端从栅格烤的那份；
  `/preview` 与 `/preview-drop` 各插一次同名栅格探测，命中就把源换成栅格再往下走（落点、`?div=`、兜底头全部照旧）。
  jpg 行因此多一个**只读**字段 `rasterPreview`；**`hasPreview`/`jpgUrl`/`previewDiv` 的语义一个字未动**（§9.2 红线）。

  **必须知道的三件事**：
  - **默认档位 ÷4 下工作流 B 基本不触发**，这是算术不是 bug：24000 源 + 8192 显示件时 ÷2 → `12000>8192` 赢、
    ÷4 → `6000>8192` 输。验收口径只能写这条比较规则本身，**不能**写「jpg 行一律走服务端」；现有 e2e 夹具在 ÷4 下
    全部判 jpg 赢，那些断言**一字不改地继续绿**（这就是本次的回归钉子）。
  - **升级日策略**：两列走 `_ensure_columns`（幂等 ALTER、**不回填**），老行的 `preview_state` 全是 `NULL`；唯一的
    屏障是年龄窗口（`finished_at >= now - SR_PRODUCT_PREVIEW_MAX_AGE_SEC`）。所以升级当天**先用
    `SR_PRODUCT_PREVIEW_DIV=0` 起一次确认无异常，再打开**。
  - **沙箱与急烤**：判据必须是 `platform._run_dataroot()`，**不能直接比 `SR_SANDBOX_ROOT`** —— 真机当前是
    「配了 `SR_SANDBOX_ROOT` + `SR_EXECUTOR=local`」这条路线，而 `run_sr.sandbox_scene_paths` 在 local 下恒返回
    `None`，直接比 env 会把本可以烤的产物判成沙箱而**永不烤**（有专门的回归钉子钉这条）。切回 slurm 提交时，
    跑在沙箱私有副本上的任务会自动 `skipped: sandbox`（正常结局，不是故障）。

  **留作下轮议题（不在本轮范围）**：一个场景在库里有 `<目录名>.jpg` 与 `<目录名>.tif` **两行**（收件白名单两样都收），
  工作流 B 之后**两行预览完全一样**（共用同一份 `<stem>.preview.jpg` 落点），且同一份落点会被不同档位的客户端
  互相顶掉（div 抖动）。本轮在文档里点明，不装作没有；去重与带档位落点都留给对比 UI 那一轮一起定。

  验证：后端 **632 passed / 4 skipped**、前端 vitest **254 passed** + `vue-tsc` 零错误、e2e
  `test-manual-scene.js` **114** / `test-scenes.js` **79** / `test-platform.js` **23** 断言全绿。
  `test-platform.js` 那条急烤断言钉的是**跑出来的事实**：假调度器只推状态机、**不写任何产物 tif**，所以该行落在
  `skipped` + `product_missing: …都不存在`（**不凭猜写 `done`**；也未钉后缀字面量 `sr`，它来自 SR 配置文件，
  钉死会把环境差异报成回归）。**真机待确认**：`<输入名>_<suffix>_NOSR.tif` 这个拼法是从 `SR_code/util.py` 推的、
  尚无真机实证 —— 缓解是候选名做成元组（加名字是一行的事）+ `/siblings` 回报 `productCandidates`，拿到
  `ls -l` 后校准。文档：契约新增 §3.8 / §4.5 / §4.6（挂起项三记录本轮的流程偏差：代码与文档同批落地）。

- **查看器「图像对比」（09-20，开发机）**：用户需求 = 工具栏加「图像对比」入口，三种模式（关闭 / 点选对比 /
  分屏对比，默认关闭）。分屏是「左右各一张、滚轮与拖动同步作用两侧、拖进来的图落在鼠标所在那半」；点选对比是
  「拖进来即覆盖当前这张，再在清单里来回切」。已按批准的计划（`~/.claude/plans/dapper-booping-parrot.md`）交付完。

  ① **分屏 = 一个画布 + 两个裁剪矩形**（不是两个 canvas）。渲染时 `save → rect+clip(pane.rect) →
  translate(rect.x, rect.y) → drawImage`，而**每格的 `ViewState` 用该格自己的局部坐标** —— 正因为这样，
  `fitView`/`locateView`/`visibleThumbRect`/`mouseToThumb` 全部原样复用。`view` 改成按侧的可写 computed
  （`get`/`set` 看 `activeSide`），**`activeId` 仍是「活动侧那张」**，所以掩码/ROI 统计/云量卡/任务状态/
  待修复清单选中/状态栏/工具栏可用性约 40 处消费点**一处未改**。滚轮不改活动侧（否则缩放时掩码与云量卡会闪）。
  ② **落点决定进哪格**：`onDrop` 按落点算 `side`（左半 `'A'`/右半 `'B'`，正好压在分隔线上算右格），
  `addFiles(files, side)` → `placeRec(id, side)` 一条规则贯穿；不 split 时（关闭 / 点选对比）**只有一个格子**，
  于是「点选对比拖进即覆盖」不需要任何特判。点选清单挂在《待修复清单》上方，进对比模式播种当前文件列表全体，
  点行换到活动侧，「清除」只把条目移出清单（文件列表条目、像素、屏幕上那张都不动）。
  ③ **分隔线可拖 + 「回正」**：拖只改比例**不重新适配**；回正 = 比例回 0.5 **且两侧重新适配**；比例持久化在
  `sr.viewer.cmpSplitRatio`（对比模式本身**不持久化**，刷新回关闭）。进分屏自动收起右侧栏、退出恢复用户此前的手动状态
  （`ctxRailOpen`/`ctxRailPrevOpen` 从 `ContextPanel` 提升到 store）。
  ④ **对比模式只读**：不画掩码、不建 ROI（`enterDraw` 加守卫、切模式时 `exitDraw`）。
  ⑤ **拖放门**：关闭模式**全窗口行为一字不改**（拖到侧栏也能打开）；对比模式下只收画布内的影像，画布外给 toast；
  `.txt` 在任何模式、任何位置都照旧喂待修复清单；`dropEffect` 任何情况都保持 `copy`（改 `'none'` 会抑制
  `drop` 事件，把 `.txt` 一起吞掉）。契约新增 §4.7 记这条模式差异。
  ⑥ **三类图快捷入口接上**：`apiSceneSiblings` 客户端 + 对比条上三枚芯片（输入影像 / 本次产物 / 上一次产物），
  绑**活动侧 rec 的 `sceneId`**（本地文件 → 整排禁用并说明原因）；拿到 item 的 `id` 走现有
  `GET /api/scenes/{id}/preview?div=N`，**没有新增取图路径、没有新增烘焙入口**。产物与 `_NOSR` 拖进来过不了
  后端身份门（`_fingerprint_mismatch` 拿输入影像 stem 比文件名，这两类永远对不上 → 必然 404），芯片是它们的廉价通道。
  契约新增 §3.8.1 记前端消费口径。

  **本轮抓出一个真 bug（已修，值得记住）**：同步缩放第一版把锚点算成了「画布局部坐标」
  （`anchorAt(rect,u,v) = rect.x + u*rect.w`），而每格的变换是**格局部的** —— 右格的 `ViewState.ox` 里没有那个
  左格宽，于是滚轮一滚，非指针那格整体平移一个左格宽、图飞出视野。**我自己的单测当时是按错语义写的，全绿**；
  修法是删掉 `anchorAt`、换成 `anchorAtLocal`，参数按**角色**命名（`pPane`/`oPane` + 显式 `pointerSide`），
  并在单测里补一条反向断言（旧公式与新公式至少差 1）。详细复盘见 `gui-experience.md` §10。

  **单屏快速路径保留字面写法**（`render()` 不 save/clip/translate，`onWheel`/`onPan` 不进新数学）：
  归一化往返是「一次除法 + 一次乘法」，`100/1314*1314 = 99.99999999999999`，而 `test-vue-viewer.js` 的 D/E 段
  直接采样画布中心 60×60 像素断言位图 —— 把单屏塞进新数学会让一整套既有断言要重写，收益为零。新数学只服务分屏。

  验证：前端 vitest **288 passed** + `vue-tsc` 零错误 + `npm run build`；e2e `test-vue-viewer.js` **122**（H 段
  64 条新断言，含真 DragEvent 的落位提示 / 落右半 / 落画布外 / `.txt` 恒通 / 同步缩放平移 / 分隔线 / 点选清单）、
  `test-manual-scene.js` **131**（J 段 17 条新断言：真后端下拖放落右半 → resolve +1、`/preview-drop` +1、
  生产那条 `/preview` +0、新图落在右格、只发 dragover 时**请求数 0**）、`test-scenes.js` **79**、
  `test-platform.js` **23**；后端**零改动零回归**：`632 passed / 4 skipped`（与基线一致）。
  新夹具首次纳入 `.e2e/lib/drag.js` —— 全仓第一个 DragEvent 模拟（`dropFiles` 与 `dragOverOnly` 必须是两个入口：
  落位提示在 `onDrop` 里被同步清掉）。

  **待核**：产物 tif 是输入影像尺寸的 **2 倍**（`SR_code/code_0817_prod.py:566` 的 `scale: 2`、
  `util.py:1521-1526`，尚无真机实证）。若如此，分屏两侧是「同一地面区域、不同像素网格」，对齐只能按**百分比**，
  ±1 像素级比对做不到 —— 要给用户说明白，别让人以为是对齐精度损失。

  **留作下轮议题**：① **攒图吃内存**（每个 rec 的预览分辨率 `Float32Array` 永不释放，÷4 约 145MB/张、
  ÷2 约 600MB/张；分屏按设计同时压两张、点选清单鼓励多开，风险比之前大 —— 本轮只记录，不做 LRU 释放）；
  ② 同场景 `<目录名>.jpg` 与 `<目录名>.tif` 两行预览相同（上一轮「栅格赢」让两行落到同一份落点），
  点选清单会把它们列成两条看起来一样的条目 —— 加提示是纯前端的事，另开一轮；③ `.e2e/qa-theme.js` 有 6 条
  **陈旧 FAIL**（期望的是 2026-09-09 restyle 之前的白色 chrome/渐变主按钮，与实际主题不符，也含两条依赖运行时
  数据 `.rec-bar`/`.qp-dot.on` 的检查）—— 本轮确认**不是本轮引入**（`git diff` 里 style.css 只加 `--cmp-*`
  15 行、Toolbar.vue 只加新规则、零删除行），未动它：究竟该改脚本期望值还是改主题，得由人定。

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
**SR 对她的 `DatarootLQ` 不是只读的** —— [`util.py:1282`](SR_code/util.py#L1282) 的 `writeTiff` 把产物 tif、
`Debug/` 日志与 meta.xml 更新都写进那个目录。所以「前端填一个路径」的字面含义是「那个目录会被写入」，
而计划把首次生产写权放在阶段 6。

> **2026-09-16 订正**：本节原写「写产物前必先把**输入**改名成 `*_NOSR.tif`（与 `Suffix` /
> `DeleteOriTifNeeded` 都无关）」。该改名（`util.writeTiff` 里的 `os.rename(path + tiftype, …)`）作用在
> **输出路径**上：非空 `Suffix` 时首跑该文件不存在（`FileNotFoundError` 被吞），重跑时被改名的是上一次的
> 产物；只有 `Suffix` 为空、输出名 == 输入名时才是输入本身。平台两个入口恒发非空 `Suffix`
> （空值回落到 SR 配置文件的 `<Suffix>`，该文件缺失/值非法时才回落到内置 `sr`；显式值非法 400），
> 所以本节的**结论**（那个目录会被写、写权留到阶段 6）不变，错的只是机制描述。见契约 §2.4 第 1 条与 §6。

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
- 🐛 **发现并修掉一个产品缺陷：`<目录名>_mask.tif` 被当成场景列出**。`scene_search.is_scene_file` 原先只排除 `.preview.jpg`；真机形态是掩膜与场景**同名同目录**，于是每个已带掩膜的目录都会在 `/scenes` 多出一行——卫星/传感器由掩膜文件名解析（`satellite=<父目录名>`、`sensor='mask'`）、尺寸取掩膜 TIFF 头、「提交 SR」还可点（同目录 → 同指纹 → 幂等命中，不至于重复跑，但列表是脏的）。修：`is_scene_file` 增加 `_DERIVED_STEM_SUFFIX = "_mask"` 判定（只认**结尾**标记，`GF07A03_mask_PMS01_….tif` 仍是场景），后端 +3 例、`test-scenes.js` 的【已知问题】**镜像断言**改为「掩膜未出现在列表」（5 行 → 3 行）。**（2026-09-16 已被白名单收件规则取代，见 §4 时间线同名条目——那条 `_DERIVED_STEM_SUFFIX` 常量已删。）**
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

### 2026-09-16 · 产物后缀默认值改为读 SR 团队配置文件（开发机）

**起因**：检查平台 SR 命名逻辑时发现，平台侧的默认后缀是写死的 `"sr"`（`SR_SUFFIX_DEFAULT`
环境变量可覆盖），而 SR 团队自己的配置样例写的是 `<Suffix>260318</Suffix>`（日期式）。两边
约定不同源，产物名就对不上。

**改动（已落地，开发机全绿）**：

1. 请求不带 `suffix` 时，平台读 `$SR_BUNDLE_DIR` 下 SR 团队配置文件里的 `<Suffix>` 当默认值；
   文件缺失 / 坏 XML / 标签缺失或为空 / 值不合白名单 → 回落内置 `"sr"`。**每次提交现读，不缓存**
   （指纹含 suffix 文本，缓存会让指纹变成进程启动时刻的函数 → 同一逻辑提交重复投作业）。
2. `SR_SUFFIX_DEFAULT` 环境变量**作废**（设了不读）。**真机 systemd 里如果加过这一行，删掉**；
   要改后缀改那份 XML。
3. 顺带修一个现存缺陷：agent 工具 `run_sr` 此前不做 strip / 不给默认值 / 不校验，而 REST 入口
   三样都做——同一个"不传 suffix"的提交，两入口算出**不同指纹 → 幂等失效 → 重复投作业**。
   现由 `services/run_sr.py::normalize_suffix` 单点收口，两入口共用。有测试钉住两入口指纹相等。
4. 前端不再预填 `'sr'`（预填值会作为显式参数压过配置文件），后缀输入框留空 + 占位文案说明来源。
5. 文件名拼写：磁盘上是 `sfsr_confgig_test_espan2_cuda1.xml`（`confgig`），契约文档记的是
   `sfsr_config_…`。生产 `$SR_BUNDLE_DIR` 里是哪个未核实，读取逻辑**两个拼写都探**。

**验证**：后端 `pytest backend/tests -q` → 375 passed / 1 skipped；前端 `npm test` → 169 passed，
`vue-tsc` 零错误；`.e2e/test-scenes.js` 61 断言、`.e2e/test-platform.js` 18 断言全过；
另外真起 uvicorn 冒烟：临时 `SR_BUNDLE_DIR` 放该 XML → `POST /api/queue` 留空 → `params.suffix=260318`，
删掉 XML 重提 → `sr`。

**流程偏差（须复核）**：`docs/planning/api-contract.md` §3.3 有契约改动，按该文档 §7 红线本应
「先改文档置评审、通过后再改代码」。这次代码与文档同批落地（理由是它同时在修那个幂等缺陷，拆开
会留下一个已知会重复投作业的中间态）。该文档状态已置回**「评审」**并写明挂起项，**请复核后改回
「已定」**。

### 2026-09-16 · 场景列表改为白名单（`<目录名>_meta.xml` 锚定）+ 页面改名「场景库」

**起因**：真机接上 `SR_SCENES_ROOT=/DiskArray/tmp/wangrz/datahub/` 后，`/api/scenes` 扫出 18 行，
其中 16 行是派生件——`_thumb.jpg` / `_cloud.preview.jpg` / `_sr.preview.jpg` /
`<目录名>.preview.jpg` / `PAN.preview.jpg`，以及 `Debug/` 下十几张调试图
（`TiaoJiaoImage1..4` / `TC_B1_inner` / `RChalf_B1_inner` / `RC_B1_test1` / `RC_B1_inner` /
`DD_B1` / `B1_outerCMOS` / `seamTif…`）。原来的黑名单（后缀白名单 + 排 `.preview.jpg` +
排结尾 `_mask`）每冒出一类新派生件就得补一条，补不完。

**改动**：`scene_search.is_scene_file()` 换成白名单，两条同时成立才收：

1. 所在目录是**场景目录**（内有 `<目录名>_meta.xml`，新增 `is_scene_dir()`）。这不是新发明的
   约定——SR 脚本本来就靠它判 RC/SC（`util.check_sr_previous_step`），没有它的目录提交也跑不起来，
   所以它同时也是「这个目录里的东西能不能提交」的判据；
2. 文件名 = `<目录名>.<ext>`（SC 步骤的输入）或 `PAN.<ext>`（RC 步骤的输入，`util.get_l1_pan_tif_rcsc`
   的 RC 分支读的就是 `PAN.tif`）。

**顺带纠正的语义**：`_scene_row` 的 `lq_path` 从「场景文件父目录」= 盘阵根，变成真正的场景目录，
与真机 `/DiskArray/GSHC2IMPS/年/月/日/生产编号` 一致；`/queue` 表单带出的 `lq_path` 也随之改变
（`.e2e/test-scenes.js`、`test-platform.js` 的断言已同步）。

**代价（已接受）**：没有 `<目录名>_meta.xml` 的目录整个不显示。

**页面**：导航与页标题「盘阵场景」→「场景库」（`App.vue` 导航项、`ScenesPage.vue` 标题与文件头注释、
`Toolbar.vue` 的按钮 tooltip）。页面里另外约 20 处「盘阵场景」说的是**图从哪来**（盘阵 vs 本地 TIF），
与该页名无关，不改。「场景（文件）」列由截断省略号改为 `word-break: break-all` 换行完整显示
（生产场景名 50+ 字符、中间无空格），页宽 1180 → 1360。

**测试**：后端 `pytest backend/tests -q` → 379 passed / 1 skipped。三处夹具按真机形态重造
（`<root>/<[子目录/]编号>/<编号>.<ext>` + `<编号>_meta.xml`）：`test_search_scenes.py`（新增
`write_scene` 助手 + `test_derived_products_are_not_scenes` / `test_debug_dir_is_not_a_scene` /
`test_rc_input_pan_is_a_scene` / `test_dir_without_meta_is_not_a_scene`）、`test_api.py`、
`test_api_platform.py`。前端 Vitest 169 passed、`vue-tsc` 零错误；`.e2e/test-scenes.js` 61 断言、
`.e2e/test-platform.js` 18 断言全过。

### 2026-09-17 · 盘阵任意场景目录：可打开 / 可画掩码 / 可就地提交 SR（开发机）

**起因**：生产数据在 `/DiskArray/GSHC2IMPS/PRODUCT/<年>/<月>/<日>/<生产编号>`，与
`SR_SCENES_ROOT`（`/DiskArray/tmp/wangrz/datahub`）是两棵树。原来只有 datahub 下的场景能在
查看器打开、进而才能提交 SR，于是生产场景要先搬进 datahub 才能用。目标改为：盘阵上**任何
合法场景目录**（目录内含 `<目录名>_meta.xml`，且含 `<目录名>.tif` 或 `PAN.tif`）都能打开 →
画掩码 → 写回该目录 → 就地提交，不搬数据。

**红线（用户逐条给的）**：

- **绝不扫盘**。盘阵数据量极庞大，后端只 `stat` 用户给的那一个路径，判场景目录只试固定文件名，
  禁止 `ls`/`glob`/`rglob`/`iterdir`。测试用 `patch(Path, "rglob"/"glob"/"iterdir")` 与
  `os.listdir`/`scandir`/`walk` 全打成 `AssertionError` 来钉这条。
- 用户粘的是 Windows 形态 `W:\GSHC2IMPS\PRODUCT\2026\09\17\<生产编号>`；`W:\` = `/DiskArray`。
- 白名单是**可配前缀**（默认 `/DiskArray`），不是"扫出来再过滤"。
- 本地 tif 按文件名反推服务端路径：**命中才允许提交，猜错必须报错**，绝不静默提交。

**新增/改动**：

1. `backend/pathguard.py`（新，零 backend 依赖）：`to_posix_array_path`（纯词法归一：剥引号 →
   拒 UNC/控制字符 → 盘符映射 → 反斜杠转正 → 拒 `..` → 折叠）、`allowed_roots` /
   `ensure_allowed`（`SR_ALLOWED_ROOTS`，**不做 `is_dir()` 检查** —— 白名单是词法策略，
   开发机没有 `/DiskArray` 也要能跑测试）、`infer_scene_paths`（`SR_SCENE_PATH_TEMPLATE`
   反推）。`api/paths.py` 改为从这里 import，`scenes_root` 等语义原样不动。
2. `POST /api/scenes/resolve`：请求 `{path}` 或 `{name, date}`（二选一），返回与 `/api/scenes`
   行**同形**的 `{source:"manual", row, resolved}`，前端直接 `scenes.open(row)`。手工行 id 是
   `~` + base64url(绝对路径)（`~` 不在 base64url 字母表里，库行 id 一字未变）。错误码分工：
   400 形态非法 · 403 越白名单 · 404 不是合法场景目录（`detail` 列出试过哪些候选、各自原因）·
   422 读不到尺寸。
3. **掩码命名同源**（本轮最大的一处实质修正）：`bake_mask` 写的是输入影像的 stem，
   `derived_mask_path` 取的是**目录名** —— SC 场景（`<目录名>.tif`）两者恰好相同，
   RC 场景（`PAN.tif`）不同名，于是写进去叫 `PAN_mask.tif`、提交时却去找
   `<目录名>_mask.tif`，**必然 400**。现由 `scene_search.mask_stem` 单点决定，写与读同源。
4. 提交侧两入口（REST `_norm_sr_params` 与 agent 工具 `run_sr`）共用
   `pathguard.normalize_submit_path`：同一个场景写成 `W:\…` 与 `/DiskArray/…` 必须算出同一个
   `task_fingerprint`，否则幂等层失效、重复投作业。
5. 前端：`ScenePathBar.vue`（两处入口共用：场景库页检索条下 + 查看器工具栏）、
   `lib/scene.ts` 的 `todayScenePrefix`/`inferScenePath`/`parseSceneDate`、
   `apiResolveScene`/`apiBakeMask`、`stores/scenes.ts::resolvePath`、查看器的
   `tryLinkScenes`/`bakeMaskToServer` 与「保存掩码到盘阵」按钮。`srReady` 放宽为
   `!!activeRec?.lqPath`（判据是"这张图有没有盘阵目录"，不是"它是怎么打开的"）。
6. 队列表单的掩码名改为**优先显示后端给的权威值**（resolve 响应 / 写掩码响应 / 该任务行自己的
   `params.mask_path`），拿不到或用户手改了 `lq_path` 才退回前端那份无 stat 的镜像 ——
   镜像拿目录名顶输入影像 stem，PAN 场景会指错文件。

**验证**（开发机全绿）：后端 `pytest backend/` → **454 passed / 1 skipped**；前端
`npx vitest run` → **177 passed**、`vue-tsc --noEmit` 零错误、`npm run build` 通过；
e2e `.e2e/test-manual-scene.js` **37 断言**（粘 `W:\…` 打开 → 画矩形 → 保存掩码到盘阵 →
提交 SR → 假调度器跑完；不存在的编号报错且不留可提交物；本地 tif 反推命中/落空/无日期三条路；
PAN 场景掩码名取输入名不取目录名，含队列表单显示的那一份），另有 `test-scenes.js` 61 断言、
`test-platform.js` 18 断言无回归。**新增仓库文件**：`.e2e/test-manual-scene.js`。

**真机待确认（上线前必查，一票否决项排第一）**：

1. **`SR_LOCKED_DIR` 是否已设**（`platform.py::_norm_sr_params`）。设了的话所有新场景提交都会被
   400 拒 —— 上线前确认，设了要清掉或改成白名单语义。
2. 服务账号（`User=nginx`）对 `/DiskArray/GSHC2IMPS/PRODUCT/.../<编号>/` 的**写权限**：meta.xml
   回写、`Debug/` 日志、掩码写入、`.preview.jpg` 缓存四处都要写。resolve 响应里带 `writable`
   标志，前端可提前提示；探针命令见 `deploy/sr-api.service` 新增段落。
3. ~~`/DiskArray/GSHC2IMPS/PRODUCT/<年>/<月>/<日>` 这一层是否就是 `<编号>/<编号>.tif` +
   `<编号>_meta.xml`~~ **2026-09-17 真机否掉**：这一层下面是 `<卫星型号>/<段级目录>/<景级目录>`，
   `<年>/<月>/<日>` 之后还有两层（详见同日时间线条目「反推模板少两层」）。
4. `SR_SANDBOX_ROOT` 若已配，提交会先复制场景目录 —— 掩码必须先写真实目录再提交（当前交互顺序
   天然如此，但 UI 文案要写清）。
5. 大 tif 首次 `/preview` 要解压采样整幅，耗时在 API 主机上；nginx `proxy_read_timeout` 已是
   3600s，够用，但界面可给"首次较慢"的提示。

**文档**：`docs/planning/api-contract.md` 新增 §3.5、改 §3.4 body 与状态挂起项；
`deploy/sr-api.service` 新增 `SR_ALLOWED_ROOTS`/`SR_DRIVE_MAP`/`SR_SCENE_PATH_TEMPLATE`
说明段；`deploy/nginx.conf` 注明**本条不需要改**（手工场景预览走 `/api` 反代，别把
`/disk-array/` 的 alias 放大到整个盘阵 —— 那会让白名单从第二层保险退化成唯一一层）。

### 2026-09-17 · 反推模板少两层：生产树「文件名 → 场景目录」永远落空（开发机）

**现象（真机）**：把生产树上的一个 `.jpg` 拖进查看器，弹窗报

```
未能关联盘阵目录 (W:\GSHC2IMPS\PRODUCT\2026\09\02\JL1KF02B03_..._102_0025_001_L1_PAN)：
没找到合法场景目录 —— /DiskArray/GSHC2IMPS/PRODUCT/2026/09/02/JL1KF02B03_..._L1_PAN：目录不存在
```

**根因**：反推模板是 `PRODUCT\{y}\{m}\{d}\{name}`（**四层**），而生产树实际是
`PRODUCT\<年>\<月>\<日>\<卫星型号>\<段级目录>\<景级目录>`（**六层**），少两层 —— 所以不是
"猜错了日期"，是模板结构本身在生产树上 100% 落空。用户 `ls` 证实
`/DiskArray/GSHC2IMPS/PRODUCT/2026/09/02` 存在，下面才是 `JL1KF02B03/`。
同一轮还发现：模板在 `pathguard.py` 与 `scene.ts` 各写了一份，前端写死的那份让
`SR_SCENE_PATH_TEMPLATE` 这个 env **从未生效过**。

**规则（用户给的命名规范，纯词法、不扫盘）**：`JXGF07D03_PMS_20260622052600_200516571_101_0006_001_L1_MSS`
按 `_` 分 9 段 = 卫星型号 / 传感器 / 14 位成像时刻 / 9 位任务计划号 / 3 位段号 / 4 位景号 /
生产次数 / 级别 / 产品（MSS|PAN）。于是：

- `{sat}` = 第 0 段 → 外层目录；
- `{mid}`（段级目录）= 景级名**去掉第 5 段（景号）**；真机对照
  `..._102_0025_001_L1_PAN`（景级）↔ `..._102_001_L1_PAN`（段级）；
- `{y}/{m}/{d}` = 第 2 段那 14 位的前 8 位。

**改动**：`backend/pathguard.py` 新增 `scene_name_layers` / `strip_raster_ext`，默认模板改六层，
名字推不出 `{sat}/{mid}` 时**跳过**该候选（不硬拼空段）；候选 = 生产树模板 + 旧扁平
`PRODUCT\{y}\{m}\{d}\{name}`（兼容平铺部署），配了 `SR_SCENE_PATH_TEMPLATE` 就只走那一条。
`POST /api/scenes/resolve` 的 `{name, date}` 放宽为 `{name}`（date 可省，后端自己从文件名解析）。
前端 `scene.ts` 删掉 `parseSceneDate`/`inferScenePath`/`stripRasterExt`/`mapWinPathToPosix`
与 `viewer.ts` 里自拼路径那段 —— **模板与命名规则只剩 pathguard.py 一处真源**，env 从此真正生效。
"命中才提交、猜错必须报错"不变：候选全落空 → 400 并列出试过的候选与各自原因。

**验证**（开发机）：后端 `pytest backend/` → **464 passed / 1 skipped**（基线 454）；前端
`npx vitest run` → **171 passed**（基线 177，删掉 6 条随后端规则迁移的用例）、`vue-tsc --noEmit`
零错误、`npm run build` 通过；e2e `test-manual-scene.js` **39 断言**（含生产树层级夹具）、
`test-scenes.js` 61、`test-platform.js` 18 无回归。

**真机待确认**：「段级 = 景级去景号」这条关系目前**只有一组真机样本**支撑，其它卫星/产品是否
同样成立待核（`ls .../PRODUCT/<年>/<月>/<日>/<卫星型号>/` 一眼可见）。规则若不成立，表现是
反推落空并报出候选 —— 不会静默指错目录。规则全文见
[docs/sr_code/production-scene-naming.md](../sr_code/production-scene-naming.md)。

### 2026-09-17 · 队列页三处前端缺陷：耗时掉成「0 秒」/ 耗时列折行 / 页宽不齐（开发机）

**现象（真机，客户端）**：① 「任务队列」页里 SR 跑完后，「耗时」列从一个正常数值**迅速变成
「0 秒」**；② 该列偏窄，`已运行 3 分 20 秒` 经常折成两行；③ 「聊天」与「任务队列」页的正文
宽度比「场景库」窄。

**根因（①，读代码可判定，已用 e2e 反证）**：终态行的耗时 = `updated_at − created_at`，而
客户端的 `updated_at` 只来自 `GET /api/queue`。链路三处对、一处缺：后端每次状态写回都刷
`updated_at`（`store.set_sr_task_state`），接口也返回它（`platform._task_view`），但 **SSE
`job_update` 帧不带这个字段**，前端 `mergeJobUpdate` 又只覆盖 `state`。于是页内那份
`updated_at` 冻结在上一次 `list()` —— 而那次 `list()` 就发生在提交刚落库之后，
`created_at` 与 `updated_at` 是同一个 `now`，差值为 0。运行中用的是本地现算的 `nowSec`，
数字正常增长；一进终态改用那份快照，恰好等于 `created_at` → 「0 秒」。

②③ 是纯样式：耗时列的 `td` 没有 `nowrap`，文案里的空格就是断行点；三页各写各的
`max-width`（场景库 1360 / 队列 1240 / 聊天 1240）。

**改动**：

- `backend/services/store.py`：`set_sr_task_state` 返回值由 `None` 改为写入的 `updated_at`
  （两个调用方都忽略返回值，无回归）。
- `backend/api/platform.py`：`_task_state` 广播帧带上 `updated_at`；**写库失败就不带** ——
  库里没变，凭本地时钟发一个只会让界面与库对不上。
- `frontend/src/lib/api.ts` / `stores/queue.ts`：`JobUpdateEvent.updated_at?` 可选字段，
  `mergeJobUpdate` 在 state 之外一并写回 `updated_at`；值非法（缺失 / 0 / NaN）时不覆盖，
  保留本地快照（老后端下退化为原行为，而不是把整列变成「—」）。
- `frontend/src/pages/QueuePage.vue`：耗时列与创建时间列 `white-space: nowrap` +
  `tabular-nums`（定长格式，秒数跳动时列宽不抖）；参数列加 `overflow-wrap: anywhere`
  吸收宽度，否则上面两列的 nowrap 会把整表撑出 `.qp-tbl-wrap` 的 `overflow: hidden`。
- `frontend/src/style.css` 新增 `--page-w: 1360px`，场景库 / 队列 / 聊天三页统一引用 ——
  这次的宽度漂移就是这个毛病，收敛成一处令牌后不会再各自漂。
- 契约同步：`api-contract.md` §3.3 的 `job_update` 事件 schema 补 `updated_at`。

**验证**（开发机，全绿）：后端 `pytest backend/` → **466 passed / 1 skipped**（基线 464，
新增 2 例：广播帧带 `updated_at`、写库失败时不带）；前端 `npx vitest run` → **175 passed**
（基线 171，新增 4 例，含一条回归钉「任务跑完不再退化成 0 秒」）、`vue-tsc --noEmit` 零错误、
`npm run build` 通过；e2e `test-manual-scene.js` 39 / `test-scenes.js` 61 /
`test-platform.js` **21**（基线 18，新增 3 条断言）无回归。

新断言做过反证：把前端的 `updated_at` 写回临时摘掉（后端保持已修）重新构建跑
`test-platform.js`，红在 `耗时列 = 接口真值（页内 0 秒 / 接口 1 秒）` —— 正是真机看到的现象；
恢复后转绿（页内 1 秒 / 接口 1 秒）。新增的布局断言同时钉住「三页同宽
（1360 / 1360 / 1360）」与「耗时列 `white-space=nowrap`」。

**真机待确认**：① 交付需**同时更新 `frontend/dist` 与 `backend/` 两个包** —— 只换 dist 的话，
新前端收不到旧后端不发的时间戳，耗时列会退回到「保留旧快照」，即原现象（`mergeJobUpdate`
对缺失字段是容错的，不会报错，只是不修）；② 真机上跑完一个任务后「耗时」列应显示真实耗时，
且点「刷新」后数值不变；③ 三页正文是否确实同宽（窗口宽度 > 1360 时才会体现出差异）。

### 2026-09-17 · 盘阵场景拉伸下拉被禁用：一个模式都用不了（开发机）

**现象（真机，客户端）**：打开盘阵场景后，工具栏的拉伸下拉是灰的、固定停在「2% 线性」，
悬停提示「盘阵 JPG 已烘焙 2% 线性拉伸」，换不了任何别的模式。

**根因（读代码可判定，已用 e2e 反证）**：不是崩溃，是一条**刻意的闸门**，但它给的理由在
数据形态上不成立。`Toolbar.vue` 用 `sceneActive`（`route === 'jpg'`）同时禁掉下拉并强制显示
`linear2`；`viewer.paintStretch` 又在开头对 `route === 'jpg'` 直接 `return`（只记
`paintedMode`、不重画）。理由是「别对已烘焙图做二次拉伸」—— 可是本地拖入的 JPG
（`route === 'img'`，走同一个 `sceneDecodePixels`、同一份 stats）从来就可以随便拉：数据形态
完全一样，能力一边有一边没有。

另有一条被忽略的事实：`sceneDecodePixels` 用 `computeStats(..., 0, 255)`，所以 `linear` 在
这张图上恒等。也就是说**即使放开闸门、起手值仍取线性，画面与服务器烤的也逐像素一致** ——
放开本身不会毁掉烘焙值，真正的取舍只在「起手值取哪个模式」。

**改动**（用户决策：场景起手值取**直方图均衡**）：

- `frontend/src/lib/scene.ts`：新增 `SCENE_START_STRETCH = 'equal'` 与纯函数
  `startStretch(route, current)` —— 盘阵场景恒以直方图均衡起手，其余路径沿用当前模式。
- `frontend/src/stores/viewer.ts`：`paintStretch(rec, mode)` 收显式模式、**删掉 jpg 早退**；
  新增 `repaintOnActivate`（没画过就按起手值画；画过的本地图仍跟随工具栏，**盘阵场景不跟随**）；
  `setStretch` 改的是当前这张图（记在 `rec.paintedMode` 上）且**场景不写回全局**；新增
  `activeStretch` 计算属性 = 下拉显示的真值（`paintedMode ?? 起手值`）。
- `frontend/src/components/Toolbar.vue`：去掉 `:disabled` 与 `'linear2'` 强制值，改显示
  `store.activeStretch`；title 改为说明「服务器烤的 2% 线性是底图，这里改的是二次拉伸，
  两端已被裁掉，拉不回来」。
- `frontend/src/components/AgentChatTab.vue`：送 Agent 的上下文如实报**当前显示层**模式
  （原先 `route==='jpg'` 时写死「2% 线性（盘阵烘焙）」）。
- `frontend/src/pages/ScenesPage.vue`：页脚提示由「交互式拉伸/导出 JPG 在场景路径不可用」
  改为「显示层拉伸可改（起手值直方图均衡），烘焙时裁掉的两端拉不回来」；顺带删掉已不存在的
  「导出 JPG」半句（导出链路 09-14 已整体删除）。
- `frontend/src/viewer/e2eHooks.ts`：`__viewer` 补 `activeStretch()` / `setStretch()`，rec 摘要
  补 `paintedMode`（浏览器回归要用）。
- 不改：`rec.layout` 文案「盘阵 JPG（已烘焙 2% 线性拉伸）」—— 它描述的是**文件本身**，仍成立。

**验证**（开发机，全绿）：后端 `pytest backend/` → **466 passed / 1 skipped**（本轮未动后端，
基线复核）；前端 `npx vitest run` → **178 passed**（基线 175，新增 3 例：场景起手值恒为均衡 /
其余路径沿用当前模式 / 场景数据上 `equal` 确实改像素）、`vue-tsc --noEmit` 零错误、
`npm run build` 通过；e2e `test-manual-scene.js` 39 / `test-scenes.js` **65**（基线 61，新增 4 条
断言）/ `test-platform.js` 21，无回归。

新断言做过反证：把 `paintStretch` 的 jpg 早退临时加回去重新构建跑 `test-scenes.js`，红在
`切到对数后画布像素变化（均衡均值 126.96 → 对数均值 126.96）` —— **下拉动了、画面没动**，
正是真机看到的现象；恢复后转绿（127.58 → 141.01）。同时钉住「下拉值与 rec 记录同步」与
「别的场景 rec 不被连带改掉」。

**真机待确认**：① **性能与内存**（本轮最大的未知数）—— 盘阵 JPG 长边 8192，整幅约 6700 万
像素；放开后**每次**打开场景、**每次**换模式都要在显示层做一遍全图 `stretchRgba`
（一遍全图循环 + 一份 RGBA ≈256MB 瞬时分配，画布与 `src` 另占约 500MB）。开发机 fixture 只有
1600×800（128 万像素），这个开销完全看不出来。真机上如果「打开场景变慢」或页面卡顿/崩，
需要改成降采样或分块再算 —— 请回传体感与机器配置。② **直方图均衡是否真的比 2% 线性更利于
判读** —— 这是选它当起手值的假设，看图确认；不合适改 `SCENE_START_STRETCH` 一个常量即可，
也可以直接告诉我要换成哪个模式。③ 在同一张场景图上改过模式后，切到别的图再切回来应保持
（每图独立），且本地 TIF 的起手值不应被场景上的操作改掉。

### 2026-09-17 · 页宽断言加严：旧断言抓不到「三页一起漂」，顺带发现 dist 停在 09-15（开发机）

**现象（复核上一日「队列页三处缺陷」的收尾项 ③「三页正文是否确实同宽」）**：源码里三页确实
已统一到 `--page-w`，但盘上 `frontend/dist` 构建于 **09-15**，产物里仍是旧值 ——
`ScenesPage` **1180px** / `QueuePage` **1240px** / `ChatPage` **1240px**。这与上一条记录里
「e2e `test-platform.js` 21 断言全绿（含三页同宽）」对不上：那条断言读的正是 dist 的
`getComputedStyle().maxWidth`，按盘上这版产物跑必然红。

**根因**：① `dist/` 在 `frontend/.gitignore` 里、不入库，git 不管它；源码改了不重新
`npm run build`，产物就一直是旧的。那次绿跑之后 dist 被旧产物覆盖过（是被谁覆盖的已不可考，
只确认盘上这版早于 09-17 的令牌改动）。② 那条断言本身也弱：只比 `max-width` **字符串**，
且跑在 puppeteer 默认 **800px** 视口下 —— 那个宽度里三页都被视口压扁成一样宽，
比字符串也好、比实际宽度也好都是恒真。

**改动**：

- **重建 dist**（`frontend/`：`npm run build`）。产物核对：三页均为 `max-width: var(--page-w)`、
  `index-*.css` 里 `--page-w: 1360px`，全库无 `1180px` / `1240px` 残留。
- `.e2e/test-platform.js` D 段：量之前先 `page.setViewport({width: 1600, height: 900})`
  （量完复位 800×600，不影响后文）；量的是实测正文盒宽 = `getBoundingClientRect().width`
  减左右内衬，不再只看字符串。断言拆成两条：
  ① 三页**实测同宽**；② 该宽度 **= 1320**（= 1360 − 2×20）—— 半句是防「三页一起跟着视口走」
  也算同宽，没有它这条断言等于没测。

**验证**（开发机，全绿）：`.e2e/test-platform.js` → **22 项断言**（基线 21，+1）；
[D] 段输出「三页实测同宽（1320 / 1320 / 1320）」「三页都顶在 --page-w 上限
（正文盒 1320 / 1320 / 1320，max-width 1360px / 1360px / 1360px）」。

**反证（两条，都实测过）**：

1. 场景库改回各写各的（`max-width: 1240px` 字面量）重新构建 → 红在
   `场景库 / 队列 / 聊天 三页实测同宽（1200 / 1320 / 1320）`。
2. **只**把 `--page-w` 改成 `1240px`（三页一起漂，正是旧断言漏掉的那类）→ 第一条**照样绿**
   （`1200 / 1200 / 1200`），红在第二条 `三页都顶在 --page-w 上限（正文盒 1200 / 1200 / 1200，
   max-width 1240px / 1240px / 1240px）`—— 证明新增的第二条确实补上了旧断言的漏。

两条还原后重新构建，`git status` 只剩 `.e2e/test-platform.js` 一处改动（src 逐字节还原），
再跑一遍转绿。

**实测附带结论（1600 视口）**：三页外框均 1360、左边缘同为 x=120、正文盒 1320。唯一差异是
聊天页内部两栏 —— `.cp-side` 固定 232px + 14px gap，`.cp-main` 实测 **1074**。这是布局设计，
不是宽度漂移；要让聊天主体也占满 1320 得动两栏结构，属另一个改动。

**交付提醒**：本次交付必须带上**这一版重建的 `dist`**（盘上旧包就是 1180/1240/1240）；
且按前一条记录的提醒，`frontend/dist` 与 `backend/` 两个包要一起更新。

### 2026-09-17 · 烘焙规则 v2：各边 1/2 + 直方图均衡，并接受「单张 .tif」入口（开发机）

**需求（用户原话）**：「当前查看器打开一个 tif 时，不直接阅读该 tif，而是在其目录将 tif "阅读"并生成
一个质量约为（长宽各为原先 1/2 质量）的 .jpg，然后读取 .jpg」；「烘焙速度越快越好」。

**六条已拍板的决策**（两轮问答 + 计划批准，实现中不再改）：

1. **入口**：只做「服务端路径」那条（盘阵场景目录 / 场景库），**外加**支持粘单个 `.tif` 文件路径。
2. **尺寸**：严格各 1/2，**不封顶**（24739×24199 → 12370×12100）。小图同样走 1/2。
3. **产物**：**覆盖**现有 `<stem>.preview.jpg`，不新建第二份；拉伸由「2% 线性」改「**直方图均衡**」。
4. **首次等待**：等烘焙完再显示（不退回「先读 TIF 顶着」）。
5. **显示层起手拉伸**：盘阵场景仍取 `equal`（`SCENE_START_STRETCH` 不动）。
6. **加速**：并行读 **8 线程**；JPEG **q85**。

#### 改动

**后端 `backend/services/preview_jpg.py`（这条链的规格）**

- `PREVIEW_SCALE = 0.5`；`ensure_preview_jpg` 先用 `scene_dims`（只读 `.hdr`/TIF 头，很便宜）算
  `max_edge = round(max(W,H) * PREVIEW_SCALE)`，再交给 `build_preview_pixels`。**采样算法一行没改** ——
  原来的 `ps = min(1, max_edge / max(W,H))` 传 1/2 就是严格各 1/2。`PREVIEW_MAX_EDGE = 8192` 删除。
- `stretch_2pct` → `stretch_equal`，逐式镜像前端 `tifDecode.ts` 的 `mode='equal'`（**min/max，不是
  p2/p98**；1024 桶；CDF 查表；常量图沿用前端规则：全零→0、其它→128）。WhiteIsZero 反相照旧。
  两遍 + 行块（`_STRETCH_CHUNK = 4M px`）：1.5 亿像素量级下整图 `astype(float64)` 会产生 1.2GB 副本。
- 读取按行分段并行（`READ_THREADS = 8`，`PARALLEL_MIN_ROWS = 512` 以下串行）。**Windows 没有
  `os.pread`**，所以每线程开自己的句柄。`ex.map(...)` 的结果必须 `list()` 消费掉 —— 异常在线程里
  抛出，不迭代就不会转交给调用方，会变成静默漏读。
- 规则戳写进 JPEG 注释（`srprev:v2:half+equal:q85`），命中时比对 —— 光看 mtime 不行：真机上换包后
  旧的 8192+2% 图 mtime 比源新，会被判有效而**永不重烤**。改尺寸/拉伸/质量任一都要 bump
  `PREVIEW_RULE_VERSION`。
- `Image.MAX_IMAGE_PIXELS = 1 << 30`：**这是一个真 bug**，不是保险。1/2 尺度把 2.4 万像素级的源烤成
  1.5 亿像素，超过 Pillow 默认上限（8948 万）的 2 倍就抛 `DecompressionBombError` → `_cache_hit` 的
  `Image.open` 失败 → 缓存永远判不中 → **每次打开都重烤一遍**，整条优化全部抵消。

**后端 `backend/api/app.py`（裸 .tif 入口）**：`resolve_scene` 的 `{path}` 分支加一个文件判定 ——
`.tif/.tiff` 走 `_resolve_bare_tif`，别的后缀 400 说清（不是掉进目录逻辑报「目录不存在」）。
响应新增 `sr_capable`：裸 TIF 的父目录**确实是**合法场景目录时才是 true，否则 `mask_path` 与
`row.lq_path` 一起为 null。另修一处 `_same_file`：原用 `Path.__eq__` 比路径，它在 Windows 上
大小写敏感，用户把盘符写成小写就会把可提交的场景误判成不可提交。

**前端**：`fetchSceneJpg` 新增可选 `onPhase`（只在「本次会触发烘焙」时响一次，接 `row.hasPreview`）；
`viewer.openScenePath` 按 `sr_capable` 决定写不写 `lqPath`（`lqPath` 是能否提交 SR 的唯一判据）；
场景库页加一行 `scenes.phase` 文案（遮罩 `viewer.showMask` 只挂在 `/viewer`，本页调了看不见）；
`ScenePathBar` 文案补「也可以粘单个 .tif 文件路径」；五处「2% 线性」文案改「直方图均衡」。

**顺带修掉一个自己的改动引出的缺陷**：`activate()` 会为「没有 lqPath 的 rec」调 `tryLinkScenes`
反推目录。裸 TIF 恰好是 `route='jpg'` 且 `lqPath=null`，于是它拿**裸文件名**去反推 —— 这是**错的**：
那张图的目录已经由 resolve 按**绝对路径**定过，反推按名字猜的可能是另一个目录，用户点「提交 SR」
就会拿着错的 `lq_path` 去跑。现在 `tryLinkScenes` 对 `route==='jpg'` 一律不试（e2e 的 404 计数抓住了它：
从 2 条刻意 404 变成 3 条）。

#### 两条被推翻的计划前提（测量过，别再按它优化）

- 「去掉 `stretch_2pct` 里的 `astype(np.float64)`（1.2GB 副本）是主要提速点」→ **实测 0.131s**，
  不是热点。行块实现仍保留（1.5 亿像素下它是内存保障），但别再把它当性能项。
- 「逐行 `seek+read` 换成块读更快」→ **实测更慢**：块读 0.583s vs 逐行 0.324s（块读要多读一倍字节）。
  memmap 0.567s、整文件顺序读 0.654s，都更慢。**没换**。
  逐行 1/2 采样只读源文件一半的字节（1.2GB 的源读 599MB），已经是所有读法里最省的。

真正的收益在**延迟隐藏**：1/2 尺度要发约 1.2 万次读，盘阵上单次读若有毫秒级延迟，串行就是十几秒，
8 路并发压回一秒量级（页缓存命中时只快 1.4 倍，所以开发机上几乎量不出收益）。

#### 验证（开发机，全绿）

后端 `pytest backend/` → **490 passed / 1 skipped / 74 subtests**（基线 466）；前端
`npx vitest run` → **183 passed**（基线 178，新增 5 例 `fetchSceneJpg`：库外走 `/preview` 响应体 /
库行先懒生成再静态读 / 已有缓存只读静态 / `onPhase` 只响一次 / `onPhase` 可选）、
`vue-tsc --noEmit` 零错误、`npm run build` 通过；e2e `test-manual-scene.js` **48**（基线 39，新增
G 段 9 条）/ `test-scenes.js` **65**（无回归，只改注释）/ `test-platform.js` **22** /
`test-vue-viewer.js` **35**，四个套件全绿。

开发机端到端实测（24739×24199 16bit，源 1.198GB，页缓存命中）：首次烘焙 **2.87s** → 12370×12100、
**100.7 MB**（q85）；二次烘焙 0.0003s；戳 = `b'srprev:v2:half+equal:q85'`。
Python 的 `stretch_equal` 与真实前端 TS 用 `vite-node` 跨语言逐像素核对，**7 个用例 0 差异**。

#### 反证（三条，都实测过）

1. **e2e 尺寸**：把 `PREVIEW_SCALE` 临时改成 `1.0` 跑 `test-manual-scene.js` → 红在
   `预览 JPG 各边为源图 1/2（400×200 → 400×200）`；还原后 `200×100` 转绿。（这条 G 段断言是
   唯一真能区分新旧规则的：`test-scenes.js` 的 fixture 靠 `.hdr` 谎报尺寸，1/2 恰好落在 tif 真实
   尺寸上，两边同值 —— 已在该文件注释里写明，别再指望它钉规则。）
2. **前端 `onPhase`**：把 `if (!row.hasPreview)` 临时改成 `if (true)` → 红在
   `onPhase 只在「本次会触发服务端烘焙」时响一次`；还原转绿。
3. **后端并行读**：`test_parallel_path_matches_serial` 用 `mock.patch` 把阈值顶到天上强制串行，
   两边逐像素比。已反证（丢掉最后一段即红）。**注意它钉的是分块覆盖**，读行逻辑本身由斜坡/常量/
   WhiteIsZero 等内容测试钉 —— 两个分支共用同一个 `read_rows` 闭包，那里的 bug 会互相抵消。

另有两个「我的断言写错了、不是代码错」的：斜坡 `row[0]` 是 1 不是 0（CDF 把最小那档自己算进去）、
`Uint8ClampedArray` 是 ties-to-even 而 `astype(np.uint8)` 是截断（已改用 `np.rint`，否则整图差 1 个灰阶）。

#### 真机待确认

1. **浏览器内存（本次最大的未知数）**：12370×12100 下位图 + `getImageData` + `graySrcFromRgba` 的
   Float32Array 各约 600MB，峰值可能到 1.2–1.8GB（2GB 单次分配上限内，但离得不远）。开发机
   fixture 只有 1600×800，看不出来。真机若卡顿/崩，退路是调小 `PREVIEW_SCALE` 或加一个长边上限
   （改一个常量）。
2. **下载体积**：q85 下这块 JPG 是 **100.7MB**，比旧的 8192 封顶产物（约 52MB）大一倍。慢内网里
   「下载」可能比「烘焙」还久 —— 请回传体感。
3. **盘阵是单块 HDD 时 8 线程可能更慢**（磁头竞争）。这个数只能真机量。
4. **nginx 缓存**：`deploy/nginx.conf` 给 `.preview.jpg` 设了 `max-age=3600`，升级后浏览器可能继续
   用同名的旧图一小时（后端戳已能识别，但浏览器拿的是缓存）。交付说明里要写硬刷新。
5. **首次打开会变慢一次**（旧缓存无戳 → 判失效 → 重烤），属预期。

**交付提醒**：`backend/` 与重建的 `frontend/dist` 两个包要一起更新（同前两条）。

### 2026-09-18 · 拖拽盘阵 `.tif` 改走服务端烘焙 JPG（临时缓存 1 天 TTL，开发机）

#### 起因

用户问的是这条链路能不能整条走通：「拖进一个盘阵中未超分的 `.tif` → 预览它的 `.jpg` →
在上面画掩码 → 保存 → 点『提交 SR』」。逐段核对后：能力都在，但**拖拽那一段走的是浏览器
本地解码**（route ∈ `utif/chunked/sparse`），而场景库/粘路径走的是服务端烘焙 JPG —— 同一个
文件、同一个人，两条入口的观感与成本完全不同（真机 1.1GB 的图每拖一次要重解一遍，吃几百 MB）。
用户拍板：**拖拽也走 JPG，但那份 JPG 是临时缓存，有效期一天，第二天 0 点清除**。

#### 改动

后端：

- 新增 `backend/services/preview_cache.py`：`tmp_preview_path()` = `SR_TEMP_PREVIEWS_ROOT/<YYYY-MM-DD>/<sha256(源绝对路径)[:16]>.jpg`，**每次调用读 env**（测试要能按请求换根），只 `mkdir` + `stat`，**绝不列举目录**（「禁止扫盘」是硬约束）。`purge_temp_previews()` 只删「桶名是 ISO 日期、名 < 今天、且带 `.sr-tmp-preview` 标记文件」的目录，符号链接与非目录一律跳过，根是符号链接/指向文件系统根时拒绝清理，任何异常都吞掉（清理失败不该让服务起不来）。
- `backend/api/app.py`：新增后台任务 `_tmp_preview_purge_loop`（**启动先清一次** → 睡到下一个本地 0 点 → 清 → 重复），`lifespan` 从单个 `app.state.poller` 改成**任务列表**逐个 cancel。清理**不进请求路径**（`iterdir` 会踩「禁止扫盘」的测试钉子）。
- `POST /api/scenes/resolve` 的 `{name}` 分支新增可选 `size_bytes`，命中候选后过 `_fingerprint_mismatch`：**文件名（去光栅后缀）与字节数都吻合**才认，不符则记原因后继续试下一条候选（最终仍是 404 并列出候选与原因，不新增错误码）；非正整数 400。
- 新增 `GET /api/scenes/{id}/preview-tmp`：与 `/preview` 同一条烘焙链、同样的规则戳，只差落点（临时根 + 当天桶）与响应头（`Cache-Control: no-store`）。

前端：

- `viewer.activate` 改成 `if (!rec.route && await tryLinkScenes(rec)) return;` —— **命中就不做本地解码**，顺带避开「本地图先画出来又被 JPG 换掉」的闪烁。`tryLinkScenes` 返回 `Promise<boolean>`，body 带 `size_bytes: r.file.size`，`AbortSignal.timeout(4000)`；服务端 4xx 写 `rec.linkNote`（**不再 `showErr`**，那不是错误而是「这张图不在盘阵上」），网络失败/超时**清掉 `linkTried`**（那不是服务端的答案，下回还能再试），烘焙失败则报错 + 回落本地解码。
- 抽出 `applySceneJpgToRec`（新建 rec 与就地升级共用）：`openSceneJpg` 原来那一串收尾动作（`sceneDecodePixels` → `markRaw` → `layout` → `startStretch` → `fit` → `refreshCloudStats`）一个都不能漏，漏一个就是云量卡不刷/拉伸下拉显示错/画面不重画。
- 查重加上 `sceneId`（`findRecByMeta`）：升级过的 rec 名字还是用户拖进来的 `SC.tif`，库行给的名字是 `SC` —— 只按名字找会开出两条 rec、两份 maskRois。
- 三处异步写加 `rec.token` 令牌守卫（本地解码 `applyDecoded`、JPG 升级 `applySceneJpgToRec`、`openOne` 的帧头 `layout` 写入）。
- 取临时图用**独立函数** `fetchTempSceneJpg`，**不改写** `row.hasPreview` —— 那是「生产 `<stem>.preview.jpg` 此刻在不在」的真值，被置真之后再从场景库打开同一场景就会跳过懒生成、直接打一个 404 的静态 URL。

#### 一条偏离计划的设计决定

计划里写的清理拒绝条件是「根是符号链接 / 根落在 `SR_SCENES_ROOT` 之内 / 根为 `/`」。实际只实现了**符号链接 + 文件系统根**两条，去掉了 `SR_SCENES_ROOT` 包含检查：那条会让「配在 scenes 根以内」这种合法（但不推荐）的配置**静默地永不清理**（服务照跑、缓存无限涨，且没有任何人会发现）。真正的安全保障是**标记文件**：只删「桶名像日期 **且**桶里有 `.sr-tmp-preview`」的目录 —— 这把「这个目录是我们建的」变成可判定的事实，比任何路径白名单都可靠，也不依赖配置正确。`deploy/sr-api.service` 里仍然写明别把临时根放进 `SR_SCENES_ROOT`（临时缓存不该混进生产数据目录），那是**建议**，不是唯一防线。

#### 验证（开发机，全绿）

后端 `python -m pytest backend/tests -q` → **511 passed / 3 skipped / 74 subtests**（基线 490；
新增 `tests/test_preview_cache.py` 12 例 + `TestResolveFingerprint` 7 例 + `TestTempPreview` 5 例）。
**3 个 skipped 是符号链接那三条**：本机（Windows，非开发者模式）建不了符号链接，那两条防线
（根是链接 → 拒绝清理、桶是链接 → 跳过）只做到了逻辑覆盖，**真机（Linux）上会真跑**。

前端 `npx vitest run` → **188 passed**（基线 183；新增 `tmpPreviewUrl` / `fetchTempSceneJpg`
三例 / `apiResolveScene` 透传两例）、`vue-tsc --noEmit` 零错误、`npm run build` 通过。

e2e 四套全绿：`test-manual-scene.js` **61**（基线 48，E 段重写并新增双指纹/临时落点/回落本地解码
三类断言）、`test-scenes.js` 65 / `test-platform.js` 22 / `test-vue-viewer.js` 35（无回归）。

E 段的改造要点：原来那两处「命中」用例上传的是 fixture 里那张凑数的小图（名字对、字节数不对），
双指纹一上**必红**；现在改成从盘阵侧 `fs.copyFileSync` 真那份再上传，并断言 `route==='jpg'` +
`thumbW===400`（= 元数据 800 的一半，证明像素来自服务端烘焙而非本地解码）+ 只打
`/preview-tmp` 不打生产 `/preview` + 临时 JPG 落在 `SR_TEMP_PREVIEWS_ROOT/<今天>/` 且桶里有标记
文件 + **生产场景目录里没有多出 `.preview.jpg`**。另新增两例：同名不同字节 → 404 + `linkNote`
带「字节数」+ 退回本地解码（`route` ∈ `utif/chunked/sparse`）+ 提交按钮保持禁用；以及
`.err-box` 断言全部换成 `rec.linkNote`（`decodeRec` 几百毫秒内就会覆盖 `rec.status`，
断言 `status` 必 flaky）。§H 的刻意 404 计数从 2 改 3、400 仍为 1。

#### 真机待确认

1. **`SR_TEMP_PREVIEWS_ROOT` 必须显式配**（默认是系统临时目录，CentOS7 的 `/tmp` 常是 tmpfs
   内存盘，而 1/2 尺度不封顶、单张可能上百 MB）。配到 nginx 可写的大盘上，**不要**放在
   `SR_SCENES_ROOT` 之下。上线前用同款 `touch` 探针确认写权限。
2. **磁盘预算**：只保留当天那一桶，桶内不设上限 —— 一天的拖拽量要能放得下。嫌大就给
   临时路径单独设 `max_edge`（需要**独立的规则戳**，别和生产那份混用 `rule_stamp()`）。
3. **双指纹在真机上的表现**：真机上 `<目录名>.tif` 与 `PAN.tif` 并存是常态（RC 场景），
   拖 `PAN.tif` 那条路本来就到不了指纹这一步（名字里没有 14 位成像时刻，反推先报 400）——
   真正生效的是「用户拖的确实是输入影像本身」这条正向判定。请回传一次真机拖拽的体感
   （首次等待时长、是否退回本地解码）。
4. **`ensure_preview_jpg` 没有单飞**：同一场景并发两次会各烤一遍（既有风险，拖拽路径会放大）。
   本次不修。

### 2026-09-18 · 交付打包统一：**前后端各一个包**（开发机）

**起因**：用户贴出真机那套更新/回滚命令（`tar -xzf /tmp/dist-….tar.gz -C $APP` +
`tar -xzf /tmp/backend-….tar.gz -C $APP`，回滚用 `dist.bak.<日期>` / `backend.bak.<日期>.tar.gz`），
指出打包应当**前后端各一个包**。此前盘上有两个 release 目录、13 个包混在一起（根 `release/`
是 09-17 手工拆的 3 个 `backend-*` + 4 个 `dist-*`，`frontend/release/` 里还有 6 个更早的、
含一个「前后端合一」的 `sr-agent-platform-*.tar.gz`）。

**改动**（`frontend/scripts/package-offline.sh` 重写）：

- 产出固定到**仓库根** `release/`，**两个**包、都**不带** `sr-agent-platform/` 前缀（顶层直接是
  `dist/` 与 `backend/`，才能 `tar -xzf … -C $APP` 直接落位）：`dist-<日期>-<时分>-<版本>.tar.gz`
  与 `backend-*.tar.gz`（backend 包照旧去 `__pycache__/` 与 `tests/`）。
- 打完自动清掉 `release/` 里同族的旧包（`dist-*` / `backend-*` / 历史 `sr-agent-platform-*`），逐条打印；
  无关文件不碰。
- 修一个 Windows 硬伤：GNU tar 见到归档名里的 `D:` 会按 `host:path` 当远程主机（`Cannot connect to D`）
  → 改为先 `cd` 进输出目录、用相对包名打包。
- **首次安装件（`nginx.conf` / `sr-api.service` / `requirements-api.txt` / 本 README）不进包**（用户选定）：
  它们是机器配置模板、真机上被手工 sed 成过真实路径，进包解压会把真路径盖回出厂示例 —— 正是
  `deploy/README.md` §5.6 那类事故的结构性消除。`deploy/README.md` §一/§二/§五、本文件 §6.1 已同步改口径。

**删除的冗余**：根 `release/` 7 个 + `frontend/release/` 6 个（含目录本身），共 13 个 `.tar.gz`。
全部在 gitignore 内、未入库，可从 git 历史重建（checkout 对应提交 → `npm run build` + `npm run package:offline`）。

**验证**：模拟真机解压 —— 两个包解进同一个空目录后顶层就是 `dist/` 与 `backend/`；包内 `dist` 含
`preview-tmp`（证明拖拽那条新链路在包里）、`backend/services/preview_cache.py` 在、`tests`/`__pycache__` 为 0；
清旧包逻辑用假旧包 + 无关文件各造一个实测（旧包被删、无关文件保留）。产物：`dist-20260918-0922-b1bd87e.tar.gz`
（327K）/ `backend-20260918-0922-b1bd87e.tar.gz`（90K）。

**同日复查出的两处**（都影响此前记录的可信度）：

1. **盘上 `frontend/dist` 停在 09-17 17:59**，早于 `1b2a415`(23:57) 与 `b1bd87e`(00:21) —— 里面连
   「1/2 预览图」文案都没有，更没有 `preview-tmp`。直接跑 `.e2e/test-manual-scene.js` 时 E 段红在
   `命中即升级成盘阵 JPG 路由（route=chunked）`（后端是新源码、前端是旧包）。**已重建**，四套 e2e
   复跑 **61/65/22/35 全绿**，这才与文档记的数字对得上。
2. **`backend/tests/test_scene_resolve.py` 在 HEAD 上不是合法 Python**：第 407-409 行的 f-string 把表达式
   跨了行（PEP 701，3.12+ 才允许），`bdc6d4d` 那版还好、`1b2a415` 引入。本机 `python` 是 3.11.3，
   `python -m pytest backend/tests -q` 在**收集阶段**就 SyntaxError，一条都跑不了 —— 也就是说
   09-17/09-18 记的「490 / 511 passed」用文档那条命令复现不出来（本机无任何 3.12+ 解释器）。
   临时改成单行等价写法后该文件 **48 passed**，全量 **463 + 48 = 511**，与文档数字吻合 → 坏的只是这个
   测试文件。**尚未修（工作区已还原）**；它不进交付包（脚本排除 `tests/`），但开发机门禁仍红。
   同日晚些时候**已修**（见下一条）：`resolve → row.id → preview` 三步拆开，该文件恢复收集。

**09-18 16:34 复查（打包前后端至 release）**：上面这轮改动的**脚本与 README 并没有留在盘上** ——
`frontend/scripts/package-offline.sh` 仍是旧的单包版（产出 `release/sr-agent-platform-<日期>-<版本>.tar.gz`、
把 `nginx.conf`/`README.md`/`sr-api.service`/`requirements-api.txt` 一起打进包），`deploy/README.md`
§一/§二/§5.5 也都还写着单包。而 `release/` 里躺着的、以及上面记的产物名，都是新口径的
（`dist-20260918-0922-b1bd87e.tar.gz` 等）—— 即当时确实用双包版打过包，但脚本事后被还原了
（同日 10:47 有一次改动，怀疑是那次「工作区已还原」连带把脚本一起回退了）。**本次按本节记的口径
重建脚本并同步 README**，改法与上面一致，另加：backend 包改用 `tar --exclude` 直接排除
`__pycache__` / `backend/tests`（不再先铺到暂存目录再删），两个包都不再校验/携带首次安装件。

- 实测验证：两个包解进**同一个空目录**后顶层就是 `dist/` 与 `backend/`；`backend` 里
  `tests`/`__pycache__` 计数 0；`dist/assets/ViewerPage-*.js` 里含 `待修复清单` 与 `同步至指定文档`
  （证明待修复清单那批改动确实进了包）；清旧包逻辑用假旧包 + 无关文件各造一个实测（旧包被删、
  无关文件保留）。产物：`dist-20260918-1634-9182c7c.tar.gz`（332.7K）/ `backend-20260918-1634-9182c7c.tar.gz`（96.8K）。
- ⚠️ **版本戳只是 `git describe`（HEAD = `9182c7c`），而工作区有未提交改动**（待修复清单那一批 +
  左右栏 400px + 左栏小图预览）。也就是说这两个包的内容**不等于 `9182c7c` 这个提交**。要按提交号
  回滚/追责的话，先提交再重打一次。
- ~~⚠️ 待修复清单的写盘一步没有真机验证过~~ —— **当天即改**：真机上 `showOpenFilePicker` 是不存在的
  （页面是 `http://内网IP`，那个 API 是 `[SecureContext]` 标的），写盘改走后端之后既有回归又能真机跑，
  见本节末「清单写回改走后端」。

### 2026-09-18 · 拖盘阵 `.jpg` 进查看器：关联场景目录、掩码能落盘（开发机）

**起因**：用户报「把盘阵上那些 `.jpg` 拖进查看器（应当作 `PAN.tif` 的原图读），鼠标悬在
『保存掩码到盘阵』上却提示『这张图没有盘阵目录』」。两处原因，各一处改动：

1. **前端压根没试过关联**：`addFiles` 按扩展名分堆（`.tif` → `openOne`，`.jpg` → `openLocalImage`），
   而 `openLocalImage` 建完 rec 就 `activate` 完事，从没调过 `tryLinkScenes`。
2. **后端指纹排除 jpg**：`_fingerprint_mismatch` 要求与输入影像**字节数完全相等** —— 盘阵上那份
   `<编号>.jpg` 是另一份产物（8bit 显示就绪预览），与输入的 TIF 不可能同字节，比下去恒不通过。

`tryLinkScenes` 本身对 `route === 'img'` 的 rec 完全可用（它只挡 `route === 'jpg'` 与
`lqPath/linkTried`），所以主要工作是把已经写好的函数**调一下**。

**改动**：

- `backend/api/app.py::_fingerprint_mismatch`：名字那半之后加一句 `Path(name).suffix.lower() in
  (".jpg", ".jpeg")` → 直接返回 None（放行）。名字那半**原样保留**（它挡的是「拖了 `<编号>.jpg`
  而 SR 实际跑的是 `PAN.tif`」）。**没有**泛化成「后缀不同就放行」—— 那会连 `SC.tiff` 与 `SC.tif`
  一起放过，而字节数那一半正是为这种「本机另存过一份」设的。
- `frontend/src/stores/viewer.ts::openLocalImage`：`activate` 之后补 `void tryLinkScenes(live)`。
- `tryLinkScenes` 命中分支：拖进来的就是 jpg 时 `blob = r.file`（**不调 `/preview-tmp`**）——
  用户要看的就是自己拖的那张，拿服务端 1/2 缩图顶掉反而降清，还白等一次解压采样。（**2026-09-20 批注**：
  这条仍是**默认**行为，但不再是一律 —— 同目录配着一位更清晰的栅格、且当前档位下服务端从它烤出来的比这张 jpg
  更清晰时，jpg 那一路也改走服务端，见 api-contract §4.6 与本文 §1 的 09-20 条目。保留原句是因为它解释的是
  「为什么默认不烤」，删掉会让后来人以为被推翻了。）裸 `.tif`
  反推命中仍走阶段4 那条老路。`applySceneJpgToRec` 加可选第四参 `layout`，本地 jpg 那条路
  自报来源（否则会显示「1/2 尺度 + 直方图均衡，服务端已烘焙」这句假话）。
- 新增 `frontend/src/components/NoticeModal.vue` + store 的 `modal` 状态（`showModal/hideModal`）：
  关联失败时 **jpg 走弹窗**（后端那句 detail 常常是「试过哪几个目录、各缺什么」，toast 六秒
  既看不完也留不住），tif 维持原 toast。**不新增错误码** —— 现有 404 的 detail 已是能直接给人
  看的话。挂在 `ViewerPage.vue` 页面根（不放 `TifCanvas` 的 slot：`.stage` 的 transform 祖先会
  困住 `fixed`），并 `onUnmounted(() => viewer.hideModal())`（store 是全局单例，不关就换页会跟着走）。
- 顺带修 `backend/tests/test_scene_resolve.py:407-409` 的跨行 f-string（PEP 701，只有 3.12+ 能解析，
  本机 3.11.3 收集阶段就 SyntaxError）。

**实测踩出来的一个坑（值得记住）**：`openLocalImage` 里 `recs.value.push(rec)` 之后，局部变量
`rec` 是**原始对象**，直接改它的字段**不触发渲染** —— 关联成功了、`__viewer.activeRec().lqPath`
也是对的，但工具栏那两颗按钮**一直是灰的**（store 值对、DOM 不更新）。改成 `recs.value.find(...)`
取的**响应式代理**再往下传。其余入口本来就是 `recs.find(...)` 拿的代理，只有新增的这条差点漏掉。
这类「store 对、页面不对」的偏差只看 `__viewer` 快照是查不出来的，得看 DOM。

**验证（开发机，全绿）**：后端 `python -m pytest backend/tests -q` → **513 passed / 3 skipped**
（基线 511；`TestResolveFingerprint` 新增 2 例：jpg 名 + 字节数故意不等 → 200，`.tiff` 名 +
字节数不等 → 仍 404）。前端 `npx vitest run` 188 passed、`vue-tsc --noEmit` 零错误、`npm run build`
通过。e2e 四套：`test-manual-scene.js` **75**（新增 E2 段）、`test-scenes.js` 65 / `test-platform.js`
22 / `test-vue-viewer.js` 35（无回归）。E2 段断言：`route==='jpg'` + `lqPath` = 场景目录 +
`W/H` 取影像头 1600×800 + **`/preview-tmp` 与 `/preview` 请求计数都不变**（证明没走服务端烘焙）+
缩略图 = 拖进来那张 jpg 的像素（800×400）+ 布局文案不谎称服务端已烘焙 + 两颗按钮由灰转亮 +
掩码真落进场景目录；失败那条断言弹窗给后端原因、状态栏不卡在「正在关联盘阵目录…」、按钮保持禁用。
§H 的刻意 404 计数 3 → 4。

**残留风险（两条，未修）**：

1. **名字那半仍要求 `strip_raster_ext(jpg 名) == 输入影像 stem`**。所以若某个 RC 目录里**只有
   `PAN.tif`**、没有同名的 `<编号>.tif`，拖 `<编号>.jpg` 会被拒（弹窗会原样显示「目录里的输入
   影像是 PAN.tif，与拖入的 X 不是同一个文件」）。用户称同目录有 tif 是 99% 的情况，先按此接受；
   真机上若发现这就是常见形态，放宽一行即可（jpg 拖入时只要目录里有输入影像就认）。
   **当天即被真机证伪并修掉** —— 这就是常见形态（纯 RC 目录只有 `PAN.tif`），用户报「极少出现
   盘阵小标」。见本篇后面那条「拖盘阵 `.jpg` 极少出现「盘阵」小标：jpg 的名字门比错了对象」。
2. **没有宽高比校验** → 若那张 jpg 与输入 tif 比例不一致（被裁过 / 加过注记），掩码坐标会整片偏。

**真机待确认**：拖一个 `<编号>.jpg` → 应出「已关联盘阵目录 …」toast 且「保存掩码到盘阵」变亮；
画 ROI → 保存 → 盘阵上应出现 `<场景目录>/<编号>_mask.tif`；回归拖 `.tif` 的老路径行为不变
（含字节数不符仍被拒）。

### 2026-09-18 · 打开报「缺 18_meta.xml」：粘错层时不再编一个 meta 文件名（开发机）

**起因**：用户报打开失败 —— `没找到合法场景目录 —— /DiskArray/GSHC2IMPS/PRODUCT/2026/09/18：
缺 18_meta.xml（SR 靠它判 RC/SC，没有就跑不起来）`，并指出「`_meta.xml` 前一般是完整前缀」，
同时给出标准形态：`W:\GSHC2IMPS\PRODUCT\2026\09\02\JL1KF02B03\<段级>\<景级>`（换言之，
被粘的那一层**少了两层文件夹**）。

**定位**：resolve 的目录分支对**所有**非场景目录一律报 `缺 <目录名>_meta.xml`。而
`<目录名>_meta.xml` 的前缀是**完整生产名**（含 14 位成像时刻），这句话只在目录名本身就是
生产名时才讲得通。复现证明唯一能产出这串字面的输入是**日期目录这一层**
（`…/PRODUCT/2026/09/18`）—— 正是 `ScenePathBar.vue` 的预填值 `todayScenePrefix()`，
用户点「打开」即中；粘完整景级路径（下划线名、空格名都试过）都是 200。`{name}`（拖拽）
分支不可能产出这种候选：它的候选永远是 `<…>/<年>/<月>/<日>/<卫星型号>/<段级>/<名字>`。
这与 09-17 那条「反推模板少两层」是同一族问题的另一面：**平台知道树有六层，但没在任何一处
告诉用户**。

**改动**：

- `backend/pathguard.py` 新增三个**纯词法**原语（不 stat、不列举，只服务诊断措辞）：
  `production_tree_depth`（日期目录之下第几层）、`flat_scene_layout`（扁平形态：日期目录下
  一段就是生产名）、`looks_like_scene_name`（名字里认不认得出 14/8 位成像时刻）。
- `backend/api/app.py::_why_not_scene_dir`：404 的原因按**粘错哪一层**分派 —— 日期目录报
  「场景目录在它下面 3 层 `<卫星型号>/<段级目录>/<景级目录>`」、卫星型号层报 2 层、段级目录
  报 1 层、场景目录内部子目录报「比场景目录还深」、名字不是生产名的报「不是完整生产名」。
  只有**层与名字都指向「它就该是场景目录」**时才报「缺 `<完整生产名>_meta.xml`」（这句本身
  是对的，也正是 SR 的判据 `osp.basename(lq_path) + "_meta.xml"`）。**准入判定一字未改**。
- **段级目录名同样带 14 位成像时刻**，所以先按层数认、再按名字认 —— 否则「缺
  `<段级名>_meta.xml`」这句同类的废话会原样搬到下一层去。
- 扁平拓扑（`<年>/<月>/<日>/<场景名>`，平铺部署与 e2e 假拓扑）里场景目录再深一层是它**内部**
  而非段级层 —— `flat_scene_layout` 就是为分开这两种拓扑加的，这条被
  `test_flat_topology_scene_subdir_404_says_too_deep` 钉住。**（09-18 续：夹具已改成六层生产树，
  这条扁平用例改成当场现搭一个平铺目录；同名的六层用例 `test_scene_subdir_404_says_too_deep` 另立。）**
- `backend/pathguard.py::scene_name_layers`：各段分隔符**下划线与空格都认**，段级目录名按
  **原文的分隔符**重建（空格名拼出下划线的段级目录必然 stat 不到）。盘阵真形态是下划线，
  这条是防御性的（用户口径里出现过空格写法）。
- `frontend/src/components/ScenePathBar.vue`：预填值仍是当天日期前缀，但提示语与占位符改教
  真形态六层 —— 原来那句「用户补上生产编号即可」还是 09-17 前的四层口径，而预填的日期目录
  本身**不是**场景目录，直接点「打开」必 404。

**验证（开发机，全绿）**：后端 `python -m pytest backend/tests -q` → **530 passed / 3 skipped**
（本次新增 17 例，基线 513）。前端 `npx vitest run` 188 passed、`vue-tsc --noEmit` 零错误。
新增用例：`test_paths.py` 的 `TestProductionTreeDepth` / `TestFlatSceneLayout` /
`TestLooksLikeSceneName` + 空格形态拆段 + 首尾分隔符拒判；`test_scene_resolve.py` 的
`test_day_dir_404_points_three_levels_down`（钉死「不许再出现 `17_meta.xml`」）与卫星型号层 /
段级层 / 场景目录子目录（六层与扁平各一）/ 非生产名四条。临时验收脚本（跑完即删）按用户回传的
标准路径实测六种错层各报对应层，下划线六层场景 200，拖空格名 `.jpg` 反推命中六层树 200
（改前必失败）。

**残留风险（两条，未修）**：

1. 层数靠 `<年>/<月>/<日>` 三段形态做**位置推断**：路径里若有一段无关的 `2026/09/18` 目录
   （比如把场景目录挂在别处），措辞会按那个位置报。只影响提示文案，不影响能不能打开。
2. 空格分隔符这条是**推测性的**：盘阵上实际是下划线（用户回传的标准路径可见），空格形态只在
   用户口径里出现过。若真机确认不存在空格名，这两行可以直接删掉（不删也无副作用）。

**真机待确认**：粘日期目录 / 卫星型号层 / 段级目录三种错层，界面是否给出「还差几层」；
`W:\…\JL1KF02B03\<段级>\<景级>` 这一串应直接 200。

### 2026-09-18 · 拖盘阵 `.jpg` 极少出现「盘阵」小标：jpg 的名字门比错了对象（开发机）

**起因**：真机客户端拖入 `.jpg` 极少关联上盘阵，左上角「盘阵」小标几乎不出现；界面不报错、
不提示，「拖完什么都没发生」。

**定位（已复现）**：`backend/api/app.py::_fingerprint_mismatch` 的名字那一半拿**显示件 jpg**
去比**栅格输入**的 stem。`inp` 是 `scene_search.input_scene_path(d)`，即
`<目录名>.{tif,tiff,img}` / `PAN.{tif,tiff,img}` 里第一份存在的（候选里**根本没有 jpg**）。
纯 RC 场景目录里只有 `PAN.tif` → `inp.stem == "PAN"`，而盘阵那份显示件叫 `<编号>.jpg`
（生产全名）→ 永远比不过 → 404。六层真机形态夹具 + TestClient 打真请求的复现表：

| 场景目录里有什么 | 拖 `<编号>.jpg` | 拖 `PAN.jpg` |
|---|---|---|
| 只有 `PAN.tif`（纯 RC） | **404** | 400 |
| `<编号>.tif` + `PAN.tif` | 200 | 400 |
| 只有 `<编号>.tif`（纯 SC） | 200 | 400 |

小标只在「目录里恰好还躺着 `<编号>.tif`」时出现（SC 场景，或 RC 目录留着上游 SC 产物）
—— 看着就像随机。09-18 那次加的「jpg 免字节数」在这条路上是**死代码**：名字门已经先拒了。

第二道门：拖 `PAN.jpg`（RC 输入自己的配套显示件）名字里没有 14 位成像时刻 →
`parse_scene_date` 取不到 → **400，候选目录一个都没试**。重命名件、别处导出的图同理。

第三道（静默）：`frontend/src/stores/viewer.ts::tryLinkScenes` 的 `AbortSignal.timeout(4000)`
到点后**一声不吭**地收场（清 `linkTried`、状态栏复位）。resolve 里要读一次输入影像的头，
盘阵冷缓存时超过 4 秒是可能的。

**改动**（按用户选定的三条）：

- jpg 的名字门改比**场景目录名**（生产全名），不再比栅格输入的 stem；字节数照旧不比。
  默认模板下候选目录名就是由这个名字（去后缀）拼出来的，所以这条通常直接成立 —— 它是给
  「换了 `SR_SCENE_PATH_TEMPLATE`、场景目录改了命名」的部署留的守卫；真正把派生件
  （`_cloud.jpg`、`.preview.jpg`）挡在外面的是候选目录不存在。
- 名字里取不到成像时刻的 jpg：400 的措辞改成可照做的（平台不猜目录 + 该改拖 `<目录名>.jpg`）；
  前端失败弹窗补一句「能关联的 jpg 只有名字与场景目录名一致的那份」。
- 关联超时 4s → **20s**，并出一条 toast（原来什么都不说）。

**验证（开发机，全绿）**：后端 `python -m pytest backend/tests -q` → **533 passed / 3 skipped**
（新增 3 例：纯 RC 拖 `<编号>.jpg` 必须 200 的回归钉子、派生件/副本 404、无时间戳 jpg 的
400 措辞）。前端 `npx vitest run` 188 passed、`vue-tsc --noEmit` 零错误、
`npm run build` 通过。`.e2e/test-manual-scene.js` 新增 **E3** 段（拖纯 RC 目录里的
`<编号>.jpg`，断言 lqPath / 掩码名仍是 `PAN_mask.tif` / W·H / 像素来源）→ **80 项断言全绿**；
E2 那条拖的是 SC 目录，**复现不出这个 bug**，所以必须单立 E3。

**残留风险（两条）**：

1. 名字里没有生产全名的 jpg（`PAN.jpg`、副本、别处导出的图）仍然关联不上，而且**只能**
   这样：名字里没有日期，后端不知道该去 `<年>/<月>/<日>` 哪一天找，猜一个就是拿别景的
   `lq_path` 去提交。要覆盖它得加「用户点确认，把它关联到刚打开的那个目录」的入口 —— 本轮
   按用户决定只改善措辞，没做这个入口。
2. jpg 的名字门在默认部署下**不可能不成立**（见改动第一条），所以它现在是**守卫**而不是
   过滤器。哪天真需要「这份 jpg 是不是这一景的」这种判断，得靠内容（尺寸之外的证据），
   目前给不出来。

**真机待确认**：拖 `<景级目录名>.jpg`（**无论目录里有没有** `<编号>.tif`）都应出现「盘阵」
小标，状态栏出现「已关联盘阵目录 …，可以提交 SR 了」。若仍不出现，请把失败弹窗里那句后端
原因原样回传 —— 它现在会直说比的是哪个名字。另请确认部署的后端包含 commit `b1bd87e`
（不含它的话，连 SC 场景那条路也关联不上，与本次定位无关但会盖住结论）。

### 2026-09-18 · 队列「耗时」量的是行的年龄，不是这次跑的时长（开发机）

**起因**：用户报队列页「耗时」列出现**大几十个小时**，而实际单次 SR 最多 200 多秒。

**定位（两个触发点都实测复现）**：那一列当时算的是 `updated_at − created_at`，也就是
**这一行的年龄**。问题出在行的身份上 —— `sr_tasks` 按 `task_fingerprint`（参数 sha256）
唯一，**一行 = 一套参数，不是一次运行**；同场景同参数重交会复用同一行并**真重跑**，而
`created_at` 停在**第一次**提交的时刻（`run_sr.submit_run_sr` 命中复用分支时在
`put_sr_task` **之前**就返回了，所以走到 `put_sr_task` 的 UPDATE 分支 == 这是一次真重跑）。

| 触发点 | 复现（开发机 pytest） | 界面读数 |
|---|---|---|
| ① 复用行：第一次跑挂/被取消，第二天重交 | 真跑了 1.0 秒 | **108001 秒 = 「30 时 00 分」** |
| ② 后端重启：校准器基准 `state.task_cache` 是内存态，重启后为空 → 每行都被判成「状态变了」→ 写回 + 刷 `updated_at` | 回填 `created_at` 到 30 小时前、重起应用 | 第 1 次 `GET /api/queue` 仍 1 秒，**第 2 次起 108001 秒** |

②在真机上**部署一次就中一条**（几天前跑完的行集体变成行龄）；①是「隔天重交」的日常。
两个触发点合起来解释了「大几十个小时」——那正好是两次提交之间的间隔。

**改动**（按用户选定的口径 = **纯算力耗时，不含排队**）：

- `sr_tasks` 新增 `started_at` / `finished_at` 两列（`store.py`）：只在**真的发生状态跃迁**
  时写 —— 首次观测到 RUNNING 记 `started_at`，进 COMPLETED/FAILED 记 `finished_at`。
  这两列是「**某一次运行**」的窗口，与 `created_at`（行第一次提交）/`updated_at`（最后一次写）
  是两个族，都不能拿来算耗时。
- 该写、该记、该广播集中在一处：`set_sr_task_state(..., mark_started=, mark_finished=)`
  返回写入后的整行，`_task_state` 广播的 `job_update` 帧带上这三个时间戳，前端
  `mergeJobUpdate` 一并写回（写库失败则不发，界面与库不会对不上）。
- 重启不再变行龄：校准器的比较基准 `prev` 从内存缓存**回落到库里存的状态**，
  终态行重启后不再被判成「变了」（②的复现路径被掐掉）。
- 前端 `taskElapsed` 改为 `finished_at − started_at`（运行中 = `now − started_at`），
  两列缺一即显示「—」；**不回落到 `created_at`** —— 那正是原来那个 bug。列头悬停说明
  写明「纯算力、不含排队」与「—」的两种成因。
- 老库用 `PRAGMA table_info` + `ALTER TABLE … ADD COLUMN` 补列（部署机上 `CREATE TABLE
  IF NOT EXISTS` 是空操作），**不回填**。

**验证（开发机，全绿）**：后端 `python -m pytest backend/tests -q` → **539 passed /
3 skipped**（`test_api_platform.py` 新增 3 例：跨 30 小时重交后耗时仍是本次、没观测到
RUNNING 时耗时为空、重启不动已跑完的行；`test_store.py` 新增 3 例：两列只在被要求时写、
重交重置窗口但不动 `created_at`、**手工造老 schema 库验证 ALTER 迁移**）。前端
`npx vitest run` **190 passed** + `vue-tsc --noEmit` 零错误 + `npm run build` 通过。
e2e `test-platform.js` **22** / `test-scenes.js` **65** / `test-vue-viewer.js` **35** /
`test-manual-scene.js` **83** 全绿。契约与实况同步：`docs/planning/api-contract.md` §3.3
（GET 形状 + SSE 帧 + 「耗时」列的三个坑）。

**残留风险（都是有意为之，不是待修）**：

1. **整段运行都没被观测到**（后端全程不在）→ `started_at` 为空 → 耗时显示「—」。
   回落到 `created_at` 会把上面那个 bug 原样请回来，所以宁可空着。
2. **升级前的老行**显示「—」（不回填），同上。
3. 运行中的耗时用**浏览器时钟**（`now − started_at`），与服务器时钟有偏差时可能微跳。
4. Slurm 路线若重新启用，作业被 requeue 会重新观测到一次 RUNNING → 起点后移，
   耗时只算**后一段**。本阶段 `SR_EXECUTOR=local` 单槽串行，不触发。

**真机待确认**：① 部署须**同时更新 `backend` 与 `frontend/dist`** 两个包，且后端要**重启**
（建列那次迁移在首次打开库时跑，不重启就没有这两列 → 接口缺字段、界面全「—」）；
② 确认部署的后端含本次改动（上一包是 `b1bd87e`）；③ 升级后**历史行一律显示「—」**属预期，
新提交的任务才有时长；④ 随便挑一条重交过的场景看一眼：耗时应当是**这一次**的秒数，
不再是两次提交之间的间隔。

**顺带修掉的一处测试脆弱**（与本次改动无关，但会咬人）：`test-scenes.js` 那条
「列表行就地翻牌为『已生成』+『打开』」偶发红 —— `fetchSceneJpg` 在**去取静态 JPG 之前**
就把 `row.hasPreview` 置真，标签先翻、按钮要等解码完才从「打开中…」变回来，而断言在
等标签后**立刻**读按钮。改成等按钮也落定（新增 `waitRowBtn`），机器忙时不再假红。

### 2026-09-18 · 像素定位框收「X,Y」整串（开发机）

**起因**：查看器的像素定位是两个 `type="number"` input，而从别处拷来的坐标常常是一对数的形态
（掩码的「掩膜中心点坐标」txt 就是 `30766.11,21862.51`）。number 框把整串判为非法、值直接变空 ——
粘进去等于什么都没发生。

**改动**：

- `lib/viewMath.parseLocPair`（新，纯函数）：半角/全角逗号与空白都作分隔符、首尾空白忽略，
  **恰好两个数**才认；认不出来返回 null，调用方**必须出声** —— 截前两个会把标记跳到别处
  （「1,30766.11,21862.51」是整行掩膜记录，不是一对坐标）。不做千分位等额外容忍。
- `Toolbar.vue`：两个框改 `type="text"` + `inputmode="decimal"`（number 框吞整串，非改不可），
  `@input` 里拆串填进两框（粘到哪个框都行，文本都按「X,Y」顺序读）；单个数 = 正在手输，放行；
  带分隔符却认不出来时出错误条并**原样留着**。拆完不自动跳，仍按「定位」/回车（与手输两个数
  的行为一致）。

**验证**：vitest **196 passed**（viewMath 新增 6 例：半角与全角逗号、空白/制表符分隔、整数与带
符号小数、单个数返回 null、三个数返回 null、非数字）；`vue-tsc --noEmit` 零错误 + `npm run build`
通过；e2e `test-vue-viewer.js` **38 项断言**（新增 3 例：粘「64,32」两框各得一半、拆开后「定位」
跳到 (64,32) 出红叉、粘「1,64,32」原样留着并报「坐标对认不出来」）。

### 2026-09-18 · 工具页文字精简 + 查看器左栏加宽（开发机）

**起因**：用户要求把几处解释性文字收掉，「保证工具页面的整洁」，并指出查看器左栏文字外溢。

**改动**（四处文字 + 一处宽度，按用户逐项确认的取舍）：

- 场景库：表格下方那段「场景 JPG 为服务器烘焙（稀疏采样 + 直方图均衡…）」**整段删**（含其中动态的
  「掩码按元数据 W×H 换算」一句，用户确认不再显示）。随删 `useViewerStore` 与 `.sp-hint` 样式。
- 队列页：标题里的「SR 作业（slurm / 假调度器）」删（口径已过时 —— 现在 `SR_EXECUTOR=local` 本机直跑），
  提交按钮旁那段「提交是真实副作用…（_NOSR.tif 覆盖 / delete_ori 已禁用）」一并删。两条样式规则随删。
- 查看器右栏：`RoiToolsTab` 开头那行「统计作用于当前显示层…」删。云量卡片底部那条「Y≥200 高亮占比
  估算；PAN 无真云掩膜…」**保留** —— 它是「这数字不是真云掩膜」的免责说明，删掉容易把估算看成实测。
- 盘阵场景栏（`ScenePathBar.vue`）：长提示压成两句 —— 预填的只是当天日期前缀、还要往下补三层；
  「支持 W:\ 形态（服务端自动映射到 /DiskArray）」改写成「路径认两种写法：**W:\…（盘阵）**与
  /DiskArray/…」。删去不扫盘之外的细节（生产名样例、meta.xml、单 .tif 入口、首次烘焙耗时）。
- 查看器左栏（`FileList.vue`）：宽由 268 加宽，**最终与右栏一起定在 400**（见下一条《待修复清单》——
  两处宽度是同一次改的，理由写进样式注释：268 里长生产名与「盘阵 JPG（1/2 尺度 + 直方图均衡，服务端
  已烘焙）」那行说明挤在一起，只能靠 `word-break: break-all` 从词中间断开）。

**验证**：vitest **196 passed**、`vue-tsc --noEmit` 零错误 + `npm run build`；e2e `test-scenes.js` **65** /
`test-platform.js` **22** / `test-vue-viewer.js` **38** / `test-manual-scene.js` **83** 全绿（先 grep 过
e2e 里没有断言依赖被删的那几句文案）；`.e2e/shot-theme.js` 重新出图，队列页与场景库页逐张看过。

### 2026-09-18 · 查看器接入《待修复清单》：置顶面板 + 三段式进度 + 一键写回（开发机）

**起因**：质检部门给的《待修复清单.txt》是当前修图工作的输入，而整条流程目前完全在查看器之外手工维护
（记事本存清单 + 人脑记进度）。清单是固定两段结构：上半部分是质检发现的问题行（`生产全名,` + 制表符 +
`产品存在伪影 (问题类型:… 行列号:行,列 影像类型:PAN )` + 制表符 + 责任人），一个空行，下半部分是我们
自己补的处置结果（`生产全名` + 制表符 + 状态词），后者是给质检部门查阅用的。

**改动**：

- `lib/qclist.ts`（新，纯函数，可单测）：解析与写回。逐行按制表符分列 —— **≥3 列 = 问题行**、**恰好 2 列
  且第二列 ∈ 状态词表 = 状态行**、其余一律原样留在上半部分。**上半部分逐字保留**（含其中的空行与认不出
  的行），写回时原样吐出 —— 「上半部分就是原始内容」要是字面意义上的原样，而不是重新生成的近似；换行符
  （`\r\n` / `\n`）随原文。写回的下半部分按上半部分顺序只输出**终态**行。
- 编码：导入先按 UTF-8 严解（`fatal:true`），失败退 GBK（老记事本存的 ANSI 就是 GBK），写回跟原编码。
  ⚠️ 当时的写法是「浏览器只有 GBK 解码器、没有编码器，原文件是 GBK 时就按 UTF-8 带 BOM 写回」——
  **09-18 改走后端之后这条降级没有了**：编码由后端做，GBK 进 GBK 出（`encodeQcText` 连同它的用例已删）。
- **坐标陷阱**（在代码注释里写明）：清单里的「行列号」是 **(行, 列)**，而 `parseLocPair` / `locatePixel(x, y)`
  要的是 **(列, 行)**。跳转处传 `locatePixel(col, row)`，照抄顺序必错。
- `stores/qclist.ts`（新）：清单 / 逐行状态 / 选中行 + localStorage 持久化 + 写盘。持久化存**原文 + 状态
  表**（存原文而不是解析结果，规则改了还能自愈），刷新不丢一下午的标记。
  ⚠️ **写盘那条路 2026-09-18 当天就整条换掉了**（原为 File System Access API 原地覆盖，真机 http 下
  不可用）—— 详见本节末的「清单写回改走后端」条目，这里保留的是当时的形态。
- **数据丢失护栏**：一份问题行都解不出来的文本**一律拒收**。拖拽入口就在画布上（拖 `.txt` 即导入清单），
  顺手把「掩膜中心点坐标.txt」这类文件丢进来是很容易发生的事，照单全收会把已经标了一下午的清单无声冲掉。
- `QcListPanel.vue`（新）挂在 `RoiToolsTab` 置顶：行列表（状态点 + 生产全名 + 行/列 · 影像类型 · 责任人）、
  选中行的三段式进度条（已绘制掩码 / 已提交任务 / 已修复，点选，再点当前项 = 取消）+ 两个人工终态（驳回 /
  非模糊通过，五个状态互斥）、页脚「同步至指定文档」。`✕` 随时收起换一份新清单。
- 联动：打开某场景 → 按 `lqPath` **末段**（= 生产全名）自动选中对应行 —— RC 场景的输入是 `PAN.tif`，
  那条路的 `rec.name` 是个没用的 `"PAN"`，只有场景目录名才对得上；提交 SR → 由队列 `tasks` 自动推进到
  「已提交任务」（只在「还没标」或「停在三段式第一段」时推进：终态是人眼判出来的结论，不覆盖、不倒退）；
  非当前图的行给「打开」小按钮（不自动跳，避免误触等网络）。
- `scenes.openByName`（新）按**错误串**返回（`''` = 成功）：本面板在 `/viewer`，而 `scenes.error` 只在场景
  库页渲染，写那里等于没写 —— 失败原因得接过来喂 `viewer.showErr`，否则点了像没反应。
- 左右栏宽度：左与右都 268 → **400**（两栏对齐，长生产名与尺寸/布局说明都排得开）；左栏文件
  卡加小图预览横幅 `FileThumb.vue` —— 画的是 `rec.thumb`，即**已经画好的显示层画布**（stretch 之后那份），
  所有 route 共用，不另拉一次盘阵 JPG（既多一次网络，又和画布上看到的不是同一份像素）。重绘只看这张图
  自己的 `thumb` / `paintedMode`，**不听全局 `renderTick`** —— 那个值在平移缩放时每个 mousemove 都 +1，
  会让整列预览跟着无谓重绘。没打开过的图没有像素，显占位（解码是懒的，为列表里的小图把每张大图都解一遍
  是几十秒 × N）。

**验证**：vitest **229 passed**（`qclist.test.ts` 33 例：真实样例的整份 round-trip「解析后原样写回 == 原文」、
CRLF 保留、BOM 剥离、只上半部分 / 空文件 / 认不出的行 / 同名去重等边界、GBK 字节解码与 `修复通过` 的
字节序列）；`vue-tsc --noEmit` 零错误 + `npm run build`；e2e `test-platform.js` **22** / `test-scenes.js`
**65** / `test-manual-scene.js` **83** / `test-vue-viewer.js` **58**（新增 G 段 21 例：导入、原样 round-trip、
拒收无关 .txt、按 (行, 列) 显示、只写终态、中间态不落文档、再点取消、未完成筛选、刷新后原文与状态仍在、
`✕` 清空缓存）。

**遗留（需留意 / 真机确认）**：

- 两栏同时展开时 chrome 占 852px，**1366 宽屏上画布只剩 ~510px**。两栏都能收起（`«` / `»`），实现不做
  额外处理，但这是选 400px 时已知的代价。
- ~~「同步至指定文档」的写盘那一步进不了自动化回归，回归只验到 `output()` 为止；真机要求 Edge/Chrome +
  https 或 localhost~~ —— **同一天作废**：`showOpenFilePicker` 在真机（http + 内网 IP）上压根不存在，
  写盘已改走后端 `POST /api/qclist/write`，这一步现在有回归（`test-manual-scene.js` §H，从磁盘读回比对）。
  剩下的真机注意事项只有两条：清单目录要对 **nginx** 可写（替换后属主会变成 nginx），以及目标 .txt
  别用记事本 / Excel 开着（占用会写失败，错误文案里写了这一条）。

### 2026-09-18 · 反推日期改按「成像日 + 次日」两天，删掉旧扁平形态兜底候选（开发机）

**起因（用户报的重大 bug）**：用户原话 ——「图名含 0917 的、大部分所属目录为 0918，这是一个重大 bug，
导致大部分图匹配不到盘阵的真凶；而且弹窗里第二个目录不存在，显然不符合六段格式，所以直接删除即可」。
两个问题都在同一个函数里（`backend/pathguard.py::infer_scene_paths`）：

1. **日期口径错了**。反推的日期取自文件名里那 14 位，而那是**成像**时刻；盘阵却是按**生产日**建目录 ——
   夜里成像的景记在第二天，`…_20260917124710_…` 大多躺在 `2026/09/18/` 下。旧代码把成像日当目录日期，
   只拼出这一条路径，于是「名字 0917 的图」大半 stat 不中 → 404。
2. **弹窗里那条四段目录**是 `_LEGACY_SCENE_TEMPLATE`（`W:\GSHC2IMPS\PRODUCT\{y}\{m}\{d}\{name}`，
   日期目录下直接是景级名 —— 正是用户说的「不符合六段格式」）。它本是「平铺部署」的兜底，在生产树上
   **必然落空**，唯一可见的效果就是错误弹窗里多列一行，把人看懵。

**改动**：

- `infer_scene_paths`：删掉 `_LEGACY_SCENE_TEMPLATE` 与那条候选，每个模板改为按 `_DAY_OFFSETS = (0, 1)`
  渲染**两天** —— 成像日在前、次日在后（顺序即优先级，两天都在时命中成像日）。日期用 `date` + `timedelta`
  算（跨月跨年自然正确），不再字符串截取；模板若不含日期占位符，两天渲染出同一条，去重后仍只有一条。
  日期校验顺序不变：先按 `YYYY-MM-DD` 的位数/数字手查（`20260910` 那种必须先拒），再构造 `date`；
  形如 `2026-09-31` 位数对、日子不存在的，按 `PathDeniedError` 报「日期须为 YYYY-MM-DD」（原先会 500）。
- `POST /api/scenes/resolve`：候选多于一条（= 两天）时，404 的 `detail` 末尾补一句
  「（成像日与次日都找过 —— 盘阵按生产日建目录，深夜成像的景常记在次日）」。候选只有一条（自定模板不带
  日期占位符）时不加这句 —— 别硬塞一句和事实不符的话。
- 一处**连带的行为变化**：`..` 单独当名字时，旧代码靠那条扁平候选渲染出带 `..` 的路径、由
  `to_posix_array_path` 拒掉（`PathDeniedError`）；现在它拆不出 `{sat}`/`{mid}`，模板被跳过 →
  返回**空列表**（调用方回 400，语义同样是拒绝）。穿越守卫本身没动：名字里混进 `..` 并被拼进路径时，
  仍是「路径含 .. 穿越段」。两条都补了用例。
- 夹具从**平铺**改成**真六层生产树**：`test_scene_resolve.py` 的 `scene_dir` 挪到
  `…/2026/09/17/<卫星型号>/<段级目录>/<景级目录>`（新增 `day_dir` / `sat_dir` 两个属性），
  `.e2e/test-manual-scene.js` 的 SC / PAN 两个场景同样挪进去（夹具 python 侧与 JS 侧都按「段级名 = 景级名
  去掉景号那一段」现算）。**这带来一个硬约束**：六层树叠在临时目录前缀之下会顶爆 Windows 260 的路径
  —— 两个夹具的场景名一律换成**合成短名**（型号/传感器段压成一个字母，判据 3 位段号 / 4 位景号 /
  14 位成像时刻一个不少），真机那种 61 字符的全名只留在文档与 `test_paths.py` 的纯字符串用例里。
- 保留 `flat_scene_layout`：平铺部署仍存在，粘路径时的诊断措辞还要靠它按层数分派（那条用例照旧，
  只是改成**现搭**一个平铺目录，不再让整个夹具都平铺）。
- 新增回归用例：`test_reverse_lookup_hits_next_day_dir`（名字 0917、目录在 0918 → 200，
  钉的正是用户报的这一条）、`test_reverse_lookup_prefers_imaging_day`（两天都在 → 命中的是成像日）、
  `test_404_without_two_days_has_no_day_note`、`test_next_day_rolls_over_month`（09-30 → 10-01）。

**验证**：后端 **545 passed / 3 skipped**（`test_paths.py` 59、`test_scene_resolve.py` 61）；
e2e `test-manual-scene.js` **83 断言全绿**（真 uvicorn + 无头 Chrome，夹具已是六层树）。
文档同步：[production-scene-naming.md](../sr_code/production-scene-naming.md) §三 增「日期目录要试两天」、
§四 改写「旧扁平形态」那段；[api-contract.md](../planning/api-contract.md) §3.5 候选清单改两天 + 404 那句说明；
`deploy/sr-api.service` 的模板注释同步（**真机若配了 `SR_SCENE_PATH_TEMPLATE` 也要知道现在每天会试两天**）。

**留给下一个窗口**：这个修复**只改了后端**（`backend/pathguard.py` + `backend/api/app.py`），
所以真机换包时 `backend-*.tar.gz` **必须**一起更新 —— 只换 `dist` 包对这个 bug 毫无作用。

### 2026-09-18 · 文件卡的「状态」收成短标签，不再重抄文件名（开发机）

**起因（用户报）**：用户原话 ——「当前『已读取』+文件名和『没找到合法场景』+文件名是臃肿的真凶，
请你将文件名部分删减掉……可以在图属性下方加入『已读取』和『未找到目录』等标签在同一行」。
一张卡三行里三处同名：标题行是文件名，第二行 `已读取：<同一个文件名> · 512×512`，第三行是后端那一整句
`没找到合法场景目录 —— W:\…\<同一个文件名>：目录不存在；…`（还被两行截断，看不全）。

**改动**（只动展示，判定逻辑一个字没改）：

- `stores/viewer.ts` 两处状态串去掉名字与尺寸：`已读取：<file.name> · tw×th` → `已读取`；
  `场景就绪：<meta.name> · 元数据 W×H · JPG tw×th` → `场景就绪`。省掉的信息卡片上都还在
  （标题行 = 名字，图属性行 = 大小·W×H·布局）。**解码进度/失败那几条串一个字没动** —— 它们本来就不含
  文件名，且「完成」「解码耗时」「稀疏条带预览」这些字样被好几个 e2e 当路径判据用。
- `components/FileList.vue`：原「状态行 + 关联失败行」两行合成一行 `.tags`，各自是一个短标签
  （`.status` 保留原有 ok/err 配色，`.link-tag` 弱一档）。后端那句长文案改挂 `title`，卡片上只留一个词：
  默认「未找到目录」；命中的是**指纹不符**（目录在、文件对不上）时说「文件对不上」—— 两者不是一回事。

**注意（取舍）**：拖裸 `.tif` 关联失败时，详细原因（试过哪几条候选、各缺什么）现在**只在悬停提示里**
（拖 `.jpg` 那条路不受影响，长文案照旧进弹窗）。用户要求的是精简，若日后觉得该能一眼看到，
把它挪成一行可展开的详情即可。

**验证**：`vue-tsc` 干净、vitest **229 passed**（未新增/改动用例）；前端重建 dist 后
e2e `test-scenes.js` **65 断言**、`test-manual-scene.js` **83 断言**全绿。`test-scenes.js` 里那条
「状态行含元数据尺寸」的断言改为「状态是短标签」—— 尺寸本来就在紧挨着的 `rec.W/H` 断言里钉着。

**另**：用户先说过「缩略图鸡肋、注释掉」，随即改口「缩略图先别动」，所以左栏的小图预览横幅
（`FileThumb.vue`）**保持原样**，本次未改动。

### 2026-09-18 · 清单写回改走后端：真机 http 下浏览器根本写不了盘阵（开发机）

**起因（用户报）**：用户原话 ——「当前还有一个 bug：『这个浏览器不支持原地覆盖写盘』，可是当前浏览器
是 chrome」。真机复述：页面是 **nginx（`http://内网IP`，`deploy/nginx.conf` 是 `listen 80` 明文）**，
清单本身在**盘阵**上。

**根因**（不是浏览器的问题，也**不是偶发**）：原先的写回读 `window.showOpenFilePicker`，而这个 API 在
规范里是 **`[SecureContext]` 标的** —— Chrome 只在 `https://`、`http://localhost`、`http://127.0.0.1`
的页面上把它挂到 `window` 上。真机是 http + 内网 IP，所以这条路**在真机上永远点不通**：功能从一开始
就选错了对象（清单在盘阵上，而浏览器的文件选择器只能选到操作员**本机**的文件）。而后端本来就写得到
盘阵 —— 掩码、SR 产物、烘焙 JPG 全是它（`User=nginx`）写的。

**改动**（契约先落：`docs/planning/api-contract.md` §2 表 + §3.7，再写代码）：

- 后端新增 `POST /api/qclist/write`（`backend/api/platform.py`）：路径走 `to_posix_array_path` +
  `ensure_allowed(kind="file")`（与 `/api/scenes/resolve` 的 path 分支同一套），必须**已存在且是文件**、
  **只收 `.txt`**、可选 `mtime` 护栏（差 > 2 秒拒写，挡住「导入之后质检又更新了一版」）、写前
  `os.access` 查目录与文件权限、`mkstemp` 同目录 + `chmod` 保留权限位 + `os.replace` 原子替换。
  请求侧被拒一律 **400**（detail 说清哪一种），写盘 `OSError` → **422**；**没有**照搬 §3.5 那套
  400/403/404 分类（前端只把 detail 原样显示，分类没有消费者）。正文不设大小上限。
- 前端**净删** FSA 那条路：`stores/qclist.ts` 的 `FsaHandle/FsaPicker/fsaPicker/句柄/forgetTarget` 全删，
  `lib/qclist.ts` 的 `encodeQcText`（连同它的 2 条 vitest）删掉 —— 留着只会让人以为「前端也能编 GBK」。
  目标从「文件选择器选出来的句柄」换成**操作员粘的盘阵路径**（`targetPath`，进 localStorage），页脚是
  一行「路径输入框 + 同步（空路径时禁用）」。
- **顺带修好的两件事**：① GBK 清单以前只能降级成 UTF-8+BOM 写回（浏览器只有 UTF-8 编码器），现在由
  后端编码，**GBK 进 GBK 出**；② 写盘这一步**第一次进了自动化回归**（原先是系统弹窗，点不了）。

**已知副作用（必须知道）**：原子替换后文件的**属主变成跑 API 的 nginx**（nginx 无权 chown 回去，
权限位保留）。质检部门若还要直接编辑这份 txt，清单所在目录得给组写权限 —— 已写进 `deploy/README.md`。

**验证**：后端 `pytest backend/tests -q` **554 passed / 4 skipped**（新增
`TestQcListWrite` 10 例：正常写回逐字节比对、GBK 按 GBK 读回、权限位 0o640 写前写后一致（Windows 上
跳过）、mtime 护栏放行与拒绝、目录/不存在/非 `.txt`/白名单外/穿越段/坏编码/GBK 编不出的字符各条拒绝
路径下**原文件一字节不动**、以及桩掉 `listdir/scandir/walk/iterdir` 断言写回**不列举任何目录**）；
前端 vitest **227 passed**（删 2 条）、`vue-tsc` 干净；e2e `test-manual-scene.js` **98 断言**（新增 H 段：
Python 转码造一份 **GBK** 清单 → 导入认成 gbk → 粘 `W:\…\待修复清单.txt` → 同步 → **从磁盘按字节读回**，
未改动时与原文完全一致、标终态后上半部分逐字不动；不存在路径 → 后端原话进错误条且不新建文件）、
`test-vue-viewer.js` **58**、`test-scenes.js` **65** 全绿（钩子名清单补了 `qcSetTarget`/`qcSync`）。

**另**：真机首测只剩两件人工事 —— 粘一次真实路径看是否写进去、确认那份 txt 的目录对 nginx 可写
（`deploy/README.md` 里那两条注意事项）。

### 2026-09-19 · 预览烘焙档位改成用户可调（全局 5 档），拖入链的产物改落盘阵（开发机）

**需求**：用户提「拖入 `.tif` 时通过下采样获取同名 `_preview.jpg` 至盘阵，下采样精度可用户调节，
放在定位组件右侧做成分档拖动条」，随后改口径为**全局**（三条入口同档）。

**定了什么**（用户拍板）：档位 = 各边 ÷2 · ÷4 · ÷8 · ÷16 · ÷32，**默认 ÷4**；作用域 = **全局**
（拖入 / 场景库 / 粘盘阵路径三条入口都按滑块当前档位烤）；拖入链落点 = **盘阵场景目录
`<源同目录>/<stem>_preview.jpg`**（新）；其余两条落点不变（`<stem>.preview.jpg`）；
临时缓存**保留但降级为兜底**（场景目录写不进去时才用）。

**为什么改**：尺度和拉伸原先写死在各处（`PREVIEW_SCALE = 0.5` + 前端两句硬编码「1/2」）。
真机上一张 2.4 万像素级的源，1/2 烤出来是 1.5 亿像素、q85 下上百 MB，首次打开要读一遍整幅大图
（几十秒）+ 传输 + 浏览器解码，用户没有回旋余地。拖入链那份落临时缓存、次日 0 点删，等于
**每天第一次拖入都要重烤一遍**。

**改了什么**：
- 后端：`PREVIEW_DIVISORS = (2,4,8,16,32)` 为唯一档位真源；`preview_max_edge(src, div)` 取代
  `PREVIEW_SCALE`；`ensure_preview_jpg(..., div=)`（尺寸与规则戳同一个入参推导，结构上不可能不同步）；
  规则戳 bump `v2` → **`v3`**、档位进戳 `srprev:v3:div<N>+equal:q<Q>`；端点改名
  `preview-tmp` → **`preview-drop`**、落点换 `paths.drop_preview_path`、两条端点都接 `div`
  （非法 400）；先 `os.access(dir, W_OK)` 预判，不可写/写失败 → 退回临时缓存 + 响应头
  `X-SR-Preview-Fallback: tmp`（已加进 CORS `expose_headers`），两条都失败才 422。
- 行上新增 **`previewDiv`**（`_scene_row` / `_manual_row`）：从盘上那份 JPEG 的注释戳解出它是在
  哪一档烤的，按 `(path, mtime)` 缓存，读不出 → `null`；`hasPreview` / `jpgUrl` 语义一字不动。
- 前端：`lib/scene.ts` 加 `SCENE_PREVIEW_DIVS` / `DEFAULT_PREVIEW_DIV = 4` / 档位直读 localStorage
  的纯函数（**不依赖 Pinia 创建顺序** —— `stores/scenes.ts` 会用到它，那时 viewer store 未必建过）；
  `sceneImageUrl` **只对 `.preview.jpg` 结尾的 URL 拼 `?div=N`**（击穿 nginx `max-age=3600` 的唯一手段，
  且无条件拼会打红「源本身就是 JPG」那条行）；`Toolbar.vue` 在定位组件右侧插 5 档 range。
- **本轮唯一的真陷阱**：前端那次「先打 `/preview` 重烤、再取静态 URL」是**带条件**的（只在
  `!row.hasPreview` 时才打），而 `hasPreview = jpg.is_file()` **不认档位** → 滑了滑块之后盘上那份
  仍在 → `hasPreview` 仍为真 → 跳过重烤 → **看到的还是旧档位那张图**，磁盘上一个字节都没变。
  红的是凡有静态 URL 的行；库外手工行每次走 `/preview` 回字节，不受影响。这就是 `previewDiv` 的来由。
- **试过又退回来**：把 `/preview` 的响应体直接当 blob（一次下载、不补静态 URL）。被
  `.e2e/test-scenes.js` 打红 —— 那条断言要求「静态读图走 nginx 的 `/disk-array/` alias 位」，
  几 MB 的 JPEG 不该占 API 进程的内存与带宽。改回「先打 `/preview` 触发烘焙（字节丢掉）+ 再取静态 URL」：
  稳态下只有一次下载（换档后的正确性已由 `?div=N` 保证 —— 缓存键变了，第二次取的一定是新图）。

**验证**：后端 **567 passed / 4 skipped**、前端 **238 passed** + `vue-tsc --noEmit` 零错误 +
`npm run build`；e2e `test-manual-scene.js` **105**（含新增的 1366 视口工具栏不溢出 + 拖动条位置/档位
四条）、`test-scenes.js` **65**、`test-vue-viewer.js` **58**、`test-locator.js` ALL PASS。
`test-platform.js` **22**，**三跑一红**（见下）。

**两处与本次改动无关、但本机跑不干净的地方**（都核过不在本次 diff 里，都不动）：
- `test-regress.js` / `test-sparse.js` 跑不出 —— 要 `test-tifs/types/big_u16.tif`（8192² 134MB），
  该 fixture 由 `.e2e/gen-test-images.py` 生成、`test-tifs/` 已 gitignore，本机没生成。
- `test-platform.js` 偶发红在「耗时列给出终态耗时（—）」：`.e2e/test-platform.js:172-173` 把假调度器
  阶段设成 `SR_SLURM_FAKE_T_MS=250`（RUNNING 窗口 = 250ms 宽）而校准广播周期是
  `SR_QUEUE_POLL_SEC=0.3`（300ms）—— **轮询周期比它要观测的窗口还宽**，于是会整个跨过 RUNNING，
  直接由 PENDING 跳到终态。`platform.py:179` 的 `mark_started` 只在**观测到**进入 RUNNING 的那次转换上
  打点，这一次没打上 → `started_at` 留 NULL → 耗时列如实显示「—」，断言 `/^\d+ 秒$/` 红。
  **只在假调度器这个配置下出现**：真机本机直跑一次 200+ 秒，2s 周期必然抓到 RUNNING。
  修法是一行（把 `SR_QUEUE_POLL_SEC` 调到明显小于档位宽，例如 `'0.05'`），但那是 09-18 那条改动的地盘，
  本次不越界去改。

**换包须知**：① **必然重烤一轮** —— 盘上所有 `<stem>.preview.jpg` 的戳是 `v2`，新代码认 `v3`，
逐个场景首次打开时重烤（**惰性**，不是一次性全量；把滑块停在 ÷2 也一样）；② `frontend/dist` 与
`backend` **必须同包更新** —— 端点改了名，只换一个会 404。

**真机待确认**：① 服务账号（`User=nginx`）对场景目录有没有写权限 —— 这决定拖入链走主落点还是兜底；
② 新落点 `<stem>_preview.jpg` **没有任何清理者**且**不吃 `SR_PREVIEWS_ROOT`**（配了镜像树的部署下
同一场景会有两份缓存），磁盘预算要把它算进去。

**并更正一条旧约定**：本文件 2026-09-17 那条「临时路径要单独设 `max_edge` 就得独立一套规则戳」
**已被「档位进同一个戳」取代**，别再按那条旧字面实现两套戳（见
[knowledge/preview-bake-pipeline.md](../knowledge/preview-bake-pipeline.md) §4.6）。

### 2026-09-20 · 查看器「图像对比」：一个画布两个格子 + 共享变换（开发机）

**需求（用户原话）**：「定位那一栏加入按键入口『图像对比』，下拉框默认关闭，可切换：点选对比 & 分屏对比。
分屏对比的业务逻辑是：常规拖进一张 `.jpg` 时默认关联盘阵，拖入一张新的会覆盖掉，左侧新增一条图像信息结果；
一旦拖动滑块至两侧，那么拖动新的 `.jpg` 进入时会左右分屏，并自动收回右侧工具栏；即将分屏时会有左右落位提示。
点选对比就是：即将拖入新的 `.jpg` 时落位会有区域提示，然后简单地覆盖掉，通过点选可以在点选清单中来回切换；
点选清单会显示在《待修复清单》上方，可以随时清掉指定的几张图」。

**本轮定的口径**（用户拍板，与计划一致）：入口 = 工具栏「定位」组一个按钮，点开在**工具栏下方**展开一条
（**不放进工具栏**：那条有「1366 下不横向溢出」的 e2e 守卫，三个模式 + 图例 + 芯片塞进去必然溢出）；
三选一用按钮组而不是下拉框（与全仓既有 tab 形态一致）；三类图来源 = **拖文件 + 场景内快捷入口**；
拉伸档位 = 对比模式下**改拉伸同时刷两侧**（关闭模式仍是每张图各自记，`test-scenes.js` 那条断言因此一字未改）。

**核心改动**：把「视图变换」从全局单例拆成**按侧**（`viewA`/`viewB` + 按侧的可写 computed `view`），
而渲染仍是一个画布 —— 每格 `save → rect+clip → translate(rect.x, rect.y) → drawImage`。
让既有代码零改动的关键是 **`activeId` 仍是「活动侧那张」**：`placeRec(id, side)` 在激活开头把它与活动格对齐，
于是掩码、ROI 统计、云量卡、任务状态、待修复清单选中、状态栏、工具栏可用性约 40 处消费点自动跟随。
新数学只有四件：`clampSplitRatio` / `splitRects` / `paneAtX` / `normAnchor`+`anchorAtLocal`+`wheelZoomBoth`+`panBoth`，
全在 `lib/viewMath.ts`，全有单测。

**真 bug（本轮最有价值的一条）**：同步缩放的锚点第一版算的是「画布局部坐标」
（`anchorAt(rect,u,v) = rect.x + u*rect.w`），而每格的变换是**格局部的** —— 右格的 `ViewState.ox` 里没有
那个左格宽。后果：滚轮一滚，**非指针那格整体平移一个左格宽、图直接飞出视野**；指针在右格时连它自己的锚点也错。
修法：删掉 `anchorAt`、换成 `anchorAtLocal(rect,u,v) = [u*rect.w, v*rect.h]`，参数按**角色**命名
（`pPane`/`oPane` + 显式 `pointerSide`，光看两个 `PaneRect` 分不出哪个是 A）。
**两次教训**：① 我自己的单测**当时是按错语义写的，全绿** —— 所以修完必须在单测里补一条**反向断言**
（旧公式与新公式算出的 `ox` 至少差 1），否则「改好了」与「这条用例本来就不会发现」长得一样；
② 第一次修完还是错的（参数名按「是不是 A 格」命名，指针在右格时整体错位），是新增的右格用例抓出来的。
e2e 侧随之把分隔线位置暴露成 `splitX()`（画布局部坐标里的左格宽），**落点判据与测试共用一个值**，
不在测试里自己拿比例乘一遍（比例是浮点、`splitX` 已取整，自己乘会差不到 1px、正好压在分隔线上时判到另一格）。

**另一处易错点（写进了契约 §4.7）**：`dropEffect` 在任何模式、任何位置都保持 `copy`。
画布外卖成 `'none'` 能拿到系统的「禁止」光标，但按规范它**同时会抑制 `drop` 事件** —— 而 `.txt` 必须在
任何模式、任何位置都能进待修复清单；且 `dragover` 阶段 Chrome 不暴露文件名，没法按类型区分。
所以视觉提示只由 overlay 负责，**真正的门在 `drop` 处理里**（`.txt` 先分流，再判影像的落点门；
顺序反了，画布外拖一份 `.txt` 会被那道门连坐吞掉）。

**新增测试设施**：`.e2e/lib/drag.js` —— 全仓第一个 DragEvent 模拟。既有脚本一律走 `input.uploadFile`
（文件选择框那条路），而拖放这条路监听在 window 上，**至今没有被测过**。两个入口**不能合并**：
`dropFiles` 发 dragover + drop，`dragOverOnly` 只发 dragover —— 落位提示在 `onDrop` 里被同步清掉，
要观察提示就必须在 drop 之前单独问一次。兜底：某些 Chrome/Edge 造的 `DataTransfer` 里 `files` 是空列表
（页面只读 `dataTransfer.files`），长度不符时在实例上 `defineProperty('files', …)` 覆盖。

**验证**：前端 vitest **288 passed**（新 `compare.test.ts` 14 例 + `viewMath.test.ts` 扩到 48 例）+ `vue-tsc` 零错误
+ `npm run build`；e2e `test-vue-viewer.js` **122**（H 段 64 条新断言：关闭态无条无清单 → 三选一默认关闭 →
分屏两格且 `rectA.w+rectB.w===画布宽`、右栏自动收起 → 落位提示只剩一侧 → 落右半（+1 rec、活动侧转 B、
左格不动、非活动侧也重新适配）→ 落画布外（有 toast、不新增）→ `.txt` 拖到侧栏照旧导入 → 真滚轮 ×1.2
两侧同步 + 拖动两侧同增 40 → 分隔线 0.7/回正 → 点选对比覆盖 + 清单点行/清除 → 回关闭后拖侧栏仍能打开）、
`test-manual-scene.js` **131**（J 段 17 条：真后端下拖盘阵那份 `.jpg` 到**右半** → `resolve +1`、
`/preview-drop +1`、生产那条 `/preview +0`，新图落在右格、左格那张不动，只发 dragover 时**请求数 0**）、
`test-scenes.js` **79**、`test-platform.js` **23**；后端**零改动零回归**：`632 passed / 4 skipped`。

**踩到的测试坑（值得记住）**：`.jpg` 走 `openLocalImage`，它先 `await decodeJpgToCanvas(file)` 才 push rec ——
所以「drop 之后同步读条数」必然是没变，那不是缺陷。`.tif` 那条（`openOne`）才是同步入列，
`test-vue-viewer.js` H 段断的瞬时 +1 是它。两条路各有各的口径，别互抄断言。

**未动**：`.e2e/qa-theme.js` 的 6 条 FAIL 是**陈旧期望**（2026-09-09 restyle 之前的白色 chrome / 渐变主按钮，
另两条依赖当时不存在的运行时数据）。已核**不是本轮引入**：`git diff` 里 `style.css` 只加 `--cmp-*` 15 行、
`Toolbar.vue` 只加新规则、**零删除行**，而 `.toolbar` 的 `background: var(--band-grad)` /
`--chrome: #DFE9E4` 都来自 09-09 那次 restyle。改脚本期望值还是改主题，得由人定。

### 2026-09-20 · 对比模式换图保持视图 + 取图路径提速（开发机）

**需求（用户原话）**：「现在的点选对比有个问题，每次切换图，图的缩放和位置都会回到初始化默认形态，
我认为两个参数应该保持现状」；附带「烘焙 1/2 切换延迟略高」。语义由用户选定为**按相对视野换算**。

**四条改动**（后端零改动、零新增端点）：

1. **换图保持视图**：新增 `lib/viewMath.ts::remapViewForImage` —— 归一化坐标 = 缩略图像素 ÷ 该图自己的
   缩略图尺寸，于是「归一化」等价于「整幅图的相对位置」。**以水平为准**（两格左右并排，宽度是稀缺维度）：
   归一化可视宽度铺满格子宽度，纵向按新图比例延伸。**两张缩略图尺寸逐字相同时原样返回入参**
   （`100/1314*1314 = 99.99999999999999`，不早退的话「同尺寸换图视图不变」只能断到 `1e-9`）。
   同长宽比不同分辨率时 `ox`/`oy` 一个数不动、`scale` 按像素比走（`scale × 图宽` 不变，这正是
   「同一片地面」的代数表达；我一开始按「`ox` 减半」写单测，被实测推翻）。
2. **`viewFor` 记账**（store 内普通对象，不进响应式）：记的是「**这一格的 `ViewState` 是按哪个缩略图尺寸
   算出来的**」，`null` = 空着/没适配过。判据 `compareOn && viewFor[side]` 真才 remap、否则老实 `fit`。
   为什么记尺寸不记 rec id：真正决定能不能 remap 的是「此前摆在这格的是多大的图」，rec id 会漏掉
   「同一张图换格子」和「两格互换」两类。三条后果都钉在 e2e 里：**空格落图仍 fit**（`viewFor.B === null`，
   `test-vue-viewer.js:652-653` 那条既有断言是刻意保住的）、**关闭模式一字不改**（`compareOn === false`
   一律 `fitView`，与改动前逐字等价）、**互换按「视图跟格走」**（罕见路径，注释里写明取舍）。
3. **入口先去重**（`openSceneSibling`）：先拿 `/siblings`（必须问，才知道对应哪条 rec），目标项若已有
   rec 开着就直接切过去、**不发 `/preview`**。于是「同一枚芯片连点两次」= `/siblings +1`、`/preview +0`。
4. **预览 blob 缓存 + 后台预取 + 右上角设置浮层**：缓存按**字节封顶的 LRU**（128MB）存**压缩态 blob**；
   键 `{id}|{div}|jpg`，源是显示件且同名栅格胜出时另一支 `{id}|{div}|ras:{栅格名}`（**两支不能串味**）；
   命中时**照做网络路径那两行副作用**（`hasPreview` / `previewDiv`）但**不调 `onPhase`**。
   预取三道门（`compareOn && cmpPrefetchOn && activeSceneId()`），合格项判据
   `exists && id && name && W/H 非空 && hasPreview && previewDiv === div && 未开 rec` ——
   这条判据本身就是「服务端已有一份现成预览」的定义，于是**结构性地保证预取永不触发烘焙**
   （真机上不允许「我什么都没点，盘阵却在读大图」）。取消用**代数计数**而不是 `AbortController`：
   取图那两个 API 不收 `AbortSignal`（要兼容静态 URL 那条支路），掐不断在飞的请求，只能「不开始下一项」。

**一处决策被我自己改掉（已说明理由，用户批准）**：用户原选「按 `sceneId+档位` 缓存**已解码位图**（带 LRU
上限）」，按内存算术改成缓存**压缩 blob** —— rec 的像素本来就常驻且不封顶（`thumb` 画布 4 B/px +
`src` Float32Array 4 B/px = **8 B/px**，尺寸是服务端 jpg 的原生尺寸，÷2 档一条几百 MB），
再缓存位图只会翻倍；而位图缓存只对「关掉再打开」有用，收益极小而代价极大。

**入口位置**：工具栏**最右端**（「提交 SR」之后）一个文字按钮「设置」→ 锚在右上角的浮层（`position: fixed`
挂在页面根，不挂工具栏 —— `.toolbar` 是 `position: static`，挂它里面会以 ICB 为包含块）。
浮层内容是两行：预取开关（`role="switch"`，**默认关**，持久化在 `localStorage['sr.viewer.cmpPrefetch']`）
与「本地预览缓存 N 项 / X MB」+「清空」。**刻意不用 `.btn` 类名**：`qa-theme.js:179` 取 `.toolbar .btn`
的第一个，别去动它。1366 工具栏宽度守卫（`test-manual-scene.js`）改完实测仍绿（`scrollW=1366`）。

**验证（开发机，全绿）**：前端 vitest **317 passed**（新 `blobCache.test.ts` 12 例 + `viewMath` 扩 6 例 +
`scene`/`api` 各扩若干）+ `vue-tsc` 零错误 + `npm run build`；e2e `test-vue-viewer.js` **148**（新 I 段
设置浮层 11 条 + J 段换图保持视图 8 条）、`test-manual-scene.js` **160**（新 K 段 29 条：芯片两次点击的
增量、开关关时进对比模式零请求、**盘上没有现成预览时预取一个字节都不取**、补一份现成的之后恰好取那
一份且 URL 逐字对上、预取不建 rec / 不换活动图 / 不占遮罩 / 不进清单）、`test-scenes.js` **79**、
`test-platform.js` **23**；后端**零改动零回归**：`632 passed / 4 skipped`。

**K 段的夹具要点（下轮别重踩）**：夹具里**没有 SR 产物**（真机才有），而 K 段要验的正是「盘上已有一份
现成的预览时预取会去取它」—— 于是测试自己在运行期造两份 `<输入名>_<suffix>.tif`（名字取 `/siblings`
回报的 `productCandidates[0]`，**不重复实现命名规则**）、并用 Node 侧 `fetch` 调 `/preview` 把其中一份烤上。
Node 侧的请求**不经过页面**，所以不污染 `page.on('request')` 那套增量计数（`countUrl`）。
另外「已开着的那张」会被预取的过滤条件排除：J 段收尾时活动图是拖进来的 `WIN.jpg`，而 resolve 给这一行
的 id 是**栅格输入影像**的（`_manual_row(inp,…)`），所以它的 `sceneId` 与「输入影像」那一类相同 ——
预取的合格项因此**恰好是自造的那一份**，这条断言才有意义。

**未动（仍留着）**：① 「攒图吃内存」——rec 的 `thumb` 画布 + `src` Float32Array **永不释放**（8 B/px），
本轮新增的缓存是按字节封顶的压缩 blob，不加剧它，但「对比模式 + 预取」会让这张图更容易被撑大，仍只记录；
② 服务端**按档位分文件落盘**（现在一份文件一个档位、换档原地重烤，是「切换 1/2 略有延迟」的另一半原因）；
③ `fetchDropSceneJpg`（拖入链 `/preview-drop`）**不进** blob 缓存 —— 它在写盘阵，语义不同；
④ 把预览档位滑块搬进设置浮层（能腾出约 170px 宽度预算，但既有 e2e 引用 `data-e2e="preview-div"`）。

**两条等用户定夺的旧问题（本轮仍未动）**：① `.e2e/qa-theme.js` 的 FAIL —— 得先纠正一个数字：
**现在跑出来是 9 条、不是上一轮记的 6 条**。本轮用 `git stash` + 重建 dist 实测对比过，改动前后
**FAIL 集合逐字相同**（`diff` 无差异），所以都不是本轮引入；条目全是陈旧期望（白色 chrome / 渐变主按钮 /
09-09 restyle 前的深色文字）。改脚本期望值还是改主题，仍得由人定。② 计划 §四 里那两条
（`exists:false` 时芯片**预先禁用**、行尾标 `suffixFrom`）本轮没做，仍待定。

**顺带记一个既有 flake（不是本轮引入）**：`test-platform.js` 等「急烤落定」的条件是
`t.preview_state` 非空，而服务端认领任务时先 CAS 写 `preview_state='running'`、`preview_note` 还是 `NULL`，
所以可能正好读到「running 且 note 为空」→ 断言消息里那句候选名成了空括号 `()`。本轮撞上一次、重跑即过。
要根治得把等待条件改成「**非 running** 的终态」，属另一个模块的测试，未擅自改。

### 2026-09-21 · 分屏对比里「在画面内拖动」撞上「换格 / 改分隔」（开发机）

**用户原话**：「主要是在画面上拉的时候，会有交换的提示（尤其是刚打开分屏对比的时候），和画面内拉的放大看图
逻辑撞了；我认为增加左右交换的判定阈值」。先用提问钉住两个事实：那次拖动「没拖文件，纯在画面上拉」；把
「输入影像」这份文件拖到右半幅的期望结果是「右格=输入影像，左格=本次产物」——**即两格互换是正确行为，必须保留**。

**定位结论：一次纯平移换不了格**。所以问题不在 `placeRec` 的互换分支，而在「让人以为马上要换格」的提示与
分隔线抓取带。证据三条：① 平移走 `.view-canvas` 的 `mousedown` → `onPan` → `panBoth`（两格同一屏幕位移），
这条路上没有任何 `placeRec` / `activate` 调用；② `placeRec` 里唯一的换位是 `otherId === id` 那一支（把已在对
面那半的图归位到本半），只能由 `activate(id, side)` 到达；③ 现实里到得了的入口只有两条 —— **文件拖放到另
一侧**、**点文件列表/对比清单里那张已在对面的行**（场景芯片与文件框给的 `side` 是 `activeSide`，同一侧不会换）。

**三条真撞法（各自独立，都已修）**：

1. **落位提示对「页面内拖放」也亮**。`onDragOver` 以前只判 `compareOn` 就 `setDragHint(true, dropSide(e))`，
   而页面内拖一段选中的文字、拖个链接同样会发 `dragover`（`drop` 时 `files` 为空、`onDrop` 直接 return）。
   于是分屏下在画面里随便拖一下，白纱 +「放在左侧 / 放在右侧」就亮起来 —— 正是用户说的「交换的提示」。
   改：`TifCanvas.vue::dragHasFiles(e)` 判 `e.dataTransfer.types` 里有没有 `'Files'`，没有就
   `setDragHint(false, null)`；`preventDefault()` 与 `dropEffect = 'copy'` 仍**无条件**（不拦会让浏览器导航到拖进来的文件）。
   实测：`lib/drag.js::dragOverText` 发一条只带 `text/plain` 的 dragover → `types` 无 `Files`、`hint.active === false`、
   DOM 里没有 `.cmp-hint`，而 `defaultPrevented === true`；同位置带 `Files` 的对照仍亮且 `side === 'B'`。
2. **解码遮罩上的文字可选**（`user-select` 默认开着）→ 在遮罩上按住拖动会先拉出一个文本选择：画布收不到
   mousedown（平移一动不动），且此后**每次**在选中文字上按下拖动，Chrome 都按「拖选中内容」走原生
   drag-and-drop → dragover → 提示亮起。这是「刚打开分屏对比时」尤其明显的成因：那一刻遮罩正显示「正在解码…」。
   改：`.decode-mask { user-select: none; }`，**只禁选中、不放开点击** —— 生成掩码/合并期间画布本该是惰性的
   （`onCanvasDownDraw` 一直没有 busy 守卫）。**这条是代码事实 + Chrome 行为推断，不是实测**：无头环境下按不住那块遮罩。
3. **分隔线抓取带没有判定阈值**。9px 抓取带（`width: 9px; margin-left: -4px`，`pointer-events: auto`）正压在
   「刚进分屏时居于画布正中」那条线上，而旧 `onDividerDown` 一按下就按**指针绝对位置**改一次比例 → 线被吸到
   指针上，图不动、左右在换。改（`CompareOverlay.vue`）：`DIVIDER_DRAG_THRESHOLD = 4` —— 挪过 4px 之前什么都
   不做（不改比例、不吸指针、不设 `draggingDivider`），过阈值之后按**位移增量**改比例（`startRatio + dx / 宽度`）。
   实测：在 `splitX()+3` 按下 → 移到 `+5px`，比例变化在 1e-12 内、视图一格不动；再在 `+3` 按下 → 移到 `+23px`，
   比例 = `ratio0 + 20/宽度`（±1e-6），且与「按绝对位置算」的 `ratio0 + 23/宽度` 相差 > 1e-4 —— 后者正是旧行为
   「吸指针」的可执行判据。

**保留**：`placeRec` 的互换分支一字未动（用户已认定「拖到另一侧 = 两格互换」是期望行为）。三种手势的分工
（平移 / 换格 / 改分隔）与判定阈值一并写进 [gui-experience.md](../experience/gui-experience.md) §10.8。

**验证**：`vue-tsc` 零错误 + `npm run build`；前端 vitest **317 passed**；e2e `test-vue-viewer.js` **161**
（新 11 条：真鼠标 40px 拖动分别起于 `cmp-tag-b` 与 `.cmp-empty-text` → `getSelection()` 为空且只看 A 的 `ox`
精确 +40；`dragOverText` 三条；分隔线阈值两条 + 位移增量一条）、`test-manual-scene.js` **160** 全绿。

**顺带记一个既有 flake（不是本轮引入，也没擅自改）**：`test-manual-scene.js:964/969` 连发两次
`uploadFile(upWinJpg)` 之间没有任何等待，而 `uploadFile` 在 CDP 应答时就返回、页面的 `change` 处理器要晚一
拍才跑；第 969 行那次上传的「同名同字节去重」于是有机会还没看到第 964 行建出的那条 rec → 建出第二条 rec →
E4 末尾那条严格等于 +1 的计数断言读到 `drop +2`。本轮撞上一次、原地重跑即过（160 全绿），并另用一段临时探针
确认去重本身正常（同一份文件连传三次仍只有一条 rec）。根治要么在两次上传之间等 rec 出现、要么放宽那条断言，
属另一段测试的取舍，未动。

### 2026-09-21 · 左栏卡片拖不进画布 + 拖入的产物 jpg 关联不上盘阵（开发机）

**用户原话**：「查看器左侧栏目图像无法拖动到中间区域预览，而是只能与文件夹交互；拖动进来的 jpg，
文件夹不会生成 `NOSR_preview.jpg`，而且 preview 无法关联至盘阵」。开工前用两轮提问钉住口径：
拖的是**场景目录里的显示件** `<目录名>.jpg`；**真正要超分的永远是本体**（RC 场景的 `PAN.tif`、SC 场景的
`<目录名>.tif`），中间产物的 preview 只需要「关联到盘阵 + 明确提示不参与修复」；左栏卡片要能用
「盘阵 + 序号 + 环节标（`PAN`/`SR`/`NOSR`）」区分同一景的不同环节；拖 jpg 之后**后台静默烤**
一份 `<源名>_preview.jpg`（不阻塞、不弹遮罩、不报进度，展示像素仍用用户拖进来那张）。

**Bug 1 是双重的、且这条链根本不存在**：`FileList.vue` 的 `.file-item` 没带 `draggable`（浏览器压根
不发 `dragstart`），而 `TifCanvas.vue` 的落图门只认 `dataTransfer.types` 里的 `'Files'`，页面内拖放
（不含 Files）在 `onDrop` 里直接 `return`。改：卡片加 `draggable="true"` +
`@dragstart/@dragend` → store 的 `startRecDrag/endRecDrag` 往 `DataTransfer` 里放自定义 MIME
`application/x-sr-rec`（不用 `text/plain`：那会让画面上拖选文字也被当成换图）；画布的门放宽成
`dragHasPayload`（`Files` **或** `REC_MIME`），`onDrop` 在 `files.length` 那道早退**之前**先读
`REC_MIME`，命中就 `activate(id, side)`（`side` 仍由 `dropSide(e)` 给，分屏下按落点进格）。
`.txt`/影像文件那条老路一个字没动。

**Bug 2 有两半**。前半：本地 jpg 胜出那条分支（`localJpg && !useServer`）**一次服务端往返都不发**，
自然没有 `_preview.jpg` 落进场景目录 —— 改成本地赢时也 `void fetch(/preview-drop).catch(() => {})`
一发（不 await、不占遮罩、不看结果、不发 `onPhase`）。后半：中间产物的目录名是从**整个文件名**反推的，
带着 `_sr` 尾巴的那份名字反推出的目录根本不存在，所以它连 resolve 都过不去。改：jpg 专属的
**第二阶段候选**（`de_suffixed_stems` 去尾段后重新反推，尾段两种读法各给一条；**只在前一阶段全落空时
才展开**，常见情形一个 stat 都不多花），命中后再过 `stage_of_jpg` 认环节 —— **真门是「同级栅格真的
在」**，名字切得干净只说明它长得像产物名（`_cloud.jpg` 同样切得干净），另配 `_NON_STAGE_TAILS` 小名单
挡住那些真有同名栅格的派生件。`resolved` 随之多出 `kind` / `suffix`。

**唯一的破坏性风险是「产物上画掩码写回本体」**：`bakeMaskToServer` 是拿 `rec.lqPath` + `rec.W/H` 去
POST `/api/masks` 的，产物尺寸的 W/H 配上本体的 lq_path，后端会把一张产物尺寸的掩码**静默写到本体的
掩码文件上**。两道锁：服务端 `kind != 'input'` 时把 `row.lq_path` 置空、`sr_capable=false`、
`mask_path=null`（第一道），前端 `stageKind` 门（`lib/stage.ts`）+ 工具栏置灰（第二道）。
`row` 同时改为描述**该环节自己**那份栅格 —— 产物的 W/H 是本体的倍数，拿本体的尺寸建画布整张比例都是错的。

**顺带修掉一个既有缺陷（本次新候选把它顶了出来）**：`backend/pathguard.scene_name_layers` 的段数
守卫只挡到 `_SCENE_IDX`，六段的名字会走进 `seps[_SCENE_IDX]` 越界 —— 本该 400 的输入变成 500，
uvicorn 直接断连，浏览器侧表现为 `TypeError: Failed to fetch`（第一次 e2e 跑就是死在这里）。
修法是补齐门槛（段数不足即判不合规则），不是加 try/except：改完 `infer_scene_paths` 对它返回 `[]`，
那条候选自然被丢掉，错误码回到 400。两条新用例钉住（`test_paths.py` 六段名 → `None`；
`test_scene_resolve.py` 七段名的 jpg 拖入 → 200，末段换成 `002` → 404 且 `detail` 仍含「目录不存在」）。

**一个流程上的取舍**：计划里 `/siblings` 每项补 `suffix` 那一条**没做**。落地时发现响应顶层本来就有
`suffix` / `suffixFrom`，前端 `openSceneSibling` 用的就是它，再加一份是重复。芯片打开那条路同时补上
`stageKind: item.kind` —— 否则同一份产物会变成「拖进来不能改、芯片打开能改」两个说法。

**验证（开发机）**：后端 `python -m pytest backend/tests -q` → **642 passed / 4 skipped**；
前端 `npm test` → **325 passed**（新增 `lib/__tests__/stage.test.ts` 8 条）、`vue-tsc` 零错误、
`npm run build` 通过；e2e `.e2e/test-vue-viewer.js` **161** 项、`test-scenes.js` **79** 项全绿
（这两套里有对 `.name` 文案的整串比对，卡片上的小标因此只在 `route==='jpg'` 且关联上了的行上渲染，
且模板里刻意不留换行 —— 换行会被 Vue 编成空白文本节点，`textContent` 就多一个空格）。
`test-manual-scene.js` → **194 项断言**（新增 L 段 L1–L5：卡片自己带票拖进
画布 / 分屏按落点进 B 格 / 对比模式画布外沿用那句 toast；拖产物 jpg → 卡片上「盘阵+序号+SR」三标齐全
且序号与本体**同号**、三颗修复按钮置灰、`<编号>_sr_preview.jpg` 落盘且**烤的是产物自己的栅格**
（1600×800，与本体那份 800×400 差一倍）；三道修复入口逐条打一遍 —— 绘制掩码被拒且不进绘制态、
保存掩码返回 false 且**本体那份掩码的 mtime 一个字节没动**、提交 SR 不跳队列页且队列一条不多）。

**记一个既有 flake（不是本轮引入，已用对照实验定性，未擅自改）**：`.e2e/test-platform.js` 的
「耗时列给出终态耗时（—）」会偶发失败（页内那行已显示「完成」，但耗时列还是「—」—— 页内那份
停在上一次 GET 的快照上、SSE 帧还没到）。做法是 `git stash` 掉本轮全部改动 → 重新 `npm run build`
→ 同一台机器上连跑对照：**基线 2/4 通过、带本轮改动 2/3 通过**，两边一样地偶发，与本轮无关。
另有一条同类的（产物急烤那条：`waitFor` 的判据 `t.preview_state` 会把中间态 `'running'` 也算命中，
于是偶发读到 `running` 而非 `skipped`，note 为空）。两条都属「测试自己的等待条件不够严」，
要根治得改等待判据（前者等耗时列 != '—'、后者排除 `'running'`），留待下一轮。

**真机待确认**：① 拖产物 jpg（真机上由 SR 跑出来的那份）能否关联上、三颗小标是否如预期；
② `SR_SCENE_PATH_TEMPLATE` 非默认的部署上，去尾段反推是否仍成立（默认模板下候选目录名就是由名字
拼出来的，这一条通常直接成立）；③ 左栏卡片拖放是页面内行为，与 `http://内网IP` 那套限制无关，但
**真机上「拖进来看」的操作习惯要用户确认一遍**。

### 2026-09-21 · 顶栏品牌区：平台改名 + logo 预留区（开发机）

**用户原话**：「页面左上角的 `sr_agent_platform` 需要改为 UTF-8：『长光卫星-修图智能体平台』，
同时左侧预留出一个兼容 .png 的 logo 区域（可以随时在服务器替换，素材文件在我内网机上），
同时刚才提到的汉字大小适当放大，其他四个页面文字大小不变」。

**名字只写一处**：`src/lib/brand.ts` 的 `APP_NAME`，顶栏与路由 `document.title` 共用。
`frontend/index.html` 的 `<title>` 是 JS 起来之前的字面量、import 不进去，改名要一并改
（只影响首屏一瞬），三处都换了。仓库目录名、文档标题、`deploy/` 里的 `sr-agent-platform.conf`
沿用工程名不动 —— 那是工程标识，跟产品名不是一件事。

**logo 素材不进仓库，它是在服务器上换的文件** —— 正式素材在内网机上，所以**部署态就只有
「没有 logo」这一种**，这一条直接决定了改法：`<img>` 拿不到文件时由 `@error` 自摘，退回
`.brand-mark` 里那颗占位小方块（就是原来 `.brand::before` 那颗白点，搬进预留区居中），
不摘就是一张破图。摘掉后不重试以免刷请求 —— 服务器上补了 logo 要硬刷一次页面。src 用
`import.meta.env.BASE_URL + 'logo.png'` 拼（产物里编译成 `./logo.png`），与 `vite base:'./'`
同一条规则，解压到子目录也跟得上；写死 `/logo.png` 就只在根部署对。落点只有一处
`<APP>/dist/logo.png`，写进 `deploy/README.md` §二（§5.1 补了一句：升级是 `scp -r dist/*`
覆盖式拷贝，不会删掉它；但手工 `rm -rf dist` 会）。

**「预留」预留的是高度，不是宽度** —— 素材不在手上，宽高比预不成一个死宽，这是唯一诚实的
做法。`.brand-mark` 是 `min-width: 28px` + `height: 28px`：没有 logo 时占住 28×28；方形 mark
填进去时平台名**一个像素都不动**（实测两种状态名字 `left` 都是 62）；横长字标（公司全称那种）
才向右撑开（120×28 时名字移到 154）。加 `max-width: 160px` 挡异常素材把名字顶出屏。

**字号只动一个**：平台名 16 → 18px；`.nav-links a` 的 14px 一个字不动（前者的角色是
「这是哪个平台」，后者是「去哪儿」，两者不该同级），e2e 把四项逐个量了一遍钉住。

**顺带修掉一条一直在空跑的断言**：`.e2e/check-frontend-build.js` 是 Phase 2 的验收脚本，
不在 `.e2e/package.json` 的任何 `test*` 里，没人随改动重跑 —— 它第 2 步钉着按钮文案
「选择 TIF」，而按钮从 `ba8e61d` 起就叫「选择影像…」，于是从那天起脚本每次都死在第 2 步。
放松成认「选择」二字（测的是「有选择入口」这个意图，不是文案），全脚本这才第一次跑到尾。

**验证（开发机）**：`vue-tsc --noEmit` 零错误、`npm run build` 通过、产物里
`长光卫星-修图智能体平台` 与 `./logo.png` 都在；`node .e2e/check-frontend-build.js`
**15 项全绿**（其中 7 条本轮新加：页面标题、平台名、无破图、占位小方块在、预留区 28×28、
名字 18px、四个导航项仍 14px）。另用一份 **dist 副本**（不动正式产物，也不碰真素材）把三种
状态实拍了一遍：无 logo → 预留区 28×28 + 小方块 `display:block` + 名字 `left`=62；方形
28×28 → `has-logo` 生效、小方块 `display:none`、**名字 `left` 仍是 62**；横长 120×28 →
预留区跟着长到 120×28、名字移到 154、不溢出视口。

**真机待确认**：① 放进 `<APP>/dist/logo.png` 后硬刷一次，logo 是否如预期出现在预留区
（本次只用合成 PNG 在开发机的 dist 副本上验过，没碰过真素材）；② 素材若**带深色底或白底**
（不是透明的），孔雀石深带上的观感要用户看一眼 —— 预留区没有加白底/圆角托板，是按「素材
自带透明或白色元素」设计的，反过来再补。

### 2026-09-21 · 平台自烤的那份 preview 拖回来不认（开发机）

**用户原话**：「所以当前的 preview 关联原始盘阵逻辑还是有问题对吗？导致已经关联的 jpg 拖入后，
再拖入它的 preview.jpg 反而会出现：『这张 JPG 没有关联到盘阵目录』…」，并贴来了真机上那一屏
完整的 404 文案（含 `…_L1_PAN_preview/…_L1_PAN_preview: 目录不存在` 两条候选）。

**先复现，不猜**：拿用户贴的那个真机文件名（`JL1KF02B03_PMS01_20260919002142_200539015_101_0003_001_L1_PAN`）
跑 `pathguard.infer_scene_paths`，两条候选路径与真机屏上**一字不差**。看清三件事：

1. 生产树模板是 `<根>/{y}/{m}/{d}/{sat}/{mid}/{name}` **三层变量**，`{name}` = 拖入文件名去掉
   `.jpg`。显示件的 `{name}` 正好等于场景目录名 → 一击命中；`<目录名>_preview` 多粘七个字符 →
   **连 `{mid}` 一起歪**（`{mid}` 是「丢掉景号那一段」拼出来的，尾巴一脏就跟着脏），两条候选全假。
2. 第二阶段（去尾段再反推）本该兜住这种名字，但 `preview` 在 `scene_search._NON_STAGE_TAILS`
   里，`de_suffixed_stems` 撞上它**当场返回空** —— 平台连「把 `_preview` 去掉再试一次」都没做过。
3. 那份文件是**平台自己写下的**：`GET /preview-drop` 由 scene id 解出**栅格**、落
   `paths.drop_preview_path`（`<栅格 stem>_preview.jpg`），用户拖本体显示件那一次就烤出来了。

**改法（用户决策：认它）**：`preview` 成为 `_NON_STAGE_TAILS` 里**唯一可以剥**的尾段。判据不是
「名字好不好切」，而是「剥掉之后会不会落到一个真实存在的目录上」：`cloud`/`thumb`/`mask`/`ori`
剥掉正好落到真场景目录名上（云量图会被认成本体的显示件）—— 一个都不能剥；`preview` 剥掉落到那份
**栅格的 stem**（本体那份正好等于目录名），正是自己写了的那份。落地：新增
`scene_search.strip_preview_tail`；`de_suffixed_stems` 的循环放行 `preview` 尾段、剥完**接着往下切**
（`<目录名>_sr_preview` 要先剥 preview 才切得出 `<目录名>`）；`stage_of_jpg` 开头也剥一次（判环节
那一半喂的是**原始名**，不剥就仍被名单挡回）。两个常量分开写（`_PREVIEW_SEG` / `_PREVIEW_TAIL`）：
名单比的是**尾段**（不带前导下划线），`endswith` / 切片要带上它 —— 第一版把带下划线的形态直接去比
名单，等于没放行。

**结果**：`<目录名>_preview` → 本体（`kind='input'`、`sr_capable=true`、掩码写本体，与拖显示件同解）；
`<目录名>_sr_preview` → 那份产物（`kind='product'`、`suffix='sr'`、`lq_path` 仍为空）；
`<目录名>_cloud_preview` 剥一层仍在名单里 → 照旧 404；那景真不在盘阵上 → 照旧 404。前端只改一句
失败弹窗文案（把它也列进可关联的形态）。

**验证（开发机）**：后端 **647 passed / 4 skipped**（本轮 +5 例：本体的预览、产物的预览、
`_cloud_preview` 仍 404、那景不在仍 404、`_preview` 那条路的探测次数**实测 23 次**上限 30 且
50 个噪声文件一个没 stat）；前端 **325 passed** + `vue-tsc` 零错误（前端只动一句字符串）。
**没动的**：`_NON_STAGE_TAILS` 另四个成员、`jpg_stage_name` 的黑名单、`/preview-drop` 的落点、
前端除文案外任何一行、探测量上界（第一阶段命中时零新增 stat 那一条性质不变）。

**真机待确认**：① 拖回 `<目录名>_preview.jpg` 后卡片是否与拖显示件同形（同场景目录、同序号、
三标齐全、提交按钮可用）；② 产物那份预览拖回来是否显示 `[盘阵][n][SR]` 且两个按钮仍灰。

### 2026-09-21 · RC 场景的产物叫 `PAN_<suffix>.jpg`：名字没错，是它里面没有场景身份（开发机）

**用户原话**：「可是超分后的 jpg 名为 PAN_260318.jpg 啊，你这个格式也不对，而且我在超分后找不到
PAN_NOSR.jpg 的中间产物」。

**先查 SR 侧真源，不猜**：

1. **产物名是按输入影像名拼的**，不是按目录名：`code_0817_prod.py` 里
   `img_name = basename(输入影像)`、输出 `img_name[0:-4] + "_" + suffix`（`product_candidates`
   的 docstring 引的就是这一段）。SC 场景输入是 `<目录名>.tif` → 产物才正好叫 `<目录名>_<suffix>`；
   **RC 场景的输入就是 `PAN.tif`**（`util.check_sr_previous_step`：`<SolarAzimuth>` 为空即 RC）→
   产物叫 `PAN_<suffix>`。`docs/sr_code/sr-pipeline-overview.md` §4.6 记的 `PAN_<六位数>` 即此。
   用户那份 `260318` 正是六位 Suffix。**所以名字是对的** —— 错的是我上一轮写进弹窗与 400 文案的
   「中间产物叫 `<目录名>_<suffix>.jpg`」，那只对 SC 场景成立。
2. **找不到的 `_NOSR` 该叫什么**：`util.writeTiff` 的改名对象是**同一输出路径上已经存在的文件**
   （`os.rename(path + tiftype, path + "_NOSR" + tiftype)`）。同一 Suffix **第一次**跑时那个路径
   还不存在 → `FileNotFoundError` 被 `except` 吞掉，**什么都不留**；跑**第二遍**时才留下
   **`PAN_260318_NOSR.tif`** —— 是整个产物名（含 Suffix）加尾巴，不是 `PAN_NOSR.*`；而且是
   `.tif`（SR 管线不写 jpg）。只有产物名恰好是 `PAN`（空 Suffix、输出名 == 输入名，
   `basename(path)` 既 `startswith("PAN")` 又 `endswith("PAN")`）时才走 `_ori` 分支 →
   `PAN_ori.tif`。**「超分后找不到」符合规则，不是丢了文件。**

**实测（临时探针，跑完删）**：造一个干净 RC 场景（目录里只有 `PAN.tif`、`PAN_260318.tif`、
`PAN_260318.jpg`）——

- 拖 `PAN_260318.jpg` → **400**：`这张 jpg 的名字里没有生产全名（缺 14 位成像时刻）…
  平台不猜目录…`。这个名字里既没有卫星段也没有成像时刻，反推不出是哪一天哪一景的目录，
  所以走不到「判环节」那一步（与 `PAN.jpg` 同一类，不是 `_preview` 那种「尾巴没剥」）。
- `GET /siblings?suffix=260318` → `input=PAN.tif`（在）、`product=PAN_260318.tif`（**在**）、
  `nosr=PAN_260318_NOSR.tif`（不在）。这条链是拿**输入影像名**拼的，RC 场景正好对。
- 但若同目录里还躺着上游 SC 遗留的 `<目录名>.tif`，`/siblings` 的「输入影像」会挑中那一份，
  产物名跟着拼成 `<目录名>_260318.tif`（不存在）—— 属于哪种要看真机目录，见下「待确认 ①」。

**改法（只改文案与注释，判据一行未动）**：三个出口（`app.py` 的 jpg-无时间戳 400、候选被拒的
`reasons`、前端那张失败弹窗）把「能关联的产物名」写成两种形态 —— SC `<目录名>_<suffix>.jpg`、
RC `PAN_<suffix>.jpg`（并说明后者名字里同样没有成像时刻，认不出是哪一景）。`scene_search.py`
模块头新增一段「产物名的规则是按输入影像名拼的」，`stage_of_jpg` 的判据 1 那句补上「这一条只管
SC 场景的产物」（`jpg_stage_name` 要求目录名前缀，RC 产物对不上；何况它连反推都过不了）。
新增用例 `test_rc_product_jpg_400_and_names_the_reason`。

**验证（开发机）**：后端 **648 passed / 4 skipped**（+1 例）。前端只动一句字符串。

**待确认**：① 那一景 `ls -l`（`PAN.tif` 在不在？上游 `<目录名>.tif` 在不在？`PAN_260318.tif`
与同名 `.jpg` 各是什么时候写的？有没有 `_NOSR`）—— 决定同场景芯片这条路今天能不能走通；
② ~~要不要做「锚定当前已打开场景目录」的拖入认产物~~ → **用户当轮拍板「让拖 `PAN_<suffix>.jpg`
也能关联吧」，已实现，见下条**。

### 2026-09-21 · 拖 `PAN_<suffix>.jpg` 能关联了：锚点是用户给的目录，不是猜的（开发机）

**用户原话**：「让拖 `PAN_<suffix>.jpg` 也能关联吧」。

**口径（上一轮已在回复里说清、用户认可）**：`PAN_<suffix>.jpg` 里没有卫星段也没有成像时刻，
**按名字反推哪一景是做不到的**（那一步只能 400，这一条不改）。要关联就换一条来路：把**用户当前
打开着的场景目录**交给后端，后端**在他指的那个目录里**认这份 jpg。这不是「平台猜目录」—— 猜是
从名字编一个路径再 stat 上去，这里目录来自用户的打开状态；平台**不搜盘、不列举、不反推**。

**请求契约**：`POST /api/scenes/resolve` 的 `{name}` 分支新增可选 `anchor` —— 一个或几个盘阵
目录（前端 `lib/scene.ts::sceneAnchors([最近显示过的 sceneDir, A 格, B 格])`，`ANCHOR_MAX = 3`，
顺序即优先级）。全部细节见 [api-contract §3.5](../planning/api-contract.md)。

**后端**（`backend/api/app.py` + `backend/services/scene_search.py`）：

- `_parse_anchors`：归一化成盘阵 POSIX 目录并逐个过白名单。坏值一律**跳过并记原因**，不 400
  也不 403 —— 它是提示不是断言，不该因为前端塞了个陈旧目录就把整次拖拽打回。接受字符串或数组，
  数组取前 4 条。
- `_anchor_stage_hit`：挨个锚定目录跑「`input_scene_path` 命中 → `stage_of_jpg`」，**不过
  `_fingerprint_mismatch`**（那条比的是「jpg 名 == 场景目录名」，`PAN_260318.jpg` 恰恰不是目录名，
  复用就把这条路重新堵死）。真门仍是 `stage_of_jpg` 的第 2 条：**这一环节自己的栅格躺在同级**。
- **只在名字自己反推不出来时才用**（`parse_scene_date` 取不到时间戳且后缀是 `.jpg/.jpeg`）：
  有成像时刻的名字照旧走候选，`anchor` 连一次 stat 都不花 —— 老路径的探测量上限纹丝未动。
- `resolve_scene` 的 200 收尾提成嵌套 `finish(hit)`，锚定命中与反推命中共用（`kind`/`suffix`/
  `row` 描述该环节自己那份栅格、产物上 `lq_path=null` 等一律照旧）。
- **`scene_search.stage_bases`（新）**：产物名的基准是**那份被超分的影像的 stem**，而一个目录里
  可能有多份输入候选（RC 场景常残留上游的 `<目录名>.tif`）。`stage_of_jpg` 改成**把
  `input_candidates` 的各个 stem 都试一遍**，不再钉死目录名 —— 这是上一轮那个错误的根：
  `<目录名>` 只在 SC 场景上成立。
- 400 的 `detail` 把每个锚定目录**各自为什么不行**追加进去（「目录不存在」/「不是可提交的场景
  目录」/「这一景里没有 `PAN_260318.tif/.tiff`」）。

**前端**：`apiResolveScene` 收 `anchor?: string[]`；`viewer.ts` 记一个**非响应式**的
`lastSceneDir`（`placeRec` 与 `applySceneJpgToRec` 两处都记 —— 主流程是拖显示件时升级已有 rec，
`lqPath` 出现在 `placeRec` 之后），拖 jpg 时按 `[lastSceneDir, A 格, B 格]` 发出去。失败弹窗
改写：原文那句「能关联的 jpg 只有名字与场景目录名一致的那份」对 RC 产物是假话，现在说明
「先打开那一景再拖进来才认得出」。

**验证（开发机）**：后端 **655 passed / 4 skipped**（+7 例，`TestResolveAnchored`）；
前端 **vitest 329 passed**（+4 例 `sceneAnchors`）+ `vue-tsc --noEmit` 退出码 0。
`TestNeverListsDirectories` 的 ≤30 / jpg ≤35 stat 钉子全过（`stage_bases` 不花 stat、
`anchor` 缺席时零 stat、命中时只有固定名字的 `is_file()`）。

**待确认（真机）**：③ 拖 `PAN_260318.jpg` 前**先把那一景打开**（左栏芯片或「盘阵场景」栏粘目录），
然后拖 —— 预期 200 且卡片上出「盘阵 + 序号 + SR」三标；不先打开就拖，预期仍是 400，且 detail 末尾
多一句「另外，当前打开的场景：…」。④ 完全没打开过任何场景时拖它 → 400 文案与上一轮一字不差。

### 2026-09-21 · 超分跑完顺带烤未超分那一份 → `PAN_NOSR_preview.jpg`（开发机）

**用户原话**：「我希望增加一条烘焙链路，就是在超分后，顺带会产生 `NOSR_preview.jpg`，你实现的越简单
越好，可以向我提问」。问过两件事，用户口径：

1. 烤哪一份 —— 「将 **`PAN_NOSR.tif`** 按照全局采样率进行下采样，**不考虑其他分支**，一般后端走超分，
   tif 产物名称也是 `PAN_NOSR.tif`」；
2. 落哪个名 —— **`PAN_NOSR_preview.jpg`**。

**实现（只动急烤那一条链）**：`app.py::_bake_nosr_preview(task, div)`，在 `_eager_bake_tick` 里紧跟
产物那一份调用。源 = `<lq_path>/PAN_NOSR.tif`（`.tiff` 也认，最多两次 `is_file()`）；落点直接用
`paths.drop_preview_path(source)`，于是文件名天然是 `PAN_NOSR_preview.jpg`（与拖入链同一条
`<源 stem>_preview.jpg` 规则，不是另立名字）；档位 = 同一个全局 `div`；命中判定 = 同一个 `cache_hit`
（盘上已是当前档位就不重读）。三条有意为之的边界：

- **不判沙箱** —— 那份栅格是盘阵上的既有文件，与这次跑在盘阵还是私有副本上无关（产物那份必须判，
  因为它刚被写出来）；
- **不动 `preview_state`/`preview_note`** —— 那一列描述的是产物预览，一个字段说不出两份文件的结局；
  这一份只写盘 + 打一行 stdout `[nosr-preview] task=<id> <状态>`（`baked:`/`cached:`/`skipped:`/`failed:`）；
- **名字钉死**，不把仓库规则推出来的 `<产物 stem>_NOSR.tif` 也顺手试一遍（见下「待确认 ⑤」）。

**没改的东西**：不新增端点、不动任何响应字段、不动产物那一份的结论与广播、`/siblings` 的「上一次
产物」那一项找的仍是点号落点 `…_NOSR.preview.jpg`（所以**这份新文件今天没有任何服务端消费方**，
它的用途是人看 / 拖回查看器 —— 拖回时 `stage_of_jpg` 剥掉 `preview` 尾段后按 `PAN_NOSR.tif` 判环节）。

**验证（开发机）**：后端 **659 passed / 4 skipped**（+4 例，`TestProductPreviewBake` 里新增：同一次
tick 烤出来且 ÷4 尺寸对、没有那份栅格时什么都不写但状态报得出「没这份」、**产物烤跳过时它也照烤**、
同档位命中 `cached` 而换档位重烤）。前端一行未动，故未重跑。

**待确认（真机）**：

- ⑤ **`PAN_NOSR.tif` 这个名字与仓库规则对不上**。`SR_code/util.py::writeTiff` 的改名对象是**输出路径**
  （`os.rename(path + tiftype, path + "_NOSR" + tiftype)`），推出来的是 `<产物 stem>_NOSR.tif`，即
  **`PAN_260318_NOSR.tif`**；而用户说真机上那份未超分的 tif 就叫 `PAN_NOSR.tif`。请 `ls -l` 一次：
  这两个名字哪个在、各自多大、什么时候写的。若在的是后者，那 **`/siblings` 的「上一次产物」那一项
  在真机上恒为 `exists: false`**（它按 `nosr_path_for` 拼名），对比视图里也就永远少一张 —— 这是同一个
  名字问题的第二个出口，届时一起订正。
- ⑥ **`PAN_NOSR.tif` 是不是 SR 真正吃进去的那份低质输入？** 用户说「一般后端走超分」，而
  `util.get_l1_pan_tif_rcsc_nosr` 的 **SC 分支**恰好把 `<lq_path>/<目录名>_NOSR.tif` 当低质输入读
  （读不到真包会 `exit()`）。若真机 RC 场景的输入其实是 `PAN_NOSR.tif`，那平台今天认的输入影像
  （`input_scene_path`：先 `<目录名>.tif`、再 `PAN.tif`）可能**不是 SR 真正吃的那一份** ——
  `/siblings` 的「输入影像」与对比视图的「本体」都会指错。这一条要靠真机的 `ls` + 一次实际提交才能定，
  先记着，**不要据此改 `input_scene_path`**。

## 5. 交接（给新窗口）

> 开新窗口时按用途挑一份整篇粘过去：[handoff-prompt.md](handoff-prompt.md)（梳理框架与当前思路）、
> [bugfix-prompt.md](bugfix-prompt.md)（修一个 bug，末尾留了 bug 描述粘贴位）。
> 两份都含协作方式、硬约束、仓库地图与验证命令。

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
- 阶段5 契约/实现：`docs/planning/api-contract.md`（**评审**——2026-09-16 挂起 §3.3 的 `suffix` 默认值条款，见 §4 该日条目；评审通过后改回「已定」）、`backend/api/platform.py`（chat/queue/tools/masks + SSE 广播）+ `backend/api/app.py`（lifespan 轮询）、`frontend/src/{lib/api.ts, stores/{chat,queue}.ts, pages/{ChatPage,QueuePage}.vue}`、查看器「提交 SR」（`stores/viewer.ts::submitSr` + Toolbar 按钮）、`.e2e/test-platform.js`（12 断言）
- 阶段5 部署增补：nginx `/api/` 反代 `proxy_buffering off`+`proxy_read_timeout 3600s`（SSE）；`sr-api.service` env（`SR_AGENT_DB`/`SR_LLM_MOCK=0`/`SR_SLURM_FAKE=0` + Slurm 六项 `SR_PYTHON`/`SR_BUNDLE_DIR`/`SR_SLURM_WORK_DIR`/`SR_SLURM_PARTITION`/`SR_SLURM_TIME`/`SR_SLURM_CPUS`）；`requirements-api.txt` 补 `openai>=1.40,<2`
- Slurm 接入件：`SR_code/variants/{code_0817_prod_slurm.py,verify_sr_run.py,.diff,.provenance.json}` + 生成器 `SR_code/tools/gen_slurm_variant.py`、只读探针 `deploy/slurm/probe_slurm.sh`；运行期 `backend/services/{run_sr,slurm}.py`；验收清单 `docs/status/slurm-acceptance.md`、差异表 `docs/sr_code/sr-slurm-deploy-variant.md`、契约 `docs/sr_code/sr-pipeline-interface.md` v1.5
- 阶段6 上下文侧舱：`frontend/src/components/{ContextPanel,RoiToolsTab,AgentChatTab}.vue` + `lib/{roiStats,agentContext}.ts`（buildStats / tasksForScene / CTX_DIVIDER）+ viewer store 选中/统计钩子 + `/api/scenes` `lq_path`（阶段6 增补见 api-contract.md）
- 生产命名/反推规则：`docs/sr_code/production-scene-naming.md`（9 段名字 + 六层目录 + 由文件名反推场景目录）；实现唯一真源 `backend/pathguard.py::scene_name_layers` / `infer_scene_paths`
- 经验文档：`docs/experience/gui-experience.md`
- 真机预演（无内网机时可跑）：`backend/tests/test_local_chain.py`（4 例，除 SR 算法外全真：真 config/批脚本/bash/校验器/退出码文件；`code_0817_prod.py` 换 stub）
- E2E 测试：`.e2e/`（**2026-09-15 起入库**，只忽略 `node_modules/`+`fixtures/`+大图）——`test-vue-viewer.js` **58 断言**本地文件回归（含 G 段待修复清单）· `test-scenes.js` **65 断言**场景 http 打开 · `test-platform.js` **22 断言** REST/SSE 全链路 + 布局（1600 视口实测页宽同宽 + 耗时列 nowrap）· `test-manual-scene.js` **194 断言**盘阵任意场景目录（粘 `W:\…` 打开 → 画掩码 → 写盘阵 → 提交 SR，含 PAN 掩码命名与反推失败两条路；**G 段**粘单个 `.tif` 文件路径 → 预览按各边 1/2 烤进源图目录、且不能提交 SR；**H 段**《待修复清单》写回：导入 GBK 清单 → 粘路径 → 同步 → **从磁盘按字节读回**比对；**J/K 段**对比模式的窗口拖放与三类图芯片、后台预取边界；**L 段**左栏卡片带票拖进画布、拖中间产物 jpg 关联盘阵并挡住三处修复入口 —— 见 §4 该日条目）；跑法 `cd .e2e && node test-<name>.js`（前置 `cd frontend && npm run build`；puppeteer-core + 无界面 Chrome + 本地静态服务顶替 nginx + uvicorn 起真后端）。四个数字 **2026-09-18 实测复核**过（此前该行停在 35/65/22/48，其中两个已过期：09-18 加 G 段后 `test-vue-viewer.js` 38 → **58**；`test-manual-scene.js` 记的 48 是更早的值，当天加 H 段后实测已到 **98**）
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
| /api/scenes 真实数据 | **完成（09-16）** | `SR_SCENES_ROOT` drop-in（`scenes.conf`）与 nginx `alias`（`conf.d/sr-agent-platform.conf`）同指 `/DiskArray/tmp/wangrz/datahub` → `{"ok":true,"source":"disk"}`、`/api/scenes` 出 disk 行、`/disk-array/<rel>` 200 `image/tiff`。收件规则同日改白名单（18 行 → 真实场景行） |
| **SR 最小原型（本机直跑，不走 Slurm）** | **A 段验收通过（09-15，job 3）** | `SR_EXECUTOR=local` / `SR_LOCAL_GPU=0`。**job 1/2 = FAILED（判据正确，非平台缺陷）**：job 1 死在 bundle `util.py` 的 `already SRed before` 提前退出（退出码 0、无产物 → 校验器判 SRLOG 为残留，契约判据避免了记成假成功）；job 2 死在 `NotImplementedError: Generator model [Espan2] not recognized` —— 根因是 `SR_BUNDLE_DIR` 指了不认 Espan2 的 ProductionSchedule 那套。**换到 wangrz/mmsr_bundle_240617 后 job 3 真跑完**：`<目录名>_sr.tif` 1.9G 与输入同目录落盘、SRLOG 末行 `Run finished.`、退出码文件 `verdict=0`、平台 `/api/queue` 状态 `COMPLETED`。**遗留小缺陷（外观）**：`POST /api/queue` 的 `notice` 与作业 WARNING 段是无条件写的，后缀非空时「输入改名 `*_NOSR.tif`」这句不成立（`writeTiff` 只在输出名 == 输入名时才改名）。工作单 `docs/planning/sr-minimal-prototype-plan.md`；bundle 结论见 memory `sr-real-bundle-is-wangrz-240617` |
| ~~提交 SR 端到端（Slurm 路线）~~ | **路线已中止（09-14）** | 代码/脚本/文档仍在（`SR_EXECUTOR=slurm` 分支、变体、校验器、`slurm-acceptance.md` 的 A–D 清单），但**不再按此推进**。**§0 前置四项曾全绿**（上车 / 变体就位 / 13 项 env / 目录与可写性，见 §4 时间线 2026-09-11），A/B/C/D 一步未跑。判据（终态读退出码文件）仍适用于本地执行器 |
| 平台侧沙箱（`SR_SANDBOX_ROOT`） | 完成（09-10 代码侧 · 09-11 真机侧） | 每个作业自建 `lq_path` 副本，SR 只写副本 → 前端填**生产路径**也不会动生产数据。见 deploy/README §7.5。真机目录已建 + `nginx` 可写（`WRITE OK`） |
| ⚠️ `$BUNDLE/code_0817_prod.py` 被覆盖 | **事故待善后** | 09-11 09:55 由 33477B/`cfbf0bda` 覆盖为 28703B/`03c9b4fc`，**旧版无备份、不可恢复**。待答：谁读这份文件？（详见 §4 时间线 2026-09-11） |
| 本会话代码/文档变更归档 | **未 commit** | 清单见下方 |

**真机配置与仓库默认值的偏差（2026-09-16 实测，全部经 `systemctl show` / `nginx -T` 核对）**

代码侧三项都可经环境变量切换，不必改仓库——但**迁移代码后要照着这张表在真机重建 drop-in**，
否则会退回仓库默认值（尤其 `SR_BUNDLE_DIR`，指回不认 Espan2 的那套）。

| 项 | 仓库默认 | 真机值 | 载体 |
|---|---|---|---|
| `SR_BUNDLE_DIR` | `/DiskArray/ProductionSchedule/exe_CentOS7/SR_bundle/mmsr_bundle/codes` | `/DiskArray/tmp/wangrz/mmsr_bundle_240617/codes` | `/etc/systemd/system/sr-api.service.d/srscript.conf` |
| `SR_SR_SCRIPT` | `code_0817_prod.py` | `code_0817_prod_slurm.py` | 同上 |
| `SR_SCENES_ROOT` | 无（不设 → `source:fake`） | `/DiskArray/tmp/wangrz/datahub` | `/etc/systemd/system/sr-api.service.d/scenes.conf` |
| nginx `alias` | `deploy/nginx.conf` 里是 `/data/scenes/` | `/DiskArray/tmp/wangrz/datahub`（同值） | `/etc/nginx/conf.d/sr-agent-platform.conf` |

两条踩过的坑：

- **drop-in 必须有 `[Service]` 段头**。`scenes.conf` 第一版只写了 `Environment=…` 一行，systemd
  静默忽略整个文件（`systemctl show` 里看不到该变量、接口照旧 `source:fake`）。正确写法：
  `printf '[Service]\nEnvironment=SR_SCENES_ROOT=/DiskArray/tmp/wangrz/datahub\n' > $D/scenes.conf`，
  再 `systemctl daemon-reload && systemctl restart sr-api`。
- **`/etc/nginx/nginx.conf` 里没有服务配置**。该文件只有 1–34 行（全局段 + `include conf.d/*.conf`），
  `location` / `alias` / `proxy_pass` 全在 `/etc/nginx/conf.d/sr-agent-platform.conf`。改动前先用
  `nginx -T | grep '^# configuration file'` 找到真身；对着 `nginx.conf` 做 `sed` 是空操作。
  URL 前缀保持仓库默认 `/disk-array/`，不要改成 `/DiskArray/`（后者会被 `location /` 兜底成
  index.html，返回 200 `text/html` 459 字节，看着像成功）。

**下一步行动**
1. **`SR_SCENES_ROOT` 的定位（09-15 定）**：本阶段**不维护场景检索**，优先保证「提交 SR」链路跑通。据此：

   - 这个 env **不能不配**：`/scenes` 盘阵场景页的行来自 `/api/scenes`，而查看器的「提交 SR」按钮只对 `route='jpg' && lqPath` 的记录可用，`lqPath` 又只由 disk 场景行提供——**场景列表是那个按钮的唯一入口**。
   - 但也不必现在就挑生产根。暂定指向 `/DiskArray/tmp/wangrz/datahub/`，nginx `alias` 取同值。
   - `SR_PREVIEWS_ROOT` 不设，预览 JPG 写在源文件旁边，正好落在 alias 根内。
   - **与既有记录的关系**：生产盘阵根此前确认为 `/DiskArray/GSHC2IMPS/`（层级 年/月/日/生产编号），`datahub` 更像临时工作区；两者关系（拷贝？软链？）按本阶段目标不必先弄清。此前在 viewer 开过的那张 590MB `JL1KF02B01_...` 来源仍未确认。

   **收件规则（2026-09-16 已修：黑名单 → 白名单，原「已知缺口」消项）**：`scene_search.is_scene_file()`
   （[scene_search.py:57-76](../../backend/services/scene_search.py#L57-L76)）改为两条同时成立：
   ① 所在目录是**场景目录**（内有 `<目录名>_meta.xml`，新增 `is_scene_dir()`）；
   ② 文件名叫 `<目录名>.<ext>`（SC 步骤的输入）或 `PAN.<ext>`（RC 步骤的输入）。
   旧规则只有「后缀白名单 + 排 `.preview.jpg` + 排结尾 `_mask`」，每冒出一类派生件就得补一条：
   2026-09-15 真机接上 datahub 后 18 行里 16 行是脏数据（`_thumb` / `_cloud` / `_sr` / `_NOSR` /
   `Debug/` 下十几张调试图）。白名单一次全挡住。附带把 `_scene_row` 的 `lq_path` 从
   「场景文件父目录」（在扁平根里等于盘阵根）改成真正的场景目录，与
   `/DiskArray/GSHC2IMPS/年/月/日/生产编号` 的目录语义一致。代价（已接受）：没有
   `<目录名>_meta.xml` 的目录整个不显示。另：`scan_root()` 仍是每次请求全树 `rglob` + 逐文件
   `stat()`，无缓存。
2. ~~改两处并**保持同值**~~ **已完成（09-16）**：`SR_SCENES_ROOT`（`sr-api.service.d/scenes.conf`）
   与 nginx `alias`（`conf.d/sr-agent-platform.conf`）同指 `/DiskArray/tmp/wangrz/datahub`，
   `curl /api/scenes` 出 disk 行、`/disk-array/<rel>` 返 `image/tiff`。
3. **`frontend/dist` 已重打进开发机，待传 node81-135**（粘贴板传不了目录树；真机 dist 位
   `/run/media/root/SSD/workspace/wangrz/sr-agent-platform/dist`）。本次 dist 含场景库改名 +
   场景名完整显示 + 后缀留空三项前端改动。
4. 数据就位后按 §6.3–6.5 逐项真机验收（§6.3 场景项现在可跑：收件规则已改、根已接）。
5. commit 下列待归档变更：`deploy/sr-api.service`（User= 行尾注释修复）、`deploy/requirements-api.txt`（+`eval-type-backport; python_version<"3.10"`）、`docs/status/real-machine-bringup.md` 与 `-adhd.md`（新增症状行）、`deploy/README.md`（真机落地、无 yum 源 ⚠️）、本会话 memory 更新，以及本轮的场景收件规则 / 后缀默认值 / 页面改名三批改动。

> 启动两个坑的完整排障（217/USER、py3.9 eval-type-backport）见 `real-machine-bringup.md` §5 症状表第 2/3 行。

### 6.1 出发前（开发机）
- [ ] **打包拷包**（⏱30′）· 开发机 `npm run build && npm run package:offline` → 仓库根 `release/` 出**两个**包（`dist-*.tar.gz` 顶层 `dist/`、`backend-*.tar.gz` 顶层 `backend/`）+ 后端依赖 wheel 拷 U 盘 · ✓= 两包解到 `$APP` 后 `$APP/dist` 与 `$APP/backend` 就位；`nginx.conf`/`sr-api.service`/`requirements-api.txt` **在仓库 `deploy/`**（不进包，见 §4 2026-09-18 打包条）· 记录:
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
- [ ] **《待修复清单》写回**（⏱20′）· 盘阵上放一份**真实清单**（GBK，先备份）→ `/viewer` 导入 → 页脚粘完整路径（`W:\…\待修复清单.txt`）→「同步」 · ✓= 文件真被改写：上半部分逐字没动、下半部分只剩终态行、编码仍是 GBK（记事本「另存为」看编码或 `file -i`）；顺带 `ls -l` 确认替换后**属主变成 nginx**、权限位没变，以及该目录对 nginx 可写（见 deploy/README 那两条注意事项） · 记录:
- [ ] **SSE 长连**（⏱30′，可挂后台）· 临时开 `SR_LLM_MOCK=1` 或挂 `/api/queue/events`，经 nginx 连 10min+ · ✓= 事件逐帧到达不攒批、断链后前端重连正常、无 502 · 记录:

### 6.6 决策点（标灰，不阻塞；当天记结论）
- [ ] **LLM 底座拍板** · 内网有无第二台 GPU → vLLM+Qwen / Ollama+Qwen / 第三方端点（§2.6⑤）· ✓= 出结论即可 · 结论:
- [ ] **上线还差的两件事记账** · 真 LLM 复测一轮 /chat（配好端点后）；产物回到用户（队列「完成」之后的目录/对比/失败诊断）+ 多人权限要不要（M2）· ✓= 列进 §5.2 · 结论:
