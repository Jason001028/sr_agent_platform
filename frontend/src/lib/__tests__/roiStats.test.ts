/**
 * roiStats.test.ts — ROI 确定性统计纯函数（阶段6，Node 环境）
 * ------------------------------------------------------------------
 * 覆盖 buildStats：空图 / 退化点 / 全像元 / 多边形数值正确性 / 多 ROI 独立 /
 * 全透明 / 抽行边界；roiOrigGeom：缩略图→原图像素几何摘要。
 * 判定语义与掩码栅格化同源（rowIntervals 闭区间），这里只验证纯数值。
 */
import { describe, it, expect } from 'vitest';
import { buildStats, roiOrigGeom, STAT_MAX_PX } from '../roiStats.js';
import type { RoiImage } from '../roiStats.js';
import type { Poly } from '../maskgen.js';

/** 从灰度函数建 RoiImage：返回 undefined 的像元 → alpha=0（不计入统计）。 */
function imgOf(W: number, H: number, g: (c: number, r: number) => number | undefined): RoiImage {
  const data = new Uint8ClampedArray(W * H * 4);
  for (let r = 0; r < H; r++) {
    for (let c = 0; c < W; c++) {
      const v = g(c, r);
      if (v === undefined) continue;           // alpha 保持 0
      const o = (r * W + c) * 4;
      data[o] = v; data[o + 1] = v; data[o + 2] = v; data[o + 3] = 255;
    }
  }
  return { width: W, height: H, data };
}

/** 一个覆盖 [x0,x1]×[y0,y1]（浮点边界，含端点语义）的四边形。 */
function rectPoly(x0: number, y0: number, x1: number, y1: number): Poly {
  return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]];
}

describe('buildStats 空 / 退化输入', () => {
  it('空图（0 宽）→ 空统计', () => {
    const s = buildStats(imgOf(0, 0, () => 0), rectPoly(0, 0, 2, 2));
    expect(s.n).toBe(0);
    expect(s.min).toBeNull();
    expect(s.sampled).toBe(false);
    expect(s.rowSpan).toBe(0);
  });

  it('退化多边形（顶点 < 3，点/线段）→ 空统计', () => {
    const img = imgOf(4, 4, () => 100);
    expect(buildStats(img, [[1, 1]]).n).toBe(0);          // 单点
    expect(buildStats(img, [[1, 1], [2, 2]]).n).toBe(0);  // 线段
  });
});

describe('buildStats 全像元覆盖', () => {
  it('多边形包住整图 → n = W*H，灰度 255 全像元', () => {
    const s = buildStats(imgOf(3, 3, () => 255), rectPoly(-1, -1, 4, 4));
    expect(s.n).toBe(9);
    expect(s.min).not.toBeNull();
    expect(s.min!).toBeCloseTo(255, 5);
    expect(s.max!).toBeCloseTo(255, 5);
    expect(s.mean!).toBeCloseTo(255, 5);
    expect(s.hiPct).toBe(100);
    expect(s.clipPct).toBe(100);
    expect(s.rowsSampled).toBe(3);   // 3 行全命中
  });
});

describe('buildStats 多边形数值正确性（与手算对照）', () => {
  it('单行 4 像元灰度 100/150/200/255 → n/min/max/mean/占比', () => {
    // 4×1 图：一行四像素；poly 浮点范围盖住整行 → 计入 4 像元
    const s = buildStats(
      imgOf(4, 1, (c) => [100, 150, 200, 255][c]),
      rectPoly(-0.5, -0.5, 3.5, 0.5),
    );
    expect(s.n).toBe(4);
    expect(s.min!).toBeCloseTo(100, 5);
    expect(s.max!).toBeCloseTo(255, 5);
    expect(s.mean!).toBeCloseTo(176.25, 5);   // (100+150+200+255)/4
    expect(s.std!).toBeCloseTo(Math.sqrt((100 ** 2 + 150 ** 2 + 200 ** 2 + 255 ** 2) / 4 - 176.25 ** 2), 4);
    expect(s.hiPct).toBe(50);                  // ≥200：200,255
    expect(s.clipPct).toBe(25);                // ≥250：255
    expect(s.rowSpan).toBe(1);
    expect(s.rowsSampled).toBe(1);
  });

  it('多边形部分覆盖 → 只计 bbox 闭区间内像元（左半边 100 vs 右半边 255）', () => {
    // 全 255 的 4×1 图，但 ROI 只盖左边两列（x ∈ [-0.5, 1.5]）→ n=2 均值 255
    const s = buildStats(
      imgOf(4, 1, () => 255),
      rectPoly(-0.5, -0.5, 1.5, 0.5),
    );
    expect(s.n).toBe(2);
    expect(s.mean!).toBeCloseTo(255, 5);
    // 只盖右半列（x∈[2.5,3.5] 之外再加些范围仍 clamp 到图像）→ 也 2 像元
    const s2 = buildStats(imgOf(4, 1, () => 255), rectPoly(1.9, -0.5, 4.5, 0.5));
    expect(s2.n).toBe(2);
  });
});

describe('buildStats 多 ROI 独立 + 界外', () => {
  it('两块不相交 ROI 各自统计不受彼此影响', () => {
    // 4×4 图：左上 2×2 = 50，右下 2×2 = 200（else 中间像素淡灰）
    const img = imgOf(4, 4, (c, r) => (c < 2 && r < 2 ? 50 : (c >= 2 && r >= 2 ? 200 : 128)));
    const tl = buildStats(img, rectPoly(-0.5, -0.5, 1.5, 1.5));   // ROI#1 左上
    const br = buildStats(img, rectPoly(1.5, 1.5, 3.5, 3.5));     // ROI#2 右下
    expect(tl.n).toBe(4);
    expect(tl.mean!).toBeCloseTo(50, 5);
    expect(br.n).toBe(4);
    expect(br.mean!).toBeCloseTo(200, 5);
  });

  it('多边形整体在图像外 → 空统计（n=0 不崩）', () => {
    const s = buildStats(imgOf(4, 4, () => 100), rectPoly(10, 10, 20, 20));
    expect(s.n).toBe(0);
    expect(s.sampled).toBe(false);
  });
});

describe('buildStats 全透明像元不计入', () => {
  it('alpha=0 区域内像元 → n=0；半透明区也不计', () => {
    const img: RoiImage = {
      width: 3, height: 3,
      data: (() => {
        const d = new Uint8ClampedArray(3 * 3 * 4);
        // 全图 alpha 0；只 (1,1) alpha=255 且 128 灰
        d[(1 * 3 + 1) * 4 + 3] = 255;
        d[(1 * 3 + 1) * 4] = 128; d[(1 * 3 + 1) * 4 + 1] = 128; d[(1 * 3 + 1) * 4 + 2] = 128;
        return d;
      })(),
    };
    const s = buildStats(img, rectPoly(-1, -1, 4, 4));
    expect(s.n).toBe(1);           // 只有那个不透明像元
    expect(s.mean!).toBeCloseTo(128, 5);
  });
});

describe('buildStats 抽行边界（sampling）', () => {
  it('bbox 面积 == maxPx 时不抽行（stride=1，rowsSampled=rowSpan）', () => {
    // W=1、12 行的竖条：rowSpan*bw = 12*3 = 36 == maxPx → 不抽行
    const img = imgOf(1, 12, () => 128);
    const s = buildStats(img, rectPoly(-0.5, -0.5, 0.5, 11.5), { maxPx: 36 });
    expect(s.sampled).toBe(false);
    expect(s.stride).toBe(1);
    expect(s.rowSpan).toBe(12);
    expect(s.rowsSampled).toBe(12);
    expect(s.n).toBe(12);
  });

  it('bbox 面积 > maxPx 时确定性等距抽行', () => {
    const img = imgOf(1, 12, () => 128);
    const s = buildStats(img, rectPoly(-0.5, -0.5, 0.5, 11.5), { maxPx: 15 });
    expect(s.sampled).toBe(true);
    expect(s.stride).toBe(3);          // ceil(36/15)
    expect(s.rowsSampled).toBe(4);     // 行 0,3,6,9
    expect(s.n).toBe(4);
    expect(s.mean!).toBeCloseTo(128, 5);
  });

  it('默认 maxPx 即 STAT_MAX_PX（模块常量单源；普通 ROI 不触发抽行）', () => {
    expect(STAT_MAX_PX).toBe(4_000_000);
    const small = buildStats(imgOf(3, 3, () => 255), rectPoly(-1, -1, 4, 4));
    expect(small.sampled).toBe(false);   // 小 ROI 未误伤
  });
});

describe('roiOrigGeom：缩略图多边形 → 原图像素几何摘要', () => {
  it('tw==W / th==H（恒等缩放）→ 顶点 round 后 bbox 含端点计数', () => {
    const g = roiOrigGeom(rectPoly(0.4, 0.4, 3.6, 2.6), 10, 10, 10, 10);
    // 顶点 round：(0,0)(4,0)(4,3)(0,3) → w=4-0+1=5, h=3-0+1=4
    expect(g.w).toBe(5);
    expect(g.h).toBe(4);
    expect(g.area).toBe(12);            // 4×3 鞋带
  });

  it('非恒等缩放按 thumbToOrig 换算并 clamp 到 [0,W-1]', () => {
    // tw=2 → 仅 0/1 两个 thumb 索引，映射到 W=5 原图：0→0、1→4
    const g = roiOrigGeom(rectPoly(0, 0, 1, 1), 5, 5, 2, 2);
    expect(g.w).toBe(5);                // 0..4 全跨度
    expect(g.h).toBe(5);
    expect(g.area).toBe(16);            // 4×4
  });

  it('退化输入 → 零几何', () => {
    expect(roiOrigGeom([[1, 1]], 5, 5, 5, 5).w).toBe(0);
    expect(roiOrigGeom(rectPoly(0, 0, 1, 1), 0, 5, 5, 5).area).toBe(0);
    expect(roiOrigGeom(rectPoly(0, 0, 1, 1), 5, 5, 0, 5).h).toBe(0);
  });
});
