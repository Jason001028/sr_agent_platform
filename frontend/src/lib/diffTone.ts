/**
 * diffTone.ts — 差异候选的「亮度归因」连续权重
 * ------------------------------------------------------------------
 * 目的不是改差异测量，而是回答「这个差异值不值得先看」：
 *
 *   * 暗部明显提亮时提高复核优先级；
 *   * 中间调平坦区额外升权，明显提亮时进一步加强；
 *
 * 这里用 softstep 隶属度把上述经验写成**连续**权重，避免 120/121 的硬切。
 * 权重有两种用法，都在显示层（见 stores/diff.ts / DiffOverlay.vue）：
 *   * 区域级：候选排序与轮廓冷暖；
 *   * 逐像素：热力图强度乘以滑动窗口权重，画面分布随之变化（见 `toneWeightMap`）。
 * `differenceMap`、`binaryMask`、区域面积与掩码资格都不受影响。
 *
 * 灰度 L 是分析网格的 0..255 显示灰度（rec.src 的自然值），不是原始 DN：
 * 盘阵预览 JPG 逐图做过直方图均衡，跨图比较绝对亮度没有意义。
 */

/** 各隶属度过渡区的两端（0..255 灰度 / 局部标准差 / 提亮量）。 */
export interface ToneBands {
  /** 中间调左斜坡 [暗→中间调]。 */
  midLo: [number, number];
  /** 中间调右斜坡 [中间调→高光]。 */
  midHi: [number, number];
  /** 暗部斜坡 [暗部→非暗部]。 */
  dark: [number, number];
  /** 平坦度斜坡 [完全平坦→有纹理]，单位是 3×3 局部标准差。 */
  flat: [number, number];
  /** 提亮斜坡 [几乎没变→明显提亮]，单位是灰度差。 */
  bright: [number, number];
  /** 中间调最高升权幅度：1 + midGain。 */
  midGain: number;
  /** 中间调明显提亮的额外增益。 */
  midBrightGain: number;
  /** 暗部明显提亮的额外增益。 */
  darkBrightGain: number;
}

export const DEFAULT_TONE_BANDS: ToneBands = {
  midLo: [90, 120],
  midHi: [180, 210],
  dark: [70, 110],
  flat: [2, 8],
  bright: [2, 15],
  midGain: 1,
  midBrightGain: 0.2,
  darkBrightGain: 0.5,
};

/** 权重理论范围，显示层据此归一化到 0..1。 */
export const TONE_WEIGHT_MIN = 1;
export const TONE_WEIGHT_MAX = 2.5;

export interface RegionToneStats {
  /** 原始侧（A）区域平均灰度 0..255。 */
  meanA: number;
  /** 对比侧（B）区域平均灰度 0..255。 */
  meanB: number;
  /** meanB − meanA：正数 = 提亮。 */
  delta: number;
  /** 中间调隶属度均值 0..1。 */
  mid: number;
  /** 暗部隶属度均值 0..1。 */
  dark: number;
  /** 原始平坦度隶属度均值 0..1（越大越平）。 */
  flat: number;
  /** 复核权重 1..2.5。 */
  weight: number;
  /** 归一化权重 0..1，供透明度 / 颜色插值。 */
  norm: number;
  /** 参与统计的有效像素数。 */
  pixels: number;
}

export interface ToneGrid {
  /** 0..255 显示灰度。预览网格是采样后的 Float32，长期保存时压成 Uint8。 */
  gray: Float32Array | Uint8Array;
  /** 0 = 无效（NoData）；缺省视为全有效。 */
  valid?: Uint8Array;
  w: number;
  h: number;
}

/** 逐像素归因权重图：norm 是 weight 在 1–2.5 区间上的 0..255 量化。 */
export interface ToneWeightMap {
  norm: Uint8Array;
  w: number;
  h: number;
  /** 实际使用的滑动窗口半径（分析网格像素）。 */
  radius: number;
}

export interface ToneRect {
  x: number;
  y: number;
  w: number;
  h: number;
}

export interface ToneSampleOptions {
  /** 区域掩码（>127 视为属于区域）；用于排除外接矩形里的背景。 */
  mask?: Uint8Array | null;
  maskWidth?: number;
  bands?: ToneBands;
}

function clamp01(v: number): number {
  return v <= 0 ? 0 : v >= 1 ? 1 : v;
}

/** 标准 smoothstep：在 [e0,e1] 上 0→1，一阶导连续。 */
export function smoothstep(e0: number, e1: number, x: number): number {
  if (e1 <= e0) return x >= e1 ? 1 : 0;
  const t = clamp01((x - e0) / (e1 - e0));
  return t * t * (3 - 2 * t);
}

export function midMembership(l: number, bands: ToneBands = DEFAULT_TONE_BANDS): number {
  return smoothstep(bands.midLo[0], bands.midLo[1], l)
    * (1 - smoothstep(bands.midHi[0], bands.midHi[1], l));
}

export function darkMembership(l: number, bands: ToneBands = DEFAULT_TONE_BANDS): number {
  return 1 - smoothstep(bands.dark[0], bands.dark[1], l);
}

export function flatMembership(sigma: number, bands: ToneBands = DEFAULT_TONE_BANDS): number {
  return 1 - smoothstep(bands.flat[0], bands.flat[1], sigma);
}

export function brightMembership(delta: number, bands: ToneBands = DEFAULT_TONE_BANDS): number {
  return smoothstep(bands.bright[0], bands.bright[1], delta);
}

/** 区域与逐像素路径共享公式，避免排序与热力图的权重方向不一致。 */
function toneWeight(mid: number, dark: number, flat: number, bright: number, bands: ToneBands): number {
  const wMid = 1 + bands.midGain * mid * (0.5 + 0.5 * flat);
  const wBright = 1 + bright * (bands.midBrightGain * mid + bands.darkBrightGain * dark);
  return Math.max(TONE_WEIGHT_MIN, Math.min(TONE_WEIGHT_MAX, wMid * wBright));
}

/** 由区域隶属度均值与提亮量算复核权重。 */
export function weightFromShares(
  mid: number,
  dark: number,
  flat: number,
  delta: number,
  bands: ToneBands = DEFAULT_TONE_BANDS,
): { weight: number; norm: number } {
  const bright = brightMembership(delta, bands);
  const weight = toneWeight(mid, dark, flat, bright, bands);
  const span = TONE_WEIGHT_MAX - TONE_WEIGHT_MIN;
  const norm = span > 0 ? clamp01((weight - TONE_WEIGHT_MIN) / span) : 0;
  return { weight, norm };
}

/** 3×3 局部标准差（只看 A 侧有效像素）。 */
function localStd(g: ToneGrid, x: number, y: number): number {
  const x0 = Math.max(0, x - 1);
  const x1 = Math.min(g.w - 1, x + 1);
  const y0 = Math.max(0, y - 1);
  const y1 = Math.min(g.h - 1, y + 1);
  let n = 0;
  let sum = 0;
  let sum2 = 0;
  for (let yy = y0; yy <= y1; yy++) {
    const row = yy * g.w;
    for (let xx = x0; xx <= x1; xx++) {
      const idx = row + xx;
      if (g.valid && !g.valid[idx]) continue;
      const v = g.gray[idx];
      if (!Number.isFinite(v)) continue;
      n++;
      sum += v;
      sum2 += v * v;
    }
  }
  if (n < 2) return 0;
  const mean = sum / n;
  return Math.sqrt(Math.max(0, sum2 / n - mean * mean));
}

function clampRect(r: ToneRect, w: number, h: number): [number, number, number, number] {
  const x0 = Math.max(0, Math.min(w, Math.floor(r.x)));
  const y0 = Math.max(0, Math.min(h, Math.floor(r.y)));
  const x1 = Math.max(x0, Math.min(w, Math.ceil(r.x + r.w)));
  const y1 = Math.max(y0, Math.min(h, Math.ceil(r.y + r.h)));
  return [x0, y0, x1, y1];
}

/**
 * 统计一个区域在 A/B 两图上的亮度归因。
 *
 * `rectB` 与 `rectA` 可以不同：原始 ROI 精算时两图的分析源分辨率未必一致，
 * 只要矩形表示同一块地理范围即可；B 侧只取均值，不做逐像素配对。
 */
export function regionTone(
  a: ToneGrid,
  b: ToneGrid,
  rectA: ToneRect,
  rectB: ToneRect = rectA,
  options: ToneSampleOptions = {},
): RegionToneStats | null {
  const bands = options.bands ?? DEFAULT_TONE_BANDS;
  const mask = options.mask ?? null;
  const maskW = options.maskWidth ?? a.w;

  const [ax0, ay0, ax1, ay1] = clampRect(rectA, a.w, a.h);
  let n = 0;
  let sumA = 0;
  let mid = 0;
  let dark = 0;
  let flat = 0;
  for (let y = ay0; y < ay1; y++) {
    const row = y * a.w;
    for (let x = ax0; x < ax1; x++) {
      const idx = row + x;
      if (a.valid && !a.valid[idx]) continue;
      if (mask && mask[y * maskW + x] < 128) continue;
      const l = a.gray[idx];
      if (!Number.isFinite(l)) continue;
      sumA += l;
      mid += midMembership(l, bands);
      dark += darkMembership(l, bands);
      flat += flatMembership(localStd(a, x, y), bands);
      n++;
    }
  }
  if (!n) return null;

  const [bx0, by0, bx1, by1] = clampRect(rectB, b.w, b.h);
  // 同一张网格（预览分析）时沿用区域掩码；精算跨分辨率时不套 A 的掩码。
  const sharedMask = mask && rectB === rectA ? mask : null;
  let bn = 0;
  let sumB = 0;
  for (let y = by0; y < by1; y++) {
    const row = y * b.w;
    for (let x = bx0; x < bx1; x++) {
      const idx = row + x;
      if (b.valid && !b.valid[idx]) continue;
      if (sharedMask && sharedMask[y * maskW + x] < 128) continue;
      const v = b.gray[idx];
      if (!Number.isFinite(v)) continue;
      sumB += v;
      bn++;
    }
  }
  if (!bn) return null;

  const meanA = sumA / n;
  const meanB = sumB / bn;
  const shares = { mid: mid / n, dark: dark / n, flat: flat / n };
  const w = weightFromShares(shares.mid, shares.dark, shares.flat, meanB - meanA, bands);
  return {
    meanA,
    meanB,
    delta: meanB - meanA,
    ...shares,
    weight: w.weight,
    norm: w.norm,
    pixels: n,
  };
}

/**
 * 动态窗口半径：按分析网格**短边**缩放，短边 1/300，夹在 2..8。
 *
 * 网格长边最大 2048，取 4:3 时短边 1536 → 半径 5；网格越小窗口越小。
 * 窗口始终在分析网格像素里滑动（与显示缩放无关），一次分析只算一次。
 */
export function toneWindowRadius(w: number, h: number): number {
  const short = Math.min(w, h);
  if (!Number.isFinite(short) || short < 1) return 2;
  return Math.max(2, Math.min(8, Math.round(short / 300)));
}

/** 权重 → 0..255 的 norm 字节（在 1–2.5 上线性归一化）。
 *  中性 1.0× 精确落在 0，不会因量化改变未加权的强度。 */
export function encodeToneWeight(weight: number): number {
  const span = TONE_WEIGHT_MAX - TONE_WEIGHT_MIN;
  const clamped = Math.max(TONE_WEIGHT_MIN, Math.min(TONE_WEIGHT_MAX, weight));
  return Math.round(((clamped - TONE_WEIGHT_MIN) / span) * 255);
}

/** norm 字节 → 权重。 */
export function decodeToneWeight(byte: number): number {
  return TONE_WEIGHT_MIN + (byte / 255) * (TONE_WEIGHT_MAX - TONE_WEIGHT_MIN);
}

/**
 * 逐像素亮度归因权重图。
 *
 * 每个像素用**滑动窗口**取原始侧（A）的局部均值 / 标准差，以及两侧窗口均值差
 * Δ = mean(B) − mean(A)，再套用与区域级相同的隶属度与权重公式。窗口用行流式
 * 的盒式滤波实现：水平前缀和按行进入环形缓冲，垂直方向维护逐列滑动和，
 * 内存只与网格宽度有关，不随像素数增长。
 *
 * 与 `regionTone` 的区别只在统计范围：区域级统计整个候选，这里统计每个像素
 * 周围的窗口，所以热力图本身会随亮度带、纹理和提亮方向重新分布。仍然是
 * 显示层先验，`differenceMap`、`binaryMask`、面积与掩码资格都不改。
 */
export function toneWeightMap(
  a: ToneGrid,
  b: ToneGrid,
  options: { bands?: ToneBands; radius?: number } = {},
): ToneWeightMap | null {
  const w = a.w;
  const h = a.h;
  if (w < 1 || h < 1 || b.w !== w || b.h !== h) return null;
  const bands = options.bands ?? DEFAULT_TONE_BANDS;
  const radius = Math.max(1, Math.min(32, Math.round(options.radius ?? toneWindowRadius(w, h))));
  const out = new Uint8Array(w * h);
  const neutral = encodeToneWeight(1);

  // 隶属度只跟量化后的灰度和局部标准差有关，先查表，避免逐像素重复算 smoothstep。
  const midLut = new Float32Array(256);
  const darkLut = new Float32Array(256);
  const flatLut = new Float32Array(256);
  for (let i = 0; i < 256; i++) {
    midLut[i] = midMembership(i, bands);
    darkLut[i] = darkMembership(i, bands);
    flatLut[i] = flatMembership(i, bands);
  }
  const brightLut = new Float32Array(511);
  for (let i = 0; i < 511; i++) brightLut[i] = brightMembership(i - 255, bands);

  const span = Math.min(2 * radius + 1, Math.max(w, h));
  const ring = new Float32Array(span * w);
  const ring2 = new Float32Array(span * w);
  const ringCnt = new Float32Array(span * w);
  const ringB = new Float32Array(span * w);
  const ringBCnt = new Float32Array(span * w);
  const pSum = new Float64Array(w + 1);
  const pSum2 = new Float64Array(w + 1);
  const pCnt = new Float64Array(w + 1);
  const pBSum = new Float64Array(w + 1);
  const pBCnt = new Float64Array(w + 1);

  /** 把第 y 行的水平窗口和写进环形缓冲。 */
  function writeRow(y: number): void {
    const slot = (y % span) * w;
    const row = y * w;
    pSum[0] = 0; pSum2[0] = 0; pCnt[0] = 0; pBSum[0] = 0; pBCnt[0] = 0;
    for (let x = 0; x < w; x++) {
      const i = row + x;
      const ga = a.gray[i];
      const okA = (!a.valid || !!a.valid[i]) && Number.isFinite(ga);
      pSum[x + 1] = pSum[x] + (okA ? ga : 0);
      pSum2[x + 1] = pSum2[x] + (okA ? ga * ga : 0);
      pCnt[x + 1] = pCnt[x] + (okA ? 1 : 0);
      const gb = b.gray[i];
      const okB = (!b.valid || !!b.valid[i]) && Number.isFinite(gb);
      pBSum[x + 1] = pBSum[x] + (okB ? gb : 0);
      pBCnt[x + 1] = pBCnt[x] + (okB ? 1 : 0);
    }
    for (let x = 0; x < w; x++) {
      const lo = x > radius ? x - radius : 0;
      const hi = x + radius < w - 1 ? x + radius : w - 1;
      ring[slot + x] = pSum[hi + 1] - pSum[lo];
      ring2[slot + x] = pSum2[hi + 1] - pSum2[lo];
      ringCnt[slot + x] = pCnt[hi + 1] - pCnt[lo];
      ringB[slot + x] = pBSum[hi + 1] - pBSum[lo];
      ringBCnt[slot + x] = pBCnt[hi + 1] - pBCnt[lo];
    }
  }

  // 逐列滑动和；环形缓冲的槽位大小 = 2r+1，离开的行与进入的行可能同槽，
  // 所以必须先减去离开行，再覆盖写入进入行。
  const vA = new Float64Array(w);
  const vA2 = new Float64Array(w);
  const vAc = new Float64Array(w);
  const vB = new Float64Array(w);
  const vBc = new Float64Array(w);
  const addRow = (y: number, sign: number): void => {
    writeRow(y);
    const slot = (y % span) * w;
    for (let x = 0; x < w; x++) {
      vA[x] += sign * ring[slot + x];
      vA2[x] += sign * ring2[slot + x];
      vAc[x] += sign * ringCnt[slot + x];
      vB[x] += sign * ringB[slot + x];
      vBc[x] += sign * ringBCnt[slot + x];
    }
  };

  for (let y = 0; y < Math.min(radius, h); y++) addRow(y, 1);
  for (let y = 0; y < h; y++) {
    const leaving = y - radius - 1;
    if (leaving >= 0) {
      const slot = (leaving % span) * w;
      for (let x = 0; x < w; x++) {
        vA[x] -= ring[slot + x];
        vA2[x] -= ring2[slot + x];
        vAc[x] -= ringCnt[slot + x];
        vB[x] -= ringB[slot + x];
        vBc[x] -= ringBCnt[slot + x];
      }
    }
    const entering = y + radius;
    if (entering < h) addRow(entering, 1);

    const row = y * w;
    for (let x = 0; x < w; x++) {
      const i = row + x;
      const n = vAc[x];
      const l = a.gray[i];
      if (n < 1 || !Number.isFinite(l) || (a.valid && !a.valid[i])) {
        out[i] = neutral;
        continue;
      }
      const meanA = vA[x] / n;
      const variance = vA2[x] / n - meanA * meanA;
      const sigma = variance > 0 ? Math.sqrt(variance) : 0;
      const nb = vBc[x];
      const delta = (nb > 0 ? vB[x] / nb : meanA) - meanA;

      const li = l <= 0 ? 0 : l >= 255 ? 255 : Math.round(l);
      const si = sigma >= 255 ? 255 : Math.round(sigma);
      const di = delta <= -255 ? 0 : delta >= 255 ? 510 : Math.round(delta) + 255;
      const mid = midLut[li];
      const dark = darkLut[li];
      const flat = flatLut[si];
      const bright = brightLut[di];
      out[i] = encodeToneWeight(toneWeight(mid, dark, flat, bright, bands));
    }
  }
  return { norm: out, w, h, radius };
}

/** 归一化权重 → 轮廓颜色：冷灰蓝（低）→ 暖橙（高），连续插值。 */
export function toneColor(norm: number): { stroke: string; fill: string; width: number } {
  const t = clamp01(norm);
  const cool = [78, 124, 168];
  const warm = [224, 86, 50];
  const mix = (i: number) => Math.round(cool[i] + (warm[i] - cool[i]) * t);
  const r = mix(0);
  const g = mix(1);
  const b = mix(2);
  return {
    stroke: `rgb(${r},${g},${b})`,
    fill: `rgba(${r},${g},${b},${(0.05 + 0.09 * t).toFixed(3)})`,
    width: 1.4 + 1.0 * t,
  };
}
