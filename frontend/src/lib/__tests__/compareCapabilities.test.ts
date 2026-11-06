import { expect, it } from 'vitest';
import { analysisPairReason, analysisPairNote, precisePairReason } from '../compareCapabilities';
it('allows PAN / NOSR resolution differences without registration certificates or server ids', () => {
  const pan = { id: 1, W: 40000, H: 30000 }, nosr = { id: 2, W: 10000, H: 7500 };
  expect(analysisPairReason(pan, nosr)).toBeNull();
  expect(analysisPairNote(pan, nosr)).toContain('分辨率不同');
  expect(analysisPairNote(pan, nosr)).toContain('未验证空间配准');
});
it('accepts subpixel rounding rather than demanding exact integer scale ratios', () => {
  expect(analysisPairReason({ id: 1, W: 10001, H: 7501 }, { id: 2, W: 5000, H: 3750 })).toBeNull();
});
it('still refuses invalid or incompatible grids', () => {
  expect(analysisPairReason({ id: 1, W: 100, H: 100 }, { id: 2, W: 100, H: 40 })).toContain('宽高比');
  expect(analysisPairReason({ id: 1, W: 0, H: 100 }, { id: 2, W: 100, H: 100 })).toBeTruthy();
});
it('allows synchronized same-scene PAN / NOSR without extra certificates', () => {
  expect(precisePairReason({ id: 1, W: 40000, H: 30000, sceneDir: '/scene' },
    { id: 2, W: 10000, H: 7500, sceneDir: '/scene/' })).toBeNull();
  expect(precisePairReason({ id: 1, W: 40000, H: 30000, sceneDir: '/one' },
    { id: 2, W: 10000, H: 7500, sceneDir: '/two' })).toBeTruthy();
});
