# Python/并发 面试八股（interview · 高频精简版）

> 定位：跳槽高频 Python 语言 + 并发/异步自测与背诵。★=高频。每题「答（要点）+ 追问」。
> 仓库真例标注 🗂：答完八股，能用 [backend/services/store.py](backend/services/store.py)、[backend/services/slurm.py](backend/services/slurm.py) 等真实文件讲成"项目实践"。阶段 5（聊天 SSE 的 `run_loop` → to_thread → asyncio.Queue）处于**规划/评审中**（契约 [docs/planning/api-contract.md](docs/planning/api-contract.md) §4.1），引用处均标注，不当作已实现事实。

---

## 一、Python 语言基础（对象 / 内存 / 语法）

1. **Q：可变默认参数 `def f(x=[])` 为什么是坑？怎么破？（★）**
   **A：**
   - 默认值在 **def 语句执行时只求值一次**，之后所有调用共享同一个对象；参数是可变容器时，函数内 `x.append(...)` 会**跨调用累积**，上一轮输入污染这一轮。
   - 默认值存在函数对象上：`f.__defaults__` 能直接看到那个被污染的 list。
   - 规范写法：可变默认置 `None` + 函数体内重建；要么用不可变默认（`()`/`0`/`""`），必要时用哨兵对象避免"None 也是合法输入"的歧义。

   ```python
   def f(x=None):
       x = [] if x is None else x
       x.append(1)
       return x
   ```

   **追问：** 同类坑还有哪些？→ dataclass 字段要用 `field(default_factory=list)`；`d.get(k, [])` 后 append 不会写回原字典；类属性放 list 也是共享（见 Q3）。

2. **Q：深拷贝和浅拷贝什么区别？哪些操作其实是浅拷贝？（★）**
   **A：**
   - 赋值 `b = a` 只是**共享引用**，改一个都变；拷贝才产生新对象。
   - `copy.copy` **浅拷贝**：新建最外层对象，**内层元素仍共享**——改嵌套子对象，原/新两侧都可见。
   - `copy.deepcopy` **深拷贝**：递归复制全部层级，结果与源完全独立。
   - 常见"以为深了其实浅"：切片 `lst[:]`、`list(lst)`、`dict(d)`、`d.copy()` 全是浅拷贝；如 `a = {"k": [1, 2]}; b = a.copy(); b["k"].append(3)` → `a["k"]` 也变。
   - 业务上先问"能否共享只读内层/用不可变内层"，别一律 deepcopy（贵）；可自定义 `__deepcopy__` 控制。

   **追问：** deepcopy 处理循环引用/重复对象靠什么？→ 内部 memo 表：遇到已复制对象直接返回副本，不会无限递归。`copy` 是共享还是复制？→ 看层级：最外层复制、内层共享，记"浅 = 外新内旧"。

3. **Q：类变量和实例变量什么区别？通过实例改类属性会怎样？（★）**
   **A：**
   - 类体内直接赋值 = **类属性**（`cls.attr`，实例共享）；`__init__` 里 `self.x = ...` = **实例属性**（每实例独立）。
   - 查找顺序：**实例 → 类 → 父类（按 MRO）**，实例没有才往上找。
   - 坑一：`obj.attr = v` **不改类属性**，只是给该实例建同名遮蔽字段。
   - 坑二：**就地修改**可变类属性（`obj.items.append(x)`）改的是共享对象，**影响所有实例**；想要各实例独立就在 `__init__` 里 `self.items = list(self.__class__.items)` 复制一份。
   - 类体只写 `x: int`（无赋值）不建属性，注解只是元数据。

   **追问：** 举一个共享坑？→ 类属性 `items = []`，两个实例都 `append`，互相看见。`type` 和 `isinstance` 里 MRO 的作用？→ isinstance 按 MRO 逐级判断，所以子类实例也是父类实例。

4. **Q：MRO 是什么？`super()` 到底指向谁？菱形继承会调几次共同祖先？（★）**
   **A：**
   - Python 多继承按 **C3 线性化**计算解析顺序（`cls.__mro__` 可查看）：每个基类只出现一次、且满足所有父类相对次序。
   - **菱形继承** D(B, C)，B、C 都继承 A：`D().m()` 沿 MRO 只执行 A 的 m **一次**，不重复、不乱序。
   - `super()` **不是"取父类"**，而是沿 MRO 返回**当前类的下一站**；类体内裸 `super()` 靠闭包的 `__class__` 单元格自动定位，是最正确姿势（手写 `super(Sub, self)` 改类/复制时易断链）。
   - 协作前提：**每个中间类都调 `super().__init__()`**，链才不断（D→B→C→A，A 只 init 一次）；漏写则父类不初始化。

   **追问：** 为什么打印顺序是 B、C 再 A？→ C3 广度优先。`super()` 在类外（模块/函数）能用吗？→ 零参 `super()` 只在类方法体内有效；`super(Sub, obj)` 在类外显式传参可用。

5. **Q：迭代器协议、生成器、`yield from` 各是什么？（★）**
   **A：**
   - 迭代器协议：实现 `__iter__`（返回自身）+ `__next__`（逐个产出、耗尽抛 `StopIteration`）；`for` 本质是循环调 `next`。
   - 生成器：函数体含 `yield`，**调用不执行**、返回生成器对象；特性是**惰性**（边取边算，读大文件/无限序列省内存）与**保存状态**（yield 处挂起，next 恢复）。
   - 生成器**单次遍历**：迭代完即耗尽，要重来必须重新创建。
   - `yield from gen` 把控制权**委托**给子生成器：转发 next/send/异常，并能取回子生成器 `return` 的值（3.3+）。

   **追问：** `.send(v)` / `.throw(e)` / `.close()` 干嘛？→ 向 yield 表达式传值/注入异常/触发 finally 清理。asyncio 来源？→ 3.3 用 `yield from` 模拟协程，3.5 才有原生 `async/await`，本质都是"挂起/恢复"。

6. **Q：装饰器怎么写？为什么必须 `functools.wraps`？（★）**
   **A：**
   - 装饰器是接收函数、返回函数的**高阶函数**；`@deco` 语法糖 = `f = deco(f)`。
   - 无参装饰器两层（外层收函数、内层 `wrapper(*a, **kw)` 包装并调用原函数）；**带参** `@deco(n=3)` 需三层：最外层收参数返回真正的装饰器。
   - `functools.wraps(fn)` 把 `__name__/__doc__/__wrapped__` 拷到 wrapper，否则函数名/文档丢失，调试与依赖注入 introspection 全乱。
   - 常见应用：日志/计时/缓存/权限/重试；同函数叠多个装饰器 = **定义时自下而上**应用、调用时自上而下执行。

   **追问：** functools 还有哪些高频？→ `lru_cache/cache`（同参缓存，参数须可哈希）、`partial`、`singledispatch`、`cached_property`。不用 wraps 出什么错？→ `__name__` 全变 wrapper，测试/文档/Swagger 都显示错函数名。

7. **Q：`with` 上下文管理器协议是什么？`__exit__` 返回 True 干什么？（★）**
   **A：**
   - `with expr as x:` 先调 `expr.__enter__()` 把资源赋给 x，块结束（含异常/return/break）必调 `__exit__(exc_type, exc_val, exc_tb)` 释放。
   - `__exit__` 返回 **True = 吞掉异常**不再向上抛；False/None = 异常继续传播。
   - 用途：文件/锁/连接/事务——**异常路径也保证清理**；`with lock:` 就不怕忘 release。
   - 生成器式写法：`@contextlib.contextmanager` 让 `yield` 前当 enter、yield 后当 exit（放 finally 最稳），少写一个类。

   **追问：** `__exit__` 里抛新异常会怎样？→ 会替换原异常（换错前想清楚，必要用 `raise ... from None`）。`ExitStack` 干嘛？→ 运行时**动态**进出任意数量资源。块内 `return` 还执行 `__exit__` 吗？→ 会，离开 with 块必走退出协议。

8. **Q：Python 内存管理：引用计数 + 分代 GC 分别管什么？（★）**
   **A：**
   - CPython 以**引用计数**为主：每对象记被引用次数，**归零立即回收**——确定性、及时，内存峰可控；del/出作用域/容器删元素都会递减。
   - 短板是**循环引用**（a↔b 互指，计数永不归零）→ 由 `gc` 模块的**分代回收**兜底。
   - gc 分 0/1/2 三带：新对象进 0 代，熬过几次回收就**晋升**，按阈值周期扫高代找"不可达环"——高频临时对象在低代常扫、老对象不常扫，性价比高。
   - 引用计数是"确定性为主 + 周期性 gc 补环"的混合方案，不是纯 mark-sweep。

   **追问：** `gc.disable()` 危险吗？→ 普通对象靠引用计数没事，**环会泄漏**。环 + `__del__` 的历史坑？→ 有 `__del__` 的对象在环里无法确定销毁顺序。破环惯用 `weakref`（不增计数）。`__slots__`？→ 省内存但禁动态加实例属性。

9. **Q：`raise ... from`、异常链、`from None` 各什么时候用？（★）**
   **A：**
   - `raise B from A` 把 A 记为 B 的 `__cause__`，回溯显示"A 是 B 的直接原因"。
   - 处理 A 时裸写 `raise B`，Python **自动**把 A 记为 `__context__`（隐式链），仍显示两段栈。
   - 想向上抛"业务错误"又不让内部细节刷屏 → `raise B from None` **抑制上下文**，栈最干净。
   - `traceback.print_exc()` 打印异常及链；except 里无参 `raise` 原样重抛、保留原栈（常用于"记日志后重抛"）。

   **追问：** except 里 `raise` 与 `raise B` 区别？→ 前者原样重抛保留原 traceback；后者新建异常、链上原异常。什么时候用 `from None`？→ 上游异常是纯噪音（已被归一成业务错误如 404/超时）时。

## 二、并发模型选型

10. **Q：GIL 是什么？为什么存在？影响什么？（★）**
    **A：**
    - GIL（全局解释器锁）是 CPython 的互斥量：**同一进程同时只允许一个线程执行 Python 字节码**。
    - 存在理由：CPython 的对象内存管理（引用计数 + 共享容器）不线程安全；**一把全局锁**比每对象细粒度锁简单，保住单线程性能，也让大量假定持 GIL 的 C 扩展（numpy 等）不用改。
    - CPU 密集：多线程**并不了行**（字节码被串行，是"并发"不是"并行"）→ 用多进程。
    - IO 密集：多线程**有收益**——阻塞 IO/系统调用时**释放 GIL**，别的线程趁机跑；另有默认约 5ms 强制切换防饿死。

    **追问：** Python 3.13 还有 GIL 吗？→ 提供**无 GIL 的 free-threaded 实验构建**，默认发行版仍有，生态/C 扩展是门槛。`sys.setswitchinterval` 调切换粒度。别答成"GIL 使 Python 不能并发"——是 **CPU 并行受限**，IO 并发一直可以。

11. **Q：进程 / 线程 / 协程的区别与切换代价？（★）**
    **A：**
    - **进程**：OS 的**资源分配单位**，独立地址空间，隔离最干净（一个崩不影响别的）；切换 = 内核上下文切换 + 换页表/TLB 等，**代价最大**；通信靠 IPC（管道/共享内存/信号）。
    - **线程**：OS 的**调度单位**，同进程共享内存、栈独立；切换由内核调度，仍要进内核，比进程轻；共享数据需加锁；Python 线程外再叠一层 GIL。
    - **协程**：**用户态、单线程内协作式**；在 `await` 点把控制权交还事件循环，就绪后恢复，**不进内核**，切换代价最低（纳秒~微秒）。
    - 代价的代价：协程前提是**每个 await 点都主动放权**，一旦写同步阻塞不 await，会**卡死整条循环里所有人**。

    **追问：** 为什么协程能单线程服务海量连接？→ 等待 IO 时挂起、等待者本身开销极小（一个状态对象），几万并发可行。选型本质一句话？→ **CPU 并行要进程，海量 IO 等待要协程，凑合同步代码要线程池**。

12. **Q：多线程 vs 多进程 vs asyncio 怎么选？（★）**
    **A：**
    - CPU 密集 → **多进程 / ProcessPool**（绕开 GIL，真并行）；
    - IO 密集且库有 async 版 → **asyncio**（单线程承载海量连接，开销最小）；
    - IO 密集但只能同步阻塞（requests 等）→ **ThreadPoolExecutor**（阻塞时 GIL 释放，能并发）；
    - asyncio 不适合纯 CPU 长循环与重阻塞调用（除非 `to_thread` 丢走，见 Q19）；三者不是互斥，常混用。

    | 维度 | 多线程 | 多进程 | asyncio 协程 |
    | --- | --- | --- | --- |
    | Python 内 CPU 并行 | 无（GIL） | 有（各带解释器） | 无（单线程） |
    | IO 并发能力 | 好 | 好但 IPC 重 | 最好 |
    | 共享状态 | 同进程易，需锁 | 靠 IPC/序列化 | 同线程仍需小心 await 边界 |
    | 适用 | 同步阻塞库并发 | CPU 重算/隔离 | 原生 async 高并发 IO |

    **追问：** 进程池为什么慢在"通信"？→ 参数/返回值要 pickle 跨进程搬运，小任务高频切分反而亏。决策口诀？→ 问"阻塞源是 CPU 还是 IO + 库有没有 async 版"。实战组合？→ asyncio 主流程 + 局部 `to_thread`/进程池跑脏活。

13. **Q：`threading.Thread` 与 ThreadPoolExecutor / ProcessPoolExecutor 区别？（★）**
    **A：**
    - `threading.Thread` 是手工起线程的底层 API（start/join，贴近 OS、样板多）；`concurrent.futures` 的线程/进程池是**统一高级接口**。
    - 池用法：`submit(fn, *args)` 返回 **Future**，`future.result()` 阻塞取结果（可 `timeout=`）；`map()` 批量、按输入序取结果；池内部复用线程/进程，省去每次创建销毁。
    - 两者接口**同构**：换 `ThreadPoolExecutor` ↔ `ProcessPoolExecutor` 即切换策略。
    - `with Executor() as e:` 退出时 `shutdown(wait=True)` 等全部跑完。

    **追问：** ProcessPool 在 Windows 上注意什么？→ 参数/返回值须可 **pickle**；Windows 是 **spawn**（子进程重新 import 主模块），顶层可执行代码要包 `if __name__ == "__main__":`，否则递归起进程。`submit` vs `map`？→ map 按序惰性取；submit 拿 Future 可独立/乱序处理。

## 三、并发同步与竞态

14. **Q：GIL 下我的操作还是原子的吗？`Lock` 和 `RLock` 区别？死锁怎么防？（★）**
    **A：**
    - **GIL ≠ 你的代码原子**：它只保证同一时刻一条线程跑字节码，不保证**跨多条字节码的复合操作**不被插队。
    - 反例：`x += 1` 是 LOAD/ADD/STORE 三条字节码，线程可中间切换 → N 线程下 `count += 1` 丢更新；"先 check 再 act"同理。
    - 内置容器**单步**操作（`list.append`、dict 单键读写）在 CPython 表现原子，但**语言规范不背书**——规范答案是加锁。
    - `threading.Lock` 互斥锁**不可重入**：同一线程二次 acquire 会**死锁**；递归/嵌套取锁用 `RLock`（acquire/release 次数对称）。
    - 死锁 = 多把锁 + 循环等待。缓解：**全局固定取锁顺序**、`acquire(timeout=...)` 兜底、`with lock:` 保证释放、**持锁期别做外部 IO/长活**。

    **追问：** 为什么"单条 append 没锁测试也过"？→ 单字节码完整执行是 CPython 常态，但"长度+切片"两段式、多容器一致性就破功。除了锁还有轻方案？→ `queue.Queue` 把共享收进队列（消息传递），少裸共享就少竞态。

15. **Q：并发下共享状态与竞态是怎么回事？asyncio 单线程就安全吗？（★）**
    **A：**
    - 竞态 = 执行顺序不确定导致结果依赖调度；多线程/多进程/多协程都有，只是错法不同。
    - asyncio **不是免死金牌**：task 在事件循环里交替，任何 `await` 都是**让出点**——跨 await 分两步读改写同一状态，期间别的 task 可能改了它。
    - 高危模式：check-then-act、read-modify-write 跨 await（如先查余额再 `await` 扣款，双双通过检查）；共享对象跨 await 复用当临时变量。
    - 防御：加锁（`asyncio.Lock`，Q18）；把共享收敛给单一拥有者（**队列/消息传递**）；跨 await 只持有不可变快照。

    **追问：** 怎么定位竞态？→ 难复现：加时间戳日志、缩小共享面、把并发改串行看是否消失。服务器最常见竞态其实是**复用一个连接/游标跨 await/线程**（对应附 Q31 的单连接懒建取舍）。

## 四、asyncio 与事件循环

16. **Q：asyncio 事件循环原理是什么？为什么同步阻塞会卡死循环？（★）**
    **A：**
    - 事件循环 = 单线程调度器，维护"就绪回调/定时器"，反复"取就绪 → 执行 → 取下一个"。
    - `async def` **调用不执行**，返回协程对象；被 `await`/`asyncio.create_task()` 调度才跑。可 await 三类：协程对象、Future、Task（create_task 才并发，裸 await 是串行等）。
    - 协程执行到 `await` 时**挂起自己**：把"结果就绪后恢复"登记进循环并交还控制权；结果一到循环回调恢复它——单线程并发由此而来。
    - **最大坑**：`time.sleep`、同步网络/磁盘、CPU 长循环等**真阻塞不经过 await、不放权** → 整个循环所有协程一起卡死。

    **追问：** `await` 与 `yield` 关系？→ 3.3 用 `yield from` 模拟协程，3.5 引入原生 `async/await`，语义同源（挂起/恢复）。为什么 await 一个 CPU 长算也白搭？→ 被 await 的东西自己不让出，循环照样被占。`asyncio.run(main())` 管建/关循环。

17. **Q：asyncio.Queue 生产者—消费者怎么写？背压是什么？（★）**
    **A：**
    - `asyncio.Queue` 是**协程安全**的 FIFO，`await put(x)` / `await get()`，专为事件循环内任务协作设计。
    - 设 `maxsize` 即**背压/流控**：满时 put 挂起、空时 get 挂起——消费不动就反压生产，不会无限堆积内存。
    - 典型：N 生产者 `await put`，M 消费者 `while True: item = await q.get(); 处理; q.task_done()`；`await q.join()` 等全部 task_done 再收尾。
    - `put_nowait/get_nowait` 是不等待版，满/空抛异常；常用于限流、流水线解耦、事件扇出。

    **追问：** 谁拿哪条任务？→ 任一空闲消费者，负载分摊。跨线程能用它吗？→ **线程不安全**；跨线程要 `loop.call_soon_threadsafe(q.put_nowait, item)` 把入队放回事件循环线程（见 Q30 的桥）。

18. **Q：asyncio 的锁和信号量怎么用？和 threading 锁能混吗？（★）**
    **A：**
    - `asyncio.Lock` 保护事件循环内**跨 await 的临界区**；`asyncio.Semaphore(n)` 限**同一时刻最多 n 个协程**进入——互斥锁是信号量=1 的特例。
    - Lock 语法 `async with lock:`；Semaphore 典型用途是限并发：最多 N 路 HTTP/任务在跑。
    - asyncio 锁与 `threading` 锁**不通用**：事件循环里拿线程锁再 await = 死锁。
    - 相关：`asyncio.Event`（条件成立唤醒一批等待者）、`Condition`（细粒度等待/通知）。

    **追问：** 单线程为何还要锁？→ 有**交替竞争**：await 边界被别的 task 插队，跨 await 的读改写需要锁（Q15）。锁粒度？→ 只锁"读→校验→改共享"的窄段，**别把整个 IO 包进锁**（持锁等待=吞吐杀手）。

19. **Q：`to_thread` / `run_in_executor` 什么时候用？（★）**
    **A：**
    - `asyncio.to_thread(func, *args)`（3.9+）把**同步阻塞函数丢进默认线程池**执行，await 期间事件循环继续跑别的任务，结果回来再续。
    - 等价 `loop.run_in_executor(None, func, ...)`；`run_in_executor` 可指定执行器——传 `ProcessPoolExecutor` 让同步 CPU 活去多进程。
    - 判据：凡同步阻塞（阻塞网络/磁盘/无 async 版库/CPU 长算）且不想让循环停摆 → to_thread 就是"老同步代码进 async 世界"的标准缝；典型写法 `result = await asyncio.to_thread(os.path.getsize, huge)`。

    **追问：** 它是银弹吗？→ 不是：默认线程池有共享上限（几十个），海量并发全 to_thread 会排队；纯 CPU 该用进程池。什么时候宁可不用？→ 高 QPS 路径能换 async 库（httpx.AsyncClient）就别绕线程池。

20. **Q：requests、OpenAI 同步 client 这类同步阻塞库，在 async 里怎么处理？（★）**
    **A：**
    - requests、同步 `OpenAI` 无协程实现，在 `async def` 里直接调用会**同步阻塞事件循环**——一个慢请求冻住全部并发，是错误示范。
    - 正确做法三条，按优先级：① **有 async 版就用 async 版**——HTTP 用 `httpx.AsyncClient`/`aiohttp`；LLM 用 `AsyncOpenAI`（同一 SDK 的 async 门面，方法前 `await`），全程原生 async、不占线程；
    - ② 没有 async 版 → `await asyncio.to_thread(sync_call, ...)` 丢线程池（Q19）；③ FastAPI 里干脆写普通 `def`，让框架统一丢线程池（Q21）。

    **追问：** AsyncOpenAI 和 OpenAI 差异？→ 同 SDK 两个门面，构造参数一致，async 方法须 await，底层可注入 httpx 传输。client 建几次？→ **模块级单例**复用连接池，别每次新建。判断一条库阻不阻塞？→ 看返回协程还是等到网络返回。

## 五、FastAPI 落地（两种 def、SSE）

21. **Q：FastAPI 里 `async def` vs 普通 `def` 有什么差别？怎么选？（★）**
    **A：**
    - FastAPI/Starlette 按函数**是不是 async def** 决定执行位置：普通 `def` → 塞进**线程池**执行，不占也不卡事件循环；`async def` → 直接跑**事件循环**，一请求一协程。
    - 选型看函数体：体内**全部是原生 async 调用** → 写 `async def`（高并发 IO 最省线程）；只要含**同步阻塞**（requests/sleep/CPU 重算/文件 IO）→ 写普通 `def`，框架自动替你包线程池。
    - 反过来，**在 async def 里手写同步阻塞才是灾难**（Q20）——你要么全程 async、要么交给线程池。

    **追问：** 普通 def 是"自动 to_thread"吗？→ 是等价物，但共享**有限线程池**，重度阻塞用户多照样排队——超重活另想（进程池/队列，见 Q30 方向）。async def 里调普通 def 函数？→ 同步执行白占循环，要并发得 to_thread。

22. **Q：事件循环里要跑"同步阻塞业务循环"，怎么转成 SSE 逐帧推送？（★）**
    **A：** 核心是**桥**四步：
    - ① 同步主流程丢进线程池（`asyncio.to_thread`），不占事件循环；
    - ② 在它的每个产出点**同步回调** `on_event(evt)` 抛事件，同步逻辑本身零 async；
    - ③ 回调把事件放进 `asyncio.Queue`——跨线程安全做法 `loop.call_soon_threadsafe(q.put_nowait, evt)`；
    - ④ 事件循环主协程 `while` `await q.get()` 逐帧拿事件，`yield` 给 `StreamingResponse`（SSE 每帧 `data: {json}\n\n`，配 `Cache-Control: no-cache`）。

    ```python
    q = asyncio.Queue()
    bridge = lambda evt: loop.call_soon_threadsafe(q.put_nowait, evt)
    asyncio.to_thread(run_loop, cfg, content, on_event=bridge)
    # 主协程: while (evt := await q.get()): yield sse_frame(evt)
    ```

    **追问：** 收益与代价？→ 同步代码不改、循环不卡、事件按真实顺序逐条下发；客户端中途断开须能通知/取消线程池任务，否则后台泄漏。为什么不直接在线程里写响应？→ 响应对象是事件循环侧资源，跨线程写要小心，队列是干净边界。这就是 Q30 讲的本仓库契约（§4.1）通用化。

## 六、类型 / 数据结构 / 格式 / 导入

23. **Q：dataclass 与类型注解（typing）怎么用？和 Pydantic 什么区别？（★）**
    **A：**
    - `@dataclass` 按字段注解自动生成 `__init__/__repr__/__eq__`（可选 `order/frozen/slots`）；可变默认字段用 `field(default_factory=list)`；`frozen=True` 才稳定可哈希（能当 dict key）。
    - typing 常用：内置泛型 `list[int]`（3.9+）；`Optional[X]` = `X | None`（3.10+ 用 `|`）；`Literal/Tuple/Any`；`Protocol` 描述结构子类型（鸭子类型静态化）；`NewType/Annotated` 加语义。
    - 模块头 `from __future__ import annotations` 让注解惰性字符串化，避免运行期求值导致循环导入/前向引用（配合 Q26）。

    **追问：** dataclass 和 Pydantic 区别？→ dataclass 只是容器（不校验、不编解码）；Pydantic 基于注解做运行期校验 + JSON 编解码，FastAPI 模型用它。注解运行期生效吗？→ 默认求值（可能 NameError/循环导入），future import 后变字符串、交给 mypy/pyright 静态消费。

24. **Q：dict / set / list 常用操作与时间复杂度？（★）**
    **A：**
    - list 是数组：下标访问、末尾 `append/pop` **O(1)**；**中间 `insert/remove` 与 `x in list` 是 O(n)**——大列表里 `in` 是头号坑。
    - dict/set 是哈希表：`get/set/del/in` **平均 O(1)**、最坏 O(n)（碰撞）；dict 3.7+ 保**插入顺序**；set 无序、元素须可哈希（list 不行，tuple 行）。
    - 工具类：`Counter` 计数、`defaultdict` 缺键默认、`deque` 头尾 O(1)（当队列用，别用 `list.pop(0)` 的 O(n)）。
    - 惯用：`lookup = set(huge_list)` 后 `in` 变 O(1)；`sorted()` 是 O(n log n)。

    **追问：** `dict.get(k, 默认)` 省什么？→ 一次查表，免 `if in` 两趟；但**无法区分"键在但值 None"与"键缺失"**。幂等删除？→ `d.pop(k, None)`。哈希表当 key 需可哈希：list 不行、frozenset 行。

25. **Q：f-string 与格式化有哪些常用花活？（★）**
    **A：**
    - 花括号里写**表达式**而非只变量：`f"{x:.2f}"` 小数位、`f"{x:>10}"`/`f"{x:05d}"` 对齐补零、`f"{n:,}"` 千分位、`f"{d['k']}"` 字典取值。
    - `f"{v=}"` 自动展开成 `v=值`（调试神器）；`f"{x!r}"` repr；`f"{name} {score:.1f}"` 混排最常用。
    - 旧式对照：`"%.2f" % x`、`str.format`；性能 f-string 通常最优（编译期处理）。
    - **3.12 加强**：f-string 允许复用同引号、支持跨行多行表达式（此前要临时变量绕行）。

    **追问：** f-string 拼 SQL/HTML 有隐患？→ 只是拼串不转义，拼用户输入会注入 → 参数化查询/模板引擎转义。3.12 前字典 f-string 同引号怎么处理？→ 错开引号（外单内双或反之）或先取临时变量。

26. **Q：包结构 / 导入：`__init__.py`、绝对 vs 相对导入、循环导入？（★）**
    **A：**
    - 包 = 目录 + `__init__.py`（3.3+ 命名空间包可省，显式加才能控导出、放公共面）。`__init__.py` 做**重导出**（`from .store import Store`，让 `from pkg import X` 直通）、设 `__all__` 约束 `from pkg import *`。
    - **绝对导入**（`import pkg.mod`）最稳、可整目录搬动，默认推荐；包内互引可用**相对导入**（`.` 本包、`..` 父包），但**脚本作为 `__main__` 直接运行时相对导入会挂**（顶层查找只认 `sys.path`）。
    - **循环导入**解法：import 下移进函数体（延迟到调用时）；类型注解处用 `from __future__ import annotations` + `if TYPE_CHECKING:` 只在静态期导入。
    - 🗂 本仓库分层：`services/`（store/slurm/mask…）、`tools/`、`api/` 各成包，用 `__init__.py` 收口公共面，REST/CLI/Agent 复用同一批 service。

    **追问：** `if __name__ == "__main__":` 干什么？→ 同一文件既被 import 又可直接运行的分界，入口放它下面；**Windows spawn/多进程会重 import 主模块**（Q13），顶层裸代码会重复执行。`__all__` 只影响 `from x import *`，不拦显式导入。

## 七、测试与版本差异

27. **Q：单元测试 unittest / pytest：fixtures、monkeypatch 的思想是什么？（★）**
    **A：**
    - unittest 是内置 xUnit（`setUp/tearDown`、`assertEqual`/`assertRaises`）；pytest 更流行：裸 `assert`、`@pytest.mark.parametrize`、**fixture 依赖注入**（`tmp_path` 临时目录、`monkeypatch` 现场替换并自动还原、`capsys` 抓输出）、`conftest.py` 共享目录级 fixture。
    - **替身原则**：只替**外部副作用边界**——网络/子进程/时钟/随机/磁盘写；别 mock 被测逻辑本身，否则测试永远绿、测了个寂寞。
    - **monkeypatch 背后的思想是"给外部副作用留注入缝"**：把副作用做成可注入参数/依赖，测试传 fake 实现，比全局 monkeypatch 更确定、可离机（本仓库 `slurm.py` 的 `run_cmd=` 即此缝，见 Q29）。

    **追问：** mock 和 fake 区别？→ mock = 记录调用并返回预设的替身（断言"被调了没/带什么参"）；fake = 有真实行为的轻量实现（内存假存储/假调度器）。想断言抛异常？→ `with pytest.raises(ValueError)`。为何注入优于到处 monkeypatch？→ 缝在签名里可见、不依赖内部实现、改名重构不破测。

28. **Q：Python 3.10/3.11/3.12 各有什么要点？（match、ExceptionGroup、TaskGroup…）（★）**
    **A：**
    - **3.10**：结构模式匹配 `match-case`（按结构/类型解构 + 守卫，适合命令/路由分发）、类型联合写 `X | Y`（PEP 604）、带括号多行 with。
    - **3.11**：**异常组 `ExceptionGroup` + `except*`**（一批异常可分别接住、未接住的继续外抛）、**`asyncio.TaskGroup`**（结构化并发：组内统一/联动取消，一个失败取消同组）、异常定位与启动提速。
    - **3.12**：类型参数语法简写 `def f[T](x: T) -> T`（PEP 695）、f-string 加强（同引号/跨行）。
    - **3.13**：无 GIL 的 free-threaded 实验构建、JIT 起步。面试主答 match 与异常组/TaskGroup 两个差异点即可，别背成词典。

    **追问：** `except*` 和 `except` 区别？→ 普通 except 只能整体接住 ExceptionGroup；`except*` 把匹配的**子集**接走处理，剩下的继续向上抛。锁版本怎么看？→ 项目若明确 3.11（本仓库 pyc 是 cpython-311），`list[int]`/`|`/match/TaskGroup 可安心用。

## 八、本仓库实践注记

> 把上面的八股落回真实代码，在"追问/讲项目"环节当钩子。路径相对仓库根目录；阶段 5 部分是**契约规划（评审中）**，答时按"设计如此"讲。

29. **Q：为什么 run_cmd/sbatch 执行器能注入"假命令"做离机测试？（★ / 🗂）**
    **A：**
    - 这是**依赖注入留缝**的经典设计（呼应 Q27）：把"执行外部命令"这一副作用做成**可注入的函数参数**，测试传假实现即可离机全测。
    - [backend/services/slurm.py](backend/services/slurm.py) 里 `sbatch_submit/squeue_status/sacct_status/job_status/cancel` 都带末参 `run_cmd: Callable = _run`；默认 `_run` 调 `subprocess.run` 真执行，正常路径不受影响。
    - 测试传**假 run_cmd**：收命令列表、返回预置 stdout（如 `"Submitted batch job 123"`）→ 没 Slurm 的开发机也跑通 submit→status→cancel 全链路、断言解析逻辑。
    - 离机性还有更上一层：`SR_SLURM_FAKE=1` 换内存假调度器、`SR_SCENES_ROOT` 未设时 scene_search 自动回退 fake 场景——同一"可替换后端"思路。

    ```python
    def fake_run(cmd, timeout=30):
        return SimpleNamespace(returncode=0,
                               stdout="Submitted batch job 123\n", stderr="")
    assert slurm.sbatch_submit("s.sh", run_cmd=fake_run) == 123
    ```

    **追问：** 注入 vs monkeypatch 谁更优？→ 注入的缝在**函数签名里可见**：契约自明、测试传参、无全局状态、跨文件复用同一 fake；monkeypatch 快但"被测代码内部藏着依赖"的耦合高。规律：函数体内硬写 subprocess/open/网络又要可测 → 加一个可注入执行器参数。

30. **Q：为什么同步阻塞的 run_loop 要丢 to_thread、再经 asyncio.Queue 转 SSE？（★ / 🗂 规划）**
    **A：**
    - 背景：[backend/agent/loop.py](backend/agent/loop.py) 的 `run_loop` 是**同步阻塞**的 agent 循环（串行调 LLM/工具，一轮几秒~几十秒），直接当协程跑会**卡死事件循环**（Q16）。
    - 契约（[docs/planning/api-contract.md](docs/planning/api-contract.md) §4.1，阶段5 = 评审中、**尚未实现**）给的桥：`asyncio.to_thread(run_loop, cfg, …, on_event=bridge)` 让循环在**线程池**跑；内部每个产出点**同步回调** `on_event({"type": ...})`；回调经 `asyncio.Queue` 入队，主协程 `while await get()` **逐帧** yield 成 SSE —— 即 Q17+Q19+Q22 的合体。
    - 会话并发：进程内 `dict[session_id → asyncio.Lock]`，锁被占再 POST 同会话 → **409**（串行，防同会话并发回合）。
    - 兼容红线：`on_event` 默认 `None` → `run_loop` 行为与现状逐字节一致，既有 loop 测试不因加钩子变更——"异步外壳 + 不改核心状态机"的演进策略。

    **追问：** 分别解决哪些并发问题？→ ① 同步循环不冻事件循环（Q16/Q19）；② 事件按真实顺序逐条流式下发，前端边收边渲染；③ 同步侧零 async、核心零改动可测。坑？→ 回调**跨线程**入队 asyncio.Queue 不安全，要 `loop.call_soon_threadsafe` 包（Q17 追问）；客户端断连要能停线程池任务，否则泄漏。为什么用 Queue 不用直接回调写流？→ 同步线程不能碰事件循环侧的响应对象，队列是干净边界。

31. **Q：SQLite 连接为什么懒创建 + 每次写提交？（🗂）**
    **A：**
    - **懒创建**：[backend/services/store.py](backend/services/store.py) 的 `Store.__init__` 只记路径、**不碰磁盘**；真正首次读写才走 `_db()`：`sqlite3.connect` + `CREATE TABLE IF NOT EXISTS`。收益：构造 Store **零副作用、近乎零成本**——工具包装器想建就建（`default_store()` 很便宜），不会"new 一个就偷偷建库"；测试/只读路径不写就不产生文件。
    - **每次写提交**：每条写（`create_session/append_message/put_sr_task` 等）末尾都 `db.commit()`——**一次写 = 一个 autocommit 事务**，落盘成功才返回。
    - 与 agent 的 **checkpoint 策略**咬合（store 注释引 agent-orchestration-research §6.3）：**外部副作用前先落 checkpoint**——`run_sr` 对 sbatch 下手前先把"提交意图"写进 `sr_tasks`（job_id=None），sbatch 成功后再补 job_id；进程崩溃重放可从库找到 intent，去 squeue/sacct **对账**而非重复提交（幂等表/防盲重试）。

    **追问：** 懒连接复用单连接有并发问题吗？→ 进程内单线程/串行写、连接同一实例、写各自 commit 不交叠；将来多线程/多进程要 `check_same_thread=False` + 写锁或每次短连接。为什么 messages 每条都 commit 不攒批？→ commit 边界 = "**检查点已持久**"的承诺；攒批吞吐高但丢了"上一步 durable 再走下一步"的恢复语义。一句话：**外部副作用（sbatch/文件）之前，状态先落库**。
