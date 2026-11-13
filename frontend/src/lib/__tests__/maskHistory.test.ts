import { describe, expect, it } from 'vitest';
import { MaskHistory, replaceAutoGroup } from '../maskHistory';
import type { OwnedPoly } from '../maskHistory';
import type { Poly } from '../maskgen';
import { sourceToMaskThumb, thumbToOrig } from '../viewMath';
function auto(region = 'r1', analysis = 'analysis'): OwnedPoly {
  const p: OwnedPoly = [[0, 0], [12, 0], [12, 12], [0, 12]];
  p.autoDiff = { source: 'auto_diff', analysis_id: analysis, image_id: 1, group_id: 'group',
    region_id: region, parameter_version: 'v1', initial_points: JSON.stringify(p), needs_review: false };
  return p;
}
describe('atomic mask vector history and ownership', () => {
  it('round-trips verified source vertices through the actual export mapping', () => {
    for (const [W, H, tw, th] of [[32761, 25127, 8192, 6284], [32, 32, 16, 16], [101, 79, 7, 5]]) {
      for (const [x, y] of [[0, 0], [W-1, H-1], [31, 27], [Math.floor(W/2), Math.floor(H/2)]]) {
        const p = sourceToMaskThumb(x, y, W, H, tw, th);
        expect(thumbToOrig(p[0], p[1], W, H, tw, th)).toEqual([x, y]);
      }
    }
  });
  it('undoes/redoes 16 polygons at once and preserves manual content', () => {
    const hand: Poly = [[1, 1], [4, 1], [4, 4]];
    const before = [hand], after = replaceAutoGroup(before, Array.from({ length: 16 }, (_, i) => auto('r'+i)), 'analysis');
    const h = new MaskHistory(); h.record(before, after);
    expect(after).toHaveLength(17);
    const undone = h.undo(after)!;
    expect(undone).toEqual(before);
    const redone = h.redo(undone)!;
    expect(redone).toHaveLength(17);
    expect((redone[1] as OwnedPoly).autoDiff?.source).toBe('auto_diff');
  });
  it('replaces the same analysis idempotently and preserves edits without duplicates', () => {
    const p = auto(), other = auto('other', 'different-analysis');
    p[1][0] = 13;
    const current = [p, other];
    const next = replaceAutoGroup(current, [auto(), auto('r2')], 'analysis');
    expect(next).toHaveLength(3);
    expect(next[0]).toBe(p);
    expect(next[1]).toBe(other);
    expect(replaceAutoGroup(next, [auto(), auto('r2')], 'analysis')).toEqual(next);
  });
  it('never restores snapshots over subsequent untracked edits', () => {
    const h = new MaskHistory(), after = [auto()];
    h.record([], after); after[0][0][0] = 8;
    expect(h.undo(after)).toBeNull();
  });
  it('preserves approved regions on regeneration and round-trips approval in history', () => {
    const approved = auto(); approved.autoDiff!.reviewed = true;
    const next = replaceAutoGroup([approved], [auto(), auto('r2')], 'analysis');
    expect(next).toHaveLength(2);
    expect(next[0]).toBe(approved);
    const h = new MaskHistory(); h.record([], next);
    const before = h.undo(next)!;
    expect((h.redo(before)![0] as OwnedPoly).autoDiff?.reviewed).toBe(true);
  });
  it('records manual operations after a batch and invalidates redo on branching', () => {
    const h = new MaskHistory(), batch = [auto()], more = [...batch, auto('manual')];
    h.record([], batch); h.record(batch, more);
    const undone = h.undo(more)!;
    expect(undone).toEqual(batch);
    h.record(undone, []);
    expect(h.canRedo).toBe(false);
  });
});
