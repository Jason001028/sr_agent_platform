# 插件管理（阶段 6）

本阶段把现有 Agent 工具纳入插件控制面。插件目录与执行注册表分离：

- `backend/tools/contract.py` 负责当前进程内的工具执行；
- `backend/services/plugins.py` 负责插件目录、机器目标状态和操作队列；
- `backend/services/store.py` 持久化机器、插件、机器插件状态与操作记录。

当前目录包含四个执行型工具插件，以及一个观测型 UI 插件 `token_visualizer`。
观测型插件不进入 Agent 的 function-calling 列表，只负责消费模型响应里的 usage 数据。

## 状态与接口

机器通过 `X-Machine-ID` 选择插件集合，未提供时使用 `local`。本机内置插件的操作立即完成，
远程机器的操作进入 `PENDING` 队列，由机器 Agent 拉取并回报结果。

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/api/machines` | 机器列表 |
| POST | `/api/machines/register` | 注册或更新机器 |
| POST | `/api/machines/{id}/heartbeat` | 机器心跳 |
| GET | `/api/plugins?machine_id=...` | 插件目录及该机器状态 |
| POST | `/api/machines/{id}/plugins/{plugin}/actions` | install/enable/disable/uninstall |
| GET | `/api/machines/{id}/plugin-operations?claim=true` | Agent 原子领取待执行操作 |
| POST | `/api/plugin-operations/{id}/result` | Agent 回报操作结果 |

`desired_state` 表示后台期望状态，`actual_state` 表示机器回报状态。状态不一致时页面保留
操作记录和错误信息，便于重试与后续回滚。

## 后续接入真实机器 Agent

Agent 应使用机器级认证主动访问接口，下载经过签名和 SHA-256 校验的插件包，在独立虚拟环境
或子进程中安装和运行，完成健康检查后再回报 `COMPLETED`。卸载前应等待运行中的任务结束，
失败时保留旧版本并回报 `FAILED`。
