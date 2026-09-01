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
export const SPARSE_MIN = 1e8;            // 无压缩+条带+单波段大图走稀疏预览的最小字节数
export const JPG_MAX = 8192;              // JPG 导出长边上限
export const JPG_QUALITY = 0.95;
export const EXPORT_BUDGET = 2.4e9;       // 导出 src+RGBA 的 JS 可见分配预算
export const MULTI_BAND_LONG = 8192;      // 多波段导出长边硬上限
export const BAND_ACC_LIMIT = 4e8;        // 分块 Float64 累加器超此字节 → 带通累加
export const ACC_BAND_ROWS = 256;         // 带通累加每带输出行数
export const CANVAS_AREA_MAX = 268435456; // Chromium 画布面积上限（16384²）
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
export function stretchMap(v: number, b: number, st: BandStats[] | null | undefined, mode: StretchMode): number {
  const s = st && st[b];
  // 统计缺失兜底：直接按值自身截断到 0~255 返回，保证任何情况下都不抛错
  if (!s) { const cv = (v > 255) ? 255 : (v < 0 ? 0 : v); return (cv === cv) ? cv : 0; }
  const lo = (mode === 'linear2') ? s.p2 : s.min;
  const hi = (mode === 'linear2') ? s.p98 : s.max;
  if (hi > lo) {
    let t = (v - lo) / (hi - lo);
    if (t < 0) t = 0; else if (t > 1) t = 1;
    if (mode === 'sqrt') return Math.sqrt(t) * 255;
    if (mode === 'log') return Math.log(1 + t) / Math.LN2 * 255;
    if (mode === 'equal') {
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
): Uint8ClampedArray {
  if (!nb) nb = (st && st.length) ? st.length : 1;   // 兜底：nb 缺失时按 stats 波段数，避免误走 3 波段分支
  const n = sw * sh;
  const rgba = new Uint8ClampedArray(n * 4);
  let k = 0;
  for (let i = 0; i < n; i++, k += 4) {
    const b0 = i * nb;
    let r: number, g: number, b: number;
    if (nb === 1) { r = g = b = stretchMap(src[b0], 0, st, mode); }
    else { r = stretchMap(src[b0], 0, st, mode); g = stretchMap(src[b0 + 1], 1, st, mode); b = stretchMap(src[b0 + 2], 2, st, mode); }
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
  if (npx * nb * 8 > BAND_ACC_LIMIT) {
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
      return image.readRasters({ window: [x0, y0, x1, y1], samples: plan.samples, interleave: true }).then((raster) => {
        for (let yy = 0; yy < chh; yy++) {
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
  return rowLoop(0).then(() => {
    const src = new Float32Array(npx * nb);
    for (let i = 0; i < npx; i++) {
      const c = cnt[i];
      if (!c) continue;
      for (let b2 = 0; b2 < nb; b2++) src[i * nb + b2] = acc[i * nb + b2] / c;
    }
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
        return image.readRasters({ window: [x0, y0, x1, y1], samples: plan.samples, interleave: true }).then((raster) => {
          for (let yy = 0; yy < chh; yy++) {
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
    return rowLoop(0).then(() => {
      for (let i = 0; i < bandH * sw; i++) {
        const c = cnt[i];
        if (!c) continue;
        const oi = (or0 * sw + i) * nb;
        for (let b2 = 0; b2 < nb; b2++) src[oi + b2] = acc[i * nb + b2] / c;
      }
      if (onProgress) onProgress(or1 / sh);
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
      if (sf === 3) return dv.getFloat32(bo, true);
      const v32 = le ? dv.getUint32(bo, true) : dv.getUint32(bo, false);
      return sf === 2 ? (v32 | 0) : v32;
  }
  return NaN;
}

// 解析无压缩条带布局（偏移/长度数组），供稀疏预览直接做字节切片。
// 仅接受：无压缩(259=1) + 条带(非tiled) + 单波段(277=1)；其余返回 {ok:false}。
const STRIP_HEAD = 2 * 1024 * 1024;
export function parseStrips(source: Source): Promise<SparseLayout> {
  return source.read(0, STRIP_HEAD).then((buf) => {
    const u8 = new Uint8Array(buf), dv = new DataView(buf);
    const le = u8[0] === 0x49;
    const u = (off: number, size: number): number => {
      if (size === 1) return dv.getUint8(off);
      if (size === 2) return le ? dv.getUint16(off, true) : dv.getUint16(off, false);
      if (size === 4) return le ? dv.getUint32(off, true) : dv.getUint32(off, false);
      if (size === 8) { try { return Number(le ? dv.getBigUint64(off, true) : dv.getBigUint64(off, false)); } catch (e) { return 0; } }
      return 0;
    };
    const magic = u(2, 2);
    if (magic !== 42 && magic !== 43) return { ok: false } as SparseLayout;
    const big = magic === 43;
    const ifdOff = big ? u(8, 8) : u(4, 4);
    const cnt = big ? u(ifdOff, 8) : u(ifdOff, 2);
    const esz = big ? 20 : 12;
    const ebase = ifdOff + (big ? 8 : 2);
    const valInline = big ? 8 : 4, valAt = big ? 12 : 8;
    const tsize = [0, 1, 1, 2, 4, 8, 1, 1, 2, 4, 8, 4, 8, 0, 0, 0, 8, 8, 8];
    const WANT: Record<number, 1> = { 256: 1, 257: 1, 277: 1, 258: 1, 278: 1, 259: 1, 273: 1, 279: 1, 339: 1 };
    const tags: Record<number, { type: number; cnt: number; off: number; s1: number }> = {};
    for (let i = 0; i < cnt; i++) {
      const e = ebase + i * esz;
      const tag = u(e, 2), type = u(e + 2, 2);
      if (!WANT[tag]) continue;
      const c = big ? u(e + 4, 8) : u(e + 4, 4);
      const s1 = (type > 0 && type < tsize.length) ? tsize[type] : 0;
      let vo = e + valAt;
      if (s1 * c > valInline) vo = u(e + valAt, big ? 8 : 4);
      tags[tag] = { type, cnt: c, off: vo, s1 };
    }
    const scalar = (tag: number, dflt: number): number => {
      const t = tags[tag];
      if (!t || !t.s1) return dflt;
      return u(t.off, t.s1);
    };
    const comp = scalar(259, -1);
    if (comp !== 1 || tags[322] || tags[323]) return { ok: false } as SparseLayout;
    const W = scalar(256, 0), H = scalar(257, 0), spp = scalar(277, 1);
    const rps = scalar(278, 0), bits = scalar(258, 8), sf = scalar(339, 1);
    if (!W || !H || !rps || spp !== 1) return { ok: false } as SparseLayout;
    const bpp = bits / 8;
    if (bpp !== Math.floor(bpp) || bpp < 1 || bpp > 8) return { ok: false } as SparseLayout;
    const strips = Math.ceil(H / rps);
    const readArr = (tag: number, n: number): number[] | null => {
      const t = tags[tag];
      if (!t || !t.s1) return null;
      if (t.off + t.s1 * n > buf.byteLength) return null;
      const arr = new Array<number>(n);
      for (let k = 0; k < n; k++) arr[k] = u(t.off + k * t.s1, t.s1);
      return arr;
    };
    const offs = readArr(273, strips), lens = readArr(279, strips);
    if (!offs || !lens) return { ok: false } as SparseLayout;
    const result: SparseLayoutOk = {
      ok: true, le, W, H, bits, sf, rps, bpp, rowBytes: W * bpp, offs, lens, strips,
    };
    return result;
  });
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
export function sparseSample(source: Source, sp: SparseLayoutOk, targetMax?: number, onProgress?: (f: number) => void): Promise<SparseSampleResult> {
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
  let bytesRead = 0, stripsTouched = 0;
  const readRow = (i: number): Promise<void> => {
    const r = mapY(i);
    const si = Math.floor(r / rps), rowOff = (r % rps) * sp.rowBytes;
    const base = sp.offs[si], avail = sp.lens[si];
    if (!base || !avail) return Promise.resolve();
    const len = Math.min(avail - rowOff, sp.rowBytes);
    if (len < bpp) return Promise.resolve();
    return source.read(base + rowOff, len).then((buf) => {
      const u8 = new Uint8Array(buf), dv = new DataView(buf);
      const off = i * pw;
      for (let j = 0; j < pw; j++) {
        src[off + j] = stripPxVal(u8, dv, le, bpp, sf, mapX(j) * bpp);
      }
      bytesRead += len; stripsTouched++;
    });
  };
  let idx = 0;
  const loop = (): Promise<void> => {
    if (idx >= ph) return Promise.resolve();
    const end = Math.min(ph, idx + 8);
    const batch: Promise<void>[] = [];
    for (let k = idx; k < end; k++) batch.push(readRow(k));
    idx = end;
    return Promise.all(batch).then(() => {
      if (onProgress) onProgress(idx / ph);
      return loop();
    });
  };
  return loop().then(() => ({ src, sw: pw, sh: ph, bytesRead, stripsTouched }));
}

// 稀疏收集纯管线（原 sparseCollect 的 silent 分支）：采样 → 统计 → 统一产出对象。
// rec 变更 / 遮罩 / 进度 UI 等交互层在 Phase 3 由 Vue 编排，本文件不碰 DOM。
export function sparseCollect(
  source: Source, probe: ProbeInfo, sp: SparseLayoutOk, targetMax?: number, onProgress?: (f: number) => void,
): Promise<SparseCollectResult> {
  return sparseSample(source, sp, targetMax, onProgress).then((r) => {
    const src = r.src, pw = r.sw, ph = r.sh;
    const stats = computeStats(src, pw, ph, 1);
    return {
      src, sw: pw, sh: ph, nb: 1 as const, stats,
      invert: probe.photometric === 0, W: sp.W, H: sp.H,
      bytesRead: r.bytesRead, stripsTouched: r.stripsTouched,
    };
  });
}

/* ---------------- 头部 IFD 解析 ---------------- */
// 直接从文件头部解析少量关键 IFD 标签（兼容 classic TIFF 与 BigTIFF）。
// geotiff 的 getFileDirectory() 在部分构建里是惰性桩，读不到 photometric/compression，故自行解析。
const TIFF_HEAD = 1048576;   // 1MB（比 parseStrips 的 2MB 小：仅需读常用标量标签）
export function tiffTags(source: Source): Promise<Record<number, number>> {
  return source.read(0, TIFF_HEAD).then((buf) => {
    const u8 = new Uint8Array(buf), dv = new DataView(buf);
    if (u8.length < 16) return {};
    const le = u8[0] === 0x49;   // 'II' = 小端
    const u = (off: number, size: number): number => {
      if (size === 1) return dv.getUint8(off);
      if (size === 2) return le ? dv.getUint16(off, true) : dv.getUint16(off, false);
      if (size === 4) return le ? dv.getUint32(off, true) : dv.getUint32(off, false);
      if (size === 8) { try { return Number(le ? dv.getBigUint64(off, true) : dv.getBigUint64(off, false)); } catch (e) { return 0; } }
      return 0;
    };
    const magic = u(2, 2);
    if (magic !== 42 && magic !== 43) return {};   // 42=classic, 43=BigTIFF
    const big = magic === 43;
    const ifdOff = big ? u(8, 8) : u(4, 4);
    const cnt = big ? u(ifdOff, 8) : u(ifdOff, 2);
    const esz = big ? 20 : 12;
    const ebase = ifdOff + (big ? 8 : 2);
    const valInline = big ? 8 : 4, valAt = big ? 12 : 8;
    const tsize = [0, 1, 1, 2, 4, 8, 1, 1, 2, 4, 8, 4, 8, 0, 0, 0, 8, 8, 8];   // TIFF type → 字节数
    const tags: Record<number, number> = {};
    const n = Math.min(cnt, 300);
    for (let i = 0; i < n; i++) {
      const e = ebase + i * esz;
      const tag = u(e, 2), type = u(e + 2, 2);
      const cntv = big ? u(e + 4, 8) : u(e + 4, 4);
      const s1 = (type > 0 && type < tsize.length) ? tsize[type] : 0;
      let vo = e + valAt;
      if (s1 * cntv > valInline) vo = u(e + valAt, big ? 8 : 4);
      if (s1) tags[tag] = u(vo, s1);
    }
    return tags;
  });
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
        return {
          W: image.getWidth(), H: image.getHeight(),
          spp: image.getSamplesPerPixel(), bits: safeBits(image),
          sampleFormat: sf,             // 1=无符号整数 2=有符号 3=浮点
          photometric: ph,
          compression: tags[259] != null ? tags[259] : null,
          layout: layoutInfo(tags),
          image, tiff,
        };
      });
    });
  });
}

// probeImage 需要 Blob 上的 tiffTags；Blob 也是 Source（read=slice.arrayBuffer）。
// 为保持 probeImage 的 Blob 签名不变（Node 测试用 FileReader shim），此处直接包一层。
async function tiffTagsFromBlob(file: Blob): Promise<Record<number, number>> {
  return tiffTags({ size: file.size, read: (o, l) => file.slice(o, o + l).arrayBuffer() });
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
