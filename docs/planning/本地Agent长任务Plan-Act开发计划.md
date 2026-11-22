# 本地 Agent 长任务 Plan–Act 开发计划

**状态：本地实现与合成验收已推进，生产 SR 待验 · 2026-11-22。** 首条落地管线为遥感影像逐景修复。交付目标是用户从 Viewer 点「Agent协助」，在侧舱看到一份持续更新的 Todo。Agent 根据真实回执选择工具，提交一景 SR 后立即获得处理下一景的机会。每个成功结束的 Job 都产生一项有据可查的效果回检任务。

## 1. 交付场景与完成定义

用三景说明交付体验。A、B 有候选缺陷，C 无候选。用户在 Todo 顶部核对范围和排除项，点击「开始执行」。Agent 检测 A，按计划准备 Mask；需要区域复核时，用户可从 A 行直接进入 Viewer。A 提交 SR 后，A 行显示“修复中”，B 行随即显示“正在检查”。如果 B 已具备提交条件但 GPU 忙，B 保持“等待提交”，Agent 可以继续检查 C。

A 成功结束时，侧舱先出现“A · 等待效果回检”。Agent 完成当前动作后优先回看 A，校验产物，调用传统 CV 效果检查，写回逐区域结论。若有区域“更清晰但出现光晕”，A 行直接显示该结论和「查看区域」。B 可同时使用已释放的 SR 槽。用户暂时离开侧舱、刷新或返回时仍能接着处理。最终 Todo 展示哪些景改善、无明显变化、混合、恶化、证据不足、无需修复、失败或待人工验收。

**完成条件**包括真实 Job、可打开的对应成果、每一景的决策与反馈历史，以及浏览器关闭期间的持续推进。整批的技术完成、CV 效果和人工验收分别统计。用户能看见等待谁、还能做什么、何时需要介入。

## 2. 开工入口与现有代码

| 开工时检查 | 复用位置 | 改造责任 |
| --- | --- | --- |
| 清单交接和会话 | [QcListPanel.vue](../../frontend/src/components/QcListPanel.vue)、[AgentChatTab.vue](../../frontend/src/components/AgentChatTab.vue) | 保留清单解析和冻结顺序，从交接消息创建长任务 Todo。检查自动发送、仅质检部署和旧会话恢复分支 |
| 修复计划及启动 | [repair_application.py](../../backend/services/repair_application.py) | 沿用范围、排除、确认和 Item 身份。让 Todo 的开始按钮调用可信启动入口，创建 Goal 时同时保存执行归属 |
| 模型与工具 | [loop.py](../../backend/agent/loop.py)、[现有 Skill](../../.agents/skills/batch-repair/SKILL.md) | 复用模型派发、工具 Schema、调用结果和请求预算；给本地执行会话挂载专用工具和执行指引 |
| 原有业务推进逻辑 | [batch_worker.py](../../backend/services/batch_worker.py)、[goal_worker.py](../../backend/services/goal_worker.py) | 沿检测、提交、监控、收敛和重启回收路径定位业务函数。共享单步服务，Agent 所属 Goal 的所有领取路径都按归属过滤 |
| 作业、状态与复核 | [run_sr.py](../../backend/services/run_sr.py)、[local_exec.py](../../backend/services/local_exec.py)、[goal_runtime.py](../../backend/services/goal_runtime.py)、[batch_quality.py](../../backend/services/batch_quality.py) | 对接真实执行器、Job 状态、Goal 事件和人工决定 |
| 现有前端投影 | [batchGoal.ts](../../frontend/src/stores/batchGoal.ts) | 复用快照和增量事件传输，为新入口建立 Todo 投影与组件。旧任务按原有呈现恢复 |

业务工具的输入、回执、脚本参考与效果指标见[本地 Agent 修复工具清单](本地Agent修复工具清单.md)。该清单中原先提到的“卡片展示”在本入口由 Todo 行、批次摘要和 Viewer 跳转承接。

## 3. 通用长任务内核

在本地平台服务生命周期内运行 Agent 会话。模型每次只拿一份当前工作视图，决定下一项工作并调用一个业务工具。工具完成一个动作后交回控制权。外部 Job 由配置好的真实执行器运行。这样即使项目的 Goal Worker 与 Batch Worker 都停用，这条管线也能独立跑完。

| 能力 | 内核负责 | 修复管线提供 |
| --- | --- | --- |
| 规划 | Todo 的增补、改序、依赖、等待、恢复、完成和进度推送 | 清单原序号、允许处理的 Item、阶段和审核策略 |
| 决策 | 组装上下文、调用模型、派发动作、存回执、触发下一轮 | 动作描述、可用条件、怎样解释成功与失败 |
| 异步 | 挂起指定 Todo、订阅带关联键的外部事件、到期唤醒 | Job 标识、终态转换、回检动作和真实执行器容量 |
| 展示 | 通用列表、筛选、事件历史、主操作与目标链接 | 阶段中文文案、ROI 定位、成果和 Viewer 复核链接 |

首次实现用一个 Python 管线适配模块与显式注册表即可。建议适配接口为 `available_actions(snapshot)`、`execute(action, subject)`、`on_event(event)`、`summarize(result)`。内核只认识动作、工作对象、依赖和事件引用。PAN、NOSR、Mask、CV 阈值均落在修复管线内。未来第二条管线只需实现这些接口并提供自己的工具与 Skill。

## 4. 本地计划、Todo 与反馈

复用平台 SQLite 作为持久状态源。以下是逻辑字段，优先映射已有 Goal、Item、checkpoint 和事件；迁移前先核对 store。每次动作的业务结果与对应 Todo 变化在同一次本地事务提交。外部 SR 使用已有提交意图与 Job 回执关联。

| 记录 | 必要内容 | 消费者 |
| --- | --- | --- |
| 批次 | Goal、管线类型、执行归属、确认的 Plan 版本、冻结清单、运行／暂停意图 | 执行器和 Todo 顶栏 |
| Todo | 稳定 ID、父项、工作对象、动作目标、顺序、依赖、状态、等待原因、结果引用 | Agent 与前端读取相同版本 |
| 决策与反馈 | 轮次、触发事件、所选 Todo、工具、简短业务依据、回执摘要、时间、下一步或计划改动 | 用户展开行历史；后续 Agent 复盘 |
| 外部作业 | Goal、Item、尝试、Job ID、提交意图、外部终态、下次查询时间 | 事件关联和重启对账 |
| 回检记录 | Job、对应输入／输出／Mask 版本、待执行／执行中／已完成／受阻、报告引用 | 去重、效果回看和终态统计 |

建议 Todo 状态为「待做、可执行、执行中、等待、已完成、受阻、跳过」。详细业务事实保留在 Item。模型可以提出“先查 B”“为 A 增加复查”“等待人工”，由运行器持久化 Todo 增量；完成、提交和验收取工具或用户回执。计划变动保留原景号和稳定 Todo ID。用户调整已确认范围时走现有版本确认；同一范围内换顺序直接体现在 Todo。

## 5. Plan–Act 与异步调度

1. 启动、动作完成、Job 终态、用户复核、恢复指令均可唤醒对应 Goal。运行器先归并到达的事件，再形成一轮工作视图，内容仅含就绪 Todo、等待原因、容量和最近相关回执。
2. 模型选定 Todo、工具及参数，可附一句面向用户的业务依据和计划增量。运行器显示“正在检查第 N 景”，调用工具，把返回的事实及工件引用写入本地。模型无需把历史对话当作执行账本。
3. 提交工具拿到 Job ID 就返回。该 Item 转为等待外部作业，立即唤醒下一轮。默认一个模型动作在执行、一个 SR 在途，CPU 检测和 GPU 修复可以重叠。耗时检测放在现有可用的 I/O 或计算执行线程中，使事件接收和页面更新持续工作。
4. 到达的完成事件先持久化。当前工具返回后优先处理已完成 Job 的回检；GPU 有空槽且已有就绪项时，可先发出一次短提交，再执行待办回检。回检已积压时优先清理最早的一项。人工待审项不占模型或 GPU 槽。
5. 仅剩外部等待时结束本轮并释放模型；由完成事件、人工输入或到期查询唤醒。工具返回输入缺失时记录具体补件；模型连续提出同一动作且状态没有变化时将该项显示为受阻，继续其他可做事项。
6. 技术动作收敛后汇总。仍有待人工决定时，Todo 标明“计算结束，待复核 N 景”。用户完成决定后更新同一 Goal 的最终结果。

## 6. SR 完成事件与逐 Job 回检

**事件来源必须独立于 Batch Worker。** 优先在真实执行器获知终态的位置发布通知。若部署的本地执行器或 Slurm 接口只支持查询，运行器按持久化的到期时间调用现有状态服务，把首次观察到的终态转换为同一类本地事件。模型只在有业务决策时参与。浏览器 SSE 负责展示，服务端负责接收、保存和消费。

| 事件／情况 | 同一 Goal 内的处理 |
| --- | --- |
| 成功终态 | 在同一事务落下终态和该 Job 的回检 Todo，释放在途槽位。绑定 Goal、Item、尝试和 Job；回检行立即可见 |
| 回检可执行 | 先调用 `verify_repair_output`，再由 Agent 调用一次逻辑上的 `assess_repair_effect(item_id, job_id)`，比较本次备份、输出和有效 Mask。逐区域保存改善、混合、恶化、无明显变化或证据不足及定位引用 |
| 产物延迟可见／缺失 | 回检保留为待产物或受阻并显示具体缺项。产物就绪后接续同一 Todo；失败或证据不足的结果可以在 Viewer 查看原因 |
| 失败终态 | 释放槽位，记录作业失败与诊断入口。保留该次尝试供回看；后续新的真实 Job 有自己的回检任务 |
| 重复通知／服务重启 | 按管线、Item、尝试和 Job 唯一关联回检。重复通知读取已有任务；恢复时检查已提交未终态、已终态缺回检和回检未完成三类记录。若计算完成后写回中断，可重算，但同一 Job 只发布一份当前报告 |

部署时用第一份真实报告核对 ROI 和备份身份，检查前后裁片能在 Viewer 同尺度打开。CV 未校准时展示测量值与证据不足原因；人工质量决定保持独立记录。

## 7. Todo 交互规格

- **入口与确认。** 点「Agent协助」后沿用当前清单解析反馈。侧舱出现单列 Todo，顶栏给出范围、排除数和「开始执行」。仅质检能力仍显示其实际可用动作。确认后按钮就地切换为运行控制。
- **列表层级。** 按原始景号显示一级行；展开可见“检测 → Mask → 提交 → 回检 → 人工复核”及各步回执。无候选直接收束。模型改序只更新下一步和执行优先级，用户正在查看的行保持位置。
- **用户最需要看见的状态。** 顶部同时显示“正在检查 B”“A 正在 SR”和待人工数。支持筛选进行中、等我处理、已结束。默认折叠完成项，保留定位入口。窄侧舱用文字和图标共同表达状态，按钮随键盘焦点可操作。
- **人工操作。** 点击「复核区域」「查看成果」进入对应 Item／Job 的 Viewer，返回保留筛选和展开位置。暂停停止发起新业务动作，已发出的 Job 继续被记录；恢复后处理积压回检。
- **历史与重连。** 首次加载读快照，再按事件序号接续；重连获取缺口，重复事件按 ID 合并。模型的计划调整显示一句可读原因。聊天里连续追问进度时聚焦已有 Todo，避免追加多份任务视图。

## 8. 开发工作包及退出条件

| 顺序 | 交付与依赖 | 可复验的退出条件 |
| --- | --- | --- |
| M1 · 通用骨架 | Goal 执行归属、Todo 投影、动作回执、执行 Skill、Agent 唤醒；接入清单和 Todo 确认按钮 | Worker 停用时，从真实入口创建首个 Goal，模型主动选择检测，刷新可还原计划和反馈 |
| M2 · 真实单景 | 提取检测、Mask、提交、验证动作；接 Viewer 的区域复核，真实提交写入 Job | 同一景从冻结清单到真实输出完整可追溯；提交参数对应已通过的区域 |
| M3 · 事件与流水执行 | 执行器通知或到期查询、槽位管理、下一景调度、重启恢复 | A 提交至终态间观察到 B 的检测开始；关闭页面后两个作业可继续；重复终态通知只产生一个回检项 |
| M4 · 效果回检 | 根据工具清单实现传统 CV 工具、报告存储及 Viewer 跳转 | 成功 Job 自动触发验证与效果检查。对真实影像能定位混合或低置信区域，失败 Job 能独立收敛 |
| M5 · 交互与扩展验收 | 完善暂停、插话、窄栏、历史、批次摘要。用另一个简单的只读质检适配器验证通用契约 | 同一 Todo 组件可展示另一管线的等待、反馈和完成。实际修复链路同时通过下表验收 |

M1–M4 可沿一条真实单景纵向推进，M3 加入第二景后检验重叠执行。首版直接接部署环境已配置好的 SR；评估优先采用真实作业时间线和成果。保留现有 Job 关联、确认和产物校验，复用已有功能即可。并发调优、多机调度和独立插件发布留到实测出现需求后排期。

## 9. 验收、效率与交接

| 场景 | 验收证据 |
| --- | --- |
| 单景成功 | 真实 Job ID、输入与 Mask 绑定、验证结果、一次回检报告、人工结论可分别追溯 |
| 多景重叠 | 选取 SR 耗时足够长的 A 和可检测的 B，记录 A 提交、B 检测开始、A 终态三个时间戳，确认 B 检测发生在 A 在途期间 |
| 人工或单景受阻 | A 等审核或缺输入时，B 仍能处理；A 全驳回时记录跳过；待审数量与 Viewer 一致 |
| 重启与重复事件 | 分别在提交后、终态到达后、效果分析中重启，验证同一 Job 继续跟踪、报告能找回、Todo 状态可恢复 |
| UI 端到端 | 从清单按钮操作直至打开成果；窄栏、刷新、离开再返回均不丢任务；新入口呈现动态 Todo |
| 归属与通用性 | 关闭原 Worker 完成实验批次；再开启原 Worker 验证双方各自只推进所属 Goal；通用 Todo 组件中没有 SR 专有状态分支 |

记录每景提交到终态耗时、提交到下一景检测开始间隔、终态到回检开始间隔、可提交但 GPU 空闲的时间，以及每景模型调用数。对同一清单记录原链路与新链路的批次耗时，并单列人工等待。成功终态按 Job 核对回检覆盖，每个成功 Job 必须能找到报告或明确待处理原因。

**后续 Agent 接手方式。** 从第一个未验收的工作包开始，先核对当前工作树与表中代码入口。每次交付在本节追加日期、工作包、变更文件、执行命令、实际结果、真实 Job 或测试证据引用、剩余缺口和下一步。优先运行触及的后端与前端既有测试；异步重叠、终态去重和跨重启回检是本计划需要新增覆盖的回归点。开发机无内网影像时，将已验证的本地行为和待部署验证项分别记录，真实 SR 验收以部署回执为准。

### 2026-11-22｜M1 执行归属与 Todo 数据投影

| 切片 | 变更文件 | 验证命令与实际结果 | Job 或证据 | 未验证项 | 下一项 |
| --- | --- | --- | --- | --- | --- |
| 确认后的 Goal 归属及旧 Worker 隔离 | `backend/services/store.py`、`repair_application.py`、`goal_worker.py`、`batch_worker.py`、`backend/tests/test_repair_application.py` | `.venv/Scripts/python.exe -m pytest -q backend/tests/test_repair_application.py backend/tests/test_store.py backend/tests/test_goal_runtime.py backend/tests/test_batch_worker.py`，167 通过，1 条 Starlette 弃用警告 | 临时 SQLite 关闭重开后仍是同一 Goal，旧生产和监控租约均被拒绝 | 两个旧 Worker 停用后由本地 Agent 自主推进 | 在服务生命周期内接入本地运行器 |
| Todo 快照与逐景状态增量 | `backend/services/batch_snapshot.py`、`store.py`、`repair_application.py`、`backend/tests/test_repair_application.py` | `.venv/Scripts/python.exe -m pytest -q backend/tests/test_repair_application.py -k local_agent_goal`，1 通过，38 未选；`git diff --check` 通过 | 同一 Todo ID 从 PENDING 到 DETECTING，快照状态与同事务事件一致；无真实 Job | Viewer 确认入口、浏览器重连、模型决策及工具回执 | 用运行器选择并触发第一个真实检测动作，再接 Viewer 启动和消费快照 |

这两项属于本地后端验证。Viewer 默认的启动行为仍由旧链路处理。M1 完整验收、M2 至 M5 和部署环境 SR 证据仍待完成。

### 2026-11-22｜M2–M5 本地修复链路与待验项

| 工作包／切片 | 变更文件 | 验证命令与实际结果 | Job 或证据 | 未验证项 | 下一项 |
| --- | --- | --- | --- | --- | --- |
| M2 · Viewer 确认分流、本地 Agent 选择检测和提交 | `backend/api/app.py`、`backend/api/repair.py`、`backend/services/repair_agent.py`、`batch_worker.py`、`frontend/src/components/AgentRepairPlanCard.vue`、`frontend/src/lib/api.ts` | `.venv/Scripts/python.exe -m pytest -q backend/tests/test_batch_worker.py backend/tests/test_batch_quality.py backend/tests/test_repair_application.py backend/tests/test_goal_runtime.py`，142 通过，1 条 Starlette 弃用警告 | 模型协议替身实际返回工具选择；提交复用已确认 Mask 和现有执行器 | 当前会话未配置真实执行器、模型端点或内网影像。检测和 Mask 当前为组合动作。 | 配置生产环境，验证人工部分通过后提交的 Mask 及真实 Job ID |
| M3 · 一次 SR 在途、继续检测、回检恢复 | `backend/services/batch_worker.py`、`backend/tests/test_batch_worker.py` | `.venv/Scripts/python.exe -m pytest -q -s backend/tests/test_batch_worker.py -k local_agent_overlaps`，1 通过 | Fake SR 持久事件秒级时间戳：A 提交 1795351500.2289703，B 检测 1795351500.2817388，A 终态 1795351500.3076987。重建运行器后保留 A Job，A 的终态事件仅一条。 | 独立服务进程重启、实际 GPU 时间线、执行器主动通知 | 在真实 Job 在途及首个成功终态后分别重启服务，核对 Job 与回检状态 |
| M4 · 验证产物后绑定效果证据 | `backend/services/batch_quality.py`、`repair_effect.py`、`batch_snapshot.py`、`backend/tests/test_repair_effect.py` | `.venv/Scripts/python.exe -m pytest -q backend/tests/test_repair_effect.py`，1 通过 | 合成 TIFF 验证只采样有效 Mask 内成对像素，局部变化被排除后差值归零；Fake SR 显示缺产物原因。 | 当前指标为梯度 P98，尚缺 §6 完整模糊／伪影指标、真实阈值校准及由模型显式选择回检。 | 实现配对边缘宽度、过冲、条带、噪声和接缝的可判条件，再测真实 ROI |
| M5 · Todo 筛选、暂停恢复、成果和区域入口 | `frontend/src/components/AgentRepairPlanCard.vue`、`frontend/src/lib/api.ts` | `npm --prefix frontend test -- --maxWorkers=2 src/components/__tests__/AgentRepairPlanCard.test.ts src/lib/__tests__/batchHandoff.test.ts`，21 通过；最后一次修改后 `npm --prefix frontend run typecheck` 和 `git diff --check` 通过。 | 复用批次快照订阅、控制接口与 Viewer 区域复核入口。 | 浏览器窄栏、重连及区域跳转实测，第二条只读管线，完整决策历史和通用适配层。 | 运行浏览器回归，补齐第二条只读管线及 Todo 历史 |

以上为代码接入与本地验证，真实 SR 及 M2–M5 完整退出条件仍待验收。

### 2026-11-22｜M2–M5 后续实现与验收

| 工作包／切片 | 变更文件 | 验证命令与实际结果 | Job 或证据 | 未验证项 | 下一项 |
| --- | --- | --- | --- | --- | --- |
| M2 · 分开检测回执与 Mask 构建，绑定候选区复核 | `backend/services/batch_worker.py`、`repair_agent.py`、`batch_snapshot.py`、`frontend/src/components/AgentRepairPlanCard.vue`、`BatchMaskReviewOverlay.vue`、`frontend/src/stores/batchMaskReview.ts`、`frontend/src/lib/batchDock.ts` | 相关后端回归 190 通过，含单景契约、部分通过 Mask、同 Job 提交恢复、旧 Worker 归属与证据分析；前端相关 Vitest 33 通过，类型检查通过 | 检测、构建 Mask、提交分别选择；Fake SR 回执关联 Item 和 Job；可从待办行请求对应景的候选复核 | 生产 Mask、执行参数、产物与真实 Job ID 待验 | 在部署环境用部分通过的生产景完成第一个 Job |
| M3 · 轮询终态与持久回检，独立进程启动开关 | `backend/api/app.py`、`backend/services/batch_worker.py`、`repair_agent.py`、`backend/tests/test_batch_worker.py` | `.venv/Scripts/python.exe -m pytest -q backend/tests/test_batch_worker.py -k 'local_agent_overlaps or read_only_inspection'`，2 通过；Chrome 启动两个旧 Worker 均关闭的服务后完成只读批次 | Fake SR 事件证明 A 提交 → B 检测开始 → A 终态；本地重建运行状态后保留同 Job 和同一个回检标识 | 真实 Job 在途时硬重启，部署环境重叠耗时和旧链并行耗时 | 设置 `SR_GOAL_WORKER_ENABLED=0`、`SR_BATCH_WORKER_ENABLED=0` 验真实 A/B，随后开启旧 Worker 对照 |
| M4 · 独立验证、效果测量、前后实图导航 | `backend/services/batch_quality.py`、`batch_worker.py`、`repair_effect.py`、`evidence_raster.py`、`goal_runtime.py`、`backend/tests/test_repair_effect.py`、`test_batch_quality.py`、`quality_browser_fixture.py` | `.venv/Scripts/python.exe -m pytest -q backend/tests/test_repair_effect.py`，3 通过；包含上述后端回归的独立验证与回检恢复用例通过；`node .e2e/test-batch-quality.js` 通过 | 合成发布的 Job 42 验证后重开 SQLite，写入同一逻辑回检，人工结论仍为待审。合成窗口覆盖边缘变窄、光晕增加、反向模糊、条带、接缝及错位。Chrome 打开指定区域的备份／成果，验证 5 次人工操作、服务重启、替换产物 409，浏览器错误 0 | 输出未校准的测量值。尚需真实 ROI 阈值、真实遮挡数据和超出单窗预算区域的生产样例 | 取得首份真实报告及带标注的 ROI，对传感器和区域类型校准变化阈值 |
| M5 · 通用 Todo 组件与第二条只读管线 | `backend/services/inspection_pipeline.py`、`repair_agent.py`、`goal_runtime.py`、`frontend/src/components/AgentTodoList.vue`、`BatchTaskDock.vue`、`AgentRepairPlanCard.vue`、`.e2e/test-batch-quality.js` | `npm --prefix frontend test -- --maxWorkers=2 src/components/__tests__/AgentRepairPlanCard.test.ts src/components/__tests__/BatchQualityPanel.test.ts src/lib/__tests__/batchHandoff.test.ts src/lib/__tests__/batchEventSync.test.ts`，33 通过；类型检查、构建通过；Chrome 联测通过 | 模型协议替身凭候选回执把 1、2、3 改为 1、3、2，零 SR 提交。Chrome 在 900 像素视口从狭窄任务侧栏打开 Todo，刷新后两条仍可读。Todo 可同时显示 A 修复和 B 检查、查看逐景决策、筛选待审、跳到指定区域 | 真实模型动作遵循率、Viewer「Agent协助」起点至生产 SR 的整链，以及各种流量下的时延 | 用部署模型与两景盘阵数据走完整验收表 |

后端复跑命令为 `.venv/Scripts/python.exe -m pytest -q backend/tests/test_batch_worker.py backend/tests/test_batch_quality.py backend/tests/test_repair_application.py backend/tests/test_goal_runtime.py backend/tests/test_repair_effect.py backend/tests/test_store.py backend/tests/test_evidence_analysis.py`，190 通过，1 条 Starlette 弃用警告。Chrome 报告记录两项合成 Job、无新增 SR 提交、两条只读 Todo 刷新恢复、浏览器错误 0。

开发机可识别 RTX 5070。当前会话没有部署模型端点及密钥、已配置的生产 SR 工作目录或可用于验收的盘阵清单。本轮未提交任何生产 SR，未取得真实 Job ID。真实验收需要在配置生产执行器的环境中运行，以持久事件、有效 Mask、产物回执和人工标注为准。
