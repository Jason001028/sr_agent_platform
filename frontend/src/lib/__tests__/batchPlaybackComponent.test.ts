// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { createApp, nextTick, reactive } from 'vue';
import type { App } from 'vue';
const state = vi.hoisted(() => ({ batch: {} as any, viewer: {} as any, playback: {} as any }));
vi.mock('../../stores/batchGoal', () => ({ useBatchGoalStore: () => state.batch }));
vi.mock('../../stores/viewer', () => ({ useViewerStore: () => state.viewer }));
vi.mock('../../stores/batchPlayback', () => ({ useBatchPlaybackStore: () => state.playback }));
import Playback from '../../components/BatchRepairPlayback.vue';
import { agentRepairSettings, DEFAULT_AGENT_REPAIR_SETTINGS } from '../agentRepairSettings';
let app: App, root: HTMLDivElement;
beforeEach(() => {
  agentRepairSettings.value = { ...DEFAULT_AGENT_REPAIR_SETTINGS };
  state.viewer = reactive({ panes: [], drawMode: false });
  state.playback = reactive({ phase: 'idle', running: false, mounted: false, doc: null,
    label: '逐景展示', ordinal: 0, name: '', note: '',
    start: vi.fn(() => { state.playback.running = true; }), stop: vi.fn(), reset: vi.fn(), hideOverlay: vi.fn() });
  state.batch = reactive({ goalId: 'restored-goal', pendingAutoPreview: null, follow: false, playbackManaged: false, overlayCloseSeq: 0,
    consumeAutoPreview: vi.fn((id: string) => {
      if (state.batch.pendingAutoPreview !== id) return false;
      state.batch.pendingAutoPreview = null;
      return true;
    }),
    setFollow: vi.fn((on: boolean) => { state.batch.follow = on; }) });
  state.batch.clearOverlay = vi.fn(() => { state.batch.follow = false; state.batch.overlayCloseSeq++; });
  root = document.createElement('div'); document.body.append(root);
  app = createApp(Playback); app.mount(root);
});
afterEach(() => { app.unmount(); root.remove(); });
it('does not show playback when refresh restores a running Agent batch', async () => {
  state.batch.snapshot = { goal: { execution_owner: 'local_agent', status: 'RUNNING' } };
  await nextTick();
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
  state.viewer.panes = [{ rec: { thumb: {} } }];
  for (const phase of ['paused', 'done']) {
    state.playback.phase = phase;
    await nextTick();
    expect(root.querySelector('[data-e2e=repair-playback]')).not.toBeNull();
  }
  state.playback.phase = 'idle';
  await nextTick();
  expect(root.querySelector('[data-e2e=repair-playback]')).toBeNull();
});
it('hides the previous playback label on a blank canvas', async () => {
  state.playback.phase = 'paused';
  await nextTick();
  expect(root.querySelector('[data-e2e=repair-playback]')).toBeNull();
});
it('shows a paused load failure before the first image opens', async () => {
  state.playback.phase = 'paused';
  state.playback.note = '加载影像超时。可继续重试。';
  await nextTick();
  expect(root.textContent).toContain('加载影像超时');
  expect(root.querySelector('.repair-toggle')?.textContent).toBe('继续');
});
it('closes an active presentation when the associated batch is cancelled', async () => {
  state.batch.setFollow(true);
  await nextTick();
  expect(root.querySelector('[data-e2e=repair-playback]')).not.toBeNull();
  state.batch.snapshot = { goal: { status: 'CANCELLED' } };
  await nextTick();
  expect(state.playback.hideOverlay).toHaveBeenCalledOnce();
  expect(root.querySelector('[data-e2e=repair-playback]')).toBeNull();
});
it('hides the card when the associated batch is cleared', async () => {
  state.playback.phase = 'paused'; await nextTick();
  state.batch.goalId = null; await nextTick();
  expect(root.querySelector('[data-e2e=repair-playback]')).toBeNull();
});

it('auto-starts the preview once for a fresh Agent start', async () => {
  state.batch.isTerminal = false;
  state.batch.snapshot = { goal: { execution_owner: 'local_agent', status: 'RUNNING' } };
  state.batch.pendingAutoPreview = state.batch.goalId;
  await nextTick();
  expect(state.batch.setFollow).toHaveBeenCalledWith(true);
  expect(state.batch.pendingAutoPreview).toBeNull();
  state.batch.setFollow(false);
  state.batch.setFollow.mockClear();
  state.batch.snapshot = { ...state.batch.snapshot };
  await nextTick();
  expect(state.batch.setFollow).not.toHaveBeenCalled();
});

it('does not auto-start the preview when the setting is off or the repair is a worker batch', async () => {
  agentRepairSettings.value = { ...DEFAULT_AGENT_REPAIR_SETTINGS, auto_play_preview: false };
  state.batch.isTerminal = false;
  state.batch.snapshot = { goal: { execution_owner: 'local_agent', status: 'RUNNING' } };
  state.batch.pendingAutoPreview = state.batch.goalId;
  await nextTick();
  expect(state.batch.setFollow).not.toHaveBeenCalled();
  agentRepairSettings.value = { ...DEFAULT_AGENT_REPAIR_SETTINGS, auto_play_preview: true };
  state.batch.snapshot = { goal: { execution_owner: 'worker', status: 'RUNNING' } };
  await nextTick();
  expect(state.batch.setFollow).not.toHaveBeenCalled();
});
