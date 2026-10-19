# Qwen3.8-27B UD GGUF 文件清单（unsloth/Qwen3.8-27B-GGUF · 独立文档，可直接喂LLM）

> ⚠️ **已核实修订（2026-09-09）**：
> - **真仓库 = `unsloth/Qwen3.8-27B-GGUF`**；原标题/下文里的 `danielhanchen/imatrix` 是抓取时的错误归属（danielhanchen = Unsloth 创始人在 HN/社区的账号）。
> - **`UD` = Unsloth Dynamic 3.0 量化**（纯 PTQ + 刷新版 imatrix 校准 + 逐层动态选量化档），**不是 Uncensored**。本清单是官方**基础版**（原生 function calling），可作 agent 底座。
> - **24GB 单卡 4bit 首选 = `UD-Q4_K_XL`**（unsloth 官方推荐口径）；`mmproj-F16/BF16` 两个视觉投影都在本仓内。
> - 「下载/带到公司选哪几个」见文末 **§下载与带到公司决策**；系统名词对照见 `docs/knowledge/llm-serving-gguf-glossary.md`。

## 基础说明

- 模型底座：Qwen3.8-27B，原生参数量27B
- 仓库：`unsloth/Qwen3.8-27B-GGUF`（Unsloth 官方；基于 imatrix/Unsloth Dynamic 3.0 量化，llama.cpp 专用 GGUF）
- 标签释义
  - `UD` = **Unsloth Dynamic 3.0** 量化（逐层动态选档 + imatrix 校准）——**不是 Uncensored**
  - `imatrix`：importance matrix 重要性矩阵校准，llama.cpp 高精度量化方案
  - `MTP`：Multi-Token Prediction，多token并行预测推理加速（单独文件）
  - `mmproj`：多模态图像投影权重文件，图文任务必须搭配使用
  - BF16：BF16原始精度基线

## GGUF量化后缀含义

| 标识                                                     | 含义                                                         |
| -------------------------------------------------------- | ------------------------------------------------------------ |
| IQ1_M / IQ1_S / IQ2_S / IQ2_XS / IQ3_S / IQ3_XS / IQ4_XS | imatrix IQ系列极致低比特量化，XS=最小体积，S=small，M=medium |
| Q2_K_XL ~ Q8_K_XL                                        | K-quant量化，XL=高保真增强版本；L/M/S代表不同精度/体积档位   |
| Q4_0 / Q4_1 / Q8_0                                       | 传统基础GGUF量化等级                                         |

## 文件清单表

| 文件名                      | 文件大小 | 备注                                       |
| --------------------------- | -------- | ------------------------------------------ |
| BF16                        | -        | BF16基线版本标记                           |
| MTP                         | -        | MTP多token预测优化标记                     |
| .glattributes               | 4.18 kB  | 仓库属性文件                               |
| Qwen3.8-27B-Q4_0.gguf       | 16.1 GB  | 基础Q4_0 GGUF                              |
| Qwen3.8-27B-Q4_1.gguf       | 17.5 GB  | 基础Q4_1 GGUF                              |
| Qwen3.8-27B-Q8_0.gguf       | 29 GB    | 基础Q8_0 GGUF，高精度                      |
| Qwen3.8-27B-UD-IQ1_M.gguf   | 6.73 GB  | UD量化，IQ1_M imatrix量化                |
| Qwen3.8-27B-UD-IQ1_S.gguf   | 6.19 GB  | UD量化，IQ1_S imatrix量化，更小体积      |
| Qwen3.8-27B-UD-IQ2_S.gguf   | 8.37 GB  | UD量化，IQ2_S imatrix量化                |
| Qwen3.8-27B-UD-IQ2_XS.gguf  | 7.27 GB  | UD量化，IQ2_XS极致压缩                   |
| Qwen3.8-27B-UD-IQ3_S.gguf   | 12 GB    | UD量化，IQ3_S imatrix量化                |
| Qwen3.8-27B-UD-IQ3_XS.gguf  | 10.9 GB  | UD量化，IQ3_XS压缩版                     |
| Qwen3.8-27B-UD-IQ4_XS.gguf  | 14.3 GB  | UD量化，IQ4_XS，低显存常用优选           |
| Qwen3.8-27B-UD-Q2_K_XL.gguf | 9.83 GB  | UD量化，Q2_K_XL                          |
| Qwen3.8-27B-UD-Q3_K_XL.gguf | 13.1 GB  | UD量化，Q3_K_XL                          |
| Qwen3.8-27B-UD-Q4_K_M.gguf  | 16.5 GB  | UD量化，Q4_K_M（平衡精度显存，热门选型） |
| Qwen3.8-27B-UD-Q4_K_S.gguf  | 15.4 GB  | UD量化，Q4_K_S，比Q4_K_M更小             |
| Qwen3.8-27B-UD-Q4_K_XL.gguf | 17.6 GB  | UD量化，Q4_K_XL高保真增强                |
| Qwen3.8-27B-UD-Q5_K_M.gguf  | 19.8 GB  | UD量化，Q5_K_M                           |
| Qwen3.8-27B-UD-Q5_K_S.gguf  | 18.7 GB  | UD量化，Q5_K_S                           |
| Qwen3.8-27B-UD-Q5_K_XL.gguf | 20.9 GB  | UD量化，Q5_K_XL高保真增强                |
| Qwen3.8-27B-UD-Q6_K.gguf    | 22 GB    | UD量化，Q6_K                             |
| Qwen3.8-27B-UD-Q6_K_L.gguf  | 24.2 GB  | UD量化，Q6_K_L                           |
| Qwen3.8-27B-UD-Q6_K_M.gguf  | 23.1 GB  | UD量化，Q6_K_M                           |
| Qwen3.8-27B-UD-Q6_K_XL.gguf | 25.3 GB  | UD量化，Q6_K_XL高保真增强                |
| Qwen3.8-27B-UD-Q8_K_L.gguf  | 28 GB    | UD量化，Q8_K_L                           |
| Qwen3.8-27B-UD-Q8_K_XL.gguf | 31.5 GB  | UD量化，Q8_K_XL最高精度GGUF              |
| README.md                   | 7.46 kB  | 仓库说明文档                               |
| config.json                 | 3.76 kB  | 模型配置文件                               |
| imatrix_unsloth.gguf        | 13.6 MB  | imatrix量化权重文件                        |
| mmproj-BF16.gguf            | 931 MB   | 多模态图像投影，BF16精度                   |
| mmproj-F16.gguf             | 928 MB   | 多模态图像投影，F16精度                    |

## 本仓库模型部署选型指南（llama.cpp）

1. **显存极度受限（<12GB）**：优先 IQ1_S / IQ2_XS 系列，文件体积最小，牺牲较多精度换取可加载
2. **低显存平衡方案（12~16GB）**：IQ4_XS，Q4_K_S
3. **通用推荐（16~20GB，精度显存均衡）**：Q4_K_M，最常用主力选型
4. **显存充足，追求更高质量**：Q5_K_M / Q5_K_XL
5. **高显存，近乎原生精度**：Q6_K / Q8_K_XL
6. **图文多模态场景**：必须搭配 `mmproj-*.gguf` 图像投影文件一起使用；纯文本推理不需要mmproj。

## 下载与带到公司决策（2026-09-09 已核实）

> 目标环境：4× RTX 3090（24GB/卡）内网 CentOS7，固定 1 张卡给 Agent LLM（llama.cpp llama-server），
> 剩余给 Slurm/SR。Qwen3.8 为 tied embeddings → llama.cpp 必须把 embedding 也载入显存，
> **文件体积 ≈ 实际显存占用**，选档别贴 24GB 上限太近。

| 档 | 文件 | 体积 | 理由 |
|---|---|---|---|
| 必带 | `Qwen3.8-27B-UD-Q4_K_XL.gguf` | ~17.6GB | **24GB 卡 4bit 首选**（内部按层混 Q5_K/Q8_0/IQ4_XS/Q6_K 等，质量优于普通 Q4_K_M） |
| 必带 | `mmproj-F16.gguf` | ~0.93GB | 视觉投影；F16/BF16 二选一即可，互通 |
| 保险 | + `Qwen3.8-27B-UD-Q4_K_M.gguf` | +16.5GB | `--parallel 4`+长上下文爆显存时的回落档 |
| 质量 | + `Qwen3.8-27B-UD-Q5_K_S.gguf` | +18.7GB | 更高精度 A/B（余量更小，并行/上下文需降档） |

- 必带 ≈ 18.5GB；+保险 ≈ 35GB；全带 ≈ 56GB。机械盘必须 **NTFS/exFAT**（FAT32 4GB 上限装不下 17GB 单文件）。
- **不带**：`Q8_0`(29G)、`Q6_K_L/M/X`(24~31G)、`Q8_K_XL`(31.5G) → 超/顶满 24GB；`Q4_0/Q4_1` → 同体积不如 K 系；`IQ1~IQ3` → 过度压缩伤 agent 推理；`imatrix_unsloth.gguf`(13.6MB) → 仅量化用，运行时无用；MTP 头 → llama.cpp 对 qwen35 投机解码未确认，留作后续优化项。
- 下载（只拉单文件，勿 `git clone` 全仓 ≈ 21 档数百 GB）：
  `hf download unsloth/Qwen3.8-27B-GGUF Qwen3.8-27B-UD-Q4_K_XL.gguf mmproj-F16.gguf … --local-dir /d/models/Qwen3.8-27B`
  （先开 [tree 页](https://huggingface.co/unsloth/Qwen3.8-27B-GGUF/tree/main) 核对文件名；HF 连不通则 `export HF_ENDPOINT=https://hf-mirror.com`。）

## 备注

1. 全部模型底座为 Qwen3.8-27B 官方基础版；`UD` = Unsloth Dynamic 3.0 量化，**与"去安全对齐"无关**（社区确有用同款 UD 量化去对齐的另类变体，但不在本仓）。
2. 全部文件为GGUF格式，**仅适配llama.cpp推理框架**。
3. imatrix量化相比传统GGUF，同等比特下文字生成质量更好。
