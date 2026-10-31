# 磁吸预览倍率控件（2026-10-30）

列表操作胶囊的清空位移修复：FileList侧栏增加 `scrollbar-gutter: stable`，
保留纵向滚动条占位。`test-preview-clear-layout.js` 使用真实Chrome，显式去掉
Puppeteer默认的 `--hide-scrollbars`，用12张本地生成JPEG验证长列表、清空和撤回。
关闭该CSS的对照组复现x=60.5→68（7.5px横移），修复后各状态x=60.5、宽263px一致。
测试使用DOM按钮click触发既有操作，验证布局状态，不代表鼠标命中测试。

`frontend/src/components/PreviewScaleControl.vue` 替代 Toolbar 原生 range，
提供从低到高的 1/16、1/8、1/4、1/2、1:1，仍向 store 发送 div 值。
整体205×32px，96px轨道与44×24px读数胶囊，使用轻量内联层级图标。

点击与键盘操作立即提交倍率，滑块位置、轨道填充和文字以260ms
`cubic-bezier(.22,1,.36,1)` 同步过渡。拖动只更新控件位置，松手才提交最近档位，
避免沿途生成大图。Pointer Capture 支持移出轨道继续拖动，cancel恢复已提交档位。
弱化档位点带工具提示和接近高亮，滚轮180ms节流，Ctrl+滚轮交给浏览器。
具备slider语义、倍率读屏文本、方向键及Home/End，减少动态设置关闭文字位移并缩短动画。

`viewer.ts::setPreviewDiv` 现在重新请求可见盘阵影像的新档位，分屏刷新两侧，
本地未关联影像继续使用既有本地逻辑。加载期间保留旧画面；成功后原子换像素，
按新旧预览尺寸重映射视图，保留归一化可视范围。复用现有请求共享及token校验，
迟到或已移除记录不写入。未增加影像双画布淡入，避免为过渡额外保留大图。

验证：

- TypeScript检查及最终Vite构建通过，保留vendor source map缺失及原有大包提示。
- `batchPreview.test.ts` 11项通过，包括卡片/普通盘阵记录即时换档和视野保持、
  内存淘汰恢复、重复加载、失败重试、删除及A→B→A交错。
- `node .e2e/test-preview-scale.js` 独立真实Chrome通过：点击插值、拖动跟手、
  松手提交、最近档吸附、文字交叉淡入淡出及稳定单标签、键盘边界、滚轮、
  Pointer取消、减少动态、1280px布局。截图为 `.e2e/preview-scale-control.png`。
- agent-browser启动CDP连续失败后，改用仓库现有launchBrowser独立Chrome启动器。
- store图像链使用Mock JPEG验证视图数学关系，尚未进行真实内网盘阵换档时延验收；
  未修改后端预览算法、源影像或部署环境，未提交或推送。
