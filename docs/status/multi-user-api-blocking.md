# 多用户切页等待：API 响应与预览调度

日期：2026-11-09。代码与合成并发回归通过；内网多用户切页实测待核。

## 已确认的问题

场景解析、清除预览、隐藏/恢复场景、提交 SR、生成掩码、写回清单等 POST 接口虽然是 `async def`，但在读取请求体后直接同步读盘、计算或访问数据库。工具直调也直接执行同步 `registry.invoke`。盘阵慢操作会占住共享事件循环，其他用户页面初始化请求与 SSE 也可能延迟。这解释了跨用户等待的一条明确机制，但不等于已排除所有现场卡顿来源。

## 本轮修复

请求体仍异步读取，以上七个接口的同步业务拆到 helper，通过 Starlette `run_in_threadpool` 执行；工具直调也移到线程池。保留请求体、错误码、结果结构及提交幂等逻辑。Store 已使用线程独立连接，广播已支持从工作线程投递；未改变现有执行状态机、后台循环、SSE 引用计数及页面生命周期。

预览、差异分析、掩码与后台预热使用应用级有界调度；同景共享、分档缓存、排队反馈与部署验收见 [multi_user_preview_scheduling.md](multi_user_preview_scheduling.md)。普通同步业务在线程池执行，用户断开不会自动停止已执行的同步写入。不能直接增加 Uvicorn workers，须先处理后台执行与事件广播的跨进程语义。

## 验证

新增 `TestResolveOk.test_slow_scene_resolve_does_not_block_other_users`，在同一 ASGI 事件循环挂起真实解析链路的尺寸读取，要求 `/api/health` 与 `/api/chat/sessions` 在释放读取前返回，随后原解析仍成功。

- 修复后用例通过。
- 将线程池调用替换为原先的内联同步调用，同一用例明确失败：`scene I/O stalled the shared event loop`，证明回归能捕获原问题。
- `test_scene_resolve.py`、`test_api_platform.py`、`test_scene_clear_preview.py` 首轮合计其余 250 passed、1 skipped；新增用例初版因误用 `/api/sessions` 得到 404，改成真实 `/api/chat/sessions` 后单独复跑通过。未把分次执行表述为一次全绿。

部署需要更新后端并重启 `sr-api`，仅更新前端不会生效。内网验收应让用户 A 持续关联/生成预览，同时用户 B 切换场景库、聊天和队列，观察 API 延迟、SSE 间隔、线程池占用、CPU/内存及盘阵 I/O；不能仅凭单用户打开速度判定完成。
