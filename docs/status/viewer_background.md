# Viewer 画布背景 · 2026-11-05 · 已定

中央 Viewer 在设置面板提供极淡微点阵、工程网格（默认）、柔和摄影棚、石墨灰。选择通过 Viewer store 保存到 `localStorage` 的 `sr.viewer.background`；无记录、非法记录或存储不可访问时默认工程网格。

`ViewerBackdrop.vue` 是舞台内的独立 CSS 背景层，固定在各 Pane 的视口坐标系，分屏分别裁剪。舞台使用 `position:relative; isolation:isolate; overflow:hidden`。影像 Canvas 位于背景之上，绘制 Canvas 在影像之上，导航和浮动控件沿用原有层级和事件规则。背景不进入像素渲染、导出或缓存，不参与 Camera 计算。影像透明区域使用跟随影像边界的原始纯色底衬 `#e9edeb`，不显示背景纹理。

网格与微点阵底色 `#e7e9ea`、间距 26px；网格为 10% 灰绿细线，采用 1px 范围羽化以保留约半像素的视觉厚度，避免硬边半像素在 1× 屏幕上完全消失。摄影棚使用低对比度径向渐变，石墨灰为 `#303738`。切换仅改变背景选中类，通过 `--motion-canvas-background:180ms` 淡入淡出；减少动态效果偏好下即时切换。

本地验收入口：

- `npm test`：732 项通过；`npm run typecheck`、`npm run build` 通过。
- `.e2e/test-viewer-background.js`：设置、刷新持久化、空态、宽幅/竖幅影像、单图、分屏、透明像素底衬、ROI、Canvas 身份与像素不变、背景切换不触发影像绘制、视口固定、缩放拖拽、鹰眼、分隔线和减少动态效果。
- `.e2e/test-tour-compare.js`：四种背景下暂停与持续移动交替、共享 Camera、缓存请求数、鹰眼覆盖交集和控制事件。
- `.e2e/test-auto-tour.js`、`.e2e/test-spatial-annotations.js`：原巡览、空间批注/ROI 与高分屏交互回归。

截图存放 `.e2e/artifacts/viewer-background/`。仅使用本地合成影像，生产大图与内网实际屏幕仍待真机确认。
