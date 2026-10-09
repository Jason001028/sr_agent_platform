/**
 * scene.ts — 盘阵场景（阶段4：读服务器预生成 JPG）纯函数 + 类型
 * ------------------------------------------------------------------
 * 阶段4 数据路径：浏览器不读盘阵原始 TIF 字节，显示 = 服务器生成预览的 JPG
 * （长宽各为源图的 1/2；稀疏采样 + 直方图均衡已生成预览）。本文件只放可单测的纯函数
 * 与前后端共享类型：
 *   - 运行期配置（apiBase/staticBase）：默认同源（nginx 同时暴露 /api 与 /disk-array），
 *     e2e/异源部署注入 window.__SR_CFG__。
 *   - 场景检索 query 串拼装（镜像后端 search_scenes 参数）。
 *   - JPG 画布 RGBA → 单波段自然值 src（灰图 R=G=B；stats 固定 0..255 → 线性=恒等，
 *     其余拉伸模式是显示层的二次拉伸，起手值见 startStretch）。与本地稀疏路径的
 *     rec 结构同构（route 不同）。
 *   - 掩码换算：缩略图坐标 → 全分辨率（thumbToOrig，scale 由元数据 W/H 提供而非 probe）。
 */
import { computeStats } from './tifDecode.js';
import type { BandStats, StretchMode } from './tifDecode.js';
import { thumbToOrig } from './viewMath.js';
import type { Poly } from './maskgen.js';
import type { StageKind } from './stage.js';

/* ---------------- 运行期配置（前后端 / e2e 注入） ---------------- */
export interface SrConfig {
  /** REST API 前缀（含 /api 的源）。默认 '' = 同源（nginx 反代）。 */
  apiBase: string;
  /** 盘阵静态前缀（/disk-array/… 的源）。默认 '' = 同源。 */
  staticBase: string;
}

const SR_DEFAULT: SrConfig = { apiBase: '', staticBase: '' };

declare global {
  interface Window {
    __SR_CFG__?: Partial<SrConfig>;
  }
}

/** 读取运行期配置；无 window（Node 测试）或无注入 → 同源默认。 */
export function loadSrConfig(): SrConfig {
  const w = typeof window === 'undefined' ? undefined : window;
  const c = w && w.__SR_CFG__ ? w.__SR_CFG__ : {};
  return { apiBase: c.apiBase ?? SR_DEFAULT.apiBase, staticBase: c.staticBase ?? SR_DEFAULT.staticBase };
}

/** base 与以 / 开头的 path 拼接（去重斜杠；base 为空直接返回 path）。 */
export function joinBase(base: string, path: string): string {
  if (!base) return path;
  const b = base.replace(/\/+$/, '');
  return b + (path.startsWith('/') ? path : '/' + path);
}

/* ---------------- 与后端 /api/scenes 响应对齐的类型 ---------------- */
export interface SceneRow {
  id: string;               // 不透明 id（供 /api/scenes/{id}/preview）
  name: string;             // 可读文件名（stem）
  satellite: string | null;
  sensor: string | null;
  date: string | null;      // YYYY-MM-DD
  size_bytes: number;
  fake: boolean;
  W: number | null;
  H: number | null;
  rel: string | null;       // scenes 根下相对路径（仅调试用）
  jpgUrl: string | null;    // 相对 /disk-array/…（JPG 已生成才非空）
  hasPreview: boolean;      // 服务器缓存 JPG 是否已生成
  /** 盘上那份预览是按哪一档（各边 ÷N）生成的；null = 没有 / 旧格式戳 /
   *  读不出 / 源本身就是显示就绪图（不参与档位）。
   *
   *  `hasPreview` **不认档位** —— 换档位后盘上那份旧图仍在，只看它就会跳过
   *  重新生成、把旧档位的图端上来。所以判定「要不要重新生成」必须连这个字段一起看
   *  （见 lib/api.ts 的 fetchSceneJpg）。 */
  previewDiv?: number | null;
  /** 阶段6 viewer 上下文侧舱：scene 文件父目录绝对路径（= run_sr 目录语义，
   *  与 /api/queue params.lq_path 同值关联）；disk 行非空、fake 恒 null。 */
  lq_path: string | null;
  /** 手工打开的盘阵场景（POST /api/scenes/resolve，不在 SR_SCENES_ROOT 之下）。
   *  这类行不进场景库表格，只进查看器；库行的 id 语义不受影响。 */
  manual?: boolean;
  /** 源是显示件 jpg、且同目录配着同名栅格时才有（其余行恒 null / 缺席）。
   *
   *  **它是「要不要改从栅格生成」的依据，不是这一行自己的预览** —— 行自己的
   *  `hasPreview/jpgUrl/previewDiv` 三个字段的语义不受它影响（那张 jpg 仍是这一行的
   *  显示源声明）。见 rasterPreviewWins。 */
  rasterPreview?: RasterPreview | null;
  /** **只活在前端这一次会话**：试过打开这一行，而盘阵静态那份文件取不到了
   *  （nginx 直出的 `/disk-array/…` 回 404，见 lib/api.ts 的 isSceneGone）。
   *  列表是「上次检索」那一刻的快照，盘阵上的数据却会被自动清理，于是行还在、
   *  文件已经没了 —— 撞过一次就记下来，那一行的「打开」改成不可点的灰色
   *  「已自动清除」（ScenesPage）。
   *
   *  判据只有这一条：**只认静态链上撞的 404**。不按年龄推定（老景 + 没预览 + 超 3 天
   *  不算已清除 —— 没有预览恰恰是还没被打开过的新景），**后端给的 404 也不算**：
   *  那说明不了盘上的文件在不在（见 isProxyMiss）。
   *
   *  后端列表不带这个字段：它是「试过一次、撞上了」的观测，不是盘上的事实
   *  （盘上只剩「取不到」）。重新检索会连行一起换掉，那时这个标记自然消失 ——
   *  文件真没了，它不会再出现在列表里。 */
  purged?: boolean;
}

export interface SceneListResponse {
  source: 'disk' | 'fake';
  scanned: number;
  count: number;
  results: SceneRow[];
}

/** 该行是不是「显示就绪图」源（盘阵 .jpg/.jpeg，最小原型 §4.7）。
 *
 * 这类行的 jpgUrl 指向源文件本身、hasPreview 恒 true —— 没有「生成预览」这一步，
 * 后端也确实不为它们落 `<basename>_preview.jpg` 缓存。仅用于列表文案（「已生成」
 * 对一张本来就是 JPG 的场景是误导）；**是否列出**由后端
 * scene_search.is_scene_file 决定，前端不参与筛选。
 */
export function isImageSource(row: Pick<SceneRow, 'name' | 'rel'>): boolean {
  const path = row.rel || row.name || '';
  return /\.jpe?g$/i.test(path);
}

/* ---------------- 检索 URL / query 拼装 ---------------- */
export interface SceneQueryParams {
  query?: string;
  satellite?: string | null;
  sensor?: string | null;
  dateFrom?: string | null;
  dateTo?: string | null;
  limit?: number;
}

/** 拼 /api/scenes 的 query 串（空/空串参数剔除；镜像后端参数名）。 */
export function scenesQuery(p: SceneQueryParams): string {
  const parts: string[] = [];
  const push = (k: string, v: string | number | null | undefined) => {
    if (v === null || v === undefined) return;
    const s = String(v).trim();
    if (s === '') return;
    parts.push(`${encodeURIComponent(k)}=${encodeURIComponent(s)}`);
  };
  push('query', p.query);
  push('satellite', p.satellite);
  push('sensor', p.sensor);
  push('dateFrom', p.dateFrom);
  push('dateTo', p.dateTo);
  if (p.limit) push('limit', p.limit);
  return parts.length ? '?' + parts.join('&') : '';
}

/** 场景列表 URL（cfg.apiBase + /api/scenes + query）。 */
export function scenesListUrl(cfg: SrConfig, p: SceneQueryParams): string {
  return joinBase(cfg.apiBase, '/api/scenes' + scenesQuery(p));
}

/* ---------------- 手工场景路径（盘阵任意合法场景目录） ----------------
 * 这一组是**预填/展示**用的镜像，不是权威：唯一权威在服务端
 * （backend/pathguard.py 的模板、命名规则与白名单）。前端**不自拼候选路径** ——
 * 反推由后端做（POST /api/scenes/resolve 收 `{name}`，日期也由它从文件名里取），
 * 前端只发裸文件名。后端算出的候选要 stat 一次才算数；不准时报错让用户手粘目录，
 * 绝不静默提交。
 */

/** 盘符映射的默认值（与 backend/pathguard.py 的 SR_DRIVE_MAP 默认一致）。 */
export const WIN_DRIVE = 'W:';

/** 当天场景目录前缀（Windows 形态），按**客户端本地日期**生成。
 *
 * 用户描述的真机布局：`W:\GSHC2IMPS\PRODUCT\<年>\<月>\<日>`，其余目录都是
 * 当天的数据。月份/日期补零，与服务端 `{y}/{m}/{d}` 模板一致。 */
export function todayScenePrefix(now: Date = new Date(),
                                 drive: string = WIN_DRIVE): string {
  const y = String(now.getFullYear());
  const m = String(now.getMonth() + 1).padStart(2, '0');
  const d = String(now.getDate()).padStart(2, '0');
  return `${drive}\\GSHC2IMPS\\PRODUCT\\${y}\\${m}\\${d}`;
}

/** 解决端点 URL：POST /api/scenes/resolve。 */
export function sceneResolveUrl(cfg: SrConfig): string {
  return joinBase(cfg.apiBase, '/api/scenes/resolve');
}

/** 懒生成预览端点 URL：GET /api/scenes/{id}/preview?div=N[&force=1] → JPEG 字节。
 *
 * 这是**长期**缓存那条链（写源同目录或 SR_PREVIEWS_ROOT 镜像树）。只有场景库
 * 与粘路径的入口该用它；拖入入口走 `dropPreviewUrl`，别混。
 *
 * `force` = 服务端**跳过缓存的全部判据**、无条件重读源图重生成一遍（原地覆盖同一份）。
 * 只有「一键解析」传它，理由见 `fetchSceneJpg` 的 force 参数。 */
export function scenePreviewUrl(cfg: SrConfig, id: string,
                               div: number = DEFAULT_PREVIEW_DIV,
                               force = false): string {
  const q = `?div=${div}` + (force ? '&force=1' : '');
  return joinBase(cfg.apiBase,
                  `/api/scenes/${encodeURIComponent(id)}/preview${q}`);
}

/** 场景内三类图端点 URL：GET /api/scenes/{id}/siblings[?suffix=…]。
 *
 * 纯只读：回答「输入影像 / 本轮超分产物 / NOSR 各叫什么、在不在、各自的 id 是什么」。
 * 每类拿它自己的 `id` 调 `scenePreviewUrl` 就能看图 —— **三类各有自己的
 * `<stem>_preview.jpg` 落点**，所以这条端点不新增任何生成预览入口。
 * `suffix` 只在用户手动断言时给；不给由后端按「最近一条 COMPLETED 任务 → 配置缺省」
 * 的顺序定，并用响应里的 `suffixFrom` 回报用的是哪一个。 */
export function sceneSiblingsUrl(cfg: SrConfig, id: string, suffix?: string): string {
  const q = suffix ? `?suffix=${encodeURIComponent(suffix)}` : '';
  return joinBase(cfg.apiBase, `/api/scenes/${encodeURIComponent(id)}/siblings${q}`);
}

/** 拖入链的预览端点 URL：GET /api/scenes/{id}/preview-drop?div=N。
 *
 * 与 `scenePreviewUrl` 是两个端点（不是同一个 URL 的参数）：这条把产物写进**源
 * 所在的盘阵场景目录**（`<stem>_preview.jpg`），生成一次长期可用；场景目录不可写时
 * 才退回 `SR_TEMP_PREVIEWS_ROOT/<今天>/`，并回 `X-SR-Preview-Fallback: tmp`。
 * 响应带 `no-store`（URL 按 id 稳定、内容随档位变），浏览器不会拿旧档位的缓存糊弄。 */
export function dropPreviewUrl(cfg: SrConfig, id: string,
                              div: number = DEFAULT_PREVIEW_DIV): string {
  return joinBase(cfg.apiBase,
                  `/api/scenes/${encodeURIComponent(id)}/preview-drop?div=${div}`);
}

/** 这条 jpgUrl 指的是不是**平台生成的的预览**（而不是源本身就是显示件）。
 *
 * 决定两件事，都是必须的：静态 URL 要不要拼 `?div=`；换档位后要不要重新生成。
 * `<stem>_preview.jpg` 是 `paths.preview_jpg_name` 的产物名，盘阵里显示就绪的源
 * `.jpg` 不含它 —— 档位对后者毫无意义（后端也确实不为它们生成）。
 *
 * 点号那代（`<stem>.preview.jpg`）与下划线那代一并认下：两代「这是平台生成的」这条
 * 语义相同，而静态 URL 是后端给的，两边版本错开一档时认得出比认不出安全（认不出就会
 * 把 `?div=` 吞掉，换档位后最长一小时看到旧图）。 */
export function isBakedPreviewUrl(jpgUrl: string | null): boolean {
  return !!jpgUrl && /[_\.]preview\.jpe?g$/i.test(jpgUrl);
}

/** 已生成 JPG 的静态 URL（cfg.staticBase 前缀 + 后端相对 /disk-array/…）。
 *
 * `div` 只对**生成的的**预览拼（见 `isBakedPreviewUrl`）：它在这里的作用是击穿
 * nginx 那条 `max-age=3600`（deploy/nginx.conf §场景静态），否则换完档位最长一小时
 * 还会看到旧图。源本身就是 JPEG 的行拼了没意义，反而会打红 e2e 里逐字断言的 URL。 */
export function sceneImageUrl(cfg: SrConfig, jpgUrl: string | null,
                             div?: number): string {
  if (!jpgUrl) return '';
  const base = joinBase(cfg.staticBase, jpgUrl);
  if (div === undefined || !isBakedPreviewUrl(jpgUrl)) return base;
  return `${base}${base.includes('?') ? '&' : '?'}div=${div}`;
}

/** 这条 URL 打的是不是 nginx 直出的盘阵静态链（`/disk-array/…`）。
 *
 *  用来分派 404 的两种含义（见 lib/api.ts 的 isSceneGone / isProxyMiss）：
 *  静态链上的 404 是「这棵树里没有这个文件」（nginx 在 root/alias 下真找过），
 *  `/api/…` 上的 404 是别人给的答复 —— 两者能得出的结论完全不同。
 *
 *  staticBase 可能是空（同源，URL 就是 `/disk-array/…`），也可能是绝对源
 *  （e2e / 异源部署注入 `http://host[:port]`），所以先剥掉查询串与协议+主机，
 *  再比路径前缀。 */
export function isDiskArrayUrl(url: string): boolean {
  const s = String(url ?? '');
  const q = s.search(/[?#]/);
  const p = q >= 0 ? s.slice(0, q) : s;
  const proto = p.indexOf('//');
  const rest = proto >= 0 ? p.slice(proto + 2) : p;
  const slash = rest.indexOf('/');
  return slash >= 0 && rest.slice(slash).startsWith('/disk-array/');
}

/* ---------------- 预览下采样档位（全局） ----------------
 * 一个**平台级**设置：拖入 / 场景库 / 粘盘阵路径三条入口都按当前档位生成。
 * 档位值必须与后端 `preview_jpg.PREVIEW_DIVISORS` 一致 —— 那边是唯一真源，
 * 传了别的值是 400。
 */

/** 可选档位：预览长宽各为源图的 1/N。与后端 PREVIEW_DIVISORS 同序同值。 */
export const SCENE_PREVIEW_DIVS = [2, 4, 8, 16, 32] as const;

/** 产品默认档位。**只活在前端** —— 后端缺参数时是 2（旧行为，逐字节不变），
 *  那是给「旧 dist 配新 backend」留的兼容口子，不是 UI 默认值。 */
export const DEFAULT_PREVIEW_DIV = 4;

const PREVIEW_DIV_KEY = 'sr.previewDiv';

/** 读当前档位（localStorage）。读不出 / 存的值不合法 → 默认档。
 *
 * **刻意写成不依赖 Pinia 的纯函数**：调用点（如 stores/scenes.ts 的取图）
 * 会在 `useViewerStore()` 还没建过的时候用到它，放在 store 里就得先建实例。
 * `localStorage` 本身也可能不可用（隐私模式、opaque origin），一律兜住。 */
export function loadPreviewDiv(): number {
  try {
    const raw = localStorage.getItem(PREVIEW_DIV_KEY);
    const n = Number(raw);
    return (SCENE_PREVIEW_DIVS as readonly number[]).includes(n)
      ? n : DEFAULT_PREVIEW_DIV;
  } catch {
    return DEFAULT_PREVIEW_DIV;
  }
}

/** 写当前档位。写不进去（不可用）就算了 —— 只是下次回到默认档，不该抛。 */
export function savePreviewDiv(div: number): void {
  try {
    localStorage.setItem(PREVIEW_DIV_KEY, String(div));
  } catch {
    /* 忽略：档位是偏好，存不下不影响本次会话 */
  }
}

/** 档位的显示文案（工具栏读数、遮罩提示共用一处，免得两处各写各的）。 */
export function previewDivLabel(div: number): string {
  return `1/${div}`;
}

/** 预览 blob 缓存的键（见 lib/blobCache.ts 与 api.fetchSceneJpg）。
 *
 * **必须把「取的是哪一份」编进去**：同一条行 id + 同一个档位，在「同名栅格赢」成立时
 * 端上来的是**栅格**那份预览（另一张图、另一串字节），与源 jpg 那份不是一回事。
 * 只按 id+div 存，用户把档位调到栅格赢的那一档再调回去，就会拿到上一档的图 ——
 * 而且看不出来（两张都是这张场景的预览，只是清晰度不同）。
 */
export function previewCacheKey(
  row: { id: string }, div: number, raster?: { name: string } | null,
): string {
  return raster ? `${row.id}|${div}|ras:${raster.name}` : `${row.id}|${div}|jpg`;
}

/** 「同一个场景、同一个档位**正在取**的那张图」的合流键（见 viewer.fetchSceneJpgShared）。
 *
 *  与 `previewCacheKey` 那条「哪一份」的道理同源：合流的前提是两边要的是同一件事。
 *  「点开取图」不介意吃缓存、也不是非重生成不可，而「一键解析」的整条链**非要服务端
 *  重写那个文件**不可 —— 合成一条，批量那次会被在飞的「点开」顶掉，症状是用户拿到旧
 *  像素、盘上一个字节没动，而这一景在账上记的是成功。
 *
 *  抽成纯函数是为了能被单测钉住：那张在飞表在 store 里，没有测试台。
 */
export function sceneJpgInflightKey(
  row: { id: string }, div: number, force = false,
): string {
  return `${row.id}|${div}` + (force ? '|force' : '');
}

/* ---------------- 显示源比较规则：谁清晰用谁 ----------------
 * 盘阵里预生成的显示件（`PAN.jpg` / `<编号>.jpg`，长边约 8192）配着一张**同名栅格**
 * （`PAN.tif` / `<编号>.tif`）。服务端从那张栅格生成的的图**在档位够浅时**比这张 jpg
 * 更清晰，那就该用服务端那份；否则保持显示这张 jpg 本身（它就是为显示生成的）。
 *
 * 落点与栅格行**同一份**（`<源 stem>_preview.jpg`，名字只由源 stem 拼，对 jpg 与 tif
 * 是同一个文件名），所以「打开这条 jpg 行」与「打开同目录的栅格行」命中同一份缓存。
 *
 * 为什么要比较而不是一律走服务端：24000 的源配 8192 的显示件时，÷2 生成出 12000（赢）、
 * ÷4 生成出 6000（**输**）、÷8 生成出 3000（输）—— 一律走服务端会在默认档位下把图换成
 * 更糊的一张，还白等一次几十秒的解压采样。所以判据只能是这一条比较式。
 */

/** 后端 `_raster_preview` 给的「同名栅格」信息。 */
export interface RasterPreview {
  /** 栅格自己的场景 id：可直接调 `/api/scenes/{id}/preview`。 */
  id: string;
  /** 栅格文件名（如 `PAN.tif`）。 */
  name: string;
  /** 栅格在场景库根下的相对路径；库外为 null（那时没有静态 URL）。 */
  rel: string | null;
  /** 栅格那份预览的静态 URL；库内才有（库外只能吃 /preview 的响应体）。 */
  jpgUrl: string | null;
  rasterW: number;
  rasterH: number;
  /** **盘阵那张 jpg** 的尺寸（不是用户本地拖进来那份的）——见 rasterPreviewWins。 */
  jpgW: number;
  jpgH: number;
  /** 栅格那份 `<stem>_preview.jpg` 在不在盘上。 */
  hasPreview: boolean;
  /** 栅格那份预览是按哪一档生成的；null = 没有 / 旧格式戳 / 读不出。 */
  previewDiv: number | null;
}

/** 服务端从同名栅格生成的的图，是否比**这张显示件 jpg 本身**更清晰。
 *
 * 唯一判据（服务端 `?div=` 就是按它生成的，与后端 `preview_jpg.preview_max_edge` 同式）：
 *
 *     round(max(rasterW, rasterH) / div)  >  max(jpgW, jpgH)
 *
 * 严格大于：相等时不换 —— 换过去要付一次生成预览（真机几十秒），换来的清晰度一样，
 * 那就没有理由动它。
 *
 * 保守兜底：没有栅格 / 尺寸任一读不出 / div 不在 `SCENE_PREVIEW_DIVS` 里 → false，
 * 也就是继续显示那张 jpg。**绝不因为「找不到更好的」把图弄没了**。
 *
 * 判据用**盘阵那张 jpg** 的尺寸（后端读的），不是用户拖进来那份本地文件的尺寸：
 * 拖入的指纹对 jpg 行只比名字（`_fingerprint_mismatch`），用户本地那份可能是另存过的，
 * 而平台的口径是「盘阵上的才是基准」。
 */
export function rasterPreviewWins(
  rp: RasterPreview | null | undefined, div: number,
): boolean {
  if (!rp) return false;
  if (!(SCENE_PREVIEW_DIVS as readonly number[]).includes(div)) return false;
  const { rasterW, rasterH, jpgW, jpgH } = rp;
  if (!rasterW || !rasterH || !jpgW || !jpgH) return false;
  const served = Math.max(1, Math.round(Math.max(rasterW, rasterH) / div));
  return served > Math.max(jpgW, jpgH);
}

/** 取这条场景行的显示 JPG，会不会触发服务端生成。

**唯一判据**（`lib/api.ts` 的取图与 `pages/ScenesPage` 的列表文案共用它，免得
两处各判各的、列表说「已生成」而打开时又生成一轮）：
  * 源是显示件 jpg 但**同名栅格赢**（见 rasterPreviewWins）→ 按**栅格那份预览**判：
    它不在、或档位不符就会生成。注意这一支看的是 `row.rasterPreview` 的字段，
    不是行自己的 `hasPreview/previewDiv` —— 那三个说的是源 jpg。
  * 没有静态 URL（库外的手工行）→ 一定走 `/preview`，会生成；
  * `hasPreview` 为假 → 缓存不在，会生成；
  * 是**生成的的**预览、但盘上那份的档位 ≠ 当前档位 → 会生成。
    `previewDiv` 为 null 属于这一类（旧格式戳 / 读不出）：换包后首次打开每个
    场景都要重新生成一轮，这是**惰性**的、预期内的。
  * 源本身就是显示件（`.jpg`/`.jpeg`，`isBakedPreviewUrl` 为假）→ 永远不生成，
    档位对它没有意义。 */
export function previewNeedsBake(row: SceneRow, div: number): boolean {
  const rp = row.rasterPreview;
  if (rp && rasterPreviewWins(rp, div)) {
    // 栅格分支的判据与栅格行**完全一样**（别在这里抄第二套）。
    return !rp.hasPreview || rp.previewDiv !== div;
  }
  if (!row.jpgUrl) return true;
  if (!row.hasPreview) return true;
  return isBakedPreviewUrl(row.jpgUrl) && row.previewDiv !== div;
}

/** 场景 → 查看器 openSceneJpg 的最小元数据（掩码换算用 W/H；sceneId 供掩码生成）。 */
export interface SceneOpenMeta {
  name: string;
  W: number;
  H: number;
  /** 阶段4 不透明场景 id（/api/masks 用；route='jpg' rec 的 sceneId 来源）。 */
  sceneId: string;
  /** 阶段6 scene 文件父目录（= run_sr 目录语义，任务区关联 queue 行用）。 */
  lqPath: string | null;
  /** 这一行所属的场景目录（**与 lqPath 正交**：中间产物的 lqPath 为空 —— 它不可
   *  提交 —— 但仍属于本景的目录，卡片上那颗「同一景共用一个序号」的小标按它分组）。
   *  无伴随件的 .tif / 库外单张图没有场景目录，传 null。 */
  sceneDir?: string | null;
  /** 后端推导的掩码路径（手工场景才拿得到：POST /api/scenes/resolve 的
   *  resolved.mask_path）。库行没有这个字段 —— 那时前端不该猜，写掩码时后端
   *  会回权威值。 */
  serverMaskPath?: string | null;
  /** 这是场景里的哪个环节（resolve 的 resolved.kind / siblings 的 item.kind）。
   *  缺省按 `'input'` 算 —— 场景库列出的行只可能是本体（`is_scene_file` 只认
   *  目录名同名的输入件与 PAN），中间产物只能从拖拽 / 快捷芯片这两条路进来。 */
  stageKind?: StageKind;
  /** 本轮超分产物的 suffix（标签用；后端给什么写什么，前端不猜）。 */
  stageSuffix?: string | null;
}

/* ---------------- JPG 像素 → 查看器 rec 的同构数据 ---------------- */
export interface SceneDecoded {
  src: Float32Array;      // 单波段自然值（0..255，服务器已生成预览）
  tw: number;             // JPG 画布尺寸（= 预览缩略图尺寸）
  th: number;
  nbands: 1;
  invert: false;          // 灰 JPEG 无反相
  stats: BandStats[] | null;   // 固定 0..255 → 线性拉伸 = 恒等
}

/** 单个 JPG 画布 RGBA → 单波段 src + 固定 0..255 stats（n = tw*th 像素数）。 */
export function graySrcFromRgba(rgba: Uint8Array | Uint8ClampedArray, n: number): Float32Array {
  const g = new Float32Array(n);
  for (let i = 0; i < n; i++) g[i] = rgba[i * 4];   // 服务器灰 JPEG：R=G=B
  return g;
}

/** 由 JPG 画布 RGBA 构造 route='jpg' 的同构数据（stats 固定 0..255 → 线性为恒等，
    其余模式是显示层的二次拉伸，见 startStretch）。 */
export function sceneDecodePixels(
  rgba: Uint8Array | Uint8ClampedArray, tw: number, th: number,
): SceneDecoded {
  const n = tw * th;
  const src = graySrcFromRgba(rgba, n);
  const stats = computeStats(src, tw, th, 1, 0, 255);
  return { src, tw, th, nbands: 1 as const, invert: false as const, stats };
}

/* ---------------- 显示层拉伸的起手值 ---------------- */
/** 盘阵场景（route='jpg'）打开时的默认拉伸 = 直方图均衡。
    服务器生成的底图现在也是直方图均衡（改动前是 2% 线性），所以这是**再均衡一次**：
    灰度分布已近似均匀，再均衡基本是恒等映射，等于「所见即底图」。留着它是因为
    SCENE_START_STRETCH 是场景图的固定起手值（与本地图的工具栏模式解耦），换规则
    时只需要改这里一处。用户可在工具栏改，改动记在该图自己身上。 */
export const SCENE_START_STRETCH: StretchMode = 'equal';

/** 一张图**首次**绘制用什么拉伸：盘阵场景恒为 SCENE_START_STRETCH，其余路径沿用
    调用方当前的模式（本地 TIF / 本地 JPG 照旧跟随工具栏）。

    只对第一次生效 —— 画过之后以 rec.paintedMode 为准，切走再回来不会被重置，
    用户对某张图的选择也就不会被另一张图的默认值覆盖。 */
export function startStretch(
  route: string | null | undefined, current: StretchMode,
): StretchMode {
  return route === 'jpg' ? SCENE_START_STRETCH : current;
}

/* ---------------- 掩码换算（元数据 W/H 分支） ---------------- */
/** 缩略图坐标多边形 → 全分辨率坐标（thumbToOrig；scale = 元数据 W/H 而非 probe）。 */
export function thumbPolysToOrig(
  polys: Poly[], W: number, H: number, tw: number, th: number,
): Poly[] {
  return polys.map((pts) =>
    pts.map((p) => thumbToOrig(p[0], p[1], W, H, tw, th)));
}

/* ---------------- 拖 jpg 时给后端的「当前打开的这一景」 ---------------- */
/** `anchor` 的上限。它是**提示不是断言**：一个够用，多给几个只是让分屏下多一条
 *  可试的（后端逐个试、取第一个成立的，自己的上限是 4）。 */
export const ANCHOR_MAX = 3;

/** 拖进来的 jpg 名字里没有场景身份时递给后端的锚定目录（`POST /api/scenes/resolve`
 *  的 `anchor`）。RC 场景的产物 `PAN_<suffix>.jpg` 是典型：产物名按输入影像名拼，
 *  里面没有卫星段也没有成像时刻，反推不出它在哪一天哪一景的目录下 —— 唯一不猜的
 *  线索是「用户当时正开着哪一景」。
 *
 *  **顺序就是优先级**：后端取第一个「这一环节的栅格躺在同级」成立的。所以调用方按
 *  「离落点近的那一景在前」传进来。空值丢掉、重复只留一份、最多 `ANCHOR_MAX` 个。
 *
 *  返回空数组 = 没有可锚的景，后端照旧按名字反推（这条提示对能反推的名字从来不起
 *  作用，见 backend/api/app.py 的 `_anchor_stage_hit`）。 */
export function sceneAnchors(dirs: (string | null | undefined)[]): string[] {
  const out: string[] = [];
  for (const d of dirs) {
    if (d && !out.includes(d)) out.push(d);
    if (out.length >= ANCHOR_MAX) break;
  }
  return out;
}

/* ---------------- 清除预览缓存（场景库「清除选定 / 全部清除」） ----------------
 * 服务端删盘阵上那批 `<stem>_preview.jpg`（判据见 backend/services/preview_clear.py）；
 * 前端这边只有两件事要做，都在本文件与 lib/api.ts 里：
 *
 *   1. 把清掉的行从列表里摘掉（`rowsAfterClear`）—— 行还在列表里显示「已生成」就是
 *      谎话，而这些行本来也要重新检索才会回来；
 *   2. 丢掉这些场景在**本地 blob 缓存**里的那一份（lib/api.ts 的
 *      `forgetScenePreviewBlobs`）—— 不丢的话同一会话里再打开会直接命中本地旧字节，
 *      既不重新生成预览也看不到新图，用户会以为清除没生效。
 */

/** 清除端点 URL：POST /api/scenes/clear-preview（请求体 `{ids: [...]}`）。
 *
 *  **POST 而不是 DELETE**：一批 id（几十上百个）得放请求体，而 DELETE 带 body
 *  是被允许但很容易在代理链上被吃掉的形式；这本来也是「批量动作」，不是「删某一条
 *  资源」。返回 200 只表示**请求被受理** —— 逐条结论在响应体里（见 ClearResult）。 */
export function sceneClearPreviewUrl(cfg: SrConfig): string {
  return joinBase(cfg.apiBase, '/api/scenes/clear-preview');
}

/** 明细里的一条（文件名 + 原因 + 它在哪个目录）。 */
export interface ClearDetail {
  name: string;
  /** 为什么跳过 / 失败；`removed` 里的条目没有这一项。 */
  reason?: string | null;
  /** 场景目录或镜像树目录 —— 同一个文件名两处都有时靠它分清。 */
  dir?: string;
}

/** 服务端对**一个 id** 的结论。四种 status 都是正常结局，不是错误码：
 *
 *  * `cleared`  确实清了（文件删掉、或库里那条主动生成状态被标掉，或两者都有）；
 *  * `nothing`  盘上本来就没有这一景的缓存；
 *  * `skipped`  不能清：后台正在生成预览 / 是场景源 / 没有规则签名 / id 本身不合法；
 *  * `failed`   该清但没清成（权限、被占用）。
 *
 *  `cleared` 与 `nothing` 的行从列表摘掉，`skipped` / `failed` 留着 —— 用户得看见它们
 *  以及为什么（尤其是「正在生成预览，稍后再清」这种等一下就能成的）。 */
export interface ClearResult {
  id: string;
  status: 'cleared' | 'nothing' | 'skipped' | 'failed';
  /** 一句话结论（skipped / failed 时必有；cleared / nothing 可能为 null）。 */
  reason: string | null;
  /** 这一条实际处理的是哪个场景目录（同景两行会是同一个值）。 */
  dir: string | null;
  removed: ClearDetail[];
  skipped: ClearDetail[];
  failed: ClearDetail[];
  /** 库里被标成 `cleared` 的主动生成状态行数（0 = 这一景没跑过超分）。 */
  marked: number;
}

/** 汇总。前四项按**请求里的 id** 数，后三项按**去重后的场景目录**数 ——
 *  一景两行时 `cleared` 是 2 而 `dirs/files` 是 1 与实际文件数，不重复计数。 */
export interface ClearSummary {
  cleared: number;
  nothing: number;
  skipped: number;
  failed: number;
  dirs: number;
  files: number;
  marked: number;
}

export interface ClearResponse {
  results: ClearResult[];
  summary: ClearSummary;
}

/** 清除后哪些行该从列表摘掉：`cleared` 与 `nothing` 两种。
 *
 *  `nothing` 也要摘 —— 它说的是「盘上本来就没有这一景的缓存」，也就是说「已生成」
 *  那一列早就该显示「未生成」，留着这一行等于继续挂着一个错的显示。
 *
 *  纯函数、返回新数组（不就地改）：store 那层只负责把它贴回 rows。`skipped` /
 *  `failed` 的行留在列表里，用户要看得到失败与原因。 */
export function rowsAfterClear<T extends { id: string }>(
  rows: T[], results: ClearResult[],
): T[] {
  const gone = new Set<string>();
  for (const r of results) {
    if (r.status === 'cleared' || r.status === 'nothing') gone.add(r.id);
  }
  return gone.size ? rows.filter((r) => !gone.has(r.id)) : rows;
}

/** 表上方那一行汇总文案（纯函数，便于单测）。
 *
 *  「已从列表移除」的尾注只在真的有移除时给：它是这个功能最容易让人误会的一点
 *  —— 行消失了不等于场景被删了，重新检索就回来。 */
export function clearSummaryText(s: ClearSummary, removed: number): string {
  const bits: string[] = [];
  if (s.cleared) {
    bits.push(`已清除 ${s.cleared} 项（${s.dirs} 个场景目录，`
      + `${s.files} 份预览缓存）`);
  }
  if (s.nothing) bits.push(`无需清除 ${s.nothing} 项`);
  if (s.skipped) bits.push(`跳过 ${s.skipped} 项`);
  if (s.failed) bits.push(`失败 ${s.failed} 项`);
  const head = bits.length ? bits.join('，') : '没有可清除的场景';
  return removed
    ? `${head}；已从列表移除 ${removed} 行，重新检索可回来`
    : head;
}

/* ---------------- 场景库快照（跨刷新的「上次检索」） ----------------
 * **为什么要有它**：清除预览缓存删的是**盘阵上那份 JPG**，场景本身还在盘上 ——
 * 「这一景现在没有缓存」这件事，列表是靠**把行摘掉**来表达的（rowsAfterClear）。
 * 而这份「摘掉之后的列表」单靠内存活不过刷新，连 `ensureSearched` 那个「本次会话
 * 不重检索」的守卫也是纯内存态：刷新即复位 → 进页自动检索 → 行原样回来、汇总行消失，
 * 看起来就是「清除没生效」（盘上那一刻其实什么都没被撤销）。
 *
 * **存哪**：sessionStorage —— 同一标签页，含刷新；关掉标签页即失效。口径与
 * stores/scenes.ts 的「列表留在页面上，直到用户自己按检索」逐字一致，只是把「本次
 * 会话」从「这次 JS 会话」放宽到「这个标签页」。**不落 localStorage**：跨天还挂着
 * 一份旧列表比自动检索更容易骗人（盘阵上的数据会被清），新会话重新检索才是对的。
 *
 * **存什么**：整份行数组（不是 id 名单）—— 行的标签（已生成 / 未生成 / JPG 源）由行
 * 自己的字段算，换档位后要跟着重算（见 previewNeedsBake），快照不该把它焊死。另存
 * 检索时刻（页面那行「上次检索 HH:MM」）、来源与计数、**当次**筛选（镜像是哪次检索
 * 出的这批行，不是输入框里现打着的字）、以及上一次清除的结论（否则刷新后汇总行与
 * 「已从列表移除」的空态文案都无处可来）。
 *
 * **不存**：选中态（刷新后还勾着几行是个陷阱）、错误文案（那是瞬时事实，且一份出错
 * 的检索根本不该留下快照，见 store 的 listFailed）。 */

/** 快照在 sessionStorage 里的键（与 `sr.previewDiv` / `sr.viewer.qcList` 同族）。 */
export const SCENES_SNAPSHOT_KEY = 'sr.scenes.snapshot';

/** 快照格式版本。**行的形状变了就加一** —— 老快照会被 parse 直接否掉，退化成
 *  「进页自动检索」，而不是拿一份缺字段的旧行去渲染。 */
export const SCENES_SNAPSHOT_V = 1;

/** 产生这批行的筛选条件（按值留存，不是 ref）。 */
export interface SceneFilters {
  query: string;
  satellite: string;
  sensor: string;
  dateFrom: string;
  dateTo: string;
}

export interface ScenesSnapshot {
  v: number;
  /** 上一次检索的**发起**时刻（ISO；页面那行「上次检索」就是读它）。 */
  at: string;
  source: 'disk' | 'fake';
  scanned: number;
  count: number;
  rows: SceneRow[];
  /** 那次检索用的筛选（恢复时填回输入框，免得「行只剩 3 条而筛选框是空的」）。 */
  filters: SceneFilters;
  /** 上一次清除摘掉了几行 —— 空表那句「已从列表移除…」按它分岔。 */
  clearRemoved: number;
  /** 上一次清除的完整结论（汇总行 + 明细）；没清过是 null。 */
  clearResult: ClearResponse | null;
}

/** 组装一份快照；**没检索过就没有快照**（`source` 为空）→ 返回 null。
 *
 *  纯函数：store 只负责把结果塞进 sessionStorage，判据（什么时候不该存）都在这儿，
 *  好单测。 */
export function scenesSnapshotOf(s: {
  at: Date | null;
  source: '' | 'disk' | 'fake';
  scanned: number;
  count: number;
  rows: SceneRow[];
  filters: SceneFilters;
  clearRemoved: number;
  clearResult: ClearResponse | null;
}): ScenesSnapshot | null {
  if (!s.at || (s.source !== 'disk' && s.source !== 'fake')) return null;
  return {
    v: SCENES_SNAPSHOT_V,
    at: s.at.toISOString(),
    source: s.source,
    scanned: s.scanned,
    count: s.count,
    rows: s.rows,
    filters: { ...s.filters },
    clearRemoved: s.clearRemoved,
    clearResult: s.clearResult,
  };
}

/** 行能不能进快照：只认得出身份（id / name 都是非空字符串）就够。
 *
 *  字段的完整形状由**版本号**守（改 SceneRow 就加 `SCENES_SNAPSHOT_V`），这里不抄
 *  第二套校验 —— 缺了 W/H 的行渲染出来是「—」，而少认一个字段就把整份列表丢掉，
 *  代价反而更大。 */
function isSnapshotRow(v: unknown): boolean {
  if (!v || typeof v !== 'object') return false;
  const r = v as Partial<SceneRow>;
  return typeof r.id === 'string' && r.id !== ''
    && typeof r.name === 'string' && r.name !== '';
}

/** 清除结论能不能收：形状固定（results 数组 + summary 对象）就认 —— 明细里的字段
 *  在渲染处都有 `?? []` / `?? ''` 兜底，不必在这里逐个钉。 */
function isClearResponse(v: unknown): boolean {
  if (!v || typeof v !== 'object') return false;
  const c = v as Partial<ClearResponse>;
  return Array.isArray(c.results)
    && !!c.summary && typeof c.summary === 'object';
}

/** 读回快照。**任何一处不成立都返回 null**（宁可不认，也不认半个）：JSON 坏、
 *  版本不符、来源不认识、时刻读不出、计数不是有限数、行数组里混进认不出身份的东西、
 *  清除结论形状不对 —— 一律当「没有快照」，退回「进页面自动检索」这条老路。
 *
 *  返回 null 的代价只是重检索一次；认下一份半坏的数据，代价是页面上挂着说不清的
 *  行与计数。 */
export function parseScenesSnapshot(raw: string | null): ScenesSnapshot | null {
  if (!raw) return null;
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return null;
  }
  if (!parsed || typeof parsed !== 'object') return null;
  const o = parsed as Record<string, unknown>;
  if (o.v !== SCENES_SNAPSHOT_V) return null;
  if (o.source !== 'disk' && o.source !== 'fake') return null;
  if (typeof o.at !== 'string' || Number.isNaN(Date.parse(o.at))) return null;
  for (const k of ['scanned', 'count', 'clearRemoved']) {
    const n = o[k];
    if (typeof n !== 'number' || !Number.isFinite(n)) return null;
  }
  if (!Array.isArray(o.rows) || !o.rows.every(isSnapshotRow)) return null;
  if (o.clearResult != null && !isClearResponse(o.clearResult)) return null;
  const f = (o.filters ?? {}) as Partial<SceneFilters>;
  const str = (v: unknown) => (typeof v === 'string' ? v : '');
  return {
    v: SCENES_SNAPSHOT_V,
    at: o.at,
    source: o.source,
    scanned: o.scanned as number,
    count: o.count as number,
    rows: o.rows as SceneRow[],
    filters: {
      query: str(f.query), satellite: str(f.satellite), sensor: str(f.sensor),
      dateFrom: str(f.dateFrom), dateTo: str(f.dateTo),
    },
    clearRemoved: o.clearRemoved as number,
    clearResult: (o.clearResult ?? null) as ClearResponse | null,
  };
}
