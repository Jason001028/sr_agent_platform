/**
 * maskgen.ts — 掩码生成纯函数（tif_viewer/maskgen.js 机械直译，接口不变）
 * ---------------------------------------------------------------
 * 「移植不重写」：算法逐函数、逐字节保留，仅把 UMD 包装换成 ES 模块。
 * 行为正确性唯一权威 = docs/experience/gui-experience.md §8；回归 = 17 项 Vitest 直译版。
 *
 * - 栅格化：把 ROI 多边形（原图像素坐标）逐行扫描线填充成 0/255 二值图，
 *   填充规则与 PIL ImageDraw.polygon 逐像素一致（经验验证）：
 *   像素 (c,r) 填充 ⟺ 整数点 (c,r) 在多边形内或边界上（even-odd、闭区间）。
 * - 掩码文档：输出「掩膜中心点坐标」格式 txt（对齐 JL1KF02B03_..._mask.txt 参考）。
 * - TIFF：写 classic little-endian、8bit 灰度、Adobe Deflate 压缩、单条带 TIFF
 *   （GDAL/PIL/cv2 均可读；SR 的 util.read_img 用 GDAL）。
 * - 魔法棒：容差 + 边缘屏障的连通区域生长（PS 风格），配套轮廓提取/洞填充/简化。
 */
import pako from '../vendor/pako.min.js';

export type Pt = [number, number];
export type Poly = Pt[];

export interface RasterRowsOpts {
  batch?: number;
  /** 以批次喂给下游（如 deflate）；返回 Promise 时每批让出主线程。
      第二参 = batch 批大小（与原实现传 opts.batch 一致） */
  feed?: (chunk: Uint8Array, batch: number) => void | Promise<unknown>;
  onProgress?: (f: number) => void;
}

export interface BuildTiffOpts {
  pako?: { Deflate: new (opts?: Record<string, unknown>) => { push: (d: Uint8Array, flush: boolean) => void; result: Uint8Array } };
  level?: number;
  batch?: number;
  onProgress?: (f: number) => void;
}

export interface MergeAsyncOpts {
  onPhase?: (phase: string) => void;
  onProgress?: (f: number) => void;
}

const EPS = 1e-9;

/* ---------------- 多边形质心（鞋带公式，退化→包围盒中心） ----------------
   返回值 [cx, cy] 为浮点，坐标系与原多边形一致（x=列、y=行）。 */
export function polygonCentroid(pts: Poly): Pt {
  const n = pts.length;
  if (n < 3) return bboxCenter(pts);
  let a2 = 0, cx = 0, cy = 0;
  for (let i = 0; i < n; i++) {
    const j = (i + 1) % n;
    const x0 = pts[i][0], y0 = pts[i][1], x1 = pts[j][0], y1 = pts[j][1];
    const cross = x0 * y1 - x1 * y0;
    a2 += cross;
    cx += (x0 + x1) * cross;
    cy += (y0 + y1) * cross;
  }
  if (a2 === 0) return bboxCenter(pts);
  return [cx / (3 * a2), cy / (3 * a2)];
}

function bboxCenter(pts: Poly): Pt {
  let minx = Infinity, maxx = -Infinity, miny = Infinity, maxy = -Infinity;
  for (let k = 0; k < pts.length; k++) {
    if (pts[k][0] < minx) minx = pts[k][0];
    if (pts[k][0] > maxx) maxx = pts[k][0];
    if (pts[k][1] < miny) miny = pts[k][1];
    if (pts[k][1] > maxy) maxy = pts[k][1];
  }
  return [(minx + maxx) / 2, (miny + maxy) / 2];
}

/* ---------------- 掩膜中心点 txt（对齐参考格式） ----------------
   UTF-8、无 BOM、\r\n；头两行全角字符照抄参考文件；
   数据行：掩膜编号,X坐标,Y坐标（质心，保留 2 位小数）。
   polygons 各顶点为原图像素坐标（x=列、y=行）。 */
const MASK_TXT_HEADER = '＃掩膜中心点坐标（X，Y）\r\n＃掩膜编号，X坐标，Y坐标\r\n';
export function buildMaskTxt(width: number, height: number, polygons: Poly[]): string {
  const out = [MASK_TXT_HEADER];
  for (let i = 0; i < polygons.length; i++) {
    const c = polygonCentroid(polygons[i]);
    out.push((i + 1) + ',' + c[0].toFixed(2) + ',' + c[1].toFixed(2) + '\r\n');
  }
  return out.join('');
}

/* ---------------- 扫描线栅格化（逐行 → emitRow(row, r)） ----------------
   行 r 取整数扫描线 y=r：每条非水平边若闭区间含 y=r 求交；排序成对得闭区间
   [a,b]；填充整数列 c ∈ [ceil(a), floor(b)]。与 Pillow ImageDraw.polygon 一致。 */
/* 行 r 的多边形与扫描线 y=r 的闭区间并集（几何正确的 even-odd + 边界含入）。
   每个多边形**单独**算区间（even-odd 只在单多边形内有效：重叠多边形若合算
   even-odd，重叠区被两条边界穿过判为偶→漏），再对所有区间做**并集**
   （与后端逐多边形填充再 OR 的 union 语义一致）。 */
export function rowIntervals(polygons: Poly[], r: number): [number, number][] {
  const all: [number, number][] = [];
  for (let p = 0; p < polygons.length; p++) {
    const poly = polygons[p];
    const n = poly.length;
    if (n < 3) continue;                       // 退化多边形不填充（与后端一致）
    const events: number[] = [], horiz: [number, number][] = [], cross: number[] = [];
    for (let i = 0; i < n; i++) {
      const j = (i + 1) % n;
      const x0 = poly[i][0], y0 = poly[i][1], x1 = poly[j][0], y1 = poly[j][1];
      if (y0 === y1) {                         // 水平边恰在扫描线上 → 整段边界含入
        if (y0 === r) horiz.push([x0 < x1 ? x0 : x1, x0 < x1 ? x1 : x0]);
        continue;
      }
      const lo = y0 < y1 ? y0 : y1, hi = y0 < y1 ? y1 : y0;
      if (r >= lo && r <= hi) {
        events.push(x0 + (r - y0) * (x1 - x0) / (y1 - y0));
      }
      if ((y0 <= r && r < y1) || (y1 <= r && r < y0)) {   // 半数规则：下半端点计入、上半端点不计
        cross.push(x0 + (r - y0) * (x1 - x0) / (y1 - y0));
      }
    }
    if (!events.length && !horiz.length) continue;
    events.sort((a, b) => a - b);
    const ev: number[] = [];
    for (let e = 0; e < events.length; e++) {
      if (!ev.length || events[e] - ev[ev.length - 1] > 1e-6) ev.push(events[e]);
    }
    for (let g = 0; g + 1 < ev.length; g++) {
      const a = ev[g], b = ev[g + 1];
      if (b - a <= 1e-6) continue;
      const mid = (a + b) / 2;
      let cnt = 0;
      for (let c = 0; c < cross.length; c++) if (cross[c] < mid - 1e-9) cnt++;
      if (cnt % 2 === 1) all.push([a, b]);
    }
    for (let q = 0; q < ev.length; q++) all.push([ev[q], ev[q]]);   // 事件点（边界点）含入
    for (let h = 0; h < horiz.length; h++) all.push(horiz[h]);       // 水平跨距含入
  }
  if (!all.length) return [];
  all.sort((a, b) => a[0] - b[0] || a[1] - b[1]);
  const merged: [number, number][] = [all[0].slice() as [number, number]];
  for (let m = 1; m < all.length; m++) {
    const last = merged[merged.length - 1];
    if (all[m][0] <= last[1] + 1e-6) {          // 相接或重叠 → 合并
      if (all[m][1] > last[1]) last[1] = all[m][1];
    } else merged.push(all[m].slice() as [number, number]);
  }
  return merged;
}

/* 逐行扫描线栅格化：行 r 填充整数列 c ∈ [ceil(a), floor(b)]（a,b 来自 rowIntervals）。
   可选 opts.feed(chunk, rowCount)：以批次喂给下游；返回 Promise 时每批让出主线程。
   opts.batch 默认 128 行/批；opts.onProgress(f) 每批回调。 */
export function rasterRows(W: number, H: number, polygons: Poly[], opts?: RasterRowsOpts): Promise<void> {
  // 捕获为 const，闭包内不再引用可变参数（TS18048 规避）；行为不变
  const o = opts || {};
  const batch = o.batch || 128;
  const feed = o.feed || null;
  const row = new Uint8Array(W);
  let acc = new Uint8Array(batch * W);
  let inB = 0, bDone = 0, r = 0;
  const totalB = Math.max(1, Math.ceil(H / batch));
  function step(): Promise<void> {
    for (; r < H; r++) {
      const gaps = rowIntervals(polygons, r);
      row.fill(0);
      for (let g = 0; g < gaps.length; g++) {
        const c0 = Math.max(0, Math.ceil(gaps[g][0] - EPS));
        const c1 = Math.min(W - 1, Math.floor(gaps[g][1] + EPS));
        if (c0 <= c1) row.fill(255, c0, c1 + 1);
      }
      acc.set(row, inB * W);
      inB++;
      if (inB === batch) {
        inB = 0;
        const chunk = acc;
        acc = new Uint8Array(batch * W);
        bDone++;
        if (o.onProgress) o.onProgress(bDone / totalB);
        if (feed) {
          const pr = feed(chunk, batch);
          if (pr && typeof (pr as Promise<unknown>).then === 'function') return (pr as Promise<void>).then(step);
        }
      }
    }
    if (inB > 0) {
      const tail = acc.subarray(0, inB * W);
      inB = 0;
      bDone++;
      if (o.onProgress) o.onProgress(bDone / totalB);
      if (feed) {
        const pr2 = feed(tail, batch);
        if (pr2 && typeof (pr2 as Promise<unknown>).then === 'function') return pr2 as Promise<void>;
      }
    }
    return Promise.resolve();
  }
  return step();
}

/* ---------------- classic TIFF（小端、8bit 灰度、Adobe Deflate、单条带） ----------------
   polygons 顶点为原图像素坐标；返回 Promise<Uint8Array>。
   opts.level 压缩等级（默认 6）；opts.onProgress(f) 按行批回调。 */
export function buildTiff(W: number, H: number, polygons: Poly[], opts?: BuildTiffOpts): Promise<Uint8Array> {
  opts = opts || {};
  const pz = opts.pako || pako;
  if (!pz || typeof pz.Deflate !== 'function') {
    return Promise.reject(new Error('pako 不可用，无法压缩掩码 TIFF'));
  }
  const defl = new pz.Deflate({ level: opts.level || 6 });
  const feed = (chunk: Uint8Array): void | Promise<unknown> => {
    defl.push(chunk, false);
    if (opts.onProgress) return new Promise((res) => setTimeout(res, 0)); // 让 UI 有机会重绘
  };
  return rasterRows(W, H, polygons, {
    batch: opts.batch || 128,
    feed,
    onProgress: opts.onProgress,
  }).then(() => {
    defl.push(new Uint8Array(0), true);   // Z_FINISH：补尾块 + adler32
    return assembleTiff(W, H, defl.result);
  });
}

function assembleTiff(W: number, H: number, comp: Uint8Array): Uint8Array {
  const N = 11;
  const dataOff = 8 + 2 + N * 12 + 4;
  const buf = new ArrayBuffer(dataOff + comp.length);
  const v = new DataView(buf);
  let off = 0;
  v.setUint16(off, 0x4949, true); off += 2;   // "II" 小端
  v.setUint16(off, 42, true); off += 2;        // classic TIFF
  v.setUint32(off, 8, true); off += 4;         // IFD 偏移
  v.setUint16(off, N, true); off += 2;
  function entry(tag: number, type: number, count: number, value: number) {
    v.setUint16(off, tag, true); off += 2;
    v.setUint16(off, type, true); off += 2;
    v.setUint32(off, count, true); off += 4;
    if (type === 3) v.setUint16(off, value, true);
    else v.setUint32(off, value, true);
    off += 4;
  }
  entry(256, 4, 1, W);                 // ImageWidth
  entry(257, 4, 1, H);                 // ImageLength
  entry(258, 3, 1, 8);                 // BitsPerSample
  entry(259, 3, 1, 8);                 // Compression = 8 (Adobe Deflate)
  entry(262, 3, 1, 1);                 // Photometric = BlackIsZero
  entry(273, 4, 1, dataOff);           // StripOffsets
  entry(277, 3, 1, 1);                 // SamplesPerPixel
  entry(278, 4, 1, H);                 // RowsPerStrip
  entry(279, 4, 1, comp.length);       // StripByteCounts
  entry(284, 3, 1, 1);                 // PlanarConfiguration = chunky
  entry(339, 3, 1, 1);                 // SampleFormat = unsigned int
  v.setUint32(off, 0, true); off += 4; // 下一 IFD = 无
  new Uint8Array(buf, dataOff).set(comp);
  return new Uint8Array(buf);
}

/* ---------------- 魔法棒：容差 + 边缘屏障 连通区域生长（自适应） ----------------
   rgba 为显示用 RGBA（Uint8ClampedArray，w*h*4）；(sx,sy) 种子像素；
   tol 用户容差（窗口下限）；edgeThresh 边缘梯度阈值（超过视为屏障）。
   选区判定为「自适应区域生长」：7×7 种子窗初始化运行均值/方差，窗口 =
   max(tol, K*spread)，spread 为已选区像素相对运行均值的逐像素 RGB 距离
   RMS（除以 √cnt，与距离同单位，消除 per-channel σ 与 RGB 欧氏距离的 √3
   维度错配）。窗口随选区增量重算、单调不减。
   返回 Uint8Array(w*h) 二值选区（1=选中）。行扫描泛洪，带 8e6 像素安全上限。 */
const WAND_MAX_PX = 8000000;
const WAND_K = 2.5;

export function floodSelect(
  w: number, h: number, rgba: Uint8ClampedArray,
  sx: number, sy: number, tol: number, edgeThresh: number,
): Uint8Array {
  const n = w * h;
  const sel = new Uint8Array(n);
  if (sx < 0 || sx >= w || sy < 0 || sy >= h) return sel;
  const eth2 = edgeThresh * edgeThresh;
  // 边缘屏障 =「穿越式」：相邻两像素灰差 > edgeThresh 才阻断跨越，不拦同侧像素。
  const edgeR = new Uint8Array(n), edgeD = new Uint8Array(n);
  for (let y = 0; y < h; y++) {
    const base = y * w;
    for (let x = 0; x < w; x++) {
      const i = base + x;
      const g = rgba[i * 4] * 0.299 + rgba[i * 4 + 1] * 0.587 + rgba[i * 4 + 2] * 0.114;
      if (x + 1 < w) {
        const gr = rgba[(i + 1) * 4] * 0.299 + rgba[(i + 1) * 4 + 1] * 0.587 + rgba[(i + 1) * 4 + 2] * 0.114;
        const d = g - gr;
        if (d * d > eth2) edgeR[i] = 1;
      }
      if (y + 1 < h) {
        const gd = rgba[(i + w) * 4] * 0.299 + rgba[(i + w) * 4 + 1] * 0.587 + rgba[(i + w) * 4 + 2] * 0.114;
        const d2 = g - gd;
        if (d2 * d2 > eth2) edgeD[i] = 1;
      }
    }
  }
  // 7×7 种子窗初始化运行统计（避免以单像素为均值时的过早偏置）
  let sumR = 0, sumG = 0, sumB = 0, sqR = 0, sqG = 0, sqB = 0, cnt = 0;
  for (let dy = -3; dy <= 3; dy++) {
    const py = sy + dy;
    if (py < 0 || py >= h) continue;
    for (let dx = -3; dx <= 3; dx++) {
      const px = sx + dx;
      if (px < 0 || px >= w) continue;
      const o = (py * w + px) * 4;
      const r = rgba[o], g = rgba[o + 1], b = rgba[o + 2];
      sumR += r; sumG += g; sumB += b;
      sqR += r * r; sqG += g * g; sqB += b * b; cnt++;
    }
  }
  let muR = sumR / cnt, muG = sumG / cnt, muB = sumB / cnt;
  function spread() {
    const vr = Math.max(0, sqR - cnt * muR * muR);
    const vg = Math.max(0, sqG - cnt * muG * muG);
    const vb = Math.max(0, sqB - cnt * muB * muB);
    return Math.sqrt(vr + vg + vb) / Math.sqrt(cnt);
  }
  let window = Math.max(tol, WAND_K * spread());
  let win2 = window * window, since = 0;
  function addStat(i: number) {
    const o = i * 4;
    const r = rgba[o], g = rgba[o + 1], b = rgba[o + 2];
    sumR += r; sumG += g; sumB += b;
    sqR += r * r; sqG += g * g; sqB += b * b; cnt++;
    muR = sumR / cnt; muG = sumG / cnt; muB = sumB / cnt;
    if ((++since & 255) === 0) {   // 每 256 像素重算一次窗口，单调不减
      const nw = Math.max(tol, WAND_K * spread());
      if (nw > window) { window = nw; win2 = window * window; }
    }
  }
  function colorOk(i: number) {
    const o = i * 4;
    const dr = rgba[o] - muR, dg = rgba[o + 1] - muG, db = rgba[o + 2] - muB;
    return dr * dr + dg * dg + db * db <= win2;
  }
  sel[sy * w + sx] = 1;
  const stack: number[] = [sx, sy];
  let count = 0;
  while (stack.length) {
    const yy = stack.pop() as number, xx = stack.pop() as number;
    let xl = xx;
    while (xl > 0) {
      const i2 = yy * w + (xl - 1);
      if (!sel[i2] && !edgeR[i2] && colorOk(i2)) { sel[i2] = 1; addStat(i2); xl--; }
      else break;
    }
    let xr = xx;
    while (xr < w - 1) {
      const i3 = yy * w + (xr + 1);
      if (!sel[i3] && !edgeR[i3 - 1] && colorOk(i3)) { sel[i3] = 1; addStat(i3); xr++; }
      else break;
    }
    for (let c = xl; c <= xr; c++) {
      count++;
      if (count > WAND_MAX_PX) return sel;   // 安全上限
      const idx = yy * w + c;
      if (yy > 0) {
        const up = (yy - 1) * w + c;
        if (!sel[up] && !edgeD[idx - w] && colorOk(up)) { sel[up] = 1; addStat(up); stack.push(c, yy - 1); }
      }
      if (yy < h - 1) {
        const dn = (yy + 1) * w + c;
        if (!sel[dn] && !edgeD[idx] && colorOk(dn)) { sel[dn] = 1; addStat(dn); stack.push(c, yy + 1); }
      }
    }
  }
  return sel;
}

/* ---------------- 洞填充：把无法从边界背景到达的 0 像素填成 1 ----------------
   使选区单连通（掩码=多边形并集模型表达不了洞）。原地返回新数组。 */
export function fillRegionHoles(mask: Uint8Array, w: number, h: number): Uint8Array {
  const n = w * h;
  const out = new Uint8Array(n);
  const stack: number[] = [];
  for (let x = 0; x < w; x++) {
    if (mask[x] === 0 && !out[x]) { out[x] = 1; stack.push(x); }
    const b = (h - 1) * w + x;
    if (mask[b] === 0 && !out[b]) { out[b] = 1; stack.push(b); }
  }
  for (let y = 0; y < h; y++) {
    const l = y * w, r = y * w + w - 1;
    if (mask[l] === 0 && !out[l]) { out[l] = 1; stack.push(l); }
    if (mask[r] === 0 && !out[r]) { out[r] = 1; stack.push(r); }
  }
  while (stack.length) {
    const i = stack.pop() as number;
    const cx = i % w, cy = (i / w) | 0;
    if (cx > 0) { const a = i - 1; if (mask[a] === 0 && !out[a]) { out[a] = 1; stack.push(a); } }
    if (cx < w - 1) { const d = i + 1; if (mask[d] === 0 && !out[d]) { out[d] = 1; stack.push(d); } }
    if (cy > 0) { const u = i - w; if (mask[u] === 0 && !out[u]) { out[u] = 1; stack.push(u); } }
    if (cy < h - 1) { const dn = i + w; if (mask[dn] === 0 && !out[dn]) { out[dn] = 1; stack.push(dn); } }
  }
  const res = mask.slice();
  for (let p = 0; p < n; p++) if (mask[p] === 0 && !out[p]) res[p] = 1;
  return res;
}

/* ---------------- 外轮廓提取（Moore 邻居追踪） ----------------
   返回边界像素中心点序（处理坐标）。要求区域四周有 ≥1px 背景以保证闭环。 */
export function traceContour(mask: Uint8Array, w: number, h: number): Pt[] {
  let sx = -1, sy = -1;
  for (let y = 0; y < h && sx < 0; y++) {
    for (let x = 0; x < w; x++) {
      if (mask[y * w + x]) { sx = x; sy = y; break; }
    }
  }
  if (sx < 0) return [];
  const dx = [1, 1, 0, -1, -1, -1, 0, 1], dy = [0, 1, 1, 1, 0, -1, -1, -1];
  const pts: Pt[] = [];
  let bx = sx, by = sy, backDir = 4;   // 起点为最左上像素，西侧必为背景 → 回溯方向 W
  let guard = 0, MAXG = w * h * 4;
  for (;;) {
    pts.push([bx, by]);
    let found = false;
    for (let s = 1; s <= 8; s++) {
      const d = (backDir + s) % 8;
      const nx = bx + dx[d], ny = by + dy[d];
      if (nx >= 0 && nx < w && ny >= 0 && ny < h && mask[ny * w + nx]) {
        bx = nx; by = ny;
        backDir = (d + 4) % 8;
        found = true;
        break;
      }
    }
    if (!found) break;
    if (++guard > MAXG) break;
    if (bx === sx && by === sy) break;
  }
  return pts;
}

/* ---------------- 折线简化：RDP + 去共线/去重 ----------------
   tol 距离阈值（与折线同单位）。返回顶点数组（不含闭合重复点）。 */
export function simplifyPoly(pts: Pt[], tol: number): Pt[] {
  const n = pts.length;
  if (n <= 2) return pts.slice();
  const clean: Pt[] = [pts[0]];
  for (let i = 1; i < n; i++) {
    const p = pts[i], q = clean[clean.length - 1];
    if (p[0] !== q[0] || p[1] !== q[1]) clean.push(p);
  }
  if (clean.length > 1) {
    const f = clean[0], l = clean[clean.length - 1];
    if (f[0] === l[0] && f[1] === l[1]) clean.pop();
  }
  if (clean.length <= 2) return clean;
  const idx = rdp(clean, tol);
  const out: Pt[] = [];
  for (let k = 0; k < idx.length; k++) out.push(clean[idx[k]]);
  // 去共线点
  const res: Pt[] = [];
  for (let m = 0; m < out.length; m++) {
    const a = out[(m + out.length - 1) % out.length], b = out[m], c = out[(m + 1) % out.length];
    const cross = (b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0]);
    if (Math.abs(cross) > 1e-9) res.push(b);
  }
  return res.length >= 3 ? res : out;
}

function rdp(pts: Pt[], tol: number): number[] {
  const n = pts.length;
  const keep = new Uint8Array(n);
  keep[0] = keep[n - 1] = 1;
  const stack: [number, number][] = [[0, n - 1]];
  const tol2 = tol * tol;
  while (stack.length) {
    const seg = stack.pop() as [number, number];
    const a = seg[0], b = seg[1];
    let maxD = -1, idx = -1;
    for (let i = a + 1; i < b; i++) {
      const d = segDistSq(pts[a], pts[b], pts[i]);
      if (d > maxD) { maxD = d; idx = i; }
    }
    if (maxD > tol2 && idx > 0) {
      keep[idx] = 1;
      stack.push([a, idx], [idx, b]);
    }
  }
  const out: number[] = [];
  for (let k = 0; k < n; k++) if (keep[k]) out.push(k);
  return out;
}

function segDistSq(a: Pt, b: Pt, p: Pt): number {
  const dx = b[0] - a[0], dy = b[1] - a[1];
  const len2 = dx * dx + dy * dy;
  if (len2 === 0) {
    const d0 = p[0] - a[0], d1 = p[1] - a[1];
    return d0 * d0 + d1 * d1;
  }
  let t = ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / len2;
  t = Math.max(0, Math.min(1, t));
  const qx = a[0] + t * dx, qy = a[1] + t * dy;
  const rx = p[0] - qx, ry = p[1] - qy;
  return rx * rx + ry * ry;
}

/* ---------------- 光栅并集 + 连通分量合并（合并重叠/贴边区域） ----------------
   掩码 .tif 的栅格化语义本就是「重叠/贴边即并集」（见 rowIntervals）。这里把
   多个 ROI 光栅化成并集 mask，按连通分量重追踪轮廓：重叠/贴边的区域并成一个
   连通多边形、真正分开的区域各自保留 —— 供「合并重叠」按钮调用。 */
export function rasterMask(polys: Poly[], w: number, h: number): Uint8Array {
  const m = new Uint8Array(w * h);
  for (let r = 0; r < h; r++) {
    const gaps = rowIntervals(polys, r);
    for (let g = 0; g < gaps.length; g++) {
      const c0 = Math.max(0, Math.ceil(gaps[g][0] - EPS));
      const c1 = Math.min(w - 1, Math.floor(gaps[g][1] + EPS));
      if (c0 <= c1) m.fill(1, r * w + c0, r * w + c1 + 1);
    }
  }
  return m;
}

/* 连通分量标记：返回每个分量的像素索引列表（就地清零 work，避免另开标签数组）。
   （内部辅助，与 maskgen.js 一致不在导出符号里；mergeConnectedGen 自带 BFS 不用它） */
function connectedComponents(mask: Uint8Array, w: number, h: number): number[][] {
  const work = mask.slice();
  const comps: number[][] = [];
  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) {
      const i = y * w + x;
      if (!work[i]) continue;
      const list: number[] = [];
      const q = [i];
      work[i] = 0;
      while (q.length) {
        const cur = q.pop() as number;
        list.push(cur);
        const cx = cur % w, cy = (cur / w) | 0;
        if (cx > 0) { const a = cur - 1; if (work[a]) { work[a] = 0; q.push(a); } }
        if (cx < w - 1) { const b = cur + 1; if (work[b]) { work[b] = 0; q.push(b); } }
        if (cy > 0) { const c = cur - w; if (work[c]) { work[c] = 0; q.push(c); } }
        if (cy < h - 1) { const d = cur + w; if (work[d]) { work[d] = 0; q.push(d); } }
      }
      comps.push(list);
    }
  }
  return comps;
}

/* 合并重叠/贴边区域（协程版）：
   并集 mask（四周垫 1px 背景保证轮廓闭环）→ 洞填充 → 各连通分量在 **bbox 子图**
   重追踪轮廓 + RDP 简化 → 返回合并后的多边形（原坐标空间）。
   重循环按批让出主线程（gen 每批 yield {phase, progress}）：浏览器用
   mergeConnectedAsync 驱动可实时刷进度、UI 不冻结；Node/测试用 mergeConnected
   同步驱动到完成。每连通域只在自身 bbox 子图（+1px 背景）追踪，不再整图重扫/整图分配。 */
export interface MergeYield { phase: string; progress: number; }

export function* mergeConnectedGen(polys: Poly[], w: number, h: number): Generator<MergeYield, Poly[], void> {
  const pw = w + 2, ph = h + 2, n = pw * ph;
  const ROWS = 128;      // 栅格化每批行数
  const OPS = 1 << 17;   // BFS/扫描/填充每批操作数（~13 万，单批 <16ms 预算）
  const m = new Uint8Array(n);

  /* 阶段 1：并集栅格化（逐行，每 ROWS 行让出） */
  let r = 0;
  while (r < h) {
    const rEnd = Math.min(r + ROWS, h);
    for (; r < rEnd; r++) {
      const gaps = rowIntervals(polys, r);
      for (let g = 0; g < gaps.length; g++) {
        const c0 = Math.max(0, Math.ceil(gaps[g][0] - EPS));
        const c1 = Math.min(w - 1, Math.floor(gaps[g][1] + EPS));
        if (c0 <= c1) m.fill(1, (r + 1) * pw + c0 + 1, (r + 1) * pw + c1 + 2);
      }
    }
    yield { phase: '栅格化', progress: 0.25 * (r / h) };
  }

  /* 阶段 2：洞填充——从边框背景可达的 0 像素 BFS 标 out，不可达 0 就地补 1（每 OPS 让出） */
  const out = new Uint8Array(n);
  const stack: number[] = [];
  for (let x = 0; x < pw; x++) {
    if (!m[x]) { out[x] = 1; stack.push(x); }
    const bI = (ph - 1) * pw + x;
    if (!m[bI]) { out[bI] = 1; stack.push(bI); }
  }
  for (let yy = 0; yy < ph; yy++) {
    const lI = yy * pw, rI = yy * pw + pw - 1;
    if (!m[lI]) { out[lI] = 1; stack.push(lI); }
    if (!m[rI]) { out[rI] = 1; stack.push(rI); }
  }
  let visited = 0;
  while (stack.length) {
    let ops = 0;
    while (stack.length && ops++ < OPS) {
      const i = stack.pop() as number;
      visited++;
      const cx = i % pw, cy = (i / pw) | 0;
      if (cx > 0) { const a = i - 1; if (!m[a] && !out[a]) { out[a] = 1; stack.push(a); } }
      if (cx < pw - 1) { const d = i + 1; if (!m[d] && !out[d]) { out[d] = 1; stack.push(d); } }
      if (cy > 0) { const u = i - pw; if (!m[u] && !out[u]) { out[u] = 1; stack.push(u); } }
      if (cy < ph - 1) { const dn = i + pw; if (!m[dn] && !out[dn]) { out[dn] = 1; stack.push(dn); } }
    }
    yield { phase: '洞填充', progress: 0.25 + 0.25 * Math.min(1, visited / n) };
  }
  for (let p = 0; p < n; p++) if (!m[p] && !out[p]) m[p] = 1;

  /* 阶段 3：连通域（行扫描 + BFS，每 OPS 让出）——记像素表 + bbox，m 就地清零当 work */
  const comps: { list: number[]; box: [number, number, number, number] }[] = [];
  let y = 0, col = 0, flood = 0, scanned = 0;
  let list: number[] | null = null, bfs: number[] | null = null, box: [number, number, number, number] | null = null;
  let scanDone = false;
  while (!scanDone || bfs) {
    let ops2 = 0;
    while (ops2++ < OPS) {
      if (bfs) {
        if (bfs.length) {
          const ii = bfs.pop() as number;
          flood++;
          const fx = ii % pw, fy = (ii / pw) | 0;
          if (fx < (box as number[])[0]) (box as number[])[0] = fx; else if (fx > (box as number[])[2]) (box as number[])[2] = fx;
          if (fy < (box as number[])[1]) (box as number[])[1] = fy; else if (fy > (box as number[])[3]) (box as number[])[3] = fy;
          (list as number[]).push(ii);
          if (fx > 0) { const na = ii - 1; if (m[na]) { m[na] = 0; bfs.push(na); } }
          if (fx < pw - 1) { const nb = ii + 1; if (m[nb]) { m[nb] = 0; bfs.push(nb); } }
          if (fy > 0) { const nc = ii - pw; if (m[nc]) { m[nc] = 0; bfs.push(nc); } }
          if (fy < ph - 1) { const nd = ii + pw; if (m[nd]) { m[nd] = 0; bfs.push(nd); } }
        } else {
          comps.push({ list: list as number[], box: box as [number, number, number, number] });
          list = null; bfs = null; box = null;
        }
      } else {
        while (y < ph && col >= pw) { y++; col = 0; }
        if (y >= ph) { scanDone = true; break; }
        const i3 = y * pw + col;
        scanned++;
        if (m[i3]) { m[i3] = 0; list = []; box = [col, y, col, y]; bfs = [i3]; }
        else col++;
      }
    }
    yield { phase: '连通域', progress: 0.5 + 0.25 * Math.min(1, (scanned + flood) / (2 * n)) };
  }

  /* 阶段 4：逐连通域在 bbox 子图（+1px 背景）重追踪轮廓 + 简化；子图填充每 OPS 让出 */
  const outPts: Poly[] = [];
  for (let c = 0; c < comps.length; c++) {
    const cm = comps[c];
    const bw = cm.box[2] - cm.box[0] + 1, bh = cm.box[3] - cm.box[1] + 1;
    const sw2 = bw + 2, sh2 = bh + 2;
    const sub = new Uint8Array(sw2 * sh2);
    const l2 = cm.list;
    let li = 0;
    while (li < l2.length) {
      const liEnd = Math.min(li + OPS, l2.length);
      for (; li < liEnd; li++) {
        const gi = l2[li];
        const gx = gi % pw, gy = (gi / pw) | 0;
        sub[(gy - cm.box[1] + 1) * sw2 + (gx - cm.box[0] + 1)] = 1;
      }
      yield { phase: '轮廓提取', progress: 0.75 + 0.25 * ((c + li / l2.length) / comps.length) };
    }
    let poly = traceContour(sub, sw2, sh2);
    if (poly.length) {
      const offX = cm.box[0] - 2, offY = cm.box[1] - 2;
      const pts = new Array<Pt>(poly.length);
      for (let k = 0; k < poly.length; k++) pts[k] = [poly[k][0] + offX, poly[k][1] + offY];
      poly = simplifyPoly(pts, 0.5);
      if (poly.length >= 3) outPts.push(poly);
    }
  }
  return outPts;
}

/* 同步版（Node 测试 / 兼容）：直接驱动 gen 到完成，忽略 yield。 */
export function mergeConnected(polys: Poly[], w: number, h: number): Poly[] {
  const g = mergeConnectedGen(polys, w, h);
  let s = g.next();
  while (!s.done) s = g.next();
  return s.value as Poly[];
}

/* 协程版（浏览器主线程）：每批 setTimeout 让出可实时刷进度；onPhase 阶段名、onProgress 0..1。 */
export function mergeConnectedAsync(polys: Poly[], w: number, h: number, opts?: MergeAsyncOpts): Promise<Poly[]> {
  opts = opts || {};
  const g = mergeConnectedGen(polys, w, h);
  const onPhase = opts.onPhase || null, onProgress = opts.onProgress || null;
  let lastPct = -1;
  function emit(s: MergeYield | undefined) {
    if (!s) return;
    if (onPhase && s.phase) onPhase(s.phase);
    if (onProgress && typeof s.progress === 'number') {
      const pct = Math.round(s.progress * 100);
      if (pct !== lastPct) { lastPct = pct; onProgress(s.progress); }
    }
  }
  return new Promise((resolve, reject) => {
    (function step() {
      let s: IteratorResult<MergeYield, Poly[]>;
      try { s = g.next(); }
      catch (e) { reject(e); return; }
      if (s.done) { resolve(s.value as Poly[]); return; }
      emit(s.value);
      setTimeout(step, 0);
    })();
  });
}

/* ---------------- 默认导出（对齐 maskgen.js 的 UMD 返回对象，接口不变） ---------------- */
const MaskGen = {
  polygonCentroid,
  buildMaskTxt,
  rasterRows,
  buildTiff,
  floodSelect,
  fillRegionHoles,
  traceContour,
  simplifyPoly,
  rasterMask,
  mergeConnected,
  mergeConnectedAsync,
};

export default MaskGen;
