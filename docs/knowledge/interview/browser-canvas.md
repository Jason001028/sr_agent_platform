# 浏览器/渲染/内存/Canvas 面试八股（interview · 高频精简版）

> 定位：跳槽高频浏览器原理 + 前端如何应对大数据/图像/离线的自测与背诵。★=高频。每题「答（要点）+ 追问」。
> 🗂 标注"实践注记"的题（见 H 节）对应本仓库真实代码与数字，可在追问环节把八股讲成项目经历；配套素材见 [README.md](docs/knowledge/interview/README.md)，教程因果见 [platform-tutorial.md](docs/knowledge/platform-tutorial.md)。

## A. 从 URL 到像素：加载与渲染

1. **Q：从输入一个 URL 到页面显示，中间发生了什么？（★）**
   **A：**
   - DNS 解析域名拿 IP（先查本地/浏览器缓存，再去递归或迭代解析）；命中 HSTS 或域名预解析可省这一步。
   - 建立连接：TCP 三次握手；HTTPS 再走 TLS 握手（TLS 1.3 典型约 1 个 RTT，会话复用更少）。
   - 发 HTTP 请求（请求行/头/体，可走 HTTP/1.1 keep-alive 或 HTTP/2 多路复用），服务器回 HTML。
   - 解析 HTML 建 DOM，边解析边请求外部资源：**CSS 默认渲染阻塞**、**普通 `<script>` 默认解析阻塞**（async/defer 改变时机）。
   - 走关键渲染路径：HTML→DOM、CSS→CSSOM→合并渲染树→布局→绘制→合成，首帧上屏。
   **追问：** TCP 队头阻塞与 HTTP/2 的关系？→ HTTP/1.1 一条连接同时一个在途请求；HTTP/2 单连接多路复用解决连接数，但 TCP 层仍有队头阻塞，HTTP/3（QUIC）才在传输层根治。

2. **Q：渲染流水线：HTML/CSS 怎么变成屏幕上的像素？（★）**
   **A：**
   - 解析 HTML → DOM 树；解析 CSS → CSSOM（层叠规则求值）。
   - 合并成**渲染树**：只留会显示的节点（`display:none` 不进渲染树；`visibility:hidden` 仍在但不可见）。
   - **布局 layout**：按视口算每个节点的几何（宽高/位置/层叠顺序）。
   - **绘制 paint**：把节点画成图层（生成绘制指令，不是直接碰屏幕）。
   - **合成 composite**：合成器按 z 序把各图层拼成最终帧交给 GPU 显示。
   - 增量与失效：不是每次样式变动都重算整棵树——浏览器用 dirty 位只重排受影响子树（layout invalidation）；但读 `offsetWidth/getBoundingClientRect` 会提前把挂起的重排"排空"（forced reflow）。
   **追问：** 为什么改样式不一定都要走全五步？→ 换颜色可能只触发 repaint；只动 `transform/opacity` 甚至只触发合成——这是性能优化的落点。

3. **Q：重排（reflow）、重绘（repaint）、合成（composite）的区别？怎么避免？（★）**
   **A：**
   - **重排**：改了布局几何（宽高/字体/位置/窗口缩放）→ 重算布局，代价最重，可能波及整棵子树。
   - **重绘**：只改视觉不改几何（颜色/背景/阴影）→ 跳过布局，重绘对应层。
   - **合成**：元素被提升为独立合成层后，只挪图层由 GPU 合成器完成（`transform/opacity/will-change` 等），不触发布局与整层重绘。
   - 避免要点：把"读布局 + 写样式"分开批量做（交替读 `offsetWidth` 再改会**强制同步重排**，即 layout thrashing）；用 class 一次改而非逐条 style；动画只动 `transform/opacity`；`will-change`/合成层别滥用（每层一张位图占内存，图层爆炸反而更卡）；`content-visibility` 跳过屏外子树。
   **追问：** `transform: translate` 与 `top/left` 动画差在哪？→ 改 top/left 每次触发布局+绘制；transform 只走合成，能上 GPU 保持 60fps。

4. **Q：CSS 和 `<script>` 怎么阻塞首屏？async/defer 区别？（★）**
   **A：**
   - CSS 不阻塞 DOM 解析，但**阻塞渲染**：渲染树要等 CSSOM 就绪才建。
   - 普通 `<script>` 同步执行且**阻塞 DOM 解析**：遇到它要先下载并跑完；不标属性时它在 HTML 解析中途执行。
   - `async`：下载不阻塞解析，**下载完立刻执行**（可能乱序、不保证 DOM 就绪）；`defer`：下载不阻塞解析，**HTML 解析完按文档顺序执行**（保序）。`<script type="module">` 默认 defer 语义。
   - 关键渲染路径优化：内联关键 CSS/JS；外部脚本按需 async/defer；`preload/preconnect` 提前关键资源。
   **追问：** `DOMContentLoaded` 与 `load` 谁先？→ 前者在 HTML 解析完（defer 脚本执行完）触发；后者等全部资源（含图片/样式）加载完。

5. **Q：浏览器是多进程的？为什么一个 tab 崩溃不拖垮整机？（★）**
   **A：**
   - 现代浏览器典型拆成：**浏览器主进程**（窗口/UI）、**GPU 进程**（合成/光栅/部分解码）、**网络进程**（网络栈）、每个站点一个**渲染进程**（Blink 布局/绘制 + V8）等；同类进程可聚合（process-per-site / 服务化）。
   - 好处：**崩溃/安全隔离**（Site Isolation）——某个 tab 的渲染进程挂了只显示"页面崩溃"，不影响浏览器和其他 tab；沙箱化降低提权风险；坏处：进程多 → 内存开销与 IPC 成本高。
   - 与内存上限的关系：超大 TypedArray 的单次分配上限、单个 tab 能用的内存，都是**渲染进程层面**的边界（见 D 节 Q16）——这也是"机器内存再大，网页也扛不住整幅 2GB+ 解码"的底层原因。
   **追问：** 所有页面都在一个渲染进程里吗？→ 同源常共享进程以省内存；不同站点按 Site Isolation 尽量分开，安全优先。

## B. 事件循环与"别卡主线程"

6. **Q：浏览器的事件循环怎么跑？宏任务/微任务/渲染的先后？（★）**
   **A：**
   - 一次循环大致：执行当前**宏任务**（task：`setTimeout`/事件回调/IO 等）→ **清空微任务**（microtask：`Promise.then`/`queueMicrotask`/`MutationObserver`，期间新加的也要清完）→ 按需**渲染**（含执行 `requestAnimationFrame`）→ 取下一个宏任务。
   - 微任务在"一个宏任务结束后、下一次渲染前"整批清空 → 在微任务里无限续排会饿死渲染。
   - `setTimeout` 是宏任务定时器，不精确：嵌套 5 层以上被钳到最小约 4ms（典型），后台标签页钳得更狠；动画不靠它对齐帧。
   - 渲染有合并与降频：同帧内多次 DOM/样式变更会被浏览器合并成一次渲染（跟屏幕刷新率，典型 60/120Hz）；页面不可见时标签会被降频甚至暂停——动画/进度类逻辑不能假设后台仍实时。
   **追问：** `await` 之后是宏任务还是微任务？→ 微任务。经典 `console` 顺序题（Promise vs setTimeout）的答案就是"先清微任务再等下个宏任务"。

7. **Q：Node 的事件循环和浏览器差在哪？（★）**
   **A：**
   - Node 基于 libuv 分**阶段**：`timers`（setTimeout/setInterval）→ `pending callbacks` → `idle/prepare` → `poll`（IO 回调、阻塞等待）→ `check`（`setImmediate`）→ `close callbacks`；跑完一轮进入下一轮 timers。
   - 每阶段**切到下一阶段前**，先清 `process.nextTick` 队列、再清微任务队列。
   - 差异：Node **没有渲染阶段**；多了 `process.nextTick`（优先级比 Promise 微任务更高）和 `setImmediate`；微任务不是"宏任务后统一清"，而是在**阶段切换处**清。
   - 顺序坑：在 poll 阶段（如 IO 回调里）`setImmediate` 会先于 `setTimeout(0)`；顶层两者顺序不定。
   **追问：** `nextTick` 和 `setImmediate` 谁先？→ 名字反直觉：nextTick 在当前阶段末尾就执行（最早）；setImmediate 要等下一轮 check 阶段。

8. **Q：`requestAnimationFrame`、防抖、节流各解决什么？（★）**
   **A：**
   - rAF：浏览器**每帧渲染前**回调一次，与屏幕刷新对齐；掉帧自动跳过；**后台标签页自动暂停** → 动画/视觉高频更新用它，不空转。
   - 防抖 debounce：**停止触发后延时执行**一次（连续输入结束后才搜索）；节流 throttle：**固定间隔至多执行一次**（滚动过程持续有输出）。一般 `scroll/resize` 节流、输入搜索防抖。
   - 高频事件不要在事件回调里直接干活：先记"脏"标记，由 rAF/节流在下一帧统一刷新（和"状态变更后统一重绘"同一思想）。
   - 实现口径：throttle 记"上次执行时间"到点才跑；debounce 存定时器、重触发就重置；需要首/尾沿变体时按需加 leading/trailing。
   **追问：** `scroll` 监听要不要 `passive: true`？→ 声明不调 `preventDefault` 后浏览器不阻塞滚动线程；要 `preventDefault`（如自定义 touch）就不能 passive。

9. **Q：async/await 为什么"防不了卡顿"？长任务怎么让出主线程？（★）**
   **A：**
   - async/await 只是 Promise 语法糖：**await 让出的是微任务边界，不是渲染机会**。微任务整批在渲染前清空，所以用已 resolve 的 Promise 串起一段 CPU 重活仍会一口气跑完、页面照卡。
   - 要让浏览器喘气必须**结束当前宏任务**：把活分块，每块后 `setTimeout(0)` / `MessageChannel` / `scheduler.yield()` 交还一次；交还时顺手报进度。
   - 单次超过约 50ms 的同步块就是 Long Task，直接影响 INP；CPU 型重活的根治方案是搬进 Web Worker。
   **追问：** 为什么 `await Promise.resolve()` 不顶用？→ 续体是微任务，同一宏任务内全清完才轮到渲染，等于没让出。

10. **Q：requestIdleCallback / scheduler 是干嘛的？什么时候该用？（★）**
    **A：**
    - rAF 贴着渲染前执行（高优先、每帧一次）；**rIC** 则在浏览器**空闲时**执行低优先分片任务（`deadline.timeRemaining()` 判断剩余时间），但**不保证执行**——一直在忙/被输入打断就可能推迟甚至饿死。
    - 现代方案是 `scheduler.yield()` / `scheduler.postTask()`（Chrome 等支持），能显式把控制权还给渲染，或用优先级队列调度。
    - 适用：非关键的后处理、上报、IndexedDB 批量写入、懒计算、空闲预解码；关键路径与用户交互不要指望 idle。
    **追问：** rAF、rIC、宏任务谁先？→ 一帧内大致 rAF（渲染前）→ 渲染 → 帧间空闲才轮到 rIC；宏任务独立排队。

## C. 单线程模型与多线程出路

11. **Q：JS 为什么单线程？Web Worker 能做什么、不能做什么？（★）**
    **A：**
    - 主线程单线程（一个 realm 一份执行），DOM/渲染只许主线程碰——并发模型简单（不用锁），代价是重活卡 UI。
    - **Web Worker** 开独立线程，有自己的全局但**无 window/DOM**；可做：纯计算、解析、编解码、fetch/XHR、IndexedDB、Cache、OffscreenCanvas；脚本需同源。
    - 与主线程用 `postMessage` 通信、结果回传；worker 自己有事件循环（无渲染阶段）。
    - 分工：主线程只编排/渲染，CPU 重的（解析/像素/压缩/加密/大数组）丢 Worker，按 `navigator.hardwareConcurrency` 起池。
    - 类型三兄弟：Dedicated Worker 一主一从；Shared Worker 同源多页共享；Service Worker 是"网络代理 + 离线缓存"（见 F 节），三者生命周期与能力不同。
    **追问：** Worker 里能直接改 DOM 吗？→ 不能；把结果 `postMessage` 回主线程更新，或用 OffscreenCanvas 离屏画（见下题）。

12. **Q：OffscreenCanvas 怎么把"每帧重绘"也搬出主线程？（★）**
    **A：**
    - 主线程 `canvas.transferControlToOffscreen()` 拿到一个 OffscreenCanvas，`postMessage` 时把它**转移**给 Worker（transferable）。
    - Worker 里对 OffscreenCanvas 拿 2D/WebGL 上下文照常绘制；主线程那个 `<canvas>` 仍负责显示——合成器直接取 Worker 画好的位图，主线程零绘制开销。
    - 不接 `<canvas>` 时也能 `new OffscreenCanvas(w, h)` 纯后台渲染，再 `transferToImageBitmap()` 把结果帧传回主线程贴到可见 canvas。
    **追问：** transfer 后主线程还能画吗？→ 不能，原 2D context 已失效；画布的像素内存仍在，只是不再由主线程绘制占用。

13. **Q：跨线程/跨窗口通信 postMessage：结构化克隆和 transferable 是什么？（★）**
    **A：**
    - 用途三处：Worker↔主线程、iframe/新窗口跨文档（`window.postMessage(msg, targetOrigin)`）、`MessageChannel` 双通道。
    - 传值默认**结构化克隆**：深拷贝一份，支持普通对象/数组/Blob/ArrayBuffer 等，但**不支持函数与 DOM 节点**。
    - **transferable 转移**：`postMessage(obj, [buf])` 把 `ArrayBuffer/ImageBitmap/OffscreenCanvas/MessagePort` 的**所有权交给对方，零拷贝**；转移后原变量被 detach（不可再用）。大像素缓冲区务必走 transfer，峰值内存差一倍。
    - 跨文档安全：发送端要写 `targetOrigin` 白名单，接收端必须校验 `event.origin`，否则任意页面都能投毒。
    **追问：** 同源页面间广播用什么？→ `BroadcastChannel` 同源一发多收，比逐窗 postMessage 省事；多线程共享内存用 `SharedArrayBuffer` + `Atomics`（需跨源隔离 COOP/COEP，场景窄）。

## D. 内存：GC 与泄漏

14. **Q：栈/堆怎么分？JS 的垃圾回收怎么工作？WeakMap 弱在哪？（★）**
    **A：**
    - **栈**：基本类型值 + 引用变量的"地址"，LIFO、容量小、线程私有；**堆**：对象/闭包/大数组的实际数据，由 GC 管。
    - **引用计数**（老派方案）：引用归零即释放，但**循环引用永远不清零 → 泄漏**；现代 V8 用**可达性分析**：从根（全局、当前调用栈、寄存器）沿引用遍历，**标记-清除**不可达对象、必要时**标记-压缩**整理碎片；**分代**：新生代小对象复制式 scavenge，存活够久晋升老生代。
    - **WeakMap/WeakSet**：key 是**弱引用**，不阻止回收，且**不可枚举、无 size** → 给对象挂私有元数据/做缓存不会造成泄漏；`WeakRef` 提供显式弱引用，`FinalizationRegistry` 收回收通知（时机与顺序不可依赖）。
    **追问：** 大 `ArrayBuffer` 是 JS 对象吗？→ 字节块在引擎外部（native 内存），GC 只收"包装对象"；真正的字节要等你把引用置 null、由分配器归还，所以大缓冲务必主动释放/复用。

15. **Q：前端常见内存泄漏怎么来的？怎么用工具定位？（★）**
    **A：**
    - 常见来源：全局变量意外挂 `window`；定时器/`setInterval` 未清且回调闭包握着大对象；事件监听加在组件/元素上销毁时没 remove；闭包长期引用 DOM（内存里留着"已脱离文档的节点"＝detached node）；缓存/Map 无限增长；`console.log` 长期打印大对象。
    - 定位（DevTools）：Performance 录制看内存锯齿是否逐段爬升不回落；**Memory → heap snapshot 连拍三张**（操作前/操作中/操作后）对比 `Retained Size` 找不回落的对象；"Allocation instrumentation on timeline" 看分配发生在哪行代码；Elements 里找被 JS 持有的 detached DOM。
    - 概念：`Shallow Size`（对象自身）≠ `Retained Size`（连带它独占引用的整棵树），查泄漏看 Retained。
    **追问：** 缓存为什么用 WeakMap？→ key 随原对象回收，不手动清理也不漏；要"可枚举 + 淘汰策略"时才退回首写 Map 做 LRU。
    🗂 **实践注记关联：** 本项目 decode/导出是重活，换图/重开会用 token 作废旧请求并丢弃旧 rec（避免旧大数组积压）；store 里的大字段（Float32Array 源/thumb canvas/统计）用 `markRaw` 存放，避免被 Vue 深代理再包一层放大内存。

16. **Q：超大 TypedArray：单次分配约 2GB？`RangeError: Array buffer allocation failed` 怎么回事？（★）**
    **A：**
    - 事实：Chromium/Edge 里 `new ArrayBuffer` / `new Uint8Array(…)` **单次分配约 2GB 就抛 `RangeError: Array buffer allocation failed`**——与机器有没有 64GB 内存无关；另有长度/地址空间与渲染进程配额的约束，超了就 RangeError 或进程被压垮。
    - 与总内存关系：浏览器是多进程的，每个渲染进程的可用内存、可单次分配的上限**都远低于系统 RAM**；所以"机器内存大"救不了"单次分配 2GB+"。设计时必须假设这个上限存在（本仓库教程第 2 章把它列为核心硬约束）。
    - 对策：**不整块扛**——分块读（只要需要的 window/瓦片）、只抽样读需要的行（稀疏读）、或让服务器先把大图降成小图再喂浏览器；把单次分配主动压到安全线以下（本仓库常量 `SAFE = 1.3e9`）；遇 RangeError 用 try/catch + 降档重试。
    - TypedArray vs 普通数组：数据在连续 buffer 上（`.buffer` 共享）、越界不抛错（截断/补 0），适合当像素/二进制通道。
    - 量级感：一张 8192×8192 的 RGBA 缓冲就是 256MiB；"预览缩略 + 全量源 + RGBA 上屏"几份同时活着，很快触顶。
    - 常见姿势：写一个"预算函数"先估解码字节数（≈宽×高×通道×位深/8），超过安全线就自动改走省内存的路径（分块/稀疏）——本仓库的 decode 决策就是这么分的。
    **追问：** 16bit 灰度图能直接塞给 canvas 吗？→ 不能直接：canvas 的 ImageData 是 **8bit/通道 RGBA**；16bit 原值要先量化/拉伸到 8bit（中间层可用 Float32Array 存原值，上屏时才转 RGBA）。

17. **Q：大 ArrayBuffer/位图为什么不"马上释放"？怎么主动管内存？（★）**
    **A：**
    - GC 只保证"最终"回收，时机引擎自定；native 内存（ArrayBuffer 字节块、解码位图、GPU 纹理）更不跟 JS 引用即时归还。
    - 主动手段：引用置 `null`；`ImageBitmap.close()`、用完的 offscreen/GPU 资源显式释放；`URL.revokeObjectURL`；**transfer 传走就没了**（别留双份）；控制**同时存活的大对象数量**（并发解码 N 份封顶）。
    - 观察手段：DevTools Performance/Task Manager 看每进程内存；Chrome `performance.memory.usedJSHeapSize`（非标准）看堆变化；操作前后各拍一次 heap snapshot 对比。
    - 少分配就少回收：复用单块缓冲（pool）、分块读而不是整读、能 transfer 就不结构化克隆。
    - 叠加峰值才是真风险：预览缩略 + 全量源 + RGBA 缓冲同时活着时最容易爆；能力允许时只保留一种"可重建"的表示、用到再算，而不是把中间结果全留一份。
    **追问：** 为什么循环里反复 `new` 大数组会看到锯齿状峰值？→ 分配→用→弃反复发生，native 归还滞后于 JS 引用；固定复用缓冲区能把峰值压平。

## E. Canvas：上限与像素

18. **Q：Canvas 有面积上限吗？大约多大？超大画布怎么办？（★）**
    **A：**
    - 有。Chromium 约限制 `宽 × 高 ≤ 268,435,456`（即 16384×16384，本仓库常量 `CANVAS_AREA_MAX`），单边另有约 3 万量级的软上限；超限常见表现是**画了空白 / 拿不到可用 context**。
    - 更早撞墙的是内存：一个 16384×16384 的 RGBA 画布 ≈ 1GB 像素缓冲，叠加 Q16 的分配上限，"大画布"在分配/编码两端都可能失败。
    - 超大内容对策：**分块/瓦片**——把全图按小块分别绘入不超过上限的中间 canvas 再汇入/导出（本仓库 `buildThumb` 全程不建超过 8192 边的大 canvas，按 4096 块逐步汇入中间画布，绕开面积上限）；或只渲染视口可见区，不整幅画。
    **追问：** CSS `width:100%` 也算大画布？→ 画布像素尺寸是 `width/height` 属性值；高 DPI 屏要再乘 `devicePixelRatio`，CSS 只是显示尺寸。

19. **Q：浏览器里"看超大的图"一般怎么实现：整幅缩放 vs 瓦片/金字塔/视口？（★）**
    **A：**
    - 原则：**别把整幅当一张位图放进浏览器**（会撞 2GB / 16384² 两个上限）。常见两条路线：
    - 概览图 + 视口渲染：先有一张（服务器或离线预生成的）缩略图撑住全貌；平移/缩放时把"图坐标↔画布坐标"做映射，只**渲染当前视口看到的那一小块**，需要更细时按需去取那一块的原始数据。
    - 精细查看按需取数：对"无压缩 + 每行一条带"的文件可按字节偏移直接切片读需要的行（稀疏读）；对压缩/需逐级放大的场景才建**瓦片金字塔**（预切多级小瓦片，按需加载）。
    - 取舍：金字塔适合"要看任意层级细节"；若文件规整、可字节级定位，稀疏抽样出概览就够，省掉建塔与存瓦片成本。
    **追问：** 预览长边为什么往往定在 2048~8192 量级？→ 缩略约原图的 1/3 已够人眼判断；再大既费内存又逼近 canvas 上限，收益递减。

20. **Q：canvas 2D、WebGL、WebGPU 怎么选？（★）**
    **A：**
    - **Canvas 2D**：高层 2D 光栅 API（路径/文本/图像/像素读写），内置、简单直接；适合 UI 覆盖层、标注、中等像素量、把像素结果 `putImageData` 上屏。
    - **WebGL**：基于 OpenGL ES 的低层 GPU 光栅，GLSL 着色器、状态机式；适合大量几何（十万级顶点）、游戏、逐像素 shader 特效与图像处理。
    - **WebGPU**：新一代贴近现代 GPU 的 API（概念近 Vulkan/Metal/DX12），带 **compute shader 通用计算**、显式管线与资源管理、WGSL；图形+大规模并行计算一体，适合超高吞吐像素/张量类后处理；生态与兼容仍在普及。
    - 一句话选型：改样式/标注/结果整块上屏 → 2D；几何与游戏特效 → WebGL；要 GPU 通用计算/极致吞吐 → WebGPU。
    **追问：** 像素级处理"一次算完再上屏"为何 2D 就够？→ 它不是每帧重建场景，是 CPU（或 Worker）算完一张 ImageData 后一次性直写缓冲；2D 直写最省事，WebGL/WebGPU 的管线开销是为"每帧海量图元/通用计算"准备的。

21. **Q：getImageData / putImageData 与 ImageData 的关系？怎么用才快？（★）**
    **A：**
    - `ImageData = {width, height, data: Uint8ClampedArray}`，`data` 每像素 4 字节 RGBA、0–255（8bit/通道）；`putImageData(imageData, x, y)` 把缓冲**原样直写**到画布。
    - `getImageData(x, y, w, h)` 从画布**读回**像素做成 ImageData（分配像素内存 + 拷贝；全屏 4K RGBA 约 33MB）；若画布被跨源图像污染（画过且无 CORS 授权），读取抛 `SecurityError`。
    - 语义坑：`putImageData` **不做合成**——不受变换/裁剪/全局透明度影响，像素覆盖直写；所以"程序生成/图像处理结果上屏"走它最快；要用合成与变换得靠 `drawImage`。
    - 加速：循环里别反复 `getImageData` 同一区域（每次都整块拷贝）；先整段算完再一次 `putImageData`；处理时当作 `Uint8ClampedArray`（越界自动钳 0–255）；像素级重活挪去 Worker，结果走 transfer 回传，主线程不反复拷贝。
    **追问：** 为什么 16bit 图要先造 RGBA 再 put？→ 上屏通道是 8bit RGBA；项目里把 16bit 原值按窗口拉伸量化成 RGBA 缓冲后一次 `putImageData`（`stretchRgba`，[tifDecode.ts](frontend/src/lib/tifDecode.ts)）。

22. **Q：toDataURL 和 toBlob 什么区别？canvas 导出大图有什么坑？（★）**
    **A：**
    - `toDataURL(type, q)`：**同步**编码成 data URL 字符串（base64，比二进制大约 1/3）；大图同步编码会长时间卡主线程、还放大体积。
    - `toBlob(cb, type, q)`：**异步**编码成 `Blob`，不阻塞主线程；配合 `URL.createObjectURL` 做显示/下载/上传——导出场景优先 toBlob。
    - 坑：canvas 边长超限或编码内存不够时，回调拿不到有效 Blob 甚至抛错；**编码尺寸要有封顶并按预算降档**（本项目 JPG 导出长边封顶 8192，遇内存不足自动降到 4096 重试一次，[exportJpg.ts](frontend/src/lib/exportJpg.ts)）。
    - 画布若含未授权跨源像素会变"脏"，toDataURL/toBlob 直接抛 SecurityError。
    **追问：** 导出 JPEG 为什么常垫白底？→ JPEG 无 alpha，透明会变黑，先填白再编码。

23. **Q：createImageBitmap 是干嘛的？图片解码怎么不卡 UI？（★）**
    **A：**
    - 解码通常由浏览器在解码线程/解码器做（不在 JS 栈里），但 `drawImage` 一张没解码完的图会**先阻塞等它 ready**。
    - `createImageBitmap(blob)`：**显式异步解码**（后台线程池），返回可 transfer 的 `ImageBitmap`；之后 `drawImage` 不再等解码。用完 `ImageBitmap.close()` 释放位图。
    - `img.decode()`：返回 Promise，解码完成再上屏，避免图片"闪一下"。
    - 大图内存：解码后的位图就是 `宽×高×4`；反复解码会攒缓存 → 用完 close、按需预解码、避免让浏览器解码整幅巨图；批量缩略要限并发逐张 `createImageBitmap`，别一次把几十张全尺寸解码挤爆配额。
    **追问：** "大图先给缩略概览"为何合理？→ 让浏览器只解码一张小的缩略位图，而不是整幅巨图，内存与解码成本都降到可控。

## F. 存储与离线

24. **Q：localStorage / sessionStorage / cookie / IndexedDB 各自上限和用途？（★）**
    **A：**
    - `localStorage`：约 5MB/源（典型值）、同步、只存字符串 KV、持久——**同步读写可能阻塞主线程**，适合偏好等小数据；`sessionStorage` 同 API、Tab 级、关页清。
    - cookie：约 4KB（典型），且**每次请求自动携带**（流量/安全成本）——只放会话凭证。
    - `IndexedDB`：异步、事务化、带索引的对象仓库，可存结构化数据/**Blob/ArrayBuffer 二进制**；容量由浏览器配额管理（量级远超 localStorage，够放 GB 级文件），适合离线缓存大对象与结果文件。
    - 结论：偏好→localStorage；凭证→cookie；大/结构化数据→IndexedDB；用户指定目录真写磁盘→File System Access（下题）。
    **追问：** 存不下了怎么查？→ `navigator.storage.estimate()` 报 quota/usage，`navigator.storage.persist()` 申请持久化（避免浏览器主动清站数据）；不同源配额互相独立。

25. **Q：File System Access API 解决什么？和 IndexedDB 的落盘分工？（★）**
    **A：**
    - `showOpenFilePicker/showDirectoryPicker` 经**用户手势授权**拿到句柄，能"真写本地磁盘"（`FileSystemWritableFileStream`）；浏览器对授权有时限/范围约束，重开可能需再授权。
    - 用途：让用户"导出到指定文件夹"而不是只能下载；权限模型是"用户点过才给"，适合内网工具。
    - 兜底：拿不到句柄/不支持时退回 IndexedDB 暂存——**FS Access 管"落盘体验"，IndexedDB 管"总有地方放"**。
    **追问：** 关掉页面句柄还能用？→ 把句柄存进 IndexedDB、下次请求授权可恢复；本项目 store 的 `initFsIO()` 就是恢复上次授权输出目录（封装 [saver.ts](frontend/src/lib/saver.ts)）。

26. **Q：离线应用怎么做？Service Worker 生命周期与 Cache API？（★）**
    **A：**
    - 前提 HTTPS（或 localhost）。**注册** SW → `install`（预缓存 App Shell/静态资源）→ `activate`（清旧版缓存）→ 之后请求都过 `fetch` 事件，可拦截/改写/兜底。
    - **Cache API**：`caches.open(name)` 拿 Cache，`put(request, response)` / `match(request)` 存"请求→响应"对；命中策略：`cache-first`（离线兜底）、`network-first`（要新、失败回退缓存）、`stale-while-revalidate`（先给旧的、后台更新）。
    - 版本化：缓存名带版本，activate 删旧，避免新旧资源混用。
    - 边界：SW 不能碰 DOM；更新靠"新 SW 脚本 diff"，但旧页面仍由旧 SW 控制到全部关闭 / `clients.claim()`。
    **追问：** 和纯 HTTP 缓存什么关系？→ 分层：Cache API 在 fetch 层，决定"这个请求回不回路、用哪份缓存"；HTTP 缓存是传输层；要离线必须前者真正落地。

27. **Q：为什么很多团队把第三方库"本地 vendor"而不是走 CDN？（★）**
    **A：**
    - 前提：**内网/离线/CDN 不可达或缓存不可控**。CDN 连不上就白屏；外链还引入版本/供应链不可控，首屏被外网卡住。
    - 做法：把固定版本的第三方库文件放进仓库 vendor，随应用一起构建打进产物——**产物自包含、零外链**；按依赖顺序固定引入（先依赖再依赖者）；**补丁版以本地文件为准，别被包管理器重装覆盖**。
    - 收益：离线可用、版本锁定、可审计；成本：升级手动换文件、体积进产物。
    **追问：** 怎么验证产物真没外链？→ 断网/拦截外网跑全功能回归，或扫描产物里的 `http(s)://` 外链做断言。

## G. 同源 / CORS / 实时推送

28. **Q：同源策略和 CORS 预检（preflight）是什么关系？（★）**
    **A：**
    - **同源** = 协议+域名+端口全同；同源策略是浏览器默认**不允许页面读跨源响应**。注意服务器通常照常收到请求，是**浏览器不把响应交给页面**。
    - 简单请求（GET/HEAD/POST、有限 content-type、无自定义头）直接发，浏览器看响应头 `Access-Control-Allow-Origin` 决定放不放行。
    - **复杂请求先预检**：方法非简单（PUT/DELETE/PATCH）或带自定义头/非简单 content-type → 先发一个 `OPTIONS` 探询，服务器回 `Access-Control-Allow-Methods/Headers/Origin` 通过后才发真请求；预检结果可用 `Access-Control-Max-Age` 缓存。
    - 结论：CORS 是**浏览器的访问策略，不是安全边界**（curl 不受拦）；`fetch` 开 `credentials` 时 `Access-Control-Allow-Origin` 不能用 `*`（典型）。
    **追问：** 图片/字体等资源跨源要什么头？→ `<img>` 默认可显示但读不到像素（canvas 被污染）；要读回像素（getImageData/导出）得让服务器回 `Access-Control-Allow-Origin`，再以 `crossorigin` 加载。

29. **Q：EventSource(SSE) 为什么只能 GET？要 POST 的"流式"怎么办？（★）**
    **A：**
    - `EventSource` 按规范**只支持 GET**：不能设自定义头、不能带 body → 鉴权只能走 query/cookie/`withCredentials`；优点是**自动重连 + Last-Event-ID 续传**，天然消费 `text/event-stream`。
    - 需要 POST body（发指令再收流式 token）、自定义头、精确控制连接时 → 用 **fetch 发请求再读响应流**：`response.body.getReader()` 逐块读，按空行自己切 `data:` 帧解析。
    - 帧格式（`data: {json}\n\n`）两种消费方式通用；事件类型约定详见 [http-sse.md](docs/knowledge/interview/http-sse.md)。
    **追问：** 什么时候真该上 WebSocket？→ 需要**双向**推送（聊天/协同，客户端也随时发）时；SSE 优势是单向服务器推送、自动重连、走普通 HTTP 易过代理。

## H. 实践注记：大图 / 掩码 / 离线 / 多页（本仓库真例）

30. **Q：为什么 24739×24199 的 16bit 大图不能整幅解码进浏览器？（★ 项目高光）**
    **A：**
    - 算一笔账：24739×24199 的 16bit 灰度，光像素数据 `宽×高×2 字节 ≈ 1.2GB`；要显示得转 RGBA，`×4 ≈ 2.4GB`——单次分配直接撞约 2GB 上限（`RangeError`），整幅位图也超画布面积上限。
    - 浏览器另有硬约束：**单次 ArrayBuffer 分配约 2GB 封顶**，与机器内存无关；所以"机器内存大也没用"，只能在架构上绕（教程 [platform-tutorial.md](docs/knowledge/platform-tutorial.md) 第 2 章三个"地心引力"约束）。
    - 两条并存路线：
      - **本地文件路径**：先估解码字节数，超过安全线（`SAFE = 1.3e9`，[tifDecode.ts](frontend/src/lib/tifDecode.ts)）就走**稀疏条带预览**——真实大图是"无压缩 + 每行一条带 + 单波段 16bit"，按条带字节偏移直接切片、只抽读若干行，秒级出 8192 长边缩略，**不用建金字塔**。
      - **盘阵场景（阶段 4）**：浏览器干脆**不读原始 TIF 字节**，改读服务器预"烤"好的 JPG（稀疏采样+拉伸已做进像素），一刀把 2GB / 16384² 两个约束消掉。**烘焙规则 v2(09-17)**：各边严格 **1/2**、拉伸用**直方图均衡**（旧为 8192 长边封顶 + 2% Linear）。
    **追问：** 本地稀疏预览的长边为何选 8192？→ 约是原图宽度的 1/3，够人眼判断；更大既费内存又逼近 canvas 上限。（盘阵场景已不在这个约束下：服务端烘焙走 1/2 尺度，不受浏览器 8192 常量影响。）真要看细节时按需回源取局部（瓦片/重采样，见 Q19），概览常驻的始终是一份小内存。
    🗂 **可讲成项目经历：** "我定原则'先出概览、别碰全图'：决策函数先算解码字节数再分派 UTIF / 分块 / 稀疏三路"（[decode.ts](frontend/src/lib/decode.ts)）。

31. **Q：栅格化掩码/连通域这类重计算，为什么"逐行流式 + 让出主线程 + 进度条"而不冻结？（★）**
    **A：**
    - 思路是**分片预算 + 主动交还**：整图处理切成小批（每批约 128 行、单批操作预算约 13 万次、目标单批 <16ms），跑完一批 `yield` 一次并上报 `{phase, progress}`；主线程用 `setTimeout` 驱动下一步——批与批之间浏览器有机会渲染，页面不冻结、进度条实时刷。
    - 同一算法写成**生成器**逐步 `yield`：**浏览器端异步驱动**（能刷进度、交还主线程），**Node 测试端同步驱动**到完成——一套逻辑两种消费，行为一致（[maskgen.ts](frontend/src/lib/maskgen.ts) 的 `mergeConnectedAsync`）。
    - 附加优化：内存上"按需分配、连通域只在自身 bbox 子图重扫"而不是整图再分配一遍。
    **追问：** 为何不直接全塞 Worker？→ 能搬则搬，但这类"边画边反馈（选区/描边预览）"与 DOM 交互状态同步在主线程更直接；分片让出+进度已满足体验。
    🗂 **可讲成项目经历：** "重循环不整段同步跑：生成器 + 每批让出 + 进度回调；测试与浏览器共用同一生成器，只换驱动方式。"

32. **Q：为什么第三方库全本地 vendor 进产物？（★）**
    **A：**
    - 直接原因：目标环境**内网离线、CDN 不可达**，外链会白屏（硬约束同教程 [platform-tutorial.md](docs/knowledge/platform-tutorial.md) 第 2 章）。仓库把 pako / utif / geotiff 放本地 vendor，`main.ts` 先 `import './vendor/vendor'`，按**固定依赖序**（pako → 依赖它的 utif → geotiff）接线，产物自包含、无 CDN 引用（[main.ts](frontend/src/main.ts)）。
    - 还踩过"补丁库被 npm 重装覆盖"：utif 是**补丁版**（cmpr 8/32946 走 pako inflate），必须从本地 vendor 文件引入而不能靠 node_modules 解析（[vendor/vendor.ts](frontend/src/vendor/vendor.ts)）——这正是"本地文件为准、锁定补丁"的理由。
    - 一致性：Vue 工程与旧单文件 [tif-viewer.html](tif_viewer/tif-viewer.html) 用同一套 vendor、同一引入顺序，行为不漂移。
    **追问：** 本地 vendor 的坑？→ 升级手动换文件；补丁随源码审计；顺序错会依赖缺失。
    🗂 **可讲成项目经历：** "我给 vendor 加断网全功能回归，保证产物零外链也完整可用。"

33. **Q：多页共享查看器为什么是"组件 + store 集中"，canvas 状态收进来？（★）**
    **A：**
    - 页面多了（聊天/队列/场景/查看器共用路由与状态）之后，"谁改了视图、下一步拿什么"必须可预测 → 状态收进一个 Pinia store 集中管，组件只负责渲染与派发动作。
    - canvas 本身仍是**组件实例**（各自持上下文），但它只"按 state 画"：store 放一个重绘信号（`renderTick`），任何影响视图的变更 `renderTick++`，canvas 组件 watch 到就重绘一次——等价"状态变更后统一刷新"，避免各处直接互调重绘画乱。
    - 大字段（Float32Array 源 / thumb canvas / 统计）用 `markRaw` 放，避免 Vue 深代理包重型结构。
    **追问：** 为何不全局对象一把梭？→ 要响应式 + 跨页共享 + 可测：store 把动作与 DOM 直改解耦，组件可替换、行为可单测（[viewer.ts](frontend/src/stores/viewer.ts) + [TifCanvas.vue](frontend/src/components/TifCanvas.vue)）。
    🗂 **可讲成项目经历：** "我把旧 HTML 全局状态按'组件 + store'重写：交互/掩码/导出状态全在 store，TifCanvas 只对 renderTick 重绘；换图/并发导出靠 token 守卫防旧副作用回写。"

## 速记：本项目数字卡（背到脱口而出）

> 讲项目的"数字与取舍"要能脱口而出，这是比背八股更大的区分度；来源见各题引用代码。

- 单次分配安全线 `SAFE = 1.3e9`（约 1.2GB）——解码字节数超过就判定"不能整幅读"，改走分块或稀疏（[tifDecode.ts](frontend/src/lib/tifDecode.ts)）。
- 浏览器单次 ArrayBuffer 分配上限约 **2GB**（与机器总内存无关）；canvas 面积上限 **16384² = 268,435,456**。
- 真机代表图 24739×24199 · 16bit：像素数据 ≈ **1.2GB**，转 RGBA ≈ **2.4GB** → 不能整幅解码。
- 缩略/JPG 导出长边封顶 **8192**（≈原图 1/3）；掩码重循环每批 **128 行**、单批操作预算约 **13 万**次、目标 <16ms。
- 数据源两套并存：本地文件走**稀疏条带读**；盘阵场景走**服务器预生成 JPG**（浏览器不碰原 TIF 字节）。后者 v3(09-19) 起为**各边 ÷2…÷32 五档可选、默认 ÷4 + 直方图均衡**（档位进规则戳），与浏览器侧的 8192 常量是两条独立的路。
- 第三方库 **pako → utif（补丁版）→ geotiff** 全本地 vendor、固定引入顺序，产物零外链。

## 再深挖去哪个文件

- HTTP 方法/状态码/缓存/HTTPS/CORS 细节与推送协议对照 → [http-sse.md](docs/knowledge/interview/http-sse.md)。
- Vue3 响应式与"状态收 store"的理论支撑 → [vue.md](docs/knowledge/interview/vue.md)；语言侧事件循环/TypedArray → [js-ts.md](docs/knowledge/interview/js-ts.md)。
- Python 侧并发/异步对照 → [python-concurrency.md](docs/knowledge/interview/python-concurrency.md)。
- 本项目完整因果（约束/选型/部署）→ [platform-tutorial.md](docs/knowledge/platform-tutorial.md)；真实 bug 复盘 → [gui-experience.md](docs/experience/gui-experience.md)。

## 一句话备忘

> **URL→页面 = DNS/TCP/TLS + 关键渲染路径（DOM/CSSOM/渲染树/布局/绘制/合成）；卡顿 = 长同步任务没让出主线程（微任务让不出渲染，要分块 + setTimeout/Worker）；内存看两个上限——约 2GB/次的 ArrayBuffer 分配与 16384² 的 canvas 面积，大图只能"分块/稀疏/让服务器预生成小图"；大对象能 transfer 就 transfer，能 WeakMap 就 WeakMap；离线三件套 = 本地 vendor + Service Worker/Cache API + IndexedDB；SSE 单向用 EventSource，要 POST/自定义头就 fetch 读流。**
