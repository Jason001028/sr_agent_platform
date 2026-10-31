# P1 批量预览优化记录（2026-10-30）

## 缩略图空白回归修复（2026-10-30）

`FileThumb.vue` 原先在盘阵卡片上禁用Canvas回退，已有 `_thumb.jpg` 缺失或失败时，
即使详细影像已经打开，仍只显示“场景就绪”。现保留轻量图片优先策略，失败或无URL时
使用用户已打开的 `rec.thumb` 绘制小Canvas，不新增详细影像请求。Canvas引用变化时
重新绑定ResizeObserver，像素监听使用post flush，覆盖图片失败后才插入Canvas的时序。
小Canvas绘制后独立保留，不持有原始大Canvas引用；详细像素被预算淘汰后仍显示小图。

`node .e2e/test-batch-preview.js` 真实Chrome回归通过：无URL、404、损坏JPEG、
打开之后缩略图才返回404、正常图片五种情况；前四种检查Canvas实际像素与状态遮罩消失，
并验证尺寸变化重绘及详细像素淘汰后的缩略图保留。原10/50/100卡片入列无详细请求、
预算恢复、截图与分屏store消费测试同时通过。TypeScript检查及最终生产构建通过。
测试图片为本地HTTP夹具，未访问内网生产影像。

## A. 根因与调用链

一键解析由 `frontend/src/stores/qclist.ts:373` 等处调用 Viewer 的批量方法。占位卡经 `fillPendingCard` 补齐权威 SceneRow 与 SceneOpenMeta；NOSR 通过 siblings 返回的身份另外插入，并保留阶段区分。

修改前 `viewer.ts:1769–1785` 的 fetchCardPixels 用 Set 去重，重复调用无法等待；成功后删除 rec.card。`insertSceneCard` 创建的是零字节 File，预算淘汰 thumb/src 后，activate 的 JPG 分支便尝试解码这个空文件。修改前 `viewer.ts:1792–1799` 的 bakeCardPixels 虽不解码，仍通过 fetchSceneJpgShared 下载完整 JPEG，填入 Blob LRU。

## B. 修改清单

- `backend/api/app.py`：增加严格、保守的 thumbnail_url 映射；原 preview GET 增加 ready_only 查询参数，生成与异常语义保留，仅返回就绪 JSON。
- `frontend/src/lib/scene.ts`：SceneRow 增加可选 thumbnail_url。
- `frontend/src/lib/api.ts`：ensureScenePreviewReady 只请求就绪状态；既有详细 JPEG、Blob LRU 和缓存键保留。
- `frontend/src/stores/viewer.ts`：永久保留 card，记录 detailDiv；按记录和档位共享像素 Promise，按场景和档位共享服务端就绪与 Blob 请求；网络返回前后的 token、成员及档位检查阻止旧结果写入；失败释放 inflight，允许重试。
- 审查后收口：批量 bakeCardPixels 仅标记卡片待打开，不再请求 ready_only，也不生成详细 JPEG。详细生成在打开时触发。qclist 保留逐景解析、元数据尺寸校验、NOSR 缺失与错误、停止和成功计数；成功的含义改为“元数据卡片入列成功”，不再包含详细预览生成成功，相关提示同步改为解析卡片。
- 档位竞态：cardFetchInflight 同时保存写入意图 token 与 Promise，仅当该意图仍有效时才复用装载；A→B→A 的最终 A 会取得新 token，但仍复用 sceneJpgInflight 的 A 网络请求。旧意图完成不会删掉新意图，也不会把取消写成失败。
- `frontend/src/components/FileThumb.vue`：盘阵卡用 lazy/async img，contain 保留完整构图，错误局限于卡片占位；本地文件仍走原小 Canvas。
- 新增后端与 store 回归文件 `test_batch_thumbnails.py`、`batchPreview.test.ts`。

## C. 缩略图映射与限制

依据用户补充约定，同景 NOSR、PAN、ORI 可以共用列表缩略图。权威源必须位于带 `<目录名>_meta.xml` 的场景目录；识别本体、PAN、ORI 和 NOSR 命名后，统一取该目录的 `<完整目录名>_thumb.jpg`，存在且解析后在静态根内才返回现有 `/disk-array/` URL。siblings 元数据也携带该字段，经 siblingRow 透传至卡片。不向浏览器新增绝对路径，不开放任意路径端点，不扫描替代文件，不生成缩略图。404 或损坏由 img error 降为占位。

共享仅用于列表浏览，详细预览仍保留每张卡自己的 Scene ID、阶段和数据源。用户本次未授权 SR 产物共享，因此 SR 产物与库外场景继续显示占位；不同目录的同名场景各取自己的缩略图，不互相替代。

## D. 内存生命周期

card 内 row/meta 是永久来源，Scene ID、stage、sceneDir 等身份不随像素淘汰。thumbnail_url 的浏览器图片缓存与详细 Blob LRU 独立。thumb/src/stats 可按原预算释放；再次 activate 从共享 JPEG 来源恢复，而非空 File。detailDiv 记录已装入像素的档位，下次激活时按当前档位校验。

缩略图从不进入详细解码、截图、ROI、Mask、空间批注或 SR。原 afterPixels 仅更新显示该记录的格子；迟到的非活动图不替换活动身份。加载引用计数避免某次重复等待先完成就提前关闭另一张的加载状态。

批量阶段只请求解析与 siblings 元数据，不调用详细预览准备、生成或下载。后端仍可能为尺寸元数据读取影像头，这不等于详细影像采样或解码。ready_only 接口保留兼容，调用它仍会生成预览；它只在打开详细影像时的准备链路使用，绝不能作为“不读取 TIF”的接口理解。

## E. 性能数据与边界

以下为 Vitest/jsdom、Mock API 的实际测量，非盘阵端到端性能，不包含网络、图像可见时间与浏览器峰值：

| 卡片数 | 入列并完成 Mock 批量准备 | 提前详细 JPEG 请求 | 详细 Canvas/Float32Array |
|---|---:|---:|---:|
| 10 | 19.82 ms | 0 | 0 |
| 50 | 44.93 ms | 0 | 0 |
| 100 | 60.28 ms | 0 | 0 |

未建立修改前相同环境基线，不报告提升百分比。冷/热 HTTP 缓存总字节、首图可见时间、点击延迟、内存峰值、长任务次数尚未实测；真实盘阵外网开发机不可达。不能用这些入列数据替代用户要求的完整性能验收。

新增真实 Chromium 测试（本机生产打包代码、本地 HTTP JPEG 夹具，非盘阵数据）：

| 卡片数 | 入列及 Vue 更新 | 首轮观察到缩略图请求 | 详细请求 | 列表 Canvas / 详细驻留 |
|---|---:|---:|---:|---:|
| 10 | 14.5 ms | 10 | 0 | 0 / 0 |
| 50 | 34.1 ms | 23 | 0 | 0 / 0 |
| 100 | 74.7 ms | 23 | 0 | 0 / 0 |

请求数反映固定视口下浏览器懒加载，不是全部滚动完成的数量。HTTP 图片响应为测试拦截夹具；这些数据不能用于推断盘阵吞吐或真实网络总字节。真实 4096² JPEG 经 createImageBitmap、Canvas、Float32Array 装载；256 MB 预算下 A→B→C 淘汰 A 后重开，确认 evicted=true、restored=true、Scene ID 保持 0、占位 File 仍为 0 字节且未被解码。

## F. 回归记录

- `npm --prefix frontend run typecheck`：最终复核通过。
- `npm --prefix frontend test`：最终完整运行 35 文件、533 项通过；随后批量提示文案修改又单独运行 qcbatch 31 项通过。
- `npm --prefix frontend run build`：通过；保留原有 chunk 体积提示。
- `python -m pytest backend/tests/test_api.py backend/tests/test_scene_resolve.py -q`：139 通过。
- `python -B -m pytest backend/tests/test_batch_thumbnails.py backend/tests/test_api.py backend/tests/test_scene_resolve.py -q -p no:cacheprovider`：最终 141 项通过，包含同景 PAN/ORI/NOSR 共享、同名不同目录隔离和根外拒绝，以及 ready_only 原有生成契约。
- `batchPreview.test.ts`：最终 9 项通过，覆盖 10/50/100 入列无准备/详细请求、256/512/1024 MB 三档反复淘汰、重复点击、失败重试、删除中迟到响应，以及 A→B→A 的两种响应顺序。用虚拟 Canvas 尺寸触发真实预算 watcher，不实际分配 GB；图像解码和网络使用 Mock。
- `node .e2e/test-batch-preview.js`：新增独立 Chromium 回归，真实列表组件、JPEG 解码和 256 MB 预算淘汰恢复通过。Agent 截图从已淘汰卡片恢复并保持原活动图、分屏 store 的右格恢复后左格身份与两格像素保留，均通过。该夹具未挂载中央 TifCanvas，不等于完整分屏视觉回归。测试 HTML 位于 frontend/tests，仅专项构建使用，不进入默认生产构建入口。
- `node .e2e/test-platform.js`：失败于 A 段聊天 `工具行 search_scenes ✓ ()` 文本断言；页面实际显示“检索目标场景 / search_scenes / 完成”，尚未运行到 Viewer 验收。未修改无关聊天断言。
- `git diff --check`：通过。

## G. 未完成验收与后续事项

完整平台浏览器 E2E 的聊天断言问题仍未解决；真实 `_thumb.jpg` 视觉效果待内网验收。档位交错已有确定性回归；Blob 缓存命中/未命中的完整端到端组合、坏 JPEG 与本地 TIF/JPG 的浏览器视觉回归仍未逐项覆盖，不能宣称无功能退化或生产恢复率达到 100%。

服务端冷 TIFF 详细预览生成延后到首次打开，仍可能成为首次点击耗时瓶颈；不新增任务系统或缩略图生成服务。所有修改留在工作树，原有未跟踪 `.claude/` 未改动，未执行 reset、clean、commit 或 push。前端 Node 沙箱权限问题通过获准的扩展权限测试执行解决，未修改用户目录权限。
