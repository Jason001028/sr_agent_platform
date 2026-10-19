# 移植 Windows 本地踩过的坑

> 日期：2026-08-21（由《移植windows本地踩过的坑》与《sr_windows_env_tryout_log》合并建档）· 状态：草稿
> 归档定位：docs/sr_code/ 类目（SR_CODE 生产管线文档簇）——内网 Windows 移植 SR_CODE 的**环境基线（含环境总结）+ 踩坑记录**，与算法全览 [sr-pipeline-overview.md](sr-pipeline-overview.md)、调用契约 [sr-pipeline-interface.md](sr-pipeline-interface.md) 互补。

> 背景：内网 Win10 机（RTX 3060 单卡）尝试把 Linux/CUDA 超分管线跑起来。
> 核心结论：真管线的"最后一公里"全是 Linux 专属（ELF .so、CUDA torch、4 卡假设），Windows 本地定位为平台侧替身。下列坑按推进顺序排。
> 说明：本文档由《移植windows本地踩过的坑》与《sr_windows_env_tryout_log》合并而来（2026-08-21）；后者记录的探索过程与排除证据归档在文末附录。

---

## 〇、机器环境基线（重跑前先确认）

| 项 | 值 |
|---|---|
| 操作系统 | Windows 10 Home China 19045 |
| GPU | NVIDIA GeForce RTX 3060（12GB） |
| 驱动 | 572.70（≥471.41，WSL2 CUDA 透传就绪） |
| CUDA | 12.8 |
| 生产环境 | py310pan（`D:\ProgramData\anaconda3\envs\py310pan`，预装 gdal ✅） |
| 内网 pip 镜像 | `http://nexus.jl1.cn/repository/cgwx-pypi/simple`（需 `--trusted-host nexus.jl1.cn`） |
| 权限 | 管理员 ✅ |

> 终端里裸 `python` 是 WindowsApps 假别名（`where.exe python` 可查），真实 Python 用 `py` 启动器。

---

## 一、装环境阶段

**1. pip 读 requirements 报 GBK 解码错**
- 现象：`UnicodeDecodeError: 'gbk' codec can't decode`
- 原因：Windows 控制台默认 GBK，requirements.txt 含非 ASCII 字符
- 解法：`python -X utf8 -m pip install -r req.txt`（`-X utf8` 在 `python` 后、`-m` 前）；根治是 requirements 全 ASCII

**2. torch 装最新版 → WinError 1114**
- 现象：`[WinError 1114] DLL 初始化例程失败`
- 原因：torch 2.13.x 的 c10.dll 自身损坏/不兼容
- 解法：降级 `torch==2.4.1` + `torchvision==0.19.1`
- 定位手段：`probe_torch_dlls.py` 逐个 `ctypes.WinDLL` 加载 torch/lib 下 DLL，锁定 c10.dll

**3. torch 1.9.1+cu111 装不上**
- 原因：内网镜像 cgwx-pypi 只代理 PyPI，PyPI 无 +cu111 后缀 wheel
- 解法：放弃，用镜像能装到的版本（CPU 2.4.1）

**4. GDAL pip 装不了**
- 现象：`could not build wheel for gdal`
- 原因：镜像只有 sdist 没 wheel；源码编译缺系统 libgdal → Windows 必失败
- 验证：`pip install --only-binary :all: GDAL` → "from versions:none" 证实无 wheel
- 解法：**换 conda 环境 py310pan**（预装 gdal ✅）
- 已排除：桌面 GDAL 2.0.1 裸 DLL（无 py3.11 绑定）、copy Linux gdal 包（ELF 不兼容）——详见附录 A

**5. conda 命令"失效"**
- 原因①：真实 conda 在 `D:\ProgramData\anaconda3`，不是 `D:\Anaconda`（后者是假壳）
- 原因②：**conda 没配内网镜像**，默认指向 repo.anaconda.com 外网 → 内网访问不了
- 解法：配频道
  ```
  conda config --add channels http://nexus.jl1.cn/repository/cgwx-anaconda
  conda config --remove channels defaults
  ```
  或绕开 conda 直接环境内 pip 装（pip 镜像源是全局配好的）：
  ```
  D:\ProgramData\anaconda3\envs\py310pan\python.exe -m pip install "numpy==1.26.4"
  ```
- 坑中坑：cgwx-anaconda 只代理 defaults 频道（无 gdal，gdal 在 conda-forge）；numpy 在 defaults 里有，够用

**6. 安装通道规范：用环境内 pip，别用 conda install**
- 现状：公司内网 conda 镜像 `cgwx-anaconda` 只代理了 defaults 频道，覆盖不完整（缺 gdal 等 conda-forge 包）；而 pip 镜像 `cgwx-pypi` 是全局配好、走 PyPI 代理，覆盖面更全
- 原则：conda 环境（如 py310pan）里装包**优先用该环境自己的 python.exe 调 pip**，而不是 `conda install`
  ```
  D:\ProgramData\anaconda3\envs\py310pan\python.exe -m pip install <包>
  ```
- 原因：① conda install 在内网可能解析不到包 / 频道不全；② pip 源已就绪，一条命令直达；③ 避免 conda 与 pip 混装引起依赖冲突
- 例外：需要 conda 才能正确处理的底层包（如协调 GDAL/torch 重建）再考虑 conda；日常第三方库一律 pip

**7. py310pan 缺 cv2**
- 解法：环境内 pip 装 `opencv-python`

## 二、跑 import / 真管线阶段

**8. numpy 2.2.6 ABI 警告**
- 现象：`Failed to initialize NumPy` + "compiled using NumPy 1.x cannot be run in NumPy 2.2.6"
- 原因：py310pan 的 torchvision 按 numpy 1.x 编译，环境里却是 numpy 2.x
- 解法：降到 `numpy==1.26.4`（1.x 最后一版）

**9. ImgHistMatch .so 加载失败**
- 现象：`npct.load_library(.../ImgHistMatch, ".")` → `OSError: no file with expected extension`
- 原因：Linux ELF .so；Windows LoadLibrary 只认 PE（.dll）；numpy.ctypeslib 对无扩展名文件自动加 `.dll` 找不到；改名 .dll 也只是 WinError 193
- 解法：加载处包 `try/except` → `lib = None`；该库只在 **restormer 分支**被调用，espan 路径用不到，绕过后不触发（对应 code_0817_prod.py 第 26 行 + 调用处守卫）

**10. GPU 健康检查硬编码 4 卡**
- 现象：`GPU Error, gpu_available=False GPU COUNT = 1` + `exit(3)`
- 原因：`if gpu_available is False or gpu_count != 4:` 要求正好 4 卡，本地单卡必挂
- 解法：改成 `gpu_count < 1`
- 坑中坑：同段 `systemctl stop slurmd.service` 是 Linux 命令，Windows 没有 → 注释掉

**11. 隐藏雷：CUDA_VISIBLE_DEVICES = "1"**
- 现象：torch 明明有卡，`torch.cuda.is_available()` 却是 False
- 原因：服务器用 GPU1 硬编码 `"1"`；单卡机只有 GPU0，映射到不存在的设备 → is_available() 变 False
- 解法：改成 `"0"`

**12. /DiskArray/... 硬编码路径要换成 W:/...**
- 现象：读盘阵文件时报文件不存在 / 路径错误
- 原因：Linux 代码写死绝对路径 `/DiskArray/ProductionSchedule/...`；Windows 盘阵挂载到 W: 盘
- 解法：全部替换 `/DiskArray/` → `W:/`（**注意去掉开头的斜线**，即 `W:/ProductionSchedule/...`；盘阵是网络盘，Windows 可访问）

## 三、平台级大判断

**13. Linux 打包环境（tar.gz）移植死路**
- 原因：`.so` / `bin/python` 全是 Linux ELF（文件头 `7F 45 4C 46`），Windows LoadLibrary 只认 PE（`4D 5A`）；错误 193 = 系统级不兼容，改配置无效

**14. Docker/WSL2 是唯一能让 .so 活的本地路线**
- Win10 Home 只能用 WSL2 后端（无 Hyper-V）；WSL 两个功能当前 Disabled，需管理员开启
- RTX 3060 + 驱动 572.70（≥471.41）→ WSL2 CUDA 透传就绪
- 待办：
  ```
  dism /online /enable-feature /featurename:Microsoft-Windows-Subsystem-Linux /all /norestart
  dism /online /enable-feature /featurename:VirtualMachinePlatform /all /norestart
  ```

---

## 常识

**超分测试要从 `_NOSR` 后缀里选图**：`_NOSR` 后缀文件是超分前的原始输入图像（LR 原图）。测试时若从成图里选（即不带 `_NOSR` 后缀、已被超分过的结果图）当输入，会被**再次超分**，分辨率长宽各再增加一倍。选输入图时认准 `*_NOSR.tif`。

**每年 8 月中下旬卫星影像效果最差（老同事经验）**：起因是**热点效应（hot-spot / opposition effect）**——太阳同步轨道光学卫星过境本地时固定（约 10:30），年周期内不同日期太阳-传感器相对方位角变化；每年约 8 月 20 日前后，观测方向与太阳照射方向趋于重合（后向散射几何，阴影被目标自身遮挡），地物反射率异常抬升 → 影像偏亮/过曝、纹理反差损失，超分与判读质量同步下降。注意该效应是否触发与目标纬度/太阳赤纬/过境时刻组合有关，并非所有区域同日同刻都最差；年周期成立，计划性测试/拍摄可避开此窗口。

---

## 附录 A：已排除路线及证据（为什么不能走）

**tar.gz Linux 打包环境（torch1.9.1py36.tar.gz）——死路**
- 命令级证据：`tar -tzf` 是列清单不是解压（`-C` 报错）；`tar -xzf` 解压成功但大量 `Can't Create XXX.so.0`
- 选 Linux 环境的 `bin/python` 直接报 `CreateProcess error＝193,81 不是有效的 win32 应用程序`
- 根因：`.so`/`bin/python` 全为 Linux ELF（文件头 `7F 45 4C 46`），Windows 的 LoadLibrary 只认 PE（`.dll`，文件头 `4D 5A`）；错误 193 = ERROR_BAD_EXE_FORMAT，系统级不兼容，与配置无关
- 附带：符号链接权限问题（需开发者模式）、0x80070422（Windows Update 服务被禁用）

**GDAL pip 安装——死路**
- 证据：`pip install --only-binary :all: GDAL` → `from versions:none`，镜像无任何 wheel；源码编译需系统 libgdal → Windows 必失败
- 已排除的全部路线：conda（本机 conda 曾不可用）、桌面 GDAL 2.0.1 裸 DLL（仅 C 库 gdal201.dll/geos.dll/proj.dll，无 py3.11 绑定 .pyd，版本过老）、copy Linux gdal 包（Linux 产物 + 服务器 Python ABI，二进制不兼容）、外部 wheel 源（conda-forge / Gohlke，内网机无法访问外网）
- 为什么不能占位/替身：GDAL 在热路径上——`util.read_img` 用 `gdal.Open` 读 GeoTIFF（util.py 182/192 行）、`util.writeTiff` 用 `GetDriverByName("GTiff")` 写（util.py 581/584 行），占位模块过不了运行层

**torch 1.9.1+cu111——死路**
- 证据：内网镜像 cgwx-pypi 只代理 PyPI，PyPI 无 `+cu111` 后缀 wheel → 只能装到 CPU 2.4.1

**Windows 原生路线的最终判断（py3.11 venv）**
- 失败点叠加：.so 平台墙 + GDAL 无 wheel + torch cu111 缺失；换 py3.6 只解决版本墙，.so 与 GDAL 墙依旧
- 结论：真管线（py3.6 + torch1.9.1+cu111 + GDAL + ImgHistMatch.so + mmsr_bundle）的"最后一公里"全是服务器专属；Windows 本机定位为平台侧（sr_agent_web + adapter），本地开发用 min_sr 当接口替身
- min_sr 产物位置：`D:\sr_min\`（对齐契约接口的最小超分管线：读 config.xml、cv2 双三次 2x、输出 `*_sr.tif` + 源图 `*_NOSR.tif` + `Debug/SRLOG` 尾行 `Run finished.`）

## 附录 B：向 mentor 汇报的压缩版（50 字内）

> 内网Win py3.11缺GDAL：镜像仅源码无wheel，无conda，仅2.0.1裸DLL，import osgeo失败

---

## 关键命令速查

```powershell
# pip 镜像（全局）
pip config set global.index-url http://nexus.jl1.cn/repository/cgwx-pypi/simple
pip config set global.trusted-host nexus.jl1.cn

# pip 单次安装指定镜像源（-i 写法，内网机 ad-hoc 装包通用；CentOS7 服务器装依赖同源）
pip install <包> -i http://nexus.jl1.cn/repository/cgwx-pypi/simple --trusted-host nexus.jl1.cn
# 例：pip install numpy -i http://nexus.jl1.cn/repository/cgwx-pypi/simple --trusted-host nexus.jl1.cn
# 平台后端依赖（requirements-api.txt）内网装机也走本镜像，见 deploy/README.md

# conda（D 盘那个）
D:\ProgramData\anaconda3\Scripts\conda.exe config --add channels http://nexus.jl1.cn/repository/cgwx-anaconda
D:\ProgramData\anaconda3\Scripts\conda.exe config --remove channels defaults

# py310pan 环境
D:\ProgramData\anaconda3\envs\py310pan\python.exe -m pip install "numpy==1.26.4"   # 降 numpy 修 ABI
D:\ProgramData\anaconda3\envs\py310pan\python.exe -m pip install opencv-python      # 补 cv2
D:\ProgramData\anaconda3\envs\py310pan\python.exe -c "import torch; print(torch.__version__, torch.version.cuda, torch.cuda.is_available())"  # 查 torch 是否 CUDA 版

# 探 torch 1114（DLL 逐个加载定位 c10.dll）
D:\sr_min\venv\Scripts\python D:\sr_min\probe_torch_dlls.py

# WSL2 / Docker 可行性检查（管理员）
nvidia-smi                                    # 确认 GPU + 驱动
Get-WindowsOptionalFeature -Online -FeatureName Microsoft-Windows-Subsystem-Linux   # Disabled → 需开启
Get-WindowsOptionalFeature -Online -FeatureName VirtualMachinePlatform              # Disabled → 需开启
wsl --status
net session >$null 2>&1; if($?){"管理员：是"}else{"管理员：否"}
```
