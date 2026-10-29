# P1 空间批注实现与验收记录

日期：2026-10-29。状态：基础交互和受验证结果通道已实现，尚未达到需求全文的最终验收标准。

批注可见性入口：`FileListTools.vue` 既有白色悬浮胶囊末端新增细分隔线与 32px 双态眼睛按钮，复用提供的 eye/eye-closed SVG（public/icon 同字节发布）。Viewer 的 `agentAnnotationsVisible` 默认为 true，是唯一可见性事实源；Annotation Store 的 hidden 为兼容既有入口的可写 computed。按钮和右侧管理器同步，切换时同步清除 selected/hovered；重新开启仅恢复编号，不恢复旧区域或气泡。显式点击消息引用仍可开启并聚焦目标。图标 16px、颜色过渡 150ms，支持 title、aria-pressed、焦点及 Enter/Space。可见性变更不写像素、ROI、掩码或视图。TypeScript、31 项专项测试、Build 通过；Chrome 验证关闭全部覆盖层、键盘重新开启仅编号、图标/aria 状态，以及切换前后 ROI/pane view 完全一致。

矩形闭合动画：选中 bbox 使用 SVG Path + pathLength=1、stroke-dasharray/stroke-dashoffset，1.75px 描边在 450ms 内顺时针闭合，结束保持静态描边和轻填充。`annotationPath.ts` 按投影后的锚点距离选择最近角点（相同距离优先左上），只调整绘制路径起点，不修改源坐标；锚点距起点超过 4px 时可返回轻量连接线。现有 bbox 锚点本来就在左上角。Overlay 全局维护一个 displayed region；切换先淡出 90ms，再挂载最新目标，连续切换会取消旧定时器；关闭/隐藏/绘制模式/卸载清除等待，缩放平移只更新 path d，不重启动画。减少动效时直接展示完整区域，不延迟切换。

本轮 TypeScript、路径+坐标 16 项单测、Build 通过；Chrome 以实际 CSS 动画验证 450ms 及 0/225/450ms 三个时点的 dashoffset=1/0.5/0、顺时针闭合路径、切换最多一个区域，并通过既有双屏、ROI、滚轮、历史恢复回归及正式 API/Loop/SSE Mock 链路。数据契约、业务状态及后端未修改。

视觉层级优化：默认仅编号锚点，所有 bbox/polygon/填充仅在选中 ID + pane 时渲染；悬停及键盘聚焦通过纯 UI hovered 状态联动锚点、消息引用和管理条目，不展开区域。新增气泡关闭键；点击空白、Esc 或关闭后恢复默认。保留 36px 命中区和 26px 可见圆点，160ms 色彩/透明度过渡，无离场滞留或循环动画。质量问题使用 #B65F55，普通地物保持青绿，区域与标签同步颜色。`annotationVisual.ts` 仅计算 CSS 变量，优先识别已有 category=quality/image_quality，缺省依据标题明确的质量问题词（条带/伪影/噪声等）作展示分类，不修改 Grounding、坐标、核实状态或数据库。现有协议未新增必填类别字段。

此轮 TypeScript、517 项 Vitest、Build 通过；Chrome 交互测试新增默认无区域、hover 不展开、单点展开、关闭恢复、质量颜色检查，保留缩放/平移/双屏/ROI/历史关联回归；正式分析按钮经 API/Loop/工具/SSE/SQLite 到 Viewer 的 Mock 回归通过。后端业务代码未因本轮视觉调整而修改。

坐标越界修复：BBox/Polygon 表示几何边界，允许右/下边界等于 width/height；Point 为像素位置，仍要求严格小于 width/height。前后端验证一致。空间工具默认 input_pixel，另支持显式 normalized_01、normalized_1000，归一化端点映射到附件像素中心 [0,width-1]/[0,height-1]，再使用冻结 crop 映射源图；不猜测模型坐标制式。工具描述包含每个 input_ref 的实际附件尺寸，错误包含批注序号、输入引用、坐标空间、尺寸和收到的坐标。拒绝真正越界及不支持的空间，不截断坐标。不同坐标空间参与 Annotation ID，避免相同数值在不同空间产生 ID 冲突。本轮后端空间+Viewer Context 30 通过，前端几何 14 通过，TypeScript/Build 通过。没有该次模型原始工具参数，不能断言线上错误的唯一原因。

## 真实触发链收口（2026-10-29，覆盖下文原有开关说明）

根因位于工具注册边界：`viewer_context.bind_registry` 原要求环境变量必须精确等于 `1`，未配置时有效附件也不会使 `present_spatial_annotations` 进入模型工具清单。Loop 的工具白名单、API 附件映射注入、Tool Result 的完整落库和 SSE，以及 Vue 的引用/覆盖层通道本来已接通。快捷入口原提示偏向地貌描述；Policy/工具描述缺少“像素批注无需地理参考”的明确说明。没有本机内网请求日志，不能断言服务器当时的环境变量值或模型实际工具选择。

修复：有效 visual_inputs 存在时默认注册只展示待核实候选的空间工具；`SR_SPATIAL_GROUNDING=0/false/off/no` 仍可显式禁用，无附件仍不注册。input_ref schema 枚举本轮输入，几何 type 和坐标元素具有明确类型。Policy、工具和快捷问题共同说明附件像素/源像素不需要经纬度，地理坐标才需要 GeoReference；没有视觉定位依据时只返回文字，禁止凭模糊方位造坐标。工具成功始终不代表坐标准确或已核实。

新增正式通路测试：`.e2e/spatial_mock_server.py` 只在测试进程替换模型函数，使用临时数据库及附件目录，通过真实 FastAPI、上传 JPEG、冻结映射、Agent Loop、Tool Registry、校验、SQLite 和 SSE 返回 Point/BBox/Polygon。`.e2e/test-spatial-trigger.js` 点击实际分析按钮，断言 Marker、两种轮廓、Popover、引用及历史恢复；额外重复投递工具事件检查无重复、随后仅文字回复检查无虚构锚点。测试不向生产模型或生产数据库注入 Mock。

本轮 TypeScript、完整 Vitest、Build 和空间/Viewer Context/SSE Ownership/request budget pytest 已执行；Goal Runtime 单独执行 30 通过、1 失败，仍是原记录的 0.25 秒时间片测试，未修改 Deadline 掩盖它。真实内网 VLM 的 Tool Calling 兼容性与视觉坐标准确性仍待验收。纯文本/不具备定位能力的部署可显式关闭空间工具；仅看见图片不保证 Grounding 正确。

## 审查后修复（2026-10-29，以下覆盖原记录中对应的旧行为）

图文分析超时补充：普通 Chat 请求只要实际 wire 包含 image_url，模型请求预算使用 `max(SR_LLM_TIMEOUT, SR_LLM_VISION_TIMEOUT)`；新参数 `SR_LLM_VISION_TIMEOUT` 默认 180 秒，文本原默认 60 秒不变。预算仍覆盖等待、视觉 prefill 和完整输出，仍可取消且不自动重试。具有 Deadline 的 Goal 请求不增加时间片。超时报错及轨迹记录增加实际 `model_timeout_seconds`。这是等待策略修复，不能证明内网模型一定在该时限内完成；部署后需重启 API 服务使新配置生效。相关 async transport、request budget 和空间专项实际执行 55 项通过。

- 修复内网 HTTP 缺少 `crypto.randomUUID` 导致的载图异常和截图失败。显示 ID 使用 HTTP 可用的 `getRandomValues`，无 Crypto 环境另有兼容分支；可选空间元数据独立处理异常，不再使已成功生成的 JPEG 附件失败。相机入口也不再将所有错误描述为图像有问题。
- 同图双屏改为逐 pane 展示和独立投影。选中状态增加 pane side，只在选中格展开一张解释卡。消息引用优先定位到已显示该图的活动格，否则使用第一匹配格。
- marker 的 wheel 事件经组件转发到 `TifCanvas.onWheel`，使用相同 Canvas rect 和原缩放路径。Popover 的滚轮保留自身内容滚动。ROI 绘制时隐藏批注交互层，不改变掩码数据。
- 新增只读 `GET /api/scenes/{scene_id}/spatial-identity`：复用场景路径白名单解析及预览对应的源栅格选择，返回路径资源哈希 + 文件 stat 修订哈希（dev/ino/size/mtime_ns/ctime_ns）。不解码、不扫描目录、不读取整份 TIF、不授予生产权限。它是文件系统修订身份，不是完整内容校验和。
- 盘阵影像使用服务端版本身份；请求通过像素 token 和 sceneId 检查迟到响应。服务不可用时保留会话临时身份。用户明确确认的历史关联按 API 环境、会话及源版本保存在本机 localStorage，刷新后可恢复；不跨浏览器同步。本地文件无法可靠确认仍需人工临时关联，不按同名、尺寸或不完整采样自动认图。资源版本不同不会继承旧批注。
- 就绪提示改成 `CurrentImagePill.vue` 白色胶囊按钮：上下两行紧凑信息、文件名省略号、相机图标和分析入口；保留完整文件名 title，不再出现裸文本撑破侧栏。
- 本轮实际执行：前端全量 Vitest **514 通过**，TypeScript、Vite Build 通过；后端 `test_spatial_annotations.py` + `test_message_viewer_context.py` **29 通过**，包括资源端点访问边界及文件修订变化。
- `.e2e/test-spatial-annotations.js` 本轮真实 Chrome 执行通过：禁用 randomUUID、左栏相机、元数据异常不阻断附件、长文件名胶囊、同图六个 marker、两格不同缩放/平移精度、单气泡所属 pane、真实鼠标滚轮、ROI 模式避让、刷新恢复确认关联、源版本变化拒绝绑定、消息引用、隐藏及 DPR。浏览器中的资源版本和消息使用明确测试夹具；不是内网 VLM 或真实盘阵大图验收。
- 真实模型 Grounding、完整 SSE 故障矩阵、大图性能及人工核实状态等原有未验收项保持未验收。本轮没有修改 Lease/Deadline/Goal Runtime 或 SR 生产链。

## A. 源码审计及最小集成点

- 原有 `TifCanvas` drop → `viewer.addFiles` → 解码 → `activate` / pane 落位不变。Overlay 只读取已具备 thumb、源尺寸和身份的 pane，不在 drop 事件中建立假定成功的上下文。
- 显示使用每个 pane 的 `rect` 和 `ViewState`。原图坐标通过现有 `origToThumb`、`thumbToScreen` 投影，叠加 pane 偏移。Overlay 使用 Canvas 的 CSS 尺寸，侧栏过渡期间与既有固定尺寸居中画布一致。
- 截图继续调用 `captureForChat` / `planShot` / `drawShot`，取实际拉伸后的 thumb 可见裁剪，不合成 ROI、掩码或 UI。新增 source crop、附件尺寸及 input_ref，不改变 TIFF 解码。
- chat.send 继续冻结消息级 Viewer Context；截图映射随对应附件传输。后端以落盘后 JPEG 的实际尺寸验证映射，并加入 attachment_index 和 attachment_url。客户端普通 Viewer Context 不能自行注入服务端 visual_inputs。
- `present_spatial_annotations` 复用消息工具结果通道。原有历史投影会丢弃 data、模型结果预算会截断大对象，因此仅对新工具保留完整结果、先持久化再发 SSE。模型后续上下文仍接受原有预算限制。

## B. 组件与状态

- `frontend/src/lib/spatial.ts`：类型、影像会话身份、几何校验、投影及输入反变换。
- `frontend/src/stores/annotations.ts`：从当前会话消息恢复批注，以 Annotation ID 去重；聚焦、隐藏、开发 Mock、显式临时重关联。
- `AgentAnnotationOverlay.vue`：独立 SVG 几何、36px 命中区的编号标记、单个解释气泡；不写底图、ROI 或掩码。Marker/Popover/Highlight 在本组件内，未拆成六个独立组件。
- `AgentAnnotationReference.vue`：聊天消息里的业务引用，Viewer Agent 和 Chat 页面复用。
- `AnnotationManager.vue`：Agent 内部折叠列表、隐藏、定位、未绑定批注及开发演示入口。
- `TifCanvas`、`ContextPanel`、`AgentChatTab`、`ChatPage` 只作既有链路集成，不新增导航页或永久侧栏。

## C. 数据与持久化

- version=1；point=[x,y]，bbox=[left,top,right,bottom]，polygon 使用不重复末端闭合点的顶点数组；正式结果显式标注 source_pixel。
- 后端检查有限数值、范围、正面积、退化/自交多边形、最多 64 条、标题 80 字符、摘要 500 字符、完整结果 128KB。拒绝越界，不静默裁剪。
- 模型只提供 input_ref 和附件像素几何。后端根据本条请求已验证的 crop 映射回源图，生成确定性 UUID，verification 固定为 unverified。HTML 内容在前端按文本插值显示。
- 会话、消息序号、Tool Call ID、Run 的关联沿用原消息日志，不创建第二套数据库。前端仅从当前 chat.messages 导出批注，切换会话清除临时演示、选中和重关联。
- 影像目前采用浏览器会话内 File 对象身份及像素写入 token，不按同名文件自动匹配，不读取或哈希大 TIF。即使盘阵影像也暂不冒充具有可靠资源版本的跨会话身份。
- 刷新后批注引用可恢复，但图像默认未绑定。管理入口允许用户明确确认重新关联同尺寸源图；重关联仅内存生效，不修改历史事实。跨会话稳定资源版本及自动重绑定尚未实现。

## D. 交互

- 青绿编号、白环、细虚线区域、浅填充、白色短解释卡；仅交互节点接收指针事件。
- 点击锚点展开气泡并滚动到右侧对应引用；点击引用定位当前已打开影像。区域已完整可见时不移动；否则复用 Viewer 缓动并按区域大小缩放。
- 空白和 Esc 收起气泡；ROI 绘制期间隐藏交互层并拒绝引用定位，不接管 ROI 的 Esc。
- 每格独立投影、裁剪。同图同时显示在两格时，固定只在 panes 中第一格显示，保证一个选中批注只出现一张气泡。
- “分析当前影像”实际截图并调用既有消息发送；已有问题保留。普通载图不自动推理。
- Mock 仅开发构建且开发者模式开启时可从 UI 进入，明确标记、不写聊天数据库。

## E. 真实多模态接入

默认不向模型提供空间工具。只有服务端设置 `SR_SPATIAL_GROUNDING=1` 且当前消息具有有效视觉附件映射才启用。开启前必须确认端点接受实际 image_url，并具备可靠定位能力；此开关是部署方声明，不是自动能力探测。

实际像素继续经既有 chat_images 落盘、OpenAI 边界水合。输入坐标通过 crop 原点与独立 x/y 缩放映射；不使用 CSS 百分比、DPR 或屏幕位置推测源图坐标。缺少映射、没有附件或没有结构化结果时不创建标记。没有真实内网模型和原始盘阵数据的验收证据，不能宣称 Grounding 已验证。

已有 ROI 的确定性几何与统计继续进入提问上下文；本轮未新增专用“询问此 ROI”按钮或 ROI 像素裁剪器。

## F. 自动化记录

使用 Node 直接调用项目内脚本，绕过 npm/nvm 在沙箱内 realpath 的权限错误。

- `node frontend/node_modules/vue-tsc/bin/vue-tsc.js --noEmit -p frontend/tsconfig.json`：通过。
- 在 frontend 执行 `node node_modules/vite/bin/vite.js build`：通过；保留既有大包提示。
- 在 frontend 执行 `node node_modules/vitest/vitest.mjs run`：32 文件、510 测试通过，包含新几何测试 10 项；vendor 缺 source map 提示不影响结果。
- `python -m pytest backend/tests/test_spatial_annotations.py backend/tests/test_message_viewer_context.py backend/tests/test_chat_images.py backend/tests/test_tool_description_contract.py backend/tests/test_request_budget.py backend/tests/test_sse_ownership.py -q`：最终 88 项通过，包含完整结果历史恢复测试；空间专项 12 项通过。
- `.e2e/test-spatial-annotations.js`：真实 Chrome；拖入 JPG、无批注空态、三类 Mock、单气泡、Esc、聊天消息引用定位、平移、1366/1600、DPR 变化、隐藏、同图分屏通过。测试在内存注入明确 Mock 工具结果，不连接真实模型或写生产对话。
- 浏览器截图 `.e2e/shots/spatial-annotations.png`。静态截图不代替交互断言。
- 扩展命令 `python -m pytest backend/tests/test_goal_runtime.py backend/tests/test_spatial_annotations.py backend/tests/test_sse_ownership.py backend/tests/test_request_budget.py -q`：76 通过、1 失败。失败为 `LoopContinuationTests.test_durable_model_request_timeout_and_sdk_retries_obey_slice`，0.25 秒预算内未到达模型调用，测试读取空的 call_args 报错。单独重跑仍失败；将 `git show HEAD:backend/agent/loop.py` 在隔离 Python 进程中加载执行原始 Loop 后同样复现（未改工作树）。没有改变 Deadline 规则来掩盖失败，仍列为回归未关闭项。

## G. 尚未完成与验收边界

- 没有内网真实 VLM Grounding 验收，能力探测及可靠检测/分割后端未实现。
- 核实状态目前只展示待核实，尚未提供持久化人工核实/撤销核实；来源筛选、用户批注创建/删除尚未实现。
- 屏幕空间聚合未实现；每次输出限制为 64 条，仍可能出现密集重叠。
- 跨会话可信资源身份/版本、自动重新绑定未实现；显式临时关联要求用户确认同一源图。
- 尚未完成需求中的所有浏览器矩阵：真实大 TIFF、不同影像双屏互换、ROI 全工具、流式断线恢复、完整原有平台 E2E 均仍需补验。
- 本轮实现不应标记为需求全文完成。后续优先补状态管理、密集批注与完整验收矩阵，再在内网开启真实模型开关。
