# Batch microfeedback · sr_agent_platform

四个纯 SVG 矢量动效 + 一份样式参考：

- `tile-scanning-loop.svg`: 检测时低频循环扫描（`DETECTING`）。
- `mask-ready-once.svg`: 掩码就绪单次描边（`MASK_READY`）。
- `sr-received-once.svg`: 后端 SR Job 回执确立后一次性盖章（不是 `SUBMITTING`）。
- `item-verified-once.svg`: 单景结果通过真实验收时一次性写入对勾（`SUCCEEDED` 需核对验收语义）。
- `batch-microfeedback.css`: 卡片一次性高亮与三条快照进度轨样式；仅是可移植参考代码，不能原样取代已有 CSS。
- `preview.html`: 浏览器本地交互原型，状态都是模拟的。

SVG 内含 CSS `<style>`，可直接通过 `<img src="/icon/...">` 使用，不需要动画依赖。单次 SVG 若要重放，可在 Vue 中给 img 设置包含 `goalId:itemId:attempt:eventSeq` 的 key；不要在每次 snapshot 刷新时重播。动效只由真实状态切换触发，历史快照回放不误报新事件。Loop 动效仅当前可见且处于进行态时加载，需通过 `v-if` 卸载暂停；HTML img 包裹元素的 `animation-play-state` 无法可靠控制嵌入 SVG 内的 CSS 动画。支持 `prefers-reduced-motion`。
