# ✅ 傻瓜版部署单 —— 把后端和前端跑起来（ADHD 友好 · node81-135）

> 日期：2026-09-05 · 状态：实测可用 · 定位：给 ADHD 大脑的**操作单**。一次只看一步，
> 一个步骤 = 一个命令框 + 一个「你该看到」。全程约 20 分钟。
> 想多懂点"为什么"→ 看 `docs/status/real-machine-bringup.md`（本文只是它的动作版）。
> 卡住就把**报错最后几行**发我，别自己硬扛。

**三条铁律（违反就乱）**
1. 每个代码框是**一整条**，整条复制、一次粘贴。**永远不要**一次粘好几行。
2. 每个框下面的「你该看到」没出现 = 出事了，去文末「卡住速查」，别往下走。
3. 命令里的路径**写死了本机实测值，不要改**。

**这条路上唯一记住的地址**

```
/run/media/root/SSD/workspace/wangrz/sr-agent-platform
```

叫它「包目录」。后面命令全是它。

---

- [ ] 第 0 步 · 找对目录（10 秒）
- [ ] 第 1 步 · 装后端服务（3 分钟）
- [ ] 第 2 步 · 给权限 + 启动后端（1 分钟）
- [ ] 第 3 步 · 装前端入口 nginx（3 分钟）
- [ ] 第 4 步 · Windows 浏览器打开（5 分钟）
- [ ] 🎉 搞定

> 任何时候想停都可以：**第 2 步做完**是安全存档点（后端已注册成服务，重启机器它自己会起）。

---

### 第 0 步 · 找对目录（10 秒）

前提：你已经 ssh 到了内网 CentOS 机器（提示符长这样 `(base)[root@node81-135 ~]#`）。还没上去就先上去。

```bash
ls /run/media/root/SSD/workspace/wangrz/sr-agent-platform
```

**你该看到**：一大串文件名。

- **眼尖找 `sr-api.service`** → 看到了？直接去第 1 步。
- 没看到它、但看到一堆 `deploy docs frontend backend` 文件夹 → 你手里是 **git 拷贝**，先跳到文末「附 B」把那 3 个路径换成 `deploy/` 再回来。

---

### 第 1 步 · 装后端服务（3 分钟）

**框 1** —— 把服务文件放进系统目录：

```bash
cp /run/media/root/SSD/workspace/wangrz/sr-agent-platform/sr-api.service /etc/systemd/system/
```

**你该看到**：没报错，回到提示符。

**框 2** —— 把文件里的旧路径改成你这台机器的（出厂写的是别人的路径，不改成启动会挂）：

```bash
sed -i 's#/data/www/sr-agent-platform#/run/media/root/SSD/workspace/wangrz/sr-agent-platform#g' /etc/systemd/system/sr-api.service
```

**你该看到**：没报错。

**框 3** —— 核对改对没有：

```bash
grep WorkingDirectory /etc/systemd/system/sr-api.service
```

**你该看到**：一行 `WorkingDirectory=` 开头、**中间有 `/run/media/`** 的路径。有 `/run/media/` 就对了；还是 `/data/www` 就重跑框 2。

---

### 第 2 步 · 给权限 + 启动后端（1 分钟）

**框 1** —— 让 nginx 用户能读能写 + 注册 + 启动（一整条）：

```bash
chown -R nginx:nginx /run/media/root/SSD/workspace/wangrz/sr-agent-platform && systemctl daemon-reload && systemctl enable --now sr-api
```

**你该看到**：没报错。（要等一两秒是正常的。）

**框 2** —— 确认它活着：

```bash
systemctl status sr-api
```

**你该看到**：绿色 **active (running)**。不是？→ 卡住速查 ①。

**框 3** —— 后端自己说句话：

```bash
curl -s http://127.0.0.1:8000/api/health
```

**你该看到**：`{"status":"ok",...}` 开头的一串。看到 = 后端活了。

> ☕ **安全存档点**：到这儿累了就停。后端已经是系统服务，重启机器它自己会起来，不会丢。
> 精神好就继续，只剩 2 步。

---

### 第 3 步 · 装前端入口 nginx（3 分钟）

nginx 是唯一大门：你 Windows 浏览器打它，它把后端"翻译"给你。后端本身只听本机，别动它。

**⚠️ 前提**：先敲 `which nginx`。有输出 = 已装，往下走。没输出/报错 = 这台机**还没装 nginx**（缺 yum 源），**停下来**，找对接的人要装法，别硬跑。

**框 1** —— 把站点配置放进去：

```bash
cp /run/media/root/SSD/workspace/wangrz/sr-agent-platform/nginx.conf /etc/nginx/conf.d/sr-agent-platform.conf
```

**框 2** —— 同样改一处旧路径（前端文件在包目录的 dist 里）：

```bash
sed -i 's#/data/www/sr-agent-platform/dist#/run/media/root/SSD/workspace/wangrz/sr-agent-platform/dist#' /etc/nginx/conf.d/sr-agent-platform.conf
```

**框 3** —— 语法检查 + 生效：

```bash
nginx -t && systemctl reload nginx
```

**你该看到**：`nginx -t` 打印 **syntax is ok**。（若报错缺目录，先 `mkdir -p /run/media/root/SSD/workspace/wangrz/sr-agent-platform/dist` 再跑一次。）

**框 4** —— 前后端一起验：

```bash
curl -s http://127.0.0.1/ | head -5
```

**你该看到**：`<!DOCTYPE html>` 之类（出网页了）。

```bash
curl -s http://127.0.0.1/api/health
```

**你该看到**：`{"status":"ok",...}` —— 恭喜，机器上**全部通了**。剩最后一件事：在 Windows 里打开它。

---

### 第 4 步 · Windows 浏览器打开（5 分钟）

**先搞清楚：你从 Windows 能怎么碰到这台 Linux？**

**情况 B（推荐，最省心）** —— 你像现在这样只能通过 ssh 连它。在 **Windows 开一个 PowerShell 窗口**，敲（注意是 `-N -L`，端口 18080）：

```powershell
ssh -N -L 18080:127.0.0.1:80 root@<Linux 的 IP>
```

它会**卡住不退出——这是对的，别关这个窗口**。再开一个浏览器，地址栏敲：

```
http://localhost:18080/
```

**情况 A** —— 你的 Windows 和这台 Linux 在同一个网络、能直接访问它。那不用隧道，浏览器直接敲：

```
http://<Linux 的 IP>/
```

不知道 IP？回 Linux 终端跑 `ip addr | grep inet`，找 `192.168.x.x` 或 `10.x.x.x` 那种。打不开就先把防火墙打开（回 Linux 敲）：

```bash
firewall-cmd --permanent --add-service=http && firewall-cmd --reload
```

**什么算成功**：首页能开 → 地址栏加 `/scenes`、`/chat`、`/queue` 都能出页面。出页面了就是你干的，收工。🎉

---

### 验收三连（可选，1 分钟）

1. `/scenes` 里有没有场景行？有 = 数据通了；**空 + 提示 fake** = 盘阵根没对上，找我看一眼，一条命令的事。
2. 打开 F12 → Network：刷新，应**只有本站请求**，没有任何外网域名。
3. 浏览器里后端是不是真干活了：随便哪个页面能加载、不报错。

---

## 卡住速查（只记三条）

| 症状 | 动作 |
|---|---|
| ① 服务没起来 / active 变红 | `journalctl -u sr-api -n 30` → 把**最后几行**发我。多半是第 1 步路径没改对（回看第 1 步框 3 的 grep） |
| ② 页面 /api 显示 502 | 后端没跑。`systemctl status sr-api` 看它是不是停了，`systemctl restart sr-api` 拉起来 |
| ③ /scenes 空 + fake 提示 | 盘阵根还没对上，发我一句，我给你改好 |
| ④ sr-api 报 `217/USER`，但 `sudo -u nginx /opt/sr-venv/bin/uvicorn --version` 能跑 | unit 里 `User=` 那行**带同行注释**把用户名读脏了 → 敲 `sed -i '/^User=/s/#.*//' /etc/systemd/system/sr-api.service`，再 `systemctl daemon-reload && systemctl restart sr-api` |
| ⑤ 启动报 `Unable to evaluate type annotation 'str \| None'` | venv 是 py3.9，代码用了新式 `X \| None` 注解 → 敲 `/opt/sr-venv/bin/pip install eval-type-backport`，再 `systemctl restart sr-api` |

---

## 附 B · 你是 git 拷贝（第 0 步没看到 sr-api.service）

不用慌，只把 3 个路径前面加 `deploy/`。替换规则：

| 原来（包目录版） | git 拷贝版 |
|---|---|
| `包目录/sr-api.service` | `包目录/deploy/sr-api.service` |
| `包目录/nginx.conf` | `包目录/deploy/nginx.conf` |
| 前端 dist：`包目录/dist` | 需先 `cd frontend && npm run build`，之后用 `包目录/frontend/dist` |

把第 1 步框 1、第 3 步框 1 的命令里的路径照上表改掉，其余一字不动。改完从第 1 步开始。
