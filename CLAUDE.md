# sr_agent_platform — 项目导航

> 本文件帮 Claude 会话快速定位文档与代码。文档全部归档于 `docs/`，分类索引见 [docs/README.md](docs/README.md)。

## 文档（先读哪个）

- **窗口交接**：新会话先读 [docs/status/current-question.md](docs/status/current-question.md)，再读 [docs/experience/gui-experience.md](docs/experience/gui-experience.md)（经验），即可无断点继续。
- **分类速查**：需求/计划 → [docs/planning/](docs/planning/)；经验 → [docs/experience/](docs/experience/)；规范 → [docs/conventions/](docs/conventions/)；背景知识 → [docs/knowledge/](docs/knowledge/)。
- **新增文档**：先看 [docs/README.md](docs/README.md) §三分类规则，再落盘对应类目。

## 代码主产物

- [tif_viewer/tif-viewer.html](tif_viewer/tif-viewer.html) —— 遥感大 TIF 查看器（UTIF / geotiff 分块 / 稀疏条带 三路分派 + JPG 中间产物导出）。本地 vendor，离线可用。
- [quick-look.html](quick-look.html) —— 快速查看器（旧）。
- 测试：[.e2e/](.e2e/)（puppeteer-core + 无头 Edge，本地 vendor）；测试图 [test-tifs/](test-tifs/)（gitignore）。

## 硬性约束（详见 gui-experience.md §1）

浏览器单次分配 ~2GB、Canvas 面积上限 16384²、CDN 不可达（必须本地 vendor）、真实大图仅在内网机盘阵（外网开发机读不到，结构靠用户回传 ENVI 头信息推断）。
