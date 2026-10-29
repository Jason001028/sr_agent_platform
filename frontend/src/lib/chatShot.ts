/**
 * chatShot.ts — 把「当前可见画面」裁成一张可发给 Agent 的 JPEG 截图
 * ---------------------------------------------------------------
 * 口径是**所见即所得**：用户在查看器里看到什么（缩放/平移/已拉伸的显示层），
 * 发出去的就是什么。源像素取 `rec.thumb` —— 它就是画在屏幕上的那份已被拉伸
 * 的画布，与 FileThumb 的小图同源；不另拉盘阵原图（那既多一次网络，又不是用户
 * 正在看的像素）。
 *
 * 拆成纯几何 `planShot` 与落笔 `drawShot` 两半：
 *   - 几何只吃数字（视图/视口/缩略图尺寸），node 环境里能直接单测；
 *   - 落笔才碰 canvas / document。
 *
 * 只缩不放：小窗口放大后截出来的图交给多模态模型没有额外信息，反而更糊。
 */

import { visibleThumbRect } from './viewMath.js';
import type { ViewState } from './viewMath.js';

/** 单条消息最多附几张截图。与服务端 `chat_images.MAX_IMAGES` 同值，改一处要同步另一处。 */
export const SHOT_MAX_COUNT = 2;
/** 截图长边上限（像素）。1280 够模型看清地物，又不至于把 base64 撑到几 MB。 */
export const SHOT_MAX_SIDE = 1280;
/** JPEG 质量。遥感图多是灰阶/低饱和，0.85 与更高档肉眼无差。 */
export const SHOT_JPEG_QUALITY = 0.85;

/** 源矩形（缩略图像素，闭区间）与目标尺寸。 */
export interface ShotPlan {
  sx: number;
  sy: number;
  sw: number;
  sh: number;
  dw: number;
  dh: number;
}

/**
 * 算裁剪矩形与目标尺寸。
 *
 * @param view   该格的视图；`null` / 视口尺寸为 0 表示「这张图没有上屏」→ 整幅。
 * @param cw,ch  该格在画布上的宽高（屏幕像素）。
 * @param tw,th  缩略图（显示层画布）的宽高。
 */
export function planShot(
  view: ViewState | null,
  cw: number,
  ch: number,
  tw: number,
  th: number,
  maxSide = SHOT_MAX_SIDE,
): ShotPlan | null {
  if (!(tw > 0) || !(th > 0)) return null;

  const rect = view && cw > 0 && ch > 0
    ? visibleThumbRect(view, cw, ch, tw, th)
    : null;
  // 整幅在视野外 / 压根没上屏（在 /chat 页点截图）→ 退化成整幅，
  // 这比给用户一张空白图诚实。
  const sx = rect ? rect.x0 : 0;
  const sy = rect ? rect.y0 : 0;
  const sw = rect ? Math.max(1, rect.x1 - rect.x0 + 1) : tw;
  const sh = rect ? Math.max(1, rect.y1 - rect.y0 + 1) : th;

  const scale = Math.min(1, maxSide / Math.max(sw, sh));
  return {
    sx, sy, sw, sh,
    dw: Math.max(1, Math.round(sw * scale)),
    dh: Math.max(1, Math.round(sh * scale)),
  };
}

/** 按 plan 把源画布画到离屏 canvas，返回 JPEG data URL。 */
export function drawShot(src: CanvasImageSource, plan: ShotPlan): string {
  const cv = document.createElement('canvas');
  cv.width = plan.dw;
  cv.height = plan.dh;
  const ctx = cv.getContext('2d');
  if (!ctx) throw new Error('无法创建截图画布');
  // JPEG 没有 alpha：先铺白，避免透明源（掩码叠加一类）变成黑底
  ctx.fillStyle = '#fff';
  ctx.fillRect(0, 0, plan.dw, plan.dh);
  ctx.imageSmoothingEnabled = true;
  ctx.imageSmoothingQuality = 'high';
  ctx.drawImage(src, plan.sx, plan.sy, plan.sw, plan.sh, 0, 0, plan.dw, plan.dh);
  return cv.toDataURL('image/jpeg', SHOT_JPEG_QUALITY);
}

/** 一张待发/已发的截图（`dataUrl` 同时用于预览与上传）。 */
export interface ChatShot {
  spatial?: import('./spatial').VisualInput;
  dataUrl: string;
  name: string;
}
