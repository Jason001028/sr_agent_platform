# 局部超分管线 — 算法与实现全览

> 日期：2026-08-17（内容迭代更新至 08-25）· 状态：草稿
> 归档定位：docs/sr_code/ 类目（SR_CODE 生产管线文档簇）——算法实现全览，与调用契约 [sr-pipeline-interface.md](sr-pipeline-interface.md) 互补。

> **阅读须知（重要，打开本文前先看）**：
>
> 1. **改进目标代码：`超分代码0803/code_0805.py`**——当前项目实际迭代/改进的源码（局部 SR：掩码引导 tile 跳过 + MTA-Grid 网格对齐 + 全管线计时；已移除膨胀与像素融合）。其余版本（0720/0723/0724/0725/0727/0803baseline）仅用于追溯版本演进与对照，常规阅读只会浪费 token，不必展开。
> 2. **代码环境不完整**：仓库内的 `超分代码*.py` 只是内网环境的**部分导出**，`models/`、`options/` 等外部依赖源码不在仓库中（在服务器 mmsr_bundle）。**但 `util.py` 已还原入库**：`SR_code/util.py`（1763 行，2026-08-17 还原），文中 `util.*` 调用均可在该文件查源码（**8.25 最新**）。因此**不能因仓库缺文件就断言某环节不存在**——遇到 `model.*` 等仍未包含的实现细节，应**主动向作者提问**或手动查询 `.py` 未导出的代码细节，可参考 §4.5 的外部依赖契约表。

## 1. 问题定义

**目标**：对大幅面卫星全色图像（单通道 16-bit，约 10000×10000 像素）进行 2 倍超分重建。
图像中仅一小部分区域（<20%）需要超分处理，其余区域可使用廉价的双三次插值上采样填充。

**约束条件**：
- 不允许重新训练模型。ESPAN 主模型当前走 PyTorch 原生推理（`create_model` + `.pth` 权重），
  RealESRGAN_TRT 分支已启用 TRT JIT 引擎。ESPAN 转为 TRT 是明确的加速方向（见 Section 12）；
  正式 TRT 部署由后续 GUI 平台统一完成，本管线本地仅确认大致加速效果
- 开发服务器：4× RTX 3090（24 GB 显存/卡），但当前代码仅使用单卡（`CUDA_VISIBLE_DEVICES="0"`）；
  部署目标：内网机 RTX 3060（12 GB 显存）。算法需在 3060 上稳定运行。多卡（4×3090 服务器）判定为
  伪需求——办公台式机普遍具备 RTX 3060 级显存，可本地部署本套模型，实际部署优先考虑有限算力的桌面端单卡
- 必须采用逐 tile 分块处理（TRT 引擎绑定固定输入尺寸）
- TRT 引擎实际输入 `(tile + 2×pad)` 必须为偶数。ESPAN 内部 pixel-shuffle 上采样要求
  空间维度被 2 整除，奇数输入导致前向传播静默失败（`fake_H` 张量未生成，抛出 `AttributeError`）。
  实验验证：tile=500 ✅ | 625 ❌ | 1250 ✅ | 2500 ✅ | 5000 ❌ (OOM)
- 内网环境，运行时无互联网访问；代码以日期命名迭代（无 git）

**背景八卦（2026-08-24 上午听来，非技术结论）**：KF02 卫星此前将数据出售给对应的 B 端客户，被客户吐槽纹理不行。可作为局部超分对纹理质量提升诉求的侧面背景参考。

> **部署现状（2026-08-25 补充）**：当前方向是**鼓励把超分部署在内网机上本地跑**。数据侧的关键事实：**主要遥感影像都存放在盘阵（磁盘阵列）里**——`run_all_folders.py`（内网存档，本仓库无） 的 `SR_ROOT = /DiskArray/tmp/wangrz/sr_utils/sr_data` 正是盘阵挂载点。**服务器访问盘阵的读写远比本地电脑快**（盘阵的存储与带宽都在服务器侧，本地跨网读写慢），所以"计算靠近数据侧"能省大量 I/O 时间。目前代码已在**服务器与内网办公电脑两处都完成部署**。讨论路径与 I/O 时默认：**数据在盘阵、首选服务器侧运行，本地是第二顺位**。

---

## 2. 版本演进

内网环境无 git，代码以日期命名迭代（`code_MMDD.py`）。每个版本在前一版基础上增量修改，
通过注释标记作者归属（`# huai core` 为原作者，`# wrz` 为局部超分改进）。

| 版本 | 文件 | 核心变更 | 关键特征 |
|------|------|---------|---------|
| 0720 | `超分代码0720/code_0720.py` | 原始全图 SR | 无掩码、逐 tile 串行 TRT、reflect-padding |
| 0724 | `超分代码0723/code_0724.py` | 掩码 tile 跳过 | 膨胀预判（dilate=2）、bicubic 回退、像素级掩码融合（7 处修改） |
| 0727 | `超分代码0723/code_0727.py` | 多上下文 GPU 并行 | block_reduce 向量化预扫描、CUDA Stream 双副本、ThreadPoolExecutor 双三次批量 |
| 0803 | `超分代码0803/code_0803baseline.py` | 去膨胀 + VRAM 门控 + 全管线计时 | 移除膨胀和像素融合、GPU warmup、NUM_CTX=1 默认 + VRAM 前置校验、多模型并行 worker、终端直出各段耗时占比表 |
| 0805 | `超分代码0803/code_0805.py` | MTA-Grid 网格原点对齐 + mini-batch | 掩码拓扑感知网格对齐、批处理（实测无加速）、MaskPath 缺省保护、全管线分阶段计时 |

### 关键架构转折

- **0720 → 0724**：引入掩码概念和 tile 级跳过策略，将全图 SR 改造为按需超分。
- **0724 → 0727**：GPU 推理并行化（多上下文 CUDA Stream）和预扫描向量化（block_reduce）。
- **0727 → 0803**：默认 NUM_CTX=1 + VRAM 前置校验；GPU warmup；全管线分阶段计时；多模型推理分支统一。
- **0803 → 0805**：引入 MTA-Grid 掩码拓扑感知的网格原点对齐（§5.8）；mini-batch 批处理（实测 compute-bound 下无加速，§5.6/§5.7）；MaskPath 缺省保护。

### 命名约定

- `code_MMDD.py`：按日期迭代，内网无 git 的替代方案
- `*_re.py`：修订版（revised），如 `code_0724baseline_re.py`
- `*baseline.py`：当前稳定基线版本

---

## 3. 管线架构全景图

```
XML配置文件    YAML配置文件      掩码mask.tif     输入图像input.tif
    │              │                │                 │
    ▼              ▼                ▼                 ▼
┌──────────────────────────────────────────────────────────────┐
│  1. 配置解析                                                  │
│     - MaskPath（可选，缺失则全图 SR）                          │
│     - 从 YAML 读取 t_ht, t_wd, pad, scl                      │
│     - 从 meta.xml 读取 DataBits 计算 max_DN                   │
└──────────────────────────┬───────────────────────────────────┘
                           ▼
┌──────────────────────────────────────────────────────────────┐
│  2. 输入图像与掩码加载                                        │
│     - GDAL ReadAsArray(全图) → uint16 ndarray (ori_img)        │
│     - 读掩码 tif → 二值化 → mask_binary ∈ {0,1}               │
│     - 校验形状一致性，打印 ROI 占比日志                        │
└──────────────────────────┬───────────────────────────────────┘
                           ▼
┌──────────────────────────────────────────────────────────────┐
│  3. Tile 网格划分                                             │
│     - dvd_2_grid: 分解为 tile_rows × tile_cols 格网           │
│     - 原图 reflect-padding → img_ori_wrap                      │
│     - 掩码 zero-padding → mask_wrapped（与 wrap 坐标系对齐）    │
│       tile 原生 reflect-padding 处理边界过渡                    │
└──────────────────────────┬───────────────────────────────────┘
                           ▼
┌──────────────────────────────────────────────────────────────┐
│  4. tile 分类：block_reduce 向量化预扫描                       │
│     - 将 mask_wrapped 重塑为 (rows, t_ht, cols, t_wd)          │
│     - .any(axis=(1,3)) → needs_sr_grid 布尔矩阵                 │
│     - O(1) numpy 操作，无 Python 循环                          │
└──────────────────────────┬───────────────────────────────────┘
                           ▼
              ┌────────────┴────────────┐
              ▼                         ▼
┌──────────────────────┐    ┌──────────────────────┐
│  5a. SR tile（GPU）   │    │  5b. 双三次（CPU）    │
│  GPU warmup 预分配     │    │  ThreadPoolExecutor  │
│  N 个模型副本          │    │  max_workers 自适应    │
│  CUDA Streams          │    │  cv2.INTER_CUBIC     │
│  多线程 worker         │    │  逐 tile 独立处理     │
│  lock_m(μs)→GPU并行   │    │                      │
└──────────┬───────────┘    └──────────┬───────────┘
           │                           │
           └──────────┬────────────────┘
                      ▼
┌──────────────────────────────────────────────────────────────┐
│  6. tile 拼接（stitching）                                     │
│     - fill_sr_grid: 将每个 tile 放置到 result_sr_wrap 中      │
│     - 通过互斥锁串行化，避免对共享输出数组的竞态条件             │
│     - pt_process 按行去重调用                                  │
└──────────────────────────┬───────────────────────────────────┘
                           ▼
┌──────────────────────────────────────────────────────────────┐
│  7. 去 padding                                                │
│     - cut_down_wrap: 裁掉 padding 恢复到原始尺寸               │
│     - result_sr = img_no_wrap（scale=2 时）                    │
│     - Clamp 值域到 [ori_min, ori_max]                         │
└──────────────────────────┬───────────────────────────────────┘
                           ▼
┌──────────────────────────────────────────────────────────────┐
│  8. 输出                                                      │
│     - writeTiff: 将结果 tif 写到 lq_path 目录下                │
│     - 更新 meta.xml（gsd, 积分时间）                           │
│     - 全管线分阶段计时摘要（终端打印各段耗时占比表）             │
│     - SRLOG.txt: 完整耗时 + GPU/CPU 内存 profile               │
└──────────────────────────────────────────────────────────────┘
```

---

## 4. 数据流

### 4.1 输入来源

| 来源 | 关键字段 | 用途 |
|------|---------|------|
| `-f config.xml` | DatarootLQ, OPT, MaskPath, SRScale, Suffix, CloudLimit | 任务级配置 |
| `*.yml`（通过 OPT 引用） | t_ht, t_wd, pad, scl, network_G, pretrain_model_G | 模型与 tile 参数 |
| `*_meta.xml` | DataBits, CloudPercent | 动态 DN 值域范围、云量过滤 |
| `*.tif`（输入原图） | 单通道 16-bit 全色图像 | 原始待超分图像 |
| `*.tif`（掩码） | 二值（0=背景, 1=ROI），分辨率与输入原图一致 | ROI 区域定义 |

### 4.2 坐标空间

```
原始图像空间               wrap 空间（补边后）           SR 输出空间
┌──────────┐         ┌──────────────────┐         ┌──────────────────────┐
│  ██      │  pad    │000000000000000000│  ×scl   │                      │
│  ██ ROI  │  ───►   │00  ██           │  ───►   │    ████              │
│          │         │00  ██           │         │    ████              │
└──────────┘         │000000000000000000│         │                      │
                     └──────────────────┘         └──────────────────────┘

mask_binary           mask_wrapped
（原始分辨率）          （zero-padding 填充，与 wrap 坐标对齐）
```

- `img_ori_wrap`：对原始图像做 reflect-padding（镜像翻折补边）。目的：为每个 tile 的边缘提供自然的上下文像素，抑制拼接伪影
- `mask_wrapped`：对二值掩码做 zero-padding（常数 0 填充），尺寸与 `img_ori_wrap` 完全一致。目的：tile 坐标在 wrap 空间下可直接复用，补边区不会被误判为 ROI
- tile 坐标由 `dvd_2_grid` 生成，使用的是 wrap 空间坐标系

### 4.3 缩放因子

所有处理逻辑假设 `scl = 2`。每个 tile 名义大小为 `t_ht × t_wd`，在此基础上四边各追加 `pad` 像素的补边区，实际送入模型的 tile 尺寸为 `(t_ht + 2×pad) × (t_wd + 2×pad)`。模型输出 `(t_ht + 2×pad)*scl × (t_wd + 2×pad)*scl`，随后 `fill_sr_grid` 从四边裁掉 `pad*scl`，将有效区域码放到结果网格中。

### 4.3A 输出 GSD 归一化：`resize_by_sat_rcsc` 逐星重采样

**背景常识**：卫星影像的"分辨率"由 GSD（像元地面采样间距）决定，SR 的几何前提是输入/输出落在标称 GSD 上。但不同卫星、以及同颗星升轨前后，产品 GSD 会偏离标称值——比如标称 0.5m 的星，实测出现 0.495 / 0.49 / 0.55m 等。因此 SR 完成后需把输出产品 GSD 归一化回标称值。这就是 `resize_by_sat_rcsc`（[util.py:1488](./util.py#L1488)，现行为 `resize_by_sat_rcsc_2`，[util.py:1521](./util.py#L1521)）干的事，在 `code_0817.py:584` 对 SR 结果调用。

**核心公式**：重采样系数 `fx = 当前GSD / 目标GSD`。

- `fx < 1` → 缩小图像（像元间距拉大，如 0.495→0.5 是 ×0.99）
- `fx > 1` → 放大图像（像元间距收窄，如 0.55→0.5 是 ×1.1）
- 注释里 "0.495 to 0.5" 即"当前GSD → 目标GSD"。

**RC 步不重采样**：RC 产品未经传感器校正，采样关系仍接近标称，`prev_step == "RC"` 时直接返回原图（打印 `SR after RC without resize().`）。**SC 步才需要**按卫星/成像日期逐项补偿。

**逐星重采样系数表**（SC 步）：

| 卫星 | 成像条件 | 当前GSD → 目标GSD | fx |
|---|---|---|---|
| JL1KF01A | < 2021-12-05（升轨前） | 0.495 → 0.5 | ×0.99 |
| JL1KF01A | ≥ 2021-12-05（升轨后） | 0.55 → 0.5 | ×1.1 |
| JL1KF02A | < 2023-12-01（升轨前） | 0.49 → 0.5 | ×0.98 |
| JL1KF02A | ≥ 2023-12-01（升轨后） | 0.55 → 0.5 | ×1.1 |
| JL1KF02B | — | 0.49 → 0.5 | ×0.98 |
| JL1GF05B | — | 0.15 → 0.2（**目标不是 0.5**） | ×0.75 |
| JL1KF01B / JL1KF01C 等其余 | — | 0.55 → 0.5 | ×1.1 |
| JL1GF04A / 裸 `PAN.tif` | — | RC 步，不重采样 | — |

**升轨为何改变 GSD**：卫星轨道抬升后，星下点像元覆盖地面范围变大 → GSD 从 ~0.49–0.495 变到 ~0.55。同一颗星按成像日期选择系数，日期从**文件名**里由 `get_img_date` 正则解析（`JL\d+\w+_PMSxx_<日期>_..._PAN.tif`）——这也是 SC 步要求输入用完整产品名 `<文件夹名>.tif` 的原因之一：日期字段就藏在里面；而裸 `PAN.tif`（RC / GF04A）没有日期、也不进这条逐日期分支。

**生产版简化**：`resize_by_sat_rcsc_2` 不再逐星判断，仅按 `resize_scale`（= 配置 `<SRScale>`，`code_0817.py:129`（内网存档，本仓库无））统一处理：`==2` 时不重采样，否则 `fx = resize_scale/2`。逐星表退化为函数内注释保留的 dead code（供追溯）——当前生产数据（JXGF07A03，SRScale=2）走 `resize_scale == 2` 分支，即不重采样。

### 4.4 I/O 瓶颈：原图与掩码的加载

卫星全色 tif 文件动辄数 GB。当前设计中，原始图像与掩码均通过 `util.read_img`（底层为 GDAL）在一切处理开始前完整读入 numpy 数组。这带来两个问题：

1. **内存压力**：同时持有 `ori_img` 和 `mask_raw` 两份完整数组，在办公级硬件上可能突破可用 RAM 上限。
2. **启动延迟**：GDAL 在第一个 tile 被处理前必须读完整个 tif。对于 4 GB 级别的图像，这部分耗时占固定开销的显著比例。

**潜在改进方向**：

- **GDAL 多线程解码（P0，一行代码）**：当前 `ReadAsArray` 为单线程解码。设置 `GDAL_NUM_THREADS=ALL_CPUS` + `GDAL_CACHEMAX=200` 即可启用 GDAL 内部并行解码，压缩 TIFF 预期加速 30%–80%。本管线为单进程调用，无 PyTorch DataLoader Worker 冲突风险。`GDAL_CACHEMAX` 需显式限制，否则默认值（物理内存 5%）在分块读取循环中会持续膨胀，造成"伪内存泄漏"。
  > **注（已落地）**：当前环境已应用 GDAL 多线程解码（`GDAL_NUM_THREADS=ALL_CPUS` + `GDAL_CACHEMAX` 显式限制），此条**不再属于待办改进**。§4.4 仍有效的待办仅剩掩码窗口读取、掩码存储格式选择等。

- **按需窗口读取掩码**：掩码仅在 tile 分类预扫描阶段逐 tile 被查询，可通过 GDAL 窗口读取按需加载对应区域，而非一次性读入整个数组。同时也消除了 `mask_wrapped` 的内存重复问题。
- **掩码存储格式的重新选择**：二值掩码以未压缩 tif 存储，空间开销与原始图像相当。替代方案：
  - **游程编码 (RLE)**：对稀疏 ROI 可实现 O(1) 级存储压缩；预扫描退化为 tile 边界框与游程序列的交集测试。
  - **多边形 / WKT 表达**：掩码生成工具本身就通过鼠标点选捕获了多边形顶点。直接存储多边形（例如以 WKT 格式写入 XML 配置文件），在存储层跳过栅格化，延迟到 tile 分类时刻按需计算，且可在 tile 粒度进行。
  - **低分辨率代理掩码**：对掩码进行 2–4 倍下采样，仅用于预扫描阶段。完整分辨率掩码仍用于最终参考。这以少量误命中 SR tile（假阳性）为代价，换取预扫描速度和内存占用的显著改善。

### 4.5 外部依赖速查

> **（2026-08-25 更新，8.25 最新）**：`util.py` 已还原入库——[util.py](./util.py)（1763 行，2026-08-17 按扫描件逐函数还原）。本表 `util.*` 系列函数均可在该文件直接查看源码；`models/`、`options/` 等其余外部依赖（服务器 mmsr_bundle）仍不在仓库，仅列契约。

以下函数来自 `utils/util.py`（现已在仓库，见上）、`options/options.py` 与 `models/`（后两者仍在服务器 mmsr_bundle，仓库无源码）。本表保留为**契约速查**：一眼看签名与行为要点；深挖实现可直接跳 `util.py` 对应行号。

| 函数 / 模块 | 输入 | 输出 / 副作用 | 备注 |
|------------|------|--------------|------|
| `util.read_img(path)` | tif 文件路径 | `np.ndarray` (uint16, H×W 或 H×W×C) | 底层 GDAL ReadAsArray，全图读入内存 |
| `util.get_cfg_value(xml, tag)` | XML 路径 + 标签名 | `str` 或 `None` | 标签不存在时**抛异常**（非返回 None），需 try-except（0805 已在 MaskPath 读取处包裹） |
| `util.dvd_2_grid([paths], pad, t_ht, t_wd, ...)` | 图像路径列表 + tile 参数 | `(tile_grid_dict, tl_info)` | tile_grid_dict: key→[top,bottom,left,right]；tl_info 含 wrap 量和 ori 尺寸 |
| `util.pad_reflect(img, top, btm, lt, rt)` | ndarray + 四边 padding 量 | padded ndarray | 镜像翻折补边 |
| `util.init_res_img(tl_obj, scl)` | wrap 信息 + scale | 零初始化的 `np.ndarray` | SR 输出缓冲，尺寸 = wrap 尺寸 × scl |
| `util.fill_sr_grid(key, tile, dst, ...)` | tile key + SR tile + 输出数组 | 写入 dst（原地修改） | 自动裁掉 `pad*scl` 边，非线程安全 |
| `util.cut_down_wrap(img, ori_ht, ori_wd, ...)` | wrapped 图像 + 原始尺寸 | 裁边后的图像 | `img[wrap_top*scl:(ori_ht+wrap_top)*scl, ...]` |
| `util.writeTiff(img, path, ...)` | ndarray + 输出路径 | 写入磁盘 tif | — |
| `util.get_LQ_tile(tile, max_DN)` | ndarray tile + DN 上限 | `torch.Tensor` (归一化到 [0,1]) | 模型输入的前处理 |
| `util.tensor2img_fast_trt(tensor, ...)` | 模型输出 tensor | `np.ndarray` (uint16) | 反归一化 + 值域裁剪 |
| `util.pt_process(key, tl_obj, ...)` | tile key + wrap 信息 | 终端打印进度 + 写 SRLOG | 按行去重，不影响计算结果 |
| `util.check_sr_previous_step(lq_path)` | 数据目录路径 | SR 历史步骤信息或 `None` | 外部图无 SR log 时会崩，需 try-except |
| `util.get_l1_pan_tif_rcsc(...)` | 目录 + tif 类型 + SR 步骤 | tif 文件绝对路径 | 在目录下定位 L1 PAN tif |
| `util.setup_logger(name, ...)` | logger 名称 | 配置好的 `logging.Logger` | base logger 用于任务日志，gpu logger 用于健康检查 |
| `util.write_print_gpu_mem_info(gpuid, log)` | GPU ID + 日志路径 | 写入 GPU 显存使用量 | 依赖 pynvml |
| `option.parse(yml, is_train)` | YAML 路径 | 配置 dict | 解析模型/tile 参数 |
| `create_model(opt)` | 配置 dict | `SRModel` 实例（PyTorch） | 加载 .pth 权重，构造 ESPAN/Restormer/RealESRGAN |
| `npct.load_library(path, ".")` | .so 路径 | C 动态库句柄 | **模块导入时执行**，.so 不存在则 import 阶段崩溃 |
| `torch.cuda.Stream()` | — | CUDA stream 句柄 | 多上下文并行的隔离单元 |

### 4.6 中间产物命名规则（SR 环节与 MTFC 环节）

> **经验（2026-08-26 问到）**：遥感影像处理管线里，SR 与 MTFC 两个环节对「处理结果 / 原图（中间产物）」的命名规则**相反**，容易混淆。最基础的遥感命名规则不在此列，这里只记两条中间产物约定。

**SR 环节**：输入 `PAN.tif`，超分后产生两个文件（六位数为两文件共享的后缀）：

| 产物 | 命名 | 说明 |
|------|------|------|
| 超分结果 | `PAN_<六位数>.tif` | SR 主产物 |
| 未超分原图（中间产物） | `PAN_<六位数>_NOSR.tif` | `_NOSR` = No Super-Resolution，原图留档，供对照/回退 |

要点：`_NOSR` 标记的是「**没做超分**」的那份，主产物反而占用不带 `_NOSR` 的名字。

**MTFC 环节**：输入 `PAN.tif`，处理前先存一份原图，再输出：

| 产物 | 命名 | 说明 |
|------|------|------|
| MTFC 处理结果 | `PAN` | 环节输出（主产物） |
| 原图（中间产物） | `PAN_ori` | `_ori` = original，处理前留档 |

要点：MTFC 用 `_ori` 标记「**原图**」，处理结果反而占用干净的 `PAN` 名。

> **一句话总结**：两个环节都是「原图作为中间产物、名字带后缀；处理结果占更干净的名字」——只是 SR 的原图后缀是 `_NOSR`、MTFC 的是 `_ori`。读文件时先看是哪个环节再对号入座，别混。

### 4.6A MTFC 与 SR：联系、区别与生产顺序

> 承接 §4.6 的命名规则，这里补一层原理：两个环节都在"提升清晰度"，但层次不同、先后不同。

**联系（为什么常被一起提）**

- 都作用于 PAN 全色影像，都影响观感"清晰度/纹理"，最终都提升图像质量；
- 成像模型上是一件事的两端：输入图像 ≈ 理想场景 ⊗ PSF + 噪声（⊗ 为卷积，PSF 即点扩散函数）。
  **MTFC 做逆卷积**，把被 PSF 压扁的高频恢复回原始分辨率下的样子；**SR 再上采样重建**，补出采样率以下的细节。两者串起来 = 先复原、再重建。
- 客户抱怨"纹理不行"时，两个环节都可能负责：MTFC 决定基础清晰度，SR 决定能否再往上提（见 §1 背景八卦的 KF02 案例）。

**区别**

| 维度 | MTFC | SR |
|------|------|------|
| 本质 | 复原（restoration）：逆滤波 / 去卷积 | 重建（reconstruction）：生成式超分 |
| 输出分辨率 | 不变（1×） | 放大（scl=2，模型固定） |
| 信息增益 | 恢复"已采到但被系统压扁"的高频，增益受限于真实采样信息量 | 重建超出采样率的高频，依赖模型先验 |
| 输入→输出关系 | 物理确定（已知 PSF/MTF 可精确反演） | 非确定（模型输出是估计） |
| 计算性质 | 线性确定性滤波 | 非线性模型推理（ESPAN 等） |
| 典型风险 | 过补偿 → 振铃、噪声放大 | 幻觉、纹理失真、tile 拼接缝 |

**实际生产环节顺序**

MTFC 在 L1 处理链**内**、几何重采样**之前**；SR 在 L1 产品定型**之后**，属后处理增强环节：

```
传感器原始帧 → 辐射校正(RC) → MTFC → 几何校正(SC/RPC) → L1 PAN 产品 → SR（局部超分）→ 成品
```

- **MTFC 必须在几何重采样之前**：几何校正的插值/重采样会再次平滑高频，MTFC 放前面补偿效果才不被吃掉；放后面等于先加模糊再补偿，事倍功半。
- **SR 放最后**：输入是成型的 L1 产品 `PAN.tif`，按 ROI 局部增强，输出 `PAN_<六位数>.tif` 并留存未超分原图 `PAN_<六位数>_NOSR.tif`。
- **与你仓库代码的对应**：MTFC 不在 SR 管线内实现，只以元数据存在——`util.judge_meta`（[util.py:32](./util.py#L32)）在 meta.xml 的 `ProcessInfo` 里补 `MtfCompensation` 节点（默认写 `NO`），记录"上游是否做过 MTFC"；SR 侧通过 `util.check_sr_previous_step` 感知前序步骤（§4.5 表）。§4.6 的命名规则也正是这条顺序的体现：MTFC 环节留存 `PAN_ori`、产出 `PAN`；SR 环节再以 `PAN` 为基础产出超分主产物。

---

## 5. 核心算法：掩码引导的 tile 跳过策略

### 5.1 掩码预处理

```
Step 1: 读取掩码 tif → 形状校验 → 二值化（阈值 >0 → 1）
        多通道取第一维，单通道直接使用
Step 2: zero-pad 二值掩码使其与 img_ori_wrap 尺寸对齐。
        使用 zero-pad（常数 0 填充）而非 reflect-pad：补边区不是 ROI。
```

掩码仅用于 tile 分类（有白色像素 → SR，纯黑 → 双三次），不做额外处理。
tile 原生 reflect-padding 为每个 tile 提供充足的边界上下文，足以抑制拼接伪影。
SR tile 的数量由 ROI 真实覆盖范围决定，分类精确且不引入额外开销。

### 5.2 基于 block_reduce 的 tile 分类

```python
# 向量化 numpy 预扫描 — 单条表达式完成所有 tile 的分类，替代逐 tile 的 Python 循环
needs_sr_grid = mask_wrapped[:mask_h, :mask_w].reshape(
    tile_rows, t_ht, tile_cols, t_wd).any(axis=(1, 3))  # 0803: .any() 替代 .sum()
```

原理：将 mask_wrapped 重新排列为四维张量 `(行数, t_ht, 列数, t_wd)`，沿 tile 内部的高和宽两个空间轴取 any 判断。如果某个 tile 区域内掩码有任何一个白色像素，标记为需要 SR。

与 0724 版本的逐 tile Python 循环相比，该操作完全在 numpy 的 C 底层完成。0803 将 0727 的 `.sum() > 0` 简化为 `.any()`，语义更直接且微幅更快。

### 5.3 SR 路径：多上下文 GPU 并行

#### 默认行为

**0803 默认 `NUM_CTX = 1`（单上下文）**。部署目标为 RTX 3060（12 GB），单模型副本约需 ~4.5 GB
（tile=500，实测）至 8–12 GB（tile=2500），默认单上下文确保 3060 上不 OOM。
开发服务器 RTX 3090（24 GB）上可手动解锁为 2：

```python
VRAM_PER_REPLICA_MB = 2048      # 每个模型副本的保守显存估算
VRAM_SAFETY_MARGIN_MB = 2048    # 安全余量
free_mb, _ = torch.cuda.mem_get_info()
free_mb = free_mb // (1024 * 1024)
if free_mb < VRAM_PER_REPLICA_MB * NUM_CTX + VRAM_SAFETY_MARGIN_MB:
    NUM_CTX = 1  # 降级为单模型推理，避免 OOM
```

> **注（0803 实测）：以上 VRAM 门控是死代码。** 非 TRT 分支中 `NUM_CTX` 在门控判定前已恒为 1（源码注释 `bump to 2 when torch>=2.0`），判定时阈值恒为 2048×1+2048=4096 MB，分支只能把 1 赋成 1 并打印 `degraded to single-context`——脚本内**不存在任何将 `NUM_CTX` 设为 2 的路径**。根因是内网机 torch 版本 <2.0，多流并发不可用，**GPU 多实例并行推理名存实亡，`NUM_CTX` 恒 1**。因此：
> - `logger.info("VRAM low ... degraded to single-context")` 是冗余且误导的打印，可适当删减；
> - 上文"开发服务器 RTX 3090 上可手动解锁为 2"在当前 torch 版本下不可行，仅代表设计意图（待 torch>=2.0 后落实）。

#### GPU warmup

在进入 tile 循环前，用一个 dummy tensor（尺寸 = `1 × 1 × (t_ht+2×pad) × (t_wd+2×pad)`）对每个模型副本各执行一次前向传播并同步。目的：预分配 CUDA 显存池，避免运行时首次推理的 lazy allocation 导致跨流隐式同步（cross-stream implicit sync），确保多 worker 真正并行。

#### 多模型并行 worker（`_run_sr_parallel`）

worker 按 yml 关键字分发到四个推理分支：

| 模型 | yml 关键字 | 推理路径 | 额外处理 |
|------|-----------|---------|---------|
| ESPAN | `espan` | feed_data_grid → test → get_current_visuals → tensor2img_fast_trt | — |
| Restormer | `restormer` | 同上 + gamma 校正 tile 输入 | ImgHistMatch 直方图匹配（.so 库） |
| RealESRGAN TRT | `realesrgan_trt` | get_LQ_tile → .cuda().half() → trt_test | FP16 JIT 引擎 |
| RealESRGAN | `realesrgan` | feed_data_grid → test → get_current_visuals → tensor2img_fast_trt | — |
| 回退 | 均不匹配 | cv2.resize INTER_CUBIC | 纯 CPU，保底不崩 |

#### 线程安全设计（五把锁）

```
                    ┌─────────────┐
    Queue ─────────►│  Worker 0   │──► model[0] @ stream[0] ──┐
    (sr_tiles)      │  Worker 1   │──► model[1] @ stream[1] ──┤
                    └─────────────┘                            ▼
                                                     fill_sr_grid (lock_w)
                                                     pt_process   (lock_p, 行去重)
                                                     timing acc   (lock_t)
                                                     SRLOG write  (lock_log)
```

| 锁 | 保护对象 | 临界区范围 | 争用程度 |
|----|---------|-----------|---------|
| `lock_m` | model.feed_data_grid + test | 仅 feed+test 调用（μs 级），GPU 推理在锁外异步执行 | 极低 |
| `lock_w` | result_sr_wrap 共享输出数组 | fill_sr_grid 全函数 | 中等（tile 数量 × 写入时长） |
| `lock_p` | rows_seen 集合 | pt_process 按行去重 | 极低（每行只触发一次） |
| `lock_t` | acc 字典（计时累加器） | 计时值累加 | 极低（单次浮点加法） |
| `lock_log` | SRLOG.txt 文件句柄 | 文件写入 | 低 |

关键设计决策：`lock_m` 仅持有 feed+test 的调用权（微秒级），释放后 GPU 推理在各自的 CUDA stream 上异步执行，worker 之间真正并行。这是 0803 相对于 0727（`stream.synchronize()` 在锁内）的核心优化。

### 5.4 双三次路径：CPU 线程池并行

非 SR tile 通过 `ThreadPoolExecutor` 在 CPU 上并行处理：

```python
max_w = min(4, os.cpu_count() or 2)
with ThreadPoolExecutor(max_workers=max_w) as executor:
    futures = [executor.submit(_bicubic_worker, key, grid) for ...]
```

每个 tile 独立做 `cv2.resize(..., INTER_CUBIC)` 放大 2 倍并裁剪值域。与 0727 不同，0803 的 `max_workers` 从硬编码 4 改为自适应 `min(4, cpu_count)`，避免在低核心数机器上过度订阅。

所有 tile 的 resize 完成后，`fill_sr_grid` 串行执行（避免对共享输出数组的竞态条件）。

### 5.5 全图 SR 回退

当 `MaskPath` 未配置或为空时（`do_local_sr = False`），管线整体退化为全图 SR 模式，但仍复用同一套多上下文 GPU 并行 worker 架构。这保证了向后兼容——不配掩码的旧 XML 配置文件无需任何修改即可正常运行。

### 5.6 批处理优化方向（mini-batch 已落地；实测无加速，见 §5.7）

将 N 块同尺寸 tile 沿 batch 维拼接为 `(N, 1, H, W)` 送入模型，可减少 kernel launch 次数（N 次 → 1 次）
并摊薄每次前向的固定开销（Python 调度、同步点）。

**实测结论（2026-08-11，tile=500 ESPAN / RTX 3090）**：GPU utilization 已达 ~100%、显存仅
~4.5GB/24GB。GPU 计算已饱和（compute-bound），扩 batch 只增加显存、不增加速度——`BATCH=4`
实测无显著加速。早期"GPU 计算单元利用率受限于单 tile 像素量、batch 可提升 CUDA core 占用率"
的前提在本次测量中不成立，收益预期作废。

约束：当前所有 SR tile 尺寸一致（同一次 dvd_2_grid 切分保证）；额外显存开销为中间张量 × N。
另需模型侧 `get_current_visuals` 支持 batch（`[0]` 折叠点已修复，见 §5.7）。

### 5.7 mini-batch 落地记录（2026-08-11，含回退指引）

> 状态：模型侧 3 个 `SR_model*.py` 已同步修改；`BATCH=4` 可运行但实测无显著加速，瓶颈定位中（GPU 占用 / CPU 转换 / 网络 batch 友好性）。

#### ① 管线侧（`超分代码0803/code_0805.py` / 服务器 `0807.py`）

- `_worker` 改为 mini-batch：每次从队列抓 `BATCH` 块同尺寸 tile → `torch.cat(dim=0)` 拼成 `(N,1,H,W)` → 单次 `feed_data_grid`+`test()` → 结果按 `vis['rlt'][i:i+1]` 拆分逐块 `fill_sr_grid`/`pt_process`。
- TRT 引擎分支强制 batch=1（引擎固定输入尺寸）。
- 开关：`BATCH = N`（本地 0805 约 L285；服务器搜 `BATCH =`）。`1` = 原串行行为。

#### ② 模型侧（服务器 `codes/models/` 下 3 个 `SR_model*.py` 包装文件）

**修改点**（三个文件内均为 `get_current_visuals` 方法中的同一行）：

```python
# 改前
out_dict['rlt'] = self.fake_H.detach()[0].float().cpu()
# 改后
out_dict['rlt'] = self.fake_H.detach().float().cpu()
```

| 文件 | `get_current_visuals` 所在行 |
| --- | --- |
| `SR_model.py` | L177 |
| `SR_model_0616tmp.py` | L153 |
| `SR_model_0616nvidiasmilog.py` | L163 |

**修改原因**：

- `test()` 内 `self.fake_H = self.netG(self.var_L)` 对整批输入执行前向，网络输出为 `(N,1,H,W)`。
- `get_current_visuals` 中 `[0]` 为整数索引，将 batch 维固定取第 0 个样本，batch>1 时仅返回第一个 tile 的结果。
- 管线侧按 `vis['rlt'][i:i+1]` 切片逐块取结果，第 2..N 块取到空张量 `(0,1,H,W)`；经 `tensor2img_fast_trt` 转为 `(0,1000,1200)`，`fill_sr_grid` 回填时广播失败（`could not broadcast input array from shape (0,1000,1200) into shape (1000,1000)`）。

**三个文件全部修改的原因**：`create_model` 依据配置选择实际加载的包装文件，无法静态确定；对三个候选文件施加相同修改以保证生效，且修改语义一致。

**影响说明**：

- batch=1 时像素值不变，仅 `rlt` 返回形状由 `(C,H,W)` 变为 `(1,C,H,W)`。
- `tensor2img_fast_trt` 兼容 4D 输入（`realesrgan_trt` 分支输入即为 4D）。
- `get_current_visuals` 中 `LQ`、`GT` 两行的 `[0]` 未修改，不影响 SR 输出路径。
- 潜在影响：restormer 分支与基线版本（0720/0724baseline）将 `vis['rlt']` 整体传入 `tensor2img_fast`（非 trt 版），原接收 3D；运行这些路径需另行验证 4D 兼容性。

#### ③ 回退条件

- `BATCH=4` 实测无加速（GPU 已 compute-bound）→ 恢复 `BATCH=1`。
- 换模型、运行基线或 restormer 分支出现形状类报错 → 恢复模型侧 `[0]`。
- 不再需要并发推理时。

#### ④ 回退命令（服务器，`mmsr_bundle_240617` 目录下）

```bash
# 模型侧：为三个文件加回 [0]
sed -i 's/self\.fake_H\.detach()/self.fake_H.detach()[0]/' \
  codes/models/SR_model.py \
  codes/models/SR_model_0616tmp.py \
  codes/models/SR_model_0616nvidiasmilog.py
# 管线侧：把 BATCH 改回 1
```

#### ⑤ 改动文件清单

| 文件 | 位置 | 改动 |
| --- | --- | --- |
| `codes/models/SR_model.py`（服务器） | `get_current_visuals` | `self.fake_H.detach()[0]` → `self.fake_H.detach()` |
| `codes/models/SR_model_0616tmp.py`（服务器） | `get_current_visuals` | 同上 |
| `codes/models/SR_model_0616nvidiasmilog.py`（服务器） | `get_current_visuals` | 同上 |
| `超分代码0803/code_0805.py` | `_worker` + `BATCH` 常量 | 逐 tile 前向 → mini-batch；默认 `BATCH=1` |

### 5.8 MTA-Grid：掩码拓扑感知的网格原点对齐

**问题**：均匀 tile 网格与任意形状 ROI 存在"错位浪费"——网格线若穿过 ROI 包围盒，SR tile 数会比最优多出整行/整列。

**做法**：将网格整体平移 δ（不改 tile 尺寸、不改总 wrap 尺寸），让网格线贴住 ROI bbox 边缘，使 SR tile 数最少。纯 numpy 预处理，不触碰推理/拼接路径。

**约束**：偏移幅度受 wrap 余量限制（`wrap − δ ≥ pad`），贴边 ROI 或 wrap 余量小时对齐只能部分生效；多块分离 ROI 按并集 bbox 规划，可能帮一块害另一块。

**实测**：紧凑 1% ROI、tile=1000 时 4→1（理论最优）；中等 ROI（18.49%、tile≈500）仅 94→93、halo 0.27→0.26——收益集中在紧凑 + 小 ROI 场景。

**详见**：算法细节、候选集推导与数值验证见 `mtagrid_design_0808.md`（及 `tools/total_plan_0804.md` §2.3/§2.4）。

---

## 6. ROI 边界处理

tile 分类严格按 ROI 重叠判断：与掩码有交集的 tile 走 SR，无交集的走双三次。
tile 原生 reflect-padding 为模型提供充足的边界上下文，抑制拼接伪影。
ROI 边界像素质量由两方面共同保证：reflect-padding 为 SR tile 提供自然的边缘上下文，
`fill_sr_grid` 的 tile 重叠区裁剪（`pad*scl` 裁边 + 邻接 tile 平滑过渡）处理拼接处过渡。

**残余浪费**：SR tile 内非 ROI 像素的 GPU 推理是 tile 粒度处理的固有开销——tile 是不可分割的最小处理单元。要彻底消除，需 TRT 动态输入尺寸支持，对每个 ROI 连通域单独裁剪推理。

---

## 7. 多模型支持

| 模型 | yml 关键字 | TRT 引擎 | 推理分支 | Gamma | 额外处理 |
|------|-----------|---------|---------|-------|---------|
| ESPAN | `espan` | tensor2img_fast_trt | feed→test→visuals | 无 | 主要目标模型 |
| Restormer | `restormer` | tensor2img_fast | feed→test→visuals | γ=1.45 | ImgHistMatch 直方图匹配 |
| RealESRGAN TRT | `realesrgan_trt` | trt_test (FP16) | .cuda().half()→trt_test | 无 | JIT 加载独立引擎 |
| RealESRGAN | `realesrgan` | tensor2img_fast_trt | feed→test→visuals | 无 | PyTorch 原生推理 |

---

## 8. 性能特征与瓶颈分析

> 耗时数据高度依赖图像尺寸、ROI 面积和 tile 配置，以下为 10000×10000 全景上的参考量级，
> 不同场景需以 `tm` dict 终端输出为准，不宜直接外推。
>
> **实测基准（2026-08-11，tile=500 ESPAN / RTX 3090）**：GPU utilization ~100%（compute-bound）、
> 显存仅 ~4.5GB / 24GB（≈1/6，显存非瓶颈）。全文档瓶颈判断以此为准：提速方向在算力侧
> （TRT、减少 tile；多卡判定为伪需求，见 §12），而非填充显存或并发复制模型。

### 8.1 参考耗时量级（0803 版本，tile=500）

| 场景 | 参考耗时 | SR tile 占比 | 主导瓶颈 |
|------|---------|-------------|---------|
| 极小 ROI（≈2 tile） | ~16s | <1% | **冷启动**（模型加载 + warmup） |
| 1/4 图区域超分 | ~40s+ | ~25% | SR 推理 + 冷启动 |
| 全图超分 | ~147–164s（tile=1000/500 实测） | 100% | SR 推理 |

> **本地 3060 全管线实测（2026-08-24，图幅 30833×30954，真实生产掩码）**：加速前（`code_0724baseline_re.py`，全局超分）约 **4458s（≈74 分钟）**；加速后（`code_0817_prod.py`）仅 **242s（≈4 分钟）**，提速约 **18×**。
> 与上表服务器 3090 的量级不同：这是部署目标机（RTX 3060）上的整管线实测，掩码为真实生产 ROI 掩码（非合成掩码）；且 baseline 为全局超分、加速版走掩码引导局部超分路径。

### 8.2 瓶颈拆解

```
冷启动（T_cold）           SR 推理（T_SR）            后处理（T_post）
┌─────────────────┐    ┌──────────────────┐    ┌────────────────┐
│ 模型加载 + warmup │    │ 随 ROI 面积线性增长 │    │ cut + clamp    │
│ I/O + wrap + mask │    │ N_SR × T_TRT      │    │ writeTiff      │
│ （≈9s，实测）    │    │ / NUM_CTX          │    │ meta 更新       │
└─────────────────┘    └──────────────────┘    └────────────────┘
     ↑                        ↑                      ↑
  ROI 越小占比越高        可被 tile 缩小优化       基本固定，可忽略
```

**关键洞察**：

- **冷启动是极小 ROI 场景的第一瓶颈**。对于 2 tile 级别的局部超分，T_cold 占比超 70%，
  后续优化重点应放在模型加载缓存机制上——在多图连续处理场景下复用已加载的模型实例。
- **SR 推理随掩码区域线性增长**。ROI 面积从 1% 到 25% 时，T_SR 从几秒增长到数十秒。
  这是 tile 粒度处理的固有特征，无法通过调参消除。
- **tile 缩小对全局耗时的影响递减**。tile 从 2500→500 时收益显著（跳过率跃升），
  500→250 则几乎无收益（T_cold + T_bookkeeping 已占主导）。
- **tile 缩小会增加 GPU 实际计算量（pad 冗余）**：模型输入为 `(tile+2×pad)²`，pad=50 固定时 tile
  越小 pad 占比越高（500 → 600²，pad 额外 ~44%；1000 → 1100²，~21%），相邻 tile 的 pad 重叠区
  还被重复计算。实测全图 SR：tile=1000 的 GPU 推理 133.5s < tile=500 的 149.5s（差 16s）——
  GPU 时间随 pad 冗余变化，并非固定。

### 8.3 真实运行计时样例

以下为一次 10000×10000 局部 SR（ROI=1%，tile=500，NUM_CTX=2，RTX 3090）的终端输出：

```
Input ori img min 0.0, max 65535.0, mean 7451.5, uint16, (10000, 10000)
Local SR mask loaded, ROI pixels: 997067 / 100000000 (1.00%)
Local SR mask wrapped (no dilation), shape: (10500, 10500)
Local SR: 9 tiles SR, 432 tiles skipped (bicubic)

================================================
  Pipeline Timing Summary  (tile=500  workers=2)
------------------------------------------------
  配置解析                     0.0s  (  0.0%)
  图像 I/O                     1.3s  (  8.0%)
  预处理 (grid+wrap+mask)      0.2s  (  1.0%)
  模型加载                     7.5s  ( 46.8%)
    Phase A: tile预扫描        0.0s  (  0.1%)
  SR 推理 (GPU)                5.2s  ( 32.6%)
  双三次插值 (CPU)             0.5s  (  2.8%)
  tile 拼接回填                0.5s  (  2.9%)
  进度日志写入                 0.0s  (  0.0%)
  后处理 (输出+写盘)           3.4s  ( 21.3%)
------------------------------------------------
  总耗时                      16.1s
================================================
```

关键读数：9 块 SR tile（441 块中），模型加载占 47% 是第一瓶颈，SR 推理占 33%，写盘占 21%。
这是 workers=2（手动解锁双上下文）的结果，单上下文（NUM_CTX=1）时 SR 推理约占 40%+。
ROI 面积增大时 SR 推理占比会上升，ROI 更小时模型加载占比会更高。

> **注**：该示例为双上下文（NUM_CTX=2）旧配置；当前代码受 torch<2.0 限制 `NUM_CTX` 恒为 1（§5.3），
> 此样本仅作历史参考，无法复现。

**MTA-Grid 中等 ROI 实测补充（2026-08-11，tile≈500，ROI=18.49%）**：`SR tiles 94 → 93`
（仅省 1 块）、halo 0.27 → 0.26、规划耗时 658ms。与理论"紧凑 1% ROI 4→1"相比，中等/大 ROI 场景
网格对齐收益显著收窄——其价值主要集中在极小 ROI。

### 8.4 tile 尺寸选择建议

| tile | 优势 | 风险 | 建议 |
|------|------|------|------|
| 500 | 跳过率最高，总耗时最短 | **过小**：模型感受野受限，SR 质量可能劣化 | 仅限性能压测，不建议生产 |
| 1000–1250 | 跳过率良好，质量有保障 | 全图实测 SR tile 400→100（较 500 少 4 倍，非原"多 2–4 块"） | **推荐生产区间** |
| 2500 | 质量最稳定 | 跳过率低，小 ROI 场景浪费明显 | 大 ROI / 全图 SR 时使用 |

> tile=500 虽有最佳性能数据，但 mentor 评估认为对 ESPAN 模型（23 残差块，有效感受野
> 100–200px）而言过小，存在边界质量隐患。生产环境建议以 1000 或 1250 为起点，
> 在具体场景下做 A/B 对比后再决定。

**实测（2026-08-11，全图 SR，ESPAN / RTX 3090，workers=1）**：

| 指标 | tile=500 | tile=1000 |
| --- | --- | --- |
| 总耗时 | 164.3s | 146.8s |
| SR 推理 (GPU) | 149.5s (91.0%) | 133.5s (90.9%) |
| 模型加载 | 8.2s | 7.5s |
| tile 拼接回填 | 0.6s | 0.5s |
| 后处理（写盘） | 4.6s | 3.4s |

结论：**GPU 推理为绝对瓶颈（~91%）**；tile 尺寸的总差 17.5s 中 GPU 项占 16s，来源是 **pad 冗余**
（模型实际计算 `(tile+2×pad)²`，pad=50 固定下 500 较 1000 多算 ~19% 输入像素），而非 CPU 开销
（fill/pt 差仅 0.1s）。batch 无法规避 pad 冗余（4×500 的 pad 总量 > 1×1000）；生产选 1000。

### 8.5 tile 尺寸与生成质量（待系统评估）

不同 tile 尺寸对质量的影响目前仅有定性判断，缺少定量测量。以下为基于模型特性的理论分析：

| 维度 | 大 tile（2500） | 中 tile（1000–1250） | 小 tile（500） |
|------|----------------|---------------------|---------------|
| tile 间拼接缝数量 | 最少（≈4×4 格网） | 中等（≈8–10 格网） | 最多（≈20×20 格网） |
| ROI 内 SR 质量 | 最佳（感受野完整覆盖） | 良好 | 潜在退化：感受野≈100–200px，tile 边缘区域上下文不足 |
| pad 相对占比 | pad=50 仅 2% 扩边 | 5%–4% 扩边 | pad=50 达 10% 扩边，模型边缘像素依赖 padding 程度最高 |
| 拼接伪影风险 | 最低 | 低 | 最高（拼接缝数量 ↑ + 单块质量 ↓ 叠加） |

**建议评估方法**：
- 准备 3 张含典型 ROI 的测试图（覆盖不同面积和边界复杂度）
- 每张图跑 tile=500 / 1000 / 1250 / 2500 各一轮局部 SR
- 输出保存后人工对比：拼接缝可见度、地物边缘锐利度、纹理一致性
- 如有全图 SR 结果作为参考，以 PSNR/SSIM 量化 ROI 内质量差异

### 8.6 TRT 引擎约束（已验证）

实际输入 `(tile + 2×pad)` 必须为偶数（pixel-shuffle 要求）：

| tile | 输入尺寸 | 结果 |
|------|---------|------|
| 500 | 600 | ✅ |
| 625 | 725 | ❌ `AttributeError: fake_H` |
| 1000 | 1100 | ✅ |
| 1250 | 1350 | ✅ |
| 2500 | 2600 | ✅ |
| 5000 | 5100 | ❌ OOM |

---

## 9. 性能模型

```
总耗时 = T_cold + T_scan + T_bicubic + T_SR + T_bookkeeping + T_post

T_cold       = 模型加载 + warmup + I/O (全图+掩码 tif 读取) + prep (grid/wrap/mask)
               ROI 无关，约占 ~9s（实测：模型加载 7.5–8.2s + I/O 0.7s + prep 0.1s）。
               极小 ROI 场景的第一瓶颈。
T_scan       ≈ O(1) numpy block_reduce，毫秒级，可忽略
T_bicubic    ≈ 常数 × N_skip，CPU 线程池并行，N_skip 很大时才累计到秒级
T_SR         = T_TRT × N_SR / NUM_CTX，随 ROI 面积线性增长
T_bookkeeping ≈ fill_sr_grid + pt_process，随总 tile 数微幅波动
T_post       ≈ cut + clamp + writeTiff + meta 更新，基本固定
```

> **注**：`T_SR` 中 `/ NUM_CTX` 项当前实际恒为 1（torch<2.0，§5.3），并行缩放不可用；
> compute-bound 下 T_SR 由 GPU 吞吐决定，与 SR tile 数成正比。

**优化优先级**（按 ROI 大小分场景）：

| 场景 | 第一瓶颈 | 优化方向 |
|------|---------|---------|
| 极小 ROI（< 2%） | T_cold | 模型缓存复用、GDAL 多线程解码、掩码窗口读取 |
| 中等 ROI（2%–25%） | T_SR + T_cold 混合 | tile 尺寸调优 + 缓存 |
| 大 ROI / 全图 | T_SR | 动态 tile 划分（多 GPU 分发判定为伪需求，部署目标为桌面端单卡，见 §12；NUM_CTX 提升受 torch<2.0 限制不可行，见 §5.3） |

---

## 10. 配置参考

### XML 新增字段

```xml
<MaskPath>/absolute/path/to/mask.tif</MaskPath>
```

`MaskPath` 为可选字段，不配置则退化为全图 SR。`MaskDilateFactor` 在 0803 中已移除。

### YAML tile 配置

```yaml
t_ht: 1000      # tile 高度（(tile + 2×pad) 必须为偶数；生产推荐 1000–1250）
t_wd: 1000      # tile 宽度
pad: 50          # tile 间重叠 padding
scale: 2         # 超分倍数
```

### 基准测试脚本

```
run_bench.sh <xml> [runs] [label]  — 单个配置跑 N 轮，输出到 bench_results/<label>/
run_all.sh   [runs]                 — 依次跑 PRESETS 数组中的所有配置
                                     PYTHON_BIN 环境变量指定 conda Python 路径
```

每轮产出 `timing.txt`（[TIMING] 行汇总）、`wall.csv`（CSV 格式耗时）和完整终端日志。跨配置汇总见 `cross_wall.csv`。

---

## 11. 修改对照表（三版本）

| 编号 | 功能 | 0724 | 0727 | 0803 |
|------|------|------|------|------|
| 1 | XML 配置 | 新增 MaskPath + MaskDilateFactor | 不变 | 移除 MaskDilateFactor |
| 2 | Debug 目录 | os.makedirs | 不变 | 不变 |
| 3 | 掩码加载 | 读→校验→二值化 | 不变 | 不变 |
| 4 | ori_img 生命周期 | 局部 SR 保留（用于最终融合） | 不变 | 统一释放（不再需要融合步骤） |
| 5 | 掩码 warp | dilation + zero-pad | 不变 | 仅 zero-pad |
| 6 | tile 循环 | 逐 tile if/else 判断 | block_reduce + multi-ctx(2) | +VRAM gate(1) + warmup + lock_m + 多模型 worker |
| 7 | 像素融合 | mask blend (SR×mask + bicubic×(1−mask)) | 不变 | 已移除 |
| — | 全管线计时 | 无 | 无 | 新增 tm dict + 终端耗时占比表 |
| — | GPU warmup | 无 | 无 | 新增 dummy tensor 预分配 |

---

## 12. 局限性与后续方向

- **TRT 固定输入尺寸**：引擎在编译时绑定特定 tile 尺寸。重新导出支持动态 shape 的 TRT engine 可将处理粒度从 tile 级进一步下推到 ROI 裁剪级，彻底消除 SR tile 内非 ROI 像素的浪费。
- **ESPAN 主模型未启用 TensorRT**：当前 ESPAN 推理走 PyTorch 原生路径（`create_model(opt)` +
  `model.test()`），仅 RealESRGAN_TRT 分支加载了 JIT TRT 引擎（第 174 行）。ESPAN 是实际生产
  使用的主模型，导出为 TRT engine 预期可获得 2–4× 的单 tile 推理加速，直接将 `T_SR` 项等比例
  压缩。**本地只需确认 TRT 的大致加速效果即可；正式 TRT 部署由后续 GUI 平台统一完成**，
  不在本管线内单独集成。导出需确认 TRT 版本与部署 GPU 的 CUDA/cuDNN 版本兼容。
- **冷启动模型加载**：极小 ROI（<2%）场景下冷启动为第一瓶颈（占比与量级见 §8.1/§8.2，实测 ~9s）。
  在多图连续处理时，可通过进程级模型缓存复用已加载的实例，避免每张图重新走 `create_model` +
  warmup 流程。短期待评估方案：将模型实例提升为模块级单例，按 `ymlpath` 做 key 缓存。
- **单 tile 推理（batch_size=1，加速预期已实测证伪）**：早期假设"GPU 计算单元利用率受限于单 tile
  像素量、mini-batch 可提升 CUDA core 占用率"，据此预期 tile≤500 场景有额外 20–40% 的 T_SR 加速。
  实测（2026-08-11，tile=500 ESPAN / RTX 3090）：GPU utilization 已 ~100%、显存仅 ~4.5GB，
  `BATCH=4` 无显著加速——GPU 为 compute-bound，扩 batch 只增显存不增速度。该方向不再作为性能抓手；
  提升吞吐应减少算力需求（ESPAN 导 TRT、减少 tile 数）；多 GPU 分发判定为伪需求（见 §12）。
- **推理调用方式**：当前采用进程内本地函数调用（`model.test()`），延迟最低、部署最简单——
  内网单机场景下此为最优选。若后续出现多客户端共享 GPU、多模型版本 A/B 测试或跨语言调用需求，
  可考虑将 SR 推理封装为 HTTP/gRPC 微服务（Flask + PyTorch 或 Triton Inference Server）。
  取舍：服务化引入网络往返延迟（ms 级），换取 GPU 资源池化和模型热更新能力。当前阶段维持
  本地直调，待多用户场景出现时再评估迁移成本。
- **NUM_CTX 自适应**：当前默认单上下文是保守策略。实测（2026-08-11）tile=500 ESPAN 单副本全进程仅
  ~4.5GB，两副本约 ~9GB（早期估计的 16–18GB 偏高；显存远未到瓶颈）。建议将 VRAM 门控改为自动计算
  `free_mb / VRAM_PER_REPLICA` 动态设置最优副本数，而非当前硬编码开关。（注：0803 实测 `NUM_CTX`
  恒为 1——内网机 torch 版本 <2.0 导致多实例并行推理不可用，VRAM 门控为死代码，见 §5.3。上述
  "可手动解锁 / 自动计算"属 torch>=2.0 之后的待办方向。）
- **多 GPU 支持（判定为伪需求，暂缓）**：当前代码硬编码 `CUDA_VISIBLE_DEVICES=0`，仅使用单卡。
  理论上多卡分发可实现近似线性加速，但**办公台式机普遍具备 RTX 3060 级显存，可本地部署本套模型**——
  实际部署目标是有限算力的桌面端单卡，多卡（4×3090 开发服务器）不进入生产方向。优先保证模型在
  桌面端单卡上稳定跑通，而非追求多卡吞吐。
- **resize_scale ≠ 2 的外部图像适配**：`resize_by_sat_rcsc_2` 依赖卫星元数据做地理校正。接入外部普通图像时应保持 `SRScale=2` 走直通路径。
- **硬编码路径 + 模块级 .so 加载风险**：第 20 行 `npct.load_library(..., "ImgHistMatch")`
  在 `import` 阶段执行——如果目标机器（RTX 3060）上该 `.so` 不存在或路径不同，**整个脚本
  import 时直接崩溃**，不会进入 `main()`。当前仅 restormer 路径使用该库（直方图匹配），
  ESPAN 和 RealESRGAN 不受影响。短期方案：将 `load_library` 移入 restormer 分支内部
  延迟加载；或在外层加 `try-except` 并在非 restormer 场景下忽略。第 174 行 TRT 引擎路径
  同样为绝对路径，部署时需确认。
- **单图内动态 tile 划分**：tile 尺寸作为部署期常数，上线后不应频繁变动。但在单张图像内部，使用统一 tile 网格并非最优：远离 ROI 的背景区域可用大 tile（最大化跳过效率），与 ROI 相交的 tile 可递归细分为更小的子 tile，实现更精细的边界贴合。第 5.2 节的 tile 分类预扫描已提供驱动这种层级细分的占用信号；主要挑战在于让 `fill_sr_grid` 适配非均匀 tile 尺寸的输出缓冲。这与"去膨胀"思路天然互补——更细粒度的 ROI tile 天然覆盖边界，无需依赖掩码膨胀。

---

## 附录：常见问题 FAQ

### 终端运行报 `no module named cv2`，但 PyCharm 正常

`sudo` 会清除 conda 环境变量（`PATH`、`CONDA_PREFIX` 等被重置为系统默认值）。运行脚本不需要 root 权限，去掉 `sudo` 即可。也可以显式设置 `PYTHON_BIN` 指向 conda 环境的 Python：

```bash
PYTHON_BIN=/path/to/conda/envs/sr/bin/python ./run_bench.sh config.xml 5 baseline
```

### XML 新增 MaskPath 标签后报 `超出索引` / `mismatched tag`

`util.get_cfg_value` 在 XML 标签不存在时抛出异常（而非返回 None）。使用 try-except 包裹：

```python
try:
    mask_path = util.get_cfg_value(sfsr_config_file, "MaskPath")
except:
    mask_path = None
```

> **已落地（0805）**：该保护已内置到 `code_0805.py` 的 MaskPath 读取处，无需手动加；本条保留作原理说明。

`mismatched tag` 通常是 XML 标签大小写不一致或嵌套错误——检查开闭标签拼写是否严格匹配。

### tile=625 报 `AttributeError: 'SRModel' object has no attribute 'fake_H'`

TRT 引擎要求实际输入尺寸为偶数。625 + 2×50 = 725 是奇数，ESPAN 的 pixel-shuffle 上采样失败（空间维度无法被 2 整除）。改用偶数 tile（500、1250、2500 等）。

### 掩码路径写成了目录而非文件

`MaskPath` 必须指向具体的 `.tif` 文件。如果误配为数据目录路径，GDAL 尝试将目录作为图像打开，报 `'NoneType' object has no attribute 'RasterXSize'`。检查 XML 中 MaskPath 是否以 `.tif` 结尾。

### `run_bench.sh` 报 `Permission denied`

脚本需要执行权限：

```bash
chmod +x run_bench.sh run_all.sh
```

### 文件保存为 `.txt` 后缀而非 `.sh`

部分编辑器（特别是 Windows 端）在保存无扩展名或 `.sh` 文件时可能自动追加 `.txt`。在内网 Linux 机器上用 `mv` 改名即可，不影响脚本内容。
