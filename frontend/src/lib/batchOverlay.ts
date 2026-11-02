/**
 * batchOverlay.ts — 批量检测叠加层的前端契约与坐标换算（PR D / P1 2）
 * ------------------------------------------------------------------
 * 后端 `GET /api/goals/{goal}/items/{item}/overlay` 只返回**已发布**的叠加层
 * 元数据（overlay.json）与小尺寸 overlay.png；本文件负责：
 *
 * 1. 从查看器 URL 的 `batch_goal` / `batch_item` 查询参数进入；
 * 2. 校验返回文档的契约版本与 Goal/Item 身份（防止缓存或串号）；
 * 3. 用**当前格子自己的** view 把源像素范围投影成格内 CSS 像素矩形 ——
 *    与 `spatial.project` 同一条 `origToThumb → thumbToScreen` 管线，
 *    所以缩放/平移/分屏各自独立，不会共享一份全局位置；
 * 4. 把 overlay 的失败原因收敛成稳定字符串，UI 文案在组件里映射。
 *
 * 这里不读像素、不碰 canvas：所有函数都可单测。overlay.png 只覆盖
 * 「整个源影像范围」（按 scale 最大池化降采样），因此它的屏幕矩形就是
 * 当前显示影像的完整屏幕矩形，不需要再读 overlay 的像素尺寸去反推。
 */
import { joinBase, loadSrConfig } from './scene.js';
import { origToThumb, thumbToScreen } from './viewMath.js';
import type { PaneRect, ViewState } from './viewMath.js';
import type { ImageRef, XY } from './spatial.js';

export const OVERLAY_CONTRACT_VERSION = 'overlay.v1';

export type BatchOverlayProvenance = 'mock' | 'real';

export interface BatchOverlaySource {
  image_key: string;
  revision: string;
  width: number;
  height: number;
}

export interface BatchOverlayCandidate {
  candidate_id: string;
  kind: 'bbox' | 'polygon' | string;
  bbox?: [number, number, number, number] | null;
  points?: XY[] | null;
  label?: string | null;
}

export interface BatchOverlayDoc {
  contract_version: string;
  goal_id: string;
  item_id: string;
  item_version?: number;
  item_status?: string;
  source: BatchOverlaySource;
  provenance: BatchOverlayProvenance;
  detector_name?: string | null;
  detector_version?: string | null;
  candidates: BatchOverlayCandidate[];
  overlay: { url: string; width: number; height: number; sha256?: string };
}

export interface BatchOverlayParams {
  goalId: string;
  itemId: string;
}

export type BatchOverlayResult =
  | { status: 'ready'; doc: BatchOverlayDoc }
  | { status: 'error'; reason: string };

export interface BatchOverlayFetchOptions {
  apiBase?: string;
  fetch?: typeof fetch;
  signal?: AbortSignal;
  /** 刷新时追加的缓存键（通常是 Item version）。 */
  cacheBust?: string | number;
}

/** 读取 URL 查询参数；两端任一缺失就视为「没有叠加层入口」。 */
export function parseBatchOverlayParams(search: string): BatchOverlayParams | null {
  const query = new URLSearchParams(search || '');
  const goalId = (query.get('batch_goal') || '').trim();
  const itemId = (query.get('batch_item') || '').trim();
  if (!goalId || !itemId) return null;
  return { goalId, itemId };
}

function withCacheBust(url: string, cacheBust?: string | number): string {
  if (cacheBust === undefined || cacheBust === null || cacheBust === '') return url;
  const glue = url.includes('?') ? '&' : '?';
  return url + glue + 'v=' + encodeURIComponent(String(cacheBust));
}

/** 元数据 / PNG 两条只读路径；ID 一律 encodeURIComponent，不拼接客户端路径。 */
export function batchOverlayUrls(
  params: BatchOverlayParams,
  apiBase: string = loadSrConfig().apiBase,
  cacheBust?: string | number,
): { meta: string; png: string } {
  const root = '/api/goals/' + encodeURIComponent(params.goalId)
    + '/items/' + encodeURIComponent(params.itemId) + '/overlay';
  return {
    meta: withCacheBust(joinBase(apiBase, root), cacheBust),
    png: withCacheBust(joinBase(apiBase, root + '.png'), cacheBust),
  };
}

function isFiniteNumber(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value);
}

/**
 * 校验服务端文档。返回稳定错误码而不是抛出：组件要把失败原因显示出来，
 * 测试也按原因断言（不按异常类型猜）。
 */
export function validateBatchOverlayDoc(
  value: unknown,
  params: BatchOverlayParams,
): { ok: true; doc: BatchOverlayDoc } | { ok: false; reason: string } {
  if (!value || typeof value !== 'object') return { ok: false, reason: 'invalid_doc' };
  const doc = value as Partial<BatchOverlayDoc>;
  if (doc.contract_version !== OVERLAY_CONTRACT_VERSION) {
    return { ok: false, reason: 'unsupported_contract' };
  }
  if (doc.goal_id !== params.goalId || doc.item_id !== params.itemId) {
    return { ok: false, reason: 'identity_mismatch' };
  }
  const source = doc.source as Partial<BatchOverlaySource> | undefined;
  if (!source || typeof source.image_key !== 'string' || !source.image_key
      || typeof source.revision !== 'string' || !source.revision
      || !isFiniteNumber(source.width) || !isFiniteNumber(source.height)
      || source.width <= 0 || source.height <= 0) {
    return { ok: false, reason: 'invalid_source' };
  }
  if (doc.provenance !== 'mock' && doc.provenance !== 'real') {
    return { ok: false, reason: 'invalid_provenance' };
  }
  if (!Array.isArray(doc.candidates)) return { ok: false, reason: 'invalid_candidates' };
  const overlay = doc.overlay as Partial<BatchOverlayDoc['overlay']> | undefined;
  if (!overlay || typeof overlay.url !== 'string' || !overlay.url.startsWith('/')
      || !isFiniteNumber(overlay.width) || !isFiniteNumber(overlay.height)
      || overlay.width <= 0 || overlay.height <= 0) {
    return { ok: false, reason: 'invalid_overlay' };
  }
  return { ok: true, doc: doc as BatchOverlayDoc };
}

/**
 * 身份匹配只认 `image_key`：它由服务端把**同一物理文件**的路径与修订
 * 一起哈希得到（`scene-v1:*`）。尺寸不参与匹配 —— 查看器显示的是预览 JPG，
 * 源 TIFF 的原尺寸本来就不同；overlay 覆盖的是整幅影像范围，按屏幕矩形铺开。
 */
export function overlayMatchesImage(
  doc: BatchOverlayDoc | null | undefined,
  identity: Pick<ImageRef, 'image_key'> | null | undefined,
): boolean {
  if (!doc || !identity || !identity.image_key) return false;
  return doc.source.image_key === identity.image_key;
}

/**
 * 一格里 overlay 的格内矩形：源影像范围 (0,0)-(W,H) 经该格自己的 view 投影。
 * `rect` 传 `x:0,y:0` 的格内矩形（格子容器本身已定位在 `rect.x/y`），
 * 这样分屏两格各用各的 view，位置互不影响。
 */
export function overlayPaneRect(
  image: Pick<ImageRef, 'source_width' | 'source_height'>,
  thumbWidth: number,
  thumbHeight: number,
  view: ViewState,
  rect: PaneRect,
): { left: number; top: number; width: number; height: number } {
  const [left, top] = projectSource([0, 0], image, thumbWidth, thumbHeight, view, rect);
  const [right, bottom] = projectSource(
    [image.source_width, image.source_height], image, thumbWidth, thumbHeight, view, rect);
  return { left, top, width: right - left, height: bottom - top };
}

function projectSource(
  point: XY,
  image: Pick<ImageRef, 'source_width' | 'source_height'>,
  thumbWidth: number,
  thumbHeight: number,
  view: ViewState,
  rect: PaneRect,
): XY {
  const thumb = origToThumb(
    point[0], point[1], thumbWidth, thumbHeight,
    image.source_width, image.source_height);
  const screen = thumbToScreen(view, thumb[0], thumb[1]);
  return [screen[0] + rect.x, screen[1] + rect.y];
}

/** 候选的格内顶点（闭合 bbox 四角 / 多边形顶点）；无法投影的候选返回 null。 */
export function candidateScreenPoints(
  candidate: BatchOverlayCandidate,
  image: Pick<ImageRef, 'source_width' | 'source_height'>,
  thumbWidth: number,
  thumbHeight: number,
  view: ViewState,
  rect: PaneRect,
): XY[] | null {
  const toScreen = (point: XY) => projectSource(
    point, image, thumbWidth, thumbHeight, view, rect);
  if (candidate.kind === 'bbox' && Array.isArray(candidate.bbox)
      && candidate.bbox.length === 4 && candidate.bbox.every(isFiniteNumber)) {
    const [x0, y0, x1, y1] = candidate.bbox;
    return [
      toScreen([x0, y0]), toScreen([x1, y0]),
      toScreen([x1, y1]), toScreen([x0, y1]),
    ];
  }
  if (candidate.kind === 'polygon' && Array.isArray(candidate.points)
      && candidate.points.length >= 3) {
    const points = candidate.points;
    if (!points.every((p) => Array.isArray(p) && p.length === 2 && p.every(isFiniteNumber))) {
      return null;
    }
    return points.map((p) => toScreen([p[0], p[1]]));
  }
  return null;
}

/** 组件最终给 `<img>` 的地址：服务端只给同源相对路径，跨域 apiBase 由这里补。 */
export function overlayImageUrl(
  doc: BatchOverlayDoc,
  apiBase: string = loadSrConfig().apiBase,
  cacheBust?: string | number,
): string {
  return withCacheBust(joinBase(apiBase, doc.overlay.url), cacheBust);
}

export const OVERLAY_FAILURE_TEXT: Record<string, string> = {
  aborted: '请求已取消',
  network: '网络不可达',
  invalid_json: '返回内容无法解析',
  invalid_doc: '返回内容格式异常',
  unsupported_contract: '叠加层契约版本不受支持',
  identity_mismatch: '叠加层不属于当前批次项',
  invalid_source: '叠加层缺少可绑定的源影像身份',
  invalid_provenance: '叠加层来源标记非法',
  invalid_candidates: '叠加层候选列表异常',
  invalid_overlay: '叠加层图片信息异常',
};

export function overlayFailureText(reason: string): string {
  if (OVERLAY_FAILURE_TEXT[reason]) return OVERLAY_FAILURE_TEXT[reason];
  if (reason.startsWith('http_')) return '服务端返回 ' + reason.slice(5);
  return reason || '未知错误';
}

export function provenanceLabel(provenance: BatchOverlayProvenance | string): string {
  return provenance === 'real' ? '真实检测' : 'Mock 检测 · 非真实结果';
}

/** 拉取并校验一份叠加层文档；失败一律收敛成 `{status:'error', reason}`。 */
export async function loadBatchOverlay(
  params: BatchOverlayParams,
  options: BatchOverlayFetchOptions = {},
): Promise<BatchOverlayResult> {
  const fetcher = options.fetch ?? (typeof fetch === 'function' ? fetch : undefined);
  if (!fetcher) return { status: 'error', reason: 'network' };
  const urls = batchOverlayUrls(
    params, options.apiBase ?? loadSrConfig().apiBase, options.cacheBust);
  let response: Response;
  try {
    response = await fetcher(urls.meta, {
      signal: options.signal,
      headers: { Accept: 'application/json' },
      cache: 'no-store',
    });
  } catch (exc) {
    const aborted = (exc as { name?: string } | null)?.name === 'AbortError'
      || options.signal?.aborted === true;
    return { status: 'error', reason: aborted ? 'aborted' : 'network' };
  }
  if (!response.ok) {
    return { status: 'error', reason: 'http_' + response.status };
  }
  let value: unknown;
  try {
    value = await response.json();
  } catch {
    return { status: 'error', reason: 'invalid_json' };
  }
  const checked = validateBatchOverlayDoc(value, params);
  if (!checked.ok) return { status: 'error', reason: checked.reason };
  return { status: 'ready', doc: checked.doc };
}
