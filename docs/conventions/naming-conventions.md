# 项目命名规范（sr_agent_platform）

> **定位**：约束**新增**文件、目录与工程代码的命名，保证仓库秩序与可维护性。
> **原则**：只约束新增，存量不强改（见 §5）；文件名/目录名用英文 + 数字 + 下划线，正文内容语言不限。

---

## 1. 三原则

| 原则 | 含义 |
|---|---|
| 可检索 | 文件名用英文 snake_case（git / URL / 跨平台友好），避免中文名与裸日期 |
| 可分类 | 类型后缀明确，一眼区分文档类型 / 代码角色 |
| 可追溯 | 外部导入必登记来源，改动必记录 |

---

## 2. 新增 .md 文档规范

- **位置**：项目文档统一收进 `docs/<类目>/`（根目录不再放散落文档）。类目见下表，新增类目需在本规范 §6 登记。
- **命名**：`<类目>-<主题>.md`，英文 snake_case（可检索、跨平台），避免中文名与裸日期。正文内容语言不限。
- **头部元信息**：每份文档首部固定一行：`标题 / 日期 / 状态（草稿·评审·已定）`。
- **路径约定**：文中引用一律用**项目根目录相对路径**（如 `tif_viewer/tif-viewer.html`）；跨文档引用写 `docs/<类目>/<文件>.md`。
- **文档与代码分离**：参考代码目录内**禁止**放 .md；历史归档文档（如 `重要新增本地文档/` 内的接口/设计文档）属存量豁免，新文档不得再混入。

| 类目目录 | 用途 | 现有文档 |
|---|---|---|
| `docs/knowledge/` | 背景知识 / 原理 / 技术实现说明 | `jpg-export-background.md` |
| `docs/planning/` | 需求规格 / 实施计划 | `gui-requirements.md`、`web-plan.md`（gitignore） |
| `docs/design/` | 设计文档 / 算法规格 | （待定） |
| `docs/experience/` | 踩坑经验 / 已验证方案 | `gui-experience.md` |
| `docs/api/` | 接口契约 | （待定） |
| `docs/status/` | 交接 / 状态 / 当前问题 | `current-question.md` |
| `docs/conventions/` | 规范 / 约定 | `naming-conventions.md` |
| `docs/readme/` | 使用说明 | （待定） |
| `docs/sr_code/` | SR_CODE 生产管线文档（**按主题域归档的例外**） | `sr-pipeline-overview.md`、`sr-pipeline-interface.md`、`sr-windows-porting-pitfalls.md` |

> **主题域例外说明**：`docs/sr_code/` 是唯一按**主题域**（而非按上表"用途"）归档的类目——三份文档同属 SR_CODE 生产管线、相互引用、常被整体调阅（问算法→全览、对接→契约、移植/环境→踩坑）。上表仍按用途分类；此例外在 §6 登记。

---

## 3. 外部导入参考代码规范

- **位置**：统一收进 `reference/` 目录（沿用"只读存档"语义）。
- **只读禁改**：原样导入；确需改动时**另存**副本并在副本头部注明差异，原文件不动。
- **导入登记**：每次导入必须在 `reference/_MANIFEST.md` 追加一行：

  | 字段 | 说明 |
  |---|---|
  | 来源路径 | 原始仓库/机器的完整路径 |
  | 导入日期 | `YYYY-MM-DD` |
  | 版本/日期 | 原版标签或产出日期 |
  | 用途 | 一句话（如"SR 生产主脚本，供抽取纯函数"） |
  | License | 若有，注明；内网导入一律视为不可分发 |

- **目录命名**：`src_<名称>_<YYYYMMDD>`。禁止中文名 + 裸日期（无法排序、无法追溯）。

---

## 4. 平台新建工程代码（sr_agent_web）命名

- **顶层目录**：`backend/`、`frontend/`、`scripts/`、`data/`（运行时数据，gitignore）。
- **Python（backend）**：模块 snake_case；分层为 `api/`（路由）、`services/`（业务）、`mta_grid/`（纯算法）、`tools/`（agent 工具）、`agent/`（编排）；测试 `tests/test_<模块>.py`。
- **前端（frontend）**：组件文件 PascalCase（`TifCanvas.vue`，`<script setup lang="ts">` 内写 TS），非组件模块 camelCase（`tifDecode.ts`）；页面收 `pages/`、复用组件收 `components/`。
- **API 路由**：REST 复数资源，`/api/<资源>[/{id}]`（如 `/api/scenes/{name}/mask`）。
- 完整目录结构与切换点见 `docs/planning/web-plan.md` §4。

---

## 5. 存量豁免清单（不强改）

| 目录 | 性质 | 处理 |
|---|---|---|
| `祖传代码本地阅读/`（含 `0817定版/`、`超分代码0803/`、`重要新增本地文档/`） | 只读历史存档 | 保持原样；其中的 .md 属归档文档，不迁移 |
| `SR_code/` | 生产算法源码 | 保持原样 |
| `langchain-master/` | 第三方 clone | 只读，不纳入本仓库版本管理 |
| `tif_viewer/`、`test-tifs/`、`.e2e/` | 复用 / 测试资产 | 保持原样 |
| `docs/planning/web-plan.md` | 内部实施计划 | gitignore，不入库 |

---

## 6. 变更与登记

- 新增文档类型（§2）、新增外部导入（§3）、新增豁免（§5）→ 在本规范相应章节追加一行并 commit。
- 2026-09-04：登记主题域类目 `docs/sr_code/`（SR_CODE 生产管线文档簇；三份文档自 docs/sr_code 原中文/下划线名改 snake_case 英文名 + 补齐头部元信息 + 相互引用改根相对路径；原 `docs/sr_code/` 下无其它文件）。
- 本规范自身的修订走 git commit 记录，头部日期随修订更新。
