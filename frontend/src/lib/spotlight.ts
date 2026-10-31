/**
 * lib/spotlight.ts — 左侧图组聚焦遮罩（Spotlight Focus）的纯函数与令牌
 * ------------------------------------------------------------------
 * 这一层只做**几何与取值**，不碰 DOM、不碰 store：
 * - 遮罩模式三档 + localStorage 持久化（右上角设置与组件共用同一套取值）；
 * - 把选中图组的若干张卡片合成「镂空窗口」：先按竖向相邻合并，再外扩给描边留位；
 * - 把「整块遮罩 - 若干镂空」拆成互不重叠的矩形块（四块法的一般化）。
 *
 * 为什么用矩形块而不是 clip-path / mask：毛玻璃靠 `backdrop-filter`，镂空必须让
 * 那一块**完全不画**，否则模糊会渗进窗口边缘。四块拆分保证窗口内没有任何元素，
 * 且块与块共用同一条边（不重叠就不会叠出更深的遮罩）。
 *
 * 坐标口径：调用方一律传**同一坐标空间**的矩形（组件里是遮罩层局部坐标），
 * 这里不关心页面坐标、滚动坐标或视口坐标的换算。
 */

export type SpotlightMode = 'frosted' | 'dim' | 'none';

export const SPOTLIGHT_MODE_KEY = 'sr.viewer.spotlightMode';

/** 三档的界面文案。**顺序就是设置里的排列顺序**（默认第一档）。 */
export const SPOTLIGHT_MODE_OPTIONS: readonly {
  value: SpotlightMode; label: string; hint: string;
}[] = [
  { value: 'frosted', label: '柔焦毛玻璃', hint: '雾白遮罩 + 3px 背景模糊（默认）' },
  { value: 'dim', label: '柔和暗化', hint: '深松绿半透明遮罩，不做模糊' },
  { value: 'none', label: '无遮罩', hint: '不铺遮罩，只保留选中图组的描边' },
];

/** 聚焦窗口移动时长（与 style.css 的 `--motion-spotlight` 同值，测试钉住）。 */
export const SPOTLIGHT_MOVE_MS = 250;
/** 遮罩整体渐入渐出时长（与 `--motion-spotlight-fade` 同值）。 */
export const SPOTLIGHT_FADE_MS = 240;
/** 镂空窗口相对图组外扩的像素：给 1.5px 描边留位置，也别压住卡片的圆角。 */
export const SPOTLIGHT_PAD = 4;

export interface SpotlightRect {
  x: number;
  y: number;
  w: number;
  h: number;
}

export function isSpotlightMode(v: unknown): v is SpotlightMode {
  return v === 'frosted' || v === 'dim' || v === 'none';
}

/** 读持久化的模式；没存过 / 存坏了都回默认的柔焦毛玻璃。 */
export function readSpotlightMode(): SpotlightMode {
  try {
    const saved = localStorage.getItem(SPOTLIGHT_MODE_KEY);
    return isSpotlightMode(saved) ? saved : 'frosted';
  } catch {
    return 'frosted';
  }
}

export function saveSpotlightMode(mode: SpotlightMode): void {
  try { localStorage.setItem(SPOTLIGHT_MODE_KEY, mode); } catch { /* 存不下不影响本次会话 */ }
}

/** 两张卡片矩形在水平方向有没有交叠（同一列的上下卡）。留 0.5px 容差。 */
function overlapX(a: SpotlightRect, b: SpotlightRect): boolean {
  return a.x < b.x + b.w + 0.5 && b.x < a.x + a.w + 0.5;
}

/**
 * 把选中图组的若干张卡片合成镂空窗口。
 *
 * 卡片在左侧列表里是**单列**排布：同景两张卡之间只隔 6px，中间夹着别的卡时距离
 * 会明显拉开。所以竖向间距 ≤ 2×pad 的卡视为同一段连续区。收藏会把同景的卡拆到
 * 列表两端——那时返回**两个**窗口，而不是一个横跨无关卡片的大窗口。
 *
 * 返回的矩形已经外扩 `pad`，尚未裁剪到视口。
 */
export function mergeGroupRects(rects: readonly SpotlightRect[], pad = SPOTLIGHT_PAD): SpotlightRect[] {
  const sorted = rects
    .filter((r) => r.w > 0 && r.h > 0)
    .slice()
    .sort((a, b) => a.y - b.y || a.x - b.x);
  const merged: SpotlightRect[] = [];
  for (const r of sorted) {
    const last = merged[merged.length - 1];
    if (last && r.y <= last.y + last.h + pad * 2 && overlapX(r, last)) {
      const x = Math.min(last.x, r.x);
      const y = Math.min(last.y, r.y);
      const right = Math.max(last.x + last.w, r.x + r.w);
      const bottom = Math.max(last.y + last.h, r.y + r.h);
      merged[merged.length - 1] = { x, y, w: right - x, h: bottom - y };
    } else {
      merged.push({ ...r });
    }
  }
  return merged.map((r) => ({
    x: r.x - pad, y: r.y - pad, w: r.w + pad * 2, h: r.h + pad * 2,
  }));
}

function uniqSorted(values: number[]): number[] {
  const out = values.slice().sort((a, b) => a - b);
  const uniq: number[] = [];
  for (const v of out) {
    if (!uniq.length || Math.abs(v - uniq[uniq.length - 1]) > 0.01) uniq.push(v);
  }
  return uniq;
}

/** 一个单元格是否完全落在某个镂空窗口里（窗口边界来自同一组 xs/ys，故不会半覆盖）。 */
function covered(x: number, y: number, w: number, h: number, holes: readonly SpotlightRect[]): boolean {
  return holes.some((q) =>
    x >= q.x - 0.01 && x + w <= q.x + q.w + 0.01
    && y >= q.y - 0.01 && y + h <= q.y + q.h + 0.01);
}

/**
 * 把「整块遮罩 − 若干镂空」拆成互不重叠的矩形块。
 *
 * 做法：把所有横边、竖边切成网格，逐行（y 带）收集未被镂空覆盖的连续 x 段，
 * 最后把上下相邻、x/宽完全相同的两段并成一块。单个不贴边的窗口正好得到
 * 上 / 左 / 右 / 下四块——与规格里的「四块无缝遮罩」逐字一致；两个窗口
 * （收藏把同景拆开）也能正确分块。
 */
export function buildMaskBlocks(
  width: number,
  height: number,
  holes: readonly SpotlightRect[],
): SpotlightRect[] {
  if (!(width > 0) || !(height > 0)) return [];
  const xs = uniqSorted([0, width, ...holes.flatMap((h) => [h.x, h.x + h.w])])
    .map((v) => Math.min(width, Math.max(0, v)));
  const ys = uniqSorted([0, height, ...holes.flatMap((h) => [h.y, h.y + h.h])])
    .map((v) => Math.min(height, Math.max(0, v)));

  const rows: SpotlightRect[] = [];
  for (let j = 0; j < ys.length - 1; j++) {
    const y = ys[j];
    const rh = ys[j + 1] - y;
    if (rh <= 0.01) continue;
    let runX: number | null = null;
    for (let i = 0; i < xs.length - 1; i++) {
      const x = xs[i];
      const rw = xs[i + 1] - x;
      if (rw <= 0.01) continue;
      if (covered(x, y, rw, rh, holes)) {
        if (runX !== null) { rows.push({ x: runX, y, w: x - runX, h: rh }); runX = null; }
      } else if (runX === null) {
        runX = x;
      }
    }
    if (runX !== null) rows.push({ x: runX, y, w: width - runX, h: rh });
  }

  // 上下相邻的等宽段并成一块：单窗口的上 / 下两条整宽遮罩不会碎成多段。
  const blocks: SpotlightRect[] = [];
  for (const r of rows.sort((a, b) => a.x - b.x || a.y - b.y)) {
    const last = blocks[blocks.length - 1];
    if (last && Math.abs(last.x - r.x) < 0.01 && Math.abs(last.w - r.w) < 0.01
        && Math.abs(last.y + last.h - r.y) < 0.01) {
      last.h += r.h;
    } else {
      blocks.push({ ...r });
    }
  }
  return blocks;
}

export function lerpRect(from: SpotlightRect, to: SpotlightRect, t: number): SpotlightRect {
  return {
    x: from.x + (to.x - from.x) * t,
    y: from.y + (to.y - from.y) * t,
    w: from.w + (to.w - from.w) * t,
    h: from.h + (to.h - from.h) * t,
  };
}

/* ---------------- cubic-bezier(0.22, 1, 0.36, 1) ----------------
   CSS 的缓动进不了 rAF：聚焦窗口的移动由 JS 插值（滚动时要跟手，不能让
   250ms 的 transition 每帧重新起跑）。这里按控制点求 y(x)，x 用二分反解。 */
const BEZIER_X1 = 0.22;
const BEZIER_Y1 = 1;
const BEZIER_X2 = 0.36;
const BEZIER_Y2 = 1;

function bezierCoord(p1: number, p2: number, t: number): number {
  const mt = 1 - t;
  return 3 * mt * mt * t * p1 + 3 * mt * t * t * p2 + t * t * t;
}

/** 时间进度 x（0..1）→ 缓动后的位移进度 y（0..1）。 */
export function easeSpotlight(x: number): number {
  const target = Math.min(1, Math.max(0, x));
  if (target === 0 || target === 1) return target;
  let lo = 0;
  let hi = 1;
  for (let i = 0; i < 24; i++) {
    const mid = (lo + hi) / 2;
    if (bezierCoord(BEZIER_X1, BEZIER_X2, mid) < target) lo = mid;
    else hi = mid;
  }
  return bezierCoord(BEZIER_Y1, BEZIER_Y2, (lo + hi) / 2);
}
