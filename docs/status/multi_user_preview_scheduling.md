# 多用户预览调度与缓存

日期：2026-11-09 · 状态：已定（代码与本地验证；内网性能待核）

## 执行与容量

`backend/services/heavy_work.py` 为每个应用提供 1 个影像执行槽、32 件等待上限。预览、清除预览、差异分析、REST 掩码、REST `fix_bad_lines`、产物预热、Batch 检测/掩码构建/提交前复核共用该调度器；Batch 监控保留自己的执行通道。等待和同景共享使用 asyncio Future，不占普通请求线程。满队列返回 503 与 `Retry-After: 2`。影像任务内部仍可使用既有受限采样线程。清除与生成串行，清除动作本身可能排队。

场景解析有独立的 2 个线程及异步准入；已有预览的读取/校验有另 2 个线程，可以在影像生成期间返回其他档位的缓存。普通查询、控制与 SSE 保留自己的执行通道。后台已有等待时，最多连续通过 4 件交互任务后执行一件后台任务；正在执行的任务不能抢占，产物及 NOSR 预热在同一件任务内串行完成。这是任务类别调度，不是经过鉴权的每用户公平。

同源、同修订、同档位的前台请求共享未完成 Future。`/preview-drop` 在入队前通过独立读取通道检查源目录和当天临时缓存，校验源修订、档位与签名；命中不等待无关重任务。临时缓存查询不创建目录，命中保留 `X-SR-Preview-Fallback: tmp` 和 `no-store`。冷拖入请求的共享键包含实际源、修订、档位和落点。后台、镜像、拖入请求排到执行槽后也会再次查缓存，不重复解码。

HTTP 请求取消只分离等待者。Batch 后台任务使用独立执行句柄：未开始时 `cancel()` 撤出排队任务，已开始则返回 false，原生执行及容量继续由调度器持有。失败释放容量、后续任务继续；应用关闭先排空调度器再关闭 Store。原生磁盘调用无法安全强杀，系统级 I/O 卡死仍需运维处理。

## Batch 拥堵与超时

队列满或关闭使用独立 `WorkCapacityError`；HTTP 契约仍为 503。Batch 检测与提交前复核将其作为调度等待，保留 DETECTING / MASK_READY 和冻结质量证据，通过 `next_wake_at` 以 2、4、8、16、30 秒退避，不标记 BLOCKED/FAILED，不增加 `detection_failures`、`submit_prepare_failures` 或物理提交次数。等待次数单独记为 `io_capacity_waits`。

Batch 记录实际开始及结束时间，`detection_timeout_sec` 只计算函数的实际执行时段。独立冻结配置 `io_queue_timeout_sec` 缺省 300 秒；尚未开始的任务排队过期时从队列撤销，并按容量等待退避，不计业务失败。执行入口还会检查排队截止时间，覆盖任务在两次轮询之间过期后执行槽恰好释放的竞态；过期的检测或复核函数不会读取输入。

已运行的原生任务超时后保留 Future 和记录，直到真实执行结束才丢弃结果并进入执行失败的既有重试策略。等待真实结束期间不启动同项副本；不能通过取消等待来假装释放容量。检测、掩码与提交前复核的 lease/fencing、独立验收和生产提交门保留。

## 同步调用边界

场景解析、清除/隐藏/恢复、SR 提交、清单写回与工具直调在事件循环之外执行。插件写入、会话属性与反馈、场景入库、Goal 创建/导入/澄清/控制/人工复核也通过同步 helper 执行。请求体异步读取，原契约保留。

Goal/Batch 后台候选查询、认领、续租、释放，以及续租时的线程锁等待在工作线程执行。会话标题扫描与 Goal SSE 数据库轮询也在工作线程执行。后台数据库调用在取消时等待原生调用结束，再进入 Store 关闭流程。Store 的线程独立连接、事务、幂等、lease/fencing 保留；线程隔离不消除数据库写竞争。

## 缓存与清除

版本件名为 `v_<24位摘要>_preview.jpg`，身份包括源路径身份、源修订、div、生成规则、JPEG 质量及兼容名称。源修订沿用路径哈希、长度与纳秒 mtime，不声称能识别保留长度及 mtime 的任意原地改写。

`<stem>_preview.jpg` 供场景列表、rasterPreview 和旧静态 URL 兼容使用；每档响应读取对应版本件并核对签名。切回已有档位不重新解码。`/preview`、`ready_only`、`/preview-drop`、差异分析及后台预热共用缓存；配置镜像目录时只探测固定候选并复用相同字节，取图不列举目录。符合签名的兼容件可复制成版本件。

前端保留静态字节的档位/源签名复核，不匹配就取 API 字节快照。静态 URL 的查询参数不能锁定盘上内容。源生成途中变化被拒绝，换版后键改变，旧图不作为新源返回。

场景目录及镜像目录的版本件由现有“当前层 + 名称 + 平台签名 + 非符号链接 + 非场景源”清除规则回收，不在请求期间扫描。盘阵外预览保持隔离缓存根，该根的签名版本件每日浅层清扫、保留 7 天；兼容件保留。拖入临时兜底仍按日期桶清理。场景历史版本由显式清除回收，多档/多修订会增加磁盘占用。

## 等待、连接与指标

`GET /api/scenes/{id}/preview-status?div=N` 只查询 queued / running / ready / failed；未请求或记录淘汰且无缓存时返回 missing。记录在进程内有界保留 128 条，不作为持久作业恢复。源不可访问沿用 404。

前端保留 JPEG 响应契约；等待超过 1 秒后每 1.5 秒查询状态，显示排队或生成文案。状态查询不启动生成，也不重试影像请求。`requestPreview` 在 `pagehide` 取消控制下完整读取响应正文，再返回保留状态码、响应头和字节的本地 Response；调用方的 `blob()` 只读本地正文。响应头到达时不释放取消监听，正文结束或失败才清理监听和状态查询。拖入、普通预览和 ready_only 均使用该生命周期。SPA 查看器常驻 store 与队列 SSE 引用计数、App 常驻订阅、pagehide/pageshow 恢复设计保留。队列 SSE 每 15 秒注释心跳，断开后释放订阅。

`GET /api/work-status` 提供执行数、队列长度、容量、去重数、成功/失败数与最近事件循环延迟。启用 `backend.services.heavy_work` INFO 日志可记录排队/执行毫秒及类别，不记录源路径或请求正文。CPU、RSS 峰值、线程池压力与盘阵读取量需由系统和代理监测。

## 验证与部署

专项：`python -m pytest backend/tests/test_multi_user_work.py backend/tests/test_batch_heavy_queue.py -q`。同一应用与事件循环的真实 ASGI 请求，使用事件/屏障覆盖慢生成时普通查询/控制/缓存可用、44 个慢解析、后台数据库等待、同景共享、取消、失败重试、源变化、多档缓存、镜像/拖入复用、受保护清理、后台防饿死及 SSE 投递/订阅释放。Batch 专项复用真实 Store、Fake SR 与同一 HeavyWork 队列，分别验证检测/复核的容量退避、排队与执行计时、过期撤销及运行超时不重复派发。前端 `api.test.ts` 使用未关闭的 ReadableStream 验证响应头到达后 pagehide 仍中止正文，正文成功后释放监听并保留字节与头信息。

本地分组执行结果（各行独立执行，不相加为全量）：

| 覆盖 | 结果 |
|---|---|
| Batch/队列/缓存组合：batch_heavy_queue、multi_user_work、batch_worker、preview_cache、scene_resolve、preview_scope、scene_clear_preview | 234 passed、3 skipped |
| 含轮询间隙过期检查的最终并发专项：batch_heavy_queue、multi_user_work | 24 passed |
| 前端 api、scene | 124 passed；typecheck、build 通过 |

SSE 启动失败测试只针对 `chat-execution` 线程注入故障，保留锁释放断言；租约续期测试从工作线程用 `call_soon_threadsafe` 唤醒测试事件。两处夹具与工作线程边界一致，不放宽业务断言。

部署：更新后端并重启 `sr-api`；构建并更新 `frontend/dist`、刷新浏览器。保持单进程，不直接增加 workers。现有 nginx `proxy_read_timeout 3600s` 覆盖等待与 SSE。

现场让 A 生成冷缓存大图，B 切换场景库/聊天/队列、打开已有图并观察 SSE；记录 API/代理延迟、work-status、CPU/RSS 与盘阵 I/O。多人同景应只解码一次，多档切回不重生成；A 关页后 B 继续等待，最终活动数回到 0；失败后其他景仍执行并可重试。改写测试源后重新解析取图，核对新尺寸/签名；清除后确认版本件与兼容件回收，源图及无签名 JPG 保留。

真实盘阵、内存预算和带宽仍待内网验收；单任务的 CPU/GIL/内存压力不会因线程隔离消失。未部署生产、未运行真实 SR/GPU。X 盘拖入禁止光标尚未定位；显式 W/X 映射要求见 `docs/status/config_locations.md`。

## 本轮 review 后复核

已复核 4 项修订：Batch 容量不足单独退避、排队/执行分别计时且排队过期撤销、拖入暖缓存与临时兜底缓存绕过重任务槽、图片正文下载结束前保留 pagehide 取消所有权。

本轮独立执行：`test_multi_user_work.py`、`test_batch_heavy_queue.py`、`test_batch_worker.py` 组合 **71 passed**；前端 `api.test.ts` **66 passed**；typecheck 与生产 build 通过。上述是分组验证，不等于全仓测试或内网生产验收。未提交、推送或部署。
