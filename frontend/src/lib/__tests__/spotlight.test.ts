/**
 * spotlight.test.ts — 场景聚焦遮罩的纯函数
 * ------------------------------------------------------------------
 * 钉四类事实：
 * 1. 镂空窗口的合并规则（上下相邻合并、隔着别的卡不合并、外扩 SPOTLIGHT_PAD）；
 * 2. 遮罩分块是「整块 − 镂空」的**无重叠完备划分**（每一点的覆盖次数恰好 1 或 0）；
 * 3. 缓动端点与形状（ease-out：中点已走过一半以上）；
 * 4. 三档模式的持久化 + 与 style.css 的令牌双写。
 */
import { readFileSync } from 'node:fs';
import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  SPOTLIGHT_FADE_MS,
  SPOTLIGHT_MODE_KEY,
  SPOTLIGHT_MODE_OPTIONS,
  SPOTLIGHT_MOVE_MS,
  SPOTLIGHT_PAD,
  buildMaskBlocks,
  easeSpotlight,
  isSpotlightMode,
  lerpRect,
  mergeGroupRects,
  readSpotlightMode,
  saveSpotlightMode,
} from '../spotlight.js';
import type { SpotlightRect } from '../spotlight.js';

/* ---------------- 测试用的小工具 ---------------- */

/** 网格采样：返回每个采样点被多少块遮罩盖住。 */
function coverCount(blocks: readonly SpotlightRect[], w: number, h: number, step = 4): number[][] {
  const out: number[][] = [];
  for (let y = 0; y < h; y += step) {
    const row: number[] = [];
    for (let x = 0; x < w; x += step) {
      const px = x + step / 2;
      const py = y + step / 2;
      row.push(blocks.filter((b) => px >= b.x && px <= b.x + b.w && py >= b.y && py <= b.y + b.h).length);
    }
    out.push(row);
  }
  return out;
}

function insideAny(r: SpotlightRect, holes: readonly SpotlightRect[]): boolean {
  return holes.some((q) => r.x >= q.x - 0.01 && r.y >= q.y - 0.01
    && r.x + r.w <= q.x + q.w + 0.01 && r.y + r.h <= q.y + q.h + 0.01);
}

const overlaps = (a: SpotlightRect, b: SpotlightRect): boolean =>
  a.x < b.x + b.w - 0.01 && b.x < a.x + a.w - 0.01
  && a.y < b.y + b.h - 0.01 && b.y < a.y + a.h - 0.01;

function fakeStorage(): Storage {
  const map = new Map<string, string>();
  return {
    get length(): number { return map.size; },
    clear: () => { map.clear(); },
    getItem: (k: string) => (map.has(k) ? map.get(k)! : null),
    key: (i: number) => [...map.keys()][i] ?? null,
    removeItem: (k: string) => { map.delete(k); },
    setItem: (k: string, v: string) => { map.set(k, v); },
  };
}

/* ---------------- mergeGroupRects ---------------- */

describe('mergeGroupRects —— 同景卡片合成镂空窗口', () => {
  const a: SpotlightRect = { x: 10, y: 100, w: 300, h: 114 };
  const b: SpotlightRect = { x: 10, y: 220, w: 300, h: 114 };   // 与 a 隔 6px（卡间缝）

  it('空列表 → 空窗口', () => {
    expect(mergeGroupRects([])).toEqual([]);
  });

  it('上下相邻的两张卡合并成一个窗口，并外扩 SPOTLIGHT_PAD', () => {
    const out = mergeGroupRects([a, b]);
    expect(out).toHaveLength(1);
    expect(out[0]).toEqual({
      x: 10 - SPOTLIGHT_PAD,
      y: 100 - SPOTLIGHT_PAD,
      w: 300 + SPOTLIGHT_PAD * 2,
      h: 234 + SPOTLIGHT_PAD * 2,
    });
  });

  it('顺序无关：先传下面的卡结果一样', () => {
    expect(mergeGroupRects([b, a])).toEqual(mergeGroupRects([a, b]));
  });

  it('中间隔着别的卡（间距 > 2×pad）→ 两个窗口，不横跨无关卡片', () => {
    const far: SpotlightRect = { x: 10, y: 400, w: 300, h: 114 };
    expect(mergeGroupRects([a, far])).toHaveLength(2);
  });

  it('收藏把同景拆到列表两端 → 仍是两个窗口', () => {
    const top: SpotlightRect = { x: 10, y: 10, w: 300, h: 114 };
    const bottom: SpotlightRect = { x: 10, y: 900, w: 300, h: 114 };
    const out = mergeGroupRects([top, bottom]);
    expect(out).toHaveLength(2);
    expect(out[0]!.y).toBe(10 - SPOTLIGHT_PAD);
    expect(out[1]!.y).toBe(900 - SPOTLIGHT_PAD);
  });

  it('横向不相交（万一并排）不合并', () => {
    const left: SpotlightRect = { x: 10, y: 100, w: 100, h: 100 };
    const right: SpotlightRect = { x: 200, y: 100, w: 100, h: 100 };
    expect(mergeGroupRects([left, right])).toHaveLength(2);
  });

  it('零宽/零高的矩形被忽略', () => {
    expect(mergeGroupRects([{ x: 0, y: 0, w: 0, h: 50 }, a])).toHaveLength(1);
  });
});

/* ---------------- buildMaskBlocks ---------------- */

describe('buildMaskBlocks —— 整块遮罩减去镂空', () => {
  const W = 400;
  const H = 800;

  it('没有镂空 → 一块铺满', () => {
    expect(buildMaskBlocks(W, H, [])).toEqual([{ x: 0, y: 0, w: W, h: H }]);
  });

  it('尺寸为 0 或负 → 不画', () => {
    expect(buildMaskBlocks(0, H, [])).toEqual([]);
    expect(buildMaskBlocks(W, -1, [])).toEqual([]);
  });

  it('一个不贴边的窗口正好四块（上/左/右/下），互不重叠', () => {
    const hole: SpotlightRect = { x: 10, y: 100, w: 380, h: 234 };
    const blocks = buildMaskBlocks(W, H, [hole]);
    expect(blocks).toHaveLength(4);
    for (let i = 0; i < blocks.length; i++) {
      for (let j = i + 1; j < blocks.length; j++) {
        expect(overlaps(blocks[i]!, blocks[j]!)).toBe(false);
      }
      expect(insideAny(blocks[i]!, [hole])).toBe(false);
    }
  });

  it('分区完备：每个采样点被覆盖 0 次（镂空内）或恰好 1 次（镂空外）', () => {
    const holes: SpotlightRect[] = [
      { x: 10, y: 100, w: 380, h: 234 },
      { x: 10, y: 500, w: 380, h: 120 },
    ];
    const blocks = buildMaskBlocks(W, H, holes);
    const counts = coverCount(blocks, W, H, 20);
    for (const row of counts) {
      for (const c of row) expect(c <= 1).toBe(true);
    }
    // 面积守恒：块面积之和 = 整块 − 窗口面积
    const sum = blocks.reduce((n, b) => n + b.w * b.h, 0);
    const holeArea = holes.reduce((n, q) => n + q.w * q.h, 0);
    expect(sum).toBeCloseTo(W * H - holeArea, 3);
  });

  it('窗口贴顶边 → 只剩左/右/下三块（没有上方那条）', () => {
    const hole: SpotlightRect = { x: 20, y: 0, w: 360, h: 200 };
    const blocks = buildMaskBlocks(W, H, [hole]);
    expect(blocks).toHaveLength(3);
    expect(blocks.some((b) => b.y === 0 && b.h === 0)).toBe(false);
  });

  it('窗口超出右/下边界 → 按层内边界裁掉，不越界', () => {
    const hole: SpotlightRect = { x: 350, y: 700, w: 200, h: 300 };
    const blocks = buildMaskBlocks(W, H, [hole]);
    for (const b of blocks) {
      expect(b.x).toBeGreaterThanOrEqual(0);
      expect(b.y).toBeGreaterThanOrEqual(0);
      expect(b.x + b.w).toBeLessThanOrEqual(W + 0.01);
      expect(b.y + b.h).toBeLessThanOrEqual(H + 0.01);
    }
  });

  it('两个窗口共享同一条竖直边时切分仍然无重叠', () => {
    const holes: SpotlightRect[] = [
      { x: 50, y: 50, w: 200, h: 200 },
      { x: 250, y: 50, w: 150, h: 200 },
    ];
    const blocks = buildMaskBlocks(W, H, holes);
    for (let i = 0; i < blocks.length; i++) {
      for (let j = i + 1; j < blocks.length; j++) {
        expect(overlaps(blocks[i]!, blocks[j]!)).toBe(false);
      }
    }
  });
});

/* ---------------- 插值与缓动 ---------------- */

describe('lerpRect / easeSpotlight', () => {
  const from: SpotlightRect = { x: 0, y: 0, w: 100, h: 50 };
  const to: SpotlightRect = { x: 200, y: 400, w: 300, h: 150 };

  it('两端取端点，中点取中值', () => {
    expect(lerpRect(from, to, 0)).toEqual(from);
    expect(lerpRect(from, to, 1)).toEqual(to);
    expect(lerpRect(from, to, 0.5)).toEqual({ x: 100, y: 200, w: 200, h: 100 });
  });

  it('缓动端点仍是 0 / 1（不会把窗口停在半路）', () => {
    expect(easeSpotlight(0)).toBe(0);
    expect(easeSpotlight(1)).toBe(1);
  });

  it('越界的进度被夹到 [0, 1]', () => {
    expect(easeSpotlight(-1)).toBe(0);
    expect(easeSpotlight(2)).toBe(1);
  });

  it('是 ease-out 形状：t 很小时已经走掉大半，且全程单调不减', () => {
    expect(easeSpotlight(0.25)).toBeGreaterThan(0.5);
    let prev = -1;
    for (let i = 0; i <= 20; i++) {
      const v = easeSpotlight(i / 20);
      expect(v).toBeGreaterThanOrEqual(prev);
      prev = v;
    }
  });
});

/* ---------------- 模式与持久化 ---------------- */

describe('遮罩模式', () => {
  afterEach(() => { vi.unstubAllGlobals(); });

  it('三档齐全，默认档排在第一个', () => {
    expect(SPOTLIGHT_MODE_OPTIONS.map((o) => o.value)).toEqual(['frosted', 'dim', 'none']);
    expect(readSpotlightMode()).toBe('frosted');
  });

  it('存取往返；未知值 / 坏数据回落默认', () => {
    vi.stubGlobal('localStorage', fakeStorage());
    saveSpotlightMode('dim');
    expect(localStorage.getItem(SPOTLIGHT_MODE_KEY)).toBe('dim');
    expect(readSpotlightMode()).toBe('dim');
    localStorage.setItem(SPOTLIGHT_MODE_KEY, 'blue');
    expect(readSpotlightMode()).toBe('frosted');
    localStorage.setItem(SPOTLIGHT_MODE_KEY, 'none');
    expect(readSpotlightMode()).toBe('none');
  });

  it('隐私模式读写抛错：读按默认、写不外抛', () => {
    vi.stubGlobal('localStorage', {
      getItem() { throw new Error('denied'); },
      setItem() { throw new Error('denied'); },
    });
    expect(readSpotlightMode()).toBe('frosted');
    expect(() => saveSpotlightMode('dim')).not.toThrow();
  });

  it('isSpotlightMode 只认三档', () => {
    expect(isSpotlightMode('frosted')).toBe(true);
    expect(isSpotlightMode('dim')).toBe(true);
    expect(isSpotlightMode('none')).toBe(true);
    expect(isSpotlightMode(null)).toBe(false);
    expect(isSpotlightMode('')).toBe(false);
  });
});

/* ---------------- 令牌双写 ---------------- */

describe('令牌双写', () => {
  const css = readFileSync(new URL('../../style.css', import.meta.url), 'utf8');

  it('SPOTLIGHT_MOVE_MS 等于 --motion-spotlight', () => {
    const m = css.match(/--motion-spotlight:\s*(\d+)ms/);
    expect(m, 'style.css 里找不到 --motion-spotlight').not.toBeNull();
    expect(Number(m![1])).toBe(SPOTLIGHT_MOVE_MS);
  });

  it('SPOTLIGHT_FADE_MS 等于 --motion-spotlight-fade', () => {
    const m = css.match(/--motion-spotlight-fade:\s*(\d+)ms/);
    expect(m, 'style.css 里找不到 --motion-spotlight-fade').not.toBeNull();
    expect(Number(m![1])).toBe(SPOTLIGHT_FADE_MS);
  });

  it('三档配色与缓动令牌都在 style.css 里', () => {
    expect(css).toMatch(/--spot-frost:\s*rgba\(\s*255,\s*255,\s*255,\s*0?\.40?\s*\)/);
    expect(css).toMatch(/--spot-dim:\s*rgba\(\s*17,\s*35,\s*32,\s*0?\.32\s*\)/);
    expect(css).toMatch(/--spot-ring:\s*#[0-9A-Fa-f]{6}/);
    expect(css).toMatch(/--ease-spotlight:\s*cubic-bezier\(\s*0\.22,\s*1,\s*0\.36,\s*1\s*\)/);
  });
});
