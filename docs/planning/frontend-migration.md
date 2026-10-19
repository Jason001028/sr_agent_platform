# 前端迁移规划：tif-viewer → Vue3（frontend/）

> 日期：2026-09-01（阶段计划定稿）；2026-09-03 更新：阶段 1-5 **离机全部完成**，仅剩真机验收。
> 状态：已定（阶段 1-5 完成 · 待真机验收）
> 定位：将现有 HTML 版 TIF 查看器 + 掩码工具迁移到 Vue3，作为平台（FastAPI + 共享队列 + 聊天）前端地基。本文固话 brief 的阶段计划，替代 gitignore 的 `docs/planning/web-plan.md` 作为交接依据。

---

## 1. 目标与「移植不重写」边界

- **目标**：`tif_viewer/tif-viewer.html` + `tif_viewer/maskgen.js` 迁移为 Vue3 前端 `frontend/`，行为正确性以 `docs/experience/gui-experience.md` 为唯一权威来源。
- **移植不重写**：解码/拉伸/稀疏/掩码算法抽成**框架无关 TS 模块**（`tifDecode.ts` / `maskgen.ts`），逐函数、逐字节保留行为（含 BigTIFF 偏移布局、稀疏映射 `round(i*(W-1)/(pw-1))`、拉伸 alpha 永不缩放、WhiteIsZero 反色、min-max 跳过 NaN/Inf、常量图退化、chunked 让出主线程进度、掩码边界含入/even-odd+UNION）。**交互层**（画布事件、绘制面板、导出编排、状态栏）用 Vue3 惯用法重写。
- **双数据源**：本地 = `source.ts` 统一抽象 `source.read(offset,len)→Promise<ArrayBuffer>`（`FileSource` 已实现）；盘阵（Phase 4 改向，见下）= **服务器预生成 JPG**，浏览器不读 TIF 字节，`HttpSource`（Range）已废弃不实现。IFD/分块/拉伸/稀疏只服务本地路径，两处复用同一套，不写两套。
- **掩码** = 像素坐标数组存后端；任务队列多人共享、服务端唯一事实源、SSE 先行（REST 动作 + SSE 推送）。
- **硬性约束（违反即失败）**：浏览器单次分配 ~2GB（大图绝不全量解码，走分块窗口 4096 或稀疏条带）；Canvas 面积上限 16384²（导出 JPG 默认长边 8192，失败降档 4096）；真实大图只在内网盘阵，功能对未知结构鲁棒；vendor 的 utif.js 为补丁版（cmpr==8/32946 走 pako inflate），**绝不 npm 重装覆盖**。

## 2. 阶段计划

| 阶段 | 内容 | 验收 | 状态 |
|---|---|---|---|
| 1 · 纯 TS 核心抽取 | `frontend/src/lib/` 下 `tifDecode.ts` / `maskgen.ts` / `source.ts`；17 项 maskgen 测试移植为 Vitest TS 版 + tifDecode 像素 golden | Node 测试全过；原 `tif-viewer.html` 浏览器回归不破坏；tifDecode 与 HTML 版对同一测试图逐像素一致 | 完成（2026-09-01） |
| 2 · Vue3 工程骨架 | Vite+Vue3+TS 脚手架、离线 vendor 落地、路由骨架 `/viewer /chat /queue`、Pinia/composable 状态层、Nginx 托管 + 离线交付压缩包脚本 | `vite build` 后 dist 无外部 CDN 引用，Nginx 打开查看器可用 | 完成（2026-09-01） |
| 3 · 查看器 UI 组件化 | 视图数学抽纯函数并 Node 测；`TifCanvas.vue` 双画布分层；工具栏/文件列表/状态栏/拉伸下拉/输出目录授权；掩码绘制面板 + 事件 + 协程进度条 + owner 守卫 + genMask 直出；导出层串行 + 降档 + saver 抽象；重建 E2E 钩子等价物 | Vue3 查看器跑通等价浏览器回归；掩码 17 项 Node 测试仍全过 | 完成（2026-09-01） |
| 4 · 盘阵场景读 JPG（09-02 改向，废弃 HttpSource/Range） | 后端 `api/` FastAPI（`/api/scenes` 检索补 W/H、`/api/scenes/{id}/preview` 懒生成 JPG）+ `services/preview_jpg.py`（镜像前端稀疏采样语义：纯 numpy 抽行 + 2% Linear + Pillow，幂等缓存）+ 路径白名单；前端 `/scenes` 页筛选/打开 → JPG→canvas → route='jpg' 同构 rec，掩码按元数据 W/H 换算；nginx /disk-array 静态托管 + /api 反代 + systemd | 后端 pytest + 前端 Vitest + `.e2e`（本地静态服务顶替 nginx）全过；本地文件零回归（75 Vitest + 38 .e2e 断言） | 完成（2026-09-02） |
| 5 · 平台新功能 | 聊天界面（Agent loop 对接 SSE）、共享任务队列（乐观更新 + 并发处理，服务端唯一事实源）、查看器掩码→SR 提交 | 契约先文档（`docs/planning/api-contract.md` 已定）；后端 190 + Vitest 114 + `.e2e/test-platform.js` 11 断言（离机 mock+fake）全过；真机见 real-machine-acceptance.md | 完成（2026-09-02） |

> 阶段间红线：每阶段提交一次，commit message 说明「移植不重写」边界。掩码像素正确性只认 Node golden。内存/画布/2GB 是运行约束，最终确认靠内网真机。

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
│   ├── lib/                 # 框架无关 TS 模块（Phase 1 主产物）
│   │   ├── tifDecode.ts     # 解码/拉伸/稀疏/分块纯逻辑
│   │   ├── maskgen.ts       # maskgen.js 机械直译，接口不变
│   │   ├── source.ts        # Source 接口 + FileSource + HttpSource 桩
│   │   ├── viewMath.ts      # 视图数学纯函数（Phase 3）
│   │   └── __tests__/       # Vitest 测试
│   ├── vendor/              # 离线 vendor（补丁版 utif.js 必在此，绝不 npm 覆盖）
│   │   ├── pako.min.js
│   │   ├── utif.js          # 补丁版（cmpr 8/32946 → pako inflate）
│   │   └── geotiff.min.js   # geotiff@3.0.5
│   └── e2e.ts               # window.__viewer 等价物适配层（Phase 3）
└── dist/                    # Vite 构建产物（自包含，离线交付）
```

## 4. 测试策略（三件套，对齐 .e2e 经验）

| 层 | 运行器 | 覆盖 |
|---|---|---|
| 纯算法层 | Vitest（Node，脱离浏览器） | maskgen 17 项直译 + tifDecode 像素 golden |
| 交互层 | puppeteer 浏览器 e2e（file:// 或 Vite dev server） | 由 .e2e/ 承担（2026-09-15 起**脚本入库**，只忽略 `node_modules/`+fixtures+大图） |
| 接线 | jsdom | Vue3 组件接线冒烟（Phase 3） |

- **fixtures 自包含（2026-09-01 定）**：`frontend/scripts/gen-fixtures.py`（零依赖）+ 提交小尺寸 golden 图入库，`npm test` 全新 clone 离线即跑。真实大图（8192²/134MB）回归仍靠 `.e2e/` 本机资产。
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

## 6. 交接记录

- 2026-09-01：本文档固化。决策：golden 测试**自包含 fixtures** 入库；开工前先固化规划文档。Phase 1 启动。
- 2026-09-01：Phase 1 提交。验收：`tsc --noEmit` 全绿；Vitest 51 全过（17 maskgen + 30 tifDecode + 4 source）；`.e2e` 浏览器回归全过（bootstrap 13 + types 13 + sparse + maskgen 17），原 HTML 未破坏。
  **移植偏差 1 处（已记录在 tifDecode.ts bandPassCollect 注释）**：原 HTML 的带通终端 `return Promise.resolve()` 丢弃就地填充的 `src`，会令 8192² 高分导出（走带通分支时）拿到的 Promise 解析为 `undefined`，属潜在 bug；TS 版改为 `return Promise.resolve(src)`，兑现"产出 src"的函数契约，其余算法逐字节不变。
- 2026-09-01：Phase 2 提交。验收：`vue-tsc --noEmit` 全绿；Vitest 51 全过；`.e2e/check-frontend-build.js` 对 `frontend/dist` 静态服务 + 无头浏览器实测全过（无外部请求、SPA fallback、`u16_whitezero.tif` 解码出 256×128 渐变、拉伸切换重绘、`/viewer/` 尾斜杠 301）。交付物：`deploy/nginx.conf`（SPA fallback + assets 长缓存 + 尾斜杠规整）、`deploy/README.md`（CentOS7 部署）、`frontend/scripts/package-offline.sh`（`npm run package:offline` → `release/*.tar.gz`）。
  **构建互操作坑 2 处（已在代码注释固化）**：(1) geotiff@3.0.5 UMD 带 `exports.default=GeoTIFF(类)`，default 导入在 esbuild(vitest) 解析成 module.exports、rollup(vite build) 解析成类 → 运行时 `fromBlob is not a function`；改用 `import * as GeoTIFF`（命名空间，两种转换都指向 exports）。(2) Vite 6 @rollup/plugin-commonjs 默认只处理 node_modules，`src/vendor` UMD 需 `commonjsOptions.include` 显式纳入；vendor 环境声明因"同名 .ts/.d.ts 兄弟"会被 TS 挤出 program，统一收在 `src/env.d.ts`（无同名 .ts 兄弟）。
- 2026-09-01：Phase 3 提交（commit `3e26e35`）。验收：`vue-tsc --noEmit` 零错误；Vitest 75 全过；`.e2e/test-vue-viewer.js` 38 断言全过（启动 30 钩子齐全 / 解码路由 utif·chunked·bigtiff·sparse 四条 + 像素 / 拉伸重绘 / 像素定位 7s 过期 / 掩码冒烟 enterDraw→commitRect→merge→del 红闪→undo/clear / 导出降档假 saver），`test-html-mask-smoke.js` 兼容旧选择器。交付物：六组件 `TifCanvas/Toolbar/FileList/DrawPanel/DecodeOverlay/StatusBar` + `stores/viewer.ts`（718 行 decodeRec/paintStretch）+ `lib/{viewMath,browserKit,decode,exportJpg,saver}` + `viewer/e2eHooks.ts`（`window.__viewer` 始终暴露）。**移植偏差 1 处**：BigTIFF 强制走 geotiff.js 分块读取（内置 UTIF 解不了 BigTIFF → 解码后 W/H 丢失，旧 HTML 同条件产出 0×0 缩略图；`needGeo` 探测 `probe.big` 即走 chunked，`bigtiff_strips` 样例 256×256 解码正确）。e2e 浏览器修复：`launchBrowser.js` 独立临时 profile 解决退出码 0 闪退。真机项（真实 24739×24199 大图稀疏 8192 不崩 / 掩码直出 / 2% Linear 导出 8192×8013 无条纹）随 §6 一页纸。
- 2026-09-02：Phase 4 提交（阶段4 改向见 §2 行4）。验收：backend pytest 148 全过 + 前端 Vitest 85 全过（75 基线 + 10 scene）+ `vue-tsc --noEmit` 零错误；`.e2e` 全绿（`test-vue-viewer.js` 42 断言本地文件回归 + `test-scenes.js` 45 断言场景 http 打开）。交付物：`backend/api/` + `backend/services/preview_jpg.py`（+路径白名单 `api/paths.py`）、前端 `/scenes` 页 + `lib/scene.ts` + viewer `route='jpg'` 同构 rec + 拉伸/导出禁用、`deploy/nginx.conf`（/disk-array/ alias + /api 反代）+ `deploy/sr-api.service` + `deploy/requirements-api.txt` + `deploy/README.md` 重写。`package-offline.sh` 增补 backend + systemd 进离线包。真机验收项见 real-machine-acceptance.md（需 CentOS7/Win11 内网机）。
- 2026-09-02：Phase 5 提交（commit `c5ca206`）。契约先文档：`docs/planning/api-contract.md` 定稿（状态已定）。验收（离机）：后端 190 全过（`test_api_platform.py` +42，含 mock LLM 端到端 + 假调度器）+ 前端 Vitest 114 + `vue-tsc --noEmit` 零错误 + `npm run build`；`.e2e/test-platform.js` 11 断言（真 uvicorn mock+fake 驱动前端：A 聊天 SSE/历史 → B 队列 COMPLETED → C 掩码→SR 预填+确认，无 pageerror、无外网请求）。交付物：`backend/api/platform.py`（`/api/tools` manifest+直调 · `/api/chat/*` 会话 REST + 单回合 SSE【loop 加 `on_step` 观察缝，不改行为】 · `/api/queue*` REST + `GET /api/queue/events` SSE 状态广播【`SR_SLURM_FAKE` 假调度器可配速】 · `/api/masks` 多边形+W/H → Pillow 全分辨率栅格化落 `<原图目录>/<stem>_mask.tif`+`_mask.txt`）+ `app.py` lifespan 轮询；store 补 list/状态写回；前端 `lib/api.ts` + `stores/{chat,queue}.ts` + `pages/{ChatPage,QueuePage}.vue` + 查看器「提交 SR」（`stores/viewer.ts::submitSr`，route='jpg' 画掩码→烘焙落盘→跳 /queue 预填**不自动提交**）。部署件：nginx `/api/` 反代 `proxy_buffering off`+清 Connection+`proxy_read_timeout 3600s`（SSE）；`sr-api.service` env `SR_AGENT_DB`/`SR_LLM_MOCK=0`/`SR_SLURM_FAKE=0`；`requirements-api.txt` 补 `openai>=1.40,<2`。真机验收 = real-machine-acceptance.md 一页纸（从零部署 → 阶段4/5 验收 → 决策点）。
