# 批量修复 PR A / PR B 实施记录（2026-11-02）

> 依据：`bf7fab2:docs/planning/1023.md` 的旧版批量修复计划。本记录只覆盖 PR A（基础设施/契约清障）与 PR B（Batch 骨架/Manifest）。
> 当前统一计划见 [1023.md](../planning/1023.md)：第一部分为 Harness P0，第二部分为 Batch v2；本记录提供 A/B 已实施成果与验收证据。
> 基线：`main` @ `6ac0408`，本机 Windows 10 + Anaconda Python 3.11.3。未执行真实 SR/GPU，未提交、未推送。

## 1. PR A：基础设施与契约清障

### 1.1 放行条件核实

| 放行条件 | 结果 | 依据 |
| --- | --- | --- |
| 保留当前失败复现 | 已保留 | 全量 `test_agent_loop.py::TestOnStepSeam` 5 项仍按旧事件序列断言；未改动 |
| Fake 产物满足原 SR 契约 | 已满足 | `test_goal_process.py` 的 `ExternalServices.finish()` 改为写 PAN 修复证据 |
| 明确 local 重启限制 | 已记录 | 见 §1.3 |
| 不调整 GPU 服务适配测试 | 未调整 | 未改 GPU/Slurm 适配代码与测试 |

### 1.2 夹具契约修正

原夹具写 `<场景>_<Suffix>.tif` 与 `<场景>_SRLOG.txt`，这是普通 SR 布局；修复模式要求：

- 输出 `PAN.tif`，原图备份为 `PAN_old_latest.tif`；
- 日志 `Debug/PAN_SRLOG.txt`，末行 `Run finished.`；
- 回执 `Debug/_SRREPAIR_<sha256(config.xml)>.json`，含 `mode=pan-nosr-v1`、输出/备份/NOSR 路径、三份 `[size, mtime_ns, inode]` 签名、宽高；
- 输出与备份字节数相同、尺寸相同，且回执/日志/输出都不早于 `config.xml`。

`test_goal_process.py` 已按上述契约生成夹具，并把场景输入改为 `PAN.tif` + `<目录名>_NOSR.tif`。修正后，两 Job 重启用例可以走到独立 Verifier 并正常完成，不再因“缺修复回执”停在等待。

另把会话自动标题请求与 Goal 预算计数分开：标题服务使用 `title-model`，不计入 `external.model_calls`。此前标题后台线程与进程测试竞争，会偶发多出一次外部模型请求。

### 1.3 local 重启限制

`backend/services/local_exec.py` 的运行表与单槽锁是进程内状态。作业已提交、进程重启后，如果判定文件还不可读，`status()` 只能回 UNKNOWN；`goal_worker` 对非只读的 UNKNOWN 保守转 BLOCKED，不自动重投。PR A 只确认并保留这个边界，不把它包装成“可自动恢复”。批量 Demo 的跨重启恢复由独立 Fake 账本承担，不能外推到真实 local/Slurm。

### 1.4 容量、NoData、配对边界

- 容量：Fake 可声明并行 3；真实 local 仍是单槽，批量默认 inflight=1。PR B 冻结清单时不承诺并发执行。
- NoData：`preview_jpg.probe_tiff()` 与修复脚本均没有明确的 NoData 有效区契约。Manifest 只记录尺寸/波段/样本类型，不推断 NoData。
- 配对：固定 `PAN.tif` + `<目录名>_NOSR.tif`，要求 `meta.xml`、单波段、同样本类型、NOSR 分辨率更低、PAN 未压缩、无既有 `PAN_old_latest.tif` / `PAN_old_2.tif`。名字命中多个合法目录按配对歧义拒绝。

## 2. PR B：Batch 骨架与 Manifest

### 2.1 新增与修改

| 文件 | 内容 |
| --- | --- |
| `backend/services/scene_resolve.py`（新增） | HTTP 场景反推与批量导入共用的命名→候选→唯一合法目录解析 |
| `backend/services/batch_manifest.py`（新增） | QC TXT 解码/逐行诊断、PAN/NOSR 配对校验、内容寻址冻结、排除规则 |
| `backend/api/app.py` | `resolve_scene()` 改调共享解析服务，行为不变 |
| `backend/services/store.py` | Step 增量列与迁移、Goal 级 `kind` 与迁移、`create_batch_goal()` 原子创建、`list_goal_items()`；`list_goals()` 在 LIMIT 前按 kind 过滤 |
| `backend/services/goal_runtime.py` | `POST /api/goals/batch-imports`、`POST /api/goals` 可信 Batch 分支、`GET /api/goals/{id}/items` |
| `backend/services/goal_worker.py` | 在 PR C 落地前停放 Batch Goal；候选查询直接排除 `kind='batch'`，不让停放行占满窗口 |
| `backend/tests/test_batch_manifest.py`（新增） | 14 项 PR B 验收与回归用例 |
| `backend/tests/test_goal_process.py` | 修复契约夹具、标题请求隔离、子进程就绪预算 20s |

### 2.2 数据与幂等

- `agent_goals` 增加 Goal 级 `kind`（默认 `chat`，批量创建写 `batch`）。`list_goals()` 支持 `exclude_kinds`，过滤条件进入 SQL 的 `WHERE`，在 `LIMIT` 之前生效；`goal_worker.goal_tick()` 只取非 Batch 候选。这样即使有 200 个更新的停放 Batch Goal，普通 Goal 也不会被挤出候选窗口。
- 旧库 `_ensure_columns()` 补列后执行 `_backfill_batch_goal_kinds()`：只挑 `kind='chat'` 且 `checkpoint` 文本含 `batch_pan_repair_v1` 的行，解析 JSON 并确认 `checkpoint.workflow` 等于该值，才改判为 `batch`；不看标题、请求键或状态，普通旧 Goal 仍按 `chat` 读取。该函数在每次打开库时都跑，因此上一版只补列、已把 Batch 行落成 `chat` 的库，下次启动也会被修正，不需要手工改库。
- `agent_goal_steps` 增加 `kind/item_id/checkpoint/version/resource_key/next_wake_at`；`kind` 默认 `tool`，旧行升级后仍按工具 Step 读取。
- 一个 Batch Goal 的所有 Item 在同一个 `BEGIN IMMEDIATE` 事务内写入；任一 Item 非法即整体回滚，不留下可运行的半批。
- `request_key` 已存在且 `batch_content_sha256` 相同则复用原 Goal；不同内容抛 `BatchConflict`，API 返回 409。
- Batch Goal 的 `checkpoint` 全部由服务端根据冻结 Manifest 构造；客户端提交的 `checkpoint` 不参与。
- 冻结工件目录：`SR_BATCH_ARTIFACT_DIR`，未配置时在 SQLite 同目录的 `batch_artifacts/<sha256>/`，保存 `original.bin` 与 `manifest.json`。

### 2.3 清单解析口径

- 与浏览器 `qclist.ts` 一致：UTF-8 BOM、严格 UTF-8、GBK 回退；制表符三列；末尾逗号清理。
- 每一行保留原始行号。重复景名合并到第一次出现并产生 `duplicate_name` 错误；无法解析的行产生 `invalid_columns`；状态行产生 warning，不建 Item。
- 有未排除错误时拒绝创建；排除行后，整行被排除的 Item 记为 `SKIPPED` 并保留原因，不静默改成 `NO_ISSUE`。
- 行列号按原文 `(行, 列)` 保存，PR B 不用它生成掩码。

### 2.4 验收对应

| 验收项 | 用例 |
| --- | --- |
| 50 行冻结 | `test_fifty_line_freeze_is_content_addressed` |
| 无效/重复诊断 | `test_invalid_and_duplicate_lines_are_numbered_and_block_creation`、`test_two_names_for_one_scene_are_a_duplicate_scene`、`test_unresolved_pair_is_a_line_diagnostic` |
| 同请求一个 Goal | `test_store_idempotency_is_content_sensitive_and_atomic`、`test_api_import_create_idempotency_and_items_projection` |
| 同键异内容 409 | 同上 |
| 崩溃无可运行半成品 | `test_store_idempotency_is_content_sensitive_and_atomic` 的失败回滚断言 |
| 旧 Goal/Step 兼容 | `test_old_step_rows_migrate_without_becoming_batch_items` |
| 旧 Batch 行迁移后不抢占普通调度 | 同上：200 条旧 Batch + 1 条普通 Goal，迁移后 `kind` 分别为 `batch`/`chat`，不带 kind 过滤的窗口看不到普通 Goal，带过滤后只返回它；另断言已跑过上一版 chat-only 迁移的库在下次打开时被回填为 `batch` |
| API 导入/创建/Item 投影 | `test_api_import_create_idempotency_and_items_projection`、`test_api_conflicting_exclusions_return_diagnostics` |
| 混合调度不饿死普通 Goal | `test_batch_goals_do_not_starve_ordinary_goals`、`test_worker_requests_non_batch_candidates` |
| 篡改 manifest / 损坏原始文档被拒 | `test_tampered_manifest_and_corrupt_source_are_rejected`、`test_corrupt_original_document_is_rejected` |
| BOM 后非法 UTF-8 返回 400 | `test_bom_with_invalid_utf8_is_a_400_not_a_server_error` |

### 2.5 冻结完整性（Manifest v2）

- 摘要覆盖除 `manifest_id`、`manifest_sha256`、`created_at` 之外的全部字段，包括 `version/workflow/source/mode/config/items/diagnostics`。`config` 在计算摘要前写入，不再出现“先算哈希、后追加执行配置”。
- 发布时先写临时目录并重新校验，再原子替换；目标目录已存在时不直接复用，而是重算摘要并校验 `original.bin`，损坏则拒绝。
- 加载时不仅比对自报的 `manifest_sha256`，还从当前 `manifest.json` 重新计算规范载荷摘要，并要求 `manifest_id`、`manifest_sha256` 与引用一致；`original.bin` 的 SHA-256 必须等于冻结的 `source.sha256`。
- `MANIFEST_VERSION` 升到 2。BOM 后内容不是合法 UTF-8 时抛 `ManifestError`，API 返回 400，不再落成 500。

## 3. 本轮测试结果

以下为本机 Windows 10、Python 3.11.3、`PYTHONDONTWRITEBYTECODE=1` 的实测输出：

```text
python -m pytest backend/tests/test_batch_manifest.py -q -p no:cacheprovider
# 14 passed in 9.64s

python -m pytest backend/tests/test_goal_runtime.py backend/tests/test_run_sr.py \
  backend/tests/test_local_exec.py backend/tests/test_batch_manifest.py \
  backend/tests/test_goal_process.py -q -p no:cacheprovider
# 164 passed in 86.12s
```

复审给出的 targeted 运行是 `146 passed, 17 skipped, 1 failed in 141.94s`，集合数与本轮同为 164 项；本轮 164 全过。差异见 §3.1，失败项的分析见 §3.2。

全量运行中两项进程重启验收均通过，`taskkill` 后不再出现 `process.wait` 超时或 teardown 二次错误；`stop()` 已增加超时兜底 `Popen.kill()`，teardown 用 `finally` 保证日志关闭。全量仍有 5 项 `test_agent_loop.py::TestOnStepSeam` 失败，属于思考强度改动后的 `thinking_status` 事件序列基线，不在 PR A/B 范围，本轮未修。

### 3.1 与审查数字的差异

- 本机 `local_exec.available()` 为 `True`（PATH 中有 `F:\Git\usr\bin\bash.exe`），`test_local_exec.py` 正常执行；审查运行中的 skip 集中在这里及依赖本机路径/门禁的用例，属于运行环境差异，与本轮 A/B 改动没有调用关系。
- 审查运行中唯一失败的进程重启验收已在 §3.2 定位并修复，本轮同一命令下通过。
- skip/fail 数量随运行环境变化，不能把任一次汇总当成稳定基线；本记录只声明本轮命令的实际输出。

### 3.2 重启验收终态事件数量差异

现象：`test_two_jobs_http_disconnect_and_hard_backend_restart` 在 `test_goal_process.py:646` 断言 `external_job_terminal` 数量失败，实际 1、期望 2；同用例其余断言（模型调用 5 次、物理提交 2 次、报告 job_ids、4 个 Step 成功）全部通过。

定位过程：

- 枚举所有能写入 RUNNING 并带终态结果的路径，只有 `goal_worker.reconcile_goal()` 的终态分支会发 `external_job_terminal`；该事件与工具有消息、Intent/Step 更新在同一个 `worker_transaction` 内提交。`_recover()` 只能把 Goal 转成 `WAITING_EXTERNAL`/`BLOCKED`，不产生终态事件。因此只要两个 Job 的完成状态都已被观察到（用例其他断言已证明），两个终态事件就必然落库，不存在“第二个事件丢失”。
- 复现：在测试子类里把 `stop()` 改成延迟 20 秒返回，使 `taskkill` 超时后的 `process.kill()` 兜底路径生效，旧后端进程没有立即退出。它继续按 `SR_GOAL_POLL_SEC=0.05` 每轮追加一条 `external_job_waiting`。原用例用 `store.list_goal_events(gid)` 只读默认第一页（500 行），第二个终态事件落在第 500 行之后，断言因此看到实际 1。原代码在该复现下稳定得到 `AssertionError: 1 != 2`，诊断列表正好 500 行。
- 证据：同一复现、改为分页读全量事件后，断言通过且保持 `== 2` 不变；事件总数 803，两个终态事件 seq 为 403 与 792，第二个在第一页之外。本机正常运行时只有 48 条事件（终态 seq 25/37），所以不做慢停复现时看不到这个问题。

修复：在 `backend/tests/test_goal_process.py` 增加 `goal_events()`，按 `after`/`limit` 翻页读完整事件日志，六处事件读取改用它；`external_job_terminal == 2` 的原断言逐字保留，未放宽。产品侧的事件持久化无需改动——差异出在测试只读到了截断的历史，而不是事件未写入。

此前记录中的 `159 passed`、`1169 passed` 是修复前旧数据，已由本节替换。

## 4. 未做与边界

- 未实现 PR C 的 Producer/Monitor：Batch Goal 创建后保持 `QUEUED`；`goal_worker.goal_tick()` 在候选查询的 `LIMIT` 前排除 `kind='batch'`，不会消耗模型预算，也不会改动 Item。PR C 的 Lease/CAS 与恢复规则仍未开始。
- 未实现 PR D/E/F 的检测、Mask、SR 提交、模拟验收和进度 Panel；前端清单导入按钮与 Goal 状态展示未接线，B 提供的是服务端导入/创建 API。`mode=production` 目前只写入冻结配置，不执行生产。
- 未做 Manifest 垃圾回收、工件下载接口和批次级报告。
- 真实盘阵大图、真实 SR/GPU、local 跨进程强杀接管仍需内网机验收。
