import { readTiffDirectory } from './tiffDirectory';
/**
 * tifDecode.ts — 遥感 TIF 解码/拉伸/预览纯函数（tif-viewer.html 机械直译）
 * ---------------------------------------------------------------
 * 「移植不重写」：算法逐函数、逐字节保留；仅做三处适配，行为不变：
 *   1. 数据源：Blob 直取 → source.read(offset, len) 抽象（Source，见 source.ts）。
 *   2. DOM：buildThumb 等 canvas 依赖改为 CanvasKit 依赖注入（浏览器由 Vue 注入真实实现；
 *      Node 测试不覆盖 canvas 函数，其正确性由 .e2e 浏览器回归保证）。
 *   3. 编排：decodeUtif / chunkedFull / sparseCollect 的 rec 变更与 DOM 更新属交互层，
 *      Phase 3 由 Vue 层按同样流程编排；本文件只保留纯管线（collect/sample/map/stats/plan）。
 *
 * 分派模型（三段）：
 *   - 小 8bit 无符号整数图 → UTIF 全量解码（decodeUtif，Phase 3）
 *   - 无压缩+条带+单波段大图 → 稀疏条带预览（只抽读少数条带做字节切片，秒级）
 *   - 其余（16bit/浮点/多波段/压缩/大图）→ geotiff 分块降采样（chunkedCollect/bandPassCollect）
 * 统一产出预览自然值 src（Float32）+ 每波段统计 st，供前端按拉伸模式实时重绘。
 */
// ⚠️ 必须用命名空间导入，不能用 default：geotiff@3.0.5 是 ESM 作者语义，UMD 忠实带了
// `exports.default = GeoTIFF`（类，无静态 fromBlob）。HTML 版用的是全局 window.GeoTIFF
// （= exports 命名空间，含 fromBlob）。esbuild(vitest) 对 CJS 给 module.exports 所以 default 碰巧可用，
// rollup(vite build) 给 exports.default（=类）→ 会挂。命名空间导入在两种转换下都指向 exports 命名空间。
// 这是「模块互操作适配」，不改任何算法/像素行为（51 Vitest golden 回归锁定）。
import * as GeoTIFF from '../vendor/geotiff.min.js';
import type { Source } from './source.js';

/* ---------------- 关键常量（与 tif-viewer.html 一致，供上层复用/覆盖） ---------------- */
export const PREVIEW_MAX = 2048;          // 整图预览长边上限（chunked/UTIF 路径；保快）
export const SPARSE_PREVIEW_MAX = 8192;   // 稀疏条带预览长边上限（≈原始 1/3）
export const SAFE = 1.3e9;                // 浏览器单次 ArrayBuffer 分配安全线
export let SPARSE_MIN = 1e8;              // 无压缩+条带+单波段大图走稀疏预览的最小字节数
export const JPG_MAX = 8192;              // JPG 导出长边上限
export const JPG_QUALITY = 0.95;
export const EXPORT_BUDGET = 2.4e9;       // 导出 src+RGBA 的 JS 可见分配预算
export const MULTI_BAND_LONG = 8192;      // 多波段导出长边硬上限
export const BAND_ACC_LIMIT = 4e8;        // 分块 Float64 累加器超此字节 → 带通累加
export const ACC_BAND_ROWS = 256;         // 带通累加每带输出行数
export const CANVAS_AREA_MAX = 268435456; // Chromium 画布面积上限（16384²）

/** 测试钩子：改写稀疏路径判定阈值（默认 1e8 不动）。浏览器回归用小 fixture 走稀疏路由时调用。 */
export function setSparseMin(n: number): void {
  SPARSE_MIN = n;
}

export const COMP_NAMES: Record<number, string> = {
  1: '无压缩', 5: 'LZW', 6: 'JPEG', 7: 'JPEG', 8: 'Deflate', 32946: '旧Deflate',
  32773: 'PackBits', 34712: 'JPEG2000', 34925: 'LZMA', 50000: 'ZSTD',
};

/* ---------------- 类型 ---------------- */
/** geotiff GeoTIFFImage 的最小结构（._ph 由 probeImage 解析后回填，供 getSamplePlan 判断反相） */
export interface GeoTiffImageLike {
  _ph?: number | null;
  getWidth(): number;
  getHeight(): number;
  getSamplesPerPixel(): number;
  getBitsPerSample(): number | number[];
  getSampleFormat(): number | number[];
  readRasters(opts: { window: [number, number, number, number]; samples: number[]; interleave: boolean }): Promise<ArrayLike<number>>;
}

export interface ProbeInfo {
  stripRows?: number;
  tileWidth?: number;
  tileHeight?: number;
  W: number;
  H: number;
  spp: number;
  bits: number;
  sampleFormat: number;          // 1=无符号整数 2=有符号 3=浮点
  photometric: number | null;    // 0=WhiteIsZero（需反相）
  compression: number | null;
  layout: string;
  image: GeoTiffImageLike;
  tiff: unknown;
  /** BigTIFF（magic 43）。vendored UTIF 不能解 BigTIFF → 强制走 geotiff 分块（解码路由用） */
  big?: boolean;
}

export interface SamplePlan {
  samples: number[];
  comps: number;
  bits: number;
  invert: boolean;               // photometric === 0（WhiteIsZero 反相）
}

export interface BandStats {
  min: number;
  max: number;
  hist: Float64Array;
  cdf: Float64Array;
  total: number;
  p2: number;
  p98: number;
}

export type StretchMode = 'linear' | 'linear2' | 'sqrt' | 'log' | 'equal';
export interface CurvePoint {x:number;y:number}
export interface StretchRange { low:number; high:number; curve?:readonly CurvePoint[] }

/** 稀疏条带解析结果（布局元数据；数据仍由 source.read 抽读，不持有 file） */
export interface SparseLayoutOk {
  ok: true;
  le: boolean;
  W: number;
  H: number;
  bits: number;
  sf: number;
  rps: number;
  bpp: number;
  rowBytes: number;
  offs: number[];
  lens: number[];
  strips: number;
}
export type SparseLayout = SparseLayoutOk | { ok: false };

export interface SparseSampleResult {
  src: Float32Array;
  sw: number;
  sh: number;
  bytesRead: number;
  stripsTouched: number;
}

export interface SparseCollectResult extends SparseSampleResult {
  nb: 1;
  stats: BandStats[];
  invert: boolean;
  W: number;
  H: number;
}

export interface BandPassOpts {
  bandRows?: number;
  checkpoint?: (stage?: string) => Promise<void>;
  onRows?: (src: Float32Array, width: number, height: number, y: number) => Promise<void>;
}

/* ---------------- geotiff 辅助 ---------------- */
function safeBits(image: GeoTiffImageLike): number {
  let b: number | number[] = 8;
  try {
    const v = image.getBitsPerSample();
    if (typeof v === 'number') b = v;
    else if (v) b = Math.max.apply(null, Array.from(v));
  } catch (e) {
    /* 保持兜底 8 */
  }
  if (!isFinite(b as number) || (b as number) <= 0) b = 8;
  return b as number;
}

export function getSamplePlan(image: GeoTiffImageLike): SamplePlan {
  const spp = image.getSamplesPerPixel();
  const samples = spp >= 3 ? [0, 1, 2] : [0];
  const ph = image._ph;   // 在 probeImage 里解析好的 photometric（0=WhiteIsZero 需反相）
  return { samples, comps: samples.length, bits: safeBits(image), invert: ph === 0 };
}

/* ---------------- 显示拉伸（ENVI 风格预处理） ---------------- */
export function stretchMap(v: number, b: number, st: BandStats[] | null | undefined, mode: StretchMode, range?:StretchRange): number {
  const s = st && st[b];
  // 统计缺失兜底：直接按值自身截断到 0~255 返回，保证任何情况下都不抛错
  if (!s) { const cv = (v > 255) ? 255 : (v < 0 ? 0 : v); return (cv === cv) ? cv : 0; }
  const lo = range?.low ?? ((mode === 'linear2') ? s.p2 : s.min);
  const hi = range?.high ?? ((mode === 'linear2') ? s.p98 : s.max);
  if (hi > lo) {
    let t = (v - lo) / (hi - lo);
    if (t < 0) t = 0; else if (t > 1) t = 1;
    if(range?.curve){
      const points=range.curve;
      for(let i=1;i<points.length;i++)if(t<=points[i].x){
        const a=points[i-1],z=points[i];
        return (a.y+(z.y-a.y)*(t-a.x)/(z.x-a.x))*255;
      }
      return 255;
    }
    if (mode === 'sqrt') return Math.sqrt(t) * 255;
    if (mode === 'log') return Math.log(1 + t) / Math.LN2 * 255;
    if (mode === 'equal') {
      if(!(s.total>0))return t*255;
      if(range&&s.max>s.min){
        if(t<=0)return 0;if(t>=1)return 255;
        const bin=(value:number)=>Math.max(0,Math.min(s.cdf.length-1,Math.floor((value-s.min)/(s.max-s.min)*s.cdf.length)));
        const a=s.cdf[bin(lo)],z=s.cdf[bin(hi)];
        return z>a?(s.cdf[bin(v)]-a)/(z-a)*255:t*255;
      }
      const BINS = s.cdf.length, k = (t * (BINS - 1)) | 0;
      return s.cdf[k] / s.total * 255;
    }
    return t * 255;                          // linear
  }
  return lo === 0 ? 0 : 128;                 // 常量图：全零→黑，否则中灰
}

export function stretchRgba(
  src: Float32Array, sw: number, sh: number, nb: number,
  st: BandStats[] | null | undefined, mode: StretchMode, invert: boolean,
  output?: Uint8ClampedArray,
  range?:StretchRange,
): Uint8ClampedArray {
  if (!nb) nb = (st && st.length) ? st.length : 1;   // 兜底：nb 缺失时按 stats 波段数，避免误走 3 波段分支
  const n = sw * sh;
  const rgba = output ?? new Uint8ClampedArray(n * 4);
  let k = 0;
  for (let i = 0; i < n; i++, k += 4) {
    const b0 = i * nb;
    let r: number, g: number, b: number;
    if (nb === 1) { r = g = b = stretchMap(src[b0], 0, st, mode, range); }
    else { r = stretchMap(src[b0], 0, st, mode, range); g = stretchMap(src[b0 + 1], 1, st, mode, range); b = stretchMap(src[b0 + 2], 2, st, mode, range); }
    if (invert) { r = 255 - r; g = 255 - g; b = 255 - b; }
    if (r < 0) r = 0; else if (r > 255) r = 255;
    if (g < 0) g = 0; else if (g > 255) g = 255;
    if (b < 0) b = 0; else if (b > 255) b = 255;
    rgba[k] = r; rgba[k + 1] = g; rgba[k + 2] = b; rgba[k + 3] = 255;
  }
  return rgba;
}

/* 每波段统计：min/max、1024 直方图 + CDF、2%/98% 百分位（供 2%线性 / 直方图均衡）。
   传 fLo/fHi 时（8bit 原样图）按固定范围统计，线性退化为恒等映射。 */
export function computeStats(src: Float32Array, sw: number, sh: number, nb: number, fLo?: number, fHi?: number): BandStats[] {
  const BINS = 1024, n = sw * sh;
  const st: BandStats[] = [];
  for (let b = 0; b < nb; b++) {
    let lo = (fLo !== undefined) ? fLo : Infinity;
    let hi = (fHi !== undefined) ? fHi : -Infinity;
    let v: number;
    if (fLo === undefined)
      for (let i = b; i < src.length; i += nb) { v = src[i]; if (v === v && isFinite(v)) { if (v < lo) lo = v; if (v > hi) hi = v; } }
    const hist = new Float64Array(BINS), cdf = new Float64Array(BINS);
    let total = 0;
    if (hi > lo) {
      const sc = BINS / (hi - lo);
      for (let j = b; j < src.length; j += nb) {
        v = src[j];
        if (v === v && isFinite(v)) {
          let q = ((v - lo) * sc) | 0;
          if (q < 0) q = 0; else if (q >= BINS) q = BINS - 1;
          hist[q]++;
        }
      }
    }
    let acc = 0;
    for (let t = 0; t < BINS; t++) { acc += hist[t]; cdf[t] = acc; total += hist[t]; }
    let p2 = lo, p98 = hi;
    if (hi > lo && total > 0) {
      const c2 = total * 0.02, c98 = total * 0.98;
      let run = 0;
      for (let u = 0; u < BINS; u++) {
        run += hist[u];
        if (p2 === lo && run >= c2) p2 = lo + (u + 0.5) * (hi - lo) / BINS;
        if (run >= c98) { p98 = lo + (u + 0.5) * (hi - lo) / BINS; break; }
      }
    }
    st[b] = { min: lo, max: hi, hist, cdf, total, p2, p98 };
  }
  return st;
}

/* geotiff 分块读取：把全图自然值按缩放系数箱式降采样到预览分辨率（Float32 累加求均值），
   内存封顶（预览 ≤2048 边）；结束后由上层按当前拉伸模式统一渲染。 */
export function chunkedCollect(
  image: GeoTiffImageLike, W: number, H: number, plan: SamplePlan, ps: number,
  sw: number, sh: number, onProgress?: (f: number) => void, opts?: BandPassOpts,
): Promise<Float32Array> {
  const nb = plan.comps, chunk = 4096;
  const npx = sw * sh;
  // 导出高分辨率时 Float64 累加器会超单次分配上限（16384² 单波段 ≈2.1GB）→ 改用带通累加。
  // 预览（≤2048）恒 < BAND_ACC_LIMIT，走原逻辑，行为逐字节不变。
  if (opts?.onRows || npx * nb * 8 > BAND_ACC_LIMIT) {
    return bandPassCollect(image, W, H, plan, ps, sw, sh, onProgress, opts);
  }
  const cols = Math.ceil(W / chunk), rows = Math.ceil(H / chunk), total = cols * rows;
  let done = 0;
  const acc = new Float64Array(npx * nb), cnt = new Uint32Array(npx);
  const rowLoop = (ry: number): Promise<void> => {
    if (ry >= rows) return Promise.resolve();
    let x = 0;
    const colStep = (): Promise<void> => {
      if (x >= cols) return Promise.resolve().then(() => rowLoop(ry + 1));
      const x0 = x * chunk, y0 = ry * chunk;
      const x1 = Math.min(W, x0 + chunk), y1 = Math.min(H, y0 + chunk);
      const cw = x1 - x0, chh = y1 - y0;
      x++;
      const dxx = new Uint32Array(cw), dyy = new Uint32Array(chh);
      for (let q = 0; q < cw; q++) { const dq = Math.floor((x0 + q) * ps); dxx[q] = dq >= sw ? sw - 1 : dq; }
      for (let r2 = 0; r2 < chh; r2++) { const dr = Math.floor((y0 + r2) * ps); dyy[r2] = dr >= sh ? sh - 1 : dr; }
      return (opts?.checkpoint?.() ?? Promise.resolve()).then(() => image.readRasters({ window: [x0, y0, x1, y1], samples: plan.samples, interleave: true })).then(async (raster) => {
        await opts?.checkpoint?.();
        for (let yy = 0; yy < chh; yy++) {
          if (yy % 128 === 0) await opts?.checkpoint?.();
          const rowA = dyy[yy] * sw, rowS = yy * cw;
          for (let xx = 0; xx < cw; xx++) {
            const ai = rowA + dxx[xx], si = (rowS + xx) * nb;
            let ok = true;
            for (let b = 0; b < nb; b++) { const v = raster[si + b]; if (v !== v || !isFinite(v)) { ok = false; break; } }
            if (!ok) continue;
            for (let c = 0; c < nb; c++) acc[ai * nb + c] += raster[si + c];
            cnt[ai]++;
          }
        }
        done++;
        if (onProgress) onProgress(done / total);
        return new Promise((r) => setTimeout(r, 0)).then(colStep);
      });
    };
    return colStep();
  };
  return rowLoop(0).then(async () => {
    const src = new Float32Array(npx * nb);
    for (let i = 0; i < npx; i++) {
      const c = cnt[i];
      if (!c) continue;
      for (let b2 = 0; b2 < nb; b2++) src[i * nb + b2] = acc[i * nb + b2] / c;
    }
    await opts?.onRows?.(src, sw, sh, 0);
    return src;
  });
}

/* 带通累加：与 chunkedCollect 同语义（逐像素求和/计数求均值），但累加器只覆盖当前输出带，
   内存由全图 2.1GB 压到 ≈ACC_BAND_ROWS×sw×nb×8。源行区间按带反推并做带内守卫，保证每个
   源像素恰好累加一次。仅供导出高分辨率使用（预览恒走原逻辑）。 */
export function bandPassCollect(
  image: GeoTiffImageLike, W: number, H: number, plan: SamplePlan, ps: number,
  sw: number, sh: number, onProgress?: (f: number) => void, opts?: BandPassOpts,
): Promise<Float32Array> {
  opts = opts || {};
  const nb = plan.comps, B = opts.bandRows || ACC_BAND_ROWS;
  const src = new Float32Array(sw * sh * nb);
  const rowBand = (or0: number): Promise<Float32Array> => {
    // 【有意偏差】原 HTML 终端为 `Promise.resolve()`（不带 src）：就地填充的 src 被丢弃，
    // 导致 8192² 高分导出（npx*nb*8 > BAND_ACC_LIMIT 走带通）拿到的 Promise 解析为
    // undefined，上游 rec.src = undefined 崩。此处改返回 src，是让函数兑现其"产出 src"的
    // 契约的最小修复；其余算法逐字节不变。
    if (or0 >= sh) return Promise.resolve(src);
    const or1 = Math.min(sh, or0 + B), bandH = or1 - or0;
    const acc = new Float64Array(bandH * sw * nb), cnt = new Uint32Array(bandH * sw);
    const yIn0 = Math.max(0, Math.floor(or0 / ps));
    const yIn1 = Math.min(H, Math.ceil(or1 / ps));
    const chunk = 4096, cols = Math.ceil(W / chunk), rows = Math.ceil((yIn1 - yIn0) / chunk);
    const rowLoop = (ri: number): Promise<void> => {
      if (ri >= rows) return Promise.resolve();
      const y0 = yIn0 + ri * chunk, y1 = Math.min(yIn1, y0 + chunk), chh = y1 - y0;
      const dyy = new Uint32Array(chh);
      for (let r2 = 0; r2 < chh; r2++) { const dr = Math.floor((y0 + r2) * ps); dyy[r2] = dr >= sh ? sh - 1 : dr; }
      let x = 0;
      const colStep = (): Promise<void> => {
        if (x >= cols) return Promise.resolve().then(() => rowLoop(ri + 1));
        const x0 = x * chunk, x1 = Math.min(W, x0 + chunk), cw = x1 - x0;
        x++;
        const dxx = new Uint32Array(cw);
        for (let q = 0; q < cw; q++) { const dq = Math.floor((x0 + q) * ps); dxx[q] = dq >= sw ? sw - 1 : dq; }
        return (opts?.checkpoint?.() ?? Promise.resolve()).then(() => image.readRasters({ window: [x0, y0, x1, y1], samples: plan.samples, interleave: true })).then(async (raster) => {
          await opts?.checkpoint?.();
          for (let yy = 0; yy < chh; yy++) {
            if (yy % 128 === 0) await opts?.checkpoint?.();
            const d = dyy[yy];
            if (d < or0 || d >= or1) continue;
            const local = (d - or0) * sw, rowS = yy * cw;
            for (let xx = 0; xx < cw; xx++) {
              const ai = local + dxx[xx], si = (rowS + xx) * nb;
              let ok = true;
              for (let b = 0; b < nb; b++) { const v = raster[si + b]; if (v !== v || !isFinite(v)) { ok = false; break; } }
              if (!ok) continue;
              for (let c = 0; c < nb; c++) acc[ai * nb + c] += raster[si + c];
              cnt[ai]++;
            }
          }
          return new Promise((r) => setTimeout(r, 0)).then(colStep);
        });
      };
      return colStep();
    };
    return rowLoop(0).then(async () => {
      for (let i = 0; i < bandH * sw; i++) {
        const c = cnt[i];
        if (!c) continue;
        const oi = (or0 * sw + i) * nb;
        for (let b2 = 0; b2 < nb; b2++) src[oi + b2] = acc[i * nb + b2] / c;
      }
      if (onProgress) onProgress(or1 / sh);
      await opts?.onRows?.(src.subarray(or0 * sw * nb, or1 * sw * nb), sw, bandH, or0);
      return rowBand(or1);
    });
  };
  return rowBand(0);
}

/* ---------------- 稀疏条带预览（无压缩+条带+单波段大图快路径） ----------------
   不再全读文件：按条带偏移/长度直接对 source 做字节切片，只抽读 ph 行 × 抽样列。
   对 1.1GB 级「无压缩·条带1行」文件从 ~2 分钟降到秒级（读量约为文件的 1/几十）。 */
export function stripPxVal(u8: Uint8Array, dv: DataView, le: boolean, bpp: number, sf: number, bo: number): number {
  switch (bpp) {
    case 1:
      if (sf === 2) return dv.getInt8(bo);
      return u8[bo];
    case 2: {
      const v = le ? dv.getUint16(bo, true) : dv.getUint16(bo, false);
      return sf === 2 ? (v << 16 >> 16) : v;
    }
    case 4:
      if (sf === 3) return dv.getFloat32(bo, le);
      const v32 = le ? dv.getUint32(bo, true) : dv.getUint32(bo, false);
      return sf === 2 ? (v32 | 0) : v32;
  }
  return NaN;
}

// 解析无压缩条带布局（偏移/长度数组），供稀疏预览直接做字节切片。
// 仅接受：无压缩(259=1) + 条带(非tiled) + 单波段(277=1)；其余返回 {ok:false}。
export async function parseStrips(source: Source): Promise<SparseLayout> {
  const dir = await readTiffDirectory(source);
  if (!dir || await dir.scalar(259, -1) !== 1 || dir.entries.has(322) || dir.entries.has(323)) return { ok: false };
  const [W, H, spp, rps, bits, sf] = await Promise.all([
    dir.scalar(256, 0), dir.scalar(257, 0), dir.scalar(277, 1), dir.scalar(278, 0), dir.scalar(258, 8), dir.scalar(339, 1),
  ]);
  const bpp = bits / 8;
  if (!W || !H || !rps || spp !== 1 || ![1, 2, 4].includes(bpp)) return { ok: false };
  const strips = Math.ceil(H / rps);
  const [offs, lens] = await Promise.all([dir.values(273, strips), dir.values(279, strips)]);
  if (!offs || !lens) return { ok: false };
  return { ok: true, le: dir.le, W, H, bits, sf, rps, bpp, rowBytes: W * bpp, offs, lens, strips };
}

export function isSparseCandidate(probe: ProbeInfo | null): boolean {
  if (!probe || probe.spp !== 1 || probe.compression !== 1) return false;
  const b = probe.bits;
  if (b !== 8 && b !== 16 && b !== 32) return false;
  const sf = probe.sampleFormat;
  if (sf !== 1 && sf !== 2 && sf !== 3) return false;
  return probe.W * probe.H * (b / 8) > SPARSE_MIN;
}

// 稀疏条带采样：直接按条带字节切片把抽样行写入目标数组（不再留 rows[] 整行副本，省 ~1GB）。
// 导出复用同一机制（targetMax 可调大），返回 {src, sw, sh, bytesRead, stripsTouched}。
export function sparseSample(source: Source, sp: SparseLayoutOk, targetMax?: number, onProgress?: (f: number) => void, checkpoint?: (stage?: string) => Promise<void>, onRows?: BandPassOpts['onRows'], priorityRows?: readonly number[]): Promise<SparseSampleResult> {
  const W = sp.W, H = sp.H, bpp = sp.bpp, sf = sp.sf, le = sp.le, rps = sp.rps;
  const tMax = (typeof targetMax === 'number') ? targetMax : SPARSE_PREVIEW_MAX;
  const ps = Math.min(1, tMax / Math.max(W, H));
  const pw = Math.max(1, Math.round(W * ps)), ph = Math.max(1, Math.round(H * ps));
  // 目标→源线性最近邻映射：0↔0、pw-1↔W-1 端点精确对齐，抽样点均匀覆盖全图。
  // 旧实现用 ceil(W/pw) 定步长再 clamp 到 W-1：右侧 (W - pw*floor(W/pw)) 列全部重复最后像素
  // （24739→2048 时列 1903..2047 全映射到 24738）→ 右/下大片拉伸条纹。此映射彻底消除。
  const mapX = (j: number): number => pw <= 1 ? 0 : Math.round(j * (W - 1) / (pw - 1));
  const mapY = (i: number): number => ph <= 1 ? 0 : Math.round(i * (H - 1) / (ph - 1));
  const src = new Float32Array(pw * ph);
  // Bound read amplification: coalescing must not turn sparse sampling into a
  // full-file read. At most 1 MiB per group, 64 KiB gaps and 50% extra bytes.
  const maxRead = 1024 * 1024, maxGap = 64 * 1024;
  const rows: { i: number; offset: number; length: number }[] = [];
  for (let i = 0; i < ph; i++) {
    const r = mapY(i);
    const si = Math.floor(r / rps), rowOff = (r % rps) * sp.rowBytes;
    const base = sp.offs[si], avail = sp.lens[si];
    if (!base || !avail) continue;
    const len = Math.min(avail - rowOff, sp.rowBytes);
    if (len < bpp) continue;
    rows.push({ i, offset: base + rowOff, length: len });
  }
  // TIFF strip offsets need not follow image order. Read in file order while
  // writing each row to its original output position.
  rows.sort((a, b) => a.offset - b.offset);
  const groups: { offset: number; end: number; useful: number; rows: typeof rows }[] = [];
  for (const row of rows) {
    const last = groups[groups.length - 1];
    const end = Math.max(last?.end ?? 0, row.offset + row.length);
    if (last && row.offset - last.end <= maxGap && end - last.offset <= maxRead
        && end - last.offset <= (last.useful + row.length) * 1.5) {
      last.end = end; last.useful += row.length; last.rows.push(row);
    } else groups.push({ offset: row.offset, end: row.offset + row.length, useful: row.length, rows: [row] });
  }
  const xs = new Uint32Array(pw);
  if (priorityRows?.length) {
    const wanted = new Set(priorityRows);
    const priority = new Set(groups.filter(group => group.rows.some(row => wanted.has(row.i))));
    // Reorder existing reads only. Coalescing, byte count and samples stay intact.
    groups.sort((a,b) => Number(priority.has(b)) - Number(priority.has(a)));
  }
  for (let j = 0; j < pw; j++) xs[j] = mapX(j);
  const nativeLE = new Uint8Array(new Uint16Array([1]).buffer)[0] === 1;
  let bytesRead = 0, stripsTouched = 0, next = 0, completed = ph - rows.length;
  let failed = false;
  // Eight continuously replenished workers avoid waiting for the slowest row
  // of every old batch. Large source rows reduce concurrency to an 8 MiB budget
  // (one oversized row remains readable without allocating the entire strip).
  const largest = groups.reduce((n, g) => Math.max(n, g.end - g.offset), 1);
  const concurrency = Math.max(1, Math.min(8, Math.floor(8 * 1024 * 1024 / largest)));
  const worker = async (): Promise<void> => {
    while (!failed && next < groups.length) {
      await checkpoint?.();
      if (failed || next >= groups.length) return;
      const group = groups[next++];
      try {
        const buf = await source.read(group.offset, group.end - group.offset);
        await checkpoint?.();
        if (failed) return;
        const u8 = new Uint8Array(buf), dv = new DataView(buf);
        bytesRead += buf.byteLength;
        for (const row of group.rows) {
          const start = row.offset - group.offset;
          if (start + xs[pw - 1] * bpp + bpp > buf.byteLength
              || xs[pw - 1] * bpp + bpp > row.length) {
            throw new Error('TIFF sampled row is truncated');
          }
          const off = row.i * pw;
          // Aligned native-endian buffers avoid a function/DataView dispatch
          // for every pixel. Other layouts retain the portable scalar reader.
          let values: ArrayLike<number> | null = null;
          if ((bpp === 1 || le === nativeLE) && start % bpp === 0) {
            const count = Math.floor(row.length / bpp);
            if (bpp === 1) values = sf === 2 ? new Int8Array(buf, start, count) : new Uint8Array(buf, start, count);
            else if (bpp === 2) values = sf === 2 ? new Int16Array(buf, start, count) : new Uint16Array(buf, start, count);
            else if (bpp === 4) values = sf === 3 ? new Float32Array(buf, start, count)
              : sf === 2 ? new Int32Array(buf, start, count) : new Uint32Array(buf, start, count);
          }
          if (values) for (let j = 0; j < pw; j++) src[off + j] = values[xs[j]];
          else for (let j = 0; j < pw; j++) src[off + j] = stripPxVal(u8, dv, le, bpp, sf, start + xs[j] * bpp);
          stripsTouched++;
        }
        completed += group.rows.length;
        onProgress?.(completed / ph);
        // A group may complete out of order. Publish only its finished rows;
        // never expose still-zero rows from another in-flight read.
        if (onRows) for (const row of group.rows) {
          await onRows(src.subarray(row.i * pw, (row.i + 1) * pw), pw, 1, row.i);
        }
      } catch (error) { failed = true; throw error; }
    }
  };
  return Promise.all(Array.from({ length: Math.min(concurrency, groups.length) }, worker))
    .then(() => {
      if (!groups.length) onProgress?.(1);
      return { src, sw: pw, sh: ph, bytesRead, stripsTouched };
    });
}

// 稀疏收集纯管线（原 sparseCollect 的 silent 分支）：采样 → 统计 → 统一产出对象。
// rec 变更 / 遮罩 / 进度 UI 等交互层在 Phase 3 由 Vue 编排，本文件不碰 DOM。
export function sparseCollect(
  source: Source, probe: ProbeInfo, sp: SparseLayoutOk, targetMax?: number, onProgress?: (f: number) => void, checkpoint?: (stage?: string) => Promise<void>,
  onRows?: BandPassOpts['onRows'], statistics?: BandStats[],
): Promise<SparseCollectResult> {
  return sparseSample(source, sp, targetMax, onProgress, checkpoint, onRows).then(async (r) => {
    await checkpoint?.('统计像素');
    const src = r.src, pw = r.sw, ph = r.sh;
    const stats = statistics ?? computeStats(src, pw, ph, 1);
    return {
      src, sw: pw, sh: ph, nb: 1 as const, stats,
      invert: probe.photometric === 0, W: sp.W, H: sp.H,
      bytesRead: r.bytesRead, stripsTouched: r.stripsTouched,
    };
  });
}

/* ---------------- 头部 IFD 解析 ---------------- */
// 按偏移读取 IFD 与值数组，兼容文件尾目录及 classic TIFF / BigTIFF。
// geotiff 的 getFileDirectory() 在部分构建里是惰性桩，读不到 photometric/compression，故自行解析。
/** 读头部 magic 判断是否 BigTIFF（magic 43 vs classic 42）。vendored UTIF 不能解 BigTIFF，
 *  decode 路由（needGeo）据此把小的 8bit BigTIFF 也走 geotiff 分块。 */
export function isBigTiff(source: Source): Promise<boolean> {
  return source.read(0, 4).then((buf) => {
    const u8 = new Uint8Array(buf);
    if (u8.length < 4) return false;
    const le = u8[0] === 0x49;   // 'II' = 小端
    const dv = new DataView(buf);
    return le ? dv.getUint16(2, true) === 43 : dv.getUint16(2, false) === 43;
  }).catch(() => false);
}

export async function tiffTags(source: Source): Promise<Record<number, number>> {
  const dir = await readTiffDirectory(source), tags: Record<number, number> = {};
  if (!dir) return tags;
  for (const tag of [256, 257, 258, 259, 262, 273, 277, 278, 279, 284, 322, 323, 324, 325, 339]) {
    const value = await dir.values(tag);
    if (value) tags[tag] = value[0];
  }
  return tags;
}

// 由头部标签生成压缩/布局摘要（用于文件列表即探显示）
export function layoutInfo(tags: Record<number, number>): string {
  const parts: string[] = [];
  const comp = tags[259];
  if (COMP_NAMES[comp]) parts.push(COMP_NAMES[comp]);
  else if (comp != null) parts.push('压缩' + comp);
  else parts.push('压缩?');
  if (tags[322] && tags[323]) parts.push('瓦片' + tags[322] + '×' + tags[323]);
  else if (tags[278]) parts.push('条带' + tags[278] + '行');
  else parts.push('布局未知');
  if (tags[324] != null) parts.push('(tiled)');
  return parts.join(' · ');
}

// 用 geotiff 懒读尺寸 + 自行解析 photometric/compression（只读文件头部几KB），估算解码内存占用
export function probeImage(file: Blob): Promise<ProbeInfo> {
  return GeoTIFF.fromBlob(file).then((tiff: unknown) => {
    const gTiff = tiff as {
      getImage(): Promise<GeoTiffImageLike>;
    };
    return gTiff.getImage().then((image) => {
      return tiffTagsFromBlob(file).then((tags) => {
        let sf = 1;
        try { sf = image.getSampleFormat() as number; } catch (e) { /* 兜底 1 */ }
        if (Array.isArray(sf)) sf = sf[0];
        if (typeof sf !== 'number' || !isFinite(sf)) sf = 1;
        const ph = tags[262] != null ? tags[262] : null;
        image._ph = ph;   // 供 getSamplePlan 判断反相
        return bigTiffFromBlob(file).then((big) => ({
          W: image.getWidth(), H: image.getHeight(),
          spp: image.getSamplesPerPixel(), bits: safeBits(image),
          sampleFormat: sf,             // 1=无符号整数 2=有符号 3=浮点
          photometric: ph,
          compression: tags[259] != null ? tags[259] : null,
          stripRows: tags[278], tileWidth: tags[322], tileHeight: tags[323],
          layout: layoutInfo(tags),
          image, tiff,
          big,
        }));
      });
    });
  });
}

// probeImage 需要 Blob 上的 tiffTags；Blob 也是 Source（read=slice.arrayBuffer）。
// 为保持 probeImage 的 Blob 签名不变（Node 测试用 FileReader shim），此处直接包一层。
async function tiffTagsFromBlob(file: Blob): Promise<Record<number, number>> {
  return tiffTags({ size: file.size, read: (o, l) => file.slice(o, o + l).arrayBuffer() });
}

// BigTIFF 判定同走 Blob→Source 包装（probeImage 加 big 字段）
function bigTiffFromBlob(file: Blob): Promise<boolean> {
  return isBigTiff({ size: file.size, read: (o, l) => file.slice(o, o + l).arrayBuffer() });
}

/* ---------------- Canvas 依赖注入（浏览器端注入真实 canvas，Node 不覆盖） ----------------
   buildThumb 全程不创建超过 8192 边的 canvas，绕开画布面积上限；按 4096 块逐步汇入中间画布。 */
export interface KitImageData {
  data: Uint8ClampedArray;
  width: number;
  height: number;
}
export interface KitCanvasCtx {
  imageSmoothingEnabled: boolean;
  imageSmoothingQuality: 'low' | 'medium' | 'high' | string;
  putImageData(im: KitImageData, dx: number, dy: number): void;
  drawImage(img: KitCanvas, ...args: number[]): void;
}
export interface KitCanvas {
  width: number;
  height: number;
  getContext(type: '2d'): KitCanvasCtx | null;
}
export interface CanvasKit {
  createCanvas(w: number, h: number): KitCanvas;
  createImageData(data: Uint8ClampedArray, w: number, h: number): KitImageData;
}

/* UTIF 全量解码后的 RGBA 下采样成 ≤2048 缩略图。
   全程不创建超过 8192 边的 canvas，绕开画布面积上限；按 4096 块逐步汇入中间画布。 */
export function buildThumb(rgba: Uint8ClampedArray, W: number, H: number, kit: CanvasKit, maxT?: number, maxI?: number): KitCanvas {
  maxT = maxT || 2048; maxI = maxI || 8192;
  const TI = 4096;
  const si = Math.min(1, maxI / Math.max(W, H));
  const iw = Math.max(1, Math.round(W * si));
  const ih = Math.max(1, Math.round(H * si));
  const ic = kit.createCanvas(iw, ih);
  const ictx = ic.getContext('2d') as KitCanvasCtx;
  ictx.imageSmoothingEnabled = true;
  ictx.imageSmoothingQuality = 'high';
  for (let ty = 0; ty < H; ty += TI) {
    const chh = Math.min(TI, H - ty);
    for (let tx = 0; tx < W; tx += TI) {
      const cww = Math.min(TI, W - tx);
      const chunk = new Uint8ClampedArray(rgba.buffer, rgba.byteOffset + (ty * W + tx) * 4, cww * chh * 4);
      const cc = kit.createCanvas(cww, chh);
      (cc.getContext('2d') as KitCanvasCtx).putImageData(kit.createImageData(chunk, cww, chh), 0, 0);
      ictx.drawImage(cc, 0, 0, cww, chh,
        Math.round(tx * si), Math.round(ty * si),
        Math.max(1, Math.round(cww * si)), Math.max(1, Math.round(chh * si)));
    }
  }
  const s = Math.min(1, maxT / Math.max(iw, ih));
  const tw = Math.max(1, Math.round(iw * s));
  const th = Math.max(1, Math.round(ih * s));
  const tc = kit.createCanvas(tw, th);
  (tc.getContext('2d') as KitCanvasCtx).drawImage(ic, 0, 0, iw, ih, 0, 0, tw, th);
  return tc;
}

/* ---------------- 导出分辨率预算规划 ---------------- */
// 内存预算规划：返回满足画布面积上限、尺寸上限与 EXPORT_BUDGET 的导出分辨率。
// 多波段直接封顶 MULTI_BAND_LONG（8192）。预算不足时自动降档并置 reduced=true 供状态提示。
export interface ExportPlan {
  pw: number;
  ph: number;
  scale: number;
  px: number;
  longEdge: number;
  reduced: boolean;
}
export function planExport(W: number, H: number, nb: number, capLong?: number): ExportPlan | null {
  capLong = capLong || JPG_MAX;
  const LONG = nb > 1 ? Math.min(capLong, MULTI_BAND_LONG) : capLong;
  let scale = Math.min(1, LONG / Math.max(W, H));
  const requested = scale;
  for (;;) {
    const pw = Math.max(1, Math.round(W * scale));
    const ph = Math.max(1, Math.round(H * scale));
    const px = pw * ph;
    if (px > CANVAS_AREA_MAX) { scale = scale * Math.sqrt(CANVAS_AREA_MAX / px) * 0.99; continue; }
    if (pw > 65535 || ph > 65535) { scale *= 0.9; continue; }
    if (px * (nb * 4 + 4) <= EXPORT_BUDGET) {
      return { pw, ph, scale, px, longEdge: Math.max(pw, ph), reduced: scale < requested * 0.999 };
    }
    scale *= 0.9;
    if (scale * Math.max(W, H) < 64) return null;
  }
}
