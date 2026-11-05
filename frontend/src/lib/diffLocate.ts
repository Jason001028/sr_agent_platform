/**
 * diffLocate.ts — 智能差异定位的纯数据层（无 Vue / 无网络）
 * ------------------------------------------------------------------
 * 这里只放能在 Node 里单测的东西：共同分析网格、双线性采样、共享灰度范围、
 * 结果坐标 → 缩略图坐标的映射。取图、编码 PNG、发请求都在 stores/diff.ts。
 *
 * 口径
 * ----
 * * 分析网格 = 两张图**共同覆盖**的一块，最长边默认 2048；宽高比不一致直接拒绝
 *   （没有配准关系时，硬拉成同一网格只会制造假差异）。
 * * 灰度按两张图**合起来的** 1%..99% 分位归一化，绝不分别自动拉伸。
 * * 所有区域坐标都在分析网格坐标里；Viewer 的缩略图坐标由 `gridPointToThumb`
 *   换算，缩略图像素不会被当成原始 TIFF 坐标。
 */

export type DiffMethod = 'absdiff' | 'ssim';
export type DiffSensitivity = 'low' | 'standard' | 'high';
export type DiffScope = 'common' | 'visible';
export type DiffDisplay = 'heat' | 'regions' | 'overlay';
export type DiffFeather = 'off' | 'light' | 'medium';

export const ANALYSIS_MAX_EDGE = 2048;

/** 与后端 `backend/services/image_diff.py::THRESHOLD_PRESETS` 同值；
 *  前端只用它做「自动阈值」的占位提示，真正的判定永远在后端。 */
export const DIFF_THRESHOLD_PRESETS: Record<DiffMethod, Record<DiffSensitivity, number>> = {
  absdiff: { low: 0.2, standard: 0.12, high: 0.07 },
  ssim: { low: 0.26, standard: 0.15, high: 0.08 },
};

export interface DiffAdvanced {
  blur: boolean;
  openKernel: number;
  closeKernel: number;
  /** null = 用灵敏度预设值；数字 = 用户显式覆盖。 */
  threshold: number | null;
  ssimWindow: number;
  ssimGaussian: boolean;
}

export const DEFAULT_ADVANCED: DiffAdvanced = {
  blur: true,
  openKernel: 3,
  closeKernel: 5,
  threshold: null,
  ssimWindow: 7,
  ssimGaussian: false,
};

export interface DiffRegion {
  id: string;
  rank: number;
  areaPx: number;
  relativeArea: number;
  bbox: [number, number, number, number];
  centroid: [number, number];
  meanDiff: number;
  maxDiff: number;
  rankScore: number;
  contour: [number, number][];
  /** `exact` = 实际外边界；`outer` = 区域含孔洞只给外边界；
   *  `bbox` = 回退外接矩形（不得直接当精确区域加入掩码）。 */
  contourKind?: 'exact' | 'outer' | 'approx' | 'bbox';
  /** 区域内被外边界围住的孔洞数量；>0 时不能按单多边形直接写掩码。 */
  holes?: number;
  /** 已按原始分辨率重算过（只更新了该区域的统计与轮廓）。 */
  refined?: boolean;
  refinedSource?: 'source-roi';
}

export interface DiffAnalysisGrid {
  width: number;
  height: number;
  origin: [number, number];
  scaleA: [number, number];
  scaleB: [number, number];
  mode: 'preview' | 'source';
  sourceRoi?: [number, number, number, number];
  imageASource?: [number, number];
  imageBSource?: [number, number];
  /** 预览分析：完整共同网格的宽高（`width/height` 只是本次分析范围）。
   *  由前端在收结果后补写；后端不知道这两项，缺省时视为等于 `width/height`。 */
  fullWidth?: number;
  fullHeight?: number;
  /** 本次分析范围在完整共同网格里的左上角。 */
  scopeX?: number;
  scopeY?: number;
}

export interface DiffImageMeta {
  id: number | null;
  name: string | null;
  sourceWidth: number | null;
  sourceHeight: number | null;
  thumbWidth: number | null;
  thumbHeight: number | null;
  gridWidth: number;
  gridHeight: number;
}

export interface DiffParameters {
  method: DiffMethod;
  sensitivity: DiffSensitivity;
  threshold: number;
  minAreaPx: number;
  blur: boolean;
  openKernel: number;
  closeKernel: number;
  ssimWindow: number;
  ssimGaussian: boolean;
  /** 实际计算后端（`scipy` / `scipy+skimage`），用于如实标注环境差异。 */
  backend?: string;
}

export interface DiffResult {
  operation: 'difference-localization';
  method: DiffMethod;
  source: 'preview' | 'source-roi';
  imageA: DiffImageMeta;
  imageB: DiffImageMeta;
  analysisGrid: DiffAnalysisGrid;
  /** data:image/png;base64 —— 8-bit 连续差异图。 */
  differenceMap: string;
  /** 白=有效，黑=NoData；兼容未提供此字段的旧服务。 */
  validityMap?: string;
  /** data:image/png;base64 —— 面积筛选后的精确二值掩码。 */
  binaryMask: string;
  regions: DiffRegion[];
  truncated: boolean;
  coverage: { validPixels: number; gridPixels: number; validRatio: number };
  parameters: DiffParameters;
  warnings: string[];
  elapsedMs: number;
}

export interface DiffImageInput {
  id: number | null;
  name: string;
  sourceWidth: number;
  sourceHeight: number;
  thumbWidth: number;
  thumbHeight: number;
  /** 前端已经采样到共同网格的 PNG（含 NoData alpha）。 */
  grayPng?: string;
  /** 服务端可读的场景 id；与 grayPng 二选一。 */
  sceneId?: string | null;
}

export interface DiffRequest {
  method: DiffMethod;
  sensitivity: DiffSensitivity;
  minAreaPx: number;
  threshold?: number;
  blur?: boolean;
  openKernel?: number;
  closeKernel?: number;
  ssimWindow?: number;
  ssimGaussian?: boolean;
  maxEdge?: number;
  roi?: { x: number; y: number; w: number; h: number } | null;
  image_a: DiffImageInput;
  image_b: DiffImageInput;
  precision?: 'source-roi';
}

export interface GridPlan {
  width: number;
  height: number;
  /** 非空 = 两张图不能建立共同网格。 */
  refusal: string;
}

/** 共同分析网格：以 A 的宽高比为基准，长边取两者较长者的上限（不超过 2048）。
 *  宽高比差超过 2% 直接拒绝。 */
export function commonGrid(aW: number, aH: number, bW: number, bH: number): GridPlan {
  if (!(aW > 0 && aH > 0 && bW > 0 && bH > 0)) {
    return { width: 0, height: 0, refusal: '影像尺寸未知，无法建立共同分析网格' };
  }
  const ratioA = aW / aH;
  const ratioB = bW / bH;
  if (Math.abs(ratioA - ratioB) / Math.max(ratioA, ratioB) > 0.02) {
    return {
      width: 0, height: 0,
      refusal: `两张影像宽高比不一致（${ratioA.toFixed(3)} / ${ratioB.toFixed(3)}），无法建立共同分析网格`,
    };
  }
  const longEdge = Math.min(ANALYSIS_MAX_EDGE, Math.max(aW, aH, bW, bH));
  const horizontal = aW >= aH;
  let width = horizontal ? longEdge : Math.round(longEdge * ratioA);
  let height = horizontal ? Math.round(longEdge / ratioA) : longEdge;
  width = Math.max(2, width);
  height = Math.max(2, height);
  return { width, height, refusal: '' };
}

/** 点采样用的步长：长边超过 2048 时只抽一部分点统计分位，避免整幅排序。 */
export function sampleStride(width: number, height: number): number {
  return Math.max(1, Math.ceil(Math.sqrt((width * height) / 200_000)));
}

/** 双线性采样到共同网格。非有限值（NoData）输出 valid=0，灰度填 0。 */
export function sampleGray(
  src: Float32Array | null,
  sw: number,
  sh: number,
  gw: number,
  gh: number,
): { gray: Float32Array; valid: Uint8Array } {
  const out = new Float32Array(gw * gh);
  const valid = new Uint8Array(gw * gh);
  if (!src || sw < 1 || sh < 1) return { gray: out, valid };
  const sxScale = sw / gw;
  const syScale = sh / gh;
  for (let gy = 0; gy < gh; gy++) {
    const fy = (gy + 0.5) * syScale - 0.5;
    const y0 = Math.max(0, Math.min(sh - 1, Math.floor(fy)));
    const y1 = Math.min(sh - 1, y0 + 1);
    const wy = Math.max(0, Math.min(1, fy - y0));
    for (let gx = 0; gx < gw; gx++) {
      const fx = (gx + 0.5) * sxScale - 0.5;
      const x0 = Math.max(0, Math.min(sw - 1, Math.floor(fx)));
      const x1 = Math.min(sw - 1, x0 + 1);
      const wx = Math.max(0, Math.min(1, fx - x0));
      const p00 = src[y0 * sw + x0], p10 = src[y0 * sw + x1];
      const p01 = src[y1 * sw + x0], p11 = src[y1 * sw + x1];
      const i = gy * gw + gx;
      // 只要求**实际加权**的邻点有效：分数坐标正好落在整数格上时，
      // 另一侧的 NoData（wx/wy = 0）不该把这一格一起判成无效。
      const right = wx > 0, down = wy > 0;
      const top = right ? p00 + (p10 - p00) * wx : p00;
      const bottom = right ? p01 + (p11 - p01) * wx : p01;
      const value = down ? top + (bottom - top) * wy : top;
      if (!(Number.isFinite(value) && Number.isFinite(p00)
        && (!right || Number.isFinite(p10))
        && (!down || Number.isFinite(p01))
        && (!right || !down || Number.isFinite(p11)))) {
        continue;
      }
      out[i] = value;
      valid[i] = 1;
    }
  }
  return { gray: out, valid };
}

/** 两张图合起来的 1%..99% 分位（同一灰度范围）。
 *
 *  只统计**所有图都有效**的像素：量化前灰度范围一旦被某张图在非共同有效区
 *  的极端值撑开，共同覆盖区内的真实差异会被压成同一个 8-bit 值而漏检。
 *  后端按 `validA & validB` 做共同归一化，这里必须同口径。 */
export function jointRange(
  grids: readonly { gray: Float32Array; valid: Uint8Array }[],
): { lo: number; hi: number; hasData: boolean } {
  if (!grids.length) return { lo: 0, hi: 1, hasData: false };
  const values: number[] = [];
  const n = Math.min(...grids.map((g) => g.gray.length));
  const step = Math.max(1, Math.floor(n / 100_000));
  // 按「下一个共同有效像素」推进，而不是死板地取 i = 0, step, 2*step…
  // 固定步长在大网格 + 稀疏有效区时可能一个样本都取不到，不能把它当成
  // 「没有有效像素」；这里每次从当前采样点向后找最近的共同有效像素，
  // 有效区无论落在哪都会至少被采到。
  let i = 0;
  while (i < n) {
    let j = i;
    let common = false;
    for (; j < n; j++) {
      common = true;
      for (const g of grids) {
        if (!g.valid[j] || !Number.isFinite(g.gray[j])) { common = false; break; }
      }
      if (common) break;
    }
    if (!common) break;                    // 后面已经没有共同有效像素
    for (const g of grids) values.push(g.gray[j]);
    i = j + step;
  }
  if (!values.length) return { lo: 0, hi: 1, hasData: false };
  values.sort((a, b) => a - b);
  const pick = (p: number) => values[Math.min(values.length - 1, Math.max(0, Math.floor(p * (values.length - 1))))];
  let lo = pick(0.01);
  let hi = pick(0.99);
  if (!Number.isFinite(lo) || !Number.isFinite(hi) || hi - lo < 1e-6) {
    lo = values[0];
    hi = values[values.length - 1];
  }
  if (hi - lo < 1e-6) return { lo: 0, hi: 1, hasData: true };
  return { lo, hi, hasData: true };
}

/** 共享范围 → 8-bit RGBA（R=G=B=灰度，A=有效性）。 */
export function quantizeToRgba(
  gray: Float32Array, valid: Uint8Array, lo: number, hi: number,
): Uint8ClampedArray {
  const n = gray.length;
  const out = new Uint8ClampedArray(n * 4);
  const span = hi - lo;
  for (let i = 0; i < n; i++) {
    const p = i * 4;
    if (!valid[i]) continue;                 // 全透明 = NoData
    const v = Math.max(0, Math.min(1, (gray[i] - lo) / span));
    const q = Math.round(v * 255);
    out[p] = q; out[p + 1] = q; out[p + 2] = q; out[p + 3] = 255;
  }
  return out;
}

/** 分析网格坐标 → 该影像缩略图坐标（归一化对应，不把网格像素当原图坐标）。 */
export function gridPointToThumb(
  result: Pick<DiffResult, 'analysisGrid' | 'imageA' | 'imageB'>,
  x: number,
  y: number,
  thumbWidth: number,
  thumbHeight: number,
): [number, number] {
  const grid = result.analysisGrid;
  let nx: number;
  let ny: number;
  if (grid.mode === 'source' && grid.sourceRoi && grid.imageASource) {
    const [rx, ry, rw, rh] = grid.sourceRoi;
    const [aw, ah] = grid.imageASource;
    nx = (rx + (x / grid.width) * rw) / aw;
    ny = (ry + (y / grid.height) * rh) / ah;
  } else {
    const fullW = grid.fullWidth ?? grid.width;
    const fullH = grid.fullHeight ?? grid.height;
    nx = ((grid.scopeX ?? 0) + x) / fullW;
    ny = ((grid.scopeY ?? 0) + y) / fullH;
  }
  return [nx * thumbWidth, ny * thumbHeight];
}

/** 网格坐标矩形 → 缩略图矩形。 */
export function gridRectToThumb(
  result: Pick<DiffResult, 'analysisGrid' | 'imageA' | 'imageB'>,
  rect: [number, number, number, number],
  thumbWidth: number,
  thumbHeight: number,
): [number, number, number, number] {
  const [x, y, w, h] = rect;
  const a = gridPointToThumb(result, x, y, thumbWidth, thumbHeight);
  const b = gridPointToThumb(result, x + w, y + h, thumbWidth, thumbHeight);
  return [Math.min(a[0], b[0]), Math.min(a[1], b[1]),
    Math.abs(b[0] - a[0]), Math.abs(b[1] - a[1])];
}

/** 缩略图矩形 → 分析网格坐标反向换算（加入掩码 / 精算 ROI 用）。 */
export function thumbRectToGrid(
  result: Pick<DiffResult, 'analysisGrid' | 'imageA' | 'imageB'>,
  rect: [number, number, number, number],
  thumbWidth: number,
  thumbHeight: number,
): [number, number, number, number] {
  const grid = result.analysisGrid;
  const [x, y, w, h] = rect;
  const nx0 = x / thumbWidth, ny0 = y / thumbHeight;
  const nx1 = (x + w) / thumbWidth, ny1 = (y + h) / thumbHeight;
  if (grid.mode === 'source' && grid.sourceRoi && grid.imageASource) {
    const [rx, ry, rw, rh] = grid.sourceRoi;
    const [aw, ah] = grid.imageASource;
    return [
      ((nx0 * aw - rx) / rw) * grid.width,
      ((ny0 * ah - ry) / rh) * grid.height,
      ((nx1 * aw - rx) / rw - (nx0 * aw - rx) / rw) * grid.width,
      ((ny1 * ah - ry) / rh - (ny0 * ah - ry) / rh) * grid.height,
    ];
  }
  const fullW = grid.fullWidth ?? grid.width;
  const fullH = grid.fullHeight ?? grid.height;
  const ox = grid.scopeX ?? 0;
  const oy = grid.scopeY ?? 0;
  return [nx0 * fullW - ox, ny0 * fullH - oy,
    (nx1 - nx0) * fullW, (ny1 - ny0) * fullH];
}

/** 分析网格矩形 → 该影像缩略图上的覆盖矩形（热力图 / 羽化掩码贴图用）。
 *  预览模式覆盖的是本次分析范围；原始 ROI 模式覆盖的是 ROI。 */
export function gridCoverageToThumb(
  result: Pick<DiffResult, 'analysisGrid'>,
  thumbWidth: number,
  thumbHeight: number,
): [number, number, number, number] {
  const grid = result.analysisGrid;
  if (grid.mode === 'source' && grid.sourceRoi && grid.imageASource) {
    const [rx, ry, rw, rh] = grid.sourceRoi;
    const [aw, ah] = grid.imageASource;
    return [rx / aw * thumbWidth, ry / ah * thumbHeight,
      rw / aw * thumbWidth, rh / ah * thumbHeight];
  }
  const fullW = grid.fullWidth ?? grid.width;
  const fullH = grid.fullHeight ?? grid.height;
  return [((grid.scopeX ?? 0) / fullW) * thumbWidth,
    ((grid.scopeY ?? 0) / fullH) * thumbHeight,
    (grid.width / fullW) * thumbWidth, (grid.height / fullH) * thumbHeight];
}

/** 分析网格矩形 → 影像 A 的原始像素坐标（精算 ROI 用）。
 *  预览模式按完整共同网格换算；原始 ROI 模式在 ROI 内换算。 */
export function gridRectToSource(
  result: Pick<DiffResult, 'analysisGrid' | 'imageA'>,
  rect: [number, number, number, number],
): [number, number, number, number] {
  const grid = result.analysisGrid;
  const [x, y, w, h] = rect;
  if (grid.mode === 'source' && grid.sourceRoi) {
    const [rx, ry, rw, rh] = grid.sourceRoi;
    return [rx + (x / grid.width) * rw, ry + (y / grid.height) * rh,
      (w / grid.width) * rw, (h / grid.height) * rh];
  }
  const fullW = grid.fullWidth ?? grid.width;
  const fullH = grid.fullHeight ?? grid.height;
  const aw = result.imageA?.sourceWidth ?? fullW;
  const ah = result.imageA?.sourceHeight ?? fullH;
  return [((grid.scopeX ?? 0) + x) / fullW * aw,
    ((grid.scopeY ?? 0) + y) / fullH * ah,
    w / fullW * aw, h / fullH * ah];
}

/** 区域矩形按比例外扩（精算 ROI 留边），并夹到网格范围内。 */
export function expandGridRect(
  rect: [number, number, number, number],
  margin: number,
  width: number,
  height: number,
): { x: number; y: number; w: number; h: number } {
  const [x, y, w, h] = rect;
  const pad = Math.round(Math.max(w, h) * margin);
  const x0 = Math.max(0, Math.floor(x - pad));
  const y0 = Math.max(0, Math.floor(y - pad));
  const x1 = Math.min(width, Math.ceil(x + w + pad));
  const y1 = Math.min(height, Math.ceil(y + h + pad));
  return { x: x0, y: y0, w: Math.max(1, x1 - x0), h: Math.max(1, y1 - y0) };
}
