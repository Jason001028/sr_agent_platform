import { describe, expect, it } from 'vitest';
import {
  DEFAULT_TONE_BANDS, brightMembership, darkMembership, encodeToneWeight, flatMembership,
  midMembership, regionTone, smoothstep, toneColor, toneWeightMap, toneWindowRadius,
  weightFromShares,
} from '../diffTone';
import type { ToneGrid } from '../diffTone';

function grid(value: number, w = 20, h = 20, valid?: Uint8Array): ToneGrid {
  const gray = new Float32Array(w * h);
  gray.fill(value);
  return { gray, valid, w, h };
}

const FULL = { x: 0, y: 0, w: 20, h: 20 };

describe('smoothstep', () => {
  it('在过渡区外夹紧，在区间内连续插值', () => {
    expect(smoothstep(0, 10, -1)).toBe(0);
    expect(smoothstep(0, 10, 11)).toBe(1);
    expect(smoothstep(0, 10, 5)).toBeCloseTo(0.5, 6);
    expect(smoothstep(0, 10, 2.5)).toBeCloseTo(0.15625, 6);
  });

  it('单调不减', () => {
    let prev = -1;
    for (let x = -5; x <= 15; x += 0.25) {
      const v = smoothstep(0, 10, x);
      expect(v).toBeGreaterThanOrEqual(prev);
      prev = v;
    }
  });
});

describe('隶属度', () => {
  it('中间调在 120–180 为 1，两侧平滑回落', () => {
    expect(midMembership(60)).toBe(0);
    expect(midMembership(150)).toBeCloseTo(1, 6);
    expect(midMembership(105)).toBeGreaterThan(0);
    expect(midMembership(105)).toBeLessThan(1);
    expect(midMembership(195)).toBeGreaterThan(0);
    expect(midMembership(195)).toBeLessThan(1);
    expect(midMembership(220)).toBe(0);
  });

  it('暗部在 70 以下为 1，110 以上为 0', () => {
    expect(darkMembership(40)).toBe(1);
    expect(darkMembership(90)).toBeGreaterThan(0);
    expect(darkMembership(90)).toBeLessThan(1);
    expect(darkMembership(120)).toBe(0);
  });

  it('平坦度与提亮量使用各自的过渡区', () => {
    expect(flatMembership(0)).toBe(1);
    expect(flatMembership(5)).toBeGreaterThan(0);
    expect(flatMembership(5)).toBeLessThan(1);
    expect(flatMembership(10)).toBe(0);
    expect(brightMembership(-3)).toBe(0);
    expect(brightMembership(8.5)).toBeGreaterThan(0);
    expect(brightMembership(8.5)).toBeLessThan(1);
    expect(brightMembership(20)).toBe(1);
  });
});

describe('weightFromShares', () => {
  it('暗部明显提亮时降到 0.3', () => {
    const w = weightFromShares(0, 1, 0.5, 20);
    expect(w.weight).toBeCloseTo(0.3, 6);
    expect(w.norm).toBeCloseTo(0, 6);
  });

  it('中间调平坦区升到 1.8', () => {
    const w = weightFromShares(1, 0, 1, 0);
    expect(w.weight).toBeCloseTo(1.8, 6);
    expect(w.norm).toBe(1);
  });

  it('暗部不变暗不享受折扣；中间调越平权重越高', () => {
    expect(weightFromShares(0, 1, 0.5, -10).weight).toBeCloseTo(1, 6);
    expect(weightFromShares(1, 0, 0, 0).weight).toBeCloseTo(1.4, 6);
    expect(weightFromShares(0.5, 0, 1, 0).weight).toBeCloseTo(1.4, 6);
  });
});

describe('regionTone', () => {
  it('中间调平坦区域得到高权重', () => {
    const s = regionTone(grid(150), grid(155), FULL);
    expect(s).not.toBeNull();
    expect(s!.mid).toBeCloseTo(1, 6);
    expect(s!.dark).toBeCloseTo(0, 6);
    expect(s!.flat).toBeCloseTo(1, 6);
    expect(s!.delta).toBeCloseTo(5, 6);
    expect(s!.weight).toBeCloseTo(1.8, 6);
    expect(s!.pixels).toBe(400);
  });

  it('暗部提亮区域得到低权重', () => {
    const s = regionTone(grid(40), grid(60), FULL);
    expect(s!.dark).toBeCloseTo(1, 6);
    expect(s!.mid).toBeCloseTo(0, 6);
    expect(s!.delta).toBeCloseTo(20, 6);
    expect(s!.weight).toBeCloseTo(0.3, 6);
  });

  it('纹理区降低中间调权重', () => {
    const a = grid(150);
    for (let i = 0; i < a.gray.length; i++) a.gray[i] = (i % 2 ? 160 : 140);
    const b = grid(155);
    for (let i = 0; i < b.gray.length; i++) b.gray[i] = (i % 2 ? 165 : 145);
    const s = regionTone(a, b, FULL);
    expect(s!.mid).toBeCloseTo(1, 6);
    expect(s!.flat).toBeLessThan(0.3);
    expect(s!.weight).toBeGreaterThan(1.0);
    expect(s!.weight).toBeLessThan(1.5);
  });

  it('掩码只统计区域像素', () => {
    const mask = new Uint8Array(400);
    for (let y = 2; y < 7; y++) for (let x = 3; x < 8; x++) mask[y * 20 + x] = 255;
    const s = regionTone(grid(150), grid(155), FULL, FULL, { mask, maskWidth: 20 });
    expect(s!.pixels).toBe(25);
    expect(s!.mid).toBeCloseTo(1, 6);
  });

  it('有效掩码和空区域分别跳过与返回 null', () => {
    const valid = new Uint8Array(400);
    valid.fill(1);
    for (let i = 0; i < 100; i++) valid[i] = 0;
    const s = regionTone(grid(150, 20, 20, valid), grid(155), FULL);
    expect(s!.pixels).toBe(300);
    const empty = new Uint8Array(400);
    expect(regionTone(grid(150, 20, 20, empty), grid(155), FULL)).toBeNull();
  });

  it('支持两图不同分辨率：矩形表示同一范围', () => {
    const s = regionTone(grid(150), grid(160, 10, 10), FULL, { x: 0, y: 0, w: 10, h: 10 });
    expect(s!.meanA).toBeCloseTo(150, 6);
    expect(s!.meanB).toBeCloseTo(160, 6);
    expect(s!.delta).toBeCloseTo(10, 6);
  });
});

describe('toneWindowRadius', () => {
  it('按分析网格短边自适应，夹在 2..8', () => {
    expect(toneWindowRadius(20, 20)).toBe(2);          // 小网格不放大窗口
    expect(toneWindowRadius(1536, 2048)).toBe(5);      // 常规 4:3 分析网格
    expect(toneWindowRadius(4096, 4096)).toBe(8);      // 上限
    expect(toneWindowRadius(0, 10)).toBe(2);           // 非法尺寸给最小窗口
  });
});

describe('toneWeightMap', () => {
  it('中间调平坦区逐像素升到上限，边界同样生效', () => {
    const map = toneWeightMap(grid(150), grid(155))!;
    expect(map).not.toBeNull();
    expect([map.w, map.h]).toEqual([20, 20]);
    expect(map.radius).toBe(2);
    expect(new Set(map.norm).size).toBe(1);
    expect(map.norm[0]).toBe(encodeToneWeight(1.8));
  });

  it('纹理区的局部标准差压低中间调权重', () => {
    const a = grid(150);
    for (let i = 0; i < a.gray.length; i++) a.gray[i] = i % 2 ? 160 : 140;
    const b = grid(155);
    for (let i = 0; i < b.gray.length; i++) b.gray[i] = i % 2 ? 165 : 145;
    const map = toneWeightMap(a, b)!;
    const center = map.norm[10 * 20 + 10];
    expect(center).toBe(encodeToneWeight(1.4));          // mid=1、flat≈0
    expect(center).toBeGreaterThan(encodeToneWeight(1));
    expect(center).toBeLessThan(encodeToneWeight(1.8));
  });

  it('暗部被明显提亮降到下限', () => {
    const map = toneWeightMap(grid(40), grid(60))!;
    expect(map.norm[10 * 20 + 10]).toBe(encodeToneWeight(0.3));
  });

  it('NoData 像素给中性权重，且不参与邻点的窗口统计', () => {
    const valid = new Uint8Array(400);
    valid.fill(1);
    valid[0] = 0;
    const a = grid(150, 20, 20, valid);
    a.gray[0] = 0;
    const map = toneWeightMap(a, grid(155))!;
    expect(map.norm[0]).toBe(encodeToneWeight(1));
    // 邻点仍按 150 的平坦网格统计，不被无效像素的 0 拖出纹理。
    expect(map.norm[1]).toBe(encodeToneWeight(1.8));
  });

  it('两侧网格尺寸不一致时拒绝', () => {
    expect(toneWeightMap(grid(150), grid(155, 10, 10))).toBeNull();
    expect(toneWeightMap(grid(150, 0, 20), grid(155))).toBeNull();
  });

  it('接受 Uint8 灰度（长期缓存用）', () => {
    const a: ToneGrid = { gray: new Uint8Array(400).fill(150), w: 20, h: 20 };
    const b: ToneGrid = { gray: new Uint8Array(400).fill(155), w: 20, h: 20 };
    expect(toneWeightMap(a, b)!.norm[0]).toBe(encodeToneWeight(1.8));
  });
});

describe('toneColor', () => {
  it('冷灰蓝到暖橙连续插值', () => {
    const cool = toneColor(0);
    const warm = toneColor(1);
    const mid = toneColor(0.5);
    expect(cool.stroke).toBe('rgb(78,124,168)');
    expect(warm.stroke).toBe('rgb(224,86,50)');
    expect(cool.width).toBeCloseTo(1.4, 6);
    expect(warm.width).toBeCloseTo(2.4, 6);
    expect(mid.stroke).toBe('rgb(151,105,109)');
  });

  it('越界值夹紧', () => {
    expect(toneColor(-1).stroke).toBe(toneColor(0).stroke);
    expect(toneColor(2).stroke).toBe(toneColor(1).stroke);
  });
});

describe('默认参数', () => {
  it('保持文档里的过渡区', () => {
    expect(DEFAULT_TONE_BANDS.midLo).toEqual([90, 120]);
    expect(DEFAULT_TONE_BANDS.midHi).toEqual([180, 210]);
    expect(DEFAULT_TONE_BANDS.dark).toEqual([70, 110]);
    expect(DEFAULT_TONE_BANDS.flat).toEqual([2, 8]);
    expect(DEFAULT_TONE_BANDS.bright).toEqual([2, 15]);
  });
});
