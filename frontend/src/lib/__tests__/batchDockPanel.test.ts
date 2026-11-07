// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { createApp, nextTick, reactive } from 'vue';
import type { App } from 'vue';
const state = vi.hoisted(() => ({ batch: {} as any, scan: vi.fn(), mounted: vi.fn() }));
vi.mock('../../stores/batchGoal', () => ({ useBatchGoalStore: () => state.batch }));
vi.mock('../../stores/chat', () => ({ useChatStore: () => ({ sessionId: 's' }) }));
vi.mock('../../stores/imageRefresh', () => ({ useImageRefreshStore: () => ({ scan: state.scan }) }));
vi.mock('../../components/BatchControls.vue', () => ({ default: { render: () => null } }));
vi.mock('../../components/RoiToolsTab.vue', () => ({ default: { render: () => null } }));
vi.mock('../../components/AppIcon.vue', () => ({ default: { render: () => null } }));
vi.mock('../../components/BatchProgressPanel.vue', () => ({ default: { template: '<div data-monitor>逐景记录与异常</div>' } }));
vi.mock('../../components/BatchResultReport.vue', () => ({ default: { template: '<div data-report>结果报告</div>' } }));
vi.mock('../../components/BatchRepairDraft.vue', async () => {
  const { h, onMounted } = await import('vue');
  return { default: { emits: ['created'], setup(_: unknown, { emit }: any) {
    onMounted(state.mounted);
    return () => h('button', { 'data-start': '', onClick: () => emit('created') }, '开始修复');
  } } };
});
import BatchTaskDock from '../../components/BatchTaskDock.vue';
import { missionFixture } from './batchMissionFixture';
import { batchDockOpen, openBatchSelection } from '../batchDock';
let app: App, root: HTMLDivElement;
function snapshot() { return missionFixture(); }
beforeEach(() => {
  vi.useFakeTimers(); vi.clearAllMocks(); localStorage.clear(); batchDockOpen.value = false;
  state.batch = reactive({ goalId: null, snapshot: null, syncState: 'live', availableGoals: [], setFollow: vi.fn(), selectGoal: vi.fn(), refreshSnapshot: vi.fn(), associate: vi.fn(), discover: vi.fn(), detach: vi.fn() });
  root = document.createElement('div'); document.body.append(root);
  root.scrollIntoView = vi.fn();
  HTMLElement.prototype.scrollIntoView = vi.fn();
  app = createApp(BatchTaskDock); app.mount(root);
});
afterEach(() => { app.unmount(); root.remove(); vi.useRealTimers(); });
it('opens and focuses the existing dock without hidden selection work or repeated tabs', async () => {
  expect(state.mounted).not.toHaveBeenCalled();
  openBatchSelection(); await nextTick(); await nextTick();
  expect(state.mounted).toHaveBeenCalledTimes(1);
  expect(document.activeElement).toBe(root.querySelector('[data-e2e=batch-task-dock]'));
  expect(root.querySelector('[role=tablist]')).toBeNull();
  openBatchSelection(); await nextTick(); expect(state.mounted).toHaveBeenCalledTimes(1);
});
it('starts monitoring without a duplicate details gate and keeps it mounted when collapsed', async () => {
  openBatchSelection(); await nextTick();
  state.batch.goalId = 'g'; state.batch.snapshot = snapshot();
  root.querySelector<HTMLButtonElement>('[data-start]')!.click(); await nextTick();
  expect(root.querySelector('[data-start]')).toBeNull();
  expect(root.querySelector('[data-monitor]')).not.toBeNull();
  const status = root.querySelector('[data-e2e=batch-status]')!;
  expect(status.textContent).toContain('检测进度');
  expect(status.textContent).not.toContain('已完成 0 · 失败');
  const panel = root.querySelector('[data-monitor]');
  root.querySelector<HTMLButtonElement>('[data-e2e=batch-task-expand]')!.click(); await nextTick();
  expect(root.querySelector('[data-monitor]')).toBe(panel);
  expect(state.batch.detach).not.toHaveBeenCalled();
  root.querySelector<HTMLButtonElement>('[data-e2e=batch-task-expand]')!.click(); await nextTick();
  state.batch.snapshot.goal = { ...state.batch.snapshot.goal, status: 'COMPLETED' }; await nextTick();
  expect(state.scan).toHaveBeenCalledWith(true);
  expect(root.querySelector('[data-e2e=batch-tracks]')).toBeNull();
});
it('settings affect only presentation and stay independent of backend control', async () => {
  state.batch.snapshot = snapshot(); state.batch.goalId = 'g'; await nextTick();
  window.dispatchEvent(new CustomEvent('batch-task-open')); await nextTick();
  root.querySelector<HTMLButtonElement>('[data-e2e=batch-settings]')!.click(); await nextTick();
  const toggle = root.querySelector<HTMLInputElement>('[data-e2e=mission-statistics-toggle]')!;
  toggle.click(); await nextTick();
  expect(root.querySelector('[data-e2e=mission-statistics]')).not.toBeNull();
  expect(state.batch.refreshSnapshot).not.toHaveBeenCalled();
  expect(state.batch.selectGoal).not.toHaveBeenCalled();
  expect(JSON.parse(localStorage.getItem('sr.batch.mission.view.v1')!).statistics).toBe(true);
  document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' })); await nextTick(); await nextTick();
  expect(document.activeElement).toBe(root.querySelector('[data-e2e=batch-settings]'));
});
