# 批量修复 PR E 实施记录：SR Submit / Monitor / Verifier（2026-11-02）

> 状态：**E1–E4 已实施；50 景 Fake SR 验收通过（并发等待、独立完成、正确对账与汇总、重复提交 0、未验证成功 0）。**
> 仅证明 demo/Fake SR 链路，不证明真实 SR/GPU 修复质量；production 批量执行仍被显式拒绝。
> 依据：[1023.md](../planning/1023.md) §E 与 §K；前序见 [batch-pr-c.md](batch-pr-c.md)、[batch-pr-d.md](batch-pr-d.md)（PR D 第三轮复审已通过）。
> 基线：`main` @ `dcb4437`，本机 Windows + `.venv` Python 3.12.14。未执行真实 SR/GPU，未扫描盘阵，未读写真实生产数据。

## 1. 范围与复用

PR E 只补“外部作业生命周期”的判定与异常收敛，不新建第二套提交服务。全部路径复用既有组件：

| 能力 | 复用 |
| --- | --- |
| 提交 | `run_sr.submit_run_sr()`；`reserve_item_submission()` 先写 Tool Intent + `sr_tasks` 意图，再调用 |
| 状态 | `run_sr.query_job_status()`（Slurm/本地共用；终态读作业自己的 verdict 文件） |
| 幂等 | `sr_tasks` 指纹 + Tool Intent；`_reconcile_submitting()` 只复用/回退/阻断，从不盲投 |
| 契约 | `goal_worker.sr_contract()` → `SR_code/variants/verify_sr_run.py::check_contract()` |
| 汇总 | Goal Verifier `_verify_batch()`；完成必须经 `complete_verified_goal()` |

禁止项仍然成立：没有第二套 submit 服务、没有独立状态判定逻辑、没有用 LLM 轮询 SR 状态。

## 2. E1 提交前持久化与稳定身份

该顺序在 PR C 已实现，本轮只补验收证据：

1. 校验冻结 PAN/NOSR/Mask 与 SR 参数（`_prepare_submission`，IO 线程）。
2. 提交边界第三次核对输入身份（`_submit_ready`）。
3. 同一事务内预约容量并写 Tool Intent（`batch:{goal}:{item}:a{attempt}:{fingerprint}`）。
4. `submit_run_sr` 在外部副作用前写 `sr_tasks`（`job_id=None` 意图行），副作用后写 Job ID。
5. Item 转 `WAITING_SR`，释放 Producer。
6. 回执丢失/进程崩溃：`_reconcile_submitting()` 先查 `sr_tasks`/Intent；能确认 Job ID 就 adopts，能证明未产生副作用就回 `MASK_READY`，否则 `BLOCKED` 并保留容量。

`_submit_layer()` 在验证时再次确认：Item 的 Job ID 与 `sr_tasks.job_id` 一致，缺失或错位都不算提交成功。

## 3. E2 Monitor 不调用 LLM

- 每个调度周期先处理到期 Monitor，再推进最多一个 Producer；`query_job_status()` 是唯一事实源。
- `Running/PENDING`：保持 `WAITING_SR`，更新下次唤醒时间。
- `Completed`：转 `VERIFYING` 并执行分层验证；`Failed/CANCELLED/TIMEOUT`：终止层失败。
- `UNKNOWN`：连续 3 次后 `BLOCKED`，保留容量，要求人工核对。
- 查询本身抛错：首次 + 3 次重试（退避 1/2/4 秒），第 4 次连续失败停放 `BLOCKED`；任一次查询成功即清零故障周期。
- 轮询不增加 `rounds_used`/`model_calls_used`，也不由 SSE 连接驱动。

## 4. E3 Verifier 分层

`batch_worker._verify_layers()` 固定五层，每层状态为 `passed / failed / skipped / not_applicable / unverified`：

| 层 | demo（Fake SR） | production（当前未准入） |
| --- | --- | --- |
| `submit` | Job ID 存在且与 `sr_tasks` 一致 → `passed` | 同左 |
| `terminal` | `COMPLETED` 且退出码 0 → `passed`；否则 `failed` | 同左 |
| `contract` | `not_applicable`（Fake SR 不产生 SRLOG/判定文件） | `goal_worker.sr_contract()` 复用部署版 verifier |
| `artifacts` | `not_applicable` | 契约给出的输出 tif 必须存在、非空；策略跳过则 `skipped` |
| `quality` | `not_applicable`（MockVerifier 不评估修复质量） | `unverified` → 拒绝成功判定 |

- 任一层 `failed` 或 `unverified` 都使 `verification.ok=False`，Item 只能 `FAILED`。
- `success_scope` 明确写 `demo_contract` 或 `production_contract`；`provenance` 在 demo 固定 `mock`。
- 批量汇总 `_verify_batch()` 不只看 Item 状态：`SUCCEEDED` 项必须持久化 `verification.ok is True` 且 `provenance` 与模式匹配，否则 Goal 不得 `COMPLETED`。
- 报告新增 `success_scope`，每个 Item 带完整 `layers/failed_layers/reasons`。
- production 仍由 `ensure_execution_allowed()` 在提交前拒绝，且质量层 `unverified` 作为第二道保险：即使未来有人绕过提交门，也不会把契约通过误写成真实修复成功。

## 5. E4 恢复与异常隔离

| 要求 | 实现/证据 |
| --- | --- |
| 一景失败不阻断无关 Item | 失败项独立 `FAILED`，容量释放；新测试中 3 景仅 1 景失败，其余 2 景 `SUCCEEDED` |
| 相同错误有限重试并停放 | 查询失败 1/2/4 秒退避，第 4 次 `BLOCKED`；UNKNOWN 3 次停放 |
| 暂停不提交新项、继续监控已提交 Job | PR C 的 `claim_goal_monitor` 受限领取；既有暂停/恢复测试 |
| 取消不默认杀死外部 SR | `_skip_pre_submit()` 只跳过未进入外部窗口的项；已接受 Job 继续监控到终态 |
| 旧 Worker 迟到结果不覆盖新状态 | `transition_goal_item(expected_version=...)` CAS；迟到检测结果按版本/状态丢弃；新测试覆盖取消后到达的检测结果 |
| 恢复遵守 Tool Intent 对账 | `_reconcile_submitting()` 先查账本；既有“接受但无 Job ID”测试证明零重投 |

## 6. 50 景 Fake SR 验收

`test_fifty_scene_fake_sr_jobs_complete_without_duplicate_submits` 不使用 mock 的 `sbatch_submit` / `query_job_status`，而是走真实 `submit_run_sr` + 内存 Fake 调度器（每阶段 100ms）：

- 50 景并发等待：采样到 ≥2 个 `WAITING_SR/VERIFYING` 同时在途（demo inflight=3）。
- 独立完成：观察到至少一个 Item 已 `SUCCEEDED` 时仍有 Job 在途。
- 正确对账：50 景全部接受后丢弃进程内状态、仅凭 `sr_tasks`/Goal 持久化重启，继续监控到全部完成。
- 重复提交 0：Fake 调度器 `sbatch` 计数恰为 50，`sr_tasks` 为 50 行 / 50 个指纹 / 50 个不同 Job ID；完成后继续 tick 不再新增。
- 未验证成功 0：每个 `SUCCEEDED` 的 `verification.ok=True`、`provenance=mock`、`success_scope=demo_contract`、`failed_layers=[]`；报告 `{"SUCCEEDED": 50}` 且 `model_calls_used=0`、`rounds_used=0`。

## 7. 文件

| 文件 | 变更 |
| --- | --- |
| `backend/services/batch_worker.py` | 五层验证器、终止层退出码、查询失败有界退避/停放、成功清零故障周期、汇总复核持久化验证与 `success_scope` |
| `backend/tests/test_batch_worker.py` | 新增 8 项：demo 成功范围、非零退出码、查询失败停放、成功清零、production 质量层拒绝、迟到检测结果、单景失败隔离、50 景 Fake SR 验收 |
| `docs/status/batch-pr-e.md`（新增） | 本记录 |

## 8. 测试

```text
./.venv/Scripts/python.exe -m pytest \
  backend/tests/test_batch_artifacts.py backend/tests/test_batch_worker.py \
  backend/tests/test_batch_manifest.py -q
./.venv/Scripts/python.exe -m pytest backend/tests -q
```

实际结果见 `docs/status/current-question.md` §6.3（本轮更新）。前端未改，沿用既有 628 passed。

## 9. 已验证不变量

1. 提交成功、作业终止、执行契约、产物完整、修复质量是五个独立判定；任何 `failed/unverified` 层都拒绝成功。
2. demo 成功只代表 `demo_contract`，`provenance=mock`；Fake SR / MockVerifier 不产生真实修复成功。
3. production 质量层为 `unverified`，且提交门仍关闭；契约通过不会自动写成真实成功。
4. 批量 `COMPLETED` 前再次核对每个 `SUCCEEDED` 的持久化验证与 provenance。
5. 查询失败最多 4 次连续尝试（含首次），退避 1/2/4 秒；成功一次即清零；UNKNOWN 3 次停放。
6. 停放/未知提交保留容量，不当作空闲，不盲目重投。
7. 同一 Item 的迟到结果受版本/状态 CAS 约束，不覆盖取消或更新的状态。
8. 暂停/取消不领取新 Producer；已接受 Job 继续监控；取消不调用 `scancel`。
9. 50 景验收中物理提交次数 = 不同指纹数 = 不同 Job ID 数 = 50。

## 10. 未覆盖的真实环境边界

- **真实 SR/GPU/盘阵**：全部为 Fake 调度器 + 合成影像；真实作业的契约层/产物层虽已接 `goal_worker.sr_contract()`，未在真实阵列与真实产物上验收。
- **真实修复质量**：没有质量 Verifier；production 质量层固定 `unverified`。真实“修复成功”必须另行准入。
- **production 提交门**：`ensure_execution_allowed("production")` 仍直接拒绝；未做真实并发容量、跨批共享容量或阵列侧对账。
- **Fake SR 产物**：Fake 调度器不生成 SRLOG/输出 tif/verdict，因此 demo 的 contract/artifacts 层只能是 `not_applicable`。
- **Slurm 查询抖动**：查询异常测试用 mock 抛错模拟；真实 `squeue` 超时/退出码路径未在阵列上演练。

## 11. 下一步

- PR F：任务进度 Panel / 演示入口，使用已稳定的事件契约（`item_state_changed`、`mask_ready`、`sr_submission_reserved`、`sr_receipt_recorded`、`external_job_terminal`、`item_verified`、`batch_phase_changed`、`batch_report_updated`、`goal_verified`）。
- 真实 SR 准入必须单独确认：真实 Detector/Mask、真实产物契约、修复质量 Verifier 与 production 提交门同时到位后才能打开。
