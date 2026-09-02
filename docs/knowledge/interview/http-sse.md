# HTTP/REST/SSE/WebSocket 面试八股（interview · 高频精简版）

> 定位：跳槽高频网络/REST/实时推送自测与背诵。★=高频。每题「答（要点）+ 追问」。
> 建议先遮答案裸答、再看要点、再对追问。凡是标了「实践注记」的题，都能对上仓库真例（[api-contract.md](docs/planning/api-contract.md)、[run_sr.py](backend/services/run_sr.py)、[contract.py](backend/tools/contract.py)），讲项目时把理论落成真实经历。
> 快速导航：报文/方法/幂等 → 状态码 → REST 设计 → 缓存/协商 → CORS → HTTPS/TLS → HTTP 版本与连接 → Cookie/Session/Token → 推送技术选型（轮询/长轮询/SSE/WS）→ 流式与代理 → 错误约定与契约 → 仓库实践 3 题。

## 报文、方法语义与幂等

### Q1 ★ HTTP 报文由哪几部分组成？GET/POST/PUT/PATCH/DELETE 各是什么语义？
**答（要点）**：请求报文 = 请求行 + 头部 + 空行 + 可选 body；请求行是「方法 SP 目标 SP HTTP 版本」，如 `POST /api/queue HTTP/1.1`。响应报文 = 状态行（版本 + 状态码 + 原因短语）+ 头 + 空行 + body。

- GET：取资源，safe（不改服务端状态）、可缓存，一般不带 body。
- POST：把数据交给指定资源处理——建子资源 / 提交副作用 / RPC 式动作。不保证幂等。
- PUT：整资源覆盖式更新（「给这个 URI 赋值这个完整表示」），语义幂等——重复 PUT 最终状态一致。
- PATCH：部分字段更新（增量描述），按实现未必幂等（例如 `{"op":"append",…}` 重复执行会叠加）。
- DELETE：删资源，语义幂等——再删一次只是 404，不改变最终状态。
- 补充常被顺带问的：HEAD = GET 只要头不要 body（探活/拿元信息）；OPTIONS = 问服务端能力 / CORS 预检。

```http
POST /api/chat/sessions/{id}/messages HTTP/1.1      ← 请求行：方法 目标 版本
Host: 192.168.1.10:8000
Content-Type: application/json

{"content": "查一下盘阵有几个场景"}
```
响应行同理：`HTTP/1.1 200 OK`。头、空行、body 三段之外，**空行是头结束的哨兵**，body 用 `Content-Length` 或 chunked 表明边界（见 Q18）。

**追问**：为什么 POST 不幂等而 PUT 幂等？幂等是「多次执行与服务端最终状态一致」，POST 每次可能新建一条记录；那么 POST 想安全重试要靠什么？——幂等键或后端指纹去重（仓库 run_sr 的做法，见实践注记）。

### Q2 ★ HTTP 状态码分哪几类？关键码怎么记？
**答（要点）**：五类见下表。速记口诀：1xx 信息、2xx 成功、3xx 重定向、4xx 客户端错、5xx 服务端错。背「404/500/502/503/504/429」这几个运维天天见的最值钱。

| 分类 | 关键码 | 适用场景 |
|---|---|---|
| 1xx | 100 Continue；**101 Switching Protocols** | 100 继续发 body；101 是 WebSocket「升级协议」成功的应答 |
| 2xx | **200** OK；**201** Created；202 Accepted；**204** No Content | 200 通用成功；201 创建成功要带 Location；202 异步已受理；204 无 body 成功 |
| 3xx | 301/302/307/308；**304** Not Modified | 301 永久/302 临时（307/308 保留方法不变）；304 协商缓存命中，无 body |
| 4xx | 400；401；403；404；405；**409** Conflict；413；**415**；**422**；429 | 400 参数错；401 未认证；403 无权限；404 不存在；409 冲突（并发/状态）；415 媒体类型不支持；422 语义校验失败（业务字段错）；429 限流 |
| 5xx | **500**；501；502 Bad Gateway；503；504 Gateway Timeout | 500 内部异常；502 上游坏响应；503 过载维护；504 上游超时（nginx 常见） |

区分记忆：401 是「你没证明你是谁」，403 是「证明了你也不许进」；422 比 400 更具体，指「请求体能解析但字段语义不过关」（FastAPI/Pydantic 默认就是 422）；502 是「上游回了个坏东西」，504 是「上游一直没回」。

**追问**：307 和 302 差别在哪？重定向会丢不丢 POST body？—— 302 历史上浏览器可能把 POST 改成 GET，307/308 强制保持方法与 body，所以表单重定向要小心。

### Q3 ★ 什么是幂等？哪些方法天然幂等？safe 和幂等是一回事吗？
**答（要点）**：幂等 = 对同一个请求执行 1 次与执行 N 次，服务端资源最终状态一致（不追求响应一致、不算请求次数）。GET/HEAD/OPTIONS/PUT/DELETE 语义上幂等；POST/PATCH 不保证。

- safe（无副作用、可被代理/爬虫放心重放）与幂等是正交概念：GET 既 safe 又幂等；DELETE 不 safe 但幂等；PUT 不 safe 但幂等。
- 幂等是 HTTP 方法语义层的事；「业务幂等」要靠实现兜底——对副作用操作，用参数指纹去查是否已执行过，命中则复用不重复执行（见实践注记 run_sr）。
- 别把幂等误读成「每次响应都一样」：它承诺的是**服务端最终状态**，第二次调用可以返回「已存在，没重复建」（201/200 之外常是 200 + 已存在标记）。把非幂等 POST 变幂等的三层常见手法：客户端带幂等键头 → 服务端按键记忆；服务端按 body 参数自算指纹（仓库做法）；或让「提交动作」本身幂等（任务表 upsert）。

**追问**：接口返回 500 之后客户端该不该自动重发？—— 仅对幂等请求可安全重发；对 POST/PATCH 需要幂等键（Idempotency-Key 或后端指纹表）否则可能重复下单/重复提交。

## REST 设计

### Q4 ★ REST 的核心约束是什么？怎么把业务建模成资源？
**答（要点）**：REST 是「面向资源」的架构风格，核心是**资源 + 表现 + 无状态**：一切可操作对象都是 URL 标识的资源（名词，不用动词）；用 HTTP 方法表达操作；用 Content-Type/协商表达资源的多种表现（JSON/XML…）；无状态 = 服务端不存客户端会话，每个请求自带全部上下文，可水平扩展、任意一台机器都能处理。

- URL 只放资源：`/api/chat/sessions/{id}/messages`，动作进方法：`POST messages` 发一条、`GET messages` 取历史；别做 `/api/getMessages` `/api/deleteUser` 这类动词 URL。
- 子资源层级化：collection `/tasks` → item `/tasks/{id}` → 子资源 `/tasks/{id}/cancel`（动词兜底给「动作型」操作，属可接受的务实例外）。
- 无状态与状态矛盾处：真正需要会话态（聊天上下文、任务进度）时，把「当前在哪一步」落库/落缓存（session_id、task 行），让 HTTP 请求本身仍无状态——仓库聊天就是把会话与回合状态持久化，GET messages 可恢复渲染。

**追问**：REST 用 HTTP 状态码表达错误是不是最「正统」？—— 正统派确实主张「语义用足 HTTP 码」。但实践里很多团队发现 HTTP 码表达不了细粒度业务失败，于是走 `{ok,data,error}` 业务码风格，两种取舍见「REST 错误约定」一题。

### Q5 URI 设计有哪些规范？Query 参数什么时候用？
**答（要点）**：URI 只标识资源、不含动作与动词；路径用**名词复数 + 层级**，资源 id 放路径（`/tasks/{id}`）；Query 参数用于**筛选、分页、排序、投影**（`?state=RUNNING&page=1&limit=20&fields=id,state`）以及不改变「资源本体」的取值参数——它不属于资源地址的一部分。传统上 POST 的字段可放 body，过滤条件放 query；注意 GET 语义是无副作用，别用 GET 携带写操作参数。

- 命名约定：小写 + 连字符（kebab-case）或下划线，前后端契约里写死一种；复数集合 + 单数条目。
- 版本化：`/v1/…` 放前缀 vs 放 header 各有拥趸；放路径最简单、可被代理/缓存区分。
- 状态码配合：POST 建新资源回 201 + Location；DELETE 成功常见 204；资源不存在 404；并发写冲突 409。

**追问**：分页参数该用 query 还是 header？大列表、二进制文件参数怎么办？—— 分页、筛选这类「对集合的视角参数」用 query；对大块数据（JSON 太长）用 POST + body 更稳，也说明「纯 REST 教条要和工程现实妥协」。

## 缓存与内容协商

### Q6 ★ 浏览器缓存怎么工作？强缓存和协商缓存的区别与优先级？
**答（要点）**：分两级。**强缓存**：命中直接本地用，不发请求。靠 `Cache-Control: max-age=60`（相对秒数，现代唯一权威）；老的 `Expires` 是绝对时间、已被 Cache-Control 覆盖。**协商缓存**：强缓存过期后带条件去问服务端，服务端比对——`ETag`（资源指纹，可带 `W/` 表示弱校验）对 `If-None-Match`，或 `Last-Modified` 对 `If-Modified-Since`；没变回 **304**（无 body），变了回 200 + 新资源。优先级：先走强缓存判断新鲜度，新鲜则不发请求；不新鲜才协商。

- `no-cache` ≠ 不缓存：no-cache 是「用之前必须先回源协商」；真正不存是 `no-store`。`public/private` 管共享缓存可否存；`must-revalidate` 防过期资源被继续用。
- 实践记两处：静态资源（打包产物）用 `max-age=一年 + 内容 hash 文件名`，hash 变了 URL 自然失效；动态接口与 SSE 要 `Cache-Control: no-cache`（SSE 里前端还得防代理把流缓存/缓冲了，见 nginx 题）。
- 谁来缓存也分档：浏览器私有缓存（`private`）、CDN/代理共享缓存（`public`）、还有协商缓存跨进程是否共享 ETag 的问题（多实例时 ETag 要一致否则来回 304/200 抖动）。
- 面试加分细节：`Vary` 声明「同一个 URL 因 Accept/语言/编码而变体」，代理没看清 Vary 会把 A 用户的响应错发给 B；`Age`/`Date` 配合判断陈旧度。

**追问**：304 会返回 body 吗？—— 不会，304 只有头；它本身也是一次网络往返，所以强缓存才「省钱」，协商缓存只省带宽不省请求。前端强刷（Ctrl+F5）会带上 `Cache-Control: no-cache` 强制回源协商。

### Q7 Content-Type 与 Accept 各管什么？为什么 415/406 与之相关？
**答（要点）**：两个方向的内容协商：

- `Accept`（请求头）：客户端声明**想收什么**（`Accept: application/json`、`*/*`）。服务端按它选表现（vary 协商）。要求得不到满足可回 406 Not Acceptable（实践中少用，多数服务端只产 JSON）。
- `Content-Type`（可双向）：声明 body 的**实际格式 + 字符集**，`application/json`、`application/x-www-form-urlencoded`、`multipart/form-data`（文件上传）、`text/event-stream`（SSE）、`text/html; charset=utf-8`。请求体类型服务端不支持 → 415；响应的 Content-Type 决定前端怎么解析（fetch `res.json()` 其实不检查它，但 devtools/代理/监控要看）。

**追问**：JSON 里中文为什么还要 charset=utf-8？—— JSON 规范默认 UTF-8，现代栈不必写；老接口/表单偶发编码错乱才需要显式。另外「同一个 URL 给 JSON 还是 HTML」正是协商的活，识别维度要一致，别按 UA 随手切。

## 同源策略与 CORS

### Q8 ★ 什么是同源策略？跨域请求浏览器会拦什么、CORS 怎么放行？
**答（要点）**：**同源 = 协议 + 主机 + 端口三者全同**。浏览器对跨源 JS 读取响应做默认封锁：脚本能**发出**请求，但读不到响应（fetch/xhr 抛错、Canvas 被「污染」）。同源策略防的是「恶意站 A 的脚本偷读你在站 B 的数据」。

- CORS（跨源资源共享）= 服务器通过响应头**显式授权**浏览器放行：核心是 `Access-Control-Allow-Origin`（`*` 或回显请求 Origin）；要带 cookie 时设 `Access-Control-Allow-Credentials: true`，此时 Allow-Origin 不能用 `*` 必须指名。
- 读响应需要的自定义头还要 `Access-Control-Expose-Headers` 暴露（默认 JS 只能读 Cache-Control/Content-Type 等少量）。
- 请求会**照样到服务器**——CORS 只是浏览器不把响应交给页面 JS，不是安全墙；防攻击要靠服务端鉴权/CSRF 手段，不是靠 CORS。
- 完整头清单别只背一个 Allow-Origin：`Allow-Credentials`（带 cookie 时必 true，且 Origin 不能 `*`）、`Allow-Methods`、`Allow-Headers`（回显预检里客户端声明的头）、`Expose-Headers`（决定 JS 能读哪些响应头）、`Max-Age`（预检结果缓存）。后端两个选择：`*` 通配（无凭证场景）或**动态回显 Origin**（把发起方 Origin 原样返回，配 Allow-Credentials:true 用）。
- 记住同源策略**只存在于浏览器**：curl/服务端互相调没有 CORS 这回事，别拿它当接口安全认证；真正边界仍是「白名单 + 鉴权」。

**追问**：什么请求需要先发 OPTIONS 预检？简单请求怎么判？—— 见下题。

### Q9 ★ 简单请求的条件与预检 OPTIONS 流程
**答（要点）**：浏览器先判断是否「简单请求」，是则直接发、不预检。简单请求需同时满足：方法 ∈ {GET, HEAD, POST}，且自定义头只有白名单几个（Accept、Accept-Language、Content-Language、Content-Type 且仅限 `application/x-www-form-urlencoded` / `multipart/form-data` / `text/plain`）、Content-Type 不是 application/json——所以**前端发 JSON 的 POST 都不是简单请求**，会触发预检。

- 预检：浏览器先发 `OPTIONS`，带 `Access-Control-Request-Method`（想用的方法）和 `Access-Control-Request-Headers`（想带的头）；服务器回 `Access-Control-Allow-Methods / Allow-Headers / Allow-Origin` 等；通过后浏览器才发真请求。
- 每次 POST 都预检会很慢，可用 `Access-Control-Max-Age` 缓存预检结果（秒）。仓库 FastAPI 直接 `CORS 全放`（内网 IP:端口直连），是因为无需鉴权且不想让预检拖慢内网 LLM/大图场景——但**生产公开接口必须收紧 Allow-Origin 白名单**。

**追问**：自定义 header（如 Authorization）为什么会逼出预检？—— 预检的意义在于让服务器先表态「我认这些跨源头/方法」，避免简单请求那种「HTML 表单即可伪造」的跨站副作用被带自定义头执行。这也是为什么 JWT 若放 Authorization 头，天然挡掉 CSRF 式简单跨站提交。

## HTTPS / TLS

### Q10 ★ HTTPS 为什么安全？TLS 握手大概怎么走？
**答（要点）**：HTTPS = HTTP + TLS，解决三件事：**机密性**（数据加密防窃听）、**完整性**（防篡改）、**身份认证**（防冒充——服务器得拿出受信任 CA 签发的证书）。没有它，局域网/公网上的 HTTP 明文可被中间人看光改光；内网传遥感任务参数虽不涉钱，但信任与调试边界仍建议 TLS。

- 简化握手（TLS 1.2 大致）：① ClientHello（随机数、支持的加密套件）→ ② ServerHello + 服务器证书 → ③ 客户端验证证书链（是否受信 CA、域名是否匹配、是否过期），用非对称交换（如 ECDHE 临时密钥）算出**预主密钥** → ④ 双方各自派生出相同的会话密钥（对称密钥），此后数据用对称加密（AES-GCM/ChaCha20）通信。ChangeCipherSpec + Finished 确认后握手完成。
- 精髓：**非对称/密钥交换用来安全地协商出对称密钥**，之后大量数据走快的对称加密；证书解决「跟你交换密钥的是不是真身」。TLS 1.3 更少往返（1-RTT，恢复连接 0-RTT），删掉了旧的不安全套件。
- 客户端验证书四问：证书是否由受信 CA 链签发、域名是否匹配（SAN）、是否过期/被吊销（OCSP）、公钥用途对不对。链上任一环造假都会被拒。
- wss:// 与 ws:// 的关系、以及 EventSource 在 https 页面必须连 https 源的注意点同理。

**追问**：为什么握手开销被反复提？—— TCP 一次握手 + TLS 一到两次往返，新连接代价高，所以连接复用（keep-alive / HTTP/2 多路复用 / 连接池）价值巨大，见下题。证书信任链谁签？根 CA → 中间 CA → 站点证书，浏览器内置根证书。

## HTTP 版本与连接管理

### Q11 ★ HTTP/1.1、HTTP/2、HTTP/3 差在哪？「队头阻塞」到底指什么？
**答（要点）**：

- HTTP/1.1：一个 TCP 连接上同一时刻只能处理一个请求（管线化基本没落地），前面的请求慢，后面全排队——**应用层队头阻塞**。缓解靠浏览器每主机开 ~6 条连接并发。默认 keep-alive 复用连接。
- HTTP/2：二进制分帧 + **多路复用**——一条连接上多个「流」交错传，应用层队头阻塞没了，大幅省连接数；还有 HPACK 头压缩。但底层仍是**单条 TCP**：TCP 保证有序，丢一个包会重传并阻塞该连接上所有流——**传输层（TCP）队头阻塞**仍在。
- HTTP/3：把传输层换成基于 UDP 的 **QUIC**，每个流独立丢包重传，不再互相阻塞；握手更快（TLS 1.3 内置）、支持连接迁移（切 WiFi 不断）。代价是 QUIC 需 UDP 放行（nginx 等代理要支持）。
- 版本怎么谈下来的：握手阶段经 **ALPN** 扩展协商——客户端列 `h2,http/1.1`，服务器选它支持的最高档；所以 TLS 一定发生在 HTTP/2 之前。升级后你该观察的指标也变了：h2 看单连接的并发流数，别再数「每域 6 连接」。

**追问**：为什么说「HTTP/2 多路复用」在弱网反而可能变差？—— 单条 TCP 只要丢包率上来，重传拖累整条连接所有流；HTTP/1.1 多条连接反而把损失隔离在各自连接里。这也是 HTTP/3 存在的核心理由。

### Q12 keep-alive / 连接复用是什么？长连接（SSE/WS）为什么特别依赖它？
**答（要点）**：HTTP/1.1 默认持久连接（keep-alive）：一次 TCP+TLS 握手后多个请求复用同一连接，避免反复握手。好处是省握手往返与资源；对 SSE / WebSocket 这类「一个连接挂很久」的更是刚需——它们本来就靠这条不关闭的 TCP 连接推送。相关配置面：`Connection: keep-alive/close` 头、HTTP/2 天然单连接多路复用（还送连接池）、代理层 keepalive（nginx `upstream keepalive`）、应用层连接/线程上限要与之匹配。

- 长连接是资源：每个挂着的连接占 fd/内存，服务端要设超时回收（nginx `proxy_read_timeout`、uvicorn worker 数）；聊天的「会话锁」思路同理——连接与回合是稀缺串行资源（见 409 语义）。

**追问**：为什么 SSE/WS 之后反向代理和 worker 数量要一起想？—— 无状态短请求可以任意水平扩 worker，但一条 SSE 连接必须**钉在某个 worker 进程**上且不能被关掉，多 worker 广播就得上外部队列；仓库契约 §4.3 就写明「单进程 uvicorn，多 worker 需外部队列」。这是「无状态 × 长连接」最典型的张力。

## Cookie / Session / Token

### Q13 ★ Cookie、Session、Token（JWT）各自怎么用、怎么选？
**答（要点）**：三者解决「请求无状态，但服务要知道你是谁」的认证/状态难题，状态放哪不同：

- Cookie：客户端小存储（约 4KB），`Set-Cookie` 由服务器下发、浏览器按域/路径自动带上。关键属性：`HttpOnly`（JS 读不到，抗 XSS 窃取）、`Secure`（仅 https）、`SameSite=Lax/Strict`（防 CSRF）、`Domain/Path` 作用域。本身是**载体**，里面装 session id 或 token 都行。
- Session：**状态存在服务端**（内存/Redis/DB），客户端只拿 session_id（常放 cookie）。优点：可随时吊销、服务端可控；缺点：有状态 → 水平扩展要共享存储（Redis）、每次请求查一次。
- Token/JWT：**状态打进自包含令牌**，服务端验签即认（`HMAC/RSA` 签名 + 过期时间），无共享存储、天然水平扩展。缺点：签发后**难提前吊销**（只能等过期，或加黑名单/短 TTL+refresh）；JWT 有点大；若放 localStorage，被 XSS 读走即失守。
- 一句话选型：要「可即时吊销、服务端权威」→ Session+共享存储；要「无状态扩展、跨域/移动端顺手」→ JWT，用短生命周期 + refresh 缓解吊销难。Chrome 三方 Cookie 限制还进一步挤压 cookie 方案的跨站场景。
- Cookie 其余属性和坑：`Expires`/`Max-Age`（会话 vs 持久）、`Domain`/`Path` 决定发给谁、单域名 ~几十个/单条 ~4KB 上限、`SameSite=None` 需要 Secure。面试爱问的辩证点：**Session 也可把 id 放 token、JWT 也可装 cookie**——关键区别不是存放位置而是「状态在谁手里、能不能吊销」。

**追问**：JWT 放 cookie 还是 localStorage 哪个安全？—— cookie+HttpOnly 抗 XSS 但怕 CSRF（用 SameSite 挡），localStorage 抗 CSRF 但怕 XSS；务实组合是 HttpOnly+Secure+SameSite cookie。另注意 refresh token 与 access token 分开、refresh 可吊销。

## 推送技术：轮询 / 长轮询 / SSE / WebSocket

### Q14 ★ 服务端要主动推数据给前端，有哪几种实现？各自的机制与坑？
**答（要点）**：四种主流：

- **轮询（polling）**：前端定时 GET。实现最简，但延迟 = 间隔、请求大量浪费、无事件时空转，负载随连接数线性涨。
- **长轮询（long polling）**：请求挂着不发响应，服务端有事件才回（或超时兜底），前端收到再立刻续一根。把「推送」在 HTTP/1.1 上模拟出来；坑是服务端得**一直占着连接和 worker**，超时/断线/乱序都要自己处理，连接风暴下容易雪崩。
- **SSE（Server-Sent Events）**：一条**只读、单向（服务端→客户端）**的 HTTP 长连接，格式就是普通 HTTP + `Content-Type: text/event-stream`，文本逐事件下发。
- **WebSocket**：**双向全双工**的独立协议（101 升级），二进制/文本都能传，开销低。

**追问**：哪种本质还是 HTTP、最省心？哪种是真协议？—— SSE 是「普通 HTTP 响应的延续」，代理/负载均衡友好、自带断线重连；WebSocket 是单独协议，代理/防火墙要显式支持。往下两题分别展开。

### Q15 ★ 什么时候用 SSE、什么时候 WebSocket、什么时候轮询？「单向状态推送」场景怎么选？
**答（要点）**：按**方向性 + 消息类型 + 复杂度**拍板：

- 只需要**服务端单向下发状态**（任务进度、日志流、通知、消息列表更新、AI 回合中间事件）→ **SSE 最省**：HTTP 语义透明、断线自动重连 + Last-Event-ID 续传、不用自己维护协议。浏览器同源连 ~6 条（HTTP/1.1），SSE 数量有限，够用。
- 要**双向低延迟**（聊天你来我往都高频、在线协作、游戏、白板）→ WebSocket，但自己补心跳、断线重连、消息序。
- 事件**稀疏且可有秒级延迟、要最大兼容** → 轮询/长轮询。
- 仓库的两个典型：聊天回合 = 一次请求内服务端**单向**吐一串事件（turn_start→tool_call→…→turn_done）→ 用「POST 即流 + SSE 帧」；队列状态 = 单向订阅 `GET /api/queue/events` → SSE 广播最贴切。规则 = **一个回合/订阅的单向事件流，先想 SSE**。
- 反例提醒：**只是「前端想拿数据、能主动拉」就不需要任何推送**——轮询复杂度最低；多个浏览器要互相实时看见对方（协作画布、多端同步）才是 WebSocket 场景，那时每个客户端既要发又要收、且要实时看「别人的」状态。再补一个规模坑：HTTP/1.1 浏览器同源 SSE 上限 ~6 条，订阅页多于几条就要 1.1→h2 合并或升级连接管理。

**追问**：SSE 能不能像 WebSocket 那样自由改协议/发二进制？—— 不能，SSE 是文本事件流、只服务端推；要全双工/二进制才轮到 WebSocket。反过来 WebSocket 没给「断线续传位置」的机制，而 SSE 的 `Last-Event-ID` 天生支持（见下题）。

### Q16 ★ SSE 的报文与事件帧格式长什么样？重连怎么续传？
**答（要点）**：响应的关键头：`Content-Type: text/event-stream`、`Cache-Control: no-cache`（防缓存），`Connection: keep-alive`，经代理还要关缓冲（`X-Accel-Buffering: no`，见 nginx 题）。body 是若干帧，每帧字段按行：`data: <payload>`（可多行，多行 data 在客户端以换行拼）、`event: <类型>`（不给默认触发 `message` 事件）、`id: <事件 id>`、`retry: <毫秒>`；帧以**空行**结束：

```
event: job_update
id: 42
data: {"type":"job_update","task_id":3,"state":"RUNNING","ok":true}

: ping 注释行（冒号开头，当心跳，防代理断链）
```

前端 `EventSource` 收到即触发对应事件的 listener。断线时浏览器**自动重连**并带 `Last-Event-ID` 请求头，服务端据它从断点续推（所以每条事件要给稳定的 id）。还可用注释行/定时 `retry` 当心跳防中间层把空闲连接掐掉。

**追问**：EventSource 有哪些硬限制？—— 只支持 GET（不能带 body、不能自定义请求头——所以带 token/发内容得用 fetch 流式手动解析，仓库聊天正是如此）；文本为主；浏览器对同源 HTTP/1.1 连接数有限；EventSource 拿不到 HTTP 码 4xx/5xx 的细节也处理不了「SSE 已经开流之后的中途错误」，这类得在事件体里传（仓库 `error` 事件）。帧里的 `data:` 若超过若干 KB，多数实现要小心代理缓冲切块，仍是大文件别走 SSE。

### Q17 ★ WebSocket 握手与全双工怎么建立？和普通 HTTP 什么关系？
**答（要点）**：WebSocket 靠 HTTP 的一次 **Upgrade 握手**进入长连接双工：

1. 客户端发普通 HTTP GET，带 `Upgrade: websocket`、`Connection: Upgrade`、`Sec-WebSocket-Key`（随机 base64）、`Sec-WebSocket-Version: 13`。
2. 服务端同意则回 **`101 Switching Protocols`** + `Sec-WebSocket-Accept`（=`base64(sha1(key + 固定 GUID))`），校验通过后连接协议切到 WebSocket。
3. 此后双方可随时互发**文本/二进制帧**（含帧头：FIN/opcode/掩码/长度），双向全双工、低开销，不再走 HTTP 语义。

- 坑：握手要过代理，nginx 要 `Upgrade`/`Connection` 头透传与长超时；URL 用 `ws://`/`wss://`。
- 可靠性靠自己：没有内建断线重连与心跳——一般实现层加定时 `ping/pong` 探活、异常后指数退避重连、消息去重（见「超时/重试」题）。
- 浏览器到某域连接数也受限；服务端 `wss` 的负载均衡要支持长连接会话保持。

**追问**：101 在 HTTP 状态码分类里属于哪类？WebSocket 握手的 Key 校验能防什么？—— 101 属于 1xx 信息类（状态码速记见 Q2 表）。`Sec-WebSocket-Accept` 的哈希校验用于证明「对方真是支持 WebSocket 的服务器」，避免代理把升级请求错误转发成普通 GET。

### Q18 ★ 分块响应 / 流式响应怎么工作？StreamingResponse 和 ReadableStream 是什么？
**答（要点）**：HTTP 响应不带 `Content-Length`（长度未知/边算边发）时用 **chunked 传输**（`Transfer-Encoding: chunked`），body 按块流式发给客户端；SSE、大文件下载、AI token 流都是它。现代前端用 `fetch` 的 `res.body`（**ReadableStream**）边收边读，而不是等整包 `res.json()`。

- 服务端（Starlette/FastAPI）：`StreamingResponse` 接收一个**生成器/迭代器**，框架逐项写进响应、保持连接；SSE 服务端也是把一个「事件队列 → 逐帧 yield 字符串」的生成器交给 StreamingResponse。异步实现里注意同步阻塞逻辑要丢线程池（`asyncio.to_thread`）别占事件循环——仓库契约用线程桥把同步 `run_loop` 的事件转 `asyncio.Queue` 再逐帧 yield，就是这个结构。
- 客户端流式：`for await (const chunk of resp.body)` 后把字节按 `\n\n` 切 SSE 帧再 `JSON.parse`——这就是「EventSource 不支持 POST 时，用 fetch+ReadableStream 手搓 SSE 解析」的原理（见实践注记 Q23）。注意 chunk 不一定按帧对齐：**要攒缓冲、跨 chunk 拼接完整帧**再解析，最后处理连接中途断开（Reader 抛错/`cancel()`）与未结束的半帧。
- 术语对照：`Transfer-Encoding: chunked` 是**传输层**的分块（逐块按长度前缀发），`Content-Length` 与之互斥；流式响应没有确定长度所以走 chunked。别把它和 SSE 的事件分帧（`\n\n`）混为一谈——两层：传输分块在前，事件帧在后。

**追问**：流式响应最容易在哪个环节「不流」？—— 中间任何一环缓冲就前功尽弃：nginx 默认 buffering、uvicorn/worker、CDN、gzip 逐块（gzip 本身还算逐块）。要流式必须从应用（X-Accel-Buffering: no）到代理（proxy_buffering off）全线关缓冲，见下题。

## 反向代理、超时与健壮性

### Q19 ★ 为什么 SSE/WS 过 nginx 会卡？`proxy_buffering off` 和读超时要怎么配？
**答（要点）**：nginx 反代默认**缓冲上游响应**——它把后端吐的块攒够才转发给客户端（还默认开 cache）。对 SSE/长轮询这种「要边到边转」的流这是灾难：事件迟迟不落地、连接像是卡死。要点：

- 应用/代理两头关缓冲：后端响应带头 `X-Accel-Buffering: no`（nginx 认这个头）；nginx 侧 `proxy_buffering off; proxy_cache off;`。关缓冲只影响流式响应，普通 JSON 不受影响。
- `proxy_read_timeout`（两次读到上游数据之间的最大间隔）默认 60s，SSE 空闲期超过就 504——所以要调大（仓库配 `proxy_read_timeout 3600s`）。
- 上游是 HTTP/1.0 时 nginx 会读一块关一次连接，需 `proxy_http_version 1.1; proxy_set_header Connection '';` 让长连接持续。WebSocket 还需透传 `Upgrade`/`Connection` 头。
- 方向：**应用层也要心跳**（SSE 注释行 ping、WS ping/pong）让中间层和浏览器都认为连接活着；代理空闲超时、负载均衡连接保持、worker 数一起通盘配（单进程广播场景见 Q12 追问）。

仓库对 `/api/` 反代的写法（契约 §6）可当背版：

```nginx
location /api/ {
    proxy_pass http://127.0.0.1:8000;
    proxy_http_version 1.1;      # 上游走 HTTP/1.1 长连接
    proxy_set_header Connection '';  # 读一块就断的锅在这治
    proxy_buffering off;         # SSE 必须关缓冲
    proxy_cache off;
    proxy_read_timeout 3600s;    # 两次读到之间的空闲上限，超长回合别 504
    # WS 场景另加：proxy_set_header Upgrade $http_upgrade;
}
```

`proxy_read_timeout` 是「两次读到上游数据之间的间隔上限」，不是总时长——回合再长，只要在持续吐帧就不会 504。

**追问**：关掉 buffering 会有什么代价？—— 一个字节一个字节往后放，转发效率与吞吐下降、对慢客户端会占用上游连接更久；所以只对需要流式的端点关，普通接口保持默认。另：`proxy_read_timeout` 调大意味着要同时约束好服务端别真的把连接挂死不放。

### Q20 ★ 超时/重试/退避怎么设计才不乱？什么时候重试是安全的？
**答（要点）**：先分层定超时（连接超时 / 读超时 / 总超时，各层自设且要协调：客户端 < nginx read timeout < 上游自己别无限挂）。再谈重试：

- **只对幂等请求自动重试**（GET/PUT/DELETE 或带幂等键的 POST）。非幂等 POST 重试前必须确认「上次到底执行没有」（结果未知 ≠ 失败）——仓库 run_sr 对「写了 intent 但没拿到 job_id」的崩溃重放就明确**拒绝盲重试**，先查调度器再决定。
- 用**指数退避 + 抖动**（1s→2s→4s…加随机），别并发齐射把服务打崩；读 `Retry-After`（429/503）给出的时间。
- 场景化：HTTP 客户端重试对 5xx/超时（可能还没执行或执行了没回执）；4xx 别重试（重试也错）。网络侧配合「失败快速失败 + 兜底提示」，别无限重试。
- 客户端给非幂等 POST 加重试时主动补 `Idempotency-Key` 头（服务端记忆、同键同果）；服务端限流回 `429 + Retry-After`、过载维护回 `503 + Retry-After`，客户端读它而不是按自己节奏硬闯——服务端给的退避最权威。
- SSE/WS 的重连也算重试：EventSource 自带退避重连 + `Last-Event-ID` 续传；WebSocket 自实现同样用退避 + 心跳 + 幂等补发。做任务的幂等去重表与「先记录意图、后记副作用结果」模式，是网络不可靠世界里的通用解法（见实践注记 Q24）。

**追问**：重试会不会放大系统负载？—— 会，所有客户端同时退避重试会造成「惊群/重试风暴」，所以要求指数退避 + 抖动 + 最大次数上限；服务端该配 429/Retry-After 主动限流。

## REST 错误约定与契约

### Q21 ★ REST 错误该用 HTTP 状态码还是 `{ok,data,error}` 业务码？两种风格怎么取舍？
**答（要点）**：两种主流：

- **纯 HTTP 语义派**：错误一律映射为状态码，body 给详情（如 `{"detail":"会话不存在"}` + 404）。优点：语义标准、能被 nginx/监控/网关直接识别、客户端 `res.ok` 一把梭；缺点：粒度不够细、业务失败与传输失败难分（例如「LLM 调用失败」算 500 还是 200？）。
- **业务码派**：传输层只管「通没通」，业务成败看 body `{ok, data, error}`（可再加 code 细分）；HTTP 恒 200 或只在不可解析时用 400/4xx。优点：工具/Agent 调用链路上「ok=false 也是一种正常结果」、调用方统一判 ok 即可、错误消息人话；缺点：HTTP 层与监控看不清、代理缓存语义变弱。

- 仓库做法即**混合**：框架/传输层错误（参数不可解析、资源不存在、并发冲突）用 `{"detail":…}` + 真实状态码（400/404/409/422）；工具层业务结果一律 `{ok,data,error}`（HTTP 恒 200，ok=false 是业务结果不是传输错误）。取舍原则：**基础设施要懂的错用 HTTP 码，业务要判断的成败用 body 码**，两层不混。

- 额外提醒：两种风格都行，但**别在同一层混成薛定谔**——要让基础设施（nginx 健康检查、告警、网关重试策略、`res.ok` 语义）能依赖 HTTP 码，就让 5xx 真代表服务端坏；若业务失败全塞 200+ok:false，网关的 5xx 告警与重试就全瞎了。这也是仓库「传输/框架层错用 HTTP 码、业务结果用 body 码」分家的动机。

**追问**：什么场景逼你非得用业务码？—— 服务端要返回「多步调用中某一步业务失败、但传输与进程都健康」的结果给程序方（Agent 工具调用、批量任务、异步作业提交），此时把 HTTP 层当纯管道最省事；而给浏览器导航/给网关看的接口则更吃 HTTP 语义。强一致的组织会定死一套，混用必须在契约里写明边界（仓库契约正是这么约定并落成代码：`contract.ok/err` 生成 `{ok,data,error}`）。

### Q22 ★ JSON Schema 怎么当「前后端契约」用？「先契约后代码」和契约测试是什么？
**答（要点）**：JSON Schema 用 JSON 描述参数/响应的结构（类型、必填、嵌套、枚举、约束），一份描述多方复用：后端用来校验入参、生成 API 文档，前端用来生成类型（快）和做 mock，LLM 工具调用用它声明 function `parameters`——**同一份 schema 一个真相源**。

- **先契约后代码（contract-first）**：动手写实现前，先定端点、请求/响应/事件字段，评审通过才写码；契约改了先改文档、标注状态、评审后再动代码——仓库的 API 契约文档就是这个流程的落点，SSE 各事件共用首字段 `type` 也是为向后加字段留的兼容缝。
- **契约测试**：前后端各自拿同一份 schema/事件样例锁字段——后端单测断言产出的 JSON/SSE 帧符合 schema，前端单测锁 parser/渲染；再加 mock（假 LLM/假调度器）驱动的 e2e，离机把整条链跑绿，真机项另排。这样「两边各自改了字段」会在 CI 立刻炸，而不是上线才断。

- 契约落地到栈：后端用 Pydantic/JSON Schema 在**运行时**校验入参（仓库 tools 的 params_schema 既喂 LLM function-calling、又被同一注册表拿去生成 REST 端点，一处 schema 两处消费）；前端用工具从 schema 生成 TS 类型、拿样例做 mock。改参数的流程 = 先改 schema + 文档 → 评审 → 后端测试 + 前端测试一起锁，谁漏改谁红。
- 时序坑：**契约是同步问题**——前后端各自独立上线时，老前端打新后端或反之都会断；所以要兼容策略（新字段可选、老字段别删）或前后端同发版。接口文档自动生成（FastAPI `/docs`）只解决「说什么」，不解决「何时两端一起变」，后者靠评审节奏和契约测试兜住。

**追问**：纯契约测试防得住「两边都按旧契约实现、契约本身过期」吗？—— 防不住，所以要「文档即契约 + 契约测试 + 变更评审」三位一体：schema 是单一真相，改动走评审，测试锁两边一致。OpenAPI（FastAPI 自动产）其实就是把契约工程化的产物：schema → 校验 → 文档 → 客户端 SDK 一条线。

## 实践注记（本仓库 · 讲项目时的「真实钩子」）

### Q23 ★ 为什么仓库聊天用「POST 即流（fetch + SSE 帧）」而不是 EventSource？
**答（要点）**：聊天回合需要一个**携带 body 的请求**开一条流：用户要发 `{"content":…}`、触发服务端一次 `run_loop`，由服务端把一个回合内的多步事件（turn_start → tool_call/tool_result → assistant → turn_done/error）作为单次响应的 SSE 帧**单向**吐回（契约 §3.2）。而 `EventSource` 只支持 GET、不能带请求头也不能带 body——放不下用户内容，也难带续接/鉴权上下文。于是前端用 `fetch(method:POST)` 读 `res.body`（ReadableStream）手工按 `\n\n` 切 SSE 帧解析。

- 反向也说明选型：这一步推送方向仍是「请求-响应式的单回合服务端 → 前端」，SSE 帧是**载体**，POST 是**入口**；真正需要服务端主动、无请求也能推（如队列状态变化广播）才用纯 `GET /api/queue/events` 的 EventSource。两条路并存是合理的——按「是否有上行内容 + 是否长驻订阅」分。

**追问**：SSE 中途出错 HTTP 状态码还能改吗？—— 不能，流已开、状态码已定，所以错误改成 `{"type":"error"}` 事件下发、前端按 type 渲染失败态；同理会话忙用 409 在开流前就把并发挡住（会话锁），一请求一回合串行。

### Q24 ★ 为什么 run_sr 提交要做成幂等？「参数指纹 + 任务表」怎么防止重复上 Slurm？
**答（要点）**：run_sr 的副作用是**提交 GPU/Slurm 作业跑遥感超分**——真副作用、贵、且 Agent 循环可能崩溃重放/网络重试。若盲目重发，同一次作业被 sbatch 两遍是灾难。做法（[run_sr.py](backend/services/run_sr.py)）：

1. `task_fingerprint(params)` = 对参数排序后 JSON 做 sha256，**同参必同指纹**。
2. 提交前先查 `sr_tasks` 表：有历史 job_id 就 `squeue/sacct` 校准——作业还 active 或已 COMPLETED → 直接返回复用标记（RESUMED_ACTIVE/RESUMED_COMPLETED），**不再 sbatch**；终态失败/查无记录 → 带上前次失败信息重跑。
3. 关键纪律是「**先记意图、后记结果**」：写 intent 行（job_id 为空）在前，sbatch 在后，拿到 job_id 再回写；崩溃正好卡在两行之间时，重放会看到「有 intent 无 job_id」，判定为**结果未知、不盲重试**（先查调度器再决定），从根上杜绝同一作业被双提交。

**追问**：这跟 HTTP 幂等键（Idempotency-Key）是同一件事吗？—— 是同一思想的两种落点：HTTP 幂等键把去重键放请求头、服务端记忆；这里是**服务端按参数内容自算指纹** + 任务表复用，客户端甚至不用传 key——相同参数天然碰撞去重，且复用还带来了「失败可重跑」的语义。两者的共同前提都是：副作用要可定位（job_id / 结果落库），不能只靠「重试 = 安全」。

### Q25 ★ 仓库为什么工具结果统一约定 `{ok, data, error}`、HTTP 恒 200？和「错误码表」怎么分家？
**答（要点）**：每个工具（@tool 注册）的运行结果被约定成 `{"ok": bool, "data": …, "error": str|null}`（[contract.py](backend/tools/contract.py)），一次函数调用无论业务成不成功都是**一份正常返回**：`ok=false` 是业务结果（比如检索没命中、LLM 失败），不是传输事故。这套统一信封的好处：工具直调接口 `POST /api/tools/{name}` 的调用方（Agent 循环、前端试跑、另一工具）只需要 `if not ok: 看 error`，一个模式吃遍所有工具；而且它同时服务 LLM 的 function-calling 与 REST——同一份返回结构、同一份 params_schema，两种消费方零翻译。

- 分家规则（契约 §1）：**传输/框架层错误走 HTTP 码**——参数 JSON 都解析不了 → 400、未知工具/资源 → 404、并发冲突 → 409、生成/提交失败 → 422，统一 `{"detail":"人话"}`；**业务成败走 body 码**——`{ok,data,error}`。一句话：基础设施该懂的错误用 HTTP 码，业务调用方要判断的成败用业务码，两层不混。这也正是 Q21「两种风格怎么取舍」的工程答案。

**追问**：那 `{ok,data,error}` 里要再细分错误类型怎么办？—— 加 `error` 的消息约定人话、或扩一个 `code` 字段做枚举，前端不必解析 500 种人话；契约改字段要走「先文档、评审、再代码」流程（Q22），并有前后端两侧单测锁 schema。
