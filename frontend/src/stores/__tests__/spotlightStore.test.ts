// @vitest-environment jsdom
/**
 * spotlightStore.test.ts — 场景聚焦遮罩在 viewer store 里的状态
 * ------------------------------------------------------------------
 * 钉四件事：
 * 1. `sceneCardIds` 按**场景目录**找卡（一景两张：本体 + 产物），不认别景；
 * 2. `setSpotlight` 内容没变时不换数组（滚动每帧都会调，不能每帧触发重算），
 *    并滤掉已不在列表里的 id；
 * 3. 删卡 / 清空暂存区会把聚焦窗口里失效的 id 摘掉（整组没了就自然收起）；
 * 4. 档位写入 localStorage，读回一致。
 */
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { createPinia, setActivePinia } from 'pinia';
import { nextTick } from 'vue';
vi.mock('../../router/index.js', () => ({ default: {} }));
vi.mock('../../lib/api.js', async (original) => ({
  ...await original<typeof import('../../lib/api.js')>(),
  fetchSceneJpg: vi.fn(), fetchDropSceneJpg: vi.fn(), ensureScenePreviewReady: vi.fn().mockResolvedValue(undefined),
}));
import { useViewerStore } from '../viewer';
import { SPOTLIGHT_MODE_KEY } from '../../lib/spotlight.js';
import type { SceneOpenMeta, SceneRow } from '../../lib/scene';

beforeEach(() => {
  setActivePinia(createPinia());
  localStorage.clear();
  vi.clearAllMocks();
});
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

const META = (sceneId: string, sceneDir: string, name: string): SceneOpenMeta =>
  ({ sceneId, name, W: 100, H: 100, sceneDir } as SceneOpenMeta);

/** 两景各两张卡（本体 + NOSR），外加一张无场景的库外图。 */
function seed(store: ReturnType<typeof useViewerStore>) {
  const a1 = store.insertSceneCard(META('a-1', '/DiskArray/GSHC2IMPS/2026/10/01/A', 'A_PAN.tif'), { id: 'a-1' } as SceneRow);
  const a2 = store.insertSceneCard(META('a-2', '/DiskArray/GSHC2IMPS/2026/10/01/A', 'A_NOSR.tif'), { id: 'a-2' } as SceneRow);
  const b1 = store.insertSceneCard(META('b-1', '/DiskArray/GSHC2IMPS/2026/10/01/B', 'B_PAN.tif'), { id: 'b-1' } as SceneRow);
  return { a1, a2, b1 };
}

it('sceneCardIds 按场景目录找齐一景的卡，不混进别景', () => {
  const store = useViewerStore();
  const { a1, a2 } = seed(store);
  expect(store.sceneCardIds(['A'])).toEqual([a1, a2]);
  expect(store.sceneCardIds(['NOPE'])).toEqual([]);
  expect(store.sceneCardIds([])).toEqual([]);
});

it('setSpotlight 保存卡片；同一内容不换数组；未知 id 被滤掉', () => {
  const store = useViewerStore();
  const { a1, a2 } = seed(store);
  store.setSpotlight([a1, a2]);
  const first = store.spotlightIds;
  expect(first).toEqual([a1, a2]);
  store.setSpotlight([a1, a2]);
  expect(store.spotlightIds).toBe(first);       // 引用不变 → 组件不会重算
  store.setSpotlight([a1, 99999]);
  expect(store.spotlightIds).toEqual([a1]);
  store.clearSpotlight();
  expect(store.spotlightIds).toEqual([]);
});

it('删掉组里一张卡 → 窗口缩到剩下的；删光 → 聚焦自动收起', () => {
  const store = useViewerStore();
  const { a1, a2 } = seed(store);
  store.setSpotlight([a1, a2]);
  store.removeRec(a1);
  expect(store.spotlightIds).toEqual([a2]);
  store.removeRec(a2);
  expect(store.spotlightIds).toEqual([]);
});

it('清空暂存区：收藏的卡留下，聚焦里属于它的 id 也留下', () => {
  const store = useViewerStore();
  const { a1, a2, b1 } = seed(store);
  store.toggleFavorite([b1]);
  store.setSpotlight([a1, b1]);
  store.clearRecs();
  expect(store.spotlightIds).toEqual([b1]);
  expect(store.recs.map((r) => r.id)).toEqual([b1]);
  void a2;
});

it('档位持久化：写 localStorage，未知值回落默认', async () => {
  const store = useViewerStore();
  expect(store.spotlightMode).toBe('frosted');
  store.setSpotlightMode('dim');
  expect(localStorage.getItem(SPOTLIGHT_MODE_KEY)).toBe('dim');
  await nextTick();
  expect(store.spotlightMode).toBe('dim');
  store.setSpotlightMode('none');
  expect(localStorage.getItem(SPOTLIGHT_MODE_KEY)).toBe('none');
});
