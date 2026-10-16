# SR 生产脚本 · 平台部署变体

> 日期：2026-10-15 · 状态：**已定** · 读者：接手 Agent 窗口 + 运维
> 前置：调用契约 [sr-pipeline-interface.md](sr-pipeline-interface.md)（唯一契约）、本机执行现状
> [../status/sr-local-execution.md](../status/sr-local-execution.md)（链路、终态、切换）
> 本文档是**静态映射表**：不含生成时间戳，变体的可复现性由 `provenance.json` + `--check` 保证。

## 1. 一句话

`SR_code/code_0817_prod.py` 逐字节不动，是唯一真源；平台部署用的可运行版本由
[SR_code/tools/gen_platform_variant.py](../../SR_code/tools/gen_platform_variant.py)
按锚点替换表**机械产出**到 `SR_code/variants/`。本文档是那张替换表的可读版本。

为什么不是「复制一份改改」：真源里有 5 类会让平台这一跑失败、或读错输入那一份的代码
（硬编码选卡、数物理卡、停宿主机服务、写共享停服日志、静默 `exit(0)`、输入定位只认已超分的那一份）。
手改副本无法审计，也无法在真源更新后被可靠地重构。锚点替换把「改了什么、为什么改」变成
可 diff、可校验、可复现的产物。

## 2. 产物与复现

| 文件 | 作用 |
|---|---|
| `SR_code/variants/code_0817_prod_platform.py` | 部署变体本体（首部带 `GENERATED FILE — DO NOT EDIT` 横幅） |
| `SR_code/variants/code_0817_prod_platform.diff` | 与真源的 unified diff，评审看这一份即可 |
| `SR_code/variants/code_0817_prod_platform.provenance.json` | 真源/变体 sha256、每条 edit 的锚点哈希与命中数（**无时间戳**） |
| `SR_code/variants/.gitattributes` | `* text eol=lf`，防止 Windows 检出让变体变形 |

```bash
python SR_code/tools/gen_platform_variant.py          # 重新生成（写盘）
python SR_code/tools/gen_platform_variant.py --check  # 只比对；过期 exit 1
```

退出码：`0` 正常 / `1` 过期（仅 `--check`）/ `2` 锚点命中数不符（不落任何文件）。

生成器读入时做 CRLF→LF 归一、写出统一 `\n`，因此换行风格不影响产物字节；`--check` 是逐字节比较，
任何手改（包括加一行注释）都会报 `STALE`。

## 3. 差异表（E1–E10）

真源基线 sha256（CRLF→LF 归一后）：
`03c9b4fc3f64cfd5e3b331128be5a09477d5ebe0362b0e9e4927fcdd356d40af`

| # | 真源位置 | 真源写法 | 变体写法 | 理由 |
|---|---|---|---|---|
| **E1** | `code_0817_prod.py:27` | 模块级 `lib = npct.load_library(...)`，无保护 | 包 `try/except Exception`，兜底 `lib = None` | 缺 `.so` 时进程在 **import 期**崩溃，且崩在任何日志写出之前，报错无法定位。对照 `code_0820_prod_windows.py:25-28` |
| **E2** | `:146` | `os.environ['CUDA_VISIBLE_DEVICES'] = "0"` | 换成注释「由启动方（`SR_LOCAL_GPU`）设定，绝不覆盖」 | 无条件覆盖会抹掉部署选的卡，所有跑挤物理卡 0 |
| **E3** | `:147` | `gpuid = '0'` | 从 env 取首个设备并 `int()`，失败兜底 `'0'` | `gpuid` 只用于日志与 `pynvml` 打印（**物理**索引）。注入形态可能是 UUID / MIG，`:624`、`:178` 的 `int(gpuid)` 会 ValueError 崩掉 |
| **E4** | `:644` | `os.environ['CUDA_VISIBLE_DEVICES'] = "1"` | 同 E2 | 同 E2（在 `__main__` 的守卫里） |
| **E5** | `:646-648` | `pynvml.nvmlInit()` + `nvmlDeviceGetCount()` + `nvmlShutdown()` | `gpu_count = torch.cuda.device_count()` | nvml 数**物理卡**、无视 `CUDA_VISIBLE_DEVICES`，4 卡机器恒为 4；要数的是「这一跑看得见的卡」 |
| **E6** | `:653` | `if gpu_available is False or gpu_count != 4:` | `if gpu_available is False or gpu_count < 1:` | 只看得见一张卡时 `!= 4` 恒真 → **这一跑必失败**。对照 `code_0820_prod_windows.py:717` 的 `< 1` |
| **E7** | `:655-656` | `cmd = "systemctl stop slurmd.service"` + `os.system(cmd)` | 删除该两行，换成注释「失败的一跑不得停宿主机服务，用退出码报」 | 从跑里停宿主机守护进程：权限不足则白写一行错误；权限够则**真把节点从池里摘掉**（集群上）或停掉与本跑无关的服务（本机上） |
| **E8** | `:660` 起 | 向 `/DiskArray/ProductionSchedule/config/SlurmStopLog.txt` 追加 `"{:s} stop {:s}"` | 删除该段，换成注释 | 那份共享日志被读作「这台机器退出了产品队列」；本机这一跑失败不该这么声称。失败由上面的日志行与退出码表达 |
| **E9** | `:159-160` | `if cloudpercent > cloudlimit: exit(0)` | 先建 SRLOG 并写 `Run skipped:` 终态行，再 `exit(0)` | 合法「按策略跳过」发生在创建 SRLOG（`:165`）**之前** → 与静默失败无法区分，会被闭环固化成成功 |
| **E10** | `:151` | `img_abs_path = util.get_l1_pan_tif_rcsc(lq_path, tiftype, sr_previous_step)` | 改调 `util.get_l1_pan_tif_rcsc_nosr(...)` | 这一跑读的是未超分那一份：跑前脚本已把 `<输入 stem>_NOSR.tif` 拷到本体名，于是本体是 1× 内容，严格判据 `1.0 < size < 1.8 GiB` 会把它当成 already SRed；单边 `size < 1.8 GiB` 才放行 |

### 3.1 E3 的兜底形状

```python
try:
    _cvd = os.environ.get('CUDA_VISIBLE_DEVICES', '0').split(',')[0]
    gpuid = str(int(_cvd))
except (ValueError, TypeError):
    gpuid = '0'
```

只读取、不赋值。形如 `"0"` / `"0,1"` 时取整数；形如 `GPU-xxxx`（UUID）或 `MIG-...` 时兜底为 `'0'`
—— `gpuid` 的消费方（`util.py` 的 `pt_process` / `write_print_gpu_mem_info` / `write_print_nvsmi`）
只把它当**物理卡号**用于打印，赋 0 不影响推理（推理设备由 `CUDA_VISIBLE_DEVICES` 的可见集合决定）。
运行脚本的审计段会把 `CUDA_VISIBLE_DEVICES` 与 `nvidia-smi` 按它过滤后的 `gpu <idx>, <uuid>`
打进日志，据此可复核这段兜底是否够用。

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
- 消费方（作业内校验器）把 `Run skipped:` 判为合法跳过：只要求 SRLOG 存在，不要求输出 tif。
  末行判定**尾读字节取最后一个非空行**，不能用 `readlines()[-1]`（`:630` 写的 `"\nRun finished."`
  无尾换行，且 `:624-626` 的 nvidia-smi 输出可能带尾换行）。

### 3.3 E10 的输入来源

```python
    # The body holds the un-superresolved copy by the time this runs (the
    # job script lands it there before invoking this file), so the input
    # is 1x and the strict "1.0 < size < 1.8 GB" gate would reject it.
    # get_l1_pan_tif_rcsc_nosr uses the one-sided "size < 1.8 GB" gate.
    img_abs_path = util.get_l1_pan_tif_rcsc_nosr(lq_path, tiftype, sr_previous_step)
```

两个定位函数的分支对照（`util.py`，只列尺寸放行的那一侧）：

| 分支 | `get_l1_pan_tif_rcsc`（严格） | `get_l1_pan_tif_rcsc_nosr`（E10 改用的） |
|---|---|---|
| `previous_step == "RC"` | 读 `<lq_path>/PAN.tif`，`1.0 < s < 1.8` GiB | 读 `<lq_path>/PAN.tif`，`s < 1.8` GiB |
| 否则（SC） | 读 `<lq_path>/<目录名>.tif`，`0.0 < s < 1.1` / `1.5 < s < 1.7` / `3.8 < s < 4.1` | 读 `<lq_path>/<目录名>_NOSR.tif`，`0.2 < s < 1.1` / `1.5 < s < 1.7` / `3.8 < s < 4.3` |

- RC 两个函数读的是**同一个路径**，差别只在判据；跑前脚本把本体换成 1× 内容后尺寸落在 `1.0` 之下，
  严格判据拒收、单边判据放行。所以「读未超分那一份」在 RC 场景由**两处合力**：跑前脚本把源头拷到
  本体名，E10 放宽判据。
- SC 两个函数读的是**不同的路径**，`_nosr` 自己就指向 `<目录名>_NOSR.tif`，判据下限也从 `0.0`
  抬到 `0.2`（排除空壳/占位文件）。
- 两个函数的 `previous_step` 都由 `util.check_sr_previous_step` 定：`<lq_path>/<basename>_meta.xml`
  的 `<SolarAzimuth>` 为空 ⇒ `"RC"`，非空 ⇒ `"SC"`。

## 4. 变体**没有**改动的（边界）

- 算法、tile 循环、MTA-Grid、`writeTiff`、meta 回写逻辑一律不动；
- `exit(3)` 的 GPU 守卫仍在（只把判据从 `!= 4` 改成 `< 1`），退出码语义不变；
- 空 `Suffix` 的破坏性语义（`util.py:1300-1315`：输出名 == 输入名 ⇒ 把输入改名）**变体不改这条**；
  平台两条提交入口都在 `run_sr.normalize_suffix` 处回落到非空默认值，走不到它；
- `pynvml` 的 import 保留（本文件不再调用它，`util.py` 内部仍用）。

## 5. 已知语义（变体不覆盖，校验器兜住）

| 项 | 现象 | 处置 |
|---|---|---|
| `util.py` 里多处静默退出 | 生产路径上可达的静默退出共 5 条，全部发生在创建 SRLOG（`code_0817_prod.py:165`）**之前**：`code_0817_prod.py:160`（云量跳过，见 §3.2 E9）、`util.py:999`（RC 步 `PAN.tif` 存在但体积不在 1.0–1.8 GiB）、`util.py:1002`（RC 步 `PAN.tif` 不存在）、`util.py:1014`（SC 步场景 tif 不存在）、`util.py:1106`（`get_cfg_value`：xml 订单文件不存在，`code_0817_prod.py:117` 起即调用，config.xml 或 meta.xml 有误都会命中）。写法是 `print` 后裸 `exit()`，退出码即 0 ⇒ 跑完既无 SRLOG 也无产物，退出码却是 0 | 这些路径**不在变体范围**（改了就等于改算法前提）。作业内校验器要求「SRLOG 存在 + 末行 `Run finished.` + 输出 tif 存在」，因此这类跑被判契约不满足 ⇒ 平台记 FAILED，可重投 —— 这正是 §2.3 说的「退出码 0 不等于成功」要挡住的东西 |
| 对已超分场景会再超分一遍 | 严格版 SC 分支的 `exit()` 被注释（`util.py:1009-1011`，函数只打印 `already SRed before` 后返回原路径），不是幂等跳过；生产 bundle 的 `utils/util.py` 里那处 `exit()` 是活的，行为是直接结束 | 真重跑。平台的重复提交保护只按参数指纹（同参数 ⇒ 复用或 409），不按影像是否已超分判重 |
| 云量跳过与静默失败 | 见 §3.2 的 E9 | 变体已把跳过写成 `Run skipped:` 终态行；校验器据末行把跳过判为成功（不出产物） |

## 6. 运行期：运行脚本 + 契约校验器 + 就位脚本

变体解决「脚本在本机这一跑里跑不跑得通」；这一节解决「跑完了算不算成功」。两者合起来才是闭环：
变体不再静默 `exit(0)`，校验器不让静默失败被固化成成功。

### 6.1 契约校验器 `SR_code/variants/verify_sr_run.py`

跑在 SR 脚本**之后**的独立进程（纯标准库、Python 3.6 兼容 —— 它跑在 SR 解释器里，不是平台 venv）：

```bash
<python> sr_prep_scene.py --body … --nosr … --backup … --mask … --mask-1x …   # 只有「换源头」的那一跑
<python> code_0817_prod_platform.py -f <cfg.xml>
_sr_rc=$?
<python> verify_sr_run.py --config <cfg.xml> --sr-exit-code "$_sr_rc" --job-id <id> \
                          [--land-on-body … --backup … --mask-1x …]
exit $_v_rc                  # 0=契约满足 / 90=契约不满足
```

判三条件（契约 §2），并额外用 `config.xml` 的 mtime 过滤上次残留：

| 条件 | 判据 | 失败后果 |
|---|---|---|
| 1 | SR 进程退出码 == 0 | 90 |
| 2 | SRLOG 存在、不早于 config.xml、**末行** == `Run finished.` | 90 |
| 3 | 输出 tif 存在、非 0 字节、不早于 config.xml | 90 |

`Run skipped:` 开头的末行（§3.2 的 E9 终态行）判为**合法跳过**：条件 1、2 成立即可，**不要求**输出 tif。
末行一律**尾读 4096 字节取最后一个非空行**（§3.2 的坑）。

`--job-id` 是**必填**：本机执行没有调度器环境变量可读，运行编号由运行脚本显式传入，校验器用它命名
判定文件。名字推导完全复刻生产脚本而不是「看起来差不多」：`img_name[:-4]` 而非 `os.path.splitext`；
`<Suffix />`（空元素）按 `util.get_cfg_value` 的语义是 **None**，即不加后缀、输出名 == 输入名
（校验器**如实复现**，不会把一个管线自己认为撞名的运行判成成功）；RC/SC 由
`<lq_path>/<basename>_meta.xml` 的 `<SolarAzimuth>` 是否为空决定，tiftype 从 `<OPT>` 指的 yml 顶层
`tiftype`/`tif_type` 读，缺省 `.tif`。

**归位（`--land-on-body` / `--backup` / `--mask-1x`，三个都可选，成组传）**：判完契约、写 verdict 之前
调一次 `land_product`。**都不传 ⇒ 校验器只读**。传了则按契约结论分四种结局：

| 结局 | 触发 | 动作 |
|---|---|---|
| `landed` | 契约过、产物在 | 记下 `os.stat(产物).st_size` → `os.replace(产物, 本体)` → 复核本体大小一致；通过则删备份与掩码临时件，verdict 的 `output` 改成本体名（产物已不在原名下，免得读者去原名找） |
| `restored` | 契约不过，**或** 合法跳过（`skip`） | 删掩码临时件、从备份把本体恢复成跑前那份；契约不过的 verdict 仍是 90，跳过的仍是 0 |
| `missing` | 契约过、但产物名不存在 | 恢复本体，并记一条「产物不在，无法归位」→ 90 |
| `failed` | `replace` 或归位后复核抛异常/大小不符 | 恢复本体，记下异常类型与说明 → 90 |

- 备份不存在时只删备份、不报错：备份由 `sr_prep_scene.py` **拷之前先留**，不存在说明本体从没被换过。
- 归位这一环的成败**写进同一条 verdict**：没人归位的跑平台读到 90（FAILED），不会被报成 COMPLETED。

**绝不抛异常**：config 不可读、meta 不可读、yml 缺、目录不存在、SRLOG 开不了、归位失败……一律收敛成
「契约不满足 + stderr 说明缺哪条」，绝不半路死掉 —— 校验器死掉等于没有判定文件，而「没有判定文件」
与「作业根本没跑」无法区分。

### 6.2 运行脚本（`run_sr.build_run_script`）

```
#!/bin/bash                                    （无 #SBATCH：本机执行器直接起它）
# Generated by backend/services/run_sr.build_run_script …
export PYTHONUNBUFFERED=1
审计段：JOB_ID / CUDA_VISIBLE_DEVICES / hostname / 解释器 / 选中的 SR 脚本 /
        config 路径 / nvidia-smi 按 $CUDA_VISIBLE_DEVICES 过滤后的 gpu index,uuid
        / IN PLACE 提示（输出落在场景目录、输入不改名不删除）
cd <SR_BUNDLE_DIR> || { …; exit 1; }
_prep_rc=0                                          # 仅「这一跑要换源头」时才有这五行
<python> sr_prep_scene.py --body … --nosr … --backup … --mask … --mask-1x … || _prep_rc=$?
if [ "$_prep_rc" -ne 0 ]; then
    echo "sr_prep_scene failed (exit $_prep_rc); skipping the SR run, the verifier restores the body" >&2
    _sr_rc=$_prep_rc
else
    <python> code_0817_prod_platform.py -f <cfg> ; _sr_rc=$?
fi
<python> verify_sr_run.py --config <cfg> --sr-exit-code "$_sr_rc" --job-id <id> \
                         [--land-on-body … --backup … --mask-1x …]          # 仅「要换源头」
_v_rc=$?
exit $_v_rc
```

三段程序名都由 env 定、缺省即 `sr_prep_scene.py` / `code_0817_prod.py` / `verify_sr_run.py`，
`cd` 之后按相对名解析（三者都拷在 bundle 根）。**生成时**就把名字烘进文本，改 env 下次提交生效。

跑前脚本失败**不 `exit 1`**：它的码被带进 `_sr_rc` 并跳过 SR 那一步，让校验器照常跑完、写 verdict
（同时从备份恢复本体）。否则这一跑没有判定文件，平台只能读 UNKNOWN，而 UNKNOWN 会挡住重投。

刻意不做的两件事：**不加 `set -e`**（与中段的 `||`、管道交互微妙，会在真正需要「失败也继续跑校验器」
时提前退出）；**脚本内不出现任何 `CUDA_VISIBLE_DEVICES` 赋值**（只有审计段的只读取值）—— E2/E4 的教训
是任何赋值都会抹掉部署选的卡。选卡由 `SR_LOCAL_GPU` 经 `backend/config.sr_runtime` 决定，
`local_exec` 把它设进子进程**环境**。

### 6.3 跑完的终态：判定文件

本机执行没有调度器，也没有可用的账务查询，终态由校验器写盘、平台读盘：

```
<lq_path>/Debug/_SREXIT_<job_id>.txt
    job_id=… / sr_exit_code=… / verdict=0|90 / skip=0|1 / reason=…
```

与 SRLOG 同目录；文件名按 **job_id** 区分，避免同一场景的两次提交互相读错结论。路径由一个约定锁死：
校验器写它、`run_sr.exit_code_file_for()` 从 config.xml 反推它、`local_exec.status()` 读它，
`backend/tests/test_sr_verify.py::NamingContractTests` 断言两侧一致。平台侧的映射是
`verdict==0 → COMPLETED`、`verdict!=0 → FAILED`（**退出码 0 但契约不满足，算失败而不是成功**）。

### 6.4 就位脚本 `SR_code/variants/sr_prep_scene.py`

「这一跑读未超分那一份」的落地点，在 SR 脚本之前跑，三步：

1. **备份**：本体先拷到 `Debug/<本体名>.srbak`，**只在不存在时留** —— 作业被硬杀后重跑，第二次不能
   把已经被换成 1× 的本体当原件存档；
2. **就位**：把 `<输入 stem>_NOSR.tif` **拷**到本体名（不是改名，归档那份原地不动）；
3. **掩码**：把掩码重采样到源尺寸，写 `Debug/<掩码名>.sr1x`。缩小取块内最大（ROI 像素不丢）、
   放大取最近邻；有 nodata 的掩码先把空洞填成背景再取块最大；源有网格时把 geotransform 带过去
   （恒等 geotransform 不带，那等于没有网格）。

任一步失败 ⇒ 非 0 退出，由 §6.2 的脚本把它带进 `_sr_rc`，校验器随后从备份恢复本体。

## 7. 怎么校验这份变体

```bash
python -m pytest backend/tests/test_sr_variant.py -q      # 25 项：锚点命中 / 禁用形态 / 确定性 / 坏锚点 exit 2
python -m pytest backend/tests/test_sr_verify.py -q       # 49 项：契约三条件全分支 + 归位四种结局 + 命名约定
python -m pytest backend/tests/test_sr_prep_scene.py -q   # 12 项：备份「只在不存在时留」/ 拷贝就位 / 缩放方向 / 退出码
python SR_code/tools/gen_platform_variant.py --check      # 逐字节新鲜度
```

（后两份里依赖 GDAL 的用例在开发机上跳过，那半边留给 SR 环境跑。）

`sr_prep_scene.py` 与 `verify_sr_run.py` 都是**手写**的作业内脚本，不由生成器产出；生成器只管
`code_0817_prod_platform.py`（+ `.diff` / `.provenance.json` / `.gitattributes`）。三份脚本一起上车，
见 [../status/sr-local-execution.md](../status/sr-local-execution.md) §三。

测试钉住的四类不变量：

1. 每条锚点在真源中**恰好命中 1 次**（命中数不符 ⇒ 生成器 exit 2，绝不静默跳过）；
2. 变体里 `CUDA_VISIBLE_DEVICES` 赋值 0 次、`systemctl` 0 次、`gpu_count != 4` 0 次；
3. 契约标记仍在：`Run finished.`、`exit(3)`、`Run skipped:`；
4. 连跑两次字节相同；手改变体 ⇒ `--check` exit 1；坏锚点 ⇒ exit 2 且**不落任何文件**。

## 8. 关联

- 调用契约：[sr-pipeline-interface.md](sr-pipeline-interface.md)（退出码 90、云量跳过终态、三条已知语义、选卡决议）
- 本机执行的现状/切换/回滚：[../status/sr-local-execution.md](../status/sr-local-execution.md)
- 真机验收：[../status/real-machine-acceptance.md](../status/real-machine-acceptance.md) §D、部署步骤 [deploy/README.md](../../deploy/README.md) §七
- 只读探针：[deploy/probes/probe_structure.sh](../../deploy/probes/probe_structure.sh)（上机确认盘阵结构 / 输入判据 / 路径权限）
- 生成器：[SR_code/tools/gen_platform_variant.py](../../SR_code/tools/gen_platform_variant.py)
