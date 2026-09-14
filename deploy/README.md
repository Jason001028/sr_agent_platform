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
聊天页、`/queue` 共享队列页，查看器点「提交 SR」→ 带入当前场景目录 → 跳 `/queue` 预填
（不自动提交）。离机验收走 mock LLM + 假调度器；**真机必须显式关 fake**（见下方 systemd env）。

> **SR 最小原型（09-14）改了三处**，详见 `docs/planning/sr-minimal-prototype-plan.md` 与 §7.6：
> ① 掩码不再由浏览器烘焙——`POST /api/masks` 保留但前端已不调用，改为用场景目录里**已有的**
> `<目录名>_mask.tif`；② 作业可**不经过 Slurm**（`SR_EXECUTOR=local`，sr-api 本机 `bash` 直跑，
> 单槽串行）；③ 浏览器端 JPG 导出 / FS Access「输出目录」授权整条链路已删除（掩码下载还在）。

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
   - `Environment=SR_AGENT_DB=` → SQLite 库（阶段5 起 chat 会话 + sr_tasks 同库；**父目录**须 `nginx` 可写）。
     ⚠️ 指向**应用解压根之外**的专用目录（模板已给 `/DiskArray/tmp/wangrz/sr_agent_db/`）——
     塞在解压根里时 nginx 写不动，库一被碰就 `unable to open database file`（2026-09-11 实机实测）；
   - `Environment=SR_LLM_MOCK=0` / `SR_SLURM_FAKE=0` → **真机显式关假实现**（service 已带默认，勿改成 1）；
   - Slurm 六项 env（`SR_PYTHON` / `SR_BUNDLE_DIR` / `SR_SLURM_WORK_DIR` / `SR_SLURM_PARTITION` / `SR_SLURM_TIME` / `SR_SLURM_CPUS`）→ 见 **§七 Slurm 接入**；缺任一项 SR 作业提交必失败；
   - 真 LLM 再配 `SR_LLM_BASE_URL/API_KEY/MODEL`（内网端点，见 service 注释）；不配则 loop 默认连外网端点（离机/MVP 用 mock，见 §四）；
   - `ExecStart=` 的 venv 路径若不同则改；用 conda 环境则填 `<conda>/envs/web-sr-agent/bin/uvicorn backend.api.app:create_app --factory --host 127.0.0.1 --port 8000`。

   > 权限：systemd 默认以 `nginx` 用户跑（`User=` 已设）。该用户需能**读**盘阵 TIF、
   > **写**预览 JPG 缓存（默认写源同目录 `<源>.preview.jpg`）、**写** `SR_AGENT_DB` 库。
   > 盘阵目录可写、所有组即可：`chgrp -R nginx <SR_SCENES_ROOT> && chmod -R g+rwX <SR_SCENES_ROOT>`；
   > 库目录单独给写权（**只给这一个目录，别 chown 整棵应用树**）：
   > `mkdir -p <SR_AGENT_DB 父目录> && chown nginx:nginx <SR_AGENT_DB 父目录> && chmod 750 <SR_AGENT_DB 父目录>`。

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

盘阵目录里**本来就是 JPG** 的影像（09-14 起）也作为场景行列出，行内标签显示「JPG 源」：
`hasPreview` 恒真、`jpgUrl` 指向源文件本身，点「打开」不经过懒生成（列表里 `W/H` 由 Pillow
读头得到）。后端自己烘焙的 `<basename>.preview.jpg` 缓存不会被当成场景列进去。

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

## 七、Slurm 接入（SR 作业提交链路）

> 链路：`/queue` 提交 → 后端写 config.xml + 批脚本 → `sbatch` → 作业在计算节点上跑 SR →
> 作业内校验器写退出码文件 → 后端读盘判终态 → SSE 推前端。
>
> 本文档位于 `deploy/`，本节引用的 `docs/...` 与 `SR_code/...` 都在**仓库**里，**不在离线包内**；
> 上车前从仓库另拷。分阶段验收命令（A 探针 / B 裸 Slurm 冒烟 / C 单场景真 SR / D 平台四条结论）
> 见 `docs/status/slurm-acceptance.md`；决策与实测背景见 `docs/status/slurm-integration.md`；
> 调用契约见 `docs/sr_code/sr-pipeline-interface.md` v1.5。

四个部件，缺一不可：

| 部件 | 位置 | 作用 |
| --- | --- | --- |
| 只读探针 | `deploy/slurm/probe_slurm.sh` | 上机第一步：核对分区 / GRES / 记账 / 路径 / 权限，**只读不改**。`--deep` 会真提交一个 2 分钟作业，**默认不开** |
| 部署变体 | `SR_code/variants/code_0817_prod_slurm.py` | 与生产脚本**并置**在 `$SR_BUNDLE_DIR` 下，由 `SR_SR_SCRIPT` 指定作业跑它（不必改名顶替，生产原文件保持字节不动）。重新生成：`python SR_code/tools/gen_slurm_variant.py`（`--check` 验新鲜度）；逐条差异见 `docs/sr_code/sr-slurm-deploy-variant.md` 的 E1–E9 |
| 契约校验器 | `SR_code/variants/verify_sr_run.py` | 作业内第二个进程：判「退出码 0 + SRLOG 末行 `Run finished.` + 输出 tif」三条件，写退出码文件，**契约不满足时以退出码 90 结束**（纯标准库、py3.6 兼容） |
| 平台侧代码 | `backend/services/run_sr.py`（生成批脚本 / 幂等指纹）+ `backend/services/slurm.py`（读退出码文件定终态） | 随 `backend/` 一起升（§5.2） |

### 7.1 装什么（两份脚本 + 九个 env）

脚本——`$BUNDLE` 即 `SR_BUNDLE_DIR`。**并置即可，不覆盖生产脚本**：

```bash
cd $BUNDLE
cp <上车目录>/code_0817_prod_slurm.py ./code_0817_prod_slurm.py   # 变体：与原脚本并排
cp <上车目录>/verify_sr_run.py        ./verify_sr_run.py
head -3 code_0817_prod_slurm.py                                  # ✓= "GENERATED FILE — DO NOT EDIT" 横幅
ls -l code_0817_prod.py                                          # ✓= 生产原脚本仍在，未被改动
```

> **为什么可以不顶替**：批脚本里那两个程序名是 `SR_SR_SCRIPT` / `SR_VERIFY_SCRIPT` 两个 env
> 决定的（`run_sr.py`），后端在生成批脚本时把名字写进文本。所以变体只要跟原脚本放在同一目录、
> 名字与 `SR_SR_SCRIPT` 对上就会被执行。好处是 `gen_slurm_variant.py` 的对照物（生产原文件）
> 始终字节不变，回滚只是把 `SR_SR_SCRIPT` 改回 `code_0817_prod.py` + restart。
>
> ⚠️ **没配 `SR_SR_SCRIPT` 就等于跑原脚本**，而原脚本有三处会咬人：`gpu_count != 4` 守卫在
> 单卡分配下恒真 ⇒ 作业 `exit(3)`；强行把 `CUDA_VISIBLE_DEVICES` 写成 `0` ⇒ 并发作业全挤 0 号
> 卡；失败时执行 `systemctl stop slurmd.service`（作业以 `User=nginx` 跑是权限拒绝，服务以 root
> 跑则**真把节点从调度池里摘掉**）。

env——`sr-api.service` 里**前八项**全部必填、第九项强烈建议配，node81-135 实测值（详见 service 内注释）：

| env | node81-135 实测值 | 说明 |
| --- | --- | --- |
| `SR_PYTHON` | `/run/media/root/SSD/program/anaconda/installed/envs/torch1.9.1py36/bin/python` | **SR 生产解释器**（py3.6 + torch + GDAL + ImgHistMatch.so），与平台 venv `/opt/sr-venv`（py3.9）平行、互不污染——后端不 import SR/torch/GDAL，只在批脚本里写一行解释器路径，该行在**计算节点**上解析。2026-09-14 实测：**torch 1.10.2+cu113 / GDAL 2.4.0**（目录名里的 1.9.1 ≠ 实际版本） |
| `SR_BUNDLE_DIR` | `/DiskArray/ProductionSchedule/exe_CentOS7/SR_bundle/mmsr_bundle/codes` | `code_0817_prod.py` / `models` / `utils` / `options` 所在目录；拼写以 `code_0817_prod.py:27` 的 `load_library()` 为准 |
| `SR_SLURM_WORK_DIR` | `/DiskArray/tmp/wangrz/sr_agent_work` | config.xml 与批脚本落盘处。**必须是共享盘**——`gpu` 分区横跨 **76** 个节点（`node81-*` 与 `node104-*` 两族，2026-09-11 探针实测），作业落在哪台由调度器决定，每一台都要能读它；默认值 `/tmp/sr_agent_work` 是本机路径，多节点下作业秒挂 |
| `SR_SLURM_PARTITION` | `gpu` | `sinfo -h -o "%P"` 实测（`centos7` / `deicc` / `gpu` / `gpu*` / `test`，星号=默认分区）。**没有 `gpup`** —— 早先文档那个值是转述错误；不配则走默认分区，多分区集群下不可控 |
| `SR_SLURM_TIME` | `02:00:00` | 作业时限（`#SBATCH --time`） |
| `SR_SLURM_CPUS` | `4` | `#SBATCH --cpus-per-task` |
| `SR_SLURM_NODELIST` | **待定（那一台 4 卡机的主机名）** | 白名单 → `#SBATCH --nodelist=`。2026-09-14 需求：**所有作业只跑在同一台 4×3090 物理机内、不调度到其他服务器**，Slurm 只做本机排队 + 单卡分配 —— 所以值是**单台**主机名，由 `docs/status/slurm-acceptance.md §B0` 的三条只读命令定出（`sinfo -N -o "%N %G %t"` 找 `gpu:4` 且非 `down` 的那台）。值是 Slurm host list，只允许字母/数字/`-`/`,`/`[`/`]`（非法值提交时报错）；不配 = 不加此行 = 调度器自选。**别填族白名单**（会把作业散到几十台机器上）。它是**硬**白名单：该机全忙时作业排队等待，不会溢出。若走「本机单节点 Slurm」方向则整项不需要 |
| `SR_SR_SCRIPT` | `code_0817_prod_slurm.py` | 作业跑哪个超分脚本（相对 `SR_BUNDLE_DIR`）。**不配 = 跑生产原脚本**，见上面的警告 |
| `SR_VERIFY_SCRIPT` | `verify_sr_run.py` | 作业内契约校验器（相对 `SR_BUNDLE_DIR`）。不配也会跑这个名字，显式写出来是为了可审计 |
| `SR_SANDBOX_ROOT` | `/DiskArray/tmp/wangrz/sr_sandbox` | **强烈建议配**：每个作业先在 `<根>/<任务指纹前12位>/` 下 `cp -a` 一份 `lq_path` 的私有副本，SR 全程只碰副本。不配 = SR 直接写 `lq_path`（阶段6 的生产形态）。详见 §7.5 |

改完 `systemctl daemon-reload && systemctl restart sr-api`（前六项由 `backend/config.sr_runtime()`、
后两项由 `run_sr.py` 在**调用时**读 env，不 restart 不生效；已经生成并提交的作业不受影响——
脚本名是**生成时**写进批脚本文本的，批脚本本身带 `--export=NONE`，进程环境不会传进作业）。

### 7.2 两个权限坑（都用 `sudo -u nginx` 验）

- 平台以 `User=nginx` 运行，它 `sbatch` 提交的作业**也以 nginx 身份在计算节点上执行**——不是 root；
- 因此 `SR_SLURM_WORK_DIR` 与 `SR_SANDBOX_ROOT` 都要 **nginx 可写**：

  ```bash
  sudo -u nginx touch <目录>/w && echo OK      # ✓= OK
  ```

  写不了 `SR_SLURM_WORK_DIR` 的直接后果：API 自己就写不出 config.xml/批脚本，提交必失败。
- **`lq_path` 只需 nginx 可读**（开沙箱时）——`Debug/` 与产物都落在副本里，校验器写退出码文件
  也写在副本。这正是沙箱的一个附带好处：不必为了让平台能跑，去给生产目录开写权限。
  ⚠️ 不开沙箱（`SR_SANDBOX_ROOT` 空）时反过来：`lq_path/<目录>/Debug/` **必须 nginx 可写**，
  否则校验器写不出退出码文件 → 该任务永远 `UNKNOWN`。

### 7.3 终态怎么判（**不要看 sacct**）

真机 `AccountingStorageType=accounting_storage/none`，`sacct` 恒不可用（探针输出里 `sacct` 两行
**FAIL 是预期结果**，不是故障）。终态由作业内校验器写盘、平台读盘：

```text
<DatarootLQ>/Debug/_SREXIT_<job_id>.txt        # job_id=… / sr_exit_code=… / verdict=0|90 / skip=0|1 / reason=…
```

映射：`verdict=0` → **COMPLETED**（含 `skip=1` 的云量跳过——合法跳过不是失败）；`verdict!=0` → **FAILED**。

关键点：**进程退出码 0 不代表成功**。有一批静默失败（输入 PAN 缺失、RC 步尺寸不符、config 缺失等）
同样是 `exit(0)`，且都发生在 SRLOG 建立之前——过去会被固化成 COMPLETED。退出码 90 就是为此引入的
（契约 §2.3）：*退出码 0，但契约不满足*。

### 7.4 就绪与否的一行判定

```bash
sh <APP>/deploy/slurm/probe_slurm.sh       # ✓= 结尾有 SUMMARY，除 sacct 两行外无 FAIL
```

探针只验「环境具备不具备」，不验链路。真正的验收按 `docs/status/slurm-acceptance.md` 分四阶段走：
A 探针 → B 裸 Slurm 冒烟（`--export=NONE` 会不会切断 `LD_LIBRARY_PATH` / GPU 分配形态 / 退出码文件
能不能落盘）→ C 单场景真 SR → D 平台链路四条结论（静默失败不再被标成成功 / 幂等回归 / 云量跳过 /
SSE）。

### 7.5 沙箱：为什么平台不能直接写 `lq_path`

**SR 对它的 `DatarootLQ` 不是只读的。** [`SR_code/util.py:1305`](../SR_code/util.py#L1305)
（`writeTiff`）在写产物之前，先把**输入**改名：

```python
os.rename(path + tiftype, path + "_NOSR" + tiftype)   # → <目录名>_NOSR.tif
```

把 `Suffix` 留空、`DeleteOriTifNeeded=False` 都躲不掉这一步——它跟这两个开关无关。所以
「用户在前端填了一个盘阵路径」的准确含义是**「那个目录会被改写」**。写生产目录 = 动生产数据，
且是不可逆的改名。

配了 `SR_SANDBOX_ROOT` 之后，作业的**第一步**是复制，SR 全程只碰副本：

```text
<SR_SANDBOX_ROOT>/<任务指纹前12位>/
└── <场景目录名>/      ← cp -a 自 lq_path，**目录名必须原样**
    ├── <目录名>.tif          ← SC 步的输入名由目录名推导：osp.basename(lq_path) + file_type
    ├── <目录名>.tif.ori / _NOSR.tif / _<suffix>.tif   ← SR 在这里改名与写产物
    └── Debug/               ← SRLOG + 退出码文件
```

**每次作业都重新复制**（先 `rm -rf` 再 `cp -a`），不做「已存在就跳过」的优化：同一组参数
失败后重跑时，源可能已经修好，跳过复制会让人对着旧副本排查——省几分钟，赔一下午。

三件事因此成立：**生产目录只读**；`exit_code_file_for()` 从 config.xml 的 `DatarootLQ`
（已指向副本）反推出同一个退出码文件路径，平台照旧能读到终态；`/api/queue` 每行多一个
`run_dataroot` 字段，说明产物到底落在哪儿（`params.lq_path` 是用户填的原路径，两者在开启沙箱时
不同）。

注意事项：

- **空间**：每任务一份完整副本。一个 ~5 GB 的场景目录，跑完约 15 GB（副本 5 + `_NOSR` 5 + 产物 5）。
  验收跑完手工清：`rm -rf <SR_SANDBOX_ROOT>/<指纹前12位>`；
- **权限**：`SR_SANDBOX_ROOT` 要 **nginx 可写**（作业以 nginx 身份跑），装法：

  ```bash
  mkdir -p /DiskArray/tmp/wangrz/sr_sandbox
  chown nginx:nginx /DiskArray/tmp/wangrz/sr_sandbox
  sudo -u nginx touch /DiskArray/tmp/wangrz/sr_sandbox/w && echo OK   # ✓= OK
  ```

- **值的格式**：必须是不含空格 / 引号 / `` ` `` / `..` 的绝对路径——它会拼进作业脚本里一行
  `rm -rf "<根>/<指纹>"`。非法值在**提交时**报 `ValueError`，不会静默降级；
- **时间**：复制发生在作业内，算进 `--time`（默认 2h）。大场景 + 慢盘时留意；
- **不配就是阶段 6 的生产形态**：SR 直接写 `lq_path`，输入被改名 `*_NOSR.tif`。
  这是上游 SR 的既有契约（生产管线本来就依赖这个改名），所以阶段 6 之后关掉沙箱是正常的，
  只是那一刻起，前端填什么路径就写什么路径。

### 7.6 最小原型：不走 Slurm，本机 conda 直接跑（`SR_EXECUTOR=local`）

最小原型（09-14，工作单 `docs/planning/sr-minimal-prototype-plan.md`）要的是「前端点一下 →
本机直接跑对应目录的掩码和 `.tif`」，不经过调度器。实现方式是把 `sbatch` 换成 `bash`：
`run_sr.build_batch_script()` 生成的批脚本本身是合法 bash（`#SBATCH` 行在 bash 眼里就是注释），
所以整条链路（config.xml → 作业内校验器 → 退出码文件 → 读盘定终态）一行不改地复用。

| env | 值（示例） | 说明 |
| --- | --- | --- |
| `SR_EXECUTOR` | `local` | `slurm`（默认）/ `local` 二选一。改完必须 `systemctl restart sr-api` |
| `SR_LOCKED_DIR` | 试验目录绝对路径 | **一旦设置，`POST /api/queue` 只接受这一个 `lq_path`**（两侧去尾部 `/` + `realpath` 后比较），不匹配返回 400；不设 = 保持旧行为（任意路径） |
| `SR_LOCAL_GPU` | `0` | 透传成子进程的 `CUDA_VISIBLE_DEVICES`（本机多卡时选哪张） |
| `SR_SUFFIX_DEFAULT` | `sr` | 前端不填 `suffix` 时的默认产物后缀。**不要留空**：空后缀会让产物名与输入同名 |

```ini
Environment=SR_EXECUTOR=local
Environment=SR_LOCKED_DIR=<试验目录>
Environment=SR_LOCAL_GPU=0
Environment=SR_SUFFIX_DEFAULT=sr
```

与 Slurm 模式的差异（`backend/services/local_exec.py`）：

- **单槽串行**：同一时刻只跑一个作业（模块级锁当槽位），后到的排队 `PENDING`，前一个结束才
  `RUNNING`。日志 `<SR_SLURM_WORK_DIR>/<批脚本名>.<job_id>.out`；
- **job_id**：自增整数，序号持久化在 `<SR_SLURM_WORK_DIR>/.local_job_seq`，sr-api 重启不重复
  （`_SREXIT_<job_id>.txt` 靠它不串号）。作业脚本里显式带 `--job-id <id>`，因此不依赖
  `$SLURM_JOB_ID`（本地模式没有这个变量）；
- **子进程环境**：从 sr-api 的环境复制后只改四件事——删 `VIRTUAL_ENV`/`PYTHONHOME`/`PYTHONPATH`
  （sr-api 跑在 `/opt/sr-venv` py3.9，别让这些变量漏进 conda py3.6）、`PATH` 前置
  `dirname(SR_PYTHON)`、设 `CUDA_VISIBLE_DEVICES`、设 `SR_EXECUTOR=local`；其余原样保留
  （**`LD_LIBRARY_PATH` 必须留**，torch/GDAL 靠它）；
- **终态判定不变**：仍然读 `<lq_path>/Debug/_SREXIT_<job_id>.txt`，`verdict=0` → COMPLETED、
  `90` → FAILED，**不看进程退出码**（§7.3 那批静默失败照旧）。读不到 = `UNKNOWN`；
- **cancel**：排队中的直接标记取消；已在跑的对整个进程组 `SIGTERM`（POSIX `killpg`；Windows 上
  退回 `taskkill /F /T`，否则只杀 bash 外壳、python 孙子进程会孤儿化并占着日志句柄）。

两条环境要求与 Slurm 模式不同：

1. **`SR_SANDBOX_ROOT` 必须不设**。沙箱会把 `DatarootLQ` 指到副本目录，产物就落在那儿，
   直接违背「输出与原图同目录」这条需求。本地模式的写入目标就是 `SR_LOCKED_DIR`，
   所以锁定目录本身应当是 `/DiskArray/tmp/wangrz/` 下的试验目录；
2. **`lq_path` 必须 nginx 可写**（没有沙箱这层缓冲了）：SR 会就地改名输入为 `*_NOSR.tif`
   并在同目录写产物与 `Debug/`。**每次真跑都要知道一次：这会覆盖该目录里已有的 `_NOSR.tif`。**

掩码规则：请求里不传 `mask_path` 时，后端取 `<lq_path>/<目录名>_mask.tif`；文件不存在直接 400
（不静默退化成全图超分）。前端「提交 SR」按钮只对**盘阵场景**（有 `lq_path` 的行）可用，
本地打开的文件没有目录语义，按钮禁用。

验收步骤见工作单 §5.2：A 段只验后端（curl 提交 → `GET /api/queue` 看 PENDING → RUNNING →
COMPLETED → 看 `Debug/_SREXIT_<job_id>.txt` 的 `verdict=0` → 确认产物落在锁定目录）；
B 段换前端点击。
