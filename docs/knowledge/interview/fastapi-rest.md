# FastAPI + REST/SSE 面试八股（interview · 高频精简版）

> 定位：跳槽高频 FastAPI 与后端接口设计自测与背诵。★=高频。每题给「答（要点）+ 追问」。
> 仓库真例标注 🗂：答完八股，能用 [backend/api/app.py](backend/api/app.py) 等真实文件讲成"项目实践"——这比只会背理论多一层区分度。阶段 5（聊天 SSE/队列）处于**规划/评审中**，引用处均标注，不当作已实现事实。

---

## A. FastAPI 框架本身

1. **Q：FastAPI 为什么能火？它底层是 Starlette + Pydantic，各解决什么？（★）**
   **A：**
   - Starlette 是 ASGI Web 框架，处理路由/中间件/请求响应生命周期，**支持异步**；
   - Pydantic 负责**数据校验与序列化**，用类型注解在"进/出"两端自动生成校验；
   - 两者一拼 = 自动请求校验 + 自动响应模型 + 自动生成 OpenAPI 文档。
   **追问：** ASGI 和 WSGI 区别？→ WSGI 是同步（一个请求一个 worker 进程/线程）；ASGI 是异步事件驱动，一个进程能同时挂很多连接（SSE/WebSocket 才现实）。

2. **Q：一个 `@app.get("/api/scenes")` 背后发生了什么？（★）**
   **A：**
   - 定义路径操作：方法 + 路径 → 路由表；FastAPI 自动做路径参数类型转换与校验；
   - 声明了 `Query(default=None)` 的查询参数，FastAPI 解析 query string、按类型转换、非法返回 422；
   - 函数返回 dict → 自动 `JSONResponse` 序列化。
   **🗂 仓库真例：** `list_scenes(query="", satellite=None, sensor=None, date_from=None, limit: int = Query(default=20, ge=1, le=500))` ——`dateFrom` 用 `alias` 映射到前端 camelCase，`ge/le` 把"limit 越界"变成自动 422。

3. **Q：Pydantic 模型有什么好处？不写模型（直接收 dict）行不行？（★）**
   **A：**
   - 声明式请求体校验：字段类型/必填/取值范围错误 → 自动 422 + 错误明细；
   - 输出模型（`response_model`）保证响应形状稳定、不把内部字段漏出去；
   - 取舍：小项目/薄壳端点常直接收 dict 减少样板——**校验要与风险成正比**，收用户自由输入就该上模型，内部服务间调用可以薄。
   **追问：** Pydantic v2 核心是 Rust 的 `pydantic-core`，校验性能远高于 v1 手写逻辑；`model_dump()` 取代 `dict()`。

4. **Q：FastAPI 为什么能自动出 /docs？**
   **A：** 函数签名 + 类型注解 + 文档字符串 → 生成 OpenAPI Schema → 内置 Swagger UI(/docs) 与 ReDoc(/redoc)。你的"接口契约"可自动产出一份活文档。

5. **Q：`async def` 还是普通 `def`？差别是什么？（★）**
   **A：**
   - **普通 `def`**：FastAPI 把调用丢进**线程池**（不会卡事件循环）——适合内部是同步阻塞库的函数；
   - **`async def`**：直接在事件循环里跑——适合 IO 密集且本身 async 的函数（如 async 请求、读流）；
   - 常见坑：async 函数里**调用同步阻塞库**（requests、同步 OpenAI client、`sleep`）会卡住整个事件循环。
   **追问：** 你这项目里既有同步工具又有 SSE，怎么处理？→ 同步阻塞的 `run_loop` 用 `asyncio.to_thread` 丢线程池，事件经 `asyncio.Queue` 桥回主协程再逐帧下发（🗂 规划，见契约 [api-contract.md](docs/planning/api-contract.md) §4.1）。

6. **Q：依赖注入 `Depends` 用来干嘛？你项目用不用？（★）**
   **A：**
   - 把"端点都要做的准备/校验/资源"抽成可复用依赖（鉴权、DB 会话、配置、分页参数），还支持作用域（每次请求一个实例）；
   - 便于测试：替换依赖 = 换行为。
   - 🗂 本仓库是"薄壳 + 工厂 + env"风格，多数端点直接调 services，暂未大量用 Depends——**答"我们因为薄、用 env 工厂注入；等要鉴权/审计会引入 Depends"即可**，别假装没用过。

7. **Q：中间件 middleware 在 FastAPI 里怎么理解？CORS 为什么要中间件？（★）**
   **A：**
   - 中间件包在每个请求/响应的外层：进来先过、出去再过（洋葱模型）——适合 CORS、日志、统一请求 id、限流；
   - 浏览器同源策略：跨源 fetch 要先看响应头 `Access-Control-Allow-Origin` 等；内网直连 IP:端口部署时前端与 API 同源反代，或直接 CORS 全放。
   **🗂 仓库真例：** app 上加了 `CORSMiddleware(allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])`——内网且数据非敏感；**防御只靠路径白名单**，不靠 CORS（CORS 不是安全边界，是浏览器访问策略）。

## B. 响应/错误/返回类型

8. **Q：JSONResponse / FileResponse / StreamingResponse 各何时用？（★）**
   **A：**
   - `JSONResponse`：普通 JSON（FastAPI 默认替你包）；
   - `FileResponse`：直接把一个文件当响应体——🖼 阶段 4 `/preview` 返回盘阵烤好的 JPG 就用它（带 `media_type="image/jpeg"`，顺带让 nginx 静态托管同路径 JPG 时也能原生缓存）；
   - `StreamingResponse`：响应体是流（逐块 yield）——SSE、大文件代理、流式下载都靠它；必须手动设 `Content-Type`。

9. **Q：SSE 在 FastAPI 里怎么落地？帧格式长什么样？（★）**
   **A：**
   - 返回 `StreamingResponse(generator(), media_type="text/event-stream")`，generator 里 `yield "data: {json}\n\n"` 逐帧吐；
   - 配合响应头 `Cache-Control: no-cache`、`X-Accel-Buffering: no`（提醒 nginx 别缓冲）；
   - 每帧是一行 `data:{json}\n\n`；前端 fetch 读 `response.body` 手工按空行切帧（EventSource 只 GET，POST 即流用不了它）。
   **🗂 规划契约示例帧：** `{"type":"tool_call","name":"run_sr","args":{...}}`、`{"type":"tool_result",...}`、`{"type":"turn_done","content":"..."}`，预留 `{"type":"token","delta":"..."}`。

10. **Q：HTTPException 与"业务失败返回 200 + {ok:false}"两种风格，怎么取舍？（★）**
    **A：**
    - `HTTPException(status, detail)`：让 HTTP 语义表达"资源不存在/没权限/参数错"，配合前端按码处理；
    - 业务层 {ok,data,error}：**调用本身成功到达了、但结果是个失败**（如工具执行失败），用 200 携带 ok=false——因为错误要被上层（Agent/前端）**当普通数据消费**，不是断连。
    **🗂 仓库真例：** 工具契约统一 `{ok,data,error}`（[backend/tools/contract.py](backend/tools/contract.py)），所以直调工具端点 HTTP 恒 200；而"资源不存在/越权"这类传输层错误用 4xx。**分清"业务失败"和"传输/资源失败"是 REST 设计的核心。**

11. **Q：错误码在本仓库怎么分？（★）**
    **A：** 400 参数非法（缺字段/路径非绝对/含 `<fake>`）· 404 资源/会话/场景不存在 · 409 会话正忙或"上次提交被中断勿盲重试" · 422 生成/提交失败（预览失败/sbatch 失败/slurm 不可用）。统一 `{"detail": "<人话>"}`。

## C. 校验与安全（能看出你会不会被人打）

12. **Q：为什么对外暴露"文件/命令"能力的第一件事是路径白名单？（★）**
    **A：** 端点能读盘阵文件、能 sbatch 提交作业 = 面向外部的"武器"；不拦 `../` 穿越、白名单外绝对路径、伪文件路径，等于把机器交给调用者。
    **🗂 仓库真例：** [backend/api/paths.py](backend/api/paths.py)：返回/生成路径必须 `realpath` 后落在场景根内；场景 id 用 base64url(相对路径) 做成**不透明 id**，前端不知盘阵绝对路径；fake 占位 `<fake>` 永不允许当真去生成预览。run_sr 侧同样拒 `<fake>` 与非绝对路径。

13. **Q：为什么 run_sr 这种"提交外部作业"的接口必须幂等？（★）**
    **A：** 网络/进程随时中断，用户/Agent 可能重试。若每次 POST 都真提交，一次崩溃重放 = **Slurm 上多一个重复作业、白烧 GPU**。所以要用稳定标识去重。
    **🗂 仓库真例：** `task_fingerprint(params)` = 参数 sha256；`submit_run_sr` **先查 `sr_tasks` 表**：已有 job_id 且还 active/COMPLETED → 直接复用返回 RESUMED_*（不重复提交）；FAILED/UNKNOWN 才重跑；**上次提交被中断（有意图无 job_id）→ 明确报"勿盲重试"**，绝不盲目补提交（[backend/services/run_sr.py](backend/services/run_sr.py)）。这就是"先记意图（checkpoint）→ 副作用 → 记结果"的持久化顺序（详见 [db-storage.md](docs/knowledge/interview/db-storage.md)）。

14. **Q：表单/JSON 参数里有目录字段时，校验"是绝对路径、无假标记"有什么用？**
    **A：** run_sr 的目标机是内网盘阵，Slurm 作业只认盘阵绝对路径；假数据（开发机 fake 场景）路径必须被挡在 Slurm 门外，否则开发机上跑通的假流程会把占位路径当真提交。

## D. 部署与进程模型

15. **Q：uvicorn 启动有哪些姿势？`--factory` 是什么？（★）**
    **A：** `uvicorn app:app`（模块里有个 app 实例）或 `uvicorn app:create_app --factory`（模块里是个返回 app 的工厂函数）；工厂的好处：**每次调用都重新读 env 构造** → 测试里设 env 后现造 app，互不污染。
    **🗂 仓库真例：** [backend/api/app.py](backend/api/app.py) 顶部 `root = paths.scenes_root()` 在 `create_app()` 里读 env；systemd 走 `--factory`。

16. **Q：SSE + 多 worker 有什么坑？为什么要单 worker？（★）**
    **A：** 进程内广播（一组 asyncio.Queue 存"在线的 SSE 连接"）**只在单进程内有效**；多 worker = 多个进程，job 状态变化发生在 worker A，订阅者挂在 worker B 就收不到 → 需要外部队列（Redis pub/sub）做跨进程广播。
    **🗂 仓库取舍：** 阶段 5 规划"uvicorn 单 worker + 进程内广播"，多 worker 留到有真实并发需求再接外部队列——**能力边界写清楚本身就是加分项**。

17. **Q：为什么要"先写契约文档再写代码"？（★）**
    **A：** REST/SSE 是**前后端共享的接口面**：前端渲染和后端推送各按同一 schema 写 parser/emitter。契约文档先落盘 = 先把字段/事件/错误码/状态机说死，避免"后端顺手加个字段、前端就崩"。改契约先改文档再改代码，两端各留单测锁字段。

18. **Q：FastAPI 项目你一般怎么组织目录？（★）**
    **A：** 按"薄壳分层"：`api/`（端点，薄）→ `services/`（流程编排，可注入假依赖）→ 更底层（算法/DB/外部客户端）。端点不该有业务逻辑，service 不该是 HTTP 的形状。
    **🗂 仓库布局：** `api/app.py + paths.py` → `services/{run_sr,slurm,store,mask,scene_search,preview_jpg}.py` → `tools/`（Agent 的薄壳）→ `mta_grid/`（纯算法）。同一份 services 同时被 REST、CLI、Agent 复用。

19. **Q：env 配置怎么做才离线可测？（★）**
    **A：** 所有可变配置从环境变量读，默认值给"开发/假"侧：没设 `SR_SCENES_ROOT` → fake 场景；`SR_LLM_MOCK=1` → 假 LLM；`SR_SLURM_FAKE=1` → 假调度器。**同一份代码 dev(假) 与真机(真) 之间零改动**，只换 env。这是"真实系统进不来就造显式标记的替身"方法论（详见 [platform-tutorial.md](docs/knowledge/platform-tutorial.md) 第 2 章）。

20. **Q：你踩过哪些 FastAPI/异步接口的坑？挑一个讲过程。（★）**
    **A（示范，可用你自己的版本替换）：**
    - 坑：同步阻塞函数卡事件循环——表现为"一个慢请求拖死所有 SSE 连接"；
    - 过程：发现 chat 回合要几十秒，且期间同进程其它 SSE 全不推 → 查是 `run_loop` 同步阻塞跑在事件循环里 → 改用 `asyncio.to_thread` 丢线程池 + `asyncio.Queue` 把事件桥回主协程逐帧下发；
    - 结果：回合期间事件持续推送、互不阻塞；并发同会话再用锁串行（409"会话正忙"）。
    **追问（讲完后自己问自己）：** nginx 反代下 SSE 为何要 `proxy_buffering off` 且 `proxy_read_timeout` 调大？→ 默认 nginx 会攒缓冲、还按空闲掐连接，会吃掉逐帧/长连接。

## 一句话备忘
> **FastAPI = Starlette(异步 HTTP) + Pydantic(校验)；async def 别塞同步阻塞；SSE 用 StreamingResponse 逐帧吐 + nginx 关缓冲；业务失败用 {ok,data,error}、资源/传输失败用 4xx；对外部副作用先做白名单 + 幂等；改接口先改契约文档。**
