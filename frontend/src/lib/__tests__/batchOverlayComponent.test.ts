// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { createApp, nextTick, reactive } from 'vue';
import type { App } from 'vue';
const state = vi.hoisted(() => ({ batch: {} as any, viewer: {} as any }));
vi.mock('../../stores/batchGoal', () => ({ useBatchGoalStore: () => state.batch }));
vi.mock('../../stores/viewer', () => ({ useViewerStore: () => state.viewer }));
vi.mock('../batchOverlay', async original => ({
  ...await original<typeof import('../batchOverlay')>(), loadBatchOverlay: vi.fn(),
}));
import { loadBatchOverlay } from '../batchOverlay';
import type { BatchOverlayDoc } from '../batchOverlay';
import Overlay from '../../components/BatchDetectionOverlay.vue';
let app: App, root: HTMLDivElement;
async function flush() { for (let i = 0; i < 5; i++) { await nextTick(); await Promise.resolve(); } }
beforeEach(() => {
  vi.clearAllMocks();
  window.history.replaceState(null, '', '/viewer');
  state.viewer = reactive({ batchOverlayTarget: { goalId: 'g1', itemId: 'i1', overlayReady: false }, batchOverlayOff: false, panes: [] });
  state.batch = reactive({ goalId: 'g1', snapshot: { producer: { current: { item_id: 'i1', overlay_ready: false } }, active_jobs: [], recent_jobs: [] } });
  vi.mocked(loadBatchOverlay).mockResolvedValue({ status: 'error', reason: 'http_404' });
  root = document.createElement('div'); document.body.append(root);
  app = createApp(Overlay); app.mount(root);
});
afterEach(() => { app.unmount(); root.remove(); });
it('waits for publication and loads the same item without repeated navigation or requests', async () => {
  await flush();
  expect(loadBatchOverlay).not.toHaveBeenCalled();
  expect(root.querySelector('[data-e2e=batch-overlay-error]')).toBeNull();
  state.batch.snapshot.producer.current.overlay_ready = true;
  await flush();
  expect(loadBatchOverlay).toHaveBeenCalledTimes(1);
  expect(loadBatchOverlay).toHaveBeenCalledWith({ goalId: 'g1', itemId: 'i1' }, expect.anything());
  expect(root.textContent).toContain('404');
  state.batch.snapshot = { ...state.batch.snapshot, producer: { current: { item_id: 'i1', overlay_ready: true, item_version: 9 } } };
  await flush();
  expect(loadBatchOverlay).toHaveBeenCalledTimes(1);
  state.batch.snapshot.producer.current.overlay_ready = false;
  await flush();
  expect(root.querySelector('[data-e2e=batch-overlay-error]')).toBeNull();
});
it('does not resurrect a disabled overlay when publication finishes', async () => {
  state.viewer.batchOverlayOff = true;
  state.batch.snapshot.producer.current.overlay_ready = true;
  await flush();
  expect(loadBatchOverlay).not.toHaveBeenCalled();
});
it('uses readiness from a manually selected item outside the snapshot', async () => {
  state.batch.snapshot.producer.current = null;
  state.viewer.batchOverlayTarget.overlayReady = true;
  await flush();
  expect(loadBatchOverlay).toHaveBeenCalledTimes(1);
});
it('renders published metadata after waiting and keeps legacy URL targets usable', async () => {
  vi.mocked(loadBatchOverlay).mockResolvedValue({ status: 'ready', doc: {
    provenance: 'real', candidates: [], item_version: 1,
    overlay: { url: '/overlay.png', width: 10, height: 10 },
  } as BatchOverlayDoc });
  state.batch.snapshot.producer.current.overlay_ready = true;
  await flush();
  expect(root.querySelector('[data-e2e=batch-overlay]')).not.toBeNull();
  expect(root.querySelector('[data-e2e=batch-overlay-error]')).toBeNull();
  app.unmount();
  vi.mocked(loadBatchOverlay).mockClear();
  state.viewer.batchOverlayTarget = null;
  state.batch.goalId = 'another-goal';
  window.history.replaceState(null, '', '/viewer?batch_goal=legacy&batch_item=old-item');
  app = createApp(Overlay); app.mount(root);
  await flush();
  expect(loadBatchOverlay).toHaveBeenCalledWith({ goalId: 'legacy', itemId: 'old-item' }, expect.anything());
});
