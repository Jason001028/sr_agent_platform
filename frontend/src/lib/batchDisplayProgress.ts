import type { BatchCounts, BatchProgressTrack } from './api';

/** Use the batch-wide denominator. verified is backend-owned, never external Job completion. */
export function batchDisplayProgress(counts: Partial<BatchCounts> | null | undefined) {
  const total = counts?.eligible;
  const known = typeof total === 'number' && Number.isFinite(total) && total >= 0;
  const denominator = known ? total : 0;
  const track = (done: number | undefined): BatchProgressTrack => ({
    done: typeof done === 'number' && Number.isFinite(done) ? Math.max(0, done) : 0,
    total: denominator, applicable: denominator > 0,
    known: known && typeof done === 'number' && Number.isFinite(done),
  });
  return {
    detection: track(counts?.detected), submission: track(counts?.submitted),
    verification: track(counts?.verified),
    processed: track(counts?.verified == null || counts?.no_issue == null
      ? undefined : counts.verified + counts.no_issue),
  };
}
export function displayPercent(track: BatchProgressTrack): number | null {
  return track.known && track.applicable && track.total > 0
    ? Math.max(0, Math.min(100, track.done / track.total * 100)) : null;
}
