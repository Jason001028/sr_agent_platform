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
