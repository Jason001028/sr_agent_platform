/**
 * scene.ts — 盘阵场景（阶段4：读服务器预生成 JPG）纯函数 + 类型
 * ------------------------------------------------------------------
 * 阶段4 数据路径：浏览器不再读盘阵原始 TIF 字节，显示 = 服务器烘焙的 JPG
 * （长宽各为源图的 1/2；稀疏采样 + 直方图均衡已烘焙）。本文件只放可单测的纯函数
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
  /** 阶段6 viewer 上下文侧舱：scene 文件父目录绝对路径（= run_sr 目录语义，
   *  与 /api/queue params.lq_path 同值关联）；disk 行非空、fake 恒 null。 */
  lq_path: string | null;
  /** 手工打开的盘阵场景（POST /api/scenes/resolve，不在 SR_SCENES_ROOT 之下）。
   *  这类行不进场景库表格，只进查看器；库行的 id 语义不受影响。 */
  manual?: boolean;
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
 * 后端也确实不为它们落 <basename>.preview.jpg 缓存。仅用于列表文案（「已生成」
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
 * （backend/pathguard.py 的模板、命名规则与白名单）。前端**不再自拼候选路径** ——
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

/** 懒生成预览端点 URL：GET /api/scenes/{id}/preview → JPEG 字节。 */
export function scenePreviewUrl(cfg: SrConfig, id: string): string {
  return joinBase(cfg.apiBase, `/api/scenes/${encodeURIComponent(id)}/preview`);
}

/** 已生成 JPG 的静态 URL（cfg.staticBase 前缀 + 后端相对 /disk-array/…）。 */
export function sceneImageUrl(cfg: SrConfig, jpgUrl: string | null): string {
  return jpgUrl ? joinBase(cfg.staticBase, jpgUrl) : '';
}

/** 场景 → 查看器 openSceneJpg 的最小元数据（掩码换算用 W/H；sceneId 供掩码烘焙）。 */
export interface SceneOpenMeta {
  name: string;
  W: number;
  H: number;
  /** 阶段4 不透明场景 id（/api/masks 用；route='jpg' rec 的 sceneId 来源）。 */
  sceneId: string;
  /** 阶段6 scene 文件父目录（= run_sr 目录语义，任务区关联 queue 行用）。 */
  lqPath: string | null;
  /** 后端推导的掩码路径（手工场景才拿得到：POST /api/scenes/resolve 的
   *  resolved.mask_path）。库行没有这个字段 —— 那时前端不该猜，写掩码时后端
   *  会回权威值。 */
  serverMaskPath?: string | null;
}

/* ---------------- JPG 像素 → 查看器 rec 的同构数据 ---------------- */
export interface SceneDecoded {
  src: Float32Array;      // 单波段自然值（0..255，服务器已烘焙）
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
    服务器烤的底图现在也是直方图均衡（改动前是 2% 线性），所以这是**再均衡一次**：
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
