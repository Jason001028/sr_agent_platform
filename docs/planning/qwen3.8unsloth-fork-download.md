# Qwen3.8-27B Unsloth下载页 社区量化模型清单（独立文档，可直接喂LLM）

## 基础说明

- 基础底座：**Qwen3.8-27B**，原生参数量固定27B。
- 任务标签定义：
  - `Image-Text-to-Text`：多模态图文模型，支持图片输入
  - `Text Generation`：纯文本大模型，仅文本对话
- 标签释义：
  - `UD` = Uncensored：移除原生安全审查限制
  - `MTP` = Multi-Token Prediction：多token并行预测，推理加速
  - `256k`：支持256k超长上下文窗口

## 量化/部署术语说明

| 标识         | 含义                                        | 适用环境                               |
| ------------ | ------------------------------------------- | -------------------------------------- |
| GGUF-IQ4_XS  | GGUF量化，IQ4_XS极致压缩4bit，Smaller精简版 | llama.cpp，CPU/低显存GPU，文件体积最小 |
| GGUF-Q4_K_XL | GGUF K-quant 4bit，XL高保真增强             | llama.cpp，平衡精度与显存              |
| GGUF-Q5_K_XL | GGUF K-quant 5bit，XL高保真                 | llama.cpp，显存充足、追求更高精度      |
| ROCMFPX      | AMD ROCm平台FPX浮点量化                     | AMD显卡ROCm环境                        |
| llamafile    | 单文件打包方案，内置推理引擎                | 全平台，开箱即用，无需额外环境配置     |
| HNPU         | 华为昇腾NPU专用部署版本                     | 昇腾硬件                               |

## 模型清单表

| 模型仓库地址                                | 任务类型           | 量化/部署方案             | 模型特性                              |
| ------------------------------------------- | ------------------ | ------------------------- | ------------------------------------- |
| jrell/Qwen3.8-27B-i1-IQ4_XS-GGUF-Smaller    | -                  | GGUF(IQ4_XS)              | Smaller精简GGUF，低显存优先           |
| jpetrina/Qwen3.8-27B-MTP-IQ4_XS-pure-GGUF   | -                  | GGUF(IQ4_XS)              | MTP多token加速，pure纯净版            |
| rcmorano/Qwen3.8-27B-ROCMFPX                | -                  | ROCMFPX                   | AMD显卡ROCm FPX量化                   |
| chimingw/qwen3.8-27b-ud-q5-k-xl-llamafile   | Image-Text-to-Text | llamafile + GGUF(Q5_K_XL) | UD无审查，llamafile单文件打包，多模态 |
| Kindadodgy/qwen38-256k-on-16gb              | Text Generation    | -                         | 256k超长上下文，16GB显存可加载        |
| mkopec12/Qwen3.8-27B-i1-IQ4_XS-GGUF-Smaller | -                  | GGUF(IQ4_XS)              | Smaller精简GGUF                       |
| meshllm/Qwen3.8-27B-UD-Q4_K_XL-layers       | Text Generation    | GGUF(Q4_K_XL)             | UD无审查，支持分层加载                |
| oolfBER/Qwen3.8-27B-UD-Q4_K_XL-single-GGUF  | -                  | GGUF(Q4_K_XL)             | UD无审查，single单文件版本            |
| mcham/Qwen3.8-27B-padthai-gguf              | -                  | GGUF                      | padthai专项微调变体                   |
| jpetrina/Qwen3.8-27B-IQ4_XS-pure-GGUF       | -                  | GGUF(IQ4_XS)              | pure纯净版IQ4_XS量化                  |
| runanywhere/qwen3_8_27b_HNPU                | Text Generation    | HNPU                      | 华为昇腾NPU部署版本                   |
| gzbx/Qwen3.8-27B-i1-IQ4_XS-GGUF-Smaller     | -                  | GGUF(IQ4_XS)              | Smaller精简GGUF                       |
| aitups/Qwen3.8-27B-saor                     | -                  | -                         | saor专项微调变体                      |

## 本页模型 部署选型指南

1. **llama.cpp（CPU/低显存NVIDIA GPU）**
   - 显存极其紧张：`IQ4_XS` GGUF（Smaller精简版）
   - 精度显存平衡：`Q4_K_XL` GGUF
   - 显存充足，优先精度：`Q5_K_XL` GGUF
2. **AMD显卡**：选用 `ROCMFPX`
3. **华为昇腾硬件**：`HNPU`版本
4. **不想配置推理环境，一键运行**：`llamafile`打包模型
5. **超长上下文需求**：Kindadodgy/qwen38-256k-on-16gb（256k上下文，最低16GB显存）
6. 任务区分：带 `Image-Text-to-Text`支持图文输入；`Text Generation`仅纯文本对话

## 备注

1. 全部模型底座为Qwen3.8-27B，参数量固定27B。
2. UD（Uncensored）属于社区去对齐版本，请自行评估使用风险。
3. GGUF系列仅适配llama.cpp推理框架。
