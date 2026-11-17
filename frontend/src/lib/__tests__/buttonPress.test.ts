import { expect, it } from 'vitest';
import { springScale, PRESS_SCALE, RELEASE_MS } from '../buttonPress';
it('starts compressed, slightly overshoots and settles with stiffness 260 / damping 12', () => {
  expect(springScale(0)).toBe(PRESS_SCALE);
  const samples = Array.from({length:111},(_,i)=>springScale(i/100));
  expect(Math.max(...samples)).toBeGreaterThan(1.01);
  expect(Math.max(...samples)).toBeLessThan(1.025);
  expect(springScale(RELEASE_MS/1000)).toBeCloseTo(1,3);
});
