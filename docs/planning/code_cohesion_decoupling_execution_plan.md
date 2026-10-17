# 代码高内聚低耦合优化执行计划 / 2026-10-17 / 状态：评审

> 目标：在保持现有 HTTP、SSE、工具返回值和 SR 本机执行行为不变的前提下，收紧平台边界，为可信源码插件和二次开发提供稳定入口。
> 依据：[代码高内聚低耦合改善调研方案](code_cohesion_decoupling_research_plan.md)。
> 执行方式：按工作包逐项完成；每个工作包完成代码、测试和文档后，才进入下一个工作包。

## 当前实施状态

- 工具注册与调用统一：已落地，Agent、REST 使用独立 `ToolRegistry`。
- 显式应用上下文：已落地，运行时依赖集中在 `AppContext`。
- 查看器只读上下文：已落地，包含版本化快照、后端校验接口和前端投影函数。
- 前端扩展入口：已落地，包含扩展注册表、动态路由、导航和影像质量示范页面。
- 后端插件清单：已落地，支持静态清单发现、重复 ID 检查和错误收集。
- Store 拆分、通用长任务抽象和在线插件安装：按触发条件保留在延后范围。

## 1. 当前代码边界与主要耦合证据

| 区域 | 文件与位置 | 当前职责 | 耦合风险 | 处理顺序 |
|---|---|---|---|---|
| 工具契约 | `backend/tools/contract.py:22-77` | 工具描述、全局注册表、装饰器、清单生成 | `_REGISTRY` 是模块级可变状态，调用方只能依赖导入副作用 | 最高 |
| 内置工具加载 | `backend/tools/__init__.py:10-14` | 导入四个工具模块触发注册 | 注册顺序和可用工具由 import side effect 决定 | 最高 |
| Agent 工具调用 | `backend/agent/loop.py:118-131,254,321-336` | 查找工具、解析参数、执行、异常封装、生成模型清单 | Agent 自己实现一套调用逻辑 | 最高 |
| REST 工具调用 | `backend/api/platform.py:249-270` | 查找工具、解析 JSON、执行、HTTP 异常映射 | REST 再实现一套调用逻辑，和 Agent 结果口径可能分叉 | 最高 |
| 应用运行时 | `backend/api/app.py:1022-1063` | 创建 FastAPI、Store、LLM 配置、锁、订阅者、任务缓存、轮询 | 路由通过 `request.app.state` 读取散落字段；测试需要完整组装运行时 | 高 |
| 配置边界 | `backend/config.py:97-205` | LLM `Config` 与 SR `SrRuntime` 两个快照 | 两组配置已经有清晰边界，应保持，不把 SR 字段塞进 Agent 配置 | 保持 |
| 持久化 | `backend/services/store.py:115-360` | 会话、消息、SR 任务、状态时间戳、预览状态 | 一个 Store 承担多个领域，后续插件容易直接依赖内部表结构 | 中 |
| 前端 API | `frontend/src/lib/api.ts:1-914` | 类型、URL、fetch、SSE、预览缓存 | 传输层、领域类型和缓存生命周期集中在一个文件 | 中 |
| 查看器状态 | `frontend/src/stores/viewer.ts:301-2511` | 文件源、解码、视图、ROI、掩码、场景、队列跳转 | 单 store 包含多个领域行为，扩展功能容易直接依赖私有状态 | 中 |
| 前端外壳 | `frontend/src/router/index.ts:11-47`、`frontend/src/App.vue:1-57` | 路由、导航、常驻队列订阅 | 插件页面需要修改核心路由和导航 | 后续 |

当前优先级按依赖关系确定为：

1. 统一工具注册与调用边界。
2. 建立显式应用上下文，并保留兼容访问路径。
3. 定义只读的查看器上下文契约。
4. 在稳定契约上开放前端扩展槽和可信示范插件。
5. 在出现第二个长任务或第二个持久化消费者后，再拆分通用任务和 Store。

## 2. 工作包一：统一工具注册、查找和执行

### 目标

Agent、REST 和后续插件都经过同一个注册表和执行入口。工具调用结果继续保持：

```json
{"ok": true, "data": {}, "error": null}
```

或：

```json
{"ok": false, "data": null, "error": "..."}
```

现有 `@tool` 使用方式、工具名称和四个内置工具的参数不变。

### 文件级动作

1. `backend/tools/contract.py`
   - 保留 `Tool`、`ok()`、`err()` 和 `@tool` 的兼容导出。
   - 增加 `ToolRegistry`，至少提供 `register()`、`get()`、`list()`、`manifest()`、`invoke()`。
   - `register()` 对重复工具名抛出明确异常，不允许静默覆盖。
   - `invoke()` 统一完成：工具查找、参数必须是 JSON 对象、调用异常转为 `ok=False`、结果结构校验。
   - 工具元数据增加可选字段：`version`、`visibility`、`timeout_sec`、`permissions`；已有工具使用安全默认值。
   - 继续提供默认注册表兼容函数，使现有测试和外部调用可以逐步迁移。

2. `backend/tools/__init__.py`
   - 增加 `build_builtin_registry()` 或等价工厂，集中加载四个内置工具。
   - 工厂返回独立注册表；测试和应用可以创建多个互不污染的注册表。
   - 保留现有导出名称，避免一次性修改全部调用方。

3. `backend/tools/run_sr.py`、`search_scenes.py`、`sr_job_status.py`、`fix_bad_lines.py`
   - 让工具定义可以注册到传入的 `ToolRegistry`。
   - 工具实现继续调用 `backend/services`，不把 HTTP、SSE 或 Agent 逻辑放入工具模块。
   - 工具模块不读取 `app.state`；需要运行时依赖时通过执行上下文传入。

4. `backend/agent/loop.py`
   - `run_loop()` 增加可选的 `registry` 关键字参数，默认使用兼容默认注册表。
   - 删除本地的工具遍历和执行分支，`call_tool()` 改成注册表适配函数，保留函数名供现有测试替换。
   - 模型工具清单从同一个 `registry.manifest()` 获取。
   - 工具执行上下文保留 `source="agent"`，为权限和日志扩展预留位置。

5. `backend/api/platform.py`
   - 工具清单接口从应用运行时注册表读取。
   - `/api/tools/{name}` 使用同一个 `registry.invoke()`，只负责 JSON 请求体校验和 HTTP 状态码映射。
   - 删除本模块对 `list_tools()` 和 `tool.run()` 的直接依赖。
   - 保持未知工具 404、非法 JSON 400、工具参数/执行失败 400 的现有 API 口径。

6. `backend/api/app.py`
   - 在应用创建时构造内置注册表并放入运行时上下文；过渡期可提供 `app.state.tool_registry` 别名。
   - 应用启动失败时报告重复工具名或工具加载错误，错误信息包含工具 ID。

7. 测试文件
   - 新增 `backend/tests/test_tools_registry.py`：独立注册表、重复注册、未知工具、非对象参数、异常封装、清单元数据。
   - 修改 `backend/tests/test_agent_loop.py`：验证 Agent 使用注入的注册表；保留现有 `call_tool` 兼容替身测试。
   - 修改 `backend/tests/test_api_platform.py`：验证 REST 与 Agent 对同一个工具得到相同业务结果。
   - 修改 `backend/tests/test_api.py`：验证每个 `create_app()` 使用独立注册表，不受前一个测试注册内容影响。

### 工作包一验收

- `backend/agent/loop.py` 和 `backend/api/platform.py` 不再各自实现工具查找与执行。
- 同名工具注册导致应用启动或注册工厂构建失败，错误可读。
- 现有 `/api/tools`、`/api/tools/{name}`、聊天工具调用和四个内置工具的外部行为不变。
- 后端测试全绿。

## 3. 工作包二：显式应用上下文

### 目标

把 Store、LLM 配置、工具注册表、场景根和队列运行时参数集中为一个小型只读依赖对象。上下文只持有已构造的依赖，不承载业务决策，也不把所有服务方法堆进一个类。

### 文件级动作

1. 新增 `backend/runtime_context.py`
   - 定义 `AppContext` 数据类，字段至少包含：`config`、`store`、`tool_registry`、`scenes_root`、`poll_sec`。
   - 提供类型明确的构造函数，不在模块导入时读取环境或创建数据库连接。
   - 上下文对象不暴露原始 `app`，服务仍通过显式参数接收所需依赖。

2. `backend/api/app.py`
   - `create_app()` 只负责读取配置、创建 Store/注册表、组装 `AppContext` 和启动任务。
   - 将上下文挂到 `app.state.context`；兼容期保留 `store`、`cfg`、`scenes_root` 等别名，避免大范围同步修改。
   - lifespan 只管理轮询和预览任务的启动、取消、等待，不新增业务逻辑。

3. `backend/api/platform.py`
   - 增加 `_context(request)` 小 helper，路由统一从 `request.app.state.context` 取依赖。
   - 聊天调用把 `context.config`、`context.store`、`context.tool_registry` 显式传给 Agent。
   - 队列接口继续把队列状态、订阅者和聊天锁作为运行时状态管理；这些对象不下沉到服务模块。

4. `backend/agent/loop.py`
   - `run_loop()` 增加可选 `context` 参数；传入时从上下文取得 config、store、registry。
   - 保留现有显式 `cfg`、`store` 参数作为兼容入口，并规定同一调用不能同时传冲突依赖。
   - Agent 不读取环境变量、不导入 FastAPI、不访问 `app.state`。

5. `backend/tests/test_runtime_context.py`（新增）
   - 使用临时 SQLite 和临时场景根构造上下文。
   - 验证两个应用实例的 Store、注册表和锁状态互不共享。
   - 验证 Agent 可以仅凭注入上下文运行 mock 回合。

6. `backend/tests/test_api.py`、`backend/tests/test_api_platform.py`、`backend/tests/test_agent_loop.py`
   - 测试夹具优先构造 `AppContext`，兼容别名只用于覆盖旧路径。
   - 不用修改全局环境来切换每个用例的 Store 或工具集合。

### 工作包二验收

- 路由层不再需要知道 Store、Config 和注册表的构造细节。
- Agent 模块可在无 FastAPI、无 `app.state` 的单元测试中运行。
- 现有 API、SSE 和队列校准行为不变。

## 4. 工作包三：只读查看器上下文契约

### 目标

定义插件可读取的稳定输入。插件只得到快照，写入动作通过宿主服务完成；插件不导入 Vue store、组件实例或 canvas 对象。

### 文件级动作

1. 新增 `backend/contracts/viewer_context.py`
   - 定义版本化 JSON 结构 `ViewerContextSnapshot`：`schema_version`、`scene`、`source`、`image`、`roi`、`coordinates`、`capabilities`。
   - 明确原图坐标、预览坐标、地理坐标的字段名和单位；缺失值使用 `null`。
   - 提供字段校验和只读序列化函数，不读取浏览器状态。

2. `backend/api/platform.py`
   - 增加只读上下文校验/回显接口（具体路径在 API 契约中固定，例如 `POST /api/context/validate`）。
   - 接口只校验结构、坐标范围和路径标识，不接受插件直接写文件。

3. `frontend/src/lib/agentContext.ts`
   - 将现有 `ViewerContextSnap` 对齐为版本化结构。
   - 保留给聊天文本的摘要函数 `buildContextNote()`，同时提供机器可读快照构造函数。
   - 不把 Pinia store、canvas、DOM 引用放入快照。

4. `frontend/src/lib/api.ts`
   - 增加 `ViewerContextSnapshot` 类型和对应请求封装。
   - 将上下文 API 类型与通用 fetch、SSE、预览缓存实现分离到 `frontend/src/lib/contextApi.ts`；`api.ts` 保留兼容导出。

5. `frontend/src/stores/viewer.ts`
   - 增加一个纯投影函数，把当前查看器状态投影成 `ViewerContextSnapshot`。
   - 插件和 Agent 只调用该投影函数，不读取 store 内部 refs 或 canvas。

6. 测试文件
   - 新增 `backend/tests/test_viewer_context.py`。
   - 新增 `frontend/src/lib/__tests__/viewerContext.test.ts`。
   - 覆盖空 ROI、多 ROI、坐标越界、无场景文件、预览坐标到原图坐标的字段完整性。

### 工作包三验收

- 示例插件可以只依赖快照类型和宿主 API。
- 禁用或移除插件不会影响查看器、Agent、队列和场景库。
- 任何插件输入路径都不能绕过宿主路径校验。

## 5. 工作包四：前端扩展槽与可信示范插件

### 目标

先支持随源码部署、重启生效的可信插件。插件清单包含 `id`、`name`、`version`、`host_api_version`、后端入口、前端入口和启用状态；不实现在线安装、远程 JavaScript 或运行时沙箱。

### 文件级动作

1. 新增 `frontend/src/extensions/types.ts`
   - 定义页面路由、查看器侧栏页签、当前影像操作按钮三类扩展槽。
   - props 只使用 `ViewerContextSnapshot`、只读配置和宿主回调。

2. 新增 `frontend/src/extensions/registry.ts`
   - 实现内置扩展清单加载、ID 唯一性校验、启用状态过滤和版本范围校验。
   - 注册表实例化，不把扩展写进 Vue 全局对象。

3. `frontend/src/router/index.ts`
   - 路由由内置路由与已启用扩展路由合并生成。
   - 内置四个页面保持固定；扩展路由冲突时拒绝加载并记录可读错误。

4. `frontend/src/App.vue`
   - 顶部导航由扩展注册表投影生成。
   - 常驻队列订阅继续由 App 外壳管理，不交给插件。

5. 新增 `backend/plugins/manifest.py`、`backend/plugins/loader.py`
   - 读取可信源码目录下的静态清单。
   - 校验宿主 API 版本、插件 ID、入口和权限。
   - 插件加载异常只影响该插件，并向健康检查/日志提供明确状态。

6. 新增示范插件目录 `backend/plugins/image_quality/` 和 `frontend/src/extensions/imageQuality/`
   - 后端注册一个 JSON Schema 工具，读取快照并返回指标或报告。
   - 前端提供一个查看器侧栏页签。
   - 默认不修改原始影像；需要写入时只能调用宿主服务。

7. 测试文件
   - 新增 `backend/tests/test_plugin_loader.py`。
   - 新增 `frontend/src/extensions/__tests__/registry.test.ts`。
   - 增加禁用插件的 API、Agent、队列和查看器回归测试。

### 工作包四验收

- 新增示范插件不需要修改 Agent 状态机和 SR 服务。
- 禁用示范插件后核心页面和 API 正常。
- 缺入口、版本不兼容、重复 ID 和越权路径均有明确错误。

## 6. 延后工作：由第二个真实场景触发

以下项目暂不进入前面四个工作包：

1. `backend/services/store.py` 的会话、消息、SR 任务拆分。触发条件是第二个持久化消费者出现；实施时新增 `session_repository.py`、`task_repository.py`，保留 `Store` 外观作为兼容门面。
2. `backend/services/run_sr.py` 与通用任务服务的抽象。触发条件是第二个长任务需要同一生命周期；SR 字段继续留在 SR 适配器内。
3. `frontend/src/stores/viewer.ts` 的全面拆分。触发条件是第二个查看器型业务接入；优先抽取纯函数和只读投影，不直接搬运 refs。
4. 在线安装、卸载、升级、远程插件包和插件沙箱。它们需要权限模型、签名和回滚设计，不能由当前可信源码插件接口直接推导。

## 7. 执行顺序与每项交付格式

执行顺序固定为：

1. 工作包一：工具注册与调用边界。
2. 工作包二：应用上下文。
3. 工作包三：查看器上下文。
4. 工作包四：前端扩展槽和示范插件。

每个工作包完成时，在对应 PR 或交付记录中提供：

```text
范围：工作包编号与文件列表
发现：代码证据与行号
决定：保留、抽取或延后
修改：接口和兼容行为
验证：执行命令、通过项、失败项
风险：未验证内容与下一步
```

## 8. 基线和验收命令

后端：

```powershell
python -m pytest backend/
```

前端：

```powershell
cd frontend
npm run typecheck
npm run test -- --run
npm run build
```

静态边界检查：

```powershell
rg -n "_REGISTRY|list_tools\(|\.run\(\*\*|app\.state|from backend\.api|from backend\.services" backend/agent backend/api backend/tools
```

完成标准：后端测试、前端类型检查、前端测试和构建全部通过；工具清单与调用结果结构一致；外部 API 不出现未记录的字段或状态变化；插件异常不会阻止核心应用启动。
