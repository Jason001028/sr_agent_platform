# 本机执行 SR —— 现状、切换与回滚

> 日期：2026-10-15 · 状态：已定 · 读者：接手 Agent 窗口 + 运维
> 用途：本平台**唯一**的 SR 执行路线（后台用 SR 生产解释器在 sr-api 所在机器上直接起进程）的
> 现状与运行期规则，以及切换/回滚步骤。部署怎么装见 [deploy/README.md](../../deploy/README.md) §七，
> 脚本差异见 [sr-platform-variant.md](../sr_code/sr-platform-variant.md)，
> 调用契约见 [sr-pipeline-interface.md](../sr_code/sr-pipeline-interface.md)。
> 真机勾选项见 [real-machine-acceptance.md](real-machine-acceptance.md) §D。

## 一、链路

```text
/queue 或 /chat 提交
   → 任务服务（backend/services/run_sr.py）：参数归一化、掩码推导、指纹、幂等、取消
   → 本机执行器（backend/services/local_exec.py）：单槽串行、起 bash 子进程、日志、进程树管理
   → 运行脚本：就位未超分那份 → SR 脚本 → 契约校验器（判定 + 归位）
   → 结果文件（<lq_path>/Debug/_SREXIT_<job_id>.txt）+ SQLite 任务行
   → SSE（/api/queue/events）→ 前端队列页
```

执行者是 sr-api 自己：**没有调度器、没有作业队列系统、没有共享盘要求**。作业在盘阵上**就地**跑
—— 用户填的 `lq_path` 就是会被写入的目录（产物落 `<输入 stem>_<suffix>.tif`，随后归位到本体名）。

## 二、运行期规则

### 2.1 单进程、单槽

- 部署必须是**单 API 进程**（`deploy/sr-api.service` 注释里写明不设 `--workers`）：队列状态轮询、
  预览清理、产物预览主动生成是 lifespan 后台循环，多进程各做一遍；而执行槽位是
  `<SR_WORK_DIR>/.exec.lock` 这一个**跨进程**文件锁，第二个进程的作业只会一直排队。
- 同一时刻只跑一个 SR 作业。锁在**子进程实际存活期内**持有，记录里带启动它的 api pid/身份与
  子进程 pid/身份（按进程创建时间戳核对）；API 进程消失而子进程仍在时，锁不会被当成失效而放行。

### 2.2 每次运行的落盘物（都在 `SR_WORK_DIR`）

| 文件 | 内容 |
|---|---|
| `run_sr_<suffix>_<指纹前 12 位>_<job_id>.xml` | 这一跑的 config.xml |
| `run_sr_<suffix>_<指纹前 12 位>_<job_id>.sh` | 这一跑的运行脚本 |
| `<脚本名>.out` | 子进程的 stdout/stderr（日志） |
| `runs/<job_id>.json` | 运行记录：状态、退出结局、子进程 pid/身份、日志路径 |
| `.local_job_seq` | 运行编号计数器 |
| `.exec.lock` | 跨进程执行槽位锁 |

文件名带 suffix + 指纹 + 运行编号三者：只有 suffix 会跨场景撞，只有指纹会让同一次提交的重跑
覆盖在跑那一次的 config。

### 2.3 终态只看结果文件

```
<lq_path>/Debug/_SREXIT_<job_id>.txt     job_id= / sr_exit_code= / verdict=0|90 / skip=0|1 / reason=
```

- `verdict=0` → COMPLETED；`verdict!=0` → FAILED；`skip=1`（云量策略跳过）→ COMPLETED 且不出产物。
- **子进程退出码 0 不等于成功**：契约三条件（退出码 0 + SRLOG 末行 `Run finished.` + 输出 tif）由
  校验器在作业内判，不满足时校验器自己以退出码 90 结束并写 `verdict=90`。
- 路径由约定锁死：校验器写它、`run_sr.exit_code_file_for()` 从 config.xml 反推它、
  `local_exec.status()` 读它，两侧一致性由测试钉住。运行编号由 `--job-id` 显式传给校验器。

### 2.4 重启、未知与取消

进程内存记录丢失（sr-api 重启）时，答案来自两处落盘事实：运行记录 + 结果文件。

| 情形 | 报告 | 能否重投 |
|---|---|---|
| 该 run 的子进程仍在（身份核对过） | RUNNING | — |
| 排队中（记录里没有子进程 pid） | FAILED（注明「排队中被 sr-api 重启打断，没有启动」） | 可以，直接重投 |
| 启动失败（Popen 抛错） | FAILED（附异常类型与说明） | 可以 |
| 子进程已退出、没有结果文件 | FAILED（无有效产物） | 可以 |
| 记录停在「正在启动」而没有任何子进程 pid | UNKNOWN，`active=None` | **不可以**：可能已经起了 |
| 该编号在本机毫无痕迹（没有运行记录也没有结果文件） | UNKNOWN | 同上 |
| 取消 / 操作员释放 | CANCELLED | 可以 |

- **UNKNOWN 不是「可以重跑」**：同参数重投会以 409 挡下（`SubmitConflict`，`kind="unknown"`），
  先按提示 `ps -ef | grep <脚本名>` 核实。
- 操作员核实无残留进程后，用取消接口带 `{"confirmed_stopped": true}` 释放该编号（转 CANCELLED），
  重投才解锁。这是唯一的人工出口，平台不替操作员判定。
- 取消是**停止完整进程树**（POSIX 下子进程自建 session，先 TERM 后 KILL）；确认停下才算停，
  停不下来的按上面那行如实报告，不释放执行槽。
- `systemctl stop sr-api` 会连正在跑的 SR 一起结束（`KillMode=control-group`，子进程不脱离 cgroup）
  —— 作业不会脱离 API 独自留在机器上，那种局面平台观察不到。

### 2.5 历史任务行

任务表里更早的、由调度器路线的编号留下的行：库里已记终态、而本机对这个编号毫无痕迹的，
队列页**按库里记的终态显示**，不改写成 UNKNOWN；本机记着它仍在活动（库里是 RUNNING 一类）的，
按 UNKNOWN 显示并挡重投。重投判定只看执行器给的状态，不看库里那一列。

## 三、切换

前提：**没有活动作业**。切换前在旧版本上确认（有活动作业就等它跑完，或由操作员处理）：

```bash
systemctl status sr-api --no-pager | head -5      # 服务当前状态
ps -ef | grep -E 'code_0817|verify_sr_run|sr_prep_scene' | grep -v grep   # 没有残留 SR 进程
curl -s localhost/api/queue | head -c 2000        # 队列里没有 PENDING/RUNNING 的行
```

步骤：

1. **备份**：`SR_AGENT_DB` 那个 sqlite 文件（任务行与历史都在里面）与 `/etc/systemd/system/sr-api.service.d/`
   整个目录。数据库**增量迁移**，不重建、不清表。
2. **清遗留 drop-in**：`srscript.conf` 一类旧配置目录里的 `SR_SLURM_*` / `SR_SANDBOX_ROOT` /
   `SR_SLURM_FAKE` 一律删掉 —— 平台不读这几个名字，留着只会让人以为配过了。
3. **上车三份脚本**：`code_0817_prod_platform.py` + `verify_sr_run.py` + `sr_prep_scene.py`
   **并置**在 `$SR_BUNDLE_DIR`（不覆盖 `code_0817_prod.py`），见 [deploy/README.md](../../deploy/README.md) §7.1。
   三份必须同一次上车：校验器是旧版就认不得 `--job-id`，作业会以 argparse 退 2 直接结束、不写判定文件。
4. **改 env**：按 §7.1 那张表核对 `SR_PYTHON` / `SR_BUNDLE_DIR` / `SR_WORK_DIR` / `SR_SR_SCRIPT` /
   `SR_VERIFY_SCRIPT` / `SR_PREP_SCRIPT` / `SR_LOCAL_GPU`；`SR_EXECUTOR` 只接受 `local`，留着一个
   别的值等于配置错误（启动即报错，不静默改语义）。`SR_SR_SCRIPT` **必须指向变体**：不配就是跑
   生产原脚本，单卡下 `gpu_count != 4` 守卫恒真，这一跑 `exit(3)` 直接结束。
5. **升应用**：`backend/` 与 `frontend/dist` 同批更新，`systemctl daemon-reload && systemctl restart sr-api`。
6. **验收**：按 [real-machine-acceptance.md](real-machine-acceptance.md) §D 逐条勾。

## 四、回滚

- 回滚 = 换回**匹配的应用版本**（旧 `backend/` + 旧 `frontend/dist`）+ 旧的部署配置（systemd unit 与
  drop-in、nginx 站点）。数据库向下兼容：新版本的增量列对旧版本是多余的列，不影响旧版本读它自己的列。
- 脚本侧回滚：把 `SR_SR_SCRIPT` 改回 `code_0817_prod.py` 即回到生产原文件（原文件字节未动）。
- **不得**用「覆盖真实影像」或「清空任务表」当回滚手段。

## 五、真机待验项

程序侧行为在开发机上由替身与真实本地链覆盖（`.e2e`、`backend/tests/test_local_chain.py`），
下面这些只有真机能给答案：

| 项 | 判据 |
|---|---|
| nginx 能写 `SR_WORK_DIR` 与场景目录 | `sudo -u nginx touch <dir>/x` 成功 |
| nginx 能读能执行 `SR_PYTHON` 与 `SR_BUNDLE_DIR` | `sudo -u nginx <SR_PYTHON> -c "import torch, osgeo"` 与 `test -r <BUNDLE>/code_0817_prod.py`。看 GDAL 版本用 `osgeo.gdal.VersionInfo()` —— 那个 py3.6 时代的 `osgeo` 绑定**没有** `gdal.__version__`，写错会抛 AttributeError，看着像环境坏了 |
| 选卡生效 | 日志开头 `CUDA_VISIBLE_DEVICES=` 与 `gpu <idx>, <uuid>` 行非空，且与 `SR_LOCAL_GPU` 一致 |
| 判定文件落盘 | 跑完 `ls <lq_path>/Debug/_SREXIT_<job_id>.txt`，内容 `verdict=0` |
| 平台不白等 | `/api/queue` 该行由 RUNNING → COMPLETED（不是一直 UNKNOWN） |
| 归位与备份清理 | 本体尺寸 = 源头 × `SRScale`，`Debug/` 里没有 `.srbak` / `.sr1x` 残留 |
| 取消停得住 | 运行中点取消 → 子进程树停止、状态转 FAILED/CANCELLED，`ps -ef` 无残留 |
| 重启后不重复启动 | `systemctl restart sr-api` → 该行按运行记录/判定文件给出终态，不自动重投 |

## 六、关联

- 部署与装法：[deploy/README.md](../../deploy/README.md) §七
- 生产脚本变体、校验器、就位脚本：[sr-platform-variant.md](../sr_code/sr-platform-variant.md)
- SR 调用契约：[sr-pipeline-interface.md](../sr_code/sr-pipeline-interface.md)
- 真机验收单：[real-machine-acceptance.md](real-machine-acceptance.md) · 部署手册：[real-machine-bringup.md](real-machine-bringup.md)
- 平台现状与交接入口：[current-question.md](current-question.md)
- 代码：`backend/services/run_sr.py` · `backend/services/local_exec.py` · `backend/services/sr_result.py` ·
  `backend/api/platform.py` · `backend/tools/run_sr.py` · `backend/tools/sr_job_status.py`
