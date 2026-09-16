# SR 管线调用契约（SR_CODE 生产管线）

> **版本**：v1.6 · **日期**：2026-09-16 · **适用对象**：`sr_agent_web` 后端与 agent 开发者
> **状态**：已定（v1.6，2026-09-16 订正 §6 源图去向的条件）· **归档定位**：docs/sr_code/ 类目（SR_CODE 生产管线文档簇，随 SR_CODE 版本迭代更新）
>
> **文档定位**：本文档是平台与超分管线之间的唯一接口规格。内容只覆盖"怎么调用、传入什么、返回什么、如何判断成败"，不描述管线内部算法。
>
> **配套文档**：算法实现见 `sr-pipeline-overview.md`（同簇 docs/sr_code/），产品需求见 `docs/planning/gui-requirements.md`。三者相互补充，本文档只约定调用契约。
>
> **适用范围**：本文档与具体部署文件名解耦。脚本名、Python 路径、conda 环境等均为部署期变量（随环境不同而变化），契约逻辑不依赖具体文件名。
>
> **v1.5（2026-09-10）**：补入 Slurm 部署路径的终态语义——§2 拆为 2.1 退出码表 / 2.2 三项成功条件 / 2.3 退出码 90 与云量跳过终态 / 2.4 三条已知语义；§7.1 由「部署前必须处理」改为「部署变体已删除该守卫」并指向变体路径；§9 记录 `--gres` 选卡决议、`<GPUIDS>` 降级为审计字段。
>
> **v1.4（2026-08-17）**：全面润色——新增术语表、统一术语表达、去除口语化与网络用语、规范表格与章节编号；技术内容与 v1.3 一致，仅修正 §1 交叉引用笔误。

## 术语表（Terminology）

本文档中的专业名词定义如下，后续章节直接引用，不再重复解释。

| 术语 | 全称 / 别名 | 含义 |
|---|---|---|
| SR | Super-Resolution，超分辨率 | 用算法将低分辨率输入图像放大并增强细节；本管线固定为 2 倍 |
| SFSR | Satellite SR，卫星超分 | 面向卫星影像的超分任务，本文档所述管线即此类 |
| PAN | Panchromatic，全色图像 | 单波段灰度图像，空间分辨率高、无色彩，是本管线的超分输入 |
| ROI | Region of Interest，感兴趣区域 | 掩码中取值为 1 的区域；管线只对 ROI 做模型超分，其余区域用双三次插值 |
| 掩码（mask） | Mask | 与输入图像分辨率一致的二值图像（0=背景，1=ROI） |
| tile | 分块 / 瓦片 | 将大图按固定尺寸（如 1000×1000）切成小块，逐块送入模型，避免显存溢出 |
| GSD | Ground Sample Distance，地面采样距离 | 单个像元对应的地面尺寸（米/像元）。meta.xml 的 ImageRowGSD / ImageColumnGSD 记录行、列两个方向 |
| DN | Digital Number，数字量化值 | 图像像元的原始整数值。max_DN 为位深决定的最大取值（位深 12 → 0~4095） |
| DataBits | 位深 | 图像每个像元用多少位存储 |
| CloudPercent | 云量百分比 | 图像云覆盖率；超过 CloudLimit 阈值时任务不执行 |
| 退出码 | returncode / exit code | 进程结束时的返回码：0 表示进程正常结束，非 0 表示发生异常。注意：0 不代表任务成功，见 §2 |
| 静默失败 | silent failure | 进程正常结束（退出码 0）但未完成任务。本管线的多种错误均表现为静默失败，只能依据输出产物判断，见 §2 |
| 退出码文件 | verdict file | `<DatarootLQ>/Debug/_SREXIT_<job_id>.txt`，由作业内契约校验器写、平台读。Slurm 账务关闭下判定作业终态的**唯一**依据，见 §2.3 |
| 合法跳过 | skip by policy | 云量超 `CloudLimit` 而不执行超分：退出码 0、不产输出 tif，但 SRLOG 末行以 `Run skipped:` 开头。与静默失败的区别就在这条终态行，见 §2.3 |
| 黑盒调用 | black-box | 调用方只按文档约定传参、读取结果，不了解也不修改脚本内部实现 |
| subprocess | 子进程调用 | 平台启动超分脚本进程的方式，用于向其传递配置并获取输出 |
| MTA-Grid | 网格对齐 | 将输入与模型的网格原点对齐的模块，可通过 GridAlign=false 关闭 |
| GPUIDS | GPU 编号 | config.xml 中声明的 GPU 编号，部署脚本据此选择显卡 |
| 冷启动 | cold start | 进程启动时加载模型并初始化，约 8–9 秒，每次调用都会发生 |
| dtype | 数据类型 | 像素数值类型；输出 tif 为 uint16（16 位无符号整数） |
| RC 步 / SC 步 | — | 管线依据 meta 的 SolarAzimuth 是否为空选择的两种输入分支。空 → RC 步（读 `PAN.tif`）；非空 → SC 步（读 `<文件夹名>.tif`），见 §4 |

## 1. 调用方式

```bash
<conda_python> <deployed_sfsr_script>.py -f <config.xml>
# 例（服务器）：
/run/media/root/SSD/program/anaconda/installed/envs/torch1.9.1py36/bin/python \
  code_0817_prod.py -f /tmp/cfg_xxx.xml
```

- `-f` 是唯一必传参数，指定任务级配置文件（config.xml，见 §3）。
- 一次进程调用处理一幅图像（一个任务），进程结束后返回。
- 管线只提供命令行接口：无守护进程、无 HTTP 接口。平台通过 subprocess 启动进程，并解析其退出码与输出文件判断结果。

## 2. 退出码与成败判定

### 2.1 退出码表

进程退出码及其含义如下表所示。

| 退出码 | 含义 | 判定方式 |
|---|---|---|
| 0 | 情况较多，包含多种"进程正常结束但未完成任务"的情形：任务正常完成；云量超过阈值被过滤（**按策略跳过**，见 §2.3）；配置文件缺失；输入 PAN 缺失；RC 步 PAN 尺寸不符。以上均表现为 `print` + `exit(0)` | 仅凭退出码无法区分，需结合输出产物判断（见 §2.2） |
| 1 | 运行期异常（进程输出带 traceback）：掩码路径指向目录、config.xml 标签缺失（IndexError）、`int("θ")` 转换失败（ValueError）、模型异常等 | 从进程输出或 SRLOG 中读取 traceback |
| 3 | GPU 相关异常：无 GPU 或**可见** GPU 数量不足（真源判据为"不等于 4"，部署变体改为"小于 1"，见 §7.1） | 见 §7.1 |
| **90** | **契约不满足**（Slurm 部署路径新增）：SR 进程已结束，但 §2.2 的三项条件未全部成立。由作业内契约校验器判定并以此码退出，同时把结论写入退出码文件（见 §2.3） | 读退出码文件的 `verdict` / `reason` 字段 |

### 2.2 三项成功条件

任务成功判定须同时满足以下三项条件：

1. 进程退出码为 0；
2. 日志文件 `<DatarootLQ>/Debug/<图像名>_SRLOG.txt` 存在，且末行为 `Run finished.`；
3. 输出 tif 文件存在。

> 重要：退出码 0 不能单独作为成功依据。配置文件缺失、输入 PAN 缺失、尺寸不符等失败均以退出码 0 结束，只有同时满足上述三项条件，才能判定任务成功。
>
> 末行判定须**尾读字节、取最后一个非空行**，不能用 `readlines()[-1]`：正式日志写的 `"\nRun finished."` 无尾换行，且紧跟其前的 nvidia-smi 输出可能带尾换行。

### 2.3 退出码 90 与云量跳过的终态

Slurm 部署路径上，SR 脚本之后还会跑一个**作业内契约校验器**（`SR_code/variants/verify_sr_run.py`），把 §2.2 三条件判成机器可读的一行结论：

| 结论 | 校验器退出码 | 退出码文件 | 平台侧 `job_status` |
|---|---|---|---|
| 契约满足 | 0 | `verdict=0 / skip=0` | COMPLETED |
| **合法跳过**（云量超 `CloudLimit`） | 0 | `verdict=0 / skip=1` | COMPLETED |
| 契约不满足 | 90 | `verdict=90 / reason=…` | **FAILED** |

- 退出码文件固定写在 `<DatarootLQ>/Debug/_SREXIT_<job_id>.txt`，与 SRLOG 同目录，按 **job_id** 区分同一场景的多次提交（避免两次提交互相读错结论）；
- **合法跳过**的判据是 SRLOG 末行以 `Run skipped:` 开头——云量过滤发生在正式日志建立**之前**，部署变体让它先落一条终态行再 `exit(0)`（`sr-slurm-deploy-variant.md §3.2`）。跳过时**不产生输出 tif**，第 3 条条件不适用，校验器只要求条件 1、2；
- 校验器**绝不抛异常**：config 不可读、meta 不可读、yml 缺、目录不存在……一律收敛为「契约不满足 + stderr 说明缺哪条」。校验器自己死掉等于没有退出码文件，而「没有退出码文件」与「作业根本没跑」无法区分；
- 因此调用方的判据是**退出码文件，而不是进程退出码 0**。退出码 90 的语义就是「退出码 0，但不能算成功」——它是 §2.1 那批静默失败（全部 `exit(0)` 且发生在 SRLOG 建立之前）的机器可读化。

### 2.4 已知语义

以下三条是核对源码与真机实测确认的**现有行为**，本批**未修复**，调用方须按"规避方式"一列自行处理。

| # | 语义 | 现象 | 规避方式 |
|---|---|---|---|
| 1 | **空 suffix 覆盖语义** | `<Suffix></Suffix>`（或 `<Suffix/>`）被读作 None（§3）⇒ 输出名 == 输入名。`util.writeTiff` 会先把输入重命名为 `*_NOSR.tif` 再以原名写出结果，数据不会真被覆盖，但**输入文件被改名**——对调用方是破坏性副作用，且第二次运行将找不到输入 | 一律传**非空** `Suffix`；真机验收与生产均按此执行 |
| 2 | **`sacct` 解析不可用** | 真机 `AccountingStorageType=accounting_storage/none`，`sacct` 恒返回非 0 且无输出，作业终态根本取不到；即便账务开启，`--format=State%20,ExitCode%10` 在状态串含空格时（如 `CANCELLED by 1000`）`split()` 会错位 | 终态一律读 §2.3 的退出码文件；`backend/services/slurm.py` 的 `sacct_status` 在真机路径上不使用（仅假调度器/离线测试保留） |
| 3 | **对已超分场景会再超分一遍** | `util.py:1009-1011` 的"已超分则跳过"分支里 `exit()` 已被注释掉，函数只打印 `already SRed before` 然后**返回原路径**，管线继续走完整个超分流程——不是幂等跳过 | 调用方在提交前自行判重（如检查该 suffix 的输出 tif 是否已存在）；平台不做拦截，重跑就是真重算 |

## 3. config.xml 配置契约

config.xml 是任务级配置，经 `-f` 传入。下表列出脚本读取的全部标签及约束。

| 标签 | 必填 | 类型 | 说明 |
|---|---|---|---|
| `DatarootLQ` | 是 | 路径 | 任务数据目录，包含输入图像与 meta.xml。路径中的反斜杠会被替换为 `/` |
| `OPT` | 是 | 路径 | 指向模型与分块参数的 YAML 文件（含 t_ht / t_wd / pad / scale / network_G 等） |
| `CloudLimit` | 是 | 整数 | 云量阈值（百分比）。图像云量超过该值时任务不执行：退出码 0、不产生输出 tif。Slurm 路径下这是**合法跳过**（退出码文件 `skip=1`，平台记 COMPLETED），见 §2.3 |
| `DeleteOriTifNeeded` | 是 | 字符串 | 是否删除原图。判定规则：仅当取值为 `TRUE`（不区分大小写）时进入删除分支；其余取值（含 `False` / `false`）保留源图像。**删除分支作用于输出路径上的同名文件，不是源图**（见 §6）：`Suffix` 为空、输出名 == 输入名时才等同删除源图；`Suffix` 非空时删掉的是上一次的同名输出，源图仍留在原位。建议平台统一填写 `false` |
| `SRScale` | 是 | 字符串 | 超分倍率，**按字符串比较**。`"2"` 走通用直通路径（推荐，普通图像安全）；其他取值走卫星地理校正路径（依赖卫星元数据，普通图像不可用） |
| `Suffix` | 是 | 字符串 | 输出文件后缀。**传非空值**：标签存在但为空时脚本读到 None（见下），输出直接以输入名命名——数据不会真被覆盖，但**输入文件会被改名**，属破坏性副作用，见 §2.4 第 1 条。平台的做法（2026-09-16 起）：请求不带 `suffix` 时，直接读本文件里的 `<Suffix>` 当默认值（当前样例即 `260318`），读不到或值不合法才回落到内置的 `sr`——所以「平台用哪个后缀」这件事以本文件为唯一权威 |
| `MaskPath` | 否 | 路径 | ROI 掩码。缺失时做全图超分；必须指向 `.tif` 文件（指向目录会导致异常）；分辨率须与输入图像一致，否则局部超分被静默跳过 |
| `GridAlign` | 否 | 字符串 | 取 `false` / `0` 时关闭 MTA-Grid，缺省启用。旧配置无此标签，脚本已有保护（不会因缺标签而崩溃） |

模板样例（真实文件 `sfsr_config_test_espan2_cuda1.xml`，2026-08-17 核对）：

> **2026-09-16 补记（文件名拼写）**：仓库 `SR_code/` 下那份文件的名字是
> `sfsr_confgig_test_espan2_cuda1.xml`（`confgig`，多一个 g），与本页记的 `config` 拼法不一致，
> 两者是否同一份当时无从核对（本记录来自回传内容，不是本机读盘）。平台的读取逻辑因此
> **两个拼写都探**，按上表顺序取第一个能解析出非空 `<Suffix>` 的：见
> [backend/services/run_sr.py](../../backend/services/run_sr.py) 的 `BUNDLE_SUFFIX_CONFIG_NAMES`。

```xml
<SFSR_Config>
  <DatarootLQ>/DiskArray/tmp/wangrz/sr_utils/sr_data/JXGF07A03_..._L1_PAN</DatarootLQ>
  <GPUIDS>3</GPUIDS>                             <!-- 模板存在，当前脚本不读取 -->
  <CloudLimit>80</CloudLimit>
  <DeleteOriTifNeeded>False</DeleteOriTifNeeded> <!-- 真实值为大写 False -->
  <SRScale>2</SRScale>
  <Suffix>260318</Suffix>                        <!-- 日期式后缀 -->
  <OPT>/DiskArray/tmp/wangrz/sr_utils/espan3_2026_gf04_tile500.yml</OPT>
  <MaskPath>.../JXGF07A03_..._L1_PAN_mask.tif</MaskPath>
  <MaskDilateFactor>2</MaskDilateFactor>         <!-- 0803 已移除膨胀，当前脚本忽略 -->
</SFSR_Config>
```

模板中有两个标签需要说明：

- `GPUIDS`：历史部署脚本曾读取该标签按任务选卡（实测值 3），本地版 `code_0817_prod.py` 不读取、硬编码 GPU 0。**Slurm 接入后该标签已不参与选卡**，降级为审计字段（平台照写、作业不读，选卡由 `--gres` 完成）——见 §7.1、§9。
- `MaskDilateFactor`（膨胀参数）：0803 起已移除，本地与部署脚本均不读取。

**OPT 指向的 YAML**：存在多个版本（本例为 tile500，另有 `espan3_2026_gf04_tile1000.yml`），平台按模板原样传递即可。

标签读取语义（依据 util.py 核对）分三种情况：

1. 配置文件缺失 → 打印提示并 `exit(0)`；
2. 标签缺失 → 抛出 IndexError（`getElementsByTagName(tag)[0]`）；
3. 标签存在但为空 → 读到的值为 `None`。

上表"必填"即标签必须存在；其中 `Suffix`、`GridAlign` 允许存在但为空，此时读作 None。

## 4. 输入目录与文件命名

`DatarootLQ` 指向的目录须包含以下文件：

| 文件 | 必选 | 说明 |
|---|---|---|
| 输入全色图 | 是 | 文件名由输入命名规则决定（见下） |
| `<文件夹名>_meta.xml` | 是 | 文件名 = 文件夹名 + `_meta.xml`，内容见 §5 |
| `mask.tif` | 否 | 二值掩码（0=背景，1=ROI），分辨率与输入图像一致 |

**输入命名规则**：脚本读取 meta.xml 中的 `<SolarAzimuth>`，判断当前属于哪个处理步，进而决定输入文件名。

| SolarAzimuth 取值 | 处理步 | 输入文件 | 文件大小要求 |
|---|---|---|---|
| 空 | RC | `<文件夹>/PAN.tif` | 须在 (1.0, 1.8) GB 区间，否则打印提示并 `exit(0)` |
| 非空 | SC | `<文件夹>/<文件夹名>.tif` | 须在 (0, 1.1) / (1.5, 1.7) / (3.8, 4.1) GB 之一；超出区间时打印 `already SRed before` 但仍返回该文件；文件缺失 → 打印提示并 `exit(0)` |

> 常见卫星图像（含 JXGF07A03）的 meta 中 `SolarAzimuth` 均有取值，因此属于 **SC 步，输入图像须命名为 `<文件夹名>.tif`**。
>
> 若 meta.xml 缺失或 `SolarAzimuth` 标签缺失，脚本会直接抛出异常——这是普通图像无法运行的原因之一（见 §9）。临时规避方法见 `run_all_folders.py` 文末注释。

## 5. meta.xml 元数据契约

**位置与命名**：`<DatarootLQ>/<文件夹名>_meta.xml`（实测确认 = 文件夹名 + `_meta.xml`）。

读取字段如下：

- `DataBits`（必填）：决定 `max_DN`，公式为 `max_DN = 2^DataBits − 1`（即代码中的 `(2 << (DataBits−1)) − 1`）。实测 `DataBits=12` 时 `max_DN=4095`。
- `CloudPercent`（缺省 0）：用于云量过滤。
- `SolarAzimuth`：决定输入文件命名（§4）。

写回规则（依据 util.py 核对，按卫星前缀条件化）如下：

- `update_meta_gsd`：仅卫星前缀为 `JL1GF04` / `TEE01B` 时，将 `ImageRowGSD` / `ImageColumnGSD` 除以 2；其余卫星（含 JXGF07A03）不做任何修改。
- `update_meta_integrationtime`：仅 `JL1GF04`（÷2）、`JL1KF01B/C`（÷2.2）、`JL1KF01A`（÷2 或 ÷2.2，按日期）、`JL1KF02`（÷1.96）；其余卫星不做任何修改。
- 因此：**平台不应假设 meta.xml 一定会被更新**。输出 tif 不带地理参考信息，分辨率应始终读取此文件，不要读取 tif 文件头。

**实测注意**：真实 meta 中 `CloudPercent` 可能是数字（实测 0 / 1 / 3），也可能是 `θ`（"无数据"占位符）。脚本对非数字值执行 `int("θ")` 会抛出 ValueError 而崩溃——云量过滤并不总是干净地跳过，遇非数字值任务会以 traceback 失败。平台侧应：调用前将 `θ` 清洗为 0；或把此类失败统一按任务异常处理。

## 6. 输出产物契约

| 产物 | 位置 | 说明 |
|---|---|---|
| 超分结果 tif | `<DatarootLQ>/<输入名去掉.tif>_<Suffix>.tif`；`Suffix` 为 None（空标签）时 → `<输入名去掉.tif>.tif` | 像素类型 uint16 |
| 源图去向 | 同目录重命名 | `util.writeTiff` 改名的对象是**输出路径上已存在的同名文件**（`os.rename(path + tiftype, …)`），因此只有 `Suffix` 为空、输出名 == 输入名时才会动到源图：非 `PAN.tif` 命名 → `*_NOSR.tif`，名为 `PAN.tif` → `PAN_ori.tif`。`Suffix` **非空**时输出名与输入名不同，首次运行该路径不存在（`os.rename` 抛 `FileNotFoundError`，被脚本吞掉），重跑时被改名为 `<输出名>_NOSR.tif` 的是**上一次的输出**；**源图既不改名也不删除**。`DeleteOriTifNeeded=TRUE` 删除的同样只是该输出路径上的文件（小于 2GB 时 `os.remove`，否则被 `driver.Create` 覆盖），只有空 `Suffix` 下才等同删除源图 |
| 日志 | `<DatarootLQ>/Debug/<输入名去掉.tif>_SRLOG.txt` | 每次运行整体重建（以写模式打开），含阶段计时、GPU/CPU 内存、末行 `Run finished.` |
| meta.xml | 原位置 | 条件化更新（§5） |

> 参考 `run_all_folders.py` 的约定：`DatarootLQ` 指向任务文件夹、`MaskPath` 指向该文件夹内的 `mask.tif`、输出写回原文件夹。

## 7. 运行环境与部署前置条件

| 项 | 值 |
|---|---|
| 运行机器 | 内网服务器（开发机为 4×RTX 3090） |
| conda 环境 | `torch1.9.1py36`（torch 1.9.1 / Python 3.6） |
| 代码依赖 | `models/`、`utils/`、`options/`（属 mmsr_bundle，随服务器部署，不随本仓库） |
| 动态库 | `ImgHistMatch`，import 时即加载（路径 `.../mmsr_bundle/codes/tools/ImgHistMatch`）；缺失时 import 阶段直接崩溃 |
| TRT 引擎 | 仅 `realesrgan_trt` 分支使用（`.../tools/trt/t2trt_fp16_realESRGAN_1640.trt`） |
| 模型侧修复 | **必做**：服务器 3 个模型文件（`codes/models/SR_model.py`、`SR_model_0616tmp.py`、`SR_model_0616nvidiasmilog.py`）的 `get_current_visuals` 中，需将 `self.fake_H.detach()[0]` 改为 `self.fake_H.detach()`。未修复时 PyTorch 各分支推理会崩溃。原因与回退命令见 `sr-pipeline-overview.md §5.7` |

### 7.1 GPU 数量检查（部署变体已删除该守卫）

真源 `code_0817_prod.py`（归档版本，逐字节不动）在入口处内置了生产队列守卫：

```python
if gpu_available is False or gpu_count != 4:
    # 停 slurmd、写 SlurmStopLog、exit(3)
```

- 该守卫对 Slurm 单卡作业是**致命**的：`--gres=gpu:1` 下可见 GPU 数为 1，`!= 4` 恒真 ⇒ 作业必然 `exit(3)`；更糟的是它会执行 `systemctl stop slurmd.service` —— 作业以 `User=nginx` 运行时是权限拒绝（作业白挂），服务以 root 运行时则**真把节点从调度池里摘掉**。
- **本轮起不再要求平台侧手工改脚本**：Slurm 部署用的可运行版本由锚点替换生成器机械产出到 `SR_code/variants/code_0817_prod_slurm.py`，**已删除该守卫的相关行为**（逐条差异见 [sr-slurm-deploy-variant.md](sr-slurm-deploy-variant.md) 的 E2/E4/E5/E6/E7/E8）：
  - 判据改为 `gpu_count < 1`（可见卡数为 0 才失败），**退出码 3 的语义不变**；
  - 计数改为 `torch.cuda.device_count()`——数"本作业看得见的卡"，而非 nvml 的物理卡数（4 卡节点恒为 4）；
  - 不再赋值 `CUDA_VISIBLE_DEVICES`（选卡唯一来源是 `--gres`，见 §9）；
  - 不再执行 `systemctl stop slurmd.service`，GPU 异常时只向 `SlurmStopLog.txt` 追加一行（措辞 `gpu-error`，不再写 `stop` 以免运维误判节点已下线）。
- **上机安装**：把变体与生产脚本**并置**在 `$SR_BUNDLE_DIR` 下，再用 `SR_SR_SCRIPT=code_0817_prod_slurm.py` 指定作业跑它——**不必改名顶替 `code_0817_prod.py`**，生产原文件因此保持字节不动（也就始终是生成器的对照物）。作业跑哪个脚本、以及校验器 `verify_sr_run.py`，都由 `SR_SR_SCRIPT` / `SR_VERIFY_SCRIPT` 两个 env 决定（不配则退回原脚本名，行为同旧版）。完整步骤见 [docs/status/slurm-acceptance.md §0.2](../status/slurm-acceptance.md) 与 [deploy/README.md §7.1](../../deploy/README.md)。
- **新鲜度校验**：`python SR_code/tools/gen_slurm_variant.py --check`（逐字节比对，手改即报 STALE）。
- **本地版与部署脚本不一致（已确认）**：真源硬编码使用 GPU 0（模块级曾设为 `"1"`，`main()` 内覆盖为 `"0"`）；模板 config.xml 里的 `<GPUIDS>` 原设计由部署脚本读取选卡（实测模板值 3）。**Slurm 接入后 `<GPUIDS>` 不再参与选卡**，降级为审计字段（见 §9）。

## 8. 性能参考与资源占用

性能数据用于设置超时与并发策略，实测结果如下：

| 场景 | 参考耗时 | 说明 |
|---|---|---|
| 冷启动（模型加载 + 预热） | 约 8–9 秒 | 每次进程启动都会发生；模型不跨任务缓存 |
| 极小 ROI（约 2 个 tile） | 约 16 秒 | 冷启动占主导 |
| 全图 10000×10000、tile=1000、ESPAN、RTX 3090 | 约 147 秒（GPU 占约 91%） | tile=500 时约 164 秒 |
| 写 tif | 约 11 秒（/DiskArray 磁盘阵列） | 磁盘 IO 慢，勿设置过紧的超时 |

- **单卡 compute-bound**：增加并发或增大 batch 均无加速（实测见 overview §5.6 / §8）。
- **显存占用**：单进程约 4.5GB（tile=500）。

## 9. 并发策略与调用方职责

- **GPU 分配（`--gres` 决议，2026-09-10）**：Slurm 接入路径上选卡**不再由 `<GPUIDS>` 决定**，改由批脚本的 `#SBATCH --gres=gpu:1` 向 Slurm 申请、由 Slurm 注入 `CUDA_VISIBLE_DEVICES`（真机实测注入的是**整数**，如 `0`）。部署变体已删除全部 `CUDA_VISIBLE_DEVICES` 赋值（E2/E4）；模板里的 `<GPUIDS>` 从此只作**审计字段**——平台照写、作业只读打印、不据此选卡。`SLURM_JOB_GPUS` 在真机上未设置，同样只作审计。
  - 并发策略不变：单任务内部是 compute-bound，同一 GPU 上的并发没有收益，因此仍是"任务间并行（一个作业一张卡）、任务内串行（任务队列）"；
  - 76 个计算节点（`gpu` 分区，横跨 `node81-*`/`node104-*`）、每节点 4 卡，落到哪张卡由 Slurm 决定，平台不指定。注意 `--gres=gpu:1` 只给出整数型 CVD、**不做设备绑定**（`SLURM_JOB_GPUS` 未设置），同节点上的并发作业理论上仍可能都落物理卡 0；
  - 桌面端（无 Slurm）路径不受影响，仍按本地脚本硬编码 GPU 0 运行。
- 模型每个进程加载一份（约 4.5GB 显存 + 8–9 秒冷启动）。任务小而多时，按目录批量处理（参考 `run_all_folders.py`）比逐个进程调用更节省资源。
- 磁盘 IO 较慢（writeTiff 约 11 秒），应将"写结果"阶段计入任务时长，不要误判为进程停滞。
- **普通外部图像不适用**：脚本的输入判定强依赖 `<文件夹名>_meta.xml`（含 `<SolarAzimuth>`）；普通图像没有该元数据，会在任务早期抛出异常。若平台需要接入普通图像，须前置生成 meta.xml，至少包含 `DataBits` 与 `SolarAzimuth`（`SolarAzimuth` 留空 → 走 RC 步、输入命名为 `PAN.tif`）。

## 10. 本地开发用 Mock 工具

本地 Windows 环境无内网、无法运行真实管线。开发期可用 Mock 工具替代，其接口与真实管线逐字段一致：

```bash
python sr_pipeline_mock.py -f <config.xml> [--latency <秒>] [--fail <none|cloud|pan|gpu>]
```

| 行为 | 说明 |
|---|---|
| 默认（正常） | 读取 §3 的必填标签（缺标签时报错，行为同真实管线）；等待 `<latency>` 秒（默认 9，模拟冷启动）；生成 dummy 输出 tif 与 SRLOG（含 `Pipeline Timing Summary` 与 `Run finished.`）；以退出码 0 结束 |
| `--fail cloud` | 以退出码 0 结束，但不产生输出 tif（模拟云量过滤）。注意：真实管线在跳过时会**先写一条 `Run skipped:` 终态行**（§2.3），Mock 不写——因此在 Slurm 契约校验路径下 `--fail cloud` 会被判为契约不满足（90）。Mock 只用于离线验证平台的成功判定逻辑 |
| `--fail pan` | 打印 `L1 PAN.tif file not found.` 后以退出码 1 结束 |
| `--fail gpu` | 以退出码 3 结束（模拟 GPU 守卫） |

> Mock 的 `--fail` 分支用于在本地测试平台的成功判定逻辑（§2 的三项条件）。部署到服务器时，将 Mock 换成真实脚本，只需修改一处命令路径。

## 附录 A：真实样例与核对记录（2026-08-17）

三份文件均位于 `docs/`，可作为平台测试夹具与 Mock 的参照：

| 文件 | 关键值 |
|---|---|
| `sfsr_config_test_espan2_cuda1.xml`（磁盘上的实际拼写为 `confgig`，见 §2 补记） | DatarootLQ=…/JXGF07A03_…_L1_PAN；GPUIDS=3（现已降级为审计字段，见 §9）；CloudLimit=80；DeleteOriTifNeeded=False（大写）；SRScale=2；Suffix=260318；OPT 指向 tile500 版 yml；MaskPath=…_mask.tif；MaskDilateFactor=2（部署脚本不读取） |
| `espan3_2026_gf04_tile1000.yml` | 规范嵌套 YAML；scale=2；t_ht/t_wd=1000；pad=50；tiftype=.tif；networkG=ESpan2(nf=64, nb=23)；pretrain_model_G=…/Espan3.pth；`max_dn: 16383`（当前脚本不读取，max_DN 由 meta 的 DataBits 计算，实测 12 → 4095） |
| `JXGF07A03_…_L1_PAN_meta.xml` | DataBits=12 → max_DN=4095；CloudPercent=θ（已确认为真实数据，"无数据"占位符）；SolarAzimuth 有值（→ SC 步，输入为 `<文件夹名>.tif`）；ImageRowGSD=0.306 / ImageColumnGSD=0.312；IntegrationTime=0.174；ProductLevel=L1 |

模板标签归属（2026-08-17 确认）如下：

- `<GPUIDS>`：历史部署脚本曾读取（按任务选卡）；本地版 `code_0817_prod.py` 不读取（硬编码 GPU 0）。**Slurm 接入后降级为审计字段**，选卡由 `--gres=gpu:1` 完成（§9）。
- `<MaskDilateFactor>`：膨胀参数，0803 起已移除，本地与部署脚本均不读取。

**util.py 核对记录（2026-08-17，逐函数，对应 §2/§3/§4/§5/§6）**：`get_cfg_value`（配置文件缺失 → exit(0)；标签缺失 → IndexError；空标签 → None）、`check_sr_previous_step` + `get_l1_pan_tif_rcsc`（SolarAzimuth 空 → `PAN.tif`、非空 → `<文件夹名>.tif`，带文件大小闸门）、`is_true` / `writeTiff`（仅 `"TRUE"` 删除源图，否则重命名保源）、`update_meta_gsd` / `update_meta_integrationtime`（按卫星前缀条件化）。

**【待确认】清单**：已全部关闭（v1.3）。剩余建议：将服务器部署脚本（`0807.py`）与 3 个 `SR_model` 文件导入归档（如 `服务器原始代码参考/`），使归档完全自包含。

## 变更记录

| 日期 | 版本 | 变更 |
|---|---|---|
| 2026-09-16 | v1.6 | 订正 §6「源图去向」：`util.writeTiff` 改名的对象是**输出路径上已存在的同名文件**，源图仅在 `Suffix` 为空（输出名 == 输入名）时才被改名或删除；`Suffix` 非空时源图不动，重跑被改名为 `<输出名>_NOSR.tif` 的是上一次的输出。`DeleteOriTifNeeded=TRUE` 的删除同样只在空 `Suffix` 下才等同删除源图（§3 的 `DeleteOriTifNeeded` 行同步订正；§2.4 第 1 条原文正确，未改）；补记 §2 `Suffix` 行：平台的产品后缀默认取本文件的 `<Suffix>`（`SR_SUFFIX_DEFAULT` 环境变量同日作废），并记录该文件在磁盘上的拼写为 `confgig`、平台两种拼写都探 |
| 2026-09-10 | v1.5 | 补入 Slurm 部署路径终态语义：§2 拆分并新增**退出码 90**（契约不满足）与**云量跳过终态**（`Run skipped:` / 退出码文件 `skip=1` ⇒ 平台 COMPLETED）——退出码 0 的一批静默失败从此机器可读；新增 §2.4 三条已知语义（空 suffix 覆盖、`sacct` 解析不可用、对已超分场景会再超分一遍）；§7.1 由"部署前必须处理"改为"**部署变体已删除该守卫**"并指向 `SR_code/variants/code_0817_prod_slurm.py`；§9 与附录 A 记录 `--gres` 选卡决议、`<GPUIDS>` 降级为审计字段 |
| 2026-08-17 | v1.4 | 全面润色：新增术语表、统一术语与句式、去除口语与网络用语、规范表格与章节编号、修正 §1 交叉引用笔误；技术内容与 v1.3 一致 |
| 2026-08-17 | v1.3 | 逐函数核对 util.py，关闭全部待确认：输入命名规则（SolarAzimuth→RC/SC）；exit(0) 静默失败语义；writeTiff 用 is_true（"TRUE" 才删源，否则 \*_NOSR.tif 保源）；meta 更新按卫星前缀条件化；get_cfg_value 三态语义 |
| 2026-08-17 | v1.2 | 核实三项待确认：CloudPercent θ 为真实占位符（int("θ") 崩溃，平台需容错）；yml 为规范嵌套 YAML；部署脚本读 GPUIDS 选卡、不读 MaskDilateFactor（§9 GPU 策略修正） |
| 2026-08-17 | v1.1 | 依据三份真实文件核对；修正 max_DN 公式为 2^DataBits−1（实测 12→4095）；补 GPUIDS/MaskDilateFactor 说明与待确认清单 |
| 2026-08-17 | v1.0 | 初版：依据 `code_0817_prod.py` + `sr-pipeline-overview.md` 实测数据建立黑盒契约 |
