# sr_agent_platform 离线部署（阶段2 前端 + 阶段4 盘阵场景 API + 阶段5 平台 API）

> 产物自包含、全离线，无任何 CDN/外网依赖。本包 = `dist/`（前端产物）+ `nginx.conf`
> + `backend/`（FastAPI 平台 API）+ `sr-api.service`（systemd）+ `requirements-api.txt`
> + 本说明。开发机（Windows，外网）打包 → 拷到内网机（CentOS7）解压 → nginx + FastAPI 托管。

阶段4 数据路径（09-02 决策）：浏览器**不再读盘阵原始 TIF**。盘阵场景由后端懒生成
「稀疏采样 + 2% 线性拉伸」的 8192 长边灰度 JPG，nginx 整块静态直出；检索走 FastAPI。
本地文件路径（选择 TIF…）保持原有稀疏 TIF 读法，零回归。

阶段5 平台 API（09-02 定稿，契约 = `docs/planning/api-contract.md`）：FastAPI 在既有场景
端点上新增 `/api/chat/*`（会话 REST + 单回合 SSE）、`/api/queue*`（共享 SR 队列 REST + SSE
状态广播）、`/api/tools`（工具直调）、`/api/masks`（掩码烘焙到原图目录）；前端新增 `/chat`
聊天页、`/queue` 共享队列页，查看器画完掩码点「提交 SR」→ 后端落盘掩码 → 跳 `/queue` 预填
（不自动提交）。离机验收走 mock LLM + 假调度器；**真机必须显式关 fake**（见下方 systemd env）。

## 一、开发机打包

```bash
cd frontend
npm run build            # 产出 dist/（全离线，含 vue/router/pinia/vendor 三库）
npm run package:offline  # 产出 release/sr-agent-platform-<日期>-<版本>.tar.gz
```

产物结构：

```
sr-agent-platform/
├── dist/                  # ← 前端产物（Vite base:'./'，相对路径引用，随便放哪都行）
├── backend/               # ← FastAPI 场景 API（含 api/ + services/，已去 pycache/测试）
├── nginx.conf             # → /etc/nginx/conf.d/
├── sr-api.service         # → /etc/systemd/system/
├── requirements-api.txt   # 后端运行依赖清单
└── README.md              # 本说明
```

## 二、内网机部署：前端 + 盘阵静态（nginx，CentOS7）

1. **装 nginx**（EPEL 源，联网装一次即可，后续部署不再需要外网）：

   ```bash
   yum install -y epel-release && yum install -y nginx
   systemctl enable nginx
   ```

   > ⚠️ 部分内网机**没有任何启用的 yum 源**——`yum install` 报 `There are no enabled repos`
   > （09-05 node81-135 实测），EPEL 装不了，**nginx 用户也不存在**（连带 `sr-api.service` 的
   > `User=nginx` 起不来）。此时先找内网 yum 镜像：`ls /etc/yum.repos.d/` + 探 nexus 仓库
   > `curl -s http://nexus.jl1.cn/service/rest/v1/repositories | grep -o '"name":"[^"]*"'`
   > （看有无 centos/epel 代理仓库）配好 repo；或外网机下 EPEL `nginx-1.20.x.el7.x86_64.rpm`
   > + 依赖（gperftools-libs 等）U 盘拷入 `rpm -ivh`。**别默认 EPEL 可达。**

2. **拷包并解压**（U 盘 / scp 均可）：

   ```bash
   mkdir -p /data/www
   tar xzf sr-agent-platform-*.tar.gz -C /data/www
   # 得到 /data/www/sr-agent-platform/{dist,backend,nginx.conf,...}
   ```

3. **放 nginx 配置并生效**：

   ```bash
   cp /data/www/sr-agent-platform/nginx.conf /etc/nginx/conf.d/sr-agent-platform.conf
   nginx -t                       # 语法检查通过再 reload
   systemctl reload nginx         # 或 nginx -s reload
   ```

   > nginx.conf 里三处按真机收紧：`root`（dist 目录）、`alias /data/scenes/`
   > （盘阵根，与后端 `SR_SCENES_ROOT` 必须一致）、`proxy_pass 127.0.0.1:8000`
   > （FastAPI 监听，勿改端口否则同步改 systemd）。

4. **防火墙放行 80**（按内网实际策略，通常不拦内网）：

   ```bash
   firewall-cmd --permanent --add-service=http && firewall-cmd --reload
   ```

## 三、内网机部署：FastAPI 场景 API（阶段4）

1. **Python ≥ 3.8**（⚠️ CentOS7 自带 `python3` = **3.6.8，不满足**）：后端依赖整链要求 ≥3.8，
   在 3.6 上 `pip install -r` 只会解析到 2021 旧版——`openai 0.10.5` 无 1.x `OpenAI()` 客户端、
   agent 代码跑不了；`fastapi 0.11x` 时代 / `numpy≤1.19.5` / `pillow≤8.4` / `uvicorn≤0.17`。
   镜像本身有新版（实测 py3.10 从同一 nexus 源装到过 numpy 1.26.4），pip 显示旧版是**按解释器
   版本过滤**，别据此降代码。

   **落地方案（09-05 真机实测）**：`sr-api.service` 的 `ExecStart` 默认就是 `/opt/sr-venv/bin/uvicorn`，
   所以拿一个**现成的 py3.8+ 解释器**建出这个 venv 即可，依赖全走 cgwx-pypi（pip 代理）。先认清本机
   conda 现状，别在死频道上耗——内网 nexus 的 **conda 频道 `cgwx-anaconda` 404 已废**、`defaults` 断网
   （`conda create` 触网报 HTTP 000；`--clone` 报 HTTPError；加 `--offline` 报 remote error）；若包缓存也
   不全（`ls <conda>/pkgs | grep -E '^python-3\.9'` 为空）则**本机造不出新 conda 环境**。因此：

   ```bash
   conda env list    # 找一个现成 py3.8+ 环境（本机实测：destriping_py39 = python 3.9）
   <conda>/envs/<py3.8+ 环境>/bin/python -m venv /opt/sr-venv   # 只借解释器二进制，不碰源环境任何包
   ```

   无现成 ≥3.8 解释器时再向 IT 索取 python3.9+ rpm；若执意要真 conda 环境，只能在**有活频道或有完整
   conda 包缓存**的机器上建好（`conda create -n web-sr-agent python=3.9`）再拷入，目标机不合适。

2. **装运行依赖**（内网机从 nexus 镜像装；无镜像时用同版本 wheel 拷 U 盘）：

   ```bash
   /opt/sr-venv/bin/pip install -i http://nexus.jl1.cn/repository/cgwx-pypi/simple --trusted-host nexus.jl1.cn \
       -r /data/www/sr-agent-platform/requirements-api.txt
   ```

3. **放 systemd 单元**，按真机核对下面几处后启动：

   ```bash
   cp /data/www/sr-agent-platform/sr-api.service /etc/systemd/system/
   ```

   - `WorkingDirectory=` → 解压根（默认 `/data/www/sr-agent-platform`，保证能 import `backend` 包）；
   - `Environment=SR_SCENES_ROOT=` → 盘阵根（**必填**，须与 nginx `alias` 同值）；
   - `Environment=SR_AGENT_DB=` → SQLite 库（阶段5 起 chat 会话 + sr_tasks 同库；父目录须 `nginx` 可写）；
   - `Environment=SR_LLM_MOCK=0` / `SR_SLURM_FAKE=0` → **真机显式关假实现**（service 已带默认，勿改成 1）；
   - 真 LLM 再配 `SR_LLM_BASE_URL/API_KEY/MODEL`（内网端点，见 service 注释）；不配则 loop 默认连外网端点（离机/MVP 用 mock，见 §四）；
   - `ExecStart=` 的 venv 路径若不同则改；用 conda 环境则填 `<conda>/envs/web-sr-agent/bin/uvicorn backend.api.app:create_app --factory --host 127.0.0.1 --port 8000`。

   > 权限：systemd 默认以 `nginx` 用户跑（`User=` 已设）。该用户需能**读**盘阵 TIF、
   > **写**预览 JPG 缓存（默认写源同目录 `<源>.preview.jpg`）、**写** `SR_AGENT_DB` 库。
   > 盘阵目录可写、所有组即可：`chgrp -R nginx <SR_SCENES_ROOT> && chmod -R g+rwX <SR_SCENES_ROOT>`；
   > 库目录单独给写权：`mkdir -p <SR_AGENT_DB 父目录> && chown -R nginx:nginx <SR_AGENT_DB 父目录>`。

   ```bash
   systemctl daemon-reload
   systemctl enable --now sr-api
   journalctl -u sr-api -f        # 查启动日志；首启无报错即就绪
   ```

## 四、验证

```bash
# 前端
curl -s http://127.0.0.1/ | head -5                            # index.html
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1/viewer   # 200（SPA fallback）

# FastAPI（health / 场景列表）
curl -s http://127.0.0.1/api/health                            # {"status":"ok",...}
curl -s 'http://127.0.0.1/api/scenes?limit=5' | head -c 400    # 场景 JSON（含 W/H/jpgUrl）

# 盘阵静态直出（取列表里第一行 rel 拼 /disk-array/<rel>）
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1/disk-array/<某个rel>  # 200
```

浏览器打开 `http://<内网机IP>/scenes` → 过滤/点「打开」→ 场景图出在查看器，文件列表项带
「盘阵」标记、拉伸下拉禁用（提示"盘阵 JPG 已烘焙 2% 线性拉伸"）。首次打开会懒生成预览
（大图几十秒，进度在 Network 里能看到 `/api/scenes/.../preview`），此后秒开（JPG 已落盘 +
浏览器缓存）。F12 Network 里应只有本站请求（`./assets/*`、`/api/*`、`/disk-array/*`），
**没有任何外网域名**。

### 阶段5 平台 API（聊天 / 队列 / 掩码）

> 真 LLM / 真 Slurm 就绪前，可先用假实现验链路（与开发机离机验收同基准）：把 service 的
> `SR_LLM_MOCK` / `SR_SLURM_FAKE` 临时置 `1` → `systemctl restart sr-api` → 验完改回 `0`。

```bash
# 工具清单 / 队列列表
curl -s http://127.0.0.1/api/tools | head -c 300            # {"tools":[…]}
curl -s http://127.0.0.1/api/queue | head -c 400            # {"tasks":[…]}

# 聊天：建会话 → 发消息看 SSE 帧（mock=1 固定先 search_scenes 再回最终回复）
curl -s -X POST http://127.0.0.1/api/chat/sessions          # 201 {"session_id":…}
curl -s -N -X POST http://127.0.0.1/api/chat/sessions/<id>/messages \
     -H 'content-type: application/json' -d '{"content":"看看盘阵上有什么"}'
     # 逐帧 data: {"type":"turn_start"|"tool_call"|"tool_result"|"assistant"|"turn_done",…}

# 队列 SSE：挂起看 job_update（后台校准器广播）
curl -s -N http://127.0.0.1/api/queue/events
```

浏览器：`http://<内网机IP>/chat` 发一条 → 工具行 ✓ + 最终回复（SSE 逐帧渲染），刷新可恢复
历史；`/queue`「提交 SR 作业」手填 `lq_path` → 提交 → 状态徽标随 SSE 从 提交中/排队/运行中
推进到「完成」（假调度器几百 ms；真 Slurm 为真实 squeue/sacct）；查看器画完掩码点「提交 SR」
→ 自动跳 `/queue` 表单预填掩码与原图目录 → 检查后点「提交到 Slurm」。F12 Network 里
`/api/chat/.../messages` 与 `/api/queue/events` 是 **SSE 长连接**（`text/event-stream`），nginx 已
`proxy_buffering off`，应看到事件逐帧到达而非攒批。

## 五、升级 / 重部署 / 回滚

> 核心一句话：**改了哪一层就只动哪一层**。前端改动不需要重启后端，后端改动不需要重打前端。
> 服务器路径用 `<APP>` 代指解压根：本文示例 `/data/www/sr-agent-platform`；真机 node81-135 的
> 实际值是 `/run/media/root/SSD/workspace/wangrz/sr-agent-platform`（本机全部固定值速查见
> `docs/status/real-machine-bringup.md` 开头表，命令结构不变、把 `<APP>` 换成真值即可）。
>
> ⚠️ **真机路径 ≠ 仓库示例路径——拷配置先换路径，否则静态页全 500**。`nginx.conf` / `sr-api.service`
> 里写的 `/data/www/...` 是**示例**；真机实际值见 `docs/status/real-machine-bringup.md` 开头速查表
> （node81-135 = `/run/media/root/SSD/workspace/wangrz/...`）。**照拷不换的病征**（2026-09-08 实测踩中）：
> 后端 `/api/*` 全 200、前端首页/各路由**全 500**（错误页带 `nginx/x.y.z`），`/var/log/nginx/error.log`
> 刷 `rewrite or internal redirection cycle while internally redirecting to "/index.html"`——这是 SPA
> `try_files` **死循环**，根源 = conf 的 `root` 还指着不存在的示例目录、读不到 `index.html`，**不是后端坏**。
> 处置：把 `root`（及 `alias`）换成真机路径 → `nginx -t && nginx -s reload` → 复测（见下方冒烟两条）。
> 同理 `sr-api.service` 的 `WorkingDirectory`/`ExecStart` 不换真值 = 服务起不来或 import 崩。

### 判定：这次改了什么，就做哪几节

| 改了哪里 | 开发机产物 | 服务器要动 | 用不到的 |
| --- | --- | --- | --- |
| 前端（`frontend/` 下 .vue/.ts/css） | `frontend/dist/`（重打） | reload nginx | restart sr-api、daemon-reload |
| 后端（`backend/` 下 .py） | 拷 `backend/*` | restart sr-api | reload nginx、重打前端 |
| systemd 环境变量 / 端口 / ExecStart（改 `sr-api.service`） | 拷 unit 文件 | daemon-reload + restart sr-api | 碰前端 |
| nginx 站点（`nginx.conf`：root/alias/proxy 等） | 拷站点文件 | nginx -t + reload nginx | 碰后端 |

> 盘阵根两处必须同值：`sr-api.service` 的 `SR_SCENES_ROOT` 与 `nginx.conf` 的 `alias`。只改其中一处
> 会出现「列表有但图读不出来」或反过来，改完两边各自 reload/restart 一次。

**改完无论哪层，先本地冒烟两条，别直接开浏览器**（哪条非 200 对症看对应日志）：

```bash
curl -s -o /dev/null -w '静态=%{http_code}\n' http://127.0.0.1/           # 前端页：应 200
curl -s -o /dev/null -w '健康=%{http_code}\n' http://127.0.0.1/api/health # 后端：应 200
```

静态非 200 → 看 `/var/log/nginx/error.log`（改 nginx/dist 后）；健康非 200 → 看
`journalctl -u sr-api`（改 backend/sr-api.service 后）。

### 5.1 前端（Vue）改动 → 重打 dist → reload nginx

只在开发机打包，产物就一个目录 `frontend/dist/`，后端进程全程不用动。

开发机（Windows，git-bash）：

```bash
export PATH="/c/Users/lenovo/AppData/Local/nvm/v20.19.5:$PATH"   # node 在 nvm20，默认 PATH 里没有
cd frontend
npm run build                      # 产出 frontend/dist/
```

把产物拷到服务器并覆盖 `<APP>/dist/`（scp 目录内容，不是拷 dist 目录本身）：

```bash
scp -r frontend/dist/* root@<内网机IP>:<APP>/dist/
```

服务器上两步收尾：

```bash
chown -R nginx:nginx <APP>/dist    # nginx 用户要能读到新文件（此前整树 chown 过可省）
systemctl reload nginx             # 前端热更：只 reload，别 restart sr-api
```

浏览器 **Ctrl+F5 强刷**一次（去掉浏览器缓存的旧页面）。带 hash 的资源名每次变化，
immutable 缓存不卡旧版。判定：刷新后页面出现本次改动。

> 只覆盖 conf `root` 指着的那个 `<APP>/dist`，**别新建/挪目录**——root 与文件一错位就是
> 上面那个静态 500。真机 node81-135 的应用根在移动盘 `/run/media/root/SSD/...`：机器重启/
> 重新插拔后挂载点一变，`nginx root`、`sr-api.service` 的 `WorkingDirectory` 会整片失效，同样
> 症状复现。**真机重启后先确认 SSD 挂载与这些绝对路径还活着，再谈更新。**

### 5.2 后端（`backend/` 代码）改动 → restart sr-api

```bash
# 开发机：
scp -r backend/* root@<内网机IP>:<APP>/backend/
```

```bash
# 服务器：
chown -R nginx:nginx <APP>/backend
systemctl restart sr-api           # 预览 JPG 缓存保留，重启不会触发重生成
systemctl status sr-api            # 判定： active (running)
curl -s http://127.0.0.1:8000/api/health   # 判定： {"status":"ok",...}
```

> 改了后端但没加新依赖时，只用这一节。若 `requirements-api.txt` 也变（新增/升版本包），
> 需先在服务器按 §三.2 重新 `pip install -r` 再 restart，否则 import 阶段就崩。

### 5.3 配置层（systemd / nginx 站点）改动

改了 `sr-api.service`（env 或端口）——本地改 `deploy/sr-api.service` 后拷到服务器，注意路径按
真机替换：

```bash
cp deploy/sr-api.service /etc/systemd/system/   # 开发机 scp 亦可
systemctl daemon-reload          # 改了 unit 文件必须 daemon-reload，restart 不读新 unit
systemctl restart sr-api
```

改了 `nginx.conf`（root/alias/proxy 等）——本地改 `deploy/nginx.conf` 后拷到服务器：

```bash
nginx -t                          # 语法不过会拒绝 reload，先过这关
systemctl reload nginx
```

### 5.4 回滚

- **前端**：把上一版 `dist` 覆盖回来（或保留旧目录、把站点 `root` 指回旧 dist）→ `systemctl reload nginx`。
- **后端**：恢复上一版 `backend/`（git 检出旧提交再拷）→ `systemctl restart sr-api`。
- 预览 JPG 缓存随源图目录存、跨回滚保留，无需重生成。

### 5.5 走完整离线包发布时的等价动作

上面是日常迭代（开发机直连 scp）。若按 §一 `npm run package:offline` 打 tar.gz 发布，
则升级 = 拷新包解压覆盖 `<APP>/` 下对应目录，前端 `systemctl reload nginx`、后端
`systemctl restart sr-api`，动作同 §5.1/5.2，只是传输介质从 scp 换成整包。

### 5.6 整包拷贝时代的操作纪律（2026-09-09 实测教训）

如果你每次更新是**整份拷贝 `sr-agent-platform/` 文件夹**（U 盘/rsync 整包），先分清包里的两类文件，
处置完全不同：

| 文件 | 性质 | 每次更新 |
| --- | --- | --- |
| `dist/`、`backend/` | **代码产物**，里面没有本机路径 | **直接覆盖即可** |
| `nginx.conf`、`sr-api.service` | **机器配置模板**，里面是出厂示例路径 `/data/www/...` | **别 cp 回 `/etc`**，除非真改了配置逻辑 |

**为什么**：这两份配置在部署时被手工 sed 成真机路径（`root`/`WorkingDirectory`/venv 等）。它们只以
`/etc/nginx/conf.d/`、`/etc/systemd/system/` 里的那份为准；包里的同名文件**永远停留在出厂示例值**。
整包覆盖后再把模板 cp 回 `/etc` = 把真路径盖回 `/data/www` → 静态页全 500（nginx 找不到
`index.html`、try_files 死循环）、或后端起不来（`status=200/CHDIR`）。§五顶部 ⚠️ 即此症状。

**正确姿势**：更新只覆盖 `dist/` + `backend/` → `chown nginx:nginx` → reload/restart（§5.1/5.2）。
`/etc` 那两份配置只在**配置内容真变了**才动，动完必须带路径替换。

**如果已经误覆盖**（把出厂模板 cp 进 `/etc` 了），一次性救回——把两份配置里的示例路径改回真值：

```bash
# node81-135 真路径；其它机器把 APP 换成自己的
APP=/run/media/root/SSD/workspace/wangrz/sr-agent-platform

# nginx：root（及 alias 如需要）改回真路径
sed -i "s#/data/www/sr-agent-platform/dist#$APP/dist#g" /etc/nginx/conf.d/sr-agent-platform.conf
nginx -t && systemctl reload nginx
curl -s -o /dev/null -w '静态=%{http_code}\n' http://127.0.0.1/        # 应 200

# systemd：WorkingDirectory / SR_AGENT_DB 改回真路径
sed -i "s#/data/www/sr-agent-platform#$APP#g" /etc/systemd/system/sr-api.service
systemctl daemon-reload && systemctl restart sr-api
curl -s -o /dev/null -w '健康=%{http_code}\n' http://127.0.0.1:8000/api/health   # 应 200
```

> `sed` 幂等：路径已是真值时执行无副作用，可以无脑补跑。
> 更稳的做法：把上面救回逻辑存成盒上脚本 `/root/sr-conf-fix.sh`，整包拷贝后顺手跑一次，把
> `/etc` 两份里残留的 `/data/www` 一律改回真路径，杜绝再次带病上线。

## 六、硬性约束（移植期红线）

- **vendor 已打进 dist，全离线**：pako/utif(补丁版)/geotiff 均来自 `frontend/src/vendor/`，Vite 构建打进产物。
- **utif.js 为补丁版（cmpr 8/32946 走 pako inflate），绝不能被 npm 重装覆盖**——只能从 `src/vendor/utif.js` 本地引入。
- **盘阵双层保险**：nginx `alias` 整块暴露 + 后端按 `SR_SCENES_ROOT` 白名单校验（拒绝 `../` 穿越、白名单外绝对路径、fake 占位）。URL 全用相对场景根的 `/disk-array/<rel>`。
- **浏览器单次分配约 2GB、Canvas 面积上限 16384²**——盘阵场景因此由服务端烘焙 8192 JPG，浏览器只解码 JPG（远低于上限）。
- 真实大图只在有盘阵的内网机（外网开发机读不到），解码回归用 `.e2e/` 本机资产 + `frontend/fixtures/` 入库小图；真机验收项见 docs/status/current-question.md。
