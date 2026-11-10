// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { createApp, nextTick, reactive } from 'vue';
import type { App } from 'vue';
const state = vi.hoisted(() => ({ batch: {} as any, viewer: {} as any, playback: {} as any }));
vi.mock('../../stores/batchGoal', () => ({ useBatchGoalStore: () => state.batch }));
vi.mock('../../stores/viewer', () => ({ useViewerStore: () => state.viewer }));
vi.mock('../../stores/batchPlayback', () => ({ useBatchPlaybackStore: () => state.playback }));
import Playback from '../../components/BatchRepairPlayback.vue';
let app: App, root: HTMLDivElement;
beforeEach(() => {
  state.viewer = reactive({ panes: [], drawMode: false });
  state.playback = reactive({ phase: 'idle', running: false, mounted: false, doc: null,
    label: '逐景展示', ordinal: 0, name: '', note: '',
    start: vi.fn(() => { state.playback.running = true; }), stop: vi.fn(), reset: vi.fn(), hideOverlay: vi.fn() });
  state.batch = reactive({ goalId: 'restored-goal', follow: false, playbackManaged: false, overlayCloseSeq: 0,
    setFollow: vi.fn((on: boolean) => { state.batch.follow = on; }) });
  root = document.createElement('div'); document.body.append(root);
  app = createApp(Playback); app.mount(root);
});
afterEach(() => { app.unmount(); root.remove(); });
it('does not show a playback card merely because refresh restored a batch', () => {
  expect(root.querySelector('[data-e2e=repair-playback]')).toBeNull();
  expect(state.playback.start).not.toHaveBeenCalled();
  // The controller still mounts so the existing task-panel toggle can start it.
  expect(state.batch.playbackManaged).toBe(true);
});
it('shows playback when explicitly enabled from the task panel, including initial async loading', async () => {
  state.batch.setFollow(true);
  await nextTick(); await nextTick();
  expect(state.playback.start).toHaveBeenCalledOnce();
  expect(root.querySelector('[data-e2e=repair-playback]')).not.toBeNull();
});
it('keeps pause/resume and completion controls after a presentation has started', async () => {
  for (const phase of ['paused', 'done']) {
    state.playback.phase = phase;
    await nextTick();
    expect(root.querySelector('[data-e2e=repair-playback]')).not.toBeNull();
  }
  state.playback.phase = 'idle';
  await nextTick();
  expect(root.querySelector('[data-e2e=repair-playback]')).toBeNull();
});
it('hides the card when the associated batch is cleared', async () => {
  state.playback.phase = 'paused'; await nextTick();
  state.batch.goalId = null; await nextTick();
  expect(root.querySelector('[data-e2e=repair-playback]')).toBeNull();
});
