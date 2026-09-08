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
- **真机部署完成、Windows 可访问（09-07/08，node81-135）**：后端 `sr-api`（systemd，开机自启）+ 前端 `nginx`（:80，开机自启，已删出厂 default.conf 由本站点接管）均 running；Windows 浏览器直接开 `http://10.10.81.135` 可访问各页面（查看器 /scenes /chat /queue），查看器能稀疏读真实大 TIF。部署操作手册：`docs/status/real-machine-bringup.md`（ADHD 动作版：`real-machine-bringup-adhd.md`）。期间排掉两个启动坑（`User=` 行尾注释致 217/USER、py3.9 缺 eval-type-backport），均已入症状表归档。
- **遗留一：真实盘阵根未定位**：默认 `SR_SCENES_ROOT=/data/scenes` 在 node81-135 **不存在**（`ls` 报无此目录）→ `/api/scenes` 返回 `source:fake` 的 12 条占位（`fake:true` / 0B）→ `/scenes` 页面全是假数据、场景打不开、查看器「提交 SR」灰掉（route=sparse 无 sceneId，设计如此）。真实 590MB `JL1KF02B01_PMS03_...` 那类数据在别处（曾从某入口在 viewer 打开过一张真图，来源未确认）。**待办**：确认真实场景根路径（在本机哪个挂载点，还是数据在别的机器）→ 把后端 `SR_SCENES_ROOT` 与 nginx `alias` 改成同值 → restart 后复核 `/api/scenes` 出真实行。
- **遗留二：真机阶段4/5 验收清单（§6.3–6.5）未跑**：需先定位真实盘阵根再逐项执行（首次 JPG 生成耗时/内存、真实掩码烘焙落盘 ENVI 核对、真 Slurm 提交/取消、SSE 长连）。进度快照见 §6.0。
- **下一步**：① 定位真实场景根 → 改 `SR_SCENES_ROOT` + nginx `alias`（同值）→ 复核 /api/scenes（进度 §6.0）；② 数据就位后按 §6.3–6.5 真机验收；③ commit 待归档变更（清单 §6.0）；④ 配 LLM key 跑通真实 /chat 闭环（§2.6）；⑤ 开发机推进 P1 ④⑤⑥⑦（§2.5）。
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

## 5. 交接（给新窗口）

### 5.1 环境约束

- 开发机是**外网机**（能上网），真实遥感图全部在**内网机的盘阵**上，无法导出/复制/读取文件头来探测结构。文件结构只能靠**用户回传 ENVI 头信息**（Edit Headers 里的 Compression/Interleave 字段）或**根据已知信息推断尺寸**。不能要求用户提供文件路径。
- 开发机浏览器 e2e **可用**（`.e2e/launchBrowser.js`：puppeteer-core 驱动无界面 Edge/Chrome + 每次用独立临时配置目录，彻底解决退出码 0 闪退）；浏览器单次内存分配约 2GB、Canvas 画布面积上限 16384²、CDN 无法访问（第三方库必须本地内置）。

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
2. **阶段5 真机验收**（开发机已离机全绿：后端 190 + Vitest 114 + `.e2e/test-platform.js` 11 断言；契约 = `docs/planning/api-contract.md`，提示词见 `frontend-phase4-phase5-prompts.md`【阶段5】）：
   - **真 LLM**：`SR_LLM_BASE_URL/API_KEY/MODEL` 指内网端点 + `SR_LLM_MOCK=0`，/chat 发一条 → 真实工具调用 + 最终回复；刷新恢复历史；
   - **真 Slurm**：`SR_SLURM_FAKE=0`，/queue 提交 → sbatch 真实作业 → SSE `job_update` 随 squeue/sacct 推进到「完成」；取消按钮 scancel 生效；
   - **盘阵掩码落点**：/viewer 打开真实场景画掩码 →「提交 SR」→ ENVI 打开 `<原图目录>/<stem>_mask.tif` 核对区域与 0817 消费路径一致（`_mask.txt` 质心坐标）；
   - **nginx SSE 长连**：/chat 与 /queue 经反代挂 10min+ 无断流/攒批（心跳/断链重连正常）；
   - **systemd 权限**：`nginx` 用户能读盘阵、写预览 JPG 缓存与 `SR_AGENT_DB`（见 deploy/README §三）。
3. **开发机可推进**：P1 ④⑤⑥⑦（见 §2.5，.env / 瞬时错误重试 / 测试缺口 / CLI --resume）；配 LLM key 跑通真实闭环、真机验证 Slurm/run_sr（§2.6）。

### 5.3 关键文件

- 查看器交付物：`tif_viewer/tif-viewer.html`（冻结）→ Vue3 版 `frontend/src/`（pages/ScenesPage.vue + components/ + stores/{viewer,scenes}.ts + lib/{tifDecode,maskgen,scene,source,viewMath}.ts）
- 阶段4 后端：`backend/api/app.py`（FastAPI：/api/scenes + /preview）、`backend/api/paths.py`（白名单 + scene id codec）、`backend/services/preview_jpg.py`（稀疏采样 + 2% 拉伸 + Pillow 缓存）
- 阶段4 部署：`deploy/nginx.conf`、`deploy/sr-api.service`、`deploy/requirements-api.txt`、`deploy/README.md`；离线包 `frontend/scripts/package-offline.sh`
- 阶段5 契约/实现：`docs/planning/api-contract.md`（**已定**）、`backend/api/platform.py`（chat/queue/tools/masks + SSE 广播）+ `backend/api/app.py`（lifespan 轮询）、`frontend/src/{lib/api.ts, stores/{chat,queue}.ts, pages/{ChatPage,QueuePage}.vue}`、查看器「提交 SR」（`stores/viewer.ts::submitSr` + Toolbar 按钮）、`.e2e/test-platform.js`（11 断言）
- 阶段5 部署增补：nginx `/api/` 反代 `proxy_buffering off`+`proxy_read_timeout 3600s`（SSE）；`sr-api.service` env（`SR_AGENT_DB`/`SR_LLM_MOCK=0`/`SR_SLURM_FAKE=0`）；`requirements-api.txt` 补 `openai>=1.40,<2`
- 经验文档：`docs/experience/gui-experience.md`
- E2E 测试：`.e2e/`（gitignore 本机资产：`test-vue-viewer.js` **42 断言**本地文件回归 + `test-scenes.js` **45 断言**场景 http 打开；puppeteer-core + 无界面 Edge + vue-lib.js 本地静态服务顶替 nginx + uvicorn 起真后端）
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
| 提交 SR 端到端（阶段4/5 真链） | **未完成** | 依赖真实根 + Slurm 可达；清单见 §6.3–6.5 |
| 本会话代码/文档变更归档 | **未 commit** | 清单见下方 |

**下一步行动**
1. 确认真实场景根在 node81-135 的挂载点（或数据在别的机器）。此前在 viewer 开过一张真实 590MB `JL1KF02B01_...`，来源未确认——找它即可定位真实根。
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
- [ ] **真实提交 + 推进**（⏱20′）· `/queue` 手填**测试目录**的 lq_path 提交（勿碰生产数据）· ✓= sbatch 起真实作业，徽标随 squeue/sacct 从 排队→运行→完成，记录真实推进节奏 · 记录 job_id / 耗时:
- [ ] **取消**（⏱10′）· 提交一个会排队的作业 → 点取消 · ✓= scancel 生效、状态变失败/取消 · 记录:
- [ ] **重启校准**（⏱10′）· `systemctl restart sr-api` → 刷新 /queue · ✓= 内存缓存丢后 GET /api/queue 当场校准，已完成作业仍显示 完成 · 记录:

### 6.5 阶段5 掩码→SR 真链 + SSE 长连
- [ ] **掩码落盘 + ENVI 核对**（⏱30′）· 真实场景画矩形掩码 →「提交 SR」→ 落盘原图目录 · ✓= ENVI 打开 `<stem>_mask.tif` 区域位置正确（JPG 上画→全分辨率落点）+ `_mask.txt` 质心可读；顺带用带 MaskPath 的真作业确认 0817 消费链一致 · 记录:
- [ ] **SSE 长连**（⏱30′，可挂后台）· 临时开 `SR_LLM_MOCK=1` 或挂 `/api/queue/events`，经 nginx 连 10min+ · ✓= 事件逐帧到达不攒批、断链后前端重连正常、无 502 · 记录:

### 6.6 决策点（标灰，不阻塞；当天记结论）
- [ ] **LLM 底座拍板** · 内网有无第二台 GPU → vLLM+Qwen / Ollama+Qwen / 第三方端点（§2.6⑤）· ✓= 出结论即可 · 结论:
- [ ] **上线还差的两件事记账** · 真 LLM 复测一轮 /chat（配好端点后）；产物回到用户（队列「完成」之后的目录/对比/失败诊断）+ 多人权限要不要（M2）· ✓= 列进 §5.2 · 结论:
