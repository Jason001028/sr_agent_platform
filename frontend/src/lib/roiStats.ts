/**
 * roiStats.ts — ROI 确定性统计纯函数（阶段6 viewer 上下文侧舱 ROI/工具 tab）
 * ------------------------------------------------------------------
 * 「数据永远来自确定算法，不让 LLM 编数字」：ROI 的 n/min/max/mean/std + 亮像元/
 * 过曝占比全部在这里对「当前显示层（stretch 后 8bit RGBA 画布像素）」做确定性统计。
 *
 * 像素归属语义与掩码栅格化**完全一致**（复用 maskgen.rowIntervals）：像素 (c,r)
 * 计入 ROI ⟺ 整数点 (c,r) 在多边形内/边界上（与 rasterRows 逐像素填充同一套闭区间
 * 判定）。因此 buildStats 扫出来的像元集合 == 该多边形栅格化后落 `1` 的集合，
 * 统计口径与「提交的掩码到底覆盖了哪些显示像素」严格对齐。
 *
 * 坐标系：poly 与传入的 ImageData 同坐标系（缩略图坐标浮点值；ImageData 可为整张
 * thumb 的，也可为调用方按 ROI bbox 裁剪、poly 平移后的局部图 —— buildStats 只按
 * (c,r) 相对坐标读 data，两种都成立）。
 *
 * 性能：单次扫描、逐 ROI 仅遍历其 bbox 行 + 行内闭区间列；当 bbox 像素面积 > maxPx
 * 时自动**等距抽行**（stride 确定性），保证任何 ROI 单次统计触达像元 ≤ ~maxPx。
 * 亮度 = 加权 RGB 亮度 Y = 0.299R+0.587G+0.114B；alpha==0 的像元不计入分母。
 *
 * 阈值默认（STAT_HI/STAT_CLIP/STAT_MAX_PX）为模块常量，调用方可按 o.hi/o.clip/o.maxPx
 * 覆写 —— 前后端/组件共享同一默认，改一处即全局生效。
 */
import { rowIntervals } from './maskgen.js';
import { thumbToOrig } from './viewMath.js';
import type { Poly } from './maskgen.js';

/* ---------------- 阈值常量（可覆写） ---------------- */
/** 亮像元阈值：Y ≥ 200（显示层 8bit）。 */
export const STAT_HI = 200;
/** 过曝阈值：Y ≥ 250。 */
export const STAT_CLIP = 250;
/** 单次统计触达像元上限：bbox 像素面积超此值 → 自动等距抽行。 */
export const STAT_MAX_PX = 4_000_000;

/** 扫描闭区间列填充用的同侧 EPS（与 maskgen 栅格化同一 1e-9 语义）。 */
const SCAN_EPS = 1e-9;

/* ---------------- 类型 ---------------- */
/** 显示层 RGBA 像素的只读最小接口（ImageData 满足；测试可传结构同形对象）。 */
export interface RoiImage {
  width: number;
  height: number;
  data: Uint8ClampedArray;   // RGBA，行优先，长度 width*height*4
}

export interface RoiStatsOpts {
  /** 亮像元阈值（默认 STAT_HI）。 */
  hi?: number;
  /** 过曝阈值（默认 STAT_CLIP）。 */
  clip?: number;
  /** bbox 像素面积抽行上限（默认 STAT_MAX_PX）。 */
  maxPx?: number;
}

/** ROI 统计结果。n==0（空选区/全透明/退化多边形）时 min..std 为 null。 */
export interface RoiStats {
  /** 计入统计的像元数（alpha>0 且落在多边形内）。 */
  n: number;
  min: number | null;
  max: number | null;
  mean: number | null;
  std: number | null;
  /** 亮像元（Y ≥ hi）占 n 的百分比（0..100）。 */
  hiPct: number;
  /** 过曝（Y ≥ clip）占 n 的百分比（0..100）。 */
  clipPct: number;
  /** 是否走了自动等距抽行（bbox 像素面积 > maxPx）。 */
  sampled: boolean;
  /** 抽行步长（未抽行 = 1）。 */
  stride: number;
  /** 多边形 bbox 覆盖的行区间长（clamp 进图）。 */
  rowSpan: number;
  /** 实际参与统计且命中的行数（未抽行 = rowSpan 中命中的行数）。 */
  rowsSampled: number;
}

/** ROI 在原图像素尺度下的几何摘要（编号列表的 尺寸/估算面积）。 */
export interface RoiGeom {
  /** bbox 宽（原图像素，含端点）。 */
  w: number;
  /** bbox 高（原图像素，含端点）。 */
  h: number;
  /** 鞋带公式多边形面积（原图像素²，四舍五入）。 */
  area: number;
}

function emptyStats(sampled: boolean, stride: number, rowSpan: number): RoiStats {
  return {
    n: 0, min: null, max: null, mean: null, std: null,
    hiPct: 0, clipPct: 0, sampled, stride, rowSpan, rowsSampled: 0,
  };
}

/** 加权 RGB 亮度 Y（0..255 整数输入 → 0..255 浮点）。 */
function luma(r: number, g: number, b: number): number {
  return 0.299 * r + 0.587 * g + 0.114 * b;
}

/* ---------------- buildStats：核心统计 ---------------- */
export function buildStats(img: RoiImage, poly: Poly, o?: RoiStatsOpts): RoiStats {
  const hi = o && o.hi !== undefined ? o.hi : STAT_HI;
  const clip = o && o.clip !== undefined ? o.clip : STAT_CLIP;
  const maxPx = o && o.maxPx !== undefined ? o.maxPx : STAT_MAX_PX;
  const W = img.width, H = img.height, data = img.data;

  // 退化多边形（<3 顶点，与 maskgen 栅格化一致不填充）或空图 → 空统计
  if (poly.length < 3 || W <= 0 || H <= 0) return emptyStats(false, 1, 0);

  // 多边形 bbox（浮点顶点 → 整数行区间，clamp 进 [0,H-1]）
  let minx = Infinity, maxx = -Infinity, miny = Infinity, maxy = -Infinity;
  for (let i = 0; i < poly.length; i++) {
    const x = poly[i][0], y = poly[i][1];
    if (x < minx) minx = x;
    if (x > maxx) maxx = x;
    if (y < miny) miny = y;
    if (y > maxy) maxy = y;
  }
  const r0 = Math.max(0, Math.floor(miny));
  const r1 = Math.min(H - 1, Math.floor(maxy));
  const rowSpan = Math.max(0, r1 - r0 + 1);
  if (rowSpan <= 0) return emptyStats(false, 1, 0);

  // bbox 像素面积（列宽取 poly 水平跨度的整数覆盖）→ 是否抽行
  const bw = Math.max(1, Math.ceil(maxx) - Math.floor(minx) + 1);
  const bboxArea = rowSpan * bw;
  const sampled = bboxArea > maxPx;
  const stride = sampled ? Math.max(2, Math.ceil(bboxArea / maxPx)) : 1;

  let n = 0, sum = 0, sumSq = 0;
  let mn = Infinity, mx = -Infinity;
  let hiN = 0, clipN = 0, rowsSampled = 0;

  for (let r = r0; r <= r1; r++) {
    if (stride > 1 && ((r - r0) % stride) !== 0) continue;   // 等距抽行（确定性）
    const gaps = rowIntervals([poly], r);                    // 与掩码栅格化同源判定
    if (!gaps.length) continue;
    let hitRow = false;
    for (let g = 0; g < gaps.length; g++) {
      const c0 = Math.max(0, Math.ceil(gaps[g][0] - SCAN_EPS));
      const c1 = Math.min(W - 1, Math.floor(gaps[g][1] + SCAN_EPS));
      for (let c = c0; c <= c1; c++) {
        const o4 = (r * W + c) * 4;
        if (data[o4 + 3] === 0) continue;                    // 全透明像元不计入
        const Y = luma(data[o4], data[o4 + 1], data[o4 + 2]);
        n++;
        hitRow = true;
        if (Y < mn) mn = Y;
        if (Y > mx) mx = Y;
        sum += Y;
        sumSq += Y * Y;
        if (Y >= hi) hiN++;
        if (Y >= clip) clipN++;
      }
    }
    if (hitRow) rowsSampled++;
  }

  if (n === 0) return emptyStats(sampled, stride, rowSpan);
  const mean = sum / n;
  const variance = Math.max(0, sumSq / n - mean * mean);
  return {
    n,
    min: mn, max: mx, mean,
    std: Math.sqrt(variance),
    hiPct: (100 * hiN) / n,
    clipPct: (100 * clipN) / n,
    sampled, stride, rowSpan, rowsSampled,
  };
}

/* ---------------- roiOrigGeom：缩略图多边形 → 原图像素几何摘要 ---------------- */
function shoelaceArea(p: Poly): number {
  let a2 = 0;
  const n = p.length;
  for (let i = 0, j = n - 1; i < n; j = i++) {
    a2 += p[j][0] * p[i][1] - p[i][0] * p[j][1];
  }
  return Math.abs(a2) / 2;
}

/** 缩略图坐标 ROI → 原图像素尺度几何摘要（尺寸/估算面积，与掩码烘焙同一换算：
    每顶点 thumbToOrig round+clamp，bbox 含端点计数，面积 = 鞋带公式）。 */
export function roiOrigGeom(
  poly: Poly, W: number, H: number, tw: number, th: number,
): RoiGeom {
  if (poly.length < 3 || W <= 0 || H <= 0 || tw <= 0 || th <= 0) {
    return { w: 0, h: 0, area: 0 };
  }
  const orig = poly.map((p) => thumbToOrig(p[0], p[1], W, H, tw, th));
  let minx = Infinity, maxx = -Infinity, miny = Infinity, maxy = -Infinity;
  for (let i = 0; i < orig.length; i++) {
    const x = orig[i][0], y = orig[i][1];
    if (x < minx) minx = x;
    if (x > maxx) maxx = x;
    if (y < miny) miny = y;
    if (y > maxy) maxy = y;
  }
  return {
    w: Math.max(0, maxx - minx + 1),
    h: Math.max(0, maxy - miny + 1),
    area: Math.round(shoelaceArea(orig)),
  };
}
