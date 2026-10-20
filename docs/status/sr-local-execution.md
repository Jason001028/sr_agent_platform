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
| `SR_SLURM_WORK_DIR`（历史变量名，默认 `/tmp/sr_agent_work`） | config.xml、运行脚本、子进程日志 |
| `SR_SLURM_WORK_DIR/.local_job_seq` | 运行编号计数器；重启后继续，不回收已发出的编号 |
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

### 2.4 重启与未知

进程内记录只活在 sr-api 进程里；重启后 `status()` 退回判定文件。

| 情形 | 报告 |
|---|---|
| 子进程仍在 | RUNNING |
| 排队中被重启 | 无判定文件 → UNKNOWN |
| 子进程已退出、没有判定文件 | UNKNOWN |
| 判定文件可读 | 按 `verdict` 给 COMPLETED / FAILED |

UNKNOWN 表示平台无法判断，不是“可以重跑”。同参数重投前先人工核实是否有残留进程。

## 三、与历史调度器路径的关系

仓库里仍保留 `backend/services/slurm.py`、`SR_SLURM_*` 环境变量和 `SR_EXECUTOR=slurm`
分支，作为 2026-09-14 中止的调度器路线存量。**上线部署一律 `SR_EXECUTOR=local`**；
交接、排障、验收都只看本机执行这条链。历史验收材料见
[timeline-archive.md](timeline-archive.md)。

## 四、真机待验项

| 项 | 判据 |
|---|---|
| nginx 能写 `SR_SLURM_WORK_DIR` 与场景目录 | `sudo -u nginx touch <dir>/x` 成功 |
| nginx 能读能执行 `SR_PYTHON` 与 `SR_BUNDLE_DIR` | `sudo -u nginx <SR_PYTHON> -c "import torch, osgeo"` 与 `test -r <BUNDLE>/code_0817_prod.py` |
| 选卡生效 | 日志开头 `CUDA_VISIBLE_DEVICES=` 与 `SR_LOCAL_GPU` 一致 |
| 判定文件落盘 | 跑完存在 `<lq_path>/Debug/_SREXIT_<job_id>.txt`，内容 `verdict=0` |
| 平台不白等 | `/api/queue` 该行由 RUNNING → COMPLETED |
| 取消停得住 | 运行中点取消 → 子进程停止，`ps -ef` 无残留 |

## 五、关联

- 部署与装法：[deploy/README.md](../../deploy/README.md) §七
- 生产脚本变体与校验器：[sr-platform-variant.md](../sr_code/sr-platform-variant.md)
- SR 调用契约：[sr-pipeline-interface.md](../sr_code/sr-pipeline-interface.md)
- 真机验收单：[real-machine-acceptance.md](real-machine-acceptance.md) · 部署手册：[real-machine-bringup.md](real-machine-bringup.md)
- 平台现状与交接入口：[current-question.md](current-question.md)
- 代码：`backend/services/run_sr.py` · `backend/services/local_exec.py` ·
  `backend/api/platform.py` · `backend/tools/run_sr.py` · `backend/tools/sr_job_status.py`
