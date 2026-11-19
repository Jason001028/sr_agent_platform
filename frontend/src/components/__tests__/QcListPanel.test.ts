// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { createApp, nextTick, reactive } from 'vue';
import type { App } from 'vue';
const state = vi.hoisted(() => ({ qc: {} as any, viewer: {} as any, send: vi.fn() }));
vi.mock('../../stores/qclist', () => ({ useQcListStore: () => state.qc }));
vi.mock('../../stores/viewer', () => ({ useViewerStore: () => state.viewer }));
vi.mock('../../stores/scenes', () => ({ useScenesStore: () => ({}) }));
vi.mock('../../stores/queue', () => ({ useQueueStore: () => ({ tasks: [] }) }));
vi.mock('../../lib/batchHandoff', () => ({ requestAgentRepair: state.send }));
import Panel from '../QcListPanel.vue';
let app: App, root: HTMLDivElement, finish: () => void;
const flush = async () => { await Promise.resolve(); await nextTick(); await Promise.resolve(); };
beforeEach(() => {
  state.send.mockClear();
  state.viewer = { showErr: vi.fn(), clearSpotlight: vi.fn(), clearLights: vi.fn() };
  state.qc = reactive({ list: {}, coordinateImportRevision: 1, loaded: true,
    issues: [{ name: 'scene', row: null, col: null }], counts: { done: 0, total: 1 },
    bakeState: 'idle', bakeFails: {}, bakeFailList: [], bakeNoteList: [], targetPath: '',
    rowForScene: () => null, selectForScene: vi.fn(), select: vi.fn(), statusOf: () => undefined,
    bakeAll: vi.fn(() => new Promise<void>(resolve => { finish = resolve; })),
  });
  root = document.createElement('div'); document.body.append(root);
  app = createApp(Panel); app.mount(root);
});
afterEach(() => { app.unmount(); root.remove(); });
function click() { root.querySelector<HTMLButtonElement>('[data-e2e="qc-agent-repair"]')!.click(); }
it.each(['idle', 'running'])('waits for the entire %s batch and sends only once', async status => {
  state.qc.bakeState = status;
  click(); click(); await flush();
  expect(state.qc.bakeAll).toHaveBeenCalledTimes(1);
  expect(state.send).not.toHaveBeenCalled();
  expect(root.querySelector<HTMLButtonElement>('.qc-agent')!.disabled).toBe(true);
  state.qc.bakeState = 'done'; finish(); await flush();
  expect(state.send).toHaveBeenCalledTimes(1);
});
it('sends immediately when already parsed successfully', () => {
  state.qc.bakeState = 'done'; click();
  expect(state.qc.bakeAll).not.toHaveBeenCalled();
  expect(state.send).toHaveBeenCalledTimes(1);
});
it('retires the focused row, its lights and spotlight on handoff', () => {
  state.qc.bakeState = 'done'; click();
  expect(state.qc.select).toHaveBeenCalledWith(null);
  expect(state.viewer.clearLights).toHaveBeenCalled();
  expect(state.viewer.clearSpotlight).toHaveBeenCalled();
});
it.each(['failure', 'stopped', 'changed', 'closed'])('does not send after %s', async outcome => {
  click();
  state.qc.bakeState = outcome === 'stopped' ? 'stopped' : 'done';
  if (outcome === 'failure') state.qc.bakeFails = { scene: { reason: 'missing' } };
  if (outcome === 'changed') state.qc.coordinateImportRevision++;
  if (outcome === 'closed') state.qc.list = null;
  finish(); await flush();
  expect(state.send).not.toHaveBeenCalled();
  expect(state.viewer.showErr).toHaveBeenCalled();
});
it('reports unexpected errors and allows retry', async () => {
  state.qc.bakeAll.mockRejectedValueOnce(new Error('offline'));
  click(); await flush();
  expect(state.send).not.toHaveBeenCalled();
  expect(state.viewer.showErr).toHaveBeenCalledWith(expect.stringContaining('offline'));
  expect(root.querySelector<HTMLButtonElement>('.qc-agent')!.disabled).toBe(false);
});
