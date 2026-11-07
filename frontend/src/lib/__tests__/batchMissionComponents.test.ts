// @vitest-environment jsdom
import { beforeEach, afterEach, expect, it, vi } from 'vitest';
import { createApp, h, nextTick, reactive } from 'vue';
import type { App } from 'vue';
import { missionFixture } from './batchMissionFixture';
const state = vi.hoisted(() => ({ batch: {} as any, viewer: {} as any }));
vi.mock('../../stores/batchGoal', () => ({ useBatchGoalStore: () => state.batch }));
vi.mock('../../stores/viewer', () => ({ useViewerStore: () => state.viewer }));
vi.mock('../../components/BatchFeedbackIcon.vue', () => ({ default: { render: () => null } }));
vi.mock('../../components/BatchQualityPanel.vue', () => ({ default: { render: () => null } }));
vi.mock('../../components/BatchResultReport.vue', () => ({ default: { render: () => null } }));
vi.mock('../api', async original => ({ ...await original<typeof import('../api')>(), apiBatchItems: vi.fn(), apiBatchReview: vi.fn() }));
import { apiBatchItems, apiBatchReview, HttpError } from '../api';
import BatchProgressPanel from '../../components/BatchProgressPanel.vue';
import BatchItemsList from '../../components/BatchItemsList.vue';
import BatchProgressTracks from '../../components/BatchProgressTracks.vue';
let app: App | undefined, root: HTMLDivElement;
async function flush() { for (let i = 0; i < 10; i++) { await Promise.resolve(); await nextTick(); } }
async function mount(component: any, props = {}) {
  root = document.createElement('div'); document.body.append(root);
  app = createApp(component, props); app.component('RouterLink', { render() { return h('span'); } }); app.mount(root); await flush();
}
const record = (n: number) => ({ item_id: 'i' + n, ordinal: n, name: 'scene-' + n, status: 'FAILED', version: 1, external_job_id: null,
  checkpoint: { target_image: { path: '/scene/' + n }, attempt: 0 }, error: 'same cause · scene ' + n });
beforeEach(() => {
  vi.clearAllMocks();
  state.batch = reactive({ snapshot: missionFixture(), goalId: 'g', feedback: {}, openItem: vi.fn().mockResolvedValue(''), refreshSnapshot: vi.fn() });
  state.viewer = reactive({ drawMode: false, recs: [], sceneOrdinalOf: vi.fn(() => 7), focusSourceBounds: vi.fn() });
  vi.mocked(apiBatchItems).mockResolvedValue({ items: [], next_after_ordinal: 0 });
});
afterEach(() => { app?.unmount(); root?.remove(); vi.useRealTimers(); });
it('renders neither empty queue/current nor any progress without a snapshot', async () => {
  state.batch.snapshot = null; await mount(BatchProgressPanel);
  expect(root.querySelector('[data-e2e=batch-panel]')).toBeNull();
});
it('limits failures to three independent rows, exposes exact causes and locates each independently', async () => {
  const s = state.batch.snapshot; s.goal.status = 'BLOCKED'; s.counts.failed = 27; s.counts.submitted = 0; s.producer.current = null;
  s.recent_jobs = Array.from({ length: 27 }, (_, n) => ({ ...record(n + 1), job_id: null, attempt: 0, image_path: '/scene/' + (n + 1) }));
  await mount(BatchProgressPanel);
  expect(root.querySelectorAll('.mission-item')).toHaveLength(3);
  expect(root.textContent).not.toContain('SR 执行失败');
  const names = root.querySelectorAll<HTMLButtonElement>('.item-content'); names[0].click(); names[1].click(); await flush();
  expect(root.textContent).toContain('same cause · scene 1'); expect(root.textContent).toContain('same cause · scene 2');
  const locate = root.querySelectorAll<HTMLButtonElement>('.locate'); locate[0].click(); locate[1].click(); await flush();
  expect(state.batch.openItem.mock.calls.map((c: any) => c[0].item_id)).toEqual(['i1', 'i2']);
});
it('keeps row details open when a fresh snapshot replaces the same item', async () => {
  await mount(BatchProgressPanel);
  root.querySelector<HTMLButtonElement>('.item-content')!.click(); await flush();
  state.batch.snapshot.producer.current = { ...state.batch.snapshot.producer.current, status: 'MASK_READY' }; await flush();
  expect(root.querySelector('.item-details')).not.toBeNull();
});
it('uses the stable Viewer group number rather than renumbering a selected subset', async () => {
  state.viewer.recs = [{ id: 8, sceneDir: '/scene/one' }];
  await mount(BatchProgressPanel);
  expect(root.querySelector('.ordinal')?.textContent).toBe('#07');
  expect(root.querySelector('.ordinal')?.getAttribute('title')).toContain('批次项 1');
});
it('renders unknown / not-applicable nodes without empty progress bars', async () => {
  const s = missionFixture();
  s.progress.submission = { done: 0, total: 0, applicable: false, known: true };
  s.progress.verification = { done: 0, total: 0, applicable: false, known: false };
  await mount(BatchProgressTracks, { snapshot: s });
  expect(root.querySelector('[data-stage=submission]')?.textContent).toContain('—');
  expect(root.querySelector('[data-stage=verification]')?.textContent).toContain('统计中');
  expect(root.querySelectorAll('[role=progressbar]')).toHaveLength(1);
});
it('pages the only complete queue and fences late replies after a goal switch', async () => {
  vi.mocked(apiBatchItems).mockImplementation(async (_id, cursor) => ({ items: cursor ? [record(21)] : Array.from({ length: 20 }, (_, n) => record(n + 1)), next_after_ordinal: cursor ? 21 : 20 }));
  await mount(BatchItemsList);
  const next = () => [...root.querySelectorAll<HTMLButtonElement>('footer button')].at(-1)!;
  expect(root.querySelectorAll('.mission-item')).toHaveLength(20); next().click(); await flush();
  expect(apiBatchItems).toHaveBeenLastCalledWith('g', 20, '');
  expect(root.querySelectorAll('.mission-item')).toHaveLength(1);
  let release!: (r: any) => void;
  vi.mocked(apiBatchItems).mockImplementationOnce(() => new Promise(r => { release = r; }));
  root.querySelector<HTMLButtonElement>('header button')!.click(); await flush();
  vi.mocked(apiBatchItems).mockResolvedValueOnce({ items: [{ ...record(1), name: 'new-goal' }], next_after_ordinal: 1 });
  state.batch.goalId = 'g2'; await flush(); release({ items: [{ ...record(99), name: 'old-goal-late' }], next_after_ordinal: 99 }); await flush();
  expect(root.textContent).toContain('new-goal'); expect(root.textContent).not.toContain('old-goal-late');
});
it('preserves guarded mask review and refreshes after a 409 without an automatic retry', async () => {
  const item = { ...record(1), status: 'MASK_READY', checkpoint: { target_image: { path: '/pan' }, review_required: true } };
  vi.mocked(apiBatchItems).mockResolvedValue({ items: [item], next_after_ordinal: 1 });
  vi.mocked(apiBatchReview).mockRejectedValueOnce(new HttpError(409, 'version changed'));
  await mount(BatchItemsList); root.querySelector<HTMLButtonElement>('.item-content')!.click(); await flush();
  root.querySelector<HTMLButtonElement>('.review-actions button')!.click(); await flush();
  expect(apiBatchReview).toHaveBeenCalledTimes(1);
  expect(root.textContent).toContain('复核对象已变化');
  expect(apiBatchItems).toHaveBeenCalledTimes(2);
});
it('refreshes the visible queue from snapshot watermarks without postponing on dense updates', async () => {
  vi.useFakeTimers();
  vi.mocked(apiBatchItems).mockResolvedValueOnce({ items: [record(1)], next_after_ordinal: 1 });
  await mount(BatchItemsList);
  vi.mocked(apiBatchItems).mockResolvedValue({ items: [{ ...record(1), status: 'SUCCEEDED', name: 'fresh-result' }], next_after_ordinal: 1 });
  state.batch.snapshot.goal.last_seq = 2; await nextTick();
  await vi.advanceTimersByTimeAsync(100);
  state.batch.snapshot.goal.last_seq = 3; await nextTick();
  await vi.advanceTimersByTimeAsync(250); await flush();
  expect(apiBatchItems).toHaveBeenCalledTimes(2);
  expect(root.querySelectorAll('.mission-item')).toHaveLength(1);
  expect(root.textContent).toContain('fresh-result');
});
