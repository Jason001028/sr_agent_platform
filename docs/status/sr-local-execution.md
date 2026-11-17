# 本机执行 SR —— 当前路线与运行期规则

> 日期：2026-10-20 · 状态：已定 · 读者：接手 Agent 窗口 + 运维
> 用途：平台上线路线只有一条 —— 后端用 SR 生产解释器在 sr-api 所在机器上直接起子进程。
> 本文只写这条路线现在怎么跑。部署装法见 [deploy/README.md](../../deploy/README.md) §七，
> 调用契约见 [sr-pipeline-interface.md](../sr_code/sr-pipeline-interface.md)。

## 一、链路

```text
/queue 或 /chat 提交
   → 任务服务（backend/services/run_sr.py）：参数归一化、掩码推导、指纹、幂等、取消
   → 本机执行器（backend/services/local_exec.py）：单槽串行、起 bash 子进程、写日志
   → 运行脚本：SR 脚本 → 契约校验器
   → 判定文件（<lq_path>/Debug/_SREXIT_<job_id>.txt）+ SQLite 任务行
   → SSE（/api/queue/events）→ 前端队列页
```

执行者是 sr-api 自己：没有调度器，没有跨主机依赖。作业在盘阵上**就地**跑，用户填的
`lq_path` 就是会被写入的目录。

## 二、运行期规则

### 2.1 单槽串行

- 同一时刻只跑一个 SR 作业。`local_exec` 用进程内的单槽锁排队；第二个提交显示 PENDING，
  不会另起一个 SR 进程。
- 单 API 进程：队列状态轮询、预览清理是 lifespan 后台循环，多 worker 会各做一遍。

### 2.2 落盘物

| 位置 | 内容 |
|---|---|
| `SR_WORK_DIR`（优先）或 `SR_SLURM_WORK_DIR`（历史名，默认 `/tmp/sr_agent_work`） | config.xml、运行脚本、子进程日志 |
| `<工作目录>/.local_job_seq` | 运行编号计数器；重启后继续，不回收已发出的编号 |
| `<工作目录>/.local_jobs.json` | 持久化排队/进程表；重启后恢复 queued、按 PID 接管 running |
| `<lq_path>/Debug/_SREXIT_<job_id>.txt` | 判定文件（校验器写） |
| SQLite `sr_tasks` | 任务行：参数指纹、job_id、状态、时间 |

### 2.3 终态只看判定文件

```
<lq_path>/Debug/_SREXIT_<job_id>.txt   job_id= / sr_exit_code= / verdict=0|90 / skip=0|1 / reason=
```

- `verdict=0` → COMPLETED；`verdict!=0` → FAILED；`skip=1`（云量策略跳过）→ COMPLETED 且不出产物。
- **子进程退出码 0 不等于成功**：契约三条件（退出码 0 + SRLOG 末行 `Run finished.` + 输出 tif）
  由校验器在作业内判，不满足时校验器以退出码 90 结束并写 `verdict=90`。
- 路径由约定锁死：校验器写它、`run_sr.exit_code_file_for()` 从 config.xml 反推它、
  `local_exec.status()` 读它。运行编号由 `--job-id` 显式传给校验器。

### 2.4 重启、排队恢复与进程接管

队列与进程号写入 `<工作目录>/.local_jobs.json`（先写记录再起子进程）。sr-api
启动时 `local_exec.recover()` 在调度循环之前执行：

| 情形 | 恢复后的报告 |
|---|---|
| 排队记录（子进程从未启动） | 重新派发；同一 job_id 只启动一次，`sr_tasks` 账本不变 |
| 子进程仍在（PID 可查到） | 按 PID 接管；看护线程持有单槽直到它退出 |
| 子进程已退出、没有判定文件 | UNKNOWN（不盲目重投） |
| 判定文件可读 | 按 `verdict` 给 COMPLETED / FAILED |
| 显式取消 / `SR_LOCAL_JOB_TIMEOUT_SEC` 到点 | 杀掉整棵进程树；无判定时分别为 CANCELLED / TIMEOUT |

`deploy/sr-api.service` 用 `KillMode=process`，让本机 SR 子进程在 sr-api 重启时
存活，重启后才能被接管；`systemctl stop sr-api` 时子进程也会继续跑，直到自己结束
或被人为清理。若子进程已经被系统杀掉且没有判定文件，仍是 UNKNOWN，需要人工核对，
平台不会为了“恢复”而重复提交。

接管与取消都先校验进程身份（POSIX `/proc/<pid>/stat` 起始时间 + cmdline；Windows
进程创建时间），PID 被复用时不会认领、也不会向该 PID 发信号。启动窗口
（`Popen` 前后）中断的记录进入 `quarantine`：不清空、不重投，并**阻塞后续排队任务**
占用单槽，直到判定文件出现（状态读取时自动解除）或运维执行
`local_exec.resolve_quarantine(<job_id>)` 人工确认。取消意图写入
`cancel_requested` 并落盘；运行中取消后即使 sr-api 立即重启，恢复逻辑也会继续终止
并确认进程树退出。

队列写盘失败的处理是硬性的：派发前写不进 `.local_jobs.json` 就直接拒绝启动；
`Popen` 成功但 running/PID 写不进时立即杀掉该子进程，磁盘上保留的 `starting`
会在下次重启进入 isolation，而不是把旧 `queued` 再派发一次。

人工确认前先看 `<工作目录>/<stem>.<job_id>.out`、`ps -ef` 与场景目录下的
`Debug/_SREXIT_<job_id>.txt`。确认没有残留进程后，在 sr-api 的 Python 环境里执行：

```bash
# 例：/opt/sr-venv/bin/python，env 与 sr-api 一致
python -c "from backend.services import local_exec; print(local_exec.resolve_quarantine(<job_id>))"
```

该调用只释放隔离、不向任何 PID 发信号；若判定文件已存在，普通状态读取会自动释放。

### 2.5 生产预检（仅 local + production）

`local_exec.preflight()` 检查并在首次调用（或相关 env 变化）时缓存：

- 可用 bash；
- `SR_WORK_DIR`（或旧 `SR_SLURM_WORK_DIR`）存在且可写；
- `SR_PYTHON` 可执行，并能导入 `SR_PREFLIGHT_IMPORTS`（缺省 `torch,osgeo,numpy`）；
- `SR_BUNDLE_DIR`、`SR_SR_SCRIPT` 与仓库内 `SR_code/variants/{repair_sr,verify_sr_run}.py`；
- config.py 默认 OPT 里的 `pretrain_model_G` 权重文件（计划参数里的 `options_yml` 仍按原逻辑生效）；
- `nvidia-smi` 能在超时内返回，且 `SR_LOCAL_GPU` 命中可用 index/UUID。

失败时 `execution.allowed=false`，计划卡显示具体中文原因。预检结果按 env 快照缓存，
计划卡轮询不会重复执行；服务启动且 `SR_BATCH_PRODUCTION=1` 时会在后台预热一次。

### 2.6 超时与取消

- `SR_LOCAL_JOB_TIMEOUT_SEC`（秒，0/未设 = 不设上限）：到点杀整棵进程树，
  无判定文件时状态为 TIMEOUT，批量 Item 走失败验收，不会宣告成功。
- 取消：queued 记录直接标记取消；running 记录按 Popen 句柄或接管 PID 杀掉进程组
  （POSIX `killpg`，Windows `taskkill /T`）。

## 三、与历史调度器路径的关系

仓库里仍保留 `backend/services/slurm.py`、`SR_SLURM_*` 环境变量和 `SR_EXECUTOR=slurm`
分支，作为 2026-09-14 中止的调度器路线存量。**上线部署一律 `SR_EXECUTOR=local`**；
交接、排障、验收都只看本机执行这条链。历史验收材料见
[timeline-archive.md](timeline-archive.md)。

批量修复的 production 模式现在也走这条执行器：`SR_BATCH_PRODUCTION=1` 是人工准入开关
（见 [repair-dual-entry.md](repair-dual-entry.md)），开关打开后 local 只再检查 bash 可用
与 `SR_SLURM_WORK_DIR` 可写；Slurm 不再是生产批量的必要条件。批量仍复用 `local_exec`
的单槽串行，`max_inflight_sr_jobs` 大于 1 时后续作业在内存队列里 PENDING，不会并行抢卡。

## 四、真机待验项

| 项 | 判据 |
|---|---|
| nginx 能写 `SR_WORK_DIR`（或旧名）与场景目录 | `sudo -u nginx touch <dir>/x` 成功 |
| nginx 能读能执行 `SR_PYTHON` 与 `SR_BUNDLE_DIR` | `sudo -u nginx <SR_PYTHON> -c "import torch, osgeo"` 与 `test -r <BUNDLE>/code_0817_prod.py` |
| 选卡生效 | 日志开头 `CUDA_VISIBLE_DEVICES=` 与 `SR_LOCAL_GPU` 一致 |
| 判定文件落盘 | 跑完存在 `<lq_path>/Debug/_SREXIT_<job_id>.txt`，内容 `verdict=0` |
| 平台不白等 | `/api/queue` 该行由 RUNNING → COMPLETED |
| 取消停得住 | 运行中点取消 → 子进程停止，`ps -ef` 无残留 |

### 4.1 真实单景 SR 与批量的验收步骤（未执行，待核）

以下步骤需要内网 GPU、真实场景与 `SR_BATCH_PRODUCTION=1`；本轮只运行了合成 bash
夹具，**没有提交真实 GPU 作业**，因此全部标为未完成。

1. **预检**：重启 sr-api 后准备一张单景修复计划，确认计划卡不再显示「暂不可执行」；
   若受阻，卡上的原因应能对应到 bash / 工作目录 / Python 导入 / 算法入口 / 模型权重 /
   GPU 六项之一。
2. **真实单景提交**：确认并启动 1 景，记录 job_id；检查
   `<工作目录>/<stem>.<job_id>.out` 的 `CUDA_VISIBLE_DEVICES` 与 `SR_LOCAL_GPU`；
   等待判定文件 `verdict=0`，确认产物与人工验收链路未变。
3. **重启接管**：启动一个较慢的真实作业，在 RUNNING 时 `systemctl restart sr-api`；
   重启后该作业仍显示 RUNNING（PID 相同），完成后进入 COMPLETED；日志里只能有
   一次物理启动。
4. **排队恢复**：让第 1 景占住单槽，再提交第 2 景（PENDING）；重启 sr-api 后确认
   第 2 景自动开始，且 `sr_tasks` 中没有第二条相同指纹的提交。
5. **取消/超时**：RUNNING 中点取消，`ps -ef` 无残留；用较小的
   `SR_LOCAL_JOB_TIMEOUT_SEC` 跑一次慢作业，确认状态为 TIMEOUT 且进程树被清理。
6. **并发**：同时提交 2 景，确认任一时刻最多 1 个 SR 子进程；第二个为 PENDING。
7. **记录**：把上述命令输出、job_id、判定文件内容与截图补进
   [real-machine-acceptance.md](real-machine-acceptance.md)；未跑完的项保持未勾选。

## 五、关联

- 部署与装法：[deploy/README.md](../../deploy/README.md) §七
- 生产脚本变体与校验器：[sr-platform-variant.md](../sr_code/sr-platform-variant.md)
- SR 调用契约：[sr-pipeline-interface.md](../sr_code/sr-pipeline-interface.md)
- 真机验收单：[real-machine-acceptance.md](real-machine-acceptance.md) · 部署手册：[real-machine-bringup.md](real-machine-bringup.md)
- 平台现状与交接入口：[current-question.md](current-question.md)
- 代码：`backend/services/run_sr.py` · `backend/services/local_exec.py` ·
  `backend/api/platform.py` · `backend/tools/run_sr.py` · `backend/tools/sr_job_status.py`
