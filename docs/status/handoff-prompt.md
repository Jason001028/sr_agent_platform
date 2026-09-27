# 新窗口开工提示词：梳理前后端框架与当前思路

> 日期：2026-09-17 · 状态：已定 · 用法：**整篇复制**粘给新开的 Claude 窗口，不必删减。

---

## 任务

你要产出两样东西：

1. **一份梳理**（先讲给我看，不要写文件）：这个项目的前端框架、后端框架、当前技术路线分别是什么，
   彼此怎么衔接，断点在哪里。
2. **更新 `docs/status/current-question.md`**：等我确认梳理无误后再落笔。

## 协作方式

- 我在内网机 node81-135（CentOS7）上执行命令，你读不到那台机器。需要什么信息就给我一条命令，
  我贴输出回来。不要假设你能直连。
- 我偏好简要回答：先给结论和清单。
- 你只能在这台外网 Windows 开发机上读写代码，仓库根为 `D:\BaiduNetdiskDownload\sr_agent_platform`。
- 改代码前先说方案。这个项目已经跑了很久，很多看着可以简化的地方是有原因的。

## 硬约束

- **不要扫盘**。真实遥感数据在盘阵上，数据量极大。后端代码禁止出现 `ls` / `glob` / `rglob` /
  `iterdir`，只允许 `stat` 用户明确给的那一个路径。测试里有专门的用例把 `os.listdir` /
  `scandir` / `walk` 打成 `AssertionError` 来钉住这条。
- 内网 CDN 不可达，第三方库必须本地 vendor。
- 浏览器单次分配约 2GB，Canvas 面积上限 16384²。

## 先读这些（按顺序）

1. `CLAUDE.md` —— 导航，文档分类与代码主产物
2. `docs/status/current-question.md` —— 交接文档，只写现状：§1 平台是什么 / §2 当前路线 /
   §3 已知未收口 / §4 下一步 / §5 硬约束 / §6 现值速查。变更经过另见
   `docs/status/timeline-archive.md`（历史时间线）、真机勾选见 `docs/status/real-machine-acceptance.md`（验收单）
3. `docs/experience/gui-experience.md` —— 踩坑与硬约束
4. `docs/README.md` —— 文档分类索引
5. `docs/knowledge/platform-tutorial.md` —— 想系统理解设计取舍时再读（从 0 开发教程）
6. 记忆索引 `C:\Users\lenovo\.claude\projects\d--BaiduNetdiskDownload-sr-agent-platform\memory\MEMORY.md`
   —— 一批环境与真机事实，例如"真实文件是无压缩 + 条带 1 行""Slurm 账务未启用""内网机无 yum 源"

## 仓库地图

后端 `backend/`（Python + FastAPI）：

| 目录 | 职责 |
|---|---|
| `api/` | HTTP 层。`app.py`：`/api/scenes` 检索、`/preview` 懒生成 JPG、`POST /api/scenes/resolve`；`platform.py`：`/api/tools`、`/api/chat/*`（REST + SSE）、`/api/queue*`（REST + SSE）、`/api/masks`；`paths.py`：路径白名单与 scene id 编解码 |
| `pathguard.py` | 盘阵路径的唯一真源。盘符映射、前缀白名单、由文件名反推场景目录 |
| `services/` | 流程编排。`scene_search`（场景目录判据）、`preview_jpg`（稀疏采样转 JPG）、`run_sr`（拼 XML 与批脚本）、`slurm`（Slurm 客户端，含假调度器）、`local_exec`（本机直跑执行器）、`mask`（矢量转栅格掩码）、`store`（SQLite 会话与任务） |
| `tools/` | 流程级 agent 工具，只做薄封装、调用 services。`contract.py` 是接口约定与 `@tool` 注册表 |
| `agent/loop.py` | 自写状态机，不引入 langchain / langgraph（见 `docs/conventions/langchain-boundary.md`） |
| `mta_grid/` | 纯算法基础模块 |

前端 `frontend/src/`（Vue3 + TypeScript + Vite）：

| 目录 | 内容 |
|---|---|
| `pages/` | `ViewerPage` 查看器、`ScenesPage` 场景库、`QueuePage` 共享队列、`ChatPage` Agent 对话 |
| `stores/` | `viewer`（查看器总调度）、`scenes`、`queue`、`chat` |
| `components/` | `TifCanvas`、`Toolbar`、`DrawPanel`、`FileList`、`StatusBar`、`ContextPanel`（上下文侧舱）、`ScenePathBar`（盘阵场景栏，两个入口共用） |
| `lib/` | `tifDecode`、`decode`、`source`、`maskgen`、`scene`、`viewMath`、`roiStats`、`agentContext`、`api` |
| `vendor/` | 第三方库，本地内置 |

部署 `deploy/`：`nginx.conf`（静态 dist + `/api/` 反代，SSE 关缓冲）、`sr-api.service`（systemd）、
`README.md`（CentOS7 部署手册）、`requirements-api.txt`。

## 当前路线

「提交 SR」的实现方式是：后端在 node81-135 上用 SR 生产 conda 解释器直接起进程，不走 Slurm。
Slurm 那条线 2026-09-14 已中止，代码、文档、验收清单保留为存量。

现状：提交链路、conda 环境、单卡绑定都已由真机实测打通。当前卡点是需要一个未超分过的场景，
才能真跑出产物（现有场景已被 `already SRed before` 判定，提前返回）。

数据来源有两棵树：

- `SR_SCENES_ROOT`（datahub），场景库检索用；
- 真机生产树 `/DiskArray/GSHC2IMPS/PRODUCT/<年>/<月>/<日>/<卫星型号>/<段级目录>/<景级目录>`。

生产树上的场景可以不搬数据直接在查看器打开、画掩码、就地提交，靠 `pathguard.py` 从文件名反推路径。
反推规则见 `docs/sr_code/production-scene-naming.md`。

## 写 `current-question.md` 时的要求

- 不用 emoji，不带情感色彩，术语统一，不用黑话。
- 实测、推测、待核三种措辞分开写，不把推断写成事实。
- 不重复文档里已有的内容。该文件是交接文档，**只写「现在是什么」**：§1 平台是什么 / §2 当前路线 /
  §3 已知未收口（阻塞 / 待真机确认 / 已记录暂不处理）/ §4 下一步 / §5 环境与硬约束 / §6 现值速查。
  变更**经过**按日期追加进 `docs/status/timeline-archive.md`，不要写回本文件。
  你要做的是补进这一轮之后的状态，不是重写全书。
- 改完告诉我改了哪几节、每节加了什么。

## 第一步

先读上面第 1 至 4 项，然后用不超过 20 行告诉我：前后端的模块划分、当前路线、你判断的断点在哪。
我确认之后你再动 `current-question.md`。
