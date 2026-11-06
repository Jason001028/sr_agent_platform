# 影像焕新 · Image Refresh

日期：2026-11-06。前端与只读成果接口已接通，合成影像浏览器验收通过；内网真实 SR 成果待核。

单景提交与 Batch Goal 均复用共享队列：App 常驻 SSE → `stores/imageRefresh.ts` 观察任务与当前工作区场景 → `/api/queue/{task_id}/image-refresh` 验证成果契约与真实源版本 → 已生成预览就绪后提示。预览尚未就绪只显示“成果准备中”；失败、跳过、沙箱成果不使用新成果视觉。每 15 秒仅复核当前工作区关联任务的元数据，补偿 SSE 丢失、预览稍后生成以及任务时间戳未改变的同名文件更新，不触发 SR 或 TIFF 预览生成。

`FileList.vue` 沿用 96px 缩略图、选中、多选与操作按钮，复用 `public/icon/sr-holo-new-{animated,static}.svg` 与 `src/styles/sr-holo-card.css`。新成果使用浅珍珠底、渐变细边、3px 状态条和徽标。当前可见列表最多一张卡扫光；其余静态，隐藏页面、离屏、收起侧栏时停止循环，减少动态效果时切换静态 SVG。新成果卡不沿用普通新增卡的整卡平移动画。分组计数按实际未读成果统计。

预热只针对可见及相邻卡片，单并发读取已生成 JPEG，不解码整张 TIFF；单份后台读取上限 32 MiB，共享 Viewer 的 128 MiB Blob LRU，键含源身份、源版本与预览档位。滚动后取消离开预热范围的请求。下载字节必须具有匹配版本与档位的 JPEG 签名，错误响应不进入缓存，用户仍可重试。

点击后重新确认成果版本，先解码成功再原子替换 Viewer 像素，显示成功后标记已读并在 300ms 内淡出。后台发现与预热不切图，不改 Camera 或 ROI。同组新图打开沿用视野；已有可靠配对仍走原有对比功能。已读按成果版本保存在 `sr.imageRefresh.read.v1`（最近 512 个版本）；已读抑制提醒，但当前记录仍为旧版本时点击仍走新版本加载。

验证：

- `cd frontend; npm run typecheck; npm test; npm run build`。
- `.venv/Scripts/python.exe -m pytest backend/tests/test_image_refresh.py -q`：3 项通过。
- `node .e2e/test-image-refresh.js`：使用构建产物和合成 JPEG，验证未读、批量计数、单卡扫光、静态 SVG 回退、损坏预览重试、显示后已读消退、Camera 保持和已读新版本重新打开。不连接真实后端或启动 SR/GPU。
- 浏览器截图：`.e2e/artifacts/image-refresh-unread.png`、`.e2e/artifacts/image-refresh-read.png`。

内网待核：单景与批量真实作业完成后，现有预览生产链生成匹配源版本的 JPEG，再由本功能发现；验证几十景同时完成、跨页面恢复以及实际 PAN/NOSR 同名覆盖行为。


### 2026-11-06 补充验证

成果查询显式补齐输出 TIFF 头中的 W/H（`_manual_row` 本身不填尺寸），避免预览已就绪却无法在客户端登记。加载成功后卡片改用当前显示像素的缩略图，不再优先沿用旧的公共浏览缩略图。

追加 `node .e2e/test-image-refresh-lifecycle.js` 合成 JPEG + 共享队列 SSE 浏览器闭环：准备态无徽标、就绪两张未读、只播放一张、点击后 Camera/ROI 保持、同名新源版本重新未读、刷新后的版本已读恢复以及 reduced-motion 静态素材；不触发常规 TIFF 预览生成。新版截图位于 `.e2e/artifacts/image-refresh/unread.png`。

本轮验证：前端全量 813 passed，typecheck/build 通过；Image Refresh + Image Diff + Batch Snapshot 后端 54 passed；原 Image Refresh、增补 lifecycle 与差异可视化三个 Mock 浏览器脚本均通过。保留既有 vendor sourcemap 与 chunk 体积提示。
