# 项目文档索引（sr_agent_platform）

> 项目所有 `.md` 文档统一归档于本目录，按类目分目录管理。
> **路径约定**：文档内引用一律用**项目根目录相对路径**（如 `tif_viewer/tif-viewer.html`）；跨文档引用写 `docs/<类目>/<文件>.md`。
> 命名与归档规则依据：[naming-conventions.md](conventions/naming-conventions.md) §2。

## 一、文档间导航速查

- **SR_CODE 生产管线**（问算法 / 调用契约 / Windows 移植环境）：[docs/sr_code/sr-pipeline-overview.md](sr_code/sr-pipeline-overview.md)（算法全览）、[docs/sr_code/sr-pipeline-interface.md](sr_code/sr-pipeline-interface.md)（调用契约）、[docs/sr_code/sr-windows-porting-pitfalls.md](sr_code/sr-windows-porting-pitfalls.md)（移植踩坑 + 环境总结）
- **交接入口**：[docs/status/current-question.md](status/current-question.md) —— 新窗口先读
- **新手指南**（有 Web 基础想系统理解前后端与设计取舍）：[docs/knowledge/platform-tutorial.md](knowledge/platform-tutorial.md) —— 读代码前的首选教程
- **面试八股**（功利向跳槽复习，教程的互补）：[docs/knowledge/interview/README.md](knowledge/interview/README.md) —— 项目已到阶段 5 的窗口可顺手更新
- **踩坑索引**：[docs/experience/gui-experience.md](experience/gui-experience.md)
- **命名 / 归档规则**：[docs/conventions/naming-conventions.md](conventions/naming-conventions.md)

## 二、分类总览

| 类目 | 目录 | 职责 | 现有文档 |
|---|---|---|---|
| 背景知识 | `docs/knowledge/` | 原理 / 学科背景 / 技术实现说明 | [platform-tutorial.md](knowledge/platform-tutorial.md)（从 0 开发教程）、[jpg-export-background.md](knowledge/jpg-export-background.md)、[agent-orchestration-research.md](knowledge/agent-orchestration-research.md)、[llm-serving-gguf-glossary.md](knowledge/llm-serving-gguf-glossary.md)（llama.cpp/GGUF 生态名词 + 本地部署选型）、[interview/](knowledge/interview/README.md)（面试八股 · 8 篇） |
| 需求与规划 | `docs/planning/` | 需求规格、实施计划 | [gui-requirements.md](planning/gui-requirements.md)、[web-plan.md](planning/web-plan.md)（gitignore） |
| 经验与复盘 | `docs/experience/` | 踩坑记录、已验证方案 | [gui-experience.md](experience/gui-experience.md) |
| 规范与约定 | `docs/conventions/` | 命名 / 流程等约束 | [naming-conventions.md](conventions/naming-conventions.md)、[langchain-boundary.md](conventions/langchain-boundary.md) |
| 交接与状态 | `docs/status/` | 当前问题、窗口交接 | [current-question.md](status/current-question.md) |
| SR_CODE 生产管线 | `docs/sr_code/` | SR 超分算法 / 调用契约 / Windows 移植环境（**主题域类目**，见 [naming-conventions.md §2](conventions/naming-conventions.md)） | [sr-pipeline-overview.md](sr_code/sr-pipeline-overview.md)、[sr-pipeline-interface.md](sr_code/sr-pipeline-interface.md)、[sr-windows-porting-pitfalls.md](sr_code/sr-windows-porting-pitfalls.md) |

## 三、怎么新增一个 .md（分类规则）

写新文档前先判断类别，放入对应目录。**判断顺序**：

1. **交接/状态**：记录"当前做到哪、下一步交给谁"？→ `docs/status/`
2. **规范**：约束"新增文件/代码怎么命名、流程怎么走"？→ `docs/conventions/`
3. **需求/规划**：回答"要做什么、按什么里程碑做"？→ `docs/planning/`
4. **经验**：记录"踩过的坑、已验证的方案"？→ `docs/experience/`
5. **其余**（原理 / 背景 / 技术实现说明）→ `docs/knowledge/`

> 类别不确定时：放 `docs/knowledge/`，头部标注「草稿」；需要新增类目 → 在 `naming-conventions.md` §6 登记。

**命名**：`<类目>-<主题>.md`，英文 snake_case，避免中文名与裸日期。
**头部元信息**：首行固定 `标题 / 日期 / 状态（草稿·评审·已定）`。

### 背景知识文档模板（以 [jpg-export-background.md](knowledge/jpg-export-background.md) 为范本）

```markdown
# <功能/主题> 背景知识

> 目标读者 / 一句话摘要 / 阅读顺序建议

## 1. 一句话总结
## 2. 需要的背景概念（按重要程度排列）
## 3. 流程总览（mermaid 图）
## 4. 关键实现与设计取舍
## 5. 常见问题
```

## 四、历史迁移对照（2026-08-29）

原有 6 个文档从根目录 / tif_viewer 迁入本结构（git mv 保留历史；web-plan 用普通 mv，gitignore 不入库）：

| 原名 | 新路径 |
|---|---|
| `current_question.md` | `docs/status/current-question.md` |
| `sr_agent_gui_requirements.md` | `docs/planning/gui-requirements.md` |
| `sr_agent_web_plan.md` | `docs/planning/web-plan.md`（gitignore） |
| `sr_agent_gui_experience.md` | `docs/experience/gui-experience.md` |
| `命名规范.md` | `docs/conventions/naming-conventions.md` |
| `tif_viewer/JPG导出背景知识.md` | `docs/knowledge/jpg-export-background.md` |

> 若在代码 / 脚本 / 其他文档中见到旧文件名，按上表改；`.gitignore` 中的 web-plan 规则已同步为新路径。
