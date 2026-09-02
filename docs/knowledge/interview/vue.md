# Vue3 面试八股（interview · 高频精简版）

> 定位：跳槽面试高频 Vue3 基础自测与背诵。★=高频。每题给「答（要点）+ 追问」。
> 复习提示：本仓库查看器恰好是"Vue3 + TS + 巨型数据"的真实战场，末尾"实践注记"两题可作为追问环节把八股讲成项目经历的钩子（对应代码见 `frontend/src/stores/viewer.ts`，方法论见 `docs/knowledge/platform-tutorial.md` 第 6 章）。自测时先遮住「答」裸答一遍，再对要点、看追问；不会的题做记号，二轮只复习记号题。

## 一、编译与响应式内核

1. **Q：模板为什么要编译成 render 函数？（★★★）**
   **A：**
   - 浏览器不认识 `<template>` 这种声明式模板，只认 JS。要把模板跑起来必须"编译"：template → 解析成 AST → 生成 render 函数（返回 VNode 树的 JS 代码），渲染时执行 render 产出虚拟 DOM，再 patch 到真实 DOM。
   - 编译发生在两处：**构建期**（Vite + `vue/compiler-sfc` 把 `.vue` 预编译成 render，产物只带运行时，体积小、最常用）；**运行期**（完整版带编译器，可编译字符串模板，一般工程不用）。
   - "为什么要编译"的核心：模板是**声明式 + 可静态分析**的 DSL。编译期能做静态提升（不变节点只建一次）、`patchFlag` 标注（告诉运行时"这个节点只有动态绑定"）、缓存事件处理函数等优化；运行时 diff 只处理真正动态的部分，这是 Vue 模板比手写 `innerHTML` 快的关键。
   - SFC 编译产物：`<script setup>` 的顶层绑定会被编译器收集进渲染上下文，所以模板里能直接用 setup 的变量/函数；`{{ }}`、指令被编译成对运行时辅助函数（如 `_toDisplayString`）的调用。
   **追问：** 为什么工程上"运行时改模板字符串"几乎见不到（预编译 + 产物不带编译器，模板一旦打包就定死）？编译出 render 和手写 `h()` 是否等价（等价，模板就是 `h()` 的声明式语法糖）。

2. **Q：为什么需要虚拟 DOM？它到底解决了什么？（★★★）**
   **A：**
   - 直接操作真实 DOM 的痛点是**命令式、难收敛**：手动一处处改、或用 `innerHTML` 全量重建，在"大列表/频繁更新"下要么改不齐、要么性能差（真实 DOM 节点创建/访问/布局都很贵）。
   - 虚拟 DOM（VNode 树）= 用 JS 普通对象描述 UI 结构；更新时先产出**新 VNode 树**，与旧树 diff，算出最小 DOM 操作集，再一次性 patch 到真实 DOM——把"命令式找差异"换成"声明式算差异"。
   - 框架价值：diff 与 DOM 操作被**收敛进框架**，开发者只声明 `UI = fn(state)`；同一套 VNode/diff 还能**跨平台渲染**（DOM、Canvas、小程序、原生走各自的 renderer），框架只 diff 一次、由 renderer 落地。
   - 配合编译期优化（patchFlag/静态提升）后，Vue 多数情况只 diff 动态节点——虚拟 DOM 不是"不用操作 DOM"，而是让 DOM 操作**只在变化发生时、以最小代价**发生。
   **追问：** 有了 vnode 还要不要整树 diff（Vue 模板预编译后用 patchFlag 只 diff 动态分支）？template、render、vnode、真实 DOM 的链路是什么（template→编译→render→vnode 树→diff→patch 真实 DOM）。

3. **Q：响应式原理 Proxy：get/set 依赖收集与触发，何时收集、何时触发？（★★★）**
   **A：**
   - Vue3 用 ES6 `Proxy` 包对象，拦截 get/set/has/deleteProperty 等操作；`reactive()` 返回代理对象，原对象不动。相比 Vue2 的 `Object.defineProperty`（只能劫持已有属性），Proxy 能覆盖**新增/删除属性、数组索引与 length 变化**。
   - **get 做依赖收集（track）**：当"正在执行的副作用 effect"读取某属性时，把该属性与这个 effect 记进依赖表（`target → key → Set<effect>`）。**set 做触发（trigger）**：属性被重新赋值、值确实变化时，把依赖它的所有 effect 取出调度执行。
   - **何时收集**：effect 执行期间（组件渲染 effect、`computed`、`watchEffect`、`watch` 的 getter）访问响应式属性才会 track。模板里用到的状态因此被自动登记为该组件的渲染依赖。
   - **何时触发**：该响应式属性被赋值/变更（含新增、删除、数组 splice）时。触发不是同步改 DOM：effect 被排进调度队列，同一轮多次变更合并，靠微任务批量渲染（与 `nextTick` 同源）。
   - `reactive` 是**惰性深代理**：只有 get 到嵌套对象时才递归再包一层；同一对象用 WeakMap 缓存避免重复代理。局限：Proxy 只能代理对象，原始值要 `ref` 包装；代理对象被解构/传参脱出后就不再收集。
   **追问：** "当前 effect"是怎么记的（一个栈/全局 `activeEffect`，嵌套 effect 压栈，读属性时栈顶者被收集）？为什么 set 触发要"排队"而不是立即重渲染（合并多次变更、避免中间态闪烁，见 nextTick 题）。

4. **Q：ref 与 reactive 怎么取舍？（★★★）**
   **A：**
   - `ref` 把值装进 `{ value }` 容器，读写要 `.value`（模板顶层自动解包）；**原始值只能用 ref**（Proxy 代理不了基本类型）。`ref` 内部装对象时会转交给 `reactive` 实现深层响应。
   - `reactive` 只能代理对象，直接属性读写省 `.value`，适合"一整个对象、整体引用"的状态（配置、服务端返回的 data 对象）。
   - 取舍结论（可背）：**默认用 ref**——对基本类型与对象统一、传参/解构不丢响应、返回给外部（组合式函数返回值）结构稳定；`reactive` 多用于"拿整个对象引用到处用、不想每次 `.value`"的场景。两者没有性能差异，关键别解构 `reactive`。
   - 边界记牢：`reactive` 对象作为属性嵌一个 `ref` 会自动解包（省 `.value`），但**放进数组/集合、或解构出来的 ref 不会自动解包**——一律显式 `.value` 最稳。
   - 数组/集合陷阱：Proxy 已能拦截"按下标赋值/改 length"，但把数组**元素**取出来改、或解构后操作仍可能脱离代理；遇到麻烦优先用变异方法/整段替换。
   **追问：** `ref(obj)` 与 `reactive(obj)` 底层是否互通（ref 存对象时就是调 reactive 代理）？为什么组合式函数返回值推荐 ref（调用方能解构且保持响应）。

5. **Q：响应式为什么会"丢失"？toRefs/解构怎么救？（★★★）**
   **A：**
   - 丢失场景：从 `reactive` 对象**解构基本类型** = 拷贝一份普通值（改它不再回写源）；把响应式对象整个传参/赋值后从新变量改也不一定能触发（脱出代理）；组合式函数返回 `reactive` 时调用方解构后即丢失。
   - `toRefs(state)`：把 `reactive` 每个**顶层属性**转成独立 ref，且与源对象双向同步——解构出来的还是 ref，`.value` 的读写落到原对象上，响应保住。常用于 `return { ...toRefs(state), ...actions }`，让调用方随意解构。
   - `toRef(state, key)`：为单个属性造 ref，适合"想把它当 ref 传出去/替换引用"。`toRefs` 只处理**顶层**属性，且对象在调用后**新增的属性不会自动生成**对应 ref——先声明好结构。
   - props 同理：想在 setup 里解构 props 用 `toRefs(props)`；更省心的是模板里直接 `props.x`。
   **追问：** 解构 `reactive` 拿到"对象类型属性"还响不响应（拿到的是原代理引用就仍响应，基本类型则是拷贝，丢）？`toRef` 改 `.value` 与直接改源对象是同一件事吗（是——它的读写都委托给源）。

6. **Q：响应式工具 API 全家桶：readonly / shallowRef / toRaw / toValue 各什么时候用？（★★）**
   **A：**
   - `readonly(target)`：返回**只读代理**，写入在开发环境警告/报错——给 provide、子组件、外部模块"只读共享"一份状态，防止被意外改写；默认是深只读。
   - `shallowReactive` / `shallowReadonly`：只**浅层第一层**代理/只读，深层保持原对象——和 `shallowRef` 一起用于"深层数据巨大、不需要深层追踪"（见第 30 题 markRaw/shallowRef 场景）。
   - `toRaw(proxy)`：拿到代理背后的**原始对象**（调试、把原始数据交给非 Vue 库时用；注意对它改不触发更新）。
   - `unref(val)`：是 ref 就返回 `.value`，否则原样返回；`toValue(val)`（3.3+）进一步支持"getter 函数/ref/普通值"三态取当前值——写"参数可传 ref 或函数"的工具/组合式函数时用。
   - 归类记忆（可背）：造 ref 组 = `ref/shallowRef/toRef/toRefs`；只读组 = `readonly/shallowReadonly`；浅层组 = `shallowReactive/shallowRef`；取原始 = `toRaw`；解包 = `unref/toValue`；改代理 = `triggerRef`（手动通知 shallowRef）。
   **追问：** readonly 与"不传 reactive 只传原始拷贝"的区别（readonly 保持引用同一份源、源变它仍能看到，且防写）？shallowRef 配 `triggerRef` 为什么存在（改内部后想手动刷一次 UI 的逃生口）。

## 二、组件更新、派生与生命周期

7. **Q：状态一变，Vue 怎么知道"重渲染哪个组件"？（★★★）**
   **A：**
   - 每个组件实例持有一个**渲染 effect**：首次执行 render 时读取到的响应式状态，都被收集为"该组件更新的依赖"。依赖收集的最小单位是**组件**，不是模板里的某个表达式/节点。
   - 某状态 set 触发时，只有"自己依赖了它"的组件渲染 effect 被调度；没读过该状态的兄弟、父组件不重渲染——这就是组件级隔离更新。
   - 调度是**异步批量**的：同一轮多次变更合并，组件只重渲染一次；重渲染 = 重新执行该组件 render 产出新 VNode 树 → 与旧 VNode 树 diff（patch），只改真正变化部分对应的真实 DOM。
   - 父子联动：父重渲染会创建新的子 vnode；子是否跟着更新，由子的渲染 effect 是否读到新变化决定（模板里用了变化的 props/注入就是读到）。props 字段被读、插槽内容来自父渲染，都会让子随之更新；完全静态的子不受牵连。
   - 所以"谁该刷新"在 Vue 里由依赖收集自动得出，开发者不用手动派发——这正是声明式比命令式（每次手动 render/刷新）省心的点。
   **追问：** 为什么"组件内改一个属性，整棵 render 都会重跑"（一个组件就是一个渲染 effect，模板的细粒度靠 VNode diff 而非多个小 effect）？provide/inject 或 Pinia store 状态改了，用它的组件如何更新（读取组件各自的渲染 effect 都收集到了该依赖，照常触发）。

8. **Q：computed 的惰性缓存与 watchEffect 的差异？（★★★）**
   **A：**
   - `computed`：声明式**派生状态**。**惰性求值**——第一次被读取才计算，之后依赖不变就复用上次结果（内部脏标记），依赖真的变了下次读取才重算；没人读取就不算。
   - `computed` 应为**纯计算**，不得写副作用（改别的状态、发请求）；适合"由已有状态算出来、多处复用"的值，还天然带缓存。
   - `watchEffect`：**立即执行一次**回调，并自动收集它访问到的所有响应式依赖，之后任一依赖变化都自动重跑。适合"依赖多方、想自动追踪"的**副作用**（请求、日志、同步外部系统、手动操作 DOM/画布）；无新旧值概念、每次整段重跑、可手动 `stop()`。
   - 取舍一句话：**能推导出值 → computed；要执行动作 → watchEffect/watch**。两者在组件/组合式函数里创建都会随组件卸载自动停止。
   **追问：** computed 为什么"依赖变了不立即重算"（惰性 + 脏标记，等读取时才求值，保证派生值总是一致快照）？可写 computed（带 set）的典型用途（让 v-model 绑定到"由多个源推导的值"，写回时分发给各源）。

9. **Q：watch 的深度监听与 flush 时机？（★★★）**
   **A：**
   - `watch(source, cb, opts)`：显式指定要盯的"源"——ref、reactive 对象、**返回值的 getter 函数**、或这些的数组；源变化才回调，带 `newVal/oldVal`。默认**惰性**：不立即执行，与 watchEffect 相反。
   - 深度监听：直接 watch 一个 `reactive` 对象会**隐式深度**追踪其内部嵌套；若源是 ref（ref 里包对象）则需显式 `deep: true` 才能感知对象内部字段变化。深监听代价大，能用 getter 精确到字段就别整对象 deep。
   - `flush` 决定回调何时跑：**默认 `'pre'`**——同一轮响应刷新里、**组件更新前**触发（此时读 DOM 还是旧值）；`'post'`——**组件更新后**（能读最新 DOM/布局尺寸）；`'sync'`——每次变更立即同步触发（最及时但性能差，慎用）。
   - 精确监听用 getter：`watch(() => state.a, ...)`；监听多条源传数组。函数返回 `stop()` 可随时停。
   **追问：** 想在 DOM 更新后拿最新布局用哪个（`flush: 'post'`，时机等价 await nextTick）？watch 一个精确 getter 与 deep watch 整个对象，性能语义差在哪（前者只收集那几个字段的依赖）。

10. **Q：组合式生命周期钩子与 setup 的执行顺序？（★★★）**
    **A：**
    - `<script setup>`（setup 的编译糖）在**组件实例创建时执行一次**，时机在 Options API 的 `beforeCreate/created` **之前**——组合式里没有这两个钩子的对应物，因为 setup 顶层本身就能初始化状态/计算，替代了它们的作用。
    - 组合式钩子是 setup 顶层**同步注册**的：`onMounted/onUpdated/onBeforeUnmount/onUnmounted/onActivated/onDeactivated/onErrorCaptured/onRenderTracked`，命名规则 `on` + 周期。
    - **父子顺序（可背）**：挂载——父 `beforeMount` → 递归挂子树（子 `beforeMount → mounted`）→ 父 `mounted`（**子先于父**）；更新——父 `beforeUpdate` → 子 `beforeUpdate/updated` → 父 `updated`；卸载——父 `beforeUnmount` → 子 `beforeUnmount/unmounted` → 父 `unmounted`（子先拆）。
    - `onMounted` 里 DOM 才可用（模板 ref、第三方库初始化放这）；`onBeforeUnmount` 里清理定时器/事件监听等副作用。
    **追问：** setup 顶层能同步读到模板 ref 的值吗（不能——ref 是 ref 对象，DOM 在挂载后才赋值）？为什么子 mounted 先于父 mounted（挂载是递归先深后返回）。

11. **Q：模板 ref（DOM 与组件实例）什么时候才有值？（★★）**
    **A：**
    - 模板里给元素/组件写 `ref="xxx"`，setup 里同名 `const xxx = ref(null)` 即自动接通绑定（编译期按名字关联）。给普通元素 → 原生 DOM；给组件 → 组件的公开实例。
    - **赋值时机**：`onMounted` 之后才真正有值——setup 顶层和 `onBeforeMount` 阶段读它都是 null；组件更新（v-for/key 变化、元素替换）时 ref 会重新赋值；**卸载时被置回 null**。
    - 对 `<script setup>` 子组件拿实例，默认只能拿到它 `defineExpose` 暴露的内容（见宏题）；Options 组件默认暴露整个实例（可调其方法）。
    - v-for 里同名 ref 得到**数组**（顺序与渲染一致）；要精确控制用**函数 ref** `:ref="(el) => (map.set(id, el))"`。TS 下模板 ref 常标注 `Ref<HTMLCanvasElement | null>`，Vue 3.5+ 可用 `useTemplateRef` 拿类型更稳的句柄。
    - 典型用法：在 `onMounted` 里拿 canvas 初始化绘制上下文、拿子组件实例调方法、测元素尺寸。
    **追问：** 想在 DOM 更新后再量一次尺寸怎么办（状态变更后 await nextTick 再读，或 onUpdated）？模板 ref 与 v-if 配合要注意什么（条件为假时该 ref 为 null，读前判空）。

12. **Q：nextTick 的原理？改完状态为什么 DOM 不马上更新？（★★★）**
    **A：**
    - Vue 的状态更新是**异步批量**的：同步代码里连续改多个状态，不会每次都重渲染，变更被推进同一个调度队列，在**下一轮微任务**里统一 flush 一次（合并、去重、避免中间态）。
    - 所以改完状态后立刻读 DOM，拿到的还是旧值。`nextTick(cb)` 把回调排到"本次响应刷新完成之后"执行，回调里能读到最新 DOM；它返回 Promise，可 `await nextTick()`。
    - 实现层：内部用 `Promise.resolve().then` 驱动微任务 flush（调度队列 + 渲染器一次性 patch）；同一轮多次调 `nextTick` 的回调会排在同一次 flush 后一起执行。
    - 关联：默认 `watch` 的 `'pre'` 回调、`onUpdated` 都在同一批 flush 的节点上；"改了状态、要立刻拿新 DOM/新尺寸"的代码就写在 `nextTick` 之后。
    **追问：** 为什么不干脆同步更新（一次函数里改十次状态就渲染十次、还难以保证子组件在正确时机更新）？`nextTick` 一定在 DOM 更新后才跑吗（保证排在本次 flush 之后，是拿新 DOM 的可靠点）。

## 三、模板与渲染机制

13. **Q：v-if 与 v-for 为什么不能同元素用？key 在虚拟 DOM diff 里的作用？（★★★）**
    **A：**
    - **不能同层并用**：Vue3 中 `v-if` 优先级**高于** `v-for`——同一元素上 `v-if` 分支里访问不到 `v-for` 的迭代变量（与 Vue2 顺序相反），且每轮都要重复判断。官方立场：不在同一元素上用。
    - 正解：外层包 `<template v-if>`/`<template v-for>` 拆开；"要过滤的列表"用**计算属性先 filter** 再 `v-for`，避免每次渲染重复遍历、语义清晰。
    - `v-for` 必须给稳定 `key`：它给每个 VNode 一个**跨更新可识别的身份**，diff 才能在新旧列表之间按 key 匹配"复用 DOM/组件实例/内部状态"（输入框内容不丢、能触发排序过渡）；没有 key 只能就地复用、靠位置猜，容易串状态。
    - **key 别用 index**：列表**前面插入/删除**时数组整体移位而 key 集没变 → DOM 复用错位、状态跟到别的行。要用数据里稳定唯一的 id。
    - 双端 diff：新旧子节点从**两端**（头头、尾尾、头尾交叉）按 key 快速比对收敛，处理增删移；收敛不了的中段建 key→index 映射、配合最长递增子序列做**最小移动**——目标是尽量"打补丁复用"而不是整段重建。
    **追问：** Vue3 把 v-if 提权到 v-for 之前，与 Vue2 差异会导致什么经典问题（模板里访问不到循环变量/语义与预期相反）？为什么 index 当 key 在"列表最前面插入"时必出 bug（元素位置平移但 key 集没变，diff 以为同一批元素）。

14. **Q：v-show 与 v-if 的区别与取舍？（★★）**
    **A：**
    - `v-if` 是真条件渲染：条件为假时**元素与整棵子树不创建**（指令、事件、子组件都不初始化）；切换代价 = 销毁 + 重建。适合"低频切换、或初始大概率不展示的大子树"。
    - `v-show`：元素**始终渲染保留在 DOM**，只切换 `display: none`；切换只改一个样式、开销极小、能保住组件内部状态。适合**高频切换**或"隐藏但想保留状态"的场景。
    - 细节：`v-if` 可配 `v-else/v-else-if`、能作用于 `<template>`；`v-show` 不能用在 `<template>`（没有真实元素承载 display）。两者都能配 transition。
    **追问：** "首屏不展示但一开就要用"的重组件选哪个（v-if 省首屏初始化，代价是首次展开时才创建，可能卡一下）？v-show 的元素 display:none 时还能测量尺寸吗（offsetWidth 为 0）。

15. **Q：事件修饰符与事件冒泡/捕获？（★★）**
    **A：**
    - DOM 事件分两阶段：**捕获**（capture，window/document 往 target 走）与**冒泡**（bubble，target 往根走）；默认监听在**冒泡阶段**触发。
    - 常用修饰符：`.stop` 阻止继续冒泡、`.prevent` 阻止默认行为、`.self`（仅当 `event.target` 就是元素自身才触发）、`.once` 只触发一次、`.capture` 改到捕获阶段监听、`.passive`（优化滚动，不能和 `.prevent` 同用）；还有按键 `.enter/.esc/...`、鼠标 `.left/.middle/.right`、`.exact`（精确修饰键组合）。
    - 组件上 `@click` 若未在 emits 里声明，会作为原生事件**透传**到根元素（见通信题的 attrs）。
    **追问：** "点遮罩关弹窗"为什么要 `.self`（防止点到弹窗内部内容把弹窗也关了）？`.stop` 与 `.self` 差别（stop 拦截传播链；self 只按 target 过滤，不挡子元素冒泡上来的事件）。

16. **Q：插槽与作用域插槽？（★★）**
    **A：**
    - 插槽解决"**结构由父决定、内容插进子**"：子用 `<slot>` 留位（可给默认内容 fallback），父往标签内写内容。具名插槽：子 `<slot name="header">`，父 `<template #header>`。
    - **作用域插槽**：子把内部数据"回传"给插槽内容，父用 `v-slot="slotProps"` 或解构 `#default="{ item }"` 接收——**渲染细节归父、数据源在子**（自定义表格列/列表行、布局组件的可变头部）。
    - 本质是"函数化插槽"：子渲染时调用父传入的渲染函数、把 slotProps 当参数传进去；父用 `<slot :item="x" :index="i">` 具名/默认均可回传多个值。
    **追问：** 父更新会不会无谓触发子重渲染（Vue3 对静态插槽内容有编译优化，插槽内容没变时不会强制子更新）？作用域插槽与"数据放父"怎么选（数据本来就在子内部时用作用域插槽，不必把状态全提给父）。

17. **Q：Teleport / KeepAlive / Suspense 各解决什么问题？（★★★）**
    **A：**
    - `Teleport`：把片段渲染到 DOM 树**其它位置**（如 `to="body"`），脱离当前组件父级层级——弹窗/下拉/toast/抽屉常用，避免被父元素 `overflow`、`z-index`、`transform`（创建包含块）裁剪；可 `disabled` 条件禁用。
    - `KeepAlive`：**缓存被切走的组件实例**，回来不销毁重建、保留内部状态与滚动位置；被缓存组件触发 `onActivated/onDeactivated` 而不是常规卸载钩子；`include/exclude/max` 控制缓存范围。典型：标签页切换、列表→详情往返。
    - `Suspense`：处理**异步依赖**的加载态——`<template #fallback>` 显示等待占位，配合 `defineAsyncComponent` / async setup（顶层 await）。注意官方仍标为 **experimental**，生产大规模使用先评估；错误处理仍要自己配。
    **追问：** KeepAlive 缓存的组件"卸载钩子"还触发吗（不触发，改走 onDeactivated/onActivated）？Teleport 的内容 DOM 在外，事件还沿原组件树冒泡吗（事件沿**组件逻辑树**而非物理 DOM 树，原父级仍能收到）。

18. **Q：动态组件 `<component :is>` 与异步组件？（★★）**
    **A：**
    - `<component :is="which">` 让同一位置按条件/数据切换渲染不同组件；`is` 可给组件对象，也可给全局注册的名字。切换默认销毁旧实例；要保留其状态可在外层包 `KeepAlive`（配合 include 限定）。
    - 动态组件解决"同位置多形态"（弹层内容、Tab 内容），比一堆 `v-if` 分支干净；路由视图本质也是 `component :is`。
    - 异步组件 `defineAsyncComponent(() => import('./Big.vue'))`：延迟加载大组件，做**代码拆分**（首屏不下载，用到才 import），路由懒加载就是同一套。
    - 可配 `{ loadingComponent, errorComponent, delay(默认 200ms 防闪烁), timeout }`；或与 Suspense 的 `#fallback` 一起管理加载态。
    **追问：** 异步组件加载失败如何兜底（errorComponent / Suspense 内用 onErrorCaptured 捕获后给重试）？动态组件切走再切回想保留编辑状态怎么办（KeepAlive 缓存）。

19. **Q：Transition / TransitionGroup 的用途与切换类名？（★★）**
    **A：**
    - `Transition`：给**单个元素/组件**的插入、更新、移除加过渡/动画；配合 `v-if`、`v-show`、动态组件切换触发。进出场各有三个阶段类名：进入 `*-enter-from/enter-active/enter-to`，离开 `*-leave-from/leave-active/leave-to`（前缀来自 `name`，默认 `v-`）；还提供 JS 钩子（`@before-enter/@enter/@after-enter/@leave/...`）做 JS 动画。
    - 原理：进入时把 from 类插到下一帧、去掉后靠 CSS transition/animation 完成，等动画结束再移除节点（Vue 监听 transitionend）；`mode="out-in"`/`"in-out"` 控制新旧是否同台。
    - `TransitionGroup`：给 **v-for 列表**整体做插入/删除/**重排**动画（对 key 位移用 transform 过渡）。
    **追问：** 进出场同时出现"闪一下"怎么解（`mode="out-in"` 先出后进）？大列表重排动画的注意事项（对 key 用 transform 而非 layout，避免昂贵回流）。

20. **Q：scoped 样式隔离的原理与穿透？（★★）**
    **A：**
    - scoped 编译期给本组件模板元素打上 `data-v-<hash>` 属性，并把 CSS 选择器改成 `[data-v-<hash>]` 形式——样式只在**本组件元素**内命中，实现隔离；子组件根元素也会带上父作用域的 hash 属性，让父能命中子的根（但不能深入子内部）。
    - 想命中**子组件内部**元素用 `:deep(.inner)`（编译成 `.parent[data-v-xxx] .inner`），经典用法是覆盖第三方组件内部样式。
    - 逃逸到全局用 `:global(...)`；作用于插槽内容用 `:slotted(...)`（父传入插槽的内容不受父 scoped 约束）。
    - SFC 里还能用 `v-bind(cssVar)` 把 setup 状态注入 style：编译成 CSS 自定义属性，状态变化时自动更新（如把主题色、画布尺寸变量喂给样式）。
    **追问：** 为什么"父的 scoped 样式偶尔能影响子根"（Vue 刻意给子根元素也贴了父的 data-v 属性，便于父控制子根布局）？`:deep()` 与去掉 scoped 写全局有什么区别（仍限制在父根子树内，不会污染兄弟）。

## 四、组件通信与编译器宏

21. **Q：父子通信全图：props/emit/attrs/slots/provide-inject？（★★★）**
    **A：**
    - 主干 = **props 下行 + emit 上行**（单向数据流：props 只读，子要改父的数据就 `emit` 让父自己改）。
    - `attrs`：父传入但**未被 props/emits 声明**的属性（class/style、原生事件、data-*、任意 attr），默认自动透传到单根组件的根元素；要显式处理用 `useAttrs()`/`$attrs`；**多根组件必须手动 `v-bind="$attrs"`**。
    - `slots`：结构内容下发（见插槽题）；`provide/inject`：**跨任意层级**注入——祖先 `provide`、任意后代 `inject`，避免逐层传 props；要响应须显式包 `ref/computed/reactive`（普通值不响应），常用 `readonly()` 包一层禁止子乱改。
    - 另：`defineExpose` + 父模板 ref 拿子方法（见宏题）；`defineModel` 做双向 `v-model`；跨组件/跨页共享用 Pinia。`mitt/event-bus` 是次选（无痕难查，不推荐做大范围通信）。
    **追问：** "该 emit 给父还是进 store"怎么判（只在父子层流动 → props/emit；多个无关组件/页面共享且会并发改 → store）？provide 传普通值为什么子拿不到更新（普通值注入时已拷贝，非响应式；要传 ref/reactive）。

22. **Q：v-model 的底层原理与自定义组件双向绑定？（★★★）**
    **A：**
    - `v-model` 是语法糖。在**原生表单元素**上 ≈ `:value` + `@input`（不同元素监听事件不同：input/textarea 用 input，checkbox/radio 用 change，select 用 change）；修饰符 `.number/.trim/.lazy`（lazy 把 input 改成 change 再同步）。
    - 在**组件**上展开为 `:modelValue` prop + `@update:modelValue` 事件：组件接收 `props.modelValue`，交互后 `emit('update:modelValue', 新值)`。`defineModel`（见下题）就是把这一对声明收敛成一行可写 ref。
    - 一个组件可绑多个模型：`v-model:title` / `v-model:content` → props `title/content` + 事件 `update:title` / `update:content`（`defineModel('title')` 对应）。
    - 自定义修饰符：`v-model.capitalize` → 组件侧收到 prop `modelModifiers`（含 `capitalize`），在 `defineModel` 的 `set` 里统一处理或手动读修饰符变换值。
    **追问：** 为什么组件上 v-model 默认不用 `:value/@input`（模型 prop 名要稳定通用、与原生 DOM 解耦，Vue 定为 `modelValue`）？与 Vue2 的 `.sync` 是什么关系（Vue3 用 `v-model:xxx` 取代了 .sync 的多 prop 同步写法）。

23. **Q：defineProps / defineEmits / defineExpose 与 defineModel？（★★★）**
    **A：**
    - 这些是**编译器宏**：只在 `<script setup>` 顶层调用、无需 import，编译期展开（生成 props/emits 声明与内部变量），不是运行时函数。
    - `defineProps`：声明接收的 props。纯类型声明 `defineProps<{ title: string; count?: number }>()`（TS 即来源校验）或运行时数组 `['title']`；返回值在 setup 里用 `props.xx`。
    - `defineEmits`：声明子触发的事件，类型化后**父模板能拿到事件名/载荷的自动提示与校验**；`const emit = defineEmits<{ (e: 'update:count', n: number): void }>()`，后 `emit('update:count', n)`。
    - `defineExpose`：`<script setup>` 组件默认**对外闭合**（父拿不到内部状态），要暴露给父模板 ref 的成员必须显式 `defineExpose({ ... })`——常用来暴露"父要调用的方法"。
    - `defineModel`（Vue 3.4+）：把"`props.modelValue` + `emit('update:modelValue')`"这对声明收敛成一个**可写 ref**——`const m = defineModel()`，读写即与父的 `v-model` 同步；支持 `defineModel('title')` 多个、带类型/修饰符。手动 props+emit 依然等价可行，只是样板多。
    **追问：** defineModel 的手写等价物是什么（props.modelValue + emit('update:modelValue')，编译期自动成对展开）？父用模板 ref 想调子方法，为什么必须先 defineExpose（script setup 组件默认闭包，只暴露显式声明的内容）。

24. **Q：自定义指令的钩子与用途？（★★）**
    **A：**
    - 指令 = 对**真实 DOM 的底层复用**：结构里不好/不该用组件包一层、但要对元素施加行为的场景——自动 focus、点击外部关闭、懒加载图片、拖拽、权限隐藏/水印。
    - 对象式钩子（与 Vue2 改名对照）：`created → beforeMount(≈bind) → mounted(≈inserted) → beforeUpdate/updated → beforeUnmount → unmounted(≈unbind)`。回调拿 `(el, binding)`，`binding.value/arg/modifiers` 取 `v-dir:arg.mod="value"` 对应值。
    - 注册：局部 `directives` 或全局 `app.directive('name', def)`；在 `<script setup>` 里顶层声明一个 `vMyDir` 命名的变量/函数会自动当局部指令。
    - 判据：只"给某个 DOM 元素附加行为、不产出结构"用指令；要"自己渲染结构/响应式内容"就写组件。指令里挂的事件/监听器记得在 `unmounted` 里清理，防泄漏。
    **追问：** 指令里怎么拿到响应式的最新值（binding.value 每次钩子触发都是最新值，别在 mounted 里闭包缓存旧值）？为什么点击外部这类逻辑常建议用 composable 而非指令（组合式可注入任意元素 ref、TS/作用域更友好）。

25. **Q：错误捕获：onErrorCaptured / errorCaptured / app.config.errorHandler？（★★）**
    **A：**
    - `onErrorCaptured(cb)`：捕获**后代组件**抛出的错误（渲染、生命周期、watch 回调、指令钩子、事件处理器里的同步错误），按"内层先、向外冒泡"逐级传；在钩子里记录日志/切降级 UI。**`return false` 表示已处理、阻止继续向外传播**；不 return false 会继续上抛到更外层。
    - `app.config.errorHandler`：全局兜底，捕获未被 return false 拦截的组件错误。
    - 错误边界实践：在边界组件 onErrorCaptured 捕获后，用标志位 + `v-if` 切到 fallback 或"重试"按钮，避免整个应用白屏。
    - 边界要讲清：`onErrorCaptured` **不兜异步错误**——`setTimeout`/Promise/axios 里 try/catch 不到的拒绝、async 中未 await 的拒绝，不会自动进组件错误捕获，得各自 catch（或 unhandledrejection 全局兜底）。
    **追问：** 和 `window.onerror` 的分工（组件树内走 Vue 捕获链，全局 JS/资源错误走 window）？"return false"在多层嵌套里的语义（当前层声明已消化，父级不再收到该错误）。

## 五、组合式与状态管理

26. **Q：组合式函数（composable）的抽离规则？（★★★）**
    **A：**
    - 组合式函数 = 以 `use` 开头命名、内部自由组合 ref/computed/watch/生命周期、返回状态与操作的函数；目标是把"一段带响应式/生命周期/异步的有状态逻辑"从组件里抽出来复用（Vue 的函数式 hooks）。
    - 抽离判据：**跨组件重复的"有状态逻辑"**（数据拉取与重试、鼠标位置、定时器、剪贴板、画布工具行为、倒计时）→ composable；**无状态的纯计算/工具**别硬套 composable，放普通 utils。
    - 规则（可背）：`useXxx` 命名；返回值多给 ref/函数（调用方解构不丢响应，见 toRefs 题）；内部可组合调用其它 composable；参数尽量支持"传入 ref 或普通值"两种形态；别在 composable 里依赖某个具体组件实例。
    - **实例隔离**：每次在组件 setup 里调用都各自新开一份状态与 effect 作用域——多个组件用同一 composable 互不干扰；其内 watch/computed 随组件卸载自动停止。需要"全组件共享一份"时把状态放模块顶层（模块级单例）。
    - 副作用清理：composable 里的 `setInterval`/事件监听要在 `onBeforeUnmount` 手动清；watch 虽自动停，但定时器不会。
    **追问：** composable 与 React hooks 的核心差异（Vue 自动依赖收集、无需 deps 数组与相关 lint 规则）？为什么"返回普通对象 vs 响应式状态"要分清（返回 reactive 对象会被调用方解构丢响应，返回 ref 更稳）。

27. **Q：组合式函数为什么取代了 mixins？（★★）**
    **A：**
    - mixins（Vue2 主复用方式）的痛点：**来源不明**——模板里的某个方法/数据来自哪个 mixin 难查；**命名冲突**——多个 mixin 同名属性按合并策略互相覆盖，难预判；**隐式耦合**——逻辑散在各 mixin、互相可能依赖，改动影响面不可见；TS 推导差。
    - 组合式函数补掉的坑：**显式**（传入参数、解构返回值，一眼看出用了谁）；**无命名冲突**（不存在合并，各取各名）；逻辑按功能组织、可再组合；TS 友好、可脱离组件单测。
    - 迁移：老 mixin 常可"原样逻辑包一层 `useXxx`"搬成 composable；组件里 `const { a, b } = useXxx()` 显式使用。
    - 残留场景：共享的纯"Options 选项"（如重复的生命周期/计算属性清单）老项目仍可能用 mixin，新代码一律 prefer composable。
    **追问：** 多个 mixin 生命周期会怎么跑（都执行、按数组顺序，反而容易"重复副作用"）？composable 能不能完全替代 mixin 的"给 Options 注入方法"（不能直接改 Options，但 options 组件可加 setup() 来用 composable）。

28. **Q：Pinia 与 Vuex 的差异？（★★★）**
    **A：**
    - Pinia 是 Vue3 官方推荐状态库。**去掉了 mutations**：改状态直接在 action 里写（同步/异步通吃），也允许直接给 state 赋值；样板大幅减少。
    - getters = 带缓存的计算属性（依赖 state/其它 getter），可返回函数做参数化查询；actions 支持 async、可调用其它 action。
    - **无模块嵌套噩梦**：store 扁平，多个 store 之间可互相 `use`（store 引 store），不需要 namespace 字符串；TS 友好，state 类型零手工重复声明，DevTools 支持完善。
    - Vuex（Vue3 需配 Vuex4）：state/mutations/actions/getters 四件套，**mutations 必须同步**是有意设计（为 DevTools 可回放/时间旅行）；嵌套 module + namespace 是 Vuex 项目主要心智负担。
    - 取舍一句话：新项目默认 Pinia；只有强烈依赖"mutation 单向写 + 时间旅行调试"旧心智才保留 Vuex。
    **追问：** Pinia 去掉 mutations 后 DevTools 时间旅行靠什么（仍记录变更，但没有"强制经 mutation"这道闸）？store 里能不能 import 另一个 store（能——在 action/函数内 `useOtherStore()` 即可组合）。

29. **Q：Pinia 的 store 什么时候用 setup 风格（defineStore 传函数）？（★★）**
    **A：**
    - Pinia 两种写法：**option 风格** `{ state, getters, actions }`；**setup 风格** `defineStore(id, () => { ... })`——函数体里声明 `ref/computed/普通函数` 并 return，Pinia 自动把 ref 当 state、computed 当 getter、函数当 action，外部访问免 `.value`。
    - **何时用 setup**：逻辑复杂、需要"编排组合"——要在 store 内部调用其它 composable/use 别的 store、把动作按函数拆分、条件初始化、把若干 composable 结果组装成一个 store（option 写不了这种）。
    - **何时 option 够**：纯"数据桶 + 少量 getter/action"的简单共享状态（用户信息、主题、列表页数据），option 更省、可读性最好。
    - 二者不可混在同一个 store。本仓库 `stores/viewer.ts` 是典型 setup 风格：重编排逻辑（打开文件/解码/拉伸/掩码/导出串成一个 store），动作逐函数组织、内部调 `lib/` 纯函数。
    **追问：** setup 风格里能随意写模块级 `if`、调用别的 composable 吗（能——它就是普通函数体，这正是它比 option 灵活的点）？setup store 的 ref 对外和 option state 是否一致（都是 store.x，读取免 .value）。

## 六、大对象与"受控重绘"实践注记

30. **Q：markRaw / shallowRef 何时用？为什么"巨型数据不该被响应式系统追踪"？（★★★）**
    **A：**
    - 响应式有代价：reactive 是深代理，**读取/遍历每个属性都要过 Proxy**。对巨型/高频对象（Float32Array、ImageData、canvas、第三方库实例、点云/图结构）做深代理 = 访问开销被放大、内存近乎翻倍，还可能把外部库对象包出边界 bug（实例方法里 this 语义、内部缓存被代理污染）。
    - `markRaw(obj)`：给对象打"不要再代理我"标记——放进 `reactive`/`ref` 也保持原样。用于"只读展示、整段替换、绝不逐字段改"的个别大对象。
    - `shallowRef`：只让 `.value` 本身响应（**换成新对象才触发**），容器内部不深代理——用于"数据整体换新、不管内部突变"的大数据容器（每帧整体替换的像素缓冲、渲染场景对象）。
    - 判据一句话：**需要"改了某字段就自动刷 UI"吗？** 不需要 → 用 markRaw/shallowRef 掐断追踪；需要 → 才放进响应式。实际模板/派生只依赖少量小状态，大块数据进 canvas/渲染器后由渲染循环自己读，不必被追踪。
    - 代价要明说：放弃深层追踪 = 内部以后若真细粒度改，UI 不会自动刷新（要自驱重绘信号），别在之后反悔。
    **追问：** `shallowRef` 触发更新的唯一通道是什么（`ref.value = 新对象` 整体替换——这正是它适合"整帧/整批数据"的原因）？markRaw 过的对象再放进 reactive 数组，替换数组项能触发吗（替换数组项本身仍触发；改对象内部不触发）。

31. **Q：实践注记①——为什么查看器把 canvas / 大像素数组用 markRaw 塞进 Pinia store？（★★★）**
    **A：**
    - 遥感大图解码后，每条"图记录 rec"带着 `thumb`（画布）、`src`（Float32Array 像素）、`stats`（统计）、`file`——动辄几十 MB 到 GB 级，且这些字段只是"一次性读取 → 绘制"，绝不会逐像素去改再等 UI 自动刷新。这正是"响应式不该追踪巨型数据"的现场。
    - 仓库做法（`stores/viewer.ts`）：rec 放进 ref 数组管理，但 rec 内部的 `thumb/src/stats/file` 在赋值处都用 `markRaw(...)` 标记后再塞入——canvas 与 TypedArray 保持原始对象，不为它们建代理（`applyDecoded`/`openSceneJpg` 里可见）。
    - 收益：省掉"每访问一次像素数组都过 Proxy"的巨额开销；避免代理 canvas/浏览器对象引发的行为异常；也让 devtools 不用深遍历巨型状态。
    - 为什么安全：这些大字段**不参与模板细粒度绑定**，页面变化由 `renderTick` 计数驱动整体重绘（见下题），所以"它们是否被追踪"不影响正确性——只有状态位（当前图、视图、拉伸档）需要响应式。
    - 通用结论：任何"巨型只读 / 整体替换即可"的数据进 store/ref 前先 `markRaw`（防个别对象被代理）或 `shallowRef`（防整棵树被深追踪）；模板与派生状态只挂少量小状态。
    **追问：** 为什么不用 `shallowRef` 包整棵 rec（rec 里仍有状态文案/进度/激活标记需要响应式更新，得保留它们的追踪，只掐掉大字段）？如果没 markRaw，`src` 被深代理后逐像素读的代价（每个像素的 TypedArray 访问都要经 Proxy 转发，性能与内存双双变差）。

32. **Q：实践注记②——为什么"状态集中到一个 store + 单一重绘入口 renderTick"比组件各自为政更适合共享画布？（★★★）**
    **A：**
    - 共享画布"画成什么样"由一组状态共同决定：当前激活图 + 视图变换（scale/ox/oy）+ 拉伸档 + 绘制工具 + ROI 掩码列表——而操作入口散落在工具栏/文件列表/绘制面板/画布等多个组件。若各组件各自维护、各自直接操作 canvas，就会"**A 改了、B 猜、C 再画一次**"，顺序与一致性失控（异步解码进度回调用旧图覆盖新图是典型事故）。
    - 仓库做法（`stores/viewer.ts`，头部注释写明"移植不重写、动作直译"）：状态与所有动作集中进 Pinia store，**组件只向 store 发请求、不互相调用逻辑**；凡影响画面的状态变更统一 `renderTick++`；`TifCanvas` 组件 `watch(renderTick)` 在**唯一入口**读齐全部状态、整帧重绘——语义等价原 HTML 里各处直接调 `render()`，但顺序被串行化、同一批多次变更只触发一次重绘。
    - 收益（可背成通用方法论）：**单一重绘入口**可天然合并/去抖（一帧内改 10 次只画一次）；**异步守卫集中**（解码进度、删除、导出都校验"回调里还是当前激活图/token 未过期"才执行，旧回调不污染新界面，详见 tutorial 第 6 章 Web 异步 UI 教训）；store 逻辑可脱离组件在 Node 里驱动与测试，UI 组件只剩薄壳。
    - 这是"命令式 canvas"与"声明式 Vue"之间的桥：响应式负责小状态与重绘信号，命令式绘制封在唯一消费方里。
    - 边界（勿滥用集中）：纯组件内、与别人无关的临时 UI 态（面板开合、tooltip 显隐）留在组件私有即可，不必全进 store。方法论与组件职责表见 `docs/knowledge/platform-tutorial.md` 第 6 章。
    **追问：** 为什么不是"每个影响画面的字段各配一个 watch 局部重绘"（多个 watch 同帧各自触发 → 重复绘制、执行顺序难控、竞态难查；收敛成一个计数器 + 唯一入口，让"画一次"是确定性的）？renderTick 信号对性能意味着什么（重绘义务方唯一，可在入口做增量/合并/节流，也便于在 e2e/测试里断言"画面确实重绘了一次"）。

## 收尾：一条可背的主线 + 易混速查

- 模板不认浏览器 → **编译成 render** → 渲染跑 render 出 VNode → patch 真实 DOM。
- 响应式 = Proxy 的 **get 收集 / set 触发**，effect 执行期读才收集，异步批量刷新（nextTick 同源）。
- 派生用 **computed（惰性缓存）**，副作用用 **watchEffect/watch**；组合式钩子按"子先父后"跑。
- 通信：props/emit 主干，attrs/slots 补充，跨层 provide/inject，跨页共享 Pinia，defineModel 收编 v-model。
- 巨型数据别进响应式深追踪——**markRaw/shallowRef**；命令式画布配**单一重绘入口**（集中 store + renderTick）。
- 两条最常被追问的反面教材：把巨型像素/画布直接交给响应式深代理；把"影响同一张画布"的状态拆给多个组件各自去画、各自刷新——背熟上面两道实践注记即可现场展开。
- 讲项目叙事线：先用「编译 → render → vnode → patch」讲清框架原理，再用「响应式只圈小状态 + renderTick 驱动画布 + markRaw 掐断大字段」讲查看器——两端在本文件都能找到对应题当底稿。

| 易混点 | 一句话区分 | 关联题 |
| --- | --- | --- |
| computed vs watchEffect | 算"派生值"用 computed（惰性缓存）；跑"副作用"用 watchEffect（自动重跑） | 8 |
| v-if vs v-show | 真条件/不渲染（重建贵） vs 始终渲染只切 display（切换便宜） | 14 |
| ref vs reactive | 原始值+统一解构选 ref；整个对象引用直读选 reactive（别解构） | 4 |
| props vs provide/inject | 相邻层显式传 props；跨 N 层共享用 inject（要响应请传 ref/reactive） | 21 |
| markRaw vs shallowRef | 防"单个对象"被代理 vs 只让顶层换引用触发（内部不追踪） | 30 |
| watch 'pre' vs 'post' | pre=组件更新前回调；post=更新后可读新 DOM | 9 |

- 相关扩展：JS/TS 八股见 `docs/knowledge/interview/js-ts.md`；Vue 骨架/测试/部署与查看器"移植不重写"方法见 `docs/knowledge/platform-tutorial.md`。
