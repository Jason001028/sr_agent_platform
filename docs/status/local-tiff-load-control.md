# 本地 TIFF 预览闭环、关联保留与暂停控制

日期：2026-11-09。代码与合成浏览器验证通过；真实 4.56 GiB / X 盘联验待核。

2026-11-10 后续：已进一步拆分整图概览、行带回填、完整像素与 JPG 导出，并支持后置 IFD；下文是初版记录。当前行为及剩余边界见 [progressive-tiff-preview.md](progressive-tiff-preview.md)。

2026-11-10：确认工具栏「选择影像」的 change 与画布拖入共用 `addFiles → activate → tryLinkScenes`。选择 TIFF 同样先核对服务端源，命中后使用当前档位的服务端下采样 JPG；只有未命中或预览失败才回退本地。补齐 TIFF 核对成功并装载 JPG 后的 NOSR 后台预热（原先仅 JPG 显示件触发）。同景入口复用已核对的 sceneId：NOSR、当前产物（如 ori）和 old 均由服务端 siblings 返回，存在时按需取得各自的 JPG，不由前端猜路径。ori 仍按已有 suffix 规则取最近任务或配置，不硬编码；缺文件时如实提示。`selectedImage.test.ts` 从真实 Toolbar 的文件 change 事件验证关联与服务端 JPG，并验证三类同景打开；服务端 I/O 与 JPEG 解码在单测中替身，真实盘阵待核。

## 行为

查看器拖入或选择 TIFF 后，仍先尝试服务端按文件名、字节数、三段内容采样核对源文件。核对成功后，源身份与预览像素分别管理：记录 `serverSourceVerified`、sceneId、sceneDir、源修订、W/H、环节和权威 mask 路径；服务端 JPEG 失败不再丢弃已经核对的关联，而从同一份本地 TIFF 生成预览。本地解码尺寸须与已核对元数据一致。SR 能力仍只采用服务端 `sr_capable`，仅预览授权及中间产物不被提升为可提交。

核对失败的本地文件依然保持 sceneId/lqPath 为空，可以完成本地预览与下载，也可使用既有「关联服务端路径」入口；不会从裸文件名伪造盘阵位置。

本地 TIFF 的属性读取、分块/稀疏采样、统计、拉伸和 JPG 编码放入独立 Worker。采样目标沿用用户选择的 div 及既有本地内存预算；Float32 DN 和原始 W/H 保留。额外生成长边不超过 4096 的显示 JPEG，卡片的「本地预览 JPG」可下载。它是加载完成时的显示件，不回写盘阵、不替代原 TIFF、不包含后续拉伸或手绘修改。

## 暂停与停止

- 解码遮罩和手动关联弹窗提供暂停、继续、停止。Esc、切换加载目标、切档、删除正在加载的卡片、离开 Viewer 会停止当前加载。
- `LoadControl` 为每次前台加载持有暂停门、取消信号及阶段状态。已暂停任务保留当前工作进度，继续后不重启本地采样。Worker 在阶段确认与受限读取块之间检查暂停；主线程仍可操作停止按钮。
- Worker 在属性、统计、拉伸、JPG 编码和发布阶段等待主线程确认；稀疏/分块循环合作让出执行权，定时让出限制频率，避免每个采样行增加一个计时器。同步原生工作进行中显示「正在暂停，当前读取或处理块结束后生效」。停止会终止该 Worker，原生磁盘已提交的 I/O 不承诺瞬间消失。
- 本地读取使用 FileReader.abort；停止后不再派发新的读取，也不发布晚到的结果。每次加载独立拥有 busy/遮罩；旧任务 finally 不能隐藏新任务。批量删除同样停止对应任务。多格切档按前台加载顺序处理。
- 服务端关联、预览下载的暂停会撤销该客户端的请求，继续时重新获取对应共享任务或缓存；不能声称暂停了所有人的服务端生成。停止是独立结果，不触发「失败后本地兜底」。共享卡片预取保持共享所有权，暂停等待与停止个人显示不取消其他使用者。
- 暂停耗时不计入 Worker 最终报告的活动耗时。旧画面保留到新结果完成，停止未完成的首次加载时保留卡片，点选可重试。

不支持 Worker/OffscreenCanvas 的旧环境保留原解码流程及合作检查；完整后台计算/硬停止与本地 JPEG 产物针对项目使用的现代 Chromium。此改造未实现整幅任意档位无损瓦片浏览，也未改变服务端无上限 JPEG 生成规则；它为已核对源的本地回退提供独立可靠路径。后置 IFD 的旧前端固定头部解析限制需另行处理，不能宣称所有 TIFF 布局都已覆盖。

## 文件

- `frontend/src/lib/loadControl.ts`：暂停门、停止、可重接的客户端请求。
- `frontend/src/lib/localDecode.worker.ts` / `localDecodeWorker.ts`：真实 Worker 和主线程所有权。
- `frontend/src/lib/source.ts`、`decode.ts`、`tifDecode.ts`：本地读取与计算检查点。
- `frontend/src/stores/viewer.ts`：关联与像素分离、前台任务隔离、结果提交与下载。
- `DecodeOverlay.vue`、`FileList.vue`、`ViewerPage.vue`：操作入口与生命周期。

## 验证

新增 LoadControl 与 Store 测试覆盖暂停继续、暂停时读取不派发、取消不兜底、客户端下载重接、已核对关联保留、未关联不伪造身份、旧任务不能覆盖新任务。

全量前端以 `npm test -- --maxWorkers=2 --minWorkers=1` 执行，82 文件 / 975 项通过；随后增加客户端下载暂停重接和少量收尾后，7 套件 150 项通过，typecheck、生产构建通过。这些是分次结果。默认高并发全量执行曾因测试进程原生 OOM 中断，未改业务断言，降低并行度后完成。

真实 Chromium 验收使用合成的 10001×10001、8bit、无压缩逐行 TIFF（约 95.5 MiB，触发真实 sparse 路由），div=4 保留 2500×2500 自然值；逐一暂停/继续属性读取、稀疏读取、统计、拉伸、JPEG 编码、发布六阶段，核对每次都保留真实关联并生成 JPEG；另验证未关联文件停止后不发布、重试成功且不伪造关联。不存在真实生产 SR/GPU 操作。

复跑浏览器夹具：

```powershell
node .e2e/serve-local-load.mjs
# 另一终端：先启动浏览器并打开生产构建的测试页
agent-browser --session local-load-fix open http://127.0.0.1:5179/tests/local-load.html
$scriptText = [IO.File]::ReadAllText((Resolve-Path .e2e/local-load-acceptance.js).Path)
$encodedScript = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($scriptText))
agent-browser --session local-load-fix eval -b $encodedScript
```

不能直接用 Vite dev 验收本地 UMD vendor：需经过项目既有 Rollup CommonJS 适配；夹具服务因此先构建再静态托管。证据位于 `.e2e/artifacts/local-load-report.json`、`local-load-complete.png`、`local-load-paused.png`。

部署需更新整个 `frontend/dist`（包括 Worker 资源）并刷新；旧 HTML/JS 与新 Worker 不应混用。前端静态策略须允许同源 Worker。内网需用用户的 4.56 GiB 原图验证服务端 JPG 失败后的本地回退、暂停/继续、停止/重试与关联能力，不能用合成用例推定真实 X 盘耗时。
