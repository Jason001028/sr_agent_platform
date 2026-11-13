// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { createApp, nextTick, reactive } from 'vue';
import type { App } from 'vue';

const state = vi.hoisted(() => ({
  chat: {} as any, batch: {} as any, viewer: {} as any,
}));
vi.mock('../../stores/chat', () => ({ useChatStore: () => state.chat }));
vi.mock('../../stores/batchGoal', () => ({ useBatchGoalStore: () => state.batch }));
vi.mock('../../stores/viewer', () => ({ useViewerStore: () => state.viewer }));
vi.mock('../../lib/viewerContext', () => ({
  snapshotWorkspace: () => ({
    version: 0, items: [], total: 0, local_only: 0, truncated: false,
  }),
}));
vi.mock('../../lib/api', () => {
  class HttpError extends Error {
    status: number;
    constructor(status: number, message = '') { super(message); this.status = status; }
  }
  return {
    HttpError,
    apiConfirmRepair: vi.fn(),
    apiStartRepair: vi.fn(),
    apiRepairStatus: vi.fn(),
  };
});
vi.mock('../../lib/batchDock', () => ({ requestQualityReview: vi.fn() }));

import AgentRepairPlanCard from '../AgentRepairPlanCard.vue';
import { apiConfirmRepair, apiRepairStatus, apiStartRepair } from '../../lib/api';
import { requestQualityReview } from '../../lib/batchDock';

const PLAN_ID = 'plan_' + 'a'.repeat(24);
const plan = (over: Record<string, unknown> = {}) => ({
  kind: 'repair_plan.v1', contract_version: 'repair_plan.v1', plan_id: PLAN_ID,
  version: 1, scope: 'batch', status: 'READY', confirmation_required: true,
  targets: { total: 2, excluded: 0, applicable: 2, items: [] },
  strategy: {
    detection: '现有差异检测管线', detection_method: 'absdiff',
    mask: 'auto_or_existing', execution: 'existing_batch_runtime',
  },
  mode: 'demo', execution: { allowed: true, mode: 'demo', reason: null },
  manifest_ref: 'manifest:test', ...over,
});
const counts = (over: Record<string, unknown> = {}) => ({
  total: 2, eligible: 2, detected: 2, detection_pending: 0, needs_repair: 2,
  submitted: 2, verified: 2, verified_demo: 2, verified_production: 0,
  verified_other: 0, converged: 2, no_issue: 0, failed: 0, blocked: 0,
  excluded: 0, skipped_runtime: 0, cancelled: 0, capacity_skipped: 0,
  unknown_jobs: 0, detecting: 0, submitting: 0, waiting_sr: 0,
  verifying: 0, in_flight_sr: 0, ...over,
});
const progress = (n: number) => ({
  detection: { done: n, total: n, applicable: true, known: true },
  submission: { done: n, total: n, applicable: true, known: true },
  verification: { done: n, total: n, applicable: true, known: true },
});
const status = (over: Record<string, unknown> = {}) => ({
  plan: plan(), goal: null, counts: null, items: [],
  quality: null, quality_reviews: { approved: 0, rejected: 0, pending: 0 },
  report: { available: false, updated_at: null }, ...over,
});

let app: App | undefined;
let root: HTMLDivElement;
async function flush() {
  for (let i = 0; i < 20; i++) { await Promise.resolve(); await nextTick(); }
}
async function mount(data: unknown) {
  root = document.createElement('div');
  document.body.append(root);
  app = createApp(AgentRepairPlanCard, { data });
  app.mount(root);
  await flush();
}
beforeEach(() => {
  vi.resetAllMocks();
  state.chat = reactive({ sessionId: 's', newSession: vi.fn(), error: '' });
  state.batch = {
    goalId: null, snapshot: null,
    attach: vi.fn().mockResolvedValue(true),
    selectGoal: vi.fn().mockResolvedValue(true),
    openItem: vi.fn().mockResolvedValue(''),
  };
  state.viewer = reactive({ workspaceVersion: 0, recsOrdered: [] });
  vi.mocked(apiConfirmRepair).mockImplementation(async () => plan({ status: 'CONFIRMED' }) as never);
  vi.mocked(apiRepairStatus).mockResolvedValue(status() as never);
});
afterEach(() => { app?.unmount(); app = undefined; root?.remove(); });

it('shows the plan stage and confirms/starts on the same card', async () => {
  await mount(plan());
  expect(root.textContent).toContain('修复计划 · 2 景');
  expect(root.querySelector('[data-e2e=repair-plan-confirm]')).not.toBeNull();
  expect(root.querySelector('[data-e2e=repair-plan-start]')).not.toBeNull();
});

it('transitions the same card into the running stage after start', async () => {
  vi.mocked(apiStartRepair).mockResolvedValueOnce({
    plan: plan({ status: 'STARTED' }),
    goal: { goal_id: 'g1' }, reused: false,
  } as never);
  vi.mocked(apiRepairStatus)
    .mockResolvedValueOnce(status() as never)
    .mockResolvedValue(status({
      plan: plan({ status: 'STARTED' }),
      goal: { goal_id: 'g1', status: 'RUNNING', phase: 'dispatching', monitoring_done: false },
      counts: counts({ verified: 1, submitted: 2, detected: 2 }),
      progress: progress(1),
      items: [{ item_id: 'i1', ordinal: 1, name: 'SCENE_A', status: 'WAITING_SR',
                attempt: 1, job_id: 7, image_path: '/a/PAN.tif', overlay_ready: true }],
    }) as never);
  await mount(plan());
  root.querySelector<HTMLButtonElement>('[data-e2e=repair-plan-start]')!.click();
  await flush();
  expect(apiStartRepair).toHaveBeenCalledTimes(1);
  expect(root.textContent).toContain('智能修复');
  expect(root.textContent).toContain('SCENE_A');
  expect(root.textContent).toContain('已验收 1');
  expect(root.querySelector('[data-e2e=repair-task-actions]')).not.toBeNull();
});

it('switches to a deterministic demo summary and does not fake a result artifact', async () => {
  vi.mocked(apiRepairStatus).mockResolvedValue(status({
    plan: plan({ status: 'STARTED',
                 targets: { total: 12, excluded: 0, applicable: 12, items: [] } }),
    goal: { goal_id: 'g1', status: 'COMPLETED', phase: 'done',
            monitoring_done: true, monitor_done: true },
    counts: counts({ total: 12, eligible: 12, needs_repair: 10, detected: 12,
                     submitted: 10, verified: 10, failed: 2 }),
    progress: progress(10),
    items: [
      { item_id: 'i1', ordinal: 1, name: 'A', status: 'SUCCEEDED', attempt: 1,
        verification_ok: true, job_id: 1, image_path: '/a/PAN.tif', overlay_ready: true },
    ],
  }) as never);
  await mount(plan({ status: 'STARTED' }));
  const text = root.textContent ?? '';
  expect(text).toContain('修复结果');
  expect(text).toContain('本批次 12 景影像的演示 / Fake SR 计算已结束');
  expect(text).toContain('10 景通过演示契约验证');
  expect(text).toContain('2 景失败或受阻');
  expect(text).toContain('不产生真实 SR 产物');
  expect(text).toContain('不代表真实 SR 质量验收');
  // Demo has no passed artifacts layer, so the original PAN must not be
  // offered as a repair result, and there is nothing to quality-review.
  expect(root.querySelector('[data-e2e=repair-view-result]')).toBeNull();
  expect(root.querySelector('[data-e2e=repair-start-review]')).toBeNull();
});

it('does not treat a missing monitoring_done marker as convergence', async () => {
  vi.mocked(apiRepairStatus).mockResolvedValue(status({
    plan: plan({ status: 'STARTED' }),
    goal: { goal_id: 'g1', status: 'COMPLETED', phase: 'done' },
    counts: counts(),
    progress: progress(2),
    items: [],
  }) as never);
  await mount(plan({ status: 'STARTED' }));
  expect(root.querySelector('[data-e2e=repair-result-summary]')).toBeNull();
  expect(root.querySelector('[data-e2e=agent-repair-task]')!.getAttribute('data-stage'))
    .toBe('running');
});

it('shows the verified SR output and opens the existing review block directly', async () => {
  vi.mocked(apiRepairStatus).mockResolvedValue(status({
    plan: plan({ status: 'STARTED', mode: 'production' }),
    goal: { goal_id: 'g1', status: 'WAITING_EXTERNAL',
            phase: 'waiting_quality_review', monitoring_done: false },
    counts: counts({ verified: 0, verified_demo: 0, verified_production: 0,
                     in_flight_sr: 0 }),
    progress: progress(1),
    items: [
      { item_id: 'i1', ordinal: 1, name: 'A', status: 'AWAITING_REVIEW', attempt: 1,
        quality_review_id: 'rev-1', job_id: 9,
        image_path: '/a/PAN_input.tif', output_path: '/a/PAN_job9.tif',
        overlay_ready: true },
    ],
    quality: { waiting_scenes: 1, rejected_scenes: 0, items: [] },
    quality_reviews: { approved: 0, rejected: 0, pending: 1 },
    quality_products: { approved: 0, rejected: 0, pending: 1 },
    quality_regions: { approved: 1, rejected: 0, pending: 2, total: 3 },
  }) as never);
  await mount(plan({ status: 'STARTED', mode: 'production' }));
  const text = root.textContent ?? '';
  expect(text).toContain('等待人工质量复核');
  expect(text).toContain('2 个区域待审');
  expect(text).toContain('区域通过 1');
  expect(root.querySelector('[data-e2e=repair-start-review]')).not.toBeNull();

  root.querySelector<HTMLButtonElement>('[data-e2e=repair-view-result]')!.click();
  await flush();
  // The artifacts layer path, not the frozen target/backup, is opened.
  expect(state.batch.openItem).toHaveBeenCalledWith({
    item_id: 'i1', image_path: '/a/PAN_job9.tif', overlay_ready: false,
  });

  root.querySelector<HTMLButtonElement>('[data-e2e=repair-start-review]')!.click();
  await flush();
  expect(requestQualityReview).toHaveBeenCalledWith('i1');
});

it('marks a fully approved production result as quality accepted', async () => {
  vi.mocked(apiRepairStatus).mockResolvedValue(status({
    plan: plan({ status: 'STARTED', mode: 'production' }),
    goal: { goal_id: 'g1', status: 'COMPLETED', phase: 'done',
            monitoring_done: true, monitor_done: true },
    counts: counts({ verified: 2, verified_demo: 0, verified_production: 2 }),
    progress: progress(2),
    items: [
      { item_id: 'i1', ordinal: 1, name: 'A', status: 'SUCCEEDED', attempt: 1,
        quality_review_id: 'rev-1', output_path: '/a/PAN.tif' },
      { item_id: 'i2', ordinal: 2, name: 'B', status: 'SUCCEEDED', attempt: 1,
        quality_review_id: 'rev-2', output_path: '/b/PAN.tif' },
    ],
    quality_reviews: { approved: 2, rejected: 0, pending: 0 },
    quality_products: { approved: 2, rejected: 0, pending: 0 },
    quality_regions: { approved: 4, rejected: 0, pending: 0, total: 4 },
    acceptance: { mode: 'production', computed: true, quality_required: 2,
                  quality_approved: 2, quality_rejected: 0, quality_pending: 0,
                  quality_passed: true },
  }) as never);
  await mount(plan({ status: 'STARTED', mode: 'production' }));
  const text = root.textContent ?? '';
  expect(text).toContain('全部必要区域已通过人工质量审核');
  expect(text).toContain('修复质量验收完成');
  expect(text).not.toContain('请以逐区域人工验收为准');
  const stateEl = root.querySelector('[data-e2e=agent-repair-task] .state');
  expect(stateEl?.className).toContain('done');
});
