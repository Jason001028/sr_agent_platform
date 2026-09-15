# 真机后端启动 + Windows 前端访问运行手册（node81-135）

> 日期：2026-09-05 · 状态：实测运行中 · 定位：把阶段4/5 后端 + 前端在**本机**（CentOS7 内网机
> node81-135）跑起来的具体操作步骤。
> 与通用文档的关系：这是 `deploy/README.md` §二/§三 在本机的**落地版**——deploy 里写的 `/data/www/...`
> 是示例路径，本机真实路径见下。涉及环境取舍的背景（conda 频道死、为何用 venv）见 memory
> `intranet-nexus-python-floor` 与 deploy/README §三。
> 图例：`⏱` 参考耗时 · `✓=` 本步成功标准 · 所有命令在 CentOS7 上**逐条贴**（别一次粘多行，会碎）。
> **ADHD 友好版**（一次一步、少讲为什么、带即时反馈）：`docs/status/real-machine-bringup-adhd.md`。
>
> **部署进度（2026-09-07/08 实测）**：§2 后端 + §3 nginx 均已跑通——Windows 浏览器开 `http://10.10.81.135` 可访问各页面（/viewer /scenes /chat /queue）。**遗留**：`SR_SCENES_ROOT` 默认值 `/data/scenes` 在本机**不存在** → `/api/scenes` 返回 `source:fake`（12 条 0B 占位），真实盘阵根未定位、提交 SR 端到端未验。真实根定位后按 §2.1 改 env、同步 §3.1 的 nginx `alias`（两处须同值）并重启，详见 §5 症状表「/api/scenes 返回空+fake」行与 `current-question.md` §6.0。
>
> **Slurm 侧（09-10 第三批）**：部署变体 + 作业内校验器 + 退出码文件定终态已备齐，**上机尚未执行**——
> 按 `docs/status/slurm-acceptance.md` 的 A→B→C→D 逐条跑，命令逐条贴、输出回传判读。注意两条与本文
> 其余部分不同的前提：① 作业以 **nginx** 身份在计算节点上跑（`User=nginx`）；② **终态不看 `sacct`**
> （本机账务关闭），看 `<lq_path>/Debug/_SREXIT_<job_id>.txt`。

**本机固定值速查**

| 变量 | 本机实测值 |
|---|---|
| 应用根 `<APP>` | `/run/media/root/SSD/workspace/wangrz/sr-agent-platform` |
| 平台 venv | `/opt/sr-venv`（python 3.9，实测依赖见 §1） |
| conda 根 | `/run/media/root/SSD/program/anaconda/installed` |
| 复用解释器 | `<conda>/envs/destriping_py39/bin/python`（py3.9，只借来建 venv，不装进它） |
| systemd 单元 | `/etc/systemd/system/sr-api.service` |
| nginx 站点 | `/etc/nginx/conf.d/sr-agent-platform.conf` |
| 盘阵根 `SR_SCENES_ROOT` | `/data/scenes`（默认值，真机数据就位后再核对，见 §5 症状表） |
| SQLite 库 | `<APP>/sr_agent.db`（父目录须 nginx 用户可写） |
| SR 沙箱根 `SR_SANDBOX_ROOT` | `/DiskArray/tmp/wangrz/sr_sandbox`（须 nginx 可写；不配 = SR 直接写 `lq_path`，见 deploy/README §7.5） |

---

## 0. 先确认 `<APP>` 的目录布局（决定下面源文件路径）

```bash
ls /run/media/root/SSD/workspace/wangrz/sr-agent-platform
```

**布局 A · 离线包解压（本文按此写）**——顶层直接是交付文件：

```
sr-agent-platform/
├── dist/                  # 前端构建产物（nginx root 指这里）
├── backend/               # FastAPI 代码（uvicorn WorkingDirectory 指这里，import backend 包）
├── nginx.conf             # → 拷到 /etc/nginx/conf.d/
├── sr-api.service         # → 拷到 /etc/systemd/system/
├── requirements-api.txt   # 后端依赖清单
└── README.md
```

**布局 B · git 克隆**（顶层有 `deploy/ docs/ frontend/` 之类）：上面 3 个交付文件在
`<APP>/deploy/` 下（`deploy/sr-api.service` 等），`dist` 需先 `cd frontend && npm run build`
再以 `<APP>/frontend/dist` 为准。下面步骤里凡写到 `<APP>/sr-api.service`、`<APP>/nginx.conf`、
`<APP>/requirements-api.txt` 的地方，布局 B 请换成 `<APP>/deploy/` 下同名文件、dist 换成
`<APP>/frontend/dist`。

> 不确定就 `ls` 看结果对号入座；本文其余路径（venv / systemd / nginx 目标位）两布局相同。

---

## 1. 前置：平台 python 环境（已就绪，留档备查）

`/opt/sr-venv` 已由 py3.9 解释器建好、依赖已装（09-05 实测）：fastapi 0.128.8 / openai 1.109.1 /
numpy 2.0.2 / pillow 11.3.0 —— 全部满足 requirements-api.txt 下限。重建/核对方法：

```bash
/opt/sr-venv/bin/python -c "import fastapi,openai,numpy,PIL; print(fastapi.__version__, openai.__version__, numpy.__version__, PIL.__version__)"
```

为什么不是 conda 环境：本机 conda 频道 `cgwx-anaconda` 404 已死、`defaults` 断网、包缓存不全 →
造不出新 conda env（`create` 报 HTTP 000 / `clone` 报 HTTPError / `clone --offline` 报 remote
error）。venv 只借 `destriping_py39` 的解释器二进制、不共享/不污染它的包，且 **`sr-api.service`
默认 `ExecStart` 就是 `/opt/sr-venv/bin/uvicorn`，零改动**。

> 与 SR 作业环境 `torch1.9.1py36`（py3.6）是两个平行世界：平台后端不 import SR/torch/GDAL，
> 只写批处理脚本 + `sbatch` 提交（批处理里那行解释器由 `SR_PYTHON` 决定，在作业节点侧解析，
> 平台 python 不进作业脚本）。`torch1.9.1py36` 原样保留，别删别 clone。

---

## 2. 起后端 sr-api（systemd）

### 2.1 拷单元 + 改 2 处路径（本机 ≠ deploy 示例值）

`sr-api.service` 里 `WorkingDirectory` / `SR_AGENT_DB` 出厂写的是 `/data/www/...`，本机是
workspace 路径，拷过去后**先全局替换再启动**（一条 sed，把 `/data/www/sr-agent-platform` 全部换成
`<APP>`，覆盖 WorkingDirectory 与 SR_AGENT_DB 两处）：

```bash
cp /run/media/root/SSD/workspace/wangrz/sr-agent-platform/sr-api.service /etc/systemd/system/
sed -i 's#/data/www/sr-agent-platform#/run/media/root/SSD/workspace/wangrz/sr-agent-platform#g' /etc/systemd/system/sr-api.service
grep -nE 'WorkingDirectory|SR_AGENT_DB' /etc/systemd/system/sr-api.service   # 确认两行都指向 <APP>
```

> 不用改的三样：`ExecStart`（已=`/opt/sr-venv/bin/uvicorn backend.api.app:create_app --factory
> --host 127.0.0.1 --port 8000`）、`SR_LLM_MOCK=0`、`SR_SLURM_FAKE=0`（真机必须保持 0，勿改成 1）。
> `SR_SCENES_ROOT=/data/scenes` 先保持默认，真机数据就位后按 §5 核对。

> **2026-09-11 修正（`SR_AGENT_DB` 这一处上面那条 sed 是错的）**：这条 sed 会把库一起指到
> `<APP>` 里，而 `<APP>` 属 root、`nginx` 写不动 —— 实机探针 `write-db FAIL`。库要单独放一个
> nginx 可写的目录，且**不要**为此把整棵应用树 `chown` 给 nginx。真机改法（drop-in，不动已部署
> 的单元正文）：`/etc/systemd/system/sr-api.service.d/20-agentdb.conf` 里
> `Environment=SR_AGENT_DB=/DiskArray/tmp/wangrz/sr_agent_db/sr_agent.db`，详见
> `slurm-acceptance.md §0.6`。本次部署**后补**上了这个 drop-in。

### 2.2 目录属主（nginx 用户要读 backend/dist；库父目录单独给写权）

```bash
chown -R nginx:nginx /run/media/root/SSD/workspace/wangrz/sr-agent-platform
```

> 会整棵树归 nginx；root 仍可读写、不影响 git。**更稳的做法是只给库目录写权**
> （`mkdir -p <库父目录> && chown nginx:nginx <库父目录> && chmod 750 <库父目录>`），
> backend/dist 保持默认即可读 —— 2026-09-11 教训：库塞在应用树里、又指望 nginx 写得动，
> 结果是第一笔队列提交就报 `unable to open database file`。

### 2.3 启动 + 探活

```bash
systemctl daemon-reload
systemctl enable --now sr-api
systemctl status sr-api                # ✓= active (running)
curl -s http://127.0.0.1:8000/api/health   # ✓= {"status":"ok",...}
```

### 2.4 起不来？

```bash
journalctl -u sr-api -n 50
```

报错一般直接点名：`WorkingDirectory … 不存在` → 2.1 的 sed 没跑对；`ModuleNotFoundError: backend`
→ WorkingDirectory 没指到 `<APP>`（uvicorn 把 WorkingDirectory 加进 sys.path 才能 import 包）；
端口占用 → `ss -ltnp | grep 8000`。

---

## 3. 起 nginx（前端静态 + `/api` 透传 + `/disk-array`）

nginx 是 Windows 侧唯一入口：静态 dist 直出、`/disk-array/` alias 盘阵、`/api/` 反代
`127.0.0.1:8000`（SSE 已禁缓冲）。uvicorn 只绑 127.0.0.1，**不要**暴露 8000。

> ⚠️ 前提：nginx 已装（`which nginx` 有输出）。09-05 实测本机**未装且无任何 yum 源**
> （`yum install` 报 `There are no enabled repos`）→ 先解决 nginx 安装（装法见 deploy/README
> §二 ⚠️；内网 yum 镜像或 U 盘 rpm）。没装前 `/etc/nginx` 不存在，下面 cp 会失败。

### 3.1 拷站点 + 改 root 一处（dist 在 workspace）

```bash
cp /run/media/root/SSD/workspace/wangrz/sr-agent-platform/nginx.conf /etc/nginx/conf.d/sr-agent-platform.conf
sed -i 's#/data/www/sr-agent-platform/dist#/run/media/root/SSD/workspace/wangrz/sr-agent-platform/dist#' /etc/nginx/conf.d/sr-agent-platform.conf
grep -n 'root' /etc/nginx/conf.d/sr-agent-platform.conf   # 确认 root = <APP>/dist
```

> `alias /data/scenes/` 与后端 `SR_SCENES_ROOT` 必须同值，先默认不动。若缺 `nginx -t` 所需目录，
> `mkdir -p <APP>/dist` 至少让 root 存在（正式 dist 缺失时首页会 404，先跑通再说）。

### 3.2 生效 + 本机验证

```bash
nginx -t                     # ✓= syntax is ok
systemctl reload nginx       # 或 nginx -s reload
ls /run/media/root/SSD/workspace/wangrz/sr-agent-platform/dist/index.html   # dist 真的在
curl -s http://127.0.0.1/ | head -5          # ✓= 出 index.html
curl -s http://127.0.0.1/api/health          # ✓= 经 nginx 反代也 {"status":"ok"}
```

---

## 4. Windows 侧访问（任选一条，取决于你能通到哪层）

### 4.A 浏览器所在 Windows 能直连局域网（真机验收那种 Win11 机）

放行 80 后，浏览器开 `http://<node81-135 的局域网 IP>/`。nginx `server_name _` 绑全部网卡，任意
可达 IP 都行：

```bash
firewall-cmd --permanent --add-service=http && firewall-cmd --reload
```

页面入口：`/scenes` 场景浏览 · `/chat` 平台聊天 · `/queue` 共享 SR 队列。F12 应只见本站请求
（`./assets/*`、`/api/*`、`/disk-array/*`），**无任何外网域名**；`/chat` 发一条、`/queue` 列表挂起
时能看到 SSE `text/event-stream` 逐帧到达（nginx 已 `proxy_buffering off`）。

### 4.B 只有 SSH 能到（像本会话这样）

Windows 端（PowerShell）单开一个终端挂 SSH 本地转发，**只转 80 就够**——/api、/disk-array、SSE
全被 nginx 反代包住，会从同一条隧道一起过：

```powershell
ssh -N -L 18080:127.0.0.1:80 root@<node81-135 IP>
```

然后浏览器开 `http://localhost:18080/`。挂着 -N 的终端别关；SSE 长连接走隧道无碍。

---

## 5. 验证闭环 & 常见症状表

| 症状 | 原因 / 处理 |
|---|---|
| `yum install` 报 `There are no enabled repos`（或 `/etc/nginx` 不存在） | 本机无任何 yum 源 → 内网 yum 镜像或 U 盘 rpm，见 deploy/README §二 ⚠️；别反复试 yum |
| `sr-api` 一直 `217/USER`，但 `sudo -u nginx /opt/sr-venv/bin/uvicorn --version` 却能跑 | unit 里 `User=` 值后带了**同行 `#` 注释**（systemd 不支持行尾注释，用户名被读成脏名）→ `sed -i '/^User=/s/#.*//' /etc/systemd/system/sr-api.service` 后 `daemon-reload && restart`（源文件已修，deploy/sr-api.service） |
| `sr-api` `status=1/FAILURE`，journal 报 `Unable to evaluate type annotation 'str \| None'` | venv 是 py3.9，fastapi/pydantic 解析不了 PEP604 `X \| None` 注解 → `/opt/sr-venv/bin/pip install eval-type-backport` 后 `systemctl restart sr-api`（py<3.10 必需，已进 requirements-api.txt） |
| `sr-api` 起来又立刻挂（`activating (auto-restart)`），`curl :8000/api/health` → `000`，`ss -ltnp \| grep :8000` 空 | 新拷进 `<APP>/backend` 的文件属主是 **root**，而服务以 `User=nginx` 跑 → import 阶段就挂：`journalctl -u sr-api -n 25` 末尾是 `PermissionError: [Errno 13] Permission denied: '<APP>/backend/api/app.py'`。处置 `chown -R nginx:nginx <APP>/backend` → `systemctl restart sr-api`；**别回滚代码**，也别被 `is-active` 恰好打印的 `active` 骗了，判定看 `health=200`（2026-09-15 实测） |
| `systemctl cat` 里有某条 `Environment=SR_*`，但 `systemctl show -p Environment` 里没有 | drop-in 文件缺 `[Service]` 段头 → systemd **静默忽略整个文件**（不报错、不警告，`cat` 照样打印那几行）。补上段头 → `daemon-reload && restart`；判定只看 `systemctl show`。2026-09-15 实测机上遗留的 `override.conf` 就是这样，`SR_EXECUTOR=local` 从未生效（本该本机直跑，实际会去 `sbatch`）→ 详见 deploy/README §5.3.1 |
| `/api/scenes` 返回空 + fake 提示 | `SR_SCENES_ROOT` 没指到真实盘阵根 → 改 service 该 env 并同步 nginx `alias`（必须同值）→ `systemctl daemon-reload && systemctl restart sr-api` |
| 首页 404 / 无页面 | dist 缺失或 `root` 没改对 → 3.1 的 grep 复查；`ls <APP>/dist/index.html` |
| `/api/*` 返回 502 | 后端没起或崩了 → `systemctl status sr-api` + `journalctl -u sr-api -n 50` |
| 静态文件 403 / Failed to open file | CentOS7 SELinux 拦 nginx 读 `/run/media` → `chcon -Rt httpd_sys_content_t <APP>/dist`；反代连不上再 `setsebool -P httpd_can_network_connect 1` |
| 80 打不开 | 防火墙未放行 → `firewall-cmd --permanent --add-service=http && firewall-cmd --reload`；或 nginx 没 reload |
| 首次开大图很慢 | 属正常：后端懒生成 8192 JPG，几十秒，Network 里能看到 `/api/scenes/<id>/preview`；此后秒开（已落盘 + 浏览器缓存） |
| 探针输出里 `sacct` / `sacct-parse` 两行 **FAIL** | **预期结果，不是故障**：本机 `AccountingStorageType=accounting_storage/none`（账务关闭），`sacct` 恒返回非 0 且无输出 → 作业终态改读**退出码文件**，见 `deploy/README.md` §7.3 与 `current-question.md` §3.2「Slurm 接入定论」 |
| 队列任务**永远 UNKNOWN**（作业明明跑完了） | 平台反推的退出码文件路径与作业写的对不上，或校验器根本写不出文件。逐层查：① `ls <lq_path>/Debug/_SREXIT_<job_id>.txt` 存不存在——不存在看作业 `.err` 有没有 `verify_sr_run: 无法写退出码文件`（作业以 **nginx** 身份跑，`<lq_path>/Debug/` 要 nginx 可写，`sudo -u nginx touch <lq_path>/Debug/w` 直接验）；② 存在但平台仍 UNKNOWN → 核对 config.xml 的 `DatarootLQ` 与作业实际工作目录是否同值（开沙箱时 `DatarootLQ` 是副本路径，`/api/queue` 的 `run_dataroot` 字段就是它）；③ 配置文件名带任务指纹，同 suffix 的不同任务已不会互相覆盖（2026-09-10 前是手写 `run_sr_<suffix>.xml`，会串；老任务若仍 UNKNOWN 请重提） |
| 作业秒挂 / 立刻失败 | `SR_SLURM_WORK_DIR` 不是**共享盘**：config.xml 与批脚本由 API 写在这个目录，作业却落到 gpu 分区的 76 个节点之一上读它。默认 `/tmp/sr_agent_work` 是本机路径 → 改到 `/DiskArray/...` 这类共享挂载（`srun -w node81-140 ls -d <WORK>` 直接验） |
| 作业 `exit(3)`（GPU 守卫） | 两种可能：① `$SR_BUNDLE_DIR/code_0817_prod.py` **还是原脚本**（没装部署变体）——`head -3` 应见 `GENERATED FILE — DO NOT EDIT` 横幅，装法见 `deploy/README.md` §7.1；② 变体已装但 `--gres` 没给到卡——查作业 `.out` 审计段的 `CUDA_VISIBLE_DEVICES=` 是否为空 |
| 作业 `.err` 报 `ImportError: libXXX.so` / `OSError` | 批脚本的 `#SBATCH --export=NONE` 把提交端环境（含 `LD_LIBRARY_PATH`）一起丢了，torch1.9.1 / GDAL 链不上系统库 → `backend/services/run_sr.py` 里把它改成 `#SBATCH --export=ALL`（唯一一处），重跑 `pytest backend/tests/test_run_sr.py` 后拷 `backend/` 到 `<APP>` 并 `systemctl restart sr-api`（上机必验项 V1） |
| 平台显示 **FAILED** 但作业退出码是 0 | 这是**预期的新行为**：契约不满足（缺 SRLOG 或末行不是 `Run finished.` 或缺输出 tif）时校验器以**退出码 90** 结束。看退出码文件的 `reason` 字段点名缺哪条——这正是以前被固化成「成功」的那批静默失败 |
| 跑完 SR 后，填的那个盘阵目录里输入被改名成 `*_NOSR.tif` | **不开沙箱时的预期行为**，不是 bug：`util.writeTiff` 写产物前必先改名输入（跟 `Suffix` / `DeleteOriTifNeeded` 都无关）。要生产目录只读就配 `SR_SANDBOX_ROOT`（deploy/README §7.5）：每个作业先 `cp -a` 一份副本，产物落在 `<根>/<task_fingerprint 前12位>/<目录名>/`，`/api/queue` 的 `run_dataroot` 字段指出确切位置 |
| 提交报 `SR_SANDBOX_ROOT ... rejected` | 沙箱根含空格 / 引号 / `` ` `` / `..` —— 它会拼进作业脚本里的 `rm -rf`，被白名单挡下。改成纯 `/A-Za-z0-9._-/` 的绝对路径再重启 sr-api |
| 云量超阈值的任务显示 COMPLETED 且没有输出 tif | **不是 bug**：合法跳过（`Run skipped:` 终态行 + 退出码文件 `skip=1`），契约只要求 SRLOG 存在，不要求输出 tif。若它显示 FAILED，说明 `$SR_BUNDLE_DIR/verify_sr_run.py` 不是本批版本 |

浏览器侧闭环：`/scenes` 打开场景出图 · 画掩码点「提交 SR」跳 `/queue` 预填 · 提交后状态徽标随
SSE 推进。详细验收项以 `docs/status/current-question.md` §6 为准，跑完把每项记录誊回并勾掉。

**Slurm 作业链路**（提交 → 作业内契约校验器 → 退出码文件 → 平台判终态）的上机操作与分阶段验收
（A 探针 / B 裸 Slurm 冒烟 / C 单场景真 SR / D 四条链路结论）见 **`docs/status/slurm-acceptance.md`**；
部署侧（变体安装位置 + 六项 env + 两个权限坑 + 终态判定）见 **`deploy/README.md` §七「Slurm 接入」**。

---

## 6. 停 / 重启 / 升级

```bash
systemctl restart sr-api       # 后端重启（预览 JPG 缓存保留，无需重新生成）
systemctl stop sr-api          # 停后端（nginx 仍在，/api 会 502，属预期）
systemctl reload nginx         # 前端热更（仅动 dist 时）
```

升级：新包覆盖 `<APP>/dist` → `systemctl reload nginx`（前端）；覆盖 `<APP>/backend` →
`systemctl restart sr-api`（后端）。回滚与更多细节见 `deploy/README.md` §五。

---

## 附：环境拓扑一图流

```
Windows（浏览器 / 或 SSH 隧道）
   │  :80（唯一入口）
   ▼
nginx（:80）─── 静态 dist 直出
   ├── /disk-array/  →  alias 盘阵根（= SR_SCENES_ROOT）
   └── /api/         →  反代 127.0.0.1:8000（SSE 禁缓冲）
                          │
                          ▼
                 uvicorn sr-api（User=nginx，/opt/sr-venv）
                          ├── SR_SCENES_ROOT 读盘阵 + 写预览 JPG
                          ├── SR_AGENT_DB 写 SQLite
                          └── sbatch 提交 SR 作业 → torch1.9.1py36（独立，不碰平台 python）
```
