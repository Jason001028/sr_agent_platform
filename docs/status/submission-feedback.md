# SR 提交反馈（2026-11-04）

实际提交入口包括 QueuePage 和 Viewer。设置新增「自动化任务提交」，默认开启并以 `sr.submit.automatic.v2` 在本机保存。此次版本迁移不沿用旧 `sr.submit.automatic` 的关闭值，之后用户主动关闭仍会保存。开启时用户点击 Viewer「提交 SR」以队列表单默认参数直接提交，留在查看器显示真实回执；关闭时仅预填并导航至队列确认。打开影像或切换开关不会发起提交。

自动提交开关两种模式共用 SRSubmitFeedback 按钮，Viewer 内统一为 32px 高、142px 宽、8px 圆角；正常态保留原图标，不再使用先退出再进入的内容动画，避免短暂空白。关联现有任务回执仅保留标题、任务 ID 和查看入口，不显示后端长篇 notice。新任务的 notice 折叠于「写入说明」，原文未丢失。

## 实现

- `SRSubmitFeedback.vue`：按钮状态、Teleport 回执、定位与关闭生命周期。
- `submissionFeedback.ts`：校验真实 task_id，区分 SUBMITTED 和 RESUMED_ACTIVE / RESUMED_COMPLETED。
- `QueuePage.vue`：绑定提交及任务定位；权威列表出现新行后一次高亮，不改排序或选择。
- `queue.ts`：提交回执到达即返回，后台刷新列表，不把网络异常统一写成失败。
- `api.ts`：仅提交请求增加 30 秒超时，无自动重试。

后端 `platform.submit_queue` 在 `submit_run_sr` 返回后读取已存任务，返回 task_id。该接口确认平台 SR task，非 Durable Goal ID；回执统一标「任务 ID」。不把入队确认解释为 SR 完成或验收成功。RESUMED_* 显示关联现有任务，不触发新增高亮。

422 等明确拒绝恢复按钮并说明原因；断线、超时、5xx、409 进入待确认状态，禁用重提，刷新权威队列并提供查询入口。现接口无请求级幂等查询键，无法仅凭丢失响应安全确认本次请求身份，故不自动恢复成成功或自动重提。

## 时序

按下 90ms / 松开 110ms；内容交替共 150ms；可信回执到达后按钮 260ms 变色，100ms 后回执以 280ms 入场，180ms 后仅在真实任务出现时高亮 750ms。表格行遵循项目约束不做位移。按钮 1.4s 恢复；回执 6s 自动收起（Hover/Focus 暂停，离开后重新计时），退出 220ms。减少动效时停用位移、缩放及装饰动画。

Viewer 直提时，可见的队列导航在真实新增确认后延迟 180ms 执行一次 240ms Pulse，减少动效时省略。查看器回执的「查看任务」由用户主动点击，跳至队列并按 task_id 定位。没有本地计数加一或自动导航。

任务结束沿用 App 常驻 SSE 与 JobNoticeStack：COMPLETED 显示完成提醒，FAILED 显示失败原因，切换页面不影响订阅。提醒依赖后端连接，离线时保留原有断线提示。自动提交使用同一套后端幂等和提交契约，不启动新的执行链。

## 验证

前端 680 项测试通过，typecheck、build 通过。既有 vendor sourcemap 缺失和大 bundle 警告仍存在。

`.e2e/test-submission-feedback.js` 使用独立 Chrome、构建产物与模拟 API，不启动后端/SR。覆盖三种宽度、等待无成功提示、防连点、真实身份、悬停保留、关闭、去重、422、断网、减少动效、路由清理及页面异常检查。

截图与真实浏览器帧序列回放在 `.e2e/artifacts/submission-feedback/`。回放是 HTML 帧序列，非 WebM，时间分辨率有限；截图已人工检查。尚未覆盖真实内网长延迟、实际 409 和 30 秒超时、键盘 Focus 暂停、分屏/折叠布局以及快速不同任务的完整视觉录制；真实 SR/GPU 未运行。
