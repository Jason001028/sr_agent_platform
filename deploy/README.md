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

1. **装 Python 3.9+ 与 venv**（联网装一次）：

   ```bash
   yum install -y python3 python3-pip
   python3 -m venv /opt/sr-venv
   ```

2. **装运行依赖**（离线：同版本 wheel 拷 U 盘 / 内网 PyPI 均可）：

   ```bash
   /opt/sr-venv/bin/pip install -r /data/www/sr-agent-platform/requirements-api.txt
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
   - `ExecStart=` 的 venv 路径若不同则改。

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

## 五、升级 / 回滚

- **升级前端**：开发机重新 `npm run build && npm run package:offline` → 拷新包 → 解压覆盖
  `dist/` → `systemctl reload nginx`。带 hash 的资源名每次变化，immutable 缓存不卡旧版。
- **升级后端**：覆盖 `backend/` → `systemctl restart sr-api`（预览 JPG 缓存保留，无需重生成）。
- **回滚**：保留上一版目录，改 nginx.conf 的 `root` 指旧版 dist；后端 `git checkout` 旧版
  backend 覆盖后 `systemctl restart sr-api`。

## 六、硬性约束（移植期红线）

- **vendor 已打进 dist，全离线**：pako/utif(补丁版)/geotiff 均来自 `frontend/src/vendor/`，Vite 构建打进产物。
- **utif.js 为补丁版（cmpr 8/32946 走 pako inflate），绝不能被 npm 重装覆盖**——只能从 `src/vendor/utif.js` 本地引入。
- **盘阵双层保险**：nginx `alias` 整块暴露 + 后端按 `SR_SCENES_ROOT` 白名单校验（拒绝 `../` 穿越、白名单外绝对路径、fake 占位）。URL 全用相对场景根的 `/disk-array/<rel>`。
- **浏览器单次分配约 2GB、Canvas 面积上限 16384²**——盘阵场景因此由服务端烘焙 8192 JPG，浏览器只解码 JPG（远低于上限）。
- 真实大图只在有盘阵的内网机（外网开发机读不到），解码回归用 `.e2e/` 本机资产 + `frontend/fixtures/` 入库小图；真机验收项见 docs/status/current-question.md。
