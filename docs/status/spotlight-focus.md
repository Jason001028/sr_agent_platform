# 场景聚焦遮罩（Spotlight Focus，2026-10-30）

在《待修复清单》里选中一行，左侧图组列表中**同景的那组卡**保持清晰并套一圈深松绿描边，
侧栏滚动视口里其余部分（别景的卡、卡间缝、留白）统一压一层遮罩。只作用于左侧列表的
滚动视口：中间画布、顶部导航、右侧侧舱、工具条与弹窗一概不碰。

## 1. 触发与取消

- **选中来源只有一个**：`qc.selName`（清单中选中的行）。点行 / 再点取消 / 当前打开的图自动
  选中 / 关清单 / 换清单，凡改 `selName` 的路径都经 `QcListPanel.vue` 的一个 watch
  → `viewer.setSpotlight(viewer.sceneCardIds(sceneNameCandidates(...)))`，遮罩不会停在另一景。
- **同景的一组卡**：按卡片 `sceneDir` 的末段对清单行的候选集合（`lib/qcbatch.ts` 的
  `sceneNameCandidates`，与后端 `scene_name_products` 同口径），命中的是本体 + NOSR 全部卡片。
- **取消选中 = 再点同一行**：`onRow` 改成开关式，再点已选中的行会 `qc.select(null)` 并
  `clearLights()` + `clearSpotlight()`，点亮与聚焦一起收（与面板里阶段/终态按钮「再点一次取消」
  同一种操作习惯）。这是本轮**有意改动的既有语义**：此前再点同一行会再点亮一次（`litTick` 继续
  加一）。现有 `.e2e/test-manual-scene.js` 的 M6 段只在不同行之间连点（第 2 景 → 第 1 景），
  `litTick + 1` 仍然成立；同连续再点同一行的情况没有脚本覆盖。
- **清单外的取消路径**照旧有效：关清单、导入另一份、切到清单里没有的图，都会清掉 `selName`。

## 2. 层级

都在 `.sidebar` 的局部层叠上下文里（`isolation: isolate`，`FileList.vue`）：

| 层 | z-index | 内容 |
|---|---|---|
| 卡片 / 图文 | 普通流 | `FileListCard` |
| 遮罩层 | 10 | `FileListSpotlight.vue` 的零高度 sticky 锚点 |
| 工具条 | 30 | `FileListTools.vue`（胶囊吸顶） |

遮罩的所有元素 `pointer-events: none`：点卡、拖拽、滚轮全部穿透；卡片上的勾选/收藏/相机键
照常可点。不给查看器父级加 `filter` / `opacity` —— 那会改变整棵子树的包含块或另开层叠上下文。

## 3. 坐标

- 锚点：`.sidebar` 的**首个子元素**，`position: sticky; top: 0; height: 0`，不占列表布局；
  滚动时钉在滚动口顶边。
- 每次同步现场量三样：滚动口视口矩形（`borderBox + clientLeft/Top`）、锚点视口矩形、
  **只量聚焦中的那几张卡**的视口矩形（绝不遍历整列）。
- 镂空窗口换算成遮罩层局部坐标（视口坐标 − 滚动口原点），锚点漂移由
  `translate(origin - anchorRect)` 抵消；遮罩层宽高 = `clientWidth/clientHeight`，多铺 1px
  避免取整造成发丝缝，超出部分由侧栏 `overflow` 裁掉。
- 滚动（rAF 合帧、passive）、窗口 resize、`ResizeObserver`（侧栏自身尺寸）、侧栏折叠/展开、
  列表增删/收藏置顶（`revision`）全部走同一个 `sync()`；只有这一套坐标，不存在内容坐标与
  页面坐标混用。
- 图组完全滚出视口时窗口被裁没，只剩整块遮罩；重新滚回来自动恢复。

## 4. 镂空与遮罩块

毛玻璃靠 `backdrop-filter`，镂空必须**完全不画**那块，否则模糊会渗进窗口边缘、块与块之间
也会留缝。所以不用 `clip-path` / `mask`，而是把「整块 − 若干镂空」拆成互不重叠的矩形块
（`lib/spotlight.ts::buildMaskBlocks`）：单个窗口正好是上/左/右/下四块，多个窗口按网格切分并
合并等高相邻块。

- 卡片合并（`mergeGroupRects`）：纵向间距 ≤ 2 × `SPOTLIGHT_PAD`（4px）且横向相交才合并。
  真实卡片 `margin-bottom: 6px`，同景两张卡稳定合成一个窗口；不同景的卡不合并。
- 窗口外扩 4px：描边（1.5px 深松绿，圆角 14px）落在留白里，不压字、不压图。描边单独画在
  `.spot-ring` 上，不挂在遮罩块上（块边会互相裁，跨缝的描边会断）。
- 实测：一个窗口 4 块，整份遮罩块 + 描边节点数恒为个位数；100 张卡来回换景时只有一个
  `.spot-layer`。

## 5. 三档模式与持久化

右上角设置浮层新增一组三选一（`SettingsPanel.vue`），即时生效并持久化：

- **柔焦毛玻璃**（默认）：`--spot-frost` 40% 雾白 + `backdrop-filter: blur(3px)`；
- **柔和暗化**：`--spot-dim` `rgba(17, 35, 32, 0.32)`，不做模糊；
- **无遮罩**：不铺任何块，只保留选中图组描边。

档位存 localStorage `sr.viewer.spotlightMode`；读写出错（隐私模式）按默认档，不抛错。
store 侧 `spotlightMode` / `setSpotlightMode` 在 `stores/viewer.ts`。

## 6. 动画

- 聚焦窗口移动：rAF 插值 250ms，`cubic-bezier(.22, 1, .36, 1)`（`easeSpotlight` 用二分求解，
  与 CSS 曲线同一条）；可被下一次移动打断，从当前显示位置续走。
- 滚动中不重启计时，只改目标值 —— 窗口始终贴住卡片，不会慢半拍。
- 遮罩整体出现/消失：`<Transition>` 透明度 240ms；离场期间保留最后几何做淡出。
- `prefers-reduced-motion: reduce`：不做插值，窗口直接到位；淡入淡出也关掉。

## 7. 状态与清理

- `spotlightIds` 只存卡片 id；`setSpotlight` 内容不变时**不换数组**（幂等，避免重复触发重算），
  并滤掉已不在列表里的 id。
- `dropRecs` / `removeRec` / `clearRecs` 后调 `pruneSpotlight()`：删掉组里一张卡窗口缩到剩下的，
  删光自然收起。
- 收起侧栏时直接收场（量不到视口），展开后重新落位。
- 纯视觉：不改像素、缓存、选中态或 Agent 状态；`viewer/e2eHooks.ts` 只补了
  `spotlightMode()` / `setSpotlightMode()` / `spotlightIds()` 三个只读/设置钩子。

## 8. 验证

- `frontend/src/lib/__tests__/spotlight.test.ts`（25 条）：合并规则、分块无重叠完备划分
  （网格采样覆盖次数、面积守恒、贴边裁剪）、缓动端点与形状、持久化与坏数据、与 `style.css`
  的令牌双写。
- `frontend/src/stores/__tests__/spotlightStore.test.ts`（5 条）：`sceneCardIds` 按场景目录找卡、
  `setSpotlight` 幂等与过滤、删卡/清空后的剪枝、档位持久化。
- `node .e2e/test-spotlight-focus.js`（真实无头 Chrome，宿主 `frontend/tests/spotlight.html`）：
  真滚动容器 + 真组件 + 真 store，卡片用等高色块。覆盖默认不出现、选中成窗（两张卡合成一个
  窗口、不盖别卡）、分区完备（每个采样点被 0 或 1 块覆盖）、节点不外溢、点击穿透（被压暗的卡
  仍可命中、遮罩层不是命中目标）、工具条在遮罩之上、毛玻璃 `blur(3px)`、滚动跟随、滚出视口
  只剩遮罩、换景后窗口跟过去、三档切换与 localStorage、窗口缩放、收起/展开侧栏、100 张卡
  来回换景节点数 ≤ 8 且只有一个遮罩层、取消选中淡出移除、减少动态立即到位、刷新后读回档位。
  截图 `.e2e/spotlight-focus.png`。
- `npm run check:terms` 通过、`npx vue-tsc --noEmit` 零错误、`npm test` 37 文件 / 565 项通过、
  `npm run build` 通过。

## 9. 未覆盖 / 待核

- e2e 宿主用等高色块代替 `FileListCard`，验的是坐标与分层；真卡片上的多行内容、勾选/收藏
  图标、相机键等细节靠人工看。真实内网盘阵数据本机不可达。
- 拖拽排序若将来改变卡片顺序，窗口跟随依赖 `revision`（`recsOrdered` 的 id 序列）与滚动事件，
  未在拖拽过程中专门验证。
- 浏览器缩放（页面 zoom）随窗口 resize 一起重算，未单独做脚本用例。
- 分屏只是视口尺寸变化，走同一条 `ResizeObserver`/resize 路径。
