import { beforeEach, afterEach, expect, it, vi } from 'vitest';
import { createPinia, setActivePinia } from 'pinia';
import { useScenesStore } from '../scenes.js';
import { apiClearScenePreviews } from '../../lib/api.js';
vi.mock('../viewer.js', () => ({ useViewerStore: vi.fn() }));
vi.mock('../../lib/api.js', () => ({ apiClearScenePreviews: vi.fn() }));

beforeEach(() => {
  const saved = new Map<string, string>();
  vi.stubGlobal('localStorage', {
    getItem: (key: string) => saved.get(key) ?? null,
    setItem: (key: string, value: string) => saved.set(key, value),
  });
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => ({
    results: [{ id: 'a', name: 'a' }, { id: 'b', name: 'b' }],
    count: 2, scanned: 2, source: 'disk',
  }) }));
  setActivePinia(createPinia());
});
afterEach(() => { vi.unstubAllGlobals(); vi.clearAllMocks(); });

it('成功项在重新检索和刷新后仍隐藏，失败项保留；显式恢复后重新出现', async () => {
  vi.mocked(apiClearScenePreviews).mockResolvedValue({
    results: [
      { id: 'a', status: 'cleared', reason: null, dir: null, removed: [], skipped: [], failed: [], marked: 0 },
      { id: 'b', status: 'failed', reason: 'busy', dir: null, removed: [], skipped: [], failed: [], marked: 0 },
    ],
    summary: { cleared: 1, nothing: 0, failed: 1, skipped: 0, dirs: 1, files: 1, marked: 0 },
  });
  let store = useScenesStore();
  await store.list();
  store.setAllSelected(true);
  await store.clearSelected();
  expect(store.rows.map(r => r.id)).toEqual(['b']);
  await store.list();
  expect(store.rows.map(r => r.id)).toEqual(['b']);
  setActivePinia(createPinia()); // F5: create a fresh store using persisted storage
  store = useScenesStore();
  await store.list();
  expect(store.rows.map(r => r.id)).toEqual(['b']);
  expect(store.count).toBe(1);
  await store.restoreRemoved();
  expect(store.rows.map(r => r.id)).toEqual(['a', 'b']);
  expect(store.removedIds.size).toBe(0);
});
