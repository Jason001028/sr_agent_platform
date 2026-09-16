# Slurm 真机接入 · 分阶段验收清单（node81-135）

> 日期：2026-09-10 · 状态：**已中止（2026-09-14）** —— 路线改走「后端本机 conda 直跑」，见 [sr-minimal-prototype-plan.md](../planning/sr-minimal-prototype-plan.md)（判据：node81-135 在集群里是 `gpu:4 down`，DOWN 节点不会被分配作业；自建单节点 Slurm 的 6818 端口已被 `slurmd` 占用；单卡分配用 `CUDA_VISIBLE_DEVICES` 即可）。**本清单保留为重启 Slurm 时的执行页**，B/C/D 的判据与命令未失效。读者：运维本人 + 接手 Agent 窗口
> 前置：[docs/status/slurm-integration.md](slurm-integration.md) §一 决策快照 / §二 已核实事实 / §2.7 P1 实测值；[sr-pipeline-interface.md](../sr_code/sr-pipeline-interface.md) v1.5（调用契约）
> 定位：本文件是 P3 的**执行页**——A 探针 → B 裸 Slurm 冒烟 → C 单场景真 SR → D 平台链路。
> 图例：`⏱` 参考耗时 · `✓=` 成功判据 · `record:` 要记下来回贴的值 · `✗` 失败怎么办。
> 命令在 node81-135 上**逐条贴**（别一次粘多行）。D 阶段四条结论必须有真实输出为证才算验收通过。

## 0. 出发前（A/B/C/D 的共同前置）

### 0.0 先把真机固定值设成 shell 变量（**每个新终端都先贴这一段**）

下面的命令全部用这几个变量，不要再手敲绝对路径。

```bash
APP=/run/media/root/SSD/workspace/wangrz/sr-agent-platform
BUNDLE=/DiskArray/ProductionSchedule/exe_CentOS7/SR_bundle/mmsr_bundle/codes
SR_PYTHON=/run/media/root/SSD/program/anaconda/installed/envs/torch1.9.1py36/bin/python
WORK=/DiskArray/tmp/wangrz/sr_agent_work
OPT=/DiskArray/tmp/wangrz/sr_utils/espan3_2026_gf04_tile500.yml
TEST=/DiskArray/tmp/wangrz/sr_test          # 手工验证用的场景副本根（§C）
SANDBOX=/DiskArray/tmp/wangrz/sr_sandbox    # 平台沙箱：每个作业的私有副本（§D）
PART=gpu                                   # 2026-09-11 sinfo 实测：centos7/deicc/gpu/gpu*/test（无 gpup）
NODELIST='node81-[129-162,165-183,185-189]'   # ⚠️ 旧口径（族白名单）。新需求=单机，§B0 定出机器名后改成那一台
export APP BUNDLE SR_PYTHON WORK OPT TEST PART NODELIST

echo "$APP"; echo "$SR_PYTHON"    # ✓= 两个空变量说明没贴全
```

出处：`$BUNDLE/$SR_PYTHON/$WORK/$PART` 见 `deploy/sr-api.service` 的 `Environment=`；
`$OPT` 见 `backend/config.SR_DEFAULT_OPTIONS_YML`；`$TEST` 是本清单新建的验收沙箱（**不碰生产数据**）。

### 0.1 上车：两个文件从开发机拷到内网

```text
SR_code/variants/code_0817_prod_slurm.py   部署变体
SR_code/variants/verify_sr_run.py          作业内契约校验器
```

U 盘或 scp 均可；本清单文件本身不用上车。

### 0.2 安装位置与命名（**关键**，装错这一步后面全错）

平台生成的批脚本（`backend/services/run_sr.build_batch_script`）在 `$BUNDLE` 里按**固定文件名**调用两个程序：

```bash
cd $BUNDLE || exit 1
$SR_PYTHON code_0817_prod.py -f <cfg.xml>                                  # ← 名字写死
$SR_PYTHON verify_sr_run.py --config <cfg.xml> --sr-exit-code "$_sr_rc"    # ← 名字写死
```

所以部署动作 = 把这两个文件拷进 `$BUNDLE`、名字保持原样（**生产原脚本一字不动**）：

```bash
cd $BUNDLE
cp /path/to/code_0817_prod_slurm.py ./code_0817_prod_slurm.py   # 变体：与生产脚本并排，不覆盖
cp /path/to/verify_sr_run.py        ./verify_sr_run.py
head -12 code_0817_prod_slurm.py                                # ✓= "GENERATED FILE — DO NOT EDIT" 横幅
ls -l code_0817_prod.py                                         # ✓= 生产原脚本仍在、未被改动
```

`record:` `ls -l $BUNDLE/code_0817_prod*` 与变体首行横幅

> **不必改名顶替**：批脚本里那两个程序名由 `SR_SR_SCRIPT` / `SR_VERIFY_SCRIPT` 决定（`run_sr.py`），
> 后端生成批脚本时把名字写进文本。所以变体只要与生产脚本同目录、名字与 `SR_SR_SCRIPT` 对上就会被执行，
> 而 `gen_slurm_variant.py` 的对照物（生产原文件）始终保持字节不变，回滚 = 改回 env + restart。
>
> ⚠️ **`SR_SR_SCRIPT` 不配就等于跑原脚本**，后果：`gpu_count != 4` 守卫在单卡分配下恒真 → 作业
> `exit(3)`；强行把 `CUDA_VISIBLE_DEVICES` 写成 `0` → 并发作业全挤 0 号卡；失败时执行
> `systemctl stop slurmd.service`（root 提交时**真把节点从池里摘掉**）。
> A/B 阶段可以先不装，**进入 D 之前必须装完并复核横幅**。

### 0.3 service env 与重启

env 分三处：主文件（`SR_SCENES_ROOT` / `SR_LLM_MOCK` / `SR_SLURM_FAKE`）
+ drop-in `/etc/systemd/system/sr-api.service.d/10-slurm.conf`（Slurm/沙箱九项）
+ drop-in `20-agentdb.conf`（`SR_AGENT_DB`，库移出应用树；见 §0.6）。
查 **systemd 实际读到的合并文本**（`systemctl cat` 按加载顺序拼接，`# 路径` 标明来源）：

```bash
systemctl cat sr-api | grep -nE '^Environment=(SR_BUNDLE_DIR|SR_PYTHON|SR_SLURM_WORK_DIR|SR_SLURM_PARTITION|SR_SLURM_TIME|SR_SLURM_CPUS|SR_SR_SCRIPT|SR_VERIFY_SCRIPT|SR_SANDBOX_ROOT|SR_SLURM_FAKE)'
```

✓= 九项都在（`SR_SLURM_FAKE=0` 也确认一下）；`SR_SLURM_WORK_DIR` 指向共享盘；
`SR_SR_SCRIPT` 指向 `code_0817_prod_slurm.py`（不配就会跑原脚本，见 §0.2）；
`SR_SANDBOX_ROOT` 指向大盘（不配 = SR 直接写 `lq_path`，见 `deploy/README.md` §7.5）

> ⚠️ **别用 `systemctl show -p Environment | tr ' ' '\n' | grep '^Environment='` 数条数。**
> `show` 只在**第一个**值前打印 `Environment=`，其余是裸的 `KEY=VAL` 空格分隔 —— 那个管道恒得 1 条，
> 会让人误判成「env 没生效」白查半天（2026-09-10 实际踩过）。要看合并结果就 `systemctl show -p Environment`
> 看**原始**输出（一行列全），或用上面的 `systemctl cat`。

判据最终以**运行中进程**的环境为准 —— `daemon-reload` 只重读 unit 配置，**不改已跑进程的 env**：

```bash
systemctl daemon-reload && systemctl restart sr-api
sleep 2
tr '\0' '\n' < /proc/$(systemctl show sr-api -p MainPID | cut -d= -f2)/environ | grep '^SR_' | sort
# ↑ 预期 13 行 SR_。systemd 219 无 `--value`，只能用 cut 取 MainPID。
```

```bash
mkdir -p $WORK && chown nginx:nginx $WORK
mkdir -p $SANDBOX && chown nginx:nginx $SANDBOX     # 沙箱根，同样要 nginx 可写
systemctl status sr-api --no-pager | head -5      # ✓= active (running)
```

✗ 少 env → 按 `deploy/sr-api.service` 注释补；改了 unit 没 `daemon-reload` 则不生效。
✗ `SR_SLURM_FAKE=1` → D 阶段全程走内存假调度器，**四条结论全部作废**——先确认它是 0。

### 0.4 待核项（`slurm-integration.md §2.7` 的「待核」行 + 两处上机复核）

```bash
# (1) bundle 目录拼写 + 入口脚本真正 import 的三样
#     以源码为准：code_0817_prod.py:27 load_library() 给目录拼写；
#     :15-17 三行 import 给依赖清单（util.py 在 utils 包**里**，不是顶层文件）
ls -d $BUNDLE && ls $BUNDLE/code_0817_prod.py $BUNDLE/models $BUNDLE/options $BUNDLE/utils/util.py

# (2) SR_PYTHON 里 torch / GDAL 齐不齐（是 torch1.9.1py36，不是登录 shell 的 base 环境）
#     ⚠️ GDAL 用 VersionInfo()：py3.6 时代的 osgeo 绑定**没有** gdal.__version__，
#     上一版这条命令因此抛 AttributeError（2026-09-11 实测），会被误读成「环境坏了」。
$SR_PYTHON -c "import torch; from osgeo import gdal; print('torch', torch.__version__, '| gdal', gdal.VersionInfo())"

# (3) 共享盘在**每一类**计算节点上都可见（gpu 分区横跨 node81-* 与 node104-* 两族，
#     作业落在哪族由调度器决定，两族都得探）
N81=$(sinfo -h -p $PART -N -o "%N %t" | awk '$2=="idle" && $1 ~ /^node81-/  {print $1; exit}')
N04=$(sinfo -h -p $PART -N -o "%N %t" | awk '$2=="idle" && $1 ~ /^node104-/{print $1; exit}')
echo "picked: node81=$N81  node104=$N04"
for n in $N81 $N04; do
  srun -N1 -n1 -w "$n" sh -c "hostname; ls -d $WORK $BUNDLE" ; echo "rc=$? node=$n"
done
```

`record:` 三条的实际输出；核完把 §2.7 对应行的「待核」改成「已核」

✗ (1) 拼写不符 → 改 `deploy/sr-api.service` 的 `SR_BUNDLE_DIR`（源码优先，先 `ls` 看清实际拼写）。
✗ (2) import 失败 → 换解释器前**先确认**新环境里 torch/GDAL/`ImgHistMatch.so` 齐（见 service 注释）。
✗ (3) 某节点看不到 `$WORK` → `SR_SLURM_WORK_DIR` 不是共享盘，作业会秒挂。

> **2026-09-11 修正**：原文的 (1) 要求 `$BUNDLE/util.py`、(3) 钉死 `node81-129/140`，两处都与实测不符。
> (1) 是 `probe_slurm.sh` 的检查清单写错了（它照抄了仓库 `SR_code/util.py` 的**扁平**布局，而真机 bundle 是
> `mmsr_bundle/codes/utils/util.py` 的**包**布局）——`bundle FAIL missing util.py` 是假 FAIL；
> (3) 当时以为集群是 12 节点，实测 `gpu` 分区有 **76** 个节点、横跨 `node81-*` 与 `node104-*` 两族（见 §2.7）。
> 探针已改，但**不在本轮上机**（改的是我们自己的工具，不值得为它单独跑一趟 U 盘），先按上面的 (1) 用 `ls` 直接核。

### 0.5 沙箱目录

```bash
mkdir -p $TEST && chown -R nginx:nginx $TEST
sudo -u nginx touch $TEST/w && echo "nginx-can-write-OK"
```

**为什么是 nginx**：`sr-api` 以 `User=nginx` 运行，它 `sbatch` 提交的作业**也以 nginx 身份在计算节点上执行**。
所以本清单里「作业用户可写」= **nginx 可写**，不是 root。

### 0.6 库目录（`write-db FAIL` 的修法，D 阶段前必做）

2026-09-11 探针实测：`PROBE: write-db FAIL nginx CANNOT write /run/media/root/SSD/workspace/wangrz/sr-agent-platform`。
**这是真问题**（同一次运行里 `write-work OK` 走的是同一条 `sudo -n -u nginx test -w` 分支，说明前者的
FAIL 不是 `sudo` 假阴性）：平台的 `SR_AGENT_DB` 落在应用解压根里，而解压根属 root，nginx 写不动。
[`store.py:88-99`](../../backend/services/store.py#L88-L99) 是懒打开——首次用到就 `makedirs(parent)` →
`connect` → `executescript(_SCHEMA)`（建表 = 写事务 = 要在**父目录**落 journal），所以目录不可写时
**第一笔** `/api/queue` 提交、第一条 chat 会话就报 `unable to open database file`，poller 每 2 秒还会再炸一次。

修法（**不动已部署的单元正文**，用 drop-in 覆盖；回滚 = 删这一个文件）：

```bash
APP=/run/media/root/SSD/workspace/wangrz/sr-agent-platform   # 应用树（属 root）
DBDIR=/DiskArray/tmp/wangrz/sr_agent_db                      # 专用库目录（与 $WORK/$SANDBOX 同族）

# ① 先取证：老库在不在、有没有数据（有数据才需要搬）
ls -l $APP/sr_agent.db 2>/dev/null || echo "OLD DB: none"

# ② 建目录并只给它写权（不要 chown 整棵应用树）
mkdir -p $DBDIR && chown nginx:nginx $DBDIR && chmod 750 $DBDIR && ls -ld $DBDIR

# ③ drop-in 覆盖（同变量多处赋值时 drop-in 胜出，因为它在主文件之后解析）
mkdir -p /etc/systemd/system/sr-api.service.d
cat > /etc/systemd/system/sr-api.service.d/20-agentdb.conf <<'EOF'
[Service]
Environment=SR_AGENT_DB=/DiskArray/tmp/wangrz/sr_agent_db/sr_agent.db
EOF
systemctl daemon-reload
systemctl cat sr-api | grep -n 'SR_AGENT_DB'      # ✓= 两行（主文件旧值 + drop-in 新值）

# ④ 老库有就搬（保留已有会话/任务）
[ -f $APP/sr_agent.db ] && cp -p $APP/sr_agent.db $DBDIR/sr_agent.db && chown nginx:nginx $DBDIR/sr_agent.db && echo migrated || echo "no old db"

# ⑤ 重启，并以**运行中进程的 env** 为准核对（daemon-reload 不改已跑进程）
systemctl restart sr-api; sleep 2
PID=$(systemctl show sr-api -p MainPID | cut -d= -f2)
tr '\0' '\n' < /proc/$PID/environ | grep '^SR_AGENT_DB'
```

`record:` ①④⑤ 的输出；✓= ⑤ 打印的是新路径

✗ ⑤ 仍打印旧路径 → drop-in 没被读到（`systemctl cat` 里没有那行 / 文件名写错）→ 回头核 ③。
✗ `nginx` 建库仍失败 → 该目录所在文件系统的 SELinux 语境或挂载不允许写；换本地盘目录（如 `/var/lib/sr-agent-platform`）。

> 两处**故意不做**的事：不把库留在应用树里（那是 root 的树，给 nginx 写权等于把代码目录交给服务进程）；
> 不为了绕过权限而 `chown -R nginx` 整棵应用树（`real-machine-bringup.md §2.2` 的老建议，已被本节取代）。
> NFS 上的 SQLite 只允许单主机单进程写：本平台只有 node81-135 跑 `sr-api`，成立；日后多机要换存储。

---

## A 探针（只读，先跑；⏱ 2 分钟）

```bash
sh $APP/deploy/slurm/probe_slurm.sh
```

✓= 结尾 `OK=… WARN=… FAIL=…`，FAIL 逐条有 HINTS 指向处理章节

> `sacct` / `sacct-parse` 两行 **FAIL 是预期结果**（`Slurm accounting storage is disabled`），不是故障——
> 账务关闭正是本方案的前提，终态改由退出码文件判定，见 §一「作业终态判定」。

✗ `client-bin FAIL` → Slurm 客户端没装，后面全免谈（H-BIN）。
✗ `scontrol-ping FAIL` / `munge-auth FAIL` → 集群本身不通，先修 H-PING / H-MUNGE。
✗ `write-work FAIL`（nginx 不能写 `$WORK`）→ `chown nginx:nginx $WORK`；这是 D 阶段必踩的坑。
✗ `write-db FAIL`（nginx 不能写库父目录）→ 按 **§0.6** 把库移出应用树；不改则 D 阶段队列/会话全废。
✗ `bundle FAIL missing util.py` → **假 FAIL**（探针检查清单写错，见 §0.4 的 2026-09-11 修正），
改用 §0.4(1) 的 `ls $BUNDLE/utils/util.py` 直接核。

**GPU 分配形态（可选项，默认不跑）**——`--deep` 会**真的提交一个 2 分钟作业**，只在复核 E3 兜底或 B2 有疑问时才跑：

```bash
sh $APP/deploy/slurm/probe_slurm.sh --deep     # ⚠️ 默认不跑
```

`record:` 两种模式的完整输出都贴回（SUMMARY + HINTS 段必须有）

### A 记录（2026-09-11 探针 + 09-14 env/节点/提交三查）

**探针**（`probe_slurm.sh` @ `fb1cce1e…`，真机）：`OK=19 WARN=3 FAIL=5`。逐条判读：

- **预期非 OK**（不算故障）：`sacct` / `sacct-parse` FAIL（账务关闭）、`assoc` / `assoc-nginx` WARN（同因）、
  `write-lq` FAIL + `write-lq-debug` WARN（`SR_SCENES_ROOT=/data/scenes` 在本机不存在 = 遗留一）。
- `bundle FAIL missing util.py` → **假 FAIL**，探针自己的检查清单写错（§0.4 修正）。
- `write-db FAIL` → **真问题**，修法见 §0.6。
- 集群：`gpu` 分区 76 节点、`sinfo -N` 96 行、控制器 node81-190、`node81-133/134/135/136` 为 `down`（本机 `down*`）。

**env**（`systemctl cat sr-api`，13 行 `Environment=`，逐项对 §0.1 的九项）：

| env | 机器值 | 判 |
|---|---|---|
| `SR_BUNDLE_DIR` | `/DiskArray/ProductionSchedule/exe_CentOS7/SR_bundle/mmsr_bundle/codes` | ✓ `ls` 见 `code_0817_prod.py` / `models/` / `options/` / `utils/util.py` |
| `SR_PYTHON` | `…/envs/torch1.9.1py36/bin/python` | ✓ 路径在，实测 **torch 1.10.2+cu113 / GDAL 2.4.0** |
| `SR_VERIFY_SCRIPT` | `verify_sr_run.py` | ✓ |
| `SR_SR_SCRIPT` | **未设置** | ✗ **必须补，见下 ①** |
| `SR_SLURM_WORK_DIR` | `/DiskArray/tmp/wangrz/sr_agent_work` | ✓ node81-129 上 `test -d` = `WORK_OK` |
| `SR_SLURM_PARTITION` / `_TIME` / `_CPUS` | `gpu` / `02:00:00` / `4` | ✓ |
| `SR_SANDBOX_ROOT` | `/DiskArray/tmp/wangrz/sr_sandbox` | ✓ 已配（不是空的） |
| `SR_SCENES_ROOT` | `/data/scenes` | ✗ 本机不存在（遗留一，进 C/D 前必须定） |
| `SR_AGENT_DB` | `…/sr-agent-platform/sr_agent.db` | ✗ nginx 写不动 → §0.6 |
| `SR_LLM_MOCK` / `SR_SLURM_FAKE` | `0` / `0` | ✓ |
| `SR_SCRIPT_PATH` | `/DiskArray/prod_slurm.py` | ⚠️ 死配置（见下 ②） |

**两个必须处理的问题**：

① **`SR_SR_SCRIPT` 没设 → 作业会去跑 `code_0817_prod.py`（生产原脚本），不是变体。**
[`run_sr.py:222`](../../backend/services/run_sr.py#L222) 的兜底值就是 `code_0817_prod.py`；按 §0.2 的记录，
原脚本里 `gpu_count != 4` 的守卫在单卡分配下恒真 → 作业 `exit(3)`。**症状会像「Slurm 环境有问题」，
实际只是脚本名没指对**——B/C 之前必须先修。变体本体已在位（见下）。

② `SR_SCRIPT_PATH=/DiskArray/prod_slurm.py` 是**死配置**：`SR_SCRIPT_PATH` 这个变量名在**整个仓库里零引用**
（`grep -r` 无命中），且指向 `$BUNDLE` 之外的路径。像是当初本意要设 `SR_SR_SCRIPT` 却写错了名字。
应删掉，否则下次读 unit 的人会以为入口脚本已经配好了。

**变体落位**（`sha256sum -c` 对仓库 `SR_code/variants/`）：

```
041bea73…474a9  30695 B  code_0817_prod_slurm.py   ← 与 provenance.json 的 variant.sha256 一致
92e400a7…3505d  16419 B  verify_sr_run.py           ← ⚠️ 已过期：机上那份是旧版
```

> ⚠️ **2026-09-15：`verify_sr_run.py` 仓库副本已变**（退出码文件的编码锁定，见
> [current-question.md](current-question.md) §4 时间线 2026-09-15）：新值 **17164 B /
> `5fa627d8…`**。机上那份 16419 B 的**行为仍正确**（`A/B/C` 不受影响），但少了两处
> `-export=NONE` + 非 UTF-8 locale 下才暴露的编码保护 → 重启 Slurm 走 **D** 之前必须重新拷一次。
> 变体 `code_0817_prod_slurm.py` 未改动，§0.2 的变体比对（含 `provenance.json`）继续有效。

**其他实测**：

- `sudo -u nginx sbatch --test-only …` **rc=0**（`Job 41140523 to start at … using 2 processors on nodes node81-131`）→ 提交权限与通路 OK。
- `$TEST=/DiskArray/tmp/wangrz/sr_test` 已建；`sudo -u nginx touch $TEST/w` → `TEST WRITE OK`。
- 共享盘可见：`node81-129` → `WORK_OK` + `BUNDLE_OK` ✓。
- ⚠️ **`node104-27` 在本机解析不了**：`getent hosts node104-27` 空、`srun -w node104-27` 报
  `getaddrinfo() failed … check slurm.conf`。它在 `gpu` 分区里且状态 idle。**这是集群侧问题，不是我们的配置**，
  但两次探测都撞上它（它是该族里第一个 idle 节点）。已知影响面：`srun`（客户端直连）必失败；
  `sbatch` 作业由控制器→slurmd 拉起，方向不同、**未验证**是否受影响。待办：向集群管理员报；B/C 里作业若莫名失败，
  先 `scontrol show job <id>` 看落在哪台。
- **未验完的一项**：`node104` 族只验到 `$BUNDLE` 可见（09-11 node104-04，`$WORK` 那行输出被截断）。

`✓= A 通过` 的判据：补齐 ①、删掉 ②、§0.6 库目录修完，且**至少一个** `node81-*` 节点报
`WORK_OK` + `BUNDLE_OK`（`node104-*` 那一族的验证改由 **B0** 承担，原因见下段）。

**A 收尾确认（2026-09-14 第二轮）**：

- `systemctl cat`/两个 unit 文件里 `SR_SCRIPT_PATH` **已搜不到**（`grep -rn` 空输出），
  `/DiskArray/prod_slurm.py` 也不存在 → ② 闭合。（注：上一轮 `systemctl cat` 的输出里出现过这一行，
  本轮两个文件里都没有 —— 要么两轮之间被移走了，要么上一轮那行是转写误差；**两种情况下现状都已确认为干净**。）
- 变体自检：`sha256sum -c` 对 `code_0817_prod_slurm.py` 与 `verify_sr_run.py` 均 **OK**
  → `$BUNDLE` 里这两个文件与仓库 `SR_code/variants/` 逐字节相同（A 阶段冻结合同仍然成立）。
- drop-in 生效（以 `/proc/<pid>/environ` 为证）：`SR_SR_SCRIPT=code_0817_prod_slurm.py`、
  `SR_AGENT_DB=/DiskArray/tmp/wangrz/sr_agent_db/sr_agent.db`、`SR_VERIFY_SCRIPT=verify_sr_run.py` → ① 闭合。
- 老库存在并已 `cp -p` 到新目录（打印 `migrated`）。
- `node81-132`：`WORK_OK` + `BUNDLE_OK` ✓。
- ⚠️ **`node104-*` 一族不是「有一台坏」，是整族在本机解析不了**：idle 里前 5 台
  （`node104-27/28/29/30/31`）`getent hosts` 全部为空。所以「用 `srun` 探 `node104-*`」这个做法**本身不成立**
  —— `srun` 需要提交端直连节点，解析不了就必失败（错在探测手段，不是共享盘）。共享盘是否可见、以及**作业在这族上
  到底能不能跑**，改由 **B0** 用 `sbatch` 验（`sbatch` 由控制器 → slurmd 拉起，正是平台真实用的路径）。

---

## B 裸 Slurm 冒烟（不碰平台；三个小作业，合计 ⏱ 3 分钟）

目的：把「平台能不能提交」「作业里的解释器能不能 import」「退出码文件能不能落盘」三件事**各自单独**验掉，
D 阶段出事时就能立刻排除掉这三层。

> **B1–B3 与部署方向无关，可以先跑**：它们验的是 `--export=NONE` 下的解释器 import、`--gres` 的分配形态、
> 退出码文件落盘 —— 方向①（集群 + 锁单机）与方向②（本机单节点 Slurm）**都要这三条结论**。
> 这里用 §0.0 的 `$NODELIST`（暂时仍是族口径）只是让作业有个落点；真正的单机锁定等方向定了再说（见 §B0）。

### B0 · 先定方向：那台 4 卡机在集群里能不能被调度（**只读，不提交作业**，⏱ 30 秒）

> **需求（用户 2026-09-14 定，优先于本清单此前所有口径）**：所有作业**只跑在同一台 4×3090 物理机内**，
> **不调度到其他服务器**；Slurm 的角色收窄为**本机排队 + 按单卡分配 GPU**。部署方向二选一：
> **①** 复用现有集群，用 `#SBATCH --nodelist=` 锁定那台机器；**②** 在**本机自建单节点 Slurm**
> （无跨节点依赖，顺带规避跨主机域名解析问题）。**方向还没定** —— 由下面三条命令定。

**为什么先跑这三条、再跑 B1–B3**：B1–B3 要么把作业钉在 `--nodelist` 上、要么依赖「作业能落到某台机器」，
而**把作业钉在一台 `down` 的节点上 = 一直排队**（不是失败，是白等）。A 记录里已经有半条坏消息：
`node81-133/134/135/136` 是 `down`，**本机 node81-135 是 `down*`**。所以先花 30 秒把三件事问清楚：

```bash
# (a) 哪几台是 4 卡机（%G=GRES，gpu:4 就是 4 张卡）+ 它们的实时状态（%t：idle/alloc/mix/down*）
sinfo -h -N -o "%N %G %t %P" | grep -E 'gpu:[0-9]+' | sort -u

# (b) 我现在在哪台机器上，它是不是被标成了 down
hostname; scontrol show node $(hostname) | head -4

# (c) 方向②的前提：本机有没有 slurmctld/slurmd 可执行文件、6817/6818 端口占没占
#     （本机**没有 yum 源**，装不了新包，只能用手上已有的）
which slurmctld slurmd munge 2>&1; systemctl is-active slurmd 2>&1; ss -lntp 2>/dev/null | grep -E ':6817|:6818'
```

`record:` 三条的完整输出（都很短）

**判读（判读方用）**：

+ (a) 里带 `gpu:4` 的节点名 = 候选「那台 4 卡机」。它的状态列若是 `down*` → **方向① 直接死**：
  DOWN 节点永远不会被分配，要恢复必须动集群（与「平台是接入方、不改集群」冲突）→ 走方向②。
+ (b) 若 `hostname` 就是 (a) 里那台、`State=DOWN*` → 同上；若本机不在 `gpu` 分区里 → 也要走方向②。
+ (c) `slurmctld` 不在 → 方向② 卡在「没有 yum 源装控制节点」；`:6818` 被占 → 本机已有 slurmd 在跑
  （集群那份），方向② 必须换 cluster 名/端口，不能直接起第二份。这三条任一条不满足，方向② 也要先解决。

✓= 三条输出到手 → 才谈方向①/② 的取舍（那是一个需要拍板的岔路，不是判读能自动得出的结论）。

> **旧 B0 已退役**（备查，不再跑）：它用 `sbatch -w node104-27 … --wrap 'hostname'` 验 node104 族
> 能不能跑作业，是**为「node81 族白名单」口径服务的**。新需求是单机，作业根本不落在 node104 族，
> 结论不再影响任何决策。真想上报集群侧解析问题时再翻出来用。

### B1 · `--export=NONE` 会不会切断 `LD_LIBRARY_PATH`（P2 遗留必验项 V1）

```bash
sudo -u nginx sbatch --parsable --job-name=acc_b1 -p $PART --gres=gpu:1 --nodelist=$NODELIST \
  --time=00:05:00 --export=NONE \
  --output=$TEST/b1_%j.out --error=$TEST/b1_%j.err \
  --wrap "$SR_PYTHON -c 'import torch; from osgeo import gdal; print(torch.__version__, gdal.VersionInfo())'"
```

✓= `$TEST/b1_*.out` 里打印出版本号（登录端实测为 `1.10.2+cu113 2020400`）
✗ 打印 `ImportError: ... libc10.so / libgdal.so` → 就是本项要抓的那个失败，按下面的改法处理。
（`gdal.VersionInfo()` 不是 `gdal.__version__`：老 osgeo 绑定没有 `__version__`，用错了会得到
`AttributeError`，看着像环境坏了，其实只是 API 名不对。）

✗ `.err` 报 `ImportError: libXXX.so` / `OSError` → **一行改动**：`backend/services/run_sr.py` 里
`"#SBATCH --export=NONE"` → `"#SBATCH --export=ALL"`；重跑 `pytest backend/tests/test_run_sr.py`，
拷 `backend/` 到 `$APP` 后 `systemctl restart sr-api`。
✗ 提交被拒 / `Invalid user` → nginx 没有提交权限，见 A 的 `assoc-nginx` 行。

`record:` `.out` 全文 + `--export=NONE` 是否够用

### B2 · 分配审计段（`--gres` 到底给了什么）

```bash
sudo -u nginx sbatch --parsable --job-name=acc_b2 -p $PART --gres=gpu:1 --nodelist=$NODELIST \
  --time=00:05:00 --export=NONE \
  --output=$TEST/b2_%j.out --error=$TEST/b2_%j.err \
  --wrap 'echo "JOB=${SLURM_JOB_ID} GPUS=${SLURM_JOB_GPUS:-<unset>} CVD=${CUDA_VISIBLE_DEVICES:-<unset>} HOST=$(hostname)"; nvidia-smi --query-gpu=index,uuid --format=csv,noheader'
```

✓= `CVD` 非空（P1 探针实测注入的是**整数**，如 `0`）；`nvidia-smi` 至少一行

✗ `CVD=<unset>` → 分配没落地，回 A 的 `gres-gpu` / `config` 行。
✗ `GPUS=<unset>` 是**已知形态**（探针实测），不影响运行——`<GPUIDS>` 在变体里只作审计字段，不选卡。

`record:` 这一行原文

### B3 · 退出码文件真的能落盘（V2，权限预检）

```bash
sudo -u nginx mkdir -p $TEST/b3_verify

cat > $TEST/b3_cfg.xml <<EOF
<SFSR_Config>
  <DatarootLQ>$TEST/b3_verify</DatarootLQ>
  <CloudLimit>80</CloudLimit>
  <DeleteOriTifNeeded>False</DeleteOriTifNeeded>
  <SRScale>2</SRScale>
  <Suffix>b3</Suffix>
  <OPT>$OPT</OPT>
</SFSR_Config>
EOF

# 用 sbatch 而不是 srun：srun 要提交端直连节点，落在解析不了的 node104-* 上会假失败
# （见 B0）；sbatch 是控制器 → slurmd 拉起，也正是平台真实用的路径。
sudo -u nginx sbatch --parsable --job-name=acc_b3 -p $PART --nodelist=$NODELIST --time=00:05:00 --export=NONE \
  --output=$TEST/b3_%j.out --error=$TEST/b3_%j.err \
  --wrap "cd $BUNDLE; $SR_PYTHON verify_sr_run.py --config $TEST/b3_cfg.xml --sr-exit-code 0 --job-id 999999; echo verifier_rc=\$?"
```

✓= `$TEST/b3_*.out` 里 `verifier_rc=90`（该目录里既没有 SRLOG 也没有输出 tif，**契约不满足才是正确结论**），且

✓= 打印 `verifier_rc=90`（该目录里既没有 SRLOG 也没有输出 tif，**契约不满足才是正确结论**），且

```bash
ls -l $TEST/b3_verify/Debug/_SREXIT_999999.txt
cat $TEST/b3_verify/Debug/_SREXIT_999999.txt          # ✓= verdict=90，reason 里点明缺 SRLOG
```

✗ 打印 `verifier_rc=90` 但没有文件、stderr 有 `无法写退出码文件` → **nginx 对 `<lq_path>/Debug/` 无写权限**
→ `chown -R nginx:nginx <lq_path>`。这条不过，D 阶段所有任务只会显示 `UNKNOWN`。

`record:` `verifier_rc` 与退出码文件内容

---

## C 单场景真 SR（不碰平台；⏱ 一次完整 SR，约 5–10 分钟）

目的：证明在真机上按契约跑得通一遍**真的超分**（不是冒烟、不是跳过），且变体没把原脚本改坏。

⚠️ **必须在场景副本上跑**：即使 `DeleteOriTifNeeded=False`、`Suffix` 非空，SR 仍会把产物 tif、
`Debug/` 日志与 meta.xml 更新写进 `DatarootLQ`（非空 suffix 下源图不改名也不删除，见契约 §2.4
第 1 条与 §6）。跑生产目录 = 动生产数据。

> **本节是手工旁路。走平台请直接看 §D** —— 平台已能自建副本（`SR_SANDBOX_ROOT`，
> `deploy/README.md` §7.5），前端填**生产路径**即可，不需要手工拷。本节留着是为了
> 「先证明裸 SR 能跑通、再怀疑平台」这条排查顺序。

```bash
SRC=<挑一个真实场景目录>            # 该目录含 <basename>.tif 与 <basename>{_,.}meta.xml
BASE=$(basename "$SRC")
mkdir -p $TEST/c_run
cp -a "$SRC" $TEST/c_run/           # 目录名必须原样保留：SC 步的输入名 = 目录名 + ".tif"
chown -R nginx:nginx $TEST/c_run

# ⚠️ meta 的两种拼写都要试 —— 真机实测外层 _L1_PAN 目录里是**点**（<目录名>.meta.xml），
#    不是本文档早期写的下划线。SR 两种都认不了时会静默当成"没有 meta"。
META=$TEST/c_run/$BASE/${BASE}_meta.xml
[ -f "$META" ] || META=$TEST/c_run/$BASE/${BASE}.meta.xml
echo "meta=$META"; grep -E 'CloudPercent|DataBits|SolarAzimuth' "$META"
```

> ⚠️ `cp -a` 整个目录在真机上约 **10 GB+**：一个 `_L1_PAN` 场景里除 `<目录名>.tif`（~5 GB）
> 还有 `<目录名>_ori.tif`（~5 GB）、`<目录名>.tif.ori`、`.jpg` 与若干 `_000X_` 子目录。
> 只想验证链路的话，可以只拷 `<目录名>.tif` + `<目录名>{_,.}meta.xml` + 建空的 `Debug/`
> —— SR 不读 `_ori.tif`（`grep -n '_ori' code_0817_prod.py` 命中的都是 numpy 变量名，不是文件）。

✓= `CloudPercent` 是**数字**（0 / 1 / 3…）；`SolarAzimuth` 有值（走 SC 步）

✗ `CloudPercent` 是 `θ` → `int("θ")` 抛 ValueError，作业以 traceback 结束（退出码 1，契约不满足）。
先把该值清洗成 `0` 再继续（契约 §5 已知语义；平台侧不做清洗，真机上遇到就是失败）。
✗ `SolarAzimuth` 标签缺失 → 脚本直接抛异常（契约 §4）。

```bash
CFG=$TEST/c_cfg.xml
cat > $CFG <<EOF
<SFSR_Config>
  <DatarootLQ>$TEST/c_run/$BASE</DatarootLQ>
  <GPUIDS>0</GPUIDS>
  <CloudLimit>100</CloudLimit>
  <DeleteOriTifNeeded>False</DeleteOriTifNeeded>
  <SRScale>2</SRScale>
  <Suffix>acc</Suffix>
  <OPT>$OPT</OPT>
</SFSR_Config>
EOF

sudo -u nginx srun -N1 -n1 -p $PART --gres=gpu:1 --time=01:00:00 sh -c "
  cd $BUNDLE || exit 1
  $SR_PYTHON code_0817_prod.py -f $CFG ; _rc=\$?; echo \"sr_rc=\$_rc\"
  $SR_PYTHON verify_sr_run.py --config $CFG --sr-exit-code \$_rc ; echo \"verifier_rc=\$?\""
```

> `<Suffix>acc</Suffix>` 是**非空**后缀，本轮一律如此——空 `<Suffix/>` 是已知破坏性语义
> （输出名 == 输入名 ⇒ 被改名/删除的是输入本身），见契约 §2.4 第 1 条，本批不修。
> 本轮期望的目录变化只有三处新增：`<目录名>_acc.tif`、`Debug/` 下的日志与退出码文件、
> meta.xml 更新；输入 `<目录名>.tif` 的 mtime 不变。

✓= 四条同时成立：

```bash
tail -c 4096 $TEST/c_run/$BASE/Debug/${BASE}_SRLOG.txt | tail -3   # ✓= 最后一个非空行 "Run finished."
ls -l $TEST/c_run/$BASE/${BASE}_acc.tif                            # ✓= 存在、非 0 字节（uint16）
ls -l $TEST/c_run/$BASE/${BASE}.tif                                # ✓= 输入原样（非空 suffix 不改名、不删除）
cat $TEST/c_run/$BASE/Debug/_SREXIT_<job_id>.txt                   # ✓= verdict=0 / skip=0
```

`record:` `sr_rc` / `verifier_rc` / 退出码文件名 / 输出 tif 大小 / 端到端耗时 / SRLOG 末三行

✗ `sr_rc=3` → 装的还是**原脚本**（复核 §0.2 的横幅），或 GPU 分配没生效（回 B2）。
✗ `sr_rc=0` 但 `verifier_rc=90` → 看 stderr 指明缺哪条：多半是尺寸闸门一类的静默退出。
  这正是校验器要抓的东西——把它当作一条**已知语义**记下来。
✗ `verifier_rc=90` 且 stderr 说「SRLOG 早于 config.xml」 → 机器间时钟偏差 > 60s，先对时
  （`STALE_SLACK_SEC`，`verify_sr_run.py`）。

---

## D 平台链路（四条结论，缺一不可）

**D0 前置**（跑完 C 才做；此时 §0.2 的变体必须已装好）

```bash
/opt/sr-venv/bin/python -c "
import sys; sys.path.insert(0,'$APP')
from backend.services.run_sr import build_batch_script
print(build_batch_script('/w/c.xml','/w'))" | sed -n '1,22p'
```

✓= 生成物里能看到 `#SBATCH --gres=gpu:1`、`--export=NONE`、`cd $BUNDLE`、以及两行 python 调用

**D 阶段通例**（每条都要遵守，否则测出来的不是我们想测的东西）

1. **`suffix` 必须非空**（`acc-d1` / `acc-d2` / `acc-d3` / `acc-d4`）。空 suffix 会把**输入改名**，
   这是破坏性的（契约 §2.4 第 1 条）。逐条不同不再是硬要求（09-10 起配置文件按
   `run_sr_<suffix>_<任务指纹前12位>.xml` 命名，同 suffix 的不同任务不再互相覆盖），
   但逐条不同能让日志好读。
2. `lq_path`：配了 `SR_SANDBOX_ROOT` 就**可以指生产目录**（平台在作业第一步自建副本，生产只读）；
   D1/D3 这类要造故障的用例仍指 `$TEST` 下手工弄坏的副本 —— 拿好数据去制造坏结果没有意义。
3. POST 走 `http://127.0.0.1:8000`（后端直连）；SSE 走 `http://127.0.0.1`（经 nginx，真实链路）。

查队列的一行过滤器（下面反复用到）——`run_dataroot` 是**作业实际工作的目录**（开沙箱时 = 副本，
产物和 `Debug/` 都在它下面，不是 `lq_path`）：

```bash
curl -s http://127.0.0.1:8000/api/queue | /opt/sr-venv/bin/python -c '
import json,sys
for t in json.load(sys.stdin)["tasks"]:
    p=t["params"]; print(t["task_id"], t["job_id"], t["state"], p["suffix"],
                         "lq="+p["lq_path"], "run="+(t.get("run_dataroot") or "-"))'
```

### D1 · 静默失败不再被标成成功（§2.3 毒药的正面验证）

**构造**：一个「SC 步、meta 齐全、但输入 tif 不存在」的场景 ⇒ `util.get_l1_pan_tif_rcsc` 打印
`not existed` 后 `exit(0)`，**发生在 SRLOG 创建之前** ⇒ 退出码 0 且完全没有 SRLOG。

```bash
SC=$TEST/d1_silent
mkdir -p $SC/Debug
cat > $SC/d1_silent_meta.xml <<'EOF'
<Meta>
  <DataBits>12</DataBits>
  <CloudPercent>0</CloudPercent>
  <SolarAzimuth>90</SolarAzimuth>
</Meta>
EOF
# 故意不建 d1_silent.tif
chown -R nginx:nginx $SC

curl -s -X POST http://127.0.0.1:8000/api/queue -H 'content-type: application/json' \
  -d '{"lq_path":"'$SC'","suffix":"acc-d1","sr_scale":2,"cloud_limit":80}'
```

等 10–30 秒（作业要排到节点上跑完），然后三看：

```bash
# (a) 作业内判定：校验器写下的结论
ls -l $SC/Debug/_SREXIT_*.txt && cat $SC/Debug/_SREXIT_*.txt
#     ✓= verdict=90，reason 含「缺 SRLOG」
ls $SC/Debug/*_SRLOG.txt 2>/dev/null ; echo "srlog_ls_rc=$?"     # ✓= 不存在（rc 非 0）

# (b) 平台判定：FAILED，不是 COMPLETED
curl -s http://127.0.0.1:8000/api/queue | /opt/sr-venv/bin/python -c '
import json,sys
for t in json.load(sys.stdin)["tasks"]:
    if t["params"]["suffix"]=="acc-d1": print("job", t["job_id"], "state", t["state"])'
#     ✓= state 是 FAILED

# (c) 同参数重投：必须真的重跑，而不是回 RESUMED_COMPLETED
curl -s -X POST http://127.0.0.1:8000/api/queue -H 'content-type: application/json' \
  -d '{"lq_path":"'$SC'","suffix":"acc-d1","sr_scale":2,"cloud_limit":80}'
```

✓= 三份证据齐全：

- (a) 退出码文件存在、`verdict=90`、reason 点名缺 SRLOG；且该场景**没有 SRLOG**（证明是静默失败被抓住，不是误判）。
- (b) `/api/queue` 该任务 `FAILED`。
- (c) 第二次 POST 返回 `"status":"SUBMITTED"`（**不是** `RESUMED_COMPLETED`）+ `"previous_state":"FAILED"`，
      `job_id` 是**新的**；随后 `$SC/Debug/` 出现**第二个** `_SREXIT_<新job_id>.txt`。

`record:` (a) 退出码文件全文 + 两次 `ls` 输出；(b) 队列行；(c) 两次 POST 的完整响应 JSON

✗ (b) `UNKNOWN` → 平台反推的路径与作业写的对不上：核对 config.xml 的 `DatarootLQ` 与
  `run_sr.exit_code_file_for` 的推导是否同值（用队列行的 `run_dataroot` 一眼比对），以及该目录下
  `Debug/` 是否 nginx 可写（B3）。**沙箱开着时 `Debug/` 在副本里**——那说明沙箱没建成，
  看作业 `.err` 有没有 `sandbox copy failed` / `sandbox copy incomplete`。
✗ (b) `COMPLETED` → 校验器没被执行。按 D0 复核批脚本里的两行调用，并确认 `$BUNDLE/verify_sr_run.py` 存在。
✗ (c) 回 `RESUMED_COMPLETED` → 平台把「退出码 0」当成了成功（正是本轮要消灭的行为），说明跑的还是旧
  `backend/services/slurm.py`：确认 `$APP/backend` 已更新到本批版本后 `systemctl restart sr-api`。

### D2 · 幂等回归（活跃态 RESUMED_ACTIVE / 终态 RESUMED_COMPLETED，且不产生第二个 job_id）

用 C 阶段的真实场景**再拷一份**（目录名 `$BASE` 必须保留），跑一次真 SR——它要几分钟，正好留出
「活跃期间重复提交」的窗口：

```bash
SRC=<C 阶段用的那个真实场景目录>
BASE=$(basename "$SRC")
mkdir -p $TEST/d2_run && cp -a "$SRC" $TEST/d2_run/ && chown -R nginx:nginx $TEST/d2_run

BODY='{"lq_path":"'$TEST'/d2_run/'$BASE'","suffix":"acc-d2","sr_scale":2,"cloud_limit":100}'

curl -s -X POST http://127.0.0.1:8000/api/queue -H 'content-type: application/json' -d "$BODY"
# ↑ 第一次；记下 job_id（下称 J1）

sleep 5
curl -s -X POST http://127.0.0.1:8000/api/queue -H 'content-type: application/json' -d "$BODY"
# ↑ 第二次（活跃期）：✓= "status":"RESUMED_ACTIVE"，job_id 仍是 J1
```

等作业跑完（SRLOG 出现 `Run finished.`），再投第三次：

```bash
curl -s -X POST http://127.0.0.1:8000/api/queue -H 'content-type: application/json' -d "$BODY"
# ↑ 第三次（终态）：✓= "status":"RESUMED_COMPLETED"，job_id 仍是 J1

curl -s http://127.0.0.1:8000/api/queue | /opt/sr-venv/bin/python -c '
import json,sys
ts=[t for t in json.load(sys.stdin)["tasks"] if t["params"]["suffix"]=="acc-d2"]
print("tasks:", len(ts), "job_ids:", {t["job_id"] for t in ts}, "states:", {t["state"] for t in ts})'
# ✓= tasks: 1  job_ids: {J1}  states: {'COMPLETED'}     ← 只有一行、只有一个 job_id
```

`record:` 三次 POST 的 `status`+`job_id`；队列统计行；活跃期的一次 `squeue --job J1 --noheader --format=%T`

✗ 活跃期返回 `SUBMITTED` 且 job_id 变了 → 幂等层没生效：查 `_norm_sr_params` 与 `tools/run_sr` 的键集
  是否一致（指纹必须同值），以及 `squeue --job J1` 是否真能查到该作业。
✗ 终态返回 `SUBMITTED` → 退出码文件没被读到（回 D1 的 (b) 分支排查）。

### D3 · 云量跳过：合法跳过 ≠ 失败，也**不被反复重投**

**构造**：输入 tif 存在（内容不会被读，1MB 占位足够——云量闸门在读图之前触发），meta 的
`CloudPercent=50`，提交时 `cloud_limit=10` ⇒ `50 > 10` ⇒ 变体 E9 分支：**先建 SRLOG 写
`Run skipped:` 终态行，再 exit(0)**。

```bash
SC=$TEST/d3_cloud
mkdir -p $SC/Debug
cat > $SC/d3_cloud_meta.xml <<'EOF'
<Meta>
  <DataBits>12</DataBits>
  <CloudPercent>50</CloudPercent>
  <SolarAzimuth>90</SolarAzimuth>
</Meta>
EOF
truncate -s 1048576 $SC/d3_cloud.tif      # 1MB 占位；跳过路径不会读它的内容
chown -R nginx:nginx $SC

curl -s -X POST http://127.0.0.1:8000/api/queue -H 'content-type: application/json' \
  -d '{"lq_path":"'$SC'","suffix":"acc-d3","sr_scale":2,"cloud_limit":10}'
```

等 10–30 秒，然后：

```bash
tail -c 4096 $SC/Debug/d3_cloud_SRLOG.txt | tail -2
#   ✓= 最后一个非空行以 "Run skipped:" 开头（"Run skipped: cloud limit exceeded"）
cat $SC/Debug/_SREXIT_*.txt
#   ✓= verdict=0 / skip=1 / sr_exit_code=0
ls $SC/d3_cloud_acc-d3.tif 2>/dev/null ; echo "out_ls_rc=$?"
#   ✓= 不存在（rc 非 0）—— 跳过不产 tif，契约 §2 明确允许

curl -s http://127.0.0.1:8000/api/queue | /opt/sr-venv/bin/python -c '
import json,sys
for t in json.load(sys.stdin)["tasks"]:
    if t["params"]["suffix"]=="acc-d3": print("job", t["job_id"], "state", t["state"])'
#   ✓= COMPLETED（跳过不是失败）
```

**不被反复重投**——同参数再投一次：

```bash
curl -s -X POST http://127.0.0.1:8000/api/queue -H 'content-type: application/json' \
  -d '{"lq_path":"'$SC'","suffix":"acc-d3","sr_scale":2,"cloud_limit":10}'
#   ✓= "status":"RESUMED_COMPLETED"、job_id 不变（没被当成失败而重投）
```

`record:` SRLOG 末两行；退出码文件全文；两次 POST 的 status/job_id

✗ 队列显示 `FAILED` → 校验器把跳过判成了不满足：确认 `$BUNDLE/verify_sr_run.py` 是本批版本
  （`RUN_SKIPPED_PREFIX` 分支），且 SRLOG 末行确实是 `Run skipped:`。
✗ SRLOG 末行不是 `Run skipped:` → 跑的还是原脚本（原脚本在这里是**无 SRLOG 的 exit(0)**，会落进 D1 的失败路径）。
  复核 §0.2 的横幅。
✗ 第二次 POST 回 `SUBMITTED` → 跳出会被无限重投，回 D1 的 (b) 分支。

### D4 · SSE：`/api/queue/events` 收到 `job_update`，前端队列页刷新

**终端 1**（经 nginx 的真实链路，挂住别关）：

```bash
curl -sN http://127.0.0.1/api/queue/events
```

**终端 2**（用**新的 suffix** 起一条新任务；同参数会命中复用、没有状态变化、也就没有事件）：

```bash
curl -s -X POST http://127.0.0.1:8000/api/queue -H 'content-type: application/json' \
  -d '{"lq_path":"'$TEST'/d3_cloud","suffix":"acc-d4","sr_scale":2,"cloud_limit":10}'
```

✓= 终端 1 在几秒内逐帧出现 `data: {"type":"job_update", …}`，同一条任务按
`SUBMITTING → PENDING/RUNNING → COMPLETED` 推进；帧要**立刻到**，不是攒一批再吐

**浏览器侧**：Win11 开 `http://10.10.81.135/queue` → 提交一条（或复用上面刚提交的）→ 状态徽标
**不用刷新页面**就随 SSE 变化到「完成」；F12 Network 里 `/api/queue/events` 是挂住的 `text/event-stream`。

`record:` 终端 1 收到的帧（含 `type` 与 `state` 至少 3 帧）；浏览器徽标行为

✗ 一条帧都没有 → 后端轮询器没起来：`journalctl -u sr-api -n 50` 看 lifespan 异常；
  或 `SR_QUEUE_POLL_SEC` 过大（缺省 2 秒）。
✗ 帧要等十几秒一起到 → nginx 缓冲没关：`deploy/nginx.conf` 的 `/api/` 段需 `proxy_buffering off`
  （SSE），改完 `nginx -t && systemctl reload nginx`。
✗ REST 正常但 SSE 断 → 反代超时太短：`proxy_read_timeout` 按 `deploy/nginx.conf` 设到 3600s。

---

## E 回贴清单（把这八样贴回来即可判读并收口）

1. **A**：`probe_slurm.sh` 完整输出（SUMMARY + HINTS 段必须有）；跑了 `--deep` 的话一并贴。
2. **§0.4**：三条待核项的实际输出（bundle 拼写 / torch+gdal 版本 / 两节点共享盘可见性）。
3. **B0**：三条只读命令的完整输出（4 卡机是谁 + 我在哪台 + slurmd/端口状态）。
4. **B**：B1 的 `.out`（版本号那行）+ B2 的审计行 + B3 的 `verifier_rc` 与退出码文件内容。
5. **C**：`sr_rc` / `verifier_rc` / 退出码文件名 / 输出 tif 大小 / 端到端耗时 / SRLOG 末三行。
6. **D1**：退出码文件全文 + 两次 `ls` 输出 + 队列行 + 两次 POST 的响应 JSON。
7. **D2 / D3**：POST 的 `status`+`job_id`（D2 三次、D3 两次）+ 队列统计行；D3 的 SRLOG 末两行。
8. **D4**：SSE 收到的前三帧原文（含 `type` / `state`）+ 浏览器徽标行为。

判读口径（判读方用）：D1 看 `verdict=90 → FAILED → 重投变 SUBMITTED`；D2 看两次复用 + 单 job_id；
D3 看 `skip=1 → COMPLETED → 复用`；D4 看 `job_update` 帧。四条都成立才算验收通过。

---

## F 关联

- 决策与分批计划：[slurm-integration.md](slurm-integration.md)（§一 决策 / §二 已核实事实 / §2.7 真机实测值）
- 调用契约 v1.5：[sr-pipeline-interface.md](../sr_code/sr-pipeline-interface.md)（§2 退出码 90 与云量跳过终态 / §2.4 三条已知语义）
- 变体差异表：[sr-slurm-deploy-variant.md](../sr_code/sr-slurm-deploy-variant.md)（E1–E9 / 校验器 / 上机必验项 V1–V6）
- 只读探针：[deploy/slurm/probe_slurm.sh](../../deploy/slurm/probe_slurm.sh)
- 真机部署现状：[real-machine-bringup.md](real-machine-bringup.md)（§5 症状表）
- 部署总览：[deploy/README.md](../../deploy/README.md)（「Slurm 接入」小节）
