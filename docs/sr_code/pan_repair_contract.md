# PAN 局部修复契约

> 日期：2026-10-26 · 状态：已定；真实 GDAL/GPU 与内网文件验收待核

## 1. 输入与输出

平台“提交 SR”用于修复最新 PAN 的伪影。查看器在当前 `PAN.tif` 网格上标注掩码，
计算输入为同景 `<完整生产序列号>_PAN_NOSR.tif`，即 `<场景目录名>_NOSR.tif`。
不从当前 PAN 再次做整图放大，也不把 `PAN_260318_NOSR.tif` 当原始输入。

| 对象 | 固定约定 |
|---|---|
| 当前影像 | 场景目录中的 `PAN.tif` |
| 计算原图 | 同目录 `<目录名>_NOSR.tif` |
| 掩码 | 当前 PAN 网格；同时接受已在 NOSR 网格上的掩码 |
| 掩码内 | 使用现有 SR 模型结果 |
| 掩码外 | 使用原始 NOSR 的 cubic 插值结果，不保留旧 PAN 的像素 |
| 正式结果 | `PAN.tif`，尺寸、样本类型、字节数与修复前 PAN 一致 |
| 旧版备份 | `PAN_old_2.tif`，保留修复前 PAN 文件内容 |

备份已存在时，在模型执行前停止，不覆盖已有备份，也不自动递增名称。
NOSR、原始场景元数据不修改。模型在私有场景中读取 NOSR，副作用限于私有场景；
已通过检查的结果才会发布到正式 PAN 路径。

## 2. 执行流程

`backend/services/run_sr.py::repair_request` 由 REST 和 Agent 工具共用，设置 repair 模式，
记录掩码与 NOSR 的文件版本；该版本参与任务指纹。相同请求重放复用任务，修改掩码后产生新指纹。
配置包含 `RepairMode=pan-nosr-v1`、明确的 `RepairNOSR` 和可用的 `RepairRevision`。

运行脚本调用 `SR_code/variants/repair_sr.py`，再由适配层调用配置的生产脚本或部署变体。
原生产算法源码保持不变。私有目录保留原场景目录名，将 NOSR 硬链接为算法预期的
`PAN.tif` 与 `<目录名>.tif`，并复制 meta.xml；原场景文件不被改名。
私有进程绕过按文件大小判断“已经超分”的输入选择规则，明确选择暂存的 NOSR。

掩码向 NOSR 网格映射时按覆盖面积保留选中像素，单个 PAN 像素的选区也不会消失。
算法仍按瓦片计算，适配层在最终合成时严格使用原选区：掩码外每个像素来自 NOSR cubic 插值。
`260318` 是常用的计算中间结果后缀，只有私有目录中出现 `PAN_260318.tif`
或 `<目录名>_260318.tif`；配置了其他合法后缀时同理。正式结果恒为 `PAN.tif`。

模型输出必须先通过普通退出码、SRLOG 与产物校验，然后检查其尺寸与当前 PAN 一致。
适配层复制当前 PAN 的 TIFF 文件布局，在副本中按块写入合成像素，避免创建不同布局导致字节数变化。
当前实现要求单波段、相同样本类型、未压缩 PAN；不支持的布局明确停止。
输出倍率必须与当前 PAN 的目标尺寸匹配，不能通过额外放大掩盖配置错误。

发布顺序为：校验候选 → 再查输入版本 → 排他创建 `PAN_old_2.tif` 硬链接备份 →
原子替换 `PAN.tif` → 原子保存本配置对应的修复回执 → 写成功日志。
同景使用独占锁，进程异常退出留下的锁需在核对任务事实后人工处理。
若在发布后、回执完成前中断，备份仍保留，任务不得判为成功或自动覆盖备份重试。

## 3. 校验与页面

修复回执按配置内容摘要命名，位于 `Debug/_SRREPAIR_<摘要>.json`。
`verify_sr_run.py` 对 repair 配置校验：进程退出码、回执归属、PAN/备份/NOSR 文件版本、
PAN 和备份的真实 TIFF 尺寸、相同字节数与成功日志。只有已存在的 PAN 或旧成功日志不能满足验收。
Goal 服务使用相同随平台交付的 verifier 复核，不使用生产环境中可能未更新的同名脚本。

任务完成后主动生成预览的源为正式 `PAN.tif`。`/siblings` 在最近完成任务为修复任务时
返回 PAN 作为结果，`suffix=old` 优先查 `PAN_old_2.tif`，供左侧 old 卡片查看。
查 NOSR 优先使用完整场景名，避免把上一次 SR 产物当原图。

## 4. 部署与测试

后端离线包包含 `backend/`，以及 `SR_code/variants/repair_sr.py`、
`SR_code/variants/verify_sr_run.py`，解压到同一个应用根目录。
两脚本由 SR 生产 Python 执行，使用已有 GDAL、NumPy 与模型环境；API 环境不导入 GDAL。
适配层与 verifier 兼容 Python 3.6。`SR_SR_SCRIPT` 仍指向生产模型脚本或部署变体。

```text
python -m pytest backend/tests/test_pan_repair.py backend/tests/test_run_sr.py backend/tests/test_sr_verify.py -q
```

开发机测试使用真实 TIFF 文件和替代 GDAL 接口，验证流程、逐像素选区、尺寸、字节数、备份、
缺输入、错误输出、静默退出与文件变化。常量 NOSR 的 cubic 背景可以精确断言。
真实非均匀影像的 GDAL cubic 插值、GPU 推理与生产体量耗时仍需内网验收。
