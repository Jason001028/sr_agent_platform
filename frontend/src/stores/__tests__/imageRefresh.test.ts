// @vitest-environment jsdom
import { beforeEach, afterEach, expect, it, vi } from 'vitest';
import { reactive } from 'vue';
import { createPinia, setActivePinia } from 'pinia';
vi.mock('../../router/index.js', () => ({ default: {} }));
vi.mock('../../lib/api', () => ({ apiImageRefresh: vi.fn(), fetchImageRefreshPreview: vi.fn() }));
const state = vi.hoisted(() => ({ viewer: null as any, queue: null as any }));
vi.mock('../viewer', () => ({ useViewerStore: () => state.viewer }));
vi.mock('../queue', () => ({ useQueueStore: () => state.queue, normDir: (p: string) => p.replace(/[/\\]+$/, '') }));
import { apiImageRefresh, fetchImageRefreshPreview } from '../../lib/api';
import type { ImageRefreshResult } from '../../lib/api';
import { useImageRefreshStore } from '../imageRefresh';
function result(rev = 'path-100-1', task = 1): ImageRefreshResult {
  return { task_id: task, state: 'ready', note: 'ready', group: '/scene', kind: 'product', version: `product|${rev}`, finished_at: task,
    row: { id: 'product', name: 'NOSR.tif', srcRev: rev, W: 16, H: 16, hasPreview: true, previewDiv: 4, lq_path: '/scene', fake: false, satellite: null, sensor: null, date: null, size_bytes: 20, rel: null, jpgUrl: null } };
}
beforeEach(() => {
  setActivePinia(createPinia()); localStorage.clear(); vi.clearAllMocks();
  state.viewer = reactive({ recs: [{ id: 1, sceneId: 'input', sceneDir: '/scene' }], busy: false, drawMode: false, userIntentSeq: 0,
    noteUserIntent: vi.fn(() => { state.viewer.userIntentSeq++; }),
    insertSceneCard: vi.fn(meta => { const id = state.viewer.recs.length + 1; state.viewer.recs.push({ id, sceneId: meta.sceneId, sceneDir: meta.sceneDir }); return id; }),
    displayImageRefresh: vi.fn().mockResolvedValue(true),
  });
  state.queue = reactive({ tasks: [], list: vi.fn().mockResolvedValue(undefined) });
  vi.mocked(apiImageRefresh).mockResolvedValue(result());
  vi.mocked(fetchImageRefreshPreview).mockResolvedValue(new Blob(['jpg']));
});
afterEach(() => { useImageRefreshStore().stop(); vi.useRealTimers(); });
it('does not decorate a completed job until its preview and source identity are ready', () => {
  const store = useImageRefreshStore();
  store.accept({ ...result(), state: 'preparing' });
  store.accept({ ...result(), state: 'unavailable' });
  store.accept({ ...result(), version: 'wrong-source' });
  const missingPreview = result(); missingPreview.row!.hasPreview = false;
  store.accept(missingPreview);
  expect(store.unreadCount).toBe(0);
  expect(state.viewer.insertSceneCard).not.toHaveBeenCalled();
});

function task(id = 1) {
  return { task_id: id, state: 'COMPLETED', updated_at: id, finished_at: id,
    preview_state: 'running', params: { lq_path: '/scene' } };
}
async function idle() { await vi.waitFor(() => expect(apiImageRefresh).toHaveBeenCalled()); await new Promise(resolve => setTimeout(resolve, 0)); }

it('keeps preparing across unrelated events, then discovers a ready preview without a task timestamp change', async () => {
  state.queue.tasks = [task()];
  vi.mocked(apiImageRefresh).mockResolvedValue({ ...result(), state: 'preparing' });
  const store = useImageRefreshStore(); store.start(); await idle();
  expect(store.preparing['/scene']).toBe(true); expect(store.unreadCount).toBe(0);
  await store.scan(); expect(store.preparing['/scene']).toBe(true);
  vi.mocked(apiImageRefresh).mockResolvedValue(result());
  await store.scan(true);
  expect(store.preparing['/scene']).toBeUndefined(); expect(store.unreadCount).toBe(1);
  vi.mocked(apiImageRefresh).mockResolvedValue(result('path-100-2'));
  await store.scan(true);
  expect(store.entries[2]?.result.version).toBe('product|path-100-2');
});

it('does not let an older ready result clear another pending job in the same group', async () => {
  state.queue.tasks = [task(), task(2)];
  vi.mocked(apiImageRefresh).mockImplementation(async id => id === 1 ? result() : { ...result('path-100-2', 2), state: 'preparing' });
  const store = useImageRefreshStore(); store.start(); await idle();
  expect(store.unreadCount).toBe(1); expect(store.preparing['/scene']).toBe(true);
  state.queue.tasks[1].state = 'FAILED'; await store.scan(true);
  expect(store.preparing['/scene']).toBeUndefined();
});

it('warms one compressed preview at a time and cancels work when its card leaves the viewport', async () => {
  const store = useImageRefreshStore(); store.start(); store.accept(result());
  const other = result('other-100-1', 2); other.row!.id = 'other'; other.version = 'other|other-100-1'; store.accept(other);
  let signal: AbortSignal | undefined;
  vi.mocked(fetchImageRefreshPreview).mockImplementationOnce((_r, s) => new Promise((_resolve, reject) => {
    signal = s; s!.addEventListener('abort', () => reject(new Error('aborted')));
  }));
  store.setVisible([2, 3]);
  expect(fetchImageRefreshPreview).toHaveBeenCalledTimes(1);
  store.setVisible([3]);
  expect(signal?.aborted).toBe(true);
  await vi.waitFor(() => expect(fetchImageRefreshPreview).toHaveBeenCalledTimes(2));
  expect(vi.mocked(fetchImageRefreshPreview).mock.calls[1]?.[0].row?.id).toBe('other');
});
it('keys unread by real source revision and only marks read after successful display', async () => {
  const store = useImageRefreshStore(); store.accept(result());
  expect(store.groupCount('/scene/')).toBe(1);
  state.viewer.displayImageRefresh.mockResolvedValueOnce(false);
  expect(await store.open(2)).toBe(false); expect(store.entries[2]?.unread).toBe(true);
  expect(await store.open(2)).toBe(true); expect(store.unreadCount).toBe(0);
  expect(JSON.parse(localStorage.getItem('sr.imageRefresh.read.v1')!)).toEqual(['product|path-100-1']);
  store.accept(result('path-100-2', 2));
  expect(store.entries[2]?.unread).toBe(true); expect(store.unreadCount).toBe(1);
  expect(state.viewer.recs).toHaveLength(2); // Same filename, new revision; no duplicate record.
});
it('restores viewed versions on reload and counts actual versions in a group', async () => {
  localStorage.setItem('sr.imageRefresh.read.v1', JSON.stringify(['product|path-100-1']));
  const store = useImageRefreshStore(); store.accept(result());
  expect(store.unreadCount).toBe(0);
  const other = result('other-100-1', 2); other.row!.id = 'other'; other.version = 'other|other-100-1';
  store.accept(other); expect(store.groupCount('/scene')).toBe(1);
  store.accept(result('path-100-2', 3)); expect(store.groupCount('/scene')).toBe(2);
});
it('matches library / manual scene aliases without duplicating the input card', () => {
  const store = useImageRefreshStore(); const input = result(); input.aliases = ['input'];
  store.accept(input); expect(store.entries[1]?.unread).toBe(true);
  expect(state.viewer.insertSceneCard).not.toHaveBeenCalled();
});
it('does not acknowledge failed or superseded preview loads', async () => {
  const store = useImageRefreshStore(); store.accept(result());
  vi.mocked(fetchImageRefreshPreview).mockRejectedValueOnce(new Error('offline'));
  expect(await store.open(2)).toBe(false); expect(store.unreadCount).toBe(1);
  vi.mocked(apiImageRefresh).mockResolvedValue(result('path-100-2', 2));
  expect(await store.open(2)).toBe(false); expect(state.viewer.displayImageRefresh).not.toHaveBeenCalled();
  expect(store.entries[2]?.result.version).toContain('path-100-2');
});
