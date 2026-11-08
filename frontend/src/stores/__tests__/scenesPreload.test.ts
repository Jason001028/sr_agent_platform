import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { createPinia, setActivePinia } from 'pinia';
import { useScenesStore } from '../scenes';
import { clearPreviewCache, forgetScenePreviewBlobs, resetPreviewCache } from '../../lib/api';

const state = vi.hoisted(() => ({ revision: 'v1', resolve: vi.fn(),
  viewer: { previewDiv: 4, openSceneJpg: vi.fn() } }));
vi.mock('../viewer.js', () => ({ useViewerStore: () => state.viewer }));
vi.mock('../../lib/api.js', async original => ({
  ...await original<typeof import('../../lib/api')>(),
  apiResolveScene: (...args: unknown[]) => state.resolve(...args),
}));

beforeEach(() => {
  setActivePinia(createPinia()); resetPreviewCache(); vi.clearAllMocks(); state.revision = 'v1';
  vi.stubGlobal('localStorage', { getItem: () => null, setItem: vi.fn() });
  state.resolve.mockImplementation(async () => ({ row: {
    id: 'scene', name: 'PAN', W: 1000, H: 1000, fake: false, lq_path: '/scene',
    jpgUrl: null, hasPreview: true, previewDiv: 4, srcRev: state.revision,
  }, resolved: { mask_path: null } }));
  vi.stubGlobal('fetch', vi.fn(async () => new Response(new Blob([state.revision], { type: 'image/jpeg' }))));
  state.viewer.openSceneJpg.mockResolvedValue(undefined);
});
afterEach(() => { resetPreviewCache(); vi.unstubAllGlobals(); });

it('resolves again on open and reuses unchanged bytes from the shared cache', async () => {
  const scenes = useScenesStore();
  await scenes.preloadQuiet('/scene/PAN.tif');
  expect(await scenes.openQuiet('/scene/PAN.tif')).toBe('');
  expect(state.resolve).toHaveBeenCalledTimes(2);
  expect(fetch).toHaveBeenCalledTimes(1);
  expect(state.viewer.openSceneJpg.mock.calls[0][0].srcRev).toBe('v1');
});

it('opens fresh pixels and metadata if SR rewrites a preloaded source', async () => {
  const scenes = useScenesStore();
  await scenes.preloadQuiet('/scene/PAN.tif'); state.revision = 'v2';
  expect(await scenes.openQuiet('/scene/PAN.tif')).toBe('');
  expect(state.resolve).toHaveBeenCalledTimes(2);
  expect(fetch).toHaveBeenCalledTimes(2);
  const [meta, blob] = state.viewer.openSceneJpg.mock.calls[0];
  expect(meta.srcRev).toBe('v2');
  expect(await blob.text()).toBe('v2');
});

it.each(['clear','forget'])('respects shared cache invalidation: %s', async action => {
  const scenes = useScenesStore(); await scenes.preloadQuiet('/scene/PAN.tif');
  if (action === 'clear') clearPreviewCache(); else forgetScenePreviewBlobs(['scene']);
  expect(await scenes.openQuiet('/scene/PAN.tif')).toBe('');
  expect(fetch).toHaveBeenCalledTimes(2);
});
