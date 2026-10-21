# 动效令牌规范（sr_agent_platform）

> 日期：2026-10-21 · 状态：已定
>
> **定位**：统一前端动效的时长与缓动，规定每种交互用哪个令牌。
> **适用范围**：`frontend/src/` 下的 `.vue`、`.css` 与查看器驱动代码。
> **不适用**：`tif_viewer/tif-viewer.html`、`quick-look.html` 是独立单页，不引用 `style.css`。
> **相关**：[style.css](../../frontend/src/style.css)（令牌定义）、[gui-experience.md](../experience/gui-experience.md)（动效策略与实测依据）。

---

## 1. 规则

| 编号 | 规则 |
|---|---|
| R1 | `transition` / `animation` 只引用 `--motion-*`、`--ease-*`，不写裸时长与裸缓动 |
| R2 | 连续操作即时：滚轮缩放、拖拽平移、分隔线拖动不加过渡 |
| R3 | 入场淡入、退场即时；会挡住交互的遮罩与模态框，关闭不得有延迟 |
| R4 | 减少动效由 `style.css` 的全局媒体查询统一处理，组件不另写一套 |
| R5 | 动态列表只做插入动画，不用 `TransitionGroup`（离场行会留在 DOM 里） |

## 2. 令牌

### 2.1 时长

| 令牌 | 值 | 用途 |
|---|---|---|
| `--motion-follow` | 80ms | 跟随指针的即时反馈（对比落位提示） |
| `--motion-fold` | 100ms | 布局切换（侧栏折叠、侧舱 tab） |
| `--motion-fade` | 120ms | 淡入与非阻塞淡出（遮罩、提示条、列表行） |
| `--motion-hover` | 150ms | 悬停、按下、描边、底色 |
| `--motion-live` | 200ms | 连续量（进度条、状态点） |
| `--motion-emphasis` | 500ms | 一次性强调（结果闪烁） |
| `--motion-spin` | 900ms | 不定进度旋转 |
| `--motion-think` | 1200ms | 思考点循环 |
| `--motion-think-step` | 150ms | 思考点错峰延迟（第二个点 ×1，第三个点 ×2） |

### 2.2 缓动

| 令牌 | 值 | 用途 |
|---|---|---|
| `--ease-standard` | `ease` | 悬停、淡出等常规过渡 |
| `--ease-enter` | `ease-out` | 入场 |
| `--ease-linear` | `linear` | 折叠、旋转；「按下即到位」 |
| `--ease-loop` | `ease-in-out` | 循环动画 |

### 2.3 JS 侧

`frontend/src/lib/motion.ts`：

| 常量 | 值 | 用途 |
|---|---|---|
| `VIEW_FIT_MS` | 160 | 查看器双击适配 |
| `VIEW_RESET_MS` | 140 | 查看器分屏回正 |
| `CARD_CASCADE_MS` | 1800 | 批量入列的整段错峰窗口 |
| `CARD_STEP_MS` | 300 | 逐条固定间隔（6 条以内生效） |
| `CARD_ENTER_MS` | 120 | 单卡淡入（与 `--motion-fade` 同值） |

CSS 变量无法在 `requestAnimationFrame` 循环里读取，故前两项以常量维护；曲线用 `viewMath.ts` 的 `easeOutCubic`，等价 `cubic-bezier(0.33, 1, 0.68, 1)`。

`cascadeDelay(i, n)` 给批量插入的第 `i` 张卡算延迟：**6 条以内按 `CARD_STEP_MS`（300ms）逐条固定累计**，第 0 张不等；超过 6 条时等距压进同一个 1800ms 窗口 —— 步长按份数摊薄（`min(300ms, 1680ms/(n-1))`），末张连同 120ms 淡入仍收在窗口末尾。份数越多步长越小，整批时长不随份数线性增长；几十行时看上去接近同时出现。6 条本身也在固定步长内（5 × 300ms + 120ms = 1620ms ≤ 1800ms），第 7 条起才被压缩（280ms）。延迟由组件内联到 `animation-delay`，CSS 只给 `--motion-fade` 与 `--ease-enter`；卡片从右侧 16px 滑入。`motion.test.ts` 会读 `style.css` 断言 `CARD_ENTER_MS === --motion-fade`，双写只许一起改。

## 3. 场景选型

| 交互 | 组合 |
|---|---|
| 悬停、按下、描边、底色 | `--motion-hover` + `--ease-standard` |
| 遮罩、提示条、列表行入场 | `--motion-fade` + `--ease-enter` |
| 一键解析批量入列 | `cascadeDelay(i, n)` 错峰，单卡 `--motion-fade` + `--ease-enter` |
| 遮罩、提示条退出（不阻塞交互） | `--motion-fade` + `--ease-standard` |
| 侧栏折叠、tab 内容切换 | `--motion-fold` + `--ease-linear`（tab 淡入用 `--ease-standard`） |
| 进度条、状态点 | `--motion-live` |
| 一次性的结果强调 | `--motion-emphasis` + `--ease-enter` |
| 加载旋转 | `--motion-spin` + `--ease-linear` |
| 思考点 | `--motion-think` + `--ease-loop` |
| 查看器双击适配 / 分屏回正 | `VIEW_FIT_MS` / `VIEW_RESET_MS` + `easeOutCubic` |

## 4. 边界

- **退场**：会挡住交互的层（解码遮罩、模态框）不写 leave 过渡；不阻塞的提示条可以淡出，时长用 `--motion-fade`。解码遮罩淡出期间加 `pointer-events: none`。
- **手势优先**：任何手势、换图、改比例、改画布尺寸都先停止正在跑的一次性动画，不让动画和用户抢同一份状态。
- **布局过渡期间不重设画布**：侧栏折叠的 width 过渡由 `TifCanvas` 等 `transitionend` 后统一重建一次，见 [gui-experience.md §11](../experience/gui-experience.md)。
- **超时兜底不算动效**：`setTimeout` 兜底、`transitionend` 判定是同步机制，不套令牌。
- **全局减少动效块**：`style.css` 中把时长压到 `0.01ms` / `0ms` 的媒体查询是统一开关，直接写数值。

## 5. 新增动效前检查

1. 这属于连续操作还是一次性反馈？连续操作不加过渡。
2. 时长选 §2.1 的哪个角色；没有对应角色时先讨论是否新增令牌，不在组件里写裸值。
3. 缓动是否选对：入场 `--ease-enter`，折叠 `--ease-linear`，循环 `--ease-loop`。
4. 关闭动画后是否仍能立刻看到结果（`prefers-reduced-motion`）。
5. 动态列表是否只做插入动画。
