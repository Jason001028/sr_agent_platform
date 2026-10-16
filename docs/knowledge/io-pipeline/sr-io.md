# 链路 C：掩码到 SR 作业与队列

> 日期：2026-09-23 · 状态：草稿（对照当日 `backend/api/platform.py`、`services/run_sr.py`、`services/local_exec.py`、`SR_code/`）
>
> **目标读者**：要改提交链路、终态判定、队列状态的人；需要向别人解释「这作业到底成没成」的人。
> **一句话摘要**：掩码先落到盘阵，提交时后端生成 config.xml 与一份运行脚本，
> 用 SR 生产解释器在本机起进程；作业自己写一个退出码文件，后端读它定终态。
>
> **算法侧不自研**：超分算法调用 SR 生产脚本，平台只负责参数、路径、执行与状态。
> 算法与调用契约见 [docs/sr_code/](../../sr_code/)。

---

## 1. 全景

```mermaid
flowchart TD
  MK["画布上的掩码多边形"] --> BM["POST /api/masks"]
  BM --> MF["盘阵：输入影像名_mask.tif<br/>加 _mask.txt 质心"]
  MF --> Q["POST /api/queue"]
  Q --> NORM["参数归一 + 任务指纹"]
  NORM --> GEN["生成 config.xml 与运行脚本"]
  GEN --> EXEC["本机起子进程<br/>conda py3.6"]
  EXEC --> JOB["SR 作业"]
  JOB --> PROD["盘阵：产物 tif 与 Debug 日志"]
  JOB --> EXF["盘阵：Debug/_SREXIT_<job_id>.txt"]
  EXF --> POLL["后台轮询读终态"]
  POLL --> SQL["SQLite：sr_tasks.status"]
  POLL --> SSE["SSE job_update"]
  SSE --> UI["/queue 页"]
```

---

## 2. 掩码落盘

掩码在浏览器里画好之后，矢量多边形交给后端栅格化：

```
POST /api/masks  { lq_path 或 scene_id, W, H, polygons }
  → services/mask.py
  → <输入影像名>_mask.tif   值域 0/255
  → <输入影像名>_mask.txt   各连通域质心坐标
```

- **0 = 不处理，255 = 处理**（`255` 那一侧才是要超分的区域）。SR 侧按「大于 0」读取。
- 坐标是**源影像的像素坐标**：x 为列、y 为行。盘阵场景的源影像尺寸取场景行的 `W` / `H`，
  与预览 JPG 的像素尺寸无关，所以换档位不影响掩码。
- 目标目录由后端从 `lq_path` 或场景 id 推出来，并过路径白名单；写盘失败回 422。
- **也可以不经过后端**：浏览器直接生成 `掩码.tif` 与 `掩膜中心点坐标.txt` 下载。
  那条路不写盘阵，适合离线留档。

---

## 3. 提交：归一与幂等

```
POST /api/queue  { lq_path, mask_path?, sr_scale?, suffix?, gpu?, cloud_limit?, ... }
  → _norm_sr_params
  → services/run_sr.submit_run_sr
```

三件事在归一阶段完成：

1. **路径归一与白名单**：盘符形态合成 POSIX 形态，再校验前缀。
2. **掩码推导**：请求不带 `mask_path` 时，按 `<lq_path>/<输入影像 stem>_mask.tif` 推导；
   文件不存在直接 400——**不静默退化成整图超分**。
3. **锁定目录**：配了 `SR_LOCKED_DIR` 时，队列只接受这一个场景目录，其余 400——
   把影响面收在一个目录里的开关。

**幂等靠任务指纹**：提交参数规范化后取 sha256，是 `sr_tasks` 表的唯一键。
同参数重交复用同一行，不会投出第二个作业。指纹进聚合了默认后缀，所以那个默认值
**刻意不做进程内缓存**——缓存会让指纹依赖进程启动时刻，反而造成重复投递。

```mermaid
stateDiagram-v2
  [*] --> 已入库: 记录提交意图，job_id 为空
  已入库 --> PENDING: 拿到 job_id
  PENDING --> RUNNING: 子进程已起、运行记录里记着它
  RUNNING --> COMPLETED: 退出码文件判成功
  RUNNING --> FAILED: 退出码文件判契约不满足
  PENDING --> FAILED: 同上
  RUNNING --> UNKNOWN: 读不到任何判据
  已入库 --> UNKNOWN: 提交中断，无 job_id
  COMPLETED --> [*]
  FAILED --> [*]
```

提交顺序是**先记意图再记 job_id**：中途崩溃重放时，靠运行记录或退出码文件
去解析，而不是盲目再投一次。中断遗留（有行、无 job_id）的行重交时返回 409。

---

## 4. 生成两份文件

| 文件 | 位置 | 内容 |
|---|---|---|
| `run_sr_<后缀>_<指纹前 12 位>_<运行编号>.xml` | `SR_WORK_DIR` | config.xml：`DatarootLQ` / `SRScale` / `Suffix` / `MaskPath` / `CloudLimit` / `GridAlign` |
| 同名 `.sh` | 同上 | 运行脚本：审计段 + 跑 SR 脚本 + 跑校验器 |

运行脚本的三段：

```mermaid
flowchart LR
  A["审计段<br/>回显作业号 可见 GPU 主机名 解释器名"] --> B["cd 到 SR 生产包目录"]
  B --> C["解释器 + SR 入口脚本 -f config.xml"]
  C --> D["契约校验器 --config ... --sr-exit-code"]
```

两条设计约束：

- **不写 `CUDA_VISIBLE_DEVICES`**：选卡由部署决定（`SR_LOCAL_GPU`），`local_exec`
  只把它设进子进程**环境**，脚本里不出现任何赋值。
- **省略 `set -e`**：要让校验器有机会在 SR 脚本失败后仍然跑起来并写下判词。

---

## 5. 执行器

```mermaid
flowchart TD
  SUB["submit_run_sr"] --> R1["取 job_id 序列号<br/>写 .local_job_seq"]
  R1 --> P["Popen bash + 运行脚本<br/>单槽串行"]
  P --> G["子进程环境：删 VIRTUAL_ENV / PYTHONHOME / PYTHONPATH<br/>前置解释器目录进 PATH<br/>设 CUDA_VISIBLE_DEVICES"]
```

| | 本机执行（`local_exec`） |
|---|---|
| 并发 | 单槽串行（跨进程文件锁 `<SR_WORK_DIR>/.exec.lock`） |
| job_id | 自增，持久化在 `.local_job_seq` |
| 选卡 | `SR_LOCAL_GPU` 透传为 `CUDA_VISIBLE_DEVICES` |
| 取消 | 停止完整进程树（POSIX 下先 TERM 后 KILL） |
| 终态判据 | 作业内校验器写的退出码文件 |

执行器返回状态字典，上层代码据此渲染。**终态判据只有一条**：
作业内校验器写的退出码文件（见 §6）。

---

## 6. 作业内的 IO（SR 侧契约）

```
<SR 解释器> <SR 入口脚本> -f <config.xml>
```

| 项 | 内容 |
|---|---|
| 输入 | config.xml；`DatarootLQ` 目录里的输入影像与 `<目录名>_meta.xml` |
| 输出 | `<输入名>_<Suffix>.tif`；`Debug/<输入名>_SRLOG.txt`，末行 `Run finished.` |
| 退出码 | 0 = 情况繁多（含云量跳过、配置缺失等提前返回）；1 = 运行期异常；3 = GPU 相关；90 = 契约不满足 |

**退出码 0 不等于成功**。这一点是整条链路最要紧的口径：生产脚本有若干条「正常提前返回」
的分支（云量超限、输入缺失）也是 `exit(0)`，只看退出码会把它们全判成成功。
所以引入了第二个进程——**契约校验器**，紧跟 SR 脚本之后跑，逐条验：

1. SR 进程退出码为 0；
2. 日志末行是 `Run finished.`（尾读字节取最后非空行，不是按行读）；
3. 输出 tif 存在且非空。

三条都满足写 `verdict=0`，否则写 `verdict=90`。合法的跳过（末行以 `Run skipped:` 开头）
只要求前两条。

判词写成文件：

```
<DatarootLQ>/Debug/_SREXIT_<job_id>.txt
job_id=…  sr_exit_code=…  verdict=0|90  skip=0|1  reason=…
```

**这是一个跨语言契约**：写入方是 SR 作业侧的 Python 脚本，读取方是平台后端；
两边各自拼这个路径，命名约定由测试钉住，改一边必须改另一边。

---

## 7. 终态与回流

终态一律来自作业内校验器写的退出码文件：

```mermaid
sequenceDiagram
  participant UI as 浏览器
  participant API as sr-api
  participant ST as SQLite
  participant DISK as 盘阵Debug

  UI->>API: GET /api/queue
  API->>ST: 读未终结行
  API->>DISK: 读 _SREXIT_jobid.txt
  DISK-->>API: verdict
  API->>ST: 状态有变才写回
  API-->>UI: 任务数组

  UI->>API: GET /api/queue/events（SSE 长连）
  loop 每 SR_QUEUE_POLL_SEC
    API->>DISK: 读终态
    API-->>UI: job_update 帧（含时间戳）
  end
```

几个必须知道的判据：

- **无 job_id 的行绝不发明终态**，如实回库里的状态。那是「提交中断」，不是「跑挂了」。
- **比较基准是内存缓存，缓存缺失回落库里的状态**。不回落的后果是服务一重启，
  每一行都被判成「状态变了」，于是所有历史行被写回一次、耗时列集体退化成行龄。
- **SSE 帧必须带上写库后的时间戳**。只推状态的话，客户端手上的还是上一次 GET 的快照，
  耗时列要么显示 0 秒，要么停在上一次的数字上。
- **耗时口径是纯算力时长**（开始跑 → 跑完），不含排队。

队列状态只有五个：`PENDING` / `RUNNING` / `COMPLETED` / `FAILED` / `UNKNOWN`。

---

## 8. 易判错点

- **退出码 0、`Run finished.`、产物存在，三者缺一不可**。任何单独一项都不足以判成功。
- **`verdict=90` 是「契约不满足」，不是「SR 算法失败」**。两者要分开报，否则排障方向会错。
- **掩码必须落在本体影像的网格上**。产物是它的放大结果，拿产物的尺寸写掩码会
  把一张产物尺寸的掩码盖到本体的掩码文件上。前端用「环节」这个概念把这件事一路带住。
- **已超分过的场景会被再超分一遍**。生产脚本里那段「已超分则跳过」的分支现在是
  打印一行就返回，不是幂等跳过；场景落在这种状态时作业会提前结束、不产出新产物，
  校验器据此判失败。
