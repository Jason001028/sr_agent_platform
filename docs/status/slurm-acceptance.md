# Slurm 真机接入 · 分阶段验收清单（node81-135）

> 日期：2026-09-10 · 状态：**待执行**（命令由运维在 node81-135 上跑，输出贴回后判读）· 读者：运维本人 + 接手 Agent 窗口
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
TEST=/DiskArray/tmp/wangrz/sr_test
PART=gpup
export APP BUNDLE SR_PYTHON WORK OPT TEST PART

echo "$APP"; echo "$SR_PYTHON"    # ✓= 两个空变量说明没贴全
```

出处：`$BUNDLE/$SR_PYTHON/$WORK/$PART` 见 `deploy/sr-api.service` 的 `Environment=`；
`$OPT` 见 `backend/config.SR_DEFAULT_OPTIONS_YML`；`$TEST` 是本清单新建的验收沙箱（**不碰生产数据**）。

### 0.1 上车：三个文件从开发机拷到内网

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

所以部署动作 = 把变体装进 `$BUNDLE`、占住这两个文件名，**原脚本先备份**：

```bash
cd $BUNDLE
cp -a code_0817_prod.py code_0817_prod.py.bak-20260910      # 备份原始生产脚本（回滚 = 一条 mv）
ls -l code_0817_prod.py.bak-20260910

cp /path/to/code_0817_prod_slurm.py ./code_0817_prod.py     # 变体顶替（CLI 与原脚本完全一致，只有 -f）
cp /path/to/verify_sr_run.py       ./verify_sr_run.py
head -12 code_0817_prod.py                                  # ✓= 看到 "GENERATED FILE — DO NOT EDIT" 横幅
```

`record:` `ls -l $BUNDLE/code_0817_prod.py*` 与变体首行横幅

> **为什么必须改名顶替**：批脚本里那一行写死了 `code_0817_prod.py`（`run_sr.py:180`），变体放在旁边叫别的名字
> **永远不会被执行**。要两个文件并存，得给 `build_batch_script` 加一个脚本名参数（开发机一行改动 +
> `test_run_sr.py` 同步），本批不做，按上面的顶替法走。
>
> ⚠️ **不装变体直接跑 D 的后果**：原脚本 `gpu_count != 4` 的守卫在单卡分配下恒真 → 作业 `exit(3)`；
> 更糟的是它会执行 `systemctl stop slurmd.service`（root 提交时**真把节点从池里摘掉**）。
> A/B 阶段可以先不装，**进入 D 之前必须装完并复核横幅**。

### 0.3 service env 与重启

```bash
grep -nE '^Environment=(SR_BUNDLE_DIR|SR_PYTHON|SR_SLURM_WORK_DIR|SR_SLURM_PARTITION|SR_SLURM_TIME|SR_SLURM_CPUS|SR_SLURM_FAKE)' /etc/systemd/system/sr-api.service
```

✓= 六项都在，且 `SR_SLURM_FAKE=0`；`SR_SLURM_WORK_DIR` 指向共享盘

```bash
mkdir -p $WORK && chown nginx:nginx $WORK
systemctl daemon-reload && systemctl restart sr-api
systemctl status sr-api --no-pager | head -5      # ✓= active (running)
```

✗ 少 env → 按 `deploy/sr-api.service` 注释补；改了 unit 没 `daemon-reload` 则不生效。
✗ `SR_SLURM_FAKE=1` → D 阶段全程走内存假调度器，**四条结论全部作废**——先确认它是 0。

### 0.4 待核项（`slurm-integration.md §2.7` 的「待核」行 + 两处上机复核）

```bash
# (1) bundle 目录拼写（以源码 code_0817_prod.py:27 的 load_library() 为准）
ls -d $BUNDLE && ls $BUNDLE/code_0817_prod.py $BUNDLE/util.py $BUNDLE/models $BUNDLE/options

# (2) SR_PYTHON 里 torch / GDAL 齐不齐（是 torch1.9.1py36，不是登录 shell 的 base 环境）
$SR_PYTHON -c "import torch, gdal; print('torch', torch.__version__, '| gdal', gdal.__version__)"

# (3) 共享盘在不同计算节点上都可见（12 节点集群，本机路径必挂）
srun -N1 -n1 -w node81-129 sh -c "ls -d $WORK $BUNDLE" ; echo "rc=$?"
srun -N1 -n1 -w node81-140 sh -c "ls -d $WORK $BUNDLE" ; echo "rc=$?"
```

`record:` 三条的实际输出；核完把 §2.7 对应行的「待核」改成「已核」

✗ (1) 拼写不符 → 改 `deploy/sr-api.service` 的 `SR_BUNDLE_DIR`（源码优先，先 `ls` 看清实际拼写）。
✗ (2) import 失败 → 换解释器前**先确认**新环境里 torch/GDAL/`ImgHistMatch.so` 齐（见 service 注释）。
✗ (3) 某节点看不到 `$WORK` → `SR_SLURM_WORK_DIR` 不是共享盘，作业会秒挂。

### 0.5 沙箱目录

```bash
mkdir -p $TEST && chown -R nginx:nginx $TEST
sudo -u nginx touch $TEST/w && echo "nginx-can-write-OK"
```

**为什么是 nginx**：`sr-api` 以 `User=nginx` 运行，它 `sbatch` 提交的作业**也以 nginx 身份在计算节点上执行**。
所以本清单里「作业用户可写」= **nginx 可写**，不是 root。

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

**GPU 分配形态（可选项，默认不跑）**——`--deep` 会**真的提交一个 2 分钟作业**，只在复核 E3 兜底或 B2 有疑问时才跑：

```bash
sh $APP/deploy/slurm/probe_slurm.sh --deep     # ⚠️ 默认不跑
```

`record:` 两种模式的完整输出都贴回（SUMMARY + HINTS 段必须有）

---

## B 裸 Slurm 冒烟（不碰平台；三个小作业，合计 ⏱ 3 分钟）

目的：把「平台能不能提交」「作业里的解释器能不能 import」「退出码文件能不能落盘」三件事**各自单独**验掉，
D 阶段出事时就能立刻排除掉这三层。

### B1 · `--export=NONE` 会不会切断 `LD_LIBRARY_PATH`（P2 遗留必验项 V1）

```bash
sudo -u nginx sbatch --parsable --job-name=acc_b1 -p $PART --gres=gpu:1 \
  --time=00:05:00 --export=NONE \
  --output=$TEST/b1_%j.out --error=$TEST/b1_%j.err \
  --wrap "$SR_PYTHON -c 'import torch, gdal; print(torch.__version__, gdal.__version__)'"
```

✓= `$TEST/b1_*.out` 里打印出版本号（如 `1.9.1 3.x`）

✗ `.err` 报 `ImportError: libXXX.so` / `OSError` → **一行改动**：`backend/services/run_sr.py` 里
`"#SBATCH --export=NONE"` → `"#SBATCH --export=ALL"`；重跑 `pytest backend/tests/test_run_sr.py`，
拷 `backend/` 到 `$APP` 后 `systemctl restart sr-api`。
✗ 提交被拒 / `Invalid user` → nginx 没有提交权限，见 A 的 `assoc-nginx` 行。

`record:` `.out` 全文 + `--export=NONE` 是否够用

### B2 · 分配审计段（`--gres` 到底给了什么）

```bash
sudo -u nginx sbatch --parsable --job-name=acc_b2 -p $PART --gres=gpu:1 \
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

sudo -u nginx srun -N1 -n1 -p $PART --time=00:05:00 sh -c "
  cd $BUNDLE || exit 1
  $SR_PYTHON verify_sr_run.py --config $TEST/b3_cfg.xml --sr-exit-code 0 --job-id 999999
  echo \"verifier_rc=\$?\""
```

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

⚠️ **必须在场景副本上跑**：即使 `DeleteOriTifNeeded=False`、`Suffix` 非空，`util.writeTiff` 仍会把
**输入**改名为 `*_NOSR.tif`。跑生产目录 = 动生产数据。

```bash
SRC=<挑一个真实场景目录>            # 该目录含 <basename>.tif 与 <basename>_meta.xml
BASE=$(basename "$SRC")
mkdir -p $TEST/c_run
cp -a "$SRC" $TEST/c_run/           # 目录名必须原样保留：SC 步的输入名 = 目录名 + ".tif"
chown -R nginx:nginx $TEST/c_run

grep -E 'CloudPercent|DataBits|SolarAzimuth' $TEST/c_run/$BASE/${BASE}_meta.xml
```

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
> （输出名 == 输入名 ⇒ 把输入改名），见契约 §2.4 第 1 条，本批不修。

✓= 四条同时成立：

```bash
tail -c 4096 $TEST/c_run/$BASE/Debug/${BASE}_SRLOG.txt | tail -3   # ✓= 最后一个非空行 "Run finished."
ls -l $TEST/c_run/$BASE/${BASE}_acc.tif                            # ✓= 存在、非 0 字节（uint16）
ls -l $TEST/c_run/$BASE/${BASE}_NOSR.tif                           # ✓= 输入已改名为 _NOSR（预期）
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

1. **`suffix` 必须非空且逐条不同**（`acc-d1` / `acc-d2` / `acc-d3` / `acc-d4`）。两个原因：空 suffix 会把
   输入改名（破坏性）；而平台按 suffix 命名 config.xml 与批脚本（`run_sr_<suffix>.xml`），**不同用例
   同 suffix 会互相覆盖配置文件**，把退出码文件的路径推断带偏。
2. `lq_path` 一律指 `$TEST` 下的沙箱目录，**不碰生产数据**。
3. POST 走 `http://127.0.0.1:8000`（后端直连）；SSE 走 `http://127.0.0.1`（经 nginx，真实链路）。

查队列的一行过滤器（下面反复用到）：

```bash
curl -s http://127.0.0.1:8000/api/queue | /opt/sr-venv/bin/python -c '
import json,sys
for t in json.load(sys.stdin)["tasks"]:
    p=t["params"]; print(t["task_id"], t["job_id"], t["state"], p["suffix"], p["lq_path"])'
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
  `run_sr.exit_code_file_for` 的推导（尤其有没有两条用例共用 suffix），以及 `<lq_path>/Debug/`
  是否 nginx 可写（B3）。
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

## E 回贴清单（把这七样贴回来即可判读并收口）

1. **A**：`probe_slurm.sh` 完整输出（SUMMARY + HINTS 段必须有）；跑了 `--deep` 的话一并贴。
2. **§0.4**：三条待核项的实际输出（bundle 拼写 / torch+gdal 版本 / 两节点共享盘可见性）。
3. **B**：B1 的 `.out`（版本号那行）+ B2 的审计行 + B3 的 `verifier_rc` 与退出码文件内容。
4. **C**：`sr_rc` / `verifier_rc` / 退出码文件名 / 输出 tif 大小 / 端到端耗时 / SRLOG 末三行。
5. **D1**：退出码文件全文 + 两次 `ls` 输出 + 队列行 + 两次 POST 的响应 JSON。
6. **D2 / D3**：POST 的 `status`+`job_id`（D2 三次、D3 两次）+ 队列统计行；D3 的 SRLOG 末两行。
7. **D4**：SSE 收到的前三帧原文（含 `type` / `state`）+ 浏览器徽标行为。

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
