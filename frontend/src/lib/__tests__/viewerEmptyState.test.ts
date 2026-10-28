import { describe, it, expect } from 'vitest';
import { isViewerEmpty } from '../viewerEmptyState';
import { LOGO_CELLS } from '../asciiLogoFrames';
describe('viewer welcome', () => {
  const empty = { compareMode: 'off', busy: false, overlay: { visible: false }, panes: [{ rec: null }] };
  it('shows only an idle, unoccupied single pane', () => {
    expect(isViewerEmpty(empty)).toBe(true);
    expect(isViewerEmpty({ ...empty, busy: true })).toBe(false);
    expect(isViewerEmpty({ ...empty, overlay: { visible: true } })).toBe(false);
    expect(isViewerEmpty({ ...empty, compareMode: 'split' })).toBe(false);
    expect(isViewerEmpty({ ...empty, panes: [{ rec: { thumb: null } }] })).toBe(false);
    expect(isViewerEmpty({ ...empty, panes: [{ rec: { thumb: {} } }] })).toBe(false);
  });
  it('precomputes only occupied mask cells within the centered emblem', () => {
    expect(LOGO_CELLS.length).toBeGreaterThan(1000);
    for (const [x,y,coverage] of LOGO_CELLS) {
      expect(x).toBeGreaterThanOrEqual(40);
      expect(x).toBeLessThan(360);
      expect(y).toBeGreaterThanOrEqual(40);
      expect(y).toBeLessThan(360);
      expect(coverage).toBeGreaterThan(0);
      expect(coverage).toBeLessThanOrEqual(1);
    }
  });
});
