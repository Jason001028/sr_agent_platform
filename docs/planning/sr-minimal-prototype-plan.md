# SR 最小原型实施计划（前端提交 → 本机 conda 环境直接执行）

日期：2026-09-14
状态：已定（决策见 §1.2，来源为 2026-09-14 与用户的问答）

> 读者：接手实现的新会话。本文不假设你有前几轮对话的上下文，所有前提在 §2 给出并标注核实方式。
> 工作方式见 §0，动手前必须先做 §3。

## 0. 工作方式（硬性）

+ 你在 Windows 开发机（仓库 `D:\BaiduNetdiskDownload\sr_agent_platform`）上写代码、跑本地测试、改文档。
+ node81-135（CentOS7 盘阵机）上的**任何操作都由用户执行**。你给出命令后停下等用户回贴输出，判读后再给下一条。
+ 一次只给一小段命令（1～3 行），不要一次给一大段探针。
+ 内网机传不出文件，所有需要看的输出要短到能截图后 OCR。
+ 读文档顺序：`CLAUDE.md` → `docs/status/current-question.md` → `docs/status/slurm-acceptance.md` → `deploy/README.md` §七。
+ 每段汇报按三段写：做成了什么 / 证据是什么（命令 + 输出）/ 下一步等用户什么。

## 1. 目标与已定决策

### 1.1 目标

前端点击「提交 SR」后，后端在 node81-135 本机用 SR 生产用的 conda 解释器直接执行 SR 代码，读取一个固定目录下的 `.tif` 与掩码，结果写回同一目录。不经过 Slurm。

最小原型范围（用户原话）：

> 「当前实现一个最小原型即可，只需要前端点击提交SR，这边打通conda环境跑对应目录下的掩码和.tif即可，不需要前后端传输完整的.tif，这些都可以写成规则式的，而且所生产的文件夹目录锁死在一个路径文件夹下，不需要考虑文件数的问题」

### 1.2 已定决策（2026-09-14 用户明确选择）

| 决策点 | 结论 |
| --- | --- |
| 执行器 | 不使用 Slurm，后端直接启动进程 |
| 掩码来源 | 用目录里已有的 `<目录名>_mask.tif`；前端不需要画掩码，「提交 SR」不再要求先画 |
| 提交步数 | 保留队列页确认：点「提交 SR」→ 跳转 `/queue` 预填 → 用户点提交才真正执行 |
| 浏览器端 JPG 导出 | 整条链路删除（含「输出目录：未授权」按钮与授权概念） |
| `.jpg` 预览 | 两项都要：「选择文件」能开本地 `.jpg`；盘阵目录里的 `.jpg` 能列出并直接打开 |

### 1.3 为什么不用 Slurm（2026-09-14 已在真机核实，不必重新论证）

+ node81-135 在集群里是 `gpu:4 down`，DOWN 节点不会被分配作业；要让它可调度就得改集群。
+ 自建单节点 Slurm 需要再起一份 slurmd：6818 端口已被集群的 slurmd 占用（`ss -lntp` 实测 `LISTEN 0 4096 *:6818 users:(("slurmd",pid=21147,fd=5))`），只能换端口并配独立 state/spool 目录与整份 conf。
+ 单卡分配不需要 Slurm：在执行进程的环境变量里设 `CUDA_VISIBLE_DEVICES` 即可。
+ 现有 `build_batch_script()` 生成的脚本本身就是合法 bash（`#SBATCH` 行对 bash 是注释）。把 `sbatch <script>` 换成 `bash <script>`，配置 XML 组装、审计段、契约校验器、退出码文件这一整套都能原样复用。

## 2. 事实基础（已核实）

+ 场景目录就是 SR 的 `DatarootLQ`：`backend/api/app.py:100` 给出的 `lq_path` 是场景文件的父目录；`backend/services/run_sr.py:154` 把它写成 `<DatarootLQ>`。
+ 掩码落盘位置：`backend/api/platform.py:536-546`，`out_dir / f"{stem}_mask.tif"`，即场景文件同目录。
+ 预览 JPG 位置：`backend/api/paths.py:118-135`，默认 `<源文件同目录>/<源文件 stem>.preview.jpg`。也就是说「JPG 与原图同目录」这条，后端本来就是对的。
+ SR 会改写入参目录：`SR_code/util.py:1312`、`:1355`、`:1424` 用 `os.rename(path + tiftype, path + "_NOSR" + tiftype)` 把输入改名；POSIX rename 会覆盖同名文件。锁定目录里已有一个 4.9GB 的 `_NOSR.tif`，再次运行会覆盖它。
+ SR 自己创建 `Debug/`：`SR_code/code_0817_prod.py:166` 有 `os.makedirs(..., exist_ok=True)`，不需要预先建目录。
+ 终态判定：`<DatarootLQ>/Debug/_SREXIT_<job_id>.txt`，由 `SR_code/variants/verify_sr_run.py` 写。`job_id` 取自命令行 `--job-id`，没有该参数时取 `$SLURM_JOB_ID`（`verify_sr_run.py:40`、`:394-418`）。集群账务未启用，`sacct` 永远不可用（恒 rc=1）。
+ 显卡：生产脚本 `SR_code/code_0817_prod.py:146` 与 `:644` 直接把 `CUDA_VISIBLE_DEVICES` 写成 `"0"` / `"1"`；变体 `SR_code/variants/code_0817_prod_slurm.py:161-166`、`:677` 改成读取环境变量。**所以本地执行必须把 `SR_SR_SCRIPT` 指向变体**，否则外部指定的显卡被覆盖。
+ 机器上 `$APP` 目前是旧版代码：`grep -c nodelist $APP/backend/services/run_sr.py` 返回 0，而开发机上该文件已有 `nodelist` 特性，说明开发机的 `backend/config.py` 与 `backend/services/run_sr.py` 还没拷过去。
+ 当前没有任何 suffix 校验：`backend/api/platform.py:375` 只做了 `str(body.get("suffix") or "")`。suffix 会拼进输出文件名，必须补字符白名单。
+ 锁定目录实测内容（2026-09-14，用户回贴的 `ls -l`）：`<目录名>.tif` 590MB、`<目录名>_meta.xml` 2722B、`<目录名>_mask.tif` 89MB、`<目录名>_mask.txt`、旧产物 `_NOSR.tif` 4.9GB、小写 `debug/`。

## 3. 动手前必须先确认的事

1. **锁定目录的拼写**。用户在 shell 里用的是 `/DiskArray/tmp/wangrz/databub/...` 且 `ls` 成功；问答里的答案写的是 `datahub`。两者只有一个存在。先让用户执行：

   ```bash
   ls -d /DiskArray/tmp/wangrz/datahub /DiskArray/tmp/wangrz/databub
   ```

   以实际存在的那个为准，后面所有硬编码都用它。

2. **作业实际运行的是哪个 SR 脚本**。本原型要求 `SR_SR_SCRIPT` 指向变体（见 §2）。先看现状：

   ```bash
   systemctl cat sr-api | grep -n "SR_SR_SCRIPT\|SR_PYTHON\|SR_BUNDLE_DIR\|SR_SCENES_ROOT\|SR_SANDBOX_ROOT\|SR_SLURM_WORK_DIR"
   ```

   若 `SR_SR_SCRIPT` 为空或指向 `code_0817_prod.py`，本阶段要改成变体。同时确认 `$SR_BUNDLE_DIR/code_0817_prod.py` 没有被覆盖过（生产文件是只读基准，不允许改）。

3. **前端产物怎么送到机器上**。现状是 nginx 提供 `frontend/dist`。先确认用户现有的拷贝通道（共享目录 / U 盘 / 其他），再决定前端验收怎么做。若一时没有通道，先只做 §5.1 的后端验收，前端改动照样提交到仓库。

## 4. 改动清单

### 4.0 实施顺序

先做完 §3 的三项确认，再按下面顺序推进。每项做完跑一次 §5.1 的本地测试。

| 序号 | 任务 | 对应章节 | 完成判据 |
| --- | --- | --- | --- |
| 1 | 读完后端执行 / 状态链路（`run_sr.py` 提交段、`store.py`、`platform.py` 队列端点） | §2 | 能说清 `submit_run_sr` → `sbatch` → `job_status` 的三段数据流，以及 `sr_tasks` 表怎么保证不重复提交 |
| 2 | 执行器切换：后端用 `$SR_PYTHON` 直接起进程，不再 `sbatch` | §4.1、§4.2 | 本地模式下 `POST /api/queue` 能起进程，状态走 PENDING → RUNNING → 终态 |
| 3 | 路径锁死：提交 SR 的 `lq_path` / `mask_path` 由场景目录推导，不接受手填 | §4.3、§4.7 | `SR_LOCKED_DIR` 不匹配返回 400；前端两个输入框只读 |
| 4 | 掩码来源改为目录里已有的 `<名字>_mask.tif`，取消「必须先画掩码」门禁 | §4.3、§4.7 | 未画掩码也能点「提交 SR」；缺掩码文件时返回 400 |
| 5 | 前端：删「输出目录」按钮 + 自动JPG + 浏览器 JPG 导出链路（`saver` / `exportJpg`） | §4.5 | 页面上不再出现「授权」相关字样；`npm run typecheck` 通过 |
| 6 | 前端：放宽文件过滤，支持本地 `.jpg` 打开预览 | §4.6 | 「选择文件」能选 `.jpg` 并正常显示 |
| 7 | 盘阵目录里的 `.jpg` 能列出并直接打开（后端 scenes + 前端） | §4.4 | 场景列表出现 jpg 行且点「打开」能显示 |
| 8 | 跑测试：backend pytest + frontend vitest 保持全绿 | §5.1 | `python -m pytest backend/tests -q` 全绿；`npm test` 全绿 |

顺序说明：

+ 第 2、3、4 项是本次的核心，做完就已经满足「前端点击提交 SR → 本机 conda 环境跑对应目录的掩码和 .tif」。真机验收可以在第 4 项之后单独做（§5.2 的 A 段），不必等前端三项。
+ 第 5、6、7 项互不依赖，可以并行。
+ 第 8 项是收尾，不是最后才开始跑——每完成一项都要跑一次。

### 4.1 后端：本地执行器（新增 `backend/services/local_exec.py`）

对外接口与 `backend/services/slurm.py` 对齐，便于 `run_sr` 按开关二选一：

```python
def available() -> bool
def submit(script_path, env=None) -> int                      # 返回 job_id
def status(job_id, exit_code_file=None) -> dict               # {"job_id","active","state","exit_code"}
def cancel(job_id) -> bool
```

实现要点：

+ **单槽串行队列**：同一时刻只跑一个作业。用模块级 `threading.Lock` 当槽位；`submit()` 记录 job_id 与日志路径后启动一个 daemon 线程，线程先抢槽位锁，再 fork 子进程，`proc.wait()` 结束后释放锁。排队中的作业 `status()` 返回 `{"active": True, "state": "PENDING"}`。
+ **job_id**：单调递增整数，持久化在 `<SR_SLURM_WORK_DIR>/.local_job_seq`（先读、加一、写回，用同一把锁保护），保证 sr-api 重启后不重复。job_id 唯一是为了让 `_SREXIT_<job_id>.txt` 不会串号。
+ **子进程**：`subprocess.Popen(["bash", str(script_path)], stdout=log_fp, stderr=subprocess.STDOUT, start_new_session=True, env=child_env, cwd=...)`。日志写到 `<SR_SLURM_WORK_DIR>/<script stem>.<job_id>.out`。
+ **子进程环境**：从 `os.environ` 复制后做四件事，其余原样保留（SR 的 torch/GDAL 依赖系统库，`LD_LIBRARY_PATH` 不要动）：
   1. 删除 `VIRTUAL_ENV`、`PYTHONHOME`、`PYTHONPATH`——sr-api 自己跑在 `/opt/sr-venv`（py3.9），这些变量漏进 conda py3.6 解释器会导入错版本的库。
   2. `PATH` 前置 `os.path.dirname(rt.python)`——等价于在 shell 里激活那个 conda 环境。
   3. 设 `CUDA_VISIBLE_DEVICES`（值取 `SR_LOCAL_GPU`，默认 `0`）。
   4. 设 `SR_EXECUTOR=local`，供脚本审计段打印。
+ **状态**：先看内存里的记录。有进程且 `poll() is None` → `{"active": True, "state": "RUNNING"}`；尚无进程 → `PENDING`；否则读退出码文件得终态（复用 `slurm.read_exit_code_file` + 终态映射）。内存里没有记录（sr-api 重启过）也要能靠退出码文件回答，读不到就是 `UNKNOWN`。
+ **终态映射复用**：把 `backend/services/slurm.py:232` 的 `_terminal_from_exit_file()` 提升为公开名 `terminal_from_exit_file()`（保留 `_terminal_from_exit_file = terminal_from_exit_file` 别名，避免动已有测试），由 `local_exec` 调用。不要在 `local_exec` 里重写一遍这段映射——它和 `verify_sr_run.py` 的退出码契约是一对，散成两份以后会不一致。
+ **cancel**：未启动的作业标记为已取消，工作线程跳过；已启动的对进程组发 `SIGTERM`（`os.killpg(os.getpgid(pid), signal.SIGTERM)`，因为用了 `start_new_session=True`）。

### 4.2 后端：`run_sr` 接入开关

+ `backend/config.py` 的 `SrRuntime`（`:116-179`）增加字段：`executor`（`SR_EXECUTOR`，默认 `"slurm"`）、`locked_dir`（`SR_LOCKED_DIR`，默认 `None`）、`local_gpu`（`SR_LOCAL_GPU`，默认 `0`）、`suffix_default`（`SR_SUFFIX_DEFAULT`，默认 `"sr"`）。
+ `backend/services/run_sr.py`：
  + `build_batch_script()`（`:170-325`）增加参数 `job_id=None`。给了 job_id 时，把最后一行校验器调用改成 `... --sr-exit-code "$_sr_rc" --job-id <job_id>`，这样本地执行不依赖 `$SLURM_JOB_ID`。审计段加一行 `echo "SR_EXECUTOR=${SR_EXECUTOR:-slurm}"`。
  + `submit_run_sr()`（`:429-504`）：按 `rt.executor` 选择 `slurm.sbatch_submit` 或 `local_exec.submit`；`available()` 的判据同理由 `slurm.slurm_available()` 换成对应执行器（本地模式恒为 True）。
  + `query_job_status()`（`:375-389`）：本地模式下走 `local_exec.status`。
  + 本地模式下强制关闭沙箱：`SR_SANDBOX_ROOT` 不参与。原因是沙箱会把 `DatarootLQ` 指向 `<sandbox>/<key>/<目录名>`，SR 的结果就写到那里，直接违背「输出与原图同目录」这条需求。锁定目录本身是 `/DiskArray/tmp/wangrz/` 下的试验目录，可以接受就地写入（但要在 API 响应与日志里把「输出目录 = 输入目录，会覆盖已有 `_NOSR.tif`」写清楚）。

### 4.3 后端：路径锁死与掩码规则（`backend/api/platform.py` 的 `POST /api/queue`）

+ `SR_LOCKED_DIR` 设置时：请求里的 `lq_path` 必须与它相等（两侧都做「去尾部 `/` + `os.path.realpath`」后比较），否则 400。`SR_LOCKED_DIR` 未设时保持现行为。
+ `mask_path` 缺省规则：请求里没给 `mask_path` 时，若 `<lq_path>/<basename(lq_path)>_mask.tif` 存在就自动用它；不存在则 400，提示该目录缺少掩码文件（不要静默退化成全图超分，用户会以为掩码生效了）。
+ `suffix` 校验：加白名单 `^[A-Za-z0-9_-]{1,16}$`，不满足 400。缺省值取 `rt.suffix_default`（默认 `"sr"`），不要再用空串——空后缀会让输出名与输入名相同，属于破坏性配置。
+ 这三条都要有测试。

### 4.4 后端：盘阵目录里的 `.jpg` 能列出（`backend/services/scene_search.py` + `backend/api/app.py`）

+ 扫描后缀加入 `.jpg` / `.jpeg`（与现有的 `.tif` 并列）。
+ `backend/api/app.py:65-105` 的行补全：源文件本身就是 jpg 时，`hasPreview = True`、`jpgUrl` 指向它自己（`/disk-array/<rel>`），不要调用 `ensure_preview_jpg`。W/H 用 Pillow 读（`Image.open(p).size`），不要走 TIF 头解析分支。
+ 保持 `lq_path` 只给父目录这条既有约定（`backend/tests/test_api.py:135-139` 已断言不外泄文件名）。

### 4.5 前端：删除浏览器端 JPG 导出链路

删除范围：

+ 删 `frontend/src/lib/exportJpg.ts` 与 `frontend/src/lib/__tests__/exportJpg.test.ts`。
+ `frontend/src/lib/saver.ts`：删 FS Access 授权相关的全部导出（`fsIO`、`getSaver`、`setSaverOverride`、`setOutDirListener`、`getOutDirState`、`OutDirState`、`Saver`、IndexedDB 句柄读写）。**保留 `downloadBlob`**——「生成掩码」下载 `mask.tif` / `mask.txt` 还在用它，它不涉及授权。
+ `frontend/src/stores/viewer.ts`：删 `outDir` / `autoExport` / `exportQueue` / `exportingNow` 状态，删 `kickExport` / `reExportJpg` / `scanPendingExports` / `pumpExport` / `doExportJob` / `setJpgStatus` / `initFsIO` / `authorizeOutDir`；`ViewerRec`（`:45-78`）删 `_jpgBusy` / `_jpgDone` / `_jpgToken` / `_exportCap` / `jpgStatus` / `jpgCls`；`applyDecoded`（`:348-369`）里删 `kickExport(rec)` 调用；`openSceneJpg`（`:387-422`）里对应字段一并删。
+ `frontend/src/components/Toolbar.vue`：删「输出目录」按钮（`:99-107`）、「自动JPG」勾选（`:109-112`）及相关的 computed（`:51-67`）与样式（`.outbtn.granted` / `.outbtn.pending`）。
+ `frontend/src/components/FileList.vue`：删 JPG 状态行（`:52-58`）与「重新导出」点击分支（`:27`）。
+ `frontend/src/pages/ViewerPage.vue`：删 `store.initFsIO()`（`:25`）。
+ `frontend/src/viewer/e2eHooks.ts`：删与 saver / exportJpg 相关的钩子与类型。
+ 跑 `npm run typecheck` 与 `npm test`，把因此失败的测试一并清理（删的测试要说明删因）。

### 4.6 前端：本地 `.jpg` 预览

+ `frontend/src/stores/viewer.ts` 的 `addFiles()`（`:263-275`）：过滤放宽成 `.tif` / `.tiff` / `.jpg` / `.jpeg`（含对应 MIME）。提示文案里的「没有识别到 tif/tiff 文件」相应改写。
+ 新增解码分支：非 tif 的图片文件用 `createImageBitmap()` → 画布 → `sceneDecodePixels()`（`frontend/src/lib/scene.ts:146-153`），得到与盘阵场景同构的 rec：`route = 'jpg'`、`sceneId = null`、`lqPath = null`、`W/H` 用图片真实尺寸。已有的 `decodeJpgToCanvas()`（`:91-102`）可以直接复用。
+ `submitSr()` 里已有「`route !== 'jpg' || !sceneId` → 拒绝」的判断，本地 jpg 因为 `sceneId` 为 null 会被正确拒绝，不需要额外处理。
+ `frontend/src/components/Toolbar.vue`：`accept` 增加 `.jpg,.jpeg,.JPG,.JPEG`，按钮文案从「选择 TIF…」改为「选择影像…」。

### 4.7 前端：提交 SR 改造

+ `frontend/src/stores/viewer.ts` 的 `submitSr()`（`:847-883`）：不再调用 `apiBakeMask`，直接根据当前场景推导草稿并跳转队列页：
  + `lq_path = rec.lqPath`（为空则报错：「此图无盘阵目录，无法提交 SR」）；
  + `mask_path = <lqPath>/<rec.name>_mask.tif`；
  + `suffix` 用非空默认值；
  + 其余参数（`sr_scale` / `gpu` / `cloud_limit` / `delete_ori` / `grid_align`）保留现有默认。
+ `frontend/src/components/Toolbar.vue` 的 `srReady`（`:19-24`）：条件从「已打开盘阵场景 + 已画掩码」改为「已打开盘阵场景且 `lqPath` 非空」，去掉掩码要求。
+ `frontend/src/pages/QueuePage.vue` 的提交表单（`:109-153`）：`lq_path` 与 `mask_path` 两个输入框改为只读展示（不可编辑），并标注「由场景目录推导」。其余参数保持可编辑。
+ `frontend/src/stores/queue.ts` 的 `defaultForm()` / `draftToForm()`（`:44-58`）：默认 suffix 改为非空值，与后端默认一致。
+ 若 `apiBakeMask`（`frontend/src/lib/api.ts:302-311`）在改动后不再有调用方，连同 `BakeMaskBody` / `MaskBakeResult` / `MaskDraft` 类型一起删除；后端 `POST /api/masks`（`backend/api/platform.py:493-551`）本轮**不删**（有测试覆盖，且未来可能回到「前端画掩码」），但在文档里记一句「当前前端已不再调用」。

## 5. 验证

### 5.1 开发机（先跑）

+ `python -m pytest backend/tests -q` 保持全绿（当前基线 309 passed，2026-09-14 实测）。
+ 新增测试至少覆盖：`SR_LOCKED_DIR` 不匹配时 400；`mask_path` 自动推导命中与缺失两种情况；`suffix` 白名单拒绝与接受；`local_exec` 的 PENDING → RUNNING → 终态（用短脚本 + 退出码文件模拟，不依赖真机）；`build_batch_script(job_id=...)` 生成文本里出现 `--job-id`。
+ 前端：先 `export PATH="/c/Users/lenovo/AppData/Local/nvm/v20.19.5:$PATH"`，再 `npm run typecheck` 与 `npm test`。

### 5.2 真机（用户执行）

分两段验收，前端没打通也不影响后端结论。

#### A 段：只验后端 API

1. 拷 `backend/config.py`、`backend/services/run_sr.py`、`backend/services/local_exec.py`、`backend/services/scene_search.py`、`backend/api/platform.py`、`backend/api/app.py` 到 `$APP` 对应位置。
2. `systemctl cat sr-api` 看清现状，再按 §6 追加环境变量，`systemctl restart sr-api`。
3. 用 `curl` 提交一次作业（`lq_path` 传锁定目录、不传 `mask_path`），确认返回 `task_id` / `job_id`。
4. 看状态推进：`GET /api/queue` 里该行从 PENDING → RUNNING → COMPLETED。
5. 作业结束后让用户回贴两样短输出：
   + `ls -l <锁定目录>/Debug/`（应出现 `_SREXIT_<job_id>.txt` 与 `*_SRLOG.txt`）
   + `cat <锁定目录>/Debug/_SREXIT_<job_id>.txt`（应 `verdict=0`）
6. 确认输出确实落在锁定目录（需求「输出与原图同目录」）。

**A 段执行结果（2026-09-15）**

| 步 | 结果 |
| --- | --- |
| 1–2 部署 + env | 通过：三项 env（`SR_EXECUTOR`/`SR_LOCAL_GPU`/`SR_LOCKED_DIR`）生效 |
| 3 提交 | 通过：`201`，`task_id=1` / `job_id=1` / `in_place:true`；`mask_path` 按目录推导正确 |
| 4 状态推进 | 通过：真的推进了（提交 → RUNNING → 终态），不是假调度器；但平台侧未收敛到 FAILED（见第 5 步） |
| 5 终态 | 契约判 FAILED：`_SREXIT_1.txt` 写出非 0 verdict，理由 `SRLOG 早于 config.xml, 是上次残留` |
| 6 产物落盘 | 未产出——SR 未执行超分（见下） |

**根因**：场景 `JL1KF02B03_PMS09_…_L1_PAN` 在 **9/12 就已超分过**，
`util.py:1003-1011` 的 SC 分支发现 `<目录名>.tif` 体积不在三个接受区间内 → 打印
`already SRed before` → **`return`（`exit()` 被注释掉，源码标 `# huai`）**，既不做处理、也不建新
SRLOG。校验器随后判定 `Debug/_SRLOG.txt`（9/12 的）是上次残留、不计入本次成果 → 契约不满足。
若沿用 sacct 判据，这次会被标成 COMPLETED 并固化。

**待核**：作业终态已由 `_SREXIT_1.txt` 判为 FAILED，但 `GET /api/queue` 两次复核均报 `RUNNING`。
`local_exec.status()`（`backend/services/local_exec.py:257-271`）只在本地子进程仍存活时返回 RUNNING，
且此时不读退出码文件——需先分清进程是否真的还在。

**顺带被证实（审计段）**：`SR_SR_SCRIPT=code_0817_prod_slurm.py`（变体在用，`CUDA_VISIBLE_DEVICES`
没被原脚本覆盖）、`CUDA_VISIBLE_DEVICES=0` 且绑到真实 GPU UUID、conda 解释器确实启动
（跑到 SC 分支判断 + nvml + SolarAzimuth）。**「前端提交 → 后端用 conda 解释器跑 SR」这条链已验证，
缺的只是让 SR 干活的输入。**

**下一步**：造一个未超分过的场景。二选一——① 换一个 `<目录名>.tif` 体积落在
`0–1.1` / `1.5–1.7` / `3.8–4.1` GB 区间内的场景目录；② 把本目录的 `<目录名>_NOSR.tif`
（= 9/12 超分前的原始输入）改名还原回 `<目录名>.tif` ——**必须先备份现存的超分产物**（rename 会覆盖）。

#### B 段：前端点击验收

> **前置（09-15 定）**：`SR_SCENES_ROOT` 必须配，且与 nginx `alias` 同值。原因：`/scenes` 盘阵场景页
> 的行来自 `/api/scenes`，查看器的「提交 SR」按钮只对 `route='jpg' && lqPath` 的记录可用，
> 而 `lqPath` 只由 disk 场景行提供——**场景列表是那个按钮的唯一入口**。
> **本阶段不维护场景检索**：根暂指 `/DiskArray/tmp/wangrz/datahub/`，接受同一景被列成多行
> （收件规则只有后缀白名单、派生件也计入，缺口与后续修法见 `current-question.md §6.0` 下一步①）。

1. 构建 `frontend/dist` 并按 §3.3 确认的通道送到机器，刷新页面。
2. 盘阵场景页打开锁定目录的场景 → 点「提交 SR」→ 队列页确认 → 提交。
3. 观察任务行状态与最终结果，与 A 段同样的两样输出对照。

## 6. 真机上要设置的环境变量

在 sr-api 的 systemd drop-in 里追加（以 `systemctl cat sr-api` 的实际输出为准，不要凭文件名猜）：

```ini
Environment=SR_EXECUTOR=local
Environment=SR_LOCKED_DIR=<§3.1 确认后的锁定目录>
Environment=SR_LOCAL_GPU=0
Environment=SR_SUFFIX_DEFAULT=sr
Environment=SR_SR_SCRIPT=code_0817_prod_slurm.py
Environment=SR_PYTHON=<conda 环境的 python 绝对路径>
```

要点：

+ `SR_SANDBOX_ROOT` 必须**不设**（见 §4.2 最后一条）。
+ `SR_SR_SCRIPT` 指到变体（见 §2 的显卡条目）。
+ `SR_LOCKED_DIR` 一旦设置，`/api/queue` 就只接受这一个目录。
+ 每一项改完都要 `systemctl restart sr-api`。

## 7. 约束与红线

+ 不覆盖 `$SR_BUNDLE_DIR/code_0817_prod.py`。变体与它并置，靠 `SR_SR_SCRIPT` 指过去。
+ 不修改 `SR_code/util.py` 里 `exit()` 那一族逻辑。
+ 不往生产盘阵写文件。本原型的写入目标是 `/DiskArray/tmp/wangrz/` 下的试验目录。
+ 不要用 `sacct` 判终态（账务未启用，恒 rc=1），终态一律读 `Debug/_SREXIT_<job_id>.txt`。
+ 文档里不留 `<<CONFIRM>>`、`<占位>` 这类没填的值。
+ 运行 SR 会覆盖锁定目录里已有的 `_NOSR.tif`（4.9GB）。这是已知且接受的代价，但每次真跑之前要说一次。
+ 后端测试基线 309 passed，任何改动后都要保持全绿。
