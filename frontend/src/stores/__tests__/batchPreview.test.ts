// @vitest-environment jsdom
import { afterEach, beforeEach, it, expect, vi } from 'vitest';
import { nextTick } from 'vue';
import { createPinia, setActivePinia } from 'pinia';
vi.mock('../../router/index.js', () => ({ default: {} }));
vi.mock('../../lib/api.js', async (original) => ({
  ...await original<typeof import('../../lib/api.js')>(),
  fetchSceneJpg: vi.fn(), ensureScenePreviewReady: vi.fn().mockResolvedValue(undefined),
}));
import { browserKit } from '../../lib/browserKit';
import { useViewerStore } from '../viewer';
import { fetchSceneJpg, ensureScenePreviewReady } from '../../lib/api';
import type { SceneRow, SceneOpenMeta } from '../../lib/scene';
beforeEach(() => { setActivePinia(createPinia()); vi.clearAllMocks(); vi.mocked(ensureScenePreviewReady).mockResolvedValue(undefined); });
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });
it.each([10, 50, 100])('enqueues %i cards without fetching detailed JPEGs', async (count) => {
  const store = useViewerStore();
  const begin = performance.now();
  for (let i = 0; i < count; i++) {
    const id = store.insertSceneCard({ sceneId: String(i), name: String(i), W: 100, H: 100 } as SceneOpenMeta,
      { id: String(i), thumbnail_url: `/disk-array/${i}/${i}_thumb.jpg` } as SceneRow);
    await store.bakeCardPixels(id);
  }
  console.log(JSON.stringify({count, enqueueMs: performance.now() - begin}));
  expect(fetchSceneJpg).not.toHaveBeenCalled();
  expect(ensureScenePreviewReady).not.toHaveBeenCalled();
  expect(store.recs.every(r => !r.thumb && !r.src && !!r.card)).toBe(true);
});

it.each([256, 512, 1024])('restores repeatedly after real budget eviction at %i MB', async (budget) => {
  vi.stubGlobal('createImageBitmap', vi.fn().mockResolvedValue({ width: 2, height: 2, close() {} }));
  vi.spyOn(browserKit, 'createCanvas').mockImplementation((width, height) => ({
    width, height, getContext: () => ({ drawImage() {}, putImageData() {},
      getImageData: () => ({ data: new Uint8ClampedArray(16).fill(100) }) }),
  } as never));
  vi.stubGlobal('ImageData', class { constructor(public data: unknown, public width: number, public height: number) {} });
  vi.mocked(fetchSceneJpg).mockResolvedValue(new Blob(['jpeg']));
  const store = useViewerStore();
  store.setPreviewMemoryMB(budget);
  const id = store.insertSceneCard({ sceneId: 'a', name: 'a', W: 200, H: 200, lqPath: '/scene' } as SceneOpenMeta,
    { id: 'a' } as SceneRow);
  for (let round = 0; round < 3; round++) {
    await Promise.all([store.activate(id), store.activate(id)]);
    const rec = store.recs.find(r => r.id === id)!;
    expect(rec.thumb).toBeTruthy();
    expect(rec.card).toBeTruthy();
    expect(rec.file.size).toBe(0);
    expect(rec.sceneId).toBe('a');
    const other = store.insertSceneCard({ sceneId: 'b' + round, name: 'b' + round } as SceneOpenMeta,
      { id: 'b' + round } as SceneRow);
    await store.activate(other);
    // Account for a large resident canvas without allocating gigabytes in the test runner.
    rec.thumb!.width = 20000;
    rec.thumb!.height = 20000;
    store.setPreviewMemoryMB(budget === 256 ? 512 : 256);
    store.setPreviewMemoryMB(budget);
    await nextTick();
    expect(rec.thumb).toBeNull();
  }
  await store.activate(id);
  expect(store.recs.find(r => r.id === id)!.thumb).toBeTruthy();
  vi.restoreAllMocks(); vi.unstubAllGlobals();
});

it('shares pending loads, drops deleted results and retries failed requests', async () => {
  const store = useViewerStore();
  const id = store.insertSceneCard({ sceneId: 'pending', name: 'pending', W: 100, H: 100 } as SceneOpenMeta,
    { id: 'pending' } as SceneRow);
  let reject!: (error: Error) => void;
  vi.mocked(fetchSceneJpg).mockImplementationOnce(() => new Promise((_, fail) => { reject = fail; }));
  const first = store.activate(id);
  const second = store.activate(id);
  await new Promise(done => setTimeout(done, 0));
  expect(fetchSceneJpg).toHaveBeenCalledTimes(1);
  reject(new Error('offline'));
  await Promise.all([first, second]);
  expect(store.recs[0].card).toBeTruthy();
  let resolve!: (blob: Blob) => void;
  vi.mocked(fetchSceneJpg).mockImplementationOnce(() => new Promise(done => { resolve = done; }));
  const retry = store.activate(id);
  await new Promise(done => setTimeout(done, 0));
  expect(fetchSceneJpg).toHaveBeenCalledTimes(2);
  store.recs.splice(0);
  resolve(new Blob(['late']));
  await retry;
  expect(store.recs).toHaveLength(0);
});

it.each(['A-first', 'B-first'])('restores the final A intent after A → B → A (%s)', async (order) => {
  vi.stubGlobal('createImageBitmap', vi.fn().mockResolvedValue({ width: 2, height: 2, close() {} }));
  vi.spyOn(browserKit, 'createCanvas').mockImplementation((width, height) => ({
    width, height, getContext: () => ({ drawImage() {}, putImageData() {},
      getImageData: () => ({ data: new Uint8ClampedArray(16).fill(100) }) }),
  } as never));
  vi.stubGlobal('ImageData', class { constructor(public data: unknown, public width: number, public height: number) {} });
  const pending = new Map<number, (blob: Blob) => void>();
  vi.mocked(fetchSceneJpg).mockImplementation((_cfg, _row, div) => new Promise(resolve => {
    pending.set(div!, resolve);
  }));
  const store = useViewerStore();
  const id = store.insertSceneCard({ sceneId: 'race', name: 'race', W: 200, H: 200 } as SceneOpenMeta,
    { id: 'race' } as SceneRow);
  store.setPreviewDiv(4);
  const a = store.activate(id);
  await new Promise(done => setTimeout(done, 0));
  store.setPreviewDiv(8);
  const b = store.activate(id);
  await new Promise(done => setTimeout(done, 0));
  store.setPreviewDiv(4);
  const finalA = store.activate(id);
  await new Promise(done => setTimeout(done, 0));
  expect(fetchSceneJpg).toHaveBeenCalledTimes(2); // Shared A network, fresh final write intent.
  for (const div of order === 'A-first' ? [4, 8] : [8, 4]) {
    pending.get(div)!(new Blob(['jpeg']));
    await new Promise(done => setTimeout(done, 0));
  }
  await Promise.all([a, b, finalA]);
  expect(store.recs[0].thumb).toBeTruthy();
  expect(store.recs[0].detailDiv).toBe(4);
  expect(store.recs[0].statusCls).toBe('ok');
  expect(store.activeId).toBe(id);
});
