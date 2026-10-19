# Qwen3.8-27B 社区量化模型汇总（LLM读取专用 + 部署方案选型）

> ⚠️ **选型修订（2026-09-09）**：下表 §三 的「部署选型指南」是一般性指引，落到本项目
> **4× RTX 3090（Ampere）+ CentOS7** 需要修正——NVFP4（Blackwell/RTX50 专属）、FP8（无 Ampere 加速）、
> EXL3（不支持 `qwen35` 混合架构）、MLX（Apple 专属）均不可用；AWQ 虽可跑但要 vLLM，而 vLLM 装不上 CentOS7。
> **本机唯一可行且够用的组合 = GGUF Q4_K_M + llama.cpp**；且应选基础版（原生 function calling），
> 不选本表里的 Uncensored/Abliterated 去对齐变体。对照与名词科普见
> [docs/knowledge/llm-serving-gguf-glossary.md](../knowledge/llm-serving-gguf-glossary.md) §4；定案见
> [agent-llm-deployment.md](../planning/agent-llm-deployment.md)。

## 基础说明

- 基础模型：Qwen3.8-27B，原生参数量固定为27B。表格内出现的2B/6B/8B/15B/20B等数字为模型文件体积，**不是模型参数量**。
- 任务分类定义：
  - `Image-Text-to-Text`：多模态图文模型，支持图像输入
  - `Text Generation`：纯文本大语言模型，仅文本输入输出
- 微调标签释义：
  - `Uncensored`：移除模型原生安全审查限制
  - `Abliterated / OBLITERATED`：对齐擦除，深度去除安全对齐约束

## 一、量化格式总览（部署方案对照表）

| 量化格式 | 适用硬件&推理框架        | 核心特点                                                                |
| -------- | ------------------------ | ----------------------------------------------------------------------- |
| GGUF     | CPU / 通用GPU，llama.cpp | 跨平台，本地部署首选；衍生GSQ-RCO、Flash、DFlash2、Ridge等优化子变体    |
| NVFP4    | NVIDIA RTX40/50系显卡    | NVIDIA FP4量化，显存占用极低；有RTX5090、NInfer、QUASAR-QAT专项优化版本 |
| FP8      | NVIDIA高显存GPU          | FP8浮点量化，推理速度快                                                 |
| EXL3     | NVIDIA显卡，EXLlamaV3    | 可自定义权重比特，示例：3.5bpw                                          |
| MLX      | Apple Silicon(M系列芯片) | 苹果MLX框架专用量化                                                     |
| AWQ-INT4 | NVIDIA GPU               | AWQ算法4bit整数量化                                                     |
| MLX 4bit | Apple Silicon            | MLX社区通用4bit量化                                                     |

## 二、完整模型清单

| 模型仓库                                             | 任务类型           | 量化方案      | 模型特性                            |
| ---------------------------------------------------- | ------------------ | ------------- | ----------------------------------- |
| ISTA-DasLab/Qwen3.8-27B-GSQ-RCO-GGUF                 | Image-Text-to-Text | GGUF(GSQ-RCO) | GSQ-RCO特殊GGUF量化，多模态         |
| Jackrong/Qwopus3.8-27B-Flash-GGUF                    | Image-Text-to-Text | GGUF(Flash)   | Flash加速GGUF，多模态               |
| orcarouter/Qwen3.8-27B-Uncensored-GGUF               | Image-Text-to-Text | GGUF          | Uncensored无审查，多模态            |
| OBLITERATUS/Qwen3.8-27B-OBLITERATED                  | Text Generation    | -             | OBLITERATED对齐擦除微调，纯文本     |
| orcarouter/Qwen3.8-27B-Uncensored-FP8                | Image-Text-to-Text | FP8           | Uncensored无审查，多模态            |
| orcarouter/Qwen3.8-27B-Uncensored-MLX                | Image-Text-to-Text | MLX           | Apple Silicon专用，无审查，多模态   |
| unsloth/Qwen3.8-27B-NVFP4                            | -                  | NVFP4         | Unsloth出品NVFP4量化                |
| peculiar-ragdoll/Dirk-Qwen3.8-27B-GGUF               | Image-Text-to-Text | GGUF          | Dirk指令微调，多模态                |
| Mia-AiLab/Qwen3.8-27B-EXL3-3.5bpw                    | Text Generation    | EXL3(3.5bpw)  | EXLlamaV3，3.5bit/权重，纯文本      |
| Qwen/Qwen3.8-27B-FP8                                 | -                  | FP8           | 官方FP8量化版本                     |
| GestaltLabs/Qwen3.8-27B-EXL3-11.5GB                  | Image-Text-to-Text | EXL3          | EXLlamaV3打包版，多模态             |
| z-lab/Qwen3.8-27B-DFlash2-GGUF                       | Text Generation    | GGUF(DFlash2) | DFlash2推理优化GGUF，纯文本         |
| gittensor-model-hub/Qwen3.8-27B-NVFP4-RTX5090        | Image-Text-to-Text | NVFP4         | RTX5090显卡专项适配，多模态         |
| cyankiwi/Qwen3.8-27B-AWQ-INT4                        | Image-Text-to-Text | AWQ INT4      | AWQ 4bit整数量化，多模态            |
| unsloth/Qwen3.8-27B-GGUF                             | -                  | GGUF          | Unsloth GGUF量化                    |
| HauhauCS/Qwen3.8-27B-Uncensored-HauhauCS-Aggressive  | Image-Text-to-Text | -             | Aggressive激进无审查微调，多模态    |
| JonathanColetti/Qwen3.8-27B-Uncensored-GGUF          | Text Generation    | GGUF          | 纯文本无审查GGUF                    |
| huihui-ai/Huihui-Qwen3.8-27B-abliterated-GGUF        | Image-Text-to-Text | GGUF          | Abliterated对齐擦除，多模态         |
| 0bserverx/Qwen3.8-27B-Heretic-Abliterated-Uncensored | Text Generation    | -             | Heretic+擦除+无审查复合微调，纯文本 |
| QUASAR-QAT/Qwen3.8-27B-QUASAR-NVFP4                  | Image-Text-to-Text | NVFP4         | QUASAR-QAT优化NVFP4，多模态         |
| nvidia/Qwen3.8-27B-NVFP4                             | Text Generation    | NVFP4         | NVIDIA官方NVFP4量化，纯文本         |
| orcarouter/Qwen3.8-27B-Uncensored-NVFP4              | Image-Text-to-Text | NVFP4         | Uncensored无审查NVFP4，多模态       |
| turboderp/Qwen3.8-27B-exl3                           | Text Generation    | EXL3          | EXLlamaV3量化纯文本                 |
| empero-ai/Qwen3.8-27B-Ridge-GGUF                     | Image-Text-to-Text | GGUF          | Ridge指令微调GGUF，多模态           |
| neroued/Qwen3.8-27B-nvfp4-NInfer                     | Image-Text-to-Text | NVFP4         | NInfer推理引擎优化NVFP4，多模态     |
| EschaLabs/Qwen3.8-27B-Escha-W2                       | Text Generation    | -             | Escha-W2微调变体，纯文本            |
| RadixArk/Qwen3.8-27B-NVFP4                           | Image-Text-to-Text | NVFP4         | RadixArk NVFP4量化，多模态          |
| mlx-community/Qwen3.8-27B-4bit                       | Image-Text-to-Text | MLX 4bit      | MLX社区4bit，Apple Silicon，多模态  |

## 三、部署选型指南

> 用于快速决策模型与量化方案

1. **CPU / 低显存GPU，llama.cpp本地部署**：优先 GGUF；Flash / DFlash2 子变体推理速度更好。
2. **NVIDIA显卡部署**
   - RTX40/50新显卡：首选 NVFP4，显存占用最低；高显存机器可选 FP8；老显卡选择 EXL3 / AWQ-INT4；RTX5090 可选用专门适配的NVFP4版本。
3. **Apple M系列芯片**：MLX系列量化模型（orcarouter Uncensored-MLX、mlx-community 4bit）。
4. **任务区分**
   - 图文多模态场景：选择标记为 `Image-Text-to-Text` 的模型
   - 纯文本对话场景：选择标记为 `Text Generation` 的模型

## 四、备注（喂给LLM时的重要提示）

1. 所有模型底座都是 Qwen3.8-27B，参数量固定27B。文件名中标注的2B/6B等是文件大小，不是参数量。
2. Uncensored、Abliterated类模型属于社区去对齐版本，需要自行评估使用风险。
3. GGUF是llama.cpp专用；NVFP4仅支持NVIDIA新架构显卡；MLX只能在Apple Silicon运行。
