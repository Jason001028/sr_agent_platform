import { beforeEach, afterEach, expect, it, vi } from 'vitest';
import { createPinia, setActivePinia } from 'pinia';
import { useScenesStore } from '../scenes.js';
import { apiClearScenePreviews, apiHideScenes, apiRestoreHidden } from '../../lib/api.js';
import type { SceneRow } from '../../lib/scene.js';
vi.mock('../viewer.js', () => ({ useViewerStore: vi.fn() }));
vi.mock('../../lib/api.js', () => ({
  apiClearScenePreviews: vi.fn(), apiHideScenes: vi.fn(), apiRestoreHidden: vi.fn(),
}));

/** 模拟服务端隐藏集合：真实实现里检索接口已经排除这些场景，前端不再本地过滤。 */
const serverHidden = new Set<string>();

beforeEach(() => {
  serverHidden.clear();
  const saved = new Map<string, string>();
  vi.stubGlobal('localStorage', {
    getItem: (key: string) => saved.get(key) ?? null,
    setItem: (key: string, value: string) => saved.set(key, value),
  });
  vi.stubGlobal('fetch', vi.fn().mockImplementation(async () => ({ ok: true, json: async () => ({
    results: [{ id: 'a', name: 'a' }, { id: 'b', name: 'b' }]
      .filter((r) => !serverHidden.has(r.id)),
    count: 2 - serverHidden.size, scanned: 2, source: 'disk', hidden: serverHidden.size,
  }) })));
  setActivePinia(createPinia());
});
afterEach(() => { vi.unstubAllGlobals(); vi.clearAllMocks(); });

it('成功项进服务端隐藏集合，重新检索和刷新后仍隐藏；显式恢复后重新出现', async () => {
  vi.mocked(apiClearScenePreviews).mockImplementation(async (_cfg, ids) => {
    // 真实后端对 cleared / nothing 的场景写隐藏集合（按场景目录）
    serverHidden.add('a');
    expect(ids).toEqual(['a', 'b']);
    return {
      results: [
        { id: 'a', status: 'cleared', reason: null, dir: null, removed: [], skipped: [], failed: [], marked: 0 },
        { id: 'b', status: 'failed', reason: 'busy', dir: null, removed: [], skipped: [], failed: [], marked: 0 },
      ],
      summary: { cleared: 1, nothing: 0, failed: 1, skipped: 0, dirs: 1, files: 1, marked: 0,
        hidden: 1, hidden_total: serverHidden.size },
    };
  });
  vi.mocked(apiRestoreHidden).mockImplementation(async () => {
    const restored = serverHidden.size;
    serverHidden.clear();
    return { restored, hidden_total: 0 };
  });
  let store = useScenesStore();
  await store.list();
  store.setAllSelected(true);
  await store.clearSelected();
  expect(store.rows.map(r => r.id)).toEqual(['b']);
  expect(store.hiddenCount).toBe(1);
  // 服务端检索本身已排除隐藏场景：重新检索不会把它们带回来
  await store.list();
  expect(store.rows.map(r => r.id)).toEqual(['b']);
  expect(store.count).toBe(1);
  setActivePinia(createPinia()); // F5：全新的 store，只靠服务端集合
  store = useScenesStore();
  await store.list();
  expect(store.rows.map(r => r.id)).toEqual(['b']);
  expect(store.hiddenCount).toBe(1);
  await store.restoreRemoved();
  expect(store.rows.map(r => r.id)).toEqual(['a', 'b']);
  expect(store.hiddenCount).toBe(0);
});

it('清除按场景目录隐藏：只提交了一行时，同目录的另一行也从列表摘掉', async () => {
  vi.mocked(apiClearScenePreviews).mockResolvedValue({
    results: [{ id: 'a', status: 'cleared', reason: null, dir: 'X/scene',
      removed: [], skipped: [], failed: [], marked: 0 }],
    summary: { cleared: 1, nothing: 0, failed: 0, skipped: 0, dirs: 1, files: 0, marked: 0,
      hidden: 1, hidden_total: 2 },
  });
  const store = useScenesStore();
  await store.list();
  store.rows = [
    { id: 'a', name: 'a', lq_path: 'X/scene' } as SceneRow,
    { id: 'a2', name: 'a_NOSR', lq_path: 'X/scene' } as SceneRow,
    { id: 'b', name: 'b', lq_path: 'X/other' } as SceneRow,
  ];
  store.count = 3;
  await store.clearSelected();          // 什么都没勾 → 不发请求
  store.toggleSelect('a');
  await store.clearSelected();
  expect(store.rows.map(r => r.id)).toEqual(['b']);
  expect(store.count).toBe(1);
  expect(store.hiddenCount).toBe(2);
});

it('卡片垃圾篓只隐藏、不删预览缓存：按场景目录摘掉同一景的所有变体', async () => {
  vi.mocked(apiHideScenes).mockResolvedValue({
    hidden: 1, hidden_total: 1, skipped: [],
  });
  const store = useScenesStore();
  await store.list();
  const target = { id: 'a', name: 'a', lq_path: '\\\\disk\\scenes\\20260101' } as SceneRow;
  const sibling = { id: 'a2', name: 'a_NOSR', lq_path: '\\\\disk\\scenes\\20260101' } as SceneRow;
  store.rows = [target, sibling, store.rows[1]];
  store.count = 3;
  const ok = await store.hideRows([target, sibling]);
  expect(ok).toBe(true);
  // 同一场景目录的变体一起消失，别的场景留着
  expect(store.rows.map(r => r.id)).toEqual(['b']);
  expect(store.hiddenCount).toBe(1);
  expect(store.count).toBe(1);
  expect(apiHideScenes).toHaveBeenCalledWith(expect.anything(), ['a', 'a2']);
});
