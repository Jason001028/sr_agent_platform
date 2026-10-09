# 前端迁移规划：tif-viewer → Vue3（frontend/）

> 日期：2026-09-01 · 状态：已定（离机完成 · 待真机验收）
> 定位：前端的架构与边界说明——HTML 版 TIF 查看器 + 掩码工具迁移到 Vue3（`frontend/`），作为平台（FastAPI + 共享队列 + 聊天）前端地基。

---

## 1. 目标与「移植不重写」边界

- **产物**：`tif_viewer/tif-viewer.html` + `tif_viewer/maskgen.js` 的 Vue3 前端落在 `frontend/`；行为正确性以 `docs/experience/gui-experience.md` 为唯一权威来源。
- **移植不重写**：解码/拉伸/稀疏/掩码算法抽成**框架无关 TS 模块**（`tifDecode.ts` / `maskgen.ts`），逐函数、逐字节保留行为（含 BigTIFF 偏移布局、稀疏映射 `round(i*(W-1)/(pw-1))`、拉伸 alpha 永不缩放、WhiteIsZero 反色、min-max 跳过 NaN/Inf、常量图退化、chunked 让出主线程进度、掩码边界含入/even-odd+UNION）。**交互层**（画布事件、绘制面板、导出编排、状态栏）用 Vue3 惯用法重写。
- **双数据源**：本地 = `source.ts` 统一抽象 `source.read(offset,len)→Promise<ArrayBuffer>`（`FileSource`）；盘阵 = **服务器预生成 JPG**，浏览器不读 TIF 字节（`HttpSource`/Range 路径不实现）。IFD/分块/拉伸/稀疏只服务本地路径，两处复用同一套，不写两套。
- **掩码** = 像素坐标数组存后端；任务队列多人共享、服务端唯一事实源、SSE 先行（REST 动作 + SSE 推送）。
- **硬性约束（违反即失败）**：浏览器单次分配 ~2GB（大图绝不全量解码，走分块窗口 4096 或稀疏条带）；Canvas 面积上限 16384²（导出 JPG 默认长边 8192，失败降档 4096）；真实大图只在内网盘阵，功能对未知结构鲁棒；vendor 的 utif.js 为补丁版（cmpr==8/32946 走 pako inflate），**绝不 npm 重装覆盖**。

## 2. 已实现能力

| 能力 | 内容 |
|---|---|
| 纯 TS 核心 | `frontend/src/lib/` 下 `tifDecode.ts` / `maskgen.ts` / `source.ts`；maskgen 17 项测试移植为 Vitest TS 版 + tifDecode 像素 golden |
| Vue3 工程骨架 | Vite+Vue3+TS 脚手架、离线 vendor、路由骨架 `/viewer /chat /queue`、Pinia/composable 状态层、Nginx 托管 + 离线交付压缩包脚本 |
| 查看器 UI 组件化 | 视图数学抽纯函数；`TifCanvas.vue` 双画布分层；工具栏/文件列表/状态栏/拉伸下拉；掩码绘制面板 + 事件 + 协程进度条 + owner 守卫 + genMask 直出；导出层串行 + 降档 + saver 抽象；重建 E2E 钩子等价物 |
| 盘阵场景读 JPG | 后端 `api/` FastAPI（`/api/scenes` 检索补 W/H、`/api/scenes/{id}/preview` 懒生成 JPG）+ `services/preview_jpg.py`（镜像前端稀疏采样语义：纯 numpy 抽行 + 2% Linear + Pillow，幂等缓存）+ 路径白名单；前端 `/scenes` 页筛选/打开 → JPG→canvas → route='jpg' 同构 rec，掩码按元数据 W/H 换算；nginx /disk-array 静态托管 + /api 反代 + systemd |
| 平台功能 | 聊天界面（Agent loop 对接 SSE）、共享任务队列（乐观更新 + 并发处理，服务端唯一事实源）、查看器掩码→SR 提交 |

> 红线：掩码像素正确性只认 Node golden。内存/画布/2GB 是运行约束，最终确认靠内网真机。

## 3. frontend/ 目录结构（对齐 naming-conventions §4）

```
frontend/
├── package.json / tsconfig.json / vite.config.ts / vitest.config.ts
├── index.html
├── public/                  # Nginx 静态资源（favicon 等）
├── scripts/
│   └── gen-fixtures.py      # 零依赖 golden 测试图生成（自包含，仿 .e2e/gen-test-images.py）
├── src/
│   ├── main.ts / App.vue
│   ├── pages/               # 页面：ViewerPage.vue / ChatPage.vue / QueuePage.vue
│   ├── components/          # 复用组件：TifCanvas.vue / Toolbar*.vue / MaskPanel.vue / ExportPanel.vue / StatusBar.vue
│   ├── stores/              # Pinia stores（viewer / queue / chat）
│   ├── lib/                 # 框架无关 TS 模块
│   │   ├── tifDecode.ts     # 解码/拉伸/稀疏/分块纯逻辑
│   │   ├── maskgen.ts       # maskgen.js 机械直译，接口不变
│   │   ├── source.ts        # Source 接口 + FileSource + HttpSource 桩
│   │   ├── viewMath.ts      # 视图数学纯函数
│   │   └── __tests__/       # Vitest 测试
│   ├── vendor/              # 离线 vendor（补丁版 utif.js 必在此，绝不 npm 覆盖）
│   │   ├── pako.min.js
│   │   ├── utif.js          # 补丁版（cmpr 8/32946 → pako inflate）
│   │   └── geotiff.min.js   # geotiff@3.0.5
│   └── e2e.ts               # window.__viewer 等价物适配层
└── dist/                    # Vite 构建产物（自包含，离线交付）
```

## 4. 测试策略（三件套，对齐 .e2e 经验）

| 层 | 运行器 | 覆盖 |
|---|---|---|
| 纯算法层 | Vitest（Node，脱离浏览器） | maskgen 17 项直译 + tifDecode 像素 golden |
| 交互层 | puppeteer 浏览器 e2e（file:// 或 Vite dev server） | 由 .e2e/ 承担（**脚本入库**，只忽略 `node_modules/`+fixtures+大图） |
| 接线 | jsdom | Vue3 组件接线冒烟 |

- **fixtures 自包含**：`frontend/scripts/gen-fixtures.py`（零依赖）+ 提交小尺寸 golden 图入库，`npm test` 全新 clone 离线即跑。真实大图（8192²/134MB）回归仍靠 `.e2e/` 本机资产。
- **检查点**：tifDecode.ts 与 HTML 版对同一测试图**逐像素一致**（golden 数组来自 .e2e 已验证的浏览器版输出）。
- **交叉比对**：掩码与 Pillow 逐像素比对（`backend/services/mask.py` 已 12 测试锁住）；tifDecode 与 .e2e 浏览器版输出比对。

## 5. 关键常量（抽取时保持，测试断言依赖）

```
PREVIEW_MAX=2048         // chunked/UTIF 路径预览长边
SPARSE_PREVIEW_MAX=8192  // 稀疏路径预览长边（≈原始 1/3）
SAFE=1.3e9               // 解码缓冲/RGBA 安全上限
SPARSE_MIN=1e8           // 稀疏条带候选字节下限
chunk=4096               // 分块窗口
WAND_MAX_PX=8e6          // 魔棒选区上限
WAND_EDGE=64             // 魔棒边缘梯度屏障
JPG_MAX=8192             // 导出 JPG 长边，失败降档 4096
BAND_ACC_LIMIT≈4e8       // 带通累加器分界
EXPORT_BUDGET≈2.4e9      // 导出内存预算
```
