/**
 * compare.ts — 图像对比的纯判据（2026-09-20 新增）
 * ------------------------------------------------------------------
 * 「关闭 / 点选对比 / 分屏对比」三态、分隔比例、落点归属、点选清单的增删 —— 这些是
 * **判据**，不是交互。它们放在这里而不是 store 里，是因为 store（stores/viewer.ts）
 * 需要 canvas/DOM 才能跑（paintStretch / decodeJpgToCanvas），而判据值得单测。
 * 本文件不碰 DOM、不碰 Pinia，localStorage 是可选的（读不出就用默认值）。
 *
 * 分屏的几何（PaneRect / splitRects / 同步缩放）在 lib/viewMath.ts，与本文件分开：
 * 那边是坐标数学，这边是业务规则。
 */

/* ---------------- 三态 ---------------- */

export type CompareMode = 'off' | 'click' | 'split';

const MODES: readonly CompareMode[] = ['off', 'click', 'split'];

/** 规范化模式值。认不出来一律 `'off'` —— 不抛，脏值不该让查看器打不开。 */
export function parseCompareMode(v: unknown): CompareMode {
  return MODES.includes(v as CompareMode) ? (v as CompareMode) : 'off';
}

/* ---------------- 分隔比例（跨会话记住） ---------------- */

export const DEFAULT_SPLIT_RATIO = 0.5;
const CMP_RATIO_KEY = 'sr.viewer.cmpSplitRatio';

/** 解析比例字符串。非有限值 / 认不出 → 默认 0.5。
 *
 * **`null` / `undefined` / `''` 必须先判出来**：`Number(null)` 与 `Number('')` 都是 `0`，
 * 是真有限数，直接往下走会被夹成 0.15 —— 于是「没存过值」和「存了个 0」看起来一样，
 * 打开查看器时分隔线跳到最左。这两个语义要分开。
 *
 * 夹取交给 `viewMath.clampSplitRatio`（它要知道画布宽才能算窄画布下界），这里只保证
 * 返回一个**有限数**，且顺带按静态上下限粗夹一道，免得脏 localStorage 值传到别处。 */
export function parseSplitRatio(v: unknown): number {
  if (v === null || v === undefined || v === '') return DEFAULT_SPLIT_RATIO;
  const n = Number(v);
  if (!Number.isFinite(n)) return DEFAULT_SPLIT_RATIO;
  return Math.max(0.15, Math.min(0.85, n));
}

/** 读记住的比例。localStorage 不可用（隐私模式 / opaque origin）→ 默认值。 */
export function loadSplitRatio(): number {
  try {
    if (typeof localStorage === 'undefined') return DEFAULT_SPLIT_RATIO;
    const raw = localStorage.getItem(CMP_RATIO_KEY);
    return raw === null ? DEFAULT_SPLIT_RATIO : parseSplitRatio(raw);
  } catch {
    return DEFAULT_SPLIT_RATIO;
  }
}

/** 写记住的比例。**只有分隔比例跨会话** —— 对比模式本身刷新即回「关闭」，
 *  一个开着分屏的查看器在重开时出现会让人莫名其妙。 */
export function saveSplitRatio(r: number): void {
  try {
    if (typeof localStorage === 'undefined') return;
    localStorage.setItem(CMP_RATIO_KEY, String(r));
  } catch {
    /* 忽略：比例是偏好，存不下不影响本次会话 */
  }
}

/* ---------------- 对比模式后台预取（用户开关，默认关） ----------------

   进对比模式时提前把同场景另两类图的预览取到本地，切图直接命中。做成开关是因为它
   **会主动读盘阵** —— 真机上「我什么都没点，盘阵却在读」是件让人不安的事，得让用户
   自己决定。默认关：装好后行为与没有这个功能时完全一样。 */

const CMP_PREFETCH_KEY = 'sr.viewer.cmpPrefetch';

/** 读开关。**只有明确存过 `'1'` 才算开** —— 没存过、存了脏值、localStorage 不可用
 *  一律当关（与 `readCtxRailOpen` 同口径，但默认值相反：这个是「未开启」。） */
export function loadCmpPrefetch(): boolean {
  try {
    if (typeof localStorage === 'undefined') return false;
    return localStorage.getItem(CMP_PREFETCH_KEY) === '1';
  } catch {
    return false;
  }
}

/** 写开关。写不进去就算了 —— 只是下次回到默认关，不该抛。 */
export function saveCmpPrefetch(on: boolean): void {
  try {
    if (typeof localStorage === 'undefined') return;
    localStorage.setItem(CMP_PREFETCH_KEY, on ? '1' : '0');
  } catch {
    /* 忽略：开关是偏好，存不下不影响本次会话 */
  }
}

/* ---------------- 拖放落点 → 归哪一格 ---------------- */

/** 画布矩形（只用到这四边；结构上兼容 DOMRect 与 getBoundingClientRect() 的返回）。 */
export interface RectLike {
  left: number;
  top: number;
  right: number;
  bottom: number;
}

/** 落在哪一格的判据。
 *
 * - `mode === 'off'`：不问落点，返回 `null`（调用方本来也不会问 —— 关闭模式下全窗口
 *   都是拖放目标，落点不参与决策）。
 * - `mode === 'click'`：只有一块画布，落在画布内就是 `'A'`，画布外 `null`。
 * - `mode === 'split'`：`splitX` 是**画布局部**坐标的分隔线位置，正好压在线上算右格
 *   （与 `viewMath.paneAtX` 同口径）。
 *
 * 边界包含四边：拖到画布最外一像素仍算「在画布上」，这与人的预期一致。
 */
export function dropSideAt(
  clientX: number, clientY: number, rect: RectLike,
  mode: CompareMode, splitX: number,
): 'A' | 'B' | null {
  if (mode === 'off') return null;
  const inside = clientX >= rect.left && clientX <= rect.right
    && clientY >= rect.top && clientY <= rect.bottom;
  if (!inside) return null;
  if (mode === 'click') return 'A';
  return clientX - rect.left < splitX ? 'A' : 'B';
}

/* ---------------- 点选清单的成员集合（有序 id 列表） ---------------- */

/** 播种：去重、保序。进入对比模式时用当前文件列表全体做初始值。 */
export function seedCompareList(ids: number[]): number[] {
  const out: number[] = [];
  for (const id of ids) if (!out.includes(id)) out.push(id);
  return out;
}

/** 加入一条（幂等）。**新加入的排在末尾**，与文件列表的先后一致。 */
export function addCompareEntry(list: number[], id: number): number[] {
  return list.includes(id) ? list : [...list, id];
}

/** 移出一条。只动清单本身 —— 文件列表条目、像素、当前显示的那张都不归它管。 */
export function removeCompareEntry(list: number[], id: number): number[] {
  return list.filter((x) => x !== id);
}

/** 丢掉已经不在 `recs` 里的 id（关掉一张图后清单不该留着幽灵条目）。保序。 */
export function pruneCompareList(list: number[], live: number[]): number[] {
  const set = new Set(live);
  return list.filter((id) => set.has(id));
}
