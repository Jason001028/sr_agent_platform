/**
 * motion.test.ts — 动效令牌的 JS 侧（批量入列错峰）
 * ------------------------------------------------------------------
 * 钉五件事：整段窗口不超 `CARD_CASCADE_MS`、第一张不等、6 条以内逐条固定
 * `CARD_STEP_MS` 累计、超过后等距压进窗口、`CARD_ENTER_MS` 与 style.css 的
 * `--motion-fade` 同值（这层双写靠这条测试守住）。取整造成的 ±1ms 抖动不算破例。
 */
import { readFileSync } from 'node:fs';
import { describe, it, expect } from 'vitest';
import {
  CARD_CASCADE_MS, CARD_ENTER_MS, CARD_STEP_MS, cascadeDelay,
} from '../motion.js';

const delaysOf = (n: number): number[] =>
  Array.from({ length: n }, (_, i) => cascadeDelay(i, n));
const gapsOf = (n: number): number[] => {
  const d = delaysOf(n);
  return d.slice(1).map((v, i) => v - d[i]);
};

describe('cascadeDelay —— 批量入列的逐卡延迟', () => {
  it('单张 / 空列表：不等', () => {
    expect(cascadeDelay(0, 1)).toBe(0);
    expect(cascadeDelay(0, 0)).toBe(0);
  });

  it('第一张立即入场，最后一张连同淡入仍落在窗口内', () => {
    for (const n of [2, 3, 4, 8, 12, 40]) {
      expect(cascadeDelay(0, n)).toBe(0);
      expect(cascadeDelay(n - 1, n) + CARD_ENTER_MS).toBeLessThanOrEqual(CARD_CASCADE_MS);
    }
  });

  it('延迟随序号单调不减', () => {
    for (const n of [4, 12, 13, 40]) {
      let prev = -1;
      for (let i = 0; i < n; i++) {
        const d = cascadeDelay(i, n);
        expect(d).toBeGreaterThanOrEqual(prev);
        prev = d;
      }
    }
  });

  it('6 条以内逐条固定 300ms 累计（第 6 条也不被窗口压缩）', () => {
    for (const n of [2, 3, 4, 5, 6]) {
      expect(cascadeDelay(0, n)).toBe(0);
      for (const g of gapsOf(n)) {
        expect(Math.abs(g - CARD_STEP_MS)).toBeLessThanOrEqual(1);
      }
    }
    // 6 条：5 × 300ms = 1500ms，连同 120ms 淡入仍在 1800ms 窗口内。
    expect(cascadeDelay(5, 6) + CARD_ENTER_MS).toBeLessThanOrEqual(CARD_CASCADE_MS);
  });

  it('超过 6 条：等距压缩进同一窗口，末张连同淡入不超 1800ms', () => {
    for (const n of [7, 12, 40, 200]) {
      const want = (CARD_CASCADE_MS - CARD_ENTER_MS) / (n - 1);
      for (const g of gapsOf(n)) {
        expect(Math.abs(g - want)).toBeLessThanOrEqual(1);
      }
      const last = cascadeDelay(n - 1, n);
      expect(last + CARD_ENTER_MS).toBeLessThanOrEqual(CARD_CASCADE_MS);
      expect(last).toBeGreaterThanOrEqual(CARD_CASCADE_MS - CARD_ENTER_MS - 1);
    }
  });

  it('份数越多步长越小（整批不随份数线性拖长）', () => {
    const stepOf = (n: number) => cascadeDelay(1, n) - cascadeDelay(0, n);
    expect(stepOf(40)).toBeLessThan(stepOf(7));
    expect(stepOf(7)).toBeLessThan(stepOf(6));
    expect(stepOf(6)).toBeLessThanOrEqual(CARD_STEP_MS);
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
