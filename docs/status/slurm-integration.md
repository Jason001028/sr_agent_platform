# Slurm 真机接入 —— 交接快照与开工 prompt

> 日期：2026-09-10 · 状态：**已定**（决策已拍板，本文件供新窗口开工用）· 读者：接手的 Agent 窗口 + 运维本人
> 前置：`docs/sr_code/sr-pipeline-interface.md` v1.4（唯一调用契约）、`docs/status/real-machine-bringup.md`（真机部署现状）
> 用法：新窗口开在**开发机**，读 §一/§二，然后照 §三 的 prompt 逐批开工。真机动作用户在 node81-135 上执行。

## 一、决策快照（已拍板，勿再讨论）

| 事项 | 决议 |
|---|---|
| GPU 分配 | **交给 Slurm `--gres=gpu:1`**。生产脚本内所有 `CUDA_VISIBLE_DEVICES` 赋值必须删除；config.xml 的 `<GPUIDS>` 降级为审计字段，不再决定选卡 |
| 生产脚本 | **保留原文件 + 生成器产出部署变体**。`SR_code/code_0817_prod.py` 逐字节不动；变体由脚本按锚点替换表机械产出，附 `.diff` + provenance，锚点找不到即报错退出 |
| 契约三条件判定 | **落在作业内，用退出码表达**（`0` 契约满足 / `90` 契约不满足）。原定「平台调度层零改动」因账务关闭作废，见下一行 |
| 作业终态判定 | **C 方案**：`squeue` 判活跃态 + 作业把退出码写进盘阵、平台读文件判终态。真机实测 `AccountingStorageType=accounting_storage/none`（§2.4-2），`sacct` **永久不可用** —— 平台不得依赖 sacct；`backend/services/slurm.py` 的 `sacct_status` / `job_status` 须在 P2 一并改（2026-09-10 拍板） |
| 空 `suffix` 破坏性语义 | **本批不改**，只在契约文档标注风险；真机验收用非空 suffix 跑 |
| `backend/config.py` env 收敛 | 做，但**只新增 `SrRuntime`**，不复用/污染现有 `Config`（它只服务 agent loop） |
| `SR_SCENES_ROOT` 真根定位 | **范围外**。它是「提交 SR」按钮解灰的前置，与 Slurm 实现彼此独立；未解时用 curl 直接打 `/api/queue` 验收 |
| 平台调度层成功路径 | **不改** `_queue_state` 的签名与语义（`test_api_platform.py` 直接调它） |

## 二、已核实的事实（含行锚点，勿再重复调研）

### 2.1 「slurm 部分是空的」要重新定位

平台的 Slurm 调度层**已经完整且有测试**：`backend/services/slurm.py`（sbatch/squeue/sacct/scancel + `SR_SLURM_FAKE` 假调度器）、`backend/services/run_sr.py`（config.xml 组装 + 批脚本生成 + sha256 指纹幂等）、`backend/api/platform.py`（`/api/queue` REST + SSE）、前端 `/queue` 页。**真正空的是真机侧。**

### 2.2 生产脚本在 Slurm 作业里跑不通：三处硬伤 + 五处需改

| # | 行锚点 | 现状 | 问题 |
|---|---|---|---|
| E1 | `code_0817_prod.py:27` | `lib = npct.load_library("/DiskArray/.../tools/ImgHistMatch", ".")` | **模块级**、无 try/except → 缺 `.so` 时进程在 import 期崩，且崩在任何日志写出**之前**，报错无法定位。对照先例 `code_0820_prod_windows.py:25-28` |
| E2 | `:146` | `os.environ['CUDA_VISIBLE_DEVICES'] = "0"` | 无条件覆盖，抹掉 Slurm `--gres` 注入的分配结果 → `--gres=gpu:1` 的作业**全挤物理卡 0** |
| E3 | `:147` | `gpuid = '0'` | `gpuid` 用于日志与 `pynvml` 打印（**物理**索引，`util.py:1583`）。从 env 取值需 try 兜底，否则 UUID/MIG 形态下 `int(gpuid)`（`:624`）会崩 |
| E4 | `:644` | `os.environ['CUDA_VISIBLE_DEVICES'] = "1"` | 同 E2（在 `__main__` 里） |
| E5 | `:646-648` | `pynvml.nvmlInit(); nvmlDeviceGetCount()` | **nvml 数物理卡、无视 `CUDA_VISIBLE_DEVICES`**。4 卡节点上恒为 4，今天"看起来能过"纯属巧合 |
| E6 | `:653` | `if gpu_available is False or gpu_count != 4:` | 单卡分配下 `!= 4` 恒真 → **作业必失败**。对照 `code_0820_prod_windows.py:717`（`< 1`） |
| E7 | `:655-656` | `cmd = "systemctl stop slurmd.service"; os.system(cmd)` | 作业里停节点调度器。`User=nginx` 下是权限拒绝（作业白挂）；若将来服务改 root 则**真的把节点从池里摘掉** |
| E8 | `:660` | 写 `SlurmStopLog.txt` 时用 `"stop"` 措辞 | 不再停服务后，写 stop 会让运维误判节点已下线 |
| E9 | `:159-160` | `if cloudpercent > cloudlimit: exit(0)` | 合法「按策略跳过」，但在创建 SRLOG（`:165`）**之前** exit(0) → 与静默失败无法区分 |

### 2.3 闭环毒药：静默失败会被永久固化成成功

契约 §2 要求三条件（exit 0 **且** SRLOG 末行 `Run finished.` **且** 输出 tif 存在）；而 `backend/services/run_sr.py:118` 只看 sacct 的 `COMPLETED` 就判 `RESUMED_COMPLETED` **永久复用、永不重跑**。

生产路径上可达的 `exit(0)` 静默失败共 **5 条，全部发生在 SRLOG 创建（`code_0817_prod.py:165`）之前**：

| 位置 | 触发条件 | 现象 |
|---|---|---|
| `code_0817_prod.py:160` | `CloudPercent > CloudLimit` | 合法跳过，见 E9 |
| `util.py:999` | RC 步 `PAN.tif` 存在但体积不在 1.0–1.8GB 区间 | 打印 `already SRed before or the previous step was WRONG!` 后 exit(0) |
| `util.py:1002` | RC 步 `PAN.tif` 不存在 | 打印 `not existed` 后 exit(0) |
| `util.py:1014` | SC 步场景 tif 不存在 | 打印 `not existed` 后 exit(0) |
| `util.py:1106` | `get_cfg_value`：**xml 订单文件不存在** | 打印 `xml order file not exist.` 后 exit(0)。`code_0817_prod.py:117` 起即调用，config.xml 或 meta.xml 有误都会命中 |

另有 **1 条不是 exit 而是 `return`** 的陷阱：`util.py:1009-1011`（见 §2.4 第 3 条）。
另注：`util.py:1023-1047` 的 `get_l1_pan_tif_rcsc_nosr` 含 4 处 `exit()`，但**全仓库无调用者，是死代码**，不计入。

**连锁**：静默失败的作业 → sacct `COMPLETED` → `_resolve_existing` 判复用 → 闭环静默烂掉。这是本方案要解的核心问题。

### 2.4 本次新核实的三条（Plan agent 报出，已逐条查证）

1. **空 `suffix` 有破坏性语义**（`util.py:1300-1315`）：`DeleteOriTifNeeded=False` 时 `writeTiff` 会先 `os.rename(现有文件 → *_NOSR.tif)` 再在**同一路径**新建。`suffix` 为空 ⇒ 输出名 == 输入名 ⇒ **把输入产品改名**。而平台 `_norm_sr_params`（`platform.py:353`）默认 `suffix=""` —— 这条现在就在线上路径上。
2. ~~**`sacct` 解析在真机会错**~~ → **2026-09-10 真机定稿：`sacct` 根本没有输出可解析。** 真机 `AccountingStorageType=accounting_storage/none`，`sacct -a -X -o JobID,State -n` 打印 `Slurm accounting storage is disabled` 且 **rc=1**。所以原定的「改用 `--parsable2` 按 `|` 切」是**无效修法**（没有行可切）。
   真实影响链：`slurm.py:126-127` 在 rc≠0 时 `raise RuntimeError`，而 `job_status` 调它的那行（`:148`）**不在 try/except 内**（只有上面 squeue 那行包了）→ 作业一离开 `squeue`，`job_status` 必然抛错穿到 `sr_job_status`（`:39` catch 后返回 `err()`）与 `_resolve_existing`（`run_sr.py:111`，同参数重投**直接报错**，既不复用也不重跑）。不是「降级成 UNKNOWN」，是硬报错。
   **结论：平台不得依赖 sacct，按 §一「作业终态判定」的 C 方案改。**
3. **对已超分的场景会再超分一遍**（`util.py:1009-1011`）：SC 分支尺寸闸门不过时打印 `"already SRed before"` 然后 `return sc_file_abs_path` —— 后面的 `exit()` **被注释掉了**。重跑不是幂等跳过，是真重跑。

### 2.5 两个未经验证的关键假设（必须真机探针一票否决）

- ~~**`sacct` 依赖 SlurmDBD**~~ → **2026-09-10 真机否决，且比假设更糟**：不是「恒空 → `UNKNOWN` → `reuse: False` → 重复重投」，而是 `sacct` rc=1 → **`job_status` 直接抛异常**，`_resolve_existing` 与 `sr_job_status` 都拿不到任何结论。详见 §2.4-2 与 §一「作业终态判定」。
- **`nginx` 在 Slurm 侧有没有账户/关联**：`sr-api` 以 `User=nginx` 运行；无 association 会被拒或挂起。

### 2.6 其它已确认的坑

- **SRLOG 末行无尾换行**：`:630` 写的是 `"\nRun finished."`，且 `:624-626` 的 nvidia-smi 输出可能带尾换行 → **不能用 `readlines()[-1]`**，须尾读若干字节取最后一个非空行。
- **`SR_SLURM_WORK_DIR` 默认 `/tmp/sr_agent_work` 是本机路径**：多计算节点时 `-f {cfg}` 与 `--output` 在节点上不存在 → 作业秒挂。须确认节点数（`sinfo -N`）并指向共享盘。
- **两套权限都要核**：`nginx` 要能写 `SR_SLURM_WORK_DIR`（config.xml/脚本落这）与 `SR_AGENT_DB` 目录；**作业用户**要能写 `<lq_path>` 与 `<lq_path>/Debug/`。
- 仓库里**没有** `sr_pipeline_mock.py`（契约 §10 引用了它，但未入库）——别指望它。

### 2.7 P1 上机实测值（用户回传，2026-09-10 回填）

P2 起这些值就是 `deploy/sr-api.service` 与 `backend/config.SR_DEFAULT_*` 的依据。
标「**待核**」的行、以及下表中两处「P3 复核」**未经探针直接确认**——上机时逐条核，三条命令见
[slurm-acceptance.md](slurm-acceptance.md) §0.4；核完把本表状态改成「已核」。

| 项 | 实测值 | 来源 | 状态 |
|---|---|---|---|
| 主分区 | `gpu` | 2026-09-11 `sinfo -h -o "%P"` 实测：`centos7` / `deicc` / `gpu` / `gpu*` / `test`（`*`=默认分区）。**无 `gpup`** —— 早先那条「用户回传 `gpup`」是转述错误，已订正 | 已确认（订正） |
| 计算节点 | **76 个**（`gpu` 分区）：`node81-[129-162,165-183,185-189]` + `node104-[27-39,41-45]`；整个集群 `sinfo -N` 计 **96** 行；控制器 `Slurmctld(primary) at node81-190`。2026-09-11 探针实测 `node81-133/134/135/136` 为 `down`（**本机 node81-135 是 `down*`** —— 从本机提交的作业不会落回本机） | 探针 §A（`scontrol show partition -o` + `sinfo -N`） | 已核（2026-09-11 订正：原记「12 个：node81-129…140」是当时的局部读数，⇒ §2.6 共享盘那条**更加适用**；另见 slurm-acceptance §0.4 新增的「两族节点都要探」） |
| bundle 目录 | `/DiskArray/ProductionSchedule/exe_CentOS7/SR_bundle/mmsr_bundle/codes` | 用户回传写作 `exe/CentOS7`+`msnr_bundle`；**采用 `code_0817_prod.py:27` 的拼写**（源码优先于转述） | 已确认（P3 用 `ls` 复核一次拼写） |
| `SR_PYTHON` | `/run/media/root/SSD/program/anaconda/installed/envs/torch1.9.1py36/bin/python` | 用户 2026-09-10 明确：**沿用文档旧值，不用本机 base 环境 python**（`(base) [root@node81-135 ...]` 只是登录 shell 所在环境） | 已确认（P3 仍建议跑一次 `<该 python> -c "import torch, gdal"` 验证 torch1.9.1 + GDAL 齐全） |
| `SR_SLURM_WORK_DIR` | `/DiskArray/tmp/wangrz/sr_agent_work` | 本窗口选定（必须在共享盘上，默认 `/tmp` 不满足） | **待核**：目录是否已存在、nginx 与作业用户是否都有写权限 |
| 记账 | `AccountingStorageType=accounting_storage/none`，`sacct` 恒 rc=1 | 探针 | 已确认（§2.4-2） |
| GRES 注入形态 | `--gres=gpu:1` 给整数 `CUDA_VISIBLE_DEVICES`，但 `SLURM_JOB_GPUS` 未设、作业内仍见 4 卡 | 探针 `--deep` | 已确认（E3 兜底够用；并发可能都挤卡 0，见 §2.5） |

## 三、分批计划与开工 prompt

> **分工前提**：新窗口在**开发机**上工作，只能交付「开发机可验证」的产物 + 给用户的上机清单；node81-135 上的动作由用户执行并把输出贴回来。

### P1 · 首批：探针 + 生成器 + 变体（产完即可上机清零未知）

```text
本轮任务：Slurm 真机接入 · 第一批（只读探针 + 生产脚本部署变体）

先读 docs/status/slurm-integration.md（§一 决策快照、§二 已核实事实、§三 P1）。本窗口在开发机工作，
不碰 node81-135；所有真机动作用户执行，你负责产出脚本 + 上机清单。
再读 SR_code/code_0817_prod.py、SR_code/code_0820_prod_windows.py、docs/sr_code/sr-pipeline-interface.md。

已拍板、勿再讨论：GPU 分配交给 --gres=gpu:1；原文件逐字节不动、变体由生成器产出；
契约判定走作业内退出码（本批不做校验器）；空 suffix 语义本批不改。

交付物：
1. deploy/slurm/probe_slurm.sh —— 只读探针，纯 POSIX sh，不需要 root、不需要 jq。
   每项输出一行 `PROBE: <id> <OK|WARN|FAIL> <一句话>`，结尾给 HINTS（每条 FAIL 指向处理章节）。
   缺 Slurm 也不能中途退出——当前最大空白就是「Slurm 到底有没有」，必须收集完证据。
   覆盖：客户端/服务端二进制、systemctl 状态、munge 认证、scontrol ping、分区与限额、
   节点 GRES、GPU 登记数与 nvidia-smi -L 计数是否一致、关键 config 开关（SelectType/TaskPlugin/
   ProctrackType/GresTypes/AccountingStorageType）、sacct/slurmdbd 可用性 + 作业用户的 association、
   盘阵与 bundle 可见性、两套写权限、版本记录。
   另留 --deep：追加一次 2 分钟的 srun --gres=gpu:1 空作业，确认「真的给一张卡」与
   CUDA_VISIBLE_DEVICES 注入的是整数还是 UUID。默认不跑，必须显式开关。
2. SR_code/tools/gen_slurm_variant.py —— 锚点替换式生成器，纯标准库。
   EDITS = 有序列表 (edit_id, anchor 精确字符串含缩进, replacement, expected_count=1)；
   锚点命中数不符即打印详情并 exit 2，绝不静默跳过。读入时 CRLF→LF 归一，写出统一 \n。
   变体首部插入不可手改横幅：源路径 + 源 sha256 + 生成器版本 + "GENERATED FILE — DO NOT EDIT"。
   支持 --check（只比对，过期 exit 1）。provenance.json 不含时间戳，保证 --check 逐字节确定。
   落 SR_code/variants/：变体 .py + .diff（difflib.unified_diff）+ .provenance.json + .gitattributes（eol=lf）。
3. 变体的 EDIT 表按 §2.2 E1–E9 逐条落地。E3 是从 env 取 gpuid 并 int() 兜底；
   E9 是把云量跳过改成「先建 SRLOG 并写显式终态行，再 exit(0)」，让合法跳过与静默失败可区分。
4. backend/tests/test_sr_variant.py —— 用 importlib.util.spec_from_file_location 按路径加载生成器
   （SR_code/ 不是包，不要用 import 语句）。断言：--check 通过；每条锚点在真源中恰好命中 1 次；
   变体里 CUDA_VISIBLE_DEVICES 赋值 0 次、systemctl stop slurmd 0 次、gpu_count != 4 0 次；
   变体仍含 "Run finished." 与 exit(3)；连跑两次字节相同；故意喂坏锚点 → exit 2。
5. docs/sr_code/ 下一份变体差异表文档（不含时间戳；SR_code/ 内禁止放 .md）。
6. 给用户的上机清单：探针怎么跑、怎么看结论、每条 FAIL 怎么修、要回贴什么。

不要做：不要改 backend/ 下的调度层；不要改 SR_code/code_0817_prod.py；不要动前端；不要引入新依赖。

验收：python -m pytest backend/tests 全绿（现有 190 项 + 新增）；生成器 --check 通过；
在开发机跑一次 probe_slurm.sh，确认「无 Slurm 时输出 FAIL 但不崩、退出码非 0」。
```

### P2 · 第二批：批脚本加强 + 作业内校验器 + 配置收口（开发机全绿）

```text
本轮任务：Slurm 真机接入 · 第二批（批脚本加强 + 作业内契约校验 + 配置收口）

先读 docs/status/slurm-integration.md §二（尤其 2.3 闭环毒药、2.6 的 SRLOG 尾读与权限）
和 §三 P2，以及 P1 产出的变体差异表文档。把 P1 上机探针的实测值回填到下面的 <<CONFIRM>> 处。

交付物：
1. deploy/sr_bundle/verify_sr_run.py（或 SR_code/variants/ 下同名文件，位置与 P1 变体一致）
   —— 契约三条件校验器，纯标准库。从 config.xml 读 DatarootLQ / Suffix / OPT(tiftype)，
   复刻 code_0817_prod.py 的字面语义（`img_name[:-4]` 而非 Path.stem）。
   SRLOG 末行必须尾读若干字节取最后一个非空行比对 "Run finished."（§2.6 的坑），
   识别 P1 引入的 "Run skipped:" 终态行并判为合法跳过（只要求 SRLOG 存在、不要求输出 tif）。
   用 config.xml 的 mtime 过滤陈旧残留。退出 0=契约满足 / 90=契约不满足（stderr 指明缺哪条）。
   绝不抛异常：任何 IO/解析异常都要收敛成明确结论。
2. backend/services/run_sr.py 的 build_batch_script() 加强 —— 保持函数签名与现有字符串断言兼容：
   加 --time（env SR_SLURM_TIME，默认 02:00:00）、--cpus-per-task（SR_SLURM_CPUS，默认 4）、
   --job-name 带 suffix、--export=NONE、PYTHONUNBUFFERED=1；
   起始审计段 echo SLURM_JOB_ID / SLURM_JOB_GPUS / CUDA_VISIBLE_DEVICES / hostname / 解释器 / config 路径，
   并用 nvidia-smi --query-gpu=index,uuid 按 $CUDA_VISIBLE_DEVICES 过滤记下 UUID；
   cd 失败要显式 exit 1；python 之后调校验器并把退出码原样交出。
   脚本内不得出现任何 CUDA_VISIBLE_DEVICES 赋值。
   不要加 set -e（会与 || / 管道交互产生微妙行为）；保留 cd 而非 --chdir（现有测试断言 cd 那行）。
   --export=NONE 有切断真机 LD_LIBRARY_PATH 的风险，列为上机必验项并写明回退到 --export=ALL 是一行改动。
3. backend/services/slurm.py 状态层按 §一「作业终态判定」的 C 方案改（**真机账务关闭、sacct 永久不可用，非改不可**）：
   `squeue_status` 保留（不依赖账务，真机实测可用）用于活跃态；终态改为读作业写出的退出码文件，
   路径与第 1 项校验器的产物同源（落在 `<lq_path>/Debug/` 下，与 SRLOG 同目录）。
   `job_status` 必须**永不抛异常** —— 任何查询失败都收敛成明确结论，不得穿到 `_resolve_existing`
   （否则同参数重投直接报错，既不复用也不重跑）。假调度器（`SR_SLURM_FAKE=1`）路径不变，
   `test_slurm_fake.py` 必须仍全绿。
4. backend/config.py 新增 SrRuntime + sr_runtime()（只读快照，**不复用现有 Config**），
   集中 SR_BUNDLE_DIR/SR_PYTHON/SR_SLURM_WORK_DIR/SR_SLURM_PARTITION/SR_SLURM_GRES/SR_SLURM_TIME/
   SR_SLURM_CPUS/SR_SLURM_MEM/SR_SLURM_FAKE/SR_QUEUE_POLL_SEC/SR_AGENT_DB/SR_SCENES_ROOT。
   必须**调用时**读 env（现有测试在 setUp 里设 env）。config.py 不得 import services（避免循环依赖）。
   run_sr.py 保留三个 DEFAULT_* 模块常量，值改为指向 config.SR_DEFAULT_*。
5. deploy/sr-api.service 补齐 env（这是唯一「不加就绝对跑不起来」的部署改动）：
   SR_BUNDLE_DIR、SR_PYTHON、SR_SLURM_WORK_DIR、SR_SLURM_PARTITION、SR_SLURM_TIME、SR_SLURM_CPUS。
   注释里点明 SR_PYTHON 是 SR 生产环境（conda py3.6 / torch1.9.1），与 API 自身的 /opt/sr-venv（py3.9）
   是两个解释器；systemd 同一行不支持行尾 # 注释（历史 217/USER 事故根因）。
   SR_SLURM_WORK_DIR 若节点数 >1 必须指共享盘（§2.6）。
6. 测试：test_run_sr.py 扩批脚本断言与 env 生效；新增 test_sr_verify.py 覆盖三条件全分支
   （RC/SC 输入名推导、suffix 空/非空、SRLOG 末行各种形态含无尾换行与后随文本、tif 缺失/0 字节、
   exit_code "1:0"/"0:0"/None、云量跳过、meta 不可读、无权限不抛异常、SRLOG >4KB 时尾读仍正确）；
   新增 test_config.py 断言 sr_runtime() 默认值 == 旧硬编码常量（防回归）。

不要做：不要改 platform.py 的 _queue_state（签名与语义都不动，test_api_platform.py 直接调它）；
不要引入新依赖；不要改 SR_code/code_0817_prod.py。

验收：python -m pytest backend/tests 全绿；cd frontend && npm run test && npx vue-tsc --noEmit && npm run build 全绿。
```

### P3 · 第三批：真机端到端验收 + 文档回填

```text
本轮任务：Slurm 真机接入 · 第三批（真机验收 + 文档）

前置：用户已把 P1 探针输出与 P2 实测参数贴回。先读 docs/status/slurm-integration.md §二/§三，
以及 P1/P2 的产出。本窗口负责验收清单 + 文档回填，真机命令由用户执行、输出贴回后你判读。

交付物：
1. 分阶段验收清单（每步给可直接复制的命令 + 成功判据 + 失败怎么办）：
   A 探针 → B 裸 Slurm 冒烟（不碰平台）→ C 单场景真 SR（用非空 suffix 避开空 suffix 覆盖语义）
   → D 平台链路。
2. D 阶段必须覆盖的四条，缺一不可：
   - 静默失败不再被标成成功：构造一个必然缺 SRLOG 的作业，确认**退出码文件/校验器判定为契约不满足**、
     平台显示失败、**同参数重投能真的重跑而不是回 RESUMED_COMPLETED**（这是 §2.3 毒药的正面验证）。
     ⚠️ 原文写的是「确认 sacct 报 FAILED」——**账务关闭下这条无法执行**（sacct 什么都不返回），
     必须按 §一「作业终态判定」的 C 方案改成看退出码文件，判据别照抄 sacct。
   - 幂等回归：同参数重复提交 → 活跃态走 squeue 得 RESUMED_ACTIVE、终态走退出码文件得 RESUMED_COMPLETED；
     两次都不出现第二个 job_id
   - 云量跳过：cloud_limit 设到必触发 → COMPLETED、SRLOG 末行 "Run skipped:"、**且不被反复重投**
   - SSE：/api/queue/events 收到 job_update，前端队列页刷新
3. 文档回填（用实测值，不留占位）：
   - docs/sr_code/sr-pipeline-interface.md → v1.5：§2 补退出码 90 语义与云量跳过终态；
     §7.1 改为「部署变体已删除该守卫」并指向变体路径；§9 记录 --gres 决议、<GPUIDS> 降级为审计字段；
     补 §2.4 三条（空 suffix 覆盖、sacct 解析、对已超分场景会再超分）作为已知语义
   - deploy/README.md 增「Slurm 接入」小节，串起探针 / 变体 / 校验器 / service env
   - docs/status/current-question.md §3.2、§6.4 与 docs/status/real-machine-bringup.md §5 症状表同步
   - docs/README.md 索引登记本文件

不要做：不要把探针里 --deep 的提交动作写成默认开启；不要在文档里留 <<CONFIRM>> 占位。

验收：四条 D 阶段结论都有真实输出为证；文档里的路径与参数都能在真机上对上。
```

### P4 · 第四批：打通「提交 SR」全链路（半监督，跨真机）

```text
本轮任务：Slurm 真机接入 · 第四批（从「提交 SR」按钮到 Slurm 分配 + conda 环境超分，端到端打通）

前置（先读，别重复调研）：
- docs/planning/sr-pipeline-restore-plan.md —— 本轮的总清单（断点 B1–B6、阶段 0–6、决策点 ①②③）。
  本窗口照它执行，不再另起方案。
- docs/status/slurm-integration.md §一/§二（决策快照、已核实事实）与 docs/status/slurm-acceptance.md
  （真机验收 A–D 的分步命令 + 成功判据 + 失败处置）。真机命令直接复用后者，不要重写。
- backend/services/run_sr.py、backend/services/slurm.py、backend/api/platform.py、backend/api/paths.py、
  backend/services/scene_search.py、frontend/src/stores/queue.ts。
- 场景的判据以生产代码为准：SR_code/util.py 的 get_l1_pan_tif_rcsc / check_sr_previous_step。

分工（半监督，务必按这个节奏，别跳）：
- 你在开发机工作，只负责：改代码、跑本地测试、写文档、把真机命令整理成可整段粘贴的一段。
- node81-135 上的任何动作都由用户执行。你给完命令就**停下等用户贴回输出**，判读后再决定下一步。
  不要替用户猜输出，也不要跳过判读直接往下做。
- 凡是影响实现方向的取舍（方案二选一、某个实测值、某条判据存疑），用 AskUserQuestion 提问并**等待**，
  不要自己拍板。反复问没关系，问错了重做更贵。

已完成，不要重做：
- 上一批（部署变体 E1–E9 + 作业内校验器 + 平台 Slurm 层 + 探针 + 文档）已提交为 e06921d。
- E-A（SR_SR_SCRIPT 开关）已在开发机完成：run_sr.py 四处 + test_run_sr.py 四例，
  后端 287 项测试全过、变体 --check 通过；deploy/README.md §7.1、slurm-acceptance.md §0.2/§0.3、
  sr-pipeline-interface.md §7.1、current-question.md §6.4 已改成「变体并置 + env 指定」的装法。
  **不要退回「改名顶替 code_0817_prod.py」的旧写法。**

本轮要做的，按顺序：

第 0 步 · 三个决策点（先问，再动手）
用 AskUserQuestion 一次性问清计划 §0.2 的三项：预览 JPG 缓存落点、默认后缀取值、阶段 6 生产写权范围。
拿到答复之前，不要开始 E-B / E-E 的编码。

第 1 步 · 阶段 0 真机结构核查（给命令 → 等用户贴回 → 你判读）
把计划 §0.1 的 5 条只读命令整理成一段可整段粘贴的脚本交给用户，然后停下等输出。
判读要点：生产编号目录里到底有没有 <目录名>.tif（SC 步）或 PAN.tif（RC 步）、有没有 <目录名>_meta.xml、
有没有 Debug/ 子目录；层级是不是「年/月/日/生产编号」。
⚠️ 这是唯一能证伪「场景 = 生产编号目录」这条判据的动作。若实测不符，**先停下来说明**——
阶段 1 的 E-D 收件规则要按实测改写，不要硬套计划里的写法。

第 2 步 · 阶段 1 剩余代码改动（你自动完成，边做边测）
按计划 §E-B / §E-C / §E-D / §E-E 逐项实现，每项都要配测试：
- E-B 空后缀归一：后端白名单归一（platform.py 的 _norm_sr_params 与 tools/run_sr.py 共用同一个函数，
  两条入口必须同结果，否则幂等指纹会分叉）+ 掩码 draft 与前端默认值一起改。
  **必须做字符白名单**：suffix 会拼进输出文件名，`../` 之类就是路径穿越。
- E-C 变体 E10：云量字段解析容错，写进 gen_slurm_variant.py 的 EDITS，重新生成 + --check。
- E-D 检索：收件规则改为 p.stem == p.parent.name 且同目录存在 <目录名>_meta.xml（一条规则即排除
  _NOSR / _mask / _<suffix> 全部派生件）；再把它改成读服务器本地索引（索引落在 SR_SCENES_ROOT 之外）。
- E-E 预览缓存迁出盘阵（仅当决策点 ① 选本地盘；选盘阵内则跳过本项）。
每项做完跑 python -m pytest backend/tests -q，全绿再进下一项。实现取舍你可以自己定，
但**改了什么语义、为什么**要在回复里讲清楚。

第 3 步起 · 阶段 2 → 6，一段一段来
每一段都是同一个循环：你给命令 → 用户跑 → 贴回 → 你判读 → 给下一段。不要一次抛五个问题。
- 阶段 2 变体上机 + 八项 env + 重启自检 + 探针（**不带 --deep**）
- 阶段 3 裸 Slurm 冒烟 B1–B3（绕开平台，先证明地基）
- 阶段 4 走 /api/queue 提交一次真 SR（沙箱场景，非空 suffix）
- 阶段 5 链路四结论 D1–D4：静默失败不再被标成成功 / 幂等回归 / 云量跳过 / SSE，每条留真实输出
- 阶段 6 切生产（**仅在阶段 5 全绿之后**，并按决策点 ③ 的范围灰度，先只放开一个生产编号目录）
每段结束时给一个明确的「下一步等你什么」。

不要做：
- 不要覆盖 $SR_BUNDLE_DIR/code_0817_prod.py —— 变体并置、用 SR_SR_SCRIPT 指定；生产原文件必须字节不动。
- 不要在阶段 6 之前往生产盘阵写任何文件；阶段 0–5 一律用沙箱副本（cp -a 且**目录名不能改**）。
- 不要把探针 --deep 的提交动作写成默认开启。
- 不要在文档里留 <<CONFIRM>> / <占位> 这类没填的值；需要实测值就去要。
- 不要 patch SR_code/util.py 里那族 exit()（由作业内校验器判成 90 / FAILED 兜住，见计划 E-C 备注）。
- 不要相信 sacct 的任何输出（账务未启用，恒 rc=1）；终态一律读 Debug/_SREXIT_<job_id>.txt。

验收：
- 阶段 1 剩余四项落地且 pytest 全绿（基线 287，只增不减）；
- 阶段 0 的结构核查有真实输出，且 E-D 的收件规则与实测一致；
- 阶段 5 四条结论各有真实输出为证；
- 文档里的路径与参数都能在真机上对上，全部是实测值。

交付方式：每完成一段，回复只讲三件事——做成了什么、证据是什么（命令 + 输出）、下一步等你什么。
```

## 四、开发机可验 vs 必须真机验证

| 内容 | 开发机 | 真机 |
|---|---|---|
| 变体生成 / 锚点命中 / `--check` / 确定性 | ✅ | — |
| 校验器全部判定分支 | ✅（tmp 造产物） | 真实产物形态抽样 |
| 批脚本内容与 env 生效 | ✅（字符串断言） | 指令被真集群接受 |
| `--gres` 真给卡、`CUDA_VISIBLE_DEVICES` 注入形态 | ❌ | 探针 `--deep` |
| 分区名 / GRES 登记 / 记账 / cgroup / nginx association | ❌ | 探针 |
| 共享盘可见性、两套写权限 | ❌ | 探针 + 裸冒烟 |
| `sacct` 真实输出格式（§2.4 第 2 条）、`--export=NONE` 安全性 | ❌ | 探针 + 裸冒烟 |
| 空 suffix 覆盖语义 | ⚠️ 代码可推断 | 单独做破坏性验证（先备份） |

## 五、关联

- 契约：[docs/sr_code/sr-pipeline-interface.md](../sr_code/sr-pipeline-interface.md)
- 真机部署：[docs/status/real-machine-bringup.md](real-machine-bringup.md)
- 交接主入口：[docs/status/current-question.md](current-question.md)
- 代码：`SR_code/code_0817_prod.py` · `SR_code/util.py` · `backend/services/run_sr.py` · `backend/services/slurm.py` · `backend/api/platform.py` · `deploy/sr-api.service`
