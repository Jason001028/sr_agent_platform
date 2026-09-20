/**
 * viewMath.test.ts — 视图/坐标纯函数回归（直译 tif-viewer.html 的 fit/映射/定位/命中）
 * ------------------------------------------------------------------
 * 用例对齐 HTML 行为：fit 取 min(...,1) + 居中；thumbToOrig round+clamp；
 * locateView 居中且 scale<1 升到 1；wheelZoom 锚点不变；pointInPoly 射线法边界。
 */
import { describe, it, expect } from 'vitest';
import {
  fitView, thumbToScreen, mouseToThumb, thumbToOrig, origToThumb, locateView, wheelZoom,
  pointInPoly, hitRoi, visibleThumbRect, parseLocPair,
  clampSplitRatio, splitRects, paneAtX, ratioFromPointer, normAnchor, anchorAtLocal,
  wheelZoomBoth, panBoth, SPLIT_MIN_HALF_PX,
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

describe('parseLocPair（输入框里的「X,Y」文本）', () => {
  it('掩膜中心点坐标那种形态：半角逗号 + 小数', () => {
    expect(parseLocPair('30766.11,21862.51')).toEqual(['30766.11', '21862.51']);
  });
  it('全角逗号 / 空格 / 制表符分隔、首尾空白都收', () => {
    expect(parseLocPair(' 30766.11，21862.51 ')).toEqual(['30766.11', '21862.51']);
    expect(parseLocPair('30766 21862')).toEqual(['30766', '21862']);
    expect(parseLocPair('30766.11\t21862.51')).toEqual(['30766.11', '21862.51']);
    expect(parseLocPair('30766.11,21862.51,')).toEqual(['30766.11', '21862.51']);  // 尾随分隔符不算第三个数
  });
  it('整数、带符号、省略整数位的小数都认', () => {
    expect(parseLocPair('0,0')).toEqual(['0', '0']);
    expect(parseLocPair('-1.5,+2')).toEqual(['-1.5', '+2']);
    expect(parseLocPair('.5,2.')).toEqual(['.5', '2.']);
  });
  it('单个数不是坐标对（调用方据此报错，不拿它跳）', () => {
    expect(parseLocPair('30766.11')).toBeNull();
    expect(parseLocPair('')).toBeNull();
    expect(parseLocPair('   ')).toBeNull();
  });
  it('**三个数一律不认**（拷了整行掩膜记录）：截前两个会把标记跳到别处', () => {
    expect(parseLocPair('1,30766.11,21862.51')).toBeNull();
  });
  it('不是数的内容不认', () => {
    expect(parseLocPair('X,Y')).toBeNull();
    expect(parseLocPair('30766.11,abc')).toBeNull();
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

describe('visibleThumbRect（当前视野可见缩略图像素矩形）', () => {
  it('scale<1 整体适配（缩略图小于画布、居中留边）→ 整幅可见', () => {
    // fitView(100,100,400,300) = { scale:1, ox:150, oy:100 }，图完全在画布内
    const v = { scale: 1, ox: 150, oy: 100 };
    expect(visibleThumbRect(v, 400, 300, 100, 100)).toEqual({ x0: 0, y0: 0, x1: 99, y1: 99 });
  });
  it('放大到左上（scale=2、ox=oy=0、画布 200×200、缩略图 200×200）→ 只露出左上 100×100', () => {
    const v = { scale: 2, ox: 0, oy: 0 };
    // 反投影屏幕 [0,200]² → thumb [0,100]²
    expect(visibleThumbRect(v, 200, 200, 200, 200)).toEqual({ x0: 0, y0: 0, x1: 99, y1: 99 });
  });
  it('平移只看中间一块', () => {
    // scale=1、画布 200×200、缩略图 1000×1000、ox=-300 → 可见 x∈[300,500)
    const v = { scale: 1, ox: -300, oy: -400 };
    expect(visibleThumbRect(v, 200, 200, 1000, 1000)).toEqual({ x0: 300, y0: 400, x1: 499, y1: 599 });
  });
  it('缩略图越过画布右/下缘 → clamp 到图边', () => {
    // scale=2、ox=-100：可见 x∈[50,150)→clamp 99；oy=0 → y∈[0,100)
    const v = { scale: 2, ox: -100, oy: 0 };
    expect(visibleThumbRect(v, 200, 200, 100, 100)).toEqual({ x0: 50, y0: 0, x1: 99, y1: 99 });
  });
  it('视图整体在图外 / 空画布 → null', () => {
    const off = { scale: 1, ox: 99999, oy: 99999 };
    expect(visibleThumbRect(off, 200, 200, 100, 100)).toBeNull();
    expect(visibleThumbRect({ scale: 1, ox: 0, oy: 0 }, 0, 200, 100, 100)).toBeNull();
    expect(visibleThumbRect({ scale: 0, ox: 0, oy: 0 }, 200, 200, 100, 100)).toBeNull();
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

/* ==================== 图像对比：分屏两格（2026-09-20） ==================== */

describe('clampSplitRatio（分隔比例夹取）', () => {
  it('常规画布下夹到 [0.15, 0.85]', () => {
    expect(clampSplitRatio(0.5, 1366)).toBeCloseTo(0.5);
    expect(clampSplitRatio(0.01, 1366)).toBeCloseTo(0.15);
    expect(clampSplitRatio(0.99, 1366)).toBeCloseTo(0.85);
  });
  it('非有限值 → 0.5（localStorage 里可能是脏数据）', () => {
    expect(clampSplitRatio(NaN, 1366)).toBe(0.5);
    expect(clampSplitRatio(Infinity, 1366)).toBe(0.5);
  });
  it('窄画布下界抬到 SPLIT_MIN_HALF_PX/w，两格都还看得见', () => {
    // 300px 宽 → 120/300 = 0.4 > 0.15，下界抬到 0.4
    expect(clampSplitRatio(0.2, 300)).toBeCloseTo(0.4);
    expect(clampSplitRatio(0.1, 300)).toBeCloseTo(0.4);
    // 上界对称抬到 0.6
    expect(clampSplitRatio(0.9, 300)).toBeCloseTo(0.6);
    // 抬完后每格仍不小于 SPLIT_MIN_HALF_PX
    const r = clampSplitRatio(0.01, 300);
    expect(300 * r).toBeGreaterThanOrEqual(SPLIT_MIN_HALF_PX);
  });
  it('画布窄到两格都放不下时退回 [0.15, 0.85]，不抛', () => {
    expect(clampSplitRatio(0.5, 100)).toBeCloseTo(0.5);
    expect(clampSplitRatio(0.01, 100)).toBeCloseTo(0.15);
  });
});

describe('splitRects（切两格：无缝隙无重叠）', () => {
  it('a.w + b.w 精确等于画布宽，a 从 0 起、b 紧接 a', () => {
    for (const cw of [1, 2, 3, 1366, 4096]) {
      const { a, b } = splitRects(0.5, cw, 800);
      expect(a.x).toBe(0);
      expect(b.x).toBe(a.w);
      expect(a.w + b.w).toBe(Math.max(0, Math.round(cw)));
      expect(a.h).toBe(800);
      expect(b.h).toBe(800);
      expect(b.y).toBe(0);
    }
  });
  it('比例 0.7 → 左格占七成', () => {
    const { a, b } = splitRects(0.7, 1000, 500);
    expect(a.w).toBe(700);
    expect(b.w).toBe(300);
  });
  it('极端比例也留得住两格（各至少 1px）', () => {
    const { a, b } = splitRects(0.99, 1000, 500);
    expect(a.w).toBeLessThanOrEqual(999);
    expect(b.w).toBeGreaterThanOrEqual(1);
  });
  it('cw <= 1 退化：整幅给左格，右格宽 0，不抛', () => {
    const { a, b } = splitRects(0.5, 1, 10);
    expect(a.w).toBe(1);
    expect(b.w).toBe(0);
    expect(splitRects(0.5, 0, 10).a.w).toBe(0);
  });
});

describe('paneAtX / ratioFromPointer', () => {
  it('正好压在分隔线上算右格', () => {
    expect(paneAtX(499, 500)).toBe('A');
    expect(paneAtX(500, 500)).toBe('B');
    expect(paneAtX(501, 500)).toBe('B');
  });
  it('负值与越界仍能定侧（不抛）', () => {
    expect(paneAtX(-10, 500)).toBe('A');
    expect(paneAtX(9999, 500)).toBe('B');
  });
  it('指针 clientX → 比例（未夹）', () => {
    expect(ratioFromPointer(1600, 1000, 1000)).toBeCloseTo(0.6);
    expect(ratioFromPointer(400, 1000, 1000)).toBeCloseTo(-0.6);   // 越界交给 clamp
    expect(ratioFromPointer(400, 0, 0)).toBe(0.5);                 // 零宽兜底
  });
});

describe('normAnchor / anchorAtLocal（归一化锚点往返）', () => {
  it('去掉 rect 原点后往返稳定在 1e-9', () => {
    const rect = { x: 683, y: 0, w: 683, h: 800 };
    const n = normAnchor(rect, 900, 400);
    const [lx, ly] = anchorAtLocal(rect, n.u, n.v);   // 该格自己的局部坐标
    expect(Math.abs(lx - (900 - rect.x))).toBeLessThan(1e-9);
    expect(Math.abs(ly - 400)).toBeLessThan(1e-9);
  });
  it('格内点的 u,v 落在 [0,1]', () => {
    const rect = { x: 100, y: 50, w: 200, h: 100 };
    const n = normAnchor(rect, 150, 100);
    expect(n.u).toBeCloseTo(0.25);
    expect(n.v).toBeCloseTo(0.5);
  });
  it('零宽/零高的格退回中心，不产生 NaN', () => {
    const n = normAnchor({ x: 0, y: 0, w: 0, h: 0 }, 10, 10);
    expect(n.u).toBe(0.5);
    expect(n.v).toBe(0.5);
  });
});

describe('wheelZoomBoth（同步缩放）', () => {
  const ra = { x: 0, y: 0, w: 683, h: 800 };
  const rb = { x: 683, y: 0, w: 683, h: 800 };
  const va = fitView(256, 256, ra.w, ra.h);
  const vb = fitView(256, 256, rb.w, rb.h);

  it('等格等视图 → 两侧 scale 恒等，且被指针那格与单侧 wheelZoom 逐位相同', () => {
    let a = va, b = vb;
    let solo = va;                                  // 单侧对照：只对 va 反复滚轮
    for (const f of [1.2, 1.2, 1 / 1.2, 1.2]) {
      const r = wheelZoomBoth(a, b, ra, rb, 'A', 300, 400, f);
      a = r.a; b = r.b;
      solo = wheelZoom(solo, 300, 400, f);
      expect(a.scale).toBeCloseTo(b.scale, 12);
    }
    // 指针所在格的变换与单侧路径完全一致（另一格的差异只在锚点位置）
    expect(a).toEqual(solo);
    expect(b.scale).toBeCloseTo(solo.scale, 12);
  });

  it('指针所在格的锚点缩略图坐标守恒', () => {
    const before = (300 - va.ox) / va.scale;        // 指针处对应的缩略图 x
    const r = wheelZoomBoth(va, vb, ra, rb, 'A', 300, 400, 1.2);
    expect((300 - r.a.ox) / r.a.scale).toBeCloseTo(before, 9);
  });

  it('另一格锚在同归一化位置（不是同一绝对偏移）—— 这一条是防回归的', () => {
    // 指针在左格的 u = 300/683；右格里的同一相对位置是它**自己局部坐标**里的
    // u*rb.w，**不是** rb.x + u*rb.w。后者（本函数最早那版）把右格锚点整体挪了
    // 一个左格宽（983 而不是 300），一滚轮右格就飞出去 —— 下面这个守恒式当时过不了。
    const u = 300 / ra.w;
    const bxLocal = u * rb.w;
    const before = (bxLocal - vb.ox) / vb.scale;
    const r = wheelZoomBoth(va, vb, ra, rb, 'A', 300, 400, 1.2);
    expect((bxLocal - r.b.ox) / r.b.scale).toBeCloseTo(before, 9);
    // 顺带把「错法确实不同」也钉住，免得哪天两条算式意外重合、这条测试变成空声明
    const buggy = wheelZoom(vb, rb.x + u * rb.w, 400, 1.2);
    expect(Math.abs(r.b.ox - buggy.ox)).toBeGreaterThan(1);
  });

  it('指针在右格时同样：两格各锚在自己的局部坐标系里', () => {
    const u = (1000 - rb.x) / rb.w;                 // 指针在右格里的相对位置
    const bxLocal = u * rb.w;                       // = 指针自己的格局部坐标
    const axLocal = u * ra.w;                       // 左格里的同一相对位置
    const beforeB = (bxLocal - vb.ox) / vb.scale;
    const beforeA = (axLocal - va.ox) / va.scale;
    const r = wheelZoomBoth(va, vb, rb, ra, 'B', 1000, 400, 1.2);
    expect((bxLocal - r.b.ox) / r.b.scale).toBeCloseTo(beforeB, 9);
    expect((axLocal - r.a.ox) / r.a.scale).toBeCloseTo(beforeA, 9);
  });

  it('clamp 每侧独立，两侧 scale 不会发散', () => {
    let a = va, b = vb;
    for (let i = 0; i < 80; i++) {
      const r = wheelZoomBoth(a, b, ra, rb, 'A', 100, 100, 1.2);
      a = r.a; b = r.b;
    }
    expect(a.scale).toBeLessThanOrEqual(64);
    expect(b.scale).toBeLessThanOrEqual(64);
    expect(a.scale).toBeCloseTo(b.scale, 9);
  });
});

describe('panBoth（同步平移）', () => {
  it('两格加同一个屏幕位移，入参不改', () => {
    const va = { scale: 2, ox: 10, oy: 20 };
    const vb = { scale: 3, ox: -5, oy: 7 };
    const r = panBoth(va, vb, 40, -15);
    expect(r.a).toEqual({ scale: 2, ox: 50, oy: 5 });
    expect(r.b).toEqual({ scale: 3, ox: 35, oy: -8 });
    expect(va).toEqual({ scale: 2, ox: 10, oy: 20 });   // 未改
    expect(vb).toEqual({ scale: 3, ox: -5, oy: 7 });
  });
});
