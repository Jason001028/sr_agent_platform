# Skill 接口

> 日期：2026-10-26 · 状态：已定；内网真实模型验收待核

## 1. 接入方式

平台支持两种用法：Agent 按需读取本地 skill；外部 skill 通过 HTTP 调用平台工具。
Skill 是工作流程与配套文本，工具是可执行能力。加载 skill 不会安装插件、启用被禁用的工具或执行脚本。

设计参考项目内 `pi-1.3.0/packages/coding-agent/src/core/skills.ts` 与
`pi-1.3.0/packages/coding-agent/docs/skills.md`：摘要先进入上下文，正文按需读取，
支持 `/skill:name 请求` 显式调用。平台不依赖 Pi 的 Node 运行时。

## 2. 目录与配置

默认读取项目根目录下 `.agents/skills/`，支持递归发现 `SKILL.md`。
遇到包含 `SKILL.md` 的目录即停止向下发现；该目录下的其他文件作为配套资源。
隐藏目录和符号链接目录不递归。`SR_SKILL_ROOTS` 可覆盖默认目录，多个目录使用服务端
操作系统的路径分隔符：Linux 用 `:`，Windows 用 `;`。空值关闭发现。

```text
.agents/skills/scene-review/
  SKILL.md
  references/checks.md
```

`SKILL.md` 使用 UTF-8，可带 BOM：

```markdown
---
name: scene-review
description: 检索遥感场景并核对处理前提；用户要求检查影像时使用。
disable-model-invocation: false
---
先使用 search_scenes 查找场景。需要细则时读取 references/checks.md。
使用 run_sr 前确认场景、掩码与处理参数，提交后通过 sr_job_status 查询。
```

名称限 64 字符，使用小写字母、数字和单个连字符；不允许首尾连字符。
省略名称时取目录名。描述必填，限 1024 字符。重名保留先发现的一项，其余进入诊断。
非法 YAML、非法字段或不可读文件进入诊断，不中断其他 skill 的发现。
其他 frontmatter 字段不参与权限判定，`allowed-tools` 不会授予工具访问权。

每次 Agent 回合与每个 HTTP 请求读取当前目录，无需重启即可发现 skill 内容变化。
单个文本资源最多 256 KiB；配套资源只能读取 skill 目录内 UTF-8 文件，禁止路径逃逸。
脚本、图片等二进制资源没有自动执行或解码入口。

## 3. 平台 Agent

系统上下文只包含名称与描述。模型通过 `read_skill(name)` 读取正文，通过
`read_skill_resource(name, resource)` 读取配套文本，随后调用现有业务工具。
这两个只读工具加入当前回合注册表，不修改全局工具集合。

用户可发送 `/skill:scene-review 检查这一景`，平台将完整正文与请求放入本轮用户消息。
`disable-model-invocation: true` 的 skill 不出现在自动选择摘要中，仍支持显式命令。
续接会话使用当前 skill 摘要；已持久化的历史消息仍保持原记录。
未知命令名称返回错误，不作为已成功加载处理。

Skill 加载及后续工具调用沿用 Agent 的回合预算、工具调用记录与恢复机制。
本接口不提供通用 shell 或脚本执行工具。真实模型是否正确选择和遵循 skill，需在部署环境验收。

## 4. 外部 skill 的 HTTP 接口

| 方法与路径 | 用途 |
|---|---|
| `GET /api/skills` | 名称、描述、诊断、工具接口地址；不含正文 |
| `GET /api/skills/{name}` | 读取 `SKILL.md` 正文 |
| `GET /api/skills/{name}?resource=references/checks.md` | 读取配套文本 |
| `GET /api/tools` | 当前机器可用工具及 JSON 参数 schema |
| `POST /api/tools/{name}` | 直接调用工具 |
| `POST /api/skills/{skill}/tools/{name}` | 检查 skill 存在后，沿用同一工具调用入口 |

请求带 `X-Machine-ID`，缺省 `local`。机器身份的部署鉴权沿用现有平台约定，
该请求头本身不是身份凭据。被禁用或不存在的工具返回 404；读取资源越界返回 400。
业务结果沿用 `{ok, data, error}`。HTTP 200 不代表业务成功，应同时检查 `ok`。

业务工具包括场景检索、超分提交、任务状态与坏线修复，以 `/api/tools` 的实际清单为准。
场景解析、分档生成预览、掩码和队列操作继续使用
`docs/planning/api-contract.md` 中的 REST 接口，不因 skill 存在而新增同名业务实现。

标准库客户端位于 `scripts/skill-client.py`，无需额外 HTTP 库：

```powershell
python scripts/skill-client.py --base-url http://127.0.0.1:8000 list
python scripts/skill-client.py read scene-review
python scripts/skill-client.py tools
'{"query":"MSS","limit":5}' | python scripts/skill-client.py call scene-review search_scenes --arguments -
```

工具业务失败、网络或 HTTP 失败时客户端以非零状态退出。
部署依赖包含 `PyYAML>=6.0,<7`，内网需随 `deploy/requirements-api.txt` 离线安装。

## 5. 验证

```text
python -m pytest backend/tests/test_skills.py backend/tests/test_agent_loop.py backend/tests/test_runtime_context.py -q
```

测试覆盖摘要与正文分离、手动技能、资源边界、无效配置诊断、Agent 实际加载正文、
外部调用与插件禁用限制。真实 LLM 与内网生产 SR 执行待核。
