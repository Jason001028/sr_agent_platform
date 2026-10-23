/**
 * motion.test.ts — 动效令牌的 JS 侧（批量入列错峰）
 * ------------------------------------------------------------------
 * 钉四件事：第一张不等、前 `CARD_STAGGER_MAX`（6）张严格按 `CARD_STEP_MS`
 * 逐条累计、其后的卡片不做入场动画（`undefined`，不是压缩步长）、
 * `CARD_ENTER_MS` 与 style.css 的 `--motion-fade` 同值（这层双写靠这条测试守住）。
 */
import { readFileSync } from 'node:fs';
import { describe, it, expect } from 'vitest';
import {
  CARD_CASCADE_MS, CARD_ENTER_MS, CARD_STAGGER_MAX, CARD_STEP_MS, cascadeDelay,
} from '../motion.js';

/** 取前 n 张的延迟；只拿有动画的那一段用。 */
const delaysOf = (n: number): (number | undefined)[] =>
  Array.from({ length: n }, (_, i) => cascadeDelay(i, n));

describe('cascadeDelay —— 批量入列的逐卡延迟', () => {
  it('单张 / 空列表：不等', () => {
    expect(cascadeDelay(0, 1)).toBe(0);
    expect(cascadeDelay(0, 0)).toBe(0);
  });

  it('第一张立即入场', () => {
    for (const n of [2, 3, 4, 6, 7, 40]) {
      expect(cascadeDelay(0, n)).toBe(0);
    }
  });

  it('前 6 张逐条固定 300ms 累计，末尾连同淡入仍落在窗口内', () => {
    const n = CARD_STAGGER_MAX;
    const d = delaysOf(n) as number[];
    expect(d).toEqual([0, 300, 600, 900, 1200, 1500]);
    expect(d[n - 1]! + CARD_ENTER_MS).toBeLessThanOrEqual(CARD_CASCADE_MS);
  });

  it('份数不到 6 张时也照固定步长，不压缩', () => {
    for (const n of [2, 3, 4, 5]) {
      const d = delaysOf(n) as number[];
      d.forEach((v, i) => expect(v).toBe(i * CARD_STEP_MS));
      expect(d[n - 1]! + CARD_ENTER_MS).toBeLessThanOrEqual(CARD_CASCADE_MS);
    }
  });

  it('第 7 张起省略入场动画（undefined），前面节奏不因份数变慢', () => {
    for (const n of [7, 12, 40, 200]) {
      const d = delaysOf(n);
      for (let i = 0; i < CARD_STAGGER_MAX; i++) {
        expect(d[i]).toBe(i * CARD_STEP_MS);
      }
      for (let i = CARD_STAGGER_MAX; i < n; i++) {
        expect(d[i]).toBeUndefined();
      }
    }
  });

  it('份数再多，有动画的始终只有前 6 张', () => {
    for (const n of [7, 40, 200]) {
      const animated = delaysOf(n).filter((v) => v !== undefined);
      expect(animated).toHaveLength(CARD_STAGGER_MAX);
    }
  });
});

describe('令牌双写', () => {
  it('CARD_ENTER_MS 等于 style.css 的 --motion-fade', () => {
    const css = readFileSync(new URL('../../style.css', import.meta.url), 'utf8');
    const m = css.match(/--motion-fade:\s*(\d+)ms/);
    expect(m, 'style.css 里找不到 --motion-fade').not.toBeNull();
    expect(Number(m![1])).toBe(CARD_ENTER_MS);
  });
});
