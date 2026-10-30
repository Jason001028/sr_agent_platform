# TIFF Overview 本地适配（2026-10-30）

实现入口：`backend/services/preview_jpg.py`。新增回归测试
`backend/tests/test_preview_overview.py` 和可重跑基准
`backend/tests/benchmark_preview_overview.py`。原始测量见
[preview-overview-benchmark.json](preview-overview-benchmark.json)。未修改生产依赖、源影像或部署配置。

## 审计与策略

现有 `ensure_preview_jpg → build_preview_pixels → sample_strips` 无 Overview 优化。
单波段及多波段条带取首波段、端点对齐最近邻采样；1024-bin CDF 均衡，
WhiteIsZero 反转，常量 0→0、非零→128。NaN/Inf 不参与统计，有限值 NoData
原先没有特殊排除，新路径也不引入新规则。原 Pillow 小图兜底为 convert L +
LANCZOS，不与条带均衡完全等价；此次保持原行为。JPEG 灰度、quality=85，临时文件原子替换。

调用链还包括 app.py 的急生成、NOSR、拖入与临时预览分支，均共享像素生成函数，
因此无需修改 API。原文件中用户已有修改未触碰。

仅访问指定 TIFF 内的 IFD 元数据，不扫目录。遍历 next IFD 与 tag 330 SubIFD，
Classic TIFF（含 IFD 类型13）、BigTIFF（含 IFD8）均支持，循环检测且最多检查64个目录。
必须标记 NewSubfileType reduced-image，排除 mask；尺寸小于原图、两边均不小于目标，
宽高比例容忍一像素舍入，波段数、sample format、photometric、orientation 与原图一致。
按面积从小到大试读，保持源图推导的精确目标宽高，禁止放大。
优先复用无压缩条带采样，候选读取失败尝试下一层，最终回到原路径。
普通多页不猜成 Overview；压缩、tiled 和未标记的层保守回退。

本地 Pillow/NumPy/tifffile/psutil 可用，GDAL/Rasterio 不存在。
tifffile 仅用于测试造图，psutil 仅用于基准，生产不引入它们。
由于 Overview 原有重采样会改变像素，规则版本从 v3 升为 v4，旧缓存按原有签名契约
惰性失效一次；文件名、div、quality、mtime 和删除时前缀识别契约不变。

## 本地性能与差异

运行 `python -m backend.tests.benchmark_preview_overview`。
4096×4096 uint16 合成阶梯渐变，Overview 为 nearest 抽取的 ÷2/4/8/16，
每个方案独立子进程，同一个 TIFF；计时包括读头、采样、均衡、JPEG 编码与写盘。
峰值为 Windows peak working set，包含解释器。每项一次，存在噪声，不构成稳定性能结论。

| 有 Overview 档位 | 原路径/快速路径 JPEG未命中 ms | 原/快峰值 MiB | JPEG像素 MAE / 最大差 |
|---|---:|---:|---:|
| 2 | 526 / 598 | 211.4 / 211.1 | 0 / 0 |
| 4 | 192 / 231 | 85.2 / 85.1 | 0 / 0 |
| 8 | 140 / 98 | 53.1 / 53.5 | 0 / 0 |
| 16 | 79 / 98 | 45.1 / 45.2 | 0.159 / 2 |
| 32 | 60 / 73 | 44.3 / 43.8 | 0.347 / 3 |

无 Overview 五档旧/新：626/602、212/225、114/128、83/95、61/70 ms，
所有像素完全相同。JPEG 热缓存命中约5–22ms，两路径都不读取像素。
这里的“冷”仅指 JPEG 缓存缺失，**不是 OS 文件缓存冷读**；未执行系统清缓存，
无法量化真实盘阵磁盘冷读延迟。Overview 减少读行字节量，但输出数组、均衡与编码
成本不变，小图页缓存命中下未实现稳定显著提速，不能宣称生产性能目标已经达成。

视觉差异用解码后 JPEG MAE/RMSE/max 量化（完整数值见JSON）；小差异不代表所有
遥感纹理均如此。已有平均/高斯重采样层可能改变纹理、NoData邻域和均衡直方图；
没有真实影像人工视觉验收，不承诺位级一致。

## 验证与限制

专项覆盖两种 IFD 拓扑、两种 TIFF 容器、五档精确尺寸、最小合适层、
普通多页排除、无 Overview、压缩层回退、异常候选读取回退、优化器异常回退、
WhiteIsZero 常量、缓存命中及版本失效。并运行既有预览、缓存、清理、批量缩略图测试。

压缩/tiled Overview 暂不能利用（缺少现成生产解码依赖）；原图本身若不支持且超过
Pillow 安全上限，仍按原逻辑报错。未主动生成金字塔或更改原 TIFF。
元数据损坏、缺失 reduced 标记、比例异常将保守忽略；检查目录数有上限。
真实遥感影像仅在内网，压缩金字塔、复杂 NoData、首波段真实纹理与冷盘阵收益仍待实机验证。
