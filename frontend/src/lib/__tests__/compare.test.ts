/**
 * compare.test.ts — 图像对比的纯判据回归（2026-09-20）
 * ------------------------------------------------------------------
 * 三态解析、分隔比例解析、拖放落点归属、点选清单增删剪。这些都是**判据**：
 * 交互层（TifCanvas / CompareBar / CompareListPanel）只负责把它们接到事件上。
 *
 * 落点判据的边界是本文件的重点 —— 「画布外忽略」全靠它，错一格就是「拖到工具栏上
 * 也能换图」或者「压在画布最外一像素上不认」。
 */
import { describe, it, expect } from 'vitest';
import {
  parseCompareMode, parseSplitRatio, dropSideAt,
  seedCompareList, addCompareEntry, removeCompareEntry, pruneCompareList,
  DEFAULT_SPLIT_RATIO,
} from '../compare.js';

describe('parseCompareMode', () => {
  it('三个合法值原样通过', () => {
    expect(parseCompareMode('off')).toBe('off');
    expect(parseCompareMode('click')).toBe('click');
    expect(parseCompareMode('split')).toBe('split');
  });
  it('认不出的一律 off（不抛）', () => {
    for (const v of [undefined, null, '', 'SPLIT', 'on', 'close', 0, 1, {}, []]) {
      expect(parseCompareMode(v)).toBe('off');
    }
  });
});

describe('parseSplitRatio', () => {
  it('常规值与字符串都收（localStorage 存的是字符串）', () => {
    expect(parseSplitRatio('0.5')).toBeCloseTo(0.5);
    expect(parseSplitRatio(0.7)).toBeCloseTo(0.7);
  });
  it('越界按静态上下限粗夹', () => {
    expect(parseSplitRatio('0.02')).toBeCloseTo(0.15);
    expect(parseSplitRatio('0.99')).toBeCloseTo(0.85);
    expect(parseSplitRatio(-3)).toBeCloseTo(0.15);
  });
  it('认不出 → 默认值', () => {
    expect(parseSplitRatio('abc')).toBe(DEFAULT_SPLIT_RATIO);
    expect(parseSplitRatio(null)).toBe(DEFAULT_SPLIT_RATIO);
    expect(parseSplitRatio(undefined)).toBe(DEFAULT_SPLIT_RATIO);
    expect(parseSplitRatio(NaN)).toBe(DEFAULT_SPLIT_RATIO);
  });
});

describe('dropSideAt（落点归哪一格）', () => {
  const rect = { left: 100, top: 50, right: 900, bottom: 650 };   // 800×600 的画布
  const splitX = 400;                                            // 局部坐标的分隔线

  it('off：不问落点，一律 null（关闭模式下全窗口都是拖放目标）', () => {
    expect(dropSideAt(300, 300, rect, 'off', splitX)).toBeNull();
    expect(dropSideAt(500, 300, rect, 'off', splitX)).toBeNull();
  });

  it('split：左半 A、右半 B', () => {
    expect(dropSideAt(100 + 399, 300, rect, 'split', splitX)).toBe('A');
    expect(dropSideAt(100 + 400, 300, rect, 'split', splitX)).toBe('B');   // 压线算右
    expect(dropSideAt(100 + 700, 300, rect, 'split', splitX)).toBe('B');
  });

  it('click：画布内一律 A（只有一块画布，落哪都覆盖当前这张）', () => {
    expect(dropSideAt(100 + 10, 300, rect, 'click', splitX)).toBe('A');
    expect(dropSideAt(100 + 780, 300, rect, 'click', splitX)).toBe('A');
  });

  it('四边包含：压在画布最外一像素仍算在画布上', () => {
    expect(dropSideAt(100, 50, rect, 'split', splitX)).toBe('A');        // 左上角
    expect(dropSideAt(900, 650, rect, 'split', splitX)).toBe('B');       // 右下角
    expect(dropSideAt(900, 50, rect, 'click', splitX)).toBe('A');
  });

  it('画布外一律 null（左右上下各差 1px）', () => {
    expect(dropSideAt(99, 300, rect, 'split', splitX)).toBeNull();
    expect(dropSideAt(901, 300, rect, 'split', splitX)).toBeNull();
    expect(dropSideAt(300, 49, rect, 'split', splitX)).toBeNull();
    expect(dropSideAt(300, 651, rect, 'split', splitX)).toBeNull();
    // click 模式同一条门：画布外也忽略
    expect(dropSideAt(99, 300, rect, 'click', splitX)).toBeNull();
    expect(dropSideAt(300, 651, rect, 'click', splitX)).toBeNull();
  });
});

describe('点选清单的成员集合', () => {
  it('seedCompareList 去重保序', () => {
    expect(seedCompareList([3, 1, 3, 2, 1])).toEqual([3, 1, 2]);
    expect(seedCompareList([])).toEqual([]);
  });
  it('addCompareEntry 幂等，新条目排在末尾', () => {
    const a = addCompareEntry([1, 2], 3);
    expect(a).toEqual([1, 2, 3]);
    expect(addCompareEntry(a, 3)).toBe(a);            // 同一个引用，未动
  });
  it('removeCompareEntry 只删那一个，删不存在的返回等值新数组', () => {
    expect(removeCompareEntry([1, 2, 3, 2], 2)).toEqual([1, 3]);
    expect(removeCompareEntry([1, 2], 9)).toEqual([1, 2]);
  });
  it('pruneCompareList 丢掉已关掉的，保序', () => {
    expect(pruneCompareList([5, 3, 1, 4], [1, 3, 5])).toEqual([5, 3, 1]);
    expect(pruneCompareList([1, 2], [])).toEqual([]);
  });
});
