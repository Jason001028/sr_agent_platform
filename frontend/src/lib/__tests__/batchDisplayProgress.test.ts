import { expect, it } from 'vitest';
import { batchDisplayProgress, displayPercent } from '../batchDisplayProgress';
it('uses the eligible batch denominator for all stages, never the growing repair subset', () => {
  const p = batchDisplayProgress({ eligible: 27, detected: 3, submitted: 3, verified: 3, no_issue: 0, needs_repair: 3 });
  for (const track of Object.values(p)) {
    expect(track.total).toBe(27);
    expect(displayPercent(track)).toBeCloseTo(11.111);
  }
});
it('keeps unknown and empty totals unfilled', () => {
  expect(batchDisplayProgress(null).detection.known).toBe(false);
  expect(displayPercent(batchDisplayProgress({ detected: 3 }).detection)).toBeNull();
  expect(displayPercent(batchDisplayProgress({ eligible: 0, detected: 0 }).detection)).toBeNull();
  expect(displayPercent(batchDisplayProgress({ eligible: 27 }).verification)).toBeNull();
});
it('does not count external completion or convergence as acceptance', () => {
  const p = batchDisplayProgress({ eligible: 27, detected: 27, submitted: 27, converged: 27, verified: 0, no_issue: 0 });
  expect(displayPercent(p.verification)).toBe(0);
  expect(displayPercent(p.processed)).toBe(0);
});
