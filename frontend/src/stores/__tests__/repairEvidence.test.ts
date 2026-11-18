import { beforeEach, afterEach, describe, it, expect, vi } from 'vitest';
import { createPinia, setActivePinia } from 'pinia';
import { useRepairEvidenceStore } from '../repairEvidence';

const snap = (id: string, pending = false) => ({ report_id: id, goal_id: id, revision: 1, status: 'ready', evidence_counts: { ready: 65 }, analysis_counts: pending ? { queued: 1 } : { ready: 65 }, coverage: { total_scenes: 2, final_regions: 65, analyzed: 65, unanalyzed: 0 } });
const page = (offset = 0) => ({ rows: Array.from({ length: Math.min(5, 65-offset) }, (_, i) => ({ row_id: String(offset+i) })), total: 65, report_total: 65, offset, next_cursor: offset+5<65 ? 'cursor-'+offset : null });
const response = (value: unknown, status = 200) => ({ ok: status === 200, status, json: async () => value }) as Response;
const flush = async () => { for (let i=0; i<15; i++) await Promise.resolve(); };
beforeEach(() => { setActivePinia(createPinia()); vi.useFakeTimers(); });
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

describe('evidence report state', () => {
  it('loads analysis history on demand and discards it when another report is selected', async () => {
    let release!: (r: Response) => void;
    const fetcher = vi.fn((url: string) => {
      if (url.endsWith('/history')) return new Promise<Response>(resolve => { release=resolve; });
      return Promise.resolve(response(url.includes('/rows?') ? page() : snap('a')));
    });
    vi.stubGlobal('fetch', fetcher);
    const store=useRepairEvidenceStore();store.start('a');await flush();
    expect(fetcher.mock.calls.some(([url])=>url.endsWith('/history'))).toBe(false);
    store.toggleHistory('row');await flush();expect(store.historyLoading).toBe(true);
    store.start('b');release(response({attempts:[{attempt_id:'old'}]}));await flush();
    expect(store.history).toEqual([]);expect(store.historyRowId).toBe('');expect(store.historyLoading).toBe(false);store.stop();
  });
  it('keeps history failure separate from the report and closes it on page changes', async () => {
    const fetcher=vi.fn(async (url: string) => url.endsWith('/history') ? response({detail:'unavailable'},503) : response(url.includes('/rows?') ? page() : snap('a')));
    vi.stubGlobal('fetch',fetcher);const store=useRepairEvidenceStore();store.start('a');await flush();
    store.toggleHistory('row');await flush();expect(store.historyError).toContain('unavailable');
    expect(store.rows).toHaveLength(5);expect(store.error).toBe('');
    store.turn(5);await flush();expect(store.historyRowId).toBe('');expect(store.historyError).toBe('');store.stop();
  });
  it('loads only one page, handles 65 rows and resets filters to page one', async () => {
    const fetcher = vi.fn(async (url: string) => {
      if (!url.includes('/rows?')) return response(snap('a'));
      const u = new URL(url, 'http://localhost'); expect(u.searchParams.get('limit')).toBe('5'); return response(page(Number(u.searchParams.get('offset'))));
    });
    vi.stubGlobal('fetch', fetcher);
    const store = useRepairEvidenceStore(); store.start('a'); await flush();
    expect(store.rows).toHaveLength(5); store.turn(5); await flush();
    expect(store.offset).toBe(5); expect(fetcher.mock.calls.some(([url]) => url.includes('cursor='))).toBe(true);
    store.turn(5); await flush(); expect(store.rows).toHaveLength(5);
    store.evidenceFilter = 'failed'; await flush(); expect(store.offset).toBe(0);
    store.stop();
  });
  it('ignores late results after another report is selected', async () => {
    let release!: (r: Response) => void;
    vi.stubGlobal('fetch', vi.fn((url: string) => {
      if (url.endsWith('/a')) return new Promise<Response>(resolve => { release=resolve; });
      return Promise.resolve(response(url.includes('/rows?') ? page() : snap('b')));
    }));
    const store = useRepairEvidenceStore(); store.start('a'); store.start('b'); await flush();
    release(response(snap('a'))); await flush();
    expect(store.report?.report_id).toBe('b'); store.stop();
  });
  it('keeps polling queued analysis and stops polling when complete or unmounted', async () => {
    let pending = true;
    const fetcher=vi.fn(async (url: string) => response(url.includes('/rows?') ? page() : snap('a', pending)));
    vi.stubGlobal('fetch', fetcher); const store=useRepairEvidenceStore();store.start('a');await flush();
    pending=false;await vi.advanceTimersByTimeAsync(5000);const calls=fetcher.mock.calls.length;
    await vi.advanceTimersByTimeAsync(10000);expect(fetcher).toHaveBeenCalledTimes(calls);
    store.stop();await vi.advanceTimersByTimeAsync(10000);expect(fetcher).toHaveBeenCalledTimes(calls);
  });
  it('returns to page one when the server invalidates a paging cursor', async () => {
    vi.stubGlobal('fetch',vi.fn(async (url: string) => response(url.includes('/rows?') ? (url.includes('cursor=') ? {detail:'cursor_stale'} : page()) : snap('a'),url.includes('cursor=') ? 409 : 200)));
    const store=useRepairEvidenceStore();store.start('a');await flush();store.turn(5);await flush();
    expect(store.offset).toBe(0);expect(store.rows).toHaveLength(5);store.stop();
  });
});
