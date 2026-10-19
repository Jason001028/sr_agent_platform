# SR 生产脚本 · Slurm 部署变体差异表

> 日期：2026-09-10 · 状态：**已定**（随生成器 v1.0.0 发布）· 读者：接手 Agent 窗口 + 运维
> 前置：[docs/status/slurm-integration.md](../status/slurm-integration.md) §2.2（E1–E9 逐条行锚点）、[sr-pipeline-interface.md](sr-pipeline-interface.md) v1.4（唯一调用契约）
> 本文档为静态映射表：**不含生成时间戳**，变体的可复现性由 `provenance.json` + `--check` 保证。

## 1. 一句话总结

`SR_code/code_0817_prod.py` 逐字节不动，作为唯一真源；Slurm 部署用的可运行版本由 [SR_code/tools/gen_slurm_variant.py](../../SR_code/tools/gen_slurm_variant.py) 按锚点替换表**机械产出**到 `SR_code/variants/`。本文档是那张替换表的可读版本。

为什么不是「复制一份改改」：原脚本里有 4 处会直接让 Slurm 作业失败或污染集群的代码（硬编码选卡、数物理卡、停 slurmd、静默退出）。手改副本无法审计，也无法在真源更新后被可靠地重构。锚点替换把「改了什么、为什么改」变成可 diff、可校验、可复现的产物。

## 2. 产物与复现

| 文件 | 作用 |
|---|---|
| `SR_code/variants/code_0817_prod_slurm.py` | 部署变体本体（首部带 `GENERATED FILE — DO NOT EDIT` 横幅） |
| `SR_code/variants/code_0817_prod_slurm.diff` | 与真源的 unified diff，评审看这一份即可 |
| `SR_code/variants/code_0817_prod_slurm.provenance.json` | 真源/变体 sha256、每条 edit 的锚点哈希与命中数（**无时间戳**） |
| `SR_code/variants/.gitattributes` | `* text eol=lf`，防止 Windows 检出让变体变形 |

```bash
python SR_code/tools/gen_slurm_variant.py          # 重新生成（写盘）
python SR_code/tools/gen_slurm_variant.py --check  # 只比对；过期 exit 1
```

退出码：`0` 正常 / `1` 过期（仅 `--check`）/ `2` 锚点命中数不符。

生成器读入时做 CRLF→LF 归一、写出统一 `\n`，因此换行风格不影响产物字节；`--check` 是逐字节比较，任何手改（包括加一行注释）都会报 `STALE`。

## 3. 差异表（E1–E9）

真源基线 sha256（CRLF→LF 归一后）：
`03c9b4fc3f64cfd5e3b331128be5a09477d5ebe0362b0e9e4927fcdd356d40af`

| # | 真源位置 | 原代码 | 变体改为 | 理由 |
|---|---|---|---|---|
| **E1** | `code_0817_prod.py:27` | 模块级 `lib = npct.load_library(...)`，无保护 | 包 `try/except Exception`，兜底 `lib = None` | 缺 `.so` 时进程在 **import 期**崩溃，且崩在任何日志写出之前，报错无法定位。对照 `code_0820_prod_windows.py:25-28` |
| **E2** | `:146` | `os.environ['CUDA_VISIBLE_DEVICES'] = "0"` | 删除，改为注释说明由 Slurm 注入 | 无条件覆盖会抹掉 `--gres=gpu:1` 的分配结果，所有作业挤物理卡 0 |
| **E3** | `:147` | `gpuid = '0'` | 从 env 取首个设备并 `int()`，失败兜底 `'0'` | `gpuid` 只用于日志与 `pynvml` 打印（**物理**索引）。Slurm 可能注入 UUID / MIG 形态，`:624`、`:178` 的 `int(gpuid)` 会 ValueError 崩掉 |
| **E4** | `:644` | `os.environ['CUDA_VISIBLE_DEVICES'] = "1"` | 同 E2 | 同 E2（在 `__main__` 的守卫里） |
| **E5** | `:646-648` | `pynvml.nvmlInit()` + `nvmlDeviceGetCount()` | `gpu_count = torch.cuda.device_count()` | nvml 数**物理卡**、无视 `CUDA_VISIBLE_DEVICES`，4 卡节点恒为 4；要数的是「本作业看得见的卡」 |
| **E6** | `:653` | `if gpu_available is False or gpu_count != 4:` | `if gpu_available is False or gpu_count < 1:` | 单卡分配下 `!= 4` 恒真 → **作业必失败**。对照 `code_0820_prod_windows.py:717` 的 `< 1` |
| **E7** | `:655-656` | `cmd = "systemctl stop slurmd.service"` + `os.system(cmd)` | 删除，改为注释说明不得停节点守护进程 | 作业里停节点调度器：`User=nginx` 下权限拒绝（作业白挂）；若服务是 root 则**真把节点从池里摘掉** |
| **E8** | `:660` | 写 `SlurmStopLog.txt` 时措辞 `"{:s} stop {:s}"` | 改为 `"{:s} gpu-error {:s}"` | 不再停服务后，写 `stop` 会让运维误判节点已下线 |
| **E9** | `:159-160` | `if cloudpercent > cloudlimit: exit(0)` | 先建 SRLOG 并写 `Run skipped:` 终态行，再 `exit(0)` | 合法「按策略跳过」发生在创建 SRLOG（`:165`）**之前** → 与静默失败无法区分，会被闭环固化成成功 |

### 3.1 E3 的兜底形状

```python
try:
    _cvd = os.environ.get('CUDA_VISIBLE_DEVICES', '0').split(',')[0]
    gpuid = str(int(_cvd))
except (ValueError, TypeError):
    gpuid = '0'
```

只读取、不赋值。形如 `"0"` / `"0,1"` 时取整数；形如 `GPU-xxxx`（UUID）或 `MIG-...` 时兜底为 `'0'`——`gpuid` 的消费方（`util.py` 的 `pt_process` / `write_print_gpu_mem_info` / `write_print_nvsmi`）只把它当**物理卡号**用于打印，赋 0 不影响推理（推理设备由 `CUDA_VISIBLE_DEVICES` 的可见集合决定）。`probe_slurm.sh --deep` 会把真机注入形态打出来，据此可复核这段兜底是否够用。

### 3.2 E9 的终态行

```python
if cloudpercent > cloudlimit:
    _skip_log = osp.join(lq_path, "Debug", img_name[:-4] + "_SRLOG.txt")
    os.makedirs(osp.dirname(_skip_log), exist_ok=True)
    with open(_skip_log, 'w') as _srlog:
        _srlog.writelines("CloudPercent {:d} > CloudLimit {:d}, SR not needed.\n"
                          .format(cloudpercent, cloudlimit))
        _srlog.writelines("Run skipped: cloud limit exceeded\n")
    print('CloudPercent {:d} > CloudLimit {:d}, SR skipped by policy.'
          .format(cloudpercent, cloudlimit))
    exit(0)
```

- SRLOG 路径与 `:165` 的正式日志**同名同址**，末行固定以 `Run skipped:` 开头；
- 合法跳过 ⇒ SRLOG 存在且末行是 `Run skipped:`；静默失败 ⇒ 没有 SRLOG。二者从此可判；
- 退出码仍是 `0`（跳过不是失败），**不产生输出 tif** 这一点与契约一致；
- 消费方（P2 的作业内校验器）应把 `Run skipped:` 判为合法跳过：只要求 SRLOG 存在，不要求输出 tif。末行判定须**尾读字节取最后一个非空行**，不能用 `readlines()[-1]`（`:630` 写的 `"\nRun finished."` 无尾换行，且 `:624-626` 的 nvidia-smi 输出可能带尾换行）。

## 4. 变体**没有**改动的（边界）

- 算法、tile 循环、MTA-Grid、`writeTiff`、meta 回写逻辑一律不动；
- `exit(3)` 的 GPU 守卫仍在（只把判据从 `!= 4` 改成 `< 1`），退出码语义不变；
- `SlurmStopLog.txt` 仍会在 GPU 异常时追加一行（只改措辞，不改行为）；
- 空 `Suffix` 的破坏性语义（`util.py:1300-1315`：输出名 == 输入名 ⇒ 把输入改名）**本批不改**，真机验收一律用非空 suffix；
- `pynvml` 的 import 保留（E5 后本文件不再调用它，但 `util.py` 内部仍用）。

## 5. 本批未覆盖、已登记的风险

以下三条在 [slurm-integration.md](../status/slurm-integration.md) §2.4 已核实，**三条均不在变体范围内、本轮不修**。
2026-09-10（P3）已把它们作为「已知语义」回填进调用契约 [sr-pipeline-interface.md](sr-pipeline-interface.md) **§2.4**，
调用方按契约「规避方式」一列自行处理：

| 项 | 现象 | 归属 / 处置 |
|---|---|---|
| 空 `suffix` 覆盖语义 | `suffix=""` ⇒ 输出名 == 输入名 ⇒ 输出路径上的那个文件就是输入 ⇒ 输入被 `rename` 成 `*_NOSR.tif` | 平台 REST 入口 `_norm_sr_params` 空值回落到默认 `sr`、非法值 400，走不到这条；**agent 工具 `backend/tools/run_sr.py` 不校验 suffix、默认恰是 `""`**，能走到。契约 §2.4 第 1 条；**验收与生产一律传非空 suffix** |
| `sacct` 解析不可用 | 真机 `AccountingStorageType=accounting_storage/none` ⇒ `sacct` 恒返回非 0 且无输出；即便账务开启，`--format=State%20,ExitCode%10` 在状态串含空格时（如 `CANCELLED by 1000`）`split()` 也会错位 | 契约 §2.4 第 2 条；**终态改由退出码文件判定**（本文件 §6.3），`backend/services/slurm.py` 的 `sacct_status` 在真机路径上不再使用（仅假调度器/离线测试保留） |
| 对已超分场景会再超分一遍 | `util.py:1009-1011` 的 `exit()` 被注释掉，函数只打印 `already SRed before` 后返回原路径，不是幂等跳过 | 契约 §2.4 第 3 条；真重跑，非本次修复范围；调用方提交前自行判重 |

## 6. 运行期（P2）：批脚本 + 契约校验器 + 上机必验项

变体解决的是「脚本在 Slurm 作业里跑不跑得通」；这一节解决的是「跑完了算不算成功」。
两者合起来才是 §1 说的闭环：变体不再静默 `exit(0)`，校验器不再让静默失败被固化成成功。

### 6.1 契约校验器 `verify_sr_run.py`

作业内、`code_0817_prod.py` 之后运行的第二个进程（纯标准库、Python 3.6 兼容——
它跑在 SR 作业解释器里，不是平台 venv）：

```bash
<python> code_0817_prod.py -f <cfg.xml>
_sr_rc=$?
<python> verify_sr_run.py --config <cfg.xml> --sr-exit-code "$_sr_rc"
exit $?                      # 0=契约满足 / 90=契约不满足
```

判三条件（契约 §2），并额外用 `config.xml` 的 mtime 过滤上次残留：

| 条件 | 判据 | 失败后果 |
|---|---|---|
| 1 | SR 进程退出码 == 0 | 90 |
| 2 | SRLOG 存在、不早于 config.xml、**末行** == `Run finished.` | 90 |
| 3 | 输出 tif 存在、非 0 字节、不早于 config.xml | 90 |

`Run skipped:` 开头的末行（§3.2 的 E9 终态行）判为**合法跳过**：条件 1、2 成立即可，
**不要求**输出 tif。末行一律**尾读 4096 字节取最后一个非空行**（§3.2 的坑：
`:630` 写的 `"\nRun finished."` 无尾换行，`readlines()[-1]` 会取到 nvidia-smi 的尾巴）。

名字推导完全复刻生产脚本而不是「看起来差不多」：`img_name[:-4]` 而非 `os.path.splitext`；
`<Suffix />`（空元素）按 `util.get_cfg_value` 的语义是 **None**，即不加后缀、输出名
== 输入名（即 §5 那条已知破坏性语义，校验器**如实复现**，不会把一个管线自己认为撞名的
运行判成成功）；RC/SC 由 `<lq_path>/<basename>_meta.xml` 的 `<SolarAzimuth>` 是否为空决定，
tiftype 从 `<OPT>` 指的 yml 顶层 `tiftype`/`tif_type` 读，缺省 `.tif`。

**绝不抛异常**：config 不可读、meta 不可读、yml 缺、目录不存在、SRLOG 开不了……
一律收敛成「契约不满足 + stderr 说明缺哪条」，绝不半路死掉——校验器死掉等于没有退出码文件，
而「没有退出码文件」与「作业根本没跑」无法区分。

### 6.2 批脚本（`run_sr.build_batch_script`）

```
#SBATCH --gres=gpu:1            （E2/E4 之后选卡唯一来源）
#SBATCH --job-name=run_sr_<suffix>
#SBATCH --time / --cpus-per-task / --output / --error
#SBATCH --export=NONE
#SBATCH --partition=<SR_SLURM_PARTITION>     （配了才写）
export PYTHONUNBUFFERED=1
审计段：SLURM_JOB_ID / SLURM_JOB_GPUS / CUDA_VISIBLE_DEVICES / hostname /
        解释器 / config 路径 + nvidia-smi --query-gpu=index,uuid 按
        $CUDA_VISIBLE_DEVICES 过滤后的 GPU UUID
cd <SR_BUNDLE_DIR> || { …; exit 1; }
<python> code_0817_prod.py -f <cfg> ; _sr_rc=$?
<python> verify_sr_run.py --config <cfg> --sr-exit-code "$_sr_rc" ; exit $?
```

刻意不做的两件事：**不加 `set -e`**（与中段的 `||`、管道交互微妙，会在真正需要
「失败也继续跑校验器」时提前退出）；**脚本内不出现任何 `CUDA_VISIBLE_DEVICES` 赋值**
（只有审计段的只读取值）——E2/E4 的教训是任何赋值都会抹掉 `--gres` 的分配结果。

### 6.3 作业终态：退出码文件（C 方案）

真机 `AccountingStorageType=accounting_storage/none`，`sacct` 恒 rc=1、**永久不可用**，
`squeue` 只认活跃作业。所以终态由校验器写盘、平台读盘：

```
<DatarootLQ>/Debug/_SREXIT_<job_id>.txt
    job_id=… / sr_exit_code=… / verdict=0|90 / skip=0|1 / reason=…
```

与 SRLOG 同目录；文件名按 **job_id** 区分，避免同一场景的两次提交互相读错结论。
路径由一个约定锁死：校验器写它、`run_sr.exit_code_file_for()` 从 config.xml 反推它、
`slurm.job_status()` 读它，`backend/tests/test_sr_verify.py::NamingContractTests`
断言两侧一致。平台侧的映射是 `verdict==0 → COMPLETED`、`verdict!=0 → FAILED`
（**退出码 0 但契约不满足，算失败而不是成功** —— §2.3 毒药的正面修复）。

### 6.4 上机必验项（P2 新增，逐条给判据）

| # | 项 | 判据 | 不过怎么办 |
|---|---|---|---|
| V1 | **`--export=NONE` 是否切断 `LD_LIBRARY_PATH`** | 作业 `.err` 里有没有 import torch / GDAL / cv2 的 `ImportError: libXXX.so` 或 `OSError`。torch1.9.1+cu111 与 GDAL 都链系统库，而 `--export=NONE` 会把提交端的整个环境**包括 `LD_LIBRARY_PATH`** 一起丢掉 | **一行改动**：`build_batch_script` 里 `"#SBATCH --export=NONE"` → `"#SBATCH --export=ALL"`（`run_sr.py` 内唯一那一处），重跑 `pytest backend/tests/test_run_sr.py` |
| V2 | 退出码文件真的落盘 | 作业跑完后 `ls <DatarootLQ>/Debug/_SREXIT_<job_id>.txt`；内容 `verdict=0` | 看 `.err` 里 `verify_sr_run: 无法写退出码文件` → 作业用户对 `<lq_path>/Debug/` 没有写权限 |
| V3 | 平台没白等 | `/api/queue` 上该任务从 RUNNING → COMPLETED（**不是一直 UNKNOWN**） | UNKNOWN 表示平台反推的路径与作业写的对不上：核对 config.xml 的 `DatarootLQ` 与作业实际的 `lq_path` |
| V4 | GPU 审计段有内容 | `.out` 里 `CUDA_VISIBLE_DEVICES=` 与 `gpu <idx>, <uuid>` 行非空 | 空 ⇒ `--gres` 没给卡或注入形态是 UUID/MIG，见探针 `--deep` |
| V5 | 两套写权限 | nginx 能写 `SR_SLURM_WORK_DIR`（config.xml/脚本）；作业用户能写 `<lq_path>` 与 `<lq_path>/Debug/` | §2.6；用 `sudo -u nginx touch <dir>/x` 直接验 |
| V6 | `SR_SLURM_WORK_DIR` 是共享盘 | `sinfo -N` 节点数 > 1 时，该目录在任意计算节点上都存在 | 默认 `/tmp/sr_agent_work` 是**本机**路径，多节点下作业秒挂 |

## 7. 怎么校验这份变体

```bash
python -m pytest backend/tests/test_sr_variant.py -q   # 23 项：锚点命中 / 禁用形态 / 确定性 / 坏锚点 exit 2
python -m pytest backend/tests/test_sr_verify.py -q    # 39 项：契约三条件全分支 + 命名约定
python SR_code/tools/gen_slurm_variant.py --check      # 逐字节新鲜度
```

测试钉住的四类不变量：

1. 每条锚点在真源中**恰好命中 1 次**（命中数不符 ⇒ 生成器 exit 2，绝不静默跳过）；
2. 变体里 `CUDA_VISIBLE_DEVICES` 赋值 0 次、`systemctl` 0 次、`gpu_count != 4` 0 次；
3. 契约标记仍在：`Run finished.`、`exit(3)`、`Run skipped:`；
4. 连跑两次字节相同；手改变体 ⇒ `--check` exit 1；坏锚点 ⇒ exit 2 且**不落任何文件**。

## 8. 关联

- 决策与分批计划：[docs/status/slurm-integration.md](../status/slurm-integration.md)
- 调用契约：[sr-pipeline-interface.md](sr-pipeline-interface.md) **v1.5**（退出码 90 与云量跳过终态已回填至 §2.3；三条已知语义见 §2.4；`--gres` 选卡决议见 §7.1/§9）
- 真机分阶段验收：[docs/status/slurm-acceptance.md](../status/slurm-acceptance.md)（A 探针 → B 裸 Slurm 冒烟 → C 单场景真 SR → D 平台四条链路结论；§0.2 是变体的上机安装位置）
- 部署侧总览：[deploy/README.md](../../deploy/README.md) §七「Slurm 接入」
- 只读探针：[deploy/slurm/probe_slurm.sh](../../deploy/slurm/probe_slurm.sh)（上机确认分区 / GRES / 记账 / 路径 / 权限）
- 生成器：[SR_code/tools/gen_slurm_variant.py](../../SR_code/tools/gen_slurm_variant.py)
