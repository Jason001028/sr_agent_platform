# 计划与工具字段

## `prepare_repair`

- `scope` 默认 `workspace`；`current` / `selection` / `workspace` / `draft`（`draft` 跳过排除项）。
- `mode` 只 `production`；`strategy` 默认 `automatic`，`review` 提交前等掩码复核。
- `detection_method` 默认 `ssim`；`smart`=LTD 固定掩码复核；`mask_policy` 默认 `auto`，`existing` 仅单景。

返回 `status` READY/BLOCKED、`targets`（total/excluded/applicable/items/unresolved）、`execution`、`confirmation_required`；`plan_id`/`version` 仅内部引用。

## 计划状态

`READY` 待启动（按 Agent 设置自动启动或等待确认）；`CONFIRMED` 已确认；`STARTED` 已启动/可复用；`EXPIRED` 过期；`SUPERSEDED` 目标或源变化；`BLOCKED` 不可启动。`start_repair` 的 `plan_version` 可省略，服务端按最新计划校验。

## `get_repair_item`

- 状态/作业：`status`、`error`、`excluded`、`exclusion_reason`、`job_id`、`job_status`、`submitted`。
- 检测/掩码/源：`candidate_count`、`detection`、`mask_present`、`mask`、`input_identities`。
- 验证：`verification`、`verification_layers`。
- 审核：`review_id`/`review_version`/`review_status`、`pending_regions`、`approved_regions`、`rejected_regions`、`rejection_reasons`；`verify=true` 时 `review_valid` 与 `review_invalid_reason`，`verify=false` 时为 null。
- 区域：`regions` 仅 `region_id`/`decision`/`reason`，前 20 条，rejected>pending>approved；`region_count`、`regions_truncated`。

不返回几何、审核人、配置或路径。

## `query_batch_repair`

`goal`、`counts`、`progress`、`active_jobs≤20`、`recent_jobs≤10`、`quality`（waiting/rejected、items≤10）、`report`；不含 `session_id` 或绝对路径。

## 语义

`ok=true` 只表示动作完成；`ok=false` 读 `error`；`sr_job_status` 的 `UNKNOWN` 是正常状态，查询服务抛错才是 `ok=false`。
