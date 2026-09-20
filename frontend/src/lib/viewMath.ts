/**
 * viewMath.ts — 查看器视图/坐标纯函数（tif-viewer.html 机械直译）
 * ---------------------------------------------------------------
 * 「移植不重写」：fit / 缩放 / 平移 / 像素定位 / 多边形命中的坐标数学逐函数直译，
 * 交互层（TifCanvas.vue）只负责把这些函数接到 canvas 事件上；本文件不碰 DOM。
 *
 * 坐标系约定（与 HTML 一致）：
 *   - 缩略图坐标 (tx,ty)：预览画布（rec.thumb）的像素坐标，0 ≤ tx < tw。
 *   - 屏幕坐标 (x,y)：viewCanvas 内像素坐标。
 *   - 原图坐标 (ox,oy)：原始 TIF 像素坐标（x=列、y=行）。
 *   - view = { scale, ox, oy }：缩略图 → 屏幕的仿射变换（HTML 全局 `view`）。
 */
import type { Pt, Poly } from './maskgen.js';

export interface ViewState {
  scale: number;
  ox: number;
  oy: number;
}

/* ---------------- fit（HTML `fit()`） ----------------
   把缩略图适配进画布：scale = min(cw/tw, ch/th, 1)，居中。 */
export function fitView(thumbW: number, thumbH: number, cw: number, ch: number): ViewState {
  const s = Math.min(cw / thumbW, ch / thumbH, 1);
  return { scale: s, ox: (cw - thumbW * s) / 2, oy: (ch - thumbH * s) / 2 };
}

/* 缩略图坐标 → 屏幕坐标（HTML `thumbToScreen`） */
export function thumbToScreen(view: ViewState, tx: number, ty: number): Pt {
  return [view.ox + tx * view.scale, view.oy + ty * view.scale];
}

/* 鼠标 client 坐标 → 缩略图坐标（HTML `mouseToThumb`；rect = canvas.getBoundingClientRect） */
export function mouseToThumb(
  view: ViewState, clientX: number, clientY: number, rect: { left: number; top: number },
): Pt {
  return [
    (clientX - rect.left - view.ox) / view.scale,
    (clientY - rect.top - view.oy) / view.scale,
  ];
}

/* 缩略图坐标 → 原图像素坐标（HTML `thumbToOrig`；round + clamp 到 [0,W-1]/[0,H-1]） */
export function thumbToOrig(tx: number, ty: number, W: number, H: number, tw: number, th: number): Pt {
  const ox = tw <= 1 ? 0 : tx * (W - 1) / (tw - 1);
  const oy = th <= 1 ? 0 : ty * (H - 1) / (th - 1);
  return [
    Math.max(0, Math.min(W - 1, Math.round(ox))),
    Math.max(0, Math.min(H - 1, Math.round(oy))),
  ];
}

/* 原图像素坐标 → 缩略图坐标（HTML `locatePixel` 用 `x * (t.width / W)`） */
export function origToThumb(x: number, y: number, tw: number, th: number, W: number, H: number): Pt {
  return [x * (tw / W), y * (th / H)];
}

/* 「X,Y」文本 → 两个坐标串（Vue 版新增，HTML 版没有这一条）。

   给工具栏那一个定位框用（原先是 X、Y 两个框，见 Toolbar.doLocate）：从别处拷来的坐标是
   **一对数一句话**的形态（掩码的「掩膜中心点坐标」txt 里就是 `30766.11,21862.51`），
   而 `type=number` 的框会把整串直接吞成空值。
   分隔符认半角/全角逗号与空白（从表格、日志里拷出来常常是制表符或空格），首尾空白忽略；
   带符号与小数都收。

   **认不出来一律返回 null，不做任何截断**：把「1,30766.11,21862.51」（整行掩膜记录）
   当成「1,30766.11」会把标记跳到别的地方去 —— 那比不跳更糟，调用方必须出声。 */
export function parseLocPair(raw: string): [string, string] | null {
  const parts = String(raw).trim().split(/[,，\s]+/).filter(Boolean);
  if (parts.length !== 2) return null;
  if (!parts.every((p) => /^[+-]?(\d+\.?\d*|\.\d+)$/.test(p))) return null;
  return [parts[0], parts[1]];
}

/* 像素定位后的视图（HTML `locatePixel` 数学）：
   目标 = 把缩略图坐标 (tx,ty) 居中到画布中心；scale < 1 时先放大到像素级（min scale 1）。 */
export function locateView(
  scale: number, tx: number, ty: number, cw: number, ch: number,
): ViewState {
  const ns = scale < 1 ? 1 : scale;
  return { scale: ns, ox: cw / 2 - tx * ns, oy: ch / 2 - ty * ns };
}

/* 滚轮缩放（HTML wheel 处理数学）：以鼠标屏幕坐标 (mx,my) 为锚点，factor=1.2/1/1.2，
   scale 夹在 [0.05, 64]。 */
export function wheelZoom(
  view: ViewState, mx: number, my: number, factor: number,
  minScale = 0.05, maxScale = 64,
): ViewState {
  const ns = Math.max(minScale, Math.min(maxScale, view.scale * factor));
  const ix = (mx - view.ox) / view.scale;
  const iy = (my - view.oy) / view.scale;
  return { scale: ns, ox: mx - ix * ns, oy: my - iy * ns };
}

/* ---------------- 可见缩略图像素矩形（阶段6 云量估算等按视野统计用） ---------------- */
/** 缩略图像素矩形（整数、闭区间、含端点；与 maskgen 栅格化/ROI 统计同一像素语义）。 */
export interface Rect {
  x0: number;
  y0: number;
  x1: number;
  y1: number;
}

/**
 * 当前画布可见的缩略图像素矩形：屏幕 [0,cw]×[0,ch] 经 view 反仿射 (s−ox)/scale
 * 映射回缩略图坐标，与 [0,tw]×[0,th] 求交后整数化（含端点）。视图整体在图外 → null。
 * （HTML 无此函数；scale<1 整体适配时返回整幅，调用方可按需复用整景结果。）
 */
export function visibleThumbRect(
  view: ViewState, cw: number, ch: number, tw: number, th: number,
): Rect | null {
  if (!(view.scale > 0) || cw <= 0 || ch <= 0 || tw <= 0 || th <= 0) return null;
  const s = view.scale;
  const ax = Math.min(0 - view.ox, cw - view.ox) / s;
  const bx = Math.max(0 - view.ox, cw - view.ox) / s;
  const ay = Math.min(0 - view.oy, ch - view.oy) / s;
  const by = Math.max(0 - view.oy, ch - view.oy) / s;
  if (bx <= 0 || by <= 0 || ax >= tw || ay >= th) return null;
  const x0 = Math.max(0, Math.floor(Math.max(0, ax)));
  const y0 = Math.max(0, Math.floor(Math.max(0, ay)));
  const x1 = Math.min(tw - 1, Math.max(x0, Math.ceil(Math.min(tw, bx)) - 1));
  const y1 = Math.min(th - 1, Math.max(y0, Math.ceil(Math.min(th, by)) - 1));
  if (x0 > x1 || y0 > y1) return null;
  return { x0, y0, x1, y1 };
}

/* ---------------- 图像对比：分屏两格（2026-09-20 新增） ----------------

   `PaneRect` 与上面的 `Rect` **刻意不同名不同义**：`Rect` 是**缩略图像素**的整数闭区间
   （`visibleThumbRect` 用，语义是像素），这里是**屏幕坐标**的浮点矩形（语义是视口）。
   同名同形会让 `visibleThumbRect` 的调用方读错，所以不复用。

   约定：每格的 `ViewState` 用**该格自己的局部屏幕坐标**（原点 = 该格左上角）。渲染时
   clip 到 `rect` 再 `translate(rect.x, rect.y)`，于是 `fitView` / `locateView` /
   `visibleThumbRect` / `mouseToThumb` 全部原样可用 —— 这是分屏不重写坐标数学的前提。 */

export interface PaneRect {
  x: number;
  y: number;
  w: number;
  h: number;
}

/** 分隔比例上下限（任一侧不小于 15%），以及半幅的最小像素宽。 */
export const SPLIT_RATIO_MIN = 0.15;
export const SPLIT_RATIO_MAX = 0.85;
export const SPLIT_MIN_HALF_PX = 120;

/**
 * 夹住分隔比例。窄画布时下界抬到 `SPLIT_MIN_HALF_PX / canvasW`，免得某一格窄到没法看；
 * 画布窄到两格都放不下 `SPLIT_MIN_HALF_PX` 时退回 [0.15, 0.85]（退化但可用，不抛异常）。
 * 非有限值一律当 0.5（localStorage 里可能是脏数据）。
 */
export function clampSplitRatio(r: number, canvasW: number): number {
  if (!Number.isFinite(r)) return 0.5;
  let lo = SPLIT_RATIO_MIN;
  let hi = SPLIT_RATIO_MAX;
  if (canvasW > 0) {
    const minR = SPLIT_MIN_HALF_PX / canvasW;
    if (minR > lo && minR < 0.5) lo = minR;
    if (1 - minR < hi && 1 - minR > 0.5) hi = 1 - minR;
  }
  return Math.max(lo, Math.min(hi, r));
}

/**
 * 按比例把画布切成左右两格。`a.w + b.w === cw` 精确成立（先定左格宽，右格吃剩下的），
 * 所以两格之间既无缝隙也无重叠。`cw <= 1` 时整幅给左格、右格宽 0（退化，不抛）。
 */
export function splitRects(ratio: number, cw: number, ch: number): { a: PaneRect; b: PaneRect } {
  const w = Math.max(0, Math.round(cw));
  const h = Math.max(0, Math.round(ch));
  let aw: number;
  if (w <= 1) {
    aw = w;
  } else {
    aw = Math.round(w * clampSplitRatio(ratio, w));
    aw = Math.max(1, Math.min(w - 1, aw));   // 两格都至少 1px
  }
  return {
    a: { x: 0, y: 0, w: aw, h },
    b: { x: aw, y: 0, w: w - aw, h },
  };
}

/** 画布局部 x 落在哪一格。**正好压在分隔线上算右格**（与拖放落点同一口径）。 */
export function paneAtX(localX: number, splitX: number): 'A' | 'B' {
  return localX < splitX ? 'A' : 'B';
}

/** 指针 clientX → 分隔比例（未夹；调用方过 `clampSplitRatio`）。 */
export function ratioFromPointer(clientX: number, originLeft: number, cw: number): number {
  if (!(cw > 0)) return 0.5;
  return (clientX - originLeft) / cw;
}

/** 格内点 → 归一化位置（0..1）。同步缩放靠它把「同一相对位置」搬到另一格。 */
export function normAnchor(rect: PaneRect, mx: number, my: number): { u: number; v: number } {
  return {
    u: rect.w > 0 ? (mx - rect.x) / rect.w : 0.5,
    v: rect.h > 0 ? (my - rect.y) / rect.h : 0.5,
  };
}

/** 归一化位置 → 该格**局部坐标系**里的点。`normAnchor` 的逆。
 *
 *  注意不是 PaneRect 原点的坐标系：`PaneRect` 是画布局部（右格的 x = 左格宽），
 *  而每格的 `ViewState` 是**那一格自己的**局部坐标系（渲染时 `translate(rect.x, …)`）。
 *  所以这里算的是 `u*w`，不是 `rect.x + u*w` —— 多加上那个 `rect.x` 正是本函数
 *  最早一版的错法：右格的锚点整体平移了一个左格宽，滚轮一滚图就飞出视野。 */
export function anchorAtLocal(rect: PaneRect, u: number, v: number): [number, number] {
  return [u * rect.w, v * rect.h];
}

/**
 * 一次滚轮同时缩两格：指针所在格的锚点就是指针本身，另一格用**同一个归一化位置**
 * 作锚点。两格等宽且都 `fit` 时这条规则就是逐像素锁定；分隔线不等宽时，它是
 * 「缩放到同一相对位置」，这是唯一在比例变化下还有意义的语义。
 *
 * `pPane`/`oPane` 是**指针所在格 / 另一格**，不是 A 格 / B 格 —— 光看两个 `PaneRect`
 * 分不出哪个是 A，所以 `pointerSide` 必须由调用方给；返回的 `a`/`b` 才是 A 格 / B 格。
 * 两个参数按「是不是 A」命名过一次，结果指针在右格时整体错位（`normAnchor` 拿了另一格
 * 的矩形），名字改成按角色命名就是为了不再犯。
 */
export function wheelZoomBoth(
  va: ViewState, vb: ViewState, pPane: PaneRect, oPane: PaneRect,
  pointerSide: 'A' | 'B', mx: number, my: number, factor: number,
): { a: ViewState; b: ViewState } {
  const n = normAnchor(pPane, mx, my);
  // 指针所在的那一格直接用指针的格局部坐标 —— 不走归一化往返，省一次除法一次乘法
  // （IEEE double 下 `100/1314*1314 = 99.99999999999999`）。
  const [px, py] = [mx - pPane.x, my - pPane.y];
  const [qx, qy] = anchorAtLocal(oPane, n.u, n.v);
  const onA = pointerSide === 'A';
  const [ax, ay] = onA ? [px, py] : [qx, qy];
  const [bx, by] = onA ? [qx, qy] : [px, py];
  return {
    a: wheelZoom(va, ax, ay, factor),
    b: wheelZoom(vb, bx, by, factor),
  };
}

/** 一次拖动同时平移两格（同一个屏幕位移量）。入参不改。 */
export function panBoth(
  va: ViewState, vb: ViewState, dx: number, dy: number,
): { a: ViewState; b: ViewState } {
  return {
    a: { scale: va.scale, ox: va.ox + dx, oy: va.oy + dy },
    b: { scale: vb.scale, ox: vb.ox + dx, oy: vb.oy + dy },
  };
}

/* 点与多边形包含判定（射线法，HTML `pointInPoly`，逐行直译） */
export function pointInPoly(x: number, y: number, poly: Poly): boolean {
  let inside = false;
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const xi = poly[i][0], yi = poly[i][1], xj = poly[j][0], yj = poly[j][1];
    if (((yi > y) !== (yj > y)) && (x < (xj - xi) * (y - yi) / (yj - yi) + xi)) inside = !inside;
  }
  return inside;
}

/* 命中检测（HTML `hitRoi`）：返回第一个包含点 (x,y) 的 ROI 下标，-1=无 */
export function hitRoi(x: number, y: number, rois: Poly[]): number {
  for (let i = 0; i < rois.length; i++) if (pointInPoly(x, y, rois[i])) return i;
  return -1;
}
