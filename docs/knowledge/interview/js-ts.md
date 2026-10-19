# JS/TS 面试八股（interview · 高频精简版）

> 定位：跳槽面试高频 JS/TS 基础自测与背诵。★=高频。每题给「答（要点）+ 追问」。

1. **Q：事件循环、宏任务与微任务（★★★）**
   **A：**
   - JS 单线程：同步代码先整段执行完，之后才轮到异步回调；回调按类型进各自的"任务队列"，由事件循环取。
   - 一轮循环：执行栈为空 → 取**一个**宏任务执行 → 把它期间新产生的**微任务全部清空** → 浏览器完成一次渲染（可选帧）→ 再取下一个宏任务。微任务整体清空，宏任务每次只取一个。
   - 微任务：`Promise.then/catch/finally`、`async` 里 `await` 之后续接的代码、`queueMicrotask`、`MutationObserver`。
   - 宏任务：`setTimeout/setInterval/setImmediate`、I/O、UI 事件；`requestAnimationFrame` 在宏任务后、渲染前这一拍，不算宏任务队列。
   - 经典判定：同一轮 `Promise.resolve().then(f)` 一定先于 `setTimeout(g, 0)` 里的 `g`；想"等渲染完成再动"就投宏任务或 RAF，
     想让异步"尽快跟上"就投微任务。
   - Node 差异点：`process.nextTick` 比 Promise 微任务**更早**（每次进入/离开 phase 的间隙先清 nextTick 再清微任务）；别在微任务里无限递归，
     会饿死渲染与宏任务。
   **追问：** `await` 左右两段代码分属什么时序（右侧表达式同步执行，左侧后续整体进微任务）？`setTimeout(0)` 真能"立即"吗（不能，至少等当前宏任务与全部微任务，
      还有约 0~4ms 的定时器钳制）。

2. **Q：Promise：状态机、链式调用与错误冒泡（★★★）**
   **A：**
   - 状态机：`pending → fulfilled / rejected`，单向不可逆；值或原因只固化一次，`then` 之后重复 `then` 拿的是同一结果。
   - 执行器同步执行：`new Promise(executor)` 的 `executor` 是立即同步调用的；它抛出的同步异常会被吞进 reject。
   - 链式：`then` 返回**新 Promise**；回调 return 普通值 → resolve 它；
     return Promise/thenable → 递归解包跟随它（这是"链能自动展开 Promise"的原因）。
   - 错误冒泡：链上任一回调抛错或返回 rejected，会沿链向后找**最近的一个 catch**；整条链没有 catch → `unhandledrejection`（Node 下可致进程按配置退出）。
     `catch` 返回 Promise，兜住后还能继续 `then`。
   - 常见坑：`.then(a, b)` 的 b 不捕获 a 自己的错误（同层的才算），`.then(a).catch(b)` 才兜住 a；别把 `catch` 放错层级以为兜住了其实没有。
   - 组合语义：
     `all` 一票否决（第一个 reject 就整组 reject）、`allSettled` 都等不 reject、`race` 取最先落定、`any` 取第一个 fulfilled（全挂才 reject）。
   **追问：** Promise 为什么不能"取消"（规范没这概念，取消要自己加 AbortController/标志位）？
      `.finally` 与 `then` 的区别（finally 不吞结果、只做清理且仍返回原状态）。

3. **Q：async/await 与错误处理（★★★）**
   **A：**
   - `async` 函数必返回 Promise：内部 return 值会 resolve、抛错会 reject，函数体里的同步异常也不会往外抛而是转成 rejected Promise。
   - `await` 暂停当前 async 函数并把后续排进微任务，等目标落定再继续 → 有顺序依赖的步骤写成同步样式，本质是 `then` 语法糖。
   - 每个 `await` 都可能 reject：只有把它包起来的 `try/catch` 能同步捕获；漏了就是 unhandledrejection，
     且**部分失败不影响已 resolve 的其它并发分支**（要自己判结果）。
   - 处理套路：a) 顺序依赖 → 单 try/catch；b) 兜底降级 → `.catch()` 链；
     c) 无依赖的多个请求 → `Promise.all` + 外层 try（要快）或 `allSettled` 逐个判（要容错）；别裸 `await` 让错误上浮成未处理。
   - 别把互不依赖的多个 `await` 写成串行（每个都多等一个 RTT）；也别在 `for` 里串行等一批可并发的请求。
   - ESM 顶层 `await` 合法（模块天然异步，可阻塞依赖它的模块）；`for await...of` 用于消费异步可迭代对象（流/SSE）。
   **追问：** "并发跑一堆、取最先成功或全部结果"用 `race`/`any`/`allSettled` 分别是什么取舍？
      为什么 `async` 函数里 `throw` 和 `return Promise.reject()` 等价（都会变成 rejected 返回）？

4. **Q：闭包与作用域链（★★★）**
   **A：**
   - 词法作用域：函数能访问哪些变量由**定义位置**决定、不是调用位置；每层函数连同外层环境串成一条作用域链，查找从内往外。
   - 闭包 = 函数 + 它捕获的外层作用域。外层函数返回后，只要闭包还活着，被它引用的变量就不会被回收（留在堆上）。
   - 每调用一次外层函数就产生一组**新**变量绑定（计数器各自独立）；这正是 `for` + `var` 陷阱的根源——`var` 整个循环共享同一绑定，闭包全拿到循环结束后的值。
   - `let` 在循环里每次迭代都新建绑定、闭包各自捕获 → 经典"每隔一秒打印 0..4"用 let 即解；面试背结论：绑定粒度是"每次迭代"不是"整个循环"。
   - 用途：模块/私有状态、防抖节流、记忆化缓存、回调中留住上下文；代价：闭包生命周期过长会把引用的大对象一起拖住（关联第 8 题）。
   **追问：** 作用域链是在定义时还是在调用时确定（定义时，闭包存的是环境引用）？
      为什么 "每隔一秒打印 i" 的 `var` 写法修法是 let、IIFE、还是闭包包一层（三者本质都是"给每次迭代造独立绑定"）？

5. **Q：this 的绑定规则（★★★）**
   **A：**
   - this 由**调用方式**在运行时决定（箭头函数除外）。四规则按优先级：
     默认绑定 < 隐式绑定（`obj.method()` 的 obj）< 显式绑定 `call/apply/bind` < `new` 绑定。
   - 默认绑定：非严格模式取 `globalThis`（浏览器 window），
     严格模式（含 ES 模块与 class 内部）取 `undefined`——这是模块里 `this` 为 undefined 的原因。
   - 隐式绑定会丢：`const f = obj.method; f()` 把方法拆出来独立调用就退回默认绑定；传给 `setTimeout`/事件监听同理。
   - 修正三招：箭头函数捕获定义处外层 this、`bind` 硬绑定、或先 `const self = this`。
     箭头函数没有自己的 this、不可用 `call/apply/bind` 改、也不能 `new`。
   - `new` 优先级最高（`bind` 出来的函数被 `new` 时 this 被忽略）；`bind` 是硬绑定（不可再被 call/apply 覆盖）。
   - 数组方法 `forEach/map/filter` 可传第二参 `thisArg` 当回调的 this；模板字符串里的 `?.` 不改变绑定规则。
   **追问：** class 方法怎么防丢 this（构造里 `this.h = this.h.bind(this)`，Vue 模板用箭头或 `@click="() => go()"`）？
      为什么 `(0, obj.method)()` 里 this 丢了（逗号运算符把 `obj.method` 的引用求值成纯函数值，隐式绑定不成立）。

6. **Q：原型链与 class（★★★）**
   **A：**
   - 每个函数对象自带 `prototype`；`new` 出来的实例的隐藏 `[[Prototype]]` 指向它。`obj.x` 找不到就在 `[[Prototype]]` 链上逐级找，
     末端是 `Object.prototype → null`。
   - 方法放 `prototype` 上全体实例共享一份，实例私有数据放实例上；`instanceof` 就是沿原型链找"构造函数的 prototype 在不在链上"。
   - 两套查成员的方式：`obj.hasOwnProperty('x')` 只查自有，`'x' in obj` 会沿链查到底；`Object.getPrototypeOf(obj)` 正规拿原型，
     `__proto__` 是历史遗留访问器。
   - `class` 是语法糖：
     `constructor` + 原型方法 + `static` 字段/方法（挂类本身）+ `extends` 继承（子类 prototype 的原型指向父类 prototype）。
     class 字段每实例一份，原型方法共享。
   - 派生类构造必须先 `super()` 才能用 this（this 由父构造初始化）；class 声明本身也在 TDZ（见第 10 题），先调用后声明会报错。
   - 显式造链：`Object.create(proto)`（顺带实现 `Object.create(null)` 的无原型纯字典，见第 14 题）、`Object.setPrototypeOf`。
   **追问：** 静态成员怎么继承的（子类构造函数自身的 `[[Prototype]]` 指向父类构造函数，所以能直接调父类 static）？
      为什么 `obj.hasOwnProperty` 在 `Object.create(null)` 上会炸、要用 `Object.prototype.hasOwnProperty.call(obj, k)`（没有原型就没有该方法）？

7. **Q：深浅拷贝与引用传递（★★★）**
   **A：**
   - 赋值对对象是**传引用**：两个变量指向同一对象，改一个另一个可见；基本类型传值。这决定了"浅拷贝/深拷贝"讨论的前提。
   - 浅拷贝只复制第一层：`{...o}`、`Object.assign({}, o)`、`Array.prototype.slice`——嵌套对象仍共享引用，改深层会串。
   - 深拷贝首选 `structuredClone`：原生、支持循环引用与 Date/RegExp/Map/Set/TypedArray/ArrayBuffer/Blob 等；
     拷贝不了函数、DOM 节点、WeakMap 键。
   - `JSON.parse(JSON.stringify(x))` 是阉割深拷贝：
     丢 `undefined/function/symbol`、`NaN/±Infinity` 变 `null`、Date 变字符串、`Map/Set` 变空对象、循环引用直接抛 `TypeError`。
   - 手写深拷贝的三道坎必须过：循环引用（用 `WeakMap` 存已拷贝对象做去重）、数组与对象分支、特殊类型各自处理——这也是面试常让手写、考的就是这几点。
   - 先想"要不要真拷贝"：共享引用常是想要的行为（省内存、性能），只有"怕对方改我的嵌套结构"才需要深拷贝。
   **追问：** 为什么拷贝一个 Vue 响应式 Proxy 常失败（proxy 的陷阱与原生对象不同，structuredClone 对不可克隆/被代理结构抛 `DataCloneError`，
      通常要 `toRaw`/序列化再处理）？拷贝后 `===` 为什么必为 false（新对象，只是内容相等）。

8. **Q：垃圾回收与内存泄漏（含典型泄漏场景）（★★）**
   **A：**
   - 主机制是**可达性分析 + 标记清除（并做分代：新生代/老生代）**：从 GC roots（全局对象、当前执行栈、活动闭包等）出发，遍历不到的对象即回收。循环引用在此模型下能被回收，JS 无需引用计数。
   - 泄漏本质 = 对象本该不可达，却仍被某个"活得久的引用"挂着。典型场景：① 忘了清理的事件监听 / `setInterval` / `requestAnimationFrame`（回调长期持大对象）；
     ② 全局或模块级缓存/数组只增不减（该用 WeakMap 当键控缓存）；③ 闭包意外让大对象/DOM 活得比预期久；④ JS 仍持着已被移出文档的 DOM（detached 节点）；
     ⑤ 隐藏的全局变量（`x = 1` 未声明）。
   - 组件框架里高发：`window.addEventListener` 加了没在 `onUnmounted`/`beforeDestroy` 里移除；`setInterval` 没清；
     回调里闭包抓到 `ref` 大对象。
   - 排查：DevTools Memory 拍两三个间隔的堆快照做对比，**只增不减**的对象是嫌疑；Performance 的 Allocation instrumentation 看分配来源。
   - 主动管理：大数组/`ImageData`/buffer 用完置 `null` 让引用早点断（仓库在 JPG 导出后把 `Float32Array src` 置空，
     见 [exportJpg.ts](frontend/src/lib/exportJpg.ts)）。
   **追问：** 为什么模块级 `Map` 做缓存会泄漏、`WeakMap` 不会（WeakMap 键是弱引用、不阻回收）？
      `WeakRef`/`FinalizationRegistry` 是什么位（底层收尾用，业务慎用，别指望它保证时机）。

9. **Q：`==` 与 `===`（★★）**
   **A：**
   - `===`：先比类型，类型不同直接 false，**不做隐式转换**；特例 `NaN !== NaN`、`+0 === -0`。
   - `==`：类型不同先转再比。转换规则：`null == undefined` 为 true 且**只等于对方**（`null == 0` 是 false）；数字与字符串互转；布尔先转数字；
     对象走 `ToPrimitive`（先 `valueOf` 后 `toString`）。
   - 反直觉清单（背这几个就够）：
     `0 == ''` true、`0 == '0'` true、`'0' == ''` false（字符串 vs 字符串不转）、`[] == 0` true（`[]`→`''`→0）、`[] == false` true、`[1] == 1` true、`'abc' == new String('abc')` true（对象转原始）。
   - 结论：业务一律 `===` / `!==`；唯一建议用 `==` 的是判空 `x == null`（同时命中 null 与 undefined），语义明确、业界认可。
   - 需要更严格语义用 `Object.is`：`Object.is(NaN, NaN)` true、`Object.is(0, -0)` false（SameValueZero 系，
     还影响 `indexOf`/`Set` 用 SameValueZero 所以能匹配 NaN）。
   **追问：** 为什么 `'0' == ''` 是 false（都是字符串时不做转换直接比）？`indexOf` 为什么找得到 NaN 而 `===` 找不到（内部用 SameValueZero）？

10. **Q：let/const、提升与暂时性死区 TDZ（★★★）**
    **A：**
    - `var`：函数作用域；声明提升但初值 `undefined`；顶层挂到 `globalThis`；可重复声明。
    - `let/const`：**块级作用域**；不挂全局；不可重复声明；存在提升但**不初始化**——从"进入块"到"实际初始化"之间叫**暂时性死区 TDZ**，
      期间任何访问（含 `typeof`）都抛 `ReferenceError`。
    - TDZ 特例的意义：普通未声明变量用 `typeof` 是安全的（返回 "undefined"），
      唯独 TDZ 中的 `let/const/class` 会让 `typeof` 抛错——语言宁可报错也不给"半初始化"的值。
    - `const`：只锁**绑定**不可重新赋值，对象内部仍可改；`const` 同样经历 TDZ；`const` 不要求初值是"常量表达式"。
    - `for` 用 `let` 每次迭代生成新绑定（闭包各自捕获，关联第 4 题）；`var` 全程共享一个绑定。
    - 声明风格：默认 `const`，确定要重新赋值才 `let`；别再用 `var`（老代码迁移时先想清楚提升行为差异）。
    **追问：** `let a; { let a; a = 1 }` 与 TDZ 的关系怎么讲（内层 a 从块开始就遮蔽外层，先于赋值访问内层 a 也是 TDZ 报错）？
       为什么 ES 模块/class 内部天然"严格模式"（文件顶部无需 'use strict'）。

11. **Q：模块化：ESM 与 CJS 的差异（★★★）**
    **A：**
    - ESM（`import/export`）：**静态**语法，可在编译期建依赖图 → 支持 Tree Shaking 死代码消除与静态分析；模块天然异步、允许顶层 `await`。
    - CJS（`require/module.exports`）：**运行时动态**、同步执行；`require` 拿到的其实是 `exports` 对象；
      条件 require、拼路径 require 在 CJS 随便写，ESM 不行（import 声明在最顶层静态位置）。
    - 导出语义：ESM 是**实时绑定**（live binding，`import` 到的是引用，被导出方之后赋值这边也能看到最新值）；CJS 是对象快照，读到的可能只是"执行到一半"的残缺导出。
    - Node 判定：`.mjs` 强制 ESM、`.cjs` 强制 CJS、`.js` 看最近 `package.json` 的 `type` 字段；
      浏览器只用 `<script type="module">`。
    - 互操作：ESM `import cjs from 'pkg'` 把 `module.exports` 当 default（具名导出靠静态探测不一定全）；
      CJS `require(esm)` 不行、只能 `await import()`（异步）。
    - 循环依赖总建议：**尽量不写**；真要写，把互相引用的公共部分抽成底层模块，别让上层两两互绕。ESM 下循环 + 读"还没初始化"的导出会撞 TDZ 报错。
    **追问：** 为什么 ESM 能 tree-shake 而 CJS 不能（动态 require 无法静态判断谁用了哪个字段）？
       webpack 里一份代码同时打包进 ESM/CJS 双份产物有什么隐患（两份模块实例、各自一份类定义 → 关联第 25 题跨副本 instanceof）。

12. **Q：防抖（debounce）与节流（throttle）（★★★）**
    **A：**
    - 防抖：停止触发后等 `delay` 才执行一次——**只认最后一次**。典型：搜索输入联想、`resize` 结束收尾、表单校验、点击提交防连点。
    - 节流：固定时间窗口内最多执行一次——**按节拍放行**。典型：`scroll`/`mousemove` 滚动进度、游戏按键、频率受限的埋点上报。
    - 一句话区别：连续高频触发下，防抖"停了才动一次"，节流"到点就放行一次"（不保证结束那下一定执行）。
    - 实现骨架：都是闭包存状态——防抖存 `timer`（clear 后重设，`clearTimeout` + `setTimeout`）；
      节流维护 `lastTime`（可选加 `timer` 实现 trailing 补发）。
    - 边沿细节：`{ leading: true, trailing: true }` 控制"开头要不要立即一次 / 结尾要不要兜底一次"；给 timer 的 callback 里恢复状态，别重复触发残留。
    - 组件里务必清理：`onUnmounted` 清定时器，否则卸载后回调仍会跑（串回第 8 题泄漏）；用 VueUse/lodash 现成实现没问题，但要读得懂参数。
    **追问：** 手写一个"前缘立即执行 + 尾缘兜底"的节流要维护哪两个量、触发时序怎么画（lastTime 判空走 leading，timer 判空兜 trailing）？
       滚动"到达底部加载更多"为什么用防抖不合适（会一直等待导致永远不触发，要用节流或滚动位置判定）。

13. **Q：数组/对象常用方法的时间复杂度（★★）**
    **A：**
    - 数组按下标 `arr[i]`、`push/pop`（尾部）均 O(1)；**`shift/unshift`（头部）是 O(n)**，每删/插一个都要整体挪位——头操作密集的队列别用数组 `shift`。
    - 查找：`indexOf`/`includes`/`find`/`lastIndexOf` O(n)；`at()` O(1)；无索引的"是否存在"高频查询应改用 `Set.has` O(1)。
    - 遍历与拷贝：`forEach/map/filter/reduce/every/some` 均 O(n)；`slice` 拷贝 O(n)；`reverse/concat/flat` O(n)；
      `flatMap` O(n)。
    - 排序：`sort` 平均 O(n log n)（V8 用稳定 TimSort）；**默认比较器把元素转字符串按字典序比**，所以 `[10,2,1].sort()` 得 `[1,10,2]`，
      数值排序必须传比较器 `(a,b)=>a-b`。
    - 对象属性读写均摊 O(1)（哈希）；但对象当字典有限制：键被转字符串、原型链会串、无 size——动态键集合用 `Map`（见第 14 题）。
    - 复杂度意识：循环里套 `includes` 会退化 O(n²) → 预建 `Set`；从数组头部持续删 → 用索引游标或 `Set`，别 `shift`。
    **追问：** 为什么 `pop` 均摊 O(1) 而 `shift` 是 O(n)（尾部不动、头部全体左移）？
       V8 的"快数组 vs 字典（慢对象）"什么条件下切换（数组出现大量洞或元素过多超出快数组预算）。

14. **Q：Map/Set 与 Object/Array 怎么选（★★）**
    **A：**
    - Map vs Object：Map 键可为**任意值**（对象/NaN 都行）、保持插入顺序、有 O(1) `size`、无原型链键污染、可直接 `for...of`；
      Object 适合结构固定的字段集合、需要 JSON 序列化、字面量可读的场景。
    - 结论句式：运行时**动态增删**的键值字典用 `Map`；**字段集固定**、当"record"用对象/`Record<K,V>`（配 `as const` 键表做类型安全）。
    - Set vs Array：Set 保证元素唯一、`has/delete` O(1)（数组 `includes` O(n)）；需要索引/顺序/重复 → 数组。去重 `[...new Set(arr)]`，
      查存在 `set.has(x)`。
    - Array 适合"有序、可重复、要按下标随机访问"；别拿数组当"集合"做去重/判存在（O(n) 浪费）。
    - 特例：要"纯字典、连原型方法都不想要"用 `Object.create(null)`（没有 `__proto__` 键，防原型污染/键碰撞，存用户可控键名时有用）。
    - 记忆细节：`Object.keys` 输出顺序是"整数键升序 + 其余按插入序"；`WeakMap/WeakSet` 键弱引用，用于"给对象挂附加数据但不阻回收"。
    **追问：** 给一个外部传入的对象"附加一份缓存数据"，为什么选 WeakMap 而不是往对象上塞属性（不想改对方结构、且对方被回收时缓存自动消失）？
       什么情况下宁可用数组 O(n) `find` 也不建 Map（量小、保序、可读性优先）？

15. **Q：类型窄化、联合类型与交叉类型（★★★）**
    **A：**
    - 联合 `A | B`：值满足其一；访问字段前**必须窄化**。窄化手段要背全：
      `typeof`（原始类型）、`in`（成员存在）、`instanceof`（类）、`Array.isArray`、`===` 字面量比较、truthiness、判别式字段。
    - 判别联合（discriminated union）：几个成员共享一个字面量 `type`/`kind` 标签字段，
      `switch (x.kind)` 后 TS 在每个 case 里自动把 x 窄到对应成员并给出字段提示——接口返回、表单事件、状态机建模的主力。
    - 交叉 `A & B`：同时满足两者、字段取并；常用于对象组合、给既有类型追加字段。与 interface `extends` 作用域不同（一个是类型层面合并、一个是接口继承）。
    - 收尾用 `never` 做穷尽检查：`switch` 的 `default` 里写 `const _exhaust: never = x`，
      漏掉的 case 编译器直接报错——让"以后加新类型忘改 switch"变成编译错而非运行时漏。
    - 可选属性 `?` 与 `undefined`：`x === undefined`、`'k' in x`、`x?.k` 三种窄化路径要想清楚"属性缺失 vs 值恰好是 undefined"。
    **追问：** 判别联合为什么比 `enum` 好窄化（成员名是字面量可 `===`/switch，enum 得拿枚举对象比）？
       `string | null | undefined` 与只写 `string | undefined` 差别在哪（null 与 undefined 是两个类型，别混）。

16. **Q：interface 与 type 怎么选（★★★）**
    **A：**
    - `interface` 的核心是**声明合并**：同名 interface 自动合并，还能被 `declare` 扩展——给第三方库补类型、扩 `Window`/`Vue`/框架全局类型全靠它；
      `extends` 继承、能配合 class `implements`。
    - `type` 是**别名**：表达能力是超集，能组合一切——联合、交叉、元组、映射类型、条件类型、工具类型结果、索引签名；但没有声明合并。
    - 差异实际就几条：能否合并（interface 独有）、能否表达"联合/元组/工具结果"（type 独有）、报错信息友好度、及作为"对象契约"的语义清晰度。
    - 实践倾向：需要**让使用者扩展**的公开 API 契约用 interface（合并能力是刚需）；内部派生、组合、和工具类型协作多用 type。
    - 现代风格也常见"统一 type、需要合并再切回 interface"；记住二者兼容判定都是结构性的（第 21 题），选型本质是"扩展性 + 可读性"之争，不是"谁更严格"。
    **追问：** interface 合并对同名方法产生的是重载，type 交叉遇到同名属性会发生什么（若类型冲突交成 `never` 或不兼容报错）？
       为什么"声明合并"对库作者是 API 设计能力（用户能不改源码地给你的接口开旁路）。

17. **Q：泛型约束与工具类型 Partial/Pick/ReturnType（★★★）**
    **A：**
    - 泛型 = 类型形参，写一次服务多种类型：`function f<T>(x: T): T` 由调用点自动推导；`T extends X` 是**约束**，保证 T 满足某结构下限。
    - `K extends keyof T` 限制键集合：`keyof` 把对象类型变成键的联合（对数组得到索引与 `length` 等，取元素类型用 `T[number]`）。
    - 三个必背工具类型：`Partial<T>` 所有属性可选；`Pick<T, K>` 只留 `K` 这些键；
      `ReturnType<F>` 取函数类型 F 的返回类型——用的时候先 `typeof`（`ReturnType<typeof handler>`）才能拿到函数类型。
    - 原理各懂一层：`Partial` 是映射类型 `{ [K in keyof T]?: T[K] }`；`Pick` = `keyof` 约束 + 索引访问 `T[P]`；
      `ReturnType` 靠条件类型里的 `infer R` 从函数签名里抠出返回类型。
    - 常配其它：`Omit<T, K>`（去掉某键）、`Required<T>`、`Readonly<T>`、`Record<K, V>`、`Exclude/Extract`、`Awaited<T>`。
    - 业务用法：接口响应的"可编辑字段"用 `Pick`；分步表单用 `Partial<T>` 渐进填充；事件 handler 复用签名直接 `ReturnType<typeof handler>`，
      别手抄第二遍。
    **追问：** `ReturnType` 拿到的是函数**声明**的返回类型还是运行时类型（编译期静态的，泛型函数拿到的是形如 `T` 的类型参数而非具体类型）？
       手写一个 `MyReturnType<T>` 用 `infer` 怎么解（`T extends (...a: any[]) => infer R ? R : never`）。

18. **Q：类型体操在业务里的"克制"（★★）**
    **A：**
    - 回报率排序：接口入参/响应类型 > 领域状态模型（让"非法状态不可表示"，
      如 `type Loading = 'idle' | 'loading' | 'done' | 'error'`）> 少量工具类型。前两类是业务收益大头。
    - 别为炫技写深嵌套条件/映射/递归/模板字面量：**编译慢**（类型实例化在编译器里是"真跑"，有深度与数量上限，
      超了报 `Type instantiation is excessively deep`）、同事读不懂、出错信息像天书。
    - 演进式写法：先显式标注/靠字面量推导，出现**重复**再抽类型，不要一步到位叠体操；复杂表达式宁可拆几步、给中间类型命名（顺带做窄化、可读）。
    - 取舍标尺：三行联合/工具类型能解决的事不上 `infer` 十行；复杂"类型编程"要解决的运行时问题，优先用普通代码 + 少量类型锁边界。
    - 验收反问：删掉这个中间类型，代码是否还读得懂、编译是否还快？两个"否"就是在过度设计。
    **追问：** 为什么递归类型/大联合会让编辑器和 CI 变慢（每个使用点都可能触发一次完整实例化，多文件共享时还要反复推）？"写给别人维护的类型"上，完备性和可读性你押哪边（押可读，
       除非是纯给编译器内部吃的工具类型）。

19. **Q：unknown 与 any（★★★）**
    **A：**
    - `any`：放弃全部检查——能赋给任何类型、任意属性访问/调用都放行，等于**给该处关掉类型系统**，错误推迟到运行时，且沿赋值把"无类型"污染到别处。
    - `unknown`：任何值都能赋给它（是顶类型），但**读它之前必须窄化**（`typeof`/`instanceof`/判别式/值比较）——"我不确定它是什么，但我要先验明再碰"。
    - 纪律：所有**不可信边界**一律 `unknown`：
      `JSON.parse` 结果、`fetch` 响应体、`catch (e)` 的 e（TS 默认把 catch 参数当 unknown）、第三方无类型数据、用户输入。窄化后再消费，拒绝 `any`。
    - `any` 只在不得不逃逸处出现：渐进迁移的 legacy、动态 JS 库的接口缝——最好隔离成小函数、边界出口再赋回有类型的值并写注释。
    - 双断言 `x as unknown as T` 是**显式承认绕过**类型系统，出现频率即坏味道频率；`.filter(Boolean)` 后仍需手写类型守卫才能把值收窄。
    **追问：** 为什么社区诟病 `JSON.parse` 的返回类型是 `any` 而不是 `unknown`（让你跳过校验直接到处用，错全推给运行时）？写一个 `isX` 类型守卫：
       返回 `x is X` 的判定函数，如何在控制层把 unknown 一次收窄成可信 DTO。

20. **Q：元组、枚举与字面量类型（★★）**
    **A：**
    - 元组：**定长定位置**的数组 `[x: number, y: number]`；支持 `readonly`、可选元素、剩余元素；
      常被 `useState` 这类 API 返回（`[state, setter]`）。
    - 字面量类型：把具体值当类型。`'up' | 'down'` 这类**字符串字面量联合**是方向/状态/事件名的首选建模；配合 `as const` 保住推导不拓宽成 `string`。
    - `enum`：运行时**真实存在**的对象；数字枚举自带反向映射（`Enum[Enum.A]` 可回查），字符串枚举没有反向；`const enum` 会被编译器内联，
      但与 `isolatedModules`/Babel 转换有冲突，使用面窄。
    - 倾向结论：大多数场景用 `as const + 字面量联合` 替代 enum——窄化自然、可 tree-shake、不生成多余运行时对象；
      真需要"反射成员列表/双向映射/后端也要同一组常量"时才用 enum。
    - 判别联合（第 15 题）是字面量类型最漂亮的用法：共用字面量标签 + switch 自动窄化，比任何类型体操都常用。
    **追问：** 数字枚举反向映射的编译产物长什么样（正反两个对象互相指）？`as const` 数组为什么有时要显式 `readonly` 元组才能传进要求 tuple 参数的函数（推导会拓宽成可变数组）。

21. **Q：结构性类型（鸭子类型）（★★★）**
    **A：**
    - TS 判兼容看"**形状**"：成员齐全、对应类型兼容即可，**不管声明来源与名字**。两个不同 interface 声明了相同字段就能互赋；两个无关 class 只要公共结构匹配也互相兼容（无需继承）。
    - 后果/收益：跨模块、跨库传对象只要字段对就通——库 A 的对象能喂给库 B 的函数；组合优先于继承、适配第三方零成本。这是 TS 与 Java/C# 名义类型系统最大的区别。
    - 副作用——**多余属性检查**（excess property check）只在"把对象**字面量直接**赋给带类型的变量/实参"时触发（freshness 规则）：
      直接写 `f({ a:1, extra:2 })` 报错，但 `const o = { a:1, extra:2 }; f(o)` 不报。所以它防的是"手滑拼错字段"，不是真正的封闭类型。
    - 需要"结构相同但语义不同"不能互赋（两个不同单位/业务 id）时做**名义化（brand）**：`type UserId = string & { __brand: 'UserId' }`，
      加个唯一标签字段让编译器区分。
    - 兼容方向注意：对象/函数参数位置在严格模式下是**逆变/双向**处理，写库类型时 `method` 与 `(prop)=>void` 的兼容规则不同（strictFunctionTypes）。
    **追问：** freshness 为什么"先存变量再传"能绕过（多余属性检查只在字面量出现的当场，转手后类型已经确定是变量声明类型）？
       `interface X { kind: 'a' }` 与 `interface Y { kind: 'a' }` 为何相等（结构相同），而这恰是某些框架用 `brand` 字段制造名义差别的动机。

22. **Q：类型断言（as）与 as const（★★★）**
    **A：**
    - `as` 是**编译期类型改写**：不生成任何运行时代码、不做校验不做转换——和 Java 运行时强转不同，写错只是骗编译器，运行时该炸照炸（见第 25 题）。
    - `as const`：让字面量**不拓宽**——`const d = { x: 1 }` 的 `x` 会推成 `number`，`as const` 后推成字面量 `1` 且整体 `readonly`；
      数组字面量变只读元组。用途：常量表、对象配置、配合 `keyof typeof` 拿键的精确类型。
    - 常见搭配 `keyof typeof`：
      `const ROUTES = { home: '/', about: '/about' } as const; type R = keyof typeof ROUTES`——把运行时常量变类型，
      是 TS 业务高频套路。
    - 双断言 `as unknown as T` 是显式跨类型系统，只在边界用并注释理由；日常代码见到它就是坏味道信号。
    - 非空断言 `x!`：只删类型上的 `null/undefined`，运行时真为 null 照样错——能不用就不用，用 `if`/守卫窄化；你每写一个 `!` 就把一条运行时事故的保险赔给了编译器。
    - `satisfies`（TS 4.9+）：**校验**值满足某类型，**同时保留**字面量精确推导，很多原用 `as` 的场景其实该用 `satisfies`（校验与推导兼得）。
    **追问：** `as` 直接改、`satisfies` 只校验不改变推导，为什么多数"配置常量"场景 prefers satisfies（既防手滑又保精确）？
       为什么 `as` 不能把互不相关类型硬转（"Conversion of type ... may be a mistake"，需经 `unknown` 过渡）？

23. **Q：Float32Array / Uint8ClampedArray / ArrayBuffer 这类 TypedArray 什么时候该用、怎么选（★）**
    **A：**
    - 要**高效读写二进制/大块数值**而不装进普通 JS 数组时用 TypedArray：普通 `number[]` 是装箱对象数组（内存数倍、访问慢）；
      TypedArray 是同一块 `ArrayBuffer` 上的定宽视图（Uint8 每元素 1B、Float32 4B…），可切片、整体 `set`、整块喂给 WebGL/canvas/网络。
    - 怎么选语义/宽度：字节流与"0~255 掩码/像素"用 `Uint8Array`；
      最终给 canvas `ImageData` 的**必须**是 `Uint8ClampedArray`（0~255 且写越界自动夹紧）；大值域或要做浮点拉伸运算用 `Float32Array`；
      解析二进制文件头的定长整数字段用 `DataView` 包字节（端序/对齐受控）。
    - 认知点：`new Uint8Array(buf, byteOffset, len)` 是**视图**不是拷贝（共享内存、互见修改）；TypedArray 没有 `push/pop`、长度固定，
      `map` 返回同类型新数组；改长度只能换新 ArrayBuffer 拷。
    - 实践注记：仓库像素管线就横跨三种——解码/抽样用 `Float32Array` 存每个像素的自然值（16bit 图值域 0~65535 装不进 Uint8，且拉伸运算要浮点精度），
      渲染前由 [tifDecode.ts](frontend/src/lib/tifDecode.ts) 转成 `Uint8ClampedArray` 的 RGBA，
      经 [browserKit.ts](frontend/src/lib/browserKit.ts) 包成 `ImageData`；
      掩码栅格化与 TIF 文件写出是纯 `Uint8Array` 0/255（见 [maskgen.ts](frontend/src/lib/maskgen.ts)），
      读文件头用 `DataView` 包 `Uint8Array`（见 [source.ts](frontend/src/lib/source.ts) 的 `read() → ArrayBuffer` 抽象）。
    - 实践注记（续）：
      [scene.ts](frontend/src/lib/scene.ts) 的 `graySrcFromRgba` 把 RGBA 的 Uint8 灰值转回单波段 `Float32Array`；
      `decode.ts` 产出的 rec 里 `src: Float32Array` 是重字段，
      [stores/viewer.ts](frontend/src/stores/viewer.ts) 用 `markRaw` 包住它、避免被 Vue 做深响应式代理（几千万浮点走 Proxy 太慢）——大 TypedArray 别交给响应式框架裸代理，这是性能常识。
    **追问：** `ArrayBuffer` 与 TypedArray、DataView 的关系一句话怎么说（Buffer 是原始字节块，TypedArray/DataView 是它的"读数眼镜"，
       一个按固定宽度读、一个按偏移+类型任意读）？
       为什么 `ImageData` 偏偏要 `Uint8ClampedArray`（canvas 像素语义要求 0~255 且越界 clamp 而不是 wrap/modulo）。

24. **Q：为什么浏览器单次 `new Uint8Array(...)` 有约 2GB 分配上限（`Array buffer allocation failed`）？和物理内存什么关系（★★）**
    **A：**
    - 报错 `RangeError: Array buffer allocation failed` 是 V8/Chromium"内存分配失败"的统一出口，两类根因：
      一是**单块 ArrayBuffer 超过引擎的平台上限**（约 2GB，来自地址空间预留与 TypedArray 最大长度策略）；二是**进程/整机内存吃紧**导致即使更小的分配也失败。
    - 关键认知：第一类**和电脑物理内存多大无关**——机器 64GB，单块超约 2GB 照抛，因为限制来自地址空间/引擎策略，不是"内存条不够"。
      本仓库 [platform-tutorial.md](docs/knowledge/platform-tutorial.md) 第 2 章把这条列为"约束 1"：
      浏览器单次分配约 2GB 封顶 + Canvas 面积上限 16384²，90% 的架构怪设计由此而来。
    - 推论：大图不能"整幅解码再显示"。一张 16bit 灰度遥感图 `W×H×2` 就是 GB 级，转 RGBA 再乘 4 直接破顶；2.4 万×2.4 万 像素的 RGBA ≈ 2.4GB，网页扛不住。
    - 仓库做法是**先算账再选路**：
      `decode.ts` 用 `decodeBytes = W*H*通道数*(bits/8)` 与 `rgbaBytes = W*H*4` 去和安全线 `SAFE = 1.3e9` 比（常量在 [tifDecode.ts](frontend/src/lib/tifDecode.ts) 顶部），超线就避开"整图一次性进内存"的 UTIF 全量解码，改走 geotiff 分块或稀疏条带抽样（只读若干行、秒级预览）。
    - 仓库做法（续）：
      `decodeOne` 还会对 `Array buffer allocation failed` 特征做一次**自动回退**到分块路径（`(e as Error).message` 匹配 `/array buffer/i`，见 [decode.ts](frontend/src/lib/decode.ts)）；编码侧也尽量"视图复用"而非反复 `new` 整块拷贝，用完置 `null` 尽快释放（JPG 导出后把 `src` 置空见 [exportJpg.ts](frontend/src/lib/exportJpg.ts)）。
    **追问：** 分配失败后页面会不会就此挂掉（不会——失败的是那一次分配，进程还在，降级/重试即可恢复）？为什么稀疏预览只抽少数行就够（真实图每行一条带且无压缩 → 能按字节精确定位任一行，不必建金字塔，
       见 [tifDecode.ts](frontend/src/lib/tifDecode.ts) 与 [platform-tutorial.md](docs/knowledge/platform-tutorial.md) 第 4 章）。

25. **Q：`(e: any) as Error` 和 `instanceof Error` 在跨模块/跨副本判错上的坑（★★★）**
    **A：**
    - `as Error` 只是**编译期改写、零运行时校验**：catch 到的可能是字符串、`undefined`，或另一个 JS 世界里构造的异常；
      `(e as Error).message` 编译期放行，运行时若是非对象照旧炸——类型骗过了，运行时骗不过。
    - `instanceof Error` 是真运行时检查，但它比对的是**原型链上某个具体的 Error 构造器**。跨模块坑就在这：
      同一类/同一库的自定义 `Error` 子类被打进**两份副本**（依赖版本分裂、双包双实例、`require` 与 `import` 各一套、webpack 重复打包、iframe/Worker 各自 realm），两副本各有一份"自己的"构造器 → 用 A 副本构造器 `instanceof` 判 B 副本抛出的对象得 false。
    - 跨 realm 同理：iframe/worker/不同 window 抛出的 Error，对主窗口全局 `Error` 做 `instanceof` 可能为 false——浏览器不共享一套全局构造器。
    - 稳健判错姿势三选一：只认原生 Error → `e instanceof Error`；
      怕跨副本/跨 realm/不标准 → 鸭子类型 `typeof e === 'object' && e !== null && typeof (e as { message?: unknown }).message === 'string'`；拿不准 → `e instanceof Error ? e.message : String(e)` 兜底成字符串展示。
    - 额外认知：`catch (e)` 在 TS 里默认是 `unknown`（`useUnknownInCatchVariables`），
      逼你窄化后再碰——`as Error` 是那条"看着省事其实撒谎"的路。
    - 实践注记：仓库对**展示型错误**统一用兜底三目 `e instanceof Error ? e.message : String(e)`，
      散见 [stores/viewer.ts](frontend/src/stores/viewer.ts) 与 [stores/scenes.ts](frontend/src/stores/scenes.ts)（同 realm 内部判断，instanceof 够用）；而对**要做恢复决策的特定错误**（`Array buffer allocation failed`），[decode.ts](frontend/src/lib/decode.ts) 用 `(e as Error).message` 配合 `/array buffer/i` **探内容而非判类型**——不依赖某个具体构造器，跨副本/跨 realm 也能命中，是有意避开 instanceof 的写法。
    **追问：** 给库用户抛自定义错误子类、还希望用户能 `instanceof` 判断，你该怎么设计（保证单副本交付，或额外导出一个 `isMyError` 鸭子守卫，文档里写明 realm/副本前提）？
       判断"这是不是我关心的错误"应优先比内容特征还是比类型（跨边界的特征内容更稳，类型只在本 realm 可靠）？
