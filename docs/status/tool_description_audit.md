# 工具描述契约审计

> 日期：2026-10-27 · 状态：已定（真实模型效果、内网 GDAL/GPU 验收待核）

## 范围与证据

审计对象为本地代码实际向模型公开的工具主描述和参数 `description`。
工具名称、参数类型、必填性、枚举、Schema 默认值、执行函数及 `ok/data/error` 结构保持一致。
描述不替代 JSON Schema 校验、Lease、Tool Intent、路径白名单或 Goal Verifier。

本地 `sr_agent.db` 的 `trajectory_events` 没有 `tool_call` 记录，`agent_runs` 为空，
无法据此报告实际调用频率。优先级依据聊天与查看器工作流：当前影像查询/场景搜索 →
SR 提交/状态查询 → 大结果读取；另外逐项检查坏线修复和两个 Skill 读取工具。
未配置 `SR_LLM_BASE_URL`、`SR_LLM_MODEL`、`SR_LLM_API_KEY`，不把默认配置或测试替身当作可用模型端点。

注册与暴露链路：`backend/tools/__init__.py` 导入四个静态工具 →
`default_registry()` 生成独立集合 → `prepare_agent()` 按目录配置注入 Skill 工具 →
`bind_registry()` 绑定本条消息的 Viewer 快照 → `ToolResults.registry()` 注入结果读取工具 →
`run_loop()` 生成 manifest 并按机器插件状态过滤 → 模型客户端收到最终工具列表。
`ToolRegistry.manifest()` 本身不按 `visibility` 过滤，不应据此推断机器当前可调用的工具。

| 工具 | 注册位置 | 普通聊天中的暴露条件 | 副作用等级 |
|---|---|---|---|
| `search_scenes` | `backend/tools/search_scenes.py` | 静态注册；有机器过滤时须插件期望及实际状态均为 enabled | 只读场景检索；磁盘模式递归检索配置根目录 |
| `run_sr` | `backend/tools/run_sr.py` | 同上 | 写任务、意图、作业文件；外部执行、PAN 文件发布，须对账后重试 |
| `sr_job_status` | `backend/tools/sr_job_status.py` | 同上 | 只读查询任务记录、执行器及判定文件 |
| `fix_bad_lines` | `backend/tools/fix_bad_lines.py` | 同上 | 内存计算；指定输出时写文件并可能覆盖 |
| `get_current_image_info` | `backend/services/viewer_context.py::bind_registry` | 每轮注入，即使无快照也公开；不受机器插件过滤 | 只读消息快照 |
| `read_tool_result` | `backend/services/tool_results.py::ToolResults.registry` | 每轮注入，不受机器插件过滤 | 只读已归档结果，不重执行 |
| `read_skill` | `backend/services/skills.py::SkillCatalog.tools` | 配置根目录发现至少一个有效 Skill 时注入 | 有界文本读取 |
| `read_skill_resource` | 同上 | 同上 | 有界文本读取 |

没有额外的内部专用注册工具。`build_config_xml`、`submit_run_sr`、`query_job_status`、
掩码栅格化和 Goal Verifier 是服务或内部函数，不是独立的模型工具。
插件目录中的 `token_visualizer` 是前端可观测性功能，不在工具注册表中。
自定义运行上下文可替换注册表，不能把本表当作所有上下文的固定全集。

无 `store` 或无 `machine_id` 时不执行机器插件过滤；有过滤时以
`desired_state=enabled` 且 `actual_state=enabled` 的插件名称为准。
Skill 工具启用时保留在过滤后的列表中。`disable-model-invocation` 影响 Skill 目录提示，
不是执行权限检查，也不会移除两个通用读取工具。

## 逐项契约与审计结论

| 工具 | 描述审计问题与修订依据 | 模型应理解的契约 | 回归依据 |
|---|---|---|---|
| `search_scenes` | 英文描述缺少计数、截断和默认参数解释；场景文件路径易与提交目录混淆 | 保留当前影像/场景库边界和 Fake 禁用语义；scanned 为场景数，count 为匹配总数，results 受 limit 及模型结果预算约束；各筛选条件同时生效 | `test_search_scenes.py`、契约测试中的实际 manifest |
| `get_current_image_info` | 简短描述没有明确快照冻结、current 与多选/显示格的区别和缺失行为 | 零参数，返回本条消息 current 的已有属性及捕获时间，不查询实时 UI、不授予文件权限；缺失返回错误 | `test_viewer_context.py`、`test_message_viewer_context.py`、冻结快照契约测试 |
| `run_sr` | `mask_path` 宣称省略即整图，与封装实现不符；suffix 容易被理解为最终发布名；GPU、云量、删除开关与 PAN 修复语义需要区分 | 异步 PAN/NOSR 掩码修复；提交回执不等于完成；准确描述推导掩码、内部后缀、最终 PAN 发布、备份、幂等和未知状态对账 | `test_run_sr.py`、`test_pan_repair.py`、`test_execution_safety.py`、Goal 测试 |
| `sr_job_status` | UNKNOWN 被解释为无本地记录，与统一服务及判定文件逻辑不符 | 返回 job_id/active/state/exit_code；UNKNOWN 是证据不足，查询异常是 ok=false；契约终态不替代业务验收 | `test_tool_job_status.py`、`test_run_sr.py`、`test_local_exec.py`、`test_slurm_fake.py` |
| `fix_bad_lines` | 检测邻域不保证全是正常行；检测索引易被误认为修复验收结果；写盘及整图内存限制未说明 | 以行/列中位数和 MAD 检测，用未标记邻域修复；省略输出仅在内存计算，输出可能覆盖且不保证地理元数据；适用于二维灰度，不代表全面视觉质检 | `test_fix_bad_lines.py` |
| `read_tool_result` | 参数缺少描述；字符串页大小、游标不前进及引用可用性容易误解 | 当前会话引用、JSON Pointer、完整元素分页；字符串取 min(limit×100,1000) 字符；单项超预算改读更深路径，不重执行原工具 | `test_tool_result_formatter.py`、结果读取契约测试 |
| `read_skill` | 英文主描述未明确返回内容、文件上限及权限边界；name 无描述 | 从已安装目录按名称读取 SKILL.md；单文件上限 256 KiB，内容仍可能被结果预算截断，不执行脚本 | `test_skills.py`、动态 manifest 契约测试 |
| `read_skill_resource` | 同上，且缺少路径归属和越界说明 | 相对 Skill 目录的 POSIX 路径；限 UTF-8、256 KiB 和目录边界，不执行内容 | `test_skills.py`、动态 manifest 契约测试 |

### SR 的缺省参数与跨工具边界

- `lq_path` 是真实场景目录，不是 `search_scenes.results[*].path` 返回的影像文件。
  工具入口对 lq_path、显式 mask_path 共用盘符映射及路径白名单，拒绝 Fake 占位路径。
- `mask_path` 省略或空串时调用 `derived_mask_path()`，按识别出的输入影像 stem 推导；
  识别失败才退回目录名。PAN 修复场景通常是 `PAN_mask.tif`，不能固定写成 `<目录名>_mask.tif`。
  掩码必须非空，尺寸匹配 PAN 或 NOSR。REST 在缺省掩码不存在时提前拒绝，Agent 工具没有同样的存在性预检，
  可能先提交，再在作业内失败；不存在自动整图回退。
- PAN 修复固定读取 `<目录名>_NOSR.tif`，不能把普通场景解析器的其他 NOSR 候选当作适配器自动支持的输入。
  适配器要求已有 PAN、NOSR、掩码及同景元数据，成功时备份 `PAN_old_2.tif` 并发布 `PAN.tif`。
  已有备份会拒绝执行，暂存文件保留在 Debug 中供诊断。
- `suffix` 首先取显式有效值，其次读生产配置，最后回退 `sr`；它控制内部推理与作业文件命名，
  最终发布仍是 `PAN.tif`。Schema 中 `default=""` 不代表实际向 SR 传空后缀。
- `sr_scale` 省略为 2，写入 SRScale；网络配置也决定推理尺度，最终候选必须匹配现有 PAN。
  不承诺当前 PAN 按该倍数扩展。
- `gpu` 省略为 0，只写 GPUIDS；本机执行器读 SR_LOCAL_GPU，实际选卡取决于执行器及生产脚本。
  `options_yml` 省略或空串取服务的 DEFAULT_OPTIONS_YML，路径不经过盘阵盘符映射。
- `cloud_limit` 省略为 80，推理超限可跳过，但修复适配器拒绝无有效修复产物的跳过结果。
- `delete_ori=true` 被拒绝；false 不关闭成功修复时的 PAN 备份与替换。
  `grid_align` 省略为 true，false 显式写入 GridAlign。
- 幂等以有效参数与可取得的掩码/NOSR 修订信息生成指纹，活动或已完成任务返回复用回执；
  已知失败可能重新提交。输入修订或参数变化可产生新指纹，不能把幂等理解为任意重复提交都没有副作用。
- SR_LOCKED_DIR 是 REST 的额外检查，Agent 工具中没有同样的检查；描述不把它宣称为工具执行边界。
  这些执行差异仅记录为当前代码事实，不在本次描述工作中调整业务实现。

### 状态、结果预算与验收

`sr_job_status` 即使查不到本地任务行也会调用执行器；缺少配置可能导致无法定位判定文件。
本机活动状态为 PENDING/RUNNING，真实终态由判定文件映射为 COMPLETED/FAILED。
取消且没有有效判定文件时本机可返回 CANCELLED；Slurm 活动状态由调度器返回，Fake 分支使用测试状态。
UNKNOWN 的 active 通常为 null，exit_code 为 null；FAILED 的 exit_code 仍可能是 `0:0`，因为契约校验失败不同于进程退出失败。
Goal 的独立产物及目标条件验收仍然必要。

长结果通过 `_tool_result_budget` 明确截断，只有 `full_result.available=true` 才提供可读取引用。
持久化引用按会话隔离，默认保留 30 天，未终结 Goal 可固定其引用；无持久化会话的引用只在当前运行有效。
归档上限或存储异常可导致引用不可用。读取前脱敏，不将摘要当作完整原结果。
数组、对象和字符串页面在预算内返回完整元素/键/字符，next_offset 只越过实际交付的数据；
标量返回 value，没有分页游标。单项过大时 shown=0、游标不前进，应下钻路径。

## 验证与待验收事项

`backend/tests/test_tool_description_contract.py` 覆盖八个工具的执行相关 Schema、中文描述及参数说明，
真实 run_loop 的最终 manifest、Skill 动态注入与机器过滤、消息快照冻结、SR 实际缺省归一、JSON Pointer 与分页读取。
测试不比较整段自然语言的固定字符串，Schema 形状则保持严格比较。
`test_pan_repair.py` 还覆盖缺失掩码及跳过推理不能发布 PAN。

七个生产文件经过 Python AST 对比：仅忽略工具主描述和 Schema description 后，与仓库基准 AST 相同。
因此参数处理、执行函数、状态机、返回结构以及安全检查没有通过描述规范化发生改变。

实测命令均在仓库根目录执行：

```powershell
.\.venv\Scripts\python.exe -m pytest backend/tests/test_tools_registry.py backend/tests/test_search_scenes.py backend/tests/test_viewer_context.py backend/tests/test_skills.py backend/tests/test_tool_result_formatter.py backend/tests/test_tool_job_status.py backend/tests/test_fix_bad_lines.py -q
# 120 passed

.\.venv\Scripts\python.exe -m pytest backend/tests/test_tool_description_contract.py backend/tests/test_pan_repair.py -q
# 22 passed

$env:PATH = 'D:\Git\bin;D:\Git\usr\bin;' + $env:PATH
$env:PYTHONUTF8 = '1'
.\.venv\Scripts\python.exe -m pytest backend/tests -q --tb=short
# 1004 passed, 1 failed, 5 skipped；156 subtests passed
```

完整回归仍有一项既有失败：
`backend/tests/test_goal_process.py::GoalProcessAcceptance::test_two_jobs_http_disconnect_and_hard_backend_restart`
等待 Goal COMPLETED 超时。隔离副本保留现有工作树的其他代码，仅将七个描述文件恢复为 HEAD 后，
同一测试仍失败（1 failed，15.80 秒），因此不能归因于本次描述内容。
完整回归不可宣称全部通过，未通过的既有进程恢复验收仍需单独处理。

默认 PATH 下完整回归为 992 passed、13 failed、5 skipped；其中 12 项与误选 WSL 启动器有关。
实测 `_bash_path()` 选中了 WindowsApps 下的 bash.exe，简单脚本执行返回 127。
上述 PATH 仅在测试进程优先选用已有的 Git Bash，没有改动执行器代码或系统配置；
失败项重跑为 12 passed、1 failed，最终完整结果如上。
`git diff --check` 通过；FastAPI/Starlette 的 httpx 弃用提示为依赖警告。

真实模型评测待核：固定用例须覆盖本图元数据、场景库检索、
提交后查询状态、大结果续读；在同一模型、采样配置与测试替身下比较首轮工具选择、无关调用、参数错误、
重复调用及人工复核的最终回答准确性。现有 `backend/eval_viewer_context.py` 对比的是 Viewer 观察是否存在，
不是描述修改前后的配对评测，不能以此证明工具选择改善。
真实盘阵、生产 GDAL/GPU、部署脚本与物理 GPU 选卡行为需要内网验收。
