# 面试八股 · 目录与复习路径（interview 系列）

> 日期：2026-09-02 · 状态：草稿（通用八股为子任务产出，项目深挖逐条可核对仓库代码）
> 定位：跳槽面试的功利复习资料。与 [platform-tutorial.md](docs/knowledge/platform-tutorial.md)（帮看懂仓库）互补：**教程讲因果，本系列讲"问到怎么答"**。题目是高频精简版（每主题 15~25 题），先遮答案自测、再看要点、再看追问。

## 本系列文档一览

| 文档 | 主题 | 覆盖 | 状态 |
|---|---|---|---|
| [js-ts.md](docs/knowledge/interview/js-ts.md) | JavaScript + TypeScript | 事件循环/Promise/闭包/原型/类型系统/TypedArray | 子任务产出 |
| [vue.md](docs/knowledge/interview/vue.md) | Vue3 | 响应式 Proxy/render/ref vs reactive/computed/Pinia/组合式 | 子任务产出 |
| [browser-canvas.md](docs/knowledge/interview/browser-canvas.md) | 浏览器 + 渲染 + 内存 + Canvas | URL→渲染/重排重绘/内存与超大数组/Canvas 上限/离线/SSE | 子任务产出 |
| [http-sse.md](docs/knowledge/interview/http-sse.md) | HTTP + REST + SSE/WebSocket | 方法/状态码/幂等/缓存/CORS/HTTPS/推送选型/nginx 反代 | 子任务产出 |
| [python-concurrency.md](docs/knowledge/interview/python-concurrency.md) | Python + 并发/异步 | GIL/线程进程协程/asyncio/FastAPI 两种 def/注入缝 | 子任务产出 |
| [db-storage.md](docs/knowledge/interview/db-storage.md) | 存储 + 数据库 + Redis | ACID/隔离/索引/SQLite/幂等表/Redis 数据结构与缓存 | 子任务产出 |
| [fastapi-rest.md](docs/knowledge/interview/fastapi-rest.md) | FastAPI + REST/SSE 实现 | 路径操作/Pydantic/def vs async def/StreamingResponse/SSE 线程桥/幂等端点 | 自写（含仓库真例） |
| [project-deep-dive.md](docs/knowledge/interview/project-deep-dive.md) | 用本仓库讲项目 | 30 秒稿/难点问答/追问防守/薄弱点/反问 | 自写（含仓库真例） |

## 复习路径（建议顺序）

目标：**简历上怎么写 → 面试怎么开头讲项目 → 被深挖的每一条都能用真实代码撑住 → 基础知识不露怯**。

1. **先讲项目**（简历命中第一问永远是"介绍下你做过什么"）：读 [project-deep-dive.md](docs/knowledge/interview/project-deep-dive.md) 的开场稿与主线脚本，把每个技术点都练到"能对应到一个真实代码文件、一段真实数字"。
2. **按简历技术栈铺基础**：简历写 Vue3/TS → 先 [js-ts.md](docs/knowledge/interview/js-ts.md) + [vue.md](docs/knowledge/interview/vue.md) + [browser-canvas.md](docs/knowledge/interview/browser-canvas.md)；写 Python/FastAPI → [python-concurrency.md](docs/knowledge/interview/python-concurrency.md) + [fastapi-rest.md](docs/knowledge/interview/fastapi-rest.md) + [db-storage.md](docs/knowledge/interview/db-storage.md)；都写 → [http-sse.md](docs/knowledge/interview/http-sse.md) 与其余全过。
3. **自测节奏**：每天 1~2 个主题，先裸答再对答案；答不出的题做记号，二轮只复习记号题。
4. **把"仓库里的题"讲成"项目实践"**：本系列里凡是标了"仓库真例/实践注记"的题，都是你在追问环节能把八股变成**真实经历的钩子**——背八股的人只会说理论，你能说"我在项目里真遇到过"。这是最大的区分度。

## 配套素材（含真实数字，讲项目不虚）

- 教程（因果全貌）：[platform-tutorial.md](docs/knowledge/platform-tutorial.md)
- 踩坑档案（真实 bug 细节）：[gui-experience.md](docs/experience/gui-experience.md)
- 平台决策与阶段：状态 [current-question.md](docs/status/current-question.md)、阶段需求 [frontend-phase4-phase5-prompts.md](docs/planning/frontend-phase4-phase5-prompts.md)
- API 契约（阶段5 评审中）：[api-contract.md](docs/planning/api-contract.md)

> 用法提醒：面试题本质是**沟通题**——每题的"答"按 30~60 秒讲完练习；讲项目的"数字/取舍"背到脱口而出。题库覆盖不了的地方，随时可让 Claude 扮演面试官出题、你作答、它点评。
