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
    apiPrepareRepair: vi.fn(),
    apiStartRepair: vi.fn(),
    apiRepairStatus: vi.fn(), apiRepairTargets: vi.fn(),
  };
});
vi.mock('../../lib/batchDock', () => ({ requestQualityReview: vi.fn(), requestMaskReview: vi.fn() }));
vi.mock('../../router', () => ({ default: { push: vi.fn().mockResolvedValue(undefined) } }));

import AgentRepairPlanCard from '../AgentRepairPlanCard.vue';
import { apiPrepareRepair, apiConfirmRepair, apiRepairStatus, apiStartRepair, apiRepairTargets, HttpError } from '../../lib/api';
import { requestQualityReview, requestMaskReview } from '../../lib/batchDock';
import router from '../../router';

const PLAN_ID = 'plan_' + 'a'.repeat(24);
const plan = (over: Record<string, unknown> = {}) => ({
  kind: 'repair_plan.v1', contract_version: 'repair_plan.v1', plan_id: PLAN_ID,
  version: 1, scope: 'batch', status: 'READY', confirmation_required: true,
  targets: { total: 2, excluded: 0, applicable: 2, items: [] },
  strategy: {
    detection: '现有差异检测管线', detection_method: 'absdiff',
    mask: 'auto_or_existing', execution: 'existing_batch_runtime',
  },
  mode: 'production', execution: { allowed: true, mode: 'production', reason: null },
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
  await new Promise(resolve => setTimeout(resolve, 70));
  await nextTick();
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
  state.viewer = reactive({ workspaceVersion: 0, recsOrdered: [], sceneOrdinalOf: (r: {id: number}) => r.id });
  vi.mocked(apiConfirmRepair).mockImplementation(async () => plan({ status: 'CONFIRMED' }) as never);
  vi.mocked(apiRepairStatus).mockResolvedValue(status() as never);
});
afterEach(() => { app?.unmount(); app = undefined; root?.remove(); document.querySelectorAll('.repair-drawer-backdrop').forEach(el => el.remove()); });

it('keeps conflict paths collapsed and prepares a new manifest plan without starting SR', async () => {
  const blocking = [1, 2].map(line => ({ code: 'pan_backup_exists', line, name: `PMS0${line}`,
    scene_ref: `/DiskArray/PMS0${line}`, filename: 'PAN_old_latest.tif',
    conflict_path: `/DiskArray/PMS0${line}/PAN_old_latest.tif`, message: `raw backup error ${line}` }));
  const p = plan({ status: 'BLOCKED', blocking, exclusions: [{line: 3, reason: '原有排除'}] });
  vi.mocked(apiRepairStatus).mockResolvedValue(status({plan:p}) as never);
  await mount(p);
  expect(root.textContent).toContain('2 项冲突');
  expect(root.textContent).not.toContain('raw backup error');
  expect(root.textContent).not.toContain('PMS02');
  expect(root.querySelector('[data-e2e=repair-plan-start]')).toBeNull();
  const expand = Array.from(root.querySelectorAll('button')).find(b => b.textContent?.includes('查看全部 2 项冲突'))!;
  expand.click(); await flush();
  expect(root.textContent).toContain('PMS02');
  const next = plan({plan_id:'plan_' + 'b'.repeat(24)});
  vi.mocked(apiPrepareRepair).mockResolvedValue(next as never);
  vi.mocked(apiRepairStatus).mockResolvedValue(status({plan:next}) as never);
  root.querySelector<HTMLButtonElement>('[data-e2e=repair-plan-exclude]')!.click();
  await flush();
  await new Promise(resolve => setTimeout(resolve, 450));
  expect(apiPrepareRepair).toHaveBeenCalledWith(expect.objectContaining({manifest_ref:p.manifest_ref,
    exclusions: [{line:3,reason:'原有排除'}, {line:1,reason:'已有 PAN 备份'}, {line:2,reason:'已有 PAN 备份'}]}));
  expect(p.status).toBe('BLOCKED');
  expect(apiStartRepair).not.toHaveBeenCalled();
  expect(root.querySelector('[data-e2e=repair-plan-start]')).not.toBeNull();
});

it('keeps 27 long names out of the card and opens a sorted Viewer-labelled drawer', async () => {
  const rows = Array.from({length:27}, (_, n) => ({item_id:`i${n}`, ordinal:n+1, scene_ref:`/scenes/${n}`, name:`SCENE_${n}_` + 'LONG_FILENAME_'.repeat(30)}));
  const p = plan({targets:{total:27,applicable:27,excluded:0,items:rows.slice().reverse()}});
  state.viewer.recsOrdered = rows.map((r, n) => ({id:n+101,sceneDir:r.scene_ref}));
  vi.mocked(apiRepairStatus).mockResolvedValue(status({plan:p}) as never);
  await mount(p);
  expect(root.textContent).not.toContain(rows[0].name);
  expect(root.querySelectorAll('.plan-actions button')).toHaveLength(1);
  expect(root.textContent).toContain('确认并启动');
  root.querySelector<HTMLButtonElement>('[data-e2e=repair-targets-open]')!.click();
  await flush();
  const drawer = document.querySelector('[role=dialog]')!;
  expect(drawer.querySelectorAll('li')).toHaveLength(27);
  expect(drawer.querySelector('.tag')?.textContent).toBe('101');
  expect(drawer.querySelector('.filename')?.getAttribute('title')).toBe(rows[0].name);
  drawer.dispatchEvent(new KeyboardEvent('keydown', {key:'Escape',bubbles:true}));
  await new Promise(resolve => setTimeout(resolve, 260));
  expect(document.querySelector('[role=dialog]')).toBeNull();
});

it('loads the full server list when the model view omits targets', async () => {
  const p = plan({targets:{total:27,applicable:27,excluded:0,items:[]}});
  vi.mocked(apiRepairStatus).mockResolvedValue(status({plan:p}) as never);
  vi.mocked(apiRepairTargets).mockResolvedValue({plan_id:PLAN_ID,version:1,total:27,has_more:false,next_offset:27,
    items:Array.from({length:27}, (_, n) => ({item_id:`i${n}`,ordinal:n+1,name:`Scene ${n}`}))});
  await mount(p);
  root.querySelector<HTMLButtonElement>('[data-e2e=repair-targets-open]')!.click();
  await flush();
  expect(apiRepairTargets).toHaveBeenCalledWith(PLAN_ID,'s',1,0);
  expect(document.querySelectorAll('.target-list li')).toHaveLength(27);
});

it('deduplicates rapid clicks and confirms before starting production', async () => {
  const p = plan({mode:'production'});
  vi.mocked(apiRepairStatus).mockResolvedValue(status({plan:p}) as never);
  let finish!: (value: never) => void;
  vi.mocked(apiConfirmRepair).mockImplementation(() => new Promise(resolve => { finish = resolve; }));
  vi.mocked(apiStartRepair).mockResolvedValue({plan:plan({status:'STARTED',mode:'production'}),goal:{goal_id:'g'},reused:false} as never);
  await mount(p);
  const button = root.querySelector<HTMLButtonElement>('[data-e2e=repair-plan-start]')!;
  expect(button.textContent).toContain('确认并启动');
  button.click(); button.click();
  await flush();
  expect(apiConfirmRepair).toHaveBeenCalledTimes(1);
  expect(apiStartRepair).not.toHaveBeenCalled();
  finish(plan({status:'CONFIRMED',mode:'production'}) as never);
  await flush();
  expect(apiStartRepair).toHaveBeenCalledTimes(1);
  expect(apiStartRepair).toHaveBeenCalledWith(PLAN_ID,expect.objectContaining({plan_version:1}));
});

it('shows version conflicts without retrying or submitting a new plan', async () => {
  vi.mocked(apiStartRepair).mockRejectedValue(new HttpError(409,'version conflict'));
  await mount(plan());
  vi.mocked(apiRepairStatus).mockResolvedValue(status({plan:plan({version:2})}) as never);
  root.querySelector<HTMLButtonElement>('[data-e2e=repair-plan-start]')!.click();
  await flush();
  expect(root.textContent).toContain('计划状态已变化');
  expect(apiStartRepair).toHaveBeenCalledTimes(1);
});

it('restores an already-started server plan without presenting a start button', async () => {
  vi.mocked(apiRepairStatus).mockResolvedValue(status({plan:plan({status:'STARTED',goal_id:'g'}),
    goal:{goal_id:'g',status:'RUNNING',phase:'dispatching',monitoring_done:false}}) as never);
  await mount(plan());
  expect(root.textContent).toContain('已启动');
  expect(root.textContent).toContain('查看进度');
  expect(root.querySelector('[data-e2e=repair-plan-start]')).toBeNull();
  expect(apiConfirmRepair).not.toHaveBeenCalled();
});

it('shows a three-quarter loading arc during execution and keeps paused and terminal states static', async () => {
  // Snapshot swaps must reach the card's computed goal, so this test's store
  // needs the same reactive wrapper Pinia provides in the app.
  state.batch = reactive({ ...state.batch });
  const snapshot = {
    goal: { goal_id: 'g1', status: 'RUNNING', phase: 'monitoring', monitoring_done: false },
    counts: counts({ verified: 1, submitted: 2, detected: 2 }),
    progress: progress(1), quality: null,
    active_jobs: [], recent_jobs: [], producer: { current: null },
  };
  vi.mocked(apiRepairStatus).mockResolvedValue(status({
    plan: plan({ status: 'STARTED' }),
    goal: { goal_id: 'g1', status: 'RUNNING', phase: 'monitoring', monitoring_done: false },
    counts: snapshot.counts, progress: snapshot.progress,
  }) as never);
  await mount(plan({ status: 'STARTED' }));
  state.batch.snapshot = snapshot as never;
  await flush();
  const ring = () => root.querySelector('[data-e2e=repair-state-ring]')!;
  expect(ring().getAttribute('data-state-icon')).toBe('running');
  expect(ring().classList.contains('running')).toBe(true);
  expect(ring().querySelector('.loading-arc')?.getAttribute('stroke-dasharray')).toBe('75 25');
  expect(ring().querySelector('.ring')).toBeNull();

  for (const [status, phase] of [['WAITING_USER', 'waiting_review'], ['WAITING_EXTERNAL', 'monitoring'], ['VERIFYING', 'verifying']]) {
    state.batch.snapshot = { ...snapshot, goal: { ...snapshot.goal, status, phase } } as never;
    await flush();
    expect(ring().getAttribute('data-state-icon')).toBe('running');
  }
  state.batch.snapshot = { ...snapshot, goal: { ...snapshot.goal, status: 'RUNNING', phase: 'waiting_quality_review' } } as never;
  await flush();
  expect(ring().getAttribute('data-state-icon')).toBe('waiting');
  state.batch.snapshot = { ...snapshot, goal: { ...snapshot.goal, status: 'PAUSED', phase: 'waiting_review' } } as never;
  await flush();
  expect(ring().getAttribute('data-state-icon')).toBe('paused');

  // Paused: rotation is gone, the ring keeps a static pause glyph.
  state.batch.snapshot = { ...snapshot, goal: { ...snapshot.goal, status: 'PAUSED' } } as never;
  await flush();
  expect(ring().getAttribute('data-state-icon')).toBe('paused');
  expect(ring().classList.contains('running')).toBe(false);
  expect(ring().querySelector('.loading-arc')).toBeNull();
  expect(ring().querySelector('.ring')).not.toBeNull();

  state.batch.snapshot = {
    ...snapshot,
    goal: { ...snapshot.goal, status: 'COMPLETED', phase: 'done', monitoring_done: true },
  } as never;
  await flush();
  expect(ring().getAttribute('data-state-icon')).toBe('done');

  state.batch.snapshot = {
    ...snapshot,
    goal: { ...snapshot.goal, status: 'FAILED', phase: 'done', monitoring_done: true },
  } as never;
  await flush();
  expect(ring().getAttribute('data-state-icon')).toBe('failed');
  expect(ring().classList.contains('running')).toBe(false);
});

it('shows the plan stage and confirms/starts on the same card', async () => {
  await mount(plan());
  expect(root.textContent).toContain('修复计划 · 2 景');
  expect(root.querySelector('[data-e2e=repair-plan-confirm]')).toBeNull();
  expect(root.querySelector('[data-e2e=repair-plan-start]')).not.toBeNull();
});

it('requires confirmation and cancels the bound task through the existing control chain', async () => {
  state.batch.goalId = 'cancel-goal';
  state.batch.control = vi.fn().mockResolvedValue('已请求取消');
  vi.mocked(apiRepairStatus).mockResolvedValue(status({
    plan: plan({status:'STARTED'}),
    goal: {goal_id:'cancel-goal',status:'WAITING_USER',phase:'waiting_review',monitoring_done:false},
  }) as never);
  await mount(plan({status:'STARTED'}));
  expect(root.textContent).not.toContain('在后台执行，可随时查看进度');
  const button = root.querySelector<HTMLButtonElement>('[data-e2e=repair-stop-task]')!;
  button.click(); await nextTick();
  expect(button.textContent).toBe('确认中止');
  expect(state.batch.control).not.toHaveBeenCalled();
  button.click(); button.click(); await flush();
  expect(state.batch.control).toHaveBeenCalledTimes(1);
  expect(state.batch.control).toHaveBeenCalledWith('cancel');
  expect(apiStartRepair).not.toHaveBeenCalled();
});

it('opens the matching mask review from the waiting card without approving or starting SR', async () => {
  vi.mocked(apiRepairStatus).mockResolvedValue(status({
    plan: plan({status:'STARTED'}),
    goal: {goal_id:'mask-goal',status:'WAITING_USER',phase:'waiting_review',monitoring_done:false},
  }) as never);
  await mount(plan({status:'STARTED'}));
  const button = root.querySelector<HTMLButtonElement>('[data-e2e=repair-mask-review]')!;
  expect(button.textContent).toBe('前往复核');
  button.click(); button.click(); await flush();
  expect(state.batch.selectGoal).toHaveBeenCalledTimes(1);
  expect(state.batch.selectGoal).toHaveBeenCalledWith('mask-goal');
  expect(router.push).toHaveBeenCalledWith('/viewer');
  expect(requestMaskReview).toHaveBeenCalledWith('mask-goal');
  expect(apiStartRepair).not.toHaveBeenCalled();
});

it('keeps unstarted historical Demo plans truthful and refuses to run them', async () => {
  const old = plan({mode:'demo',execution:{allowed:true,mode:'demo',reason:null}});
  vi.mocked(apiRepairStatus).mockResolvedValue(status({plan:old}) as never);
  await mount(old);
  expect(root.textContent).toContain('历史演示计划');
  expect(root.textContent).not.toContain('重新准备自动修复');
  root.querySelector<HTMLButtonElement>('.plan-body > .list-link')!.click();
  await flush();
  root.querySelector<HTMLButtonElement>('.detail-link')!.click();
  await flush();
  expect(root.textContent).toContain('重新准备自动修复');
  expect(root.querySelector('[data-e2e=repair-plan-start]')).toBeNull();
  expect(apiStartRepair).not.toHaveBeenCalled();
});

it('labels a prepared plan with unavailable execution clearly', async () => {
  const unavailable = plan({ execution: { allowed: false, mode: 'demo', reason: '演示工作目录不可写' } });
  vi.mocked(apiRepairStatus).mockResolvedValue(status({ plan: unavailable }) as never);
  await mount(unavailable);
  expect(root.textContent).toContain('暂不可执行');
  root.querySelector<HTMLButtonElement>('.detail-link')!.click();
  await flush();
  expect(root.textContent).toContain('演示工作目录不可写');
  expect(root.textContent).not.toContain('待确认');
  expect(root.querySelector('[data-e2e=repair-plan-start]')).toBeNull();
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
  expect(root.textContent).toContain('自动修复');
  expect(root.textContent).toContain('SCENE_A');
  expect(root.querySelector('[data-stage=verification]')?.textContent).toContain('1 / 2');
  expect(root.querySelector('[data-e2e=repair-task-actions]')).not.toBeNull();
});

it('switches to a deterministic demo summary and does not fake a result artifact', async () => {
  vi.mocked(apiRepairStatus).mockResolvedValue(status({
    plan: plan({ status: 'STARTED', mode: 'demo',
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
  await mount(plan({ status: 'STARTED', mode: 'demo' }));
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
