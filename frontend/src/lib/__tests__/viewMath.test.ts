/**
 * viewMath.test.ts — 视图/坐标纯函数回归（直译 tif-viewer.html 的 fit/映射/定位/命中）
 * ------------------------------------------------------------------
 * 用例对齐 HTML 行为：fit 取 min(...,1) + 居中；thumbToOrig round+clamp；
 * locateView 居中且 scale<1 升到 1；wheelZoom 锚点不变；pointInPoly 射线法边界。
 */
import { describe, it, expect } from 'vitest';
import {
  fitView, thumbToScreen, mouseToThumb, thumbToOrig, origToThumb, locateView, wheelZoom,
  pointInPoly, hitRoi,
} from '../viewMath.js';
import type { Poly } from '../maskgen.js';

describe('fitView（适配 + 居中）', () => {
  it('thumb 大于画布 → scale 缩到能放下', () => {
    const v = fitView(400, 200, 100, 100);
    expect(v.scale).toBeCloseTo(0.25);            // min(100/400, 100/200, 1)
    expect(v.ox).toBeCloseTo(0);                   // 100 - 400*0.25 = 0
    expect(v.oy).toBeCloseTo(25);                  // (100 - 200*0.25)/2 = 25
  });
  it('thumb 小于画布 → scale=1 且居中', () => {
    const v = fitView(100, 50, 400, 300);
    expect(v.scale).toBe(1);
    expect(v.ox).toBe(150);
    expect(v.oy).toBe(125);
  });
  it('正方形进方形 → 满铺', () => {
    const v = fitView(100, 100, 300, 300);
    expect(v.scale).toBe(1);
    expect(v.ox).toBe(100);
    expect(v.oy).toBe(100);
  });
});

describe('thumbToScreen / mouseToThumb（互为逆映射）', () => {
  const view = { scale: 2, ox: 30, oy: 40 };
  it('缩略图坐标 → 屏幕坐标', () => {
    expect(thumbToScreen(view, 10, 20)).toEqual([50, 80]);   // 30+10*2=50, 40+20*2=80
  });
  it('鼠标屏幕坐标 → 缩略图坐标（rect 偏移抵消）', () => {
    const rect = { left: 100, top: 50 };
    const p = mouseToThumb(view, 100 + 50, 50 + 80, rect);
    expect(p[0]).toBeCloseTo(10);
    expect(p[1]).toBeCloseTo(20);
  });
});

describe('thumbToOrig（反算原图像素，round + clamp）', () => {
  it('端点映射精确：缩略图两端 ↔ 原图两端', () => {
    // 8192 宽缩略图 ↔ 24739 宽原图：tw-1=8191 ↔ W-1=24738
    expect(thumbToOrig(0, 0, 24739, 24199, 8192, 8192)).toEqual([0, 0]);
    expect(thumbToOrig(8191, 8191, 24739, 24199, 8192, 8192)).toEqual([24738, 24198]);
  });
  it('中点 round 正确', () => {
    // tx = (tw-1)/2 = 4095.5 → round((4095.5)*24738/8191) = round(12369.0..) → 中点附近
    const [ox] = thumbToOrig(4095.5, 0, 24739, 24199, 8192, 8192);
    expect(ox).toBeGreaterThanOrEqual(12368);
    expect(ox).toBeLessThanOrEqual(12370);
  });
  it('越界坐标 clamp 到图内', () => {
    expect(thumbToOrig(-100, -100, 100, 100, 50, 50)).toEqual([0, 0]);
    expect(thumbToOrig(999, 999, 100, 100, 50, 50)).toEqual([99, 99]);
  });
  it('tw<=1 退化为 0（HTML `tw <= 1 ? 0 : ...`）', () => {
    expect(thumbToOrig(0, 0, 100, 100, 1, 1)).toEqual([0, 0]);
    expect(thumbToOrig(5, 5, 100, 100, 1, 50)).toEqual([0, 10]);   // ox=0(tw=1)；oy=round(5*99/49)=10
  });
});

describe('origToThumb / locateView（像素定位）', () => {
  it('原图坐标 → 缩略图坐标（线性，不 round）', () => {
    expect(origToThumb(50, 25, 100, 50, 200, 100)).toEqual([25, 12.5]);
  });
  it('locateView：scale≥1 时居中到目标', () => {
    const v = locateView(2, 100, 50, 800, 600);
    expect(v).toEqual({ scale: 2, ox: 800 / 2 - 100 * 2, oy: 600 / 2 - 50 * 2 });
  });
  it('locateView：scale<1 时升到 1 再居中（HTML `if (view.scale < 1) view.scale = 1`）', () => {
    const v = locateView(0.5, 100, 50, 800, 600);
    expect(v.scale).toBe(1);
    expect(v.ox).toBe(800 / 2 - 100);
    expect(v.oy).toBe(600 / 2 - 50);
  });
});

describe('wheelZoom（以鼠标为锚点）', () => {
  it('放大后锚点像素不移动', () => {
    const view = { scale: 1, ox: 0, oy: 0 };
    const mx = 300, my = 200;
    const z = wheelZoom(view, mx, my, 1.2);
    expect(z.scale).toBeCloseTo(1.2);
    // 锚点不变：鼠标 (mx,my) 缩放前后指向同一缩略图像素 (mx - ox)/scale
    expect((mx - z.ox) / z.scale).toBeCloseTo(mx);
    expect((my - z.oy) / z.scale).toBeCloseTo(my);
  });
  it('缩小（factor=1/1.2）', () => {
    const z = wheelZoom({ scale: 12, ox: 10, oy: 20 }, 0, 0, 1 / 1.2);
    expect(z.scale).toBeCloseTo(10);
  });
  it('scale 夹在 [0.05, 64]', () => {
    expect(wheelZoom({ scale: 0.01, ox: 0, oy: 0 }, 0, 0, 1.2).scale).toBe(0.05);
    expect(wheelZoom({ scale: 100, ox: 0, oy: 0 }, 0, 0, 1.2).scale).toBe(64);
  });
});

describe('pointInPoly / hitRoi（射线法命中）', () => {
  const square: Poly = [[0, 0], [10, 0], [10, 10], [0, 10]];
  it('内部/外部/边界', () => {
    expect(pointInPoly(5, 5, square)).toBe(true);
    expect(pointInPoly(11, 5, square)).toBe(false);
    expect(pointInPoly(5, 11, square)).toBe(false);
    expect(pointInPoly(0, 0, square)).toBe(true);   // 顶点（HTML 同款射线法语义）
  });
  it('hitRoi 返回首个命中下标', () => {
    const rois: Poly[] = [
      [[20, 20], [30, 20], [30, 30], [20, 30]],
      square,
    ];
    expect(hitRoi(5, 5, rois)).toBe(1);      // 命中第二个
    expect(hitRoi(25, 25, rois)).toBe(0);    // 命中第一个
    expect(hitRoi(99, 99, rois)).toBe(-1);   // 无命中
  });
});
