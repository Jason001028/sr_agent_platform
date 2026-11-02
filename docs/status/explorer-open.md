# 缩略图卡片：在资源管理器中打开

> 日期：2026-11-02 · 状态：用户已反馈安装及打开流程跑通，并反馈 PowerShell 窗口闪现；新增无控制台 GUI 启动器，本地已验证后台 PowerShell 控制台句柄为 0。内网覆盖安装后的实际效果待用户确认。

## 使用与界面

每张非占位卡片右上角，在 × 左边增加 15px 文件夹图标，按钮 24×22px，淡绿色底与细描边；悬停/键盘聚焦再加强颜色。点击不激活卡片、不移除、不截图。多选时隐藏；没有 sceneDir 的本地影像置灰，提示先关联服务端目录。

PAN、NOSR、SR 卡片使用各自已确认的 sceneDir，打开所在目录，不打开或执行 TIFF 文件。不能从浏览器 File.name 猜出访问者本机完整路径；未关联文件不会猜目录。

首次点击展示安装说明、服务端路径与打开链接。用户点击“已安装，打开目录”后，此浏览器记住配置提示，下次图标直接调用协议。Shift 点击或右键始终可重新打开说明。localStorage 只控制提示，不代表检测到安装成功；浏览器不提供可靠的安装/打开成功回执，界面不显示虚假的“打开成功”。关闭弹窗或 Escape 会返回原卡片。

## Windows 客户端一次性配置

首次说明提供显眼的“下载 Windows 安装包”按钮。下载 SrExplorer-Setup.zip 后先解压，双击 Install-SrExplorer.cmd；按公司统一环境直接使用 /DiskArray → W:\，不询问盘符、不要求输入命令。成功后窗口约 3 秒自动关闭；失败时停留以便查看错误。新版同时合并当前用户的 Chrome 免提示规则；此前已安装但每次仍弹窗的用户需重新运行新版安装包一次。

安装包随 frontend/dist/client-explorer/SrExplorer-Setup.zip 发布，由 scripts/explorer-package.mjs 在 Vite 开发服务与正式构建时从当前源码生成，包含 Install-SrExplorer.cmd、Install-SrExplorer.ps1、Chrome-SrExplorerPolicy.ps1 三个文件；须全部解压并保持同一文件夹。不维护手工 ZIP，不依赖 CDN、API 或额外压缩依赖。

以下为折叠在界面“高级配置与手动安装”中的替代步骤：

在**访问者 Windows 电脑**、脚本所在文件夹运行，不在 Linux API 服务器上运行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\Install-SrExplorer.ps1
```

默认客户端映射 /DiskArray → W:\。W: 必须已在该用户的普通 Windows 会话中连通；脚本不会挂载盘阵、收集凭据或修改服务器。盘符不同可配置：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\Install-SrExplorer.ps1 -WindowsRoot 'Z:\'
```

服务器挂载前缀不同，可同时传 -ServerRoot '/实际挂载根'。根按大小写与完整路径段匹配，/DiskArrayElse 不会命中 /DiskArray。本版只支持一个 POSIX 服务端根映射到一个本机盘符根；多映射与 UNC 不在本版范围。

安装位置：%LOCALAPPDATA%\SrAgentExplorer，包含 Open-SrDirectory.exe、Open-SrDirectory.ps1 与 mapping.json；协议注册在 HKCU\Software\Classes\sr-explorer。公司客户端盘符统一 W:，客户端 mapping.json 与服务端 SR_DRIVE_MAP 仍是独立配置；特殊情况可通过高级参数覆盖。

卸载（使用同一份下载脚本）：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\Install-SrExplorer.ps1 -Uninstall
```

重复安装更新当前用户配置。卸载只移除本组件拥有的协议与两个文件；不会递归清空安装目录或处理影像目录。客户端脚本位于前端静态包，前端升级不自动覆盖已安装的客户端；修改处理器时需重新运行下载脚本。

## Chrome 每次提示打开 PowerShell：一次性配置

在访问平台的 Windows 客户端运行新版安装包，不在 API 服务器或外网开发机运行。安装程序写入当前用户的 HKCU\Software\Policies\Google\Chrome 下 REG_SZ 值 AutoLaunchProtocolsFromOrigins，仅增加：

```json
[{"protocol":"sr-explorer","allowed_origins":["http://10.10.81.135:80"]}]
```

此处按用户提供的 10.10.81.135/viewer 使用 HTTP 默认 80 端口；不含 /viewer 路径，不使用通配符。若实际改成 HTTPS、其他主机或端口，应同步更新规则；外网开发地址不会因此免提示。该配置不需要访问互联网，也不连接或探测内网服务器。

安装后验收：

1. 在 Chrome 地址栏输入 chrome://policy。
2. 点击“重新加载政策 / Reload policies”，搜索 AutoLaunchProtocolsFromOrigins。
3. 核对规则仅包含所需内网站点与 sr-explorer，状态正常且未被覆盖/忽略。
4. 回到 http://10.10.81.135/viewer 点击目录图标，确认不再提示；必要时保存工作后关闭并重新打开 Chrome。

已有其他协议规则时按内容合并；已有同条规则时不重复增加。原有 JSON 或注册表类型异常时拒绝覆盖，安装窗口保留错误。公司机器级或云端策略可能优先，遇到“忽略/覆盖/错误”应交给 IT 下发同条规则，不自动覆盖 HKLM、公司策略或设置全局放行。

组件将本次是否新增该规则记录到 %LOCALAPPDATA%\SrAgentExplorer\chrome-policy.json。卸载时只移除本组件新增的该站点规则，保留原本已存在的相同规则、其他站点和协议；不整项删除公司已有策略。保存到注册表不等于 Chrome 已采纳，脚本只报告配置写入并提示检查；目前无公司客户端免提示实证。

策略支持 HTTP 来源，且禁止将 /viewer 等路径写入 allowed_origins，见 [Chromium 官方策略定义](https://raw.githubusercontent.com/chromium/chromium/main/components/policy/resources/templates/policy_definitions/Miscellaneous/AutoLaunchProtocolsFromOrigins.yaml)。企业策略禁用 PowerShell 或自定义协议时，仍需内网管理员按现有策略部署。

## 技术与边界

- 前端发送 sr-explorer://open/<base64url-UTF8目录>；点击直接发起，无异步 API 导致用户手势丢失。
- 协议改为启动本机 Open-SrDirectory.exe。安装时用 Windows PowerShell 自带的 .NET 编译能力生成 WindowsApplication（PE GUI 子系统），由它通过 UseShellExecute=false、CreateNoWindow=true 启动固定处理器。仅给 powershell.exe 传 WindowStyle Hidden 仍可能先分配控制台再隐藏，这是旧版闪窗的原因；新版不分配控制台。
- 正常打开时只显示资源管理器；安装窗口和确有错误时的提示保持可见。没有修改 Chrome 安全策略之外的系统设置，没有依赖 VBS 或下载预编译 EXE。公司若禁止本地 .NET 编译或新 EXE 运行，安装/启动会如实失败，需按公司 IT 规则部署。
- 客户端只接受固定 open 动作与严格 base64url，严格 UTF-8 解码后拒绝控制字符、引号、路径穿越、设备名、非法 Windows 文件名及白名单根之外的目录。目录在当前客户端可访问时才启动 explorer.exe。
- 请求不进入 Shell、Invoke-Expression、cmd 或任意应用执行链；路径是单个引号保护的 Explorer 目录参数。中文、空格和 &、$ 等按数据处理。
- 不在 API 服务器启动 Explorer；无需新增后端开目录接口、不扫盘、不改变 SR/Mask/预览权限。
- 纯 HTTP 部署不能据此假设 file 链接可用。Edge 的 [IntranetFileLinksEnabled 官方说明](https://learn.microsoft.com/en-us/deployedge/microsoft-edge-policies/intranetfilelinksenabled)还要求相应内网 HTTPS 与策略；本功能采用显式安装的协议组件。
- 用户已反馈原安装及打开流程跑通，未回传逐项验收记录；不能据此外推所有 PAN/NOSR/SR 与异常场景均通过。断开映射盘时应显示客户端错误，不宣称打开成功。

## 实现与验证

前端：components/ExplorerButton.vue、lib/explorer.ts，由 FileList.vue 在关闭按钮左侧接入。安装源码位于 frontend/public/client-explorer/，增加 Chrome-SrExplorerPolicy.ps1 负责免提示规则合并与卸载。构建只打包，不执行安装，测试也不会自动修改注册表。

```powershell
# 在 frontend/ 目录运行；直接执行本地工具，避开本机全局 npm 入口权限问题
node node_modules/vue-tsc/bin/vue-tsc.js --noEmit
node node_modules/vitest/vitest.mjs run src/lib/__tests__/explorer.test.ts
node node_modules/vite/bin/vite.js build

# 在仓库根目录运行
powershell -NoProfile -ExecutionPolicy Bypass -File .e2e/test-explorer-handler.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File .e2e/test-explorer-chrome-policy.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File .e2e/test-explorer-launcher.ps1
node .e2e/test-explorer.js
```

客户端处理器 21 项测试覆盖根目录/中文/空格/特殊字符、前缀混淆、穿越、非法 URI/UTF-8、设备名等；不会写注册表或启动 Explorer。原生启动器专项测试编译实际 GUI EXE、检查 PE 子系统为 2，并用安全的记录型处理器证明带空格目录可运行、URI 原样传递、PowerShell 的 GetConsoleWindow()=0；不注册协议或打开资源管理器。浏览器回归使用真实 FileList，验证位置/尺寸/文字避让、关联与未关联、首次说明、安装下载、路径与协议、点击隔离、Escape、重新配置和多选；截图在被忽略的 .e2e/fixtures/ 下。命令行浏览器直接启动失败后，已使用仓库现有 puppeteer-core + 独立临时配置目录完成实测。
