# P1 原生窗口与 Tile API

P2 可直接复用 `PreviewAsset.id`。先用现有 `POST /api/image-open/assets` 解析盘阵路径或场景，也可以使用已完成上传的资产。Tile 与 JPEG 共用预览路径权限、上传缓存租约和 P0 源修订。前端入口是 `fetchAssetTileInfo` 和 `fetchAssetTile`，都支持 `AbortSignal`。先请求 `/tiles` 可探测当前 API 环境与资产的窗口读取能力。

## API 契约

以下路径相对于 `/api/image-open/assets/{id}`。波段用从 1 开始的 `band` 查询参数，默认第一波段。

| 请求 | 返回 |
| --- | --- |
| `GET /tiles` | JSON，源尺寸、波段数、DN 类型、NoData、Strip/Tiled、压缩、GDAL block 大小、已有 Overview 尺寸、tileSize、maxLevel 和版本。 |
| `GET /tiles/0/39/29` | 8-bit 灰度 PNG。示例覆盖原图 `[19968, 20480) × [14848, 15360)`。 |
| `GET /raw-window?x=20000&y=15000&width=512&height=512` | 按行排列的原生 DN 二进制，小端，无行填充。每轴上限 512；越过右下边界时以实际返回窗口为准。 |

- 像素坐标和 Tile 网格从 0 开始。第 `L` 层步长 `S = 2**L`，原图起点 `(tile_x * 512 * S, tile_y * 512 * S)`。右、下边界采用半开区间裁剪。输出尺寸为实际窗口宽、高分别除以 `S` 向上取整。只有最后一个像素的覆盖范围可能缩小，P2 可直接按原图边界摆放图块。
- L0 对应原生窗口。低 LOD 使用 Nearest，优先复用尺寸符合二次幂网格的现有 Overview，边缘采用该层最后一个采样。没有匹配层时从基础栅格取样，使用各输出像素覆盖范围的中心。已有 Overview 的采样方法由源文件决定。算法侧可使用 `RasterWindowReader.read_window` 另选 Average 或 Bilinear，输出仍保持源 DN 类型。
- `X-SR-Tile` JSON 包含 `id`、原图起点、原图窗口大小 `sourceWidth/sourceHeight`、PNG 实际大小 `width/height`、LOD、网格坐标、波段、重采样、拉伸版本和缓冲大小。完整 UInt16 Tile 的 `dnBytes`、`displayBytes`、`decodedBytes` 分别是 524288、262144、1048576。
- `X-SR-Raster-Window` JSON 包含 `x/y/width/height`、`band`、NumPy `dtype` 字符串（例如 `<u2`、`<i2`、`<f4`）、`noData`、`dnBytes`。`nan` 等非有限 NoData 用字符串表示；二进制保留 DN 原值。
- 两种响应均含 `sourceVersion`（P0 修订）和 `rasterVersion`（额外含实文件和已有旁文件修订）。P2 拼图时以 `rasterVersion` 校验同一代，PNG 可直接 `createImageBitmap(blob)`。浏览器可跨源读取元数据头。PNG 提供 `ETag` 和 `Cache-Control: private, no-cache`，条件请求可返回 304。
- 资产失效沿用 404/410，源修订变化用 409，非 TIFF 用 415，非法窗口、波段、网格和不支持的 TIFF 解码块用 422，缺少可用 GDAL 或队列满用 503。队列满带 `Retry-After: 1`。

## 读取和资源

- GDAL 以只读模式打开 GTiff，每 Worker 最多常驻两个 Dataset。默认 2 个 Worker，`SR_TILE_WORKERS` 可调至 4，另设 32 个不同请求的总上限（含运行中）。同一资产和坐标的并行请求共享任务；最后一个消费者断开时撤销未启动任务。旧 JPEG 使用既有执行资源。
- GDAL Block Cache 上限 128 MiB。对 GDAL 解码路径，单个可能读取的原生块限制为 32 MiB。压缩条带的 I/O 最小单位仍是条带。标准无压缩单波段灰度 Strip 的 Nearest 路径复用 P0 TIFF 条带元数据，按所需行和列读取，适用于大单条带。窗口路径从不调用整幅预览或创建 Overview。上传源句柄在当前请求完成后释放。
- 8-bit 灰度用 P0 的均衡分桶和查表约定，统计来自每源修订、每波段最多 25 个 32×32 原生窗口，忽略 NoData 和非有限值，回写显示缓冲时将这些像素置黑。全部 LOD 共用统计；JPEG 和 PNG 使用不同的采样总体。统计 LRU 最多 128 份，PNG LRU 最多 512 份且压缩数据总量最多 32 MiB。缓存身份包含源、波段、LOD、坐标、重采样和拉伸规则。

## CentOS 7 离线部署

仓库的生产部署记录为 API Python 3.9，另有 SR 专用 Python 3.6 / GDAL 2.4.0。P2.4 将 Tile 的最低 GDAL 版本设为 3.4.1。先在 API 解释器验证 GDAL、NumPy 和 GTiff 可用性。若探针通过，可直接保留该 API 环境。GDAL 缺失时现有 JPEG 路由仍可用。 [P2.4 兼容性](#p24-gdal-341-兼容性) · [现有部署记录](../../deploy/README.md)

```sh
/opt/sr-venv/bin/python -c "import sys,numpy as np; from osgeo import gdal,gdal_array; print(sys.version,np.__version__,gdal.VersionInfo()); assert int(gdal.VersionInfo())>=3090000; ds=gdal.GetDriverByName('MEM').Create('',2,2,1,gdal.GDT_UInt16); assert ds.GetRasterBand(1).ReadAsArray().dtype==np.uint16; assert gdal.GetDriverByName('GTiff')"
```

若探针失败，在可联网的 Linux x86-64 构建机准备独立 API 环境。以下命令将求解目标约束在 glibc 2.17，并生成显式包清单及归档。构建机需有 conda 和 conda-pack。GDAL 3.9.3 有声明兼容 glibc 2.17 的 Python 3.9 构建；完整依赖仍需在目标机执行同一探针和 API 冒烟验证。 [GDAL 3.9.3 包元数据](https://api.anaconda.org/release/conda-forge/gdal/3.9.3) · [Conda 虚拟依赖](https://docs.conda.io/projects/conda/en/stable/user-guide/tasks/manage-virtual.html) · [Conda-Pack](https://conda.github.io/conda-pack/)

```sh
CONDA_OVERRIDE_GLIBC=2.17 conda create -y -p /tmp/sr-api-p1-build --override-channels -c conda-forge --strict-channel-priority 'python=3.9' 'gdal=3.9.3' 'numpy=1.26' 'fastapi>=0.110' 'uvicorn>=0.29' 'pillow>=10' 'tifffile>=2021.7.2' 'pyyaml>=6,<7' 'openai>=1.40,<2' eval-type-backport 'jsonschema>=4.17,<4.18' 'scipy>=1.10,<2'
conda list -p /tmp/sr-api-p1-build --explicit > sr-api-p1-linux-64.lock
conda pack -p /tmp/sr-api-p1-build --dest-prefix /opt/sr-api-p1 -o sr-api-p1-linux-64.tar.gz
sha256sum sr-api-p1-linux-64.tar.gz > sr-api-p1-linux-64.tar.gz.sha256
```

将三份文件带入内网后，在目标机校验并解包到固定路径。

```sh
sha256sum -c sr-api-p1-linux-64.tar.gz.sha256 && sudo mkdir -p /opt/sr-api-p1 && sudo tar -xzf sr-api-p1-linux-64.tar.gz -C /opt/sr-api-p1
```

用 `/opt/sr-api-p1/bin/python` 重跑探针。冒烟通过后用 systemd drop-in 先清空 `ExecStart`，再设为 `/opt/sr-api-p1/bin/python -m uvicorn backend.api.app:create_app --factory --host 127.0.0.1 --port 8000`。沿用现有工作目录、环境变量和服务账户。启动后用真实允许路径解析资产，验证 `/tiles`、边缘 PNG、Raw DN 和 `/preview`。回滚时移除该 drop-in 并重启原 API 服务。SR 的 Python、GDAL 及生产 TIFF 与该部署独立。

## 基准及复验

Windows 本机，Ryzen 7 9700X。两份 49475×49454 UInt16 合成 PAN 均使用可逐像素复算的 DN。Strip 是无压缩 8 行条带（4.89 GB），Tiled 是 512×512 DEFLATE（21.83 MB），均无金字塔。Tiled 压缩率取决于数据纹理。数字来自进程 I/O 计数器，包含必要元数据和操作系统已缓存的读取；Windows 数据未分离物理磁盘 I/O。冷/热分别指新建 Worker 句柄或 PNG LRU 未命中/命中，操作系统页缓存未主动清空。完整记录见 [基准 JSONL](raster-window-p1-benchmark.jsonl)。

| 场景 | Strip P50/P95 ms | Tiled P50/P95 ms |
| --- | ---: | ---: |
| 任意坐标 L0 首次，新 Worker | 89.81 / 89.81 | 4.61 / 4.61 |
| 同一 L0 窗口重复 32 次 | 6.57 / 7.30 | 1.69 / 1.80 |
| L1 | 5.97 / 6.32 | 2.47 / 2.65 |
| L2 | 6.29 / 6.58 | 7.90 / 9.58 |
| L3 | 6.77 / 7.28 | 27.47 / 33.95 |
| L0 随机，2 并发 | 10.14 / 11.81 | 2.62 / 3.23 |
| L0 相邻，2 并发 | 10.72 / 16.14 | 2.31 / 3.33 |
| PNG LRU 冷，32 个不同 L0 Tile | 8.51 / 9.36 | 4.20 / 5.09 |
| PNG LRU 热，同一批 Tile | <0.01 / <0.01 | <0.01 / 0.01 |
| 2 个 PNG Worker 与旧 2048px JPEG 同时工作 | 12.05 / 46.30 | 8.11 / 10.51 |

同规模 Strip 上，首轮纯 GDAL L0 读取约 51 MB，P1 新窗口约 1.05 MB（含首次 TIFF 元数据），随机 32 块从约 1.57 GB 降到 16.90 MB。最终 L0 单窗口 0.5 MiB DN 无整图数组；随机 2 并发 Strip 约 196 块/秒，Tiled 约 743 块/秒。PNG 缓存从 0% 到 100%，热 LRU 记录为 0 读取字节。同一批基准的 4/8 并发在 Strip 上拉长尾延迟，默认选择 2 个 Worker。冷 PNG 与旧 JPEG 并行时同时统计双方的进程 I/O/RSS。大尺寸 Tiled 的 JPEG 仍受 P0 的 Pillow 回退尺寸限制，混跑使用 Strip 作为旧 JPEG 输入。

```sh
python -m scripts.benchmark_tiles /tmp/sr-tile-bench
python -m scripts.benchmark_tiles /tmp/sr-tile-bench --source /path/to/readonly/PAN.tif --jpeg-source /path/to/readonly/PAN.tif --drop-file-cache
python -m pytest -q backend/tests/test_image_tiles.py
python .e2e/serve-image-open-api.py
CHROME_PATH=/path/to/chrome node .e2e/test-image-tiles.js
```

基准工具另依赖 `psutil`，两个合成文件生成需约 5 GB 可用空间。`--source` 使用指定 TIFF 只读复测；若旧 JPEG 无法处理该源，选用可处理的 Strip 作为 `--jpeg-source`。`--drop-file-cache` 在 Linux 上仅对当前源文件调用 `posix_fadvise(DONTNEED)`，首次物理读取量以报告的 `disk_read_bytes` 和盘阵计数为准。

Chrome 158 与 Edge 158 已通过跨域请求、L0 PNG 画布绘制、262144 个 UInt16 DN 比对和旧 JPEG 解码。既有 Viewer 浏览器验收也通过了分段上传、服务端预览切档、映射路径零上传和取消加载。真实盘阵、冷物理 I/O 和 CentOS 7 目标机仍待现场验收。

## P2.4 GDAL 3.4.1 兼容性

| 审计项 | GDAL 3.4.1 | GDAL 3.9+ |
| --- | --- | --- |
| 绑定和原始 DN | 启动时用 MEM 对 UInt16 的 0 和 65535 做 NumPy 写读回环。L0、原始窗口和 P2 Area 的输入按源分辨率读取。 | 使用相同的绑定探针和原生读取。 |
| Python 调用作用域 | 使用线程局部配置 API 并在退出时恢复。通过返回值检查 I/O 失败，保留进程现有异常模式。 | 使用 `gdal.config_options` 和 `gdal.ExceptionMgr`。 |
| 严格 Tile 的 Overview | Nearest 优先选像素尺寸匹配的二倍数层。仍需降采样时，用 `OVERVIEW_LEVEL=NONE` 或 `<index>only` 固定源层，含内部和外置金字塔。 | 显式选择相同的层，再用 GDAL 3.9 新增的 `GDAL_OVERVIEW_OVERSAMPLING_THRESHOLD=0.001` 屏蔽隐式改选。 |
| Nearest、Average、Bilinear、Area | 严格网格保留三种 RasterIO 算法和 P2 的 NoData 感知 Area。任意缩放窗口遇到可能的 Overview 时，Average/Bilinear 显式报错，因为旧版默认使用 1.2 的 Overview 阈值。 | 任意缩放窗口保留 GDAL 自动选层。非 Nearest 默认阈值为 1。 |
| TIFF 数据与 I/O | GTiff `OpenEx`、条带/分块、BigTIFF、DEFLATE、SparseStrips、无压缩分块 Direct I/O 均可用。Scale/Offset 仍为元数据，读取返回原始 DN。旧版以 Byte 暴露有符号 8 位 TIFF，可读原生 DN、Nearest 和 Area，Average/Bilinear 显式要求原生 Int8。 | 保留原生 Int8 与既有读取和缓存策略。 |

实现集中在 [raster_window.py](../../backend/services/raster_window.py)。原始句柄用于枚举 Overview 和读取元数据。3.4.1 的隔离句柄只在实际降采样且可能命中 Overview 时打开，并与原始句柄共用每线程的源版本失效与释放机制。遇到 JPEG 压缩也走此判断，因为 GTiff 可以在 RasterIO 内暴露平时不可见的隐式 Overview。每个源最多保留一个隔离句柄。边缘仍按现有 `grid_step` 拆分，末像素按实际剩余 footprint 采样。Tile 缓存规则继续使用原版本，源文件和 `.ovr`、`.msk` 等边车的版本变化仍触发缓存失效。 [GDAL 3.4.1 OpenEx 源码](https://github.com/OSGeo/gdal/blob/v3.4.1/gdal/gcore/gdaldataset.cpp#L3073) · [RasterIO 阈值说明](https://gdal.org/en/stable/doxygen/classGDALRasterBand.html) · [GDAL 3.4.1 GTiff 源码](https://github.com/OSGeo/gdal/blob/v3.4.1/gdal/frmts/gtiff/geotiff.cpp)

`deploy/requirements-api.txt` 继续通过现有 API 环境提供 NumPy。GDAL 的 Python 扩展和原生库应来自匹配的构建。服务器若已通过软链接和动态链接加载现有 GDAL，部署无需改库路径。SR 子进程会继承服务的 `LD_LIBRARY_PATH`，因此也应避免给整个服务导出一串 Conda 库路径。当前工作环境无法访问生产主机的 `/opt/sr-venv`、`nginx` 用户和 `sr-api` 单元。需在生产仓库根目录验证 nginx 身份加载和运行中服务的实际栅格端点。 [SR 子进程环境](../../backend/services/local_exec.py) · [sr-api 单元](../../deploy/sr-api.service)

```sh
sudo -u nginx -- /opt/sr-venv/bin/python -c "import sys,numpy; from backend.services.raster_window import _gdal; g=_gdal(); assert g.GetDriverByName('GTiff'); print(sys.version,numpy.__version__,g.VersionInfo('RELEASE_NAME'))"
systemctl show sr-api --property=User,MainPID,ActiveState,ExecStart
curl -fsS 'http://127.0.0.1:8000/api/image-open/assets/<asset-id>/tiles'
```

已有资产 ID 可通过本服务正常的图像打开流程获取。对同一资产测 `tiles/0`、`tiles/1`、`tiles/2` 和 `raw-window` 可覆盖 systemd 服务实际读图。以独立审计样本比对时，可用 `python -m scripts.audit_raster <样本目录> --layout deflate --api http://127.0.0.1:8000`。

2026-11-25 在 Linux x86-64（WSL）用 Python 3.9.13、NumPy 1.26.4 和 conda-forge 的 GDAL 3.4.1、3.9.3 各跑 63 项测试，均通过。Windows Python 3.12.14、NumPy 2.5.4、GDAL 3.11.4 同样通过 63 项。用例覆盖 UInt16 DN、NoData、窄范围和常量、奇数边缘、相邻 Tile、原生 Int8 与旧 SignedByte、SparseStrips、JPEG 隐式 Overview、内部和外置 Overview、缓存版本及重复请求。Average/Bilinear 使用固定 DN 参考和无 Overview 的 MEM 参考。 [用例](../../backend/tests/test_image_tiles.py)

```sh
python -m pytest -q backend/tests/test_image_tiles.py backend/tests/test_image_open.py backend/tests/test_preview_plan.py
```

复用四份 49475×49454 UInt16 [BigTIFF 审计样本](bigtiff-performance-audit.md)。GDAL 3.4.1 下通过本地 HTTP 对每份样本核对 1,417,480 个原始 DN、L0/L1/L2 PNG 及 323×302 的右下边缘，全部逐像素一致。审计端点使用固定线性显示窗口，将重采样结果与自动拉伸统计独立验证。纯读基准的 L0/L1/L2 DN 与解析式参考完全一致，GDAL 3.4.1、3.9.3 前后版本的 L7 DN 摘要也一致，L7 读取明确命中 387×387 的 Overview。相同的 GDAL 3.4.1 环境运行旧版 `935d6f6` 时在版本检查处拒绝读取。 [HTTP 审计脚本](../../scripts/audit_raster.py)

纯读取基准使用同机 WSL 的 Windows 挂载路径，对每种布局的同一右下 Tile 测量 5 次，逐次释放 GDAL 源句柄，保留 OS 页缓存。3.9.3 的前后版本各跑两轮并交替先后顺序。表中延迟为中位数，单位 ms，三列中的数值按 L0、L1、L2 排列。

| 布局 | 3.9.3 原 HEAD | 3.9.3 适配后 | 3.4.1 适配后 |
| --- | ---: | ---: | ---: |
| 无压缩 Strip | 59.79 / 61.25 / 38.95 | 59.89 / 60.76 / 39.34 | 59.66 / 61.30 / 38.77 |
| 无压缩 Tiled | 11.66 / 11.85 / 11.75 | 11.86 / 11.57 / 11.63 | 11.68 / 11.42 / 11.28 |
| Tiled DEFLATE | 10.18 / 10.27 / 10.23 | 10.92 / 10.27 / 10.25 | 10.16 / 10.25 / 10.36 |
| DEFLATE 加外置 Overview | 11.81 / 11.97 / 11.91 | 11.96 / 12.10 / 11.92 | 11.97 / 14.39 / 14.30 |

- L7 明确选用外置 Overview 时，三组分别为 12.82、13.05、13.19 ms，逻辑读取量均为 549,520 B。3.4.1 的同一外置 Overview 样本需对 L1/L2 固定基底层，重开句柄时逻辑读取从 40,869 B 增至 49,061 B。其他布局的单次逻辑读取相同，分别约为 Strip 155–302 kB、Tiled 552 kB、DEFLATE 33 kB。
- 各布局的 L0 阶段 RSS 增量约 2.1–3.6 MiB，后续阶段额外增量为 0–384 KiB。这些数值为进程阶段采样，包含图块渲染与缓存，未测瞬态峰值。
- 各场景紧接的第二次 PNG 请求均命中应用 LRU，耗时 0.007–0.026 ms。WSL 物理读取计数为 0，真实盘阵和空页缓存性能仍待实测。
