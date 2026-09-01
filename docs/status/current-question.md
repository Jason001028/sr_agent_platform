# current_question — 当前问题解决时间线（显示遥感图像）

> 用途：窗口交接文档。下一个 Claude 窗口先读本文件，再读 `docs/experience/gui-experience.md`（经验），即可无断点继续。

---

## 1. 当前状态（摘要）

- **查看器已可用（08-29）**：`tif_viewer/tif-viewer.html` 能正常打开、预览本地遥感图像，用户实测时间成本可接受。
- **真实文件布局已确认**：GF07A03（1.11GB）与 KF02B04（1.78GB）均为**无压缩 · 条带1行**的单波段 16bit 影像 → 无需金字塔，走稀疏条带预览（秒级；134MB 测试图从全量 4.5s → 1.3s，预计真实 1.1GB 文件从 ~2 分钟降到秒级）。
- **预览路径定案（09-01）= JPG 中间产物**：网页/预览显示导出的 JPG（长边 8192 ≈ 原始 1/3），浏览器内对原始 TIF 的全分辨率切片读取已砍（决策记录见 §3.4）。
- **掩码绘制已落地（08-31）**：tif_viewer「绘制掩码」模式（矩形/多边形/魔棒）；浏览器**直出** `掩码.tif`（0/255）+ `掩膜中心点坐标.txt`，或导出矢量 JSON 走 `python -m backend.services.mask`（详见 §3.3）。
- **魔棒/合并/删除打磨（09-01）**：魔棒改**自适应区域生长**（修复"总出默认大小椭圆"）；新增「合并重叠」按钮（重叠/相接区域并成连通区）与「删除」工具（命中区域柔和红闪后移除）。详见 §3.3。
- **SR 部署定论（08-31）**：目标机 = 现成 CentOS7 盘阵那台；生产跑 `realesrgan_trt`；保留 Slurm；SR 用**裸机+Slurm**（TRT 版本锁定，不加 Docker）；bundle/权重/so/TRT 全在 `/DiskArray`（Linux 读写更快），打包 = 挂载复用、路径零改动（详见 §3.2）。
- **Agent 最小原型 M1（08-31 晚）**：`backend/agent/loop.py` 自写循环 + 首个真实工具 `fix_bad_lines`；**M1 待完善 P0①②③ 已补齐（09-01）**——SQLite 会话持久化 + run_sr 幂等层 + 假路径拦截，**114 测试全过**（详见 §2.5）。
- **下一步**：P1 ④⑤⑥⑦（见 §2.5）；配 LLM key 真闭环、Slurm/run_sr 真机验证（见 §2.6）。
- **约束提醒**：开发机 e2e 浏览器仍无法启动（环境问题，见 08-29 条目）；真实图在内网盘阵，外网机读不到（见 §5.1）。

## 2. 里程碑计划与待办

### 2.1 平台里程碑（M1 / M2 / M3）

- **M1 验证闭环（当前）**：Agent 秒级验证闭环。Agent 层自写状态机 + `contract.py` 工具契约，不引 langchain / langgraph；SQLite 会话持久化 + run_sr 幂等层已就位（见 §2.5）。
- **M2 批量队列 / 可视化**：多人共享队列、任务进度可视化（P2/P3 覆盖）。
- **M3 生产端到端**：跨小时断点恢复 + 人审，按需上 LangGraph 薄层。
- **依据**：里程碑阶段名源自 `docs/planning/web-plan.md`；Agent 层具体范围与行为锚定 `docs/knowledge/agent-orchestration-research.md` §5/§6（08-31 定稿，已取代 web-plan.md 的 langchain 选型）。

### 2.2 优先级 P0–P4

- **P0 阻塞**：定 LLM 底座（内网有无第二台 GPU）+ Slurm 接入验证（sbatch 提交 / squeue 轮询）。注：0817←0820 合并已取消，两版按环境选脚本（run_sr 已按 0817 提交）。
- **P1 工具库先行（当前在做）**：见 §2.3，纯价值零 LLM。
- **P2 FastAPI 骨架**：REST + SSE + **Slurm 客户端**（提交/轮询/取消）+ SQLite 作业表，工具暴露成端点。
- **P3 Vue3+TS 前端骨架**：聊天 + 共享队列 + 移植 TIF 查看器（**双数据源**）+ 盘阵检索/查看 UI。
- **P4 Agent 编排层**：最后加，native function-calling 状态机或 LangGraph。

### 2.3 P1 交付物（工具库）

1. `tools/` 包 + **工具契约**：`name` / `description` / JSON-schema params / `run(**params)->result`。
2. 首批工具：
   - **SR 侧（已有代码纯包装）**：`run_sr_inference`（包 `code_0817_prod.py`（Linux 生产版）为无头/Slurm 调用）、`run_all_folders`（内网，需回传/重写）、掩码生成（掩码.tif + 掩膜中心点坐标.txt）。
   - **cv 侧（从零，先做一个定契约）**：如 `fix_bad_lines`（坏行/条带修复，OpenCV/numpy）。
   - **盘阵检索**：`search_scenes`（先定接口 + 本地假实现，真机再接盘阵）。
3. 注册表（在 `tools/contract.py` 内，`@tool` 装饰器自动收集）→ `manifest()` 生成 OpenAI 兼容工具清单 JSON（日后直接喂 agent function-calling + 自动生成 API 文档）。

### 2.4 P1 进展（2026-08-30 → 08-31）

> **校正**：`grid_offset_planner` 是 `run_sr` 全流程里的局部函数，**不当 agent 工具**。工具粒度 = 流程级操作（run_sr / 批量 / 检索 / cv 修复），内部积木归 `mta_grid/`/`services/`。

- ✅ `backend/mta_grid/grid_planner.py`：忠实移植 `_count_sr_tiles` / `_grid_offset_planner`（纯 numpy，作 `run_sr` 的积木）。纯算法测试仍全过。
- ✅ `backend/tools/contract.py`：工具契约 `Tool` + `@tool` 注册表 + `ok/err` + `manifest()`（契约成立，首个真实工具待流程级）。
- ❌ 已移除 `backend/tools/grid_planner.py`（误把积木当工具的包装）。
- **分层定论**：`services/`（流程编排，`run_sr` 在此组合 mta_grid + 推理 + util）→ `tools/`（流程级 agent 工具，薄壳调 services）→ `mta_grid/`（纯算法积木）。
- ✅ **agent 最小原型 M1（2026-08-31 晚）**：`backend/agent/loop.py` 自写状态机（工具错误回填自愈 + 迭代上限，**不引 langchain/langgraph**）+ `backend/config.py`（env 驱动 `SR_LLM_BASE_URL/API_KEY/MODEL/...`）+ CLI `python -m backend.agent [--tools|--json|--max-turns] "prompt"`。**首个真实流程级工具 `fix_bad_lines`**（`services/fix_bad_lines.py` 纯 numpy 坏行/条带检测 + 邻线中值替换、Pillow IO；`tools/fix_bad_lines.py` 薄壳）。36 测试全过（含循环 6 场景：直达答案 / 工具→答案 / 工具错误不中断 / 未知工具 / 迭代上限 / API 错误）。
- ✅ **`search_scenes` 盘阵检索工具（2026-08-31 晚）**：定接口 + 双后端——`SR_SCENES_ROOT` 指向真实目录则扫盘（`scan_root` 递归 + 文件名解析 satellite/sensor/date，对齐 `JL1KF02B03_PMS05_20260722125045...` 命名），未设置/目录不存在则回退**确定性假数据**（`fake:true` 标记，防 agent 把占位路径喂给 run_sr）。过滤：query 子串 / satellite / date_from~date_to / limit。新增 18 测试（现共 **54 全过**）。真机接盘阵 = 只设 `SR_SCENES_ROOT` 指向挂载点。
- ✅ **`run_sr` + `sr_job_status`（2026-08-31 晚）**：异步 Slurm 作业形态（生产路径 = Slurm 提交 0817，输入 = 高层参数组装 XML）。`services/slurm.py` 薄客户端（sbatch/squeue/sacct/scancel，`run_cmd` 可注入做测试）；`services/run_sr.py` 高层参数（lq_path/mask_path/sr_scale/suffix/gpu/cloud_limit/delete_ori/grid_align/options_yml）→ 组装 `<SFSR_Config>` XML + batch 脚本（`--gres=gpu:1`、cd bundle、`code_0817_prod.py -f`）→ sbatch 返回 job_id。`sr_job_status` 轮询（squeue 活动态 → sacct 终态+退出码）。**本机无 sbatch → 干净 err**（不会误信 SR 已跑）；真机需设 `SR_BUNDLE_DIR/SR_PYTHON/SR_SLURM_WORK_DIR/SR_SLURM_PARTITION`。新增 18 测试（现共 **74 全过**）。

### 2.5 M1 待完善（P0 已完成 09-01 · P1 待接续）

> **P0①②③ 已补齐（2026-09-01，114 测试全过）**，对照 `agent-orchestration-research.md` §5 的 M1 承诺已兑现（会话持久化、幂等层），并补假路径拦截。P1 ④⑤⑥⑦ 留待办，全部开发机可做，不卡内网。

**P0 · 已完成（M1 范围真实缺口）**

1. ✅ **会话持久化**（§5.1 / §6.3）：`backend/services/store.py`（SQLite sessions + messages + sr_tasks 三表）；checkpoint 时机 = **每步前**（助手 tool_calls 在工具副作用前落库，崩溃留下可修复的「未闭合 turn」）；`loop.py` 接入、messages 落库，`session_id` + `resume=True` 续接接口就绪（P1⑦ CLI --resume 铺路）；恢复时按 deepseek-harness repair.ts 语义合成「结果未知、勿盲目重试」，不重跑工具。
2. ✅ **run_sr 幂等层**（§5.3）：sr_tasks 表存 `job_id`，按参数指纹（sha256）去重；`submit_run_sr` 前先查表，已有 job_id 先 squeue/sacct —— active / COMPLETED 复用（RESUMED_ACTIVE / RESUMED_COMPLETED，**不重复提交**），FAILED / UNKNOWN 重跑；中断提交（无 job_id）→ err 不盲目重试。新增用例锁住「不重复提交」。
3. ✅ **run_sr 假路径拦截**（§6.2 handle_tool_errors 语义）：`lq_path`/`mask_path` 含 `<fake>` 或非绝对路径 → err（不抛异常，回填自愈改参重试），新增拒绝用例。

**P1 · 待办（健壮性/体验）**

4. **配置 UX**：`.env` 加载（零依赖，~20 行）+ `.env.example` 入库；`system_prompt`/`max_turns` 进 config（现硬编码在 `loop.py`）。
5. **瞬时错误重试**：LLM 偶发 429/超时目前直接杀循环。补 N 次退避重试再放弃（langgraph RetryPolicy 借鉴项——只做了迭代上限，没做重试）。
6. **测试缺口**：loop 畸形响应/空 choices 用例（当前已兜住但没锁住）；`fix_bad_lines` uint8/float 用例（现只测 uint16）。
7. **CLI `--resume <session_id>`**（依赖 1，接口已就绪）。

**P2 · 边界外 / 靠真机（暂不做）**

- tiles 结构化进度回传（`{"tiles_done","tiles_total"}`）——需改 0817 脚本在作业内写进度文件，服务器侧。
- 工具执行门控/审计（deepseek-harness allow/deny/ask）——多人共享队列时才需要，M2。
- 真机验证：LLM key 真闭环、Slurm 实机提交、盘阵 `SR_SCENES_ROOT`。

### 2.6 下一步（平台侧）

> 开发机可推进的新工作 = §2.5 的 **P1 ④⑤⑥⑦**；其余为配置/真机事项。

1. **配 LLM 端点跑真闭环**（M1 已具备，缺 key）：`SR_LLM_BASE_URL` / `SR_LLM_API_KEY` / `SR_LLM_MODEL` → `python -m backend.agent "把 test-tifs 下这张图修一下坏行"`。key 走环境变量（无 .env，见 09-01 条目）。
2. **Slurm 接入验证**（内网真机）：`sbatch` 提交 0817 + `squeue/sacct` 轮询，跑通一条真实 job.xml（0817←0820 合并已取消，两版按环境选脚本）。
3. **run_sr 真机验证**（CentOS7 盘阵机）：设 `SR_BUNDLE_DIR/SR_PYTHON/SR_SLURM_WORK_DIR/SR_SLURM_PARTITION` 后，用假配置 xml 跑通 sbatch→squeue→sacct 一条真实 job.xml；确认 `code_0817_prod.py` 的 `<MaskPath>` 消费 0/255 掩码。
4. 继续 P1：盘点 `run_all_folders` 能否无头跑（内网，需回传/重写）；`mta_grid` 已移植，run_sr 的 `tiles` 进度回传待 Slurm 作业内加结构化 JSON（现 0817 无进度条）。
5. 确认 LLM 底座可选项（内网有无第二台 GPU）——决定 M3 生产底座，M1 不阻塞。

> 记忆索引：`~/.claude/projects/.../memory/MEMORY.md`（现 5 条：local-vendor-libs-for-viewers / browser-2gb-alloc-cap / intranet-data-inaccessible / real-files-uncompressed-1row-strips / openai-version-pin）。

## 3. 背景决策

### 3.1 平台化技术栈方向（2026-08-30 定调）

> 结论先行：前端 Vue3+TS（**移植不重写** tif-viewer 逻辑）；后端 FastAPI + 自建作业队列 + 工具库；**LangChain 非地基，后置为可选薄层**。四问已答：LLM 底座**未定**（按 OpenAI 兼容写，可替换）、**SLURM 计划保留**（GPU 调度用 Slurm，FastAPI 作其客户端）、cv 工具箱**从零开发**、近期**先包工具库（P1）**。

> **修正（2026-09-01）**：前端由 React+TS 改为 **Vue3+TS**（`.vue` 单文件 + `<script setup lang="ts">`）；「移植不重写」决策与「框架无关 TS 模块」抽取不变。

> 无公司技术标准（08-31 确认）：老员工（谢志兵）表示此前 Vue2+SpringBoot 工程只是 clone 的 GitHub 源码、可用可不用；Nginx/Redis 可选。技术栈完全自由，维持上述选型；Spring Boot 门面折中方案作废。

**技术栈决策**

| 层 | 选型 | 理由 / 要点 |
|---|---|---|
| 前端 | Vue3 + TypeScript（Vite 构建 + Nginx 静态托管，离线 vendor） | 多视图 + 共享状态 + 实时进度是 Vue3 主场；`naming-conventions §4` 已预留 `TifCanvas.vue`/`tifDecode.ts` 结构 |
| 前端迁移 | **移植不重写** tif-viewer | 116KB HTML 里已踩完坑的解码/拉伸/稀疏条带逻辑抽成**框架无关 TS 模块**，Vue3 应用 import 它 |
| 后端框架 | FastAPI（REST + SSE） | Agent/SR/cv 工具全在 Python，避免双语言；SSE 推任务进度 |
| 作业队列 | **Slurm 调度 GPU**（sbatch 提交 code_0817）+ FastAPI 作 **Slurm 客户端**（轮询 squeue/sacct）+ SQLite 作业表 | 3090×4 计划保留 Slurm，SR 脚本本就集成 Slurm（健康门 stop slurmd）；SR 是阻塞 GPU 进程，靠 Slurm 排队；CPU 小活（掩码栅格化等）可自建轻队列或也走 Slurm；SQLite 记任务元数据，Redis 后置 |
| LLM 底座 | **未定**，统一按 OpenAI 兼容 `/v1/chat/completions` + tools 参数写 | 底座可换（Ollama→vLLM/DeepSeek 不改业务码）；有第二台 GPU→vLLM+Qwen2.5-14B，仅单 3060→Ollama+Qwen2.5-7B Q4 与 SR 错峰 |
| Agent 层 | 先不碰 LangChain 全家桶；原生 function-calling + 自写 ~200 行状态机 | 撞到跨小时级断点续跑/人审再上 **LangGraph**（此时是替换薄层，非重写）；`langchain-master` 当参考不引为依赖；边界见 docs/conventions/langchain-boundary.md |

**前端实现约束（2026-08-31 · 两轮问答确认）**

| 约束 | 结论 |
|---|---|
| 浏览器 | 仅 Chrome/Edge → FS Access、WebGL2 放心用，无需降级 |
| 查看器数据源 | 本地 File + 盘阵 HTTP **两条并存** → 解码层抽 `source.read(offset,len)→Promise<ArrayBuffer>` 抽象（FileSource / HttpSource），IFD/稀疏/分块/拉伸复用 |
| 掩码 | 键鼠 + 像素坐标（无 GIS），掩码=像素坐标数组，存后端 |
| 任务队列 | 多人**共享同一队列** → 前端实时同步 + 乐观更新 + 并发处理；服务端为唯一事实源 |
| 实时通道 | `pushClient` 单模块抽象，**SSE 先行**（反代兼容最好、单向推状态够用），WS 可换；动作走 REST |
| 构建/部署 | 无公司标准；产物自包含（离线 vendor，Vite 构建，Nginx 托管） |
| 时间预算 | 1-2 个月 → 可做完整平台（对话+队列+掩码+失败诊断） |

**前端迁移策略（2026-09-01）**

- **HTML 侧收口条件**（满足后 tif-viewer.html 冻结，不再新增功能）：
  1. 掩码「删除」+「合并重叠」完成并提交（现未提交）——掩码编辑功能集定稿；
  2. 真机实测掩码直出：真实大图（24739×24199）浏览器直出 `掩码.tif` + `掩膜中心点坐标.txt`、稀疏预览 8192 不崩（开发机仅 jsdom 接线，交互层未实测）。
- **冻结后新增功能直接进 Vue3（P3）**：盘阵 HTTP 读图（HttpSource）、多视图、任务队列，不再加进 HTML。
- **Vue3 迁移时机 = 冻结后 + P2 就绪**（FastAPI REST/SSE，HttpSource 有端点可绑）：逻辑层忠实移植（沿用「移植不重写」决策），交互层用 Vue3 惯用法重写。
- **可提前项**：`tifDecode.ts` 抽取 + Node 像素级 golden 单测，不依赖 Vue3 时机，为移植备回归基准。

### 3.2 SR 部署定论（2026-08-31 · 两版对比 + 部署形态）

- **目标机**：现成 CentOS7 盘阵那台，mmsr_bundle/权重/ImgHistMatch.so/TRT engine 全在 `/DiskArray`（Windows+Linux 都能访问，Linux 本地读写显著更快）→ **打包=挂载复用，代码路径零改动**。
- **生产跑 realesrgan_trt** → 依赖 `torch_tensorrt` + TRT engine（**版本锁定**，engine 在 `/DiskArray/.../trt/t2trt_fp16_realESRGAN_1640.trt`）；**ImgHistMatch.so 只有 restormer 用，生产可忽略**。
- **部署形态**：SR = **裸机 + Slurm（不加 Docker）**——TRT 版本锁定 + 固定机不换 + Slurm 本就在裸机起作业，Docker 只添耦合；平台服务（FastAPI/Nginx）可选 Docker 或裸机 venv+systemd。
- **两版对比（SR_code/）**：`code_0817_prod.py`（Linux 生产版：4 卡校验 `gpu_count!=4`→stop slurmd、硬编码 /DiskArray、无进度条）vs `code_0820_prod_windows.py`（Windows 开发分支：lib 软容错、**进度条+五段剖析**、去掉 Slurm 联动、GPU≥1）。**核心流水线逐行一致 → 合并**：0817 作底 + 0820 进度/剖析并回 + 加结构化进度 JSON（`{"tiles_done":N,"tiles_total":M}`）供队列 tail→SSE。
- **代码小改动**：`CUDA_VISIBLE_DEVICES` 硬编码 `"0"` 改为**交给 Slurm 分配**（`--gres=gpu:1`）；清 `__main__` 残留 `"1"`。

### 3.3 掩码绘制（前端已落地 · 2026-08-31）

- **需求**（gui-requirements G1/P0）：本地 HTML 交互式绘制 ROI 掩码，输出 `掩码.tif`（**分辨率与原图一致**）+ `掩膜中心点坐标.txt`。
- **核心矛盾**：预览/JPG 是降采样（稀疏 8192≈1/3、其余 2048）+ 8bit 拉伸，而掩码要全分辨率。**JPG 的有损/拉伸不影响掩码几何（掩码只关心位置 0/1），只有分辨率影响精度。**
- **设计**：HTML 只出**矢量多边形**（在预览画布上画，顶点坐标按 scale 映射回原图像素）；全分辨率栅格化最初计划放 Python（6 亿像素，浏览器存疑）。**08-31 已验证浏览器可承载**：栅格化按行流式（`rasterRows` 逐行事件扫描，非全量位图）+ pako Deflate 压缩，内存占用只与压缩缓冲相关 → **浏览器直出成为默认路径**，Python 路径保留为备选。
- **已定**：① 栅格化前后端双实现（`tif_viewer/maskgen.js` 前端流式 + `backend/services/mask.py` 后端 Pillow，12 测试全过、产物交叉验证逐像素一致）；② 精度**混合**——先在 8192 预览粗画、`thumbToOrig` 反算全分辨率（**09-01 定：不做「按可视区读全分辨率」放大精画**，预览=JPG 中间产物，见 §3.4）。
- **矢量格式**：`{"width":W,"height":H,"polygons":[{"label":"roi","points":[[x,y],...]}]}`，x=列、y=行（原图像素坐标）。填充约定：顶点为像素中心、**边界含入**（方形 [2,2]..[7,7] 覆盖 6×6）。
- **✅ 前端已实现（tif_viewer/tif-viewer.html）**：工具栏「绘制掩码」按钮 → 进入绘制模式（光标变十字）→ 顶部浮动面板选「矩形/多边形/魔棒」工具，在叠加层 `#drawCanvas` 上画多个 ROI（矩形拖拽、多边形点击加点 + 双击/回车/右键闭合 + Esc 取消、撤销/清空）。顶点存**缩略图坐标**，渲染时按 `(ox,oy,scale)` 映射回屏幕，随平移/缩放走。
- **魔棒（2026-08-31 新增）**：`WAND_WIN=4096` 取种子周围窗口 → `floodSelect`（容差 + 边缘梯度屏障）→ 洞填充 + 外轮廓追踪 → `simplifyPoly(0.5)` → 转缩略图坐标 ROI。适配常见地物（同色连通区一键圈选）。
- **魔棒自适应区域生长（2026-09-01 修复"总出默认椭圆"）**：旧 `floodSelect` 是"固定容差 vs 种子像素"，纹理/渐变地物被碎成多个小区域。改为**自适应区域生长**：7×7 种子窗口初始化运行统计（均值/平方和）→ 窗口 = `max(tol, 2.5*spread)`，spread = 逐像素 RGB 距离 RMS（分母 `√cnt`，**非 `√(3cnt)`**——修掉 √3 维数不匹配，灰像素 RGB 距离 = 亮度差×√3）→ 随生长增量重算（每 256 像素一次、窗口单调不减）。边缘屏障 = 相邻像素亮度差 > `WAND_EDGE` 硬停（32→64，避免纹理内部被切碎）；`WAND_MAX_PX=8e6` 防失控。验证：默认 tol=20 下 ±15/±30/±40 纹理、漂移、边界种子 100% 覆盖，bg 150 零泄漏；4096² 全窗口 ~1.2s。**已知边界**：近底色纹理（区域 ±30 纹理 vs bg 差 10-20）无法用任何单一窗口分离（色差 < 2.5×spread，降低 tol 无效）。
- **区域重叠与「合并重叠」按钮（2026-09-01）**：掩码.tif 本就是 0/1 像素**并集**（`rowIntervals` 跨多边形合并相接/重叠区间）→ 重叠/相接多边形天然合成一个连通区，像素层面无重复。用户选择**手动合并**而非生成时自动合并（不改变输出区域数）：`maskgen.js` 新增 `rasterMask`（栅格并集）+ `connectedComponents`（BFS 连通域标记）+ `mergeConnected`（1px 膨胀 → 并集栅格化 → 洞填充 → 逐连通区重追踪轮廓 → 收缩回 1px）；tif-viewer「合并重叠」按钮调用，`X 个区域 → Y 个连通区` toast。
- **合并协程化 + 进度条（2026-09-01）**：真实图缩略图 ≈8192×8013（6500 万像素），原 `mergeConnected` 主线程同步执行会冻结数秒。重构为生成器 `mergeConnectedGen`（每批工作后 `yield {phase, progress}` 让出主线程）+ 同步 `mergeConnected`（Node/测试驱动到完成，结果不变）+ 协程 `mergeConnectedAsync`（setTimeout 驱动，`onPhase/onProgress`）。浏览器 `mergeRois` 接四阶段遮罩进度条（栅格化→洞填充→连通域→轮廓提取）+ 按钮禁用 + **owner 守卫**（合并期间切图则丢弃结果不写错图）。顺带优化：每连通域只在自身 **bbox 子图（+1px 背景）**重追踪轮廓，不再整图重扫/整图分配（原为每域新建整图 65MB 数组）。
- **删除工具（2026-09-01）**：`drawTool='del'` 时点击**命中检测**（`pointInPoly` 射线法），hover 高亮，命中后 **200ms 柔和红（#d8605a）闪烁再移除**。异步移除防误删：`delClick` 捕获 `owner=activeRec`，`setTimeout` 回调里 `activeRec===owner` 才 splice（闪烁期间切图/清空则跳过）。
- **浏览器直出掩码（2026-08-31，无需 Python）**：「生成掩码」按钮 → ROI 经 `thumbToOrig` 反算回原图像素 → `MaskGen.buildTiff`（`rasterRows` 逐行 + pako Deflate → classic TIFF 小端/8bit/压缩8）→ 下载 `掩码.tif`（**0/255**）+ `掩膜中心点坐标.txt`（**质心点阵，2 位小数，CRLF**）。E2E 钩子已挂 `window.__viewer.{enterDraw,exitDraw,buildMaskJson,exportMaskJson,thumbToOrig,getRois,genMask,wandSelect,maskGen}`。
- **产物格式（2026-08-31 对齐参考文件定稿）**：txt 参考 `SR_code/JL1KF02B03_..._mask.txt`——`＃掩膜中心点坐标（X，Y）\r\n＃掩膜编号，X坐标，Y坐标\r\n` + `序号,质心X,质心Y`（2dp、CRLF、UTF-8）；掩码.tif 前后端一致用 **0/255**（`run_sr` 经 `cv2.threshold(>0)` 消费，0/1 等价）。原「01点阵.txt」（W×H 字符、24k² 图 ~600MB）方案**废弃**。
- **端到端已验证**：maskgen Node 单测 **12 项全过**（含与 Pillow 逐像素比对、随机多边形仅边界 ≤1px 离散化差异、纹理整块点选、重叠→1 区 / 相接→合并 / 包含→吞噬三组 merge）；后端 `test_mask.py` 12 项全过（txt 逐字节对齐参考、tif 0/255 往返）；jsdom 冒烟 **5 项全过**（页面加载 + genMask 直出两次下载 + wandSelect 加 ROI + 合并按钮接线 + 删除命中/闪烁/移除）。
- **剩余**：① 放大精画路径 **已砍**（09-01 预览路径定案=JPG 中间产物，不做浏览器内全分辨率读取；保持 8192 预览粗画 → `thumbToOrig` 反算全分辨率，符合已定混合精度）；② 真机浏览器实测（开发机 e2e 浏览器无法启动，jsdom 只验证接线，pako/像素管线已在 Node 层覆盖）。

### 3.4 新需求（2026-08-27 晚）——已收口（09-01 预览路径定案）

- **显示拉伸（ENVI 风格）+ 像素定位**：✅ 已实现并全部 E2E 通过（见时间线）。
- **加速大图读取**：真实文件布局已确认（GF07A03/KF02B04 均**无压缩·条带1行** → **无需金字塔**，任意窗口直接字节切片）；**稀疏条带预览**（秒级概览）✅ 已完成，显示分辨率提到 8192（`SPARSE_PREVIEW_MAX`），与导出 JPG 同清晰度。
- **预览路径定案（2026-09-01）= JPG 中间产物**：网页/预览显示导出的 JPG 产物（长边 8192 ≈ 原始 1/3），不再在浏览器内对原始 TIF 做全分辨率切片读取。
- **因此「按可视区按需读全分辨率瓦片」已砍**（原设想：缩放超过预览分辨率时按可视窗口条带切片叠加绘制、读量与屏幕分辨率成正比、无压缩直接切片、无需金字塔）——其前提被 JPG 产物路径取代。若日后要网页内全分辨率，无压缩条带下仍可直接字节切片捡回（无需金字塔，工作冻结非丢失）。
- **取舍明示**：JPG 为 8bit 有损 + 8192≈1/3 分辨率，网页内「看 1/3 以上细节」被放弃，细节靠 JPG 产物/原始文件看；掩码保持「8192 预览粗画 → `thumbToOrig` 反算全分辨率」，放弃「放大精画」升级（不破坏掩码产物本身）。

## 4. 时间线

### 2026-08-26 · 起点：CDN 不可达
- 用户报错：`geotiff.js 加载失败（CDN 不可达）`。内网机加载 jsdelivr 失败。
- 修复 quick-look.html：移除 jQuery（改原生 JS）、修 `toRGBA8` 返回 Uint8Array 而 ImageData 需 Uint8ClampedArray 的兼容问题、**pako+UTIF 内联进 HTML 单文件（零外部引用，自包含）**。

### 2026-08-27 上午 · 方案定型 + 2GB 分配上限
- 用户选择「UTIF 单库简化版（冻结等待）」。目标：10000² 内 UTIF 全量，12000² 以上分块。
- 实测失败：`解码失败，Array buffer allocation failed`。
- `.e2e/probe.js` 确认：**Edge 单次分配上限 ~2GB（2.0GB 成功，2.7/3.6GB 抛 RangeError）**，64GB 内存无关。30000² RGB8 全量解码（2.7GB+3.6GB）必然失败。
- 引入 **geotiff.js 分块回退**（本地 vendor），构建双引擎架构。

### 2026-08-27 下午 · 修 400MB 全黑 + 收口大图解码失败
1. **复现全黑**：生成 float32/uint32 测试图 → UTIF 路径 100% 全黑。根因：**UTIF.toRGBA8 灰度只支持 bps 1/2/8/16，32bit 落空全 0**。
2. **定位第二个全黑 bug**：16bit RGB 输出 `[0,255,0,0,…]`——**alpha 被拉伸系数缩放**（scale≈0.0039 → alpha 0 → 全透明黑）。修：alpha 永不缩放，comps<4 强制 255。
3. **修浮点/uint32 全白**：统一 min-max 线性拉伸（跳过 NaN/Inf），8bit 原样直连。
4. **geotiff 惰性 fileDirectory 陷阱**：getFileDirectory 是惰性桩、无 getField/getTag → 自写 `tiffTags()` 直接解析 TIFF/BigTIFF 头部 tag 262(photometric)/259(compression)。
5. **修 WhiteIsZero 反色**（photometric=0 → 输出反相）。
6. **修 E2E 陷阱**：页面清空 input.value → 从 `window.recs[].file` 读文件。
7. **验证通过**：
   - `test-types.js`：8 种数据类型（8bit RGB / 16bit 灰度 / 16bit RGB / uint32 / float32×3 / WhiteIsZero 反色）全部通过。
   - `test-regress.js`：8192² uint16 灰度 134MB → 分块多窗口 + 进度 100% + 渐变正确（4.5s）；UTIF 分配失败自动回退 geotiff 通过。
8. 文档落盘：`docs/experience/gui-experience.md`（经验）、`.gitignore`（屏蔽 test-tifs/.e2e/*.zip）、内存文件更新。

### 2026-08-28 · 稀疏条带预览（大图加速）
- 用户回传探针：GF07A03、KF02B04 均为**无压缩 · 条带1行**。
- 2 分钟之谜根因确认：旧路径把全图全量读一遍做预览，耗时 ∝ 字节；且 geotiff 窗口读在 1 行条带上每窗口触发 ~4096 次小 slice（42 窗口 × 4096 ≈ 17 万次切片读）。
- 实现 `parseStrips`（头部解析条带偏移/长度数组，兼容 classic/BigTIFF）+ `sparseCollect`（按条带字节切片，抽 ph 行 × 抽样列，8 路并发，进度条）；`stripPxVal` 支持 8/16/32bit 的 uint/int/float。
- 路由：`无压缩 + 条带 + 单波段 + >100MB` → 稀疏预览；否则原分块/UTIF 路径不变。
- E2E `test-sparse.js` 全过（像素精确 + Deflate 回退）；`test-regress/stretch/stretch8/locator/types/probe-list` 全过无回归。
- 134MB 8192² 测试图：稀疏 **1.3s**（读 32MB/128MB）；原全量 4.5s。big_u16 也自动转稀疏（1.5s）。

### 2026-08-29 · JPG 导出三 bug 解耦修复（稀疏大图）
- 用户报：导出 JPG 报 `Cannot read properties of undefined(reading 'p2')`；预览/导出分辨率停在 2048×2003（位图过低，想要 ~1/3）；右/下大片拉伸条纹。要求解耦分步排查。
- **Bug A（导出崩溃，根因）**：`sparseCollect` 的 silent 返回 `nbands:1`，而 `exportToJpg` 读 `c.nb` → 未定义 → `stretchRgba` 误走 3 波段分支 → `stretchMap` 在 `st[1]`（不存在）上读 `p2` → 崩溃。修：silent 返回补 `nb:1`；另给 `stretchMap`/`stretchRgba` 加兜底（统计缺失时按值截断返回，绝不抛错）。
- **Bug C（右/下条纹，根因）**：`sparseSample` 用 `ceil(W/pw)` 定步长 + clamp 到 W-1 → 右/下各 ~145 列/行重复最后像素。修：改目标→源线性最近邻映射 `round(j*(W-1)/(pw-1))`，端点精确对齐。
- **Bug B（分辨率）**：用户看到的 2048×2003 是预览（`PREVIEW_MAX=2048` 的产物）；导出因 Bug A 从未成功。修：默认导出长边 `JPG_MAX` 16384→**8192**（24739 宽 → 8192×8013 ≈ 原始 1/3），同时规避 16384² 画布面积/toBlob 边界；失败降档重试同步 8192→4096。
- 同步：`docs/knowledge/jpg-export-background.md` 默认值/降档说明；`.e2e/test-sparse.js` 注释（新映射下断言仍成立，`round(i*8191/2047)=4i`）。
- ⚠️ **待验证**：开发机 e2e 浏览器启动仍失败（puppeteer-core 25.9.0 + 无头 Edge，Code: 0，环境问题，与本次改动无关）；真机（内网）验证：打开 24739×24199 图 → 预览无条纹、2% 线性导出得 8192×8013 JPG 且无报错。

### 2026-08-29 · 网页显示清晰度对齐本地 JPG（稀疏预览 2048→8192）
- 用户报："同样的 jpg，本地目录预览清晰度远超 html 网页显示"。根因：导出 JPG 是 8192（≈1/3），而**网页稀疏预览仍是 2048**（`PREVIEW_MAX`），所以页面图被放大看就糊。
- 方案（用户选定「网页按 8192≈1/3 直接显示」）：新增 `SPARSE_PREVIEW_MAX=8192`，仅**稀疏条带路径**预览目标长边 2048→8192；`sparseSample` 默认目标改为该常量。chunked/UTIF 路径保持 2048（压缩/小图预览仍快）。
- 代价与约束：稀疏预览 8192² 时 src(Float32)+canvas ≈ **0.5GB/图**，避免同时开过多大图（已写入 gui-experience §4 内存提示）。
- 同步：`.e2e/test-sparse.js` 与 `.e2e/test-regress.js` 预览尺寸断言 2048→8192（测试图为 8192²，无压缩命中稀疏，全分辨率恒等映射）；`docs/knowledge/jpg-export-background.md` 预览降采样说明改口（稀疏 8192 / 其余 2048）。
- ⚠️ **待真机验证**：8192² 稀疏预览在真实 24739×24199 图上的内存/耗时；e2e 本机仍无法启动（环境问题）。

### 2026-08-31 · 前端约束问答 + SR 部署定论（平台化决策）
- **前端约束两轮问答**（见 §3.1「前端实现约束」表）：仅 Chromium、查看器双数据源（本地 File + 盘阵 HTTP，解码层加 `source` 抽象）、掩码键鼠像素坐标、多人共享队列、`pushClient`（SSE 先行）、Vite 自包含产物、1-2 个月完整平台。
- **无公司技术标准确认**：老员工（谢志兵）称此前 Vue2+SpringBoot 工程只是 clone 的 GitHub 源码、可用可不用，Nginx/Redis 可选 → 技术栈完全自由，维持 Vue3+TS+FastAPI；Spring Boot 门面折中作废。
- **SR 两版对比**（`SR_code/code_0817_prod.py` vs `code_0820_prod_windows.py`）：核心流水线逐行一致；差异=lib 软容错、进度条+五段剖析、Slurm 联动（stop slurmd）、GPU 数校验（==4 vs ≥1）。合并策略见 §3.2「SR 部署定论」。
- **部署形态定论**：目标机=现成 CentOS7 盘阵那台（bundle 已跑通）；生产 realesrgan_trt（TRT 版本锁定）；计划保留 Slurm；SR 裸机+Slurm（不加 Docker）；bundle/权重/so/TRT 全在 `/DiskArray`（Linux 读写显著更快）。详见 §3.2。

### 2026-09-01 · M1 待完善 P0①②③ 补齐（会话持久化 + run_sr 幂等 + 假路径拦截）

- ✅ **P0① SQLite 会话持久化**：`backend/services/store.py`（sessions + messages + sr_tasks 三表，lazy 连接）。checkpoint 时机 = **每步前**（借 deepseek-harness「副作用前 checkpoint」：助手 tool_calls 在工具执行前落库，崩溃留「未闭合 turn」）。`loop.py` 接入，新增 `session_id` + `resume=True` 续接接口（P1⑦ CLI --resume 铺路）；恢复时按 repair.ts 语义合成「结果未知、勿盲目重试」，**不重跑工具**。CLI 加 `--db`。16 测试。
- ✅ **P0② run_sr 幂等层**：sr_tasks 表按参数指纹（sha256）key 任务；`submit_run_sr` **先查表再提交**——已有 job_id 先 squeue/sacct：active / COMPLETED 复用（RESUMED_*，**不重复提交**），FAILED / UNKNOWN 重跑，中断提交（无 job_id）→ err 不盲目重试。修 `run_cmd=None` 覆盖默认 _run 的潜伏 bug。8 测试锁住「不重复提交」。
- ✅ **P0③ 假路径拦截**：`run_sr` 拒绝 `lq_path`/`mask_path` 含 `<fake>` 或非绝对路径 → err（回填自愈，不抛异常）；修 `@tool` 装饰器误挂 `_bad_path` 导致 registry 分派 TypeError，加 registry 回归测试。
- 修复：openai 3.x 与 aiohttp 3.8.3 不兼容 → 锁 `openai>=1.40,<2`（1.109.1）；resume 测试 chat seam 用引用快照；Windows 临时目录清理 LIFO（先关 store 再删目录）。
- 端到端冒烟：假路径 → err 回填 → 模型自愈换真路径 → 恰好一次 sbatch → 任务行 job_id 落库。
- 提交：`034081c`（12 文件 +1093/-61，含先前未提交的 gui-experience / current-question 预览定案改动）。
- **api_key 接入**：无 .env / 配置文件，CLI 直接读环境变量 `SR_LLM_BASE_URL` / `SR_LLM_API_KEY` / `SR_LLM_MODEL`（`backend/config.py`，base_url 默认 `https://api.openai.com/v1`）；P1④ 将补 `.env` 加载消除手敲。
- **文档依据澄清**：M1/M2 **阶段名**源自 `docs/planning/web-plan.md`（M1 验证闭环 / M2 批量队列可视化 / M3 生产端到端）；**Agent 层具体范围与行为**锚定 `docs/knowledge/agent-orchestration-research.md` §5/§6（08-31 定稿，自写状态机不引库，已取代 web-plan.md 的 langchain 选型）。

### 2026-09-01 · 魔棒自适应修复 + 掩码区域合并/删除工具

- ✅ **魔棒自适应区域生长**：`maskgen.js` `floodSelect` 重写——7×7 种子窗口运行统计 + `max(tol, 2.5*spread)` 自适应窗口（spread 分母 `√cnt` 修 √3 维数不匹配）、每 256 像素增量重算、边缘屏障 `WAND_EDGE` 32→64。纹理/漂移/边界种子 100% 覆盖，bg 150 零泄漏；详见 §3.3。
- ✅ **「合并重叠」按钮**：`maskgen.js` 新增 `rasterMask` / `connectedComponents` / `mergeConnected`（1px 膨胀→并集栅格化→洞填充→逐连通区轮廓重追踪→收缩）。用户选定**手动合并**（不动输出区域数）。tif-viewer 加 `mergeRoi` 按钮 + `mergeRois()`。
- ✅ **「删除」工具**：`drawTool='del'` 点击命中（`pointInPoly` 射线法）→ hover 高亮 → 200ms 柔和红（#d8605a）闪烁 → 移除；`delClick` 捕获 `owner=activeRec` 防闪烁期间切图/清空误删。
- ✅ **测试**：`.e2e/test-maskgen.js` **12 项**全过（纹理整块点选 + 三组 merge：重叠→1 区、相接→合并/分离→2 区、包含→吞噬）；`.e2e/test-html-mask-smoke.js` **5 项**全过（合并接线 + 删除命中/闪烁/移除；4 个 okA 全部 `await` 消除并发共享态竞争）；后端 `python -m pytest backend/tests -q` **114 全过**（无回归）。
- ⚠️ **提交状态**：本轮改动 `tif_viewer/maskgen.js`（+142）、`tif_viewer/tif-viewer.html`（+83），连同先前未提交的 `CLAUDE.md` / `docs/README.md` / `current-question.md` 三文档，共 5 文件**未提交**；`.e2e/` 测试 gitignore 不入库。真机浏览器实测待内网（开发机 e2e 浏览器仍无法启动）。

## 5. 交接（给新窗口）

### 5.1 环境约束

- 开发机是**外网机**，真实遥感图全在**内网机盘阵**，无法导出/复制/读头探测。文件结构只能靠**用户回传 ENVI 头信息**（Edit Headers：Compression/Interleave）或**尺寸推断法**。不能要求用户给文件路径。
- 开发机 e2e 浏览器无法启动（puppeteer-core + 无头 Edge，Code: 0，环境问题，与代码改动无关）；浏览器单次分配 ~2GB、Canvas 面积上限 16384²、CDN 不可达（必须本地 vendor）。

### 5.2 下一步（给新窗口）

1. **等用户回传**（两种途径，用户二选一）：
   - 用当前 `tif_viewer/tif-viewer.html` 打开 (a) 400MB 小图、(b) 报解码失败的大图，把状态栏**完整报错文本**发来（含 `[属性 W×H，bits/spp，类型，压缩code]`）。
   - 或回传 ENVI 头信息：**Compression 字段** + 文件字节大小 + 宽×高 + 有没有 `.ovr`（用户已确认 Interleave=BSQ，单波段下不是瓶颈）。
2. 看 compression 编码：
   - 若为 1/5/8/32946（无/LZW/Deflate/旧 deflate）→ 不该失败，需进一步查。
   - 若为 7/34712/34925/50000（JPEG/JPEG2000/LZMA/ZSTD）→ 补解码器或换库。
3. 若 400MB 小图仍异常，对照 §3 经验核对数据流。

### 5.3 关键文件

- 主交付物：`tif_viewer/tif-viewer.html`（UTIF 小图 / geotiff 分块大图 / **稀疏条带预览** 三路分派）
- 经验文档：`docs/experience/gui-experience.md`
- E2E：`.e2e/test-sparse.js`（稀疏）、`test-regress.js`、`test-types.js`、`test-stretch.js`、`test-locator.js`（puppeteer-core + 无头 Edge）
- 测试图：`test-tifs/`、`test-tifs/types/`、`test-tifs/sparse/`
- 记忆：`~/.claude/projects/.../memory/MEMORY.md`（5 条索引，见 §2.6 注）
