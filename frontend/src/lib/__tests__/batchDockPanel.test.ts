// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { createApp, nextTick, reactive } from 'vue';
import type { App } from 'vue';
const state = vi.hoisted(() => ({ batch: {} as any, scan: vi.fn(), mounted: vi.fn() }));
vi.mock('../../stores/batchGoal', () => ({ useBatchGoalStore: () => state.batch }));
vi.mock('../../stores/chat', () => ({ useChatStore: () => ({ sessionId: 's' }) }));
vi.mock('../../stores/imageRefresh', () => ({ useImageRefreshStore: () => ({ scan: state.scan }) }));
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
import { batchDockOpen, openBatchSelection } from '../batchDock';
let app: App, root: HTMLDivElement;
function snapshot() {
  const track = { done: 2, total: 5, applicable: true, known: true };
  return { goal: { goal_id: 'g', title: '实际批次', status: 'RUNNING', phase: 'detecting', mode: 'production' },
    counts: { total: 5, verified: 2, failed: 1, excluded: 1, skipped_runtime: 0, capacity_skipped: 0, no_issue: 0, detecting: 1, in_flight_sr: 2, verifying: 1, blocked: 0 },
    progress: { detection: track, submission: track, verification: track }, report: { available: false } };
}
beforeEach(() => {
  vi.useFakeTimers(); vi.clearAllMocks(); batchDockOpen.value = false;
  state.batch = reactive({ goalId: null, snapshot: null, syncState: 'live', associate: vi.fn(), discover: vi.fn(), detach: vi.fn() });
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
it('replaces selection with actual progress, lazy details, and a completion refresh', async () => {
  openBatchSelection(); await nextTick();
  state.batch.goalId = 'g'; state.batch.snapshot = snapshot();
  root.querySelector<HTMLButtonElement>('[data-start]')!.click(); await nextTick();
  expect(root.querySelector('[data-start]')).toBeNull();
  const status = root.querySelector('[data-e2e=batch-status]')!;
  expect(status.textContent).toContain('已完成 2 · 失败 1 · 跳过 1');
  expect(status.textContent).toContain('检测中 1 · SR 执行中 2 · 验收中 1');
  expect(root.querySelector('[data-monitor]')).toBeNull();
  const details = root.querySelector('details')!; details.open = true; details.dispatchEvent(new Event('toggle')); await nextTick();
  expect(root.querySelector('[data-monitor]')).not.toBeNull();
  state.batch.snapshot.goal = { ...state.batch.snapshot.goal, status: 'COMPLETED' }; await nextTick();
  expect(state.scan).toHaveBeenCalledWith(true);
  expect(status.textContent).toContain('左侧符合条件的影像将显示更新提醒');
});
