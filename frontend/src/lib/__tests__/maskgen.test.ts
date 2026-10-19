/**
 * maskgen.test.ts — maskgen.js 17 项 e2e 纯算法回归的 Vitest 直译
 * ------------------------------------------------------------------
 * 逐用例对齐 .e2e/test-maskgen.js（断言/输入/期望值全部原样保留），
 * 保证「移植不重写」的回归权威仍是旧实现那套行为。
 * 覆盖：质心/退化、txt 格式、填充约定（边界含入）、重叠并集、连通合并、洞填充、
 * 轮廓+简化、魔棒自适应生长/边缘屏障、buildTiff 产物结构。
 */
import { describe, it, expect } from 'vitest';
import MaskGen from '../maskgen.js';
import type { Poly } from '../maskgen.js';
import pako from '../../vendor/pako.min.js';

// 构造 RGBA（fillFn(x,y) -> [r,g,b]）
function makeRgba(w: number, h: number, fillFn: (x: number, y: number) => [number, number, number]): Uint8ClampedArray {
  const a = new Uint8ClampedArray(w * h * 4);
  for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
    const o = (y * w + x) * 4;
    const c = fillFn(x, y);
    a[o] = c[0]; a[o + 1] = c[1]; a[o + 2] = c[2]; a[o + 3] = 255;
  }
  return a;
}
function countOnes(m: Uint8Array): number {
  let n = 0;
  for (let i = 0; i < m.length; i++) if (m[i]) n++;
  return n;
}

const SQ: Poly = [[2, 2], [7, 2], [7, 7], [2, 7]];   // 方形 [2,2]..[7,7] 边界含入 6×6

describe('polygonCentroid', () => {
  it('三角形', () => {
    const c = MaskGen.polygonCentroid([[0, 0], [4, 0], [0, 4]]);
    expect(Math.abs(c[0] - 4 / 3)).toBeLessThanOrEqual(1e-6);
    expect(Math.abs(c[1] - 4 / 3)).toBeLessThanOrEqual(1e-6);
  });
  it('退化→包围盒中心', () => {
    const c = MaskGen.polygonCentroid([[0, 0], [0, 0], [4, 0]]);
    expect(Math.abs(c[0] - 2)).toBeLessThanOrEqual(1e-6);
    expect(Math.abs(c[1] - 0)).toBeLessThanOrEqual(1e-6);
  });
});

describe('buildMaskTxt 格式（全角头/CRLF/2dp）', () => {
  it('头部与数据行', () => {
    const txt = MaskGen.buildMaskTxt(10, 10, [[[0, 0], [4, 0], [0, 4]]]);
    const lines = txt.split('\r\n');
    expect(lines[0]).toBe('＃掩膜中心点坐标（X，Y）');
    expect(lines[1]).toBe('＃掩膜编号，X坐标，Y坐标');
    expect(lines[2]).toBe('1,1.33,1.33');
    expect(lines[3]).toBe('');   // 应以 \r\n 结尾且无多余行
  });
});

describe('填充约定', () => {
  it('方形边界含入 = 36px', () => {
    const m = MaskGen.rasterMask([SQ], 16, 16);
    expect(countOnes(m)).toBe(36);
    expect(m[2 * 16 + 2]).toBe(1);
    expect(m[7 * 16 + 7]).toBe(1);   // 顶点像素应含入
    expect(m[1 * 16 + 2]).toBe(0);
    expect(m[8 * 16 + 2]).toBe(0);   // 边界外应排除
  });
  it('三角形 4+3+2+1 = 10px', () => {
    const tri: Poly = [[1, 1], [4, 1], [1, 4]];
    const m = MaskGen.rasterMask([tri], 8, 8);
    expect(countOnes(m)).toBe(10);
  });
  it('重叠多边形并集不重复计（逐多边形 even-odd + UNION）', () => {
    const m = MaskGen.rasterMask(
      [[[2, 2], [6, 2], [6, 6], [2, 6]], [[4, 4], [8, 4], [8, 8], [4, 8]]],
      12, 12,
    );
    // 25 + 25 - 9(重叠) = 41
    expect(countOnes(m)).toBe(41);
  });
});

describe('mergeConnected', () => {
  it('重叠→1 区', () => {
    const out = MaskGen.mergeConnected([[[2, 2], [6, 2], [6, 6], [2, 6]], [[4, 4], [8, 4], [8, 8], [4, 8]]], 12, 12);
    expect(out.length).toBe(1);
  });
  it('相接→合并', () => {
    const out = MaskGen.mergeConnected([[[2, 2], [6, 2], [6, 6], [2, 6]], [[6, 2], [10, 2], [10, 6], [6, 6]]], 12, 12);
    expect(out.length).toBe(1);
  });
  it('包含→吞噬', () => {
    const out = MaskGen.mergeConnected([[[2, 2], [10, 2], [10, 10], [2, 10]], [[4, 4], [6, 4], [6, 6], [4, 6]]], 14, 14);
    expect(out.length).toBe(1);
  });
  it('分离→2 区', () => {
    const out = MaskGen.mergeConnected([[[2, 2], [5, 2], [5, 5], [2, 5]], [[8, 8], [11, 8], [11, 11], [8, 11]]], 14, 14);
    expect(out.length).toBe(2);
  });
});

describe('fillRegionHoles 环形洞填充', () => {
  it('洞被填充、背景保持 0', () => {
    const w = 20, h = 20;
    const mask = new Uint8Array(w * h);
    for (let y = 4; y <= 15; y++) for (let x = 4; x <= 15; x++) mask[y * w + x] = 1;
    for (let y = 8; y <= 11; y++) for (let x = 8; x <= 11; x++) mask[y * w + x] = 0;  // 洞
    const out = MaskGen.fillRegionHoles(mask, w, h);
    expect(out[9 * w + 9]).toBe(1);      // 洞应被填充
    expect(out[0]).toBe(0);
    expect(out[19 * w + 19]).toBe(0);    // 背景仍为 0
  });
});

describe('traceContour + simplifyPoly 边界轮廓', () => {
  it('5×5 方块 → 4 角点', () => {
    const w = 20, h = 20;
    const mask = new Uint8Array(w * h);
    for (let y = 5; y <= 9; y++) for (let x = 5; x <= 9; x++) mask[y * w + x] = 1;
    const pts = MaskGen.traceContour(mask, w, h);
    expect(pts.length).toBeGreaterThanOrEqual(8);
    expect(pts[0][0]).toBe(5);
    expect(pts[0][1]).toBe(5);   // 起点应为最左上填充像素
    let top = 0, bottom = 0, left = 0, right = 0;   // 四边都要有边界点
    for (const p of pts) {
      expect(p[0]).toBeGreaterThanOrEqual(5);
      expect(p[0]).toBeLessThanOrEqual(9);
      expect(p[1]).toBeGreaterThanOrEqual(5);
      expect(p[1]).toBeLessThanOrEqual(9);
      if (p[1] === 5) top++; if (p[1] === 9) bottom++;
      if (p[0] === 5) left++; if (p[0] === 9) right++;
    }
    expect(top).toBeGreaterThan(0);
    expect(bottom).toBeGreaterThan(0);
    expect(left).toBeGreaterThan(0);
    expect(right).toBeGreaterThan(0);   // 轮廓应覆盖四边
    const poly = MaskGen.simplifyPoly(pts, 0.5);
    expect(poly.length).toBe(4);   // 5×5 方块简化后应 4 角点
    let a2 = 0;
    for (let i = 0; i < poly.length; i++) {
      const j = (i + 1) % poly.length;
      a2 += poly[i][0] * poly[j][1] - poly[j][0] * poly[i][1];
    }
    expect(Math.abs(a2)).toBeGreaterThan(0);   // 简化多边形应非退化
  });
});

describe('魔棒 floodSelect', () => {
  it('实心色块全选 + 背景零泄漏', () => {
    const w = 150, h = 150;
    const rgba = makeRgba(w, h, (x, y) => (x >= 30 && x < 120 && y >= 30 && y < 120) ? [100, 100, 100] : [200, 200, 200]);
    const sel = MaskGen.floodSelect(w, h, rgba, 75, 75, 20, 64);
    const cnt = countOnes(sel);
    expect(cnt).toBeGreaterThanOrEqual(8000);   // 应选完整块 8100
    expect(sel[145 * w + 145]).toBe(0);
    expect(sel[0]).toBe(0);                     // 背景零泄漏
  });
  it('纹理自适应区域生长（±15 细纹理整块）', () => {
    const w = 150, h = 150;
    const rgba = makeRgba(w, h, (x, y) => {
      const inPatch = x >= 30 && x < 120 && y >= 30 && y < 120;
      if (!inPatch) return [200, 200, 200];
      return ((((x >> 1) + (y >> 1)) & 1) === 0) ? [85, 85, 85] : [115, 115, 115];
    });
    const sel = MaskGen.floodSelect(w, h, rgba, 75, 75, 20, 64);
    const cnt = countOnes(sel);
    expect(cnt).toBeGreaterThanOrEqual(7000);   // 自适应窗口应覆盖整块纹理区
    expect(sel[145 * w + 145]).toBe(0);         // 背景零泄漏
  });
  it('边缘屏障不跨越强边缘', () => {
    const w = 150, h = 150;
    const rgba = makeRgba(w, h, (x, y) => (x < 120) ? [100, 100, 100] : [200, 200, 200]);
    const sel = MaskGen.floodSelect(w, h, rgba, 50, 75, 20, 64);
    expect(sel[75 * w + 130]).toBe(0);          // 屏障右侧不应选中
    expect(countOnes(sel)).toBeGreaterThanOrEqual(16000);   // 左侧应基本全选
  });
  it('种子在边界不崩', () => {
    const w = 50, h = 50;
    const rgba = makeRgba(w, h, () => [150, 150, 150]);
    const sel = MaskGen.floodSelect(w, h, rgba, 0, 0, 20, 64);
    expect(sel[0]).toBe(1);
    expect(sel.length).toBe(w * h);
  });
});

describe('buildTiff 产物结构（classic TIFF / Deflate / 0-255）', () => {
  it('解压后 256 字节且方形 36 白像素', async () => {
    const tif = await MaskGen.buildTiff(16, 16, [SQ]);
    expect(tif[0]).toBe(0x49);
    expect(tif[1]).toBe(0x49);
    expect(tif[2]).toBe(42);   // II+42 小端 classic TIFF
    const dv = new DataView(tif.buffer, tif.byteOffset, tif.byteLength);
    const ifd = dv.getUint32(4, true);
    const n = dv.getUint16(ifd, true);
    const tags: Record<number, { type: number; count: number; value: number }> = {};
    for (let i = 0; i < n; i++) {
      const e = ifd + 2 + i * 12;
      const tag = dv.getUint16(e, true);
      tags[tag] = { type: dv.getUint16(e + 2, true), count: dv.getUint32(e + 4, true), value: dv.getUint32(e + 8, true) };
    }
    expect(tags[256].value).toBe(16);
    expect(tags[257].value).toBe(16);
    expect(tags[259].value).toBe(8);       // Deflate(8)
    expect(tags[262].value).toBe(1);       // BlackIsZero
    expect(tags[258].value).toBe(8);
    expect(tags[277].value).toBe(1);       // 8bit 单波段
    const off = tags[273].value, len = tags[279].value;
    const comp = new Uint8Array(tif.buffer, off, len);
    const raw = pako.inflate(comp);
    expect(raw.length).toBe(256);
    let white = 0;
    for (let i = 0; i < raw.length; i++) if (raw[i] === 255) white++;
    expect(white).toBe(36);
    expect(raw[0]).toBe(0);
    expect(raw[15]).toBe(0);
    expect(raw[2 * 16 + 2]).toBe(255);
    expect(raw[7 * 16 + 7]).toBe(255);   // 方形顶点应为 255
  });
});

// Pt/Poly 类型已在上方用例中实际使用（SQ/tri 标注），此处不重复声明
