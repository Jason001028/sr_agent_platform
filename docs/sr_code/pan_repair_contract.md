# PAN 局部修复契约

> 日期：2026-10-26 · 状态：已定；真实 GDAL/GPU 与内网文件验收待核

## 1. 输入与输出

平台“提交 SR”用于修复最新 PAN 的伪影。查看器在当前 `PAN.tif` 网格上标注掩码，
计算输入为同景 `<完整生产序列号>_PAN_NOSR.tif`，即 `<场景目录名>_NOSR.tif`。
GF07（含 JXGF07）场景没有该文件时，使用同目录的 `PAN_ori.tif`，同样要求它是比当前 PAN 更低分辨率的原始输入。
同景存在 `PAN.tif` 时，GF07 新作业以它作为掩码及发布网格，备份为 `PAN_old_latest.tif`。生产序列号命名的 TIFF 可单独查看。旧作业配置沿用原本的 RC/SC 规则。
不从当前 PAN 再次做整图放大，也不把 `PAN_260318_NOSR.tif` 当原始输入。

### GF07 生产顺序与现场现象（2026-11-26）

GF07（含 JXGF07）的生产顺序是**先 SR，后几何校正（SC）**。`PAN_ori.tif` 是 SC 前的低分辨率输入，`PAN.tif` 是同一阶段的 SR 网格。后续以完整生产序列号命名的 SC 产品采用另一套像素网格。

现场 GF07B04 样例中，模型输出和 `PAN.tif` 均为 `61440×59360`，SC 产品为 `49481×49450`。若将 SC 产品当成 SR 回贴基准，尺寸校验会拦截。若在查看或对比时直接混用两阶段的像素坐标，也可能出现截图所示的条带状区域重叠、重复或错位。卡片宽高与 ENVI 一致只说明尺寸读取正确。排查时先分别在同一份 TIFF 的原生窗口与低层级视图核对相同区域，确认现象出现在哪一阶段。

| 对象 | 固定约定 |
|---|---|
| 当前影像 | 场景目录中的 `PAN.tif` |
| 计算原图 | 同目录 `<目录名>_NOSR.tif` |
| 掩码 | 当前 PAN 网格；同时接受已在 NOSR 网格上的掩码 |
| 掩码内 | 使用现有 SR 模型结果 |
| 掩码外 | 逐值保留修复前 PAN，不写入 NOSR 插值或模型值 |
| 正式结果 | `PAN.tif`，尺寸、样本类型、字节数与修复前 PAN 一致 |
| 旧版备份 | `PAN_old_latest.tif`，保留修复前 PAN 文件内容 |

备份命名顺序为 `<完整生产名>_old.tif`（上一轮产物）→ `PAN_old_latest.tif`
（本次修复前）→ `PAN.tif`（修复后）；`latest` 明确表示它比 `_old` 新。
新任务只写 `PAN_old_latest.tif`。改名前的平台旧名 `PAN_old_2.tif` 继续按
**只读兼容**处理：已有备份的两种名字任一存在都拒绝再次修复，`/siblings? suffix=old`
也仍能找到旧名。`old`、`old_latest`、`NOSR`、`ori`、`mask`、`cloud`、`thumb`、
`preview` 是平台/产线保留后缀，SR 产物后缀不得使用。

备份已存在时，在模型执行前停止，不覆盖已有备份，也不自动递增名称。
NOSR、原始场景元数据不修改。模型在私有场景中读取 NOSR，副作用限于私有场景；
已通过检查的结果才会发布到正式 PAN 路径。

## 2. 执行流程

`backend/services/run_sr.py::repair_request` 由 REST 和 Agent 工具共用，设置 repair 模式，
记录掩码与 NOSR 的文件版本；该版本参与任务指纹。相同请求重放复用任务，修改掩码后产生新指纹。
配置包含 `RepairMode=pan-nosr-v1`、明确的 `RepairNOSR` 和可用的 `RepairRevision`。

运行脚本调用 `SR_code/variants/repair_sr.py`，再由适配层调用配置的生产脚本或部署变体。
原生产算法源码保持不变。生产批次检出候选后，在掩码生成期间预复制冻结的 NOSR。
每个后端实例同时复制一景，副本和来源描述写完后一起原子发布到批次工件根目录的
`nosr-stage/`。该目录与批次掩码一样，须由 API 节点和 SR 节点以同一路径访问。
提交前复核原文件和副本身份，已有同版本副本可复用。

私有目录保留原场景目录名，将独立副本映射为算法需要的 `PAN.tif` 和 `<目录名>.tif`，
并复制 meta.xml。未携带 `RepairNOSRStage` 的单景入口和旧 Job 在私有目录中临时复制一次。
推理读取暂存 NOSR，修复回执与验收仍绑定原 NOSR 路径。推理退出时清理私有输入入口。
批次在该景结算后清理预复制文件，未提交即取消时也清理。

掩码向 NOSR 网格映射时按覆盖面积保留选中像素，单个 PAN 像素的选区也不会消失。
算法仍按瓦片计算，适配层最终只在发布掩码内写入模型值；保留掩码外以及 PAN NoData/非有限值。
原子替换前逐块复读候选影像，校验被保护的样本与修复前 PAN 一致。这个发布边界不是 MSS 配准证明；
缺少经过验证和冻结的 MSS 陆地约束时，生产批次的自动生成掩码不得提交。已有人工掩码路径不变。
`260318` 是常用的计算中间结果后缀，只有私有目录中出现 `PAN_260318.tif`
或 `<目录名>_260318.tif`；配置了其他合法后缀时同理。正式结果恒为 `PAN.tif`。

模型输出必须先通过普通退出码、SRLOG 与产物校验，然后检查其尺寸与当前 PAN 一致。
修复适配器与部署变体都更新后，2× 模型输出直接匹配 PAN 网格且存在背景瓦片时，
私有中间 TIFF 只存储 SR 窗口。适配器按窗口清单检查所有待替换像素都有模型值。
需要重采样的场景继续沿用整景中间图。作业日志的 `[REPAIR WINDOW OUTPUT]` 记录实际写出的窗口数。
适配层复制当前 PAN 的 TIFF 文件布局，在副本中按块写入合成像素，避免创建不同布局导致字节数变化。
当前实现要求单波段、相同样本类型、未压缩 PAN；不支持的布局明确停止。
输出倍率必须与当前 PAN 的目标尺寸匹配，不能通过额外放大掩盖配置错误。

发布顺序为：校验候选 → 再查输入版本 → 排他创建 `PAN_old_latest.tif` 硬链接备份 →
原子替换 `PAN.tif` → 原子保存本配置对应的修复回执 → 写成功日志。
同景使用独占锁，进程异常退出留下的锁需在核对任务事实后人工处理。
若在发布后、回执完成前中断，备份仍保留，任务不得判为成功或自动覆盖备份重试。

## 3. 校验与页面

修复回执按配置内容摘要命名，位于 `Debug/_SRREPAIR_<摘要>.json`。
`verify_sr_run.py` 对 repair 配置校验：进程退出码、回执归属、PAN/备份/NOSR 文件版本、
PAN 和备份的真实 TIFF 尺寸、相同字节数与成功日志。只有已存在的 PAN 或旧成功日志不能满足验收。
Goal 服务使用相同随平台交付的 verifier 复核，不使用生产环境中可能未更新的同名脚本。

预复制功能上线前的适配器曾在私有目录内硬链接原 NOSR，导致原文件 `ctime` 更新。
旧 Job 进入质量审核时，只有冻结修订、提交配置和回执的 NOSR 签名均匹配，且私有推理配置
和两个保留的硬链接能佐证该变更时，才接受这一旧版身份差异。审核同时绑定冻结身份和当前
文件身份，建立审核后的版本变化仍会使审核失效。带 `RepairNOSRStage` 的作业继续要求原 NOSR 身份完全匹配。

任务完成后主动生成预览的源为正式 `PAN.tif`。`/siblings` 在最近完成任务为修复任务时
返回 PAN 作为结果，`suffix=old` 依次查 `PAN_old_latest.tif`、旧名 `PAN_old_2.tif`、
`<目录名>_old.tif`，供左侧 old 卡片查看。
查 NOSR 优先使用完整场景名，避免把上一次 SR 产物当原图。

## 4. 部署与测试

后端离线包包含 `backend/`，以及 `SR_code/variants/repair_sr.py`、
`SR_code/variants/verify_sr_run.py`，解压到同一个应用根目录。
两脚本由 SR 生产 Python 执行，使用已有 GDAL、NumPy 与模型环境；API 环境不导入 GDAL。
适配层与 verifier 兼容 Python 3.6。`SR_SR_SCRIPT` 仍指向生产模型脚本或部署变体。
窗口交付需同步更新应用目录的 `SR_code/variants/repair_sr.py` 和 `SR_BUNDLE_DIR`
下由 `SR_SR_SCRIPT` 选中的 `code_0817_prod_slurm.py`。

```text
python -m pytest backend/tests/test_pan_repair.py backend/tests/test_run_sr.py backend/tests/test_sr_verify.py -q
```

开发机测试使用真实 TIFF 文件和替代 GDAL 接口，验证流程、逐像素选区、尺寸、字节数、备份、
缺输入、错误输出、静默退出与文件变化。掩码外使用非均匀 PAN 逐值验证。
真实 GDAL、GPU 推理与生产体量耗时仍需内网验收。

## 5. P1 可信陆地自动修复

查看器设置中的「MSS 可信陆地校验」默认关闭。新建自动计划在关闭时冻结 `mask_policy=auto_diff`，直接使用差异掩码，仍需用户确认及生产执行预检。
开启校验时，生产自动计划要求服务端设置 `SR_MSS_EXEC_PROFILE`，指向 API 和 SR worker 都可读取的 JSON 文件。
Profile 包含 `profile_id`、实景验收记录 `verification_ref`、`satellite`、`sensor`、
`registration_method`、`land_threshold`、`water_threshold`、`shoreline_pan_pixels`
和 `registration_error_pan_pixels`。距离按目标 PAN 像素计。两个阈值之间保留不确定类。
当前实现支持 `exact_embedded_geotiff_footprint`，要求 XML 明示匹配的 SceneID/ProductType，
PAN 和 MSS 使用同 CRS、同覆盖、轴对齐的内嵌 GeoTIFF 网格。外部 RPC、无法确认的配准、
不支持的有效性掩膜，以及缺少适用 Profile 的场景阻断自动计划。

计划在确认前复用原差分检测和 MaskBuilder，保留原候选。最近邻映射后按 MSS 像元大小、
配准误差及岸线距离建立保护区。执行掩膜只包含候选内的可信陆地和有效 PAN 样本。
计划与 Item 持久化来源、Profile、波段、配准、P0 产物版本、候选/执行掩膜及摘要。
计划卡统计完全排除和部分裁剪，Viewer 核对层绑定 PAN 版本和尺寸。
完全排除的 Item 不生成作业。逐区域复核产生子集时沿用原约束重新求交，并由复核记录绑定新摘要。

Worker 提交前复核证据，作业在启动前检查确认有效期及共享输入版本，并校验执行掩膜 SHA-256。
推理使用私有掩膜副本，发布使用原 PAN 网格上的同一冻结掩膜。发布前复核源和掩膜，
复读候选 TIFF 并比较禁止样本，回执包含约束证据。作业配置中的文件路径与文件身份要求
API 和 worker 对共享盘阵具有一致的视图。正式启用前仍需现场校验 Profile、配准精度、
GDAL 行为和大图磁盘预算。测试 Profile 仅用于合成夹具。
