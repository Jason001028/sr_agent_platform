# 批量修复 PR C 实施记录：Producer / Monitor（2026-11-02）

> 依据：[1023.md](../planning/1023.md) 第二部分 C 与 K1「C：确定性调度」。前序成果见 [batch-pr-ab.md](batch-pr-ab.md)。
> 基线：`main` @ `6ac0408`，本机 Windows 10 + Python 3.11.3。本轮只做后端与测试；未执行真实 SR/GPU，未创建 Git 提交，未覆盖已有未提交改动。
> 演示口径：Mock Detector + 真实最小 Mask 文件 IO + Fake SR + MockVerifier（`provenance=mock`）。生产路径在本阶段被显式拒绝。

## 1. 范围与取舍

- 交付 5 景异步纵向切片所需的后端语义：单 Batch Scheduler、Producer/Monitor 分职责、持久化续跑、提交幂等与容量预留、暂停/取消后的外部作业追踪、PR F 可消费的事件契约。
- 不交付开发机可跑的 5 景演示入口（HTTP/前端演示归 PR F）；不建设真实检测算法、完整 Compaction、进度 Panel。
- 演示执行三条硬门（`ensure_execution_allowed`）：`mode=demo`、`SR_EXECUTOR=slurm`、`SR_SLURM_FAKE=1`；另要求显式可写的 `SR_SLURM_WORK_DIR` 且路径不含 `DiskArray`。生产 `mode` 直接拒绝，MockVerifier 不能进入生产。

## 2. 实现

### 2.1 调度与执行隔离

- 单个 `batch_worker.batch_loop()`（默认 `SR_BATCH_POLL_SEC=1`）串行驱动所有 Batch Goal；`batch_tick()` 加进程内锁，多个调度者不会重复领取同一 Item。
- 每一趟先对账、收集已完成 IO、再监控，最后最多推进一个 Producer 步骤；普通 Goal 仍由原 `goal_worker` 调度，Batch 候选不进入其窗口。
- 检测 + Mask 生成提交到常驻 `batch_io_executor`，调度线程只轮询 `concurrent.futures` 的完成状态，从不等待。慢检测不会阻塞 SR 状态查询；结果提交前重新校验 Item 版本与 Goal Lease，过期结果丢弃。`DETECTING` 没有外部副作用，重启后重跑安全。
- 每趟只做有限工作，并以 `arm_goal(next_wake_at)` 决定继续或休眠；不持长事务做文件 IO 或外部请求。

### 2.2 Item 状态机

`PENDING → OPENING → DETECTING → MASK_READY → SUBMITTING → WAITING_SR → VERIFYING → SUCCEEDED`

分支：`NO_ISSUE / RETRY_WAIT / FAILED / BLOCKED / SKIPPED`。状态迁移全部走 `Store.transition_goal_item()`，以 Step `version` 做 CAS，并校验 Goal Lease token 与到期时间。

- `OPENING`：路径守卫、文件存在性、Manifest 冻结的 `source_revision` 一致性、TIFF 尺寸/波段/样本类型/压缩探测、拒绝既有 `PAN_old_latest.tif` / `PAN_old_2.tif`。
- 检测失败限 3 次（退避 1s/2s）后 `FAILED`；空候选直接 `NO_ISSUE`，不发 SR；单景失败不终止同批其他 Item。

### 2.3 提交身份与容量

- `reserve_item_submission()` 在单个事务内复用现有 Tool Intent 与 `sr_tasks` 账本：意图键为 `batch:{goal}:{item}:a{attempt}:{fingerprint}`，指纹沿用 `run_sr.task_fingerprint`；意图在外部提交前落库，Item 同时 `MASK_READY → SUBMITTING` 并置 `capacity_reserved=1`。
- 容量同时受 `max_inflight_sr_jobs` 与 `max_prepared_items` 约束；`count_reserved_slots()` 按 `SUBMITTING|WAITING_SR` 或 `capacity_reserved=1` 计数，未决阻断项跨重启继续占位。
- 崩溃恢复顺序固定为：先查现有 SR 记录/Job ID → 采用已有回执转 `WAITING_SR`；能证明副作用未发生（无 `sr_tasks`、意图未决）才退回 `MASK_READY` 安全重试；无法确认则 `BLOCKED` 并保留容量。任何分支都不盲目重投。
- 提交结果校验 `batch_script` 位于演示工作目录内且 `job_id` 非空；`SubmissionUnknown` 或异常一律进入上述对账路径。

### 2.4 暂停 / 取消

- 用户暂停或取消后 Producer 停止：不再启动检测、不再提交 SR。
- Monitor 通过 `claim_goal_monitor()` 取得受限 Lease，不改写 Goal 状态；已接受的外部 Job 继续查询、验收并收敛。取消时未提交的 Item 记 `SKIPPED`（`exclusion_reason=user_cancelled`）。
- `monitoring_done` / `monitor_status` 只记录「对账是否已完成」；Producer 每趟开始时清除旧值，避免上一次暂停的标记让后续暂停跳过仍在运行的 Job。

### 2.5 事件契约（供 PR F）

状态与事件在同一事务提交；事件均已通过测试校验顺序与版本单调：

| 事件 | 载荷要点 |
| --- | --- |
| `item_state_changed` | `item_id, item_version, attempt, from, to` |
| `mask_ready` | `item_id, path, sha256, width, height, spp, foreground_pixels, provenance` |
| `sr_submission_reserved` | `item_id, intent_key, fingerprint, attempt` |
| `sr_receipt_recorded` | `item_id, job_id, fingerprint, status, recovered` |
| `external_job_terminal` | `item_id, job_id, state, exit_code, provenance` |
| `item_verified` | `item_id, ok, provenance, state, reasons` |
| `batch_phase_changed` | `status, phase, counts` |
| `batch_control_changed` | `status, monitoring_done, counts` |
| `batch_report_updated` | 完整汇总对象 |
| `goal_verified` | Batch 验收结果 + 汇总（既有事件） |

### 2.6 汇总与收口

- 全部 Item 收敛（`SUCCEEDED/NO_ISSUE/FAILED/SKIPPED/BLOCKED`）后进入 `VERIFYING`，报告写 `artifact_root()/reports/<goal>.json`（原子替换），再用 `complete_verified_goal` 结束 Goal。
- 终态优先级：`BLOCKED > FAILED > COMPLETED`；存在阻断或失败时如实呈现部分完成，不把业务 DONE 当作成功。

## 3. 文件

| 文件 | 变更 |
| --- | --- |
| `backend/services/batch_worker.py`（新增） | Scheduler、Producer、Monitor、提交预留/对账、暂停取消、汇总与事件 |
| `backend/services/batch_detector.py`（新增） | 确定性中心框检测（`SR_BATCH_DETECTION=center/none/error`） |
| `backend/services/batch_mask.py`（新增） | 真实最小 Mask 文件 IO：单波段 uint8、PAN 尺寸、0/255、原子发布、契约自检 |
| `backend/tests/test_batch_worker.py`（新增） | 25 项 PR C 验收与回归用例 |
| `backend/services/store.py` | 新增 `arm_goal`、`claim_goal_monitor`、`patch_goal_checkpoint`、`transition_goal_item`、`reserve_item_submission`、Item 查询/计数；Step 增 `capacity_reserved` 列；`transition_goal` 支持同事务附加事件（叠加在既有未提交的 PR B 改动之上） |
| `backend/api/app.py` | lifespan 中启动 `batch_worker.batch_loop(app.state)`（叠加在既有未提交改动之上） |
| `backend/agent/async_transport.py` | 在请求计时外预热 httpcore 传输（复审后的计时稳定性修复，见 §7.5） |

## 4. 测试

命令：

```text
PYTHONDONTWRITEBYTECODE=1 python -m pytest backend/tests/test_goal_runtime.py \
  backend/tests/test_run_sr.py backend/tests/test_local_exec.py \
  backend/tests/test_batch_manifest.py backend/tests/test_batch_worker.py \
  backend/tests/test_goal_process.py -q -p no:cacheprovider
```

结果：189 passed（其中 `test_batch_worker.py` 25 项）。

`test_batch_worker.py` 覆盖：

| 用例 | 验证点 |
| --- | --- |
| `test_five_scene_pipeline_completes_without_model_calls` | 5 景完成、5 次提交、`max_model_calls=0`、Mock 报告与报告文件 |
| `test_n_plus_one_starts_before_first_job_is_terminal` | 第 1 景未终态时第 2 景已提交 |
| `test_restart_does_not_resubmit_an_accepted_job` | 重启后沿用 Job ID，不重复提交 |
| `test_accepted_without_job_id_blocks_and_never_resubmits` | 已接受无 Job ID → 对账后 `BLOCKED`、容量保留、不重投 |
| `test_reservation_without_side_effect_is_released_and_retried` | 仅有预留无副作用 → 退回 `MASK_READY` 并只重试一次 |
| `test_pause_stops_producer_but_monitor_keeps_the_accepted_job` | 暂停后不再提交，已接受 Job 仍收敛，Goal 保持 `PAUSED` |
| `test_resume_after_pause_restarts_the_producer` | 恢复后 Producer 继续推进 |
| `test_cancel_skips_unsubmitted_items_and_finishes_monitoring` | 取消跳过未提交项、继续监控已接受项 |
| `test_unknown_sr_state_converges_to_blocked_after_bounded_checks` | UNKNOWN 限量 3 次后 `BLOCKED` |
| `test_no_candidate_never_submits` | 空候选 `NO_ISSUE`，不发 SR |
| `test_replaced_pan_is_blocked_before_submission` | PAN 被替换在检测前阻断 |
| `test_pan_drift_while_waiting_for_capacity_blocks_before_submit` | `MASK_READY` 等容量期间 PAN 变化 → 提交前 `BLOCKED` |
| `test_pan_drift_across_pause_and_resume_blocks_before_submit` | 暂停/恢复窗口的 PAN 变化 → 提交前 `BLOCKED` |
| `test_pan_drift_after_restart_blocks_before_submit` | 重启后的 PAN 变化 → 提交前 `BLOCKED` |
| `test_mask_drift_after_mask_ready_blocks_before_submit` | 掩码摘要变化 → 提交前 `BLOCKED` |
| `test_capacity_deadlock_is_finalized_with_blocked_report` | 未决容量耗尽 → 批次 `BLOCKED`、剩余项 `SKIPPED`、报告落盘、不释放容量 |
| `test_monitor_query_reaches_old_paused_goal_behind_converged_backlog` | 205 个已收敛暂停批次之后仍能监控旧的活动 Job |
| `test_creation_time_skipped_items_complete_without_submit` | 创建即全跳过时直接收口 |
| `test_mask_writer_contract_and_atomic_publish` | Mask 尺寸/单波段/0-255/原子发布 |
| `test_demo_gate_rejects_production_local_and_bad_work_dir` | 生产、local、Fake 关闭、目录缺失/越界均拒绝 |
| `test_two_schedulers_do_not_submit_one_item_twice` | 并发调度不重复物理提交 |
| `test_detection_io_does_not_block_sr_status_queries` | 检测阻塞时 SR 状态查询仍推进 |
| `test_events_expose_the_pr_f_contract_in_transition_order` | 事件顺序与版本单调 |
| `test_single_item_detection_error_is_retried_without_failing_the_batch` | 单景检测错误重试且不拖垮整批 |
| `test_ordinary_goal_is_not_touched_by_the_batch_scheduler` | 普通 Goal 不被 Batch 调度触碰 |

## 5. 已验证不变量

1. 第 N 景进入 `WAITING_SR` 后第 N+1 景可开始，整批不停在首个外部 Job。
2. 调度与检测 IO 分离：检测阻塞期间 SR 状态查询照常执行。
3. 重启后 Step 状态、Job ID 与容量占位保留；已接受作业不重复提交。
4. 外部 `UNKNOWN` 不被解释为未提交；重复未确认转 `BLOCKED`，不是重投。
5. 暂停/取消不杀死外部作业，也从不改写已暂停/取消的 Goal 状态。
6. 同一批内单景失败/阻断不终止其他 Item，终态如实上报。
7. 状态与事件同事务；事件顺序与 Item 版本单调，可直接供 PR F 消费。
8. Batch 不消耗模型轮次与预算（`max_model_calls=0`），普通 Goal 不被饿死。
9. 提交预留前重新核对冻结 PAN/NOSR 与掩码身份；`MASK_READY` 之后的输入漂移不会进入外部提交。
10. 未决提交占满容量且无活动 Job 时，批次收敛为 `BLOCKED` 并落报告，不无限等待，也不释放未知容量。
11. 暂停/取消批次的 Monitor 按 SQL 过滤后的窗口轮转，不会被已收敛控制状态饿死。

## 6. 未覆盖的真实环境边界

- **真实检测算法与掩码质量**：本阶段只有确定性中心框与 Mock 掩码，不验证真实模糊/云检测精度。
- **真实 SR / GPU / Slurm**：只跑 Fake Scheduler；真实 `SR_EXECUTOR=local` 单槽、Slurm 分区与 GPU 绑定未在本阶段验证。生产 `mode` 在 PR C 被显式拒绝。
- **真实 Verifier**：生产验收走既有 `goal_worker.sr_contract` 动态查找，但本阶段没有用真实产物与回执完整跑通；真实环境验收仍属 M4。
- **跨进程/多实例调度**：进程内锁只保证单进程内不重复；多实例并发依赖外部部署约束，未做分布式验证。
- **真实盘阵与量产规模**：未在内网盘阵、50 景与真实大图上验证；`max_attempts_per_item` 已冻结但 PR C 单景只物理提交一次。
- **进度 Panel / HTTP 演示入口**：事件契约已固定，但读数、恢复与控制 UI 归 PR F。
- **提交边界的 TOCTOU**：冻结核对与外部提交之间仍有极小窗口，平台无法原子锁定外部调度器侧的文件。
- **容量死锁的恢复**：只定义人工对账后重新导入；没有自动释放未知容量或自动续跑的机制。

## 7. 复审修复（2026-11-02 第二轮）

复审提出两个 P1 与一个 P2，均已关闭并补测试。

### 7.1 P1：提交边界重新核对冻结输入与掩码

- `_submit_ready()` 在生成参数、预留容量之前调用新增的 `_verify_submission_inputs()`：
  - 重新执行 `_open_item()`，核对 PAN/NOSR 的冻结修订、TIFF 尺寸/波段/样本类型/压缩，并继续拒绝既有 `PAN_old_*.tif`；
  - 核对掩码位于批次工件目录内，重新执行单波段/尺寸/0-255/非空前景检查，并比对冻结 sha256 与前景像素数。
- 任一不一致转为 `BLOCKED`，不预留容量、不调用外部提交。覆盖了等待容量、暂停后恢复、进程重启三个窗口。
- 遗留边界：核对与外部提交之间仍存在极小的 TOCTOU 窗口；平台无法在外部调度器一侧原子锁定输入文件。

### 7.2 P1：未决容量耗尽后的批次收口

- `_settle()` 新增死锁判定：无 `SUBMITTING/WAITING_SR/VERIFYING` 项，且 `capacity_reserved=1` 的 `BLOCKED` 项已占满 `max_inflight_sr_jobs` 时，剩余 Item 已不可能提交。
- 此时不再无限 `WAITING_EXTERNAL`，而是：
  - 取消该 Goal 的未完成检测 IO；
  - 把剩余非收敛项标记为 `SKIPPED`，`exclusion_reason=capacity_deadlock`；
  - 写批次报告与 `report_ref`，Goal 转 `BLOCKED`（`blocked_reason=capacity_deadlock`），发出 `batch_phase_changed` 与 `batch_report_updated`。
- 未知提交的容量位**不释放**。恢复规则：先人工核对调度器/SR 记录；该批次为终态，剩余景重新导入为新批次。

### 7.3 P2：暂停批次 Monitor 查询窗口

- `agent_goals` 新增 `monitor_done` / `monitor_state` 两列（老库由 `_ensure_columns` 补列，默认未收敛）。
- 新增 `Store.list_batch_monitor_goals()`，在 SQL 的 `LIMIT` 之前过滤已收敛的控制状态，按 `updated_at ASC` 取最久未处理的行；即使活动暂停批次超过窗口也会轮转，不再被较新的已收敛批次挤出。
- `claim_goal_monitor()` 在领取时清 `monitor_done`，领取后崩溃可重试；`_process_batch_monitor()` 在同一事务里回写列与 Checkpoint。
- 监控路径仍不改写 Goal 的 `PAUSED/CANCELLED` 状态。

### 7.4 重启完成超时

复审所指的 `test_two_jobs_http_disconnect_and_hard_backend_restart` 超时的根因是夹具与真实修复契约不一致，不是 PR C 的调度逻辑：

- 夹具的 `meta.xml` 写了 `<SolarAzimuth>1</SolarAzimuth>`，真实契约判定为 SC，本体应是 `<场景目录名>.tif`；
- 但 `ExternalServices.finish()` 按 RC 写 `PAN.tif` 与 `PAN_old_latest.tif`，回执路径因此永远匹配失败，Goal 反复重试到预算耗尽后 `FAILED`。

夹具改为空 `<SolarAzimuth></SolarAzimuth>`（RC，本体 `PAN.tif`），与回执一致；原验收断言未改。单跑该用例通过。

### 7.5 组合套件的计时抖动

重启用例修好后，组合套件仍偶发 `test_goal_runtime.py::LoopContinuationTests::test_durable_model_request_timeout_and_sdk_retries_obey_slice` 失败（`create.call_args` 为 `None`），单跑该用例则通过。定位结果：

- 该用例用 `max_seconds=0.25`、`llm_deadline_margin=0.1`，即首个模型请求的实际余量只有 0.15s。
- `async_transport.request()` 里 `httpx.AsyncClient(...)` 会先被求值；这台 Windows 机器上首次构造 `AsyncHTTPTransport` 要惰性 import `httpcore`，冷启动约 0.2s。它落在轮次计时内，负载高时把余量吃穿，`request` 在发出请求前抛 `DeadlineExceeded`，`run_loop` 返回 `BUDGET_YIELD`。
- 与 PR C 调度逻辑无关；属于既有请求路径的首调用开销，只是被 0.15s 的紧预算放大。

修复：`backend/agent/async_transport.py` 在模块导入时（请求计时之外）预热一次 `httpx.AsyncHTTPTransport`，与同文件既有「平台信任链在计时外加载」的处理同一思路。用例断言未改。

修复后：`test_goal_runtime.py` 连跑 5 次均 31 passed；六套件组合运行 **189 passed**（104.93s）。

## 8. 下一步

- PR D：真实 Detector / MaskBuilder，保持 `detect` 与 Mask 契约不变。
- PR E：真实 SR 生命周期与独立 Verifier 接入，复用本阶段 Intent、回执与 UNKNOWN 对账。
- PR F：按 §2.5 事件契约实现进度 Panel、跟随与暂停/继续/取消。
