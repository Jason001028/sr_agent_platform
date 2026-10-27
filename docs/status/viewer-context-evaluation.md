# Viewer 消息状态感知：审计与评测报告

日期：2026-10-27。范围：消息级观察和只读工具，沿用原 Agent loop、ToolRegistry、SQLite 与 Durable Runtime。

## 审计结论

- Vue Viewer 的 activeRec 来自 activeId；selectedIds 是多选勾选，不等于活动影像；panes 是显示格。快照明确分开三个字段，不按文件名猜对象。
- 已有 lib/agentContext.ts 的 buildViewerContextSnapshot 与 backend/contracts/viewer_context.py 是插件只读契约；此前聊天没有传递该结构。AgentChatTab 仅拼文本附文，且本地 TIF 被 sceneId 门槛禁用；Chat Store 仅发送文本和截图。
- 已有 tifDecode probe 与场景打开服务负责元数据。新增投影复用 buildViewerContextSnapshot.image 和 Viewer 中已读元数据，不另开文件、不扫描盘阵、不建立元数据数据库。未知字段保持 null。
- ToolRegistry 原有 search_scenes 的职责是库检索，不是读取 Viewer；新增消息作用域的 get_current_image_info 无参数，不能指定路径、改写文件或提交任务。
- 消息先落库再调用模型。viewer_snapshot 存在原用户消息 payload，模型投影剥离扩展键并转成标准 text block；新回合绑定最新用户消息，恢复时也使用该消息，不读取实时 UI。

## 实现与边界

发送时同步拷贝版本、时间、current、selected_ids、displayed、元数据和附件数量；不序列化 File、画布或 Float32Array。displayed 只包含已有 thumb 的格，未加载者为空。此字段代表渲染输入，不声称做过屏幕截图识别。

服务端限制快照大小和数组规模、删除未知影像属性，以实际收到的附件数量覆盖客户端数字。快照视为不可信观察，不授予文件权限。工具注册表每轮单独构造，闭包深拷贝绑定影像，仍通过原校验、lease fencing、结果预算、trajectory 和工具执行边界。P0 完整性校验未放宽。

轻量系统提示明确：本图=current；元数据无需附件；视觉判断需要像素；库无匹配不代表影像不存在。截图继续走现有保存与水合链路，历史附件不自动归属新影像。当前附件只有数量状态和原附件名称，尚不提供逐张截图的捕获影像 ID；涉及截图与当前影像不一致时需要模型说明或询问，不能当作同图。

Viewer 侧已开放本地 TIF 元数据提问。旧插件 schema_version=1 契约不变；聊天 viewer_context.version=1 是包含多格和附件状态的消息信封，不用于插件上下文校验端点。

## 基线与评测

现有误调用的根因可确定复现：旧消息只有“请给出本图像信息”，没有可绑定影像的结构化信息，工具表没有当前影像查询。测试固定该状态缺失；没有声称复现了某个真实模型反复检索的完整轨迹。

| 检查 | 结果 |
|---|---|
| Agent loop / API / P0 安全与新增绑定回归 | 189 passed，1 skipped |
| 原只读契约与新增消息快照测试 | 13 passed |
| 前端快照、聊天、API 回归 | 见下方最终检查 |
| A→B 切换、旧闭包不变、无截图查元数据、恢复执行 | 确定性测试通过 |
| 真实模型首轮工具选择率、无关搜索次数、回答准确性 | 待测；本机端点请求未产生有效答案，不计算成功率 |

新增 backend/eval_viewer_context.py 可用真实 SR_LLM 配置执行对照，输出每案首工具正确性、无关检索次数、原始回答、错误和人工准确性评分位。命令：`python -m backend.eval_viewer_context --output viewer-eval.json`。固定 metadata、dimensions、visual_without_pixels、search 四类输入。baseline 仅消融快照，工具与策略保持相同，不能解释成历史版本分数。

真实模型验收：在 Viewer 选 A、附件空时问“请给出本图像信息”，首工具应为 get_current_image_info、search_scenes 次数应为 0，回答名称和尺寸等于快照；切 B 再问，答案应跟随 B；A 回合执行中切 B，A 回合仍报告 A；无截图询问条带应明确缺少视觉证据。真实盘阵和多模态模型验收尚未完成。

## 参考与取舍

- [ai-agent-book 第二章 Context Engineering](https://github.com/bojieli/ai-agent-book/blob/main/book/chapter2.md)：在决策点提供结构化观察，区分环境与观察。
- [第四章工具与 ACI](https://github.com/bojieli/ai-agent-book/blob/main/book/chapter4.md)：工具名称、描述和输入边界直接表达职责，使用零参数只读查询降低误用空间。
- [第六章 GUI 与交互](https://github.com/bojieli/ai-agent-book/blob/main/book/chapter6.md)：状态会变化，固定消息观察，区分元数据与像素。
- [第七章 Agent Evaluation](https://github.com/bojieli/ai-agent-book/blob/main/book/chapter7.md)：评估模型与 Harness 组合，报告消融边界，不把测试桩表现当模型指标。
- 本地 pi-1.3.0/packages/agent/README.md 的 transformContext → convertToLlm：借鉴应用状态与模型消息分离；不引入 pi 运行框架。
