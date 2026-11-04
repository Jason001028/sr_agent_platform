/**
 * flicker.ts — 交替对比（Flicker Comparison）的纯判据
 * ------------------------------------------------------------------
 * 交替对比只做一件事：**同一块画布、同一视口，按固定节拍在两张已关联影像之间
 * 切换可见图层**。它不重新解码、不重新请求、不重建画布 —— 所以这里只放
 * 「哪两张能配、节拍是多少、当前该看谁」这类判定，渲染与计时在 stores/viewer.ts。
 *
 * 配准口径（为什么是这两条）：
 *   - **同一场景目录**：盘阵上同一个场景目录里的本体 / 产物 / NOSR 是同一景的三份
 *     影像。没有这份关联就只能靠文件名猜，猜错就是拿两张不同地面的图来回闪。
 *   - **宽高比一致**：渲染层把「整幅图」当作同一块地面（归一化坐标一一对应），
 *     只有宽高比一致时这条映射才在不缩放变形的前提下覆盖同一片区域。产物是输入
 *     的 2 倍，宽高比不变；若两张被裁过，宽高比会露馅，宁可拒绝也不闪错位。
 *   两条都满足才自动交替；否则给出**具体原因**，不让用户只看到一颗灰按钮。
 *
 * 本文件不碰 DOM、不碰 Pinia。localStorage 只用于记住节拍偏好。
 */

/** 切换节拍（毫秒）。五档，最短 0.6s —— 低于这个数会被读成高频强闪烁。 */
export const FLICKER_INTERVALS_MS: readonly number[] = [600, 800, 1000, 1200, 1500];

export const DEFAULT_FLICKER_INTERVAL_MS = 1000;

const FLICKER_INTERVAL_KEY = 'sr.viewer.flickerInterval';

/** 宽高比的相对偏差上限。2 倍产物按有理数缩放后偏差接近 0；真正被裁过的图
 *  通常远大于 1%。 */
export const FLICKER_ASPECT_TOLERANCE = 0.01;

/** 参与判定的最小影像信息。字段刻意只取判定需要的几个，避免 lib 反向依赖 store。 */
export interface FlickerImageMeta {
  id: number;
  w: number;
  h: number;
  /** 场景目录（盘阵 POSIX）。同一景的三份影像共用它；本地单张图为 null。 */
  sceneDir?: string | null;
  /** 环节：本体输入 / 本轮超分产物 / NOSR。 */
  stageKind?: 'input' | 'product' | 'nosr' | null;
  /** 卡片标签（PAN / 本体 / SR / NOSR）；没有就用文件名兜底。 */
  stageLabel?: string | null;
  name: string;
  /** 「一键解析」占位卡、还没补齐身份的，不参与配对。 */
  pending?: boolean;
}

/** 节拍解析：只认五档里的值，脏值回默认。 */
export function parseFlickerInterval(v: unknown): number {
  const n = Number(v);
  return FLICKER_INTERVALS_MS.includes(n) ? n : DEFAULT_FLICKER_INTERVAL_MS;
}

export function loadFlickerInterval(): number {
  try {
    if (typeof localStorage === 'undefined') return DEFAULT_FLICKER_INTERVAL_MS;
    return parseFlickerInterval(localStorage.getItem(FLICKER_INTERVAL_KEY));
  } catch {
    return DEFAULT_FLICKER_INTERVAL_MS;
  }
}

export function saveFlickerInterval(ms: number): void {
  try {
    if (typeof localStorage === 'undefined') return;
    localStorage.setItem(FLICKER_INTERVAL_KEY, String(parseFlickerInterval(ms)));
  } catch {
    /* 忽略：节拍是偏好，存不下不影响本次会话 */
  }
}

function ratio(w: number, h: number): number {
  return w > 0 && h > 0 ? w / h : 0;
}

/** 两张图能否按「整幅 = 同一地面」配准。能 → null；不能 → 一句给用户看的原因。 */
export function registrationRefusal(a: FlickerImageMeta, b: FlickerImageMeta): string | null {
  if (!(a.w > 0 && a.h > 0 && b.w > 0 && b.h > 0)) {
    return '两张影像的尺寸未知，无法确认同一地面范围';
  }
  const dirA = a.sceneDir ?? '';
  const dirB = b.sceneDir ?? '';
  if (!dirA || !dirB) {
    return '两张影像缺少共同的盘阵场景目录，无法确认是同一地块';
  }
  if (dirA !== dirB) {
    return '两张影像来自不同的场景目录，无法确认是同一地块';
  }
  const arA = ratio(a.w, a.h);
  const arB = ratio(b.w, b.h);
  const dev = Math.abs(arA - arB) / Math.max(arA, arB);
  if (dev > FLICKER_ASPECT_TOLERANCE) {
    return '两张影像的宽高比不一致（' + a.w + '×' + a.h + ' / ' + b.w + '×' + b.h
      + '），无法在不变形的前提下覆盖同一视口';
  }
  return null;
}

/** 给用户的短标签：优先环节标签（PAN / 本体 / SR / NOSR），没有就用文件名。 */
export function flickerLabel(m: Pick<FlickerImageMeta, 'stageLabel' | 'name'>): string {
  const s = (m.stageLabel ?? '').trim();
  if (s) return s;
  return m.name.replace(/\.(tif|tiff|img|jpg|jpeg)$/i, '');
}

/** 胶囊上的配对文字，例如 `PAN/NOSR`。 */
export function flickerPairLabel(a: FlickerImageMeta, b: FlickerImageMeta): string {
  return flickerLabel(a) + '/' + flickerLabel(b);
}

/** 从同一场景目录里挑另一张。返回 null = 没有可配的。
 *
 *  排序（越靠前越优先）：
 *   1. 能通过 `registrationRefusal` 的（宽高比一致）；
 *   2. 与当前环节互补的：看 NOSR 时优先产物，看产物时优先 NOSR，
 *      看本体时优先 NOSR（与胶囊示例 PAN/NOSR 同序），其余垫后；
 *   3. id 小的在前（稳定，不受列表重排影响）。
 *
 *  **只在同场景目录里挑** —— 跨场景配对没有可靠的地面关联，不该被自动选上。 */
export function pickFlickerPartner<T extends FlickerImageMeta>(
  active: FlickerImageMeta, candidates: readonly T[],
): T | null {
  const dir = active.sceneDir ?? '';
  if (!dir) return null;
  const pool = candidates.filter((c) =>
    c.id !== active.id && !c.pending && c.sceneDir === dir);
  if (!pool.length) return null;
  const stageRank = (c: FlickerImageMeta): number => {
    if (active.stageKind === 'nosr') return c.stageKind === 'product' ? 0 : c.stageKind === 'input' ? 1 : 2;
    if (active.stageKind === 'product') return c.stageKind === 'nosr' ? 0 : c.stageKind === 'input' ? 1 : 2;
    return c.stageKind === 'nosr' ? 0 : c.stageKind === 'product' ? 1 : c.stageKind === 'input' ? 2 : 3;
  };
  const scored = pool.map((c) => ({
    c,
    compatible: registrationRefusal(active, c) === null ? 0 : 1,
    rank: stageRank(c),
  }));
  scored.sort((x, y) =>
    x.compatible - y.compatible || x.rank - y.rank || x.c.id - y.c.id);
  return scored[0].c;
}
